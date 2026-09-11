#!/usr/bin/env bash
# Prove the queue's ordering doctrine and its wake model, rather than assert them.
#
# `scripts/queue.sh` makes claims that are easy to write down and easy to get
# backwards. Each one gets a test here, against a throwaway queue in a temp
# directory (`FLEET_QUEUE_DIR`), so a change that quietly inverts one fails the
# gate instead of a run six weeks later:
#
#   1. Independent work dispatches AT ONCE, with no concurrency cap. One
#      `plan` names every ready task and one `dispatch` launches all of them.
#   2. File overlap does NOT serialize. Two ready tasks touching one file are
#      still both ready; the overlap is reported as a risk note beside them.
#   3. A recorded blocker DOES serialize, and clears only when the task it
#      names is genuinely done — not when that task's session merely stopped.
#   4. A turn ending is not a task finishing. `watch` folds transitions into
#      the record and never closes a task; only `collect`, reading a result
#      the worker wrote, does that.
#   5. A blocker with no category and no reason is refused, so "these touch the
#      same file" cannot be spelled as a dependency.
#   6. A task whose BRIEF.md was never written does not go out.
#   7. The queue belongs to a CHECKOUT, not to the shell's cwd — and a checkout
#      that is not the control plane cannot silently open a second one.
#   8. `collect` VERIFIES the artifact rather than trusting the worker's word
#      for it, and "could not check" is a third answer that is neither pass
#      nor fail. The brief scaffold points at the standing policy rather than
#      restating it, and at the operator's own OPERATOR.md when there is one —
#      and at no such file when there is not.
#   9. A session is released when its work LANDS, and never before: a merged
#      pull request is reapable, an open one is not, a session thurbox says is
#      working is never touched whatever the record claims, and a task the
#      worker gave up in keeps its session because that session is the
#      evidence. A blocker clears on the merge, not on the conclusion.
#  10. EVERY open pull request on the repo is shepherded, whether or not a task
#      records it: one that goes bad gets a FIXER, and one that clears every
#      merge gate gets merged. A stranger's is neither. Everything the shepherd
#      cannot read is left exactly as it is. What it saw is WRITTEN DOWN on the
#      task, and the gate is method-aware: a pull request the forge is happy
#      with but nobody attested is recorded `green`, handed back and never
#      merged, while a `no-mistakes` task with no attestation is recorded
#      `unattested` and still gets its fixer.
#  11. A task can name a HOST and run there, and a task that names none takes
#      exactly the path it took before the flag existed. Nothing is spawned on
#      a host until three probes pass; the brief really lands on that host's
#      filesystem and the result really comes back off it; and a session whose
#      host cannot be reached is kept, not reaped — thurbox's CLI still calls
#      it `idle`, which is the trap.
#  12. `refuel` asks the ACCOUNT's quota window first and restarts nothing
#      while it is spent — that window is the operator's subscription, shared
#      by the lead and every worker, so a restart there burns the reset it was
#      waiting for. With fuel in the account, a session wedged on its agent's
#      own limit banner is restarted through dispatch's own handoff, capped and
#      recorded; a merely slow one is not, nor is one whose stale state predates
#      the restart it already got; the lead is refused; and a quota that cannot
#      be read is undetermined, which acts on nothing.
#  13. The status output never contradicts itself. A terminal state shows no
#      blocker, a blocker whose upstream can never land says so with that
#      upstream's state, a state and an outcome that disagree are printed as a
#      disagreement, a sweep that could not read every repo says so in its
#      headline, and a queued task nobody dispatched is marked as one.
#  14. A topic whose every task reached `landed` or `abandoned` ARCHIVES
#      itself, and leaves every default view while staying reachable by name.
#      One task still running, or one a worker gave up in, keeps the whole
#      topic in front of the operator — and all four readers answer the same,
#      out of the topic file alone.
#  15. NO TRANSITION IS LOST. Every event thurbox reports for a task's session
#      reaches that task's progress.jsonl — whether another task's events came
#      through the same batch, whether the task was dispatched while the watch
#      was already streaming, whether nothing was watching at the time, and
#      whether the run that read them died part-way. Exactly once each.
#  16. The tool leaves the lead no reason to work around it, and it refuses at
#      `add` what only `dispatch` used to discover. A `--brief-file` that
#      carries the four standard headings fills the four standard sections,
#      and one that leaves any of them unwritten is refused THERE, naming
#      them, with nothing created; the check is structural, so a brief quoting
#      the scaffold's placeholder to talk about it still dispatches; a branch
#      no worktree could be cut for is refused before a task exists;
#      `dispatch` takes refs, so holding a task back needs no invented
#      blocker, and a bare `dispatch` still sends everything and still names
#      the sections of a brief nobody wrote; and `block --kind` lists its four
#      values in `--help` instead of only in the refusal.
#  17. ONE setting puts fleet's mark on the lead and on every worker, and takes
#      it back off both — rendered into the name thurbox is actually asked to
#      create, and cut to thurbox's byte cap on a codepoint boundary.
#  18. A MESSAGE THE LEAD SENT IS COMPARABLE AGAINST WHAT MOVED AFTER IT.
#      `send` records the instant and a baseline of the branch head; `list` and
#      `show` report a commit or a transition dated after it as movement, a
#      silence as a silence and never as a verdict about the worker, a git this
#      machine cannot read as `not checked`, and a task nobody messaged as
#      nothing at all. Nothing there writes `state` or `outcome`.
#  19. The run log is something the queue PRODUCES. Opening a topic opens
#      one; the loop's own commands refresh the facts inside a fenced block
#      and rewrite it rather than appending to it; and prose the lead wrote
#      outside that block survives every later pass.
#  20. A TASK HELD BY SOMETHING OUTSIDE THE QUEUE HAS A STATE IT CAN BE
#      RECORDED IN. A blocker may name a CONDITION instead of a task — a
#      credential, an approval, a window, a machine somebody has to fix — with
#      its own closed set of kinds and the same required reason. It puts the
#      task in `waiting` and never in `ready`, it reaches `list`, `show` and
#      `fleet-status.sh`, and the line the reconciler types into the lead's
#      terminal does not count it. Nothing clears one but `block --clear`
#      naming it back: not `collect`, not `reap`, not another task landing.
#      The task-to-task form keeps its `landed` gate and its `UNCLEARABLE`,
#      asserted in that same queue because the two share the code.
#
#  20. A TITLE IS A SESSION NAME, so `add` refuses one thurbox could not spawn
#      — judged on the RENDERED name, mirroring thurbox's own rule and
#      widening it by nothing — and a spawn that fails anyway reports what
#      thurbox said rather than its exit status.
#
# Test 4 is also the wake proof. The event source is `thurbox-cli watch`, which
# this script replaces with a recorded stream through `FLEET_QUEUE_WATCH_CMD` —
# the same override a different transport would use. What matters is the shape:
# the lead READS a stream when it chooses and READS a file the worker wrote.
# Nothing is delivered into its terminal, which is what `message send` does and
# why the queue does not use it.
#
# Test 7 is the silent-fork proof, and it runs against a THROWAWAY CLONE built
# in a temp directory rather than against this one, so it answers the same on a
# machine with thurbox installed and on CI without it.

# Test 8 is the enforcement proof, and test 9 the release proof. `gh`,
# `thurbox-cli`, `ssh` and `quota-axi` are all stubbed on PATH for the whole
# run (see the stubs below), so every collect and every reap here answers the same offline,
# on CI, on an operator's laptop and on a machine with no network — and so that
# a run of this file can never delete a real session or touch a real host. Test
# 11 needs that last part most: the machine this was written on has hosts
# configured in a real hosts.toml, and a selftest that reached one would be
# acting on someone's else's machine.
#
# Usage: scripts/queue-selftest.sh        (also: ./scripts/check.sh queue)
#
# Requires: python3 (with PyYAML), plus git and jq for test 9 — `gh` and
# `thurbox-cli` are stubbed on PATH there, but session-trust.sh, which test 9
# drives for real, reads their output with jq.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

# Every repo the tests below build is a throwaway in $tmp, and the operator's
# own commit signing must not reach it: `commit.gpgsign = true` with the key
# scoped by an `includeIf gitdir:` block makes every commit here fail with
# `either user.signingkey or gpg.ssh.defaultKeyCommand needs to be configured`
# and surfaces as a dozen unrelated-looking queue failures. The
# GIT_CONFIG_COUNT triple outranks every config file, including a
# GIT_CONFIG_GLOBAL the caller set, so this settles it for the whole run.
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=commit.gpgsign
export GIT_CONFIG_VALUE_0=false

QUEUE="./scripts/queue.sh"
nl=$'\n'
failed=0
tmp=""

# Inline rather than a function: shellcheck cannot see that a trap invokes one.
trap '[ -n "$tmp" ] && rm -rf "$tmp"' EXIT

pass() { printf '  \033[32mok\033[0m    %s\n' "$1"; }

fail() {
	printf '  \033[31mFAIL\033[0m  %s\n' "$1" >&2
	if [ $# -gt 1 ]; then printf '%s\n' "$2" | sed 's/^/          /' >&2; fi
	failed=1
}

# Assert a command's combined output contains (or does not contain) a string.
# The whole output goes into the failure message, because a queue report is
# short and the interesting part is always the bit that is missing.
expect() {
	local label="$1" want="$2" out="$3"
	if printf '%s' "$out" | grep -qF -- "$want"; then
		pass "$label"
	else
		fail "$label" "expected to find: $want${nl}--- got ---${nl}$out"
	fi
}

# A brief with its wrapping squeezed out, so an assertion can name a phrase
# without having to know where the scaffold's own line-breaking put it.
brief_text() { tr -s '[:space:]' ' ' <"$1"; }

refute() {
	local label="$1" unwanted="$2" out="$3"
	if printf '%s' "$out" | grep -qF -- "$unwanted"; then
		fail "$label" "expected NOT to find: $unwanted${nl}--- got ---${nl}$out"
	else
		pass "$label"
	fi
}

for tool in python3 git jq; do
	command -v "$tool" >/dev/null || {
		echo "error: $tool not found" >&2
		exit 2
	}
done

tmp="$(mktemp -d)"
export FLEET_QUEUE_DIR="$tmp/queue"
# Run logs go to a throwaway directory too (test 12). Without this, every run
# of this file would scaffold logs into the operator's own orchestration/runs/.
export FLEET_RUNS_DIR="$tmp/runs"

# --- `glab`, a STAND-IN on PATH for the whole run ----------------------------
#
# The GitLab adapter asks `glab auth status` which instances this machine
# holds, and it asks the moment the forge registry is built — which is to say
# in nearly every section below, whether or not that section is about GitLab.
# On the operator's own laptop the REAL `glab` would answer, so the verdicts
# below would depend on who ran the file and on a network being there. This
# stub is a hermetic stand-in for a machine with no GitLab configuration at
# all, which is exactly the machine every section except 14 is written for.
#
# It is NOT a tripwire, and cannot be one: unlike `gh`, `glab` is now something
# the adapter legitimately invokes in every section. It goes in front of
# `base_path`, so every section that builds its own PATH out of it inherits it
# — section 13 included, where reaching a real `glab` would be the same defect
# as reaching a real `gh`. Section 14 puts a `glab` of its own in front of it.
noglab="$tmp/no-glab"
mkdir -p "$noglab"
cat >"$noglab/glab" <<'SH'
#!/bin/sh
echo "glab: no GitLab instance is configured on this machine" >&2
exit 1
SH
chmod +x "$noglab/glab"
export PATH="$noglab:$PATH"

# Captured here, before test 7's subshell exports its own PATH: reading $PATH
# after that point is what SC2031 is about, and the stubbed sections below
# want the PATH this script started with, not whatever a subshell left.
base_path="$PATH"

echo "queue-selftest: $FLEET_QUEUE_DIR"

# --- `gh`, stubbed on PATH for the whole run ---------------------------------
#
# `collect` asks the forge about each artifact — the body, the head commit, the
# head branch and the state, all in one `gh pr view` (test 8). Every collect
# below therefore has to answer offline and identically on CI, so the real `gh`
# is replaced here rather than in test 8 alone: this stub serves one pull
# request per number and fails, the way an unreachable API fails, for a number
# it has no file for.

bodies="$tmp/pr-bodies"
states="$tmp/pr-states"
heads="$tmp/pr-heads"
commits="$tmp/pr-commits"
ghbin="$tmp/gh-bin"
mkdir -p "$bodies" "$states" "$heads" "$commits" "$ghbin"

# What `--json body,headRefOid,headRefName,state,commits` answers with,
# assembled from the fixture files a test wrote for that number. A pull request
# with no commits file gets an EMPTY list, which is what the real API gives for
# one `gh` could not enumerate — and which no message may read anything into.
cat >"$tmp/pr-json.py" <<'PY'
import json
import sys

n, root = sys.argv[1], sys.argv[2]


def read(path):
    try:
        return open(path).read()
    except OSError:
        return ""


# One `<oid> <headline>` per line, oldest first, the way `gh` orders them.
commits = [
    {"oid": line.split(" ", 1)[0], "messageHeadline": line.split(" ", 1)[1]}
    for line in read(f"{root}/pr-commits/{n}.txt").splitlines()
    if " " in line
]

print(json.dumps({
    "body": read(f"{root}/pr-bodies/{n}.md"),
    "state": read(f"{root}/pr-states/{n}.state").strip() or "OPEN",
    "headRefName": read(f"{root}/pr-heads/{n}.branch").strip(),
    "headRefOid": read(f"{root}/pr-heads/{n}.sha").strip(),
    "commits": commits,
}))
PY

cat >"$ghbin/gh" <<SH
#!/bin/sh
# Stands in for \`gh pr view <url> --json body,headRefOid,headRefName,state,commits\`
# (collect's publish check) and \`gh pr view <url> --json state\` (reap's
# landing check). A pull request with no body file is one the API cannot be
# reached for; one with no state file is OPEN, which is what a pull request is
# until something changes it.
#
# \`pr list\` is the third call, and it belongs to fleet-status.sh rather than to
# the queue: a repo it CAN read and that has nothing open is what makes an
# incomplete sweep distinguishable from an empty one.
[ "\$1 \$2" = "pr list" ] && { echo "[]"; exit 0; }
prev=""
for a in "\$@"; do
	case "\$a" in http*) url="\$a" ;; esac
	[ "\$prev" = --json ] && want="\$a"
	prev="\$a"
done
n="\${url##*/}"
if [ ! -f "$bodies/\$n.md" ]; then
	echo "could not resolve host: api.github.com" >&2
	exit 1
fi
case "\${want:-body}" in
*headRef*) python3 "$tmp/pr-json.py" "\$n" "$tmp" ;;
*state*) cat "$states/\$n.state" 2>/dev/null || echo OPEN ;;
*) cat "$bodies/\$n.md" ;;
esac
SH
chmod +x "$ghbin/gh"

# The body a `no-mistakes` pull request carries: an attestation naming the
# commit the pipeline ran on. It is written DURING the `pr` step, so `pr` reads
# `running` and `ci` `pending` in every real one.
cat >"$tmp/attest.py" <<'PY'
import json
import sys

steps = [
    {"step": s, "status": "completed"}
    for s in ("intent", "rebase", "review", "test", "document", "lint", "push")
] + [{"step": "pr", "status": "running"}, {"step": "ci", "status": "pending"}]
payload = json.dumps({"head_sha": sys.argv[1], "steps": steps})
print(f"<!-- no-mistakes-pipeline-attestation:v1 {payload} -->")
print()
print("Shipped it.")
PY

# A pull request the pipeline opened: from `branch`, attested for its own head.
# The optional third argument attests some OTHER commit, which is the stale
# case — a verdict about code that is no longer what would merge.
pipeline_pr() {
	local sha
	sha="$(printf '%040d' "$1")"
	printf '%s' "$2" >"$heads/$1.branch"
	printf '%s' "$sha" >"$heads/$1.sha"
	python3 "$tmp/attest.py" "${3:-$sha}" >"$bodies/$1.md"
}

# The commits `gh` would list for a pull request, oldest first, one
# `<oid> <headline>` per line. Only a test that cares WHO moved the head past
# the attestation writes one; every other pull request here is enumerated as
# nothing, which is the answer that must never be read as evidence.
pr_history() {
	local n="$1"
	shift
	printf '%s\n' "$@" >"$commits/$n.txt"
}

# One opened by any other means: a branch, a body, and no attestation at all.
plain_pr() {
	printf '%s' "$2" >"$heads/$1.branch"
	printf '%040d' "$1" >"$heads/$1.sha"
	printf '%s\n' "${3:-Opened by hand.}" >"$bodies/$1.md"
}

# --- `thurbox-cli`, stubbed on PATH for the whole run ------------------------
#
# `reap` asks thurbox what a session is doing before it deletes anything, and
# deletes with `session delete <id> --force`. Both are stubbed here: the states
# come from one file per session id, and every delete is APPENDED TO A LOG
# rather than performed, so a test can assert on exactly what would have been
# killed — including that nothing was.

sessions="$tmp/sessions"
deletions="$tmp/deletions.log"
panes="$tmp/panes"
restarts="$tmp/restarts.log"
sends="$tmp/sends.log"
tbxbin="$tmp/tbx-bin"
mkdir -p "$sessions" "$tbxbin" "$panes"
: >"$deletions"
: >"$restarts"
: >"$sends"

cat >"$tbxbin/thurbox-cli" <<SH
#!/bin/sh
# session list --json | session get <id> --json | session delete <id> --force
# | session create --json | session capture --json | config show --json
case "\$1 \$2" in
"config show")
	# Only ever read to LOCATE hosts.toml. Pointed at the fixture below so a
	# selftest run can never read — let alone act on — the operator's own
	# hosts.toml, whatever machine it runs on.
	printf '{"paths":{"hosts_toml":"%s"}}\n' "$tmp/hosts.toml"
	;;
"session create")
	# The id the next create hands back, so a test can name its own session
	# and then say what that session is doing.
	cat "$tmp/next-session.json" 2>/dev/null || echo '{"id":"stub","created":true}'
	;;
"session capture")
	# A pane fixture when a test wrote one — that is how refuel is shown an
	# agent's own limit banner — and otherwise an empty pane, on which
	# session-trust.sh sees no dialog and falls through to hook_reported.
	if [ -f "$panes/\$3.txt" ]; then
		python3 -c 'import json,sys; print(json.dumps({"output": open(sys.argv[1]).read()}))' \
			"$panes/\$3.txt"
	else
		echo '{"output":""}'
	fi
	;;
"session restart")
	echo "\$*" >>"$restarts"
	echo '{"id":"'"\$3"'","restarted":true}'
	;;
"session send")
	echo "\$*" >>"$sends"
	;;
"session list")
	printf '['
	sep=""
	for f in "$sessions"/*.json; do
		[ -e "\$f" ] || continue
		printf '%s%s' "\$sep" "\$(cat "\$f")"
		sep=","
	done
	printf ']\n'
	;;
"session get")
	[ -f "$sessions/\$3.json" ] || { echo "no such session: \$3" >&2; exit 1; }
	cat "$sessions/\$3.json"
	;;
"session delete")
	echo "\$*" >>"$deletions"
	;;
*) exit 0 ;;
esac
SH
chmod +x "$tbxbin/thurbox-cli"

# Record a session the stub will report, in whatever state the test needs.
# `session_is <id> <state> [hook_state_age_secs]`. The hook fields come with
# it because `refuel` reads them: a session that ran dry reads `working` with an
# age that keeps growing, and nothing else in `get --json` says so.
session_is() {
	printf '{"id":"%s","name":"%s","state":"%s","agent":"claude","hook_reported":true,' \
		"$1" "worker $1" "$2" >"$sessions/$1.json"
	printf '"hook_state":"%s","hook_state_age_secs":%s,"agent_session_id":"agent-%s"}\n' \
		"$2" "${3:-5}" "$1" >>"$sessions/$1.json"
}

# --- `ssh`, stubbed on PATH for the whole run --------------------------------
#
# A remote task's brief, result and every probe travel over ssh, and this run
# has no remote host to travel to — the machine it is written on has hosts
# configured that a test must never touch. So `ssh` is a fake host here, with a
# real filesystem under $remotes: `cat > path` writes into it and `cat path`
# reads back out, which is what makes the brief push and the result fetch
# provable rather than asserted.
#
# A host is made to fail by touching a flag file: `<dest>.down` for an
# unreachable one, `.nonposix` for a Windows-shaped shell, `.noforge` for one
# with no GitHub credentials, `.norepo` for one where the repo is not there.

remotes="$tmp/remotes"
sshstate="$tmp/ssh-state"
sshbin="$tmp/ssh-bin"
mkdir -p "$remotes" "$sshstate" "$sshbin"

cat >"$sshbin/ssh" <<SH
#!/bin/sh
# ssh [opts...] <destination> <script>. Only the last two arguments matter.
dest=""
prev=""
for a in "\$@"; do
	dest="\$prev"
	prev="\$a"
done
script="\$prev"

[ -f "$sshstate/\$dest.down" ] && {
	echo "ssh: connect to host \$dest port 22: No route to host" >&2
	exit 255
}

case "\$script" in
*fleet-posix-ok*)
	[ -f "$sshstate/\$dest.nonposix" ] && {
		echo "printf : The term 'printf' is not recognized as a cmdlet." >&2
		exit 1
	}
	printf fleet-posix-ok
	;;
*successfully?authenticated*)
	[ -f "$sshstate/\$dest.noforge" ] && { printf github.com; exit 1; }
	printf 'github.com with an ssh key'
	;;
*no-dir*)
	[ -f "$sshstate/\$dest.norepo" ] && { printf no-dir; exit 1; }
	printf ok
	;;
"cat > "*)
	p="\${script#cat > }"
	p="\$(printf %s "\$p" | sed "s/^'//; s/'\$//")"
	mkdir -p "$remotes/\$dest\$(dirname "\$p")"
	cat >"$remotes/\$dest\$p"
	;;
"cat "*)
	p="\${script#cat }"
	p="\$(printf %s "\$p" | sed "s/^'//; s/'\$//")"
	[ -f "$remotes/\$dest\$p" ] || {
		echo "cat: \$p: No such file or directory" >&2
		exit 1
	}
	cat "$remotes/\$dest\$p"
	;;
*) exit 0 ;;
esac
SH
chmod +x "$sshbin/ssh"

# The hosts thurbox is told about, in hosts.toml's own shape. `devbox` is the
# ordinary POSIX host; the other two are the shapes fleet refuses outright,
# and both refusals happen at `add` time without any host being contacted.
cat >"$tmp/hosts.toml" <<'EOF'
[[hosts]]
name = "devbox"
destination = "me@devbox"

[[hosts]]
name = "winbox"
destination = "me@winbox"
multiplexer = "psmux"

[[hosts]]
name = "lonebox"
destination = "me@lonebox"
share_sessions = false
EOF

# --- `quota-axi`, stubbed on PATH for the whole run --------------------------
#
# The ACCOUNT's quota window, which the lead and every worker share. `refuel`
# asks it FIRST and restarts nothing while it is spent, so this run must be
# able to say "spent" and "has fuel" without an account, a network or a
# credential. The shape is quota-axi's own schemaVersion 5, trimmed to the
# fields read: one file per answer, and no file at all is the tool failing the
# way an expired credential fails.

quotabin="$tmp/quota-bin"
quota="$tmp/quota.json"
mkdir -p "$quotabin"

cat >"$quotabin/quota-axi" <<SH
#!/bin/sh
[ -f "$quota" ] || { echo "quota-axi: no credentials for provider claude" >&2; exit 1; }
cat "$quota"
SH
chmod +x "$quotabin/quota-axi"

# `quota_is <percent remaining> <resets at>` for the five-hour window, which is
# the one a session runs dry against.
quota_is() {
	cat >"$quota" <<EOF
{"generatedAt": "2026-09-08T21:18:52.142Z", "schemaVersion": 5,
 "providers": [{"provider": "claude", "plan": "max",
  "windows": [
   {"id": "five_hour", "label": "session", "kind": "session",
    "resetsAt": "$2", "percentRemaining": $1},
   {"id": "seven_day", "label": "week", "kind": "weekly",
    "resetsAt": "2026-09-15T00:00:00+00:00", "percentRemaining": 75}],
  "state": {"status": "fresh", "stale": false},
  "quotaSemantics": {"status": "known", "effectiveAvailability": [
   {"scope": "all_models", "status": "known", "effectivePercentRemaining": $1,
    "boundedBy": ["five_hour", "seven_day"], "limitingWindowIds": ["five_hour"]}]}}]}
EOF
}

export PATH="$ghbin:$tbxbin:$sshbin:$quotabin:$PATH"

# A pull request that passes the publish check for the task test 3 collects, so
# that test says what it always said — that a blocker clears on a real
# conclusion — and nothing about how the work was published.
pipeline_pr 999 fix/drop-idle-default

# --- a topic, and four tasks under it ----------------------------------------
#
# One prompt, two repos, four units of work: the shape the queue exists for.
# `01` and `02` are independent. `03` genuinely depends on `01`. `04` edits the
# same file as `01` and depends on nothing.

# stdout is the VALUE and stderr is the note, so the topic id can be captured
# with `$(...)` while the run log this also opened still gets named (test 12).
if ! topic="$($QUEUE topic add report-status-honestly \
	--title 'Make thurbox report agent status honestly' \
	--prompt 'idle should mean the agent said it is at rest, nothing else' \
	2>"$tmp/topic-add.err")"; then
	fail "topic add" "$topic$nl$(cat "$tmp/topic-add.err")"
	exit 1
fi
expect "topic add returns a topic id, and only that" "report-status-honestly" "$topic"

for spec in \
	"01:drop-idle-default:Stop defaulting an unreported session to idle:/tmp/repo-a:src/state.rs" \
	"02:document-the-states:Document the state vocabulary:/tmp/repo-b:docs/states.md" \
	"03:render-detected-agent:Render detected_agent in the session list:/tmp/repo-a:src/list.rs" \
	"04:log-state-changes:Log every state change:/tmp/repo-a:src/state.rs"; do
	IFS=: read -r n slug title repo touches <<<"$spec"
	if ! out="$($QUEUE add "$topic" "$slug" --title "$title" --repo "$repo" \
		--branch "fix/$slug" --touches "$touches" --number "$n" 2>&1)"; then
		fail "add $slug" "$out"
	fi
done

# --- 5. a blocker must name a category and a reason --------------------------

if out="$($QUEUE block "$topic/03-render-detected-agent" \
	--on "$topic/01-drop-idle-default" 2>&1)"; then
	fail "a blocker with no reason is refused" "$out"
else
	expect "a blocker with no reason is refused" "--kind" "$out"
fi

if out="$($QUEUE block "$topic/04-log-state-changes" --on "$topic/01-drop-idle-default" \
	--kind file-overlap --why 'both edit src/state.rs' 2>&1)"; then
	fail "file overlap cannot be spelled as a blocker kind" "$out"
else
	expect "file overlap cannot be spelled as a blocker kind" "semantic-dependency" "$out"
	expect "and the refusal says where overlap belongs instead" "--touches" "$out"
fi

if ! out="$($QUEUE block "$topic/03-render-detected-agent" --on "$topic/01-drop-idle-default" \
	--kind semantic-dependency \
	--why 'reads the detected_agent field 01 introduces' 2>&1)"; then
	fail "record a real blocker" "$out"
fi

if out="$($QUEUE block "$topic/01-drop-idle-default" --on "$topic/03-render-detected-agent" \
	--kind semantic-dependency --why 'closes a loop' 2>&1)"; then
	fail "a blocker that closes a cycle is refused" "$out"
else
	expect "a blocker that closes a cycle is refused" "cycle" "$out"
fi

# --- 1 and 2. the first plan -------------------------------------------------

plan="$($QUEUE plan 2>&1)"
expect "ready set names 01" "01-drop-idle-default" "$plan"
expect "ready set names 02" "02-document-the-states" "$plan"
expect "ready set names 04 despite the file overlap" "04-log-state-changes" "$plan"
expect "three tasks are ready at once" "ready: 3" "$plan"
expect "01 and 04 are reported as an overlap risk" "src/state.rs" "$plan"
expect "the overlap note refuses to be a reason to wait" "not a reason to wait" "$plan"
expect "03 waits" "waiting: 1" "$plan"
expect "03's blocker is durable and stated" "reads the detected_agent field" "$plan"

ready="$($QUEUE plan --json 2>&1 |
	python3 -c 'import json,sys; print(len(json.load(sys.stdin)["ready"]))')"
if [ "$ready" = 3 ]; then
	pass "plan --json agrees: 3 ready"
else
	fail "plan --json ready count" "got $ready"
fi

# --- 6. an unwritten brief stops the dispatch --------------------------------
#
# And the brief it refuses is a SKELETON: seven hand-written briefs invented 20
# headings between them on top of the scaffold's, several of those headings
# being the same rhetorical construction the lead had just told another worker
# to strip from the repo's docs. The scaffold emits the sections, so the lead
# supplies content instead of designing a document -- and because every section
# starts unwritten, a half-written brief is refused here too, not just a blank
# one.

raw="$(cat "$FLEET_QUEUE_DIR/$topic/01-drop-idle-default/BRIEF.md")"
for heading in "## What to do" "## Hard constraints" "## Coordination" \
	"## Done means"; do
	expect "the scaffold emits the section \`$heading\`" "$heading" "$raw"
done
unwritten="$(printf '%s\n' "$raw" | grep -c 'WRITE THE INSTRUCTIONS HERE')"
if [ "$unwritten" = 4 ]; then
	pass "every scaffolded section starts unwritten, so a half-written brief is refused"
else
	fail "every scaffolded section starts unwritten" \
		"counted $unwritten placeholder(s)${nl}$raw"
fi

if out="$($QUEUE dispatch --dry-run 2>&1)"; then
	fail "a task with an unwritten BRIEF.md does not go out" "$out"
else
	expect "a task with an unwritten BRIEF.md does not go out" "BRIEF.md" "$out"
fi

for t in 01-drop-idle-default 02-document-the-states 03-render-detected-agent \
	04-log-state-changes; do
	brief="$FLEET_QUEUE_DIR/$topic/$t/BRIEF.md"
	python3 - "$brief" <<'PY'
import sys
path = sys.argv[1]
body = open(path).read().replace(
    "<!-- WRITE THE INSTRUCTIONS HERE -->",
    "Make the change, open a pull request, and write the result file below.",
)
open(path, "w").write(body)
PY
done

# --- 1. one dispatch launches all of them ------------------------------------

out="$($QUEUE dispatch --dry-run 2>&1)"
spawns="$(printf '%s\n' "$out" | grep -c 'session create')"
if [ "$spawns" = 3 ]; then
	pass "one dispatch spawns 3 sessions"
else
	fail "one dispatch spawns 3 sessions" "counted $spawns${nl}$out"
fi
refute "dispatch does not spawn the blocked task" "03-render-detected-agent" "$out"
expect "dispatch says the ready set goes out together" "no concurrency cap" "$out"
expect "each worker is pointed at its own brief and nothing else" "BRIEF.md and do what it says" "$out"
expect "every spawn answers the trust dialog before it prompts" "session-trust.sh" "$out"
expect "and the trust step comes before the prompt" \
	"trust dialog first" "$out"

# --- 4. a turn ending is not a task finishing --------------------------------

# The stream is already at seq 100 before either task attaches, so attach can
# prove it stamps each task's own floor from the high-water mark rather than
# from 0 or from "now".
events="$tmp/events.jsonl"
cat >"$events" <<'EOF'
{"seq":100,"at":1788792150000,"session":"11111111-1111-1111-1111-111111111111","event":"present","from_state":null,"to_state":"working","state":"working","reason":null}
{"seq":100,"at":1788792150000,"session":"22222222-2222-2222-2222-222222222222","event":"present","from_state":null,"to_state":"working","state":"working","reason":null}
EOF
export FLEET_QUEUE_WATCH_CMD="cat $events"

$QUEUE attach "$topic/01-drop-idle-default" 11111111-1111-1111-1111-111111111111 >/dev/null
$QUEUE attach "$topic/02-document-the-states" 22222222-2222-2222-2222-222222222222 >/dev/null

seeded="$(grep -h '^watch_from:' \
	"$FLEET_QUEUE_DIR/$topic/01-drop-idle-default/task.yaml" \
	"$FLEET_QUEUE_DIR/$topic/02-document-the-states/task.yaml" | sort -u)"
if [ "$seeded" = "watch_from: 100" ]; then
	pass "attach stamps each task's own floor from the stream's high-water mark"
else
	fail "attach stamps each task's own floor from the stream's high-water mark" \
		"got: $seeded"
fi
if [ -e "$FLEET_QUEUE_DIR/.cursor" ]; then
	fail "and writes no queue-wide cursor for another task to consume" \
		"$FLEET_QUEUE_DIR/.cursor exists"
else
	pass "and writes no queue-wide cursor for another task to consume"
fi

cat >>"$events" <<'EOF'
{"seq":101,"at":1788792159766,"session":"11111111-1111-1111-1111-111111111111","event":"state","from_state":null,"to_state":"working","state":"working","reason":"hook"}
{"seq":102,"at":1788792160000,"session":"22222222-2222-2222-2222-222222222222","event":"state","from_state":null,"to_state":"working","state":"working","reason":"hook"}
{"seq":103,"at":1788792199000,"session":"11111111-1111-1111-1111-111111111111","event":"state","from_state":"working","to_state":"done","state":"done","reason":"hook"}
EOF

out="$($QUEUE watch --for-secs 1 2>&1)"
expect "watch folds transitions into the record" "01-drop-idle-default" "$out"
expect "watch sees the stream's sequence numbers" "seq 101" "$out"
expect "watch reports the turn that ended with no result" "no result" "$out"
refute "watch never closes a task" "done: " "$out"

state="$($QUEUE show "$topic/01-drop-idle-default" 2>&1 | grep -F 'state:')"
expect "the task whose turn ended is still dispatched" "dispatched" "$state"

progress="$FLEET_QUEUE_DIR/$topic/01-drop-idle-default/progress.jsonl"
if [ -s "$progress" ]; then
	pass "progress.jsonl records what happened when"
else
	fail "progress.jsonl records what happened when" "empty or missing: $progress"
fi

out="$($QUEUE watch --for-secs 1 2>&1)"
refute "each floor resumes, so a second watch replays nothing" "seq 101" "$out"

# --- 15. no transition is lost between watch runs ----------------------------
#
# The bug this proves gone: 19 of 20 live tasks had an EMPTY progress.jsonl
# while the stream still held their transitions. `watch` kept one queue-wide
# `.cursor` and advanced it over every event it read — folded or not — while
# deciding what to fold from a `by_session` map snapshotted before the stream
# was opened. Two things fell through that seam:
#
#   (a) a task dispatched WHILE a watch is streaming is not in that map, so its
#       transitions were skipped and the shared cursor was written past them.
#       They are then unreachable on every later run: the stream was consumed
#       for a task that was never updated.
#   (b) a watch that died part-way through a batch had written no cursor at
#       all, so the next one replayed and re-appended what it had already
#       folded.
#
# The floor is now per task and derived from the task's OWN progress.jsonl, so
# a task advances only over the events it actually folded, and a crash costs
# neither a skip nor a duplicate. This runs against its own throwaway queue so
# the fixtures above keep the state the tests before it left them in.

captmp="$(mktemp -d)"
capq="$captmp/queue"
capev="$captmp/events.jsonl"
capseed="$captmp/seed.jsonl"
sesa=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa
sesb=bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb
sesc=cccccccc-cccc-cccc-cccc-cccccccccccc

capqueue() { FLEET_QUEUE_DIR="$capq" $QUEUE "$@"; }

capline() {
	printf '{"seq":%s,"at":1788793000000,"session":"%s","event":"state",' "$1" "$2"
	printf '"from_state":null,"to_state":"%s","state":"%s","reason":"hook"}\n' "$3" "$3"
}

# The sequence numbers a task's record actually holds, in order, so a SKIP and
# a DUPLICATE are both visible in one string.
capseqs() {
	python3 - "$1" <<'CAPPY'
import json, os, sys
path = sys.argv[1]
if not os.path.exists(path):
    print("<missing>")
    raise SystemExit
print(" ".join(str(json.loads(line)["seq"]) for line in open(path) if line.strip()))
CAPPY
}

capheld() {
	local label="$1" want="$2" ref="$3" got
	got="$(capseqs "$capq/$capt/$ref/progress.jsonl")"
	if [ "$got" = "$want" ]; then
		pass "$label"
	else
		fail "$label" "$ref holds: $got${nl}expected:  $want"
	fi
}

: >"$capseed"
export FLEET_QUEUE_WATCH_CMD="cat $capseed"
capt="$(capqueue topic add capture-every-transition \
	--prompt 'prove no transition is lost between watch runs')" || exit 1
capqueue add "$capt" task-a --title 'task a' \
	--repo /tmp/repo-a --branch fix/a --number 01 >/dev/null
capqueue add "$capt" task-b --title 'task b' \
	--repo /tmp/repo-b --branch fix/b --number 02 >/dev/null
capqueue attach "$capt/01-task-a" "$sesa" >/dev/null

# (a) One batch, two tasks — and the second one is dispatched INSIDE the watch
#     window. The stream command itself does that attach, which is the only
#     honest way to write "a dispatch landed while the stream was open": it is
#     precisely the instant `by_session` cannot know about. The nested attach
#     reads the seed stream, not this one, so task b's own floor starts where
#     its session did and not past its first transition.
{
	capline 101 "$sesa" "working"
	capline 102 "$sesb" "idle"
	capline 103 "$sesa" "working"
} >"$capev"

export FLEET_QUEUE_WATCH_CMD="FLEET_QUEUE_WATCH_CMD='cat $capseed' FLEET_QUEUE_DIR='$capq' $QUEUE attach '$capt/02-task-b' $sesb >/dev/null 2>&1; cat $capev"
capqueue watch --for-secs 0 >/dev/null 2>&1
export FLEET_QUEUE_WATCH_CMD="cat $capev"
capqueue watch --for-secs 0 >/dev/null 2>&1

capheld "a batch carrying two tasks leaves the first one complete" "101 103" 01-task-a
capheld "and the one dispatched mid-window keeps its transition too" "102" 02-task-b

# (b) Nothing is watching while the next two events happen. The stream is
#     replayed from each task's own floor, so the gap costs only the wait.
{
	capline 104 "$sesa" "done"
	capline 105 "$sesb" "done"
} >>"$capev"
capqueue watch --for-secs 0 >/dev/null 2>&1
capheld "events that arrived with no watch running are folded by the next one" \
	"101 103 104" 01-task-a
capheld "for every task in the gap, not just the first" "102 105" 02-task-b

# (c) A watch that dies part-way. task b's record is replaced by a DIRECTORY,
#     so the append fails for real — no permission trick that a run as root
#     would sail straight through. The run stops after folding 106 into task a
#     and before writing 107 anywhere; what matters is what the NEXT run does
#     with the events it never reached.
{
	capline 106 "$sesa" "working"
	capline 107 "$sesb" "working"
	capline 108 "$sesa" "idle"
} >>"$capev"

bprog="$capq/$capt/02-task-b/progress.jsonl"
mv "$bprog" "$captmp/b-progress.saved"
mkdir "$bprog"
if out="$(capqueue watch --for-secs 0 2>&1)"; then
	fail "a watch that cannot write a record stops instead of walking past it" "$out"
else
	expect "a watch that cannot write a record stops instead of walking past it" \
		"02-task-b" "$out"
fi
rmdir "$bprog"
mv "$captmp/b-progress.saved" "$bprog"

capqueue watch --for-secs 0 >/dev/null 2>&1
capheld "an interrupted watch skips nothing it had not written" \
	"101 103 104 106 108" 01-task-a
capheld "and replays nothing it had" "102 105 107" 02-task-b

# (d) The queue this landed on already had a `.cursor` and no per-task floors.
#     A record written before the floor moved onto the task falls back to that
#     retired file, once, so the live queue resumes where its old cursor
#     stopped — neither replaying the whole backlog nor skipping the gap. The
#     legacy value is set to 109 and an event at 109 is offered with it: a
#     fallback of 0 would fold that one too, and a fallback of "now" would fold
#     neither.
capqueue add "$capt" task-c --title 'task c' \
	--repo /tmp/repo-c --branch fix/c --number 03 >/dev/null
capqueue attach "$capt/03-task-c" "$sesc" >/dev/null
python3 - "$capq/$capt/03-task-c/task.yaml" <<'CAPPY'
import sys
path = sys.argv[1]
kept = [line for line in open(path) if not line.startswith("watch_from:")]
open(path, "w").write("".join(kept))
CAPPY
echo 109 >"$capq/.cursor"
{
	capline 109 "$sesc" "idle"
	capline 110 "$sesc" "working"
} >>"$capev"
capqueue watch --for-secs 0 >/dev/null 2>&1
capheld "a record from before per-task floors resumes at the retired cursor" \
	"110" 03-task-c

rm -rf "$captmp"
export FLEET_QUEUE_WATCH_CMD="cat $events"

# --- 7. a genuinely zero floor is not treated as "no floor" ------------------
#
# `attach` legitimately stamps 0 when the stream's high-water mark really is 0
# (a brand-new thurbox instance), and that must stay distinct from a task with
# no floor recorded at all: cmd_watch used to test its cursor for truthiness,
# so a real 0 was silently treated the same as "nothing recorded" and the
# --since flag was dropped from the real `thurbox-cli watch` call, starting the
# first watch after a dispatch from "now" instead of replaying from seq 0 —
# quietly losing any transition in between. This needs the real command path
# (not FLEET_QUEUE_WATCH_CMD, which replaces the whole command and never sees
# the flags), so it stubs `thurbox-cli` on PATH and reads what it was called
# with.
zerotmp="$(mktemp -d)"
fakebin="$zerotmp/bin"
mkdir -p "$fakebin"
cliargs="$zerotmp/cli-args.log"
cat >"$fakebin/thurbox-cli" <<SH
#!/bin/sh
echo "\$@" >>"$cliargs"
exit 0
SH
chmod +x "$fakebin/thurbox-cli"

(
	export PATH="$fakebin:$PATH"
	unset FLEET_QUEUE_WATCH_CMD
	zt="$(FLEET_QUEUE_DIR="$zerotmp/queue" $QUEUE topic add zero-cursor 2>/dev/null \
		--prompt 'prove a real zero cursor is not dropped')" || exit 1
	FLEET_QUEUE_DIR="$zerotmp/queue" $QUEUE add "$zt" only-task --title 'only task' \
		--repo /tmp/repo-z --branch fix/only-task --number 01 >/dev/null
	FLEET_QUEUE_DIR="$zerotmp/queue" $QUEUE attach "$zt/01-only-task" \
		33333333-3333-3333-3333-333333333333 >/dev/null
	FLEET_QUEUE_DIR="$zerotmp/queue" $QUEUE watch --for-secs 0 >/dev/null 2>&1
)

floor="$(grep -h '^watch_from:' \
	"$zerotmp"/queue/*/01-only-task/task.yaml 2>/dev/null || echo '<missing>')"
