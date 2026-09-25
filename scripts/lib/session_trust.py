#!/usr/bin/env python3
"""Get a freshly created session past its agent's trust dialog, without
touching anything the operator owns.

THE BUG. thurbox mints a FRESH worktree path per session, and most agents ask
whether they may work in a directory they have not seen before. So every
worker fleet spawned sat on that dialog: the session existed, the pane was
live, the agent had not started. `session send` then typed the brief INTO the
dialog. Sessions fleet created were broken, every time.

WHY A KEYSTROKE AND NOT A CONFIG EDIT. `fleet trust-thurbox-dir` can
seed Claude Code's trust into ~/.claude.json and it still works — it is the
right fallback when a dialog cannot be answered. It is the wrong DEFAULT: it
writes to a file the user owns, for a tool fleet did not install, and it
needs a different file format for every agent. Answering the prompt touches
nothing that outlives the session, and it is one mechanism for the agents
whose gate is a prompt at all.

THE FAILURE MODE THIS IS BUILT AROUND: sending a key blindly. If the dialog
is not there, the key lands in a live agent's composer — noise at best, a
stray instruction at worst. So this never sends unless it can SEE the
dialog, and never reports success unless it can see the dialog is gone.

    confirm  the pane shows one of this agent's dialogs
    answer   the keys for THAT dialog, which are not the same per agent
    confirm  the dialog is gone — and answer the next one if another
             comes up behind it

and if either confirmation fails it sends nothing more and says so. A session
waiting on a dialog is visible and fixable; a session that has been typed
into randomly is neither.

It is safe to run only in the window between `session create` and the first
`session send`, which is when `fleet queue dispatch` runs it: nothing
has been typed into that pane yet, so there is no composer content to
corrupt. Do not run it against a session that is already working.

IN-PROCESS, AND NO SHELL. `dispatch` calls `answer_dialogs` directly, and
`fleet session-trust` runs this file. It needs thurbox-cli and Python and
nothing else, because a native Windows machine has no shell to lean on: the
old bash version crashed dispatch there after `session create`, leaving a
session that was never sent its brief.

A REMOTE SESSION IS ANSWERED THE SAME WAY, and this is the reason the
keystroke is the default rather than the config edit. `session get`, `session
capture` and `session key` each DELEGATE to the thurbox-cli on the host, so
every command below reaches a pane on another machine unchanged. The config
edit does not: `fleet trust-thurbox-dir` writes THIS machine's ~/.claude.json,
and a remote agent reads the remote one, so seeding here would do nothing at
all for a worker over there — silently.

The one host this cannot answer is one whose hosts.toml entry sets
`share_sessions = false`, which switches that delegation off wholesale.
`fleet queue add --host` refuses such a host outright rather than dispatching a
worker that would sit on a dialog nothing can see.

PER-AGENT, and the differences are real (see GATES below):

  claude          a dialog whose default selection is "No, exit". A bare
                  Enter DISMISSES it. Down, then Enter. Under a directory
                  whose CLAUDE.md imports a file outside it, a second dialog
                  follows, and ITS default — No — is the answer. Enter.
  codex           a folder dialog; Enter accepts only when the accepting
                  option is selected. A following hook-review menu needs a
                  person; fleet never accepts unknown hooks. Its visible
                  composer confirms readiness before the first hook reports.
  pi, pi-signed   a dialog; Enter accepts. Persists per path.
  grok, kimi      no dialog in a git worktree. Nothing to do.
  cursor          NOT a keystroke — `--trust` answers the folder dialog.
                  Ready only when the session document's
                  `foreground_command` carries `--trust` (there is no
                  `args` array on `session get`). A hand spawn without
                  that flag stays flag-required.
                  `--command cursor-agent` is named `cursor-agent` on the
                  session document, not `cursor`; both names are this row.
  muse            NOT a keystroke either, but `--yolo` is not a trust flag.
                  It aliases `--disable-approval` and drops confirmations
                  and the sandbox together. Vendor: a one-off isolated
                  container only. Flag-required until a session has been
                  watched to start. Nothing is typed.

AN AGENT THAT IS NOT IN THE TABLE is the operator's to teach, not fleet's to
guess: `TRUST_SIGNATURE` and `TRUST_KEYS` in orchestration/agent.conf, with
`TRUST_KEYS=none` for an agent that shows no dialog at all. With neither set
this refuses and sends nothing, because the `claude` row above is why —
guessing a keystroke there exits the agent.

BOTH ARE SAYABLE ABOUT ONE AGENT — `<agent>.TRUST_SIGNATURE=`,
`<agent>.TRUST_KEYS=` — and a line that names an agent outranks the table,
since it is the operator describing that agent rather than their fleet. The
case that needed it is the SECOND ACCOUNT of an agent already in the table: it
runs under a name of its own, so it matched no key, and answering its dialog
meant editing the checkout's one `TRUST_SIGNATURE` before each dispatch and
putting it back afterwards. `<agent>.LIKE=<the watched agent>` is the whole of
it now. See scripts/lib/agent_settings.py.

Usage:
  uv run fleet session-trust <session-uuid-or-name> [--timeout SECS] [--json]

Exit codes, so a caller can decide without parsing prose:
  0  the pane is ready for a prompt — every dialog was answered, or there was
     none and the agent is up
  2  usage, or the session could not be read
  3  could NOT confirm. Nothing more was sent. Do not send a prompt either.

Requires: thurbox-cli.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name: str, filename: str):
    """Load a module from scripts/lib beside this file, under a name of its own.

    The rule every module here follows: keyed in sys.modules as `fleet/cli.py`
    keys it, so the copy `queue.py` already holds is the copy this gets — one
    answer about what the settings say, not two.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# WHICH AGENT. A trust dialog is a fact about an agent, so which one to expect
