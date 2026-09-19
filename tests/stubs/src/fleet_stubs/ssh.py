"""`ssh`: a fake host with a real filesystem under `remotes/<destination>`.

`ssh [opts...] <destination> <script>` — only the last two arguments matter.
`cat > path` writes into the fake host and `cat path` reads back out, which is
what makes a brief push and a result fetch provable rather than asserted.

A host is shaped by a flag file under `ssh-state/`: `<dest>.down` for an
unreachable one, `.nonposix` for a Windows-shaped shell, `.noforge` for one with
no GitHub credentials, `.norepo` for one where the repo is not there.
`.windows` hands the command to `windows_host`, which answers only what a
PowerShell 5 sshd answers. `.realshell` RUNS the script with `sh -c`, the way
sshd runs an account's login shell, with HOME at `<dest>.home` — POSIX only.
"""

import os
import re
import subprocess
import sys

from fleet_stubs import called, windows_host


def unquote(path: str) -> str:
    return re.sub(r"'$", "", re.sub(r"^'", "", path))


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
        env = dict(os.environ, HOME=str(state / f"{dest}.home"))
        return subprocess.run(["/bin/sh", "-c", script], env=env).returncode

    if "fleet-posix-ok" in script:
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
    elif script.startswith("cat > "):
        target = remotes / dest / unquote(script[len("cat > "):]).lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(sys.stdin.buffer.read())
    elif script.startswith("cat "):
        path = unquote(script[len("cat "):])
        target = remotes / dest / path.lstrip("/")
        if not target.is_file():
            sys.stderr.write(f"cat: {path}: No such file or directory\n")
            return 1
        sys.stdout.buffer.write(target.read_bytes())
    elif "thurbox-cli session list" in script:
        listing = state / f"{dest}.session-list.json"
        if listing.is_file():
            sys.stdout.write(listing.read_text(encoding="utf-8"))
        else:
            sys.stdout.write("[]")
    return 0
