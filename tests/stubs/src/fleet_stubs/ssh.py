"""`ssh`: a fake host with a real filesystem under `remotes/<destination>`.

`ssh [opts...] <destination> <script>` — only the last two arguments matter.
`cat > path` writes into the fake host and `cat path` reads back out, which is
what makes a brief push and a result fetch provable rather than asserted.

A host is shaped by a flag file under `ssh-state/`: `<dest>.down` for an
unreachable one, `.nonposix` for a Windows-shaped shell, `.noforge` for one with
no GitHub credentials, `.norepo` for one where the repo is not there.
`.pane-probe` is what a fleet on that host answers when its queue probe
(`pane_probe.py`) is run there, `.context` what it answers to a peer's context
read (`context.py ... --wire json`), and `.slow` holds the seconds either takes
first.
`.windows` hands the command to `windows_host`, which answers only what a
PowerShell 5 sshd answers. `.origin` holds what `git remote get-url origin`
prints there, and `.origins.json` what it prints per checkout path; a base
fetch succeeds wherever one of those names an origin, unless `.nofetch` holds
git's failure, and an upstream is always unset. `mkdir -p`/`chmod`, `rm -f`/`rmdir`, `mv -f` and `cat >` behave as a
shell does: a write into a directory that does not exist fails. `.realshell` RUNS the script with `sh -c`, the way
sshd runs an account's login shell, with HOME at `<dest>.home` and only the
system's PATH — POSIX only.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time

from fleet_stubs import called, windows_host


def unquote(path: str) -> str:
    return re.sub(r"'$", "", re.sub(r"^'", "", path))


def named_origin(state, dest: str, script: str) -> str | None:
    """What `git remote get-url origin` prints for the checkout `script` names.

    `.origins.json` maps a checkout path to its own origin, for a task spanning
    repositories; `.origin` is every other path's. Longest first, so `/a/b` is
    not answered for `/a/bc`; matched as text because a login shell wraps the
    script in a second quoting.
    """
    origins = state / f"{dest}.origins.json"
    named = json.loads(origins.read_text(encoding="utf-8")) if origins.is_file() else {}
    path = next((p for p in sorted(named, key=len, reverse=True) if p in script), None)
    if path is not None:
        return named[path]
    origin = state / f"{dest}.origin"
    return origin.read_text(encoding="utf-8") if origin.is_file() else None


def main() -> int:
    root = called("ssh")
    args = sys.argv[1:]
    dest = args[-2] if len(args) >= 2 else ""
    script = args[-1] if args else ""
    state = root / "ssh-state"
    remotes = root / "remotes"

    def flag(name: str) -> bool:
        return (state / f"{dest}.{name}").exists()

    if flag("down"):
        sys.stderr.write(f"ssh: connect to host {dest} port 22: No route to host\n")
        return 255
    if flag("windows"):
        return windows_host.main([str(remotes), str(state), dest, script])
    if flag("realshell"):
        # The system's PATH and not this one: the stubs on it are THIS
        # machine's tools, and another host has none of them.
        env = dict(os.environ, HOME=str(state / f"{dest}.home"), PATH="/usr/bin:/bin")
        return subprocess.run(["/bin/sh", "-c", script], env=env).returncode

    if "pane_probe.py" in script or "context.py" in script:
        slow = state / f"{dest}.slow"
        if slow.is_file():
            time.sleep(float(slow.read_text(encoding="utf-8").strip() or 0))
        answer = state / f"{dest}.{'pane-probe' if 'pane_probe.py' in script else 'context'}"
        if not answer.is_file():
            sys.stderr.write("uv: command not found\n")
            return 127
        sys.stdout.write(answer.read_text(encoding="utf-8"))
    elif "fleet-posix-ok" in script:
        if flag("nonposix"):
            sys.stderr.write("printf : The term 'printf' is not recognized as a cmdlet.\n")
            return 1
        sys.stdout.write("fleet-posix-ok")
    elif re.search(r"successfully.authenticated", script):
        if flag("noforge"):
            sys.stdout.write("github.com")
            return 1
        sys.stdout.write("github.com with an ssh key")
    elif "no-dir" in script:
        if flag("norepo"):
            sys.stdout.write("no-dir")
            return 1
        sys.stdout.write("ok")
    elif "fleet-fetched" in script:
        # A base fetch: `.nofetch` holds what git says when it fails, and a
        # checkout with no origin here fails the way git does.
        nofetch = state / f"{dest}.nofetch"
        said = nofetch.read_text(encoding="utf-8") if nofetch.is_file() else ""
        if not said and named_origin(state, dest, script) is None:
            said = "fatal: 'origin' does not appear to be a git repository\n"
        if said:
            sys.stderr.write(said)
            sys.stdout.write("fleet-fetch-failed")
            return 1
        sys.stdout.write("fleet-fetched")
    elif "fleet-no-upstream" in script:
        sys.stdout.write("fleet-no-upstream")
    elif "remote get-url origin" in script:
        origin = named_origin(state, dest, script)
        if origin is None:
            sys.stderr.write("error: No such remote 'origin'\n")
            return 2
        sys.stdout.write(origin)
    elif script.startswith("mkdir -p -- "):
        words = shlex.split(script)
        target = remotes / dest / words[3].lstrip("/")
        target.mkdir(parents=True, exist_ok=True)
        if "chmod 700" in script:
            target.chmod(0o700)
    elif script.startswith("rm -f -- "):
        words = shlex.split(script)
        for word in words[3:words.index("&&")]:
            (remotes / dest / word.lstrip("/")).unlink(missing_ok=True)
        try:
            (remotes / dest / words[-1].lstrip("/")).rmdir()
        except OSError:
            sys.stderr.write(f"rmdir: failed to remove '{words[-1]}': Directory not empty\n")
            return 1
    elif script.startswith("mv -f -- "):
        words = shlex.split(script.split(" 2>/dev/null")[0])
        src = remotes / dest / words[3].lstrip("/")
        if src.is_file():
            src.replace(remotes / dest / words[4].lstrip("/"))
    elif script.startswith("cat > "):
        target = remotes / dest / unquote(script[len("cat > "):]).lstrip("/")
        if not target.parent.is_dir():
            sys.stderr.write(f"sh: {target.name}: Directory nonexistent\n")
            return 2
        target.write_bytes(sys.stdin.buffer.read())
    elif script.startswith("cat "):
        path = unquote(script[len("cat "):])
        target = remotes / dest / path.lstrip("/")
        if not target.is_file():
            sys.stderr.write(f"cat: {path}: No such file or directory\n")
            return 1
        sys.stdout.buffer.write(target.read_bytes())
    elif "session list --json" in script:
        listing = state / f"{dest}.session-list.json"
        if listing.is_file():
            sys.stdout.write(listing.read_text(encoding="utf-8"))
        else:
            sys.stdout.write("[]")
    return 0
