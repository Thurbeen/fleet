"""The GitHub adapter's own parsing of a checkout's `origin`.

The counterpart to `test_gitlab.py`'s remote-form check, and it exists because
GitHub had only half of it: the scp pattern, which reads `https` as the host
and then fails on the `//`. Everything that identifies a repository from a
CHECKOUT rather than from an artifact URL went through that — the agent policy
above all — so an ordinary `https://` clone, which is what `install.sh` makes,
was covered by no rule at all and refused at dispatch.
"""

from __future__ import annotations

import pytest

from harness import lib


@pytest.fixture
def forge(monkeypatch):
    monkeypatch.delenv("GH_HOST", raising=False)
    mod = lib("forge.py")
    mod.reset()
    return mod


def test_every_ordinary_github_remote_form_names_the_same_repository(forge):
    gh = forge.GitHubForge()
    want = forge.RepoId("github.com", "Thurbeen/fleet")
    for remote in ("https://github.com/Thurbeen/fleet.git",
                   "https://github.com/Thurbeen/fleet",
                   "https://github.com/Thurbeen/fleet/",
                   "http://github.com/Thurbeen/fleet.git",
                   "https://someone@github.com/Thurbeen/fleet.git",
                   "ssh://git@github.com/Thurbeen/fleet.git",
                   "git://github.com/Thurbeen/fleet.git",
                   "git@github.com:Thurbeen/fleet.git",
                   "github.com/Thurbeen/fleet"):
        assert gh.repo_from_remote(remote) == want, remote


def test_a_remote_that_is_not_ours_is_still_not_ours(forge):
    """The scheme half must not have widened what this adapter CLAIMS.

    Host ownership is the whole guard between two adapters that both parse a
    URL, so a GitLab remote matching the shape is not a GitHub repository.
    """
    gh = forge.GitHubForge()
    for remote in ("https://gitlab.com/group/project.git",
                   "git@gitlab.com:group/project.git",
                   "ssh://git@gitlab.example.com/acme/widgets.git",
                   "https://github.com/onlyonesegment.git",
                   ""):
        assert gh.repo_from_remote(remote) is None, remote
    # And the module-level dispatcher still hands a GitLab remote to GitLab.
    assert forge.repo_from_remote("https://gitlab.com/group/project.git") == \
        forge.RepoId("gitlab.com", "group/project")
