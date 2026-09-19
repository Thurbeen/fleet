"""Claim 11: a task can name a HOST, and a task that names none does not change.

A remote worker's filesystem is not this one, so the brief has to reach it and
the result has to come back, and none of that may show up in the path a local
task takes. Nothing is spawned on a host until its probes pass; a session whose
host cannot be reached is kept, not reaped — thurbox's CLI still calls it
`idle`, which is the trap. A remote spawn asks for no `--parent`, which thurbox
refuses across hosts. Every command fleet runs on a POSIX host goes through a
LOGIN shell, so a binary only the profile puts on `PATH` is found, while the
two calls that merely move bytes do not. A Windows host is spoken to in
PowerShell throughout, and the bytes survive the trip.

The `ssh` stub is a real fake host: what the push writes, the fetch reads.
"""

import json
import os

import pytest
import yaml
from kit_hosts import Hosts, creates, deletions
from queuekit import ok

from harness import PYTHON, REPO, expect, queue_module, refute, run, write
from harness import run_queue as q

LEAD = "cccccccc-cccc-cccc-cccc-cccccccccccc"
RSESSION = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
WORKTREE = "/srv/worktrees/app-1234/fix-build-on-devbox"
WSESSION = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
WINREPO = "C:\\src\\widget"
WWORKTREE = "C:\\work\\trees\\widget-5678\\fix-build-on-winbox"


@pytest.fixture
def hosts(stubs) -> Hosts:
    return Hosts(stubs)


@pytest.fixture
def rtopic(hosts) -> str:
    return ok(q("topic", "add", "run-somewhere-else", "--title", "Run a task on another machine",
                "--prompt", "fleet should be able to spawn a worker on a remote thurbox host")).stdout.strip()


def hold_open(topic: str, number: str) -> None:
    """A task held on a condition, so the topic is not FINISHED when the remote
    task lands: a finished topic archives itself in that same pass, and `reap`
    loads live topics only, so a session it kept for a host that was down would
    never be looked at again."""
    ok(q("add", topic, "hold-topic-open", "--title", "Hold the topic open", "--repo", "/tmp/repo-a",
         "--branch", "fix/hold-topic-open", "--number", number))
    ok(q("block", f"{topic}/{number}-hold-topic-open", "--condition", "the remote claims have run",
         "--kind", "other", "--why", "keeps this topic live, so reap still sees the remote session"))


def test_the_refusals_that_cost_nothing_happen_at_add_time(rtopic):
    """A name thurbox does not know, a multiplexer fleet has no shell for, and a
    host whose session sharing is off — which is the trust dialog, decided."""
    for host, want in (("nosuch", "no host named"), ("oddbox", "has no shell for"),
                       ("lonebox", "share_sessions = false")):
        r = q("add", rtopic, f"reject-{host}", "--title", f"Reject {host}", "--repo", "/srv/code/app",
              "--host", host, "--branch", "fix/reject")
        assert r.code != 0, f"--host {host} is refused at add time\n{r.out}"
        expect(r.out, want)
    # The refusal for an unknown host names the ones that exist.
    expect(q("add", rtopic, "x", "--title", "x", "--repo", "/srv/code/app", "--host", "nosuch",
             "--branch", "fix/x").out, "devbox")


def test_a_task_with_no_host_dispatches_exactly_as_before(rtopic, stubs, queue_dir):
    ok(q("add", rtopic, "stay-local", "--title", "Stay local", "--repo", "/tmp/repo-a",
         "--branch", "fix/stay-local", "--number", "20"))
    task = queue_dir / rtopic / "20-stay-local"
    brief = (task / "BRIEF.md").read_text(encoding="utf-8")
    # The queue's own result.md, absolutely, and nothing about a host.
    expect(brief, str(task / "result.md"))
    refute(brief, "on host", "in the root of this worktree")
    write(task / "BRIEF.md", "Do the local thing.\n")

    out = q("dispatch", "--dry-run").out
    refute(out, "--host", "ssh <host>")
    expect(out, str(task / "BRIEF.md"))

    # A LOCAL worker is created as a child of the lead's session.
    q("dispatch", THURBOX_SESSION=LEAD)
    spawned = [c for c in creates(stubs) if "--repo-path /tmp/repo-a" in c]
    assert spawned, creates(stubs)
    expect(spawned[-1], f"--parent {LEAD}")


