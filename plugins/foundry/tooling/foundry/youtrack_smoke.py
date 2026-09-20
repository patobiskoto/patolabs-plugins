"""Opt-in, destructive YouTrack smoke harness with provider-local cleanup.

The normal Tracker port intentionally has no delete operation.  Deletion here is
test infrastructure only and stays coupled to YouTrack's native paths.
"""
from __future__ import annotations

import os
import secrets
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Mapping

from foundry.models import Adr, Project
from foundry.trackers.youtrack import YouTrackTracker, _YouTrackHTTPError


_OPT_IN = "FOUNDRY_YOUTRACK_SMOKE"
_CONFIRM_TEST = "FOUNDRY_YOUTRACK_SMOKE_CONFIRM_TEST_PROJECT"
_PROJECT_KEY = "FOUNDRY_YOUTRACK_SMOKE_PROJECT_KEY"
_PROJECT_ID = "FOUNDRY_YOUTRACK_SMOKE_PROJECT_ID"
_ALLOW_FOUNDRY = "FOUNDRY_YOUTRACK_SMOKE_ALLOW_FOUNDRY"
_REQUIRED = ("YOUTRACK_URL", "YOUTRACK_TOKEN", _PROJECT_KEY, _PROJECT_ID, _CONFIRM_TEST)
_ISSUE_CLEANUP_FIELDS = "idReadable,summary,description,project(id,shortName)"


class SmokeDisabled(RuntimeError):
    """The destructive integration test was not explicitly opted into."""


class SmokeConfigurationError(RuntimeError):
    """Opt-in was present, but its safe target configuration was invalid."""


class SmokeRunError(RuntimeError):
    """Sanitized smoke failure; provider bodies and credentials are never retained."""


class _CleanupValidationError(RuntimeError):
    """An artifact could not be proved safe to delete."""


@dataclass(frozen=True)
class SmokeSettings:
    url: str
    token: str = field(repr=False)
    project: Project
    allow_foundry: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "SmokeSettings":
        env = os.environ if environ is None else environ
        missing = [name for name in _REQUIRED if not env.get(name, "").strip()]
        if missing:
            prefix = (
                f"YouTrack smoke disabled; set {_OPT_IN}=1 to opt in."
                if env.get(_OPT_IN) != "1"
                else "YouTrack smoke not run."
            )
            raise SmokeDisabled(
                prefix
                + " Missing required configuration: "
                + ", ".join(missing)
                + "."
            )
        if env.get(_OPT_IN) != "1":
            raise SmokeDisabled(
                f"YouTrack smoke disabled; set {_OPT_IN}=1 to opt in."
            )

        if env[_CONFIRM_TEST] != "1":
            raise SmokeConfigurationError(
                f"{_CONFIRM_TEST} must equal 1 to identify an intentionally disposable project."
            )

        key = env[_PROJECT_KEY].strip().upper()
        if key == "FOUNDRY" and env.get(_ALLOW_FOUNDRY) != "1":
            raise SmokeConfigurationError(
                "project FOUNDRY is refused by default; use a disposable test project "
                f"or set {_ALLOW_FOUNDRY}=1 as a separate explicit override."
            )
        return cls(
            url=env["YOUTRACK_URL"].strip().rstrip("/"),
            token=env["YOUTRACK_TOKEN"],
            project=Project(key=key, id=env[_PROJECT_ID].strip()),
            allow_foundry=env.get(_ALLOW_FOUNDRY) == "1",
        )


@dataclass(frozen=True)
class CleanupFailure:
    operation: str
    status: int | None
    error_type: str

    def __str__(self) -> str:
        status = f" HTTP {self.status}" if self.status is not None else ""
        return f"cleanup failure during {self.operation}:{status} {self.error_type}"


@dataclass(frozen=True)
class SmokeResult:
    run_id: str
    issue_ids: tuple[str, str]
    adr_id: str


def make_run_id() -> str:
    """Return an audit marker with enough entropy to avoid collisions and guessing."""
    return secrets.token_hex(16)


