"""Transport-bound regression tests for PAT-57's GitHub Projects read slice."""
import json
import subprocess
from copy import deepcopy

import pytest

import foundry
from foundry import query
from foundry.models import Issue, Project
from foundry.registry import RepositoryTrackerBinding
from foundry.trackers.base import IssueUnavailableError
from foundry.trackers.ghprojects import GitHubProjectsTracker, GitHubProjectsTrackerError


PROJECT = Project(
    key="GHQUAL", id="PVT_project",
    extra={"owner": "patobiskoto", "number": "7",
           "canonical_repo": "github.com/patobiskoto/foundry-v1-ghprojects-sandbox"},
)


@pytest.fixture(autouse=True)
def _qualified_active_binding(monkeypatch):
    """Exercise the public authority gate against a complete V1 binding."""
    binding = RepositoryTrackerBinding(
        tracker="ghprojects",
        repository=PROJECT.extra["canonical_repo"],
        project=PROJECT,
        registry_binding_digest="sha256:" + "1" * 64,
        migration_manifest_digest=None,
        configuration_digest="sha256:" + "2" * 64,
        activation_kind="bootstrap",
    )
    monkeypatch.setattr(
        "foundry.trackers.ghprojects.registry.repository_tracker_selection",
        lambda cwd=None: {"tracker": "ghprojects", "mode": "v1", "binding": binding},
    )


def _page(item_id, number, cursor, more, *, adr=False):
    labels = [{"name": "foundry:adr"}] if adr else []
    return {"data": {"user": {"projectV2": {
        "id": "PVT_project", "number": 7, "public": False,
        "fields": {"nodes": [
            {"id": "state", "name": "Foundry normalized state", "dataType": "SINGLE_SELECT",
             "options": [{"id": f"state-{name}", "name": name} for name in (
                 "backlog", "ready", "in-progress", "review", "blocked", "done", "dropped",
             )]},
            {"id": "type", "name": "Foundry type", "dataType": "SINGLE_SELECT",
             "options": [{"id": f"type-{name.casefold()}", "name": name}
                         for name in ("Bug", "Feature", "Task", "Epic")]},
            {"id": "priority", "name": "Foundry priority", "dataType": "SINGLE_SELECT",
             "options": [{"id": f"priority-{name.casefold()}", "name": name}
                         for name in ("P1", "P2")]},
            {"id": "estimate", "name": "Foundry estimate", "dataType": "NUMBER"},
        ], "pageInfo": {"hasNextPage": False, "endCursor": None}},
        "items": {"nodes": [{"id": item_id, "type": "ISSUE", "content": {
            "__typename": "Issue", "id": f"issue-node-{number}", "number": number,
            "title": f"issue {number}", "body": "UTF-8 é", "repository": {
                "id": "repo-node", "nameWithOwner": "patobiskoto/foundry-v1-ghprojects-sandbox",
                "isPrivate": True, "owner": {"__typename": "User", "login": "patobiskoto"},
            },
            "labels": {"nodes": labels, "pageInfo": {"hasNextPage": False, "endCursor": None}},
        }, "fieldValues": {"nodes": [
            {"__typename": "ProjectV2ItemFieldSingleSelectValue",
             "field": {"id": "state", "name": "Foundry normalized state"},
             "optionId": "state-ready", "name": "ready"},
            {"__typename": "ProjectV2ItemFieldSingleSelectValue",
             "field": {"id": "type", "name": "Foundry type"},
             "optionId": "type-task", "name": "Task"},
            {"__typename": "ProjectV2ItemFieldSingleSelectValue",
             "field": {"id": "priority", "name": "Foundry priority"},
             "optionId": "priority-p1", "name": "P1"},
            {"__typename": "ProjectV2ItemFieldNumberValue",
             "field": {"id": "estimate", "name": "Foundry estimate"}, "number": 3.0},
        ], "pageInfo": {"hasNextPage": False, "endCursor": None}}}], "pageInfo": {"hasNextPage": more, "endCursor": cursor}},
    }}}}


