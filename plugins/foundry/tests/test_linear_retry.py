"""PAT-98: bounded retry of read-only Linear calls and typed quota exhaustion.

Fake transport only; no test contacts a Linear workspace. The autouse conftest fixture
`_no_linear_retry_sleep` replaces `linear._sleep` and records the waits.
"""
import email.message
import io
import json
import urllib.error

import pytest

from foundry.trackers import linear
from foundry.trackers.linear import (
    _ISSUE_CREATE, _ISSUE_QUERY, LinearQuotaExhaustedError, LinearTracker,
    LinearTrackerError, _is_read_only_document,
)

TOKEN = "linear-test-secret"
READ = "query Q { viewer { id } }"
WRITE = "mutation M($input: X!) { issueCreate(input: $input) { success } }"
OK = {"data": {"ok": True}}


class Boom(Exception):
    def __init__(self, status=None, headers=None):
        super().__init__("boom")
        self.status = status
        self.headers = headers


def scripted(*steps):
    """Transport replaying steps: an Exception is raised, anything else returned."""
    calls = []

    def transport(document, variables):
        calls.append(document)
        step = steps[min(len(calls), len(steps)) - 1]
        if isinstance(step, Exception):
            raise step
        return step

    transport.calls = calls
    return transport


def tracker(transport, **kw):
    return LinearTracker(token=TOKEN, transport=transport, **kw)


def test_transient_failure_then_success(_no_linear_retry_sleep):
    t = scripted(Boom(), Boom(503), OK)
    assert tracker(t)._graphql(READ, {}, "issue.read") == {"ok": True}
    assert len(t.calls) == 3
    assert _no_linear_retry_sleep == [1.0, 2.0]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_retryable_statuses_are_retried(status):
    t = scripted(Boom(status), OK)
    assert tracker(t)._graphql(READ, {}, "issue.read") == {"ok": True}
    assert len(t.calls) == 2


def test_retries_exhausted_keeps_original_error_and_exposes_count(_no_linear_retry_sleep):
    t = scripted(Boom(503))
    with pytest.raises(LinearTrackerError) as raised:
        tracker(t)._graphql(READ, {}, "issue.read")
    error = raised.value
    assert len(t.calls) == 4  # 1 call + 3 retries
    assert (error.code, error.status, error.retries) == ("http_error", 503, 3)
    assert str(error) == "Linear issue.read -> 503 (http_error) after 3 retries"
    assert _no_linear_retry_sleep == [1.0, 2.0, 4.0]
    assert TOKEN not in str(error)


def test_transport_error_exhausted_keeps_code():
    t = scripted(Boom())
    with pytest.raises(LinearTrackerError) as raised:
        tracker(t)._graphql(READ, {}, "issue.read")
    assert raised.value.code == "transport_error" and raised.value.retries == 3


def test_message_unchanged_without_retry():
    assert str(LinearTrackerError("issue.read", 400, "http_error")) == (
        "Linear issue.read -> 400 (http_error)"
    )
    assert LinearTrackerError("x", None, "c").retries == 0


@pytest.mark.parametrize("failure", [Boom(), Boom(503), Boom(500), Boom(429)])
def test_mutation_is_never_retried(failure, _no_linear_retry_sleep):
    t = scripted(failure, OK)
    with pytest.raises(LinearTrackerError) as raised:
        tracker(t)._graphql(WRITE, {}, "issue.create")
    assert len(t.calls) == 1
    assert raised.value.retries == 0
    assert _no_linear_retry_sleep == []


def test_mixed_document_and_unrecognised_documents_are_not_retried():
    for doc in (
        "query Q { a } mutation M { b }", "{ viewer { id } }", "", "subscription S { a }",
        "fragment F on X { id }", "# query\nmutation M { a }",
    ):
        t = scripted(Boom(503), OK)
        with pytest.raises(LinearTrackerError):
            tracker(t)._graphql(doc, {}, "issue.read")
        assert len(t.calls) == 1, doc


