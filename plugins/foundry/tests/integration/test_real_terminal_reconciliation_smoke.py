"""Opt-in real HTTP smoke for original Epic closure and terminal projection.

The disposable nominal fixture needs one open Epic with complete or absent AC.
The late fixture instead needs exactly one ``- [ ] smoke acceptance`` marker and
sets ``DEVHUB_CLOSURE_SMOKE_LATE=1``. Both need terminal direct children and one
requested Dev Hub command bound to the Epic's open version. The operations under
test use the public Python adapters; no synthetic Tracker, provider, SQL transition,
cloud host, agent, PR, merge or budget seam is available.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import ipaddress
import json
import os
import time
import urllib.parse

import pytest

from foundry.devhub_commands import DevHubCommandClient
from foundry.campaign_runtime import CampaignCommandEffectProvider
from foundry.command_worker import _binding_digest
from foundry.execution_receipts import epic_closure_receipt
from foundry.models import EpicClosureChild, EpicClosureReceipt, Project
from foundry.trackers.devhub import DevHubTracker, DevHubTrackerError


_CONFIRM = "I_UNDERSTAND_CLOSURE_SMOKE_MUTATES_DISPOSABLE_LOOPBACK_DATA"
_LATE_ACCEPTANCE_MARKER = "- [ ] smoke acceptance"
_LATE_ACCEPTANCE_COMPLETED_MARKER = "- [x] smoke acceptance"
_TERMINAL_CONTRACT = "foundry-terminal-campaign-projection.v1"
pytestmark = pytest.mark.integration


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"variable smoke requise absente: {name}")
    return value


def _complete_late_acceptance(body: str) -> str:
    if body.count(_LATE_ACCEPTANCE_MARKER) != 1:
        raise ValueError(
            "fixture marqueur d'acceptation tardive invalide: "
            "exactement une occurrence requise",
        )
    return body.replace(
        _LATE_ACCEPTANCE_MARKER, _LATE_ACCEPTANCE_COMPLETED_MARKER, 1,
    )


def _loopback_url() -> str:
    value = _required("DEVHUB_CLOSURE_SMOKE_URL").rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname or ""
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("URL smoke DevHub loopback invalide") from None
    if (parsed.scheme != "http" or not address.is_loopback
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or parsed.username is not None or parsed.password is not None):
        raise ValueError("le smoke DevHub doit viser un HTTP loopback jetable")
    return value


def _closure_receipt(
    tracker: DevHubTracker, project: Project, epic_id: str,
) -> EpicClosureReceipt:
    parent = tracker.get_issue(epic_id)
    child_ids = sorted(
        relation.target for relation in parent.links
        if relation.type == "parent-of"
    )
    if (parent.id != epic_id or parent.state in {"done", "dropped"}
            or not isinstance(parent.type, str) or parent.type.casefold() != "epic"
            or parent.pr_url or parent.ac_done > parent.ac_total
            or (parent.ac_total > 0 and parent.ac_done != parent.ac_total)
            or not 1 <= len(child_ids) <= 100
            or len(child_ids) != len(set(child_ids))):
        raise ValueError("fixture parent smoke clôture invalide")
    children = []
    for child_id in child_ids:
        child = tracker.get_issue(child_id)
        if child.id != child_id or child.state not in {"done", "dropped"}:
            raise ValueError("fixture enfant smoke clôture invalide")
        children.append(EpicClosureChild(child.id, child.version, child.state))
    try:
        issued_at = int(_required("DEVHUB_CLOSURE_SMOKE_ISSUED_AT"))
    except ValueError:
        raise ValueError("horodatage smoke clôture invalide") from None
    return EpicClosureReceipt(
        project_key=project.key,
        project_id=project.id,
        parent_id=parent.id,
        parent_version=parent.version,
        parent_type=parent.type,
        parent_ac_done=parent.ac_done,
        parent_ac_total=parent.ac_total,
        children=tuple(children),
        issued_at=issued_at,
        nonce=_required("DEVHUB_CLOSURE_SMOKE_NONCE"),
    )


def _event_id(command_id: str, event_type: str) -> str:
    digest = hashlib.sha256(
        f"{command_id}\0{event_type}".encode("utf-8"),
    ).hexdigest()[:32]
    return f"closure-smoke-{event_type}-{digest}"


def _append_lifecycle(
    client: DevHubCommandClient, command, event_type: str, occurred_at: int,
):
    if command.lease is None:
        raise ValueError("lease smoke terminale absente")
    event_id = _event_id(command.id, event_type)
    return client.append_event(
        command, event_id=event_id, sequence=command.next_event_sequence,
        event_type=event_type, occurred_at=occurred_at,
        lease_id=command.lease.id, idempotency_key=event_id,
    )


@pytest.mark.skipif(
    os.environ.get("DEVHUB_CLOSURE_SMOKE_CONFIRM") != _CONFIRM,
    reason="real closure smoke not explicitly authorized",
)
def test_real_epic_closure_replay_and_terminal_projection_smoke():
    url = _loopback_url()
    tracker_token = _required("DEVHUB_CLOSURE_SMOKE_TRACKER_TOKEN")
    proof_secret = _required("DEVHUB_CLOSURE_SMOKE_PROOF_SECRET")
    project = Project(
        key=_required("DEVHUB_CLOSURE_SMOKE_PROJECT_KEY"),
        id=_required("DEVHUB_CLOSURE_SMOKE_PROJECT_ID"),
    )
    epic_id = _required("DEVHUB_CLOSURE_SMOKE_EPIC_ID")
    tracker = DevHubTracker(
        url=url, token=tracker_token, proof_secret=proof_secret,
    )
    command_client = DevHubCommandClient(
        url=url, token=_required("DEVHUB_CLOSURE_SMOKE_COMMAND_TOKEN"),
    )
    command_id = _required("DEVHUB_CLOSURE_SMOKE_COMMAND_ID")
    worker_id = _required("DEVHUB_CLOSURE_SMOKE_WORKER_ID")
    attempt_id = _required("DEVHUB_CLOSURE_SMOKE_ATTEMPT_ID")
    try:
        occurred_at = int(_required("DEVHUB_CLOSURE_SMOKE_ISSUED_AT"))
    except ValueError:
        raise ValueError("horodatage smoke clôture invalide") from None
    command = command_client.get_command(command_id)
    late = os.environ.get("DEVHUB_CLOSURE_SMOKE_LATE") == "1"
    if late:
        attempt_id = CampaignCommandEffectProvider._attempt_id(command, _binding_digest(command))
    else:
        requested = _closure_receipt(tracker, project, epic_id)
    if (command.epic_id != epic_id or command.state != "requested"
            or command.projection_stale or command.lease is not None):
        raise ValueError("fixture commande smoke demandée invalide")
    command = command_client.claim(
        command, worker_id=worker_id, lease_seconds=15 if late else 300,
        idempotency_key=f"closure-smoke-claim-{command.id}",
    )
    command = _append_lifecycle(
        command_client, command, "accepted", occurred_at,
    )
    command = _append_lifecycle(
        command_client, command, "running", occurred_at,
    )

    if late:
        # Exercise the real F102 acceptance-sync adapter. The terminal
        # publication boundary must see its durable foundry-ac receipt rather
        # than treating an arbitrary same-version PATCH as an approved lineage.
        parent = tracker.get_issue(epic_id)
        updated_body = _complete_late_acceptance(parent.body)
        synchronized = tracker.sync_acceptance_body(
            epic_id,
            parent.body,
            updated_body,
            hashlib.sha256(
                f"f103-late-smoke-sync:{command_id}".encode("utf-8"),
            ).hexdigest(),
            project=project,
        )
        assert synchronized is True
        requested = _closure_receipt(tracker, project, epic_id)
        assert requested.parent_version == parent.version + 1

    closed = tracker.close_epic(project, requested)
    assert closed.receipt == requested
    assert closed.closed_parent_version == requested.parent_version + 1
    assert closed.replayed is False

    fresh_tracker = DevHubTracker(
        url=url, token=tracker_token, proof_secret=proof_secret,
    )
    recovered = fresh_tracker.get_epic_closure(project, epic_id)
    replayed = fresh_tracker.close_epic(project, requested)
    assert recovered == replayed
    assert recovered is not None and recovered.receipt == requested
    assert recovered.audit_id == closed.audit_id
    assert recovered.replayed is replayed.replayed is True

    # A changed request carries the original pre-close version under a fresh
    # idempotency key. The public boundary rejects that stale If-Match before
    # considering a new closure receipt; it is still never a replacement.
    changed = replace(requested, issued_at=requested.issued_at + 1)
    with pytest.raises(DevHubTrackerError) as conflict:
        fresh_tracker.close_epic(project, changed)
    assert (conflict.value.status, conflict.value.code) == (
        409, "version_conflict",
    )

    if (command.state != "running" or command.projection_stale
            or command.lease is None or command.version < 1
            or command.next_event_sequence < 1):
        raise ValueError("commande smoke running invalide")

    projected = epic_closure_receipt(
        "devhub", epic_id, closed, observed_at=requested.issued_at,
    ).receipt
    receipts = {
        "pr": None, "review": None, "ci": None, "merge": None,
        "epic_closure": projected,
    }
    event_id = _event_id(command_id, "completed")
    if late:
        # Real expiry, not a mocked clock or a database state rewrite.
        remaining = command.lease.expires_at / 1000 - time.time()
        assert remaining <= 15
        if remaining > 0:
            time.sleep(remaining + 0.1)
        publication = {
            "publication_id": f"late-smoke-{command_id}", "event_id": event_id,
            "sequence": command.next_event_sequence, "occurred_at": requested.issued_at,
            "lease_id": command.lease.id, "attempt_id": attempt_id,
            "original_closure": json.loads(json.dumps({
                "schema_version": "devhub-epic-closure.v1",
                "outcome": {**asdict(recovered), "replayed": True},
            })), "cost_cents": None, "duration_ms": None,
        }
        reconciliation = command_client.publish_late_terminal(command, **publication)
        replay = command_client.publish_late_terminal(command, **publication)
        assert reconciliation == replay
        assert reconciliation.provider_effects == 0
        assert reconciliation.cost_cents is reconciliation.duration_ms is None
        terminal = command_client.get_command(command_id)
        assert terminal.state == "completed" and not terminal.projection_stale
        assert terminal.lease == command.lease  # no claim or renewal
        events = command_client.list_events(command_id, cursor="0", limit=100)
        completed = [item for item in events.items if item.type == "completed"]
        assert len(completed) == 1 and completed[0].receipts == receipts
        assert fresh_tracker.get_epic_closure(project, epic_id) == recovered
        terminal_receipt = command_client._request(
            "GET", f"/projects/{project.key}/epics/{epic_id}/terminal-receipt",
            expected_contract="devhub-foundry-terminal-receipt.v1",
        )
        assert terminal_receipt["command"]["id"] == command_id
        assert terminal_receipt["reconciliation"]["id"] == reconciliation.id
        assert terminal_receipt["reconciliation"]["provider_effects"] == 0
        assert terminal_receipt["command"]["preview_epic_version"] + 2 == closed.closed_parent_version
        return
    terminal = command_client.append_event(
        command, event_id=event_id, sequence=command.next_event_sequence,
        event_type="completed", occurred_at=requested.issued_at,
        lease_id=command.lease.id, cost_cents=None, duration_ms=None,
        attempt_id=attempt_id, issue_id=epic_id, receipts=receipts,
        idempotency_key=f"closure-smoke-event-{event_id}",
    )
    assert terminal.state == "completed"
    assert terminal.projection_stale is False

    events = command_client.list_events(command_id, cursor="0", limit=100)
    published = next(item for item in events.items if item.id == event_id)
    assert published.schema_version == "devhub-foundry-command-event.v1"
    assert published.provenance == {
        "command_id": command_id, "attempt_id": attempt_id,
        "issue_id": epic_id, "source": "foundry",
    }
    assert published.receipts == receipts

    identity = {
        "contract": _TERMINAL_CONTRACT,
        "command_id": command_id,
        "terminal_event_id": event_id,
        "closure_digest": projected["closure_digest"],
    }
    reconciliation_id = "reconcile-" + hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    reconciliation = command_client.reconcile_terminal(
        terminal, reconciliation_id=reconciliation_id,
        terminal_event_id=event_id,
        epic_version=closed.closed_parent_version,
    )
    replayed_reconciliation = command_client.reconcile_terminal(
        terminal, reconciliation_id=reconciliation_id,
        terminal_event_id=event_id,
        epic_version=closed.closed_parent_version,
    )
    assert replayed_reconciliation == reconciliation
    assert reconciliation.command_id == command_id
    assert reconciliation.terminal_event_id == event_id
    assert reconciliation.epic_id == epic_id
    assert reconciliation.reconciled_epic_version == closed.closed_parent_version
    assert reconciliation.closure_digest == projected["closure_digest"]
    assert reconciliation.provider_effects == 0
    assert reconciliation.cost_cents is reconciliation.duration_ms is None
