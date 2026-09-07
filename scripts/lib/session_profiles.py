#!/usr/bin/env python3
"""Render one session profile into `thurbox-cli session create` flags.

Called by scripts/session-flags.sh, never on its own — that wrapper owns the
argument handling and the usage message. Two modes:

    session_profiles.py <path> --check       validate every profile, print a
                                             one-line summary
    session_profiles.py <path> <profile>     validate every profile, then
                                             write that one's flags to stdout

Flags are written NUL-separated so a value may contain anything execve
accepts — a `--arg` is frequently a whole command line, and a line-based
protocol would corrupt one that spans lines. The caller reads them with
`mapfile -d '' -t`.

Both modes validate the whole file. A profile that breaks a rule means the
file is broken, and finding that out while rendering a different profile is
better than finding it out when a worker starts wrong.
"""

import re
import sys

import yaml

ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROFILE_KEYS = ("env", "command", "args", "reports_as")


def scalar(value, where, errors):
    """A YAML scalar as the string execve will see, or None with an error.

    `true` and `null` are refused rather than rendered: Python spells them
    `True` and `None`, which is not what anyone writing YAML meant. Quoting
    says what was meant, so the message asks for that.
    """
    if isinstance(value, bool) or value is None:
        errors.append(f"{where}: quote the value — {value!r} is not a string")
        return None
    if isinstance(value, (str, int, float)):
        return str(value)
    errors.append(f"{where}: expected a scalar, got {type(value).__name__}")
    return None


def check_profile(name, profile, errors):
    where = f"profiles.{name}"
    if not isinstance(profile, dict):
        errors.append(f"{where}: expected a mapping")
        return

    unknown = sorted(set(profile) - set(PROFILE_KEYS))
    if unknown:
        errors.append(f"{where}: unknown key(s) {', '.join(unknown)}")

    env = profile.get("env") or {}
    if not isinstance(env, dict):
        errors.append(f"{where}.env: expected a mapping of KEY: VALUE")
    else:
        for key, value in env.items():
            if not isinstance(key, str) or not ENV_KEY.match(key):
                errors.append(f"{where}.env: {key!r} is not a valid variable name")
                continue
            # thurbox's own identity vars always win over --env, so accepting
            # one here would emit a flag that is silently discarded.
            if key.startswith("THURBOX_"):
                errors.append(
                    f"{where}.env: {key} is thurbox's to set — it always wins "
                    "over --env, so setting it here would do nothing"
                )
                continue
            scalar(value, f"{where}.env.{key}", errors)

    command = profile.get("command")
    if command is not None:
        scalar(command, f"{where}.command", errors)
        # A --command session is named after the command's file stem, so
        # thurbox reads hook coverage against `sh` rather than against the
        # agent in the pane: coverage `none`, no reportable states, and a
        # working session rendering as `uncovered`. --reports-as is the
        # declaration that fixes it, which is why one never ships without it.
        if not profile.get("reports_as"):
            errors.append(
                f"{where}: command needs reports_as — without it thurbox reads "
                "hook coverage against the command, not the agent in the pane"
            )

    args = profile.get("args") or []
    if not isinstance(args, list):
        errors.append(f"{where}.args: expected a list")
    else:
        if args and command is None:
            errors.append(f"{where}.args: args without command has nothing to pass")
        for i, arg in enumerate(args):
            scalar(arg, f"{where}.args[{i}]", errors)

    reports_as = profile.get("reports_as")
    if reports_as is not None:
        scalar(reports_as, f"{where}.reports_as", errors)


def render(profile):
    flags = []
    for key, value in (profile.get("env") or {}).items():
        flags += ["--env", f"{key}={value}"]
    if profile.get("command") is not None:
        flags += ["--command", str(profile["command"])]
    for arg in profile.get("args") or []:
        flags += ["--arg", str(arg)]
    if profile.get("reports_as") is not None:
        flags += ["--reports-as", str(profile["reports_as"])]
    return flags


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: session_profiles.py <path> --check|<profile>", file=sys.stderr)
        return 2
    path, wanted = sys.argv[1], sys.argv[2]

    try:
        with open(path) as fh:
            doc = yaml.safe_load(fh)
    except OSError as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return 1
    except yaml.YAMLError as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return 1

    errors: list[str] = []
    if not isinstance(doc, dict) or not isinstance(doc.get("profiles"), dict):
        print(f"{path}: expected a top-level `profiles:` mapping", file=sys.stderr)
        return 1
    unknown = sorted(set(doc) - {"profiles"})
    if unknown:
        errors.append(f"unknown top-level key(s) {', '.join(unknown)}")

    profiles = doc["profiles"]
    for name, profile in profiles.items():
        check_profile(name, profile, errors)

    if errors:
        for error in errors:
            print(f"::error file={path}::{error}", file=sys.stderr)
        return 1

    if wanted == "--check":
        print(f"profiles ok: {len(profiles)} in {path}")
        return 0

    if wanted not in profiles:
        known = ", ".join(sorted(profiles)) or "none"
        print(f"{path}: no profile {wanted!r} (have: {known})", file=sys.stderr)
        return 1

    sys.stdout.write("".join(f"{flag}\0" for flag in render(profiles[wanted])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
