# Playbook: ship-feature

> Reusable recipe for shipping one scoped change to a single repo via thurbox.

**When to use.** A well-defined feature or fix that fits in one repo and one PR.

**Targets.** Any active repo in the registry.

## Inputs

- `goal` — the change to make, stated as an outcome.
- `repo` — `owner/name`.
- `base` — base branch (default the repo's default branch).

## Sessions

One worker session.

- **name** — the change, imperative and in sentence case:
  `Cache the registry sync between runs`. No repo prefix — the session already
  carries the repo, and so does the run log.
- **repo / worktree** — `repo`, fresh worktree off `base`.
- **prompt** — self-contained: the goal, acceptance criteria, "open a PR when
  done, then mail the PR URL back". Point the worker at the repo's own
  conventions (its `AGENTS.md` / `CLAUDE.md`, tests, lint) rather than
  restating them here.
- **done when** — a result message carrying the PR URL, CI green.

## Run

1. Read `registry/context/<repo>.md` for goals and gotchas; fold the relevant
   bits into the prompt.
2. Open a run log.
3. Fast-forward `base` in the target repo, then `session create --parent
   "$THURBOX_SESSION"` → `session send`.
4. Wait for the worker's result message; the send wakes you, so don't poll.
5. Review the PR; record it in the run log; merge or hand back.
6. `session delete <uuid> --force` once merged or abandoned.

## Notes

Keep it to one repo. If the change spans repos, use `cross-repo-sweep` instead.

A prompt longer than a sentence does not survive `session send`, which types the
text and presses Enter. Write it to `BRIEF.md` in the worker's worktree and send
a one-liner pointing at it — and tell the worker to delete `BRIEF.md` before it
commits, or the brief lands in the PR.
