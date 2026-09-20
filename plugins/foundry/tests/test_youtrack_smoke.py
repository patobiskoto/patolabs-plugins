from __future__ import annotations

import itertools
import re
import urllib.parse
from pathlib import Path

import pytest

from foundry.models import Adr, Issue, Link, Project
from foundry.youtrack_smoke import (
    SmokeConfigurationError,
    SmokeDisabled,
    SmokeSettings,
    YouTrackSmokeCleanup,
    make_run_id,
    run_smoke,
)
from foundry.trackers.youtrack import YouTrackTracker, _YouTrackHTTPError


BASE_ENV = {
    "FOUNDRY_YOUTRACK_SMOKE": "1",
    "FOUNDRY_YOUTRACK_SMOKE_CONFIRM_TEST_PROJECT": "1",
    "FOUNDRY_YOUTRACK_SMOKE_PROJECT_KEY": "FTEST",
    "FOUNDRY_YOUTRACK_SMOKE_PROJECT_ID": "0-42",
    "YOUTRACK_URL": "https://youtrack.invalid",
    "YOUTRACK_TOKEN": "token-sentinel-must-not-leak",
}

def test_youtrack_advertises_bounded_acceptance_sync():
    tracker = object.__new__(YouTrackTracker)

    assert tracker.acceptance_sync_supported is True


def _ci_workflow() -> str:
    return (Path(__file__).parents[3] / ".github/workflows/ci.yml").read_text()


def _workflow_job(workflow: str, name: str) -> str:
    """Return one top-level workflow job without coupling the job order."""
    tail = workflow.split(f"  {name}:", 1)[1]
    return re.split(r"\n  [A-Za-z][A-Za-z0-9-]*:", tail, maxsplit=1)[0]


class FakeTracker:
    def __init__(self, fail_at: str | None = None, identity_matches: bool = True):
        self.fail_at = fail_at
        self.identity_matches = identity_matches
        self.calls = []
        self._issues = {}
        self._adrs = []
        self._counter = itertools.count(1)

    def _record(self, name, *args):
        self.calls.append((name, *args))
        if self.fail_at == name:
            raise _YouTrackHTTPError("POST", f"/{name}", 500, "response-secret-sentinel")

    def verify_project_identity(self, project):
        self._record("verify_project_identity", project)
        return self.identity_matches

    def _req(self, method, path, body=None, fields=None, top=None):
        self._record("_req", method, path, body, fields, top)
        if method == "GET" and path.startswith("/issues/"):
            issue_id = urllib.parse.unquote(path.removeprefix("/issues/"))
            issue = self._issues.get(issue_id)
            if issue is None:
                raise _YouTrackHTTPError("GET", path, 404, "missing-secret")
            return {
                "idReadable": issue.id,
                "summary": issue.title,
                "description": issue.body,
                "project": {"id": "0-42", "shortName": "FTEST"},
            }
        if method == "DELETE" and path.startswith("/issues/"):
            issue_id = urllib.parse.unquote(path.removeprefix("/issues/"))
            self._issues.pop(issue_id, None)
        if method == "DELETE" and path.startswith("/articles/"):
            article_ref = urllib.parse.unquote(path.removeprefix("/articles/"))
            self._adrs = [adr for adr in self._adrs if adr.ref != article_ref]

    def search(self, project, query=""):
        self._record("search", project, query)
        return list(self._issues.values())

    def create_issue(self, project, title, body, fields=None, parent=None):
        self._record("create_issue", project, title, body, fields, parent)
        issue = Issue(
            id=f"FTEST-{next(self._counter)}",
            title=title,
            body=body,
            state=(fields or {}).get("State"),
        )
        self._issues[issue.id] = issue
        return issue

    def get_issue(self, issue_id):
        self._record("get_issue", issue_id)
        return self._issues[issue_id]

    def link(self, src_id, link_type, dst_id):
        self._record("link", src_id, link_type, dst_id)
        self._issues[src_id].links.append(
            Link(type=link_type, direction="outward", target=dst_id)
        )

    def set_state(self, issue_id, state):
        self._record("set_state", issue_id, state)
        self._issues[issue_id].state = state

    def add_comment(self, issue_id, text):
        self._record("add_comment", issue_id, text)
        self._issues[issue_id].comments.append({"text": text})

    def list_adrs(self, project):
        self._record("list_adrs", project)
        return list(self._adrs)

    def create_adr(self, project, title, body, status="proposed"):
        self._record("create_adr", project, title, body, status)
        adr = Adr(
            id=f"FTEST-ADR-{len(self._adrs) + 1:04d}",
            title=title,
            body=body,
            status=status,
            ref=f"article-{len(self._adrs) + 1}",
        )
        self._adrs.append(adr)
        return adr

    def set_adr_status(self, adr, status):
        self._record("set_adr_status", adr, status)
        adr.status = status


