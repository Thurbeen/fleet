"""The topic most queue tests share, and the steps that take it through the loop.

One prompt, two repos, four units of work: the shape the queue exists for.
`01` and `02` are independent. `03` genuinely depends on `01`. `04` edits the
same file as `01` and depends on nothing.
"""

from pathlib import Path

from harness import Run, write

S1 = "11111111-1111-1111-1111-111111111111"
S2 = "22222222-2222-2222-2222-222222222222"

TOPIC = "report-status-honestly"
TOPIC_TITLE = "Make thurbox report agent status honestly"
TOPIC_PROMPT = "idle should mean the agent said it is at rest, nothing else"

TASKS = (
    ("01", "drop-idle-default", "Stop defaulting an unreported session to idle", "/tmp/repo-a", "src/state.rs"),
    ("02", "document-the-states", "Document the state vocabulary", "/tmp/repo-b", "docs/states.md"),
    ("03", "render-detected-agent", "Render detected_agent in the session list", "/tmp/repo-a", "src/list.rs"),
    ("04", "log-state-changes", "Log every state change", "/tmp/repo-a", "src/state.rs"),
)

PLACEHOLDER = "<!-- WRITE THE INSTRUCTIONS HERE -->"

# The operator's settings for every queue test: exactly the shipped defaults
# plus what the fixtures need — `attested` with a `how` naming a tool that does
# not exist, which is the point: fleet renders it and never parses it — and an
# allowlist naming the repositories the fixtures open pull requests in.
PUBLISH_CONF = (
    "METHOD=attested\nHOW=run `/publish --yes`\n"
    "ATTESTATION_MARKER=fleet-attestation\nPIPELINE_COMMIT_PREFIX=publish\n"
)
AGENT_CONF = "AGENT=claude\nFUEL_PROVIDER=claude\n"
AUTO_MERGE_CONF = (
    "# The repositories this suite's fixtures open pull requests in.\n"
    "github.com/Thurbeen/fleet\ngithub.com/Thurbeen/thurbox\n"
)
# The one host a queue test names: an ordinary POSIX host, which the ssh stub answers.
HOSTS_TOML = '[[hosts]]\nname = "devbox"\ndestination = "me@devbox"\n'


def ok(run: Run) -> Run:
    assert run.code == 0, run.out
    return run


def result(task_dir: Path, outcome: str, note: str, artifact: str | None = None) -> None:
    """The result.md a worker writes."""
    front = f"outcome: {outcome}\n" + (f"artifact: {artifact}\n" if artifact else "")
    write(task_dir / "result.md", f"---\n{front}---\n{note}\n")


def fill_brief(path: Path) -> None:
    body = path.read_text(encoding="utf-8").replace(
        PLACEHOLDER, "Make the change, open a pull request, and write the result file below."
    )
    write(path, body)
