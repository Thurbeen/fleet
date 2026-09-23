---
name: diagnose-machine
description: Find out what is actually consuming this machine's CPU, RAM, swap and disk, free what is safe to free, and trace the debris back to the project whose code produced it. Covers the protect list that has to exist before anything is killed, the age filter that spares in-flight work, build directories and stale sockets, and the issue that closes the loop. Use when the machine is slow, loaded or out of space, when something is eating memory or disk, when asked to free up resources or clean up old test processes, or when invoked as /diagnose-machine.
user-invocable: true
allowed-tools: Read, Bash, Glob, Grep
---

## diagnose-machine

A machine running agents accumulates debris that belongs to no running
session: orphaned multiplexer servers, agents whose parent is gone, build
directories in worktrees nobody uses, sockets pointing at nothing. **This
produces two things, and the second is the point:** the resources freed, and
*which project's code produced the debris*. A machine swept clean by hand
fills up again next week. The sweep this skill is written from freed 400
orphaned multiplexer servers, 1432 processes, 4.4 GiB of RSS and around 6 GiB
of swap — and its real product was thurbox issue #1175, the test harnesses
that made them.

One pass, not a dashboard: measure, identify, protect, free, trace,
re-measure. The recipe is the POSIX one, where a fleet's debris accumulates;
on native Windows the shape holds with `Get-Process`, `Get-CimInstance
Win32_LogicalDisk` and `Get-PSDrive` in place of `ps`, `df` and `du`, and §4's
rule — the protect list before the kill list — is the part that does not
change.

## The one-pass checklist

1. Baseline: load against core count, memory, swap, disk (§2).
2. Top consumers by CPU and by memory; `etimes` decides what is a runaway (§3).
3. **Protect list first** — live sessions, queue workers, anything under an
   hour old, your own ancestor chain (§4).
4. Kill set = old − protected, via `comm -23`, into a file you read; assert
   your own pid is not in it (§5).
5. SIGTERM in batches, count after each, escalate only for survivors (§6).
6. Disk by level; `git status`, unpushed, on-a-remote before any deletion (§7).
7. Stale sockets, re-derived after the kills (§8).
8. Trace the debris to the code that made it, and file the issue (§9).
9. Re-measure and report the delta (§10).

## 1. Give it its own session

The sweep reads hundreds of process rows and megabytes of `du` output, which
does not belong in the lead's context, and a lead that has to stay responsive
should not be running `pkill`.

```bash
thurbox-cli session create \
  --name "$(uv run fleet session-name diagnose 'Diagnose this machine and free what is dead')" \
  --repo-path <the repo whose leak you suspect, or the control plane> \
  --on-existing adopt --parent <the lead's uuid> --json
```

**The name is rendered, never typed**: which mark a session wears is a
setting, and `uv run fleet session-name --help` owns why and what it refuses
(a title thurbox would reject, or one the 64-byte cap would cut — take the
`Try this title:` line it offers). Run the substitution from the control-plane
checkout. `$(...)` swallows its exit code, so a `--name ''` refusal from
thurbox means read the stderr above it.

`adopt`, not `fail`: this is a recurring chore and one long-lived session is
correct. **`adopt` matches on the NAME**, so a sweep session running under
another name — created before the mark existed, or under another glyph
setting — is not the one it finds, and the spawn makes a second sweep, both
willing to run `pkill`. Rename the old one BEFORE you spawn:

```bash
thurbox-cli session rename '<its current name>' \
  "$(uv run fleet session-name diagnose 'Diagnose this machine and free what is dead')"
```

Rename refuses a name another active session holds, so once a second sweep
exists, delete the new empty one (`session delete --force <uuid>`) and then
rename. `--on-existing replace` matches the same name `adopt` does and is not
the way out. **Read `created` before you send anything**; on `false` the
session may be mid-sweep. A hand-spawned session asks its agent's trust
question, which `uv run fleet session-trust <uuid>` answers
(`thurbox-session` §1b).

No worktree and no branch: the sweep changes no code. Send one line pointing
at this file — do not paste the recipe, it goes stale. **A queue task is not
the shape for this**: the queue is for work that ends in an artifact on a
branch; this ends in a machine that is no longer full.

## 2. Baseline — measure before you touch anything

Every number here is quoted in the final report, so take them first.

```bash
echo "=== LOAD ==="; uptime
echo "=== CPU ==="; nproc; lscpu | grep -E "Model name|^CPU\(s\)|Thread|Core"
echo "=== MEM ==="; free -h
echo "=== DISK ==="; df -hT -x tmpfs -x devtmpfs -x squashfs
```

Read the load average **against `nproc`**: 17.7 means nothing until you know
the machine has 4 cores. Swap is the signal people miss — free RAM and 6 GiB
of swap in use means the machine has already been full and is telling you
about a peak you did not see.

## 3. Top consumers, by both measures

```bash
ps -eo pid,ppid,user,%cpu,%mem,etimes,rss,stat,comm --sort=-%cpu | head -25
ps -eo pid,ppid,user,%cpu,%mem,etimes,rss,stat,comm --sort=-%mem | head -25
```

`etimes` — seconds alive — decides. A process at 168% CPU is interesting; one
at 168% CPU *for 23 hours* is a runaway. For anything that looks wrong, ask
the kernel:

```bash
tr '\0' ' ' < /proc/<pid>/cmdline; echo
readlink /proc/<pid>/cwd
ps -o pid,ppid,lstart,etimes,%cpu,args -p <pid> | cat
```

