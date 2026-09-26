"""Write tier — deterministic setters + the invariants that must NEVER be skipped.

The old design left "CI must be green before merge" in prose (a skill instruction);
a rushed session could skip it. Here it's a function that refuses. The rule of thumb:
mechanical invariants live in code (this file); judgment lives in the skills.
"""

from __future__ import annotations

import re
import secrets
import time

from foundry import registry
from foundry.models import (
    EpicClosureChild,
    EpicClosureOutcome,
    EpicClosureReceipt,
    Issue,
    Project,
)
from foundry.routing import synchronize_acceptance_body
from foundry.trackers.base import (
    AcceptanceSyncUnavailableError,
    EpicClosureUnavailableError,
)

ALLOWED_STATES = {
    "backlog",
    "ready",
    "in-progress",
    "review",
    "blocked",
    "done",
    "dropped",
}
EPIC_CHILD_TERMINAL_STATES = frozenset({"done", "dropped"})
_SAFE_RECEIPT_NONCE = re.compile(r"[A-Za-z0-9_-]{16,128}")
_SAFE_AUDIT_ID = re.compile(r"[A-Za-z0-9._:-]{1,160}")


def validate_state(state: str) -> str:
    if state not in ALLOWED_STATES:
        raise SystemExit(
            f"État illégal : '{state}'. Autorisés : {', '.join(sorted(ALLOWED_STATES))}."
        )
    return state


def mutation_project(tracker):
    """Resolve the current repo binding for adapters that require write isolation."""
    if not getattr(tracker, "requires_mutation_binding", False):
        legacy_validator = getattr(tracker, "validate_legacy_mutation", None)
        if callable(legacy_validator):
            legacy_validator()
        return None
    repo = registry.repo_basename()
    validator = getattr(tracker, "validate_mutation_repository", None)
    if not callable(validator):
        return tracker.resolve_project(repo)
    try:
        checkout_identity = registry.checkout_repository_identity()
    except ValueError as exc:
        raise SystemExit(
            "Binding de mutation refusé : identité du remote origin invalide ou absente."
        ) from exc
    validator(repo, checkout_identity)
    resolver = getattr(tracker, "resolve_checkout_project", None)
    if callable(resolver):
        project = resolver(checkout_identity=checkout_identity)
    else:
        project = tracker.resolve_project(repo)
    project_validator = getattr(tracker, "validate_mutation_project", None)
    if callable(project_validator):
        project_validator(project)
    return project


def preflight_issue_operation(tracker, operation: str) -> None:
    """Run the provider's no-effect lifecycle capability preflight."""
    preflight = getattr(tracker, "preflight_issue_operation", None)
    if callable(preflight):
        preflight(operation)


def preflight_merge_effect(tracker) -> None:
    """Run a provider-specific, read-only check immediately before a merge effect."""
    preflight = getattr(tracker, "preflight_merge_effect", None)
    if callable(preflight):
        preflight()


def issue_binding(tracker, *issue_ids):
    """Resolve and validate the current project before any issue-derived side effect."""
    project = mutation_project(tracker)
    if project is not None:
        tracker.validate_issue_binding(project, *issue_ids)
    return project


def adr_binding(tracker, *adr_ids):
    """Resolve and validate the current project before an ADR mutation."""
    project = mutation_project(tracker)
    if project is not None:
        tracker.validate_adr_binding(project, *adr_ids)
    return project


def transition(tracker, issue_id: str, state: str, context=None) -> None:
    """Set State after validating it against the controlled vocabulary."""
    normalized = validate_state(state)
    if (
        getattr(tracker, "bounded_transition_proofs", False)
        and normalized in {"review", "done"}
        and context is None
    ):
        raise SystemExit(
            f"Transition {normalized} refusée : utilise le flux mécanique "
            f"{'openpr' if normalized == 'review' else 'merge'} pour produire la preuve bornée."
        )
    binding = issue_binding(tracker, issue_id)
    kwargs = {}
    if context is not None:
        kwargs["context"] = context
    if binding is not None:
        kwargs["project"] = binding
    tracker.set_state(issue_id, normalized, **kwargs)


def set_field(tracker, issue_id: str, name: str, value) -> None:
    """Generic field write. Judgment skills MUST get human confirmation before calling this —
    the tier does the write, it does not decide to."""
    binding = issue_binding(tracker, issue_id)
    kwargs = {"project": binding} if binding is not None else {}
    tracker.update_fields(issue_id, {name: value}, **kwargs)


