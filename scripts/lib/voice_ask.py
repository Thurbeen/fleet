"""Ask what the lead calls the operator and what it answers to — once, before
the extension renders them.

WHY IT EXISTS. `fleet install-extension` renders both names into
FLEET.rendered.md, the lead's standing context, from the operator's gitignored
`orchestration/voice.conf` or else the tracked `voice.example.conf`. Nothing
asked, so every install took the defaults. Onboarding runs this BEFORE the
install, because a name chosen after it is a re-install and a lead restart.

WHY ONCE. The answer is voice.conf itself, and a file that exists is an
answer: kept, never re-asked, never overwritten without `--replace`. Record the
defaults too when the operator accepts them, or the next run asks again.

THE RENDERER DECIDES WHICH NAMES ARE REFUSED. Each answer is rendered into a
temp directory before it is written, so the refused characters have one owner.
A name spanning two lines is refused here, because the reader takes the first
line and would drop the rest in silence.

Usage:
  uv run fleet voice-ask                                  # `ask`, or `skip: <the names kept>`
  uv run fleet voice-ask set <operator> <lead>            # record the answer
  uv run fleet voice-ask set --replace <operator> <lead>  # ... over an existing voice.conf

The first word of the output is the answer. Exit: 0; 1 when a name is refused
or voice.conf already holds an answer, and nothing was written; 2 on a usage
error.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
CONF_NAME = "orchestration/voice.conf"
CONF = os.path.join(REPO_ROOT, "orchestration", "voice.conf")
EXAMPLE = os.path.join(REPO_ROOT, "orchestration", "voice.example.conf")
USAGE = "usage: fleet voice-ask [set [--replace] <operator> <lead>]"


def _load_sibling(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fleet_platform = _load_sibling("fleet_platform", "fleet_platform.py")
install_extension = _load_sibling("fleet_install_extension", "install_extension.py")
setting = install_extension.setting


def main(argv: list[str]) -> int:
    if not argv:
        if os.path.isfile(CONF):
            print(
                f"skip: already answered — the lead calls the operator {setting(CONF, 'OPERATOR_NAME')} "
                f"and answers to {setting(CONF, 'ASSISTANT_NAME')} ({CONF_NAME})"
            )
            return 0
        print(
            "ask: nobody has said what the lead calls the operator or what it answers to.\n"
            "Ask the operator both, before the extension is installed:\n"
            f"  what should the lead call you?     default: {setting(EXAMPLE, 'OPERATOR_NAME')}\n"
            f"  what should the lead answer to?    default: {setting(EXAMPLE, 'ASSISTANT_NAME')}\n"
            "Then record the answer, defaults included, so it is asked once:\n"
            "  uv run fleet voice-ask set '<operator>' '<lead>'"
        )
        return 0
    if argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] != "set":
        print(USAGE, file=sys.stderr)
        return 2

    names = argv[1:]
    replace = names[:1] == ["--replace"]
    if replace:
        names = names[1:]
    if len(names) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    operator, lead = names

    if os.path.isfile(CONF) and not replace:
        print(
            f"refused: {CONF_NAME} already holds an answer ({setting(CONF, 'OPERATOR_NAME')}, "
            f"{setting(CONF, 'ASSISTANT_NAME')}). Nothing was written.\n"
            "Replacing it is the operator's call: uv run fleet voice-ask set --replace <operator> <lead>",
            file=sys.stderr,
        )
        return 1
    if any("\n" in name or "\r" in name for name in names):
        print("refused: a name spans two lines. Nothing was written.", file=sys.stderr)
        return 1

    body = f"OPERATOR_NAME={operator}\nASSISTANT_NAME={lead}\n"
    with tempfile.TemporaryDirectory() as tmp:
        trial = os.path.join(tmp, "voice.conf")
        fleet_platform.write_record(trial, body)
        try:
            install_extension.render(tmp, voice=trial)
        except install_extension.Refused as refused:
            print(f"refused: {refused}\nNothing was written.", file=sys.stderr)
            return 1

    fleet_platform.write_record(
        CONF,
        "# The operator's answer, written by `fleet voice-ask`. Gitignored;\n"
        "# orchestration/voice.example.conf documents both settings.\n" + body,
    )
    print(f"recorded: the lead calls the operator {operator} and answers to {lead} ({CONF_NAME})")
    # A lead rendered before this answer keeps the names it was rendered with.
    if os.path.isfile(os.path.join(REPO_ROOT, "FLEET.rendered.md")):
        print("A lead that is already running still uses the old names: re-install and restart it,")
        print("which .agents/skills/update-fleet/ owns.")
    else:
        print("Next: uv run fleet install-extension renders them.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
