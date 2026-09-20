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
    TransitionContext,
)
from foundry.trackers.base import IssueUnavailableError, Tracker, TrackerConflictError


_CONTRACT = "devhub-tracker.v1"
_APPLICATION_CONTRACT = "devhub-application.v1"
_PROJECT_CATALOG_CONTRACT = "devhub-project-catalog.v1"
_EPIC_CLOSURE_CONTRACT = "devhub-epic-closure.v1"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_SEARCH_ITEMS = 1_000
_MAX_SEARCH_PAGES = 100
_MAX_AUDIT_ITEMS = 1_000
_MAX_AUDIT_PAGES = 100
_MAX_CATALOG_ITEMS = 1_000
_MAX_CATALOG_PAGES = 100
_MAX_ADR_ITEMS = 1_000
_MAX_ADR_PAGES = 100
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
    project_provisioning_supported = True
    project_provisioning_requires_repository = True
    epic_subgraph_supported = True

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
