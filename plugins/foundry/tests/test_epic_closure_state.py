"""PAT-100: truthful close-epic interruption messages and the read-only --status mode."""

from __future__ import annotations

import pytest

from foundry import issue as issue_cli
from foundry import write
from foundry.trackers.base import TrackerConflictError
from foundry.trackers.linear import (
    LinearQuotaExhaustedError, LinearTrackerError,
)

from test_epic_closure_transports import (  # noqa: F401  (autouse intent isolation)
    _isolated_youtrack_epic_closure_intents,
    _linear_graph,
)

FLAGS = {"--human-verdict=accepted"}
RESET_MS = 1_800_000_000_000


@pytest.fixture
def linear(monkeypatch, tmp_path):
    tracker, wire, project = _linear_graph()
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    monkeypatch.setattr(issue_cli.foundry, "tracker", lambda: tracker)
    wire.base = len(wire.calls)  # graph setup (deliveries) is not under test
    return tracker, wire, project, tmp_path / "linear-epic-closure-intents"


def _message(*flags) -> str:
    with pytest.raises(SystemExit) as raised:
        issue_cli.close_epic("LIN-1", flags=set(flags or FLAGS))
    return str(raised.value)


def _mutations(wire):
    return [c for c in wire.calls[getattr(wire, "base", 0):] if "mutation" in c[0]]


def _audits(wire):
    return [r for r in wire.comments.values() if "foundry-epic-closure.v1" in r["body"]]


def _fail_when(tracker, wire, predicate, error):
    original = wire.__call__

    def transport(document, variables):
        if predicate(document, variables):
            raise error
        return original(document, variables)

    tracker._transport = transport


# --- AC1/AC2: state classification in the interruption message --------------------

def test_interruption_before_any_write_names_network_cause_and_says_nothing_written(linear):
    tracker, wire, _project, intents = linear
    _fail_when(
        tracker, wire,
        lambda doc, v: "FoundryLinearIssue" in doc and v.get("id") == "LIN-2",
        OSError("boom"),
    )
    # the strict snapshot read fails; only that node, so the later state read succeeds
    message = _message()
    assert "Clôture Epic interrompue pour LIN-1" in message
    assert "erreur réseau" in message and "3 relecture(s)" in message
    assert "aucun audit provider" in message and "rien n'a été écrit" in message
    assert "le reçu provider permettra la reprise" not in message
    assert "Relance exactement `issue close-epic LIN-1 --human-verdict=accepted`" in message
    assert not _mutations(wire) and not intents.exists()


def test_interruption_after_audit_written_says_audit_exists_with_its_id(linear):
    tracker, wire, _project, _intents = linear
    _fail_when(
        tracker, wire,
        lambda doc, v: "FoundryLinearIssueUpdate" in doc and v.get("id") == wire.issues["LIN-1"]["id"],
        OSError("state write lost"),
    )
    message = _message()
    audit_id = write.epic_closure_state(tracker, "LIN-1").audit_id
    assert audit_id and audit_id.startswith("linear:epic:") and len(_audits(wire)) == 1
    assert f"audit provider {audit_id} présent" in message
    assert "clôture en attente" in message and "Relance exactement" in message
    assert "rien n'a été écrit" not in message
    # the identical command then resumes (no second audit) and closes
    tracker._transport = wire
    issue_cli.close_epic("LIN-1", flags=set(FLAGS))
    assert len(_audits(wire)) == 1


def test_interruption_with_closed_epic_says_closed_and_replay_verifies(linear, monkeypatch):
    tracker, wire, _project, _intents = linear
    issue_cli.close_epic("LIN-1", flags=set(FLAGS))
    audit_id = _audits(wire) and write.epic_closure_state(tracker, "LIN-1").audit_id

    def interrupted(*_a, **_k):
        raise RuntimeError("late read failed")

    monkeypatch.setattr(issue_cli.write, "close_epic", interrupted)
    message = _message()
    assert "RuntimeError: late read failed" in message
    assert f"audit provider {audit_id} présent et Epic done" in message
    assert "aucun second audit" in message


