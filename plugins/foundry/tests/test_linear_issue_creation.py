"""Controlled creation tests on exact captured bodies (names neutralized) and synthetic cases."""
import copy
import difflib
import json
import runpy
import sys
from pathlib import Path

import pytest

from foundry import edit, write
from foundry.trackers import base, linear
from test_linear_tracker import PROJECT

pytest_plugins = ["test_linear_tracker"]


# Exact bodies sent to / read back from Linear for PAT-195, PAT-196 and PAT-202, with the
# owner/repository path of one URL replaced by the same neutral token on both sides.
PAIRS = {
    pair['name']: pair
    for pair in json.loads(
        (Path(__file__).parent / 'fixtures' / 'linear_issue_creation_pairs.json')
        .read_text(encoding='utf-8')
    )
}
SOURCE = PAIRS['pat-195']['source']
RENDERED = PAIRS['pat-195']['readback']


@pytest.mark.parametrize('name', sorted(PAIRS))
def test_exact_captures_are_admitted_with_exactly_the_observed_difference(name):
    pair = PAIRS[name]
    source, observed = pair['source'], pair['readback']
    assert len(source.encode()) == pair['source_bytes']
    assert len(observed.encode()) == pair['readback_bytes']
    assert source != observed
    diff = list(difflib.unified_diff(
        source.splitlines(keepends=True), observed.splitlines(keepends=True), n=0,
    ))[2:]
    assert diff == pair['observed_diff']
    assert linear._issue_creation_body_matches(observed, source)


@pytest.mark.parametrize(('source', 'observed'), [
    ('texte ~/.codex ici', 'texte \\~/.codex ici'),
    ('texte\n', 'texte'),
    ('## Cadre\n\ntexte', '## Cadre\n\ntexte'),
    ('texte', 'texte'),
])
def test_qualified_renderings(source, observed):
    assert linear._issue_creation_body_matches(observed, source)


@pytest.mark.parametrize('observed', [
    RENDERED.replace('Autorisation', 'autorisation'),
    RENDERED.replace('pull/138>)', 'pull/139>)'),
    RENDERED + '\n\n',
    RENDERED.replace('## Objet et origine\n\n', '## Objet et origine\n\n\n'),
    RENDERED.replace('https://', 'http://'),
])
def test_real_changes_refused(observed):
    assert not linear._issue_creation_body_matches(observed, SOURCE)


@pytest.mark.parametrize(('source', 'observed'), [
    ('texte\n\n', 'texte'),
    ('a  b', 'a b'),
    ('~other', '\\~other'),
    ('`~/.codex`', '`\\~/.codex`'),
    ('```\n## Cadre\ntexte ~/.codex\n```', '```\n## Cadre\n\ntexte \\~/.codex\n```'),
    ('[lien](https://example.org)', '[autre](https://example.org)'),
    ('````\n```\n## H\nx\n```\n````', '````\n```\n## H\n\nx\n```\n````'),
    ('~~~~\n~~~\n## H\nx\n~~~\n~~~~', '~~~~\n~~~\n## H\n\nx\n~~~\n~~~~'),
    ('<https://example.org/~/a>', '<https://example.org/\\~/a>'),
    ('<a ~/b>', '<a \\~/b>'),
    ('```a``` b\n```\n## H\nx\n```', '```a``` b\n```\n## H\n\nx\n```'),
    ('```\n    ```\n## H\nx\n```', '```\n    ```\n## H\n\nx\n```'),
    ('    ```\n```\n## H\nx\n```', '    ```\n```\n## H\n\nx\n```'),
    ('```\n\t```\n## H\nx\n```', '```\n\t```\n## H\n\nx\n```'),
    ('> ```\n> voir ~/x\n> ```', '> ```\n> voir \\~/x\n> ```'),
    ('> https://example.org/a', '> [https://example.org/a](<https://example.org/a>)'),
])
def test_unqualified_normalizations_refused(source, observed):
    assert not linear._issue_creation_body_matches(observed, source)


def intercept_create(instance, wire, monkeypatch, change=None, response=None):
    original = instance._graphql

    def graphql(document, variables, operation):
        result = original(document, variables, operation)
        if operation == 'issue.create':
            created = wire.issues['LIN-3']
            if change:
                change(created)
            if response:
                return response(result)
        return result

    monkeypatch.setattr(instance, '_graphql', graphql)


