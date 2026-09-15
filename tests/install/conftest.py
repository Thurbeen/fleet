from __future__ import annotations

from pathlib import Path

import pytest
from installkit import build_upstream, clone


@pytest.fixture(scope="session")
def upstream(tmp_path_factory) -> Path:
    """This tree, committed once per run: every test clones it rather than copying."""
    return build_upstream(tmp_path_factory.mktemp("upstream"))


@pytest.fixture
def checkout(upstream, tmp_path) -> Path:
    """A clone of the tree under test, which `fleet install` may write into."""
    return clone(upstream, tmp_path / "fleet")
