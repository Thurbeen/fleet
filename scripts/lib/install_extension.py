"""Render extension.toml from extension.toml.in, then install it into thurbox.

THE PLACEHOLDERS. `[[sessions]] repo_path` must be the absolute path of THIS
checkout, and no thurbox token spells "my clone" (`{home}` is the extension
home), so the manifest carries `__REPO_PATH__` and this substitutes it — as a
TOML string, so a Windows path's backslashes survive. `__LEAD_GLYPH__` is the
lead's mark, one machine's TERMINAL's choice: `orchestration/session-glyphs
.example.conf`, or the gitignored `session-glyphs.conf` beside it.
`__LEAD_AGENT__` is `AGENT` in `orchestration/agent.conf`, else thurbox's stock
`claude`. `__FLEET_SUFFIX__` and `__FLEET_LABEL__` are this fleet's NAME
(`orchestration/fleet.conf`) on the extension id and on the lead — `fleet-acme`
and `Mission Control · acme` — and render empty for the unnamed fleet every
clone is until it says otherwise. The rendered `extension.toml` is gitignored.

SEVERAL FLEETS ON ONE MACHINE ARE THOSE TWO NAMES AND NOTHING ELSE. Each fleet
is a checkout — queue, registry, run logs, reconciler runtime, first-run answers
— and thurbox resolves an extension and a session by NAME, so an unnamed second
clone registers over the first and is handed the first's lead. Naming it is what
makes the two independent; `orchestration/fleet.example.conf` argues the rest.

IT ALSO RENDERS THE PAYLOAD. FLEET.md carries `@OPERATOR_NAME@` and
`@ASSISTANT_NAME@` (`@` because markdown reads `__x__` as bold), and
`orchestration/voice.example.conf` or the gitignored `voice.conf` names them.
The substituted copy is the gitignored `FLEET.rendered.md`, so an operator who
renamed themselves has changed no tracked file and `fleet sync-checkout` still
has a clean tree. It is rewritten IN PLACE: without privilege, thurbox on
Windows makes `[[symlinks]]` into hard links, and a file replaced by a new one
would leave those links on the old names. A rendered payload reaches no
running lead — the session froze FLEET.md at launch.

Settings are read as DATA, never executed. FLEET_GLYPH_ROOT, FLEET_AGENT_ROOT,
FLEET_NAME_ROOT and FLEET_VOICE_CONF relocate them, so the gate renders the
tracked defaults and never an operator's override.

CHANGING THE GLYPH IS A RENAME, and this cannot apply one: thurbox has no
rename verb and `ensure_extension` matches by name. extension.toml.in's
RENAMING header holds the sequences; this says so when it sees the old name
still running.

MOVED THE CLONE? Re-installing is not enough. thurbox reuses an extension's
session by NAME and never compares its directory, so a re-install reports
success and the live session still opens the OLD path. This compares the live
session's cwd with this checkout and exits non-zero naming the remedy, which
deletes the lead's conversation and is therefore the operator's call:

    thurbox-cli extension deactivate fleet   # deletes the session
    uv run fleet install-extension           # respawns it at the new path

THE SAME SYMPTOM IS ALSO A SECOND FLEET that has not named itself, and the
remedies are opposites — so an UNNAMED fleet is told both, and a named one,
which already made that choice, is told only the first.

IT ALSO INSTALLS THE TUI PANE with `thurbox-cli plugin install`, which records
it in the user's plugins.toml, and does NOT place it: placing is an edit to the
operator's own layout.lua, which `fleet place-pane` makes on their word. The
first Mission Control session asks once, through `fleet pane-ask`. Once placed,
F3 opens and closes it.

TAKING THE PANE BACK is `plugin remove`, and its argument is the DESTINATION
PATH, not the file's basename:

    thurbox-cli plugin remove plugins/91_fleet_queue.lua

Usage:
  uv run fleet install-extension                     # render, then install
  uv run fleet install-extension --render-only <dir> # render both files into
                                                     # <dir> and stop; needs no
                                                     # thurbox-cli

Requires thurbox-cli for a real install.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
PANE_DEST = "plugins/91_fleet_queue.lua"
PLACEHOLDERS = ("__REPO_PATH__", "__LEAD_GLYPH__", "__LEAD_AGENT__", "__FLEET_SUFFIX__", "__FLEET_LABEL__")
NAME_PLACEHOLDERS = ("@OPERATOR_NAME@", "@ASSISTANT_NAME@")


def _load_sibling(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fleet_platform = _load_sibling("fleet_platform", "fleet_platform.py")


class Refused(Exception):
    """A setting this will not render; nothing was written."""


@dataclass
class Rendered:
    report: list[str]
    manifest: str
    glyph: str
    glyph_conf: str
    fleet: str


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def setting(path: str, key: str) -> str:
    """The first `KEY=value` line's value, or ''."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(key + "="):
                return line[len(key) + 1:].rstrip("\n")
    return ""


