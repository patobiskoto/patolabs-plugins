"""Per-turn review data of one issue, read-only and offline (PLP-318).

A turn is one non-carried acceptance proof of the issue (``AcceptanceProofStore`` in
``routing.py``). Read side only: the proof store, the review ledger (``ReviewDeduplicator``
directory, for the completion time), the campaign remarks receipt (``review_remarks``) and
the PLP-316 invocation record (``invocation_log``) are read and never written; the same
state gives the same result. No network, tracker, clock or git call, no routing or gate
input. A value that no local source records is ``None`` and the turn's ``unrecorded`` maps
the field to the reason; ``SOURCES`` names where each recorded value comes from. The output
carries no proof, claim or invocation identifier, no remark text, prompt or path.
"""
from __future__ import annotations

import json
import re

from foundry import invocation_log, review_remarks
from foundry.review_rounds import _ledger_times
from foundry.routing import AcceptanceProofStore, ReviewDeduplicator, RoutingConfigError

SCHEMA = "review-turns.v1"
SOURCES = {
    "rank": "completed_at of the review ledger record bound to the proof diff hash, then generation",
    "head": "acceptance proof coordinates.head",
    "base": "acceptance proof coordinates.base",
    "type": "not recorded: previous_head is written only to the packet coordinates.json",
    "verdict": "acceptance proof quality and criteria verdicts",
    "remarks": "campaign remarks receipt bound to the proof identifier (review_remarks.py)",
    "reviewer_model": "first reviewer record of the invocation log for the issue and head (model requested)",
}
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _remarks(store: review_remarks.ReviewRemarksStore, proof: dict) -> tuple[int | None, str | None]:
    try:
        text = (store.directory / proof["proof_id"]).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "no_campaign_receipt"
    except (OSError, UnicodeDecodeError):
        return None, "receipt_unreadable"
    try:
        receipt = json.loads(text)
        remarks = review_remarks.validate_remarks(receipt["remarks"])
        if (receipt["proof_id"] != proof["proof_id"] or receipt["issue"] != proof["issue"]["id"]
                or receipt["diff_hash"] != proof["coordinates"]["diff_hash"]
                or receipt["generation"] != proof["review"]["generation"]):
            raise ValueError
    except (ValueError, KeyError, TypeError, review_remarks.ReviewRemarksError):
        return None, "receipt_unreadable"
    return len(remarks), None


def report(issue: str, repository: str, *, state_dir=None) -> dict:
    proofs = AcceptanceProofStore(repository, state_dir)
    ledger_dir = ReviewDeduplicator(repository, state_dir).directory
    remarks_store = review_remarks.ReviewRemarksStore(repository, state_dir)
    readable = invocation_log.records_by_id(repository=repository, state_dir=state_dir) is not None
    markers = sorted(p for p in proofs.directory.iterdir() if not p.name.startswith(".")) \
        if proofs.directory.is_dir() else []
    turns, carried, unreadable = [], 0, 0
    for marker in markers:
        try:
            proof = proofs.validated_proof(marker)
        except RoutingConfigError:
            unreadable += 1
            continue
        if proof["issue"]["id"] != issue:
            continue
        if "carried_from" in proof:
            carried += 1
            continue
        coordinates = proof["coordinates"]
        verdicts = [c["verdict"] for c in proof["issue"]["criteria"]]
        passed = sum(v == "pass" for v in verdicts)
        _, completed = _ledger_times(ledger_dir, coordinates["diff_hash"])
        unrecorded = {"type": "previous_head_not_persisted"}
        count, why = _remarks(remarks_store, proof)
        if why:
            unrecorded["remarks"] = why
        base = coordinates["base"] if _SHA.fullmatch(coordinates["base"]) else None
        found = invocation_log.invocations_for_diff(
            issue, coordinates["head"], base, repository=repository,
            state_dir=state_dir) if readable else None
        reviewer = found["reviewer"] if found else None
        if reviewer is None:
            unrecorded["reviewer_model"] = ("invocation_log_unreadable" if not readable
                                            else "no_reviewer_record_for_head")
        turns.append({
            "generation": proof["review"]["generation"], "head": coordinates["head"],
            "base": coordinates["base"], "type": None, "completed_at": completed,
            "verdict": "validated" if proof["quality"] == "mergeable" and passed == len(verdicts)
            else "blocked",
            "quality": proof["quality"], "criteria_pass": passed, "criteria": len(verdicts),
            "remarks": count,
            "reviewer_model": reviewer["model_requested"] if reviewer else None,
            "reviewer_effort": reviewer["effort"] if reviewer else None,
            "reviewer_host": reviewer["host"] if reviewer else None,
            "unrecorded": unrecorded,
        })
    turns.sort(key=lambda t: (t["completed_at"] is None, t["completed_at"] or "", t["generation"]))
    for rank, turn in enumerate(turns, start=1):
        turn["rank"] = rank
    return {"schema": SCHEMA, "issue": issue, "repository": repository, "sources": SOURCES,
            "carried_proofs": carried, "unreadable_proofs": unreadable, "turns": turns}