# is asked per agent rather than per checkout.
agent_settings = _load_sibling("fleet_agent_settings", "agent_settings.py")

# After each answer the pane is watched this many more seconds for a dialog
# behind it; `MAX_ANSWERS` bounds a dialog that keeps coming back, which is not
# one this understands.
SETTLE = 3
MAX_ANSWERS = 4

# --- the per-agent table -----------------------------------------------------
#
# A GATE is one dialog: a signature — a regex matched case-insensitively
# against the pane — and the space-separated key sequence that answers it, in
# order. An agent can show more than one, one after another. An agent with no
# gate has no dialog to answer.

GATES = {
    "claude": [
        # Observed live on Claude Code, 2026-09-07, in a fresh thurbox worktree:
        #
        #     Quick safety check: Is this a project you created or one you trust?
        #     ❯ No, exit
        #       Yes, I trust this folder
        #     Enter to confirm · Esc to cancel
        #
        # Matched on the accepting option's own label, which is specific enough
        # that ordinary agent output cannot produce it by accident.
        #
        # THE TRAP, and it is right there in the capture above: the default
        # selection is "No, exit". A bare Enter DISMISSES the dialog and the
        # agent exits. Move the selection down to the accepting option first.
        # This is the one dialog where the obvious answer is the wrong one.
        ("yes, i trust this folder|quick safety check: is this a project you created",
         "down enter"),
        # Observed live on Claude Code, 2026-09-12, on a shepherd fixer started
        # under a directory whose CLAUDE.md imports a file outside it — shown
        # before anything else:
        #
        #     Allow external CLAUDE.md file imports?
        #     ❯ No, disable external imports
        #       Yes, allow external imports
        #
        # Answered with its DEFAULT, a bare Enter. `Yes` would load a guide
        # written for someone else — the lead's, in the case that found it —
        # into a worker.
        (r"allow external claude\.md file imports|no, disable external imports", "enter"),
    ],
    "codex": [
        ("do you trust the contents of this directory|do you trust this directory", "enter"),
        # Observed on Codex v0.156.1. Match the folder question, its permission
        # text and the selected accepting option before sending Enter.
        (r"trust this folder\?codex can read, edit, and run files here.{0,500}[›❯]1\.trust and continue",
         "enter"),
    ],
    "pi": [("trust this project|do you trust", "enter")],
    "pi-signed": [("trust this project|do you trust", "enter")],
    # No dialog when launched inside a git repo root, which a thurbox worktree
    # always is. Nothing to answer; still confirmed as up below.
    "grok": [],
    "kimi": [],
}
# Agents with no keystroke gate. Only cursor's --trust answers a dialog;
# muse's --yolo is --disable-approval and does not. TRUST_LAUNCH is the
# subset that is ready when that flag is on the launch line.
FLAG_ONLY = {"cursor", "cursor-agent", "muse"}
TRUST_LAUNCH = {"cursor", "cursor-agent"}