if [ "$floor" = "watch_from: 0" ]; then
	pass "attach stamps a genuine zero when the stream's high-water mark is 0"
else
	fail "attach stamps a genuine zero when the stream's high-water mark is 0" \
		"got: $floor"
fi

expect "watch passes --since 0 to the real stream rather than dropping it" \
	"--since 0" "$(cat "$cliargs" 2>/dev/null)"

rm -rf "$zerotmp"

# --- 3 and 9. the blocker clears on the MERGE, not on the conclusion ---------
#
# The bug this proves gone: a task collected `shipped` released its dependents
# while its pull request was still open and unreviewed, and the lead had to
# hold the dependent task by hand. `outcome: shipped` in a result means a pull
# request EXISTS. Whether it landed is a question only the forge can answer,
# and it is the same question that decides whether a session may be reaped.

out="$($QUEUE collect 2>&1)"
expect "collect finds nothing to conclude yet" "0 result" "$out"

plan="$($QUEUE plan 2>&1)"
expect "03 still waits after its blocker's turn ended" "waiting: 1" "$plan"

session_is 11111111-1111-1111-1111-111111111111 idle
session_is 22222222-2222-2222-2222-222222222222 working

cat >"$FLEET_QUEUE_DIR/$topic/01-drop-idle-default/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/Thurbeen/thurbox/pull/999
---
Dropped the idle default; an unreported session now reads `unreported`.
EOF

out="$($QUEUE collect 2>&1)"
expect "collect reads the worker's own conclusion" "shipped" "$out"
expect "collect names the artifact" "pull/999" "$out"
expect "and keeps the session, saying the pull request is still open" \
	"still open" "$out"
refute "an open pull request's session is never reaped" "reaped" "$out"

plan="$($QUEUE plan 2>&1)"
expect "03 keeps waiting: its blocker concluded but did not land" "waiting: 1" "$plan"
refute "so the ready set has not grown" "ready: 2" "$plan"

# The merge — the only thing that lands a task, and the only thing that
# authorises deleting the session that produced it.
echo MERGED >"$states/999.state"

out="$($QUEUE reap --dry-run 2>&1)"
expect "a dry run says what would land" "would be landed" "$out"
expect "and which session it would release" "would reap" "$out"
if [ -s "$deletions" ]; then
	fail "a dry run deletes nothing" "$(cat "$deletions")"
else
	pass "a dry run deletes nothing"
fi
plan="$($QUEUE plan 2>&1)"
expect "and a dry run wrote nothing either: 03 still waits" "waiting: 1" "$plan"

out="$($QUEUE reap 2>&1)"
expect "the merge lands the task" "landed" "$out"
expect "and its session is released" "reaped" "$out"
expect "the deletion is forced, or the worktree is never actually freed" \
	"--force" "$(cat "$deletions")"
expect "and it names the session the record held" \
	"11111111-1111-1111-1111-111111111111" "$(cat "$deletions")"

state="$($QUEUE show "$topic/01-drop-idle-default" 2>&1)"
expect "the record says landed" "state:       landed" "$state"
expect "and keeps the receipt for the session it released" "reaped:" "$state"
refute "and stops pointing at an id that no longer resolves" \
	"session:     11111111" "$state"

plan="$($QUEUE plan 2>&1)"
expect "03 becomes ready once 01 has LANDED" "03-render-detected-agent" "$plan"
expect "and it joins 04, which never waited" "ready: 2" "$plan"
refute "01 has left the plan entirely" "01-drop-idle-default" "$plan"

# --- the record carries the topic view without any new field -----------------

out="$($QUEUE list 2>&1)"
expect "list groups by topic" "$topic" "$out"
expect "list is one line per task, not a brief" "04-log-state-changes" "$out"

out="$($QUEUE check 2>&1)"
expect "check validates every record" "ok" "$out"

# --- 8. collect verifies the artifact instead of trusting the worker ---------
#
# The bug this proves gone: a brief said "open the PR by running
# `/no-mistakes --yes`", which is an instruction about a METHOD, and a method
# leaves no trace a checker can read. Two tasks were collected `shipped` with
# hand-made `gh pr create` PRs and nothing noticed until an operator read the
# bodies himself. So a task declares what its publish must LEAVE BEHIND, and
# `collect` goes and looks for that instead — the forge for a pull request, git
# for a commit on the base branch.
#
# These four tasks take the operator's own default from POLICY.md's
# frontmatter, which is `no-mistakes`: a pull request from the task's own
# branch whose body attests the commit that would merge. 8b below covers the
# other two methods and the declaration that chooses between them.
#
# The `gh` stub at the top of this file serves one pull request per number, so
# all three answers are reachable offline: one the pipeline opened, one opened
# by hand, and one the stub cannot fetch at all.

pipeline_pr 1001 fix/document-the-states
plain_pr 1002 fix/render-detected-agent \
	"Rendered detected_agent. Opened with \`gh pr create\`, which is the thing to catch."

cat >"$FLEET_QUEUE_DIR/$topic/02-document-the-states/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/Thurbeen/thurbox/pull/1001
---
Documented the state vocabulary.
EOF

cat >"$FLEET_QUEUE_DIR/$topic/03-render-detected-agent/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/Thurbeen/thurbox/pull/1002
---
Rendered detected_agent, and opened the PR by hand.
EOF

cat >"$FLEET_QUEUE_DIR/$topic/04-log-state-changes/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/Thurbeen/thurbox/pull/1003
---
Logged every state change. The PR body cannot be fetched from here.
EOF

out="$($QUEUE collect 2>&1)"

expect "an attested PR from the task's own branch collects clean" \
	"02-document-the-states" "$out"
expect "and collect says which method it verified" \
	"[publish verified: no-mistakes]" "$out"

expect "a PR that skipped the pipeline is caught" "03-render-detected-agent" "$out"
expect "the refusal says what the body does not carry" "attestation" "$out"
expect "the refusal says the task was not closed" "NOT CLOSED" "$out"
expect "and states what would have proved it" "no-mistakes" "$out"
expect "and quotes the tool the brief named, in the operator's own words" \
	"Its brief said:" "$out"

state="$($QUEUE show "$topic/03-render-detected-agent" 2>&1)"
refute "a task whose PR failed the check is not closed" "state:       done" "$state"

expect "an unreachable gh degrades to unknown" "04-log-state-changes" "$out"
expect "and says the check could not run" "could not" "$out"
refute "an unchecked artifact is never reported as verified" \
	"04-log-state-changes  shipped  https://github.com/Thurbeen/thurbox/pull/1003  [publish verified" "$out"

state="$($QUEUE show "$topic/04-log-state-changes" 2>&1)"
expect "but the loop still closes it — no network must not break collect" \
	"state:       done" "$state"
expect "and the record says the check could not run, not that it passed" \
	"unknown" "$state"

out="$($QUEUE collect --allow-unverified 2>&1)"
expect "the lead can close a flagged task deliberately" "03-render-detected-agent" "$out"
state="$($QUEUE show "$topic/03-render-detected-agent" 2>&1)"
expect "and the record keeps saying the artifact failed the check" "missing" "$state"

# --- `shipped` with no pull request is a claim, not a skip -------------------
#
# `not-applicable` and `stuck` legitimately produce no artifact, and that is
# `skipped`. `shipped` is a claim that a pull request exists, so a worker that
# omits `artifact` (or writes something that is not a pull request URL) must
# not sail through as if it were that same legitimate silence.

$QUEUE add "$topic" ship-without-proof --title 'Ship without proof' \
	--repo /tmp/repo-a --branch fix/ship-without-proof --number 05 >/dev/null

cat >"$FLEET_QUEUE_DIR/$topic/05-ship-without-proof/result.md" <<'EOF'
---
outcome: shipped
---
Shipped it, but did not say where.
EOF

out="$($QUEUE collect 2>&1)"
expect "a shipped claim with no pull request is caught, not skipped" \
	"05-ship-without-proof" "$out"
expect "and the refusal says the task was not closed" "NOT CLOSED" "$out"

state="$($QUEUE show "$topic/05-ship-without-proof" 2>&1)"
refute "a shipped claim with no pull request is not closed" "state:       done" "$state"
expect "and the record says missing, not skipped" "missing" "$state"

# --- 8b. the publish METHOD: declared at intake, verified at collect ---------
#
# Fleet used to know exactly one way of publishing and hard-coded the proof of
# it. That is the same non-agnosticism whichever tool is hard-coded, so a task
# now declares an ARTIFACT SHAPE — `no-mistakes`, `pr` or `push` — and the tool
# rides beside it as `--how`, free text that is rendered into the brief and
# never parsed. That last part is the whole property: an operator's own
# `/publish` skill, or a repo's `make release`, works because fleet does not
# try to understand it.
#
# Everything below is one claim per method: the brief says the right thing, and
# collect goes and looks at the right place.

# A real repository for the `push` method, because "the commit reached the base
# branch" is a fact of git and stubbing git would prove nothing. `origin` is a
# bare repo beside it, so `origin/main` is a genuine remote-tracking ref.
porigin="$tmp/push-origin"
pwork="$tmp/push-work"
git init -q --bare -b main "$porigin"
git init -q -b main "$pwork"
git -C "$pwork" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base
git -C "$pwork" remote add origin "$porigin"
git -C "$pwork" push -q origin main
landed_sha="$(git -C "$pwork" rev-parse HEAD)"
git -C "$pwork" checkout -q -b aside
git -C "$pwork" -c user.email=t@t -c user.name=t commit -q --allow-empty -m 'never pushed'
aside_sha="$(git -C "$pwork" rev-parse HEAD)"
git -C "$pwork" checkout -q main

ptopic="$($QUEUE topic add publish-methods --title 'How a task publishes' \
	--prompt 'be agnostic about the tool; verify the artifact')"

$QUEUE add "$ptopic" pipeline-task --title 'Publish through the pipeline' \
	--repo /tmp/repo-a --branch fix/pipeline-task --number 01 >/dev/null
$QUEUE add "$ptopic" pr-task --title 'Publish as a plain pull request' \
	--repo /tmp/repo-a --branch fix/pr-task --number 02 \
	--publish pr --how 'run the operator xyz skill' >/dev/null
$QUEUE add "$ptopic" push-task --title 'Publish straight onto the base branch' \
	--repo "$pwork" --branch fix/push-task --base main --number 03 \
	--publish push >/dev/null

# (a) The declaration reaches the worker, rendered from the one dict in
#     queue.py — with the operator's words for the tool when there are any, and
#     nothing at all when there are not.

b="$(brief_text "$FLEET_QUEUE_DIR/$ptopic/01-pipeline-task/BRIEF.md")"
expect "a task with no --publish takes POLICY.md's own default" \
	"**Publish.** \`no-mistakes\`" "$b"
expect "and the brief names the tool in the operator's own words" \
	"Here that means: run \`/no-mistakes --yes\`." "$b"

b="$(brief_text "$FLEET_QUEUE_DIR/$ptopic/02-pr-task/BRIEF.md")"
expect "a --publish pr task says so" "**Publish.** \`pr\`" "$b"
expect "and carries the --how it was given" "operator xyz skill" "$b"
refute "and not the operator's default tool, which belongs to another method" \
	"\`no-mistakes\`" "$b"

b="$(brief_text "$FLEET_QUEUE_DIR/$ptopic/03-push-task/BRIEF.md")"
expect "a --publish push task says so" "**Publish.** \`push\`" "$b"
refute "and says nothing about a tool when it was given none" \
	"Here that means" "$b"
expect "and every brief's result contract now admits a commit URL" \
	"commit URL for a" "$b"

# (b) A `pr` task is proven by a pull request from ITS OWN branch — the one
#     claim about a pull request a worker cannot write into its own result.md.

plain_pr 1010 fix/pr-task
cat >"$FLEET_QUEUE_DIR/$ptopic/02-pr-task/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/acme/app/pull/1010
---
Opened it with the operator's own skill, which fleet knows nothing about.
EOF

# (c) A `push` task is proven by git: the commit is on the base branch.

cat >"$FLEET_QUEUE_DIR/$ptopic/03-push-task/result.md" <<EOF
---
outcome: shipped
artifact: https://github.com/acme/app/commit/$landed_sha
---
Committed onto main and pushed it; there is no pull request.
EOF

out="$($QUEUE collect --no-reap 2>&1)"
expect "a pull request from the task's branch proves a pr task" \
	"[publish verified: pr]" "$out"
expect "and a commit on the base branch proves a push task" \
	"[publish verified: push]" "$out"

state="$($QUEUE show "$ptopic/02-pr-task" 2>&1)"
expect "the record keeps the method beside the verdict" "publish:     pr" "$state"
expect "and the publish state a passing pr task reaches" "published:   open" "$state"
state="$($QUEUE show "$ptopic/03-push-task" 2>&1)"
expect "a push task's publish state is terminal, not awaiting a review" \
	"published:   pushed" "$state"
expect "and list carries that state beside the artifact" "pushed" \
	"$($QUEUE list --topic "$ptopic" 2>&1)"

# (d) The four ways a claim fails to hold up, each held OPEN rather than
#     closed on the word alone.

$QUEUE add "$ptopic" pr-elsewhere --title 'Paste somebody else good PR' \
	--repo /tmp/repo-a --branch fix/pr-elsewhere --number 04 --publish pr >/dev/null
plain_pr 1011 fix/somebody-elses-work
cat >"$FLEET_QUEUE_DIR/$ptopic/04-pr-elsewhere/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/acme/app/pull/1011
---
Here is a pull request. It is green. It is not mine.
EOF

$QUEUE add "$ptopic" stale-attestation --title 'Push again after the pipeline ran' \
	--repo /tmp/repo-a --branch fix/stale-attestation --number 05 >/dev/null
pipeline_pr 1012 fix/stale-attestation "$(printf 'f%.0s' $(seq 40))"
cat >"$FLEET_QUEUE_DIR/$ptopic/05-stale-attestation/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/acme/app/pull/1012
---
The pipeline ran, and then I pushed one more commit.
EOF

$QUEUE add "$ptopic" push-astray --title 'Push a commit that never landed' \
	--repo "$pwork" --branch fix/push-astray --base main --number 06 \
	--publish push >/dev/null
cat >"$FLEET_QUEUE_DIR/$ptopic/06-push-astray/result.md" <<EOF
---
outcome: shipped
artifact: https://github.com/acme/app/commit/$aside_sha
---
Committed it. It is not on main.
EOF

out="$($QUEUE collect --no-reap 2>&1)"
expect "a pull request from another branch does not prove this task" \
	"04-pr-elsewhere" "$out"
expect "and the refusal names the branch it actually came from" \
	"fix/somebody-elses-work" "$out"
expect "an attestation for an earlier head does not prove this one" \
	"05-stale-attestation" "$out"
expect "a commit that never reached the base branch does not prove a push task" \
	"06-push-astray" "$out"
expect "and the refusal says where it looked" "origin/main" "$out"
for t in 04-pr-elsewhere 05-stale-attestation 06-push-astray; do
	state="$($QUEUE show "$ptopic/$t" 2>&1)"
	refute "$t is held open, not closed" "state:       done" "$state"
	expect "and the record says why nobody could prove it" \
		"published:   unverified" "$state"
done

# (e) The three ways the check cannot RUN. None of them is a pass and none is a
#     failure: an offline laptop and a CI runner with no `gh` both still have to
#     be able to collect, and a timeout must never manufacture a verdict.

$QUEUE add "$ptopic" push-elsewhere --title 'Push on a machine that is not this one' \
	--repo /srv/code/app --host devbox --branch fix/push-elsewhere --base main \
	--number 07 \
	--publish push >/dev/null
cat >"$FLEET_QUEUE_DIR/$ptopic/07-push-elsewhere/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/acme/app/commit/0123456789abcdef0123456789abcdef01234567
---
Pushed it on devbox.
EOF

$QUEUE add "$ptopic" push-unreadable --title 'Push into a repo this machine has not got' \
	--repo /tmp/not-a-checkout --branch fix/push-unreadable --base main \
	--number 08 \
	--publish push >/dev/null
cat >"$FLEET_QUEUE_DIR/$ptopic/08-push-unreadable/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/acme/app/commit/0123456789abcdef0123456789abcdef01234567
---
Pushed it somewhere this machine cannot see.
EOF

out="$($QUEUE collect --no-reap 2>&1)"
expect "a push task on a host is not judged from here" \
	"the base branch is on host devbox" "$out"
expect "and neither is one whose repo this machine has not got" \
	"could not be read" "$out"
for t in 07-push-elsewhere 08-push-unreadable; do
	state="$($QUEUE show "$ptopic/$t" 2>&1)"
	refute "$t still closes — could not check must not break collect" \
		"state:       queued" "$state"
	expect "and the record says the check could not run" "published:   unknown" "$state"
done

# (f) A record written before any of this existed. It carries no `publish`
#     block at all, and it must keep the verification it was dispatched under —
#     which is the operator's POLICY.md default and NOT the `pr` fleet ships.
#     A `pr` reading would pass this pull request; a `no-mistakes` one holds it.

$QUEUE add "$ptopic" legacy-record --title 'A task from before the field existed' \
	--repo /tmp/repo-a --branch fix/legacy-record --number 09 >/dev/null
python3 - "$FLEET_QUEUE_DIR/$ptopic/09-legacy-record/task.yaml" <<'PY'
import sys

import yaml

path = sys.argv[1]
doc = yaml.safe_load(open(path))
doc.pop("publish")
open(path, "w").write(yaml.safe_dump(doc, sort_keys=False))
PY
plain_pr 1013 fix/legacy-record
cat >"$FLEET_QUEUE_DIR/$ptopic/09-legacy-record/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/acme/app/pull/1013
---
Opened it by hand, exactly as the two tasks that started all this did.
EOF

out="$($QUEUE collect --no-reap 2>&1)"
expect "a record with no publish block is still verified" "09-legacy-record" "$out"
expect "and against the operator's default, not the one fleet ships" \
	"attestation" "$out"
state="$($QUEUE show "$ptopic/09-legacy-record" 2>&1)"
refute "so it is held open, exactly as it would have been before" \
	"state:       done" "$state"
expect "and show reports the method it was read as" "publish:     no-mistakes" "$state"

out="$($QUEUE check 2>&1)"
expect "and check is happy with a record that declares nothing" "ok" "$out"

# (g) No `gh` on PATH at all. Its own queue and its own PATH, so the answer
#     cannot depend on anything else that happens to be pending.

nogh="$tmp/nogh-bin"
noghq="$tmp/nogh-queue"
mkdir -p "$nogh"
for t in bash dirname python3 git; do ln -sf "$(command -v "$t")" "$nogh/$t"; done
nq() { env PATH="$nogh" FLEET_QUEUE_DIR="$noghq" ./scripts/queue.sh "$@"; }

nq topic add offline --prompt 'collect on a machine with no forge to ask' >/dev/null
nq add offline unreachable --title 'Publish with nothing to ask about it' \
	--repo /tmp/repo-a --branch fix/unreachable --publish pr >/dev/null
cat >"$noghq/offline/01-unreachable/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/acme/app/pull/1
---
Opened it. This machine has no gh.
EOF

out="$(nq collect --no-reap 2>&1)"
expect "with no gh on PATH the check is unchecked, not failed" "unchecked" "$out"
state="$(nq show offline/01-unreachable 2>&1)"
expect "and the task still closes — an offline machine must still collect" \
	"state:       done" "$state"
expect "and the record says gh was never there to ask" "gh not found" "$state"

# (h) A POLICY.md with no frontmatter — the state this repo shipped in, and the
#     state a fresh clone is in. It answers `pr`, and nothing errors.

bare="$tmp/bare-clone"
mkdir -p "$bare/scripts/lib" "$bare/orchestration/queue"
cp scripts/queue.sh "$bare/scripts/queue.sh"
for lib in queue.py forge.py; do
	ln -s "$PWD/scripts/lib/$lib" "$bare/scripts/lib/$lib"
done
cat >"$bare/orchestration/queue/POLICY.md" <<'EOF'
# Standing policy for fleet workers

No frontmatter here, which is what every clone starts with.
EOF
bq() { env FLEET_QUEUE_DIR="$tmp/bare-queue" "$bare/scripts/queue.sh" "$@"; }

if out="$(bq topic add unconfigured --prompt 'a clone nobody has configured' 2>&1)"; then
	pass "a clone whose POLICY.md has no frontmatter opens a topic"
else
	fail "a clone whose POLICY.md has no frontmatter opens a topic" "$out"
fi
bq add unconfigured first-task --title 'The first task of a fresh clone' \
	--repo /tmp/repo-a --branch fix/first-task >/dev/null 2>&1
b="$(brief_text "$tmp/bare-queue/unconfigured/01-first-task/BRIEF.md" 2>&1)"
expect "and its task defaults to pr, which needs no setup at all" \
	"**Publish.** \`pr\`" "$b"
refute "with no tool named, because nobody named one" "Here that means" "$b"

# (i) The stale attestation the pipeline caused ITSELF, told apart from every
#     other one. `no-mistakes` writes the attestation while it opens the pull
#     request and can then push its own CI fixes on top, which leaves the body
#     naming an ancestor of the head — the shape of #38, #40 and #48, three
#     pull requests that could never auto-merge and that read, at collect
#     time, exactly like a worker force-pushing over the pipeline. One is
#     fixed by running the tool again and the other is not.
#
#     Both are still REFUSED, and by the same line of code. Only the wording
#     is new.

$QUEUE add "$ptopic" pipeline-pushed-after \
	--title 'Let the pipeline push its own CI fix after it attested' \
	--repo /tmp/repo-a --branch fix/pipeline-pushed-after --number 10 >/dev/null
attested_sha="$(printf 'a%.0s' $(seq 40))"
pipeline_pr 1014 fix/pipeline-pushed-after "$attested_sha"
pr_history 1014 \
	"$attested_sha chore: no-mistakes document - Sync the docs" \
	"$(printf '%040d' 1014) no-mistakes: apply CI fixes"
cat >"$FLEET_QUEUE_DIR/$ptopic/10-pipeline-pushed-after/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/acme/app/pull/1014
---
Ran the pipeline. It attested, opened the PR, and then pushed a CI fix.
EOF

$QUEUE add "$ptopic" pushed-over-pipeline \
	--title 'Push over the pipeline by hand' \
	--repo /tmp/repo-a --branch fix/pushed-over-pipeline --number 11 >/dev/null
pipeline_pr 1015 fix/pushed-over-pipeline "$attested_sha"
pr_history 1015 \
	"$attested_sha chore: no-mistakes document - Sync the docs" \
	"$(printf '%040d' 1015) fix: one more thing I thought of"
cat >"$FLEET_QUEUE_DIR/$ptopic/11-pushed-over-pipeline/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/acme/app/pull/1015
---
Ran the pipeline, then remembered one more thing and pushed it.
EOF

out="$($QUEUE collect --no-reap 2>&1)"
expect "an attestation the pipeline outran is still not proof" \
	"10-pipeline-pushed-after" "$out"
expect "and the refusal names what moved the head" \
	"the pipeline pushed that head itself" "$out"
expect "and the commit that did it, so a lead need not go and look" \
	"no-mistakes: apply CI fixes" "$out"
expect "and the one thing that fixes it" "re-run \`/no-mistakes --yes\`" "$out"

state="$($QUEUE show "$ptopic/10-pipeline-pushed-after" 2>&1)"
refute "the gate is exactly as strict as it was — nothing here closes a task" \
	"state:       done" "$state"
expect "and the record carries the whole reason, not just the terminal" \
	"the pipeline pushed that head itself" "$state"

state="$($QUEUE show "$ptopic/11-pushed-over-pipeline" 2>&1)"
expect "a head a PERSON moved is refused for the same reason" \
	"no longer what would merge" "$state"
