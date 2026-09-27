"""Bounded read adapter for qualified private personal GitHub Projects V2.

PAT-57 owns reads only: Project items and fields are GraphQL, while canonical
Issues, comments and relations are REST.  The adapter never guesses an owner,
project or repository from token state.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from foundry import registry
from foundry.models import Adr, Issue, Link, Project
from foundry.trackers.base import (
    IssueUnavailableError,
    Tracker,
    TrackerCapabilityUnavailableError,
)

_MAX_PAGES, _TRANSPORT_TIMEOUT_SECONDS, _ADR_LABEL = 100, 30, "foundry:adr"
_FIELDS = {"state": "Foundry normalized state", "type": "Foundry type",
           "priority": "Foundry priority", "estimate": "Foundry estimate"}
_FIELD_TYPES = {"state": "SINGLE_SELECT", "type": "SINGLE_SELECT",
                "priority": "SINGLE_SELECT", "estimate": "NUMBER"}
_FIELD_OPTIONS = {
    "state": {"backlog", "ready", "in-progress", "review", "blocked", "done", "dropped"},
    "type": {"Bug", "Feature", "Task", "Epic"},
    "priority": {"P0", "P1", "P2", "P3"},
}
# Keep this in lockstep with routing._AC_LINE: every complete checkbox accepted
# by the proof grammar is observed by the tracker progress counters as well.
_AC_CHECKBOX = re.compile(r"^\s*[-*+]\s+\[(?P<mark>[ xX])\]\s+.+?\s*$")


class GitHubProjectsTrackerError(RuntimeError):
    """A bounded GitHub read failure; messages contain no provider payload."""
    def __init__(self, operation: str, reason: str):
        super().__init__(f"GitHub Projects {operation}: {reason}")
        self.operation, self.reason = operation, reason


@dataclass(frozen=True)
class _Binding:
    owner: str
    number: int
    project_id: str
    repo: str
    key: str


@dataclass(frozen=True)
class _FieldBinding:
    id: str
    name: str
    data_type: str
    options: dict[str, str]


class GitHubProjectsTracker(Tracker):
    name = "ghprojects"

    def __init__(self, *, runner=subprocess.run):
        self._runner = runner

    @staticmethod
    def _binding(project: Project) -> _Binding:
        owner, number, repo = (project.extra.get("owner"), project.extra.get("number"),
                               project.extra.get("canonical_repo"))
        if (not isinstance(owner, str) or not owner or not isinstance(number, str)
                or not number.isdigit() or int(number) < 1
                or not isinstance(repo, str) or not repo.startswith("github.com/")
                or not isinstance(project.id, str) or not project.id.startswith("PVT_")):
            raise GitHubProjectsTrackerError("binding", "invalid_binding")
        native_repo = repo.removeprefix("github.com/")
        if (native_repo.count("/") != 1
                or native_repo.split("/", 1)[0].casefold() != owner.casefold()
                or not isinstance(project.key, str) or not project.key):
            raise GitHubProjectsTrackerError("binding", "unqualified_binding")
        return _Binding(owner, int(number), project.id, native_repo, project.key)

    def _run(self, command: list[str], operation: str) -> Any:
        try:
            result = self._runner(
                command, capture_output=True, text=True, check=False,
                timeout=_TRANSPORT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitHubProjectsTrackerError(operation, "transport_failed") from exc
        if result.returncode:
            msg = (result.stderr or "").casefold()
            try:
                error_body = json.loads(result.stdout)
            except (TypeError, ValueError):
                error_body = None
            if isinstance(error_body, dict):
                msg += " " + str(error_body.get("message", "")).casefold()
                status = str(error_body.get("status", ""))
            else:
                status = ""
            if (operation == "issue.parent" and isinstance(error_body, dict)
                    and str(error_body.get("status")) == "404"
                    and error_body.get("message") == "No parent issue found"):
                reason = "parent_absent"
            else:
                reason = (
                    "not_found" if status == "404" or "404" in msg or "not found" in msg else
                    "rate_limited" if status == "429" or "429" in msg or "rate limit" in msg or "rate_limit" in msg else
                    "authentication_failed" if status == "401" or "401" in msg or "authentication" in msg else
                    "permission_denied" if status == "403" or "403" in msg or "permission" in msg or "resource not accessible" in msg else
                    "transport_failed"
                )
            raise GitHubProjectsTrackerError(operation, reason)
        try:
            return json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise GitHubProjectsTrackerError(operation, "invalid_json") from exc

    def _graphql(self, query: str, variables: dict[str, Any], operation: str) -> dict:
        command = ["gh", "api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            command += ["-F" if isinstance(value, (int, float)) or value is None else "-f",
                        f"{key}={'null' if value is None else value}"]
        payload = self._run(command, operation)
        if not isinstance(payload, dict) or payload.get("errors") or not isinstance(payload.get("data"), dict):
            raise GitHubProjectsTrackerError(operation, "ambiguous_graphql_response")
        return payload["data"]

    def _rest(self, path: str, operation: str) -> Any:
        return self._run(["gh", "api", "-X", "GET", path], operation)

    def _project_page(self, binding: _Binding, cursor: str | None) -> dict:
        query = """query($login:String!,$number:Int!,$cursor:String){
          user(login:$login){projectV2(number:$number){
            id number public
            fields(first:100){
              nodes{
                __typename
                ... on ProjectV2Field{id name dataType}
                ... on ProjectV2SingleSelectField{id name dataType options{id name}}
              }
              pageInfo{hasNextPage endCursor}
            }
            items(first:100,after:$cursor){
              nodes{
                id type
                content{
                  __typename
                  ... on Issue{id number title body repository{id nameWithOwner isPrivate owner{__typename login}} labels(first:100){nodes{name} pageInfo{hasNextPage endCursor}}}
                  ... on PullRequest{id number}
                  ... on DraftIssue{id title}
                }
                fieldValues(first:100){
                  nodes{
                    __typename
                    ... on ProjectV2ItemFieldSingleSelectValue{field{... on ProjectV2SingleSelectField{id name}} optionId name}
                    ... on ProjectV2ItemFieldNumberValue{field{... on ProjectV2Field{id name}} number}
                  }
                  pageInfo{hasNextPage endCursor}
                }
              }
              pageInfo{hasNextPage endCursor}
            }
          }}
        }"""
        data = self._graphql(query, {"login": binding.owner, "number": binding.number, "cursor": cursor}, "project.read")
        raw = data.get("user", {}).get("projectV2") if isinstance(data.get("user"), dict) else None
        if (not isinstance(raw, dict) or raw.get("id") != binding.project_id
                or raw.get("number") != binding.number or raw.get("public") is not False):
            raise GitHubProjectsTrackerError("project.read", "foreign_or_unqualified_project")
        return raw

    def verify_project_identity(self, project: Project) -> bool:
        try:
            binding, cursor = self._binding(project), None
            # PAT-65 qualified a *personal* project.  Querying organization and
            # user together makes GitHub return partial errors for this owner;
            # errors stay fail-closed in _graphql, so use only the qualified shape.
            query = """query($login:String!,$number:Int!,$cursor:String){user(login:$login){projectV2(number:$number){id number public repositories(first:100,after:$cursor){nodes{id nameWithOwner isPrivate owner{__typename login}} pageInfo{hasNextPage endCursor}}}}}"""
            for _ in range(10):
                data = self._graphql(query, {"login": binding.owner, "number": binding.number, "cursor": cursor}, "project.identity")
                raw = data.get("user", {}).get("projectV2") if isinstance(data.get("user"), dict) else None
                if not isinstance(raw, dict) or raw.get("id") != binding.project_id or raw.get("number") != binding.number or raw.get("public") is not False:
                    return False
                repos = raw.get("repositories")
                if not isinstance(repos, dict) or not isinstance(repos.get("nodes"), list):
                    return False
                if any(
                    isinstance(n, dict)
                    and isinstance(n.get("id"), str) and n["id"]
                    and n.get("nameWithOwner", "").casefold() == binding.repo.casefold()
                    and n.get("isPrivate") is True
                    and isinstance(n.get("owner"), dict)
                    and n["owner"].get("__typename") == "User"
                    and n["owner"].get("login", "").casefold() == binding.owner.casefold()
                    for n in repos["nodes"]
                ):
                    return True
                page = repos.get("pageInfo")
                if not isinstance(page, dict) or page.get("hasNextPage") is not True:
                    return False
                cursor = page.get("endCursor")
                if not isinstance(cursor, str) or not cursor:
                    return False
        except GitHubProjectsTrackerError as exc:
            if exc.reason in {
                "transport_failed", "authentication_failed", "permission_denied", "rate_limited",
            }:
                raise
            return False
        return False

    def resolve_project(self, repo: str) -> Project:
        return registry.resolve("ghprojects", repo)

    @staticmethod
    def _field_map(raw: dict) -> dict[str, _FieldBinding]:
        connection = raw.get("fields") if isinstance(raw.get("fields"), dict) else None
        nodes = connection.get("nodes") if connection else None
        if not isinstance(nodes, list):
            raise GitHubProjectsTrackerError("project.read", "missing_fields")
        page_info = connection.get("pageInfo")
        if (not isinstance(page_info, dict)
                or page_info.get("hasNextPage") is not False):
            # Field IDs are authority coordinates.  A first-100 projection cannot
            # establish uniqueness when GitHub says more fields exist.
            raise GitHubProjectsTrackerError("project.read", "truncated_fields")
        found: dict[str, _FieldBinding] = {}
        field_ids: set[str] = set()
        option_ids: set[str] = set()
        for semantic, name in _FIELDS.items():
            candidates = [x for x in nodes if isinstance(x, dict) and x.get("name") == name]
            if (len(candidates) != 1 or not isinstance(candidates[0].get("id"), str)
                    or not candidates[0]["id"]
                    or candidates[0].get("dataType") != _FIELD_TYPES[semantic]
                    or candidates[0]["id"] in field_ids
                    or candidates[0]["id"] in option_ids):
                raise GitHubProjectsTrackerError("project.read", f"missing_or_ambiguous_field:{semantic}")
            field_ids.add(candidates[0]["id"])
            options_by_name: dict[str, str] = {}
            if semantic != "estimate":
                options = candidates[0].get("options")
                if (not isinstance(options, list) or not options
                        or any(not isinstance(option, dict) or not isinstance(option.get("id"), str)
                               or not option["id"]
                               or not isinstance(option.get("name"), str) for option in options)
                        or len({option["id"] for option in options}) != len(options)
                        or len({option["name"] for option in options}) != len(options)
                        or not {option["name"] for option in options} <= _FIELD_OPTIONS[semantic]
                        or (semantic in {"state", "type"}
                            and {option["name"] for option in options} != _FIELD_OPTIONS[semantic])
                        or any(option["id"] in field_ids or option["id"] in option_ids for option in options)):
                    raise GitHubProjectsTrackerError("project.read", f"invalid_field_options:{semantic}")
                options_by_name = {option["name"]: option["id"] for option in options}
                option_ids.update(options_by_name.values())
            found[semantic] = _FieldBinding(
                candidates[0]["id"], name, _FIELD_TYPES[semantic], options_by_name,
            )
        return found

    @staticmethod
    def _qualified_repository(raw: Any, binding: _Binding) -> str:
        owner = raw.get("owner") if isinstance(raw, dict) else None
        if (not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]
                or raw.get("nameWithOwner", "").casefold() != binding.repo.casefold()
                or raw.get("isPrivate") is not True or not isinstance(owner, dict)
                or owner.get("__typename") != "User"
                or owner.get("login", "").casefold() != binding.owner.casefold()):
            raise GitHubProjectsTrackerError("project.items", "foreign_or_unqualified_repository")
        return raw["id"]

    def _item(
        self, raw: dict, binding: _Binding, fields: dict[str, _FieldBinding],
    ) -> tuple[Issue | None, str, str]:
        if raw.get("type") != "ISSUE":
            raise GitHubProjectsTrackerError("project.items", "unsupported_item_type")
        content = raw.get("content")
        if not isinstance(content, dict) or content.get("__typename") != "Issue":
            raise GitHubProjectsTrackerError("project.items", "unsupported_item_content")
        content_id = content.get("id")
        item_id = raw.get("id")
        if (not isinstance(content_id, str) or not content_id
                or not isinstance(item_id, str) or not item_id or content_id == item_id):
            raise GitHubProjectsTrackerError("project.items", "invalid_or_conflated_native_ids")
        repo_id = self._qualified_repository(content.get("repository"), binding)
        authority_ids = [binding.project_id, item_id, content_id, repo_id]
        authority_ids.extend(field.id for field in fields.values())
        authority_ids.extend(
            option_id for field in fields.values() for option_id in field.options.values()
        )
        if len(authority_ids) != len(set(authority_ids)):
            raise GitHubProjectsTrackerError("project.items", "conflated_native_ids")
        if (not isinstance(content.get("title"), str)
                or content.get("body") is not None and not isinstance(content.get("body"), str)):
            raise GitHubProjectsTrackerError("project.items", "invalid_issue_content")
        labels_connection = content.get("labels") if isinstance(content.get("labels"), dict) else None
        labels = labels_connection.get("nodes") if labels_connection else None
        labels_page = labels_connection.get("pageInfo") if labels_connection else None
        if (not isinstance(labels, list) or not isinstance(labels_page, dict)
                or labels_page.get("hasNextPage") is not False):
            raise GitHubProjectsTrackerError("project.items", "truncated_or_invalid_labels")
        if (not all(isinstance(x, dict) and isinstance(x.get("name"), str)
                    and x["name"] for x in labels)
                or len({x["name"] for x in labels}) != len(labels)):
            raise GitHubProjectsTrackerError("project.items", "invalid_labels")
        labels = [x["name"] for x in labels]
        # ADR supports remain readable by the later ADR codec, but are never a
        # delivery backlog candidate.  One ADR item must not poison the whole
        # project's profile/backlog read.
        if _ADR_LABEL in labels:
            return None, content_id, repo_id
        values_connection = raw.get("fieldValues") if isinstance(raw.get("fieldValues"), dict) else None
        values = values_connection.get("nodes") if values_connection else None
        if not isinstance(values, list):
            raise GitHubProjectsTrackerError("project.items", "invalid_field_values")
        values_page = values_connection.get("pageInfo")
        if (not isinstance(values_page, dict)
                or values_page.get("hasNextPage") is not False):
            raise GitHubProjectsTrackerError("project.items", "truncated_field_values")
        by_id: dict[str, list[dict]] = {}
        required_by_name = {field.name: field for field in fields.values()}
        required_by_id = {field.id: field for field in fields.values()}
        qualified_value_types = {
            "ProjectV2ItemFieldSingleSelectValue",
            "ProjectV2ItemFieldNumberValue",
        }
        for item_value in values:
            if not isinstance(item_value, dict):
                raise GitHubProjectsTrackerError("project.items", "incoherent_field_value")
            value_field = item_value.get("field")
            if not isinstance(value_field, dict):
                # Other Project field-value variants are outside this slice and do
                # not expose `field` through the qualified fragments.  A supported
                # variant without its field coordinate is instead a partial payload.
                if item_value.get("__typename") in qualified_value_types:
                    raise GitHubProjectsTrackerError("project.items", "incoherent_field_value")
                continue
            field_id, field_name = value_field.get("id"), value_field.get("name")
            if (not isinstance(field_id, str) or not field_id
                    or not isinstance(field_name, str) or not field_name):
                raise GitHubProjectsTrackerError("project.items", "incoherent_field_value")
            expected_by_id = required_by_id.get(field_id)
            expected_by_name = required_by_name.get(field_name)
            if expected_by_id is not expected_by_name:
                raise GitHubProjectsTrackerError("project.items", "incoherent_field_value")
            by_id.setdefault(field_id, []).append(item_value)
        def value(name: str):
            field = fields[name]
            candidates = by_id.get(field.id, [])
            if len(candidates) > 1:
                raise GitHubProjectsTrackerError("project.items", f"ambiguous_value:{name}")
            if not candidates:
                return None
            candidate = candidates[0]
            if field.data_type == "SINGLE_SELECT":
                if (candidate.get("__typename") != "ProjectV2ItemFieldSingleSelectValue"
                        or not isinstance(candidate.get("optionId"), str)
                        or not isinstance(candidate.get("name"), str)
                        or field.options.get(candidate["name"]) != candidate["optionId"]):
                    raise GitHubProjectsTrackerError("project.items", f"invalid_option_value:{name}")
            elif (candidate.get("__typename") != "ProjectV2ItemFieldNumberValue"
                  or type(candidate.get("number")) not in (int, float)):
                raise GitHubProjectsTrackerError("project.items", f"invalid_number_value:{name}")
            return candidate
        state, kind, priority, estimate = (value("state"), value("type"), value("priority"), value("estimate"))
        state_name = state.get("name") if isinstance(state, dict) else None
        type_name = kind.get("name") if isinstance(kind, dict) else None
        priority_name = priority.get("name") if isinstance(priority, dict) else None
        number = estimate.get("number") if isinstance(estimate, dict) else None
        # The normalized state field is qualified but an item can legitimately
        # have no value.  Preserve that absence; an unknown *present* option is
        # unsafe to normalize.
        if state_name is not None and state_name not in _FIELD_OPTIONS["state"]:
            raise GitHubProjectsTrackerError("project.items", "invalid_state")
        if type_name not in _FIELD_OPTIONS["type"]:
            raise GitHubProjectsTrackerError("project.items", "invalid_type")
        if priority_name is not None and priority_name not in _FIELD_OPTIONS["priority"]:
            raise GitHubProjectsTrackerError("project.items", "invalid_priority")
        if number is not None and type(number) not in (int, float):
            raise GitHubProjectsTrackerError("project.items", "invalid_estimate")
        issue_no = content.get("number")
        if type(issue_no) is not int or issue_no < 1:
            raise GitHubProjectsTrackerError("project.items", "invalid_issue_number")
        return Issue(id=f"{binding.key}-{issue_no}", title=str(content.get("title", "")), state=state_name,
                     # PAT-67 owns projection/lifecycle semantics.  A Project
                     # field is an observation, never proof of alignment.
                     normalized_state=state_name, native_state=None, projection_status="unknown",
                     priority=priority_name, estimate=number, type=type_name, labels=labels, body=content.get("body")), content_id, repo_id

    def _search_raw(self, project: Project, query: str = "") -> list[Issue]:
        if query:
            raise TrackerCapabilityUnavailableError(self.name, "provider-native-search-query")
        binding = self._authoritative_binding(project)
        cursor, out, seen, content_ids, issue_ids = None, [], set(), set(), set()
        seen_cursors: set[str] = set()
        repository_id: str | None = None
        field_signature: tuple | None = None
        for _ in range(_MAX_PAGES):
            page = self._project_page(binding, cursor)
            fields, items = self._field_map(page), page.get("items")
            page_field_signature = tuple(
                (semantic, field.id, field.name, field.data_type, tuple(sorted(field.options.items())))
                for semantic, field in sorted(fields.items())
            )
            if field_signature is None:
                field_signature = page_field_signature
            elif field_signature != page_field_signature:
                raise GitHubProjectsTrackerError("project.read", "ambiguous_field_catalog")
            if not isinstance(items, dict) or not isinstance(items.get("nodes"), list):
                raise GitHubProjectsTrackerError("project.items", "invalid_response")
            for row in items["nodes"]:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] in seen:
                    raise GitHubProjectsTrackerError("project.items", "pagination_stalled")
                seen.add(row["id"])
                issue, content_id, row_repo_id = self._item(row, binding, fields)
                if content_id in content_ids:
                    raise GitHubProjectsTrackerError("project.items", "duplicate_issue_content_id")
                content_ids.add(content_id)
                if repository_id is None:
                    repository_id = row_repo_id
                elif repository_id != row_repo_id:
                    raise GitHubProjectsTrackerError("project.items", "ambiguous_repository_id")
                if issue is not None:
                    if issue.id in issue_ids:
                        raise GitHubProjectsTrackerError("project.items", "duplicate_issue_id")
                    issue_ids.add(issue.id)
                    out.append(issue)
            info = items.get("pageInfo")
            if not isinstance(info, dict) or type(info.get("hasNextPage")) is not bool:
                raise GitHubProjectsTrackerError("project.items", "invalid_pagination")
            if not info["hasNextPage"]:
                return out
            cursor = info.get("endCursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise GitHubProjectsTrackerError("project.items", "pagination_stalled")
            seen_cursors.add(cursor)
        raise GitHubProjectsTrackerError("project.items", "pagination_limit")

    def search(self, project: Project, query: str = "") -> list[Issue]:
        """Return backlog candidates with their complete qualified relation graph."""
        raw = self._search_raw(project, query)
        binding = self._binding(project)
        return [self._hydrate_issue(issue, binding) for issue in raw]

    def _project(self) -> Project:
        selection = registry.repository_tracker_selection()
        if selection["tracker"] != self.name:
            raise GitHubProjectsTrackerError("binding", "foreign_tracker")
        project = selection["binding"].project if selection["mode"] == "v1" else selection["project"]
        if not isinstance(project, Project):
            raise GitHubProjectsTrackerError("binding", "invalid_binding")
        return project

    def _authoritative_binding(self, project: Project) -> _Binding:
        """Match every caller-supplied coordinate to the active checkout binding."""
        supplied = self._binding(project)
        active = self._binding(self._project())
        if supplied != active:
            raise GitHubProjectsTrackerError("binding", "foreign_project_binding")
        return active

    @staticmethod
    def _number(issue_id: str, binding: _Binding) -> int:
        for candidate in (issue_id.removeprefix(f"{binding.key}-"), issue_id.removeprefix("#"), issue_id):
            if candidate.isdigit() and int(candidate) > 0:
                return int(candidate)
        raise GitHubProjectsTrackerError("issue.read", "invalid_issue_coordinate")

    def _rows(self, path: str, operation: str) -> list[dict]:
        """Exhaust one REST list with an explicit cap and no opaque gh retry."""
        out: list[dict] = []
        seen: set[int] = set()
        separator = "&" if "?" in path else "?"
        for page in range(1, _MAX_PAGES + 1):
            rows = self._rest(f"{path}{separator}per_page=100&page={page}", operation)
            if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
                raise GitHubProjectsTrackerError(operation, "invalid_response")
            for row in rows:
                native_id = row.get("id")
                if type(native_id) is not int or native_id < 1 or native_id in seen:
                    raise GitHubProjectsTrackerError(operation, "duplicate_or_invalid_item")
                seen.add(native_id)
            out.extend(rows)
            if len(rows) < 100:
                return out
        raise GitHubProjectsTrackerError(operation, "pagination_limit")

    @staticmethod
    def _api_repo(binding: _Binding) -> str:
        return f"https://api.github.com/repos/{binding.repo}"

    def _issue_number_from_rest(self, raw: Any, binding: _Binding, operation: str) -> int:
        """Validate a REST Issue coordinate before turning its number into an ID."""
        if not isinstance(raw, dict):
            raise GitHubProjectsTrackerError(operation, "invalid_response")
        number, native_id = raw.get("number"), raw.get("id")
        if (type(number) is not int or number < 1 or type(native_id) is not int
                or native_id < 1 or raw.get("pull_request") is not None
                or raw.get("repository_url") != self._api_repo(binding)
                or raw.get("url") != f"{self._api_repo(binding)}/issues/{number}"
                or raw.get("html_url") != f"https://github.com/{binding.repo}/issues/{number}"):
            raise GitHubProjectsTrackerError(operation, "foreign_issue_coordinate")
        return number

    @staticmethod
    def _timestamp(value: Any, operation: str) -> int:
        if not isinstance(value, str):
            raise GitHubProjectsTrackerError(operation, "invalid_timestamp")
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp() * 1000)
        except ValueError as exc:
            raise GitHubProjectsTrackerError(operation, "invalid_timestamp") from exc

    @staticmethod
    def _ac_counts(body: str | None) -> tuple[int, int]:
        if not body:
            return 0, 0
        # Match split lines independently so whitespace never assembles a
        # checkbox from separate Markdown lines.
        marks = [
            match.group("mark")
            for line in body.splitlines()
            if (match := _AC_CHECKBOX.match(line)) is not None
        ]
        done = sum(mark in {"x", "X"} for mark in marks)
        return done, len(marks)

    def _hydrate_issue(self, issue: Issue, binding: _Binding) -> Issue:
        number = self._number(issue.id, binding)
        base = f"repos/{binding.repo}/issues/{number}"
        raw = self._rest(base, "issue.read")
        if self._issue_number_from_rest(raw, binding, "issue.read") != number:
            raise GitHubProjectsTrackerError("issue.read", "unsupported_or_foreign_issue")
        if raw.get("body") is not None and not isinstance(raw.get("body"), str):
            raise GitHubProjectsTrackerError("issue.read", "invalid_body")
        ac_done, ac_total = self._ac_counts(raw.get("body"))
        raw_labels = raw.get("labels")
        if isinstance(raw_labels, list) and any(isinstance(label, dict) and label.get("name") == _ADR_LABEL for label in raw_labels):
            raise TrackerCapabilityUnavailableError(self.name, "adr_issue_read")
        comments = self._rows(base + "/comments", "issue.comments")
        try:
            parent = self._rest(base + "/parent", "issue.parent")
        except GitHubProjectsTrackerError as exc:
            if exc.reason != "parent_absent":
                raise
            parent = None
        blocked_by = self._rows(base + "/dependencies/blocked_by", "issue.dependencies")
        blocking = self._rows(base + "/dependencies/blocking", "issue.dependencies")
        children = self._rows(base + "/sub_issues", "issue.children")
        links = []
        if isinstance(parent, dict):
            links.append(Link("subtask-of", "inward", f"{binding.key}-{self._issue_number_from_rest(parent, binding, 'issue.parent')}"))
        elif parent not in ({}, None):
            raise GitHubProjectsTrackerError("issue.parent", "invalid_response")
        for kind, rows in (("depends-on", blocked_by), ("blocks", blocking)):
            for row in rows:
                links.append(Link(kind, "outward", f"{binding.key}-{self._issue_number_from_rest(row, binding, 'issue.dependencies')}"))
        for row in children:
            links.append(Link("parent-of", "outward", f"{binding.key}-{self._issue_number_from_rest(row, binding, 'issue.children')}"))
        comment_rows = []
        for row in comments:
            if not isinstance(row.get("body"), str):
                raise GitHubProjectsTrackerError("issue.comments", "invalid_response")
            comment_rows.append({"text": row["body"], "created": self._timestamp(row.get("created_at"), "issue.comments")})
        issue.body, issue.comments, issue.links = raw.get("body"), comment_rows, links
        issue.ac_done, issue.ac_total = ac_done, ac_total
        issue.created = self._timestamp(raw.get("created_at"), "issue.read")
        issue.updated = self._timestamp(raw.get("updated_at"), "issue.read")
        return issue

    def get_issue(self, issue_id: str) -> Issue:
        project = self._project()
        binding = self._binding(project)
        number = self._number(issue_id, binding)
        issue = next((x for x in self._search_raw(project) if x.id == f"{binding.key}-{number}"), None)
        if issue is None:
            raise IssueUnavailableError(issue_id)
        try:
            return self._hydrate_issue(issue, binding)
        except GitHubProjectsTrackerError as exc:
            # A complete bound-Project read followed by an exact target 404 proves
            # only this issue unavailable.  Auth, malformed data, relation failures,
            # transport and pagination errors remain visible to the caller.
            if exc.operation == "issue.read" and exc.reason == "not_found":
                raise IssueUnavailableError(issue_id) from None
            raise

    # PAT-66/PAT-58/PAT-67 own all mutation and ADR codec work.
    def create_issue(self, project, title, body, fields=None, parent=None): raise TrackerCapabilityUnavailableError(self.name, "create_issue")
    def update_fields(self, issue_id, fields, project=None): raise TrackerCapabilityUnavailableError(self.name, "update_fields")
    def set_state(self, issue_id, state, context=None, project=None): raise TrackerCapabilityUnavailableError(self.name, "set_state")
    def link(self, src_id, link_type, dst_id, project=None): raise TrackerCapabilityUnavailableError(self.name, "link")
    def add_comment(self, issue_id, text, project=None): raise TrackerCapabilityUnavailableError(self.name, "add_comment")
    def list_adrs(self, project: Project) -> list[Adr]: raise TrackerCapabilityUnavailableError(self.name, "adr_index")
    def create_adr(self, project, title, body, status="proposed"): raise TrackerCapabilityUnavailableError(self.name, "adr_create")
    def set_adr_status(self, adr, status, project=None): raise TrackerCapabilityUnavailableError(self.name, "adr_status")
