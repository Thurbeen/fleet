#!/usr/bin/env python3
# The lead's whole situational awareness, in one call. scripts/fleet-status.sh
# is the entry point and its header is the usage.
#
# WHY THIS EXISTS. Answering "where are we?" used to cost three to five
# commands spread over three checkouts and four tools: a `git status` and a
# `git log` per checkout, `queue.sh list`, `queue.sh plan`, `thurbox-cli
# session list`, `webui.sh status`, `gh pr list`. Most of a long session's tool
# calls were situational awareness rather than work, and every one of them cost
# a round trip and a piece of the context window. This is those calls, folded
# into one screen the lead can afford to run reflexively.
#
# THE ONE RULE: DEGRADE, NEVER FAIL. No network, no `gh`, no thurbox, no
# monitor, no queue — each of those costs exactly its own section, which then
# says what it could not determine and why. Every probe funnels through run(),
# which converts every way a subprocess can go wrong into a reason string, and
# every section carries an `unavailable` field that is either None or that
# reason. A status command that exits non-zero because one probe failed tells
# the lead nothing at all, which is strictly worse than not having one.
#
# THE OTHER RULE: IT READS. It starts nothing, stops nothing, syncs nothing and
# dispatches nothing. Every probe below is a list command or a `status`.
# scripts/fleet-status-selftest.sh proves both rules against stubs.
#
# It also does not re-derive what another command already resolves. The queue
# root comes from queue.py and the monitor's line comes from `webui.sh status`,
# so this can report that the dashboard is serving a DIFFERENT queue — the
# reading that answers "why is the dashboard not updated?" in one look —
# without being a third opinion about where either of them lives.

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone


