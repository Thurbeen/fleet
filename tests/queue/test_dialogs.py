"""§21: every dialog in front of the agent is answered before the send.

A shepherd fixer started under a directory whose CLAUDE.md imports a file outside
it, and Claude Code opened that dialog BEFORE anything else. The trust step knew
only the folder-trust dialog, saw nothing it recognised, typed nothing, and the
fixer sat unprompted until a person pressed Enter. The answer is the DEFAULT,
`No`, a bare Enter: `Yes` would load a guide written for someone else into the
worker. Driven for real against a pane that closes on Enter, with the agent not
yet reporting, as it is behind a modal dialog.

§21b: dispatch runs with no bash on the machine. It used to shell out to
shell scripts for the profile and for the dialog, and
with no bash the profile was SWALLOWED — the worker started without its settings
and nothing said so — while the trust step failed AFTER `session create`, leaving
a session never sent its brief. Both run in-process now, and so does the watch
override that used to be `sh -c`. On native Windows this is simply the machine.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path

import pytest
from kit_dispatch import ANSWERING_KEYS, FOLDER_DIALOG, IMPORTS_DIALOG, behind_dialog, keys, next_session
from queuekit import ok

from harness import PYTHON, REPO, Run, expect, refute, run, write
from harness import run_fleet as fleet
from harness import run_queue as q

TRUST = REPO / "scripts" / "lib" / "session_trust.py"


def trust(*args: str, **env: str | None) -> Run:
    return run([*PYTHON, str(TRUST), *args], **env)


@pytest.fixture(autouse=True)
def answering(stubs) -> None:
    stubs.tool("thurbox-cli", ANSWERING_KEYS)


def test_the_external_imports_dialog_is_answered_with_its_default(stubs):
    sid = "d1a10900-0000-0000-0000-000000000001"
    behind_dialog(stubs, sid, IMPORTS_DIALOG)
    done = trust(sid, "--timeout", "5")
    assert done.code == 0, done.out
    # One bare Enter and nothing else, and the dialog is gone before anything could be sent.
    assert keys(stubs) == [f"session key {sid} enter"], done.out
    assert not (stubs.root / "panes" / f"{sid}.txt").exists()


def test_a_dialog_queued_behind_the_folder_trust_one_is_answered_too(stubs):
    """What a fresh path under such a repo shows: folder trust, then imports behind
    it. Answering the first and returning would hand the send to the second."""
    sid = "d1a10900-0000-0000-0000-000000000002"
    behind_dialog(stubs, sid, FOLDER_DIALOG, queued=IMPORTS_DIALOG)
    done = trust(sid, "--timeout", "5")
    assert done.code == 0, done.out
    assert keys(stubs) == [f"session key {sid} down", f"session key {sid} enter", f"session key {sid} enter"]


# --- 21b. dispatch runs with no bash on the machine --------------------------------


def shell_free(path: str) -> bool:
    return not (shutil.which("bash", path=path) or shutil.which("sh", path=path))


@pytest.fixture
def no_shell_path(tmp_path) -> str:
    """The stub tools, `git`, and this Python's own directory — and no bash or sh."""
    dirs = [str(Path(shutil.which("thurbox-cli")).parent)]
    stand_ins = Path(os.environ["FLEET_STUB_ROOT"]) / "bin"
    stand_ins.mkdir(parents=True, exist_ok=True)
    dirs.insert(0, str(stand_ins))
    git_dir = str(Path(shutil.which("git")).parent)
    if not shell_free(git_dir):
        # git shares its directory with sh (a Linux /usr/bin): carry git alone.
        alone = tmp_path / "git-alone"
        alone.mkdir()
        (alone / "git").symlink_to(shutil.which("git"))
        git_dir = str(alone)
    dirs += [git_dir, str(Path(sys.executable).parent)]
    path = os.pathsep.join(dirs)
    assert shell_free(path), f"the PATH this test runs under has bash or sh on it: {path}"
    return path


def test_dispatch_needs_no_bash(stubs, queue_dir, no_shell_path):
    topic = ok(q("topic", "add", "no-bash", "--title", "Dispatch with no bash",
                 "--prompt", "dispatch must not call bash", PATH=no_shell_path)).stdout.strip()
    ok(q("add", topic, "profiled", "--title", "Profiled", "--repo", "/tmp/repo-nobash", "--branch", "fix/nobash",
         "--number", "01", "--profile", "sweep", PATH=no_shell_path))
    write(queue_dir / topic / "01-profiled" / "BRIEF.md", "Do the thing without bash.\n")

    sid = "d1a10900-0000-0000-0000-00000000000b"
    next_session(stubs, sid)
    behind_dialog(stubs, sid, FOLDER_DIALOG)

    out = q("dispatch", PATH=no_shell_path).out
    refute(out, "Traceback", "NOT PROMPTED")
    create = [c for c in stubs.calls("thurbox-cli", "session create") if "--repo-path /tmp/repo-nobash" in c]
    assert create, out
    # The task's profile reaches session create, and the trust dialog is answered
    # in-process — down, then enter — before the brief is sent.
    expect(create[-1], "--env MAX_THINKING_TOKENS=8000")
    assert keys(stubs) == [f"session key {sid} down", f"session key {sid} enter"], out
    expect("\n".join(stubs.calls("thurbox-cli", "session send")), f"session send {sid} Read")

    # The watch override is an argv list: with no `sh` to hand a line to, it still
    # runs, and what it read is folded into the task it belongs to.
    events = queue_dir.parent / "events-nobash.jsonl"
    write(events, json.dumps({"seq": 900, "at": 1788793000000, "session": sid, "event": "state",
                              "from_state": None, "to_state": "working", "state": "working",
                              "reason": "hook"}) + "\n")
    replay = shlex.join([sys.executable, "-c",
                         "import sys; sys.stdout.write(open(sys.argv[1], encoding='utf-8').read())", str(events)])
    out = q("watch", "--for-secs", "0", PATH=no_shell_path, FLEET_QUEUE_WATCH_CMD=replay).out
    refute(out, "could not read the event stream")
    progress = (queue_dir / topic / "01-profiled" / "progress.jsonl").read_text(encoding="utf-8")
    assert [json.loads(line)["seq"] for line in progress.splitlines() if line.strip()] == [900], out


