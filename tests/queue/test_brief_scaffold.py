"""The brief scaffold (claim 6's other half): it points at documents instead of
restating them.

871 lines of brief across five tasks once carried ~30 hand-copied lines of the
same policy, and one of the five had drifted. One tracked POLICY.md, referenced
by absolute path, is what a scaffolded brief hands a worker — and, when the
operator has written one, their own OPERATOR.md beside it.
"""

from queuekit import ok

from harness import REPO, expect, git_ignored, queue_module, refute, write
from harness import run_queue as q

POLICY = REPO / "orchestration" / "queue" / "POLICY.md"


def brief(queue_dir, topic, slug) -> str:
    [task] = (queue_dir / topic).glob(f"*-{slug}")
    return (task / "BRIEF.md").read_text(encoding="utf-8")


def test_a_brief_points_at_the_tracked_policy_by_absolute_path(topic, queue_dir):
    assert POLICY.is_file() and not git_ignored(POLICY), f"missing or gitignored: {POLICY}"
    expect(brief(queue_dir, topic, "document-the-states"), str(POLICY))


def test_the_policy_restates_no_proof_the_code_checks():
    # Parsed, not grepped: POLICY.md carries nothing collect checks, and the
    # operator's default comes from publish.conf rather than being typed in.
    out = queue_module(
        "text = open(sys.argv[1], encoding='utf-8').read()\n"
        "method, how = q.policy_publish_default()\n"
        "copied = [m for m, spec in q.PUBLISH_METHODS.items() if spec['proof'] in text]\n"
        "print(f'default={method} how={bool(how)} copied={copied}')\n",
        str(POLICY),
    )
    expect(out, "default=attested how=True", "copied=[]")


def test_a_brief_carries_the_operators_own_instructions_only_when_there_are_any(topic, queue_dir):
    refute(brief(queue_dir, topic, "document-the-states"), "OPERATOR.md")

    constitution = queue_dir / "OPERATOR.md"
    write(constitution, "Always reach for the operator's own `xyz` skill before writing a script.\n")
    ok(q("add", topic, "honour-the-constitution", "--title", "Honour the constitution",
         "--repo", "/tmp/x", "--branch", "fix/honour", "--base", "main"))
    with_one = brief(queue_dir, topic, "honour-the-constitution")
    # By absolute path, and saying the brief still wins over it.
    expect(with_one, str(constitution), "brief wins")

    write(constitution, "")
    ok(q("add", topic, "empty-constitution", "--title", "Empty constitution",
         "--repo", "/tmp/x", "--branch", "fix/empty", "--base", "main"))
    refute(brief(queue_dir, topic, "empty-constitution"), "OPERATOR.md")


def test_the_example_is_tracked_and_the_operators_copy_is_not():
    example = REPO / "orchestration" / "queue" / "OPERATOR.example.md"
    assert example.is_file() and not git_ignored(example)
    assert git_ignored(REPO / "orchestration" / "queue" / "OPERATOR.md")