def _rest_issue(number):
    repo = "patobiskoto/foundry-v1-ghprojects-sandbox"
    api = f"https://api.github.com/repos/{repo}"
    return {
        "id": 1000 + number, "number": number, "body": "UTF-8 é", "labels": [],
        "repository_url": api, "url": f"{api}/issues/{number}",
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "created_at": "2026-09-27T12:00:00Z", "updated_at": "2026-09-27T12:01:00Z",
    }


def test_search_pages_graphql_and_excludes_only_reserved_adr_support():
    replies = [_page("item-1", 1, "next", True), _page("item-adr", 3, None, False, adr=True)]

    def runner(command, **kwargs):
        path = command[-1]
        if "/comments" in path or "/dependencies/" in path or "/sub_issues" in path:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        if path.endswith("/parent"):
            return subprocess.CompletedProcess(
                command, 1,
                json.dumps({"message": "No parent issue found", "status": "404"}),
                "gh: No parent issue found (HTTP 404)",
            )
        if "/issues/1" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(1)), "")
        return subprocess.CompletedProcess(command, 0, json.dumps(replies.pop(0)), "")

    issues = GitHubProjectsTracker(runner=runner).search(PROJECT)
    assert [(issue.id, issue.title, issue.state, issue.estimate) for issue in issues] == [
        ("GHQUAL-1", "issue 1", "ready", 3.0)
    ]


def test_get_issue_uses_bound_graphql_coordinates_and_expected_rest_paths():
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[2] == "graphql":
            return subprocess.CompletedProcess(command, 0, json.dumps(_page("item-1", 1, None, False)), "")
        path = command[-1]
        if path == "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1":
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(1)), "")
        if path.endswith("/parent"):
            return subprocess.CompletedProcess(
                command, 1,
                json.dumps({"message": "No parent issue found", "status": "404"}),
                "gh: No parent issue found (HTTP 404)",
            )
        return subprocess.CompletedProcess(command, 0, "[]", "")

    issue = GitHubProjectsTracker(runner=runner).get_issue("GHQUAL-1")

    assert issue.id == "GHQUAL-1"
    graphql = calls[0]
    assert "login=patobiskoto" in graphql
    assert "number=7" in graphql
    assert "optionId" in graphql[4]
    assert "repository{id nameWithOwner isPrivate" in graphql[4]
    rest_paths = [command[-1] for command in calls[1:]]
    assert rest_paths == [
        "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1",
        "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1/comments?per_page=100&page=1",
        "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1/parent",
        "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1/dependencies/blocked_by?per_page=100&page=1",
        "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1/dependencies/blocking?per_page=100&page=1",
        "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1/sub_issues?per_page=100&page=1",
    ]


@pytest.mark.parametrize("labels", ["missing", None, {}, [{}], ["foundry:adr"],
                                   [{"name": ""}], [{"name": "x"}, {"name": "x"}]])
def test_get_issue_refuses_ambiguous_rest_labels_before_other_reads(labels):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[2] == "graphql":
            payload = _page("item-1", 1, None, False)
        else:
            assert command[-1].endswith("/issues/1")
            payload = _rest_issue(1)
            if labels == "missing":
                payload.pop("labels")
            else:
                payload["labels"] = labels
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match="invalid_labels"):
        GitHubProjectsTracker(runner=runner).get_issue("GHQUAL-1")
    assert len(calls) == 2


def test_rest_adr_discrimination_remains_explicit_after_graphql_read():
    def runner(command, **kwargs):
        if command[2] == "graphql":
            payload = _page("item-1", 1, None, False)
        else:
            assert command[-1].endswith("/issues/1")
            payload = _rest_issue(1)
            payload["labels"] = [{"name": "foundry:adr"}, {"name": "preserve"}]
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    from foundry.trackers.base import TrackerCapabilityUnavailableError

    with pytest.raises(TrackerCapabilityUnavailableError, match="adr_issue_read"):
        GitHubProjectsTracker(runner=runner).get_issue("GHQUAL-1")


@pytest.mark.parametrize("value", ["2026-09-27", "2026-09-27T12:00:00",
                                   "2026-09-27 12:00:00"])
def test_timestamp_refuses_dates_and_times_without_explicit_timezone(value):
    with pytest.raises(GitHubProjectsTrackerError, match="invalid_timestamp"):
        GitHubProjectsTracker._timestamp(value, "issue.read")


