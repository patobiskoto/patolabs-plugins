from __future__ import annotations

import hashlib
import io
import json
import urllib.error
from dataclasses import replace

import pytest

from foundry.command_worker import (
    CommandRevalidationError,
    CommandJournalEventTransport,
    CommandWorker,
    CommandWorkerError,
    EffectReceipt,
    EffectiveLimits,
    ExecutionAuthorization,
    ExecutionProfile,
    HostReservationStore,
    PreEffectCapacityError,
    ReceiptStore,
    RevalidationObservation,
    SnapshotAdvancementObservation,
    UnlaunchedReservationProof,
    _binding_digest,
    revalidate_command,
)
from foundry.devhub_commands import (
    COMMAND_CONTRACT,
    COMMAND_EVENT_CONTRACT,
    OBSERVATION_CONTRACT,
    CommandLease,
    CommandPage,
    DevHubCommand,
    DevHubCommandClient,
    DevHubCommandError,
    DevHubCommandTransportError,
    parse_command,
    parse_event,
    parse_terminal_reconciliation,
)
from foundry.devhub_events import PassiveEventPublisher
from foundry.execution_receipts import ExecutionReceiptStore


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
FOUNDRY_ONLY_SUSPENSION_STATES = {
    "suspended-blocker", "suspended-review", "suspended-human",
    "suspended-host", "suspended-ambiguous", "suspended-gate",
    "suspended-budget", "suspended-policy",
}


def command(**overrides) -> DevHubCommand:
    value = DevHubCommand(
        id="command-1", project="DEVHUB", preview_id="preview-1",
        approval_id="approval-1", preview_digest=DIGEST_A,
        snapshot_digest=DIGEST_B, policy_digest=DIGEST_C,
        epic_id="DEVHUB-20", planning_version_id="DEVHUB-VERSION-1",
        max_cost_cents=500, max_concurrency=4, state="requested",
        projection_stale=False, next_event_sequence=1, scheduled_for=None,
        lease=None, version=3, created_at=100, updated_at=100,
    )
    return replace(value, **overrides)


def observation(**overrides) -> RevalidationObservation:
    value = RevalidationObservation(
        command_id="command-1", project="DEVHUB",
        preview_id="preview-1", approval_id="approval-1",
        approval_state="approved",
        epic_id="DEVHUB-20",
        epic_version=4,
        planning_version_id="DEVHUB-VERSION-1",
        preview_digest=DIGEST_A, snapshot_digest=DIGEST_B,
        policy_digest=DIGEST_C, preview_expires_at=20_000,
        approval_expires_at=19_000, approved_minimum_tier="balanced",
        current_minimum_tier="frontier", local_max_cost_cents=400,
        local_max_concurrency=3, budget_remaining_cents=300,
        active_concurrency=1, observed_at=9_000,
        valid_until=15_000, host_available=True,
        required_gates=frozenset({"tests", "independent-review", "human-test"}),
        provider_invocation_ceiling_cents=300,
    )
    return replace(value, **overrides)


def snapshot_advancement(**overrides) -> SnapshotAdvancementObservation:
    value = SnapshotAdvancementObservation(
        "command-1", "effect-parent-sync", _binding_digest(command()), DIGEST_B,
        4, 5, DIGEST_A, DIGEST_C,
    )
    return replace(value, **overrides)


def raw_command(**overrides):
    value = {
        "contract": COMMAND_CONTRACT, "id": "command-1", "project": "DEVHUB",
        "preview_id": "preview-1", "approval_id": "approval-1",
        "preview_digest": DIGEST_A, "snapshot_digest": DIGEST_B,
        "policy_digest": DIGEST_C, "epic_id": "DEVHUB-20",
        "planning_version_id": "DEVHUB-VERSION-1",
        "limits": {"max_cost_cents": 500, "max_concurrency": 4},
        "state": "requested", "projection_stale": False,
        "next_event_sequence": 1, "scheduled_for": None, "lease": None,
        "version": 3, "created_at": 100, "updated_at": 100,
    }
    value.update(overrides)
    return value


def test_command_contract_is_strict_and_bounded():
    assert parse_command(raw_command()).id == "command-1"
    for state in FOUNDRY_ONLY_SUSPENSION_STATES:
        with pytest.raises(DevHubCommandError, match="invalid_response"):
            parse_command(raw_command(state=state))
    with pytest.raises(DevHubCommandError, match="invalid_response"):
        parse_command({**raw_command(), "shell": "rm -rf"})
    with pytest.raises(DevHubCommandError, match="invalid_response"):
        parse_command(raw_command(limits={"max_cost_cents": 500, "max_concurrency": 17}))
    with pytest.raises(DevHubCommandError, match="invalid_response"):
        parse_command(raw_command(contract="devhub-tracker.v1"))


