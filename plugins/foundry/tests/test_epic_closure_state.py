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


def test_intent_without_visible_audit_is_ambiguous_and_says_a_rerun_is_refused(linear):
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
    # N1: a re-run is REFUSED until the audit is visible; it never claims a repost
    assert "REFUSÉE (no second POST)" in message and "ne reposte jamais" in message
    assert "tant que l'audit attendu n'est pas visible" in message
    assert "intervention manuelle" in message and "--status" in message
    assert "reprend depuis l'intention exacte" not in message
    assert "rien n'a été écrit" not in message and "Relance exactement" not in message


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


# --- PAT-100 review B1: only a READ and empty intent store may claim "nothing written" ---

from foundry.models import Project  # noqa: E402

from test_epic_closure_transports import _YouTrackWire  # noqa: E402

YT_COORDS = {"issued_at": 1_800_000_000_000, "nonce": "nonce_1234567890abcdef"}


@pytest.fixture
def youtrack(monkeypatch, tmp_path):
    tracker = _YouTrackWire()
    project = Project(key="YT", id="p")
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    monkeypatch.setattr(issue_cli.foundry, "tracker", lambda: tracker)
    return tracker, project, tmp_path / "youtrack-epic-closure-intents"


def _yt_hide_audit_effect(tracker):
    original = tracker._req

    def hidden(method, path, body=None, fields=None, top=None):
        result = original(method, path, body, fields, top)
        if method == "POST" and path == "/issues/YT-1/comments":
            tracker.rows["YT-1"]["comments"].clear()
            raise OSError("comment response lost and effect not observable")
        return result

    tracker._req = hidden


def _yt_message(*flags):
    with pytest.raises(SystemExit) as raised:
        issue_cli.close_epic("YT-1", flags=set(flags or FLAGS))
    return str(raised.value)


def test_youtrack_intent_present_without_visible_audit_never_claims_nothing_written(youtrack):
    tracker, _project, intents = youtrack
    _yt_hide_audit_effect(tracker)
    message = _yt_message()
    assert list(intents.glob("*.json"))
    assert "intention locale présente (pending" in message
    assert "rien n'a été écrit" not in message and "c'est sûr" not in message
    assert "aucune intention locale" not in message
    assert "REFUSÉE (no second POST)" in message


def test_youtrack_status_reports_the_intent_and_never_plain_aucun_audit(youtrack, capsys):
    tracker, _project, intents = youtrack
    _yt_hide_audit_effect(tracker)
    _yt_message()
    files = sorted(intents.glob("*"))
    contents = [f.read_text() for f in files]
    issue_cli.close_epic("YT-1", flags={"--status"})
    out = capsys.readouterr().out
    assert out.startswith("aucun audit") and "intention locale pending" in out
    assert sorted(intents.glob("*")) == files and [f.read_text() for f in files] == contents


def test_youtrack_empty_store_read_keeps_state_none_and_creates_nothing(youtrack, capsys):
    tracker, _project, intents = youtrack
    state = write.epic_closure_state(tracker, "YT-1")
    assert state.kind == "none" and state.intent is None
    issue_cli.close_epic("YT-1", flags={"--status"})
    assert capsys.readouterr().out.startswith("aucun audit")
    assert not intents.exists()  # no mkdir, no lock file
    assert not [c for c in tracker.calls if c[0] == "POST"]


def test_unreadable_youtrack_journal_is_unknown_not_safe(youtrack):
    tracker, _project, intents = youtrack
    intents.mkdir(parents=True)
    fingerprint = tracker._epic_closure_scope_fingerprint(Project(key="YT", id="p"), "YT-1")
    (intents / f"{fingerprint}.json").write_text("{not json")
    state = write.epic_closure_state(tracker, "YT-1")
    assert state.kind == "unknown" and state.intent == "unreadable"


def test_tracker_whose_intent_store_is_not_read_is_unknown_never_safe(
    youtrack, monkeypatch, capsys,
):
    tracker, _project, intents = youtrack
    tracker.read_epic_closure_intent = None  # e.g. DevHub / unknown tracker: store not read
    state = write.epic_closure_state(tracker, "YT-1")
    assert state.kind == "unknown" and state.intent == "unverified"
    monkeypatch.setattr(
        issue_cli.write, "close_epic",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("late read failed")),
    )
    message = _yt_message()
    assert "intention locale non vérifiée pour ce tracker" in message
    assert "rien n'a été écrit" not in message and "c'est sûr" not in message
    assert "aucune intention locale" not in message and "Relance exactement" not in message
    with pytest.raises(SystemExit) as raised:
        issue_cli.close_epic("YT-1", flags={"--status"})
    assert "non vérifiée" in str(raised.value)
    assert capsys.readouterr().out == ""
    assert not intents.exists()


# --- N2: no contradictory advice after a refusal -----------------------------------

