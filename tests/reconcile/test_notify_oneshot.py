"""`CONDUCTOR=on` runs the conductor once per notice, headless, and types nothing.

The claims, each one a way the one-shot could be worse than the session it
replaces:

  * a notice is a RUN, never a line typed into a session, the lead's included;
  * the run is handed the notice, the fleet's state read then, and its
    standing instructions read from the checkout THEN — not at some launch;
  * it runs as thurbox would launch the agent, with the headless arguments of
    the agent fleet watched — `LIKE` included — and none of the interactive
    session's own;
  * a run that went is the notice told, once; one that failed hands the same
    line to the lead, so a broken conductor is never silence;
  * every run is journalled, and the next run is shown the journal;
  * `CONDUCTOR=session` and unset are untouched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from harness import PYTHON, REPO, expect, refute, run, write
from reconcilekit import LEAD

NOTIFY = REPO / "scripts" / "lib" / "notify_conductor.py"

# A `claude -p` stand-in: keeps its prompt, answers `-p --output-format json`.
CLAUDE = """\
import json, os, sys
from pathlib import Path
root = Path(os.environ["FLEET_STUB_ROOT"])
with open(root / "prompts.log", "a", encoding="utf-8") as fh:
    fh.write(sys.stdin.read() + "\\n<<<END>>>\\n")
with open(root / "cwd.log", "a", encoding="utf-8") as fh:
    fh.write(os.getcwd() + "\\n")
answer = root / "answer.json"
if answer.is_file():
    doc = json.loads(answer.read_text(encoding="utf-8"))
    print(json.dumps(doc))
    raise SystemExit(doc.pop("_exit", 0))
print(json.dumps({"result": "dispatched 1: alpha/01-first", "is_error": False,
                  "permission_denials": []}))
