#!/usr/bin/env python3
"""What fleet knows about ONE agent, asked per agent rather than per checkout.

Four things here are facts about an agent: the trust dialog it shows, the
sentence it prints at its limit, where it keeps its transcripts, and which
account it draws on. `orchestration/agent.conf` held all four as single
values, so a checkout could describe exactly one agent — and the second
account of the SAME agent (`<agent>-spare` beside `<agent>`) is a second agent
by every one of those four measures. Describing it meant editing the checkout's
one answer before each dispatch and putting it back afterwards.

THE PRECEDENT THIS CONTINUES is `AGENT_PROVIDERS` in that same file: one line
carrying `agent=provider` pairs, because which provider an agent draws on was
already a fact about the agent. This is that, for the rest of them.

THE SYNTAX is the file's own `KEY=value`, with the agent in front of the key:

    AGENT=my-agent                          # the checkout's, as before
    LIMIT_BANNER=                           # the checkout's, as before

    my-agent-spare.LIKE=my-agent            # same agent, another account
    my-agent-spare.ENV=AGENT_CONFIG_DIR=~/.my-agent-spare

No sections, no new grammar, no second file: a line without a dot is what it
always was, and a checkout with no dotted line behaves exactly as it did.

THE KEYS, and nothing else is per-agent:

  LIKE             this agent IS that one, under another account. The built-in
                   tables in `scripts/lib/queue.py` and
                   `scripts/lib/session_trust.py` are keyed by the agent fleet
                   WATCHED, so a second account of it answers to the same row
                   rather than to a guess — which is the whole reason a second
                   account was unserviceable.
  ENV              the account: `NAME=VALUE` pairs, comma separated. ONE record
                   read by every reader that needs it — `transcript_root()` for
                   the directory the records are in, `probe_fuel()` for the
                   environment `refuel`'s quota reading runs under, and
                   `fuel_accounts()` for the set of accounts `fleet status`
                   reads its FUEL section from — because "which account" is one
                   answer and two of them would drift. The screen was the last
                   reader to be wired up, and until it was it reported the
                   window of whichever account the LEAD ran as while the
                   workers spent another.
  TRUST_SIGNATURE  \\
  TRUST_KEYS        |  the four the checkout already held globally, now
  LIMIT_BANNER      |  sayable about one agent.
  TRANSCRIPT_DIR   /

HOW A VALUE IS RESOLVED, and it is one rule for every key: the agent's own
line, then the line of whatever it is `LIKE`, then the checkout-wide line of
the same name. MORE SPECIFIC WINS, and the global setting keeps the position
it has today — so a checkout that names no agent reads exactly what it read
before, and an operator who had set `LIMIT_BANNER` globally still has it.

WHAT IS NOT HERE. This teaches fleet how to BE TOLD about an agent; it adds no
entry to either built-in table. Those hold what fleet has watched, for the
reason `AGENT_LIMIT_SIGNALS` argues in place: a pattern invented for an agent
nobody watched hit its limit restarts a live worker mid-turn.

THE ONE READER of `orchestration/agent.conf`. `queue.py`, `fleet_status.py`
and `session_trust.py` all asked it their own way before — three parses, and
`session_trust.py`'s read only the first occurrence of a key. They go through
this now, so the per-agent rule is stated once and a change of syntax is a
change to this file.
"""

from __future__ import annotations

import os

# The operator's copy, and the tracked example that is the default when there
# is none — the two-file rule every `orchestration/*.conf` follows.
AGENT_CONF = "orchestration/agent.conf"
AGENT_CONF_DEFAULTS = "orchestration/agent.example.conf"

# The keys an agent may be named in front of. A dotted key outside this set is
# left alone: it is an ordinary setting whose name happens to carry a dot, and
# refusing one here would make this file the authority on what settings exist.
PER_AGENT = ("LIKE", "ENV", "TRUST_SIGNATURE", "TRUST_KEYS", "LIMIT_BANNER", "TRANSCRIPT_DIR")

# A `LIKE` chain is followed, so `a.LIKE=b` and `b.LIKE=c` resolve to `c`, but
# never further than this: a cycle an operator typed is a settings error, not a
# reason for fleet to hang.
LIKE_DEPTH = 8


