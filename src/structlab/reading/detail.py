"""Excel・レポートの作り方の動画で AI に送る画像を、保存した元の解像度の可逆画像（WebP lossless）から作る。

- 全体: 元の 2 倍（最大 2576）
- 部分拡大（代表画面）: 2×2 に少し重ねて分け、文字・線がある部分だけ（白紙の部分は拡大しない）を元の 3 倍
- 操作した部分の拡大: 直前の画面との差がある範囲（セル・数式バー・ダイアログ・メニューなど）を元の 3 倍。
  画面全体が変わったとき・差がごく小さいとき（マウスの移動など）は作らない
- AI が求めた部分の拡大（第2段）: 1回目の読み取りで「小さくて読めない・不確か」と AI が示した範囲を元の 4 倍
どれも元の画素から作る（拡大した画像からさらに拡大しない）。作った画像は frames/derived/ に保存し、作り直さない。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

MAX_EDGE = 2576
OVERVIEW_SCALE = 2.0
TILE_SCALE = 3.0
CHANGE_SCALE = 3.0
ZOOM_SCALE = 4.0
TILE_OVERLAP = 0.1
TILE_MIN_INK = 0.01        # 文字・線の画素の割合がこれ未満の部分（白紙）は拡大しない
CHANGE_MIN, CHANGE_MAX = 0.002, 0.4
CHANGE_MARGIN = 0.06


def _open(path: str | Path):
    from PIL import Image
    return Image.open(path).convert("RGB")


def _out(src: Path, tag: str) -> Path:
    d = src.parent / "derived"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{src.stem}_{tag}.webp"


def _scaled(im, scale: float):
    from PIL import Image
    w, h = im.size
    k = min(scale, MAX_EDGE / max(w, h))
    if abs(k - 1.0) < 1e-3:
        return im
    return im.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)


def derive(src: str | Path, tag: str, scale: float, box: tuple[int, int, int, int] | None = None) -> Path:
    """src（元の解像度）の box（画素）を scale 倍にした可逆画像。"""
    src = Path(src)
    out = _out(src, tag)
    if out.exists():
        return out
    im = _open(src)
    if box:
        im = im.crop(box)
    _scaled(im, scale).save(out, lossless=True, method=4)
    return out


def overview(src: str | Path, scale: float = OVERVIEW_SCALE) -> Path:
    """全体の画面（既定は元の 2 倍）。scale=1 は元の解像度のまま（候補 D1、既定では使わない）。"""
    if abs(scale - OVERVIEW_SCALE) < 1e-6:
        return derive(src, "x2", OVERVIEW_SCALE)
    return derive(src, f"x{scale:g}", scale)


def _ink(gray: np.ndarray) -> np.ndarray:
    """文字・線（隣の画素との明るさの差が大きい画素）。"""
    g = gray.astype(np.int16)
    dx = np.abs(np.diff(g, axis=1))[:-1, :]
    dy = np.abs(np.diff(g, axis=0))[:, :-1]
    return (np.maximum(dx, dy) > 40)


TILE_NAMES = ["左上", "右上", "左下", "右下"]


def tile_boxes(src: str | Path) -> list[tuple[int, tuple[int, int, int, int]]]:
    """代表画面の 2×2（少し重ねる）のうち、文字・線がある部分の範囲（画素）。"""
    im = _open(src)
    w, h = im.size
    gray = np.asarray(im.convert("L"))
    tw, th = int(w * (0.5 + TILE_OVERLAP / 2)), int(h * (0.5 + TILE_OVERLAP / 2))
    out = []
    for k, (x, y) in enumerate([(0, 0), (w - tw, 0), (0, h - th), (w - tw, h - th)]):
        ink = _ink(gray[y:y + th, x:x + tw])
        if ink.size and float(ink.mean()) >= TILE_MIN_INK:
            out.append((k, (x, y, x + tw, y + th)))
    return out


def content_tiles(src: str | Path) -> list[tuple[Path, str]]:
    """代表画面の 2×2 の部分拡大（文字・線がある部分だけ）。"""
    return [(derive(src, f"t{k}", TILE_SCALE, box), TILE_NAMES[k]) for k, box in tile_boxes(src)]


def scaled_size(w: int, h: int, scale: float) -> tuple[int, int]:
    k = min(scale, MAX_EDGE / max(w, h))
    return max(1, int(w * k)), max(1, int(h * k))


def planned_sizes(rep: str, events: list[str], prev: str | None) -> list[tuple[int, int]]:
    """送る画像の大きさ（ファイルを作らずに計算。見積もり用）: 全体・部分拡大・変わった部分の拡大。"""
    from PIL import Image
    out = []
    first = prev is None
    with Image.open(rep) as im:
        w, h = im.size
    out.append(scaled_size(w, h, OVERVIEW_SCALE))
    if first:
        out += [scaled_size(b[2] - b[0], b[3] - b[1], TILE_SCALE) for _, b in tile_boxes(rep)]
    last = rep if first else prev
    for f in events:
        with Image.open(f) as im:
            w, h = im.size
        out.append(scaled_size(w, h, OVERVIEW_SCALE))
        box = change_box(last, f)
        if box:
            out.append(scaled_size(box[2] - box[0], box[3] - box[1], CHANGE_SCALE))
        last = f
    return out


def change_box(prev: str | Path, cur: str | Path) -> tuple[int, int, int, int] | None:
    """直前の画面との差がある範囲（画素）。差が無い・ごく小さい・画面全体のときは None。"""
    a, b = np.asarray(_open(prev).convert("L"), dtype=np.int16), np.asarray(_open(cur).convert("L"), dtype=np.int16)
    if a.shape != b.shape:
        return None
    diff = np.abs(a - b) > 30
    frac = float(diff.mean())
    if not (CHANGE_MIN <= frac <= CHANGE_MAX):
        return None
    ys, xs = np.nonzero(diff)
    h, w = diff.shape
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    if (x1 - x0) * (y1 - y0) > CHANGE_MAX * w * h:
        return None
    mx, my = int(w * CHANGE_MARGIN), int(h * CHANGE_MARGIN)
    return max(0, x0 - mx), max(0, y0 - my), min(w, x1 + mx + 1), min(h, y1 + my + 1)


def change_zoom(prev: str | Path | None, cur: str | Path) -> Path | None:
    if not prev:
        return None
    box = change_box(prev, cur)
    return derive(cur, "chg", CHANGE_SCALE, box) if box else None


def zoom_box(src: str | Path, rel: dict) -> tuple[int, int, int, int] | None:
    """AI が示した範囲（画面に対する割合 0〜1）を画素に。少し広げる。"""
    im = _open(src)
    w, h = im.size
    try:
        x, y, rw, rh = (float(rel[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if rw <= 0 or rh <= 0:
        return None
    m = 0.02
    x0, y0 = max(0.0, x - m), max(0.0, y - m)
    x1, y1 = min(1.0, x + rw + m), min(1.0, y + rh + m)
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        return None
    return int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)


def ai_zoom(src: str | Path, rel: dict, n: int) -> Path | None:
    box = zoom_box(src, rel)
    return derive(src, f"z{n}", ZOOM_SCALE, box) if box else None
