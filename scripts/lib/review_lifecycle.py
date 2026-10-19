"""Opt-in independent review, owned by queue commands, observed by the reconciler.

publish.conf's REVIEW_REPOS (host-qualified, comma separated), REVIEWER_TASK
and REVIEW_SCORE bind a repository to a durable reviewer task and its required
score. Empty REVIEW_REPOS disables this contract. Misconfiguration never passes.
There is no default reviewer, publisher, review tool or repository.

Each PR has one runtime record under shepherd/reviews, protected by an OS lock.
It holds the assessment the gate reads and, as `rounds`, every one recorded
before it — head, score, findings, state, at — which the task's trace reads.
Publication and CI do not attest a review. Only `queue review record`, run from
the configured reviewer's live session, can record an assessment. It requires a
full current head and a readable, linked forge summary containing that head.
The reviewer is responsible for its score and findings; fleet does not guess
English review prose. The configured session must not own or fix the PR.

An assessment is invalidated by a new head, reviewer assignment, policy change,
or any changed forge feedback (including resolved threads). Errors stay pending.
A deliberate session stop preserves a completed assessment; it prevents new
recordings and wakes. Reviewer task retirement, blockers or absence invalidate it.
A clean assessment supplements the existing shepherd merge gate. This module
never merges, dispatches, spawns, cancels, or writes task records.

Notifications use the existing empty-composer and rest checks. Busy sessions
get one no-wake mailbox note; a wake waits. Receipts are per obligation and
session/conversation, recorded only on successful delivery. Pending obligations
also reach plan's lead notification, so a reviewer that ends a turn without an
assessment cannot silently finish the review. No automatic reviewer replacement.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager


def policy(q, repo):
    conf = q.publish_conf()
    repos = [r.strip() for r in conf.get("REVIEW_REPOS", "").split(",") if r.strip()]
    if not repos:
        return None
    invalid = any(q.forge.RepoId.parse(r) is None for r in repos)
    if repo is None or (repo.qualified.casefold() not in {r.casefold() for r in repos} and not invalid):
        return None
    ref = conf.get("REVIEWER_TASK", "").strip()
    try:
        score = int(conf.get("REVIEW_SCORE", ""))
    except ValueError:
        score = 0
    return {"task": ref, "score": score,
            "error": "invalid review policy" if invalid or not ref or score <= 0 else ""}


def configured_repos(q):
    return {repo for value in q.publish_conf().get("REVIEW_REPOS", "").split(",")
            if (repo := q.forge.RepoId.parse(value.strip())) is not None}


def observed_state(q, cr):
    if policy(q, cr.repo) is None:
        return ""
    doc = read(path(q, cr.url))
    return doc.get("state", "pending") if doc.get("head") == cr.head_sha else "pending"


def directory(q):
    return os.path.join(q.shepherd_store().store_dir(q), "reviews")


_RECORD_SUFFIX = ".json"

# Rounds kept per change request: far past any review that converges, and a
# bound on a record a loop re-reads every pass.
MAX_ROUNDS = 100


def path(q, url):
    return os.path.join(directory(q), hashlib.sha256(url.encode()).hexdigest() + _RECORD_SUFFIX)


def read(filename):
    try:
        with open(filename, encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


@contextmanager
def locked(q, url):
    os.makedirs(directory(q), exist_ok=True)
    filename = path(q, url)
    try:
        with q.fleet_platform.exclusive_lock(filename + ".lock", wait=2):
            yield filename, read(filename)
    except q.fleet_platform.LockHeld as exc:
        raise q.QueueError("review record is busy; retry") from exc


def reviewer(q, pol):
    try:
        queue = q.Queue(q.queue_root(), scope="all")
        task = queue.get(pol["task"])
    except q.QueueError:
        return "", "configured reviewer task is absent"
    if (task.state not in ("dispatched", "done", "landed")
            or any(not queue.blocker_cleared(b) for b in task.blockers)):
        return "", "configured reviewer task is retired or blocked"
    return str(task.doc.get("session") or ""), ""


def owners(q, cr):
    queue = q.Queue(q.queue_root(), scope="all")
    tasks = list(queue.tasks.values())
    targets = q.shepherd_targets(tasks)
    task = q.link_task(cr, [t for t in tasks if cr.repo in targets.get(t.ref, ())])
    if not task:
        return set()
    return {str(s) for s in (task.doc.get("session"),
                            (task.doc.get("reaped") or {}).get("session"),
                            q.rec_for(task, cr.url).get("session"),
                            q.comments_record(task, cr.url).get("session")) if s}


def feedback(q, cr):
    which, why = q.forge.for_repo(cr.repo)
    items, why = which.feedback(cr.ref) if which else (None, why)
    if why or items is None:
        return [], "", why or "feedback not read"
    fields = [(f.id, f.body, f.state, f.resolved) for f in items]
    digest = hashlib.sha256(json.dumps(sorted(fields), ensure_ascii=True).encode()).hexdigest()
    return items, digest, ""


def recipient_refusal(q, cr, sid):
    rule = q.agents_for_repo(cr.repo, q.agent_policy())
    if rule is None:
        return ""
    task = next((t for t in q.Queue(q.queue_root(), scope="all").tasks.values()
                 if t.doc.get("session") == sid), None)
    agent = q.task_agent(task) if task else ""
    allowed, prefix = rule
    return "" if agent and agent in allowed else f"review recipient is not permitted by agent policy {prefix}"


def assessment(q, cr, pol, doc):
    sid, why = reviewer(q, pol)
    items, digest, error = feedback(q, cr)
    saved = doc.get("assessment") or {}
    live, live_error = q.live_sessions() if saved else (set(), "")
    present = live is not None and sid in live
    refusal = recipient_refusal(q, cr, sid)
    valid = (not pol["error"] and not error and not refusal and bool(cr.head_sha)
             and saved.get("head") == cr.head_sha and saved.get("reviewer") == sid
             and saved.get("policy") == pol and saved.get("feedback") == digest
             and present
             and sid not in owners(q, cr)
             and any(f.url == saved.get("evidence") and cr.head_sha in f.body for f in items))
    state = saved.get("state", "pending") if valid else "pending"
    absent = (live_error or "configured reviewer session is absent") if saved and not present else ""
    return state, sid, digest, pol["error"] or why or error or absent or refusal


def _post_once(helper, doc, receipt, sid, line):
    """A busy recipient gets one no-wake mailbox note; the wake waits."""
    if not receipt.get("posted"):
        receipt["posted"], _ = helper.post(sid, line, kind="fleet-review")
    doc["delivery"] = receipt
    return "waiting", "wake waits for rest and an empty input line"


def notify(q, doc, sid, key, line):
    helper = q._load_sibling("fleet_notify_delivery", "notify_delivery.py")
    live, why = q.live_sessions()
    status = q.session_status(sid, live) if sid else "gone"
    if status in ("gone", "unknown"):
        return "needs-lead", why or "review recipient absent or unreadable"
    row, why = q.session_doc(sid)
    if not row:
        return "needs-lead", why
    if row.get("stopped") or row.get("state") == "stopped":
        return "needs-lead", "review recipient is deliberately stopped"
    # The pane probe can change between reads; use the latest word before
    # choosing a mailbox note or a wake.
    status = str(row.get("state") or "unknown")
    identity = helper.identity(row)
    receipt = doc.get("delivery") or {}
    if receipt.get("key") != key or receipt.get("identity") != identity:
        receipt = {"key": key, "identity": identity}
    if receipt.get("woken"):
        return "awaiting-review", "obligation delivered; assessment still required"
    if status not in q.SESSION_AT_REST or not helper.composer_empty(sid):
        return _post_once(helper, doc, receipt, sid, line)
    receipt["woken"], why = helper.wake(sid, line)
    doc["delivery"] = receipt
    return ("sent", "review obligation delivered") if receipt["woken"] else ("needs-lead", why)


def _store(q, filename, latest, cr, pol, state, digest, action, detail):
    latest.update(url=cr.url, repo=cr.repo.qualified, head=cr.head_sha, state=state,
                  action=action, detail=detail, reviewer_task=pol["task"],
                  feedback=digest, required_score=pol["score"])
    q.fleet_platform.write_record(filename, json.dumps(latest) + "\n")


def _store_clean(q, cr, args, pol, doc, digest, view):
    """Records a clean state; the hold to report instead when the assessment moved underneath."""
    if args.dry_run:
        return None
    with locked(q, cr.url) as (filename, latest):
        if latest.get("assessment") != doc.get("assessment"):
            view.update(state="pending", action="retry", detail="assessment changed; retry")
            return "review-pending", view["detail"]
        _store(q, filename, latest, cr, pol, "clean", digest, "none", "")
    return None


def _fixer(q, cr, task, error):
    """The live session to hand findings to, and what stops that when nothing can take them."""
    candidates = (q.comments_record(task, cr.url).get("session"),
                  q.rec_for(task, cr.url).get("session"), task.doc.get("session")) if task else ()
    live, _ = q.live_sessions()
    sid = next((str(s) for s in candidates if s and q.session_status(str(s), live) != "gone"), "")
    refusal = q.agent_policy_refusal(task) if task and not task.doc.get("host") else None
    error = error or refusal or ("no existing fixer; lead must assign one" if not sid else "")
    return sid, error


def _obligation_line(q, cr, state):
    command = q.fleet_platform.shell_command([
        "uv", "run", "--project", str(q.checkout_root()), "fleet", "queue", "review", "record",
        cr.url, "--head", cr.head_sha, "--score", "<score>", "--findings", "<count>",
        "--evidence", "<summary-note-url>",
    ])
    return (f"fleet review: {cr.url} head {cr.head_sha} is review-{state}. "
            f"Read the forge feedback and the configured review policy. "
            + ("Fix valid findings in place, publish, and await independent reassessment."
               if state == "findings" else
               "Review the full head independently, publish a linked summary, then run: "
               + command))


def _hold_for_review(q, cr, task, args, pol, state, sid, digest, error, view):
    """Delivers the review obligation of a PR the other gates clear, which `gate` holds."""
    if state == "findings":
        sid, error = _fixer(q, cr, task, error)
    if sid in owners(q, cr) and state == "pending":
        error = "configured reviewer owns or fixes this PR; independent review required"
    line = _obligation_line(q, cr, state)
    if args.dry_run:
        view["action"] = "would-notify" if sid and not error else "needs-lead"
        return
    with locked(q, cr.url) as (filename, latest):
        # A concurrent reviewer record wins; never overwrite its assessment.
        latest_state, _, latest_digest, _ = assessment(q, cr, pol, latest)
        if latest_state != state or latest_digest != digest:
            view["action"] = "retry"
        elif error:
            view["action"], view["detail"] = "needs-lead", error
        else:
            key = f"{cr.head_sha}:{state}:{digest}:{pol['task']}:{pol['score']}"
            view["action"], view["detail"] = notify(q, latest, sid, key, line)
        _store(q, filename, latest, cr, pol, state, digest, view["action"], view["detail"])


def gate(q, cr, task, args, condition, detail):
    pol = policy(q, cr.repo)
    if pol is None:
        return condition, detail, None
    filename = path(q, cr.url)
    doc = read(filename)
    state, sid, digest, error = assessment(q, cr, pol, doc)
    view = {"state": state, "head": cr.head_sha, "reviewer_task": pol["task"],
            "action": "none", "detail": error}
    if state == "clean":
        held = _store_clean(q, cr, args, pol, doc, digest, view)
        if held:
            return held[0], held[1], view
        return condition, detail, view
    if condition not in ("ready", "threads-open"):
        view["detail"] = error or detail
    else:
        condition, detail = "review-" + state, error or f"independent current-head review {state}"
        _hold_for_review(q, cr, task, args, pol, state, sid, digest, error, view)
    if not args.dry_run and condition not in ("review-pending", "review-findings"):
        with locked(q, cr.url) as (filename, latest):
            _store(q, filename, latest, cr, pol, state, digest, view["action"], view["detail"])
    return condition, detail, view


def _recording_reviewer(q, cr, pol):
    """The configured reviewer's session id, when this process is it and may record."""
    sid, _ = reviewer(q, pol)
    if not sid or os.environ.get("THURBOX_SESSION") != sid:
        raise q.QueueError("only the configured reviewer session can record this assessment")
    live, _ = q.live_sessions()
    if live is None or sid not in live or sid in owners(q, cr):
        raise q.QueueError("configured reviewer is absent or owns/fixes this PR")
    session, _ = q.session_doc(sid)
    if not session or session.get("stopped") or session.get("state") == "stopped":
        raise q.QueueError("configured reviewer session is unreadable or deliberately stopped")
    refusal = recipient_refusal(q, cr, sid)
    if refusal:
        raise q.QueueError(refusal)
    return sid


