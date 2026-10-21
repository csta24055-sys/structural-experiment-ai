# GitHub Actions のワークフロー（全文）

## `.github/workflows/analyze.yml`

```yaml
# Dropbox の資料を解析する（手動実行）。
#   - 既定ブランチに取り込んだ後は、GitHub の Actions タブ → Analyze materials → Run workflow
#   - ops/requests/analyze.txt を変更して push（Claude Code が代わりに行えます）。
#     有料のAI解析は、そのファイルに allow_external_ai: true と書いたときだけ（利用者の承認後に限る）
# 解析結果を出力フォルダへ保存するため、Dropbox に files.content.write の許可が必要（docs/SETUP_DROPBOX.md 手順B）。
# 結果と処理状況は Dropbox の出力フォルダにのみ保存し、GitHub（Artifacts を含む）には保存しません。
name: Analyze materials

on:
  workflow_dispatch:
    inputs:
      allow_external_ai:
        description: "Claude（外部AI）へ資料を送信して解析する"
        type: boolean
        default: false
      max_runtime_minutes:
        description: "この時間（分）で安全に停止し、次回続きから"
        type: number
        default: 330
  push:
    paths:
      - "ops/requests/analyze.txt"

permissions:
  contents: read

concurrency:
  group: analyze-materials
  cancel-in-progress: false

jobs:
  analyze:
    runs-on: ubuntu-latest
    timeout-minutes: 355
    env:
      DROPBOX_APP_KEY: ${{ secrets.DROPBOX_APP_KEY }}
      DROPBOX_REFRESH_TOKEN: ${{ secrets.DROPBOX_REFRESH_TOKEN }}
      DROPBOX_APP_SECRET: ${{ secrets.DROPBOX_APP_SECRET }}
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY || secrets.CLAUDE_API_KEY || secrets.ANTHROPIC_KEY || secrets.CLAUDE_KEY }}
      STRUCTLAB_CONFIG: configs/dropbox.yaml
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: System packages
        run: |
          sudo apt-get update -q
          sudo apt-get install -y -q ffmpeg tesseract-ocr tesseract-ocr-jpn
      - name: Cache Whisper model
        uses: actions/cache@v4
        with:
          path: ~/.cache/huggingface
          key: whisper-${{ runner.os }}-large-v3-turbo
      - name: Python packages
        run: |
          python -m pip install -r requirements.txt
          python -m pip install -e . faster-whisper
      - name: Analyze
        run: |
          if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
            MINUTES="${{ inputs.max_runtime_minutes }}"; AI="${{ inputs.allow_external_ai }}"
          else
            MINUTES=$(sed -n 's/^max_runtime_minutes:[[:space:]]*\([0-9][0-9]*\).*/\1/p' ops/requests/analyze.txt | head -1)
            AI=$(sed -n 's/^allow_external_ai:[[:space:]]*\(true\|false\).*/\1/p' ops/requests/analyze.txt | head -1)
          fi
          FLAGS="--max-runtime ${MINUTES:-330}"
          if [ "$AI" = "true" ]; then FLAGS="$FLAGS --allow-external-ai"; fi
          scripts/run_analysis.sh $FLAGS
```

## `.github/workflows/connection-check.yml`