# WHERE THE SELECTOR ALREADY IS, and it outranks the table. The
# folder-trust dialog above defaults to "No, exit"; the one Claude Code 2.1.247
# draws on a Windows 11 host (2026-09-12) is numbered and defaults to the other
# option:
#
#     ❯ 1. Yes, I trust this folder
#       2. No, exit
#
# `down enter` there selects "No, exit" and the agent exits — observed, not
# supposed. So when the selector is already on the accepting option, Enter
# alone accepts, whichever layout drew it. Matched against the squeezed pane.
#
# It is spelled with Claude Code's own label, so it can only match Claude
# Code's dialog however the agent is NAMED — which is what makes it hold for a
# second account of it, running under a name of its own.
YES_SELECTED = re.compile(r"❯([0-9]+\.)?yes,itrustthisfolder", re.IGNORECASE)


# --- talking to thurbox ------------------------------------------------------


def _run(args: list) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["thurbox-cli", *args], capture_output=True, check=False)
    except OSError:
        return None


def _json(proc: subprocess.CompletedProcess | None) -> dict:
    if proc is None:
        return {}
    try:
        value = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def session_info(session: str) -> dict:
    """`session get --json`, or {} when thurbox could not answer it."""
    proc = _run(["session", "get", session, "--json"])
    return _json(proc) if proc is not None and proc.returncode == 0 else {}


def launch_tokens(info: dict) -> list[str]:
    """Argv tokens from the session document.

    `session get --json` has no `args` array (measured 2026-09-18). The
    pane probe puts the launch line in `foreground_command`. A stored
    `args` list is still accepted if a later thurbox starts writing one.
    """
    raw = info.get("foreground_command")
    if isinstance(raw, str) and raw.strip():
        return raw.split()
    extra = info.get("args")
    if isinstance(extra, list):
        return [str(a) for a in extra]
    return []


def squeeze(text: str) -> str:
    """Text with every whitespace character gone.

    WHITESPACE IS NOT PART OF THE MATCH, on either side. psmux 3.3.6 — the
    multiplexer of a Windows host — captures Claude Code's dialog with every
    space gone (`❯1.Yes,Itrustthisfolder`, observed 2026-09-12), so a signature
    spelled with spaces never matched there and the worker sat on its dialog
    unprompted. Stripping both sides changes nothing a tmux pane matched, and it
    also survives a dialog wrapped at the pane's width.
    """
    return re.sub(r"\s+", "", text)


def pane_text(uuid: str, lines: int = 60) -> str:
    """Only the captured pane text, without thurbox's metadata.

    `--json` and `.output`, not the plain capture: the human format wraps the
    pane in metadata lines, and a signature could in principle match one of
    those instead of the pane itself. Zero lines excludes scrollback while
    retaining the visible pane.
    """
    output = _json(_run(["session", "capture", uuid, "--lines", str(lines), "--json"])).get("output")
    return output if isinstance(output, str) else ""


def pane(uuid: str) -> str:
    """The captured pane squeezed for signatures that may wrap or lose spaces."""
    return squeeze(pane_text(uuid))