The `cwd` usually names the project, and a runaway whose `cwd` is a deleted
worktree is an orphan by definition.

## 4. The protect list — build it BEFORE the kill list

**This is the section that keeps the skill safe.** Everything after it is a
set subtraction.

| Protect | How to find it | Why |
|---|---|---|
| Live thurbox sessions | `thurbox-cli session list --json` — every row's server | the operator's actual work |
| The queue's workers | `uv run fleet queue list` — anything not terminal | killing one loses a dispatched task mid-flight |
| In-flight test runners | anything younger than an hour | a suite that takes 40 minutes looks exactly like an orphan at minute 39 |
| **Your own ancestor chain** | walk `PPid` in `/proc/$$/status` up to init | you are inside a session on this machine; kill your ancestor and the sweep dies half-done |

Resolve who owns a socket by asking the socket, not by reading its name:

```bash
for f in /tmp/tmux-$(id -u)/*; do
  [ -S "$f" ] || continue
  o=$(timeout 3 tmux -S "$f" display-message -p '#{pid}' 2>/dev/null)
  [ -n "$o" ] && echo "$f $o"
done
```

A socket that answers has a live server behind it; one that does not is
debris (§8). Write every protected pid, one per line, into `protect.txt`.

## 5. The kill set is a subtraction, never a judgement

```bash
ps -eo pid=,etimes=,comm= | awk '$3=="tmux:" && $2>=3600 {print $1}' | sort -u > old.txt
sort -u protect.txt > keep.txt
comm -23 old.txt keep.txt > kill.txt
```

Age-gated (`>=3600` spares in-flight work), explicitly subtractive (`comm -23`
cannot include something on the protect list), and auditable (`kill.txt` is a
file you read and spot-check before anything is sent). Then assert the one
thing a wrong list costs you the sweep for:

```bash
# $MINE is your own session's server pid, walked up from $$ in §4
grep -cx "$MINE" kill.txt    # must print 0
```

Anything else means the list is wrong: stop and rebuild it. **Never pipe a
`ps | grep` straight into `kill`** — the pattern that matches the servers also
matches the `grep`, the shell, and the session you are typing in.

## 6. Signal, and escalate only if you must

```bash
kill -TERM $(cat kill.txt) 2>/dev/null
sleep 2
# then re-check, and only for survivors:
kill -KILL <survivors>
```

SIGTERM first, always: a multiplexer server that gets SIGTERM tears its panes
down, and one that gets SIGKILL leaves its children reparented to init, which
is how 400 orphaned servers become 1400 orphaned processes. Send in batches
of a few dozen and count after each (`ps -eo comm= | grep -c '^tmux:'`); if
the count does not fall, something is respawning them, and that is a
different bug worth more than the cleanup. Re-verify every protected pid
afterwards.

## 7. Disk — and the check that goes before every deletion

```bash
du -xhd1 "$HOME" | sort -rh | head -15
du -xhd2 "$HOME/.local" | sort -rh | head -20
```

On a machine running agents the answer is nearly always **build directories
in worktrees nobody uses**: four stale Rust `target/` trees ran to tens of
gigabytes in the sweep this is written from. Before removing any build
directory, prove the worktree around it holds nothing:

```bash
git -C "$w" status --porcelain | head              # must be empty
git -C "$w" log --oneline @{u}..                   # must be empty
git branch -r --contains "$(git -C "$w" rev-parse HEAD)"   # must name a remote branch
```

**Read the second one's exit code, not just its output**: a branch with no
upstream fails outright and prints nothing, which looks like "nothing
unpushed" and is the opposite answer. The third is the one that matters after
a squash merge: the branch is gone from the remote, but the content is in
`main`. A head reachable from no remote branch is unpushed work — leave it and
say so.

Delete the build output; keep the worktree. Removing a worktree is the
operator's call, and `git worktree remove` without `--force` refuses a dirty
one for the same reason.

## 8. Stale sockets

A multiplexer never unlinks its socket, so a correctly killed server still
leaves the file behind:

```bash
cd /tmp/tmux-$(id -u) && for f in *; do
  [ -S "$f" ] || continue     # an unmatched glob is a literal `*`, not a socket
  timeout 3 tmux -S "$f" display-message -p '#{pid}' >/dev/null 2>&1 || echo "$f"
done > stale.txt
```

Re-derive this list **after** the kills: a socket that was live when you
started is dead now. Then remove exactly that list, by name, with `xargs -r`.

## 9. Trace it back — this is the deliverable

You have freed the machine and learned nothing durable. Ask what the orphans
had in common — socket names, `cwd`, command lines, ages. A name derived from
a hash, one per run, means something derives a socket name from a path that
changes every run, and nothing will ever reap those. One fixed name in many
copies means a cleanup that exists but is not reached on every path: a panic,
a timeout, an interrupt.

Then read that code: `grep` the repo for the socket names you collected; the
harness that spells them is the one that leaks. Check the project's open
issues first, and whether a merged change already fixed half of it — half was
already fixed in the #1175 case, and the issue was more useful for saying
which half. File it against the project that owns the code, with §2's
measurements, the mechanism and a suggested fix; whatever standing writing
rules your agent loads govern the wording. **The fix itself is a queue task**
(`.agents/skills/fleet-queue/`), not this session's job.

## 10. Report the delta

Re-run §2 verbatim and put the two sets of numbers side by side: load, free
memory, swap, disk, and the count of whatever you reaped. A sweep that cannot
show a delta did not need to run. Then, one line each: what was freed, what
was deliberately left alone and why, and the issue you filed.