def test_create_recognizes_rendering_and_reconcile_is_read_only_replay(tracker, monkeypatch):
    instance, wire = tracker
    intercept_create(instance, wire, monkeypatch, lambda raw: raw.update(description=RENDERED))
    created = instance.create_issue(PROJECT, 'Task', SOURCE)
    assert created.id == 'LIN-3'
    before = copy.deepcopy(wire.issues)
    count = len(wire.calls)
    for _ in range(2):
        result = instance.reconcile_issue_creation(PROJECT, created.id, 'Task', SOURCE)
        assert result['validation'] == 'accepted'
        assert result['comparison'] == 'qualified-rendering'
        assert result['issue_id'] == 'LIN-3'
        assert result['creation'] == 'observed-existing'
    assert wire.issues == before
    assert not any('mutation ' in document for document, _ in wire.calls[count:])


@pytest.mark.parametrize('change', [
    lambda raw: raw.update(description='changed'),
    lambda raw: raw.update(title='changed'),
    lambda raw: raw.update(priority=4),
    lambda raw: raw.update(parent={'id': 'unexpected'}),
    lambda raw: raw.update(project={'id': 'foreign'}),
])
def test_partial_effect_refusal_names_known_created_id(tracker, monkeypatch, change):
    instance, wire = tracker
    intercept_create(instance, wire, monkeypatch, change)
    with pytest.raises(base.IssueCreationValidationError) as caught:
        instance.create_issue(PROJECT, 'Task', 'source', fields={'Priority': 'P1'})
    result = caught.value.details
    assert result['issue_id'] == 'LIN-3'
    assert result['creation'] == 'created'
    assert result['validation'] == 'refused'
    assert result['reconciliation'] == 'required'
    assert len(wire.issues) == 3


@pytest.mark.parametrize('response', [
    lambda _: {},
    lambda _: {'issueCreate': {'success': False}},
    lambda _: {'issueCreate': {'success': True, 'issue': {}}},
])
def test_ambiguous_response_resolved_by_read_not_second_create(tracker, monkeypatch, response):
    instance, wire = tracker
    intercept_create(instance, wire, monkeypatch, response=response)
    assert instance.create_issue(PROJECT, 'Task', 'source').id == 'LIN-3'
    assert sum('FoundryLinearIssueCreate' in doc for doc, _ in wire.calls) == 1


def test_ambiguous_response_and_unreadable_native_remains_unknown(tracker, monkeypatch):
    instance, wire = tracker
    original = instance._graphql

    def graphql(document, variables, operation):
        if operation in {'issue.create', 'issue.read'}:
            raise linear.LinearTrackerError(operation, None, 'transport_error')
        return original(document, variables, operation)

    monkeypatch.setattr(instance, '_graphql', graphql)
    with pytest.raises(base.IssueCreationValidationError) as caught:
        instance.create_issue(PROJECT, 'Task', 'source')
    result = caught.value.details
    assert result['creation'] == 'unknown'
    assert result['issue_id'] is None
    assert result['native_id'] is None
    assert result['client_id']
    assert result['validation'] == 'unknown'
    assert len(wire.issues) == 2


def test_known_response_then_unreadable_retains_public_id(tracker, monkeypatch):
    instance, wire = tracker
    original = instance._read_raw

    def read(reference):
        if reference not in {'LIN-1', 'LIN-2'}:
            raise base.IssueUnavailableError(reference)
        return original(reference)

    monkeypatch.setattr(instance, '_read_raw', read)
    with pytest.raises(base.IssueCreationValidationError) as caught:
        instance.create_issue(PROJECT, 'Task', 'source')
    assert caught.value.details['issue_id'] == 'LIN-3'
    assert caught.value.details['creation'] == 'created'
    assert caught.value.details['validation'] == 'unknown'


@pytest.mark.parametrize(('change', 'expected'), [
    (lambda raw: raw.update(description='altered'), r'diverged: body\)'),
    (lambda raw: raw.update(title='altered'), r'diverged: title\)'),
    (lambda raw: raw.update(priority=4), r'diverged: priority\)'),
    (lambda raw: raw.update(parent={'id': 'unexpected'}), r'diverged: parent_id\)'),
    (lambda raw: raw.update(project={'id': 'foreign'}), 'issue_outside_binding'),
])
def test_reconciliation_refuses_divergence_without_effect(tracker, change, expected):
    instance, wire = tracker
    instance.create_issue(PROJECT, 'Task', 'source', fields={'Priority': 'P1'})
    change(wire.issues['LIN-3'])
    before = copy.deepcopy(wire.issues)
    count = len(wire.calls)
    # The project divergence is a binding error; every other one names its property.
    with pytest.raises((base.TrackerConflictError, base.TrackerBindingError), match=expected):
        instance.reconcile_issue_creation(PROJECT, 'LIN-3', 'Task', 'source', {'Priority': 'P1'})
    assert before == wire.issues
    assert not any('mutation ' in doc for doc, _ in wire.calls[count:])


