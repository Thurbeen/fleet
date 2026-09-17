"""fleet's own source, parsed once, so a rule about its shape can be stated once.

Every other area RUNS fleet and reads what it did. These read the source
instead, because the rules they hold are true of lines no run reaches: a
caller that reads `os.name` works perfectly on the machine that wrote it and
is found by the next operator on the other one, and a `gh` invocation on a
branch taken once a year is a forge coupling all the same.

So: no stubs, no environment, no subprocess. The unit is the module tree,
`fleet/` and `scripts/lib/` together, which is everything fleet ships as code.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

from harness import REPO

# The two seams AGENTS.md names, spelled the way `sources` reports a path.
PLATFORM_SEAM = "scripts/lib/fleet_platform.py"
FORGE_SEAM = "scripts/lib/forge.py"

LIB = REPO / "scripts" / "lib"


def rel(path: Path) -> str:
    """A checkout-relative path with forward slashes, so a rule reads the same on both OSes."""
    return path.relative_to(REPO).as_posix()


@cache
def sources() -> tuple[tuple[str, ast.Module], ...]:
    """Every module fleet ships, as (path, parsed tree), in a stable order."""
    files = sorted([*(REPO / "fleet").glob("*.py"), *LIB.glob("*.py")])
    trees = tuple((rel(f), ast.parse(f.read_text(encoding="utf-8"), filename=str(f))) for f in files)
    assert len(trees) > 20, f"only {len(trees)} modules found — is the checkout complete?"
    return trees


def nodes(kind: type | tuple[type, ...], skip: tuple[str, ...] = ()) -> list[tuple[str, ast.AST]]:
    """Every node of `kind` in every module, as (where, node). `skip` names paths to leave out."""
    return [(path, node) for path, tree in sources() if path not in skip
            for node in ast.walk(tree) if isinstance(node, kind)]


def at(path: str, node: ast.AST) -> str:
    return f"{path}:{getattr(node, 'lineno', 0)}"


def keyword(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def calls_builtin(call: ast.Call, name: str) -> bool:
    """`open(...)` and not `q.open(...)` or a helper of fleet's own named `open`."""
    return isinstance(call.func, ast.Name) and call.func.id == name


def calls_method(call: ast.Call, *names: str) -> bool:
    """`path.read_text(...)`: a method, so queue.py's own `read_text(path)` is not one."""
    return isinstance(call.func, ast.Attribute) and call.func.attr in names


def text_constant(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


@cache
def runnable() -> tuple[tuple[str, str], ...]:
    """Every file that can RUN a module in scripts/lib, as (path, text).

    Python is not the whole answer: `interface/fleet_queue.lua` runs
    `pane_probe.py` in a child, and the two bootstraps run the installer.
    """
    files = [*(REPO / "fleet").glob("*.py"), *LIB.glob("*.py"), *LIB.glob("*.lua"),
             *(REPO / "interface").glob("*.lua"), REPO / "install.sh", REPO / "install.ps1"]
    return tuple((rel(f), f.read_text(encoding="utf-8")) for f in files if f.is_file())
