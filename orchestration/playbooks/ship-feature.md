# Playbook: ship-feature

> Reusable recipe for shipping one scoped change to a single repo via thurbox.

**When to use.** A well-defined feature or fix that fits in one repo and one PR.

**Targets.** Any active repo in the registry.

## Inputs

- `goal` — the change to make, stated as an outcome.
- `repo` — `owner/name`.
- `base` — base branch (default the repo's default branch).
- `profile` — the session profile the worker starts under, from
  `../session-profiles.yaml` (default `default`). Record which one in the run
  log: it is part of what produced the result.

## Sessions

One worker session.

- **name** — the change, imperative and in sentence case:
  `Cache the registry sync between runs`. No repo prefix — the session already
  carries the repo, and so does the run log.
- **repo / worktree** — `repo`, fresh worktree off `base`.
- **prompt** — self-contained: the goal, acceptance criteria, "open a PR when
  done, then write the result file carrying the PR URL". Point the worker at
  the repo's own conventions (its `AGENTS.md` / `CLAUDE.md`, tests, lint)
  rather than restating them here.
- **done when** — a result file carrying the PR URL, CI green.

## Run

1. Read `registry/context/<repo>.md` for goals and gotchas; fold the relevant
   bits into the prompt.
2. Open a run log.
3. Fast-forward `base` in the target repo, then `session create --parent
   "$THURBOX_SESSION" --on-existing adopt` with the profile's flags →
   `session send`.

   `adopt` because this step is a reconciliation: the run may be resumed after
   an interruption, and re-running it should end with one worker on this goal,
   not two sharing a name. It returns `created: false` when the session was
   already there — **skip the send in that case**, or the brief interrupts a
   worker mid-turn. The skill's §1c and §1d have the mechanics.
4. Read the result file the worker wrote, when you choose. Do not ask it to
   mail you: `message send` wakes the lead and interrupts whoever is talking to
   it. `./scripts/queue.sh watch` gives the timing without interrupting anyone.
5. Review the PR; record it in the run log; merge or hand back.
6. `session delete <uuid> --force` once merged or abandoned.

## Notes

Keep it to one repo. If the change spans repos, use `cross-repo-sweep` instead.

Settings that shape the agent — model, effort, feature flags, a command line
thurbox has never heard of — do not belong in the prompt or on the spawn
command line. They belong in a profile in `../session-profiles.yaml`, so the
run log can name it and a reviewer can read it.

A prompt longer than a sentence does not survive `session send`, which types the
text and presses Enter. Write it to `BRIEF.md` in the worker's worktree and send
a one-liner pointing at it — and tell the worker to delete `BRIEF.md` before it
commits, or the brief lands in the PR.
