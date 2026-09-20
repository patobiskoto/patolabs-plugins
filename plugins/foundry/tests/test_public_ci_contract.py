"""Regression coverage for the public successor's bounded test exclusions."""

import ast
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
TESTS_ROOT = Path(__file__).resolve().parent

EXPECTED_HISTORICAL_FIXTURES = {
    (
        "test_query_profiles.py",
        "test_historical_measurement_command_order_matches_skill_workflows",
    ),
    (
        "test_query_profiles.py",
        "test_reproducible_five_workflow_measurement_exceeds_reduction_target",
    ),
    (
        "test_query_profiles.py",
        "test_versioned_baseline_matches_fresh_generator_except_duration",
    ),
    (
        "test_release_contract.py",
        "test_published_release_sections_and_documents_stay_frozen",
    ),
}


def _historical_fixture_markers(file_name):
    tree = ast.parse((TESTS_ROOT / file_name).read_text(encoding="utf-8"))
    markers = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if ast.unparse(decorator.func) != "pytest.mark.historical_fixture":
                continue
            reason = next(
                (
                    keyword.value.value
                    for keyword in decorator.keywords
                    if keyword.arg == "reason"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ),
                "",
            )
            markers[(file_name, node.name)] = reason
    return markers


def test_historical_fixture_exclusions_are_exact_and_individually_justified():
    markers = {}
    for file_name in ("test_query_profiles.py", "test_release_contract.py"):
        markers.update(_historical_fixture_markers(file_name))

    assert set(markers) == EXPECTED_HISTORICAL_FIXTURES
    assert all(reason.strip() for reason in markers.values())


def test_public_ci_collects_active_tests_from_both_mixed_modules():
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8",
    )
    foundry_job = workflow.split("  foundry:", 1)[1].split("\n  ship-ios:", 1)[0]

    assert "not historical_fixture" in foundry_job
    assert "--ignore=tests/test_query_profiles.py" not in foundry_job
    assert "--ignore=tests/test_release_contract.py" not in foundry_job
    assert "--ignore=tests/test_routing_contract.py" in foundry_job
