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
#      cannot read is left exactly as it is.
#  11. A task can name a HOST and run there, and a task that names none takes
#      exactly the path it took before the flag existed. Nothing is spawned on
#      a host until three probes pass; the brief really lands on that host's
#      filesystem and the result really comes back off it; and a session whose
#      host cannot be reached is kept, not reaped — thurbox's CLI still calls
#      it `idle`, which is the trap.
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
# `thurbox-cli` and `ssh` are all stubbed on PATH for the whole run (see the
# stubs below), so every collect and every reap here answers the same offline,
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

# Captured here, before test 7's subshell exports its own PATH: reading $PATH
# after that point is what SC2031 is about, and the stubbed sections below
# want the PATH this script started with, not whatever a subshell left.
base_path="$PATH"

echo "queue-selftest: $FLEET_QUEUE_DIR"

# --- `gh`, stubbed on PATH for the whole run ---------------------------------
#
# `collect` fetches each artifact's pull request body to check that it came
# from the pipeline (test 8). Every collect below therefore has to answer
# offline and identically on CI, so the real `gh` is replaced here rather than
# in test 8 alone: this stub serves one body file per PR number and fails, the
# way an unreachable API fails, for a number it has no file for.

bodies="$tmp/pr-bodies"
states="$tmp/pr-states"
ghbin="$tmp/gh-bin"
mkdir -p "$bodies" "$states" "$ghbin"

cat >"$ghbin/gh" <<SH
#!/bin/sh
# Stands in for \`gh pr view <url> --json body\` (collect's pipeline check) and
# \`gh pr view <url> --json state\` (reap's landing check). A pull request with
# no body file is one the API cannot be reached for; one with no state file is
# OPEN, which is what a pull request is until something changes it.
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
*state*) cat "$states/\$n.state" 2>/dev/null || echo OPEN ;;
*) cat "$bodies/\$n.md" ;;
esac
SH
chmod +x "$ghbin/gh"

# --- `thurbox-cli`, stubbed on PATH for the whole run ------------------------
#
# `reap` asks thurbox what a session is doing before it deletes anything, and
# deletes with `session delete <id> --force`. Both are stubbed here: the states
# come from one file per session id, and every delete is APPENDED TO A LOG
# rather than performed, so a test can assert on exactly what would have been
# killed — including that nothing was.

sessions="$tmp/sessions"
deletions="$tmp/deletions.log"
tbxbin="$tmp/tbx-bin"
mkdir -p "$sessions" "$tbxbin"
: >"$deletions"

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
	# No dialog on the pane. session-trust.sh then falls through to the
	# hook_reported check, which is what the session fixtures answer.
	echo '{"output":""}'
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
session_is() {
	printf '{"id":"%s","name":"%s","state":"%s"}\n' "$1" "worker $1" "$2" \
		>"$sessions/$1.json"
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
	[ -f "$sshstate/\$dest.noforge" ] && exit 1
	printf 'an ssh key'
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

export PATH="$ghbin:$tbxbin:$sshbin:$PATH"

# A compliant body for the pull request test 3 collects, so that test says what
# it always said — that a blocker clears on a real conclusion — and nothing
# about the pipeline.
cat >"$bodies/999.md" <<'EOF'
## Intent
Drop the idle default.
## What Changed
src/state.rs.
## Risk Assessment
Low.
## Testing
cargo test.
## Pipeline
no-mistakes, all gates green.
EOF

# --- a topic, and four tasks under it ----------------------------------------
#
# One prompt, two repos, four units of work: the shape the queue exists for.
# `01` and `02` are independent. `03` genuinely depends on `01`. `04` edits the
# same file as `01` and depends on nothing.

if ! topic="$($QUEUE topic add report-status-honestly \
	--title 'Make thurbox report agent status honestly' \
	--prompt 'idle should mean the agent said it is at rest, nothing else' 2>&1)"; then
	fail "topic add" "$topic"
	exit 1
fi
expect "topic add returns a topic id" "report-status-honestly" "$topic"

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
# prove it seeds the cursor from the high-water mark rather than from 0 or
# from "now".
events="$tmp/events.jsonl"
cat >"$events" <<'EOF'
{"seq":100,"at":1788792150000,"session":"11111111-1111-1111-1111-111111111111","event":"present","from_state":null,"to_state":"working","state":"working","reason":null}
{"seq":100,"at":1788792150000,"session":"22222222-2222-2222-2222-222222222222","event":"present","from_state":null,"to_state":"working","state":"working","reason":null}
EOF
export FLEET_QUEUE_WATCH_CMD="cat $events"

$QUEUE attach "$topic/01-drop-idle-default" 11111111-1111-1111-1111-111111111111 >/dev/null
$QUEUE attach "$topic/02-document-the-states" 22222222-2222-2222-2222-222222222222 >/dev/null

cursor="$(cat "$FLEET_QUEUE_DIR/.cursor" 2>/dev/null || echo '<missing>')"
if [ "$cursor" = 100 ]; then
	pass "attach seeds the cursor from the stream's high-water mark"
else
	fail "attach seeds the cursor from the stream's high-water mark" "cursor: $cursor"
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
refute "the cursor resumes, so a second watch replays nothing" "seq 101" "$out"

# --- 7. a genuinely zero cursor is not treated as "no cursor" ----------------
#
# seed_cursor legitimately writes 0 when the stream's high-water mark really
# is 0 (a brand-new thurbox instance). read_cursor must tell that apart from
# "no cursor file at all": cmd_watch used to test the cursor for truthiness,
# so a real 0 was silently treated the same as "no cursor" and the --since
# flag was dropped from the real `thurbox-cli watch` call, starting the first
# watch after a dispatch from "now" instead of replaying from seq 0 — quietly
# losing any transition in between. This needs the real command path (not
# FLEET_QUEUE_WATCH_CMD, which replaces the whole command and never sees the
# flags), so it stubs `thurbox-cli` on PATH and reads what it was called with.
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
	zt="$(FLEET_QUEUE_DIR="$zerotmp/queue" $QUEUE topic add zero-cursor \
		--prompt 'prove a real zero cursor is not dropped')" || exit 1
	FLEET_QUEUE_DIR="$zerotmp/queue" $QUEUE add "$zt" only-task --title 'only task' \
		--repo /tmp/repo-z --branch fix/only-task --number 01 >/dev/null
	FLEET_QUEUE_DIR="$zerotmp/queue" $QUEUE attach "$zt/01-only-task" \
		33333333-3333-3333-3333-333333333333 >/dev/null
	FLEET_QUEUE_DIR="$zerotmp/queue" $QUEUE watch --for-secs 0 >/dev/null 2>&1
)