class RecordingCleanup:
    def __init__(self, tracker, project, marker):
        self.tracker = tracker
        self.project = project
        self.marker = marker
        self.issues = []
        self.articles = []
        self.cleaned = False

    def register_issue(self, issue_id):
        self.issues.append(issue_id)
        return issue_id

    def register_article(self, article_ref):
        self.articles.append(article_ref)
        return article_ref

    def cleanup(self):
        self.cleaned = True
        return []


def test_disabled_smoke_has_a_precise_skip_reason():
    with pytest.raises(SmokeDisabled) as exc:
        SmokeSettings.from_env({})

    reason = str(exc.value)
    assert "FOUNDRY_YOUTRACK_SMOKE=1" in reason
    assert "YOUTRACK_TOKEN" in reason
    assert "FOUNDRY_YOUTRACK_SMOKE_PROJECT_KEY" in reason


@pytest.mark.parametrize(
    "missing",
    [
        "YOUTRACK_URL",
        "YOUTRACK_TOKEN",
        "FOUNDRY_YOUTRACK_SMOKE_PROJECT_KEY",
        "FOUNDRY_YOUTRACK_SMOKE_PROJECT_ID",
        "FOUNDRY_YOUTRACK_SMOKE_CONFIRM_TEST_PROJECT",
    ],
)
def test_enabled_but_incomplete_smoke_skips_without_leaking_values(missing):
    env = {k: v for k, v in BASE_ENV.items() if k != missing}

    with pytest.raises(SmokeDisabled, match=missing) as exc:
        SmokeSettings.from_env(env)

    rendered = f"{exc.value!s}\n{exc.value!r}"
    assert "token-sentinel-must-not-leak" not in rendered
    assert "https://youtrack.invalid" not in rendered


def test_present_but_invalid_confirmation_remains_fail_closed():
    env = {
        **BASE_ENV,
        "FOUNDRY_YOUTRACK_SMOKE_CONFIRM_TEST_PROJECT": "not-confirmed-secret",
    }

    with pytest.raises(SmokeConfigurationError) as exc:
        SmokeSettings.from_env(env)

    rendered = f"{exc.value!s}\n{exc.value!r}"
    assert "must equal 1" in rendered
    assert "not-confirmed-secret" not in rendered


def test_production_key_is_refused_before_tracker_or_mutation():
    env = {**BASE_ENV, "FOUNDRY_YOUTRACK_SMOKE_PROJECT_KEY": "FOUNDRY"}
    tracker_built = False

    def tracker_factory():
        nonlocal tracker_built
        tracker_built = True
        return FakeTracker()

    with pytest.raises(SmokeConfigurationError, match="FOUNDRY"):
        settings = SmokeSettings.from_env(env)
        run_smoke(settings, tracker_factory=tracker_factory)

    assert not tracker_built


def test_production_key_requires_a_second_explicit_override():
    env = {
        **BASE_ENV,
        "FOUNDRY_YOUTRACK_SMOKE_PROJECT_KEY": "FOUNDRY",
        "FOUNDRY_YOUTRACK_SMOKE_ALLOW_FOUNDRY": "1",
    }

    assert SmokeSettings.from_env(env).project.key == "FOUNDRY"


def test_direct_production_settings_are_refused_before_tracker_construction():
    settings = SmokeSettings(
        url="https://youtrack.invalid",
        token="token-sentinel-must-not-leak",
        project=Project(key="FOUNDRY", id="production-id"),
    )
    tracker_built = False

    def tracker_factory():
        nonlocal tracker_built
        tracker_built = True
        return FakeTracker()

    with pytest.raises(SmokeConfigurationError, match="before any mutation"):
        run_smoke(settings, tracker_factory=tracker_factory)

    assert not tracker_built


def test_settings_repr_redacts_token():
    settings = SmokeSettings.from_env(BASE_ENV)

    assert "token-sentinel-must-not-leak" not in repr(settings)


