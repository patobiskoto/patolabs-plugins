"""PAT-215: campaign approval journal, human-only approval, refusals before effect."""
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from foundry import campaign_approval as ca
from foundry import epic_preview
from foundry.epic_preview import PREVIEW_CONTRACT, _digest

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
STRUCT = "b" * 64


def make_preview(epic="PAT-100", structure=STRUCT, extra="x"):
    material = {"contract": PREVIEW_CONTRACT, "snapshot": {"root": epic}, "note": extra}
    return {**material, "preview_digest": _digest(material), "structure_digest": structure}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("CLAUDECODE", raising=False)
    for name in [n for n in __import__("os").environ if n.startswith("CODEX_")]:
        monkeypatch.delenv(name)
    # any tracker access fails the test
    import foundry
    monkeypatch.setattr(foundry, "tracker", lambda *a, **k: pytest.fail("tracker touched"),
                        raising=False)
    monkeypatch.setattr(ca, "_stdio_is_tty", lambda: True)
    return tmp_path


def preview_file(tmp_path, **kwargs):
    preview = make_preview(**kwargs)
    path = tmp_path / "preview.json"
    path.write_text(json.dumps(preview))
    return str(path), preview


def fake_tty(monkeypatch, answer):
    monkeypatch.setattr(ca, "_open_tty", lambda: (io.StringIO(answer + "\n"), io.StringIO()))


def do_approve(env, monkeypatch, preview, path, **kwargs):
    fake_tty(monkeypatch, preview["preview_digest"][:12])
    return ca.approve("PAT-100", path, max_sessions=kwargs.pop("max_sessions", 5),
                      max_sessions_per_issue=kwargs.pop("per_issue", 2), now=NOW, **kwargs)


def test_approve_writes_one_bound_record(env, monkeypatch):
    path, preview = preview_file(env)
    record = do_approve(env, monkeypatch, preview, path)
    lines = ca.journal_path().read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0]) == record
    assert str(ca.journal_path()).startswith(str(env / "data"))
    assert record["epic"] == "PAT-100"
    assert record["preview_digest"] == preview["preview_digest"]
    assert record["structure_digest"] == STRUCT
    assert record["expires_at"] == "2026-10-17T12:00:00Z"
    assert (record["max_sessions"], record["max_sessions_per_issue"]) == (5, 2)
    assert record["approver"] == {"kind": "human-tty"}
    assert ca.check("PAT-100", preview["preview_digest"], STRUCT, NOW) == record


def test_no_tracker_name_in_format_or_path(env, monkeypatch):
    path, preview = preview_file(env)
    do_approve(env, monkeypatch, preview, path)
    text = ca.journal_path().read_text() + ca.JOURNAL_NAME + ca.APPROVAL_CONTRACT
    source = Path(ca.__file__).read_text()
    for name in ("linear", "youtrack", "devhub", "github"):
        assert name not in text.lower()
        assert name not in source.lower()
    assert "foundry.tracker" not in source and "import tracker" not in source


def test_refused_without_tty(env, monkeypatch):
    path, preview = preview_file(env)
    monkeypatch.setattr(ca, "_stdio_is_tty", lambda: False)
    with pytest.raises(ca.ApproverRefused):
        ca.approve("PAT-100", path, max_sessions=1, max_sessions_per_issue=None)
    assert not ca.journal_path().exists()


def test_refused_when_no_controlling_terminal(env, monkeypatch):
    path, _ = preview_file(env)

    def no_tty():
        raise OSError("no tty")
    monkeypatch.setattr(ca, "_open_tty", no_tty)
    with pytest.raises(ca.ApproverRefused):
        ca.approve("PAT-100", path, max_sessions=1, max_sessions_per_issue=None)
    assert not ca.journal_path().exists()


@pytest.mark.parametrize("marker", ["CLAUDECODE", "CODEX_SANDBOX", "CODEX_THREAD_ID"])
def test_refused_under_agent_marker_naming_it_not_its_value(env, monkeypatch, marker):
    path, preview = preview_file(env)
    monkeypatch.setenv(marker, "secret-value")
    fake_tty(monkeypatch, preview["preview_digest"][:12])  # even a perfect tty answer
    with pytest.raises(ca.ApproverRefused) as caught:
        ca.approve("PAT-100", path, max_sessions=1, max_sessions_per_issue=None)
    assert marker in str(caught.value) and "secret-value" not in str(caught.value)
    assert not ca.journal_path().exists()


def test_wrong_confirmation_records_nothing(env, monkeypatch):
    path, _ = preview_file(env)
    fake_tty(monkeypatch, "0" * 12)
    with pytest.raises(ca.ApproverRefused):
        ca.approve("PAT-100", path, max_sessions=1, max_sessions_per_issue=None)
    assert not ca.journal_path().exists()


