"""Drive review publication through both real adapters with the other CLI a tripwire."""
import json

import pytest
import yaml

from harness import run_fleet, write

API_STUB = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
store = Path(os.environ['REVIEW_STORE'])
s = json.loads(store.read_text(encoding='utf-8'))
cli = os.environ['REVIEW_CLI']
if args[0] != 'api':
    raise SystemExit('only API calls expected')
if cli == 'glab':
    assert '--hostname' in args and args[args.index('--hostname') + 1] == 'gitlab.example'
method = args[args.index('--method') + 1] if '--method' in args else 'GET'
endpoint = "user" if args[-1] == "user" else args[1].split("?", 1)[0]
payload = json.load(sys.stdin) if '--input' in args else {}
s['calls'].append([method, endpoint, payload])
s.setdefault('drafts', [])
if endpoint == 'graphql':
    if 'mutation' in payload['query']:
        for thread in s['threads']:
            if thread['id'] == payload['variables']['id']:
                thread['isResolved'] = True
        out = {'data': {'resolveReviewThread': {'thread': {'isResolved': True}}}}
    else:
        out = {'data': {'repository': {'pullRequest': {'reviewThreads': {
            'nodes': s['threads'], 'pageInfo': {'hasNextPage': False}}}}}}
elif endpoint == 'user':
    out = {'login': 'reviewer', 'username': 'reviewer'}
elif endpoint.endswith('/pulls/7') or endpoint.endswith('/merge_requests/7'):
    out = {'head': {'sha': s['head']}, 'sha': s['head'],
           'diff_refs': {'base_sha': 'b' * 40, 'start_sha': 'b' * 40, 'head_sha': s['head']}}
elif endpoint.startswith('repos/') and endpoint.count('/') == 2 or endpoint == 'projects/acme%2Fwidgets':
    out = {'private': s['private'], 'visibility': 'private' if s['private'] else 'public'}
elif '/discussions/' in endpoint and method == 'PUT':
    for t in s['threads']:
        if endpoint.endswith('/' + t['id']):
            t['notes'][0]['resolved'] = True
    out = {}
elif endpoint.endswith('/reviews') and method == 'POST':
    s['batches'] += 1
    for c in payload['comments']:
        s['threads'].append({'id': str(len(s['threads']) + 1), 'isResolved': False,
            'comments': {'nodes': [{'body': c['body'], 'author': {'login': 'reviewer'}}]}})
    out = {}
elif endpoint.endswith('/draft_notes/bulk_publish'):
    s['batches'] += 1
    for d in s['drafts']:
        s['threads'].append({'id': str(len(s['threads']) + 1), 'notes': [
            {'body': d['note'], 'author': {'username': 'reviewer'}, 'resolved': False, 'resolvable': True}]})
    s['drafts'] = []
    out = {}
elif '/draft_notes/' in endpoint and method == 'DELETE':
    s['drafts'] = [d for d in s['drafts'] if str(d['id']) != endpoint.rsplit('/', 1)[1]]
    out = {}
elif endpoint.endswith('/draft_notes'):
    if method == 'POST':
        out = {'id': len(s['drafts']) + 1, **payload}
        s['drafts'].append(out)
    else:
        out = s['drafts']
elif endpoint.endswith('/discussions'):
    if method == 'POST':
        s['batches'] += 1
        s['threads'].append({'id': str(len(s['threads']) + 1), 'notes': [
            {'body': payload['body'], 'author': {'username': 'reviewer'}, 'resolved': False, 'resolvable': True}]})
        out = {}
    else:
        out = s['threads']
elif '/comments/' in endpoint or '/notes/' in endpoint:
    s['notes'][0]['body'] = payload['body']
    out = s['notes'][0]
elif endpoint.endswith('/comments') or endpoint.endswith('/notes'):
    if method == 'POST':
        s['notes'].append({'id': 11, 'body': payload['body'],
            'author': {'username': 'reviewer'}, 'user': {'login': 'reviewer'}})
        out = s['notes'][-1]
    else:
        out = s['notes']
else:
    raise SystemExit('unexpected endpoint ' + endpoint)
