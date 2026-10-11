"""The freshness CLI contract must survive a delay between collection and execution."""

from pathlib import Path

from harness import PYTHON, run


def test_freshness_fixture_survives_delayed_execution():
    source = Path(__file__).with_name("test_prs_records.py")
    script = f"""
import datetime
import runpy
import sys

sys.path.insert(0, {str(source.parent.parent)!r})

real_datetime = datetime.datetime
clock = real_datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)

class CollectionClock(real_datetime):
    @classmethod
    def now(cls, tz=None):
        return clock.astimezone(tz)

datetime.datetime = CollectionClock
tests = runpy.run_path({str(source)!r})
clock = real_datetime.now(datetime.timezone.utc)
tests["test_the_freshness_window_is_two_shepherd_passes"]()
"""
    result = run([*PYTHON, "-c", script])
    assert result.code == 0, result.out
