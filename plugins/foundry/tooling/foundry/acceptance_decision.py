"""Closed human acceptance decision of a non-code issue (PAT-206, PAT-ADR-0025).

The decision file is a closed JSON document.  This module only parses it, computes its
canonical digest and verifies its cited evidence by reading; it never writes and never
authenticates the person.  The size bound is the advisory comment bound
(``advisory_schema.MAX_COMMENT_BYTES``) reused for the lifecycle receipt: it is a retained
bound, not a qualification of the largest native Linear comment (unknown).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re

from foundry.advisory_schema import MAX_COMMENT_BYTES

SCHEMA = "foundry.issue-acceptance-decision.v1"
AUTHORITY = "maintainer-direct"
VERDICT = "accepted"
CRITERION_VERDICT = "pass"
DELIVERY = "none"
MAX_BYTES = MAX_COMMENT_BYTES
EVIDENCE_TYPES = ("advisory-report", "issue")

_TOP_KEYS = frozenset({
    "schema", "issue", "body_sha256", "ac_digest", "from_state", "authority",
    "decided_on", "verdict", "delivery", "criteria",
})
_CRITERION_KEYS = frozenset({"id", "digest", "verdict", "evidence", "unknowns"})
_EVIDENCE_KEYS = {
    "advisory-report": frozenset({"type", "issue", "attempt_id", "report_hash"}),
    "issue": frozenset({"type", "id"}),
}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ISSUE_ID = re.compile(r"[A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,8}\Z")
_CRITERION_ID = re.compile(r"ac-[1-9][0-9]{0,3}\Z")
# Native states a human decision may start from; the Linear tracker imports this set.
FROM_STATES = frozenset({"backlog", "ready", "in-progress"})


class AcceptanceDecisionError(ValueError):
    """The decision document is not a valid closed document."""


def _pairs(items):
    keys = [key for key, _value in items]
    if len(keys) != len(set(keys)):
        raise AcceptanceDecisionError("clé dupliquée dans le document de décision")
    return dict(items)


def _refuse_constant(_name):
    raise AcceptanceDecisionError("constante JSON non finie refusée")


def _text(value, label) -> str:
    if not isinstance(value, str) or not value:
        raise AcceptanceDecisionError(f"{label} : chaîne non vide requise")
    return value


def _digest(value, label) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise AcceptanceDecisionError(f"{label} : empreinte sha256 hexadécimale requise")
    return value


def _issue_id(value, label) -> str:
    if not isinstance(value, str) or _ISSUE_ID.fullmatch(value) is None:
        raise AcceptanceDecisionError(f"{label} : identifiant d'issue invalide")
    return value


def parse(raw, issue_id: str | None = None) -> dict:
    """Parse and structurally validate one decision document (no provider read)."""
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if not isinstance(data, (bytes, bytearray)):
        raise AcceptanceDecisionError("document de décision illisible")
    if len(data) > MAX_BYTES:
        raise AcceptanceDecisionError(
            f"document de décision trop grand ({len(data)} > {MAX_BYTES} octets)"
        )
    try:
        document = json.loads(
            bytes(data).decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=_refuse_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, AcceptanceDecisionError):
            raise
        raise AcceptanceDecisionError("document de décision JSON invalide") from None
    return validate(document, issue_id)


def validate(document, issue_id: str | None = None) -> dict:
    if not isinstance(document, dict) or set(document) != _TOP_KEYS:
        raise AcceptanceDecisionError("document de décision fermé : clés inattendues ou absentes")
    if document["schema"] != SCHEMA:
        raise AcceptanceDecisionError("schéma de décision inconnu")
    _issue_id(document["issue"], "issue")
    if issue_id is not None and document["issue"] != issue_id:
        raise AcceptanceDecisionError(
            f"la décision porte l'issue {document['issue']}, pas {issue_id}"
        )
    _digest(document["body_sha256"], "body_sha256")
    _digest(document["ac_digest"], "ac_digest")
    if document["from_state"] not in FROM_STATES:
        raise AcceptanceDecisionError("from_state : état natif observé non admis")
    if document["authority"] != AUTHORITY:
        raise AcceptanceDecisionError(f"autorité `{AUTHORITY}` requise")
    decided_on = document["decided_on"]
    try:
        valid_date = (
            isinstance(decided_on, str)
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", decided_on) is not None
            and datetime.date.fromisoformat(decided_on).isoformat() == decided_on
        )
    except ValueError:
        valid_date = False
    if not valid_date:
        raise AcceptanceDecisionError("decided_on : date AAAA-MM-JJ requise")
    if document["verdict"] != VERDICT:
        raise AcceptanceDecisionError(f"verdict `{VERDICT}` requis")
    if document["delivery"] != DELIVERY:
        raise AcceptanceDecisionError(f"delivery `{DELIVERY}` requis")
    criteria = document["criteria"]
    if not isinstance(criteria, list) or not criteria:
        raise AcceptanceDecisionError("criteria : liste non vide requise")
    for index, criterion in enumerate(criteria, start=1):
        label = f"critère {index}"
        if not isinstance(criterion, dict) or set(criterion) != _CRITERION_KEYS:
            raise AcceptanceDecisionError(f"{label} : clés inattendues ou absentes")
        if (
            not isinstance(criterion["id"], str)
            or _CRITERION_ID.fullmatch(criterion["id"]) is None
        ):
            raise AcceptanceDecisionError(f"{label} : identifiant invalide")
        _digest(criterion["digest"], f"{label} digest")
        if criterion["verdict"] != CRITERION_VERDICT:
            raise AcceptanceDecisionError(f"{label} : verdict `{CRITERION_VERDICT}` requis")
        evidence = criterion["evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise AcceptanceDecisionError(f"{label} : au moins une preuve requise")
        for item in evidence:
            _validate_evidence(item, label, document["issue"])
        unknowns = criterion["unknowns"]
        if not isinstance(unknowns, list):
            raise AcceptanceDecisionError(f"{label} : liste d'inconnues requise")
        for unknown in unknowns:
            _text(unknown, f"{label} inconnue")
    if len(canonical(document).encode("ascii")) > MAX_BYTES:
        raise AcceptanceDecisionError("document de décision au-delà de la borne de taille")
    return document


def _validate_evidence(item, label, own_issue) -> None:
    if not isinstance(item, dict) or item.get("type") not in _EVIDENCE_KEYS:
        raise AcceptanceDecisionError(
            f"{label} : type de preuve hors liste fermée ({', '.join(EVIDENCE_TYPES)})"
        )
    if set(item) != _EVIDENCE_KEYS[item["type"]]:
        raise AcceptanceDecisionError(f"{label} : clés de preuve inattendues ou absentes")
    if item["type"] == "advisory-report":
        _issue_id(item["issue"], f"{label} preuve issue")
        _digest(item["attempt_id"], f"{label} preuve attempt_id")
        _digest(item["report_hash"], f"{label} preuve report_hash")
    else:
        _issue_id(item["id"], f"{label} preuve id")
        if item["id"] == own_issue:
            raise AcceptanceDecisionError(f"{label} : une issue ne se cite pas elle-même")


def canonical(document: dict) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def decision_digest(document: dict) -> str:
    return hashlib.sha256(canonical(document).encode("ascii")).hexdigest()


def expected_criteria(body: str) -> list[dict]:
    """The ordered ``{id, digest}`` identities of the body's criteria (routing)."""
    from foundry.routing import acceptance_criteria

    return acceptance_criteria(body)


