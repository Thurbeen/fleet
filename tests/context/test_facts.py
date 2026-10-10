"""A fact is one file, created once and never edited — and that is what makes many writers safe.

What the design promises, each driven through fleet's own entry points:
`fleet context learn` from fifty processes at once, two `collect` passes over
one result, a `replaces` chain, two facts contesting one id, a malformed
`learned:` entry that must not hold its task open, and repository names that
must neither collide across forges nor climb out of `registry/facts/`.
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml
from contextkit import BRIEF, checkout, context, fact_files, facts_root, parse_toon, plant, read_fact

from harness import lib, run_queue, run_queue_batch, write

APP = "github.com/acme/app"


def learn(repo: str, text: str, *more: str):
    return context("learn", repo, text, *more)


# --- the write ---------------------------------------------------------------


def test_learn_records_one_fact_with_provenance_and_answers_a_repeat_as_already_recorded():
    first = learn(APP, "Release builds need the `vendored` feature on Windows.")
    assert first.code == 0, first.out
    doc = parse_toon(first.stdout)
    [path] = fact_files(APP)
    front, body = read_fact(path)
    assert body == "Release builds need the `vendored` feature on Windows."
    assert front["id"] == doc["recorded"] == path.stem
    assert re.fullmatch(r"\d{8}-[0-9a-f]{8}", front["id"]), front
    assert front["repo"] == APP and front["source"].startswith("agent:") and front["at"].endswith("Z")

    again = learn(APP, "Release builds need the `vendored` feature on Windows.")
    assert again.code == 0, again.out
    assert parse_toon(again.stdout)["already"] == front["id"], again.stdout
    assert len(fact_files(APP)) == 1


def test_fifty_concurrent_learns_leave_exactly_one_file_per_id_and_every_file_parses():
    texts = [f"Fact number {n % 10} is stated by several writers at once." for n in range(50)]
    with ThreadPoolExecutor(max_workers=50) as pool:
        runs = list(pool.map(lambda t: learn(APP, t), texts))
    assert all(r.code == 0 for r in runs), [r.out for r in runs if r.code]
    files = fact_files(APP)
    assert len(files) == 10, [f.name for f in files]
    for path in files:
        front, body = read_fact(path)
        assert front["id"] == path.stem and body.startswith("Fact number")
    # No writer's temp file is left behind for a reader to trip over.
    assert sorted(p.name for p in (facts_root() / APP).iterdir()) == sorted(p.name for p in files)


def test_create_once_never_overwrites_and_leaves_no_temp_file(tmp_path):
    platform = lib("fleet_platform.py")
    target = tmp_path / "store" / "fact.md"
    assert platform.create_once(str(target), "first\n") is True
    assert platform.create_once(str(target), "second\n") is False
    assert target.read_text(encoding="utf-8") == "first\n"
    assert sorted(p.name for p in target.parent.iterdir()) == ["fact.md"]


def test_create_once_from_many_threads_has_exactly_one_winner(tmp_path):
    platform = lib("fleet_platform.py")
    target = tmp_path / "store" / "fact.md"
    with ThreadPoolExecutor(max_workers=32) as pool:
        won = list(pool.map(lambda n: platform.create_once(str(target), f"writer {n}\n"), range(64)))
    assert won.count(True) == 1
    assert re.fullmatch(r"writer \d+\n", target.read_text(encoding="utf-8"))
    assert sorted(p.name for p in target.parent.iterdir()) == ["fact.md"]


# --- replaces ----------------------------------------------------------------


def test_a_replaced_fact_leaves_the_read_but_its_file_stays_as_history():
    plant(APP, "20260912-00000001", "Builds use the old toolchain.")
    done = learn(APP, "Builds use the new toolchain.", "--replaces", "20260912-00000001")
    assert done.code == 0, done.out
    doc = parse_toon(context("repo", APP).stdout)
    assert doc["facts"].startswith("1 live · 1 replaced"), doc
    assert [r["text"] for r in doc["live"]] == ["Builds use the new toolchain."]
    assert (facts_root() / APP / "20260912-00000001.md").read_text(encoding="utf-8").count("old toolchain") == 1


def test_two_facts_replacing_one_id_are_both_shown_with_their_dates():
    plant(APP, "20260912-00000001", "The cache lives in /var.")
    plant(APP, "20261001-00000002", "The cache lives in ~/.cache.", at="2026-10-01T00:00:00Z",
          replaces="20260912-00000001")
    plant(APP, "20261002-00000003", "The cache lives in the worktree.", at="2026-10-02T00:00:00Z",
          replaces="20260912-00000001")
    doc = parse_toon(context("repo", APP).stdout)
    assert {(r["date"], r["text"]) for r in doc["live"]} == {
        ("2026-10-01", "The cache lives in ~/.cache."), ("2026-10-02", "The cache lives in the worktree.")}
    assert "20260912-00000001" in doc["contested"], doc


def test_replacing_a_fact_that_does_not_exist_is_refused():
    done = learn(APP, "Something new.", "--replaces", "20990101-deadbeef")
    assert done.code == 1, done.out
    assert parse_toon(done.stdout)["code"] == "unknown_fact"
    assert fact_files(APP) == []


def test_a_malformed_fact_file_is_reported_in_one_line_and_never_blocks_the_read():
    plant(APP, "20261001-00000001", "A good fact.")
    write(facts_root() / APP / "20261002-00000002.md", "no front matter at all\n")
    done = context("repo", APP)
    assert done.code == 0, done.out
    doc = parse_toon(done.stdout)
    assert [r["text"] for r in doc["live"]] == ["A good fact."]
    assert "20261002-00000002.md" in doc["skipped"], doc


# --- identity ----------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../escape", "github.com/../../etc", "/etc/passwd", "github.com/acme/..",
                                 "github.com/acme/app/../../x", "C:\\Windows", "github.com/acme/con",
                                 "github.com//app", "local/"])
def test_a_repo_name_cannot_climb_out_of_the_facts_directory(bad, tmp_path):
    done = learn(bad, "An attempt to write somewhere else.")
    assert done.code == 1, done.out
    assert parse_toon(done.stdout)["code"] in ("invalid_repo", "unknown_repo"), done.stdout
    outside = [p for p in tmp_path.parent.rglob("*.md") if facts_root() not in p.parents]
    assert not [p for p in outside if "somewhere else" in p.read_text(encoding="utf-8", errors="replace")]


def test_the_same_path_on_two_forges_is_two_repositories():
    assert learn("github.com/acme/app", "GitHub's copy.").code == 0
    assert learn("gitlab.example.com/acme/app", "GitLab's copy.").code == 0
    assert [read_fact(p)[1] for p in fact_files("github.com/acme/app")] == ["GitHub's copy."]
    assert [read_fact(p)[1] for p in fact_files("gitlab.example.com/acme/app")] == ["GitLab's copy."]
    # And a bare name both could mean is ambiguous rather than a guess.
    done = context("repo", "app")
    assert done.code == 1 and parse_toon(done.stdout)["code"] == "ambiguous_repo", done.out


def test_a_checkout_names_its_repository_by_origin_and_a_local_one_by_its_own_path(tmp_path):
    forge = checkout(tmp_path, "app", "git@github.com:Acme/App.git")
    https = checkout(tmp_path, "app-https", "https://github.com/acme/app")
    lonely = checkout(tmp_path / "one", "tool", None)
    other = checkout(tmp_path / "two", "tool", None)
    ids = {}
    for path in (forge, https, lonely, other):
        done = context("repo", str(path))
        assert done.code == 0, done.out
        ids[path] = parse_toon(done.stdout)["repo"]
    assert ids[forge] == ids[https] == "github.com/acme/app"
    assert ids[lonely].startswith("local/tool-") and ids[other].startswith("local/tool-")
    assert ids[lonely] != ids[other], "two local repositories with one name share a fact store"


# --- collect -----------------------------------------------------------------


@pytest.fixture
def worked(tmp_path) -> Path:
    """A dispatched task on a GitHub checkout whose worker wrote a result with `learned:`."""
    repo = checkout(tmp_path, "app", "https://github.com/acme/app.git")
    brief = tmp_path / "brief.md"
    write(brief, BRIEF)
    assert run_queue_batch([
        ["topic", "add", "app-ci", "--title", "App CI", "--prompt", "fix the build"],
        ["add", "app-ci", "fix-windows", "--title", "Fix Windows", "--repo", str(repo), "--branch",
         "fix/windows", "--number", "02", "--publish", "none", "--brief-file", str(brief)],
    ]).code == 0
    task = Path(os.environ["FLEET_QUEUE_DIR"]) / "app-ci" / "02-fix-windows"
    record = task / "task.yaml"
    write(record, record.read_text(encoding="utf-8").replace("state: queued", "state: dispatched"))
    return task


def learned_result(task: Path, entries: list) -> None:
    front = yaml.safe_dump({"outcome": "shipped", "learned": entries}, sort_keys=False)
    write(task / "result.md", f"---\n{front}---\nDid it.\n")


def test_collect_records_learned_facts_with_the_task_as_source(worked):
    learned_result(worked, [
        {"repo": APP, "fact": "Release builds need the `vendored` feature on Windows."},
        {"repo": "app", "fact": "CI caches nothing between jobs."},
    ])
    done = run_queue("collect")
    assert done.code == 0, done.out
    assert "2 facts recorded" in done.out, done.out
    files = fact_files(APP)
    assert [p.stem.rsplit("-", 1)[-1] for p in files] == ["1", "2"]
    for path in files:
        front, _ = read_fact(path)
        assert front["source"] == "app-ci/02-fix-windows"
        assert re.fullmatch(r"\d{8}-app-ci-02-fix-windows-[12]", front["id"]), front


def test_a_malformed_learned_entry_is_reported_and_the_task_still_closes(worked):
    learned_result(worked, [{"fact": "No repo named."}, {"repo": APP, "fact": "Kept."},
                            "not a mapping", {"repo": APP, "fact": "x" * 2000}])
    done = run_queue("collect")
    assert done.code == 0, done.out
    assert "learned[1]: no repo — skipped" in done.out, done.out
    assert "1 fact recorded" in done.out, done.out
    assert "state: done" in (worked / "task.yaml").read_text(encoding="utf-8") or \
        "state: landed" in (worked / "task.yaml").read_text(encoding="utf-8")
    assert [read_fact(p)[1] for p in fact_files(APP)] == ["Kept."]


def test_more_than_five_learned_entries_records_the_first_five(worked):
    learned_result(worked, [{"repo": APP, "fact": f"Fact {n}."} for n in range(7)])
    done = run_queue("collect")
    assert done.code == 0, done.out
    assert len(fact_files(APP)) == 5
    assert "5 facts recorded" in done.out and "2 over the limit of 5" in done.out, done.out


def test_two_overlapping_collects_produce_each_fact_once(worked):
    learned_result(worked, [{"repo": APP, "fact": f"Fact {n}."} for n in range(5)])
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(lambda _: run_queue("collect"), range(2)))
    # Two collects saving one task.yaml or topic.yaml can still collide on
    # write_record's shared `<path>.tmp` (the design's D3, fixed by its own
    # step). That race is the only failure tolerated here; facts never go
    # through it.
    for r in runs:
        assert r.code == 0 or re.search(r"FileNotFoundError: .*\.yaml\.tmp' -> ", r.stderr), r.out
    files = fact_files(APP)
    assert len(files) == 5, [f.name for f in files]
    assert all(read_fact(p)[1].startswith("Fact") for p in files)


def test_a_collected_result_shows_in_the_repos_context(worked):
    learned_result(worked, [{"repo": APP, "fact": "Kept."}])
    assert run_queue("collect").code == 0
    doc = parse_toon(context("repo", APP).stdout)
    assert [r["task"] for r in doc["recent"]] == ["app-ci/02-fix-windows"], doc
    assert doc["recent"][0]["path"].endswith("result.md")


# --- the brief ---------------------------------------------------------------


def test_the_brief_points_at_project_memory_and_the_results_it_waits_on(worked, tmp_path):
    assert run_queue_batch([
        ["add", "app-ci", "follow-up", "--title", "Follow up", "--repo", str(tmp_path / "app"),
         "--branch", "fix/follow", "--number", "03", "--publish", "none", "--brief-file", str(tmp_path / "brief.md")],
    ]).code == 0
    assert run_queue("block", "app-ci/03-follow-up", "--on", "app-ci/02-fix-windows", "--kind",
                     "semantic-dependency", "--why", "builds on it").code == 0
    text = (worked.parent / "03-follow-up" / "BRIEF.md").read_text(encoding="utf-8")
    assert "fleet context repo github.com/acme/app --task app-ci/03-follow-up" in text, text

    doc = parse_toon(context("repo", APP, "--task", "app-ci/03-follow-up").stdout)
    assert [r["task"] for r in doc["prior"]] == ["app-ci/02-fix-windows"], doc
    assert doc["prior"][0]["path"] == str(worked / "result.md")


def test_a_fact_that_starts_with_a_dash_is_recorded_after_a_double_dash():
    done = learn(APP, "--", "-O2 breaks the release build.")
    assert done.code == 0, done.out
    assert [read_fact(p)[1] for p in fact_files(APP)] == ["-O2 breaks the release build."]


def test_the_briefs_placeholder_copied_verbatim_is_not_a_fact(worked):
    """The brief shows `fact: "<one or two sentences>"`; a worker who copies it learned nothing."""
    learned_result(worked, [{"repo": APP, "fact": "<one or two sentences>"}])
    done = run_queue("collect")
    assert done.code == 0, done.out
    assert "learned[1]:" in done.out and fact_files(APP) == [], done.out


def test_restating_an_older_fact_with_replaces_supersedes_the_newer_one():
    first = parse_toon(learn(APP, "Builds use toolchain A.").stdout)["recorded"]
    second = parse_toon(learn(APP, "Builds use toolchain B.", "--replaces", first).stdout)["recorded"]
    back = learn(APP, "Builds use toolchain A.", "--replaces", second)
    assert back.code == 0 and "recorded" in parse_toon(back.stdout), back.stdout
    doc = parse_toon(context("repo", APP).stdout)
    assert [r["text"] for r in doc["live"]] == ["Builds use toolchain A."], doc
    # The same statement with the same replaces is still a repeat.
    assert "already" in parse_toon(learn(APP, "Builds use toolchain A.", "--replaces", second).stdout)
