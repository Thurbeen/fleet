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
import time
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
                    # None for a local task. When it is set, `repo` above is a
                    # path on THAT machine and not on this one — a reader that
                    # showed the path alone would be showing a lie.
                    "host": t.doc.get("host"),
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
    # A remote task's `repo` is a path on its host, so `gh -C` here would ask
    # the wrong filesystem and report "no such directory" about a checkout that
    # exists. Skipped and SAID, rather than turned into an error that reads as
    # a broken record.
    live = [t for t in tasks if t.get("repo") and t.get("state") != "queued"]
    remote = [
        {"repo": f"(on host {h})",
         "reason": "runs on a remote host; its pull requests are read by "
                   "`queue.sh shepherd`, which asks the forge and not a checkout"}
        for h in sorted({t["host"] for t in live if t.get("host")})
    ]
    live = [t for t in live if not t.get("host")]
    repos: dict = {}
    for t in live:
        repos.setdefault(t["repo"], []).append(t)
    if not repos:
        sec["errors"] = remote
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
    # After the collapse, never inside it: a remote task is not a repo that
    # failed, so it must not count towards "every repo failed the same way".
    sec["errors"] += remote
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


# --- fuel --------------------------------------------------------------------

FUEL_PROVIDER = "claude"
# The floor the lead does not dispatch past, in percent remaining. FLEET.md's
# `## Fuel` section owns the rule; this is the same number so the screen can
# print it beside the reading.
#
# IT IS NOT quota-axi's `reserve`. That field is `pace.reservePercentPoints` —
# `percentRemaining - timeRemainingPercent`, a signed residual against the
# reset clock, which is negative whenever you are burning faster than linear
# and is `unknown` for every window whose pace could not be computed. Fleet
# needs an absolute floor that survives a stale reading, so it states its own.
FUEL_RESERVE = 20


def fuel_reason(state: dict) -> str:
    """Why quota-axi handed back no number, in its own words."""
    bits = [str(state.get("status") or "no reading")]
    for key in ("error", "reason"):
        value = state.get(key)
        if value:
            bits.append(str(value))
    if state.get("remedyCommand"):
        bits.append(f"remedy: {state['remedyCommand']}")
    return "; ".join(bits)


def fuel_windows(provider: dict) -> list:
    """The provider's windows that carry a number, in declaration order.

    Claude has three and they reset independently — `five_hour`, `seven_day`
    and a `model:<name>` week — so there is no one reset to report and
    quota-axi deliberately does not invent one. A window with no `resetsAt` has
    not been triggered yet rather than being a gap, so it is kept and its reset
    is simply absent.
    """
    out = []
    for w in provider.get("windows") or []:
        if not isinstance(w, dict):
            continue
        remaining = w.get("percentRemaining")
        if not isinstance(remaining, (int, float)) or isinstance(remaining, bool):
            continue
        out.append(
            {
                "id": str(w.get("id") or "?"),
                "label": w.get("label"),
                "remaining": remaining,
                "resets_at": str(w["resetsAt"]) if w.get("resetsAt") else None,
            }
        )
    return out