"""


def settings(name: str) -> Path:
    return Path(os.environ["FLEET_NAME_ROOT"]) / "orchestration" / name


def oneshot(stubs, mode: str = "on") -> None:
    write(settings("fleet.conf"), f"CONDUCTOR={mode}\n")
    Path(os.environ["FLEET_CONDUCTOR_DIR"]).mkdir(parents=True, exist_ok=True)
    stubs.tool("claude", CLAUDE)


def lead(stubs) -> None:
    write(stubs.root / "sessions" / "lead-1.json",
          json.dumps({"id": "lead-1", "name": LEAD, "state": "idle", "agent_session_id": "c-lead"}) + "\n")
    stubs.composer("lead-1", "")


def notify(state_dir, ready=(), stalled=(), **env):
    plan = json.dumps({"ready": list(ready), "stalled": list(stalled)})
    env = {"FLEET_LEAD_SESSION": LEAD, **env}
    return run([*PYTHON, str(NOTIFY), "--state-dir", str(state_dir)], stdin=plan, **env)


def prompts(stubs) -> list[str]:
    try:
        text = (stubs.root / "prompts.log").read_text(encoding="utf-8")
    except OSError:
        return []
    return [p for p in text.split("\n<<<END>>>\n") if p.strip()]


def journal(state_dir) -> list[dict]:
    path = Path(state_dir) / "conductor.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_notice_is_a_run_and_nobody_is_typed_into(stubs, tmp_path):
    oneshot(stubs)
    lead(stubs)
    done = notify(tmp_path, ready=["alpha/01-first"])
    assert done.code == 0, done.out
    assert stubs.calls("thurbox-cli", "session send") == [], "a one-shot conductor typed into a session"
    assert stubs.calls("thurbox-cli", "message send") == []
    assert len(prompts(stubs)) == 1
    expect(done.out, "notice ready → conductor (one-shot) ran", "alpha/01-first")
    assert Path((stubs.root / "cwd.log").read_text(encoding="utf-8").strip()) == Path(os.environ["FLEET_CONDUCTOR_DIR"])


def test_it_runs_as_thurbox_launches_the_agent_headless(stubs, tmp_path):
    oneshot(stubs)
    notify(tmp_path, ready=["alpha/01-first"])
    expect(" ".join(stubs.calls("thurbox-cli", "agent launch-args")), "agent launch-args claude")
    (argv,) = stubs.calls("claude")
    expect(argv, "-p", "--output-format json", "--no-session-persistence", "--permission-mode auto",
           "Bash(uv run fleet:*)", "--disallowedTools Edit,Write,NotebookEdit")
    # The interactive session's own args are its status hooks, and a run is no session.
    refute(argv, "--interactive-only")


def test_the_prompt_carries_the_notice_the_state_and_the_instructions_read_now(stubs, tmp_path):
    oneshot(stubs)
    notify(tmp_path, ready=["alpha/01-first"])
    (prompt,) = prompts(stubs)
    expect(prompt, "fleet reconciler: 1 task(s) ready", "alpha/01-first",
           "### uv run fleet queue plan", "### uv run fleet queue list --live", "### uv run fleet queue prs",
           "### uv run fleet status",
           "# CONDUCTOR.md — standing context", "## fleet-conductor")
    # Rendered with the voice names, as install renders the session's copy.
    refute(prompt, "@OPERATOR_NAME@", "@ASSISTANT_NAME@")


def test_a_set_already_run_on_is_not_run_on_again(stubs, tmp_path):
    oneshot(stubs)
    notify(tmp_path, ready=["alpha/01-first"])
    notify(tmp_path, ready=["alpha/01-first"])
    assert len(prompts(stubs)) == 1, "the same ready set ran the conductor twice"
    notify(tmp_path, ready=["alpha/01-first", "beta/02-second"])
    assert len(prompts(stubs)) == 2
    expect(prompts(stubs)[-1], "beta/02-second")


def test_a_failed_run_is_told_to_the_lead_instead_and_once(stubs, tmp_path):
    oneshot(stubs)
    lead(stubs)
    write(stubs.root / "answer.json", json.dumps({"result": "", "is_error": True, "subtype": "error_during_execution",
                                                  "_exit": 1}))
    done = notify(tmp_path, ready=["alpha/01-first"])
    expect(done.out, "notice ready → conductor (one-shot) failed", "the lead is told instead")
    (sent,) = stubs.calls("thurbox-cli", "session send")
    expect(sent, "session send lead-1", "alpha/01-first")

    # Told is told: the next pass runs nothing and types nothing.
    notify(tmp_path, ready=["alpha/01-first"])
    assert len(prompts(stubs)) == 1
    assert len(stubs.calls("thurbox-cli", "session send")) == 1


def test_an_agent_with_no_headless_form_is_the_leads_notice(stubs, tmp_path):
    oneshot(stubs)
    lead(stubs)
    write(settings("agent.conf"), "CONDUCTOR_AGENT=muse\n")
    done = notify(tmp_path, ready=["alpha/01-first"])
    expect(done.out, "failed", "muse", "muse.HEADLESS")
    assert prompts(stubs) == []
    expect(stubs.calls("thurbox-cli", "session send")[0], "session send lead-1")


def test_a_second_account_runs_the_watched_agents_row_under_its_own_command(stubs, tmp_path):
    oneshot(stubs)
    write(settings("agent.conf"), "CONDUCTOR_AGENT=claude-spare\nclaude-spare.LIKE=claude\n")
    write(stubs.root / "agents" / "claude-spare.json",
          json.dumps({"command": "claude", "args": [], "env": {"SPARE_ACCOUNT": "1"}}) + "\n")
    notify(tmp_path, ready=["alpha/01-first"])
    expect(" ".join(stubs.calls("thurbox-cli", "agent launch-args")), "launch-args claude-spare")
    expect(stubs.calls("claude")[0], "-p", "--permission-mode auto")


def test_codex_runs_exec_under_its_own_auto_review(stubs, tmp_path):
    oneshot(stubs)
    write(settings("agent.conf"), "CONDUCTOR_AGENT=codex\n")
    stubs.tool("codex", "import sys\nsys.stdin.read()\n"
                        "out = sys.argv[sys.argv.index('-o') + 1]\n"
                        "open(out, 'w', encoding='utf-8').write('held 1: fuel codex 3%, reserve 20%')\n")
    done = notify(tmp_path, ready=["alpha/01-first"])
    assert done.code == 0, done.out
    (argv,) = stubs.calls("codex")
    expect(argv, "codex exec --approve-for-me", "sandbox_workspace_write.network_access=true", "--ephemeral")
    refute(argv, "dangerously", "bypass")
    assert journal(tmp_path)[-1]["said"] == "held 1: fuel codex 3%, reserve 20%"


def test_headless_in_agent_conf_overrides_the_built_in_row(stubs, tmp_path):
    oneshot(stubs)
    write(settings("agent.conf"), "claude.HEADLESS=-p --output-format json --model a-cheaper-one\n")
    notify(tmp_path, ready=["alpha/01-first"])
    (argv,) = stubs.calls("claude")
    expect(argv, "--model a-cheaper-one")
    refute(argv, "--permission-mode")


def test_every_run_is_journalled_and_the_next_one_is_shown_it(stubs, tmp_path):
    oneshot(stubs)
    write(stubs.root / "answer.json", json.dumps({
        "result": "dispatched 1: alpha/01-first", "is_error": False,
        "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "rm -rf somewhere"}}]}))
    done = notify(tmp_path, ready=["alpha/01-first"])
    expect(done.out, "ran", "refused 1: rm -rf somewhere")
    (entry,) = journal(tmp_path)
    assert entry["ok"] is True and entry["agent"] == "claude"
    assert entry["denied"] == ["rm -rf somewhere"]
    expect(entry["notice"], "alpha/01-first")

    notify(tmp_path, ready=["alpha/01-first"], stalled=["gamma/03-third"])
    expect(prompts(stubs)[-1], "# Your last runs", "you said: dispatched 1: alpha/01-first",
           "refused: rm -rf somewhere", "gamma/03-third")


def test_a_sync_notice_is_a_run_too(stubs, tmp_path):
    oneshot(stubs)
    lead(stubs)
    report = json.dumps({"before": "a" * 40, "after": "b" * 40, "actions": {"restart-lead": "FLEET.md"}})
    done = run([*PYTHON, str(NOTIFY), "--state-dir", str(tmp_path), "--stale"], stdin=report,
               FLEET_LEAD_SESSION=LEAD)
    expect(done.out, "notice sync → conductor (one-shot) ran")
    expect(prompts(stubs)[0], "fast-forwarded this checkout", "/update-fleet aaaaaaaa")
    assert stubs.calls("thurbox-cli", "session send") == []
    assert not (tmp_path / "stale.json").exists(), "a delivered sync notice was kept"


def test_session_and_unset_never_run_anything(stubs, tmp_path):
    for mode in ("session", ""):
        oneshot(stubs, mode)
        lead(stubs)
        notify(tmp_path / (mode or "unset"), ready=["alpha/01-first"])
    assert prompts(stubs) == []
    assert len(stubs.calls("thurbox-cli", "session send")) == 2
