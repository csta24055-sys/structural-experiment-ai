"""候補 G5: Claude Code で解析するための依頼の受け渡し（API の依頼と同じ内容・同じ出力の形）。"""
import json
from pathlib import Path

import pytest

from structlab.ai.base import AIImage, AIRequest, AwaitingAnswer
from structlab.ai.mock_provider import MockProvider
from structlab.ai.queue_provider import QueueProvider, answer, pending, request_record


def test_queue_round_trip_resumes_and_validates(tmp_path):
    from PIL import Image
    img = tmp_path / "f.png"
    Image.new("RGB", (40, 30), "white").save(img)
    schema = {"type": "object", "properties": {"ok": {"type": "integer"}}, "required": ["ok"], "additionalProperties": False}
    reqs = [AIRequest(task="t", source_label=f"s{i}", text=f"本文{i}", system="指示", schema=schema,
                      images=[AIImage(img, "画面", lossless=True)]) for i in range(3)]
    q = QueueProvider(tmp_path / "q")
    res = q.analyze_batch(reqs, log=lambda m: None)
    assert all(isinstance(r, AwaitingAnswer) for r in res) and len(pending(tmp_path / "q")) == 3
    rec = json.loads(pending(tmp_path / "q")[0].read_text(encoding="utf-8"))
    # API と同じ指示文・本文・画像（元のファイル）・出力の形が書き出される
    assert rec["system"] == "指示" and rec["text"].startswith("本文") and rec["schema"] == schema
    assert rec["images"][0]["source"] == str(img.resolve()) and rec["images"][0]["lossless"]
    # 答え役が見る画像は、API が受け取るのと同じ画素（同じ縮小・符号化）の PNG
    assert Image.open(rec["images"][0]["path"]).size == (40, 30) and rec["images"][0]["width"] == 40
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"ok": "x"}), encoding="utf-8")
    assert answer(tmp_path / "q", rec["id"], bad, "m")                         # 形に合わない答えは保存しない
    good = tmp_path / "good.json"
    for p in pending(tmp_path / "q"):
        good.write_text(json.dumps({"ok": 1}), encoding="utf-8")
        assert answer(tmp_path / "q", p.stem, good, "m") == []
    res = q.analyze_batch(reqs, log=lambda m: None)                             # 同じ依頼をもう一度: 答えを使う
    assert [r.data for r in res] == [{"ok": 1}] * 3 and not pending(tmp_path / "q")


def test_semantic_layer_through_queue_matches_direct_answers(tmp_path):
    """意味理解を受け渡し口で実行し、各依頼に（Mock の）同じ答えを書き戻すと、直接答えた場合と同じ指示の一覧になる。
    （答える側が同じ答えを返せば結果は同じ。Claude Code の答えが API と同等かは見本の比較が必要）"""
    from structlab.reading.semantic_bridge import run_semantic
    from tests.test_reading import make_cfg
    rec = {"path": "v.mp4", "kind": "video", "duration": 300.0, "has_audio": True, "content_hash": "h", "transcriber": "x",
           "scenes": [{"index": 0, "start": 0, "end": 300, "frame_time": 1, "frame": "", "mode": "slides",
                       "ai": {"title": "t", "text_blocks": []}}],
           "speech": [{"start": 20.0 + 100 * k, "end": 24.0 + 100 * k, "text": f"手順{k}は必ず守ってください"} for k in range(3)]}
    cfg = make_cfg(tmp_path, tmp_path)
    cfg.data["semantic"]["ai"] = True
    run_semantic(cfg, tmp_path / "direct", {"v.mp4": rec}, MockProvider(), batch=False, log=lambda m: None)
    q = QueueProvider(tmp_path / "q")
    mock = MockProvider()
    for _ in range(60):  # 受け渡し → 回答（ここでは Mock が Claude Code の代わり）→ 再実行 を繰り返す
        run_semantic(cfg, tmp_path / "queued", {"v.mp4": rec}, q, batch=True, log=lambda m: None)
        todo = pending(tmp_path / "q")
        if not todo:
            break
        for p in todo:
            r = json.loads(p.read_text(encoding="utf-8"))
            req = AIRequest(task=r["task"], source_label=r["source_label"], text=r["text"], system=r["system"],
                            schema=r["schema"])
            f = tmp_path / "a.json"
            f.write_text(json.dumps(mock.analyze(req).data, ensure_ascii=False), encoding="utf-8")
            assert answer(tmp_path / "q", p.stem, f, "mock") == []
    load = lambda d: sorted((x["content"], x["class"], x["verification"]) for x in json.loads(  # noqa: E731
        (tmp_path / d / "semantic" / "directives.json").read_text(encoding="utf-8")))
    assert load("queued") == load("direct") and load("direct")


