"""`thurbox-cli`: sessions from files, and every mutation logged instead of performed.

`session list|get` read one JSON file per session id under `sessions/`, so a
test says what a session is doing, and `session capture` reads its pane from
`panes/`. `session create|delete|restart|send` only
land in `calls.log`, so a test asserts on exactly what would have been spawned
or killed — including that nothing was. `watch --json` replays `watch.jsonl`
whole, whatever `--since` says, which is what a recorded stream does: the floor
is the queue's to apply. `config show` is only ever read to LOCATE hosts.toml,
and points at the fixture's, so no run can read the operator's own.

`message send|inbox` model the mailbox: a send appends to `inbox/<to>.jsonl`
and `inbox` reads it back, `--claim` marking what it returns read. A send
without `--no-wake` is recorded as `woke`, which is the real CLI pushing the
body into the recipient's conversation. A `no-inbox` file makes `message` the
unknown subcommand it is on a thurbox that predates the mailbox.
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
        # `panes/<id>.json` is the whole `--json` answer, cursor and all; a
        # bare `<id>.txt` is the text alone, with no cursor to read.
        shown = read(root / "panes" / f"{ident}.json")
        print(shown.strip() or json.dumps({"output": read(root / "panes" / f"{ident}.txt")}))
    elif verb == "session send":
        # `--no-enter` leaves the text in the composer. The real CLI types it
        # into the pane; the stub writes the same `→ <text>` line capture
        # reads, so a type-then-verify-then-submit loop can see it.
        if "--no-enter" in args:
            pos = [a for a in args[2:] if not a.startswith("-")]
            if len(pos) >= 2:
                pane = root / "panes" / f"{pos[0]}.txt"
                pane.parent.mkdir(parents=True, exist_ok=True)
                pane.write_text(f"→ {pos[1]}\n", encoding="utf-8", newline="\n")
    elif verb in ("session stop", "session start", "session restart"):
        print(json.dumps({"id": ident, "restarted": True}))
    elif verb == "session list":
        answer = root / "session-list.json"
        if answer.is_file():
            sys.stdout.write(read(answer))
            return 0
        sessions = sorted((root / "sessions").glob("*.json"))
        print(json.dumps([json.loads(read(s)) for s in sessions]))
    elif verb == "session get":
        record = root / "sessions" / f"{ident}.json"
        if not record.is_file():
            sys.stderr.write(f"no such session: {ident}\n")
            return 1
        sys.stdout.write(read(record))
    elif args[:1] == ["message"]:
        return mailbox(root, args[1:])
    elif args[:1] == ["watch"]:
        sys.stdout.write(read(root / "watch.jsonl"))
    return 0


def flag(args: list, name: str) -> str:
    return args[args.index(name) + 1] if name in args[:-1] else ""


def mailbox(root, args: list) -> int:
    if (root / "no-inbox").exists():
        sys.stderr.write("error: unrecognized subcommand 'message'\n")
        return 2
    verb = args[0] if args else ""
    if verb == "send":
        to = flag(args, "--to")
        box = root / "inbox" / f"{to}.jsonl"
        box.parent.mkdir(parents=True, exist_ok=True)
        rows = [json.loads(line) for line in read(box).splitlines() if line]
        row = {
            "id": len(rows) + 1,
            "kind": flag(args, "--kind"),
            "body": flag(args, "--body"),
            "woke": "--no-wake" not in args,
            "read_at": None,
        }
        with open(box, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row) + "\n")
        via = "mailbox" if "--no-wake" in args else "claude-socket"
        print(json.dumps({"enqueued": True, "message_id": row["id"], "delivered_via": via}))
    elif verb == "inbox":
        box = root / "inbox" / f"{flag(args, '--for')}.jsonl"
        rows = [json.loads(line) for line in read(box).splitlines() if line]
        shown = rows if "--all" in args and "--claim" not in args else [
            r for r in rows if r["read_at"] is None
        ]
        if "--claim" in args:
            for r in shown:
                r["read_at"] = 1
            box.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        print(json.dumps(shown))
    else:
        sys.stderr.write(f"error: unrecognized subcommand {verb!r}\n")
        return 2
    return 0