def test_the_trust_step_alone_needs_no_bash_and_keeps_its_json(stubs, no_shell_path):
    sid = "d1a10900-0000-0000-0000-00000000000c"
    behind_dialog(stubs, sid, IMPORTS_DIALOG)
    out = trust(sid, "--timeout", "5", "--json", PATH=no_shell_path).out
    expect(out, '"outcome":"answered"', f'{{"session":"{sid}","agent":"claude"')


def test_a_squeezed_dialog_already_on_yes_is_answered_with_enter_alone(stubs, no_shell_path):
    """A psmux capture squeezes the spaces out, and the selector is already on
    "Yes", so Enter alone answers it — and the report names the key it sent."""
    sid = "d1a10900-0000-0000-0000-00000000000d"
    behind_dialog(stubs, sid, "Quicksafetycheck:Isthisaprojectyoucreatedoroneyoutrust?\n"
                              "❯1.Yes,Itrustthisfolder\n2.No,exit")
    out = trust(sid, "--timeout", "5", PATH=no_shell_path).out
    assert keys(stubs) == [f"session key {sid} enter"], out
    expect(out, "with 'enter'; none is left")


def test_the_trust_command_keeps_its_cli():
    """The trust command's CLI, `fleet session-trust`: --help and all, and exit 2
    on a session it cannot read."""
    expect(fleet("session-trust", "--help").out, "Exit codes")
    assert fleet("session-trust", "no-such-session").code == 2


def test_a_command_stem_that_takes_a_launch_flag_is_ready_without_a_keystroke(stubs):
    """thurbox names a `--command cursor-agent` session `cursor-agent`, not
    `cursor`. Measured 2026-09-18: session get --json has no args array; the
    pane probe puts the launch line in foreground_command, including --trust.
    That flag already answered trust, so this must be ready — exit 0, no
    keys — or dispatch creates the session and never sends the brief."""
    sid = "d1a10900-0000-0000-0000-0000000000ca"
    write(stubs.root / "sessions" / f"{sid}.json", json.dumps({
        "id": sid, "agent": "cursor-agent", "reports_as": None,
        "detected_agent": None, "hook_reported": False, "state": "uncovered",
        "hook_coverage": "none",
        "foreground_command": "cursor-agent --trust",
    }) + "\n")
    done = trust(sid, "--timeout", "5", "--json")
    assert done.code == 0, done.out
    report = json.loads(done.stdout)
    assert report["outcome"] == "ready", done.out
    assert report["agent"] == "cursor-agent", done.out
    assert keys(stubs) == []


def test_cursor_without_trust_on_the_launch_stays_flag_required(stubs):
    """A hand session create without --trust is what thurbox-session documents.
    Ready without looking at the launch line would send the brief into the
    dialog."""
    sid = "d1a10900-0000-0000-0000-0000000000cb"
    write(stubs.root / "sessions" / f"{sid}.json", json.dumps({
        "id": sid, "agent": "cursor-agent", "reports_as": None,
        "hook_reported": False, "state": "uncovered",
        "foreground_command": "cursor-agent",
    }) + "\n")
    done = trust(sid, "--timeout", "5", "--json")
    assert done.code == 3, done.out
    report = json.loads(done.stdout)
    assert report["outcome"] == "flag-required", done.out
    assert keys(stubs) == []


def test_cursor_without_a_launch_line_stays_flag_required(stubs):
    """Missing foreground_command is the same as a line without --trust:
    nothing asserts the dialog was answered."""
    sid = "d1a10900-0000-0000-0000-0000000000cd"
    write(stubs.root / "sessions" / f"{sid}.json", json.dumps({
        "id": sid, "agent": "cursor-agent", "reports_as": None,
        "hook_reported": False, "state": "uncovered",
    }) + "\n")
    done = trust(sid, "--timeout", "5", "--json")
    assert done.code == 3, done.out
    assert json.loads(done.stdout)["outcome"] == "flag-required", done.out
    assert keys(stubs) == []


def test_muse_stays_flag_required(stubs):
    """--yolo is not a trust flag, and no muse session has been watched to
    start. Ready here would send the brief into whatever is on the pane."""
    sid = "d1a10900-0000-0000-0000-0000000000cc"
    write(stubs.root / "sessions" / f"{sid}.json", json.dumps({
        "id": sid, "agent": "muse", "reports_as": None,
        "hook_reported": False, "state": "uncovered",
        "foreground_command": "muse --yolo",
    }) + "\n")
    done = trust(sid, "--timeout", "5", "--json")
    assert done.code == 3, done.out
    report = json.loads(done.stdout)
    assert report["outcome"] == "flag-required", done.out
    assert keys(stubs) == []