def extended_terminal_event(**overrides):
    closure = {
        "epic_id": "DEVHUB-20", "state": "closed", "receipt_id": "close-20",
        "parent_version": 6,
        "children": [{"issue_id": "DEVHUB-21", "version": 2, "state": "done"}],
    }
    closure["closure_digest"] = hashlib.sha256(json.dumps({
        "contract": "devhub-foundry-epic-closure.v1", **closure,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    value = {
        "contract": COMMAND_CONTRACT, "schema_version": COMMAND_EVENT_CONTRACT,
        "id": "event-terminal", "command_id": "command-1", "sequence": 3,
        "type": "completed", "actor": "foundry-worker", "lease_id": "lease-1",
        "occurred_at": 100, "received_at": 101,
        "provenance": {
            "command_id": "command-1", "attempt_id": "attempt-1",
            "issue_id": "DEVHUB-20", "source": "foundry",
        },
        "receipts": {
            "pr": None, "review": None, "ci": None, "merge": None,
            "epic_closure": closure,
        },
        "telemetry": {
            "input_tokens": None, "cached_input_tokens": None,
            "output_tokens": None, "reasoning_tokens": None,
            "tool_tokens": None, "cost_cents": None, "duration_ms": None,
        },
    }
    value.update(overrides)
    return value


def terminal_reconciliation(**overrides):
    value = {
        "contract": COMMAND_CONTRACT, "id": "reconcile-terminal",
        "command_id": "command-1", "terminal_event_id": "event-terminal",
        "epic_id": "DEVHUB-20", "preview_epic_version": 4,
        "reconciled_epic_version": 6, "closure_digest": DIGEST_A,
        "terminal_state": "completed",
        "economics": {"cost_cents": None, "duration_ms": None},
        "provider_effects": 0, "version": 1, "created_at": 102,
    }
    value.update(overrides)
    return value


def test_extended_terminal_event_and_reconciliation_are_parsed_strictly():
    event = parse_event(extended_terminal_event())
    assert event.schema_version == COMMAND_EVENT_CONTRACT
    assert event.receipts["epic_closure"]["parent_version"] == 6
    assert event.cost_cents is event.duration_ms is None
    receipt = parse_terminal_reconciliation(terminal_reconciliation())
    assert receipt.provider_effects == 0
    assert receipt.preview_epic_version == 4

    with pytest.raises(DevHubCommandError, match="invalid_response"):
        parse_event({**extended_terminal_event(), "restart_agent": True})
    with pytest.raises(DevHubCommandError, match="invalid_response"):
        parse_terminal_reconciliation(
            terminal_reconciliation(provider_effects=1),
        )
    with pytest.raises(DevHubCommandError, match="invalid_response"):
        parse_terminal_reconciliation(
            terminal_reconciliation(economics={"cost_cents": 1, "estimated": True}),
        )


@pytest.mark.parametrize("receipt_id", (None, "historical-closure-20"))
def test_extended_event_keeps_legacy_compact_closure_markers_readable(receipt_id):
    raw = extended_terminal_event()
    closure = {"epic_id": "DEVHUB-20", "state": "closed"}
    if receipt_id is not None:
        closure["receipt_id"] = receipt_id
    raw["receipts"]["epic_closure"] = closure

    assert parse_event(raw).receipts["epic_closure"] == closure


@pytest.mark.parametrize(
    ("children_count", "accepted"),
    ((50, True), (51, True), (100, True), (101, False)),
)
def test_terminal_event_closure_uses_the_shared_bounded_child_cap(
    children_count, accepted,
):
    raw = extended_terminal_event()
    closure = raw["receipts"]["epic_closure"]
    closure["children"] = sorted((
        {"issue_id": f"DEVHUB-{index}", "version": 2, "state": "done"}
        for index in range(21, 21 + children_count)
    ), key=lambda child: child["issue_id"])
    closure["closure_digest"] = hashlib.sha256(json.dumps({
        "contract": "devhub-foundry-epic-closure.v1",
        **{key: value for key, value in closure.items()
           if key != "closure_digest"},
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    if accepted:
        assert len(parse_event(raw).receipts["epic_closure"]["children"]) == children_count
    else:
        with pytest.raises(DevHubCommandError, match="invalid_response"):
            parse_event(raw)


def test_terminal_event_rejects_a_changed_closure_digest():
    raw = extended_terminal_event()
    raw["receipts"]["epic_closure"]["closure_digest"] = "f" * 64
    with pytest.raises(DevHubCommandError, match="invalid_response"):
        parse_event(raw)


@pytest.mark.parametrize(
    ("kind", "receipt"),
    (
        ("pr", {}),
        ("review", {"provider": "github", "repository": "example/devhub"}),
        ("ci", {
            "provider": "bitbucket", "repository": "example/devhub",
            "status": "success",
        }),
        ("ci", {
            "provider": "github", "repository": "example/devhub",
            "status": "green",
        }),
        ("merge", {
            "provider": "github", "repository": "example/devhub",
            "pr_number": 88, "merged": False,
        }),
        ("merge", {
            "provider": "github", "repository": "example/devhub",
            "pr_number": 88, "merged": True, "deployment_id": "deploy-1",
        }),
    ),
)
def test_extended_event_rejects_malformed_code_host_receipts(kind, receipt):
    raw = extended_terminal_event()
    raw["receipts"][kind] = receipt

    with pytest.raises(DevHubCommandError, match="invalid_response"):
        parse_event(raw)


def test_command_client_publishes_the_complete_closure_on_native_terminal_event(
    monkeypatch,
):
    client = DevHubCommandClient(url="https://devhub.example", token="x" * 24)
    calls = []
    terminal = extended_terminal_event()
    receipts = terminal["receipts"]

    def request(
        method, path, body=None, *, version=None, idempotency_key=None,
        expected_contract=COMMAND_CONTRACT,
    ):
        calls.append((method, path, body, version, idempotency_key, expected_contract))
        return raw_command(
            state="completed", version=4, next_event_sequence=4,
        )

    monkeypatch.setattr(client, "_request", request)
    result = client.append_event(
        command(), event_id="event-terminal", sequence=3,
        event_type="completed", occurred_at=100, lease_id="lease-1",
        cost_cents=None, duration_ms=None, attempt_id="attempt-1",
        issue_id="DEVHUB-20", receipts=receipts,
        idempotency_key="event-terminal-key",
    )

    assert result.state == "completed"
    body = calls[0][2]
    assert body == {
        "schema_version": COMMAND_EVENT_CONTRACT,
        "event_id": "event-terminal", "sequence": 3, "type": "completed",
        "occurred_at": 100, "lease_id": "lease-1",
        "provenance": terminal["provenance"],
        "receipts": receipts,
        "telemetry": terminal["telemetry"],
    }
    assert calls[0][0:2] == ("POST", "/commands/command-1/events")
    assert calls[0][3:] == (3, "event-terminal-key", COMMAND_CONTRACT)


def test_terminal_reconciliation_client_uses_version_and_deterministic_identity(monkeypatch):
    client = DevHubCommandClient(url="https://devhub.example", token="x" * 24)
    calls = []

    def request(method, path, body=None, *, version=None, idempotency_key=None, expected_contract=COMMAND_CONTRACT):
        calls.append((method, path, body, version, idempotency_key, expected_contract))
        return terminal_reconciliation(id="reconcile-terminal")

    monkeypatch.setattr(client, "_request", request)
    receipt = client.reconcile_terminal(
        command(state="completed", version=9),
        reconciliation_id="reconcile-terminal",
        terminal_event_id="event-terminal", epic_version=6,
    )

    assert receipt.provider_effects == 0
    assert calls == [(
        "POST", "/commands/command-1/terminal-reconciliations",
        {
            "reconciliation_id": "reconcile-terminal",
            "terminal_event_id": "event-terminal", "epic_version": 6,
        },
        9, "reconcile-terminal", COMMAND_CONTRACT,
    )]


@pytest.mark.parametrize(
    ("preview_version", "corruption"),
    (
        (4, None), (5, None), (4, "command"), (4, "digest"),
        (4, "cost"), (4, "event"), (3, "preview"),
    ),
)
def test_late_terminal_client_exact_transport_retry_and_response_binding(
    monkeypatch, preview_version, corruption,
):
    client = DevHubCommandClient(url="https://devhub.example", token="x" * 24)
    original = {
        "schema_version": "devhub-epic-closure.v1",
        "outcome": {
            "receipt": {
                "project_key": "DEVHUB", "project_id": "1", "parent_id": "DEVHUB-20",
                "parent_version": 5, "parent_type": "Epic", "parent_ac_done": 1,
                "parent_ac_total": 1, "children": [{"id": "DEVHUB-21", "version": 2, "state": "done"}],
                "issued_at": 100, "nonce": "nonce_1234567890abcdef",
            },
            "closed_parent_version": 6, "audit_id": "42", "replayed": True,
        },
    }
    closure = {
        "contract": "devhub-foundry-epic-closure.v1", "epic_id": "DEVHUB-20",
        "state": "closed", "receipt_id": "42", "parent_version": 6,
        "children": [{"issue_id": "DEVHUB-21", "version": 2, "state": "done"}],
    }
    digest = hashlib.sha256(json.dumps(closure, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result = terminal_reconciliation(
        id="late-terminal", closure_digest=digest,
        preview_epic_version=preview_version,
    )
    if corruption == "command":
        result["command_id"] = "command-other"
    elif corruption == "digest":
        result["closure_digest"] = "0" * 64
    elif corruption == "cost":
        result["economics"]["cost_cents"] = 1
    elif corruption == "event":
        result["terminal_event_id"] = "event-other"
    calls = []

    def request(*args, **kwargs):
        calls.append(json.loads(json.dumps([args, kwargs])))
        if len(calls) == 1:
            raise DevHubCommandTransportError("POST", "late", None, "transport_error")
        return result

    monkeypatch.setattr(client, "_request", request)

    def publish():
        return client.publish_late_terminal(
            command(state="running", lease=CommandLease("lease-1", "worker-1", 200, 100)),
            publication_id="late-terminal", event_id="event-terminal", sequence=1,
            occurred_at=100, lease_id="lease-1", attempt_id="attempt-1",
            original_closure=original, cost_cents=None, duration_ms=None,
        )

    if corruption:
        with pytest.raises(DevHubCommandError, match="binding_mismatch"):
            publish()
    else:
        assert publish().provider_effects == 0
    assert len(calls) == 2 and calls[0] == calls[1]
    assert calls[0][0][:2] == ["POST", "/commands/command-1/late-terminal-publications"]
    assert calls[0][1] == {"version": 3, "idempotency_key": "late-terminal"}


def test_passive_transport_uses_the_observation_journal_and_normalizes_reconciliation(monkeypatch):
    client = DevHubCommandClient(url="https://devhub.example", token="x" * 24)
    calls = []

    def request(method, path, body=None, *, version=None, idempotency_key=None, expected_contract=COMMAND_CONTRACT):
        calls.append((method, path, body, version, idempotency_key, expected_contract))
        if method == "POST":
            return {
                "schema_version": OBSERVATION_CONTRACT, "id": body["observation_id"],
                "command_id": "command-1", "sequence": body["sequence"],
                "type": body["type"], "actor": "foundry", "observed_at": body["observed_at"],
                "received_at": body["observed_at"], "provenance": body["provenance"],
                "receipts": body["receipts"], "telemetry": body["telemetry"],
            }
        if path == "/commands/command-1":
            return raw_command(version=4, next_event_sequence=2)
        return {
            "schema_version": OBSERVATION_CONTRACT, "command_id": "command-1",
            "items": [{"id": "event-1", "sequence": 1}],
            "page": {"returned_count": 1, "next_cursor": None, "truncated": False},
            "reconciliation": {"status": "current", "stale": False, "next_sequence": 2},
        }

    monkeypatch.setattr(client, "_request", request)
    transport = CommandJournalEventTransport(client)
    event = {
        "observation_id": "event-1", "sequence": 1, "type": "completed",
        "observed_at": 1,
        "provenance": {"command_id": "command-1", "attempt_id": "attempt-1", "issue_id": "DEVHUB-20", "source": "foundry"},
        "receipts": {"pr": None, "review": None, "ci": None, "merge": None, "epic_closure": None},
        "telemetry": {"input_tokens": None, "cached_input_tokens": None, "output_tokens": None, "reasoning_tokens": None, "tool_tokens": None, "cost_cents": None, "duration_ms": None},
    }

    assert transport.append_event("command-1", 3, event) == {
        "accepted": True,
        "observation": {"id": "event-1", "sequence": 1},
    }
    assert transport.reconcile("command-1") == {
        "command": {"version": 4, "next_event_sequence": 2, "projection_stale": False, "observation_status": "current"},
        "events": [{"event_id": "event-1", "sequence": 1}],
    }
    assert calls[0] == (
        "POST", "/commands/command-1/observations", event, 3, "event-1", OBSERVATION_CONTRACT,
    )
    assert calls[1][0:2] == ("GET", "/commands/command-1/observations?cursor=0&limit=100")


def test_passive_reconciliation_reads_every_bounded_page_before_acknowledging(monkeypatch):
    client = DevHubCommandClient(url="https://devhub.example", token="x" * 24)
    paths = []

    def request(method, path, body=None, *, version=None, idempotency_key=None, expected_contract=COMMAND_CONTRACT):
        paths.append(path)
        if path == "/commands/command-1":
            return raw_command(version=9, next_event_sequence=3)
        cursor = "0" if "cursor=0" in path else "1"
        return {
            "schema_version": OBSERVATION_CONTRACT, "command_id": "command-1",
            "items": [{"id": f"event-{int(cursor) + 1}", "sequence": int(cursor) + 1}],
            "page": {
                "returned_count": 1,
                "next_cursor": "1" if cursor == "0" else None,
                "truncated": cursor == "0",
            },
            "reconciliation": {"status": "current", "stale": False, "next_sequence": 3},
        }

    monkeypatch.setattr(client, "_request", request)
    view = CommandJournalEventTransport(client).reconcile("command-1")

    assert [row["event_id"] for row in view["events"]] == ["event-1", "event-2"]
    assert paths == [
        "/commands/command-1/observations?cursor=0&limit=100",
        "/commands/command-1/observations?cursor=1&limit=100",
        "/commands/command-1",
    ]


@pytest.mark.parametrize("field", [
    "command_id", "project", "preview_id", "approval_id", "epic_id",
    "planning_version_id", "preview_digest", "snapshot_digest", "policy_digest",
])
def test_revalidation_refuses_every_changed_authority_binding(field):
    value = "OTHER" if field == "project" else "changed"
    with pytest.raises(CommandRevalidationError, match="binding"):
        revalidate_command(command(), observation(**{field: value}), now_ms=10_000)


def test_revalidation_refuses_expiry_floor_reduction_gate_bypass_and_capacity():
    bad = [
        observation(approval_expires_at=10_000),
        observation(current_minimum_tier="economy"),
        observation(required_gates=frozenset({"tests", "human-test"})),
        observation(required_gates=frozenset({"tests", "human-test", "extra"})),
        observation(active_concurrency=3),
    ]
    for item in bad:
        with pytest.raises(CommandRevalidationError):
            revalidate_command(command(), item, now_ms=10_000)


def test_revalidation_only_tightens_approved_limits_and_floor():
    effective = revalidate_command(command(), observation(), now_ms=10_000)
    assert effective.max_cost_cents == 300
    assert effective.max_concurrency == 3
    assert effective.minimum_tier == "frontier"


def test_revalidation_recognizes_only_the_exact_monotone_parent_snapshot_transition():
    accepted = observation(snapshot_advancement=snapshot_advancement())
    assert revalidate_command(command(), accepted, now_ms=10_000).max_cost_cents == 300

    for forged in (
        snapshot_advancement(command_id="other-command"),
        snapshot_advancement(binding_digest=DIGEST_C),
        snapshot_advancement(snapshot_digest=DIGEST_C),
        snapshot_advancement(from_version=5, to_version=6),
    ):
        with pytest.raises(CommandRevalidationError, match="avancement snapshot"):
            revalidate_command(
                command(), observation(snapshot_advancement=forged), now_ms=10_000,
            )


def test_receipt_store_refuses_binding_or_effect_identity_replacement(tmp_path):
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    claimed = command(lease=CommandLease("lease-1", "worker-1", 20_000, 10_000))
    effect = EffectReceipt("command-1", "running", DIGEST_A)
    store.record(claimed, DIGEST_B, "effect-proven", lease_id="lease-1", effect=effect, now_ms=10_000)

    with pytest.raises(Exception, match="commande durable"):
        store.record(claimed, DIGEST_C, "effect-proven", lease_id="lease-1", effect=effect, now_ms=10_001)
    with pytest.raises(Exception, match="effet"):
        store.record(
            claimed, DIGEST_B, "effect-proven", lease_id="lease-1",
            effect=EffectReceipt("other-effect", "running", DIGEST_A), now_ms=10_002,
        )
    assert store.path.stat().st_mode & 0o077 == 0


def test_terminal_receipt_is_immutable_and_cannot_be_downgraded(tmp_path):
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    claimed = command(lease=CommandLease("lease-1", "worker-1", 20_000, 10_000))
    terminal = EffectReceipt("command-1", "completed", DIGEST_A, 10, 20)
    store.record(
        claimed, DIGEST_B, "terminal", lease_id="lease-1",
        effect=terminal, now_ms=10_000,
    )

    with pytest.raises(CommandWorkerError, match="terminal"):
        store.record(
            claimed, DIGEST_B, "effect-proven", lease_id="lease-2",
            effect=replace(terminal, status="running"), now_ms=10_001,
        )
    with pytest.raises(CommandWorkerError, match="terminal"):
        store.record(
            claimed, DIGEST_B, "terminal", lease_id="lease-2",
            effect=replace(terminal, status="failed"), now_ms=10_002,
        )
    assert store.get("command-1").effect == terminal


def test_post_gateway_failure_is_transport_ambiguity_but_get_is_definitive(monkeypatch):
    class Gateway:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, 503, "gateway unavailable", {}, io.BytesIO(b"{}"),
            )

    monkeypatch.setattr("urllib.request.build_opener", lambda *_args: Gateway())
    client = DevHubCommandClient(
        url="https://devhub.example", token="x" * 24,
    )

    with pytest.raises(DevHubCommandTransportError) as post:
        client._request("POST", "/commands/command-1/heartbeats", {})
    assert post.value.status == 503
    with pytest.raises(DevHubCommandError) as get:
        client._request("GET", "/commands/command-1")
    assert not isinstance(get.value, DevHubCommandTransportError)


class FakeClient:
    def __init__(
        self, item: DevHubCommand, *, claim_transport=False,
        claim_changes=None, heartbeat_result="advanced",
    ):
        self.item = item
        self.claim_transport = claim_transport
        self.claim_changes = claim_changes or {}
        self.heartbeat_result = heartbeat_result
        self.launch_events = []
        self.event_evidence = []
        self.claim_calls = 0
        self.heartbeat_calls = 0

    def list_commands(self, _project, *, cursor, limit):
        assert limit <= 100
        return CommandPage((self.item,), None, False)

    def claim(self, item, *, worker_id, lease_seconds, idempotency_key):
        self.claim_calls += 1
        self.item = replace(
            item, version=item.version + 1,
            lease=CommandLease("lease-1", worker_id, 30_000, 10_000),
            **self.claim_changes,
        )
        if self.claim_transport:
            self.claim_transport = False
            raise DevHubCommandTransportError("POST", "/pull", None, "command_unavailable_service")
        return self.item

    def get_command(self, _command_id):
        return self.item

    def heartbeat(self, item, *, lease_id, extend_seconds, idempotency_key):
        self.heartbeat_calls += 1
        if self.heartbeat_result == "transport-unchanged":
            raise DevHubCommandTransportError(
                "POST", "/heartbeats", None, "command_unavailable_service",
            )
        lease = item.lease
        assert lease is not None
        if self.heartbeat_result == "unchanged":
            next_lease = lease
        elif self.heartbeat_result == "expired":
            next_lease = replace(
                lease, heartbeat_at=lease.heartbeat_at + 1, expires_at=10_000,
            )
        elif self.heartbeat_result == "wrong-lease":
            next_lease = replace(
                lease, id="lease-replaced", heartbeat_at=lease.heartbeat_at + 1,
                expires_at=lease.expires_at + 1_000,
            )
        else:
            next_lease = replace(
                lease, heartbeat_at=lease.heartbeat_at + 1,
                expires_at=lease.expires_at + 1_000,
            )
        self.item = replace(
            item, version=item.version + 1,
            lease=next_lease,
            projection_stale=(
                True
                if (self.heartbeat_result == "stale-on-second"
                    and self.heartbeat_calls >= 2)
                else item.projection_stale
            ),
            state=(
                "cancel-requested"
                if (self.heartbeat_result == "cancel-requested"
                    or (self.heartbeat_result == "cancel-on-second"
                        and self.heartbeat_calls >= 2))
                else item.state
            ),
        )
        if self.heartbeat_result == "transport-applied":
            raise DevHubCommandTransportError(
                "POST", "/heartbeats", None, "command_unavailable_service",
            )
        return self.item

    def append_event(
        self, item, *, event_id, sequence, event_type, occurred_at, lease_id,
        cost_cents, duration_ms, idempotency_key, attempt_id=None, issue_id=None,
        receipts=None,
    ):
        self.launch_events.append((event_id, sequence, event_type, cost_cents, duration_ms))
        self.event_evidence.append((attempt_id, issue_id, receipts))
        self.item = replace(
            item, version=item.version + 1, state=event_type,
            projection_stale=False, next_event_sequence=sequence + 1,
        )
        return self.item

    def list_events(self, *_args, **_kwargs):
        raise AssertionError("no ambiguous event in this test")


class FakeProvider:
    def __init__(
        self, *, launch_result=None, resolves=(), observations=(), profile=None,
        resume_result=None,
    ):
        self.launch_result = launch_result or EffectReceipt(
            "command-1", "completed", DIGEST_A, 120, 2_000,
            "attempt-1", 9_000,
        )
        self.resolves = list(resolves)
        self.observations = list(observations)
        self.profile = profile or ExecutionProfile("frontier", 300)
        self.resume_result = resume_result
        self.launches = 0
        self.resumes = 0
        self.observe_calls = 0
        self.limits = []
        self.authorizations = []

    def observe(self, _command):
        self.observe_calls += 1
        return self.observations.pop(0) if self.observations else observation()

    def resolve(self, _command_id, _binding_digest):
        return self.resolves.pop(0) if self.resolves else None

    def execution_profile(self, _command, _receipt):
        return self.profile

    def launch(
        self, _command, authorization, *, effect_id, heartbeat,
        reconcile_capacity,
    ):
        assert isinstance(authorization, ExecutionAuthorization)
        self.authorizations.append(authorization)
        self.limits.append(authorization)
        assert effect_id == "command-1"
        reconcile_capacity(self.observe(_command))
        self.launches += 1
        if isinstance(self.launch_result, Exception):
            raise self.launch_result
        return self.launch_result

    def resume(
        self, _command, receipt, authorization, *, heartbeat,
        reconcile_capacity,
    ):
        reconcile_capacity(self.observe(_command))
        self.resumes += 1
        self.authorizations.append(authorization)
        return self.resume_result or replace(receipt, status="completed")


def worker(tmp_path, client, provider):
    return CommandWorker(
        client, ReceiptStore(tmp_path / "receipts.sqlite3"), provider,
        worker_id="worker-1", lease_seconds=60, now_ms=lambda: 10_000,
    )


class PassiveTransport:
    def __init__(self, *, fail=False, version=1):
        self.fail = fail
        self.version = version
        self.events = []

    def append_event(self, command_id, expected_version, event):
        if self.fail:
            raise OSError("offline")
        self.events.append((command_id, expected_version, event))
        return {"accepted": True, "observation": {
            "id": event["observation_id"], "sequence": event["sequence"],
        }}

    def reconcile(self, _command_id):
        return {"command": {
            "version": self.version, "next_event_sequence": 1, "projection_stale": False,
            "observation_status": "current",
        }, "events": []}


def test_worker_passively_publishes_only_proven_effect_with_nullable_facts(tmp_path):
    client = FakeClient(command())
    provider = FakeProvider()
    transport = PassiveTransport()
    publisher = PassiveEventPublisher(tmp_path / "passive", transport)
    actual = CommandWorker(
        client, ReceiptStore(tmp_path / "receipts.sqlite3"), provider,
        worker_id="worker-1", lease_seconds=60, now_ms=lambda: 10_000,
        event_publisher=publisher,
    )

    outcome = actual.run_once("DEVHUB")[0]

    assert (outcome.status, outcome.detail) == ("completed", "effect_proven")
    assert [event[2:] for event in client.launch_events] == [
        ("accepted", None, None), ("running", None, None), ("completed", 120, 2_000),
    ]
    assert len(transport.events) == 2
    assert [row[2]["type"] for row in transport.events] == ["running", "completed"]
    payload = transport.events[1][2]
    assert payload["provenance"] == {
        "command_id": "command-1", "attempt_id": "attempt-1",
        "issue_id": "DEVHUB-20", "source": "foundry",
    }
    assert payload["receipts"] == {
        "pr": None, "review": None, "ci": None, "merge": None, "epic_closure": None,
    }
    assert payload["telemetry"] == {
        "input_tokens": None, "cached_input_tokens": None, "output_tokens": None,
        "reasoning_tokens": None, "tool_tokens": None, "cost_cents": 120,
        "duration_ms": 2_000,
    }


def test_worker_binds_real_epic_closure_to_native_completed_event(tmp_path):
    terminal = extended_terminal_event()

    class ReceiptEvidence:
        def for_attempt(self, attempt_id):
            assert attempt_id == "attempt-1"
            return ({
                "attempt_id": attempt_id,
                "issue_id": "DEVHUB-20",
                "receipts": terminal["receipts"],
            },)

    client = FakeClient(command())
    actual = CommandWorker(
        client, ReceiptStore(tmp_path / "receipts.sqlite3"), FakeProvider(),
        worker_id="worker-1", lease_seconds=60, now_ms=lambda: 10_000,
        execution_receipts=ReceiptEvidence(),
    )

    outcome = actual.run_once("DEVHUB")[0]

    assert (outcome.status, outcome.detail) == ("completed", "effect_proven")
    assert client.event_evidence[:-1] == [(None, None, None), (None, None, None)]
    assert client.event_evidence[-1] == (
        "attempt-1", "DEVHUB-20", terminal["receipts"],
    )
    assert client.launch_events[-1][2:] == ("completed", 120, 2_000)


@pytest.mark.parametrize("receipt_state", ("missing", "unreadable"))
def test_worker_never_fabricates_terminal_evidence_from_unavailable_f91(
    tmp_path, receipt_state,
):
    receipt_directory = tmp_path / "execution-receipts"
    execution_receipts = ExecutionReceiptStore(receipt_directory)
    if receipt_state == "unreadable":
        receipt_directory.mkdir()
        (receipt_directory / "receipts.json").write_text("{}", encoding="utf-8")
    client = FakeClient(command())
    actual = CommandWorker(
        client, ReceiptStore(tmp_path / "receipts.sqlite3"), FakeProvider(),
        worker_id="worker-1", lease_seconds=60, now_ms=lambda: 10_000,
        execution_receipts=execution_receipts,
    )

    outcome = actual.run_once("DEVHUB")[0]

    assert outcome.status == "completed"
    assert client.event_evidence[-1] == (None, None, None)


def test_worker_forwards_only_exact_f91_receipts_as_separate_passive_issue_facts(tmp_path):
    class ReceiptEvidence:
        def for_attempt(self, attempt_id):
            assert attempt_id == "attempt-1"
            return ({
                "attempt_id": attempt_id,
                "issue_id": "DEVHUB-20",
                "receipts": {
                    "pr": None, "review": None, "ci": None,
                    "merge": {
                        "provider": "github", "repository": "patobiskoto/dev-hub",
                        "url": "https://github.com/patobiskoto/dev-hub/pull/20",
                        "pr_number": 20, "merged": True, "merge_sha": "c" * 40,
                    },
                    "epic_closure": None,
                },
            }, {
                "attempt_id": attempt_id,
                "issue_id": "DEVHUB-21",
                "receipts": {
                    "pr": {
                        "provider": "github", "repository": "patobiskoto/dev-hub",
                        "url": "https://github.com/patobiskoto/dev-hub/pull/22",
                        "number": 22, "state": "open", "head_sha": "a" * 40,
                        "base_sha": "b" * 40,
                    },
                    "review": None, "ci": None, "merge": None,
                    "epic_closure": None,
                },
            },)

    client = FakeClient(command())
    transport = PassiveTransport()
    actual = CommandWorker(
        client, ReceiptStore(tmp_path / "receipts.sqlite3"), FakeProvider(),
        worker_id="worker-1", lease_seconds=60, now_ms=lambda: 10_000,
        event_publisher=PassiveEventPublisher(tmp_path / "passive", transport),
        execution_receipts=ReceiptEvidence(),
    )

    outcome = actual.run_once("DEVHUB")[0]

    assert outcome.status == "completed"
    assert [event[2]["provenance"]["issue_id"] for event in transport.events] == [
        "DEVHUB-20", "DEVHUB-20", "DEVHUB-21",
    ]
    assert transport.events[1][2]["receipts"]["merge"] == {
        "provider": "github", "repository": "patobiskoto/dev-hub",
        "url": "https://github.com/patobiskoto/dev-hub/pull/20",
        "pr_number": 20, "merged": True, "merge_sha": "c" * 40,
    }
    receipt_event = transport.events[-1][2]
    assert receipt_event["type"] == "unknown"
    assert receipt_event["telemetry"] == {
        "input_tokens": None, "cached_input_tokens": None, "output_tokens": None,
        "reasoning_tokens": None, "tool_tokens": None, "cost_cents": None,
        "duration_ms": None,
    }
    assert receipt_event["receipts"]["pr"] == {
        "provider": "github", "repository": "patobiskoto/dev-hub",
        "url": "https://github.com/patobiskoto/dev-hub/pull/22",
        "number": 22, "state": "open", "head_sha": "a" * 40,
        "base_sha": "b" * 40,
    }
    assert all(receipt_event["receipts"][kind] is None for kind in (
        "review", "ci", "merge", "epic_closure",
    ))


def test_terminal_command_replays_a_durable_passive_spool_without_reexecution(tmp_path):
    transport = PassiveTransport(fail=True, version=3)
    spool = tmp_path / "passive"
    publisher = PassiveEventPublisher(spool, transport, retries=1)
    pending = publisher.publish(
        command={"id": "command-1", "version": 3}, attempt_id="attempt-1",
        issue_id="DEVHUB-20", event_type="completed", occurred_at=10_000,
    )
    assert pending.status == "buffered"

    transport.fail = False
    client = FakeClient(command(state="completed", version=3))
    provider = FakeProvider()
    actual = CommandWorker(
        client, ReceiptStore(tmp_path / "receipts.sqlite3"), provider,
        worker_id="worker-1", lease_seconds=60, now_ms=lambda: 10_000,
        event_publisher=PassiveEventPublisher(spool, transport, retries=1),
    )

    assert actual.run_once("DEVHUB") == ()
    assert provider.launches == provider.resumes == 0
    assert [(event[1], event[2]["observation_id"])
            for event in transport.events] == [(3, pending.event_id)]


def test_passive_publication_outage_never_changes_execution_outcome_or_policy(tmp_path):
    client = FakeClient(command())
    provider = FakeProvider()
    transport = PassiveTransport(fail=True)
    actual = CommandWorker(
        client, ReceiptStore(tmp_path / "receipts.sqlite3"), provider,
        worker_id="worker-1", lease_seconds=60, now_ms=lambda: 10_000,
        event_publisher=PassiveEventPublisher(tmp_path / "passive", transport, retries=1),
    )

    outcome = actual.run_once("DEVHUB")[0]

    assert (outcome.status, outcome.detail) == ("completed", "effect_proven")
    assert provider.launches == 1
    assert provider.authorizations[0].selected_tier == "frontier"
    assert client.launch_events[-1][2] == "completed"
    assert (tmp_path / "passive" / "pending.json").exists()


def test_worker_claims_with_lease_revalidates_tightened_limits_and_records_effect(tmp_path):
    client = FakeClient(command())
    provider = FakeProvider()

    outcomes = worker(tmp_path, client, provider).run_once("DEVHUB")

    assert [(item.status, item.detail) for item in outcomes] == [("completed", "effect_proven")]
    assert client.claim_calls == 1
    assert client.heartbeat_calls >= 2
    assert provider.launches == 1
    assert provider.authorizations[0].approved_max_cost_cents == 300
    assert provider.authorizations[0].cost_ceiling_cents == 300
    assert provider.authorizations[0].provider_invocation_ceiling_cents == 300
    assert provider.authorizations[0].minimum_tier == "frontier"
    assert client.launch_events[-1][2:] == ("completed", 120, 2_000)


def test_execution_authorization_validates_aggregate_and_per_call_caps_separately():
    valid = ExecutionAuthorization(
        command_id="command-1", binding_digest=DIGEST_A,
        selected_tier="frontier", cost_ceiling_cents=300,
        concurrency_units=2, approved_max_cost_cents=400,
        approved_max_concurrency=3, minimum_tier="frontier",
        provider_invocation_ceiling_cents=250,
    )

    assert valid.cost_ceiling_cents == 300
    assert valid.provider_invocation_ceiling_cents == 250
    with pytest.raises(ValueError, match="élargie"):
        replace(valid, provider_invocation_ceiling_cents=301)
    with pytest.raises(ValueError, match="hors borne"):
        replace(valid, provider_invocation_ceiling_cents=0)


def test_worker_emits_a_contiguous_native_cycle_before_a_paused_effect(tmp_path):
    client = FakeClient(command())
    provider = FakeProvider(
        launch_result=EffectReceipt("command-1", "paused", DIGEST_A, 0, 12),
    )

    outcome = worker(tmp_path, client, provider).run_once("DEVHUB")[0]

    assert (outcome.status, outcome.detail) == ("paused", "effect_proven")
    assert [(event[1], event[2]) for event in client.launch_events] == [
        (1, "accepted"), (2, "running"), (3, "paused"),
    ]
    assert client.item.state == "paused"
    assert client.item.projection_stale is False


@pytest.mark.parametrize(
    ("failed_event", "failure", "stale_projection", "state_after_failure"),
    (
        ("accepted", "crash", False, "requested"),
        ("running", "crash", False, "accepted"),
        ("accepted", "transport", False, "accepted"),
        ("running", "transport", False, "running"),
        ("accepted", "transport", True, "unknown"),
    ),
)
def test_valid_capacity_preamble_failure_is_durably_retried_without_duplicate_effect(
    tmp_path, failed_event, failure, stale_projection, state_after_failure,
):
    class RecordingStore(ReceiptStore):
        def __init__(self, path):
            self.phases = []
            super().__init__(path)

        def record(self, *args, **kwargs):
            durable = super().record(*args, **kwargs)
            self.phases.append(durable.phase)
            return durable

    class FailingPreambleClient(FakeClient):
        fail = True

        def append_event(self, *args, **kwargs):
            if self.fail and kwargs["event_type"] == failed_event:
                if failure == "crash":
                    raise OSError("lifecycle preamble crashed")
                super().append_event(*args, **kwargs)
                if stale_projection:
                    self.item = replace(
                        self.item, state="unknown", projection_stale=True,
                    )
                raise DevHubCommandTransportError(
                    "POST", "/events", None, "command_unavailable_service",
                )
            return super().append_event(*args, **kwargs)

        def list_events(self, *_args, **_kwargs):
            if self.fail:
                raise DevHubCommandError(
                    "GET", "/events", None, "command_unavailable_service",
                )
            return super().list_events(*_args, **_kwargs)

    client = FailingPreambleClient(command())
    provider = FakeProvider()
    store = RecordingStore(tmp_path / "receipts.sqlite3")
    subject = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    )

    if failure == "crash":
        with pytest.raises(OSError, match="lifecycle preamble crashed"):
            subject.run_once("DEVHUB")
    else:
        deferred = subject.run_once("DEVHUB")[0]
        assert (deferred.status, deferred.detail) == (
            "deferred", "command_unavailable_service",
        )

    durable = store.get("command-1")
    reservation = subject.reservations.get("command-1")
    assert durable is not None and durable.effect is None
    assert durable.phase == "pre-effect-preamble"
    assert store.phases == ["claimed", "pre-effect-preamble"]
    assert "launch-intent" not in store.phases
    assert provider.launches == provider.resumes == 0
    assert client.item.state == state_after_failure
    assert client.item.projection_stale is stale_projection
    assert reservation is not None
    assert (
        reservation.state, reservation.owner_id,
        reservation.cost_ceiling_cents, reservation.concurrency_units,
    ) == ("active", None, 300, 1)

    client.fail = False
    recovered = subject.run_once("DEVHUB")[0]

    assert (recovered.status, recovered.detail) == ("completed", "effect_proven")
    assert provider.launches == 1
    assert provider.resumes == 0
    assert provider.observe_calls == 5
    assert store.phases[-3:] == [
        "pre-effect-preamble", "launch-intent", "terminal",
    ]
    settled = subject.reservations.get("command-1")
    assert settled is not None
    assert settled.reserved_at == reservation.reserved_at
    assert (settled.state, settled.settled_cost_cents) == ("settled", 120)

    assert subject.run_once("DEVHUB") == ()
    assert provider.launches == 1
    assert provider.resumes == 0


def test_reservation_commit_then_process_failure_is_reclaimed_from_prior_preamble(
    tmp_path,
):
    clock = [10_000]

    class SimulatedProcessExit(BaseException):
        pass

    class CrashAfterCommitReservations(HostReservationStore):
        crash_after_commit = True

        def reserve(self, *args, **kwargs):
            reserved = super().reserve(*args, **kwargs)
            if self.crash_after_commit:
                raise SimulatedProcessExit
            return reserved

    class ClockedClient(FakeClient):
        def claim(self, item, *, worker_id, lease_seconds, idempotency_key):
            self.claim_calls += 1
            self.item = replace(
                item, version=item.version + 1,
                lease=CommandLease(
                    "lease-1", worker_id, clock[0] + 60_000, clock[0],
                ),
            )
            return self.item

    class FreshProvider(FakeProvider):
        def observe(self, _command):
            self.observe_calls += 1
            return observation(
                preview_expires_at=120_000,
                approval_expires_at=110_000,
                observed_at=clock[0],
                valid_until=clock[0] + 10_000,
            )

    client = ClockedClient(command())
    provider = FreshProvider()
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    reservations = CrashAfterCommitReservations(
        tmp_path / "host-reservations.sqlite3",
    )
    subject = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: clock[0], reservation_store=reservations,
    )

    with pytest.raises(SimulatedProcessExit):
        subject.run_once("DEVHUB")

    durable = store.get("command-1")
    stranded = reservations.get("command-1")
    assert durable is not None and durable.effect is None
    assert durable.phase == "pre-effect-preamble"
    assert provider.launches == provider.resumes == 0
    assert client.launch_events == []
    assert stranded is not None
    assert (
        stranded.state, stranded.cost_ceiling_cents,
        stranded.concurrency_units, stranded.settled_cost_cents,
    ) == ("active", 300, 1, None)
    assert stranded.owner_id is not None
    assert stranded.owner_expires_at is not None

    reservations.crash_after_commit = False
    clock[0] = stranded.owner_expires_at + 1
    recovered = subject.run_once("DEVHUB")[0]

    assert (recovered.status, recovered.detail) == ("completed", "effect_proven")
    assert provider.launches == 1
    assert provider.resumes == 0
    assert provider.observe_calls == 5
    assert [event[2] for event in client.launch_events] == [
        "accepted", "running", "completed",
    ]
    settled = reservations.get("command-1")
    assert settled is not None
    assert settled.reserved_at == stranded.reserved_at
    assert (
        settled.state, settled.cost_ceiling_cents,
        settled.concurrency_units, settled.settled_cost_cents,
    ) == ("settled", 300, 1, 120)
    assert store.get("command-1").phase == "terminal"

    assert subject.run_once("DEVHUB") == ()
    assert provider.launches == 1
    assert provider.resumes == 0


@pytest.mark.parametrize(
    ("control_state", "effect_status", "durable_phase"),
    (
        ("pause-requested", "paused", "human-paused"),
        ("cancel-requested", "cancelled", "human-cancelled"),
    ),
)
def test_human_control_wins_after_a_durable_preamble_failure(
    tmp_path, control_state, effect_status, durable_phase,
):
    class CrashingClient(FakeClient):
        crash = True

        def append_event(self, *args, **kwargs):
            if self.crash and kwargs["event_type"] == "running":
                raise OSError("lifecycle preamble crashed")
            return super().append_event(*args, **kwargs)

    client = CrashingClient(command())
    provider = FakeProvider()
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    subject = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    )
    with pytest.raises(OSError, match="lifecycle preamble crashed"):
        subject.run_once("DEVHUB")
    assert store.get("command-1").phase == "pre-effect-preamble"

    client.crash = False
    client.item = replace(client.item, state=control_state)
    controlled = subject.run_once("DEVHUB")[0]

    assert (controlled.status, controlled.detail) == (
        effect_status, "human_control_confirmed",
    )
    assert provider.launches == provider.resumes == 0
    receipt = store.get("command-1")
    assert receipt is not None and receipt.effect is not None
    assert (receipt.phase, receipt.effect.status) == (durable_phase, effect_status)


def test_worker_runs_after_proven_unlaunched_reservation_reconciliation(tmp_path):
    reservations = HostReservationStore(tmp_path / "host-reservations.sqlite3")
    profile = ExecutionProfile("frontier", 300)
    bounded = EffectiveLimits(300, 1, 300, "frontier", 300, 0, 9_000)
    reservations.reserve(
        "command-old", DIGEST_A, profile, bounded,
        owner_id="owner-old", now_ms=9_000,
    )
    reservations.yield_owner("command-old", DIGEST_A, "owner-old")
    with pytest.raises(CommandWorkerError, match="capacité hôte"):
        reservations.reserve(
            "command-probe", DIGEST_B, profile, bounded,
            owner_id="owner-probe", now_ms=9_001,
        )
    reservations.reconcile_unlaunched(
        UnlaunchedReservationProof("command-old", DIGEST_A, DIGEST_C, 9_002),
        now_ms=9_002,
    )
    available = observation(
        local_max_concurrency=1, active_concurrency=0,
    )
    client = FakeClient(command())
    provider = FakeProvider(observations=(available, available, available))
    subject = CommandWorker(
        client, ReceiptStore(tmp_path / "receipts.sqlite3"), provider,
        worker_id="worker-1", lease_seconds=60, now_ms=lambda: 10_000,
        reservation_store=reservations,
    )

    outcome = subject.run_once("DEVHUB")[0]

    assert (outcome.status, outcome.detail) == ("completed", "effect_proven")
    assert provider.launches == 1
    assert reservations.get("command-old").settled_cost_cents == 0
    assert reservations.get("command-1").settled_cost_cents == 120


def test_worker_publishes_a_replayable_pause_when_host_capacity_refuses_pre_effect(tmp_path):
    client = FakeClient(command())
    provider = FakeProvider()
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    reservations = HostReservationStore(tmp_path / "host-reservations.sqlite3")
    # The external observation accounts for one active unit.  A distinct local
    # allocation consumes two more, so this command cannot reserve its own unit.
    reservations.reserve(
        "prior-command", DIGEST_A, ExecutionProfile("frontier", 1, concurrency_units=2),
        EffectiveLimits(300, 3, 300, "frontier", 300, 1, 9_000),
        owner_id="prior-worker", now_ms=10_000,
    )
    subject = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000, reservation_store=reservations,
    )

    outcome = subject.run_once("DEVHUB")[0]

    assert (outcome.status, outcome.detail) == ("paused", "pre_effect_paused")
    assert provider.launches == provider.resumes == 0
    assert [(event[1], event[2]) for event in client.launch_events] == [
        (1, "accepted"), (2, "running"), (3, "paused"),
    ]
    receipt = store.get("command-1")
    assert receipt is not None
    assert receipt.phase == "pre-effect-paused"
    assert receipt.effect is not None
    assert (receipt.effect.status, receipt.effect.cost_cents, receipt.effect.duration_ms) == (
        "paused", 0, 0,
    )
    assert reservations.get("command-1") is None


def test_worker_pauses_missing_provider_ceiling_before_provider_effect(tmp_path):
    client = FakeClient(command())
    provider = FakeProvider(observations=(observation(
        provider_invocation_ceiling_cents=None,
    ),))
    store = ReceiptStore(tmp_path / "receipts.sqlite3")

    subject = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    )

    outcome = subject.run_once("DEVHUB")[0]

    assert (outcome.status, outcome.detail) == ("paused", "pre_effect_paused")
    assert provider.launches == provider.resumes == 0
    assert [(event[1], event[2]) for event in client.launch_events] == [
        (1, "accepted"), (2, "running"), (3, "paused"),
    ]
    receipt = store.get("command-1")
    assert receipt is not None and receipt.effect is not None
    assert receipt.phase == "pre-effect-paused"
    assert (receipt.effect.status, receipt.effect.cost_cents) == ("paused", 0)

    resumed = subject.run_once("DEVHUB")[0]

    assert (resumed.status, resumed.detail) == ("completed", "effect_proven")
    assert provider.launches == 1
    assert provider.resumes == 0
    assert [(event[1], event[2]) for event in client.launch_events] == [
        (1, "accepted"), (2, "running"), (3, "paused"),
        (4, "running"), (5, "completed"),
    ]


def test_known_invalid_capacity_is_durable_before_event_crash_and_never_marks_launch(
    tmp_path,
):
    class PhaseStore(ReceiptStore):
        def __init__(self, path):
            self.phases = []
            super().__init__(path)

        def record(self, *args, **kwargs):
            durable = super().record(*args, **kwargs)
            self.phases.append(durable.phase)
            return durable

    class CrashingEventClient(FakeClient):
        event_crashes = True

        def append_event(self, *args, **kwargs):
            if self.event_crashes:
                raise OSError("event journal unavailable")
            return super().append_event(*args, **kwargs)

    client = CrashingEventClient(command())
    provider = FakeProvider(observations=(observation(
        provider_invocation_ceiling_cents=None,
    ),))
    store = PhaseStore(tmp_path / "receipts.sqlite3")
    subject = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    )

    with pytest.raises(OSError, match="event journal unavailable"):
        subject.run_once("DEVHUB")

    durable_pause = store.get("command-1")
    assert durable_pause is not None
    assert durable_pause.phase == "pre-effect-paused"
    assert store.phases == ["claimed", "pre-effect-paused"]
    assert "launch-intent" not in store.phases
    assert provider.launches == provider.resumes == 0
    assert subject.reservations.get("command-1") is None

    client.event_crashes = False
    recovered = subject.run_once("DEVHUB")[0]

    assert (recovered.status, recovered.detail) == ("completed", "effect_proven")
    assert provider.launches == 1
    assert provider.resumes == 0
    assert store.phases == [
        "claimed", "pre-effect-paused", "launch-intent", "terminal",
    ]


@pytest.mark.parametrize(
    ("control_state", "effect_status", "durable_phase"),
    (
        ("pause-requested", "paused", "human-paused"),
        ("cancel-requested", "cancelled", "human-cancelled"),
    ),
)
def test_human_control_after_pre_effect_pause_never_becomes_a_technical_relaunch(
    tmp_path, control_state, effect_status, durable_phase,
):
    client = FakeClient(command())
    provider = FakeProvider(observations=(observation(
        provider_invocation_ceiling_cents=None,
    ),))
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    subject = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    )

    technical = subject.run_once("DEVHUB")[0]
    technical_receipt = store.get("command-1")
    assert (technical.status, technical.detail) == ("paused", "pre_effect_paused")
    assert technical_receipt is not None and technical_receipt.effect is not None
    assert technical_receipt.phase == "pre-effect-paused"

    # Capacity is valid again, but an intervening human control must win before
    # any provider launch and must no longer be journaled as a technical pause.
    client.item = replace(client.item, state=control_state)
    controlled = subject.run_once("DEVHUB")[0]

    assert (controlled.status, controlled.detail) == (
        effect_status, "human_control_confirmed",
    )
    assert provider.launches == provider.resumes == 0
    receipt = store.get("command-1")
    assert receipt is not None and receipt.effect is not None
    assert receipt.phase == durable_phase
    assert receipt.effect.status == effect_status
    assert receipt.effect.proof_digest != technical_receipt.effect.proof_digest
    assert (
        receipt.effect.cost_cents,
        receipt.effect.duration_ms,
        receipt.effect.attempt_id,
        receipt.effect.attempt_started_at,
    ) == (0, 0, None, None)
    assert [event[2] for event in client.launch_events] == [
        "accepted", "running", "paused", effect_status,
    ]

    if control_state == "pause-requested":
        retained = subject.run_once("DEVHUB")[0]
        assert (retained.status, retained.detail) == (
            "paused", "human_pause_retained",
        )
        assert provider.launches == provider.resumes == 0
    else:
        assert subject.run_once("DEVHUB") == ()


