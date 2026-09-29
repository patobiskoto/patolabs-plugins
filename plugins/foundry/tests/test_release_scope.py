"""PAT-59 portable release scope and factual changelog contract."""

from types import MethodType

import pytest

import foundry
from foundry import query, registry
from foundry.models import Issue, Project, ReleaseIssue, ReleaseScope
from foundry.trackers.base import ReleaseScopeUnavailableError
from foundry.trackers.ghprojects import (
    GitHubProjectsTracker, GitHubProjectsTrackerError, _Binding,
)
from foundry.trackers.linear import LinearTracker
from foundry.trackers.youtrack import YouTrackTracker, _YouTrackHTTPError


def _fact(identifier, disposition, *, kind="Feature", state="done", references=None):
    return ReleaseIssue(
        id=identifier,
        title=f"title {identifier}",
        type=kind,
        state=state,
        labels=("public",),
        disposition=disposition,
        references=references or {},
    )


def test_query_keeps_legacy_groups_and_exposes_all_factual_classes(monkeypatch):
    class Tracker:
        name = "fixture"

        def resolve_project(self, _repo):
            return Project("APP", "product-1")

        def read_release_scope(self, project, release):
            return ReleaseScope(
                self.name, project.key, project.id, release, "native-v1", "open",
                (
                    _fact("APP-1", "accepted", references={"merge_sha": "a" * 40}),
                    _fact("APP-2", "deviated", references={"override_reason": "human-override"}),
                    _fact("APP-3", "unfinished", state="review"),
                    _fact("APP-4", "unavailable", state="done"),
                ),
                {"mode": "operator", "action": "close-native-release"},
            )

    monkeypatch.setattr(foundry, "tracker", lambda name=None: Tracker())
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "app")

    payload = query.changelog("v1.0.0")

    assert payload["contract"] == "foundry.release-scope.v1"
    assert payload["count"] == 2
    assert payload["scope_count"] == 4
    assert payload["counts"] == {
        "accepted": 1, "deviated": 1, "unfinished": 1, "unavailable": 1,
    }
    assert [row["id"] for row in payload["groups"]["Feature"]] == ["APP-1", "APP-2"]
    assert payload["categories"]["unavailable"][0]["id"] == "APP-4"


def test_mapped_empty_release_is_distinct_from_missing_release(monkeypatch):
    class Tracker:
        name = "fixture"

        def resolve_project(self, _repo):
            return Project("APP", "product-1")

        def read_release_scope(self, project, release):
            if release != "v1.0.0":
                raise ReleaseScopeUnavailableError(self.name, release, "unmapped")
            return ReleaseScope(
                self.name, project.key, project.id, release, "native-v1", "open",
                (), {"mode": "operator"},
            )

    monkeypatch.setattr(foundry, "tracker", lambda name=None: Tracker())
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "app")

    assert query.changelog("v1.0.0")["scope_count"] == 0
    with pytest.raises(ReleaseScopeUnavailableError, match="unmapped"):
        query.changelog("v1.0.0-beta")


class _YouTrackRelease(YouTrackTracker):
    def __init__(self, native, issues):
        self.native, self.issues, self.searches, self.search_fields = (
            native, issues, [], [],
        )

    def _req(self, method, path, body=None, fields=None, top=None):
        assert method == "GET" and "/values/" in path
        return self.native

    def _search_raw(self, query_text, fields, top=1000):
        self.searches.append(query_text)
        self.search_fields.append(fields)
        return self.issues


def _youtrack_raw(
    identifier,
    release_id,
    release_name,
    *,
    project_id="project-app",
    project_key="APP",
    bundle_id="bundle-app",
    state="In Progress",
):
    return {
        "id": f"native-{identifier}",
        "idReadable": identifier,
        "summary": identifier,
        "description": "",
        "project": {"id": project_id, "shortName": project_key},
        "customFields": [
            {
                "name": "Milestone",
                "projectCustomField": {"bundle": {"id": bundle_id}},
                "value": {"id": release_id, "name": release_name},
            },
            {"name": "State", "value": {"id": "state-1", "name": state}},
            {"name": "Type", "value": {"id": "type-1", "name": "Feature"}},
        ],
        "links": [],
    }


