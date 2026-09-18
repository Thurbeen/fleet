"""What `refuel` reads, written as the files it reads them from.

The account's quota window (`quota-axi`), a session's pane (`session capture`),
and an agent's own transcript: three sources, each a fixture here, so a test
says "spent", "a limit banner" or "a rejected turn" without an account, a
network or a real agent.
"""

import datetime
import json
from pathlib import Path

import yaml
from queuekit import ok

from harness import Stubs, write
from harness import run_queue as q

# Observed on a real pane, 2026-09-08: Claude Code prints its limit as one line
# in the transcript view and then stops. Nothing else in `session get` changes.
CLAUDE_BANNER = "You've hit your session limit · resets 11:30pm (Europe/Paris)"


def quota_is(stubs: Stubs, percent: int, resets_at: str, provider: str = "claude") -> None:
    """quota-axi's schemaVersion 5, trimmed to the fields read, for the five-hour window."""
    write(stubs.root / "quota.json", json.dumps({
        "generatedAt": "2026-09-08T21:18:52.142Z", "schemaVersion": 5,
        "providers": [{
            "provider": provider, "plan": "max",
            "windows": [
                {"id": "five_hour", "label": "session", "kind": "session",
                 "resetsAt": resets_at, "percentRemaining": percent},
                {"id": "seven_day", "label": "week", "kind": "weekly",
                 "resetsAt": "2026-09-15T00:00:00+00:00", "percentRemaining": 75},
            ],
            "state": {"status": "fresh", "stale": False},
            "quotaSemantics": {"status": "known", "effectiveAvailability": [
                {"scope": "all_models", "status": "known", "effectivePercentRemaining": percent,
                 "boundedBy": ["five_hour", "seven_day"], "limitingWindowIds": ["five_hour"]},
            ]},
        }],
    }) + "\n")


def no_quota(stubs: Stubs) -> None:
    """quota-axi failing the way an expired credential fails."""
    (stubs.root / "quota.json").unlink(missing_ok=True)


def pane(stubs: Stubs, sid: str, text: str | None) -> None:
    """What `session capture` shows for this session; None clears it."""
    path = stubs.root / "panes" / f"{sid}.txt"
    if text is None:
        path.unlink(missing_ok=True)
    else:
        write(path, text)


def transcript(path: Path, window: str, banner: str) -> None:
    """The shape Claude Code writes when the window rejects a request: a synthetic
    assistant turn carrying `error`, `apiErrorStatus` and the window that did it."""
    rows = [
        {"type": "user", "timestamp": "2026-09-08T18:00:00.000Z",
         "message": {"role": "user", "content": "Read /brief and do what it says."}},
        {"type": "assistant", "timestamp": "2026-09-08T19:56:38.987Z",
         "isApiErrorMessage": True, "error": "rate_limit", "apiErrorStatus": 429,
         "quotaLimits": {"status": "rejected", "resetsAt": 1788999000, "rateLimitType": window},
         "message": {"role": "assistant", "model": "<synthetic>",
                     "content": [{"type": "text", "text": banner}]}},
        {"type": "last-prompt"},
    ]
    write(path, "".join(json.dumps(row) + "\n" for row in rows))


def attach_task(topic: str, slug: str, number: str, sid: str, branch: str | None = None) -> None:
    ok(q("add", topic, slug, "--title", f"Task {slug}", "--repo", "/tmp/repo-a",
         "--branch", branch or f"fix/{slug}", "--number", number))
    ok(q("attach", f"{topic}/{number}-{slug}", sid))


def restarts(stubs: Stubs, sid: str = "") -> list[str]:
    return [c for c in stubs.calls("thurbox-cli", "session restart") if sid in c]


def sends(stubs: Stubs) -> str:
    return "\n".join(stubs.calls("thurbox-cli", "session send"))


def rewind_refuels(task_yaml: Path, hours: int = 3) -> None:
    """Move every restart receipt back: a session that came up, worked, and ran dry again."""
    doc = yaml.safe_load(task_yaml.read_text(encoding="utf-8"))
    for rec in doc.get("refuels") or []:
        rec["at"] = (datetime.datetime.fromisoformat(rec["at"]) - datetime.timedelta(hours=hours)).isoformat()
    write(task_yaml, yaml.safe_dump(doc))


def one_refuel_just_now(task_yaml: Path) -> None:
    """One receipt, stamped where refuel itself would have stamped it: just now."""
    doc = yaml.safe_load(task_yaml.read_text(encoding="utf-8"))
    doc["refuels"] = doc["refuels"][:1]
    doc["refuels"][0]["at"] = datetime.datetime.now(datetime.UTC).isoformat()
    write(task_yaml, yaml.safe_dump(doc))


def teach(isolated_env: Path, *lines: str) -> None:
    """Add settings lines to the agent conf this test's fleet reads.

    The file `tests/queue/conftest.py` already wrote, appended to — so a test
    can say one thing about one agent without restating the checkout's own
    answers, which is exactly the shape an operator's edit has.
    """
    conf = isolated_env / "settings" / "orchestration" / "agent.conf"
    write(conf, conf.read_text(encoding="utf-8") + "".join(line + "\n" for line in lines))


# `quota-axi` answering PER ACCOUNT: the document it reads is named after the
# config directory it was run under, so a reading taken with the wrong
# environment finds no document and fails the way a missing credential fails.
# It is the only way to prove the environment reached the tool.
ACCOUNT_QUOTA = '''
import os
import sys
from pathlib import Path

root = Path(os.environ["FLEET_STUB_ROOT"])
account = Path(os.environ.get("CLAUDE_CONFIG_DIR", "")).name or "default"
doc = root / f"quota-{account}.json"
if not doc.is_file():
    sys.stderr.write(f"quota-axi: no credentials for the {account} account\\n")
    raise SystemExit(1)
sys.stdout.write(doc.read_text(encoding="utf-8"))
'''


def account_quota(stubs: Stubs, account: str, percent: int, resets_at: str,
                  provider: str = "claude") -> None:
    """One account's window, keyed by the config directory that selects it."""
    quota_is(stubs, percent, resets_at, provider)
    (stubs.root / "quota.json").replace(stubs.root / f"quota-{account}.json")
