"""DevHubTracker v1 adapter for Foundry's normalized Tracker port."""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from foundry import config, registry
from foundry.models import (
    Adr,
    EpicClosureChild,
    EpicClosureOutcome,
    EpicClosureReceipt,
    Issue,
    Link,
    Project,
    ReleaseIssue,
    ReleaseScope,
    TransitionContext,
)
from foundry.trackers.base import (
    BodyUpdateUnavailableError,
    IssueUnavailableError,
    ReleaseScopeUnavailableError,
    Tracker,
    TrackerConflictError,
)


_CONTRACT = "devhub-tracker.v1"
_APPLICATION_CONTRACT = "devhub-application.v1"
_PROJECT_CATALOG_CONTRACT = "devhub-project-catalog.v1"
_EPIC_CLOSURE_CONTRACT = "devhub-epic-closure.v1"
_ADR_AUTHORING_CONTRACT = "devhub-adr-authoring.v1"
_ADR_RELATIONSHIPS_CONTRACT = "devhub-adr-relationships.v1"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_SEARCH_ITEMS = 1_000
_MAX_SEARCH_PAGES = 100
_MAX_AUDIT_ITEMS = 1_000
_MAX_AUDIT_PAGES = 100
_MAX_CATALOG_ITEMS = 1_000
_MAX_CATALOG_PAGES = 100
_MAX_ADR_ITEMS = 1_000
_MAX_ADR_PAGES = 100
_MAX_RECEIPT_ITEMS = 1_000
_MAX_RECEIPT_PAGES = 100
_STATES = {"backlog", "ready", "in-progress", "review", "blocked", "done", "dropped"}
_START_TRANSITION_PATHS = {
    "backlog": ("ready", "in-progress"),
    "ready": ("in-progress",),
    "in-progress": (),
    "review": ("in-progress",),
    "blocked": ("in-progress",),
}
_LINK_TYPES = {"subtask-of", "parent-of", "relates", "depends-on", "blocks"}
_DIRECTIONS = {"outward", "inward"}
_INVERSE_LINK_TYPES = {
    "subtask-of": "parent-of",
    "parent-of": "subtask-of",
    "relates": "relates",
    "depends-on": "blocks",
    "blocks": "depends-on",
}
_ADR_STATES = {"proposed", "accepted", "deprecated", "superseded"}
_AUDIT_OPERATIONS = {
    "project.create", "issue.create", "issue.update", "issue.transition",
    "issue.link", "issue.comment", "adr.create", "adr.status",
}
_AUDIT_RESULTS = {"success", "refused", "unavailable"}
# PAT-309: shape of one `GET /issues/:id/proof-receipts` item (DHN-38).
_PROOF_RECEIPT_KEYS = {
    "id", "issue", "project", "action", "issue_version", "pr_url", "head_sha",
    "base_sha", "review_digest", "merge_sha", "proof_nonce", "actor", "created",
}
_RECEIPT_ID = re.compile(r"[1-9][0-9]*")
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PROOF_NONCE = re.compile(r"[A-Za-z0-9_-]{16,128}")
_RECEIPT_COORDINATES = ("pr_url", "head_sha", "base_sha", "review_digest")
_VERSION_STATUSES = {"planned", "active", "release-pending", "released", "cancelled"}
_UNFINISHED_STATES = {"backlog", "ready", "in-progress", "review", "blocked"}
_AUDIT_KEYS = {"id", "operation", "resource", "resource_version", "result", "created"}
_FIELD_MAP = {
    "Priority": "priority",
    "Estimate": "estimate",
    "Milestone": "milestone",
    "Type": "type",
    "Labels": "labels",
    "GitHub PR": "pr_url",
}
_PROJECT_KEY = re.compile(r"[A-Z][A-Z0-9]{1,15}")
_PROJECT_ID = re.compile(r"[1-9][0-9]*")
_ISSUE_ID = re.compile(r"[A-Z][A-Z0-9]{1,15}-[0-9]+")
_RECEIPT_NONCE = re.compile(r"[A-Za-z0-9_-]{16,128}")
_EPIC_CLOSURE_ERROR_STATUSES = {
    "invalid_request": 400,
    "unauthenticated": 401,
    "insufficient_scope": 403,
    "authority_boundary": 403,
    "project_unavailable": 404,
    "epic_unavailable": 404,
    "version_required": 428,
    "version_conflict": 409,
    "invalid_idempotency_key": 400,
    "idempotency_conflict": 409,
    "epic_closure_conflict": 409,
    "epic_closure_unavailable": 404,
    "epic_closure_unavailable_service": 503,
}
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_MAX_EPIC_CLOSURE_CHILDREN = 100
_AUDIT_ID = re.compile(r"[A-Za-z0-9._:-]{1,160}")


def _closure_invalid(method: str, path: str) -> None:
    raise DevHubTrackerError(method, path, None, "invalid_response")


def _closure_object(raw, keys: set[str], method: str, path: str):
    if not isinstance(raw, dict) or set(raw) != keys:
        _closure_invalid(method, path)
    return raw


def _closure_string(
    value, method: str, path: str, pattern: re.Pattern | None = None, *,
    maximum: int | None = None,
) -> str:
    if (not isinstance(value, str) or not value
            or (maximum is not None and len(value) > maximum)
            or (pattern is not None and pattern.fullmatch(value) is None)):
        _closure_invalid(method, path)
    return value


def _closure_int(
    value, method: str, path: str, *, positive: bool,
    maximum: int = _MAX_SAFE_INTEGER,
) -> int:
    if (type(value) is not int or value < (1 if positive else 0)
            or value > maximum):
        _closure_invalid(method, path)
    return value


class DevHubTrackerError(RuntimeError):
    """Sanitized provider error retaining only controlled status and code."""

    def __init__(self, method: str, path: str, status: int | None, code: str):
        self.status = status
        self.code = code
        status_text = str(status) if status is not None else "unavailable"
        super().__init__(f"DevHubTracker {method} {path} -> {status_text} ({code})")


class AdrRelationFrozenError(TrackerConflictError):
    """The ADR is decided: DevHub no longer takes an ADR-to-issue relation (DHN-33)."""

    def __init__(self, adr_id: str, status: str):
        super().__init__(
            f"relation ADR-issue refusée pour {adr_id} (statut {status}) : DevHub fige "
            "une ADR décidée ; pose la relation avant l'acceptation, ou porte-la par l'issue"
        )
        self.adr_id = adr_id
        self.status = status


