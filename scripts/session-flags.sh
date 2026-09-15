#!/usr/bin/env bash
# A forwarder to `uv run fleet session-flags` (scripts/lib/session_profiles.py),
# kept while instructions still name this path. `--help` prints the usage.
exec uv run --quiet --project "$(dirname "${BASH_SOURCE[0]}")/.." fleet session-flags "$@"