def codex_composer_ready(uuid: str) -> bool:
    """The Codex input prompt is visible at the foot of the captured pane.

    Codex can be running before its startup hook reports. Its composer is the
    pane's own evidence that the trust gate is behind it. A mention of the
    placeholder in older transcript text is not that evidence.
    """
    tail = [line.strip() for line in pane_text(uuid).splitlines() if line.strip()]
    return (len(tail) >= 2 and tail[-2] == "› Ask Codex to do anything"
            and " · " in tail[-1])


def shows(signature: str, text: str) -> bool:
    try:
        return re.search(squeeze(signature), text, re.IGNORECASE) is not None
    except re.error:
        # A signature the operator wrote that is not a valid pattern matches
        # nothing, which is what `grep -E` made of one.
        return False


def agent_reported(uuid: str) -> bool:
    """An agent whose hooks have fired is running its own loop, which is proof
    there is no modal dialog in front of it."""
    return session_info(uuid).get("hook_reported") is True


def send_key(uuid: str, key: str) -> bool:
    proc = _run(["session", "key", uuid, key])
    return proc is not None and proc.returncode == 0


# --- an agent fleet has not watched ------------------------------------------


def taught_gates(agent_root: str, agent: str = "", only_named: bool = False) -> list | None:
    """What `orchestration/agent.conf` teaches: a gate list, or None for nothing.

    NOT IN THE TABLE IS NOT THE END. The table is what fleet has WATCHED, and
    the operator has watched their own agent — so `TRUST_SIGNATURE` and
    `TRUST_KEYS` there teach it one, the same way `LIMIT_BANNER` and
    `TRANSCRIPT_DIR` teach `refuel` one. Without them this still refuses rather
    than guessing a keystroke: a bare Enter into Claude Code's dialog exits the
    agent, and an invented answer would do that to somebody's.

    TWO LOOKUPS, AND THE ORDER IS THE POINT. `only_named` reads the lines that
    NAME this agent (`<agent>.TRUST_SIGNATURE=`) and nothing else; the caller
    puts that ahead of the built-in table, because a line naming one agent is
    the operator saying something about that agent and not about their fleet.
    The plain lookup — the agent's line, then the checkout's — is the fallback,
    and the checkout-wide setting keeps exactly the position it has always had:
    behind the table, answering for agents the table does not carry.
    """
    conf = agent_settings.conf(agent_root)
    read = agent_settings.named if only_named else agent_settings.value
    signature = read("TRUST_SIGNATURE", agent, conf)
    keys = read("TRUST_KEYS", agent, conf)
    if keys == "none":
        # The operator says this agent shows no dialog. Nothing to answer; it
        # is still confirmed as up.
        return []
    if not signature:
        return None
    return [(signature, keys or "enter")]


# --- the whole handshake -----------------------------------------------------


