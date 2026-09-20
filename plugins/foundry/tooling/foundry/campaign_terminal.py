"""Evidence-only projection of a proven terminal campaign into DevHub.

This module has no provider, lease, gate, PR or merge port.  Its only mutation is
the append-only DevHub terminal-reconciliation receipt after both local and remote
terminal evidence have been revalidated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import foundry
from foundry import write
from foundry.campaign_coordinator import (
    LEGACY_F89_CAMPAIGN_ID,
    CampaignSpec,
    LegacyParentRequalification,
    _digest,
)
from foundry.campaign_legacy import (
    LegacyCampaignRequalificationError,
    _closure_digest,
    _legacy_f89_record,
    _preflight_legacy_f89_evidence,
    _read_mapping as _read_legacy_mapping,
    _validated_requalification_request,
)
from foundry.devhub_commands import (
    COMMAND_EVENT_CONTRACT,
    DevHubCommand,
    DevHubCommandClient,
    DevHubCommandError,
    TerminalReconciliation,
)
from foundry.routing import acceptance_criteria, acceptance_digest


_MAX_EVENT_PAGES = 100
_MAX_EVENT_ITEMS = 10_000
_PROJECTION_CONTRACT = "foundry-terminal-campaign-projection.v1"
_CLOSURE_CONTRACT = "devhub-foundry-epic-closure.v1"


class TerminalCampaignReconciliationError(RuntimeError):
    """The terminal projection is unavailable, incomplete or contradictory."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _devhub_closure_digest(coordinate: dict[str, object]) -> str:
    return hashlib.sha256(_canonical({
        "contract": _CLOSURE_CONTRACT,
        **coordinate,
    })).hexdigest()


def _require_command_binding(command: DevHubCommand, spec: CampaignSpec) -> None:
    expected = {
        "id": spec.command_id,
        "project": spec.project,
        "epic_id": spec.epic_id,
        "preview_id": spec.preview_id,
        "approval_id": spec.approval_id,
        "planning_version_id": spec.planning_version_id,
        "preview_digest": spec.preview_digest,
        "snapshot_digest": spec.snapshot_digest,
        "policy_digest": spec.policy_digest,
        "max_cost_cents": spec.budget_cents,
        "max_concurrency": spec.max_concurrency,
        "scheduled_for": spec.scheduled_for,
    }
    if any(getattr(command, field) != value for field, value in expected.items()):
        raise TerminalCampaignReconciliationError(
            "binding commande/campagne terminale contradictoire"
        )
    if command.state != "completed" or command.projection_stale:
        raise TerminalCampaignReconciliationError(
            "commande DevHub non terminale ou projection stale"
        )


def _verified_legacy_f89_record(
    campaign_directory: str | Path,
    mapping: tuple[tuple[str, str, str], ...], *, actor: str, tracker,
) -> tuple[CampaignSpec, LegacyParentRequalification]:
    """Verify the exact journaled F102 proof without opening SQLite."""
    candidate, normalized_mapping = _validated_requalification_request(
        campaign_directory, mapping, actor,
    )
    _raw, evidence, _database_identities = _preflight_legacy_f89_evidence(
        candidate,
    )
    existing = evidence.existing_requalification
    if existing is None:
        raise LegacyCampaignRequalificationError(
            "requalification legacy F89 absente"
        )
    merge_by_issue = {
        issue_id: (effect_id, proof_digest)
        for issue_id, _attempt, effect_id, _work_id, proof_digest
        in evidence.merges
    }
    try:
        qualified = tuple(
            (
                criterion_id, criterion_digest, issue_id,
                merge_by_issue[issue_id][0], merge_by_issue[issue_id][1],
            )
            for criterion_id, criterion_digest, issue_id in normalized_mapping
        )
    except KeyError:
        raise LegacyCampaignRequalificationError(
            "recu merge legacy F89 invalide"
        ) from None
    # The terminal operator is not the author of the F102 audit. Rebuild the
    # digest with the immutable journaled author/time and require exact equality.
    observed = _legacy_f89_record(
        evidence.spec, qualified, actor=existing.actor, tracker=tracker,
        recorded_at=existing.recorded_at,
    )
    if observed != existing:
        raise LegacyCampaignRequalificationError(
            "requalification legacy F89 modifiee"
        )
    return evidence.spec, existing