def test_worker_persists_runtime_cap_tightening_as_a_resumable_pause(tmp_path):
    class RuntimeTighteningProvider(FakeProvider):
        def __init__(self):
            super().__init__()
            self.runtime_checks = 0
            self.runtime_cap_tightened = True

        def launch(
            self, _command, authorization, *, effect_id, heartbeat,
            reconcile_capacity,
        ):
            self.runtime_checks += 1
            if self.runtime_cap_tightened:
                raise PreEffectCapacityError(
                    "plafond par invocation provider resserré avant effet",
                )
            return super().launch(
                _command, authorization, effect_id=effect_id, heartbeat=heartbeat,
                reconcile_capacity=reconcile_capacity,
            )

    client = FakeClient(command())
    provider = RuntimeTighteningProvider()
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    subject = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    )

    paused = subject.run_once("DEVHUB")[0]

    assert (paused.status, paused.detail) == ("paused", "pre_effect_paused")
    assert provider.runtime_checks == 1
    assert provider.launches == provider.resumes == 0
    assert [(event[1], event[2]) for event in client.launch_events] == [
        (1, "accepted"), (2, "running"), (3, "paused"),
    ]
    receipt = store.get("command-1")
    assert receipt is not None and receipt.effect is not None
    assert receipt.phase == "pre-effect-paused"
    assert (receipt.effect.status, receipt.effect.cost_cents, receipt.effect.duration_ms) == (
        "paused", 0, 0,
    )

    provider.runtime_cap_tightened = False
    resumed = subject.run_once("DEVHUB")[0]

    assert (resumed.status, resumed.detail) == ("completed", "effect_proven")
    assert provider.runtime_checks == 2
    assert provider.launches == 1
    assert provider.resumes == 0
    assert [(event[1], event[2]) for event in client.launch_events] == [
        (1, "accepted"), (2, "running"), (3, "paused"),
        (4, "running"), (5, "completed"),
    ]
    recovered = store.get("command-1")
    assert recovered is not None and recovered.effect is not None
    assert (recovered.phase, recovered.effect.status) == ("terminal", "completed")