def test_run_ids_are_unpredictable_unique_and_log_safe():
    ids = {make_run_id() for _ in range(100)}

    assert len(ids) == 100
    assert all(len(value) >= 20 and value.replace("-", "").isalnum() for value in ids)


def test_mismatched_native_project_id_stops_before_mutation():
    settings = SmokeSettings.from_env(BASE_ENV)
    tracker = FakeTracker(identity_matches=False)

    with pytest.raises(RuntimeError, match="authentication/project lookup"):
        run_smoke(settings, tracker_factory=lambda: tracker)

    names = [call[0] for call in tracker.calls]
    assert "create_issue" not in names
    assert "link" not in names
    assert "set_state" not in names


@pytest.mark.parametrize(
    ("foreign_at", "failed_stage"),
    [
        (1, "create/read parent issue"),
        (2, "create/read child issue"),
    ],
)
def test_foreign_created_issue_id_stops_before_followup_mutations(
    foreign_at, failed_stage
):
    settings = SmokeSettings.from_env(BASE_ENV)

    class ForeignCreateResponseTracker(FakeTracker):
        def __init__(self):
            super().__init__()
            self.create_count = 0

        def create_issue(self, project, title, body, fields=None, parent=None):
            created = super().create_issue(project, title, body, fields, parent)
            self.create_count += 1
            if self.create_count != foreign_at:
                return created
            foreign = Issue(
                id="OTHER-9",
                title=title,
                body=body,
                state=(fields or {}).get("State"),
            )
            self._issues[foreign.id] = foreign
            return foreign

        def _req(self, method, path, body=None, fields=None, top=None):
            if method == "GET" and path == "/issues/OTHER-9":
                self._record("_req", method, path, body, fields, top)
                foreign = self._issues["OTHER-9"]
                return {
                    "idReadable": foreign.id,
                    "summary": foreign.title,
                    "description": foreign.body,
                    "project": {"id": "0-foreign", "shortName": "OTHER"},
                }
            return super()._req(method, path, body, fields, top)

        def search(self, project, query=""):
            self._record("search", project, query)
            return [
                issue
                for issue_id, issue in self._issues.items()
                if issue_id.startswith("FTEST-")
            ]

    tracker = ForeignCreateResponseTracker()

    with pytest.raises(RuntimeError, match=failed_stage):
        run_smoke(
            settings,
            tracker_factory=lambda: tracker,
            run_id_factory=lambda: "unique-run-id-0123456789",
        )

    names = [call[0] for call in tracker.calls]
    assert names.count("create_issue") == foreign_at
    assert "link" not in names
    assert "set_state" not in names
    assert "add_comment" not in names
    assert ("get_issue", "OTHER-9") not in tracker.calls
    assert [
        call[2]
        for call in tracker.calls
        if call[0] == "_req" and call[1] == "DELETE"
    ] == [f"/issues/FTEST-{number}" for number in range(foreign_at, 0, -1)]
    assert "OTHER-9" in tracker._issues


def test_foreign_created_article_ref_stops_before_status_mutation():
    settings = SmokeSettings.from_env(BASE_ENV)

    class ForeignAdrResponseTracker(FakeTracker):
        def create_adr(self, project, title, body, status="proposed"):
            created = super().create_adr(project, title, body, status)
            return Adr(
                id=created.id,
                title=created.title,
                body=created.body,
                status=created.status,
                ref="article-foreign-valid",
            )

    tracker = ForeignAdrResponseTracker()

    with pytest.raises(RuntimeError, match="create/read ADR"):
        run_smoke(
            settings,
            tracker_factory=lambda: tracker,
            run_id_factory=lambda: "unique-run-id-0123456789",
        )

    assert not any(call[0] == "set_adr_status" for call in tracker.calls)
    delete_paths = [
        call[2]
        for call in tracker.calls
        if call[0] == "_req" and call[1] == "DELETE"
    ]
    assert delete_paths == [
        "/articles/article-1",
        "/issues/FTEST-2",
        "/issues/FTEST-1",
    ]
    assert "/articles/article-foreign-valid" not in delete_paths


