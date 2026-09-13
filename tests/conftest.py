"""Load fleet's modules the way they load each other: by path, under a name of their own.

Not by putting `scripts/lib` on sys.path: `queue.py` there would shadow the
standard library's `queue`, which pytest itself imports.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"


def load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, LIB / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        del sys.modules[name]
        raise
    return mod


@pytest.fixture
def fp():
    return load("fleet_platform", "fleet_platform.py")


@pytest.fixture
def queue_mod():
    return load("fleet_queue_under_test", "queue.py")
