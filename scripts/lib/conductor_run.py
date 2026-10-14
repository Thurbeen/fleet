#!/usr/bin/env python3
"""Run the conductor ONCE, headless, for one notice: the one-shot conductor.

WHY ONE RUN PER NOTICE AND NOT A SESSION. Everything the conductor needs is
already on disk: the queue's records, `plan`, what the loop has told it
(`notified.json`) and this module's own journal. A session remembers nothing a
prompt cannot hand it, and costs three things a run does not:

  IT FREEZES ITS INSTRUCTIONS.  A session reads `CONDUCTOR.md` and the skills
      once, at launch, so every sync that touches them needs a restart. A run
      reads them from the checkout every time.
  IT CAN ANSWER WITHOUT LOOKING.  On 2026-10-14 the conductor session, asked
      about a merge conflict, answered from the notices it had seen ("never
      looked at that task's branch") rather than from the fleet. A run is
      handed the state, read the moment before it starts, in its prompt: there
      is nothing to answer from but the fleet as it is.
  IT HAS TO BE TYPED INTO.  At rest only, into a provably empty line, else
      the mailbox: `notify_delivery.py`'s rules exist because a session is a
      terminal somebody may be using. A run is started, and has no line.

And a session's context grows until something compacts it, at a moment nobody
chose. A run starts empty.

WHAT A RUN IS. `notify_conductor.py`, the loop's notifier, calls `run` with the
one line it would have typed. This builds a prompt — the notice, the last runs
from the journal, the state digest (`fleet status`, `queue plan`, `queue list
--live`, `queue prs`, each read now), then `CONDUCTOR.md` and the
fleet-conductor skill read fresh from the checkout — and runs the conductor's
agent on it, headless, in `orchestration/conductor/`: the generated directory
whose settings keep the lead's CLAUDE.md out. What the run does is its own
`uv run fleet …` commands; what it says is journalled and logged, and reaches
nobody.

WHICH AGENT, AND AS WHOM. `CONDUCTOR_AGENT` in `agent.conf`, else `AGENT`, else
`claude`. Thurbox is asked how it would launch that agent (`thurbox-cli agent
launch-args`), so a registered wrapper — an agent that is a second account, or
a role — runs as itself and fleet restates none of it. Its `args` are left
off: they are an interactive session's status hooks, and a run reports to no
session. Then the HEADLESS arguments, which are a fact about the agent fleet
WATCHED and so resolve through `LIKE` (`agent_settings.py`):

  claude  `-p`, JSON out, no session kept, `--permission-mode auto`: every
          action is reviewed by Claude Code's own classifier, and none waits
          for a person. Pre-approved on top, so a READ never costs a
          review: fleet's read verbs, reading sessions, capturing a pane,
          reading files. Every verb that acts — `dispatch`, `send`, `prompt`,
          `refuel`, `block`, `declare`, `abandon`, a `message send` — is left
          to the review, and that is the point of it. Measured on Claude Code 2.1.300: `-p`
          never stops on a prompt, and an action the classifier refuses is
          recorded in `permission_denials`, which the journal keeps. Edit,
          Write and NotebookEdit are removed outright: the conductor edits no
          file. Auto and not a bare allowlist, because the prompt carries text
          fleet did not write — a pull request's title, a worker's question —
          and a reviewed action is the one an injected line cannot simply ask
          for.
  codex   `exec --approve-for-me` — the workspace-write sandbox, with every
          escalation decided by automatic review and never a person, which is
          codex's auto mode — plus
          network access, which `uv` and the forge CLIs need, `--ephemeral`
          and the last message to a file.

An agent that is neither, and `LIKE` neither, has no headless form fleet has
watched: `<agent>.HEADLESS=` in `agent.conf` gives it one, and is also how an
operator overrides either row. `{last}` in it is the file the run's last
message is read from. Without one, `run` fails and says so, and the notifier
tells the lead instead.

ONE AT A TIME, BY CONSTRUCTION. The loop holds its checkout's lock and runs its
notifier in the foreground, so two runs never overlap. Each is held to
`CONDUCTOR_SECS` (`reconcile.conf`, default 300), never more than the
notifier's own bound less the digest's, and is killed with its tree past it —
the rule every child of the loop keeps. A run cut off mid-`dispatch` is
`dispatch`'s to recover: its per-task lock and its adoption of an unrecorded
session are for exactly that.

TOLD MEANS RAN. A run that exits 0 and does not report an error has been told,
whatever it decided; one that fails, or is killed, has not, and the notifier
hands the same line to the lead instead — a notice nobody receives is the six
hours `notify_conductor.py` exists to prevent. A refused command is not a
failed run: the run decided around it, and the journal and the log say which.

THE JOURNAL is `conductor.jsonl` in the reconciler's runtime directory, one line
per run: when, the notice, whether it ran, what it said, what it was refused.
It is the conductor's only memory of itself, and the next run reads its tail.
Runtime state, never a record, for the reason every file there is.

Usage: it is `notify_conductor.py`'s, and nothing else's. `mode()` is also
`install_extension.py`'s, which renders what each mode needs.

Environment:
  FLEET_RECONCILE_CONDUCTOR_SECS  the most one run may take, over CONDUCTOR_SECS
  FLEET_CONDUCTOR_DIR             the directory a run starts in, instead of the
                                  checkout's rendered one; the gate's, which
                                  may not run in the operator's
  FLEET_NAME_ROOT                 where `fleet.conf` is read, as everywhere

Requires: the conductor's agent on PATH, and `thurbox-cli` to resolve it (a
built-in agent runs as its own command without it).
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys


def _load_sibling(name: str, filename: str):
    """A scripts/lib module under a `fleet_` key, never shadowing the standard library."""
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fleet_platform = _load_sibling("fleet_platform", "fleet_platform.py")
agent_settings = _load_sibling("fleet_agent_settings", "agent_settings.py")

CHECKOUT = fleet_platform.checkout_dir()
CONDUCTOR_DIR = os.path.join("orchestration", "conductor")
SKILL = os.path.join(".agents", "skills", "fleet-conductor", "SKILL.md")
JOURNAL = "conductor.jsonl"
LAST = "conductor.last"
# How a run is started: the same boot `reconcile.py` runs `fleet` by.
BOOT = "import sys\nsys.path.insert(0, sys.argv[1])\nfrom fleet.cli import main\nsys.exit(main(sys.argv[2:]))"

# The default bound, argued in the docstring: a dispatch of a large ready set
# is minutes, and an idle run is seconds.
CONDUCTOR_SECS = 300
# What the digest may take, all four reads together, and what one read may.
DIGEST_SECS = 60
# The journal kept, and the tail of it a run is shown.
JOURNAL_KEEP, JOURNAL_SHOWN = 200, 8
# The most of one run's last message the journal keeps.
SAID_CHARS = 1500

# The reads every run is handed, in the order they are shown.
DIGEST = (
    ("status",),
    ("queue", "plan"),
    ("queue", "list", "--live"),
    ("queue", "prs"),
)

# What a claude run may do without a review: READS ONLY. The prompt carries
# text fleet did not write — a pull request's title, a worker's question — so
# a pre-approved verb that acts is one an injected line could run unreviewed.
# Everything that changes what runs or reaches a session (`dispatch`, `send`,
# `abandon`, `message send`, …) is the auto-mode classifier's to allow.
CLAUDE_ALLOWED = (
    "Read", "Grep", "Glob",
    "Bash(uv run fleet status:*)",
    "Bash(uv run fleet context:*)",
    "Bash(uv run fleet queue plan:*)",
    "Bash(uv run fleet queue list:*)",
    "Bash(uv run fleet queue prs:*)",
    "Bash(uv run fleet queue show:*)",
    "Bash(thurbox-cli session list:*)",
    "Bash(thurbox-cli session get:*)",
    "Bash(thurbox-cli session capture:*)",
    "Bash(git log:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git show:*)",
)

# The headless arguments of each agent fleet has watched run headless, after
# thurbox's command for it. `{last}` is the file a run's last message lands in.
HEADLESS = {
    "claude": [
        "-p", "--output-format", "json", "--no-session-persistence",
        "--permission-mode", "auto",
        "--allowedTools", ",".join(CLAUDE_ALLOWED),
        "--disallowedTools", "Edit,Write,NotebookEdit",
    ],
    "codex": [
        "exec", "--approve-for-me", "-c", "sandbox_workspace_write.network_access=true",
        "--ephemeral", "-o", "{last}", "-",
    ],
}


# --- which mode ----------------------------------------------------------------


def fleet_conf() -> str:
    """The operator's `fleet.conf`, else the tracked example — where NAME and CONDUCTOR live."""
    root = os.environ.get("FLEET_NAME_ROOT") or CHECKOUT
    own = os.path.join(root, "orchestration", "fleet.conf")
    return own if os.path.isfile(own) else os.path.join(root, "orchestration", "fleet.example.conf")


MODES = {"": "", "off": "", "on": "oneshot", "session": "session"}


def mode() -> str:
    """"oneshot" for `CONDUCTOR=on`, "session" for `CONDUCTOR=session`, "" for none.

    A value this does not know is "" here, which is the lead conducting as it
    always did; `install-extension` refuses it outright, so it never renders.
    """
    said = agent_settings.read_conf(fleet_conf()).get("CONDUCTOR", "").strip().lower()
    return MODES.get(said, "")


# --- which agent, and how ------------------------------------------------------


def conductor_agent(settings: dict) -> str:
    """`CONDUCTOR_AGENT`, else `AGENT`, else `claude`: the rule the manifest renders by."""
    return (settings.get("CONDUCTOR_AGENT", "").strip() or settings.get("AGENT", "").strip() or "claude")


def launch(agent: str) -> tuple[list, dict]:
    """(the command thurbox would launch `agent` with, the environment it adds).

    `args` are not part of it; see WHICH AGENT. A thurbox that cannot be asked
    leaves the agent's own name as its command, which is right for the two
    built-in agents and is the agent's own error for anything else.
    """
    if not shutil.which("thurbox-cli"):
        return [agent], {}
    try:
        done = subprocess.run(["thurbox-cli", "agent", "launch-args", agent, "--json"],
                              capture_output=True, text=True, encoding="utf-8", timeout=15)
        doc = json.loads(done.stdout) if done.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        doc = {}
    command = str(doc.get("command") or "").strip() if isinstance(doc, dict) else ""
    if not command:
        return [agent], {}
    env = doc.get("env") if isinstance(doc.get("env"), dict) else {}
    return [command], {str(k): str(v) for k, v in env.items()}


