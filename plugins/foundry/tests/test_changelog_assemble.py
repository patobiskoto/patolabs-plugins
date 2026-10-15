"""PAT-210: unreleased entries live in changelog.d fragments, assembled at release."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HELPER = PLUGIN / "scripts" / "changelog_assemble.py"
sys.path.insert(0, str(PLUGIN / "scripts"))
import changelog_assemble as assembler  # noqa: E402


def _write(directory, name, text):
    (directory / name).write_text(text, encoding="utf-8")


def test_assembly_is_deterministic_and_orders_by_issue_number_descending(tmp_path):
    _write(tmp_path, "PAT-9.md", "- PAT-9: nine\n")
    _write(tmp_path, "PAT-100.md", "- PAT-100: hundred\n  second line\n")
    _write(tmp_path, "PAT-20.md", "- PAT-20: twenty\n")
    _write(tmp_path, "README.md", "ignored\n")
    first = assembler.assemble("9.9.9", "2030-01-01", tmp_path)
    assert first == (
        "## 9.9.9 — 2030-01-01\n\n"
        "- PAT-100: hundred\n  second line\n- PAT-20: twenty\n- PAT-9: nine\n"
    )
    assert assembler.assemble("9.9.9", "2030-01-01", tmp_path) == first


def test_assembly_refuses_malformed_or_missing_fragments(tmp_path):
    with pytest.raises(ValueError):
        assembler.assemble("9.9.9", "2030-01-01", tmp_path)
    _write(tmp_path, "PAT-1.md", "not a list item\n")
    with pytest.raises(ValueError):
        assembler.assemble("9.9.9", "2030-01-01", tmp_path)
    _write(tmp_path, "PAT-1.md", "- ok\n")
    _write(tmp_path, "notes.md", "- x\n")
    with pytest.raises(ValueError):
        assembler.assemble("9.9.9", "2030-01-01", tmp_path)


def test_shipped_fragments_assemble_and_unreleased_may_be_empty():
    # The release PR deletes every fragment (skills/release/SKILL.md): zero is valid here.
    paths = assembler.fragment_paths()
    names = {p.stem for p in paths}
    if paths:
        text = assembler.assemble("0.0.0", "2000-01-01")
        entries = re.findall(r"(?m)^- ([A-Za-z]+-\d+):", text)
        numbers = [int(e.split("-")[1]) for e in entries]
        assert numbers == sorted(numbers, reverse=True)
        assert set(entries) == names
        for path in paths:
            assert path.read_text(encoding="utf-8").startswith(f"- {path.stem}:")
    # CHANGELOG.md keeps an empty Unreleased heading; fragments are not duplicated in it
    changelog = (PLUGIN / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.startswith("# Changelog\n\n## Unreleased\n\n## ")
    for name in names:
        assert f"- {name}:" not in changelog.split("\n## ", 2)[1]


def test_hidden_files_are_ignored_other_unexpected_files_refused(tmp_path):
    _write(tmp_path, "PAT-1.md", "- PAT-1: one\n")
    _write(tmp_path, ".DS_Store", "x")
    assert [p.name for p in assembler.fragment_paths(tmp_path)] == ["PAT-1.md"]
    _write(tmp_path, "notes.md", "- x\n")
    with pytest.raises(ValueError):
        assembler.fragment_paths(tmp_path)


def test_helper_prints_and_writes_nothing(tmp_path):
    _write(tmp_path, "PAT-1.md", "- PAT-1: one\n")
    before = sorted(p.name for p in PLUGIN.rglob("*") if ".git" not in p.parts
                    and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts)
    changelog = (PLUGIN / "CHANGELOG.md").read_bytes()
    ok = subprocess.run(
        [sys.executable, str(HELPER), "9.9.9", "2030-01-01", "--dir", str(tmp_path)],
        capture_output=True, text=True, check=True,
    )
    assert ok.stdout == "## 9.9.9 — 2030-01-01\n\n- PAT-1: one\n"
    empty = tmp_path / "empty"
    empty.mkdir()
    bad = subprocess.run(
        [sys.executable, str(HELPER), "9.9.9", "2030-01-01", "--dir", str(empty)],
        capture_output=True, text=True,
    )
    assert bad.returncode != 0
    after = sorted(p.name for p in PLUGIN.rglob("*") if ".git" not in p.parts
                   and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts)
    assert after == before
    assert (PLUGIN / "CHANGELOG.md").read_bytes() == changelog
    assert sorted(tmp_path.iterdir()) == sorted([tmp_path / "PAT-1.md", empty])
