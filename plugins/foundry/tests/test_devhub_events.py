import hashlib
import json

import pytest

from foundry.devhub_events import PassiveEventPublisher


def command(**overrides):
    value = {"id": "command-1", "version": 7, "lease": {"id": "lease-1"}, "next_event_sequence": 1}
    value.update(overrides)
    return value


class Transport:
    def __init__(self, *, fail=False, reconciliation=None):
        self.fail = fail
        self.reconciliation = reconciliation or {
            "command": {
                "version": 7, "next_event_sequence": 1,
                "projection_stale": False, "observation_status": "current",
            },
            "events": [],
        }
        self.calls = []
        self.version = 7
        self.next_sequence = 1

    def append_event(self, command_id, expected_version, event):
        self.calls.append((command_id, expected_version, event))
        if self.fail:
            raise OSError("offline")
        self.version = expected_version
        self.next_sequence = event["sequence"] + 1
        return {"accepted": True, "observation": {
            "id": event["observation_id"], "sequence": event["sequence"],
        }}

    def reconcile(self, command_id):
        self.calls.append(("reconcile", command_id))
        return self.reconciliation


def append_calls(transport):
    return [call for call in transport.calls if len(call) == 3]


def receipts():
    return {
        "pr": {"provider": "github", "repository": "patobiskoto/claude-plugins", "number": 88,
               "state": "merged", "head_sha": "a" * 40},
        "review": {"provider": "github", "repository": "patobiskoto/claude-plugins", "pr_number": 88,
                   "verdict": "approved", "review_id": "review-1", "head_sha": "a" * 40},
        "ci": {"provider": "github", "repository": "patobiskoto/claude-plugins", "status": "success",
               "run_id": "run-1", "head_sha": "a" * 40},
        "merge": {"provider": "github", "repository": "patobiskoto/claude-plugins", "pr_number": 88,
                  "merged": True, "merge_sha": "b" * 40},
        "epic_closure": {"epic_id": "FOUNDRY-88", "state": "closed", "receipt_id": "closure-1"},
    }


