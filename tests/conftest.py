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


def pytest_addoption(parser):
    parser.addoption("--fleet-partition", help="one deterministic I/N slice of collected test node ids")


def pytest_collection_modifyitems(config, items):
    partition = config.getoption("--fleet-partition")
    if not partition:
        return
    index, count = map(int, partition.split("/"))
    # An area's membership cannot shift when another area is added to a job:
    # the workflow validator proves coverage per area, not per invocation.
    positions, counts = {}, {}
    for item in sorted(items, key=lambda item: item.nodeid):
        parts = item.path.relative_to(Path(__file__).parent).parts
        area = parts[0] if len(parts) > 1 else "."
        positions[item.nodeid] = counts.get(area, 0)
        counts[area] = positions[item.nodeid] + 1
    selected, deselected = [], []
    for item in items:
        (selected if positions[item.nodeid] % count == index - 1 else deselected).append(item)
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected
