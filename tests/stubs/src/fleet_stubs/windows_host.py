"""A Windows host behind ssh, which answers ONLY what a PowerShell 5 sshd answers.

A command that is not `powershell ... -EncodedCommand <base64>` is POSIX shell
sent to the wrong machine, and it fails the way it fails there — and is logged
to `<dest>.posix`, the tripwire, so a POSIX command reaching this host is a
named failure rather than an empty answer. A command that IS encoded is decoded
from UTF-16LE and answered on what it asks, and the brief push and result fetch
move real bytes through base64 into `remotes`, with the host's `C:\\...` spelled
as a directory tree under it.

    python3 windows_host.py <remotes> <state> <destination> <script>
"""

import base64
import os
import re
import sys

PREFIX = "powershell -NoProfile -NonInteractive -EncodedCommand "


def main(argv: list) -> int:
    remotes, state, dest, script = argv[:4]

    def flag(name):
        return os.path.exists(os.path.join(state, f"{dest}.{name}"))

    def local(path):
        return os.path.join(remotes, dest, path.replace(":", "").replace("\\", "/").lstrip("/"))

    def literal(pattern, body):
        m = re.search(pattern + r"'((?:[^']|'')*)'", body)
        return m.group(1).replace("''", "'") if m else ""

    if not script.startswith(PREFIX):
        with open(os.path.join(state, f"{dest}.posix"), "a", encoding="utf-8") as fh:
            fh.write(script + "\n")
        word = (script.split() or ["?"])[0]
        sys.stderr.write(
            f"{word} : The term '{word}' is not recognized as the name of a cmdlet, "
            "function, script file, or operable program.\n"
        )
        return 1

    body = base64.b64decode(script[len(PREFIX):]).decode("utf-16-le")
    with open(os.path.join(state, f"{dest}.commands"), "a", encoding="utf-8") as fh:
        fh.write(body + "\n---\n")

    if "fleet-powershell-ok" in body:
        print("fleet-powershell-ok")
    elif "'no-dir'" in body:
        if flag("norepo"):
            print("no-dir")
            return 1
        print("ok")
    elif "with an ssh key" in body:
        if flag("noforge"):
            print("github.com|`gh`")
            return 1
        print("github.com with an ssh key")
    elif "FromBase64String" in body:
        target = local(literal(r"WriteAllBytes\(", body))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(base64.b64decode(sys.stdin.read().strip()))
        print("fleet-wrote")
    elif "ToBase64String" in body:
        target = local(literal(r"-LiteralPath ", body))
        if not os.path.isfile(target):
            print("fleet-no-file")
            return 1
        with open(target, "rb") as fh:
            print(base64.b64encode(fh.read()).decode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
