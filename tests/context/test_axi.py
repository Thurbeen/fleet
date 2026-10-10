"""Every `fleet context` surface holds the AXI contract, and a new one fails here until it does.

The contract (axi.md, `axi/1.0-2026-07`) as the design states it: TOON on
stdout, totals before any list, an explicit empty state and never a blank, a
structured `error:` block on stdout with exit 1 for an error and 2 for a usage
mistake, `help[]` as the last line of everything, `--help` on every
subcommand, nothing ever read from stdin, and a byte cap on every default
output. A small model reads this as the lead, so the shape is the interface.
"""

from __future__ import annotations

import pytest
from contextkit import BRIEF, context, parse_toon, plant

from harness import run_queue_batch, write

REPO_ID = "github.com/acme/app"

# Each subcommand, the argv that should succeed, the argv that is a bad
# argument, and the byte cap its default output stays under.
SURFACES = {
    "summary": ([], ["nonsense-subcommand-argument", "x"], 1536),
    "lead": (["lead"], ["lead", "extra"], 1536),
    "repo": (["repo", REPO_ID], ["repo", "../../etc"], 4096),
    "pending": (["pending"], ["pending", "extra"], 4096),
    "learn": (["learn", REPO_ID, "Release builds need the vendored feature."], ["learn", REPO_ID, "  "], 1024),
}

# What says "this is the total" first, per surface: the line a reader can stop at.
TOTALS = {"summary": "topics", "lead": "topics", "repo": "facts", "pending": "pending", "learn": "facts"}
# The list each capped surface cuts, under a key of its own: TOON keys are unique.
TABLES = {"summary": "open", "lead": "open", "repo": "live"}


def axi(run, cap: int) -> dict:
    """The output is TOON, ends with help[], read nothing, and fits its cap."""
    assert "stdin was read" not in run.stderr, run.stderr
    assert "Traceback" not in run.out, run.out
    encoded = len(run.stdout.encode("utf-8"))
    assert encoded <= cap, f"{encoded} bytes, over the {cap}-byte cap:\n{run.stdout}"
    lines = run.stdout.splitlines()
    assert lines, "blank output: an empty state must say so"
    assert lines[-1].startswith("help["), f"the last line is not help[]:\n{run.stdout}"
    doc = parse_toon(run.stdout)
    assert doc["help"], "help[] names no next command"
    return doc


@pytest.fixture
def many(tmp_path) -> None:
    """More of everything than any default output may show."""
    for n in range(60):
        plant(REPO_ID, f"202609{n % 28 + 1:02d}-{n:08x}", f"Fact {n}: " + "a long sentence, " * 20,
              at=f"2026-09-{n % 28 + 1:02d}T00:00:00Z")
    commands = []
    for n in range(40):
        slug = f"topic-{n:02d}"
        commands.append(["topic", "add", slug, "--title", f"Topic {n}", "--prompt", "many topics"])
        brief = tmp_path / f"brief-{n}.md"
        write(brief, BRIEF)
        commands.append(["add", slug, "work", "--title", f"Work {n}", "--repo", f"/nowhere/{n}",
                         "--branch", f"fix/{n}", "--publish", "none", "--brief-file", str(brief)])
    assert run_queue_batch(commands).code == 0


@pytest.mark.parametrize("surface", SURFACES)
def test_empty_state_is_explicit_with_totals_first(surface):
    good, _bad, cap = SURFACES[surface]
    done = context(*good)
    assert done.code == 0, done.out
    doc = axi(done, cap)
    first = done.stdout.splitlines()[0]
    assert first.startswith(TOTALS[surface] + ":"), f"totals are not first:\n{done.stdout}"
    assert doc[TOTALS[surface]].split()[0].isdigit() or surface == "learn", doc


@pytest.mark.parametrize("surface", SURFACES)
def test_many_records_stay_under_the_cap_and_say_what_was_left_out(surface, many):
    good, _bad, cap = SURFACES[surface]
    done = context(*good)
    assert done.code == 0, done.out
    doc = axi(done, cap)
    if surface in ("summary", "lead", "repo"):
        assert "more" in doc and "--all" in doc["more"], f"no disclosure of what was cut:\n{done.stdout}"


@pytest.mark.parametrize("surface", ["summary", "lead", "repo"])
def test_all_and_full_disclose_what_the_default_cut(surface, many):
    good, *_ = SURFACES[surface]
    capped = parse_toon(context(*good).stdout)
    done = context(*good, "--all", "--full")
    assert done.code == 0, done.out
    doc = axi(done, 1 << 20)
    assert "more" not in doc, done.stdout
    table = TABLES[surface]
    assert len(doc[table]) > len(capped[table]), f"--all listed no more than the default:\n{done.stdout}"
    if surface == "repo":
        assert "truncated" not in done.stdout, "--full still truncates a fact"
        assert "truncated" in context(*good).stdout, "a 300-character fact was not truncated by default"


@pytest.mark.parametrize("surface", SURFACES)
def test_an_unknown_flag_is_a_usage_error_on_stdout_with_exit_2(surface):
    good, _bad, _cap = SURFACES[surface]
    done = context(*good, "--no-such-flag")
    assert done.code == 2, done.out
    doc = axi(done, 4096)
    assert doc["code"] == "usage" and "--no-such-flag" in doc["error"], done.stdout


@pytest.mark.parametrize("surface", SURFACES)
def test_a_bad_argument_is_an_error_on_stdout_with_exit_1(surface):
    _good, bad, _cap = SURFACES[surface]
    done = context(*bad)
    assert done.code in (1, 2), done.out
    doc = axi(done, 4096)
    assert doc["error"] and doc["code"], done.stdout
    if surface != "summary":
        assert done.code == 1, done.out


@pytest.mark.parametrize("surface", SURFACES)
def test_every_subcommand_has_help(surface):
    good, _bad, _cap = SURFACES[surface]
    done = context(*(good[:1] if good else []), "--help")
    assert done.code == 0, done.out
    doc = axi(done, 4096)
    assert "usage" in doc, done.stdout


def test_a_bare_context_is_the_lead_summary_not_help():
    """Content first: no arguments means live data."""
    assert context().stdout == context("lead").stdout


def test_an_unknown_subcommand_is_a_usage_error():
    done = context("frobnicate")
    assert done.code == 2, done.out
    assert parse_toon(done.stdout)["code"] == "usage"
