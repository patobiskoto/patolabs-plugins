"""DevHubTracker v1 adapter for Foundry's normalized Tracker port."""
from __future__ import annotations

import base64
import contextlib
import copy
import dataclasses
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

from foundry import acceptance_decision as _acceptance_decision
from foundry import config, registry
from foundry.models import (
    AC_OVERRIDE_REASON,
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
    _UNSPECIFIED_ADR_RELATION,
    BodyUpdateUnavailableError,
    IssueUnavailableError,
    ReleaseScopeUnavailableError,
    Tracker,
    TrackerCapabilityUnavailableError,
    TrackerConflictError,
)


_CONTRACT = "devhub-tracker.v1"
_APPLICATION_CONTRACT = "devhub-application.v1"
_PROJECT_CATALOG_CONTRACT = "devhub-project-catalog.v1"
_EPIC_CLOSURE_CONTRACT = "devhub-epic-closure.v1"
_ADR_AUTHORING_CONTRACT = "devhub-adr-authoring.v1"
_ADR_RELATIONSHIPS_CONTRACT = "devhub-adr-relationships.v1"
_ADR_DECISIONS_CONTRACT = "devhub-adr-decisions.v1"
_GOVERNANCE_CONTRACT = "devhub-governance.v1"
_WORKFLOW_CONTRACT = "devhub-workflow.v1"
_WORKFLOW_API_PREFIX = "/api/tracker/workflow/v1"
_GOVERNANCE_MODES = frozenset({"manual", "foundry"})
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
# Historical imports and reads have a wider vocabulary than native decisions.
_ADR_IMPORT_STATES = _ADR_STATES | {"rejected"}
_IMPORT_ISSUE_NUMBER_MAX = 999999
_AUDIT_OPERATIONS = {
    "project.create", "issue.create", "issue.update", "issue.transition",
    "issue.link", "issue.comment", "adr.create", "adr.status",
}
_AUDIT_RESULTS = {"success", "refused", "unavailable"}
# PAT-309 / PAT-318: shape of one `GET /issues/:id/proof-receipts` item (DHN-38).  The
# extended form (DEVHUB-106, DevHub merge `f347f38`) adds
# `reason`, `decision_digest` and `decision`, null where the action does not use them.
_PROOF_RECEIPT_KEYS = {
    "id", "issue", "project", "action", "issue_version", "pr_url", "head_sha",
    "base_sha", "review_digest", "merge_sha", "proof_nonce", "actor", "created",
    "reason", "decision_digest", "decision",
}
_PROOF_RECEIPT_ACTIONS = {"review", "done", "acceptance_override", "human_acceptance"}
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
# PAT-319 / DHN-34, DHN-36: origin coordinates of DEVHUB-105 (`sourceRefSchema`,
# `foreignIdSchema`, `src/tracker/contract.ts:94-96` at `b0231d9`): no whitespace, no
# control character (C0, DEL, C1); lengths are counted in UTF-16 units like the server.
# PAT-324: limits of a created issue and of an imported ADR (`createIssueSchema`,
# `importAdrSchema`, `issueFieldsSchema` of `src/tracker/contract.ts` at `12ca158`).
_IMPORT_TITLE_MAX = 300
_IMPORT_BODY_MAX = 200_000
_IMPORT_TYPE_MAX = 80
_IMPORT_PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})
_IMPORT_ESTIMATES = (1, 21)
_IMPORT_CREATION_STATES = ("backlog", "ready")
# DEVHUB-126 (``IMPORTED_TERMINAL_STATES``): the states of the terminal issue import, and
# the three optional source dates it stores (epoch ms).
_IMPORT_TERMINAL_STATES = ("done", "dropped")
_IMPORT_PR_URL_MAX = 500
_IMPORT_DATES = ("source_created", "source_updated", "source_completed")
# DEVHUB-128 (``importCommentSchema``, ``MAX_COMMENT_TEXT`` of ``contract.ts``, 200000 since
# DEVHUB-131 at ``72a24e2``, 50000 before): the text is 1 to 200000 UTF-16 units with a non-blank character, no NUL, no unpaired
# surrogate; the author is 1 to 200 units, not blank, no control character
# (``\p{Cc}``) and no unpaired surrogate; the source date is epoch ms in ``_IMPORT_EPOCH``.
_IMPORT_COMMENT_MAX = 200_000
_IMPORT_AUTHOR_MAX = 200
_IMPORT_EPOCH_MAX = 253_402_300_799_999  # 9999-12-31T23:59:59.999Z, DevHub 890e338 (DEVHUB-130)
_COMMENT_SOURCE_KEYS = ("source_ref", "source_author", "source_created")
_COMMENT_TEXT_FORBIDDEN = re.compile(r"[\x00\ud800-\udfff]")
_COMMENT_AUTHOR_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff]")
# ECMAScript ``String.prototype.trim`` strips exactly these (the server's blank test).
_JS_WHITESPACE = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008"
    "\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_ADR_LINK_MAX = 50
# DEVHUB-122: an unpaired surrogate is refused too (a Python ``str`` holds no paired one).
_ORIGIN_TEXT = re.compile(r"[^\s\x00-\x1f\x7f-\x9f\ud800-\udfff]+")
_SOURCE_REF_MAX = 200
_FOREIGN_ID_MAX = 120
# DEVHUB-122 (``MAX_ADR_SOURCE_ONLY_REFS``, ``ADR_RELATION_FAMILIES``, ``contract.ts`` of ``3f0cf4b``).
_ADR_SOURCE_ONLY_MAX = 200
_ADR_UNKNOWN_FAMILIES = ("issues", "supersedes", "superseded_by")
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


def _issue_order(issue_id: str) -> tuple[str, int]:
    """Sort key of readable issue ids: project key, then the number as a number (PAT-324)."""
    key, _separator, number = issue_id.rpartition("-")
    return (key, int(number)) if number.isdigit() else (issue_id, -1)


def _utf16_length(value: str) -> int:
    """Length in UTF-16 units, the unit of the server's string limits (PAT-324)."""
    return len(value.encode("utf-16-le")) // 2


def _has_cycle(edges: dict[str, set[str]]) -> bool:
    """True when the directed graph ``edges`` (node to successors) holds a cycle."""
    done: set[str] = set()
    for root in edges:
        if root in done:
            continue
        path = {root}
        stack = [(root, iter(sorted(edges.get(root, ()))))]
        while stack:
            node, successors = stack[-1]
            for successor in successors:
                if successor in path:
                    return True
                if successor not in done:
                    path.add(successor)
                    stack.append((successor, iter(sorted(edges.get(successor, ())))))
                    break
            else:
                path.discard(node)
                done.add(node)
                stack.pop()
    return False


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


