#!/usr/bin/env bash
# Update this control plane from the fleet template, safely.
#
# NOT scripts/sync-checkout.sh. That one fast-forwards this checkout from its
# OWN origin — your repo, your commits. This one brings changes from the
# TEMPLATE the control plane was made from, which is a different remote holding
# different history. Both are fast-forward-first and both refuse rather than
# force; they are otherwise unrelated and neither replaces the other.
#
# WHY THIS CAN WORK AT ALL. A fleet instance is a CLONE of the template with
# `origin` repointed at your own repo, so it carries the template's history and
# an update is ordinary git. The alternatives do not survive contact:
#
#   "Use this template"  a generated repo shares NO history with its source —
#                        no common ancestor, nothing for git to merge against.
#   a fork               shares history, but GitHub refuses to make a fork of a
#                        public repo private ("Public forks can't be made
#                        private", HTTP 422), and a control plane is private.
#
# A clone is the only shape that gives both. Instances that predate it — a
# template-generated one, or one bootstrapped on its own — join up with a
# one-time `--adopt`; see below.
#
# THE SAFETY POSTURE, copied from firstmate's own self-update. It never forces,
# never rebases, never resets, never stashes, and never leaves conflict markers
# behind. It advances only when that is unambiguously safe, and anything else is
# SKIPPED AND REPORTED rather than fixed for you:
#
#   fast-forward   your `main` is the template's `main` plus nothing. The
#                  ordinary case once .gitignore keeps what fleet writes out of
#                  git, and the reason that split exists.
#   clean merge    you have your own commits (a tracked run log, a tuned
#                  profile) and the merge is provably conflict-free. Proved
#                  first with `git merge-tree`, which computes the whole merge
#                  in memory and touches nothing.
#   skipped        dirty, on another branch, offline, no shared history, or the
#                  merge would conflict. Nothing is written. The report names
#                  the reason and the exact command that resolves it.
#
# Usage:
#   scripts/update-from-template.sh            # preview: fetch, compare, report
#   scripts/update-from-template.sh --apply    # do it
#   scripts/update-from-template.sh --adopt    # ONE-TIME, see below
#   scripts/update-from-template.sh --help
#
# --adopt is for an instance with no shared history: it records the template as
# an ancestor with `-s ours`, which keeps YOUR tree byte for byte and imports no
# content at all. After it, every later update is an ordinary one. It is not a
# way to receive the improvements that already exist upstream — the report says
# how to take those deliberately.
#
# THE HANDOVER. When the template stops tracking a file and its new .gitignore
# declares that file yours — how owners.txt and the generated map became
# instance-owned — a plain merge sees "you changed it, they deleted it" and
# conflicts, on every instance, on the one update that matters most. So a path
# that is (a) deleted upstream, (b) tracked here, and (c) ignored by the
# INCOMING .gitignore is untracked here first with `git rm --cached`, which
# leaves the file exactly where it is on disk. The merge then agrees with
# itself and the operator keeps their file. It is reported, never silent, and
# it is the only write this script makes that is not the update itself.
#
# Exit codes, so a cron or a CI job can read the outcome without parsing prose:
#
#   0  nothing to do, a preview was printed, or the update applied cleanly
#   1  wrong usage, or git is missing / this is not a repository
#   2  refused: it changed nothing and the report says what needs a human
#
# Requires: git. The template URL below is the default; an existing `template`
# remote wins, so a fork of the template is respected.

set -uo pipefail

TEMPLATE_URL="${FLEET_TEMPLATE_URL:-https://github.com/Thurbeen/fleet.git}"
REMOTE="template"
FETCH_TIMEOUT=60

# Changing one of these means the running `fleet` session is holding stale
# instructions: it froze FLEET.md and every skill it had loaded at launch, and
# no harness reloads them from disk. Nothing here can fix that, so it is
# reported as an action for the operator.
INSTRUCTION_PATHS=(FLEET.md AGENTS.md CLAUDE.md .agents/skills .claude/skills .claude/settings.json)
# Changing one of these means the installed thurbox extension no longer matches
# the manifest it was rendered from.
WIRING_PATHS=(extension.toml.in FLEET.md)

