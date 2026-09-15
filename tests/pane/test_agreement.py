"""The pane's wiring, held to one spelling of every string that must agree.

The pane names a slot, and the installer and two skills each hand the operator
a `layout.lua` block naming that slot. If any of them drifts, the operator is
given a block that places a slot nothing fills: the pane loads, lists, and
draws nothing, and every message they have says it should work. The same goes
for the F-key, the name `plugin remove` takes, the fuel reading's one source,
its field names and threshold, the lead's name, and the glyphs.

Not a Lua linter, deliberately: the pane's own gate is `thurbox-cli plugin
check`, which needs a thurbox install and belongs at install time.
"""

from __future__ import annotations

import re

import pytest
from panekit import PANE, clone, slot, thurbox

from harness import PYTHON, REPO, expect, lib, run

PANE_TEXT = PANE.read_text(encoding="utf-8")
PANE_CODE = "\n".join(line for line in PANE_TEXT.splitlines() if not re.match(r"\s*--", line))
SLOT = slot()
INSTALLER = REPO / "scripts" / "lib" / "install_extension.py"
ONBOARDING = REPO / ".agents" / "skills" / "fleet-onboarding" / "SKILL.md"
PANE_SKILL = REPO / ".agents" / "skills" / "fleet-pane" / "SKILL.md"
STATUS = REPO / "scripts" / "lib" / "fleet_status.py"
GLYPHS = REPO / "orchestration" / "session-glyphs.example.conf"

# Chords the KERNEL owns (help, theme, settings, reload, perf). A plugin-scoped
# binding does not outrank one: F6 once shipped reading "F6 hides" in the
# pane's title while F6 opened Settings.
KERNEL_CHORDS = {"f1", "f4", "f6", "f10", "f12"}

# A variation selector asks for a presentation the terminal may not have and
# a zero-width joiner builds a glyph whose width nothing agrees on; every row is
# budgeted in cells.
WIDTH_TRAPS = ("️", "︎", "‍")


def read(path) -> str:
    return path.read_text(encoding="utf-8")


def one(pattern: str, text: str) -> str:
    found = re.search(pattern, text, re.M)
    assert found, f"no match for {pattern!r}"
    return found.group(1)


@pytest.mark.parametrize("doc", [ONBOARDING, PANE_SKILL], ids=["fleet-onboarding", "fleet-pane"])
def test_each_skill_documents_the_panes_slot_with_its_guard(doc):
    """Without `panels.shown` the column is carved every frame, so the F-key
    flips a state nothing reads — which `plugin check` cannot catch."""
    expect(read(doc), f'slot = "{SLOT}"', f'panels.shown("{SLOT}")')


def test_the_installer_prints_a_block_naming_the_panes_slot_with_its_guard(stubs, tmp_path):
    thurbox(stubs)
    fleet = clone(tmp_path)
    done = run([*PYTHON, str(fleet / "scripts" / "lib" / "install_extension.py")], cwd=fleet, FLEET_VOICE_CONF=None)
    assert done.code == 0, done.out
    expect(done.out, f'slot = "{SLOT}"', f'panels.shown("{SLOT}")')


def test_plugin_remove_is_documented_with_the_destination_path():
    """`plugin remove 91_fleet_queue.lua` answers "not listed" and removes nothing."""
    dest = lib("install_extension.py").PANE_DEST
    assert dest.startswith("plugins/")
    expect(read(INSTALLER), f"plugin remove {dest}")


def test_the_chord_is_not_a_kernel_chord_and_every_doc_names_it():
    chord = one(r'^      key = "(f[0-9]*)",$', PANE_TEXT)
    assert chord not in KERNEL_CHORDS, f"{chord} is a kernel chord; the key would never reach the pane"
    for doc in (INSTALLER, REPO / "README.md", PANE_SKILL):
        expect(read(doc), chord.upper())


def test_fuel_is_read_through_fleet_status_and_never_parsed_twice():
    expect(PANE_TEXT, "fleet status --fuel")
    expect(read(STATUS), '"--fuel"')
    assert "quota-axi" not in PANE_CODE, "the pane reads quota-axi itself"


def test_every_field_the_pane_reads_is_one_the_record_emits():
    found = re.search(r"^RECORD_FIELDS = \((.*?)^\)", read(STATUS), re.M | re.S)
    assert found, "no RECORD_FIELDS in fleet_status.py"
    block = found.group(1)
    wire = set(re.findall(r'"([a-z_]+)"', block))
    read_fields = set(re.findall(r"fields\.([a-z_]+)", PANE_TEXT))
    assert read_fields <= wire, f"the pane reads fields the record never emits: {read_fields - wire}"


def test_the_pane_never_spells_the_reserve_threshold():
    reserve = one(r"^FUEL_RESERVE = ([0-9]+)$", read(STATUS))
    assert not re.search(rf"(^|[^0-9]){reserve}([^0-9]|$)", PANE_CODE, re.M)


def test_the_pane_matches_the_lead_the_manifest_spawns():
    lead = one(r'^local CONTROL_PLANE = "(.*)"$', PANE_TEXT)
    sessions = read(REPO / "extension.toml.in").split("\n[[sessions]]\n", 1)[1]
    assert one(r'^name *= *"(.*)"', sessions) == f"__LEAD_GLYPH__ {lead}"


def test_no_glyph_setting_is_spelled_in_the_panes_code():
    for key in ("LEAD_GLYPH_ON", "LEAD_GLYPH_OFF", "WORKER_GLYPH_ON"):
        glyph = one(rf"^{key}=(.*)$", read(GLYPHS))
        assert glyph not in PANE_CODE, f"the pane spells the {key} glyph in code"


@pytest.mark.parametrize("path", [GLYPHS, PANE], ids=["session-glyphs", "pane"])
def test_no_variation_selector_or_joiner(path):
    text = read(path)
    assert not any(trap in text for trap in WIDTH_TRAPS)


def test_the_fuel_probe_runs_less_often_than_the_queue_probe():
    """The fuel probe hits the network; refetching it at the queue's cadence
    burns the fuel it reports."""
    assert int(one(r"^local FUEL_TTL = ([0-9]+)$", PANE_TEXT)) > int(one(r"^local TTL = ([0-9]+)$", PANE_TEXT))


def test_every_probe_is_one_command_line_both_platform_shells_run():
    """thurbox runs a probe through `sh -c` on POSIX and `cmd /C` on Windows,
    so a probe is one plain command and never a script."""
    probes = re.findall(r'^local (?:FUEL_)?PROBE = "(.*)"$', PANE_TEXT, re.M)
    assert len(probes) == 2, "PROBE and FUEL_PROBE are each one quoted command line"
    for probe in probes:
        assert probe.startswith("uv run ")
        assert not re.search(r"[|&;<>$`'\\]", probe), probe
