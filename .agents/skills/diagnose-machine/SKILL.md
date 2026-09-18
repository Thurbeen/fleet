---
name: diagnose-machine
description: Find out what is actually consuming this machine's CPU, RAM, swap and disk, free what is safe to free, and trace the debris back to the project whose code produced it. Covers the protect list that has to exist before anything is killed, the age filter that spares in-flight work, build directories and stale sockets, and the issue that closes the loop. Use when the machine is slow, loaded or out of space, when something is eating memory or disk, when asked to free up resources or clean up old test processes, or when invoked as /diagnose-machine.
user-invocable: true
allowed-tools: Read, Bash, Glob, Grep
---

## diagnose-machine

A machine running agents accumulates debris that belongs to no running session:
orphaned multiplexer servers, agents whose parent is gone, build directories in
worktrees nobody is using, socket files pointing at nothing. The machine is
slow, and the reason is never in the place you are working.

**This produces two things, and the second is the point:** the resources freed,
and *which project's code produced the debris*. A machine swept clean by hand
fills up again next week. In the sweep this skill is written from, 400 orphaned
multiplexer servers, 1432 processes, 4.4 GiB of RSS and around 6 GiB of swap
came back — and the real product of the pass was thurbox issue #1175, the test
harnesses that made them. Free the resources, then go find the bug.

**It is not a monitoring dashboard and it does not run forever.** One pass:
measure, identify, protect, free, trace, re-measure. Run it again when the
machine is slow again.

**The recipe below is the POSIX one**, which is where a fleet's debris
accumulates. On native Windows the shape holds and every command changes: read
`Get-Process`, `Get-CimInstance Win32_LogicalDisk` and `Get-PSDrive` in place of
`ps`, `df` and `du`, and §4's rule — build the protect list before the kill list
— is the part that does not change.

## 1. Give it its own session

The sweep reads hundreds of process rows and megabytes of `du` output. That
does not belong in the lead's context, and a lead that has to stay responsive
should not be the thing running `pkill`.

```bash
thurbox-cli session create --name 'Diagnose this machine and free what is dead' \
  --repo-path <the repo whose leak you suspect, or the control plane> \
  --on-existing adopt --parent <the lead's uuid> --json
```

`adopt` rather than `fail`: this is a recurring chore and one long-lived
session for it is correct. **Read `created` before you send anything** — on
`false` the session was already there and may be mid-sweep, so read its state
instead of typing over it. `.agents/skills/thurbox-session/` owns all of that,
and §1b in particular: a session spawned by hand asks its agent's trust
question, and `uv run fleet session-trust <uuid>` is what answers it. Sending a
prompt into that dialog types the prompt into the dialog.

No worktree and no branch: the sweep changes no code, and a worktree would be
one more directory to account for in §7.

Then send it one line pointing at this file. **Do not paste the recipe into the
prompt** — it goes stale the moment this file changes.

**A queue task is not the shape for this.** The queue is for work that ends in
an artifact on a branch; this ends in a machine that is no longer full, and the
one artifact it may produce is an issue on somebody else's repo.

## 2. Baseline — measure before you touch anything

Every number here is quoted in the final report, so take them first.

```bash
echo "=== LOAD ==="; uptime
echo "=== CPU ==="; nproc; lscpu | grep -E "Model name|^CPU\(s\)|Thread|Core"
echo "=== MEM ==="; free -h
echo "=== DISK ==="; df -hT -x tmpfs -x devtmpfs -x squashfs
```

Read the load average **against `nproc`**. A load of 17.7 means nothing until
you know the machine has 4 cores, and then it means everything.

Swap is the signal people miss. A machine with free RAM and 6 GiB of swap in
use has already been full; it is telling you about a peak you did not see.

## 3. Top consumers, by both measures