mode="preview"

usage() {
	sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

say() { printf '%s\n' "$*"; }
note() { printf '  %s\n' "$*"; }

# Report and stop. Nothing has been written by the time this is reached.
skip() {
	say "template: skipped: $1"
	shift
	for line in "$@"; do note "$line"; done
	exit 2
}

for arg in "$@"; do
	case "$arg" in
	-h | --help)
		usage
		exit 0
		;;
	--apply) mode="apply" ;;
	--adopt) mode="adopt" ;;
	*)
		printf 'error: unknown option %q (want: --apply, --adopt, --help)\n' "$arg" >&2
		exit 1
		;;
	esac
done

command -v git >/dev/null || {
	echo "error: git not found" >&2
	exit 1
}

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
	echo "error: not inside a git repository" >&2
	exit 1
}
cd "$repo_root" || exit 1

# --- who is the template, and are we it? -------------------------------------

# Compare two remote URLs as REPOSITORIES: scheme, credentials, a trailing
# `.git` and host case must not make github.com/Thurbeen/fleet look like a
# different repo from git@github.com:Thurbeen/fleet.git.
normalize_url() {
	printf '%s' "$1" |
		sed -e 's#^[a-z+]*://##' -e 's#^[^@/]*@##' -e 's#:#/#' -e 's#\.git$##' -e 's#/*$##' |
		tr '[:upper:]' '[:lower:]'
}

remote_url=""
if git remote get-url "$REMOTE" >/dev/null 2>&1; then
	remote_url="$(git remote get-url "$REMOTE")"
else
	origin_url="$(git remote get-url origin 2>/dev/null || true)"
	if [ -n "$origin_url" ] &&
		[ "$(normalize_url "$origin_url")" = "$(normalize_url "$TEMPLATE_URL")" ]; then
		say "template: already current"
		note "This checkout IS the template ($origin_url). There is nothing upstream of it."
		exit 0
	fi
	if [ "$mode" = "preview" ]; then
		say "template: skipped: no '$REMOTE' remote"
		note "Add it once, then re-run:"
		note "  git remote add $REMOTE $TEMPLATE_URL"
		note "Or let --apply / --adopt add it for you."
		exit 2
	fi
	git remote add "$REMOTE" "$TEMPLATE_URL" || {
		echo "error: could not add the '$REMOTE' remote" >&2
		exit 1
	}
	remote_url="$TEMPLATE_URL"
	say "template: added remote '$REMOTE' -> $TEMPLATE_URL"
fi

# --- guards: nothing below writes until every one of these passes ------------

default_branch="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')"
[ -n "$default_branch" ] && [ "$default_branch" != "HEAD" ] || default_branch=main

branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
[ -n "$branch" ] || skip "detached HEAD, expected $default_branch" \
	"Check out your default branch first:  git checkout $default_branch"
[ "$branch" = "$default_branch" ] || skip "on '$branch', expected $default_branch" \
	"An update belongs on the default branch. Switch to it:  git checkout $default_branch"

# Only TRACKED modifications block an update — an untracked scratch file must
# not wedge it. A tracked change would be merged into, which is the one thing
# refusing a dirty tree exists to prevent.
if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
	skip "dirty working tree" \
		"Commit or discard your changes first — an update never merges into uncommitted work." \
		"  git status --short"
fi

if ! timeout "$FETCH_TIMEOUT" git fetch --quiet --no-tags "$REMOTE" 2>/dev/null; then
	skip "could not fetch $REMOTE ($remote_url)" \
		"Offline, or the template is unreachable. Nothing was changed."
fi

upstream="$REMOTE/$default_branch"
git rev-parse --verify --quiet "$upstream^{commit}" >/dev/null || upstream="$REMOTE/main"
git rev-parse --verify --quiet "$upstream^{commit}" >/dev/null ||
	skip "$REMOTE has no '$default_branch' or 'main' branch" \
		"Check what it does have:  git branch -r --list '$REMOTE/*'"

