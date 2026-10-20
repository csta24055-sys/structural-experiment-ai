"""設定ファイル（YAML）の読み込み。秘密情報は環境変数からのみ読む。"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "source": {"type": "dropbox", "path": ""},
    "output": {
        "work_dir": "./work",
        "dropbox_path": "",
        "sync_state": True,
        "sync_interval_minutes": 15,
    },
    "stability": {
        "min_age_seconds": 300,
        "min_stable_seconds": 300,
        "ignore_patterns": [
            "~$*", ".~lock.*", "*.tmp", "*.part", "*.crdownload",
            ".DS_Store", "desktop.ini", "Thumbs.db", ".dropbox*",
        ],
        "dedupe_identical_content": True,
    },
    "limits": {
        "max_file_size_mb": 20000,
        "min_free_disk_mb": 2000,
        "max_download_mb_per_run": 0,
        "max_runtime_minutes": 0,
        "max_attempts": 3,
        "keep_downloads": False,
    },
    "zip": {
        "max_total_uncompressed_mb": 20000,
        "max_files": 5000,
        "max_compression_ratio": 200,
        "max_nesting_depth": 2,
    },
    "pdf": {"render_dpi": 110, "max_chars_per_unit": 12000},
    "video": {
        "segment_seconds": 120,
        "sample_interval_seconds": 2.0,
        "scene_change_threshold": 0.02,
        "max_keyframes_per_segment": 6,
        "focus_analysis": True,
        "important_keywords": [
            "重要", "大事", "必ず", "注意", "提出", "締切", "期限", "禁止", "ダメ",
            "間違え", "ポイント", "覚えて", "レポート", "グラフ", "式",
        ],
    },
    "excel": {"rows_per_unit": 500, "max_cells_per_unit": 20000},
    "transcription": {
        "backend": "none", "model": "small", "language": "ja",
        "device": "cpu", "compute_type": "int8",
    },
    "ocr": {"backend": "auto", "languages": "jpn+eng"},
    "ai": {
        "provider": "none",
        "allow_external": False,
        "model": "claude-opus-5-5",
        "effort": "medium",
        "max_output_tokens": 8000,
        "use_fallbacks": True,
        "max_images_per_call": 6,
        "max_image_edge": 1568,
        "max_calls_per_run": 300,
        "max_input_tokens_per_run": 3_000_000,
        "max_output_tokens_per_run": 600_000,
        "max_calls_total": 0,
    },
}

# 設定ファイルに書かれていたら拒否するキー（秘密情報は環境変数のみ）
FORBIDDEN_KEYS = {"api_key", "access_token", "refresh_token", "app_secret", "token", "password"}


class ConfigError(ValueError):
    pass


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _check_no_secrets(d: Any, path: str = "") -> None:
    if isinstance(d, dict):
        for k, v in d.items():
            if str(k).lower() in FORBIDDEN_KEYS and v:
                raise ConfigError(
                    f"設定ファイルに秘密情報 '{path}{k}' が書かれています。環境変数で渡してください。"
                )
            _check_no_secrets(v, f"{path}{k}.")


class Section(dict):
    """dict だが属性アクセスもできる。"""

    def __getattr__(self, name: str) -> Any:
        try:
            v = self[name]
        except KeyError as e:
            raise AttributeError(name) from e
        return Section(v) if isinstance(v, dict) else v


@dataclass
class Config:
    data: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    base_dir: Path = field(default_factory=Path.cwd)

    def __getattr__(self, name: str) -> Section:
        if name in ("data", "base_dir"):
            raise AttributeError(name)
        try:
            return Section(self.data[name])
        except KeyError as e:
            raise AttributeError(name) from e

    @property
    def work_dir(self) -> Path:
        p = Path(self.data["output"]["work_dir"])
        return p if p.is_absolute() else (self.base_dir / p).resolve()

    @property
    def db_path(self) -> Path:
        return self.work_dir / "state" / "state.db"

    @property
    def results_dir(self) -> Path:
        return self.work_dir / "results"

    @property
    def cache_dir(self) -> Path:
        return self.work_dir / "cache"

    def validate(self) -> None:
        st = self.data["source"]["type"]
        if st not in ("dropbox", "local"):
            raise ConfigError(f"source.type が不正です: {st}")
        if not self.data["source"]["path"] and st == "local":
            raise ConfigError("source.path を指定してください")
        out = (self.data["output"].get("dropbox_path") or "").rstrip("/")
        src = (self.data["source"]["path"] or "").rstrip("/")
        if out and st == "dropbox":
            o, s = out.lower() + "/", (src.lower() + "/") if src else "/"
            if o.startswith(s) or s.startswith(o):
                raise ConfigError(
                    "output.dropbox_path は解析対象フォルダ(source.path)と重ならない場所にしてください"
                    "（元資料を読み取り専用に保つため）"
                )
        ai = self.data["ai"]
        if ai["provider"] not in ("none", "mock", "anthropic"):
            raise ConfigError(f"ai.provider が不正です: {ai['provider']}")
        tb = self.data["transcription"]["backend"]
        if tb not in ("none", "mock", "faster_whisper"):
            raise ConfigError(f"transcription.backend が不正です: {tb}")


def load_config(path: str | os.PathLike | None = None, overrides: dict | None = None) -> Config:
    data = copy.deepcopy(DEFAULTS)
    base_dir = Path.cwd()
    if path:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"設定ファイルが見つかりません: {p}")
        with open(p, encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
        _check_no_secrets(user)
        data = _deep_merge(data, user)
        base_dir = p.resolve().parent
    if overrides:
        data = _deep_merge(data, overrides)
    cfg = Config(data=data, base_dir=base_dir)
    cfg.validate()
    return cfg