def probe_fuel() -> dict:
    """How much of the account's provider windows is left, per quota-axi.

    THE ONLY SOURCE. `thurbox-cli session get --json` carries no token, usage,
    cost or limit field, so nothing thurbox knows can answer this. quota-axi
    (MIT, github.com/kunchenguid/quota-axi) reads the first-party endpoints and
    normalises them. It is a tool on the operator's PATH and never a bundled
    dependency: absent, it costs this section and says so.

    IT MEASURES THE ACCOUNT, NOT A SESSION. These are the subscription windows
    the lead and every worker spend at once, so this section carries ONE
    reading and can never say what a given worker burned. Six workers
    dispatched together spend one window set six ways.

    IT READS `windows[]`, NOT THE HEADROOM SUMMARY. On a rate-limited fetch
    quota-axi answers with an empty `quota[]` and an `attention[]` row per
    scope — `headroom_unknown` — while the per-window percentages survive from
    its cache. Reading `effectiveAvailability[].effectivePercentRemaining`
    would report that live case as no reading at all; reading the windows
    reports a stale number and says it is stale, which is the true statement.
    The binding window is whichever has least remaining, and every window is
    printed so the reader can see the rest.

    IT REPORTS WHAT WAS MEASURED, NEVER WHAT WAS PROJECTED. quota-axi also
    publishes `pace`, `burnMultiple`, `runway`, `usableRunwaySeconds` and
    `projectedExhaustedAt`; those are forecasts, and FLEET.md forbids fleet
    from carrying one. `percentRemaining` and `resetsAt` are measurements — a
    spent window is spent, and when it comes back is the fact that can be acted
    on.

    `--provider claude` because that is the agent the fleet runs: every other
    provider on this machine is one the operator does not reach through fleet,
    and its auth state is not fleet's problem to report. `--full` because
    `state.refreshedAt` — the age that makes a cached number honest — is
    demoted out of the default tier; the account identity `--full` also returns
    is never read and never printed. `--no-credential-refresh` because this
    command READS: a plain quota read may delegate an expired session's
    renewal to the vendor CLI that owns it, and that is a write.

    Written against `schemaVersion` 5 and parsed defensively rather than
    pinned — a field this cannot find costs the section its reading and names
    itself, which is the same bargain every other probe makes.
    """
    sec: dict = {
        # EPOCH SECONDS, not the ISO instant the rest of this document speaks
        # in, because the reading is CACHED by its readers and an age is what
        # a cached number has to be drawn with. The TUI pane is the one that
        # cannot do the arithmetic itself: a thurbox pane has no `os`, so an
        # instant it cannot subtract is an instant it cannot age.
        "read_at": int(time.time()),
        "unavailable": None, "source": "quota-axi", "provider": FUEL_PROVIDER,
        "remaining": None, "reserve": FUEL_RESERVE, "below_reserve": None,
        "binding": None, "windows": [], "stale": None, "state": None,
        "refreshed_at": None, "retry_after": None, "error": None,
        "schema_version": None,
    }
    doc, why = run_json(
        ["quota-axi", "--provider", FUEL_PROVIDER, "--full", "--json",
         "--no-credential-refresh"],
        timeout=20,
    )
    if why:
        sec["unavailable"] = why
        return sec
    if not isinstance(doc, dict):
        sec["unavailable"] = "quota-axi did not answer a report"
        return sec
    sec["schema_version"] = doc.get("schemaVersion")
    provider = next(
        (p for p in doc.get("providers") or []
         if isinstance(p, dict) and p.get("provider") == FUEL_PROVIDER),
        None,
    )
    if provider is None:
        sec["unavailable"] = f"quota-axi reported no {FUEL_PROVIDER} provider"
        return sec

    state = provider.get("state") if isinstance(provider.get("state"), dict) else {}
    sec["state"], sec["stale"] = state.get("status"), bool(state.get("stale"))
    sec["error"] = state.get("error")
    sec["refreshed_at"] = state.get("refreshedAt")
    # `retryAfter` is documented as a state rather than a shape, so it is
    # printed only when it is already a sentence. When it is not, the same
    # instant is in `state.error`, which always is.
    if isinstance(state.get("retryAfter"), str):
        sec["retry_after"] = state["retryAfter"]

    windows = fuel_windows(provider)
    if not windows:
        # No number is quota-axi's own encoding of "no number", so it is
        # reported as one — never as a zero, which reads as a spent window
        # rather than an unread one.
        sec["unavailable"] = fuel_reason(state)
        return sec

    sec["windows"] = windows
    binding = min(windows, key=lambda w: w["remaining"])
    sec["binding"] = binding["id"]
    sec["remaining"] = binding["remaining"]
    sec["below_reserve"] = binding["remaining"] < FUEL_RESERVE
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
            # The host, when there is one, comes before everything else on the
            # line: where a task RUNS changes what every other field on it means.
            if t.get("host"):
                extra = f"on {t['host']}  {extra}".rstrip()
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



