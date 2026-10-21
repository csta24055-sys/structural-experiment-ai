"""設定・Dropbox クライアント・Anthropic プロバイダ（いずれも通信なしの模擬で確認）。"""
from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import dropbox
import pytest

from structlab.ai.anthropic_provider import AnthropicProvider
from structlab.ai.base import AIError, AIImage, AIRequest
from structlab.config import ConfigError, load_config
from structlab.sources import SourceError
from structlab.sources.dropbox_source import DropboxOutput, DropboxSource


def test_secrets_in_config_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("ai: {provider: anthropic, api_key: sk-xxx}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="秘密情報"):
        load_config(p)


def test_output_inside_source_rejected():
    with pytest.raises(ConfigError):
        load_config(None, {"source": {"type": "dropbox", "path": "/授業"}, "output": {"dropbox_path": "/授業/結果"}})
    load_config(None, {"source": {"type": "dropbox", "path": "/授業"}, "output": {"dropbox_path": "/授業_解析結果"}})


def test_example_config_loads():
    from pathlib import Path
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
    assert cfg.data["ai"]["allow_external"] is False and cfg.data["ai"]["provider"] == "none"


# ---------- Dropbox（模擬クライアント） ----------
def _fm(path, size=10, h="a" * 64):
    return dropbox.files.FileMetadata(name=path.rsplit("/", 1)[-1], id="id:" + path, path_display=path,
                                      path_lower=path.lower(), size=size, content_hash=h, rev="0123456789abc",
                                      server_modified=dt.datetime(2026, 10, 1), client_modified=dt.datetime(2026, 10, 1))


class FakeDbx:
    def __init__(self):
        self.calls = []
        self.uploads = []

    def files_list_folder(self, path, recursive, limit, include_non_downloadable_files):
        self.calls.append(("list", path, recursive))
        return SimpleNamespace(entries=[dropbox.files.FolderMetadata(name="第1回", id="id:d", path_display="/授業/第1回",
                                                                    path_lower="/授業/第1回"),
                                        _fm("/授業/第1回/a.pdf")], has_more=True, cursor="c1")

    def files_list_folder_continue(self, cursor):
        self.calls.append(("continue", cursor))
        return SimpleNamespace(entries=[_fm("/授業/第2回/動画.MP4", 5_000_000_000)], has_more=False, cursor="c2")

    def files_upload(self, data, path, mode, mute):
        self.uploads.append(path)


def test_dropbox_listing_recursive_with_pagination():
    fake = FakeDbx()
    files = list(DropboxSource("/授業", client=fake).iter_files())
    assert [f.path for f in files] == ["第1回/a.pdf", "第2回/動画.MP4"]
    assert files[1].size == 5_000_000_000 and files[1].rev
    assert fake.calls == [("list", "/授業", True), ("continue", "c1")]


def test_dropbox_source_has_no_write_methods():
    src = DropboxSource("/授業", client=FakeDbx())
    assert not any(hasattr(src, m) for m in ("upload", "delete", "move", "write"))


def test_dropbox_output_restricted_to_output_folder(tmp_path):
    with pytest.raises(SourceError):
        DropboxOutput("/授業/out", "/授業", client=FakeDbx())
    with pytest.raises(SourceError):
        DropboxOutput("/", "/授業", client=FakeDbx())
    fake = FakeDbx()
    out = DropboxOutput("/授業_解析結果", "/授業", client=fake)
    f = tmp_path / "x.md"
    f.write_text("x")
    out.upload(f, "results/x.md")
    assert fake.uploads == ["/授業_解析結果/results/x.md"]
    with pytest.raises(SourceError):
        out.upload(f, "../授業/x.md")


# ---------- Anthropic（模擬クライアント） ----------
class FakeMessages:
    def __init__(self, resp):
        self.resp = resp
        self.kwargs = None

    def create(self, **kw):
        self.kwargs = kw
        return self.resp


def _resp(text, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=1234, output_tokens=56), model="claude-opus-5-5")


def test_anthropic_request_shape(tmp_path):
    from PIL import Image
    img = tmp_path / "p.png"
    Image.new("RGB", (4000, 3000), "white").save(img)
    out = {"items": [], "unreadable": [], "important_moments": []}
    msgs = FakeMessages(_resp(json.dumps(out)))
    client = SimpleNamespace(beta=SimpleNamespace(messages=msgs), messages=None)
    p = AnthropicProvider(client=client)
    r = p.analyze(AIRequest(task="pdf_page", source_label="a.pdf p.1", text="本文", images=[AIImage(img, "p.1")]))
    kw = msgs.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in kw and "temperature" not in kw
    blocks = kw["messages"][0]["content"]
    assert blocks[1]["type"] == "image" and blocks[1]["source"]["media_type"] == "image/jpeg"
    assert "a.pdf p.1" in blocks[-1]["text"]
    assert r.input_tokens == 1234 and r.data == out


