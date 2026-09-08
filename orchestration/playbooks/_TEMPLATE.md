# Playbook: <name>

> A reusable recipe for running thurbox against a class of work.

**When to use.** The situation this playbook fits (e.g. "ship one scoped feature
to a single repo", "apply the same change across N repos").

**Targets.** Which repos / kinds of repos this applies to.

## Inputs

- `goal` — what the run should achieve.
- `profile` — the session profile workers start under, from
  `../session-profiles.yaml` (default `default`).
- `<other>` — anything the operator must decide before launching.

## Sessions

How to decompose the goal into thurbox sessions. For each session, define:

- **name** — an imperative sentence in sentence case, ≤ 64 chars, no slashes
  (e.g. `Document the customization surface`). Spaces are fine. Don't prefix it
  with the repo; the session and the run log both carry that already.
- **repo / worktree** — the target repo and branch the worker operates on.
- **prompt** — self-contained; the worker never sees this conversation.
- **on collision** — the `--on-existing` mode, and why. `adopt` if re-running
  this playbook is meant to reconcile rather than duplicate; `fail` if a name
  already in use means the operator has two things confused. Never leave it
  defaulted — the default is `allow`, which makes a twin and breaks by-name
  addressing for both.
- **done when** — the acceptance signal (a PR, a passing test, a file).

## Run

1. Open a run log from `../runs/_TEMPLATE.md`.
2. Fast-forward each target repo's base branch, then `thurbox-cli session create`
   with `--parent "$THURBOX_SESSION"`, the chosen `--on-existing` mode, and the
   profile's flags from `./scripts/session-flags.sh`.
3. Get each new session past its trust dialog (`./scripts/session-trust.sh
   <uuid>`), then `thurbox-cli session send <uuid>` the prompt, ending with the
   result-FILE contract so the worker reports back instead of you polling — but
   only when the spawn returned `created: true`; an adopted session is already
   working. `./scripts/queue.sh dispatch` does both steps for you.
4. Read the results the workers wrote (`./scripts/queue.sh watch` for the
   timing, `collect` for the conclusions); record each outcome in the run log
   as it lands. Do not have workers mail you — `message send` wakes the lead
   and interrupts whoever is talking to it.
5. Review artifacts (PRs). A queue-dispatched session releases itself once its
   pull request merges (`collect` reaps it; `reap --dry-run` first if unsure);
   `session delete <uuid> --force` is for one you spawned by hand.

## Notes

Gotchas, ordering constraints, things that bit you last time.
