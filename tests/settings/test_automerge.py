"""Where fleet may merge, and every other tracked setting, belong to the operator.

The merge allowlist used to be a literal in scripts/lib/queue.py, so naming a
repository meant committing it to a PUBLIC repo, and every clone inherited the
last operator's merge rights. It is `orchestration/auto-merge.conf` now, the
operator's and gitignored, and these tests keep it from drifting back: the
tracked example names NOTHING, and the set a fresh clone reads is empty.

The same rule for every tracked setting: an owner, a repository, a publishing
tool or an agent written into a file this repo SHIPS is one operator's setup
handed to every clone. The example files carry defaults, never names.
"""

import re
import shutil

from harness import REPO, queue_module

ORCH = REPO / "orchestration"


def values(conf: str, key: str) -> list[str]:
    """EVERY occurrence of `key=`, not the first: a leak appended below a correct
    line is exactly the edit a first-match read would wave through."""
    text = (ORCH / conf).read_text(encoding="utf-8")
    return ["".join(m.group(1).split()) for m in re.finditer(rf"^{key}=(.*)$", text, re.MULTILINE)]


def test_the_tracked_allowlist_names_no_repository():
    live = [line for line in (ORCH / "auto-merge.example.conf").read_text(encoding="utf-8").splitlines()
            if line.split("#", 1)[0].strip()]
    assert live == [], f"auto-merge.example.conf names a repository; the tracked copy must name none: {live}"


def test_a_fresh_clone_merges_nowhere(tmp_path, monkeypatch):
    # Read the way `shepherd` reads it, from a root holding the tracked example
    # and nothing else — never this checkout's root, where an operator's own
    # auto-merge.conf would answer instead.
    (tmp_path / "orchestration").mkdir()
    shutil.copy(ORCH / "auto-merge.example.conf", tmp_path / "orchestration")
    monkeypatch.delenv("FLEET_AUTO_MERGE_ROOT", raising=False)
    monkeypatch.setenv("FLEET_AUTO_MERGE_REPOS", "")
    out = queue_module("print(sorted(q.auto_merge_repos(sys.argv[1])))\n", str(tmp_path))
    assert out.strip() == "[]", out


def test_the_tracked_publish_default_needs_no_tool():
    assert values("publish.example.conf", "METHOD") == ["pr"]
    assert all(v == "" for v in values("publish.example.conf", "HOW")), "HOW names a tool; that is the operator's"


def test_the_tracked_agent_settings_name_no_agent_or_vendor():
    for key in ("AGENT", "FUEL_PROVIDER", "LIMIT_BANNER", "TRANSCRIPT_DIR", "AGENT_PROVIDERS", "TRUST_SIGNATURE",
                "TRUST_KEYS"):
        assert all(v == "" for v in values("agent.example.conf", key)), f"agent.example.conf ships {key}"


def test_every_publish_method_is_an_artifact_shape():
    out = queue_module("print(' '.join(sorted(q.PUBLISH_METHODS)))\n")
    assert out.strip() == "attested none note pr push"