def _require_mergeable_if_clean(q, cr, clean):
    """A clean assessment is only recorded on a PR every other gate already clears."""
    condition, detail = q.classify(cr, "pr")
    if not clean:
        return
    if condition == "ready":
        condition, detail = q.threads_gate(cr, detail)
    if condition != "ready":
        raise q.QueueError("clean review cannot be recorded: " + detail)


def record(q, args):
    which, ref = q.forge.for_url(args.url)
    cr, why = which.get(ref) if which else (None, str(ref))
    if cr is None:
        raise q.QueueError("review head not read: " + why)
    pol = policy(q, cr.repo)
    if not pol or pol["error"]:
        raise q.QueueError("no valid configured review policy")
    sid = _recording_reviewer(q, cr, pol)
    if (cr.state != "open" or cr.draft or cr.head_is_ours is not True
            or not re.fullmatch(r"[0-9a-f]{40}", args.head) or args.head != cr.head_sha):
        raise q.QueueError("review must name the full current head of an open, owned, non-draft PR")
    if args.score < 0 or args.score > pol["score"] or args.findings < 0:
        raise q.QueueError("score or findings outside configured review range")
    items, digest, why = feedback(q, cr)
    if why:
        raise q.QueueError("review evidence not read: " + why)
    if not any(f.url == args.evidence and args.head in f.body for f in items):
        raise q.QueueError("linked forge review summary must name the full current head")
    clean = args.score == pol["score"] and args.findings == 0
    _require_mergeable_if_clean(q, cr, clean)
    latest, why = which.get(ref)
    if latest is None or latest.head_sha != args.head or latest.state != "open" or latest.draft:
        raise q.QueueError("current head changed or could not be re-read; review not recorded")
    with locked(q, cr.url) as (filename, doc):
        doc["assessment"] = {"head": args.head, "reviewer": sid, "policy": pol,
                             "feedback": digest, "evidence": args.evidence,
                             "score": args.score, "findings": args.findings,
                             "state": "clean" if clean else "findings", "at": q.now()}
        # The assessment above is what the gate reads, and a new head replaces
        # it; every round stays here, for the task's trace (telemetry.py).
        rounds = doc.get("rounds") if isinstance(doc.get("rounds"), list) else []
        rounds.append({k: doc["assessment"][k] for k in ("head", "score", "findings", "state", "at")})
        doc["rounds"] = rounds[-MAX_ROUNDS:]
        doc.update(url=cr.url, repo=cr.repo.qualified, head=args.head,
                   state=doc["assessment"]["state"], action="recorded", reviewer_task=pol["task"])
        q.fleet_platform.write_record(filename, json.dumps(doc) + "\n")
    print(f"review {doc['state']}: {cr.url} at {args.head}")
    return 0