def test_cycle_uses_public_tracker_api_and_marks_every_created_artifact():
    settings = SmokeSettings.from_env(BASE_ENV)
    tracker = FakeTracker()
    cleanup_holder = []

    def cleanup_factory(tr, project, marker):
        cleanup = RecordingCleanup(tr, project, marker)
        cleanup_holder.append(cleanup)
        return cleanup

    result = run_smoke(
        settings,
        tracker_factory=lambda: tracker,
        cleanup_factory=cleanup_factory,
        run_id_factory=lambda: "unique-run-id-0123456789",
    )

    names = [call[0] for call in tracker.calls]
    assert names == [
        "verify_project_identity",
        "search",
        "create_issue",
        "get_issue",
        "create_issue",
        "get_issue",
        "link",
        "get_issue",
        "set_state",
        "get_issue",
        "add_comment",
        "get_issue",
        "list_adrs",
        "create_adr",
        "list_adrs",
        "set_adr_status",
        "list_adrs",
    ]
    marker = "[foundry-smoke:unique-run-id-0123456789]"
    issues = [call for call in tracker.calls if call[0] == "create_issue"]
    assert all(marker in call[2] and marker in call[3] for call in issues)
    comment = next(call for call in tracker.calls if call[0] == "add_comment")
    assert marker in comment[2]
    adr = next(call for call in tracker.calls if call[0] == "create_adr")
    assert marker in adr[2] and marker in adr[3]
    assert result.run_id == "unique-run-id-0123456789"
    assert cleanup_holder[0].issues == ["FTEST-1", "FTEST-2"]
    assert cleanup_holder[0].articles == ["article-1"]
    assert cleanup_holder[0].cleaned


def test_default_tracker_uses_only_credentials_validated_by_settings(monkeypatch):
    settings = SmokeSettings.from_env(BASE_ENV)
    built_with = []

    class ExplicitCredentialTracker(FakeTracker):
        def __init__(self, *, url, token):
            super().__init__()
            built_with.append((url, token))

    monkeypatch.setattr(
        "foundry.youtrack_smoke.YouTrackTracker", ExplicitCredentialTracker
    )
    monkeypatch.setenv("YOUTRACK_URL", "https://ambient.invalid")
    monkeypatch.setenv("YOUTRACK_TOKEN", "ambient-token-sentinel")

    run_smoke(
        settings,
        cleanup_factory=RecordingCleanup,
        run_id_factory=lambda: "unique-run-id-0123456789",
    )

    assert built_with == [
        ("https://youtrack.invalid", "token-sentinel-must-not-leak")
    ]


def test_youtrack_provider_accepts_explicit_credentials_without_ambient_reads(
    monkeypatch,
):
    monkeypatch.setenv("YOUTRACK_URL", "https://ambient.invalid")
    monkeypatch.setenv("YOUTRACK_TOKEN", "ambient-token-sentinel")

    tracker = YouTrackTracker(
        url="https://validated.invalid/", token="validated-token-sentinel"
    )

    assert tracker.url == "https://validated.invalid"
    assert tracker.token == "validated-token-sentinel"


def test_youtrack_provider_keeps_ambient_configuration_compatibility(monkeypatch):
    monkeypatch.setenv("YOUTRACK_URL", "https://ambient.invalid/")
    monkeypatch.setenv("YOUTRACK_TOKEN", "ambient-token-sentinel")

    tracker = YouTrackTracker()

    assert tracker.url == "https://ambient.invalid"
    assert tracker.token == "ambient-token-sentinel"


class _RedirectResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return b"{}"


class _RedirectingOpener:
    def __init__(self, handler, target):
        self.handler = handler
        self.target = target
        self.requests = []

    def open(self, request):
        self.requests.append(request)
        redirected = self.handler.redirect_request(
            request, None, 302, "Found", {}, self.target
        )
        self.requests.append(redirected)
        return _RedirectResponse()


@pytest.mark.parametrize(
    "target",
    [
        "/api/redirected",
        "https://youtrack.invalid:443/api/redirected",
    ],
)
def test_youtrack_follows_same_origin_redirect_with_bearer_auth(
    monkeypatch, target
):
    built = []

    def build_opener(handler):
        opener = _RedirectingOpener(handler, target)
        built.append(opener)
        return opener

    monkeypatch.setattr(
        "foundry.trackers.youtrack.urllib.request.build_opener", build_opener
    )
    tracker = YouTrackTracker(
        url="https://youtrack.invalid", token="token-sentinel-must-not-leak"
    )

    assert tracker._req("GET", "/redirect") == {}

    assert len(built) == 1
    assert len(built[0].requests) == 2
    assert built[0].requests[1].get_header("Authorization") == (
        "Bearer token-sentinel-must-not-leak"
    )