@pytest.fixture
def devbox_task(rtopic, queue_dir) -> str:
    ok(q("add", rtopic, "build-on-devbox", "--title", "Build it on devbox", "--repo", "/srv/code/app",
         "--host", "devbox", "--branch", "fix/build-on-devbox", "--number", "22"))
    return f"{rtopic}/22-build-on-devbox"


def test_a_remote_brief_names_nothing_the_worker_cannot_reach(devbox_task, rtopic, queue_dir):
    """Every control-plane path the brief would name is not on that filesystem,
    so each is stated relative to the brief itself."""
    brief = (queue_dir / devbox_task / "BRIEF.md").read_text(encoding="utf-8")
    expect(brief, "on host `devbox`", "in the root of this worktree",
           "PROMPT.md`, alongside this file", "POLICY.md`, alongside this file",
           "Delete `BRIEF.md`, `POLICY.md`, and `PROMPT.md` before you commit")
    refute(brief, str(queue_dir / devbox_task / "result.md"), str(queue_dir / rtopic / "PROMPT.md"),
           str(REPO / "orchestration" / "queue" / "POLICY.md"))

    expect(q("show", devbox_task).out, "host:        devbox")
    # Spelled host-first, so a remote repo cannot read as local.
    expect(q("list", "--topic", rtopic).out, "devbox:/srv/code/app")
    expect(q("plan").out, "devbox:/srv/code/app")


def test_a_host_that_fails_a_probe_is_not_spawned(devbox_task, hosts, queue_dir):
    """A remote worker that starts and then fails at its first `git` call looks
    like an agent bug and is not one."""
    write(queue_dir / devbox_task / "BRIEF.md", "Build the thing on devbox.\n")
    for flag, probe in (("down", "reachable"), ("nonposix", "posix shell"), ("noforge", "forge"),
                        ("norepo", "repo")):
        hosts.flag("me@devbox", flag)
        out = q("dispatch").out
        hosts.unflag("me@devbox", flag)
        expect(out, "NOT SPAWNED", probe)
        if flag == "norepo":
            # --repo is a path on the host, not here.
            expect(out, "nothing local validates it")
        # Left queued, so fixing the host and re-running sends it.
        expect("\n".join(line for line in q("show", devbox_task).out.splitlines() if "state:" in line), "queued")
    assert creates(hosts.stubs) == []


@pytest.fixture
def devbox_dispatched(devbox_task, rtopic, hosts, queue_dir) -> str:
    hold_open(rtopic, "23")
    write(queue_dir / devbox_task / "BRIEF.md", "Build the thing on devbox.\n")
    hosts.next_session(RSESSION)
    hosts.session(RSESSION, name="Build it on devbox", state="working", agent="claude", hook_reported=True,
                  worktrees=[{"repo_path": "/srv/code/app", "worktree_path": WORKTREE,
                              "branch": "fix/build-on-devbox"}])
    out = q("dispatch", THURBOX_SESSION=LEAD).out
    expect(out, "probe ok", "brief copied to me@devbox")
    return out