def _load_queue():
    """Load scripts/lib/queue.py under a name that is not `queue`.

    The same trick webui.py uses, and for the same reason: this directory on
    sys.path would shadow the standard library's `queue` for the whole process.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.py")
    spec = importlib.util.spec_from_file_location("fleet_queue", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fleet_queue"] = module
    spec.loader.exec_module(module)
    return module


fleetqueue = _load_queue()

# The checkout this file ships in, found from the file rather than from the
# working directory — the lead may run this from anywhere.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LABEL = 10  # the left gutter every section header shares

# A task in one of these has said what it concluded; the rest are open.
# `landed` is concluded AND merged — the state whose session `queue.sh reap`
# has already released, so its absence from thurbox is expected, not news.
CONCLUDED = {"done", "landed", "stuck", "failed", "abandoned"}


# --- probing -----------------------------------------------------------------


def run(argv: list, cwd: str | None = None, timeout: int = 10) -> tuple[str | None, str | None]:
    """(stdout, None) or (None, why-not). Never raises, never inherits stdio.

    A missing tool, a tool that hangs, a tool that exits non-zero and a tool
    that is not executable are four different sentences the lead can act on,
    and none of them is an exception.
    """
    if not shutil.which(argv[0]):
        return None, f"{argv[0]} not found"
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"{argv[0]} timed out after {timeout}s"
    except OSError as exc:
        return None, f"{argv[0]}: {exc.strerror or exc}"
    if p.returncode != 0:
        detail = (p.stderr or p.stdout or "").strip().splitlines()
        return None, f"{argv[0]} exited {p.returncode}" + (f": {detail[0]}" if detail else "")
    return p.stdout, None


def run_json(argv: list, cwd: str | None = None, timeout: int = 10):
    out, why = run(argv, cwd=cwd, timeout=timeout)
    if why:
        return None, why
    try:
        return json.loads(out), None
    except ValueError:
        return None, f"{argv[0]} did not answer JSON"


# --- queue -------------------------------------------------------------------


def probe_queue() -> dict:
    root = os.path.abspath(fleetqueue.queue_root())
    sec: dict = {"unavailable": None, "root": root, "topics": [], "counts": {}, "risks": []}
    try:
        q = fleetqueue.Queue(root)
    except Exception as exc:  # a malformed record must cost this section only
        sec["unavailable"] = f"{root}: {exc}"
        return sec

    def display(task) -> str:
        if task.state != "queued":
            return task.state
        return "ready" if q.is_ready(task) else "waiting"

    counts: dict = {}
    for topic, tasks in sorted(q.by_topic().items()):
        entry = {
            "slug": topic,
            "title": q.topics.get(topic, {}).get("title", ""),
            "tasks": [],
        }
        for t in tasks:
            state = display(t)
            counts[state] = counts.get(state, 0) + 1
            entry["tasks"].append(
                {
                    "ref": t.ref,
                    "id": t.id,
                    "title": t.doc.get("title", ""),
                    "state": t.state,
                    "display_state": state,
                    "repo": t.doc.get("repo"),
                    "branch": t.doc.get("branch"),
                    "session": t.doc.get("session"),
                    "outcome": t.doc.get("outcome"),
                    "artifact": t.doc.get("artifact"),
                    "touches": list(t.touches),
                    "blockers": [
                        {
                            "task": b.get("task"),
                            "kind": b.get("kind"),
                            "why": b.get("why"),
                            "cleared": q.blocker_cleared(b),
                        }
                        for b in t.blockers
                    ],
                }
            )
        sec["topics"].append(entry)

    sec["counts"] = counts
    # Overlap across everything IN FLIGHT, not just the ready set: two workers
    # already editing one file is the risk the lead is living with right now.
    # It is reported and never acted on — a rebase reconciles it.
    in_flight = [t for t in q.tasks.values() if t.state == "dispatched" or q.is_ready(t)]
    sec["risks"] = q.overlaps(sorted(in_flight, key=lambda t: t.ref))
    return sec


def all_tasks(queue: dict) -> list:
    return [t for topic in queue.get("topics", []) for t in topic["tasks"]]


# --- sessions ----------------------------------------------------------------


def probe_sessions(tasks: list) -> dict:
    """The workers this queue dispatched, in thurbox's own vocabulary.

    `state` is copied through verbatim. `idle`, `running`, `uncovered` and
    `unreported` are four different facts (thurbox-session SKILL §4a) and the
    last three are not "no news" — collapsing any of them into `idle` reports a
    worker mid-turn as finished, which is the one mistake this section can make.

    `session list` and not `session get`: `list` does not probe panes, so this
    is one call for the whole fleet instead of one per worker. That is the
    difference between a status command the lead runs reflexively and one it
    thinks twice about.
    """
    wanted = [t for t in tasks if t.get("session")]
    sec: dict = {"unavailable": None, "sessions": []}
    if not wanted:
        return sec

    doc, why = run_json(["thurbox-cli", "session", "list", "--json"], timeout=10)
    if why:
        sec["unavailable"] = why
        return sec
    if not isinstance(doc, list):
        sec["unavailable"] = "thurbox-cli session list did not answer a list"
        return sec

    live = {s.get("id"): s for s in doc if isinstance(s, dict)}
    for t in sorted(wanted, key=lambda t: t["ref"]):
        sid = t["session"]
        s = live.get(sid)
        concluded = t["state"] in CONCLUDED
        if s is None:
            # A concluded task's session is MEANT to be gone — the loop's last
            # step deletes it — so that pairing is not news and is not printed.
            # The same absence under an OPEN task is: the worker went away
            # without concluding anything.
            if concluded:
                continue
            sec["sessions"].append(
                {"ref": t["ref"], "session": sid, "listed": False, "orphan": False,
                 "name": None, "state": None, "state_source": None,
                 "age_secs": None, "stopped": None}
            )
            continue
        sec["sessions"].append(
            {
                "ref": t["ref"],
                "session": sid,
                "listed": True,
                # Concluded, and still holding a session: the one thing the
                # lead has left to do about this task.
                "orphan": concluded,
                "name": s.get("name"),
                "state": s.get("state"),
                "state_source": s.get("state_source"),
                "age_secs": s.get("hook_state_age_secs"),
                "stopped": s.get("stopped"),
            }
        )
    return sec


# --- pull requests -----------------------------------------------------------

CHECK_FAIL = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}
CHECK_PASS = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def rollup(entries) -> str:
    """One word for a PR's checks: passing, failing, pending, or none."""
    if not isinstance(entries, list) or not entries:
        return "none"
    failing = pending = 0
    for e in entries:
        if not isinstance(e, dict):
            continue
        verdict = (e.get("conclusion") or e.get("state") or "").upper()
        if verdict in CHECK_FAIL:
            failing += 1
        elif verdict in CHECK_PASS:
            continue
        else:
            pending += 1
    if failing:
        return "failing"
    return "pending" if pending else "passing"


