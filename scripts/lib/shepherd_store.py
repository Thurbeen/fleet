"""Shepherd's second half: sessions fleet did not spawn, and one file of every watched PR's state.

Loaded by `fleet queue shepherd` (`cmd_shepherd` in queue.py) at the end of
every pass, and by `fleet queue prs`, which prints what the last pass wrote.
Nothing else writes the store, and nothing here writes a queue record.

ADOPTION. An operator opens thurbox sessions by hand, and every one of them can
open a pull request that goes CONFLICTING or red with nothing watching it. Each
pass lists the sessions (`thurbox-cli session list --json`) and adopts every one
that is:

  not the lead      refused by name (the rendered extension.toml, or
                    FLEET_LEAD_SESSION), and by id when THURBOX_SESSION says
                    this process runs inside it — the same name `notify_lead`
                    and `reap` refuse;
  not fleet's own   held by no queue task, as its worker or as a fixer sent
                    for it (`sessions.py`'s `orphans` reads "held" the same way);
  local             a session on a `hosts.toml` host keeps its worktree on that
                    host, where this pass cannot ask git which branch it is on.
                    Local only, deliberately; a remote one is skipped;
  on an open PR     one of its worktrees is on a branch with an OPEN change
                    request on a forge `forge.py` reaches (GitHub, GitLab).
                    The branch is the worktree's CURRENT one, read from git:
                    thurbox records the branch a session was created on, and
                    an agent that switched branches opened its PR from the new
                    one.

There is no startup step. The reconciler's first shepherd pass after fleet
starts is the one that picks them up.

WHAT AN ADOPTED SESSION GETS: A MESSAGE, AND NOTHING ELSE. Its change request is
classified by queue.py's own `classify`, as a `pr`-method one (nobody asked an
outside session for an attestation, so its absence is not a fault). A condition
in `FIXABLE` — read off queue.py at call time, so a condition added there
reaches adopted sessions too — gets a short brief written under
`<store dir>/briefs/` and ONE line sent into the session pointing at it:

  - only when the session is at rest by its own hook word (`SESSION_AT_REST`).
    `working` and `blocked` are busy; `running`, `uncovered` and `unreported`
    are observations, not the agent speaking (thurbox-session §4a). Either
    way nothing is sent, and the next pass asks again;
  - once per condition per PR head. The key lives in the store's `sent` list,
    because no task exists to record it on. A push moves the head and resets it;
  - never a fixer session, and never a merge. Adoption adds watching and
    messaging. A PR on a repository the queue already watches is still merged
    by the main pass if its gates and `orchestration/auto-merge.conf` allow it,
    exactly as before, and the store then says `merged`.

THE STORE. `<store dir>/prs.json`, where the store dir is FLEET_SHEPHERD_DIR or
`orchestration/shepherd/` in this checkout (gitignored, as
`orchestration/reconcile/` is). Rewritten whole, atomically, by every shepherd
pass that is not a `--dry-run`, through `fleet_platform.write_record` (UTF-8,
LF). The board's coloured PR dots read ONLY this file, through `fleet queue
prs --json`, so THIS IS A CONTRACT: add fields, never rename or retype them.

    {
      "schema": 1,
      "written_at": "<ISO-8601 UTC>",       # when this pass wrote the file
      "sessions": {
        "<thurbox session id>": {
          "session": "<thurbox session id>",
          "name": "<session name, or the task title when thurbox did not list it>",
          "task": "<topic/NN-slug>" | null,   # null for an adopted session
          "adopted": true | false,            # true: fleet did not spawn it
          "prs": [
            {
              "repo": "github.com/owner/repo",  # host-qualified
              "number": 42,
              "url": "https://github.com/owner/repo/pull/42",
              "title": "...",
              "head_branch": "feat/x",
              "head_sha": "<sha>" | "",
              "state": "open" | "draft" | "merged" | "closed",
              "checks": "pending" | "passing" | "failing" | "none",
              "failing_checks": ["job name", ...],
              "review_decision": "changes-requested" | "approved" | null,
              "unresolved_threads": 3 | null,   # null: the forge did not say
              "mergeable": "mergeable" | "conflicting" | null,  # null: not computed yet
              "condition": "<classify's word: conflicting, checks-failed,
                            changes-requested, policy, ready, undetermined,
                            foreign, closed, ...>",
              "detail": "<classify's one line>",
              "action": "sent" | "left-alone" | "fixer" | "merged" | "none",
              "action_note": "<why, in a sentence>",
              "observed_at": "<ISO-8601 UTC>",  # when the forge last answered for it
              "stale": false,
              "stale_reason": "",               # set when stale is true
              "sent": [                         # adopted sessions only
                {"condition": "checks-failed", "head": "<sha>",
                 "at": "<ISO-8601 UTC>", "brief": "<absolute path>"}
              ]
            }
          ]
        }
      }
    }

`action` is what shepherd did about that PR: `sent` a line to the session (an
adopted session, or a task's own worker reused for a fix), dispatched a `fixer`
session (tasks only), `merged` it, `left-alone` (busy, foreign, or nothing it
may do), or `none` (nothing called for). A `merged` or `closed` PR stays under
its session for as long as the session is still on that branch, so the board
can draw how it ended.

STALE, NEVER DROPPED. A forge, thurbox or network that cannot be read leaves
the previous entry exactly as it was, with `stale: true` and the reason, so a
dot keeps its last colour and says it is old. An entry is removed only when its
source PROVES it gone: thurbox listed every session and that one was not among
them, its task released it, or the session moved to another branch.
"""