local_rev="$(git rev-parse HEAD)"
upstream_rev="$(git rev-parse "$upstream")"
short() { git rev-parse --short "$1"; }

# --- classify: fast-forward, clean merge, adopt, or skip ---------------------

shares_history=1
git merge-base HEAD "$upstream" >/dev/null 2>&1 || shares_history=0

if [ "$mode" = "adopt" ]; then
	if [ "$shares_history" -eq 1 ]; then
		say "template: already current"
		note "This instance already shares history with the template — --adopt is a"
		note "one-time step and has already happened. Run without it."
		exit 0
	fi
	say "template: adopting $upstream ($(short "$upstream_rev")) as an ancestor"
	note "Your tree is not touched: -s ours keeps every byte of it and imports no content."
	if ! out="$(git merge -s ours --allow-unrelated-histories --no-edit \
		-m "chore: adopt the fleet template as an ancestor

Records $upstream ($(short "$upstream_rev")) as a second parent without taking
any of its content, so later updates have a real merge base and become
ordinary. Made by scripts/update-from-template.sh --adopt." "$upstream" 2>&1)"; then
		say "template: skipped: adopt failed"
		note "$(printf '%s' "$out" | head -3)"
		exit 2
	fi
	say "template: adopted $(short "$local_rev")..$(short "$(git rev-parse HEAD)")"
	note "Nothing in your working tree changed, so nothing upstream arrived with it."
	note "Take a specific improvement deliberately, for example:"
	note "  git checkout $upstream -- scripts/ .agents/skills/"
	note "From here on, ./scripts/update-from-template.sh is an ordinary update."
	exit 0
fi

if [ "$shares_history" -eq 0 ]; then
	skip "no shared history with the template" \
		"This instance was not cloned from the template — a repo made with \"Use this" \
		"template\", or bootstrapped on its own, shares no commit with its source, so" \
		"git has no merge base to work from." \
		"Join them once, which keeps your tree byte for byte:" \
		"  ./scripts/update-from-template.sh --adopt"
fi

if [ "$local_rev" = "$upstream_rev" ]; then
	say "template: already current ($(short "$local_rev"))"
	exit 0
fi

if git merge-base --is-ancestor "$upstream" HEAD 2>/dev/null; then
	say "template: already current"
	note "$upstream ($(short "$upstream_rev")) is already in your history; you are ahead of it."
	exit 0
fi

# --- the handover set (see THE HANDOVER in the header) -----------------------

# Test each candidate against the INCOMING .gitignore, not the one on disk: the
# rules that hand the file over arrive with the very update being applied.
incoming_ignore="$(mktemp)"
trap 'rm -f "$incoming_ignore"' EXIT
git show "$upstream_rev:.gitignore" >"$incoming_ignore" 2>/dev/null || : >"$incoming_ignore"

merge_base="$(git merge-base HEAD "$upstream" 2>/dev/null || true)"

# Read against the MERGE BASE, not against our HEAD. A two-dot diff calls every
# file this instance added a "deletion", which is both the wrong report and the
# wrong handover set — the template never had those files and is not handing
# anything over.
handover=()
while IFS= read -r p; do
	[ -n "$p" ] || continue
	# Tracked here, or git would not delete it in the first place.
	git ls-files --error-unmatch -- "$p" >/dev/null 2>&1 || continue
	# Claimed by the INCOMING rules — a plain deletion is not a handover.
	git -c "core.excludesFile=$incoming_ignore" check-ignore -q --no-index -- "$p" || continue
	# Carrying something of the operator's. A copy still identical to what the
	# template shipped loses nothing by being deleted, and skipping it here is
	# what keeps an untouched instance on a pure fast-forward.
	ours="$(git rev-parse --quiet --verify "HEAD:$p" 2>/dev/null || true)"
	base_blob="$(git rev-parse --quiet --verify "$merge_base:$p" 2>/dev/null || true)"
	[ -n "$ours" ] && [ "$ours" = "$base_blob" ] && continue
	handover+=("$p")