def test_portable_reconciliation_binding_and_cli_file(tracker, monkeypatch, tmp_path, capsys):
    instance, wire = tracker
    instance.create_issue(PROJECT, 'Task', 'source')
    validated = []
    monkeypatch.setattr(write, 'issue_binding', lambda tr, *ids: validated.extend(ids) or PROJECT)
    monkeypatch.setattr(edit.foundry, 'tracker', lambda: instance)
    spec = tmp_path / 'spec.json'
    spec.write_text(json.dumps({'title': 'Task', 'body': 'source'}))
    before = copy.deepcopy(wire.issues)
    for _ in range(2):
        launcher = Path(__file__).resolve().parents[1] / 'tooling' / 'foundry_cli.py'
        monkeypatch.setattr(sys, 'argv', [str(launcher), 'edit', 'reconcile-create', 'LIN-3', str(spec)])
        runpy.run_path(str(launcher), run_name='__main__')
        assert json.loads(capsys.readouterr().out)['validation'] == 'accepted'
    assert validated == ['LIN-3', 'LIN-3']
    assert wire.issues == before


def test_cli_partial_failure_has_no_success_output(tracker, monkeypatch, capsys):
    instance, wire = tracker
    monkeypatch.setattr(edit.foundry, 'tracker', lambda: instance)
    monkeypatch.setattr(write, 'mutation_project', lambda tr: PROJECT)
    intercept_create(instance, wire, monkeypatch, lambda raw: raw.update(description='changed'))
    with pytest.raises(SystemExit):
        edit.create_issue(json.dumps({'title': 'Task', 'body': 'source'}))
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)['issue_id'] == 'LIN-3'


def test_lost_response_after_effect_resolves(tracker, monkeypatch):
    instance, wire = tracker
    original = instance._graphql

    def graphql(document, variables, operation):
        result = original(document, variables, operation)
        if operation == 'issue.create':
            raise linear.LinearTrackerError(operation, None, 'transport_error')
        return result

    monkeypatch.setattr(instance, '_graphql', graphql)
    assert instance.create_issue(PROJECT, 'Task', 'source').id == 'LIN-3'
    assert sum('FoundryLinearIssueCreate' in doc for doc, _ in wire.calls) == 1


def test_quota_exhaustion_propagates_typed_without_extra_read(tracker, monkeypatch):
    instance, wire = tracker
    original = instance._graphql

    def graphql(document, variables, operation):
        if operation == 'issue.create':
            raise linear.LinearQuotaExhaustedError(
                operation, 400, 0, 1_700_000_000_000, scope='requests',
            )
        return original(document, variables, operation)

    monkeypatch.setattr(instance, '_graphql', graphql)
    count = len(wire.calls)
    with pytest.raises(linear.LinearQuotaExhaustedError) as caught:
        instance.create_issue(PROJECT, 'Task', 'source')
    assert caught.value.reset_at == '2023-11-14T22:13:20Z'
    client_id = caught.value.client_id
    assert client_id and client_id in str(caught.value)
    assert not any('mutation ' in document for document, _ in wire.calls[count:])
    assert not any(client_id in json.dumps(variables) for _, variables in wire.calls[count:])
    assert len(wire.issues) == 2


def _refuse_create(instance, wire, monkeypatch, code):
    original = wire.__call__
    sent = []

    def transport(document, variables):
        if 'FoundryLinearIssueCreate' in document:
            sent.append(document)
            return {'errors': [{'message': 'secret body text', 'extensions': {'code': code}}]}
        return original(document, variables)

    monkeypatch.setattr(instance, '_transport', transport)
    return sent