def test_read_only_decision_from_real_documents():
    assert _is_read_only_document(_ISSUE_QUERY)
    assert not _is_read_only_document(_ISSUE_CREATE)
    for name in dir(linear):
        value = getattr(linear, name)
        if name.startswith("_") and isinstance(value, str) and "$" in value:
            assert _is_read_only_document(value) == (
                "mutation" not in value
            ), name


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_non_retryable_http_errors_are_not_retried(status):
    t = scripted(Boom(status), OK)
    with pytest.raises(LinearTrackerError) as raised:
        tracker(t)._graphql(READ, {}, "issue.read")
    assert len(t.calls) == 1
    assert (raised.value.code, raised.value.retries) == ("http_error", 0)
    assert not isinstance(raised.value, LinearQuotaExhaustedError)


@pytest.mark.parametrize(
    "envelope", [
        {"errors": [{"message": "no"}], "data": None},
        {"data": "bad"},
        [],
    ],
)
def test_data_and_graphql_errors_are_not_retried(envelope):
    t = scripted(envelope, OK)
    with pytest.raises(LinearTrackerError):
        tracker(t)._graphql(READ, {}, "issue.read")
    assert len(t.calls) == 1


def test_existing_linear_error_from_transport_is_classified_too():
    t = scripted(LinearTrackerError("issue.read", 503, "http_error"), OK)
    assert tracker(t)._graphql(READ, {}, "issue.read") == {"ok": True}
    t = scripted(LinearTrackerError("issue.read", None, "binding_invalid"), OK)
    with pytest.raises(LinearTrackerError):
        tracker(t)._graphql(READ, {}, "issue.read")
    assert len(t.calls) == 1


RESET_MS = 1_790_000_000_000
ZERO = {
    "x-ratelimit-requests-remaining": "0",
    "x-ratelimit-requests-reset": str(RESET_MS),
}


@pytest.mark.parametrize("document", [READ, WRITE])
@pytest.mark.parametrize(
    "failure", [Boom(429), Boom(400, ZERO), Boom(429, ZERO)],
)
def test_quota_exhausted_is_typed_and_never_retried(failure, document, _no_linear_retry_sleep):
    t = scripted(failure, OK)
    with pytest.raises(LinearQuotaExhaustedError) as raised:
        tracker(t)._graphql(document, {}, "issue.read")
    error = raised.value
    assert len(t.calls) == 1 and _no_linear_retry_sleep == []
    assert isinstance(error, LinearTrackerError)
    assert error.code == "quota_exhausted" and error.retries == 0
    assert TOKEN not in str(error) and "Authorization" not in str(error)


def test_quota_error_exposes_remaining_and_reset():
    t = scripted(Boom(400, ZERO))
    with pytest.raises(LinearQuotaExhaustedError) as raised:
        tracker(t)._graphql(READ, {}, "issue.read")
    error = raised.value
    assert error.remaining == 0 and error.reset_at_ms == RESET_MS
    assert error.reset_at == "2026-09-21T14:13:20Z"
    assert error.operation == "issue.read"
    assert "remaining=0" in str(error) and "2026-09-21T14:13:20Z" in str(error)


def test_quota_after_retries_reports_the_retry_count():
    t = scripted(Boom(503), Boom(503), Boom(400, ZERO))
    with pytest.raises(LinearQuotaExhaustedError) as raised:
        tracker(t)._graphql(READ, {}, "issue.read")
    assert raised.value.retries == 2 and "after 2 retries" in str(raised.value)
    assert len(t.calls) == 3


def test_ratelimited_graphql_code_and_envelope_headers():
    body = {"errors": [{"extensions": {"code": "RATELIMITED"}}], "_headers": ZERO}
    t = scripted(body, OK)
    with pytest.raises(LinearQuotaExhaustedError) as raised:
        tracker(t)._graphql(READ, {}, "issue.read")
    assert len(t.calls) == 1 and raised.value.remaining == 0


def test_plain_400_with_remaining_quota_is_not_quota_and_not_retried():
    headers = {"x-ratelimit-requests-remaining": "100", "x-ratelimit-requests-reset": "1"}
    t = scripted(Boom(400, headers), OK)
    with pytest.raises(LinearTrackerError) as raised:
        tracker(t)._graphql(READ, {}, "issue.read")
    assert not isinstance(raised.value, LinearQuotaExhaustedError)
    assert len(t.calls) == 1


