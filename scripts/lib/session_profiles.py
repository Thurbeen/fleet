#!/usr/bin/env python3
"""Render one session profile into `thurbox-cli session create` flags.

Called by scripts/session-flags.sh, never on its own — that wrapper owns the
argument handling and the usage message. Two modes:

    session_profiles.py <path> --check       validate every profile, print a
                                             one-line summary
    session_profiles.py <path> <profile>     validate every profile, then
                                             write that one's flags to stdout

TWO LAYERS, not one. `<path>` holds the defaults the TEMPLATE ships and is
tracked; `<path>` with a `.local.yaml` suffix holds this instance's overrides
and is gitignored. A profile named in the local file REPLACES the shipped one
of that name wholesale — not key by key, because a half-overridden profile is
the kind of thing nobody can predict from reading either file. A name only the
local file has is simply added.

A replacement is ANNOUNCED on stderr, in both modes. Silent precedence is how
someone loses an hour to an override that did nothing, or to a template change
that did nothing; naming the shadowed profiles costs one line and answers both.

The `.local.example.yaml` sibling, if present, is validated and then discarded:
it is the tracked copy that documents the schema, so it has to stay correct
without ever contributing a profile.

That split exists so tuning a profile does not put a commit on `main`. The
tracked tree of an instance stays identical to the template's, which is what
keeps scripts/update-from-template.sh a fast-forward rather than a merge that
can conflict on settings someone tuned deliberately. .gitignore's header owns
the full reasoning.

Each layer is validated on its own, before the merge, so an error names the
file it is actually in.

Flags are written NUL-separated so a value may contain anything execve
accepts — a `--arg` is frequently a whole command line, and a line-based
protocol would corrupt one that spans lines. The caller reads them with
`mapfile -d '' -t`.

Both modes validate BOTH layers in full. A profile that breaks a rule means
the file is broken, and finding that out while rendering a different profile is
better than finding it out when a worker starts wrong.
"""

import os
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


def local_path(path, suffix=".local.yaml"):
    """A sibling of the tracked defaults: the override file, or its example."""
    base = path[: -len(".yaml")] if path.endswith(".yaml") else path
    return f"{base}{suffix}"


def load_layer(path, errors, required):
    """Parse and validate one layer. Returns its `profiles` mapping, or None.

    A missing REQUIRED layer is an error; a missing optional one is the normal
    state of an instance that has not overridden anything.
    """
    try:
        with open(path) as fh:
            doc = yaml.safe_load(fh)
    except FileNotFoundError:
        if required:
            print(f"{path}: no such file", file=sys.stderr)
            return None
        return {}
    except OSError as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return None
    except yaml.YAMLError as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return None

    # An override file that exists but is empty is a no-op, not a broken file.
    if doc is None and not required:
        return {}
    if not isinstance(doc, dict) or not isinstance(doc.get("profiles"), dict):
        print(f"{path}: expected a top-level `profiles:` mapping", file=sys.stderr)
        return None

    unknown = sorted(set(doc) - {"profiles"})
    if unknown:
        errors.append(f"{path}: unknown top-level key(s) {', '.join(unknown)}")

    layer_errors: list[str] = []
    for name, profile in doc["profiles"].items():
        check_profile(name, profile, layer_errors)
    errors.extend(f"{path}: {error}" for error in layer_errors)
    return doc["profiles"]


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: session_profiles.py <path> --check|<profile>", file=sys.stderr)
        return 2
    path, wanted = sys.argv[1], sys.argv[2]

    errors: list[str] = []
    profiles = load_layer(path, errors, required=True)
    if profiles is None:
        return 1

    overrides_file = local_path(path)
    overrides = load_layer(overrides_file, errors, required=False)
    if overrides is None:
        return 1

    # The tracked example ships the schema and must not rot: hold it to the same
    # rules, then throw it away. It never contributes a profile.
    example_file = local_path(path, ".local.example.yaml")
    if os.path.exists(example_file) and load_layer(example_file, errors, False) is None:
        return 1

    if errors:
        for error in errors:
            print(f"::error::{error}", file=sys.stderr)
        return 1

    # Wholesale per name: your `sweep` is your `sweep`, not a blend of two.
    shadowed = sorted(set(profiles) & set(overrides))
    profiles = {**profiles, **overrides}

    # Precedence is never silent. Someone debugging "why did my override do
    # nothing" — or "why did the template's change do nothing" — is answered
    # here rather than by reading two files and guessing which won.
    if shadowed:
        print(
            f"note: {overrides_file} replaces shipped profile(s): "
            f"{', '.join(shadowed)}",
            file=sys.stderr,
        )

    if wanted == "--check":
        summary = f"profiles ok: {len(profiles)} in {path}"
        if overrides:
            added = sorted(set(overrides) - set(shadowed))
            detail = []
            if shadowed:
                detail.append(f"{len(shadowed)} replaced")
            if added:
                detail.append(f"{len(added)} added")
            summary += f" (+{overrides_file}: {', '.join(detail)})"
        print(summary)
        return 0

    if wanted not in profiles:
        known = ", ".join(sorted(profiles)) or "none"
        print(f"{path}: no profile {wanted!r} (have: {known})", file=sys.stderr)
        return 1

    sys.stdout.write("".join(f"{flag}\0" for flag in render(profiles[wanted])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