refute "and is never blamed on the pipeline" \
	"the pipeline pushed that head itself" "$state"

state="$($QUEUE show "$ptopic/05-stale-attestation" 2>&1)"
refute "nor is one whose commits the forge would not enumerate" \
	"the pipeline pushed that head itself" "$state"

# --- 9. what is never reaped, and why ---------------------------------------
#
# Four sessions had accumulated on one machine, three with merged pull
# requests, the oldest holding twenty gigabytes since the previous day. The
# loop already said "delete each session as it closes out"; it was documented,
# it was manual, and it did not happen. The cases below are the ones where the
# answer is still "leave it", and getting any of them wrong kills live work or
# throws away the only evidence of a failure.

# (a) A session thurbox says is WORKING is never touched, whatever the record
#     claims. 02's pull request merges here, so the record says the task is
#     finished — and the session stays anyway.
echo MERGED >"$states/1001.state"

out="$($QUEUE reap 2>&1)"
expect "a merged artifact lands its task" "02-document-the-states" "$out"
expect "but a session thurbox says is working is kept" "working" "$out"
refute "and nothing was deleted for it" \
	"22222222-2222-2222-2222-222222222222" "$(cat "$deletions")"

state="$($QUEUE show "$topic/02-document-the-states" 2>&1)"
expect "the task still landed — landing and releasing are two questions" \
	"state:       landed" "$state"
expect "and it still names its session, because it still has one" \
	"22222222" "$state"

# (b) A task the worker gave up in keeps its session: that session IS the
#     evidence, and a human decides what to do with it.
$QUEUE add "$topic" investigate-the-crash --title 'Investigate the crash' \
	--repo /tmp/repo-a --branch fix/investigate-the-crash --number 06 >/dev/null
$QUEUE attach "$topic/06-investigate-the-crash" \
	66666666-6666-6666-6666-666666666666 >/dev/null
session_is 66666666-6666-6666-6666-666666666666 idle

cat >"$FLEET_QUEUE_DIR/$topic/06-investigate-the-crash/result.md" <<'EOF'
---
outcome: failed
---
The crash does not reproduce here. The worktree has the logs.
EOF

out="$($QUEUE collect 2>&1)"
expect "a failed task is closed by its own result" "06-investigate-the-crash" "$out"
expect "but its session is kept as the evidence" "evidence" "$out"
refute "and an idle session is still not deleted when the task failed" \
	"66666666-6666-6666-6666-666666666666" "$(cat "$deletions")"

# (c) A task that produced no artifact skips straight through rather than
#     waiting forever for a merge that is never coming — and `collect` does the
#     reaping itself, because a step only a human remembers does not run.
$QUEUE add "$topic" answer-a-question --title 'Answer a question' \
	--repo /tmp/repo-b --branch fix/answer-a-question --number 07 >/dev/null
$QUEUE attach "$topic/07-answer-a-question" \
	77777777-7777-7777-7777-777777777777 >/dev/null
session_is 77777777-7777-7777-7777-777777777777 idle

cat >"$FLEET_QUEUE_DIR/$topic/07-answer-a-question/result.md" <<'EOF'
---
outcome: not-applicable
---
The behaviour already worked; there was nothing to change.
EOF

out="$($QUEUE collect 2>&1)"
expect "a task with no artifact lands without waiting for a merge" \
	"07-answer-a-question" "$out"
expect "and collect releases its session without being asked to" "reaped" "$out"
expect "with --force, so the worktree actually goes" "--force" "$(cat "$deletions")"
expect "and it is the session the record held" \
	"77777777-7777-7777-7777-777777777777" "$(cat "$deletions")"

state="$($QUEUE show "$topic/07-answer-a-question" 2>&1)"
expect "the record keeps the receipt" "deleted" "$state"

# (d) `uncovered` is not `idle`. An agent wired to report nothing says nothing
#     by being quiet, so the reap reads the word and never the silence.
$QUEUE add "$topic" tidy-the-readme --title 'Tidy the readme' \
	--repo /tmp/repo-b --branch fix/tidy-the-readme --number 08 >/dev/null
$QUEUE attach "$topic/08-tidy-the-readme" \
	88888888-8888-8888-8888-888888888888 >/dev/null
session_is 88888888-8888-8888-8888-888888888888 uncovered

cat >"$FLEET_QUEUE_DIR/$topic/08-tidy-the-readme/result.md" <<'EOF'
---
outcome: not-applicable
---
Nothing to tidy.
EOF

out="$($QUEUE collect 2>&1)"
expect "an uncovered session is not an idle one" "uncovered" "$out"
refute "so it is kept, not deleted" \
	"88888888-8888-8888-8888-888888888888" "$(cat "$deletions")"

# (e) The lead's own session is not a worker and is never a candidate, even if
#     a record somehow names it.
out="$(THURBOX_SESSION=88888888-8888-8888-8888-888888888888 \
	$QUEUE reap --dry-run 2>&1)"
expect "the lead's own session is refused by name" "lead" "$out"

# (f) The reap can be told to stand down, and then it writes what landed and
#     touches nothing else.
out="$($QUEUE collect --no-reap 2>&1)"
expect "collect can be told to leave every session alone" "no-reap" "$out"
refute "and then it releases nothing" "reaped" "$out"

# --- the brief scaffold points at the policy instead of restating it ---------
#
# 871 lines of brief across five tasks, ~30 of them the same hand-copied
# policy, and `squash merge` had made it into one of the five. One tracked
# document, referenced by absolute path, is what the scaffold hands a worker.

brief="$(cat "$FLEET_QUEUE_DIR/$topic/02-document-the-states/BRIEF.md")"
policy="$PWD/orchestration/queue/POLICY.md"
if [ -f "$policy" ] && ! git check-ignore -q "$policy"; then
	pass "the policy document is tracked beside the ignored queue"
else
	fail "the policy document is tracked beside the ignored queue" \
		"missing or gitignored: $policy"
fi
expect "a scaffolded brief points the worker at it, by absolute path" \
	"$policy" "$brief"

# Design decision (d), restated for the vocabulary that replaced the headings:
# POLICY.md quotes nothing collect checks. It carries the operator's DEFAULT as
# frontmatter — the one thing in the file fleet parses — and points at the
# brief's Publish line for the rest, which is rendered from queue.py's own
# dict. Prove both by parsing, rather than by grepping the prose for a phrase:
# a rewording of the policy text does not touch this, and a second copy of a
# proof sentence does.
policy_vs_code="$(python3 - "$policy" <<'PY'
import sys

sys.path.insert(0, "scripts/lib")
import queue as q

text = open(sys.argv[1]).read()
method, how = q.policy_publish_default()
copied = [m for m, spec in q.PUBLISH_METHODS.items() if spec["proof"] in text]
print(f"default={method} how={bool(how)} copied={copied}")
PY
)"
expect "the operator's default is read out of POLICY.md, not typed into the code" \
	"default=no-mistakes how=True" "$policy_vs_code"
expect "and the policy restates no proof sentence, so none of them can drift" \
	"copied=[]" "$policy_vs_code"

# --- and it carries the OPERATOR's standing instructions, when there are any --
#
# `POLICY.md`'s counterpart: fleet owns the policy, the operator owns this, and
# a fresh clone has neither the file nor any mention of it. Both halves are
# proved here, because the absent case is the one that breaks a clone that
# never had a constitution.

refute "a brief written with no constitution mentions none" "OPERATOR.md" "$brief"

constitution="$FLEET_QUEUE_DIR/OPERATOR.md"
cat >"$constitution" <<'EOF'
Always reach for the operator's own `xyz` skill before writing a script.
EOF
$QUEUE add "$topic" honour-the-constitution --title 'Honour the constitution' \
	--repo /tmp/x --branch fix/honour --base main >/dev/null 2>&1
with_c="$(cat "$FLEET_QUEUE_DIR/$topic/09-honour-the-constitution/BRIEF.md")"
expect "a brief written with one points at it, by absolute path" \
	"$constitution" "$with_c"
expect "and says the brief still wins over it" "brief wins" "$with_c"

: >"$constitution"
$QUEUE add "$topic" empty-constitution --title 'Empty constitution' \
	--repo /tmp/x --branch fix/empty --base main >/dev/null 2>&1
refute "an empty constitution is the same as no constitution" "OPERATOR.md" \
	"$(cat "$FLEET_QUEUE_DIR/$topic/10-empty-constitution/BRIEF.md")"
rm -f "$constitution"

if [ -f orchestration/queue/OPERATOR.example.md ] &&
	! git check-ignore -q orchestration/queue/OPERATOR.example.md; then
	pass "the example that documents the format is tracked"
else
	fail "the example that documents the format is tracked" \
		"missing or gitignored: orchestration/queue/OPERATOR.example.md"
fi
if git check-ignore -q orchestration/queue/OPERATOR.md; then
	pass "the operator's own copy is gitignored"
else
	fail "the operator's own copy is gitignored" \
		"orchestration/queue/OPERATOR.md is not ignored"
fi

# --- 7. the queue belongs to a CHECKOUT, not to a cwd ------------------------
#
# The bug this proves gone: `queue_root()` was relative, so it resolved against
# whatever directory the shell happened to be in. A lead whose shell sat in a
# second clone of this repo opened a topic there, dispatched from it, and the
# TUI pane — reading the control plane's queue — correctly showed nothing. No
# warning at any point. Two queues, silently.
#
# The whole thing is exercised against a THROWAWAY CLONE rather than this one,
# so the test says the same thing on a machine with thurbox and on CI without
# it: a directory holding `scripts/queue.sh`, symlinks to the real
# `scripts/lib/*.py` (the anchor is the script's own path, so a symlink is a
# whole clone for this purpose) and a rendered `extension.toml` that decides
# whether that clone IS the control plane.

clonetmp="$(mktemp -d)"
fake="$clonetmp/second-clone"
mkdir -p "$fake/scripts/lib" "$fake/deep/sub/dir"
cp scripts/queue.sh "$fake/scripts/queue.sh"
for lib in queue.py forge.py; do
	ln -s "$PWD/scripts/lib/$lib" "$fake/scripts/lib/$lib"
done
FAKEQ="$fake/scripts/queue.sh"

# Render the manifest install-extension.sh would have written, naming whichever
# checkout is the control plane for the case under test.
declare_control_plane() {
	printf '[[sessions]]\nname = "fleet"\nrepo_path = "%s"\n' "$1" >"$fake/extension.toml"
}

# --- it is the control plane: anchored to the checkout, from anywhere in it ---

declare_control_plane "$fake"

from_root="$(cd "$fake" && env -u FLEET_QUEUE_DIR ./scripts/queue.sh root 2>&1)"
expect "root is the checkout's own queue, absolute" "$fake/orchestration/queue" "$from_root"

from_sub="$(cd "$fake/deep/sub/dir" && env -u FLEET_QUEUE_DIR "$FAKEQ" root 2>&1)"
if [ "$from_sub" = "$from_root" ]; then
	pass "a subdirectory resolves to the same queue as the root does"
else
	fail "a subdirectory resolves to the same queue as the root does" \
		"root: $from_root${nl}sub:  $from_sub"
fi

out="$(cd "$fake/deep/sub/dir" && env -u FLEET_QUEUE_DIR "$FAKEQ" topic add \
	from-a-subdir --prompt 'opened from deep inside the checkout' 2>&1)"
if [ -d "$fake/orchestration/queue/from-a-subdir" ]; then
	pass "the control plane's own queue takes a topic from a subdirectory"
else
	fail "the control plane's own queue takes a topic from a subdirectory" "$out"
fi
refute "and says nothing about the control plane, because it is it" \
	"control plane" "$out"
# This throwaway clone has no orchestration/runs/_TEMPLATE.md, and intake must
# not depend on one: a run log that cannot be scaffolded is reported and the
# topic is opened anyway.
expect "a missing run log template is reported, and stops nothing" \
	"run log not scaffolded" "$out"

# --- it is NOT the control plane: creating a second queue is refused ----------

declare_control_plane "$clonetmp/the-real-control-plane"

out="$(cd "$fake" && env -u FLEET_QUEUE_DIR ./scripts/queue.sh topic add forked \
	--prompt 'this would have silently forked the queue' 2>&1)"
rc=$?
if [ "$rc" -ne 0 ]; then
	pass "a topic opened outside the control plane is refused, not created"
else
	fail "a topic opened outside the control plane is refused, not created" "$out"
fi
expect "the refusal names this checkout" "$fake" "$out"
expect "the refusal names the control plane" "$clonetmp/the-real-control-plane" "$out"
expect "the refusal names the override that means it" "FLEET_QUEUE_DIR" "$out"
refute "and nothing was written" "forked" \
	"$(ls "$fake/orchestration/queue" 2>&1)"

# --- a second queue that already exists is loud on every read ----------------

out="$(cd "$fake" && env -u FLEET_QUEUE_DIR ./scripts/queue.sh list 2>&1)"
expect "list still works outside the control plane" "from-a-subdir" "$out"
expect "but it says which checkout it is reading" "$fake/orchestration/queue" "$out"
expect "and warns that this is not the control plane's queue" \
	"$clonetmp/the-real-control-plane" "$out"

out="$(cd "$fake" && env -u FLEET_QUEUE_DIR ./scripts/queue.sh check 2>&1)"
expect "check prints the resolved queue root too" "$fake/orchestration/queue" "$out"

# --- FLEET_QUEUE_DIR is honoured verbatim, with no guard --------------------
#
# The selftests point the queue at a temp directory. Someone who set it meant
# it, so nothing here may warn about it or refuse it.

out="$(cd "$fake" && FLEET_QUEUE_DIR="$clonetmp/explicit" ./scripts/queue.sh \
	topic add explicit --prompt 'I named the directory I meant' 2>&1)"
if [ -d "$clonetmp/explicit/explicit" ]; then
	pass "FLEET_QUEUE_DIR creates a topic outside the control plane unguarded"
else
	fail "FLEET_QUEUE_DIR creates a topic outside the control plane unguarded" "$out"
fi
refute "and the guard says nothing about it" "control plane" "$out"

rm -rf "$clonetmp"

# --- 7a. the session's real name, not the manifest header's own prose --------
#
# The bug this proves gone: manifest_session() used to find the session's name
# with `text.partition("[[sessions]]")` — a plain substring search. This repo's
# own extension.toml.in mentions "[[sessions]]" inside prose comments well
# before the real `[[sessions]]` table, so the partition landed on that FIRST
# occurrence and read whatever `name = "..."` came next — the top-level
# extension name, not the session's. That was invisible for exactly as long as
# the extension and the session shared a word, and returns the wrong name the
# moment they differ, which is exactly what renaming the session to
# "⌖ Mission Control" while the extension stays "fleet" does. manifest_session()
# now anchors on the table header at the start of a line, the same thing
# scripts/install-extension.sh matches with `/^\[\[sessions\]\]/`.

manifesttmp="$(mktemp -d)"
cat >"$manifesttmp/extension.toml" <<'EOF'
# Some prose that happens to mention [[sessions]] before the real table,
# the same way extension.toml.in's own header commentary does.
name = "fleet"

[[agents]]
name = "fleet"

[[sessions]]
name = "⌖ Mission Control"
repo_path = "/tmp/does-not-matter"
EOF

out="$(python3 -c '
import sys
sys.path.insert(0, "scripts/lib")
import queue as q
print(q.manifest_session(sys.argv[1]))
' "$manifesttmp/extension.toml")"
expect "manifest_session reads the TABLE's name, not the prose above it" \
	"('⌖ Mission Control', '/tmp/does-not-matter')" "$out"

rm -rf "$manifesttmp"

# --- 7b. the lead's glyph is rendered, and the WORDS still have to agree -----
#
# The glyph moved out of every file except the manifest, where it arrives as
# `__LEAD_GLYPH__` and `scripts/install-extension.sh` substitutes it from
# `orchestration/session-glyphs.conf`. Two things have to stay true for that to
# be safe, and neither is visible by reading one file:
#
#   the manifest must NOT carry a literal glyph, or the setting is decoration
#     over a name that never moves
#   whatever it renders to must still END in the name the pane matches, or the
#     pane hunts a session nobody spawns — the RENAMING header's partial rename,
#     which is the failure this whole arrangement exists to keep impossible

out="$(python3 -c '
import sys
sys.path.insert(0, "scripts/lib")
import queue as q
print(q.manifest_session(sys.argv[1])[0])
' "$PWD/extension.toml.in")"
expect "the tracked manifest names the lead through the glyph placeholder" \
	"__LEAD_GLYPH__ Mission Control" "$out"

pane_name="$(sed -n 's/^local CONTROL_PLANE = "\(.*\)"$/\1/p' interface/fleet_queue.lua)"
if [ -z "$pane_name" ]; then
	fail "the pane names the lead in a CONTROL_PLANE constant" \
		"could not read it from interface/fleet_queue.lua"
else
	pass "the pane names the lead in a CONTROL_PLANE constant ($pane_name)"
fi

# Rendered with each value the setting can take. Both must end in the pane's
# constant, and neither may equal it — a lead with no mark at all would mean the
# substitution silently produced nothing.
for key in LEAD_GLYPH_ON LEAD_GLYPH_OFF; do
	glyph="$(sed -n "s/^$key=//p" orchestration/session-glyphs.example.conf | head -1)"
	if [ -z "$glyph" ]; then
		fail "$key has a value in orchestration/session-glyphs.example.conf"
		continue
	fi
	rendered="${out/__LEAD_GLYPH__/$glyph}"
	if [ "$rendered" = "$glyph $pane_name" ]; then
		pass "rendered with $key the lead is '$rendered', which the pane still matches"
	else
		fail "rendered with $key the lead is a name the pane does not match" \
			"$rendered vs '<glyph> $pane_name'"
	fi
done

# --- 9. the shepherd: the pull request, after the worker stopped -------------
#
# The gap this closes, three times in one day: #14 went CONFLICTING when #13
# merged and nothing noticed; #11 and #12 were opened outside the pipeline and
# nobody saw for hours; a review finding sat in a PR body until a human read it
# out. Noticing was never the expensive part, so the claim under test is that a
# broken PR gets a FIXER and a good one gets MERGED — not that a status line
# gets printed.
#
# The list comes from the FORGE and not from the task records, because a task
# records one artifact — the first PR its worker reported — and #25 was a
# second PR from a task still pointing at the already-merged #23.
#
# The claims, and the ones after the first four are what make it safe to run
# unattended against a public repo that has a fork:
#
#   a conflicting PR dispatches exactly ONE fixer, on the branch that exists
#   a second pass over the same PR dispatches NONE
#   a green PR in an allowlisted repo is squash-merged
#   a PR outside the allowlist is never merged, whatever its state
#   a PR NO TASK RECORDS is discovered, classified and merged all the same
#   a second PR on a task's branch is linked back to that task
#   a PR from a FORK is never merged and never handed to an agent
#   a PR opened by someone who cannot push here is never merged
#   an attestation for an EARLIER head sha does not authorise this one
#   a body that SAYS the pipeline ran, which anyone can type, authorises
#   an unreachable `gh` dispatches none and merges none — "could not check"
#     is never "broken", and it is never "ready" either
#
# Both dependencies are stubbed on PATH, the way test 7's zero-cursor case
# stubs `thurbox-cli` and reads back what it was called with. The stubs are
# themselves assertions: the `gh` stub refuses every subcommand it was not
# taught, so an unexpected reach for `pr close` fails the run rather than
# passing quietly.

shep="$tmp/shepherd"
mkdir -p "$shep/bin" "$shep/gh" "$shep/sessions"
export SHEP="$shep"
: >"$shep/gh.log"
: >"$shep/tbx.log"

cat >"$shep/bin/gh" <<'SH'
#!/bin/sh
echo "$*" >>"$SHEP/gh.log"
if [ -n "${SHEP_GH_DOWN:-}" ]; then
	echo "gh: could not connect to github.com" >&2
	exit 1
fi
# Who has push access, which is what "opened by the repository owner" means
# once the owner is an organisation and the author is a person in it.
if [ "$1" = api ]; then
	login=$(printf '%s' "$2" | sed 's#/permission$##; s#.*/##')
	printf '{"permission":"%s"}\n' "$(cat "$SHEP/perms/$login" 2>/dev/null || echo none)"
	exit 0
fi
n=$(printf '%s' "$3" | sed 's#/*$##; s#.*/##')
case "$1 $2" in
"pr list")
	# The forge is the source of truth now, so this answers with every OPEN
	# pull request on the repo asked for — including ones no task records.
	repo=""
	while [ $# -gt 0 ]; do
		case "$1" in
		--repo) repo="$2" ;;
		esac
		shift
	done
	python3 -c '
import glob, json, sys
repo, where = sys.argv[1], sys.argv[2]
docs = []
for f in sorted(glob.glob(where + "/*.json")):
    d = json.load(open(f))
    if d.get("state") != "OPEN":
        continue
    if d["url"].split("/pull/")[0][len("https://github.com/"):] != repo:
        continue
    docs.append(d)
print(json.dumps(docs))
' "$repo" "$SHEP/gh"
	;;
"pr view")
	if [ ! -f "$SHEP/gh/$n.json" ]; then
		echo "gh: no pull request $n" >&2
		exit 1
	fi
	# `collect`'s pipeline check asks for one field with a jq filter; the
	# shepherd asks for the whole set. Answer whichever was asked for.
	case "$*" in
	*"-q .body") python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["body"])' \
		"$SHEP/gh/$n.json" ;;
	*) cat "$SHEP/gh/$n.json" ;;
	esac
	;;
"pr merge")
	echo "$n" >>"$SHEP/merged"
	echo "merged"
	;;
*)
	echo "gh: the shepherd is not allowed to run '$1 $2'" >&2
	exit 1
	;;
esac
SH

cat >"$shep/bin/thurbox-cli" <<'SH'
#!/bin/sh
echo "$*" >>"$SHEP/tbx.log"
case "$1 $2" in
"session get")
	if [ -f "$SHEP/sessions/$3.json" ]; then cat "$SHEP/sessions/$3.json"; else
		echo "no such session: $3" >&2
		exit 1
	fi
	;;
