"""`fleet peers` — every OTHER fleet's queue, read and never written.

  uv run fleet peers [--json]      one JSON document: this fleet and every peer,
                                   grouped by host, each with a status
  uv run fleet peers --records     the same peers for the Kanban board, on the
                                   wire the board already reads (below)
          [--refresh]              ask every peer now, whatever the cache says
          [--timeout SECONDS]      how long one peer may take (default 15)

A PEER IS ANOTHER FLEET'S LEAD, and nothing here reaches its records except by
READING them: the one thing a peer is ever asked to run is its own queue probe,
`scripts/lib/pane_probe.py` — the read-only command its own pane runs every ten
seconds. No `collect`, no `reap`, no `dispatch`, no write; `tests/pane/` holds
the peer's records byte for byte across a reading, and every ssh to the probe.

WHO THE PEERS ARE is asked of thurbox first. thurbox mirrors every host it
reaches, so `thurbox-cli session list` already holds every Mission Control on
this machine and on each host in its `hosts.toml`: the lead's NAME says it is a
lead and which fleet (the grammar `interface/fleet_reader.lua` reads, and
`tests/pane/test_agreement.py` holds the two to one spelling), its `cwd` is the
checkout, and its `backend_type` — `local-tmux`, or `ssh:<host>[:<mux>]` —
where. That is the same list the board's own lead comes from, so nothing has to
be declared for a peer the operator can already see. This checkout is never its
own peer.

`orchestration/peers.conf` is the OVERRIDE, gitignored, beside the tracked
`peers.example.conf` that names none: `PEER=<host> <path> [<name>]` adds a
fleet thurbox does not list (`local` for this machine), and `DISCOVER=off`
stops asking thurbox at all. `FLEET_PEERS_ROOT` relocates it, as every other
setting's root does.

HOW A PEER IS READ. A fleet on this machine: its probe, run by this
interpreter with the peer's checkout as the only root it sees. A fleet on a
host: the same probe through `uv run` in that checkout, over the ssh path
`fleet queue` already uses for a remote task — the host's `hosts.toml` entry,
its login shell, its platform — bounded by `--timeout`. Every peer is asked at
once, so a host that never answers costs one timeout, never one per peer.

WHAT A PEER CAN BE, and every answer is a row, never a blank board:

  ok            read just now (or inside the TTL)
  stale         the last attempt failed; `tasks` is the last good reading and
                `age` its seconds
  unreachable   no reading ever; `reason` says why and `age` how long it has
                been failing

THE CACHE is `orchestration/peers/cache.json` (`FLEET_PEERS_DIR` relocates it),
gitignored runtime state like the reconciler's: a peer asked inside `TTL`
seconds is not asked again, which keeps the board's periodic probe one process
and no ssh, and the last good reading is what makes `stale` possible.

THE JSON. `{"read_at", "ttl", "hosts": [{"host", "fleets": [{"key", "host",
"fleet", "label", "path", "self", "source", "status", "reason", "age",
"read_at", "tasks": [...]}]}]}` — `source` is `self`, `thurbox` or `config`,
and every task carries its `fleet` and `host` beside the queue's own fields.

THE BOARD'S WIRE (`--records`) is one header per peer and then that peer's
probe output verbatim, so the board parses a peer with the parser it already
has — one reading, never a second format:

  P <key> <host> <label> <status> <age seconds> <reason>
  <the peer's own R/H/T/K/B/A records>

This fleet is never on it: the board already reads it through its own probe.
No peer prints nothing at all, which is what keeps a one-fleet board unchanged.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time
import tomllib

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
probe = _load_sibling("fleet_pane_probe", "pane_probe.py")
fleet_platform = _load_sibling("fleet_platform", "fleet_platform.py")

# The lead's name, as `extension.toml.in` renders it and the pane reads it.
# `tests/pane/test_agreement.py` holds both to the Lua's spelling.
CONTROL_PLANE = "Mission Control"
FLEET_MARK = " · "

# A peer read this recently is not read again. Under the board's 30s cadence,
# because a reading is stamped after the session list and `uv run` start, and
# a TTL equal to the cadence served every other ask from the cache.
TTL = 25
# ssh's own ConnectTimeout is 10s; a login shell and `uv run` sit on top.
TIMEOUT = 15.0

# The records a probe speaks. Anything else on stdout is a login banner.
RECORD = re.compile(r"^[RHATKBE]\t")

# What would point a LOCAL peer's probe at THIS fleet's state instead of its own.
RELOCATING = ("FLEET_QUEUE_DIR", "FLEET_RUNS_DIR", "FLEET_RECONCILE_DIR", "FLEET_SHEPHERD_DIR", "FLEET_REGISTRY_FILE",
              "FLEET_PEERS_DIR")


def lead_fleet(name: str) -> str | None:
    """The fleet a session leads — "" unnamed — or None for any other session.

    `fleet_of` in `interface/fleet_reader.lua`, rule for rule: one bounded mark
    in front, then the lead's name, then the fleet's bare name.
    """
    body = name
    mark, _, rest = name.partition(" ")
    if rest and len(mark.encode("utf-8")) <= 4 and (
        rest == CONTROL_PLANE or rest.startswith(CONTROL_PLANE + FLEET_MARK)
    ):
        body = rest
    if body == CONTROL_PLANE:
        return ""
    found = re.fullmatch(re.escape(CONTROL_PLANE + FLEET_MARK) + r"([A-Za-z0-9_-]+)", body)
    return found.group(1) if found else None


def label(host: str, fleet: str, path: str) -> str:
    """What a card and the header call a fleet: where it is, then which."""
    name = fleet or os.path.basename(path.rstrip("/\\")) or path
    return f"{host}/{name}"


def peer(host: str, path: str, fleet: str, source: str) -> dict:
    return {"key": f"{host}:{path}", "host": host, "path": path, "fleet": fleet,
            "label": label(host, fleet, path), "source": source}


# --- who the peers are -------------------------------------------------------


def settings() -> tuple[bool, list[dict]]:
    """(ask thurbox?, the operator's own peers) out of peers.conf."""
    root = os.environ.get("FLEET_PEERS_ROOT") or fleetqueue.checkout_root()
    path = os.path.join(root, "orchestration", "peers.conf")
    if not os.path.isfile(path):
        path = os.path.join(root, "orchestration", "peers.example.conf")
    discover, declared = True, []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return discover, declared
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if not sep or key.strip().startswith("#"):
            continue
        key, value = key.strip(), value.strip()
        if key == "DISCOVER":
            discover = value.lower() not in ("off", "no", "false", "0")
        elif key == "PEER" and value:
            words = value.split()
            if len(words) >= 2:
                declared.append(peer(words[0], words[1], words[2] if len(words) > 2 else "", "config"))
    return discover, declared


def discovered() -> list[dict] | None:
    """Every lead thurbox lists, this machine's and every host's — or None when
    thurbox could not be asked, which is not the same as no lead anywhere."""
    sessions, _why = fleetqueue.session_snapshot()
    if sessions is None:
        return None
    found = []
    for row in (sessions or {}).values():
        fleet = lead_fleet(str(row.get("name") or ""))
        cwd = str(row.get("cwd") or "")
        if fleet is None or not cwd:
            continue
        backend = str(row.get("backend_type") or "")
        host = backend.split(":")[1] if ":" in backend and backend.split(":")[1] else "local"
        found.append(peer(host, cwd, fleet, "thurbox"))
    return found


def is_self(entry: dict) -> bool:
    if entry["host"] != "local":
        return False
    try:
        return os.path.samefile(entry["path"], fleetqueue.checkout_root())
    except OSError:
        return False


def peers(cache: dict) -> list[dict]:
    discover, declared = settings()
    found = discovered() if discover else []
    if found is None:
        # thurbox did not answer: the leads it listed last time are still the
        # best answer, and dropping them would drop their last good reading.
        found = [{k: v[k] for k in ("key", "host", "path", "fleet", "label", "source")}
                 for v in cache.values() if isinstance(v, dict) and v.get("source") == "thurbox"]
    out, seen = [], set()
    for entry in declared + found:
        if entry["key"] in seen or is_self(entry):
            continue
        seen.add(entry["key"])
        out.append(entry)
    # Two unnamed clones are both called after their folder, which is `fleet`
    # more often than not: a name two fleets share names neither, so they are
    # told apart by where they are instead.
    taken = [e["label"] for e in out] + [SELF_LABEL]
    for entry in out:
        if taken.count(entry["label"]) > 1:
            entry["label"] = f"{entry['host']}:{entry['path']}"
    return out


# --- reading one ---------------------------------------------------------------


def host_entry(name: str) -> tuple[dict | None, str]:
    """A hosts.toml entry to READ from.

    Not `fleet queue`'s `host_entry`: that one also refuses what a DISPATCH
    cannot do — a host with `share_sessions = false` — which says nothing about
    whether a queue can be read there.
    """
    path = fleetqueue.hosts_file()
    try:
        with open(path, "rb") as fh:
            hosts = [h for h in tomllib.load(fh).get("hosts") or [] if isinstance(h, dict)]
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return None, f"{path} could not be read: {exc}"
    entry = next((h for h in hosts if h.get("name") == name), None)
    if entry is None or not entry.get("destination"):
        return None, f"no host named {name!r} with a destination in {path}"
    if fleetqueue.host_platform(entry) is None:
        return None, f"host {name!r} speaks no shell fleet knows"
    return entry, ""


def remote_script(entry: dict, path: str) -> str:
    shell = fleetqueue.host_shell(entry)
    script = shell.join(shell.join(shell.join(path, "scripts"), "lib"), "pane_probe.py")
    if shell is fleetqueue.POSIX:
        return f"uv run --project {shlex.quote(path)} --frozen --quiet python {shlex.quote(script)}"
    # PowerShell 5 re-encodes a native command's output through the console
    # code page, which turns every non-ASCII title into mojibake on the way.
    return ("[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()\n"
            f"& uv run --project {fleetqueue.ps_quote(path)} --frozen --quiet python {fleetqueue.ps_quote(script)}")


def ask(entry: dict, timeout: float) -> tuple[list[str] | None, str]:
    """(the peer's records, "") or (None, why it could not be read)."""
    try:
        if entry["host"] == "local":
            script = os.path.join(entry["path"], "scripts", "lib", "pane_probe.py")
            if not os.path.isfile(script):
                return None, f"no fleet checkout at {entry['path']}"
            env = {k: v for k, v in os.environ.items() if k not in RELOCATING}
            done = subprocess.run([sys.executable, script], cwd=entry["path"], env=env,
                                  capture_output=True, timeout=timeout)
        else:
            host, why = host_entry(entry["host"])
            if host is None:
                return None, why
            argv = fleetqueue.ssh_argv(host) + [
                fleetqueue.host_shell(host).command(remote_script(host, entry["path"]), True)]
            done = subprocess.run(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"no answer in {timeout:g}s"
    except OSError as exc:
        return None, str(exc)
    proc = subprocess.CompletedProcess(done.args, done.returncode, fleetqueue.ssh_text(done.stdout),
                                       fleetqueue.ssh_text(done.stderr))
    records = [line for line in proc.stdout.splitlines() if RECORD.match(line)]
    error = next((line.split("\t", 1)[1] for line in records if line.startswith("E\t")), "")
    if error:
        return None, error
    if not any(line.startswith("R\t") for line in records):
        return None, fleetqueue.first_line(proc) or "its queue probe answered nothing"
    return records, ""


# --- the cache -----------------------------------------------------------------


def cache_path() -> str:
    root = os.environ.get("FLEET_PEERS_DIR") or os.path.join(fleetqueue.checkout_root(), "orchestration", "peers")
    return os.path.join(root, "cache.json")


def load_cache() -> dict:
    try:
        with open(cache_path(), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def save_cache(doc: dict) -> None:
    """One writer at a time: `write_record`'s temporary file has one name, so two
    callers at once — the board's probe and a person — could publish half a file.
    The one that finds the lock held leaves the write to the one holding it.
    A cache that cannot be written costs the next reading an ssh, never this one.
    """
    try:
        os.makedirs(os.path.dirname(cache_path()), exist_ok=True)
        with fleet_platform.exclusive_lock(cache_path() + ".lock"):
            fleet_platform.write_record(cache_path(), json.dumps(doc, indent=1) + "\n")
    except (OSError, fleet_platform.LockHeld):
        pass


def read_all(entries: list[dict], old: dict, refresh: bool, timeout: float) -> list[dict]:
    """Every peer with its status, asking only those the cache cannot answer."""
    now = time.time()
    due = [e for e in entries if refresh or now - float((old.get(e["key"]) or {}).get("tried_at") or 0) >= TTL]
    answers = {}
    if due:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(due)) as pool:
            for entry, answer in zip(due, pool.map(lambda e: ask(e, timeout), due)):
                answers[entry["key"]] = answer
    cache, out = {}, []
    for entry in entries:
        seen = dict(old.get(entry["key"]) or {})
        if entry["key"] in answers:
            records, why = answers[entry["key"]]
            seen["tried_at"] = now
            if records is not None:
                seen.update(records=records, ok_at=now, error="", failing_since=None)
            else:
                seen["error"] = why
                seen["failing_since"] = seen.get("failing_since") or now
        seen.update({k: entry[k] for k in ("key", "host", "path", "fleet", "label", "source")})
        cache[entry["key"]] = seen
        error = seen.get("error") or ""
        if not error:
            status, age = "ok", now - float(seen.get("ok_at") or now)
        elif seen.get("records"):
            status, age = "stale", now - float(seen.get("ok_at") or now)
        else:
            status, age = "unreachable", now - float(seen.get("failing_since") or now)
        out.append({**entry, "self": False, "status": status, "reason": error, "age": int(age),
                    "read_at": int(seen.get("ok_at") or 0) or None, "records": seen.get("records") or []})
    save_cache(cache)
    return out


# --- what it prints ------------------------------------------------------------


def tasks(records: list[str], fleet: str, host: str) -> list[dict]:
    """The probe's records as tasks, by the field names pane_probe.py documents."""
    out, topic, title, by_ref = [], "", "", {}
    for line in records:
        f = line.split("\t")
        if f[0] == "T":
            topic, title = f[1], f[2] if len(f) > 2 else ""
        elif f[0] == "K" and len(f) >= 15:
            blockers = []
            for pair in filter(None, f[6].split(",")):
                ref, _, kind = pair.partition("|")
                blockers.append({"ref": ref.removeprefix("!"), "kind": kind, "condition": ref.startswith("!")})
            task = {
                "fleet": fleet, "host": host, "topic": topic, "topic_title": title, "id": f[1],
                "ref": f"{topic}/{f[1]}", "state": f[2], "title": f[3], "outcome": f[4], "artifact": f[5],
                "blocked_by": blockers, "branch": f[10], "moved_at": int(f[11] or 0),
                "publish": {"method": f[12], "state": f[13], "at": int(f[14] or 0)},
            }
            by_ref[task["ref"]] = task
            out.append(task)
        elif f[0] == "B" and f[1] in by_ref and len(f) >= 8:
            by_ref[f[1]].update(agent=f[2], worker_host=f[3], session=f[4], review=f[5])
            by_ref[f[1]]["publish"]["detail"] = f[6]
            by_ref[f[1]]["publish"]["threads"] = int(f[7]) if f[7].isdigit() else None
    return out


# What the board calls this fleet when it is unnamed: `local/this`.
SELF_LABEL = "local/this"


def own() -> dict:
    root = fleetqueue.checkout_root()
    records = probe.records(fleetqueue.queue_root())
    error = next((line.split("\t", 1)[1] for line in records if line.startswith("E\t")), "")
    return {**peer("local", root, "", "self"), "label": SELF_LABEL, "self": True, "status": "unreachable" if error else "ok",
            "reason": error, "age": 0, "read_at": int(time.time()), "records": records}


def document(fleets: list[dict]) -> dict:
    hosts: dict[str, list] = {}
    for entry in fleets:
        records = entry.pop("records")
        entry["tasks"] = tasks(records, entry["fleet"], entry["host"])
        hosts.setdefault(entry["host"], []).append(entry)
    return {"read_at": int(time.time()), "ttl": TTL,
            "hosts": [{"host": h, "fleets": hosts[h]} for h in sorted(hosts, key=lambda h: (h != "local", h))]}


def flat(value) -> str:
    return probe.flat(value)


def wire(fleets: list[dict]) -> str:
    out = []
    for entry in fleets:
        out.append("\t".join(["P", flat(entry["key"]), flat(entry["host"]), flat(entry["label"]),
                              entry["status"], str(entry["age"]), flat(entry["reason"])]))
        out.extend(entry["records"])
    return "".join(line + "\n" for line in out)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="fleet peers", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    shape = parser.add_mutually_exclusive_group()
    shape.add_argument("--json", action="store_true", help="one JSON document (the default)")
    shape.add_argument("--records", action="store_true", help="the Kanban board's wire")
    parser.add_argument("--refresh", action="store_true", help="ask every peer now")
    parser.add_argument("--timeout", type=float, default=TIMEOUT, help="seconds one peer may take")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    cache = load_cache()
    read = read_all(peers(cache), cache, args.refresh, args.timeout)
    if args.records:
        sys.stdout.write(wire(read))
    else:
        print(json.dumps(document([own(), *read]), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
