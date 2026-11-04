"""cc-autoresume: usage-limit aware stop / one-shot resume for Claude Code projects.

Standard library only. No network access, no authentication, no undocumented APIs.

Data sources (in order of trust)
  official   rate_limits.{five_hour,seven_day}.{used_percentage,resets_at} that Claude Code passes to the
             status line command on stdin (subscribers, after the first API response). `ccar statusline`
             saves it. Fresh (< statusline_max_age_min) -> percentages are used for 85% / 90% decisions.
  official_reset  the same capture when it is stale: percentages are no longer trusted, but `resets_at`
             is still an official timestamp, so it is used to decide whether the window has reset.
  estimated  no capture at all (e.g. claude.ai cloud sessions, where the status line does not run):
             reset := recorded window start + window_hours. Always reported with "official": false.

Everything project-specific (paths, branch, trigger IDs, task text, thresholds) lives in the config file
(.claude/autoresume.json). Nothing here is tied to one repository.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "1.0.0"
CONFIG_NAME = ".claude/autoresume.json"

DEFAULTS = {
    "project": "",
    "remote": "origin",
    "branch": "",                       # empty = current branch
    "state_file": "AUTORESUME_STATE.json",   # committed: survives container resets
    "log_file": "AUTORESUME_LOG.md",          # committed checkpoint log (human readable)
    "private_dir": ".claude/autoresume/run",  # NOT committed: status line capture, lock, jsonl log
    "timezone_offset_hours": 9,
    "thresholds": {"prepare": 85, "stop": 90},
    "window_hours": 5,
    "weekly_fallback_hours": 169,       # 7 days + 1 h: always after any weekly reset
    "wake_margin_min": 5,
    "statusline_max_age_min": 15,
    "lock_stale_hours": 3,
    "install_scope": "project",
    "progress_files": [],
    "test_command": "",
    "resume_task": "",
    "commit_trailer": "",
    "schedule": {"backend": "claude-remote", "primary_trigger_id": "", "backup_trigger_id": "", "weekly_trigger_id": ""},
    "allow_when_stopped": ["^mcp__claude-code-remote__", "^Read$", "^Grep$", "^Glob$",
                           "Bash:^\\s*(git|cd [^;&|]+ && git)\\b", "Bash:autoresume/ccar\\.py|tools/cc-autoresume/ccar\\.py",
                           "Write:PROGRESS|STATE|HISTORY|CHECKLIST|AUTORESUME", "Edit:PROGRESS|STATE|HISTORY|CHECKLIST|AUTORESUME"],
}

EXIT_PROCEED, EXIT_NOT_YET, EXIT_WEEKLY, EXIT_LOCKED, EXIT_SUPERSEDED = 0, 10, 11, 12, 13


# ----------------------------------------------------------------------------- config / paths
def now() -> float:
    return float(os.environ.get("CCAR_NOW", time.time()))


def find_config(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    if os.environ.get("CCAR_CONFIG"):
        return Path(os.environ["CCAR_CONFIG"]).resolve()
    starts = [Path.cwd()]
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        starts.insert(0, Path(os.environ["CLAUDE_PROJECT_DIR"]))
    for s in starts:
        for d in [s, *s.resolve().parents]:
            if (d / CONFIG_NAME).exists():
                return (d / CONFIG_NAME).resolve()
    raise SystemExit(f"ccar: {CONFIG_NAME} not found (use --config or CCAR_CONFIG)")


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return out


class Ctx:
    def __init__(self, config_path: str | None = None):
        self.config_path = find_config(config_path)
        self.root = self.config_path.parent.parent
        self.cfg = _merge(DEFAULTS, json.loads(self.config_path.read_text(encoding="utf-8")))
        self.tz = timezone(timedelta(hours=self.cfg["timezone_offset_hours"]))
        self.private = self.root / self.cfg["private_dir"]
        self.state_path = self.root / self.cfg["state_file"]
        self.log_path = self.root / self.cfg["log_file"]
        self.capture = Path(os.environ.get("CCAR_STATUSLINE_FILE") or self.private / "statusline_latest.json")
        self.lock_dir = self.private / "lock"

    # -- helpers
    def iso(self, ts: float | None) -> str | None:
        return None if ts is None else datetime.fromtimestamp(ts, self.tz).isoformat(timespec="minutes")

    @staticmethod
    def utc(ts: float) -> str:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_state(self, st: dict) -> None:
        st["tool"] = f"cc-autoresume {VERSION}"
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    def log(self, event: str, **kw) -> None:
        self.private.mkdir(parents=True, exist_ok=True)
        with open(self.private / "autoresume.log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": self.iso(now()), "event": event, **kw}, ensure_ascii=False) + "\n")

    def git(self, *args: str, check: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, check=check)

    def branch(self) -> str:
        return self.cfg["branch"] or self.git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


# ----------------------------------------------------------------------------- usage / decision
def read_capture(c: Ctx) -> dict | None:
    try:
        d = json.loads(c.capture.read_text(encoding="utf-8") or "{}")
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    rl = d.get("rate_limits")
    if not rl:
        return None
    age = now() - c.capture.stat().st_mtime
    return {"five_hour": rl.get("five_hour") or {}, "seven_day": rl.get("seven_day") or {},
            "age_sec": int(age), "fresh": age <= c.cfg["statusline_max_age_min"] * 60}


def decide(c: Ctx) -> dict:
    """Current usage decision. Never presents an estimate as official."""
    t, st, cap = now(), c.load_state(), read_capture(c)
    th, margin = c.cfg["thresholds"], c.cfg["wake_margin_min"] * 60
    out = {"now": c.iso(t), "basis": "estimated", "official": False, "action": "continue", "reason": "", "limit": None}
    win = st.get("window") or {}
    if cap:
        fh, sd = cap["five_hour"], cap["seven_day"]
        r5, r7 = fh.get("resets_at"), sd.get("resets_at")
        out.update(five_hour_resets_at=c.iso(r5), seven_day_resets_at=c.iso(r7), capture_age_sec=cap["age_sec"])
        if cap["fresh"]:
            p5, p7 = float(fh.get("used_percentage") or 0), float(sd.get("used_percentage") or 0)
            out.update(basis="official", official=True, five_hour_pct=p5, seven_day_pct=p7)
            if p7 >= th["stop"] and r7:
                out.update(action="stop", limit="seven_day", reason=f"weekly {p7:.0f}% >= {th['stop']}%", resume_ts=r7 + margin)
            elif p5 >= th["stop"]:
                out.update(action="stop", limit="five_hour", reason=f"5-hour {p5:.0f}% >= {th['stop']}%", resume_ts=(r5 or t) + margin)
            elif p5 >= th["prepare"] or p7 >= th["prepare"]:
                out.update(action="prepare", reason=f"usage 5h {p5:.0f}% / 7d {p7:.0f}% >= {th['prepare']}%: finish the current step, checkpoint, do not start new large work")
            else:
                out["resume_ts"] = (r5 or t + c.cfg["window_hours"] * 3600) + margin
        else:
            # stale percentages are not trusted; the reset timestamps still are
            out.update(basis="official_reset", official=True, note="percentages stale; using official resets_at only")
            if r7 and r7 > t and float(sd.get("used_percentage") or 0) >= th["stop"]:
                out.update(action="wait", limit="seven_day", reason="weekly limit was reached and has not reset yet", resume_ts=r7 + margin)
            elif r5 and r5 > t and float(fh.get("used_percentage") or 0) >= th["stop"]:
                out.update(action="wait", limit="five_hour", reason="5-hour limit was reached and has not reset yet", resume_ts=r5 + margin)
            elif r5:
                out["resume_ts"] = (r5 if r5 > t else t + c.cfg["window_hours"] * 3600) + margin
    if out["basis"] == "estimated":
        out["note"] = "no status line data (e.g. cloud session): percentages unknown, reset time is an ESTIMATE"
        if win.get("start_ts"):
            est = win["start_ts"] + c.cfg["window_hours"] * 3600
            out.update(window_start=c.iso(win["start_ts"]), estimated_reset=c.iso(est), estimated_reset_ts=est, resume_ts=est + margin)
    if out.get("resume_ts"):
        out["resume_at"] = c.iso(out["resume_ts"])
    return out


def start_run(c: Ctx, run_id: str) -> dict:
    st, t = c.load_state(), now()
    win = st.get("window") or {}
    cap = read_capture(c)
    r5 = (cap or {}).get("five_hour", {}).get("resets_at")
    if r5 and r5 > t:
        win = {"start_ts": r5 - c.cfg["window_hours"] * 3600, "basis": "official resets_at"}
    elif not win.get("start_ts") or t >= win["start_ts"] + c.cfg["window_hours"] * 3600:
        win = {"start_ts": t, "basis": "estimated: start of this run"}
    win["start"] = c.iso(win["start_ts"])
    st["window"] = win
    st["last_run"] = {"id": run_id, "started_ts": t, "started": c.iso(t)}
    st.setdefault("runs", []).append({"id": run_id, "started": c.iso(t)})
    st["runs"] = st["runs"][-30:]
    c.save_state(st)
    c.log("start-run", id=run_id, window=win)
    return decide(c)


# ----------------------------------------------------------------------------- wake planning / prompts
def render_prompt(c: Ctx, role: str) -> str:
    cfg, launcher = c.cfg, os.path.relpath(Path(__file__).resolve().parents[1] / "ccar.py", c.root)
    cmd = f"cd {shlex.quote(str(c.root))} && python3 {launcher}"
    sch = cfg["schedule"]
    head = {"primary": "[auto-resume] one-shot wake after the usage window reset",
            "backup": "[auto-resume backup] retry in case the primary wake could not run",
            "weekly": "[auto-resume weekly fallback] retry after any weekly reset"}[role]
    inst = f"cd {shlex.quote(str(c.root))} && python3 {os.path.relpath(Path(__file__).resolve().parents[1] / 'install.py', c.root)} . --scope {cfg['install_scope']}"
    return "\n".join([
        f"{head} — project: {cfg['project'] or c.root.name}",
        f"0) A new container loses user-level settings: re-run `{inst}` (idempotent) so the usage hook is active.",
        f"1) {cmd} resume-check --role {role} --pull   (prints JSON).",
        f"   exit 0 = proceed. exit 10/11 = not reset yet (5h/weekly): do no work, run `plan-wake` and set this task's run_once_at to the printed time, then stop.",
        f"   exit 12 = another run holds the lock: do nothing and stop. exit 13 = superseded by a newer run: do nothing and stop.",
        "   An empty/0% reading alone is not proof of recovery; only the checks above are.",
        f"2) Resume from git + progress files ({', '.join(cfg['progress_files']) or 'see the log'}); never redo items marked done. Task: {cfg['resume_task']}",
        f"3) Checkpoint at every milestone: {cmd} checkpoint --done ... --todo ... --next ... --test --push",
        f"4) Before ending: {cmd} plan-wake, then update_trigger for each entry in its `triggers` list (same IDs, run_once_at only; never create duplicates), then {cmd} lock release.",
        "Never enable extra usage / pay-as-you-go. Never touch scheduled tasks of other projects.",
    ]) + (f"\nIDs: primary={sch['primary_trigger_id']} backup={sch['backup_trigger_id']} weekly={sch['weekly_trigger_id']}" if sch.get("primary_trigger_id") else "")


def plan_wake(c: Ctx) -> dict:
    d, t = decide(c), now()
    margin = c.cfg["wake_margin_min"] * 60
    primary = d.get("resume_ts") or t + c.cfg["window_hours"] * 3600 + margin
    primary = max(primary, t + 120)
    backup = primary + c.cfg["window_hours"] * 3600 + margin
    weekly = max(t + c.cfg["weekly_fallback_hours"] * 3600, backup + 3600)
    sch = c.cfg["schedule"]
    plan = {"basis": d["basis"], "official": d["official"], "decision": d,
            "primary": {"utc": c.utc(primary), "local": c.iso(primary)},
            "backup": {"utc": c.utc(backup), "local": c.iso(backup)},
            "weekly": {"utc": c.utc(weekly), "local": c.iso(weekly)}}
    plan["triggers"] = [{"role": r, "trigger_id": sch.get(f"{r}_trigger_id") or None, "run_once_at": plan[r]["utc"],
                         "prompt": render_prompt(c, r)} for r in ("primary", "backup", "weekly")]
    if sch["backend"] == "local-at":
        plan["local_commands"] = [local_at_command(c, r, plan[r]["utc"]) for r in ("primary", "backup", "weekly")]
    st = c.load_state()
    st["scheduled"] = {"primary_ts": primary, "primary": c.iso(primary), "backup": c.iso(backup), "weekly": c.iso(weekly),
                       "basis": d["basis"], "planned_at": c.iso(t)}
    c.save_state(st)
    c.log("plan-wake", primary=c.iso(primary), backup=c.iso(backup), basis=d["basis"])
    return plan


def local_at_command(c: Ctx, role: str, utc: str) -> str:
    """For a local machine: an `at` job that starts headless Claude Code (`claude -p`). Generated only."""
    when = datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone().strftime("%H:%M %Y-%m-%d")
    job = f"cd {shlex.quote(str(c.root))} && claude -p {shlex.quote(render_prompt(c, role))}"
    return f"echo {shlex.quote(job)} | at {when}"


# ----------------------------------------------------------------------------- lock
def lock(c: Ctx, op: str, run_id: str = "run") -> dict:
    c.private.mkdir(parents=True, exist_ok=True)
    owner = c.lock_dir / "owner.json"
    stale = c.cfg["lock_stale_hours"] * 3600

    def info():
        try:
            return json.loads(owner.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None
    if op == "status":
        i = info()
        return {"held": bool(i), **(i or {}), "stale": bool(i) and now() - i.get("ts", 0) > stale}
    if op == "release":
        if c.lock_dir.exists():
            owner.unlink(missing_ok=True)
            c.lock_dir.rmdir()
        c.log("lock-release", id=run_id)
        return {"held": False}
    for _ in range(2):
        try:
            c.lock_dir.mkdir()          # atomic
            owner.write_text(json.dumps({"id": run_id, "ts": now(), "at": c.iso(now()), "pid": os.getpid()}))
            c.log("lock-acquire", id=run_id)
            return {"acquired": True, "id": run_id}
        except FileExistsError:
            i = info() or {}
            if i.get("id") == run_id:
                return {"acquired": True, "id": run_id, "reentrant": True}
            if now() - i.get("ts", 0) > stale:
                owner.unlink(missing_ok=True)
                try:
                    c.lock_dir.rmdir()
                except OSError:
                    pass
                c.log("lock-stale-broken", previous=i)
                continue
            return {"acquired": False, "holder": i}
    return {"acquired": False}


# ----------------------------------------------------------------------------- resume check
def resume_check(c: Ctx, role: str = "primary", pull: bool = False, run_id: str | None = None) -> tuple[int, dict]:
    run_id = run_id or "run-" + datetime.fromtimestamp(now(), c.tz).strftime("%Y%m%d-%H%M")
    out: dict = {"role": role, "run_id": run_id}
    if pull:
        r = c.git("pull", "--ff-only", c.cfg["remote"], c.branch())
        out["git_pull"] = (r.stdout + r.stderr).strip()[-300:]
    st, t = c.load_state(), now()
    sch = st.get("scheduled") or {}
    if role != "primary" and (st.get("last_run") or {}).get("started_ts", 0) >= (sch.get("primary_ts") or 1e18) - 60:
        out.update(result="superseded", reason="a run already started at/after the primary wake time")
        c.log("resume-check", **out)
        return EXIT_SUPERSEDED, out
    d = decide(c)
    out["decision"] = d
    if d["action"] in ("stop", "wait"):
        code = EXIT_WEEKLY if d["limit"] == "seven_day" else EXIT_NOT_YET
        out.update(result="not_reset", reason=d["reason"], retry_at=d.get("resume_at"))
        c.log("resume-check", **out)
        return code, out
    if d["basis"] == "estimated" and d.get("estimated_reset_ts") and t < d["estimated_reset_ts"]:
        out.update(result="not_reset", reason="estimated reset time has not passed", retry_at=d.get("resume_at"))
        c.log("resume-check", **out)
        return EXIT_NOT_YET, out
    lk = lock(c, "acquire", run_id)
    if not lk.get("acquired"):
        out.update(result="locked", holder=lk.get("holder"))
        c.log("resume-check", **out)
        return EXIT_LOCKED, out
    out["recovery_evidence"] = ("official resets_at passed" if d["official"] else
                                "estimated reset passed and this turn is executing (requests succeed)")
    start_run(c, run_id)
    st = c.load_state()
    out.update(result="proceed", last_checkpoint=st.get("checkpoint"), progress_files=c.cfg["progress_files"],
               git_log=c.git("log", "--oneline", "-5").stdout.strip().splitlines(),
               git_status=c.git("status", "--short").stdout.strip().splitlines()[:20])
    c.log("resume-check", result="proceed", id=run_id, role=role)
    return EXIT_PROCEED, out


# ----------------------------------------------------------------------------- checkpoint
def checkpoint(c: Ctx, done: list[str], todo: list[str], nxt: list[str], message: str = "", run_test: bool = False,
               commit: bool = True, push: bool = False, add_all: bool = False) -> dict:
    st, d = c.load_state(), decide(c)
    test = None
    if run_test and c.cfg["test_command"]:
        r = subprocess.run(c.cfg["test_command"], shell=True, cwd=c.root, capture_output=True, text=True)
        tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
        test = {"command": c.cfg["test_command"], "exit": r.returncode, "tail": tail}
    usage = {k: d.get(k) for k in ("basis", "official", "five_hour_pct", "seven_day_pct", "five_hour_resets_at",
                                   "seven_day_resets_at", "estimated_reset", "action") if d.get(k) is not None}
    cp = {"at": c.iso(now()), "run": (st.get("last_run") or {}).get("id"), "done": done, "todo": todo, "next": nxt,
          "usage": usage, "test": test}
    st["checkpoint"] = cp
    c.save_state(st)
    lines = [f"\n## {cp['at']} — {cp['run'] or '-'}",
             *(f"- done: {x}" for x in done), *(f"- todo: {x}" for x in todo), *(f"- next: {x}" for x in nxt),
             f"- usage: {json.dumps(usage, ensure_ascii=False)}"]
    if test:
        lines.append(f"- test: exit {test['exit']} — {' / '.join(test['tail'])}")
    if not c.log_path.exists():
        c.log_path.write_text(f"# Auto-resume checkpoint log — {c.cfg['project'] or c.root.name}\n", encoding="utf-8")
    with open(c.log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    out = {"checkpoint": cp}
    if commit:
        c.git("add", "-A") if add_all else c.git("add", "-u")
        c.git("add", str(c.state_path), str(c.log_path))
        msg = message or f"checkpoint: {', '.join(done)[:60] or 'progress'}"
        if c.cfg["commit_trailer"]:
            msg += "\n\n" + c.cfg["commit_trailer"]
        r = c.git("commit", "-q", "-m", msg)
        out["commit"] = c.git("rev-parse", "--short", "HEAD").stdout.strip() if r.returncode == 0 else (r.stdout + r.stderr).strip()[-200:]
    if push:
        out["push"] = git_push(c)
    c.log("checkpoint", commit=out.get("commit"), push=out.get("push"))
    return out


def git_push(c: Ctx) -> str:
    delay = 2
    for i in range(5):
        r = c.git("push", "-u", c.cfg["remote"], c.branch())
        if r.returncode == 0:
            return "ok"
        if i < 4 and re.search(r"(?i)could not resolve|timed out|connection|network|502|503|504", r.stderr):
            time.sleep(delay)
            delay *= 2
            continue
        return "failed: " + r.stderr.strip()[-200:]
    return "failed"


# ----------------------------------------------------------------------------- hooks / status line
def hook(c: Ctx, payload: dict) -> dict | None:
    """PreToolUse hook. Only official, fresh data can block; estimates never block."""
    d = decide(c)
    tool, ti = payload.get("tool_name", ""), payload.get("tool_input") or {}
    if d["action"] == "stop":
        subject = ti.get("command") or ti.get("file_path") or ""
        for rule in c.cfg["allow_when_stopped"]:
            name, _, pat = rule.partition(":") if ":" in rule and not rule.startswith("^") else ("", "", rule)
            if (name and name == tool and re.search(pat, subject)) or (not name and re.search(pat, tool)):
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext":
                        f"usage guard: {d['reason']} — only saving/scheduling is allowed now."}}
        c.log("hook-deny", tool=tool, reason=d["reason"])
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason":
                f"usage guard: {d['reason']}. Do not start new work. Save progress (ccar checkpoint --push), run "
                f"ccar plan-wake and set the one-shot wake(s) to {d.get('resume_at')}, release the lock, then end."}}
    if d["action"] == "prepare":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "usage guard: " + d["reason"]}}
    return None


def statusline(c: Ctx, raw: str, chain: str | None = None) -> str:
    c.private.mkdir(parents=True, exist_ok=True)
    try:
        d = json.loads(raw or "{}")
    except json.JSONDecodeError:
        d = {}
    if d.get("rate_limits"):
        tmp = c.capture.with_suffix(".tmp")
        tmp.write_text(json.dumps({"rate_limits": d["rate_limits"], "captured": now()}))
        tmp.replace(c.capture)
    if chain:
        return subprocess.run(chain, shell=True, input=raw, capture_output=True, text=True).stdout.rstrip("\n")
    rl = d.get("rate_limits") or {}
    f = lambda k: f"{rl[k]['used_percentage']:.0f}%" if rl.get(k, {}).get("used_percentage") is not None else "?"
    return f"5h:{f('five_hour')} 7d:{f('seven_day')}"
