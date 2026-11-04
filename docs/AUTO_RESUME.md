# 自動停止・自動再開（cc-autoresume 1.0.0）

移植元：csta24055-sys/-1 `claude/intelligent-darwin-l3q4kc` の `tools/cc-autoresume/`（共通モジュール、標準ライブラリのみ）。
コード：`.claude/autoresume/`（コミット対象）／設定：`.claude/autoresume.json`／状態：`AUTORESUME_STATE.json`／ログ：`AUTORESUME_LOG.md`
非公開（コミットしない）：`.claude/autoresume/run/`（status line の取得値・ロック・jsonl ログ）

## 判定方式（正直な記載）
| 環境 | 利用率 | 85% 準備 / 90% 停止 | リセット時刻 |
|---|---|---|---|
| ローカル CLI（status line が動く） | 公式 `rate_limits`（取得から15分以内のみ採用） | **有効**（PreToolUse フックで新規作業を拒否） | 公式 `resets_at` |
| claude.ai クラウド（このセッション） | **取得不可**（status line が実行されない） | **発動しない** | 推定：実行開始＋5時間（`official:false`） |

クラウドでは 90% で停止できない。推定方式は「前回の実行開始から5時間後＋5分」に一回限りで起こし、`resume-check` が推定リセット経過・ロック・新しい実行の有無を確認してから再開する。

## 起動予約（一回限り、このセッションに届く）
| 役割 | ID | 用途 |
|---|---|---|
| primary | trig_01Wgq3pthHL8ZMdqeNqHv2hH | 推定リセット＋5分 |
| backup | trig_012iSXEygHhy7ArKbjvh4vA3 | primary＋5時間5分（primary が動けなかった場合） |
| weekly | trig_01LR9yTiMjxMAnCTptQfjKxv | 7日＋1時間（週間枠リセット後の保険。実行ごとに先送り） |

毎時の定期起動は使わない。予約は毎回 `plan-wake` の時刻で **同じ ID のまま** `run_once_at` を更新する（新規作成しない）。
`PROGRESS.md` の次の作業がユーザー入力待ちだけなら、再予約せず3件を `enabled=false` にして止める。

## コマンド
```bash
L="python3 .claude/autoresume/ccar.py"
$L status                         # 現在の判定（basis: official / official_reset / estimated）
$L checkpoint --done … --todo … --next … --test --push   # 進捗保存＋コミット＋push（2/4/8/16秒再試行）
$L plan-wake                      # 次の起動時刻と update_trigger 用ペイロード
$L resume-check --role primary --pull   # 0 続行 / 10 5h未回復 / 11 週間未回復 / 12 他が実行中 / 13 既に新しい実行あり
$L lock acquire|release|status <id>     # 二重起動防止（mkdir 原子性、3時間で古いロック解除）
```
新しいコンテナでは `python3 .claude/autoresume/install.py . --scope user`（冪等）で `~/.claude/settings.json` にフックを再登録する。
アンインストール：`python3 .claude/autoresume/uninstall.py . --scope user`、予約は `update_trigger enabled=false`。

## 既存の自動化との関係（2026-11-04 確認）
- 環境の SessionStart / Stop フック（launcher-settings.json）は変更していない。本ツールは PreToolUse と statusLine のみ追加。Stop フック（未コミット確認）とは checkpoint のコミットで整合する。
- 同じアカウントに他プロジェクト（AQ Research OS、YUCACO）の予約と毎時チェックがあるが、別セッション宛て。変更していない。利用枠は共有なので、それらが動くと本プロジェクトの推定リセット時刻はさらに不正確になる。