done < <(git diff --name-only --diff-filter=D "$merge_base" "$upstream_rev")

plan="merge"
if git merge-base --is-ancestor HEAD "$upstream" 2>/dev/null; then
	plan="fast-forward"
else
	# Diverged. Prove the merge is conflict-free BEFORE touching anything:
	# merge-tree computes the whole merge in memory and writes nothing.
	# merge-tree prints the merged tree's oid, then the conflicted paths, then a
	# BLANK LINE and a block of human-readable messages. Only the middle section
	# is a path list; reading past the blank line turns prose into filenames.
	if ! conflicts="$(git merge-tree --write-tree --name-only HEAD "$upstream" 2>/dev/null)"; then
		conflicted="$(printf '%s\n' "$conflicts" | tail -n +2 | sed '/^$/q' | sed '/^$/d')"
		# A handover path conflicts only as modify/delete, which the handover
		# below resolves before the merge runs. Do not report it as a blocker.
		for p in ${handover[@]+"${handover[@]}"}; do
			conflicted="$(printf '%s\n' "$conflicted" | grep -vxF "$p" || true)"
		done
		conflicted="$(printf '%s' "$conflicted" | sed '/^$/d')"
		n="$(printf '%s' "$conflicted" | grep -c . || true)"
	fi
	if [ "${n:-0}" -gt 0 ]; then
		say "template: skipped: $n file(s) would conflict"
		note "Your commits and the template's both changed these:"
		printf '%s\n' "$conflicted" | sed 's/^/    /'
		note ""
		note "Nothing was written. Resolve them yourself, in ordinary git:"
		note "  git merge $upstream        # then fix the conflicts and commit"
		note "Or see what the template did to one of them first:"
		note "  git diff HEAD...$upstream -- <path>"
		exit 2
	fi
fi

# --- report the plan ---------------------------------------------------------

# From the merge base forward: what the TEMPLATE did. A two-dot diff would list
# this instance's own run logs and context files as deletions and frighten
# somebody into thinking the update is about to remove them. It is not.
changed="$(git diff --name-status "$merge_base" "$upstream_rev")"
n_changed="$(printf '%s' "$changed" | grep -c . || true)"

instr_changed="$(git diff --name-only "$merge_base" "$upstream_rev" -- "${INSTRUCTION_PATHS[@]}" 2>/dev/null)"
wiring_changed="$(git diff --name-only "$merge_base" "$upstream_rev" -- "${WIRING_PATHS[@]}" 2>/dev/null)"

report_handover() {
	[ "${#handover[@]}" -gt 0 ] || return 0
	say ""
	say "handover: ${#handover[@]} file(s) the template now treats as yours"
	printf '%s\n' "${handover[@]}" | sed 's/^/    /'
	note "They stop being tracked here and stay exactly where they are on disk."
	note "Without this the update would conflict on every one of them."
}

if [ "$mode" = "preview" ]; then
	say "template: would $plan $(short "$local_rev")..$(short "$upstream_rev") — $n_changed file(s)"
	printf '%s\n' "$changed" | sed 's/^/    /'
	report_handover
	say ""
	say "Nothing was written. Apply it with:"
	note "./scripts/update-from-template.sh --apply"
	exit 0
fi

# --- apply -------------------------------------------------------------------

# The handover, first — see THE HANDOVER in the header. `git rm --cached` drops
# the path from the index and leaves the file untouched on disk, so after this
# both sides agree the file is gone from git and the merge has nothing to argue
# about. It is a commit, so it turns this one update into a merge; every later
# one fast-forwards again, because the tracked tree now matches the template's.
if [ "${#handover[@]}" -gt 0 ]; then
	if ! out="$(git rm --cached --quiet -- "${handover[@]}" 2>&1)"; then
		say "template: skipped: could not untrack the handover files"
		note "$(printf '%s' "$out" | head -3)"
		exit 2
	fi
	git commit --quiet -m "chore: accept the template's handover of instance-owned files

