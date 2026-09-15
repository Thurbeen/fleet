"""The hosts a remote task can name, and the fake machines behind them.

`devbox` is the ordinary POSIX host; `profilebox` is the same shape with a real
shell behind it, for the login-`PATH` question; `winbox` is a Windows host,
answered by `fleet_stubs/windows_host.py`; `oddbox` and `lonebox` are the
shapes fleet refuses outright, at `add` time, without contacting any host.

`profilebox`'s account is an account as it really is: a `~/.local/bin` only the
profile puts on `PATH`, a binary in it, and a profile that PRINTS — because
plenty do, and a banner glued to the front of a fetched `result.md` would be a
worse bug than the one being fixed.
"""

import json
import stat

from harness import Stubs, write

HOSTS_TOML = """\
[[hosts]]
name = "devbox"
destination = "me@devbox"

[[hosts]]
name = "profilebox"
destination = "me@profilebox"

[[hosts]]
name = "winbox"
destination = "me@winbox"
multiplexer = "psmux"

[[hosts]]
name = "oddbox"
destination = "me@oddbox"
multiplexer = "zellij"

[[hosts]]
name = "lonebox"
destination = "me@lonebox"
share_sessions = false
"""


class Hosts:
    def __init__(self, stubs: Stubs):
        self.stubs = stubs
        self.state = stubs.root / "ssh-state"
        self.remotes = stubs.root / "remotes"
        write(stubs.root / "hosts.toml", HOSTS_TOML)

        home = self.state / "me@profilebox.home"
        write(home / ".profile",
              f"echo 'Welcome to profilebox.'\nPATH=\"{home}/.local/bin:$PATH\"\nexport PATH\n")
        agent = home / ".local" / "bin" / "fleet-fake-agent"
        write(agent, "#!/bin/sh\necho fleet-fake-agent 1.2.3\n")
        agent.chmod(agent.stat().st_mode | stat.S_IXUSR)
        self.flag("me@profilebox", "realshell")
        self.flag("me@winbox", "windows")

    def flag(self, dest: str, name: str) -> None:
        write(self.state / f"{dest}.{name}", "")

    def unflag(self, dest: str, name: str) -> None:
        (self.state / f"{dest}.{name}").unlink(missing_ok=True)

    def read_flag(self, dest: str, name: str) -> str:
        path = self.state / f"{dest}.{name}"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def next_session(self, sid: str) -> None:
        write(self.stubs.root / "next-session.json", json.dumps({"id": sid, "created": True}) + "\n")

    def session(self, sid: str, **doc) -> None:
        write(self.stubs.root / "sessions" / f"{sid}.json", json.dumps({"id": sid, **doc}) + "\n")

    def remote(self, dest: str, path: str):
        """Where a path on a fake host lives here: `C:\\x\\y` is spelled as `C/x/y`."""
        return self.remotes / dest / path.replace(":", "").replace("\\", "/").lstrip("/")


def creates(stubs: Stubs) -> list[str]:
    return stubs.calls("thurbox-cli", "session create")


def deletions(stubs: Stubs) -> str:
    return "\n".join(stubs.calls("thurbox-cli", "session delete"))
