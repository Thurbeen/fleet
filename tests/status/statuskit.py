"""What the status tests stand on: a queue with something in it, and what each probe answers.

The answers are the tools' real shapes: quota-axi schemaVersion 5 (measured
fields AND projected ones side by side, so a test can prove which reach the
screen), `thurbox-cli session list --json`, and `gh pr list --json`.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness import run_queue, write

BRIEF = "## What to do\n\nDo the thing.\n\n## Hard constraints\n\nNone.\n\n## Coordination\n\nNone.\n\n## Done means\n\nIt is done.\n"
S1 = "11111111-1111-1111-1111-111111111111"


def ok(done):
    assert done.code == 0, done.out
    return done


def build_queue(tmp: Path) -> None:
    """One topic: a dispatched task, a ready one, and one blocked on the first."""
    repo = tmp / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    write(tmp / "brief.md", BRIEF)
    ok(run_queue("topic", "add", "selftest", "--title", "Selftest topic", "--prompt", "the prompt, verbatim"))
    for slug, title, branch, touches in (
        ("dispatched-task", "A dispatched task", "t/dispatched", ["--touches", "FLEET.md"]),
        ("ready-task", "A ready task", "t/ready", ["--touches", "FLEET.md"]),
        ("waiting-task", "A waiting task", "t/waiting", []),
    ):
        ok(run_queue("add", "selftest", slug, "--title", title, "--repo", str(repo), "--branch", branch,
                     *touches, "--brief-file", str(tmp / "brief.md")))
    ok(run_queue("block", "selftest/03-waiting-task", "--on", "selftest/01-dispatched-task",
                 "--kind", "semantic-dependency", "--why", "consumes the flag the first one adds"))
    ok(run_queue("attach", "selftest/01-dispatched-task", S1))


def answer(text: str) -> str:
    """A tool that prints `text` whatever it is asked."""
    return f"import sys\nsys.stdout.write({text!r})\n"


def quota_axi(auth: dict, fetch: dict) -> str:
    """quota-axi: the credential read under `auth`, the reading under anything else."""
    return (
        "import sys\n"
        f"AUTH = {json.dumps(auth)!r}\n"
        f"FETCH = {json.dumps(fetch)!r}\n"
        "sys.stdout.write(AUTH if sys.argv[1:2] == ['auth'] else FETCH)\n"
    )


def auth(*providers: tuple[str, str], at: str = "2026-03-15T16:42:00.000Z") -> dict:
    return {"generatedAt": at, "schemaVersion": 1, "auth": [
        {"provider": p, "sources": [{"source": "oauth-file", "status": status}]} for p, status in providers
    ]}


def fetch(*providers: dict, at: str = "2026-03-15T16:42:00.000Z") -> dict:
    return {"generatedAt": at, "schemaVersion": 5, "providers": list(providers)}


SESSIONS = [
    {"id": S1, "name": "A dispatched task", "state": "uncovered", "state_source": "process",
     "hook_state_age_secs": None, "stopped": False, "hook_reported": False},
    {"id": "22222222-2222-2222-2222-222222222222", "name": "Someone else's session", "state": "unreported",
     "state_source": "process", "hook_state_age_secs": None, "stopped": False, "hook_reported": False},
]

PRS = [{"number": 13, "url": "https://github.com/Thurbeen/fleet/pull/13", "title": "Add one-call fleet status",
        "headRefName": "t/dispatched", "state": "OPEN",
        "statusCheckRollup": [{"__typename": "CheckRun", "name": "gate", "status": "COMPLETED",
                               "conclusion": "SUCCESS"}]}]


def claude(weekly: int) -> dict:
    """Three windows that reset independently, measured and projected."""
    return {
        "provider": "claude", "plan": "max", "source": "oauth",
        "windows": [
            {"id": "five_hour", "label": "session", "kind": "session", "percentRemaining": 90,
             "resetsAt": "2026-03-15T20:10:48.000Z",
             "pace": {"status": "behind", "reservePercentPoints": 12.4, "burnMultiple": 0.5921,
                      "projectedExhaustedAt": "2026-03-15T18:02:11.000Z"}},
            {"id": "seven_day", "label": "week", "kind": "weekly", "percentRemaining": weekly,
             "resetsAt": "2026-03-20T17:59:45.600Z",
             "pace": {"status": "ahead", "reservePercentPoints": -8.2, "burnMultiple": 1.295,
                      "projectedExhaustedAt": "2026-03-19T03:43:45.600Z"}},
            {"id": "model:fable", "label": "Fable week", "kind": "model", "percentRemaining": 100,
             "resetsAt": "2026-03-20T08:25:12.000Z"},
        ],
        "state": {"status": "fresh", "stale": False},
        "quotaSemantics": {"status": "known", "effectiveAvailability": [
            {"scope": "all_models", "status": "known", "effectivePercentRemaining": weekly,
             "boundedBy": ["five_hour", "seven_day"], "limitingWindowIds": ["seven_day"],
             "runway": {"status": "projected_exhaustion", "usableRunwaySeconds": 298906,
                        "projectedExhaustedAt": "2026-03-19T03:43:45.600Z",
                        "limitingWindowId": "seven_day", "projectionConfidence": "established"}}]},
    }


def fuel_of(weekly: int) -> str:
    """This machine has exactly one credential, which keeps every assertion about one reading."""
    return quota_axi(auth(("claude", "available"), ("codex", "missing")), fetch(claude(weekly)))


# A rate-limited fetch: an EMPTY quota reading per scope, while the numbers
# survive in windows[] from cache. A degraded reading, not zero fuel.
STALE = quota_axi(auth(("claude", "available"), at="2026-09-08T21:29:21.000Z"), fetch({
    "provider": "claude", "plan": "max", "source": "cache",
    "windows": [
        {"id": "five_hour", "label": "session", "kind": "session", "percentRemaining": 90,
         "resetsAt": "2026-09-09T02:09:59.840656+00:00", "pace": {"status": "unknown", "reason": "stale"}},
        {"id": "seven_day", "label": "week", "kind": "weekly", "percentRemaining": 74,
         "resetsAt": "2026-09-14T23:59:59.840676+00:00", "pace": {"status": "unknown", "reason": "stale"}},
        {"id": "model:fable", "label": "Fable week", "kind": "model", "percentRemaining": 100,
         "resetsAt": "2026-09-15T00:00:00+00:00", "pace": {"status": "unknown", "reason": "stale"}},
    ],
    "state": {"status": "stale", "stale": True, "refreshedAt": "2026-09-08T21:28:34.926Z",
              "error": "Claude quota endpoint rate limited retry after 2026-09-08T21:33:47.413Z"},
    "quotaSemantics": {"status": "unknown", "effectiveAvailability": [
        {"scope": "all_models", "status": "unknown", "boundedBy": ["five_hour", "seven_day"]}]},
}, at="2026-09-08T21:29:21.000Z"))

# A provider with no window at all.
MUTE = quota_axi(auth(("claude", "available")), fetch({
    "provider": "claude", "windows": [],
    "state": {"status": "auth_required", "stale": False, "error": "Claude sign-in required",
              "reason": "credentials_missing"},
    "quotaSemantics": {"status": "unknown", "effectiveAvailability": []},
}))


def window(wid: str, pct: int, resets: str, label: str = "5h") -> dict:
    return {"id": wid, "label": label, "percentRemaining": pct, "resetsAt": resets}


# Several subscriptions, one failing, and two providers with no credential.
MANY = quota_axi(
    {"generatedAt": "2026-03-15T16:42:00.000Z", "schemaVersion": 1, "auth": [
        {"provider": "claude", "sources": [{"source": "oauth-file", "status": "available"}]},
        {"provider": "codex", "sources": [{"source": "auth-json", "status": "expired"},
                                          {"source": "cli-rpc", "status": "available"}]},
        {"provider": "cursor", "sources": [{"source": "cli-authfile", "status": "missing"}]},
        {"provider": "zai", "sources": [{"source": "opencode:auth.json", "status": "available"}]},
        {"provider": "grok", "sources": [{"source": "auth-json", "status": "missing"}]},
    ]},
    fetch(
        {"provider": "claude", "plan": "max", "source": "oauth",
         "windows": [window("five_hour", 90, "2026-03-15T20:10:48.000Z", "session"),
                     window("seven_day", 64, "2026-03-20T17:59:45.600Z", "week")],
         "state": {"status": "fresh", "stale": False}},
        {"provider": "codex", "windows": [],
         "state": {"status": "auth_required", "stale": False, "error": "Codex sign-in required"}},
        {"provider": "zai", "windows": [window("monthly", 7, "2026-04-01T00:00:00.000Z", "month")],
         "state": {"status": "fresh", "stale": False}},
    ),
)

# Two subscriptions under names this repo never chose.
SEAM = quota_axi(
    auth(("zai", "available"), ("nova", "available")),
    fetch(
        {"provider": "zai", "plan": "pro", "source": "oauth",
         "windows": [window("five_hour", 61, "2026-03-15T21:00:00.000Z")], "state": {"status": "ok", "stale": False}},
        {"provider": "nova", "plan": "team", "source": "oauth",
         "windows": [window("five_hour", 12, "2026-03-15T22:00:00.000Z")], "state": {"status": "ok", "stale": False}},
    ),
)

# The sole credential, under a name the module has never heard of.
GLORBNAK = quota_axi(
    auth(("glorbnak", "available")),
    fetch({"provider": "glorbnak", "plan": "pro", "source": "oauth",
           "windows": [window("five_hour", 48, "2026-03-15T21:00:00.000Z")],
           "state": {"status": "ok", "stale": False}}),
)


def records(text: str) -> list[dict]:
    """`--fuel`'s blank-line-separated `name<TAB>value` records."""
    return [
        dict(line.split("\t", 1) for line in block.splitlines() if "\t" in line)
        for block in text.split("\n\n") if block.strip()
    ]