@pytest.mark.parametrize("changes", [
    {"preview_id": "preview-2"},
    {"approval_id": "approval-2"},
    {"preview_digest": DIGEST_B},
    {"snapshot_digest": DIGEST_C},
    {"policy_digest": DIGEST_A},
    {"epic_id": "DEVHUB-21"},
    {"planning_version_id": "DEVHUB-VERSION-2"},
    {"max_cost_cents": 499},
    {"max_concurrency": 3},
    {"scheduled_for": 9_000},
    {"created_at": 101},
])
def test_claim_cannot_rewrite_any_approved_input(tmp_path, changes):
    client = FakeClient(command(), claim_changes=changes)
    provider = FakeProvider()

    with pytest.raises(CommandWorkerError, match="inputs approuvés"):
        worker(tmp_path, client, provider).run_once("DEVHUB")
    assert provider.launches == 0


def test_launch_preconditions_are_reobserved_after_preeffect_heartbeat(tmp_path):
    client = FakeClient(command())
    provider = FakeProvider(observations=(
        observation(),
        observation(required_gates=frozenset({"tests", "human-test", "extra"})),
    ))

    outcome = worker(tmp_path, client, provider).run_once("DEVHUB")[0]

    assert (outcome.status, outcome.detail) == ("refused", "revalidation_failed")
    assert client.heartbeat_calls == 2
    assert provider.launches == 0


