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


@dataclass(frozen=True)
class EpicClosureChild:
    """One exact required-child coordinate in an Epic closure receipt."""

    id: str
    version: int
    state: str


@dataclass(frozen=True)
class EpicClosureReceipt:
    """Provider-neutral snapshot a supporting tracker must verify atomically."""

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

    def to_dict(self) -> dict[str, Any]:
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
