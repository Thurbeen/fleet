"""The voice is a setting and not a literal.

FLEET.md is the lead's standing context, and the two names in it are the
operator's choice. `orchestration/voice.example.conf` is the one place they are
spelled, the installer renders them into the gitignored FLEET.rendered.md, and
FLEET.md carries placeholders: a name written into it would be a second copy
no voice.conf could move. The render itself is proved off an override conf into
a temp directory, so no operator's rendered payload is touched.
"""

from __future__ import annotations

import re

from harness import REPO, expect, refute, run_fleet, write

EXAMPLE = REPO / "orchestration" / "voice.example.conf"
FLEET_MD = (REPO / "FLEET.md").read_text(encoding="utf-8")


def test_the_tracked_defaults_set_both_names():
    text = EXAMPLE.read_text(encoding="utf-8")
    for key in ("OPERATOR_NAME", "ASSISTANT_NAME"):
        found = re.search(rf"^{key}=(.+)$", text, re.M)
        assert found, f"{EXAMPLE.name} sets no {key}"


def test_fleet_md_carries_both_placeholders_and_neither_default():
    expect(FLEET_MD, "@OPERATOR_NAME@", "@ASSISTANT_NAME@")
    text = EXAMPLE.read_text(encoding="utf-8")
    for key in ("OPERATOR_NAME", "ASSISTANT_NAME"):
        refute(FLEET_MD, re.search(rf"^{key}=(.+)$", text, re.M).group(1))


def test_the_render_carries_the_confs_names_and_no_placeholder(tmp_path):
    conf = tmp_path / "voice.conf"
    write(conf, "OPERATOR_NAME=GATEOP\nASSISTANT_NAME=GATEAI\n")
    done = run_fleet("install-extension", "--render-only", str(tmp_path), FLEET_VOICE_CONF=str(conf))
    assert done.code == 0, done.out
    rendered = (tmp_path / "FLEET.rendered.md").read_text(encoding="utf-8")
    expect(rendered, "GATEOP", "GATEAI")
    assert not re.search(r"@(OPERATOR|ASSISTANT)_NAME@", rendered)
