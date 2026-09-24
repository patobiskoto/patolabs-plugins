"""Fail-closed Linear adapter for Foundry's provider-neutral Tracker port.

The adapter uses only explicit registry coordinates: canonical repository identity,
Linear team UUID, Linear project UUID, and a complete normalized-state -> workflow-
state UUID map. Runtime checkout resolution matches the actual canonical Git remote;
it never selects a binding from an environment alias, basename, title, or issue key.

Linear does not expose a compare-and-swap precondition for existing-issue replacement.
Those writes are therefore unavailable: a local lock or a readback cannot prevent an
external writer from being overwritten.  The supported writes are atomic creation and
additive provider mutations which do not replace existing issue values.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

from foundry import config, registry
from foundry.models import Adr, Issue, Link, Project, TransitionContext
from foundry.trackers.base import (
    AcceptanceSyncUnavailableError,
    AdrUnavailableError,
    BodyUpdateUnavailableError,
    IssueUnavailableError,
    Tracker,
    TrackerCapabilityUnavailableError,
    TrackerConflictError,
)


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
    "cockpit-evidence",
    }
)
_ADR_SCHEMA = "foundry-linear-adr.v1"
_ADR_HEADER = "<!-- foundry-linear-adr.v1\n"
_ADR_DOCUMENT_PREFIX = "[Foundry ADR] "
_ADR_WITNESS_SCHEMA = "foundry-linear-adr-witness.v1"
_ADR_WITNESS_HEADER = "<!-- foundry-linear-adr-witness.v1\n"
_ADR_WITNESS_PREFIX = "[Foundry ADR witness] "
_ADR_ISSUE_LINK_SCHEMA = "foundry-linear-adr-issue-link.v1"
_ADR_ID = re.compile(r"[A-Z][A-Z0-9_-]*-ADR-[0-9]{4}\Z")
_ADR_STATUSES = frozenset({"proposed", "accepted", "deprecated", "superseded"})
_ADR_RELATION_LIMIT = 100
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

_ISSUE_CREATE = """
mutation FoundryLinearIssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { id identifier } }
}
"""

_RELATION_CREATE = """
mutation FoundryLinearIssueRelationCreate($input: IssueRelationCreateInput!) {
  issueRelationCreate(input: $input) {
    success issueRelation { id type issue { id identifier } relatedIssue { id identifier } }
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


class LinearTrackerError(RuntimeError):
    """Sanitized transport/provider failure with no response or credential echo."""

    def __init__(self, operation: str, status: int | None, code: str):
        self.operation = operation
        self.status = status
        self.code = code
        rendered_status = str(status) if status is not None else "unavailable"
        super().__init__(f"Linear {operation} -> {rendered_status} ({code})")


class LinearBindingError(RuntimeError):
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


def _adr_document_id(project_id: str, adr_id: str, sequence: int) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL, f"{_ADR_SCHEMA}:{project_id}:{adr_id}:{sequence}"
        )
    )


def _adr_witness_id(project_id: str, adr_id: str, sequence: int) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{_ADR_WITNESS_SCHEMA}:{project_id}:{adr_id}:{sequence}",
        )
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
    comment_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{_ADR_ISSUE_LINK_SCHEMA}:{binding['project_id']}:{adr_id}:{issue_id}",
        )
    )
    return comment_id, f"Foundry ADR relation (reciprocal).\n\n{canonical}"


def _adr_document_title(metadata: dict) -> str:
    return f"{_ADR_DOCUMENT_PREFIX}{metadata['id']} / v{metadata['sequence']:04d} / {metadata['title']}"


def _adr_document_content(metadata: dict, body: str) -> str:
    header = json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return f"{_ADR_HEADER}{header}\n-->\n\n{body}"


def _adr_witness_document(binding: dict, metadata: dict, raw: dict) -> dict:
    witness = {
        "schema": _ADR_WITNESS_SCHEMA,
        "project_id": binding["project_id"],
        "team_id": binding["team_id"],
        "adr_id": metadata["id"],
        "sequence": metadata["sequence"],
        "document_id": raw["id"],
        "document_sha256": hashlib.sha256(raw["content"].encode()).hexdigest(),
    }
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
    encoded, separator, remainder = content[len(_ADR_WITNESS_HEADER) :].partition(
        "\n-->"
    )
    if not separator or remainder:
        raise LinearTrackerError("adr.witness.normalize", None, "invalid_response")
    try:
        witness = json.loads(encoded)
    except (TypeError, ValueError):
        raise LinearTrackerError(
            "adr.witness.normalize", None, "invalid_response"
        ) from None
    required = {
        "schema",
        "project_id",
        "team_id",
        "adr_id",
        "sequence",
        "document_id",
        "document_sha256",
    }
    adr_id = witness.get("adr_id") if isinstance(witness, dict) else None
    sequence = witness.get("sequence") if isinstance(witness, dict) else None
    if (
        not isinstance(witness, dict)
        or set(witness) != required
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
    ):
        raise LinearTrackerError("adr.witness.normalize", None, "invalid_response")
    return witness


