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
import stat

import pytest
import yaml
from kit_hosts import Hosts, creates, deletions
from queuekit import fill_brief, ok

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
    expect(brief, "on host `devbox`", "in\n    `<worktree>.fleet`, and not inside your worktree",
           "PROMPT.md`, alongside this file", "POLICY.md`, alongside this file",
           "CONTEXT.md`, alongside this file", "no commit can carry them")
    refute(brief, "Delete `BRIEF.md`", "fleet context repo")
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

    there = hosts.remote("me@devbox", WORKTREE + ".fleet")
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
    write(hosts.remote("me@devbox", WORKTREE + ".fleet") / "result.md",
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


def test_a_requeued_remote_task_is_not_closed_on_the_previous_attempts_result(
    devbox_dispatched, devbox_task, hosts, queue_dir
):
    """The first attempt wrote its result on the host. Put back to `queued` by
    hand and dispatched again on the same branch, the new session gets the same
    worktree, and that file is still beside it: it is not the new attempt's."""
    stubs = hosts.stubs
    there = hosts.remote("me@devbox", WORKTREE + ".fleet")
    write(there / "result.md",
          "---\noutcome: shipped\nartifact: https://github.com/remote-owner/app/pull/4242\n---\nOld attempt.\n")
    stubs.pipeline_pr(4242, "fix/build-on-devbox")
    record = queue_dir / devbox_task / "task.yaml"
    doc = yaml.safe_load(record.read_text(encoding="utf-8"))
    doc["state"] = "queued"
    write(record, yaml.safe_dump(doc, sort_keys=False))

    again = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    hosts.next_session(again)
    hosts.session(again, name="Build it on devbox", state="working", agent="claude", hook_reported=True,
                  worktrees=[{"repo_path": "/srv/code/app", "worktree_path": WORKTREE,
                              "branch": "fix/build-on-devbox"}])
    expect(q("dispatch", THURBOX_SESSION=LEAD).out, "brief copied to me@devbox")

    assert not (there / "result.md").exists(), "the previous attempt's result is moved aside on the host"
    expect((there / "result.previous.md").read_text(encoding="utf-8"), "pull/4242")
    refute(q("collect").out, "pull/4242")
    expect(q("show", devbox_task).out, "state:       dispatched")


def test_a_windows_host_moves_the_previous_result_aside_in_powershell(hosts):
    there = hosts.remote("me@winbox", WWORKTREE + ".fleet")
    write(there / "result.md", "---\noutcome: shipped\n---\nOld attempt.\n")
    out = queue_module(
        "entry, why = q.host_entry('winbox')\n"
        "shell = q.host_shell(entry)\n"
        f"beside = {WWORKTREE + '.fleet'!r}\n"
        "proc = q.ssh_run(entry, shell.set_aside(shell.join(beside, q.RESULT_FILE),"
        " shell.join(beside, q.PREVIOUS_RESULT_FILE)), login=False)\n"
        "print('rc=%s' % proc.returncode)\n"
    )
    expect(out, "rc=0")
    assert not (there / "result.md").exists()
    expect((there / "result.previous.md").read_text(encoding="utf-8"), "Old attempt.")
    assert hosts.read_flag("me@winbox", "posix") == "", "it was PowerShell that moved it"


def landed_on_a_real_shell(hosts) -> tuple:
    """The devbox task collected and merged, its session idle, and devbox a
    host with a real shell: (stubs, the host's HOME, its session list)."""
    stubs = hosts.stubs
    write(hosts.remote("me@devbox", WORKTREE + ".fleet") / "result.md",
          "---\noutcome: shipped\nartifact: https://github.com/remote-owner/app/pull/4242\n---\nDone.\n")
    stubs.pipeline_pr(4242, "fix/build-on-devbox")
    hosts.session(RSESSION, name="Build it on devbox", state="idle", agent="claude",
                  backend_type="ssh:devbox", cwd=WORKTREE,
                  worktrees=[{"repo_path": "/srv/code/app", "worktree_path": WORKTREE,
                              "branch": "fix/build-on-devbox", "created_by_thurbox": True}])
    expect(q("collect").out, "shipped")
    stubs.pr_state(4242, "MERGED")
    hosts.flag("me@devbox", "realshell")
    listing = json.dumps([{"id": RSESSION, "cwd": WORKTREE, "backend_type": "local:tmux"}])
    return stubs, hosts.state / "me@devbox.home", listing


def host_cli(home, flavour: str, listing: str):
    """A thurbox-cli where thurbox provisions one for `flavour`, answering `listing`."""
    cli = home / ".local" / "share" / flavour / "bin" / "thurbox-cli"
    write(cli, "#!/bin/sh\n[ \"$1 $2 $3\" = 'session list --json' ] || exit 2\n"
               f"cat <<'EOF'\n{listing}\nEOF\n")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    return cli


@pytest.mark.skipif(os.name == "nt", reason="the fake host's login shell is a real /bin/sh, which Windows has not got")
def test_reap_finds_the_cli_thurbox_provisioned_on_the_host_off_path(
    devbox_dispatched, devbox_task, hosts
):
    """thurbox puts its own `thurbox-cli` on a host under its data directory,
    deliberately NOT on `PATH`, and drives every remote session through it. A
    reap that asked the host for `thurbox-cli` by bare name read "not found" on
    exactly the hosts thurbox provisioned, and kept every session there for
    good. This host has a real shell, the CLI where thurbox puts it, and none
    on any `PATH`."""
    stubs, home, listing = landed_on_a_real_shell(hosts)

    # No CLI anywhere: a named keep, never a guess that the host is empty. The
    # fake host's shell is THIS machine's, so a thurbox-cli installed here
    # system-wide would answer for it; that half is only provable without one.
    if not any(os.path.exists(f"{d}/thurbox-cli") for d in ("/usr/local/bin", "/usr/bin", "/bin")):
        out = q("reap").out
        expect(out, "kept", "no thurbox-cli on the host")
        refute(deletions(stubs), RSESSION)

    host_cli(home, "thurbox", listing)
    expect(q("reap").out, "reaped", RSESSION)
    expect(deletions(stubs), RSESSION)


@pytest.mark.skipif(os.name == "nt", reason="the fake host's login shell is a real /bin/sh, which Windows has not got")
def test_a_dev_thurbox_reaps_through_its_own_flavours_cli_and_not_the_release_one(
    devbox_dispatched, devbox_task, hosts
):
    """A dev build provisions under `thurbox-dev`, and the release copy beside
    it reads another database. Read the wrong one and its list judges this
    session: here it does not hold the session at all, so a reap that always
    looked under `thurbox` keeps it, and one that asked the local CLI's
    `data_dir` for the flavour reaps it."""
    stubs, home, listing = landed_on_a_real_shell(hosts)
    write(stubs.root / "version.json", json.dumps(
        {"version": "0.0.0-dev", "data_dir": str(home / ".local" / "share" / "thurbox-dev")}))
    host_cli(home, "thurbox", json.dumps([{"id": "release-only", "cwd": "/elsewhere",
                                          "backend_type": "local:tmux"}]))
    host_cli(home, "thurbox-dev", listing)
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
    there = hosts.remote("me@winbox", WWORKTREE + ".fleet")
    for name, src in (("BRIEF.md", task / "BRIEF.md"),
                      ("POLICY.md", REPO / "orchestration" / "queue" / "POLICY.md"),
                      ("PROMPT.md", queue_dir / wtopic / "PROMPT.md")):
        assert (there / name).read_bytes() == src.read_bytes(), f"{name} lands in the Windows worktree byte for byte"
    remote = yaml.safe_load((task / "task.yaml").read_text(encoding="utf-8"))["remote"]
    # Named in the host's own spelling, the result beside it where collect will look.
    assert remote["brief"] == WWORKTREE + ".fleet\\BRIEF.md", remote
    assert remote["result"] == WWORKTREE + ".fleet\\result.md", remote

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


COMPANIONS = WORKTREE + ".fleet"


@pytest.mark.skipif(os.name == "nt", reason="the fake worktree's mode bits are POSIX")
def test_a_remote_worktree_stays_clean_and_its_memory_sits_beside_it(rtopic, hosts, queue_dir):
    """Nothing private lands inside the worktree a remote worker commits from:
    the brief, its prompt, policy and operator file, and the context bundle all
    sit in `<worktree>.fleet/` beside it, private to the account, so `git add -A`
    cannot publish them. The bundle is rendered here, because that host has no
    `fleet context` to run, and it is self-contained — the prior result's text,
    not a control-plane path. `reap` takes the directory away with the session,
    and nothing else."""
    stubs = hosts.stubs
    write(queue_dir / "OPERATOR.md", "Never name the lab machines.\n")
    ok(q("add", rtopic, "upstream", "--title", "Upstream", "--repo", "/tmp/repo-a",
         "--branch", "fix/upstream", "--number", "40"))
    upstream = queue_dir / rtopic / "40-upstream"
    write(upstream / "result.md", "---\noutcome: shipped\n---\nThe upstream API is `build --fast`.\n")
    ok(q("add", rtopic, "build-on-devbox", "--title", "Build it on devbox", "--repo", "/srv/code/app",
         "--host", "devbox", "--branch", "fix/build-on-devbox", "--number", "41"))
    ok(q("block", f"{rtopic}/41-build-on-devbox", "--on", f"{rtopic}/40-upstream",
         "--kind", "semantic-dependency", "--why", "uses the upstream API"))
    doc = yaml.safe_load((upstream / "task.yaml").read_text(encoding="utf-8"))
    doc.update(state="landed", outcome="shipped", concluded_at="2026-10-01T00:00:00+00:00")
    write(upstream / "task.yaml", yaml.safe_dump(doc))
    hold_open(rtopic, "42")
    task = queue_dir / rtopic / "41-build-on-devbox"
    fill_brief(task / "BRIEF.md")
    ok(run([*PYTHON, str(REPO / "scripts" / "lib" / "context.py"), "learn", "github.com/remote-owner/app",
            "The app builds only with the vendored toolchain."]))

    there = hosts.remote("me@devbox", WORKTREE)
    there.mkdir(parents=True)
    git_env = {"GIT_DIR": None}
    for argv in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "base"]):
        ok(run(["git", "-C", str(there), *argv], **git_env))
    write(hosts.state / "me@devbox.origin", "git@github.com:remote-owner/app.git\n")
    hosts.next_session(RSESSION)
    hosts.session(RSESSION, name="Build it on devbox", state="working", agent="claude", hook_reported=True,
                  worktrees=[{"repo_path": "/srv/code/app", "worktree_path": WORKTREE,
                              "branch": "fix/build-on-devbox"}])
    expect(q("dispatch", THURBOX_SESSION=LEAD).out, "brief copied to me@devbox")

    status = ok(run(["git", "-C", str(there), "status", "--porcelain", "--untracked-files=all"], **git_env)).stdout
    assert status == "", f"the remote worktree is clean after dispatch:\n{status}"
    beside = hosts.remote("me@devbox", COMPANIONS)
    assert sorted(p.name for p in beside.iterdir()) == ["BRIEF.md", "CONTEXT.md", "OPERATOR.md",
                                                       "POLICY.md", "PROMPT.md"]
    assert beside.stat().st_mode & 0o777 == 0o700, oct(beside.stat().st_mode)
    expect(stubs.calls("thurbox-cli", "session send")[-1], f"{COMPANIONS}/BRIEF.md")

    bundle = (beside / "CONTEXT.md").read_text(encoding="utf-8")
    expect(bundle, "repo: github.com/remote-owner/app", "vendored toolchain", "The upstream API is `build --fast`")
    refute(bundle, str(upstream / "result.md"))
    assert len(bundle.encode("utf-8")) <= 8192, len(bundle)
    brief = (beside / "BRIEF.md").read_text(encoding="utf-8")
    expect(brief, "CONTEXT.md`, alongside this file", "`<worktree>.fleet`")
    refute(brief, "Delete `BRIEF.md`", "fleet context repo", "in the root of this worktree")
    remote = yaml.safe_load((task / "task.yaml").read_text(encoding="utf-8"))["remote"]
    assert remote["brief"] == COMPANIONS + "/BRIEF.md", remote
    assert remote["result"] == COMPANIONS + "/result.md", remote

    # The worker writes its result beside the brief, and collect fetches it from there.
    write(beside / "result.md", "---\noutcome: shipped\nartifact: https://github.com/remote-owner/app/pull/4444\n"
          "learned:\n  - repo: github.com/remote-owner/app\n    fact: \"Run the linter twice.\"\n---\nDone.\n")
    write(beside / "notes.txt", "mine")
    stubs.pipeline_pr(4444, "fix/build-on-devbox")
    hosts.session(RSESSION, name="Build it on devbox", state="idle", agent="claude",
                  backend_type="ssh:devbox", cwd=WORKTREE,
                  worktrees=[{"repo_path": "/srv/code/app", "worktree_path": WORKTREE,
                              "branch": "fix/build-on-devbox", "created_by_thurbox": True}])
    expect(q("collect").out, "result fetched from me@devbox", "shipped", "1 fact recorded")

    # Released with the session, and only what fleet put there.
    stubs.pr_state(4444, "MERGED")
    expect(q("reap").out, "reaped", RSESSION, COMPANIONS)
    assert sorted(p.name for p in beside.iterdir()) == ["notes.txt"], "a file fleet did not write is left"
    assert there.is_dir(), "the worktree itself is thurbox's, not this cleanup's"