class YouTrackSmokeCleanup:
    """Best-effort cleanup using only YouTrack-specific DELETE operations."""

    def __init__(self, tracker: YouTrackTracker, project: Project, marker: str):
        self.tracker = tracker
        self.project = project
        self.marker = marker
        self._issue_ids: list[str] = []
        self._article_refs: list[str] = []
        self._validation_failures: list[CleanupFailure] = []

    def register_issue(self, issue_id: str) -> str | None:
        try:
            validated_id = self._validated_issue_id(issue_id)
        except BaseException as exc:
            self._validation_failures.append(
                self._problem("validate issue registration", exc)
            )
            return None
        if validated_id not in self._issue_ids:
            self._issue_ids.append(validated_id)
        return validated_id

    def register_article(self, article_ref: str) -> str | None:
        try:
            validated_ref = self._validated_article_ref(article_ref)
        except BaseException as exc:
            self._validation_failures.append(
                self._problem("validate article registration", exc)
            )
            return None
        if validated_ref not in self._article_refs:
            self._article_refs.append(validated_ref)
        return validated_ref

    @staticmethod
    def _problem(operation: str, exc: BaseException) -> CleanupFailure:
        status = exc.status if isinstance(exc, _YouTrackHTTPError) else None
        return CleanupFailure(operation, status, type(exc).__name__)

    def _validated_issue_id(self, issue_id: str) -> str:
        """Return only the id supplied by a fresh, project-and-marker-safe read."""
        encoded = urllib.parse.quote(issue_id, safe="")
        raw = self.tracker._req(
            "GET", f"/issues/{encoded}", fields=_ISSUE_CLEANUP_FIELDS
        )
        if not isinstance(raw, dict):
            raise _CleanupValidationError()
        raw_project = raw.get("project")
        if not isinstance(raw_project, dict):
            raise _CleanupValidationError()
        if (
            raw_project.get("id") != self.project.id
            or raw_project.get("shortName") != self.project.key
        ):
            raise _CleanupValidationError()
        validated_id = raw.get("idReadable")
        if not isinstance(validated_id, str) or not validated_id:
            raise _CleanupValidationError()
        marked_values = (raw.get("summary"), raw.get("description"))
        if not any(
            isinstance(value, str) and self.marker in value
            for value in marked_values
        ):
            raise _CleanupValidationError()
        return validated_id

    def _validated_article_ref(
        self, article_ref: str, *, missing_ok: bool = False
    ) -> str | None:
        """Prove an article ref through the configured project's ADR listing."""
        if not isinstance(article_ref, str) or not article_ref:
            raise _CleanupValidationError()
        adr = next(
            (
                candidate
                for candidate in self.tracker.list_adrs(self.project)
                if candidate.ref == article_ref
            ),
            None,
        )
        if adr is None:
            if missing_ok:
                return None
            raise _CleanupValidationError()
        if self.marker not in adr.title and self.marker not in (adr.body or ""):
            raise _CleanupValidationError()
        if not isinstance(adr.ref, str) or not adr.ref:
            raise _CleanupValidationError()
        return adr.ref

    def _take_validation_failures(self) -> list[CleanupFailure]:
        failures = self._validation_failures
        self._validation_failures = []
        return failures

    def _discover(self) -> list[CleanupFailure]:
        """Recover ids when a create succeeded server-side but its response failed."""
        failures = []
        try:
            for issue in self.tracker.search(self.project, f'summary: "{self.marker}"'):
                if self.marker in issue.title or self.marker in (issue.body or ""):
                    self.register_issue(issue.id)
        except BaseException as exc:
            failures.append(self._problem("discover issues", exc))
        try:
            for adr in self.tracker.list_adrs(self.project):
                if self.marker in adr.title or self.marker in (adr.body or ""):
                    if adr.ref:
                        self.register_article(adr.ref)
        except BaseException as exc:
            failures.append(self._problem("discover articles", exc))
        return failures

    def _delete(self, kind: str, native_id: str) -> CleanupFailure | None:
        operation = f"DELETE {kind}"
        try:
            encoded = urllib.parse.quote(native_id, safe="")
            path = f"/{kind}/{encoded}"
            self.tracker._req("DELETE", path)
        except _YouTrackHTTPError as exc:
            if exc.status == 404:
                return None
            return self._problem(operation, exc)
        except BaseException as exc:
            return self._problem(operation, exc)
        return None

    def _delete_issue(self, issue_id: str) -> CleanupFailure | None:
        try:
            validated_id = self._validated_issue_id(issue_id)
        except _YouTrackHTTPError as exc:
            if exc.status == 404:
                return None
            return self._problem("validate issue deletion", exc)
        except BaseException as exc:
            return self._problem("validate issue deletion", exc)
        return self._delete("issues", validated_id)

    def _delete_article(self, article_ref: str) -> CleanupFailure | None:
        try:
            validated_ref = self._validated_article_ref(
                article_ref, missing_ok=True
            )
        except BaseException as exc:
            return self._problem("validate article deletion", exc)
        if validated_ref is None:
            return None
        return self._delete("articles", validated_ref)

    def cleanup(self) -> list[CleanupFailure]:
        failures = self._take_validation_failures()
        failures.extend(self._discover())
        failures.extend(self._take_validation_failures())
        for article_ref in reversed(self._article_refs):
            failure = self._delete_article(article_ref)
            if failure:
                failures.append(failure)
        for issue_id in reversed(self._issue_ids):
            failure = self._delete_issue(issue_id)
            if failure:
                failures.append(failure)
        return failures


def _find_adr(adrs: list[Adr], adr_id: str) -> Adr | None:
    return next((item for item in adrs if item.id == adr_id), None)


def _assert_marker(value: str | None, marker: str, artifact: str) -> None:
    if marker not in (value or ""):
        raise RuntimeError(f"{artifact} read-back did not contain the run marker")


