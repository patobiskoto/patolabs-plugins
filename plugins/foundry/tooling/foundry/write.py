"""Write tier — deterministic setters + the invariants that must NEVER be skipped.

The old design left "CI must be green before merge" in prose (a skill instruction);
a rushed session could skip it. Here it's a function that refuses. The rule of thumb:
mechanical invariants live in code (this file); judgment lives in the skills.
"""

from __future__ import annotations

import re
import secrets
import time
import json
import hashlib

from foundry import registry
from foundry.models import (
    MergeDeliveryReceipt,
    EpicClosureChild,
    EpicClosureDependency,
    EpicClosureOutcome,
    EpicClosureOverride,
    EpicClosureReceipt,
    Issue,
    Project,
)
from foundry.routing import (
    RoutingConfigError,
    acceptance_criteria,
    acceptance_digest,
    synchronize_acceptance_body,
)
from foundry.trackers.base import (
    AcceptanceSyncUnavailableError,
    EpicClosureUnavailableError,
    TrackerBindingError,
    TrackerConflictError,
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
# DevHub retains its historical atomic contract, which permits a dropped child.
# PAT-ADR-0006 providers apply the stricter accepted-only rule below.
EPIC_CHILD_TERMINAL_STATES = frozenset({"done", "dropped"})
_EPIC_HUMAN_VERDICT = "accepted"
_SAFE_RECEIPT_NONCE = re.compile(r"[A-Za-z0-9_-]{16,128}")
_SAFE_AUDIT_ID = re.compile(r"[A-Za-z0-9._:-]{1,160}")
# PAT-ADR-0014: nominative waiver of nodes delivered under an audited override.
_EPIC_OVERRIDE_SOURCE = "linear-acceptance-override"
_EPIC_OVERRIDE_REASON = re.compile(r"[a-z0-9_-]{3,80}\Z")
_EPIC_OVERRIDE_BOUND_KEYS = ("pr_url", "head_sha", "base_sha", "review_digest")
_EPIC_NODE_ID = re.compile(r"[A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,8}\Z")


def validate_state(state: str) -> str:
    if state not in ALLOWED_STATES:
        raise SystemExit(
            f"État illégal : '{state}'. Autorisés : {', '.join(sorted(ALLOWED_STATES))}."
        )
    return state


def mutation_project(tracker):
    """Resolve the current repo binding for adapters that require write isolation."""
    requires_binding = getattr(tracker, "requires_mutation_binding", False)
    if not requires_binding:
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


def record_delivery_receipt(
    tracker,
    issue_id: str,
    *,
    project: Project,
    codehost: str,
    repository: str,
    pr_number: int,
    pr_url: str,
    head_sha: str,
    base_sha: str,
    review_digest: str,
    merge_sha: str,
    acceptance: str,
    override_reason: str | None = None,
    review_proof_id: str,
    review_generation: int,
    proof_ac_digest: str,
) -> bool:
    """Persist exact Foundry merge evidence through a qualified tracker port."""
    if not getattr(tracker, "delivery_receipt_supported", False):
        return False
    if not isinstance(project, Project) or not project.key or not project.id:
        raise TrackerConflictError("projet du reçu de livraison invalide")
    if (
        type(pr_number) is not int
        or pr_number < 1
        or not all(isinstance(value, str) and value for value in (
            codehost, repository, pr_url,
        ))
        or any(
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{40}", value) is None
            for value in (head_sha, base_sha, merge_sha)
        )
        or not isinstance(review_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", review_digest) is None
    ):
        raise TrackerConflictError("coordonnées du reçu de livraison invalides")
    tracker.validate_issue_binding(project, issue_id)
    issue = tracker.get_issue(issue_id)
    if (
        issue.id != issue_id
        or issue.pr_url != pr_url
        or str(issue.state or "").casefold() not in {"review", "done"}
    ):
        raise TrackerConflictError("projection native divergente avant reçu de livraison")
    try:
        criteria = acceptance_criteria(issue.body)
    except RoutingConfigError as exc:
        raise TrackerConflictError("AC illisibles avant reçu de livraison") from exc
    if (
        not isinstance(review_proof_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", review_proof_id) is None
        or type(review_generation) is not int
        or review_generation < 1
        or not isinstance(proof_ac_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", proof_ac_digest) is None
    ):
        raise TrackerConflictError("preuve de review invalide avant reçu de livraison")
    current_ac_digest = acceptance_digest(criteria)
    if current_ac_digest != proof_ac_digest:
        raise TrackerConflictError("AC modifiées depuis la preuve de review")
    if acceptance == "accepted":
        if issue.ac_total < 1 or issue.ac_done != issue.ac_total or override_reason is not None:
            raise TrackerConflictError("AC non acceptées avant reçu de livraison")
        source = "structured-review-proof"
    elif acceptance == "deviated":
        if (
            not isinstance(override_reason, str)
            or re.fullmatch(r"[a-z0-9_-]{3,80}", override_reason) is None
        ):
            raise TrackerConflictError("dérogation AC invalide avant reçu de livraison")
        source = "human-override"
    else:
        raise TrackerConflictError("qualification AC invalide avant reçu de livraison")
    receipt = MergeDeliveryReceipt(
        project_key=project.key,
        project_id=project.id,
        issue_id=issue_id,
        codehost=codehost,
        repository=repository,
        pr_number=pr_number,
        pr_url=pr_url,
        head_sha=head_sha,
        base_sha=base_sha,
        review_digest=review_digest,
        merge_sha=merge_sha,
        body_sha256=hashlib.sha256((issue.body or "").encode()).hexdigest(),
        ac_digest=current_ac_digest,
        review_proof_id=review_proof_id,
        review_generation=review_generation,
        acceptance=acceptance,
        acceptance_source=source,
        override_reason=override_reason,
    )
    return tracker.record_delivery_receipt(project, receipt)


def issue_binding(tracker, *issue_ids):
    """Resolve and validate the current project before any issue-derived side effect."""
    project = mutation_project(tracker)
    if project is not None:
        tracker.validate_issue_binding(project, *issue_ids)
    return project


def create_issue(
    tracker,
    title: str,
    body: str = "",
    fields: dict | None = None,
    parent: str | None = None,
) -> Issue:
    """Create an issue through the common repository-bound write seam."""
    project = mutation_project(tracker)
    if project is None:
        project = tracker.resolve_project(registry.repo_basename())
    elif parent is not None:
        # The adapter create may emit provider effects before attaching the parent.
        # Prove the existing endpoint against the canonical repository first.
        tracker.validate_issue_binding(project, parent)
    return tracker.create_issue(
        project,
        title,
        body,
        fields=fields,
        parent=parent,
    )


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
    # A provider without an append-only lifecycle receipt needs a caller-owned
    # predecessor coordinate.  Never manufacture it from the current native
    # state here: a retry could otherwise adopt a third-party edit as the new
    # predecessor and overwrite it.  Public lifecycle paths either carry their
    # stable predecessor (review <- in-progress, done <- review) or refuse when
    # the original coordinate is no longer available.
    if (
        getattr(tracker, "bounded_state_transitions", False)
        and not getattr(context, "expected_state", None)
    ):
        raise TrackerConflictError(
            "transition bornée sans état prédécesseur explicite"
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
    tracker, issue_id: str, reason: str, *, pr_url: str, head_sha: str, base_sha: str, merge_sha: str,
) -> bool:
    """Append only the missing override receipt of an issue already merged under it."""
    binding = issue_binding(tracker, issue_id)
    kwargs = {"project": binding} if binding is not None else {}
    return tracker.recover_acceptance_override(
        issue_id, reason, pr_url=pr_url, head_sha=head_sha, base_sha=base_sha, merge_sha=merge_sha, **kwargs,
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


def epic_parent_validation_digest(parent: Issue) -> str:
    """Bind the Epic's need and test procedure independently of provider timestamps."""
    value = {
        "id": parent.id,
        "title": parent.title,
        "body": parent.body,
        "type": parent.type,
        "ac_done": parent.ac_done,
        "ac_total": parent.ac_total,
        "pr_url": parent.pr_url,
    }
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _validate_epic_parent(parent, *, bounded: bool = False) -> None:
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
    if ac_done > ac_total or (bounded and ac_total == 0):
        raise SystemExit("Clôture Epic refusée : critères propres de l'Epic absents ou invalides.")
    if not bounded and ac_total and ac_done != ac_total:
        raise SystemExit(
            "Clôture Epic refusée : les AC propres de l'Epic sont incomplètes."
        )


def parse_accept_overrides(raw) -> tuple[str, ...]:
    """Validate the nominative ``--accept-override`` list; no provider read here.

    Accepts one comma-separated string or an iterable of ids.  Empty list, empty
    element, duplicate, wildcard, lowercase or any non-canonical id is refused.
    """
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    if not items:
        raise SystemExit(
            "Clôture Epic refusée : --accept-override exige au moins un identifiant."
        )
    bad = [
        item for item in items
        if not isinstance(item, str) or _EPIC_NODE_ID.match(item) is None
    ]
    if bad:
        raise SystemExit(
            "Clôture Epic refusée : --accept-override n'accepte que des identifiants "
            "exacts (ex. PAT-10, sans joker ni élément vide) ; refusé : "
            + ", ".join(repr(item) for item in bad) + "."
        )
    duplicated = sorted({item for item in items if items.count(item) > 1})
    if duplicated:
        raise SystemExit(
            "Clôture Epic refusée : --accept-override contient des doublons : "
            + ", ".join(duplicated) + "."
        )
    return tuple(sorted(items))


def _epic_override_evidence(node) -> tuple[str, str] | None:
    """Return (receipt digest, reason code) of a node's valid override, else None.

    A node is only ever waivable when it is terminal with criteria and carries a
    complete typed override coordinate bound to one review generation and diff.
    """
    if (
        getattr(node, "acceptance_status", None) != "override"
        or getattr(node, "acceptance_source", None) != _EPIC_OVERRIDE_SOURCE
        or type(node.ac_total) is not int
        or node.ac_total <= 0
        or not isinstance(node.acceptance_coordinates, str)
    ):
        return None
    try:
        coordinates = json.loads(node.acceptance_coordinates)
    except (TypeError, ValueError):
        return None
    if (
        not isinstance(coordinates, dict)
        or set(coordinates) != {
            "native_state_id", "reason", "review_generation",
            *_EPIC_OVERRIDE_BOUND_KEYS,
        }
        or not isinstance(coordinates["reason"], str)
        or _EPIC_OVERRIDE_REASON.match(coordinates["reason"]) is None
        or type(coordinates["review_generation"]) is not int
        or any(
            not isinstance(coordinates[key], str) or not coordinates[key]
            for key in ("native_state_id", *_EPIC_OVERRIDE_BOUND_KEYS)
        )
    ):
        return None
    digest = hashlib.sha256(node.acceptance_coordinates.encode("utf-8")).hexdigest()
    return digest, coordinates["reason"]


def _bounded_epic_node(
    issue: Issue, *, role: str, tracker=None, waivable: frozenset = frozenset(),
) -> EpicClosureChild:
    """Build one strictly accepted graph coordinate for PAT-ADR-0006."""
    if issue.state == "dropped":
        raise SystemExit(
            f"Clôture Epic refusée : {role} {issue.id} est dérogé, pas accepté."
        )
    if issue.state != "done":
        raise SystemExit(
            f"Clôture Epic refusée : {role} {issue.id} n'est pas terminal accepté."
        )
    observed_version = issue.version
    if observed_version is None:
        resolver = getattr(tracker, "bounded_epic_version", None)
        if callable(resolver):
            observed_version = resolver(issue)
    version = _exact_positive_version(observed_version, f"version {role}")
    ac_done = _exact_nonnegative_int(issue.ac_done, f"AC {role} satisfaites")
    ac_total = _exact_nonnegative_int(issue.ac_total, f"AC {role} totales")
    if issue.id in waivable:
        # PAT-ADR-0014: the human named this node; its ONLY insufficiency may be a
        # valid typed override receipt.  Anything else stays a closed failure.
        if _epic_override_evidence(issue) is None:
            if ac_total == 0:
                cause = "aucun critère d'acceptation"
            elif issue.acceptance_status == "accepted":
                cause = "preuve positive : le nœud est accepté, pas dérogé"
            else:
                cause = "reçu d'override absent, invalide ou preuve inconnue"
            raise SystemExit(
                f"Clôture Epic refusée : --accept-override {issue.id} "
                f"({role}) inacceptable : {cause}."
            )
    else:
        if ac_total == 0 or ac_done != ac_total:
            raise SystemExit(
                f"Clôture Epic refusée : la preuve AC de {role} {issue.id} est inconnue."
            )
        if issue.acceptance_status != "accepted":
            qualifier = "dérogée" if issue.acceptance_status == "override" else "inconnue"
            raise SystemExit(
                f"Clôture Epic refusée : la preuve de {role} {issue.id} est {qualifier}."
            )
    if (
        not isinstance(issue.acceptance_source, str)
        or not issue.acceptance_source
        or not isinstance(issue.acceptance_coordinates, str)
        or not issue.acceptance_coordinates
    ):
        raise SystemExit(
            f"Clôture Epic refusée : coordonnées de preuve de {role} {issue.id} absentes."
        )
    return EpicClosureChild(
        id=issue.id,
        version=version,
        state=issue.state,
        ac_done=ac_done,
        ac_total=ac_total,
        acceptance_status=issue.acceptance_status,
        acceptance_source=issue.acceptance_source,
        acceptance_coordinates=issue.acceptance_coordinates,
    )


def bounded_epic_graph_snapshot(
    tracker, project: Project, parent: Issue,
    accept_overrides: frozenset = frozenset(),
) -> tuple[tuple[EpicClosureChild, ...], tuple[EpicClosureDependency, ...]]:
    """Read the complete required-child/dependency graph in canonical order.

    This is the one canonical builder used by the write preflight, provider S1,
    readback and replay.  It has no write side effect.  ``accept_overrides`` is
    the exact nominative PAT-ADR-0014 waiver set; empty keeps the historical rule.
    """
    waivable = frozenset(accept_overrides)
    child_ids = [
        relation.target for relation in parent.links if relation.type == "parent-of"
    ]
    if not child_ids:
        raise SystemExit(
            "Clôture Epic refusée : aucun enfant requis n'est lié à l'Epic."
        )
    if any(not isinstance(item, str) or not item for item in child_ids):
        raise SystemExit("Clôture Epic refusée : lien enfant invalide.")
    if len(child_ids) != len(set(child_ids)):
        raise SystemExit("Clôture Epic refusée : liens enfants dupliqués.")
    child_ids.sort()
    tracker.validate_issue_binding(project, parent.id, *child_ids)
    cache: dict[str, Issue] = {parent.id: parent}

    def fetch(issue_id: str) -> Issue:
        if issue_id not in cache:
            tracker.validate_issue_binding(project, parent.id, issue_id)
            candidate = tracker.get_issue(issue_id)
            if candidate.id != issue_id:
                raise SystemExit(
                    "Clôture Epic refusée : identité du graphe contradictoire."
                )
            cache[issue_id] = candidate
        return cache[issue_id]

    children = tuple(
        _bounded_epic_node(
            fetch(child_id), role="enfant", tracker=tracker, waivable=waivable,
        )
        for child_id in child_ids
    )
    edges: list[EpicClosureDependency] = []
    expanded: set[str] = set()

    def visit(source: Issue, path: tuple[str, ...]) -> None:
        dependency_ids = [
            relation.target
            for relation in source.links
            if relation.type == "depends-on"
        ]
        if any(not isinstance(item, str) or not item for item in dependency_ids):
            raise SystemExit("Clôture Epic refusée : lien de dépendance invalide.")
        if len(dependency_ids) != len(set(dependency_ids)):
            raise SystemExit(
                f"Clôture Epic refusée : dépendances dupliquées pour {source.id}."
            )
        for target_id in sorted(dependency_ids):
            if target_id in path:
                raise SystemExit(
                    "Clôture Epic refusée : cycle dans le graphe de dépendances."
                )
            tracker.validate_issue_binding(project, parent.id, source.id, target_id)
            target = fetch(target_id)
            edges.append(EpicClosureDependency(
                source_id=source.id,
                target=_bounded_epic_node(
                    target, role="dépendance", tracker=tracker, waivable=waivable,
                ),
            ))
            if target_id not in expanded:
                visit(target, (*path, target_id))
        expanded.add(source.id)

    for root in (parent, *(cache[item] for item in child_ids)):
        if root.id not in expanded:
            visit(root, (root.id,))
    ordered = tuple(sorted(edges, key=lambda edge: (edge.source_id, edge.target.id)))
    if len({(edge.source_id, edge.target.id) for edge in ordered}) != len(ordered):
        raise SystemExit("Clôture Epic refusée : graphe de dépendances dupliqué.")
    return children, ordered


def epic_receipt_overrides(
    children: tuple[EpicClosureChild, ...],
    dependencies: tuple[EpicClosureDependency, ...],
) -> tuple[EpicClosureOverride, ...]:
    """Bind every node accepted under an override: id, receipt digest, reason."""
    found: dict[str, EpicClosureOverride] = {}
    nodes = (*children, *(edge.target for edge in dependencies))
    for node in nodes:
        if node.acceptance_status != "override":
            continue
        evidence = _epic_override_evidence(node)
        if evidence is None:
            raise SystemExit(
                f"Clôture Epic refusée : reçu d'override de {node.id} invalide."
            )
        item = EpicClosureOverride(node.id, evidence[0], evidence[1])
        if found.setdefault(node.id, item) != item:
            raise SystemExit(
                f"Clôture Epic refusée : coordonnées d'override contradictoires pour {node.id}."
            )
    return tuple(found[key] for key in sorted(found))


def epic_graph_diagnostic(
    tracker, project: Project, parent: Issue, accept: frozenset = frozenset(),
) -> list[dict]:
    """List EVERY graph node whose acceptance proof is not positive (read-only).

    Walks the same required-child + transitive-dependency graph as the snapshot but
    never stops at the first cause: a node whose read raises is reported with that
    cause (it cannot be traversed further).  No provider write is ever issued.
    """
    nodes: dict[str, dict] = {}
    seen: set[str] = {parent.id}
    cache: dict[str, Issue] = {parent.id: parent}

    def add(issue_id: str, role: str, code: str, cause: str) -> None:
        nodes.setdefault(
            issue_id,
            {"id": issue_id, "role": role, "code": code, "cause": cause,
             "waivable": code == "override"},
        )

    def read(issue_id: str, role: str) -> Issue | None:
        if issue_id in cache:
            return cache[issue_id]
        try:
            tracker.validate_issue_binding(project, parent.id, issue_id)
        except Exception as exc:
            add(issue_id, role, "foreign-project",
                f"hors projet ou binding invalide ({type(exc).__name__}: {exc})")
            return None
        try:
            issue = tracker.get_issue(issue_id)
        except Exception as exc:
            add(issue_id, role, "read-error",
                f"lecture impossible ({type(exc).__name__}: {exc})")
            return None
        if getattr(issue, "id", None) != issue_id:
            add(issue_id, role, "read-error", "identité du nœud contradictoire")
            return None
        cache[issue_id] = issue
        return issue

    def classify(issue: Issue, role: str) -> None:
        if issue.state == "dropped":
            add(issue.id, role, "dropped", "dérogé (dropped), jamais acceptable")
        elif issue.state != "done":
            add(issue.id, role, "non-terminal", f"n'est pas terminal (état {issue.state})")
        elif type(issue.ac_total) is not int or issue.ac_total <= 0:
            add(issue.id, role, "zero-criteria", "aucun critère d'acceptation")
        elif (
            issue.acceptance_status == "accepted"
            and issue.ac_done == issue.ac_total
            and issue.acceptance_source
            and issue.acceptance_coordinates
        ):
            return
        else:
            evidence = _epic_override_evidence(issue)
            if evidence is not None:
                add(issue.id, role, "override",
                    f"terminal sous reçu d'override valide (raison {evidence[1]}) ; "
                    "acceptable seulement nominativement par --accept-override")
            else:
                add(issue.id, role, "unknown",
                    "preuve d'acceptation inconnue ou incomplète, ou reçu d'override invalide")

    def visit(issue: Issue, role: str) -> None:
        if issue.id != parent.id:
            classify(issue, role)
        for relation in issue.links:
            if relation.type != "depends-on" or not isinstance(relation.target, str):
                continue
            if relation.target in seen:
                continue
            seen.add(relation.target)
            target = read(relation.target, "dépendance")
            if target is not None:
                visit(target, "dépendance")

    child_ids = sorted(
        {relation.target for relation in parent.links
         if relation.type == "parent-of" and isinstance(relation.target, str)}
    )
    seen.update(child_ids)
    children = [(cid, read(cid, "enfant")) for cid in child_ids]
    visit(parent, "parent")
    for cid, child in children:
        if child is not None:
            visit(child, "enfant")
    for item in nodes.values():
        item["named"] = item["id"] in accept
    report = sorted(nodes.values(), key=lambda item: item["id"])
    for item in report:
        item["total"] = len(seen) - 1
    return report


def format_epic_diagnostic(report: list[dict]) -> str:
    if not report:
        return ""
    lines = [
        f"Nœuds du graphe sans preuve d'acceptation positive ({len(report)} "
        f"sur {report[0]['total']}) :"
    ]
    for item in report:
        flag = " [nommé]" if item["named"] else ""
        lines.append(f"  - {item['id']} ({item['role']}){flag} : {item['cause']}")
    waivable = [item["id"] for item in report if item["waivable"]]
    if waivable and len(waivable) == len(report):
        lines.append(
            "Seuls des reçus d'override valides bloquent : le mainteneur peut les attester "
            "nommément par --human-verdict=accepted --accept-override=" + ",".join(waivable)
            + " (ni CAS ni acceptation)."
        )
    return "\n".join(lines)


def _snapshot_with_diagnostic(
    tracker, project: Project, parent: Issue, accept: frozenset,
):
    """Build the graph snapshot; on refusal, append the complete read-only diagnostic."""
    try:
        return bounded_epic_graph_snapshot(tracker, project, parent, accept)
    except (SystemExit, TrackerConflictError, TrackerBindingError) as exc:
        try:
            text = format_epic_diagnostic(
                epic_graph_diagnostic(tracker, project, parent, accept)
            )
        except Exception:
            text = ""
        if not text:
            raise
        message = f"{exc}\n{text}"
        if isinstance(exc, TrackerBindingError):
            raise SystemExit(
                f"Clôture Epic refusée : nœud hors projet ou binding invalide ({exc}).\n{text}"
            ) from None
        if isinstance(exc, SystemExit):
            raise SystemExit(message) from None
        raise TrackerConflictError(message) from None


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
        receipt.human_verdict is None
        and receipt.parent_ac_total
        and receipt.parent_ac_done != receipt.parent_ac_total
    ):
        raise SystemExit("Clôture Epic refusée : AC du reçu incomplètes.")
    if type(receipt.children) is not tuple or not receipt.children:
        raise SystemExit("Clôture Epic refusée : liste complète des enfants absente.")
    bounded_receipt = receipt.human_verdict is not None
    if bounded_receipt and (
        receipt.human_verdict != _EPIC_HUMAN_VERDICT
        or not isinstance(receipt.parent_state, str)
        or receipt.parent_state in EPIC_CHILD_TERMINAL_STATES
        or receipt.parent_ac_total == 0
        or not isinstance(receipt.parent_validation_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", receipt.parent_validation_digest) is None
    ):
        raise SystemExit("Clôture Epic refusée : prédécesseur ou verdict invalide.")

    waivers: dict[str, EpicClosureOverride] = {}
    if type(receipt.accepted_overrides) is not tuple:
        raise SystemExit("Clôture Epic refusée : dérogations du reçu invalides.")
    for item in receipt.accepted_overrides:
        if (
            not bounded_receipt
            or type(item) is not EpicClosureOverride
            or not isinstance(item.node_id, str)
            or _EPIC_NODE_ID.match(item.node_id) is None
            or not isinstance(item.receipt_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", item.receipt_digest) is None
            or not isinstance(item.reason, str)
            or _EPIC_OVERRIDE_REASON.match(item.reason) is None
            or item.node_id in waivers
        ):
            raise SystemExit("Clôture Epic refusée : dérogation du reçu invalide.")
        waivers[item.node_id] = item
    if list(waivers) != sorted(waivers):
        raise SystemExit("Clôture Epic refusée : dérogations non canoniques.")
    waived_seen: set[str] = set()

    def validate_node(child: EpicClosureChild, label: str) -> None:
        if (
            type(child) is not EpicClosureChild
            or not isinstance(child.id, str)
            or not child.id
            or child.state not in EPIC_CHILD_TERMINAL_STATES
        ):
            raise SystemExit(f"Clôture Epic refusée : coordonnée {label} invalide.")
        _exact_positive_version(child.version, f"version {label} du reçu")
        _exact_nonnegative_int(child.ac_done, f"AC {label} satisfaites du reçu")
        _exact_nonnegative_int(child.ac_total, f"AC {label} totales du reçu")
        if child.id in waivers:
            evidence = _epic_override_evidence(child)
            if (
                child.state != "done"
                or evidence is None
                or evidence != (waivers[child.id].receipt_digest, waivers[child.id].reason)
            ):
                raise SystemExit(
                    f"Clôture Epic refusée : dérogation de {child.id} incohérente avec son reçu d'override."
                )
            waived_seen.add(child.id)
            return
        if child.acceptance_status == "override" and bounded_receipt:
            raise SystemExit(
                f"Clôture Epic refusée : {child.id} dérogé sans attestation nominative."
            )
        if child.ac_done != child.ac_total:
            raise SystemExit(f"Clôture Epic refusée : AC {label} incomplètes ou inconnues.")
        if bounded_receipt:
            if child.ac_total == 0 or child.acceptance_status != "accepted":
                raise SystemExit(f"Clôture Epic refusée : preuve {label} non acceptée.")
            if not isinstance(child.acceptance_source, str) or not child.acceptance_source:
                raise SystemExit(f"Clôture Epic refusée : source de preuve {label} absente.")
            if not isinstance(child.acceptance_coordinates, str):
                raise SystemExit(f"Clôture Epic refusée : coordonnées de preuve {label} absentes.")
            try:
                coordinates = json.loads(child.acceptance_coordinates)
            except (TypeError, ValueError):
                coordinates = None
            if not isinstance(coordinates, dict) or not coordinates:
                raise SystemExit(f"Clôture Epic refusée : coordonnées de preuve {label} invalides.")

    child_ids = []
    for child in receipt.children:
        validate_node(child, "enfant")
        child_ids.append(child.id)
    if child_ids != sorted(child_ids) or len(child_ids) != len(set(child_ids)):
        raise SystemExit("Clôture Epic refusée : ensemble enfant non canonique.")
    if type(receipt.dependencies) is not tuple:
        raise SystemExit("Clôture Epic refusée : graphe de dépendances invalide.")
    dependency_keys = []
    adjacency: dict[str, list[str]] = {}
    known_nodes = {receipt.parent_id, *child_ids}
    for dependency in receipt.dependencies:
        if (
            type(dependency) is not EpicClosureDependency
            or not isinstance(dependency.source_id, str)
            or not dependency.source_id
        ):
            raise SystemExit("Clôture Epic refusée : dépendance invalide.")
        validate_node(dependency.target, "dépendance")
        dependency_keys.append((dependency.source_id, dependency.target.id))
        adjacency.setdefault(dependency.source_id, []).append(dependency.target.id)
        known_nodes.add(dependency.target.id)
    if dependency_keys != sorted(dependency_keys) or len(dependency_keys) != len(set(dependency_keys)):
        raise SystemExit("Clôture Epic refusée : graphe de dépendances non canonique.")
    if any(source not in known_nodes for source in adjacency):
        raise SystemExit("Clôture Epic refusée : source de dépendance étrangère.")
    if waived_seen != set(waivers):
        raise SystemExit(
            "Clôture Epic refusée : dérogation nommée absente du graphe du reçu : "
            + ", ".join(sorted(set(waivers) - waived_seen)) + "."
        )

    def reject_cycle(node: str, path: frozenset[str]) -> None:
        if node in path:
            raise SystemExit("Clôture Epic refusée : cycle de dépendances dans le reçu.")
        for target in adjacency.get(node, ()):
            reject_cycle(target, path | {node})

    reject_cycle(receipt.parent_id, frozenset())
    for child_id in child_ids:
        reject_cycle(child_id, frozenset())
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
    human_verdict: str | None = None,
    accept_overrides=None,
) -> EpicClosureOutcome:
    """Close one non-code Epic through its qualified audited capability.

    The client preflight makes errors cheap and clear.  DevHub owns the atomic
    boundary; PAT-ADR-0006 adapters instead apply their documented fresh-read,
    one targeted parent-write, append-only-audit and readback sequence.
    """
    atomic = getattr(tracker, "epic_closure_supported", False)
    bounded = getattr(tracker, "bounded_epic_closure_supported", False)
    if not atomic and not bounded:
        raise EpicClosureUnavailableError(
            f"clôture Epic non-code indisponible pour le tracker {tracker.name}"
        )
    if not isinstance(parent_id, str) or not parent_id:
        raise SystemExit("Clôture Epic refusée : identifiant parent invalide.")
    if bounded and human_verdict != _EPIC_HUMAN_VERDICT:
        raise SystemExit(
            "Clôture Epic refusée : verdict humain explicite `accepted` requis."
        )
    # PAT-ADR-0014: validated before any provider read, fail closed off Linear.
    accepted_ids: tuple[str, ...] = ()
    if accept_overrides is not None:
        accepted_ids = parse_accept_overrides(accept_overrides)
        if human_verdict != _EPIC_HUMAN_VERDICT:
            raise SystemExit(
                "Clôture Epic refusée : --accept-override exige --human-verdict=accepted."
            )
        if not bounded or not getattr(tracker, "epic_override_closure_supported", False):
            raise SystemExit(
                "Clôture Epic refusée : --accept-override n'est qualifié que pour Linear "
                f"(tracker {tracker.name})."
            )
    requested = frozenset(accepted_ids)
    project = _epic_closure_project(tracker)
    tracker.validate_issue_binding(project, parent_id)
    try:
        parent = tracker.get_issue(parent_id)
    except TrackerConflictError:
        # A Linear bounded Epic closure deliberately has no code-issue lifecycle
        # receipt.  Its native done projection is therefore unreadable through
        # the code-lifecycle normalizer; recover only from its exact audit.
        prior = tracker.get_epic_closure(project, parent_id)
        if prior is None:
            raise
        parent = Issue(
            id=parent_id, title="", state="done", type=prior.receipt.parent_type,
            ac_done=prior.receipt.parent_ac_done, ac_total=prior.receipt.parent_ac_total,
            version=prior.closed_parent_version,
        )
    if parent.id != parent_id:
        raise SystemExit("Clôture Epic refusée : identité parent contradictoire.")
    _validate_epic_parent(parent, bounded=bounded)
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
        recorded = tuple(item.node_id for item in outcome.receipt.accepted_overrides)
        if recorded != accepted_ids:
            raise SystemExit(
                "Clôture Epic refusée : ensemble --accept-override différent du reçu "
                f"déjà écrit (reçu : {', '.join(recorded) or 'aucun'} ; "
                f"demandé : {', '.join(accepted_ids) or 'aucun'})."
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

    if bounded:
        pending_getter = getattr(tracker, "get_pending_epic_closure", None)
        pending = pending_getter(project, parent_id) if callable(pending_getter) else None
        if pending is not None:
            if type(pending) is not EpicClosureReceipt:
                raise SystemExit("Clôture Epic refusée : audit pending invalide.")
            if (
                pending.project_key != project.key
                or pending.project_id != project.id
                or pending.parent_id != parent.id
                or parent.version is None
                or parent.version < pending.parent_version
                or pending.parent_state != parent.state
                or pending.parent_type != parent.type
                or pending.parent_ac_done != parent.ac_done
                or pending.parent_ac_total != parent.ac_total
                or pending.parent_validation_digest != epic_parent_validation_digest(parent)
                or pending.human_verdict != human_verdict
                or (issued_at is not None and pending.issued_at != issued_at)
                or (nonce is not None and pending.nonce != nonce)
            ):
                raise TrackerConflictError(
                    "audit pending contradictoire avec le prédécesseur original"
                )
            pending_ids = tuple(item.node_id for item in pending.accepted_overrides)
            if pending_ids != accepted_ids:
                raise SystemExit(
                    "Clôture Epic refusée : audit en attente avec un autre ensemble "
                    f"--accept-override (audit : {', '.join(pending_ids) or 'aucun'} ; "
                    f"demandé : {', '.join(accepted_ids) or 'aucun'}). "
                    "Relance avec l'ensemble exact de l'audit."
                )
            children, dependencies = _snapshot_with_diagnostic(
                tracker, project, parent, requested,
            )
            if (
                pending.children != children
                or pending.dependencies != dependencies
            ):
                raise TrackerConflictError(
                    "graphe Epic divergent depuis l'audit pending"
                )
            outcome = tracker.close_epic(project, pending)
            return _validate_epic_outcome(
                outcome,
                project=project,
                parent=parent,
                expected=pending,
            )

    dependencies: tuple[EpicClosureDependency, ...] = ()
    overrides: tuple[EpicClosureOverride, ...] = ()
    if bounded:
        children, dependencies = _snapshot_with_diagnostic(
            tracker, project, parent, requested,
        )
        overrides = epic_receipt_overrides(children, dependencies)
        missing = requested - {item.node_id for item in overrides}
        if missing:
            raise SystemExit(
                "Clôture Epic refusée : --accept-override désigne des identifiants "
                "hors du graphe de l'Epic : " + ", ".join(sorted(missing)) + "."
            )
    else:
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
        atomic_children = []
        for child_id in child_ids:
            child = tracker.get_issue(child_id)
            if child.id != child_id:
                raise SystemExit("Clôture Epic refusée : identité enfant contradictoire.")
            if child.state not in EPIC_CHILD_TERMINAL_STATES:
                raise SystemExit(
                    f"Clôture Epic refusée : l'enfant {child_id} n'est pas terminal."
                )
            atomic_children.append(EpicClosureChild(
                id=child_id,
                version=_exact_positive_version(child.version, "version enfant"),
                state=child.state,
                ac_done=_exact_nonnegative_int(child.ac_done, "AC enfant satisfaites"),
                ac_total=_exact_nonnegative_int(child.ac_total, "AC enfant totales"),
            ))
            if child.ac_done != child.ac_total:
                raise SystemExit(
                    f"Clôture Epic refusée : les AC de l'enfant {child_id} sont incomplètes ou inconnues."
                )
        children = tuple(atomic_children)

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
        human_verdict=human_verdict if bounded else None,
        parent_state=parent.state if bounded else None,
        dependencies=dependencies,
        parent_validation_digest=(
            epic_parent_validation_digest(parent) if bounded else None
        ),
        accepted_overrides=overrides,
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