@pytest.mark.parametrize(
    "target",
    [
        "https://other.invalid/api/redirected",
        "http://youtrack.invalid/api/redirected",
        "https://youtrack.invalid:444/api/redirected",
        "https://[invalid/api/redirected",
    ],
)
def test_youtrack_refuses_cross_origin_redirect_before_second_request(
    monkeypatch, target
):
    built = []

    def build_opener(handler):
        opener = _RedirectingOpener(handler, target)
        built.append(opener)
        return opener

    monkeypatch.setattr(
        "foundry.trackers.youtrack.urllib.request.build_opener", build_opener
    )
    tracker = YouTrackTracker(
        url="https://youtrack.invalid", token="token-sentinel-must-not-leak"
    )

    with pytest.raises(RuntimeError, match="cross-origin") as exc:
        tracker._req(
            "POST",
            "/redirect",
            {"value": "request-body-sentinel-must-not-leak"},
        )

    rendered = f"{exc.value!s}\n{exc.value!r}"
    assert len(built) == 1
    assert len(built[0].requests) == 1
    assert "token-sentinel-must-not-leak" not in rendered
    assert "request-body-sentinel-must-not-leak" not in rendered
    assert target not in rendered


class RawRequestTracker:
    def __init__(self, *, issues=None, adrs=None, failures=None, search_results=None):
        self.calls = []
        self.list_calls = []
        self.issues = dict(issues or {})
        self.adrs = list(adrs or [])
        self.failures = failures or {}
        self.search_results = list(search_results or [])

    def _req(self, method, path, body=None, fields=None, top=None):
        self.calls.append((method, path, body, fields, top))
        failure = self.failures.get((method, path))
        if failure:
            raise failure
        if path.startswith("/issues/"):
            issue_id = urllib.parse.unquote(path.removeprefix("/issues/"))
            if method == "GET":
                raw = self.issues.get(issue_id)
                if raw is None:
                    raise _YouTrackHTTPError("GET", path, 404, "missing-secret")
                return dict(raw)
            if method == "DELETE":
                self.issues.pop(issue_id, None)
        if method == "DELETE" and path.startswith("/articles/"):
            article_ref = urllib.parse.unquote(path.removeprefix("/articles/"))
            self.adrs = [adr for adr in self.adrs if adr.ref != article_ref]
        return None

    def search(self, project, query=""):
        return list(self.search_results)

    def list_adrs(self, project):
        self.list_calls.append(project)
        return list(self.adrs)


def _cleanup_issue(
    issue_id: str,
    *,
    marker: str = "[foundry-smoke:run]",
    project_id: str = "0-42",
    project_key: str = "FTEST",
):
    return {
        "idReadable": issue_id,
        "summary": f"{marker} smoke issue",
        "description": f"{marker}\ntemporary",
        "project": {"id": project_id, "shortName": project_key},
    }


def _cleanup_adr(
    article_ref: str,
    *,
    marker: str = "[foundry-smoke:run]",
    number: int = 1,
):
    return Adr(
        id=f"FTEST-ADR-{number:04d}",
        title=f"FTEST-ADR-{number:04d} — {marker} smoke decision",
        body=f"{marker}\ntemporary",
        status="proposed",
        ref=article_ref,
    )


def test_provider_verifies_the_explicit_project_id_and_key_without_registry():
    tracker = object.__new__(YouTrackTracker)
    calls = []

    def request(method, path, body=None, fields=None, top=None):
        calls.append((method, path, fields))
        return {"id": "0/42", "shortName": "FTEST"}

    tracker._req = request
    settings = SmokeSettings.from_env({
        **BASE_ENV,
        "FOUNDRY_YOUTRACK_SMOKE_PROJECT_ID": "0/42",
    })

    assert tracker.verify_project_identity(settings.project)
    assert calls == [("GET", "/admin/projects/0%2F42", "id,shortName")]