```bash
ps -eo pid,ppid,user,%cpu,%mem,etimes,rss,stat,comm --sort=-%cpu | head -25
ps -eo pid,ppid,user,%cpu,%mem,etimes,rss,stat,comm --sort=-%mem | head -25
```

`etimes` — seconds alive — is doing the heavy lifting. A process at 168% CPU is
interesting; a process at 168% CPU *for 23 hours* is a runaway, and the second
fact is the one that decides.

For anything that looks wrong, ask the kernel rather than guessing:

```bash
tr '\0' ' ' < /proc/<pid>/cmdline; echo
readlink /proc/<pid>/cwd
ps -o pid,ppid,lstart,etimes,%cpu,args -p <pid> | cat
```

The `cwd` is usually what names the project, and a runaway whose `cwd` is a
deleted worktree is an orphan by definition — the directory it was working in
does not exist any more.

## 4. The protect list — build it BEFORE the kill list

**This is the section that keeps the skill safe, and it comes first for that
reason.** Everything after it is a set subtraction.

Four things are protected, and the fourth is the one people forget:

| Protect | How to find it | Why |
|---|---|---|
| Live thurbox sessions | `thurbox-cli session list --json` — every row's server | these are the operator's actual work |
| The queue's workers | `uv run fleet queue list` — anything not terminal | killing one loses a dispatched task mid-flight |
| In-flight test runners | anything younger than an hour | a suite that takes 40 minutes looks exactly like an orphan at minute 39 |
| **Your own ancestor chain** | walk `PPid` in `/proc/$$/status` up to init | you are running inside a session on this machine; kill your own ancestor and the sweep dies half-done, having signalled an unknown subset |

Resolve who owns a socket by asking the socket, not by reading its name:

```bash
for f in /tmp/tmux-$(id -u)/*; do
  [ -S "$f" ] || continue
  o=$(timeout 3 tmux -S "$f" display-message -p '#{pid}' 2>/dev/null)
  [ -n "$o" ] && echo "$f $o"
done
```

A socket that answers has a live server behind it, and a client that may be
attached. A socket that does not answer is debris — §8.

Write every protected pid, one per line, into `protect.txt`.

## 5. The kill set is a subtraction, never a judgement

```bash
ps -eo pid=,etimes=,comm= | awk '$3=="tmux:" && $2>=3600 {print $1}' | sort -u > old.txt
sort -u protect.txt > keep.txt
comm -23 old.txt keep.txt > kill.txt
```

Three properties worth keeping:

- **Age-gated.** `>=3600` spares everything young, and in-flight work is young.
- **Explicitly subtractive.** `comm -23` cannot include something on the
  protect list, whatever the age filter does.
- **Auditable.** `kill.txt` is a file you can read, count and spot-check before
  anything is sent. Read the first twenty rows and confirm they are what you
  think they are.

Then assert the one thing a wrong list costs you the sweep for:

```bash
# $MINE is your own session's server pid, walked up from $$ in §4
grep -cx "$MINE" kill.txt    # must print 0
```

If it prints anything else the list is wrong. Stop and rebuild it; do not
delete the line and carry on.

**Never pipe a `ps | grep` straight into `kill`.** The pattern that matches the
servers also matches the `grep`, the shell, and — on a bad day — the session
you are typing in.

## 6. Signal, and escalate only if you must

```bash
kill -TERM $(cat kill.txt) 2>/dev/null
sleep 2
# then re-check, and only for survivors:
kill -KILL <survivors>
```

SIGTERM first, always. A multiplexer server that gets SIGTERM tears its panes
down; one that gets SIGKILL leaves its children reparented to init, which is
how 400 orphaned servers become 1400 orphaned processes.

Send in batches of a few dozen rather than one enormous argument list, and
count after each: `ps -eo comm= | grep -c '^tmux:'` should fall by roughly what
you sent. If it does not, stop — something is respawning them, and that is a
different bug worth more than the cleanup.

Re-verify the protected pids afterwards. Every one of them.

## 7. Disk — and the check that goes before every deletion

