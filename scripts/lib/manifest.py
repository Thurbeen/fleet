"""The lead session as the extension manifest names it: `name` and `repo_path`.

The ONE READER of a manifest's `[[sessions]]` table. `queue.py`'s
control-plane guard, `notify_lead.py` (through the queue), `sync_checkout.py`
and `install_extension.py` all ask this module, so they cannot disagree about
what the manifest says.

DECODED AS TOML, never matched as text. `fleet install-extension` writes
`repo_path` as a TOML basic string, which escapes every backslash, so on native
Windows the file holds `"C:\\\\Users\\\\you\\\\fleet"` for `C:\\Users\\you\\fleet`.
A regex capture returned the escaped spelling verbatim, and the guard then
judged the control plane foreign to ITSELF on every Windows machine — while
Linux, with no backslash in any path, never saw it. `tomllib` is the standard
library's own TOML reader, so there is no escape rule here to get wrong.

Unknown is `(None, None)`: no file, a file that is not TOML, or no
`[[sessions]]` table. Every caller treats that as "nothing here can tell",
which must stay silent.
"""

from __future__ import annotations

import tomllib


def lead_session(text: str) -> tuple[str | None, str | None]:
    """(name, repo_path) of the first [[sessions]] table in a manifest's text."""
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None, None
    sessions = doc.get("sessions")
    if not isinstance(sessions, list) or not sessions or not isinstance(sessions[0], dict):
        return None, None
    first = sessions[0]
    name, repo = first.get("name"), first.get("repo_path")
    return (name if isinstance(name, str) else None, repo if isinstance(repo, str) else None)


def read_lead_session(path: str) -> tuple[str | None, str | None]:
    """`lead_session` of the manifest at `path`, or (None, None) when it cannot be read."""
    try:
        with open(path, encoding="utf-8") as fh:
            return lead_session(fh.read())
    except OSError:
        return None, None