def test_a_remote_task_is_spawned_on_its_host_with_its_brief_really_there(
    devbox_dispatched, devbox_task, rtopic, hosts, queue_dir
):
    remote = [c for c in creates(hosts.stubs) if "--host devbox" in c]
    assert remote, creates(hosts.stubs)
    # No --parent: thurbox validates a parent against the host's own backend,
    # and the lead is local by construction.
    refute(remote[-1], "--parent")

    there = hosts.remote("me@devbox", WORKTREE)
    assert (there / "BRIEF.md").is_file(), "the brief really is on the host's filesystem"
    # Records are LF on every OS, and a Windows lead's text-mode stdin would add a CR to every line.
    for name in ("BRIEF.md", "POLICY.md", "PROMPT.md"):
        assert b"\r" not in (there / name).read_bytes(), f"{name} reached a POSIX host with CRLF"
    expect((there / "BRIEF.md").read_text(encoding="utf-8"), "Build the thing on devbox")
    policy = REPO / "orchestration" / "queue" / "POLICY.md"
    assert (there / "POLICY.md").read_text(encoding="utf-8") == policy.read_text(encoding="utf-8")
    prompt = queue_dir / rtopic / "PROMPT.md"
    assert (there / "PROMPT.md").read_text(encoding="utf-8") == prompt.read_text(encoding="utf-8")
    # The record keeps the host-side worktree, so collect knows where to look.
    expect(q("show", devbox_task).out, f"me@devbox:{WORKTREE}")


def test_a_remote_result_comes_back_and_its_session_outlives_a_down_host(
    devbox_dispatched, devbox_task, hosts, queue_dir
):
    stubs = hosts.stubs
    write(hosts.remote("me@devbox", WORKTREE) / "result.md",
          "---\noutcome: shipped\nartifact: https://github.com/remote-owner/app/pull/4242\n---\n"
          "Built it on devbox and opened the pull request.\n")
    stubs.pipeline_pr(4242, "fix/build-on-devbox")
    hosts.session(RSESSION, name="Build it on devbox", state="idle", agent="claude",
                  backend_type="ssh:devbox", cwd=WORKTREE,
                  worktrees=[{"repo_path": "/srv/code/app", "worktree_path": WORKTREE,
                              "branch": "fix/build-on-devbox", "created_by_thurbox": True}])

    # The worker writes a file and collect reads a file; ssh is only how it gets here.
    expect(q("collect").out, "result fetched from me@devbox", "shipped", "[publish verified: attested]")
    assert (queue_dir / devbox_task / "result.md").is_file(), "the fetched result lands in the queue"

    # A down host leaves the LATCHED state standing, so the session still reads
    # `idle`, and `idle` is reapable. The host is asked first.
    stubs.pr_state(4242, "MERGED")
    hosts.flag("me@devbox", "down")
    expect(q("reap").out, "unreachable: host devbox")
    refute(deletions(stubs), RSESSION)
    expect((stubs.root / "sessions" / f"{RSESSION}.json").read_text(encoding="utf-8"), '"state": "idle"')

    hosts.unflag("me@devbox", "down")
    # Reachability is not permission to delete: the host is asked for its own
    # session list, the same ssh path host_reachable just used. An occupant
    # there keeps the session; an empty list is a real absence and is reaped.
    write(stubs.root / "ssh-state" / "me@devbox.session-list.json", json.dumps([
        {"id": RSESSION, "cwd": WORKTREE, "backend_type": "local-tmux"},
        {"id": "manual-on-host", "cwd": WORKTREE + "/src", "backend_type": "local-tmux"},
    ]))
    expect(q("reap").out, "kept", RSESSION, "manual-on-host", WORKTREE)
    refute(deletions(stubs), RSESSION)
    (stubs.root / "ssh-state" / "me@devbox.session-list.json").unlink()
    expect(q("reap").out, "reaped", RSESSION)
    expect(deletions(stubs), RSESSION)


