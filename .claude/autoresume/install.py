#!/usr/bin/env python3
"""Install cc-autoresume into a Claude Code project (idempotent).

  python3 install.py <project_dir> [--scope project|user] [--project-name N] [--branch B]
                     [--progress FILE ...] [--test-command CMD] [--resume-task TEXT] [--no-statusline]
  python3 install.py <project_dir> --uninstall [--scope ...]

What it does
  1. copies ccar/ + ccar.py to <project>/.claude/autoresume/
  2. writes <project>/.claude/autoresume.json if it does not exist (never overwrites)
  3. adds a PreToolUse hook (+ status line capture) to settings.json
       --scope project : <project>/.claude/settings.json   (relative to $CLAUDE_PROJECT_DIR)
       --scope user    : ~/.claude/settings.json            (absolute paths; use when the session's project
                                                             root is not the repo, e.g. some cloud sessions)
     An existing statusLine of another tool is kept and chained (ccar statusline --chain "<old command>").
  4. adds the private run directory to .gitignore
Scheduled tasks (one-shot wakes) are created by Claude with its scheduling tool; see MANUAL.md.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARK = "autoresume/ccar.py"


def settings_path(project: Path, scope: str, home: Path) -> Path:
    return (project / ".claude" / "settings.json") if scope == "project" else (home / ".claude" / "settings.json")


def load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def strip_ours(s: dict) -> dict:
    hooks = s.get("hooks", {})
    for ev in list(hooks):
        hooks[ev] = [m for m in hooks[ev] if not any(MARK in h.get("command", "") for h in m.get("hooks", []))]
        if not hooks[ev]:
            del hooks[ev]
    if not hooks:
        s.pop("hooks", None)
    sl = s.get("statusLine") or {}
    if MARK in sl.get("command", ""):
        cmd = sl["command"]
        if "--chain " in cmd:          # restore the chained original
            import shlex
            s["statusLine"] = {**sl, "command": shlex.split(cmd.split("--chain ", 1)[1])[0]}
        else:
            s.pop("statusLine", None)
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--scope", choices=["project", "user"], default="project")
    ap.add_argument("--home", default=str(Path.home()), help=argparse.SUPPRESS)
    ap.add_argument("--project-name", default="")
    ap.add_argument("--branch", default="")
    ap.add_argument("--progress", nargs="*", default=[])
    ap.add_argument("--test-command", default="")
    ap.add_argument("--resume-task", default="")
    ap.add_argument("--no-statusline", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--config-from", help="start the project config from this JSON (e.g. examples/*/autoresume.json)")
    a = ap.parse_args(argv)
    project, home = Path(a.project).resolve(), Path(a.home)
    dest = project / ".claude" / "autoresume"
    cfg_path = project / ".claude" / "autoresume.json"
    sp = settings_path(project, a.scope, home)
    raw = load(sp)
    prev_file = dest / f"previous_statusline.{a.scope}.json"
    original_sl = raw.get("statusLine") if MARK not in (raw.get("statusLine") or {}).get("command", MARK) else None
    s = strip_ours(raw)
    if prev_file.exists() and MARK in json.dumps(load(sp).get("statusLine") or {}):
        s["statusLine"] = json.loads(prev_file.read_text())      # exact original (incl. its own options)

    if a.uninstall:
        sp.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n")
        shutil.rmtree(dest, ignore_errors=True)    # safe even when running from dest (code already loaded)
        print(json.dumps({"uninstalled": str(dest), "settings": str(sp), "config_kept": str(cfg_path)}))
        return 0

    # 1. code
    dest.mkdir(parents=True, exist_ok=True)
    if HERE != dest:
        shutil.rmtree(dest / "ccar", ignore_errors=True)
        shutil.copytree(HERE / "ccar", dest / "ccar", ignore=shutil.ignore_patterns("__pycache__"))
    if HERE != dest:
        shutil.copy2(HERE / "ccar.py", dest / "ccar.py")
        shutil.copy2(HERE / "install.py", dest / "install.py")
        shutil.copy2(HERE / "uninstall.py", dest / "uninstall.py")
        shutil.copytree(HERE / "templates", dest / "templates", dirs_exist_ok=True)
    if original_sl:
        prev_file.write_text(json.dumps(original_sl))
    # 2. config (never overwrite)
    created = False
    if not cfg_path.exists():
        src = Path(a.config_from) if a.config_from else HERE / "templates" / "autoresume.example.json"
        cfg = json.loads(src.read_text(encoding="utf-8"))
        overrides = {"project": a.project_name, "branch": a.branch, "progress_files": a.progress,
                     "test_command": a.test_command, "resume_task": a.resume_task}
        cfg.update({k: v for k, v in overrides.items() if v})
        if not a.config_from:
            cfg.setdefault("project", project.name) if cfg.get("project") else cfg.update(project=project.name)
            if not a.project_name and cfg["project"] == "my-project":
                cfg["project"] = project.name
            cfg["install_scope"] = a.scope
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        created = True
    # 3. settings
    if a.scope == "project":
        launcher = '"$CLAUDE_PROJECT_DIR"/.claude/autoresume/ccar.py'
        base = f"python3 {launcher}"
    else:
        base = f'python3 "{dest / "ccar.py"}" --config "{cfg_path}"'
    s.setdefault("hooks", {}).setdefault("PreToolUse", []).append(
        {"matcher": "*", "hooks": [{"type": "command", "command": f"{base} hook", "timeout": 10}]})
    if not a.no_statusline:
        old = (s.get("statusLine") or {}).get("command")
        import shlex
        cmd = f"{base} statusline" + (f" --chain {shlex.quote(old)}" if old else "")
        s["statusLine"] = {"type": "command", "command": cmd, "refreshInterval": 60}
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n")
    # 4. gitignore
    priv = json.loads(cfg_path.read_text(encoding="utf-8")).get("private_dir", ".claude/autoresume/run")
    gi = project / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    if priv.rstrip("/") + "/" not in lines:
        gi.write_text("\n".join([*lines, priv.rstrip("/") + "/"]) + "\n")
    print(json.dumps({"installed": str(dest), "config": str(cfg_path), "config_created": created, "settings": str(sp),
                      "next": ["edit .claude/autoresume.json (project, branch, progress_files, resume_task, schedule)",
                               "python3 .claude/autoresume/ccar.py status",
                               "ask Claude to create the one-shot wakes from `ccar.py plan-wake` (see MANUAL.md)"]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