def headless(agent: str, settings: dict) -> tuple[list | None, str]:
    """(this agent's headless arguments, the watched agent they are) — (None, "") for none.

    The agent's own `HEADLESS` line first, then its `LIKE`'s, then the built-in
    row of the watched agent its chain reaches."""
    said = agent_settings.named("HEADLESS", agent, settings)
    kind = next((name for name in agent_settings.chain(agent, settings) if name in HEADLESS), "")
    if said:
        try:
            return fleet_platform.split_command(said), kind
        except ValueError:
            return None, kind
    return (list(HEADLESS[kind]), kind) if kind else (None, "")


# --- the prompt ----------------------------------------------------------------


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def voiced(text: str) -> str:
    """`CONDUCTOR.md` with the two names `voice.conf` gives, as install renders it."""
    root = os.environ.get("FLEET_NAME_ROOT") or CHECKOUT
    conf = os.environ.get("FLEET_VOICE_CONF") or ""
    if not conf:
        own = os.path.join(root, "orchestration", "voice.conf")
        conf = own if os.path.isfile(own) else os.path.join(root, "orchestration", "voice.example.conf")
    names = agent_settings.read_conf(conf)
    for key in ("OPERATOR_NAME", "ASSISTANT_NAME"):
        if names.get(key):
            text = text.replace(f"@{key}@", names[key])
    return text


def digest(secs: float) -> str:
    """The fleet's state, read now: each read's output, or why it has none."""
    parts = []
    each = max(5.0, secs / len(DIGEST))
    for args in DIGEST:
        argv = [sys.executable, "-c", BOOT, CHECKOUT, *args]
        try:
            done = fleet_platform.run_bounded(argv, each, capture_output=True, stdin=subprocess.DEVNULL,
                                              cwd=CHECKOUT, text=True, encoding="utf-8", errors="replace")
        except OSError as exc:
            out = f"(could not run: {exc})"
        else:
            if done is None:
                out = f"(killed after {each:.0f}s)"
            else:
                out = (done.stdout or "").strip() or "(nothing)"
                if done.returncode != 0:
                    out += f"\n(exit {done.returncode}: {(done.stderr or '').strip()[-300:]})"
        parts.append(f"### uv run fleet {' '.join(args)}\n\n```\n{out}\n```")
    return "\n\n".join(parts)


def journal_tail(state_dir: str, n: int = JOURNAL_SHOWN) -> list[dict]:
    lines = read_text(os.path.join(state_dir, JOURNAL)).splitlines()
    out = []
    for line in lines[-n:]:
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict):
            out.append(doc)
    return out


def prompt(state_dir: str, notice: str, state: str) -> str:
    """Everything one run is handed: what happened, what it did before, the
    fleet now, and its standing instructions — the last two read this moment."""
    runs = journal_tail(state_dir)
    past = "\n".join(
        f"- {r.get('at', '?')} {'ran' if r.get('ok') else 'FAILED'}: {r.get('notice', '')}"
        + (f"\n  you said: {' '.join(str(r.get('said') or '').split())[:400]}" if r.get("said") else "")
        + (f"\n  refused: {', '.join(r['denied'])}" if r.get("denied") else "")
        for r in runs
    ) or "(none — this is the first run this loop journalled)"
    return "\n\n".join((
        "You are this fleet's CONDUCTOR, run once, headless, for the notice below. "
        "Nobody reads what you print except the reconciler's log and your journal: "
        "act with commands, then end with one line per thing you did or held, "
        "in the state words CONDUCTOR.md asks for. "
        "The state digest below was read the moment before you started; read "
        "again before you act on anything it does not show.",
        f"# The notice\n\n{notice}",
        f"# Your last runs, newest last\n\n{past}",
        f"# The fleet now\n\n{state}",
        f"# Your standing instructions: CONDUCTOR.md\n\n{voiced(read_text(os.path.join(CHECKOUT, 'CONDUCTOR.md')))}",
        f"# Your role: {SKILL.replace(os.sep, '/')}\n\n{read_text(os.path.join(CHECKOUT, SKILL))}",
    ))


