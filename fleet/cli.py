"""`fleet`: one command, the same on Linux and on native Windows.

    uv run fleet queue <verb> ...   scripts/lib/queue.py
    uv run fleet status ...         scripts/lib/fleet_status.py
    uv run fleet check ...          scripts/lib/check.py

A group names a module that already has a `main(argv) -> int`. This loads it by
path and hands it the rest of the argv, the way those modules already load each
other (`_load_sibling` in scripts/lib/queue.py), so nothing moved: every record is
read and written by the same code as before. `scripts/queue.sh` and
`scripts/fleet-status.sh` forward here, which is why their usage text still
says `queue.sh` and `fleet-status.sh`.
"""

from __future__ import annotations

import importlib.util
import os
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "lib")

GROUPS = {
    "queue": ("queue.py", "the task queue: topics, tasks, dispatch and completion"),
    "status": ("fleet_status.py", "one reading of the whole fleet"),
    "reconcile": ("reconcile.py", "the supervised loop that keeps the queue moving"),
    "check": ("check.py", "the gate: every check, or the named ones"),
    "install": ("install.py", "set this checkout up: dependencies, skills link, extension"),
    "preflight": ("preflight.py", "every dependency fleet needs, and how to install it"),
    "discover-owners": ("discover_owners.py", "the GitHub owners this machine can reach"),
    "add-owner": ("add_owner.py", "add owners the current gh accounts reach to the map"),
    "sync-registry": ("sync_registry.py", "rebuild the generated repo map"),
    "sync-checkout": ("sync_checkout.py", "fast-forward this checkout from origin"),
    "install-extension": ("install_extension.py", "render and install the thurbox extension and pane"),
    "place-pane": ("place_pane.py", "place the queue pane in layout.lua"),
    "pane-ask": ("pane_ask.py", "ask once whether to place the queue pane"),
    "voice-ask": ("voice_ask.py", "ask once for the names the lead uses"),
    "trust-thurbox-dir": ("trust_thurbox_dir.py", "trust a directory for thurbox's agent"),
    "session-flags": ("session_profiles.py", "render a session profile into session create flags"),
    "session-trust": ("session_trust.py", "answer a new session's trust dialog"),
    "paths": ("fleet_platform.py", "where thurbox's config and fleet's data live here"),
}

USAGE = "usage: fleet <group> [args...]\n\ngroups:\n" + "".join(
    f"  {name:<19}{about}\n" for name, (_, about) in GROUPS.items()
)


def load(filename: str):
    """The module in scripts/lib/, keyed in sys.modules as its loaders key it."""
    name = "fleet_" + filename.removesuffix(".py").removeprefix("fleet_")
    spec = importlib.util.spec_from_file_location(name, os.path.join(LIB, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    # UTF-8 whatever the console's code page. A Windows console defaults to
    # cp1252, where `—` comes out as a different byte and `🚀` raises before
    # its line is written; here, once, rather than in every module.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        sys.stderr.write(USAGE)
        return 2
    if argv[0] in ("-h", "--help"):
        sys.stdout.write(USAGE)
        return 0
    group, rest = argv[0], argv[1:]
    if group not in GROUPS:
        sys.stderr.write(f"fleet: no group {group!r}\n\n{USAGE}")
        return 2
    return load(GROUPS[group][0]).main(rest)