def test_usage_limit_refusal_is_typed_known_and_not_retried(tracker, monkeypatch):
    instance, wire = tracker
    sent = _refuse_create(instance, wire, monkeypatch, 'USAGE_LIMIT_EXCEEDED')
    count = len(wire.calls)
    with pytest.raises(linear.LinearUsageLimitError) as caught:
        instance.create_issue(PROJECT, 'Task', 'source')
    text = str(caught.value)
    assert caught.value.code == 'usage_limit_exceeded'
    assert 'usage limit' in text and 'creation=not_created' in text
    assert 'graphql_error' not in text and 'unknown' not in text and 'secret body' not in text
    assert caught.value.client_id in text
    assert len(sent) == 1
    assert not any('mutation ' in d and 'IssueCreate' not in d for d, _ in wire.calls[count:])
    assert not any('query ' in d for d, _ in wire.calls[count:])  # no read-back follows
    assert len(wire.issues) == 2


def test_another_unknown_graphql_code_stays_graphql_error(tracker, monkeypatch):
    instance, wire = tracker
    _refuse_create(instance, wire, monkeypatch, 'SOMETHING_ELSE')
    with pytest.raises(base.IssueCreationValidationError) as caught:
        instance.create_issue(PROJECT, 'Task', 'source')
    assert not isinstance(caught.value, linear.LinearUsageLimitError)
    assert caught.value.details['creation'] == 'unknown'
    assert caught.value.details['causes']['create']['code'] == 'graphql_error'


@pytest.mark.parametrize('status, operation', ((400, 'issue.create'), (503, 'issue.read')))
def test_usage_limit_code_in_an_http_error_body_is_the_same_typed_refusal(
        monkeypatch, status, operation):
    import io
    import urllib.error

    body = json.dumps({'errors': [{'extensions': {'code': 'USAGE_LIMIT_EXCEEDED'}}]}).encode()
    calls = []

    class Opener:
        def open(self, request, timeout=None):
            calls.append(request)
            raise urllib.error.HTTPError('https://x', status, 'x', {}, io.BytesIO(body))

    monkeypatch.setattr(linear.urllib.request, 'build_opener', lambda *a, **k: Opener())
    instance = linear.LinearTracker(token='linear-test-secret')
    with pytest.raises(linear.LinearUsageLimitError):
        instance._graphql('mutation {}' if operation == 'issue.create' else 'query {}', {}, operation)
    assert len(calls) == 1  # not retried, even on a 503 read


def test_reconciliation_checks_requested_fields_and_parent(tracker):
    instance, wire = tracker
    fields = {'State': 'ready', 'Priority': 'P1', 'Estimate': 5,
              'Milestone': 'M1', 'Type': 'Feature', 'Labels': ['pilot']}
    instance.create_issue(PROJECT, 'Task', 'source', fields=fields, parent='LIN-1')
    assert instance.reconcile_issue_creation(
        PROJECT, 'LIN-3', 'Task', 'source', fields, 'LIN-1',
    )['validation'] == 'accepted'
    for field, changed in [('State', 'blocked'), ('Priority', 'P2'), ('Estimate', 8),
                           ('Milestone', None), ('Type', 'Bug'), ('Labels', ['api'])]:
        with pytest.raises(base.TrackerConflictError):
            instance.reconcile_issue_creation(
                PROJECT, 'LIN-3', 'Task', 'source', {**fields, field: changed}, 'LIN-1',
            )
    with pytest.raises(base.TrackerConflictError):
        instance.reconcile_issue_creation(PROJECT, 'LIN-3', 'Task', 'source', fields, 'LIN-2')


def test_partial_rendering_is_not_admitted():
    assert not linear._issue_creation_body_matches(RENDERED + '\n', SOURCE)
    assert not linear._issue_creation_body_matches(SOURCE.rstrip('\n'), SOURCE)


def test_reconciliation_reads_fresh_even_in_read_scope(tracker):
    instance, wire = tracker
    instance.create_issue(PROJECT, 'Task', 'source')
    with instance.read_scope():
        instance._read_raw('LIN-3')
        wire.issues['LIN-3']['description'] = 'changed'
        with pytest.raises(base.TrackerConflictError):
            instance.reconcile_issue_creation(PROJECT, 'LIN-3', 'Task', 'source')


def test_empty_body_null_readback_retains_empty_contract(tracker, monkeypatch):
    instance, wire = tracker
    intercept_create(instance, wire, monkeypatch, lambda raw: raw.update(description=None))
    assert instance.create_issue(PROJECT, 'Task', '').id == 'LIN-3'
    result = instance.reconcile_issue_creation(PROJECT, 'LIN-3', 'Task', '')
    assert result['comparison'] == 'exact'
    assert result['source_sha256'] == result['readback_sha256']


