"""Review links are optional evidence beside the verified artifact."""
import os
import json
from pathlib import Path

import pytest
import yaml
from queuekit import ok

from harness import run_queue as q
from harness import PYTHON, REPO, run, write


@pytest.mark.parametrize('review', ['https://review.example/reviews/change', None])
def test_collect_and_every_record_view_include_review(topic, queue_dir, stubs, review):
    task = queue_dir / topic / '01-drop-idle-default'
    stubs.pipeline_pr(991, 'fix/drop-idle-default')
    front = 'outcome: shipped\nartifact: https://github.com/Thurbeen/thurbox/pull/991\n'
    if review:
        front += f'review: {review}\n'
    write(task / 'result.md', f'---\n{front}---\nDone.\n')
    collected = ok(q('collect', '--no-reap'))
    doc = yaml.safe_load((task / 'task.yaml').read_text(encoding='utf-8'))
    assert doc['state'] == 'done'
    if review:
        assert doc['review_url'] == review
    expected = f'review: {review or "missing"}'
    assert expected in collected.out
    assert expected in ok(q('list')).out
    assert expected in ok(q('show', f'{topic}/01-drop-idle-default')).out
    logs = '\n'.join(p.read_text(encoding='utf-8') for p in Path(os.environ['FLEET_RUNS_DIR']).glob('*.md'))
    assert expected in logs


def test_collect_refreshes_review_after_publication_without_reopening(topic, queue_dir, stubs):
    task = queue_dir / topic / '01-drop-idle-default'
    stubs.pipeline_pr(991, 'fix/drop-idle-default')
    front = 'outcome: shipped\nartifact: https://github.com/Thurbeen/thurbox/pull/991\n'
    write(task / 'result.md', f'---\n{front}---\nDone.\n')
    ok(q('collect', '--no-reap'))
    write(task / 'result.md', f'---\n{front}review: https://review.example/reviews/updated\n---\nDone.\n')
    ok(q('collect', '--no-reap'))
    doc = yaml.safe_load((task / 'task.yaml').read_text(encoding='utf-8'))
    assert doc['state'] == 'done'
    assert doc['review_url'].endswith('/updated')


def test_each_repository_keeps_its_own_review(topic, queue_dir):
    task = queue_dir / topic / '01-drop-idle-default'
    doc = yaml.safe_load((task / 'task.yaml').read_text(encoding='utf-8'))
    doc['add_repos'] = ['/repo/second@main']
    write(task / 'task.yaml', yaml.safe_dump(doc))
    write(task / 'result.md', '---\noutcome: stuck\nreviews:\n'
          '  /tmp/repo-a: https://review.example/reviews/first\n'
          '  /repo/second: https://review.example/reviews/second\n---\nWaiting.\n')
    ok(q('collect', '--no-reap'))
    doc = yaml.safe_load((task / 'task.yaml').read_text(encoding='utf-8'))
    assert doc['review_url'] == [
        {'repo': '/tmp/repo-a', 'url': 'https://review.example/reviews/first'},
        {'repo': '/repo/second', 'url': 'https://review.example/reviews/second'}]
    shown = ok(q('show', f'{topic}/01-drop-idle-default')).out
    assert '/reviews/first' in shown and '/reviews/second' in shown


def collect_result_reads():
    script = '''import json, os, runpy, sys
reads = []
def audit(event, args):
    if event == "open" and isinstance(args[0], str) and os.path.basename(args[0]) == "result.md":
        reads.append(args[0])
sys.addaudithook(audit)
sys.argv = [sys.argv[1], "collect", "--no-reap"]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit as exc:
    if exc.code: raise
print("RESULT_READS=" + json.dumps(reads))
'''
    out = ok(run([*PYTHON, '-c', script, str(REPO / 'scripts/lib/queue.py')]))
    return json.loads(next(line.removeprefix('RESULT_READS=') for line in out.stdout.splitlines()
                           if line.startswith('RESULT_READS=')))


def test_collect_does_not_read_unchanged_completed_results(topic, queue_dir, stubs):
    task = queue_dir / topic / '01-drop-idle-default'
    stubs.pipeline_pr(991, 'fix/drop-idle-default')
    front = 'outcome: shipped\nartifact: https://github.com/Thurbeen/thurbox/pull/991\n'
    write(task / 'result.md', f'---\n{front}---\nDone.\n')
    assert collect_result_reads() == [str(task / 'result.md')]
    assert collect_result_reads() == []
    write(task / 'result.md', f'---\n{front}review: https://review.example/updated\n---\nDone.\n')
    assert collect_result_reads() == [str(task / 'result.md')]
    assert yaml.safe_load((task / 'task.yaml').read_text(encoding='utf-8'))['review_url'].endswith('/updated')


def test_collect_does_not_read_archived_results(topic, queue_dir):
    for task in (queue_dir / topic).glob('*/task.yaml'):
        doc = yaml.safe_load(task.read_text(encoding='utf-8'))
        doc['state'] = 'landed'
        write(task, yaml.safe_dump(doc))
        write(task.parent / 'result.md', '---\noutcome: shipped\n---\nDone.\n')
    meta = queue_dir / topic / 'topic.yaml'
    doc = yaml.safe_load(meta.read_text(encoding='utf-8'))
    doc['archived'] = '2026-10-01T00:00:00+00:00'
    write(meta, yaml.safe_dump(doc))
    assert collect_result_reads() == []