def test_youtrack_uses_exact_product_mapping_and_near_version_coordinate():
    project = Project(
        "APP", "project-app",
        {"ms_bundle": "bundle-app", "release_ids": {
            "Release 1.0": "release-100", "Release 1.0 beta": "release-beta",
        }},
    )
    tracker = _YouTrackRelease(
        {"id": "release-100", "name": "Release 1.0"},
        [_youtrack_raw("APP-1", "release-100", "Release 1.0")],
    )

    scope = tracker.read_release_scope(project, "Release 1.0")

    assert scope.project_id == "project-app" and scope.release_id == "release-100"
    assert tracker.searches == ["project: {APP} Milestone: {Release 1.0}"]
    assert tracker.search_fields[0].count("customFields(") == 1
    assert "projectCustomField(bundle(id))" in tracker.search_fields[0]
    assert "project(id,shortName)" in tracker.search_fields[0]
    assert scope.issues[0].disposition == "unfinished"


@pytest.mark.parametrize("candidate", ["Release {1.0}", "Release } 1.0"])
def test_youtrack_refuses_unrepresentable_query_value_without_search(candidate):
    project = Project(
        "APP", "project-app",
        {"ms_bundle": "bundle-app", "release_ids": {candidate: "release-100"}},
    )
    tracker = _YouTrackRelease(
        {"id": "release-100", "name": candidate},
        [_youtrack_raw("APP-1", "release-100", candidate)],
    )

    with pytest.raises(
        ReleaseScopeUnavailableError, match="query_value_unrepresentable"
    ):
        tracker.read_release_scope(project, candidate)

    assert tracker.searches == []


@pytest.mark.parametrize(
    ("project_id", "bundle_id", "release_id"),
    [
        ("foreign-project", "bundle-app", "release-100"),
        ("project-app", "foreign-bundle", "release-100"),
        ("project-app", "bundle-app", "foreign-release"),
    ],
)
def test_youtrack_refuses_foreign_rows_even_after_server_filter(
    project_id, bundle_id, release_id,
):
    project = Project(
        "APP", "project-app",
        {"ms_bundle": "bundle-app", "release_ids": {"Release 1.0": "release-100"}},
    )
    tracker = _YouTrackRelease(
        {"id": "release-100", "name": "Release 1.0"},
        [_youtrack_raw(
            "APP-1",
            release_id,
            "Release 1.0",
            project_id=project_id,
            bundle_id=bundle_id,
        )],
    )

    with pytest.raises(ReleaseScopeUnavailableError, match="membership_mismatch"):
        tracker.read_release_scope(project, "Release 1.0")


def test_youtrack_native_done_and_pr_field_do_not_become_delivery_proof():
    project = Project(
        "OTHER", "project-other",
        {"ms_bundle": "bundle-other", "release_ids": {"v1.0.0": "release-other"}},
    )
    raw = _youtrack_raw(
        "OTHER-1",
        "release-other",
        "v1.0.0",
        project_id="project-other",
        project_key="OTHER",
        bundle_id="bundle-other",
        state="Done",
    )
    raw["customFields"].append({
        "name": "GitHub PR", "value": "https://example.invalid/prerequisite",
    })
    scope = _YouTrackRelease(
        {"id": "release-other", "name": "v1.0.0"}, [raw],
    ).read_release_scope(project, "v1.0.0")

    assert scope.issues[0].disposition == "unavailable"
    assert scope.issues[0].references["pr_url"].endswith("prerequisite")


def test_youtrack_permission_failure_is_explicit_and_sanitized():
    class Forbidden(YouTrackTracker):
        def __init__(self):
            pass

        def _req(self, method, path, body=None, fields=None, top=None):
            raise _YouTrackHTTPError(method, path, 403, "private provider detail")

    project = Project(
        "APP", "project-app",
        {"ms_bundle": "bundle", "release_ids": {"v1.0.0": "release-1"}},
    )
    with pytest.raises(ReleaseScopeUnavailableError, match="inaccessible") as error:
        Forbidden().read_release_scope(project, "v1.0.0")
    assert "private provider detail" not in str(error.value)


class _LinearPagedRelease(LinearTracker):
    def __init__(self):
        self.pages = 0

    def _mapped_release(self, project, release):
        return {
            "team_id": "team", "project_id": project.id,
        }, "milestone-v1"

    def _graphql(self, document, variables, operation):
        assert operation == "release.issues"
        self.pages += 1
        if variables["after"] is None:
            return {"issues": {
                "nodes": [{
                    "identifier": "APP-1", "project": {"id": "product-app"},
                    "team": {"id": "team"},
                    "projectMilestone": {"id": "milestone-v1"},
                }],
                "pageInfo": {"hasNextPage": True, "endCursor": "next"},
            }}
        return {"issues": {
            "nodes": [{
                "identifier": "APP-2", "project": {"id": "product-app"},
                "team": {"id": "team"},
                "projectMilestone": {"id": "milestone-v1"},
            }],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }}

    def _release_issue(self, raw, project):
        return _fact(raw["identifier"], "unfinished", state="ready")