@pytest.mark.parametrize("heartbeat_result", ["transport-unchanged", "unchanged", "expired"])
def test_ambiguous_unchanged_or_expired_heartbeat_never_reaches_launch(
    tmp_path, heartbeat_result,
):
    client = FakeClient(command(), heartbeat_result=heartbeat_result)
    provider = FakeProvider()

    outcome = worker(tmp_path, client, provider).run_once("DEVHUB")[0]

    assert (outcome.status, outcome.detail) == ("unknown", "identity_unresolved")
    assert provider.launches == 0


def test_ambiguous_heartbeat_only_continues_after_command_id_proves_advance(tmp_path):
    client = FakeClient(command(), heartbeat_result="transport-applied")
    provider = FakeProvider()

    outcome = worker(tmp_path, client, provider).run_once("DEVHUB")[0]

    assert outcome.status == "completed"
    assert provider.launches == 1


def test_human_control_state_observed_at_last_heartbeat_is_confirmed_by_provider(tmp_path):
    client = FakeClient(command(), heartbeat_result="cancel-on-second")
    provider = FakeProvider(
        launch_result=EffectReceipt("command-1", "cancelled", DIGEST_A, 0, 0),
    )

    outcome = worker(tmp_path, client, provider).run_once("DEVHUB")[0]
    assert outcome.status == "cancelled"
    assert provider.launches == 1