def pending(q):
    rows = []
    if not os.path.isdir(directory(q)):
        return rows
    for name in sorted(os.listdir(directory(q))):
        if not name.endswith(_RECORD_SUFFIX):
            continue
        doc = read(os.path.join(directory(q), name))
        pol = policy(q, q.forge.RepoId.parse(doc.get("repo", ""))) if doc.get("repo") else None
        if not pol or doc.get("state") in ("clean", "closed"):
            continue
        rows.append({**doc, "task": f"{doc.get('url')}@{doc.get('head')}:review-{doc.get('state')}:"
                                    f"{doc.get('feedback')}:"
                                    f"{doc.get('reviewer_task')}:{doc.get('required_score')}:"
                                    f"{'needs-lead' if doc.get('action') == 'needs-lead' else 'pending'}"})
    return rows


def retire(q, listings, unreadable, dry_run):
    if dry_run or not os.path.isdir(directory(q)):
        return
    for name in os.listdir(directory(q)):
        if not name.endswith(_RECORD_SUFFIX):
            continue
        filename = os.path.join(directory(q), name)
        doc = read(filename)
        repo = q.forge.RepoId.parse(doc.get("repo", ""))
        failed = next((r["detail"] for r in unreadable
                       if r["repo"].casefold() == str(doc.get("repo", "")).casefold()), "")
        if failed:
            with locked(q, doc["url"]) as (filename, latest):
                latest.update(state="pending", action="needs-lead", detail="forge not read: " + failed)
                q.fleet_platform.write_record(filename, json.dumps(latest) + "\n")
            continue
        if repo not in listings or any(cr.url == doc.get("url") for cr in listings[repo]):
            continue
        with locked(q, doc["url"]) as (filename, latest):
            latest.update(state="closed", action="none", detail="forge no longer lists this PR as open")
            q.fleet_platform.write_record(filename, json.dumps(latest) + "\n")


def holds_reviewer(q, task):
    conf = q.publish_conf()
    return bool(conf.get("REVIEW_REPOS", "").strip()
                and conf.get("REVIEWER_TASK", "").strip() == task.ref
                and task.state in ("dispatched", "done", "landed"))


def before_merge(q, cr, method):
    pol = policy(q, cr.repo)
    if pol is None:
        return True, ""
    which, why = q.forge.for_repo(cr.repo)
    fresh, why = which.get(cr.ref) if which else (None, why)
    if fresh is None or not fresh.head_sha or fresh.head_sha != cr.head_sha:
        return False, "reviewed head changed or could not be re-read; nothing merged"
    condition, detail = q.classify(fresh, method or "pr")
    if condition == "ready":
        condition, detail = q.threads_gate(fresh, detail)
    state, _, _, error = assessment(q, fresh, pol, read(path(q, cr.url)))
    if condition != "ready" or state != "clean":
        return False, error or f"current-head merge recheck: {condition}, review-{state}: {detail}"
    return True, ""


def unavailable(q, why):
    errors = [{"repo": repo.qualified, "detail": why} for repo in configured_repos(q)]
    retire(q, {}, errors, False)