def link(tracker, src_id: str, link_type: str, dst_id: str) -> None:
    """Create a link with the current repository binding attached when required."""
    binding = issue_binding(tracker, src_id, dst_id)
    kwargs = {"project": binding} if binding is not None else {}
    tracker.link(src_id, link_type, dst_id, **kwargs)


def add_comment(tracker, issue_id: str, text: str) -> None:
    """Append a comment with the current repository binding when required."""
    binding = issue_binding(tracker, issue_id)
    kwargs = {"project": binding} if binding is not None else {}
    tracker.add_comment(issue_id, text, **kwargs)


def update_issue_body(
    tracker, issue_id: str, expected_body: str, updated_body: str
) -> bool:
    """Rewrite one issue body through the provider-agnostic bounded body port."""
    binding = issue_binding(tracker, issue_id)
    kwargs = {"project": binding} if binding is not None else {}
    return tracker.update_body(
        Issue(id=issue_id, title=""), expected_body, updated_body, **kwargs
    )


def update_adr_body(
    tracker, adr_id: str, expected_body: str, updated_body: str
) -> bool:
    """Rewrite one ADR body through the provider-agnostic bounded body port."""
    binding = adr_binding(tracker, adr_id)
    project = (
        binding
        if binding is not None
        else tracker.resolve_project(registry.repo_basename())
    )
    current = adr_for_mutation(tracker, project, adr_id)
    if current is None:
        raise RuntimeError(f"ADR introuvable : {adr_id}")
    kwargs = {"project": binding} if binding is not None else {}
    return tracker.update_body(current, expected_body, updated_body, **kwargs)


def sync_acceptance(tracker, issue_id: str, expected_body: str, proof: dict) -> dict:
    """Apply only proof-authorized checkbox markers through the tracker port."""
    updated_body, checked = synchronize_acceptance_body(issue_id, expected_body, proof)
    if getattr(tracker, "acceptance_proof_projection_supported", False):
        binding = issue_binding(tracker, issue_id)
        projector = getattr(tracker, "project_acceptance_proof", None)
        if not callable(projector):
            raise AcceptanceSyncUnavailableError("projection AC indisponible")
        outcomes = (proof.get("issue") or {}).get("criteria", [])
        attested = sum(
            isinstance(outcome, dict) and outcome.get("verdict") == "pass"
            for outcome in outcomes
        )
        projected = projector(
            issue_id,
            expected_body,
            proof,
            checked=attested,
            project=binding,
        )
        return {
            "status": "proof-projected" if projected else "proof-already-projected",
            "checked": attested,
            "audit": "append-only-proof",
        }
    if checked == 0:
        return {"status": "unchanged", "checked": 0}
    if not getattr(tracker, "acceptance_sync_supported", False):
        raise AcceptanceSyncUnavailableError(
            f"synchronisation AC indisponible pour le tracker {tracker.name}"
        )
    binding = issue_binding(tracker, issue_id)
    kwargs = {"project": binding} if binding is not None else {}
    mutated = tracker.sync_acceptance_body(
        issue_id,
        expected_body,
        updated_body,
        proof,
        **kwargs,
    )
    return {
        "status": "updated" if mutated else "unchanged",
        "checked": checked if mutated else 0,
        "audit": "provider",
    }


def project_acceptance_override(tracker, issue_id: str, reason: str, context) -> bool:
    """Record the typed human AC override receipt through the tracker port."""
    binding = issue_binding(tracker, issue_id)
    kwargs = {"project": binding} if binding is not None else {}
    return tracker.project_acceptance_override(issue_id, reason, context, **kwargs)


def recover_acceptance_override(
    tracker, issue_id: str, reason: str, *, pr_url: str, head_sha: str, merge_sha: str,
) -> bool:
    """Append only the missing override receipt of an issue already merged under it."""
    binding = issue_binding(tracker, issue_id)
    kwargs = {"project": binding} if binding is not None else {}
    return tracker.recover_acceptance_override(
        issue_id, reason, pr_url=pr_url, head_sha=head_sha, merge_sha=merge_sha, **kwargs,
    )