def test_provider_cannot_report_success_after_observing_cancel(tmp_path):
    client = FakeClient(command(), heartbeat_result="cancel-on-second")
    provider = FakeProvider()

    with pytest.raises(CommandWorkerError, match="contrôle humain"):
        worker(tmp_path, client, provider).run_once("DEVHUB")
    assert provider.launches == 1


@pytest.mark.parametrize(
    ("state", "effect_status"),
    (("pause-requested", "paused"), ("cancel-requested", "cancelled")),
)
def test_future_command_control_is_confirmed_before_schedule_skip(
    tmp_path, state, effect_status,
):
    item = command(state=state, scheduled_for=11_000)
    client = FakeClient(item)
    provider = FakeProvider(
        launch_result=EffectReceipt("command-1", effect_status, DIGEST_A, 0, 0),
    )

    outcome = worker(tmp_path, client, provider).run_once("DEVHUB")[0]

    assert outcome.status == effect_status
    assert provider.launches == 1
    assert client.launch_events[0][2] == effect_status


def test_future_command_without_control_remains_unclaimed(tmp_path):
    client = FakeClient(command(scheduled_for=11_000))
    provider = FakeProvider()

    assert worker(tmp_path, client, provider).run_once("DEVHUB") == ()
    assert client.claim_calls == provider.launches == 0