@pytest.mark.parametrize("value", ["2026-09-27T12:00:00Z",
                                   "2026-09-27T14:00:00+02:00",
                                   "2026-09-27T07:00:00-05:00"])
def test_timestamp_normalizes_explicit_offsets_to_same_epoch(value):
    assert GitHubProjectsTracker._timestamp(value, "issue.read") == 1790510400000


@pytest.mark.parametrize("read", ["search", "get_issue"])
def test_public_reads_count_complete_rest_body_acceptance_checkboxes(read):
    body = (
        "## Critères d'acceptation\n\n"
        "- [x] lower-case done\n"
        "* [X] upper-case done\n"
        "+ [ ] still open\n"
    )

    def runner(command, **kwargs):
        if command[2] == "graphql":
            return subprocess.CompletedProcess(
                command, 0, json.dumps(_page("item-1", 1, None, False)), "",
            )
        path = command[-1]
        if path.endswith("/issues/1"):
            raw = _rest_issue(1)
            raw["body"] = body
            return subprocess.CompletedProcess(command, 0, json.dumps(raw), "")
        if path.endswith("/parent"):
            return subprocess.CompletedProcess(
                command, 1,
                json.dumps({"message": "No parent issue found", "status": "404"}),
                "gh: No parent issue found (HTTP 404)",
            )
        return subprocess.CompletedProcess(command, 0, "[]", "")

    tracker = GitHubProjectsTracker(runner=runner)
    issue = tracker.search(PROJECT)[0] if read == "search" else tracker.get_issue("GHQUAL-1")

    assert issue.body == body
    assert (issue.ac_done, issue.ac_total) == (2, 3)


def test_get_issue_ignores_comments_and_split_line_pseudo_checkboxes():
    body = "No acceptance criteria here.\n- [x]\nnot one criterion\n-\n [ ] neither is this\n"

    def runner(command, **kwargs):
        if command[2] == "graphql":
            return subprocess.CompletedProcess(
                command, 0, json.dumps(_page("item-1", 1, None, False)), "",
            )
        path = command[-1]
        if path.endswith("/issues/1"):
            raw = _rest_issue(1)
            raw["body"] = body
            return subprocess.CompletedProcess(command, 0, json.dumps(raw), "")
        if "/comments" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps([{
                "id": 88,
                "body": "- [X] a comment checkbox is not issue acceptance progress",
                "created_at": "2026-09-27T12:02:00Z",
            }]), "")
        if path.endswith("/parent"):
            return subprocess.CompletedProcess(
                command, 1,
                json.dumps({"message": "No parent issue found", "status": "404"}),
                "gh: No parent issue found (HTTP 404)",
            )
        return subprocess.CompletedProcess(command, 0, "[]", "")

    issue = GitHubProjectsTracker(runner=runner).get_issue("GHQUAL-1")

    assert issue.body == body
    assert (issue.ac_done, issue.ac_total) == (0, 0)
    assert issue.comments[0]["text"].startswith("- [X]")


def test_search_refuses_duplicate_cursor_item_and_graphql_partial_data():
    replies = [_page("item-1", 1, "next", True), _page("item-1", 1, None, False)]

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(replies.pop(0)), "")

    with pytest.raises(GitHubProjectsTrackerError, match="pagination_stalled"):
        GitHubProjectsTracker(runner=runner).search(PROJECT)

    def partial(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps({"data": {}, "errors": [{"message": "denied"}]}), "")

    with pytest.raises(GitHubProjectsTrackerError, match="ambiguous_graphql_response"):
        GitHubProjectsTracker(runner=partial).search(PROJECT)


def test_search_refuses_repeated_cursor_even_when_items_are_distinct():
    replies = [_page("item-1", 1, "same", True), _page("item-2", 2, "same", True)]

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(replies.pop(0)), "")

    with pytest.raises(GitHubProjectsTrackerError, match="pagination_stalled"):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