def test_intent_without_visible_audit_is_ambiguous_and_demands_the_same_command(linear):
    tracker, wire, _project, intents = linear
    original = wire.__call__

    def hidden(document, variables):
        result = original(document, variables)
        if "FoundryLinearCommentCreate" in document:
            wire.issues["LIN-1"]["comments"]["nodes"].clear()
            wire.comments.clear()
            raise OSError("effect invisible")
        return result

    tracker._transport = hidden
    message = _message()
    assert list(intents.glob("*.json"))
    assert "intention locale présente (pending" in message
    assert "aucun audit visible" in message and "effet ambigu" in message
    assert "ne poste jamais deux audits" in message and "serait refusée" in message
    assert "rien n'a été écrit" not in message
    assert "Relance exactement la même commande" in message


def test_quota_names_remaining_and_reset_and_does_not_advise_rerun_now(linear):
    tracker, wire, _project, _intents = linear
    original = tracker._graphql_once

    def once(document, variables, operation):
        if variables.get("id") == "LIN-2":
            raise LinearQuotaExhaustedError(operation, 429, 0, RESET_MS)
        return original(document, variables, operation)

    tracker._graphql_once = once
    message = _message()
    assert "quota Linear épuisé" in message and "restant 0" in message
    assert "2027-01-15T08:00:00Z" in message
    assert "ne relance pas maintenant" in message
    assert "Après la réinitialisation, relance exactement" in message
    assert "\nÉtat : aucun audit provider" in message and "Relance exactement" not in message


def test_state_unreadable_says_unknown_and_never_claims_safety(linear):
    tracker, wire, _project, _intents = linear
    _fail_when(
        tracker, wire, lambda doc, v: "FoundryLinearIssue" in doc,
        OSError("down"),
    )
    message = _message()
    assert "état du reçu inconnu: lecture impossible" in message.lower().replace(
        "État", "état")
    assert "Vérifie les commentaires de l'Epic" in message
    assert "c'est sûr" not in message and "Relance exactement" not in message


def test_a_non_provider_error_shows_type_and_message_but_never_a_token(linear, monkeypatch):
    def boom(*_a, **_k):
        raise KeyError("Authorization: Bearer lin_api_SECRET123 and token=abc")

    monkeypatch.setattr(issue_cli.write, "close_epic", boom)
    message = _message()
    assert "KeyError" in message
    assert "SECRET123" not in message and "token=abc" not in message


def test_conflict_refusal_keeps_its_wording_and_adds_state(linear, monkeypatch):
    def conflict(*_a, **_k):
        raise TrackerConflictError("graphe Epic divergent")

    monkeypatch.setattr(issue_cli.write, "close_epic", conflict)
    message = _message()
    assert "Clôture Epic refusée. Cause : graphe Epic divergent" in message
    assert "a changé" in message and "aucun audit provider" in message


# --- AC3: a read error during the strict snapshot is intercepted like a conflict ----

def test_snapshot_read_error_is_intercepted_like_a_conflict_with_its_cause(linear):
    tracker, wire, project, _intents = linear
    original = tracker._graphql_once

    def once(document, variables, operation):
        if variables.get("id") == "LIN-2":
            raise LinearTrackerError(operation, 503, "http_error")
        return original(document, variables, operation)

    tracker._graphql_once = once
    parent = tracker.get_issue("LIN-1")
    with pytest.raises(TrackerConflictError, match="lecture du graphe Epic impossible") as raised:
        write._snapshot_with_diagnostic(tracker, project, parent, frozenset())
    assert isinstance(raised.value.__cause__, LinearTrackerError)
    assert raised.value.__cause__.status == 503


def test_programming_errors_in_the_snapshot_are_not_swallowed(linear, monkeypatch):
    tracker, _wire, project, _intents = linear
    parent = tracker.get_issue("LIN-1")

    def bad(*_a, **_k):
        raise TypeError("bug")

    monkeypatch.setattr(write, "_bounded_epic_graph_snapshot", bad)
    with pytest.raises(TypeError):
        write._snapshot_with_diagnostic(tracker, project, parent, frozenset())


# --- AC4: read-only status ----------------------------------------------------

def test_status_without_audit_says_aucun_audit_and_writes_nothing(linear, capsys):
    tracker, wire, _project, intents = linear
    before = len(_mutations(wire))
    issue_cli.close_epic("LIN-1", flags={"--status"})
    assert capsys.readouterr().out.startswith("aucun audit")
    assert len(_mutations(wire)) == before and not _audits(wire) and not intents.exists()


