#!/usr/bin/env python3
"""Render one session profile into `thurbox-cli session create` flags.

Run as `uv run fleet session-flags`, and in-process by `queue.py dispatch`,
which calls `load_profiles` and `render` directly so that no shell stands
between a task and its profile:

    fleet session-flags                  the `default` profile
    fleet session-flags sweep            a named profile
    fleet session-flags --check          validate every profile

`orchestration/session-profiles.yaml` is read from this checkout whatever the
caller's directory is. The gate's older form names the file itself:

    session_profiles.py <path> --check|<profile>

A profile carrying `command` renders `--command`, so drop `--agent` from that
`session create` call: thurbox refuses both together.

ONE FILE, ONE LAYER. `<path>` holds every profile there is. There used to be a
gitignored `.local.yaml` overlay beside it, so a profile could be tuned without
touching a tracked file; that only mattered while this repo was a template
somebody pulled from, and the file is now simply the operator's to edit.

Flags are written NUL-separated so a value may contain anything execve
accepts — a `--arg` is frequently a whole command line, and a line-based
protocol would corrupt one that spans lines. A caller splits them on NUL.

Both modes validate EVERY profile, not just the one being rendered. A profile
that breaks a rule means the file is broken, and finding that out while
rendering a different profile is better than finding it out when a worker
starts wrong.
"""

import os
import re
import sys

import yaml

ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROFILE_KEYS = ("env", "command", "args", "reports_as", "uncovered")
UNCOVERED_NOTICE = (
    "uncovered — no hook family; watch, refuel and reap will not see this session"
)


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

    # `uncovered: true` is the other declaration beside reports_as: this
    # command has no hook family, the session will be uncovered, and that
    # is accepted. Silence — neither key — is still the trap rule 2 exists
    # to catch. YAML `true` only; a string or a false is not a declaration.
    declared = False
    if "uncovered" in profile:
        if profile["uncovered"] is True:
            declared = True
        else:
            errors.append(
                f"{where}.uncovered: expected true — this is the declaration "
                "that the session will be uncovered"
            )
    if declared and command is None:
        errors.append(f"{where}: uncovered without command has nothing to uncover")

    if command is not None:
        # A --command session is named after the command's file stem, so
        # thurbox reads hook coverage against `sh` rather than against the
        # agent in the pane: coverage `none`, no reportable states, and a
        # working session rendering as `uncovered`. --reports-as names the
        # family that fixes it; uncovered: true accepts that there is none.
        has_reports = profile.get("reports_as") is not None
        if declared and has_reports:
            errors.append(
                f"{where}: command has both reports_as and uncovered — pick "
                "one: declare the hook family, or accept that the session "
                "will be uncovered"
            )
        elif not declared and not has_reports:
            errors.append(
                f"{where}: command needs reports_as or uncovered — without "
                "one, thurbox reads hook coverage against the command, not "
                "the agent in the pane"
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


def uncovered_notice(flags):
    """What dispatch (and session-flags stderr) say about a spawn with no family.

    Derived from the flags that actually reach `session create`, not from the
    YAML key: after the gate, `--command` without `--reports-as` is the
    declared uncovered session.
    """
    if "--command" in flags and "--reports-as" not in flags:
        return UNCOVERED_NOTICE
    return None


def load_profiles(path, errors):
    """Parse and validate the profiles file. Returns its mapping, or None."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
    except FileNotFoundError:
        print(f"{path}: no such file", file=sys.stderr)
        return None
    except OSError as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return None
    except yaml.YAMLError as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return None

    if not isinstance(doc, dict) or not isinstance(doc.get("profiles"), dict):
        print(f"{path}: expected a top-level `profiles:` mapping", file=sys.stderr)
        return None

    unknown = sorted(set(doc) - {"profiles"})
    if unknown:
        errors.append(f"{path}: unknown top-level key(s) {', '.join(unknown)}")

    file_errors: list[str] = []
    for name, profile in doc["profiles"].items():
        check_profile(name, profile, file_errors)
    errors.extend(f"{path}: {error}" for error in file_errors)
    return doc["profiles"]


PROFILES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "orchestration", "session-profiles.yaml",
)


def parse_args(argv: list[str]) -> tuple[str, str] | int:
    """(path, wanted), or an exit code once the usage is dealt with."""
    if len(argv) == 2 and not argv[0].startswith("-") and argv[0].endswith((".yaml", ".yml")):
        return argv[0], argv[1]
    wanted = "default"
    for arg in argv:
        if arg in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        if arg == "--check":
            wanted = "--check"
        elif arg.startswith("-"):
            print(f"error: unknown option {arg!r} (want: --check)", file=sys.stderr)
            return 2
        else:
            wanted = arg
    return PROFILES, wanted


def main(argv: list[str] | None = None) -> int:
    parsed = parse_args(sys.argv[1:] if argv is None else argv)
    if isinstance(parsed, int):
        return parsed
    path, wanted = parsed

    errors: list[str] = []
    profiles = load_profiles(path, errors)
    if profiles is None:
        return 1

    if errors:
        for error in errors:
            print(f"::error::{error}", file=sys.stderr)
        return 1

    if wanted == "--check":
        shown = os.path.relpath(path, os.path.dirname(os.path.dirname(PROFILES))) if path == PROFILES else path
        print(f"profiles ok: {len(profiles)} in {shown}")
        return 0

    if wanted not in profiles:
        known = ", ".join(sorted(profiles)) or "none"
        print(f"{path}: no profile {wanted!r} (have: {known})", file=sys.stderr)
        return 1

    rendered = render(profiles[wanted])
    sys.stdout.write("".join(f"{flag}\0" for flag in rendered))
    if notice := uncovered_notice(rendered):
        print(notice, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