def pick(root: str, name: str) -> str:
    """The operator's `<name>.conf` when there is one, else the tracked example."""
    own = os.path.join(root, "orchestration", name + ".conf")
    return own if os.path.isfile(own) else os.path.join(root, "orchestration", name + ".example.conf")


def lead_glyph(conf: str) -> str:
    if not os.path.isfile(conf):
        raise Refused(f"missing the glyph setting: {conf}")
    mode = setting(conf, "GLYPHS")
    if mode == "off":
        glyph = setting(conf, "LEAD_GLYPH_OFF")
    elif mode in ("on", ""):
        glyph = setting(conf, "LEAD_GLYPH_ON")
    else:
        raise Refused(f"GLYPHS in {conf} is neither 'on' nor 'off'")
    if not glyph:
        raise Refused(f"no lead glyph in {conf}")
    # The name goes on to be a shell argument in every hint printed here.
    if any(c in glyph for c in "|'\" "):
        raise Refused(f"the lead glyph from {conf} contains a quote, a pipe or a space: {glyph}")
    return glyph


# WHERE A FLEET'S NAME IS ALLOWED TO GO. It becomes a directory under thurbox's
# config (`extensions/fleet-<name>`) and a path segment inside a session name,
# on two operating systems — so the grammar is bare, and narrow on purpose. The
# cap is short because the whole rendered lead name is what a mailbox address
# has to be pasted as.
FLEET_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,23}")

# What separates the lead from its fleet. One cell, no variation selector, and
# not a character a worker's imperative title reaches for — extension.toml.in's
# own header owns that argument, and interface/fleet_queue.lua matches it.
FLEET_SEPARATOR = " · "


def fleet_name() -> str:
    """Which fleet this checkout is, or "" — and "" is the tracked default.

    One setting, read HERE and nowhere else, exactly as the glyph and the agent
    are: everything downstream reads the rendered manifest or the live session
    list instead, so there is no second copy of this answer to keep in step.

    Unnamed renders the two names fleet always used, so a machine with one
    fleet is never renamed by this setting existing. `orchestration/fleet.example.conf`
    holds the rest of the argument.
    """
    conf = pick(os.environ.get("FLEET_NAME_ROOT") or REPO_ROOT, "fleet")
    name = (setting(conf, "NAME") if os.path.isfile(conf) else "").strip()
    if not name:
        return ""
    if not FLEET_NAME_RE.fullmatch(name):
        raise Refused(
            f"NAME in {conf} is not a name a fleet can carry: {name!r}\n"
            "  It becomes a directory under thurbox's config and a path segment\n"
            "  inside a session name, so it is letters, digits, '_' and '-',\n"
            "  starting with a letter or a digit, at most 24 of them."
        )
    return name


def lead_agent() -> str:
    conf = pick(os.environ.get("FLEET_AGENT_ROOT") or REPO_ROOT, "agent")
    agent = (setting(conf, "AGENT") if os.path.isfile(conf) else "") or "claude"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent):
        raise Refused(f"AGENT in {conf} is not a bare agent name: {agent}")
    return agent