def _parse_adr_document(raw: dict, binding: dict) -> tuple[dict, str]:
    if not isinstance(raw, dict) or raw.get("archivedAt") is not None:
        raise LinearTrackerError("adr.normalize", None, "invalid_response")
    content = raw.get("content")
    if not isinstance(content, str) or not content.startswith(_ADR_HEADER):
        raise LinearTrackerError("adr.normalize", None, "invalid_response")
    encoded, separator, body = content[len(_ADR_HEADER) :].partition("\n-->\n\n")
    if not separator:
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
        or metadata["body_sha256"] != hashlib.sha256(body.encode()).hexdigest()
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
        or (metadata["status"] == "superseded")
        != (relations["superseded_by"] is not None)
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
        valid_origin = (
            set(origin)
            == {
                "kind",
                "source_tracker",
                "source_ref",
                "source_created",
                "source_updated",
                "source_body_sha256",
            }
            and origin["source_tracker"] == "youtrack"
            and isinstance(origin["source_ref"], str)
            and bool(origin["source_ref"])
            and all(
                v is None or (type(v) is int and v >= 0)
                for v in (origin["source_created"], origin["source_updated"])
            )
            and isinstance(origin["source_body_sha256"], str)
            and _DIGEST.fullmatch(origin["source_body_sha256"]) is not None
            and (
                sequence != 0 or origin["source_body_sha256"] == metadata["body_sha256"]
            )
        )
    if (
        not valid_origin
        or raw.get("id") != _adr_document_id(binding["project_id"], adr_id, sequence)
        or raw.get("title") != _adr_document_title(metadata)
        or raw.get("project", {}).get("id") != binding["project_id"]
        or content != _adr_document_content(metadata, body)
    ):
        raise LinearTrackerError("adr.normalize", None, "invalid_response")
    return metadata, body


def _adr_chain(documents: list[dict], binding: dict) -> dict:
    chains = {}
    witnesses = {}
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
        metadata, _ = _parse_adr_document(raw, binding)
        chains.setdefault(metadata["id"], []).append((metadata, raw))
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
                    or metadata["relations"]["issues"]
                    != previous["relations"]["issues"]
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
                ):
                    raise TrackerConflictError("Linear ADR version chain diverged")
    if set(witnesses) != observed:
        raise TrackerConflictError("Linear ADR witness has no matching version")
    return chains


