"""PLP-29: the read-only Release body helper (scripts/release_notes.py)."""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import release_notes  # noqa: E402

HELPER = SCRIPTS / "release_notes.py"
REPO = "owner/repo"


def _fake(monkeypatch, sections):
    monkeypatch.setattr(
        release_notes.contract, "changelog_sections",
        lambda: [(v, "2026-01-01", text) for v, text in sections.items()],
    )


def test_section_is_returned_verbatim(monkeypatch):
    section = "## 1.2.3 — 2026-01-01\n\n- one\n- two"
    _fake(monkeypatch, {"1.2.3": section, "1.2.2": "## 1.2.2 — 2025-12-31\n\n- old"})
    assert release_notes.release_body("1.2.3", REPO) == section + "\n"


def test_missing_and_empty_sections_are_refused(monkeypatch):
    _fake(monkeypatch, {"1.2.3": "## 1.2.3 — 2026-01-01\n\n  \n"})
    with pytest.raises(ValueError, match="no published CHANGELOG section for 9.9.9"):
        release_notes.release_body("9.9.9", REPO)
    with pytest.raises(ValueError, match="is empty"):
        release_notes.release_body("1.2.3", REPO)


def test_long_section_is_cut_at_a_line_boundary_with_the_link(monkeypatch):
    lines = [f"- entry {n} " + "x" * 40 for n in range(200)]
    section = "## 1.2.3 — 2026-01-01\n\n" + "\n".join(lines)
    _fake(monkeypatch, {"1.2.3": section})
    body = release_notes.release_body("1.2.3", REPO, limit=1000)
    assert len(body) <= 1000
    link = "https://github.com/owner/repo/blob/foundry-v1.2.3/plugins/foundry/CHANGELOG.md"
    assert body.endswith(f"({link})._\n")
    kept = body.split("\n\n_Truncated")[0].split("\n")
    assert kept[0] == "## 1.2.3 — 2026-01-01"
    assert all(line in lines for line in kept[2:])  # only whole lines survive


def test_cli_refuses_without_writing_and_writes_only_the_output(tmp_path):
    out = tmp_path / "notes.md"
    bad = subprocess.run(
        [sys.executable, str(HELPER), "99.0.0", "--repo", REPO, "--output", str(out)],
        capture_output=True, text=True,
    )
    assert bad.returncode != 0 and not out.exists() and bad.stdout == ""
    version = release_notes.contract.changelog_sections()[0][0]
    ok = subprocess.run(
        [sys.executable, str(HELPER), version, "--repo", REPO, "--output", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert ok.stdout == "" and out.read_text(encoding="utf-8").startswith(f"## {version} ")
    assert [p.name for p in tmp_path.iterdir()] == ["notes.md"]


def test_a_malformed_version_is_refused_as_a_value_error_not_an_assertion(monkeypatch):
    _fake(monkeypatch, {"1.2.3": "## 1.2.3 — 2026-01-01\n\n- one"})
    with pytest.raises(ValueError, match="not a plain semver version"):
        release_notes.release_body("1.2.3-rc1", REPO)


def test_cli_says_on_stderr_when_the_body_was_cut_and_stays_silent_otherwise(monkeypatch, tmp_path, capsys):
    lines = [f"- entry {n} " + "x" * 40 for n in range(5000)]
    _fake(monkeypatch, {"1.2.3": "## 1.2.3 — 2026-01-01\n\n" + "\n".join(lines),
                        "1.2.4": "## 1.2.4 — 2026-01-02\n\n- short"})
    out = tmp_path / "notes.md"
    release_notes.main(["1.2.3", "--repo", REPO, "--output", str(out)])
    assert capsys.readouterr().err == "release_notes: the body was cut below 125000 characters\n"
    release_notes.main(["1.2.4", "--repo", REPO, "--output", str(out)])
    assert capsys.readouterr().err == ""
    with pytest.raises(SystemExit) as refused:
        release_notes.main(["1.2.3-rc1", "--repo", REPO])
    assert "not a plain semver version" in str(refused.value)