def test_cleanup_uses_provider_specific_delete_paths_in_reverse_order():
    marker = "[foundry-smoke:run]"
    tracker = RawRequestTracker(
        issues={
            "FTEST/1": _cleanup_issue("FTEST/1"),
            "FTEST-2": _cleanup_issue("FTEST-2"),
        },
        adrs=[_cleanup_adr("article/1")],
    )
    settings = SmokeSettings.from_env(BASE_ENV)
    cleanup = YouTrackSmokeCleanup(tracker, settings.project, marker)
    cleanup.register_issue("FTEST/1")
    cleanup.register_issue("FTEST-2")
    cleanup.register_article("article/1")

    assert cleanup.cleanup() == []
    delete_calls = [
        (method, path)
        for method, path, *_ in tracker.calls
        if method == "DELETE"
    ]
    assert delete_calls == [
        ("DELETE", "/articles/article%2F1"),
        ("DELETE", "/issues/FTEST-2"),
        ("DELETE", "/issues/FTEST%2F1"),
    ]
    issue_calls = [
        (method, path, fields)
        for method, path, _body, fields, _top in tracker.calls
        if path.startswith("/issues/")
    ]
    assert issue_calls == [
        ("GET", "/issues/FTEST%2F1", "idReadable,summary,description,project(id,shortName)"),
        ("GET", "/issues/FTEST-2", "idReadable,summary,description,project(id,shortName)"),
        ("GET", "/issues/FTEST-2", "idReadable,summary,description,project(id,shortName)"),
        ("DELETE", "/issues/FTEST-2", None),
        ("GET", "/issues/FTEST%2F1", "idReadable,summary,description,project(id,shortName)"),
        ("DELETE", "/issues/FTEST%2F1", None),
    ]
    assert len(tracker.list_calls) >= 2


def test_cleanup_treats_404_as_success_and_continues_after_other_failures():
    not_found = _YouTrackHTTPError("DELETE", "/issues/FTEST-2", 404, "gone-secret")
    server_error = _YouTrackHTTPError("DELETE", "/articles/a-1", 500, "raw-secret")
    tracker = RawRequestTracker(
        issues={
            "FTEST-1": _cleanup_issue("FTEST-1"),
            "FTEST-2": _cleanup_issue("FTEST-2"),
        },
        adrs=[_cleanup_adr("a-1")],
        failures={
            ("DELETE", "/articles/a-1"): server_error,
            ("DELETE", "/issues/FTEST-2"): not_found,
        },
    )
    settings = SmokeSettings.from_env(BASE_ENV)
    cleanup = YouTrackSmokeCleanup(tracker, settings.project, "[foundry-smoke:run]")
    cleanup.register_issue("FTEST-1")
    cleanup.register_issue("FTEST-2")
    cleanup.register_article("a-1")

    errors = cleanup.cleanup()

    assert [
        call[1] for call in tracker.calls if call[0] == "DELETE"
    ] == [
        "/articles/a-1",
        "/issues/FTEST-2",
        "/issues/FTEST-1",
    ]
    assert len(errors) == 1
    assert errors[0].status == 500
    assert "raw-secret" not in str(errors[0])
    assert "gone-secret" not in str(errors[0])


def test_cleanup_is_idempotent_when_artifacts_are_already_gone():
    not_found = _YouTrackHTTPError("DELETE", "/issues/FTEST-1", 404, "gone-secret")
    tracker = RawRequestTracker(
        issues={"FTEST-1": _cleanup_issue("FTEST-1")},
        failures={("DELETE", "/issues/FTEST-1"): not_found},
    )
    settings = SmokeSettings.from_env(BASE_ENV)
    cleanup = YouTrackSmokeCleanup(tracker, settings.project, "[foundry-smoke:run]")
    cleanup.register_issue("FTEST-1")

    assert cleanup.cleanup() == []
    assert cleanup.cleanup() == []
    assert [
        call[1] for call in tracker.calls if call[0] == "DELETE"
    ] == [
        "/issues/FTEST-1",
        "/issues/FTEST-1",
    ]


def test_mistyped_cleanup_id_is_sanitized_and_does_not_stop_later_deletes(capsys):
    tracker = RawRequestTracker(
        issues={"FTEST-1": _cleanup_issue("FTEST-1")}
    )
    settings = SmokeSettings.from_env(BASE_ENV)
    cleanup = YouTrackSmokeCleanup(tracker, settings.project, "[foundry-smoke:run]")
    cleanup.register_article(42)
    cleanup.register_issue("FTEST-1")

    failures = cleanup.cleanup()

    captured = capsys.readouterr()
    rendered = f"{failures!s}\n{failures!r}\n{captured.out}\n{captured.err}"
    assert len(failures) == 1
    assert failures[0].error_type == "_CleanupValidationError"
    assert [
        (call[0], call[1]) for call in tracker.calls if call[0] == "DELETE"
    ] == [
        ("DELETE", "/issues/FTEST-1")
    ]
    assert "token-sentinel-must-not-leak" not in rendered