def test_large_images_get_full_resolution_tiles(tmp_path):
    """Claude Code の Read は長辺 2000 を超える画像を縮小するので、API と同じ画素を部分画像で添える。"""
    from PIL import Image
    from structlab.ai.queue_provider import READ_MAX_EDGE, tile_boxes
    for w, h in ((2576, 1449), (2576, 2576), (1999, 1000), (4000, 300)):
        boxes = tile_boxes(w, h)
        if max(w, h) <= READ_MAX_EDGE:
            assert boxes == []
            continue
        assert all(max(x1 - x0, y1 - y0) <= READ_MAX_EDGE for x0, y0, x1, y1 in boxes)
        pts = [(x, y) for x in [*range(0, w, 37), w - 1] for y in [*range(0, h, 37), h - 1]]
        assert all(any(x0 <= x < x1 and y0 <= y < y1 for x0, y0, x1, y1 in boxes) for x, y in pts)  # 全画素を覆う
    img = tmp_path / "big.png"
    Image.new("RGB", (2576, 1449), "white").save(img)
    q = QueueProvider(tmp_path / "q")
    q.analyze_batch([AIRequest(task="t", source_label="s", text="x", system="y", schema={"type": "object"},
                               images=[AIImage(img, "画面", lossless=True)])], log=lambda m: None)
    rec = json.loads(pending(tmp_path / "q")[0].read_text(encoding="utf-8"))
    tiles = rec["images"][0]["tiles"]
    assert len(tiles) == 2 and all(max(Image.open(p).size) <= READ_MAX_EDGE for p in tiles)


def _one(tmp_path, schema=None):
    from PIL import Image
    img = tmp_path / "f.png"
    if not img.exists():
        Image.new("RGB", (60, 40), "white").save(img)
    schema = schema or {"type": "object", "properties": {"ok": {"type": "integer"}}, "required": ["ok"],
                        "additionalProperties": False}
    return AIRequest(task="t", source_label="s", text="本文", system="指示", schema=schema, images=[AIImage(img, "画面")])


def test_queue_abnormal_cases(tmp_path):
    """異常系: 形の違う答え・壊れた JSON・重複した答え・依頼の無い答え・壊れた answers・依頼の変更・画像の欠落。"""
    from structlab.ai.queue_provider import Queue
    q = QueueProvider(tmp_path / "q")
    Q = Queue(tmp_path / "q")
    req = _one(tmp_path)
    assert isinstance(q.analyze_batch([req], log=lambda m: None)[0], AwaitingAnswer)      # 未回答
    rid = Q.pending()[0]
    inbox = tmp_path / "q" / "inbox"
    (inbox / f"{rid}.json").write_text('{"data": {"ok": "文字"}}', encoding="utf-8")         # 形が違う
    assert Q.ingest()["rejected"] == 1 and Q.pending() == [rid]
    (inbox / f"{rid}.json").write_text('{"data": {"ok": 1', encoding="utf-8")               # 壊れた JSON
    assert Q.ingest()["rejected"] == 1 and Q.pending() == [rid]
    (inbox / "ffff.json").write_text('{"data": {"ok": 1}}', encoding="utf-8")               # 依頼の無い答え
    assert Q.ingest()["unknown"] == 1
    (inbox / f"{rid}.json").write_text('{"data": {"ok": 1}, "model": "m"}', encoding="utf-8")
    assert Q.ingest()["accepted"] == 1 and Q.pending() == []
    (inbox / f"{rid}.json").write_text('{"data": {"ok": 2}}', encoding="utf-8")             # 重複: 最初の答えを使う
    assert Q.ingest()["duplicate"] == 1
    assert q.analyze_batch([req], log=lambda m: None)[0].data == {"ok": 1}
    ap = tmp_path / "q" / "answers" / f"{rid}.json"
    ap.write_text("{壊れた", encoding="utf-8")                                              # answers が壊れた
    assert isinstance(q.analyze_batch([req], log=lambda m: None)[0], AwaitingAnswer) and Q.pending() == [rid]
    assert len(list((tmp_path / "q" / "rejected").glob("*.json"))) == 5
    (inbox / f"{rid}.json").write_text('{"data": {"ok": 3}}', encoding="utf-8")
    Q.ingest()
    rec = Q.request(rid)
    for p in [rec["images"][0]["path"]]:                                                    # 画像の欠落: 作り直す
        Path(p).unlink()
    assert q.analyze_batch([req], log=lambda m: None)[0].data == {"ok": 3}
    assert Path(Q.request(rid)["images"][0]["path"]).exists()
    a = json.loads(ap.read_text(encoding="utf-8"))
    a["record_sha256"] = "x" * 64                                                          # 依頼と合わない答え
    ap.write_text(json.dumps(a), encoding="utf-8")
    assert isinstance(q.analyze_batch([req], log=lambda m: None)[0], AwaitingAnswer)
    assert not list((tmp_path / "q" / "answers").glob(".*.tmp"))                           # 書きかけの答えを残さない


