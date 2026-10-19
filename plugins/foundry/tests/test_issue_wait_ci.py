"""PAT-274: `issue wait-ci` polls write.ci_gate, read-only, with injected clock."""
from types import SimpleNamespace

import pytest

from foundry import issue
from foundry.models import Check


def _host(rounds):
    calls = {"n": 0, "merge": 0}

    def runs(_repo, _sha):
        i = min(calls["n"], len(rounds) - 1)
        calls["n"] += 1
        return rounds[i]

    host = SimpleNamespace(
        resolve_repo=lambda: "acme/demo",
        get_pr=lambda _r, n: SimpleNamespace(sha="a" * 40, number=n),
        check_runs=runs,
        commit_statuses=lambda _r, _s: [],
        ci_expected=lambda _r, _s: False,
    )
    return host, calls


def _run(rounds, flags=(), capsys=None):
    host, calls = _host(rounds)
    t = {"now": 0.0}
    slept = []

    def sleep(s):
        slept.append(s)
        t["now"] += s

    code = issue.wait_ci(
        "7", flags, codehost=host, sleep=sleep, clock=lambda: t["now"],
        now=lambda: __import__("datetime").datetime(2026, 1, 2, 3, 4, 5),
    )
    return code, slept, capsys.readouterr().out


def ok(name="t"):
    return Check(name=name, status="completed", conclusion="success")


def test_green_after_pending(capsys):
    code, slept, out = _run(
        [[Check(name="t", status="in_progress", conclusion=None)], [ok()]],
        ("--interval=5",), capsys)
    assert code == issue.WAIT_CI_EXIT_GREEN and slept == [5.0]
    assert "verdict=green" in out and "observed_at=2026-01-02T03:04:05Z" in out


def test_red_stops_at_once(capsys):
    bad = Check(name="lint", status="completed", conclusion="failure")
    code, slept, out = _run([[bad, Check(name="x", status="queued", conclusion=None)]],
                            (), capsys)
    assert code == issue.WAIT_CI_EXIT_RED and slept == []
    assert "failed=['lint']" in out and "in_progress=['x']" in out


def test_timeout_when_never_green(capsys):
    code, slept, out = _run([[]], ("--timeout=10", "--interval=4"), capsys)
    assert code == issue.WAIT_CI_EXIT_TIMEOUT and slept == [4.0, 4.0]
    assert "verdict=timeout" in out


@pytest.mark.parametrize("flag", ["--timeout=0", "--interval=x", "--timeout=-1"])
def test_invalid_numbers_refused(flag):
    with pytest.raises(SystemExit):
        issue.wait_ci("7", (flag,), codehost=_host([[ok()]])[0])
