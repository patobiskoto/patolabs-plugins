"""PLP-343: the KPI publication that follows a successful `issue merge` (PLP-ADR-0063).

Fake credentials, a simulated loopback server and a temporary state directory only: the suite-wide fixtures of
`conftest.py` redirect HOME, and `_no_kpi_publication_after_merge` turns the step off in every other test."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from foundry import config, issue, kpi_export, kpi_merge, kpi_sessions
from test_kpi_publish import CONTRACT, FAKE, _chain_record, _devhub_like, server  # noqa: F401

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CAPABLE = SimpleNamespace(kpi_publication_supported=True)
LEGACY = SimpleNamespace()


def _document(*records):
    return json.dumps({"export_schema": "foundry-kpi-export.v1", "batches": [
        {"schema_version": "devhub-observations.v2", "records": list(records)}]}).encode()


@pytest.fixture
def wired(monkeypatch, server):  # noqa: F811
    srv = server(_devhub_like)
    srv.bodies = {}
    monkeypatch.setenv("DEVHUB_URL", srv.url)
    monkeypatch.setenv("DEVHUB_METRICS_TOKEN", FAKE)
    state = {"records": [_chain_record(1, "f1", issue="DEVHUB-1")], "built": []}

    def build(issues, repository, *, claude_sessions, codex_sessions):
        state["built"].append((list(issues), repository))
        return _document(*state["records"])

    monkeypatch.setattr(kpi_export, "build", build)
    monkeypatch.setattr(kpi_sessions, "discover", lambda *a, **k: ([], []))
    monkeypatch.setattr(kpi_sessions, "repository_roots", lambda root: [])
    state["server"] = srv
    return state


def _publish():
    return kpi_merge.publish_after_merge(CAPABLE, "DEVHUB-1", "acme/demo")


def test_a_tracker_without_the_capability_publishes_and_says_nothing(wired):
    assert kpi_merge.publish_after_merge(LEGACY, "DEVHUB-1", "acme/demo") == (None, False)
    assert wired["built"] == [] and wired["server"].seen == []


def test_success_is_one_summary_line_then_replayed_then_revised(wired):
    assert _publish() == ("kpi: created=1 revised=0 replayed=0 refused=0 not_sent=0", False)
    assert wired["built"] == [(["DEVHUB-1"], "acme/demo")]
    path, auth, body = wired["server"].seen[0]
    assert path.endswith("/projects/DEVHUB/observations:batch") and auth == f"Bearer {FAKE}"
    assert _publish() == ("kpi: created=0 revised=0 replayed=1 refused=0 not_sent=0", False)
    wired["records"] = [_chain_record(2, "f2", issue="DEVHUB-1")]
    assert _publish() == ("kpi: created=0 revised=1 replayed=0 refused=0 not_sent=0", False)
    assert wired["server"].seen[-1][2]["records"][0]["supersedes_id"] == "kpi-1"


def test_token_absent_is_one_warning_and_nothing_is_sent(wired, monkeypatch):
    monkeypatch.delenv("DEVHUB_METRICS_TOKEN")
    monkeypatch.setattr(config, "get", lambda key, *a, **k: None)  # no keychain lookup either
    line, warning = _publish()
    assert warning and "\n" not in line and line.startswith("kpi: publication skipped")
    assert "DEVHUB_METRICS_TOKEN" in line and wired["server"].seen == []


def test_tracker_unreachable_is_a_warning_with_the_count_not_sent(wired, monkeypatch):
    monkeypatch.setenv("DEVHUB_URL", "http://127.0.0.1:9")
    line, warning = _publish()
    assert warning and "not_sent=1" in line and "127.0.0.1" not in line and FAKE not in line


def test_a_refusal_is_a_warning_with_the_count_refused(wired):
    # the reduced DevHub rules refuse a revision whose predecessor is unknown
    wired["records"] = [{**_chain_record(2, "f2", issue="DEVHUB-1"), "revision": 2, "supersedes_id": "nope"}]
    line, warning = _publish()
    assert warning and "refused=1" in line and "\n" not in line


def test_an_unexpected_exception_becomes_a_warning_without_detail(wired, monkeypatch):
    def boom(*a, **k):
        raise ValueError(f"secret {FAKE} /home/x")

    monkeypatch.setattr(kpi_export, "build", boom)
    line, warning = _publish()
    assert warning and "unexpected ValueError" in line and FAKE not in line and "/home" not in line


def test_a_document_of_another_project_is_a_warning(wired):
    wired["records"] = [_chain_record(1, "f1", issue="OTHER-1")]
    line, warning = _publish()
    assert warning and "anchored outside project DEVHUB" in line and wired["server"].seen == []


# --- the wiring in issue.merge ------------------------------------------------------------------------------

def _real():
    return issue._publish_kpi_after_merge.__wrapped__


def test_the_step_prints_the_summary_on_stdout_and_the_warning_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr(issue, "repository_identity", lambda *a: "acme/demo")
    monkeypatch.setattr(kpi_merge, "publish_after_merge", lambda tr, i, r: ("kpi: created=1", False))
    _real()(CAPABLE, "DEVHUB-1")
    out = capsys.readouterr()
    assert out.out == "kpi: created=1\n" and out.err == ""
    monkeypatch.setattr(kpi_merge, "publish_after_merge", lambda tr, i, r: ("kpi: skipped", True))
    _real()(CAPABLE, "DEVHUB-1")
    out = capsys.readouterr()
    assert out.out == "" and out.err == "kpi: skipped\n"
    monkeypatch.setattr(kpi_merge, "publish_after_merge", lambda tr, i, r: (None, False))
    _real()(LEGACY, "DEVHUB-1")
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("error", [RuntimeError("x"), SystemExit(7), KeyError("k")])
def test_the_step_never_raises_whatever_fails_inside_it(monkeypatch, capsys, error):
    monkeypatch.setattr(issue, "repository_identity", lambda *a: (_ for _ in ()).throw(error))
    _real()(CAPABLE, "DEVHUB-1")
    out = capsys.readouterr()
    assert out.out == "" and out.err.count("\n") == 1 and "kpi: publication skipped" in out.err


def _merge_harness(monkeypatch, tmp_path):
    from test_issue_merge import _merge_with_failing_pull
    return _merge_with_failing_pull(monkeypatch, tmp_path)


def test_merge_runs_the_step_after_done_and_a_failing_step_changes_neither_output_nor_exit(
        monkeypatch, tmp_path, capsys):
    events = _merge_harness(monkeypatch, tmp_path)
    monkeypatch.setattr(issue, "_publish_kpi_after_merge", lambda tr, i: events.append("kpi"))
    with pytest.raises(SystemExit) as raised:  # the harness's local pull fails: exit 3 after the success
        issue.merge("DEMO-7", "12")
    assert raised.value.code == 3 and events == ["done", "kpi"]
    assert "✅ PR #12 mergée" in capsys.readouterr().out


def test_merge_exit_code_is_unchanged_when_the_real_step_explodes(monkeypatch, tmp_path, capsys):
    events = _merge_harness(monkeypatch, tmp_path)
    monkeypatch.setattr(issue, "_publish_kpi_after_merge", _real())
    monkeypatch.setattr(kpi_merge, "publish_after_merge", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(SystemExit) as raised:
        issue.merge("DEMO-7", "12")
    out, err = capsys.readouterr()
    assert raised.value.code == 3 and events == ["done"]
    assert "✅ PR #12 mergée" in out and "kpi: publication skipped" in err


def test_a_refused_merge_never_reaches_the_step(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(issue, "_publish_kpi_after_merge", lambda tr, i: called.append(1))
    monkeypatch.setattr(issue.foundry, "tracker", lambda: (_ for _ in ()).throw(SystemExit("refused")))
    with pytest.raises(SystemExit):
        issue.merge("DEMO-7", "12")
    assert called == []


def test_only_the_merge_step_and_the_cli_import_the_publisher_and_no_hook_does():
    pattern = r"^\s*(from foundry import .*\bkpi_(publish|merge)\b|from foundry\.kpi_(publish|merge)\b)"
    importers = {p.relative_to(PLUGIN_ROOT).as_posix()
                 for base in (PLUGIN_ROOT / "tooling", PLUGIN_ROOT / "hooks") for p in base.rglob("*.py")
                 if re.search(pattern, p.read_text(), re.M)}
    assert importers == {"tooling/foundry/routing.py", "tooling/foundry/kpi_merge.py",
                         "tooling/foundry/issue.py"}
