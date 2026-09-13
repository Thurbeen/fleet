"""`thurbox-cli`: sessions from files, and every mutation logged instead of performed.

`session list|get` read one JSON file per session id under `sessions/`, so a
test says what a session is doing. `session create|delete|restart|send` only
land in `calls.log`, so a test asserts on exactly what would have been spawned
or killed — including that nothing was. `watch --json` replays `watch.jsonl`
whole, whatever `--since` says, which is what a recorded stream does: the floor
is the queue's to apply. `config show` is only ever read to LOCATE hosts.toml,
and points at the fixture's, so no run can read the operator's own.
"""

import json
import sys

from fleet_stubs import called, read


def main() -> int:
    root = called("thurbox-cli")
    args = sys.argv[1:]
    verb = " ".join(args[:2])
    ident = args[2] if len(args) > 2 else ""

    if verb == "config show":
        print(json.dumps({"paths": {"hosts_toml": str(root / "hosts.toml")}}))
    elif verb == "session create":
        print(read(root / "next-session.json").strip() or '{"id":"stub","created":true}')
    elif verb == "session capture":
        print(json.dumps({"output": read(root / "panes" / f"{ident}.txt")}))
    elif verb == "session restart":
        print(json.dumps({"id": ident, "restarted": True}))
    elif verb == "session list":
        sessions = sorted((root / "sessions").glob("*.json"))
        print(json.dumps([json.loads(read(s)) for s in sessions]))
    elif verb == "session get":
        record = root / "sessions" / f"{ident}.json"
        if not record.is_file():
            sys.stderr.write(f"no such session: {ident}\n")
            return 1
        sys.stdout.write(read(record))
    elif args[:1] == ["watch"]:
        sys.stdout.write(read(root / "watch.jsonl"))
    return 0
