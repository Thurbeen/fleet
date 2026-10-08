"""`fleet peers`: every other fleet's queue, read and never written.

The board's peer view stands on one command, so this drives that command end
to end against the stub `ssh` and `thurbox-cli`: fleets thurbox already lists,
one more the operator declares, a host that answers, one that is down and one
that answers too late. What is asserted is what the board draws from — the
JSON and the `--records` wire — and that nothing on a peer moved.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import yaml

from harness import REPO, Stubs, run_fleet, write

HOSTS_TOML = """\
[[hosts]]
name = "devbox"
destination = "me@devbox"

[[hosts]]
name = "downbox"
destination = "me@downbox"

[[hosts]]
name = "slowbox"
destination = "me@slowbox"
"""

PEER_PROBE = (
    "R\t/srv/fleet/orchestration/queue\nH\tticking\t1999999000\n"
    "T\tremote-topic\tRemote topic\n"
    "K\t01-far\tdispatched\tFar away task\t\t\t\t1\t0\t0\tfeat/far\t1999999000\tpr\t\t0\n"
    "B\tremote-topic/01-far\tclaude\t\tworker-far\t\t\t\nA\t0\n"
)


def lead(sid: str, cwd: str, backend: str, name: str = "📡 Mission Control") -> dict:
    return {"id": sid, "name": name, "cwd": cwd, "backend_type": backend, "state": "idle", "worktrees": []}


def task(root: Path, topic: str, tid: str, state: str, title: str) -> Path:
    write(root / topic / "topic.yaml", f"title: {topic.title()}\n")
    path = root / topic / tid / "task.yaml"
    write(path, yaml.safe_dump({"id": tid, "state": state, "title": title, "agent": "codex"}))
    return path


def peer_checkout(where: Path) -> Path:
    """A second fleet on this machine: its code, and a queue of its own."""
    shutil.copytree(REPO / "scripts" / "lib", where / "scripts" / "lib",
                    ignore=shutil.ignore_patterns("__pycache__"))
    task(where / "orchestration" / "queue", "local-topic", "01-near", "queued", "Near task")
    return where


def machine(stubs: Stubs, tmp_path: Path, sessions: list[dict]) -> None:
    write(stubs.root / "hosts.toml", HOSTS_TOML)
    write(stubs.root / "session-list.json", json.dumps(sessions))
    state = stubs.root / "ssh-state"
    write(state / "me@devbox.pane-probe", PEER_PROBE)
    write(state / "me@slowbox.pane-probe", PEER_PROBE)
    write(state / "me@slowbox.slow", "6")
    write(state / "me@downbox.down", "")


def peers(*args: str):
    done = run_fleet("peers", *args)
    assert done.code == 0, done.out
    return done


def fleets(doc: dict) -> dict:
    return {f["key"]: f for host in doc["hosts"] for f in host["fleets"]}


def test_one_peer_ok_one_unreachable_one_too_slow(stubs, tmp_path):
    own = task(Path(os.environ["FLEET_QUEUE_DIR"]), "home-topic", "01-here", "queued", "Here task")
    peer = peer_checkout(tmp_path / "peer-fleet")
    machine(stubs, tmp_path, [
        lead("self", str(REPO), "local-tmux"),
        lead("near", str(peer), "local-tmux", "📡 Mission Control · near"),
        lead("far", "/srv/fleet", "ssh:devbox"),
        lead("gone", "/srv/fleet", "ssh:downbox:tmux"),
        lead("late", "/srv/fleet", "ssh:slowbox"),
        {"id": "w", "name": "🚀 Rename Mission Control", "cwd": "/x", "backend_type": "local-tmux"},
    ])
    peer_before = (peer / "orchestration/queue/local-topic/01-near/task.yaml").read_bytes()
    own_before = own.read_bytes()

    started = time.monotonic()
    doc = json.loads(peers("--json", "--timeout", "2").stdout)
    took = time.monotonic() - started

    found = fleets(doc)
    assert sorted(h["host"] for h in doc["hosts"]) == ["devbox", "downbox", "local", "slowbox"]
    assert set(found) == {"local:" + str(REPO), "local:" + str(peer), "devbox:/srv/fleet",
                          "downbox:/srv/fleet", "slowbox:/srv/fleet"}, "a worker was read as a lead"

    me = found["local:" + str(REPO)]
    assert me["self"] and me["status"] == "ok"
    assert [t["ref"] for t in me["tasks"]] == ["home-topic/01-here"]

    near = found["local:" + str(peer)]
    assert near["status"] == "ok" and near["fleet"] == "near" and near["source"] == "thurbox"
    assert [(t["ref"], t["state"], t["fleet"], t["host"]) for t in near["tasks"]] == [
        ("local-topic/01-near", "queued", "near", "local")]

    far = found["devbox:/srv/fleet"]
    assert far["status"] == "ok", far
    assert far["tasks"][0]["title"] == "Far away task" and far["tasks"][0]["host"] == "devbox"
    assert far["tasks"][0]["session"] == "worker-far" and far["tasks"][0]["agent"] == "claude"

    gone = found["downbox:/srv/fleet"]
    assert gone["status"] == "unreachable" and "No route to host" in gone["reason"] and gone["tasks"] == []

    late = found["slowbox:/srv/fleet"]
    assert late["status"] == "unreachable" and "2s" in late["reason"], late

    # Every peer is asked at once: three hosts and a local fleet in one
    # timeout, not one after another.
    assert took < 5.5, f"peers were read one after another: {took:.1f}s"
    assert (peer / "orchestration/queue/local-topic/01-near/task.yaml").read_bytes() == peer_before
    assert own.read_bytes() == own_before
    assert stubs.calls("thurbox-cli", "session") == ["thurbox-cli session list --json"]
    for call in stubs.calls("ssh"):
        assert "pane_probe.py" in call, f"a peer was asked something other than to be read: {call}"


def test_the_board_wire_carries_peers_and_never_this_fleet(stubs, tmp_path):
    task(Path(os.environ["FLEET_QUEUE_DIR"]), "home-topic", "01-here", "queued", "Here task")
    machine(stubs, tmp_path, [lead("self", str(REPO), "local-tmux"), lead("far", "/srv/fleet", "ssh:devbox")])
    lines = peers("--records").stdout.splitlines()
    assert lines[0].split("\t")[:6] == ["P", "devbox:/srv/fleet", "devbox", "devbox/fleet", "ok", "0"]
    assert "K\t01-far\tdispatched\tFar away task" in "\n".join(lines)
    assert not any("Here task" in line for line in lines), "this fleet rode the peer wire twice"


def test_a_peer_that_stops_answering_is_stale_and_keeps_its_last_reading(stubs, tmp_path):
    machine(stubs, tmp_path, [lead("self", str(REPO), "local-tmux"), lead("far", "/srv/fleet", "ssh:devbox")])
    assert fleets(json.loads(peers("--json").stdout))["devbox:/srv/fleet"]["status"] == "ok"

    write(stubs.root / "ssh-state" / "me@devbox.down", "")
    cached = fleets(json.loads(peers("--json").stdout))["devbox:/srv/fleet"]
    assert cached["status"] == "ok", "a fresh reading was not reused inside its TTL"
    assert len(stubs.calls("ssh")) == 1, "the TTL did not spare the host a second ssh"

    far = fleets(json.loads(peers("--json", "--refresh").stdout))["devbox:/srv/fleet"]
    assert far["status"] == "stale", far
    assert "No route to host" in far["reason"] and far["age"] >= 0
    assert [t["ref"] for t in far["tasks"]] == ["remote-topic/01-far"], "a stale peer blanked its tasks"
    line = peers("--records").stdout.splitlines()[0].split("\t")
    assert line[4] == "stale"


def test_thurbox_failing_to_answer_keeps_every_peer_it_found_before(stubs, tmp_path):
    machine(stubs, tmp_path, [lead("self", str(REPO), "local-tmux"), lead("far", "/srv/fleet", "ssh:devbox")])
    assert fleets(json.loads(peers("--json").stdout))["devbox:/srv/fleet"]["status"] == "ok"
    stubs.tool("thurbox-cli", "import sys\nsys.exit(1)\n")
    write(stubs.root / "ssh-state" / "me@devbox.down", "")
    far = fleets(json.loads(peers("--json", "--refresh").stdout)).get("devbox:/srv/fleet")
    assert far, "a session list that failed once forgot every peer it had found"
    assert far["status"] == "stale" and [t["ref"] for t in far["tasks"]] == ["remote-topic/01-far"]


def test_no_peers_is_this_fleet_alone_and_an_empty_wire(stubs, tmp_path):
    machine(stubs, tmp_path, [lead("self", str(REPO), "local-tmux")])
    doc = json.loads(peers("--json").stdout)
    assert [f["key"] for f in fleets(doc).values()] == ["local:" + str(REPO)]
    assert peers("--records").stdout == ""
    assert stubs.calls("ssh") == []


def test_the_operator_setting_adds_a_peer_and_can_turn_discovery_off(stubs, tmp_path):
    machine(stubs, tmp_path, [lead("self", str(REPO), "local-tmux"), lead("far", "/srv/fleet", "ssh:devbox")])
    settings = Path(os.environ["FLEET_PEERS_ROOT"]) / "orchestration"
    write(settings / "peers.conf", "DISCOVER=off\nPEER=slowbox /srv/fleet late\n")
    write(stubs.root / "ssh-state" / "me@slowbox.slow", "0")
    found = fleets(json.loads(peers("--json").stdout))
    assert "devbox:/srv/fleet" not in found, "DISCOVER=off still read thurbox's leads"
    late = found["slowbox:/srv/fleet"]
    assert late["status"] == "ok" and late["fleet"] == "late" and late["source"] == "config"


def test_two_unnamed_fleets_in_folders_of_one_name_keep_two_names(stubs, tmp_path):
    machine(stubs, tmp_path, [lead("self", str(REPO), "local-tmux"), lead("a", "/srv/fleet", "ssh:devbox"),
                              lead("b", "/opt/fleet", "ssh:devbox")])
    found = fleets(json.loads(peers("--json").stdout))
    assert found["local:" + str(REPO)]["label"] == "local/this"
    assert found["devbox:/srv/fleet"]["label"] != found["devbox:/opt/fleet"]["label"]


def test_an_unknown_host_is_unreachable_and_not_a_crash(stubs, tmp_path):
    machine(stubs, tmp_path, [lead("self", str(REPO), "local-tmux"), lead("x", "/srv/fleet", "ssh:nobox")])
    gone = fleets(json.loads(peers("--json").stdout))["nobox:/srv/fleet"]
    assert gone["status"] == "unreachable" and "nobox" in gone["reason"]


def test_the_tracked_example_names_no_peer():
    text = (REPO / "orchestration" / "peers.example.conf").read_text(encoding="utf-8")
    live = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert all(not line.startswith("PEER=") or line == "PEER=" for line in live), live