def pr_slug(url: str) -> str:
    """`Thurbeen/fleet#13` out of the URL gh already handed back.

    Deliberately not a second `gh repo view`: the identity is in the artifact,
    and a status command should not spend an API call to pretty-print a name.
    """
    parts = [p for p in str(url).split("/") if p]
    if len(parts) >= 4 and parts[-2] == "pull":
        return f"{parts[-4]}/{parts[-3]}#{parts[-1]}"
    return str(url)


def probe_prs(tasks: list) -> dict:
    """Open PRs in the repos this queue is working in, matched back to tasks.

    Matched by recorded artifact first, then by branch — so a PR a worker
    opened and has not reported yet still shows up, which is exactly the gap
    between "the worker should have opened a PR" and the artifact itself.

    One `gh pr list` per distinct repo, not one per task.
    """
    sec: dict = {"unavailable": None, "prs": [], "errors": []}
    live = [t for t in tasks if t.get("repo") and t.get("state") != "queued"]
    repos: dict = {}
    for t in live:
        repos.setdefault(t["repo"], []).append(t)
    if not repos:
        return sec

    reasons = []
    for repo, owners in sorted(repos.items()):
        if not os.path.isdir(repo):
            reasons.append(f"{repo}: no such directory")
            sec["errors"].append({"repo": repo, "reason": "no such directory"})
            continue
        doc, why = run_json(
            ["gh", "pr", "list", "--state", "open", "--limit", "50", "--json",
             "number,url,title,headRefName,state,statusCheckRollup"],
            cwd=repo,
            timeout=20,
        )
        if why:
            reasons.append(why)
            sec["errors"].append({"repo": repo, "reason": why})
            continue
        for pr in doc if isinstance(doc, list) else []:
            if not isinstance(pr, dict):
                continue
            url = str(pr.get("url") or "")
            head = pr.get("headRefName")
            owner = next(
                (t for t in owners if t.get("artifact") and str(t["artifact"]).rstrip("/") == url.rstrip("/")),
                None,
            ) or next((t for t in owners if head and t.get("branch") == head), None)
            if owner is None:
                continue  # somebody else's PR in the same repo
            sec["prs"].append(
                {
                    "ref": owner["ref"],
                    "repo": repo,
                    "slug": pr_slug(url),
                    "number": pr.get("number"),
                    "url": url,
                    "title": pr.get("title"),
                    "branch": head,
                    "checks": rollup(pr.get("statusCheckRollup")),
                }
            )

    # Every repo failed the same way — `gh` absent, most likely — so that is
    # the section's story rather than a list of identical per-repo errors.
    if reasons and len(sec["errors"]) == len(repos) and len(set(reasons)) == 1:
        sec["unavailable"] = reasons[0]
        sec["errors"] = []
    sec["prs"].sort(key=lambda p: p["ref"])
    return sec


# --- monitor -----------------------------------------------------------------


