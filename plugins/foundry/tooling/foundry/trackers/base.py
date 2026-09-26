"""The Tracker port — the only surface the pipeline knows about a tracker.

Every method speaks normalized models (Issue, Adr, Project). Concrete adapters
(YouTrack today; Jira / GitHub Projects tomorrow) implement this. Add a provider
by writing one subclass — the query/write tiers and the skills never change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from foundry.models import (
    Adr,
    EpicClosureOutcome,
    EpicClosureReceipt,
    Issue,
    Project,
    TransitionContext,
)


_UNSPECIFIED_ADR_RELATION = object()


class IssueUnavailableError(RuntimeError):
    """A requested issue is absent or inaccessible to the current tracker user.

    This is deliberately narrower than ``RuntimeError`` so callers can tolerate a
    missing *related* issue without hiding outages, malformed provider payloads,
    or programming errors.  Its public text is fixed apart from the issue id and
    therefore never exposes provider responses or credentials.
    """

    def __init__(self, issue_id: str):
        super().__init__(f"issue unavailable: {issue_id}")
        self.issue_id = issue_id


class AdrUnavailableError(RuntimeError):
    """A referenced ADR is absent from the active tracker project."""

    def __init__(self, adr_id: str):
        super().__init__(f"ADR unavailable: {adr_id}")
        self.adr_id = adr_id


class AcceptanceSyncUnavailableError(RuntimeError):
    """The active tracker cannot safely synchronize acceptance checkboxes."""


class BodyUpdateUnavailableError(RuntimeError):
    """The active tracker cannot perform a bounded body replacement."""


class TrackerConflictError(RuntimeError):
    """A provider version/body changed before a bounded tracker mutation."""


class TrackerCapabilityUnavailableError(RuntimeError):
    """The active tracker cannot safely provide one explicitly named capability."""

    def __init__(self, tracker: str, capability: str):
        super().__init__(f"capability unavailable: {tracker}.{capability}")
        self.tracker = tracker
        self.capability = capability


class EpicClosureUnavailableError(RuntimeError):
    """The active tracker lacks atomic, audited non-code Epic closure."""


class ProjectProvisioningUnavailableError(RuntimeError):
    """The active tracker does not expose project provisioning."""


class EpicSubgraphUnavailableError(RuntimeError):
    """The active tracker does not expose a complete versioned Epic subgraph."""


class Tracker(ABC):
    name: str = "abstract"
    bounded_transition_proofs: bool = False
    requires_mutation_binding: bool = False
    acceptance_sync_supported: bool = False
    epic_closure_supported: bool = False
    epic_closure_supported: bool = False
    project_provisioning_supported: bool = False
    project_provisioning_requires_repository: bool = False
    epic_subgraph_supported: bool = False
    append_only_lifecycle_supported: bool = False
    acceptance_proof_projection_supported: bool = False
    acceptance_override_projection_supported: bool = False
    cockpit_evidence_projection_supported: bool = False

    # --- optional read-only graph projection -----------------------------
    def get_epic_subgraph(
        self,
        project: Project,
        epic_id: str,
        *,
        depth: int = 8,
        nodes: int = 100,
    ) -> dict:
        """Return one provider-versioned, explicitly bounded Epic subgraph.

        The result is provider data only.  Consumers must still validate its
        completeness, consistency and acyclicity before deriving a plan.
        """
        raise EpicSubgraphUnavailableError(
            f"sous-graphe Epic indisponible pour le tracker {self.name}"
        )

    # --- optional project provisioning -----------------------------------
    def provision_project(
        self,
        name: str,
        key: str,
        canonical_repository: str | None = None,
    ) -> Project:
        """Create or recover one provider project without registering a checkout.

        This optional capability owns all provider-specific provisioning and must
        be idempotent.  The caller persists the returned normalized ``Project`` in
        Foundry's registry only after the provider operation succeeds.
        """
        raise ProjectProvisioningUnavailableError(
            f"provisionnement de projet indisponible pour le tracker {self.name}"
        )

    # --- resolution -------------------------------------------------------
    @abstractmethod
    def resolve_project(self, repo: str) -> Project:
        """Map a repo basename to its tracker project (raises if unknown)."""

    def resolve_checkout_project(
        self,
        cwd: str | None = None,
        *,
        checkout_identity: str | None = None,
    ) -> Project:
        """Resolve the tracker project for one checkout.

        Existing providers retain their repository-name lookup. Providers whose
        binding contract is repository-identity based may override this method and
        use ``checkout_identity`` (or derive it from ``cwd``) without trusting an
        inherited environment alias.
        """
        del checkout_identity
        from foundry import registry

        repo = registry.repo_basename() if cwd is None else registry.repo_basename(cwd)
        return self.resolve_project(repo)

    def preflight_issue_operation(self, operation: str) -> None:
        """Refuse an issue lifecycle operation before any code-host effect.

        ``openpr`` and ``merge`` call this seam before pushing, creating/updating a
        pull request, merging, or deleting a branch. Existing providers intentionally
        keep this no-op default; an adapter with a narrower capability boundary must
        fail closed here.
        """
        del operation

    def validate_issue_binding(self, project: Project, *issue_ids: str) -> None:
        """Fail when an issue mutation would escape ``project`` (default: no-op)."""

    def validate_adr_binding(self, project: Project, *adr_ids: str) -> None:
        """Fail when an ADR mutation would escape ``project`` (default: no-op)."""

    def validate_mutation_repository(self, repo: str, checkout_identity: str) -> None:
        """Fail when the actual checkout is not the registered mutation target.

        Providers without a canonical repository coordinate keep the default no-op.
        The write tier invokes this before resolving a mutation project.
        """

    def validate_mutation_project(self, project: Project) -> None:
        """Fail when a resolved project is not writable through this provider.

        This is distinct from read resolution: an archived provider project may
        remain queryable while every lifecycle mutation is refused. Providers
        without such an archive boundary keep the default no-op.
        """
        del project

    def start_transition_path(self, current_state: str) -> tuple[str, ...]:
        """Provider-valid states needed to start an issue from ``current_state``.

        Trackers with a constrained workflow override this preflight. The default
        preserves YouTrack's established direct transition behavior.
        """
        return () if current_state == "in-progress" else ("in-progress",)

    def sync_acceptance_body(
        self,
        issue_id: str,
        expected_body: str,
        updated_body: str,
        proof: dict,
        project: Project | None = None,
    ) -> bool:
        """Replace only proof-authorized acceptance checkbox markers.

        ``proof`` is the full structured review proof, not only its digest. Providers
        may offer stronger concurrency/audit guarantees (DevHub CAS plus receipt) or
        a documented bounded read-verify-write fallback (YouTrack). They return
        ``True`` only when they performed the mutation.
        """
        raise AcceptanceSyncUnavailableError(
            f"synchronisation AC indisponible pour le tracker {self.name}"
        )

    def project_acceptance_override(
        self,
        issue_id: str,
        reason: str,
        context: TransitionContext,
        project: Project | None = None,
    ) -> bool:
        """Append a typed receipt of an explicit human AC override before merge.

        ``reason`` is the validated public audit code and ``context`` the exact
        current review coordinates. The receipt never checks an AC or counts as
        acceptance. Providers without it keep the free-text audit note fallback
        (``acceptance_override_projection_supported`` stays ``False``).
        """
        del issue_id, reason, context, project
        raise TrackerCapabilityUnavailableError(self.name, "acceptance-override-receipt")

    def recover_acceptance_override(
        self,
        issue_id: str,
        reason: str,
        *,
        pr_url: str,
        head_sha: str,
        merge_sha: str,
        project: Project | None = None,
    ) -> bool:
        """Append only the missing override receipt of an already-merged issue."""
        del issue_id, reason, pr_url, head_sha, merge_sha, project
        raise TrackerCapabilityUnavailableError(self.name, "acceptance-override-recovery")

    def update_body(
        self,
        resource: Issue | Adr,
        expected_body: str,
        updated_body: str,
        project: Project | None = None,
    ) -> bool:
        """Replace one issue or ADR body through the provider's bounded write path.

        ``expected_body`` is a byte-exact snapshot supplied by the caller. A
        supporting provider must read it immediately before writing, refuse a
        divergence, and read back the result after its one write.  ``True`` means
        it wrote; ``False`` means the requested body was already current.
        """
        raise BodyUpdateUnavailableError(
            f"mise à jour de corps indisponible pour le tracker {self.name}"
        )

    def close_epic(
        self,
        project: Project,
        receipt: EpicClosureReceipt,
    ) -> EpicClosureOutcome:
        """Atomically verify ``receipt``, close its parent and audit the result.

        A supporting provider must lock the parent graph, compare the exact parent
        version/type/AC snapshot and the complete required-child id/version/state set,
        then persist both ``done`` and a replayable audit receipt in one transaction.
        It must never delegate to the ordinary code-issue ``done`` transition.
        """
        raise EpicClosureUnavailableError(
            f"clôture Epic non-code indisponible pour le tracker {self.name}"
        )

    def get_epic_closure(
        self,
        project: Project,
        parent_id: str,
    ) -> EpicClosureOutcome | None:
        """Return a prior audited closure for interruption recovery, if any."""
        raise EpicClosureUnavailableError(
            f"clôture Epic non-code indisponible pour le tracker {self.name}"
        )

    # --- issues (read) ----------------------------------------------------
    @abstractmethod
    def search(self, project: Project, query: str = "") -> list[Issue]:
        """All issues in the project matching an optional provider-native query."""

    @abstractmethod
    def get_issue(self, issue_id: str) -> Issue:
        """One fully-hydrated issue (links + AC counts included)."""

    # --- issues (write) ---------------------------------------------------
    @abstractmethod
    def create_issue(
        self,
        project: Project,
        title: str,
        body: str,
        fields: dict | None = None,
        parent: str | None = None,
    ) -> Issue:
        """Create an issue; optionally link it as a subtask of `parent`."""

    @abstractmethod
    def update_fields(
        self, issue_id: str, fields: dict, project: Project | None = None
    ) -> Issue:
        """Set custom fields by normalized name (Priority, Estimate, Milestone, State…)."""

    @abstractmethod
    def set_state(
        self,
        issue_id: str,
        state: str,
                  context: TransitionContext | None = None,
        project: Project | None = None,
    ) -> None:
        """Transition State (validated against allowed states by the write tier)."""

    @abstractmethod
    def link(
        self, src_id: str, link_type: str, dst_id: str, project: Project | None = None
    ) -> None:
        """Create a typed link (subtask-of, relates, depends-on…) src → dst."""

    @abstractmethod
    def add_comment(
        self, issue_id: str, text: str, project: Project | None = None
    ) -> None:
        """Append a comment (progress note) to an issue — the mid-flight memory."""

    # --- ADR / knowledge base --------------------------------------------
    @abstractmethod
    def list_adrs(self, project: Project) -> list[Adr]:
        """All ADRs for the project (for retrieval-before-reasoning)."""

    @abstractmethod
    def create_adr(
        self, project: Project, title: str, body: str, status: str = "proposed"
    ) -> Adr:
        """Record a new ADR in the project's knowledge base."""

    def import_adr(
        self,
        project: Project,
        *,
        adr_id: str,
        title: str,
        body: str,
        historical_status: str,
        source_ref: str,
        source_created: int | None,
        source_updated: int | None,
        expected_source_sha256: str,
        supersedes: tuple[str, ...] | object = _UNSPECIFIED_ADR_RELATION,
        superseded_by: str | None | object = _UNSPECIFIED_ADR_RELATION,
        issue_refs: tuple[str, ...] | object = _UNSPECIFIED_ADR_RELATION,
    ) -> Adr:
        """Import one bounded historical ADR snapshot without accepting it.

        Import is intentionally a distinct optional port from native creation so a
        provider cannot silently reinterpret a historical status as a lifecycle
        transition. Providers without a provenance-preserving implementation must
        refuse explicitly.
        """
        del (
            project,
            adr_id,
            title,
            body,
            historical_status,
            source_ref,
            source_created,
            source_updated,
            expected_source_sha256,
            supersedes,
            superseded_by,
            issue_refs,
        )
        raise TrackerCapabilityUnavailableError(self.name, "adr_historical_import")

    def import_adr_batch(
        self, project: Project, records: tuple[dict, ...]
    ) -> list[Adr]:
        """Import one closed historical ADR manifest without implicit acceptance."""
        del project, records
        raise TrackerCapabilityUnavailableError(self.name, "adr_historical_batch_import")

    @abstractmethod
    def set_adr_status(
        self, adr: Adr, status: str, project: Project | None = None
    ) -> None:
        """Move an ADR along proposed → accepted → …"""

    def supersede_adr(
        self, adr: Adr, replacement_id: str, project: Project | None = None
    ) -> None:
        """Supersede one accepted ADR with another, when supported."""
        raise TrackerCapabilityUnavailableError(self.name, "adr_supersession")

    def link_adr_issue(
        self, adr: Adr, issue_ref: str, project: Project | None = None
    ) -> Adr:
        """Append a reciprocal ADR-to-issue relation when supported."""
        raise TrackerCapabilityUnavailableError(self.name, "adr_issue_link")