def _epic_closure_project(tracker) -> Project:
    project = mutation_project(tracker)
    if project is None:
        project = tracker.resolve_project(registry.repo_basename())
    if (
        not isinstance(project, Project)
        or not isinstance(project.key, str)
        or not project.key
        or not isinstance(project.id, str)
        or not project.id
    ):
        raise SystemExit("Clôture Epic refusée : projet tracker invalide.")
    return project


def _exact_nonnegative_int(value, field: str) -> int:
    if type(value) is not int or value < 0:
        raise SystemExit(f"Clôture Epic refusée : {field} invalide.")
    return value


def _exact_positive_version(value, field: str) -> int:
    version = _exact_nonnegative_int(value, field)
    if version == 0:
        raise SystemExit(f"Clôture Epic refusée : {field} absent.")
    return version


def _validate_epic_parent(parent) -> None:
    if not isinstance(parent.id, str) or not parent.id:
        raise SystemExit("Clôture Epic refusée : identifiant parent invalide.")
    if not isinstance(parent.type, str) or parent.type.casefold() != "epic":
        raise SystemExit("Clôture Epic refusée : la cible n'est pas un Epic.")
    if parent.pr_url:
        raise SystemExit(
            "Clôture Epic refusée : un Epic lié à une PR doit suivre le gate merge existant."
        )
    _exact_positive_version(parent.version, "version parent")
    ac_done = _exact_nonnegative_int(parent.ac_done, "AC satisfaites")
    ac_total = _exact_nonnegative_int(parent.ac_total, "AC totales")
    if ac_done > ac_total or (ac_total and ac_done != ac_total):
        raise SystemExit(
            "Clôture Epic refusée : les AC propres de l'Epic sont incomplètes."
        )


def _validate_epic_outcome(
    outcome,
    *,
    project: Project,
    parent,
    expected: EpicClosureReceipt | None,
) -> EpicClosureOutcome:
    if type(outcome) is not EpicClosureOutcome:
        raise SystemExit("Clôture Epic refusée : reçu provider invalide.")
    receipt = outcome.receipt
    if type(receipt) is not EpicClosureReceipt:
        raise SystemExit("Clôture Epic refusée : reçu provider invalide.")
    if expected is not None and receipt != expected:
        raise SystemExit(
            "Clôture Epic refusée : reçu provider différent de la demande."
        )
    if (
        receipt.project_key != project.key
        or receipt.project_id != project.id
            or receipt.parent_id != parent.id
        or receipt.parent_type.casefold() != "epic"
    ):
        raise SystemExit("Clôture Epic refusée : reçu provider hors projet ou parent.")
    _exact_positive_version(receipt.parent_version, "version parent du reçu")
    _exact_nonnegative_int(receipt.parent_ac_done, "AC satisfaites du reçu")
    _exact_nonnegative_int(receipt.parent_ac_total, "AC totales du reçu")
    if expected is None and (
        receipt.parent_type != parent.type
        or receipt.parent_ac_done != parent.ac_done
        or receipt.parent_ac_total != parent.ac_total
    ):
        raise SystemExit(
            "Clôture Epic refusée : snapshot parent de reprise contradictoire."
        )
    if receipt.parent_ac_done > receipt.parent_ac_total or (
        receipt.parent_ac_total and receipt.parent_ac_done != receipt.parent_ac_total
    ):
        raise SystemExit("Clôture Epic refusée : AC du reçu incomplètes.")
    if type(receipt.children) is not tuple or not receipt.children:
        raise SystemExit("Clôture Epic refusée : liste complète des enfants absente.")
    child_ids = []
    for child in receipt.children:
        if (
            type(child) is not EpicClosureChild
            or not isinstance(child.id, str)
            or not child.id
            or child.state not in EPIC_CHILD_TERMINAL_STATES
        ):
            raise SystemExit("Clôture Epic refusée : coordonnée enfant invalide.")
        _exact_positive_version(child.version, "version enfant du reçu")
        child_ids.append(child.id)
    if child_ids != sorted(child_ids) or len(child_ids) != len(set(child_ids)):
        raise SystemExit("Clôture Epic refusée : ensemble enfant non canonique.")
    _exact_nonnegative_int(receipt.issued_at, "horodatage du reçu")
    if not isinstance(receipt.nonce, str) or not _SAFE_RECEIPT_NONCE.fullmatch(
        receipt.nonce
    ):
        raise SystemExit("Clôture Epic refusée : nonce du reçu invalide.")
    closed_version = _exact_positive_version(
        outcome.closed_parent_version,
        "version parent clôturée",
    )
    if closed_version <= receipt.parent_version:
        raise SystemExit("Clôture Epic refusée : version clôturée non avancée.")
    if not isinstance(outcome.audit_id, str) or not _SAFE_AUDIT_ID.fullmatch(
        outcome.audit_id
    ):
        raise SystemExit("Clôture Epic refusée : audit provider invalide.")
    if type(outcome.replayed) is not bool:
        raise SystemExit("Clôture Epic refusée : statut de reprise invalide.")
    return outcome