def test_reconciliation_type_scope_preserves_unrequested_labels(tracker):
    instance, wire = tracker
    instance.create_issue(PROJECT, 'Task', 'source')
    # Existing labels and a subsequent state are outside this declared comparison.
    wire.issues['LIN-3']['state']['id'] = 'state-in-progress'
    result = instance.reconcile_issue_creation(
        PROJECT, 'LIN-3', 'Task', 'source', {'Type': 'Feature'},
    )
    assert result['validation'] == 'accepted'
    assert 'State' not in result['compared']
    assert 'Labels' not in result['compared']


# --- PAT-205: deferred remarks of PAT-197 and the Linear autolink of PAT-209 -------------

@pytest.mark.parametrize(('source', 'observed'), [
    # Observed on PAT-209 (2026-10-15): Linear wraps this reference form.
    ('voir (AGENTS.md#R3) ici', 'voir ([AGENTS.md#R3](<http://AGENTS.md#R3>)) ici'),
    ('(AGENTS.md#R12)', '([AGENTS.md#R12](<http://AGENTS.md#R12>))'),
    # Exact forms stay admitted.
    ('voir (AGENTS.md#R3) ici', 'voir (AGENTS.md#R3) ici'),
    # A line break other than LF is not a line end: no empty line is inserted.
    ('a\x0c## H\nx', 'a\x0c## H\nx'),
    ('a\u2028## H\nx', 'a\u2028## H\nx'),
    # A heading that follows a quote line is a heading: it receives the empty line.
    ('> q\n## H\nx', '> q\n## H\n\nx'),
    # A lazy continuation line of a quote is a plain line and receives the rendering.
    ('> a\nvoir ~/x', '> a\nvoir \\~/x'),
    ('> a\nhttps://example.org/a', '> a\n[https://example.org/a](<https://example.org/a>)'),
    # The URL alphabet contains the dot: a final dot is wrapped with the URL (deduced from
    # the rule, not observed; a GFM autolink would exclude it).
    ('voir https://example.org/a.', 'voir [https://example.org/a.](<https://example.org/a.>)'),
])
def test_pat205_qualified_renderings(source, observed):
    assert linear._issue_creation_body_matches(observed, source)


@pytest.mark.parametrize(('source', 'observed'), [
    ('voir (AGENTS.md#R3) ici', 'voir ([AGENTS.md#R3](<https://AGENTS.md#R3>)) ici'),
    ('voir (AGENTS.md#R3) ici', 'voir ([AGENTS.md#R4](<http://AGENTS.md#R3>)) ici'),
    ('voir (CLAUDE.md#R3) ici', 'voir ([CLAUDE.md#R3](<http://CLAUDE.md#R3>)) ici'),
    ('voir [x](AGENTS.md#R3) ici', 'voir [x]([AGENTS.md#R3](<http://AGENTS.md#R3>)) ici'),
    # A heading containing a backtick stays exact: no empty line is qualified after it.
    ('## Portée de `edit`\ntexte', '## Portée de `edit`\n\ntexte'),
    # A line break other than LF does not make a heading.
    ('a\x0c## H\nx', 'a\x0c## H\n\nx'),
    # An inline code span over several lines stays exact on every line.
    ('`a\n~/b\nc`', '`a\n\\~/b\nc`'),
    ('``a\n~/b `x`\nc``', '``a\n\\~/b `x`\nc``'),
    # Inside an HTML comment, over one line or several, nothing is rendered.
    ('<!--\n~/b\n-->', '<!--\n\\~/b\n-->'),
    ('<!-- https://example.org/a -->', '<!-- [https://example.org/a](<https://example.org/a>) -->'),
    ('<!-- a\nhttps://example.org/a\n-->', '<!-- a\n[https://example.org/a](<https://example.org/a>)\n-->'),
])
def test_pat205_unqualified_renderings_refused(source, observed):
    assert not linear._issue_creation_body_matches(observed, source)


@pytest.mark.parametrize(('source', 'rendered'), [
    ('`a\n~/b\nc`', '`a\n\\~/b\nc`'),
    ('<!--\n~/b\n-->', '<!--\n\\~/b\n-->'),
    ('## Portée de `edit`\ntexte', '## Portée de `edit`\n\ntexte'),
    ('a\x0c## H\nx', 'a\x0c## H\n\nx'),
])
def test_pat205_exact_context_sources_refuse_the_rendering(source, rendered):
    # The rendering rules would change these sources (tilde escape, inserted empty
    # line); in these contexts only the exact bytes are admitted.
    assert linear._issue_creation_body_matches(source, source)
    assert not linear._issue_creation_body_matches(rendered, source)


