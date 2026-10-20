"""各工程に組み込むチェックと、独立した再検証。結果は checks テーブルに記録する。"""
from .base import STAGE_LABEL, STATUS_LABEL, record

__all__ = ["STAGE_LABEL", "STATUS_LABEL", "record"]
