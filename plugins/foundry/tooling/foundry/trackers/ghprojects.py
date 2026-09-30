"""Bounded adapter for qualified private personal GitHub Projects V2.

Project items and fields are GraphQL, while canonical Issues, comments and
relations are REST.  The adapter never guesses an owner, project or repository
from token state. Existing-record writes use bounded detection, not CAS; issue
creation additionally keeps a machine-local intent so an unknown effect is not
blindly posted again.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
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
from typing import Any, Callable, Iterator

from foundry import registry
from foundry.models import (
    Adr, Issue, Link, Project, ReleaseIssue, ReleaseScope, TransitionContext,
)
from foundry.trackers.base import (
    AdrIssueUnavailableError,
    TrackerConflictError,
    AcceptanceSyncUnavailableError,
    IssueUnavailableError,
    ReleaseScopeUnavailableError,
    Tracker,
    TrackerCapabilityUnavailableError,
)

_MAX_PAGES, _TRANSPORT_TIMEOUT_SECONDS, _ADR_LABEL = 100, 30, "foundry:adr"
_ADR_SCHEMA = "foundry-ghprojects-adr.v1"
_ADR_HEADER = "<!-- foundry-ghprojects-adr.v1\n"
_ADR_HEAD = "foundry-head:v1"
_ADR_TRANSITIONS = {
    "proposed": {"accepted", "deprecated"},
    "accepted": {"deprecated"},
    "deprecated": set(),
    "superseded": set(),
}
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
_CREATE_STEPS = {
    "item", "parent", *(f"field:{name}" for name in _FIELDS),
    "adr:identity", "adr:label", "adr:item", "adr:version", "adr:head",
}
_LIFECYCLE_SCHEMA = "foundry-ghprojects-lifecycle.v1"
_LIFECYCLE_INTENT_SCHEMA = "foundry-ghprojects-lifecycle-intent.v1"
_LIFECYCLE_HEADER = "Foundry lifecycle proof (append-only)."
_LIFECYCLE_OPERATIONS = frozenset({"state-in-progress", "state-review", "state-done", "acceptance"})
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


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
    adr_issue_link_supported = True
    name = "ghprojects"
    requires_mutation_binding = True
    bounded_transition_proofs = True
    bounded_state_transitions = True
    append_only_lifecycle_supported = True
    acceptance_proof_projection_supported = True

    def __init__(self, *, runner=subprocess.run, state_dir: Path | str | None = None):
        self._runner = runner
        base = Path(state_dir) if state_dir is not None else Path(registry.data_dir())
        self._create_intent_dir = base / "ghprojects-create-intents"
        self._lifecycle_intent_dir = base / "ghprojects-lifecycle-intents"

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

    @contextmanager
    def _adr_corpus_lock(self, binding: _Binding) -> Iterator[None]:
        """Serialize machine-local ADR allocation for one qualified corpus.

        This is only a local contention aid.  It does not claim provider CAS or
        exclude a writer on another machine.
        """
        corpus = hashlib.sha256(
            f"{binding.project_id}\0{binding.repo}\0{binding.key}".encode("utf-8"),
        ).hexdigest()
        with self._create_intent_lock(f"adr-corpus-{corpus}"):
            yield

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
        legacy_keys = {
            "attempt", "content_id", "fingerprint", "issue_id", "native_id",
            "schema", "state", "step",
        }
        if not isinstance(raw, dict) or frozenset(raw) not in {
            frozenset(legacy_keys), frozenset({*legacy_keys, "adr_id"}),
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
        adr_id = raw.get("adr_id")
        if adr_id is not None and (
            not isinstance(adr_id, str)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-ADR-\d{4}", adr_id) is None
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
            or step is None and attempt != 0 and not (
                state == "pending" and adr_id is not None and attempt == 1
            )
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
        adr_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "adr_id": adr_id,
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

    def _rest_labels(self, binding: _Binding, number: int) -> None:
        """Apply the one reserved ADR discriminator without touching other labels."""
        raw = self._run(
            ["gh", "api", "-X", "POST", f"repos/{binding.repo}/issues/{number}/labels",
             "-f", f"labels[]={_ADR_LABEL}"],
            "adr.label_write",
        )
        if not isinstance(raw, list) or not any(
            isinstance(label, dict) and label.get("name") == _ADR_LABEL for label in raw
        ):
            raise GitHubProjectsTrackerError("adr.label_write", "ambiguous_mutation_response")

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
                    mapping = project.extra.get("release_ids", {})
                    if not isinstance(mapping, dict):
                        return False
                    for release, encoded in mapping.items():
                        if (
                            not isinstance(encoded, str)
                            or not encoded.isdigit()
                            or int(encoded) < 1
                        ):
                            return False
                        number = int(encoded)
                        milestone = self._rest(
                            f"repos/{binding.repo}/milestones/{number}",
                            "release.identity",
                        )
                        if (
                            not isinstance(milestone, dict)
                            or milestone.get("number") != number
                            or milestone.get("title") != release
                            or type(milestone.get("id")) is not int
                            or milestone["id"] < 1
                            or not isinstance(milestone.get("node_id"), str)
                            or not milestone["node_id"]
                            or milestone.get("url") != (
                                f"{self._api_repo(binding)}/milestones/{number}"
                            )
                            or milestone.get("html_url") != (
                                f"https://github.com/{binding.repo}/milestone/{number}"
                            )
                        ):
                            return False
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
        return [
            self._projection(
                issue.id,
                self._hydrate_issue(issue, binding),
                strict=True,
                binding=binding,
            )
            for issue in raw
        ]

    def read_release_scope(self, project: Project, release: str) -> ReleaseScope:
        binding = self._authoritative_binding(project)
        mapping = project.extra.get("release_ids")
        if not isinstance(mapping, dict) or release not in mapping:
            raise ReleaseScopeUnavailableError(self.name, release, "unmapped")
        encoded = mapping[release]
        if not isinstance(encoded, str) or not encoded.isdigit() or int(encoded) < 1:
            raise ReleaseScopeUnavailableError(self.name, release, "invalid_mapping")
        number = int(encoded)
        try:
            native = self._rest(
                f"repos/{binding.repo}/milestones/{number}", "release.read",
            )
        except GitHubProjectsTrackerError as exc:
            if exc.reason in {"not_found", "permission_denied", "authentication_failed"}:
                raise ReleaseScopeUnavailableError(
                    self.name, release, "inaccessible_or_absent"
                ) from None
            raise
        api_repo = self._api_repo(binding)
        if (
            not isinstance(native, dict)
            or native.get("number") != number
            or native.get("title") != release
            or native.get("url") != f"{api_repo}/milestones/{number}"
            or native.get("html_url") != f"https://github.com/{binding.repo}/milestone/{number}"
            or type(native.get("id")) is not int
            or native["id"] < 1
            or not isinstance(native.get("node_id"), str)
            or not native["node_id"]
            or native.get("state") not in {"open", "closed"}
        ):
            raise ReleaseScopeUnavailableError(self.name, release, "mapping_mismatch")

        rows = self._rows(
            f"repos/{binding.repo}/issues?state=all&milestone={number}",
            "release.issues",
        )
        release_rows: dict[int, dict] = {}
        for row in rows:
            if row.get("pull_request") is not None:
                continue
            issue_number = self._issue_number_from_rest(row, binding, "release.issues")
            native_issue_state = row.get("state")
            if (
                not isinstance(native_issue_state, str)
                or native_issue_state not in {"open", "closed"}
            ):
                raise ReleaseScopeUnavailableError(
                    self.name, release, "invalid_issue_state"
                )
            milestone = row.get("milestone")
            if (
                not isinstance(milestone, dict)
                or milestone.get("number") != number
                or milestone.get("title") != release
            ):
                raise ReleaseScopeUnavailableError(
                    self.name, release, "membership_mismatch"
                )
            release_rows[issue_number] = row

        project_issues = {
            self._number(issue.id, binding): issue
            for issue in self._search_raw(project)
        }
        if not set(release_rows).issubset(project_issues):
            raise ReleaseScopeUnavailableError(
                self.name, release, "issue_outside_product_project"
            )
        issues: list[ReleaseIssue] = []
        for issue_number in sorted(release_rows):
            issue = self._hydrate_issue(project_issues[issue_number], binding)
            row = release_rows[issue_number]
            terminal = row["state"] == "closed" or str(issue.state or "").casefold() in {
                "done", "completed", "fixed", "dropped",
            }
            references = {
                "provider_issue_id": row.get("node_id"),
                "issue_url": row.get("html_url"),
            }
            issues.append(ReleaseIssue(
                id=issue.id,
                title=issue.title,
                type=issue.type,
                state=issue.state,
                labels=tuple(issue.labels),
                # PAT-67 owns proof-bound lifecycle projection. A terminal Project
                # state, Issue closure, or prerequisite PR mention cannot prove delivery.
                disposition="unavailable" if terminal else "unfinished",
                references={key: value for key, value in references.items() if value},
            ))
        return ReleaseScope(
            provider=self.name,
            project_key=project.key,
            project_id=project.id,
            release=release,
            release_id=encoded,
            native_state=native["state"],
            issues=tuple(issues),
            closure={
                "mode": "operator",
                "action": "close-repository-milestone",
                "native_mutation": True,
                "preconditions": ["unfinished=0", "unavailable=0"],
                "verification": "read-release-scope",
            },
            coordinates={
                "project_id": binding.project_id,
                "repository": binding.repo,
                "milestone_number": number,
                "milestone_id": native["id"],
                "milestone_node_id": native["node_id"],
            },
        )

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

    def _native_issue_read(self, issue_id: str) -> Issue:
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

    @staticmethod
    def _issue_snapshot(issue: Issue) -> dict[str, Any]:
        """Capture every normalized property observable around a narrow field write."""
        return issue.to_dict()

    @classmethod
    def _field_result_snapshot(
        cls, issue: Issue, semantic: str, value: Any,
    ) -> dict[str, Any]:
        expected = cls._issue_snapshot(issue)
        expected[semantic] = value
        if semantic == "state":
            # PAT-57 exposes this Project field through both the legacy display
            # value and the explicit normalized-state observation.
            expected["normalized_state"] = value
        return expected

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
        return self._create_parent_snapshot(issue_id, binding)[0]

    def _create_parent_snapshot(self, issue_id: str, binding: _Binding) -> tuple[int, Issue]:
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
        return number, self._hydrate_issue(observed[1], binding)

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

    @staticmethod
    def _created_parent_pair(child: Issue, parent: Issue) -> bool:
        parents = [link.target for link in child.links if link.type == "subtask-of"]
        children = [link.target for link in parent.links if link.type == "parent-of"]
        return parents == [parent.id] and children.count(child.id) == 1

    @staticmethod
    def _parent_unaffected(issue: Issue, link_type: str, target: str) -> dict[str, Any]:
        snapshot = {k: v for k, v in issue.to_dict().items() if k not in {"links", "updated"}}
        snapshot["links"] = sorted(
            (link.type, link.direction, link.target) for link in issue.links
            if not (link.type == link_type and link.target == target)
        )
        return snapshot

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
                and not self._created_parent_pair(
                    completed, self._create_parent_snapshot(parent, binding)[1],
                )
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
            fresh_number, before_parent = self._create_parent_snapshot(parent, binding)
            if fresh_number != parent_number:
                raise TrackerConflictError("coordonnée parent modifiée avant attachement")
            observed_parents = [
                link.target for link in created.links if link.type == "subtask-of"
            ]
            reciprocal = self._has_link(before_parent, "parent-of", candidate.issue_id)
            if self._created_parent_pair(created, before_parent):
                if record["step"] == step:
                    record = self._complete_create_step(fingerprint, record, step, candidate)
            elif observed_parents or reciprocal:
                raise TrackerConflictError("parent GitHub divergent ou asymétrique ; aucune réparation automatique")
            else:
                record = self._begin_create_step(fingerprint, record, step, candidate)
                write_error: GitHubProjectsTrackerError | None = None
                try:
                    self._rest_write(
                        "POST",
                        f"repos/{binding.repo}/issues/{fresh_number}/sub_issues",
                        {"sub_issue_id": candidate.native_id, "replace_parent": False},
                        "issue.parent_create",
                    )
                except GitHubProjectsTrackerError as exc:
                    write_error = exc
                refreshed = self._created_issue_readback(binding, candidate)
                after_parent = self._create_parent_snapshot(parent, binding)[1]
                if not self._created_parent_pair(refreshed, after_parent):
                    if (write_error is not None and refreshed.to_dict() == created.to_dict()
                            and after_parent.to_dict() == before_parent.to_dict()):
                        raise GitHubProjectsPartialCreateError(step, candidate) from write_error
                    raise TrackerConflictError("parent GitHub absent ou asymétrique après attachement") from write_error
                if (
                    self._parent_unaffected(created, "subtask-of", parent)
                    != self._parent_unaffected(refreshed, "subtask-of", parent)
                    or self._parent_unaffected(before_parent, "parent-of", candidate.issue_id)
                    != self._parent_unaffected(after_parent, "parent-of", candidate.issue_id)
                ):
                    raise TrackerConflictError("propriétés non visées modifiées après attachement parent")
                created = refreshed
                record = self._complete_create_step(fingerprint, record, step, candidate)

        created = self._created_issue_readback(binding, candidate)
        if (
            created.title != title
            or (created.body or "") != body
            or self._snapshot(created, requested) != requested
        ):
            raise TrackerConflictError(
                "GitHub issue divergente après création ; aucune seconde tentative",
            )
        if parent is not None and not self._created_parent_pair(
            created, self._create_parent_snapshot(parent, binding)[1],
        ):
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
        if not self.verify_project_identity(project):
            raise GitHubProjectsTrackerError("issue.create_prewrite", "unqualified_repository_project")
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
        current = self.get_issue(issue_id)
        if self._snapshot(current, portable) == portable:
            return current
        observed = self._issue_snapshot(current)
        item_id, _ = self._item_coordinate(issue_id, binding)
        catalog = self._write_catalog(binding, portable)
        # GitHub has no multi-field transaction.  Give every individual field its
        # own S1/S2/S3 observation boundary, and advance to the next field only
        # after the previous target and all unrelated observable properties match.
        for semantic, value in portable.items():
            if getattr(current, semantic) == value:
                continue
            fresh = self.get_issue(issue_id)
            if self._issue_snapshot(fresh) != observed:
                raise TrackerConflictError(
                    "champs GitHub modifiés avant écriture bornée",
                )
            expected = self._field_result_snapshot(fresh, semantic, value)
            write_error: GitHubProjectsTrackerError | None = None
            try:
                self._set_project_field(
                    item_id, binding.project_id, catalog[semantic], value,
                )
            except GitHubProjectsTrackerError as exc:
                # A transport loss may follow a committed GraphQL mutation.  The
                # sole recovery is this authoritative observation; never rewrite.
                write_error = exc
            readback = self.get_issue(issue_id)
            actual = self._issue_snapshot(readback)
            if actual == expected:
                current, observed = readback, actual
                continue
            if write_error is not None and actual == observed:
                raise write_error
            raise TrackerConflictError(
                "champ GitHub divergent après écriture ; aucune seconde tentative",
            ) from write_error
        return current

    def _update_issue_body(self, resource, expected_body, updated_body, project=None):
        if not isinstance(resource, Issue) or not isinstance(expected_body, str) or not isinstance(updated_body, str):
            raise TrackerCapabilityUnavailableError(self.name, "issue_body_only")
        binding = self._authoritative_binding(project or self._project())
        number = self._number(resource.id, binding)
        current = self.get_issue(resource.id)
        if current.body == updated_body:
            return False
        if current.body != expected_body:
            raise TrackerConflictError("corps GitHub modifié avant écriture bornée")
        write_error: GitHubProjectsTrackerError | None = None
        try:
            self._rest_write("PATCH", f"repos/{binding.repo}/issues/{number}", {"body": updated_body}, "issue.body_write")
        except GitHubProjectsTrackerError as exc:
            write_error = exc
        readback = self.get_issue(resource.id)
        # The Issue timestamp and checkbox counters derive from the body PATCH;
        # every other observable property must remain at its predecessor value.
        ignored = {"body", "updated", "ac_done", "ac_total"}
        before_other = {k: v for k, v in current.to_dict().items() if k not in ignored}
        after_other = {k: v for k, v in readback.to_dict().items() if k not in ignored}
        if readback.body == updated_body and after_other == before_other:
            return True
        if write_error is not None and readback.to_dict() == current.to_dict():
            raise write_error
        raise TrackerConflictError("corps GitHub divergent après écriture ; aucune seconde tentative") from write_error

    # ---- PAT-67 proof-bound lifecycle ---------------------------------
    # GitHub comments do not offer a caller-selected id.  The deterministic
    # marker therefore identifies an owned receipt; every replay first reads the
    # complete comment history and either observes that exact body once or
    # refuses.  A free-text comment is never decoded as a lifecycle receipt.
    @staticmethod
    def _lifecycle_scope(
        binding: _Binding,
        issue_id: str,
        number: int,
        native_id: int,
        item_id: str,
        node_id: str,
    ) -> dict[str, Any]:
        scope = {
            "repository": binding.repo,
            "project_id": binding.project_id,
            "project_number": binding.number,
            "project_key": binding.key,
            "issue_id": issue_id,
            "issue_number": number,
            "native_issue_id": native_id,
            "issue_node_id": node_id,
            "project_item_id": item_id,
        }
        if (
            not all(isinstance(scope[key], str) and scope[key] for key in {
                "repository", "project_id", "project_key", "issue_id",
                "issue_node_id", "project_item_id",
            })
            or type(number) is not int or number < 1
            or type(native_id) is not int or native_id < 1
            or type(binding.number) is not int or binding.number < 1
        ):
            raise TrackerConflictError("GitHub lifecycle native coordinates invalid")
        return scope

    def _current_lifecycle_scope(
        self, issue_id: str, binding: _Binding,
    ) -> dict[str, Any]:
        number, native_id = self._native_issue(
            issue_id, binding, "lifecycle.native_issue",
        )
        item_id, node_id = self._item_coordinate(issue_id, binding)
        return self._lifecycle_scope(
            binding, issue_id, number, native_id, item_id, node_id,
        )

    @staticmethod
    def _lifecycle_marker(
        operation: str,
        issue_id: str,
        payload: dict,
        scope: dict[str, Any],
    ) -> tuple[str, str]:
        if operation not in _LIFECYCLE_OPERATIONS:
            raise TrackerCapabilityUnavailableError("ghprojects", f"lifecycle:{operation}")
        coordinates = json.dumps({"schema": _LIFECYCLE_SCHEMA, "operation": operation,
                                  "issue": issue_id, "scope": scope, "payload": payload},
                                 sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        marker = f"{_LIFECYCLE_SCHEMA}:{operation}:{hashlib.sha256(coordinates.encode('ascii')).hexdigest()}"
        return marker, f"{_LIFECYCLE_HEADER}\nmarker: {marker}\ncoordinates: {coordinates}"

    @classmethod
    def _decode_lifecycle_comment(
        cls,
        issue_id: str,
        body: object,
        expected_scope: dict[str, Any] | None = None,
    ) -> tuple[str, dict, str] | None:
        if not isinstance(body, str) or not body.startswith(_LIFECYCLE_HEADER):
            return None
        lines = body.splitlines()
        if len(lines) != 3 or not lines[1].startswith("marker: ") or not lines[2].startswith("coordinates: "):
            raise TrackerConflictError("GitHub lifecycle comment malformed")
        try:
            value = json.loads(lines[2].removeprefix("coordinates: "))
        except (TypeError, ValueError, RecursionError) as exc:
            raise TrackerConflictError("GitHub lifecycle comment malformed") from exc
        scope = value.get("scope") if isinstance(value, dict) else None
        if (not isinstance(value, dict) or set(value) != {"schema", "operation", "issue", "scope", "payload"}
                or value.get("schema") != _LIFECYCLE_SCHEMA or value.get("issue") != issue_id
                or value.get("operation") not in _LIFECYCLE_OPERATIONS
                or not isinstance(value.get("payload"), dict)
                or not isinstance(scope, dict)
                or set(scope) != {
                    "repository", "project_id", "project_number", "project_key",
                    "issue_id", "issue_number", "native_issue_id", "issue_node_id",
                    "project_item_id",
                }
                or scope.get("issue_id") != issue_id
                or not all(isinstance(scope.get(key), str) and scope[key] for key in {
                    "repository", "project_id", "project_key", "issue_node_id",
                    "project_item_id",
                })
                or type(scope.get("project_number")) is not int
                or scope["project_number"] < 1
                or type(scope.get("issue_number")) is not int
                or scope["issue_number"] < 1
                or type(scope.get("native_issue_id")) is not int
                or scope["native_issue_id"] < 1
                or expected_scope is not None and scope != expected_scope):
            raise TrackerConflictError("GitHub lifecycle comment malformed")
        marker, expected = cls._lifecycle_marker(
            value["operation"], issue_id, value["payload"], scope,
        )
        if lines[1].removeprefix("marker: ") != marker or expected != body:
            raise TrackerConflictError("GitHub lifecycle comment integrity invalid")
        return value["operation"], value["payload"], body

    @staticmethod
    def _state_payload(state: str, context: TransitionContext | None) -> dict:
        payload = {"state": state}
        if state in {"review", "done"}:
            if not isinstance(context, TransitionContext) or not all((context.pr_url, context.head_sha, context.base_sha, context.review_digest)):
                raise TrackerCapabilityUnavailableError("ghprojects", "lifecycle-proof")
            if (not context.pr_url.startswith("https://github.com/") or _SHA.fullmatch(context.head_sha) is None
                    or _SHA.fullmatch(context.base_sha) is None or _DIGEST.fullmatch(context.review_digest) is None):
                raise TrackerConflictError("GitHub lifecycle coordinates invalid")
            payload.update(pr_url=context.pr_url, head_sha=context.head_sha, base_sha=context.base_sha,
                           review_digest=context.review_digest)
        if state == "done":
            if not isinstance(context, TransitionContext) or _SHA.fullmatch(context.merge_sha or "") is None:
                raise TrackerCapabilityUnavailableError("ghprojects", "merge-proof")
            payload["merge_sha"] = context.merge_sha
        return payload

    @staticmethod
    def _lifecycle_source_digest(issue: Issue) -> str:
        """Digest the complete pre-effect business snapshot, excluding observations."""
        ignored = {
            "comments", "created", "updated", "normalized_state", "native_state",
            "projection_status",
        }
        source = {
            key: value for key, value in issue.to_dict().items() if key not in ignored
        }
        return hashlib.sha256(
            json.dumps(
                source, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            ).encode("ascii"),
        ).hexdigest()

    @staticmethod
    def _unrelated_state_snapshot(issue: Issue) -> dict[str, Any]:
        ignored = {
            "state", "normalized_state", "native_state", "projection_status",
            "updated",
        }
        return {
            key: value for key, value in issue.to_dict().items() if key not in ignored
        }

    @staticmethod
    def _state_source_snapshot(issue: Issue) -> dict[str, Any]:
        """Compare business data across the receipt append, excluding that comment."""
        ignored = {
            "state", "comments", "normalized_state", "native_state",
            "projection_status", "updated",
        }
        return {
            key: value for key, value in issue.to_dict().items() if key not in ignored
        }

    @staticmethod
    def _comment_effect_snapshot(issue: Issue) -> dict[str, Any]:
        """Protect every non-comment field around a comment-only effect."""
        ignored = {
            "comments", "normalized_state", "native_state", "projection_status",
            "updated",
        }
        return {
            key: value for key, value in issue.to_dict().items() if key not in ignored
        }

    def _lifecycle_rows(
        self,
        issue_id: str,
        issue: Issue,
        scope: dict[str, Any],
    ) -> dict[str, list[tuple[dict, str]]]:
        rows: dict[str, list[tuple[dict, str]]] = {}
        for row in issue.comments:
            decoded = self._decode_lifecycle_comment(
                issue_id, row.get("text"), scope,
            )
            if decoded is not None:
                operation, payload, body = decoded
                rows.setdefault(operation, []).append((payload, body))
        return rows

    def _validate_lifecycle_order(
        self,
        issue_id: str,
        issue: Issue,
        scope: dict[str, Any],
    ) -> None:
        started = False
        latest_review = 0
        accepted: set[int] = set()
        done = False
        for row in issue.comments:
            decoded = self._decode_lifecycle_comment(
                issue_id, row.get("text"), scope,
            )
            if decoded is None:
                continue
            operation, payload, _body = decoded
            if done:
                raise TrackerConflictError("GitHub lifecycle history out of order")
            if operation == "state-in-progress":
                if started or latest_review or accepted:
                    raise TrackerConflictError("GitHub lifecycle history out of order")
                started = True
            elif operation == "state-review":
                generation = payload.get("generation")
                if (
                    not started
                    or type(generation) is not int
                    or generation != latest_review + 1
                ):
                    raise TrackerConflictError("GitHub lifecycle history out of order")
                latest_review = generation
            elif operation == "acceptance":
                generation = payload.get("review_generation")
                if generation != latest_review or generation in accepted:
                    raise TrackerConflictError("GitHub lifecycle history out of order")
                accepted.add(generation)
            elif operation == "state-done":
                if (
                    latest_review < 1
                    or payload.get("review_generation") != latest_review
                    or latest_review not in accepted
                ):
                    raise TrackerConflictError("GitHub lifecycle history out of order")
                done = True

    def _lifecycle_intent_path(self, fingerprint: str) -> Path:
        return self._lifecycle_intent_dir / f"{fingerprint}.json"

    @contextmanager
    def _lifecycle_intent_lock(self, fingerprint: str) -> Iterator[None]:
        try:
            self._lifecycle_intent_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._lifecycle_intent_dir.chmod(0o700)
            descriptor = os.open(
                self._lifecycle_intent_dir / f".{fingerprint}.lock",
                os.O_RDWR | os.O_CREAT,
                0o600,
            )
        except OSError as exc:
            raise GitHubProjectsTrackerError(
                "lifecycle.comment", "local_intent_unavailable",
            ) from exc
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_lifecycle_intent(self, fingerprint: str) -> dict[str, str] | None:
        try:
            record = json.loads(
                self._lifecycle_intent_path(fingerprint).read_text(encoding="utf-8"),
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise GitHubProjectsTrackerError(
                "lifecycle.comment", "invalid_local_intent",
            ) from exc
        if (
            not isinstance(record, dict)
            or set(record) != {"schema", "fingerprint", "state"}
            or record.get("schema") != _LIFECYCLE_INTENT_SCHEMA
            or record.get("fingerprint") != fingerprint
            or record.get("state") not in {"pending", "complete"}
        ):
            raise GitHubProjectsTrackerError(
                "lifecycle.comment", "invalid_local_intent",
            )
        return record

    def _write_lifecycle_intent(self, fingerprint: str, state: str) -> None:
        payload = json.dumps(
            {"schema": _LIFECYCLE_INTENT_SCHEMA, "fingerprint": fingerprint,
             "state": state},
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._lifecycle_intent_dir,
                prefix=f".{fingerprint}.", delete=False,
            ) as temporary:
                temporary_name = temporary.name
                os.chmod(temporary_name, 0o600)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self._lifecycle_intent_path(fingerprint))
            directory_fd = os.open(self._lifecycle_intent_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise GitHubProjectsTrackerError(
                "lifecycle.comment", "local_intent_unavailable",
            ) from exc
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass

    def _clear_lifecycle_intent(self, fingerprint: str) -> None:
        try:
            self._lifecycle_intent_path(fingerprint).unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise GitHubProjectsTrackerError(
                "lifecycle.comment", "local_intent_unavailable",
            ) from exc

    @staticmethod
    def _canonical_pr_url(value: object, binding: _Binding) -> bool:
        """Accept only the configured repository's canonical pull URL."""
        if not isinstance(value, str):
            return False
        match = re.fullmatch(
            r"https://github\.com/(?P<repo>[^/]+/[^/]+)/pull/(?P<number>[1-9][0-9]*)",
            value,
        )
        return match is not None and match["repo"] == binding.repo

    @classmethod
    def _validate_state_payload(
        cls, name: str, payload: dict, binding: _Binding,
    ) -> None:
        expected = {"state"}
        if name in {"review", "done"}:
            expected |= {"pr_url", "head_sha", "base_sha", "review_digest"}
        if name == "review":
            expected |= {"generation", "previous_projection_digest"}
        if name == "done":
            expected |= {"merge_sha", "review_generation"}
        expected |= {"source_state", "source_digest"}
        if (
            set(payload) != expected
            or payload.get("state") != name
            or payload.get("source_state") not in _FIELD_OPTIONS["state"]
            or _DIGEST.fullmatch(str(payload.get("source_digest"))) is None
            or (name in {"review", "done"} and not cls._canonical_pr_url(payload.get("pr_url"), binding))
            or (name in {"review", "done"} and _SHA.fullmatch(str(payload.get("head_sha"))) is None)
            or (name in {"review", "done"} and _SHA.fullmatch(str(payload.get("base_sha"))) is None)
            or (name in {"review", "done"} and _DIGEST.fullmatch(str(payload.get("review_digest"))) is None)
            or (name == "review" and (type(payload.get("generation")) is not int or payload["generation"] < 1))
            or (name == "review" and payload.get("previous_projection_digest") is not None
                and _DIGEST.fullmatch(str(payload.get("previous_projection_digest"))) is None)
            or (name == "done" and _SHA.fullmatch(str(payload.get("merge_sha"))) is None)
            or (name == "done" and (type(payload.get("review_generation")) is not int or payload["review_generation"] < 1))
            or (name == "review" and payload.get("source_state")
                != ("in-progress" if payload.get("generation") == 1 else "review"))
            or (name == "done" and payload.get("source_state") != "review")
            or (name == "in-progress" and payload.get("source_state") in {
                "review", "done", "in-progress",
            })
        ):
            raise TrackerConflictError("GitHub lifecycle state proof malformed")

    @staticmethod
    def _acceptance_body_digest(body: str) -> str:
        """Bind immutable AC semantics while ignoring checkbox progress markers."""
        from foundry.routing import acceptance_criteria, acceptance_digest

        return acceptance_digest(acceptance_criteria(body))

    @staticmethod
    def _validate_acceptance_payload(
        issue_id: str, body: str, payload: dict, review: dict | None,
    ) -> None:
        """Validate the canonical PAT-56 proof before deriving any progress."""
        if set(payload) != {"review_generation", "body_digest", "checked", "proof"}:
            raise TrackerConflictError("GitHub acceptance proof malformed")
        proof = payload.get("proof")
        try:
            from foundry.routing import (
                AcceptanceProofStore,
                RoutingConfigError,
                acceptance_criteria,
                acceptance_digest,
            )
            proof = AcceptanceProofStore._validate_proof(proof)
        except RoutingConfigError as exc:
            raise TrackerConflictError("GitHub acceptance proof malformed") from exc
        expected = acceptance_criteria(body)
        issue = proof.get("issue")
        proof_review = proof.get("review")
        coordinates = proof.get("coordinates")
        if (
            review is None
            or payload.get("body_digest") != GitHubProjectsTracker._acceptance_body_digest(body)
            or type(payload.get("checked")) is not int
            or not expected or payload["checked"] != len(expected)
            or proof.get("quality") != "mergeable"
            or not isinstance(issue, dict) or set(issue) != {"id", "ac_digest", "criteria"}
            or issue.get("id") != issue_id
            or issue.get("ac_digest") != acceptance_digest(expected)
            or issue.get("criteria") != [{**item, "verdict": "pass"} for item in expected]
            or not isinstance(proof_review, dict)
            or proof_review.get("role") != "reviewer"
            or not isinstance(coordinates, dict) or set(coordinates) != {"head", "base", "diff_hash"}
            or coordinates.get("head") != review.get("head_sha")
            or coordinates.get("base") != review.get("base_sha")
            or coordinates.get("diff_hash") != review.get("review_digest")
            or payload.get("review_generation") != review.get("generation")
        ):
            raise TrackerConflictError("GitHub acceptance proof malformed or stale")

    def _projection(
        self,
        issue_id: str,
        native: Issue,
        *,
        strict: bool,
        binding: _Binding | None = None,
        scope: dict[str, Any] | None = None,
    ) -> Issue:
        binding = binding or self._binding(self._project())
        # Native checkbox progress is observable input, never V1 AC authority.
        # Only a valid receipt for the current review generation may make it
        # positive; keep the semantic criterion count for callers either way.
        native.ac_done = 0
        has_lifecycle = any(
            isinstance(row.get("text"), str)
            and row["text"].startswith(_LIFECYCLE_HEADER)
            for row in native.comments
        )
        if not has_lifecycle:
            if native.state == "done":
                raise TrackerConflictError(
                    "GitHub native state changed outside lifecycle",
                )
            native.normalized_state = None
            native.native_state = native.state
            native.projection_status = "native-only"
            return native
        scope = scope or self._current_lifecycle_scope(issue_id, binding)
        rows = self._lifecycle_rows(issue_id, native, scope)
        for op in ("state-in-progress", "state-done"):
            if len(rows.get(op, ())) > 1:
                raise TrackerConflictError("GitHub lifecycle duplicate projection")
        in_progress = rows.get("state-in-progress", [])
        reviews = rows.get("state-review", [])
        done = rows.get("state-done", [])
        if len(done) > 1:
            raise TrackerConflictError("GitHub lifecycle duplicate projection")
        if in_progress:
            self._validate_state_payload("in-progress", in_progress[0][0], binding)
        if reviews:
            expected = 1
            previous = None
            for payload, body in reviews:
                self._validate_state_payload("review", payload, binding)
                if (payload.get("generation") != expected
                        or payload.get("previous_projection_digest") != previous):
                    raise TrackerConflictError("GitHub lifecycle review chain invalid")
                previous = hashlib.sha256(body.encode()).hexdigest()
                expected += 1
        review = reviews[-1][0] if reviews else None
        done_payload = done[0][0] if done else None
        if done_payload is not None:
            self._validate_state_payload("done", done_payload, binding)
        if done_payload is not None and (review is None or any(done_payload.get(k) != review.get(k) for k in ("pr_url", "head_sha", "base_sha", "review_digest")) or done_payload.get("review_generation") != review.get("generation")):
            raise TrackerConflictError("GitHub done proof lacks matching review")
        state = "done" if done_payload else "review" if review else "in-progress" if in_progress else None
        if native.state == "done" and state != "done":
            raise TrackerConflictError("GitHub native state changed outside lifecycle")
        if state is None:
            native.normalized_state = None
            native.native_state = native.state
            native.projection_status = "native-only"
            return native
        native.normalized_state = state
        native.native_state = native.state
        native.projection_status = "aligned" if native.state == state else "disagreement"
        native.state = state
        native.pr_url = (done_payload or review or {}).get("pr_url")
        acceptance_by_generation = {}
        for payload, _body in rows.get("acceptance", []):
            generation = payload.get("review_generation")
            if generation in acceptance_by_generation:
                raise TrackerConflictError("GitHub lifecycle duplicate projection")
            bound = next((candidate for candidate, _body in reviews if candidate.get("generation") == generation), None)
            self._validate_acceptance_payload(issue_id, native.body, payload, bound)
            acceptance_by_generation[generation] = payload
        self._validate_lifecycle_order(issue_id, native, scope)
        if review is not None and review["generation"] in acceptance_by_generation:
            native.ac_done = acceptance_by_generation[review["generation"]]["checked"]
        if done_payload is not None and review["generation"] not in acceptance_by_generation:
            raise TrackerConflictError("GitHub done proof lacks matching acceptance")
        if strict and native.projection_status != "aligned":
            raise TrackerConflictError("GitHub native state changed outside lifecycle")
        return native

    def observe_issue(self, issue_id: str) -> Issue:
        return self._projection(
            issue_id, self._native_issue_read(issue_id), strict=False,
        )

    def recover_done_projection(
        self, issue_id: str, *, pr_url: str, head_sha: str, base_sha: str,
        merge_sha: str, project: Project | None = None,
    ) -> bool:
        """Complete only the native State write of an exact, valid done receipt."""
        if project is None:
            return False
        binding = self._authoritative_binding(project)
        native = self._native_issue_read(issue_id)
        scope = self._current_lifecycle_scope(issue_id, binding)
        observed = self._projection(
            issue_id, native, strict=False, binding=binding, scope=scope,
        )
        if observed.normalized_state != "done":
            return False
        rows = self._lifecycle_rows(issue_id, native, scope)
        matches = [
            payload for payload, _body in rows.get("state-done", [])
            if (payload.get("pr_url"), payload.get("head_sha"),
                payload.get("base_sha"), payload.get("merge_sha"))
            == (pr_url, head_sha, base_sha, merge_sha)
        ]
        if len(matches) != 1:
            return False
        payload = matches[0]
        self.set_state(
            issue_id, "done",
            context=TransitionContext(
                pr_url=pr_url, head_sha=head_sha, base_sha=base_sha,
                review_digest=payload["review_digest"], merge_sha=merge_sha,
                expected_state=payload["source_state"],
            ),
            project=project,
        )
        return self.get_issue(issue_id).state == "done"

    def get_issue(self, issue_id: str) -> Issue:
        return self._projection(
            issue_id, self._native_issue_read(issue_id), strict=True,
        )

    def start_transition_path(self, current_state: str) -> tuple[str, ...]:
        if current_state not in _FIELD_OPTIONS["state"]:
            raise TrackerConflictError("GitHub lifecycle predecessor invalid")
        return () if current_state == "in-progress" else ("in-progress",)

    def preflight_issue_operation(self, operation: str) -> None:
        if operation not in {"openpr", "merge"}:
            raise TrackerCapabilityUnavailableError(self.name, f"lifecycle:{operation}")

    def _append_lifecycle(
        self,
        issue_id: str,
        binding: _Binding,
        scope: dict[str, Any],
        operation: str,
        payload: dict,
        native: Issue,
    ) -> None:
        _marker, body = self._lifecycle_marker(
            operation, issue_id, payload, scope,
        )
        fingerprint = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with self._lifecycle_intent_lock(fingerprint):
            record = self._read_lifecycle_intent(fingerprint)
            fresh_scope = self._current_lifecycle_scope(issue_id, binding)
            if fresh_scope != scope:
                raise TrackerConflictError(
                    "GitHub lifecycle native coordinates changed before append",
                )
            fresh = self._native_issue_read(issue_id)
            fresh_rows = self._lifecycle_rows(issue_id, fresh, scope)
            matches = [
                candidate for _payload, candidate in fresh_rows.get(operation, [])
                if candidate == body
            ]
            if len(matches) > 1:
                raise TrackerConflictError("GitHub lifecycle duplicate projection")
            if matches:
                self._write_lifecycle_intent(fingerprint, "complete")
                return
            if record is not None:
                if record["state"] == "pending":
                    raise GitHubProjectsTrackerError(
                        "lifecycle.comment", "effect_unknown",
                    )
                raise TrackerConflictError(
                    "GitHub lifecycle completed receipt missing",
                )
            if fresh.state != native.state:
                raise TrackerConflictError(
                    "GitHub native state changed before lifecycle append",
                )
            if self._unrelated_state_snapshot(fresh) != self._unrelated_state_snapshot(native):
                raise TrackerConflictError(
                    "GitHub lifecycle source changed before append",
                )
            self._write_lifecycle_intent(fingerprint, "pending")
            try:
                self._rest_write(
                    "POST",
                    f"repos/{binding.repo}/issues/{scope['issue_number']}/comments",
                    {"body": body},
                    "lifecycle.comment",
                )
            except GitHubProjectsTrackerError as error:
                observed = self._native_issue_read(issue_id)
                occurrences = sum(
                    row.get("text") == body for row in observed.comments
                )
                if occurrences == 1:
                    self._write_lifecycle_intent(fingerprint, "complete")
                    return
                if occurrences > 1:
                    raise TrackerConflictError(
                        "GitHub lifecycle duplicate projection",
                    ) from error
                if error.reason in {
                    "authentication_failed", "permission_denied", "not_found",
                    "rate_limited",
                }:
                    self._clear_lifecycle_intent(fingerprint)
                    raise
                raise GitHubProjectsTrackerError(
                    "lifecycle.comment", "effect_unknown",
                ) from error
            observed = self._native_issue_read(issue_id)
            if sum(row.get("text") == body for row in observed.comments) != 1:
                raise TrackerConflictError(
                    "GitHub lifecycle receipt divergent after write",
                )
            self._write_lifecycle_intent(fingerprint, "complete")

    def set_state(self, issue_id, state, context=None, project=None):
        if state not in {"in-progress", "review", "done"}:
            raise TrackerCapabilityUnavailableError(self.name, "set_state")
        binding = self._authoritative_binding(project or self._project())
        native = self._native_issue_read(issue_id)
        source_native = deepcopy(native)
        native_state = native.state
        scope = self._current_lifecycle_scope(issue_id, binding)
        rows = self._lifecycle_rows(issue_id, native, scope)
        payload = self._state_payload(state, context)
        if state == "review":
            prior_reviews = rows.get("state-review", [])
            same = [row for row in prior_reviews if all(row[0].get(key) == payload.get(key) for key in ("state", "pr_url", "head_sha", "base_sha", "review_digest"))]
            if same:
                payload = dict(same[-1][0])
            else:
                payload["generation"] = len(prior_reviews) + 1
                previous = prior_reviews[-1][1] if prior_reviews else None
                payload["previous_projection_digest"] = hashlib.sha256(previous.encode()).hexdigest() if previous else None
        if state == "done":
            review = rows.get("state-review", [])[-1][0] if rows.get("state-review") else None
            if review is None or any(payload.get(k) != review.get(k) for k in ("pr_url", "head_sha", "base_sha", "review_digest")):
                raise TrackerConflictError("GitHub done proof lacks matching review")
            payload["review_generation"] = review["generation"]
        operation = f"state-{state}"
        existing_same = [
            candidate for candidate, _body in rows.get(operation, [])
            if all(candidate.get(key) == payload.get(key) for key in payload)
        ]
        if len(existing_same) == 1:
            payload = dict(existing_same[0])
        elif not existing_same:
            expected_state = (
                context.expected_state
                if isinstance(context, TransitionContext)
                and context.expected_state is not None
                else native_state
            )
            if native_state != expected_state:
                raise TrackerConflictError("GitHub lifecycle predecessor unavailable")
            payload.update(
                source_state=expected_state,
                source_digest=self._lifecycle_source_digest(source_native),
            )
        self._validate_state_payload(state, payload, binding)
        _marker, body = self._lifecycle_marker(operation, issue_id, payload, scope)
        exact = [candidate for _p, candidate in rows.get(operation, []) if candidate == body]
        if len(exact) > 1:
            raise TrackerConflictError("GitHub lifecycle duplicate projection")
        rank = {"in-progress": 1, "review": 2, "done": 3}
        # Validate the entire existing proof graph before deciding whether this
        # call is a replay.  A hash-valid marker alone is never an authority.
        observed = self._projection(
            issue_id, native, strict=False, binding=binding, scope=scope,
        )
        observed_rank = rank.get(observed.normalized_state or "")
        native_rank = rank.get(native_state)
        repair_exact = bool(
            exact and observed.normalized_state == state
            and observed.projection_status == "disagreement"
            and native_state != state
        )
        if exact:
            # Exact historical replays are harmless once a later authenticated
            # receipt exists. They must return before predecessor checks: the
            # predecessor has naturally advanced. They never write a weaker State.
            if observed_rank is not None and observed_rank > rank[state]:
                return
            if native_rank is not None and native_rank >= rank[state]:
                if native_rank == rank[state]:
                    self._projection(issue_id, native, strict=True, binding=binding)
                return
        if repair_exact and (
            native_state != payload.get("source_state")
            or self._lifecycle_source_digest(source_native)
            != payload.get("source_digest")
        ):
            raise TrackerConflictError(
                "GitHub lifecycle exact receipt source changed",
            )
        predecessor = {"review": "in-progress", "done": "review"}.get(state)
        # A new review generation advances evidence at the same logical review
        # state. It is not a second transition and is permitted only while the
        # current native projection is still review.
        review_generation_advance = (
            state == "review"
            and observed.normalized_state == "review"
            and native_state == "review"
            and not exact
        )
        if (
            predecessor is not None
            and observed.normalized_state != predecessor
            and not review_generation_advance
            and not repair_exact
        ):
            raise TrackerConflictError("GitHub lifecycle predecessor unavailable")
        if state == "done" and observed.ac_done != observed.ac_total:
            raise TrackerConflictError("GitHub done proof lacks matching acceptance")
        if (
            isinstance(context, TransitionContext)
            and context.expected_state is not None
            and native_state != context.expected_state
            and not exact
        ):
            raise TrackerConflictError("GitHub lifecycle predecessor unavailable")
        if observed.normalized_state in rank and rank[observed.normalized_state] > rank[state]:
            if exact:
                return
            raise TrackerConflictError("GitHub weaker transition lacks exact receipt")
        if not exact:
            self._append_lifecycle(
                issue_id, binding, scope, operation, payload, source_native,
            )
        current = self._native_issue_read(issue_id)
        if self._current_lifecycle_scope(issue_id, binding) != scope:
            raise TrackerConflictError(
                "GitHub lifecycle native coordinates changed before state write",
            )
        if (
            self._state_source_snapshot(current)
            != self._state_source_snapshot(source_native)
        ):
            raise TrackerConflictError(
                "GitHub lifecycle source changed before state write",
            )
        if current.state == state:
            self._projection(
                issue_id, current, strict=True, binding=binding, scope=scope,
            )
            return
        # S1 before the single targeted ProjectV2 State update.  A state changed
        # by a foreign actor cannot be adopted as a new predecessor.
        if current.state != native_state:
            raise TrackerConflictError("GitHub native state changed before bounded write")
        before_unrelated = self._unrelated_state_snapshot(current)
        item_id, _ = self._item_coordinate(issue_id, binding)
        catalog = self._write_catalog(binding, {"state": state})
        try:
            self._set_project_field(item_id, binding.project_id, catalog["state"], state)
        except GitHubProjectsTrackerError as error:
            readback = self._native_issue_read(issue_id)
            if readback.state != state:
                raise error
        readback = self._native_issue_read(issue_id)
        if self._current_lifecycle_scope(issue_id, binding) != scope:
            raise TrackerConflictError(
                "GitHub lifecycle native coordinates changed after state write",
            )
        if self._unrelated_state_snapshot(readback) != before_unrelated:
            raise TrackerConflictError(
                "GitHub lifecycle changed untargeted properties after state write",
            )
        self._projection(
            issue_id, readback, strict=True, binding=binding, scope=scope,
        )

    def project_acceptance_proof(self, issue_id: str, expected_body: str, proof: dict, *, checked: int, project=None) -> bool:
        """Append the reviewed AC proof; native checkboxes remain non-authoritative."""
        if not isinstance(proof, dict) or type(checked) is not int or checked < 1:
            raise AcceptanceSyncUnavailableError("preuve AC GitHub invalide")
        binding = self._authoritative_binding(project or self._project())
        native = self._native_issue_read(issue_id)
        source_native = deepcopy(native)
        scope = self._current_lifecycle_scope(issue_id, binding)
        if native.body != expected_body:
            raise TrackerConflictError("corps GitHub modifié avant projection AC")
        rows = self._lifecycle_rows(issue_id, native, scope)
        projected = self._projection(
            issue_id, native, strict=False, binding=binding, scope=scope,
        )
        reviews = rows.get("state-review", [])
        if not reviews:
            raise TrackerConflictError("GitHub acceptance proof lacks review")
        review = reviews[-1][0]
        try:
            from foundry.routing import (
                AcceptanceProofStore,
                RoutingConfigError,
                acceptance_criteria,
                acceptance_digest,
            )
            proof = AcceptanceProofStore._validate_proof(proof)
            expected = acceptance_criteria(expected_body)
            issue = proof.get("issue")
            proof_review = proof.get("review")
            coordinates = proof.get("coordinates")
            valid = (
                proof.get("quality") == "mergeable" and isinstance(issue, dict)
                and issue.get("id") == issue_id and issue.get("ac_digest") == acceptance_digest(expected)
                and issue.get("criteria") == [{**item, "verdict": "pass"} for item in expected]
                and isinstance(coordinates, dict)
                and all(coordinates.get(name) == review.get(mapped) for name, mapped in
                        (("head", "head_sha"), ("base", "base_sha"), ("diff_hash", "review_digest")))
                and isinstance(proof_review, dict)
                and proof_review.get("role") == "reviewer"
                and checked == len(expected)
            )
        except (AttributeError, TypeError, ValueError, RoutingConfigError):
            valid = False
        if not valid:
            raise TrackerConflictError("GitHub acceptance proof malformed or stale")
        payload = {"review_generation": review["generation"], "body_digest": self._acceptance_body_digest(expected_body), "checked": checked, "proof": proof}
        _marker, body = self._lifecycle_marker(
            "acceptance", issue_id, payload, scope,
        )
        exact = [candidate for _p, candidate in rows.get("acceptance", []) if candidate == body]
        if len(exact) > 1:
            raise TrackerConflictError("GitHub lifecycle duplicate projection")
        if exact:
            return False
        if (
            projected.normalized_state != "review"
            or projected.projection_status != "aligned"
        ):
            raise TrackerConflictError(
                "GitHub acceptance proof lacks aligned review",
            )
        same_generation = [
            candidate for candidate, _body in rows.get("acceptance", [])
            if candidate.get("review_generation") == review["generation"]
        ]
        if same_generation:
            raise TrackerConflictError(
                "GitHub acceptance proof conflicts with this review generation",
            )
        self._append_lifecycle(
            issue_id, binding, scope, "acceptance", payload, source_native,
        )
        readback = self._native_issue_read(issue_id)
        if self._current_lifecycle_scope(issue_id, binding) != scope:
            raise TrackerConflictError(
                "GitHub lifecycle native coordinates changed after acceptance write",
            )
        if (
            self._comment_effect_snapshot(readback)
            != self._comment_effect_snapshot(source_native)
        ):
            raise TrackerConflictError(
                "GitHub lifecycle changed untargeted properties after acceptance write",
            )
        observed = self._projection(
            issue_id, readback, strict=True,
            binding=binding, scope=scope,
        )
        if observed.ac_done != observed.ac_total:
            raise TrackerConflictError("GitHub acceptance projection missing after write")
        return True
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
        reciprocal = {"subtask-of": "parent-of", "parent-of": "subtask-of",
                      "depends-on": "blocks", "blocks": "depends-on", "relates": "relates"}
        reverse_type = reciprocal[link_type]
        source_has = self._has_link(before_src, link_type, dst_id)
        destination_has = self._has_link(before_dst, reverse_type, src_id)
        if source_has and destination_has:
            return
        if source_has != destination_has:
            raise TrackerConflictError("lien GitHub asymétrique avant écriture ; aucune réparation automatique")
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
            path = None
        else:
            raise TrackerCapabilityUnavailableError(self.name, f"link:{link_type}")
        write_error: GitHubProjectsTrackerError | None = None
        try:
            if path is not None:
                self._rest_write("POST", path, payload, "issue.link_write")
            else:
                query = """mutation($issue:ID!,$related:ID!){addRelatesTo(input:{issueId:$issue,relatedIssueId:$related}){issue{id} relatedIssue{id}}}"""
                data = self._graphql_mutation(query, {"issue": src_node, "related": dst_node}, "issue.relates_write")
                result = data.get("addRelatesTo")
                if (not isinstance(result, dict) or not isinstance(result.get("issue"), dict)
                        or not isinstance(result.get("relatedIssue"), dict)
                        or result["issue"].get("id") != src_node
                        or result["relatedIssue"].get("id") != dst_node):
                    raise GitHubProjectsTrackerError("issue.relates_write", "ambiguous_mutation_response")
        except GitHubProjectsTrackerError as exc:
            write_error = exc
        read_src, read_dst = self.get_issue(src_id), self.get_issue(dst_id)
        # Native relation endpoints change the graph and may advance timestamps;
        # they must not change either endpoint's other observable properties.
        def unrelated(issue, is_source):
            snapshot = {k: v for k, v in issue.to_dict().items() if k not in {"links", "updated"}}
            affected_type = link_type if is_source else reverse_type
            affected_target = dst_id if is_source else src_id
            # Reparenting replaces the child's single parent; other relations,
            # including other children of either Epic, are not this operation.
            def affected(link):
                return link.type == affected_type and (
                    affected_type == "subtask-of" or link.target == affected_target
                )
            snapshot["links"] = sorted(
                (link.type, link.direction, link.target)
                for link in issue.links if not affected(link)
            )
            return snapshot
        if (self._has_link(read_src, link_type, dst_id)
                and self._has_link(read_dst, reverse_type, src_id)
                and unrelated(read_src, True) == unrelated(fresh_src, True)
                and unrelated(read_dst, False) == unrelated(fresh_dst, False)):
            return
        if (write_error is not None
                and read_src.to_dict() == fresh_src.to_dict()
                and read_dst.to_dict() == fresh_dst.to_dict()):
            raise write_error
        raise TrackerConflictError("lien GitHub divergent après écriture ; aucune seconde tentative") from write_error

    def add_comment(self, issue_id, text, project=None):
        binding = self._authoritative_binding(project or self._project())
        if not isinstance(text, str) or not text:
            raise GitHubProjectsTrackerError("issue.comment", "invalid_comment")
        number, _ = self._native_issue(issue_id, binding, "issue.comment_prewrite")
        # A canonical REST Issue is not sufficient authority: comments belong only
        # to one unique, complete, non-ADR delivery item in the active Project.
        self._item_coordinate(issue_id, binding)
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
    # ---- PAT-58 ADR issue/comment codec ---------------------------------
    # GitHub has no immutable document or conditional comment API.  The comments
    # below are a Foundry append-only *contract*: every read verifies the full
    # sequence, while writes retain the provider's explicit S1--S2 residual race.
    @staticmethod
    def _adr_title(adr_id: str, title: str) -> str:
        return f"[{adr_id}] {title}"

    @classmethod
    def _adr_head_title(
        cls,
        adr_id: str,
        title: str,
        metadata: dict[str, Any],
        body: str,
        comment_id: int,
    ) -> str:
        digest = cls._adr_version_digest(metadata, body)
        return (
            f"{cls._adr_title(adr_id, title)} "
            f"[{_ADR_HEAD}:{metadata['sequence']}:{comment_id}:{digest}]"
        )

    @staticmethod
    def _adr_native_title(raw_title: Any) -> tuple[str, str, tuple[int, int, str] | None]:
        if not isinstance(raw_title, str):
            raise GitHubProjectsTrackerError("adr.issue_read", "invalid_adr_title")
        marker = re.fullmatch(
            r"(\[[A-Za-z][A-Za-z0-9_]*-ADR-\d{4}\] .+) "
            rf"\[{re.escape(_ADR_HEAD)}:(\d+):(\d+):([0-9a-f]{{64}})\]",
            raw_title,
        )
        base_title = marker.group(1) if marker is not None else raw_title
        match = re.fullmatch(r"\[([A-Za-z][A-Za-z0-9_]*-ADR-\d{4})\] (.+)", base_title)
        if match is None or (marker is None and f"[{_ADR_HEAD}:" in raw_title):
            raise GitHubProjectsTrackerError("adr.issue_read", "invalid_adr_title")
        head = None if marker is None else (
            int(marker.group(2)), int(marker.group(3)), marker.group(4),
        )
        return match.group(1), match.group(2), head

    def _adr_issue(self, raw: Any, binding: _Binding) -> tuple[str, str, int]:
        number = self._issue_number_from_rest(raw, binding, "adr.issue_read")
        title, body, labels = raw.get("title"), raw.get("body"), raw.get("labels")
        if (not isinstance(body, str)
                or not isinstance(labels, list)
                or not all(isinstance(x, dict) and isinstance(x.get("name"), str) for x in labels)
                or _ADR_LABEL not in {x["name"] for x in labels}):
            raise GitHubProjectsTrackerError("adr.issue_read", "invalid_adr_support")
        adr_id, display_title, _head = self._adr_native_title(title)
        return adr_id, display_title, number

    @staticmethod
    def _adr_comment(metadata: dict[str, Any], body: str) -> str:
        return _ADR_HEADER + json.dumps(
            metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ) + "\n-->\n\n" + body

    @classmethod
    def _adr_version_digest(cls, metadata: dict[str, Any], body: str) -> str:
        """Digest the whole observable version, including its metadata."""
        return hashlib.sha256(cls._adr_comment(metadata, body).encode("utf-8")).hexdigest()

    def _parse_adr_comment(
        self, raw: dict[str, Any], binding: _Binding, adr_id: str,
    ) -> tuple[dict[str, Any], str, int]:
        native_id, content = raw.get("id"), raw.get("body")
        if type(native_id) is not int or native_id < 1 or not isinstance(content, str):
            raise GitHubProjectsTrackerError("adr.history", "invalid_comment")
        if not content.startswith(_ADR_HEADER) or "\n-->\n\n" not in content:
            raise GitHubProjectsTrackerError("adr.history", "foreign_or_malformed_comment")
        encoded, body = content[len(_ADR_HEADER):].split("\n-->\n\n", 1)
        try:
            metadata = json.loads(encoded)
        except ValueError as exc:
            raise GitHubProjectsTrackerError("adr.history", "invalid_metadata") from exc
        required = {"schema", "project_id", "repository", "id", "title", "status", "sequence", "body_sha256", "previous_comment_id", "previous_sha256", "relations"}
        if (not isinstance(metadata, dict) or set(metadata) != required
                or metadata.get("schema") != _ADR_SCHEMA
                or metadata.get("project_id") != binding.project_id
                or metadata.get("repository") != binding.repo
                or metadata.get("id") != adr_id
                or not isinstance(metadata.get("title"), str) or not metadata["title"]
                or metadata.get("status") not in _ADR_TRANSITIONS
                or type(metadata.get("sequence")) is not int or metadata["sequence"] < 0
                or not isinstance(metadata.get("body_sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", metadata["body_sha256"]) is None
                or not isinstance(metadata.get("relations"), dict)
                or set(metadata["relations"]) != {"issues", "supersedes", "superseded_by"}
                or not isinstance(metadata["relations"]["issues"], list)
                or not all(isinstance(x, str) and x for x in metadata["relations"]["issues"])
                or len(set(metadata["relations"]["issues"])) != len(metadata["relations"]["issues"])
                or not isinstance(metadata["relations"]["supersedes"], list)
                or not all(isinstance(x, str) and x for x in metadata["relations"]["supersedes"])
                or len(set(metadata["relations"]["supersedes"])) != len(metadata["relations"]["supersedes"])
                or metadata["relations"]["superseded_by"] is not None and not isinstance(metadata["relations"]["superseded_by"], str)):
            raise GitHubProjectsTrackerError("adr.history", "invalid_metadata")
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != metadata["body_sha256"]:
            raise TrackerConflictError("ADR GitHub source digest divergent")
        return metadata, body, native_id

    @staticmethod
    def _rest_label_names(raw: dict[str, Any], operation: str) -> set[str]:
        labels = raw.get("labels")
        if (
            not isinstance(labels, list)
            or not all(
                isinstance(label, dict)
                and isinstance(label.get("name"), str)
                and bool(label["name"])
                for label in labels
            )
            or len({label["name"] for label in labels}) != len(labels)
        ):
            raise GitHubProjectsTrackerError(operation, "invalid_labels")
        return {label["name"] for label in labels}

    def _repository_issue_inventory(
        self, binding: _Binding, operation: str,
    ) -> dict[int, dict[str, Any]]:
        """Read every visible native Issue/PR coordinate in the bound repository."""
        inventory: dict[int, dict[str, Any]] = {}
        for raw in self._rows(
            f"repos/{binding.repo}/issues?state=all", operation,
        ):
            number, native_id = raw.get("number"), raw.get("id")
            pull_request = raw.get("pull_request") is not None
            html_kind = "pull" if pull_request else "issues"
            if (
                type(number) is not int
                or number < 1
                or type(native_id) is not int
                or native_id < 1
                or raw.get("repository_url") != self._api_repo(binding)
                or raw.get("url") != f"{self._api_repo(binding)}/issues/{number}"
                or raw.get("html_url")
                != f"https://github.com/{binding.repo}/{html_kind}/{number}"
                or number in inventory
            ):
                raise GitHubProjectsTrackerError(
                    operation, "foreign_or_ambiguous_repository_issue",
                )
            inventory[number] = raw
        return inventory

    def _project_issue_inventory(
        self, binding: _Binding,
    ) -> dict[int, dict[str, Any]]:
        """Read all observable canonical Issue items from the bound Project."""
        cursor: str | None = None
        seen_items: set[str] = set()
        seen_cursors: set[str] = set()
        inventory: dict[int, dict[str, Any]] = {}
        for _ in range(_MAX_PAGES):
            page = self._project_page(binding, cursor)
            items = page.get("items")
            if not isinstance(items, dict) or not isinstance(items.get("nodes"), list):
                raise GitHubProjectsTrackerError(
                    "adr.list", "invalid_project_items",
                )
            for item in items["nodes"]:
                item_id = item.get("id") if isinstance(item, dict) else None
                if (
                    not isinstance(item_id, str)
                    or not item_id
                    or item_id in seen_items
                ):
                    raise GitHubProjectsTrackerError(
                        "adr.list", "invalid_project_items",
                    )
                seen_items.add(item_id)
                content = item.get("content")
                # GitHub can expose non-Issue items (and may expose no content
                # after deletion).  Without an Issue coordinate or marker they
                # cannot be attributed to the ADR corpus.
                if not isinstance(content, dict) or content.get("__typename") != "Issue":
                    continue
                repository = content.get("repository")
                if (
                    not isinstance(content.get("id"), str)
                    or not content["id"]
                    or not isinstance(repository, dict)
                    or not isinstance(repository.get("id"), str)
                    or not repository["id"]
                    or repository.get("nameWithOwner", "").casefold()
                    != binding.repo.casefold()
                    or repository.get("isPrivate") is not True
                    or not isinstance(repository.get("owner"), dict)
                    or repository["owner"].get("__typename") != "User"
                    or repository["owner"].get("login", "").casefold()
                    != binding.owner.casefold()
                ):
                    raise GitHubProjectsTrackerError(
                        "adr.list", "foreign_adr_support_coordinate",
                    )
                number = content.get("number")
                title, body = content.get("title"), content.get("body")
                labels = content.get("labels")
                nodes = labels.get("nodes") if isinstance(labels, dict) else None
                label_page = labels.get("pageInfo") if isinstance(labels, dict) else None
                if (
                    type(number) is not int
                    or number < 1
                    or not isinstance(title, str)
                    or not title
                    or body is not None
                    and not isinstance(body, str)
                    or not isinstance(nodes, list)
                    or not isinstance(label_page, dict)
                    or label_page.get("hasNextPage") is not False
                    or not all(
                        isinstance(label, dict)
                        and isinstance(label.get("name"), str)
                        and bool(label["name"])
                        for label in nodes
                    )
                    or len({label["name"] for label in nodes}) != len(nodes)
                    or number in inventory
                ):
                    raise GitHubProjectsTrackerError(
                        "adr.list", "invalid_adr_support",
                    )
                inventory[number] = content
            page_info = items.get("pageInfo")
            if (
                not isinstance(page_info, dict)
                or type(page_info.get("hasNextPage")) is not bool
            ):
                raise GitHubProjectsTrackerError(
                    "adr.list", "invalid_pagination",
                )
            if not page_info["hasNextPage"]:
                return inventory
            cursor = page_info.get("endCursor")
            if (
                not isinstance(cursor, str)
                or not cursor
                or cursor in seen_cursors
            ):
                raise GitHubProjectsTrackerError(
                    "adr.list", "pagination_stalled",
                )
            seen_cursors.add(cursor)
        raise GitHubProjectsTrackerError("adr.list", "pagination_limit")

    def _adr_surface_candidate(
        self,
        title: Any,
        labels: set[str],
        binding: _Binding,
    ) -> tuple[str, str] | None:
        """Recognize an in-scope support without trusting one mutable marker."""
        labelled = _ADR_LABEL in labels
        if not isinstance(title, str):
            if labelled:
                raise GitHubProjectsTrackerError(
                    "adr.list", "invalid_adr_title",
                )
            return None
        try:
            adr_id, display_title, head = self._adr_native_title(title)
        except GitHubProjectsTrackerError:
            # PAT-65's EXP probe deliberately shares the reserved label but is
            # outside the bound corpus namespace.
            if labelled and not title.startswith("EXP-ADR"):
                raise GitHubProjectsTrackerError(
                    "adr.list", "invalid_adr_title",
                )
            return None
        if not adr_id.startswith(f"{binding.key}-ADR-"):
            return None
        # A committed head is an independent native footprint.  It lets a
        # missing label be detected, while an unrelated similarly titled Issue
        # without the reserved marker or a valid head remains ordinary work.
        if not labelled and head is None:
            return None
        return adr_id, display_title

    def _adr_project_support(
        self,
        binding: _Binding,
        candidate: _CreateCandidate,
        adr_id: str,
        title: str,
    ) -> tuple[str, str, int] | None:
        """Observe only the create candidate's Project attachment step."""
        content = self._project_issue_inventory(binding).get(candidate.number)
        if content is None:
            return None
        labels = {
            label["name"] for label in content["labels"]["nodes"]
        }
        if (
            content.get("id") != candidate.content_id
            or _ADR_LABEL not in labels
        ):
            raise TrackerConflictError(
                "ADR GitHub support Project coordinate or label divergent",
            )
        observed_id, observed_title, _head = self._adr_native_title(
            content.get("title"),
        )
        if observed_id != adr_id or observed_title != title:
            raise TrackerConflictError("ADR GitHub support Project identity divergent")
        return observed_id, observed_title, candidate.number

    def _stable_adr_id(
        self,
        binding: _Binding,
        candidate_number: int,
        latest: dict[str, tuple[Adr, dict[str, Any], int]],
    ) -> str:
        """Allocate from the native monotonic Issue coordinate without reuse.

        GitHub never reuses an Issue number.  Keeping that number in the ADR ID
        makes simultaneous provisional candidates distinct without provider
        CAS and makes every deleted coordinate a harmless permanent gap.
        """
        inventory = self._repository_issue_inventory(
            binding, "adr.allocate",
        )
        raw_candidate = inventory.get(candidate_number)
        if raw_candidate is None or raw_candidate.get("pull_request") is not None:
            raise GitHubProjectsTrackerError(
                "adr.allocate", "create_candidate_not_visible",
            )
        support_numbers: set[int] = set()
        for adr_id, (adr, _metadata, _comment_id) in latest.items():
            match = re.fullmatch(
                rf"{re.escape(binding.key)}-ADR-(\d{{4}})", adr_id,
            )
            try:
                support_number = int(adr.ref or "")
            except ValueError as exc:
                raise TrackerConflictError(
                    "ADR GitHub allocation coordinate invalid",
                ) from exc
            if (
                match is None
                or support_number < 1
                or support_number in support_numbers
                or support_number not in inventory
                or int(match.group(1)) > support_number
            ):
                raise TrackerConflictError(
                    "ADR GitHub allocation coordinate invalid",
                )
            support_numbers.add(support_number)
        allocated = candidate_number
        if allocated < 1 or allocated > 9999:
            raise GitHubProjectsTrackerError(
                "adr.allocate", "adr_id_space_exhausted",
            )
        adr_id = f"{binding.key}-ADR-{allocated:04d}"
        if adr_id in latest:
            raise TrackerConflictError("ADR GitHub allocation reused a visible ID")
        return adr_id

    def _adr_supports(self, project: Project) -> list[tuple[str, str, int]]:
        binding = self._authoritative_binding(project)
        # Neither the mutable label nor Project membership is sufficient alone.
        # Exhaust both native surfaces, identify the corpus independently on
        # each, then require exact agreement before reading history.
        repository = self._repository_issue_inventory(binding, "adr.list")
        project_items = self._project_issue_inventory(binding)
        repository_candidates: dict[int, tuple[str, str]] = {}
        project_candidates: dict[int, tuple[str, str]] = {}
        for number, raw in repository.items():
            if raw.get("pull_request") is not None:
                continue
            candidate = self._adr_surface_candidate(
                raw.get("title"), self._rest_label_names(raw, "adr.list"), binding,
            )
            if candidate is not None:
                repository_candidates[number] = candidate
        for number, content in project_items.items():
            labels = {
                label["name"] for label in content["labels"]["nodes"]
            }
            candidate = self._adr_surface_candidate(
                content.get("title"), labels, binding,
            )
            if candidate is not None:
                project_candidates[number] = candidate

        supports: list[tuple[str, str, int]] = []
        for number in sorted(repository_candidates.keys() | project_candidates.keys()):
            repository_candidate = repository_candidates.get(number)
            project_candidate = project_candidates.get(number)
            if repository_candidate is None:
                # Preserve the provider's precise deleted/inaccessible outcome
                # when the Project still exposes the native support footprint.
                self._rest(
                    f"repos/{binding.repo}/issues/{number}", "adr.issue_read",
                )
                raise TrackerConflictError(
                    "ADR GitHub support missing from repository enumeration",
                )
            if project_candidate is None:
                raise TrackerConflictError(
                    "ADR GitHub support missing from bound Project",
                )
            if repository_candidate != project_candidate:
                raise TrackerConflictError(
                    "ADR GitHub support identity differs between repository and Project",
                )
            repository_raw = repository[number]
            project_raw = project_items[number]
            repository_labels = self._rest_label_names(repository_raw, "adr.list")
            project_labels = {
                label["name"] for label in project_raw["labels"]["nodes"]
            }
            if _ADR_LABEL not in repository_labels or _ADR_LABEL not in project_labels:
                raise TrackerConflictError(
                    "ADR GitHub reserved support label missing",
                )
            if (
                repository_raw.get("node_id") != project_raw.get("id")
                or repository_raw.get("title") != project_raw.get("title")
                or repository_raw.get("body") != project_raw.get("body")
            ):
                raise TrackerConflictError(
                    "ADR GitHub support differs between repository and Project",
                )
            raw = self._rest(
                f"repos/{binding.repo}/issues/{number}", "adr.issue_read",
            )
            if (
                self._issue_number_from_rest(raw, binding, "adr.issue_read")
                != number
                or raw.get("node_id") != project_raw.get("id")
                or raw.get("title") != repository_raw.get("title")
                or raw.get("body") != repository_raw.get("body")
                or self._rest_label_names(raw, "adr.issue_read")
                != repository_labels
            ):
                raise TrackerConflictError(
                    "ADR GitHub exact support read differs from indexed surfaces",
                )
            supports.append(self._adr_issue(raw, binding))
        return supports

    def _adr_snapshot(
        self,
        project: Project,
        *,
        _validate_graph: bool = True,
        _reconcile_head_for: str | None = None,
    ) -> tuple[_Binding, dict[str, tuple[Adr, dict[str, Any], int]]]:
        binding = self._authoritative_binding(project)
        latest: dict[str, tuple[Adr, dict[str, Any], int]] = {}
        for adr_id, title, number in self._adr_supports(project):
            if adr_id in latest:
                raise TrackerConflictError("ADR GitHub duplicate support")
            comments = self._rows(f"repos/{binding.repo}/issues/{number}/comments", "adr.history")
            parsed = [self._parse_adr_comment(row, binding, adr_id) for row in comments]
            if not parsed:
                raise TrackerConflictError("ADR GitHub history missing")
            parsed.sort(key=lambda row: row[0]["sequence"])
            for sequence, (metadata, _body, comment_id) in enumerate(parsed):
                if metadata["sequence"] != sequence:
                    raise TrackerConflictError("ADR GitHub history has a hole or duplicate")
                if sequence == 0:
                    if (metadata["previous_comment_id"] is not None
                            or metadata["previous_sha256"] is not None
                            or metadata["status"] != "proposed"):
                        raise TrackerConflictError("ADR GitHub initial history invalid")
                else:
                    previous, previous_body, previous_id = parsed[sequence - 1]
                    if (metadata["previous_comment_id"] != previous_id
                            or metadata["previous_sha256"]
                            != self._adr_version_digest(previous, previous_body)):
                        raise TrackerConflictError("ADR GitHub history predecessor divergent")
                    if metadata["title"] != previous["title"]:
                        raise TrackerConflictError("ADR GitHub title history divergent")
                    if metadata["status"] != previous["status"]:
                        legal = metadata["status"] in _ADR_TRANSITIONS[previous["status"]]
                        supersession = (
                            previous["status"] == "accepted"
                            and metadata["status"] == "superseded"
                            and isinstance(metadata["relations"]["superseded_by"], str)
                        )
                        if not legal and not supersession:
                            raise TrackerConflictError("ADR GitHub status history divergent")
                relations = metadata["relations"]
                if (
                    any(re.fullmatch(rf"{re.escape(binding.key)}-\d+", issue) is None for issue in relations["issues"])
                    or any(re.fullmatch(rf"{re.escape(binding.key)}-ADR-\d{{4}}", target) is None for target in relations["supersedes"])
                    or (relations["superseded_by"] is not None
                        and re.fullmatch(rf"{re.escape(binding.key)}-ADR-\d{{4}}", relations["superseded_by"]) is None)
                ):
                    raise TrackerConflictError("ADR GitHub relation coordinate invalid")
            raw = self._rest(f"repos/{binding.repo}/issues/{number}", "adr.issue_read")
            _id, current_title, _number = self._adr_issue(raw, binding)
            _native_id, _native_title, head = self._adr_native_title(raw.get("title"))
            if head is None:
                raise TrackerConflictError(
                    "ADR GitHub head commitment missing; legacy support requires migration"
                )
            head_indexes = [
                index for index, (candidate_metadata, candidate_body, candidate_id)
                in enumerate(parsed)
                if head == (
                    candidate_metadata["sequence"], candidate_id,
                    self._adr_version_digest(candidate_metadata, candidate_body),
                )
            ]
            if len(head_indexes) != 1:
                raise TrackerConflictError("ADR GitHub head commitment divergent")
            head_index = head_indexes[0]
            if head_index != len(parsed) - 1:
                if (
                    _reconcile_head_for != adr_id
                    or head_index != len(parsed) - 2
                ):
                    raise TrackerConflictError("ADR GitHub head commitment divergent")
            metadata, body, comment_id = parsed[head_index]
            observed_bodies = {body}
            if head_index != len(parsed) - 1:
                observed_bodies.add(parsed[-1][1])
            if current_title != metadata["title"] or raw.get("body") not in observed_bodies:
                raise TrackerConflictError("ADR GitHub current source divergent")
            latest[adr_id] = (Adr(adr_id, current_title, metadata["status"], body, str(number)), metadata, comment_id)
        # Validate graph only after the entire in-scope corpus is present.
        if _validate_graph:
            for adr_id, (_adr, metadata, _comment) in latest.items():
                relations = metadata["relations"]
                for target in relations["supersedes"]:
                    other = latest.get(target)
                    if other is None or other[1]["relations"]["superseded_by"] != adr_id:
                        raise TrackerConflictError("ADR GitHub supersession is not reciprocal")
                target = relations["superseded_by"]
                if target is not None and (
                    target not in latest
                    or adr_id not in latest[target][1]["relations"]["supersedes"]
                ):
                    raise TrackerConflictError("ADR GitHub supersession is not reciprocal")
        linked_by_issue: dict[str, list[str]] = {}
        for adr_id, (_adr, metadata, _comment) in latest.items():
            for issue_id in metadata["relations"]["issues"]:
                linked_by_issue.setdefault(issue_id, []).append(adr_id)
        if linked_by_issue:
            project_issue_ids = {issue.id for issue in self._search_raw(project)}
            for issue_id, adr_ids in sorted(linked_by_issue.items()):
                adr_id = sorted(adr_ids)[0]
                if issue_id not in project_issue_ids:
                    raise AdrIssueUnavailableError(adr_id, issue_id)
                try:
                    number, _native_id = self._native_issue(
                        issue_id, binding, "adr.issue_relation_read",
                    )
                except GitHubProjectsTrackerError as exc:
                    if exc.reason in {"not_found", "permission_denied"}:
                        raise AdrIssueUnavailableError(adr_id, issue_id) from None
                    raise
                if issue_id != f"{binding.key}-{number}":
                    raise TrackerConflictError("ADR GitHub issue relation coordinate divergent")
        return binding, latest

    def list_adrs(self, project: Project) -> list[Adr]:
        _binding, latest = self._adr_snapshot(project)
        return [latest[key][0] for key in sorted(latest)]

    def adr_for_mutation(self, project: Project, adr_id: str) -> Adr | None:
        _binding, latest = self._adr_snapshot(
            project, _reconcile_head_for=adr_id,
        )
        entry = latest.get(adr_id)
        return entry[0] if entry is not None else None

    @staticmethod
    def _same_adr_snapshot(expected: Adr, observed: Adr) -> bool:
        return (
            expected.id == observed.id
            and expected.title == observed.title
            and expected.status == observed.status
            and expected.body == observed.body
            and expected.ref == observed.ref
        )

    @staticmethod
    def _adr_issue_unaffected(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value for key, value in raw.items()
            if key not in {"body", "updated_at"}
        }

    def _commit_adr_head(
        self,
        binding: _Binding,
        number: int,
        adr_id: str,
        title: str,
        body: str,
        metadata: dict[str, Any],
        comment_id: int,
        expected_native_title: str,
    ) -> None:
        desired_title = self._adr_head_title(
            adr_id, title, metadata, body, comment_id,
        )
        raw = self._rest(f"repos/{binding.repo}/issues/{number}", "adr.head_prewrite")
        native_adr_id, native_title, native_number = self._adr_issue(raw, binding)
        if (
            native_adr_id != adr_id
            or native_title != title
            or native_number != number
            or raw.get("body") != body
        ):
            raise TrackerConflictError("ADR GitHub support changed before head commitment")
        if raw.get("title") == desired_title:
            return
        if raw.get("title") != expected_native_title:
            raise TrackerConflictError("ADR GitHub head changed before bounded write")
        unaffected = {
            key: value for key, value in raw.items()
            if key not in {"title", "updated_at"}
        }
        write_error: GitHubProjectsTrackerError | None = None
        try:
            self._rest_write(
                "PATCH", f"repos/{binding.repo}/issues/{number}",
                {"title": desired_title}, "adr.head_write",
            )
        except GitHubProjectsTrackerError as exc:
            write_error = exc
        observed = self._rest(
            f"repos/{binding.repo}/issues/{number}", "adr.head_readback",
        )
        if observed.get("title") != desired_title:
            if write_error is not None and observed.get("title") == expected_native_title:
                raise write_error
            raise TrackerConflictError(
                "ADR GitHub head commitment divergent after write"
            ) from write_error
        observed_unaffected = {
            key: value for key, value in observed.items()
            if key not in {"title", "updated_at"}
        }
        if observed_unaffected != unaffected:
            raise TrackerConflictError(
                "ADR GitHub untargeted properties changed after head commitment"
            )

    def _append_adr_version(self, project: Project, adr: Adr, *, status: str | None = None,
                            body: str | None = None, relations: dict[str, Any] | None = None,
                            _allow_incomplete_graph: bool = False,
                            _precomment_check: Callable[[], None] | None = None) -> Adr:
        binding, latest = self._adr_snapshot(
            project,
            _validate_graph=not _allow_incomplete_graph,
            _reconcile_head_for=adr.id,
        )
        entry = latest.get(adr.id)
        if entry is None or not self._same_adr_snapshot(adr, entry[0]):
            raise TrackerConflictError("ADR GitHub snapshot is stale")
        previous, metadata, previous_comment = entry
        source = previous.body if body is None else body
        if not isinstance(source, str):
            raise GitHubProjectsTrackerError("adr.write", "invalid_source")
        next_metadata = {
            **metadata, "status": metadata["status"] if status is None else status,
            "sequence": metadata["sequence"] + 1,
            "body_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "previous_comment_id": previous_comment,
            "previous_sha256": self._adr_version_digest(metadata, previous.body),
            "relations": deepcopy(metadata["relations"] if relations is None else relations),
        }
        number = int(previous.ref or 0)
        previous_native_title = self._adr_head_title(
            adr.id, previous.title, metadata, previous.body, previous_comment,
        )
        # Prove the native support coordinate and all caller-visible predecessor
        # fields before the first effect.  The Issue number alone is not authority.
        raw = self._rest(f"repos/{binding.repo}/issues/{number}", "adr.prewrite")
        native_adr_id, title, native_number = self._adr_issue(raw, binding)
        node_id = raw.get("node_id")
        if (
            native_adr_id != adr.id
            or native_number != number
            or title != previous.title
            or raw.get("title") != previous_native_title
            or raw.get("body") != previous.body
            or not isinstance(node_id, str)
            or not node_id
        ):
            raise TrackerConflictError("ADR GitHub changed before bounded write")
        candidate = _CreateCandidate(adr.id, number, raw["id"], node_id)
        version_body = self._adr_comment(next_metadata, source)
        fingerprint = self._create_fingerprint(
            binding, f"adr-version:{adr.id}", version_body,
            {"kind": "adr-version"}, None,
        )
        with self._create_intent_lock(fingerprint):
            record = self._read_create_intent(fingerprint)
            if record is None:
                record = self._intent_record(
                    fingerprint, "known", candidate, step="adr:version",
                    adr_id=adr.id,
                )
                self._write_create_intent(fingerprint, record)
            elif (
                record.get("adr_id") != adr.id
                or record["issue_id"] != candidate.issue_id
                or record["native_id"] != candidate.native_id
                or record["content_id"] != candidate.content_id
            ):
                raise GitHubProjectsTrackerError(
                    "adr.version_reconcile", "invalid_local_intent",
                )

            comments = self._rows(
                f"repos/{binding.repo}/issues/{number}/comments",
                "adr.version_reconcile",
            )
            exact = [row for row in comments if row.get("body") == version_body]
            if len(exact) > 1:
                raise TrackerConflictError("ADR GitHub version candidate is ambiguous")
            if len(comments) > metadata["sequence"] + 1 and not exact:
                raise TrackerConflictError(
                    "ADR GitHub uncommitted head does not match requested version"
                )
            if exact:
                self._commit_adr_head(
                    binding, number, adr.id, previous.title, source,
                    next_metadata, exact[0]["id"], previous_native_title,
                )
                if record["state"] != "complete":
                    self._write_create_intent(
                        fingerprint,
                        self._intent_record(
                            fingerprint, "complete", candidate, adr_id=adr.id,
                        ),
                    )
                _binding, reread = self._adr_snapshot(
                    project, _validate_graph=not _allow_incomplete_graph,
                )
                result = reread.get(adr.id)
                if (
                    result is None
                    or result[0] != Adr(
                        adr.id, previous.title, next_metadata["status"],
                        source, previous.ref,
                    )
                    or result[1] != next_metadata
                    or result[2] != exact[0].get("id")
                ):
                    raise TrackerConflictError("ADR GitHub version readback divergent")
                return result[0]
            elif record["state"] == "complete" or record["step"] == "adr:version" and record["attempt"] == 1:
                raise GitHubProjectsTrackerError(
                    "adr.version_reconcile", "version_effect_unknown",
                )

            if not exact:
                # attempt=1 means the one non-idempotent request may now have an
                # unknown effect.  It is durable before the POST and is never reset
                # merely because the response or immediate observation was empty.
                armed = {**record, "state": "known", "step": "adr:version", "attempt": 1}
                self._write_create_intent(fingerprint, armed)
        if body is not None:
            write_error: GitHubProjectsTrackerError | None = None
            try:
                self._rest_write("PATCH", f"repos/{binding.repo}/issues/{number}", {"body": source}, "adr.source_write")
            except GitHubProjectsTrackerError as exc:
                write_error = exc
            observed = self._rest(f"repos/{binding.repo}/issues/{number}", "adr.readback")
            if observed.get("body") != source:
                if write_error is not None and observed.get("body") == previous.body:
                    raise write_error
                raise TrackerConflictError("ADR GitHub source divergent after write") from write_error
            if self._adr_issue_unaffected(observed) != self._adr_issue_unaffected(raw):
                raise TrackerConflictError("ADR GitHub untargeted properties changed after source write")

        # Refresh the exact predecessor immediately before COMMENT.  This catches
        # both another version and same-source metadata tampering.
        before_comment = self._rows(
            f"repos/{binding.repo}/issues/{number}/comments", "adr.version_prewrite",
        )
        parsed = [self._parse_adr_comment(row, binding, adr.id) for row in before_comment]
        parsed.sort(key=lambda row: row[0]["sequence"])
        if (
            len(parsed) != metadata["sequence"] + 1
            or parsed[-1][0] != metadata
            or parsed[-1][1] != previous.body
            or parsed[-1][2] != previous_comment
        ):
            raise TrackerConflictError("ADR GitHub predecessor changed before version write")
        current_raw = self._rest(f"repos/{binding.repo}/issues/{number}", "adr.version_prewrite")
        current_id, current_title, current_number = self._adr_issue(current_raw, binding)
        if (
            current_id != adr.id
            or current_title != previous.title
            or current_number != number
            or current_raw.get("title") != previous_native_title
            or current_raw.get("id") != candidate.native_id
            or current_raw.get("node_id") != candidate.content_id
            or current_raw.get("body") != source
        ):
            raise TrackerConflictError("ADR GitHub support changed before version write")
        if _precomment_check is not None:
            _precomment_check()

        raw_comment: dict[str, Any] | None = None
        try:
            candidate_response = self._rest_write(
                "POST", f"repos/{binding.repo}/issues/{number}/comments",
                {"body": version_body}, "adr.version_write",
            )
            if (
                not isinstance(candidate_response, dict)
                or type(candidate_response.get("id")) is not int
                or candidate_response.get("body") != version_body
            ):
                raise GitHubProjectsTrackerError(
                    "adr.version_write", "ambiguous_mutation_response",
                )
            raw_comment = candidate_response
        except GitHubProjectsTrackerError as exc:
            observed_comments = self._rows(
                f"repos/{binding.repo}/issues/{number}/comments",
                "adr.version_reconcile",
            )
            exact = [row for row in observed_comments if row.get("body") == version_body]
            if len(exact) != 1:
                raise GitHubProjectsTrackerError(
                    "adr.version_reconcile", "version_effect_unknown",
                ) from exc
            raw_comment = exact[0]
        observed_comments = self._rows(
            f"repos/{binding.repo}/issues/{number}/comments", "adr.version_readback",
        )
        exact = [row for row in observed_comments if row.get("body") == version_body]
        if len(exact) != 1 or exact[0].get("id") != raw_comment.get("id"):
            raise TrackerConflictError("ADR GitHub version readback divergent")
        self._commit_adr_head(
            binding, number, adr.id, previous.title, source,
            next_metadata, raw_comment["id"], previous_native_title,
        )
        with self._create_intent_lock(fingerprint):
            self._write_create_intent(
                fingerprint,
                self._intent_record(
                    fingerprint, "complete", candidate, adr_id=adr.id,
                ),
            )
        _binding, reread = self._adr_snapshot(
            project, _validate_graph=not _allow_incomplete_graph,
        )
        result = reread.get(adr.id)
        if (
            result is None
            or result[0] != Adr(adr.id, previous.title, next_metadata["status"], source, previous.ref)
            or result[1] != next_metadata
            or result[2] != raw_comment["id"]
        ):
            raise TrackerConflictError("ADR GitHub version readback divergent")
        return result[0]

    def create_adr(self, project, title, body, status="proposed"):
        if status != "proposed" or not isinstance(title, str) or not title or not isinstance(body, str):
            raise GitHubProjectsTrackerError("adr.create", "invalid_adr_payload")
        binding = self._authoritative_binding(project)
        if not self.verify_project_identity(project):
            raise GitHubProjectsTrackerError("adr.create", "unqualified_repository_project")
        fingerprint = self._create_fingerprint(
            binding, title, body, {"kind": "adr", "status": "proposed"}, None,
        )
        # Allocation is serialized only among Foundry processes on this machine.
        # The exact qualified provider snapshot is reloaded while holding that
        # lock; this deliberately makes no distributed exclusion claim.
        with self._adr_corpus_lock(binding), self._create_intent_lock(fingerprint):
            record = self._read_create_intent(fingerprint)
            latest: dict[str, tuple[Adr, dict[str, Any], int]] | None = None
            if record is None:
                _binding, latest = self._adr_snapshot(project)
                numbers = [
                    int(key.rsplit("-", 1)[1]) for key in latest
                    if key.startswith(f"{binding.key}-ADR-")
                ]
                native_inventory = self._repository_issue_inventory(
                    binding, "adr.allocate",
                )
                next_visible = max(
                    max(numbers, default=0) + 1,
                    max(native_inventory, default=0) + 1,
                )
                if next_visible > 9999:
                    raise GitHubProjectsTrackerError(
                        "adr.allocate", "adr_id_space_exhausted",
                    )
                adr_id = f"{binding.key}-ADR-{next_visible:04d}"
                record = self._intent_record(fingerprint, "pending", adr_id=adr_id)
                self._write_create_intent(fingerprint, record)
            else:
                adr_id = record.get("adr_id")
                if adr_id is None:
                    raise GitHubProjectsTrackerError(
                        "adr.create_reconcile", "invalid_local_intent",
                    )

            def initial_metadata(identifier: str) -> dict[str, Any]:
                return {
                    "schema": _ADR_SCHEMA, "project_id": binding.project_id,
                    "repository": binding.repo, "id": identifier, "title": title,
                    "status": "proposed", "sequence": 0,
                    "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    "previous_comment_id": None, "previous_sha256": None,
                    "relations": {
                        "issues": [], "supersedes": [], "superseded_by": None,
                    },
                }

            metadata = initial_metadata(adr_id)
            native_title = self._adr_title(adr_id, title)
            provisional_title = f"[foundry-adr-create:v1:{fingerprint}]"

            if record["state"] == "complete":
                _binding, latest = self._adr_snapshot(project)
                entry = latest.get(adr_id)
                candidate = self._candidate_from_record(record, binding)
                raw = self._rest(
                    f"repos/{binding.repo}/issues/{candidate.number}",
                    "adr.create_reconcile",
                )
                native_id, native_name, native_number = self._adr_issue(raw, binding)
                comments = self._rows(
                    f"repos/{binding.repo}/issues/{candidate.number}/comments",
                    "adr.create_reconcile",
                )
                initial = [
                    self._parse_adr_comment(row, binding, adr_id)
                    for row in comments
                    if isinstance(row.get("body"), str)
                    and row["body"].startswith(_ADR_HEADER)
                ]
                initial = [row for row in initial if row[0]["sequence"] == 0]
                if (
                    entry is None
                    or native_id != adr_id
                    or native_name != title
                    or native_number != candidate.number
                    or raw.get("id") != candidate.native_id
                    or raw.get("node_id") != candidate.content_id
                    or len(initial) != 1
                    or initial[0][0] != metadata
                    or initial[0][1] != body
                ):
                    raise TrackerConflictError("ADR GitHub completed create drift")
                return entry[0]

            def create_candidates() -> list[tuple[dict[str, Any], bool]]:
                same_coordinate: list[dict[str, Any]] = []
                exact: list[tuple[dict[str, Any], bool]] = []
                for observed in self._rows(
                    f"repos/{binding.repo}/issues?state=all", "adr.create_reconcile",
                ):
                    observed_title = observed.get("title")
                    is_provisional = observed_title == provisional_title
                    is_legacy = (
                        isinstance(observed_title, str)
                        and observed_title.startswith(f"[{adr_id}] ")
                    )
                    if is_provisional or is_legacy:
                        self._issue_number_from_rest(
                            observed, binding, "adr.create_reconcile",
                        )
                        same_coordinate.append(observed)
                        if (
                            observed.get("body") == body
                            and (is_provisional or observed_title == native_title)
                        ):
                            exact.append((observed, is_provisional))
                if len(same_coordinate) != len(exact):
                    raise TrackerConflictError("ADR GitHub reserved coordinate is occupied")
                if len(exact) > 1:
                    raise TrackerConflictError("ADR GitHub create candidate is ambiguous")
                return exact

            raw: dict[str, Any]
            if record["state"] == "pending":
                candidates = create_candidates()
                if record["attempt"] == 0:
                    if candidates:
                        raise TrackerConflictError("ADR GitHub create candidate is unowned")
                    armed = {**record, "attempt": 1}
                    self._write_create_intent(fingerprint, armed)
                    record = armed
                    try:
                        created = self._rest_write(
                            "POST", f"repos/{binding.repo}/issues",
                            {"title": provisional_title, "body": body}, "adr.create",
                        )
                        if not isinstance(created, dict):
                            raise GitHubProjectsTrackerError(
                                "adr.create", "ambiguous_mutation_response",
                            )
                        raw = created
                        provisional_candidate = True
                    except GitHubProjectsTrackerError as exc:
                        candidates = create_candidates()
                        if len(candidates) != 1:
                            raise GitHubProjectsTrackerError(
                                "adr.create_reconcile", "create_effect_unknown",
                            ) from exc
                        raw, provisional_candidate = candidates[0]
                else:
                    if len(candidates) != 1:
                        raise GitHubProjectsTrackerError(
                            "adr.create_reconcile", "create_effect_unknown",
                        )
                    raw, provisional_candidate = candidates[0]
                number = self._issue_number_from_rest(raw, binding, "adr.create")
                content_id = raw.get("node_id")
                if not isinstance(content_id, str) or not content_id:
                    raise GitHubProjectsTrackerError(
                        "adr.create", "missing_issue_node_id",
                    )
                candidate = _CreateCandidate(
                    f"{binding.key}-{number}", number, raw["id"], content_id,
                )
                if provisional_candidate:
                    _binding, latest = self._adr_snapshot(project)
                    adr_id = self._stable_adr_id(
                        binding, candidate.number, latest,
                    )
                    metadata = initial_metadata(adr_id)
                    native_title = self._adr_title(adr_id, title)
                record = self._intent_record(
                    fingerprint, "known", candidate, adr_id=adr_id,
                )
                self._write_create_intent(fingerprint, record)
            else:
                candidate = self._candidate_from_record(record, binding)
                raw = self._rest(
                    f"repos/{binding.repo}/issues/{candidate.number}",
                    "adr.create_reconcile",
                )
                observed_number = self._issue_number_from_rest(
                    raw, binding, "adr.create_reconcile",
                )
                if (
                    observed_number != candidate.number
                    or raw.get("id") != candidate.native_id
                    or raw.get("node_id") != candidate.content_id
                    or raw.get("body") != body
                ):
                    raise TrackerConflictError("ADR GitHub known create candidate drift")
                if raw.get("title") != provisional_title:
                    native_adr_id, native_name, _head = self._adr_native_title(
                        raw.get("title")
                    )
                    if native_adr_id != adr_id or native_name != title:
                        raise TrackerConflictError(
                            "ADR GitHub known create candidate drift",
                        )

            def complete_step(step: str) -> None:
                nonlocal record
                record = self._intent_record(
                    fingerprint, "known", candidate, adr_id=adr_id,
                )
                self._write_create_intent(fingerprint, record)

            def arm_step(step: str) -> None:
                nonlocal record
                if record["step"] is not None:
                    raise GitHubProjectsTrackerError(
                        "adr.create_reconcile", f"{record['step']}_effect_unknown",
                    )
                record = self._intent_record(
                    fingerprint, "known", candidate, step=step, attempt=1,
                    adr_id=adr_id,
                )
                self._write_create_intent(fingerprint, record)

            if raw.get("title") == native_title:
                if record["step"] == "adr:identity":
                    complete_step("adr:identity")
            elif raw.get("title") == provisional_title:
                arm_step("adr:identity")
                unaffected = {
                    key: value for key, value in raw.items()
                    if key not in {"title", "updated_at"}
                }
                write_error: GitHubProjectsTrackerError | None = None
                try:
                    self._rest_write(
                        "PATCH", f"repos/{binding.repo}/issues/{candidate.number}",
                        {"title": native_title}, "adr.identity_write",
                    )
                except GitHubProjectsTrackerError as exc:
                    write_error = exc
                raw = self._rest(
                    f"repos/{binding.repo}/issues/{candidate.number}",
                    "adr.identity_readback",
                )
                observed_unaffected = {
                    key: value for key, value in raw.items()
                    if key not in {"title", "updated_at"}
                }
                if raw.get("title") != native_title:
                    if write_error is not None and raw.get("title") == provisional_title:
                        raise GitHubProjectsTrackerError(
                            "adr.create_reconcile", "adr:identity_effect_unknown",
                        ) from write_error
                    raise TrackerConflictError(
                        "ADR GitHub identity divergent after bounded write",
                    ) from write_error
                if observed_unaffected != unaffected:
                    raise TrackerConflictError(
                        "ADR GitHub untargeted properties changed after identity write",
                    )
                complete_step("adr:identity")
            else:
                raise TrackerConflictError("ADR GitHub initial identity drift")

            labels = raw.get("labels")
            labelled = (
                isinstance(labels, list)
                and _ADR_LABEL in {
                    row.get("name") for row in labels if isinstance(row, dict)
                }
            )
            if labelled and record["step"] == "adr:label":
                complete_step("adr:label")
            elif not labelled:
                arm_step("adr:label")
                write_error: GitHubProjectsTrackerError | None = None
                try:
                    self._rest_labels(binding, candidate.number)
                except GitHubProjectsTrackerError as exc:
                    write_error = exc
                raw = self._rest(
                    f"repos/{binding.repo}/issues/{candidate.number}",
                    "adr.label_readback",
                )
                labels = raw.get("labels")
                if not isinstance(labels, list) or _ADR_LABEL not in {
                    row.get("name") for row in labels if isinstance(row, dict)
                }:
                    raise GitHubProjectsTrackerError(
                        "adr.create_reconcile", "adr:label_effect_unknown",
                    ) from write_error
                complete_step("adr:label")

            observed_support = self._adr_project_support(
                binding, candidate, adr_id, title,
            )
            attached = observed_support == (adr_id, title, candidate.number)
            if attached and record["step"] == "adr:item":
                complete_step("adr:item")
            elif not attached:
                arm_step("adr:item")
                write_error = None
                try:
                    self._add_project_item(binding, candidate.content_id)
                except GitHubProjectsTrackerError as exc:
                    write_error = exc
                observed_support = self._adr_project_support(
                    binding, candidate, adr_id, title,
                )
                if observed_support != (adr_id, title, candidate.number):
                    raise GitHubProjectsTrackerError(
                        "adr.create_reconcile", "adr:item_effect_unknown",
                    ) from write_error
                complete_step("adr:item")

            version_body = self._adr_comment(metadata, body)
            comments = self._rows(
                f"repos/{binding.repo}/issues/{candidate.number}/comments",
                "adr.create_reconcile",
            )
            exact = [row for row in comments if row.get("body") == version_body]
            if len(exact) > 1:
                raise TrackerConflictError("ADR GitHub initial version is ambiguous")
            if exact and record["step"] == "adr:version":
                complete_step("adr:version")
            elif not exact:
                arm_step("adr:version")
                write_error = None
                try:
                    response = self._rest_write(
                        "POST",
                        f"repos/{binding.repo}/issues/{candidate.number}/comments",
                        {"body": version_body}, "adr.version_write",
                    )
                    if (
                        not isinstance(response, dict)
                        or type(response.get("id")) is not int
                        or response.get("body") != version_body
                    ):
                        raise GitHubProjectsTrackerError(
                            "adr.version_write", "ambiguous_mutation_response",
                        )
                except GitHubProjectsTrackerError as exc:
                    write_error = exc
                comments = self._rows(
                    f"repos/{binding.repo}/issues/{candidate.number}/comments",
                    "adr.version_readback",
                )
                exact = [row for row in comments if row.get("body") == version_body]
                if len(exact) != 1:
                    raise GitHubProjectsTrackerError(
                        "adr.create_reconcile", "adr:version_effect_unknown",
                    ) from write_error
                complete_step("adr:version")

            initial_comment_id = exact[0].get("id")
            if type(initial_comment_id) is not int or initial_comment_id < 1:
                raise GitHubProjectsTrackerError(
                    "adr.create_reconcile", "invalid_initial_version_coordinate",
                )
            raw = self._rest(
                f"repos/{binding.repo}/issues/{candidate.number}",
                "adr.head_prewrite",
            )
            desired_head_title = self._adr_head_title(
                adr_id, title, metadata, body, initial_comment_id,
            )
            if raw.get("title") == desired_head_title and record["step"] == "adr:head":
                complete_step("adr:head")
            elif raw.get("title") != desired_head_title:
                if raw.get("title") != native_title:
                    raise TrackerConflictError("ADR GitHub initial head commitment drift")
                arm_step("adr:head")
                self._commit_adr_head(
                    binding, candidate.number, adr_id, title, body, metadata,
                    initial_comment_id, native_title,
                )
                complete_step("adr:head")

            _binding, reread = self._adr_snapshot(project)
            result = reread.get(adr_id)
            if (
                result is None
                or result[0] != Adr(adr_id, title, "proposed", body, str(candidate.number))
                or result[1] != metadata
                or result[2] != exact[0].get("id")
            ):
                raise TrackerConflictError("ADR GitHub create readback divergent")
            complete = self._intent_record(
                fingerprint, "complete", candidate, adr_id=adr_id,
            )
            self._write_create_intent(fingerprint, complete)
            return result[0]

    def update_body(self, resource, expected_body, updated_body, project=None):
        if isinstance(resource, Adr):
            if not isinstance(expected_body, str) or not isinstance(updated_body, str):
                raise GitHubProjectsTrackerError("adr.write", "invalid_source")
            current = self.adr_for_mutation(project or self._project(), resource.id)
            if current is None or current.ref != resource.ref:
                raise TrackerConflictError("ADR GitHub snapshot is stale")
            same_fixed_snapshot = (
                current.id == resource.id
                and current.title == resource.title
                and current.status == resource.status
                and current.ref == resource.ref
            )
            if (
                same_fixed_snapshot
                and resource.body == expected_body
                and current.body == updated_body
            ):
                return False
            if (
                not same_fixed_snapshot
                or resource.body != expected_body
                or current.body != expected_body
            ):
                raise TrackerConflictError("ADR GitHub source changed before bounded write")
            self._append_adr_version(project or self._project(), current, body=updated_body)
            return True
        return self._update_issue_body(resource, expected_body, updated_body, project=project)

    def set_adr_status(self, adr, status, project=None):
        project = project or self._project()
        current = self.adr_for_mutation(project, adr.id)
        if current is None or current.ref != adr.ref:
            raise TrackerConflictError("ADR GitHub snapshot is stale")
        if status == current.status:
            if (
                current.title == adr.title
                and current.body == adr.body
                and current.ref == adr.ref
                and (
                    adr.status == status
                    or status in _ADR_TRANSITIONS.get(adr.status, set())
                )
            ):
                return
            raise TrackerConflictError("ADR GitHub snapshot is stale")
        if not self._same_adr_snapshot(adr, current):
            raise TrackerConflictError("ADR GitHub snapshot is stale")
        if status == "superseded" or status not in _ADR_TRANSITIONS.get(current.status, set()):
            raise TrackerConflictError("ADR GitHub status transition refused")
        self._append_adr_version(project, current, status=status)

    def link_adr_issue(self, adr, issue_ref, project=None):
        project = project or self._project()
        binding, latest = self._adr_snapshot(project)
        entry = latest.get(adr.id)
        if (
            entry is None
            or not self._same_adr_snapshot(adr, entry[0])
            or entry[0].status not in {"proposed", "accepted"}
        ):
            raise TrackerConflictError("ADR GitHub issue link binding invalid")
        number, _native = self._native_issue(issue_ref, binding, "adr.issue_link")
        # The relation target must be a delivery item, never another ADR support.
        if not any(issue.id == f"{binding.key}-{number}" for issue in self._search_raw(project)):
            raise IssueUnavailableError(issue_ref)
        relations = deepcopy(entry[1]["relations"])
        canonical_issue_ref = f"{binding.key}-{number}"
        if canonical_issue_ref in relations["issues"]:
            return entry[0]
        relations["issues"] = sorted([*relations["issues"], canonical_issue_ref])

        def verify_target() -> None:
            fresh_number, _native = self._native_issue(
                canonical_issue_ref, binding, "adr.issue_link_prewrite",
            )
            if fresh_number != number or not any(
                issue.id == canonical_issue_ref for issue in self._search_raw(project)
            ):
                raise TrackerConflictError(
                    "ADR GitHub issue link target changed before version write",
                )

        return self._append_adr_version(
            project, entry[0], relations=relations,
            _precomment_check=verify_target,
        )

    def supersede_adr(self, adr, replacement_id, project=None):
        project = project or self._project()
        snapshot = self._adr_snapshot(project)
        _binding, latest = snapshot
        old, replacement = latest.get(adr.id), latest.get(replacement_id)
        if (old is None or replacement is None or adr.id == replacement_id
                or not self._same_adr_snapshot(adr, old[0])
                or old[0].status != "accepted" or replacement[0].status != "accepted"
                or old[1]["relations"]["superseded_by"] is not None
                or adr.id in replacement[1]["relations"]["supersedes"]):
            raise TrackerConflictError("ADR GitHub supersession binding invalid")
        # Two provider writes cannot be atomic.  Do not make a partial pair look
        # complete: the next read sees the non-reciprocal graph and refuses.
        replacement_relations = deepcopy(replacement[1]["relations"])
        replacement_relations["supersedes"] = sorted([*replacement_relations["supersedes"], adr.id])
        self._append_adr_version(
            project, replacement[0], relations=replacement_relations,
            _allow_incomplete_graph=True,
        )
        # The second comment uses a newly observed predecessor, never the snapshot
        # that preceded the first provider write.
        _binding, intermediate = self._adr_snapshot(project, _validate_graph=False)
        fresh_old, fresh_replacement = intermediate.get(adr.id), intermediate.get(replacement_id)
        if (
            fresh_old is None
            or fresh_replacement is None
            or not self._same_adr_snapshot(old[0], fresh_old[0])
            or fresh_replacement[1]["relations"] != replacement_relations
            or fresh_replacement[0].status != replacement[0].status
            or fresh_replacement[0].body != replacement[0].body
        ):
            raise TrackerConflictError("ADR GitHub supersession predecessor drift")
        old_relations = deepcopy(old[1]["relations"])
        old_relations["superseded_by"] = replacement_id
        self._append_adr_version(
            project, fresh_old[0], status="superseded", relations=old_relations,
            _allow_incomplete_graph=True,
        )
        # This is deliberately the public validator: interrupted pairs remain
        # visible as non-reciprocal and fail closed; only a complete pair returns.
        _binding, reread = self._adr_snapshot(project)
        if (reread.get(adr.id) is None or reread.get(replacement_id) is None
                or reread[adr.id][0].status != "superseded"
                or reread[adr.id][1]["relations"]["superseded_by"] != replacement_id
                or adr.id not in reread[replacement_id][1]["relations"]["supersedes"]):
            raise TrackerConflictError("ADR GitHub supersession readback divergent")