def test_search_refuses_missing_or_ambiguous_qualified_field():
    payload = _page("item-1", 1, None, False)
    payload["data"]["user"]["projectV2"]["fields"]["nodes"].append(
        {"id": "another-state", "name": "Foundry normalized state"}
    )

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match="missing_or_ambiguous_field:state"):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize("coordinate", ["field", "option"])
def test_search_refuses_empty_authority_ids_even_when_values_match(coordinate):
    payload = _page("item-1", 1, None, False)
    project = payload["data"]["user"]["projectV2"]
    state_field = next(field for field in project["fields"]["nodes"] if field["id"] == "state")
    state_value = project["items"]["nodes"][0]["fieldValues"]["nodes"][0]
    if coordinate == "field":
        state_field["id"] = ""
        state_value["field"]["id"] = ""
        reason = "missing_or_ambiguous_field:state"
    else:
        next(option for option in state_field["options"] if option["name"] == "ready")["id"] = ""
        state_value["optionId"] = ""
        reason = "invalid_field_options:state"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner)._search_raw(PROJECT)


@pytest.mark.parametrize("semantic", ["state", "priority", "estimate"])
def test_search_refuses_qualified_optional_field_value_without_id(semantic):
    payload = _page("item-1", 1, None, False)
    values = payload["data"]["user"]["projectV2"]["items"]["nodes"][0]["fieldValues"]["nodes"]
    next(value for value in values if value["field"]["id"] == semantic)["field"].pop("id")

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match="incoherent_field_value"):
        GitHubProjectsTracker(runner=runner)._search_raw(PROJECT)


@pytest.mark.parametrize("malformation", ["null_node", "null_field", "empty_field_id"])
def test_search_refuses_malformed_qualified_field_value_coordinates(malformation):
    payload = _page("item-1", 1, None, False)
    values = payload["data"]["user"]["projectV2"]["items"]["nodes"][0]["fieldValues"]["nodes"]
    priority_index = next(
        index for index, value in enumerate(values) if value["field"]["id"] == "priority"
    )
    if malformation == "null_node":
        values[priority_index] = None
    elif malformation == "null_field":
        values[priority_index]["field"] = None
    else:
        values[priority_index]["field"]["id"] = ""

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match="incoherent_field_value"):
        GitHubProjectsTracker(runner=runner)._search_raw(PROJECT)


@pytest.mark.parametrize(
    "failure", ["missing_field", "missing_value", "missing_option", "duplicate_option"],
)
def test_search_refuses_incomplete_or_ambiguous_field_contract(failure):
    payload = _page("item-1", 1, None, False)
    project = payload["data"]["user"]["projectV2"]
    if failure == "missing_field":
        project["fields"]["nodes"] = [
            field for field in project["fields"]["nodes"] if field["id"] != "type"
        ]
        reason = "missing_or_ambiguous_field:type"
    elif failure == "missing_value":
        project["items"]["nodes"][0]["fieldValues"]["nodes"] = [
            value for value in project["items"]["nodes"][0]["fieldValues"]["nodes"]
            if value["field"]["id"] != "type"
        ]
        reason = "invalid_type"
    elif failure == "missing_option":
        state_field = next(field for field in project["fields"]["nodes"] if field["id"] == "state")
        state_field["options"] = [
            option for option in state_field["options"] if option["name"] != "blocked"
        ]
        reason = "invalid_field_options:state"
    else:
        type_field = next(field for field in project["fields"]["nodes"] if field["id"] == "type")
        type_field["options"].append({"id": "type-task", "name": "Bug"})
        reason = "invalid_field_options:type"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize("coordinate", ["field_name", "option_id"])
