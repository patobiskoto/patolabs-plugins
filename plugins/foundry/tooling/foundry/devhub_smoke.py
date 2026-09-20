"""Explicitly gated destructive smoke for a disposable DevHubTracker instance."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re

from foundry.models import TransitionContext
from foundry.trackers.devhub import DevHubTracker, DevHubTrackerError


_CONFIRM = "I_UNDERSTAND_DEVHUB_SMOKE_CREATES_DATA"


def _require_audit_receipts(events, *, resource, operations):
    missing = []
    for operation in operations:
        if not any(
            event.get("operation") == operation
            and event.get("resource") == resource
            and event.get("result") == "success"
            and type(event.get("resource_version")) is int
            and event["resource_version"] > 0
            for event in events
        ):
            missing.append(operation)
    if missing:
        raise RuntimeError(f"reçus audit DevHub manquants : {', '.join(missing)}")


def _expect_error(call, *, status, code):
    try:
        call()
    except DevHubTrackerError as error:
        if error.status == status and error.code == code:
            return code
        raise RuntimeError(f"erreur DevHub inattendue : {error.status}/{error.code}") from None
    raise RuntimeError(f"DevHub devait refuser avec {status}/{code}")


def run_smoke(tracker: DevHubTracker, *, key: str, repository: str, name: str) -> dict:
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", key):
        raise ValueError("smoke ticker invalide")
    slug = f"foundry-smoke-{key.lower()}"
    raw_project = tracker._req(
        "POST", "/projects",
        {"name": name, "slug": slug, "ticker": key, "repository": repository},
        idempotent=True,
    )
    project = tracker._to_project(raw_project)
    target = tracker.create_issue(
        project, "Foundry DevHubTracker smoke target", "Disposable link target",
        fields={"State": "backlog", "Priority": "P3", "Type": "Task"},
    )
    issue = tracker.create_issue(
        project, "Foundry DevHubTracker smoke", "- [ ] verify provider",
        fields={"State": "backlog", "Priority": "P3", "Type": "Task"},
    )
    tracker.set_state(issue.id, "ready", project=project)
    tracker.set_state(issue.id, "in-progress", project=project)
    tracker.link(issue.id, "depends-on", target.id, project=project)
    tracker.add_comment(issue.id, "Foundry smoke round-trip", project=project)
    round_trip = tracker.get_issue(issue.id)
    link_round_trip = any(
        link.type == "depends-on" and link.direction == "outward" and link.target == target.id
        for link in round_trip.links
    )
    comment_round_trip = any(
        comment.get("text") == "Foundry smoke round-trip" for comment in round_trip.comments
    )
    if not link_round_trip or not comment_round_trip:
        raise RuntimeError("round-trip lien/commentaire DevHub incomplet")

    current_version = tracker._issue_raw(issue.id)["version"]
    stale = _expect_error(
        lambda: tracker._req(
            "POST", f"/issues/{issue.id}/comments", {"text": "stale must not persist"},
            version=current_version - 1, idempotent=True,
            idempotency_key=f"smoke-stale-{key.lower()}",
        ),
        status=409, code="version_conflict",
    )
    replay_key = f"smoke-replay-{key.lower()}"
    replay_body = {"text": "Foundry smoke idempotent replay"}
    replayed = tracker._req(
        "POST", f"/issues/{issue.id}/comments", replay_body,
        version=current_version, idempotent=True, idempotency_key=replay_key,
    )
    replay_copy = tracker._req(
        "POST", f"/issues/{issue.id}/comments", replay_body,
        version=current_version, idempotent=True, idempotency_key=replay_key,
    )
    if replay_copy != replayed:
        raise RuntimeError("replay idempotent DevHub divergent")
    divergent = _expect_error(
        lambda: tracker._req(
            "POST", f"/issues/{issue.id}/comments", {"text": "divergent replay"},
            version=current_version, idempotent=True, idempotency_key=replay_key,
        ),
        status=409, code="idempotency_conflict",
    )

    concurrent_version = replayed["version"]

    def contender(index):
        try:
            result = tracker._req(
                "POST", f"/issues/{issue.id}/comments",
                {"text": f"Foundry smoke contender {index}"},
                version=concurrent_version, idempotent=True,
                idempotency_key=f"smoke-concurrent-{key.lower()}-{index}",
            )
            return "success", result
        except DevHubTrackerError as error:
            return error.code, error.status

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        concurrency = list(executor.map(contender, (1, 2)))
    concurrency_codes = sorted(item[0] for item in concurrency)
    if concurrency_codes != ["success", "version_conflict"]:
        raise RuntimeError(f"sémantique concurrence DevHub inattendue : {concurrency_codes}")

    pr_url = f"https://github.com/foundry-smoke/{key.lower()}/pull/1"
    tracker.update_fields(issue.id, {"GitHub PR": pr_url}, project=project)
    head = hashlib.sha256(f"{key}:head".encode()).hexdigest()[:40]
    base = hashlib.sha256(f"{key}:base".encode()).hexdigest()[:40]
    digest = hashlib.sha256(f"{key}:diff".encode()).hexdigest()
    review = TransitionContext(
        pr_url=pr_url, head_sha=head, base_sha=base, review_digest=digest,
    )
    tracker.set_state(issue.id, "review", context=review, project=project)
    tracker.set_state(
        issue.id, "done",
        context=TransitionContext(
            pr_url=pr_url, head_sha=head, review_digest=digest,
            base_sha=base,
            merge_sha=hashlib.sha256(f"{key}:merge".encode()).hexdigest()[:40],
        ),
        project=project,
    )
    adr = tracker.create_adr(project, f"{key} smoke boundary", "Disposable smoke decision")
    tracker.set_adr_status(adr, "accepted", project=project)
    hydrated = tracker.get_issue(issue.id)
    adrs = tracker.list_adrs(project)
    listed = tracker.search(project)
    listed_issue = next((item for item in listed if item.id == issue.id), None)
    listed_target = next((item for item in listed if item.id == target.id), None)
    search_link_round_trip = listed_issue is not None and any(
        link.type == "depends-on" and link.direction == "outward" and link.target == target.id
        for link in listed_issue.links
    )
    search_inverse_round_trip = listed_target is not None and any(
        link.type == "blocks" and link.direction == "inward" and link.target == issue.id
        for link in listed_target.links
    )
    if not search_link_round_trip or not search_inverse_round_trip:
        raise RuntimeError("recherche DevHub a perdu le graphe de dépendances")
    issue_resource = f"issue:{issue.id}"
    issue_audit = tracker.audit(project, resource=issue_resource)
    _require_audit_receipts(
        issue_audit, resource=issue_resource,
        operations={"issue.create", "issue.update", "issue.transition", "issue.link", "issue.comment"},
    )
    adr_resource = f"adr:{adr.id}"
    adr_audit = tracker.audit(project, resource=adr_resource)
    _require_audit_receipts(
        adr_audit, resource=adr_resource, operations={"adr.create", "adr.status"},
    )
    return {
        "contract": "devhub-tracker.v1",
        "project": project.key,
        "issue": hydrated.id,
        "state": hydrated.state,
        "issue_visible": any(item.id == issue.id for item in listed),
        "adr_visible": any(item.id == adr.id and item.status == "accepted" for item in adrs),
        "link_round_trip": link_round_trip,
        "search_link_round_trip": search_link_round_trip,
        "search_inverse_round_trip": search_inverse_round_trip,
        "comment_round_trip": comment_round_trip,
        "stale_etag": stale,
        "divergent_replay": divergent,
        "concurrency": {"success": concurrency_codes.count("success"),
                        "version_conflict": concurrency_codes.count("version_conflict")},
        "audit_verification": "public_http",
        "audit_receipts": len(issue_audit) + len(adr_audit),
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Run the destructive DevHubTracker smoke")
    parser.add_argument("--key", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--name", default="Foundry DevHubTracker smoke")
    args = parser.parse_args(argv)
    if os.environ.get("DEVHUB_SMOKE_CONFIRM") != _CONFIRM:
        raise SystemExit(
            f"⛔ Smoke refusé — DEVHUB_SMOKE_CONFIRM={_CONFIRM} requis pour cette création jetable."
        )
    result = run_smoke(DevHubTracker(), key=args.key, repository=args.repository, name=args.name)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
