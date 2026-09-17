"""One module, one copy, one key — and no module fleet ships that nothing runs.

`scripts/lib/` is not a package. Every module there is loaded BY PATH, by
`fleet/cli.py` for a group and by each module's own `_load_sibling` for a
sibling, because `queue.py` is also loaded with no path change at all (by
`fleet_status.py` and by the pane harness) and a plain `import forge` would
find nothing. Two consequences that nothing held:

- A loader that does not hand back the copy already in `sys.modules` EXECUTES
  THE MODULE AGAIN. The two copies then disagree about anything either
  remembers — which forges are configured is the one queue.py's `_load_sibling`
  docstring names — and the one a caller holds depends on which loader ran
  first.
- A key that is not the one every other loader uses is the same thing spelled
  differently: `fleet_queue` loaded twice under two names is two registries.
"""

from __future__ import annotations

import ast

from archkit import LIB, at, nodes, rel, runnable, sources, text_constant
from harness import lib

from fleet.cli import GROUPS


def key_for(filename: str) -> str:
    """The sys.modules key fleet/cli.py gives a module, which every loader owes it."""
    return "fleet_" + filename.removesuffix(".py").removeprefix("fleet_")


def test_a_module_is_executed_once_per_process():
    """Every loader hands back the copy already loaded, whichever ran first.

    Read as three loaders meeting: `fleet_status.py`'s own `_load_queue`,
    `queue.py`'s `_load_sibling`, and `fleet/cli.py`'s `load`.
    """
    status = lib("fleet_status.py")
    queue = lib("queue.py")
    platform = lib("fleet_platform.py")

    assert status.fleetqueue is queue, "fleet status and `fleet queue` run different copies of queue.py"
    assert queue.fleet_platform is platform, "queue.py holds a second copy of the platform seam"
    assert queue.forge is status.forge, "two forge registries in one process"
    assert lib("reconcile.py") is lib("reconcile.py"), "loading a group twice executes it twice"


def test_every_sibling_is_keyed_the_way_fleet_cli_keys_it():
    """`_load_sibling("fleet_forge", "forge.py")`: the key is the filename's, always."""
    wrong = []
    for path, node in nodes(ast.Call):
        if not (isinstance(node.func, ast.Name) and node.func.id == "_load_sibling" and len(node.args) == 2):
            continue
        name, filename = text_constant(node.args[0]), text_constant(node.args[1])
        assert name and filename, f"{at(path, node)}: a sibling load with something other than two literals"
        if name != key_for(filename):
            wrong.append(f"{at(path, node)}: {filename} keyed {name}, not {key_for(filename)}")
    assert wrong == [], "a module would load twice under two keys:\n  " + "\n  ".join(wrong)


def test_no_module_imports_a_sibling_by_its_bare_name():
    """`import queue` is the standard library's, for the rest of the process."""
    imports = []
    siblings = {p.stem for p in LIB.glob("*.py")}
    for path, node in nodes((ast.Import, ast.ImportFrom)):
        named = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
        for name in named:
            if name.split(".")[0] in siblings:
                imports.append(f"{at(path, node)}: {name}")
    assert imports == [], "a sibling is imported by name rather than loaded by path:\n  " + "\n  ".join(imports)


def test_every_group_names_a_module_that_can_be_one():
    """A group needs a `main(argv) -> int`, and its docstring is that group's usage."""
    for group, (filename, _) in GROUPS.items():
        module = LIB / filename
        assert module.is_file(), f"group {group} names {filename}, which is not in scripts/lib"
        tree = ast.parse(module.read_text(encoding="utf-8"))
        assert ast.get_docstring(tree), f"{filename} has no docstring, and a group's usage lives there"
        mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
        assert mains, f"group {group} names {filename}, which has no main()"
        assert mains[0].args.args, f"{filename}'s main() takes no argv, so `fleet {group} ...` drops its arguments"


def test_every_module_fleet_ships_says_what_it_is_for():
    """AGENTS.md sends every reader to a module's docstring for its usage and
    its rationale, and `fleet <group> --help` prints one. A comment block above
    the imports reads the same to a person and is not there at all to
    `__doc__` — which is how `fleet_status.py`, the longest module here, came
    to have no docstring while reading as though it did."""
    silent = [path for path, tree in sources() if not ast.get_docstring(tree)]
    assert silent == [], "no module docstring, so nothing says what these are for:\n  " + "\n  ".join(silent)


def test_nothing_in_scripts_lib_is_unreachable():
    """A module no group names, no module loads and no command runs is dead code.

    Three ways to be reached, all of them real here: a `fleet` group, a sibling
    load, or a child process another file starts — `check.py` runs the three
    static checks that way, the reconciler runs `notify_lead.py`, and the queue
    pane runs `pane_probe.py` from Lua.

    Documentation does not count. A module every skill explains and nothing
    runs is the case this is here to find.
    """
    named = {filename for filename, _ in GROUPS.values()}
    here = [p.name for p in LIB.glob("*.py")]
    # Never its own file: every module names itself in its own docstring.
    mentioned = {name for path, text in runnable() for name in here if name in text and not path.endswith("/" + name)}
    orphans = sorted(p.name for p in LIB.glob("*.py") if p.name not in named | mentioned)
    assert orphans == [], f"nothing loads or runs {', '.join(orphans)}"


def test_the_modules_this_area_reads_are_the_ones_the_gate_ships():
    """`sources()` is a glob; a renamed directory would empty it and pass everything."""
    on_disk = {rel(p) for p in LIB.glob("*.py")}
    assert on_disk <= {path for path, _ in sources()}