def test_created_issue_id_read_back_in_another_project_is_never_deleted():
    tracker = RawRequestTracker(
        issues={
            "OTHER-9": _cleanup_issue(
                "OTHER-9", project_id="0-foreign", project_key="OTHER"
            ),
            "FTEST-1": _cleanup_issue("FTEST-1"),
        }
    )
    settings = SmokeSettings.from_env(BASE_ENV)
    cleanup = YouTrackSmokeCleanup(
        tracker, settings.project, "[foundry-smoke:run]"
    )

    cleanup.register_issue("OTHER-9")
    cleanup.register_issue("FTEST-1")
    failures = cleanup.cleanup()

    assert [
        path for method, path, *_ in tracker.calls if method == "DELETE"
    ] == ["/issues/FTEST-1"]
    assert "OTHER-9" in tracker.issues
    assert len(failures) == 1
    assert failures[0].operation == "validate issue registration"


def test_foreign_article_ref_is_never_deleted_and_safe_cleanup_continues():
    tracker = RawRequestTracker(adrs=[_cleanup_adr("article-safe")])
    settings = SmokeSettings.from_env(BASE_ENV)
    cleanup = YouTrackSmokeCleanup(
        tracker, settings.project, "[foundry-smoke:run]"
    )

    # The ref can be provider-valid while remaining absent from this project's
    # project-scoped article list. It must never become a DELETE candidate.
    cleanup.register_article("article-foreign-valid")
    cleanup.register_article("article-safe")
    failures = cleanup.cleanup()

    assert [
        path for method, path, *_ in tracker.calls if method == "DELETE"
    ] == ["/articles/article-safe"]
    assert len(failures) == 1
    assert failures[0].operation == "validate article registration"


def test_unmarked_project_artifacts_are_rejected_while_safe_cleanup_continues():
    tracker = RawRequestTracker(
        issues={
            "FTEST-unsafe": _cleanup_issue("FTEST-unsafe", marker=""),
            "FTEST-safe": _cleanup_issue("FTEST-safe"),
        },
        adrs=[
            _cleanup_adr("article-unsafe", marker="", number=1),
            _cleanup_adr("article-safe", number=2),
        ],
    )
    settings = SmokeSettings.from_env(BASE_ENV)
    cleanup = YouTrackSmokeCleanup(
        tracker, settings.project, "[foundry-smoke:run]"
    )

    cleanup.register_issue("FTEST-unsafe")
    cleanup.register_article("article-unsafe")
    cleanup.register_issue("FTEST-safe")
    cleanup.register_article("article-safe")
    failures = cleanup.cleanup()

    assert [
        path for method, path, *_ in tracker.calls if method == "DELETE"
    ] == ["/articles/article-safe", "/issues/FTEST-safe"]
    assert "FTEST-unsafe" in tracker.issues
    assert [adr.ref for adr in tracker.adrs] == ["article-unsafe"]
    assert len(failures) == 2
    assert {failure.operation for failure in failures} == {
        "validate issue registration",
        "validate article registration",
    }


def test_cleanup_deletes_the_reread_issue_id_not_the_create_response_id():
    reread = _cleanup_issue("FTEST-canonical")
    tracker = RawRequestTracker(
        issues={
            "create-response-alias": reread,
            "FTEST-canonical": reread,
        }
    )
    settings = SmokeSettings.from_env(BASE_ENV)
    cleanup = YouTrackSmokeCleanup(
        tracker, settings.project, "[foundry-smoke:run]"
    )

    cleanup.register_issue("create-response-alias")

    assert cleanup.cleanup() == []
    assert [
        path for method, path, *_ in tracker.calls if method == "DELETE"
    ] == ["/issues/FTEST-canonical"]


def test_cleanup_revalidates_marker_immediately_before_each_delete():
    tracker = RawRequestTracker(
        issues={"FTEST-1": _cleanup_issue("FTEST-1")},
        adrs=[_cleanup_adr("article-1")],
    )
    settings = SmokeSettings.from_env(BASE_ENV)
    cleanup = YouTrackSmokeCleanup(
        tracker, settings.project, "[foundry-smoke:run]"
    )
    cleanup.register_issue("FTEST-1")
    cleanup.register_article("article-1")

    tracker.issues["FTEST-1"] = _cleanup_issue("FTEST-1", marker="")
    tracker.adrs = [_cleanup_adr("article-1", marker="")]
    failures = cleanup.cleanup()

    assert not any(method == "DELETE" for method, *_ in tracker.calls)
    assert {failure.operation for failure in failures} == {
        "validate issue deletion",
        "validate article deletion",
    }