class LinearTracker(Tracker):
    name = "linear"
    requires_mutation_binding = True
    acceptance_sync_supported = False
    bounded_transition_proofs = True
    append_only_lifecycle_supported = True
    acceptance_proof_projection_supported = True
    cockpit_evidence_projection_supported = True

    def __init__(
        self,
        *,
        token: str | None = None,
        transport=None,
        endpoint: str = LINEAR_GRAPHQL_ENDPOINT,
    ):
        if endpoint != LINEAR_GRAPHQL_ENDPOINT:
            raise ValueError("Linear endpoint must be the official GraphQL endpoint")
        self.endpoint = endpoint
        self.token = token if token is not None else config.require("LINEAR_API_TOKEN")
        if not isinstance(self.token, str) or not self.token:
            raise ValueError("credential Linear invalid")
        self._transport = transport
        self._active_project: Project | None = None

    # ---- transport -------------------------------------------------
    def _graphql(self, document: str, variables: dict, operation: str) -> dict:
        if self._transport is not None:
            try:
                envelope = self._transport(document, variables)
            except LinearTrackerError:
                raise
            except Exception:
                raise LinearTrackerError(operation, None, "transport_error") from None
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
            except LinearTrackerError:
                raise
            except urllib.error.HTTPError as error:
                raise LinearTrackerError(operation, error.code, "http_error") from None
            except (OSError, urllib.error.URLError, TimeoutError):
                raise LinearTrackerError(operation, None, "transport_error") from None
            try:
                envelope = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise LinearTrackerError(
                    operation, status, "invalid_response"
                ) from None
        if not isinstance(envelope, dict):
            raise LinearTrackerError(operation, None, "invalid_response")
        if envelope.get("errors"):
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

    # ---- explicit binding -----------------------------------------
    @staticmethod
    def _binding(project: Project) -> dict:
        if not isinstance(project, Project):
            raise LinearBindingError("project_missing")
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
        try:
            observed = (
                registry.checkout_repository_identity(cwd)
                if checkout_identity is None
                else registry.canonical_repository_identity(checkout_identity)
            )
        except ValueError:
            raise SystemExit(
                "Binding Linear refusé : identité canonique du checkout invalide ou absente."
            ) from None
        project = registry.resolve_canonical_repository(self.name, observed)
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
        elif operation == "acceptance":
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

    def _validate_native_state_history(
        self,
        rows: dict[str, list[tuple[dict, str]]],
        ordered: list[dict],
        native_state_id: str,
        *,
        pending_operation: str | None,
        acceptance_complete: bool,
        done: dict | None,
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

        ordered_ids = [payload["native_state_id"] for payload in ordered]
        cockpit_ids = [
            payload["native_state_id"]
            for payload, _body in rows.get("cockpit-evidence", [])
        ]
        if not ordered_ids:
            ordered_ids = cockpit_ids
        elif any(identifier not in set(ordered_ids) for identifier in cockpit_ids):
            raise TrackerConflictError("Linear native state changed outside lifecycle")
        if not ordered_ids:
            raise TrackerConflictError("Linear lifecycle native state proof malformed")

        names = [state_by_id[identifier] for identifier in ordered_ids]
        if any(
            not self._native_state_can_advance(previous, current)
            for previous, current in zip(names, names[1:])
        ):
            raise TrackerConflictError("Linear native state changed outside lifecycle")

        current_name = state_by_id.get(native_state_id)
        if current_name is None:
            raise TrackerConflictError("Linear lifecycle native state unavailable")
        latest_name = names[-1]
        current_is_durable = native_state_id == ordered_ids[-1]
        pending_forward = (
            pending_operation in {"state-review", "acceptance"}
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
        if not (current_is_durable or pending_forward or pending_done):
            raise TrackerConflictError("Linear native state changed outside lifecycle")
        if (
            current_name == "done"
            and not pending_done
            and (done is None or done.get("native_state_id") != native_state_id)
        ):
            raise TrackerConflictError("Linear native state changed outside lifecycle")

    def _lifecycle_projection(
        self,
        issue_id: str,
        raw: dict,
        *,
        pending_operation: str | None = None,
    ) -> dict:
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
            if native_state_id == done_state_id:
                raise TrackerConflictError(
                    "Linear native state changed outside lifecycle"
                )
            return {
                "state": None,
                "pr_url": None,
                "acceptance_complete": False,
                "in_progress": None,
                "acceptance_by_generation": {},
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
        if review is not None:
            acceptance_complete = review["generation"] in acceptance_by_generation
        if done is not None and not acceptance_complete:
            raise TrackerConflictError("Linear done proof lacks matching acceptance")

        ordered = []
        if in_progress is not None:
            ordered.append(in_progress)
        for review_payload, _review_body in reviews:
            ordered.append(review_payload)
            acceptance = acceptance_by_generation.get(review_payload["generation"])
            if acceptance is not None:
                ordered.append(acceptance)
        if done is not None:
            ordered.append(done)
        self._validate_native_state_history(
            rows,
            ordered,
            native_state_id,
            pending_operation=pending_operation,
            acceptance_complete=acceptance_complete,
            done=done,
        )
        return {
            "state": projected_state,
            "pr_url": (done or review or {}).get("pr_url"),
            "in_progress": in_progress,
            "acceptance_by_generation": acceptance_by_generation,
            "acceptance_complete": acceptance_complete,
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
        native_state_id = state.get("id") if isinstance(state, dict) else None
        if not isinstance(native_state_id, str):
            raise TrackerConflictError("Linear lifecycle native state unavailable")
        bounded_payload = {**payload, "native_state_id": native_state_id}
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
        elif operation == "acceptance":
            previous = projection["acceptance_by_generation"].get(
                payload.get("review_generation"),
            )
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
        if operation not in {"state-review", "acceptance"} and existing:
            raise TrackerConflictError("Linear lifecycle divergent before comment")
        if operation in {"state-review", "acceptance"}:
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
        self._lifecycle_projection(issue_id, fresh)
        return created_now

    def validate_issue_binding(self, project: Project, *issue_ids: str) -> None:
        binding = self._activate(project)
        for issue_id in issue_ids:
            raw = self._read_raw(issue_id)
            self._assert_issue_project(raw, binding)

    def validate_adr_binding(self, project: Project, *adr_ids: str) -> None:
        known = {adr.id for adr in self.list_adrs(project)}
        for adr_id in adr_ids:
            if adr_id not in known:
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

    # ---- reads / normalization ------------------------------------
    def _read_raw(self, issue_id: str) -> dict:
        if not isinstance(issue_id, str) or not issue_id:
            raise IssueUnavailableError(str(issue_id))
        data = self._graphql(_ISSUE_QUERY, {"id": issue_id}, "issue.read")
        raw = data.get("issue")
        if raw is None:
            raise IssueUnavailableError(issue_id)
        if not isinstance(raw, dict):
            raise LinearTrackerError("issue.read", None, "invalid_response")
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

    def _to_issue(self, raw: dict, project: Project | None = None) -> Issue:
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
        lifecycle = self._lifecycle_projection(raw["identifier"], raw)
        if lifecycle["acceptance_complete"]:
            done = total
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
            state=lifecycle["state"] or state_by_id[state["id"]],
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
                issue = self._to_issue(raw, project)
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

    def get_issue(self, issue_id: str) -> Issue:
        project = self._project()
        return self._to_issue(self._read_raw(issue_id), project)

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
            values["labelIds"] = sorted(desired_ids)
            expected["label_ids"] = desired_ids
        return values, expected

    @staticmethod
    def _raw_matches(raw: dict, expected: dict) -> bool:
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
        self._activate(project)
        if isinstance(fields, dict) and "GitHub PR" in fields:
            raise TrackerCapabilityUnavailableError(self.name, "github-pr-projection")
        self._field_names(fields)
        raise TrackerCapabilityUnavailableError(
            self.name,
            "existing-issue-field-replacement",
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
        self._project_lifecycle(issue_id, "state-" + state, payload, project)

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
        if link_type in {"subtask-of", "parent-of"}:
            raise TrackerCapabilityUnavailableError(
                self.name,
                "existing-issue-parent-replacement",
            )
        src_raw, dst_raw = self._read_raw(src_id), self._read_raw(dst_id)
        self._assert_issue_project(src_raw, binding)
        self._assert_issue_project(dst_raw, binding)
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
        data = self._graphql(
            _RELATION_CREATE,
            {
                "input": {
                    "issueId": source["id"],
                    "relatedIssueId": target["id"],
                "type": relation,
                }
            },
            "issue.relation.create",
        )
        payload = self._mutation_payload(
            data,
            "issueRelationCreate",
            "issue.relation.create",
        )
        created = payload.get("issueRelation")
        if (
            not isinstance(created, dict)
            or created.get("type") != relation
                or (created.get("issue") or {}).get("id") != source["id"]
            or (created.get("relatedIssue") or {}).get("id") != target["id"]
        ):
            raise LinearTrackerError("issue.relation.create", None, "invalid_response")
        readback = self._read_raw(src_id)
        if not any(
            link.type == link_type and link.target == dst_id
            for link in self._to_issue(readback, project).links
        ):
            raise TrackerConflictError(
                "Linear relation divergent after write; no retry"
            )

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
        raise BodyUpdateUnavailableError(
            "mise à jour de corps indisponible pour le tracker linear : "
            "aucune précondition atomique anti-écrasement"
        )

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
                metadata, parsed_body = _parse_adr_document(raw, binding)
                key = (metadata["id"], metadata["sequence"])
                if key in versions:
                    raise TrackerConflictError("Linear ADR version slot has a fork")
                versions[key] = (metadata, parsed_body, raw)
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
            or parsed_body != body
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
        self._create_adr_document(binding, metadata, parsed_body)
        return True

    def _validate_adr_graph(self, chains: dict, binding: dict) -> None:
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
            issue_refs.extend((adr_id, issue_id) for issue_id in relations["issues"])
        for adr_id, issue_id in sorted(issue_refs):
            raw = self._read_raw(issue_id)
            self._assert_issue_project(raw, binding)
            comment_id, body = _adr_issue_link(binding, adr_id, issue_id)
            reciprocal = self._read_comment(comment_id, "adr.issue-link.read")
            if (
                not isinstance(reciprocal, dict)
                or reciprocal.get("body") != body
                or (reciprocal.get("issue") or {}).get("id") != raw.get("id")
            ):
                raise TrackerConflictError(
                    "Linear ADR issue relation is not reciprocal"
                )

    @staticmethod
    def _adr_model(versions: list[tuple[dict, dict]]) -> Adr:
        metadata, raw = versions[-1]
        return Adr(
            id=metadata["id"],
            title=metadata["title"],
            status=metadata["status"],
            body=raw["content"],
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

    def _create_adr_document(self, binding: dict, metadata: dict, body: str) -> dict:
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

        def verify_version(raw):
            if raw is None:
                return None
            _parse_adr_document(raw, binding)
            if any(raw.get(k) != v for k, v in expected.items()):
                raise TrackerConflictError(
                    "Linear ADR version slot already has different content"
                )
            return raw

        version = self._create_exact_adr_document(
            expected, "adr.create", verify_version
        )
        witness = _adr_witness_document(binding, metadata, version)

        def verify_witness(raw):
            if raw is None:
                return None
            _parse_adr_witness(raw, binding)
            if any(raw.get(k) != v for k, v in witness.items()):
                raise TrackerConflictError(
                    "Linear ADR witness slot already has different content"
                )
            return raw

        self._create_exact_adr_document(
            witness, "adr.witness.create", verify_witness
        )
        return version

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
        self, binding: dict, adr_id: str, issue_id: str
    ) -> None:
        raw = self._read_raw(issue_id)
        self._assert_issue_project(raw, binding)
        comment_id, body = _adr_issue_link(binding, adr_id, issue_id)

        def verify(comment):
            if comment is None:
                return None
            if (
                not isinstance(comment, dict)
                or comment.get("body") != body
                or (comment.get("issue") or {}).get("id") != raw.get("id")
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
                            "issueId": raw["id"],
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
    ):
        old, raw = previous
        _, old_body = _parse_adr_document(
            raw, {"project_id": old["project_id"], "team_id": old["team_id"]}
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
        return metadata, new_body

    def _append_adr_version(self, project, previous, metadata, body):
        return self._append_adr_versions(
            project, [(previous, metadata, body)]
        )[metadata["id"]]

    def _append_adr_versions(self, project, changes):
        binding, chains = self._adr_snapshot(project)
        for previous, metadata, _body in changes:
            if (
                metadata["id"] not in chains
                or chains[metadata["id"]][-1][1]["id"] != previous[1]["id"]
            ):
                raise TrackerConflictError("Linear ADR changed before version append")
        for _previous, metadata, body in changes:
            self._create_adr_document(binding, metadata, body)
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
        self._create_adr_document(binding, metadata, body)
        _, fresh = self._adr_snapshot(project)
        if metadata["id"] not in fresh or fresh[metadata["id"]][-1][0] != metadata:
            raise TrackerConflictError("Linear ADR creation diverged")
        return self._adr_model(fresh[metadata["id"]])

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
        supersedes: tuple[str, ...] = (),
        superseded_by: str | None = None,
        issue_refs: tuple[str, ...] = (),
    ) -> Adr:
        """Store a bounded historical snapshot; this never invokes acceptance."""
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
            or len(issue_refs) != len(set(issue_refs))
            or adr_id in supersedes
            or superseded_by == adr_id
            or (superseded_by is not None and superseded_by in supersedes)
            or (historical_status == "superseded") != (superseded_by is not None)
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
        binding, chains = self._adr_snapshot(project)
        related_ids = {*supersedes}
        if superseded_by is not None:
            related_ids.add(superseded_by)
        for related_id in sorted(related_ids):
            if related_id not in chains:
                raise AdrUnavailableError(related_id)
        for issue_id in sorted(issue_refs):
            raw_issue = self._read_raw(issue_id)
            self._assert_issue_project(raw_issue, binding)
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
                "source_tracker": "youtrack",
                "source_ref": source_ref,
                "source_created": source_created,
                "source_updated": source_updated,
                "source_body_sha256": digest,
            },
            "relations": {
                "supersedes": list(supersedes),
                "superseded_by": superseded_by,
                "issues": list(issue_refs),
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
            if any(chains[adr_id][0][1].get(k) != v for k, v in candidate.items()):
                raise TrackerConflictError(
                    "Linear ADR migration conflicts with existing origin"
                )
            return self._adr_model(chains[adr_id])

        changes = []
        for target_id in sorted(supersedes):
            target = chains[target_id]
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
        for issue_id in sorted(issue_refs):
            self._create_adr_issue_link(binding, adr_id, issue_id)
        self._create_adr_document(binding, metadata, body)
        for _previous, related_metadata, related_body in changes:
            self._create_adr_document(binding, related_metadata, related_body)
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
        _, chains = self._adr_snapshot(project)
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
        _, chains = self._adr_snapshot(project)
        versions, replacement = chains.get(adr.id), chains.get(replacement_id)
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

    def _update_adr_body(
        self, adr: Adr, expected_body: str, updated_body: str, project: Project | None
    ) -> bool:
        project = project or self._project()
        _, chains = self._adr_snapshot(project)
        versions = chains.get(adr.id)
        if (
            versions is None
            or versions[-1][1]["id"] != adr.ref
            or versions[-1][1]["content"] != expected_body
        ):
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