def test_linear_release_membership_paginates_to_completion():
    tracker = _LinearPagedRelease()
    scope = tracker.read_release_scope(Project("APP", "product-app"), "v1.0.0")
    assert tracker.pages == 2
    assert [issue.id for issue in scope.issues] == ["APP-1", "APP-2"]
    assert scope.closure["native_capability"] == "unavailable"
    assert scope.closure["native_mutation"] is False


@pytest.mark.parametrize(
    ("project_id", "team_id", "milestone_id"),
    [
        ("foreign-product", "team", "milestone-v1"),
        ("product-app", "foreign-team", "milestone-v1"),
        ("product-app", "team", "near-milestone"),
    ],
)
def test_linear_refuses_foreign_rows_even_after_server_filter(
    project_id, team_id, milestone_id,
):
    tracker = _LinearPagedRelease()
    tracker._graphql = MethodType(lambda self, document, variables, operation: {
        "issues": {
            "nodes": [{
                "identifier": "APP-1", "project": {"id": project_id},
                "team": {"id": team_id},
                "projectMilestone": {"id": milestone_id},
            }],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
    }, tracker)

    with pytest.raises(ReleaseScopeUnavailableError, match="foreign_membership"):
        tracker.read_release_scope(Project("APP", "product-app"), "v1.0.0")


def test_linear_identity_uses_teams_connection_and_verifies_release_mapping():
    class IdentityLinear(LinearTracker):
        def __init__(self):
            self.operations = []

        def _graphql(self, document, variables, operation):
            self.operations.append((document, variables, operation))
            if operation == "project-binding-read":
                return {"project": {
                    "id": "project-uuid",
                    "teams": {
                        "nodes": [{"id": "team-uuid", "key": "APP"}],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    },
                }}
            return {"project": {
                "id": "project-uuid",
                "projectMilestones": {
                    "nodes": [{"id": "milestone-v1", "name": "v1.0.0"}],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                },
            }}

    project = Project("APP", "project-uuid", {
        "canonical_repo": "github.com/acme/app",
        "team_id": "team-uuid",
        "state_ids": {
            "backlog": "s-backlog", "ready": "s-ready",
            "in-progress": "s-progress", "review": "s-review",
            "blocked": "s-blocked", "done": "s-done", "dropped": "s-dropped",
        },
        "milestone_ids": {"v1.0.0": "milestone-v1"},
        "type_label_ids": {}, "label_ids": {},
    })
    tracker = IdentityLinear()

    assert tracker.verify_project_identity(project) is True
    assert "teams(filter:" in tracker.operations[0][0]
    assert [operation for _document, _variables, operation in tracker.operations] == [
        "project-binding-read", "release.read",
    ]


@pytest.mark.parametrize(
    ("override", "expected"), [(None, "accepted"), ("human-override", "deviated")],
)
def test_linear_exact_done_receipt_classifies_accepted_or_deviated(override, expected):
    tracker = object.__new__(LinearTracker)
    issue = Issue(
        id="APP-1", title="feature", type="Feature", state="done",
        native_state="done", projection_status="aligned",
    )
    tracker._to_issue = MethodType(lambda self, raw, project, observe_lifecycle=False: issue, tracker)
    tracker._lifecycle_projection = MethodType(lambda self, issue_id, raw, **kwargs: {
        "state": "done",
        "done": {
            "pr_url": "https://github.com/acme/app/pull/1",
            "head_sha": "a" * 40,
            "base_sha": "b" * 40,
            "merge_sha": "c" * 40,
            "review_digest": "d" * 64,
            "review_generation": 1,
        },
        "acceptance_override": override,
        "acceptance_by_generation": {1: {"proof": {"proof_id": "e" * 64}}},
    }, tracker)

    fact = tracker._release_issue({"id": "native-1"}, Project("APP", "product"))

    assert fact.disposition == expected
    assert fact.references["merge_sha"] == "c" * 40


def test_linear_historical_done_receipt_with_native_reopen_is_unavailable():
    tracker = object.__new__(LinearTracker)
    issue = Issue(
        id="APP-1", title="feature", type="Feature", state="done",
        native_state="in-progress", normalized_state="done",
        projection_status="disagreement",
    )
    tracker._to_issue = MethodType(
        lambda self, raw, project, observe_lifecycle=False: issue, tracker,
    )
    tracker._lifecycle_projection = MethodType(lambda self, issue_id, raw, **kwargs: {
        "state": "done",
        "done": {
            "pr_url": "https://github.com/acme/app/pull/1",
            "head_sha": "a" * 40, "base_sha": "b" * 40,
            "merge_sha": "c" * 40, "review_digest": "d" * 64,
            "review_generation": 1,
        },
        "acceptance_override": None,
        "acceptance_by_generation": {1: {"proof": {"proof_id": "e" * 64}}},
    }, tracker)

    fact = tracker._release_issue({"id": "native-1"}, Project("APP", "product"))

    assert fact.disposition == "unavailable"
    assert fact.references["merge_sha"] == "c" * 40


def test_github_repository_milestone_is_exact_and_done_is_not_proof(monkeypatch):
    project = Project(
        "GH", "PVT_product",
        {
            "owner": "acme", "number": "7", "canonical_repo": "github.com/acme/app",
            "release_ids": {"v1.0.0": "12", "v1.0.0-beta": "13"},
        },
    )
    binding = _Binding("acme", 7, "PVT_product", "acme/app", "GH")
    tracker = object.__new__(GitHubProjectsTracker)
    monkeypatch.setattr(tracker, "_authoritative_binding", lambda supplied: binding)
    monkeypatch.setattr(tracker, "_rest", lambda path, operation: {
        "id": 1200, "node_id": "MI_node", "number": 12,
        "title": "v1.0.0", "state": "open",
        "url": "https://api.github.com/repos/acme/app/milestones/12",
        "html_url": "https://github.com/acme/app/milestone/12",
    })
    row = {
        "id": 101, "node_id": "I_node", "number": 1, "title": "feature",
        "body": "", "labels": [], "repository_url": "https://api.github.com/repos/acme/app",
        "url": "https://api.github.com/repos/acme/app/issues/1",
        "html_url": "https://github.com/acme/app/issues/1",
        "milestone": {"number": 12, "title": "v1.0.0"},
    }
    monkeypatch.setattr(tracker, "_rows", lambda path, operation: [row])
    monkeypatch.setattr(
        tracker, "_search_raw",
        lambda supplied: [Issue(id="GH-1", title="feature", state="done", type="Feature")],
    )
    monkeypatch.setattr(tracker, "_hydrate_issue", lambda issue, supplied: issue)

    scope = tracker.read_release_scope(project, "v1.0.0")

    assert scope.release_id == "12"
    assert scope.coordinates == {
        "project_id": "PVT_product", "repository": "acme/app",
        "milestone_number": 12, "milestone_id": 1200,
        "milestone_node_id": "MI_node",
    }
    assert scope.issues[0].disposition == "unavailable"


def test_github_release_permission_failure_is_not_an_empty_scope(monkeypatch):
    project = Project(
        "GH", "PVT_product",
        {"owner": "acme", "number": "7", "canonical_repo": "github.com/acme/app",
         "release_ids": {"v1.0.0": "12"}},
    )
    tracker = object.__new__(GitHubProjectsTracker)
    monkeypatch.setattr(
        tracker, "_authoritative_binding",
        lambda supplied: _Binding("acme", 7, "PVT_product", "acme/app", "GH"),
    )
    monkeypatch.setattr(
        tracker, "_rest",
        lambda path, operation: (_ for _ in ()).throw(
            GitHubProjectsTrackerError(operation, "permission_denied")
        ),
    )
    with pytest.raises(ReleaseScopeUnavailableError, match="inaccessible_or_absent"):
        tracker.read_release_scope(project, "v1.0.0")


def test_registry_release_maps_are_provider_scoped_and_preserve_linear_milestones():
    canonical = "github.com/acme/app"
    yt = registry._validated_binding_entry(
        "youtrack", "app", "APP", "0-1", canonical,
        {"ms_bundle": "bundle", "release_ids": {"v1.0.0": "137-1"}},
    )
    gh = registry._validated_binding_entry(
        "ghprojects", "app", "APP", "PVT_product", canonical,
        {"owner": "acme", "number": "7", "release_ids": {"v1.0.0": "12"}},
    )
    assert yt["release_ids"] == {"v1.0.0": "137-1"}
    assert gh["release_ids"] == {"v1.0.0": "12"}

    with pytest.raises(ValueError, match="dupliqués"):
        registry._validated_binding_entry(
            "youtrack", "app", "APP", "0-1", canonical,
            {"ms_bundle": "bundle", "release_ids": {"v1": "same", "v2": "same"}},
        )


def test_registry_cli_decodes_release_map_for_non_linear_providers():
    parsed = registry._parse_extra_arguments(
        ['release_ids={"v1.0.0":"12"}', "owner=acme", "number=7"],
        "usage", tracker="ghprojects",
    )
    assert parsed["release_ids"] == {"v1.0.0": "12"}