def voice_names(conf: str) -> tuple[str, str]:
    if not os.path.isfile(conf):
        raise Refused(f"missing the voice setting: {conf}")
    operator, lead = setting(conf, "OPERATOR_NAME"), setting(conf, "ASSISTANT_NAME")
    if not operator:
        raise Refused(f"no OPERATOR_NAME in {conf}")
    if not lead:
        raise Refused(f"no ASSISTANT_NAME in {conf}")
    # `@` is the placeholder delimiter: a name holding the OTHER placeholder
    # would be rewritten by the second substitution. The rest are refused
    # because the lead's context and every hint quote the names.
    for name in (operator, lead):
        if any(c in name for c in "|\\&'\"@"):
            raise Refused(f"a name in {conf} contains a quote, a pipe, a backslash, an '&' or an '@': {name}")
    return operator, lead


def write_in_place(path: str, text: str) -> None:
    """Truncate and rewrite the same file, so every hard link to it sees the new text."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def render(dest: str, voice: str | None = None) -> Rendered:
    """Render extension.toml and FLEET.rendered.md into `dest`, or raise Refused before writing either."""
    template = os.path.join(REPO_ROOT, "extension.toml.in")
    fleet_md = os.path.join(REPO_ROOT, "FLEET.md")
    for path in (template, fleet_md):
        if not os.path.isfile(path):
            raise Refused(f"missing {path}")
    if '"' in REPO_ROOT or any(ord(c) < 32 for c in REPO_ROOT):
        raise Refused(f"the repo path contains a quote or a control character, which the manifest cannot carry: {REPO_ROOT}")

    glyph_conf = pick(os.environ.get("FLEET_GLYPH_ROOT") or REPO_ROOT, "session-glyphs")
    glyph = lead_glyph(glyph_conf)
    agent = lead_agent()
    fleet = fleet_name()
    voice = voice or os.environ.get("FLEET_VOICE_CONF") or pick(REPO_ROOT, "voice")
    operator, lead = voice_names(voice)

    out = os.path.join(dest, "extension.toml")
    manifest = (
        read(template)
        .replace("__REPO_PATH__", REPO_ROOT.replace("\\", "\\\\"))
        .replace("__LEAD_GLYPH__", glyph)
        .replace("__LEAD_AGENT__", agent)
        .replace("__FLEET_SUFFIX__", f"-{fleet}" if fleet else "")
        .replace("__FLEET_LABEL__", f"{FLEET_SEPARATOR}{fleet}" if fleet else "")
    )
    # A half-rendered manifest would register a session in a directory literally
    # named __REPO_PATH__, or bound to an agent thurbox has never heard of.
    if any(p in manifest for p in PLACEHOLDERS):
        raise Refused(f"placeholder survived substitution; {out} not written")
    if not manifest.strip():
        raise Refused(f"rendered manifest is empty; {out} not written")

    payload_out = os.path.join(dest, "FLEET.rendered.md")
    payload = read(fleet_md).replace("@OPERATOR_NAME@", operator).replace("@ASSISTANT_NAME@", lead)
    if any(p in payload for p in NAME_PLACEHOLDERS):
        raise Refused(f"placeholder survived substitution; {payload_out} not written")
    if not payload.strip():
        raise Refused(f"rendered payload is empty; {payload_out} not written")

    fleet_platform.write_record(out, manifest)
    write_in_place(payload_out, payload)
    return Rendered(
        report=[
            f"rendered {out} (repo_path = {REPO_ROOT}, lead glyph = {glyph}, agent = {agent}, "
            f"fleet = {fleet or 'unnamed'})",
            f"rendered {payload_out} (operator = {operator}, lead answers to = {lead})",
        ],
        manifest=manifest,
        glyph=glyph,
        glyph_conf=glyph_conf,
        fleet=fleet,
    )


def die(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def session_list(cli: str) -> list[dict]:
    done = subprocess.run([cli, "session", "list", "--json"], capture_output=True, encoding="utf-8", errors="replace")
    try:
        listing = json.loads(done.stdout)
    except ValueError:
        return []
    return [s for s in listing if isinstance(s, dict)] if isinstance(listing, list) else []


def same_dir(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def install(cli: str, rendered: Rendered) -> int:
    # The names are read out of the manifest rather than spelled here, so a
    # rename reaches these checks and hints for free.
    ext = re.search(r'^name *= *"(.*)"', rendered.manifest, re.M)
    if not ext:
        return die("could not read the extension name from the rendered manifest")
    ext_name = ext.group(1)
    sessions_part = rendered.manifest.split("\n[[sessions]]", 1)
    found = re.search(r'^name *= *"(.*)"', sessions_part[1], re.M) if len(sessions_part) == 2 else None
    session_name = found.group(1) if found else ""

    code = subprocess.run([cli, "extension", "install", REPO_ROOT]).returncode
    if code:
        return code

    if session_name:
        listing = session_list(cli)
        live = next((s.get("cwd") or "" for s in listing if s.get("name") == session_name), "")
        if live and not same_dir(live, REPO_ROOT):
            # TWO SITUATIONS WEAR THIS ONE SYMPTOM, and they have opposite
            # remedies. The clone MOVED — one fleet, repoint it, and that costs
            # the lead's conversation. Or this is a SECOND fleet that has not
            # been named yet — two fleets, and naming this one costs nothing at
            # all, because nothing is running under the name it would take.
            # Only an unnamed fleet can be the second case: a named one already
            # made that choice, so it is told the moved-clone remedy alone.
            second = "" if rendered.fleet else (
                "\n\nIf this is a SECOND fleet rather than the first one moving, name it\n"
                "instead — it then gets an extension and a Mission Control of its own,\n"
                "and the session above is left alone:\n\n"
                f"  echo NAME=<name> > {os.path.join('orchestration', 'fleet.conf')}\n"
                "  uv run fleet install-extension\n\n"
                "orchestration/fleet.example.conf holds the grammar and what a name costs\n"
                "a fleet that is already running."
            )
            print(
                f"\nerror: the '{session_name}' session still opens a different directory.\n\n"
                f"  live session: {live}\n"
                f"  this clone:   {REPO_ROOT}\n\n"
                "The manifest was rendered and installed, but thurbox reuses an existing\n"
                "session by name and never moves it, so nothing changed for the session\n"
                "that actually runs. To repoint it — this DELETES that session and its\n"
                "conversation history, so it is your call, not this command's:\n\n"
                f"  thurbox-cli extension deactivate {ext_name}\n"
                "  uv run fleet install-extension"
                f"{second}",
                file=sys.stderr,
            )
            return 1

        # THE GLYPH FLIP, WHICH LOOKS LIKE SUCCESS AND IS NOT: nothing answers
        # to the new name yet, so the check above has nothing to compare.
        if not live:
            other = setting(rendered.glyph_conf, "LEAD_GLYPH_OFF")
            if other == rendered.glyph:
                other = setting(rendered.glyph_conf, "LEAD_GLYPH_ON")
            bare = session_name[len(rendered.glyph):] if session_name.startswith(rendered.glyph) else session_name
            stale = other + bare
            if other and stale != session_name and any(s.get("name") == stale for s in listing):
                print(
                    f"\nnote: the running lead is still '{stale}'.\n\n"
                    f"The manifest now declares '{session_name}', but thurbox names a\n"
                    "session when it SPAWNS it and has no rename verb, so nothing that\n"
                    "is already running moved. Applying it is your call and costs\n"
                    "either the lead's conversation or a fork — extension.toml.in's\n"
                    "RENAMING header holds both sequences. Until you run one, the old\n"
                    "session is the live one and the new name is what the next spawn\n"
                    "would use.",
                    file=sys.stderr,
                )

    install_pane(cli)
    print(
        "\nInstalled. Useful follow-ups:\n\n"
        f"  thurbox-cli extension status {ext_name}      # per-resource health\n"
        f"  thurbox-cli extension deactivate {ext_name}  # the real off-switch\n"
        f"  thurbox-cli extension uninstall {ext_name}   # reverse the install"
    )
    return 0


def install_pane(cli: str) -> None:
    """Separate from the extension, and not fatal to it: a control plane with no pane still works."""
    source = os.path.join(REPO_ROOT, "interface", "fleet_queue.lua")
    if not os.path.isfile(source):
        print(f"\nwarning: {source} is missing; the TUI queue pane was not installed", file=sys.stderr)
        return
    if subprocess.run([cli, "plugin", "install", source, "--as", PANE_DEST, "--text"]).returncode:
        print(f"\nwarning: could not install the TUI queue pane from {source}", file=sys.stderr)
        return

    # `plugin check` exits non-zero on the failure that looks like success — a
    # pane that loads and is placed by no arrangement — so its verdict is read.
    check = subprocess.run([cli, "plugin", "check", "--text"], capture_output=True, encoding="utf-8", errors="replace")
    if check.returncode == 0:
        print("\nThe fleet queue pane is installed and placed. Press F3 in thurbox.")
        return
    ui_lines = subprocess.run(
        [cli, "plugin", "dir", "--text"], capture_output=True, encoding="utf-8", errors="replace"
    ).stdout.splitlines()
    ui = ui_lines[0].strip() if ui_lines else "<thurbox-cli plugin dir>"
    place_pane = _load_sibling("fleet_place_pane", "place_pane.py")
    try:
        block = "\n".join("  " + line for line in place_pane.guard(place_pane.slot_of(source)))
    except place_pane.Stop as stop:
        block = f"  ({stop})"
    report = "\n".join("  " + line for line in (check.stdout + check.stderr).rstrip("\n").splitlines())
    print(
        "\nThe fleet queue pane is installed but NOT PLACED, so it will draw\n"
        "nothing yet. Mission Control asks you once, on its first session,\n"
        "whether to put it on screen. To do it now instead, one command puts\n"
        "it to the right of the terminal, and it is not run for you — every\n"
        "pane on your screen shares that file:\n\n"
        "  uv run fleet place-pane --dry-run   # what it would write, where\n"
        "  uv run fleet place-pane             # place it (--left for the other side)\n\n"
        "It backs the file up first, refuses an arrangement it cannot read,\n"
        "and re-reads its own edit. To do it by hand instead, add this block\n"
        "to:\n\n"
        f"  {os.path.join(ui, 'layout.lua')}\n\n"
        "beside the other side columns, inside the `columns` list:\n\n"
        f"{block}\n\n"
        "The `panels.shown` guard is not optional: without it the column is\n"
        "carved on every frame and F3 toggles a value nothing reads, so the\n"
        "pane opens and never closes. `panels` and `filled` both already\n"
        "exist in the stock layout.lua, beside the same guard on the session\n"
        "list. Then `thurbox-cli plugin check` goes green and F3 opens and\n"
        "closes the pane. What it reported:\n\n"
        f"{report}"
    )


def main(argv: list[str]) -> int:
    render_only = None
    if argv[:1] == ["--render-only"]:
        if len(argv) != 2:
            return die("--render-only takes a directory")
        render_only = argv[1]
        if not os.path.isdir(render_only):
            return die(f"not a directory: {render_only}")
    elif argv[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    elif argv:
        return die(f"unknown argument: {argv[0]} (usage: fleet install-extension [--render-only <dir>])")

    cli = None
    if render_only is None:
        cli = shutil.which("thurbox-cli")
        if not cli:
            return die("thurbox-cli not found; install thurbox first")

    try:
        rendered = render(render_only or REPO_ROOT)
    except Refused as refused:
        return die(str(refused))
    print("\n".join(rendered.report))

    if render_only is not None:
        print("\n--render-only: nothing was installed.")
        return 0
    sys.stdout.flush()
    return install(cli, rendered)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
