"""Both names asked before the render, kept once answered, refused before anything is written.

Onboarding asks what the lead calls the operator and what it answers to BEFORE
the extension renders them, because after it a new name is a re-install and a
lead restart. The answer is the operator's gitignored voice.conf, and the
renderer's own rule decides which names are refused. The names arrive as
arguments, which reach Python as Unicode on every console.
"""

from __future__ import annotations

import re

import pytest
from panekit import clone, thurbox

from harness import PYTHON, REPO, expect, refute, run

EXAMPLE = (REPO / "orchestration" / "voice.example.conf").read_text(encoding="utf-8")
DEFAULT_OPERATOR = re.search(r"^OPERATOR_NAME=(.*)$", EXAMPLE, re.M).group(1)
DEFAULT_LEAD = re.search(r"^ASSISTANT_NAME=(.*)$", EXAMPLE, re.M).group(1)


@pytest.fixture
def fleet(tmp_path):
    return clone(tmp_path)


def voice(fleet, *args: str):
    return run([*PYTHON, str(fleet / "scripts" / "lib" / "voice_ask.py"), *args], cwd=fleet, FLEET_VOICE_CONF=None)


def conf(fleet):
    return fleet / "orchestration" / "voice.conf"


def install(fleet):
    return run([*PYTHON, str(fleet / "scripts" / "lib" / "install_extension.py")], cwd=fleet, FLEET_VOICE_CONF=None)


def test_nothing_answered_asks_offering_the_tracked_defaults(fleet):
    done = voice(fleet)
    assert done.code == 0, done.out
    assert done.stdout.startswith("ask"), done.out
    expect(done.out, DEFAULT_OPERATOR, DEFAULT_LEAD, "fleet voice-ask set")
    assert not conf(fleet).exists()


@pytest.mark.parametrize("bad", ["Ri|ley", "Ri\\ley", "R&D", "o@k", "O'Neil", 'say "hi"', ""])
def test_a_name_the_renderer_refuses_is_refused_before_anything_is_written(fleet, bad):
    done = voice(fleet, "set", bad, "Mother")
    assert done.code != 0, done.out
    assert not conf(fleet).exists()


def test_a_name_spanning_two_lines_is_refused(fleet):
    done = voice(fleet, "set", "Ripley", "Mo\nther")
    assert done.code != 0, done.out
    assert not conf(fleet).exists()


def test_replace_before_any_install_points_at_installing(fleet):
    """FLEET.rendered.md decides which message prints, not whether --replace was used."""
    done = voice(fleet, "set", "--replace", "Scratch", "Name")
    assert done.code == 0, done.out
    expect(done.out, "Next: uv run fleet install-extension renders them.")
    refute(done.out, "update-fleet")


@pytest.fixture
def answered(fleet, stubs):
    thurbox(stubs)
    done = voice(fleet, "set", "Ripley", "Mother")
    assert done.code == 0, done.out
    return fleet


def test_two_names_are_recorded_and_the_render_carries_them(answered, stubs):
    text = conf(answered).read_text(encoding="utf-8")
    expect(text, "OPERATOR_NAME=Ripley", "ASSISTANT_NAME=Mother")
    done = install(answered)
    assert done.code == 0, done.out
    rendered = (answered / "FLEET.rendered.md").read_text(encoding="utf-8")
    expect(rendered, "Ripley", "Mother")
    refute(rendered, DEFAULT_OPERATOR)
    assert not run(["git", "status", "--porcelain"], cwd=answered).stdout, "the answer or the render is tracked"


def test_answered_once_is_never_asked_again_or_overwritten_unasked(answered):
    assert install(answered).code == 0
    before = conf(answered).read_bytes()
    done = voice(answered)
    assert done.stdout.startswith("skip"), done.out
    expect(done.out, "Ripley")

    done = voice(answered, "set", "Dallas", "Ash")
    assert done.code != 0
    expect(done.out, "--replace")
    assert conf(answered).read_bytes() == before

    assert voice(answered, "set", "--replace", "Dal|las", "Ash").code != 0
    assert conf(answered).read_bytes() == before

    done = voice(answered, "set", "--replace", "Dallas", "Ash")
    assert done.code == 0, done.out
    expect(conf(answered).read_text(encoding="utf-8"), "OPERATOR_NAME=Dallas")
    expect(done.out, "update-fleet")


def test_a_name_outside_ascii_is_recorded_and_rendered_as_utf8(fleet, stubs):
    thurbox(stubs)
    done = voice(fleet, "set", "Zoë", "Ōkami")
    assert done.code == 0, done.out
    expect(conf(fleet).read_text(encoding="utf-8"), "OPERATOR_NAME=Zoë", "ASSISTANT_NAME=Ōkami")
    assert install(fleet).code == 0
    expect((fleet / "FLEET.rendered.md").read_text(encoding="utf-8"), "Zoë", "Ōkami")


def test_usage_errors_exit_2(fleet):
    assert voice(fleet, "set", "only-one").code == 2
    assert voice(fleet, "bogus").code == 2
