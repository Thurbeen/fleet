import os
import sys
from pathlib import Path

import pytest

from harness import STUBS, Stubs, install_stubs, isolate

# `Stubs` renders pull request bodies with the stub package's own attest.py, so
# a fixture and the tool it feeds cannot disagree about the attestation's shape.
sys.path.insert(0, str(STUBS / "src"))


@pytest.fixture(scope="session")
def stub_bin(tmp_path_factory) -> Path:
    """The directory holding every stub tool, installed once per run."""
    return install_stubs(tmp_path_factory.mktemp("stubs"))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch, stub_bin) -> Path:
    """Every test runs isolated: see harness.py for what that pins."""
    root = tmp_path / "env"
    env = isolate(dict(os.environ), root, stub_bin)
    for key in list(os.environ):
        if key not in env:
            monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return root


@pytest.fixture
def stubs(isolated_env) -> Stubs:
    return Stubs(isolated_env / "stubs")