def complete_epic_closure(children_count):
    closure = {
        "epic_id": "FOUNDRY-88", "state": "closed",
        "receipt_id": "closure-1", "parent_version": 9,
        "children": sorted((
            {"issue_id": f"FOUNDRY-{index}", "version": 2, "state": "done"}
            for index in range(100, 100 + children_count)
        ), key=lambda child: child["issue_id"]),
    }
    closure["closure_digest"] = hashlib.sha256(json.dumps({
        "contract": "devhub-foundry-epic-closure.v1", **closure,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return closure


def test_extended_event_is_sequenced_idempotent_with_exact_provenance_receipts_and_nullable_metrics(tmp_path):
    transport = Transport()
    publisher = PassiveEventPublisher(tmp_path, transport)
    observed_command = command()
    metrics = {"input_tokens": 10, "cached_input_tokens": None, "output_tokens": 5,
               "reasoning_tokens": None, "tool_tokens": 2, "cost_cents": 0, "duration_ms": None}
    first = publisher.publish(command=observed_command, attempt_id="attempt-1", issue_id="FOUNDRY-88", event_type="completed", occurred_at=1, metrics=metrics, receipts=receipts())
    second = publisher.publish(command=observed_command, attempt_id="attempt-1", issue_id="FOUNDRY-88", event_type="completed", occurred_at=1, metrics=metrics, receipts=receipts())

    assert first.status == second.status == "delivered"
    assert first.event_id == second.event_id
    assert [call[2]["sequence"] for call in append_calls(transport)] == [1]
    wire = append_calls(transport)[0][2]
    assert wire == {
        "schema_version": "devhub-foundry-observation.v1", "observation_id": first.event_id,
        "sequence": 1, "type": "completed", "observed_at": 1,
        "provenance": {"command_id": "command-1", "attempt_id": "attempt-1", "issue_id": "FOUNDRY-88", "source": "foundry"},
        "receipts": receipts(), "telemetry": metrics,
    }
    assert observed_command == command()  # publishing records an observed fact only


def test_receipts_and_metrics_are_closed_and_unavailable_values_remain_null(tmp_path):
    transport = Transport()
    publisher = PassiveEventPublisher(tmp_path, transport)
    with pytest.raises(ValueError, match="unknown metric"):
        publisher.publish(command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88", event_type="completed", occurred_at=1, metrics={"total_tokens": 1})
    with pytest.raises(ValueError, match="metric input_tokens"):
        publisher.publish(command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88", event_type="completed", occurred_at=1, metrics={"input_tokens": 1_000_000_001})
    with pytest.raises(ValueError, match="invalid pr receipt"):
        publisher.publish(command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88", event_type="completed", occurred_at=1, receipts={"pr": {"provider": "github", "repository": "example/repo", "number": 1, "state": "open", "merge_sha": "a" * 40}})
    result = publisher.publish(command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88", event_type="completed", occurred_at=1)
    assert result.status == "delivered"
    assert append_calls(transport)[0][2]["telemetry"] == {
        "input_tokens": None, "cached_input_tokens": None, "output_tokens": None,
        "reasoning_tokens": None, "tool_tokens": None, "cost_cents": None, "duration_ms": None,
    }
    assert append_calls(transport)[0][2]["receipts"] == {key: None for key in ("pr", "review", "ci", "merge", "epic_closure")}


@pytest.mark.parametrize(
    ("children_count", "accepted"),
    ((50, True), (51, True), (100, True), (101, False)),
)
def test_passive_closure_receipt_uses_shared_child_cap(
    tmp_path, children_count, accepted,
):
    transport = Transport()
    publisher = PassiveEventPublisher(tmp_path, transport)
    evidence = receipts()
    evidence["epic_closure"] = complete_epic_closure(children_count)

    if accepted:
        result = publisher.publish(
            command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88",
            event_type="completed", occurred_at=1, receipts=evidence,
        )
        assert result.status == "delivered"
        assert len(append_calls(transport)[0][2]["receipts"]["epic_closure"]["children"]) == children_count
    else:
        with pytest.raises(ValueError, match="invalid epic_closure receipt"):
            publisher.publish(
                command=command(), attempt_id="attempt-1",
                issue_id="FOUNDRY-88", event_type="completed",
                occurred_at=1, receipts=evidence,
            )
        assert append_calls(transport) == []


def test_f91_optional_null_fields_are_omitted_without_losing_the_available_receipt(tmp_path):
    transport = Transport()
    publisher = PassiveEventPublisher(tmp_path, transport)

    result = publisher.publish(
        command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88",
        event_type="completed", occurred_at=1,
        receipts={
            "ci": {
                "provider": "github", "repository": "patobiskoto/claude-plugins",
                "url": None, "status": "success", "run_id": None,
                "head_sha": "a" * 40,
            },
        },
    )

    assert result.status == "delivered"
    assert append_calls(transport)[0][2]["receipts"]["ci"] == {
        "provider": "github", "repository": "patobiskoto/claude-plugins",
        "status": "success", "head_sha": "a" * 40,
    }


def test_fresh_spool_uses_the_remote_observation_head_before_allocating_a_sequence(tmp_path):
    transport = Transport(reconciliation={
        "command": {
            "version": 9, "next_event_sequence": 3,
            "projection_stale": False, "observation_status": "current",
        },
        "events": [
            {"event_id": "remote-1", "sequence": 1},
            {"event_id": "remote-2", "sequence": 2},
        ],
    })
    publisher = PassiveEventPublisher(tmp_path, transport)

    result = publisher.publish(
        command=command(version=7), attempt_id="attempt-3", issue_id="FOUNDRY-88",
        event_type="completed", occurred_at=3,
    )

    assert result.status == "delivered"
    assert [(call[1], call[2]["sequence"]) for call in append_calls(transport)] == [(9, 3)]


def test_outage_buffers_losslessly_then_replays_without_logical_duplicate(tmp_path):
    transport = Transport(fail=True)
    publisher = PassiveEventPublisher(tmp_path, transport, retries=2)
    result = publisher.publish(command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88", event_type="running", occurred_at=1)
    assert result.status == "buffered"
    assert len(append_calls(transport)) == 2

    transport.fail = False
    flushed = publisher.flush("command-1")
    assert flushed.status == "delivered"
    assert append_calls(transport)[-1][2]["observation_id"] == result.event_id
    state = json.loads((tmp_path / "pending.json").read_text())
    assert state["pending"] == []
    assert result.event_id in state["delivered"]


def test_pending_versions_rebase_after_prior_delivery_and_heartbeat(tmp_path):
    transport = Transport(fail=True)
    publisher = PassiveEventPublisher(tmp_path, transport, retries=1)
    first = publisher.publish(
        command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88",
        event_type="running", occurred_at=1,
    )
    second = publisher.publish(
        command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88",
        event_type="completed", occurred_at=2,
    )
    assert (first.status, second.status) == ("buffered", "buffered")

    transport.fail = False
    assert publisher.flush("command-1").status == "delivered"
    delivered_calls = [call for call in transport.calls if len(call) == 3][-2:]
    assert [(call[1], call[2]["sequence"]) for call in delivered_calls] == [(7, 1), (7, 2)]

    # A heartbeat can advance the version while leaving the pending sequence
    # unchanged. Re-publishing the exact fact rebases only that version.
    heartbeat_transport = Transport(fail=True)
    heartbeat_publisher = PassiveEventPublisher(tmp_path / "heartbeat", heartbeat_transport, retries=1)
    pending = heartbeat_publisher.publish(
        command=command(), attempt_id="attempt-2", issue_id="FOUNDRY-88",
        event_type="running", occurred_at=3,
    )
    assert pending.status == "buffered"
    heartbeat_transport.fail = False
    replayed = heartbeat_publisher.publish(
        command=command(version=8), attempt_id="attempt-2", issue_id="FOUNDRY-88",
        event_type="running", occurred_at=3,
    )
    assert replayed.status == "delivered"
    assert heartbeat_transport.calls[-1][1] == 8


def test_bounded_spool_reports_backpressure_without_dropping_a_new_fact(tmp_path):
    transport = Transport(fail=True)
    publisher = PassiveEventPublisher(tmp_path, transport, max_pending=1, retries=1)
    assert publisher.publish(command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88", event_type="running", occurred_at=1).status == "buffered"
    assert publisher.publish(command=command(), attempt_id="attempt-2", issue_id="FOUNDRY-88", event_type="running", occurred_at=2).status == "backpressured"
    pending = (tmp_path / "pending.json").read_text()
    assert '"backpressure"' in pending
    assert '"attempt_id":"attempt-2"' in pending


def test_gap_or_rejection_marks_only_projection_stale_and_never_success(tmp_path):
    transport = Transport(fail=True, reconciliation={
        "command": {
            "version": 7, "next_event_sequence": 2, "projection_stale": True,
            "observation_status": "unknown",
        },
        "events": [],
    })
    publisher = PassiveEventPublisher(tmp_path, transport, retries=1)
    result = publisher.publish(command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88", event_type="completed", occurred_at=1)
    assert result.status == "stale"
    assert publisher.flush("command-1").status == "stale"
    assert (tmp_path / "pending.json").exists()


def test_explicit_devhub_rejection_marks_the_local_projection_stale(tmp_path):
    class RejectingTransport(Transport):
        def append_event(self, command_id, expected_version, event):
            self.calls.append((command_id, expected_version, event))
            return {"accepted": False, "error": {"code": "invalid_request"}}

    transport = RejectingTransport()
    publisher = PassiveEventPublisher(tmp_path, transport)
    result = publisher.publish(command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88", event_type="completed", occurred_at=1)
    assert result.status == "stale"
    assert [call[0] for call in append_calls(transport)] == ["command-1"]
    assert publisher.flush("command-1").status == "stale"


def test_accepted_response_with_stale_or_wrong_sequence_facts_is_not_acknowledged(tmp_path):
    class StaleResponseTransport(Transport):
        def append_event(self, command_id, expected_version, event):
            self.calls.append((command_id, expected_version, event))
            return {"accepted": True, "command": {
                "version": expected_version + 1,
                "next_event_sequence": event["sequence"] + 2,
                "projection_stale": True,
            }}

    transport = StaleResponseTransport()
    publisher = PassiveEventPublisher(tmp_path, transport)
    result = publisher.publish(
        command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88",
        event_type="completed", occurred_at=1,
    )

    assert result.status == "stale"
    state = json.loads((tmp_path / "pending.json").read_text())
    assert state["stale"] == {"command-1": True}
    assert [row["event_id"] for row in state["pending"]] == [result.event_id]


def test_reconcile_acknowledges_only_the_same_event_identity_and_sequence(tmp_path):
    transport = Transport(fail=True, reconciliation={
        "command": {
            "version": 7, "next_event_sequence": 2, "projection_stale": False,
            "observation_status": "current",
        },
        "events": [],
    })
    publisher = PassiveEventPublisher(tmp_path, transport, retries=1)
    buffered = publisher.publish(command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88", event_type="running", occurred_at=1)
    assert buffered.status == "stale"  # a gap without identity is not success

    # A new spool illustrates acknowledgement after a response-loss replay.
    transport = Transport(fail=True)
    publisher = PassiveEventPublisher(tmp_path / "two", transport, retries=1)
    buffered = publisher.publish(command=command(), attempt_id="attempt-1", issue_id="FOUNDRY-88", event_type="running", occurred_at=1)
    transport.reconciliation = {
        "command": {
            "version": 8, "next_event_sequence": 2, "projection_stale": False,
            "observation_status": "current",
        },
        "events": [{"event_id": buffered.event_id, "sequence": 1}],
    }
    assert publisher.flush("command-1").status == "delivered"


def test_legacy_spool_is_upgraded_to_the_extended_wire_without_losing_the_pending_fact(tmp_path):
    pending = {
        "schema": "foundry-devhub-passive-events.v1",
        "pending": [{
            "command_id": "command-1", "attempt_id": "attempt-1", "issue_id": "FOUNDRY-88",
            "type": "running", "occurred_at": 1, "metrics": {"cost_cents": None, "duration_ms": 10},
            "receipts": {"pr": None, "review": None, "ci": None, "merge": None, "epic_closure": None},
            "event_id": "legacy-event-1", "sequence": 1, "command_version": 7, "lease_id": "lease-1",
        }], "stale": {},
    }
    (tmp_path / "pending.json").write_text(json.dumps(pending), encoding="utf-8")
    transport = Transport()
    publisher = PassiveEventPublisher(tmp_path, transport)
    assert publisher.flush("command-1").status == "delivered"
    wire = append_calls(transport)[0][2]
    assert wire["schema_version"] == "devhub-foundry-observation.v1"
    assert wire["telemetry"]["duration_ms"] == 10
    assert all(wire["telemetry"][key] is None for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "tool_tokens", "cost_cents"))