Work down by level rather than running one `du` over everything:

```bash
du -xhd1 "$HOME" | sort -rh | head -15
du -xhd2 "$HOME/.local" | sort -rh | head -20
```

On a machine running agents the answer is nearly always **build directories in
worktrees nobody is using**. Four stale Rust `target/` trees ran to tens of
gigabytes in the sweep this is written from.

**Before removing any build directory, prove the worktree around it holds
nothing:**

```bash
git -C "$w" status --porcelain | head              # must be empty
git -C "$w" log --oneline @{u}..                   # must be empty
git branch -r --contains "$(git -C "$w" rev-parse HEAD)"   # must name a remote branch
```

**Read the second one's exit code, not just its output.** A branch with no
upstream fails it outright and prints nothing on stdout, which is
indistinguishable from "nothing unpushed" if you only look at the output — and
it is the opposite answer: no upstream means the branch was never pushed at all.

A `target/` is always safe to delete — it rebuilds. **The worktree around it is
not**, and the third check is the one that matters after a squash merge: the
branch is gone from the remote, but the commit's content is in `main`. A head
on no remote, reachable from no remote branch, is unpushed work. Leave it, and
say so in the report.

Delete the build output; keep the worktree. Removing a worktree is the
operator's call, and `git worktree remove` without `--force` refuses a dirty
one for the same reason.

## 8. Stale sockets

A multiplexer never unlinks its socket, so a correctly-killed server still
leaves the file behind. The ones that answer nothing:

```bash
cd /tmp/tmux-$(id -u) && for f in *; do
  [ -S "$f" ] || continue     # an unmatched glob is a literal `*`, not a socket
  timeout 3 tmux -S "$f" display-message -p '#{pid}' >/dev/null 2>&1 || echo "$f"
done > stale.txt
```

Re-derive this list **after** the kills, never before: a socket that was live
when you started is dead now, and a list taken before is not evidence about
now. Then remove exactly that list, by name, with `xargs -r`.

## 9. Trace it back — this is the deliverable

You have now freed the machine and learned nothing durable. The debris came
from somewhere, and that somewhere is code in a project.

Ask what the orphans had in common: their socket names, their `cwd`, their
command lines, their ages. Names group them.

- A name derived from a hash, one per run, means something derives a socket
  name from a path that changes every run — nothing will ever reap those.
- One fixed name in many copies means a cleanup that exists but is not reached
  on every path: a panic, a timeout, an interrupt.

Then go and read that code. `grep` the repo for the socket names you collected;
the harness that spells them is the one that leaks. Check the project's open
issues before filing, and whether a merged change request already fixed half of
it — half was already fixed in the #1175 case, and the issue was far more
useful for saying which half.

File it against the project that owns the code, with §2's measurements, the
mechanism and a suggested fix. Whatever standing writing rules your agent loads
govern the wording. **The fix itself is a queue task**
(`.agents/skills/fleet-queue/`), not this session's job.

## 10. Report the delta

Re-run §2 verbatim and put the two sets of numbers side by side: load, free
memory, swap, disk, and the count of whatever you reaped. A sweep that cannot
show a delta did not need to run.

Then say, in one line each: what was freed, what was deliberately left alone
and why, and the issue you filed.

## The one-pass checklist

1. Baseline: load against core count, memory, swap, disk.
2. Top consumers by CPU and by memory; `etimes` decides what is a runaway.
3. **Protect list first** — live sessions, queue workers, anything under an
   hour old, and your own ancestor chain.
4. Kill set = old − protected, via `comm -23`, into a file you read; then
   assert your own pid is not in it.
5. SIGTERM in batches, count after each, escalate only for survivors.
6. Disk by level; `git status`, unpushed, on-a-remote before any deletion.
7. Stale sockets, re-derived after the kills.
8. Trace the debris to the code that made it, and file the issue.
9. Re-measure and report the delta.