"session list")
	printf '['
	sep=""
	for f in "$SHEP"/sessions/*.json; do
		[ -e "$f" ] || continue
		printf '%s%s' "$sep" "$(cat "$f")"
		sep=","
	done
	printf ']'
	;;
"session create")
	n=$(cat "$SHEP/creates" 2>/dev/null || echo 0)
	n=$((n + 1))
	echo "$n" >"$SHEP/creates"
	id="f1xe4000-0000-0000-0000-00000000000$n"
	# Up and reporting, so session-trust.sh confirms with no keystroke.
	printf '{"id":"%s","state":"idle","agent":"claude","hook_reported":true}\n' \
		"$id" >"$SHEP/sessions/$id.json"
	printf '{"id":"%s","created":true}\n' "$id"
	;;
"session capture") echo '{"output":""}' ;;
esac
exit 0
SH
chmod +x "$shep/bin/gh" "$shep/bin/thurbox-cli"

# A real repository, because the fix for the branch trap is a real git
# worktree: `--worktree-branch` only ever CREATES a branch, and every branch
# with a pull request on it already exists.
srepo="$shep/repo"
mkdir -p "$srepo"
git -C "$srepo" init -q -b main
git -C "$srepo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base

# Push access, which is what "opened by the repository owner" means once the
# owner is an organisation and the author is a person inside it. `stranger` has
# no file here, so the stub answers `none` for them.
mkdir -p "$shep/perms"
echo admin >"$shep/perms/LeTuR"

stopic="$($QUEUE topic add shepherd-cases --title 'The PRs, after the work' \
	--prompt 'watch every open PR and dispatch a fixer when one goes bad' 2>/dev/null)"

for spec in 01:conflicting:101 02:green:102 03:skipped:103 04:elsewhere:104 \
	05:busy:105 06:unrun:106 07:gone:107 08:second:113; do
	IFS=: read -r n slug pr <<<"$spec"
	$QUEUE add "$stopic" "$slug" --title "A PR that is $slug" --repo "$srepo" \
		--branch "fix/$slug" --number "$n" >/dev/null
	owner=Thurbeen/fleet
	[ "$slug" = elsewhere ] && owner=someone-else/their-repo
	cat >"$FLEET_QUEUE_DIR/$stopic/$n-$slug/result.md" <<EOF
---
outcome: shipped
artifact: https://github.com/$owner/pull/$pr
---
Shipped it.
EOF
done

# The branches appear only NOW, which is the order the real thing happens in:
# `add` records a branch that does not exist yet and refuses one that does, and
# the worker's own spawn is what creates it. Every branch with a pull request
# on it is therefore already there by the time the shepherd looks.
for br in conflicting green skipped elsewhere busy unrun gone second prose-only; do
	git -C "$srepo" branch "fix/$br"
done

env PATH="$shep/bin:$base_path" $QUEUE collect >/dev/null

python3 - "$shep/gh" <<'PY'
import json
import sys

out = sys.argv[1]
green = {"__typename": "CheckRun", "name": "CI", "status": "COMPLETED", "conclusion": "SUCCESS"}

# Copied from a real no-mistakes body. The attestation is written DURING the
# `pr` step, so `pr` reads `running` and `ci` `pending` in every body that
# carries one; everything up to and including the push is `completed`.
STEPS = [
    {"step": s, "status": "completed"}
    for s in ("intent", "rebase", "review", "test", "document", "lint", "push")
] + [{"step": "pr", "status": "running"}, {"step": "ci", "status": "pending"}]


def attested(sha, steps=None):
    payload = json.dumps({"head_sha": sha, "steps": steps or STEPS})
    return f"<!-- no-mistakes-pipeline-attestation:v1 {payload} -->\n\nShipped it.\n"


def pr(n, owner="Thurbeen/fleet", sha=None, body=None, **kw):
    sha = sha or f"{n:040d}"
    doc = {
        "number": n, "state": "OPEN", "title": f"PR {n}", "isDraft": False,
        "url": f"https://github.com/{owner}/pull/{n}",
        "mergeable": "MERGEABLE", "reviewDecision": "", "statusCheckRollup": [green],
        "body": attested(sha) if body is None else body,
        "headRefName": "fix/x", "baseRefName": "main", "headRefOid": sha,
        "author": {"login": "LeTuR", "is_bot": False},
        "headRepositoryOwner": {"login": owner.split("/")[0]},
        "isCrossRepository": False,
    }
    doc.update(kw)
    json.dump(doc, open(f"{out}/{n}.json", "w"))


pr(101, mergeable="CONFLICTING", headRefName="fix/conflicting")
pr(102, headRefName="fix/green")
# Green in every way GitHub can see, and opened outside the pipeline.
pr(103, headRefName="fix/skipped", body="Fixed it.\n")
pr(104, owner="someone-else/their-repo", headRefName="fix/elsewhere")
# Green in the only sense GitHub can offer before its checks exist.
pr(106, headRefName="fix/unrun", statusCheckRollup=[])
pr(107, mergeable="CONFLICTING", headRefName="fix/gone")
pr(105, mergeable="CONFLICTING", headRefName="fix/busy")

# --- the four the forge knows about and the task records do not -------------

# 108: no task ever recorded it, and it is perfect. Discovery from the forge
# is the whole point: this one is invisible to a shepherd reading artifacts.
pr(108, headRefName="fix/nobody-sent-me")
# 109: a stranger's, from the fork this public repo has. Green, and attested
# with this PR's own head sha — everything a body can be made to say.
pr(109, headRefName="patch-1", author={"login": "stranger", "is_bot": False},
   headRepositoryOwner={"login": "stranger"}, isCrossRepository=True)
# 110: attested for the sha BEFORE the last push. The pipeline ran on code
# that is no longer what would be merged.
pr(110, headRefName="fix/stale", body=attested("f" * 40))
# 111: all five headings, which anyone can type, and no attestation at all.
pr(111, headRefName="fix/prose-only",
   body="Reviewed, tested, linted, and opened through the pipeline.\n")
# 112: the branch IS ours — anyone with read access can open a pull request
# between two branches that already exist, and the body would then be theirs.
pr(112, headRefName="fix/green", author={"login": "stranger", "is_bot": False})

# --- the #25 case: a SECOND pull request from a task whose artifact is the
# first one, already merged. Only the head branch connects 114 to that task.
pr(113, state="MERGED", headRefName="fix/second")
pr(114, mergeable="CONFLICTING", headRefName="fix/second")
PY

# 05's own worker is still mid-turn. `working` is the agent SAYING it is not at
# rest, and a shepherd that types into that pane interrupts a fix in progress.
busy=99999999-9999-9999-9999-999999999999
printf '{"id":"%s","state":"working","agent":"claude","hook_reported":true}\n' \
	"$busy" >"$shep/sessions/$busy.json"
$QUEUE attach "$stopic/05-busy" "$busy" >/dev/null

# 07's worker was cleaned up after the run, which is the ordinary case: the
# session id is still on the record and there is nothing behind it. A shepherd
# that reads "no such session" as "not busy, go ahead" types a brief at an id
# that answers nobody, and the fix never happens.
gone=88888888-8888-8888-8888-888888888888
$QUEUE attach "$stopic/07-gone" "$gone" >/dev/null

creates() { grep -c 'session create' "$shep/tbx.log" 2>/dev/null || true; }
merges() { grep -c 'pr merge' "$shep/gh.log" 2>/dev/null || true; }
count_is() {
	if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected $3, counted $2$nl$4"; fi
}

# --- read-only by default ----------------------------------------------------

out="$(env PATH="$shep/bin:$base_path" $QUEUE shepherd --topic "$stopic" --dry-run 2>&1)"
expect "a dry run names the conflicting PR" "pull/101" "$out"
expect "and says what it would dispatch" "would-dispatch" "$out"
expect "and why, in the pull request's own terms" "conflicts with main" "$out"
expect "a dry run also names what it would merge" "would-merge" "$out"
expect "and the command it would merge with" "--squash --delete-branch" "$out"
count_is "a dry run spawns nothing" "$(creates)" 0 "$(cat "$shep/tbx.log")"
count_is "a dry run merges nothing" "$(merges)" 0 "$(cat "$shep/gh.log")"

json="$(env PATH="$shep/bin:$base_path" $QUEUE shepherd --dry-run --json 2>&1)"
if printf '%s' "$json" | python3 -c 'import json,sys; json.load(sys.stdin)["prs"]' 2>/dev/null; then
	pass "--json is a clean seam for scripts/fleet-status.sh"
else
	fail "--json is a clean seam for scripts/fleet-status.sh" "$json"
fi

# --- 9a. a conflicting PR dispatches exactly one fixer -----------------------

out="$(env PATH="$shep/bin:$base_path" $QUEUE shepherd --topic "$stopic" 2>&1)"
# ONE per broken PR, not one per pass: 101 conflicts and 103 skipped the
# pipeline, so two fixers is right and two for either one of them is the bug.
count_is "a conflicting PR dispatches exactly one fixer" \
	"$(grep -c 'session create .*__01-conflicting' "$shep/tbx.log")" 1 \
	"$out$nl$(cat "$shep/tbx.log")"
count_is "one fixer per broken PR, and none for the ones that are not" \
	"$(creates)" 4 "$out$nl$(cat "$shep/tbx.log")"
expect "the fixer is prompted, not left on its trust dialog" "session send" \
	"$(cat "$shep/tbx.log")"

created="$(grep 'session create' "$shep/tbx.log")"
expect "the fixer is spawned on a checkout of the branch that already exists" \
	"--repo-path" "$created"
refute "and never asks thurbox to create a branch that is already there" \
	"--worktree-branch" "$created"
if git -C "$srepo" worktree list | grep -q 'fix/conflicting'; then
	pass "the existing branch is checked out as a worktree, not renamed aside"
else
	fail "the existing branch is checked out as a worktree, not renamed aside" \
		"$(git -C "$srepo" worktree list)"
fi

fixbrief="$(find "$FLEET_QUEUE_DIR/$stopic/01-conflicting" -name 'fix-*.md' | head -1)"
if [ -n "$fixbrief" ]; then
	fixbody="$(cat "$fixbrief")"
	expect "the fixer's brief states the condition, not just a PR number" \
		"conflicts with main" "$fixbody"
	expect "and names the pull request it must update" "pull/101" "$fixbody"
	expect "and says the fix lands on that PR in place" "in place" "$fixbody"
	expect "and forbids a second pull request" "Do not open a second" "$fixbody"
	expect "and forbids merging" "Do not merge it" "$fixbody"
else
	fail "the fixer gets a written brief of its own" "no fix-*.md under 01-conflicting"
fi

# --- 9b. a green PR in an allowlisted repo is merged -------------------------

expect "a green, pipeline-opened PR is merged" "merged" "$out"
if grep -qx 102 "$shep/merged" 2>/dev/null; then
	pass "and it is the green one that got merged"
else
	fail "and it is the green one that got merged" "$(cat "$shep/merged" 2>/dev/null)"
fi
expect "the merge is a squash, the only method the remote allows" \
	"pr merge https://github.com/Thurbeen/fleet/pull/102 --squash --delete-branch" \
	"$(cat "$shep/gh.log")"

# --- 9c. the two things that are never merged --------------------------------

if grep -qx 103 "$shep/merged" 2>/dev/null; then
	fail "a PR that skipped the pipeline is never merged, however green" \
		"$(cat "$shep/merged")"
else
	pass "a PR that skipped the pipeline is never merged, however green"
fi
expect "it gets a fixer's condition instead" "policy" "$out"

if grep -qx 106 "$shep/merged" 2>/dev/null; then
	fail "a PR whose checks have not reported is never merged" "$(cat "$shep/merged")"
else
	pass "a PR whose checks have not reported is never merged"
fi
expect "an empty check rollup is its own answer, not a pass" "no check has reported" "$out"

if grep -qx 104 "$shep/merged" 2>/dev/null; then
	fail "a PR outside the allowlisted repo is never merged" "$(cat "$shep/merged")"
else
	pass "a PR outside the allowlisted repo is never merged"
fi
expect "and is handed back to the operator by name" "fleet does not merge in" "$out"

# --- 9c2. every open PR, whether or not a task ever recorded it --------------
#
# The bug this closes: a task records ONE artifact, the first pull request its
# worker reported. #25 was a SECOND pull request from a task whose artifact
# still pointed at the already-merged #23, so a shepherd reading artifacts
# could not see it and the unattended pass would never have merged it.
# Discovery comes from the forge now, so a pull request nobody recorded is
# shepherded like any other.

expect "a pull request no task records is discovered from the forge" \
	"pull/108" "$out"
if grep -qx 108 "$shep/merged" 2>/dev/null; then
	pass "and an eligible unlinked pull request is merged like any other"
else
	fail "and an eligible unlinked pull request is merged like any other" \
		"$out$nl$(cat "$shep/merged" 2>/dev/null)"
fi
expect "and it is named as belonging to no task, not passed over in silence" \
	"no task" "$out"

# --- 9c3. a stranger's pull request is never merged and never fixed ----------
#
# Thurbeen/fleet is public and has a fork, and this command runs unattended on
# a timer. A body cannot authorise its own merge: the five `## ` headings are
# text anyone can paste, which is why they were never the gate they looked
# like. 109 is green, attested for its own head sha, and still not ours.

if grep -qx 109 "$shep/merged" 2>/dev/null; then
	fail "a pull request from a fork is never merged, however green" \
		"$(cat "$shep/merged")"
else
	pass "a pull request from a fork is never merged, however green"
fi
refute "and no agent is ever dispatched at a stranger's branch" \
	"patch-1" "$(cat "$shep/tbx.log")"
expect "and it is reported by name rather than skipped silently" "pull/109" "$out"

# --- 9c4. the attestation, and not the prose anyone can type -----------------

if grep -qx 110 "$shep/merged" 2>/dev/null; then
	fail "an attestation for an earlier head sha does not authorise this one" \
		"$(cat "$shep/merged")"
else
	pass "an attestation for an earlier head sha does not authorise this one"
fi
expect "and it says the attestation names another commit" "attestation" "$out"

if grep -qx 111 "$shep/merged" 2>/dev/null; then
	fail "a body that only says the pipeline ran never authorises a merge" \
		"$(cat "$shep/merged")"
else
	pass "a body that only says the pipeline ran never authorises a merge"
fi

# --- 9c4b. ours by branch, and still not opened by anyone who can push -------

if grep -qx 112 "$shep/merged" 2>/dev/null; then
	fail "a pull request opened by someone who cannot push here is not merged" \
		"$(cat "$shep/merged")"
else
	pass "a pull request opened by someone who cannot push here is not merged"
fi
expect "and it says whose access fell short" "stranger has no access" "$out"

# --- 9c5. a second pull request links back to its task by branch -------------

expect "a second PR on a task's branch is linked back to that task" \
	"08-second" "$out"
if grep -q 'session create .*__08-second' "$shep/tbx.log"; then
	pass "and its fixer works in that task's own branch checkout"
else
	fail "and its fixer works in that task's own branch checkout" \
		"$(cat "$shep/tbx.log")"
fi

# --- 9d. a worker still mid-turn is left alone -------------------------------

expect "a PR whose own worker is working is left alone, not interrupted" \
	"left-alone" "$out"
refute "and nothing was typed into that pane" "$busy" \
	"$(grep 'session send' "$shep/tbx.log")"

# --- 9d2. a session that is gone is not a session to send to -----------------

if grep -q "session send $gone" "$shep/tbx.log"; then
	fail "a brief is never typed at a session id that answers nobody" \
		"$(grep "$gone" "$shep/tbx.log")"
else
	pass "a brief is never typed at a session id that answers nobody"
fi
if grep -q 'session create .*__07-gone' "$shep/tbx.log"; then
	pass "and its PR gets a fresh session on the branch instead"
else
	fail "and its PR gets a fresh session on the branch instead" "$(cat "$shep/tbx.log")"
fi

# --- 9e. a second pass dispatches none ---------------------------------------

before="$(creates)"
out="$(env PATH="$shep/bin:$base_path" $QUEUE shepherd --topic "$stopic" 2>&1)"
count_is "a second pass over the same broken PR dispatches no second fixer" \
	"$(creates)" "$before" "$out$nl$(cat "$shep/tbx.log")"
expect "and says the fixer it already sent is still in flight" "in-flight" "$out"

# --- 9e2. a fixer that died without fixing anything gets replaced -----------
#
# The same "gone reads as no session" rule 9d2 checks for the task's own
# worker has to hold for the shepherd's own record too, or a fixer that
# crashes or gets cleaned up after dispatch stalls that PR's recovery forever.

fixer_101="$(python3 -c "
import yaml
doc = yaml.safe_load(open('$FLEET_QUEUE_DIR/$stopic/01-conflicting/task.yaml'))
print(doc['shepherd']['session'])
")"
rm -f "$shep/sessions/$fixer_101.json"
before="$(creates)"
out="$(env PATH="$shep/bin:$base_path" $QUEUE shepherd --topic "$stopic" 2>&1)"
count_is "a dead fixer's PR gets a fresh fixer, not silence" \
	"$(creates)" "$((before + 1))" "$out$nl$(cat "$shep/tbx.log")"
expect "and it is reported dispatched, not still in-flight" "dispatched:" "$out"
if grep -q 'session create .*__01-conflicting' "$shep/tbx.log"; then
	pass "and it is dispatched on the same branch as before"
else
	fail "and it is dispatched on the same branch as before" "$(cat "$shep/tbx.log")"
fi

# --- 9f. an unreachable gh does nothing at all -------------------------------
#
# The one failure that costs more than the bug it fixes: a shepherd that reads
# "could not check" as "broken" spawns fixers for healthy pull requests, and
# one that reads it as "fine" merges PRs it never looked at.

before="$(creates)"
beforem="$(merges)"
out="$(env PATH="$shep/bin:$base_path" SHEP_GH_DOWN=1 $QUEUE shepherd --topic "$stopic" 2>&1)"
count_is "an unreachable gh dispatches nothing" "$(creates)" "$before" "$out"
count_is "an unreachable gh merges nothing" "$(merges)" "$beforem" "$out"
expect "and says what it could not determine" "could not read the pull request" "$out"
refute "and never calls a PR it could not read ready" "would-merge" "$out"

# --- 9g. the shepherd only ever reads and merges -----------------------------

refute "the shepherd never closes a pull request" "pr close" "$(cat "$shep/gh.log")"
refute "and never edits one" "pr edit" "$(cat "$shep/gh.log")"

# --- 9h. a repo with more open PRs than gh's list can be trusted to return ---
#
# `gh pr list --limit N` is a request cap, not a page size: gh paginates the
# GraphQL calls itself to reach it, so reaching N genuinely means "there may be
# more". A repo that hits the limit exactly must be reported UNREADABLE, the
# same as one `gh` could not reach at all — an empty answer and a possibly-
# truncated one are not the same claim, and only one of them means "nothing is
# open". Reported here rather than silently merging or fixing whatever
# happened to fit in the first page.

many="$shep/bin-many"
mkdir -p "$many"
: >"$shep/many.log"
cat >"$many/gh" <<'SH'
#!/bin/sh
echo "$*" >>"$SHEP/many.log"
if [ "$1 $2" = "pr list" ]; then
	python3 -c '
import json
print(json.dumps([
    {"number": i, "state": "OPEN", "isDraft": False,
     "url": "https://github.com/many-owner/many-repo/pull/%d" % i,
     "mergeable": "MERGEABLE", "reviewDecision": "", "statusCheckRollup": [],
     "body": "", "headRefName": "branch-%d" % i, "baseRefName": "main",
     "headRefOid": "0" * 40, "author": {"login": "someone", "is_bot": False},
     "headRepositoryOwner": {"login": "many-owner"}, "isCrossRepository": False}
    for i in range(1000)
]))
'
	exit 0
fi
echo "gh: the many-repo stub is not allowed to run '$1 $2'" >&2
exit 1
SH
chmod +x "$many/gh"

trepo="$shep/repo-many"
mkdir -p "$trepo"
git -C "$trepo" init -q -b main
git -C "$trepo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base

mtopic="$($QUEUE topic add many-prs --title 'A repo at the pagination limit' \
	--prompt 'shepherd a repo with at least GH_PR_LIST_LIMIT open pull requests' 2>/dev/null)"
$QUEUE add "$mtopic" only --title only --repo "$trepo" --branch fix/only --number 1 >/dev/null
cat >"$FLEET_QUEUE_DIR/$mtopic/1-only/result.md" <<EOF
---
outcome: shipped
artifact: https://github.com/many-owner/many-repo/pull/9999
---
Shipped it.
EOF
env PATH="$many:$base_path" $QUEUE collect >/dev/null

out="$(env PATH="$many:$base_path" $QUEUE shepherd --topic "$mtopic" 2>&1)"
expect "a repo at gh's list limit is reported unreadable, not silently capped" \
	"could not read the pull requests on many-owner/many-repo" "$out"
expect "and says the result may be truncated" "may be truncated" "$out"
refute "and nothing from it is merged" "pr merge" "$(cat "$shep/many.log")"
refute "and it is not reported as having zero open pull requests either" \
	"no open pull requests" "$out"

# --- 9i. thurbox is on the allowlist, on the same gates as fleet -------------
#
# The allowlist grew, so the claim under test is that adding a repository adds
# a REPOSITORY and not a looser rule. Two pull requests on thurbox, both green
# and mergeable and both on branches that are ours: the attested one merges the
# way fleet's own do, and the one nothing vetted is still handed back.
#
# The third claim is the one this addition could quietly weaken. The allowlist
# is matched HOST-QUALIFIED, so `Thurbeen/thurbox` — the way a person writes it
# and the way the operator asked for it — is refused rather than matched
# against the bare slug. 13e proves that for a forge that is not GitHub; this
# proves it for the repo that was just added, where the bare slug is the
# plausible typo.

ttopic="$($QUEUE topic add thurbox-allowlist --title 'Auto-merge in thurbox' \
	--prompt 'thurbox merges on the same gates as fleet, and only host-qualified' 2>/dev/null)"
$QUEUE add "$ttopic" attested --title 'A thurbox PR the pipeline vetted' \
	--repo "$srepo" --branch tbx/attested --number 01 >/dev/null
cat >"$FLEET_QUEUE_DIR/$ttopic/01-attested/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/Thurbeen/thurbox/pull/201
---
Shipped it.
EOF
git -C "$srepo" branch tbx/attested

python3 - "$shep/gh" <<'PY'
import json
import sys

out = sys.argv[1]
green = {"__typename": "CheckRun", "name": "CI", "status": "COMPLETED",
         "conclusion": "SUCCESS"}
STEPS = [
    {"step": s, "status": "completed"}
    for s in ("intent", "rebase", "review", "test", "document", "lint", "push")
] + [{"step": "pr", "status": "running"}, {"step": "ci", "status": "pending"}]


def pr(n, branch, body):
    sha = f"{n:040d}"
    json.dump({
        "number": n, "state": "OPEN", "title": f"PR {n}", "isDraft": False,
        "url": f"https://github.com/Thurbeen/thurbox/pull/{n}",
        "mergeable": "MERGEABLE", "reviewDecision": "", "statusCheckRollup": [green],
        "body": body, "headRefName": branch, "baseRefName": "main",
        "headRefOid": sha, "author": {"login": "LeTuR", "is_bot": False},
        "headRepositoryOwner": {"login": "Thurbeen"}, "isCrossRepository": False,
    }, open(f"{out}/{n}.json", "w"))


payload = json.dumps({"head_sha": f"{201:040d}", "steps": STEPS})
pr(201, "tbx/attested",
   f"<!-- no-mistakes-pipeline-attestation:v1 {payload} -->\n\nShipped it.\n")
# Green in every way the forge can see, and nothing vetted the head that would
# land. No task records it either, so nothing here spawns a fixer.
pr(202, "tbx/unvetted", "Reviewed, tested, linted, and opened through the pipeline.\n")
PY

# The artifact reaches the record through `collect`, the same way every
# other task's does, and the shepherd derives the repository from it.
env PATH="$shep/bin:$base_path" $QUEUE collect >/dev/null

out="$(env PATH="$shep/bin:$base_path" $QUEUE shepherd --topic "$ttopic" 2>&1)"
expect "the shepherd reaches thurbox at all" "Thurbeen/thurbox" "$out"
if grep -qx 201 "$shep/merged" 2>/dev/null; then
	pass "an attested, green thurbox pull request is merged unattended"
else
	fail "an attested, green thurbox pull request is merged unattended" \
		"$out$nl$(cat "$shep/merged" 2>/dev/null)"
fi
expect "and by the same squash fleet's own are merged by" \
	"pr merge https://github.com/Thurbeen/thurbox/pull/201 --squash --delete-branch" \
	"$(cat "$shep/gh.log")"

if grep -qx 202 "$shep/merged" 2>/dev/null; then
	fail "joining the allowlist loosens no gate: an unattested one is not merged" \
		"$(cat "$shep/merged")"
else
	pass "joining the allowlist loosens no gate: an unattested one is not merged"
fi
expect "and it is named for what it lacks, not passed over" \
	"the body carries no no-mistakes attestation" "$out"

# The typo the operator's own words invite: the allowlist is host-qualified,
# and `Thurbeen/thurbox` names no forge.
out="$(env PATH="$shep/bin:$base_path" FLEET_AUTO_MERGE_REPOS="Thurbeen/thurbox" \
	$QUEUE shepherd --topic "$ttopic" --dry-run 2>&1)"
expect "a bare Thurbeen/thurbox is refused, not matched against the slug" \
	"must name its forge" "$out"
refute "and nothing in thurbox would be merged under it" "would-merge" "$out"

# --- 9j. mazet is on the allowlist, and the whole set names its forge --------
#
# The allowlist grew a second time, and this entry is the first under an owner
# no other entry shares. The two claims 9i makes about thurbox are made again
# here about `github.com/LeTuR/mazet`, because they are claims about an ENTRY
# and not about the code once and for all: the gates travel with it — an
# attested one merges, an unvetted one is still handed back — and it is matched
# HOST-QUALIFIED, so `LeTuR/mazet` is refused rather than matched against the
# bare slug.
#
# The third claim is the one only the whole set can make, and it is the one
# nothing above would catch. `auto_merge_repos()` parses what the ENVIRONMENT
# overrides it with; the literal in `queue.py` is never parsed, so a bare slug
# written there would match nothing, refuse nothing, and fail no test here.
# So the set itself is read and every entry put through the same parse.

maztopic="$($QUEUE topic add mazet-allowlist --title 'Auto-merge in mazet' \
	--prompt 'mazet merges on the same gates as fleet, and only host-qualified' 2>/dev/null)"
$QUEUE add "$maztopic" attested --title 'A mazet PR the pipeline vetted' \
	--repo "$srepo" --branch mzt/attested --number 01 >/dev/null
cat >"$FLEET_QUEUE_DIR/$maztopic/01-attested/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/LeTuR/mazet/pull/301
---
Shipped it.
EOF
git -C "$srepo" branch mzt/attested

python3 - "$shep/gh" <<'PY'
import json
import sys

out = sys.argv[1]
green = {"__typename": "CheckRun", "name": "CI", "status": "COMPLETED",
         "conclusion": "SUCCESS"}
STEPS = [
    {"step": s, "status": "completed"}
    for s in ("intent", "rebase", "review", "test", "document", "lint", "push")
] + [{"step": "pr", "status": "running"}, {"step": "ci", "status": "pending"}]


def pr(n, branch, body):
    sha = f"{n:040d}"
    json.dump({
        "number": n, "state": "OPEN", "title": f"PR {n}", "isDraft": False,
        "url": f"https://github.com/LeTuR/mazet/pull/{n}",
        "mergeable": "MERGEABLE", "reviewDecision": "", "statusCheckRollup": [green],
        "body": body, "headRefName": branch, "baseRefName": "main",
        "headRefOid": sha, "author": {"login": "LeTuR", "is_bot": False},
        "headRepositoryOwner": {"login": "LeTuR"}, "isCrossRepository": False,
    }, open(f"{out}/{n}.json", "w"))


payload = json.dumps({"head_sha": f"{301:040d}", "steps": STEPS})
pr(301, "mzt/attested",
   f"<!-- no-mistakes-pipeline-attestation:v1 {payload} -->\n\nShipped it.\n")
# Green in every way the forge can see, and nothing vetted the head that would
# land. No task records it either, so nothing here spawns a fixer.
pr(302, "mzt/unvetted", "Reviewed, tested, linted, and opened through the pipeline.\n")
PY

env PATH="$shep/bin:$base_path" $QUEUE collect >/dev/null

out="$(env PATH="$shep/bin:$base_path" $QUEUE shepherd --topic "$maztopic" 2>&1)"
expect "the shepherd reaches mazet at all" "LeTuR/mazet" "$out"
if grep -qx 301 "$shep/merged" 2>/dev/null; then
	pass "an attested, green mazet pull request is merged unattended"
else
	fail "an attested, green mazet pull request is merged unattended" \
		"$out$nl$(cat "$shep/merged" 2>/dev/null)"
fi
expect "and by the same squash fleet's own are merged by" \
	"pr merge https://github.com/LeTuR/mazet/pull/301 --squash --delete-branch" \
	"$(cat "$shep/gh.log")"

if grep -qx 302 "$shep/merged" 2>/dev/null; then
	fail "the second addition loosens no gate either: an unattested one is not merged" \
		"$(cat "$shep/merged")"
else
	pass "the second addition loosens no gate either: an unattested one is not merged"
fi

# The typo the operator's own words invite, for the new entry as for the last.
out="$(env PATH="$shep/bin:$base_path" FLEET_AUTO_MERGE_REPOS="LeTuR/mazet" \
	$QUEUE shepherd --topic "$maztopic" --dry-run 2>&1)"
expect "a bare LeTuR/mazet is refused, not matched against the slug" \
	"must name its forge" "$out"
refute "and nothing in mazet would be merged under it" "would-merge" "$out"

# The set itself: four repositories, every one of them host-qualified.
allowlist="$(python3 - <<'PY'
import sys

sys.path.insert(0, "scripts/lib")
import forge
import queue as q

repos = sorted(q.auto_merge_repos())
print("entries=" + " ".join(repos))
print("unqualified=" + (" ".join(r for r in repos if forge.RepoId.parse(r) is None) or "none"))
PY
)"
expect "the allowlist is the four repositories fleet may merge in" \
	"entries=github.com/LeTuR/mazet github.com/Thurbeen/fleet github.com/Thurbeen/thurbox github.com/Thurbeen/thurview" \
	"$allowlist"
expect "and every entry in it names its forge, so none can match a bare slug" \
	"unqualified=none" "$allowlist"

# --- 10. the shepherd writes down the publish state it already saw -----------
#
# Every fact below arrived in the ONE `gh pr list` the pass already makes, and
# until now the pass threw all of it away between runs: the record said
# `shipped` and nothing else, so "are its checks still running or did they fail
# an hour ago" could only be answered by running the command again and reading
# the terminal. A real pass now stamps `publish.state` on every task it linked.
#
# Four claims, and the first is the one this whole topic turns on:
#
#   a `pr` task whose PR is green, mergeable and ours is recorded `green`,
#     gets no fixer, and is NOT merged — the forge is happy and NOTHING
#     vetted the head that would land, which is a different sentence
#   a `no-mistakes` task whose PR carries no attestation is still recorded
#     `unattested` and still gets the `policy` fixer
#   a dry run writes none of it
#   the landing sweep says merged/closed in that same block

$QUEUE add "$stopic" plain-pr --title 'A PR opened by whatever this repo uses' \
	--repo "$srepo" --branch fix/plain-pr --number 09 --publish pr \
	--how 'run the release script this repo already has' >/dev/null
# A real branch, so "no fixer was sent" means fleet chose not to send one and
# not that a fixer tried and fell over on a branch that was never there.
git -C "$srepo" branch fix/plain-pr

# Green in every way GitHub can see, and never claiming to be a pipeline:
# 102's own fixture with another branch and a body nobody attested.
python3 - "$shep/gh" <<'PY'
import json
import sys

out = sys.argv[1]
doc = json.load(open(f"{out}/102.json"))
doc.update({
    "number": 115,
    "url": "https://github.com/Thurbeen/fleet/pull/115",
    "headRefName": "fix/plain-pr",
    "headRefOid": f"{115:040d}",
    "body": "Opened with this repo's own release script.\n",
})
json.dump(doc, open(f"{out}/115.json", "w"))
PY

# (a) A dry run looks and tells you; it does not write.

out="$(env PATH="$shep/bin:$base_path" $QUEUE shepherd --topic "$stopic" --dry-run 2>&1)"
expect "a dry run classifies the pr-method pull request too" "pull/115" "$out"
state="$($QUEUE show "$stopic/09-plain-pr" 2>&1)"
refute "and records nothing — a dry run changes nothing, records included" \
	"published:" "$state"

# (b) The real pass, and the word it must not use.

out="$(env PATH="$shep/bin:$base_path" $QUEUE shepherd --topic "$stopic" 2>&1)"
row="$(printf '%s' "$out" | grep -A 2 'pull/115')"
state="$($QUEUE show "$stopic/09-plain-pr" 2>&1)"
expect "a real pass writes down what it saw" "published:   green" "$state"
expect "and stamps the command that looked" "(shepherd," "$state"
refute "a green pr-method PR is never recorded ready — nothing vetted its head" \
	"published:   ready" "$state"
if grep -qx 115 "$shep/merged" 2>/dev/null; then
	fail "and fleet never merges it, however green" "$(cat "$shep/merged")"
else
	pass "and fleet never merges it, however green"
fi
expect "it is handed to the operator with the reason" "not attested" "$row"
refute "and it is never called a policy breach — nobody asked it for an attestation" \
	"policy:" "$row"
count_is "and no fixer goes out for it, on a branch a fixer could have had" \
	"$(grep -c 'session create .*__09-plain-pr' "$shep/tbx.log")" 0 \
	"$out$nl$(cat "$shep/tbx.log")"

# (c) The `no-mistakes` half of the same gate, unchanged: 03-skipped declared
#     the pipeline and opened its pull request by hand.

state="$($QUEUE show "$stopic/03-skipped" 2>&1)"
expect "a no-mistakes PR with no attestation is recorded unattested" \
	"published:   unattested" "$state"
if grep -q 'session create .*__03-skipped' "$shep/tbx.log"; then
	pass "and still gets the policy fixer it always got"
else
	fail "and still gets the policy fixer it always got" "$(cat "$shep/tbx.log")"
fi

# (d) The other producer: the landing sweep. Its own queue, because `reap`
#     acts on every `done` task there is and the sections above are mid-flight.

lq() { env FLEET_QUEUE_DIR="$tmp/queue-landings" $QUEUE "$@"; }

lq topic add landings --title 'What the landing sweep writes down' \
	--prompt 'the publish block must say what the sweep learned' >/dev/null
lq add landings merged-pr --title 'A pull request that merged' \
	--repo /tmp/repo-a --branch fix/merged-pr --publish pr >/dev/null
lq add landings closed-pr --title 'A pull request that was closed unmerged' \
	--repo /tmp/repo-a --branch fix/closed-pr --publish pr >/dev/null
plain_pr 1020 fix/merged-pr
plain_pr 1021 fix/closed-pr
for spec in 01-merged-pr:1020 02-closed-pr:1021; do
	IFS=: read -r dir n <<<"$spec"
	cat >"$tmp/queue-landings/landings/$dir/result.md" <<EOF
---
outcome: shipped
artifact: https://github.com/acme/app/pull/$n
---
Opened it.
EOF
done
lq collect --no-reap >/dev/null
expect "an open pull request is recorded open first" "published:   open" \
	"$(lq show landings/01-merged-pr 2>&1)"

echo MERGED >"$states/1020.state"
echo CLOSED >"$states/1021.state"
lq reap >/dev/null 2>&1
expect "and the landing sweep overwrites it with the merge" \
	"published:   merged" "$(lq show landings/01-merged-pr 2>&1)"
expect "a pull request closed unmerged says so in the same place" \
	"published:   closed" "$(lq show landings/02-closed-pr 2>&1)"

# The worktrees the fixers got are real; take them back off the test repo so
# the temp directory can be removed without leaving stale registrations.
for slug in 01-conflicting 03-skipped 07-gone 08-second 09-plain-pr; do
	git -C "$srepo" worktree remove --force \
		"$FLEET_QUEUE_DIR/.worktrees/${stopic}__${slug}" 2>/dev/null
done

# --- 11. a task can name a HOST, and a local task does not change ------------
#
# The two halves that matter, in that order. A remote worker's filesystem is
# not this one, so the brief has to reach it and the result has to come back —
# and none of that may show up in the path a task with no host takes. The `ssh`
# stub above is a real fake host: what the push writes, the fetch reads.
#
# In a queue of its own, because `dispatch` acts on every ready task in the
# whole queue and the sections above deliberately leave some of theirs
# unwritten. Same stubs, same PATH — only the records are separate.

export FLEET_QUEUE_DIR="$tmp/queue-remote"

rtopic="$($QUEUE topic add run-somewhere-else \
	--title 'Run a task on another machine' \
	--prompt 'fleet should be able to spawn a worker on a remote thurbox host' \
	2>/dev/null)"

# (a) The refusals that cost nothing, all at `add` time and none of them
#     touching a host: a name thurbox does not know, a Windows host, and a
#     host whose session sharing is off — which is the trust dialog, decided.
for spec in \
	"nosuch:no host named" \
	"winbox:POSIX hosts only" \
	"lonebox:share_sessions = false"; do
	IFS=: read -r hname want <<<"$spec"
	if out="$($QUEUE add "$rtopic" "reject-$hname" --title "Reject $hname" \
		--repo /srv/code/app --host "$hname" --branch fix/reject 2>&1)"; then
		fail "--host $hname is refused at add time" "$out"
	else
		expect "--host $hname is refused at add time" "$want" "$out"
	fi
done
expect "and the refusal for an unknown host names the ones that exist" \
	"devbox" "$($QUEUE add "$rtopic" x --title x --repo /srv/code/app \
		--host nosuch --branch fix/x 2>&1)"

# (b) A local task, dispatched exactly as before: no --host anywhere near the
#     spawn, no ssh in the plan, and a brief named by ITS OWN absolute path on
#     this machine. This is the half that must not have changed.
$QUEUE add "$rtopic" stay-local --title 'Stay local' --repo /tmp/repo-a \
	--branch fix/stay-local --number 20 >/dev/null
localbrief="$(cat "$FLEET_QUEUE_DIR/$rtopic/20-stay-local/BRIEF.md")"
expect "a local brief still names the queue's own result.md, absolutely" \
	"$FLEET_QUEUE_DIR/$rtopic/20-stay-local/result.md" "$localbrief"
refute "and says nothing about a host" "on host" "$localbrief"
refute "and never tells the worker to write beside its brief" \
	"in the root of this worktree" "$localbrief"
printf 'Do the local thing.\n' >"$FLEET_QUEUE_DIR/$rtopic/20-stay-local/BRIEF.md"

out="$($QUEUE dispatch --dry-run 2>&1)"
refute "a task with no host spawns with no --host" "--host" "$out"
refute "and its dispatch plan mentions no ssh at all" "ssh <host>" "$out"
expect "and it is pointed at its brief here, by absolute path" \
	"$FLEET_QUEUE_DIR/$rtopic/20-stay-local/BRIEF.md" "$out"

# (c) A remote task's brief is the same document, but every control-plane path
#     it would otherwise name — the result, the prompt, the standing policy —
#     is not on that filesystem, so each is stated relative to the brief itself,
#     the one path a remote worker can always resolve.
$QUEUE add "$rtopic" build-on-devbox --title 'Build it on devbox' \
	--repo /srv/code/app --host devbox --branch fix/build-on-devbox \
	--number 22 >/dev/null
rbrief="$(cat "$FLEET_QUEUE_DIR/$rtopic/22-build-on-devbox/BRIEF.md")"
expect "a remote brief says which host the repo is on" "on host \`devbox\`" "$rbrief"
expect "and points the result at the worktree it is sitting in" \
	"in the root of this worktree" "$rbrief"
refute "and never at a control-plane path the worker cannot reach" \
	"$FLEET_QUEUE_DIR/$rtopic/22-build-on-devbox/result.md" "$rbrief"
expect "and points the prompt at a sibling of itself, not a control-plane path" \
	"PROMPT.md\`, alongside this file" "$rbrief"
refute "and never at the control plane's own PROMPT.md path" \
	"$FLEET_QUEUE_DIR/$rtopic/PROMPT.md" "$rbrief"
expect "and points the standing policy at a sibling of itself too" \
	"POLICY.md\`, alongside this file" "$rbrief"
refute "and never at the control plane's own POLICY.md path" \
	"$PWD/orchestration/queue/POLICY.md" "$rbrief"
expect "and says to delete every copy before committing" \
	"Delete \`BRIEF.md\`, \`POLICY.md\`, and \`PROMPT.md\` before you commit" "$rbrief"

expect "\`show\` says where a task runs" "host:        devbox" \
	"$($QUEUE show "$rtopic/22-build-on-devbox")"
expect "\`list\` spells a remote repo host-first, so it cannot read as local" \
	"devbox:/srv/code/app" "$($QUEUE list --topic "$rtopic")"
expect "\`plan\` does too" "devbox:/srv/code/app" "$($QUEUE plan)"

printf 'Build the thing on devbox.\n' \
	>"$FLEET_QUEUE_DIR/$rtopic/22-build-on-devbox/BRIEF.md"

# (d) Every probe runs before anything is spawned, and one failure stops that
#     task where it stands. A remote worker that starts and then fails at its
#     first `git` call looks like an agent bug and is not one.
for spec in \
	"down:reachable:No route to host" \
	"nonposix:posix shell:POSIX" \
	"noforge:forge:credentials of its own" \
	"norepo:repo:not on the machine"; do
	IFS=: read -r flag probe want <<<"$spec"
	: >"$sshstate/me@devbox.$flag"
	out="$($QUEUE dispatch 2>&1)"
	expect "a host that fails the \`$probe\` probe is not spawned" \
		"NOT SPAWNED" "$out"
	expect "and the failing probe is named: $probe" "$probe" "$out"
	rm -f "$sshstate/me@devbox.$flag"
	state="$($QUEUE show "$rtopic/22-build-on-devbox" | grep -F 'state:')"
	expect "and the task is left queued, so fixing the host and re-running sends it" \
		"queued" "$state"
done
expect "the repo probe says --repo is a path on the host, not here" \
	"nothing local validates it" \
	"$(: >"$sshstate/me@devbox.norepo"
	$QUEUE dispatch 2>&1
	rm -f "$sshstate/me@devbox.norepo")"

# (e) Probes pass: the session is spawned WITH --host, and the brief is put on
#     the host before the worker is told to read it.
rsession=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa
worktree=/home/me/.local/share/thurbox/worktrees/app-1234/fix-build-on-devbox
printf '{"id":"%s","created":true}\n' "$rsession" >"$tmp/next-session.json"
cat >"$sessions/$rsession.json" <<EOF
{"id":"$rsession","name":"Build it on devbox","state":"working",
 "agent":"claude","hook_reported":true,
 "worktrees":[{"repo_path":"/srv/code/app","worktree_path":"$worktree",
               "branch":"fix/build-on-devbox"}]}
EOF

out="$($QUEUE dispatch 2>&1)"
expect "every probe passing is reported, not just the failures" "probe ok" "$out"
expect "and the brief is copied to the host" "brief copied to me@devbox" "$out"

if [ -f "$remotes/me@devbox$worktree/BRIEF.md" ]; then
	pass "the brief really is on the host's filesystem, not only claimed to be"
else
	fail "the brief really is on the host's filesystem" \
		"nothing at $remotes/me@devbox$worktree/BRIEF.md"
fi
expect "and it is the brief the lead wrote" "Build the thing on devbox" \
	"$(cat "$remotes/me@devbox$worktree/BRIEF.md" 2>/dev/null)"
if [ -f "$remotes/me@devbox$worktree/POLICY.md" ]; then
	pass "the standing policy is copied to the host too, not only claimed to be"
else
	fail "the standing policy is copied to the host" \
		"nothing at $remotes/me@devbox$worktree/POLICY.md"
fi
expect "and it is the same policy the checkout ships" \
	"$(cat "$PWD/orchestration/queue/POLICY.md")" \
	"$(cat "$remotes/me@devbox$worktree/POLICY.md" 2>/dev/null)"
if [ -f "$remotes/me@devbox$worktree/PROMPT.md" ]; then
	pass "the topic's prompt is copied to the host too, not only claimed to be"
else
	fail "the topic's prompt is copied to the host" \
		"nothing at $remotes/me@devbox$worktree/PROMPT.md"
fi
expect "and it is this topic's own prompt" \
	"$(cat "$FLEET_QUEUE_DIR/$rtopic/PROMPT.md")" \
	"$(cat "$remotes/me@devbox$worktree/PROMPT.md" 2>/dev/null)"
expect "the record keeps the host-side worktree, so collect knows where to look" \
	"me@devbox:$worktree" "$($QUEUE show "$rtopic/22-build-on-devbox")"

# (f) The completion model is the same one: the worker writes a file, and
#     `collect` reads a file. ssh is only how it gets here.
mkdir -p "$remotes/me@devbox$worktree"
cat >"$remotes/me@devbox$worktree/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/remote-owner/app/pull/4242
---
Built it on devbox and opened the pull request.
EOF
pipeline_pr 4242 fix/build-on-devbox
session_is "$rsession" idle
cat >"$sessions/$rsession.json" <<EOF
{"id":"$rsession","name":"Build it on devbox","state":"idle","agent":"claude"}
EOF

out="$($QUEUE collect 2>&1)"
expect "collect fetches a remote worker's result over ssh" \
	"result fetched from me@devbox" "$out"
expect "and closes the task on it, exactly as it would locally" "shipped" "$out"
expect "and the publish check ran on it like any other" \
	"[publish verified: no-mistakes]" "$out"
if [ -f "$FLEET_QUEUE_DIR/$rtopic/22-build-on-devbox/result.md" ]; then
	pass "the fetched result lands in the queue, where it outlives the host"
else
	fail "the fetched result lands in the queue" "no local result.md"
fi

# (g) An unreachable remote session is never reaped. thurbox's CLI never says
#     `unreachable` — a down host leaves the LATCHED state standing, so this
#     session still reads `idle`, and `idle` is reapable. The host is asked
#     first, and that is the only thing between a temporary outage and a
#     deleted worktree.
echo MERGED >"$states/4242.state"
: >"$sshstate/me@devbox.down"
out="$($QUEUE reap 2>&1)"
expect "a session on an unreachable host is kept" "unreachable: host devbox" "$out"
refute "and nothing is deleted for it" "$rsession" "$(cat "$deletions")"
expect "thurbox still calls that session idle, which is the whole trap" \
	'"state":"idle"' "$(cat "$sessions/$rsession.json")"

rm -f "$sshstate/me@devbox.down"
out="$($QUEUE reap 2>&1)"
expect "and once the host answers again, the merged task's session is released" \
	"reaped" "$out"
expect "with --force, on the host thurbox owns" "$rsession" "$(cat "$deletions")"

# --- 12. refuel: the account window first, then the session that ran dry -----
#
# A worker that hits its agent's token limit does not fail — it sits. thurbox
# goes on reporting the last state its hook saw, so the session reads `working`
# forever and neither `watch` nor `collect` nor `reap` ever touches it.
#
# The claims, and the first one is the one that costs real money to get wrong:
#
#   the ACCOUNT window outranks every per-session reading. It is the
#     operator's subscription, shared by the lead and every worker, so while it
#     is spent a restart resumes, hits the same wall and burns the reset.
#   a stale `working` on its own is a SLOW worker, and is never restarted
#   a session whose pane carries the agent's own limit banner IS restarted, and
#     the handoff is the dispatch path's: trust the pane, then send the brief
#   the transcript, keyed by `agent_session_id`, is the precise source — it
#     names the reset time the pane only implies
#   the lead is refused by name
#   the cap holds, so a session that keeps running dry is visible rather than
#     restarted in a loop
#   no quota-axi is `undetermined`: never a pass, never a failure, and nothing
#     restarted on a guess
#   nothing here ever writes `state` or `outcome` — `collect` stays the only
#     thing that closes a task

export FLEET_QUEUE_DIR="$tmp/queue-refuel"
: >"$restarts"
: >"$sends"

ftopic="$($QUEUE topic add ran-dry --title 'Workers that ran dry' \
	--prompt 'restart the sessions that hit the token limit')"

# `f1` sits on a limit banner, `f2` is merely slow, `f3` never went stale.
fuelled() {
	$QUEUE add "$ftopic" "$1" --title "Task $1" --repo /tmp/repo-a \
		--branch "fix/$1" --number "$2" >/dev/null
	$QUEUE attach "$ftopic/$2-$1" "$3" >/dev/null
}
fuelled ran-dry 01 aaaaaaa1-0000-0000-0000-000000000001
fuelled just-slow 02 aaaaaaa2-0000-0000-0000-000000000002
fuelled busy 03 aaaaaaa3-0000-0000-0000-000000000003

# Observed on a real pane, 2026-09-08: Claude Code prints its limit as one
# line in the transcript view and then stops. Nothing else in `session get`
# changes when it does.
cat >"$panes/aaaaaaa1-0000-0000-0000-000000000001.txt" <<'EOF'
● Now I will run the gate.

You've hit your session limit · resets 11:30pm (Europe/Paris)
/upgrade to increase your usage limit
EOF

# All three have been `working` for two hours by the hook's own clock; only the
# first has anything on its pane to say why.
session_is aaaaaaa1-0000-0000-0000-000000000001 working 7200
session_is aaaaaaa2-0000-0000-0000-000000000002 working 7200
session_is aaaaaaa3-0000-0000-0000-000000000003 working 90

# (a) The account window is spent. Every session is stuck for the same reason
#     and none of them is restarted — including the one with the banner.
quota_is 0 "2026-09-09T02:10:00+00:00"
out="$($QUEUE refuel 2>&1)"
expect "a spent account window stops the whole sweep" "account" "$out"
expect "and says when it comes back, from quota-axi's own resetsAt" \
	"2026-09-09T02:10:00+00:00" "$out"
expect "and says the fleet waits on the window, not on any session" \
	"waiting on the window" "$out"
refute "so nothing is restarted while the fuel is gone" \
	"aaaaaaa1" "$(cat "$restarts")"

# (b) The account has fuel again. Now — and only now — a wedged session is a
#     session a restart can actually recover.
quota_is 62 "2026-09-09T02:10:00+00:00"

out="$($QUEUE refuel --dry-run 2>&1)"
expect "a dry run names what it would restart" "would restart" "$out"
expect "and names the session that carries the banner" "01-ran-dry" "$out"
refute "and restarts nothing" "aaaaaaa1" "$(cat "$restarts")"
refute "and writes nothing" "refuels" \
	"$(cat "$FLEET_QUEUE_DIR/$ftopic/01-ran-dry/task.yaml")"

out="$($QUEUE refuel 2>&1)"
expect "a session whose pane carries the limit banner is restarted" \
	"restarted" "$out"
expect "and it is the one that ran dry" \
	"aaaaaaa1-0000-0000-0000-000000000001" "$(cat "$restarts")"
expect "a stale working state on its own is a SLOW worker, not a dry one" \
	"02-just-slow" "$out"
refute "so it is left alone" "aaaaaaa2" "$(cat "$restarts")"
refute "and a session still reporting fresh is never a candidate" \
	"aaaaaaa3" "$(cat "$restarts")"

# The handoff is dispatch's, in dispatch's order: the trust dialog is answered
# before anything is typed, and what is typed is the brief's own absolute path.
expect "the restarted worker is pointed back at its own BRIEF.md" \
	"$FLEET_QUEUE_DIR/$ftopic/01-ran-dry/BRIEF.md" "$(cat "$sends")"
expect "and told to continue where it stopped" "continue" "$(cat "$sends")"

state="$($QUEUE show "$ftopic/01-ran-dry" 2>&1)"
expect "the restart is recorded on the task" "refuelled:" "$state"
expect "and the task is still exactly as dispatched — a restart is not a
        completion" "state:       dispatched" "$state"
refute "and no outcome was invented for it" "outcome:     shipped" "$state"

# (c) The transcript is the precise source. `agent_session_id` names it, and it
#     carries the reset time the rendered pane only implies.
proj="$tmp/claude-config/projects/-tmp-repo-a"
mkdir -p "$proj"
export CLAUDE_CONFIG_DIR="$tmp/claude-config"
fuelled from-transcript 04 aaaaaaa4-0000-0000-0000-000000000004
session_is aaaaaaa4-0000-0000-0000-000000000004 working 7200
python3 - "$proj/agent-aaaaaaa4-0000-0000-0000-000000000004.jsonl" <<'PY'
import json
import sys

# The shape Claude Code actually writes when the window rejects a request,
# copied from a transcript in ~/.claude/projects: a synthetic assistant turn
# carrying `error`, `apiErrorStatus` and the quota window that rejected it.
rows = [
    {"type": "user", "timestamp": "2026-09-08T18:00:00.000Z",
     "message": {"role": "user", "content": "Read /brief and do what it says."}},
    {"type": "assistant", "timestamp": "2026-09-08T19:56:38.987Z",
     "isApiErrorMessage": True, "error": "rate_limit", "apiErrorStatus": 429,
     "quotaLimits": {"status": "rejected", "resetsAt": 1788999000,
                     "rateLimitType": "five_hour"},
     "message": {"role": "assistant", "model": "<synthetic>", "content": [
         {"type": "text", "text": "You've hit your session limit · resets 11:30pm (Europe/Paris)"}]}},
    {"type": "last-prompt"},
]
with open(sys.argv[1], "w") as fh:
    for row in rows:
        fh.write(json.dumps(row) + "\n")
PY

out="$($QUEUE refuel "$ftopic/04-from-transcript" --dry-run 2>&1)"
expect "the agent's own transcript is read, keyed by agent_session_id" \
	"transcript" "$out"
expect "and it is the source that names the window that rejected the turn" \
	"five_hour" "$out"
expect "so that session would be restarted too" "would restart" "$out"

# A single ref narrows the sweep; the default is every recorded session.
refute "and a ref narrows the sweep to that one task" "01-ran-dry" "$out"

# (d) The lead's own session is not a worker. A restart of it kills the
#     operator's conversation, so it is refused by name — the same guard reap
#     already makes.
out="$(THURBOX_SESSION=aaaaaaa1-0000-0000-0000-000000000001 \
	$QUEUE refuel --dry-run 2>&1)"
expect "the lead's own session is refused by name" "lead" "$out"
refute "and is never named as something to restart" "would restart  aaaaaaa1" "$out"

# (e) The cap. A session that runs dry AGAIN after a restart is restarted
#     again; one that keeps running dry becomes visible instead of looping.
#
#     The receipts are rewound between passes because a restart seconds ago is
#     deliberately not a second wedge: a `working` state reported BEFORE the
#     last restart is evidence from before it, and refuel waits rather than
#     spending the cap on one wedge. Three hours back is a session that came up,
#     worked, and ran dry all over again.
rewind_refuels() {
	python3 -c '
import datetime
import sys

import yaml

path = sys.argv[1]
doc = yaml.safe_load(open(path))
for rec in doc.get("refuels") or []:
    rec["at"] = (
        datetime.datetime.fromisoformat(rec["at"]) - datetime.timedelta(hours=3)
    ).isoformat()
yaml.safe_dump(doc, open(path, "w"))
' "$1"
}

for _ in 1 2 3 4; do
	$QUEUE refuel >/dev/null 2>&1
	rewind_refuels "$FLEET_QUEUE_DIR/$ftopic/01-ran-dry/task.yaml"
done
out="$($QUEUE refuel 2>&1)"
expect "a session that keeps running dry stops being restarted" \
	"the cap is 3" "$out"
expect "and the record says how many times it has run dry" "ran dry again" "$out"
capped="$(grep -c aaaaaaa1-0000-0000-0000-000000000001 "$restarts")"
if [ "$capped" -eq 3 ]; then
	pass "so it was restarted exactly 3 times across five passes, and no more"
else
	fail "so it was restarted exactly 3 times across five passes, and no more" \
		"restarted $capped times"
fi

# And a restart that just happened is not a second wedge: the same session,
# asked again with its receipt where refuel wrote it, is left to come back up.
$QUEUE show "$ftopic/01-ran-dry" >/dev/null
python3 -c '
import datetime
import sys

import yaml

path = sys.argv[1]
doc = yaml.safe_load(open(path))
# One receipt, stamped where refuel itself would have stamped it: just now.
doc["refuels"] = doc["refuels"][:1]
doc["refuels"][0]["at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
yaml.safe_dump(doc, open(path, "w"))
' "$FLEET_QUEUE_DIR/$ftopic/01-ran-dry/task.yaml"
before="$(wc -l <"$restarts")"
out="$($QUEUE refuel 2>&1)"
expect "a session restarted moments ago is given a moment" "give it a moment" "$out"
if [ "$(wc -l <"$restarts")" -eq "$before" ]; then
	pass "and is not restarted again on evidence from before that restart"
else
	fail "and is not restarted again on evidence from before that restart" \
		"$(tail -2 "$restarts")"
fi

# (f) The account reading is the gate, and a gate that could not be read is
#     `undetermined` — never a pass and never a failure. quota-axi refusing
#     (an expired credential, a provider it cannot see) is that case.
rm -f "$quota"
out="$($QUEUE refuel 2>&1)"
expect "a quota reading that cannot be taken is undetermined" "undetermined" "$out"
refute "and undetermined restarts nothing" "restarted" "$out"

unset CLAUDE_CONFIG_DIR

# --- 13. the display never contradicts itself --------------------------------
#
# Five readings that were all wrong on one screen, every one of them produced
# by records a real run wrote:
#
#   a `landed` task printed with a "held by" line under it
#   that blocker naming an `abandoned` upstream, which can never land, so
#     nothing would ever clear it and the output did not say so
#   `abandoned` printed beside `shipped` and a pull request URL, as though the
#     state and the outcome agreed
#   a pull request sweep answering "none open" on the same screen as a repo it
#     could not read
#   a `queued` task nobody ever dispatched, indistinguishable from one queued a
#     minute ago
#
# The records below ARE those contradictions, written deliberately. The fix is
# in what the surfaces say about them and never in the records: a task.yaml is
# the history of what happened, and tidying one so the output agrees with
# itself deletes the evidence. So every claim here is about output.
#
# In a queue of its own, like test 11, because these states are reached through
# collect, reap and the forge and the sections above own their own records.

export FLEET_QUEUE_DIR="$tmp/queue-status"
mkdir -p "$tmp/repo-readable"

# Rewrite one field of a THROWAWAY record, to reach a state a real run only
# reaches through the forge. Nothing outside this fixture queue is ever edited.
set_field() {
	python3 - "$FLEET_QUEUE_DIR/$1/task.yaml" "$2" "$3" <<'FIELD'
import sys, yaml
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
doc = yaml.safe_load(open(path))
doc[key] = value
with open(path, "w") as fh:
    yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)
FIELD
}

ctopic="$($QUEUE topic add contradictions \
	--title 'Records that disagree with themselves' \
	--prompt 'the status output must not contradict itself')"

# 05 names a checkout that is not there, which is the repo the sweep cannot
# read; the rest share one that is, which is the repo it can.
for spec in \
	"01:upstream-abandoned:An upstream closed unmerged:$tmp/repo-readable" \
	"02:landed-holder:A task that already landed:$tmp/repo-readable" \
	"03:still-queued:A task still waiting on that upstream:$tmp/repo-readable" \
	"04:never-dispatched:A task nobody ever sent out:$tmp/repo-readable" \
	"05:sweeps-a-missing-repo:A task whose checkout is gone:$tmp/no-such-repo"; do
	IFS=: read -r n slug title repo <<<"$spec"
	if ! out="$($QUEUE add "$ctopic" "$slug" --title "$title" --repo "$repo" \
		--branch "fix/$slug" --number "$n" 2>&1)"; then
		fail "add $slug" "$out"
	fi
done

$QUEUE block "$ctopic/02-landed-holder" --on "$ctopic/01-upstream-abandoned" \
	--kind semantic-dependency --why 'reads the field the upstream adds' >/dev/null
$QUEUE block "$ctopic/03-still-queued" --on "$ctopic/01-upstream-abandoned" \
	--kind semantic-dependency --why 'needs that same field' >/dev/null

# The worker reported it shipped; the forge closed the pull request unmerged.
# Both are true, both are recorded, and the pair is the contradiction.
set_field "$ctopic/01-upstream-abandoned" state abandoned
set_field "$ctopic/01-upstream-abandoned" outcome shipped
set_field "$ctopic/01-upstream-abandoned" artifact \
	https://github.com/Thurbeen/thurbox/pull/1091
set_field "$ctopic/02-landed-holder" state landed
set_field "$ctopic/05-sweeps-a-missing-repo" state dispatched

out="$($QUEUE list 2>&1)"
refute "a landed task never displays a blocker" \
	"reads the field the upstream adds" "$out"
expect "a blocker whose upstream can never land is called unclearable" \
	"UNCLEARABLE" "$out"
expect "and the line carries the upstream's state, so the dead end is visible" \
	"which is abandoned and can never land" "$out"
expect "a state and an outcome that disagree are printed as a conflict" \
	"state abandoned disagrees with outcome shipped" "$out"
expect "a queued task nobody dispatched carries a marker" \
	"no session dispatched" "$out"

expect "the marker is on the task with no session" "no session dispatched" \
	"$($QUEUE show "$ctopic/04-never-dispatched" 2>&1)"
refute "and not on the one a blocker is holding, which is a different fact" \
	"no session dispatched" "$($QUEUE show "$ctopic/03-still-queued" 2>&1)"

held="$($QUEUE show "$ctopic/02-landed-holder" 2>&1)"
refute "show does not call a landed task's blocker HOLDING either" "HOLDING" "$held"
expect "it says the blocker holds nothing now" "holds nothing" "$held"

plan="$($QUEUE plan 2>&1)"
expect "plan names the unclearable blocker as one too" "UNCLEARABLE" "$plan"

# --- 13b. the same five, in fleet-status.sh ----------------------------------

status="$(./scripts/fleet-status.sh 2>&1)"
refute "fleet-status drops the landed task's blocker as well" \
	"reads the field the upstream adds" "$status"
expect "it names the unclearable one" "UNCLEARABLE" "$status"
expect "it shows the state/outcome conflict" \
	"disagrees with outcome shipped" "$status"
expect "it marks the task nobody dispatched" "no session dispatched" "$status"
refute "the PR headline cannot read 'none open' when a repo went unread" \
	"none open for these tasks" "$status"
expect "the headline itself says the sweep was incomplete" "INCOMPLETE" "$status"
expect "and the repo it could not read is still named" "no such directory" "$status"

# --- 13c. and in the machine-readable reading, which is the same records -----
#
# `--json` is the reading a program consumes, and it derives its blockers and
# its notes through queue.py exactly as the text above does. The claim is that
# the two cannot disagree, which is a question about the derivation.

view="$(./scripts/fleet-status.sh --json 2>&1)"
expect "--json calls the unclearable blocker unclearable" \
	'"status": "unclearable"' "$view"
expect "and a landed task's blocker moot" '"status": "moot"' "$view"
expect "it carries the same conflict note" "disagrees with outcome shipped" "$view"
expect "and the same no-session marker" "no session dispatched" "$view"

# --- 14. a finished topic archives itself, and leaves every default view ------
#
# The queue reached 24 topics with 27 of its 30 tasks `landed`, and the two
# topics with live work were buried under twenty-two finished ones in both
# readers. Archiving is a FLAG and a FILTER — nothing is moved, deleted or
# rewritten, because this queue is gitignored and the repo does not back it up.
#
# The one predicate underneath all of it: a topic is archivable when every
# task it has is `landed` or `abandoned`. `stuck` and `failed` are the
# worker's own verdicts, whose sessions §5b keeps alive as evidence, so a
# topic holding either must stay in front of the operator. The automatic
# sweep, the manual command and the `add` clear all ask the same question, and
# the cases below are what stops the three drifting apart.
#
# In a queue of its own: the sections above leave tasks in every state there
# is, and this one is about what a WHOLE topic adds up to.

export FLEET_QUEUE_DIR="$tmp/queue-archive"

# A task that really landed, by the only path that produces one: the worker's
# result, a pipeline-compliant pull request body, and the forge saying merged.
landed_task() {
	local topic="$1" number="$2" slug="$3" pr="$4"
	$QUEUE add "$topic" "$slug" --title "$slug" --repo /tmp/repo-a \
		--branch "fix/$slug" --number "$number" >/dev/null
	pipeline_pr "$pr" "fix/$slug"
	echo MERGED >"$states/$pr.state"
	cat >"$FLEET_QUEUE_DIR/$topic/$number-$slug/result.md" <<EOF
---
outcome: shipped
artifact: https://github.com/Thurbeen/thurbox/pull/$pr
---
Landed.
EOF
}

atopic="$($QUEUE topic add all-landed --title 'Every task merged' \
	--prompt 'a topic whose work is entirely on main')"
landed_task "$atopic" 01 first-half 2001
landed_task "$atopic" 02 second-half 2002

ltopic="$($QUEUE topic add half-live --title 'One merged, one still out' \
	--prompt 'a topic with a worker still running in it')"
landed_task "$ltopic" 01 merged-part 2003
$QUEUE add "$ltopic" running-part --title 'running part' --repo /tmp/repo-a \
	--branch fix/running-part --number 02 >/dev/null
sed -i 's/^state: .*/state: dispatched/' \
	"$FLEET_QUEUE_DIR/$ltopic/02-running-part/task.yaml"

