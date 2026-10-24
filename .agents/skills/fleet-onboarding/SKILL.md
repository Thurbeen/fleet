---
name: fleet-onboarding
description: Use when explicitly asked to set up a fresh Fleet deployment.
---

# Install and configure Fleet

Read README.md's installation and deliberate cutover sections first.
Fleet v2 is a beta and Fleet v1 stays the default: install v2 only when the
user asks for it. Check the release's supported target and checksum. Inspect
then run `scripts/install-release.sh` or `scripts/install-release.ps1` with
`FLEET_VERSION` set to the v2 tag the user chose; the installers refuse to run
without one. Do not pipe a remote installer into a shell. An offline release
base is supported.

Choose a fresh data directory and author a versioned TOML config. Run
`fleet version --json` before applying Tasks. Apply each Task with
`fleet apply -f FILE`, keeping `dispatch` false until it has been inspected.
Use the fake-runtime examples for an isolated trial. Real sessions use the
thurbox runtime adapter, on the host each Task names.

Installation never starts a manager or performs a live migration. Preserve
existing deployments and active sessions. A separate, explicit no-child
cutover is required before changing an operational manager. Never configure
credentials by copying secret values into manifests or argv.
