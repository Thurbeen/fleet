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
#  16. The tool leaves the lead no reason to work around it. A `--brief-file`
#      that carries the four standard headings fills the four standard
#      sections and one with none behaves as it always did; `dispatch` takes
#      refs, so holding a task back needs no invented blocker, and a bare
#      `dispatch` still sends everything; and `block --kind` lists its four
#      values in `--help` instead of only in the refusal.
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
	zt="$(FLEET_QUEUE_DIR="$zerotmp/queue" $QUEUE topic add zero-cursor \
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
	--repo "$pwork" --branch main --base main --number 03 --publish push >/dev/null

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
	--repo "$pwork" --branch main --base main --number 06 --publish push >/dev/null
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
	--repo /srv/code/app --host devbox --branch main --base main --number 07 \
	--publish push >/dev/null
cat >"$FLEET_QUEUE_DIR/$ptopic/07-push-elsewhere/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/acme/app/commit/0123456789abcdef0123456789abcdef01234567
---
Pushed it on devbox.
EOF

$QUEUE add "$ptopic" push-unreadable --title 'Push into a repo this machine has not got' \
	--repo /tmp/not-a-checkout --branch main --base main --number 08 \
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
ln -s "$PWD/scripts/lib/queue.py" "$bare/scripts/lib/queue.py"
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
for br in conflicting green skipped elsewhere busy unrun gone second prose-only; do
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
expect "while the sections it said nothing about stay unwritten" \
	"## Coordination <!-- WRITE THE INSTRUCTIONS HERE -->" "$odd"

# (c) A body with no headings at all behaves exactly as it did before: the
#     whole file into `What to do`, the other three left for the lead.

printf 'Just do it, there is nothing else to say.\n' >"$tmp/flat-brief.md"
$QUEUE add "$etopic" headingless-body --title 'Headingless body' \
	--repo /tmp/repo-a --branch fix/headingless-body --number 03 \
	--brief-file "$tmp/flat-brief.md" >/dev/null
flatraw="$(cat "$FLEET_QUEUE_DIR/$etopic/03-headingless-body/BRIEF.md")"
expect "a headingless body still fills What to do" \
	"## What to do Just do it, there is nothing else to say." \
	"$(brief_text "$FLEET_QUEUE_DIR/$etopic/03-headingless-body/BRIEF.md")"
left="$(printf '%s\n' "$flatraw" | grep -c 'WRITE THE INSTRUCTIONS HERE')"
if [ "$left" = 3 ]; then
	pass "and leaves the other three for the lead, as it always did"
else
	fail "and leaves the other three for the lead, as it always did" \
		"counted $left placeholder(s)${nl}$flatraw"
fi

# A `## ` inside a fenced block is example text a brief is quoting, not a
# heading it is opening.
cat >"$tmp/fenced-brief.md" <<'MD'
Copy this shape:

```markdown
## Done means

not a real heading
```

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
# set, and still refuses this queue's half-written briefs before it sends any.
if out="$($QUEUE dispatch --dry-run 2>&1)"; then
	fail "a bare dispatch still refuses the queue's unwritten briefs" "$out"
else
	expect "a bare dispatch still refuses the queue's unwritten briefs" \
		"BRIEF.md" "$out"
fi
for t in 02-keep-odd-headings 03-headingless-body 04-fenced-body; do
	printf 'Written now.\n' >"$FLEET_QUEUE_DIR/$etopic/$t/BRIEF.md"
done
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

echo
if [ "$failed" -eq 0 ]; then
	printf '\033[32mqueue-selftest: every claim holds\033[0m\n'
else
	printf '\033[31mqueue-selftest: failed\033[0m\n' >&2
fi
exit "$failed"
