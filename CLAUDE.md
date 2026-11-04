# CLAUDE.md — structural-experiment-ai

建築構造実験の資料解析・自動化。

- 進捗は `PROGRESS.md`。区切りごとに `python3 .claude/autoresume/ccar.py checkpoint --done … --todo … --next … --test --push`。
- 自動停止・再開は `docs/AUTO_RESUME.md`。新しいコンテナでは `python3 .claude/autoresume/install.py . --scope user` を実行。毎時の定期チェックは使わない。
- 実験の生データ・個人情報はコミットしない。推定値を公式値として扱わない。