def probe_monitor(queue_root: str | None) -> dict:
    """`webui.sh status`, read rather than re-derived.

    Its `queue` line is the absolute directory the dashboard is actually
    serving (#11 added it for this), so comparing it with the queue section's
    root is what turns "why is the dashboard not updated?" into one look.
    """
    sec: dict = {
        "unavailable": None, "up": None, "url": None, "pid": None,
        "serving": None, "detail": None, "serves_this_queue": None,
    }
    script = os.path.join(REPO_ROOT, "scripts", "webui.sh")
    out, why = run([script, "status"], cwd=REPO_ROOT, timeout=20)
    if why:
        sec["unavailable"] = why
        return sec
    for line in (out or "").splitlines():
        head, _, rest = line.strip().partition(" ")
        rest = rest.strip()
        if head == "up":
            sec["up"], sec["url"] = True, rest
        elif head == "down":
            sec["up"], sec["detail"] = False, rest
        elif head == "pid":
            sec["pid"] = rest
        elif head == "queue":
            sec["serving"] = rest
    if sec["serving"] and queue_root:
        sec["serves_this_queue"] = os.path.abspath(sec["serving"]) == os.path.abspath(queue_root)
    return sec


# --- checkout ----------------------------------------------------------------


def probe_checkout() -> dict:
    sec: dict = {"unavailable": None, "path": REPO_ROOT, "branch": None, "head": None, "dirty": None}
    branch, why = run(["git", "-C", REPO_ROOT, "rev-parse", "--abbrev-ref", "HEAD"], timeout=10)
    if why:
        sec["unavailable"] = why
        return sec
    sec["branch"] = (branch or "").strip()
    head, why = run(["git", "-C", REPO_ROOT, "rev-parse", "--short", "HEAD"], timeout=10)
    sec["head"] = None if why else (head or "").strip()
    porcelain, why = run(["git", "-C", REPO_ROOT, "status", "--porcelain"], timeout=15)
    # The COUNT of changes, never their names: this output goes in front of an
    # agent that is orienting, not reviewing, and a file list is not orientation.
    sec["dirty"] = None if why else len([ln for ln in (porcelain or "").splitlines() if ln.strip()])
    return sec


# --- rendering ---------------------------------------------------------------


def age(secs) -> str:
    if secs is None:
        return "-"
    try:
        s = int(secs)
    except (TypeError, ValueError):
        return "-"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def head(label: str, rest: str = "") -> str:
    return f"{label:<{LABEL}}{rest}".rstrip()


def cont(rest: str) -> str:
    return f"{'':<{LABEL}}{rest}".rstrip()


def render_queue(sec: dict) -> list:
    if sec["unavailable"]:
        return [head("QUEUE", f"unavailable — {sec['unavailable']}")]
    lines = [head("QUEUE", sec["root"])]
    topics, tasks = sec["topics"], sum(len(t["tasks"]) for t in sec["topics"])
    if not tasks:
        return lines + [cont("empty — `queue.sh topic add` opens one")]
    order = ("ready", "waiting", "dispatched", "done", "landed", "stuck", "failed",
             "abandoned")
    tally = [f"{k} {sec['counts'][k]}" for k in order if sec["counts"].get(k)]
    lines.append(cont(f"{len(topics)} topic(s), {tasks} task(s) — " + ", ".join(tally)))
    for topic in topics:
        lines.append(f"  {topic['slug']} — {topic['title']}")
        for t in topic["tasks"]:
            if t["artifact"]:
                extra = f"{t['outcome'] or ''} {t['artifact']}".strip()
            elif t["display_state"] == "dispatched" and t["session"]:
                extra = t["session"][:8]
            else:
                extra = t["branch"] or ""
            lines.append(f"    {t['id']:<34} {t['display_state']:<11} {extra}")
            for b in t["blockers"]:
                if not b["cleared"]:
                    lines.append(f"        held by {b['kind']} on {b['task']}: {b['why']}")
    for risk in sec["risks"]:
        lines.append(f"  risk: {', '.join(risk['tasks'])} all touch {risk['touches']}")
    return lines


