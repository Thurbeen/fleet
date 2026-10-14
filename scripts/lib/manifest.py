"""The sessions the extension manifest names: the lead's and the conductor's
`name` and `repo_path`.

The ONE READER of a manifest's `[[sessions]]` table. `queue.py`'s
control-plane guard, `notify_conductor.py` (through the queue), `sync_checkout.py`
and `install_extension.py` all ask this module, so they cannot disagree about
what the manifest says.

DECODED AS TOML, never matched as text. `fleet install-extension` writes
`repo_path` as a TOML basic string, which escapes every backslash, so on native
Windows the file holds `"C:\\\\Users\\\\you\\\\fleet"` for `C:\\Users\\you\\fleet`.
A regex capture returned the escaped spelling verbatim, and the guard then
judged the control plane foreign to ITSELF on every Windows machine — while
Linux, with no backslash in any path, never saw it. `tomllib` is the standard
library's own TOML reader, so there is no escape rule here to get wrong.

THE LEAD IS THE FIRST `[[sessions]]` TABLE AND THE CONDUCTOR THE SECOND, by
the order `extension.toml.in` writes them in. A fleet that has not enabled its
conductor renders one table, and `conductor_session` answers `(None, None)`
for it — the same "nothing here can tell" as a missing file.

Unknown is `(None, None)`: no file, a file that is not TOML, or no
`[[sessions]]` table. Every caller treats that as "nothing here can tell",
which must stay silent.
"""

from __future__ import annotations

import tomllib


def nth_session(text: str, index: int) -> tuple[str | None, str | None]:
    """(name, repo_path) of the `index`th [[sessions]] table in a manifest's text."""
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None, None
    sessions = doc.get("sessions")
    if not isinstance(sessions, list) or len(sessions) <= index or not isinstance(sessions[index], dict):
        return None, None
    table = sessions[index]
    name, repo = table.get("name"), table.get("repo_path")
    return (name if isinstance(name, str) else None, repo if isinstance(repo, str) else None)


def lead_session(text: str) -> tuple[str | None, str | None]:
    """(name, repo_path) of the lead: the first [[sessions]] table."""
    return nth_session(text, 0)


def conductor_session(text: str) -> tuple[str | None, str | None]:
    """(name, repo_path) of the conductor: the second [[sessions]] table, when there is one."""
    return nth_session(text, 1)


def _read(path: str, which) -> tuple[str | None, str | None]:
    try:
        with open(path, encoding="utf-8") as fh:
            return which(fh.read())
    except OSError:
        return None, None


def read_lead_session(path: str) -> tuple[str | None, str | None]:
    """`lead_session` of the manifest at `path`, or (None, None) when it cannot be read."""
    return _read(path, lead_session)


def read_conductor_session(path: str) -> tuple[str | None, str | None]:
    """`conductor_session` of the manifest at `path`, or (None, None) when it cannot be read."""
    return _read(path, conductor_session)