def test_g5_driver_guards_and_assignment(tmp_path):
    """G5 の実行役: 有料の API の設定では動かない、受け渡し中の依頼を二重に渡さない、テスト用の答えでは完了にしない。"""
    import importlib.util
    import yaml
    spec = importlib.util.spec_from_file_location("g5", Path(__file__).parent.parent / "scripts" / "g5_driver.py")
    g5 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g5)
    work = tmp_path / "g5"
    g5.prepare(work, [], str(Path(__file__).parent.parent / "configs" / "dropbox.yaml"))
    c = yaml.safe_load((work / "config.yaml").read_text(encoding="utf-8"))
    assert c["ai"]["provider"] == "claude_code_queue" and not c["ai"]["allow_external"]
    assert "ANTHROPIC_API_KEY" not in g5.env()
    c["ai"]["provider"] = "anthropic"
    (work / "config.yaml").write_text(yaml.safe_dump(c), encoding="utf-8")
    with pytest.raises(SystemExit):
        g5.guard(g5.paths(work))
    q = QueueProvider(work / "queue")
    reqs = [_one(tmp_path, {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]})]
    reqs.append(AIRequest(task="t", source_label="s2", text="別", system="指示", schema=reqs[0].schema))
    q.analyze_batch(reqs, log=lambda m: None)
    first = g5.next_ids(work, 1)
    assert len(first) == 1 and g5.next_ids(work, 5) != first and len(g5.next_ids(work, 5)) == 0
    assert g5.fake_answer(work) == 2
    from structlab.ai.queue_provider import Queue
    Queue(work / "queue").ingest()
    s = g5.status(work, quiet=True)
    assert s["pending"] == 0 and s["fake_answers"] == 2 and not s["complete"]


def test_request_record_rebuilds_the_identical_api_request(tmp_path):
    """見本の比較で API に送る依頼は、本番で API に送る依頼と同じ（指紋が同じ）。"""
    from structlab.ai.queue_provider import api_request, request_id
    req = _one(tmp_path)
    req.cache_prefix_chars = 2
    q = QueueProvider(tmp_path / "q")
    q.analyze_batch([req], log=lambda m: None)
    rec = json.loads(pending(tmp_path / "q")[0].read_text(encoding="utf-8"))
    assert request_id(api_request(rec)) == request_id(req) == rec["id"]


