"""PAT-274: `issue wait-ci` polls write.ci_gate, read-only, with injected clock."""
import sys
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


def test_all_skipped_run_answers_at_once_that_it_is_not_provably_green(capsys):
    """PAT-286: complete, none failed, none `success`: final, so no polling to the timeout."""
    skipped = [Check(name="a", status="completed", conclusion="skipped"),
               Check(name="b", status="completed", conclusion="neutral")]
    code, slept, out = _run([skipped], ("--timeout=600",), capsys)
    assert code == issue.WAIT_CI_EXIT_RED and slept == []
    assert "verdict=not_green" in out and "aucun check n'a conclu success" in out
    assert "in_progress=[]" in out and "failed=[]" in out


def test_skipped_beside_a_pending_check_keeps_waiting(capsys):
    rounds = [[Check(name="a", status="completed", conclusion="skipped"),
               Check(name="b", status="in_progress", conclusion=None)],
              [Check(name="a", status="completed", conclusion="skipped"), ok("b")]]
    code, slept, out = _run(rounds, ("--interval=5",), capsys)
    assert code == issue.WAIT_CI_EXIT_GREEN and slept == [5.0]


@pytest.mark.parametrize("flag", ["--timeout=nan", "--interval=inf", "--timeout=-inf",
                                  "--interval=NaN", "--timeout=infinity"])
def test_non_finite_numbers_are_refused(flag):
    with pytest.raises(SystemExit, match="nombre fini"):
        issue.wait_ci("7", (flag,), codehost=_host([[ok()]])[0])


def test_a_non_numeric_pr_number_is_a_traceback_not_a_system_exit():
    """PAT-286: documents `docs/ci-wait.md` on errors that `wait_ci` does not catch."""
    with pytest.raises(ValueError):
        issue.wait_ci("abc", (), codehost=_host([[ok()]])[0])


def _main(monkeypatch, *argv):
    import runpy
    import warnings
    monkeypatch.setattr(sys, "argv", ["issue.py", *argv])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module("foundry.issue", run_name="__main__")


def test_main_dispatches_wait_ci_and_exits_with_its_code(monkeypatch, capsys):
    host, _ = _host([[Check(name="t", status="completed", conclusion="failure")]])
    monkeypatch.setattr(issue.foundry, "codehost", lambda: host)
    with pytest.raises(SystemExit) as raised:
        _main(monkeypatch, "wait-ci", "7", "--timeout=5", "--interval=1")
    assert raised.value.code == issue.WAIT_CI_EXIT_RED
    assert "verdict=red pr=7" in capsys.readouterr().out


def test_main_dispatches_wait_ci_all_skipped_without_polling(monkeypatch, capsys):
    host, calls = _host([[Check(name="t", status="completed", conclusion="skipped")]])
    monkeypatch.setattr(issue.foundry, "codehost", lambda: host)
    with pytest.raises(SystemExit) as raised:
        _main(monkeypatch, "wait-ci", "7", "--timeout=600", "--interval=1")
    assert raised.value.code == issue.WAIT_CI_EXIT_RED and calls["n"] == 1
    assert "verdict=not_green" in capsys.readouterr().out


@pytest.mark.parametrize("flags", [
    ["--timeout=5", "--timeout=5"], ["--interval=5", "--interval=5"],
    ["--timeout=nan"], ["--interval=inf"],
])
def test_main_refuses_a_repeated_or_non_finite_wait_ci_option(monkeypatch, flags):
    monkeypatch.setattr(issue.foundry, "codehost", lambda: pytest.fail("code host reached"))
    with pytest.raises(SystemExit) as raised:
        _main(monkeypatch, "wait-ci", "7", *flags)
    assert isinstance(raised.value.code, str) and raised.value.code.startswith("⛔")


@pytest.mark.parametrize("verb", ["wait-ci", "start", "openpr", "close-epic", "accept"])
def test_main_refuses_the_override_reason_for_a_verb_that_does_not_read_it(monkeypatch, verb):
    monkeypatch.setattr(issue.foundry, "codehost", lambda: pytest.fail("code host reached"))
    monkeypatch.setattr(issue.foundry, "tracker", lambda: pytest.fail("tracker reached"))
    with pytest.raises(SystemExit) as raised:
        _main(monkeypatch, verb, "7", "--ac-override-reason=human_confirmed")
    assert "Flag(s) inconnus" in raised.value.code
    assert "--ac-override-reason=human_confirmed" in raised.value.code


def test_main_still_accepts_the_override_reason_for_merge(monkeypatch):
    class Reached(Exception):
        pass

    def tracker():
        raise Reached
    monkeypatch.setattr(issue.foundry, "tracker", tracker)
    with pytest.raises(Reached):
        _main(monkeypatch, "merge", "DEMO-7", "12", "--allow-incomplete-ac",
              "--ac-override-reason=human_confirmed")


@pytest.mark.parametrize("argv", [["-h"], ["--help"], ["start", "-h"], ["merge", "X", "1", "--help"],
                                  ["openpr", "-h"], ["wait-ci", "--help"]])
def test_main_help_prints_usage_and_reaches_nothing(monkeypatch, capsys, argv):
    """PAT-286: `start -h` used to take `-h` as an issue id and call the tracker."""
    monkeypatch.setattr(issue.foundry, "tracker", lambda: pytest.fail("tracker reached"))
    monkeypatch.setattr(issue.foundry, "codehost", lambda: pytest.fail("code host reached"))
    with pytest.raises(SystemExit) as raised:
        _main(monkeypatch, *argv)
    assert raised.value.code == 0
    assert capsys.readouterr().out.startswith("usage: python3 -m foundry.issue")
