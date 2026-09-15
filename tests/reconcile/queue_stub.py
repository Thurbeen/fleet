"""The queue command, stood in for: it records every call and answers as the real one does.

`root` prints a path (the loop's precondition), `watch` blocks for --for-secs and
prints the summary line whose "N task(s) moved" the loop reads, and `plan`
prints the ready set the test drives. It is itself an assertion: a verb it was
not taught is refused, so a reconciler that grew a call to `dispatch` fails.

Every path it reads comes from the environment, beside FLEET_RECONCILE_DIR when
unset, so it also serves a hand-run demonstration with nothing else configured.
"""

import json
import os
import sys
import time
from pathlib import Path


def spot(var: str, name: str) -> Path:
    value = os.environ.get(var)
    if value:
        return Path(value)
    return Path(os.environ.get("FLEET_RECONCILE_DIR", ".")).parent / name


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def main(argv: list[str]) -> int:
    with open(spot("RECON_CALLS", "calls"), "a", encoding="utf-8", newline="\n") as fh:
        fh.write(" ".join(argv) + "\n")
    verb = argv[0] if argv else ""
    if verb == "root":
        # `root --foreign` is the control-plane guard's question, and the honest
        # answer for a throwaway queue is "this IS the control plane".
        if argv[1:2] == ["--foreign"]:
            return 1
        print(os.environ.get("FLEET_QUEUE_DIR", "/nowhere"))
    elif verb == "watch":
        # The real `watch` BLOCKS for --for-secs. One that returned at once would
        # hide the loop's pacing floor.
        if argv[1:2] == ["--for-secs"]:
            time.sleep(float(argv[2]))
        moved = read(spot("RECON_MOVED", "moved")).strip() or "0"
        print(f"watch: {moved} task(s) moved, stream at seq 7")
    elif verb == "collect":
        print("collect: nothing new")
    elif verb == "shepherd":
        print("shepherd: 0 open pull request(s)")
    elif verb == "refuel":
        print("refuel: no task here is holding a session")
    elif verb == "plan":
        ready = read(spot("RECON_READY", "ready")).split()
        print(json.dumps({"ready": ready, "waiting": [], "overlaps": []}))
    else:
        sys.stderr.write(f"queue-stub: REFUSING an unexpected subcommand: {' '.join(argv)}\n")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
