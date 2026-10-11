"""`fleet context repo <repo> --peers`: what every other fleet knows about one repository, read and never written.

Driven end to end: a second fleet on this machine, whose own copy of fleet
answers the read, and a fleet on a host behind the stub `ssh`, which answers
with what a real peer printed. Asserted is what the reader gets — the TOON,
each fleet's status and provenance — and what a peer must never give away:
its prompts, its operator's rules, its run logs, its narrative, and anything
at all once its operator turned sharing off.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import yaml
from contextkit import checkout, context, parse_toon, plant

from harness import REPO, Stubs, write

REPO_ID = "github.com/acme/app"
TWIN_ID = "gitlab.example.com/acme/app"

HOSTS_TOML = """\
[[hosts]]
name = "devbox"
destination = "me@devbox"

[[hosts]]
name = "downbox"
destination = "me@downbox"
"""

# Words planted in everything a peer must never serve. None may reach the reader.
SECRETS = {
    "PROMPT.md": "SECRET-PROMPT-WORDS",
    "BRIEF.md": "SECRET-BRIEF-WORDS",
    "OPERATOR.md": "SECRET-OPERATOR-RULE",
    "run log": "SECRET-RUN-LOG-PROSE",
    "narrative": "SECRET-NARRATIVE",
    "result body": "SECRET-RESULT-DETAIL",
}


def lead(sid: str, cwd: str, backend: str, name: str = "📡 Mission Control") -> dict:
    return {"id": sid, "name": name, "cwd": cwd, "backend_type": backend, "state": "idle", "worktrees": []}


def peer_fact(root: Path, repo: str, fid: str, text: str, at: str = "2026-09-12T00:00:00Z") -> Path:
    path = root / "registry" / "facts" / repo / f"{fid}.md"
    write(path, "---\n" + yaml.safe_dump({"id": fid, "repo": repo, "at": at, "source": "app-ci/02-fix"},
                                         sort_keys=False) + "---\n" + text + "\n")
    return path


def peer_checkout(where: Path, tmp_path: Path, share: str | None = None) -> Path:
    """A second fleet: its own code, facts, a finished task, and every private file a fleet keeps."""
    shutil.copytree(REPO / "scripts" / "lib", where / "scripts" / "lib",
                    ignore=shutil.ignore_patterns("__pycache__"))
    peer_fact(where, REPO_ID, "20260912-app-ci-02-1", "Release builds need the vendored feature on Windows.")
    peer_fact(where, TWIN_ID, "20260913-other-01-1", "The GitLab twin deploys from tags only.")
    app = checkout(tmp_path / "peer-code", "app", "git@github.com:acme/app.git")
    queue = where / "orchestration" / "queue"
    write(queue / "OPERATOR.md", SECRETS["OPERATOR.md"] + "\n")
    write(queue / "app-ci" / "topic.yaml", "title: App CI\ncreated: '2026-09-12T00:00:00+00:00'\n")
    write(queue / "app-ci" / "PROMPT.md", SECRETS["PROMPT.md"] + "\n")
    task = queue / "app-ci" / "02-fix"
    write(task / "task.yaml", yaml.safe_dump({
        "id": "02-fix", "topic": "app-ci", "title": "Fix Windows", "state": "landed", "repo": str(app),
        "branch": "fix/win", "created": "2026-09-12T00:00:00+00:00",
        "concluded_at": "2026-09-12T10:00:00+00:00", "outcome": "shipped"}))
    write(task / "BRIEF.md", SECRETS["BRIEF.md"] + "\n")
    write(task / "result.md", "---\noutcome: shipped\n---\nFixed the Windows release build.\n\n"
          + SECRETS["result body"] + "\n")
    write(where / "orchestration" / "runs" / "2026-09-12-app-ci.md", SECRETS["run log"] + "\n")
    write(where / "registry" / "context" / "app.md", SECRETS["narrative"] + "\n")
    if share is not None:
        write(where / "orchestration" / "fleet.conf", f"SHARE={share}\n")
    return where


def snapshot(root: Path) -> dict:
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def machine(stubs: Stubs, sessions: list[dict]) -> None:
    write(stubs.root / "hosts.toml", HOSTS_TOML)
    write(stubs.root / "session-list.json", json.dumps(sessions))


def wire_of(peer: Path, *args: str) -> str:
    """What a fleet prints to a peer's read, by running that fleet's own copy of context.py."""
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if not k.startswith("FLEET_")}
    done = subprocess.run([sys.executable, str(peer / "scripts" / "lib" / "context.py"), "repo", *args],
                          cwd=peer, env=env, capture_output=True, text=True, encoding="utf-8")
    return done.stdout


def fleets(doc: dict) -> dict:
    return {row["fleet"]: row for row in doc.get("fleets", [])}


def assert_private(out: str) -> None:
    for what, word in SECRETS.items():
        assert word not in out, f"a peer served its {what}:\n{out}"


def test_a_second_fleet_on_this_machine_shares_its_facts_and_nothing_private(stubs, tmp_path):
    plant(REPO_ID, "20261001-aaaaaaaa", "Our own fact about the app.")
    peer = peer_checkout(tmp_path / "near-fleet", tmp_path)
    machine(stubs, [lead("self", str(REPO), "local-tmux"),
                    lead("near", str(peer), "local-tmux", "📡 Mission Control · near")])
    before = snapshot(peer)

    done = context("repo", REPO_ID, "--peers")
    assert done.code == 0, done.out
    doc = parse_toon(done.stdout)
    assert done.stdout.splitlines()[-1].startswith("help[")
    assert [f["id"] for f in doc["live"]] == ["20261001-aaaaaaaa"], "this fleet's own facts went missing"
    near = fleets(doc)["local/near"]
    assert near["status"] == "ok" and near["facts"] == "1", near
    rows = doc["peer_facts"]
    assert [(r["fleet"], r["id"], r["date"]) for r in rows] == [("local/near", "20260912-app-ci-02-1", "2026-09-12")]
    assert "vendored" in rows[0]["text"]
    assert "peer_results" not in doc, "results were served without the peer opting in"
    assert "GitLab twin" not in done.stdout, "a same-named repository on another forge was read as this one"
    assert_private(done.stdout)
    assert snapshot(peer) == before, "reading a peer wrote into it"


def test_a_peer_that_turned_sharing_off_refuses_and_serves_nothing(stubs, tmp_path):
    peer = peer_checkout(tmp_path / "near-fleet", tmp_path, share="none")
    machine(stubs, [lead("self", str(REPO), "local-tmux"),
                    lead("near", str(peer), "local-tmux", "📡 Mission Control · near")])

    raw = wire_of(peer, REPO_ID, "--wire", "json")
    answer = json.loads(raw)
    assert answer.get("refused") and not answer.get("facts") and not answer.get("results"), raw
    assert "vendored" not in raw

    done = context("repo", REPO_ID, "--peers")
    assert done.code == 0, done.out
    doc = parse_toon(done.stdout)
    assert fleets(doc)["local/near"]["status"] == "refused", done.stdout
    assert "vendored" not in done.stdout and "peer_facts" not in doc
    assert_private(done.stdout)


def test_result_summaries_are_served_only_when_the_peer_opts_in(stubs, tmp_path):
    peer = peer_checkout(tmp_path / "near-fleet", tmp_path, share="facts,results")
    machine(stubs, [lead("self", str(REPO), "local-tmux"),
                    lead("near", str(peer), "local-tmux", "📡 Mission Control · near")])
    done = context("repo", REPO_ID, "--peers")
    assert done.code == 0, done.out
    doc = parse_toon(done.stdout)
    [row] = doc["peer_results"]
    assert row["fleet"] == "local/near" and row["task"] == "app-ci/02-fix" and row["state"] == "landed"
    assert row["summary"] == "Fixed the Windows release build."
    assert_private(done.stdout)


def test_a_fleet_on_a_host_is_read_over_ssh_then_stale_from_the_cache(stubs, tmp_path):
    far = peer_checkout(tmp_path / "far-fleet", tmp_path)
    write(stubs.root / "ssh-state" / "me@devbox.context", wire_of(far, REPO_ID, "--wire", "json"))
    write(stubs.root / "ssh-state" / "me@downbox.down", "")
    machine(stubs, [lead("self", str(REPO), "local-tmux"), lead("far", "/srv/fleet", "ssh:devbox"),
                    lead("gone", "/srv/fleet", "ssh:downbox")])

    doc = parse_toon(context("repo", REPO_ID, "--peers", "--timeout", "5").stdout)
    found = fleets(doc)
    assert found["devbox/fleet"]["status"] == "ok", found
    assert found["downbox/fleet"]["status"] == "unreachable" and "No route" in found["downbox/fleet"]["reason"]
    assert [(r["fleet"], r["id"]) for r in doc["peer_facts"]] == [("devbox/fleet", "20260912-app-ci-02-1")]
    calls = stubs.calls("ssh")
    asked = [c for c in calls if "me@devbox" in c]
    assert asked and all("context.py" in c and REPO_ID in c and "--wire json" in c for c in asked), asked
    for call in calls:
        assert "context.py" in call, f"a peer was asked something other than to be read: {call}"

    write(stubs.root / "ssh-state" / "me@devbox.down", "")
    doc = parse_toon(context("repo", REPO_ID, "--peers", "--refresh").stdout)
    far_row = fleets(doc)["devbox/fleet"]
    assert far_row["status"] == "stale" and int(far_row["age"]) >= 0, far_row
    assert [r["id"] for r in doc["peer_facts"]] == ["20260912-app-ci-02-1"], "a stale peer blanked its facts"


def test_a_peer_answer_that_breaks_the_wire_is_rejected_not_shown(stubs, tmp_path):
    good = {"wire": "fleet-context/1", "repo": REPO_ID, "share": ["facts"], "results": [],
            "facts": [{"id": "20260912-x-1", "at": "2026-09-12T00:00:00Z", "source": "a/b", "text": "ok"}]}
    bad = {
        "devbox": "login banner and no json at all " + "x" * 400 + "\n",
        "otherbox": json.dumps({**good, "repo": TWIN_ID}) + "\n",
        "bigbox": json.dumps({**good, "facts": [dict(good["facts"][0], id=f"20260912-x-{n}")
                                                for n in range(500)]}) + "\n",
        "idbox": json.dumps({**good, "facts": [dict(good["facts"][0], id="../../etc/passwd")]}) + "\n",
    }
    hosts = "".join(f'[[hosts]]\nname = "{h}"\ndestination = "me@{h}"\n\n' for h in bad)
    sessions = [lead("self", str(REPO), "local-tmux")]
    for host, answer in bad.items():
        write(stubs.root / "ssh-state" / f"me@{host}.context", answer)
        sessions.append(lead(host, "/srv/fleet", f"ssh:{host}"))
    write(stubs.root / "hosts.toml", hosts)
    write(stubs.root / "session-list.json", json.dumps(sessions))

    done = context("repo", REPO_ID, "--peers")
    assert done.code == 0, done.out
    doc = parse_toon(done.stdout)
    for host in bad:
        assert fleets(doc)[f"{host}/fleet"]["status"] == "unreachable", (host, done.stdout)
    assert "peer_facts" not in doc, done.stdout
    assert "passwd" not in done.stdout
    assert all(len(row["reason"]) <= 160 for row in doc["fleets"]), "a peer's error ran past its cap"


def test_two_fleets_of_one_name_stay_two_fleets(stubs, tmp_path):
    one = peer_checkout(tmp_path / "a" / "fleet", tmp_path / "a")
    two = peer_checkout(tmp_path / "b" / "fleet", tmp_path / "b")
    machine(stubs, [lead("self", str(REPO), "local-tmux"), lead("a", str(one), "local-tmux"),
                    lead("b", str(two), "local-tmux")])
    doc = parse_toon(context("repo", REPO_ID, "--peers").stdout)
    labels = sorted(fleets(doc))
    assert len(labels) == 2 and labels[0] != labels[1], labels
    assert sorted(r["fleet"] for r in doc["peer_facts"]) == labels, "two fleets' facts were merged under one name"


def test_no_peer_is_an_explicit_empty_state(stubs, tmp_path):
    machine(stubs, [lead("self", str(REPO), "local-tmux")])
    done = context("repo", REPO_ID, "--peers")
    assert done.code == 0, done.out
    doc = parse_toon(done.stdout)
    assert doc["peers"].startswith("0"), done.stdout
    assert stubs.calls("ssh") == []


def test_the_wire_refuses_what_a_peer_may_not_be_asked(stubs, tmp_path):
    for argv, code in [
        (["repo", "app", "--wire", "json"], 1),                     # a bare name resolves against the PEER's map
        (["repo", str(tmp_path), "--wire", "json"], 1),             # a path is the peer's filesystem
        (["repo", REPO_ID, "--wire", "xml"], 2),
        (["repo", REPO_ID, "--wire", "json", "--peers"], 2),       # a read never fans out again
        (["repo", REPO_ID, "--wire", "json", "--task", "a/b"], 2),  # prior results are not shared
    ]:
        done = context(*argv)
        assert done.code == code, (argv, done.out)
    done = context("repo", REPO_ID, "--wire", "json")
    assert done.code == 0, done.out
    answer = json.loads(done.stdout)
    assert answer["wire"] == "fleet-context/1" and answer["repo"] == REPO_ID and answer["facts"] == []


def test_wire_is_hidden_and_peers_is_documented():
    done = context("repo", "--help")
    assert "--peers" in done.stdout and "--wire" not in done.stdout


def test_the_tracked_example_shares_nothing_beyond_the_default():
    text = (REPO / "orchestration" / "fleet.example.conf").read_text(encoding="utf-8")
    live = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert "SHARE=" in live, "fleet.example.conf does not document SHARE"


def test_a_peer_cannot_put_terminal_or_bidi_controls_into_the_read(stubs, tmp_path):
    """A peer's text reaches a reader's terminal: C1 escapes and bidi overrides are stripped like C0."""
    sneaky = "\u009b31m red ‮evil‬ ⁦x⁩ ‏"
    answer = {"wire": "fleet-context/1", "repo": REPO_ID, "share": ["facts", "results"],
              "facts": [{"id": "20260912-x-1", "at": "2026-09-12T00:00:00Z", "source": "a/b" + sneaky,
                         "text": "fact" + sneaky}],
              "results": [{"task": "app-ci/02-fix", "state": "landed", "date": "2026-09-12",
                           "summary": "done" + sneaky}]}
    write(stubs.root / "ssh-state" / "me@devbox.context", json.dumps(answer) + "\n")
    machine(stubs, [lead("self", str(REPO), "local-tmux"), lead("far", "/srv/fleet", "ssh:devbox")])
    done = context("repo", REPO_ID, "--peers")
    assert done.code == 0, done.out
    doc = parse_toon(done.stdout)
    assert doc["peer_facts"] and doc["peer_results"], done.stdout
    for ch in ("\u009b", "‮", "‬", "⁦", "⁩", "‏"):
        assert ch not in done.stdout, f"U+{ord(ch):04X} reached the reader"