store.write_text(json.dumps(s), encoding='utf-8')
print(json.dumps(out))
'''


@pytest.fixture(params=['gh', 'glab'])
def publisher(request, tmp_path, stubs):
    cli = request.param
    stubs.tool(cli, API_STUB)
    stubs.tool('glab' if cli == 'gh' else 'gh', "raise SystemExit('wrong forge CLI')")
    store = tmp_path / 'store.json'
    write(store, json.dumps({'notes': [], 'threads': [], 'calls': [], 'batches': 0,
                            'private': False, 'head': 'a' * 40}))
    url = ('https://github.com/acme/widgets/pull/7' if cli == 'gh'
           else 'https://gitlab.example/acme/widgets/-/merge_requests/7')
    source = tmp_path / 'review.yaml'
    config = tmp_path / 'review.conf'
    write(config, '')

    def post(head='a' * 40, findings=None, summary='Explain the change.'):
        write(source, yaml.safe_dump({'head': head, 'verdict': 'comment', 'summary': summary,
              'confidence': 0.9, 'review': 'https://review.example/reviews/change', 'findings': findings or []}))
        return run_fleet('review-post', url, '--file', str(source), '--config', str(config),
                   REVIEW_STORE=str(store), REVIEW_CLI=cli, GITLAB_HOST='gitlab.example')

    return post, store, config, cli


def read(store):
    return json.loads(store.read_text(encoding='utf-8'))


def test_one_summary_updated_on_a_new_head_and_no_empty_inline_batch(publisher):
    post, store, _, _ = publisher
    first = post()
    assert first.code == 0, first.out
    state = read(store)
    state['head'] = 'c' * 40
    write(store, json.dumps(state))
    second = post('c' * 40)
    assert second.code == 0, second.out
    state = read(store)
    assert len(state['notes']) == 1
    assert '<!-- fleet-review -->' in state['notes'][0]['body']
    assert 'c' * 40 in state['notes'][0]['body']
    assert state['batches'] == 0


@pytest.mark.parametrize('private, opt_in, linked', [(False, False, False), (True, False, True), (False, True, True)])
def test_public_links_default_off_private_links_always_present(publisher, private, opt_in, linked):
    post, store, config, _ = publisher
    state = read(store)
    state['private'] = private
    write(store, json.dumps(state))
    write(config, 'PUBLIC_REVIEW_LINKS=on\n' if opt_in else '')
    done = post()
    assert done.code == 0, done.out
    body = read(store)['notes'][0]['body']
    assert ('https://review.example/reviews/change' in body) == linked
    if not linked:
        assert 'thurview review available to the operator' in body


def test_only_new_findings_are_posted_and_fixed_threads_are_resolved(publisher):
    post, store, _, cli = publisher
    finding = {'id': 'bad-state', 'severity': 'high', 'body': 'Incorrect state.', 'path': 'fleet/cli.py', 'line': 20}
    for _ in range(2):
        done = post(findings=[finding])
        assert done.code == 0, done.out
    state = read(store)
    assert len(state['threads']) == 1
    assert state['batches'] == 1
    state['head'] = 'c' * 40
    write(store, json.dumps(state))
    done = post('c' * 40)
    assert done.code == 0, done.out
    state = read(store)
    assert (state['threads'][0]['isResolved'] if cli == 'gh' else state['threads'][0]['notes'][0]['resolved'])
    assert state['batches'] == 1


def test_new_findings_are_published_as_one_batch(publisher):
    post, store, _, _ = publisher
    findings = [{'id': str(n), 'severity': 'medium', 'body': 'Concrete defect.',
                 'path': 'fleet/cli.py', 'line': n + 10} for n in range(2)]
    done = post(findings=findings)
    assert done.code == 0, done.out
    state = read(store)
    assert len(state['threads']) == 2
    assert state['batches'] == 1


def test_stale_head_makes_no_mutation(publisher):
    post, store, _, _ = publisher
    done = post('c' * 40)
    assert done.code == 1
    assert 'head moved' in done.out
    assert not read(store)['notes']
    assert not read(store)['threads']


def test_public_link_is_also_redacted_from_summary_and_findings(publisher):
    post, store, _, _ = publisher
    url = 'https://review.example/reviews/change'
    done = post(summary=f'Walkthrough: {url}', findings=[{
        'id': 'detail', 'severity': 'low', 'body': f'Defect explained at {url}',
        'path': 'fleet/cli.py', 'line': 20}])
    assert done.code == 0, done.out
    state = read(store)
    assert url not in state['notes'][0]['body']
    assert url not in json.dumps(state['threads'])


def test_gitlab_preserves_unrelated_pending_drafts(publisher):
    post, store, _, cli = publisher
    if cli != 'glab':
        pytest.skip('GitLab has pending draft notes')
    state = read(store)
    state['drafts'] = [{'id': 41, 'note': 'Human pending comment.'}]
    write(store, json.dumps(state))
    done = post(findings=[{'id': 'bad', 'severity': 'high', 'body': 'Concrete defect.',
                          'path': 'fleet/cli.py', 'line': 20}])
    assert done.code == 1
    assert 'unrelated draft notes' in done.out
    state = read(store)
    assert state['drafts'] == [{'id': 41, 'note': 'Human pending comment.'}]
    assert state['batches'] == 0
    assert not state['notes']


def test_numeric_finding_id_is_rejected_before_mutation(publisher):
    post, store, _, _ = publisher
    done = post(findings=[{'id': 12, 'severity': 'low', 'body': 'Concrete defect.',
                          'path': 'fleet/cli.py', 'line': 20}])
    assert done.code == 1
    assert not read(store)['calls']