def test_search_refuses_incoherent_field_value_coordinates(coordinate):
    payload = _page("item-1", 1, None, False)
    value = payload["data"]["user"]["projectV2"]["items"]["nodes"][0]["fieldValues"]["nodes"][0]
    if coordinate == "field_name":
        value["field"]["name"] = "Foundry type"
        reason = "incoherent_field_value"
    else:
        value["optionId"] = "state-other"
        reason = "invalid_option_value:state"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"type": "DRAFT_ISSUE", "content": {"__typename": "DraftIssue"}}, "unsupported_item_type"),
        ({"type": "PULL_REQUEST", "content": {"__typename": "PullRequest"}}, "unsupported_item_type"),
        ({"project.public": True}, "foreign_or_unqualified_project"),
        ({"content.repository.nameWithOwner": "patobiskoto/foreign"}, "foreign_or_unqualified_repository"),
        ({"content.repository.isPrivate": False}, "foreign_or_unqualified_repository"),
        ({"content.repository.owner.__typename": "Organization"}, "foreign_or_unqualified_repository"),
    ],
)
def test_search_refuses_unsupported_or_unqualified_project_items(mutation, reason):
    payload = _page("item-1", 1, None, False)
    project = payload["data"]["user"]["projectV2"]
    item = project["items"]["nodes"][0]
    for path, value in mutation.items():
        parts = path.split(".")
        target = project if parts[0] == "project" else item
        if parts[0] == "project":
            parts = parts[1:]
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize(
    ("duplicate", "reason"),
    [
        ("number", "duplicate_issue_id"),
        ("content", "duplicate_issue_content_id"),
    ],
)
def test_search_refuses_duplicate_issue_coordinates(duplicate, reason):
    payload = _page("item-1", 1, None, False)
    first = payload["data"]["user"]["projectV2"]["items"]["nodes"][0]
    second = deepcopy(first)
    second["id"] = "item-2"
    if duplicate == "number":
        second["content"]["id"] = "issue-node-other"
    else:
        second["content"]["number"] = 2
    payload["data"]["user"]["projectV2"]["items"]["nodes"].append(second)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize("collision", ["project_issue", "field_option"])
def test_search_refuses_conflated_native_identifier_namespaces(collision):
    payload = _page("item-1", 1, None, False)
    project = payload["data"]["user"]["projectV2"]
    if collision == "project_issue":
        project["items"]["nodes"][0]["content"]["id"] = "PVT_project"
        reason = "conflated_native_ids"
    else:
        project["fields"]["nodes"][0]["options"][0]["id"] = "type"
        reason = "missing_or_ambiguous_field:type"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


def test_search_refuses_field_catalog_drift_between_pages():
    first = _page("item-1", 1, "next", True)
    second = _page("item-2", 2, None, False)
    second["data"]["user"]["projectV2"]["fields"]["nodes"][0]["options"][0]["id"] = "state-ready-v2"
    second["data"]["user"]["projectV2"]["items"]["nodes"][0]["fieldValues"]["nodes"][0]["optionId"] = "state-ready-v2"
    replies = [first, second]

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(replies.pop(0)), "")

    with pytest.raises(GitHubProjectsTrackerError, match="ambiguous_field_catalog"):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize("coordinate", ["key", "project_id", "owner", "number", "repository"])
def test_search_refuses_foreign_input_binding_before_transport(coordinate):
    foreign = deepcopy(PROJECT)
    if coordinate == "key":
        foreign.key = "OTHER"
    elif coordinate == "project_id":
        foreign.id = "PVT_foreign"
    elif coordinate == "owner":
        foreign.extra["owner"] = "foreign"
        foreign.extra["canonical_repo"] = "github.com/foreign/foundry-v1-ghprojects-sandbox"
    elif coordinate == "number":
        foreign.extra["number"] = "8"
    else:
        foreign.extra["canonical_repo"] = "github.com/patobiskoto/foreign"

    def runner(command, **kwargs):
        pytest.fail(f"foreign binding reached transport: {command}")

    with pytest.raises(GitHubProjectsTrackerError, match="foreign_project_binding"):
        GitHubProjectsTracker(runner=runner).search(foreign)


def test_search_refuses_foreign_active_provider_before_transport(monkeypatch):
    monkeypatch.setattr(
        "foundry.trackers.ghprojects.registry.repository_tracker_selection",
        lambda: {"tracker": "linear", "mode": "legacy", "project": PROJECT},
    )

    def runner(command, **kwargs):
        pytest.fail(f"foreign provider reached transport: {command}")

    with pytest.raises(GitHubProjectsTrackerError, match="foreign_tracker"):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize(
    ("stderr", "body", "reason"),
    [
        ("gh: HTTP 401", {}, "authentication_failed"),
        ("gh: HTTP 403", {}, "permission_denied"),
        ("", {"status": "429", "message": "Too many requests"}, "rate_limited"),
    ],
)
def test_search_classifies_bounded_transport_failures(stderr, body, reason):
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, json.dumps(body), stderr)

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