def _canonical_loopback(host: str) -> bool:
    """Return whether *host* is an unambiguous numeric loopback literal."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        address.is_loopback
        and host == address.compressed
        and getattr(address, "ipv4_mapped", None) is None
        and getattr(address, "scope_id", None) is None
    )


def _origin(value: str) -> tuple[str, str, int]:
    try:
        if (not isinstance(value, str) or not value
                or any(ord(character) <= 0x20 or ord(character) == 0x7f
                       for character in value)):
            raise ValueError
        parsed = urllib.parse.urlsplit(value)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
        parsed_port = parsed.port
        port = parsed_port if parsed_port is not None else (443 if scheme == "https" else 80)
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("DEVHUB_URL invalide") from None
    if (scheme not in {"http", "https"} or not host or parsed.username is not None
            or parsed.password is not None or not 1 <= port <= 65535):
        raise ValueError("DEVHUB_URL invalide")
    if scheme == "http" and not _canonical_loopback(host):
        raise ValueError("DEVHUB_URL doit utiliser HTTPS hors loopback")
    return scheme, host, port


def validate_base_url(value: str) -> None:
    """Validate the DevHub transport origin without reading credentials or DNS."""
    _origin(value)
    parsed = urllib.parse.urlsplit(value)
    if parsed.query or parsed.fragment or "?" in value or "#" in value:
        raise ValueError("DEVHUB_URL invalide")


# Kept for callers/tests that used the original private spelling.
_validate_base_url = validate_base_url


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            target = urllib.parse.urljoin(req.full_url, newurl)
            source_origin = _origin(req.full_url)
            target_origin = _origin(target)
        except (TypeError, ValueError, UnicodeError):
            raise DevHubTrackerError(
                req.get_method(), "/redirect", code, "cross_origin_redirect",
            ) from None
        if source_origin != target_origin:
            raise DevHubTrackerError(req.get_method(), "/redirect", code, "cross_origin_redirect")
        return super().redirect_request(req, fp, code, msg, headers, target)


def _stable(value):
    if isinstance(value, list):
        return [_stable(item) for item in value]
    if isinstance(value, dict):
        return {key: _stable(value[key]) for key in sorted(value)}
    return value


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class DevHubTracker(Tracker):
    name = "devhub"
    bounded_transition_proofs = True
    requires_mutation_binding = True
    acceptance_sync_supported = True
    epic_closure_supported = True
    # PAT-ADR-0030 / DHN-24: `write.close_epic` requires every abandoned direct child
    # to be named by `--accept-dropped` before the atomic closure.
    epic_dropped_closure_supported = True
    project_provisioning_supported = True
    project_provisioning_requires_repository = True
    epic_subgraph_supported = True
    # PAT-309 / DHN-38: every proven review and done transition appends one
    # receipt in the server transaction, read by `read_proof_receipts`.
    append_only_lifecycle_supported = True

    def __init__(
        self,
        *,
        url: str | None = None,
        token: str | None = None,
        proof_secret: str | None = None,
        timeout: float = 10.0,
        now_ms=None,
        nonce_factory=None,
        idempotency_factory=None,
    ):
        candidate_url = (
            url if url is not None else config.require_public("DEVHUB_URL")
        ).rstrip("/")
        validate_base_url(candidate_url)
        self.url = candidate_url
        self.token = token if token is not None else config.require("DEVHUB_TRACKER_TOKEN")
        self.proof_secret = (
            proof_secret if proof_secret is not None
            else config.require("DEVHUB_TRACKER_PROOF_SECRET")
        )
        if len(self.token) < 24 or len(self.proof_secret) < 32:
            raise ValueError("credentials DevHubTracker invalides")
        self.timeout = timeout
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._nonce_factory = nonce_factory or (lambda: secrets.token_urlsafe(24))
        self._idempotency_factory = idempotency_factory or (lambda: uuid.uuid4().hex)

    def _req(
        self, method, path, body=None, *, version=None, proof=None,
        idempotent=False, idempotency_key=None, response_contract=_CONTRACT,
    ):
        validate_base_url(self.url)
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode() if body is not None else None
        req = urllib.request.Request(f"{self.url}/api/tracker/v1{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if version is not None:
            # Application V1 makes the unquoted decimal header canonical.  Keep the
            # complete ETag form during the documented compatibility period so an
            # older compatible runtime sees the same precondition, never a weaker
            # unversioned write.
            req.add_header("X-DevHub-Version", str(version))
            req.add_header("If-Match", f'"{version}"')
        if proof is not None:
            req.add_header("X-Foundry-Proof", proof)
        if idempotency_key is not None and not idempotent:
            raise ValueError("idempotency_key requiert idempotent=True")
        if idempotent:
            key = idempotency_key or f"foundry-{self._idempotency_factory()}"
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", key):
                raise ValueError("Idempotency-Key DevHubTracker invalide")
            req.add_header("Idempotency-Key", key)
        opener = urllib.request.build_opener(_SameOriginRedirectHandler())
        attempts = 2 if idempotent else 1
        for attempt in range(attempts):
            try:
                with opener.open(req, timeout=self.timeout) as response:
                    if response.headers.get("X-DevHub-Contract") != response_contract:
                        raise DevHubTrackerError(method, path, response.status, "contract_mismatch")
                    raw = response.read(_MAX_RESPONSE_BYTES + 1)
                    if len(raw) > _MAX_RESPONSE_BYTES:
                        raise DevHubTrackerError(method, path, response.status, "response_too_large")
                break
            except DevHubTrackerError:
                raise
            except urllib.error.HTTPError as error:
                if (response_contract == _EPIC_CLOSURE_CONTRACT
                        and (error.headers or {}).get("X-DevHub-Contract")
                        != response_contract):
                    raise DevHubTrackerError(
                        method, path, error.code, "contract_mismatch",
                    ) from None
                payload = None
                try:
                    payload = json.loads(error.read(16_384).decode("utf-8"))
                except Exception:
                    pass
                if response_contract == _EPIC_CLOSURE_CONTRACT:
                    if (not isinstance(payload, dict)
                            or set(payload) != {"schema_version", "error"}
                            or payload.get("schema_version") != response_contract
                            or not isinstance(payload.get("error"), dict)
                            or set(payload["error"]) != {"code", "message"}
                            or payload["error"].get("code") not in _EPIC_CLOSURE_ERROR_STATUSES
                            or _EPIC_CLOSURE_ERROR_STATUSES[payload["error"]["code"]] != error.code
                            or not isinstance(payload["error"].get("message"), str)
                            or not 1 <= len(payload["error"]["message"]) <= 160):
                        raise DevHubTrackerError(
                            method, path, error.code, "invalid_response",
                        ) from None
                    code = payload["error"]["code"]
                else:
                    code = "provider_error"
                    if isinstance(payload, dict):
                        candidate = payload.get("error", {}).get("code")
                        if (isinstance(candidate, str)
                                and candidate.replace("_", "").isalnum()):
                            code = candidate[:80]
                raise DevHubTrackerError(method, path, error.code, code) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt + 1 < attempts:
                    continue
                raise DevHubTrackerError(method, path, None, "tracker_unavailable") from None
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise DevHubTrackerError(method, path, None, "invalid_response") from None

    def _application_req(self, method, path, body=None, **kwargs):
        """Call an application-V1 route with its exact published contract."""
        if "response_contract" in kwargs:
            raise ValueError("contrat application DevHubTracker imposé par la route")
        if body is None:
            return self._req(
                method, path,
                response_contract=_APPLICATION_CONTRACT,
                **kwargs,
            )
        return self._req(
            method, path, body,
            response_contract=_APPLICATION_CONTRACT,
            **kwargs,
        )

    def _catalog_req(self, method, path, body=None, **kwargs):
        """Call the deliberately separate, read-only project-catalog contract."""
        if "response_contract" in kwargs:
            raise ValueError("contrat catalogue DevHubTracker imposé par la route")
        if body is None:
            return self._req(
                method, path,
                response_contract=_PROJECT_CATALOG_CONTRACT,
                **kwargs,
            )
        return self._req(
            method, path, body,
            response_contract=_PROJECT_CATALOG_CONTRACT,
            **kwargs,
        )

    def _epic_closure_req(self, method, path, body=None, **kwargs):
        """Call only Dev Hub's atomic original-Epic-closure contract."""
        if "response_contract" in kwargs:
            raise ValueError("contrat clôture Epic DevHubTracker imposé par la route")
        if body is None:
            return self._req(
                method, path, response_contract=_EPIC_CLOSURE_CONTRACT,
                **kwargs,
            )
        return self._req(
            method, path, body, response_contract=_EPIC_CLOSURE_CONTRACT,
            **kwargs,
        )

    @staticmethod
    def _to_epic_closure_receipt(
        raw, *, project: Project, parent_id: str, method: str, path: str,
    ) -> EpicClosureReceipt:
        receipt = _closure_object(raw, {
            "project_key", "project_id", "parent_id", "parent_version",
            "parent_type", "parent_ac_done", "parent_ac_total", "children",
            "issued_at", "nonce",
        }, method, path)
        project_key = _closure_string(
            receipt["project_key"], method, path, _PROJECT_KEY,
        )
        project_id = _closure_string(
            receipt["project_id"], method, path, _PROJECT_ID,
        )
        receipt_parent_id = _closure_string(
            receipt["parent_id"], method, path, _ISSUE_ID,
        )
        parent_version = _closure_int(
            receipt["parent_version"], method, path, positive=True,
        )
        parent_type = _closure_string(
            receipt["parent_type"], method, path, maximum=80,
        )
        parent_ac_done = _closure_int(
            receipt["parent_ac_done"], method, path, positive=False,
        )
        parent_ac_total = _closure_int(
            receipt["parent_ac_total"], method, path, positive=False,
        )
        issued_at = _closure_int(
            receipt["issued_at"], method, path, positive=False,
        )
        nonce = _closure_string(
            receipt["nonce"], method, path, _RECEIPT_NONCE,
        )
        if (project_key != project.key or project_id != project.id
                or receipt_parent_id != parent_id
                or not parent_id.startswith(f"{project.key}-")
                or parent_type.casefold() != "epic"
                or parent_ac_done > parent_ac_total
                or (parent_ac_total > 0 and parent_ac_done != parent_ac_total)
                or not isinstance(receipt["children"], list)
                or not 1 <= len(receipt["children"]) <= _MAX_EPIC_CLOSURE_CHILDREN):
            _closure_invalid(method, path)
        children = []
        for raw_child in receipt["children"]:
            child = _closure_object(
                raw_child, {"id", "version", "state"}, method, path,
            )
            child_id = _closure_string(child["id"], method, path, _ISSUE_ID)
            if (not child_id.startswith(f"{project.key}-")
                    or child["state"] not in {"done", "dropped"}):
                _closure_invalid(method, path)
            children.append(EpicClosureChild(
                id=child_id,
                version=_closure_int(
                    child["version"], method, path, positive=True,
                ),
                state=child["state"],
            ))
        child_ids = [child.id for child in children]
        if child_ids != sorted(child_ids) or len(child_ids) != len(set(child_ids)):
            _closure_invalid(method, path)
        return EpicClosureReceipt(
            project_key=project_key,
            project_id=project_id,
            parent_id=receipt_parent_id,
            parent_version=parent_version,
            parent_type=parent_type,
            parent_ac_done=parent_ac_done,
            parent_ac_total=parent_ac_total,
            children=tuple(children),
            issued_at=issued_at,
            nonce=nonce,
        )

    @staticmethod
    def _to_epic_closure_outcome(
        raw, *, project: Project, parent_id: str, method: str = "GET",
        path: str = "/projects/epics/closure",
    ) -> EpicClosureOutcome:
        """Strictly normalize one original, replayable provider closure."""
        envelope = _closure_object(
            raw, {"schema_version", "outcome"}, method, path,
        )
        if envelope["schema_version"] != _EPIC_CLOSURE_CONTRACT:
            _closure_invalid(method, path)
        outcome = _closure_object(envelope["outcome"], {
            "receipt", "closed_parent_version", "audit_id", "replayed",
        }, method, path)
        receipt = DevHubTracker._to_epic_closure_receipt(
            outcome["receipt"], project=project, parent_id=parent_id,
            method=method, path=path,
        )
        closed_parent_version = _closure_int(
            outcome["closed_parent_version"], method, path, positive=True,
        )
        audit_id = _closure_string(
            outcome["audit_id"], method, path, _AUDIT_ID,
        )
        if (type(outcome["replayed"]) is not bool
                or closed_parent_version != receipt.parent_version + 1):
            _closure_invalid(method, path)
        return EpicClosureOutcome(
            receipt=receipt,
            closed_parent_version=closed_parent_version,
            audit_id=audit_id,
            replayed=outcome["replayed"],
        )

    @staticmethod
    def _to_project(raw) -> Project:
        if not isinstance(raw, dict) or not isinstance(raw.get("key"), str) or not isinstance(raw.get("id"), str):
            raise DevHubTrackerError("GET", "/projects", None, "invalid_response")
        extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
        # The canonical Tracker API exposes this binding at the top level.  Keep
        # accepting the historical adapter shape in ``extra`` so existing
        # registrations remain readable, but never let the two shapes disagree.
        canonical_repository = raw.get("canonical_repo")
        if canonical_repository is not None:
            if not isinstance(canonical_repository, str):
                raise DevHubTrackerError("GET", "/projects", None, "invalid_response")
            legacy_repository = extra.get("canonical_repo")
            if legacy_repository is not None and legacy_repository != canonical_repository:
                raise DevHubTrackerError("GET", "/projects", None, "invalid_response")
            extra = {**extra, "canonical_repo": canonical_repository}
        return Project(key=raw["key"], id=raw["id"], extra=dict(extra))

    @staticmethod
    def _to_issue(raw) -> Issue:
        if (not isinstance(raw, dict)
                or not all(isinstance(raw.get(key), str) for key in ("id", "title", "state"))
                or raw["state"] not in _STATES
                or type(raw.get("version")) is not int or raw["version"] < 1
                or type(raw.get("ac_done")) is not int or raw["ac_done"] < 0
                or type(raw.get("ac_total")) is not int or raw["ac_total"] < raw["ac_done"]
                or not isinstance(raw.get("labels"), list)
                or not all(isinstance(item, str) for item in raw["labels"])):
            raise DevHubTrackerError("GET", "/issues", None, "invalid_response")
        links = []
        raw_links = raw.get("links")
        if raw_links is not None and not isinstance(raw_links, list):
            raise DevHubTrackerError("GET", "/issues", None, "invalid_response")
        for item in raw_links or []:
            if (not isinstance(item, dict) or item.get("type") not in _LINK_TYPES
                    or item.get("direction") not in _DIRECTIONS
                    or not isinstance(item.get("target"), str)):
                raise DevHubTrackerError("GET", "/issues", None, "invalid_response")
            direction = item["direction"]
            link_type = item["type"]
            if direction == "inward":
                link_type = _INVERSE_LINK_TYPES[link_type]
            links.append(Link(type=link_type, direction=direction, target=item["target"]))
        comments = []
        raw_comments = raw.get("comments")
        if raw_comments is not None and not isinstance(raw_comments, list):
            raise DevHubTrackerError("GET", "/issues", None, "invalid_response")
        for item in raw_comments or []:
            if (not isinstance(item, dict) or not isinstance(item.get("text"), str)
                    or type(item.get("created")) is not int):
                raise DevHubTrackerError("GET", "/issues", None, "invalid_response")
            comments.append({"text": item["text"], "created": item["created"]})
        return Issue(
            id=raw["id"], title=raw.get("title", ""), state=raw.get("state"),
            priority=raw.get("priority"), estimate=raw.get("estimate"),
            milestone=raw.get("milestone"), type=raw.get("type"),
            labels=list(raw.get("labels") or []), ac_done=int(raw.get("ac_done") or 0),
            ac_total=int(raw.get("ac_total") or 0), links=links,
            pr_url=raw.get("pr_url"), body=raw.get("body"), comments=comments,
            created=raw.get("created"), updated=raw.get("updated"),
            version=raw["version"],
        )

    @staticmethod
    def _to_adr(raw) -> Adr:
        if (not isinstance(raw, dict) or not isinstance(raw.get("id"), str)
                or not isinstance(raw.get("title"), str)
                or raw.get("status") not in _ADR_STATES
                or type(raw.get("version")) is not int or raw["version"] < 1):
            raise DevHubTrackerError("GET", "/adrs", None, "invalid_response")
        return Adr(id=raw["id"], title=raw.get("title", ""), status=raw.get("status", "proposed"),
                   body=raw.get("body"), ref=raw.get("ref"))

    @staticmethod
    def _project_key(issue_id: str) -> str:
        key, separator, number = issue_id.rpartition("-")
        if not separator or not key or not number.isdigit():
            raise ValueError("issue id DevHubTracker invalide")
        return key

    @staticmethod
    def _adr_project_key(adr_id: str) -> str:
        key, separator, number = adr_id.partition("-ADR-")
        if not separator or not key or not number.isdigit() or len(number) < 4:
            raise ValueError("ADR id DevHubTracker invalide")
        return key

    @staticmethod
    def _require_project(project: Project | None) -> Project:
        if (not isinstance(project, Project) or not isinstance(project.key, str)
                or not project.key or not isinstance(project.id, str) or not project.id):
            raise ValueError("binding projet DevHubTracker requis")
        return project

    def _require_issue_binding(self, issue_id: str, project: Project | None) -> str:
        bound = self._require_project(project)
        if self._project_key(issue_id) != bound.key:
            raise SystemExit(
                f"Binding DevHub refusé pour '{issue_id}' : le repo courant est lié à {bound.key}."
            )
        return bound.key

    def _require_adr_binding(self, adr_id: str, project: Project | None) -> str:
        bound = self._require_project(project)
        if self._adr_project_key(adr_id) != bound.key:
            raise SystemExit(
                f"Binding DevHub refusé pour '{adr_id}' : le repo courant est lié à {bound.key}."
            )
        return bound.key

    def validate_issue_binding(self, project: Project, *issue_ids: str) -> None:
        for issue_id in issue_ids:
            self._require_issue_binding(issue_id, project)

    def validate_adr_binding(self, project: Project, *adr_ids: str) -> None:
        for adr_id in adr_ids:
            self._require_adr_binding(adr_id, project)

    def validate_mutation_repository(self, repo: str, checkout_identity: str) -> None:
        registered = registry.resolve(self.name, repo)
        canonical = registered.extra.get("canonical_repo")
        if not isinstance(canonical, str) or not canonical:
            raise SystemExit(
                f"Binding DevHub incomplet pour '{repo}' : canonical_repo requis."
            )
        try:
            expected = registry.canonical_repository_identity(canonical)
        except ValueError:
            raise SystemExit(
                f"Binding DevHub invalide pour '{repo}' : canonical_repo non canonique."
            ) from None
        if checkout_identity != expected:
            raise SystemExit(
                f"Binding DevHub refusé pour '{repo}' : le remote origin courant "
                "ne correspond pas au canonical_repo enregistré."
            )

    def _issue_raw(self, issue_id: str):
        path = f"/issues/{urllib.parse.quote(issue_id, safe='')}"
        try:
            raw = self._application_req("GET", path)
        except DevHubTrackerError as error:
            if error.status in {403, 404}:
                raise IssueUnavailableError(issue_id) from None
            raise
        if (not isinstance(raw, dict) or type(raw.get("version")) is not int
                or raw["version"] < 1):
            raise DevHubTrackerError("GET", path, None, "invalid_response")
        return raw

    def _bound_issue_raw(self, issue_id: str, project: Project | None):
        self._require_issue_binding(issue_id, project)
        return self._issue_raw(issue_id)

    def _proof(self, *, action, project_key, resource_id, version, context=None, target_status=None):
        now = self._now_ms()
        payload = {
            "action": action,
            "contract_version": _CONTRACT,
            "expected_version": version,
            "expires_at": now + 5 * 60 * 1000,
            "nonce": self._nonce_factory(),
            "project_key": project_key,
            "resource_id": resource_id,
        }
        if context is not None:
            for key in ("pr_url", "head_sha", "base_sha", "review_digest", "merge_sha"):
                value = getattr(context, key)
                if value is not None:
                    payload[key] = value
        if target_status is not None:
            payload["target_status"] = target_status
        encoded = _b64url(json.dumps(_stable(payload), ensure_ascii=False, separators=(",", ":")).encode())
        signature = hmac.new(self.proof_secret.encode(), encoded.encode("ascii"), hashlib.sha256).digest()
        return f"{encoded}.{_b64url(signature)}"

    def provision_project(
        self, name: str, key: str, canonical_repository: str | None = None,
    ) -> Project:
        """Create or recover one repository-bound Dev Hub tracker project."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("nom de projet DevHubTracker invalide")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", key):
            raise ValueError("ticker de projet DevHubTracker invalide")
        if not isinstance(canonical_repository, str):
            raise ValueError("remote origin canonique requis pour DevHubTracker")
        try:
            canonical_repository = registry.canonical_repository_identity(
                canonical_repository,
            )
        except ValueError:
            raise ValueError("remote origin canonique invalide pour DevHubTracker") from None

        resolve_path = "/projects/resolve?" + urllib.parse.urlencode(
            {"repo": canonical_repository}
        )
        try:
            raw = self._application_req("GET", resolve_path)
        except DevHubTrackerError as error:
            if error.status != 404:
                raise
            raw = None

        if raw is None:
            identity = f"{key}\0{canonical_repository}".encode("utf-8")
            idempotency_key = f"foundry-project-{hashlib.sha256(identity).hexdigest()}"
            created = self._to_project(self._application_req(
                "POST", "/projects",
                {"name": name.strip(), "slug": key.lower(), "ticker": key,
                 "repository": canonical_repository},
                idempotent=True, idempotency_key=idempotency_key,
            ))
            if created.key != key:
                raise DevHubTrackerError("POST", "/projects", None, "binding_mismatch")
            # Bind the returned native id to the repository resolver before the
            # caller persists anything locally. A retry after interruption starts
            # with this same read and therefore cannot create a second project.
            raw = self._application_req("GET", resolve_path)

        project = self._to_project(raw)
        if project.key != key:
            raise DevHubTrackerError("GET", "/projects/resolve", None, "binding_mismatch")
        remote_repository = project.extra.get("canonical_repo")
        try:
            remote_repository = registry.canonical_repository_identity(remote_repository)
        except ValueError:
            raise DevHubTrackerError(
                "GET", "/projects/resolve", None, "binding_mismatch",
            ) from None
        if remote_repository != canonical_repository:
            raise DevHubTrackerError(
                "GET", "/projects/resolve", None, "binding_mismatch",
            )
        # Only this credential-free binding coordinate is part of Foundry's
        # provisioning contract; never copy opaque provider metadata into the
        # durable local registry.
        return Project(
            key=project.key, id=project.id,
            extra={"canonical_repo": canonical_repository},
        )

    def resolve_project(self, repo: str) -> Project:
        registered = registry.resolve(self.name, repo)
        canonical = registered.extra.get("canonical_repo")
        if not isinstance(canonical, str) or not canonical:
            raise SystemExit(f"Binding DevHub incomplet pour '{repo}' : canonical_repo requis.")
        try:
            # Registry loads already sanitize legacy records. Keep the transport
            # boundary defensive for injected/custom registries as well: only the
            # credential-free canonical identity may enter a request target.
            canonical = registry.canonical_repository_identity(canonical)
        except ValueError:
            raise SystemExit(
                f"Binding DevHub invalide pour '{repo}' : canonical_repo non canonique."
            ) from None
        raw = self._application_req(
            "GET", "/projects/resolve?" + urllib.parse.urlencode({"repo": canonical}),
        )
        remote = self._to_project(raw)
        if remote.key != registered.key or remote.id != registered.id:
            raise SystemExit(f"Binding DevHub contradictoire pour '{repo}' ; mutation refusée.")
        return remote

    def verify_project_identity(self, project: Project) -> bool:
        """Read back the exact project before a binding is published (DHN-41, PAT-314).

        One ``GET /projects/resolve`` on the project's canonical repository; ``True`` only
        when the server answers the same key, id and canonical repository.  An absent
        or non-canonical ``canonical_repo``, or a 404, is ``False``; other errors raise.
        """
        canonical = project.extra.get("canonical_repo")
        try:
            canonical = registry.canonical_repository_identity(canonical)
        except (TypeError, ValueError):
            return False
        try:
            raw = self._application_req(
                "GET", "/projects/resolve?" + urllib.parse.urlencode({"repo": canonical}),
            )
        except DevHubTrackerError as error:
            if error.status == 404:
                return False
            raise
        remote = self._to_project(raw)
        try:
            remote_repository = registry.canonical_repository_identity(
                remote.extra.get("canonical_repo"),
            )
        except (TypeError, ValueError):
            return False
        return (remote.key == project.key and remote.id == project.id
                and remote_repository == canonical)

    def resolve_checkout_project(
        self,
        cwd: str | None = None,
        *,
        checkout_identity: str | None = None,
    ) -> Project:
        """Keep the internal pilot's historical registry resolution outside V1."""
        del cwd, checkout_identity
        return self.resolve_project(registry.repo_basename())

    def search(self, project: Project, query: str = "") -> list[Issue]:
        items, cursor, seen = [], 0, set()
        self._require_project(project)
        for _page_number in range(1, _MAX_SEARCH_PAGES + 1):
            params = urllib.parse.urlencode({"query": query, "cursor": cursor, "limit": 100})
            raw = self._application_req(
                "GET", f"/projects/{urllib.parse.quote(project.key, safe='')}/issues?{params}",
            )
            if not isinstance(raw, dict) or not isinstance(raw.get("items"), list) or not isinstance(raw.get("page"), dict):
                raise DevHubTrackerError("GET", "/projects/issues", None, "invalid_response")
            page = raw["page"]
            if (page.get("schema_version") != _APPLICATION_CONTRACT
                    or type(page.get("truncation")) is not bool
                    or type(page.get("returned_count")) is not int
                    or page["returned_count"] != len(raw["items"])):
                raise DevHubTrackerError("GET", "/projects/issues", None, "invalid_response")
            for item in raw["items"]:
                issue = self._to_issue(item)
                if issue.id in seen:
                    raise DevHubTrackerError("GET", "/projects/issues", None, "pagination_repeated")
                seen.add(issue.id)
                items.append(issue)
                if len(items) > _MAX_SEARCH_ITEMS:
                    raise DevHubTrackerError("GET", "/projects/issues", None, "pagination_limit")
            if not page["truncation"]:
                return items
            if not raw["items"]:
                raise DevHubTrackerError(
                    "GET", "/projects/issues", None, "pagination_empty_page",
                )
            next_cursor = page.get("cursor")
            if type(next_cursor) is not int or next_cursor <= cursor:
                raise DevHubTrackerError("GET", "/projects/issues", None, "pagination_stalled")
            cursor = next_cursor
        raise DevHubTrackerError("GET", "/projects/issues", None, "pagination_page_limit")

    def get_epic_subgraph(
        self, project: Project, epic_id: str, *, depth: int = 8, nodes: int = 100,
    ) -> dict:
        """Read the strict Dev Hub projection without acquiring execution authority."""
        project = self._require_project(project)
        self._require_issue_binding(epic_id, project)
        if (type(depth) is not int or not 0 <= depth <= 8
                or type(nodes) is not int or not 1 <= nodes <= 100):
            raise ValueError("budget de sous-graphe DevHubTracker invalide")
        params = urllib.parse.urlencode({"root": epic_id, "depth": depth, "nodes": nodes})
        path = (
            f"/projects/{urllib.parse.quote(project.key, safe='')}/subgraph?{params}"
        )
        raw = self._application_req("GET", path)
        if not isinstance(raw, dict):
            raise DevHubTrackerError("GET", "/projects/subgraph", None, "invalid_response")
        return raw

    def _epic_closure_path(self, project: Project, parent_id: str) -> str:
        project = self._require_project(project)
        if (_PROJECT_KEY.fullmatch(project.key) is None
                or _PROJECT_ID.fullmatch(project.id) is None):
            raise ValueError("binding projet clôture Epic DevHubTracker invalide")
        self._require_issue_binding(parent_id, project)
        if _ISSUE_ID.fullmatch(parent_id) is None:
            raise ValueError("identifiant Epic DevHubTracker invalide")
        return (
            f"/projects/{urllib.parse.quote(project.key, safe='')}"
            f"/epics/{urllib.parse.quote(parent_id, safe='')}/closure"
        )

    def close_epic(
        self, project: Project, receipt: EpicClosureReceipt,
    ) -> EpicClosureOutcome:
        """Atomically close one Epic through Dev Hub's original receipt boundary."""
        if type(receipt) is not EpicClosureReceipt:
            raise ValueError("reçu clôture Epic DevHubTracker invalide")
        path = self._epic_closure_path(project, receipt.parent_id)
        receipt_wire = {
            "project_key": receipt.project_key,
            "project_id": receipt.project_id,
            "parent_id": receipt.parent_id,
            "parent_version": receipt.parent_version,
            "parent_type": receipt.parent_type,
            "parent_ac_done": receipt.parent_ac_done,
            "parent_ac_total": receipt.parent_ac_total,
            "children": [
                {"id": child.id, "version": child.version, "state": child.state}
                for child in receipt.children
            ] if isinstance(receipt.children, tuple) else receipt.children,
            "issued_at": receipt.issued_at,
            "nonce": receipt.nonce,
        }
        request = {
            "schema_version": _EPIC_CLOSURE_CONTRACT,
            "receipt": receipt_wire,
        }
        normalized = self._to_epic_closure_receipt(
            request["receipt"], project=project, parent_id=receipt.parent_id,
            method="POST", path=path,
        )
        if normalized != receipt:
            _closure_invalid("POST", path)
        encoded = json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        idempotency_key = (
            f"foundry-epic-close-{hashlib.sha256(encoded).hexdigest()}"
        )
        outcome = self._to_epic_closure_outcome(
            self._epic_closure_req(
                "POST", path, request, version=receipt.parent_version,
                idempotent=True, idempotency_key=idempotency_key,
            ),
            project=project, parent_id=receipt.parent_id,
            method="POST", path=path,
        )
        if outcome.receipt != receipt:
            _closure_invalid("POST", path)
        return outcome

    def get_epic_closure(
        self, project: Project, parent_id: str,
    ) -> EpicClosureOutcome | None:
        """Read the exact original Dev Hub closure without projecting a new fact."""
        path = self._epic_closure_path(project, parent_id)
        try:
            raw = self._epic_closure_req("GET", path)
        except DevHubTrackerError as error:
            if error.status == 404 and error.code == "epic_closure_unavailable":
                return None
            raise
        outcome = self._to_epic_closure_outcome(
            raw, project=project, parent_id=parent_id, method="GET", path=path,
        )
        if outcome.replayed is not True:
            _closure_invalid("GET", path)
        return outcome

    def get_issue(self, issue_id: str) -> Issue:
        return self._to_issue(self._issue_raw(issue_id))

    def create_issue(self, project, title, body, fields=None, parent=None) -> Issue:
        project = self._require_project(project)
        parent_version = None
        if parent is not None:
            self._require_issue_binding(parent, project)
            parent_version = self._bound_issue_raw(parent, project)["version"]
        normalized = self._normalize_fields(fields or {}, allow_state=True)
        request = {"title": title, "body": body, "fields": normalized, "parent": parent}
        if parent_version is not None:
            request["parent_version"] = parent_version
        raw = self._application_req(
            "POST", f"/projects/{urllib.parse.quote(project.key, safe='')}/issues",
            request, idempotent=True,
        )
        return self._to_issue(raw)

    def reconcile_issue_creation(
        self, project, issue_id, title, body, fields=None, parent=None,
    ) -> dict:
        """Read-only check that ``issue_id`` is the creation described (DHN-37, PAT-313).

        Reads the issue once; never writes, replays or creates.  DevHub offers no read by
        ``Idempotency-Key`` (``devhub-requirements.md``, question 9), so the reference must be
        an issue id of the bound project, not a client identifier.  A divergence raises
        ``TrackerConflictError`` (the "validation refused; no retry" text of the Linear
        adapter); an absent issue raises ``IssueUnavailableError``.
        """
        project = self._require_project(project)
        if not isinstance(title, str) or not title or not isinstance(body, str):
            raise ValueError("titre/corps de création DevHubTracker invalides")
        self._require_issue_binding(issue_id, project)
        if parent:
            self._require_issue_binding(parent, project)
        wanted = self._normalize_fields(fields or {}, allow_state=True)
        raw = self._issue_raw(issue_id)
        issue = self._to_issue(raw)
        parents = [
            link.target for link in issue.links if link.type == "subtask-of"
        ]
        diverged = [
            name for name, same in (
                ("identity", issue.id == issue_id),
                ("title", issue.title == title),
                ("body", raw.get("body") == body),
                ("parent", parents == ([parent] if parent else [])),
            ) if not same
        ] + [
            key for key, value in sorted(wanted.items())
            if (sorted(raw.get(key) or []) != sorted(value or [])
                if key == "labels" else raw.get(key) != value)
        ]
        if diverged:
            # Names the diverging properties, never their values.
            raise TrackerConflictError(
                "DevHub issue creation validation refused; no retry "
                f"(diverged: {', '.join(diverged)})"
            )
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return {
            "issue_id": issue.id, "native_id": issue.id,
            "creation": "observed-existing", "validation": "accepted",
            "reconciliation": "validated-read-only", "automatic_retry": False,
            "comparison": "exact", "source_sha256": digest, "readback_sha256": digest,
            "compared": ["project", "identity", "title", "body", "parent"]
                        + sorted(fields or {}),
        }

    @staticmethod
    def _normalize_fields(fields, *, allow_state=False):
        normalized = {}
        for key, value in fields.items():
            if key == "State" and allow_state:
                normalized["state"] = value
            elif key in _FIELD_MAP:
                normalized[_FIELD_MAP[key]] = value
            else:
                raise ValueError(f"champ Tracker non supporté par DevHub : {key}")
        return normalized

    def update_fields(
        self, issue_id: str, fields: dict, project: Project | None = None,
    ) -> Issue:
        self._require_issue_binding(issue_id, project)
        pending = dict(fields)
        state = pending.pop("State", None)
        if pending:
            raw = self._bound_issue_raw(issue_id, project)
            raw = self._application_req(
                "PATCH", f"/issues/{urllib.parse.quote(issue_id, safe='')}",
                {"fields": self._normalize_fields(pending)}, version=raw["version"], idempotent=True,
            )
        if state is not None:
            self.set_state(issue_id, state, project=project)
        return self.get_issue(issue_id)

    def sync_acceptance_body(
        self, issue_id: str, expected_body: str, updated_body: str, proof: str | dict,
        project: Project | None = None,
    ) -> bool:
        """Patch and audit the body behind Dev Hub's exact version boundary."""
        proof_id = proof.get("proof_id") if isinstance(proof, dict) else proof
        if not isinstance(proof_id, str) or not re.fullmatch(r"[0-9a-f]{64}", proof_id):
            raise ValueError("proof_id AC invalide")
        self._require_issue_binding(issue_id, project)
        raw = self._bound_issue_raw(issue_id, project)
        current_body = raw.get("body")
        if current_body == updated_body:
            return False
        if current_body != expected_body:
            raise TrackerConflictError(
                "corps DevHub modifié ; recharge l'issue puis relance le merge"
            )
        try:
            self._application_req(
                "PATCH", f"/issues/{urllib.parse.quote(issue_id, safe='')}",
                {"body": updated_body}, version=raw["version"], idempotent=True,
                idempotency_key=f"foundry-ac-{proof_id}",
            )
        except DevHubTrackerError as exc:
            if exc.status == 409 and exc.code == "version_conflict":
                raise TrackerConflictError(
                    "version DevHub modifiée ; recharge l'issue puis relance le merge"
                ) from None
            raise
        return True

    def update_body(
        self, resource: Issue | Adr, expected_body: str, updated_body: str,
        project: Project | None = None,
    ) -> bool:
        """Replace an issue body through the body `PATCH` route (PAT-312, DHN-21).

        The body is read first and compared with ``expected_body``; the write carries
        the version of that read, so a concurrent edit is a 409 `version_conflict`.
        An ADR body is not written here: the ADR edit route is limited to proposed
        ADRs and is only used by `link_adr_issue`.
        """
        if isinstance(resource, Adr):
            raise BodyUpdateUnavailableError(
                "mise à jour de corps indisponible pour une ADR DevHub"
            )
        if not isinstance(expected_body, str) or not isinstance(updated_body, str):
            raise ValueError("corps attendu et voulu doivent être des chaînes")
        raw = self._bound_issue_raw(resource.id, project)
        current_body = raw.get("body") or ""
        if current_body != expected_body:
            raise TrackerConflictError(
                "corps DevHub modifié ; recharge l'issue puis relance l'écriture"
            )
        if current_body == updated_body:
            return False
        try:
            self._application_req(
                "PATCH", f"/issues/{urllib.parse.quote(resource.id, safe='')}",
                {"body": updated_body}, version=raw["version"], idempotent=True,
            )
        except DevHubTrackerError as exc:
            if exc.status == 409 and exc.code == "version_conflict":
                raise TrackerConflictError(
                    "version DevHub modifiée ; recharge l'issue puis relance l'écriture"
                ) from None
            raise
        if (self._issue_raw(resource.id).get("body") or "") != updated_body:
            raise DevHubTrackerError(
                "GET", f"/issues/{urllib.parse.quote(resource.id, safe='')}",
                None, "invalid_response",
            )
        return True

    def start_transition_path(self, current_state: str) -> tuple[str, ...]:
        """Preflight Dev Hub's constrained workflow before Git is mutated."""
        try:
            return _START_TRANSITION_PATHS[current_state]
        except (KeyError, TypeError):
            raise ValueError(
                f"issue DevHub non démarrable depuis l'état '{current_state}'"
            ) from None

    def set_state(
        self, issue_id: str, state: str, context: TransitionContext | None = None,
        project: Project | None = None,
    ) -> None:
        if state not in _STATES:
            raise ValueError("état Tracker non supporté par DevHub")
        if state == "review":
            if context is None or not all(
                (context.pr_url, context.head_sha, context.base_sha, context.review_digest)
            ):
                raise ValueError("preuve review incomplète")
        elif state == "done":
            if context is None or not all((
                context.pr_url, context.head_sha, context.base_sha,
                context.review_digest, context.merge_sha,
            )):
                raise ValueError("preuve done incomplète")
        elif context is not None:
            raise ValueError("preuve de transition inattendue")
        project_key = self._require_issue_binding(issue_id, project)
        raw = self._bound_issue_raw(issue_id, project)
        if state == "review" and raw.get("state") == "review":
            # DevHub answers review -> review with the stored issue and no new
            # receipt (`transitionTrackerIssue`: `row.state === state`). A new PR
            # generation must therefore go back to in-progress first, as the
            # refresh of `issue merge` does for adapters without this flag.
            receipts = self.read_proof_receipts(issue_id, project=project)
            if receipts and self._same_review(receipts[-1], context):
                return
            self._application_req(
                "POST", f"/issues/{urllib.parse.quote(issue_id, safe='')}/state",
                {"state": "in-progress"}, version=raw["version"], idempotent=True,
            )
            raw = self._bound_issue_raw(issue_id, project)
        proof = None
        if state == "review":
            proof = self._proof(
                action="review", project_key=project_key,
                resource_id=issue_id, version=raw["version"], context=context,
            )
        elif state == "done":
            proof = self._proof(
                action="done", project_key=project_key,
                resource_id=issue_id, version=raw["version"], context=context,
            )
        self._application_req(
            "POST", f"/issues/{urllib.parse.quote(issue_id, safe='')}/state",
            {"state": state}, version=raw["version"], proof=proof, idempotent=True,
        )

    @staticmethod
    def _same_review(receipt: dict, context: TransitionContext) -> bool:
        return receipt["action"] == "review" and all(
            receipt[key] == getattr(context, key) for key in _RECEIPT_COORDINATES
        )

    def read_proof_receipts(
        self, issue_id: str, project: Project | None = None,
    ) -> tuple[dict, ...]:
        """Read the append-only review/done receipts of one issue, oldest first.

        Route and fields: DevHub `docs/TRACKER_API.md`, "Issue proof receipts" (read at
        `2edcea2`). Every item is validated; a malformed, foreign, repeated or
        out-of-order item, or a stalled cursor, fails closed.
        """
        key = (self._require_issue_binding(issue_id, project) if project is not None
               else self._project_key(issue_id))
        base = f"/issues/{urllib.parse.quote(issue_id, safe='')}/proof-receipts"
        receipts: list[dict] = []
        seen_nonces: set[str] = set()
        cursor = "0"
        for _page_number in range(1, _MAX_RECEIPT_PAGES + 1):
            params = urllib.parse.urlencode({"cursor": cursor, "limit": 100})
            try:
                raw = self._application_req("GET", f"{base}?{params}")
            except DevHubTrackerError as error:
                if error.status in {403, 404}:
                    raise IssueUnavailableError(issue_id) from None
                raise
            if (not isinstance(raw, dict) or set(raw) != {"items", "page"}
                    or not isinstance(raw["items"], list)
                    or not isinstance(raw["page"], dict)):
                raise DevHubTrackerError("GET", base, None, "invalid_response")
            page = raw["page"]
            if (page.get("schema_version") != _APPLICATION_CONTRACT
                    or type(page.get("truncation")) is not bool
                    or type(page.get("returned_count")) is not int
                    or page["returned_count"] != len(raw["items"])):
                raise DevHubTrackerError("GET", base, None, "invalid_response")
            for item in raw["items"]:
                receipt = self._to_proof_receipt(item, issue_id, key, base)
                previous = receipts[-1]["id"] if receipts else cursor
                if int(receipt["id"]) <= int(previous):
                    raise DevHubTrackerError("GET", base, None, "pagination_repeated")
                if receipt["proof_nonce"] in seen_nonces:
                    raise DevHubTrackerError("GET", base, None, "pagination_repeated")
                seen_nonces.add(receipt["proof_nonce"])
                receipts.append(receipt)
                if len(receipts) > _MAX_RECEIPT_ITEMS:
                    raise DevHubTrackerError("GET", base, None, "pagination_limit")
            if not page["truncation"]:
                return tuple(receipts)
            if not raw["items"]:
                raise DevHubTrackerError("GET", base, None, "pagination_empty_page")
            if page.get("cursor") != receipts[-1]["id"]:
                raise DevHubTrackerError("GET", base, None, "pagination_stalled")
            cursor = receipts[-1]["id"]
        raise DevHubTrackerError("GET", base, None, "pagination_page_limit")

    @staticmethod
    def _to_proof_receipt(item, issue_id: str, project_key: str, path: str) -> dict:
        if (not isinstance(item, dict) or set(item) != _PROOF_RECEIPT_KEYS
                or not isinstance(item["id"], str) or not _RECEIPT_ID.fullmatch(item["id"])
                or item["issue"] != issue_id or item["project"] != project_key
                or item["action"] not in {"review", "done"}
                or type(item["issue_version"]) is not int or item["issue_version"] < 1
                or not isinstance(item["pr_url"], str) or not 1 <= len(item["pr_url"]) <= 500
                or not isinstance(item["head_sha"], str) or not _GIT_SHA.fullmatch(item["head_sha"])
                or not isinstance(item["base_sha"], str) or not _GIT_SHA.fullmatch(item["base_sha"])
                or not isinstance(item["review_digest"], str)
                or not _SHA256.fullmatch(item["review_digest"])
                or (item["action"] == "done") != (item["merge_sha"] is not None)
                or (item["merge_sha"] is not None
                    and (not isinstance(item["merge_sha"], str)
                         or not _GIT_SHA.fullmatch(item["merge_sha"])))
                or not isinstance(item["proof_nonce"], str)
                or not _PROOF_NONCE.fullmatch(item["proof_nonce"])
                or not isinstance(item["actor"], str) or not 1 <= len(item["actor"]) <= 200
                or type(item["created"]) is not int or item["created"] < 0):
            raise DevHubTrackerError("GET", path, None, "invalid_response")
        return dict(item)

    def recover_done_projection(
        self, issue_id: str, *, pr_url: str, head_sha: str, base_sha: str,
        merge_sha: str, project: Project | None = None,
    ) -> bool:
        """Confirm, never write, that the done state is backed by the exact receipt.

        DevHub appends the done receipt and sets the state in one transaction
        (`2edcea2:src/tracker/store.ts:1714-1744`), so there is no native State left to
        repair: this returns True only when the issue is `done` on this PR and exactly
        one done receipt carries these coordinates after a review receipt of the same
        head, base and diff digest.
        """
        if project is None:
            return False
        self._require_issue_binding(issue_id, project)
        issue = self.get_issue(issue_id)
        if issue.state != "done" or issue.pr_url != pr_url:
            return False
        receipts = self.read_proof_receipts(issue_id, project=project)
        done = [
            receipt for receipt in receipts
            if receipt["action"] == "done"
            and (receipt["pr_url"], receipt["head_sha"], receipt["base_sha"],
                 receipt["merge_sha"]) == (pr_url, head_sha, base_sha, merge_sha)
        ]
        if len(done) != 1:
            return False
        return any(
            receipt["action"] == "review" and int(receipt["id"]) < int(done[0]["id"])
            and all(receipt[key] == done[0][key] for key in _RECEIPT_COORDINATES)
            for receipt in receipts
        )

    def read_release_scope(self, project: Project, release: str) -> ReleaseScope:
        """Read one mapped Version scope (DHN-39), never discovering it by name.

        The mapping is `project.extra["release_ids"][release]`, the Version id
        `<KEY>-VERSION-N` (names are not unique); the read is `GET /versions/:id`.
        """
        project = self._require_project(project)
        mapping = project.extra.get("release_ids")
        if not isinstance(mapping, dict) or release not in mapping:
            raise ReleaseScopeUnavailableError(self.name, release, "unmapped")
        version_id = mapping[release]
        if (not isinstance(version_id, str) or not re.fullmatch(
                rf"{re.escape(project.key)}-VERSION-[1-9][0-9]*", version_id)):
            raise ReleaseScopeUnavailableError(self.name, release, "invalid_mapping")
        path = f"/versions/{urllib.parse.quote(version_id, safe='')}"
        try:
            raw = self._application_req("GET", path)
        except DevHubTrackerError as error:
            if error.status in {403, 404}:
                raise ReleaseScopeUnavailableError(
                    self.name, release, "inaccessible_or_absent",
                ) from None
            raise
        scope = raw.get("scope") if isinstance(raw, dict) else None
        direct = scope.get("direct") if isinstance(scope, dict) else None
        if (not isinstance(raw, dict) or not isinstance(scope, dict)
                or raw.get("id") != version_id or raw.get("project") != project.key
                or raw.get("status") not in _VERSION_STATUSES
                or type(raw.get("version")) is not int or raw["version"] < 1
                or raw.get("kind") not in {"code", "non-code"}
                or not isinstance(direct, list)
                or scope.get("complete") is not True
                or type(scope.get("direct_count")) is not int
                or type(scope.get("total_count")) is not int
                or scope["direct_count"] != len(direct)
                or len(set(direct)) != len(direct)
                or any(not isinstance(item, str) or not re.fullmatch(
                    rf"{re.escape(project.key)}-[0-9]+", item) for item in direct)):
            raise ReleaseScopeUnavailableError(self.name, release, "invalid_response")
        if raw.get("name") != release:
            raise ReleaseScopeUnavailableError(self.name, release, "mapping_mismatch")
        if scope["total_count"] != scope["direct_count"]:
            # The Version publishes only its direct issue ids; the Epic descendants it
            # counts are not listed, and listing them would be approximate discovery.
            raise ReleaseScopeUnavailableError(
                self.name, release, "indirect_scope_unlisted",
            )
        issues = []
        for issue_id in sorted(direct):
            try:
                issue = self._to_issue(self._issue_raw(issue_id))
            except IssueUnavailableError:
                raise ReleaseScopeUnavailableError(
                    self.name, release, "issue_unavailable",
                ) from None
            issues.append(self._release_issue(issue, project))
        return ReleaseScope(
            provider=self.name,
            project_key=project.key,
            project_id=project.id,
            release=release,
            release_id=version_id,
            native_state=raw["status"],
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
                "version_id": version_id,
                "version": raw["version"],
                "kind": raw["kind"],
            },
        )

    def _release_issue(self, issue: Issue, project: Project) -> ReleaseIssue:
        references: dict[str, object] = {}
        if issue.pr_url:
            references["pr_url"] = issue.pr_url
        if issue.state in _UNFINISHED_STATES:
            disposition = "unfinished"
        elif issue.state == "done":
            # Accepted only with the exact done receipt and all criteria checked; a
            # done issue with unchecked criteria could be an audited override, which
            # DevHub has no typed receipt for (DEVHUB-106): it stays unavailable.
            disposition = "unavailable"
            receipts = self.read_proof_receipts(issue.id, project=project)
            done = [receipt for receipt in receipts if receipt["action"] == "done"]
            if (len(done) == 1 and done[0]["pr_url"] == issue.pr_url
                    and issue.ac_total >= 1 and issue.ac_done == issue.ac_total
                    and any(
                        receipt["action"] == "review"
                        and int(receipt["id"]) < int(done[0]["id"])
                        and all(receipt[key] == done[0][key]
                                for key in _RECEIPT_COORDINATES)
                        for receipt in receipts)):
                disposition = "accepted"
                references.update({
                    key: done[0][key] for key in (*_RECEIPT_COORDINATES, "merge_sha")
                })
                references["proof_receipt_id"] = done[0]["id"]
        else:
            disposition = "unavailable"
        return ReleaseIssue(
            id=issue.id, title=issue.title, type=issue.type, state=issue.state,
            labels=tuple(issue.labels), disposition=disposition, references=references,
        )

    def link(
        self, src_id: str, link_type: str, dst_id: str,
        project: Project | None = None,
    ) -> None:
        if link_type not in _LINK_TYPES:
            raise ValueError("type de lien Tracker non supporté par DevHub")
        self._require_issue_binding(src_id, project)
        self._require_issue_binding(dst_id, project)
        source = self._bound_issue_raw(src_id, project)
        target = self._bound_issue_raw(dst_id, project)
        self._application_req(
            "POST", f"/issues/{urllib.parse.quote(src_id, safe='')}/links",
            {
                "type": link_type,
                "target": dst_id,
                "target_version": target["version"],
            },
            version=source["version"],
            idempotent=True,
        )

    def migration_link_issue(
        self, project: Project, src_id: str, link_type: str, dst_id: str,
    ) -> None:
        """Link two issues of a staged migration target (DHN-35, PAT-314).

        Refused on an archived project (``registry.require_writable_project``).  A
        replay is a no-op: the link is not posted again when the source already
        carries it, in either stored direction.
        """
        project = self._require_project(project)
        registry.require_writable_project(self.name, project)
        if link_type not in _LINK_TYPES:
            raise ValueError("type de lien Tracker non supporté par DevHub")
        self._require_issue_binding(src_id, project)
        self._require_issue_binding(dst_id, project)
        source = self._to_issue(self._issue_raw(src_id))
        if any(link.type == link_type and link.target == dst_id for link in source.links):
            return
        self.link(src_id, link_type, dst_id, project)

    def add_comment(
        self, issue_id: str, text: str, project: Project | None = None,
    ) -> None:
        raw = self._bound_issue_raw(issue_id, project)
        self._application_req(
            "POST", f"/issues/{urllib.parse.quote(issue_id, safe='')}/comments",
            {"text": text}, version=raw["version"], idempotent=True,
        )

    def list_project_catalog(self, query: str = "") -> list[dict]:
        """Read the bounded catalog without treating it as a repository binding.

        The catalog intentionally omits ``canonical_repo``.  It is suitable for
        discovery only; mutations still resolve the registered canonical binding
        through the separate application-V1 endpoint.
        """
        if not isinstance(query, str) or len(query) > 100 or any(
            ord(character) <= 0x1f or ord(character) == 0x7f
            for character in query
        ):
            raise ValueError("requête catalogue DevHubTracker invalide")
        items, cursor, seen = [], None, set()
        for _page_number in range(1, _MAX_CATALOG_PAGES + 1):
            params = {"query": query.strip(), "limit": 100}
            if cursor is not None:
                params["cursor"] = cursor
            raw = self._catalog_req("GET", "/projects?" + urllib.parse.urlencode(params))
            if (not isinstance(raw, dict) or raw.get("schema_version") != _PROJECT_CATALOG_CONTRACT
                    or not isinstance(raw.get("items"), list) or not isinstance(raw.get("page"), dict)):
                raise DevHubTrackerError("GET", "/projects", None, "invalid_response")
            page = raw["page"]
            if (type(page.get("truncation")) is not bool
                    or type(page.get("returned_count")) is not int
                    or page["returned_count"] != len(raw["items"])):
                raise DevHubTrackerError("GET", "/projects", None, "invalid_response")
            for item in raw["items"]:
                if (not isinstance(item, dict) or set(item) != {
                        "id", "key", "title", "state", "freshness_at",
                    } or not isinstance(item.get("id"), str)
                        or not re.fullmatch(r"[1-9][0-9]*", item["id"])
                        or not isinstance(item.get("key"), str)
                        or not re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", item["key"])
                        or not isinstance(item.get("title"), str)
                        or not item["title"]
                        or item.get("state") != "active"
                        or (item.get("freshness_at") is not None
                            and (type(item["freshness_at"]) is not int or item["freshness_at"] < 0))
                        or item["id"] in seen):
                    raise DevHubTrackerError("GET", "/projects", None, "invalid_response")
                seen.add(item["id"])
                items.append(dict(item))
                if len(items) > _MAX_CATALOG_ITEMS:
                    raise DevHubTrackerError("GET", "/projects", None, "pagination_limit")
            next_cursor = page.get("cursor")
            if not page["truncation"]:
                if next_cursor is not None:
                    raise DevHubTrackerError("GET", "/projects", None, "invalid_response")
                return items
            if not raw["items"]:
                raise DevHubTrackerError("GET", "/projects", None, "pagination_empty_page")
            if (not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor
                    or len(next_cursor) > 512):
                raise DevHubTrackerError("GET", "/projects", None, "pagination_stalled")
            cursor = next_cursor
        raise DevHubTrackerError("GET", "/projects", None, "pagination_page_limit")

    def _list_adr_items(self, project: Project) -> list[dict]:
        project = self._require_project(project)
        items, cursor, seen = [], 0, set()
        for _page_number in range(1, _MAX_ADR_PAGES + 1):
            params = urllib.parse.urlencode({"cursor": cursor, "limit": 100})
            raw = self._application_req(
                "GET", f"/projects/{urllib.parse.quote(project.key, safe='')}/adrs?{params}",
            )
            if (not isinstance(raw, dict) or not isinstance(raw.get("items"), list)
                    or not isinstance(raw.get("page"), dict)):
                raise DevHubTrackerError("GET", "/projects/adrs", None, "invalid_response")
            page = raw["page"]
            if (page.get("schema_version") != _APPLICATION_CONTRACT
                    or type(page.get("truncation")) is not bool
                    or type(page.get("returned_count")) is not int
                    or page["returned_count"] != len(raw["items"])):
                raise DevHubTrackerError("GET", "/projects/adrs", None, "invalid_response")
            for item in raw["items"]:
                adr = self._to_adr(item)
                if adr.id in seen:
                    raise DevHubTrackerError("GET", "/projects/adrs", None, "pagination_repeated")
                seen.add(adr.id)
                items.append(dict(item))
                if len(items) > _MAX_ADR_ITEMS:
                    raise DevHubTrackerError("GET", "/projects/adrs", None, "pagination_limit")
            if not page["truncation"]:
                if page.get("cursor") is not None:
                    raise DevHubTrackerError("GET", "/projects/adrs", None, "invalid_response")
                return items
            if not raw["items"]:
                raise DevHubTrackerError("GET", "/projects/adrs", None, "pagination_empty_page")
            next_cursor = page.get("cursor")
            if type(next_cursor) is not int or next_cursor <= cursor:
                raise DevHubTrackerError("GET", "/projects/adrs", None, "pagination_stalled")
            cursor = next_cursor
        raise DevHubTrackerError("GET", "/projects/adrs", None, "pagination_page_limit")

    def list_adrs(self, project: Project) -> list[Adr]:
        """Return full ADRs, preserving the Tracker port's retrieval contract."""
        return [self._to_adr(self._application_req(
            "GET", f"/adrs/{urllib.parse.quote(item['id'], safe='')}",
        )) for item in self._list_adr_items(project)]

    def create_adr(self, project, title, body, status="proposed") -> Adr:
        project = self._require_project(project)
        if status != "proposed":
            raise ValueError("une ADR DevHub doit être créée au statut proposed")
        raw = self._application_req(
            "POST", f"/projects/{urllib.parse.quote(project.key, safe='')}/adrs",
            {"title": title, "body": body, "status": status}, idempotent=True,
        )
        return self._to_adr(raw)

    @staticmethod
    def _audit_resource_belongs_to(project: Project, resource: str) -> bool:
        return bool(
            resource == f"project:{project.key}"
            or re.fullmatch(rf"issue:{re.escape(project.key)}-[0-9]+", resource)
            or re.fullmatch(rf"adr:{re.escape(project.key)}-ADR-[0-9]{{4,}}", resource)
        )

    def audit(
        self, project: Project, *, operation: str | None = None,
        resource: str | None = None,
    ) -> list[dict]:
        """Read the bounded, sanitized public audit projection for one project."""
        project = self._require_project(project)
        if operation is not None and operation not in _AUDIT_OPERATIONS:
            raise ValueError("opération audit DevHubTracker invalide")
        if resource is not None and not self._audit_resource_belongs_to(project, resource):
            raise ValueError("ressource audit DevHubTracker hors projet")
        events, cursor, seen = [], "0", set()
        for _page_number in range(1, _MAX_AUDIT_PAGES + 1):
            params = {"cursor": cursor, "limit": 100}
            if operation is not None:
                params["operation"] = operation
            if resource is not None:
                params["resource"] = resource
            path = (
                f"/projects/{urllib.parse.quote(project.key, safe='')}/audit?"
                f"{urllib.parse.urlencode(params)}"
            )
            raw = self._application_req("GET", path)
            if (not isinstance(raw, dict) or not isinstance(raw.get("items"), list)
                    or not isinstance(raw.get("page"), dict)):
                raise DevHubTrackerError("GET", "/projects/audit", None, "invalid_response")
            page = raw["page"]
            if (page.get("schema_version") != _APPLICATION_CONTRACT
                    or type(page.get("truncation")) is not bool
                    or type(page.get("returned_count")) is not int
                    or page["returned_count"] != len(raw["items"])):
                raise DevHubTrackerError("GET", "/projects/audit", None, "invalid_response")
            for event in raw["items"]:
                if (not isinstance(event, dict) or set(event) != _AUDIT_KEYS
                        or not isinstance(event.get("id"), str)
                        or not re.fullmatch(r"[1-9][0-9]*", event["id"])
                        or event["id"] in seen
                        or event.get("operation") not in _AUDIT_OPERATIONS
                        or (operation is not None and event["operation"] != operation)
                        or not isinstance(event.get("resource"), str)
                        or not self._audit_resource_belongs_to(project, event["resource"])
                        or (resource is not None and event["resource"] != resource)
                        or (event.get("resource_version") is not None
                            and (type(event["resource_version"]) is not int
                                 or event["resource_version"] < 1))
                        or event.get("result") not in _AUDIT_RESULTS
                        or type(event.get("created")) is not int
                        or event["created"] < 0):
                    raise DevHubTrackerError(
                        "GET", "/projects/audit", None, "invalid_response",
                    )
                seen.add(event["id"])
                events.append(dict(event))
                if len(events) > _MAX_AUDIT_ITEMS:
                    raise DevHubTrackerError(
                        "GET", "/projects/audit", None, "pagination_limit",
                    )
            if not page["truncation"]:
                return events
            if not raw["items"]:
                raise DevHubTrackerError(
                    "GET", "/projects/audit", None, "pagination_empty_page",
                )
            next_cursor = page.get("cursor")
            if (not isinstance(next_cursor, str)
                    or not re.fullmatch(r"[1-9][0-9]*", next_cursor)
                    or int(next_cursor) <= int(cursor)):
                raise DevHubTrackerError(
                    "GET", "/projects/audit", None, "pagination_stalled",
                )
            cursor = next_cursor
        raise DevHubTrackerError("GET", "/projects/audit", None, "pagination_page_limit")

    def set_adr_status(
        self, adr: Adr, status: str, project: Project | None = None,
    ) -> None:
        if status not in _ADR_STATES - {"proposed"}:
            raise ValueError("statut ADR non supporté par DevHub")
        project_key = self._require_adr_binding(adr.id, project)
        raw = next(
            (item for item in self._list_adr_items(Project(key=project_key, id="bound"))
             if item.get("id") == adr.id),
            None,
        )
        if not isinstance(raw, dict) or not isinstance(raw.get("version"), int):
            raise DevHubTrackerError("GET", "/projects/adrs", None, "invalid_response")
        proof = self._proof(action="adr-status", project_key=project_key, resource_id=adr.id,
                            version=raw["version"], target_status=status)
        self._application_req(
            "POST", f"/adrs/{urllib.parse.quote(adr.id, safe='')}/status",
            {"status": status}, version=raw["version"], proof=proof, idempotent=True,
        )

    def _read_adr_document(self, adr_id: str) -> dict:
        """Read one ADR with its `linked_issue_ids` (`devhub-adr-authoring.v1`)."""
        path = f"/adr-authoring/adrs/{urllib.parse.quote(adr_id, safe='')}"
        raw = self._req("GET", path, response_contract=_ADR_AUTHORING_CONTRACT)
        if (not isinstance(raw, dict)
                or raw.get("schema_version") != _ADR_AUTHORING_CONTRACT
                or not isinstance(raw.get("adr"), dict)
                or not isinstance(raw.get("linked_issue_ids"), list)
                or not all(isinstance(item, str) for item in raw["linked_issue_ids"])):
            raise DevHubTrackerError("GET", path, None, "invalid_response")
        self._to_adr(raw["adr"])
        return raw

    def link_adr_issue(
        self, adr: Adr, issue_ref: str, project: Project | None = None,
    ) -> Adr:
        """Add one issue to a proposed ADR through the ADR edit route (PAT-312, DHN-33).

        Only a proposed ADR takes the relation. A decided one is frozen by the server
        (403 `authority_boundary`, `2edcea2:src/tracker/store.ts:1966`): the refusal is
        typed, never a silent no-op, and the relation must be set before acceptance.
        """
        project_key = self._require_adr_binding(adr.id, project)
        if (not isinstance(issue_ref, str) or _ISSUE_ID.fullmatch(issue_ref) is None
                or self._project_key(issue_ref) != project_key):
            raise ValueError("référence d'issue ADR DevHub invalide")
        document = self._read_adr_document(adr.id)
        current = self._to_adr(document["adr"])
        if current.status != "proposed":
            raise AdrRelationFrozenError(adr.id, current.status)
        linked = list(document["linked_issue_ids"])
        if issue_ref in linked:
            return current
        path = f"/adr-authoring/adrs/{urllib.parse.quote(adr.id, safe='')}"
        try:
            raw = self._req(
                "PATCH", path,
                {
                    "schema_version": _ADR_AUTHORING_CONTRACT,
                    "title": current.title, "body": current.body,
                    "linked_issue_ids": [*linked, issue_ref],
                },
                version=document["adr"]["version"], idempotent=True,
                response_contract=_ADR_AUTHORING_CONTRACT,
            )
        except DevHubTrackerError as exc:
            if exc.status == 403 and exc.code == "authority_boundary":
                raise AdrRelationFrozenError(adr.id, "decided") from None
            if exc.status == 409 and exc.code == "version_conflict":
                raise TrackerConflictError(
                    "version ADR DevHub modifiée ; recharge l'ADR puis relance"
                ) from None
            raise
        if (not isinstance(raw, dict) or not isinstance(raw.get("adr"), dict)
                or issue_ref not in (raw.get("linked_issue_ids") or [])):
            raise DevHubTrackerError("PATCH", path, None, "invalid_response")
        return self._to_adr(raw["adr"])

    def read_adr_issue_relations(
        self, adr: Adr, project: Project | None = None,
    ) -> list[str]:
        """Return the issue ids related to an ADR, also once it is decided (DHN-33).

        Reads `GET /adrs/:id/relationships` (`devhub-adr-relationships.v1`,
        `2edcea2:src/app/api/tracker/v1/[...path]/route.ts:889`) page by page.
        """
        self._require_adr_binding(adr.id, project)
        base = f"/adrs/{urllib.parse.quote(adr.id, safe='')}/relationships"
        cursor, ids = 0, []
        for _page_number in range(1, _MAX_ADR_PAGES + 1):
            path = f"{base}?{urllib.parse.urlencode({'cursor': cursor, 'limit': 100})}"
            raw = self._req("GET", path, response_contract=_ADR_RELATIONSHIPS_CONTRACT)
            if (not isinstance(raw, dict)
                    or raw.get("schema_version") != _ADR_RELATIONSHIPS_CONTRACT
                    or not isinstance(raw.get("items"), list)
                    or not isinstance(raw.get("page"), dict)
                    or not all(isinstance(i, dict) and isinstance(i.get("id"), str)
                               for i in raw["items"])):
                raise DevHubTrackerError("GET", base, None, "invalid_response")
            ids.extend(item["id"] for item in raw["items"])
            if not raw["page"].get("truncation"):
                return ids
            next_cursor = raw["page"].get("cursor")
            if (type(next_cursor) is not int or next_cursor <= cursor or not raw["items"]
                    or len(ids) > _MAX_ADR_ITEMS):
                raise DevHubTrackerError("GET", base, None, "pagination_stalled")
            cursor = next_cursor
        raise DevHubTrackerError("GET", base, None, "pagination_page_limit")