def check_against_body(document: dict, body: str) -> None:
    """Refuse any drift between the decision and the body it was written for."""
    from foundry.routing import RoutingConfigError, acceptance_digest

    if hashlib.sha256(body.encode()).hexdigest() != document["body_sha256"]:
        raise AcceptanceDecisionError("le corps a changé depuis la décision (body_sha256)")
    try:
        expected = expected_criteria(body)
    except RoutingConfigError:
        raise AcceptanceDecisionError("aucun critère d'acceptation lisible") from None
    got = [{"id": item["id"], "digest": item["digest"]} for item in document["criteria"]]
    if got != expected or document["ac_digest"] != acceptance_digest(expected):
        raise AcceptanceDecisionError(
            "les critères de la décision diffèrent de ceux du corps (texte, ordre ou nombre)"
        )


def verify_evidence(document: dict, tracker, project) -> None:
    """Read-only check that every cited evidence exists in the same project.

    An advisory report counts only when ``postmerge_review.restituted_reports`` returns
    it (confirmed, three records, equal hashes); an isolated or unconfirmed record, or
    another hash, is refused.  Nothing is written.
    """
    from foundry import postmerge_review

    reports: dict[str, list] = {}
    checked: set[str] = set()
    for index, criterion in enumerate(document["criteria"], start=1):
        for item in criterion["evidence"]:
            if item["type"] == "issue":
                target = item["id"]
                if target not in checked:
                    tracker.validate_issue_binding(project, target)
                    checked.add(target)
                continue
            target = item["issue"]
            if target not in checked:
                tracker.validate_issue_binding(project, target)
                checked.add(target)
            if target not in reports:
                reports[target] = postmerge_review.restituted_reports(tracker, target)
            if not any(
                report.get("attempt_id") == item["attempt_id"]
                and report.get("report_hash") == item["report_hash"]
                for report in reports[target]
            ):
                raise AcceptanceDecisionError(
                    f"critère {index} : rapport consultatif confirmé introuvable "
                    f"({target}, tentative {item['attempt_id'][:12]})"
                )