gtopic="$($QUEUE topic add gave-up --title 'One merged, one given up on' \
	--prompt 'a topic a worker could not finish')"
landed_task "$gtopic" 01 done-part 2004
$QUEUE add "$gtopic" broken-part --title 'broken part' --repo /tmp/repo-a \
	--branch fix/broken-part --number 02 >/dev/null
cat >"$FLEET_QUEUE_DIR/$gtopic/02-broken-part/result.md" <<'EOF'
---
outcome: failed
---
Could not make the migration work; the session is the evidence.
EOF

# (a) The sweep that writes the flag is the one that moves the last task into a
#     terminal state — `collect`, through the landing sweep `reap` owns.
out="$($QUEUE collect 2>&1)"
expect "the topic whose every task landed is archived" "all-landed" "$out"
expect "and the sweep says so in the word the record uses" "archived" "$out"
refute "a topic still holding a dispatched worker is not archived" \
	"half-live      " "$out"

meta="$(cat "$FLEET_QUEUE_DIR/$atopic/topic.yaml")"
expect "the flag lands in topic.yaml, beside slug and title" "archived:" "$meta"
expect "and topic.yaml keeps everything it already carried" "title: Every task merged" "$meta"

refute "a topic with a live task carries no flag" "archived:" \
	"$(cat "$FLEET_QUEUE_DIR/$ltopic/topic.yaml")"
refute "and neither does one whose worker gave up: failed is not terminal here" \
	"archived:" "$(cat "$FLEET_QUEUE_DIR/$gtopic/topic.yaml")"

# (b) Every default view drops it, and every one of them still says how many it
#     dropped — a queue that looks small is worse than a queue that looks long.
out="$($QUEUE list 2>&1)"
refute "list hides the archived topic" "all-landed" "$out"
expect "but says how many it is hiding" "1 archived topic(s)" "$out"
expect "and the live topics are still there" "half-live" "$out"
expect "as is the one that needs an operator" "gave-up" "$out"

out="$($QUEUE list --archived 2>&1)"
expect "--archived shows only the archived set" "all-landed" "$out"
refute "and nothing else" "half-live" "$out"

out="$($QUEUE list --all 2>&1)"
expect "--all shows the archived topic" "all-landed" "$out"
expect "and the live ones beside it" "half-live" "$out"

out="$($QUEUE show "$atopic/01-first-half" 2>&1)"
expect "show reaches an archived task by name, with no unarchiving first" \
	"01-first-half" "$out"
expect "and its whole record with it" "state:       landed" "$out"

# (c) Not read, not merely not shown. The point of the flag is that a finished
#     topic costs one `sed` of topic.yaml and nothing else — so a task file
#     that cannot be parsed at all must not reach any default view.
mv "$FLEET_QUEUE_DIR/$atopic/01-first-half/task.yaml" "$tmp/parked-task.yaml"
printf 'a: b: c\n' >"$FLEET_QUEUE_DIR/$atopic/01-first-half/task.yaml"
out="$($QUEUE list 2>&1)"
expect "an archived topic's task files are never opened by list" \
	"1 archived topic(s)" "$out"
expect "and the live topics still render" "half-live" "$out"
out="$(./scripts/fleet-status.sh 2>&1)"
expect "nor by fleet-status.sh, which counts them the same way" \
	"1 archived topic(s)" "$out"
refute "and does not draw the archived topic" "all-landed" "$out"
out="$(./scripts/fleet-status.sh --json 2>&1)"
expect "nor by the machine-readable reading, which counts them the same way" \
	'"archived": 1' "$out"
refute "and does not carry the archived topic" '"all-landed"' "$out"

# The TUI pane is the third reader, and the only one that is not Python. Its
# probe is plain shell, so it is run here exactly as the pane runs it — a pane
# that disagreed with `list` would be a second opinion about a model it does
# not own.
sed -n '/^local PROBE = \[==\[$/,/^\]==\]$/p' interface/fleet_queue.lua |
	sed '1d;$d' >"$tmp/pane-probe.sh"
out="$(sh "$tmp/pane-probe.sh" 2>/dev/null)"
expect "the pane's probe counts the archived topic" "$(printf 'A\t1')" "$out"
refute "and never emits a record for it" "all-landed" "$out"
expect "while still emitting the live ones" "half-live" "$out"
mv "$tmp/parked-task.yaml" "$FLEET_QUEUE_DIR/$atopic/01-first-half/task.yaml"

# And the other half of "not read": `list --archived` opens the archived set
# when something asks it to, on a route the default view never touches.
expect "list --archived can fetch the archived set on demand" "all-landed" \
	"$($QUEUE list --archived 2>&1)"

# (d) The manual override, and the one refusal that keeps it honest.
if out="$($QUEUE archive "$ltopic" 2>&1)"; then
	fail "archive refuses a topic with a live task" "$out"
else
	expect "archive refuses a topic with a live task" "not finished" "$out"
	expect "and names the task that is holding it" "02-running-part" "$out"
	expect "with the state that made it non-terminal" "dispatched" "$out"
fi
if out="$($QUEUE archive "$gtopic" 2>&1)"; then
	fail "archive refuses a topic whose worker gave up" "$out"
else
	expect "archive refuses a topic whose worker gave up" "02-broken-part" "$out"
fi

$QUEUE unarchive "$atopic" >/dev/null
refute "unarchive clears the flag" "archived:" \
	"$(cat "$FLEET_QUEUE_DIR/$atopic/topic.yaml")"
expect "and the topic is back in the default view" "all-landed" "$($QUEUE list 2>&1)"
$QUEUE archive "$atopic" >/dev/null
expect "and archive puts it back out of it" "1 archived topic(s)" "$($QUEUE list 2>&1)"

# (e) A topic that grows a new task is live again, whatever it was. An
#     operator who did not notice the flag would otherwise dispatch into a
#     topic no default view draws.
$QUEUE add "$atopic" third-half --title 'third half' --repo /tmp/repo-a \
	--branch fix/third-half --number 03 >/dev/null
refute "adding a task un-archives the topic it went into" "archived:" \
	"$(cat "$FLEET_QUEUE_DIR/$atopic/topic.yaml")"
out="$($QUEUE list 2>&1)"
expect "so the new work is visible where it was dispatched from" "all-landed" "$out"
refute "and nothing is left claiming to be hidden" "archived topic(s)" "$out"

out="$($QUEUE check 2>&1)"
expect "and every record still validates" "ok" "$out"

# (f) What archiving must NOT narrow. The shepherd derives the repositories it
#     watches from the tasks the queue holds, and then asks the forge about
#     every open pull request in each — including ones no task recorded. A
#     shepherd that only saw live topics would stop watching a repository the
#     moment its last topic finished, which is exactly when a stray pull
#     request has nobody left looking at it.
$QUEUE archive "$atopic" >/dev/null 2>&1 || true
$QUEUE unarchive "$atopic" >/dev/null 2>&1 || true
sed -i 's/^state: .*/state: landed/' \
	"$FLEET_QUEUE_DIR/$atopic/03-third-half/task.yaml"
$QUEUE archive "$atopic" >/dev/null
expect "the topic is archived again once its last task landed" \
	"1 archived topic(s)" "$($QUEUE list 2>&1)"
out="$($QUEUE shepherd --ref "$atopic/01-first-half" --dry-run 2>&1)"
refute "the shepherd still reaches an archived task's record" "no such task" "$out"

# (g) The one shape of this flag that is actively harmful: a topic marked
#     archived while it still holds live work is work nothing draws. Nothing
#     fleet does can produce one — the sweep refuses it and `add` clears the
#     flag — but a hand-edited topic.yaml can, so `check` says so.
printf "archived: '2026-01-01T00:00:00+00:00'\n" \
	>>"$FLEET_QUEUE_DIR/$ltopic/topic.yaml"
if out="$($QUEUE check 2>&1)"; then
	fail "check catches a topic archived over live work" "$out"
else
	expect "check catches a topic archived over live work" "half-live" "$out"
	expect "and names the task nothing would have drawn" "02-running-part" "$out"
	expect "with the remedy" "unarchive" "$out"
fi

# --- 16. the three defects that made the lead work around the tool -----------
#
# All three were hit repeatedly in one orchestration session (2026-09-09), and
# the second wrote a lie into the queue's own records: with `dispatch`
# all-or-nothing, holding two of five ready tasks back could only be spelled as
# a blocker, and one was recorded with the reason "Operator has not been asked
# whether to run it at all".
#
# In a queue of its own, because a bare `dispatch` acts on every ready task in
# the whole queue and the sections above leave some of theirs deliberately
# unwritten.

export FLEET_QUEUE_DIR="$tmp/queue-ergonomics"

etopic="$($QUEUE topic add stop-the-workarounds \
	--title 'Stop the lead working around the queue' \
	--prompt 'three tool defects made the lead hand-repair what the tool should do')"

# (a) A brief file carrying the four standard headings fills the four standard
#     sections. The whole file used to go into section 0, which produced a
#     brief with `## What to do` twice and three untouched placeholders --
#     refused by `dispatch`, and hand-repaired five times in one session.

cat >"$tmp/full-brief.md" <<'MD'
## What to do

Rewrite the state machine so `idle` means the agent said so.

## Done means

`cargo test` passes and the pull request is open.

## Hard constraints

Do not touch `src/list.rs`; another worker is in it.

## Coordination

`02-document-the-states` reads what you write here.
MD

$QUEUE add "$etopic" fill-every-section --title 'Fill every section' \
	--repo /tmp/repo-a --branch fix/fill-every-section --number 01 \
	--brief-file "$tmp/full-brief.md" >/dev/null
fullraw="$(cat "$FLEET_QUEUE_DIR/$etopic/01-fill-every-section/BRIEF.md")"
full="$(brief_text "$FLEET_QUEUE_DIR/$etopic/01-fill-every-section/BRIEF.md")"

refute "a brief file with the four headings leaves no placeholder" \
	"WRITE THE INSTRUCTIONS HERE" "$fullraw"
dupes="$(printf '%s\n' "$fullraw" | grep -c '^## What to do$')"
if [ "$dupes" = 1 ]; then
	pass "and does not duplicate the heading it was filed under"
else
	fail "and does not duplicate the heading it was filed under" \
		"counted $dupes \`## What to do\` headings${nl}$fullraw"
fi
expect "the body's own prose lands in \`What to do\`" \
	"## What to do Rewrite the state machine" "$full"
expect "and each other heading's content lands under that heading" \
	"## Hard constraints Do not touch \`src/list.rs\`" "$full"
expect "including the last one" \
	"## Done means \`cargo test\` passes" "$full"

# The skeleton is still the skeleton: the same four headings, in
# BRIEF_SECTIONS' order, whatever order the file put them in.
order="$(printf '%s\n' "$fullraw" | grep '^## ' | tr '\n' '|')"
want='## What to do|## Hard constraints|## Coordination|## Done means|'
case "$order" in
"$want"*) pass "and the four sections keep the skeleton's order" ;;
*) fail "and the four sections keep the skeleton's order" "got: $order" ;;
esac

# (b) A heading that is not one of the four is CONTENT, not structure. It is
#     kept verbatim under whichever section it appeared in: dropping it loses
#     what the lead wrote, and promoting it is the 20 invented headings that
#     BRIEF_SECTIONS exists to stop.

cat >"$tmp/odd-brief.md" <<'MD'
Do the thing.

## Background

The reason it matters.

## Hard constraints

None.

## Coordination

None.

## Done means

It is done.
MD

$QUEUE add "$etopic" keep-odd-headings --title 'Keep odd headings' \
	--repo /tmp/repo-a --branch fix/keep-odd-headings --number 02 \
	--brief-file "$tmp/odd-brief.md" >/dev/null
odd="$(brief_text "$FLEET_QUEUE_DIR/$etopic/02-keep-odd-headings/BRIEF.md")"
expect "an unrecognised heading is kept in place, under the section it was in" \
	"Do the thing. ## Background The reason it matters." "$odd"
expect "and a recognised heading after it still fills its own section" \
	"## Done means It is done." "$odd"
refute "so that section is no longer unwritten" \
	"## Done means <!-- WRITE THE INSTRUCTIONS HERE -->" "$odd"

# (c) A body with no headings at all is still all `What to do` -- and that is
#     now something you can READ, because a file filling only that section is
#     refused at `add` naming the other three (16d). The mapping is the same
#     one it always was; where you find out about it changed.