def test_anthropic_refusal_and_bad_json():
    for resp, retry in ((_resp("", "refusal"), False), (_resp("not json"), True)):
        client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(resp)), messages=None)
        with pytest.raises(AIError) as e:
            AnthropicProvider(client=client).analyze(AIRequest(task="image", source_label="x", text=""))
        assert e.value.retryable is retry


def _strip_cache(x):
    if isinstance(x, dict):
        return {k: _strip_cache(v) for k, v in x.items() if k != "cache_control"}
    if isinstance(x, list):
        return [_strip_cache(v) for v in x]
    return x


def test_batch_cache_priming_changes_only_cache_settings_and_order(tmp_path):
    """同じ先頭部分（長い指示文）を持つ依頼が多いときだけ、1件を先に送ってキャッシュを書き（1 時間）、残りを後で送る。
    各依頼の中身（モデル・指示文・本文・画像・出力の設定）は先書きしない場合と1文字も変わらない。"""
    from structlab.ai.anthropic_provider import PRIME_MIN_GROUP
    p = AnthropicProvider(client=SimpleNamespace())
    long_sys = "建築構造実験の資料を正確に読む。" * 60
    many = [AIRequest(task="t", source_label=f"s{i}", text=f"本文{i}", system=long_sys, schema={"type": "object"})
            for i in range(PRIME_MIN_GROUP + 2)]
    phases, params = p.batch_plan(many)
    assert phases == [[0], list(range(1, len(many)))]
    assert all(pp["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"} for pp in params)
    assert [_strip_cache(pp) for pp in params] == [_strip_cache(p._params(r)) for r in many]
    few = many[:PRIME_MIN_GROUP - 1]
    phases, params = p.batch_plan(few)                 # 少ないときは従来どおり 1 回、5 分のキャッシュ
    assert phases == [list(range(len(few)))] and params == [p._params(r) for r in few]
    short = [AIRequest(task="t", source_label="s", text=f"本文{i}", system="短い指示", schema={"type": "object"})
             for i in range(PRIME_MIN_GROUP + 2)]
    assert len(p.batch_plan(short)[0]) == 1           # 512 トークン未満の先頭部分はキャッシュされないので先書きしない
    mixed = many + [AIRequest(task="t", source_label="x", text="別", system="別の指示" * 300, schema={"type": "object"})]
    phases, _ = p.batch_plan(mixed)
    assert phases[0] == [0] and len(mixed) - 1 in phases[1]


def test_batch_priming_sends_first_phase_before_the_rest(tmp_path):
    from structlab.ai.anthropic_provider import PRIME_MIN_GROUP
    order = []

    class Batches:
        def __init__(self):
            self.made = {}

        def create(self, requests):
            bid = f"b{len(self.made)}"
            self.made[bid] = requests
            order.append([r["custom_id"] for r in requests])
            return SimpleNamespace(id=bid, processing_status="in_progress")

        def retrieve(self, bid):
            return SimpleNamespace(id=bid, processing_status="ended")

        def results(self, bid):
            for r in self.made[bid]:
                msg = SimpleNamespace(content=[SimpleNamespace(type="text", text='{"ok": 1}')], stop_reason="end_turn",
                                      usage=SimpleNamespace(input_tokens=10, output_tokens=2, cache_read_input_tokens=0,
                                                            cache_creation_input_tokens=0), model="m")
                yield SimpleNamespace(custom_id=r["custom_id"], result=SimpleNamespace(type="succeeded", message=msg))
    p = AnthropicProvider(client=SimpleNamespace(messages=SimpleNamespace(batches=Batches())))
    reqs = [AIRequest(task="t", source_label=f"s{i}", text=f"本文{i}", system="長い指示文。" * 200, schema={"type": "object"})
            for i in range(PRIME_MIN_GROUP + 1)]
    created = {}
    res = p.analyze_batch(reqs, poll_seconds=0, log=lambda m: None, on_created=lambda b, i: created.update({b: i}))
    assert order[0] == ["r0"] and order[1] == [f"r{i}" for i in range(1, len(reqs))]
    assert all(r.data == {"ok": 1} for r in res)
    assert sorted(created) == [0, 1_000_000]          # 中断後の再開用の番号（段階ごと）