def test_complexity_exhaustion_is_quota():
    headers = {"x-ratelimit-complexity-remaining": "0", "x-ratelimit-complexity-reset": "5000"}
    with pytest.raises(LinearQuotaExhaustedError) as raised:
        tracker(scripted(Boom(400, headers)))._graphql(READ, {}, "issue.read")
    assert raised.value.remaining == 0 and raised.value.reset_at_ms == 5000


def test_read_retries_is_configurable():
    t = scripted(Boom(503), OK)
    with pytest.raises(LinearTrackerError):
        tracker(t, read_retries=0)._graphql(READ, {}, "issue.read")
    assert len(t.calls) == 1


# ---- real urllib path (opener faked, no network) ---------------------------
class _Response(io.BytesIO):
    def __init__(self, body, headers):
        super().__init__(body)
        self.status = 200
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(status, headers, body=b"{}"):
    msg = email.message.Message()
    for key, value in headers.items():
        msg[key] = value
    return urllib.error.HTTPError(linear.LINEAR_GRAPHQL_ENDPOINT, status, "x", msg, io.BytesIO(body))


def _patch_opener(monkeypatch, *steps):
    calls = []

    class Opener:
        def open(self, request, timeout=None):
            calls.append(request)
            step = steps[min(len(calls), len(steps)) - 1]
            if isinstance(step, BaseException):
                raise step
            return step

    monkeypatch.setattr(linear.urllib.request, "build_opener", lambda *a: Opener())
    return calls


def test_real_path_retries_read_and_records_headers(monkeypatch):
    headers = email.message.Message()
    headers["x-ratelimit-requests-remaining"] = "42"
    headers["x-ratelimit-requests-reset"] = "99"
    calls = _patch_opener(
        monkeypatch, _http_error(503, {}), TimeoutError(),
        _Response(json.dumps(OK).encode(), headers),
    )
    t = LinearTracker(token=TOKEN)
    assert t._graphql(READ, {}, "issue.read") == {"ok": True}
    assert len(calls) == 3
    assert t.rate_limit["requests_remaining"] == 42 and t.rate_limit["requests_reset"] == 99
    assert TOKEN not in json.dumps(t.rate_limit)


def test_real_path_mutation_not_replayed(monkeypatch):
    calls = _patch_opener(monkeypatch, _http_error(503, {}), _Response(b'{"data":{}}', {}))
    with pytest.raises(LinearTrackerError) as raised:
        LinearTracker(token=TOKEN)._graphql(WRITE, {}, "issue.create")
    assert len(calls) == 1 and raised.value.retries == 0

    calls = _patch_opener(monkeypatch, TimeoutError(), _Response(b'{"data":{}}', {}))
    with pytest.raises(LinearTrackerError):
        LinearTracker(token=TOKEN)._graphql(WRITE, {}, "issue.create")
    assert len(calls) == 1


def test_real_path_quota_from_headers_and_body(monkeypatch):
    calls = _patch_opener(monkeypatch, _http_error(400, ZERO), _Response(b'{"data":{}}', {}))
    with pytest.raises(LinearQuotaExhaustedError) as raised:
        LinearTracker(token=TOKEN)._graphql(READ, {}, "issue.read")
    assert len(calls) == 1 and raised.value.reset_at_ms == RESET_MS
    assert TOKEN not in str(raised.value)

    body = json.dumps({"errors": [{"extensions": {"code": "RATELIMITED"}}]}).encode()
    _patch_opener(monkeypatch, _http_error(400, {}, body))
    with pytest.raises(LinearQuotaExhaustedError) as raised:
        LinearTracker(token=TOKEN)._graphql(READ, {}, "issue.read")
    assert raised.value.remaining is None and "unknown" in str(raised.value)

    calls = _patch_opener(monkeypatch, _http_error(400, {"x-ratelimit-requests-remaining": "5"}))
    with pytest.raises(LinearTrackerError) as raised:
        LinearTracker(token=TOKEN)._graphql(READ, {}, "issue.read")
    assert not isinstance(raised.value, LinearQuotaExhaustedError) and len(calls) == 1


