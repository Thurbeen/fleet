"""Instruction files hand review ownership to one installed skill.

These are the worker's executable instructions, rather than a second posting
implementation. A missing publisher must stay a setup problem instead of
silently choosing a different review format.
"""

import pytest

from harness import REPO


@pytest.mark.parametrize('path', [
    '.agents/skills/review-prs/SKILL.md', 'orchestration/queue/POLICY.md',
])
def test_review_instructions_delegate_without_a_posting_fallback(path):
    text = (REPO / path).read_text(encoding='utf-8')
    assert 'thurview-pr-review' in text
    assert 'publish config' in text
    assert 'do not fall back' in text.lower()
    assert 'fleet review-post' not in text
    assert 'PUBLIC_REVIEW_LINKS' not in text
