"""Normalized domain models the pipeline reasons about.

The skills and the query/write tiers only ever see these — never a raw YouTrack
custom-field blob or a GitHub PR payload. Adapters translate provider shapes to
and from these dataclasses, so swapping a provider never touches the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Link:
    """A typed relation between two issues (subtask, relates, depends…)."""
    type: str                 # normalized: "subtask-of", "parent-of", "relates", "depends-on", "blocks"
    direction: str            # "outward" | "inward"
    target: str               # id of the linked issue


@dataclass
class Issue:
    id: str                             # human id, e.g. FOUNDRY-42
    title: str
    state: Optional[str] = None
    priority: Optional[str] = None
    estimate: Optional[float] = None
    milestone: Optional[str] = None
    type: Optional[str] = None
    labels: list[str] = field(default_factory=list)
    ac_done: int = 0
    ac_total: int = 0
    links: list[Link] = field(default_factory=list)
    pr_url: Optional[str] = None
    body: Optional[str] = None
    # progress notes [{text, created}] — hydrated by get_issue only, not by search
    comments: list[dict] = field(default_factory=list)
    created: Optional[int] = None       # epoch ms
    updated: Optional[int] = None       # epoch ms
    version: Optional[int] = None       # provider concurrency coordinate, when exposed
    # A lifecycle observation deliberately keeps proof-derived and provider-native
    # coordinates distinct.  ``state`` remains the backwards-compatible display
    # value; callers that need to decide or resume must inspect this triplet.
    normalized_state: Optional[str] = None
    native_state: Optional[str] = None
    projection_status: Optional[str] = None  # aligned | native-only | disagreement | unknown
    # Qualified acceptance authority exposed by adapters for bounded Epic
    # closure.  Counts alone are never proof: 0/0 is unknown, and an explicit
    # waiver remains distinct from an accepted review/body projection.
    acceptance_status: Optional[str] = None  # accepted | override | unknown
    acceptance_source: Optional[str] = None
    acceptance_coordinates: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Adr:
    id: str                             # e.g. FOUNDRY-ADR-0003
    title: str
    status: str = "proposed"            # proposed | accepted | deprecated | superseded
    body: Optional[str] = None
    ref: Optional[str] = None           # provider-native id (article idReadable)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Project:
    key: str                            # short ticker, e.g. FOUNDRY
    id: str                             # provider-native project id
    extra: dict[str, Any] = field(default_factory=dict)  # board/sprint ids, etc.


@dataclass(frozen=True)
class ReleaseIssue:
    """One locale-neutral issue fact in an exact native release scope."""

    id: str
    title: str
    type: Optional[str]
    state: Optional[str]
    labels: tuple[str, ...]
    disposition: str  # accepted | deviated | unfinished | unavailable
    references: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["labels"] = list(self.labels)
        return value


@dataclass(frozen=True)
class ReleaseScope:
    """Provider-neutral read of one mapped release, distinct from product and Epic."""

    provider: str
    project_key: str
    project_id: str
    release: str
    release_id: str
    native_state: Optional[str]
    issues: tuple[ReleaseIssue, ...]
    closure: dict[str, Any]
    coordinates: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "project_key": self.project_key,
            "project_id": self.project_id,
            "release": self.release,
            "release_id": self.release_id,
            "native_state": self.native_state,
            "issues": [issue.to_dict() for issue in self.issues],
            "closure": dict(self.closure),
            "coordinates": dict(self.coordinates),
        }


@dataclass(frozen=True)
class TransitionContext:
    """Provider-neutral evidence attached to a mechanical state transition.

    Most trackers ignore it. Providers with proof-bound transitions consume only
    the fields required by their contract; judgment and merge authority stay in
    Foundry's existing review and code-host gates.
    """

    pr_url: Optional[str] = None
    head_sha: Optional[str] = None
    base_sha: Optional[str] = None
    review_digest: Optional[str] = None
    merge_sha: Optional[str] = None
    # The native state observed by the shared write tier immediately before a
    # bounded provider transition.  This is deliberately a coordinate, not a
    # lifecycle proof: adapters still own their native read/write/readback and
    # receipt semantics.
    expected_state: Optional[str] = None


@dataclass(frozen=True)
class MergeDeliveryReceipt:
    """Exact Foundry merge evidence persisted by a qualified tracker adapter."""

    project_key: str
    project_id: str
    issue_id: str
    codehost: str
    repository: str
    pr_number: int
    pr_url: str
    head_sha: str
    base_sha: str
    review_digest: str
    merge_sha: str
    body_sha256: str
    ac_digest: str
    review_proof_id: str
    review_generation: int
    acceptance: str  # accepted | deviated
    acceptance_source: str
    override_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EpicClosureChild:
    """One exact required-child coordinate in an Epic closure receipt."""

    id: str
    version: int
    state: str
    # The state alone is never acceptance evidence.  These coordinates make the
    # complete child snapshot replayable without treating a waiver as acceptance.
    ac_done: int = 0
    ac_total: int = 0
    acceptance_status: str | None = None
    acceptance_source: str | None = None
    acceptance_coordinates: str | None = None


@dataclass(frozen=True)
class EpicClosureDependency:
    """One exact dependency edge and target snapshot in an Epic closure."""

    source_id: str
    target: EpicClosureChild


@dataclass(frozen=True)
class EpicClosureReceipt:
    """Provider-neutral closure snapshot; each adapter enforces its declared gate."""

    project_key: str
    project_id: str
    parent_id: str
    parent_version: int
    parent_type: str
    parent_ac_done: int
    parent_ac_total: int
    children: tuple[EpicClosureChild, ...]
    issued_at: int
    nonce: str
    # A category-1 product decision (FOUNDRY-ADR-0017), supplied explicitly by
    # the human closing the Epic.  ``None`` is intentionally not success.
    human_verdict: str | None = None
    # PAT-ADR-0006 bounded providers bind the original predecessor, the Epic's
    # validation text, and the complete dependency graph. Defaults preserve
    # DevHub's existing atomic wire contract and historical receipts byte-for-byte.
    parent_state: str | None = None
    dependencies: tuple[EpicClosureDependency, ...] = ()
    parent_validation_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        # DevHub's atomic v1 receipt predates PAT-69 and is a byte-stable public
        # contract.  Keep its historical shape; bounded receipts carry the
        # additional predecessor, acceptance and dependency coordinates.
        if self.human_verdict is None:
            return {
                "project_key": self.project_key,
                "project_id": self.project_id,
                "parent_id": self.parent_id,
                "parent_version": self.parent_version,
                "parent_type": self.parent_type,
                "parent_ac_done": self.parent_ac_done,
                "parent_ac_total": self.parent_ac_total,
                "children": [
                    {
                        "id": child.id,
                        "version": child.version,
                        "state": child.state,
                    }
                    for child in self.children
                ],
                "issued_at": self.issued_at,
                "nonce": self.nonce,
            }
        return asdict(self)


@dataclass(frozen=True)
class EpicClosureOutcome:
    """Closed, audited provider result; no code-host or merge claim is implied."""

    receipt: EpicClosureReceipt
    closed_parent_version: int
    audit_id: str
    replayed: bool = False


@dataclass
class PullRequest:
    number: int
    url: str
    head: str
    base: str
    base_sha: Optional[str] = None
    sha: Optional[str] = None
    merged: bool = False
    merge_sha: Optional[str] = None
    state: str = "open"                  # open | closed


@dataclass
class Check:
    name: str
    status: str                         # queued | in_progress | completed
    conclusion: Optional[str] = None    # success | failure | neutral | cancelled | ...