def _cleanup_note(failures) -> str:
    safe = []
    for failure in failures:
        if isinstance(failure, CleanupFailure):
            safe.append(str(failure))
        else:
            safe.append(f"cleanup failure: {type(failure).__name__}")
    return "; ".join(safe)


def run_smoke(
    settings: SmokeSettings,
    *,
    tracker_factory: Callable[[], YouTrackTracker] | None = None,
    cleanup_factory: Callable[..., YouTrackSmokeCleanup] = YouTrackSmokeCleanup,
    run_id_factory: Callable[[], str] = make_run_id,
) -> SmokeResult:
    """Exercise Foundry's real YouTrack API surface and always clean its artifacts."""
    # Re-check the production guard before constructing the networked tracker.  Tests
    # may construct SmokeSettings directly instead of using from_env().
    if settings.project.key.upper() == "FOUNDRY" and not settings.allow_foundry:
        raise SmokeConfigurationError("project FOUNDRY is refused before any mutation")

    tracker_factory = tracker_factory or (
        lambda: YouTrackTracker(url=settings.url, token=settings.token)
    )
    tracker = tracker_factory()
    run_id = run_id_factory()
    marker = f"[foundry-smoke:{run_id}]"
    cleanup = cleanup_factory(tracker, settings.project, marker)
    stage = "authentication/project lookup"
    result = None
    primary = None

    try:
        # Confirm native id + key before the first mutation. Registry/cwd resolution
        # is intentionally never consulted. The reads also prove authentication.
        if not tracker.verify_project_identity(settings.project):
            raise RuntimeError("configured project id and key do not match")
        tracker.search(settings.project, f'summary: "{marker}"')

        stage = "create/read parent issue"
        parent = tracker.create_issue(
            settings.project,
            f"{marker} smoke parent",
            f"{marker}\nTemporary parent created by the YouTrack smoke test.",
            fields={"State": "backlog", "Type": "Task"},
        )
        parent_id = cleanup.register_issue(parent.id)
        if parent_id is None:
            raise RuntimeError("parent issue cleanup validation failed")
        _assert_marker(tracker.get_issue(parent_id).title, marker, "parent issue")

        stage = "create/read child issue"
        child = tracker.create_issue(
            settings.project,
            f"{marker} smoke child",
            f"{marker}\nTemporary child created by the YouTrack smoke test.",
            fields={"State": "backlog", "Type": "Task"},
        )
        child_id = cleanup.register_issue(child.id)
        if child_id is None:
            raise RuntimeError("child issue cleanup validation failed")
        _assert_marker(tracker.get_issue(child_id).title, marker, "child issue")

        stage = "link"
        tracker.link(child_id, "subtask-of", parent_id)
        linked = tracker.get_issue(child_id)
        if not any(link.target == parent_id for link in linked.links):
            raise RuntimeError("issue link was not visible on read-back")

        stage = "transition"
        tracker.set_state(child_id, "in-progress")
        if tracker.get_issue(child_id).state != "in-progress":
            raise RuntimeError("issue transition was not visible on read-back")

        stage = "comment"
        comment = f"{marker} smoke comment"
        tracker.add_comment(child_id, comment)
        comments = tracker.get_issue(child_id).comments
        if not any(marker in (item.get("text") or "") for item in comments):
            raise RuntimeError("issue comment was not visible on read-back")

        stage = "create/read ADR"
        tracker.list_adrs(settings.project)
        adr = tracker.create_adr(
            settings.project,
            f"{marker} smoke decision",
            f"{marker}\nTemporary ADR created by the YouTrack smoke test.",
            status="proposed",
        )
        validated_adr_ref = (
            cleanup.register_article(adr.ref) if adr.ref else None
        )
        if validated_adr_ref is None:
            raise RuntimeError("ADR cleanup validation failed")
        adr.ref = validated_adr_ref
        read_adr = _find_adr(tracker.list_adrs(settings.project), adr.id)
        if read_adr is None or read_adr.status != "proposed":
            raise RuntimeError("proposed ADR was not visible on read-back")
        _assert_marker(read_adr.title, marker, "ADR")

        stage = "accept/read ADR"
        tracker.set_adr_status(adr, "accepted")
        accepted = _find_adr(tracker.list_adrs(settings.project), adr.id)
        if accepted is None or accepted.status != "accepted":
            raise RuntimeError("accepted ADR status was not visible on read-back")

        result = SmokeResult(run_id, (parent_id, child_id), adr.id)
    except BaseException as exc:
        primary = SmokeRunError(f"smoke stage '{stage}' failed ({type(exc).__name__})")
    finally:
        try:
            cleanup_failures = cleanup.cleanup()
        except BaseException as exc:
            cleanup_failures = [YouTrackSmokeCleanup._problem("cleanup", exc)]

    if primary is not None:
        if cleanup_failures:
            primary.add_note(_cleanup_note(cleanup_failures))
        raise primary from None
    if cleanup_failures:
        raise SmokeRunError(_cleanup_note(cleanup_failures))
    assert result is not None
    return result