```yaml
# Dropbox と Claude API の接続確認（読み取りのみ・課金なし）。Dropbox には何も書き込みません。
# 起動方法:
#   - ops/requests/connection-check.txt を変更して push（Claude Code が代わりに行えます。どのブランチでも動きます）
#   - 既定ブランチに取り込んだ後は、GitHub の Actions タブ → Connection check → Run workflow
# 秘密情報は Secrets から環境変数として渡すだけで、表示・保存しません（GitHub がログ上でも伏せ字にします）。
name: Connection check

on:
  workflow_dispatch:
    inputs:
      download_samples:
        description: "動画・PDF・Excel を1件ずつ取得して読めるか確認する（終了時に削除）"
        type: boolean
        default: true
  push:
    paths:
      - "ops/requests/connection-check.txt"

permissions:
  contents: read

concurrency:
  group: connection-check
  cancel-in-progress: false

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      DROPBOX_APP_KEY: ${{ secrets.DROPBOX_APP_KEY }}
      DROPBOX_APP_SECRET: ${{ secrets.DROPBOX_APP_SECRET }}
      DROPBOX_REFRESH_TOKEN: ${{ secrets.DROPBOX_REFRESH_TOKEN }}
      # Claude API キーは ANTHROPIC_API_KEY の名前を推奨。別名で登録済みでも使えるようにする
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY || secrets.CLAUDE_API_KEY || secrets.ANTHROPIC_KEY || secrets.CLAUDE_KEY }}
      # 登録の有無だけ（true/false）。値ではない
      SECRET_SET_DROPBOX_APP_KEY: ${{ secrets.DROPBOX_APP_KEY != '' }}
      SECRET_SET_DROPBOX_APP_SECRET: ${{ secrets.DROPBOX_APP_SECRET != '' }}
      SECRET_SET_DROPBOX_REFRESH_TOKEN: ${{ secrets.DROPBOX_REFRESH_TOKEN != '' }}
      SECRET_SET_ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY != '' }}
      SECRET_SET_CLAUDE_API_KEY: ${{ secrets.CLAUDE_API_KEY != '' }}
      SECRET_SET_ANTHROPIC_KEY: ${{ secrets.ANTHROPIC_KEY != '' }}
      SECRET_SET_CLAUDE_KEY: ${{ secrets.CLAUDE_KEY != '' }}
      STRUCTLAB_CONFIG: configs/dropbox.yaml
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: System packages
        run: |
          sudo apt-get update -q
          sudo apt-get install -y -q ffmpeg
      - name: Python packages
        run: |
          python -m pip install -q -r requirements.txt
          python -m pip install -q -e .
      - name: Connection check
        run: |
          FLAGS=""
          if [ "${{ github.event_name }}" = "workflow_dispatch" ] && [ "${{ inputs.download_samples }}" = "false" ]; then FLAGS="--no-download"; fi
          python -m structlab -c "$STRUCTLAB_CONFIG" connect-check $FLAGS
```

## `.github/workflows/dropbox-auth.yml`

```yaml
# Dropbox の認可コードを GitHub Actions でリフレッシュトークンに交換し、GitHub Secrets に登録する。
#   mode: preflight … 本人認証の前の確認（App key/secret、PAT で Secrets を書き込めるか。Dropbox のコードは使わない）
#   mode: exchange  … Secret DROPBOX_AUTH_CODE を交換 → DROPBOX_REFRESH_TOKEN に登録 → DROPBOX_AUTH_CODE を削除 → 接続確認
# 起動: ops/requests/dropbox-auth.txt の mode を書き換えて push（Claude Code が行う）
# GITHUB_TOKEN は Secrets を書き込めないため、このリポジトリの Secrets 読み書きだけを許可した PAT（Secret GH_SECRETS_TOKEN）を使う。
# トークンの値はログ・コード・コミットに出さない（GitHub の公開鍵で暗号化して登録し、ログは伏せ字指定）。
name: Dropbox auth

on:
  push:
    paths:
      - "ops/requests/dropbox-auth.txt"

permissions:
  contents: read

concurrency:
  group: dropbox-auth
  cancel-in-progress: false

jobs:
  auth:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    env:
      DROPBOX_APP_KEY: ${{ secrets.DROPBOX_APP_KEY }}
      DROPBOX_APP_SECRET: ${{ secrets.DROPBOX_APP_SECRET }}
      STRUCTLAB_CONFIG: configs/dropbox.yaml
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Mode
        id: mode
        run: echo "mode=$(sed -n 's/^mode:[[:space:]]*\([a-z]*\).*/\1/p' ops/requests/dropbox-auth.txt | head -1)" >> "$GITHUB_OUTPUT"
      # コードは数分で失効するので、ライブラリの準備より先に標準ライブラリだけで交換する
      - name: Exchange authorization code
        if: steps.mode.outputs.mode == 'exchange'
        env:
          DROPBOX_AUTH_CODE: ${{ secrets.DROPBOX_AUTH_CODE }}
          PYTHONPATH: src
        run: python -m structlab.token_store exchange
      - name: Python packages
        run: |
          python -m pip install -q -r requirements.txt
          python -m pip install -q -e .
      - name: Preflight (before authorization)
        if: steps.mode.outputs.mode == 'preflight'
        env:
          GH_SECRETS_TOKEN: ${{ secrets.GH_SECRETS_TOKEN }}
        run: python -m structlab.token_store preflight
      - name: Store refresh token in GitHub Secrets
        if: steps.mode.outputs.mode == 'exchange'
        env:
          GH_SECRETS_TOKEN: ${{ secrets.GH_SECRETS_TOKEN }}
        run: python -m structlab.token_store store
      - name: System packages
        if: steps.mode.outputs.mode == 'exchange'
        run: |
          sudo apt-get update -q
          sudo apt-get install -y -q ffmpeg
      - name: Connection check (with the new token)
        if: steps.mode.outputs.mode == 'exchange'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY || secrets.CLAUDE_API_KEY || secrets.ANTHROPIC_KEY || secrets.CLAUDE_KEY }}
        run: python -m structlab -c "$STRUCTLAB_CONFIG" connect-check
```