def render_fuel(sec: dict) -> list:
    if sec["unavailable"]:
        return [head("FUEL", f"unavailable — {sec['unavailable']}")]
    line = f"{sec['remaining']}% remaining   reserve {sec['reserve']}%"
    if sec["binding"]:
        line += f"   binding {sec['binding']}"
    lines = [
        head("FUEL", line),
        cont(f"{sec['provider']} — account windows, every session spends them at once"),
    ]
    for w in sec["windows"]:
        resets = f"resets {w['resets_at']}" if w["resets_at"] else "not triggered yet"
        binds = "  binds" if w["id"] == sec["binding"] else ""
        lines.append(f"    {w['id']:<16}{w['remaining']:>4}%  {resets}{binds}")
    # A cached number is a fact with an age, and the age is part of the fact.
    if sec["stale"] or (sec["state"] and sec["state"] != "fresh"):
        detail = [str(sec["state"] or "stale")]
        if sec["refreshed_at"]:
            detail.append(f"last refreshed {sec['refreshed_at']}")
        if sec["error"]:
            detail.append(str(sec["error"]))
        if sec["retry_after"]:
            detail.append(f"retry after {sec['retry_after']}")
        lines.append(cont("; ".join(detail)))
    if sec["below_reserve"]:
        lines.append(cont(f"! under the {sec['reserve']}% reserve — dispatch, do not investigate (FLEET.md `## Fuel`)"))
    return lines


RECORD_FIELDS = (
    "read_at", "unavailable", "reserve", "remaining", "below_reserve",
    "limited_by", "resets_at", "stale", "state", "provider", "scope",
)


def render_fuel_record(sec: dict) -> str:
    """The same reading, one `name<TAB>value` line per field.

    FOR A READER WITH NO JSON. `interface/fleet_queue.lua` draws this reading
    in the TUI column, and a thurbox pane is Lua with no JSON parser and no
    `os` — so the one format it can afford is lines and tabs, which is already
    how its queue probe answers. A tab because none of these values carries
    one.

    IT IS NOT A SECOND READING. Every field here is `probe_fuel()`'s own, under
    its own name, so the record and the FUEL section on the screen cannot come
    to different conclusions about what quota-axi said. Nothing is computed
    here and nothing is phrased here; `render_fuel` stays the only renderer
    that puts this into words.

    A FIELD WITH NO VALUE IS ABSENT, never empty and never zero. An unreadable
    reading carries `unavailable` and no `remaining` at all, because a
    `remaining` line reading 0 is the one way this could say "the window is
    spent" when it means "nobody could tell".
    """
    lines = []
    for name in RECORD_FIELDS:
        value = sec.get(name)
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            value = "1" if value else "0"
        elif isinstance(value, list):
            value = ",".join(str(v) for v in value)
        lines.append(f"{name}\t{value}")
    return "\n".join(lines) + "\n"


def render(doc: dict) -> str:
    blocks = [
        render_fuel(doc["fuel"]),
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
        "fuel": probe_fuel(),
        "queue": queue,
        "sessions": probe_sessions(tasks),
        "prs": probe_prs(tasks),
        "monitor": probe_monitor(None if queue["unavailable"] else queue["root"]),
        "checkout": probe_checkout(),
    }


def main(argv: list) -> int:
    p = argparse.ArgumentParser(prog="fleet-status.sh", add_help=True)
    p.add_argument("--json", action="store_true", help="the same reading, machine-readable")
    p.add_argument(
        "--fuel", action="store_true",
        help="only the fuel reading, as one name<TAB>value record per field",
    )
    args = p.parse_args(argv)

    # THE FUEL SECTION ALONE, AND AT ITS OWN COST. `--json` collects
    # everything, which is a `gh pr list` per repo in flight and a
    # `thurbox-cli session list` — a bill a reader that only wants the fuel
    # number should not pay, and one the TUI pane could not pay at all.
    if args.fuel:
        sec = probe_fuel()
        if args.json:
            print(json.dumps(sec, indent=2))
        else:
            sys.stdout.write(render_fuel_record(sec))
        return 0

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