@pytest.mark.parametrize(
    ("state", "effect_status"),
    (("pause-requested", "paused"), ("cancel-requested", "cancelled")),
)
def test_existing_running_campaign_is_resumed_to_confirm_control(
    tmp_path, state, effect_status,
):
    item = command(state=state)
    client = FakeClient(item)
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    store.record(
        item, _binding_digest(item), "effect-proven", lease_id="old-lease",
        effect=EffectReceipt("command-1", "running", DIGEST_A), now_ms=9_000,
    )
    provider = FakeProvider(
        resume_result=EffectReceipt("command-1", effect_status, DIGEST_A, 0, 0),
    )
    subject = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    )

    outcome = subject.run_once("DEVHUB")[0]

    assert outcome.status == effect_status
    assert provider.resumes == 1
    assert client.launch_events[0][2] == effect_status


def test_explicitly_reactivated_paused_campaign_resumes_same_effect(tmp_path):
    item = command(state="requested")
    client = FakeClient(item)
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    paused = EffectReceipt("command-1", "paused", DIGEST_A, 10, 20)
    store.record(
        item, _binding_digest(item), "effect-proven", lease_id="old-lease",
        effect=paused, now_ms=9_000,
    )
    provider = FakeProvider(
        resume_result=EffectReceipt("command-1", "completed", DIGEST_B, 20, 40),
    )
    subject = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    )

    outcome = subject.run_once("DEVHUB")[0]

    assert outcome.status == "completed"
    assert provider.resumes == 1
    assert provider.launches == 0


def test_paused_projection_reaches_same_effect_reconciliation_without_duplicate_event(
    tmp_path,
):
    item = command(state="paused")
    client = FakeClient(item)
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    paused = EffectReceipt("command-1", "paused", DIGEST_A, 10, 20)
    store.record(
        item, _binding_digest(item), "effect-proven", lease_id="old-lease",
        effect=paused, now_ms=9_000,
    )
    provider = FakeProvider(resume_result=paused)
    subject = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    )

    outcome = subject.run_once("DEVHUB")[0]

    assert (outcome.status, outcome.detail) == (
        "paused", "effect_already_projected",
    )
    assert provider.resumes == 1
    assert provider.launches == 0
    assert client.launch_events == []


def test_same_worker_active_lease_is_heartbeated_without_a_second_claim(tmp_path):
    item = command(
        state="paused",
        lease=CommandLease("lease-active", "worker-1", 20_000, 9_000),
    )
    client = FakeClient(item)
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    paused = EffectReceipt("command-1", "paused", DIGEST_A, 10, 20)
    store.record(
        item, _binding_digest(item), "effect-proven", lease_id="lease-active",
        effect=paused, now_ms=9_000,
    )
    provider = FakeProvider(
        resume_result=EffectReceipt("command-1", "completed", DIGEST_B, 20, 40),
    )

    outcome = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    ).run_once("DEVHUB")[0]

    assert outcome.status == "completed"
    assert client.claim_calls == 0
    assert client.heartbeat_calls >= 3
    assert provider.resumes == 1
    assert provider.launches == 0


def test_active_lease_owned_by_another_worker_is_ignored(tmp_path):
    client = FakeClient(command(
        state="paused",
        lease=CommandLease("lease-active", "worker-2", 20_000, 9_000),
    ))
    provider = FakeProvider()

    assert worker(tmp_path, client, provider).run_once("DEVHUB") == ()
    assert client.claim_calls == client.heartbeat_calls == 0
    assert provider.launches == provider.resumes == 0


def test_same_worker_active_lease_identity_cannot_be_replaced(tmp_path):
    item = command(
        state="paused",
        lease=CommandLease("lease-active", "worker-1", 20_000, 9_000),
    )
    client = FakeClient(item, heartbeat_result="wrong-lease")
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    store.record(
        item, _binding_digest(item), "effect-proven", lease_id="lease-active",
        effect=EffectReceipt("command-1", "paused", DIGEST_A), now_ms=9_000,
    )

    with pytest.raises(CommandWorkerError, match="lease DevHub contradictoire"):
        CommandWorker(
            client, store, FakeProvider(), worker_id="worker-1", lease_seconds=60,
            now_ms=lambda: 10_000,
        ).run_once("DEVHUB")


