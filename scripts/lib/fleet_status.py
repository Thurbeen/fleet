#!/usr/bin/env python3
"""The lead's whole situational awareness, in one call — the fuel, the queue,
the workers, the pull requests and this checkout, on one screen.

Usage:
  uv run fleet status             # the screen
  uv run fleet status --json      # the same reading, machine-readable
  uv run fleet status --fuel      # the fuel section alone, one field per line
  uv run fleet status --records   # validate your queue records and registry map

WHY THIS EXISTS. Answering "where are we?" used to cost three to five
commands spread over three checkouts and four tools: a `git status` and a
`git log` per checkout, `fleet queue list`, `fleet queue plan`, `thurbox-cli
session list`, the forge's own list. Most of a long session's tool
calls were situational awareness rather than work, and every one of them cost
a round trip and a piece of the context window. This is those calls, folded
into one screen the lead can afford to run reflexively.

THE ONE RULE: DEGRADE, NEVER FAIL. No network, no forge CLI, no thurbox, no
queue — each of those costs exactly its own section, which then
says what it could not determine and why. Every probe funnels through run(),
which converts every way a subprocess can go wrong into a reason string, and
every section carries an `unavailable` field that is either None or that
reason. A status command that exits non-zero because one probe failed tells
the lead nothing at all, which is strictly worse than not having one.

THE OTHER RULE: IT READS. It starts nothing, stops nothing, syncs nothing and
dispatches nothing. Every probe below is a list command or a `status`.
tests/status/ proves both rules against stubs. The exit status is 0 for
"this command ran", never for "the fleet is healthy" — read the sections.

It also does not re-derive what another command already resolves. The queue
root comes from queue.py, so this is never a second opinion about which
records it is reading.

FUEL IS THE ACCOUNT'S, NOT A SESSION'S. It comes from `quota-axi`, the only
source that has a number at all — `thurbox-cli session get --json` carries no
token, usage, cost or limit field. quota-axi measures the subscription window
every session spends at once, so there is one reading per account and no
per-worker breakdown to be had. FLEET.md's `## Fuel` section owns the reserve
and what the lead does near it.

AN ACCOUNT IS A PROVIDER PLUS AN ENVIRONMENT, and the screen reads every one
the fleet spends — not just the one this command happens to run as. Which
accounts exist is `orchestration/agent.conf`, through
`scripts/lib/agent_settings.py`'s `ENV` records; `fuel_accounts()` reads them
and `probe_fuel_all()` argues the cost. `fleet queue refuel` reads the same
records through `probe_fuel()`, so the gate and the screen cannot disagree
about which window a worker is sitting on.

`--fuel` IS THAT SECTION ALONE, as `name<TAB>value` records — one per
reading, separated by a blank line. It exists for the TUI queue pane, which
draws the same readings and can afford neither `--json` (which collects
every section, so a `gh pr list` per repo in flight) nor a JSON parser. It
prints `probe_fuel_all()`'s own fields under their own names, so the pane
and this screen cannot come to different conclusions about what quota-axi
said.

`--records` IS THE OPERATOR'S HEALTH CHECK, argued at the records section
below. It is a flag and not a section because it opens every record,
archived topics' included, and the screen promises never to.

Environment: FLEET_QUEUE_DIR, honoured exactly as `fleet queue` honours it,
and FLEET_REGISTRY_FILE, which relocates the registry map the same way.

Requires: uv. thurbox-cli, gh, git and quota-axi are each optional and cost
only their own section — quota-axi in particular is a tool on the operator's
PATH, never a dependency this repo vendors.
"""

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

    Loaded under another name because this directory on sys.path would
    shadow the standard library's `queue` for the whole process, and under the
    key every other loader uses, so a process that already holds queue.py gets
    that copy rather than a second one with a forge registry of its own.
    """
    if "fleet_queue" in sys.modules:
        return sys.modules["fleet_queue"]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.py")
    spec = importlib.util.spec_from_file_location("fleet_queue", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fleet_queue"] = module
    spec.loader.exec_module(module)
    return module


fleetqueue = _load_queue()

# The agent settings seam, which queue.py has already loaded and keyed — the
# SAME module object, so this file and `fleet queue` resolve a per-agent
# setting one way and not two.
agent_settings = fleetqueue.agent_settings

# The forge seam, which queue.py has already loaded and keyed in sys.modules —
# so this is the SAME module object and therefore the same registry, not a
# second opinion about which forges are configured.
forge = fleetqueue.forge

# The checkout this file ships in, found from the file rather than from the
# working directory — the lead may run this from anywhere.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LABEL = 10  # the left gutter every section header shares

# A task in one of these has said what it concluded; the rest are open.
# `landed` is concluded AND merged — the state whose session `fleet queue reap`
# has already released, so its absence from thurbox is expected, not news.
CONCLUDED = {"done", "landed", "stuck", "failed", "abandoned"}


# --- probing -----------------------------------------------------------------


def run(argv: list, cwd: str | None = None, timeout: int = 10,
        env: dict | None = None) -> tuple[str | None, str | None]:
    """(stdout, None) or (None, why-not). Never raises, never inherits stdio.

    A missing tool, a tool that hangs, a tool that exits non-zero and a tool
    that is not executable are four different sentences the lead can act on,
    and none of them is an exception.

    `env` is the WHOLE environment the child gets, already merged by its
    caller, and None means this process's — which is what every probe here but
    the fuel gauge wants.
    """
    if not shutil.which(argv[0]):
        return None, f"{argv[0]} not found"
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                           encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"{argv[0]} timed out after {timeout}s"
    except OSError as exc:
        return None, f"{argv[0]}: {exc.strerror or exc}"
    if p.returncode != 0:
        detail = (p.stderr or p.stdout or "").strip().splitlines()
        return None, f"{argv[0]} exited {p.returncode}" + (f": {detail[0]}" if detail else "")
    return p.stdout, None


def run_json(argv: list, cwd: str | None = None, timeout: int = 10, env: dict | None = None):
    out, why = run(argv, cwd=cwd, timeout=timeout, env=env)
    if why:
        return None, why
    try:
        return json.loads(out), None
    except ValueError:
        return None, f"{argv[0]} did not answer JSON"


# --- queue -------------------------------------------------------------------


def probe_queue() -> dict:
    root = os.path.abspath(fleetqueue.queue_root())
    sec: dict = {"unavailable": None, "root": root, "topics": [], "counts": {},
                 "risks": [], "archived": 0}
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
                        fleetqueue.blocker_view(q, t, b) for b in t.blockers
                    ],
                    # Everything the one-line row cannot say: a state that
                    # disagrees with its own outcome, a task nothing dispatched,
                    # and the blockers that are actually holding something.
                    # queue.py derives it, so this cannot say it differently
                    # from `fleet queue list` or the pane.
                    "notes": fleetqueue.task_notes(q, t),
                }
            )
        sec["topics"].append(entry)

    sec["counts"] = counts
    # Topics this reading declined to open. Same number `fleet queue list` prints
    # and the TUI pane shows: they are three readers over one set of records,
    # and the whole point of a status screen is that it cannot disagree with
    # the thing it is reporting on.
    sec["archived"] = len(q.archived_hidden)
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


# --- change requests ---------------------------------------------------------


def rollup(checks) -> str:
    """One word for a change request's checks: passing, failing, pending, or none.

    `cancelled` counts as failing here — this line's own long-standing
    reading, separate from the shepherd's, which is why the forge hands back
    `cancelled` as its own verdict rather than pre-deciding for either.
    """
    if not checks:
        return "none"
    if any(c.verdict in ("failed", "cancelled") for c in checks):
        return "failing"
    return "pending" if any(c.verdict == "pending" for c in checks) else "passing"


def probe_prs(tasks: list) -> dict:
    """Open change requests in the repos this queue works in, matched to tasks.

    Matched by recorded artifact first, then by branch — so one a worker opened
    and has not reported yet still shows up, which is exactly the gap between
    "the worker should have opened a pull request" and the artifact itself.

    One list call per distinct repo, not one per task. Asked of the CHECKOUT
    and not of a repository id: this command has a path on disk and no identity
    for it, and a directory that is not a git repository at all still has to
    produce a sentence rather than an empty list that reads as "nothing is
    open". `scripts/lib/forge.py` decides which forge answers.
    """
    sec: dict = {"unavailable": None, "prs": [], "errors": []}
    # A remote task's `repo` is a path on its host, so asking a forge CLI here
    # would ask the wrong filesystem and report "no such directory" about a
    # checkout that exists. Skipped and SAID, rather than turned into an error that reads as
    # a broken record.
    live = [t for t in tasks if t.get("repo") and t.get("state") != "queued"]
    # `kind` is what the headline counts. `unread` is a repo this sweep tried
    # and failed to read — a hole in the finding. `skipped` is one it never
    # swept, which is a different sentence and must not read as a failure.
    remote = [
        {"repo": f"(on host {h})", "kind": "skipped",
         "reason": "runs on a remote host; its change requests are read by "
                   "`fleet queue shepherd`, which asks the forge and not a checkout"}
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
            sec["errors"].append({"repo": repo, "kind": "unread",
                                  "reason": "no such directory"})
            continue
        crs, why = forge.open_change_requests_in_checkout(repo)
        if why:
            reasons.append(why)
            sec["errors"].append({"repo": repo, "kind": "unread", "reason": why})
            continue
        for cr in crs:
            url, head = cr.url, cr.head_branch
            owner = next(
                (t for t in owners if t.get("artifact") and str(t["artifact"]).rstrip("/") == url.rstrip("/")),
                None,
            ) or next((t for t in owners if head and t.get("branch") == head), None)
            if owner is None:
                continue  # somebody else's change request in the same repo
            sec["prs"].append(
                {
                    "ref": owner["ref"],
                    "repo": repo,
                    "slug": cr.name,
                    "number": cr.number,
                    "url": url,
                    "title": cr.title,
                    "branch": head,
                    "checks": rollup(cr.checks),
                }
            )

    # Every repo failed the same way — no forge CLI, most likely — so that is
    # the section's story rather than a list of identical per-repo errors.
    if reasons and len(sec["errors"]) == len(repos) and len(set(reasons)) == 1:
        sec["unavailable"] = reasons[0]
        sec["errors"] = []
    # After the collapse, never inside it: a remote task is not a repo that
    # failed, so it must not count towards "every repo failed the same way".
    sec["errors"] += remote
    sec["prs"].sort(key=lambda p: p["ref"])
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

# The provider the SCREEN leads with, and the fallback when quota-axi names
# none. Not a gate: `probe_fuel_all()` reads every provider every ACCOUNT holds
# a credential for, and `scripts/lib/queue.py`'s `refuel` asks for its provider
# by name rather than inheriting this.
#
# NO NAME IS WRITTEN HERE, for the same reason `queue.py`'s `fuel_agent()`
# writes none: a literal would make one operator's vendor this repo's answer,
# and this file is tracked. The operator's own `orchestration/agent.conf` is
# where that answer already lives — `FUEL_PROVIDER` outright, else `AGENT`
# read as its own provider name, which is the identity `agent_providers()`
# ships. With neither set the screen has no preference and simply draws
# quota-axi's own order; `FLEET_FUEL_PROVIDER` overrides both for one run.
AGENT_CONF = agent_settings.AGENT_CONF
AGENT_CONF_DEFAULTS = agent_settings.AGENT_CONF_DEFAULTS


def agent_conf() -> dict:
    """`KEY=value` lines from the agent settings in force, read as data.

    The operator's copy when it exists, the tracked example beside it when it
    does not. Read through `agent_settings`, which is the one reader of that
    file: this module used to keep a parse of its own, and a second parse is
    how two answers about one setting come apart.
    """
    return agent_settings.conf()


def fuel_provider() -> str:
    """The provider this screen leads with, or "" for no preference.

    `FLEET_FUEL_PROVIDER` for one run, else the operator's `FUEL_PROVIDER`,
    else their `AGENT` read as its own provider name — the identity map
    `queue.py`'s `agent_providers()` ships. "" is a real answer and means the
    operator has named none; `probe_fuel()` then reads whichever provider holds
    a credential rather than this file naming a vendor.
    """
    pinned = os.environ.get("FLEET_FUEL_PROVIDER", "").strip()
    if pinned:
        return pinned
    conf = agent_conf()
    return conf.get("FUEL_PROVIDER", "").strip() or conf.get("AGENT", "").strip()


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


def epoch_of(instant) -> int | None:
    """An ISO instant as epoch seconds, or None when it is not one."""
    try:
        return int(datetime.fromisoformat(str(instant)).timestamp())
    except ValueError:
        return None


def fuel_windows(provider: dict) -> list:
    """The provider's windows that carry a number, SHORTEST WINDOW FIRST.

    A provider can have several that reset independently — a session window, a
    week, a per-model week — so there is no one reset to report and quota-axi
    deliberately does not invent one. A window with no `resetsAt` has not been
    triggered yet rather than being a gap, so it is kept and its reset is
    simply absent.

    THE ORDER IS THE WINDOW'S LENGTH, never which one binds. A reader that
    drew windows in binding order would swap its rows whenever two percentages
    crossed — the flip this order exists to prevent. The sort is stable, so
    windows of one length, and any whose length quota-axi did not say, keep
    its declaration order, the unmeasured ones last.

    `resets_epoch` is the same instant as `resets_at`, in epoch seconds, for
    the same reason `read_at` is: the pane has no `os` and cannot parse an
    instant, and a reset it cannot subtract is a reset it cannot count down.
    """
    out = []
    for w in provider.get("windows") or []:
        if not isinstance(w, dict):
            continue
        remaining = w.get("percentRemaining")
        if not isinstance(remaining, (int, float)) or isinstance(remaining, bool):
            continue
        seconds = w.get("windowSeconds")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            seconds = None
        out.append(
            {
                "id": str(w.get("id") or "?"),
                "label": w.get("label"),
                "remaining": remaining,
                "window_seconds": seconds,
                "resets_at": str(w["resetsAt"]) if w.get("resetsAt") else None,
                "resets_epoch": epoch_of(w["resetsAt"]) if w.get("resetsAt") else None,
            }
        )
    out.sort(key=lambda w: (w["window_seconds"] is None, w["window_seconds"] or 0))
    return out


# What `authenticated_providers()` says when it READ the credentials on disk
# and none of them is usable — as opposed to not being able to read them at
# all. One spelling, because a caller that has to tell the two apart compares
# against it, and two copies of a sentence are two answers waiting to drift.
NO_CREDENTIAL = "quota-axi auth named no provider with a credential"


def authenticated_providers(env: dict | None = None) -> tuple[list, str | None]:
    """The providers with a working credential, fleet's own first.

    WHY ASK AT ALL. `quota-axi --provider a,b,c` will happily go and ask a
    provider the operator has never signed in to, and pay a network round trip
    to be told what was already on disk. `quota-axi auth` IS that disk read:
    one entry per provider, each with its sources and a `status` per source.
    `available` is the only status that means a fetch can succeed. `expired` is
    not — and a provider can carry an expired source beside a working one, which
    is exactly the case `--no-credential-refresh` exists to keep from turning
    this read into a write.

    FLEET'S OWN PROVIDER LEADS and the rest follow in quota-axi's order, so the
    reading fleet's workers actually spend is the first one drawn. Which one
    that is comes from `fuel_provider()` — the operator's setting — and when
    they have named none there is no preference to apply and quota-axi's own
    order stands. When `auth` cannot be read at all, that named provider ALONE
    is the answer, because a status screen reporting no fuel because a
    discovery call failed is worse than one reporting the single reading the
    fleet runs on; with none named there is nothing to fall back to and the
    section says so rather than guessing a vendor.

    THE TWO WAYS THIS ANSWERS NOTHING ARE DIFFERENT FACTS, and `NO_CREDENTIAL`
    is how a caller tells them apart: `auth` READ and naming nothing available
    is a fact about that machine, while `auth` being unreadable is nobody being
    able to tell. The fallback above is the same for both because a screen with
    no reading is the worse outcome either way; `account_providers()` is the
    caller that must not treat them alike.
    """
    lead = fuel_provider()
    doc, why = run_json(["quota-axi", "auth", "--json"], timeout=15, env=env)
    if why:
        return ([lead] if lead else []), why
    names = []
    for entry in (doc or {}).get("auth") or []:
        if not isinstance(entry, dict):
            continue
        sources = entry.get("sources") or []
        if any(isinstance(s, dict) and s.get("status") == "available" for s in sources):
            name = str(entry.get("provider") or "").strip()
            if name:
                names.append(name)
    if not names:
        return ([lead] if lead else []), NO_CREDENTIAL
    # Stable, so the rest keep quota-axi's own order behind the one fleet runs.
    if lead:
        names.sort(key=lambda n: n != lead)
    return names, None


def fuel_read(providers: list, env: dict | None = None):
    """ONE quota-axi invocation, however many providers are being measured.

    The comma list is quota-axi's own way of asking for several at once, and it
    is the only way this may ask: the pane redraws on a timer and a reading
    that costs one process per provider is a reading that burns the fuel it
    reports.

    `--full` because `state.refreshedAt` — the age that makes a cached number
    honest — is demoted out of the default tier; the account identity `--full`
    also returns is never read and never printed. `--no-credential-refresh`
    because this command READS: a plain quota read may delegate an expired
    session's renewal to the vendor CLI that owns it, and that is a write.
    """
    return run_json(
        ["quota-axi", "--provider", ",".join(providers), "--full", "--json",
         "--no-credential-refresh"],
        timeout=20, env=env,
    )


def fuel_accounts() -> list[dict]:
    """Every ACCOUNT this fleet spends, as records, the checkout's own first.

    AN ACCOUNT IS A PROVIDER PLUS AN ENVIRONMENT, and this answers the
    environment half. quota-axi picks its credentials out of the environment it
    runs under, so the checkout's own environment holds one account per vendor
    — which is every account fleet had until an agent could carry an `ENV` line
    of its own.

    EACH RECORD IS `{account, provider, env, problem}`. `provider` is None for
    the checkout's own account and means "every provider that holds a
    credential here"; a named account carries the ONE provider its agent draws
    on, resolved by `fleet queue refuel`'s own `fuel_agent()` so the screen and
    the gate cannot name different vendors for one agent.

    WHY A NAMED ACCOUNT READS ONE PROVIDER AND NOT WHAT DISCOVERY FINDS. An
    `ENV` line moves ONE vendor's credential — `CLAUDE_CONFIG_DIR` does not
    move `~/.codex` — so every other vendor read under it is the SAME account
    as the checkout's, with the same window and the same number. Reading them
    all per environment drew one codex subscription as two accounts and paid a
    second network round trip for it on every redraw of the pane.

    WHICH AGENTS ARE ASKED IS `orchestration/agent.conf`, never the queue: the
    checkout-wide `AGENT`, then every agent the file says anything about, in
    the file's own order. The screen reports the accounts the fleet HAS, so one
    with nothing dispatched against it is still drawn, and `fleet check
    isolation` keeps the gate out of operator records.

    THE CHECKOUT'S OWN ACCOUNT READS ITS `ENV` THROUGH THE SAME CHAIN `refuel`
    does — `agent_settings.account_env(lead, settings)` — never a bare `{}`. A
    checkout-wide `ENV=` line, or a `<lead>.ENV=` line naming the checkout's
    own agent directly, moves the checkout's own default account exactly as it
    moves `task_agent()`'s default resolution; reading it as `{}` regardless
    left `fleet status` and `fleet queue refuel` naming two different accounts
    for a checkout that set only that one line.

    AN AGENT WITH NO `ENV` ADDS NOTHING: it runs on the checkout's own account,
    which is already first in the list. THE MEMBERSHIP CHECK IS `named()`, NEVER
    `value()` — `value()` falls back to the checkout-wide setting for ANY agent
    the file mentions, for ANY reason (a bare `LIMIT_BANNER` line is enough), so
    it would hand a checkout-wide `ENV=` line to every such agent as if each
    named its own account. `named()` answers only what the agent's OWN line or
    its `LIKE`'s says, which is exactly "does this agent name an account of its
    own" — an agent with nothing there truly adds nothing.

    `lead` ITSELF IS NEVER RE-ASKED IN THE LOOP — its account is entry zero,
    resolved above; asking again would add a second entry under the same name
    for the same environment, and the `(provider, env)` dedup below cannot save
    it because the two entries have no key in common (`provider=None` a "every
    provider" entry, `provider=<vendor>` a one-provider entry). BUT A NAME OTHER
    THAN `lead` CAN STILL REACH `lead`'s OWN ENVIRONMENT — a `LIKE` chain ending
    at the agent `AGENT=` already names, with no `ENV` of its own along the way,
    resolves through `named()`'s chain to that same `ENV` line. So every named
    agent's resolved `env` is checked against `checkout_env` directly, ahead of
    the `(provider, env)` dedup: equal means this is the checkout's own account
    under an alias, not a second one, whatever name or provider it carries.
    Two OTHER agents whose `ENV` and provider resolve alike are ONE account too
    — the `(provider, env)` dedup below governs any two names other than a
    `checkout_env` match.

    EVERY NON-CHECKOUT ENTRY CARRIES `checkout: False`, entry zero alone
    `checkout: True` — not "the account named `lead`" and not "the account
    whose provider matches its own name", either of which a NAMED account can
    coincidentally satisfy (a bare agent named the same as its own vendor, or
    the same as `lead` before this function stopped revisiting `lead`). The
    renderers key the "is this the checkout's own reading" question off this
    flag, never off comparing strings.

    AN `ENV` LINE THAT PARSES TO NOTHING IS REPORTED, never dropped. Writing
    `ENV=~/.spare` instead of `ENV=VAR=~/.spare` would otherwise collapse that
    account into the checkout's own and put the screen back to reading a window
    nobody spends — silently, which is the exact failure this whole section
    exists to end.
    """
    settings = agent_conf()
    lead = settings.get("AGENT", "").strip()
    checkout_env = agent_settings.account_env(lead, settings)
    out: list[dict] = [
        {"account": lead, "provider": None, "env": checkout_env, "problem": None, "checkout": True}
    ]
    seen: set = set()
    for agent in agent_settings.agents(settings):
        # `named()`, NEVER `value()`: an agent with no `ENV` of its own or its
        # `LIKE`'s is not a second account just because the CHECKOUT-WIDE
        # fallback happens to answer something — `value()` would apply that
        # fallback to every agent `agent.conf` mentions for ANY reason (a
        # `LIMIT_BANNER` line is enough), turning a checkout-wide `ENV=` line
        # into a phantom account per unrelated agent name, each an extra
        # `quota-axi auth` call and, once a no-credential account renders
        # visibly, a false failure on the screen for an account that was
        # never distinct from the checkout's own.
        if agent == lead or not agent_settings.named("ENV", agent, settings):
            continue
        provider = fleetqueue.fuel_agent(agent) or agent
        env = agent_settings.account_env(agent, settings)
        if not env:
            out.append({"account": agent, "provider": provider, "env": {}, "checkout": False, "problem": (
                f"{agent}.ENV names no NAME=VALUE pair, so fleet cannot tell this "
                "account from the checkout's own")})
            continue
        # An agent named only through `agent == lead` above misses an agent
        # that reaches the SAME environment a different way — a `LIKE` chain
        # ending at `lead`'s own `ENV` line, most directly. Two agents whose
        # resolved environment is byte-identical to the checkout's own are
        # not a second account; `refuel`'s `account_key()` has no notion of
        # "the lead" at all and buckets by `(provider, env)` alone, so this
        # is the same equality it applies.
        if env == checkout_env:
            continue
        key = (provider, tuple(sorted(env.items())))
        if key in seen:
            continue
        seen.add(key)
        out.append({"account": agent, "provider": provider, "env": env, "problem": None, "checkout": False})
    return out


def account_providers(provider: str, discovered: list, why: str | None) -> list:
    """What a NAMED account reads: its own provider, or nothing at all.

    `quota-axi auth` NAMING NOTHING under this account is a fact about this
    account and is obeyed — the provider is not probed, for the reason
    `authenticated_providers()` gives: that round trip only ever ends in what
    `auth` already said. `auth` being UNREADABLE is not that fact; nobody could
    tell, and reading the account's own provider is better than reporting no
    fuel for it.

    NEVER THE CHECKOUT'S LEAD in either case. That is the fallback
    `authenticated_providers()` applies, and it is the operator's
    checkout-wide setting — applied here it produced a second reading of the
    checkout's own vendor wearing this account's name, with numbers that were
    not this account's.
    """
    if why == NO_CREDENTIAL:
        return []
    if why:
        return [provider]
    return [provider] if provider in discovered else []


def fuel_blank(provider: str, read_at: int, account: str = "") -> dict:
    """One provider's record with nothing read into it yet."""
    return {
        # EPOCH SECONDS, not the ISO instant the rest of this document speaks
        # in, because the reading is CACHED by its readers and an age is what
        # a cached number has to be drawn with. The TUI pane is the one that
        # cannot do the arithmetic itself: a thurbox pane has no `os`, so an
        # instant it cannot subtract is an instant it cannot age.
        "read_at": read_at,
        "unavailable": None, "source": "quota-axi", "provider": provider,
        # WHICH ACCOUNT THIS IS, as the agent that names it in agent.conf — the
        # checkout-wide `AGENT` for its own environment, "" when even that is
        # unset. The only account a fleet with one has.
        "account": account,
        "remaining": None, "reserve": FUEL_RESERVE, "below_reserve": None,
        "binding": None, "resets_at": None, "windows": [], "stale": None,
        "state": None, "refreshed_at": None, "retry_after": None, "error": None,
        "schema_version": None,
    }


def fuel_record(doc, provider: str, read_at: int, account: str = "") -> dict:
    """One provider's reading, out of the document `fuel_read` answered.

    IT READS `windows[]`, NOT THE HEADROOM SUMMARY. On a rate-limited fetch
    quota-axi answers with an empty `quota[]` and an `attention[]` row per
    scope — `headroom_unknown` — while the per-window percentages survive from
    its cache. Reading `effectiveAvailability[].effectivePercentRemaining`
    would report that live case as no reading at all; reading the windows
    reports a stale number and says it is stale, which is the true statement.
    The binding window is whichever has least remaining, and every window is
    kept so the reader can see the rest.

    IT REPORTS WHAT WAS MEASURED, NEVER WHAT WAS PROJECTED. quota-axi also
    publishes `pace`, `burnMultiple`, `runway`, `usableRunwaySeconds` and
    `projectedExhaustedAt`; those are forecasts, and FLEET.md forbids fleet
    from carrying one. `percentRemaining` and `resetsAt` are measurements — a
    spent window is spent, and when it comes back is the fact that can be acted
    on.

    A PROVIDER THAT COULD NOT BE READ CARRIES ITS OWN `unavailable` and no
    `remaining` at all. One provider failing is not the others failing, and a
    zero here would read as a spent window rather than an unread one.

    Written against `schemaVersion` 5 and parsed defensively rather than
    pinned — a field this cannot find costs the record its reading and names
    itself, which is the same bargain every other probe makes.
    """
    sec = fuel_blank(provider, read_at, account)
    if not isinstance(doc, dict):
        sec["unavailable"] = "quota-axi did not answer a report"
        return sec
    sec["schema_version"] = doc.get("schemaVersion")
    found = next(
        (p for p in doc.get("providers") or []
         if isinstance(p, dict) and p.get("provider") == provider),
        None,
    )
    if found is None:
        sec["unavailable"] = f"quota-axi reported no {provider} provider"
        return sec

    state = found.get("state") if isinstance(found.get("state"), dict) else {}
    sec["state"], sec["stale"] = state.get("status"), bool(state.get("stale"))
    sec["error"] = state.get("error")
    sec["refreshed_at"] = state.get("refreshedAt")
    # `retryAfter` is documented as a state rather than a shape, so it is
    # printed only when it is already a sentence. When it is not, the same
    # instant is in `state.error`, which always is.
    if isinstance(state.get("retryAfter"), str):
        sec["retry_after"] = state["retryAfter"]

    windows = fuel_windows(found)
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
    sec["resets_at"] = binding["resets_at"]
    return sec


def probe_fuel(provider: str | None = None, env: dict | None = None) -> dict:
    """ONE provider's remaining windows, per quota-axi. Fleet's own by default.

    THE ONLY SOURCE. `thurbox-cli session get --json` carries no token, usage,
    cost or limit field, so nothing thurbox knows can answer this. quota-axi
    (MIT, github.com/kunchenguid/quota-axi) reads the first-party endpoints and
    normalises them. It is a tool on the operator's PATH and never a bundled
    dependency: absent, it costs this section and says so.

    IT MEASURES THE ACCOUNT, NOT A SESSION. These are the subscription windows
    every session on that account spends at once, so this is ONE reading per
    account and can never say what a given worker burned. Six workers
    dispatched together spend one window set six ways.

    WHICH ACCOUNT IS `env`, AND IT IS A SECOND AXIS. A provider is a vendor;
    an account is a credential, and quota-axi picks one out of the environment
    it runs under. So two workers on one provider and two accounts are two
    calls here under two environments, and `scripts/lib/queue.py`'s `refuel`
    judges each worker against the reading its OWN account produced. Without
    it, the gate read whichever account the LEAD happened to be signed in to
    and every worker on another one was reported `undetermined` — honest, and
    no autopilot at all. The environment comes from that agent's `ENV` record
    (`scripts/lib/agent_settings.py`); None means this process's, which is the
    single-account case and what the screen always does.

    THIS IS THE GATE'S ENTRY POINT, and that is why it takes one provider.
    `scripts/lib/queue.py`'s `account_fuel()` calls it to decide whether
    `fleet queue refuel` restarts anything, and it passes the provider IT derived
    from the agent the tasks in hand are running. The gate must read that one
    window and never an average or whichever provider happens to be lowest: a
    spent window on a provider the fleet never dispatches is no reason to leave
    a worker sitting at its limit, and reading the wrong window is worse than
    reading none. The SCREEN reads every ACCOUNT instead, through
    `probe_fuel_all()`. With no argument this falls back to
    `fuel_provider()`, which is the operator's setting and not a name this file
    chose.
    """
    read_at = int(time.time())
    provider = provider or fuel_provider()
    if not provider:
        # The operator has named none, so the one to read is whichever they are
        # actually signed in to. Asked rather than guessed: a vendor written
        # here would be this repo answering a question that is theirs.
        names, why = authenticated_providers(env=env)
        provider = names[0] if names else ""
        if not provider:
            sec = fuel_blank(provider, read_at)
            sec["unavailable"] = why or "no provider has a credential to read"
            return sec
    doc, why = fuel_read([provider], env=env)
    if why:
        sec = fuel_blank(provider, read_at)
        sec["unavailable"] = why
        return sec
    return fuel_record(doc, provider, read_at)


def probe_fuel_all() -> dict:
    """Every ACCOUNT's every authenticated provider, one quota-axi call each.

    ONE READING PER ACCOUNT, where an account is a provider PLUS the
    environment that selects the credential. It used to be one reading per
    provider under whatever account this command happened to run as, which on a
    fleet whose workers draw on a second login reported a window nobody was
    spending: measured on 2026-09-18, the screen said 6% remaining while the
    account the fleet actually dispatches on had 70%. The lead read the screen
    and dispatched nothing. `fleet queue refuel` already read the account —
    same records, same seam — so the screen was the half that had not caught
    up.

    WHICH ACCOUNTS is `fuel_accounts()`, off `orchestration/agent.conf`, and
    WHICH PROVIDERS is `authenticated_providers()` asked once per environment —
    a credential on disk under one account is not one under another, so the
    disk read belongs to the account and never to the process.

    THE COST IS ONE FETCH PER ACCOUNT, never one per provider. Each account's
    providers go in one `--provider a,b,c` invocation, so a second subscription
    on an account fleet already reads costs nothing and a second ACCOUNT costs
    exactly one more `auth`-and-fetch PAIR — the environment differs, so there
    is no comma list that would ask for both and no `auth` call answers for two
    environments at once.

    A NAMED ACCOUNT WITH NO CREDENTIAL STAYS ON SCREEN, as an `unavailable`
    record rather than a silent absence — the whole point of naming an account
    is that its silence is now visible instead of collapsing into "the fleet
    has one account", which is the failure this section exists to end. No
    `fuel_read` is spent finding that out: `account_providers()` already
    decided nothing is probed, so this is `auth`'s own answer restated, never a
    second round trip.

    `discovery_fallback` ON A RECORD MARKS A READING TAKEN ON A GUESS — this
    account's own `auth` could not be read, so its provider came from the
    operator's checkout-wide setting rather than from what this account is
    actually signed in to. `render_fuel`'s "not discovered" line names only
    these records, never an account whose own `auth` succeeded: attributing
    every reading in the section to one account's failed discovery blamed
    readings the failure never touched.

    `unavailable` here is the WHOLE reading failing, which is now every account
    failing: one account's fetch failing leaves that account's providers
    carrying their own reason and the other accounts' readings intact, exactly
    as one provider's failure always did. Every account contributes to either
    `records` or `failures` (a named account with nothing to probe still adds
    one placeholder to both), so `len(failures) == len(accounts)` below is a
    true "every account failed" and not an undercount from an account skipped
    without a trace.
    """
    read_at = int(time.time())
    accounts = fuel_accounts()
    sec: dict = {
        "read_at": read_at, "unavailable": None, "source": "quota-axi",
        "reserve": FUEL_RESERVE, "discovery": None, "schema_version": None,
        "providers": [],
    }
    records, failures = [], []
    for acc in accounts:
        account, provider, env, problem, checkout = (
            acc["account"], acc["provider"], acc["env"], acc["problem"], acc["checkout"])
        if problem:
            failures.append(problem)
            records.append(fuel_blank(provider, read_at, account)
                            | {"unavailable": problem, "checkout": checkout})
            continue
        # The account's environment laid over this process's, or None for the
        # checkout's own — which is what `run_json` inherits, and what every
        # fleet with one account has always passed.
        child = {**os.environ, **env} if env else None
        discovered, why = authenticated_providers(env=child)
        if why and sec["discovery"] is None:
            sec["discovery"] = why
        # The checkout's own account (provider is None) reads every provider
        # discovery found; a named account reads its own provider alone, per
        # `account_providers()` — never what discovery found for the others.
        # `NO_CREDENTIAL` GETS NO FALLBACK EITHER WAY: `auth` was read and
        # named nothing available, which is obeyed for the checkout's own
        # account exactly as `account_providers()` already obeys it for a
        # named one — the difference is only which provider that leaves
        # nothing to read for. Reading the fallback name anyway spent a fetch
        # `auth` had already answered for, and the empty `providers[]` a real
        # vendor would answer for a missing credential came back indistinguishable
        # from one this repo never asked about.
        names = ([] if why == NO_CREDENTIAL else discovered) if provider is None \
            else account_providers(provider, discovered, why)
        if not names:
            # NO `provider is not None` GUARD HERE: the checkout's own account
            # naming zero providers is exactly as much "nothing to read" as a
            # named account naming zero, and skipping the guard for it used to
            # call `fuel_read([])` anyway — an extra quota-axi process asked
            # for no provider at all — and then add this account to NEITHER
            # `records` NOR `failures`. With another account in the list that
            # DID have a reading, that silent drop is not a blank line — it is
            # the survivor's reading rendered as if the checkout were the only
            # account, unlabelled: `fuel_named()` counts one distinct account
            # in `records` and drops the very field that would have told them
            # apart. Still ON SCREEN, though: a record that names the reason
            # rather than an account that simply never appears.
            #
            # `why == NO_CREDENTIAL` GETS THE FRIENDLY DEFAULT, NOT THE RAW
            # SENTENCE: `authenticated_providers()`'s own wording is written for
            # a caller distinguishing "read and empty" from "unreadable", which
            # this reason line does not need to repeat — a real discovery
            # failure (`why` truthy and not that constant) is still surfaced
            # verbatim, because that one names what actually went wrong.
            reason = (why if why and why != NO_CREDENTIAL else
                      (f"quota-axi named no {provider} credential for this account"
                       if provider is not None else "no provider has a credential to read"))
            failures.append(reason)
            records.append(fuel_blank(provider or "", read_at, account)
                            | {"unavailable": reason, "checkout": checkout})
            continue
        doc, fetch_why = fuel_read(names, env=child)
        if fetch_why:
            failures.append(fetch_why)
            records += [fuel_blank(name, read_at, account)
                        | {"unavailable": fetch_why, "checkout": checkout, "discovery_fallback": why}
                        for name in names]
            continue
        if isinstance(doc, dict) and sec["schema_version"] is None:
            sec["schema_version"] = doc.get("schemaVersion")
        records += [fuel_record(doc, name, read_at, account) | {"checkout": checkout, "discovery_fallback": why}
                    for name in names]
    if failures and len(failures) == len(accounts):
        sec["unavailable"] = failures[0]
        return sec
    sec["providers"] = records
    return sec


def fuel_named(sec: dict) -> bool:
    """Does this reading hold more than one account, so the identity is worth drawing?

    The account is what tells two readings of one provider apart, and with one
    account there is nothing to tell apart: a fleet that never named a second
    one draws exactly what it always drew, down to the `--fuel` record the TUI
    pane parses. The JSON carries the field either way, since a reader that
    parses fields is not one a new field confuses.
    """
    return len({rec.get("account", "") for rec in sec.get("providers") or []}) > 1


# --- records -----------------------------------------------------------------
#
# THE OPERATOR'S HEALTH CHECK. The live queue records and the registry map are
# validated here, and nowhere in `fleet check`: the gate reads no operator
# state, because a gate that did gave one commit a different verdict in the
# control-plane checkout than on CI. A problem in this section is "your records
# need attention", never "this commit is broken". Both validators are the ones
# the rest of the repo uses — `queue.py`'s record_problems(), which
# `fleet queue check` prints, and `check_yaml.py`'s registry_problems().
#
# ON REQUEST, NEVER ON THE SCREEN. Validating a record means opening it, and an
# archived topic's task files are exactly the ones the screen promises never to
# open — a finished topic costs one read of its topic.yaml. So this is
# `--records`, and the screen stays the cheap reading.

# Relocates the registry map, as FLEET_QUEUE_DIR relocates the queue.
REGISTRY_ENV = "FLEET_REGISTRY_FILE"
REGISTRY_FILE = os.path.join("registry", "repos.generated.yaml")
RECORD_PROBLEMS_SHOWN = 8


def _load_check_yaml():
    if "fleet_check_yaml" in sys.modules:
        return sys.modules["fleet_check_yaml"]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_yaml.py")
    spec = importlib.util.spec_from_file_location("fleet_check_yaml", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fleet_check_yaml"] = module
    spec.loader.exec_module(module)
    return module


def probe_records() -> dict:
    sec: dict = {"unavailable": None, "queue": None, "registry": None}

    root = os.path.abspath(fleetqueue.queue_root())
    row = {"path": root, "summary": "", "problems": []}
    if not os.path.isdir(root):
        row["summary"] = "not created yet"
    else:
        try:
            q, row["problems"] = fleetqueue.record_problems(root)
            row["summary"] = f"{len(q.topics)} topic(s), {len(q.tasks)} task(s)"
        except Exception as exc:  # a record too broken to load is a problem, not a crash
            row["problems"] = [f"{root}: {exc}"]
    sec["queue"] = row

    path = os.environ.get(REGISTRY_ENV) or os.path.join(REPO_ROOT, REGISTRY_FILE)
    row = {"path": path, "summary": "", "problems": []}
    if not os.path.exists(path):
        row["summary"] = "not synced yet — `uv run fleet sync-registry`"
    else:
        try:
            row["summary"], row["problems"] = _load_check_yaml().registry_problems(path)
        except Exception as exc:
            row["problems"] = [f"{path}: {exc}"]
    sec["registry"] = row
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
        if sec.get("archived"):
            return lines + [cont(f"{sec['archived']} archived topic(s) and nothing "
                                 "live — `fleet queue list --archived`")]
        return lines + [cont("empty — `fleet queue topic add` opens one")]
    order = ("ready", "waiting", "dispatched", "done", "landed", "stuck", "failed",
             "abandoned")
    tally = [f"{k} {sec['counts'][k]}" for k in order if sec["counts"].get(k)]
    lines.append(cont(f"{len(topics)} topic(s), {tasks} task(s) — " + ", ".join(tally)))
    if sec.get("archived"):
        lines.append(cont(f"{sec['archived']} archived topic(s) hidden — "
                          "`fleet queue list --archived`"))
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
            for note in t["notes"]:
                lines.append(f"        {note}")
    for risk in sec["risks"]:
        lines.append(f"  risk: {', '.join(risk['tasks'])} all touch {risk['touches']}")
    return lines


def render_records(sec: dict) -> list:
    lines = [head("RECORDS", "your live queue and registry map, validated")]
    for name, remedy in (("queue", "`uv run fleet queue check` lists every one"),
                         ("registry", "`uv run fleet sync-registry` regenerates it")):
        row = sec[name]
        problems = row["problems"]
        if not problems:
            lines.append(cont(f"{name}: ok — {row['summary']}"))
            continue
        lines.append(cont(f"{name}: {len(problems)} problem(s) in {row['path']}"))
        lines += [cont(f"  {p}") for p in problems[:RECORD_PROBLEMS_SHOWN]]
        hidden = len(problems) - RECORD_PROBLEMS_SHOWN
        lines.append(cont(f"  {f'and {hidden} more; ' if hidden > 0 else ''}{remedy}"))
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
    """The finding, and how much of the sweep it rests on — on the SAME line.

    "none open for these tasks" and "gh exited 1: no git remotes found" once
    printed on adjacent lines, and a reader takes the headline: "none open" is
    a finding, "one of the repos could not be read" means there is no finding
    yet. So a hole in the sweep is part of the headline, and "none open" is
    unreachable whenever anything went unread or unswept.
    """
    if sec["unavailable"]:
        return [head("PRS", f"unavailable — {sec['unavailable']}")]
    prs = sec["prs"]
    unread = [e for e in sec["errors"] if e.get("kind") == "unread"]
    skipped = [e for e in sec["errors"] if e.get("kind") != "unread"]
    if prs:
        line = f"{len(prs)} open, matched to this queue's tasks"
    elif unread or skipped:
        line = "none open in the repos that were read"
    else:
        line = "none open for these tasks"
    # INCOMPLETE is for a hole: a repo this tried to read and could not. A
    # remote host was never in the sweep, which is expected and stated without
    # the alarm word — but it still keeps "none open" off the line.
    gaps = [f"INCOMPLETE: {len(unread)} repo(s) unread"] if unread else []
    if skipped:
        gaps.append(f"{len(skipped)} not swept (remote)")
    if gaps:
        line += " — " + ", ".join(gaps)
    lines = [head("PRS", line)]
    for p in prs:
        lines.append(f"    {p['slug']:<24} {p['checks']:<8} {p['branch'] or '':<32} {p['ref']}")
    for e in sec["errors"]:
        lines.append(f"    {e['repo']}: {e['reason']}")
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


def fuel_label(rec: dict, named: bool) -> str:
    """How one reading names itself: the provider, and whose account when there
    is more than one. `refuel`'s account lines are phrased the same way, so the
    two commands name one account one way.

    THE CHECKOUT'S OWN READING IS `checkout: True` ON THE RECORD, never
    inferred by comparing `account` against `provider` — a named account whose
    agent happens to be called the same as its own vendor (or, before
    `fuel_accounts()` stopped revisiting `lead`, the same as the checkout's own
    agent) satisfies that comparison too, which suppressed its label exactly
    when a second reading of one vendor most needed one.
    """
    provider = str(rec.get("provider") or "?")
    if not named or rec.get("checkout", True):
        return provider
    account = str(rec.get("account") or "")
    return f"{provider} ({account})" if account else provider


def fuel_provider_lines(rec: dict, named: bool = False) -> list:
    """One reading's block: the reading, then every window behind it."""
    label = fuel_label(rec, named)
    if rec["unavailable"]:
        return [f"  {label}  unavailable — {rec['unavailable']}"]
    line = f"{rec['remaining']}% remaining   reserve {rec['reserve']}%"
    if rec["binding"]:
        line += f"   binding {rec['binding']}"
    lines = [f"  {label}  {line}"]
    for w in rec["windows"]:
        resets = f"resets {w['resets_at']}" if w["resets_at"] else "not triggered yet"
        binds = "  binds" if w["id"] == rec["binding"] else ""
        lines.append(f"      {w['id']:<16}{w['remaining']:>4}%  {resets}{binds}")
    # A cached number is a fact with an age, and the age is part of the fact.
    if rec["stale"] or (rec["state"] and rec["state"] != "fresh"):
        detail = [str(rec["state"] or "stale")]
        if rec["refreshed_at"]:
            detail.append(f"last refreshed {rec['refreshed_at']}")
        if rec["error"]:
            detail.append(str(rec["error"]))
        if rec["retry_after"]:
            detail.append(f"retry after {rec['retry_after']}")
        lines.append("      " + "; ".join(detail))
    if rec["below_reserve"]:
        lines.append(f"      ! under the {rec['reserve']}% reserve — dispatch, "
                     "do not investigate (FLEET.md `## Fuel`)")
    return lines


def render_fuel(sec: dict) -> list:
    """The FUEL section: one block per ACCOUNT-and-provider the fleet spends.

    ONE READING PER ACCOUNT, never one summed or averaged across them. Each
    provider's windows reset on their own clock and are spent by whatever
    reaches for that provider under that credential, so a single number over
    three subscriptions — or over two logins of one subscription — would be a
    number nobody could act on.
    """
    if sec["unavailable"]:
        return [head("FUEL", f"unavailable — {sec['unavailable']}")]
    if not sec["providers"]:
        return [head("FUEL", "unavailable — no provider has a credential to read")]
    named = fuel_named(sec)
    count = len(sec["providers"])
    if named:
        accounts = len({rec.get("account", "") for rec in sec["providers"]})
        summary = f"{count} reading(s) over {accounts} account(s)"
    else:
        summary = f"{count} provider(s)"
    lines = [head("FUEL", f"{summary} — account windows, every session spends them at once")]
    if sec.get("discovery"):
        # Only the records taken on a GUESS — this account's own `auth` failed
        # and its provider came from the checkout-wide setting rather than
        # from what it is actually signed in to. An account whose own
        # discovery succeeded is not "read alone" just because another
        # account's did not.
        guessed = [r for r in sec["providers"] if r.get("discovery_fallback")]
        if guessed:
            read = ", ".join(fuel_label(r, named) for r in guessed)
            lines.append(cont(f"providers not discovered ({sec['discovery']}) — "
                              f"read {read} alone"))
    for rec in sec["providers"]:
        lines += fuel_provider_lines(rec, named)
    return lines


RECORD_FIELDS = (
    "read_at", "unavailable", "reserve", "remaining", "below_reserve",
    "limited_by", "resets_at", "stale", "state", "provider", "account",
    "checkout", "scope",
)


def render_fuel_record(sec: dict) -> str:
    """The same reading, as `name<TAB>value` lines — ONE RECORD PER READING.

    FOR A READER WITH NO JSON. `interface/fleet_queue.lua` draws this reading
    in the TUI column, and a thurbox pane is Lua with no JSON parser and no
    `os` — so the one format it can afford is lines and tabs, which is already
    how its queue probe answers. A tab because none of these values carries
    one.

    RECORDS ARE SEPARATED BY A BLANK LINE, and each names itself with its own
    `provider` field. That is the whole extension for several subscriptions: a
    reader that splits on blank lines and then on tabs is the same kind of
    reader the single record needed, where a provider-qualified key would have
    made every field name dynamic. A reading nobody could take at all — no
    quota-axi, no credential anywhere — is ONE record carrying `unavailable`
    and no provider, which is exactly what a single-provider reading that
    failed used to look like.

    `account` AND `checkout` ARE DRAWN ONLY WHEN THERE IS MORE THAN ONE, for
    the reason `fuel_named()` argues: two records of one provider under two
    logins are told apart by nothing else, and a fleet with a single account
    would otherwise gain fields it has no second reading to distinguish from.
    So a checkout whose `agent.conf` names no second account emits exactly the
    record it always did. `checkout` is `1` for the checkout's own reading and
    `0` for a named account — `interface/fleet_queue.lua` reads it to decide
    whether to draw the account in parentheses, the same flag `fuel_label()`
    reads here, so neither has to compare `account` against `provider` to
    guess which reading is the checkout's own.

    IT IS NOT A SECOND READING. Every field here is `probe_fuel_all()`'s own,
    under its own name, so the record and the FUEL section on the screen cannot
    come to different conclusions about what quota-axi said. Nothing is
    computed here and nothing is phrased here; `render_fuel` stays the only
    renderer that puts this into words.

    A FIELD WITH NO VALUE IS ABSENT, never empty and never zero. An unreadable
    reading carries `unavailable` and no `remaining` at all, because a
    `remaining` line reading 0 is the one way this could say "the window is
    spent" when it means "nobody could tell".
    """
    # `limited_by` is the wire name a reader with no JSON parses; `probe_fuel`
    # itself calls the same fact `binding`, since the fuel record is the only
    # place that has to speak the pane's vocabulary.
    source = {"limited_by": "binding"}
    records = sec.get("providers") or [{
        "read_at": sec.get("read_at"),
        "reserve": sec.get("reserve"),
        "unavailable": sec.get("unavailable") or "no provider has a credential to read",
    }]
    drop = set() if fuel_named(sec) else {"account", "checkout"}
    blocks = []
    for rec in records:
        lines = []
        for name in RECORD_FIELDS:
            if name in drop:
                continue
            value = rec.get(source.get(name, name))
            if value is None or value == "":
                continue
            if isinstance(value, bool):
                value = "1" if value else "0"
            elif isinstance(value, list):
                value = ",".join(str(v) for v in value)
            lines.append(f"{name}\t{value}")
        # EVERY WINDOW, after the fields: `window<TAB>id<TAB>percent<TAB>reset
        # epoch<TAB>label`, one line each, in `fuel_windows`' order. A reader
        # that knows only `name<TAB>value` still parses the record — it sees a
        # field it does not draw — and an absent reset or label is an empty
        # column, so the line keeps its shape.
        for w in rec.get("windows") or []:
            cols = (w["id"], w["remaining"], w.get("resets_epoch"), w.get("label"))
            lines.append("\t".join(
                ["window"] + [" ".join(str("" if c is None else c).split()) for c in cols]
            ))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def render(doc: dict) -> str:
    blocks = [
        render_fuel(doc["fuel"]),
        render_queue(doc["queue"]),
        render_sessions(doc["sessions"]),
        render_prs(doc["prs"]),
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
        "fuel": probe_fuel_all(),
        "queue": queue,
        "sessions": probe_sessions(tasks),
        "prs": probe_prs(tasks),
        "checkout": probe_checkout(),
    }


def main(argv: list) -> int:
    p = argparse.ArgumentParser(prog="fleet status", add_help=True)
    p.add_argument("--json", action="store_true", help="the same reading, machine-readable")
    p.add_argument(
        "--fuel", action="store_true",
        help="only the fuel reading, as one blank-line-separated "
             "name<TAB>value record per provider",
    )
    p.add_argument(
        "--records", action="store_true",
        help="validate your live queue records and registry map, and print only that",
    )
    args = p.parse_args(argv)

    if args.records:
        sec = probe_records()
        if args.json:
            print(json.dumps(sec, indent=2))
        else:
            sys.stdout.write("\n".join(render_records(sec)) + "\n")
        return 0

    # THE FUEL SECTION ALONE, AND AT ITS OWN COST. `--json` collects
    # everything, which is a `gh pr list` per repo in flight and a
    # `thurbox-cli session list` — a bill a reader that only wants the fuel
    # number should not pay, and one the TUI pane could not pay at all.
    if args.fuel:
        sec = probe_fuel_all()
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
