# Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io/),
certificate by [SignPath Foundation](https://signpath.org/).

## What is signed

Only `fleet.exe`, the Windows x86_64 executable in each
[Fleet release](https://github.com/Thurbeen/fleet/releases), is signed with
Authenticode. It is built from this repository's source alone. Linux binaries,
installers, checksums and metadata are not Authenticode-signed; every release
asset keeps its SHA-256 checksum and GitHub build provenance attestation.

## How releases are built

A release is an immutable `v2.*` tag on a commit merged into `main`. The
[release workflow](../.github/workflows/cd.yml) runs on GitHub-hosted
runners. It tests that exact commit, then builds `fleet.exe` and submits it to
SignPath. A person listed under Approvers approves every signing request in
SignPath. The workflow then packages the signed file and runs a clean install
of the archive. It checks the installed executable with
`Get-AuthenticodeSignature`. Checksums and the provenance attestation are
computed afterwards, so they cover the signed bytes. Publication also needs
approval in the protected `release` environment. Build scripts and CI
configuration are source and go through the same review as code.

Only publishing runs sign. Dry runs and branch dispatches stay unsigned and
create no signing request.

## Team roles

- Committers and reviewers: people with write access to this repository. A
  change from anyone else is reviewed by one of them before merge.
- Approvers: [owners of the Thurbeen organization](https://github.com/orgs/Thurbeen/people)
  with the admin role on this repository.

Every team member must use multi-factor authentication for GitHub and
SignPath.

## Privacy

This program will not transfer any information to other networked systems
unless specifically requested by the user or the person installing or
operating it. Fleet has no telemetry. It reaches a forge, an agent harness or
a terminal session only through the tools and accounts the user configures.

## Verify a download

```powershell
Get-AuthenticodeSignature .\fleet.exe
```

The status must be `Valid`. To check the archive and its provenance as well:

```sh
sha256sum --check --ignore-missing fleet-<version>-checksums.txt
gh attestation verify fleet-<version>-x86_64-pc-windows-msvc.zip --repo Thurbeen/fleet
```