def test_search_classifies_transport_launch_failure():
    def runner(command, **kwargs):
        raise OSError("synthetic transport launch failure")

    with pytest.raises(GitHubProjectsTrackerError, match="transport_failed"):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize("connection, reason", [
    ("fields", "truncated_fields"),
    ("labels", "truncated_or_invalid_labels"),
    ("fieldValues", "truncated_field_values"),
])
def test_search_refuses_truncated_authority_connections(connection, reason):
    payload = _page("item-1", 1, None, False)
    project = payload["data"]["user"]["projectV2"]
    if connection == "fields":
        project[connection]["pageInfo"]["hasNextPage"] = True
    elif connection == "labels":
        project["items"]["nodes"][0]["content"][connection]["pageInfo"]["hasNextPage"] = True
    else:
        project["items"]["nodes"][0][connection]["pageInfo"]["hasNextPage"] = True

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize(
    ("connection", "malformation", "reason"),
    [
        ("fields", "missing", "missing_fields"),
        ("fields", "page_info", "truncated_fields"),
        ("labels", "missing", "truncated_or_invalid_labels"),
        ("labels", "page_info", "truncated_or_invalid_labels"),
        ("fieldValues", "missing", "invalid_field_values"),
        ("fieldValues", "page_info", "truncated_field_values"),
    ],
)
def test_search_refuses_missing_or_malformed_authority_connections(
    connection, malformation, reason,
):
    payload = _page("item-1", 1, None, False)
    project = payload["data"]["user"]["projectV2"]
    if connection == "fields":
        parent = project
    elif connection == "labels":
        parent = project["items"]["nodes"][0]["content"]
    else:
        parent = project["items"]["nodes"][0]
    if malformation == "missing":
        parent.pop(connection)
    else:
        parent[connection]["pageInfo"] = []

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner)._search_raw(PROJECT)


@pytest.mark.parametrize("unavailable", ["not_in_project", "rest_404"])
def test_get_issue_raises_typed_unavailable_only_after_target_absence_is_proven(unavailable):
    payload = _page("item-1", 1, None, False)

    def runner(command, **kwargs):
        if command[2] == "graphql":
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if unavailable == "rest_404" and command[-1].endswith("/issues/1"):
            return subprocess.CompletedProcess(
                command, 1, json.dumps({"message": "Not Found", "status": "404"}),
                "gh: Not Found (HTTP 404)",
            )
        raise AssertionError(command)

    issue_id = "GHQUAL-2" if unavailable == "not_in_project" else "GHQUAL-1"
    with pytest.raises(IssueUnavailableError) as exc:
        GitHubProjectsTracker(runner=runner).get_issue(issue_id)
    assert exc.value.issue_id == issue_id
    assert str(exc.value) == f"issue unavailable: {issue_id}"


def test_get_issue_keeps_ambiguous_permission_failure_visible():
    payload = _page("item-1", 1, None, False)

    def runner(command, **kwargs):
        if command[2] == "graphql":
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if command[-1].endswith("/issues/1"):
            return subprocess.CompletedProcess(
                command, 1,
                json.dumps({"message": "Resource not accessible", "status": "403"}),
                "gh: Resource not accessible (HTTP 403)",
            )
        raise AssertionError(command)

    with pytest.raises(GitHubProjectsTrackerError, match="permission_denied") as exc:
        GitHubProjectsTracker(runner=runner).get_issue("GHQUAL-1")
    assert exc.value.operation == "issue.read"


