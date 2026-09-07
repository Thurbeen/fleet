# Your playbooks

Playbooks **you** write go here. Everything in this directory is gitignored
except this file.

The three playbooks one level up — `_TEMPLATE.md`, `cross-repo-sweep.md`,
`ship-feature.md` — belong to the fleet template and are tracked. Yours belong
to this instance and are not, which is what keeps `./scripts/update-from-template.sh`
a clean fast-forward instead of a merge that can conflict on a recipe you care
about. `.gitignore` explains the split in full.

Start from `../_TEMPLATE.md`:

```bash
cp ../_TEMPLATE.md my-playbook.md
```

**Nothing here is backed up by this repo.** That is the deliberate trade — the
same one run logs and project context make. If a playbook here becomes worth
keeping, the durable home for it is the template itself: open a pull request
against the template repo and it becomes tracked, shipped, and yours forever.