# ---- PAT-105: review residuals ---------------------------------------------
@pytest.mark.parametrize("code", [["RATELIMITED"], {"a": 1}, 7, None])
def test_non_string_graphql_code_does_not_break_error_path(code):
    t = scripted({"errors": [{"extensions": {"code": code}}], "data": None})
    with pytest.raises(LinearTrackerError) as raised:
        tracker(t)._graphql(READ, {}, "issue.read")
    assert raised.value.code == "graphql_error"


def test_unreadable_http_error_body_keeps_typed_error(monkeypatch):
    class Unreadable(io.BytesIO):
        def read(self, *a):
            raise RuntimeError("boom")

    msg = email.message.Message()
    error = urllib.error.HTTPError(
        linear.LINEAR_GRAPHQL_ENDPOINT, 400, "x", msg, Unreadable(b"")
    )

    class Opener:
        def open(self, *a, **k):
            raise error

    monkeypatch.setattr(linear.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(LinearTrackerError) as raised:
        tracker(None)._graphql(READ, {}, "issue.read")
    assert raised.value.code == "http_error" and raised.value.status == 400


@pytest.mark.parametrize("reset", [10**30, -(10**30)])
def test_aberrant_reset_keeps_typed_quota_error(reset):
    headers = {
        "x-ratelimit-requests-remaining": "0",
        "x-ratelimit-requests-reset": str(reset),
    }
    with pytest.raises(LinearQuotaExhaustedError) as raised:
        tracker(scripted(Boom(400, headers)))._graphql(READ, {}, "issue.read")
    assert raised.value.reset_at is None and "reset_at=unknown" in str(raised.value)


def test_429_is_never_retried_even_from_a_raised_linear_error(_no_linear_retry_sleep):
    t = scripted(LinearTrackerError("issue.read", 429, "http_error"), OK)
    with pytest.raises(LinearTrackerError) as raised:
        tracker(t)._graphql(READ, {}, "issue.read")
    assert len(t.calls) == 1 and raised.value.retries == 0
    assert _no_linear_retry_sleep == []


def test_zero_remaining_on_unrelated_200_graphql_error_is_not_quota():
    body = {"errors": [{"message": "no"}], "data": None, "_headers": ZERO}
    with pytest.raises(LinearTrackerError) as raised:
        tracker(scripted(body))._graphql(READ, {}, "issue.read")
    assert not isinstance(raised.value, LinearQuotaExhaustedError)
    assert raised.value.code == "graphql_error"


def test_complexity_quota_is_distinguished_in_message():
    headers = {"x-ratelimit-complexity-remaining": "0", "x-ratelimit-complexity-reset": str(RESET_MS)}
    with pytest.raises(LinearQuotaExhaustedError) as raised:
        tracker(scripted(Boom(400, headers)))._graphql(READ, {}, "issue.read")
    assert raised.value.scope == "complexity" and "complexity quota" in str(raised.value)
    with pytest.raises(LinearQuotaExhaustedError) as raised:
        tracker(scripted(Boom(400, ZERO)))._graphql(READ, {}, "issue.read")
    assert raised.value.scope == "requests" and "requests quota" in str(raised.value)


def test_singular_retry_message():
    error = LinearTrackerError("issue.read", 503, "http_error")._with_retries(1)
    assert str(error).endswith("after 1 retry")


def test_retry_stats_expose_instability_without_final_failure():
    tr = tracker(scripted(Boom(503), Boom(), OK))
    tr._graphql(READ, {}, "issue.read")
    assert tr.retry_stats == {"retries": 2, "recovered": 1, "exhausted": 0}
    tr = tracker(scripted(Boom(503)))
    with pytest.raises(LinearTrackerError):
        tr._graphql(READ, {}, "issue.read")
    assert tr.retry_stats == {"retries": 3, "recovered": 0, "exhausted": 1}