def _current_closure_coordinate(
    tracker, record: LegacyParentRequalification,
) -> dict[str, object]:
    """Rebuild the DevHub coordinate from current provider facts and F102 proof."""
    try:
        parent = tracker.get_issue(record.parent_id)
        body = getattr(parent, "body", None)
        criteria = acceptance_criteria(body) if isinstance(body, str) else []
        linked_children = tuple(sorted(
            relation.target for relation in getattr(parent, "links", ())
            if getattr(relation, "type", None) == "parent-of"
        ))
        proven_children = tuple(sorted(item[2] for item in record.criteria))
        if (getattr(parent, "id", None) != record.parent_id
                or not isinstance(getattr(parent, "type", None), str)
                or parent.type.casefold() != "epic"
                or getattr(parent, "state", None) != "done"
                or getattr(parent, "version", None) != record.closed_version
                or not isinstance(body, str) or _digest(body) != record.parent_body_digest
                or acceptance_digest(criteria) != record.parent_ac_digest
                or {(item[0], item[1]) for item in record.criteria} != {
                    (criterion["id"], criterion["digest"]) for criterion in criteria
                }
                or linked_children != proven_children
                or len(linked_children) != len(set(linked_children))):
            raise ValueError
        project = write._epic_closure_project(tracker)
        outcome = write._validate_epic_outcome(
            tracker.get_epic_closure(project, record.parent_id),
            project=project, parent=parent, expected=None,
        )
    except (AttributeError, KeyError, SystemExit, TypeError, ValueError):
        raise TerminalCampaignReconciliationError(
            "parent, enfants ou AC de clôture modifiés"
        ) from None
    if (outcome.receipt.parent_version != record.acceptance_version
            or outcome.closed_parent_version != record.closed_version
            or _closure_digest(outcome) != record.closure_receipt_digest
            or tuple(child.id for child in outcome.receipt.children) != linked_children):
        raise TerminalCampaignReconciliationError(
            "reçu de clôture parent contradictoire"
        )
    children = []
    for receipt_child in outcome.receipt.children:
        try:
            current = tracker.get_issue(receipt_child.id)
        except Exception:
            raise TerminalCampaignReconciliationError(
                "enfant terminal actuel indisponible"
            ) from None
        if (getattr(current, "id", None) != receipt_child.id
                or getattr(current, "version", None) != receipt_child.version
                or getattr(current, "state", None) != receipt_child.state
                or receipt_child.state not in {"done", "dropped"}):
            raise TerminalCampaignReconciliationError(
                "enfant terminal actuel modifié"
            )
        children.append({
            "issue_id": receipt_child.id,
            "version": receipt_child.version,
            "state": receipt_child.state,
        })
    coordinate: dict[str, object] = {
        "epic_id": record.parent_id,
        "state": "closed",
        "receipt_id": outcome.audit_id,
        "parent_version": outcome.closed_parent_version,
        "children": children,
    }
    coordinate["closure_digest"] = _devhub_closure_digest(coordinate)
    return coordinate


def _terminal_event(
    client: DevHubCommandClient, command: DevHubCommand, event_id: str,
):
    cursor = "0"
    visited = {cursor}
    identities: set[tuple[str, int]] = set()
    found = None
    projection = None
    count = 0
    for _page in range(_MAX_EVENT_PAGES):
        try:
            page = client.list_events(command.id, cursor=cursor, limit=100)
        except DevHubCommandError as exc:
            raise TerminalCampaignReconciliationError(
                "journal terminal DevHub indisponible"
            ) from exc
        current_projection = (page.projection_stale, page.next_sequence)
        if projection is not None and current_projection != projection:
            raise TerminalCampaignReconciliationError(
                "journal terminal DevHub modifié pendant la lecture"
            )
        projection = current_projection
        if page.projection_stale:
            raise TerminalCampaignReconciliationError(
                "journal terminal DevHub stale"
            )
        for item in page.items:
            count += 1
            identity = (item.id, item.sequence)
            if count > _MAX_EVENT_ITEMS or identity in identities:
                raise TerminalCampaignReconciliationError(
                    "journal terminal DevHub hors borne ou dupliqué"
                )
            identities.add(identity)
            if item.id == event_id:
                if found is not None:
                    raise TerminalCampaignReconciliationError(
                        "événement terminal DevHub dupliqué"
                    )
                found = item
        if page.next_cursor is None:
            break
        if page.next_cursor in visited:
            raise TerminalCampaignReconciliationError(
                "pagination du journal terminal DevHub invalide"
            )
        cursor = page.next_cursor
        visited.add(cursor)
    else:
        raise TerminalCampaignReconciliationError(
            "journal terminal DevHub hors borne"
        )
    if found is None:
        raise TerminalCampaignReconciliationError(
            "événement terminal DevHub absent"
        )
    return found