@pytest.mark.skipif(os.name == "nt", reason="the fake host's login shell is a real /bin/sh, which Windows has not got")
def test_a_remote_command_runs_in_a_login_shell_and_moving_bytes_does_not(hosts, tmp_path):
    """`ssh host 'cmd'` gets a shell that is neither login nor interactive, so
    the profile has not run and a binary in `~/.local/bin` reads as missing —
    indistinguishable from an unprovisioned machine. The brief push and result
    fetch are the exception, because a profile that prints would land its banner
    in the middle of them."""
    found = queue_module(
        "entry, why = q.host_entry('profilebox')\n"
        "proc = q.ssh_run(entry, 'command -v fleet-fake-agent')\n"
        "print('rc=%d' % proc.returncode)\n"
        "print('found=' + ' '.join(proc.stdout.split()))\n"
    )
    expect(found, "/.local/bin/fleet-fake-agent", "rc=0")

    moved = queue_module(
        "entry, why = q.host_entry('profilebox')\n"
        "body = '---\\noutcome: shipped\\n---\\nDone on profilebox.\\n'\n"
        "print('push=' + (q.push_brief(entry, sys.argv[1], body) or 'ok'))\n"
        "text, why = q.fetch_result(entry, sys.argv[1])\n"
        "print('fetch=' + (why or 'ok'))\n"
        "print('same=' + str(text == body))\n",
        str(tmp_path / "profilebox-result.md"),
    )
    expect(moved, "push=ok", "same=True")
    refute(moved, "Welcome to profilebox")


def test_a_windows_host_misdeclared_as_tmux_is_caught_at_the_first_probe(hosts):
    """The tripwire logs any command that is not `-EncodedCommand`; this proves
    it fires, so its silence in the next test means something."""
    out = queue_module(
        "entry, why = q.host_entry('winbox')\n"
        "entry = dict(entry, multiplexer='tmux')\n"
        "last = q.probe_host(entry, '/srv/code/app')[-1]\n"
        "print('%s|%s|%s' % (last['check'], last['ok'], last['detail']))\n"
    )
    expect(out, "posix shell|False|the host answered ssh but did not print the POSIX shell sentinel")
    assert hosts.read_flag("me@winbox", "posix"), "the tripwire saw the POSIX command that reached it"