def read_conf(path: str) -> dict[str, str]:
    """`KEY=value` lines, as data. An unreadable file is no settings at all.

    Read and never executed: a setting that can run is a different kind of
    file. The LAST occurrence of a key wins, because a line appended below a
    correct one is the edit a first-match read would wave through.
    """
    conf: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                conf[key.strip()] = value.strip()
    except OSError:
        return {}
    return conf


def conf_root(root: str | None = None) -> str:
    """The checkout whose agent settings are in force. `FLEET_AGENT_ROOT` relocates them."""
    if root:
        return root
    return os.environ.get("FLEET_AGENT_ROOT") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def conf(root: str | None = None) -> dict[str, str]:
    """The agent settings in force: the operator's copy, else the tracked example."""
    base = conf_root(root)
    path = os.path.join(base, AGENT_CONF)
    if not os.path.exists(path):
        path = os.path.join(base, AGENT_CONF_DEFAULTS)
    return read_conf(path)


def chain(agent: str | None, settings: dict[str, str]) -> list[str]:
    """This agent and every agent it is `LIKE`, nearest first.

    The list an ordinary lookup walks. An agent that is `LIKE` nothing is a
    list of one, and a cycle stops at the first name already on it.
    """
    name = (agent or "").strip()
    seen: list[str] = []
    while name and name not in seen and len(seen) < LIKE_DEPTH:
        seen.append(name)
        name = settings.get(f"{name}.LIKE", "").strip()
    return seen


def row(table, agent: str | None, settings: dict[str, str]):
    """This agent's entry in one of fleet's built-in tables, following `LIKE`.

    `AGENT_LIMIT_SIGNALS` and `session_trust.GATES` are keyed by the agent
    fleet WATCHED, so a second account of a watched agent has no key of its
    own. Saying it is `LIKE` that agent is what gives it the watched row —
    rather than fleet inventing one, which is the thing neither table may do.
    Returns None when nothing in the chain is in the table.
    """
    for name in chain(agent, settings):
        if name in table:
            return table[name]
    return None


def named(key: str, agent: str | None, settings: dict[str, str]) -> str:
    """The value some agent in the chain names for `key`, or "" — never the global one.

    The narrow lookup, for the one caller that must not fall back:
    `session_trust.py` puts a NAMED gate ahead of its built-in table, while the
    global `TRUST_SIGNATURE` stays where it has always been, behind it.
    """
    for name in chain(agent, settings):
        value = settings.get(f"{name}.{key}", "").strip()
        if value:
            return value
    return ""


def value(key: str, agent: str | None, settings: dict[str, str]) -> str:
    """What this agent's `key` is: its own line, its `LIKE`'s, then the checkout's."""
    return named(key, agent, settings) or settings.get(key, "").strip()


def account_env(agent: str | None, settings: dict[str, str]) -> dict[str, str]:
    """The environment this agent's account is read under, from its `ENV` line.

    `NAME=VALUE` pairs separated by commas — commas and not spaces, because
    every value here is a path and a path may hold a space. `~` is expanded,
    since the value an operator writes for a config directory is the one they
    would type in a shell.

    EMPTY IS THE ORDINARY ANSWER and means this agent runs on whatever account
    the machine is already signed in to, which is what every fleet before this
    assumed of every agent.
    """
    out: dict[str, str] = {}
    for pair in value("ENV", agent, settings).split(","):
        name, sep, raw = pair.partition("=")
        if sep and name.strip():
            out[name.strip()] = os.path.expanduser(raw.strip())
    return out


def child_env(agent: str | None, settings: dict[str, str]) -> dict[str, str] | None:
    """This process's environment with the agent's account laid over it, or None.

    None where the agent names no account, so a caller passes it straight to
    `subprocess.run(env=...)` and inherits exactly what it inherited before.
    """
    account = account_env(agent, settings)
    return {**os.environ, **account} if account else None


def agents(settings: dict[str, str]) -> list[str]:
    """Every agent the settings say anything about, in the file's own order."""
    out: list[str] = []
    for key in settings:
        name, _, tail = key.rpartition(".")
        if name and tail in PER_AGENT and name not in out:
            out.append(name)
    return out