def close_epic(
    tracker,
    parent_id: str,
    *,
    issued_at: int | None = None,
    nonce: str | None = None,
) -> EpicClosureOutcome:
    """Close one non-code Epic through an atomic provider graph operation.

    The client preflight makes errors cheap and clear. It is not the concurrency
    boundary: a supporting tracker must compare the receipt against the complete,
    locked parent graph and durably store the state change plus its replayable audit.
    """
    if not getattr(tracker, "epic_closure_supported", False):
        raise EpicClosureUnavailableError(
            f"clôture Epic non-code indisponible pour le tracker {tracker.name}"
        )
    if not isinstance(parent_id, str) or not parent_id:
        raise SystemExit("Clôture Epic refusée : identifiant parent invalide.")
    project = _epic_closure_project(tracker)
    tracker.validate_issue_binding(project, parent_id)
    parent = tracker.get_issue(parent_id)
    if parent.id != parent_id:
        raise SystemExit("Clôture Epic refusée : identité parent contradictoire.")
    _validate_epic_parent(parent)

    if parent.state == "done":
        prior = tracker.get_epic_closure(project, parent_id)
        if prior is None:
            raise SystemExit(
                "Clôture Epic refusée : Epic déjà done sans audit de clôture récupérable."
            )
        outcome = _validate_epic_outcome(
            prior,
            project=project,
            parent=parent,
            expected=None,
        )
        if outcome.closed_parent_version != parent.version:
            raise SystemExit(
                "Clôture Epic refusée : version de reprise contradictoire."
            )
        return EpicClosureOutcome(
            receipt=outcome.receipt,
            closed_parent_version=outcome.closed_parent_version,
            audit_id=outcome.audit_id,
            replayed=True,
        )
    if parent.state == "dropped":
        raise SystemExit(
            "Clôture Epic refusée : un Epic dropped ne peut pas être clôturé done."
        )

    child_ids = []
    for relation in parent.links:
        if relation.type != "parent-of":
            continue
        if not isinstance(relation.target, str) or not relation.target:
            raise SystemExit("Clôture Epic refusée : lien enfant invalide.")
        child_ids.append(relation.target)
    if not child_ids:
        raise SystemExit(
            "Clôture Epic refusée : aucun enfant requis n'est lié à l'Epic."
        )
    if len(child_ids) != len(set(child_ids)):
        raise SystemExit("Clôture Epic refusée : liens enfants dupliqués.")
    child_ids.sort()
    tracker.validate_issue_binding(project, parent_id, *child_ids)

    children = []
    for child_id in child_ids:
        child = tracker.get_issue(child_id)
        if child.id != child_id:
            raise SystemExit("Clôture Epic refusée : identité enfant contradictoire.")
        if child.state not in EPIC_CHILD_TERMINAL_STATES:
            raise SystemExit(
                f"Clôture Epic refusée : l'enfant {child_id} n'est pas terminal."
            )
        children.append(
            EpicClosureChild(
            id=child_id,
            version=_exact_positive_version(child.version, "version enfant"),
            state=child.state,
            )
        )

    issued = int(time.time() * 1000) if issued_at is None else issued_at
    _exact_nonnegative_int(issued, "horodatage")
    receipt_nonce = secrets.token_urlsafe(24) if nonce is None else nonce
    if not isinstance(receipt_nonce, str) or not _SAFE_RECEIPT_NONCE.fullmatch(
        receipt_nonce
    ):
        raise SystemExit("Clôture Epic refusée : nonce invalide.")
    receipt = EpicClosureReceipt(
        project_key=project.key,
        project_id=project.id,
        parent_id=parent.id,
        parent_version=parent.version,
        parent_type=parent.type,
        parent_ac_done=parent.ac_done,
        parent_ac_total=parent.ac_total,
        children=tuple(children),
        issued_at=issued,
        nonce=receipt_nonce,
    )
    outcome = tracker.close_epic(project, receipt)
    return _validate_epic_outcome(
        outcome,
        project=project,
        parent=parent,
        expected=receipt,
    )