def test_hand_edited_or_foreign_preview_is_refused(env, monkeypatch):
    path, preview = preview_file(env)
    tampered = {**preview, "note": "changed"}
    Path(path).write_text(json.dumps(tampered))
    fake_tty(monkeypatch, preview["preview_digest"][:12])
    with pytest.raises(ca.ApprovalError):
        ca.approve("PAT-100", path, max_sessions=1, max_sessions_per_issue=None)
    other, _ = preview_file(env, epic="PAT-101")
    with pytest.raises(ca.ApprovalError):
        ca.approve("PAT-100", other, max_sessions=1, max_sessions_per_issue=None)
    assert not ca.journal_path().exists()


def test_invalid_root_preview_is_refused_and_journal_stays_readable(env, monkeypatch):
    path, preview = preview_file(env)
    do_approve(env, monkeypatch, preview, path)
    before = ca.journal_path().read_bytes()
    bad_path, bad = preview_file(env, epic="not-an-issue")
    fake_tty(monkeypatch, bad["preview_digest"][:12])
    with pytest.raises(ca.ApprovalError):
        ca.approve("not-an-issue", bad_path, max_sessions=1, max_sessions_per_issue=None, now=NOW)
    assert ca.journal_path().read_bytes() == before
    assert len(ca.read_journal()) == 1
    assert ca.check("PAT-100", preview["preview_digest"], STRUCT, NOW)["epic"] == "PAT-100"


@pytest.mark.parametrize("kwargs", [
    {"max_sessions": 0}, {"max_sessions": 5000}, {"per_issue": 9},
    {"ttl": timedelta(days=8)}, {"ttl": timedelta(0)},
])
def test_ceilings_are_bounded(env, monkeypatch, kwargs):
    path, preview = preview_file(env)
    with pytest.raises(ca.ApprovalError):
        do_approve(env, monkeypatch, preview, path, **kwargs)


def test_check_refusals(env, monkeypatch):
    path, preview = preview_file(env)
    digest = preview["preview_digest"]
    with pytest.raises(ca.ApprovalAbsent):
        ca.check("PAT-100", digest, STRUCT, NOW)
    do_approve(env, monkeypatch, preview, path)
    with pytest.raises(ca.ApprovalAbsent):
        ca.check("PAT-999", digest, STRUCT, NOW)
    with pytest.raises(ca.PreviewDigestMismatch):
        ca.check("PAT-100", "c" * 64, STRUCT, NOW)
    with pytest.raises(ca.StructureDigestMismatch):
        ca.check("PAT-100", digest, "c" * 64, NOW)
    with pytest.raises(ca.ApprovalExpired):
        ca.check("PAT-100", digest, STRUCT, NOW + timedelta(hours=24))
    assert ca.check("PAT-100", digest, STRUCT, NOW + timedelta(hours=23))


def test_tampered_or_malformed_journal_is_refused(env, monkeypatch):
    path, preview = preview_file(env)
    record = do_approve(env, monkeypatch, preview, path)
    journal = ca.journal_path()
    original = journal.read_text()
    journal.write_text(original.replace('"max_sessions":5', '"max_sessions":50'))
    with pytest.raises(ca.JournalInvalid):
        ca.check("PAT-100", record["preview_digest"], STRUCT, NOW)
    journal.write_text(original + "not json\n")
    with pytest.raises(ca.JournalInvalid):
        ca.check("PAT-100", record["preview_digest"], STRUCT, NOW)


def test_journal_is_append_only(env, monkeypatch):
    path, preview = preview_file(env)
    do_approve(env, monkeypatch, preview, path)
    first = ca.journal_path().read_bytes()
    do_approve(env, monkeypatch, preview, path, max_sessions=7)
    second = ca.journal_path().read_bytes()
    assert second.startswith(first) and len(second.splitlines()) == 2
    assert oct(ca.journal_path().stat().st_mode & 0o777) == "0o600"
    latest = ca.check("PAT-100", preview["preview_digest"], STRUCT, NOW)
    assert latest["max_sessions"] in (5, 7)


def test_preview_module_has_no_approval_path():
    source = Path(epic_preview.__file__).read_text().lower()
    assert "approv" not in source.replace("approuv", "")  # no code path; prose "approved" absent
    assert "campaign_approval" not in source


def test_cli_status_and_check(env, monkeypatch, capsys):
    path, preview = preview_file(env)
    do_approve(env, monkeypatch, preview, path)
    assert ca.main(["status", "PAT-100"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert ca.main(["check", "PAT-100", "--preview-file", path]) == 0
    capsys.readouterr()
    other, _ = preview_file(env, extra="y")
    assert ca.main(["check", "PAT-100", "--preview-file", other]) == 2
    assert json.loads(capsys.readouterr().err)["refused"] == "preview_digest_mismatch"