# --- the run -------------------------------------------------------------------


def bound(outer: float) -> float:
    """CONDUCTOR_SECS, never more than the notifier's own bound less the digest's."""
    raw = os.environ.get("FLEET_RECONCILE_CONDUCTOR_SECS", "").strip()
    if not raw:
        raw = _load_sibling("fleet_queue", "queue.py").reconcile_conf().get("CONDUCTOR_SECS", "").strip()
    try:
        secs = float(raw) if raw else CONDUCTOR_SECS
    except ValueError:
        secs = CONDUCTOR_SECS
    return max(10.0, min(secs, outer - DIGEST_SECS - 30))


def outcome(kind: str, done, last: str) -> tuple[bool, str, str, list]:
    """(ran, why not, what it said, what it was refused) from one finished run."""
    out = (done.stdout or "").strip()
    if kind == "claude" and out.startswith("{"):
        try:
            doc = json.loads(out)
        except ValueError:
            doc = {}
        said = str(doc.get("result") or "")
        denied = [str((d.get("tool_input") or {}).get("command") or d.get("tool_name"))
                  for d in doc.get("permission_denials") or [] if isinstance(d, dict)]
        if done.returncode != 0 or doc.get("is_error"):
            return False, f"exit {done.returncode}: {doc.get('subtype') or said[:200]}", said, denied
        return True, "", said, denied
    said = read_text(last).strip() if kind == "codex" else out[-SAID_CHARS:]
    if done.returncode != 0:
        tail = ((done.stderr or "") + out).strip().splitlines()
        return False, f"exit {done.returncode}" + (f": {tail[-1][:200]}" if tail else ""), said, []
    return True, "", said, []


def journal(state_dir: str, entry: dict) -> None:
    """Append one run, keeping the last JOURNAL_KEEP; into the runtime directory, never making it."""
    if not os.path.isdir(state_dir):
        return
    path = os.path.join(state_dir, JOURNAL)
    lines = read_text(path).splitlines()[-(JOURNAL_KEEP - 1):]
    lines.append(json.dumps(entry, ensure_ascii=False))
    try:
        fleet_platform.write_record(path, "\n".join(lines) + "\n")
    except OSError:
        pass


def run(state_dir: str, notice: str, outer: float = 600) -> tuple[bool, str]:
    """Run the conductor once on `notice`: (it ran, why not / what it was refused).

    Never raises: what the notifier does with a False is tell the lead."""
    settings = agent_settings.conf()
    agent = conductor_agent(settings)
    args, kind = headless(agent, settings)
    if args is None:
        return False, (f"agent {agent!r} has no headless form fleet knows: it is LIKE neither claude "
                       f"nor codex, and agent.conf names no {agent}.HEADLESS")
    home = os.environ.get("FLEET_CONDUCTOR_DIR") or os.path.join(CHECKOUT, CONDUCTOR_DIR)
    if not os.path.isdir(home):
        return False, f"{home} is not rendered; run uv run fleet install-extension"
    last = os.path.join(state_dir, LAST)
    try:
        os.remove(last)
    except OSError:
        pass
    command, env = launch(agent)
    argv = command + [a.replace("{last}", last) for a in args]
    text = prompt(state_dir, notice, digest(DIGEST_SECS))
    secs = bound(outer)
    began = datetime.datetime.now(datetime.timezone.utc)
    child = {**os.environ, **agent_settings.account_env(agent, settings), **env}
    try:
        done = fleet_platform.run_bounded(argv, secs, input=text, capture_output=True, cwd=home, env=child,
                                          text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        done, ok, why, said, denied = None, False, f"could not start {argv[0]}: {exc}", "", []
    else:
        if done is None:
            ok, why, said, denied = False, f"killed after {secs:.0f}s, its bound", "", []
        else:
            ok, why, said, denied = outcome(kind, done, last)
    journal(state_dir, {
        "at": began.isoformat(timespec="seconds"), "agent": agent, "notice": notice, "ok": ok,
        "why": why, "secs": round((datetime.datetime.now(datetime.timezone.utc) - began).total_seconds(), 1),
        "said": said[:SAID_CHARS], "denied": denied,
    })
    if not ok:
        return False, why
    return True, (f"refused {len(denied)}: {', '.join(denied)[:200]}" if denied else "")
