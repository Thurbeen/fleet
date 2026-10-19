"""The seams AGENTS.md names, held where a new caller would cross one.

A seam is one place a whole class of coupling lives: the OS in
`scripts/lib/fleet_platform.py`, the forge in `scripts/lib/forge.py`, the
encoding of every record fleet writes. Each is already argued in its module's
docstring and stated in AGENTS.md. Nothing held any of them.

What crosses a seam is not a failing test. A second caller reading `os.name`
passes every test on the machine that wrote it; a `gh` invocation inside
`queue.py` works until the day a task names a GitLab repo. Both are found by
an operator, not by a run — so these read the source.
"""

from __future__ import annotations

import ast

from archkit import (FORGE_SEAM, PLATFORM_SEAM, at, calls_builtin, calls_method, keyword, nodes, sources,
                     text_constant)

# How a module asks which OS it is on. `platform` is the standard library
# module, not `fleet_platform`, which several modules bind to that same name.
OS_TELLS = {("os", "name"), ("sys", "platform"), ("os", "uname"), ("sys", "getwindowsversion")}


def test_only_the_platform_seam_reads_which_os_this_is():
    """One function per difference, both branches inside it, so no caller reads `os.name`.

    A caller that reads it instead grows a second, undocumented branch — and
    the OS it gets wrong is never the one it was written on.
    """
    crossings = []
    for path, node in nodes(ast.Attribute, skip=(PLATFORM_SEAM,)):
        if isinstance(node.value, ast.Name) and (node.value.id, node.attr) in OS_TELLS:
            crossings.append(f"{at(path, node)}: {node.value.id}.{node.attr}")
    for path, node in nodes((ast.Import, ast.ImportFrom), skip=(PLATFORM_SEAM,)):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
        if "platform" in names:
            crossings.append(f"{at(path, node)}: imports the platform module")
    assert crossings == [], "the OS is asked about outside the platform seam:\n  " + "\n  ".join(crossings)


# Which module may run a forge CLI, and why it is not a leak. Everything else
# asks forge.py, which is why a self-hosted GitLab is the ordinary case here.
FORGE_CALLERS = {
    FORGE_SEAM: "the seam itself: both adapters live here",
    "scripts/lib/gh_accounts.py": "reads every gh account's token by name, which no forge question needs",
    "scripts/lib/glab_hosts.py": "reads which hosts glab has configured, which decides who owns a repo",
    "scripts/lib/preflight.py": "probes whether each CLI is there and logged in at all",
}


def test_only_the_forge_seam_and_the_login_probes_run_a_forge_cli():
    """`gh` and `glab` as argv[0], anywhere else, is fleet learning a forge twice.

    queue.py is the one this protects: it runs no forge CLI and builds no forge
    URL, so a second forge is a configuration rather than a rewrite.
    """
    crossings = []
    for path, node in nodes(ast.List, skip=tuple(FORGE_CALLERS)):
        first = text_constant(node.elts[0]) if node.elts else None
        if first in ("gh", "glab"):
            crossings.append(f"{at(path, node)}: runs {first}")
    assert crossings == [], "a forge CLI is run outside the forge seam:\n  " + "\n  ".join(crossings)


def test_the_forge_seam_is_where_the_forge_cli_actually_is():
    """The rule above passes trivially if forge.py stopped running one."""
    argv = [text_constant(node.elts[0]) for path, node in nodes(ast.List) if path == FORGE_SEAM and node.elts]
    assert {"gh", "glab"} <= set(argv), "forge.py runs neither CLI: the rule above now proves nothing"


# Where each temporary file takes its mode; given none, it opens in binary.
TEMP_OPENERS = {"NamedTemporaryFile": 0, "TemporaryFile": 0, "SpooledTemporaryFile": 1}


def opener_mode(node: ast.Call) -> tuple[ast.expr | None, str] | None:
    """(the mode argument, the default mode) if `node` opens a file object, else None."""
    func = node.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else None
    if name in TEMP_OPENERS:
        position, default = TEMP_OPENERS[name], "w+b"
    elif calls_builtin(node, "open") or (owner, name) in (("io", "open"), ("os", "fdopen")):
        position, default = 1, "r"
    elif name == "open" and owner != "os":  # `os.open` takes integer flags and returns a descriptor
        position, default = 0, "r"
    elif calls_method(node, "read_text", "write_text"):
        return None, "r"
    else:
        return None
    return (node.args[position] if len(node.args) > position else keyword(node, "mode")), default


def opens_text_without_encoding(node: ast.Call) -> bool:
    opened = opener_mode(node)
    if opened is None or keyword(node, "encoding") is not None:
        return False
    given, default = opened
    return "b" not in (default if given is None else text_constant(given) or "")


