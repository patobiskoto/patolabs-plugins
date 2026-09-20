import urllib.parse
import urllib.error

import pytest

from foundry.models import Project
from foundry.trackers.base import IssueUnavailableError
from foundry.trackers.youtrack import YouTrackTracker, _YouTrackHTTPError


class _PagedIssues(YouTrackTracker):
    def __init__(self, pages, *, failure_at=None):
        self.pages = pages
        self.failure_at = failure_at
        self.calls = []

    def _req(self, method, path, body=None, fields=None, top=None):
        assert method == "GET"
        assert body is None and fields is None and top is None
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        skip = int(params["$skip"][0])
        self.calls.append(params)
        if skip == self.failure_at:
            raise RuntimeError("GET /issues -> HTTP 503")
        return self.pages.get(skip, [])


def _issues(start, count):
    return [{"idReadable": f"T-{number}"} for number in range(start, start + count)]


def test_youtrack_issue_search_paginates_past_one_thousand_without_duplicates():
    tracker = _PagedIssues({0: _issues(1, 1000), 1000: _issues(1001, 1)})

    result = tracker._search_raw("project: T State: ready", "idReadable,summary")

    assert len(result) == 1001
    assert len({issue["idReadable"] for issue in result}) == 1001
    assert [call["$skip"] for call in tracker.calls] == [["0"], ["1000"]]
    assert all(call["$top"] == ["1000"] for call in tracker.calls)
    assert all(call["query"] == ["project: T State: ready"] for call in tracker.calls)
    assert all(call["fields"] == ["idReadable,summary"] for call in tracker.calls)


def test_youtrack_issue_search_stops_after_one_short_page():
    tracker = _PagedIssues({0: _issues(1, 2)})

    assert tracker._search_raw("project: T", "idReadable") == _issues(1, 2)
    assert len(tracker.calls) == 1


def test_youtrack_issue_search_refuses_repeated_page_instead_of_looping():
    first_page = _issues(1, 1000)
    tracker = _PagedIssues({0: first_page, 1000: first_page})

    with pytest.raises(RuntimeError, match="pagination.*n'avance pas"):
        tracker._search_raw("project: T", "idReadable")

    assert len(tracker.calls) == 2


def test_youtrack_issue_search_refuses_duplicate_inside_one_page():
    duplicate = {"idReadable": "T-1"}
    tracker = _PagedIssues({0: [duplicate, duplicate]})

    with pytest.raises(RuntimeError, match="pagination.*n'avance pas"):
        tracker._search_raw("project: T", "idReadable")


@pytest.mark.parametrize("bad_page", [None, {"idReadable": "T-1"}, "not-a-list"])
def test_youtrack_issue_search_rejects_invalid_page(bad_page):
    tracker = _PagedIssues({0: bad_page})

    with pytest.raises(RuntimeError, match="page invalide"):
        tracker._search_raw("project: T", "idReadable")


def test_youtrack_issue_search_propagates_provider_error_without_partial_result():
    tracker = _PagedIssues({0: _issues(1, 1000)}, failure_at=1000)

    with pytest.raises(RuntimeError, match="HTTP 503"):
        tracker._search_raw("project: T", "idReadable")

    assert len(tracker.calls) == 2


@pytest.mark.parametrize("status", [403, 404])
def test_youtrack_get_issue_maps_only_unavailable_http_statuses(status):
    class _UnavailableIssue(YouTrackTracker):
        def __init__(self):
            pass

        def _req(self, *args, **kwargs):
            raise _YouTrackHTTPError("GET", "/issues/T-1", status, "provider detail")

    with pytest.raises(IssueUnavailableError) as exc:
        _UnavailableIssue().get_issue("T-1")

    assert str(exc.value) == "issue unavailable: T-1"
    assert "provider detail" not in str(exc.value)


def test_youtrack_get_issue_does_not_convert_server_error():
    class _ServerErrorIssue(YouTrackTracker):
        def __init__(self):
            pass

        def _req(self, *args, **kwargs):
            raise _YouTrackHTTPError("GET", "/issues/T-1", 500, "provider detail")

    with pytest.raises(_YouTrackHTTPError) as exc:
        _ServerErrorIssue().get_issue("T-1")

    assert exc.value.status == 500


