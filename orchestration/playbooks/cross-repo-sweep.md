# Playbook: cross-repo-sweep

> Apply the same class of change across many repos, one thurbox session each.

**When to use.** A change that repeats across repos: a dependency bump, a CI
tweak, a license header, a README badge, a config migration.

**Targets.** A list of repos from the registry (filter
`registry/repos.generated.yaml` by owner, language, or topic).

## Inputs

- `goal` — the repeated change, stated once, generically.
- `repos` — the target list. Derive it from the registry, don't guess.
- `max_parallel` — how many sessions to run at once (start small, e.g. 3).
- `profile` — the session profile every worker in the sweep starts under, from
  `../session-profiles.yaml` (default `default`). A sweep is the case that most
  wants one: the same settings, N times, stated once and reviewable.

## Sessions

One worker session **per repo**, all with the same prompt shape.

- **name** — the change, imperative and in sentence case, ending in the repo it
  targets: `Add a license header to widgets`. A sweep runs one goal many times
  and the name is a mailbox address, so a name that omits the repo is a name
  every session in the wave shares. Bare repo name, no owner — `--name` takes no
  slashes.
- **repo / worktree** — that repo, fresh worktree off its default branch.
- **prompt** — the generic goal + "adapt to this repo's stack; open a PR. If the
  change doesn't apply here, say so and stop." Either way it finishes by mailing
  the result.
- **done when** — a result message carrying a PR URL, or `NOT_APPLICABLE`.

## Run

1. Build the repo list from the registry; write it into the run log up front.
2. Fast-forward every target's base branch before spawning against it. A stale
   local `main` yields a worker that does correct work in a conflicting PR.
3. Launch in waves of `max_parallel`, each with `--parent "$THURBOX_SESSION"`,
   `--on-existing adopt`, and the profile's flags:

   ```bash
   mapfile -d '' -t flags < <(./scripts/session-flags.sh "$profile")
   out=$(thurbox-cli session create --name "$name" --repo-path "$repo" \
     --worktree-branch "$branch" --parent "$THURBOX_SESSION" \
     --on-existing adopt "${flags[@]}" --json)
   [ "$(jq -r .created <<<"$out")" = true ] && send_the_brief
   ```

   `adopt` is the whole point of a sweep: this loop is a driver reconciling
   desired state, it gets re-run whenever a wave is resumed or a repo list
   grows, and the default (`allow`) would answer that with a second session
   per repo — after which the name that addresses each worker's mailbox
   matches two sessions and is refused rather than guessed. `created: false`
   means that repo is already covered, so do not re-send its brief.
4. Drain the inbox (`message inbox --for "$THURBOX_SESSION" --claim --json`);
   as one repo reports, start the next. A wave can also stall on a worker
   waiting for an approval nobody is going to give: `session list --json`
   reports each one's `state`, and `blocked` is the word for that. Read the
   skill's session-state section before you act on any of those words —
   `idle` means the agent said it is at rest, and it is the only one that does.
5. Collect PR URLs and `NOT_APPLICABLE` into the run log's session table.
6. Review PRs in a batch. `session delete <uuid> --force` per repo as it closes
   out.

## Notes

- Prompts must be self-contained and repo-agnostic — workers don't share context
  with you or with each other.
- Record the profile in the run log alongside the repo list. Two sweeps of the
  same goal under different settings are two different runs.
- Log every repo that reported `NOT_APPLICABLE` so the sweep is auditable and
  not silently partial. This is the reason to prefer the mailbox over polling
  `gh pr list`: a PR poll cannot tell "doesn't apply here" from "still working".
