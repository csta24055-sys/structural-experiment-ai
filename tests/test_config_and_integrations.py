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