from __future__ import annotations

import json
import os

SCHEMA = 1
STORE_ENV = "FLEET_SHEPHERD_DIR"
STORE_FILE = "prs.json"
TERMINAL = ("merged", "closed")


# --- where it lives, and reading and writing it -------------------------------


def store_dir(q) -> str:
    return os.path.join(q.checkout_root(), os.environ.get(STORE_ENV) or os.path.join("orchestration", "shepherd"))


def store_path(q) -> str:
    return os.path.join(store_dir(q), STORE_FILE)


def empty() -> dict:
    return {"schema": SCHEMA, "written_at": None, "sessions": {}}


def load(q) -> tuple[dict, str]:
    """(the store, why it could not be read). A missing file is an empty store and no fault."""
    path = store_path(q)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return empty(), ""
    except (OSError, ValueError) as exc:
        return empty(), f"{path} could not be read: {exc}"
    if not isinstance(doc, dict) or not isinstance(doc.get("sessions"), dict):
        return empty(), f"{path} is not a shepherd store"
    return doc, ""


def save(q, sessions: dict) -> None:
    os.makedirs(store_dir(q), exist_ok=True)
    doc = {"schema": SCHEMA, "written_at": q.now(), "sessions": sessions}
    q.fleet_platform.write_record(store_path(q), json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


# --- one PR, as the store spells it ------------------------------------------


def checks_word(q, cr) -> tuple[str, list]:
    failed, pending = q.check_verdicts(cr)
    if failed:
        return "failing", failed
    if pending:
        return "pending", []
    if not cr.checks:
        return "none", []
    return "passing", []


def state_word(cr) -> str:
    if cr.state == "open" and cr.draft:
        return "draft"
    return cr.state or "open"


def unresolved_threads(q, cr):
    which, _why = q.forge.for_repo(cr.repo)
    if which is None:
        return None
    count, _why = which.threads(cr.ref)
    return count


def entry_for(q, cr, condition: str, detail: str, action: str, note: str) -> dict:
    checks, failing = checks_word(q, cr)
    return {
        "repo": cr.repo.qualified,
        "number": cr.number,
        "url": cr.url,
        "title": cr.title,
        "head_branch": cr.head_branch,
        "head_sha": cr.head_sha,
        "state": state_word(cr),
        "checks": checks,
        "failing_checks": failing,
        "review_decision": cr.review_decision or None,
        "unresolved_threads": unresolved_threads(q, cr) if cr.state == "open" else None,
        "mergeable": cr.mergeable or None,
        "condition": condition,
        "detail": detail,
        "action": action,
        "action_note": note,
        "observed_at": q.now(),
        "stale": False,
        "stale_reason": "",
    }


def stale(entry: dict, why: str) -> dict:
    return {**entry, "stale": True, "stale_reason": why}


# What a task row's `action` means in the store's five words. A fix sent into
# the task's own worker is a line typed into a session, which is what `sent`
# means for an adopted one; a fresh session on the branch is the `fixer`.
TASK_ACTIONS = {
    "merged": "merged",
    "dispatched": "fixer",
    "left-alone": "left-alone",
    "in-flight": "left-alone",
    "policy-refused": "left-alone",
    "not-dispatched": "left-alone",
    "not-merged": "left-alone",
    "needs-human": "left-alone",
    "merge-failed": "left-alone",
}


def task_action(row: dict) -> str:
    if row["action"] == "dispatched" and "reused its own worker" in (row.get("note") or ""):
        return "sent"
    return TASK_ACTIONS.get(row["action"], "none")


# --- the sessions -------------------------------------------------------------


def lead_refusals(q) -> tuple[str, str]:
    """(the lead's name, the lead's id), either "" when nothing here can tell.

    The name the way `notify_lead.lead_name` reads it — FLEET_LEAD_SESSION,
    else the rendered extension.toml's session — and the id the way `reap`
    refuses it, from THURBOX_SESSION.
    """
    name = os.environ.get("FLEET_LEAD_SESSION", "").strip()
    if not name:
        found, _repo = q.manifest_session(os.path.join(q.checkout_root(), "extension.toml"))
        name = found if found and "__" not in found else ""
    return name, os.environ.get("THURBOX_SESSION", "").strip()


def held_sessions(tasks) -> dict:
    """session id -> the task holding it, as its worker or as a fixer sent for it."""
    held: dict = {}
    for task in tasks:
        fixer = (task.doc.get("shepherd") or {}).get("session")
        for sid in (task.doc.get("session"), fixer):
            if sid:
                held.setdefault(str(sid), task)
    return held


def is_local(row: dict) -> bool:
    backend = str(row.get("backend_type") or "")
    return not row.get("host") and (not backend or backend.startswith("local"))


def current_branch(q, path: str) -> str:
    if not path or not os.path.isdir(path):
        return ""
    return q.git_out(path, ["branch", "--show-current"], timeout=10).strip()


def session_branches(q, row: dict) -> list:
    """[(repo, branch)] for each worktree of a session that is on a branch of its own."""
    base = str(row.get("base_branch") or "").removeprefix("origin/") or "main"
    found = []
    for wt in row.get("worktrees") or []:
        if not isinstance(wt, dict):
            continue
        path = str(wt.get("worktree_path") or "")
        branch = current_branch(q, path) or str(wt.get("branch") or "")
        if not branch or branch == base:
            continue
        repo = q.repo_from_checkout(path) or q.repo_from_checkout(str(wt.get("repo_path") or ""))
        if repo is not None and (repo, branch) not in found:
            found.append((repo, branch))
    return found


# --- an adopted session's PR --------------------------------------------------


def brief_text(cr, condition: str, detail: str, checks: tuple) -> str:
    failing = checks[1]
    lines = [
        f"# PR #{cr.number}: {condition}",
        "",
        "Fleet's shepherd watches this session's pull request. Fleet did not",
        "spawn this session, so there is no task brief; this file is the whole",
        "message.",
        "",
        f"- **Pull request.** {cr.url}",
        f"- **Title.** {cr.title}" if cr.title else None,
        f"- **Branch.** `{cr.head_branch}` onto `{cr.base_branch or 'its base'}`, head `{cr.head_sha[:12]}`",
        f"- **Condition.** `{condition}`: {detail}",
    ]
    if failing:
        lines.append("- **Failing checks.** " + ", ".join(failing))
    if cr.mergeable == "conflicting":
        lines.append(f"- **Conflict.** It conflicts with `{cr.base_branch or 'its base'}`; rebase onto it.")
    if cr.review_decision == "changes-requested":
        lines.append("- **Review.** A reviewer requested changes; read the review threads.")
    lines += [
        "",
        "Fix it on the same branch and push, so the pull request updates in",
        "place. Fleet will not merge this pull request for you and will not",
        "send another session at it. It sends this condition once for this",
        "head commit; a new push resets that.",
        "",
    ]
    return "\n".join(line for line in lines if line is not None)


def brief_path(q, sid: str, cr, condition: str) -> str:
    slug = cr.repo.path.replace("/", "-")
    return os.path.join(store_dir(q), "briefs", sid,
                        f"{slug}-{cr.number}-{condition}-{(cr.head_sha or 'nohead')[:12]}.md")


def shepherd_adopted(q, args, sid: str, cr, prev: dict, live: set, merged_by_pass: dict) -> dict:
    """One adopted session's open PR: classify, maybe send one line, and the store entry."""
    condition, detail = q.classify(cr, "pr")
    sent = [s for s in (prev.get("sent") or []) if s.get("head") == cr.head_sha]
    act = [c for c in q.FIXABLE if c != "policy"]

    def done(action: str, note: str) -> dict:
        entry = entry_for(q, cr, condition, detail, action, note)
        entry["sent"] = sent
        return entry

    if cr.url in merged_by_pass:
        return done("merged", merged_by_pass[cr.url])
    if condition == "foreign":
        return done("left-alone", "not ours; fleet only messages about its own repository's branches")
    if condition not in act:
        return done("none", "nothing to tell the session")
    told = next((s for s in sent if s.get("condition") == condition), None)
    if told:
        return done("sent", f"told at {told.get('at')} for this head; not again until it changes")

    status = q.session_status(sid, live)
    if status not in q.SESSION_AT_REST:
        return done("left-alone", f"the session is {status}; it is told once it is at rest by its own word")
    path = brief_path(q, sid, cr, condition)
    if args.dry_run:
        # A dry run writes no store, so this word never reaches the contract.
        return done("would-send", f"a line pointing at {path}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    q.fleet_platform.write_record(path, brief_text(cr, condition, detail, checks_word(q, cr)))
    line = (f"fleet shepherd: PR #{cr.number} on {cr.repo} is {condition}. "
            f"Read {os.path.abspath(path)} and do what it says.")
    ok, report = q.trust_and_send(sid, line)
    if not ok:
        return done("left-alone", f"could not send: {report}")
    sent.append({"condition": condition, "head": cr.head_sha, "at": q.now(), "brief": os.path.abspath(path)})
    return done("sent", f"one line sent pointing at {os.path.abspath(path)}")


# --- carrying what this pass did not see --------------------------------------


def carry(q, entry: dict, readable: dict) -> dict | None:
    """A previous entry this pass did not see open: settled, kept stale, or None to drop.

    `readable` maps a host-qualified repo this pass asked about to "" (it
    answered) or why it did not. A repo nobody asked about this pass is left
    exactly as it was.
    """
    if entry.get("state") in TERMINAL:
        return entry
    repo = entry.get("repo")
    if repo not in readable:
        return entry
    if readable[repo]:
        return stale(entry, readable[repo])
    # The repo answered and this PR was not open in it: it merged, closed, or
    # this session moved off it. Only the forge can say which.
    which, ref = q.forge.for_url(entry.get("url") or "")
    if which is None:
        return stale(entry, str(ref))
    state, why = which.state(ref)
    if state is None:
        return stale(entry, why)
    if state == "open":
        return None
    return {**entry, "state": state, "action": "none", "action_note": f"the pull request is {state}",
            "observed_at": q.now(), "stale": False, "stale_reason": ""}


def carry_session(q, prev: dict, fresh: list, readable: dict, branches: set | None) -> list:
    """Fresh entries first, then whatever of the previous ones is still this session's."""
    seen = {e["url"] for e in fresh}
    out = list(fresh)
    for entry in prev.get("prs") or []:
        if entry.get("url") in seen:
            continue
        if branches is not None and entry.get("head_branch") not in branches:
            continue
        kept = carry(q, entry, readable)
        if kept is not None:
            out.append(kept)
    return out


def mark_stale(session: dict, why: str) -> dict:
    return {**session, "prs": [e if e.get("stale") else stale(e, why) for e in session.get("prs") or []]}


# --- the pass -----------------------------------------------------------------


def forge_unavailable(q, why: str) -> None:
    """No forge CLI at all: every entry stays, marked stale, and nothing is adopted."""
    previous, _why = load(q)
    if previous["sessions"]:
        save(q, {sid: mark_stale(s, why) for sid, s in previous["sessions"].items()})


def after_pass(q, args, all_tasks: list, observed: list, listings: dict, unreadable: list) -> list:
    """Adopt outside sessions, shepherd their PRs, and rewrite the store. Returns the adopted rows.

    `observed` is the main pass's (change request, task, row) for every open
    PR it looked at; `listings` the repos it listed, by RepoId; `unreadable`
    its rows for the repos it could not.
    """
    previous, _why = load(q)
    prev_sessions = previous["sessions"]
    readable = {r.qualified: "" for r in listings}
    readable.update({u["repo"]: u["detail"] for u in unreadable})
    scoped = bool(getattr(args, "topic", None) or getattr(args, "ref", None))
    snapshot, snap_why = q.session_snapshot()
    held = held_sessions(all_tasks)
    sessions: dict = {}

    # The tasks' own sessions, from what the main pass already classified.
    by_session: dict = {}
    for cr, task, row in observed:
        sid = str(task.doc.get("session") or "") if task else ""
        if sid:
            by_session.setdefault(sid, (task, []))[1].append(
                entry_for(q, cr, row["condition"], row["detail"], task_action(row), row.get("note") or ""))
    for sid, task in held.items():
        if str(task.doc.get("session") or "") != sid:
            continue
        fresh = by_session.get(sid, (task, []))[1]
        prev = prev_sessions.get(sid) or {}
        prs = carry_session(q, prev, fresh, readable, None)
        if not prs:
            continue
        row = (snapshot or {}).get(sid) or {}
        sessions[sid] = {"session": sid, "name": str(row.get("name") or task.doc.get("title") or task.ref),
                         "task": task.ref, "adopted": False, "prs": prs}

    adopted_rows: list = []
    if scoped:
        # A narrowed pass adopts nothing and settles nothing it did not look at.
        for sid, prev in prev_sessions.items():
            sessions.setdefault(sid, prev)
    elif snapshot is None:
        for sid, prev in prev_sessions.items():
            if sid not in sessions and prev.get("adopted"):
                sessions[sid] = mark_stale(prev, f"thurbox could not be read: {snap_why}")
    else:
        adopted_rows = adopt(q, args, snapshot, held, prev_sessions, listings, readable, observed, sessions)

    if not args.dry_run:
        save(q, sessions)
    return adopted_rows


def adopt(q, args, snapshot: dict, held: dict, prev_sessions: dict, listings: dict, readable: dict,
          observed: list, sessions: dict) -> list:
    lead_name, lead_id = lead_refusals(q)
    live = set(snapshot)
    merged_by_pass = {cr.url: row.get("note") or "" for cr, _t, row in observed if row["action"] == "merged"}
    rows: list = []
    for sid, row in sorted(snapshot.items(), key=lambda kv: str(kv[1].get("name") or kv[0])):
        name = str(row.get("name") or "")
        if sid in held or sid == lead_id or (lead_name and name == lead_name) or not is_local(row):
            continue
        prev = prev_sessions.get(sid) or {}
        prev_by_url = {e.get("url"): e for e in prev.get("prs") or []}
        fresh: list = []
        branches: set = set()
        for repo, branch in session_branches(q, row):
            branches.add(branch)
            if repo not in listings:
                crs, err = q.open_prs(repo)
                if err:
                    readable[repo.qualified] = err
                    continue
                listings[repo] = crs
                readable[repo.qualified] = ""
            for cr in listings[repo]:
                if cr.head_branch == branch and cr.head_is_ours is not False:
                    entry = shepherd_adopted(q, args, sid, cr, prev_by_url.get(cr.url) or {}, live, merged_by_pass)
                    fresh.append(entry)
                    rows.append({"session": sid, "name": name, "pr": cr.url, "repo": cr.repo.qualified,
                                 "condition": entry["condition"], "detail": entry["detail"],
                                 "action": entry["action"], "note": entry["action_note"]})
        prs = carry_session(q, prev, fresh, readable, branches)
        # Adopted on an OPEN pull request; one already followed keeps how it ended.
        if fresh or (prev.get("adopted") and prs):
            sessions[sid] = {"session": sid, "name": name, "task": None, "adopted": True, "prs": prs}
    return rows


# --- what the pass prints, and `fleet queue prs` ------------------------------


def print_adopted(rows: list, dry_run: bool) -> None:
    if not rows:
        return
    verb = "would do" if dry_run else "did"
    print(f"shepherd: {len(rows)} pull request(s) from sessions fleet did not spawn — what it {verb}:\n")
    for r in rows:
        print(f"    {r['name'] or r['session']}  {r['pr']}")
        print(f"        {r['condition']}: {r['detail']}")
        print(f"        {r['action']}: {r['note']}")
    print("\n          Adopted sessions are told, once per condition per head and only at rest;"
          "\n          fleet never sends them a fixer.\n")


def cmd_prs(q, args) -> int:
    doc, why = load(q)
    if args.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0
    if why:
        print(f"prs: {why}")
        return 0
    sessions = doc.get("sessions") or {}
    if not sessions:
        print(f"prs: nothing recorded yet — `fleet queue shepherd` writes {store_path(q)}")
        return 0
    print(f"prs: as of {doc.get('written_at')}, {len(sessions)} watched session(s)\n")
    for sid, s in sorted(sessions.items(), key=lambda kv: str(kv[1].get("name") or kv[0])):
        whose = s.get("task") or "adopted"
        print(f"    {s.get('name') or sid}  ({whose})")
        for e in s.get("prs") or []:
            stale_note = f"  STALE: {e.get('stale_reason')}" if e.get("stale") else ""
            print(f"        #{e.get('number')} {e.get('repo')}  {e.get('state')}, checks {e.get('checks')}, "
                  f"{e.get('condition')} -> {e.get('action')}{stale_note}")
    return 0