printf 'Just do it, there is nothing else to say.\n' >"$tmp/flat-brief.md"
if out="$($QUEUE add "$etopic" headingless-body --title 'Headingless body' \
	--repo /tmp/repo-a --branch fix/headingless-body --number 03 \
	--brief-file "$tmp/flat-brief.md" 2>&1)"; then
	fail "a headingless body fills only What to do, and is refused for the rest" "$out"
else
	pass "a headingless body fills only What to do, and is refused for the rest"
	for heading in "Hard constraints" "Coordination" "Done means"; do
		expect "and the refusal names \`$heading\`" "$heading" "$out"
	done
	refute "and not the one the body landed in" "What to do" "$out"
fi

# A `## ` inside a fenced block is example text a brief is quoting, not a
# heading it is opening.
cat >"$tmp/fenced-brief.md" <<'MD'
Copy this shape:

```markdown
## Done means

not a real heading
```

## Hard constraints

None.

## Coordination

None.

## Done means

The real one.
MD
$QUEUE add "$etopic" fenced-body --title 'Fenced body' --repo /tmp/repo-a \
	--branch fix/fenced-body --number 04 --brief-file "$tmp/fenced-brief.md" >/dev/null
fenced="$(brief_text "$FLEET_QUEUE_DIR/$etopic/04-fenced-body/BRIEF.md")"
# shellcheck disable=SC2016  # literal backticks in expected fenced text, not a substitution
expect "a \`## \` inside a fence stays in the section it was written in" \
	'```markdown ## Done means not a real heading ```' "$fenced"
expect "and the real heading after the fence still fills its section" \
	"## Done means The real one." "$fenced"

# --- 16b. dispatch takes refs, so holding one back needs no fake blocker -----

for spec in \
	"10:goes-out-alone:Goes out alone" \
	"11:goes-out-together:Goes out together" \
	"12:waits-for-real:Waits for a real reason"; do
	IFS=: read -r n slug title <<<"$spec"
	$QUEUE add "$etopic" "$slug" --title "$title" --repo /tmp/repo-a \
		--branch "fix/$slug" --number "$n" \
		--brief-file "$tmp/full-brief.md" >/dev/null
done
$QUEUE block "$etopic/12-waits-for-real" --on "$etopic/10-goes-out-alone" \
	--kind semantic-dependency --why 'reads the field 10 introduces' >/dev/null

out="$($QUEUE dispatch "$etopic/10-goes-out-alone" --dry-run 2>&1)"
spawns="$(printf '%s\n' "$out" | grep -c 'session create')"
if [ "$spawns" = 1 ]; then
	pass "dispatch <ref> launches only that task"
else
	fail "dispatch <ref> launches only that task" "counted $spawns${nl}$out"
fi
refute "and none of the other ready ones" "11-goes-out-together" "$out"
expect "and says the rest were left queued with nothing recording the choice" \
	"no ref is the norm" "$out"

out="$($QUEUE dispatch "$etopic/10-goes-out-alone" "$etopic/11-goes-out-together" \
	--dry-run 2>&1)"
spawns="$(printf '%s\n' "$out" | grep -c 'session create')"
if [ "$spawns" = 2 ]; then
	pass "several refs launch exactly those"
else
	fail "several refs launch exactly those" "counted $spawns${nl}$out"
fi

if out="$($QUEUE dispatch "$etopic/12-waits-for-real" --dry-run 2>&1)"; then
	fail "a named task that is blocked is refused" "$out"
else
	expect "a named task that is blocked is refused by name" \
		"12-waits-for-real" "$out"
	expect "and the refusal states the blocker holding it" \
		"reads the field 10 introduces" "$out"
fi

if out="$($QUEUE dispatch "$etopic/99-no-such-task" --dry-run 2>&1)"; then
	fail "a ref no task answers to is refused" "$out"
else
	expect "a ref no task answers to is refused" "no such task" "$out"
fi

# The default is unchanged, and stays the norm: no ref sends the whole ready
# set, and still refuses a brief nobody has written before it sends any.
$QUEUE add "$etopic" written-later --title 'Written later' --repo /tmp/repo-a \
	--branch fix/written-later --number 13 >/dev/null
if out="$($QUEUE dispatch --dry-run 2>&1)"; then
	fail "a bare dispatch still refuses the queue's unwritten briefs" "$out"
else
	expect "a bare dispatch still refuses the queue's unwritten briefs" \
		"13-written-later" "$out"
fi
printf 'Written now.\n' >"$FLEET_QUEUE_DIR/$etopic/13-written-later/BRIEF.md"
out="$($QUEUE dispatch --dry-run 2>&1)"
spawns="$(printf '%s\n' "$out" | grep -c 'session create')"
if [ "$spawns" = 6 ]; then
	pass "a bare dispatch still launches the whole ready set"
else
	fail "a bare dispatch still launches the whole ready set" "counted $spawns${nl}$out"
fi
expect "and still says so in the words that make it the norm" \
	"no concurrency cap" "$out"
refute "and says nothing about holding anything back" "no ref is the norm" "$out"

# --- 16c. block --kind lists its own valid values in --help ------------------
#
# The set only ever appeared in the refusal you got after guessing wrong, and
# the lead guessed twice. COLUMNS keeps argparse from wrapping a hyphenated
# choice across two lines.

out="$(COLUMNS=200 $QUEUE block --help 2>&1)"
for kind in semantic-dependency shared-external-state incompatible-migration other; do
	expect "block --help lists \`$kind\`" "$kind" "$out"
done
expect "and says why --clear still names a blocker with --on" \
	"more than one" "$out"

if out="$($QUEUE block "$etopic/11-goes-out-together" --on "$etopic/10-goes-out-alone" \
	--kind file-overlap --why 'both edit one file' 2>&1)"; then
	fail "an invalid --kind is still refused with the guidance" "$out"
else
	expect "an invalid --kind is still refused with the guidance" \
		"semantic-dependency" "$out"
	expect "and still says where file overlap belongs instead" "--touches" "$out"
fi

# --- 16d. the intake path refuses at `add`, where the repair is one edit -----
#
# Three more of the same family, all hit repeatedly on 2026-09-09. Each one is
# something `add` already knew and `dispatch` was left to discover, which costs
# the lead a whole round-trip per task: dispatch, read the refusal, go and look
# at the file or the record, repair it, dispatch again.
#
#   (d) `--brief-file` is a CLAIM to have written the brief. A file that leaves
#       a scaffolded section unwritten is refused at `add`, naming the sections
#       — not accepted, then refused by `dispatch` as "unwritten", about a
#       brief the lead did write.
#   (e) The scaffold check is STRUCTURAL. It compares each section against what
#       the scaffold wrote, so a brief that quotes the placeholder to talk
#       about it still dispatches. A substring grep meant the queue could not
#       carry a task about its own scaffold — this very task's brief hit it.
#   (f) `--branch` equal to `--base` cannot be spawned: `--worktree-branch`
#       only ever CREATES a branch, and a base exists by definition. `add` has
#       both values, so `add` refuses — and asks the repo about every other
#       branch that is already there, which is the same failure.
#
# `add` with NO --brief-file is untouched. That is the deliberate "scaffold it,
# I will write it" path, and `dispatch` stays its backstop — with a refusal
# that now names the sections too.

cat >"$tmp/half-brief.md" <<'MD'
## What to do

Rewrite the state machine so `idle` means the agent said so.
MD

if out="$($QUEUE add "$etopic" half-written --title 'Half written' \
	--repo /tmp/repo-a --branch fix/half-written --number 20 \
	--brief-file "$tmp/half-brief.md" 2>&1)"; then
	fail "a --brief-file that leaves a section unwritten is refused at add" "$out"
else
	pass "a --brief-file that leaves a section unwritten is refused at add"
	for heading in "Hard constraints" "Coordination" "Done means"; do
		expect "and the refusal names \`$heading\` as one of them" "$heading" "$out"
	done
	refute "and does not name the one the file did fill" "What to do" "$out"
fi

if [ -e "$FLEET_QUEUE_DIR/$etopic/20-half-written" ]; then
	fail "and leaves nothing behind, so the repair is one edit and one re-run" \
		"$(ls "$FLEET_QUEUE_DIR/$etopic/20-half-written")"
else
	pass "and leaves nothing behind, so the repair is one edit and one re-run"
fi

# The one-step property: the same `add`, once the file is whole, produces a
# task that dispatches. No patching of the rendered brief in between.
cat >>"$tmp/half-brief.md" <<'MD'

## Hard constraints

None.

## Coordination

None.

## Done means

`cargo test` passes.
MD

if out="$($QUEUE add "$etopic" half-written --title 'Half written' \
	--repo /tmp/repo-a --branch fix/half-written --number 20 \
	--brief-file "$tmp/half-brief.md" 2>&1)"; then
	pass "a --brief-file that fills every section is accepted"
	if out="$($QUEUE dispatch "$etopic/20-half-written" --dry-run 2>&1)"; then
		pass "and dispatches in one step, with nothing hand-repaired between"
	else
		fail "and dispatches in one step, with nothing hand-repaired between" "$out"
	fi
else
	fail "a --brief-file that fills every section is accepted" "$out"
fi

# The scaffold path is untouched, and its backstop now says WHICH sections.
$QUEUE add "$etopic" scaffold-me --title 'Scaffold me' --repo /tmp/repo-a \
	--branch fix/scaffold-me --number 21 >/dev/null
if out="$($QUEUE dispatch "$etopic/21-scaffold-me" --dry-run 2>&1)"; then
	fail "add with no --brief-file still scaffolds, and dispatch still refuses it" "$out"
else
	pass "add with no --brief-file still scaffolds, and dispatch still refuses it"
	for heading in "What to do" "Hard constraints" "Coordination" "Done means"; do
		expect "and the backstop names \`$heading\`, not just the path" "$heading" "$out"
	done
fi

# (e) A brief that QUOTES the placeholder is a written brief. The check that
#     could not tell the two apart is why this task's own brief had to have the
#     quotation cut out of it before it could be dispatched.

cat >"$tmp/quoting-brief.md" <<'MD'
## What to do

Every section of the scaffold starts as `<!-- WRITE THE INSTRUCTIONS HERE -->`
and the dispatch precondition used to grep the whole file for that string, so
a brief describing it refused to go out. Compare each section instead.

## Hard constraints

Do not weaken the check. A worker sent a scaffold has nothing to do.

## Coordination

None.

## Done means

This brief, which quotes `<!-- WRITE THE INSTRUCTIONS HERE -->`, dispatches.
MD

if out="$($QUEUE add "$etopic" quotes-the-scaffold --title 'Quotes the scaffold' \
	--repo /tmp/repo-a --branch fix/quotes-the-scaffold --number 22 \
	--brief-file "$tmp/quoting-brief.md" 2>&1)"; then
	pass "a brief that quotes the placeholder is accepted at add"
else
	fail "a brief that quotes the placeholder is accepted at add" "$out"
fi
if out="$($QUEUE dispatch "$etopic/22-quotes-the-scaffold" --dry-run 2>&1)"; then
	pass "and dispatches, so the queue can carry a task about its own scaffold"
else
	fail "and dispatches, so the queue can carry a task about its own scaffold" "$out"
fi

# (f) The branch. `--branch main --base main` was accepted and then died at
#     spawn with thurbox's own non-zero exit, leaving the task queued and the
#     operator editing task.yaml by hand.

if out="$($QUEUE add "$etopic" branch-is-base --title 'Branch is base' \
	--repo /tmp/repo-a --branch main --base main --number 23 2>&1)"; then
	fail "--branch equal to --base is refused at add" "$out"
else
	pass "--branch equal to --base is refused at add"
	expect "and the refusal names the branch" "main" "$out"
	expect "and says why it could never be spawned" "worktree" "$out"
fi

# The same precondition, generally. `--worktree-branch` only ever CREATES the
# branch, so ANY branch already in the repo fails the spawn — base is merely
# the one that exists by definition. A repo this machine can read gets asked.

brepo="$tmp/branch-repo"
git init -q -b main "$brepo"
git -C "$brepo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base
git -C "$brepo" branch fix/left-behind

if out="$($QUEUE add "$etopic" branch-exists --title 'Branch exists' \
	--repo "$brepo" --branch fix/left-behind --base main --number 24 2>&1)"; then
	fail "a branch already in that repo is refused at add" "$out"
else
	pass "a branch already in that repo is refused at add"
	expect "and the refusal names it" "fix/left-behind" "$out"
	expect "and quotes the failure the spawn would have died with" \
		"already exists" "$out"
fi

# A branch that is not there is the ordinary case, and nothing about this is
# allowed to make it slower or louder.
if out="$($QUEUE add "$etopic" branch-is-new --title 'Branch is new' \
	--repo "$brepo" --branch fix/is-new --base main --number 25 2>&1)"; then
	pass "a branch that is not there yet is created as it always was"
else
	fail "a branch that is not there yet is created as it always was" "$out"
fi

# A repo this machine has not got is not a repo to ask, so the check is silent
# and dispatch stays the backstop it was — which is every `--host` task.
if out="$($QUEUE add "$etopic" repo-not-here --title 'Repo not here' \
	--repo /tmp/repo-a --branch fix/repo-not-here --base main --number 26 2>&1)"; then
	pass "a repo this machine cannot read is left to dispatch, as before"
else
	fail "a repo this machine cannot read is left to dispatch, as before" "$out"
fi

# --- 17. one setting puts a mark on every session, and takes it back ---------
#
# The lead's mark and the workers' are ONE setting, because the reason to turn
# either off is the same one: this terminal draws a two-cell glyph badly. So the
# claims are about the setting and not about a glyph.
#
#   `on` is the default, and it needs no file — the tracked defaults answer
#   `off` restores EXACTLY the naming that predates the glyphs: the lead wears
#     the one-cell mark it always wore, and a worker wears nothing
#   the name is cut to thurbox's real cap, which is BYTES and not characters —
#     `session create` refuses at 65 bytes with a message that says
#     "64 characters", so a title that fits chars-wise can still fail at spawn,
#     and a spawn that fails takes the whole dispatch with it
#   the mark reaches the session thurbox is actually asked to create

glyphs_at() {
	python3 -c '
import sys
sys.path.insert(0, "scripts/lib")
import queue as q
root = sys.argv[1] or None
print(q.glyph_conf(root).get("LEAD_GLYPH_ON", ""), q.worker_glyph(root), sep="\t")
' "$1"
}

expect "with no local file the tracked defaults answer, and the marks are the emoji" \
	"$(printf '\xf0\x9f\x93\xa1\t\xf0\x9f\x9a\x80')" "$(glyphs_at "")"

glyphtmp="$(mktemp -d)"
mkdir -p "$glyphtmp/orchestration"
cat >"$glyphtmp/orchestration/session-glyphs.conf" <<'EOF'
GLYPHS=off
LEAD_GLYPH_ON=📡
LEAD_GLYPH_OFF=⌖
WORKER_GLYPH_ON=🚀
EOF
out="$(glyphs_at "$glyphtmp")"
if [ "$out" = "$(printf '\xf0\x9f\x93\xa1\t')" ]; then
	pass "GLYPHS=off gives a worker no mark at all"
else
	fail "GLYPHS=off gives a worker no mark at all" "$(printf '%s' "$out" | cat -A)"
fi

out="$(python3 -c '
import sys
sys.path.insert(0, "scripts/lib")
import queue as q
title = sys.argv[1]
print(q.session_name(title, "\N{ROCKET}"))
print(q.session_name(title, ""))
print(len(q.session_name("a" * 70, "\N{ROCKET}").encode()))
print(q.session_name("é" * 70, "\N{ROCKET}").encode().decode())
' "Fix the thing")"
expect "a worker wears its mark in front of the work" "🚀 Fix the thing" "$out"
expect "and with the setting off the name is the title, unchanged" \
	"Fix the thing" "$(printf '%s\n' "$out" | sed -n 2p)"
expect "a long name is cut to thurbox's cap, counted in bytes" "64" "$out"
if [ "$(printf '%s\n' "$out" | sed -n 4p | wc -c)" -le 65 ]; then
	pass "and the cut lands on a codepoint boundary, so the name is still a name"
else
	fail "and the cut lands on a codepoint boundary, so the name is still a name" "$out"
fi

rm -rf "$glyphtmp"

# The wiring, and not only the function: the mark has to reach the argv thurbox
# is handed. FLEET_GLYPH_ROOT points this dispatch at an isolated copy of the
# default setting rather than the real checkout's — a developer running this
# selftest with their own gitignored GLYPHS=off must not see a spurious failure
# here.
export FLEET_QUEUE_DIR="$tmp/queue-glyph"
FLEET_GLYPH_ROOT="$(mktemp -d)"
export FLEET_GLYPH_ROOT
mkdir -p "$FLEET_GLYPH_ROOT/orchestration"
cat >"$FLEET_GLYPH_ROOT/orchestration/session-glyphs.conf" <<'EOF'
GLYPHS=on
LEAD_GLYPH_ON=📡
LEAD_GLYPH_OFF=⌖
WORKER_GLYPH_ON=🚀
EOF
gtopic="$($QUEUE topic add marked --title 'Sessions wear a mark' \
	--prompt 'give every session a glyph')"
$QUEUE add "$gtopic" wear-it --title 'Wear the mark' --repo /tmp/repo-a \
	--branch feat/mark --number 01 >/dev/null
printf '# Wear the mark\n\nA brief with real content in it.\n' \
	>"$FLEET_QUEUE_DIR/$gtopic/01-wear-it/BRIEF.md"
out="$($QUEUE dispatch --dry-run 2>&1)"
expect "the mark reaches the name thurbox is asked to create" \
	"🚀 Wear the mark" "$out"
rm -rf "$FLEET_GLYPH_ROOT"
unset FLEET_GLYPH_ROOT

# --- 18. did that message land, and has anything moved since? ----------------
#
# The failure this answers, from a real session on 2026-09-09: the lead sent
# new scope to a parked worker, `session send` reported success, and ten
# minutes later the session read `done`, age 3043s — a state from BEFORE the
# message. On that evidence the worker looked dead. It had taken the message,
# done the work and committed it, and the only way the lead found out was
# opening the worker's worktree and running `git log`.
#
# The claims:
#
#   a send is RECORDED, with a baseline of the things a worker cannot fake
#   a worker that received it and moved is visible as moved — from the branch
#     head, and from a transition dated after the message
#   a send with nothing moving since says exactly that, and never that the
#     worker is stuck, dead or unreachable
#   a task NOBODY messaged reads as it always did: no line at all, because a
#     question nobody asked has no answer
#   a send that did not go in is `NOT DELIVERED`, which is the other half of
#     "did it land"
#   a branch this machine cannot read is `not checked` — never a silent
#     "no movement"
#   nothing here writes `state` or `outcome`

export FLEET_QUEUE_DIR="$tmp/queue-live"
: >"$sends"

# A real checkout, because the branch head is read out of the task's own repo:
# a worker's worktree shares this object store, so a commit made there moves
# `refs/heads/<branch>` right here — which is what makes the reading work with
# no session to ask and no worktree path to resolve.
liverepo="$tmp/live-repo"
mkdir -p "$liverepo"
git -C "$liverepo" init -q -b main
git -C "$liverepo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base

ltopic="$($QUEUE topic add course-correct --title 'Message a worker mid-flight' \
	--prompt 'tell a parked worker about new scope')"
messaged() {
	$QUEUE add "$ltopic" "$1" --title "Task $1" --repo "$liverepo" \
		--branch "feat/$1" --number "$2" >/dev/null
	$QUEUE attach "$ltopic/$2-$1" "$3" >/dev/null
	session_is "$3" "done" 3043
}
messaged moved 01 cccccccc-0000-0000-0000-000000000001
messaged quiet 02 cccccccc-0000-0000-0000-000000000002
messaged never 03 cccccccc-0000-0000-0000-000000000003

# The branches exist from here on, because the worker's own spawn is what
# creates one — `add` above recorded a branch that was not there yet.
git -C "$liverepo" branch feat/moved
git -C "$liverepo" branch feat/quiet
git -C "$liverepo" branch feat/never

out="$($QUEUE send "$ltopic/01-moved" 'Also update the changelog.' 2>&1)"
expect "the queue sends the message itself, so the lead stops reaching past it" \
	"delivered" "$out"
expect "and it really reached that worker's session" \
	"Also update the changelog." "$(cat "$sends")"
expect "and the branch head it will be compared against is written down" \
	"baseline:" "$out"
$QUEUE send "$ltopic/02-quiet" 'Anything to report?' >/dev/null 2>&1

# (a) A SEND RECORDED, THEN MOVEMENT. The worker committed on its branch —
#     the exact evidence the lead had to go and dig out of a foreign worktree.
git -C "$liverepo" -c user.email=t@t -c user.name=t commit -q --allow-empty \
	-m 'the work the lead thought had never happened'
git -C "$liverepo" branch -f feat/moved HEAD
row="$($QUEUE list --topic "$ltopic" 2>&1 | grep -A2 01-moved)"
expect "a worker that moved after the message is visible as moved" \
	"committed" "$row"
refute "and is never reported quiet" "no commit since" "$row"

# A transition dated after the message is the other half, and it is the half
# `watch` produces — `session get` was still reporting a state from before the
# send. The older event in the same file is the trap: a `watch` run catching up
# folds it in AFTER the message, and it is still not movement.
python3 - "$FLEET_QUEUE_DIR/$ltopic/01-moved" <<'PY'
import json
import sys
from datetime import datetime, timedelta

import yaml

# Dated against the RECORDED SEND rather than against the wall clock, so one
# event is unambiguously before it and one after, however fast this runs.
task = yaml.safe_load(open(f"{sys.argv[1]}/task.yaml"))
sent = datetime.fromisoformat(task["sends"][-1]["at"])
now = datetime.now(sent.tzinfo).isoformat()
rows = [
    {"seq": 1, "at": (sent - timedelta(hours=2)).isoformat(), "to": "working",
     "observed": now},
    {"seq": 2, "at": (sent + timedelta(seconds=1)).isoformat(), "to": "done",
     "observed": now},
]
with open(f"{sys.argv[1]}/progress.jsonl", "w") as fh:
    for row in rows:
        fh.write(json.dumps(row) + "\n")
PY
out="$($QUEUE show "$ltopic/01-moved" 2>&1)"
expect "a transition dated after the message counts as movement" \
	"transitioned" "$out"
expect "and the message itself is on the record, with its age" "messaged:" "$out"

# (b) A SEND RECORDED, NOTHING MOVED. A FACT, and never a verdict.
out="$($QUEUE show "$ltopic/02-quiet" 2>&1)"
expect "a message with nothing moving since says exactly that" \
	"no commit since" "$out"
expect "and says the same of the transitions it folded" "no transition since" "$out"
expect "and refuses to turn that into a claim about the worker" \
	"not what the worker is doing" "$out"
for guess in "is stuck" "is dead" unreachable; do
	refute "and never guesses the worker $guess" "$guess" "$out"
done
expect "the record keeps the send itself, not a flag" "sends:" \
	"$(cat "$FLEET_QUEUE_DIR/$ltopic/02-quiet/task.yaml")"
expect "and the task is still exactly as dispatched — a message is not a
        completion" "state:       dispatched" "$out"
refute "with no outcome invented for it" "outcome:     shipped" "$out"

# (c) NO SEND EVER RECORDED. Today, and not "no movement": a question nobody
#     asked gets no answer, so an unmessaged queue reads as it always did.
out="$($QUEUE show "$ltopic/03-never" 2>&1)"
refute "a task nobody messaged says nothing about a message" "messaged" "$out"
refute "and nothing about a silence it was never asked to explain" \
	"no commit since" "$out"
row="$($QUEUE list --topic "$ltopic" 2>&1 | grep -A2 03-never)"
refute "and its row is the row it always was" "messaged" "$row"

# A branch this machine cannot read degrades to `not checked`, and a task that
# runs on a host is the case that matters: its git is over there.
messaged remote 04 cccccccc-0000-0000-0000-000000000004
git -C "$liverepo" branch feat/remote
python3 - "$FLEET_QUEUE_DIR/$ltopic/04-remote/task.yaml" <<'PY'
import sys

import yaml

path = sys.argv[1]
doc = yaml.safe_load(open(path))
doc["host"] = "devbox"
yaml.safe_dump(doc, open(path, "w"), sort_keys=False)
PY
$QUEUE send "$ltopic/04-remote" 'How is it going?' >/dev/null 2>&1
out="$($QUEUE show "$ltopic/04-remote" 2>&1)"
expect "a git this machine cannot read is 'not checked', never a false negative" \
	"commit not checked" "$out"
expect "and it names the host whose git it would have had to read" "devbox" "$out"

# A send that did not go in. `session-trust.sh` cannot get past a session the
# stub has never heard of, so nothing was typed — and that is the other half of
# "did it land", written down rather than guessed at from a silence.
$QUEUE attach "$ltopic/03-never" cccccccc-0000-0000-0000-0000000dead1 >/dev/null
out="$($QUEUE send "$ltopic/03-never" 'Are you there?' 2>&1)"
expect "a send that could not be delivered says so" "NOT DELIVERED" "$out"
out="$($QUEUE show "$ltopic/03-never" 2>&1)"
expect "and the record carries it, so a silence is never read as delivery" \
	"NOT DELIVERED" "$out"

# Once the task closes, the send is part of the RECORD and not part of what is
# happening: `collect` answered the question with a result file.
python3 - "$FLEET_QUEUE_DIR/$ltopic/02-quiet/task.yaml" <<'PY'
import sys

import yaml

path = sys.argv[1]
doc = yaml.safe_load(open(path))
doc["state"] = "done"
yaml.safe_dump(doc, open(path, "w"), sort_keys=False)
PY
row="$($QUEUE list --topic "$ltopic" 2>&1 | grep -A2 02-quiet)"
refute "a concluded task's row drops the liveness line" "no commit since" "$row"
out="$($QUEUE show "$ltopic/02-quiet" 2>&1)"
expect "and \`show\`, which is the record itself, keeps it" \
	"this task concluded" "$out"

# --- 19. the run log is produced, not remembered -----------------------------
#
# The gap this closes: `AGENTS.md` said "record the run in orchestration/runs/
# as it happens", and two consecutive runs did not. One was written only
# because its lead session was being migrated; the other was reconstructed from
# chat history after the fact. An instruction two leads failed the same way is
# a tool gap, so the queue writes the half it knows and leaves the half it
# cannot know alone.
#
# Back in the main queue: this run's topic has been through dispatch, collect,
# reap and shepherd by now, so the facts are real ones rather than a fixture's.

export FLEET_QUEUE_DIR="$tmp/queue"
runlog="$FLEET_RUNS_DIR/$(date -u +%F)-report-status-honestly.md"

# (a) Opening a topic is what opens the run log. Nobody asked for it.
if [ -f "$runlog" ]; then
	pass "topic add scaffolds a run log without being asked"
else
	fail "topic add scaffolds a run log without being asked" \
		"no $runlog${nl}$(ls -A "$FLEET_RUNS_DIR" 2>&1)"
fi

log="$(cat "$runlog" 2>/dev/null)"
expect "the run log names the topic it was opened for" \
	"Make thurbox report agent status honestly" "$log"
expect "and topic add said where it is, on stderr rather than in the value" \
	"$runlog" "$(cat "$tmp/topic-add.err")"
expect "and the prose sections the lead owns are already there" "## Outcome" "$log"
expect "and the generated block is fenced" "<!-- fleet:facts -->" "$log"

# (b) The facts the queue already knows are in it, without being retyped.
out="$($QUEUE collect 2>&1)"
log="$(cat "$runlog")"
expect "the facts block carries each task" "01-drop-idle-default" "$log"
expect "with the branch it runs on" "fix/document-the-states" "$log"
expect "and the artifact its worker reported" "/pull/1001" "$log"
expect "and the timeline says when it was dispatched" "dispatched" "$log"
expect "and the overlap that was accepted rather than serialized" \
	"Overlap on \`src/state.rs\`" "$log"

# (c) The lead's judgement is never clobbered — the whole reason the file
#     exists is the part no record can produce.
python3 - "$runlog" <<'PY'
import sys
p = sys.argv[1]
body = open(p).read().replace(
    "## Outcome", "## Outcome\n\nSerializing this topic would have been a mistake.", 1)
open(p, "w").write(body)
PY

$QUEUE shepherd --dry-run >/dev/null 2>&1
$QUEUE collect >/dev/null 2>&1
log="$(cat "$runlog")"
expect "prose the lead wrote survives every later refresh" \
	"Serializing this topic would have been a mistake." "$log"

# (d) A refresh REWRITES the block; it does not append to it. `collect` runs
#     many times over one run, and a line appended per pass is the timeline
#     nobody reads — this failure relocated rather than fixed.
before="$(grep -c 'dispatched' "$runlog")"
$QUEUE collect >/dev/null 2>&1
$QUEUE collect >/dev/null 2>&1
after="$(grep -c 'dispatched' "$runlog")"
if [ "$before" = "$after" ]; then
	pass "three refreshes leave the same file, not three copies of it"
else
	fail "three refreshes leave the same file, not three copies of it" \
		"$before dispatch line(s) became $after"
fi

# (e) The explicit verb, for a topic older than this feature and for a lead
#     that just wants the path.
out="$($QUEUE run 2>&1)"
expect "\`run\` names the log it maintains" "$runlog" "$out"

# (f) A log whose fence was removed is a log the lead took over. Nothing is
#     written into it again, and the queue says so rather than going quiet.
taken="$FLEET_RUNS_DIR/$(date -u +%F)-taken-over.md"
tk="$($QUEUE topic add taken-over --title 'Taken over' --prompt 'mine now' 2>/dev/null)"
grep -v 'fleet:facts' "$FLEET_RUNS_DIR/$(date -u +%F)-$tk.md" >"$taken.tmp"
mv "$taken.tmp" "$FLEET_RUNS_DIR/$(date -u +%F)-$tk.md"
echo "Every word of this is mine." >>"$FLEET_RUNS_DIR/$(date -u +%F)-$tk.md"
out="$($QUEUE run 2>&1)"
expect "a log with no generated block is reported, not rewritten" "left alone" "$out"
expect "and it keeps every word" "Every word of this is mine." \
	"$(cat "$FLEET_RUNS_DIR/$(date -u +%F)-$tk.md")"

# (g) Nothing machine-specific reaches the one file here that IS tracked.
if grep -qE '/home/|/Users/|[0-9a-f]{8}-[0-9a-f]{4}' orchestration/runs/_TEMPLATE.md; then
	fail "the tracked template carries no path and no session id" \
		"$(grep -nE '/home/|/Users/' orchestration/runs/_TEMPLATE.md)"
else
	pass "the tracked template carries no path and no session id"
fi

# --- 13. THE SEAM: the whole queue driven by a forge that is not GitHub ------
#
# A seam with one implementation is a claim. This is the second implementation:
# a forge with no network, no `gh` and no GitHub anywhere in it, that `collect`,
# `reap`'s landing check and `shepherd` are driven all the way through.
#
# It is also the regression test. `gh` on this section's PATH is a TRIPWIRE, not
# a stub — it logs the call and fails — so any code that reaches around
# `scripts/lib/forge.py` and runs `gh` directly again shows up here by name
# instead of quietly working on the operator's machine and nowhere else.
#
# What it proves, beyond "the calls go through the interface":
#
#   a self-hosted host with a PORT round-trips — identity is host + path, and
#     `forge.test:8443/acme/widgets` is not `github.com/acme/widgets`
#   a `/-/merge_requests/<n>` URL is a change request, the same as `/pull/<n>`
#   AUTO_MERGE_REPOS is matched host-qualified, and an entry naming no forge
#     is refused rather than matched against a bare slug
#   a repository is discovered from a checkout's `origin` through the same seam
#   a forge that cannot perform fleet's merge method SAYS SO, and nothing is
#     merged by some other method instead

fk="$tmp/fake-forge"
mkdir -p "$fk/crs" "$fk/push" "$fk/bin"
export FAKE_FORGE_DIR="$fk"
: >"$fk/gh-calls.log"
: >"$fk/merged.log"
printf '["squash"]\n' >"$fk/merge-methods.json"

# A TRIPWIRE. Nothing in this section may reach GitHub, so `gh` records who
# tried and then fails the way an unreachable API fails.
cat >"$fk/bin/gh" <<'SH'
#!/bin/sh
echo "gh $*" >>"$FAKE_FORGE_DIR/gh-calls.log"
echo "gh: nothing in the fake-forge section may reach GitHub" >&2
exit 1
SH
chmod +x "$fk/bin/gh"

# The second implementation. It answers the questions in scripts/lib/forge.py's
# header and knows nothing else — if it had to grow a field to keep the queue
# working, the seam would be in the wrong place and the fix would be to move
# the seam rather than to widen this.
cat >"$fk/forge_plugin.py" <<'PY'
"""A forge that is not GitHub: files on disk, no network, no CLI.

Deliberately shaped like the forge fleet does NOT run on. It is self-hosted
with a port, it spells a change request `/-/merge_requests/<n>`, and it can be
told it cannot squash — three of the ways a second adapter is expected to
differ.
"""

import json
import os
import re

import fleet_forge as fg

HOST = "forge.test:8443"
URL_RE = re.compile(r"^https://" + re.escape(HOST) + r"/(.+?)/-/merge_requests/(\d+)$")
REMOTE_RE = re.compile(r"^https://" + re.escape(HOST) + r"/(.+?)(?:\.git)?/?$")


