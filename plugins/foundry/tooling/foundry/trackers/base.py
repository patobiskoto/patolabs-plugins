"""The Tracker port — the only surface the pipeline knows about a tracker.

Every method speaks normalized models (Issue, Adr, Project). Concrete adapters
(YouTrack today; Jira / GitHub Projects tomorrow) implement this. Add a provider
by writing one subclass — the query/write tiers and the skills never change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy

from foundry.models import (
    Adr,
    EpicClosureOutcome,
    EpicClosureReceipt,
    Issue,
    Project,
    ReleaseScope,
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


class AdrIssueUnavailableError(RuntimeError):
    """An ADR's declared issue relation is absent or inaccessible.

    The pair is preserved so the one read-only ``query issue`` projection can
    report the exact broken relation without treating the requested issue as
    unavailable.  ADR reads and writes deliberately continue to fail closed.
    """

    def __init__(self, adr_id: str, issue_id: str):
        super().__init__(f"ADR issue unavailable: {adr_id} -> {issue_id}")
        self.adr_id = adr_id
        self.issue_id = issue_id


class AcceptanceSyncUnavailableError(RuntimeError):
    """The active tracker cannot safely synchronize acceptance checkboxes."""


class BodyUpdateUnavailableError(RuntimeError):
    """The active tracker cannot perform a bounded body replacement."""


class TrackerConflictError(RuntimeError):
    """A provider version/body changed before a bounded tracker mutation."""


def migration_source_only_issue_refs(value: object) -> list[str]:
    """Validate exact issue coordinates retained without a migrated target issue."""
    if (
        not isinstance(value, list)
        or any(
            not isinstance(ref, str)
            or len(ref.split(":", 2)) != 3
            or ref.split(":", 2)[0] not in {"youtrack", "linear", "ghprojects"}
            or ref.split(":", 2)[1] != "issue"
            or not ref.split(":", 2)[2]
            for ref in value
        )
        or value != sorted(set(value))
    ):
        raise TrackerConflictError("références issues source de migration invalides")
    return value


class TrackerCapabilityUnavailableError(RuntimeError):
    """The active tracker cannot safely provide one explicitly named capability."""

    def __init__(self, tracker: str, capability: str):
        super().__init__(f"capability unavailable: {tracker}.{capability}")
        self.tracker = tracker
        self.capability = capability


class ReleaseScopeUnavailableError(RuntimeError):
    """An exact release coordinate is absent, unmapped, inaccessible or invalid."""

    def __init__(self, tracker: str, release: str, reason: str):
        super().__init__(f"release scope unavailable: {tracker}:{release}:{reason}")
        self.tracker, self.release, self.reason = tracker, release, reason


class EpicClosureUnavailableError(RuntimeError):
    """The active tracker lacks atomic, audited non-code Epic closure."""


class ProjectProvisioningUnavailableError(RuntimeError):
    """The active tracker does not expose project provisioning."""


class EpicSubgraphUnavailableError(RuntimeError):
    """The active tracker does not expose a complete versioned Epic subgraph."""


class Tracker(ABC):
    name: str = "abstract"
    bounded_transition_proofs: bool = False
    bounded_state_transitions: bool = False
    requires_mutation_binding: bool = False
    acceptance_sync_supported: bool = False
    epic_closure_supported: bool = False
    # PAT-ADR-0006's bounded S1-S5 closure.  This is deliberately distinct from
    # the DevHub provider transaction advertised by ``epic_closure_supported``.
    bounded_epic_closure_supported: bool = False
    project_provisioning_supported: bool = False
    project_provisioning_requires_repository: bool = False
    epic_subgraph_supported: bool = False
    append_only_lifecycle_supported: bool = False
    acceptance_proof_projection_supported: bool = False
    acceptance_override_projection_supported: bool = False
    cockpit_evidence_projection_supported: bool = False

    # PAT-64 migration is deliberately a separate port.  ``create_issue`` is
    # not an import primitive: several providers generate a fresh client id on
    # every call and therefore cannot safely be replayed by an orchestrator.
    # Adapters opt in only when they can keep and look up the source coordinate.
    migration_supported_attributes: frozenset[str] = frozenset()

    def migration_preflight(
        self, project: Project, records: tuple[dict, ...],
    ) -> dict:
        """Qualify the target provenance profile before the first provider effect."""
        del project, records
        raise TrackerCapabilityUnavailableError(self.name, "migration_provenance_profile")

    def migration_attribute_exceptions(
        self, project: Project, snapshot: dict,
    ) -> dict[str, str]:
        """Describe source values this target cannot reproduce exactly.

        Attribute names advertised by ``migration_supported_attributes`` still need
        value-level qualification.  Returning an exception keeps the source value in
        the immutable manifest while authorizing the target adapter to omit or replace
        only that named value.  The default assumes every advertised value is exact.
        """
        del project, snapshot
        return {}

    def migration_export_adrs(self, project: Project) -> list[dict]:
        """Return ADR source bytes plus relation knowledge available at the provider."""
        return [
            {
                "adr": adr,
                "relations": {
                    "supersedes": "unknown",
                    "superseded_by": "unknown",
                    "issues": "unknown",
                },
                "source_created": None,
                "source_updated": None,
            }
            for adr in self.list_adrs(project)
        ]

    def read_release_scope(self, project: Project, release: str) -> ReleaseScope:
        """Read one explicitly mapped provider release without approximate discovery."""
        del project
        raise ReleaseScopeUnavailableError(self.name, release, "unsupported")

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

    def verify_project_identity(self, project: Project) -> bool:
        """Read back an existing provider project before publishing a local binding."""
        del project
        raise ProjectProvisioningUnavailableError(
            f"vérification de projet indisponible pour le tracker {self.name}"
        )

    # --- resolution -------------------------------------------------------
    @abstractmethod
    def resolve_project(self, repo: str) -> Project:
        """Map a repo basename to its tracker project (raises if unknown)."""

    def observe_issue(self, issue_id: str) -> Issue:
        """Return the portable lifecycle observation used by query and resume.

        Providers with append-only proofs may override this to expose a native /
        normalized disagreement without treating it as a successful transition.
        """
        return self.get_issue(issue_id)

    def recover_done_projection(
        self, issue_id: str, *, pr_url: str, head_sha: str, base_sha: str,
        merge_sha: str, project: Project | None = None,
    ) -> bool:
        """Repair only a native done projection backed by an exact receipt."""
        del issue_id, pr_url, head_sha, base_sha, merge_sha, project
        return False

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
        from foundry import registry
        selection = registry.repository_tracker_selection(cwd)
        if selection["tracker"] != self.name:
            raise SystemExit(
                f"Binding tracker refusé : dépôt actif sur '{selection['tracker']}', "
                f"pas '{self.name}'."
            )
        if selection["mode"] == "v1":
            binding = selection["binding"]
            assert binding is not None
            if checkout_identity is not None:
                observed = registry.canonical_repository_identity(checkout_identity)
                if observed != binding.repository:
                    raise SystemExit("Binding tracker incompatible avec le checkout.")
            return binding.project
        project = selection["project"]
        assert isinstance(project, Project)
        return project

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
        base_sha: str,
        merge_sha: str,
        project: Project | None = None,
    ) -> bool:
        """Append only the missing override receipt of an already-merged issue."""
        del issue_id, reason, pr_url, head_sha, base_sha, merge_sha, project
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
        """Verify ``receipt``, close its parent and audit the result.

        DevHub atomically locks the graph and persists state plus audit.  A V1
        provider may instead implement PAT-ADR-0006's explicitly weaker bounded
        read/one-parent-write/append-only-audit/readback sequence.  It must expose
        that capability separately and never claim CAS or a transaction.
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

    def get_pending_epic_closure(
        self,
        project: Project,
        parent_id: str,
    ) -> EpicClosureReceipt | None:
        """Return one exact append-only audit awaiting its parent transition.

        Atomic providers never expose this intermediate state.  PAT-ADR-0006
        adapters use it only to resume the original predecessor coordinates
        after interruption; it cannot authorize a newly captured receipt.
        """
        return None

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

    # --- bounded tracker migration (PAT-64) -----------------------------
    def migration_find_issue(self, project: Project, source_ref: str) -> Issue | None:
        """Return the prior imported issue for an exact source coordinate.

        This is a lookup, not a fuzzy title/body search.  Returning ``None``
        authorizes one import; providers which cannot make that distinction
        must refuse rather than let a resume duplicate work.
        """
        del project, source_ref
        raise TrackerCapabilityUnavailableError(self.name, "migration_issue_import")

    def migration_import_issue(
        self, project: Project, snapshot: dict, *, source_ref: str,
    ) -> Issue:
        """Create or recover one faithful living-work snapshot.

        ``snapshot`` is the closed manifest record.  The adapter owns how it
        preserves ``source_ref`` and must make an exact replay return the same
        target object without a second provider write.
        """
        del project, snapshot, source_ref
        raise TrackerCapabilityUnavailableError(self.name, "migration_issue_import")

    def migration_link_issue(
        self,
        project: Project,
        src_id: str,
        link_type: str,
        dst_id: str,
    ) -> None:
        """Create one relation through an explicitly staged migration target."""
        del project, src_id, link_type, dst_id
        raise TrackerCapabilityUnavailableError(self.name, "migration_issue_link")

    def migration_find_adr(self, project: Project, source_ref: str) -> Adr | None:
        """Return the prior imported ADR for an exact source coordinate."""
        del project, source_ref
        raise TrackerCapabilityUnavailableError(self.name, "migration_adr_import")

    def migration_prepare_adr(self, project: Project, snapshot: dict) -> dict:
        """Translate source graph coordinates into this target's ADR namespace."""
        del project
        return deepcopy(snapshot)

    def migration_qualify_adrs(
        self, project: Project, snapshots: tuple[dict, ...],
    ) -> object | None:
        """Return target evidence required before the first authoritative ADR effect.

        Providers whose ADR codec needs no source-specific qualification return
        ``None``.  A provider that needs qualification may write only isolated,
        non-authoritative probes here; the orchestrator persists the returned
        evidence before it calls :meth:`migration_import_adrs`.
        """
        del project, snapshots
        return None

    def migration_import_adr(
        self, project: Project, snapshot: dict, *, source_ref: str,
    ) -> Adr:
        """Create or recover one ADR while retaining its exact source body."""
        del project, snapshot, source_ref
        raise TrackerCapabilityUnavailableError(self.name, "migration_adr_import")

    def migration_import_adrs(
        self, project: Project, snapshots: tuple[dict, ...],
        *, qualification: object | None = None,
    ) -> list[Adr]:
        """Import one closed ADR corpus; adapters may override for graph atomicity."""
        if qualification is not None:
            raise TrackerConflictError(
                f"{self.name} ADR migration qualification is unexpected"
            )
        return [
            self.migration_find_adr(project, snapshot["source_ref"])
            or self.migration_import_adr(
                project, snapshot, source_ref=snapshot["source_ref"],
            )
            for snapshot in snapshots
        ]

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
