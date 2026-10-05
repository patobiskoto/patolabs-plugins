"""Fail-closed Linear adapter for Foundry's provider-neutral Tracker port.

The adapter uses only explicit registry coordinates: canonical repository identity,
Linear team UUID, Linear project UUID, and a complete normalized-state -> workflow-
state UUID map. Runtime checkout resolution matches the actual canonical Git remote;
it never selects a binding from an environment alias, basename, title, or issue key.

Linear does not expose a compare-and-swap precondition for existing-issue replacement.
Foundry consequently uses bounded detection for the explicitly targeted grooming
properties: one fresh read, one targeted write, and one readback.  This detects a
divergence before the read or after the write, but cannot exclude an external writer in
the S1→S2 window; it is neither CAS nor a distributed lock.  Untargeted properties are
not sent to ``issueUpdate`` (labels use provider delta inputs).  Lifecycle and ADR
writes retain their append-only paths where those are available.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import copy
from datetime import datetime, timezone
import hashlib
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid

from foundry import config, registry
from foundry.models import (
    Adr,
    EpicClosureChild,
    EpicClosureDependency,
    EpicClosureOutcome,
    EpicClosureOverride,
    EpicClosureReceipt,
    Issue,
    Link,
    Project,
    ReleaseIssue,
    ReleaseScope,
    TransitionContext,
)
from foundry.trackers.base import (
    _UNSPECIFIED_ADR_RELATION,
    AcceptanceSyncUnavailableError,
    AdrIssueUnavailableError,
    AdrUnavailableError,
    IssueUnavailableError,
    Tracker,
    TrackerBindingError,
    TrackerCapabilityUnavailableError,
    TrackerConflictError,
    ReleaseScopeUnavailableError,
    migration_source_only_issue_refs,
)
from foundry.trackers.epic_intent import EpicAuditIntent


LINEAR_GRAPHQL_ENDPOINT = "https://api.linear.app/graphql"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_PAGE_SIZE = 100
_MAX_PAGES = 100
_STATES = frozenset(
    {"backlog", "ready", "in-progress", "review", "blocked", "done", "dropped"}
)
_PRIORITY_TO_LINEAR = {"P0": 1, "P1": 2, "P2": 3, "P3": 4, None: 0}
_LINEAR_TO_PRIORITY = {value: key for key, value in _PRIORITY_TO_LINEAR.items()}
_AC_CHECKBOX = re.compile(r"(?m)^\s*[-*+]\s+\[(?P<mark>[ xX])\]\s+.+?\s*$")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{1,255}")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_LIFECYCLE_SCHEMA = "foundry-linear-lifecycle.v1"
_LIFECYCLE_HEADER = "Foundry lifecycle proof (append-only)."
_LIFECYCLE_OPERATIONS = frozenset(
    {
        "state-in-progress",
        "state-review",
        "state-done",
        "acceptance",
        "acceptance-override",
    "cockpit-evidence",
    }
)
# Public audit code of an explicit human AC override (never free text).
_AC_OVERRIDE_REASON = re.compile(r"[a-z0-9_-]{3,80}\Z")
_AC_OVERRIDE_BOUND_KEYS = ("pr_url", "head_sha", "base_sha", "review_digest")
_ADR_SCHEMA = "foundry-linear-adr.v1"
_ADR_HEADER = "<!-- foundry-linear-adr.v1\n"
_ADR_DOCUMENT_PREFIX = "[Foundry ADR] "
_ADR_WITNESS_SCHEMA = "foundry-linear-adr-witness.v1"
_ADR_WITNESS_HEADER = "<!-- foundry-linear-adr-witness.v1\n"
_ADR_WITNESS_PREFIX = "[Foundry ADR witness] "
_ADR_QUALIFICATION_PROBE_PREFIX = "[Foundry qualification probe] "
# A historical batch record carries evidence of the complete provider bytes of
# both Documents it creates, observed on non-authoritative probes before import.
_ADR_BATCH_RECOVERY_PROFILE_FIELDS = frozenset({
    "qualification_project_id",
    "expected_linear_witness_content",
    "expected_linear_witness_sha256",
    "witness_probe_id",
})
_ADR_BATCH_PROFILE_FIELDS = _ADR_BATCH_RECOVERY_PROFILE_FIELDS | {
    "expected_linear_document_content",
    "expected_linear_document_sha256",
    "document_probe_id",
}
_ADR_SOURCE_ENCODING = "base64-utf8"
_ADR_BOUND_SOURCE_BODY = "_foundry_bound_source_body"
_ADR_ISSUE_LINK_SCHEMA = "foundry-linear-adr-issue-link.v1"
_ADR_ID = re.compile(r"[A-Z][A-Z0-9_-]*-ADR-[0-9]{4}\Z")
_ISSUE_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}\Z")
_ISSUE_NUMBER = re.compile(r"[1-9][0-9]*\Z")
_ADR_STATUSES = frozenset({"proposed", "accepted", "deprecated", "superseded"})
_ADR_RELATION_LIMIT = 100
_ADR_MISSING_RELATION_FAMILIES = ("issues", "superseded_by", "supersedes")
_ADR_TRANSITIONS = {
    "proposed": frozenset({"accepted", "deprecated"}),
    "accepted": frozenset({"deprecated", "superseded"}),
    "deprecated": frozenset(),
    "superseded": frozenset(),
}


_ISSUE_FIELDS = """
id identifier title description priority estimate createdAt updatedAt
state { id name }
team { id key }
project { id }
projectMilestone { id name }
labels(first: 100) { nodes { id name } pageInfo { hasNextPage endCursor } }
parent { id identifier }
children(first: 100) { nodes { id identifier } pageInfo { hasNextPage endCursor } }
relations(first: 100) {
  nodes { type relatedIssue { id identifier } }
  pageInfo { hasNextPage endCursor }
}
inverseRelations(first: 100) {
  nodes { type issue { id identifier } }
  pageInfo { hasNextPage endCursor }
}
comments(first: 100) {
  nodes { id body createdAt } pageInfo { hasNextPage endCursor }
}
"""

_ISSUE_QUERY = f"""
query FoundryLinearIssue($id: String!) {{
  issue(id: $id) {{ {_ISSUE_FIELDS} }}
}}
"""

_ISSUE_STATE_HISTORY_QUERY = """
query FoundryLinearIssueStateHistory($id: String!) {
  issue(id: $id) {
    id identifier state { id }
    stateHistory(first: 100) {
      nodes { id stateId startedAt endedAt }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_PROJECT_BINDING_QUERY = """
query FoundryLinearProjectBinding($id: String!, $teamId: ID!) {
  project(id: $id) {
    id
    teams(filter: { id: { eq: $teamId } }, first: 2) {
      nodes { id key }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_ISSUES_QUERY = f"""
query FoundryLinearIssues($teamId: ID!, $projectId: ID!, $after: String) {{
  issues(
    filter: {{ team: {{ id: {{ eq: $teamId }} }}, project: {{ id: {{ eq: $projectId }} }} }}
    first: 100
    after: $after
  ) {{
    nodes {{ {_ISSUE_FIELDS} }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""

_PROJECT_RELEASES_QUERY = """
query FoundryLinearProjectMilestones($id: String!, $after: String) {
  project(id: $id) {
    id
    projectMilestones(first: 100, after: $after) {
      nodes { id name }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_RELEASE_ISSUES_QUERY = f"""
query FoundryLinearReleaseIssues(
  $teamId: ID!, $projectId: ID!, $milestoneId: ID!, $after: String
) {{
  issues(
    filter: {{
      team: {{ id: {{ eq: $teamId }} }},
      project: {{ id: {{ eq: $projectId }} }},
      projectMilestone: {{ id: {{ eq: $milestoneId }} }}
    }}
    first: 100
    after: $after
  ) {{
    nodes {{ {_ISSUE_FIELDS} }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""

_RELEASE_NESTED_FIELDS = {
    "labels": "id name",
    "children": "id identifier",
    "relations": "type relatedIssue { id identifier }",
    "inverseRelations": "type issue { id identifier }",
    "comments": "id body createdAt",
}

_ISSUE_CREATE = """
mutation FoundryLinearIssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { id identifier } }
}
"""

_ISSUE_UPDATE = """
mutation FoundryLinearIssueUpdate($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success issue { id identifier } }
}
"""

_RELATION_CREATE = """
mutation FoundryLinearIssueRelationCreate($input: IssueRelationCreateInput!) {
  issueRelationCreate(input: $input) {
    success issueRelation { id type issue { id identifier } relatedIssue { id identifier } }
  }
}
"""

_RELATION_QUERY = """
query FoundryLinearIssueRelationById($id: String!) {
  issueRelation(id: $id) {
    id type issue { id identifier } relatedIssue { id identifier }
  }
}
"""

_COMMENT_CREATE = """
mutation FoundryLinearCommentCreate($input: CommentCreateInput!) {
  commentCreate(input: $input) { success comment { id body issue { id identifier } } }
}
"""

_COMMENT_QUERY = """
query FoundryLinearCommentsById($id: ID!) {
  comments(filter: { id: { eq: $id } }, first: 1) {
    nodes { id body issue { id identifier } }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_ADR_DOCUMENTS_QUERY = """
query FoundryLinearAdrDocuments($projectId: ID!, $after: String) {
  documents(filter: { project: { id: { eq: $projectId } } } first: 100 after: $after includeArchived: true) {
    nodes { id title content archivedAt project { id } }
    pageInfo { hasNextPage endCursor }
  }
}
"""
_ADR_DOCUMENT_BY_ID_QUERY = """
query FoundryLinearAdrDocumentById($id: ID!) {
  documents(filter: { id: { eq: $id } }, first: 2, includeArchived: true) {
    nodes { id title content archivedAt project { id } }
    pageInfo { hasNextPage endCursor }
  }
}
"""
_ADR_DOCUMENT_CREATE = """
mutation FoundryLinearAdrDocumentCreate($input: DocumentCreateInput!) {
 documentCreate(input: $input) { success document { id title content project { id } } }
}
"""

_TEAM_GIT_AUTOMATION_STATES_QUERY = """
query FoundryLinearTeamGitAutomationStates($id: String!) {
  team(id: $id) {
    id
    gitAutomationStates(first: 100) {
      nodes { id event state { id type } }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_GIT_AUTOMATION_EVENTS = frozenset({"draft", "merge", "mergeable", "review", "start"})
_WORKFLOW_STATE_TYPES = frozenset(
    {"backlog", "triage", "unstarted", "started", "completed", "canceled", "duplicate"}
)


# PAT-98: bounded retry of READ-ONLY calls only. A write is never retried (PAT-ADR-0006
# S2: its effect may be invisible). Readonly-ness is decided from the GraphQL document.
_sleep = time.sleep  # module-level seam: tests replace it, so the suite never sleeps
_READ_RETRY_DELAYS = (1.0, 2.0, 4.0)  # deterministic, no jitter: at most 7 s per call
# 429 is always classified as quota exhaustion first (typed error, never retried).
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_RATE_LIMIT_CODES = frozenset({"RATELIMITED", "RATE_LIMITED"})
_QUERY_DOCUMENT = re.compile(r"\s*query\b")
_WRITE_OR_STREAM_WORD = re.compile(r"\b(mutation|subscription)\b")
# Secondary documentation aid only (the document decides): every read operation name.
_READ_OPERATIONS = frozenset({
    "issue.read", "issue.search", "issue.state-history", "adr.list", "adr.read",
    "release.read", "release.issues", "release.labels", "release.children",
    "release.comments", "release.inverseRelations", "project-binding-read",
    "team.git-automation-states", "lifecycle.readback", "lifecycle.comment.read",
    "issue.relation.readback", "issue.readback.labels", "comment.readback",
    "adr.issue.resolve",
})


def _is_read_only_document(document: str) -> bool:
    """Fail closed: only a `query` document with no `mutation`/`subscription` word."""
    return (
        isinstance(document, str)
        and _QUERY_DOCUMENT.match(document) is not None
        and _WRITE_OR_STREAM_WORD.search(document) is None
    )


def _header_int(headers, name: str) -> int | None:
    try:
        value = headers.get(name) if headers is not None else None
        return int(str(value).strip()) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


class LinearTrackerError(RuntimeError):
    """Sanitized transport/provider failure with no response or credential echo.

    `retries` is the number of automatic read retries performed before this failure
    (PAT-98); it is 0 for every failure that was never retried, and then the message is
    unchanged.
    """

    def __init__(self, operation: str, status: int | None, code: str, retries: int = 0):
        self.operation = operation
        self.status = status
        self.code = code
        self.retries = retries
        super().__init__(self._render())

    def _render(self) -> str:
        rendered_status = str(self.status) if self.status is not None else "unavailable"
        suffix = f" after {self.retries} retries" if self.retries else ""
        return f"Linear {self.operation} -> {rendered_status} ({self.code}){suffix}"

    def _with_retries(self, retries: int) -> "LinearTrackerError":
        self.retries = retries
        self.args = (self._render(),)
        return self


class LinearQuotaExhaustedError(LinearTrackerError):
    """Linear API quota exhausted (PAT-98). Never retried automatically."""

    def __init__(
        self, operation: str, status: int | None, remaining: int | None,
        reset_at_ms: int | None, retries: int = 0,
    ):
        self.remaining = remaining
        self.reset_at_ms = reset_at_ms
        self.reset_at = (
            datetime.fromtimestamp(reset_at_ms / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            if reset_at_ms is not None else None
        )
        super().__init__(operation, status, "quota_exhausted", retries)

    def _render(self) -> str:
        remaining = self.remaining if self.remaining is not None else "unknown"
        reset = self.reset_at if self.reset_at is not None else "unknown"
        return (
            f"Linear quota exhausted: {super()._render()}"
            f" remaining={remaining} reset_at={reset}"
        )


class LinearBindingError(TrackerBindingError):
    """A credential-free explicit binding validation failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Linear binding invalid: {code}")


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            target = urllib.parse.urljoin(req.full_url, newurl)
            source = urllib.parse.urlsplit(req.full_url)
            destination = urllib.parse.urlsplit(target)
            source_origin = (
                source.scheme.lower(),
                (source.hostname or "").lower(),
                source.port or 443,
            )
            target_origin = (
                destination.scheme.lower(),
                (destination.hostname or "").lower(),
                destination.port or 443,
            )
        except (TypeError, ValueError, UnicodeError):
            raise LinearTrackerError(
                "redirect", code, "cross_origin_redirect"
            ) from None
        if source_origin != target_origin:
            raise LinearTrackerError("redirect", code, "cross_origin_redirect")
        return super().redirect_request(req, fp, code, msg, headers, target)


def _safe_id(value, code: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise LinearBindingError(code)
    return value


def _mapping(extra: dict, key: str, *, required: bool = False) -> dict[str, str]:
    raw = extra.get(key)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raise LinearBindingError(f"{key}_invalid") from None
    if raw is None and not required:
        return {}
    if not isinstance(raw, dict) or any(
        not isinstance(name, str)
        or not name
        or not isinstance(identifier, str)
        or _SAFE_ID.fullmatch(identifier) is None
        for name, identifier in raw.items()
    ):
        raise LinearBindingError(f"{key}_invalid")
    if len(set(raw.values())) != len(raw):
        raise LinearBindingError(f"{key}_ambiguous")
    return dict(raw)


def _epoch_ms(value) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LinearTrackerError("normalize", None, "invalid_response")
    try:
        return int(
            datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000
        )
    except ValueError:
        raise LinearTrackerError("normalize", None, "invalid_response") from None


def _connection(raw, operation: str) -> list[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list):
        raise LinearTrackerError(operation, None, "invalid_response")
    page = raw.get("pageInfo")
    if not isinstance(page, dict) or type(page.get("hasNextPage")) is not bool:
        raise LinearTrackerError(operation, None, "invalid_response")
    if page["hasNextPage"]:
        raise LinearTrackerError(operation, None, "nested_pagination_unavailable")
    if any(not isinstance(node, dict) for node in raw["nodes"]):
        raise LinearTrackerError(operation, None, "invalid_response")
    return raw["nodes"]


def _relation_link(relation: dict, *, inverse: bool) -> Link:
    operation = "normalize.inverse-relations" if inverse else "normalize.relations"
    relation_type = relation.get("type")
    if not isinstance(relation_type, str):
        raise LinearTrackerError(operation, None, "invalid_response")
    if relation_type not in {"blocks", "related"}:
        raise LinearTrackerError(operation, None, "unsupported_relation_type")

    target = relation.get("issue" if inverse else "relatedIssue")
    if not isinstance(target, dict) or not isinstance(target.get("identifier"), str):
        raise LinearTrackerError(operation, None, "invalid_response")
    if relation_type == "related":
        return Link("relates", "inward" if inverse else "outward", target["identifier"])
    if inverse:
        return Link("depends-on", "outward", target["identifier"])
    return Link("blocks", "inward", target["identifier"])


def _adr_client_uuid(slot: str) -> str:
    digest = hashlib.sha256(slot.encode("utf-8")).hexdigest()
    return str(uuid.UUID(digest[:32], version=4))


def _migration_issue_id(project_id: str, source_ref: str) -> str:
    return _adr_client_uuid(
        f"foundry-linear-migration-issue.v1:{project_id}:{source_ref}"
    )


def _issue_relation_id(
    project_id: str, relation: str, source_id: str, target_id: str,
) -> str:
    """Return the provider id for one canonical append-only relation slot."""
    if relation == "related":
        source_id, target_id = sorted((source_id, target_id))
    return _adr_client_uuid(
        "foundry-linear-issue-relation.v1:"
        f"{project_id}:{relation}:{source_id}:{target_id}"
    )


def _adr_document_id(project_id: str, adr_id: str, sequence: int) -> str:
    return _adr_client_uuid(f"{_ADR_SCHEMA}:{project_id}:{adr_id}:{sequence}")


def _adr_witness_id(project_id: str, adr_id: str, sequence: int) -> str:
    return _adr_client_uuid(
        f"{_ADR_WITNESS_SCHEMA}:{project_id}:{adr_id}:{sequence}"
    )


def _adr_issue_link(
    binding: dict, adr_id: str, issue_id: str
) -> tuple[str, str]:
    payload = {
        "schema": _ADR_ISSUE_LINK_SCHEMA,
        "project_id": binding["project_id"],
        "team_id": binding["team_id"],
        "adr_id": adr_id,
        "issue_id": issue_id,
    }
    canonical = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    comment_id = _adr_client_uuid(
        f"{_ADR_ISSUE_LINK_SCHEMA}:{binding['project_id']}:{adr_id}:{issue_id}"
    )
    return comment_id, f"Foundry ADR relation (reciprocal).\n\n{canonical}"


def _adr_document_title(metadata: dict) -> str:
    return f"{_ADR_DOCUMENT_PREFIX}{metadata['id']} / v{metadata['sequence']:04d} / {metadata['title']}"


def _adr_document_content(metadata: dict, body: str) -> str:
    header = json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return f"{_ADR_HEADER}{header}\n-->\n\n{body}"


def _markdown_fence_opening(line: str) -> tuple[str, int] | None:
    """Return a descriptor for one supported Markdown fence opener."""
    candidate = line.lstrip(" ")
    if len(line) - len(candidate) > 3 or not candidate:
        return None
    marker = candidate[0]
    if marker not in {"`", "~"}:
        return None
    length = len(candidate) - len(candidate.lstrip(marker))
    if length < 3:
        return None
    info = candidate[length:]
    if marker == "`" and "`" in info:
        return None
    return marker, length


def _markdown_fence_closing(
    line: str,
    marker: str,
    opening_length: int,
) -> bool:
    candidate = line.lstrip(" ")
    if len(line) - len(candidate) > 3:
        return False
    length = len(candidate) - len(candidate.lstrip(marker))
    return (
        length >= opening_length
        and not candidate[length:].strip(" \t")
    )


_FOUNDRY_ADR_0012_READBACK_PROFILE = "foundry-adr-0012-v1"
_FOUNDRY_ADR_0012_SOURCE_SHA256 = (
    "69bd2cb2be04313de06dee865187b5e0e68eaed00298a7ce2be124f6ca1e96e5"
)
_FOUNDRY_ADR_0012_READBACK_SHA256 = (
    "3bbab7c31d737bb97320ea74644f8acd6201cf7ec0a8cb7e1a09cd554c49246c"
)
# PAT-72 records one surviving native version whose provider readback collapsed
# precisely one extra blank line. These digests deliberately name the whole
# source and rendered bodies; this is not a Markdown whitespace normalization.
_PAT_72_SOURCE_SHA256 = (
    "17aafead50bd87f6578dbb78dba4eba12502360f7acad50ba3bc78164a289e0b"
)
_PAT_72_READBACK_SHA256 = (
    "7d1ad9357556881edb5ffd8de3e16cd4189313d963b7fbcdea4c785e30c33aaa"
)
_PAT_72_BLANK_LINE_FRAGMENT = (
    "append-only à identifiant déterministe, il est préféré à un remplacement en place.\n"
    "\n"
    "\n"
    "**Portée des propriétés.**"
)
_PAT_72_RENDERED_FRAGMENT = _PAT_72_BLANK_LINE_FRAGMENT.replace("\n\n\n", "\n\n")
# This historical slot predates witnesses. Its source is deliberately not
# represented here: the digests qualify the exact private source and bytes already
# held by Linear. Version 0 remains recovery-only; the separately pinned body delta
# below is the forward rule for later version Documents.
_FOUNDRY_ADR_0001_SOURCE_SHA256 = (
    "eea144009b8ee8ff5846051ed70fe35d1cf920a78cb4de0ba74d2d616f8535db"
)
_FOUNDRY_ADR_0001_ID = "FOUNDRY-ADR-0001"
_FOUNDRY_ADR_0001_READBACK_SHA256 = (
    "9d723a7a64225d930531d968f78dba108f940eb7091d7110f8b12c037d29b193"
)
_FOUNDRY_ADR_0001_BODY_READBACK_SHA256 = (
    "aa5fee81139bdc046d6056e95a3a4b5218b8f032821fa4048c8f6c02b5493bc5"
)
# Exact forward delta observed for the digest-pinned historical source above. The
# replacements contain only observed Markdown serialization fragments; the private source and rendered
# body stay outside the repository. Applying the delta to a neighbouring source is
# impossible because the source digest is checked first, and the result digest is
# checked before it can authorize a write.
_FOUNDRY_ADR_0001_READBACK_EDITS = (
    (31, 32, ""),
    (1516, 1516, "**"),
    (1517, 1517, "**"),
    (1601, 1601, "**"),
    (1602, 1602, "**"),
    (2148, 2149, "\n*"),
    (2445, 2446, "*"),
    (2684, 2684, "\n"),
    (3118, 3119, "\n*"),
    (3126, 3126, "**"),
    (3134, 3136, ""),
    (3288, 3288, "**"),
    (3291, 3291, "**"),
    (3353, 3354, "*"),
    (3361, 3361, "**"),
    (3369, 3371, ""),
    (3430, 3430, "**"),
    (3433, 3433, "**"),
    (3694, 3695, "\n*"),
    (3848, 3849, "*"),
    (4098, 4098, "**"),
    (4101, 4101, "**"),
    (4289, 4290, "\n*"),
    (4388, 4389, "*"),
    (4577, 4578, "*"),
    (4664, 4665, "*"),
    (4812, 4812, "\n"),
    (5492, 5492, " "),
    (5494, 5495, " "),
    (5496, 5496, " "),
    (5498, 5499, " "),
    (5500, 5500, " "),
    (5502, 5503, " "),
    (5504, 5504, " "),
    (5506, 5507, " "),
    (5542, 5544, ""),
    (5560, 5562, ""),
    (5755, 5757, ""),
    (5775, 5777, ""),
    (6103, 6105, ""),
    (6122, 6124, ""),
    (7225, 7225, "["),
    (7231, 7231, "](<http://adr.py>)"),
    (7505, 7506, "*"),
    (7824, 7825, "*"),
    (7850, 7850, "**"),
    (7863, 7865, ""),
    (8024, 8025, "*"),
    (8042, 8042, "**"),
    (8068, 8070, ""),
    (8236, 8237, "*"),
    (8584, 8584, "**"),
    (8585, 8585, "**"),
    (8794, 8795, "*"),
    (8829, 8830, "*"),
    (8974, 8975, "*"),
    (8976, 8978, ""),
    (8985, 8987, ""),
    (9146, 9147, "*"),
    (9396, 9397, "*"),
    (9468, 9469, ""),
)


def _is_recovery_only_historical_source(body: str) -> bool:
    return hashlib.sha256(body.encode("utf-8")).hexdigest() == (
        _FOUNDRY_ADR_0001_SOURCE_SHA256
    )


def _linear_foundry_adr_0001_v1_readback(body: str) -> str | None:
    """Apply the one digest-pinned forward profile observed for ADR-0001."""
    if not _is_recovery_only_historical_source(body):
        return None
    rendered = body
    for start, end, replacement in reversed(_FOUNDRY_ADR_0001_READBACK_EDITS):
        rendered = f"{rendered[:start]}{replacement}{rendered[end:]}"
    if hashlib.sha256(rendered.encode("utf-8")).hexdigest() != (
        _FOUNDRY_ADR_0001_BODY_READBACK_SHA256
    ):
        raise ValueError("foundry-adr-0001-v1 rendering is invalid")
    return rendered


def _is_qualified_historical_readback(observed: object, canonical: str) -> bool:
    """Accept one digest-pinned historical readback, never a renderer family."""
    if not isinstance(observed, str):
        return False
    canonical_header, separator, body = canonical.partition("\n-->\n\n")
    if not separator or not canonical_header.startswith(_ADR_HEADER):
        return False
    expected_metadata = canonical_header[len(_ADR_HEADER) :]
    observed_payload = observed[len(_ADR_HEADER) :] if observed.startswith(
        _ADR_HEADER
    ) else ""
    encoded = None
    for delimiter, serialized in (("\n-->\n\n", False), ("\n\\-->\n\n", True)):
        candidate, found, _observed_body = observed_payload.partition(delimiter)
        if found:
            encoded = candidate.replace("\\[", "[").replace("\\]", "]") if serialized else candidate
            break
    return (
        encoded == expected_metadata
        and _is_recovery_only_historical_source(body)
        and hashlib.sha256(observed.encode("utf-8")).hexdigest()
        == _FOUNDRY_ADR_0001_READBACK_SHA256
    )


def _linear_foundry_adr_0012_v1_readback(body: str) -> str | None:
    """Return the one qualified raw-HTML serialization observed for ADR-0012.

    This is deliberately a source-digest-pinned profile, rather than a raw HTML
    parser. It records Linear's observed insertion of a blank line between the
    four headings and their angle-bracket placeholders, dash-list rewrite, and
    final-newline removal. Any source-body variation remains unsupported.
    """
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != (
        _FOUNDRY_ADR_0012_SOURCE_SHA256
    ):
        return None
    rendered = body
    for heading in (
        "## Contexte",
        "## Décision",
        "## Conséquences",
        "## Alternatives écartées",
    ):
        rendered = rendered.replace(f"{heading}\n", f"{heading}\n\n", 1)
    rendered = rendered.replace("\n- **<Alt>**", "\n* **<Alt>**", 1)
    rendered = rendered.removesuffix("\n")
    if hashlib.sha256(rendered.encode("utf-8")).hexdigest() != (
        _FOUNDRY_ADR_0012_READBACK_SHA256
    ):
        raise ValueError(
            f"{_FOUNDRY_ADR_0012_READBACK_PROFILE} rendering is invalid"
        )
    return rendered


# PAT-86 pins the six observed native PAT-ADR-0011 rendering deltas.
# No autolink or table grammar is generalized beyond this complete source.
_PAT_86_SOURCE_SHA256 = (
    "33982ca3393e9bca78dc4744481b83214cd7bc67248687970cb56f290e5c826e"
)
_PAT_86_READBACK_SHA256 = (
    "ce79b36941e9e745c12f0ecbcf679844abab2bc2a5d30d8683b135200e2fd2be"
)
_PAT_86_FRAGMENTS = (
    (
        "Auth reports claude.ai, firstParty, Pro. Foundry source manifest is 0.9.0;\n",
        (
            "Auth reports [claude.ai](<http://claude.ai>), firstParty, Pro. Foundry source ma"
            "nifest is 0.9.0;\n"
        ),
    ),
    (
        (
            "plan billing rule : https://support.claude.com/en/articles/15424964-claude-fable"
            "-models-on-your-plan.\n"
        ),
        (
            "plan billing rule : [https://support.claude.com/en/articles/15424964-claude-fabl"
            "e-models-on-your-plan](<https://support.claude.com/en/articles/15424964-claude-f"
            "able-models-on-your-plan>).\n"
        ),
    ),
    (
        "model configuration : https://code.claude.com/docs/en/model-config.\n",
        (
            "model configuration : [https://code.claude.com/docs/en/model-config](<https://co"
            "de.claude.com/docs/en/model-config>).\n"
        ),
    ),
    (
        "| --- | --- | --- | --- |\n",
        "| -- | -- | -- | -- |\n",
    ),
    (
        "The supported subagent contract : https://code.claude.com/docs/en/sub-agents\n",
        (
            "The supported subagent contract : [https://code.claude.com/docs/en/sub-agents](<"
            "https://code.claude.com/docs/en/sub-agents>)\n"
        ),
    ),
    (
        (
            "These two proposal documents are updated. Current runtime, AGENTS.md/CLAUDE.md R"
            "7,\n"
        ),
        (
            "These two proposal documents are updated. Current runtime, [AGENTS.md/CLAUDE.md]"
            "(<http://AGENTS.md/CLAUDE.md>) R7,\n"
        ),
    ),
)


def _linear_pat_86_readback(body: str, rendered: str) -> str | None:
    """Apply only the digest-pinned PAT-ADR-0011 forward serialization."""
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != _PAT_86_SOURCE_SHA256:
        return None
    for source, readback in _PAT_86_FRAGMENTS:
        if body.count(source) != 1 or rendered.count(source) != 1:
            raise ValueError("pat-86 rendering source is invalid")
        rendered = rendered.replace(source, readback, 1)
    if hashlib.sha256(rendered.encode("utf-8")).hexdigest() != _PAT_86_READBACK_SHA256:
        raise ValueError("pat-86 rendering is invalid")
    return rendered


# One observed PAT-16 qualification-budget ADR body; not an autolink grammar.
_PAT_16_ADR13_SOURCE_SHA256 = "754275fa572100ece1b8ed427881b00768d822beec72ee69b7d602cc1b747d97"
_PAT_16_ADR13_READBACK_SHA256 = "ca3dadd06cc0f0f42185531f6ef811e98bdfb69d74be4771c52405a3fae49a3a"


def _linear_pat_16_adr13_readback(body: str, rendered: str) -> str | None:
    """Recognize only the complete native PAT-ADR-0013 body observed in Linear."""
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != _PAT_16_ADR13_SOURCE_SHA256:
        return None
    source = "copie de calculator.py dans un dossier temporaire isolé"
    target = "copie de [calculator.py](<http://calculator.py>) dans un dossier temporaire isolé"
    if body.count(source) != 1 or rendered.count(source) != 1:
        raise ValueError("pat-16-adr13 rendering source is invalid")
    rendered = rendered.replace(source, target, 1)
    if hashlib.sha256(rendered.encode("utf-8")).hexdigest() != _PAT_16_ADR13_READBACK_SHA256:
        raise ValueError("pat-16-adr13 rendering is invalid")
    return rendered


def _linear_pat_72_readback(body: str, rendered: str) -> str | None:
    """Return the sole PAT-72 blank-line serialization observed from Linear.

    The source digest and resulting digest bind this to one native source body.
    Fences, other whitespace runs, and every neighbouring source stay on the
    strict general model.
    """
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != _PAT_72_SOURCE_SHA256:
        return None
    if (
        body.count(_PAT_72_BLANK_LINE_FRAGMENT) != 1
        or rendered.count(_PAT_72_BLANK_LINE_FRAGMENT) != 1
    ):
        raise ValueError("pat-72 rendering source is invalid")
    rendered = rendered.replace(
        _PAT_72_BLANK_LINE_FRAGMENT, _PAT_72_RENDERED_FRAGMENT, 1
    )
    if hashlib.sha256(rendered.encode("utf-8")).hexdigest() != _PAT_72_READBACK_SHA256:
        raise ValueError("pat-72 rendering is invalid")
    return rendered


_MULTILINE_INLINE_CODE = re.compile(
    r"(?<![A-Za-z0-9_`])`(?![\s`])(?P<before>[^`\n]+)(?<!\s)\n"
    r"(?P<indent>[ \t]+)(?!\s)(?P<after>[^`\n]+)(?<!\s)`(?![A-Za-z0-9_`])"
)
_MULTILINE_BOLD = re.compile(
    r"(?<![A-Za-z0-9_*\\])\*\*(?![\s*])(?P<before>[^*`_~\[\]\n]+)(?<!\s)\n"
    r"(?P<indent>[ \t]+)(?!\s)(?P<after>[^*`_~\[\]\n]+)(?<!\s)\*\*(?![A-Za-z0-9_*])"
)
# Linear's observed serializer closes a simple strong span before a trailing
# single-backtick code fragment.  This stays narrower than a general Markdown
# transformation: both parts are plain and the code span is non-empty.
_STRONG_TRAILING_INLINE_CODE = re.compile(
    r"(?<![A-Za-z0-9_*\\])\*\*(?![\s*])"
    r"(?P<before>[^*`_~\[\]<>\\&\n\r\v\f\x1c-\x1e\x85\u2028\u2029]+?)(?<!\s) "
    r"(?P<code>`(?![\s`])[^`\n\r\v\f\x1c-\x1e\x85\u2028\u2029]+(?<!\s)`)"
    r"\*\*(?![A-Za-z0-9_*])"
)
_MULTILINE_LINK_DESTINATION = re.compile(r"\]\([^\n]*\n[^)]*\)")
_RAW_INLINE_HTML = re.compile(r"<(?:/?[A-Za-z][A-Za-z0-9-]*(?=[\s/>])|[!?])")


def _paired_delimiters(fragment: str, delimiter: str) -> list[tuple[int, int]]:
    """Return active delimiter pairs; literals and unpaired markers stay text."""
    code_spans = _paired_backtick_delimiters(fragment)
    openers: list[int] = []
    pairs = []
    start = 0
    while (position := fragment.find(delimiter, start)) >= 0:
        if _is_escaped(fragment, position):
            # A Markdown escape consumes only the first punctuation character.
            # Resume inside the run so residual markers remain visible.
            start = position + 1
            continue
        start = position + len(delimiter)
        if any(
            code_start <= position < code_end + length
            for code_start, code_end, length in code_spans
        ):
            continue
        can_open = _is_emphasis_opener(fragment, position, delimiter)
        can_close = _is_emphasis_closer(fragment, position, delimiter)
        if can_close and openers:
            pairs.append((openers.pop(), position))
        elif can_open:
            openers.append(position)
    return pairs


def _paired_backtick_delimiters(fragment: str) -> list[tuple[int, int, int]]:
    """Pair active equal-length backtick runs without inventing a delimiter."""
    runs = [(match.start(), len(match.group())) for match in re.finditer(r"`+", fragment)]
    pairs = []
    index = 0
    while index + 1 < len(runs):
        start, length = runs[index]
        # Escaped backticks are literal only while outside a code span.  Once an
        # unescaped opening delimiter is active, Markdown keeps backslashes
        # literal, so an apparently escaped equal-length run still closes it.
        if _is_escaped(fragment, start):
            start += 1
            length -= 1
            if not length:
                index += 1
                continue
        closing = next(
            (
                candidate
                for candidate in range(index + 1, len(runs))
                if runs[candidate][1] == length
            ),
            None,
        )
        if closing is None:
            index += 1
            continue
        end, _ = runs[closing]
        pairs.append((start, end, length))
        index = closing + 1
    return pairs


def _is_escaped(fragment: str, position: int) -> bool:
    """Whether the character at ``position`` has an odd slash prefix."""
    slashes = 0
    position -= 1
    while position >= 0 and fragment[position] == "\\":
        slashes += 1
        position -= 1
    return bool(slashes % 2)


def _masked_inline_spans(fragment: str, delimiter: str) -> str:
    masked = list(fragment)
    for start, end in _paired_delimiters(fragment, delimiter):
        masked[start : end + len(delimiter)] = "x" * (end + len(delimiter) - start)
    return "".join(masked)


def _masked_backtick_spans(fragment: str, *, preserve_newlines: bool = False) -> str:
    masked = list(fragment)
    for start, end, length in _paired_backtick_delimiters(fragment):
        masked[start : end + length] = [
            "\n" if preserve_newlines and value == "\n" else "x"
            for value in fragment[start : end + length]
        ]
    return "".join(masked)


def _is_emphasis_opener(fragment: str, position: int, marker: str) -> bool:
    before = fragment[position - 1] if position else " "
    after_position = position + len(marker)
    after = fragment[after_position] if after_position < len(fragment) else " "
    before_punctuation = _is_markdown_punctuation(before)
    after_punctuation = _is_markdown_punctuation(after)
    left_flanking = not after.isspace() and (
        not after_punctuation or before.isspace() or before_punctuation
    )
    right_flanking = not before.isspace() and (
        not before_punctuation or after.isspace() or after_punctuation
    )
    return left_flanking and (
        marker[0] != "_" or not right_flanking or not before.isalnum()
    )


def _is_emphasis_closer(fragment: str, position: int, marker: str) -> bool:
    before = fragment[position - 1] if position else " "
    after_position = position + len(marker)
    after = fragment[after_position] if after_position < len(fragment) else " "
    before_punctuation = _is_markdown_punctuation(before)
    after_punctuation = _is_markdown_punctuation(after)
    left_flanking = not after.isspace() and (
        not after_punctuation or before.isspace() or before_punctuation
    )
    right_flanking = not before.isspace() and (
        not before_punctuation or after.isspace() or after_punctuation
    )
    return right_flanking and (
        marker[0] != "_" or not left_flanking or not after.isalnum()
    )


def _is_markdown_punctuation(value: str) -> bool:
    return (
        value.isascii() and not value.isalnum() and not value.isspace()
    ) or unicodedata.category(value).startswith(("P", "S"))


def _reject_multiline_emphasis(
    fragment: str, marker: str, *, allow_observed_bold: bool = False
) -> None:
    """Refuse multiline Markdown spans using their actual open/close roles.

    A delimiter can be ordinary text (``2 * 3``), an opener, or a closer.  Pairing
    every other character loses that distinction and can hide a later real span.
    Delimiter runs are consumed one or two characters at a time, as emphasis and
    strong emphasis can share a run.  Keeping a run whole would miss the outer
    emphasis in ``***a**\n b*`` after the inner ``**`` pair consumes only part of
    the opening run.  An escape consumes only the first marker of a run.  The sole
    allowed multiline formatting span is the recorded simple bold form, checked
    against its exact source grammar below.
    """
    runs = []
    for match in re.finditer(re.escape(marker) + "+", fragment):
        position = match.start()
        run = match.group()
        # A backslash escapes one punctuation character, not the complete run.
        # Keep any residual markers available for real emphasis delimiters.
        if _is_escaped(fragment, position):
            position += 1
            run = run[1:]
        if run:
            runs.append((position, run))
    # Each opener keeps its unconsumed delimiter count. Opening delimiters are
    # consumed from the right and closing delimiters from the left, so a residual
    # marker can still form an outer span with a later run. The rule of three
    # uses original run lengths, never the counts left after partial consumption.
    openers: list[tuple[int, int, bool, int]] = []
    for position, run in runs:
        if marker == "~" and len(run) < 2:
            continue
        can_open = _is_emphasis_opener(fragment, position, run)
        can_close = _is_emphasis_closer(fragment, position, run)
        closing_remaining = len(run)
        closing_consumed = 0
        while can_close and closing_remaining and openers:
            opener_index = None
            for candidate in range(len(openers) - 1, -1, -1):
                (
                    _opener, opener_remaining, opener_can_close, opener_length
                ) = openers[candidate]
                # CommonMark's multiple-of-three restriction prevents a run
                # that can serve both roles from being paired ambiguously.
                if (
                    marker != "~"
                    and (opener_can_close or can_open)
                    and (opener_length + len(run)) % 3 == 0
                    and (
                        opener_length % 3 != 0
                        or len(run) % 3 != 0
                    )
                ):
                    continue
                opener_index = candidate
                break
            if opener_index is None:
                break
            (
                opener, opener_remaining, opener_can_close, opener_length
            ) = openers[opener_index]
            use = 2 if opener_remaining >= 2 and closing_remaining >= 2 else 1
            if marker == "~" and use != 2:
                break
            opener_start = opener + opener_remaining - use
            closer_end = position + closing_consumed + use
            span = fragment[opener_start:closer_end]
            bold_match = _MULTILINE_BOLD.match(fragment, opener_start)
            allowed_multiline_bold = (
                allow_observed_bold
                and marker == "*"
                and use == 2
                and bold_match is not None
                and bold_match.end() == closer_end
            )
            if "\n" in span and not allowed_multiline_bold:
                raise ValueError("unsupported multiline inline Markdown in ADR body")
            opener_remaining -= use
            closing_remaining -= use
            closing_consumed += use
            if opener_remaining:
                openers[opener_index] = (
                    opener,
                    opener_remaining,
                    opener_can_close,
                    opener_length,
                )
            else:
                openers.pop(opener_index)
        if can_open and closing_remaining >= (2 if marker == "~" else 1):
            openers.append(
                (position + closing_consumed, closing_remaining, can_close, len(run))
            )


def _reject_unqualified_inline_breaks_and_html(fragment: str) -> None:
    """Refuse hardbreaks and raw HTML without interpreting an HTML surface."""
    for match in re.finditer("\n", fragment):
        position = match.start()
        if fragment[max(0, position - 2) : position] == "  " or _is_escaped(
            fragment, position
        ):
            raise ValueError("unsupported multiline inline Markdown in ADR body")
    if "\n" in fragment and any(
        not _is_escaped(fragment, match.start())
        for match in _RAW_INLINE_HTML.finditer(fragment)
    ):
        raise ValueError("unsupported multiline inline Markdown in ADR body")


def _reject_multiline_link_labels(fragment: str) -> None:
    """Refuse each matched label, including nested and escaped brackets."""
    openers: list[int] = []
    for position, value in enumerate(fragment):
        if value not in "[]" or _is_escaped(fragment, position):
            continue
        if value == "[":
            openers.append(position)
        elif openers:
            opener = openers.pop()
            if "\n" in fragment[opener : position + 1]:
                raise ValueError("unsupported multiline inline Markdown in ADR body")


def _linear_strong_trailing_inline_code_readback(fragment: str) -> str:
    """Render the one observed strong/code form or refuse its variants.

    A code span inside a paired strong range must match the whole closed profile;
    a future renderer variation therefore fails preflight rather than being
    silently accepted.
    """
    replacements = []
    strong_ranges = (
        _paired_delimiters(fragment, "**") + _paired_delimiters(fragment, "__")
    )
    for strong_start, strong_end in strong_ranges:
        marker = fragment[strong_start : strong_start + 2]
        if not (
            _is_emphasis_opener(fragment, strong_start, marker)
            and _is_emphasis_closer(fragment, strong_end, marker)
        ):
            continue
        strong_end += 2
        code_spans = [
            (start, end, length)
            for start, end, length in _paired_backtick_delimiters(fragment)
            if strong_start < start and end + length < strong_end
        ]
        if not code_spans:
            continue
        match = _STRONG_TRAILING_INLINE_CODE.match(fragment, strong_start)
        if (
            len(code_spans) != 1
            or match is None
            or match.end() != strong_end
            or code_spans[0][2] != 1
            or match["code"] != fragment[code_spans[0][0] : code_spans[0][1] + 1]
        ):
            raise ValueError("unsupported strong inline-code Markdown in ADR body")
        replacements.append(
            (strong_start, strong_end, f"**{match['before']}** {match['code']}")
        )
    # Qualify the surrounding line too: a matching-looking span in HTML or
    # another Markdown construct is not the observed simple strong/code form.
    # Mask literal code and every qualified span before inspecting its context.
    context = list(_masked_backtick_spans(fragment, preserve_newlines=True))
    for start, end, _ in replacements:
        context[start:end] = "x" * (end - start)
    offset = 0
    lines = fragment.splitlines(keepends=True)
    for index, line in enumerate(lines):
        end = offset + len(line)
        has_candidate = any(offset <= start < end for start, _, _ in replacements)
        indentation = line[: len(line) - len(line.lstrip(" \t"))]
        if has_candidate and len(indentation.expandtabs(4)) >= 4:
            raise ValueError("unsupported strong inline-code Markdown in ADR body")
        list_prefix = re.match(r"^ {0,3}(?:[*+-]|[0-9]{1,9}[.)])([ \t]+)", line)
        if has_candidate and list_prefix is not None and list_prefix[1] != " ":
            raise ValueError("unsupported strong inline-code Markdown in ADR body")
        if line.startswith("* "):
            # The already-qualified list conversion runs before this pass.
            context[offset] = "x"
        if has_candidate and any(
            character in "*_~[]<>&\\#|" for character in context[offset:end]
        ):
            raise ValueError("unsupported strong inline-code Markdown in ADR body")
        if has_candidate:
            # A setext underline can turn the whole preceding paragraph into
            # a heading, including candidate lines before its last line. A
            # single-column table delimiter is unqualified for the same reason.
            for following in lines[index + 1 :]:
                if not following.strip():
                    break
                if re.fullmatch(
                    r" {0,3}(?:=+|-+|:?-{3,}:?)[ \t]*", following.rstrip("\r\n")
                ):
                    raise ValueError(
                        "unsupported strong inline-code Markdown in ADR body"
                    )
        offset = end
    rendered = fragment
    for start, end, replacement in sorted(replacements, reverse=True):
        rendered = f"{rendered[:start]}{replacement}{rendered[end:]}"
    return rendered


def _linear_nonfenced_markdown_readback(fragment: str) -> str:
    """Render the two qualified multiline inline forms and reject every other one.

    This is deliberately a source-to-readback model.  It has no inverse: the
    witness remains the sole source-byte recovery path.
    """
    code_replacements = []
    source_code_spans = _paired_backtick_delimiters(fragment)
    bold_ranges = _paired_delimiters(fragment, "**")
    source_masked = _masked_backtick_spans(fragment, preserve_newlines=True)
    _reject_unqualified_inline_breaks_and_html(source_masked)
    _reject_multiline_emphasis(source_masked, "*", allow_observed_bold=True)
    _reject_multiline_emphasis(source_masked, "_")
    _reject_multiline_emphasis(source_masked, "~")
    if _MULTILINE_LINK_DESTINATION.search(source_masked) is not None:
        raise ValueError("unsupported multiline inline Markdown in ADR body")
    _reject_multiline_link_labels(source_masked)
    for start, end, length in source_code_spans:
        span = fragment[start : end + length]
        if "\n" not in span:
            continue
        if any(bstart <= start < bend for bstart, bend in bold_ranges):
            raise ValueError("unsupported multiline inline Markdown in ADR body")
        match = _MULTILINE_INLINE_CODE.match(fragment, start)
        if length != 1 or match is None or match.end() != end + length:
            raise ValueError("unsupported multiline inline Markdown in ADR body")
        code_replacements.append(
            (start, end + length, f"`{match['before']} {match['after']}`")
        )
    rendered = fragment
    for start, end, replacement in reversed(code_replacements):
        rendered = f"{rendered[:start]}{replacement}{rendered[end:]}"
    rendered = _linear_strong_trailing_inline_code_readback(rendered)

    # Regex matches are only candidates: literal delimiters cannot be rendered
    # as strong spans, and the same source flanking rules qualify replacements.
    def render_bold(match: re.Match[str]) -> str:
        if not (
            _is_emphasis_opener(rendered, match.start(), "**")
            and _is_emphasis_closer(rendered, match.end() - 2, "**")
        ):
            return match.group()
        return f"**{match['before']}**\n{match['indent']}**{match['after']}**"

    rendered = _MULTILINE_BOLD.sub(render_bold, rendered)
    if any(
        "\n" in rendered[start : end + length]
        for start, end, length in _paired_backtick_delimiters(rendered)
    ):
        raise ValueError("unsupported multiline inline Markdown in ADR body")
    masked = _masked_inline_spans(_masked_backtick_spans(rendered), "**")
    _reject_multiline_emphasis(masked, "*")
    _reject_multiline_emphasis(masked, "_")
    _reject_multiline_emphasis(masked, "~")
    if _MULTILINE_LINK_DESTINATION.search(masked) is not None:
        raise ValueError("unsupported multiline inline Markdown in ADR body")
    _reject_multiline_link_labels(masked)
    return rendered


# PAT-94: Linear re-serializes a loose list.  One form is observed; see
# `_linear_observed_list_blank_lines`.  A list-item-like line has any marker and
# any indentation; a thematic break (`* * *`, `- - -`) is not one.
_LIST_LIKE = re.compile(r"[ \t]*(?:[*+-]|[0-9]+[.)])(?:[ \t]|$)")
_THEMATIC_BREAK = re.compile(r" {0,3}([*_-])[ \t]*(?:\1[ \t]*){2,}")
_BLOCKQUOTE_PREFIX = re.compile(r"(?: {0,3}> ?)+")
_OBSERVED_ORDERED_ITEM = re.compile(r"([1-9])\. ([^ \t\n][^\n]*)\n?")
# Item text that would itself open block syntax (a nested list, heading, quote,
# fence, HTML, table, thematic break or link reference definition) is not plain.
_NOT_PLAIN_ITEM_TEXT = re.compile(
    r"[*+-](?:[ \t]|$)|[0-9]+[.)](?:[ \t]|$)|#{1,6}(?:[ \t]|$)|[>|<]|`{3}|~{3}"
    r"|([*_-])[ \t]*(?:\1[ \t]*){2,}$|\[[^\]]*\]:"
)
_NON_LF_SEPARATORS = frozenset("\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")

# CommonMark line endings only: `str.splitlines` also breaks on form feed, U+2028
# and others, which would invent blank lines Linear's parser does not see.
_COMMONMARK_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")


def _linear_observed_list_blank_lines(body: str, *, strict: bool) -> set[int]:
    """Return the offsets of the empty lines Linear drops from the observed form.

    Observed (PAT-94, native PAT-ADR-0014): a top-level ordered list `1. ` to at
    most `9. `, numbered from 1 by +1, one plain LF line per item, items separated
    by exactly one empty LF line, preceded by the start of the body or empty LF
    lines and followed by the end of the body or empty LF lines and a non-list
    block, is read back with the separating empty lines removed.  ``strict``
    accepts it only with at most one empty line on each side (PAT-72: Linear
    collapses two empty lines after a list item).

    Whitelist: a blank line in a list context -- after a block holding a
    list-item-like line (also inside a blockquote) or its indented continuation,
    before a block starting with a list-item-like or indented line -- has an
    unobserved rendering unless it lies inside that exact form.  So has a gap of
    two or more empty lines before or after a list-context paragraph.  ``strict``
    refuses either, naming the first offending 1-based CommonMark line; otherwise
    they are left untouched, as before PAT-94.  A body with neither is never
    refused here.  Lines and blank lines are CommonMark ones (only spaces or
    tabs); fenced code is one opaque block.  Forward only: a source is never
    recovered from a rendering.
    """
    lines = _COMMONMARK_LINE.findall(body)
    offsets = [0]
    for source_line in lines:
        offsets.append(offsets[-1] + len(source_line))
    blocks: list[tuple[int, bool]] = []  # (first line, fenced)
    fence = None
    for number, source_line in enumerate(lines):
        line = source_line.rstrip("\r\n")
        if fence is not None:
            if _markdown_fence_closing(line, *fence):
                fence = None
            continue
        fence = _markdown_fence_opening(line)
        blocks.append((number, fence is not None))

    def content(block: int) -> str:
        line = lines[blocks[block][0]].rstrip("\r\n")
        quote = _BLOCKQUOTE_PREFIX.match(line)
        return line[quote.end() :] if quote else line

    def is_item(block: int) -> bool:
        text = content(block)
        return (
            not blocks[block][1]
            and _LIST_LIKE.match(text) is not None
            and _THEMATIC_BREAK.fullmatch(text) is None
        )

    def is_lf_gap(gap: list[int]) -> bool:
        return all(lines[blocks[block][0]] == "\n" for block in gap)

    leading: list[int] = []
    paragraphs: list[list[int]] = []
    gaps: list[list[int]] = []  # gaps[k]: blank blocks after paragraphs[k]
    for block, (_, fenced) in enumerate(blocks):
        if not fenced and not content(block).strip(" \t"):
            (gaps[-1] if paragraphs else leading).append(block)
        elif paragraphs and not gaps[-1]:
            paragraphs[-1].append(block)
        else:
            paragraphs.append([block])
            gaps.append([])

    risky = []  # risky[k]: blank lines between paragraphs k and k + 1 are in a list
    listed = []  # listed[k]: paragraph k is in a list context
    in_list = False
    for index, paragraph in enumerate(paragraphs):
        indented = content(paragraph[0])[:1] in (" ", "\t")
        if index:
            risky.append(in_list and (is_item(paragraph[0]) or indented))
        in_list = any(is_item(block) for block in paragraph) or (indented and in_list)
        listed.append(in_list)

    def observed_item(index: int, number: int) -> bool:
        if len(paragraphs[index]) != 1:
            return False
        line_number, fenced = blocks[paragraphs[index][0]]
        line = lines[line_number]
        match = _OBSERVED_ORDERED_ITEM.fullmatch(line)
        return (
            not fenced
            and match is not None
            and match[1] == str(number)
            and _NOT_PLAIN_ITEM_TEXT.match(match[2]) is None
            and match[2][-1] not in " \t"
            and _NON_LF_SEPARATORS.isdisjoint(line)
        )

    drops: set[int] = set()
    covered: set[int] = set()
    start = 0
    while start < len(paragraphs):
        last = start
        while (
            observed_item(start, 1)
            and last + 1 < len(paragraphs)
            and len(gaps[last]) == 1
            and is_lf_gap(gaps[last])
            and observed_item(last + 1, last - start + 2)
        ):
            last += 1
        if (
            last > start
            and (start == 0 or not risky[start - 1])
            and (last + 1 == len(paragraphs) or not risky[last])
            and is_lf_gap((leading if start == 0 else gaps[start - 1]) + gaps[last])
        ):
            covered.update(range(start, last))
            drops.update(
                offsets[blocks[gaps[index][0]][0]] for index in range(start, last)
            )
        start = last + 1
    if strict:
        # Uncovered list blank lines, and every gap of two or more empty lines
        # next to a list-context paragraph: PAT-72 shows Linear collapsing such a
        # gap after a list item, so even the observed form is accepted only with
        # at most one empty line on each side.
        offending = [
            gaps[index][0] for index, risk in enumerate(risky)
            if risk and index not in covered
        ]
        offending.extend(
            gap[0]
            for gap, beside in [
                (leading, listed[:1]),
                *((gap, listed[index : index + 2]) for index, gap in enumerate(gaps)),
            ]
            if len(gap) > 1 and any(beside)
        )
        if offending:
            line = blocks[min(offending)][0] + 1
            raise ValueError(f"unsupported list Markdown in ADR body (line {line})")
    return drops


def _linear_markdown_readback_body(
    body: str,
    *,
    allow_foundry_adr_0001: bool = False,
    strict: bool = False,
    observed_lists: bool = True,
) -> str:
    """Model only closed, observed Linear Markdown serializations.

    ``observed_lists=False`` is the pre-PAT-94 model: a Document stored before
    PAT-94 may hold that rendering.  ``strict`` refuses every list blank line
    outside the one observed form (`_linear_observed_list_blank_lines`).
    """
    if allow_foundry_adr_0001:
        qualified = _linear_foundry_adr_0001_v1_readback(body)
        if qualified is not None:
            return qualified
    qualified = _linear_foundry_adr_0012_v1_readback(body)
    if qualified is not None:
        return qualified
    drops = (
        _linear_observed_list_blank_lines(body, strict=strict)
        if observed_lists or strict
        else set()
    )
    rendered = []
    nonfenced = []

    def flush_nonfenced() -> None:
        if nonfenced:
            rendered.append(_linear_nonfenced_markdown_readback("".join(nonfenced)))
            nonfenced.clear()

    fence = None
    offset = 0
    for source_line in body.splitlines(keepends=True):
        offset += len(source_line)
        if offset - len(source_line) in drops:
            continue
        line = source_line.removesuffix("\n").removesuffix("\r")
        if fence is not None:
            flush_nonfenced()
            rendered.append(source_line)
            if _markdown_fence_closing(line, *fence):
                fence = None
            continue
        opening = _markdown_fence_opening(line)
        if opening is not None:
            flush_nonfenced()
            fence = opening
            rendered.append(source_line)
            continue
        candidate = line.lstrip(" ")
        if len(line) - len(candidate) <= 3 and candidate.startswith("<"):
            raise ValueError("ambiguous raw HTML block in ADR body")
        if source_line.startswith("- "):
            thematic = line.replace(" ", "").replace("\t", "")
            if len(thematic) < 3 or set(thematic) != {"-"}:
                source_line = f"* {source_line[2:]}"
        nonfenced.append(source_line)
    flush_nonfenced()
    rendered_body = "".join(rendered)
    qualified = _linear_pat_72_readback(body, rendered_body)
    if qualified is not None:
        return qualified
    qualified = _linear_pat_86_readback(body, rendered_body)
    if qualified is not None:
        return qualified
    qualified = _linear_pat_16_adr13_readback(body, rendered_body)
    return rendered_body if qualified is None else qualified


def _linear_markdown_readback_bodies(
    body: str, *, allow_foundry_adr_0001: bool = False
) -> tuple[str, ...]:
    """Every body rendering a read accepts: PAT-94 first, then pre-PAT-94.

    Additive: a Document stored with the pre-PAT-94 model output stays readable,
    and the pre-PAT-94 refusals still raise.  Never strict.
    """
    legacy = _linear_markdown_readback_body(
        body, allow_foundry_adr_0001=allow_foundry_adr_0001, observed_lists=False
    )
    try:
        observed = _linear_markdown_readback_body(
            body, allow_foundry_adr_0001=allow_foundry_adr_0001
        )
    except ValueError:
        return (legacy,)
    return (observed,) if observed == legacy else (observed, legacy)


def _preflight_adr_body_readback(
    body: str, *, allow_foundry_adr_0001: bool = False, new_body: bool = True
) -> None:
    """Refuse a body with an unmodelled Linear rendering before any write.

    Every body keeps the pre-PAT-94 refusals.  A ``new_body`` (the fail-closed
    default) also gets the strict PAT-94 list whitelist.  Callers pass
    ``new_body=False`` only when the body's Linear rendering is already proven:
    it is the exact body of the stored previous version whose bytes the model
    verifies (`_is_stored_adr_body`), of an existing exact slot being recovered,
    or of a batch import whose provider bytes a qualification probe pins.
    """
    try:
        _linear_markdown_readback_body(
            body, allow_foundry_adr_0001=allow_foundry_adr_0001, observed_lists=False
        )
        if new_body:
            _linear_markdown_readback_body(
                body, allow_foundry_adr_0001=allow_foundry_adr_0001, strict=True
            )
    except ValueError as exc:
        raise TrackerConflictError(
            "Linear ADR body has unsupported Markdown serialization"
        ) from exc


def _is_stored_adr_body(previous: tuple[dict, dict], body: str) -> bool:
    """Whether ``body`` is the stored ``previous`` body with a model-proven rendering.

    The SHA-256 alone is not enough: the stored bytes of a probe-qualified
    historical version 0 may be proven by the probe only, while the next version
    is verified by the closed model.  Only stored bytes the model itself accepts
    for this body prove its rendering; anything else is a new body (fail closed).
    """
    metadata, raw = previous
    return (
        metadata["body_sha256"] == hashlib.sha256(body.encode()).hexdigest()
        and isinstance(raw, dict)
        and _adr_readback_content_matches(
            raw.get("content"), _adr_document_content(metadata, body)
        )
    )


def _linear_adr_readback_contents(content: str) -> tuple[str, ...]:
    """Return the provider serializations accepted beside canonical bytes.

    Linear escapes the closing delimiter of an HTML comment in Markdown readback.
    Keep compatibility pinned to the observed deterministic header transformation:
    JSON brackets, the marker delimiter, and the closed body model outside fenced
    code only (`_linear_markdown_readback_bodies`).
    """
    if content.startswith(_ADR_HEADER):
        payload = content[len(_ADR_HEADER) :]
        encoded, separator, body = payload.partition("\n-->\n\n")
        if not separator:
            raise ValueError("canonical ADR document delimiter is missing")
        try:
            metadata = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise ValueError("canonical ADR metadata is invalid") from exc
        encoded = encoded.replace("[", "\\[").replace("]", "\\]")
        return tuple(
            f"{_ADR_HEADER}{encoded}\n\\-->\n\n{rendered}"
            for rendered in _linear_markdown_readback_bodies(
                body,
                allow_foundry_adr_0001=_is_foundry_adr_0001_profile_chain(metadata),
            )
        )
    if content.startswith(_ADR_WITNESS_HEADER) and content.endswith("\n-->"):
        suffix = "\n-->"
        return (f"{content[:-len(suffix)]}\n\\-->",)
    raise ValueError("canonical ADR content is invalid")


def _linear_adr_readback_content(content: str) -> str:
    """Return the predicted provider serialization (PAT-94 rendering first)."""
    return _linear_adr_readback_contents(content)[0]


def _adr_qualification_probe_title(adr_id: str, kind: str, content: str) -> str:
    """Bind a qualification probe to the exact canonical bytes it was created from."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"{_ADR_QUALIFICATION_PROBE_PREFIX}{adr_id} v0000 {kind} {digest}"


def _adr_version_readback_header_matches(observed: object, canonical: str) -> bool:
    """Keep the one qualified header encoding; the probe pins the complete bytes."""
    canonical_header, separator, _body = canonical.partition("\n-->\n\n")
    if (
        not isinstance(observed, str)
        or not separator
        or not canonical_header.startswith(_ADR_HEADER)
    ):
        return False
    serialized = (
        canonical_header[len(_ADR_HEADER) :].replace("[", "\\[").replace("]", "\\]")
    )
    return observed.startswith(f"{canonical_header}\n-->\n\n") or observed.startswith(
        f"{_ADR_HEADER}{serialized}\n\\-->\n\n"
    )


def _adr_readback_content_matches(observed: object, canonical: str) -> bool:
    if observed == canonical:
        return True
    try:
        return (
            observed in _linear_adr_readback_contents(canonical)
            or _is_qualified_historical_readback(observed, canonical)
        )
    except ValueError:
        return _is_qualified_historical_readback(observed, canonical)


def _exact_adr_document_matches(raw: object, expected: dict) -> bool:
    if not isinstance(raw, dict):
        return False
    return all(
        _adr_readback_content_matches(raw.get(key), value)
        if key == "content"
        else raw.get(key) == value
        for key, value in expected.items()
    )


def _adr_witness_document(
    binding: dict, metadata: dict, raw: dict, source_body: str | None = None
) -> dict:
    witness = {
        "schema": _ADR_WITNESS_SCHEMA,
        "project_id": binding["project_id"],
        "team_id": binding["team_id"],
        "adr_id": metadata["id"],
        "sequence": metadata["sequence"],
        "document_id": raw["id"],
        "document_sha256": hashlib.sha256(raw["content"].encode()).hexdigest(),
    }
    if source_body is not None:
        source_bytes = source_body.encode("utf-8")
        witness.update(
            source_encoding=_ADR_SOURCE_ENCODING,
            source_body=base64.b64encode(source_bytes).decode("ascii"),
            source_body_sha256=hashlib.sha256(source_bytes).hexdigest(),
        )
    encoded = json.dumps(
        witness, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return {
        "id": _adr_witness_id(
            binding["project_id"], metadata["id"], metadata["sequence"]
        ),
        "title": (
            f"{_ADR_WITNESS_PREFIX}{metadata['id']} / "
            f"v{metadata['sequence']:04d}"
        ),
        "content": f"{_ADR_WITNESS_HEADER}{encoded}\n-->",
        "project": {"id": binding["project_id"]},
        "archivedAt": None,
    }


def _parse_adr_witness(raw: dict, binding: dict) -> dict:
    if not isinstance(raw, dict) or raw.get("archivedAt") is not None:
        raise LinearTrackerError("adr.witness.normalize", None, "invalid_response")
    content = raw.get("content")
    if not isinstance(content, str) or not content.startswith(_ADR_WITNESS_HEADER):
        raise LinearTrackerError("adr.witness.normalize", None, "invalid_response")
    payload = content[len(_ADR_WITNESS_HEADER) :]
    encoded = None
    for delimiter in ("\n-->", "\n\\-->"):
        candidate, separator, remainder = payload.partition(delimiter)
        if separator and not remainder:
            encoded = candidate
            break
    if encoded is None:
        raise LinearTrackerError("adr.witness.normalize", None, "invalid_response")
    try:
        witness = json.loads(encoded)
    except (TypeError, ValueError):
        raise LinearTrackerError(
            "adr.witness.normalize", None, "invalid_response"
        ) from None
    legacy_required = {
        "schema",
        "project_id",
        "team_id",
        "adr_id",
        "sequence",
        "document_id",
        "document_sha256",
    }
    source_required = legacy_required | {
        "source_encoding",
        "source_body",
        "source_body_sha256",
    }
    adr_id = witness.get("adr_id") if isinstance(witness, dict) else None
    sequence = witness.get("sequence") if isinstance(witness, dict) else None
    if (
        not isinstance(witness, dict)
        or frozenset(witness)
        not in {frozenset(legacy_required), frozenset(source_required)}
        or witness["schema"] != _ADR_WITNESS_SCHEMA
        or witness["project_id"] != binding["project_id"]
        or witness["team_id"] != binding["team_id"]
        or not isinstance(adr_id, str)
        or _ADR_ID.fullmatch(adr_id) is None
        or type(sequence) is not int
        or sequence < 0
        or witness["document_id"]
        != _adr_document_id(binding["project_id"], adr_id, sequence)
        or not isinstance(witness["document_sha256"], str)
        or _DIGEST.fullmatch(witness["document_sha256"]) is None
        or raw.get("id")
        != _adr_witness_id(binding["project_id"], adr_id, sequence)
        or raw.get("title")
        != f"{_ADR_WITNESS_PREFIX}{adr_id} / v{sequence:04d}"
        or raw.get("project", {}).get("id") != binding["project_id"]
        or not _adr_readback_content_matches(
            content,
            (
                f"{_ADR_WITNESS_HEADER}"
                f"{json.dumps(witness, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
                "\n-->"
            ),
        )
    ):
        raise LinearTrackerError("adr.witness.normalize", None, "invalid_response")
    if set(witness) == source_required:
        try:
            source_bytes = base64.b64decode(
                witness["source_body"], validate=True
            )
            source_body = source_bytes.decode("utf-8")
        except (TypeError, ValueError, UnicodeDecodeError, binascii.Error):
            raise LinearTrackerError(
                "adr.witness.normalize", None, "invalid_response"
            ) from None
        if (
            witness["source_encoding"] != _ADR_SOURCE_ENCODING
            or base64.b64encode(source_bytes).decode("ascii")
            != witness["source_body"]
            or not isinstance(witness["source_body_sha256"], str)
            or _DIGEST.fullmatch(witness["source_body_sha256"]) is None
            or witness["source_body_sha256"]
            != hashlib.sha256(source_bytes).hexdigest()
        ):
            raise LinearTrackerError(
                "adr.witness.normalize", None, "invalid_response"
            )
        witness[_ADR_BOUND_SOURCE_BODY] = source_body
    return witness


def _parse_adr_document(
    raw: dict,
    binding: dict,
    *,
    source_body: str | None = None,
    allow_unbound_body: bool = False,
    allow_witness_bound_readback: bool = False,
) -> tuple[dict, str]:
    if not isinstance(raw, dict) or raw.get("archivedAt") is not None:
        raise LinearTrackerError("adr.normalize", None, "invalid_response")
    content = raw.get("content")
    if not isinstance(content, str) or not content.startswith(_ADR_HEADER):
        raise LinearTrackerError("adr.normalize", None, "invalid_response")
    payload = content[len(_ADR_HEADER) :]
    encoded = None
    body = None
    for delimiter, linear_serialized in (
        ("\n-->\n\n", False),
        ("\n\\-->\n\n", True),
    ):
        candidate, separator, candidate_body = payload.partition(delimiter)
        if not separator:
            continue
        if linear_serialized:
            candidate = candidate.replace("\\[", "[").replace("\\]", "]")
        try:
            json.loads(candidate)
        except (TypeError, ValueError):
            continue
        encoded, body = candidate, candidate_body
        break
    if encoded is None or body is None:
        raise LinearTrackerError("adr.normalize", None, "invalid_response")
    try:
        metadata = json.loads(encoded)
    except (TypeError, ValueError):
        raise LinearTrackerError("adr.normalize", None, "invalid_response") from None
    required = {
        "schema",
        "project_id",
        "team_id",
        "id",
        "title",
        "status",
        "sequence",
        "previous_id",
        "previous_sha256",
        "body_sha256",
        "origin",
        "relations",
    }
    if not isinstance(metadata, dict) or set(metadata) != required:
        raise LinearTrackerError("adr.normalize", None, "invalid_response")
    adr_id, sequence, origin, relations = (
        metadata["id"],
        metadata["sequence"],
        metadata["origin"],
        metadata["relations"],
    )
    canonical_body = body if source_body is None else source_body
    if (
        metadata["schema"] != _ADR_SCHEMA
        or metadata["project_id"] != binding["project_id"]
        or metadata["team_id"] != binding["team_id"]
        or not isinstance(adr_id, str)
        or _ADR_ID.fullmatch(adr_id) is None
        or not isinstance(metadata["title"], str)
        or not metadata["title"].strip()
        or metadata["status"] not in _ADR_STATUSES
        or type(sequence) is not int
        or sequence < 0
        or (
            sequence == 0
            and (
                metadata["previous_id"] is not None
                or metadata["previous_sha256"] is not None
            )
        )
        or (
            sequence > 0
            and (
                not isinstance(metadata["previous_id"], str)
                or not isinstance(metadata["previous_sha256"], str)
                or _DIGEST.fullmatch(metadata["previous_sha256"]) is None
            )
        )
        or not isinstance(metadata["body_sha256"], str)
        or _DIGEST.fullmatch(metadata["body_sha256"]) is None
        or (
            not allow_unbound_body
            and metadata["body_sha256"]
            != hashlib.sha256(canonical_body.encode()).hexdigest()
        )
        or not isinstance(relations, dict)
        or set(relations) != {"supersedes", "superseded_by", "issues"}
        or not isinstance(relations["supersedes"], list)
        or not isinstance(relations["issues"], list)
        or len(relations["supersedes"]) > _ADR_RELATION_LIMIT
        or len(relations["issues"]) > _ADR_RELATION_LIMIT
        or len(relations["supersedes"]) != len(set(relations["supersedes"]))
        or len(relations["issues"]) != len(set(relations["issues"]))
        or any(
            not isinstance(v, str) or _ADR_ID.fullmatch(v) is None
            for v in relations["supersedes"]
        )
        or any(
            not isinstance(v, str) or _SAFE_ID.fullmatch(v) is None
            for v in relations["issues"]
        )
        or (
            relations["superseded_by"] is not None
            and (
                not isinstance(relations["superseded_by"], str)
                or _ADR_ID.fullmatch(relations["superseded_by"]) is None
            )
        )
        or (
            (metadata["status"] == "superseded")
            != (relations["superseded_by"] is not None)
            and not (
                sequence == 0
                and metadata["status"] == "superseded"
                and relations["superseded_by"] is None
                and isinstance(origin, dict)
                and origin.get("kind") == "migration"
                and isinstance(origin.get("missing_relations"), list)
                and "superseded_by" in origin["missing_relations"]
            )
        )
        or adr_id in relations["supersedes"]
        or relations["superseded_by"] == adr_id
        or not isinstance(origin, dict)
        or origin.get("kind") not in {"native", "migration"}
    ):
        raise LinearTrackerError("adr.normalize", None, "invalid_response")
    if origin["kind"] == "native":
        valid_origin = set(origin) == {"kind"} and (
            sequence != 0 or metadata["status"] == "proposed"
        )
    else:
        migration_keys = {
            "kind",
            "source_tracker",
            "source_ref",
            "source_created",
            "source_updated",
            "source_body_sha256",
            "missing_relations",
        }
        valid_origin = (
            set(origin) in (
                migration_keys, migration_keys | {"batch_sha256"},
                migration_keys | {"source_only_issue_refs"},
                migration_keys | {"batch_sha256", "source_only_issue_refs"},
            )
            and (
                "batch_sha256" not in origin
                or (
                    isinstance(origin["batch_sha256"], str)
                    and _DIGEST.fullmatch(origin["batch_sha256"]) is not None
                )
            )
            and origin["source_tracker"] in {"youtrack", "linear", "ghprojects"}
            and isinstance(origin["source_ref"], str)
            and bool(origin["source_ref"])
            and all(
                v is None or (type(v) is int and v >= 0)
                for v in (origin["source_created"], origin["source_updated"])
            )
            and isinstance(origin["source_body_sha256"], str)
            and _DIGEST.fullmatch(origin["source_body_sha256"]) is not None
            and isinstance(origin["missing_relations"], list)
            and all(isinstance(value, str) for value in origin["missing_relations"])
            and origin["missing_relations"] == sorted(set(origin["missing_relations"]))
            and set(origin["missing_relations"]).issubset(
                _ADR_MISSING_RELATION_FAMILIES
            )
            and (
                "source_only_issue_refs" not in origin
                or (
                    isinstance(origin["source_only_issue_refs"], list)
                    and all(
                        isinstance(ref, str)
                        for ref in origin["source_only_issue_refs"]
                    )
                    and origin["source_only_issue_refs"]
                    == sorted(set(origin["source_only_issue_refs"]))
                    and all(
                        ref.split(":", 2)[0] in {"youtrack", "linear", "ghprojects"}
                        and len(ref.split(":", 2)) == 3
                        and ref.split(":", 2)[1] == "issue"
                        and bool(ref.split(":", 2)[2])
                        for ref in origin["source_only_issue_refs"]
                    )
                )
            )
            and (
                sequence != 0
                or (
                    (
                        "supersedes" not in origin["missing_relations"]
                        or not relations["supersedes"]
                    )
                    and (
                        "superseded_by" not in origin["missing_relations"]
                        or relations["superseded_by"] is None
                    )
                    and (
                        "issues" not in origin["missing_relations"]
                        or not relations["issues"]
                    )
                )
            )
            and (
                sequence != 0 or origin["source_body_sha256"] == metadata["body_sha256"]
            )
        )
    if (
        not valid_origin
        or raw.get("id") != _adr_document_id(binding["project_id"], adr_id, sequence)
        or raw.get("title") != _adr_document_title(metadata)
        or raw.get("project", {}).get("id") != binding["project_id"]
        or (
            not allow_unbound_body
            and not (
                allow_witness_bound_readback
                and _is_probe_qualified_historical_version(metadata)
            )
            and not _adr_readback_content_matches(
                content, _adr_document_content(metadata, canonical_body)
            )
        )
    ):
        raise LinearTrackerError("adr.normalize", None, "invalid_response")
    return metadata, canonical_body


def _is_probe_qualified_historical_version(metadata: dict) -> bool:
    """Only batch-qualified historical version 0 may carry probe bytes.

    Its readable Document was qualified before creation by a probe of its complete
    provider readback, and its witness binds those bytes plus the exact source.
    Native ADRs and every later version keep the closed serialization check.
    """
    return metadata.get("sequence") == 0 and (
        _is_probe_qualified_historical_chain(metadata)
    )


def _is_probe_qualified_historical_chain(metadata: dict) -> bool:
    """Whether metadata belongs to a batch-qualified migration chain."""
    origin = metadata.get("origin")
    batch_sha256 = origin.get("batch_sha256") if isinstance(origin, dict) else None
    return (
        isinstance(origin, dict)
        and origin.get("kind") == "migration"
        and isinstance(batch_sha256, str)
        and _DIGEST.fullmatch(batch_sha256) is not None
    )


def _is_foundry_adr_0001_profile_chain(metadata: dict) -> bool:
    """Whether metadata may use the one private-source forward profile."""
    origin = metadata.get("origin")
    return (
        _is_probe_qualified_historical_chain(metadata)
        and metadata.get("id") == _FOUNDRY_ADR_0001_ID
        and origin.get("source_body_sha256") == _FOUNDRY_ADR_0001_SOURCE_SHA256
    )


def _valid_adr_version_delta(previous: dict, current: dict) -> bool:
    old_relations = previous["relations"]
    new_relations = current["relations"]
    body_changed = current["body_sha256"] != previous["body_sha256"]
    status_changed = current["status"] != previous["status"]

    if new_relations == old_relations:
        if status_changed:
            return not body_changed and current["status"] != "superseded"
        return body_changed

    if body_changed:
        return False
    if new_relations["issues"] != old_relations["issues"]:
        old_issues = old_relations["issues"]
        new_issues = new_relations["issues"]
        return (
            not status_changed
            and current["status"] in {"proposed", "accepted"}
            and new_relations["supersedes"] == old_relations["supersedes"]
            and new_relations["superseded_by"] == old_relations["superseded_by"]
            and len(new_issues) == len(old_issues) + 1
            and set(old_issues).issubset(new_issues)
            and new_issues == sorted(new_issues)
        )
    if previous["status"] == "accepted" and current["status"] == "superseded":
        return (
            old_relations["superseded_by"] is None
            and new_relations["superseded_by"] is not None
            and new_relations["supersedes"] == old_relations["supersedes"]
        )
    if status_changed or current["status"] != "accepted":
        return False
    old_supersedes = old_relations["supersedes"]
    new_supersedes = new_relations["supersedes"]
    return (
        new_relations["superseded_by"] == old_relations["superseded_by"]
        and len(new_supersedes) == len(old_supersedes) + 1
        and set(old_supersedes).issubset(new_supersedes)
        and new_supersedes == sorted(new_supersedes)
    )


def _adr_chain(documents: list[dict], binding: dict) -> dict:
    chains = {}
    witnesses = {}
    version_documents = []
    for raw in documents:
        if isinstance(raw, dict) and (
            str(raw.get("title", "")).startswith(_ADR_WITNESS_PREFIX)
            or str(raw.get("content", "")).startswith(_ADR_WITNESS_HEADER)
        ):
            witness = _parse_adr_witness(raw, binding)
            key = (witness["adr_id"], witness["sequence"])
            if key in witnesses:
                raise TrackerConflictError("Linear ADR witness slot has a fork")
            witnesses[key] = (witness, raw)
            continue
        if not (
            isinstance(raw, dict)
            and (
                str(raw.get("title", "")).startswith(_ADR_DOCUMENT_PREFIX)
                or str(raw.get("content", "")).startswith(_ADR_HEADER)
            )
        ):
            continue
        version_documents.append(raw)

    for raw in version_documents:
        matching_witnesses = [
            witness
            for witness, _witness_raw in witnesses.values()
            if witness["document_id"] == raw.get("id")
        ]
        if len(matching_witnesses) > 1:
            raise TrackerConflictError("Linear ADR witness slot has a fork")
        witness = matching_witnesses[0] if matching_witnesses else None
        source_body = (
            witness.get(_ADR_BOUND_SOURCE_BODY)
            if witness is not None
            else None
        )
        metadata, parsed_body = _parse_adr_document(
            raw,
            binding,
            source_body=source_body,
            allow_unbound_body=witness is None,
            allow_witness_bound_readback=witness is not None,
        )
        if witness is not None and (
            witness["adr_id"] != metadata["id"]
            or witness["sequence"] != metadata["sequence"]
            or (
                source_body is not None
                and witness["source_body_sha256"] != metadata["body_sha256"]
            )
        ):
            raise TrackerConflictError("Linear ADR version witness diverged")
        bound_raw = dict(raw)
        bound_raw[_ADR_BOUND_SOURCE_BODY] = parsed_body
        chains.setdefault(metadata["id"], []).append((metadata, bound_raw))
    observed = set()
    for adr_id, versions in chains.items():
        versions.sort(key=lambda item: item[0]["sequence"])
        for sequence, (metadata, raw) in enumerate(versions):
            key = (adr_id, sequence)
            witness_pair = witnesses.get(key)
            if witness_pair is None:
                raise TrackerConflictError("Linear ADR version witness is missing")
            witness, _witness_raw = witness_pair
            if witness["document_sha256"] != hashlib.sha256(
                raw["content"].encode()
            ).hexdigest():
                raise TrackerConflictError("Linear ADR version witness diverged")
            observed.add(key)
            if metadata["sequence"] != sequence:
                raise TrackerConflictError("Linear ADR version chain has a gap or fork")
            if sequence:
                previous, previous_raw = versions[sequence - 1]
                if (
                    metadata["previous_id"] != previous_raw["id"]
                    or metadata["previous_sha256"]
                    != hashlib.sha256(previous_raw["content"].encode()).hexdigest()
                    or metadata["title"] != previous["title"]
                    or metadata["origin"] != previous["origin"]
                    or previous["status"] in {"deprecated", "superseded"}
                    or (
                        metadata["status"] != previous["status"]
                        and metadata["status"]
                        not in _ADR_TRANSITIONS[previous["status"]]
                    )
                    or (
                        metadata["status"] == "superseded"
                        and (
                            previous["status"] != "accepted"
                            or previous["relations"]["superseded_by"] is not None
                        )
                    )
                    or (
                        metadata["status"] == previous["status"]
                        and metadata["body_sha256"] == previous["body_sha256"]
                        and metadata["relations"] == previous["relations"]
                    )
                    or not _valid_adr_version_delta(previous, metadata)
                ):
                    raise TrackerConflictError("Linear ADR version chain diverged")
    if set(witnesses) != observed:
        raise TrackerConflictError("Linear ADR witness has no matching version")
    return chains


class LinearTracker(Tracker):
    name = "linear"
    adr_issue_link_supported = True
    requires_mutation_binding = True
    acceptance_sync_supported = False
    bounded_transition_proofs = True
    append_only_lifecycle_supported = True
    acceptance_proof_projection_supported = True
    acceptance_override_projection_supported = True
    cockpit_evidence_projection_supported = True
    bounded_epic_closure_supported = True
    epic_override_closure_supported = True
    migration_supported_attributes = frozenset({
        "type", "priority", "estimate", "state", "parent", "children", "dependencies",
    })

    def __init__(
        self,
        *,
        token: str | None = None,
        transport=None,
        endpoint: str = LINEAR_GRAPHQL_ENDPOINT,
        read_retries: int = 3,
    ):
        if endpoint != LINEAR_GRAPHQL_ENDPOINT:
            raise ValueError("Linear endpoint must be the official GraphQL endpoint")
        self.endpoint = endpoint
        self.token = token if token is not None else config.require("LINEAR_API_TOKEN")
        if not isinstance(self.token, str) or not self.token:
            raise ValueError("credential Linear invalid")
        self._transport = transport
        self.read_retries = max(0, min(int(read_retries), len(_READ_RETRY_DELAYS)))
        # Last rate-limit headers seen on any response (never the token): diagnostics only.
        self.rate_limit: dict[str, int | None] = {}
        self._epic_closure_reads = threading.local()
        self._snapshot_scopes = threading.local()
        self._active_project: Project | None = None

    def migration_attribute_exceptions(
        self, project: Project, snapshot: dict,
    ) -> dict[str, str]:
        self._binding(project)
        attributes = snapshot.get("attributes")
        if not isinstance(attributes, dict):
            raise TrackerConflictError("snapshot issue de migration Linear invalide")
        skeleton = {
            "labels": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
        }
        exceptions: dict[str, str] = {}
        for attribute, native in (
            ("type", "Type"), ("priority", "Priority"),
            ("estimate", "Estimate"), ("state", "State"),
        ):
            value = attributes.get(attribute)
            if value is None:
                continue
            try:
                self._desired_update(skeleton, project, {native: value})
            except (LinearBindingError, ValueError) as exc:
                if isinstance(exc, LinearBindingError) and exc.code not in {
                    "state_unmapped", "type_unmapped",
                }:
                    raise
                exceptions[attribute] = f"target value unavailable: {value}"
        return exceptions

    def migration_preflight(
        self, project: Project, records: tuple[dict, ...],
    ) -> dict:
        if project.extra.get("migration_identity_profile") != (
            "foundry-linear-deterministic-v1"
        ):
            raise TrackerCapabilityUnavailableError(
                self.name, "migration_provenance_profile"
            )
        source_refs = tuple(record["source_ref"] for record in records)
        if len(source_refs) != len(set(source_refs)):
            raise TrackerConflictError("provenance source de migration dupliquée")
        identities = [_migration_issue_id(project.id, ref) for ref in source_refs]
        if len(identities) != len(set(identities)):
            raise TrackerConflictError("identité déterministe Linear ambiguë")
        if not self.verify_project_identity(project):
            raise TrackerConflictError("profil migration Linear hors projet")
        for record in records:
            if record.get("kind") != "issue":
                continue
            unsupported = {
                item["attribute"] for item in record.get("exceptions", [])
            }
            fields = {
                name: value
                for name, value in {
                    "Type": record["attributes"].get("type"),
                    "Priority": record["attributes"].get("priority"),
                    "Estimate": record["attributes"].get("estimate"),
                    "State": record["attributes"].get("state"),
                }.items()
                if value is not None and name.casefold() not in unsupported
            }
            self._desired_update(
                {"labels": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}},
                project,
                fields,
            )
        adrs = tuple(record for record in records if record.get("kind") == "adr")
        qualification_project_id = None
        if adrs:
            qualification_project_id = project.extra.get(
                "migration_adr_qualification_project_id"
            )
            try:
                qualification_uuid = uuid.UUID(qualification_project_id)
            except (TypeError, ValueError, AttributeError):
                raise TrackerCapabilityUnavailableError(
                    self.name, "migration_adr_batch_qualification",
                ) from None
            if (
                qualification_uuid.version != 4
                or str(qualification_uuid) != qualification_project_id
                or qualification_project_id == project.id
            ):
                raise TrackerConflictError(
                    "projet de qualification batch ADR Linear non isolé"
                )
            qualification_project = Project(
                project.key,
                qualification_project_id,
                {**project.extra, "milestone_ids": {}},
            )
            if not self.verify_project_identity(qualification_project):
                raise TrackerConflictError(
                    "projet de qualification batch ADR Linear non vérifié"
                )
            # The first cutover preflight is deliberately effect-free.  It can
            # validate the source corpus and the isolated qualification target,
            # but it cannot derive the final ADR bytes yet: known issue links
            # need Linear's readable identifiers, assigned during issue copy.
            base = self._migration_adr_batch_records(adrs)
            if len({record["adr_id"] for record in base}) != len(base):
                raise TrackerConflictError("identité ADR source de migration dupliquée")
            # Linear imports this corpus only as a batch whose complete
            # provider bytes a qualification probe pins before any write.
            for record in base:
                _preflight_adr_body_readback(
                    record["body"],
                    allow_foundry_adr_0001=(
                        record["adr_id"] == _FOUNDRY_ADR_0001_ID
                    ),
                    new_body=False,
                )
        profile = {
            "kind": "linear-deterministic-identity-v1",
            "namespace": "foundry-linear-migration-issue.v1",
        }
        if qualification_project_id is not None:
            profile.update({
                "adr_qualification": "deferred-after-issue-readback",
                "adr_qualification_project_id": qualification_project_id,
            })
        return profile

    @staticmethod
    def _normalize_migration_adr_profile(raw: list[dict]) -> tuple[dict, ...]:
        if not raw or not all(isinstance(item, dict) for item in raw):
            raise ValueError("profil batch ADR Linear invalide")
        normalized = []
        for item in raw:
            candidate = dict(item)
            for name in (
                "supersedes", "issue_refs", "missing_relations",
                "source_only_issue_refs",
            ):
                if name in candidate and isinstance(candidate[name], list):
                    candidate[name] = tuple(candidate[name])
            normalized.append(candidate)
        return tuple(normalized)

    @staticmethod
    def _migration_adr_batch_records(records: tuple[dict, ...]) -> tuple[dict, ...]:
        out = []
        for record in records:
            relations = record["relations"]
            missing = tuple(sorted(
                name for name in ("supersedes", "superseded_by", "issues")
                if relations.get(name) == "unknown"
            ))
            out.append({
                "adr_id": record["id"], "title": record["title"],
                "body": record["body"],
                "historical_status": record["status"],
                "source_ref": record["source_ref"],
                "source_created": record.get("source_created"),
                "source_updated": record.get("source_updated"),
                "expected_source_sha256": hashlib.sha256(
                    record["body"].encode()
                ).hexdigest(),
                "supersedes": (
                    () if relations.get("supersedes") == "unknown"
                    else tuple(relations["supersedes"])
                ),
                "superseded_by": (
                    None if relations.get("superseded_by") == "unknown"
                    else relations["superseded_by"]
                ),
                "issue_refs": (
                    () if relations.get("issues") == "unknown"
                    else tuple(relations["issues"])
                ),
                "missing_relations": missing,
                **({
                    "source_only_issue_refs": tuple(record["source_only_issue_refs"]),
                } if "source_only_issue_refs" in record else {}),
            })
        return tuple(out)

    def migration_find_issue(self, project: Project, source_ref: str) -> Issue | None:
        binding = self._activate(project)
        native_id = _migration_issue_id(binding["project_id"], source_ref)
        try:
            raw = self._read_raw(native_id)
        except IssueUnavailableError:
            return None
        self._assert_issue_project(raw, binding)
        if raw.get("id") != native_id:
            raise TrackerConflictError("identité migration Linear divergente")
        return self._to_issue(raw, project)

    def migration_import_issue(
        self, project: Project, snapshot: dict, *, source_ref: str,
    ) -> Issue:
        registry.require_writable_project(self.name, project)
        existing = self.migration_find_issue(project, source_ref)
        if existing is not None:
            return existing
        binding = self._activate(project)
        fields = {
            native: value
            for native, value in {
                "Type": snapshot["attributes"].get("type"),
                "Priority": snapshot["attributes"].get("priority"),
                "Estimate": snapshot["attributes"].get("estimate"),
                "State": snapshot["attributes"].get("state"),
            }.items()
            if value is not None
        }
        skeleton = {
            "labels": {
                "nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
        values, expected = (
            self._desired_update(skeleton, project, fields) if fields else ({}, {})
        )
        if "label_ids" in expected:
            values.pop("addedLabelIds", None)
            values.pop("removedLabelIds", None)
            values["labelIds"] = sorted(expected["label_ids"])
        native_id = _migration_issue_id(binding["project_id"], source_ref)
        input_value = {
            "id": native_id,
            "teamId": binding["team_id"],
            "projectId": binding["project_id"],
            "title": snapshot["title"],
            "description": snapshot["body"],
            **values,
        }
        try:
            data = self._graphql(_ISSUE_CREATE, {"input": input_value}, "migration.issue.create")
            payload = self._mutation_payload(
                data, "issueCreate", "migration.issue.create"
            )
            created = payload.get("issue")
            if not isinstance(created, dict) or created.get("id") != native_id:
                raise LinearTrackerError(
                    "migration.issue.create", None, "ambiguous_mutation_response"
                )
        except LinearTrackerError:
            recovered = self.migration_find_issue(project, source_ref)
            if recovered is None:
                raise
            return recovered
        raw = self._read_raw(native_id)
        self._assert_issue_project(raw, binding)
        if (
            raw.get("title") != snapshot["title"]
            or (raw.get("description") or "") != snapshot["body"]
            or not self._raw_matches(raw, expected)
        ):
            raise TrackerConflictError("issue migration Linear divergente après création")
        return self._to_issue(raw, project)

    def migration_link_issue(
        self, project: Project, src_id: str, link_type: str, dst_id: str,
    ) -> None:
        registry.require_writable_project(self.name, project)
        self.link(src_id, link_type, dst_id, project=project)

    # ---- transport -------------------------------------------------
    def _graphql(self, document: str, variables: dict, operation: str) -> dict:
        """One GraphQL call; a pure read is retried a bounded number of times (PAT-98).

        Only a transport failure or HTTP 429/500/502/503/504 on a read-only document is
        retried. Writes, data/binding/authorization errors and quota exhaustion never are.
        The retry wraps the single raw HTTP call, never any caller recovery logic.
        """
        retries = 0
        while True:
            try:
                return self._graphql_once(document, variables, operation)
            except LinearQuotaExhaustedError as error:
                raise error._with_retries(retries) from None
            except LinearTrackerError as error:
                retryable = (
                    error.code == "transport_error"
                    or (error.code == "http_error" and error.status in _RETRYABLE_STATUSES)
                )
                if (
                    not retryable
                    or retries >= self.read_retries
                    or not _is_read_only_document(document)
                ):
                    raise error._with_retries(retries) from None
                _sleep(_READ_RETRY_DELAYS[retries])
                retries += 1

    def _note_rate_limit(self, headers) -> None:
        for kind in ("requests", "complexity"):
            for field in ("remaining", "reset"):
                value = _header_int(headers, f"x-ratelimit-{kind}-{field}")
                if value is not None:
                    self.rate_limit[f"{kind}_{field}"] = value

    def _quota_error(
        self, operation: str, status: int | None, headers, body_codes=(),
    ) -> LinearQuotaExhaustedError | None:
        """Classify a rate-limit failure: HTTP 429, a RATELIMITED code, or HTTP 400/429
        whose headers show a zero remaining quota. Anything else (e.g. a plain 400) is None."""
        self._note_rate_limit(headers)
        remaining = _header_int(headers, "x-ratelimit-requests-remaining")
        reset = _header_int(headers, "x-ratelimit-requests-reset")
        complexity = _header_int(headers, "x-ratelimit-complexity-remaining")
        if complexity == 0 and remaining != 0:
            remaining = 0
            reset = _header_int(headers, "x-ratelimit-complexity-reset")
        exhausted = (
            status == 429
            or any(code in _RATE_LIMIT_CODES for code in body_codes)
            or (remaining == 0 and status in (None, 400, 429))
        )
        if not exhausted:
            return None
        return LinearQuotaExhaustedError(operation, status, remaining, reset)

    @staticmethod
    def _error_codes(envelope) -> list:
        errors = envelope.get("errors") if isinstance(envelope, dict) else None
        if not isinstance(errors, list):
            return []
        codes = []
        for item in errors:
            extensions = item.get("extensions") if isinstance(item, dict) else None
            if isinstance(extensions, dict):
                codes.append(extensions.get("code"))
        return codes

    def _graphql_once(self, document: str, variables: dict, operation: str) -> dict:
        headers = None
        if self._transport is not None:
            # Test seam: a fake transport may raise an exception carrying `status` (int HTTP
            # status) and `headers` (mapping of x-ratelimit-* values), or return an
            # envelope with an optional "_headers" mapping, to simulate real responses.
            try:
                envelope = self._transport(document, variables)
            except LinearTrackerError:
                raise
            except Exception as error:
                status = getattr(error, "status", None)
                headers = getattr(error, "headers", None)
                if not isinstance(status, int) or isinstance(status, bool):
                    raise LinearTrackerError(operation, None, "transport_error") from None
                quota = self._quota_error(operation, status, headers)
                if quota is not None:
                    raise quota from None
                raise LinearTrackerError(operation, status, "http_error") from None
            if isinstance(envelope, dict):
                headers = envelope.get("_headers")
        else:
            data = json.dumps(
                {"query": document, "variables": variables},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            request = urllib.request.Request(self.endpoint, data=data, method="POST")
            request.add_header("Authorization", self.token)
            request.add_header("Accept", "application/json")
            request.add_header("Content-Type", "application/json")
            try:
                opener = urllib.request.build_opener(_SameOriginRedirectHandler())
                with opener.open(request, timeout=15) as response:
                    raw = response.read(_MAX_RESPONSE_BYTES + 1)
                    if len(raw) > _MAX_RESPONSE_BYTES:
                        raise LinearTrackerError(
                            operation, response.status, "response_too_large"
                        )
                    status = response.status
                    headers = response.headers
            except LinearTrackerError:
                raise
            except urllib.error.HTTPError as error:
                codes = []
                try:
                    codes = self._error_codes(json.loads(error.read(_MAX_RESPONSE_BYTES)))
                except (OSError, ValueError, UnicodeError):
                    pass
                quota = self._quota_error(operation, error.code, error.headers, codes)
                if quota is not None:
                    raise quota from None
                raise LinearTrackerError(operation, error.code, "http_error") from None
            except (OSError, urllib.error.URLError, TimeoutError):
                raise LinearTrackerError(operation, None, "transport_error") from None
            try:
                envelope = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise LinearTrackerError(
                    operation, status, "invalid_response"
                ) from None
        self._note_rate_limit(headers)
        if not isinstance(envelope, dict):
            raise LinearTrackerError(operation, None, "invalid_response")
        if envelope.get("errors"):
            quota = self._quota_error(operation, None, headers, self._error_codes(envelope))
            if quota is not None:
                raise quota
            raise LinearTrackerError(operation, None, "graphql_error")
        payload = envelope.get("data")
        if not isinstance(payload, dict):
            raise LinearTrackerError(operation, None, "invalid_response")
        return payload

    @staticmethod
    def _mutation_payload(data: dict, field: str, operation: str) -> dict:
        payload = data.get(field)
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise LinearTrackerError(operation, None, "mutation_failed")
        return payload

    def _read_comment(self, comment_id: str, operation: str) -> dict | None:
        data = self._graphql(_COMMENT_QUERY, {"id": comment_id}, operation)
        comments = _connection(data.get("comments"), operation)
        if len(comments) > 1:
            raise LinearTrackerError(operation, None, "invalid_response")
        if comments and comments[0].get("id") != comment_id:
            raise LinearTrackerError(operation, None, "invalid_response")
        return comments[0] if comments else None

    def _read_issue_relation(self, relation_id: str, operation: str) -> dict:
        """Read one relation by its provider-declared unique identifier.

        Linear's singular query is non-null: an absent relation and a permission
        failure both surface as GraphQL errors.  Callers must therefore use this only
        to verify or recover an expected slot, never to infer that a create is safe.
        """
        data = self._graphql(_RELATION_QUERY, {"id": relation_id}, operation)
        relation = data.get("issueRelation")
        if not isinstance(relation, dict) or relation.get("id") != relation_id:
            raise LinearTrackerError(operation, None, "invalid_response")
        return relation

    @staticmethod
    def _issue_relation_matches(
        relation: object,
        *,
        relation_id: str,
        relation_type: str,
        issue_id: str,
        related_issue_id: str,
    ) -> bool:
        if not isinstance(relation, dict):
            return False
        issue = relation.get("issue")
        related_issue = relation.get("relatedIssue")
        return (
            relation.get("id") == relation_id
            and relation.get("type") == relation_type
            and isinstance(issue, dict)
            and issue.get("id") == issue_id
            and isinstance(related_issue, dict)
            and related_issue.get("id") == related_issue_id
        )

    # ---- explicit binding -----------------------------------------
    @staticmethod
    def _binding(project: Project) -> dict:
        if not isinstance(project, Project):
            raise LinearBindingError("project_missing")
        project_key = project.key
        if (
            not isinstance(project_key, str)
            or _ISSUE_KEY.fullmatch(project_key) is None
        ):
            raise LinearBindingError("project_key_invalid")
        project_id = _safe_id(project.id, "project_id_invalid")
        team_id = _safe_id(project.extra.get("team_id"), "team_id_invalid")
        canonical = project.extra.get("canonical_repo")
        try:
            canonical = registry.canonical_repository_identity(canonical)
        except (TypeError, ValueError):
            raise LinearBindingError("canonical_repo_invalid") from None
        states = _mapping(project.extra, "state_ids", required=True)
        if set(states) != _STATES:
            raise LinearBindingError("state_ids_incomplete")
        milestone_ids = _mapping(project.extra, "milestone_ids")
        type_label_ids = _mapping(project.extra, "type_label_ids")
        label_ids = _mapping(project.extra, "label_ids")
        if set(type_label_ids.values()) & set(label_ids.values()):
            raise LinearBindingError("label_ids_overlap")
        return {
            "key": project_key,
            "project_id": project_id,
            "team_id": team_id,
            "canonical_repo": canonical,
            "state_ids": states,
            "milestone_ids": milestone_ids,
            "type_label_ids": type_label_ids,
            "label_ids": label_ids,
        }

    def _activate(self, project: Project) -> dict:
        binding = self._binding(project)
        self._active_project = project
        return binding

    def _project(self) -> Project:
        if self._active_project is None:
            raise LinearBindingError("project_not_resolved")
        self._binding(self._active_project)
        return self._active_project

    def verify_project_identity(self, project: Project) -> bool:
        binding = self._binding(project)
        data = self._graphql(
            _PROJECT_BINDING_QUERY,
            {"id": project.id, "teamId": binding["team_id"]},
            "project-binding-read",
        )
        raw = data.get("project")
        teams = raw.get("teams") if isinstance(raw, dict) else None
        try:
            rows = _connection(teams, "project-binding-read.teams")
        except LinearTrackerError:
            return False
        if not (
            raw.get("id") == project.id
            and len(rows) == 1
            and rows[0].get("id") == binding["team_id"]
            and rows[0].get("key") == project.key
        ):
            return False
        try:
            for release in binding["milestone_ids"]:
                self._mapped_release(project, release)
        except ReleaseScopeUnavailableError:
            return False
        return True

    def resolve_project(self, repo: str) -> Project:
        # The provider-neutral port historically passes a repository basename here.
        # Linear must never treat that value (or PROJECT_REPO) as identity evidence:
        # resolve the actual checkout instead, so even an overlooked generic read
        # remains fail-closed at the adapter boundary.
        del repo
        return self.resolve_checkout_project()

    def resolve_checkout_project(
        self,
        cwd: str | None = None,
        *,
        checkout_identity: str | None = None,
    ) -> Project:
        project = super().resolve_checkout_project(
            cwd, checkout_identity=checkout_identity,
        )
        self._activate(project)
        return project

    def validate_mutation_repository(self, repo: str, checkout_identity: str) -> None:
        del repo
        self.resolve_checkout_project(checkout_identity=checkout_identity)

    def preflight_issue_operation(self, operation: str) -> None:
        if operation == "openpr" and self.append_only_lifecycle_supported:
            return
        if (
            operation == "merge"
            and self.append_only_lifecycle_supported
            and self.acceptance_proof_projection_supported
        ):
            return
        if operation not in {"openpr", "merge"}:
            raise TrackerCapabilityUnavailableError(self.name, f"lifecycle:{operation}")
        raise TrackerCapabilityUnavailableError(
            self.name,
            "append-only-lifecycle-proof",
        )

    def preflight_merge_effect(self) -> None:
        """Refuse a GitHub merge while Linear can auto-complete linked issues.

        This is deliberately a final, read-only provider check: PR linkage is allowed,
        but a team-level ``merge -> completed`` rule would let Linear close every
        linked issue, including an unfinished prerequisite.  The API does not expose
        a CAS for this configuration, so an unavailable, malformed, or paginated
        response is also unsafe and refuses the irreversible code-host effect.
        """
        binding = self._binding(self._project())
        data = self._graphql(
            _TEAM_GIT_AUTOMATION_STATES_QUERY,
            {"id": binding["team_id"]},
            "team.git-automation-states",
        )
        team = data.get("team")
        if not isinstance(team, dict) or team.get("id") != binding["team_id"]:
            raise LinearTrackerError(
                "team.git-automation-states",
                None,
                "invalid_response",
            )
        states = _connection(
            team.get("gitAutomationStates"),
            "team.git-automation-states",
        )
        for automation in states:
            event = automation.get("event")
            state = automation.get("state")
            if (
                not isinstance(automation.get("id"), str)
                    or event not in _GIT_AUTOMATION_EVENTS
                or (
                    state is not None
                    and (
                        not isinstance(state, dict)
                        or not isinstance(state.get("id"), str)
                        or state.get("type") not in _WORKFLOW_STATE_TYPES
                    )
                )
            ):
                raise LinearTrackerError(
                    "team.git-automation-states",
                    None,
                    "invalid_response",
                )
            if event == "merge" and state is not None and state["type"] == "completed":
                raise TrackerConflictError(
                    "Linear Git automation unsafe: merge maps to completed state",
                )

    @staticmethod
    def _lifecycle_marker(
        operation: str,
        issue_id: str,
        payload: dict,
    ) -> tuple[str, str, str]:
        if operation not in _LIFECYCLE_OPERATIONS:
            raise TrackerCapabilityUnavailableError("linear", f"lifecycle:{operation}")
        canonical = json.dumps(
            {
            "schema": _LIFECYCLE_SCHEMA,
            "operation": operation,
            "issue": issue_id,
            "payload": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        marker = f"{_LIFECYCLE_SCHEMA}:{operation}:{digest}"
        slot = {
            "schema": _LIFECYCLE_SCHEMA,
            "operation": operation,
            "issue": issue_id,
        }
        if operation == "state-review":
            slot["generation"] = payload.get("generation")
        elif operation in {"acceptance", "acceptance-override"}:
            slot["generation"] = payload.get("review_generation")
        slot_canonical = json.dumps(
            slot,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        slot_digest = hashlib.sha256(slot_canonical.encode("ascii")).hexdigest()
        comment_id = str(uuid.UUID(slot_digest[:32], version=4))
        body = f"{_LIFECYCLE_HEADER}\nmarker: {marker}\ncoordinates: {canonical}"
        return marker, body, comment_id

    @classmethod
    def _decode_lifecycle_comment(
        cls,
        issue_id: str,
        body: object,
    ) -> tuple[str, dict, str, str] | None:
        if not isinstance(body, str) or not body.startswith(_LIFECYCLE_HEADER):
            return None
        lines = body.splitlines()
        if (
            len(lines) != 3
            or not lines[1].startswith("marker: ")
            or not lines[2].startswith("coordinates: ")
        ):
            raise TrackerConflictError("Linear lifecycle comment malformed")
        marker = lines[1].removeprefix("marker: ")
        try:
            value = json.loads(lines[2].removeprefix("coordinates: "))
        except (ValueError, TypeError, RecursionError):
            raise TrackerConflictError("Linear lifecycle comment malformed") from None
        if (
            not isinstance(value, dict)
                or set(value) != {"schema", "operation", "issue", "payload"}
                or value.get("schema") != _LIFECYCLE_SCHEMA
                or value.get("issue") != issue_id
                or value.get("operation") not in _LIFECYCLE_OPERATIONS
            or not isinstance(value.get("payload"), dict)
        ):
            raise TrackerConflictError("Linear lifecycle comment malformed")
        expected_marker, expected_body, expected_id = cls._lifecycle_marker(
            value["operation"],
            issue_id,
            value["payload"],
        )
        if marker != expected_marker or body != expected_body:
            raise TrackerConflictError("Linear lifecycle comment integrity invalid")
        return value["operation"], value["payload"], body, expected_id

    @staticmethod
    def _validate_acceptance_projection(
        issue_id: str,
        body: str,
        payload: dict,
        review: dict | None,
    ) -> None:
        if set(payload) != {
            "native_state_id",
            "body_digest",
            "checked",
            "proof",
            "review_generation",
        }:
            raise TrackerConflictError("Linear acceptance proof malformed")
        proof = payload.get("proof")
        if not isinstance(proof, dict) or set(proof) != {
            "schema_version",
            "proof_id",
            "issue",
            "review",
            "coordinates",
            "quality",
        }:
            raise TrackerConflictError("Linear acceptance proof malformed")
        canonical_proof = dict(proof)
        proof_id = canonical_proof.pop("proof_id", None)
        calculated = hashlib.sha256(
            json.dumps(
                canonical_proof,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()
        issue = proof.get("issue")
        coordinates = proof.get("coordinates")
        criteria = issue.get("criteria") if isinstance(issue, dict) else None
        from foundry.routing import acceptance_criteria, acceptance_digest

        expected = acceptance_criteria(body)
        if (
            proof.get("schema_version") != 1
            or proof_id != calculated
                or proof.get("quality") != "mergeable"
                or payload.get("body_digest") != hashlib.sha256(body.encode()).hexdigest()
                or not isinstance(issue, dict)
                or set(issue) != {"id", "ac_digest", "criteria"}
                or issue.get("id") != issue_id
                or issue.get("ac_digest") != acceptance_digest(expected)
                or not isinstance(criteria, list)
                or criteria != [{**item, "verdict": "pass"} for item in expected]
                or type(payload.get("checked")) is not int
                or not 1 <= payload["checked"] <= len(expected)
                or not isinstance(coordinates, dict)
                or set(coordinates) != {"head", "diff_hash", "base"}
                or review is None
                or coordinates.get("head") != review.get("head_sha")
                or coordinates.get("base") != review.get("base_sha")
                or coordinates.get("diff_hash") != review.get("review_digest")
            or payload.get("review_generation") != review.get("generation")
        ):
            raise TrackerConflictError("Linear acceptance proof malformed or stale")

    @staticmethod
    def _validate_acceptance_override_projection(
        payload: dict,
        review: dict | None,
    ) -> None:
        """Validate one typed human AC override bound to one exact review generation.

        The override is audit evidence that AC were *not* proven; it never counts as
        acceptance and only its public reason code (never prose) is retained.
        """
        if (
            set(payload)
            != {"native_state_id", "review_generation", "reason", *_AC_OVERRIDE_BOUND_KEYS}
            or type(payload.get("review_generation")) is not int
            or not isinstance(payload.get("reason"), str)
            or _AC_OVERRIDE_REASON.fullmatch(payload["reason"]) is None
            or review is None
            or payload["review_generation"] != review.get("generation")
            or any(payload.get(key) != review.get(key) for key in _AC_OVERRIDE_BOUND_KEYS)
        ):
            raise TrackerConflictError("Linear acceptance override malformed or stale")

    @staticmethod
    def _native_state_can_advance(previous: str, current: str) -> bool:
        if previous == current:
            return True
        rank = {
            "backlog": 0,
            "ready": 0,
            "in-progress": 1,
            "review": 2,
            "done": 3,
        }
        return previous in rank and current in rank and rank[current] >= rank[previous]

    def _review_regression_proven(
        self,
        issue_id: str,
        native_issue_id: str,
        native_state_id: str,
        review_receipt_at: int | None,
        state_ids: dict[str, str],
    ) -> bool:
        """Prove the native Review span existed after its receipt and then regressed."""
        if review_receipt_at is None:
            return False
        data = self._graphql(
            _ISSUE_STATE_HISTORY_QUERY, {"id": issue_id}, "issue.state-history"
        )
        raw = data.get("issue")
        if (
            not isinstance(raw, dict)
            or raw.get("id") != native_issue_id
            or raw.get("identifier") != issue_id
            or not isinstance(raw.get("state"), dict)
            or raw["state"].get("id") != native_state_id
        ):
            raise TrackerConflictError("Linear native state history identity changed")
        history = raw.get("stateHistory")
        if not isinstance(history, dict):
            raise TrackerConflictError("Linear native state history unavailable")
        page = history.get("pageInfo")
        if not isinstance(page, dict) or page.get("hasNextPage") is not False:
            raise TrackerConflictError("Linear native state history incomplete")
        spans = history.get("nodes")
        if not isinstance(spans, list) or len(spans) < 3:
            return False
        start, review, current = spans[-3:]
        if any(not isinstance(span, dict) for span in (start, review, current)):
            raise TrackerConflictError("Linear native state history malformed")
        if (
            start.get("stateId") != state_ids["in-progress"]
            or review.get("stateId") != state_ids["review"]
            or current.get("stateId") != native_state_id
            or current.get("endedAt") is not None
        ):
            return False
        start_end = _epoch_ms(start.get("endedAt"))
        review_start = _epoch_ms(review.get("startedAt"))
        review_end = _epoch_ms(review.get("endedAt"))
        current_start = _epoch_ms(current.get("startedAt"))
        return (
            start_end is not None
            and review_start is not None
            and review_end is not None
            and current_start is not None
            and start_end == review_start
            and review_end == current_start
            and review_receipt_at <= review_start < review_end
        )

    def _validate_native_state_history(
        self,
        rows: dict[str, list[tuple[dict, str]]],
        ordered: list[tuple[str, dict]],
        native_state_id: str,
        *,
        issue_id: str,
        native_issue_id: str,
        review_receipt_at: int | None,
        pending_operation: str | None,
        acceptance_complete: bool,
        done: dict | None,
        allow_current_disagreement: bool,
    ) -> None:
        state_ids = self._binding(self._project())["state_ids"]
        state_by_id = {identifier: name for name, identifier in state_ids.items()}
        receipt_ids = [
            payload.get("native_state_id")
            for operation_rows in rows.values()
            for payload, _body in operation_rows
        ]
        if any(
            not isinstance(identifier, str) or identifier not in state_by_id
            for identifier in receipt_ids
        ):
            raise TrackerConflictError("Linear lifecycle native state proof malformed")

        # ``native_state_id`` is an immutable observation coordinate.  Older
        # lifecycle receipts recorded the State observed *before* Foundry's
        # projection; current receipts record the target.  Validate those
        # observations as one history, separately from the authenticated logical
        # targets.  Mixing the two manufactures a false review -> in-progress
        # regression when a historical acceptance receipt observed in-progress.
        def logical_target(operation: str, payload: dict) -> str:
            if operation.startswith("state-"):
                target = payload.get("state")
            elif operation in {"acceptance", "acceptance-override"}:
                # These receipts reached this point only after their generation and
                # exact review coordinates were authenticated above.  They describe
                # acceptance at that review, regardless of the native State observed
                # by the historical writer.
                target = "review"
            else:
                raise TrackerConflictError(
                    "Linear lifecycle native state proof malformed"
                )
            observed = state_by_id[payload["native_state_id"]]
            if target not in state_ids or not self._native_state_can_advance(
                observed, target,
            ):
                raise TrackerConflictError(
                    "Linear lifecycle native state proof malformed"
                )
            return target

        observed_names = [
            state_by_id[payload["native_state_id"]]
            for _operation, payload in ordered
        ]
        if any(
            not self._native_state_can_advance(previous, current)
            for previous, current in zip(observed_names, observed_names[1:])
        ):
            raise TrackerConflictError("Linear native state changed outside lifecycle")
        logical_names = [
            logical_target(operation, payload) for operation, payload in ordered
        ]
        ordered_ids = [state_ids[name] for name in logical_names]
        cockpit_ids = [
            payload["native_state_id"]
            for payload, _body in rows.get("cockpit-evidence", [])
        ]
        if not ordered_ids:
            ordered_ids = cockpit_ids
            logical_names = [state_by_id[identifier] for identifier in ordered_ids]
        elif any(
            identifier not in (
                {state_ids[name] for name in observed_names} | set(ordered_ids)
            )
            for identifier in cockpit_ids
        ):
            # Advisory cockpit receipts record native observations too. A valid
            # historic observation must not be mistaken for a logical target.
            # Neither set gives cockpit evidence lifecycle or acceptance authority.
            raise TrackerConflictError("Linear native state changed outside lifecycle")
        if not ordered_ids:
            raise TrackerConflictError("Linear lifecycle native state proof malformed")

        if any(
            not self._native_state_can_advance(previous, current)
            for previous, current in zip(logical_names, logical_names[1:])
        ):
            raise TrackerConflictError("Linear native state changed outside lifecycle")

        current_name = state_by_id.get(native_state_id)
        if current_name is None:
            raise TrackerConflictError("Linear lifecycle native state unavailable")
        latest_name = logical_names[-1]
        current_is_durable = native_state_id == ordered_ids[-1]
        if allow_current_disagreement:
            return
        # PAT-28: Linear's GitHub integration can asynchronously apply its native
        # ``start`` automation after Foundry has already durably projected a PR
        # review.  This is observation-only: it neither changes the lifecycle
        # projection nor supplies a new receipt.
        reviewed_start_drift = (
            pending_operation in {None, "state-in-progress"}
            and done is None
            and rows.get("state-review")
            and state_by_id[next(
                payload["native_state_id"]
                for operation, payload in reversed(ordered)
                if operation == "state-review"
            )] in {"backlog", "ready"}
            and current_name == "in-progress"
        )
        # PAT-76: current receipt writers project the native start and review
        # states themselves.  Preserve that distinct, fully receipted history when
        # the integration later regresses only the native State to in-progress.
        reviewed_start_review_drift = (
            pending_operation in {None, "state-in-progress"}
            and done is None
            and rows.get("state-in-progress")
            and rows.get("state-review")
            and latest_name == "review"
            and current_name == "in-progress"
        )
        if reviewed_start_review_drift:
            reviewed_start_review_drift = self._review_regression_proven(
                issue_id, native_issue_id, native_state_id,
                review_receipt_at, state_ids,
            )
        pending_forward = (
            pending_operation in {"state-review", "acceptance", "acceptance-override"}
            and current_name != "done"
            and self._native_state_can_advance(latest_name, current_name)
        )
        pending_done = (
            pending_operation == "state-done"
            and current_name == "done"
            and done is None
            and acceptance_complete
            and self._native_state_can_advance(latest_name, current_name)
        )
        # A pre-PAT-56 done receipt records the native State observed before its
        # projection.  Its only supported incomplete replay is the typed AC
        # override recovery; it may read that exact coordinate long enough to
        # append the missing override, then projects State through the normal
        # receipt-authenticated path below.  This is not a native success claim.
        pending_legacy_done_override = (
            pending_operation == "acceptance-override"
            and done is not None
            and done.get("native_state_id") == native_state_id
            and done.get("native_state_id") != state_ids["done"]
        )
        # The append-only receipt is written before its human-facing native State
        # projection.  Between those two effects it is durable evidence of the
        # intended transition, while the older native state remains an observable
        # disagreement.  Only the writer/recovery path may read this interval;
        # ordinary reads have ``pending_operation=None`` and fail closed.
        pending_receipt_projection = (
            pending_operation in {"state-in-progress", "state-review", "state-done"}
            and rows.get(pending_operation, [])
            and current_name != "done"
            and self._native_state_can_advance(current_name, latest_name)
        )
        if not (
            current_is_durable
            or reviewed_start_drift
            or reviewed_start_review_drift
            or pending_forward
            or pending_done
            or pending_legacy_done_override
            or pending_receipt_projection
        ):
            raise TrackerConflictError("Linear native state changed outside lifecycle")
        if (
            current_name == "done"
            and not pending_done
            and (done is None or state_ids["done"] != native_state_id)
        ):
            raise TrackerConflictError("Linear native state changed outside lifecycle")

    def _lifecycle_projection(
        self,
        issue_id: str,
        raw: dict,
        *,
        pending_operation: str | None = None,
        allow_current_disagreement: bool = False,
        epic_closure: EpicClosureOutcome | None = None,
    ) -> dict:
        # Only the fully verified non-code Epic audit supplies this terminal
        # authority. Historical code receipts still undergo all normal checks.
        comments = _connection(raw.get("comments"), "lifecycle.comments")
        rows: dict[str, list[tuple[dict, str]]] = {}
        for comment in comments:
            decoded = self._decode_lifecycle_comment(issue_id, comment.get("body"))
            if decoded is None:
                continue
            operation, payload, body, expected_id = decoded
            if comment.get("id") != expected_id:
                raise TrackerConflictError("Linear lifecycle comment id invalid")
            rows.setdefault(operation, []).append((payload, body))
        native_state = raw.get("state")
        native_state_id = (
            native_state.get("id") if isinstance(native_state, dict) else None
        )
        if not isinstance(native_state_id, str):
            raise TrackerConflictError("Linear lifecycle native state unavailable")
        if not rows:
            done_state_id = self._binding(self._project())["state_ids"]["done"]
            if native_state_id == done_state_id and epic_closure is None:
                raise TrackerConflictError(
                    "Linear native state changed outside lifecycle"
                )
            return {
                "state": "done" if epic_closure is not None else None,
                "pr_url": None,
                "acceptance_complete": False,
                "acceptance_override": None,
                "in_progress": None,
                "acceptance_by_generation": {},
                "acceptance_override_by_generation": {},
                "done": None,
                "review_generation": 0,
                "latest_review": None,
                "latest_review_body_digest": None,
            }

        for operation in {"state-in-progress", "state-done", "cockpit-evidence"}:
            if len(rows.get(operation, [])) > 1:
                raise TrackerConflictError("Linear lifecycle duplicate projection")

        def state_payload(name: str, *, merged: bool = False) -> dict | None:
            operation_rows = rows.get(f"state-{name}", [])
            if not operation_rows:
                return None
            if len(operation_rows) != 1:
                raise TrackerConflictError("Linear lifecycle duplicate projection")
            payload = operation_rows[0][0]
            keys = {"state", "native_state_id"}
            if name == "done":
                keys |= {"pr_url", "head_sha", "base_sha", "review_digest"}
                keys.add("review_generation")
            if merged:
                keys.add("merge_sha")
            if set(payload) != keys or payload.get("state") != name:
                raise TrackerConflictError("Linear lifecycle state proof malformed")
            if name == "done":
                if (
                    not isinstance(payload.get("pr_url"), str)
                        or not payload["pr_url"].startswith("https://github.com/")
                        or _SHA.fullmatch(str(payload.get("head_sha"))) is None
                        or _SHA.fullmatch(str(payload.get("base_sha"))) is None
                    or _DIGEST.fullmatch(str(payload.get("review_digest"))) is None
                ):
                    raise TrackerConflictError("Linear lifecycle state proof malformed")
            if merged and _SHA.fullmatch(str(payload.get("merge_sha"))) is None:
                raise TrackerConflictError("Linear lifecycle state proof malformed")
            return payload

        in_progress = state_payload("in-progress")
        reviews = []
        for payload, body in rows.get("state-review", []):
            expected_keys = {
                "state",
                "native_state_id",
                "pr_url",
                "head_sha",
                "base_sha",
                "review_digest",
                "generation",
                "previous_projection_digest",
            }
            if (
                set(payload) != expected_keys
                or payload.get("state") != "review"
                    or type(payload.get("generation")) is not int
                    or payload["generation"] < 1
                    or not isinstance(payload.get("pr_url"), str)
                    or not payload["pr_url"].startswith("https://github.com/")
                    or _SHA.fullmatch(str(payload.get("head_sha"))) is None
                    or _SHA.fullmatch(str(payload.get("base_sha"))) is None
                or _DIGEST.fullmatch(str(payload.get("review_digest"))) is None
            ):
                raise TrackerConflictError("Linear lifecycle state proof malformed")
            reviews.append((payload, body))
        reviews.sort(key=lambda item: item[0]["generation"])
        previous_body = None
        for expected_generation, (payload, body) in enumerate(reviews, start=1):
            previous_digest = (
                hashlib.sha256(previous_body.encode()).hexdigest()
                if previous_body is not None
                else None
            )
            if (
                payload["generation"] != expected_generation
                or payload.get("previous_projection_digest") != previous_digest
            ):
                raise TrackerConflictError("Linear lifecycle review chain invalid")
            previous_body = body
        review = reviews[-1][0] if reviews else None
        done = state_payload("done", merged=True)
        if done is not None:
            if (
                review is None
                or any(
                done[key] != review[key]
                for key in ("pr_url", "head_sha", "base_sha", "review_digest")
                )
                or done.get("review_generation") != review.get("generation")
            ):
                raise TrackerConflictError("Linear done proof lacks matching review")
        projected_state = (
            "done"
            if done
            else "review"
            if review
            else "in-progress"
            if in_progress
            else None
        )

        acceptance_complete = False
        acceptance_by_generation = {}
        body = raw.get("description") or ""
        reviews_by_generation = {item[0]["generation"]: item[0] for item in reviews}
        for payload, _comment_body in rows.get("acceptance", []):
            generation = payload.get("review_generation")
            if generation in acceptance_by_generation:
                raise TrackerConflictError("Linear lifecycle duplicate projection")
            bound_review = reviews_by_generation.get(generation)
            self._validate_acceptance_projection(issue_id, body, payload, bound_review)
            acceptance_by_generation[generation] = payload
        override_by_generation = {}
        for payload, _comment_body in rows.get("acceptance-override", []):
            generation = payload.get("review_generation")
            bound_review = (
                reviews_by_generation.get(generation)
                if type(generation) is int
                else None
            )
            self._validate_acceptance_override_projection(payload, bound_review)
            if generation in override_by_generation:
                raise TrackerConflictError("Linear lifecycle duplicate projection")
            override_by_generation[generation] = payload
        acceptance_override = None
        if review is not None:
            acceptance_complete = review["generation"] in acceptance_by_generation
            override = override_by_generation.get(review["generation"])
            if override is not None and not acceptance_complete:
                # Audited human override: AC stay incomplete, only the reason is exposed.
                acceptance_override = override["reason"]
        if (
            done is not None
            and not acceptance_complete
            and acceptance_override is None
            # Bounded recovery only: the writer of the missing override receipt must
            # read this exact shape; its readback and every ordinary read stay strict.
            and pending_operation != "acceptance-override"
        ):
            raise TrackerConflictError("Linear done proof lacks matching acceptance")

        state_by_id = {
            identifier: name
            for name, identifier in self._binding(self._project())["state_ids"].items()
        }
        ordered = []
        if in_progress is not None:
            ordered.append(("state-in-progress", in_progress))
        for review_payload, _review_body in reviews:
            ordered.append(("state-review", review_payload))
            bound = [
                (operation, receipt)
                for operation, receipt in (
                    (
                        "acceptance",
                        acceptance_by_generation.get(review_payload["generation"]),
                    ),
                    (
                        "acceptance-override",
                        override_by_generation.get(review_payload["generation"]),
                    ),
                )
                if receipt is not None
            ]
            # Both receipts of one generation sit between its review and the next
            # causal receipt; their mutual order is only the native-state order.
            if len(bound) == 2 and not self._native_state_can_advance(
                str(state_by_id.get(bound[0][1].get("native_state_id"))),
                str(state_by_id.get(bound[1][1].get("native_state_id"))),
            ):
                bound.reverse()
            ordered.extend(bound)
        if done is not None:
            ordered.append(("state-done", done))
        self._validate_native_state_history(
            rows,
            ordered,
            native_state_id,
            issue_id=issue_id,
            native_issue_id=raw["id"],
            review_receipt_at=next(
                (
                    _epoch_ms(comment.get("createdAt"))
                    for comment in comments
                    if reviews and comment.get("body") == reviews[-1][1]
                ),
                None,
            ),
            pending_operation=pending_operation,
            acceptance_complete=acceptance_complete or acceptance_override is not None,
            done=done,
            allow_current_disagreement=(
                allow_current_disagreement or epic_closure is not None
            ),
        )
        return {
            "state": "done" if epic_closure is not None else projected_state,
            "pr_url": (done or review or {}).get("pr_url"),
            "in_progress": in_progress,
            "acceptance_by_generation": acceptance_by_generation,
            "acceptance_complete": acceptance_complete,
            "acceptance_override": acceptance_override,
            "acceptance_override_by_generation": override_by_generation,
            "done": done,
            "review_generation": review.get("generation") if review else 0,
            "latest_review": review,
            "latest_review_body_digest": (
                hashlib.sha256(reviews[-1][1].encode()).hexdigest() if reviews else None
            ),
        }

    def _project_lifecycle(
        self,
        issue_id: str,
        operation: str,
        payload: dict,
        project: Project,
        *,
        native_state_id: str | None = None,
    ) -> bool:
        raw = self._read_raw(issue_id)
        binding = self._activate(project)
        self._assert_issue_project(raw, binding)
        projection = self._lifecycle_projection(
            issue_id,
            raw,
            pending_operation=operation,
        )
        state = raw.get("state")
        observed_native_state_id = state.get("id") if isinstance(state, dict) else None
        if not isinstance(observed_native_state_id, str):
            raise TrackerConflictError("Linear lifecycle native state unavailable")
        if operation == "state-in-progress" and projection["state"] == "review":
            # merge() replays this step before the current exact review/CI gates.
            # A start receipt written after any durable review generation would be
            # reordered before that review during validation and can poison the
            # append-only chain. The review is already stronger evidence; never
            # append or rewrite an in-progress receipt in this state.
            return False
        bounded_payload = {
            **payload,
            "native_state_id": native_state_id or observed_native_state_id,
        }
        if not isinstance(bounded_payload["native_state_id"], str):
            raise TrackerConflictError("Linear lifecycle native state unavailable")
        if operation == "state-in-progress" and projection["in_progress"] is not None:
            bounded_payload["native_state_id"] = projection["in_progress"][
                "native_state_id"
            ]
        if operation == "state-review":
            latest = projection["latest_review"]
            coordinate_keys = {"pr_url", "head_sha", "base_sha", "review_digest"}
            if latest is not None and all(
                latest[key] == payload.get(key) for key in coordinate_keys
            ):
                bounded_payload.update(
                    {
                    "native_state_id": latest["native_state_id"],
                    "generation": latest["generation"],
                        "previous_projection_digest": latest[
                            "previous_projection_digest"
                        ],
                    }
                )
            else:
                bounded_payload.update(
                    {
                    "generation": projection["review_generation"] + 1,
                        "previous_projection_digest": projection[
                            "latest_review_body_digest"
                        ],
                    }
                )
        elif operation == "state-done":
            if projection["review_generation"] < 1:
                raise TrackerConflictError("Linear done proof lacks matching review")
            bounded_payload["review_generation"] = projection["review_generation"]
        elif operation in {"acceptance", "acceptance-override"}:
            previous = projection[
                "acceptance_by_generation"
                if operation == "acceptance"
                else "acceptance_override_by_generation"
            ].get(payload.get("review_generation"))
            if previous is not None:
                bounded_payload["native_state_id"] = previous["native_state_id"]
        _marker, body, comment_id = self._lifecycle_marker(
            operation,
            issue_id,
            bounded_payload,
        )
        existing = []
        for item in _connection(raw.get("comments"), "lifecycle.comments"):
            decoded = self._decode_lifecycle_comment(issue_id, item.get("body"))
            if decoded is not None and decoded[0] == operation:
                if item.get("id") != decoded[3]:
                    raise TrackerConflictError("Linear lifecycle comment id invalid")
                existing.append((item.get("id"), decoded[2]))
        exact = [item for item in existing if item == (comment_id, body)]
        if exact == [(comment_id, body)]:
            prior = self._read_comment(comment_id, "lifecycle.comment.read")
            if (
                not isinstance(prior, dict)
                or prior.get("body") != body
                or (prior.get("issue") or {}).get("id") != raw["id"]
            ):
                raise TrackerConflictError("Linear lifecycle replay readback divergent")
            fresh = self._read_raw(issue_id)
            self._lifecycle_projection(issue_id, fresh)
            return False
        per_generation = {"state-review", "acceptance", "acceptance-override"}
        if operation not in per_generation and existing:
            raise TrackerConflictError("Linear lifecycle divergent before comment")
        if operation in per_generation:
            generation = bounded_payload.get(
                "generation" if operation == "state-review" else "review_generation",
            )
            for _item_id, item_body in existing:
                decoded = self._decode_lifecycle_comment(issue_id, item_body)
                prior_payload = decoded[1] if decoded is not None else {}
                prior_generation = prior_payload.get(
                    "generation"
                    if operation == "state-review"
                    else "review_generation",
                )
                if prior_generation == generation:
                    raise TrackerConflictError(
                        "Linear lifecycle divergent before comment"
                    )

        prior = self._read_comment(comment_id, "lifecycle.comment.read")
        if prior is not None:
            if (
                not isinstance(prior, dict)
                or prior.get("body") != body
                or (prior.get("issue") or {}).get("id") != raw["id"]
            ):
                raise TrackerConflictError("Linear lifecycle comment id collision")
            created_now = False
        else:
            created_now = True
            try:
                data = self._graphql(
                    _COMMENT_CREATE,
                    {"input": {"id": comment_id, "issueId": raw["id"], "body": body}},
                    "lifecycle.comment.create",
                )
                created = self._mutation_payload(
                    data,
                    "commentCreate",
                    "lifecycle.comment.create",
                ).get("comment")
                if (
                    not isinstance(created, dict)
                    or created.get("id") != comment_id
                        or created.get("body") != body
                    or (created.get("issue") or {}).get("id") != raw["id"]
                ):
                    raise LinearTrackerError(
                        "lifecycle.comment.create",
                        None,
                        "invalid_response",
                    )
            except LinearTrackerError:
                # The provider may have committed the deterministic create before the
                # response was interrupted. Recovery is allowed only through its exact ID.
                recovered = self._read_comment(
                    comment_id,
                    "lifecycle.comment.recover",
                )
                if (
                    not isinstance(recovered, dict)
                    or recovered.get("body") != body
                    or (recovered.get("issue") or {}).get("id") != raw["id"]
                ):
                    raise
        fresh = self._read_raw(issue_id)
        observed = []
        for item in _connection(fresh.get("comments"), "lifecycle.readback"):
            decoded = self._decode_lifecycle_comment(issue_id, item.get("body"))
            if decoded is not None and decoded[0] == operation:
                if item.get("id") != decoded[3]:
                    raise TrackerConflictError("Linear lifecycle comment id invalid")
                observed.append((item.get("id"), decoded[2]))
        if (comment_id, body) not in observed:
            raise TrackerConflictError("Linear lifecycle divergent after comment")
        self._lifecycle_projection(
            issue_id,
            fresh,
            pending_operation=operation,
        )
        return created_now

    def validate_issue_binding(self, project: Project, *issue_ids: str) -> None:
        binding = self._activate(project)
        for issue_id in issue_ids:
            raw = self._read_raw(issue_id)
            self._assert_issue_project(raw, binding)

    def validate_adr_binding(self, project: Project, *adr_ids: str) -> None:
        for adr_id in adr_ids:
            if self.adr_for_mutation(project, adr_id) is None:
                raise AdrUnavailableError(adr_id)

    @staticmethod
    def _assert_issue_project(raw: dict, binding: dict) -> None:
        team = raw.get("team") if isinstance(raw, dict) else None
        project = raw.get("project") if isinstance(raw, dict) else None
        if (
            not isinstance(team, dict)
            or team.get("id") != binding["team_id"]
                or not isinstance(project, dict)
            or project.get("id") != binding["project_id"]
        ):
            raise LinearBindingError("issue_outside_binding")

    @staticmethod
    def _adr_issue_identity(
        raw: dict, binding: dict, operation: str
    ) -> tuple[str, str]:
        """Return the canonical readable and native IDs for one bound issue."""
        LinearTracker._assert_issue_project(raw, binding)
        native_id = raw.get("id") if isinstance(raw, dict) else None
        identifier = raw.get("identifier") if isinstance(raw, dict) else None
        team = raw.get("team") if isinstance(raw, dict) else None
        prefix = f"{binding['key']}-"
        number = (
            identifier[len(prefix) :]
            if isinstance(identifier, str) and identifier.startswith(prefix)
            else None
        )
        if (
            not isinstance(native_id, str)
            or _SAFE_ID.fullmatch(native_id) is None
            or not isinstance(identifier, str)
            or _SAFE_ID.fullmatch(identifier) is None
            or not isinstance(team, dict)
            or team.get("key") != binding["key"]
            or not isinstance(number, str)
            or _ISSUE_NUMBER.fullmatch(number) is None
        ):
            raise LinearTrackerError(operation, None, "invalid_response")
        return identifier, native_id

    def _resolve_adr_issue_reference(
        self, issue_ref: str, binding: dict
    ) -> tuple[str, str]:
        """Resolve an alias and corroborate both provider identity coordinates."""
        operation = "adr.issue.resolve"
        raw = self._read_raw(issue_ref)
        identity = self._adr_issue_identity(raw, binding, operation)
        for exact_ref in dict.fromkeys(identity):
            if exact_ref == issue_ref:
                continue
            try:
                exact = self._read_raw(exact_ref)
            except IssueUnavailableError:
                raise TrackerConflictError(
                    "Linear ADR issue alias resolved ambiguously"
                ) from None
            if self._adr_issue_identity(exact, binding, operation) != identity:
                raise TrackerConflictError(
                    "Linear ADR issue alias resolved ambiguously"
                )
        return identity

    def _canonicalize_adr_issue_refs(
        self, issue_refs: tuple[str, ...], binding: dict
    ) -> tuple[tuple[str, ...], dict[str, str]]:
        canonical_to_native = {}
        native_to_canonical = {}
        for issue_ref in sorted(issue_refs):
            identifier, native_id = self._resolve_adr_issue_reference(
                issue_ref, binding
            )
            if identifier in canonical_to_native:
                if canonical_to_native[identifier] != native_id:
                    raise TrackerConflictError(
                        "Linear ADR issue alias resolved ambiguously"
                    )
                raise TrackerConflictError(
                    "Linear ADR issue references duplicate provider identity"
                )
            if native_id in native_to_canonical:
                raise TrackerConflictError(
                    "Linear ADR issue references duplicate provider identity"
                )
            canonical_to_native[identifier] = native_id
            native_to_canonical[native_id] = identifier
        if len(canonical_to_native) > _ADR_RELATION_LIMIT:
            raise TrackerConflictError("Linear ADR issue relation limit exceeded")
        identifiers = tuple(sorted(canonical_to_native))
        return identifiers, canonical_to_native

    # ---- reads / normalization ------------------------------------
    @contextlib.contextmanager
    def graph_snapshot(self):
        """PAT-99: one graph snapshot reads each node at most once.

        Inside this context (same thread) a successful ``issue.read`` is reused by the
        next read of the same id, so a node is fetched once for validation, binding
        check, normalization and diagnostic.  The scope is private to ONE snapshot: it
        is created empty, never shared with another scope (a nested snapshot, the
        pre-write S1, the post-write verification each open their own), and dropped on
        exit, so nothing survives across snapshots, calls or the write.
        """
        stack = getattr(self._snapshot_scopes, "stack", None)
        if stack is None:
            stack = self._snapshot_scopes.stack = []
        scope: dict[str, dict] = {}
        stack.append(scope)
        try:
            yield
        finally:
            stack.pop()

    def _read_raw(self, issue_id: str) -> dict:
        if not isinstance(issue_id, str) or not issue_id:
            raise IssueUnavailableError(str(issue_id))
        stack = getattr(self._snapshot_scopes, "stack", None)
        scope = stack[-1] if stack else None
        if scope is not None and issue_id in scope:
            return copy.deepcopy(scope[issue_id])
        data = self._graphql(_ISSUE_QUERY, {"id": issue_id}, "issue.read")
        raw = data.get("issue")
        if raw is None:
            raise IssueUnavailableError(issue_id)
        if not isinstance(raw, dict):
            raise LinearTrackerError("issue.read", None, "invalid_response")
        if scope is not None:
            scope[issue_id] = copy.deepcopy(raw)
        return raw

    @staticmethod
    def _ac_counts(body: str) -> tuple[int, int]:
        marks = [
            match.group("mark")
            for line in body.splitlines()
            if (match := _AC_CHECKBOX.match(line)) is not None
        ]
        done = sum(mark in {"x", "X"} for mark in marks)
        return done, len(marks)

    def _to_issue(
        self,
        raw: dict,
        project: Project | None = None,
        *,
        observe_lifecycle: bool = False,
        project_epic_closure: bool = True,
    ) -> Issue:
        project = project or self._project()
        binding = self._binding(project)
        self._assert_issue_project(raw, binding)
        for key in ("id", "identifier", "title", "state", "team", "project"):
            if key not in raw:
                raise LinearTrackerError("normalize", None, "invalid_response")
        state = raw.get("state")
        if not isinstance(state, dict) or not isinstance(state.get("id"), str):
            raise LinearTrackerError("normalize", None, "invalid_response")
        state_by_id = {
            identifier: name for name, identifier in binding["state_ids"].items()
        }
        if state["id"] not in state_by_id:
            raise LinearBindingError("state_id_unmapped")

        labels_raw = _connection(raw.get("labels"), "normalize.labels")
        type_by_id = {
            identifier: name for name, identifier in binding["type_label_ids"].items()
        }
        label_by_id = {
            identifier: name for name, identifier in binding["label_ids"].items()
        }
        label_ids = []
        for node in labels_raw:
            identifier = node.get("id")
            if not isinstance(identifier, str):
                raise LinearTrackerError("normalize.labels", None, "invalid_response")
            if identifier not in type_by_id and identifier not in label_by_id:
                raise LinearBindingError("label_id_unmapped")
            label_ids.append(identifier)
        if len(label_ids) != len(set(label_ids)):
            raise LinearTrackerError("normalize.labels", None, "invalid_response")
        types = [
            type_by_id[identifier]
            for identifier in label_ids
            if identifier in type_by_id
        ]
        if len(types) > 1:
            raise LinearBindingError("type_labels_ambiguous")
        labels = [
            label_by_id[identifier]
            for identifier in label_ids
            if identifier in label_by_id
        ]

        links: list[Link] = []
        parent = raw.get("parent")
        if parent is not None:
            if not isinstance(parent, dict) or not isinstance(
                parent.get("identifier"), str
            ):
                raise LinearTrackerError("normalize", None, "invalid_response")
            links.append(Link("subtask-of", "inward", parent["identifier"]))
        for child in _connection(raw.get("children"), "normalize.children"):
            if not isinstance(child.get("identifier"), str):
                raise LinearTrackerError("normalize", None, "invalid_response")
            links.append(Link("parent-of", "outward", child["identifier"]))
        for relation in _connection(raw.get("relations"), "normalize.relations"):
            links.append(_relation_link(relation, inverse=False))
        for relation in _connection(
            raw.get("inverseRelations"), "normalize.inverse-relations"
        ):
            links.append(_relation_link(relation, inverse=True))

        milestone = None
        milestone_raw = raw.get("projectMilestone")
        if milestone_raw is not None:
            if not isinstance(milestone_raw, dict) or not isinstance(
                milestone_raw.get("id"), str
            ):
                raise LinearTrackerError("normalize", None, "invalid_response")
            milestone_by_id = {
                identifier: name
                for name, identifier in binding["milestone_ids"].items()
            }
            if milestone_raw["id"] not in milestone_by_id:
                raise LinearBindingError("milestone_id_unmapped")
            milestone = milestone_by_id[milestone_raw["id"]]

        body = raw.get("description") or ""
        if not isinstance(body, str):
            raise LinearTrackerError("normalize", None, "invalid_response")
        _native_done, total = self._ac_counts(body)
        # Native checkbox state is not a Linear lifecycle authority: only the
        # proof-bound append-only projection may make AC complete.
        done = 0
        native_state = state_by_id[state["id"]]
        try:
            epic_closure = None
            if project_epic_closure and any(
                isinstance(row.get("body"), str)
                and row["body"].startswith("Foundry Epic closure audit (append-only).")
                for row in _connection(raw.get("comments"), "epic-closure.comments")
            ):
                if native_state == "done":
                    epic_closure = self._verified_epic_closure(raw, project)
                else:
                    self._closure_from_raw(raw, project, require_done=False)
            lifecycle = self._lifecycle_projection(
                raw["identifier"],
                raw,
                allow_current_disagreement=observe_lifecycle,
                epic_closure=epic_closure,
            )
            projection_status = (
                "native-only" if lifecycle["state"] is None
                else "aligned" if lifecycle["state"] == native_state
                else "disagreement"
            )
        except TrackerConflictError:
            if not observe_lifecycle:
                raise
            # Observation relaxes only the current human-facing State comparison.
            # Any malformed marker, incompatible historical observation or invalid
            # review/acceptance binding remains unknown and supplies no authority.
            lifecycle = {
                "state": None,
                "acceptance_complete": False,
                "pr_url": None,
            }
            projection_status = "unknown"
        if lifecycle["acceptance_complete"]:
            done = total
        acceptance_status = "unknown"
        acceptance_source = None
        acceptance_coordinates = None
        review_generation = lifecycle.get("review_generation")
        accepted = lifecycle.get("acceptance_by_generation", {}).get(
            review_generation
        )
        overridden = lifecycle.get("acceptance_override_by_generation", {}).get(
            review_generation
        )
        if lifecycle["acceptance_complete"] and isinstance(accepted, dict):
            acceptance_status = "accepted"
            acceptance_source = "linear-acceptance-proof"
            acceptance_coordinates = json.dumps(
                {
                    "body_digest": accepted.get("body_digest"),
                    "checked": accepted.get("checked"),
                    "native_state_id": accepted.get("native_state_id"),
                    "proof_id": (accepted.get("proof") or {}).get("proof_id"),
                    "review_generation": accepted.get("review_generation"),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        elif lifecycle.get("acceptance_override") is not None and isinstance(
            overridden, dict
        ):
            acceptance_status = "override"
            acceptance_source = "linear-acceptance-override"
            acceptance_coordinates = json.dumps(
                {
                    "native_state_id": overridden.get("native_state_id"),
                    "reason": overridden.get("reason"),
                    "review_generation": overridden.get("review_generation"),
                    # PAT-ADR-0014: bind the exact diff the override was granted for.
                    **{key: overridden.get(key) for key in _AC_OVERRIDE_BOUND_KEYS},
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        priority = raw.get("priority", 0)
        if priority not in _LINEAR_TO_PRIORITY:
            raise LinearTrackerError("normalize", None, "invalid_response")
        comments = []
        if "comments" in raw:
            for comment in _connection(raw["comments"], "normalize.comments")[-10:]:
                if not isinstance(comment.get("body"), str):
                    raise LinearTrackerError("normalize", None, "invalid_response")
                comments.append(
                    {
                        "text": comment["body"],
                        "created": _epoch_ms(comment.get("createdAt")),
                    }
                )
        return Issue(
            id=raw["identifier"],
            title=raw["title"],
            state=(
                lifecycle["state"] if lifecycle["state"] is not None
                else native_state if projection_status == "native-only"
                else None
            ),
            priority=_LINEAR_TO_PRIORITY[priority],
            estimate=raw.get("estimate"),
            milestone=milestone,
            type=types[0] if types else None,
            labels=labels,
            ac_done=done,
            ac_total=total,
            links=links,
            pr_url=lifecycle["pr_url"],
            body=body,
            comments=comments,
            created=_epoch_ms(raw.get("createdAt")),
            updated=_epoch_ms(raw.get("updatedAt")),
            # Linear exposes no independent issue revision.  Its server-managed
            # update timestamp is the only fresh, provider-issued coordinate this
            # bounded (non-CAS) path may bind into a closure receipt.
            version=_epoch_ms(raw.get("updatedAt")),
            normalized_state=lifecycle["state"],
            native_state=native_state,
            projection_status=projection_status,
            acceptance_status=acceptance_status,
            acceptance_source=acceptance_source,
            acceptance_coordinates=acceptance_coordinates,
        )

    def search(self, project: Project, query: str = "") -> list[Issue]:
        if query:
            raise TrackerCapabilityUnavailableError(
                self.name, "provider-native-search-query"
            )
        binding = self._activate(project)
        out: list[Issue] = []
        cursor = None
        seen = set()
        for _ in range(_MAX_PAGES):
            data = self._graphql(
                _ISSUES_QUERY,
                {
                    "teamId": binding["team_id"],
                    "projectId": binding["project_id"],
                    "after": cursor,
                },
                "issue.search",
            )
            connection = data.get("issues")
            if not isinstance(connection, dict) or not isinstance(
                connection.get("nodes"), list
            ):
                raise LinearTrackerError("issue.search", None, "invalid_response")
            page = connection.get("pageInfo")
            if not isinstance(page, dict) or type(page.get("hasNextPage")) is not bool:
                raise LinearTrackerError("issue.search", None, "invalid_response")
            for raw in connection["nodes"]:
                # Backlog/graph queries must surface an authenticated lifecycle
                # disagreement instead of making the whole project unreadable.
                # Unknown or malformed proof chains remain explicit with
                # ``state=None`` and never become terminal authority.
                issue = self._to_issue(raw, project, observe_lifecycle=True)
                if issue.id in seen:
                    raise LinearTrackerError("issue.search", None, "pagination_stalled")
                seen.add(issue.id)
                out.append(issue)
            if not page["hasNextPage"]:
                return out
            cursor = page.get("endCursor")
            if not isinstance(cursor, str) or not cursor:
                raise LinearTrackerError("issue.search", None, "pagination_stalled")
        raise LinearTrackerError("issue.search", None, "pagination_limit")

    def _mapped_release(self, project: Project, release: str) -> tuple[dict, str]:
        binding = self._activate(project)
        release_id = binding["milestone_ids"].get(release)
        if release_id is None:
            raise ReleaseScopeUnavailableError(self.name, release, "unmapped")
        cursor = None
        seen: set[str] = set()
        match: dict | None = None
        for _ in range(_MAX_PAGES):
            data = self._graphql(
                _PROJECT_RELEASES_QUERY,
                {"id": binding["project_id"], "after": cursor},
                "release.read",
            )
            raw_project = data.get("project")
            if (
                not isinstance(raw_project, dict)
                or raw_project.get("id") != binding["project_id"]
            ):
                raise ReleaseScopeUnavailableError(
                    self.name, release, "project_unavailable"
                )
            connection = raw_project.get("projectMilestones")
            if not isinstance(connection, dict) or not isinstance(
                connection.get("nodes"), list
            ):
                raise ReleaseScopeUnavailableError(self.name, release, "invalid_response")
            page = connection.get("pageInfo")
            if not isinstance(page, dict) or type(page.get("hasNextPage")) is not bool:
                raise ReleaseScopeUnavailableError(self.name, release, "invalid_response")
            for row in connection["nodes"]:
                if (
                    not isinstance(row, dict)
                    or not isinstance(row.get("id"), str)
                    or not isinstance(row.get("name"), str)
                    or row["id"] in seen
                ):
                    raise ReleaseScopeUnavailableError(
                        self.name, release, "invalid_response"
                    )
                seen.add(row["id"])
                if row["id"] == release_id:
                    if match is not None:
                        raise ReleaseScopeUnavailableError(
                            self.name, release, "ambiguous_coordinate"
                        )
                    match = row
            if not page["hasNextPage"]:
                break
            cursor = page.get("endCursor")
            if not isinstance(cursor, str) or not cursor:
                raise ReleaseScopeUnavailableError(
                    self.name, release, "pagination_stalled"
                )
        else:
            raise ReleaseScopeUnavailableError(self.name, release, "pagination_limit")
        if match is None:
            raise ReleaseScopeUnavailableError(self.name, release, "absent")
        if match["name"] != release:
            raise ReleaseScopeUnavailableError(self.name, release, "mapping_mismatch")
        return binding, release_id

    def _release_issue(self, raw: dict, project: Project) -> ReleaseIssue:
        issue = self._to_issue(raw, project, observe_lifecycle=True)
        references: dict[str, object] = {"provider_issue_id": raw.get("id")}
        try:
            lifecycle = self._lifecycle_projection(
                issue.id, raw, allow_current_disagreement=True,
            )
        except TrackerConflictError:
            lifecycle = None
        if lifecycle is not None and lifecycle["state"] == "done":
            done = lifecycle.get("done") or {}
            references.update({
                key: done.get(key)
                for key in (
                    "pr_url", "head_sha", "base_sha", "merge_sha",
                    "review_digest", "review_generation",
                )
            })
            if issue.projection_status != "aligned":
                # A historical exact merge receipt remains useful evidence, but a
                # current native reopen is a conflicting fact. It cannot appear as
                # accepted current release scope until the disagreement is resolved.
                disposition = "unavailable"
            elif lifecycle.get("acceptance_override") is None:
                acceptance = lifecycle.get("acceptance_by_generation", {}).get(
                    done.get("review_generation")
                )
                if isinstance(acceptance, dict):
                    proof = acceptance.get("proof")
                    if isinstance(proof, dict):
                        references["acceptance_proof_id"] = proof.get("proof_id")
                disposition = "accepted"
            else:
                references["override_reason"] = lifecycle["acceptance_override"]
                disposition = "deviated"
        elif lifecycle is not None and lifecycle["state"] in {"in-progress", "review"}:
            disposition = "unfinished"
            if lifecycle.get("pr_url"):
                references["pr_url"] = lifecycle["pr_url"]
        elif str(issue.native_state or "").casefold() in {
            "done", "completed", "fixed", "dropped",
        }:
            disposition = "unavailable"
        else:
            disposition = "unfinished" if issue.projection_status != "unknown" else "unavailable"
        return ReleaseIssue(
            id=issue.id,
            title=issue.title,
            type=issue.type,
            state=issue.state or issue.native_state,
            labels=tuple(issue.labels),
            disposition=disposition,
            references={key: value for key, value in references.items() if value is not None},
        )

    def _complete_release_issue_connections(
        self, raw: dict, binding: dict, release_id: str, release: str,
    ) -> dict:
        """Exhaust nested issue facts before classifying a release member."""
        complete = dict(raw)
        issue_id = raw.get("id")
        if not isinstance(issue_id, str) or not issue_id:
            raise ReleaseScopeUnavailableError(self.name, release, "invalid_response")
        for field, fields in _RELEASE_NESTED_FIELDS.items():
            connection = raw.get(field)
            if not isinstance(connection, dict) or not isinstance(
                connection.get("nodes"), list,
            ):
                raise ReleaseScopeUnavailableError(self.name, release, "invalid_response")
            nodes = list(connection["nodes"])
            page = connection.get("pageInfo")
            cursors: set[str] = set()
            for _ in range(_MAX_PAGES):
                if not isinstance(page, dict) or type(page.get("hasNextPage")) is not bool:
                    raise ReleaseScopeUnavailableError(self.name, release, "invalid_response")
                if not page["hasNextPage"]:
                    complete[field] = {
                        "nodes": nodes,
                        "pageInfo": {"hasNextPage": False, "endCursor": page.get("endCursor")},
                    }
                    break
                cursor = page.get("endCursor")
                if not isinstance(cursor, str) or not cursor or cursor in cursors:
                    raise ReleaseScopeUnavailableError(self.name, release, "pagination_stalled")
                cursors.add(cursor)
                query = f"""
                query FoundryLinearReleaseNested($id: String!, $after: String) {{
                  issue(id: $id) {{
                    id identifier project {{ id }} team {{ id }} projectMilestone {{ id }}
                    {field}(first: 100, after: $after) {{
                      nodes {{ {fields} }} pageInfo {{ hasNextPage endCursor }}
                    }}
                  }}
                }}
                """
                data = self._graphql(
                    query, {"id": issue_id, "after": cursor},
                    f"release.{field}",
                )
                current = data.get("issue")
                if not isinstance(current, dict) or current.get("id") != issue_id:
                    raise ReleaseScopeUnavailableError(self.name, release, "invalid_response")
                if (
                    current.get("identifier") != raw.get("identifier")
                    or not isinstance(current.get("project"), dict)
                    or current["project"].get("id") != binding["project_id"]
                    or not isinstance(current.get("team"), dict)
                    or current["team"].get("id") != binding["team_id"]
                    or not isinstance(current.get("projectMilestone"), dict)
                    or current["projectMilestone"].get("id") != release_id
                ):
                    raise ReleaseScopeUnavailableError(self.name, release, "foreign_membership")
                connection = current.get(field)
                if not isinstance(connection, dict) or not isinstance(
                    connection.get("nodes"), list,
                ) or any(not isinstance(node, dict) for node in connection["nodes"]):
                    raise ReleaseScopeUnavailableError(self.name, release, "invalid_response")
                nodes.extend(connection["nodes"])
                page = connection.get("pageInfo")
            else:
                raise ReleaseScopeUnavailableError(self.name, release, "pagination_stalled")
        return complete

    def read_release_scope(self, project: Project, release: str) -> ReleaseScope:
        binding, release_id = self._mapped_release(project, release)
        cursor = None
        seen: set[str] = set()
        issues: list[ReleaseIssue] = []
        for _ in range(_MAX_PAGES):
            data = self._graphql(
                _RELEASE_ISSUES_QUERY,
                {
                    "teamId": binding["team_id"],
                    "projectId": binding["project_id"],
                    "milestoneId": release_id,
                    "after": cursor,
                },
                "release.issues",
            )
            connection = data.get("issues")
            if not isinstance(connection, dict) or not isinstance(
                connection.get("nodes"), list
            ):
                raise ReleaseScopeUnavailableError(self.name, release, "invalid_response")
            page = connection.get("pageInfo")
            if not isinstance(page, dict) or type(page.get("hasNextPage")) is not bool:
                raise ReleaseScopeUnavailableError(self.name, release, "invalid_response")
            for raw in connection["nodes"]:
                native_project = raw.get("project") if isinstance(raw, dict) else None
                native_team = raw.get("team") if isinstance(raw, dict) else None
                native_milestone = (
                    raw.get("projectMilestone") if isinstance(raw, dict) else None
                )
                if (
                    not isinstance(native_project, dict)
                    or native_project.get("id") != binding["project_id"]
                    or not isinstance(native_team, dict)
                    or native_team.get("id") != binding["team_id"]
                    or not isinstance(native_milestone, dict)
                    or native_milestone.get("id") != release_id
                ):
                    raise ReleaseScopeUnavailableError(
                        self.name, release, "foreign_membership"
                    )
                complete = self._complete_release_issue_connections(
                    raw, binding, release_id, release,
                )
                item = self._release_issue(complete, project)
                if item.id in seen:
                    raise ReleaseScopeUnavailableError(
                        self.name, release, "pagination_stalled"
                    )
                seen.add(item.id)
                issues.append(item)
            if not page["hasNextPage"]:
                break
            cursor = page.get("endCursor")
            if not isinstance(cursor, str) or not cursor:
                raise ReleaseScopeUnavailableError(
                    self.name, release, "pagination_stalled"
                )
        else:
            raise ReleaseScopeUnavailableError(self.name, release, "pagination_limit")
        return ReleaseScope(
            provider=self.name,
            project_key=project.key,
            project_id=project.id,
            release=release,
            release_id=release_id,
            native_state=None,
            issues=tuple(issues),
            closure={
                "mode": "operator",
                "native_capability": "unavailable",
                "action": "record-scope-freeze",
                "native_mutation": False,
                "preconditions": ["unfinished=0", "unavailable=0"],
                "verification": "read-release-scope",
            },
            coordinates={
                "project_id": binding["project_id"],
                "team_id": binding["team_id"],
                "project_milestone_id": release_id,
            },
        )

    def get_issue(self, issue_id: str) -> Issue:
        project = self._project()
        return self._to_issue(self._read_raw(issue_id), project)

    def observe_issue(self, issue_id: str) -> Issue:
        project = self._project()
        return self._to_issue(
            self._read_raw(issue_id), project, observe_lifecycle=True,
        )

    def recover_done_projection(
        self,
        issue_id: str,
        *,
        pr_url: str,
        head_sha: str,
        base_sha: str,
        merge_sha: str,
        project: Project | None = None,
    ) -> bool:
        """Use one authentic done receipt to repair only its missing State."""
        if project is None:
            return False
        raw = self._read_raw(issue_id)
        binding = self._activate(project)
        self._assert_issue_project(raw, binding)
        target_state_id = binding["state_ids"]["done"]
        native = raw.get("state")
        receipts = []
        for row in _connection(raw.get("comments"), "lifecycle.recover"):
            decoded = self._decode_lifecycle_comment(issue_id, row.get("body"))
            if decoded is None or decoded[0] != "state-done":
                continue
            payload = decoded[1]
            if (
                payload.get("state") == "done"
                and payload.get("pr_url") == pr_url
                and payload.get("head_sha") == head_sha
                and payload.get("base_sha") == base_sha
                and payload.get("merge_sha") == merge_sha
            ):
                receipts.append(payload)
        if len(receipts) != 1:
            return False
        payload = receipts[0]
        # This validates the full chain and every receipt coordinate before the
        # private review digest can authorize the targeted native projection.
        self._lifecycle_projection(
            issue_id, {**raw, "state": {**native, "id": target_state_id}},
        )
        if isinstance(native, dict) and native.get("id") == target_state_id:
            self._lifecycle_projection(issue_id, raw)
            return True
        self.set_state(
            issue_id,
            "done",
            context=TransitionContext(
                pr_url=pr_url, head_sha=head_sha, base_sha=base_sha,
                review_digest=payload["review_digest"], merge_sha=merge_sha,
            ),
            project=project,
        )
        return True

    # ---- writes with provider-demonstrable no-overwrite semantics --
    @staticmethod
    def _field_names(fields: dict) -> None:
        if not isinstance(fields, dict) or not fields:
            raise ValueError("Linear fields must be a non-empty object")
        supported = {"State", "Priority", "Estimate", "Milestone", "Type", "Labels"}
        unknown = sorted(set(fields) - supported)
        if unknown:
            raise TrackerCapabilityUnavailableError("linear", f"field:{unknown[0]}")

    def _desired_update(
        self, raw: dict, project: Project, fields: dict
    ) -> tuple[dict, dict]:
        self._field_names(fields)
        binding = self._binding(project)
        values: dict = {}
        expected: dict = {}
        if "State" in fields:
            state = fields["State"]
            if state not in binding["state_ids"]:
                raise LinearBindingError("state_unmapped")
            values["stateId"] = binding["state_ids"][state]
            expected["state_id"] = values["stateId"]
        if "Priority" in fields:
            priority = fields["Priority"]
            if priority not in _PRIORITY_TO_LINEAR:
                raise ValueError("Linear priority must be P0, P1, P2, P3, or null")
            values["priority"] = _PRIORITY_TO_LINEAR[priority]
            expected["priority"] = values["priority"]
        if "Estimate" in fields:
            estimate = fields["Estimate"]
            if estimate is not None and (type(estimate) is not int or estimate < 0):
                raise ValueError(
                    "Linear estimate must be a non-negative integer or null"
                )
            values["estimate"] = estimate
            expected["estimate"] = estimate
        if "Milestone" in fields:
            milestone = fields["Milestone"]
            if milestone is None:
                milestone_id = None
            else:
                milestone_id = binding["milestone_ids"].get(milestone)
                if milestone_id is None:
                    raise LinearBindingError("milestone_unmapped")
            values["projectMilestoneId"] = milestone_id
            expected["milestone_id"] = milestone_id

        labels = _connection(raw.get("labels"), "issue.update.labels")
        current_ids = {
            node.get("id") for node in labels if isinstance(node.get("id"), str)
        }
        type_ids = set(binding["type_label_ids"].values())
        desired_ids = set(current_ids)
        if "Labels" in fields:
            requested = fields["Labels"]
            if not isinstance(requested, list) or any(
                not isinstance(item, str) for item in requested
            ):
                raise ValueError("Linear Labels must be a list of configured names")
            missing = [name for name in requested if name not in binding["label_ids"]]
            if missing:
                raise LinearBindingError("label_unmapped")
            desired_ids = {binding["label_ids"][name] for name in requested} | (
                current_ids & type_ids
            )
        if "Type" in fields:
            issue_type = fields["Type"]
            if issue_type is None:
                type_id = None
            else:
                type_id = binding["type_label_ids"].get(issue_type)
                if type_id is None:
                    raise LinearBindingError("type_unmapped")
            desired_ids -= type_ids
            if type_id is not None:
                desired_ids.add(type_id)
        if "Labels" in fields or "Type" in fields:
            # IssueUpdate exposes additive/removal label inputs.  Do not submit a
            # whole label replacement: labels outside this normalized operation must
            # survive, including provider-side labels that change after S1.
            added = desired_ids - current_ids
            removed = current_ids - desired_ids
            if added:
                values["addedLabelIds"] = sorted(added)
            if removed:
                values["removedLabelIds"] = sorted(removed)
            expected["label_ids"] = desired_ids
        return values, expected

    @staticmethod
    def _raw_matches(raw: dict, expected: dict) -> bool:
        if "body" in expected and (raw.get("description") or "") != expected["body"]:
            return False
        if (
            "parent_id" in expected
            and (raw.get("parent") or {}).get("id") != expected["parent_id"]
        ):
            return False
        if (
            "state_id" in expected
            and (raw.get("state") or {}).get("id") != expected["state_id"]
        ):
            return False
        if "priority" in expected and raw.get("priority") != expected["priority"]:
            return False
        if "estimate" in expected and raw.get("estimate") != expected["estimate"]:
            return False
        if "milestone_id" in expected:
            actual = (raw.get("projectMilestone") or {}).get("id")
            if actual != expected["milestone_id"]:
                return False
        if "label_ids" in expected:
            actual_ids = {
                node.get("id")
                for node in _connection(raw.get("labels"), "issue.readback.labels")
            }
            if actual_ids != expected["label_ids"]:
                return False
        return True

    @staticmethod
    def _field_snapshot(raw: dict, fields: dict) -> dict:
        """Return only the provider properties a grooming write may change."""
        snapshot: dict = {}
        if "State" in fields:
            snapshot["state_id"] = (raw.get("state") or {}).get("id")
        if "Priority" in fields:
            snapshot["priority"] = raw.get("priority")
        if "Estimate" in fields:
            snapshot["estimate"] = raw.get("estimate")
        if "Milestone" in fields:
            snapshot["milestone_id"] = (raw.get("projectMilestone") or {}).get("id")
        if "Labels" in fields or "Type" in fields:
            snapshot["label_ids"] = {
                node.get("id")
                for node in _connection(raw.get("labels"), "issue.snapshot.labels")
            }
        return snapshot

    def create_issue(
        self,
        project: Project,
        title: str,
        body: str,
        fields: dict | None = None,
        parent: str | None = None,
    ) -> Issue:
        binding = self._activate(project)
        if not isinstance(title, str) or not title or not isinstance(body, str):
            raise ValueError("Linear issue title/body invalid")
        fields = dict(fields or {})
        if "GitHub PR" in fields:
            raise TrackerCapabilityUnavailableError(self.name, "github-pr-projection")
        skeleton = {
            "labels": {
                "nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
        }
        }
        values, expected = (
            self._desired_update(skeleton, project, fields) if fields else ({}, {})
        )
        # Creation owns the complete new record.  The delta inputs are reserved for
        # existing issues, where replacing all labels would risk foreign loss.
        if "label_ids" in expected:
            values.pop("addedLabelIds", None)
            values.pop("removedLabelIds", None)
            values["labelIds"] = sorted(expected["label_ids"])
        input_value = {
            "id": str(uuid.uuid4()),
            "teamId": binding["team_id"],
            "projectId": binding["project_id"],
            "title": title,
            "description": body,
            **values,
        }
        if parent:
            parent_raw = self._read_raw(parent)
            self._assert_issue_project(parent_raw, binding)
            input_value["parentId"] = parent_raw["id"]
        data = self._graphql(_ISSUE_CREATE, {"input": input_value}, "issue.create")
        payload = self._mutation_payload(data, "issueCreate", "issue.create")
        created = payload.get("issue")
        if not isinstance(created, dict) or not isinstance(
            created.get("identifier"), str
        ):
            raise LinearTrackerError("issue.create", None, "invalid_response")
        raw = self._read_raw(created["identifier"])
        self._assert_issue_project(raw, binding)
        if (
            raw.get("title") != title
            or (raw.get("description") or "") != body
            or not self._raw_matches(raw, expected)
        ):
            raise TrackerConflictError("Linear issue divergent after create; no retry")
        if parent and (raw.get("parent") or {}).get("id") != input_value["parentId"]:
            raise TrackerConflictError("Linear parent divergent after create; no retry")
        return self._to_issue(raw, project)

    def update_fields(
        self,
        issue_id: str,
        fields: dict,
        project: Project | None = None,
    ) -> Issue:
        if project is None:
            raise LinearBindingError("mutation_project_required")
        binding = self._activate(project)
        if isinstance(fields, dict) and "GitHub PR" in fields:
            raise TrackerCapabilityUnavailableError(self.name, "github-pr-projection")
        raw = self._read_raw(issue_id)
        self._assert_issue_project(raw, binding)
        values, expected = self._desired_update(raw, project, fields)
        if self._raw_matches(raw, expected):
            return self._to_issue(raw, project)
        expected_snapshot = self._field_snapshot(raw, fields)
        fresh = self._read_raw(issue_id)
        self._assert_issue_project(fresh, binding)
        if not self._raw_matches(fresh, expected_snapshot):
            raise TrackerConflictError("Linear issue changed before bounded write")
        updated = self._bounded_issue_update(
            issue_id, fresh, binding, values, expected, "issue.update.fields"
        )
        return self._to_issue(updated, project)

    def _bounded_issue_update(
        self,
        issue_id: str,
        raw: dict,
        binding: dict,
        values: dict,
        expected: dict,
        operation: str,
    ) -> dict:
        """Perform the PAT-ADR-0006 S1-S5 path for one existing Linear issue."""
        if not isinstance(raw.get("id"), str) or not raw["id"]:
            raise LinearTrackerError(operation, None, "invalid_response")
        try:
            data = self._graphql(
                _ISSUE_UPDATE, {"id": raw["id"], "input": values}, operation,
            )
            payload = self._mutation_payload(data, "issueUpdate", operation)
            issue = payload.get("issue")
            if (
                not isinstance(issue, dict)
                or issue.get("id") != raw["id"]
                or issue.get("identifier") != issue_id
            ):
                raise LinearTrackerError(operation, None, "invalid_response")
        except LinearTrackerError as error:
            # A transport loss may have followed a committed provider mutation.
            # Recovery is observational only and never repeats the mutation.
            recovered = self._read_raw(issue_id)
            self._assert_issue_project(recovered, binding)
            if self._raw_matches(recovered, expected):
                return recovered
            raise error
        readback = self._read_raw(issue_id)
        self._assert_issue_project(readback, binding)
        if not self._raw_matches(readback, expected):
            raise TrackerConflictError("Linear issue divergent after bounded write")
        return readback

    def _project_native_state(
        self, issue_id: str, state: str, project: Project, binding: dict,
    ) -> dict:
        """Project only State through S1-S5 after its durable receipt exists."""
        raw = self._read_raw(issue_id)
        self._assert_issue_project(raw, binding)
        values, expected = self._desired_update(raw, project, {"State": state})
        if self._raw_matches(raw, expected):
            return raw
        snapshot = self._field_snapshot(raw, {"State": state})
        fresh = self._read_raw(issue_id)
        self._assert_issue_project(fresh, binding)
        if not self._raw_matches(fresh, snapshot):
            raise TrackerConflictError("Linear issue changed before bounded write")
        return self._bounded_issue_update(
            issue_id, fresh, binding, values, expected,
            "issue.update.lifecycle-state",
        )

    def set_state(
        self,
        issue_id: str,
        state: str,
        context=None,
        project: Project | None = None,
    ) -> None:
        if project is None:
            raise LinearBindingError("mutation_project_required")
        if state not in {"in-progress", "review", "done"}:
            raise TrackerCapabilityUnavailableError(
                self.name, "existing-issue-field-replacement"
            )
        payload = {"state": state}
        if state in {"review", "done"}:
            if not isinstance(context, TransitionContext) or not all(
                [
                    context.pr_url,
                    context.head_sha,
                    context.base_sha,
                    context.review_digest,
                ]
            ):
                raise TrackerCapabilityUnavailableError(self.name, "lifecycle-proof")
            payload.update(
                {
                    "pr_url": context.pr_url,
                    "head_sha": context.head_sha,
                    "base_sha": context.base_sha,
                    "review_digest": context.review_digest,
                }
            )
            if state == "done":
                if not context.merge_sha:
                    raise TrackerCapabilityUnavailableError(self.name, "merge-proof")
                payload["merge_sha"] = context.merge_sha
        # The typed lifecycle receipt is Foundry's authority, while the native
        # Linear State remains the human-visible projection.  Write the receipt
        # first so a native transport failure cannot erase delivery evidence.
        # The intentional receipt/native interval is observable as disagreement
        # and the exact receipt authorizes recovery of only that missing State
        # effect.  This deliberately leaves a named residual S1->S2 race: it is
        # bounded detection, not CAS.
        #
        # Read the receipt graph first.  In particular, never replay a late
        # in-progress projection after a durable review: that historical retry
        # is a no-op and must not regress the native state.
        binding = self._activate(project)
        raw = self._read_raw(issue_id)
        self._assert_issue_project(raw, binding)
        projection = self._lifecycle_projection(
            issue_id, raw, pending_operation="state-" + state,
        )
        ranks = {"in-progress": 1, "review": 2, "done": 3}
        # Recovery also handles the inverse interruption order used by historic
        # lifecycle writers: an exact, integrity-checked Foundry receipt exists
        # but its matching native State projection did not.  The receipt remains
        # append-only; we apply only that missing native effect.  A bare native
        # terminal state never enters this branch and remains fail-closed below.
        target_state_id = binding["state_ids"][state]
        native = raw.get("state")
        native_state_id = native.get("id") if isinstance(native, dict) else None
        exact_receipts = []
        for row in _connection(raw.get("comments"), "lifecycle.comments"):
            decoded = self._decode_lifecycle_comment(issue_id, row.get("body"))
            if decoded is None or decoded[0] != "state-" + state:
                continue
            receipt = decoded[1]
            if all(receipt.get(key) == value for key, value in payload.items()):
                exact_receipts.append(receipt)
        if len(exact_receipts) > 1:
            raise TrackerConflictError("Linear lifecycle duplicate projection")
        if (
            projection["state"] in ranks
            and ranks[projection["state"]] > ranks[state]
        ):
            if len(exact_receipts) != 1:
                raise TrackerConflictError(
                    "Linear weaker transition lacks an exact historical receipt"
                )
            # Only an authenticated replay of this exact operation can converge
            # below a stronger durable state. New coordinates are a conflicting
            # intention, never a successful no-op or authority to regress State.
            return
        if native_state_id != target_state_id and len(exact_receipts) == 1:
            # Authenticate the durable receipt, including its review/merge and
            # generation bindings, before it authorizes recovery of the native
            # projection.  A marker with a valid hash but invalid lifecycle
            # content is still not positive proof.
            latest_for_state = {
                "in-progress": projection["in_progress"],
                "review": projection["latest_review"],
                "done": projection["done"],
            }[state]
            if exact_receipts[0] != latest_for_state:
                # The requested receipt is authentic but historical (for
                # example an older review generation).  It cannot project a
                # weaker native state; preserve both provider surfaces.
                return
            self._project_native_state(issue_id, state, project, binding)
            repaired = self._read_raw(issue_id)
            self._assert_issue_project(repaired, binding)
            self._lifecycle_projection(issue_id, repaired)
            return
        self._project_lifecycle(
            issue_id,
            "state-" + state,
            payload,
            project,
            native_state_id=target_state_id,
        )
        self._project_native_state(issue_id, state, project, binding)
        repaired = self._read_raw(issue_id)
        self._assert_issue_project(repaired, binding)
        self._lifecycle_projection(issue_id, repaired)

    def project_acceptance_proof(
        self,
        issue_id: str,
        expected_body: str,
        proof: dict,
        *,
        checked: int,
        project: Project | None = None,
    ) -> bool:
        if (
            project is None
            or not isinstance(proof, dict)
            or not isinstance(proof.get("proof_id"), str)
        ):
            raise AcceptanceSyncUnavailableError("preuve AC Linear invalide")
        self._activate(project)
        raw = self._read_raw(issue_id)
        self._assert_issue_project(raw, self._binding(project))
        lifecycle = self._lifecycle_projection(
            issue_id,
            raw,
            pending_operation="acceptance",
        )
        body = raw.get("description") or ""
        if body != expected_body:
            raise TrackerConflictError(
                "Linear acceptance body changed before projection"
            )
        review = lifecycle["latest_review"]
        if review is None:
            raise TrackerConflictError(
                "Linear acceptance proof lacks one review projection"
            )
        state = raw.get("state")
        native_state_id = state.get("id") if isinstance(state, dict) else None
        projected = {
            "proof": json.loads(json.dumps(proof)),
            "body_digest": hashlib.sha256(expected_body.encode()).hexdigest(),
            "checked": checked,
            "review_generation": lifecycle["review_generation"],
        }
        self._validate_acceptance_projection(
            issue_id,
            expected_body,
            {**projected, "native_state_id": native_state_id},
            review,
        )
        return self._project_lifecycle(
            issue_id,
            "acceptance",
            projected,
            project,
        )

    @staticmethod
    def _acceptance_override_reason(reason: object) -> str:
        if not isinstance(reason, str) or _AC_OVERRIDE_REASON.fullmatch(reason) is None:
            raise TrackerCapabilityUnavailableError("linear", "acceptance-override-reason")
        return reason

    def project_acceptance_override(
        self,
        issue_id: str,
        reason: str,
        context: TransitionContext,
        project: Project | None = None,
    ) -> bool:
        """Append the typed human AC override for the exact current review generation.

        This is written before the code-host merge. It never checks an AC box and never
        counts as acceptance; it only lets a later ``state-done`` receipt of the same
        generation be read as "done under an explicit, audited AC override".
        """
        if project is None:
            raise LinearBindingError("mutation_project_required")
        reason = self._acceptance_override_reason(reason)
        if (
            not isinstance(context, TransitionContext)
            or context.merge_sha is not None
            or not all(getattr(context, key) for key in _AC_OVERRIDE_BOUND_KEYS)
        ):
            raise TrackerCapabilityUnavailableError(self.name, "lifecycle-proof")
        self._activate(project)
        raw = self._read_raw(issue_id)
        self._assert_issue_project(raw, self._binding(project))
        lifecycle = self._lifecycle_projection(
            issue_id,
            raw,
            pending_operation="acceptance-override",
        )
        review = lifecycle["latest_review"]
        if (
            lifecycle["state"] != "review"
            or review is None
            or any(review[key] != getattr(context, key) for key in _AC_OVERRIDE_BOUND_KEYS)
        ):
            raise TrackerConflictError(
                "Linear acceptance override lacks the current review projection"
            )
        payload = {
            "review_generation": review["generation"],
            "reason": reason,
            **{key: review[key] for key in _AC_OVERRIDE_BOUND_KEYS},
        }
        return self._project_lifecycle(issue_id, "acceptance-override", payload, project)

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
        """Append only the missing override of an issue already merged under override.

        Bounded recovery for a ``state-done`` receipt that has neither an acceptance
        nor an override receipt for its review generation (receipts written before
        the typed override existed). The raw issue is read through the recovery-only
        projection mode; the receipt is bound to the done receipt's own generation and
        coordinates, which must match the exact merged PR. Nothing is merged, deleted
        or rewritten, and the readback is validated by the ordinary strict projection.
        """
        if project is None:
            raise LinearBindingError("mutation_project_required")
        reason = self._acceptance_override_reason(reason)
        self._activate(project)
        raw = self._read_raw(issue_id)
        self._assert_issue_project(raw, self._binding(project))
        lifecycle = self._lifecycle_projection(
            issue_id,
            raw,
            pending_operation="acceptance-override",
        )
        done = lifecycle["done"]
        state = raw.get("state")
        native_state_id = state.get("id") if isinstance(state, dict) else None
        if (
            done is None
            or done["pr_url"] != pr_url
            or done["head_sha"] != head_sha
            or done["base_sha"] != base_sha
            or done["merge_sha"] != merge_sha
            or native_state_id != done["native_state_id"]
        ):
            raise TrackerConflictError(
                "Linear acceptance override recovery lacks the exact done receipt"
            )
        if lifecycle["acceptance_complete"]:
            raise TrackerConflictError(
                "Linear acceptance override recovery not applicable"
            )
        payload = {
            "review_generation": done["review_generation"],
            "reason": reason,
            **{key: done[key] for key in _AC_OVERRIDE_BOUND_KEYS},
        }
        created = self._project_lifecycle(
            issue_id, "acceptance-override", payload, project,
        )
        # A historic done receipt can have recorded the preceding native review
        # coordinate.  Once its missing override is durable, use that exact
        # receipt to repair only State; no lifecycle row is rewritten or added.
        self.set_state(
            issue_id,
            "done",
            context=TransitionContext(
                pr_url=done["pr_url"], head_sha=done["head_sha"],
                base_sha=done["base_sha"], review_digest=done["review_digest"],
                merge_sha=done["merge_sha"],
            ),
            project=project,
        )
        return created

    def project_cockpit_evidence(
        self,
        issue_id: str,
        envelope: dict,
        *,
        repository: str,
        ac_digest: str,
        pr,
        diff_hash: str,
        project: Project | None = None,
    ) -> bool:
        """Append one complete advisory cockpit envelope without lifecycle authority."""
        if project is None:
            raise LinearBindingError("mutation_project_required")
        from foundry.evidence_plane import verify_evidence_envelope

        verdict = verify_evidence_envelope(
            envelope,
            repository=repository,
            issue_id=issue_id,
            ac_digest=ac_digest,
            pr=pr,
            diff_hash=diff_hash,
        )
        if verdict.get("decision") != "GO":
            raise TrackerCapabilityUnavailableError(
                self.name,
                "cockpit-evidence-complete-go",
            )
        if not isinstance(envelope, dict):
            raise TrackerCapabilityUnavailableError(
                self.name,
                "cockpit-evidence-complete-go",
            )
        coordinates = envelope.get("coordinates")
        evidence = envelope.get("evidence")
        ci = evidence.get("ci") if isinstance(evidence, dict) else None
        review = evidence.get("review") if isinstance(evidence, dict) else None
        tests = evidence.get("tests") if isinstance(evidence, dict) else None
        if not all(
            isinstance(value, dict)
            for value in (
                coordinates,
                ci,
                review,
                tests,
            )
        ):
            raise TrackerCapabilityUnavailableError(
                self.name,
                "cockpit-evidence-complete-go",
            )
        payload = {
            "envelope_id": envelope.get("envelope_id"),
            "repository": coordinates.get("repository"),
            "issue": coordinates.get("issue"),
            "ac_digest": coordinates.get("ac_digest"),
            "pr_number": coordinates.get("pr_number"),
            "base_sha": coordinates.get("base_sha"),
            "head_sha": coordinates.get("head_sha"),
            "diff_hash": coordinates.get("diff_hash"),
            "review_proof_id": review.get("proof_id"),
            "test_receipt_id": tests.get("receipt_id"),
            "check_runs_receipt_id": (ci.get("check_runs") or {}).get("receipt_id"),
            "commit_statuses_receipt_id": (ci.get("commit_statuses") or {}).get(
                "receipt_id"
            ),
        }
        if _DIGEST.fullmatch(str(payload["envelope_id"])) is None or any(
            value is None for value in payload.values()
        ):
            raise TrackerCapabilityUnavailableError(
                self.name,
                "cockpit-evidence-complete-go",
            )
        return self._project_lifecycle(
            issue_id,
            "cockpit-evidence",
            payload,
            project,
        )

    def link(
        self,
        src_id: str,
        link_type: str,
        dst_id: str,
        project: Project | None = None,
    ) -> None:
        if project is None:
            raise LinearBindingError("mutation_project_required")
        binding = self._activate(project)
        if link_type not in {
            "subtask-of",
            "parent-of",
            "depends-on",
            "blocks",
            "relates",
        }:
            raise TrackerCapabilityUnavailableError(self.name, f"link:{link_type}")
        src_raw, dst_raw = self._read_raw(src_id), self._read_raw(dst_id)
        self._assert_issue_project(src_raw, binding)
        self._assert_issue_project(dst_raw, binding)
        if link_type in {"subtask-of", "parent-of"}:
            child, parent = (
                (src_raw, dst_raw)
                if link_type == "subtask-of"
                else (dst_raw, src_raw)
            )
            current_parent = (child.get("parent") or {}).get("id")
            if current_parent == parent["id"]:
                return
            fresh_child = self._read_raw(child["identifier"])
            fresh_parent = self._read_raw(parent["identifier"])
            self._assert_issue_project(fresh_child, binding)
            self._assert_issue_project(fresh_parent, binding)
            if (
                (fresh_child.get("parent") or {}).get("id") != current_parent
                or fresh_parent.get("id") != parent["id"]
            ):
                raise TrackerConflictError("Linear parent changed before bounded write")
            self._bounded_issue_update(
                fresh_child["identifier"],
                fresh_child,
                binding,
                {"parentId": fresh_parent["id"]},
                {"parent_id": fresh_parent["id"]},
                "issue.update.parent",
            )
            return
        if any(
            link.type == link_type and link.target == dst_id
            for link in self._to_issue(src_raw, project).links
        ):
            return
        source, target, relation = src_raw, dst_raw, "related"
        if link_type == "blocks":
            relation = "blocks"
        elif link_type == "depends-on":
            source, target, relation = dst_raw, src_raw, "blocks"
        elif source["id"] > target["id"]:
            # Linear's `related` relation is symmetric.  Bind both its deterministic
            # identity and native payload to one endpoint order so inverse replays
            # converge even when endpoint projections are temporarily hidden.
            source, target = target, source
        source_links = self._to_issue(source, project).links
        target_links = self._to_issue(target, project).links
        fresh_source = self._read_raw(source["identifier"])
        fresh_target = self._read_raw(target["identifier"])
        self._assert_issue_project(fresh_source, binding)
        self._assert_issue_project(fresh_target, binding)
        if (
            self._to_issue(fresh_source, project).links != source_links
            or self._to_issue(fresh_target, project).links != target_links
        ):
            raise TrackerConflictError("Linear relations changed before bounded write")
        relation_id = _issue_relation_id(
            binding["project_id"], relation, fresh_source["id"], fresh_target["id"],
        )
        try:
            data = self._graphql(
                _RELATION_CREATE,
                {"input": {
                    "id": relation_id,
                    "issueId": fresh_source["id"],
                    "relatedIssueId": fresh_target["id"],
                    "type": relation,
                }},
                "issue.relation.create",
            )
            payload = self._mutation_payload(
                data, "issueRelationCreate", "issue.relation.create",
            )
            created = payload.get("issueRelation")
            if not self._issue_relation_matches(
                created,
                relation_id=relation_id,
                relation_type=relation,
                issue_id=fresh_source["id"],
                related_issue_id=fresh_target["id"],
            ):
                raise LinearTrackerError(
                    "issue.relation.create", None, "invalid_response",
                )
        except LinearTrackerError as error:
            try:
                recovered = self._read_issue_relation(
                    relation_id, "issue.relation.recover",
                )
            except LinearTrackerError:
                # The singular query is non-null, so its GraphQL error cannot prove
                # absence or authorize another write. Preserve the original failed
                # create and leave this invocation fail-closed.
                raise error from None
            if self._issue_relation_matches(
                recovered,
                relation_id=relation_id,
                relation_type=relation,
                issue_id=fresh_source["id"],
                related_issue_id=fresh_target["id"],
            ):
                return
            raise TrackerConflictError(
                "Linear relation deterministic id is occupied by another relation"
            )
        readback = self._read_issue_relation(
            relation_id, "issue.relation.readback",
        )
        if not self._issue_relation_matches(
            readback,
            relation_id=relation_id,
            relation_type=relation,
            issue_id=fresh_source["id"],
            related_issue_id=fresh_target["id"],
        ):
            raise TrackerConflictError(
                "Linear relation deterministic id is occupied by another relation"
            )

    @staticmethod
    def _epic_closure_audit(receipt: EpicClosureReceipt) -> tuple[str, str, str]:
        value = {"schema": "foundry-epic-closure.v1", "receipt": receipt.to_dict()}
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        audit_id = f"linear:epic:{digest}"
        return audit_id, (
            "Foundry Epic closure audit (append-only).\n"
            f"marker: foundry-epic-closure.v1:{digest}\ncoordinates: {canonical}"
        ), str(uuid.UUID(digest[:32], version=4))

    @classmethod
    def _closure_from_issue(
        cls, issue: Issue, project: Project, *, require_done: bool = True,
    ) -> EpicClosureOutcome | None:
        matches = []
        for row in issue.comments:
            text = row.get("text") if isinstance(row, dict) else None
            if not isinstance(text, str) or not text.startswith("Foundry Epic closure audit (append-only)."):
                continue
            lines = text.splitlines()
            try:
                raw = json.loads(lines[2].removeprefix("coordinates: "))["receipt"]
                dependencies = tuple(
                    EpicClosureDependency(
                        source_id=item["source_id"],
                        target=EpicClosureChild(**item["target"]),
                    )
                    for item in raw.get("dependencies", ())
                )
                receipt = EpicClosureReceipt(**{
                    **raw,
                    "children": tuple(EpicClosureChild(**x) for x in raw["children"]),
                    "dependencies": dependencies,
                    "accepted_overrides": tuple(
                        EpicClosureOverride(**x)
                        for x in raw.get("accepted_overrides", ())
                    ),
                })
            except (IndexError, KeyError, TypeError, ValueError):
                raise TrackerConflictError("audit de clôture Linear malformé") from None
            audit_id, expected, _comment_id = cls._epic_closure_audit(receipt)
            if len(lines) != 3 or text != expected or lines[1] != f"marker: foundry-epic-closure.v1:{audit_id.removeprefix('linear:epic:')}":
                raise TrackerConflictError("audit de clôture Linear divergent")
            matches.append((receipt, audit_id))
        if len(matches) > 1:
            raise TrackerConflictError("audit de clôture Linear dupliqué")
        if not matches:
            return None
        receipt, audit_id = matches[0]
        if type(receipt.parent_version) is not int or receipt.parent_version <= 0:
            raise TrackerConflictError("audit de clôture Linear version invalide")
        if (
            receipt.project_key != project.key
            or receipt.project_id != project.id
            or receipt.parent_id != issue.id
        ):
            raise TrackerConflictError("audit de clôture Linear hors coordonnées")
        from foundry.write import epic_parent_validation_digest
        if receipt.parent_validation_digest != epic_parent_validation_digest(issue):
            raise TrackerConflictError("Epic Linear modifié depuis le verdict humain")
        if require_done and (
            issue.state != "done"
            or issue.version is None
            or issue.version <= receipt.parent_version
        ):
            raise TrackerConflictError("audit de clôture Linear sans parent clôturé")
        return EpicClosureOutcome(
            receipt,
            issue.version if require_done else receipt.parent_version + 1,
            audit_id,
            replayed=True,
        )

    def _closure_from_raw(
        self, raw: dict, project: Project, *, require_done: bool = True,
    ) -> EpicClosureOutcome | None:
        """Read the Epic audit without treating it as a code-issue lifecycle proof."""
        identifier = raw.get("identifier")
        state = raw.get("state")
        if not isinstance(identifier, str) or not isinstance(state, dict):
            raise LinearTrackerError("epic-closure.readback", None, "invalid_response")
        issue = self._to_issue(
            raw, project, observe_lifecycle=True, project_epic_closure=False,
        )
        issue.comments = [
            {"text": row.get("body")}
            for row in _connection(raw.get("comments"), "epic-closure.comments")
        ]
        issue.state = (
            "done" if state.get("id") == self._binding(project)["state_ids"]["done"]
            else issue.state
        )
        native_done = state.get("id") == self._binding(project)["state_ids"]["done"]
        outcome = self._closure_from_issue(
            issue, project, require_done=require_done and native_done,
        )
        if outcome is not None:
            if not native_done and (
                issue.native_state != outcome.receipt.parent_state
                or issue.version is None
                or issue.version < outcome.receipt.parent_version
            ):
                raise TrackerConflictError(
                    "audit pending Linear séparé de son prédécesseur original"
                )
            for row in _connection(raw.get("comments"), "epic-closure.comments"):
                if isinstance(row.get("body"), str) and row["body"].startswith(
                    "Foundry Epic closure audit (append-only)."
                ):
                    if row.get("id") != self._epic_closure_audit(outcome.receipt)[2]:
                        raise TrackerConflictError("audit de clôture Linear id invalide")
            from foundry.write import _validate_epic_parent, _validate_epic_outcome
            try:
                _validate_epic_parent(issue, bounded=True)
                if outcome.receipt.human_verdict != "accepted":
                    raise SystemExit("verdict Epic absent ou invalide")
                _validate_epic_outcome(
                    outcome, project=project, parent=issue, expected=None,
                )
            except (SystemExit, AttributeError, TypeError, ValueError) as exc:
                raise TrackerConflictError("audit de clôture Linear invalide") from exc
            if require_done and not native_done:
                raise TrackerConflictError("audit de clôture Linear sans parent clôturé")
        return outcome

    def get_epic_closure(self, project: Project, parent_id: str) -> EpicClosureOutcome | None:
        self.validate_issue_binding(project, parent_id)
        binding = self._activate(project)
        raw = self._read_raw(parent_id)
        self._assert_issue_project(raw, binding)
        return self._verified_epic_closure(raw, project)

    def _verified_epic_closure(
        self, raw: dict, project: Project,
    ) -> EpicClosureOutcome | None:
        """Read-only terminal authority shared by ordinary reads and Epic replay."""
        # A malformed child/parent cycle must not recurse through normalization.
        active = getattr(self._epic_closure_reads, "active", set())
        parent_id = raw.get("identifier")
        if parent_id in active:
            raise TrackerConflictError("graphe Epic Linear cyclique")
        self._epic_closure_reads.active = active
        active.add(parent_id)
        try:
            outcome = self._closure_from_raw(raw, project)
            if outcome is None:
                return None
            from foundry.write import bounded_epic_graph_snapshot
            try:
                parent = self._to_issue(
                    raw, project, observe_lifecycle=True, project_epic_closure=False,
                )
                self._lifecycle_projection(parent_id, raw, epic_closure=outcome)
                current, dependencies = bounded_epic_graph_snapshot(
                    self, project, parent,
                    accept_overrides=frozenset(
                        item.node_id for item in outcome.receipt.accepted_overrides
                    ),
                )
            except (SystemExit, TrackerConflictError) as exc:
                raise TrackerConflictError(
                    "graphe Epic Linear divergent au rejeu"
                ) from exc
            if (
                current != outcome.receipt.children
                or dependencies != outcome.receipt.dependencies
            ):
                raise TrackerConflictError("graphe Epic Linear divergent au rejeu")
            return outcome
        finally:
            active.remove(parent_id)

    def get_pending_epic_closure(
        self, project: Project, parent_id: str,
    ) -> EpicClosureReceipt | None:
        self.validate_issue_binding(project, parent_id)
        binding = self._activate(project)
        raw = self._read_raw(parent_id)
        self._assert_issue_project(raw, binding)
        state = raw.get("state")
        native_state_id = state.get("id") if isinstance(state, dict) else None
        if native_state_id == binding["state_ids"]["done"]:
            return None
        pending = self._closure_from_raw(raw, project, require_done=False)
        if pending is None:
            return None
        receipt = pending.receipt
        current_version = _epoch_ms(raw.get("updatedAt"))
        current_state = next(
            (
                name for name, identifier in binding["state_ids"].items()
                if identifier == native_state_id
            ),
            None,
        )
        if (
            current_version is None
            or current_version < receipt.parent_version
            or current_state != receipt.parent_state
        ):
            raise TrackerConflictError(
                "audit pending Linear séparé de son prédécesseur original"
            )
        return receipt

    def close_epic(self, project: Project, receipt: EpicClosureReceipt) -> EpicClosureOutcome:
        binding = self._activate(project)
        self.validate_issue_binding(project, receipt.parent_id, *(x.id for x in receipt.children))
        parent = self.get_issue(receipt.parent_id)
        observed = self._closure_from_issue(parent, project, require_done=False)
        if observed is not None and parent.state == "done":
            existing = self._closure_from_issue(parent, project)
            if existing is None or existing.receipt != receipt:
                raise TrackerConflictError("audit de clôture Linear divergent")
            return existing
        if observed is not None and observed.receipt != receipt:
            raise TrackerConflictError("audit pending Linear divergent")
        from foundry.write import bounded_epic_graph_snapshot, epic_parent_validation_digest

        try:
            children, dependencies = bounded_epic_graph_snapshot(
                self, project, parent,
                accept_overrides=frozenset(
                    item.node_id for item in receipt.accepted_overrides
                ),
            )
        except SystemExit as exc:
            raise TrackerConflictError(
                f"graphe Epic Linear divergent avant écriture : {exc}"
            ) from exc
        if (((observed is None and parent.version != receipt.parent_version)
                or (observed is not None and (
                    parent.version is None or parent.version < receipt.parent_version
                )))
                or parent.type != receipt.parent_type
                or parent.ac_done != receipt.parent_ac_done or parent.ac_total != receipt.parent_ac_total
                or epic_parent_validation_digest(parent) != receipt.parent_validation_digest
                or parent.state != receipt.parent_state
                or children != receipt.children
                or dependencies != receipt.dependencies):
            raise TrackerConflictError("graphe Epic Linear divergent avant écriture")
        audit_id, body, comment_id = self._epic_closure_audit(receipt)
        intent = EpicAuditIntent("linear", project, receipt.parent_id)
        with intent.lock():
            record = intent.read()
            if record is not None and record["audit_id"] != audit_id:
                raise TrackerConflictError(
                    "reçu d'audit Epic Linear différent de l'effet local incertain ; "
                    "no second POST"
                )
            raw = self._read_raw(receipt.parent_id)
            self._assert_issue_project(raw, binding)
            prior = self._read_comment(comment_id, "epic-closure.comment.read")
            if observed is None and prior is None:
                if record is not None:
                    raise TrackerConflictError(
                        "effet de l'audit Epic Linear inconnu ou invisible ; no second POST"
                    )
                # Persist the exact audit identity before the only append. A new
                # receipt nonce must not turn an unresolved effect into a new POST.
                intent.write(audit_id, "pending")
                try:
                    data = self._graphql(_COMMENT_CREATE, {"input": {"id": comment_id, "issueId": raw["id"], "body": body}}, "epic-closure.comment.create")
                    created = self._mutation_payload(data, "commentCreate", "epic-closure.comment.create").get("comment")
                    if not isinstance(created, dict) or created.get("id") != comment_id or created.get("body") != body:
                        raise LinearTrackerError("epic-closure.comment.create", None, "invalid_response")
                except LinearTrackerError:
                    prior = self._read_comment(comment_id, "epic-closure.comment.recover")
                    if not isinstance(prior, dict) or prior.get("body") != body:
                        raise
            elif prior is None or prior.get("body") != body or (prior.get("issue") or {}).get("id") != raw["id"]:
                raise TrackerConflictError("audit de clôture Linear collision")
            intent.write(audit_id, "complete")
            # The audit is append-only and carries the deterministic replay identity;
            # only after it exists can the one targeted parent State projection run.
            # This remains PAT-ADR-0006 bounded detection, not a transaction.
            self._project_native_state(receipt.parent_id, "done", project, binding)
            closed_raw = self._read_raw(receipt.parent_id)
            self._assert_issue_project(closed_raw, binding)
            recovered = self._closure_from_raw(closed_raw, project)
            if recovered is None or recovered.receipt != receipt:
                raise TrackerConflictError("audit de clôture Linear absent après écriture")
            try:
                closed_parent = self._to_issue(
                    closed_raw, project, observe_lifecycle=True,
                )
                closed_children, closed_dependencies = bounded_epic_graph_snapshot(
                    self, project, closed_parent,
                    accept_overrides=frozenset(
                        item.node_id for item in receipt.accepted_overrides
                    ),
                )
            except (SystemExit, TrackerConflictError) as exc:
                raise TrackerConflictError(
                    "graphe Epic Linear divergent après écriture"
                ) from exc
            if (
                closed_children != receipt.children
                or closed_dependencies != receipt.dependencies
            ):
                raise TrackerConflictError(
                    "graphe Epic Linear divergent après écriture"
                )
            return EpicClosureOutcome(receipt, recovered.closed_parent_version, audit_id)

    def add_comment(
        self,
        issue_id: str,
        text: str,
        project: Project | None = None,
    ) -> None:
        if project is None:
            raise LinearBindingError("mutation_project_required")
        if not isinstance(text, str) or not text:
            raise ValueError("Linear comment must be non-empty")
        binding = self._activate(project)
        raw = self._read_raw(issue_id)
        self._assert_issue_project(raw, binding)
        data = self._graphql(
            _COMMENT_CREATE,
            {"input": {"issueId": raw["id"], "body": text}},
            "comment.create",
        )
        payload = self._mutation_payload(data, "commentCreate", "comment.create")
        comment = payload.get("comment")
        if (
            not isinstance(comment, dict)
            or not isinstance(comment.get("id"), str)
                or comment.get("body") != text
            or (comment.get("issue") or {}).get("id") != raw["id"]
        ):
            raise LinearTrackerError("comment.create", None, "invalid_response")
        readback = self._read_comment(comment["id"], "comment.readback")
        if (
            not isinstance(readback, dict)
            or readback.get("body") != text
            or (readback.get("issue") or {}).get("id") != raw["id"]
        ):
            raise TrackerConflictError("Linear comment divergent after write; no retry")

    def update_body(
        self,
        resource: Issue | Adr,
        expected_body: str,
        updated_body: str,
        project: Project | None = None,
    ) -> bool:
        if isinstance(resource, Adr):
            return self._update_adr_body(resource, expected_body, updated_body, project)
        if project is None:
            raise LinearBindingError("mutation_project_required")
        if not isinstance(expected_body, str) or not isinstance(updated_body, str):
            raise ValueError("corps attendu et voulu doivent être des chaînes")
        binding = self._activate(project)
        raw = self._read_raw(resource.id)
        self._assert_issue_project(raw, binding)
        current = raw.get("description") or ""
        if current != expected_body:
            raise TrackerConflictError("Linear body changed before bounded write")
        if current == updated_body:
            return False
        self._bounded_issue_update(
            resource.id,
            raw,
            binding,
            {"description": updated_body},
            {"body": updated_body},
            "issue.update.body",
        )
        return True

    def sync_acceptance_body(
        self,
        issue_id: str,
        expected_body: str,
        updated_body: str,
        proof: dict,
        project: Project | None = None,
    ) -> bool:
        del issue_id, expected_body, updated_body, proof, project
        raise AcceptanceSyncUnavailableError(
            "synchronisation AC indisponible pour le tracker linear : "
            "aucune précondition atomique anti-écrasement"
        )

    # ---- project-scoped ADR documents --------------------------------
    def _adr_documents(self, project: Project) -> tuple[dict, list[dict]]:
        binding = self._activate(project)
        documents = []
        after = None
        seen = set()
        for _ in range(_MAX_PAGES):
            data = self._graphql(
                _ADR_DOCUMENTS_QUERY,
                {"projectId": binding["project_id"], "after": after},
                "adr.list",
            )
            connection = data.get("documents")
            if not isinstance(connection, dict) or not isinstance(
                connection.get("nodes"), list
            ):
                raise LinearTrackerError("adr.list", None, "invalid_response")
            if any(not isinstance(node, dict) for node in connection["nodes"]):
                raise LinearTrackerError("adr.list", None, "invalid_response")
            page = connection.get("pageInfo")
            if not isinstance(page, dict) or type(page.get("hasNextPage")) is not bool:
                raise LinearTrackerError("adr.list", None, "invalid_response")
            documents.extend(connection["nodes"])
            if not page["hasNextPage"]:
                return binding, documents
            cursor = page.get("endCursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise LinearTrackerError("adr.list", None, "invalid_response")
            seen.add(cursor)
            after = cursor
        raise LinearTrackerError("adr.list", None, "pagination_limit")

    def _adr_snapshot(self, project: Project) -> tuple[dict, dict]:
        binding, documents = self._adr_documents(project)
        chains = _adr_chain(documents, binding)
        self._validate_adr_graph(chains, binding)
        return binding, chains

    def _recover_native_create_witness(
        self, project: Project, title: str, body: str
    ) -> bool:
        """Complete exactly one interrupted native v0 pair from its surviving version.

        The ADR version is created before its witness, so this is the only possible
        between-call process-stop state. Ordinary reads never call this repair path.
        """
        binding, documents = self._adr_documents(project)
        versions = {}
        witnesses = set()
        for raw in documents:
            if isinstance(raw, dict) and (
                str(raw.get("title", "")).startswith(_ADR_WITNESS_PREFIX)
                or str(raw.get("content", "")).startswith(_ADR_WITNESS_HEADER)
            ):
                witness = _parse_adr_witness(raw, binding)
                key = (witness["adr_id"], witness["sequence"])
                if key in witnesses:
                    raise TrackerConflictError("Linear ADR witness slot has a fork")
                witnesses.add(key)
            elif isinstance(raw, dict) and (
                str(raw.get("title", "")).startswith(_ADR_DOCUMENT_PREFIX)
                or str(raw.get("content", "")).startswith(_ADR_HEADER)
            ):
                metadata, _parsed_body = _parse_adr_document(
                    raw, binding, allow_unbound_body=True
                )
                key = (metadata["id"], metadata["sequence"])
                if key in versions:
                    raise TrackerConflictError("Linear ADR version slot has a fork")
                versions[key] = (metadata, _parsed_body, raw)
        missing = set(versions) - witnesses
        if set(witnesses) - set(versions) or len(missing) != 1:
            return False
        key = next(iter(missing))
        metadata, parsed_body, raw = versions[key]
        if (
            metadata["sequence"] != 0
            or metadata["origin"] != {"kind": "native"}
            or metadata["status"] != "proposed"
            or metadata["title"] != title
            or metadata["body_sha256"]
            != hashlib.sha256(body.encode()).hexdigest()
            or not _exact_adr_document_matches(
                raw,
                {
                    "id": raw["id"],
                    "title": _adr_document_title(metadata),
                    "content": _adr_document_content(metadata, body),
                    "project": {"id": binding["project_id"]},
                    "archivedAt": None,
                },
            )
        ):
            return False

        # Prove every other pair and relation before the one bounded repair write.
        remaining = [
            item
            for item in documents
            if not isinstance(item, dict) or item.get("id") != raw["id"]
        ]
        other_chains = _adr_chain(remaining, binding)
        self._validate_adr_graph(other_chains, binding)
        # The surviving exact slot already proves this body's rendering.
        self._create_adr_document(binding, metadata, body, new_body=False)
        return True

    def _validate_adr_graph(
        self, chains: dict, binding: dict, *, pending_comments: set[str] | None = None
    ) -> None:
        latest = {adr_id: versions[-1][0] for adr_id, versions in chains.items()}
        issue_refs = []
        for adr_id, metadata in latest.items():
            relations = metadata["relations"]
            for target_id in relations["supersedes"]:
                target = latest.get(target_id)
                if target is None:
                    raise AdrUnavailableError(target_id)
                if (
                    metadata["status"] not in {"accepted", "superseded"}
                    or target["status"] != "superseded"
                    or target["relations"]["superseded_by"] != adr_id
                ):
                    raise TrackerConflictError(
                        "Linear ADR supersession relation is not reciprocal"
                    )
            replacement_id = relations["superseded_by"]
            if replacement_id is not None:
                replacement = latest.get(replacement_id)
                if replacement is None:
                    raise AdrUnavailableError(replacement_id)
                if adr_id not in replacement["relations"]["supersedes"]:
                    raise TrackerConflictError(
                        "Linear ADR supersession relation is not reciprocal"
                    )
            observed_native_ids = set()
            for issue_id in relations["issues"]:
                try:
                    canonical_id, native_id = self._resolve_adr_issue_reference(
                        issue_id, binding
                    )
                except IssueUnavailableError:
                    raise AdrIssueUnavailableError(adr_id, issue_id) from None
                if canonical_id != issue_id:
                    raise TrackerConflictError(
                        "Linear ADR issue relation identifier is not canonical"
                    )
                if native_id in observed_native_ids:
                    raise TrackerConflictError(
                        "Linear ADR issue references duplicate provider identity"
                    )
                observed_native_ids.add(native_id)
                issue_refs.append((adr_id, canonical_id, native_id))
        for adr_id, issue_id, native_id in sorted(issue_refs):
            comment_id, body = _adr_issue_link(binding, adr_id, issue_id)
            reciprocal = self._read_comment(comment_id, "adr.issue-link.read")
            reciprocal_issue = (
                reciprocal.get("issue") if isinstance(reciprocal, dict) else None
            )
            if reciprocal is None and comment_id in (pending_comments or set()):
                continue
            if (
                not isinstance(reciprocal, dict)
                or reciprocal.get("body") != body
                or not isinstance(reciprocal_issue, dict)
                or reciprocal_issue.get("id") != native_id
                or reciprocal_issue.get("identifier") != issue_id
            ):
                raise TrackerConflictError(
                    "Linear ADR issue relation is not reciprocal"
                )

    @staticmethod
    def _adr_model(versions: list[tuple[dict, dict]]) -> Adr:
        metadata, raw = versions[-1]
        body = raw.get(_ADR_BOUND_SOURCE_BODY)
        if not isinstance(body, str):
            _, body = _parse_adr_document(
                raw,
                {
                    "project_id": metadata["project_id"],
                    "team_id": metadata["team_id"],
                },
            )
        return Adr(
            id=metadata["id"],
            title=metadata["title"],
            status=metadata["status"],
            body=_adr_document_content(metadata, body),
            ref=raw["id"],
        )

    def _read_adr_document(self, document_id: str) -> dict | None:
        """Read one deterministic document slot without Linear's erroring singular lookup."""
        data = self._graphql(
            _ADR_DOCUMENT_BY_ID_QUERY,
            {"id": document_id},
            "adr.read",
        )
        connection = data.get("documents")
        if not isinstance(connection, dict) or not isinstance(
            connection.get("nodes"), list
        ):
            raise LinearTrackerError("adr.read", None, "invalid_response")
        page = connection.get("pageInfo")
        if (
            not isinstance(page, dict)
            or type(page.get("hasNextPage")) is not bool
            or page["hasNextPage"]
            or (
                page.get("endCursor") is not None
                and (
                    not isinstance(page["endCursor"], str)
                    or not page["endCursor"]
                )
            )
        ):
            raise LinearTrackerError("adr.read", None, "invalid_response")
        nodes = connection["nodes"]
        if len(nodes) > 1:
            raise TrackerConflictError("Linear ADR document slot collision")
        if not nodes:
            return None
        raw = nodes[0]
        if not isinstance(raw, dict) or raw.get("id") != document_id:
            raise LinearTrackerError("adr.read", None, "invalid_response")
        return raw

    def _create_adr_document(
        self, binding: dict, metadata: dict, body: str,
        *, readback_content: str | None = None,
        witness_readback: str | None = None,
        new_body: bool = True,
    ) -> dict:
        _preflight_adr_body_readback(
            body,
            allow_foundry_adr_0001=_is_foundry_adr_0001_profile_chain(metadata),
            new_body=new_body,
        )
        doc_id = _adr_document_id(
            binding["project_id"], metadata["id"], metadata["sequence"]
        )
        title = _adr_document_title(metadata)
        content = _adr_document_content(metadata, body)
        expected = {
            "id": doc_id,
            "title": title,
            "content": content,
            "project": {"id": binding["project_id"]},
            "archivedAt": None,
        }

        def pinned_matches(raw: object, slot: dict, pinned: str) -> bool:
            # A qualification probe pins the complete provider bytes.  Accept
            # those bytes only, never a neighbouring local-model serialization.
            return isinstance(raw, dict) and all(
                raw.get(key) == value for key, value in slot.items() if key != "content"
            ) and raw.get("content") == pinned

        def exact_version_matches(raw: object) -> bool:
            if readback_content is not None:
                return pinned_matches(raw, expected, readback_content)
            return _exact_adr_document_matches(raw, expected)

        # The historical ADR-0001 profile is evidence about an already-written
        # Document, not a rule for producing new Markdown.  Read its deterministic
        # slot before the create path so an absent slot fails before any mutation;
        # a present exact slot may still receive its missing witness below.
        existing = None
        recovery_only = (
            metadata["sequence"] == 0
            and _is_recovery_only_historical_source(body)
        )
        if recovery_only:
            existing = self._read_adr_document(doc_id)
            if existing is None:
                raise TrackerConflictError(
                    "Linear ADR historical readback requires existing exact slot"
                )
            if not exact_version_matches(existing):
                raise TrackerConflictError("Linear ADR historical slot diverged")

        def verify_version(raw):
            if raw is None:
                return None
            if not exact_version_matches(raw):
                raise TrackerConflictError(
                    "Linear ADR version slot already has different content"
                )
            # A qualification probe authorizes only these exact provider bytes.
            # Parse the envelope after that byte-for-byte check, rather than
            # requiring the local renderer to recognize a provider rendering
            # which the probe deliberately replaces.
            _parse_adr_document(
                raw,
                binding,
                source_body=body,
                allow_witness_bound_readback=readback_content is not None,
            )
            return raw

        # The recovery-only slot is never re-entered through the create path: a
        # deletion after this read must not turn into a fresh historical Document.
        version = (
            verify_version(existing)
            if existing is not None
            else self._create_exact_adr_document(
                expected, "adr.create", verify_version
            )
        )
        witness = _adr_witness_document(binding, metadata, version, body)

        def verify_witness(raw):
            if raw is None:
                return None
            _parse_adr_witness(raw, binding)
            if (
                not pinned_matches(raw, witness, witness_readback)
                if witness_readback is not None
                else not _exact_adr_document_matches(raw, witness)
            ):
                raise TrackerConflictError(
                    "Linear ADR witness slot already has different content"
                )
            return raw

        self._create_exact_adr_document(
            witness, "adr.witness.create", verify_witness
        )
        return version

    def _recover_exact_missing_witness(
        self, project: Project, binding: dict, documents: list[dict],
        metadata: dict, body: str,
    ) -> Adr:
        """Finish one exact version/witness pair only after whole-graph preflight."""
        candidate = {
            "id": _adr_document_id(
                binding["project_id"], metadata["id"], metadata["sequence"]
            ),
            "title": _adr_document_title(metadata),
            "content": _adr_document_content(metadata, body),
            "project": {"id": binding["project_id"]},
            "archivedAt": None,
        }
        matches = [
            raw for raw in documents
            if isinstance(raw, dict) and raw.get("id") == candidate["id"]
        ]
        if len(matches) != 1 or not _exact_adr_document_matches(
            matches[0], candidate
        ):
            raise TrackerConflictError("Linear ADR interrupted version slot diverged")
        witness = _adr_witness_document(binding, metadata, matches[0], body)
        if any(
            isinstance(raw, dict) and raw.get("id") == witness["id"]
            for raw in documents
        ):
            raise TrackerConflictError("Linear ADR witness is not the missing slot")
        hypothetical = _adr_chain([*documents, witness], binding)
        self._validate_adr_graph(hypothetical, binding)
        # The exact version slot exists: only its witness is written.
        self._create_adr_document(binding, metadata, body, new_body=False)
        _, fresh = self._adr_snapshot(project)
        if fresh[metadata["id"]][-1][0] != metadata:
            raise TrackerConflictError("Linear ADR witness recovery diverged")
        return self._adr_model(fresh[metadata["id"]])

    @staticmethod
    def _unwitnessed_adr_versions(
        binding: dict, documents: list[dict]
    ) -> list[tuple[dict, dict]]:
        """Locate version slots with no witness without guessing their source body."""
        witnesses = set()
        versions = []
        for raw in documents:
            if isinstance(raw, dict) and (
                str(raw.get("title", "")).startswith(_ADR_WITNESS_PREFIX)
                or str(raw.get("content", "")).startswith(_ADR_WITNESS_HEADER)
            ):
                witness = _parse_adr_witness(raw, binding)
                key = (witness["adr_id"], witness["sequence"])
                if key in witnesses:
                    raise TrackerConflictError("Linear ADR witness slot has a fork")
                witnesses.add(key)
                continue
            if not (
                isinstance(raw, dict)
                and (
                    str(raw.get("title", "")).startswith(_ADR_DOCUMENT_PREFIX)
                    or str(raw.get("content", "")).startswith(_ADR_HEADER)
                )
            ):
                continue
            metadata, _ = _parse_adr_document(
                raw, binding, allow_unbound_body=True
            )
            versions.append((metadata, raw))
        return [
            (metadata, raw)
            for metadata, raw in versions
            if (metadata["id"], metadata["sequence"]) not in witnesses
        ]

    def _interrupted_mutation_predecessor(
        self,
        binding: dict,
        documents: list[dict],
        adr: Adr,
        operation: str,
    ) -> tuple[tuple[dict, dict], tuple[dict, dict]]:
        """Bind one dangling version to its intact witnessed predecessor."""
        dangling = self._unwitnessed_adr_versions(binding, documents)
        if len(dangling) != 1:
            raise TrackerConflictError(
                f"Linear ADR interrupted {operation} has ambiguous witness"
            )
        dangling_metadata, dangling_raw = dangling[0]
        remaining = [raw for raw in documents if raw is not dangling_raw]
        chains = _adr_chain(remaining, binding)
        self._validate_adr_graph(chains, binding)
        versions = chains.get(adr.id)
        if versions is None or versions[-1][1]["id"] != adr.ref:
            raise TrackerConflictError(
                f"Linear ADR interrupted {operation} predecessor diverged"
            )
        return versions[-1], (dangling_metadata, dangling_raw)

    def _create_exact_adr_document(self, expected, operation, verify):
        document_id = expected["id"]
        existing = verify(self._read_adr_document(document_id))
        if existing is not None:
            return existing
        error = None
        try:
            payload = self._mutation_payload(
                self._graphql(
                    _ADR_DOCUMENT_CREATE,
                    {
                        "input": {
                            "id": document_id,
                            "title": expected["title"],
                            "content": expected["content"],
                            "projectId": expected["project"]["id"],
                        }
                    },
                    operation,
                ),
                "documentCreate",
                operation,
            )
            if (
                not isinstance(payload.get("document"), dict)
                or payload["document"].get("id") != document_id
            ):
                raise LinearTrackerError(operation, None, "invalid_response")
        except LinearTrackerError as exc:
            error = exc
        readback = verify(self._read_adr_document(document_id))
        if readback is None:
            if error:
                raise error
            raise TrackerConflictError(
                "Linear ADR deterministic document has no readback"
            )
        return readback

    def _create_adr_issue_link(
        self,
        binding: dict,
        adr_id: str,
        issue_id: str,
        native_id: str,
    ) -> None:
        prefix = f"{binding['key']}-"
        number = (
            issue_id[len(prefix) :]
            if isinstance(issue_id, str) and issue_id.startswith(prefix)
            else None
        )
        if (
            not isinstance(native_id, str)
            or _SAFE_ID.fullmatch(native_id) is None
            or not isinstance(number, str)
            or _ISSUE_NUMBER.fullmatch(number) is None
        ):
            raise TrackerConflictError("Linear ADR issue identity is not canonical")
        comment_id, body = _adr_issue_link(binding, adr_id, issue_id)

        def verify(comment):
            if comment is None:
                return None
            comment_issue = (
                comment.get("issue") if isinstance(comment, dict) else None
            )
            if (
                not isinstance(comment, dict)
                or comment.get("body") != body
                or not isinstance(comment_issue, dict)
                or comment_issue.get("id") != native_id
                or comment_issue.get("identifier") != issue_id
            ):
                raise TrackerConflictError(
                    "Linear ADR issue relation slot already has different content"
                )
            return comment

        if verify(self._read_comment(comment_id, "adr.issue-link.read")) is not None:
            return
        error = None
        try:
            payload = self._mutation_payload(
                self._graphql(
                    _COMMENT_CREATE,
                    {
                        "input": {
                            "id": comment_id,
                            "issueId": native_id,
                            "body": body,
                        }
                    },
                    "adr.issue-link.create",
                ),
                "commentCreate",
                "adr.issue-link.create",
            )
            verify(payload.get("comment"))
        except LinearTrackerError as exc:
            error = exc
        readback = verify(
            self._read_comment(comment_id, "adr.issue-link.readback")
        )
        if readback is None:
            if error:
                raise error
            raise TrackerConflictError(
                "Linear ADR issue relation has no comment readback"
            )

    @staticmethod
    def _next_adr_metadata(
        previous,
        *,
        status=None,
        body=None,
        replacement_id=None,
        supersedes_id=None,
        issue_id=None,
    ):
        old, raw = previous
        _, old_body = _parse_adr_document(
            raw,
            {"project_id": old["project_id"], "team_id": old["team_id"]},
            source_body=raw.get(_ADR_BOUND_SOURCE_BODY),
            allow_witness_bound_readback=_is_probe_qualified_historical_version(old),
        )
        new_body = old_body if body is None else body
        metadata = dict(old)
        metadata.update(
            sequence=old["sequence"] + 1,
            previous_id=raw["id"],
            previous_sha256=hashlib.sha256(raw["content"].encode()).hexdigest(),
            status=old["status"] if status is None else status,
            body_sha256=hashlib.sha256(new_body.encode()).hexdigest(),
            relations=dict(old["relations"]),
        )
        if replacement_id is not None:
            metadata["relations"]["superseded_by"] = replacement_id
        if supersedes_id is not None:
            metadata["relations"]["supersedes"] = sorted(
                {*metadata["relations"]["supersedes"], supersedes_id}
            )
        if issue_id is not None:
            metadata["relations"]["issues"] = sorted(
                {*metadata["relations"]["issues"], issue_id}
            )
        candidate = {
            "id": _adr_document_id(
                metadata["project_id"], metadata["id"], metadata["sequence"]
            ),
            "title": _adr_document_title(metadata),
            "content": _adr_document_content(metadata, new_body),
            "project": {"id": metadata["project_id"]},
            "archivedAt": None,
        }
        try:
            _parse_adr_document(
                candidate,
                {
                    "project_id": metadata["project_id"],
                    "team_id": metadata["team_id"],
                },
            )
        except LinearTrackerError:
            raise TrackerConflictError(
                "Linear ADR derived candidate is invalid"
            ) from None
        return metadata, new_body

    def _append_adr_version(self, project, previous, metadata, body):
        return self._append_adr_versions(
            project, [(previous, metadata, body)]
        )[metadata["id"]]

    def _is_exact_adr_body_replay(
        self,
        versions: list[tuple[dict, dict]],
        expected_body: str,
        updated_body: str,
    ) -> bool:
        if (
            len(versions) < 2
            or updated_body == expected_body
            or not _adr_readback_content_matches(
                versions[-2][1].get("content"), expected_body
            )
        ):
            return False
        old_header, separator, _ = expected_body.partition("\n-->\n\n")
        new_header, new_separator, body = updated_body.partition("\n-->\n\n")
        if not separator or not new_separator or old_header != new_header:
            return False
        metadata, body = self._next_adr_metadata(versions[-2], body=body)
        candidate = {
            "id": _adr_document_id(
                metadata["project_id"], metadata["id"], metadata["sequence"]
            ),
            "title": _adr_document_title(metadata),
            "content": _adr_document_content(metadata, body),
            "project": {"id": metadata["project_id"]},
            "archivedAt": None,
        }
        return (
            versions[-1][0] == metadata
            and _exact_adr_document_matches(versions[-1][1], candidate)
        )

    def _append_adr_versions(self, project, changes):
        # Only a changed body is new: an unchanged one is the stored previous
        # version's body, whose rendering is proven if the model verifies it.
        new_bodies = [
            not _is_stored_adr_body(previous, body)
            for previous, _metadata, body in changes
        ]
        for (_previous, metadata, body), new_body in zip(changes, new_bodies):
            _preflight_adr_body_readback(
                body,
                allow_foundry_adr_0001=(
                    _is_foundry_adr_0001_profile_chain(metadata)
                ),
                new_body=new_body,
            )
        binding, chains = self._adr_snapshot(project)
        for previous, metadata, _body in changes:
            if (
                metadata["id"] not in chains
                or chains[metadata["id"]][-1][1]["id"] != previous[1]["id"]
            ):
                raise TrackerConflictError("Linear ADR changed before version append")
        for (_previous, metadata, body), new_body in zip(changes, new_bodies):
            self._create_adr_document(binding, metadata, body, new_body=new_body)
        _, fresh = self._adr_snapshot(project)
        result = {}
        for _previous, metadata, _body in changes:
            versions = fresh.get(metadata["id"])
            if versions is None or versions[-1][0] != metadata:
                raise TrackerConflictError("Linear ADR version diverged after write")
            result[metadata["id"]] = self._adr_model(versions)
        return result

    def list_adrs(self, project: Project) -> list[Adr]:
        _, chains = self._adr_snapshot(project)
        return [self._adr_model(chains[key]) for key in sorted(chains)]

    def migration_export_adrs(self, project: Project) -> list[dict]:
        _, chains = self._adr_snapshot(project)
        exported = []
        for adr_id in sorted(chains):
            metadata = chains[adr_id][-1][0]
            origin = metadata.get("origin")
            missing = set(
                origin.get("missing_relations", [])
                if isinstance(origin, dict) else []
            )
            relations = {
                name: "unknown" if name in missing else metadata["relations"][name]
                for name in ("supersedes", "superseded_by", "issues")
            }
            exported.append({
                "adr": self._adr_model(chains[adr_id]),
                "relations": relations,
                **({
                    "source_only_issue_refs": origin["source_only_issue_refs"],
                } if isinstance(origin, dict)
                   and origin.get("source_only_issue_refs") else {}),
                "source_created": (
                    origin.get("source_created") if isinstance(origin, dict) else None
                ),
                "source_updated": (
                    origin.get("source_updated") if isinstance(origin, dict) else None
                ),
            })
        return exported

    def migration_find_adr(self, project: Project, source_ref: str) -> Adr | None:
        _, chains = self._adr_snapshot(project)
        matches = []
        for versions in chains.values():
            origin = versions[-1][0].get("origin")
            if isinstance(origin, dict) and origin.get("source_ref") == source_ref:
                matches.append(self._adr_model(versions))
        if len(matches) > 1:
            raise TrackerConflictError("provenance ADR Linear ambiguë")
        return matches[0] if matches else None

    def migration_import_adr(
        self, project: Project, snapshot: dict, *, source_ref: str,
    ) -> Adr:
        registry.require_writable_project(self.name, project)
        existing = self.migration_find_adr(project, source_ref)
        if existing is not None:
            return existing
        relations = snapshot["relations"]
        optional = {}
        if relations.get("supersedes") != "unknown":
            optional["supersedes"] = tuple(relations["supersedes"])
        if relations.get("superseded_by") != "unknown":
            optional["superseded_by"] = relations["superseded_by"]
        if relations.get("issues") != "unknown":
            optional["issue_refs"] = tuple(relations["issues"])
        return self.import_adr(
            project,
            adr_id=snapshot["id"],
            title=snapshot["title"],
            body=snapshot["body"],
            historical_status=snapshot["status"],
            source_ref=source_ref,
            source_created=snapshot.get("source_created"),
            source_updated=snapshot.get("source_updated"),
            expected_source_sha256=hashlib.sha256(
                snapshot["body"].encode()
            ).hexdigest(),
            source_only_issue_refs=tuple(
                snapshot.get("source_only_issue_refs", []),
            ),
            **optional,
        )

    def _migration_qualification_probe(
        self,
        *,
        target_project_id: str,
        qualification_project_id: str,
        adr_id: str,
        kind: str,
        title: str,
        content: str,
    ) -> tuple[str, str]:
        """Create or recover one deterministic, non-authoritative probe."""
        probe_id = _adr_client_uuid(
            "foundry-linear-adr-qualification.v1:"
            f"{target_project_id}:{qualification_project_id}:{adr_id}:{kind}:"
            f"{hashlib.sha256(content.encode()).hexdigest()}"
        )
        expected = {
            "id": probe_id,
            "title": title,
            "content": content,
            "project": {"id": qualification_project_id},
            "archivedAt": None,
        }

        def verify(raw):
            if raw is None:
                return None
            if (
                not isinstance(raw, dict)
                or any(
                    raw.get(key) != value
                    for key, value in expected.items()
                    if key != "content"
                )
                or not isinstance(raw.get("content"), str)
            ):
                raise TrackerConflictError(
                    "Linear ADR batch qualification probe is not exact"
                )
            return raw

        observed = self._create_exact_adr_document(
            expected, "adr.qualification-probe.create", verify,
        )
        return probe_id, observed["content"]

    def migration_qualify_adrs(
        self, project: Project, snapshots: tuple[dict, ...],
    ) -> object | None:
        """Qualify exact version/witness bytes after issue identifiers exist."""
        if not snapshots:
            return None
        qualification_project_id = project.extra.get(
            "migration_adr_qualification_project_id"
        )
        try:
            qualification_uuid = uuid.UUID(qualification_project_id)
        except (TypeError, ValueError, AttributeError):
            raise TrackerCapabilityUnavailableError(
                self.name, "migration_adr_batch_qualification",
            ) from None
        if (
            qualification_uuid.version != 4
            or str(qualification_uuid) != qualification_project_id
            or qualification_project_id == project.id
        ):
            raise TrackerConflictError(
                "projet de qualification batch ADR Linear non isolé"
            )

        base = self._migration_adr_batch_records(snapshots)
        first_plan = self.plan_adr_batch_qualification(project, base)
        observed_documents = {}
        document_probes = {}
        for item in first_plan:
            if item["recovery_only"]:
                continue
            probe_id, observed = self._migration_qualification_probe(
                target_project_id=project.id,
                qualification_project_id=qualification_project_id,
                adr_id=item["adr_id"],
                kind="document",
                title=item["document_probe_title"],
                content=item["document_content"],
            )
            observed_documents[item["adr_id"]] = observed
            document_probes[item["adr_id"]] = probe_id

        final_plan = self.plan_adr_batch_qualification(
            project, base, observed_documents,
        )
        qualified = []
        for record, item in zip(base, final_plan):
            profiled = {
                **record,
                "qualification_project_id": qualification_project_id,
            }
            if not item["recovery_only"]:
                document_content = observed_documents[item["adr_id"]]
                profiled.update({
                    "expected_linear_document_content": document_content,
                    "expected_linear_document_sha256": hashlib.sha256(
                        document_content.encode()
                    ).hexdigest(),
                    "document_probe_id": document_probes[item["adr_id"]],
                })
            witness_probe_id, witness_content = self._migration_qualification_probe(
                target_project_id=project.id,
                qualification_project_id=qualification_project_id,
                adr_id=item["adr_id"],
                kind="witness",
                title=item["witness_probe_title"],
                content=item["witness_content"],
            )
            profiled.update({
                "expected_linear_witness_content": witness_content,
                "expected_linear_witness_sha256": hashlib.sha256(
                    witness_content.encode()
                ).hexdigest(),
                "witness_probe_id": witness_probe_id,
            })
            qualified.append(profiled)

        # Bind the returned evidence to the exact closed target batch now, so a
        # qualification failure leaves only copied issues and isolated probes.
        normalized = self._normalize_migration_adr_profile(qualified)
        binding, _documents, by_id, entries, _comments = self._prepare_adr_batch(
            project, normalized, profiled=True,
        )
        self._verify_adr_batch_qualification(binding, by_id, entries)
        return qualified

    def migration_import_adrs(
        self, project: Project, snapshots: tuple[dict, ...],
        *, qualification: object | None = None,
    ) -> list[Adr]:
        registry.require_writable_project(self.name, project)
        if not snapshots:
            return []
        supplied = (
            qualification
            if qualification is not None
            else project.extra.get("migration_adr_batch_profile")
        )
        if not isinstance(supplied, list):
            raise TrackerCapabilityUnavailableError(
                self.name, "migration_adr_batch_qualification",
            )
        qualified = self._normalize_migration_adr_profile(supplied)
        expected = self._migration_adr_batch_records(snapshots)
        profile_fields = (
            _ADR_BATCH_PROFILE_FIELDS | _ADR_BATCH_RECOVERY_PROFILE_FIELDS
        )
        observed_base = tuple(
            {key: value for key, value in record.items() if key not in profile_fields}
            for record in qualified
        )
        if observed_base != expected:
            raise TrackerConflictError(
                "profil batch ADR Linear incompatible avec la copie demandée"
            )
        return self.import_adr_batch(project, qualified)

    def adr_for_mutation(self, project: Project, adr_id: str) -> Adr | None:
        """Return a witnessed predecessor for an exact mutation replay.

        This is deliberately not a read/list fallback: only the typed mutation
        may decide whether an interrupted slot matches its requested action.
        """
        try:
            return next((a for a in self.list_adrs(project) if a.id == adr_id), None)
        except TrackerConflictError as error:
            if str(error) not in {
                "Linear ADR version witness is missing",
                "Linear ADR supersession relation is not reciprocal",
            }:
                raise
        binding, documents = self._adr_documents(project)
        dangling = self._unwitnessed_adr_versions(binding, documents)
        if len(dangling) > 1:
            raise TrackerConflictError("Linear ADR mutation has ambiguous witness")
        dangling_raw = dangling[0][1] if dangling else None
        remaining = [
            raw for raw in documents
            if dangling_raw is None or raw is not dangling_raw
        ]
        chains = _adr_chain(remaining, binding)
        versions = chains.get(adr_id)
        return self._adr_model(versions) if versions else None

    def create_adr(
        self,
        project: Project,
        title: str,
        body: str,
        status: str = "proposed",
    ) -> Adr:
        if status != "proposed":
            raise TrackerConflictError("Linear ADR creation must begin proposed")
        if not isinstance(title, str) or not title.strip() or not isinstance(body, str):
            raise ValueError("Linear ADR title and body required")
        title = title.strip()
        try:
            binding, chains = self._adr_snapshot(project)
        except TrackerConflictError as error:
            if (
                str(error) != "Linear ADR version witness is missing"
                or not self._recover_native_create_witness(project, title, body)
            ):
                raise
            binding, chains = self._adr_snapshot(project)
        digest = hashlib.sha256(body.encode()).hexdigest()
        matching = [
            v
            for v in chains.values()
            if v[0][0]["origin"]["kind"] == "native"
            and v[0][0]["title"] == title
            and v[0][0]["body_sha256"] == digest
        ]
        if matching:
            if (
                len(matching) != 1
                or matching[0][-1][0]["status"] != "proposed"
                or matching[0][-1][0]["body_sha256"] != digest
            ):
                raise TrackerConflictError("Linear ADR content already exists")
            return self._adr_model(matching[0])
        prefix = f"{project.key}-ADR-"
        number = (
            max(
                (
                    int(key.rsplit("-", 1)[1])
                    for key in chains
                    if key.startswith(prefix)
                ),
                default=0,
            )
            + 1
        )
        if number > 9999:
            raise TrackerConflictError("Linear ADR identifier range exhausted")
        metadata = {
            "schema": _ADR_SCHEMA,
            "project_id": binding["project_id"],
            "team_id": binding["team_id"],
            "id": f"{prefix}{number:04d}",
            "title": title,
            "status": "proposed",
            "sequence": 0,
            "previous_id": None,
            "previous_sha256": None,
            "body_sha256": digest,
            "origin": {"kind": "native"},
            "relations": {"supersedes": [], "superseded_by": None, "issues": []},
        }
        self._create_adr_document(binding, metadata, body, new_body=True)
        _, fresh = self._adr_snapshot(project)
        if metadata["id"] not in fresh or fresh[metadata["id"]][-1][0] != metadata:
            raise TrackerConflictError("Linear ADR creation diverged")
        return self._adr_model(fresh[metadata["id"]])

    def link_adr_issue(
        self, adr: Adr, issue_ref: str, project: Project | None = None
    ) -> Adr:
        """Append one native ADR↔issue relation after canonical identity preflight."""
        project = project or self._project()
        try:
            binding, chains = self._adr_snapshot(project)
        except TrackerConflictError as error:
            if str(error) != "Linear ADR version witness is missing":
                raise
            binding, documents = self._adr_documents(project)
            previous, _dangling = self._interrupted_mutation_predecessor(
                binding, documents, adr, "link"
            )
            previous_metadata = previous[0]
            if previous_metadata["id"] != adr.id or previous_metadata["status"] not in {
                "proposed", "accepted"
            }:
                raise TrackerConflictError("Linear ADR interrupted link binding invalid")
            issue_id, _native_id = self._resolve_adr_issue_reference(issue_ref, binding)
            metadata, body = self._next_adr_metadata(
                previous, issue_id=issue_id
            )
            return self._recover_exact_missing_witness(
                project, binding, documents, metadata, body
            )
        versions = chains.get(adr.id)
        if versions is None or versions[-1][1]["id"] != adr.ref:
            raise TrackerConflictError("Linear ADR snapshot is stale")
        if versions[-1][0]["status"] not in {"proposed", "accepted"}:
            raise TrackerConflictError("Linear ADR issue link requires active ADR")
        if not isinstance(issue_ref, str) or _SAFE_ID.fullmatch(issue_ref) is None:
            raise ValueError("Linear ADR issue reference invalid")
        issue_id, native_id = self._resolve_adr_issue_reference(issue_ref, binding)
        if issue_id in versions[-1][0]["relations"]["issues"]:
            return self._adr_model(versions)
        metadata, body = self._next_adr_metadata(
            versions[-1], issue_id=issue_id
        )
        _preflight_adr_body_readback(
            body,
            allow_foundry_adr_0001=_is_foundry_adr_0001_profile_chain(metadata),
            new_body=not _is_stored_adr_body(versions[-1], body),
        )
        # The comment is a deterministic reciprocal slot. If a process stops after
        # this write but before the version append, the exact call can reuse it.
        self._create_adr_issue_link(binding, adr.id, issue_id, native_id)
        return self._append_adr_version(project, versions[-1], metadata, body)

    def _prepare_adr_batch(
        self, project: Project, records: tuple[dict, ...], *, profiled: bool
    ) -> tuple[dict, list[dict], dict, list[dict], list[tuple]]:
        """Validate one historical manifest and derive its exact version-0 slots.

        This is pure over the current project snapshot: no provider write.  The
        qualification plan and the import share it so that a probe is created
        from exactly the bytes the import later sends.
        """
        keys = {
            "adr_id", "title", "body", "historical_status", "source_ref",
            "source_created", "source_updated", "expected_source_sha256",
            "supersedes", "superseded_by", "issue_refs",
        }
        if not isinstance(records, tuple) or not 1 <= len(records) <= 100:
            raise ValueError("Linear ADR batch requires 1..100 records")
        if any(not isinstance(record, dict) for record in records):
            raise ValueError("Linear ADR batch record fields invalid")
        # ``missing_relations`` is part of the base historical record, never a
        # qualification profile field: the planner and the import accept it
        # identically.  One manifest is either entirely complete or entirely
        # declares its unavailable relation families; the shapes never mix.
        declared = {"missing_relations" in record for record in records}
        if len(declared) != 1:
            raise ValueError("Linear ADR batch record fields invalid")
        declares_missing_relations = next(iter(declared))
        if declares_missing_relations:
            keys = keys | {"missing_relations"}
        source_only_declared = {"source_only_issue_refs" in record for record in records}
        if len(source_only_declared) != 1:
            raise ValueError("Linear ADR batch record fields invalid")
        declares_source_only = next(iter(source_only_declared))
        if declares_source_only:
            keys = keys | {"source_only_issue_refs"}
        binding, documents = self._adr_documents(project)
        by_id = {}
        for raw in documents:
            if raw.get("id") in by_id:
                raise TrackerConflictError("Linear ADR batch document slot has a fork")
            by_id[raw.get("id")] = raw
        prepared = []
        comments = []
        seen_ids = set()
        for record in records:
            body = record.get("body")
            recovery_only = (
                isinstance(body, str)
                and _is_recovery_only_historical_source(body)
            )
            expected_keys = keys
            if profiled:
                expected_keys = keys | (
                    _ADR_BATCH_RECOVERY_PROFILE_FIELDS
                    if recovery_only
                    else _ADR_BATCH_PROFILE_FIELDS
                )
            if set(record) != expected_keys:
                raise ValueError("Linear ADR batch record fields invalid")
            adr_id = record["adr_id"]
            title = record["title"]
            status = record["historical_status"]
            source_ref = record["source_ref"]
            supersedes = record["supersedes"]
            superseded_by = record["superseded_by"]
            issue_refs = record["issue_refs"]
            source_only_refs = record.get("source_only_issue_refs", ())
            if not isinstance(source_only_refs, tuple):
                raise ValueError("Linear ADR batch source-only references invalid")
            try:
                migration_source_only_issue_refs(list(source_only_refs))
            except TrackerConflictError as exc:
                raise ValueError("Linear ADR batch source-only references invalid") from exc
            missing_relations = (
                record["missing_relations"] if declares_missing_relations else ()
            )
            if (
                not isinstance(missing_relations, tuple)
                or any(not isinstance(value, str) for value in missing_relations)
                or missing_relations
                != tuple(sorted(set(missing_relations)))
                or not set(missing_relations).issubset(
                    _ADR_MISSING_RELATION_FAMILIES
                )
            ):
                raise ValueError("Linear ADR batch missing relations invalid")
            if (
                ("supersedes" in missing_relations and supersedes != ())
                or (
                    "superseded_by" in missing_relations
                    and superseded_by is not None
                )
                or ("issues" in missing_relations and issue_refs != ())
            ):
                raise ValueError("Linear ADR batch unknown relation is not empty")
            if (
                not isinstance(adr_id, str)
                or _ADR_ID.fullmatch(adr_id) is None
                or adr_id in seen_ids
                or not isinstance(title, str) or not title.strip()
                or not isinstance(body, str)
                or not isinstance(status, str) or status not in _ADR_STATUSES
                or not isinstance(source_ref, str) or not source_ref
                or record["expected_source_sha256"] != hashlib.sha256(body.encode()).hexdigest()
                or any(
                    value is not None and (type(value) is not int or value < 0)
                    for value in (record["source_created"], record["source_updated"])
                )
                or not isinstance(supersedes, tuple)
                or not isinstance(issue_refs, tuple)
                or len(supersedes) > _ADR_RELATION_LIMIT
                or len(issue_refs) > _ADR_RELATION_LIMIT
                or any(not isinstance(value, str) or _ADR_ID.fullmatch(value) is None for value in supersedes)
                or len(set(supersedes)) != len(supersedes)
                or any(not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None for value in issue_refs)
                or len(set(issue_refs)) != len(issue_refs)
                or (superseded_by is not None and (
                    not isinstance(superseded_by, str)
                    or _ADR_ID.fullmatch(superseded_by) is None
                ))
                or adr_id in supersedes
                or superseded_by == adr_id
                or superseded_by in supersedes
                or (
                    (status == "superseded") != (superseded_by is not None)
                    and not (
                        status == "superseded"
                        and superseded_by is None
                        and "superseded_by" in missing_relations
                    )
                )
                or (bool(supersedes) and status not in {"accepted", "superseded"})
            ):
                raise ValueError("Linear ADR batch record invalid")
            # Unsupported source Markdown is refused before any probe read or
            # write.  The batch pins the complete provider bytes of each slot by
            # a qualification probe (or an existing exact slot), so the strict
            # new-body list whitelist does not apply.
            _preflight_adr_body_readback(
                body,
                allow_foundry_adr_0001=adr_id == _FOUNDRY_ADR_0001_ID,
                new_body=False,
            )
            seen_ids.add(adr_id)
            canonical_refs, native_ids = self._canonicalize_adr_issue_refs(
                issue_refs, binding
            )
            metadata = {
                "schema": _ADR_SCHEMA,
                "project_id": binding["project_id"],
                "team_id": binding["team_id"],
                "id": adr_id,
                "title": title.strip(),
                "status": status,
                "sequence": 0,
                "previous_id": None,
                "previous_sha256": None,
                "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "origin": {
                    "kind": "migration",
                    "source_tracker": (
                        source_ref.split(":", 1)[0]
                        if source_ref.split(":", 1)[0]
                        in {"youtrack", "linear", "ghprojects"}
                        else "youtrack"
                    ),
                    "source_ref": source_ref,
                    "source_created": record["source_created"],
                    "source_updated": record["source_updated"],
                    "source_body_sha256": record["expected_source_sha256"],
                    "missing_relations": list(missing_relations),
                    **({
                        "source_only_issue_refs": list(source_only_refs),
                    } if declares_source_only else {}),
                },
                "relations": {
                    "supersedes": sorted(supersedes),
                    "superseded_by": superseded_by,
                    "issues": list(canonical_refs),
                },
            }
            prepared.append((metadata, body, record, recovery_only))
            comments.extend(
                (adr_id, issue_id, native_ids[issue_id])
                for issue_id in canonical_refs
            )

        # Bind every version-0 slot to the entire normalized manifest, not only
        # its own record. A reordered exact replay is equivalent, but a subset or
        # a changed member has different slot bytes and must fail before effects.
        # Qualification profiles are evidence about these bytes, not part of them.
        manifest = [item[0] for item in sorted(prepared, key=lambda item: item[0]["id"])]
        batch_sha256 = hashlib.sha256(json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        entries = []
        for metadata, body, record, recovery_only in prepared:
            metadata["origin"]["batch_sha256"] = batch_sha256
            candidate = {
                "id": _adr_document_id(binding["project_id"], metadata["id"], 0),
                "title": _adr_document_title(metadata),
                "content": _adr_document_content(metadata, body),
                "project": {"id": binding["project_id"]},
                "archivedAt": None,
            }
            _parse_adr_document(candidate, binding)
            existing = None
            if recovery_only:
                # Do not manufacture a rendering from one historical observation.
                # The exact digest-pinned slot must already exist; its provider
                # bytes are the version readback the witness will bind.
                existing = by_id.get(candidate["id"])
                if existing is None:
                    raise TrackerConflictError(
                        "Linear ADR historical readback requires existing exact slot"
                    )
                if not _exact_adr_document_matches(existing, candidate):
                    raise TrackerConflictError("Linear ADR batch slot diverged")
            entries.append({
                "metadata": metadata,
                "body": body,
                "record": record,
                "recovery_only": recovery_only,
                "candidate": candidate,
                "existing_content": None if existing is None else existing["content"],
            })
        return binding, documents, by_id, entries, comments

    def plan_adr_batch_qualification(
        self,
        project: Project,
        records: tuple[dict, ...],
        observed_documents: dict[str, str] | None = None,
    ) -> list[dict]:
        """Return the exact bytes a historical batch import would create.

        Read-only: it performs no provider write.  Records carry no qualification
        profile.  Each item gives the canonical version Document and the title
        its qualification probe must bear.  When ``observed_documents`` maps an
        ADR ID to the complete version readback observed for that exact content,
        the item also gives the canonical witness derived from it.  A recovery-only
        slot uses the provider bytes already stored in the project instead.
        """
        if observed_documents is not None and not isinstance(observed_documents, dict):
            raise ValueError("Linear ADR batch observed documents invalid")
        binding, _documents, _by_id, entries, _comments = self._prepare_adr_batch(
            project, records, profiled=False
        )
        adr_ids = {entry["metadata"]["id"] for entry in entries}
        if observed_documents and not set(observed_documents) <= adr_ids:
            raise ValueError("Linear ADR batch observed documents invalid")
        plan = []
        for entry in entries:
            metadata, candidate = entry["metadata"], entry["candidate"]
            adr_id = metadata["id"]
            item = {
                "adr_id": adr_id,
                "recovery_only": entry["recovery_only"],
                "document_id": candidate["id"],
                "document_title": candidate["title"],
                "document_content": candidate["content"],
                "document_sha256": hashlib.sha256(
                    candidate["content"].encode()
                ).hexdigest(),
                "document_probe_title": _adr_qualification_probe_title(
                    adr_id, "document", candidate["content"]
                ),
                "existing_document_content": entry["existing_content"],
            }
            observed = (observed_documents or {}).get(adr_id)
            if entry["recovery_only"]:
                observed = entry["existing_content"]
            if observed is not None:
                if not isinstance(observed, str) or not _adr_version_readback_header_matches(
                    observed, candidate["content"]
                ):
                    raise TrackerConflictError(
                        "Linear ADR batch document readback is not qualified"
                    )
                witness = _adr_witness_document(
                    binding, metadata, {**candidate, "content": observed}, entry["body"]
                )
                item.update(
                    witness_id=witness["id"],
                    witness_title=witness["title"],
                    witness_content=witness["content"],
                    witness_sha256=hashlib.sha256(
                        witness["content"].encode()
                    ).hexdigest(),
                    witness_probe_title=_adr_qualification_probe_title(
                        adr_id, "witness", witness["content"]
                    ),
                )
            plan.append(item)
        return plan

    def _verify_adr_batch_qualification(
        self, binding: dict, by_id: dict, entries: list[dict]
    ) -> list[tuple[dict, dict, str, str]]:
        """Read every qualification probe before any effect; return exact profiles."""
        if any(
            not isinstance(entry["record"]["qualification_project_id"], str)
            for entry in entries
        ):
            raise ValueError("Linear ADR batch record invalid")
        projects = {entry["record"]["qualification_project_id"] for entry in entries}
        qualification_project = next(iter(projects))
        if (
            len(projects) != 1
            or qualification_project == binding["project_id"]
        ):
            raise TrackerConflictError(
                "Linear ADR batch qualification project is not isolated"
            )
        try:
            project_uuid = uuid.UUID(qualification_project)
        except (TypeError, ValueError, AttributeError):
            raise ValueError("Linear ADR batch record invalid") from None
        if str(project_uuid) != qualification_project:
            raise ValueError("Linear ADR batch record invalid")
        slot_ids = set(by_id)
        for entry in entries:
            slot_ids.add(entry["candidate"]["id"])
            slot_ids.add(
                _adr_witness_id(binding["project_id"], entry["metadata"]["id"], 0)
            )
        seen_probes = set()

        def read_probe(probe_id: object, title: str, content: str) -> None:
            try:
                probe_uuid = uuid.UUID(probe_id)
            except (TypeError, ValueError, AttributeError):
                raise ValueError("Linear ADR batch record invalid") from None
            if probe_uuid.version != 4 or str(probe_uuid) != probe_id:
                raise ValueError("Linear ADR batch record invalid")
            if probe_id in seen_probes or probe_id in slot_ids:
                raise TrackerConflictError(
                    "Linear ADR batch qualification probe collides"
                )
            seen_probes.add(probe_id)
            probe = self._read_adr_document(probe_id)
            if (
                probe is None
                or probe.get("id") != probe_id
                or probe.get("title") != title
                or probe.get("archivedAt") is not None
                or probe.get("project", {}).get("id") != qualification_project
                or probe.get("content") != content
            ):
                raise TrackerConflictError(
                    "Linear ADR batch qualification probe is not exact"
                )

        profiles = []
        for entry in entries:
            record, metadata, candidate = (
                entry["record"], entry["metadata"], entry["candidate"]
            )
            adr_id = metadata["id"]
            if entry["recovery_only"]:
                version_content = entry["existing_content"]
            else:
                version_content = record["expected_linear_document_content"]
                if (
                    not isinstance(version_content, str)
                    or record["expected_linear_document_sha256"]
                    != hashlib.sha256(version_content.encode()).hexdigest()
                ):
                    raise ValueError("Linear ADR batch record invalid")
                if not _adr_version_readback_header_matches(
                    version_content, candidate["content"]
                ):
                    raise TrackerConflictError(
                        "Linear ADR batch document readback is not qualified"
                    )
                parsed, _body = _parse_adr_document(
                    {**candidate, "content": version_content},
                    binding,
                    source_body=entry["body"],
                    allow_witness_bound_readback=True,
                )
                if parsed != metadata:
                    raise TrackerConflictError(
                        "Linear ADR batch document readback is not qualified"
                    )
                read_probe(
                    record["document_probe_id"],
                    _adr_qualification_probe_title(
                        adr_id, "document", candidate["content"]
                    ),
                    version_content,
                )
            witness = _adr_witness_document(
                binding, metadata, {**candidate, "content": version_content},
                entry["body"],
            )
            witness_content = record["expected_linear_witness_content"]
            if (
                not isinstance(witness_content, str)
                or record["expected_linear_witness_sha256"]
                != hashlib.sha256(witness_content.encode()).hexdigest()
            ):
                raise ValueError("Linear ADR batch record invalid")
            # The witness keeps the closed modelled serialization that every
            # ordinary read already requires; the probe proves Linear returns it.
            if not _exact_adr_document_matches(
                {**witness, "content": witness_content}, witness
            ):
                raise TrackerConflictError(
                    "Linear ADR batch witness readback is not qualified"
                )
            read_probe(
                record["witness_probe_id"],
                _adr_qualification_probe_title(adr_id, "witness", witness["content"]),
                witness_content,
            )
            profiles.append((candidate, witness, version_content, witness_content))
        return profiles

    def import_adr_batch(
        self, project: Project, records: tuple[dict, ...]
    ) -> list[Adr]:
        """Import one finite historical manifest with a reciprocal asserted graph.

        Linear has no multi-Document transaction. The complete hypothetical graph is
        checked before the first effect; a partial write is unreadable and replay of
        the identical manifest fills only deterministic, byte-identical slots.
        Every version and witness is qualified before the first effect by a
        non-authoritative probe holding its complete provider readback.
        """
        binding, documents, by_id, entries, comments = self._prepare_adr_batch(
            project, records, profiled=True
        )
        profiles = self._verify_adr_batch_qualification(binding, by_id, entries)
        hypothetical = list(documents)
        for candidate, witness, version_content, witness_content in profiles:
            # Imports write a version before its witness.  A missing witness can
            # therefore be the exact interrupted-create state and is recoverable,
            # but a surviving witness without its version cannot result from that
            # order.  Refuse that orphan before adding any hypothetical slot: doing
            # otherwise would authorize recreating a deleted version on replay.
            if (
                by_id.get(candidate["id"]) is None
                and by_id.get(witness["id"]) is not None
            ):
                raise TrackerConflictError(
                    "Linear ADR batch witness has no matching version"
                )
            for expected, provider_content in (
                (candidate, version_content),
                (witness, witness_content),
            ):
                current = by_id.get(expected["id"])
                if current is None:
                    hypothetical.append({**expected, "content": provider_content})
                elif current.get("content") != provider_content or not all(
                    current.get(key) == value
                    for key, value in expected.items()
                    if key != "content"
                ):
                    raise TrackerConflictError("Linear ADR batch slot diverged")
        chains = _adr_chain(hypothetical, binding)
        for entry in entries:
            metadata = entry["metadata"]
            versions = chains.get(metadata["id"])
            if versions is None or len(versions) != 1 or versions[0][0] != metadata:
                raise TrackerConflictError("Linear ADR batch history diverged")
        # Batch writes create every reciprocal comment before the first Document.
        # Thus a missing comment is only a recoverable pre-Document interruption when
        # no slot from this manifest has become durable yet.  Once a version or its
        # witness exists, recreating a missing reciprocal comment would mask an
        # external deletion from an otherwise completed (or advancing) batch.
        durable_document = any(
            by_id.get(slot["id"]) is not None
            for candidate, witness, _version, _witness in profiles
            for slot in (candidate, witness)
        )
        pending_comments = (
            set()
            if durable_document
            else {
                _adr_issue_link(binding, adr_id, issue_id)[0]
                for adr_id, issue_id, _native_id in comments
            }
        )
        self._validate_adr_graph(
            chains, binding, pending_comments=pending_comments
        )
        # A durable manifest has already crossed the comment-before-Document
        # boundary.  Do not call the write-capable helper on replay: a comment
        # can disappear after the graph read, and the final snapshot must refuse
        # that external deletion rather than recreate it.
        if not durable_document:
            for adr_id, issue_id, native_id in comments:
                self._create_adr_issue_link(binding, adr_id, issue_id, native_id)
        for entry, (_candidate, _witness, version_content, witness_content) in zip(
            entries, profiles
        ):
            self._create_adr_document(
                binding,
                entry["metadata"],
                entry["body"],
                readback_content=None if entry["recovery_only"] else version_content,
                witness_readback=witness_content,
                new_body=False,
            )
        _, fresh = self._adr_snapshot(project)
        return [
            self._adr_model(fresh[entry["metadata"]["id"]])
            for entry in entries
        ]

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
        source_only_issue_refs: tuple[str, ...] = (),
    ) -> Adr:
        """Store a bounded historical snapshot; this never invokes acceptance."""
        missing_relations = sorted(
            name for name, value in (
                ("supersedes", supersedes),
                ("superseded_by", superseded_by),
                ("issues", issue_refs),
            ) if value is _UNSPECIFIED_ADR_RELATION
        )
        if supersedes is _UNSPECIFIED_ADR_RELATION:
            supersedes = ()
        if superseded_by is _UNSPECIFIED_ADR_RELATION:
            superseded_by = None
        if issue_refs is _UNSPECIFIED_ADR_RELATION:
            issue_refs = ()
        if not isinstance(source_only_issue_refs, tuple):
            raise ValueError("Linear ADR historical source-only references invalid")
        try:
            migration_source_only_issue_refs(list(source_only_issue_refs))
        except TrackerConflictError as exc:
            raise ValueError("Linear ADR historical source-only references invalid") from exc
        source_tracker = source_ref.split(":", 1)[0]
        if source_tracker not in {"youtrack", "linear", "ghprojects"}:
            source_tracker = "youtrack"  # Legacy historical imports used opaque refs.
        digest = hashlib.sha256(body.encode()).hexdigest()
        if (
            not isinstance(adr_id, str)
            or _ADR_ID.fullmatch(adr_id) is None
            or not isinstance(title, str)
            or not title.strip()
            or historical_status not in _ADR_STATUSES
            or not isinstance(source_ref, str)
            or not source_ref
            or expected_source_sha256 != digest
            or not isinstance(supersedes, tuple)
            or not isinstance(issue_refs, tuple)
            or len(supersedes) > _ADR_RELATION_LIMIT
            or len(issue_refs) > _ADR_RELATION_LIMIT
            or len(supersedes) != len(set(supersedes))
            or any(
                not isinstance(issue_ref, str)
                or _SAFE_ID.fullmatch(issue_ref) is None
                for issue_ref in issue_refs
            )
            or len(issue_refs) != len(set(issue_refs))
            or adr_id in supersedes
            or superseded_by == adr_id
            or (superseded_by is not None and superseded_by in supersedes)
            or (
                (historical_status == "superseded") != (superseded_by is not None)
                and not (
                    historical_status == "superseded"
                    and superseded_by is None
                    and "superseded_by" in missing_relations
                )
            )
            or (
                bool(supersedes)
                and historical_status not in {"accepted", "superseded"}
            )
            or any(
                v is not None and (type(v) is not int or v < 0)
                for v in (source_created, source_updated)
            )
        ):
            raise ValueError("Linear ADR historical import invalid")
        partial_import = False
        interrupted_pair = None
        try:
            binding, chains = self._adr_snapshot(project)
        except TrackerConflictError as error:
            if str(error) not in {
                "Linear ADR supersession relation is not reciprocal",
                "Linear ADR version witness is missing",
            }:
                raise
            binding, documents = self._adr_documents(project)
            if str(error) == "Linear ADR version witness is missing":
                matches = []
                document_ids = {
                    raw.get("id") for raw in documents if isinstance(raw, dict)
                }
                for raw in documents:
                    if not isinstance(raw, dict) or not str(
                        raw.get("content", "")
                    ).startswith(_ADR_HEADER):
                        continue
                    interrupted_metadata, interrupted_body = _parse_adr_document(
                        raw, binding, allow_unbound_body=True
                    )
                    if _adr_witness_id(
                        binding["project_id"],
                        interrupted_metadata["id"],
                        interrupted_metadata["sequence"],
                    ) not in document_ids:
                        matches.append(
                            (interrupted_metadata, interrupted_body, raw)
                        )
                if len(matches) != 1:
                    raise TrackerConflictError(
                        "Linear ADR interrupted import has ambiguous witness"
                    ) from None
                interrupted_metadata, interrupted_body, interrupted_raw = matches[0]
                remaining = [
                    raw for raw in documents if raw is not interrupted_raw
                ]
                complete_chains = _adr_chain(remaining, binding)
                if (
                    interrupted_metadata["id"] == adr_id
                    and interrupted_metadata["sequence"] == 0
                    and interrupted_metadata["body_sha256"] == digest
                ):
                    interrupted_body = body
                else:
                    predecessors = complete_chains.get(interrupted_metadata["id"])
                    if (
                        not predecessors
                        or interrupted_metadata["sequence"]
                        != predecessors[-1][0]["sequence"] + 1
                        or interrupted_metadata["body_sha256"]
                        != predecessors[-1][0]["body_sha256"]
                    ):
                        raise TrackerConflictError(
                            "Linear ADR interrupted import source is not derivable"
                        ) from None
                    interrupted_body = predecessors[-1][1].get(
                        _ADR_BOUND_SOURCE_BODY
                    )
                    if not isinstance(interrupted_body, str):
                        raise TrackerConflictError(
                            "Linear ADR interrupted import source is not bound"
                        ) from None
                witness = _adr_witness_document(
                    binding, interrupted_metadata, interrupted_raw, interrupted_body
                )
                chains = _adr_chain([*documents, witness], binding)
                interrupted_pair = (
                    interrupted_metadata,
                    interrupted_body,
                    interrupted_raw,
                )
            else:
                chains = _adr_chain(documents, binding)
            partial_import = True
        related_ids = {*supersedes}
        if superseded_by is not None:
            related_ids.add(superseded_by)
        for related_id in sorted(related_ids):
            if related_id not in chains:
                raise AdrUnavailableError(related_id)
        canonical_issue_refs, issue_native_ids = self._canonicalize_adr_issue_refs(
            issue_refs, binding
        )
        metadata = {
            "schema": _ADR_SCHEMA,
            "project_id": binding["project_id"],
            "team_id": binding["team_id"],
            "id": adr_id,
            "title": title.strip(),
            "status": historical_status,
            "sequence": 0,
            "previous_id": None,
            "previous_sha256": None,
            "body_sha256": digest,
            "origin": {
                "kind": "migration",
                "source_tracker": source_tracker,
                "source_ref": source_ref,
                "source_created": source_created,
                "source_updated": source_updated,
                "source_body_sha256": digest,
                "missing_relations": missing_relations,
                **({
                    "source_only_issue_refs": list(source_only_issue_refs),
                } if source_only_issue_refs else {}),
            },
            "relations": {
                "supersedes": list(supersedes),
                "superseded_by": superseded_by,
                "issues": list(canonical_issue_refs),
            },
        }
        candidate = {
            "id": _adr_document_id(binding["project_id"], adr_id, 0),
            "title": _adr_document_title(metadata),
            "content": _adr_document_content(metadata, body),
            "project": {"id": binding["project_id"]},
            "archivedAt": None,
        }
        _parse_adr_document(candidate, binding)
        if adr_id in chains:
            if not _exact_adr_document_matches(
                chains[adr_id][0][1], candidate
            ):
                raise TrackerConflictError(
                    "Linear ADR migration conflicts with existing origin"
                )
            if not partial_import:
                return self._adr_model(chains[adr_id])
            if len(chains[adr_id]) != 1:
                raise TrackerConflictError(
                    "Linear ADR interrupted import has unexpected versions"
                )
        elif partial_import:
            raise TrackerConflictError(
                "Linear ADR graph conflict is not this interrupted import"
            )

        interrupted_slot_matches = (
            interrupted_pair is not None
            and interrupted_pair[0]["id"] == adr_id
            and interrupted_pair[0]["sequence"] == 0
            and _exact_adr_document_matches(interrupted_pair[2], candidate)
        )
        changes = []
        for target_id in sorted(supersedes):
            target = chains[target_id]
            if target[-1][0]["status"] == "superseded" and partial_import:
                if len(target) < 2:
                    raise TrackerConflictError(
                        "Linear ADR interrupted import target has no predecessor"
                    )
                expected_metadata, _ = self._next_adr_metadata(
                    target[-2], status="superseded", replacement_id=adr_id
                )
                if target[-1][0] != expected_metadata:
                    raise TrackerConflictError(
                        "Linear ADR interrupted import target diverged"
                    )
                if (
                    interrupted_pair is not None
                    and target[-1][1]["id"] == interrupted_pair[2]["id"]
                ):
                    interrupted_slot_matches = True
            else:
                if target[-1][0]["status"] != "accepted":
                    raise TrackerConflictError(
                        "Linear ADR import supersedes a non-accepted ADR"
                    )
                target_metadata, target_body = self._next_adr_metadata(
                    target[-1], status="superseded", replacement_id=adr_id
                )
                changes.append((target[-1], target_metadata, target_body))
        if superseded_by is not None:
            replacement = chains[superseded_by]
            if (
                partial_import
                and len(replacement) >= 2
                and adr_id in replacement[-1][0]["relations"]["supersedes"]
            ):
                expected_metadata, _ = self._next_adr_metadata(
                    replacement[-2], supersedes_id=adr_id
                )
                if replacement[-1][0] != expected_metadata:
                    raise TrackerConflictError(
                        "Linear ADR interrupted import replacement diverged"
                    )
                if (
                    interrupted_pair is not None
                    and replacement[-1][1]["id"] == interrupted_pair[2]["id"]
                ):
                    interrupted_slot_matches = True
            else:
                if replacement[-1][0]["status"] != "accepted":
                    raise TrackerConflictError(
                        "Linear ADR import replacement is not accepted"
                    )
                replacement_metadata, replacement_body = self._next_adr_metadata(
                    replacement[-1], supersedes_id=adr_id
                )
                changes.append(
                    (replacement[-1], replacement_metadata, replacement_body)
                )
        if interrupted_pair is not None and not interrupted_slot_matches:
            raise TrackerConflictError(
                "Linear ADR graph conflict is not this interrupted import"
            )
        if partial_import:
            hypothetical = {key: list(value) for key, value in chains.items()}
            for _previous, related_metadata, related_body in changes:
                related_candidate = {
                    "id": _adr_document_id(
                        binding["project_id"], related_metadata["id"],
                        related_metadata["sequence"],
                    ),
                    "title": _adr_document_title(related_metadata),
                    "content": _adr_document_content(
                        related_metadata, related_body
                    ),
                    "project": {"id": binding["project_id"]},
                    "archivedAt": None,
                }
                hypothetical[related_metadata["id"]].append(
                    (related_metadata, related_candidate)
                )
            self._validate_adr_graph(hypothetical, binding)
        # Only a version 0 slot that does not exist yet carries a new body; the
        # existing exact slot and the interrupted slot already have a proven
        # rendering, an unchanged related body only if the model verifies it.
        new_body = adr_id not in chains
        _preflight_adr_body_readback(
            body,
            allow_foundry_adr_0001=_is_foundry_adr_0001_profile_chain(metadata),
            new_body=new_body,
        )
        if interrupted_pair is not None:
            _preflight_adr_body_readback(
                interrupted_pair[1],
                allow_foundry_adr_0001=_is_foundry_adr_0001_profile_chain(
                    interrupted_pair[0]
                ),
                new_body=False,
            )
        related_new = [
            not _is_stored_adr_body(previous, related_body)
            for previous, _related_metadata, related_body in changes
        ]
        for (_previous, related_metadata, related_body), related_is_new in zip(
            changes, related_new
        ):
            _preflight_adr_body_readback(
                related_body,
                allow_foundry_adr_0001=_is_foundry_adr_0001_profile_chain(
                    related_metadata
                ),
                new_body=related_is_new,
            )
        for issue_id in canonical_issue_refs:
            self._create_adr_issue_link(
                binding, adr_id, issue_id, issue_native_ids[issue_id]
            )
        if interrupted_pair is not None:
            interrupted_metadata, interrupted_body, _raw = interrupted_pair
            self._create_adr_document(
                binding, interrupted_metadata, interrupted_body, new_body=False
            )
        self._create_adr_document(binding, metadata, body, new_body=new_body)
        for (_previous, related_metadata, related_body), related_is_new in zip(
            changes, related_new
        ):
            self._create_adr_document(
                binding, related_metadata, related_body, new_body=related_is_new
            )
        _, fresh = self._adr_snapshot(project)
        if fresh[adr_id][-1][0] != metadata:
            raise TrackerConflictError("Linear ADR import diverged after write")
        return self._adr_model(fresh[adr_id])

    def set_adr_status(
        self,
        adr: Adr,
        status: str,
        project: Project | None = None,
    ) -> None:
        project = project or self._project()
        try:
            _, chains = self._adr_snapshot(project)
        except TrackerConflictError as error:
            if str(error) != "Linear ADR version witness is missing":
                raise
            binding, documents = self._adr_documents(project)
            previous, _dangling = self._interrupted_mutation_predecessor(
                binding, documents, adr, "status"
            )
            old = previous[0]
            if (
                old["id"] != adr.id
                or status == "superseded"
                or status not in _ADR_TRANSITIONS[old["status"]]
            ):
                raise TrackerConflictError("Linear ADR interrupted status binding invalid")
            metadata, body = self._next_adr_metadata(
                previous, status=status
            )
            self._recover_exact_missing_witness(
                project, binding, documents, metadata, body
            )
            return
        versions = chains.get(adr.id)
        if versions is None or versions[-1][1]["id"] != adr.ref:
            raise TrackerConflictError("Linear ADR snapshot is stale")
        old = versions[-1][0]
        if status == old["status"]:
            return
        if status == "superseded":
            raise TrackerConflictError(
                "Linear ADR supersession requires replacement id"
            )
        if status not in _ADR_TRANSITIONS[old["status"]]:
            raise TrackerConflictError("Linear ADR status transition refused")
        metadata, body = self._next_adr_metadata(versions[-1], status=status)
        self._append_adr_version(project, versions[-1], metadata, body)

    def supersede_adr(
        self, adr: Adr, replacement_id: str, project: Project | None = None
    ) -> None:
        project = project or self._project()
        try:
            _, chains = self._adr_snapshot(project)
        except TrackerConflictError as error:
            if str(error) not in {
                "Linear ADR supersession relation is not reciprocal",
                "Linear ADR version witness is missing",
            }:
                raise
            self._recover_supersession_pair(project, adr, replacement_id)
            return
        versions, replacement = chains.get(adr.id), chains.get(replacement_id)
        if (
            versions is not None
            and replacement is not None
            and versions[-1][0]["status"] == "superseded"
            and versions[-1][0]["relations"]["superseded_by"] == replacement_id
            and adr.id in replacement[-1][0]["relations"]["supersedes"]
        ):
            return
        if (
            versions is None
            or replacement is None
            or adr.id == replacement_id
            or versions[-1][1]["id"] != adr.ref
            or versions[-1][0]["status"] != "accepted"
            or replacement[-1][0]["status"] != "accepted"
        ):
            raise TrackerConflictError("Linear ADR supersession binding invalid")
        metadata, body = self._next_adr_metadata(
            versions[-1], status="superseded", replacement_id=replacement_id
        )
        replacement_metadata, replacement_body = self._next_adr_metadata(
            replacement[-1], supersedes_id=adr.id
        )
        self._append_adr_versions(
            project,
            [
                (replacement[-1], replacement_metadata, replacement_body),
                (versions[-1], metadata, body),
            ],
        )

    def _recover_supersession_pair(
        self, project: Project, adr: Adr, replacement_id: str
    ) -> None:
        """Complete only the exact second half of a proven interrupted pair."""
        binding, documents = self._adr_documents(project)
        dangling = self._unwitnessed_adr_versions(binding, documents)
        if len(dangling) > 1:
            raise TrackerConflictError(
                "Linear ADR interrupted supersession has ambiguous witness"
            )
        dangling_pair = dangling[0] if dangling else None
        remaining = [
            raw for raw in documents
            if dangling_pair is None or raw is not dangling_pair[1]
        ]
        chains = _adr_chain(remaining, binding)
        source, replacement = chains.get(adr.id), chains.get(replacement_id)
        if (
            source is None
            or replacement is None
            or source[-1][1]["id"] != adr.ref
            or source[-1][0]["status"] != "accepted"
            or source[-1][0]["relations"]["superseded_by"] is not None
            or replacement[-1][0]["status"] != "accepted"
        ):
            raise TrackerConflictError(
                "Linear ADR interrupted supersession does not match exact pair"
            )

        replacement_complete = adr.id in replacement[-1][0]["relations"]["supersedes"]
        if replacement_complete and len(replacement) < 2:
            raise TrackerConflictError(
                "Linear ADR interrupted supersession does not match exact pair"
            )
        replacement_previous = replacement[-2] if replacement_complete else replacement[-1]
        if replacement_complete and adr.id in replacement_previous[0]["relations"][
            "supersedes"
        ]:
            raise TrackerConflictError(
                "Linear ADR interrupted supersession does not match exact pair"
            )
        replacement_metadata, replacement_body = self._next_adr_metadata(
            replacement_previous, supersedes_id=adr.id
        )
        replacement_candidate = {
            "id": _adr_document_id(
                binding["project_id"], replacement_id,
                replacement_metadata["sequence"],
            ),
            "title": _adr_document_title(replacement_metadata),
            "content": _adr_document_content(
                replacement_metadata, replacement_body
            ),
            "project": {"id": binding["project_id"]},
            "archivedAt": None,
        }
        if replacement_complete and (
            replacement[-1][0] != replacement_metadata
            or not _exact_adr_document_matches(
                replacement[-1][1], replacement_candidate
            )
        ):
            raise TrackerConflictError(
                "Linear ADR interrupted supersession does not match exact pair"
            )

        source_metadata, source_body = self._next_adr_metadata(
            source[-1], status="superseded", replacement_id=replacement_id
        )
        source_candidate = {
            "id": _adr_document_id(
                binding["project_id"], adr.id, source_metadata["sequence"]
            ),
            "title": _adr_document_title(source_metadata),
            "content": _adr_document_content(source_metadata, source_body),
            "project": {"id": binding["project_id"]},
            "archivedAt": None,
        }

        dangling_side = None
        dangling_witness = None
        if dangling_pair is not None:
            dangling_metadata, dangling_raw = dangling_pair
            if (
                dangling_metadata == replacement_metadata
                and _exact_adr_document_matches(
                    dangling_raw, replacement_candidate
                )
            ):
                dangling_side = "replacement"
                dangling_witness = _adr_witness_document(
                    binding,
                    replacement_metadata,
                    dangling_raw,
                    replacement_body,
                )
            elif (
                dangling_metadata == source_metadata
                and _exact_adr_document_matches(dangling_raw, source_candidate)
            ):
                dangling_side = "source"
                dangling_witness = _adr_witness_document(
                    binding, source_metadata, dangling_raw, source_body
                )
            else:
                raise TrackerConflictError(
                    "Linear ADR interrupted supersession does not match exact pair"
                )
        if replacement_complete != (dangling_side != "replacement"):
            raise TrackerConflictError(
                "Linear ADR interrupted supersession does not match exact pair"
            )

        hypothetical_documents = list(documents)
        if dangling_witness is not None:
            hypothetical_documents.append(dangling_witness)
        if dangling_side != "source":
            hypothetical_documents.extend(
                [
                    source_candidate,
                    _adr_witness_document(
                        binding,
                        source_metadata,
                        source_candidate,
                        source_body,
                    ),
                ]
            )
        hypothetical = _adr_chain(hypothetical_documents, binding)
        self._validate_adr_graph(hypothetical, binding)

        # Supersession never changes a body: each is new only when its stored
        # rendering is not verified by the model (`_is_stored_adr_body`).
        source_new = not _is_stored_adr_body(source[-1], source_body)
        replacement_new = not _is_stored_adr_body(
            replacement_previous, replacement_body
        )
        _preflight_adr_body_readback(
            source_body,
            allow_foundry_adr_0001=_is_foundry_adr_0001_profile_chain(
                source_metadata
            ),
            new_body=source_new,
        )
        if dangling_side == "replacement":
            _preflight_adr_body_readback(
                replacement_body,
                allow_foundry_adr_0001=_is_foundry_adr_0001_profile_chain(
                    replacement_metadata
                ),
                new_body=replacement_new,
            )
            self._create_adr_document(
                binding, replacement_metadata, replacement_body,
                new_body=replacement_new,
            )
        self._create_adr_document(
            binding, source_metadata, source_body, new_body=source_new
        )
        _, fresh = self._adr_snapshot(project)
        if (
            fresh[adr.id][-1][0] != source_metadata
            or fresh[replacement_id][-1][0] != replacement_metadata
        ):
            raise TrackerConflictError("Linear ADR supersession recovery diverged")

    def _update_adr_body(
        self, adr: Adr, expected_body: str, updated_body: str, project: Project | None
    ) -> bool:
        project = project or self._project()
        try:
            _, chains = self._adr_snapshot(project)
        except TrackerConflictError as error:
            if str(error) != "Linear ADR version witness is missing":
                raise
            binding, documents = self._adr_documents(project)
            previous, _dangling = self._interrupted_mutation_predecessor(
                binding, documents, adr, "body"
            )
            if not _adr_readback_content_matches(
                previous[1].get("content"), expected_body
            ):
                raise TrackerConflictError("Linear ADR interrupted body predecessor diverged")
            old = previous[0]
            old_header, sep, _ = expected_body.partition("\n-->\n\n")
            new_header, new_sep, body = updated_body.partition("\n-->\n\n")
            if (
                old["id"] != adr.id
                or old["status"] in {"deprecated", "superseded"}
                or not sep or not new_sep or old_header != new_header
                or updated_body == expected_body
            ):
                raise TrackerConflictError("Linear ADR interrupted body binding invalid")
            metadata, body = self._next_adr_metadata(
                previous, body=body
            )
            self._recover_exact_missing_witness(
                project, binding, documents, metadata, body
            )
            return True
        versions = chains.get(adr.id)
        if versions is None or versions[-1][1]["id"] != adr.ref:
            raise TrackerConflictError("Linear ADR body changed before edit")
        if not _adr_readback_content_matches(
            versions[-1][1].get("content"), expected_body
        ):
            if self._is_exact_adr_body_replay(
                versions, expected_body, updated_body
            ):
                return False
            raise TrackerConflictError("Linear ADR body changed before edit")
        if updated_body == expected_body:
            return False
        if versions[-1][0]["status"] in {"deprecated", "superseded"}:
            raise TrackerConflictError("Linear ADR terminal body is immutable")
        old_header, sep, _ = expected_body.partition("\n-->\n\n")
        new_header, new_sep, body = updated_body.partition("\n-->\n\n")
        if not sep or not new_sep or old_header != new_header:
            raise TrackerConflictError(
                "Linear ADR metadata edit requires a typed operation"
            )
        metadata, body = self._next_adr_metadata(versions[-1], body=body)
        self._append_adr_version(project, versions[-1], metadata, body)
        return True
