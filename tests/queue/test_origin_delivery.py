"""Every task has an origin, and one function says where a message for it goes.

`add` inside a thurbox session records that session as `thurbox:<id>` and the
mailbox command that reaches it; outside thurbox both stay empty, and a stated
value always wins. `origin_target` resolves an origin to that session, to an
`owners.conf` rule's session or relay, or to `gone` once thurbox no longer
lists the session. It is driven in a child over the stub `thurbox-cli`, the way
the reconciler would call it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml
from queuekit import ok

from harness import PYTHON, REPO, expect, refute, run, run_queue as q, write

SID = "5b0c7d2e-0000-4000-8000-000000000001"
DEFAULT_REPLY = f"thurbox-cli message send --to {SID} --kind reply --body"

RESOLVE = (
    "import importlib.util, json, sys\n"
    "spec = importlib.util.spec_from_file_location('fleet_queue', sys.argv[1])\n"
    "m = sys.modules[spec.name] = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(m)\n"
    "t = m.origin_target(sys.argv[2])\n"
    "out = None if t is None else {**t._asdict(), 'reachable': t.reachable}\n"
    "if t is not None and t.kind == 'relay':\n"
    "    out['argv'] = m.relay_argv(t, 'hello there')\n"
    "print(json.dumps(out))\n"
)


def topic() -> str:
    return ok(q("topic", "add", "asked", "--title", "Asked", "--prompt", "p")).stdout.strip()


def add(t: str, slug: str, *extra: str, **env) -> str:
    return ok(q("add", t, slug, "--title", slug, "--repo", "/tmp/repo-a", "--branch", f"fix/{slug}",
                "--publish", "none", *extra, **env)).stdout.strip()


def record(queue_dir: Path, ref: str) -> dict:
    return yaml.safe_load((queue_dir / ref / "task.yaml").read_text(encoding="utf-8"))


def conf(text: str) -> None:
    write(Path(os.environ["FLEET_OWNERS_ROOT"]) / "orchestration" / "owners.conf", text)


def listed(stubs, sid: str) -> None:
    write(stubs.root / "sessions" / f"{sid}.json",
          json.dumps({"name": "Somebody", "id": sid, "state": "idle"}) + "\n")


def resolve(origin: str) -> dict | None:
    done = run([*PYTHON, "-c", RESOLVE, str(REPO / "scripts" / "lib" / "queue.py"), origin])
    assert done.code == 0, done.stderr
    return json.loads(done.stdout)


def test_add_inside_a_session_records_it_and_the_reply_that_reaches_it(queue_dir):
    ref = add(topic(), "mine", THURBOX_SESSION=SID)
    doc = record(queue_dir, ref)
    assert (doc["origin"], doc["reply"]) == (f"thurbox:{SID}", DEFAULT_REPLY)
    brief = (queue_dir / ref / "BRIEF.md").read_text(encoding="utf-8")
    expect(brief, "## Replying to the requester", f"`{DEFAULT_REPLY} '<text>'`:",
           f"fleet queue declare {ref}", "needs-input --why '<your question>'",
           "in your own session")
    # The default command takes no file, so the brief does not offer one.
    refute(brief, "--file <path>")


def test_outside_thurbox_both_stay_empty(queue_dir):
    ref = add(topic(), "plain", THURBOX_SESSION=None)
    doc = record(queue_dir, ref)
    assert doc["origin"] is None and doc["reply"] is None
    refute((queue_dir / ref / "BRIEF.md").read_text(encoding="utf-8"), "Replying to the requester")


def test_a_stated_origin_or_reply_wins(queue_dir):
    t = topic()
    stated = add(t, "stated", "--origin", "chat:room/9", THURBOX_SESSION=SID)
    assert (record(queue_dir, stated)["origin"], record(queue_dir, stated)["reply"]) == ("chat:room/9", None)
    replied = add(t, "replied", "--reply", "relay-tool say room/9", THURBOX_SESSION=SID)
    assert (record(queue_dir, replied)["origin"], record(queue_dir, replied)["reply"]) == (
        f"thurbox:{SID}", "relay-tool say room/9")
    brief = (queue_dir / replied / "BRIEF.md").read_text(encoding="utf-8")
    expect(brief, "`relay-tool say room/9 '<text>'` (or `--file <path>`)")
    # A stated session origin gets its default reply even from outside thurbox.
    other = add(t, "other", "--origin", "thurbox:other-id", THURBOX_SESSION=None)
    assert record(queue_dir, other)["reply"] == "thurbox-cli message send --to other-id --kind reply --body"


def test_a_host_task_records_the_origin_and_no_reply_it_could_not_run(queue_dir):
    ok(q("add", topic(), "far", "--title", "far", "--repo", "/srv/repo", "--branch", "fix/far",
         "--publish", "none", "--host", "devbox", THURBOX_SESSION=SID))
    doc = record(queue_dir, "asked/01-far")
    assert (doc["origin"], doc["reply"]) == (f"thurbox:{SID}", None)


def test_a_session_origin_resolves_to_the_session_while_thurbox_lists_it(stubs):
    listed(stubs, SID)
    t = resolve(f"thurbox:{SID}")
    assert (t["kind"], t["value"], t["rule"], t["reachable"]) == ("session", SID, None, True)


def test_a_session_origin_thurbox_no_longer_lists_is_gone(stubs):
    listed(stubs, "someone-else")
    t = resolve(f"thurbox:{SID}")
    assert (t["kind"], t["value"], t["reachable"]) == ("gone", SID, False)


def test_a_session_rule_is_todays_substring_form_unchanged(stubs):
    conf("chat: = Concierge\n")
    t = resolve("chat:room/42")
    assert (t["kind"], t["value"], t["rule"], t["reachable"]) == ("owner", "Concierge", "chat:", True)


def test_a_relay_rule_receives_the_origin_and_the_message(stubs):
    conf("chat: = Concierge\nchat:thread/ = !relay-tool --via 'lead chat'\n")
    t = resolve("chat:thread/7")
    assert (t["kind"], t["value"], t["rule"], t["reachable"]) == (
        "relay", "relay-tool --via 'lead chat'", "chat:thread/", True)
    assert t["argv"] == ["relay-tool", "--via", "lead chat", "chat:thread/7", "hello there"]
    # The shorter rule still owns what the longer one does not name.
    assert resolve("chat:room/1")["kind"] == "owner"


def test_a_rule_routes_even_a_session_origin(stubs):
    conf("thurbox: = !relay-tool\n")
    assert resolve(f"thurbox:{SID}")["kind"] == "relay"


def test_any_other_origin_is_reachable_and_unrouted_and_none_is_none(stubs):
    t = resolve("mail:inbox/7")
    assert (t["kind"], t["reachable"]) == ("unrouted", True)
    assert resolve("") is None


def test_a_relay_rule_without_a_command_is_refused_out_loud(stubs):
    conf("chat: = !\n")
    done = run([*PYTHON, "-c", RESOLVE, str(REPO / "scripts" / "lib" / "queue.py"), "chat:room/1"])
    assert json.loads(done.stdout)["kind"] == "unrouted"
    expect(done.stderr, "ignoring", "!RELAY-COMMAND")
