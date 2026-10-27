# structural-experiment-ai
建築構造実験の資料解析・自動化

- `structlab` … 実験資料（PDF・動画・画像・Excel）を Dropbox から取得して解析する既存のツール（`src/structlab/`）
- `structlab-exam` … 授業動画・配布資料・過去問・Box 共有リンクから出題が有力な論点を選び、
  対策教材と模擬テスト 3 回分を作る試験対策システム（`src/structlab/exam/`）。使い方は [docs/EXAM.md](docs/EXAM.md)

```bash
pip install -r requirements.txt && pip install -e .
python -m pytest -q
structlab-exam run --input 資料フォルダ
```

既存コードは添付された 2 つの全文ダンプから `tools/restore_dump.py` で復元しました（一覧と sha256 は `docs/restore/`）。
