"""The TUI queue pane's queue probe: the whole queue, one record per line.

WHY PYTHON AND NOT A SHELL SCRIPT. thurbox runs a pane's probe through `sh -c`
on POSIX and `cmd /C` on Windows, so the only probe that runs on both is one
plain command line. interface/fleet_queue.lua runs this as
`uv run --frozen --quiet python scripts/lib/pane_probe.py`, in the checkout the
Mission Control session opens, and everything the probe used to do with sed,
awk and date happens here. The queue root is queue.py's own answer.

THE FORMAT, tab-separated because a tab is the one character no field carries:

  R <queue root>
  E <what went wrong>
  A <archived topic count>
  T <topic slug> <topic title>
  K <id> <state> <title> <outcome> <artifact> <blockers> <brief> <events>
    <result> <branch> <moved-at, epoch seconds> <publish-method>
    <publish-state> <publish-at, epoch seconds>

`<blockers>` is `ref|kind` pairs, comma separated. A CONDITION rides in the
same field behind a `!` no task ref can begin with, flattened of the `,` and
`|` this encoding owns. `<events>` is drawn by nothing any more and still
emitted, because renumbering fourteen positional fields is the worse trade.
`moved-at` is concluded, else dispatched, else created — a fact about the
record, resolved beside it rather than in a renderer that has no clock.

IT NEVER RELIES ON AN EXIT STATUS. Every outcome it can tell apart is spelled
on stdout and it exits 0; the pane reads only a probe that could not RUN, or
said nothing, as a failure. Output is UTF-8 whatever the console's code page.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fleetqueue = _load_sibling("fleet_queue", "queue.py")


def flat(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def epoch(stamp) -> int:
    return int(fleetqueue.record_time(stamp)) if stamp else 0


def load(path: str) -> dict | None:
    # queue.py's loader, which is libyaml where the interpreter has it: this
    # probe runs on every redraw of the pane, and the pure-Python parser was
    # nine tenths of what a redraw cost.
    try:
        with open(path, encoding="utf-8") as fh:
            doc = fleetqueue.load_yaml(fh)
    except (OSError, yaml.YAMLError):
        return None
    return doc if isinstance(doc, dict) else None


def blockers(doc: dict) -> str:
    edges = []
    for edge in doc.get("blocked_by") or []:
        if not isinstance(edge, dict):
            continue
        if edge.get("task"):
            ref = flat(edge["task"])
        elif edge.get("condition"):
            ref = "!" + flat(edge["condition"]).replace(",", " ").replace("|", " ")
        else:
            continue
        edges.append(f"{ref}|{flat(edge.get('kind'))}")
    return ",".join(edges)


def artifact(doc: dict) -> str:
    """The URL the pane draws a link to.

    A task that spans repositories records one artifact per repository, and a
    row is ONE line: it names the first and the pane's `url:` verb opens that
    one. `fleet queue show` is where all of them are, and it says so.
    """
    urls = [
        a["url"]
        for a in fleetqueue.artifact_entries(doc.get("artifact"), str(doc.get("repo") or ""))
        if a["url"]
    ]
    return urls[0] if urls else ""


def task_record(task_dir: str, doc: dict) -> str:
    moved = next((doc[k] for k in ("concluded_at", "dispatched_at", "created") if doc.get(k)), None)
    publish = doc.get("publish") if isinstance(doc.get("publish"), dict) else {}
    events = 0
    progress = os.path.join(task_dir, "progress.jsonl")
    if os.path.isfile(progress):
        with open(progress, "rb") as fh:
            events = sum(1 for _ in fh)
    return "\t".join([
        "K", flat(doc.get("id")), flat(doc.get("state")), flat(doc.get("title")), flat(doc.get("outcome")),
        flat(artifact(doc)), blockers(doc),
        "1" if os.path.isfile(os.path.join(task_dir, "BRIEF.md")) else "0",
        str(events),
        "1" if os.path.isfile(os.path.join(task_dir, "result.md")) else "0",
        flat(doc.get("branch")), str(epoch(moved)),
        flat(publish.get("method")), flat(publish.get("state")), str(epoch(publish.get("at"))),
    ])


def records(root: str) -> list[str]:
    if not os.path.isdir(root):
        return [f"E\tno queue directory at {root}"]
    out = [f"R\t{root}"]
    archived = 0
    for topic in sorted(os.listdir(root)):
        topic_dir = os.path.join(root, topic)
        topic_file = os.path.join(topic_dir, "topic.yaml")
        if not os.path.isfile(topic_file):
            continue
        topic_doc = load(topic_file) or {}
        # The flag lives on topic.yaml so the hidden case is the cheap one: its
        # task directories are never opened.
        if topic_doc.get("archived") not in (None, ""):
            archived += 1
            continue
        out.append(f"T\t{topic}\t{flat(topic_doc.get('title'))}")
        for task in sorted(os.listdir(topic_dir)):
            task_dir = os.path.join(topic_dir, task)
            doc = load(os.path.join(task_dir, "task.yaml")) if os.path.isdir(task_dir) else None
            if doc is not None:
                out.append(task_record(task_dir, doc))
    out.append(f"A\t{archived}")
    return out


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        lines = records(fleetqueue.queue_root())
    except Exception as exc:  # the pane draws an E record; a traceback it cannot
        lines = [f"E\tthe queue probe failed: {exc}"]
    sys.stdout.write("".join(line + "\n" for line in lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