def test_a_windows_host_is_spoken_to_in_powershell_and_the_bytes_survive(hosts, queue_dir):
    """PowerShell's console code page is not UTF-8, and neither a brief nor a
    result is ASCII."""
    stubs = hosts.stubs
    wtopic = ok(q("topic", "add", "run-on-windows", "--title", "Run a task on Windows",
                  "--prompt", "fleet should dispatch to a psmux host — café ✓")).stdout.strip()
    ok(q("add", wtopic, "build-on-winbox", "--title", "Build it on winbox", "--repo", WINREPO, "--host", "winbox",
         "--branch", "fix/build-on-winbox", "--number", "30"))
    hold_open(wtopic, "31")
    task = queue_dir / wtopic / "30-build-on-winbox"
    write(task / "BRIEF.md", 'Build the thing on winbox: naïve café ✓, "double", $dollar, `tick`.\n')

    out = q("dispatch", "--dry-run").out
    expect(out, "speaking PowerShell")
    refute(out, "cat > <worktree>")

    for flag, probe, want in (("down", "reachable", "No route to host"),
                              ("norepo", "repo", "does not exist on that host"),
                              ("noforge", "forge", "credentials of its own")):
        hosts.flag("me@winbox", flag)
        out = q("dispatch").out
        hosts.unflag("me@winbox", flag)
        # Not spawned, and the probe says why in the same words a POSIX host's does.
        expect(out, "NOT SPAWNED", want)
    assert creates(stubs) == [], creates(stubs)

    hosts.next_session(WSESSION)
    hosts.session(WSESSION, name="Build it on winbox", state="working", agent="claude", hook_reported=True,
                  worktrees=[{"repo_path": WINREPO, "worktree_path": WWORKTREE, "branch": "fix/build-on-winbox"}])
    expect(q("dispatch").out, "reachable: answers ssh, PowerShell", "brief copied to me@winbox")
    there = hosts.remote("me@winbox", WWORKTREE)
    for name, src in (("BRIEF.md", task / "BRIEF.md"),
                      ("POLICY.md", REPO / "orchestration" / "queue" / "POLICY.md"),
                      ("PROMPT.md", queue_dir / wtopic / "PROMPT.md")):
        assert (there / name).read_bytes() == src.read_bytes(), f"{name} lands in the Windows worktree byte for byte"
    remote = yaml.safe_load((task / "task.yaml").read_text(encoding="utf-8"))["remote"]
    # Named in the host's own spelling, the result beside it where collect will look.
    assert remote["brief"] == WWORKTREE + "\\BRIEF.md", remote
    assert remote["result"] == WWORKTREE + "\\result.md", remote

    written = ("---\noutcome: shipped\nartifact: https://github.com/remote-owner/app/pull/4343\n---\n"
               "Built it on winbox \u2014 caf\u00e9 \u2713.\r\nWritten by Windows.\r\n").encode()
    (there / "result.md").write_bytes(written)
    stubs.pipeline_pr(4343, "fix/build-on-winbox")
    hosts.session(WSESSION, name="Build it on winbox", state="idle", agent="claude",
                  backend_type="ssh:winbox", cwd=WWORKTREE,
                  worktrees=[{"repo_path": WINREPO, "worktree_path": WWORKTREE,
                              "branch": "fix/build-on-winbox", "created_by_thurbox": True}])
    expect(q("collect").out, "result fetched from me@winbox", "shipped")
    assert (task / "result.md").read_bytes() == written, "the fetched result is the bytes the worker wrote, CRLF and all"

    stubs.pr_state(4343, "MERGED")
    hosts.flag("me@winbox", "down")
    expect(q("reap").out, "unreachable: host winbox")
    hosts.unflag("me@winbox", "down")
    write(stubs.root / "ssh-state" / "me@winbox.session-list.json", json.dumps([
        {"id": WSESSION, "cwd": WWORKTREE, "backend_type": "local-tmux"},
        {"id": "manual-on-winbox", "cwd": WWORKTREE + "\\src", "backend_type": "local-tmux"},
    ]))
    expect(q("reap").out, "kept", WSESSION, "manual-on-winbox", WWORKTREE)
    refute(deletions(stubs), WSESSION)
    (stubs.root / "ssh-state" / "me@winbox.session-list.json").unlink()
    expect(q("reap").out, "reaped", WSESSION)
    expect(deletions(stubs), WSESSION)

    assert hosts.read_flag("me@winbox", "posix") == "", "no POSIX command ever reached the Windows host"
    # And it was spoken to throughout, so that silence means something.
    expect(hosts.read_flag("me@winbox", "commands"), "ToBase64String")


TSESSION = "dddddddd-dddd-dddd-dddd-dddddddddddd"


def trust(stubs, pane: list[str]):
    write(stubs.root / "panes" / f"{TSESSION}.txt", "\n".join(pane) + "\n")
    return run([*PYTHON, str(REPO / "scripts" / "lib" / "session_trust.py"), "--timeout", "3", TSESSION])


def test_the_trust_dialog_on_a_windows_pane_is_answered_in_both_layouts(stubs):
    """Observed on a Windows 11 test machine, each breaking the handoff alone:
    psmux captures the pane with every space gone, and the Claude Code there
    draws the dialog with the selector already on "Yes", where `down enter`
    selects "No, exit". The pane never changes, so each run ends "still on the
    pane" and names the keys it sent, which is the claim."""
    write(stubs.root / "sessions" / f"{TSESSION}.json", json.dumps(
        {"id": TSESSION, "name": "trust", "state": "unreported", "agent": "claude", "hook_reported": False}) + "\n")

    out = trust(stubs, ["Quicksafetycheck:Isthisaprojectyoucreatedoroneyoutrust?", "❯1.Yes,Itrustthisfolder",
                        "2.No,exit", "", "Entertoconfirm·Esctocancel"]).out
    refute(out, "no trust dialog seen")
    expect(out, "sent 'enter' but")

    out = trust(stubs, ["Quick safety check: Is this a project you created or one you trust?", "❯ No, exit",
                        "  Yes, I trust this folder", "Enter to confirm · Esc to cancel"]).out
    expect(out, "sent 'down enter' but")