def test_pg5_select_run_and_compare(tmp_path):
    """P-G5: 種類ごとの見本を選び、API（ここでは Mock）で 2 回答えさせ、G5 の答えと比べる。
    G5 が API と同じ項目を返せば合格、重要な項目（指示）を落とせば不合格。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pg5", Path(__file__).parent.parent / "scripts" / "pg5.py")
    pg5 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pg5)
    from structlab.ai.base import AIResponse
    from structlab.ai.queue_provider import Queue
    from structlab.semantic import prompts as P
    work = tmp_path / "g5"
    q = QueueProvider(work / "queue")
    reqs = [AIRequest(task="directive_extraction", source_label=f"資料{i} #1", text=f"本文{i}", system=P.COMMON_SYSTEM,
                      schema=P.DIRECTIVE_SCHEMA) for i in range(4)]
    q.analyze_batch(reqs, log=lambda m: None)

    def ans(i, drop=False):
        d = {"content": f"グラフには必ず単位{i}を書く", "directive_class": "mandatory", "base_strength": None, "condition": None,
             "target_task": "", "deliverable": "", "audience": "", "t_start": 10.0, "t_end": 12.0, "screen": "",
             "operation": "", "speech_quote": "必ず", "context_before": "", "context_after": "", "certainty": "high",
             "unconfirmed": "", "origin": "explicit", "speaker": "unknown", "speaker_basis": "", "relation_type": "none",
             "relation_refers_to": "", "matched_candidates": []}
        return {"directives": [] if drop else [d], "dismissed_candidates": []}
    Q = Queue(work / "queue")
    for rid in Q.pending():
        i = int(Q.request(rid)["text"][-1])
        (work / "queue" / "inbox" / f"{rid}.json").write_text(json.dumps({"data": ans(i, drop=(i == 0))}), encoding="utf-8")
    Q.ingest()
    ids = pg5.select(work)
    assert len(ids) == 3                                   # directive_extraction の見本は 3 件

    class Api:
        def analyze_batch(self, rs, log=print, **_):
            return [AIResponse(data=ans(int(r.text[-1])), model="m") for r in rs]
    pg5.run_api(work, 2, provider=Api())
    res = pg5.compare(work)
    sample_has_dropped = any(Q.request(i)["text"].endswith("0") for i in ids)
    assert res["pass"] is (not sample_has_dropped)
    assert (work / "pg5" / "decision.md").exists()


def test_request_markdown_is_complete_and_line_split(tmp_path):
    """答え役が読む依頼の Markdown: 指示・画像・本文・出力の形がすべて入り、長い 1 行が無く、終わりの印がある。"""
    from structlab.ai.queue_provider import END_MARK
    from structlab.semantic import prompts as P
    req = AIRequest(task="directive_extraction", source_label="s", text="本文\n" * 50, system=P.COMMON_SYSTEM,
                    schema=P.DIRECTIVE_SCHEMA, images=[_one(tmp_path).images[0]])
    QueueProvider(tmp_path / "q").analyze_batch([req], log=lambda m: None)
    md = next((tmp_path / "q" / "requests").glob("*.md")).read_text(encoding="utf-8")
    lines = md.rstrip("\n").split("\n")
    assert lines[-1] == END_MARK and f"全 {len(lines)} 行" in lines[0]
    assert P.COMMON_SYSTEM.split("\n")[0] in md and "directive_class" in md and "f.png" not in md.split("## 資料")[0][:0]
    assert max(len(x) for x in lines) < 2000


def test_review_fixes_tamper_wrapper_thin_tiles(tmp_path):
    from structlab.ai.queue_provider import Queue, tile_boxes
    assert tile_boxes(1, 2001) and all(y1 - y0 <= 2000 for _, y0, _, y1 in tile_boxes(1, 2001))
    q = QueueProvider(tmp_path / "q")
    Q = Queue(tmp_path / "q")
    req = _one(tmp_path)
    q.analyze_batch([req], log=lambda m: None)
    rid = Q.pending()[0]
    (tmp_path / "q" / "inbox" / f"{rid}.json").write_text('{"ok": 1}', encoding="utf-8")   # 決まった形でない
    assert Q.ingest()["rejected"] == 1
    (tmp_path / "q" / "inbox" / f"{rid}.json").write_text('{"data": {"ok": 1}}', encoding="utf-8")
    assert Q.ingest()["accepted"] == 1
    rp = tmp_path / "q" / "requests" / f"{rid}.json"
    rec = json.loads(rp.read_text(encoding="utf-8"))
    rec["text"] = "書き換え"                                                                 # 依頼の書き換え
    rp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    assert Q.answer_for(rec) is None


def test_fake_answers_taint_the_work_folder_forever(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("g5", Path(__file__).parent.parent / "scripts" / "g5_driver.py")
    g5 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g5)
    work = tmp_path / "g5"
    g5.prepare(work, [], str(Path(__file__).parent.parent / "configs" / "dropbox.yaml"))
    QueueProvider(work / "queue").analyze_batch([_one(tmp_path)], log=lambda m: None)
    g5.fake_answer(work)
    for p in (work / "queue" / "answers").glob("*.json"):   # 仮の答えを消しても
        p.unlink()
    assert g5.status(work, quiet=True)["test_only"] and not g5.status(work, quiet=True)["complete"]
    with pytest.raises(SystemExit):                          # 本番に使い回せない
        g5.prepare(work, [], str(Path(__file__).parent.parent / "configs" / "dropbox.yaml"))