def render_sessions(sec: dict) -> list:
    if sec["unavailable"]:
        return [head("SESSIONS", f"unavailable — {sec['unavailable']}")]
    rows = sec["sessions"]
    if not rows:
        return [head("SESSIONS", "none dispatched by this queue")]
    lines = [head("SESSIONS", f"{len(rows)} still attached to a task")]
    for s in rows:
        if not s["listed"]:
            lines.append(
                f"    {'not listed':<11} {'-':>5}  {'-':<8} {s['ref']}"
                f"  (session {s['session'][:8]} is gone, and the task is still open)"
            )
            continue
        note = "  stopped" if s.get("stopped") else ""
        if s.get("orphan"):
            note += "  — task concluded; delete the session"
        lines.append(
            f"    {str(s['state']):<11} {age(s['age_secs']):>5}  "
            f"{str(s['state_source'] or '-'):<8} {s['ref']}{note}"
        )
    return lines


def render_prs(sec: dict) -> list:
    if sec["unavailable"]:
        return [head("PRS", f"unavailable — {sec['unavailable']}")]
    prs = sec["prs"]
    lines = [head("PRS", f"{len(prs)} open, matched to this queue's tasks" if prs else "none open for these tasks")]
    for p in prs:
        lines.append(f"    {p['slug']:<24} {p['checks']:<8} {p['branch'] or '':<32} {p['ref']}")
    for e in sec["errors"]:
        lines.append(f"    {e['repo']}: {e['reason']}")
    return lines


def render_monitor(sec: dict) -> list:
    if sec["unavailable"]:
        return [head("MONITOR", f"unavailable — {sec['unavailable']}")]
    if sec["up"]:
        lines = [head("MONITOR", f"up    {sec['url']}")]
    else:
        lines = [head("MONITOR", f"down  {sec['detail'] or ''}".rstrip())]
    if sec["serving"]:
        lines.append(cont(f"serving {sec['serving']}"))
        if sec["serves_this_queue"] is False:
            lines.append(cont("! that is NOT the queue above — the dashboard is showing another one"))
    return lines


def render_checkout(sec: dict) -> list:
    if sec["unavailable"]:
        return [head("CHECKOUT", f"unavailable — {sec['unavailable']}")]
    if sec["dirty"] is None:
        tree = "tree unread"
    elif sec["dirty"]:
        tree = f"{sec['dirty']} change(s)"
    else:
        tree = "clean"
    return [head("CHECKOUT", f"{sec['path']}  {sec['branch']} @ {sec['head'] or '?'}  {tree}")]


def render(doc: dict) -> str:
    blocks = [
        render_queue(doc["queue"]),
        render_sessions(doc["sessions"]),
        render_prs(doc["prs"]),
        render_monitor(doc["monitor"]),
        render_checkout(doc["checkout"]),
    ]
    out = [f"fleet status  ·  {doc['generated']}", ""]
    for block in blocks:
        out += block + [""]
    return "\n".join(out).rstrip() + "\n"


# --- entry point -------------------------------------------------------------


def collect() -> dict:
    queue = probe_queue()
    tasks = all_tasks(queue)
    return {
        "generated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "queue": queue,
        "sessions": probe_sessions(tasks),
        "prs": probe_prs(tasks),
        "monitor": probe_monitor(None if queue["unavailable"] else queue["root"]),
        "checkout": probe_checkout(),
    }


def main(argv: list) -> int:
    p = argparse.ArgumentParser(prog="fleet-status.sh", add_help=True)
    p.add_argument("--json", action="store_true", help="the same reading, machine-readable")
    args = p.parse_args(argv)
    doc = collect()
    if args.json:
        print(json.dumps(doc, indent=2))
    else:
        sys.stdout.write(render(doc))
    # Always 0. The exit code says this command ran, not that the fleet is
    # healthy — a section that could not be read says so in its own words.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
