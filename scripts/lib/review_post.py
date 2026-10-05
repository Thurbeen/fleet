"""Publish one maintained review summary and only concrete inline findings.

Usage: uv run fleet review-post <change-request-url> --file review.yaml
       [--config orchestration/review.conf]

The input is an authored verdict about one pinned head, not an automatic
approval. Required keys: head (full SHA), verdict (approve/request-changes/comment),
summary, confidence (0..1), review (served URL), findings (a list, possibly empty).
Each finding has a stable id, severity (critical/high/medium/low), body, path and
head-side line. Removing an id asserts its finding was fixed: its thread is
resolved. Keep ids stable across heads; unrelated human threads are never touched.

The forge seam owns every API call. GitHub posts new findings in one review
batch; GitLab stages draft notes and publishes them together.
Neither writes an empty inline review or casts an approval vote. The maintainer
skill owns those decisions separately. Review summaries are edited by marker and
current author. Failed reads and stale pins stop before any mutation.

PUBLIC_REVIEW_LINKS=off in orchestration/review.example.conf is the default.
Only the operator's ignored review.conf may enable public links. An explicit
--config is useful on a remote worker. Private repositories always carry them;
GitLab internal visibility uses the public rule. No URL goes into a PR body.
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from fleet.cli import load

forge = load("forge.py")
agent_settings = load("agent_settings.py")
MARKER = "<!-- fleet-review -->"
FINDING = re.compile(r"<!-- fleet-finding:([a-zA-Z0-9_-]+) -->")
SEVERITY = ("critical", "high", "medium", "low")


def validate(doc):
    if not isinstance(doc, dict):
        raise ValueError("review input must be a mapping")
    if not isinstance(doc.get("head"), str) or not re.fullmatch(r"[0-9a-f]{40}", doc["head"]):
        raise ValueError("head must be a full commit SHA")
    if doc.get("verdict") not in ("approve", "request-changes", "comment"):
        raise ValueError("verdict must be approve, request-changes or comment")
    score = doc.get("confidence")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
        raise ValueError("confidence must be between 0 and 1")
    for field in ("summary", "review"):
        if not isinstance(doc.get(field), str) or not doc[field].strip():
            raise ValueError(f"{field} must be nonempty text")
    url = urlsplit(doc["review"])
    if url.scheme not in ("http", "https") or not url.netloc or any(c.isspace() for c in doc["review"]):
        raise ValueError("review must be an HTTP(S) URL")
    if not isinstance(doc.get("findings"), list):
        raise ValueError("findings must be a list")
    ids = set()
    for f in doc["findings"]:
        if (not isinstance(f, dict) or not isinstance(f.get("id"), str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]+", f["id"])):
            raise ValueError("each finding needs a stable id (letters, numbers, _ or -)")
        if f["id"] in ids:
            raise ValueError("finding ids must be unique")
        ids.add(f["id"])
        if f.get("severity") not in SEVERITY:
            raise ValueError("finding severity must be critical/high/medium/low")
        if isinstance(f.get("line"), bool) or not isinstance(f.get("line"), int) or f["line"] < 1:
            raise ValueError("finding line must be a positive head-side line")
        if not all(isinstance(f.get(k), str) and f[k].strip() for k in ("body", "path")):
            raise ValueError("finding body and path must be nonempty text")
        if MARKER in f["body"] or FINDING.search(f["body"]):
            raise ValueError("finding body must not contain fleet markers")
    return doc


def post(adapter, ref, doc, public_links=False):
    snapshot, why = adapter.review_snapshot(ref)
    if why:
        raise ValueError(why)
    if snapshot["head"] != doc["head"]:
        raise ValueError("change request head moved; re-publish the review before posting")
    login, why = adapter.whoami(ref.repo.host)
    if why or not login:
        raise ValueError(why or "review author could not be determined")
    notes = [n for n in snapshot["notes"] if MARKER in n["body"] and n["author"] == login]
    if len(notes) > 1:
        raise ValueError("multiple fleet summaries already exist; reconcile them before posting")
    findings = sorted(doc["findings"], key=lambda f: SEVERITY.index(f["severity"]))
    link = doc["review"] if snapshot["private"] or public_links else "thurview review available to the operator"
    stamp = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    ranked = "; ".join(f"{f['severity']}: {f['body']}" for f in findings) or "No concrete findings."
    body = (f"{MARKER}\nVerdict: {doc['verdict']} — {doc['summary']}\n"
            f"{ranked}\nConfidence: {doc['confidence']} · Head: `{doc['head']}` · Reviewed: {stamp}\n"
            f"{link}\n" + str(doc.get("signature") or "")).rstrip()
    if not snapshot["private"] and not public_links:
        body = body.replace(doc["review"], "thurview review available to the operator")
    active = {f["id"] for f in findings}
    known = set()
    for thread in snapshot["threads"]:
        match = FINDING.search(thread["body"])
        if not match or thread["author"] != login:
            continue
        fid = match[1]
        if not thread["resolved"]:
            known.add(fid)
            if fid not in active:
                ok, why = adapter.resolve_finding(ref, thread["id"])
                if not ok:
                    raise ValueError(why)
    fresh = [{**f, "body": f"<!-- fleet-finding:{f['id']} -->\n{f['severity']}: {f['body']}"
              + (f"\n{doc['signature']}" if doc.get("signature") else "")}
             for f in findings if f["id"] not in known]
    if not snapshot["private"] and not public_links:
        fresh = [{**f, "body": f["body"].replace(
            doc["review"], "thurview review available to the operator"
        )} for f in fresh]
    if fresh:
        ok, why = adapter.review_findings(ref, doc["head"], fresh, snapshot["pins"])
        if not ok:
            raise ValueError(why)
    ok, why = adapter.review_summary(ref, body, notes[0]["id"] if notes else None)
    if not ok:
        raise ValueError(why)
    return "updated" if notes else "posted"


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url")
    parser.add_argument("--file", required=True)
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    try:
        with open(args.file, encoding="utf-8") as fh:
            doc = validate(yaml.safe_load(fh))
        root = Path(__file__).resolve().parents[2] / "orchestration"
        local = root / "review.conf"
        config = Path(args.config) if args.config else (local if local.exists() else root / "review.example.conf")
        settings = agent_settings.read_conf(str(config))
        value = settings.get("PUBLIC_REVIEW_LINKS", "off")
        if value not in ("off", "on"):
            raise ValueError("PUBLIC_REVIEW_LINKS must be off or on")
        adapter, ref = forge.for_url(args.url)
        if not adapter or not ref:
            raise ValueError("URL names no configured change request")
        action = post(adapter, ref, doc, value == "on")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"review-post: {exc}", file=sys.stderr)
        return 1
    print(f"review-post: {action} summary for {args.url} at {doc['head']}")
    return 0