def _dir():
    return os.environ["FAKE_FORGE_DIR"]


def _down():
    return os.path.exists(os.path.join(_dir(), "down"))


def _docs():
    out = []
    crs = os.path.join(_dir(), "crs")
    for name in sorted(os.listdir(crs)):
        if name.endswith(".json"):
            with open(os.path.join(crs, name)) as fh:
                out.append(json.load(fh))
    return out


class FakeForge(fg.Forge):
    name = "fake"
    hosts = (HOST,)

    @property
    def merge_methods(self):
        with open(os.path.join(_dir(), "merge-methods.json")) as fh:
            return tuple(json.load(fh))

    def parse_change_url(self, url):
        m = URL_RE.match((url or "").strip())
        if not m:
            return None
        return fg.ChangeRef(fg.RepoId(HOST, m.group(1)), int(m.group(2)), m.group(0))

    def repo_from_remote(self, remote_url):
        m = REMOTE_RE.match((remote_url or "").strip())
        return fg.RepoId(HOST, m.group(1)) if m else None

    def _find(self, ref):
        if _down():
            return None, "the fake forge is unreachable"
        for d in _docs():
            if d["number"] == ref.number and d["repo"] == ref.repo.path:
                return d, ""
        return None, f"no change request {ref.number} on {ref.repo}"

    def get(self, ref):
        d, why = self._find(ref)
        return (None, why) if why else (self._change_request(d, ref.repo), "")

    def state(self, ref):
        d, why = self._find(ref)
        return (None, why) if why else (d.get("state", "open"), "")

    def open_change_requests(self, repo):
        if _down():
            return [], "the fake forge is unreachable"
        return [
            self._change_request(d, repo)
            for d in _docs()
            if d["repo"] == repo.path and d.get("state", "open") == "open"
        ], ""

    def open_change_requests_in_checkout(self, path):
        repo = self.repo_from_remote(fg._git_remote(path))
        if repo is None:
            return [], "not a checkout of a fake-forge repository"
        return self.open_change_requests(repo)

    def _change_request(self, d, repo):
        n = d["number"]
        return fg.ChangeRequest(
            ref=fg.ChangeRef(repo, n, f"https://{HOST}/{repo.path}/-/merge_requests/{n}"),
            title=d.get("title", ""),
            state=d.get("state", "open"),
            body=d.get("body", ""),
            head_branch=d.get("head_branch", ""),
            base_branch=d.get("base_branch", "main"),
            head_sha=d.get("head_sha", ""),
            author=d.get("author", ""),
            mergeable=d.get("mergeable", "mergeable"),
            checks=[fg.Check(c[0], c[1]) for c in d.get("checks", [])],
            commits=[fg.Commit(c[0], c[1]) for c in d.get("commits", [])],
            head_is_ours=d.get("head_is_ours", True),
            head_location=d.get("head_location", ""),
        )

    def can_push(self, repo, login):
        if _down():
            return False, "the fake forge is unreachable"
        ok = os.path.exists(os.path.join(_dir(), "push", login))
        return ok, f"{login} {'may' if ok else 'may not'} push to {repo}"

    def describe_merge(self, method, delete_branch):
        return f"fake forge: {method}" + (" and delete the branch" if delete_branch else "")

    def merge(self, cr, method, delete_branch):
        if method not in self.merge_methods:
            return False, f"this project forbids {method} merges"
        with open(os.path.join(_dir(), "merged.log"), "a") as fh:
            fh.write(f"{cr.number} {method}\n")
        return True, f"{method}-merged on the fake forge"


def forges():
    return [FakeForge()]
PY

# One change request, written the way the fake forge stores them. The body it
# gets is what the pipeline would have written: the attestation naming THIS
# head, which is what both `collect` and the merge gate demand.
fake_cr() {
	python3 - "$fk/crs" "$@" <<'PY'
import json
import sys

out, n = sys.argv[1], int(sys.argv[2])
sha = f"{n:040d}"
steps = [{"step": s, "status": "completed"} for s in
         ("intent", "rebase", "review", "test", "document", "lint", "push")]
steps += [{"step": "pr", "status": "running"}, {"step": "ci", "status": "pending"}]
payload = json.dumps({"head_sha": sha, "steps": steps})
body = f"<!-- no-mistakes-pipeline-attestation:v1 {payload} -->\n\n" + "\n".join(
    f"## {h}\nx\n" for h in
    ("Intent", "What Changed", "Risk Assessment", "Testing", "Pipeline")
)
doc = {"number": n, "repo": "acme/widgets", "state": "open", "body": body,
       "title": f"change {n}", "base_branch": "main", "head_sha": sha,
       "author": "letur", "mergeable": "mergeable", "checks": [["gate", "passed"]]}
for pair in sys.argv[3:]:
    key, _, value = pair.partition("=")
    doc[key] = json.loads(value)
json.dump(doc, open(f"{out}/{n}.json", "w"))
PY
}

frepo="$fk/repo"
mkdir -p "$frepo"
git -C "$frepo" init -q -b main
git -C "$frepo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base
# The repository this checkout belongs to, discovered through the seam rather
# than recorded anywhere: no task below has to name it.
git -C "$frepo" remote add origin "https://forge.test:8443/acme/widgets.git"

touch "$fk/push/letur" # can push; `stranger` has no file here, so cannot

FPATH="$fk/bin:$tbxbin:$sshbin:$base_path"
fq() {
	env PATH="$FPATH" FLEET_QUEUE_DIR="$tmp/queue-fake" \
		FLEET_FORGE_PLUGINS="$fk/forge_plugin.py" \
		FLEET_AUTO_MERGE_REPOS="forge.test:8443/acme/widgets" \
		"$QUEUE" "$@"
}

ftopic="$(fq topic add on-another-forge --title 'Work on a forge that is not GitHub' \
	--prompt 'fleet must not assume GitHub')"

for spec in 01:landed:201 02:conflicting:202 03:green:203 04:foreign:204 06:cancelled-check:206; do
	IFS=: read -r n slug num <<<"$spec"
	fq add "$ftopic" "$slug" --title "A change that is $slug" --repo "$frepo" \
		--branch "fix/$slug" --number "$n" >/dev/null
	cat >"$tmp/queue-fake/$ftopic/$n-$slug/result.md" <<EOF
---
outcome: shipped
artifact: https://forge.test:8443/acme/widgets/-/merge_requests/$num
---
Shipped it.
EOF
done

# The branches only appear once each task is `add`ed and recorded as already
# shipped: `add` itself refuses a branch that already exists in the repo (a
# spawn thurbox could never cut a worktree for), and every task here is
# standing in for one whose worker already created and pushed its branch.
for br in landed conflicting green foreign cancelled-check; do git -C "$frepo" branch "fix/$br"; done

fake_cr 201 'head_branch="fix/landed"'
fake_cr 202 'head_branch="fix/conflicting"' 'mergeable="conflicting"'
fake_cr 203 'head_branch="fix/green"'
fake_cr 204 'head_branch="fix/foreign"' 'head_is_ours=false' \
	'head_location="a stranger'"'"'s fork"'
fake_cr 206 'head_branch="fix/cancelled-check"' 'checks=[["gate", "cancelled"]]'

# --- 13a. collect reads the change request through the seam ------------------

session_is aaaaaaaa-0000-0000-0000-000000000001 idle
fq attach "$ftopic/01-landed" aaaaaaaa-0000-0000-0000-000000000001 >/dev/null

out="$(fq collect 2>&1)"
expect "collect verifies a publish claim on a forge that is not GitHub" \
	"01-landed" "$out"
expect "and it read a /-/merge_requests/ URL as a change request" \
	"merge_requests/201" "$out"
refute "its pull request is open, so nothing was reaped" "reaped" "$out"

# A change request nobody can read is `unknown`, never `missing` — the fourth
# word has to survive the seam, or an unreachable forge starts holding tasks
# open on evidence nobody has.
fq add "$ftopic" unreadable --title 'One the forge cannot answer for' \
	--repo "$frepo" --branch fix/unreadable --number 05 >/dev/null
cat >"$tmp/queue-fake/$ftopic/05-unreadable/result.md" <<'EOF'
---
outcome: shipped
artifact: https://forge.test:8443/acme/widgets/-/merge_requests/999
---
Shipped it; the forge cannot be asked about it from here.
EOF
out="$(fq collect 2>&1)"
expect "a change request the forge cannot answer for degrades to unknown" \
	"unchecked" "$out"
expect "and says what the forge said" "no change request 999" "$out"

# --- 13b. the landing check asks the same seam -------------------------------

out="$(fq reap --dry-run 2>&1)"
refute "an open change request lands nothing" "would be landed" "$out"

fake_cr 201 'head_branch="fix/landed"' 'state="merged"'
out="$(fq reap 2>&1)"
expect "a merge on the fake forge lands the task" "landed" "$out"
expect "and releases the session that produced it" "reaped" "$out"
expect "and it is the session the record held" \
	"aaaaaaaa-0000-0000-0000-000000000001" "$(cat "$deletions")"

# --- 13c. shepherd, all the way through --------------------------------------

out="$(fq shepherd --topic "$ftopic" --dry-run 2>&1)"
expect "shepherd names the self-hosted repository, port and all" \
	"acme/widgets on forge.test:8443" "$out"
expect "and says what it would merge, in the fake forge's own words" \
	"fake forge: squash" "$out"
if [ -s "$fk/merged.log" ]; then
	fail "a dry run merges nothing on the fake forge" "$(cat "$fk/merged.log")"
else
	pass "a dry run merges nothing on the fake forge"
fi

out="$(fq shepherd --topic "$ftopic" 2>&1)"
if grep -q '^203 squash$' "$fk/merged.log"; then
	pass "a green, attested change request is merged through the seam"
else
	fail "a green, attested change request is merged through the seam" \
		"$out$nl$(cat "$fk/merged.log")"
fi
expect "a conflicting one gets a fixer, in the base branch's own terms" \
	"conflicts with main" "$out"
expect "and the fixer is dispatched" "dispatched:" "$out"
expect "one whose head is not ours is left alone" "left-alone" "$out"
expect "and named as where the forge said it lives" "a stranger's fork" "$out"
refute "and a change request that is not ours is never merged" \
	"204 " "$(cat "$fk/merged.log")"

# A CANCELLED check is not a FAILED one: the shepherd's own long-standing
# reading, kept alive through the seam rather than collapsed into
# fleet-status's stricter one now that both read the same `checks` field.
expect "a cancelled check reads as still running, not as a failed one" \
	"undetermined: checks still running: gate" "$out"
refute "so it never gets a fixer for a failed check" \
	"206" "$(cat "$fk/merged.log")"

# --- 13d. a forge that cannot do fleet's merge method says so ----------------
#
# THE MISMATCH WORTH CATCHING BEFORE A SECOND ADAPTER EXISTS. Fleet merges by
# squash because that is the only method its own remotes allow, and a project
# on another forge can forbid exactly that. "The forge refused this merge
# method" has to be a sentence the interface can say, and it has to be said
# BEFORE the merge rather than after one that quietly used another method.

printf '["merge"]\n' >"$fk/merge-methods.json"
fake_cr 205 'head_branch="fix/green"'
before="$(wc -l <"$fk/merged.log")"
out="$(fq shepherd --topic "$ftopic" 2>&1)"
expect "a forge that cannot squash says so rather than merging some other way" \
	"cannot merge by squash" "$out"
count_is "and nothing is merged while it cannot" "$(wc -l <"$fk/merged.log")" \
	"$before" "$out"
printf '["squash"]\n' >"$fk/merge-methods.json"

# --- 13e. the allowlist is host-qualified, and a bare slug is not a match ----

out="$(env PATH="$FPATH" FLEET_QUEUE_DIR="$tmp/queue-fake" \
	FLEET_FORGE_PLUGINS="$fk/forge_plugin.py" \
	FLEET_AUTO_MERGE_REPOS="acme/widgets" \
	"$QUEUE" shepherd --topic "$ftopic" --dry-run 2>&1)"
expect "an auto-merge entry that names no forge is refused, not matched" \
	"must name its forge" "$out"
refute "and nothing under it would be merged" "would-merge" "$out"

# --- 13f. the regression test: nobody reached around the seam ----------------

if [ -s "$fk/gh-calls.log" ]; then
	fail "no code path ran \`gh\` while a different forge was configured" \
		"$(cat "$fk/gh-calls.log")"
else
	pass "no code path ran \`gh\` while a different forge was configured"
fi

# --- 14. GITLAB: the second REAL adapter, over recorded `glab` output --------
#
# Section 13 proves the seam with a forge that exists only in that section.
# This proves the adapter fleet actually ships for GitLab, and it is a
# different claim: the fake forge answers whatever fleet asks, while `glab`
# answers what GitLab decided to answer, in GitLab's own words and shapes.
#
# So the fixtures matter more than the code here. `scripts/fixtures/glab/` is
# real `glab` 1.117.0 output — its README says which command produced each
# file, where it was recorded and what was edited out of it, and which two
# answers are behind authentication and therefore CONSTRUCTED below rather
# than recorded. A fake `glab` on PATH replays them; nothing in this section
# reaches a network, and `gh` is a tripwire again, because a GitLab merge
# request is the one thing that must never be asked about with `gh`.
#
# What it proves:
#
#   the recorded shapes parse — a fork's merge request is read as NOT ours,
#     commits come back oldest-first out of a newest-first answer, and glab's
#     two-stream error is read from the stream that carries the reason
#   a pipeline for a commit that is no longer the head is NO check, not a pass
#   a self-hosted instance round-trips: `GITLAB_HOST`, a subgroup path, and
#     every call naming its host by full URL
#   `squash_option: never` is a refusal fleet RECORDS, not a crash and not a
#     merge by some other method
#   the remote-host credential probe asks the repository's own forge
#   WHICH hosts are GitLab is read off the machine rather than waited for,
#     and the whole loop runs on an instance discovered that way

gl="$tmp/gitlab"
mkdir -p "$gl/mrs" "$gl/api" "$gl/bin"
export FAKE_GLAB_DIR="$gl"
: >"$gl/calls.log"
: >"$gl/merged.log"
: >"$gl/gh-calls.log"

fixtures="$PWD/scripts/fixtures/glab"

# A `glab` that reads files instead of an instance. It knows only the four
# verbs the adapter uses, and it is deliberately literal about the two things
# recorded output taught us: `--jq .state` prints a bare word, and a failure
# puts its reason on STDOUT as JSON with a decorated box on stderr.
cat >"$gl/bin/glab" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys

D = os.environ["FAKE_GLAB_DIR"]
argv = sys.argv[1:]
with open(os.path.join(D, "calls.log"), "a") as fh:
    fh.write(" ".join(argv) + "\n")


def flag(name, default=None):
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def emit(doc):
    sys.stdout.write(json.dumps(doc) + "\n")
    raise SystemExit(0)


def refuse(message, recorded=None):
    # A refusal is two streams, and which one carries the REASON is the thing
    # an adapter gets wrong. `recorded` replays the pair exactly as glab wrote
    # it; everything else is built to the same shape.
    if recorded and os.path.exists(recorded + ".json"):
        sys.stdout.write(open(recorded + ".json").read())
        sys.stderr.write(open(recorded + ".stderr").read())
        raise SystemExit(1)
    sys.stdout.write(json.dumps({"error": {"message": message}}) + "\n")
    sys.stderr.write("\n          \n   ERROR  \n          \n  %s\n\n" % message)
    raise SystemExit(1)


def load(number):
    path = os.path.join(D, "mrs", "%s.json" % number)
    return json.load(open(path)) if os.path.exists(path) else None


def opened():
    out = []
    for name in sorted(os.listdir(os.path.join(D, "mrs"))):
        if not name.endswith(".json"):
            continue
        doc = json.load(open(os.path.join(D, "mrs", name)))
        if doc.get("state") == "opened":
            out.append(doc)
    return out


if argv[:2] == ["mr", "view"]:
    doc = load(argv[2])
    if doc is None:
        refuse("failed to get merge request %s: 404 Not Found" % argv[2],
               recorded=os.path.join(D, "missing")
               if argv[2] == "999999" else None)
    if flag("--jq") == ".state":
        sys.stdout.write(str(doc.get("state") or "") + "\n")
        raise SystemExit(0)
    emit(doc)

if argv[:2] == ["mr", "list"]:
    emit([] if int(flag("--page", "1")) > 1 else opened())

if argv[:2] == ["mr", "merge"]:
    if os.path.exists(os.path.join(D, "merge-refused")):
        refuse("405 Method Not Allowed")
    with open(os.path.join(D, "merged.log"), "a") as fh:
        fh.write(" ".join(argv) + "\n")
    sys.stdout.write("Merged!\n")
    raise SystemExit(0)

if argv[:2] == ["auth", "status"]:
    # Where the host list comes from. The recording is replayed only when this
    # section has put one in the store, so 14a keeps asking the adapter what a
    # machine with NO GitLab configuration owns — and the refusal below is
    # what that machine looks like. The exit code is 1 either way, which is
    # what `glab` does whenever any one instance has no token.
    served = os.path.join(D, "auth-status.stderr")
    if not os.path.exists(served):
        refuse("no GitLab instance is configured")
    sys.stderr.write(open(served).read())
    raise SystemExit(1)

if argv[:1] == ["api"]:
    path = argv[1].split("?")[0]
    if path.endswith("/commits"):
        name = "commits"
    elif "/members/all" in path:
        name = "members"
    elif path.count("/") == 1:
        name = "project"
    else:
        refuse("404 Not Found")
    served = os.path.join(D, "api", name + ".json")
    if not os.path.exists(served):
        refuse("404 Not Found")
    sys.stdout.write(open(served).read())
    raise SystemExit(0)

refuse("unknown command: %s" % " ".join(argv))
PY
chmod +x "$gl/bin/glab"

cat >"$gl/bin/gh" <<'SH'
#!/bin/sh
echo "gh $*" >>"$FAKE_GLAB_DIR/gh-calls.log"
echo "gh: a GitLab merge request must never be asked about with gh" >&2
exit 1
SH
chmod +x "$gl/bin/gh"

# The recorded fork merge request, under its own number, exactly as recorded —
# and the recorded refusal, replayed on both streams for merge request 999999,
# which is the number it was recorded against.
cp "$fixtures/mr-view.json" "$gl/mrs/3877.json"
cp "$fixtures/mr-commits.json" "$gl/api/commits.json"
cp "$fixtures/mr-view-missing.json" "$gl/missing.json"
cp "$fixtures/mr-view-missing.stderr" "$gl/missing.stderr"

# CONSTRUCTED, and labelled: `GET /projects/:id` and `/members/all` are behind
# authentication, so these carry the field names from GitLab's REST API
# documentation and values this test chooses. The README beside the recordings
# says so too.
printf '{"id": 42, "path_with_namespace": "acme/group/widgets", "squash_option": "default_on"}\n' \
	>"$gl/api/project.json"
printf '[{"id": 7, "username": "letur", "access_level": 40}]\n' >"$gl/api/members.json"

# Every merge request below is DERIVED FROM THE RECORDED ONE: the recorded
# object is loaded and named fields are overwritten, so each fixture keeps the
# real shape and only the facts under test are this test's invention.
# `GLAB_MR_DIR` aims it at a second store, which 14e wants so that its queue
# sees its own merge requests and none of this section's.
glab_mr() {
	python3 - "$fixtures/mr-view.json" "${GLAB_MR_DIR:-$gl/mrs}" "$@" <<'PY'
import json
import sys

recorded, out, number = sys.argv[1], sys.argv[2], int(sys.argv[3])
doc = json.load(open(recorded))
sha = "%040d" % number
steps = [{"step": s, "status": "completed"} for s in
         ("intent", "rebase", "review", "test", "document", "lint", "push")]
steps += [{"step": "pr", "status": "running"}, {"step": "ci", "status": "pending"}]
payload = json.dumps({"head_sha": sha, "steps": steps})
doc.update({
    "iid": number,
    "id": 900000 + number,
    "web_url": "https://gitlab.example.com/acme/group/widgets"
               "/-/merge_requests/%d" % number,
    "project_id": 42,
    "source_project_id": 42,
    "target_project_id": 42,
    "state": "opened",
    "draft": False,
    "sha": sha,
    "title": "change %d" % number,
    "target_branch": "main",
    "has_conflicts": False,
    "detailed_merge_status": "mergeable",
    "author": {"id": 7, "username": "letur", "name": "letur", "state": "active"},
    "description": "<!-- no-mistakes-pipeline-attestation:v1 %s -->\n\n" % payload
                   + "\n".join("## %s\nx\n" % h for h in
                               ("Intent", "What Changed", "Risk Assessment",
                                "Testing", "Pipeline")),
    "head_pipeline": {"id": 5000 + number, "name": "", "sha": sha,
                      "status": "success"},
})
for pair in sys.argv[4:]:
    key, _, value = pair.partition("=")
    doc[key] = json.loads(value)
json.dump(doc, open("%s/%d.json" % (out, number), "w"))
PY
}

# --- 14a. the recorded shapes parse, and the fork is read as not ours -------

GLPATH="$gl/bin:$base_path"
env PATH="$GLPATH" FAKE_GLAB_DIR="$gl" python3 - "$PWD/scripts/lib" >"$tmp/gl-unit.tsv" <<'PY'
import json
import os
import sys

sys.path.insert(0, sys.argv[1])
import forge  # noqa: E402

rows = []


def claim(name, got, want):
    rows.append(("PASS", name, "") if got == want
                else ("FAIL", name, "wanted %r, got %r" % (want, got)))


gl = forge.GitLabForge()

# The recorded merge request, through the public interface and the fake CLI.
ref = gl.parse_change_url("https://gitlab.com/gitlab-org/cli/-/merge_requests/3877")
claim("a /-/merge_requests/ URL on gitlab.com is a change request", ref is not None, True)
cr, why = gl.get(ref)
claim("and glab answers for it", why, "")
claim("its head commit is the recorded one", cr.head_sha,
      "c152195ba6b110064690fca331b186c55a674fdf")
claim("its head branch is the recorded one", cr.head_branch, "patch-1")
claim("its base branch is the recorded one", cr.base_branch, "main")
claim("GitLab's `opened` is fleet's `open`", cr.state, "open")
claim("a merge request from a FORK is not ours", cr.head_is_ours, False)
claim("and the refusal line says where it lives",
      "another project on gitlab.com" in cr.head_location, True)
claim("its failed pipeline is one failed check",
      [(c.verdict) for c in cr.checks], ["failed"])
claim("an undocumented detailed_merge_status is not read as mergeable",
      cr.mergeable, "")
claim("commits come back oldest-first out of a newest-first answer",
      [c.headline for c in cr.commits][0],
      "chore(lint): add comment volume and overlap scripts")
claim("and the newest recorded commit is last",
      [c.headline for c in cr.commits][-1],
      "refactor: fix gocritic findings and delete comments that restate the code")

# The recorded error: the reason is on stdout, and stderr's first line is a box.
missing = forge.ChangeRef(ref.repo, 999999,
                          "https://gitlab.com/gitlab-org/cli/-/merge_requests/999999")
gone, why = gl.get(missing)
claim("a merge request that is not there is a reason, not an exception", gone, None)
claim("and the reason is the one glab put on stdout", "404 Not Found" in why, True)
claim("not the decorated box it put on stderr", "ERROR" in why, False)

# Hosts.
claim("a github.com pull request is not this adapter's",
      gl.parse_change_url("https://github.com/Thurbeen/fleet/pull/1"), None)
claim("nor is a single-segment path, which GitLab has no such thing as",
      gl.parse_change_url("https://gitlab.com/project/-/merge_requests/1"), None)
claim("an unconfigured self-hosted host is not ours either",
      gl.parse_change_url(
          "https://gitlab.example.com/acme/group/widgets/-/merge_requests/9"), None)

os.environ["GITLAB_HOST"] = "https://gitlab.example.com/"
selfhosted = forge.GitLabForge()
ref = selfhosted.parse_change_url(
    "https://gitlab.example.com/acme/group/widgets/-/merge_requests/301")
claim("GITLAB_HOST configures a self-hosted instance, scheme and all",
      ref is not None, True)
claim("and a subgroup path is the whole path", ref.repo.path, "acme/group/widgets")
claim("whose first segment is the owner", ref.repo.owner, "acme")
for remote in ("https://gitlab.example.com/acme/group/widgets.git",
               "git@gitlab.example.com:acme/group/widgets.git",
               "ssh://git@gitlab.example.com/acme/group/widgets"):
    claim("a checkout's origin names the project: %s" % remote,
          selfhosted.repo_from_remote(remote),
          forge.RepoId("gitlab.example.com", "acme/group/widgets"))

# Pipelines. Each case is written into the fake CLI's own store and read back
# through `get`, so the interface under test is the one the queue calls.
mrs = os.path.join(os.environ["FAKE_GLAB_DIR"], "mrs")
recorded = json.load(open(os.path.join(mrs, "3877.json")))


def pipeline_verdicts(pipeline):
    doc = dict(recorded, iid=401, web_url=ref.url.replace("301", "401"),
               source_project_id=42, target_project_id=42, head_pipeline=pipeline)
    json.dump(doc, open(os.path.join(mrs, "401.json"), "w"))
    got, _why = selfhosted.get(forge.ChangeRef(ref.repo, 401, doc["web_url"]))
    return [c.verdict for c in got.checks]


head = recorded["sha"]
claim("a pipeline for a commit that is no longer the head is no check at all",
      pipeline_verdicts({"id": 1, "sha": "0" * 40, "status": "success"}), [])
claim("and a merge request with no pipeline at all is no check either",
      pipeline_verdicts(None), [])
for said, want in (("success", "passed"), ("skipped", "passed"), ("failed", "failed"),
                   ("canceled", "cancelled"), ("running", "pending"),
                   ("manual", "pending"), ("created", "pending")):
    claim("pipeline %s reads as %s" % (said, want),
          pipeline_verdicts({"id": 1, "sha": head, "status": said}), [want])

for verdict, name, detail in rows:
    print("%s\t%s\t%s" % (verdict, name, detail))
PY

while IFS=$'\t' read -r verdict claim detail; do
	if [ "$verdict" = PASS ]; then pass "$claim"; else fail "$claim" "$detail"; fi
done <"$tmp/gl-unit.tsv"

# --- 14b. the whole queue, driven through the GitLab adapter -----------------
#
# The recorded merge request and the pipeline cases above belong to gitlab.com
# and to no task; take them out of the fake CLI's store before the queue is
# asked what is open, and start the call log over so that the "nothing was
# aimed at gitlab.com" claim below is about this half of the section.

rm -f "$gl/mrs/3877.json" "$gl/mrs/401.json"
: >"$gl/calls.log"

glrepo="$gl/repo"
mkdir -p "$glrepo"
git -C "$glrepo" init -q -b main
git -C "$glrepo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base
git -C "$glrepo" remote add origin "https://gitlab.example.com/acme/group/widgets.git"

glq() {
	env PATH="$gl/bin:$tbxbin:$sshbin:$base_path" FLEET_QUEUE_DIR="$tmp/queue-gitlab" \
		GITLAB_HOST=gitlab.example.com \
		FLEET_AUTO_MERGE_REPOS="gitlab.example.com/acme/group/widgets" \
		"$QUEUE" "$@"
}

gltopic="$(glq topic add on-gitlab --title 'Work on a self-hosted GitLab' \
	--prompt 'fleet must work on GitLab too')"

for spec in 01:landed:301 02:conflicting:302 03:green:303 04:foreign:304; do
	IFS=: read -r n slug num <<<"$spec"
	glq add "$gltopic" "$slug" --title "A change that is $slug" --repo "$glrepo" \
		--branch "fix/$slug" --number "$n" >/dev/null
	cat >"$tmp/queue-gitlab/$gltopic/$n-$slug/result.md" <<EOF
---
outcome: shipped
artifact: https://gitlab.example.com/acme/group/widgets/-/merge_requests/$num
---
Shipped it.
EOF
done
for br in landed conflicting green foreign; do git -C "$glrepo" branch "fix/$br"; done

glab_mr 301 'source_branch="fix/landed"'
glab_mr 302 'source_branch="fix/conflicting"' 'has_conflicts=true' \
	'detailed_merge_status="conflict"'
glab_mr 303 'source_branch="fix/green"'
glab_mr 304 'source_branch="fix/foreign"' 'source_project_id=99'

session_is bbbbbbbb-0000-0000-0000-000000000001 idle
glq attach "$gltopic/01-landed" bbbbbbbb-0000-0000-0000-000000000001 >/dev/null

out="$(glq collect 2>&1)"
expect "collect verifies a publish claim on a self-hosted GitLab" "01-landed" "$out"
expect "and it read a /-/merge_requests/ URL as a change request" \
	"merge_requests/301" "$out"
refute "its merge request is open, so nothing was reaped" "reaped" "$out"

python3 - "$gl/mrs/301.json" <<'PY'
import json
import sys
doc = json.load(open(sys.argv[1]))
doc["state"] = "merged"
json.dump(doc, open(sys.argv[1], "w"))
PY
out="$(glq reap 2>&1)"
expect "a merged merge request lands the task" "landed" "$out"
expect "and releases the session that produced it" "reaped" "$out"

out="$(glq shepherd --topic "$gltopic" --dry-run 2>&1)"
expect "shepherd names the self-hosted project, subgroup and all" \
	"acme/group/widgets on gitlab.example.com" "$out"
expect "and says what it would run, in glab's own flags" \
	"glab mr merge --yes --auto-merge=false --squash" "$out"

out="$(glq shepherd --topic "$gltopic" 2>&1)"
if grep -q 'mr merge 303' "$gl/merged.log"; then
	pass "a green, attested merge request is merged through the adapter"
else
	fail "a green, attested merge request is merged through the adapter" \
		"$out$nl$(cat "$gl/merged.log")"
fi
expect "and the merge names the exact head it checked, so a race cannot slip in" \
	"--sha 0000000000000000000000000000000000000303" "$(cat "$gl/merged.log")"
expect "a conflicting one gets a fixer, in the base branch's own terms" \
	"conflicts with main" "$out"
expect "one whose head is in another project is left alone" "left-alone" "$out"
refute "and is never merged" "mr merge 304" "$(cat "$gl/merged.log")"

# Every call carried the host. This is the whole self-hosted claim: a slug
# would have reached gitlab.com, and `RepoId` is host plus path for this reason.
refute "no call was ever aimed at gitlab.com" "gitlab.com" "$(cat "$gl/calls.log")"
expect "every call named the self-hosted instance by full URL" \
	"-R https://gitlab.example.com/acme/group/widgets" "$(cat "$gl/calls.log")"

# --- 14c. a project that forbids squash is a refusal, not a crash ------------
#
# GitLab's `squash` is not a merge method — it is a flag on the merge, and
# `squash_option: never` is the PROJECT setting that forbids it. So the
# mismatch section 13d proves at the forge level has a second form here, per
# project, and it must still be a sentence fleet records rather than a merge
# by whatever method the project does allow.

printf '{"id": 42, "path_with_namespace": "acme/group/widgets", "squash_option": "never"}\n' \
	>"$gl/api/project.json"
glab_mr 305 'source_branch="fix/green"'
before="$(wc -l <"$gl/merged.log")"
out="$(glq shepherd --topic "$gltopic" 2>&1)"
expect "a project configured against squash says so in its own words" \
	"squash_option: never" "$out"
count_is "and nothing is merged while it forbids it" "$(wc -l <"$gl/merged.log")" \
	"$before" "$out"
printf '{"id": 42, "path_with_namespace": "acme/group/widgets", "squash_option": "default_on"}\n' \
	>"$gl/api/project.json"

if [ -s "$gl/gh-calls.log" ]; then
	fail "no code path ran \`gh\` against a GitLab merge request" \
		"$(cat "$gl/gh-calls.log")"
else
	pass "no code path ran \`gh\` against a GitLab merge request"
fi

# --- 14d. the remote-host probe asks the REPOSITORY's forge, not github.com --
#
# The credential probe is plain shell that runs on somebody else's machine, so
# it is run here exactly as that machine runs it, with `git` and `ssh` stubbed.
# It used to name github.com flatly, which passes on a host that cannot reach
# the GitLab instance the checkout actually pushes to.

