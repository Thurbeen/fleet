#!/usr/bin/env python3
"""`fleet watch`: wait for a pipeline or a change request to finish, one line per change.

    uv run fleet watch <pipeline-url | change-request-url | task-ref> [options]

      --until checks|merged   a change request: stop once no check is still
                              running (default), or once it merged or closed
      --follow                after the merge, find the pipeline the merge
                              commit started on the base branch, watch it, and
                              print the lines of its job logs that --match finds
      --match REGEX           what --follow prints from a job log (default: a
                              Terraform apply's or destroy's summary line)
      --grace SECONDS         --until checks: stop once this long passes with
                              no check reported at all (default 300)
      --interval SECONDS      between reads (default 30)
      --timeout SECONDS       give up after this long (default 3600)

    exit 0   passed, or merged
    exit 1   failed, cancelled, closed unmerged, or waiting on a person (manual)
    exit 2   nothing here to watch
    exit 3   no check was reported within --grace: nothing to wait for
    exit 124 timed out, which is no verdict about the thing watched

WHY THIS EXISTS. Every lead wrote its own poller out of `gh` and `glab` calls
and `sleep`s: hundreds of hand forge calls per lead, parsers that broke on
emoji job names and on shell quoting, background jobs that died with a wiped
scratchpad, and one coordinator that printed merges which never happened
because of one wrong flag. This is that poller, written once.

WHAT IT READS. Only the forge seam (`scripts/lib/forge.py`): `pipeline` for a
pipeline's jobs, `get` for a change request — the same normalised record
shepherd classifies — and `pipelines_for_commit` and `job_log` for `--follow`.
A job name is data and is never parsed, so an emoji, a slash or a space
survives whole; a control character in one is flattened so that one change
stays one line. Each job is its latest attempt, so a retry that passed reads as
passed. A read that fails is printed once and read again: an unreachable forge
is not a verdict, and neither is a timeout.

A task ref names its change requests through its record, exactly as
`queue list --live` reads them. Nothing here writes a record.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
import time


def _load_queue():
    """queue.py under the key every other loader uses — one forge registry per process."""
    if "fleet_queue" in sys.modules:
        return sys.modules["fleet_queue"]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.py")
    spec = importlib.util.spec_from_file_location("fleet_queue", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fleet_queue"] = module
    spec.loader.exec_module(module)
    return module


fleetqueue = _load_queue()
forge = fleetqueue.forge

# A pipeline verdict nothing will change without a person.
PIPELINE_DONE = ("passed", "failed", "cancelled", "manual")
SUCCESS = ("passed", "merged", "checks passed")
TIMED_OUT = 124
NO_CHECKS = "no check reported"
NOTHING_TO_WAIT_FOR = 3
DEFAULT_MATCH = r"Apply complete!|Destroy complete!"
CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def one_line(text: str) -> str:
    return CONTROL.sub(" ", ANSI.sub("", str(text or ""))).strip()


def say(text: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {text}", flush=True)


class Clock:
    def __init__(self, timeout: float, interval: float):
        self.start = time.monotonic()
        self.deadline = self.start + max(0.0, timeout)
        self.interval = max(0.0, interval)

    def expired(self) -> bool:
        return time.monotonic() >= self.deadline

    def wait(self) -> None:
        time.sleep(min(self.interval, max(0.0, self.deadline - time.monotonic())))

    def elapsed(self) -> str:
        s = int(time.monotonic() - self.start)
        return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def watch_pipeline(which, ref, clock: Clock) -> tuple:
    """(verdict | 'timeout', the last Pipeline read or None)."""
    jobs: dict = {}
    verdict, why_seen, last = None, "", None
    while True:
        p, why = which.pipeline(ref)
        if p is None:
            if why != why_seen:
                say(f"{ref.url}  could not read: {why}")
                why_seen = why
        else:
            why_seen, last = "", p
            for job in p.jobs:
                name = one_line(job.name) or f"job {job.id}"
                if jobs.get(name) != job.verdict:
                    say(f"{ref.url}  {name}: {job.verdict}")
                    jobs[name] = job.verdict
            if p.verdict != verdict:
                say(f"{ref.url}  pipeline: {p.verdict}")
                verdict = p.verdict
            if p.verdict in PIPELINE_DONE:
                return p.verdict, p
        if clock.expired():
            return "timeout", last
        clock.wait()


def cr_outcome(cr, until: str, graced: bool = False) -> str:
    """The word a change request's watch stops on, or '' to keep watching.

    `graced`: the grace for a first check has passed. A change request on a
    repository with no CI never gets one, and waiting the full timeout for it
    would end in a timeout that says nothing true.
    """
    if cr.state in ("merged", "closed"):
        return cr.state
    if until != "checks" or cr.state != "open":
        return ""
    if not cr.checks:
        return NO_CHECKS if graced else ""
    verdicts = {c.verdict for c in cr.checks}
    if "pending" in verdicts:
        return ""
    return "checks passed" if verdicts == {"passed"} else "checks failed"


def watch_changes(pairs: list, until: str, clock: Clock, grace: float = 0.0) -> dict:
    """url -> (outcome | 'timeout', the last ChangeRequest read or None)."""
    seen: dict = {}
    done: dict = {}
    while True:
        for which, ref in pairs:
            if ref.url in done:
                continue
            cr, why = which.get(ref)
            line = fleetqueue.live_summary(cr) if cr else f"could not read: {why}"
            name = cr.name if cr else ref.url
            if seen.get(ref.url) != line:
                say(f"{name}  {line}")
                seen[ref.url] = line
            graced = time.monotonic() - clock.start >= grace
            outcome = cr_outcome(cr, until, graced) if cr else ""
            if outcome:
                done[ref.url] = (outcome, cr)
        if len(done) == len(pairs):
            return done
        if clock.expired():
            return {ref.url: done.get(ref.url, ("timeout", None)) for _, ref in pairs}
        clock.wait()


def follow(which, cr, match: re.Pattern, clock: Clock) -> str:
    """From a merge to the base branch's pipeline, and what its jobs' logs said."""
    if not cr.merge_sha:
        say(f"{cr.name}  merged, but the forge named no merge commit: nothing to follow")
        return "merged"
    refs, why_seen = [], None
    while not refs:
        refs, why = which.pipelines_for_commit(cr.repo, cr.merge_sha)
        if not refs and why != why_seen:
            say(f"{cr.name}  could not list the pipelines of {cr.merge_sha[:12]}: {why}" if why
                else f"{cr.name}  no pipeline on {cr.merge_sha[:12]} yet; waiting for one")
            why_seen = why
        if refs:
            break
        if clock.expired():
            return "timeout"
        clock.wait()
    worst = "passed"
    for ref in refs:
        say(f"{cr.name}  following {cr.merge_sha[:12]} on {cr.base_branch or 'the base branch'}: {ref.url}")
        verdict, p = watch_pipeline(which, ref, clock)
        for job in (p.jobs if p else []):
            if job.verdict not in ("passed", "failed"):
                continue
            text, why = which.job_log(ref, job)
            if why:
                say(f"{ref.url}  {one_line(job.name)}: log not read — {why}")
            for raw in text.splitlines():
                line = one_line(raw.rsplit("\t", 1)[-1])
                if match.search(line):
                    say(f"{ref.url}  {one_line(job.name)}: {line}")
        if verdict != "passed":
            worst = verdict
    return worst


def resolve(target: str) -> tuple:
    """('pipeline', [(forge, PipelineRef)]) | ('changes', [(forge, ChangeRef)]), or (None, why)."""
    if "://" not in target:
        try:
            task = fleetqueue.Queue(fleetqueue.queue_root(), scope="all").get(target)
        except fleetqueue.QueueError as exc:
            return None, str(exc).splitlines()[0]
        pairs, reasons = [], []
        for entry in fleetqueue.recorded_artifacts(task):
            which, ref = forge.for_url(str(entry.get("url") or ""))
            if which is None:
                reasons.append(f"{entry.get('url')}: {ref}")
            else:
                pairs.append((which, ref))
        if not pairs:
            return None, f"{task.ref} records no change request fleet can ask about" + (
                f" ({'; '.join(reasons)})" if reasons else "")
        return "changes", pairs
    which, ref = forge.for_pipeline(target)
    if which is not None:
        return "pipeline", [(which, ref)]
    pipeline_why = ref
    which, ref = forge.for_url(target)
    if which is not None:
        return "changes", [(which, ref)]
    return None, ref if forge.change_url(target) else pipeline_why


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(prog="fleet watch", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="a pipeline URL, a change request URL, or a task ref")
    parser.add_argument("--until", choices=("checks", "merged"), default="checks")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--match", default=DEFAULT_MATCH)
    parser.add_argument("--grace", type=float, default=300.0)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--timeout", type=float, default=3600.0)
    args = parser.parse_args(argv)
    try:
        match = re.compile(args.match)
    except re.error as exc:
        print(f"watch: --match is not a regular expression: {exc}", file=sys.stderr)
        return 2

    kind, pairs = resolve(args.target)
    if kind is None:
        print(f"watch: {args.target} is not a pipeline, change request or task fleet can watch: "
              f"{pairs}", file=sys.stderr)
        return 2
    clock = Clock(args.timeout, args.interval)
    try:
        if kind == "pipeline":
            which, ref = pairs[0]
            verdict, p = watch_pipeline(which, ref, clock)
            bad = [one_line(j.name) for j in (p.jobs if p else []) if j.verdict in ("failed", "cancelled", "manual")]
            return finish(ref.url, verdict, clock, last=p.verdict if p else "nothing read", bad=bad)
        until = "merged" if args.follow else args.until
        results = watch_changes(pairs, until, clock, args.grace)
        outcomes = []
        for which, ref in pairs:
            outcome, cr = results[ref.url]
            if args.follow and outcome == "merged":
                outcome = follow(which, cr, match, clock)
            outcomes.append(outcome)
            if len(pairs) > 1:
                say(f"{cr.name if cr else ref.url}  {outcome}")
        # A verdict outranks a timeout: one change request closed while another
        # was still running at the deadline is a failure, said as one.
        overall = next((o for o in outcomes if o not in SUCCESS and o != "timeout"), None) or next(
            (o for o in outcomes if o == "timeout"), outcomes[0])
        return finish(args.target, overall, clock)
    except KeyboardInterrupt:
        return 130


def finish(label: str, verdict: str, clock: Clock, last: str = "", bad: list | None = None) -> int:
    """The one closing line, and the exit code it stands for."""
    if verdict == "timeout":
        seen = f" — last seen: {last}" if last else ""
        print(f"watch: {label} timed out after {clock.elapsed()}{seen}", flush=True)
        return TIMED_OUT
    tail = f" — {', '.join(bad)}" if bad and verdict not in SUCCESS else ""
    print(f"watch: {label} {verdict} after {clock.elapsed()}{tail}", flush=True)
    if verdict == NO_CHECKS:
        return NOTHING_TO_WAIT_FOR
    return 0 if verdict in SUCCESS else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