def test_pat205_rendering_resumes_after_a_closed_span_and_comment():
    source = '`a\nb`\nvoir ~/x\n<!--\n~/y\n-->\nvoir ~/z'
    observed = '`a\nb`\nvoir \\~/x\n<!--\n~/y\n-->\nvoir \\~/z'
    assert linear._issue_creation_body_matches(observed, source)


def test_pat205_refusal_names_the_diverging_properties_only(tracker, monkeypatch):
    instance, wire = tracker
    intercept_create(
        instance, wire, monkeypatch,
        lambda raw: raw.update(title='secret-title', priority=4),
    )
    with pytest.raises(base.IssueCreationValidationError) as caught:
        instance.create_issue(PROJECT, 'Task', 'source', fields={'Priority': 'P1'})
    assert caught.value.details['validation'] == 'refused'
    wire.issues['LIN-3'].update(title='secret-title', priority=4)
    with pytest.raises(base.TrackerConflictError) as refused:
        instance.reconcile_issue_creation(
            PROJECT, 'LIN-3', 'Task', 'source', {'Priority': 'P1'},
        )
    assert str(refused.value).endswith('(diverged: title, priority)')
    assert 'secret-title' not in str(refused.value)


def test_pat205_reconcile_accepts_the_client_id_of_an_unvalidated_creation(tracker, monkeypatch):
    instance, wire = tracker
    intercept_create(instance, wire, monkeypatch, lambda raw: raw.update(title='changed'))
    with pytest.raises(base.IssueCreationValidationError) as caught:
        instance.create_issue(PROJECT, 'Task', 'source')
    client_id = caught.value.details['client_id']
    wire.issues['LIN-3']['title'] = 'Task'
    result = instance.reconcile_issue_creation(PROJECT, client_id, 'Task', 'source')
    assert result['validation'] == 'accepted'
    assert result['issue_id'] == 'LIN-3'


def test_pat205_certain_refusal_before_creation_keeps_its_code(tracker, monkeypatch):
    instance, wire = tracker
    original = instance._graphql

    def graphql(document, variables, operation):
        if operation == 'issue.create':
            raise linear.LinearTrackerError(operation, 400, 'graphql_error')
        return original(document, variables, operation)

    monkeypatch.setattr(instance, '_graphql', graphql)
    with pytest.raises(base.IssueCreationValidationError) as caught:
        instance.create_issue(PROJECT, 'Task', 'source')
    details = caught.value.details
    assert details['creation'] == 'unknown'
    assert details['causes']['create'] == {
        'operation': 'issue.create', 'status': 400, 'code': 'graphql_error',
    }


def test_pat205_quota_during_readback_keeps_code_and_reset_at(tracker, monkeypatch):
    instance, wire = tracker
    original = instance._graphql

    def graphql(document, variables, operation):
        if operation == 'issue.read':
            raise linear.LinearQuotaExhaustedError(
                operation, 400, 0, 1_700_000_000_000, scope='requests',
            )
        return original(document, variables, operation)

    monkeypatch.setattr(instance, '_graphql', graphql)
    with pytest.raises(base.IssueCreationValidationError) as caught:
        instance.create_issue(PROJECT, 'Task', 'source')
    assert caught.value.details['causes']['read'] == {
        'operation': 'issue.read', 'status': 400, 'code': 'quota_exhausted',
        'reset_at': '2023-11-14T22:13:20Z',
    }


def test_pat205_validation_after_the_readback_makes_no_provider_call(tracker, monkeypatch):
    # Evidence for the broad ``except`` of ``create_issue``: after the read-back, validation
    # and normalization work on the record already read.  A provider call there would be
    # the only way for a transport or quota error to be classified ``refused`` instead of
    # ``unknown``; none is made.
    instance, wire = tracker
    original = instance._graphql
    state = {'read': False}

    def graphql(document, variables, operation):
        assert not state['read'], f'provider call after the read-back: {operation}'
        result = original(document, variables, operation)
        if operation == 'issue.read':
            state['read'] = True
        return result

    monkeypatch.setattr(instance, '_graphql', graphql)
    assert instance.create_issue(PROJECT, 'Task', 'source').id == 'LIN-3'