def decodes_child_without_encoding(node: ast.Call) -> bool:
    """Any `text=` that is not literally off, since a variable is on as often as not.

    `dict(text=...)` is a key, not a decode mode.
    """
    textual = keyword(node, "text") or keyword(node, "universal_newlines")
    if textual is None or calls_builtin(node, "dict") or keyword(node, "encoding") is not None:
        return False
    return not (isinstance(textual, ast.Constant) and textual.value in (False, None))


def test_every_text_file_fleet_opens_names_its_encoding():
    """A record is UTF-8 on every OS, or it is a different file on Windows.

    `open()` with no `encoding=` decodes in the locale's, which is cp1252 on a
    stock Windows console: a task title round-trips through a different byte,
    and a brief holding one raises where it is read.
    """
    unnamed = [at(path, node) for path, node in nodes(ast.Call) if opens_text_without_encoding(node)]
    assert unnamed == [], "text is read or written in the locale's encoding at:\n  " + "\n  ".join(unnamed)


def test_no_child_process_is_decoded_in_the_locales_encoding():
    """`text=True` without `encoding=` is the same bug one layer out.

    It is how `gh`'s JSON and thurbox's event stream would come back mangled on
    a Windows console. Fleet reads bytes and decodes them itself, or says utf-8.
    """
    unnamed = [at(path, node) for path, node in nodes(ast.Call) if decodes_child_without_encoding(node)]
    assert unnamed == [], "a child's output is decoded in the locale's encoding at:\n  " + "\n  ".join(unnamed)


LOCALE_DECODED = '''
pathlib.Path(p).open("w")
pathlib.Path(p).open()
tempfile.NamedTemporaryFile("w", delete=False)
tempfile.TemporaryFile(mode="w+")
tempfile.TemporaryFile("w+")
tempfile.SpooledTemporaryFile(mode="w")
tempfile.SpooledTemporaryFile(0, "w")
io.open(p)
os.fdopen(fd, "w")
subprocess.run(argv, capture_output=True, text=textual)
subprocess.run(argv, universal_newlines=flag)
'''

NAMED_OR_BINARY = '''
pathlib.Path(p).open("rb")
pathlib.Path(p).open("w", encoding="utf-8")
tempfile.NamedTemporaryFile(dir=where)
tempfile.NamedTemporaryFile("w", encoding="utf-8")
tempfile.SpooledTemporaryFile(1024)
os.open(tmp, os.O_WRONLY, mode)
subprocess.run(argv, text=textual, encoding="utf-8")
subprocess.run(argv, text=False)
dict(f, text=cut(f["text"]))
'''


def calls(source: str) -> list[ast.Call]:
    return [ast.parse(line).body[0].value for line in source.strip().splitlines()]


def test_the_encoding_rules_see_every_spelling_of_a_text_open():
    """The two rules above hold only the spellings they match, so each one is named here.

    Every line of LOCALE_DECODED decodes in the locale's encoding, and none of
    them is the builtin `open` or a literal `text=True`.
    """
    missed = [ast.unparse(c) for c in calls(LOCALE_DECODED)
              if not (opens_text_without_encoding(c) or decodes_child_without_encoding(c))]
    assert missed == [], "the encoding rules let these through:\n  " + "\n  ".join(missed)
    flagged = [ast.unparse(c) for c in calls(NAMED_OR_BINARY)
               if opens_text_without_encoding(c) or decodes_child_without_encoding(c)]
    assert flagged == [], "the encoding rules flag calls that name it or read bytes:\n  " + "\n  ".join(flagged)


def test_the_rules_above_read_every_module_fleet_ships():
    """A glob that stopped matching would pass every rule in this file in silence."""
    paths = [path for path, _ in sources()]
    for named in (PLATFORM_SEAM, FORGE_SEAM, "scripts/lib/queue.py", "scripts/lib/reconcile.py", "fleet/cli.py"):
        assert named in paths, f"{named} is not among the {len(paths)} modules read"


REVIEW_RECORDS = "scripts/lib/review_lifecycle.py"


def joins_the_review_store(call: ast.Call) -> bool:
    """`os.path.join(<shepherd store>, "reviews")`: a path into the review store."""
    return calls_method(call, "join") and any(text_constant(a) == "reviews" for a in call.args)


def test_review_records_have_one_reader():
    """The review store's path and shape are review_lifecycle.py's: a module
    that builds a path into it re-derives both, and drifts from them."""
    crossings = [at(path, node) for path, node in nodes(ast.Call, skip=(REVIEW_RECORDS,))
                 if joins_the_review_store(node)]
    assert crossings == [], "a path into the review store outside review_lifecycle.py:\n  " + "\n  ".join(crossings)
    owner = [node for path, node in nodes(ast.Call) if path == REVIEW_RECORDS and joins_the_review_store(node)]
    assert owner, "review_lifecycle.py no longer builds the store's path: the rule above now proves nothing"
