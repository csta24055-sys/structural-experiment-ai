"""候補 G5: Claude Code で解析するための依頼の受け渡し（API の依頼と同じ内容・同じ出力の形）。"""
import json

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
    assert rec["images"][0]["path"] == str(img.resolve()) and rec["images"][0]["lossless"]
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