def set_adr_status(tracker, adr, status: str) -> None:
    """Advance an ADR with the current repository binding when required."""
    binding = adr_binding(tracker, adr.id)
    kwargs = {"project": binding} if binding is not None else {}
    tracker.set_adr_status(adr, status, **kwargs)


def adr_for_mutation(tracker, project, adr_id: str):
    """Resolve the ADR predecessor without making ordinary reads recover writes."""
    resolver = getattr(tracker, "adr_for_mutation", None)
    if callable(resolver):
        return resolver(project, adr_id)
    return next((adr for adr in tracker.list_adrs(project) if adr.id == adr_id), None)


def supersede_adr(tracker, adr, replacement_id: str) -> None:
    binding = adr_binding(tracker, adr.id, replacement_id)
    tracker.supersede_adr(
        adr, replacement_id, **({"project": binding} if binding is not None else {})
    )


def link_adr_issue(tracker, adr, issue_ref: str):
    binding = adr_binding(tracker, adr.id)
    return tracker.link_adr_issue(
        adr, issue_ref, **({"project": binding} if binding is not None else {})
    )


def ci_gate(codehost, repo: str, sha: str, allow_no_ci: bool = False) -> dict:
    """The merge invariant, in code. Returns a verdict; callers MUST honor `passed`.

    Reads BOTH CI sources — check-runs AND the legacy commit-status API (Jenkins,
    CircleCI… report only to the latter) — merged into one verdict. See ADR-0002.

    passed=True  → at least one check (either source) concluded `success`, none
                   pending, none failing (`neutral`/`skipped` are tolerated ALONGSIDE
                   a real success — they never count as proof on their own).
    passed=False → `pending` non-empty (wait), `failing` non-empty (stop), ZERO
                   checks found on both sources, or nothing concluded success (e.g.
                   every workflow path-filtered to `skipped`) — green means PROVEN
                   green. A red legacy status blocks even with zero check-runs.

    The ONE waiver lives here, not in callers: allow_no_ci=True passes the
    zero-check-on-BOTH-sources case only, and only if `ci_expected()` sees no sign of
    ACTIVE CI for the commit (a suite in progress, with runs, or freshly queued —
    the post-push window; stale queued-empty suites don't count, see the adapter).
    Legacy-only CIs (Jenkins…) create no check-suite, so their post-push window is
    not detectable — there, the explicit human flag is the only guard (ADR-0002,
    limites assumées). The verdict carries waived=True for the audit trail. Failing,
    pending, or all-skipped checks are never waivable.
    """
    checks = codehost.check_runs(repo, sha) + codehost.commit_statuses(repo, sha)
    pending = [c.name for c in checks if c.status != "completed"]
    failing = [
        c.name
        for c in checks
        if c.status == "completed"
        and c.conclusion not in ("success", "neutral", "skipped")
    ]
    proven = any(c.status == "completed" and c.conclusion == "success" for c in checks)
    waived = ci_seen = False
    if not checks and allow_no_ci:
        ci_seen = codehost.ci_expected(repo, sha)
        waived = not ci_seen
    return {
        "passed": (proven or waived) and not pending and not failing,
        "waived": waived,
        "pending": pending,
        "failing": failing,
        "total": len(checks),
        "reason": (
            "checks en cours"
            if pending
            else "checks en échec"
            if failing
            else "aucun check — waiver explicite (--allow-no-ci)"
            if waived
            else "aucun check visible mais une check-suite existe — la CI n'a "
                   "probablement pas encore démarré ; waiver refusé, attends et "
            "relance"
            if ci_seen
            else "aucun check trouvé — rien ne prouve la CI verte (pas encore "
            "démarrée, ou pas de CI)"
            if not checks
            else "aucun check n'a conclu success (tous neutral/skipped) — rien ne "
            "prouve la CI verte"
            if not proven
            else "tous verts"
        ),
    }