def reconcile_legacy_f89_terminal(
    campaign_directory: str | Path,
    mapping: tuple[tuple[str, str, str], ...], *,
    actor: str, terminal_event_id: str, tracker=None,
    client: DevHubCommandClient | None = None, now_ms=None,
) -> TerminalReconciliation:
    """Project the exact F89 v4->v6 history without any execution-side effect."""
    active_tracker = tracker or foundry.tracker()
    active_client = client or DevHubCommandClient()
    try:
        spec, record = _verified_legacy_f89_record(
            campaign_directory, mapping, actor=actor, tracker=active_tracker,
        )
    except (LegacyCampaignRequalificationError, OSError, ValueError) as exc:
        raise TerminalCampaignReconciliationError(str(exc)) from None
    if (spec.campaign_id != LEGACY_F89_CAMPAIGN_ID
            or spec.binding_digest != record.binding_digest
            or spec.snapshot_digest != record.snapshot_digest):
        raise TerminalCampaignReconciliationError(
            "preuve locale terminale absente ou contradictoire"
        )
    try:
        command = active_client.get_command(record.campaign_id)
    except DevHubCommandError as exc:
        raise TerminalCampaignReconciliationError(
            "commande DevHub terminale indisponible"
        ) from exc
    _require_command_binding(command, spec)
    closure = _current_closure_coordinate(active_tracker, record)
    event = _terminal_event(active_client, command, terminal_event_id)
    expected_provenance = {
        "command_id": command.id,
        "issue_id": command.epic_id,
        "source": "foundry",
    }
    if (event.type != "completed"
            or event.schema_version != COMMAND_EVENT_CONTRACT
            or event.command_id != command.id
            or event.provenance is None
            or any(event.provenance.get(key) != value for key, value in expected_provenance.items())
            or event.receipts is None
            or event.receipts.get("epic_closure") != closure):
        raise TerminalCampaignReconciliationError(
            "attestation terminale DevHub contradictoire"
        )

    # Re-read every mutable provider/local coordinate immediately before the
    # sole remote mutation. DevHub repeats the graph/version check atomically.
    try:
        confirmed_spec, confirmed = _verified_legacy_f89_record(
            campaign_directory, mapping, actor=actor, tracker=active_tracker,
        )
    except (LegacyCampaignRequalificationError, OSError, ValueError) as exc:
        raise TerminalCampaignReconciliationError(str(exc)) from None
    if confirmed_spec != spec or confirmed != record:
        raise TerminalCampaignReconciliationError(
            "preuve locale terminale modifiée"
        )
    if _current_closure_coordinate(active_tracker, confirmed) != closure:
        raise TerminalCampaignReconciliationError(
            "preuve de clôture modifiée avant projection"
        )
    identity_digest = _digest({
        "contract": _PROJECTION_CONTRACT,
        "command_id": command.id,
        "terminal_event_id": event.id,
        "local_evidence_digest": record.evidence_digest,
        "closure_digest": closure["closure_digest"],
    })
    reconciliation_id = f"reconcile-{identity_digest}"
    try:
        result = active_client.reconcile_terminal(
            command, reconciliation_id=reconciliation_id,
            terminal_event_id=event.id, epic_version=record.closed_version,
        )
    except DevHubCommandError as exc:
        raise TerminalCampaignReconciliationError(
            f"projection terminale DevHub refusée: {exc.code}"
        ) from exc
    if (result.preview_epic_version != record.from_version
            or result.reconciled_epic_version != record.closed_version
            or result.closure_digest != closure["closure_digest"]
            or result.cost_cents != event.cost_cents
            or result.duration_ms != event.duration_ms
            or result.provider_effects != 0):
        raise TerminalCampaignReconciliationError(
            "reçu de projection terminale DevHub contradictoire"
        )
    return result


def _read_mapping(path: str | Path) -> tuple[tuple[str, str, str], ...]:
    raw = _read_legacy_mapping(path)
    if not isinstance(raw, list) or any(
        not isinstance(item, dict)
        or set(item) != {"criterion_id", "criterion_digest", "issue_id"}
        for item in raw
    ):
        raise ValueError("mapping terminal invalide")
    return tuple(
        (item["criterion_id"], item["criterion_digest"], item["issue_id"])
        for item in raw
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Projette la preuve terminale F89 dans le journal DevHub",
    )
    parser.add_argument("--campaign-dir", required=True)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--terminal-event-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = reconcile_legacy_f89_terminal(
            args.campaign_dir, _read_mapping(args.mapping), actor=args.actor,
            terminal_event_id=args.terminal_event_id,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError,
            TerminalCampaignReconciliationError) as exc:
        raise SystemExit(f"réconciliation terminale refusée: {exc}") from None
    print(json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":
    main()
