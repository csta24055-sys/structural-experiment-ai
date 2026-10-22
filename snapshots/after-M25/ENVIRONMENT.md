# 実行環境

保存データを作った環境: Python 3.13.16（Linux x86_64）。GitHub Actions は ubuntu-latest・Python 3.12（.github/workflows/*.yml の setup-python を参照）。

## Python ライブラリ（requirements.txt / requirements-optional.txt）

| モジュール | 版 |
|---|---|
| yaml | 6.0.3 |
| dropbox | 12.2.3 |
| fitz | 2.0 |
| openpyxl | 3.1.5 |
| PIL | 12.3.0 |
| numpy | 2.5.3 |
| anthropic | 1.15.0 |
| faster_whisper | 1.2.1 |
| pytest | 9.1.1 |
| reportlab | 5.0.1 |

## OS のコマンド

- ffmpeg: ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023 the FFmpeg developers
- ffprobe: ffprobe version 6.1.1-3ubuntu5 Copyright (c) 2007-2023 the FFmpeg developers
- tesseract: tesseract 5.3.4

## 必要な環境変数・GitHub Secrets（名前だけ。値は保存していない）

- `ANTHROPIC_API_KEY`
- `ANTHROPIC_KEY`
- `CLAUDE_API_KEY`
- `CLAUDE_KEY`
- `DROPBOX_APP_KEY`
- `DROPBOX_APP_SECRET`
- `DROPBOX_AUTH_CODE`
- `DROPBOX_REFRESH_TOKEN`
- `DROPBOX_REFRESH_TOKEN_FILE`
- `GH_SECRETS_TOKEN`
- `GITHUB_ACTIONS`
- `GITHUB_REPOSITORY`
- `GITHUB_STEP_SUMMARY`
- `STRUCTLAB_CONFIG`

値の登録方法は docs/SETUP_DROPBOX.md（手順A）・docs/SETUP_AI.md を参照。値をチャット・ログ・コードに出さない。

## 環境構築

```bash
sudo apt-get install -y ffmpeg tesseract-ocr tesseract-ocr-jpn   # OS のコマンド
python3 -m pip install -r requirements.txt -r requirements-optional.txt
python3 -m pip install -e .
python3 -m pytest -q -m "not slow"
```
