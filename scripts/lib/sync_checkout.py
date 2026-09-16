"""Keep this checkout current with origin, safely.

    uv run fleet sync-checkout

Wired to the SessionStart hook in .claude/settings.json, so every Claude Code
session that opens the control plane starts from an up-to-date base. A stale
local `main` is silently inherited by every new thurbox worktree: the worker
branches off it, does correct work, and its PR arrives CONFLICTING.

It only ever fast-forwards, and only when that is unambiguously safe:

  dirty working tree        -> report, change nothing
  not on the default branch -> report how far origin/<default> has moved
  diverged from upstream    -> report, change nothing (never rebase/reset here)
  strictly behind + clean   -> `git merge --ff-only`
  already current           -> say nothing

RESTART THE LEAD when a sync brings new INSTRUCTIONS in. The running Mission
Control session froze FLEET.md and every skill it had loaded at launch, and
nothing reloads them from disk, so a fast-forward that touched one of those
paths says so, as an action for the operator. The same goes for the extension
manifest: once it or FLEET.md moves, the installed extension no longer matches
what it was rendered from.

RESTART THE RECONCILER when a sync moves its code. A running loop keeps the
code it started with, and one whose code the sync deleted fails every pass: the
bash reconciler outlived the update that removed `scripts/reconcile.sh` and
logged exit 127 for hours while nothing collected.

THE FETCH IS BOUNDED BY THE CHILD'S OWN TIMEOUT, never by coreutils `timeout`,
which is not on a stock macOS or on Windows. Calling it there exited 127, which
read as a failed fetch, so every session on a Mac reported "could not reach
origin (offline?)" while the network was fine and then worked from a stale
`main`, the exact outcome this exists to prevent.

Prints a single JSON object on stdout: Claude Code shows `systemMessage` to the
user, and `suppressOutput` keeps the raw text out of the transcript. ALWAYS
EXITS 0 AND SAYS WHAT HAPPENED, whatever goes wrong: a sync problem must never
block a session, and the hook is one shell-neutral command with no `|| true`
of its own, so tolerating failure is this module's job.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

# How long the fetch may take before the hook gives up and works from the local
# checkout. A session start is the wrong place to wait on a network.
FETCH_TIMEOUT_SECS = 15

# Changing one of these means the running Mission Control session is holding
# stale instructions; changing a wiring path means the installed thurbox
# extension no longer matches the manifest it was rendered from. Neither is
# fixable from here, so both are reported as actions for the operator.
INSTRUCTION_PATHS = ("FLEET.md", "AGENTS.md", "CLAUDE.md", ".agents/skills", ".claude/skills", ".claude/settings.json")
# session-glyphs.example.conf and voice.example.conf are wiring paths because
# the installer RENDERS both: new defaults in the first are a new lead name, and
# in the second they change what the rendered FLEET payload calls the operator.
WIRING_PATHS = (
    "extension.toml.in",
    "FLEET.md",
    "orchestration/session-glyphs.example.conf",
    "orchestration/voice.example.conf",
)

# What a running reconciler loop executes: the bash script from before the uv
# port, and the module and platform seam the supervisor holds in memory.
RECONCILER_PATHS = ("scripts/reconcile.sh", "scripts/lib/reconcile.py", "scripts/lib/fleet_platform.py")

OFFLINE ="control-plane sync: could not reach origin (offline?). Working from the local checkout."


def git(root: str | None, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace",
        check=False,
    )


def fetch_bounded(root: str) -> bool:
    """`git fetch origin`, given up on after FETCH_TIMEOUT_SECS.

    No pipes on the child: a killed fetch can leave a transport helper holding
    them, and reading them to the end would wait out the very hang the timeout
    exists to cut short.
    """
    try:
        done = subprocess.run(
            ["git", "fetch", "--quiet", "origin"], cwd=root,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=FETCH_TIMEOUT_SECS, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return done.returncode == 0


def count(root: str, spec: str) -> int:
    done = git(root, "rev-list", "--count", spec)
    try:
        return int(done.stdout.strip()) if done.returncode == 0 else 0
    except ValueError:
        return 0


def lead_name(root: str) -> str:
    """The lead's name as the INSTALLED manifest spells it, or "" with nothing installed.

    Never a literal: the glyph in front of it is a setting
    (orchestration/session-glyphs.conf), so a name written here would be one
    setting's value pretending to be the answer.
    """
    try:
        with open(os.path.join(root, "extension.toml"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return ""
    in_sessions = False
    for line in lines:
        if line.startswith("[[sessions]]"):
            in_sessions = True
        if in_sessions and (m := re.match(r'name *= *"(.*)"', line)):
            return m.group(1)
    return ""


def changed(root: str, before: str, paths: tuple[str, ...]) -> str:
    """What this sync actually moved under `paths`, space-separated.

    What arrived, not what exists: only a path the sync moved is worth an
    action. Both tuples are non-empty, because `git diff` over an empty
    pathspec would match the whole tree.
    """
    done = git(root, "diff", "--name-only", before, "HEAD", "--", *paths)
    return " ".join(done.stdout.split()) if done.returncode == 0 else ""


def sync() -> str | None:
    """The message to show, or None to stay silent."""
    if not shutil.which("git"):
        return "control-plane sync: git not found; skipped."

    top = git(None, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return None
    root = top.stdout.strip()

    if not fetch_bounded(root):
        return OFFLINE

    default = git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD").stdout.strip()
    default = default.removeprefix("origin/")
    if not default or default == "HEAD":
        default = "main"

    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    remote_ref = f"origin/{default}"
    if git(root, "rev-parse", "--verify", "--quiet", remote_ref).returncode != 0:
        return None

    # Only TRACKED modifications block a fast-forward. An untracked file, a
    # worker's BRIEF.md or a stray note, would otherwise wedge every sync; one
    # that genuinely collides makes `merge --ff-only` refuse on its own.
    dirty = bool(git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip())

    if branch != default:
        behind_base = count(root, f"HEAD..{remote_ref}")
        if behind_base > 0:
            return (f"control-plane sync: on '{branch}'; {remote_ref} is {behind_base} commit(s) ahead. "
                    "Rebase before opening a PR, or it will conflict.")
        return None

    behind = count(root, f"HEAD..{remote_ref}")
    ahead = count(root, f"{remote_ref}..HEAD")

    if behind == 0 and ahead == 0:
        return None
    if ahead > 0 and behind > 0:
        return (f"control-plane sync: '{branch}' has diverged from {remote_ref} "
                f"({ahead} ahead, {behind} behind). Left alone — reconcile by hand.")
    if ahead > 0:
        return f"control-plane sync: '{branch}' is {ahead} commit(s) ahead of {remote_ref} and not pushed."
    if dirty:
        return (f"control-plane sync: '{branch}' is {behind} commit(s) behind {remote_ref}, "
                "but the tree is dirty. Not fast-forwarding.")

    before = git(root, "rev-parse", "HEAD").stdout.strip()
    if git(root, "merge", "--ff-only", "--quiet", remote_ref).returncode != 0:
        return (f"control-plane sync: '{branch}' is {behind} commit(s) behind {remote_ref} "
                "and would not fast-forward. Left alone.")

    short = git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    msg = f"control-plane sync: fast-forwarded '{branch}' {behind} commit(s) to {short}."

    instr = changed(root, before, INSTRUCTION_PATHS)
    if instr:
        lead = lead_name(root) or "<the lead, from thurbox-cli session list>"
        msg += (
            f"\nrestart-lead: yes — {instr}\n"
            "The running Mission Control session froze FLEET.md and every skill it had\n"
            "loaded at launch; new bytes on disk change nothing for it. Replace the agent\n"
            f"with: thurbox-cli session restart '{lead}' — that resumes the\n"
            "conversation, so the old copy is still in its history. For an instruction change\n"
            "that has to win, start a fresh one instead: thurbox-cli session delete\n"
            f"'{lead}' (the extension self-heals it). Copy that name rather than\n"
            "retyping it: a glyph is part of the session name and is not on a keyboard."
        )

    wiring = changed(root, before, WIRING_PATHS)
    if wiring:
        msg += (
            f"\nreinstall-extension: yes — {wiring}\n"
            "That changed, so the installed extension no longer matches the manifest it was\n"
            "rendered from. Re-render and reinstall it: 'uv run fleet install-extension'."
        )

    loop = changed(root, before, RECONCILER_PATHS)
    if loop:
        msg += (
            f"\nrestart-reconciler: yes — {loop}\n"
            "A reconciler started before this sync still runs its old code, and fails every pass\n"
            "if that code was removed. Ask 'uv run fleet reconcile status': a legacy loop it names,\n"
            "'uv run fleet reconcile ensure' stops and replaces; a loop that is up,\n"
            "'uv run fleet reconcile restart' replaces. One asked down stays down."
        )
    return msg


def main(argv: list[str]) -> int:
    if argv[:1] in (["-h"], ["--help"]):
        sys.stdout.write(__doc__)
        return 0
    try:
        msg = sync()
    except Exception as exc:  # noqa: BLE001 — the hook must never fail a session start
        msg = (f"control-plane sync: failed unexpectedly ({type(exc).__name__}: {exc}). "
               "Working from the local checkout.")
    if msg:
        sys.stdout.write(json.dumps({"systemMessage": msg, "suppressOutput": True}, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
