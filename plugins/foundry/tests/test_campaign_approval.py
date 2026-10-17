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


FAR_MS = 4_102_444_800_000  # 2100-01-01T00:00:00Z


def make_preview(epic="PAT-100", structure=STRUCT, extra="x", expires_ms=FAR_MS):
    material = {
        "contract": PREVIEW_CONTRACT, "snapshot": {"root": epic}, "note": extra,
        "policy": {"expires_at": expires_ms},
    }
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
                      max_sessions_per_issue=kwargs.pop("per_issue", 2),
                      now=kwargs.pop("now", NOW), **kwargs)


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
    # The CLI checks against the real clock, so approve at the real clock too (PAT-232).
    do_approve(env, monkeypatch, preview, path, now=datetime.now(timezone.utc))
    assert ca.main(["status", "PAT-100"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert ca.main(["check", "PAT-100", "--preview-file", path]) == 0
    capsys.readouterr()
    other, _ = preview_file(env, extra="y")
    assert ca.main(["check", "PAT-100", "--preview-file", other]) == 2
    assert json.loads(capsys.readouterr().err)["refused"] == "preview_digest_mismatch"


def test_approval_never_outlives_the_approved_preview(env, monkeypatch):
    # PAT-234 (215-4 e): preview expires 2 hours after NOW, ttl asks for 24 hours
    ends = int((NOW + timedelta(hours=2)).timestamp() * 1000)
    path, preview = preview_file(env, expires_ms=ends)
    record = do_approve(env, monkeypatch, preview, path)
    assert record["expires_at"] == "2026-10-16T14:00:00Z"
    assert ca.check("PAT-100", preview["preview_digest"], STRUCT, NOW + timedelta(hours=1))
    with pytest.raises(ca.ApprovalExpired):
        ca.check("PAT-100", preview["preview_digest"], STRUCT, NOW + timedelta(hours=2))


class KeptOutput(io.StringIO):
    def close(self):  # `with writer` closes it; keep the text readable
        self.flush()


def test_shorter_ttl_is_kept_and_expiry_is_shown_before_confirmation(env, monkeypatch):
    path, preview = preview_file(env)
    shown = KeptOutput()
    monkeypatch.setattr(ca, "_open_tty", lambda: (
        io.StringIO(preview["preview_digest"][:12] + "\n"), shown))
    record = ca.approve("PAT-100", path, max_sessions=3, max_sessions_per_issue=None,
                        ttl=timedelta(hours=1), now=NOW)
    assert record["expires_at"] == "2026-10-16T13:00:00Z"
    assert "expire 2026-10-16T13:00:00Z" in shown.getvalue()
    assert "plafonné" not in shown.getvalue()
    capped = int((NOW + timedelta(hours=2)).timestamp() * 1000)
    path2, preview2 = preview_file(env, extra="y", expires_ms=capped)
    shown2 = KeptOutput()
    monkeypatch.setattr(ca, "_open_tty", lambda: (
        io.StringIO(preview2["preview_digest"][:12] + "\n"), shown2))
    ca.approve("PAT-100", path2, max_sessions=3, max_sessions_per_issue=None, now=NOW)
    assert "expire 2026-10-16T14:00:00Z (plafonné par l'expiration de l'aperçu)" in shown2.getvalue()


def test_expired_or_expiryless_preview_is_refused_and_records_nothing(env, monkeypatch):
    past = int((NOW - timedelta(hours=1)).timestamp() * 1000)
    path, preview = preview_file(env, expires_ms=past)
    with pytest.raises(ca.ApprovalError, match="expiré"):
        do_approve(env, monkeypatch, preview, path)
    for bad in (None, 0, "soon", 1.5, True):
        path, preview = preview_file(env, extra=str(bad), expires_ms=bad)
        with pytest.raises(ca.ApprovalError, match="policy.expires_at"):
            do_approve(env, monkeypatch, preview, path)
    assert not ca.journal_path().exists()


def test_naive_now_is_a_typed_refusal(env, monkeypatch):
    path, preview = preview_file(env)
    naive = datetime(2026, 10, 16, 12, 0)
    with pytest.raises(ca.ApprovalError, match="naïf"):
        do_approve(env, monkeypatch, preview, path, now=naive)
    do_approve(env, monkeypatch, preview, path)
    with pytest.raises(ca.ApprovalError, match="naïf"):
        ca.check("PAT-100", preview["preview_digest"], STRUCT, naive)


def test_partial_os_write_is_completed(env, monkeypatch):
    path, preview = preview_file(env)
    real = __import__("os").write
    monkeypatch.setattr(ca.os, "write", lambda fd, data: real(fd, bytes(data)[:7]))
    record = do_approve(env, monkeypatch, preview, path)
    assert ca.read_journal() == [record]


def test_unwritable_journal_is_a_json_refusal_not_a_trace(env, monkeypatch, capsys):
    path, preview = preview_file(env)
    journal = ca.journal_path()
    journal.parent.mkdir(parents=True)
    journal.symlink_to(env / "elsewhere")  # O_NOFOLLOW refuses the open
    fake_tty(monkeypatch, preview["preview_digest"][:12])
    code = ca.main(["approve", "PAT-100", "--preview-file", path, "--max-sessions", "2"])
    assert code == 2
    assert json.loads(capsys.readouterr().err)["refused"] == "journal_invalid"
    assert not (env / "elsewhere").exists()


def test_read_waits_for_a_writer_holding_the_lock(env, monkeypatch):
    import fcntl
    import os
    import threading
    path, preview = preview_file(env)
    do_approve(env, monkeypatch, preview, path)
    holder = os.open(ca.journal_path(), os.O_WRONLY)
    fcntl.flock(holder, fcntl.LOCK_EX)
    done = threading.Event()
    thread = threading.Thread(target=lambda: (ca.read_journal(), done.set()))
    thread.start()
    assert not done.wait(0.3)
    fcntl.flock(holder, fcntl.LOCK_UN)
    os.close(holder)
    assert done.wait(5)
    thread.join()


def test_real_cli_in_a_subprocess_refuses_without_terminal(tmp_path):
    # PAT-234 (215-5): no monkeypatch; stdin and stdout are pipes, not a terminal
    import os
    import subprocess
    import sys
    path, _ = preview_file(tmp_path)
    cli = Path(ca.__file__).resolve().parents[1] / "foundry_cli.py"
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE" and not k.startswith("CODEX_")}
    env["FOUNDRY_DATA"] = str(tmp_path / "data")
    result = subprocess.run(
        [sys.executable, str(cli), "campaign-approval", "approve", "PAT-100",
         "--preview-file", path, "--max-sessions", "2"],
        input="", capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stderr)["refused"] == "approver_refused"
    assert not (tmp_path / "data" / ca.JOURNAL_NAME).exists()