cursor="$(cat "$zerotmp/queue/.cursor" 2>/dev/null || echo '<missing>')"
if [ "$cursor" = 0 ]; then
	pass "seed_cursor writes a genuine zero when the stream's high-water mark is 0"
else
	fail "seed_cursor writes a genuine zero when the stream's high-water mark is 0" "cursor: $cursor"
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
# bodies himself. A `no-mistakes` PR body carries five headings; `collect`
# fetches the body and looks for them.
#
# The `gh` stub at the top of this file serves one body file per PR number, so
# all three answers are reachable offline: a compliant body, a non-compliant
# one, and a pull request the stub cannot fetch at all.

cat >"$bodies/1001.md" <<'EOF'
## Intent
Document the state vocabulary.
## What Changed
docs/states.md, rewritten.
## Risk Assessment
Docs only.
## Testing
`./scripts/check.sh` green.
## Pipeline
no-mistakes, all gates green.
EOF

cat >"$bodies/1002.md" <<'EOF'
## Summary
Rendered detected_agent in the session list.

Opened with `gh pr create`, which is exactly the thing that must be caught.
EOF

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

expect "a compliant PR body collects clean" "02-document-the-states" "$out"
expect "and collect says the pipeline was verified" "verified" "$out"

expect "a PR that skipped the pipeline is caught" "03-render-detected-agent" "$out"
expect "the refusal names the headings that are missing" "Risk Assessment" "$out"
expect "the refusal says the task was not closed" "NOT CLOSED" "$out"
expect "and names the remedy" "no-mistakes" "$out"

state="$($QUEUE show "$topic/03-render-detected-agent" 2>&1)"
refute "a task whose PR failed the check is not closed" "state:       done" "$state"

expect "an unreachable gh degrades to unknown" "04-log-state-changes" "$out"
expect "and says the check could not run" "could not" "$out"
refute "an unchecked artifact is never reported as verified" \
	"04-log-state-changes  shipped  https://github.com/Thurbeen/thurbox/pull/1003  [pipeline verified]" "$out"

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

# Design decision (d): the heading list is ONE constant, and POLICY.md quotes
# it rather than restating it. Prove that by parsing POLICY.md's fenced
# heading block into the same shape as PIPELINE_HEADINGS and comparing the
# two, instead of grepping the prose for a phrase — a rewording of the policy
# text around the headings would not touch this, only a drift between the
# quoted list and the code's would.
policy_vs_code="$(python3 - "$policy" <<'PY'
import re
import sys

sys.path.insert(0, "scripts/lib")
import queue as q

