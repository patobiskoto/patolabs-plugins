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


@pytest.mark.parametrize('change', [
    lambda raw: raw.update(description='altered'),
    lambda raw: raw.update(title='altered'),
    lambda raw: raw.update(priority=4),
    lambda raw: raw.update(parent={'id': 'unexpected'}),
    lambda raw: raw.update(project={'id': 'foreign'}),
])
def test_reconciliation_refuses_divergence_without_effect(tracker, change):
    instance, wire = tracker
    instance.create_issue(PROJECT, 'Task', 'source', fields={'Priority': 'P1'})
    change(wire.issues['LIN-3'])
    before = copy.deepcopy(wire.issues)
    count = len(wire.calls)
    with pytest.raises((base.TrackerConflictError, base.TrackerBindingError)):
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
