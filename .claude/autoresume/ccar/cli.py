"""ccar command line. Exit codes of resume-check: 0 proceed, 10 not reset (5h), 11 weekly, 12 locked, 13 superseded."""
from __future__ import annotations

import argparse
import json
import sys

from . import core


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ccar", description="Claude Code usage-limit stop / one-shot resume")
    p.add_argument("--config", help="path to .claude/autoresume.json (default: search upward from cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="current usage decision (JSON)")
    s = sub.add_parser("start-run"); s.add_argument("run_id", nargs="?", default="run")
    sub.add_parser("plan-wake", help="next one-shot wake times + update_trigger payloads")
    s = sub.add_parser("prompt"); s.add_argument("role", choices=["primary", "backup", "weekly"])
    s = sub.add_parser("resume-check"); s.add_argument("--role", default="primary", choices=["primary", "backup", "weekly"])
    s.add_argument("--pull", action="store_true"); s.add_argument("--run-id")
    s = sub.add_parser("lock"); s.add_argument("op", choices=["acquire", "release", "status"]); s.add_argument("run_id", nargs="?", default="run")
    s = sub.add_parser("checkpoint")
    for k in ("done", "todo", "next"):
        s.add_argument(f"--{k}", action="append", default=[])
    s.add_argument("-m", "--message", default=""); s.add_argument("--test", action="store_true")
    s.add_argument("--no-commit", action="store_true"); s.add_argument("--push", action="store_true"); s.add_argument("--add-all", action="store_true")
    sub.add_parser("hook", help="PreToolUse hook (reads hook JSON on stdin)")
    s = sub.add_parser("statusline", help="status line command (reads status JSON on stdin)"); s.add_argument("--chain")
    a = p.parse_args(argv)

    if a.cmd == "hook":       # must never break the session: any error -> allow silently
        try:
            raw = sys.stdin.read()
            r = core.hook(core.Ctx(a.config), json.loads(raw or "{}"))
            if r:
                print(json.dumps(r, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            print(f"ccar hook error (ignored): {e}", file=sys.stderr)
        return 0
    if a.cmd == "statusline":
        raw = sys.stdin.read()
        try:
            print(core.statusline(core.Ctx(a.config), raw, a.chain))
        except Exception as e:  # noqa: BLE001
            print(f"ccar: {e}")
        return 0
    c = core.Ctx(a.config)
    code, out = 0, None
    if a.cmd == "status":
        out = core.decide(c)
    elif a.cmd == "start-run":
        out = core.start_run(c, a.run_id)
    elif a.cmd == "plan-wake":
        out = core.plan_wake(c)
    elif a.cmd == "prompt":
        print(core.render_prompt(c, a.role)); return 0
    elif a.cmd == "resume-check":
        code, out = core.resume_check(c, a.role, a.pull, a.run_id)
    elif a.cmd == "lock":
        out = core.lock(c, a.op, a.run_id)
        code = 0 if out.get("acquired", True) else core.EXIT_LOCKED
    elif a.cmd == "checkpoint":
        out = core.checkpoint(c, a.done, a.todo, a.next, a.message, a.test, not a.no_commit, a.push, a.add_all)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