def test_status_pending_then_closed_with_audit_id_and_no_write(linear, capsys):
    tracker, wire, _project, _intents = linear
    _fail_when(
        tracker, wire,
        lambda doc, v: "FoundryLinearIssueUpdate" in doc and v.get("id") == wire.issues["LIN-1"]["id"],
        OSError("state write lost"),
    )
    _message()
    tracker._transport = wire
    before = len(_mutations(wire))
    issue_cli.close_epic("LIN-1", flags={"--status"})
    out = capsys.readouterr().out
    audit_id = write.epic_closure_state(tracker, "LIN-1").audit_id
    assert out.startswith("audit en attente") and audit_id in out
    assert len(_mutations(wire)) == before

    issue_cli.close_epic("LIN-1", flags=set(FLAGS))
    capsys.readouterr()
    before = len(_mutations(wire))
    issue_cli.close_epic("LIN-1", flags={"--status"})
    out = capsys.readouterr().out
    assert out.startswith("clos") and audit_id in out and "dérogations" in out
    assert len(_mutations(wire)) == before and len(_audits(wire)) == 1


def test_status_reports_waived_nodes_bound_by_the_receipt(monkeypatch, capsys):
    from test_epic_override_waiver import _close, _graph

    tracker, wire, _project = _graph(monkeypatch)
    monkeypatch.setattr(issue_cli.foundry, "tracker", lambda: tracker)
    _close(tracker)
    issue_cli.close_epic("LIN-1", flags={"--status"})
    out = capsys.readouterr().out
    assert out.startswith("clos") and "dérogations liées au reçu : LIN-2" in out


def test_status_local_intent_without_audit_is_reported_without_creating_anything(
    linear, capsys,
):
    tracker, wire, _project, intents = linear
    original = wire.__call__

    def hidden(document, variables):
        result = original(document, variables)
        if "FoundryLinearCommentCreate" in document:
            wire.issues["LIN-1"]["comments"]["nodes"].clear()
            wire.comments.clear()
            raise OSError("effect invisible")
        return result

    tracker._transport = hidden
    _message()
    files = sorted(intents.glob("*.json"))
    contents = [f.read_text() for f in files]
    issue_cli.close_epic("LIN-1", flags={"--status"})
    out = capsys.readouterr().out
    assert out.startswith("aucun audit") and "intention locale pending" in out
    assert sorted(intents.glob("*.json")) == files
    assert [f.read_text() for f in files] == contents


def test_status_unreadable_exits_non_zero_without_claiming_a_state(linear):
    tracker, wire, _project, intents = linear
    _fail_when(tracker, wire, lambda doc, v: True, OSError("down"))
    with pytest.raises(SystemExit) as raised:
        issue_cli.close_epic("LIN-1", flags={"--status"})
    assert isinstance(raised.value.code, str)  # non-zero exit with a message
    assert "illisible" in str(raised.value) and "inconnu" in str(raised.value)
    assert not intents.exists()


def test_status_refuses_other_flags_and_unsupported_trackers(linear, monkeypatch):
    with pytest.raises(SystemExit, match="aucun autre flag"):
        issue_cli.close_epic("LIN-1", flags={"--status", "--human-verdict=accepted"})
    monkeypatch.setattr(
        issue_cli.foundry, "tracker", lambda: type("Plain", (), {"name": "plain"})(),
    )
    with pytest.raises(SystemExit, match="indisponible"):
        issue_cli.close_epic("LIN-1", flags={"--status"})


def test_state_check_is_cheap_one_provider_read_and_never_writes(linear):
    tracker, wire, _project, intents = linear
    before = len(wire.calls)
    state = write.epic_closure_state(tracker, "LIN-1")
    assert state.kind == "none" and len(wire.calls) - before <= 3
    assert not _mutations(wire) and not intents.exists()


def test_local_validation_refusal_costs_no_provider_call(linear):
    _tracker, wire, _project, _intents = linear
    before = len(wire.calls)
    with pytest.raises(SystemExit):
        issue_cli.close_epic("LIN-1", flags={"--accept-override=LIN-2"})
    assert len(wire.calls) == before
