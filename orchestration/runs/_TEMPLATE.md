# Run: `<YYYY-MM-DD>` — `<slug>`

> Copy to `orchestration/runs/<YYYY-MM-DD>-<slug>.md`. This log is the source of
> truth for what happened.

- **Goal.** What this run is meant to achieve.
- **Playbook.** `../playbooks/<name>.md` (or "ad hoc").
- **Profile.** `../session-profiles.yaml` name the workers started under
  (`default` if you did not pick one). Settings are part of what produced the
  result, so a run that does not name one cannot be reproduced.
- **Started.** <date/time> · **Status.** planning | running | done | abandoned

## Sessions

Name each session as an imperative sentence describing the work, in sentence
case — no repo prefix, the repo has its own column.

| Session name | Repo | Intent | Status | Artifact / PR |
|---|---|---|---|---|
| `Document the customization surface` | `owner/repo` | one line | running / done | link |

## Timeline

- `<time>` — launched `Document the customization surface` against `owner/repo`.
- `<time>` — drained the inbox; that session reported …

## Outcome

What shipped, what's pending, what to follow up on. Update the relevant
`registry/context/<repo>.md` if this run changed a project's goals or relations.
