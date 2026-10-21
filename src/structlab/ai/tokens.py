"""画像の入力トークン数（公式の式）。見積もりと、キャッシュの判断に使う。

公式（https://platform.claude.com/docs/en/build-with-claude/vision、2026-10 確認）:
  トークン = ⌈幅/28⌉ × ⌈高さ/28⌉（28×28 画素ごとに 1 トークン）
  Opus 5.5 は高解像度の区分: 長辺 2576 画素・1 枚 4784 トークンまで。超える画像はサーバーで縦横比を保って縮小され、
  縮小後の大きさで数えられる。
"""
from __future__ import annotations

import math

MAX_EDGE = 2576
MAX_TOKENS = 4784


def tokens_for_size(w: float, h: float, max_edge: int = MAX_EDGE) -> int:
    k = min(1.0, max_edge / max(w, h, 1))
    w, h = w * k, h * k
    n = math.ceil(w / 28) * math.ceil(h / 28)
    while n > MAX_TOKENS:  # サーバー側でさらに縮小される
        w, h = w * 0.99, h * 0.99
        n = math.ceil(w / 28) * math.ceil(h / 28)
    return n


def image_tokens(path, max_edge: int = MAX_EDGE, default: int = 1500) -> int:
    from PIL import Image
    try:
        with Image.open(path) as im:
            w, h = im.size
    except OSError:
        return default
    return tokens_for_size(w, h, max_edge)