$(printf '%s\n' "${handover[@]}")

The template stopped tracking these and its .gitignore now declares them this
instance's own. Untracked here with git rm --cached, so the files stay on disk
untouched. Made by scripts/update-from-template.sh." || {
		say "template: skipped: could not commit the handover"
		exit 2
	}
	report_handover
	plan="merge" # our own commit means there is no fast-forward left to take
fi

if [ "$plan" = "fast-forward" ]; then
	if ! out="$(git merge --ff-only "$upstream" 2>&1)"; then
		say "template: skipped: fast-forward failed"
		note "$(printf '%s' "$out" | head -3)"
		exit 2
	fi
else
	if ! out="$(git merge --no-edit -m "chore: update from the fleet template

Merges $upstream ($(short "$upstream_rev")) into this control plane. Proved
conflict-free with git merge-tree before anything was written, by
scripts/update-from-template.sh." "$upstream" 2>&1)"; then
		# merge-tree said clean, so this is a race or a hook refusing. Leave no
		# half-merged tree behind.
		git merge --abort 2>/dev/null
		say "template: skipped: merge failed and was aborted"
		note "$(printf '%s' "$out" | head -3)"
		note "Your checkout is exactly as it was."
		exit 2
	fi
fi

new_rev="$(git rev-parse HEAD)"
# firstmate's vocabulary: one line per target, fixed verbs.
case "$plan" in
fast-forward) verb="updated" ;;
*) verb="merged" ;;
esac
say "template: $verb $(short "$local_rev")..$(short "$new_rev")"

# Files this instance committed that the new rules call instance-owned. These
# are REPORTED and never touched: untracking somebody's run logs is a policy
# decision about their own history, not part of an update. Saying nothing is
# not an option either — while they are tracked, this instance stays diverged
# and every future update is a merge rather than a fast-forward.
# --no-index, or this finds nothing: check-ignore skips tracked paths by
# default, which is exactly the set being looked for here.
tracked_owned="$(git ls-files | git check-ignore --no-index --stdin 2>/dev/null || true)"
n_owned="$(printf '%s' "$tracked_owned" | grep -c . || true)"
if [ "$n_owned" -gt 0 ]; then
	say ""
	say "still tracked, though the template treats them as yours: $n_owned file(s)"
	printf '%s\n' "$tracked_owned" | sed 's/^/    /'
	note "Left alone on purpose — they are your history to keep or drop, and git"
	note "never untracks on its own. While they are tracked this instance stays"
	note "diverged, so updates merge instead of fast-forwarding, which is safe but"
	note "can conflict. To take the trade and untrack them (the files stay on disk,"
	note "and stop being backed up by git):"
	note "  git ls-files | git check-ignore --no-index --stdin |"
	note "    xargs -r git rm --cached --"
fi

say ""
if [ -n "$instr_changed" ]; then
	say "restart-lead: yes"
	note "$(printf '%s' "$instr_changed" | tr '\n' ' ')"
	note "The running 'fleet' session froze FLEET.md and every skill it had loaded at"
	note "launch; new bytes on disk change nothing for it. Replace the agent:"
	note "  thurbox-cli session restart fleet"
	note "A restart resumes the conversation, so the old copy is still in its history."
	note "For an instruction change that has to win, start a fresh one instead:"
	note "  thurbox-cli session delete fleet   # the extension self-heals it"
else
	say "restart-lead: no"
fi

if [ -n "$wiring_changed" ]; then
	say "reinstall-extension: yes"
	note "$(printf '%s' "$wiring_changed" | tr '\n' ' ') changed — the installed extension"
	note "no longer matches the manifest it was rendered from:"
	note "  ./scripts/install-extension.sh"
else
	say "reinstall-extension: no"
fi

exit 0