text = open(sys.argv[1]).read()
block = re.search(r"```text\n(.*?)```", text, re.S).group(1)
quoted = tuple(line.removeprefix("## ").strip() for line in block.splitlines() if line.strip())
print("match" if quoted == q.PIPELINE_HEADINGS else f"policy={quoted} code={q.PIPELINE_HEADINGS}")
PY
)"
expect "the policy quotes PIPELINE_HEADINGS exactly, not a second copy" "match" "$policy_vs_code"

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
# monitor — reading the control plane's queue — correctly showed nothing. No
# warning at any point. Two queues, silently.
#
# The whole thing is exercised against a THROWAWAY CLONE rather than this one,
# so the test says the same thing on a machine with thurbox and on CI without
# it: a directory holding `scripts/queue.sh`, a symlink to the real
# `scripts/lib/queue.py` (the anchor is the script's own path, so a symlink is
# a whole clone for this purpose) and a rendered `extension.toml` that decides
# whether that clone IS the control plane.

clonetmp="$(mktemp -d)"
fake="$clonetmp/second-clone"
mkdir -p "$fake/scripts/lib" "$fake/deep/sub/dir"
cp scripts/queue.sh "$fake/scripts/queue.sh"
ln -s "$PWD/scripts/lib/queue.py" "$fake/scripts/lib/queue.py"
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
# webui-selftest and this file both point the queue at a temp directory. Someone
# who set it meant it, so nothing here may warn about it or refuse it.

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
# "mission control" while the extension stays "fleet" does. manifest_session()
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
name = "mission control"
repo_path = "/tmp/does-not-matter"
EOF

out="$(python3 -c '
import sys
sys.path.insert(0, "scripts/lib")
import queue as q
print(q.manifest_session(sys.argv[1]))
' "$manifesttmp/extension.toml")"
expect "manifest_session reads the TABLE's name, not the prose above it" \
	"('mission control', '/tmp/does-not-matter')" "$out"

rm -rf "$manifesttmp"

out="$(python3 -c '
import sys
sys.path.insert(0, "scripts/lib")
import queue as q
print(q.manifest_session(sys.argv[1]))
' "$PWD/extension.toml.in")"
expect "and the real extension.toml.in resolves to the same session name" \
	"mission control" "$out"

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
#   the five `## ` headings, which anyone can paste, authorise nothing
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
for br in conflicting green skipped elsewhere busy unrun gone second; do
	git -C "$srepo" branch "fix/$br"
done

# Push access, which is what "opened by the repository owner" means once the
# owner is an organisation and the author is a person inside it. `stranger` has
# no file here, so the stub answers `none` for them.
mkdir -p "$shep/perms"
echo admin >"$shep/perms/LeTuR"

stopic="$($QUEUE topic add shepherd-cases --title 'The PRs, after the work' \
	--prompt 'watch every open PR and dispatch a fixer when one goes bad')"

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
env PATH="$shep/bin:$base_path" $QUEUE collect >/dev/null

python3 - "$shep/gh" <<'PY'
import json
import sys

out = sys.argv[1]
headings = "\n".join(
    f"## {h}\nx\n"
    for h in ("Intent", "What Changed", "Risk Assessment", "Testing", "Pipeline")
)
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
    return f"<!-- no-mistakes-pipeline-attestation:v1 {payload} -->\n\n" + headings


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
pr(111, headRefName="fix/headings-only", body=headings)
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

# --- 9c4. the attestation, and not the headings anyone can type --------------

if grep -qx 110 "$shep/merged" 2>/dev/null; then
	fail "an attestation for an earlier head sha does not authorise this one" \
		"$(cat "$shep/merged")"
else
	pass "an attestation for an earlier head sha does not authorise this one"
fi
expect "and it says the attestation names another commit" "attestation" "$out"

if grep -qx 111 "$shep/merged" 2>/dev/null; then
	fail "the five headings alone never authorise a merge" "$(cat "$shep/merged")"
else
	pass "the five headings alone never authorise a merge"
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
	--prompt 'shepherd a repo with at least GH_PR_LIST_LIMIT open pull requests')"
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

# The worktrees the fixers got are real; take them back off the test repo so
# the temp directory can be removed without leaving stale registrations.
for slug in 01-conflicting 03-skipped 07-gone 08-second; do
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
	--prompt 'fleet should be able to spawn a worker on a remote thurbox host')"

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
cat >"$bodies/4242.md" <<'EOF'
## Intent
Build it there.
## What Changed
src/main.rs.
## Risk Assessment
Low.
## Testing
cargo test on the host.
## Pipeline
no-mistakes, all gates green.
EOF
session_is "$rsession" idle
cat >"$sessions/$rsession.json" <<EOF
{"id":"$rsession","name":"Build it on devbox","state":"idle","agent":"claude"}
EOF

out="$($QUEUE collect 2>&1)"
expect "collect fetches a remote worker's result over ssh" \
	"result fetched from me@devbox" "$out"
expect "and closes the task on it, exactly as it would locally" "shipped" "$out"
expect "and the pipeline check ran on it like any other" "pipeline verified" "$out"
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

echo
if [ "$failed" -eq 0 ]; then
	printf '\033[32mqueue-selftest: every claim holds\033[0m\n'
else
	printf '\033[31mqueue-selftest: failed\033[0m\n' >&2
fi
exit "$failed"