def test_cleanup_failure_does_not_mask_primary_failure_or_leak_secrets(capsys):
    settings = SmokeSettings.from_env(BASE_ENV)
    tracker = FakeTracker(fail_at="link")

    class FailingCleanup(RecordingCleanup):
        def cleanup(self):
            self.cleaned = True
            return [RuntimeError("cleanup-secret-sentinel")]

    with pytest.raises(RuntimeError) as exc:
        run_smoke(
            settings,
            tracker_factory=lambda: tracker,
            cleanup_factory=FailingCleanup,
            run_id_factory=lambda: "unique-run-id-0123456789",
        )

    captured = capsys.readouterr()
    notes = "\n".join(getattr(exc.value, "__notes__", []))
    rendered = f"{exc.value}\n{notes}\n{captured.out}\n{captured.err}"
    assert "smoke stage 'link' failed" in rendered
    assert "cleanup failure" in rendered
    assert "token-sentinel-must-not-leak" not in rendered
    assert "response-secret-sentinel" not in rendered
    assert "cleanup-secret-sentinel" not in rendered


@pytest.mark.parametrize("primary_failure", [False, True])
def test_raising_cleanup_is_sanitized_and_never_masks_primary(
    primary_failure, capsys
):
    settings = SmokeSettings.from_env(BASE_ENV)
    tracker = FakeTracker(fail_at="link" if primary_failure else None)

    class RaisingCleanup(RecordingCleanup):
        def cleanup(self):
            raise RuntimeError("raising-cleanup-secret-sentinel")

    with pytest.raises(RuntimeError) as exc:
        run_smoke(
            settings,
            tracker_factory=lambda: tracker,
            cleanup_factory=RaisingCleanup,
            run_id_factory=lambda: "unique-run-id-0123456789",
        )

    captured = capsys.readouterr()
    notes = "\n".join(getattr(exc.value, "__notes__", []))
    rendered = (
        f"{exc.value!s}\n{exc.value!r}\n{notes}\n{captured.out}\n{captured.err}"
    )
    if primary_failure:
        assert "smoke stage 'link' failed" in rendered
    else:
        assert "cleanup failure" in rendered
    assert "raising-cleanup-secret-sentinel" not in rendered
    assert "token-sentinel-must-not-leak" not in rendered
    assert "response-secret-sentinel" not in rendered


def test_finally_rediscovers_an_issue_if_create_response_fails():
    settings = SmokeSettings.from_env(BASE_ENV)

    class LostCreateResponseTracker(FakeTracker):
        def create_issue(self, project, title, body, fields=None, parent=None):
            super().create_issue(project, title, body, fields, parent)
            raise _YouTrackHTTPError("POST", "/issues", 502, "response-secret-sentinel")

    tracker = LostCreateResponseTracker()

    with pytest.raises(RuntimeError, match="create/read parent issue") as exc:
        run_smoke(settings, tracker_factory=lambda: tracker)

    delete_calls = [
        call
        for call in tracker.calls
        if call[0] == "_req" and call[1] == "DELETE"
    ]
    assert [(call[1], call[2]) for call in delete_calls] == [
        ("DELETE", "/issues/FTEST-1")
    ]
    assert "response-secret-sentinel" not in str(exc.value)


def test_public_ci_executes_only_internal_prs_on_ephemeral_secret_free_runners():
    workflow = _ci_workflow()
    public_jobs = {
        "foundry": "ubuntu-24.04",
        "ship-ios": "macos-14",
        "catalogue": "ubuntu-24.04",
    }

    assert "pull_request:" in workflow.split("jobs:", 1)[0]
    assert "pull_request_target:" not in workflow
    assert "self-hosted" not in workflow
    assert "secrets." not in workflow
    for name, runner in public_jobs.items():
        job = _workflow_job(workflow, name)
        assert "github.event.pull_request.head.repo.fork == false" in job
        assert f"runs-on: {runner}" in job
    assert "Compile tooling" in _workflow_job(workflow, "foundry")
    assert "Lint" in _workflow_job(workflow, "foundry")
    assert "Validate both marketplaces" in _workflow_job(workflow, "catalogue")
