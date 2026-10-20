#!/usr/bin/env bash
# Dropbox の資料を解析する（クラウド実行用のワンコマンド）。
#   scripts/run_analysis.sh                       … ローカル抽出のみ（外部AIへ送信しない）
#   scripts/run_analysis.sh --allow-external-ai   … Claude による解析も行う（明示的な許可）
#   scripts/run_analysis.sh --max-runtime 300     … 300分で安全に停止（次回続きから）
# 設定ファイルは環境変数 STRUCTLAB_CONFIG で変更可（既定: configs/dropbox.yaml）
set -euo pipefail
cd "$(dirname "$0")/.."

CONFIG="${STRUCTLAB_CONFIG:-configs/dropbox.yaml}"

if ! python3 -c "import structlab" 2>/dev/null; then
  echo "== 依存ライブラリをインストールします"
  python3 -m pip install -q -r requirements.txt
  python3 -m pip install -q -e .
fi
if grep -q "faster_whisper" "$CONFIG" && ! python3 -c "import faster_whisper" 2>/dev/null; then
  python3 -m pip install -q faster-whisper || echo "!! faster-whisper を入れられませんでした（音声の文字起こしは行われません）"
fi
if ! command -v ffmpeg >/dev/null; then
  echo "!! ffmpeg がありません。動画は解析できません（apt-get install ffmpeg）"
fi

CHECK_ARGS=()
for a in "$@"; do [ "$a" = "--allow-external-ai" ] && CHECK_ARGS+=("$a"); done
echo "== 環境確認"
python3 -m structlab -c "$CONFIG" check "${CHECK_ARGS[@]}" || echo "!! 上の NG 項目を確認してください（docs/TROUBLESHOOTING.md）"

echo "== 解析開始（ログ: work/logs/）"
rc=0
python3 -m structlab -c "$CONFIG" run "$@" || rc=$?

echo "== 処理状況"
python3 -m structlab -c "$CONFIG" status
exit $rc