class DevHubGovernanceModeError(TrackerConflictError):
    """The DevHub project is not under Foundry governance (PAT-321).

    DevHub refuses Foundry writes on such a project (`assertFoundryGovernance`); the
    refusal names the governance mode read from ``GET /governance/projects/:key``.
    """

    def __init__(self, project_key: str, mode: str):
        super().__init__(
            f"projet DevHub {project_key} en mode de gouvernance '{mode}' : DevHub refuse "
            "les écritures Foundry hors gouvernance Foundry ; repasse le projet en mode "
            "'foundry' dans DevHub ou n'y lie pas ce dépôt"
        )
        self.project_key = project_key
        self.mode = mode


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
    # PAT-318 / DHN-25: `project_acceptance_override` appends the typed
    # `acceptance_override` receipt through the DEVHUB-106 route (announced by DevHub on
    # 2026-10-20, merged as `f347f38`) before the merge.
    acceptance_override_projection_supported = True
    # PAT-330 / PAT-ADR-0033: `issue accept` and `--status` run on DevHub with the
    # PAT-ADR-0025 conditions; `accept_issue` is the port operation, the typed
    # `human_acceptance` receipt the proof, `observe_human_acceptance` the status read.
    human_acceptance_supported = True
    human_acceptance_source = "devhub-human-acceptance"
    # PAT-331: an issue imported as terminal (done or dropped in the source tracker); its
    # proof lives in the source tracker, so this source is never a DevHub receipt.
    imported_terminal_acceptance_source = "devhub-imported-terminal"
    human_acceptance_predecessors = _acceptance_decision.FROM_STATES
    # PAT-324: DevHub can be the target of a selective cutover (FOUNDRY-ADR-0026).  The four
    # field attributes are sent by ``migration_import_issue`` (values it cannot reproduce are
    # named by ``migration_attribute_exceptions``); the three graph attributes are written by
    # ``migration_link_issue`` and checked as a whole by ``migration_preflight``.
    migration_supported_attributes = frozenset({
        "type", "priority", "estimate", "state", "parent", "children", "dependencies",
    })
    # PAT-331: terminal issues are imported by ``POST /projects/:key/issues/import``
    # (DEVHUB-126), linked by ``migration_link_issue`` like a living issue (DEVHUB-127, stage
    # 2) and their comments, lifecycle receipts included, as those of a living issue are
    # imported by ``POST /issues/:id/comments/import`` (DEVHUB-128, stage 3): nothing is
    # pending any more, so ``migration.ready_for_cutover`` no longer waits on this map.
    migration_terminal_import_supported = True
    migration_comments_supported = True
    migration_epic_only_parent = True  # createTrackerLink: "parent must be an Epic" (PAT-332)
    migration_pending_terminal_items: dict[str, str] = {}

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
        api_prefix="/api/tracker/v1",
    ):
        validate_base_url(self.url)
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode() if body is not None else None
        req = urllib.request.Request(f"{self.url}{api_prefix}{path}", data=data, method=method)
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
            comment = {"text": item["text"], "created": item["created"]}
            imported = [key for key in _COMMENT_SOURCE_KEYS if key in item]
            if imported:
                # DEVHUB-128: the three members come together on an imported comment only.
                if (len(imported) != 3 or not DevHubTracker._origin_text(item["source_ref"], _SOURCE_REF_MAX)
                        or not isinstance(item["source_author"], str)
                        or type(item["source_created"]) is not int
                        or item["source_created"] < 0):
                    raise DevHubTrackerError("GET", "/issues", None, "invalid_response")
                comment.update({key: item[key] for key in _COMMENT_SOURCE_KEYS})
            comments.append(comment)
        history = None
        if raw.get("imported_terminal") is True:
            # DEVHUB-126: the three source dates of an imported terminal issue, null when
            # not sent; an invalid date is refused like any other malformed read.
            history = {key: raw.get(key) for key in _IMPORT_DATES}
            if any(value is not None and (type(value) is not int or value < 0)
                   for value in history.values()):
                raise DevHubTrackerError("GET", "/issues", None, "invalid_response")
        elif raw.get("imported_terminal") not in (None, False):
            raise DevHubTrackerError("GET", "/issues", None, "invalid_response")
        return Issue(
            id=raw["id"], title=raw.get("title", ""), state=raw.get("state"),
            priority=raw.get("priority"), estimate=raw.get("estimate"),
            milestone=raw.get("milestone"), type=raw.get("type"),
            labels=list(raw.get("labels") or []), ac_done=int(raw.get("ac_done") or 0),
            ac_total=int(raw.get("ac_total") or 0), links=links,
            pr_url=raw.get("pr_url"), body=raw.get("body"), comments=comments,
            created=raw.get("created"), updated=raw.get("updated"),
            version=raw["version"], import_history=history,
        )

    @staticmethod
    def _to_adr(raw) -> Adr:
        if (not isinstance(raw, dict) or not isinstance(raw.get("id"), str)
                or not isinstance(raw.get("title"), str)
                or raw.get("status") not in _ADR_IMPORT_STATES
                or type(raw.get("version")) is not int or raw["version"] < 1):
            raise DevHubTrackerError("GET", "/adrs", None, "invalid_response")
        # DEVHUB-105 keys are additive: absent on an older server, null on a normal ADR.
        for key in ("source_ref", "foreign_id"):
            if raw.get(key) is not None and not isinstance(raw[key], str):
                raise DevHubTrackerError("GET", "/adrs", None, "invalid_response")
        for key in ("source_created", "source_updated"):
            if raw.get(key) is not None and (
                    type(raw[key]) is not int or raw[key] < 0):
                raise DevHubTrackerError("GET", "/adrs", None, "invalid_response")
        foreign_id = raw.get("foreign_id")
        if foreign_id is not None and not DevHubTracker._origin_text(
                foreign_id, _FOREIGN_ID_MAX):
            raise DevHubTrackerError("GET", "/adrs", None, "invalid_response")
        # The foreign id is the port identity of an imported ADR (PAT-319, DHN-36);
        # the server id `<KEY>-ADR-NNNN` stays the route identity.
        return Adr(id=foreign_id or raw["id"], title=raw.get("title", ""),
                   status=raw.get("status", "proposed"),
                   body=raw.get("body"), ref=raw.get("ref"))

    @staticmethod
    def _origin_text(value, limit: int) -> bool:
        return (isinstance(value, str) and _ORIGIN_TEXT.fullmatch(value) is not None
                and _utf16_length(value) <= limit)

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

    @classmethod
    def _server_adr_key(cls, adr_id: str) -> str | None:
        """Project key of a server-supplied ADR id; a malformed id is ``None``, never raised."""
        try:
            return cls._adr_project_key(adr_id)
        except ValueError:
            return None

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

    def _proof(
        self, *, action, project_key, resource_id, version, context=None,
        target_status=None, extra=None,
    ):
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
        if extra:
            payload.update(extra)
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
        return self._read_back_registered(registry.resolve(self.name, repo), repo)

    def _read_back_registered(self, registered: Project, repo: str) -> Project:
        """Re-read a registered project on the server and refuse any contradiction."""
        self._preserve_issue_numbers(registered)  # validate the local-only option before transport
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
        # Number preservation is local intent: the server cannot activate it or override it.
        extra = {key: value for key, value in remote.extra.items() if key != "preserve_issue_numbers"}
        # `release_ids` keeps its existing local-map fallback; other metadata stays remote.
        for name in ("release_ids", "preserve_issue_numbers"):
            if name in registered.extra and name not in extra:
                extra[name] = registered.extra[name]
        return Project(key=remote.key, id=remote.id, extra=extra)

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
        if not (remote.key == project.key and remote.id == project.id
                and remote_repository == canonical):
            return False
        # Identity holds: refuse a project DevHub does not govern for Foundry (PAT-321).
        mode = self._governance_mode(project.key)
        if mode is not None and mode != "foundry":
            raise DevHubGovernanceModeError(project.key, mode)
        return True

    def _governance_mode(self, project_key: str) -> str | None:
        """Read the governance mode (`GET /governance/projects/:key`, `devhub-governance.v1`).

        ``None`` when an older server answers 404 `route_unavailable` (mode unknown, no
        refusal); a malformed answer is `invalid_response`.
        """
        path = f"/governance/projects/{urllib.parse.quote(project_key, safe='')}"
        try:
            raw = self._req("GET", path, response_contract=_GOVERNANCE_CONTRACT)
        except DevHubTrackerError as error:
            if error.status == 404 and error.code == "route_unavailable":
                return None
            raise
        if (not isinstance(raw, dict)
                or raw.get("schema_version") != _GOVERNANCE_CONTRACT
                or raw.get("project") != project_key
                or raw.get("mode") not in _GOVERNANCE_MODES):
            raise DevHubTrackerError("GET", "/governance/projects", None, "invalid_response")
        return raw["mode"]

    @contextlib.contextmanager
    def _governance_guard(self, project_key: str):
        """Name a governance refusal behind a 403 `authority_boundary` on a write (PAT-321).

        The code is shared with other authority refusals: re-read the mode once and raise
        the typed error only when the project is not under Foundry governance; otherwise
        (or when that read fails) the original error propagates.
        """
        try:
            yield
        except DevHubTrackerError as error:
            if error.status == 403 and error.code == "authority_boundary":
                try:
                    mode = self._governance_mode(project_key)
                except DevHubTrackerError:
                    mode = None
                if mode is not None and mode != "foundry":
                    raise DevHubGovernanceModeError(project_key, mode) from None
            raise

    def resolve_checkout_project(
        self,
        cwd: str | None = None,
        *,
        checkout_identity: str | None = None,
    ) -> Project:
        """Resolve the checkout through its V1 binding, then re-read it on the server (PAT-323)."""
        registered = super().resolve_checkout_project(
            cwd, checkout_identity=checkout_identity,
        )
        return self._read_back_registered(
            registered, registry.repo_basename(cwd, use_env=False),
        )

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
        with self._governance_guard(project.key):
            closed = self._epic_closure_req(
                "POST", path, request, version=receipt.parent_version,
                idempotent=True, idempotency_key=idempotency_key,
            )
        outcome = self._to_epic_closure_outcome(
            closed,
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
            # An override receipt belongs to the review generation it follows (PAT-318):
            # it does not make that generation a different one.
            reviews = [item for item in receipts if item["action"] != "acceptance_override"]
            if reviews and self._same_review(reviews[-1], context):
                return
            with self._governance_guard(project_key):
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
        with self._governance_guard(project_key):
            self._application_req(
                "POST", f"/issues/{urllib.parse.quote(issue_id, safe='')}/state",
                {"state": state}, version=raw["version"], proof=proof, idempotent=True,
            )

    @staticmethod
    def _stale_error(exc: DevHubTrackerError):
        if exc.status == 409 and exc.code == "version_conflict":
            return TrackerConflictError(
                "version DevHub modifiée ; recharge l'issue puis relance l'écriture"
            )
        return None

    @staticmethod
    def _route_unavailable(exc: DevHubTrackerError, capability: str):
        # A server older than DEVHUB-106 answers 404 `route_unavailable` (DevHub reply
        # of 2026-10-20); nothing was written.
        if exc.status == 404 and exc.code == "route_unavailable":
            return TrackerCapabilityUnavailableError("devhub", capability)
        return None

    def project_acceptance_override(
        self, issue_id: str, reason: str, context: TransitionContext,
        project: Project | None = None,
    ) -> bool:
        """Append the typed `acceptance_override` receipt before the merge (DHN-25, PAT-318).

        Route and contract: ``POST /issues/:id/acceptance-override``, announced by DevHub
        on 2026-10-20 (DEVHUB-106, merged as `f347f38`); see ``docs/devhub-tracker.md``.  The
        issue must be in `review` and the coordinates must equal the latest review
        receipt.  Returns ``True`` when it wrote, ``False`` when the same override already
        follows that review (exact replay); another reason on the same generation raises
        ``TrackerConflictError``.  The receipt is read back.
        """
        if (not isinstance(reason, str) or AC_OVERRIDE_REASON.match(reason) is None
                or not isinstance(context, TransitionContext)
                or context.merge_sha is not None
                or not all(getattr(context, key) for key in _RECEIPT_COORDINATES)):
            raise ValueError("override AC DevHub invalide")
        project_key = self._require_issue_binding(issue_id, project)
        raw = self._bound_issue_raw(issue_id, project)
        if raw.get("state") != "review":
            raise TrackerConflictError("override AC DevHub : l'issue n'est pas en review")
        receipts = self.read_proof_receipts(issue_id, project=project)
        review_at = max(
            (index for index, item in enumerate(receipts) if item["action"] == "review"),
            default=None,
        )
        if review_at is None or not self._same_review(receipts[review_at], context):
            raise TrackerConflictError(
                "override AC DevHub : les coordonnées ne sont pas celles de la dernière review"
            )
        if any(item["action"] == "done" for item in receipts[review_at + 1:]):
            raise TrackerConflictError("override AC DevHub : génération de review déjà close")
        existing = [item for item in receipts[review_at + 1:]
                    if item["action"] == "acceptance_override"]
        if existing:
            if existing[0]["reason"] != reason:
                raise TrackerConflictError(
                    "override AC DevHub : un autre motif est déjà enregistré pour cette review"
                )
            return False
        body = {"reason": reason, **{key: getattr(context, key) for key in _RECEIPT_COORDINATES}}
        proof = self._proof(
            action="acceptance-override", project_key=project_key, resource_id=issue_id,
            version=raw["version"], context=context, extra={"reason": reason},
        )
        try:
            with self._governance_guard(project_key):
                self._application_req(
                    "POST", f"/issues/{urllib.parse.quote(issue_id, safe='')}/acceptance-override",
                    body, version=raw["version"], proof=proof, idempotent=True,
                )
        except DevHubTrackerError as exc:
            unavailable = self._route_unavailable(exc, "acceptance_override")
            if unavailable is not None:
                raise unavailable from None
            conflict = self._stale_error(exc)
            if conflict is not None:
                raise conflict from None
            raise
        after = self.read_proof_receipts(issue_id, project=project)
        if not (len(after) == len(receipts) + 1 and after[:-1] == receipts
                and after[-1]["action"] == "acceptance_override"
                and after[-1]["reason"] == reason
                and all(after[-1][key] == getattr(context, key) for key in _RECEIPT_COORDINATES)):
            raise TrackerConflictError("override AC DevHub : relecture du reçu divergente")
        return True

    def recover_acceptance_override(
        self, issue_id: str, reason: str, *, pr_url: str, head_sha: str, base_sha: str,
        merge_sha: str, project: Project | None = None,
    ) -> bool:
        """DevHub has no recovery: the route needs the `review` state (PAT-318)."""
        del issue_id, reason, pr_url, head_sha, base_sha, merge_sha, project
        raise TrackerConflictError(
            "DevHub n'a pas de reprise d'override AC : le reçu s'écrit avant la fusion"
        )

    def accept_issue(
        self, issue_id: str, decision: dict, *, project: Project | None = None,
        verify_evidence=None,
    ) -> Issue:
        """Accept a non-code issue by one closed human decision (DHN-26, PAT-ADR-0025).

        Route and contract: ``POST /issues/:id/human-acceptance``, announced by DevHub on
        2026-10-20 (DEVHUB-106, merged as `f347f38`); see ``docs/devhub-tracker.md``.  Local
        validation, then a fresh read and every refusal, then the cited-evidence check,
        then one POST (done, version + 1 and the receipt in one server transaction), then
        a read-back that recomputes the digest.  An exact replay converges without a
        second POST while the issue is ``done`` and its one decision receipt still matches
        the body (``_valid_human_decision``); a different decision is refused.  No
        automatic retry.
        """
        from foundry import acceptance_decision as decisions

        def refuse(why: str):
            raise TrackerConflictError(f"DevHub acceptance refused: {why}")

        try:
            decisions.validate(decision, issue_id)
        except decisions.AcceptanceDecisionError as exc:
            refuse(str(exc))
        digest = decisions.decision_digest(decision)
        project_key = self._require_issue_binding(issue_id, project)
        raw = self._bound_issue_raw(issue_id, project)
        issue = self._to_issue(raw)
        receipts = self.read_proof_receipts(issue_id, project=project)
        recorded = [item for item in receipts if item["action"] == "human_acceptance"]
        if recorded:
            if recorded[0]["decision_digest"] != digest:
                refuse("a different decision is already recorded for this issue")
            if not isinstance(self._valid_human_decision(issue, receipts), dict):
                refuse("the recorded decision sits next to another receipt, or the body "
                       "or criteria changed after it")
            if issue.state != "done" or recorded[0]["decision"] != decision:
                raise TrackerConflictError("DevHub acceptance read-back divergent")
            return self._with_human_acceptance(issue, digest, raw["state"])
        if raw.get("state") != decision["from_state"]:
            refuse(f"native state is {raw.get('state')}, the decision was made from "
                   f"{decision['from_state']}")
        if receipts:
            refuse("PR receipts present; use `issue merge`")
        if issue.type == "Epic":
            refuse("an Epic is closed by `issue close-epic`")
        if any(link.type == "parent-of" for link in issue.links):
            refuse("the issue has children")
        if issue.pr_url:
            refuse("the issue is linked to a PR; use `issue merge`")
        if issue.ac_total < 1:
            refuse("the issue has no acceptance criterion")
        try:
            decisions.check_against_body(decision, raw.get("body") or "")
            if verify_evidence is not None:
                verify_evidence()
        except decisions.AcceptanceDecisionError as exc:
            refuse(str(exc))
        proof = self._proof(
            action="human-acceptance", project_key=project_key, resource_id=issue_id,
            version=raw["version"], extra={"decision_digest": digest},
        )
        try:
            with self._governance_guard(project_key):
                self._application_req(
                    "POST", f"/issues/{urllib.parse.quote(issue_id, safe='')}/human-acceptance",
                    {"decision": decision}, version=raw["version"], proof=proof, idempotent=True,
                )
        except DevHubTrackerError as exc:
            unavailable = self._route_unavailable(exc, "human_acceptance")
            if unavailable is not None:
                raise unavailable from None
            conflict = self._stale_error(exc)
            if conflict is not None:
                raise conflict from None
            raise
        fresh = self.get_issue(issue_id)
        after = [item for item in self.read_proof_receipts(issue_id, project=project)
                 if item["action"] == "human_acceptance"]
        if (fresh.state != "done" or len(after) != 1
                or after[0]["decision_digest"] != digest or after[0]["decision"] != decision):
            raise TrackerConflictError("DevHub acceptance read-back divergent")
        return self._with_human_acceptance(fresh, digest, fresh.state)

    def _with_human_acceptance(self, issue: Issue, digest: str, native_state: str) -> Issue:
        """Mark an issue read from DevHub as accepted by the receipt of this digest."""
        return dataclasses.replace(
            issue, native_state=native_state, projection_status="aligned",
            acceptance_status="accepted", acceptance_source=self.human_acceptance_source,
            acceptance_coordinates=json.dumps(
                {"decision_digest": digest}, sort_keys=True, separators=(",", ":"),
            ),
        )

    def _with_imported_terminal(self, issue: Issue) -> Issue:
        """Mark an imported terminal issue; ``done`` counts as accepted, ``dropped`` as abandoned."""
        marked = dataclasses.replace(
            issue, native_state=issue.state, projection_status="aligned",
            acceptance_source=self.imported_terminal_acceptance_source,
        )
        if issue.state != "done":
            return marked
        return dataclasses.replace(
            marked, acceptance_status="accepted", ac_done=issue.ac_total,
            acceptance_coordinates=json.dumps(
                {"imported_terminal": True}, sort_keys=True, separators=(",", ":"),
            ),
        )

    def observe_epic_closure_child(self, issue_id: str) -> Issue:
        """Read a direct child for the atomic Epic closure (PAT-330, PAT-ADR-0025).

        ``get_issue`` plus one receipt read, made only for a ``done`` issue without a
        ``pr_url`` whose criteria are not all checked: exactly one ``human_acceptance`` receipt then marks it accepted
        by the decision and counts its criteria as satisfied (the decision passed every
        criterion).  No extra request for any other child.
        """
        issue = self.get_issue(issue_id)
        if issue.import_history is not None and issue.state in {"done", "dropped"}:
            # PAT-331 / PAT-ADR-0032: counted terminal without a new proof; the read of
            # the detail (``imported_terminal``) is the only source of this fact.
            return self._with_imported_terminal(issue)
        if (issue.state != "done" or issue.ac_total < 1 or issue.ac_done == issue.ac_total
                or issue.pr_url):
            # An issue with a PR is never accepted by decision (as in `_release_issue`).
            return issue
        decided = self._valid_human_decision(issue, self.read_proof_receipts(issue_id))
        if not isinstance(decided, dict):
            return issue
        return dataclasses.replace(
            self._with_human_acceptance(issue, decided["decision_digest"], issue.state),
            ac_done=issue.ac_total,
        )

    @staticmethod
    def _valid_human_decision(issue: Issue, receipts: list[dict]) -> dict | str | None:
        """The one decision receipt of an issue, ``None`` if there is none, ``"conflict"``.

        Conflict (as Linear's, ``docs/linear-tracker.md`` "Reading"): two decisions, a
        decision next to a PR-bound receipt (``review``, ``done``, ``acceptance_override``),
        or a body or criteria changed after the decision (PAT-ADR-0025, PAT-ADR-0033).
        """
        recorded = [item for item in receipts if item["action"] == "human_acceptance"]
        if not recorded:
            return None
        if len(recorded) != 1 or len(receipts) != 1:
            return "conflict"
        try:
            _acceptance_decision.check_against_body(recorded[0]["decision"], issue.body or "")
        except _acceptance_decision.AcceptanceDecisionError:
            return "conflict"
        return recorded[0]

    def observe_human_acceptance(self, issue_id: str) -> Issue:
        """Read-only status of `issue accept` (PAT-330): the issue and its receipt.

        ``acceptance_source`` is set only when exactly one ``human_acceptance`` receipt
        exists; a read that fails with a DevHub error is ``projection_status`` ``unknown``.
        """
        issue = self.get_issue(issue_id)
        try:
            receipts = self.read_proof_receipts(issue_id)
        except DevHubTrackerError:
            return dataclasses.replace(
                issue, native_state=issue.state, projection_status="unknown",
            )
        decided = self._valid_human_decision(issue, receipts)
        if decided == "conflict":
            return dataclasses.replace(
                issue, native_state=issue.state, projection_status="unknown",
            )
        if decided is None:
            return dataclasses.replace(
                issue, native_state=issue.state, projection_status="aligned",
            )
        return self._with_human_acceptance(issue, decided["decision_digest"], issue.state)

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
        out-of-order item, or a stalled cursor, fails closed. The next cursor must equal the
        id of the last receipt returned: the server returns exactly that
        (`6721ad8:src/tracker/store.ts:1954-1985`, `next_cursor` is `selected.at(-1)?.id`).
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
        """Validate one receipt item against its action's exact null rules (PAT-318)."""
        if (not isinstance(item, dict) or set(item) != _PROOF_RECEIPT_KEYS
                or not isinstance(item["id"], str) or not _RECEIPT_ID.fullmatch(item["id"])
                or item["issue"] != issue_id or item["project"] != project_key
                or item["action"] not in _PROOF_RECEIPT_ACTIONS
                or type(item["issue_version"]) is not int or item["issue_version"] < 1
                or not isinstance(item["proof_nonce"], str)
                or not _PROOF_NONCE.fullmatch(item["proof_nonce"])
                or not isinstance(item["actor"], str) or not 1 <= len(item["actor"]) <= 200
                or type(item["created"]) is not int or item["created"] < 0):
            raise DevHubTrackerError("GET", path, None, "invalid_response")
        action = item["action"]
        if action == "human_acceptance":
            # No PR, commit or review coordinate: all four are null, and so is merge_sha.
            if (any(item[key] is not None for key in _RECEIPT_COORDINATES)
                    or item["merge_sha"] is not None or item["reason"] is not None
                    or not isinstance(item["decision_digest"], str)
                    or not _SHA256.fullmatch(item["decision_digest"])
                    or not isinstance(item["decision"], dict)):
                raise DevHubTrackerError("GET", path, None, "invalid_response")
            # The decision is stored as jsonb: recompute the digest from the object read,
            # never compare raw bytes.
            from foundry import acceptance_decision as decisions
            try:
                decisions.validate(item["decision"], issue_id)
                recomputed = decisions.decision_digest(item["decision"])
            except (decisions.AcceptanceDecisionError, ValueError, TypeError):
                raise DevHubTrackerError("GET", path, None, "invalid_response") from None
            if recomputed != item["decision_digest"]:
                raise DevHubTrackerError("GET", path, None, "invalid_response")
            return dict(item)
        if (not isinstance(item["pr_url"], str) or not 1 <= len(item["pr_url"]) <= 500
                or not isinstance(item["head_sha"], str) or not _GIT_SHA.fullmatch(item["head_sha"])
                or not isinstance(item["base_sha"], str) or not _GIT_SHA.fullmatch(item["base_sha"])
                or not isinstance(item["review_digest"], str)
                or not _SHA256.fullmatch(item["review_digest"])
                or (action == "done") != (item["merge_sha"] is not None)
                or (item["merge_sha"] is not None
                    and (not isinstance(item["merge_sha"], str)
                         or not _GIT_SHA.fullmatch(item["merge_sha"])))
                or item["decision_digest"] is not None or item["decision"] is not None
                or (action == "acceptance_override")
                != (item["reason"] is not None)
                or (item["reason"] is not None
                    and (not isinstance(item["reason"], str)
                         or not AC_OVERRIDE_REASON.match(item["reason"])))):
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

    def _read_version(self, project: Project, release: str, version_id: str) -> dict:
        """One validated `GET /versions/:id` of the mapped Version (PAT-309, DHN-39)."""
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
        return raw

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
        raw = self._read_version(project, release, version_id)
        scope = raw["scope"]
        direct = scope["direct"]
        if scope["total_count"] != scope["direct_count"]:
            # The Version publishes only its direct issue ids; the Epic descendants it
            # counts are not listed, and listing them would be approximate discovery.
            raise ReleaseScopeUnavailableError(
                self.name, release, "indirect_scope_unlisted",
            )
        issues = []
        # Numeric order of the issue number (`X-2` before `X-10`), not lexicographic.
        for issue_id in sorted(direct, key=lambda item: int(item.rpartition("-")[2])):
            try:
                issue = self._to_issue(self._issue_raw(issue_id))
            except IssueUnavailableError:
                raise ReleaseScopeUnavailableError(
                    self.name, release, "issue_unavailable",
                ) from None
            issues.append(self._release_issue(issue, project))
        # Re-read the Version after the issue reads (as `ghprojects.py` re-reads its
        # coordinates): a scope or status that moved meanwhile is not returned.
        if self._read_version(project, release, version_id) != raw:
            raise ReleaseScopeUnavailableError(self.name, release, "coordinates_changed")
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
        if issue.import_history is not None and issue.state == "done":
            # PAT-331: imported history, proof kept in the source tracker; never in a
            # new release's scope, never read as accepted here.
            references["unavailable_reason"] = "imported terminal issue (PAT-331)"
            disposition = "unavailable"
        elif issue.state in _UNFINISHED_STATES:
            disposition = "unfinished"
        elif issue.state == "done":
            # Accepted by the exact done receipt (after a review receipt of the same
            # coordinates) with all criteria checked, or by one valid decision receipt
            # below; a done issue with unchecked criteria could be an audited override, which
            # DevHub records as a typed `acceptance_override` receipt (DEVHUB-106, PAT-318);
            # that receipt is not read here: such an issue stays unavailable until a release
            # rule reads it.  A human decision (`human_acceptance`) is read below.
            disposition = "unavailable"
            receipts = self.read_proof_receipts(issue.id, project=project)
            done = [receipt for receipt in receipts if receipt["action"] == "done"]
            # A `human_acceptance` receipt next to the PR-bound ones is a conflict, as in
            # `_valid_human_decision` and on Linear: the issue stays unavailable (PAT-325).
            if (len(done) == 1 and done[0]["pr_url"] == issue.pr_url
                    and not any(receipt["action"] == "human_acceptance"
                                for receipt in receipts)
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
                # PAT-330 / PAT-ADR-0025: a decision receipt without PR or `done` receipt;
                # the reference key is the one Linear emits.
                decided = self._valid_human_decision(issue, receipts)
                if (isinstance(decided, dict) and not issue.pr_url
                        and issue.ac_total >= 1):
                    disposition = "accepted"
                    references["acceptance_decision_digest"] = decided["decision_digest"]
                    references["proof_receipt_id"] = decided["id"]
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
        self._post_link(src_id, link_type, dst_id, source, target)

    def _post_link(self, src_id: str, link_type: str, dst_id: str, source, target) -> None:
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
        source_raw = self._issue_raw(src_id)
        if any(link.type == link_type and link.target == dst_id
               for link in self._to_issue(source_raw).links):
            return
        # The source read above is the one the link version is taken from: two reads and
        # one POST for a new link, not a second read of the source.
        self._post_link(src_id, link_type, dst_id, source_raw, self._issue_raw(dst_id))

    @classmethod
    def migration_comment_exception(cls, project: Project, comment: dict) -> str | None:
        """Why DevHub cannot store ``comment`` byte for byte, or ``None`` (PAT-331 stage 3).

        The rules are those of ``importCommentSchema`` (see the constants above); a reason
        never quotes the content.
        """
        del project
        ref, text = comment.get("source_ref"), comment.get("text")
        author, created = comment.get("source_author"), comment.get("source_created")
        if not cls._origin_text(ref, _SOURCE_REF_MAX):
            return "source_ref not reproducible by DevHub"
        if not isinstance(text, str) or not text:
            return "text empty"
        if _COMMENT_TEXT_FORBIDDEN.search(text):
            return "text holds a NUL or an unpaired surrogate"
        if _utf16_length(text) > _IMPORT_COMMENT_MAX:
            return f"text longer than {_IMPORT_COMMENT_MAX} characters"
        if not text.strip(_JS_WHITESPACE):
            return "text blank"
        if not isinstance(author, str) or not author:
            return "source author absent"
        if _COMMENT_AUTHOR_FORBIDDEN.search(author):
            return "source author holds a control character or an unpaired surrogate"
        if _utf16_length(author) > _IMPORT_AUTHOR_MAX:
            return f"source author longer than {_IMPORT_AUTHOR_MAX} characters"
        if not author.strip(_JS_WHITESPACE):
            return "source author blank"
        if type(created) is not int or not 0 <= created <= _IMPORT_EPOCH_MAX:
            return "source date outside the epoch range of DevHub"
        return None

    def migration_import_comment(
        self, project: Project, issue_id: str, comment: dict,
    ) -> None:
        """Import one source comment (PAT-331 stage 3, DEVHUB-128).

        One ``POST /issues/:id/comments/import`` with a strict four-member body and no
        version precondition.  The ``Idempotency-Key`` is derived from the issue and the
        exact content, so a replay sends the same key and a changed content a new one (and
        gets the server's 409 ``resource_conflict``).  On that 409 the issue is read again:
        the comment already stored under the ``source_ref`` with the same four members is
        success (a lost answer, a concurrent replay), anything else is a
        ``TrackerConflictError`` and nothing is retried.
        """
        project = self._require_project(project)
        registry.require_writable_project(self.name, project)
        self._require_issue_binding(issue_id, project)
        reason = self.migration_comment_exception(project, comment)
        if reason is not None:
            raise ValueError(f"commentaire de migration DevHubTracker invalide: {reason}")
        body = {
            "source_ref": comment["source_ref"], "text": comment["text"],
            "source_author": comment["source_author"],
            "source_created": comment["source_created"],
        }
        digest = hashlib.sha256(
            json.dumps([issue_id, body], ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        path = f"/issues/{urllib.parse.quote(issue_id, safe='')}/comments/import"
        try:
            raw = self._application_req(
                "POST", path, body, idempotent=True,
                idempotency_key=f"foundry-comment-import-{digest[:48]}",
            )
        except DevHubTrackerError as error:
            if error.status == 409 and error.code == "resource_conflict":
                stored = [
                    item for item in self._to_issue(self._issue_raw(issue_id)).comments
                    if item.get("source_ref") == body["source_ref"]
                ]
                if len(stored) == 1 and all(stored[0].get(key) == body[key] for key in (
                        "source_ref", "text", "source_author", "source_created")):
                    return
                raise TrackerConflictError(
                    "commentaire source déjà importé avec un autre contenu ; import refusé"
                ) from None
            raise
        if (not isinstance(raw, dict) or any(raw.get(key) != body[key] for key in body)):
            raise TrackerConflictError("commentaire de migration DevHub divergent après import")

    @classmethod
    def _source_ref(cls, value) -> str:
        if not cls._origin_text(value, _SOURCE_REF_MAX):
            raise ValueError("source_ref DevHubTracker invalide")
        return value

    @staticmethod
    def _preserve_issue_numbers(project: Project) -> bool:
        value = project.extra.get("preserve_issue_numbers", False)
        if type(value) is not bool:
            raise ValueError("preserve_issue_numbers doit être un booléen explicite")
        return value

    @classmethod
    def _migration_issue_number(cls, project: Project, source_ref: str) -> int | None:
        """An explicitly requested same-key source number, never an inferred opt-in."""
        if not cls._preserve_issue_numbers(project):
            return None
        match = re.fullmatch(
            rf"[^:]+:issue:{re.escape(project.key)}-([1-9][0-9]{{0,5}})", source_ref,
        )
        if match is None or not 1 <= int(match[1]) <= _IMPORT_ISSUE_NUMBER_MAX:
            raise TrackerConflictError(
                "identifiant issue source non reproductible par DevHub "
                f"avec preserve_issue_numbers: {source_ref}")
        return int(match[1])

    @classmethod
    def _snapshot_issue_number(cls, project: Project, snapshot: dict, source_ref: str) -> int | None:
        number = cls._migration_issue_number(project, source_ref)
        if number is not None and snapshot.get("id") != f"{project.key}-{number}":
            raise TrackerConflictError("identifiant issue source divergent de source_ref")
        return number

    def _import_lookup_page(self, path: str, base: str) -> list[dict]:
        """One exact-lookup list answer: zero or one item, never a page to walk."""
        raw = self._application_req("GET", path)
        if (not isinstance(raw, dict) or not isinstance(raw.get("items"), list)
                or not isinstance(raw.get("page"), dict)):
            raise DevHubTrackerError("GET", base, None, "invalid_response")
        page = raw["page"]
        if (page.get("schema_version") != _APPLICATION_CONTRACT
                or page.get("truncation") is not False
                or page.get("cursor") is not None
                or page.get("returned_count") != len(raw["items"])
                or len(raw["items"]) > 1
                or not all(isinstance(item, dict) for item in raw["items"])):
            raise DevHubTrackerError("GET", base, None, "invalid_response")
        return raw["items"]

    def migration_find_issue(self, project: Project, source_ref: str) -> Issue | None:
        """Find the issue imported for an exact source coordinate (DHN-34, PAT-319).

        One ``GET /projects/:key/issues?source_ref=`` (zero or one body-free item), then
        ``GET /issues/:id`` for the body.  The answer must carry the same ``source_ref`` and
        belong to the bound project; a server that ignores the filter answers a shape this
        method refuses (``invalid_response``) instead of returning a wrong issue.
        """
        project = self._require_project(project)
        self._source_ref(source_ref)
        number = self._migration_issue_number(project, source_ref)
        base = f"/projects/{urllib.parse.quote(project.key, safe='')}/issues"
        items = self._import_lookup_page(
            f"{base}?{urllib.parse.urlencode({'source_ref': source_ref})}", base,
        )
        if not items:
            return None
        listed = items[0]
        if number is not None and listed.get("id") != f"{project.key}-{number}":
            raise TrackerConflictError("identifiant issue DevHub divergent du numéro source")
        if (listed.get("source_ref") != source_ref or not isinstance(listed.get("id"), str)
                or _ISSUE_ID.fullmatch(listed["id"]) is None
                or self._project_key(listed["id"]) != project.key):
            raise DevHubTrackerError("GET", base, None, "invalid_response")
        raw = self._issue_raw(listed["id"])
        if raw.get("source_ref") != source_ref or raw.get("id") != listed["id"]:
            raise DevHubTrackerError("GET", base, None, "invalid_response")
        return self._to_issue(raw)

    def migration_import_issue(
        self, project: Project, snapshot: dict, *, source_ref: str,
    ) -> Issue:
        """Import one issue under its source coordinate (DHN-34, PAT-319).

        A record carrying ``terminal`` (done or dropped, PAT-331) goes to
        ``_import_terminal_issue``; the rest of this docstring is the living import.

        Always one ``POST /projects/:key/issues`` carrying ``source_ref``: the server
        answers 201 for a first import, 200 with the existing issue for a replay of the same
        content, 409 ``resource_conflict`` for different content or a coordinate held by
        another issue (``TrackerConflictError``).  No client-side lookup precedes it, so
        the server's content comparison is never bypassed.  ``None`` attributes are omitted;
        the server accepts only ``backlog`` or ``ready`` as creation state (409
        ``invalid_transition`` otherwise).  Relations are not sent: ``migration_link_issue``.
        """
        project = self._require_project(project)
        registry.require_writable_project(self.name, project)
        self._source_ref(source_ref)
        if snapshot.get("source_ref", source_ref) != source_ref:
            raise ValueError("source_ref de migration divergent")
        number = self._snapshot_issue_number(project, snapshot, source_ref)
        title, body = snapshot["title"], snapshot["body"]
        if not isinstance(title, str) or not title.strip() or not isinstance(body, str):
            raise ValueError("titre/corps de migration DevHubTracker invalides")
        attributes = snapshot.get("attributes") or {}
        if snapshot.get("terminal") is not None:
            return self._import_terminal_issue(project, snapshot, source_ref)
        fields = self._normalize_fields({
            native: attributes[name]
            for native, name in (
                ("Type", "type"), ("Priority", "priority"),
                ("Estimate", "estimate"), ("State", "state"),
            )
            if attributes.get(name) is not None
        }, allow_state=True)
        request = {"title": title, "body": body, "fields": fields, "source_ref": source_ref}
        if number is not None:
            request["source_number"] = number
        try:
            raw = self._application_req(
                "POST", f"/projects/{urllib.parse.quote(project.key, safe='')}/issues",
                request,
                idempotent=True,
            )
        except DevHubTrackerError as error:
            if error.status == 409 and error.code == "resource_conflict":
                raise TrackerConflictError(
                    "source_ref DevHub déjà importé avec un autre contenu "
                    "ou tenu par une autre issue ; import refusé"
                ) from None
            raise
        issue = self._to_issue(raw)
        if (raw.get("source_ref") != source_ref or raw.get("body") != body
                or issue.title != title.strip()
                or (number is not None and issue.id != f"{project.key}-{number}")
                or self._project_key(issue.id) != project.key):
            raise TrackerConflictError("issue migration DevHub divergente après import")
        return issue

    @staticmethod
    def _terminal_import_body(snapshot: dict, source_ref: str) -> dict:
        """The exact body of ``POST /projects/:key/issues/import`` (PAT-331).

        The server digests ``fields`` as sent, so the three keys are always present (``None``
        for an absent or excepted value) and ``milestone`` / ``labels`` are never sent, as the
        living import: a replay is the same bytes.  ``pr_url`` and the dates are omitted
        when ``None``.
        """
        attributes = snapshot["attributes"]
        state = attributes.get("state")
        if state not in _IMPORT_TERMINAL_STATES:
            raise ValueError("état terminal de migration DevHubTracker invalide")
        body = {
            "source_ref": source_ref, "title": snapshot["title"], "body": snapshot["body"],
            "state": state,
            "fields": {name: attributes.get(name) for name in ("type", "priority", "estimate")},
        }
        terminal = snapshot["terminal"]
        if terminal.get("pr_url") is not None:
            body["pr_url"] = terminal["pr_url"]
        for key in _IMPORT_DATES:
            if terminal.get(key) is not None:
                body[key] = terminal[key]
        return body

    def _import_terminal_issue(
        self, project: Project, snapshot: dict, source_ref: str,
    ) -> Issue:
        """Import one terminal issue (PAT-331): 201 first, 200 same content, 409 otherwise."""
        body = self._terminal_import_body(snapshot, source_ref)
        number = self._snapshot_issue_number(project, snapshot, source_ref)
        if number is not None:
            body["source_number"] = number
        try:
            raw = self._application_req(
                "POST", f"/projects/{urllib.parse.quote(project.key, safe='')}/issues/import",
                body, idempotent=True,
            )
        except DevHubTrackerError as error:
            if error.status == 409 and error.code == "resource_conflict":
                raise TrackerConflictError(
                    "source_ref DevHub déjà importé avec un autre contenu "
                    "ou tenu par une autre issue ; import refusé"
                ) from None
            raise
        issue = self._to_issue(raw)
        if (raw.get("source_ref") != source_ref or raw.get("body") != body["body"]
                or raw.get("title") != body["title"] or issue.import_history is None
                or (number is not None and issue.id != f"{project.key}-{number}")
                or self._project_key(issue.id) != project.key):
            raise TrackerConflictError("issue migration DevHub divergente après import")
        return issue

    # --- selective cutover target (PAT-324, FOUNDRY-ADR-0026) ------------------------
    def migration_attribute_exceptions(
        self, project: Project, snapshot: dict,
    ) -> dict[str, str]:
        """Name the field values ``migration_import_issue`` cannot reproduce exactly.

        Limits of the DevHub fields, each from the server schema read at ``12ca158``
        (``issueFieldsSchema`` of ``src/tracker/contract.ts``, ``createTrackerIssue`` of
        ``src/tracker/store.ts``): ``priority`` is ``P0`` to ``P3``, ``estimate`` an integer
        from 1 to 21, ``type`` a string of 80 UTF-16 units at most (the creation schema accepts an empty one, but
        the ADR relations read, ``adrRelatedEntitySchema`` of
        ``src/tracker/adr-relationships-contract.ts``, requires ``min(1)``, so an empty type is
        excepted), and a new issue
        is created in ``backlog`` or ``ready`` only (409 ``invalid_transition`` otherwise).  A
        value outside these is excepted, never approximated.  An absent (``None``) ``type``,
        ``priority`` or ``estimate`` is not excepted: the import omits it and the server
        stores ``null`` (``fields.priority ?? null`` of ``createTrackerIssue`` at ``3f0cf4b``,
        deduced from source, PAT-325); an absent ``state`` is excepted (see below).  A value the published project
        workflow disables is not an exception: ``migration_preflight`` reads the workflow and
        refuses it before any write (PAT-328), so the manifest never records it.
        """
        self._require_project(project)
        attributes = snapshot.get("attributes")
        if not isinstance(attributes, dict):
            raise TrackerConflictError("snapshot issue de migration DevHub invalide")
        exceptions: dict[str, str] = {}
        issue_type = attributes.get("type")
        if issue_type is not None and (
            not isinstance(issue_type, str) or not issue_type
            or _utf16_length(issue_type) > _IMPORT_TYPE_MAX
        ):
            exceptions["type"] = "target value unavailable: type"
        priority = attributes.get("priority")
        if priority is not None and priority not in _IMPORT_PRIORITIES:
            exceptions["priority"] = f"target value unavailable: {str(priority)[:40]}"
        estimate = attributes.get("estimate")
        if estimate is not None and (
            type(estimate) is not int or not _IMPORT_ESTIMATES[0] <= estimate <= _IMPORT_ESTIMATES[1]
        ):
            exceptions["estimate"] = "target value unavailable: estimate"
        state = attributes.get("state")
        terminal = snapshot.get("terminal") is not None
        if state not in (_IMPORT_TERMINAL_STATES if terminal else _IMPORT_CREATION_STATES):
            # An absent state is omitted by the import and the server creates the issue in
            # ``backlog``: the readback could never equal ``None`` (PAT-325, deduced from
            # ``createTrackerIssue`` at ``3f0cf4b``), so it is excepted like any other state.
            exceptions["state"] = f"target creation state unavailable: {str(state)[:40]}"
        return exceptions

    def migration_preflight(
        self, project: Project, records: tuple[dict, ...],
    ) -> dict:
        """Qualify a DevHub target before the first effect (PAT-324).

        No request writes anything.  Three reads: two (``verify_project_identity``: ``GET
        /projects/resolve`` then ``GET /governance/projects/:key``) prove the key, id and
        canonical repository of the target and refuse a project outside Foundry governance
        (``DevHubGovernanceModeError``); the third (``GET /api/tracker/workflow/v1/projects/:key``,
        ``devhub-workflow.v1``, ``_read_workflow``) gives the published workflow, and every
        ``type``, ``priority`` and ``estimate`` value, and every creation state, that the first
        import would refuse as disabled is refused here, naming it (PAT-328); a refused or
        malformed workflow read is a refusal naming the cause.  The rule copies
        ``assertWorkflowAssignments`` and ``createTrackerIssue`` of ``cd23304`` (observed in
        source): nothing is checked while the workflow ``version`` is 1 (never published),
        otherwise an omitted value counts as ``null`` and must equal an enabled entry.  It is
        best effort: a publish between this read and the write, or a replay of an already
        imported ``source_ref``, can change the server's answer (no workflow-version
        precondition on create).  Everything else is decided locally on
        the closed manifest, because ``migration_import_issue`` and ``migration_import_adrs``
        run only after the issues are written: the source coordinates (valid and unique), the
        issue texts, the living graph (every parent an exact ``Epic`` with at most one parent,
        no hierarchy or dependency cycle: the server refuses the others on the link) and the
        ADR corpus (status, relations that DevHub can record, reciprocal supersession, a
        replacement that is neither ``proposed`` nor ``rejected``).  A route missing on an older server (DEVHUB-105)
        is not probed (unknown); the first copy request would refuse it.
        """
        project = self._require_project(project)
        preserve_numbers = self._preserve_issue_numbers(project)
        refs = [record["source_ref"] for record in records]
        if len(refs) != len(set(refs)):
            raise TrackerConflictError("provenance source de migration dupliquée")
        for ref in refs:
            try:
                self._source_ref(ref)
            except ValueError:
                raise TrackerConflictError(
                    f"source_ref de migration invalide pour DevHub: {str(ref)[:60]}"
                ) from None
        issues = [record for record in records if record.get("kind") == "issue"]
        adrs = [record for record in records if record.get("kind") == "adr"]
        if preserve_numbers:
            numbers = [self._snapshot_issue_number(project, record, record["source_ref"])
                       for record in issues]
            if len(numbers) != len(set(numbers)):
                raise TrackerConflictError("numéro issue source dupliqué")
        self._preflight_issues(issues)
        self._preflight_adrs(project, adrs)
        if not self.verify_project_identity(project):
            raise TrackerConflictError("profil migration DevHub hors projet")
        self._preflight_workflow(project, issues)
        profile = {
            "kind": "devhub-source-ref-v1",
            "issue_coordinate": "source_ref",
            "adr_coordinate": "source_ref+foreign_id",
            "creation_states": list(_IMPORT_CREATION_STATES),
        }
        if preserve_numbers:
            profile["preserve_issue_numbers"] = True
        return profile

    def _read_workflow(self, project: Project) -> tuple[int, dict, list]:
        """Read the published workflow (``GET /api/tracker/workflow/v1/projects/:key``).

        Returns ``(version, enabled values by field, statuses)``; any shape the import rule
        cannot rely on is ``invalid_response`` (fail closed).  ``version == 1`` means never
        published (observed in source at ``cd23304``: ``workflowRevision`` default).
        """
        path = f"/projects/{urllib.parse.quote(project.key, safe='')}"
        raw = self._req("GET", path, response_contract=_WORKFLOW_CONTRACT,
                        api_prefix=_WORKFLOW_API_PREFIX)
        invalid = DevHubTrackerError(
            "GET", f"{_WORKFLOW_API_PREFIX}{path}", None, "invalid_response")
        if (not isinstance(raw, dict) or raw.get("schema_version") != _WORKFLOW_CONTRACT
                or raw.get("project") != project.key):
            raise invalid
        version, settings = raw.get("version"), raw.get("settings")
        if type(version) is not int or version < 1 or not isinstance(settings, dict):
            raise invalid
        fields, statuses = settings.get("fields"), settings.get("statuses")
        if not isinstance(fields, dict) or not isinstance(statuses, list):
            raise invalid
        enabled: dict[str, list] = {}
        for name in ("type", "estimate", "priority"):
            entries = fields.get(name)
            if not isinstance(entries, list):
                raise invalid
            enabled[name] = []
            for entry in entries:
                if (not isinstance(entry, dict) or type(entry.get("enabled")) is not bool
                        or "value" not in entry
                        or not (entry["value"] is None or type(entry["value"]) is (
                            int if name == "estimate" else str))):
                    raise invalid
                if entry["enabled"]:
                    enabled[name].append(entry["value"])
        for status in statuses:
            if (not isinstance(status, dict) or type(status.get("enabled")) is not bool
                    or not isinstance(status.get("canonical"), str)):
                raise invalid
        return version, enabled, statuses

    def _preflight_workflow(self, project: Project, issues: list[dict]) -> None:
        """Refuse a value or creation state the published workflow disables (PAT-328).

        Mirrors ``assertWorkflowAssignments`` (creating) and the initial-status check of
        ``createTrackerIssue`` at ``cd23304``: the match is strict (case, type), an omitted
        or excepted attribute counts as ``null``, and an unpublished workflow
        (``version`` 1) checks nothing.
        """
        try:
            version, enabled, statuses = self._read_workflow(project)
        except DevHubTrackerError as error:
            raise TrackerConflictError(
                f"workflow DevHub illisible, valeurs désactivées non vérifiables: {error}"
            ) from None
        if version == 1:
            return
        for record in issues:
            excepted = {item["attribute"] for item in record.get("exceptions", [])}
            attributes = record["attributes"]
            for name in ("type", "priority", "estimate"):
                value = None if name in excepted else attributes.get(name)
                if not any(type(item) is type(value) and item == value for item in enabled[name]):
                    raise TrackerConflictError(
                        f"valeur désactivée par le workflow DevHub: {name}="
                        f"{str(value)[:40]!r} ({record['source_ref']})")
            state = None if "state" in excepted else attributes.get("state")
            creation = _IMPORT_TERMINAL_STATES if record.get("terminal") else _IMPORT_CREATION_STATES
            state = state if state in creation else "backlog"
            # The server counts every status of the canonical state, enabled or not
            # (``resolveStatus`` of ``workflow-settings.ts`` at ``3f0cf4b``, PAT-325).
            matches = [item for item in statuses if item["canonical"] == state]
            if len(matches) != 1 or not matches[0]["enabled"]:
                raise TrackerConflictError(
                    f"état de création désactivé ou ambigu dans le workflow DevHub: {state} "
                    f"({record['source_ref']})")

    def _preflight_issues(self, issues: list[dict]) -> None:
        by_id = {record["id"]: record for record in issues}
        # A link whose end is a record with pending links is not written yet (PAT-331).
        deferred = {
            record["id"] for record in issues
            if any(entry["item"] == "links" for entry in record.get("pending", []))
        }
        parents: dict[str, str] = {}
        hierarchy: dict[str, set[str]] = {}
        dependencies: dict[str, set[str]] = {}
        for record in issues:
            title, body = record["title"], record["body"]
            if (not isinstance(title, str) or not title.strip() or title != title.strip()
                    or _utf16_length(title) > _IMPORT_TITLE_MAX
                    or not isinstance(body, str) or _utf16_length(body) > _IMPORT_BODY_MAX):
                raise TrackerConflictError(
                    f"titre ou corps non reproductible par DevHub: {record['source_ref']}"
                )
            terminal = record.get("terminal")
            if terminal is not None:
                created = terminal.get("source_created")
                dates = [terminal.get(key) for key in _IMPORT_DATES]
                if (any(value is not None and (type(value) is not int or not 0 <= value <= _IMPORT_EPOCH_MAX)
                        for value in dates)
                        or (created is not None and any(
                            value is not None and value < created for value in dates[1:]))):
                    # DEVHUB-126: updated and completed may not precede created.
                    raise TrackerConflictError(
                        f"dates source non reproductibles par DevHub: {record['source_ref']}")
                pr_url = terminal.get("pr_url")
                if pr_url is not None and (
                        not isinstance(pr_url, str) or not pr_url
                        or _utf16_length(pr_url) > _IMPORT_PR_URL_MAX):
                    raise TrackerConflictError(
                        f"pr_url non reproductible par DevHub: {record['source_ref']}")
            seen_comments: set[str] = set()
            for comment in record.get("comments", []):
                # PAT-331 stage 3: a comment outside DevHub's limits is a named manifest
                # exception at capture; one that reaches the copy is refused before any write.
                reason = self.migration_comment_exception(None, comment)
                if reason is not None or comment["source_ref"] in seen_comments:
                    raise TrackerConflictError(
                        "commentaire non reproductible par DevHub: "
                        f"{record['source_ref']} ({reason or 'source_ref dupliqué'})")
                seen_comments.add(comment["source_ref"])
            for link in record["links"]:
                other, kind = link["target"], link["type"]
                if kind not in _LINK_TYPES:
                    raise TrackerCapabilityUnavailableError(
                        self.name, f"migration_issue_link:{kind}")
                if other not in by_id:
                    continue  # not an issue of the manifest, as migration.copy_and_verify does
                if record["id"] in deferred or other in deferred:
                    continue
                if other == record["id"]:
                    raise TrackerConflictError(
                        f"lien vers soi-même refusé par DevHub: {record['source_ref']}")
                if kind in {"subtask-of", "parent-of"}:
                    child, parent = (
                        (record["id"], other) if kind == "subtask-of" else (other, record["id"]))
                    if parents.setdefault(child, parent) != parent:
                        raise TrackerConflictError(
                            f"plusieurs parents refusés par DevHub: {by_id[child]['source_ref']}")
                    hierarchy.setdefault(child, set()).add(parent)
                elif kind in {"depends-on", "blocks"}:
                    first, second = (
                        (record["id"], other) if kind == "depends-on" else (other, record["id"]))
                    dependencies.setdefault(first, set()).add(second)
        for child, parent in parents.items():
            parent_record = by_id[parent]
            excepted = {item["attribute"] for item in parent_record.get("exceptions", [])}
            if parent_record["attributes"].get("type") != "Epic" or "type" in excepted:
                raise TrackerConflictError(
                    f"parent non Epic refusé par DevHub: {by_id[child]['source_ref']}")
        if _has_cycle(hierarchy) or _has_cycle(dependencies):
            raise TrackerConflictError("cycle de liens refusé par DevHub")

    def _preflight_adrs(self, project: Project, adrs: list[dict]) -> None:
        ordered, by_id = self._numbering_order(project, tuple(adrs))
        del ordered  # only the cycle, duplicate and numbering refusals matter here
        unlinked = {
            record["id"] for record in adrs
            if record["status"] == "superseded"
            and record["relations"]["superseded_by"] == "unknown"
        }
        for record in adrs:
            ref, relations = record["source_ref"], record["relations"]
            # An ``unknown`` family and the source-only issue references are carried by
            # ``unknown_relations`` and ``source_only_refs`` (DEVHUB-122, PAT-327); the
            # combinations the server refuses are refused by ``_prepare_adr_import`` below.
            title, body = record["title"], record["body"]
            if (not isinstance(title, str) or title != title.strip()
                    or _utf16_length(title) > _IMPORT_TITLE_MAX
                    or not isinstance(body, str) or _utf16_length(body) > _IMPORT_BODY_MAX):
                raise TrackerConflictError(f"titre ou corps ADR non reproductible: {ref}")
            replacement = relations["superseded_by"]
            if replacement != "unknown":
                if replacement is not None and replacement not in by_id:
                    raise TrackerConflictError(f"ADR de remplacement hors corpus: {ref}")
                if (record["status"] == "superseded") != (replacement is not None):
                    raise TrackerConflictError(f"supersession ADR sans lien réciproque: {ref}")
            if replacement not in (None, "unknown") and (
                    by_id[replacement]["status"] in {"proposed", "rejected"}):
                # DevHub refuses the import with 409 invalid_transition (``cd23304``, PAT-328);
                # a replacement that is accepted, superseded or deprecated is taken;
                # historical rejected imports cannot become replacements either.
                raise TrackerConflictError(
                    f"ADR de remplacement {by_id[replacement]['status']} refusée par DevHub: {ref} "
                    f"(remplaçant {replacement})")
            if relations["supersedes"] != "unknown":
                expected = sorted(
                    other["id"] for other in adrs
                    if other["relations"]["superseded_by"] == record["id"])
                if sorted(relations["supersedes"]) != expected:
                    raise TrackerConflictError(f"supersession ADR non réciproque: {ref}")
                if unlinked - {record["id"]}:
                    # The export gives ``unknown`` to every other ADR's ``supersedes`` while
                    # a superseded ADR has no recorded replacement (PAT-ADR-0008): a known
                    # list would never read back.
                    raise TrackerConflictError(
                        f"supersedes connu illisible tant qu'une ADR superseded a un "
                        f"remplaçant inconnu: {ref}")
            if relations["issues"] != "unknown" and len(relations["issues"]) > _ADR_LINK_MAX:
                raise TrackerConflictError(f"trop de relations d'issue pour DevHub: {ref}")
            arguments = self._snapshot_import_arguments(record, ref, by_id)
            arguments.setdefault("supersedes", _UNSPECIFIED_ADR_RELATION)
            arguments.setdefault("superseded_by", _UNSPECIFIED_ADR_RELATION)
            # The issue ids are still the source's: their shape is checked on the target
            # ids by ``migration_import_adrs``.
            arguments["issue_refs"] = _UNSPECIFIED_ADR_RELATION
            try:
                self._prepare_adr_import(project, **arguments)
            except ValueError as error:
                raise TrackerConflictError(f"ADR non importable dans DevHub: {ref} ({error})") from None

    def migration_prepare_adr(self, project: Project, snapshot: dict) -> dict:
        """Give the target ADR snapshot a canonical relation order (PAT-324).

        The foreign id is the port id, so no id is translated.  DevHub does not keep the order
        of ``supersedes`` or of the issue relations, and ``migration_export_adrs`` sorts them:
        sorting the expected side here makes the readback comparison one of sets.
        """
        del project
        prepared = copy.deepcopy(snapshot)
        relations = prepared["relations"]
        if isinstance(relations.get("supersedes"), list):
            relations["supersedes"] = sorted(relations["supersedes"])
        if isinstance(relations.get("issues"), list):
            relations["issues"] = sorted(relations["issues"], key=_issue_order)
        return prepared

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

    def _adr_lookup(
        self, project: Project, *, source_ref: str | None = None,
        foreign_id: str | None = None,
    ) -> dict | None:
        """Exact ADR lookup by ``source_ref`` and/or ``foreign_id`` (zero or one item).

        The two filters combine by AND on the server; the answer must echo every filter
        given and belong to the bound project, otherwise ``invalid_response``.
        """
        project = self._require_project(project)
        params = {}
        if source_ref is not None:
            params["source_ref"] = self._source_ref(source_ref)
        if foreign_id is not None:
            if not self._origin_text(foreign_id, _FOREIGN_ID_MAX):
                raise ValueError("foreign_id DevHubTracker invalide")
            params["foreign_id"] = foreign_id
        if not params:
            raise ValueError("source_ref ou foreign_id requis")
        base = f"/projects/{urllib.parse.quote(project.key, safe='')}/adrs"
        items = self._import_lookup_page(f"{base}?{urllib.parse.urlencode(params)}", base)
        if not items:
            return None
        item = items[0]
        self._to_adr(item)
        if (self._server_adr_key(item["id"]) != project.key
                or any(item.get(key) != value for key, value in params.items())):
            raise DevHubTrackerError("GET", base, None, "invalid_response")
        return item

    def migration_find_adr(
        self, project: Project, source_ref: str | None = None, *,
        foreign_id: str | None = None,
    ) -> Adr | None:
        """Read an imported ADR back by ``source_ref`` or ``foreign_id`` (DHN-36, PAT-319).

        The port passes the ``source_ref``; ``foreign_id`` is an additive keyword for the
        alias.  The body-free list item is completed by ``GET /adrs/:id``; the returned
        ``Adr.id`` is the foreign id when the ADR has one (``_to_adr``).
        """
        item = self._adr_lookup(project, source_ref=source_ref, foreign_id=foreign_id)
        if item is None:
            return None
        path = f"/adrs/{urllib.parse.quote(item['id'], safe='')}"
        raw = self._application_req("GET", path)
        adr = self._to_adr(raw)
        if (raw.get("id") != item["id"] or raw.get("source_ref") != item.get("source_ref")
                or raw.get("foreign_id") != item.get("foreign_id")
                or not isinstance(raw.get("body"), str)):
            raise DevHubTrackerError("GET", path, None, "invalid_response")
        return adr

    def _prepare_adr_import(
        self, project: Project, *, adr_id, title, body, historical_status, source_ref,
        source_created, source_updated, expected_source_sha256, supersedes, superseded_by,
        issue_refs, source_only_issue_refs, unknown_relations=(),
    ) -> dict:
        """Validate one ADR import locally and build its request, without any request.

        ``superseded_by`` is left out: it needs the replacement's ``source_ref``, read by
        ``import_adr``. ``migration_import_adrs`` runs this for every snapshot before its
        first write (PAT-319).
        """
        self._source_ref(source_ref)
        if not self._origin_text(adr_id, _FOREIGN_ID_MAX):
            raise ValueError("identifiant ADR étranger DevHubTracker invalide")
        if (not isinstance(title, str) or not title.strip() or not isinstance(body, str)
                or historical_status not in _ADR_IMPORT_STATES):
            raise ValueError("ADR d'import DevHubTracker invalide")
        source_only = list(source_only_issue_refs)
        if (len(source_only) > _ADR_SOURCE_ONLY_MAX or len(set(source_only)) != len(source_only)
                or any(not self._origin_text(ref, _SOURCE_REF_MAX) for ref in source_only)):
            raise ValueError("références source ADR DevHub invalides")
        unknown = list(unknown_relations)
        if (len(set(unknown)) != len(unknown)
                or any(name not in _ADR_UNKNOWN_FAMILIES for name in unknown)):
            raise ValueError("familles de relations ADR inconnues invalides")
        if "issues" in unknown and (
                source_only or (issue_refs is not _UNSPECIFIED_ADR_RELATION and list(issue_refs))):
            raise ValueError("relation issues déclarée inconnue mais des valeurs sont fournies")
        if "superseded_by" in unknown and (
                superseded_by is not _UNSPECIFIED_ADR_RELATION and superseded_by is not None):
            raise ValueError("relation superseded_by déclarée inconnue mais une valeur est fournie")
        if supersedes is not _UNSPECIFIED_ADR_RELATION and supersedes:
            raise TrackerCapabilityUnavailableError(
                self.name, "adr_import_supersedes",
            )
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if not isinstance(expected_source_sha256, str) or digest != expected_source_sha256:
            raise TrackerConflictError("empreinte du corps ADR divergente ; import refusé")
        request = {
            "source_ref": source_ref, "foreign_id": adr_id, "title": title, "body": body,
            "body_sha256": digest, "status": historical_status,
        }
        for key, value in (("source_created", source_created), ("source_updated", source_updated)):
            if value is not None:
                if type(value) is not int or not 0 <= value <= _IMPORT_EPOCH_MAX:
                    raise ValueError("date source ADR DevHubTracker invalide")
                request[key] = value
        if superseded_by is not _UNSPECIFIED_ADR_RELATION and superseded_by is not None:
            if historical_status != "superseded":
                raise ValueError("superseded_by exige le statut superseded")
        if issue_refs is not _UNSPECIFIED_ADR_RELATION:
            linked = list(issue_refs)
            for ref in linked:
                if (not isinstance(ref, str) or _ISSUE_ID.fullmatch(ref) is None
                        or self._project_key(ref) != project.key):
                    raise ValueError("référence d'issue ADR DevHub invalide")
            request["linked_issue_ids"] = linked
        # Absent when empty: an empty list is the same content, and leaving the key out keeps
        # the request hash an older server knows (DEVHUB-122).
        if source_only:
            request["source_only_refs"] = source_only
        if unknown:
            request["unknown_relations"] = unknown
        return request

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
        unknown_relations: tuple[str, ...] = (),
    ) -> Adr:
        """Import one historical ADR through ``POST /projects/:key/adrs/import`` (DHN-36).

        ``adr_id`` is kept as ``foreign_id`` and becomes the port ``Adr.id``; the status
        and the exact body are kept (``body_sha256`` is checked here against
        ``expected_source_sha256`` and again by the server).  ``superseded_by`` names a
        replacement of this project by its foreign id, imported first; the server takes its
        ``source_ref``.  A non-empty ``supersedes`` is refused (DevHub stores the
        edge from the replaced ADR, written when that ADR is imported with its
        ``superseded_by``). Historical ``rejected`` is read/import vocabulary only
        (``_ADR_IMPORT_STATES``), never a native decision target. A ``foreign_id`` equal to the server id of a native ADR of
        the project (no ``foreign_id``) is refused before the write.  Replay 200, divergence
        or a taken ``foreign_id`` 409 (``TrackerConflictError``).  The answer does not echo
        ``superseded_by`` or ``linked_issue_ids`` (``6721ad8:src/tracker/store.ts:374-386``,
        ``publicAdr``); the server compares them in the replay digest (``:2244-2248``).

        ``source_only_issue_refs`` is sent as ``source_only_refs`` and ``unknown_relations``
        as such (DEVHUB-122, PAT-327, ``3f0cf4b``), each only when non-empty.  The answer
        must carry both exactly as sent (an answer without them counts as empty), otherwise
        ``TrackerConflictError``.  A server older than DEVHUB-122 answers 400 to a request
        that carries either key.  A family named in ``unknown_relations`` with a value for it
        in the same request is refused here, as the server refuses it (400).
        """
        project = self._require_project(project)
        registry.require_writable_project(self.name, project)
        request = self._prepare_adr_import(
            project, adr_id=adr_id, title=title, body=body,
            historical_status=historical_status, source_ref=source_ref,
            source_created=source_created, source_updated=source_updated,
            expected_source_sha256=expected_source_sha256, supersedes=supersedes,
            superseded_by=superseded_by, issue_refs=issue_refs,
            source_only_issue_refs=source_only_issue_refs,
            unknown_relations=unknown_relations,
        )
        if self._server_adr_key(adr_id) == project.key:
            # A foreign id of the form `<KEY>-ADR-NNNN` equal to the server id of a native
            # ADR gives two ADRs the same port id, which makes the project listing fail
            # `pagination_repeated` for good.  Refused here before any write.  The server id
            # of another imported ADR is no clash: its port id is its own foreign id.  The
            # inverse case (a later native creation taking that number) is prevented only by a
            # server carrying DEVHUB-111 (`16b6888`), which also refuses a taken number (409).
            for item in self._list_adr_items(project):
                if item["id"] == adr_id and item.get("foreign_id") is None:
                    raise TrackerConflictError(
                        "foreign_id ADR DevHub égal à l'identifiant serveur d'une ADR native ; "
                        "import refusé"
                    )
        if superseded_by is not _UNSPECIFIED_ADR_RELATION and superseded_by is not None:
            replacement = self._adr_lookup(project, foreign_id=superseded_by)
            if replacement is None or not isinstance(replacement.get("source_ref"), str):
                raise TrackerConflictError(
                    "ADR de remplacement DevHub non importée ; importe-la d'abord"
                )
            request["superseded_by"] = replacement["source_ref"]
        try:
            with self._governance_guard(project.key):
                raw = self._application_req(
                    "POST", f"/projects/{urllib.parse.quote(project.key, safe='')}/adrs/import",
                    request, idempotent=True,
                )
        except DevHubTrackerError as error:
            unavailable = self._route_unavailable(error, "migration_adr_import")
            if unavailable is not None:
                raise unavailable from None
            if error.status == 409 and error.code == "resource_conflict":
                raise TrackerConflictError(
                    "import ADR DevHub refusé : source_ref déjà importé avec un autre "
                    "contenu, foreign_id ou numéro d'origine pris, ou remplacement invalide"
                ) from None
            raise
        adr = self._to_adr(raw)
        if (raw.get("source_ref") != source_ref or raw.get("foreign_id") != adr_id
                or adr.status != historical_status or raw.get("body") != body
                or adr.title != title.strip()
                or self._server_adr_key(raw["id"]) != project.key
                or any(raw.get(key) != request.get(key)
                       for key in ("source_created", "source_updated"))
                or any(raw.get(key, []) != request.get(key, [])
                       for key in ("source_only_refs", "unknown_relations"))):
            raise TrackerConflictError("ADR migration DevHub divergente après import")
        return adr

    def migration_import_adr(
        self, project: Project, snapshot: dict, *, source_ref: str,
    ) -> Adr:
        return self.import_adr(project, **self._snapshot_import_arguments(snapshot, source_ref))

    @staticmethod
    def _snapshot_import_arguments(
        snapshot: dict, source_ref: str, corpus: dict | None = None,
    ) -> dict:
        """Keyword arguments of ``import_adr`` for one snapshot.

        A known non-empty ``supersedes`` is handed to ``import_adr``, which refuses it
        (DevHub writes that edge when the replaced ADR is imported with its
        ``superseded_by``).  Only inside a corpus (``corpus``: snapshots by id) is it left
        out, and only when every predecessor it names is in the corpus and names this ADR
        as its ``superseded_by``: the edge is then written by that predecessor's import.
        """
        relations = snapshot["relations"]
        optional = {}
        if relations.get("supersedes", "unknown") != "unknown":
            predecessors = tuple(relations["supersedes"])
            covered = corpus is not None and all(
                predecessor in corpus
                and corpus[predecessor]["relations"].get("superseded_by") == snapshot["id"]
                for predecessor in predecessors
            )
            if predecessors and not covered:
                optional["supersedes"] = predecessors
        if relations.get("superseded_by") != "unknown":
            optional["superseded_by"] = relations["superseded_by"]
        if relations.get("issues") != "unknown":
            optional["issue_refs"] = tuple(relations["issues"])
        unknown = tuple(
            name for name in _ADR_UNKNOWN_FAMILIES if relations.get(name) == "unknown")
        return {
            "adr_id": snapshot["id"], "title": snapshot["title"], "body": snapshot["body"],
            "historical_status": snapshot["status"], "source_ref": source_ref,
            "source_created": snapshot.get("source_created"),
            "source_updated": snapshot.get("source_updated"),
            "expected_source_sha256": hashlib.sha256(
                snapshot["body"].encode("utf-8")
            ).hexdigest(),
            "source_only_issue_refs": tuple(snapshot.get("source_only_issue_refs", [])),
            "unknown_relations": unknown,
            **optional,
        }

    @staticmethod
    def _origin_adr_number(key: str, foreign_id: object) -> int | None:
        """The number DevHub keeps for an imported ADR, ``None`` when it assigns its own.

        ``originAdrNumber`` of DevHub ``890e338`` (DEVHUB-111, DEVHUB-116): a foreign id
        byte-identical to ``<KEY>-ADR-NNNN``, NNNN being the number (1 to 999999) left-padded
        to 4 digits and not over-padded, keeps that number (PAT-332, read from DevHub's
        message, not re-run here).
        """
        prefix = f"{key}-ADR-"
        if not isinstance(foreign_id, str) or not foreign_id.startswith(prefix):
            return None
        digits = foreign_id[len(prefix):]
        if not digits.isascii() or not digits.isdigit() or len(digits) > 6:
            return None
        number = int(digits)
        if not 1 <= number <= 999_999 or digits != str(number).zfill(4):
            return None
        return number

    @classmethod
    def _numbering_order(
        cls, project: Project, snapshots: tuple[dict, ...],
    ) -> tuple[list[dict], dict]:
        """``_replacement_order`` with the ADRs that keep their number first (PAT-332).

        DevHub gives the others the project counter and raises it past every number it
        holds, so an imported ``FOUNDRY-ADR-0001`` taking number 1 first refused
        ``PAT-ADR-0001``.  The order keeps replacement first, then simulates the server
        numbering from an empty project and refuses, before any write and naming the ADR,
        one that would claim a number already held (a counter hit by a later numbered ADR).

        Limit: it picks one order and does not search the others.  An unnumbered replacement
        placed before the numbered ADR it replaces takes the counter, so a corpus can be
        refused although another order exists (``TRAME-ADR-0005`` replaced by an unnumbered
        ADR, plus ``TRAME-ADR-0001``: the order replacement, 0005, 0001 is refused, the order
        0001, replacement, 0005 is not).  Writing the numbered ADRs in decreasing number
        order does not settle it (that example stays refused); an increasing order is not shown to
        settle every corpus either.  The refusal fails closed, before any write.
        """
        def numbered(snapshot):
            return cls._origin_adr_number(project.key, snapshot["id"]) is not None

        ordered, by_id = cls._replacement_order(
            tuple(sorted(snapshots, key=lambda snapshot: not numbered(snapshot))))
        held: dict[int, str] = {}
        counter = 1
        for snapshot in ordered:
            number = cls._origin_adr_number(project.key, snapshot["id"])
            if number is None:
                number = counter
            elif number in held:
                raise TrackerConflictError(
                    f"numéro ADR déjà pris dans l'ordre d'import: {snapshot['id']} "
                    f"(numéro {number}, pris par {held[number]})")
            held[number] = snapshot["id"]
            counter = max(counter, number + 1)
        return ordered, by_id

    @staticmethod
    def _replacement_order(snapshots: tuple[dict, ...]) -> tuple[list[dict], dict]:
        """Snapshots ordered replacement first, and the snapshots by id (PAT-319, PAT-324).

        The server refuses ``superseded_by`` to an ADR not yet imported.  A cycle is
        ``TrackerConflictError``; a replacement outside the corpus is not reordered.
        """
        by_id = {snapshot["id"]: snapshot for snapshot in snapshots}
        if len(by_id) != len(snapshots):
            raise TrackerConflictError("identifiant ADR de migration dupliqué")
        ordered, state = [], {}

        def visit(snapshot):
            mark = state.get(snapshot["id"])
            if mark == "done":
                return
            if mark == "active":
                raise TrackerConflictError("cycle de remplacement ADR de migration")
            state[snapshot["id"]] = "active"
            replacement = snapshot["relations"].get("superseded_by")
            if replacement in by_id:
                visit(by_id[replacement])
            state[snapshot["id"]] = "done"
            ordered.append(snapshot)

        for snapshot in snapshots:
            visit(snapshot)
        return ordered, by_id

    def migration_import_adrs(
        self, project: Project, snapshots: tuple[dict, ...],
        *, qualification: object | None = None,
    ) -> list[Adr]:
        """Import a closed corpus, replacements first, answers in input order (PAT-319).

        PAT-332: among the orders that respect it, the ADRs that keep their number
        (``_origin_adr_number``) go first (``_numbering_order``).

        The server refuses ``superseded_by`` to an ADR not yet imported, so each ADR is
        written after the one that replaces it; a cycle or a replacement outside the
        corpus is not reordered (the server's refusal stands).
        """
        if qualification is not None:
            raise TrackerConflictError("devhub ADR migration qualification is unexpected")
        project = self._require_project(project)
        ordered, by_id = self._numbering_order(project, snapshots)
        registry.require_writable_project(self.name, project)
        for snapshot in ordered:
            # Local refusals (status, source-only issue references, supersedes, digest,
            # shapes) for the whole corpus before the first write.
            arguments = self._snapshot_import_arguments(
                snapshot, snapshot["source_ref"], by_id)
            arguments.setdefault("supersedes", _UNSPECIFIED_ADR_RELATION)
            arguments.setdefault("superseded_by", _UNSPECIFIED_ADR_RELATION)
            arguments.setdefault("issue_refs", _UNSPECIFIED_ADR_RELATION)
            self._prepare_adr_import(project, **arguments)
        imported = {}
        for snapshot in ordered:
            imported[snapshot["id"]] = self.import_adr(
                project, **self._snapshot_import_arguments(
                    snapshot, snapshot["source_ref"], by_id),
            )
        return [imported[snapshot["id"]] for snapshot in snapshots]

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

    def _adr_route_item(self, adr: Adr, project: Project | None) -> tuple[str, dict]:
        """Resolve the server item whose port id is ``adr.id`` (project key, item).

        ``Adr.id`` is the foreign id of an imported ADR (``_to_adr``), which can equal the
        server id of another ADR of the project; the route id is therefore taken from the
        project listing by port id, never from ``adr.id``.  Absent, or an item whose server
        id is not of the bound project, is ``invalid_response``.
        """
        project_key, items = self._adr_route_items((adr,), project)
        return project_key, items[0]

    def _adr_route_items(
        self, adrs: tuple[Adr, ...], project: Project | None,
    ) -> tuple[str, list[dict]]:
        """`_adr_route_item` for several port ids over one project listing."""
        project_key = self._require_adr_binding(adrs[0].id, project)
        for adr in adrs[1:]:
            self._require_adr_binding(adr.id, project)
        listing = self._list_adr_items(Project(key=project_key, id="bound"))
        found = []
        for adr in adrs:
            item = next((item for item in listing if self._to_adr(item).id == adr.id), None)
            if (not isinstance(item, dict) or not isinstance(item.get("version"), int)
                    or self._server_adr_key(item["id"]) != project_key):
                raise DevHubTrackerError("GET", "/projects/adrs", None, "invalid_response")
            found.append(item)
        return project_key, found

    def set_adr_status(
        self, adr: Adr, status: str, project: Project | None = None,
    ) -> None:
        if status == "superseded":
            # DEVHUB-116 (`12ca158:src/tracker/store.ts:2365`): the status route answers 409
            # `invalid_transition` to `superseded`; only the linked route supersedes.
            raise ValueError(
                "DevHub refuse superseded sur la route de statut ; "
                "utilise supersede_adr (adr supersede), qui nomme le remplaçant"
            )
        if status not in _ADR_STATES - {"proposed"}:
            raise ValueError("statut ADR non supporté par DevHub")
        project_key, raw = self._adr_route_item(adr, project)
        server_id = raw["id"]
        proof = self._proof(action="adr-status", project_key=project_key,
                            resource_id=server_id,
                            version=raw["version"], target_status=status)
        with self._governance_guard(project_key):
            self._application_req(
                "POST", f"/adrs/{urllib.parse.quote(server_id, safe='')}/status",
                {"status": status}, version=raw["version"], proof=proof, idempotent=True,
            )

    def _read_adr_decision_document(self, server_id: str) -> dict:
        """Read the supersession relations of one ADR (`devhub-adr-decisions.v1`, DHN-32).

        `GET /adr-decisions/adrs/:id` (`6721ad8:src/tracker/adr-decisions-http.ts`, scope
        `tracker:read`); `relations` come from the recorded supersede decisions
        (`6721ad8:src/tracker/adr-decisions-store.ts:20-22`).
        """
        path = f"/adr-decisions/adrs/{urllib.parse.quote(server_id, safe='')}"
        raw = self._req("GET", path, response_contract=_ADR_DECISIONS_CONTRACT)
        relations = raw.get("relations") if isinstance(raw, dict) else None
        if (not isinstance(raw, dict)
                or raw.get("schema_version") != _ADR_DECISIONS_CONTRACT
                or not isinstance(raw.get("adr"), dict)
                or raw["adr"].get("id") != server_id
                or not isinstance(relations, dict)
                or any(relations.get(key) is not None
                       and not isinstance(relations[key], str)
                       for key in ("supersedes", "superseded_by"))
                or ("supersedes_all" in relations
                    and (not isinstance(relations["supersedes_all"], list)
                         or not all(isinstance(item, str)
                                    for item in relations["supersedes_all"])))):
            raise DevHubTrackerError("GET", path, None, "invalid_response")
        return raw

    def supersede_adr(
        self, adr: Adr, replacement_id: str, project: Project | None = None,
    ) -> None:
        """Supersede an accepted ADR by an already accepted one (PAT-320, DHN-32).

        Both ADRs are resolved by port id to their server item (`_adr_route_item`). The
        source must be `accepted` and the replacement `accepted`, unless the source is
        already superseded with a link naming that same replacement, a no-write replay
        (checked first, whatever the replacement's status now is): otherwise
        `TrackerConflictError` before any write. The route is `POST /adrs/:id/supersede` (`6721ad8:docs/TRACKER_API.md`,
        "Proof-bound ADR supersession"), never the status route.  Up to DevHub ``8028000``
        that route created an unlinked supersession; since ``12ca158`` it refuses
        ``superseded``.  The proof is bound to both server ids and versions; the
        link is read back from `GET /adr-decisions/adrs/:id`.
        """
        project_key, (source, replacement) = self._adr_route_items(
            (adr, Adr(id=replacement_id, title="")), project)
        if source["id"] == replacement["id"]:
            raise TrackerConflictError("ADR DevHub : une ADR ne peut se remplacer elle-même")
        if source.get("status") == "superseded":
            # Replay first: the link may already name this replacement, which has since
            # left `accepted` (A by B, then B by C, then A by B again).
            if self._read_adr_decision_document(source["id"])["relations"].get(
                    "superseded_by") == replacement["id"]:
                return
            raise TrackerConflictError(
                "ADR DevHub déjà remplacée par une autre ADR ou sans lien nommé")
        if replacement.get("status") != "accepted":
            raise TrackerConflictError("ADR DevHub : le remplaçant doit être accepté")
        if source.get("status") != "accepted":
            raise TrackerConflictError("ADR DevHub : seule une ADR acceptée peut être remplacée")
        server_id, replacement_server_id = source["id"], replacement["id"]
        proof = self._proof(
            action="adr-supersede", project_key=project_key, resource_id=server_id,
            version=source["version"], target_status="superseded",
            extra={"replacement_id": replacement_server_id,
                   "replacement_version": replacement["version"]},
        )
        path = f"/adrs/{urllib.parse.quote(server_id, safe='')}/supersede"
        try:
            with self._governance_guard(project_key):
                self._application_req(
                    "POST", path,
                    {"replacement": replacement_server_id,
                     "replacement_version": replacement["version"]},
                    version=source["version"], proof=proof, idempotent=True,
                )
        except DevHubTrackerError as exc:
            unavailable = self._route_unavailable(exc, "adr_supersession")
            if unavailable is not None:
                raise unavailable from None
            if exc.status == 409 and exc.code in {
                    "version_conflict", "invalid_transition", "resource_conflict"}:
                raise TrackerConflictError(
                    "supersession ADR DevHub refusée (version modifiée, état ou lien "
                    "invalide) ; recharge les ADR puis relance"
                ) from None
            raise
        linked = self._read_adr_decision_document(server_id)["relations"]
        if linked.get("superseded_by") != replacement_server_id:
            raise DevHubTrackerError("GET", path, None, "invalid_response")

    def migration_export_adrs(self, project: Project) -> list[dict]:
        """Export ADRs with their supersession relations read back (PAT-320, DHN-32).

        One project listing feeds the ADR reads and the id maps.  `superseded_by` and
        `supersedes` come from `GET /adr-decisions/adrs/:id`, mapped to port ids.  A
        superseded ADR with no recorded link (up to DevHub ``8028000`` the status route created
        one; since ``12ca158`` it refuses it, but such an ADR may still exist) keeps `unknown`
        (PAT-ADR-0003).  Its replacement is unknown too, so while the project holds such an
        ADR other than the entry itself, no entry's `supersedes` is known complete: it is
        `unknown`, never `[]` or a partial list (PAT-ADR-0008).  `issues` is read from
        ``GET /adrs/:id/relationships`` (one more read per ADR, PAT-324): a complete empty page is
        a known empty list.  ``supersedes`` and ``issues`` are sorted, the order
        ``migration_prepare_adr`` gives the expected side, so a readback comparison is by set.
        A family named in the ADR's ``unknown_relations`` is exported as ``unknown`` whatever
        later writes added to it (an import-time declaration, DEVHUB-122, PAT-327), and
        its ``source_only_refs`` come back, in the order stored, as ``source_only_issue_refs``
        (key absent when none).
        """
        items = self._list_adr_items(project)
        port_id = {item["id"]: self._to_adr(item).id for item in items}
        entries = []
        for item in items:
            raw = self._application_req(
                "GET", f"/adrs/{urllib.parse.quote(item['id'], safe='')}")
            adr = self._to_adr(raw)
            source_only, declared = self._adr_source_relations(raw)
            relations = self._read_adr_decision_document(item["id"])["relations"]
            # A family declared unknown at import is not read: the declaration is the answer.
            issues = [] if "issues" in declared else self._adr_relationship_ids(item["id"])
            entries.append((adr, relations, issues, source_only, declared))
        unlinked = {
            adr.id for adr, relations, *_rest in entries
            if adr.status == "superseded" and relations.get("superseded_by") is None
        }
        exported = []
        for adr, relations, issues, source_only, declared in entries:
            entry_relations = {
                "supersedes": "unknown", "superseded_by": "unknown",
                "issues": sorted(issues, key=_issue_order)}
            try:
                if unlinked - {adr.id}:
                    entry_relations["supersedes"] = "unknown"
                else:
                    predecessors = relations.get("supersedes_all")
                    if predecessors is None:
                        predecessors = (
                            [relations["supersedes"]] if relations.get("supersedes") else [])
                    entry_relations["supersedes"] = sorted(
                        port_id[item] for item in predecessors)
                successor = relations.get("superseded_by")
                if successor is not None:
                    entry_relations["superseded_by"] = port_id[successor]
                elif adr.status != "superseded":
                    entry_relations["superseded_by"] = None
            except KeyError:
                raise DevHubTrackerError(
                    "GET", "/adr-decisions/adrs", None, "invalid_response") from None
            for name in declared:
                entry_relations[name] = "unknown"
            exported.append({
                "adr": adr, "relations": entry_relations,
                "source_created": None, "source_updated": None,
                **({"source_only_issue_refs": source_only} if source_only else {}),
            })
        return exported

    @staticmethod
    def _adr_source_relations(raw: dict) -> tuple[list[str], list[str]]:
        """``source_only_refs`` and ``unknown_relations`` of an ADR detail (DEVHUB-122).

        Both are additive keys: absent on a server older than ``3f0cf4b`` and ``[]`` when
        none was sent.  Any other shape is ``invalid_response`` (fail closed).
        """
        source_only = raw.get("source_only_refs", [])
        declared = raw.get("unknown_relations", [])
        if (not isinstance(source_only, list)
                or any(not isinstance(ref, str) for ref in source_only)
                or len(set(source_only)) != len(source_only)
                or not isinstance(declared, list)
                or any(name not in _ADR_UNKNOWN_FAMILIES for name in declared)
                or len(set(declared)) != len(declared)):
            raise DevHubTrackerError("GET", "/adrs", None, "invalid_response")
        return list(source_only), list(declared)

    def _read_adr_document(self, adr_id: str) -> dict:
        """Read one ADR with its `linked_issue_ids` (`devhub-adr-authoring.v1`)."""
        path = f"/adr-authoring/adrs/{urllib.parse.quote(adr_id, safe='')}"
        raw = self._req("GET", path, response_contract=_ADR_AUTHORING_CONTRACT)
        if (not isinstance(raw, dict)
                or raw.get("schema_version") != _ADR_AUTHORING_CONTRACT
                or not isinstance(raw.get("adr"), dict)
                or not isinstance(raw.get("linked_issue_ids"), list)
                or not all(isinstance(item, str) for item in raw["linked_issue_ids"])
                or not isinstance(raw["adr"].get("body"), str)):
            raise DevHubTrackerError("GET", path, None, "invalid_response")
        self._to_adr(raw["adr"])
        return raw

    def link_adr_issue(
        self, adr: Adr, issue_ref: str, project: Project | None = None,
    ) -> Adr:
        """Add one issue to a proposed ADR through the ADR edit route (PAT-312, DHN-33).

        Only a proposed ADR takes a new relation. A decided one is frozen by the server
        (403 `authority_boundary`, `2edcea2:src/tracker/store.ts:1966`): the refusal is
        typed, never a silent no-op, and the relation must be set before acceptance. A
        relation that already exists is returned without a write whatever the status (as
        Linear does).
        """
        project_key = self._require_adr_binding(adr.id, project)
        if (not isinstance(issue_ref, str) or _ISSUE_ID.fullmatch(issue_ref) is None
                or self._project_key(issue_ref) != project_key):
            raise ValueError("référence d'issue ADR DevHub invalide")
        server_id = self._adr_route_item(adr, project)[1]["id"]
        document = self._read_adr_document(server_id)
        if document["adr"].get("id") != server_id:
            raise DevHubTrackerError("GET", "/adr-authoring/adrs", None, "invalid_response")
        current = self._to_adr(document["adr"])
        linked = list(document["linked_issue_ids"])
        if issue_ref in linked:
            return current  # a replay is a no-op, also once the ADR is decided
        if current.status != "proposed":
            raise AdrRelationFrozenError(adr.id, current.status)
        path = f"/adr-authoring/adrs/{urllib.parse.quote(server_id, safe='')}"
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
                # The server answers this code for other causes than a decided ADR
                # (credential or capability): name a freeze only when a re-read shows one.
                reread = self._to_adr(self._read_adr_document(server_id)["adr"])
                if reread.status != "proposed":
                    raise AdrRelationFrozenError(adr.id, reread.status) from None
            if exc.status == 409 and exc.code == "version_conflict":
                raise TrackerConflictError(
                    "version ADR DevHub modifiée ; recharge l'ADR puis relance"
                ) from None
            raise
        if (not isinstance(raw, dict) or not isinstance(raw.get("adr"), dict)
                or raw["adr"].get("id") != server_id
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
        return self._adr_relationship_ids(self._adr_route_item(adr, project)[1]["id"])

    def _adr_relationship_ids(self, server_id: str) -> list[str]:
        """Page through ``GET /adrs/:id/relationships`` of one server ADR id (DHN-33)."""
        base = f"/adrs/{urllib.parse.quote(server_id, safe='')}/relationships"
        cursor, ids, seen = 0, [], set()
        for _page_number in range(1, _MAX_ADR_PAGES + 1):
            path = f"{base}?{urllib.parse.urlencode({'cursor': cursor, 'limit': 100})}"
            raw = self._req("GET", path, response_contract=_ADR_RELATIONSHIPS_CONTRACT)
            if (not isinstance(raw, dict)
                    or raw.get("schema_version") != _ADR_RELATIONSHIPS_CONTRACT
                    or not isinstance(raw.get("adr"), dict)
                    or raw["adr"].get("id") != server_id
                    or not isinstance(raw.get("items"), list)
                    or not isinstance(raw.get("page"), dict)
                    or not all(isinstance(i, dict) and isinstance(i.get("id"), str)
                               and _ISSUE_ID.fullmatch(i["id"]) is not None
                               for i in raw["items"])):
                raise DevHubTrackerError("GET", base, None, "invalid_response")
            page = raw["page"]
            if (type(page.get("truncation")) is not bool
                    or type(page.get("returned_count")) is not int
                    or page["returned_count"] != len(raw["items"])):
                raise DevHubTrackerError("GET", base, None, "invalid_response")
            for item in raw["items"]:
                if item["id"] in seen:
                    raise DevHubTrackerError("GET", base, None, "pagination_repeated")
                seen.add(item["id"])
                ids.append(item["id"])
                if len(ids) > _MAX_ADR_ITEMS:
                    raise DevHubTrackerError("GET", base, None, "pagination_limit")
            if not page["truncation"]:
                if page.get("cursor") is not None:
                    raise DevHubTrackerError("GET", base, None, "invalid_response")
                return ids
            next_cursor = page.get("cursor")
            if not raw["items"]:
                raise DevHubTrackerError("GET", base, None, "pagination_empty_page")
            if type(next_cursor) is not int or next_cursor <= cursor:
                raise DevHubTrackerError("GET", base, None, "pagination_stalled")
            cursor = next_cursor
        raise DevHubTrackerError("GET", base, None, "pagination_page_limit")
