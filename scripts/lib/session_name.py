#!/usr/bin/env python3
r"""Render the name one session fleet spawns is created under.

    fleet session-name diagnose 'Diagnose this machine and free what is dead'
    fleet session-name review   'Review open change requests on <project>'
    fleet session-name worker   'Fix the thing'

Written to be interpolated into the `thurbox-cli session create` line a SKILL
holds:

    thurbox-cli session create \
      --name "$(uv run fleet session-name diagnose 'Sweep this machine')" ...

WHY A COMMAND AND NOT A LITERAL. thurbox has no per-session icon field, so a
session wears its mark in its NAME, and which mark is a setting —
`orchestration/session-glyphs.example.conf`, or the gitignored
`session-glyphs.conf` beside it. A skill's prose reads no setting, so a skill
that spelled the glyph would be a second copy of one this repo already owns,
and `GLYPHS=off` could never take it back off. This is the way in.

It is the counterpart of `fleet session-flags`, which renders a session
PROFILE into `session create` flags: settings a session starts under, as
opposed to what it is called.

THE SETTING IS READ ONCE, IN `queue.py`. This module decides nothing — it maps
a kind to a mark through `session_glyph` and lays the name out with
`session_name`, both of which are what `fleet queue dispatch` already uses for
a worker. So a spawned session and a queue worker cannot disagree about the
mark or about `GLYPHS=off`.

IT REFUSES RATHER THAN CUTS, which is the one place it does NOT behave like
`dispatch`. `session_name` truncates to thurbox's byte cap because a dispatch
has nobody to ask; here a person is typing the title, so a title that would be
cut — or that thurbox would reject outright — is an error with the reason on
stderr. Silently cutting is worse than it sounds: the mark costs 5 of the 64
bytes, so two long titles differing only past the cut render the SAME name, and
`--on-existing adopt` matches on the name. A reviewer spawned for one project
would adopt another's session. A refusal ends with a `Try this title:` line
carrying one that WOULD render, derived from the one that was typed by the same
rule that refused it, so that being told no does not also mean inventing the
name — or, where no title can be derived from what was typed, with `Reword the
title.` The CAP is the usual way to reach that second ending: no WORD is ever
dropped to fit it, because what runs over is the end of the title, which is
where the identity is.

Exit codes: 0 and the name on stdout, 2 for a usage error — an unknown kind is
one, because the kinds are fleet's and not the operator's — and 1 for a title
or a setting this refuses to render.
"""

from __future__ import annotations

import importlib.util
import os
import sys

LIB = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name: str, filename: str):
    """A scripts/lib module by path, keyed the way fleet/cli.py keys it.

    The copy already loaded when there is one: `queue.py` executed twice is two
    forge registries, and this module is loaded both as a `fleet` group and,
    in a test, beside one.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(LIB, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fleetqueue = _load_sibling("fleet_queue", "queue.py")

USAGE = (
    "usage: fleet session-name <kind> <title>\n"
    f"kinds: {' '.join(fleetqueue.GLYPH_KEYS)}\n"
)


def refuse(title: str, glyph: str) -> str:
    """Every reason this title cannot become a session name, or "".

    EVERY reason and not the first, because an operator who fixes one and
    re-runs into the next has been sent round twice by a message that knew
    both. And asked about the WHOLE rendered name, never the cut one: a `/`
    past byte 64 is still a `/` in the title somebody has to edit, and quoting
    a name back at them that they did not type and that this would never hand
    over is its own small lie.

    It ends with a title that WOULD render — `queue.py`'s `safe_name_title`,
    from the same rule that produced the refusal — because a reviewer session
    for `github.com/Thurbeen/fleet` still needs a name, and the operator who
    was refused one should not also have to invent the other. Or, where that
    rule can derive none, with `Reword the title.`: a suggestion nobody can
    type back is worse than none.
    """
    whole = fleetqueue.rendered_name(title, glyph)
    reasons = []

    # thurbox's rule, from `queue.py`'s `unsafe_name` — stated once, there, and
    # re-implementing it here is how two callers come to disagree about what
    # thurbox accepts.
    if why := fleetqueue.unsafe_name(whole):
        reasons.append(
            f"thurbox would refuse the name {whole!r},\n{why}.\n"
            "A session name becomes a path segment there: no '/', no '\\', no "
            "'..', no leading\n'.', and never empty."
        )
    # Not thurbox's rule — it accepts a blank name — but a session called
    # nothing is one nobody can pick out of the list, which is the whole
    # purpose of rendering a name here.
    elif not title.strip():
        reasons.append("an empty title leaves nothing to tell this session from another.")

    if len(whole.encode()) > fleetqueue.SESSION_NAME_BYTES:
        reasons.append(
            f"the name would be cut to {fleetqueue.SESSION_NAME_BYTES} bytes, "
            "which is thurbox's cap.\nTwo titles that differ only past the cut "
            "become one name, and\n`--on-existing adopt` would then adopt the "
            "wrong session."
        )
    if not reasons:
        return ""
    # A suggestion only where one falls out of what was typed: an empty title
    # leaves nothing to suggest, and a name nobody typed is not an answer.
    suggestion = fleetqueue.safe_name_title(title, glyph)
    # A TITLE and not a command line. `fleet` is not on PATH — this repo is
    # `uv run fleet …` — and a command would have to be quoted for a shell,
    # which is POSIX on this operator's machine and neither on the Windows one
    # the same checkout runs on. The title is the thing they edit anyway.
    #
    # Printed as itself, with no quoting at all. `repr` escapes with `\`, which
    # is one of the characters thurbox refuses, so a quoted suggestion holding
    # an apostrophe is refused when it is typed back — and suggests itself
    # again. `safe_name_title` is what makes bare printing safe: it suggests
    # nothing unprintable, and nothing holding a newline, so there is neither
    # anything to escape nor a second line to mistake for another message.
    way_out = f"Try this title: {suggestion}" if suggestion else "Reword the title."
    return "\n".join([*reasons, way_out])


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        sys.stdout.write(__doc__.strip() + "\n\n" + USAGE)
        return 0
    if len(argv) != 2 or argv[0].startswith("-"):
        sys.stderr.write(USAGE)
        return 2
    kind, title = argv
    if kind not in fleetqueue.GLYPH_KEYS:
        sys.stderr.write(f"fleet session-name: no session kind {kind!r}\n{USAGE}")
        return 2
    try:
        glyph = fleetqueue.session_glyph(kind)
    except fleetqueue.QueueError as err:
        sys.stderr.write(f"fleet session-name: {err}\n")
        return 1

    if refusal := refuse(title, glyph):
        sys.stderr.write(f"fleet session-name: {refusal}\n")
        return 1

    # A mark the setting in force has no word for, reported only once there IS
    # a name — a refused title has no mark to be missing. Not a refusal: the
    # plain name still spawns the session. `queue.py` decides it, for the
    # reason its own docstring gives.
    if key := fleetqueue.missing_glyph_word(kind):
        sys.stderr.write(
            f"fleet session-name: no {key} in {fleetqueue.GLYPH_CONF} — this "
            f"session wears no mark.\nCopy the key from "
            f"{fleetqueue.GLYPH_CONF_DEFAULTS}, or set GLYPHS=off to mean it.\n"
        )
    print(fleetqueue.session_name(title, glyph))
    return 0


if __name__ == "__main__":
    sys.exit(main())
