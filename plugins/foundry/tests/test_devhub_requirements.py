"""PAT-296: the requirements page and the live suite carry the same needs."""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import devhub_live_gate as gate

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "devhub-requirements.md"
SUITE = ROOT / "tests" / "integration" / "test_devhub_live_conformance.py"
STATUSES = {"green", "red", "unknown"}


def _page_rows() -> dict[str, dict]:
    rows = {}
    for line in PAGE.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 6 and re.fullmatch(r"DHN-\d{2}", cells[0]):
            assert cells[0] not in rows, cells[0]
            rows[cells[0]] = {
                "test": cells[2].strip("`"), "status": cells[3], "owner": cells[4],
            }
    return rows


def _suite_tests() -> dict[str, dict]:
    tree = ast.parse(SUITE.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        decorators = [
            item for item in node.decorator_list
            if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
            and item.func.id in {"need", "gap"}
        ]
        assert len(decorators) == 1, f"{node.name} needs exactly one @need or @gap"
        call = decorators[0]
        need_id = call.args[0].value
        assert need_id not in found, need_id
        found[need_id] = {"test": node.name, "kind": call.func.id}
    return found


def test_every_need_of_the_page_has_one_live_test_and_the_reverse():
    rows, tests = _page_rows(), _suite_tests()
    assert rows and set(rows) == set(tests)
    assert sorted(rows) == [f"DHN-{index:02d}" for index in range(1, len(rows) + 1)]
    for need_id, row in rows.items():
        assert row["test"] == tests[need_id]["test"], need_id
        assert tests[need_id]["test"].startswith(f"test_dhn_{need_id[-2:]}_"), need_id


def test_expected_red_needs_are_strict_gaps_and_never_green_on_the_page():
    rows, tests = _page_rows(), _suite_tests()
    for need_id, row in rows.items():
        assert row["status"] in STATUSES, need_id
        if tests[need_id]["kind"] == "gap":
            assert row["status"] == "red" and row["owner"] != "-", need_id
        else:
            assert row["status"] in {"green", "unknown"} and row["owner"] == "-", need_id
    source = SUITE.read_text(encoding="utf-8")
    assert "pytest.mark.xfail(\n            strict=True" in source


def test_page_counts_equal_the_table():
    rows = _page_rows()
    counts = {
        status: sum(1 for row in rows.values() if row["status"] == status)
        for status in STATUSES
    }
    expected = (
        f"{len(rows)} needs; {counts['red']} red"
    )
    text = " ".join(PAGE.read_text(encoding="utf-8").split())
    assert f"Counts (equal to the table): {expected}" in text
    assert f"{counts['unknown']} unknown" in text and f"{counts['green']} green" in text


def test_page_documents_the_command_the_variables_and_the_confirmation():
    text = PAGE.read_text(encoding="utf-8")
    assert (
        "python3 -m pytest -q -m devhub_live "
        "tests/integration/test_devhub_live_conformance.py"
    ) in text
    for name in (gate.ENV_URL, gate.ENV_CONFIRM, gate.ENV_TOKEN, gate.ENV_PROOF_SECRET):
        assert f"`{name}`" in text
    assert gate.CONFIRM in text
    assert "## Questions for the DevHub agent" in text


def test_live_suite_is_skipped_with_its_reason_without_the_url():
    env = {
        name: value for name, value in os.environ.items() if not name.startswith("DEVHUB_")
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
         "-m", "devhub_live", "-rs", str(SUITE)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout[-2000:]
    assert f"{len(_suite_tests())} skipped" in result.stdout
    assert "DEVHUB_LIVE_URL not set" in result.stdout