def test_query_issue_keeps_unavailable_parent_and_dependency_as_related_errors(monkeypatch):
    payload = _page("item-2", 2, None, False)

    def runner(command, **kwargs):
        if command[2] == "graphql":
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        path = command[-1]
        if path.endswith("/issues/2"):
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(2)), "")
        if path.endswith("/issues/2/parent"):
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(1)), "")
        if "/dependencies/blocked_by" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps([_rest_issue(3)]), "")
        if "/comments" in path or "/dependencies/blocking" in path or "/sub_issues" in path:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        raise AssertionError(path)

    tracker = GitHubProjectsTracker(runner=runner)
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)

    out = query.issue("GHQUAL-2")

    assert {(link["type"], link["target"]) for link in out["issue"]["links"]} == {
        ("subtask-of", "GHQUAL-1"),
        ("depends-on", "GHQUAL-3"),
    }
    assert out["related"] == {
        "GHQUAL-1": {"id": "GHQUAL-1", "error": "issue unavailable"},
        "GHQUAL-3": {"id": "GHQUAL-3", "error": "issue unavailable"},
    }


def test_query_issue_still_refuses_foreign_relation_uri_before_aliasing(monkeypatch):
    payload = _page("item-2", 2, None, False)
    foreign_parent = _rest_issue(1)
    foreign_parent["repository_url"] = "https://api.github.com/repos/patobiskoto/foreign"
    foreign_parent["url"] = "https://api.github.com/repos/patobiskoto/foreign/issues/1"
    foreign_parent["html_url"] = "https://github.com/patobiskoto/foreign/issues/1"

    def runner(command, **kwargs):
        if command[2] == "graphql":
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        path = command[-1]
        if path.endswith("/issues/2"):
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(2)), "")
        if path.endswith("/issues/2/parent"):
            return subprocess.CompletedProcess(command, 0, json.dumps(foreign_parent), "")
        if "/comments" in path or "/dependencies/" in path or "/sub_issues" in path:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        raise AssertionError(path)

    tracker = GitHubProjectsTracker(runner=runner)
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)

    with pytest.raises(GitHubProjectsTrackerError, match="foreign_issue_coordinate"):
        query.issue("GHQUAL-2")


def test_hydration_refuses_foreign_relation_before_number_aliasing():
    foreign_parent = _rest_issue(1)
    foreign_parent["repository_url"] = "https://api.github.com/repos/patobiskoto/foreign-test-only"
    foreign_parent["url"] = "https://api.github.com/repos/patobiskoto/foreign-test-only/issues/1"
    foreign_parent["html_url"] = "https://github.com/patobiskoto/foreign-test-only/issues/1"

    def runner(command, **kwargs):
        path = command[-1]
        if path.endswith("/issues/2"):
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(2)), "")
        if path.endswith("/issues/2/parent"):
            return subprocess.CompletedProcess(command, 0, json.dumps(foreign_parent), "")
        return subprocess.CompletedProcess(command, 0, "[]", "")

    tracker = GitHubProjectsTracker(runner=runner)
    with pytest.raises(GitHubProjectsTrackerError, match="foreign_issue_coordinate"):
        tracker._hydrate_issue(Issue(id="GHQUAL-2", title="child"), tracker._binding(PROJECT))


def test_hydration_reads_complete_native_hierarchy_and_dependency_directions():
    def runner(command, **kwargs):
        path = command[-1]
        if path.endswith("/issues/2"):
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(2)), "")
        if path.endswith("/issues/2/parent"):
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(1)), "")
        if "/dependencies/blocked_by" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps([_rest_issue(3)]), "")
        if "/dependencies/blocking" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps([_rest_issue(4)]), "")
        if "/sub_issues" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps([_rest_issue(5)]), "")
        if "/comments" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps([{
                "id": 88, "body": "commentaire é", "created_at": "2026-09-27T12:02:00Z",
            }]), "")
        raise AssertionError(path)

    tracker = GitHubProjectsTracker(runner=runner)
    issue = tracker._hydrate_issue(Issue(id="GHQUAL-2", title="child"), tracker._binding(PROJECT))
    assert [(link.type, link.direction, link.target) for link in issue.links] == [
        ("subtask-of", "inward", "GHQUAL-1"),
        ("depends-on", "outward", "GHQUAL-3"),
        ("blocks", "outward", "GHQUAL-4"),
        ("parent-of", "outward", "GHQUAL-5"),
    ]
    assert issue.comments == [{"text": "commentaire é", "created": 1790510520000}]
    assert issue.created == 1790510400000
    assert issue.updated == 1790510460000