def answer_dialogs(session: str, timeout: int = 20, as_json: bool = False) -> tuple[int, str]:
    """Confirm, answer and confirm every dialog in front of the agent.

    Returns the exit code and the one line of report, formatted for the
    command line (`session-trust: ...`) or as the `--json` object. Every
    outcome is terminal and says exactly one thing, which is why this returns
    rather than prints.
    """
    agent = ""

    def say(detail: str, outcome: str) -> str:
        if as_json:
            return json.dumps(
                {"session": session, "agent": agent, "outcome": outcome, "detail": detail},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return f"session-trust: {detail}"

    # --- who is in the pane --------------------------------------------------
    info = session_info(session)
    if not info:
        return 2, say(f"no such session: {session}", "error")
    uuid = str(info.get("id") or session)
    # `detected_agent` is what is observably running and wins over the row's
    # `agent` when they disagree — a session created as a bare shell that a
    # harness launched an agent into is exactly that case.
    for key in ("detected_agent", "reports_as", "agent"):
        if info.get(key) not in (None, False):
            agent = str(info[key])
            break

    agent_root = os.environ.get("FLEET_AGENT_ROOT") or os.path.dirname(os.path.dirname(HERE))
    conf = agent_settings.conf(agent_root)
    # A line that NAMES this agent is the most specific answer there is, so it
    # comes before everything — including the two tables below, which are keyed
    # by the agents fleet itself has watched. A second account of a watched
    # agent has neither a line nor a key of its own; `<agent>.LIKE=` hands it
    # the watched row through `agent_settings.row`.
    named = taught_gates(agent_root, agent, only_named=True) if agent else None
    table = agent_settings.row(GATES, agent, conf)
    if named is not None:
        gates = named
    elif table is not None:
        gates = table
    elif any(name in FLAG_ONLY for name in agent_settings.chain(agent, conf)):
        names = agent_settings.chain(agent, conf)
        if any(name in TRUST_LAUNCH for name in names):
            if "--trust" in launch_tokens(info):
                return 0, say(
                    f"'{agent}' is not answered by a keystroke; nothing was "
                    "typed (--trust on the launch already answered the dialog)",
                    "ready",
                )
            return 3, say(
                f"'{agent}' is not answered by a keystroke — it takes --trust "
                "on the launch. This session's pane does not show that flag; "
                "nothing was typed.",
                "flag-required",
            )
        return 3, say(
            f"'{agent}' is not answered by a keystroke. muse's --yolo is "
            "--disable-approval, not a trust flag, and nothing here has "
            "watched a session start; nothing was typed.",
            "flag-required",
        )
    elif not agent:
        return 3, say("could not tell which agent holds the pane; sending nothing", "unconfirmed")
    else:
        gates = taught_gates(agent_root, agent)
        if gates is None:
            return 3, say(
                f"no trust gate is known for '{agent}'; sending nothing. Teach fleet\n"
                f"             one with {agent}.TRUST_SIGNATURE and {agent}.TRUST_KEYS in\n"
                "             orchestration/agent.conf — or, where it is another account of\n"
                f"             an agent fleet knows, {agent}.LIKE=<that agent>. A checkout-wide\n"
                "             TRUST_SIGNATURE still answers for every agent with no line of\n"
                f"             its own; the table is in {os.path.join(HERE, 'session_trust.py')}",
                "unknown-agent",
            )

    is_codex = "codex" in agent_settings.chain(agent, conf)

    def gate_on_pane() -> int | None:
        """The index of the gate the pane shows right now, if any."""
        if not gates:
            return None
        text = pane(uuid)
        for i, (signature, _keys) in enumerate(gates):
            if shows(signature, text):
                return i
        return None

    def hook_review_on_pane() -> bool:
        return is_codex and shows("hooks need review", squeeze(pane_text(uuid, lines=0)))

    def unanswerable_codex_folder_on_pane() -> bool:
        # A folder screen may leave the composer visible below it. A changed
        # selector or wording must block the brief, even though no gate matches.
        return is_codex and shows(r"trust this folder\?", pane(uuid))

    def hook_review_blocked() -> tuple[int, str]:
        return 3, say(
            "Codex shows 'Hooks need review'. Inspect the hooks in the pane:\n"
            f"               thurbox-cli session capture {uuid}\n"
            "             Choose how to continue yourself, then retry the queue "
            "prompt. Nothing was typed into this menu and the brief was not sent.",
            "hooks-review-required",
        )

    def folder_blocked() -> tuple[int, str]:
        return 3, say(
            "Codex's folder trust dialog is visible, but the accepting option "
            "could not be confirmed. Inspect the pane:\n"
            f"               thurbox-cli session capture {uuid}\n"
            "             Answer it yourself, then retry the queue prompt. "
            "Nothing was typed into this dialog and the brief was not sent.",
            "unconfirmed",
        )

    def answer(i: int) -> tuple[tuple[int, str] | None, str]:
        """Answer one gate, then confirm it took: (the failure or None, the keys sent).

        A send that reports success is not proof the dialog was answered. The
        dialog being GONE is. On either failure nothing more is sent.
        """
        signature, keys = gates[i]
        if keys == "down enter" and YES_SELECTED.search(pane(uuid)):
            keys = "enter"
        for k in keys.split():
            if not send_key(uuid, k):
                return (3, say(f"could not send '{k}' to the pane; the dialog is still up",
                               "send-failed")), keys
            time.sleep(1)
        for _ in range(10):
            if not shows(signature, pane(uuid)):
                return None, keys
            time.sleep(1)
        return (3, say(
            f"sent '{keys}' but {agent}'s dialog is still on the pane. Do not\n"
            "             prompt this session; look at it:\n"
            f"               thurbox-cli session capture {uuid}\n"
            "             The config-seeding fallback is:\n"
            f"               uv run --project {os.path.dirname(os.path.dirname(HERE))} fleet trust-thurbox-dir"
            " <that session's worktree path>\n"
            "             — which seeds THIS machine. For a session on a remote host, run\n"
            "             that command ON THE HOST, against the worktree path there.",
            "unconfirmed",
        )), keys

    # --- watch, answering every dialog in turn -------------------------------
    #
    # Another dialog can come up BEHIND the one just answered — Claude Code
    # shows folder trust, then external imports — and returning after the first
    # would hand the send to the second. So after each answer the pane is
    # watched for SETTLE more seconds. Codex keeps that watch even when a
    # composer or hook report appears first: its hook-review menu may follow.
    answered: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if hook_review_on_pane():
            return hook_review_blocked()
        i = gate_on_pane()
        if i is not None:
            if len(answered) >= MAX_ANSWERS:
                return 3, say(
                    f"answered {len(answered)} dialogs ({', '.join(answered)}) and another"
                    " is still coming.\n"
                    "             Nothing more was sent. Look at the pane before prompting it:\n"
                    f"               thurbox-cli session capture {uuid}",
                    "unconfirmed",
                )
            failed, sent = answer(i)
            if failed:
                return failed
            answered.append(f"'{sent}'")
            deadline = time.monotonic() + SETTLE
            continue
        if unanswerable_codex_folder_on_pane():
            return folder_blocked()
        if answered and is_codex:
            time.sleep(1)
            continue
        if is_codex and codex_composer_ready(uuid):
            break
        if agent_reported(uuid):
            break
        time.sleep(1)

    if hook_review_on_pane():
        return hook_review_blocked()
    if gate_on_pane() is not None:
        return 3, say(
            f"{agent}'s trust dialog is still on the pane; nothing was sent",
            "unconfirmed",
        )
    if unanswerable_codex_folder_on_pane():
        return folder_blocked()
    if answered:
        return 0, say(f"answered {agent}'s dialog(s) with {', '.join(answered)}; "
                      "none is left on the pane", "answered")
    if agent_reported(uuid):
        return 0, say(f"no dialog: {agent} is already reporting; nothing sent", "ready")
    if is_codex and codex_composer_ready(uuid):
        return 0, say(f"no dialog: {agent}'s composer is ready; nothing sent", "ready")
    if not gates:
        return 0, say(f"{agent} shows no trust dialog in a git worktree; nothing sent", "ready")
    return 3, say(
        f"no trust dialog seen in {timeout}s and {agent} has not reported.\n"
        "             Nothing was sent. Look at the pane before prompting it:\n"
        f"               thurbox-cli session capture {uuid}",
        "unconfirmed",
    )


def main(argv: list | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    timeout, as_json, session = 20, False, ""
    while args:
        arg = args.pop(0)
        if arg in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        if arg == "--timeout":
            value = args.pop(0) if args else "20"
            try:
                timeout = int(value)
            except ValueError:
                print(f"error: --timeout wants whole seconds, not {value!r}", file=sys.stderr)
                return 2
        elif arg == "--json":
            as_json = True
        elif arg.startswith("-"):
            print(f"error: unknown option {arg}", file=sys.stderr)
            return 2
        else:
            session = arg
    if not session:
        print(__doc__.strip())
        return 2
    if shutil.which("thurbox-cli") is None:
        print("error: thurbox-cli not found", file=sys.stderr)
        return 2
    code, report = answer_dialogs(session, timeout, as_json)
    print(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