def test_expired_lease_restores_projection_before_resuming_same_effect(tmp_path):
    stale = command(
        state="unknown", projection_stale=True,
        lease=CommandLease("lease-expired", "worker-1", 9_000, 8_000),
    )
    client = FakeClient(stale)
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    paused = EffectReceipt("command-1", "paused", DIGEST_A, 10, 20)
    store.record(
        stale, _binding_digest(stale), "effect-proven", lease_id="lease-expired",
        effect=paused, now_ms=9_000,
    )
    provider = FakeProvider(
        resume_result=EffectReceipt("command-1", "completed", DIGEST_B, 20, 40),
    )
    subject = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    )

    restored = subject.run_once("DEVHUB")[0]

    assert (restored.status, restored.detail) == (
        "running", "resume_projection_restored",
    )
    assert client.claim_calls == 1
    assert provider.launches == provider.resumes == 0
    assert [(event[1], event[2]) for event in client.launch_events] == [(1, "running")]
    assert client.item.projection_stale is False
    assert store.get("command-1").effect == paused

    resumed = subject.run_once("DEVHUB")[0]

    assert resumed.status == "completed"
    assert client.claim_calls == 1
    assert provider.launches == 0
    assert provider.resumes == 1
    assert store.get("command-1").effect.effect_id == "command-1"


@pytest.mark.parametrize("missing_observation", ["initial", "latest"])
def test_stale_projection_with_missing_provider_cap_pauses_and_recovers(
    tmp_path, missing_observation,
):
    stale = command(
        state="unknown", projection_stale=True,
        lease=CommandLease("lease-expired", "worker-1", 9_000, 8_000),
    )
    client = FakeClient(stale)
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    prior = EffectReceipt("command-1", "paused", DIGEST_A, 10, 20)
    store.record(
        stale, _binding_digest(stale), "effect-proven", lease_id="lease-expired",
        effect=prior, now_ms=9_000,
    )
    missing = observation(provider_invocation_ceiling_cents=None)
    observations = (
        (missing,)
        if missing_observation == "initial"
        else (observation(), missing)
    )
    provider = FakeProvider(
        observations=observations,
        resume_result=EffectReceipt("command-1", "completed", DIGEST_B, 20, 40),
    )
    subject = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    )

    paused = subject.run_once("DEVHUB")[0]

    assert (paused.status, paused.detail) == ("paused", "pre_effect_paused")
    assert provider.launches == provider.resumes == 0
    assert [(event[1], event[2]) for event in client.launch_events] == [(1, "paused")]
    receipt = store.get("command-1")
    assert receipt is not None and receipt.effect is not None
    assert receipt.phase == "effect-proven"
    assert (receipt.effect.status, receipt.effect.cost_cents, receipt.effect.duration_ms) == (
        "paused", 0, 0,
    )

    resumed = subject.run_once("DEVHUB")[0]

    assert (resumed.status, resumed.detail) == ("completed", "effect_proven")
    assert provider.launches == 0
    assert provider.resumes == 1
    recovered = store.get("command-1")
    assert recovered is not None and recovered.effect is not None
    assert (recovered.phase, recovered.effect.status) == ("terminal", "completed")


def test_expired_stale_command_without_resumable_receipt_remains_unknown(tmp_path):
    client = FakeClient(command(
        state="unknown", projection_stale=True,
        lease=CommandLease("lease-expired", "worker-1", 9_000, 8_000),
    ))
    provider = FakeProvider(resolves=(None,))

    outcome = worker(tmp_path, client, provider).run_once("DEVHUB")[0]

    assert (outcome.status, outcome.detail) == ("unknown", "prior_effect_unresolved")
    assert provider.launches == provider.resumes == 0




@pytest.mark.parametrize("profile", [
    ExecutionProfile("frontier", 301),
    ExecutionProfile("balanced", 300),
    ExecutionProfile("frontier", 300, concurrency_units=4),
])
def test_cost_floor_and_concurrency_are_refused_before_provider_effect(tmp_path, profile):
    client = FakeClient(command())
    provider = FakeProvider(profile=profile)

    with pytest.raises(CommandWorkerError, match="avant effet"):
        worker(tmp_path, client, provider).run_once("DEVHUB")
    assert provider.launches == 0
    assert provider.resumes == 0


def test_claim_transport_is_resolved_by_command_id_without_second_claim(tmp_path):
    client = FakeClient(command(), claim_transport=True)
    provider = FakeProvider()

    assert worker(tmp_path, client, provider).run_once("DEVHUB")[0].status == "completed"
    assert client.claim_calls == 1
    assert provider.launches == 1


def test_timeout_resolves_effect_identity_and_never_launches_twice(tmp_path):
    proof = EffectReceipt("command-1", "completed", DIGEST_A, 10, 20)
    client = FakeClient(command())
    provider = FakeProvider(launch_result=TimeoutError(), resolves=(None, proof))

    assert worker(tmp_path, client, provider).run_once("DEVHUB")[0].status == "completed"
    assert provider.launches == 1


def test_unresolved_timeout_stays_unknown_across_restart_without_blind_launch(tmp_path):
    client = FakeClient(command())
    first = FakeProvider(launch_result=TimeoutError(), resolves=(None, None))
    assert worker(tmp_path, client, first).run_once("DEVHUB")[0].status == "unknown"
    assert first.launches == 1

    client.item = replace(client.item, lease=None, state="unknown", version=client.item.version + 1)
    second = FakeProvider(resolves=(None,))
    assert worker(tmp_path, client, second).run_once("DEVHUB")[0].status == "unknown"
    assert second.launches == 0


def test_restart_resumes_durable_running_effect_without_a_second_launch(tmp_path):
    client = FakeClient(command())
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    running = FakeProvider(
        launch_result=EffectReceipt("command-1", "running", DIGEST_A, 10, 20),
    )
    first = CommandWorker(
        client, store, running, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    ).run_once("DEVHUB")
    assert first[0].status == "running"
    assert running.launches == 1

    client.item = replace(client.item, lease=None, state="unknown", version=client.item.version + 1)
    resumed = FakeProvider(resolves=(None,))
    second = CommandWorker(
        client, store, resumed, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    ).run_once("DEVHUB")
    assert second[0].status == "completed"
    assert resumed.launches == 0
    assert resumed.resumes == 1


def test_running_receipt_with_latest_stale_projection_never_resumes(tmp_path):
    prior = command(
        state="unknown", version=4,
        lease=CommandLease("old-lease", "worker-1", 20_000, 9_000),
    )
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    running = EffectReceipt("command-1", "running", DIGEST_A, 10, 20)
    store.record(
        prior, _binding_digest(prior), "effect-proven", lease_id="old-lease",
        effect=running, now_ms=9_000,
    )
    client = FakeClient(
        replace(prior, lease=None, version=5), heartbeat_result="stale-on-second",
    )
    provider = FakeProvider(resolves=(None,))

    outcome = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    ).run_once("DEVHUB")[0]

    assert (outcome.status, outcome.detail) == ("unknown", "identity_unresolved")
    assert client.item.projection_stale is True
    assert provider.launches == 0
    assert provider.resumes == 0
    assert store.get("command-1").effect == running


def test_terminal_durable_effect_cannot_be_downgraded_or_resumed(tmp_path):
    client = FakeClient(command())
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    claimed = command(lease=CommandLease("old-lease", "worker-1", 20_000, 9_000))
    terminal = EffectReceipt("command-1", "completed", DIGEST_A, 10, 20)
    store.record(
        claimed, _binding_digest(claimed), "terminal", lease_id="old-lease",
        effect=terminal, now_ms=9_000,
    )
    provider = FakeProvider(
        resolves=(EffectReceipt("command-1", "running", DIGEST_A, 10, 20),),
    )

    with pytest.raises(CommandWorkerError, match="terminal"):
        CommandWorker(
            client, store, provider, worker_id="worker-1", lease_seconds=60,
            now_ms=lambda: 10_000,
        ).run_once("DEVHUB")
    assert provider.launches == 0
    assert provider.resumes == 0
    assert store.get("command-1").effect == terminal


def test_restart_reconciles_terminal_effect_after_authority_expiry_without_provider_action(
    tmp_path,
):
    client = FakeClient(command())
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    # The binding is independent of lease/version. Prime it through a first worker
    # whose launch becomes durable but whose DevHub terminal event is interrupted.
    provider = FakeProvider()
    original_append = client.append_event
    def fail_only_terminal_event(*args, **kwargs):
        if kwargs["event_type"] == "completed":
            raise DevHubCommandTransportError(
                "POST", "/events", None, "command_unavailable_service",
            )
        return original_append(*args, **kwargs)

    client.append_event = fail_only_terminal_event
    client.list_events = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        DevHubCommandError("GET", "/events", None, "command_unavailable_service")
    )
    first = CommandWorker(
        client, store, provider, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    ).run_once("DEVHUB")
    assert first[0].status == "deferred"
    assert provider.launches == 1
    durable = store.get("command-1")
    assert durable is not None
    assert durable.phase == "terminal"
    assert durable.effect == provider.launch_result

    client.append_event = original_append
    client.item = replace(client.item, lease=None, state="unknown", version=client.item.version + 1)
    expired = observation(preview_expires_at=10_000, approval_expires_at=9_999)
    resumed = FakeProvider(
        resolves=(None,),
        observations=(expired,),
    )
    outcome = CommandWorker(
        client, store, resumed, worker_id="worker-1", lease_seconds=60,
        now_ms=lambda: 10_000,
    ).run_once("DEVHUB")[0]
    assert outcome.status == "completed"
    assert resumed.observe_calls == 0
    assert resumed.launches == 0
    assert resumed.resumes == 0
    assert [event[2:] for event in client.launch_events] == [
        ("accepted", None, None), ("running", None, None), ("completed", 120, 2_000),
    ]