def test_refusal_on_a_different_waived_set_does_not_advise_the_refused_command(
    monkeypatch,
):
    from test_epic_override_waiver import _close, _graph

    tracker, _wire, _project = _graph(monkeypatch)
    monkeypatch.setattr(issue_cli.foundry, "tracker", lambda: tracker)
    _close(tracker)
    message = _message()  # refused set: none, audit binds LIN-2
    assert "ensemble --accept-override différent du reçu" in message
    assert "Relance exactement `issue close-epic LIN-1 --human-verdict=accepted`" not in message
    assert "Ne relance pas la commande refusée" in message
    assert ("relance avec `issue close-epic LIN-1 --human-verdict=accepted "
            "--accept-override=LIN-2`") in message
    assert "issue close-epic LIN-1 --status" in message


def test_valid_command_for_the_observed_state_keeps_relance_exactement(linear, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("x")

    monkeypatch.setattr(issue_cli.write, "close_epic", boom)
    assert "Relance exactement `issue close-epic LIN-1 --human-verdict=accepted`" in _message()


def test_graph_divergence_refusal_with_audit_never_advises_the_same_command(
    linear, monkeypatch,
):
    tracker, wire, _project, _intents = linear
    _fail_when(
        tracker, wire,
        lambda doc, v: "FoundryLinearIssueUpdate" in doc and v.get("id") == wire.issues["LIN-1"]["id"],
        OSError("state write lost"),
    )
    _message()
    tracker._transport = wire
    monkeypatch.setattr(
        issue_cli.write, "close_epic",
        lambda *_a, **_k: (_ for _ in ()).throw(
            TrackerConflictError("graphe Epic divergent depuis l'audit pending")),
    )
    message = _message()
    assert "Ne relance pas la commande refusée telle quelle" in message
    assert "Relance exactement" not in message


# --- N6: quota advice survives an unreadable state ----------------------------------

def test_quota_with_unreadable_state_still_says_do_not_rerun_now(linear):
    tracker, _wire, _project, _intents = linear

    def quota(*_a, **_k):
        raise LinearQuotaExhaustedError("issue.read", 429, 0, RESET_MS)

    tracker._graphql_once = quota
    message = _message()
    assert "quota Linear épuisé" in message and "État du reçu inconnu" in message
    assert "ne relance pas maintenant" in message
    assert "c'est sûr" not in message


# --- N7: BaseException is not swallowed; `from None` hides the context ---------------

def test_state_read_does_not_swallow_keyboard_interrupt(linear):
    tracker, _wire, _project, _intents = linear

    def interrupted(*_a, **_k):
        raise KeyboardInterrupt

    tracker.read_epic_closure_state_with_id = interrupted
    with pytest.raises(KeyboardInterrupt):
        write.epic_closure_state(tracker, "LIN-1")


def test_provider_cause_honours_suppress_context():
    try:
        try:
            raise LinearTrackerError("op", None, "transport_error")
        except LinearTrackerError:
            raise TrackerConflictError("conflict") from None
    except TrackerConflictError as exc:
        assert issue_cli._provider_cause(exc) is None
    try:
        try:
            raise LinearTrackerError("op", None, "transport_error")
        except LinearTrackerError:
            raise TrackerConflictError("conflict")
    except TrackerConflictError as exc:
        assert issue_cli._provider_cause(exc) is not None


# --- N3: redaction independent of the lin_api_ prefix ---------------------------------

@pytest.mark.parametrize("text,secret", [
    ("Authorization: Bearer abc123", "abc123"),
    ("authorization=Basic dXNlcjpwdw==", "dXNlcjpwdw"),
    ("bearer abc123", "abc123"),
    ("failed gho_AbC123xyz", "AbC123xyz"),
    ("failed ghs_AbC123xyz", "AbC123xyz"),
    ("failed ghu_AbC123xyz", "AbC123xyz"),
    ("failed ghp_AbC123xyz", "AbC123xyz"),
    ("failed github_pat_11ABC_xyz987", "xyz987"),
    ("youtrack perm:YWRtaW4=.NDQtMQ==.tok3n", "tok3n"),
    ("GET https://user:s3cret@host.example/x", "s3cret"),
    ("api_key=hunter2", "hunter2"),
])
def test_redact_covers_common_credential_shapes(text, secret):
    assert secret not in issue_cli._redact(text)


def test_redact_keeps_ordinary_text():
    assert issue_cli._redact("Linear issue.read http_error 503") == "Linear issue.read http_error 503"


# --- N8: the intent is looked up with the canonical id ---------------------------------

def test_intent_lookup_uses_the_canonical_id_returned_by_the_provider(monkeypatch):
    from foundry.trackers.epic_intent import EpicAuditIntent

    project = Project(key="LIN", id="proj")
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    audit = "linear:epic:" + "a" * 64
    intent = EpicAuditIntent("linear", project, "LIN-1")
    with intent.lock():
        intent.write(audit, "pending")

    class Stub:
        name = "linear"

        def read_epic_closure_state_with_id(self, _project, _parent_id):
            return None, False, "LIN-1"

    state = write.epic_closure_state(Stub(), "lin-1")  # raw argument is not canonical
    assert state.kind == "intent-only" and state.intent_audit_id == audit
