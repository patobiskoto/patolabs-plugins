"""Fail-closed Linear adapter for Foundry's provider-neutral Tracker port.

The adapter uses only explicit registry coordinates: canonical repository identity,
Linear team UUID, Linear project UUID, and a complete normalized-state -> workflow-
state UUID map.  It never discovers a binding from a name, title, label, or issue key.

Linear does not expose a compare-and-swap precondition for existing-issue replacement.
Those writes are therefore unavailable: a local lock or a readback cannot prevent an
external writer from being overwritten.  The supported writes are atomic creation and
additive provider mutations which do not replace existing issue values.
"""
from __future__ import annotations

from datetime import datetime
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

from foundry import config, registry
from foundry.models import Adr, Issue, Link, Project
from foundry.trackers.base import (
    AcceptanceSyncUnavailableError,
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
_PR_METADATA = {"foundry_contract": "foundry.linear.v1", "foundry_kind": "github-pr"}


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
attachments(first: 100) {
  nodes { id url metadata }
  pageInfo { hasNextPage endCursor }
}
"""

_ISSUE_QUERY = f"""
query FoundryLinearIssue($id: String!) {{
  issue(id: $id) {{ {_ISSUE_FIELDS}
    comments(first: 100) {{
      nodes {{ id body createdAt }} pageInfo {{ hasNextPage endCursor }}
    }}
  }}
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
query FoundryLinearComment($id: String!) {
  comment(id: $id) { id body issue { id identifier } }
}
"""

_ATTACHMENT_CREATE = """
mutation FoundryLinearAttachmentCreate($input: AttachmentCreateInput!) {
  attachmentCreate(input: $input) {
    success attachment { id url metadata issue { id identifier } }
  }
}
"""


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
            source_origin = (source.scheme.lower(), (source.hostname or "").lower(), source.port or 443)
            target_origin = (
                destination.scheme.lower(), (destination.hostname or "").lower(),
                destination.port or 443,
            )
        except (TypeError, ValueError, UnicodeError):
            raise LinearTrackerError("redirect", code, "cross_origin_redirect") from None
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
        not isinstance(name, str) or not name
        or not isinstance(identifier, str) or _SAFE_ID.fullmatch(identifier) is None
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
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
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


class LinearTracker(Tracker):
    name = "linear"
    requires_mutation_binding = True
    acceptance_sync_supported = False

    def __init__(
        self, *, token: str | None = None, transport=None,
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
                ensure_ascii=False, separators=(",", ":"),
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
                        raise LinearTrackerError(operation, response.status, "response_too_large")
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
                raise LinearTrackerError(operation, status, "invalid_response") from None
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
        if (not isinstance(payload, dict) or payload.get("success") is not True):
            raise LinearTrackerError(operation, None, "mutation_failed")
        return payload

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
        project = registry.resolve("linear", repo)
        self._activate(project)
        return project

    def validate_mutation_repository(self, repo: str, checkout_identity: str) -> None:
        project = registry.resolve("linear", repo)
        binding = self._activate(project)
        try:
            observed = registry.canonical_repository_identity(checkout_identity)
        except ValueError:
            raise SystemExit("Binding Linear refusé : canonical_repo invalide.") from None
        if observed != binding["canonical_repo"]:
            raise SystemExit("Binding Linear refusé : canonical_repo contradictoire.")

    def validate_issue_binding(self, project: Project, *issue_ids: str) -> None:
        binding = self._activate(project)
        for issue_id in issue_ids:
            raw = self._read_raw(issue_id)
            self._assert_issue_project(raw, binding)

    def validate_adr_binding(self, project: Project, *adr_ids: str) -> None:
        self._activate(project)
        raise TrackerCapabilityUnavailableError(self.name, "adr-knowledge-base")

    @staticmethod
    def _assert_issue_project(raw: dict, binding: dict) -> None:
        team = raw.get("team") if isinstance(raw, dict) else None
        project = raw.get("project") if isinstance(raw, dict) else None
        if (not isinstance(team, dict) or team.get("id") != binding["team_id"]
                or not isinstance(project, dict)
                or project.get("id") != binding["project_id"]):
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
            match.group("mark") for line in body.splitlines()
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
        state_by_id = {identifier: name for name, identifier in binding["state_ids"].items()}
        if state["id"] not in state_by_id:
            raise LinearBindingError("state_id_unmapped")

        labels_raw = _connection(raw.get("labels"), "normalize.labels")
        type_by_id = {identifier: name for name, identifier in binding["type_label_ids"].items()}
        label_by_id = {identifier: name for name, identifier in binding["label_ids"].items()}
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
        types = [type_by_id[identifier] for identifier in label_ids if identifier in type_by_id]
        if len(types) > 1:
            raise LinearBindingError("type_labels_ambiguous")
        labels = [label_by_id[identifier] for identifier in label_ids if identifier in label_by_id]

        links: list[Link] = []
        parent = raw.get("parent")
        if parent is not None:
            if not isinstance(parent, dict) or not isinstance(parent.get("identifier"), str):
                raise LinearTrackerError("normalize", None, "invalid_response")
            links.append(Link("subtask-of", "outward", parent["identifier"]))
        for child in _connection(raw.get("children"), "normalize.children"):
            if not isinstance(child.get("identifier"), str):
                raise LinearTrackerError("normalize", None, "invalid_response")
            links.append(Link("parent-of", "outward", child["identifier"]))
        for relation in _connection(raw.get("relations"), "normalize.relations"):
            target = relation.get("relatedIssue")
            if not isinstance(target, dict) or not isinstance(target.get("identifier"), str):
                raise LinearTrackerError("normalize", None, "invalid_response")
            if relation.get("type") == "blocks":
                links.append(Link("blocks", "outward", target["identifier"]))
            elif relation.get("type") == "related":
                links.append(Link("relates", "outward", target["identifier"]))
        for relation in _connection(raw.get("inverseRelations"), "normalize.inverse-relations"):
            target = relation.get("issue")
            if not isinstance(target, dict) or not isinstance(target.get("identifier"), str):
                raise LinearTrackerError("normalize", None, "invalid_response")
            if relation.get("type") == "blocks":
                links.append(Link("depends-on", "inward", target["identifier"]))
            elif relation.get("type") == "related":
                links.append(Link("relates", "inward", target["identifier"]))

        pr_urls = []
        for attachment in _connection(raw.get("attachments"), "normalize.attachments"):
            if attachment.get("metadata") == _PR_METADATA and isinstance(attachment.get("url"), str):
                pr_urls.append(attachment["url"])
        if len(pr_urls) > 1:
            raise LinearBindingError("github_pr_ambiguous")

        milestone = None
        milestone_raw = raw.get("projectMilestone")
        if milestone_raw is not None:
            if not isinstance(milestone_raw, dict) or not isinstance(milestone_raw.get("id"), str):
                raise LinearTrackerError("normalize", None, "invalid_response")
            milestone_by_id = {
                identifier: name for name, identifier in binding["milestone_ids"].items()
            }
            if milestone_raw["id"] not in milestone_by_id:
                raise LinearBindingError("milestone_id_unmapped")
            milestone = milestone_by_id[milestone_raw["id"]]

        body = raw.get("description") or ""
        if not isinstance(body, str):
            raise LinearTrackerError("normalize", None, "invalid_response")
        done, total = self._ac_counts(body)
        priority = raw.get("priority", 0)
        if priority not in _LINEAR_TO_PRIORITY:
            raise LinearTrackerError("normalize", None, "invalid_response")
        comments = []
        if "comments" in raw:
            for comment in _connection(raw["comments"], "normalize.comments")[-10:]:
                if not isinstance(comment.get("body"), str):
                    raise LinearTrackerError("normalize", None, "invalid_response")
                comments.append({"text": comment["body"], "created": _epoch_ms(comment.get("createdAt"))})
        return Issue(
            id=raw["identifier"], title=raw["title"], state=state_by_id[state["id"]],
            priority=_LINEAR_TO_PRIORITY[priority], estimate=raw.get("estimate"),
            milestone=milestone, type=types[0] if types else None, labels=labels,
            ac_done=done, ac_total=total, links=links, pr_url=pr_urls[0] if pr_urls else None,
            body=body, comments=comments, created=_epoch_ms(raw.get("createdAt")),
            updated=_epoch_ms(raw.get("updatedAt")),
        )

    def search(self, project: Project, query: str = "") -> list[Issue]:
        if query:
            raise TrackerCapabilityUnavailableError(self.name, "provider-native-search-query")
        binding = self._activate(project)
        out: list[Issue] = []
        cursor = None
        seen = set()
        for _ in range(_MAX_PAGES):
            data = self._graphql(
                _ISSUES_QUERY,
                {"teamId": binding["team_id"], "projectId": binding["project_id"], "after": cursor},
                "issue.search",
            )
            connection = data.get("issues")
            if not isinstance(connection, dict) or not isinstance(connection.get("nodes"), list):
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
        supported = {"State", "Priority", "Estimate", "Milestone", "Type", "Labels", "GitHub PR"}
        unknown = sorted(set(fields) - supported)
        if unknown:
            raise TrackerCapabilityUnavailableError("linear", f"field:{unknown[0]}")

    def _desired_update(self, raw: dict, project: Project, fields: dict) -> tuple[dict, dict]:
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
                raise ValueError("Linear estimate must be a non-negative integer or null")
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
        current_ids = {node.get("id") for node in labels if isinstance(node.get("id"), str)}
        type_ids = set(binding["type_label_ids"].values())
        desired_ids = set(current_ids)
        if "Labels" in fields:
            requested = fields["Labels"]
            if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
                raise ValueError("Linear Labels must be a list of configured names")
            missing = [name for name in requested if name not in binding["label_ids"]]
            if missing:
                raise LinearBindingError("label_unmapped")
            desired_ids = {binding["label_ids"][name] for name in requested} | (current_ids & type_ids)
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
        if "state_id" in expected and (raw.get("state") or {}).get("id") != expected["state_id"]:
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
                node.get("id") for node in _connection(raw.get("labels"), "issue.readback.labels")
            }
            if actual_ids != expected["label_ids"]:
                return False
        return True

    def create_issue(
        self, project: Project, title: str, body: str,
        fields: dict | None = None, parent: str | None = None,
    ) -> Issue:
        binding = self._activate(project)
        if not isinstance(title, str) or not title or not isinstance(body, str):
            raise ValueError("Linear issue title/body invalid")
        fields = dict(fields or {})
        if "GitHub PR" in fields:
            raise TrackerCapabilityUnavailableError(self.name, "create-with-github-pr")
        skeleton = {
            "labels": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        }
        values, expected = self._desired_update(skeleton, project, fields) if fields else ({}, {})
        input_value = {
            "id": str(uuid.uuid4()), "teamId": binding["team_id"],
            "projectId": binding["project_id"], "title": title, "description": body,
            **values,
        }
        if parent:
            parent_raw = self._read_raw(parent)
            self._assert_issue_project(parent_raw, binding)
            input_value["parentId"] = parent_raw["id"]
        data = self._graphql(_ISSUE_CREATE, {"input": input_value}, "issue.create")
        payload = self._mutation_payload(data, "issueCreate", "issue.create")
        created = payload.get("issue")
        if not isinstance(created, dict) or not isinstance(created.get("identifier"), str):
            raise LinearTrackerError("issue.create", None, "invalid_response")
        raw = self._read_raw(created["identifier"])
        self._assert_issue_project(raw, binding)
        if raw.get("title") != title or (raw.get("description") or "") != body or not self._raw_matches(raw, expected):
            raise TrackerConflictError("Linear issue divergent after create; no retry")
        if parent and (raw.get("parent") or {}).get("id") != input_value["parentId"]:
            raise TrackerConflictError("Linear parent divergent after create; no retry")
        return self._to_issue(raw, project)

    def _validate_pr_url(self, project: Project, value) -> str:
        if not isinstance(value, str):
            raise ValueError("GitHub PR must be an HTTPS URL")
        binding = self._binding(project)
        try:
            parsed = urllib.parse.urlsplit(value)
        except ValueError:
            raise ValueError("GitHub PR must be an HTTPS URL") from None
        repo_host, owner, repo_name = binding["canonical_repo"].split("/", 2)
        expected_prefix = f"/{owner}/{repo_name}/pull/"
        suffix = parsed.path[len(expected_prefix):] if parsed.path.startswith(expected_prefix) else ""
        if (parsed.scheme != "https" or (parsed.hostname or "").lower() != repo_host
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or not suffix.isdigit() or "/" in suffix):
            raise ValueError("GitHub PR URL is outside the registered repository")
        return value

    def _set_pr(self, raw: dict, project: Project, value) -> Issue:
        url = self._validate_pr_url(project, value)
        current = self._to_issue(raw, project)
        if current.pr_url == url:
            return current
        if current.pr_url is not None:
            raise TrackerConflictError("Linear GitHub PR already bound to a different URL")
        data = self._graphql(
            _ATTACHMENT_CREATE,
            {"input": {
                "issueId": raw["id"], "title": "Foundry GitHub PR", "url": url,
                "metadata": dict(_PR_METADATA),
            }},
            "attachment.create",
        )
        payload = self._mutation_payload(data, "attachmentCreate", "attachment.create")
        attachment = payload.get("attachment")
        if (not isinstance(attachment, dict) or attachment.get("url") != url
                or attachment.get("metadata") != _PR_METADATA
                or (attachment.get("issue") or {}).get("id") != raw["id"]):
            raise LinearTrackerError("attachment.create", None, "invalid_response")
        readback = self._read_raw(raw["identifier"])
        if self._to_issue(readback, project).pr_url != url:
            raise TrackerConflictError("Linear GitHub PR divergent after write; no retry")
        return self._to_issue(readback, project)

    def update_fields(
        self, issue_id: str, fields: dict, project: Project | None = None,
    ) -> Issue:
        if project is None:
            raise LinearBindingError("mutation_project_required")
        binding = self._activate(project)
        self._field_names(fields)
        if set(fields) != {"GitHub PR"}:
            raise TrackerCapabilityUnavailableError(
                self.name, "existing-issue-field-replacement",
            )
        raw = self._read_raw(issue_id)
        self._assert_issue_project(raw, binding)
        return self._set_pr(raw, project, fields["GitHub PR"])

    def set_state(
        self, issue_id: str, state: str, context=None,
        project: Project | None = None,
    ) -> None:
        self.update_fields(issue_id, {"State": state}, project=project)

    def link(
        self, src_id: str, link_type: str, dst_id: str,
        project: Project | None = None,
    ) -> None:
        if project is None:
            raise LinearBindingError("mutation_project_required")
        binding = self._activate(project)
        if link_type not in {"subtask-of", "parent-of", "depends-on", "blocks", "relates"}:
            raise TrackerCapabilityUnavailableError(self.name, f"link:{link_type}")
        if link_type in {"subtask-of", "parent-of"}:
            raise TrackerCapabilityUnavailableError(
                self.name, "existing-issue-parent-replacement",
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
            {"input": {
                "issueId": source["id"], "relatedIssueId": target["id"],
                "type": relation,
            }},
            "issue.relation.create",
        )
        payload = self._mutation_payload(
            data, "issueRelationCreate", "issue.relation.create",
        )
        created = payload.get("issueRelation")
        if (not isinstance(created, dict) or created.get("type") != relation
                or (created.get("issue") or {}).get("id") != source["id"]
                or (created.get("relatedIssue") or {}).get("id") != target["id"]):
            raise LinearTrackerError("issue.relation.create", None, "invalid_response")
        readback = self._read_raw(src_id)
        if not any(
            link.type == link_type and link.target == dst_id
            for link in self._to_issue(readback, project).links
        ):
            raise TrackerConflictError("Linear relation divergent after write; no retry")

    def add_comment(
        self, issue_id: str, text: str, project: Project | None = None,
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
        if (not isinstance(comment, dict) or not isinstance(comment.get("id"), str)
                or comment.get("body") != text
                or (comment.get("issue") or {}).get("id") != raw["id"]):
            raise LinearTrackerError("comment.create", None, "invalid_response")
        readback = self._graphql(
            _COMMENT_QUERY, {"id": comment["id"]}, "comment.readback",
        ).get("comment")
        if (not isinstance(readback, dict) or readback.get("body") != text
                or (readback.get("issue") or {}).get("id") != raw["id"]):
            raise TrackerConflictError("Linear comment divergent after write; no retry")

    def update_body(
        self, resource: Issue | Adr, expected_body: str, updated_body: str,
        project: Project | None = None,
    ) -> bool:
        del resource, expected_body, updated_body, project
        raise BodyUpdateUnavailableError(
            "mise à jour de corps indisponible pour le tracker linear : "
            "aucune précondition atomique anti-écrasement"
        )

    def sync_acceptance_body(
        self, issue_id: str, expected_body: str, updated_body: str, proof: dict,
        project: Project | None = None,
    ) -> bool:
        del issue_id, expected_body, updated_body, proof, project
        raise AcceptanceSyncUnavailableError(
            "synchronisation AC indisponible pour le tracker linear : "
            "aucune précondition atomique anti-écrasement"
        )

    # ---- unsupported provider capabilities -------------------------
    def list_adrs(self, project: Project) -> list[Adr]:
        self._activate(project)
        raise TrackerCapabilityUnavailableError(self.name, "adr-knowledge-base")

    def create_adr(
        self, project: Project, title: str, body: str, status: str = "proposed",
    ) -> Adr:
        self._activate(project)
        raise TrackerCapabilityUnavailableError(self.name, "adr-knowledge-base")

    def set_adr_status(
        self, adr: Adr, status: str, project: Project | None = None,
    ) -> None:
        raise TrackerCapabilityUnavailableError(self.name, "adr-knowledge-base")