## `.github/workflows/reading.yml`

```yaml
# 資料の読み取り基盤（structlab read）を全資料に対して実行する。Dropbox は読み取りのみで、何も書き込まない。
# 結果（棚卸し・資料ごとの書き起こし・照合・見積もり・場面の画像）は GitHub Actions の成果物（Artifacts、30日で削除）に置く。
# 読み取り結果は内容のハッシュごとにキャッシュし、同じ資料は読み直さない（AI の結果も再送しない）。
# 最後に資料をまたぐ整理（重複・差分、先生の実験経過 1→2 の時間軸、生徒の表裏の観察、先生と生徒の対応）を行う。
# 起動: ops/requests/reading.txt を変更して push（Claude Code が行う）
#   allow_external_ai: true は有料（Claude による画像の書き起こし・用語の訂正）。見積もりを利用者が承認した後だけ
name: Read materials

on:
  push:
    paths:
      - "ops/requests/reading.txt"

permissions:
  contents: read
  actions: read

concurrency:
  group: read-materials
  cancel-in-progress: false

env:
  STRUCTLAB_CONFIG: configs/dropbox.yaml
  DROPBOX_APP_KEY: ${{ secrets.DROPBOX_APP_KEY }}
  DROPBOX_APP_SECRET: ${{ secrets.DROPBOX_APP_SECRET }}
  DROPBOX_REFRESH_TOKEN: ${{ secrets.DROPBOX_REFRESH_TOKEN }}

jobs:
  plan:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    outputs:
      targets: ${{ steps.t.outputs.targets }}
      allow_ai: ${{ steps.t.outputs.allow_ai }}
      count_tokens: ${{ steps.t.outputs.count_tokens }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: |
          python -m pip install -q -r requirements.txt
          python -m pip install -q -e .
      - id: t
        run: |
          echo "targets=$(python -m structlab -c "$STRUCTLAB_CONFIG" read --list-targets)" >> "$GITHUB_OUTPUT"
          AI=$(sed -n 's/^allow_external_ai:[[:space:]]*\(true\|false\).*/\1/p' ops/requests/reading.txt | head -1)
          echo "allow_ai=${AI:-false}" >> "$GITHUB_OUTPUT"
          CT=$(sed -n 's/^count_tokens:[[:space:]]*\(true\|false\).*/\1/p' ops/requests/reading.txt | head -1)
          echo "count_tokens=${CT:-false}" >> "$GITHUB_OUTPUT"

  read:
    needs: plan
    runs-on: ubuntu-latest
    timeout-minutes: 355
    strategy:
      fail-fast: false
      max-parallel: 6
      matrix:
        target: ${{ fromJSON(needs.plan.outputs.targets) }}
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY || secrets.CLAUDE_API_KEY || secrets.ANTHROPIC_KEY || secrets.CLAUDE_KEY }}
      TARGET: ${{ matrix.target.path }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: System packages
        run: |
          sudo apt-get update -q
          sudo apt-get install -y -q ffmpeg tesseract-ocr tesseract-ocr-jpn
      - name: Cache Whisper model
        uses: actions/cache@v4
        with:
          path: ~/.cache/huggingface
          key: whisper-${{ runner.os }}-large-v3-turbo-and-large-v3
      - name: Python packages
        run: |
          python -m pip install -q -r requirements.txt
          python -m pip install -q -e . faster-whisper
      - name: Restore earlier reading of this file
        uses: actions/cache/restore@v4
        with:
          path: work/reading
          key: reading-${{ matrix.target.key }}-${{ github.run_id }}
          # 同じ内容・同じ設定の読み取り → 無ければ同じ内容の以前の読み取り（設定が変わった部分だけ読み直す）
          restore-keys: |
            reading-${{ matrix.target.key }}-
            reading-${{ matrix.target.base }}-
      - name: Read
        run: |
          FLAGS=""
          if [ "${{ needs.plan.outputs.allow_ai }}" = "true" ]; then FLAGS="--allow-external-ai"; fi
          # 入力トークンの実測（count_tokens。推論しないので課金なし）
          if [ "${{ needs.plan.outputs.count_tokens }}" = "true" ]; then FLAGS="$FLAGS --count-tokens"; fi
          python -m structlab -c "$STRUCTLAB_CONFIG" read --only "$TARGET" --out work/reading $FLAGS
      - name: Save reading of this file
        if: always()
        uses: actions/cache/save@v4
        with:
          # 拡大画像（derived）は元の解像度の画像からいつでも作り直せるので保存しない
          path: |
            work/reading
            !work/reading/**/derived
          key: reading-${{ matrix.target.key }}-${{ github.run_id }}
      - name: Artifact name
        if: always()
        id: n
        run: echo "name=reading-$(echo -n "$TARGET" | sha1sum | cut -c1-10)" >> "$GITHUB_OUTPUT"
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: ${{ steps.n.outputs.name }}
          path: |
            work/reading
            !work/reading/**/derived
          retention-days: 30
          if-no-files-found: warn

  summary:
    needs: [plan, read]
    if: always()
    runs-on: ubuntu-latest
    timeout-minutes: 355
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY || secrets.CLAUDE_API_KEY || secrets.ANTHROPIC_KEY || secrets.CLAUDE_KEY }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: actions/download-artifact@v4
        with:
          pattern: reading-*
          path: results
      - name: Summary
        run: |
          PYTHONPATH=src python -m structlab.reading.merge results | tee -a "$GITHUB_STEP_SUMMARY"
          echo; echo "== 棚卸し"; cat "$(ls results/*/inventory.md | head -1)" | tee -a "$GITHUB_STEP_SUMMARY"
      - name: Across materials (duplicates, teacher 1→2 timeline, student faces, correspondence)
        run: |
          python -m pip install -q -r requirements.txt
          python -m pip install -q -e .
          FLAGS=""
          if [ "${{ needs.plan.outputs.allow_ai }}" = "true" ]; then FLAGS="--allow-external-ai"; fi
          python -m structlab.reading.corpus results --out corpus -c "$STRUCTLAB_CONFIG" $FLAGS | tee -a "$GITHUB_STEP_SUMMARY"
          cp results/*/inventory.md corpus/ 2>/dev/null || true
          PYTHONPATH=src python -m structlab.reading.merge results > corpus/summary_all.md
      - name: Restore earlier meaning results (resume; identical inputs are not sent again)
        uses: actions/cache/restore@v4
        with:
          path: corpus/semantic_run/semantic.db
          key: semantic-${{ github.run_id }}
          restore-keys: |
            semantic-
      - name: Meaning and directives (3 passes, rules always; AI only when allowed)
        run: |
          FLAGS=""
          if [ "${{ needs.plan.outputs.allow_ai }}" = "true" ]; then FLAGS="--allow-external-ai"; fi
          python -m structlab.reading.semantic_bridge results --out corpus/semantic_run -c "$STRUCTLAB_CONFIG" $FLAGS
      - name: Save meaning results
        if: always()
        uses: actions/cache/save@v4
        with:
          path: corpus/semantic_run/semantic.db
          key: semantic-${{ github.run_id }}
      - name: Cost breakdown (where the cost arises; no API calls)
        if: always()
        run: |
          python -m structlab -c "$STRUCTLAB_CONFIG" cost --reading results --semantic-db corpus/semantic_run/semantic.db \
            --out corpus/costs | tee -a "$GITHUB_STEP_SUMMARY" || true
      - name: Requirements ledger (user instructions + teacher directives)
        if: always()
        run: |
          python -m structlab -c "$STRUCTLAB_CONFIG" requirements verify --stage analysis --reading results \
            --corpus corpus --semantic corpus/semantic_run --out corpus/requirements | tee -a "$GITHUB_STEP_SUMMARY"
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: reading-corpus
          path: corpus
          retention-days: 30
          if-no-files-found: warn
```

## `.github/workflows/tests.yml`

```yaml
name: Tests

on:
  push:
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: System packages
        run: |
          sudo apt-get update -q
          sudo apt-get install -y -q ffmpeg tesseract-ocr tesseract-ocr-jpn fonts-ipafont-gothic
      - name: Python packages
        run: |
          python -m pip install -r requirements.txt
          python -m pip install -e .
      - name: Run tests (dummy data only)
        run: python -m pytest -q
```
