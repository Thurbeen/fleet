#!/usr/bin/env python3
"""`fleet sessions`: the thurbox sessions this fleet's lead and conductor spawned, seen from the queue.

    uv run fleet sessions orphans [--json] [--parent ID]

ORPHANS ARE WHAT `reap` CANNOT SEE. `reap` releases only a session a task
recorded, which is what makes it safe. Every other session the lead spawns —
a `review-prs` reviewer, a `diagnose-machine` sweep, a worker attached to no
task, a fixer whose task has since ended — is parented to the lead and named in
no live record, so no queue view shows it and nothing ever releases it. Asking
for those to be cleaned up was the operator's most repeated request.

So this lists every session whose parent is a CONTROL SESSION and which no
live task holds: none records it, or the one that does is `landed` or
`abandoned`. It DELETES NOTHING. Whether a reviewer's conversation is still
worth having is the operator's call, and the line under each row is the
command that makes it.

THE PARENTS. `dispatch` passes the session it runs in as `--parent`, so a
worker is parented to whoever dispatched it: the lead, the conductor, or an
origin. The control sessions are this checkout's lead and its conductor, as
`notify_conductor.py`'s `control_sessions` finds them — the rendered manifest's
names, or FLEET_LEAD_SESSION and FLEET_CONDUCTOR_SESSION — plus THURBOX_SESSION
when this runs inside a session, which is how an origin lists its own. An
origin is otherwise not enumerable: many may come and go, and none is named
anywhere fleet can read. `--parent` names one outright, and only that one.
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
notify = _load_sibling("fleet_notify_conductor", "notify_conductor.py")


def parent_ids(parent: str | None) -> tuple[dict[str, str], str]:
    """{session id: what it is} for the parents whose children are listed, or {} and why none was found."""
    if parent:
        return {parent: "the lead"}, ""
    found = {sid: f"the {role}" for sid, role in notify.control_sessions().items()}
    inside = os.environ.get("THURBOX_SESSION", "").strip()
    if inside and inside not in found:
        found[inside] = "this session"
    if found:
        return found, ""
    name = notify.lead_name()
    if name:
        return {}, notify.lead_session(name)[1]
    return {}, "no lead session is configured; name its id with --parent"


def _held_sessions() -> dict:
    """Each session id any task names, mapped to the tasks that name it."""
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
    return held


def orphans(parents) -> tuple[list | None, str]:
    """Every session parented to one of `parents` that no live task holds, oldest-idle first."""
    parents = [parents] if isinstance(parents, str) else list(parents)
    sessions, why = fleetqueue.session_snapshot()
    if sessions is None:
        return None, why
    held = _held_sessions()
    rows = []
    for sid, row in sessions.items():
        if sid in parents or row.get("parent_session_id") not in parents:
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
            "parent": row.get("parent_session_id"),
        })
    rows.sort(key=lambda r: -(r["idle_secs"] if isinstance(r["idle_secs"], (int, float)) else 0))
    return rows, ""


def cmd_orphans(args) -> int:
    parents, why = parent_ids(args.parent)
    if not parents:
        print(f"sessions orphans: {why}", file=sys.stderr)
        return 1
    rows, why = orphans(list(parents))
    if rows is None:
        print(f"sessions orphans: {why}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"lead": next(iter(parents)), "parents": list(parents), "orphans": rows}, indent=2))
        return 0
    whom = list(parents.values())
    whom = whom[0] if len(whom) == 1 else ", ".join(whom[:-1]) + " or " + whom[-1]
    print(f"orphans: {len(rows)} session(s) parented to {whom} that no live task holds")
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
    o.add_argument("--parent", help="one parent's session id; by default the lead, the conductor and this session")
    args = ap.parse_args(argv)
    return cmd_orphans(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
