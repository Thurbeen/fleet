# Recorded `glab` output

What `scripts/queue-selftest.sh` §14 drives the GitLab adapter with, so that
the adapter's parsing is tested against **what `glab` actually prints** rather
than against what this repo assumed it prints. Nothing here reaches the
network when the selftest runs; a fake `glab` on `PATH` replays these files.

Every `mr-*` file below was recorded on **2026-09-10** with **`glab` 1.117.0
(44790937b)** against **gitlab.com**, unauthenticated, from the public project
`gitlab-org/cli`; those commands are exact and repeatable. `auth-status.stderr`
is the one exception on every count — it prints whatever the running machine is
configured for, and it was recorded elsewhere and edited afterwards. Its own
bullet below owns its provenance.

| File | Command |
| --- | --- |
| `mr-view.json` | `glab mr view 3877 -R https://gitlab.com/gitlab-org/cli -F json` |
| `mr-commits.json` | `glab api "projects/gitlab-org%2Fcli/merge_requests/3875/commits?per_page=100" --hostname gitlab.com` |
| `mr-view-missing.json` | `glab mr view 999999 -R https://gitlab.com/gitlab-org/cli -F json` — stdout |
| `mr-view-missing.stderr` | the same call's stderr |
| `auth-status.stderr` | `glab auth status --all` — stderr, **with the hostnames and account renamed**, see below |

`glab mr list -F json` answers with the same objects minus `head_pipeline`, so
it is not recorded separately — the adapter reads only `iid` out of a listing
and asks `mr view` for each one, which is exactly because the pipeline is
missing from the list.

Each file was kept for a specific reason, and the reason is what makes it worth
its bytes in a control-plane repo:

- **`mr-view.json` is a FORK's merge request.** `source_project_id` is
  86287219 and `target_project_id` is 34675721, so it is the one shape whose
  misreading merges a stranger's code — recorded rather than imagined.
  Its `head_pipeline.status` is `failed`, and its `detailed_merge_status` is
  `title_regex`, a word absent from the documented set the adapter was written
  against. That is why the adapter reads only `mergeable` and `conflict` out
  of that field and calls everything else "the forge has not said".
- **`mr-commits.json` is NEWEST FIRST.** Fleet's `Commit` list is oldest
  first, so the adapter reverses; this file is the evidence that it must.
- **`mr-view-missing.*` is the error shape**, and it is two streams: the
  reason is the JSON on *stdout*, while stderr carries a blank-line-padded
  `ERROR` box whose first line is decoration. An adapter that read stderr
  first would report the box.
- **`auth-status.stderr` is where the GitLab host list comes from**, and it
  is the one file here that was **edited after recording**. It was recorded on
  **2026-09-11** with the same `glab`, from a machine logged in to one
  self-hosted instance and not to gitlab.com; that instance's hostname, the
  account name and the home directory were then replaced with
  `gitlab.example.com`, `some-account` and `/home/user`, because this
  repository is public. Nothing else was touched, so the shape is real —
  which is the whole point, since `forge.configured_hosts` reads it by shape:

  - the report goes to **stderr**, not stdout, which is the opposite of `gh`;
  - each instance is a **bare hostname, alone on an unindented line**, with
    everything said about it indented underneath;
  - the trailing `ERROR` box — its blank-padded lines, and the line of spaces
    inside it — is decoration that must not read as a host, and it is here
    verbatim, trailing whitespace and all, so that it is tested rather than
    imagined;
  - `glab` **exits non-zero** because one of the two instances has no token,
    which is the ordinary state of a machine logged in to one and not the
    other. An adapter that read the exit code would discover nothing on
    exactly the machines this exists for.

## What is NOT here

Two answers the adapter needs are behind authentication, and this recording
had none. They are **constructed inside the selftest and labelled there**, from
the field names in GitLab's REST API documentation — not recorded, and not
presented as recorded:

- `GET /projects/:id` → `squash_option`, which is what can forbid the only
  merge method fleet uses. Unauthenticated it comes back `null`.
- `GET /projects/:id/members/all` → `access_level`. Unauthenticated it is
  `401 Unauthorized`.

## Refreshing

Re-run the commands above. `3877` and `3875` are ordinary merge requests on a
public project and will eventually be closed; when they are, pick any open
merge request with a fork source and any with several commits, and update the
numbers in this table.
