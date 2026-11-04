#!/usr/bin/env python3
"""Remove cc-autoresume from a project: python3 uninstall.py <project_dir> [--scope project|user]
Restores settings.json (incl. the original status line) and deletes .claude/autoresume/.
Keeps .claude/autoresume.json and the committed state/log files (delete them yourself if wanted).
Scheduled tasks (one-shot wakes) are not touched: disable them with Claude (update_trigger enabled=false)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from install import main  # noqa: E402

sys.exit(main([*sys.argv[1:], "--uninstall"]))
