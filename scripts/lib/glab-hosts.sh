# shellcheck shell=bash
# Which GitLab instances this machine has a credential for, ONE HOST AT A TIME.
#
# SOURCED, never executed. The counterpart to gh-accounts.sh one forge over,
# and it exists for the same reason: the CLI's own status command is
# all-or-nothing, so the question fleet actually has — "is there a credential
# for the instance this work lives on" — cannot be read off its exit code.
#
# WHY THIS EXISTS. `glab auth status` exits non-zero when ANY configured
# instance fails. An operator authenticated to their company's GitLab, where
# their repositories live, and not to gitlab.com, which they have never used,
# therefore got
#
#   missing  glab auth    reading a merge request needs a credential ...
#            install: glab auth login
#
# from scripts/preflight.sh — a remedy they had already run. That contradicts
# the forge seam, where which hosts a CLI owns comes from that CLI's own
# variable and a SELF-HOSTED INSTANCE IS THE ORDINARY CASE rather than a
# special one; scripts/lib/forge.py's header owns that argument.
#
# Verified against glab 1.117.0, which is where these three shapes come from:
# `--hostname <host>` answers for one instance and exits 0/1 for it alone,
# `--all` lists every configured instance at column 0, and a bare `auth
# status` answers for `GITLAB_HOST` when that is set.
#
# IT READS AND NEVER WRITES. `glab auth login` is interactive and the
# operator's; nothing here logs anybody in or touches their glab config.

# glab_hosts
#
# Every GitLab instance `glab` has configured, one per line.
#
# PRINTS NOTHING, successfully, when the list cannot be read — an older `glab`
# with no `--all`, or none installed. That is the documented fallback and not a
# failure: a caller that gets no lines asks the bare `glab auth status`, which
# is exactly what every caller did before it asked per host.
#
# Hosts are the only lines `--all` puts at column 0; everything else it prints
# — the per-instance findings, and the error banner when one of them failed —
# is indented. The `.` is what keeps a stray unindented word out: an instance
# is a hostname.
glab_hosts() {
	command -v glab >/dev/null 2>&1 || return 0
	glab auth status --all 2>&1 |
		sed -n 's/^\([A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9._-]*\)[[:space:]]*$/\1/p' ||
		true
}

# glab_host_ok <host>
#
# Whether that ONE instance authenticates. Exit status is the whole answer.
glab_host_ok() {
	command -v glab >/dev/null 2>&1 || return 1
	glab auth status --hostname "$1" >/dev/null 2>&1
}
