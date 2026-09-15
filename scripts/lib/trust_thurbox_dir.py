"""Mark a directory as trusted for Claude Code, so a session started there does
not stop on the workspace-trust dialog.

WHERE TRUST LIVES. Not in settings.json: in ~/.claude.json, as
`.projects["<absolute path>"].hasTrustDialogAccepted = true`, keyed by EXACT
absolute path, with no glob and no prefix rule. Answering the dialog inside a
thurbox worktree records it against the repository's main worktree path, so
the first wave of workers dispatched at once against a repo all draw it.

THIS IS THE FALLBACK. `fleet session-trust` answers the dialog with a
keystroke, which touches nothing that outlives the session. Seeding is for when
a dialog cannot be answered, and for pre-seeding worktrees ahead of an
unattended run. It writes to a file the operator owns.

TRUST IS A REAL GUARD: it says "I vouch for the code in this directory". Only
point this at worktrees of repos you already trust, which is what
--all-worktrees does.

CAVEAT: every running Claude Code process rewrites ~/.claude.json, and a
concurrent write can clobber this edit. Seed a path before the session that
uses it starts.

CLAUDE_JSON and THURBOX_WORKTREES override the two locations; thurbox's
worktrees live under its data directory (XDG_DATA_HOME, else %LOCALAPPDATA% on
Windows, else ~/.local/share).

Usage:
  uv run fleet trust-thurbox-dir <absolute-dir> [<absolute-dir> ...]
  uv run fleet trust-thurbox-dir --all-worktrees   # every existing thurbox worktree
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fleet_platform = _load_sibling("fleet_platform", "fleet_platform.py")


class Stop(Exception):
    pass


def thurbox_worktrees() -> str:
    # thurbox's data directory follows the same rule as fleet's, one level up.
    return os.path.join(os.path.dirname(fleet_platform.fleet_data_dir()), "thurbox", "worktrees")


def worktrees_of(root: str) -> list[str]:
    """Every directory exactly two levels under `root`: <repo>/<worktree>."""
    if not os.path.isdir(root):
        raise Stop(f"no such dir: {root}")
    found = []
    for repo in sorted(os.scandir(root), key=lambda e: e.name):
        if repo.is_dir(follow_symlinks=False):
            found += [e.path for e in sorted(os.scandir(repo.path), key=lambda e: e.name)
                      if e.is_dir(follow_symlinks=False)]
    return found


def trust(claude_json: str, dirs: list[str]) -> None:
    for d in dirs:
        if not os.path.isabs(d):
            raise Stop(f"path must be absolute: {d}")
    backup = f"{claude_json}.bak.{os.getpid()}"
    shutil.copy2(claude_json, backup)
    try:
        with open(claude_json, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Stop(f"could not read {claude_json} as JSON ({exc}); it is untouched (backup at {backup})") from None
    projects = doc.setdefault("projects", {}) if isinstance(doc, dict) else None
    if not isinstance(projects, dict):
        raise Stop(f"{claude_json} has no .projects object; it is untouched (backup at {backup})")
    for d in dirs:
        entry = projects.get(d)
        if not isinstance(entry, dict):
            entry = projects[d] = {}
        entry["hasTrustDialogAccepted"] = True
    fleet_platform.write_record(claude_json, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    os.remove(backup)


def main(argv: list[str]) -> int:
    if argv[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    claude_json = os.environ.get("CLAUDE_JSON") or os.path.join(os.path.expanduser("~"), ".claude.json")
    try:
        if not os.path.isfile(claude_json):
            raise Stop(f"no such file: {claude_json}")
        if argv[:1] == ["--all-worktrees"]:
            root = os.environ.get("THURBOX_WORKTREES") or thurbox_worktrees()
            dirs = worktrees_of(root)
            if not dirs:
                print(f"no worktrees under {root}")
                return 0
        elif argv:
            dirs = argv
        else:
            raise Stop("usage: fleet trust-thurbox-dir <absolute-dir>... | --all-worktrees")
        trust(claude_json, dirs)
    except Stop as stop:
        print(f"error: {stop}", file=sys.stderr)
        return 1
    for d in dirs:
        print(f"trusted  {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
