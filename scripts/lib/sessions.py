#!/usr/bin/env python3
"""`fleet sessions`: the thurbox sessions this fleet's lead spawned, seen from the queue.

    uv run fleet sessions orphans [--json] [--parent ID]

ORPHANS ARE WHAT `reap` CANNOT SEE. `reap` releases only a session a task
recorded, which is what makes it safe. Every other session the lead spawns —
a `review-prs` reviewer, a `diagnose-machine` sweep, a worker attached to no
task, a fixer whose task has since ended — is parented to the lead and named in
no live record, so no queue view shows it and nothing ever releases it. Asking
for those to be cleaned up was the operator's most repeated request.

So this lists every session whose parent is the lead and which no live task
holds: none records it, or the one that does is `landed` or `abandoned`. It
DELETES NOTHING. Whether a reviewer's conversation is still worth having is the
operator's call, and the line under each row is the command that makes it.

The lead is the session `notify_lead.py` would wake — the rendered manifest's
name, or FLEET_LEAD_SESSION — then THURBOX_SESSION when this runs inside it;
`--parent` names one outright.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys


def _load_sibling(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fleetqueue = _load_sibling("fleet_queue", "queue.py")
notify = _load_sibling("fleet_notify_lead", "notify_lead.py")


def lead_id(parent: str | None) -> tuple[str | None, str]:
    """The lead's session id, or None with the reason it could not be found."""
    if parent:
        return parent, ""
    name = notify.lead_name()
    if name:
        sid, status = notify.lead_session(name)
        return (sid, "") if sid else (None, status)
    inside = os.environ.get("THURBOX_SESSION", "").strip()
    if inside:
        return inside, ""
    return None, "no lead session is configured; name its id with --parent"


def orphans(lead: str) -> tuple[list | None, str]:
    """Every session parented to `lead` that no live task holds, oldest-idle first."""
    sessions, why = fleetqueue.session_snapshot()
    if sessions is None:
        return None, why
    held: dict = {}
    for task in fleetqueue.Queue(fleetqueue.queue_root(), scope="all").tasks.values():
        fixer = (task.doc.get("shepherd") or {}).get("session")
        # The sessions shepherd sent review comments to, one per pull request.
        talks = task.doc.get("shepherd_comments")
        talkers = {r.get("session") for r in talks.values() if isinstance(r, dict)} if isinstance(talks, dict) else set()
        # A session `reap` KEPT (`ON_LANDED`/`ON_CLOSED=notify`) left the
        # record's `session`, and is still this task's to name.
        reaped = task.doc.get("reaped") or {}
        kept = reaped.get("session") if reaped.get("how") == "kept" else None
        for sid in ({task.doc.get("session"), fixer, kept} | talkers) - {None}:
            held.setdefault(sid, []).append(task)
    rows = []
    for sid, row in sessions.items():
        if sid == lead or row.get("parent_session_id") != lead:
            continue
        tasks = sorted(held.get(sid, []), key=lambda t: t.ref)
        if any(t.state not in fleetqueue.TERMINAL_STATES for t in tasks):
            continue
        rows.append({
            "id": sid,
            "name": row.get("name"),
            "state": row.get("state"),
            "idle_secs": row.get("hook_state_age_secs"),
            "cwd": row.get("cwd"),
            "task": tasks[0].ref if tasks else None,
            "task_state": tasks[0].state if tasks else None,
        })
    rows.sort(key=lambda r: -(r["idle_secs"] if isinstance(r["idle_secs"], (int, float)) else 0))
    return rows, ""


def cmd_orphans(args) -> int:
    lead, why = lead_id(args.parent)
    if not lead:
        print(f"sessions orphans: {why}", file=sys.stderr)
        return 1
    rows, why = orphans(lead)
    if rows is None:
        print(f"sessions orphans: {why}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"lead": lead, "orphans": rows}, indent=2))
        return 0
    print(f"orphans: {len(rows)} session(s) parented to the lead that no live task holds")
    for row in rows:
        age = row["idle_secs"]
        since = f"  for {int(age) // 60}m" if isinstance(age, (int, float)) else ""
        print(f"    {row['id']}  {row['state'] or '?':<10} {row['name'] or ''}{since}")
        if row["task"]:
            print(f"        task {row['task']} is {row['task_state']}")
        else:
            print("        no task records it")
        print(f"        thurbox-cli session delete {row['id']} --force   # also removes its worktrees")
    return 0


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="fleet sessions", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    o = sub.add_parser("orphans", help="the lead's sessions that no live task holds")
    o.add_argument("--json", action="store_true", help="one JSON document instead of lines")
    o.add_argument("--parent", help="the lead's session id; by default the lead is found by name")
    args = ap.parse_args(argv)
    return cmd_orphans(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