@pytest.mark.parametrize("failure", [
    urllib.error.URLError("network down"),
    None,
])
def test_youtrack_get_issue_does_not_convert_network_or_invalid_payload(failure):
    class _BrokenIssue(YouTrackTracker):
        def __init__(self):
            pass

        def _req(self, *args, **kwargs):
            if failure is not None:
                raise failure
            return {}

    expected = urllib.error.URLError if failure is not None else KeyError
    with pytest.raises(expected):
        _BrokenIssue().get_issue("T-1")


class _PagedArticles(YouTrackTracker):
    def __init__(self, pages, *, failure_at=None):
        self.pages = pages
        self.failure_at = failure_at
        self.calls = []

    def _req(self, method, path, body=None, fields=None, top=None):
        assert method == "GET"
        assert body is None and fields is None and top is None
        split = urllib.parse.urlsplit(path)
        params = urllib.parse.parse_qs(split.query)
        skip = int(params["$skip"][0])
        self.calls.append((split.path, params))
        if skip == self.failure_at:
            raise RuntimeError("GET /admin/projects/0-1/articles -> HTTP 503")
        return self.pages.get(skip, [])


def _articles(start, count, *, prefix="T-ADR"):
    return [
        {"idReadable": f"A-{number}", "summary": f"{prefix}-{number:04d} — decision",
         "content": "statut : `accepted`"}
        for number in range(start, start + count)
    ]


def test_youtrack_project_articles_paginate_past_one_thousand():
    tracker = _PagedArticles({0: _articles(1, 1000), 1000: _articles(1001, 1)})

    result = tracker.list_adrs(Project(key="T", id="0-1"))

    assert [adr.id for adr in result] == [f"T-ADR-{number:04d}" for number in range(1, 1002)]
    assert [path for path, _params in tracker.calls] == [
        "/admin/projects/0-1/articles", "/admin/projects/0-1/articles",
    ]
    assert [params["$skip"] for _path, params in tracker.calls] == [["0"], ["1000"]]
    assert all(params["$top"] == ["1000"] for _path, params in tracker.calls)
    assert all(params["fields"] == ["idReadable,summary,content"]
               for _path, params in tracker.calls)


def test_youtrack_project_articles_keep_anchored_prefix_guard():
    foreign, own = _articles(1, 1, prefix="MDTOC-ADR"), _articles(1, 1, prefix="TOC-ADR")
    own[0]["idReadable"] = "A-2"
    tracker = _PagedArticles({0: [*foreign, *own]})

    assert [adr.id for adr in tracker.list_adrs(Project(key="TOC", id="0-1"))] == ["TOC-ADR-0001"]


@pytest.mark.parametrize("bad_page", [
    None,
    {"idReadable": "A-1"},
    "not-a-list",
    [None],
    [{}],
    [{"idReadable": ""}],
])
def test_youtrack_project_articles_reject_invalid_pages(bad_page):
    tracker = _PagedArticles({0: bad_page})

    with pytest.raises(RuntimeError, match="page invalide"):
        tracker.list_adrs(Project(key="T", id="0-1"))


def test_youtrack_project_articles_refuse_duplicate_page_instead_of_looping():
    first_page = _articles(1, 1000)
    tracker = _PagedArticles({0: first_page, 1000: first_page})

    with pytest.raises(RuntimeError, match="pagination.*n'avance pas"):
        tracker.list_adrs(Project(key="T", id="0-1"))

    assert len(tracker.calls) == 2


def test_youtrack_project_articles_propagate_provider_error_without_partial_return():
    tracker = _PagedArticles({0: _articles(1, 1000)}, failure_at=1000)

    with pytest.raises(RuntimeError, match="HTTP 503"):
        tracker.list_adrs(Project(key="T", id="0-1"))

    assert len(tracker.calls) == 2


def test_youtrack_create_adr_uses_an_adr_beyond_first_thousand_for_numbering():
    class _CreateAfterPagedArticles(_PagedArticles):
        def _req(self, method, path, body=None, fields=None, top=None):
            if method == "POST":
                assert path == "/articles"
                assert body["summary"].startswith("T-ADR-1002 — ")
                return {"idReadable": "A-1002"}
            return super()._req(method, path, body, fields, top)

    tracker = _CreateAfterPagedArticles(
        {0: _articles(1, 1000), 1000: _articles(1001, 1)}
    )

    adr = tracker.create_adr(Project(key="T", id="0-1"), "Next", "body")

    assert adr.id == "T-ADR-1002"
