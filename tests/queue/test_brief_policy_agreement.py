"""The brief and the policy it points at say one thing about `message send`.

A brief whose task has a `reply` tells its worker to post its verdict, its
change request and every question with that command — by default
`thurbox-cli message send` into the session the task came from. POLICY.md,
which the same brief says outranks it, once forbade that command outright. On
2026-10-15 four workers met both: two posted, two did not and named the
contradiction in their `result.md`. What a worker did turned on which line it
read more carefully.

So this reads what a worker reads, the way it reaches it: the brief `fleet
queue add` writes, and the policy file at the path that brief names — never
the renderer or a hard-coded path, which would stay green with the section or
the pointer unwired.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from queuekit import ok

from harness import expect, run_queue as q

SECTION = "Replying to the requester"
# Lexical, deliberately broad: a false red costs a rewording, a false green
# costs the 2026-10-15 split again.
FORBIDS = re.compile(r"\b(?:not|never|don't|avoid|forbid\w*|prohibit\w*|nothing)\b", re.I)


def scaffolded_brief(queue_dir: Path) -> str:
    topic = ok(q("topic", "add", "asked", "--title", "Asked", "--prompt", "p")).stdout.strip()
    # The default route: `add` run inside a thurbox session owns the task and
    # gets that session's mailbox as its reply, with no flag naming either.
    ref = ok(q("add", topic, "talks", "--title", "Talks", "--repo", "/tmp/repo-a",
               "--branch", "fix/talks", "--base", "main", "--publish", "pr",
               THURBOX_SESSION="sess-origin")).stdout.strip()
    return (queue_dir / ref / "BRIEF.md").read_text(encoding="utf-8")


def policy_named_by(brief: str) -> str:
    paths = re.findall(r"\*\*Standing policy\.\*\* `([^`]+)`", brief)
    assert len(paths) == 1, f"the brief names {len(paths)} standing policies, not one:\n{brief}"
    return Path(paths[0]).read_text(encoding="utf-8")


def reply_verb(brief: str) -> str:
    """The program and verb the brief's reply section tells the worker to run."""
    expect(brief, f"## {SECTION}")
    [cmd] = re.findall(r"Post with `(.+?) '<text>'`", brief)
    words = shlex.split(cmd)
    # A program's verb is the words before its first flag.
    return " ".join(words[:next((i for i, w in enumerate(words) if w.startswith("-")), len(words))])


def paragraphs(text: str) -> list[str]:
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]


def sentences(paragraph: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", paragraph)


def permits(paragraph: str) -> bool:
    """Whether a sentence here tells the worker to post through the brief's section, unhedged."""
    return any(SECTION in s and re.search(r"\bpost\b", s, re.I) and not FORBIDS.search(s)
               for s in sentences(paragraph))


def test_the_policy_never_forbids_the_reply_command_the_brief_hands_the_worker(queue_dir):
    brief = scaffolded_brief(queue_dir)
    verb = reply_verb(brief)
    assert verb == "thurbox-cli message send", verb
    policy = policy_named_by(brief)

    # Every paragraph of the policy that names the command — with or without
    # its program, the way the playbooks write it — and forbids anything must
    # also tell the worker, in a sentence of its own that hedges nothing, to
    # post through the brief's section: a bare "do not use it" outranks the
    # brief that told the worker to.
    short = " ".join(verb.split()[-2:])
    bans = [p for p in paragraphs(policy) if short in p and FORBIDS.search(p)]
    unqualified = [p for p in bans if not permits(p)]
    assert not unqualified, (
        f"POLICY.md forbids `{verb}` without telling the worker to post through the"
        f" brief's '{SECTION}' section, which hands it that command:\n\n" + "\n\n".join(unqualified))


def test_the_policy_says_what_the_reply_route_is_for(queue_dir):
    # The other half of agreement: the policy itself tells the worker to use
    # the route, so a mention that forbids it — "whatever the Replying to the
    # requester section says" — is not acknowledgement.
    policy = policy_named_by(scaffolded_brief(queue_dir))
    assert any(permits(p) for p in paragraphs(policy)), (
        f"POLICY.md never tells the worker to post through the brief's '{SECTION}' section")
