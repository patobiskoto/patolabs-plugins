"""Bounded adapter for qualified private personal GitHub Projects V2.

Project items and fields are GraphQL, while canonical Issues, comments and
relations are REST.  The adapter never guesses an owner, project or repository
from token state. Existing-record writes use bounded detection, not CAS; issue
creation additionally keeps a machine-local intent so an unknown effect is not
blindly posted again.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from foundry import registry
from foundry.models import Adr, Issue, Link, Project
from foundry.trackers.base import (
    TrackerConflictError,
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
_CREATE_INTENT_SCHEMA = "foundry-ghprojects-create-intent.v1"
_CREATE_STEPS = {"item", "parent", *(f"field:{name}" for name in _FIELDS)}


class GitHubProjectsTrackerError(RuntimeError):
    """A bounded GitHub adapter failure; messages contain no provider payload."""
    def __init__(self, operation: str, reason: str):
        super().__init__(f"GitHub Projects {operation}: {reason}")
        self.operation, self.reason = operation, reason


class GitHubProjectsPartialCreateError(GitHubProjectsTrackerError):
    """A create stopped after its native Issue identity became observable."""

    def __init__(self, phase: str, candidate: _CreateCandidate):
        reason = (
            f"partial_create:{phase}:issue={candidate.issue_id}:"
            f"native_issue_id={candidate.native_id}"
        )
        super().__init__("issue.create", reason)
        self.phase = phase
        self.partial_issue_id = candidate.issue_id
        self.partial_native_issue_id = candidate.native_id


class GitHubProjectsPartialCreateConflict(TrackerConflictError):
    """A create readback conflict with its known native Issue coordinates."""

    def __init__(self, detail: str, candidate: _CreateCandidate):
        super().__init__(
            f"{detail}; "
            f"issue={candidate.issue_id}:native_issue_id={candidate.native_id}",
        )
        self.partial_issue_id = candidate.issue_id
        self.partial_native_issue_id = candidate.native_id


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


@dataclass(frozen=True)
class _CreateCandidate:
    issue_id: str
    number: int
    native_id: int
    content_id: str


class GitHubProjectsTracker(Tracker):
    name = "ghprojects"

    def __init__(self, *, runner=subprocess.run, state_dir: Path | str | None = None):
        self._runner = runner
        base = Path(state_dir) if state_dir is not None else Path(registry.data_dir())
        self._create_intent_dir = base / "ghprojects-create-intents"

    @staticmethod
    def _create_fingerprint(
        binding: _Binding,
        title: str,
        body: str,
        fields: dict[str, Any],
        parent: str | None,
    ) -> str:
        payload = {
            "binding": {
                "key": binding.key,
                "owner": binding.owner,
                "project_id": binding.project_id,
                "project_number": binding.number,
                "repository": binding.repo,
            },
            "body": body,
            "fields": fields,
            "parent": parent,
            "title": title,
        }
        encoded = json.dumps(
            payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _intent_path(self, fingerprint: str) -> Path:
        return self._create_intent_dir / f"{fingerprint}.json"

    @contextmanager
    def _create_intent_lock(self, fingerprint: str) -> Iterator[None]:
        try:
            self._create_intent_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._create_intent_dir.chmod(0o700)
            lock = self._create_intent_dir / f".{fingerprint}.lock"
            descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "local_intent_unavailable",
            ) from exc
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_create_intent(self, fingerprint: str) -> dict[str, Any] | None:
        path = self._intent_path(fingerprint)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "invalid_local_intent",
            ) from exc
        if not isinstance(raw, dict) or set(raw) != {
            "attempt", "content_id", "fingerprint", "issue_id", "native_id",
            "schema", "state", "step",
        }:
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "invalid_local_intent",
            )
        state, step, attempt = raw["state"], raw["step"], raw["attempt"]
        if (
            raw["schema"] != _CREATE_INTENT_SCHEMA
            or raw["fingerprint"] != fingerprint
            or state not in {"pending", "known", "complete"}
            or step is not None and step not in _CREATE_STEPS
            or type(attempt) is not int
            or attempt not in {0, 1}
        ):
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "invalid_local_intent",
            )
        coordinates = (raw["issue_id"], raw["native_id"], raw["content_id"])
        pending_coordinates = coordinates == (None, None, None)
        known_coordinates = (
            isinstance(coordinates[0], str) and bool(coordinates[0])
            and type(coordinates[1]) is int and coordinates[1] > 0
            and isinstance(coordinates[2], str) and bool(coordinates[2])
        )
        if (
            state == "pending" and (not pending_coordinates or step is not None)
            or state in {"known", "complete"} and not known_coordinates
            or state == "complete" and step is not None
            or step is None and attempt != 0
        ):
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "invalid_local_intent",
            )
        return raw

    def _write_create_intent(self, fingerprint: str, record: dict[str, Any]) -> None:
        path = self._intent_path(fingerprint)
        payload = json.dumps(
            record, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
        )
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._create_intent_dir,
                prefix=f".{fingerprint}.", delete=False,
            ) as temporary:
                temporary_name = temporary.name
                os.chmod(temporary_name, 0o600)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, path)
            directory = os.open(self._create_intent_dir, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "local_intent_unavailable",
            ) from exc
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _intent_record(
        fingerprint: str,
        state: str,
        candidate: _CreateCandidate | None = None,
        *,
        step: str | None = None,
        attempt: int = 0,
    ) -> dict[str, Any]:
        return {
            "attempt": attempt,
            "content_id": candidate.content_id if candidate else None,
            "fingerprint": fingerprint,
            "issue_id": candidate.issue_id if candidate else None,
            "native_id": candidate.native_id if candidate else None,
            "schema": _CREATE_INTENT_SCHEMA,
            "state": state,
            "step": step,
        }

    def _begin_create_step(
        self,
        fingerprint: str,
        record: dict[str, Any],
        step: str,
        candidate: _CreateCandidate,
    ) -> dict[str, Any]:
        current = record["step"]
        if current not in {None, step}:
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "incoherent_partial_step",
            )
        if current == step:
            if record["attempt"] == 1:
                raise GitHubProjectsTrackerError(
                    "issue.create_reconcile", f"partial_retry_exhausted:{step}",
                )
            attempt = 1
        else:
            attempt = 0
        updated = {**record, "state": "known", "step": step, "attempt": attempt}
        try:
            self._write_create_intent(fingerprint, updated)
        except GitHubProjectsTrackerError as exc:
            raise GitHubProjectsPartialCreateError(
                f"local_intent:{step}", candidate,
            ) from exc
        return updated

    def _complete_create_step(
        self,
        fingerprint: str,
        record: dict[str, Any],
        step: str,
        candidate: _CreateCandidate,
    ) -> dict[str, Any]:
        if record["step"] not in {None, step}:
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "incoherent_partial_step",
            )
        updated = {**record, "state": "known", "step": None, "attempt": 0}
        try:
            self._write_create_intent(fingerprint, updated)
        except GitHubProjectsTrackerError as exc:
            raise GitHubProjectsPartialCreateError(
                f"local_observation:{step}", candidate,
            ) from exc
        return updated

    @staticmethod
    def _binding(project: Project) -> _Binding:
        if not isinstance(project, Project):
            raise GitHubProjectsTrackerError("binding", "invalid_binding")
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

    def _rest_write(self, method: str, path: str, payload: dict[str, Any], operation: str) -> Any:
        """Issue REST has no qualified conditional-write parameter.

        Callers therefore do their fresh targeted read and their readback around
        this single request.  In particular this helper never retries a failed or
        ambiguous request: a response loss after POST can have had an effect.
        """
        command = ["gh", "api", "-X", method, path]
        for key, value in payload.items():
            # ``gh api -f`` always sends a string.  The native hierarchy and
            # dependency endpoints require JSON numbers and booleans, whereas
            # issue titles and bodies must remain verbatim UTF-8 strings.
            typed = isinstance(value, (bool, int, float)) or value is None
            encoded = "null" if value is None else str(value).lower() if isinstance(value, bool) else str(value)
            command += ["-F" if typed else "-f", f"{key}={encoded}"]
        return self._run(command, operation)

    def _graphql_mutation(self, query: str, variables: dict[str, Any], operation: str) -> dict:
        return self._graphql(query, variables, operation)

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
                  ... on Issue{id number title body repository{id nameWithOwner isPrivate owner{__typename login}} labels(first:100){nodes{name} pageInfo{hasNextPage endCursor}} relatesTo(first:100){nodes{id number repository{id nameWithOwner isPrivate owner{__typename login}}} pageInfo{hasNextPage endCursor}}}
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
        self,
        raw: dict,
        binding: _Binding,
        fields: dict[str, _FieldBinding],
        *,
        allow_incomplete_create: bool = False,
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
        if (
            type_name is not None or not allow_incomplete_create
        ) and type_name not in _FIELD_OPTIONS["type"]:
            raise GitHubProjectsTrackerError("project.items", "invalid_type")
        if priority_name is not None and priority_name not in _FIELD_OPTIONS["priority"]:
            raise GitHubProjectsTrackerError("project.items", "invalid_priority")
        if number is not None:
            if type(number) not in (int, float):
                raise GitHubProjectsTrackerError("project.items", "invalid_estimate")
            if isinstance(number, float):
                if not number.is_integer():
                    raise GitHubProjectsTrackerError("project.items", "invalid_estimate")
                number = int(number)
        issue_no = content.get("number")
        if type(issue_no) is not int or issue_no < 1:
            raise GitHubProjectsTrackerError("project.items", "invalid_issue_number")
        related = content.get("relatesTo")
        related_nodes = related.get("nodes") if isinstance(related, dict) else None
        related_page = related.get("pageInfo") if isinstance(related, dict) else None
        if (not isinstance(related_nodes, list) or not isinstance(related_page, dict)
                or related_page.get("hasNextPage") is not False):
            raise GitHubProjectsTrackerError("project.items", "truncated_or_invalid_relates")
        relates = [f"{binding.key}-{self._issue_number_from_graphql(node, binding)}" for node in related_nodes]
        if len(relates) != len(set(relates)):
            raise GitHubProjectsTrackerError("project.items", "duplicate_relates")
        issue = Issue(id=f"{binding.key}-{issue_no}", title=str(content.get("title", "")), state=state_name,
                     # PAT-67 owns projection/lifecycle semantics.  A Project
                     # field is an observation, never proof of alignment.
                     normalized_state=state_name, native_state=None, projection_status="unknown",
                     priority=priority_name, estimate=number, type=type_name, labels=labels, body=content.get("body"))
        issue.links = [Link("relates", "outward", target) for target in relates]
        return issue, content_id, repo_id

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

    def _create_candidate(
        self,
        raw: Any,
        binding: _Binding,
        title: str,
        body: str,
        operation: str,
    ) -> _CreateCandidate | None:
        """Decode one exact observable create result, excluding ADR supports."""
        if not isinstance(raw, dict) or raw.get("pull_request") is not None:
            return None
        number = self._issue_number_from_rest(raw, binding, operation)
        raw_title, raw_body, labels = raw.get("title"), raw.get("body"), raw.get("labels")
        if (
            not isinstance(raw_title, str)
            or raw_body is not None and not isinstance(raw_body, str)
            or not isinstance(labels, list)
            or not all(
                isinstance(label, dict) and isinstance(label.get("name"), str)
                and bool(label["name"])
                for label in labels
            )
            or len({label["name"] for label in labels}) != len(labels)
        ):
            raise GitHubProjectsTrackerError(operation, "invalid_create_candidate")
        if _ADR_LABEL in {label["name"] for label in labels}:
            return None
        if raw_title != title or (raw_body or "") != body:
            return None
        content_id = raw.get("node_id")
        if (
            not isinstance(content_id, str) or not content_id
            or content_id == binding.project_id
        ):
            raise GitHubProjectsTrackerError(operation, "invalid_create_candidate")
        return _CreateCandidate(
            f"{binding.key}-{number}", number, raw["id"], content_id,
        )

    def _create_candidates(
        self, binding: _Binding, title: str, body: str,
    ) -> list[_CreateCandidate]:
        rows = self._rows(
            f"repos/{binding.repo}/issues?state=all", "issue.create_reconcile",
        )
        candidates = [
            candidate
            for raw in rows
            if (candidate := self._create_candidate(
                raw, binding, title, body, "issue.create_reconcile",
            )) is not None
        ]
        if len({candidate.issue_id for candidate in candidates}) != len(candidates):
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "duplicate_create_candidate",
            )
        return candidates

    def _partial_project_issue(
        self, binding: _Binding, candidate: _CreateCandidate,
    ) -> tuple[str, Issue] | None:
        """Observe a delivery item even while its create-time fields are incomplete."""
        cursor, seen, seen_cursors = None, set(), set()
        coordinate: tuple[str, Issue] | None = None
        for _ in range(_MAX_PAGES):
            page = self._project_page(binding, cursor)
            fields, items = self._field_map(page), page.get("items")
            if not isinstance(items, dict) or not isinstance(items.get("nodes"), list):
                raise GitHubProjectsTrackerError(
                    "issue.create_reconcile", "invalid_project_items",
                )
            for row in items["nodes"]:
                if (
                    not isinstance(row, dict)
                    or not isinstance(row.get("id"), str)
                    or not row["id"]
                    or row["id"] in seen
                ):
                    raise GitHubProjectsTrackerError(
                        "issue.create_reconcile", "pagination_stalled",
                    )
                seen.add(row["id"])
                issue, content_id, _ = self._item(
                    row, binding, fields, allow_incomplete_create=True,
                )
                if issue is None:
                    if content_id == candidate.content_id:
                        raise GitHubProjectsTrackerError(
                            "issue.create_reconcile", "candidate_is_adr_support",
                        )
                    continue
                same_number = issue.id == candidate.issue_id
                same_content = content_id == candidate.content_id
                if same_number != same_content:
                    raise GitHubProjectsTrackerError(
                        "issue.create_reconcile", "candidate_coordinate_mismatch",
                    )
                if same_number:
                    if coordinate is not None:
                        raise GitHubProjectsTrackerError(
                            "issue.create_reconcile", "multiple_project_candidates",
                        )
                    coordinate = (row["id"], issue)
            info = items.get("pageInfo")
            if not isinstance(info, dict) or type(info.get("hasNextPage")) is not bool:
                raise GitHubProjectsTrackerError(
                    "issue.create_reconcile", "invalid_pagination",
                )
            if not info["hasNextPage"]:
                return coordinate
            cursor = info.get("endCursor")
            if (
                not isinstance(cursor, str) or not cursor
                or cursor in seen_cursors
            ):
                raise GitHubProjectsTrackerError(
                    "issue.create_reconcile", "pagination_stalled",
                )
            seen_cursors.add(cursor)
        raise GitHubProjectsTrackerError(
            "issue.create_reconcile", "pagination_limit",
        )

    @staticmethod
    def _timestamp(value: Any, operation: str) -> int:
        if not isinstance(value, str):
            raise GitHubProjectsTrackerError(operation, "invalid_timestamp")
        try:
            observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if observed.tzinfo is None or observed.utcoffset() is None:
                raise ValueError("timestamp requires an explicit offset")
            return int(observed.astimezone(timezone.utc).timestamp() * 1000)
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
        if (not isinstance(raw_labels, list)
                or not all(isinstance(label, dict) and isinstance(label.get("name"), str)
                           and label["name"] for label in raw_labels)
                or len({label["name"] for label in raw_labels}) != len(raw_labels)):
            raise GitHubProjectsTrackerError("issue.read", "invalid_labels")
        if any(label["name"] == _ADR_LABEL for label in raw_labels):
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
        links = list(issue.links)
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

    def _relates(self, issue_id: str, binding: _Binding) -> list[Link]:
        """Read the qualified symmetric GraphQL relation without partial pages."""
        wanted, cursor, seen = self._number(issue_id, binding), None, set()
        for _ in range(_MAX_PAGES):
            page = self._project_page(binding, cursor)
            fields, items = self._field_map(page), page.get("items")
            if not isinstance(items, dict) or not isinstance(items.get("nodes"), list):
                raise GitHubProjectsTrackerError("issue.relates", "invalid_response")
            for row in items["nodes"]:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] in seen:
                    raise GitHubProjectsTrackerError("issue.relates", "pagination_stalled")
                seen.add(row["id"])
                issue, _, _ = self._item(row, binding, fields)
                if issue is None or self._number(issue.id, binding) != wanted:
                    continue
                content = row.get("content")
                related = content.get("relatesTo") if isinstance(content, dict) else None
                nodes = related.get("nodes") if isinstance(related, dict) else None
                info = related.get("pageInfo") if isinstance(related, dict) else None
                if not isinstance(nodes, list) or not isinstance(info, dict) or info.get("hasNextPage") is not False:
                    raise GitHubProjectsTrackerError("issue.relates", "truncated_or_invalid_relates")
                out = []
                for target in nodes:
                    number = self._issue_number_from_graphql(target, binding)
                    out.append(Link("relates", "outward", f"{binding.key}-{number}"))
                if len({link.target for link in out}) != len(out):
                    raise GitHubProjectsTrackerError("issue.relates", "duplicate_relates")
                return out
            info = items.get("pageInfo")
            if not isinstance(info, dict) or info.get("hasNextPage") is not True:
                break
            cursor = info.get("endCursor")
            if not isinstance(cursor, str) or not cursor:
                raise GitHubProjectsTrackerError("issue.relates", "pagination_stalled")
        raise IssueUnavailableError(issue_id)

    def _issue_number_from_graphql(self, raw: Any, binding: _Binding) -> int:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
            raise GitHubProjectsTrackerError("issue.relates", "invalid_related_issue")
        number = raw.get("number")
        if type(number) is not int or number < 1:
            raise GitHubProjectsTrackerError("issue.relates", "invalid_related_issue")
        self._qualified_repository(raw.get("repository"), binding)
        return number

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

    # ---- PAT-66 bounded core writes ------------------------------------
    # GitHub gives these endpoints neither an ETag precondition nor a ProjectV2
    # item version.  The checks below are bounded detection (S1--S4), not CAS.
    def _portable_fields(self, fields: dict | None) -> dict[str, Any]:
        if fields is None:
            return {}
        if not isinstance(fields, dict):
            raise GitHubProjectsTrackerError("issue.write", "invalid_fields")
        aliases = {"State": "state", "Type": "type", "Priority": "priority", "Estimate": "estimate"}
        out = {}
        for name, value in fields.items():
            semantic = aliases.get(name)
            if semantic is None or value is None:
                raise TrackerCapabilityUnavailableError(self.name, f"field:{name}")
            if semantic in {"state", "type", "priority"}:
                if not isinstance(value, str) or value not in _FIELD_OPTIONS[semantic]:
                    raise GitHubProjectsTrackerError("issue.write", f"invalid_field_value:{semantic}")
            elif type(value) is not int:
                raise GitHubProjectsTrackerError("issue.write", "invalid_field_value:estimate")
            out[semantic] = value
        return out

    @staticmethod
    def _snapshot(issue: Issue, fields: dict[str, Any]) -> dict[str, Any]:
        return {name: getattr(issue, name) for name in fields}

    def _item_coordinate(self, issue_id: str, binding: _Binding) -> tuple[str, str]:
        """Return distinct Project item and Issue node IDs for one bound issue."""
        # Reuse PAT-57's exhaustive page and catalog validation before a raw
        # item ID becomes authority for a mutation.  In particular, do not let
        # a matching first-page row hide ambiguity on a later page.
        if not any(issue.id == issue_id for issue in self._search_raw(self._project())):
            raise IssueUnavailableError(issue_id)
        number, cursor, seen = self._number(issue_id, binding), None, set()
        coordinate: tuple[str, str] | None = None
        for _ in range(_MAX_PAGES):
            page = self._project_page(binding, cursor)
            fields = self._field_map(page)
            items = page.get("items")
            if not isinstance(items, dict) or not isinstance(items.get("nodes"), list):
                raise GitHubProjectsTrackerError("project.items", "invalid_response")
            for row in items["nodes"]:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] in seen:
                    raise GitHubProjectsTrackerError("project.items", "pagination_stalled")
                seen.add(row["id"])
                issue, content_id, _ = self._item(row, binding, fields)
                if issue is not None and issue.id == f"{binding.key}-{number}":
                    if coordinate is not None:
                        raise GitHubProjectsTrackerError("project.items", "duplicate_issue_id")
                    coordinate = (row["id"], content_id)
            info = items.get("pageInfo")
            if not isinstance(info, dict) or info.get("hasNextPage") is not True:
                break
            cursor = info.get("endCursor")
            if not isinstance(cursor, str) or not cursor:
                raise GitHubProjectsTrackerError("project.items", "pagination_stalled")
        if coordinate is None:
            raise IssueUnavailableError(issue_id)
        return coordinate

    def _field_catalog(self, binding: _Binding) -> dict[str, _FieldBinding]:
        # A fresh page establishes field and option IDs immediately before a mutation.
        return self._field_map(self._project_page(binding, None))

    def _set_project_field(self, item_id: str, project_id: str, field: _FieldBinding, value: Any) -> None:
        if field.data_type == "NUMBER":
            if type(value) is not int:
                raise GitHubProjectsTrackerError(
                    "project.field_write", "invalid_field_value:estimate",
                )
            payload = f'{{number:{value}}}'
        else:
            option_id = field.options[value]
            payload = f'{{singleSelectOptionId:"{option_id}"}}'
        query = f"""mutation($project:ID!,$item:ID!,$field:ID!){{updateProjectV2ItemFieldValue(input:{{projectId:$project,itemId:$item,fieldId:$field,value:{payload}}}){{projectV2Item{{id}}}}}}"""
        data = self._graphql_mutation(query, {"project": project_id, "item": item_id, "field": field.id}, "project.field_write")
        result = data.get("updateProjectV2ItemFieldValue")
        if not isinstance(result, dict) or not isinstance(result.get("projectV2Item"), dict) or result["projectV2Item"].get("id") != item_id:
            raise GitHubProjectsTrackerError("project.field_write", "ambiguous_mutation_response")

    def _add_project_item(self, binding: _Binding, content_id: str) -> str:
        query = """mutation($project:ID!,$content:ID!){addProjectV2ItemById(input:{projectId:$project,contentId:$content}){item{id}}}"""
        data = self._graphql_mutation(query, {"project": binding.project_id, "content": content_id}, "project.item_create")
        result = data.get("addProjectV2ItemById")
        item = result.get("item") if isinstance(result, dict) else None
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise GitHubProjectsTrackerError("project.item_create", "ambiguous_mutation_response")
        return item["id"]

    def _native_issue(self, issue_id: str, binding: _Binding, operation: str) -> tuple[int, int]:
        number = self._number(issue_id, binding)
        raw = self._rest(f"repos/{binding.repo}/issues/{number}", operation)
        if self._issue_number_from_rest(raw, binding, operation) != number:
            raise GitHubProjectsTrackerError(operation, "foreign_issue_coordinate")
        return number, raw["id"]

    def _create_parent(self, issue_id: str, binding: _Binding) -> int:
        """Prove a complete delivery parent without relaxing ordinary strict reads."""
        number = self._number(issue_id, binding)
        raw = self._rest(
            f"repos/{binding.repo}/issues/{number}", "issue.parent_prewrite",
        )
        if self._issue_number_from_rest(
            raw, binding, "issue.parent_prewrite",
        ) != number:
            raise GitHubProjectsTrackerError(
                "issue.parent_prewrite", "foreign_issue_coordinate",
            )
        content_id = raw.get("node_id")
        if not isinstance(content_id, str) or not content_id:
            raise GitHubProjectsTrackerError(
                "issue.parent_prewrite", "missing_issue_node_id",
            )
        candidate = _CreateCandidate(
            issue_id, number, raw["id"], content_id,
        )
        observed = self._partial_project_issue(binding, candidate)
        if observed is None:
            raise IssueUnavailableError(issue_id)
        if observed[1].type is None:
            raise GitHubProjectsTrackerError(
                "issue.parent_prewrite", "incomplete_project_parent",
            )
        self._hydrate_issue(observed[1], binding)
        return number

    def _write_catalog(self, binding: _Binding, fields: dict[str, Any]) -> dict[str, _FieldBinding]:
        """Prove every requested option before the first effectful request."""
        catalog = self._field_catalog(binding)
        for semantic, value in fields.items():
            field = catalog[semantic]
            if field.data_type == "SINGLE_SELECT" and value not in field.options:
                raise TrackerCapabilityUnavailableError(
                    self.name, f"field_option:{semantic}:{value}",
                )
        return catalog

    def _candidate_from_record(
        self, record: dict[str, Any], binding: _Binding,
    ) -> _CreateCandidate:
        number = self._number(record["issue_id"], binding)
        return _CreateCandidate(
            record["issue_id"], number, record["native_id"], record["content_id"],
        )

    @staticmethod
    def _same_candidate(left: _CreateCandidate, right: _CreateCandidate) -> bool:
        return left == right

    def _observe_create_candidate(
        self,
        binding: _Binding,
        title: str,
        body: str,
        record: dict[str, Any] | None,
    ) -> _CreateCandidate | None:
        candidates = self._create_candidates(binding, title, body)
        expected = (
            self._candidate_from_record(record, binding)
            if record is not None and record["state"] in {"known", "complete"}
            else None
        )
        if len(candidates) > 1:
            if expected is not None:
                raise GitHubProjectsPartialCreateError(
                    "reconcile:multiple_create_candidates", expected,
                )
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "multiple_create_candidates",
            )
        candidate = candidates[0] if candidates else None
        if record is None:
            if candidate is not None:
                raise GitHubProjectsTrackerError(
                    "issue.create_reconcile", "unowned_create_candidate",
                )
            return None
        if record["state"] == "pending":
            if candidate is None:
                raise GitHubProjectsTrackerError(
                    "issue.create_reconcile", "create_effect_unknown",
                )
            return candidate
        if expected is None:
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "invalid_local_intent",
            )
        if candidate is None:
            raise GitHubProjectsPartialCreateError(
                "reconcile:known_create_candidate_missing", expected,
            )
        if not self._same_candidate(candidate, expected):
            raise GitHubProjectsPartialCreateError(
                "reconcile:create_candidate_mismatch", expected,
            )
        return candidate

    def _resume_created_issue(
        self,
        binding: _Binding,
        fingerprint: str,
        record: dict[str, Any],
        candidate: _CreateCandidate,
        title: str,
        body: str,
        requested: dict[str, Any],
        catalog: dict[str, _FieldBinding],
        parent: str | None,
        parent_number: int | None,
    ) -> Issue:
        if record["state"] == "complete":
            completed = self._created_issue_readback(binding, candidate)
            if (
                completed.title != title
                or (completed.body or "") != body
                or self._snapshot(completed, requested) != requested
                or parent is not None
                and not self._has_link(completed, "subtask-of", parent)
            ):
                raise GitHubProjectsTrackerError(
                    "issue.create_reconcile", "completed_create_drift",
                )
            return completed
        observed = self._partial_project_issue(binding, candidate)
        if observed is not None:
            if record["step"] == "item":
                record = self._complete_create_step(
                    fingerprint, record, "item", candidate,
                )
        else:
            record = self._begin_create_step(
                fingerprint, record, "item", candidate,
            )
            try:
                returned_item_id = self._add_project_item(binding, candidate.content_id)
            except GitHubProjectsTrackerError as exc:
                observed = self._partial_project_issue(binding, candidate)
                if observed is None:
                    raise GitHubProjectsPartialCreateError("item", candidate) from exc
                record = self._complete_create_step(
                    fingerprint, record, "item", candidate,
                )
            else:
                observed = self._partial_project_issue(binding, candidate)
                if observed is None or observed[0] != returned_item_id:
                    raise GitHubProjectsPartialCreateError("item_readback", candidate)
                record = self._complete_create_step(
                    fingerprint, record, "item", candidate,
                )
        if observed is None:
            raise GitHubProjectsPartialCreateError("item_observation", candidate)
        item_id, partial = observed
        if partial.title != title or (partial.body or "") != body:
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "create_candidate_content_drift",
            )

        for semantic, value in requested.items():
            step = f"field:{semantic}"
            actual = getattr(partial, semantic)
            if actual == value:
                if record["step"] == step:
                    record = self._complete_create_step(
                        fingerprint, record, step, candidate,
                    )
                continue
            if actual is not None:
                raise GitHubProjectsTrackerError(
                    "issue.create_reconcile", f"partial_field_conflict:{semantic}",
                )
            record = self._begin_create_step(
                fingerprint, record, step, candidate,
            )
            try:
                self._set_project_field(
                    item_id, binding.project_id, catalog[semantic], value,
                )
            except GitHubProjectsTrackerError as exc:
                refreshed = self._partial_project_issue(binding, candidate)
                if refreshed is None or getattr(refreshed[1], semantic) != value:
                    raise GitHubProjectsPartialCreateError(step, candidate) from exc
                observed = refreshed
                record = self._complete_create_step(
                    fingerprint, record, step, candidate,
                )
            else:
                refreshed = self._partial_project_issue(binding, candidate)
                if refreshed is None or getattr(refreshed[1], semantic) != value:
                    raise GitHubProjectsPartialCreateError(
                        f"{step}_readback", candidate,
                    )
                observed = refreshed
                record = self._complete_create_step(
                    fingerprint, record, step, candidate,
                )
            item_id, partial = observed

        created = self._created_issue_readback(binding, candidate)
        if parent is not None:
            step = "parent"
            observed_parents = [
                link.target for link in created.links if link.type == "subtask-of"
            ]
            if observed_parents == [parent]:
                if record["step"] == step:
                    record = self._complete_create_step(
                        fingerprint, record, step, candidate,
                    )
            elif observed_parents:
                raise GitHubProjectsTrackerError(
                    "issue.create_reconcile", "partial_parent_conflict",
                )
            else:
                if parent_number is None:
                    raise GitHubProjectsTrackerError(
                        "issue.create_reconcile", "missing_parent_coordinate",
                    )
                record = self._begin_create_step(
                    fingerprint, record, step, candidate,
                )
                try:
                    self._rest_write(
                        "POST",
                        f"repos/{binding.repo}/issues/{parent_number}/sub_issues",
                        {"sub_issue_id": candidate.native_id, "replace_parent": False},
                        "issue.parent_create",
                    )
                except GitHubProjectsTrackerError as exc:
                    refreshed = self._created_issue_readback(binding, candidate)
                    if not self._has_link(refreshed, "subtask-of", parent):
                        raise GitHubProjectsPartialCreateError(step, candidate) from exc
                    created = refreshed
                    record = self._complete_create_step(
                        fingerprint, record, step, candidate,
                    )
                else:
                    created = self._created_issue_readback(binding, candidate)
                    if not self._has_link(created, "subtask-of", parent):
                        raise GitHubProjectsPartialCreateError(
                            "parent_readback", candidate,
                        )
                    record = self._complete_create_step(
                        fingerprint, record, step, candidate,
                    )

        created = self._created_issue_readback(binding, candidate)
        if (
            created.title != title
            or (created.body or "") != body
            or self._snapshot(created, requested) != requested
        ):
            raise TrackerConflictError(
                "GitHub issue divergente après création ; aucune seconde tentative",
            )
        if parent is not None and not self._has_link(created, "subtask-of", parent):
            raise TrackerConflictError(
                "parent GitHub absent après création ; aucune seconde tentative",
            )
        if record["step"] is not None:
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "incoherent_partial_step",
            )
        try:
            self._write_create_intent(
                fingerprint, {**record, "state": "complete", "attempt": 0},
            )
        except GitHubProjectsTrackerError as exc:
            raise GitHubProjectsPartialCreateError(
                "local_observation:complete", candidate,
            ) from exc
        return created

    def _created_issue_readback(
        self, binding: _Binding, candidate: _CreateCandidate,
    ) -> Issue:
        """Hydrate the exact create candidate while other partial items stay isolated."""
        observed = self._partial_project_issue(binding, candidate)
        if observed is None:
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "known_project_item_missing",
            )
        if observed[1].type is None:
            raise GitHubProjectsTrackerError(
                "issue.create_reconcile", "known_project_item_incomplete",
            )
        return self._hydrate_issue(observed[1], binding)

    def create_issue(self, project, title, body, fields=None, parent=None):
        binding = self._authoritative_binding(project)
        portable = self._portable_fields(fields)
        if not isinstance(title, str) or not title or not isinstance(body, str):
            raise GitHubProjectsTrackerError("issue.create", "invalid_issue_payload")
        parent_number = None
        if parent is not None:
            # A canonical repository URL is insufficient: the parent must be one
            # unique non-ADR delivery item in the exact bound Project before any
            # child effect can begin.
            parent_number = self._create_parent(parent, binding)
        # ``get_issue`` requires a typed Project item.  Establish its explicit
        # Type default and validate the complete catalog before creating the
        # REST issue, so an unsupported option cannot be discovered afterwards.
        requested = {"type": "Task", **portable}
        catalog = self._write_catalog(binding, requested)
        fingerprint = self._create_fingerprint(
            binding, title, body, requested, parent,
        )
        with self._create_intent_lock(fingerprint):
            record = self._read_create_intent(fingerprint)
            candidate = self._observe_create_candidate(
                binding, title, body, record,
            )
            if record is None:
                record = self._intent_record(fingerprint, "pending")
                self._write_create_intent(fingerprint, record)
                lost_response: GitHubProjectsTrackerError | None = None
                try:
                    raw = self._rest_write(
                        "POST", f"repos/{binding.repo}/issues",
                        {"title": title, "body": body}, "issue.create",
                    )
                    candidate = self._create_candidate(
                        raw, binding, title, body, "issue.create",
                    )
                    if candidate is None:
                        raise GitHubProjectsTrackerError(
                            "issue.create", "ambiguous_mutation_response",
                        )
                except GitHubProjectsTrackerError as exc:
                    candidate = self._observe_create_candidate(
                        binding, title, body, record,
                    )
                    lost_response = exc
                record = self._intent_record(
                    fingerprint, "known", candidate,
                )
                try:
                    self._write_create_intent(fingerprint, record)
                except GitHubProjectsTrackerError as exc:
                    raise GitHubProjectsPartialCreateError(
                        "local_observation:issue", candidate,
                    ) from exc
                if lost_response is not None:
                    raise GitHubProjectsPartialCreateError(
                        "issue_response_lost", candidate,
                    ) from lost_response
            elif candidate is not None and record["state"] == "pending":
                record = self._intent_record(
                    fingerprint, "known", candidate,
                )
                try:
                    self._write_create_intent(fingerprint, record)
                except GitHubProjectsTrackerError as exc:
                    raise GitHubProjectsPartialCreateError(
                        "local_observation:issue", candidate,
                    ) from exc
            if candidate is None:
                raise GitHubProjectsTrackerError(
                    "issue.create_reconcile", "create_effect_unknown",
                )
            try:
                return self._resume_created_issue(
                    binding, fingerprint, record, candidate, title, body, requested,
                    catalog, parent, parent_number,
                )
            except GitHubProjectsPartialCreateError:
                raise
            except TrackerConflictError as exc:
                raise GitHubProjectsPartialCreateConflict(str(exc), candidate) from exc
            except GitHubProjectsTrackerError as exc:
                raise GitHubProjectsPartialCreateError(
                    f"reconcile:{exc.reason}", candidate,
                ) from exc

    def update_fields(self, issue_id, fields, project=None):
        active = self._project()
        binding = self._authoritative_binding(project or active)
        portable = self._portable_fields(fields)
        if not portable:
            return self.get_issue(issue_id)
        before = self.get_issue(issue_id)
        expected = self._snapshot(before, portable)
        fresh = self.get_issue(issue_id)
        if self._snapshot(fresh, portable) != expected:
            raise TrackerConflictError("champs GitHub modifiés avant écriture bornée")
        item_id, _ = self._item_coordinate(issue_id, binding)
        catalog = self._write_catalog(binding, portable)
        # Each provider call has a narrow, independently verified target.  A later
        # field failure leaves a known partial result and is never retried here.
        for semantic, value in portable.items():
            self._set_project_field(item_id, binding.project_id, catalog[semantic], value)
        readback = self.get_issue(issue_id)
        if self._snapshot(readback, portable) != portable:
            raise TrackerConflictError("champs GitHub divergents après écriture ; aucune seconde tentative")
        return readback

    def update_body(self, resource, expected_body, updated_body, project=None):
        if not isinstance(resource, Issue) or not isinstance(expected_body, str) or not isinstance(updated_body, str):
            raise TrackerCapabilityUnavailableError(self.name, "issue_body_only")
        binding = self._authoritative_binding(project or self._project())
        number = self._number(resource.id, binding)
        current = self.get_issue(resource.id)
        if current.body != expected_body:
            raise TrackerConflictError("corps GitHub modifié avant écriture bornée")
        if current.body == updated_body:
            return False
        self._rest_write("PATCH", f"repos/{binding.repo}/issues/{number}", {"body": updated_body}, "issue.body_write")
        if self.get_issue(resource.id).body != updated_body:
            raise TrackerConflictError("corps GitHub divergent après écriture ; aucune seconde tentative")
        return True

    def set_state(self, issue_id, state, context=None, project=None): raise TrackerCapabilityUnavailableError(self.name, "set_state")
    @staticmethod
    def _has_link(issue: Issue, link_type: str, target: str) -> bool:
        return any(link.type == link_type and link.target == target for link in issue.links)

    def link(self, src_id, link_type, dst_id, project=None):
        if link_type not in {"subtask-of", "parent-of", "depends-on", "blocks", "relates"}:
            raise TrackerCapabilityUnavailableError(self.name, f"link:{link_type}")
        binding = self._authoritative_binding(project or self._project())
        # Both endpoint GETs prove raw REST URLs and repository ownership before
        # a mutation; they also supply the distinct integer native IDs required by REST.
        src_number, src_native = self._native_issue(src_id, binding, "issue.link_prewrite")
        dst_number, dst_native = self._native_issue(dst_id, binding, "issue.link_prewrite")
        before_src, before_dst = self.get_issue(src_id), self.get_issue(dst_id)
        if self._has_link(before_src, link_type, dst_id):
            return
        fresh_src, fresh_dst = self.get_issue(src_id), self.get_issue(dst_id)
        if fresh_src.links != before_src.links or fresh_dst.links != before_dst.links:
            raise TrackerConflictError("liens GitHub modifiés avant écriture bornée")
        if link_type == "subtask-of":
            path, payload = f"repos/{binding.repo}/issues/{dst_number}/sub_issues", {"sub_issue_id": src_native, "replace_parent": True}
        elif link_type == "parent-of":
            path, payload = f"repos/{binding.repo}/issues/{src_number}/sub_issues", {"sub_issue_id": dst_native, "replace_parent": True}
        elif link_type == "depends-on":
            path, payload = f"repos/{binding.repo}/issues/{src_number}/dependencies/blocked_by", {"issue_id": dst_native}
        elif link_type == "blocks":
            path, payload = f"repos/{binding.repo}/issues/{dst_number}/dependencies/blocked_by", {"issue_id": src_native}
        elif link_type == "relates":
            _, src_node = self._item_coordinate(src_id, binding)
            _, dst_node = self._item_coordinate(dst_id, binding)
            query = """mutation($issue:ID!,$related:ID!){addRelatesTo(input:{issueId:$issue,relatedIssueId:$related}){issue{id} relatedIssue{id}}}"""
            data = self._graphql_mutation(query, {"issue": src_node, "related": dst_node}, "issue.relates_write")
            result = data.get("addRelatesTo")
            if (not isinstance(result, dict) or not isinstance(result.get("issue"), dict)
                    or not isinstance(result.get("relatedIssue"), dict)
                    or result["issue"].get("id") != src_node
                    or result["relatedIssue"].get("id") != dst_node):
                raise GitHubProjectsTrackerError("issue.relates_write", "ambiguous_mutation_response")
            path = None
        else:
            raise TrackerCapabilityUnavailableError(self.name, f"link:{link_type}")
        if path is not None:
            self._rest_write("POST", path, payload, "issue.link_write")
        if not self._has_link(self.get_issue(src_id), link_type, dst_id):
            raise TrackerConflictError("lien GitHub divergent après écriture ; aucune seconde tentative")

    def add_comment(self, issue_id, text, project=None):
        binding = self._authoritative_binding(project or self._project())
        if not isinstance(text, str) or not text:
            raise GitHubProjectsTrackerError("issue.comment", "invalid_comment")
        number, _ = self._native_issue(issue_id, binding, "issue.comment_prewrite")
        # Free-text progress notes do not carry state.  A lost response may have
        # created one note; it is deliberately surfaced, never blindly replayed.
        raw = self._rest_write("POST", f"repos/{binding.repo}/issues/{number}/comments", {"body": text}, "issue.comment")
        if (not isinstance(raw, dict) or type(raw.get("id")) is not int or raw.get("id", 0) < 1
                or raw.get("body") != text
                or raw.get("issue_url") != f"{self._api_repo(binding)}/issues/{number}"):
            raise GitHubProjectsTrackerError("issue.comment", "ambiguous_mutation_response")
        observed = self._rows(
            f"repos/{binding.repo}/issues/{number}/comments", "issue.comment_readback",
        )
        if not any(row.get("id") == raw["id"] and row.get("body") == text for row in observed):
            raise TrackerConflictError("commentaire GitHub divergent après écriture ; aucune seconde tentative")
    def list_adrs(self, project: Project) -> list[Adr]: raise TrackerCapabilityUnavailableError(self.name, "adr_index")
    def create_adr(self, project, title, body, status="proposed"): raise TrackerCapabilityUnavailableError(self.name, "adr_create")
    def set_adr_status(self, adr, status, project=None): raise TrackerCapabilityUnavailableError(self.name, "adr_status")