probe="$tmp/probe"
mkdir -p "$probe/bin"
python3 - "$PWD/scripts/lib/queue.py" >"$probe/probe.sh" <<'PY'
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("fleet_queue_probe", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
sys.stdout.write(mod.forge_probe("/srv/code/app"))
PY
cat >"$probe/bin/git" <<'SH'
#!/bin/sh
cat "$PROBE_ORIGIN" 2>/dev/null || exit 1
SH
cat >"$probe/bin/ssh" <<'SH'
#!/bin/sh
echo "ssh $*" >>"$PROBE_LOG"
cat "$PROBE_BANNER" 2>/dev/null
exit 1
SH
chmod +x "$probe/bin/git" "$probe/bin/ssh"

probe_says() {
	: >"$probe/ssh.log"
	printf '%s\n' "$1" >"$probe/origin"
	printf '%s\n' "$2" >"$probe/banner"
	env PATH="$probe/bin:$base_path" PROBE_ORIGIN="$probe/origin" \
		PROBE_BANNER="$probe/banner" PROBE_LOG="$probe/ssh.log" \
		sh "$probe/probe.sh"
}

expect "the probe proves a GitLab host against GitLab's own welcome" \
	"gitlab.example.com with an ssh key" \
	"$(probe_says 'git@gitlab.example.com:acme/group/widgets.git' \
		'Welcome to GitLab, @letur!')"
expect "and it asked THAT host, not github.com" "git@gitlab.example.com" \
	"$(cat "$probe/ssh.log")"
expect "a self-hosted instance on a port is asked on that port" "-p 2222" \
	"$(probe_says 'ssh://git@gitlab.example.com:2222/acme/widgets.git' \
		'Welcome to GitLab, @letur!' >/dev/null; cat "$probe/ssh.log")"
expect "GitHub's own banner still passes, unchanged" \
	"github.com with an ssh key" \
	"$(probe_says 'git@github.com:Thurbeen/fleet.git' \
		"Hi letur! You've successfully authenticated")"
expect "a repo whose origin cannot be read says THAT, not 'no credentials'" \
	"has no readable" \
	"$(probe_says '' 'Welcome to GitLab, @letur!' 2>&1)"


# --- 14e. which hosts are GitLab: DISCOVERED, not waited for -----------------
#
# Everything above this line sets `GITLAB_HOST`, and that is what hid the
# defect this section is about: nothing on a real machine exports it. With it
# unset, a merge request on a self-hosted instance was on no configured forge,
# so `shepherd` never listed it, `collect` could not verify a publish there,
# and `reap` could never move the task to `landed` — which means its session
# and its worktree were never released, once per task, with no upper bound.
#
# The answer was already on the machine: `glab auth status` prints every
# instance the operator logged their CLI in to. `forge.configured_hosts` reads
# it, and `scripts/fixtures/glab/auth-status.stderr` is that report, recorded
# and then stripped of the operator's own names — its README says so.
#
# What this proves, all of it offline, with `gh` still a tripwire:
#
#   a self-hosted instance glab holds is OURS with no GITLAB_HOST set, and the
#     whole loop reaches it — collect verifies a publish, reap lands the task,
#     shepherd lists what is still open
#   `GITLAB_HOST` still DECIDES when it is set: it is the answer, and an
#     instance glab holds is not ours while it names another
#   no glab, and a configuration or a report it cannot read, each leave the
#     adapter exactly where it was — gitlab.com and nothing else
#   a glab too old for `--all` is asked again without the flag, so an older
#     CLI still discovers the instances it holds
#   AUTO_MERGE_REPOS is untouched by discovery: a green, attested, mergeable
#     merge request on a discovered host is REPORTED and never merged

gl2="$tmp/gitlab-discovered"
mkdir -p "$gl2/mrs" "$gl2/api"
cp "$gl/api/project.json" "$gl/api/members.json" "$gl2/api/"
cp "$fixtures/auth-status.stderr" "$gl2/auth-status.stderr"
: >"$gl2/calls.log"
: >"$gl2/merged.log"
: >"$gl2/gh-calls.log"

env PATH="$GLPATH" FAKE_GLAB_DIR="$gl2" python3 - "$PWD/scripts/lib" "$gl2" \
	>"$tmp/gl-hosts.tsv" <<'PYHOSTS'
import os
import sys

sys.path.insert(0, sys.argv[1])
store = sys.argv[2]
import forge  # noqa: E402

rows = []


def claim(name, got, want):
    rows.append(("PASS", name, "") if got == want
                else ("FAIL", name, "wanted %r, got %r" % (want, got)))


real_path = os.environ["PATH"]


def adapter(gitlab_host=None, path=None):
    """A fresh adapter on a machine described by `gitlab_host` and `path`.

    Every case below is a different machine answering a different way, inside
    one process: discovery is not cached, so each adapter asks the `glab` its
    own PATH holds and gets that machine's answer.
    """
    forge.reset()
    os.environ.pop("GITLAB_HOST", None)
    if gitlab_host:
        os.environ["GITLAB_HOST"] = gitlab_host
    os.environ["PATH"] = real_path if path is None else path
    return forge.GitLabForge()


def cli_dir(name, script):
    """A PATH holding one `glab` that behaves as `script` says — or none.

    `/usr/bin` and `/bin` come after it so the stub has the ordinary tools,
    and NOT the rest of this file's PATH, which carries two other `glab`s.
    """
    path = os.path.join(store, name)
    os.makedirs(path, exist_ok=True)
    glab = os.path.join(path, "glab")
    if not script:
        if os.path.exists(glab):
            os.remove(glab)
        return path
    with open(glab, "w") as fh:
        fh.write(script)
    os.chmod(glab, 0o755)
    return os.pathsep.join([path, "/usr/bin", "/bin"])


# --- the recording, read the way a machine's own `glab` would be read ---
found = adapter()
claim("both instances in the recording are read out of it",
      forge.configured_hosts("glab"), ["gitlab.com", "gitlab.example.com"])
claim("so a self-hosted instance is ours with no GITLAB_HOST set",
      found.owns_host("gitlab.example.com"), True)
claim("and its merge request is a change request fleet can be asked about",
      found.parse_change_url(
          "https://gitlab.example.com/acme/group/widgets/-/merge_requests/301"
      ) is not None, True)
claim("an instance nothing on this machine holds is still not ours",
      found.owns_host("gitlab.nowhere.example"), False)

# --- GITLAB_HOST still decides ---
named = adapter(gitlab_host="https://gitlab.other.example/")
claim("GITLAB_HOST set is still the answer, scheme and all",
      named.owns_host("gitlab.other.example"), True)
claim("and it DECIDES: an instance glab holds is not ours while it is set",
      named.owns_host("gitlab.example.com"), False)

# --- and discovery is never a requirement ---
claim("a machine with no glab at all is exactly where it was",
      adapter(path=cli_dir("no-cli", "")).hosts, ("gitlab.com", "www.gitlab.com"))
claim("nor does a configuration glab cannot read move it", adapter(path=cli_dir(
    "unreadable",
    "#!/bin/sh\n"
    "echo 'failed to parse config.yml: yaml: line 3: could not find expected key' >&2\n"
    "exit 1\n",
)).hosts, ("gitlab.com", "www.gitlab.com"))
claim("nor a glab that answers something that is not a host list", adapter(path=cli_dir(
    "garbled",
    "#!/bin/sh\nprintf 'Logged in somewhere, probably\\n'\nexit 0\n",
)).hosts, ("gitlab.com", "www.gitlab.com"))

# A `glab` too old for `--all` refuses the whole command, so the bare form is
# what has to answer — and on a machine with no git context it answers the
# same thing. Without that second try an older CLI discovers nothing.
claim("a glab too old for --all is asked again without it", adapter(path=cli_dir(
    "old",
    "#!/bin/sh\n"
    'case "$*" in *--all*) echo "unknown flag: --all" >&2; exit 1 ;; esac\n'
    'cat "$FAKE_GLAB_DIR/auth-status.stderr" >&2\nexit 1\n',
)).owns_host("gitlab.example.com"), True)

for verdict, name, detail in rows:
    print("%s\t%s\t%s" % (verdict, name, detail))
PYHOSTS

while IFS=$'\t' read -r verdict claim detail; do
	if [ "$verdict" = PASS ]; then pass "$claim"; else fail "$claim" "$detail"; fi
done <"$tmp/gl-hosts.tsv"

# --- the whole loop, on a discovered host, with no GITLAB_HOST anywhere ------

gdq() {
	env -u GITLAB_HOST PATH="$gl/bin:$tbxbin:$sshbin:$base_path" \
		FAKE_GLAB_DIR="$gl2" FLEET_QUEUE_DIR="$tmp/queue-discovered" \
		"$QUEUE" "$@"
}

gdtopic="$(gdq topic add discovered --title 'Work on an instance glab already holds' \
	--prompt 'no GITLAB_HOST is exported anywhere')"
gdq add "$gdtopic" shipped --title 'A change already shipped' --repo "$glrepo" \
	--branch fix/discovered --number 01 >/dev/null
cat >"$tmp/queue-discovered/$gdtopic/01-shipped/result.md" <<'EOF'
---
outcome: shipped
artifact: https://gitlab.example.com/acme/group/widgets/-/merge_requests/306
---
Shipped it.
EOF

GLAB_MR_DIR="$gl2/mrs" glab_mr 306 'source_branch="fix/discovered"'

session_is cccccccc-0000-0000-0000-000000000001 idle
gdq attach "$gdtopic/01-shipped" cccccccc-0000-0000-0000-000000000001 >/dev/null

# `publish verified` is printed only when the publish check PASSED, which
# needs a forge that owns the host. Undiscovered, the task is still concluded
# and its URL still printed — it is the verdict that degrades to
# `publish unchecked: ... no configured forge owns gitlab.example.com`, which
# is the pre-fix symptom itself. So the marker is what this asserts.
out="$(gdq collect 2>&1)"
expect "collect verifies a publish on an instance only glab knew about" \
	"publish verified" "$out"
refute "and does not report it as on no configured forge" \
	"publish unchecked" "$out"
refute "which is not the same as landing it" "reaped" "$out"

# The merge set is host-qualified and discovery adds nothing to it. 306 is
# green, attested, mergeable and opened by someone who can push — every gate
# the shepherd has — and on a discovered host it is still only REPORTED.
before="$(wc -l <"$gl2/merged.log")"
out="$(gdq shepherd 2>&1)"
expect "shepherd lists what is open on the discovered instance" \
	"acme/group/widgets on gitlab.example.com" "$out"
expect "and a mergeable, attested one there is handed back, not merged" \
	"fleet does not merge in acme/group/widgets on gitlab.example.com" "$out"
# What the set IS belongs to 9j, which reads it back whole; what belongs here
# is that discovery added nothing to it, so this names the host and not the
# entries — a fifth entry is not a failure of section 14.
limited="$(printf '%s\n' "$out" | grep 'Merging is limited to')"
expect "and the pass says what it limits merging to" "Merging is limited to" "$limited"
refute "because discovering the instance put nothing of it on that set" \
	"gitlab.example.com" "$limited"
count_is "so nothing on a discovered host was merged" "$(wc -l <"$gl2/merged.log")" \
	"$before" "$out"

python3 - "$gl2/mrs/306.json" <<'PYMERGED'
import json
import sys
doc = json.load(open(sys.argv[1]))
doc["state"] = "merged"
json.dump(doc, open(sys.argv[1], "w"))
PYMERGED
out="$(gdq reap 2>&1)"
expect "reap lands a task on a discovered host" "landed" "$out"
expect "and releases the session that used to leak with it" "reaped" "$out"

if [ -s "$gl2/gh-calls.log" ]; then
	fail "no code path ran \`gh\` while the host came from glab" \
		"$(cat "$gl2/gh-calls.log")"
else
	pass "no code path ran \`gh\` while the host came from glab"
fi


# --- 20. a title `dispatch` cannot spawn, and a spawn failure that says why --
#
# Both halves of one run on 2026-09-11. `add` took the title `Rust crate,
# CI/CD and the profile model`; `dispatch` then died with nothing but
# thurbox's exit status echoed back, and the cause — thurbox refuses a session
# name containing `/` — was found by running the printed `session create` by
# hand. The repair was a hand-edit of `title` in task.yaml, because there is
# no retitle verb.
#
#   (a) `add` refuses a title that cannot become a session name, names the
#       character, and creates nothing — the same bargain as the `--branch`
#       refusal in 16d(f).
#   (b) The check is on the name thurbox is actually SENT, not on the raw
#       title: the glyph goes in front and the title is cut to the byte cap,
#       so a title that is only made long by the rendering is still fine, and
#       a leading `.` is unsafe exactly when no glyph precedes it.
#   (c) It mirrors thurbox's rule rather than inventing a stricter one. The
#       four cases in thurbox's own `unsafe_names_are_rejected` are refused,
#       and every character it accepts is still accepted here — a title is
#       human-facing text and narrowing it further is its own defect.
#   (d) A failing spawn carries thurbox's own words, from whichever stream it
#       used, and the three things that were already right stay right: the
#       task is left `queued`, the rest of the set still goes out, and a
#       re-run does not spawn what already went.

export FLEET_QUEUE_DIR="$tmp/queue-titles"
ntopic="$($QUEUE topic add unspawnable-titles --title 'A title becomes a session name' \
	--prompt 'add accepted a title dispatch could not spawn' 2>/dev/null)"

# (a) The title from the run, verbatim.
if out="$($QUEUE add "$ntopic" ci-cd --title 'Rust crate, CI/CD and the profile model' \
	--repo /tmp/repo-a --branch feat/ci-cd --number 01 2>&1)"; then
	fail "a title that cannot become a session name is refused at add" "$out"
else
	pass "a title that cannot become a session name is refused at add"
	expect "and the refusal names the offending character" "'/'" "$out"
	expect "and says it is the SESSION NAME that cannot carry it" \
		"session name" "$out"
	expect "and quotes the name thurbox would have been asked to create" \
		"Rust crate, CI" "$out"
fi
if [ -e "$FLEET_QUEUE_DIR/$ntopic/01-ci-cd" ]; then
	fail "and creates nothing, so the repair is one re-run and not an edit" \
		"$(ls "$FLEET_QUEUE_DIR/$ntopic/01-ci-cd")"
else
	pass "and creates nothing, so the repair is one re-run and not an edit"
fi

# (b) The RENDERED name, and only that. A 61-character title wearing a 5-byte
#     glyph is 66 bytes and would fail a check against the raw string, but
#     `session_name` cuts it to the cap before thurbox ever sees it.
long="Codify the out-of-band identity and patch settings on the box"
if out="$($QUEUE add "$ntopic" long-title --title "$long" \
	--repo /tmp/repo-a --branch feat/long-title --number 02 2>&1)"; then
	pass "a title only made over-long by the glyph and the cut is still accepted"
else
	fail "a title only made over-long by the glyph and the cut is still accepted" "$out"
fi
expect "and it really was the rendering that made it long: 66 bytes, cut to 64" \
	"66 64" "$(python3 -c '
import sys
sys.path.insert(0, "scripts/lib")
import queue as q
title = sys.argv[1]
print(len(("\N{ROCKET} " + title).encode()), len(q.session_name(title, "\N{ROCKET}").encode()))
' "$long")"

# The other direction: a leading `.` is unsafe exactly when nothing precedes
# it, so the same title is fine with the mark on and refused with it off.
nglyph="$(mktemp -d)"
mkdir -p "$nglyph/orchestration"
printf 'GLYPHS=on\nLEAD_GLYPH_ON=📡\nLEAD_GLYPH_OFF=⌖\nWORKER_GLYPH_ON=🚀\n' \
	>"$nglyph/orchestration/session-glyphs.conf"
if out="$(FLEET_GLYPH_ROOT="$nglyph" $QUEUE add "$ntopic" dot-with-mark \
	--title '.hidden agenda' --repo /tmp/repo-a --branch feat/dot-mark \
	--number 03 2>&1)"; then
	pass "a title starting '.' is accepted while a mark goes in front of it"
else
	fail "a title starting '.' is accepted while a mark goes in front of it" "$out"
fi
printf 'GLYPHS=off\nLEAD_GLYPH_ON=📡\nLEAD_GLYPH_OFF=⌖\nWORKER_GLYPH_ON=🚀\n' \
	>"$nglyph/orchestration/session-glyphs.conf"
if out="$(FLEET_GLYPH_ROOT="$nglyph" $QUEUE add "$ntopic" dot-no-mark \
	--title '.hidden agenda' --repo /tmp/repo-a --branch feat/dot-no-mark \
	--number 04 2>&1)"; then
	fail "and refused with the mark off, where the name really does start '.'" "$out"
else
	pass "and refused with the mark off, where the name really does start '.'"
	expect "naming the rule it broke and not merely the character" \
		"beginning with '.'" "$out"
fi
rm -rf "$nglyph"

# (c) thurbox's rule, not a stricter one. The four unsafe names are the case
#     list from its own `unsafe_names_are_rejected`; the accepted ones are
#     ordinary titles, every character of which thurbox takes.
out="$(python3 -c '
import sys
sys.path.insert(0, "scripts/lib")
import queue as q
unsafe = [".hidden", "foo/bar", "foo..bar", "foo\\bar"]
safe = [
    "Rust crate, CI-CD and the profile model",
    "Fix the parser (again!)",
    "Ship v2.1: metrics & alerts @ 99% — done?",
    "Réécrire le lecteur ~ étape 1",
    "a.b.c and #42 + [brackets] {braces} <angles>",
    "trailing dot.",
]
for name in unsafe:
    print("REFUSED" if q.session_name_refusal(name, "") else "ACCEPTED", name, sep="\t")
for name in safe:
    print("REFUSED" if q.session_name_refusal(name, "") else "ACCEPTED", name, sep="\t")
' 2>&1)"
if [ "$(printf '%s\n' "$out" | grep -c '^REFUSED')" = 4 ]; then
	pass "every name thurbox's own unsafe_names_are_rejected lists is refused"
else
	fail "every name thurbox's own unsafe_names_are_rejected lists is refused" "$out"
fi
refute "and nothing thurbox accepts is refused alongside them" \
	"$(printf 'REFUSED\tRust')" "$out"
if [ "$(printf '%s\n' "$out" | grep -c '^ACCEPTED')" = 6 ]; then
	pass "a title is human-facing text, so no character is narrowed beyond that"
else
	fail "a title is human-facing text, so no character is narrowed beyond that" "$out"
fi

# (d) The spawn failure. A `thurbox-cli` in front of the run's own stub fails
#     one named branch with a known string, on whichever stream the test
#     chooses, and delegates everything else — so the tasks that can be
#     spawned are spawned by the same stub as every other section.
boombin="$tmp/boom-bin"
mkdir -p "$boombin"
cat >"$boombin/thurbox-cli" <<'SH'
#!/bin/sh
if [ "$1 $2" = "session create" ]; then
	case "$*" in
	*"$BOOM_MATCH"*)
		if [ "${BOOM_STREAM:-stderr}" = stdout ]; then
			printf '%s\n' "$BOOM_MESSAGE"
		else
			printf '%s\n' "$BOOM_MESSAGE" >&2
		fi
		exit 1
		;;
	esac
fi
exec "$TBX_REAL" "$@"
SH
chmod +x "$boombin/thurbox-cli"

for spec in \
	"10:goes-out:Goes out anyway" \
	"11:boom:Spawn fails on stderr" \
	"12:stdout-boom:Spawn fails on stdout"; do
	IFS=: read -r n slug title <<<"$spec"
	$QUEUE add "$ntopic" "$slug" --title "$title" --repo /tmp/repo-a \
		--branch "feat/$slug" --number "$n" >/dev/null
	printf '# %s\n\nA brief with real content in it.\n' "$title" \
		>"$FLEET_QUEUE_DIR/$ntopic/$n-$slug/BRIEF.md"
done
# The two above that were accepted carry a scaffolded brief, and `dispatch`
# refuses the whole wave over one of those. Hold them out of the ready set.
for held in 02-long-title 03-dot-with-mark; do
	$QUEUE block "$ntopic/$held" --on "$ntopic/10-goes-out" \
		--kind other --why 'held out of the spawn test below' >/dev/null
done

nsession=dddddddd-dddd-dddd-dddd-dddddddddddd
printf '{"id":"%s","created":true}\n' "$nsession" >"$tmp/next-session.json"
session_is "$nsession" idle

# The stubs are named rather than taken off $PATH: test 7's subshell exported
# its own, which is what SC2031 is about, and this section wants the one the
# run set up.
boom() {
	env PATH="$boombin:$ghbin:$tbxbin:$sshbin:$quotabin:$base_path" \
		TBX_REAL="$tbxbin/thurbox-cli" "$@"
}

out="$(boom BOOM_MATCH=feat/boom \
	BOOM_MESSAGE='Name contains invalid characters' \
	$QUEUE dispatch "$ntopic/10-goes-out" "$ntopic/11-boom" 2>&1)"
expect "a failing spawn reports what thurbox said" \
	"Name contains invalid characters" "$out"
refute "and not only the exit status the code used to echo back" \
	"returned non-zero exit status" "$out"
expect "the task that could be spawned still went out" "10-goes-out" "$out"
expect "and it really got its session" "$nsession" "$out"
expect "the failed one is left queued, so fixing it and re-running sends it" \
	"queued" "$($QUEUE show "$ntopic/11-boom" | grep -F 'state:')"

# thurbox prints its structured failure on STDOUT, not stderr — that is why
# reading only stderr left the operator with an exit status and nothing else.
out="$(boom BOOM_MATCH=feat/stdout-boom BOOM_STREAM=stdout \
	BOOM_MESSAGE='{"error":"Name contains invalid characters","suggestion":"the command ran and failed"}' \
	$QUEUE dispatch "$ntopic/12-stdout-boom" 2>&1)"
expect "a refusal thurbox printed on stdout is read too" \
	"Name contains invalid characters" "$out"
refute "and its JSON wrapping is unwrapped rather than echoed" \
	"suggestion" "$out"

# A re-run sends the ones that failed and does not touch the one that went.
resent=eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee
printf '{"id":"%s","created":true}\n' "$resent" >"$tmp/next-session.json"
session_is "$resent" idle
out="$(boom BOOM_MATCH=no-such-branch $QUEUE dispatch 2>&1)"
expect "a re-run spawns the one that failed" "11-boom" "$out"
refute "and does not spawn the one already sent" "10-goes-out" "$out"
expect "whose session is still the first one" "$nsession" \
	"$($QUEUE show "$ntopic/10-goes-out")"


# The fixer above got a real worktree; take it back off the test repo, as
# section 13 does with its own.
git -C "$glrepo" worktree remove --force \
	"$tmp/queue-gitlab/.worktrees/${gltopic}__02-conflicting" 2>/dev/null

# The fixer above got a real worktree; take it back off the test repo so the
# temp directory can be removed without leaving a stale registration.
git -C "$frepo" worktree remove --force \
	"$tmp/queue-fake/.worktrees/${ftopic}__02-conflicting" 2>/dev/null

# --- 20. a wait on a CONDITION: recordable, visible, and cleared only by hand -
#
# The defect this proves gone, measured. On 2026-09-11
# `vending-machine-egress-resume/01-vm-identity-reconciliation` was ready by
# every record fleet keeps and unrunnable in fact: its brief's first
# instruction reads Azure and `az` was not authenticated. `block` took only
# `--on <ref>` — "the task this one waits for" — so there was nothing to write
# down. `plan` listed the task as ready and `notify_lead.py` correctly typed
# the loop's one line into the lead's terminal telling it to dispatch. The only
# honest answer left was to refuse in conversation and leave the record saying
# nothing.
#
# Four claims, and the last one is the incident:
#
#   A condition can be RECORDED, and it still needs a kind from a closed set
#     and a reason, so "these edit the same file" gains no new spelling.
#   It shows up EVERYWHERE work in flight is reported — `plan`'s waiting block,
#     `list`, `show`, `fleet-status.sh` — and never in `ready`.
#   NOTHING CLEARS IT BUT A HAND. Not a timer, not `collect`, not `reap`.
#   `notify_lead.py` DOES NOT COUNT IT toward the ready set it wakes the lead
#     about, which is the line that was typed on 2026-09-11.
#
# And the form that already existed is asserted here too rather than assumed,
# because it shares every line of code this section changed: the `landed` gate
# and `UNCLEARABLE` both have to still be true in the same queue.

export FLEET_QUEUE_DIR="$tmp/queue-conditions"
mkdir -p "$tmp/repo-conditions"

xtopic="$($QUEUE topic add vending-machine-egress \
	--title 'Resume the vending machine egress' \
	--prompt 'reconcile the VM identities and resume egress')"

for spec in \
	"01:vm-identity:Reconcile the VM identities against Azure" \
	"02:document-the-tables:Write the identity tables down" \
	"03:terraform-the-vms:Terraform the three VMs" \
	"04:after-a-dead-end:A task waiting on one that never lands" \
	"05:the-dead-end:The task that gets abandoned"; do
	IFS=: read -r n slug title <<<"$spec"
	if ! out="$($QUEUE add "$xtopic" "$slug" --title "$title" \
		--repo "$tmp/repo-conditions" --branch "fix/$slug" --number "$n" 2>&1)"; then
		fail "add $slug" "$out"
	fi
done

# --- the refusals come first: a new form is a new way to spell the old lie ---

if out="$($QUEUE block "$xtopic/01-vm-identity" \
	--condition 'az is authenticated for the mazet tenant' 2>&1)"; then
	fail "a condition with no kind and no reason is refused" "$out"
else
	expect "a condition with no kind and no reason is refused" "--kind" "$out"
	expect "and the refusal lists the condition kinds, not the task ones" \
		"missing-credential" "$out"
	expect "and says what the only release is" "--clear" "$out"
fi

# An unset shell variable is the ordinary way this arrives — `--condition
# "$COND"` with nothing in `$COND`. argparse is satisfied, because the flag was
# given; a blank condition is still a wait nobody named, and `--clear` could
# never name it back.
for blank in '' '   '; do
	if out="$($QUEUE block "$xtopic/01-vm-identity" --condition "$blank" \
		--kind missing-credential --why 'az is not authenticated' 2>&1)"; then
		fail "a blank condition is refused" "$out"
	else
		expect "a blank condition is refused" "--condition" "$out"
		refute "and refused, not crashed" "Traceback" "$out"
	fi
	if out="$($QUEUE block "$xtopic/01-vm-identity" --clear \
		--condition "$blank" 2>&1)"; then
		fail "and clearing a blank condition is refused too" "$out"
	else
		refute "and clearing a blank condition is refused too" "Traceback" "$out"
	fi
done

if out="$($QUEUE block "$xtopic/01-vm-identity" --condition 'az is authenticated' \
	--kind file-overlap --why 'both edit main.tf' 2>&1)"; then
	fail "file overlap is not a condition kind either" "$out"
else
	expect "file overlap is not a condition kind either" "--kind" "$out"
	expect "and the refusal still points at --touches" "--touches" "$out"
fi

# The two closed sets stay two. A kind that describes a relationship BETWEEN
# TASKS says nothing about a credential, and taking it would have made the set
# decorative.
if out="$($QUEUE block "$xtopic/01-vm-identity" --condition 'az is authenticated' \
	--kind semantic-dependency --why 'reads Azure' 2>&1)"; then
	fail "a task kind is refused on a condition" "$out"
else
	expect "a task kind is refused on a condition" "missing-credential" "$out"
fi

if out="$($QUEUE block "$xtopic/03-terraform-the-vms" --on "$xtopic/01-vm-identity" \
	--kind missing-credential --why 'az is not authenticated' 2>&1)"; then
	fail "and a condition kind is refused on --on" "$out"
else
	expect "and a condition kind is refused on --on" "semantic-dependency" "$out"
fi

if out="$($QUEUE block "$xtopic/01-vm-identity" --on "$xtopic/02-document-the-tables" \
	--condition 'az is authenticated' --kind other --why 'both' 2>&1)"; then
	fail "a blocker names a task or a condition, never both" "$out"
else
	expect "a blocker names a task or a condition, never both" \
		"not allowed with argument" "$out"
fi

# --- recording one, and where it then shows up -------------------------------

AZ='az is authenticated for the mazet tenant'
if ! out="$($QUEUE block "$xtopic/01-vm-identity" --condition "$AZ" \
	--kind missing-credential \
	--why "the brief's first instruction reads Azure and az account show fails" 2>&1)"; then
	fail "record a condition" "$out"
fi
expect "recording one says who releases it, since nothing else will" \
	"--clear" "$out"

# The other form, in the same queue, so the `landed` gate below is proved
# against the same code path this section changed.
$QUEUE block "$xtopic/03-terraform-the-vms" --on "$xtopic/02-document-the-tables" \
	--kind semantic-dependency --why 'terraforms the identities 02 writes down' >/dev/null
$QUEUE block "$xtopic/04-after-a-dead-end" --on "$xtopic/05-the-dead-end" \
	--kind semantic-dependency --why 'consumes what 05 was going to add' >/dev/null
set_field "$xtopic/05-the-dead-end" state abandoned

plan="$($QUEUE plan 2>&1)"
expect "the condition-held task waits" "01-vm-identity" "$plan"
expect "and it is in the waiting block, not the ready one" "waiting: 3" "$plan"
expect "the ready set is what is left" "ready: 1" "$plan"
expect "the plan names the condition itself" "$AZ" "$plan"
expect "and the reason somebody recorded for it" "az account show fails" "$plan"
expect "and says the release is a hand, not an event" "only \`block --clear\`" "$plan"
refute "a condition is never called a wait on a task" "on $AZ" "$plan"

ready="$($QUEUE plan --json 2>&1 |
	python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)["ready"]))')"
if [ "$ready" = "$xtopic/02-document-the-tables" ]; then
	pass "plan --json agrees: the condition-held task is not ready"
else
	fail "plan --json ready set" "got $ready"
fi

out="$($QUEUE list 2>&1)"
expect "list shows the task as waiting" "01-vm-identity                     waiting" "$out"
expect "and carries the condition under it" "$AZ" "$out"

out="$($QUEUE show "$xtopic/01-vm-identity" 2>&1)"
expect "show carries it too" "$AZ" "$out"
expect "and names its kind" "missing-credential" "$out"

out="$(./scripts/fleet-status.sh 2>&1)"
expect "fleet-status reports it as well" "$AZ" "$out"

view="$(./scripts/fleet-status.sh --json 2>&1)"
expect "and the machine-readable reading calls it a wait on something outside" \
	'"status": "outside"' "$view"
expect "which is never cleared" '"cleared": false' "$view"

out="$($QUEUE check 2>&1)"
expect "a recorded condition is a valid record" "ok" "$out"

# --- the existing form, unchanged, in the same queue -------------------------

expect "a blocker on a task that can never land still says so" "UNCLEARABLE" "$plan"
expect "and still carries that upstream's state" "which is abandoned" "$plan"

# --- and nothing else clears the condition -----------------------------------
#
# `collect` and `reap` are the two commands that move tasks without being told
# which, and they run over this queue with the condition standing. A condition
# that expired because some other task landed would put back exactly the
# silence this section is about.

session_is 44444444-4444-4444-4444-444444444444 idle
$QUEUE attach "$xtopic/02-document-the-tables" \
	44444444-4444-4444-4444-444444444444 >/dev/null
pipeline_pr 777 fix/document-the-tables
cat >"$FLEET_QUEUE_DIR/$xtopic/02-document-the-tables/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/Thurbeen/fleet/pull/777
---
Wrote the identity tables down.
EOF

out="$($QUEUE collect 2>&1)"
expect "collect closes the task that finished" "shipped" "$out"
echo MERGED >"$states/777.state"
out="$($QUEUE reap 2>&1)"
expect "and reap lands it once the forge says merged" "landed" "$out"

plan="$($QUEUE plan 2>&1)"
expect "the task blocker cleared on the LAND, exactly as before" \
	"03-terraform-the-vms" "$plan"
expect "and the condition did not: it is still holding 01" "$AZ" "$plan"
expect "so the ready set grew by one and not by two" "ready: 1" "$plan"
refute "01 is still out of the ready set after a collect and a reap" \
	"    $xtopic/01-vm-identity  " "$plan"

# --- 20a. the line the reconciler types, which is where this went wrong ------
#
# THE ASSERTION THAT REPRESENTS THE INCIDENT, written as the case it came from:
# one task ready, one held by a condition, and the loop says ONE. `notify_lead`
# reads `plan --json`'s ready set and nothing else, which is why the fix lives
# in `is_ready` — but "the loop no longer names this task" is the claim that
# was false on 2026-09-11, so it is asserted here rather than inferred from the
# reading it is built on.

notify="$tmp/notify-conditions"
mkdir -p "$notify"
printf '{"id":"lead-1","name":"Gate Control","state":"idle"}\n' \
	>"$sessions/lead-1.json"
: >"$sends"

log="$($QUEUE plan --json | FLEET_LEAD_SESSION="Gate Control" \
	python3 scripts/lib/notify_lead.py --state-dir "$notify" 2>&1)"
woke="$(cat "$sends")"
expect "the loop woke the lead about the ready work" "1 task(s) ready" "$log"
expect "and the line it typed names the task nothing is holding" \
	"03-terraform-the-vms" "$woke"
expect "it says ONE task is ready, not two" \
	"1 task(s) ready and nothing will dispatch" "$woke"
refute "the condition-held task is not in the line the lead was sent" \
	"01-vm-identity" "$woke"

rm -f "$sessions/lead-1.json"
: >"$sends"

# --- 20b. and a hand is what releases it -------------------------------------

if out="$($QUEUE block "$xtopic/01-vm-identity" --clear \
	--condition 'some other condition' 2>&1)"; then
	fail "clearing a condition this task does not hold is refused" "$out"
else
	expect "clearing a condition this task does not hold is refused" \
		"records no condition" "$out"
	expect "and the refusal says what it does hold" "$AZ" "$out"
fi

out="$($QUEUE block "$xtopic/01-vm-identity" --clear --condition "$AZ" 2>&1)"
expect "clearing it by name removes it" "1 blocker(s) cleared" "$out"

plan="$($QUEUE plan 2>&1)"
expect "and the task returns to the ready set" "01-vm-identity" "$plan"
expect "which is now two" "ready: 2" "$plan"
refute "with nothing left holding it" "$AZ" "$plan"

echo
if [ "$failed" -eq 0 ]; then
	printf '\033[32mqueue-selftest: every claim holds\033[0m\n'
else
	printf '\033[31mqueue-selftest: failed\033[0m\n' >&2
fi
exit "$failed"
