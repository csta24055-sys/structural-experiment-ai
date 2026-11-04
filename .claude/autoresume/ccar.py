#!/usr/bin/env python3
"""Launcher: python3 ccar.py <command>. Works from a copy inside any project (no install needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ccar.cli import main  # noqa: E402

sys.exit(main())
