"""PAT-68: deterministic Tracker-port conformance suite composition.

The suite deliberately reuses existing fake-transport regression tests instead of
copying one provider harness three times. The versioned manifest is the executable
index: pytest marks every referenced test with tracker_conformance at collection
time, while this module checks that the index stays aligned with the V1 contract.

A core cell marked supported must have at least one executable case for that
provider. A core cell still marked gap or to_qualify remains an explicit V1
blocker with an owner ticket; it is never represented as a passing conformance case.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from foundry import config, registry
from foundry.models import Project
from foundry.trackers.linear import LinearTracker
from foundry.trackers.youtrack import YouTrackTracker


pytestmark = pytest.mark.tracker_conformance

TESTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TESTS_ROOT.parents[2]
MANIFEST_PATH = TESTS_ROOT / "fixtures" / "tracker-conformance-v1.json"
CONTRACT_PATH = REPO_ROOT / "plugins" / "foundry" / "docs" / "tracker-contract.v1.json"
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_BLOCKING = {"gap", "to_qualify"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _function_decorators(path: Path) -> dict[str, set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        out[node.name] = {ast.unparse(item) for item in node.decorator_list}
    return out


def test_conformance_manifest_is_versioned_and_unique():
    manifest = _load(MANIFEST_PATH)

    assert manifest["schema"] == "foundry.tracker-conformance.v1"
    assert manifest["contract"] == "plugins/foundry/docs/tracker-contract.v1.json"
    ids = [case["id"] for case in manifest["cases"]]
    selectors = [case["test"] for case in manifest["cases"]]
    assert len(ids) == len(set(ids))
    assert len(selectors) == len(set(selectors))


def test_conformance_manifest_references_existing_unit_tests_only():
    manifest = _load(MANIFEST_PATH)

    for case in manifest["cases"]:
        file_name, function_name = case["test"].split("::", 1)
        path = TESTS_ROOT / file_name
        assert path.is_file(), case["test"]
        functions = _function_decorators(path)
        assert function_name in functions, case["test"]
        decorators = functions[function_name]
        assert not any(
            marker.startswith("pytest.mark.integration")
            or marker.startswith("pytest.mark.skip")
            for marker in decorators
        ), f"{case['test']} is not deterministic public-unit coverage"


def test_conformance_categories_and_adversarial_matrix_are_complete():
    manifest = _load(MANIFEST_PATH)
    categories = {
        category
        for case in manifest["cases"]
        for category in case.get("categories", [])
    }
    adversarial = {
        scenario
        for case in manifest["cases"]
        for scenario in case.get("adversarial", [])
    }

    assert categories == set(manifest["required_categories"])
    assert adversarial == set(manifest["required_adversarial"])


def _core_coverage_state(manifest: dict, contract: dict):
    v1_providers = {
        name for name, provider in contract["providers"].items()
        if provider.get("v1_core") is True
    }
    covered = {
        (provider, operation)
        for case in manifest["cases"]
        for provider in case["providers"]
        for operation in case.get("operations", [])
    }

    supported = set()
    blockers = set()
    for operation in contract["operations"]:
        if not operation["core"]:
            continue
        for provider in v1_providers:
            cell = operation["cells"][provider]
            pair = (provider, operation["id"])
            if cell["status"] == "supported":
                supported.add(pair)
            else:
                assert cell["status"] in _BLOCKING, pair
                assert isinstance(cell.get("ticket"), str) and cell["ticket"].startswith(
                    "PAT-"
                ), pair
                blockers.add(pair)
    return supported, blockers, covered


def test_supported_core_cells_have_executable_provider_cases():
    manifest = _load(MANIFEST_PATH)
    contract = _load(CONTRACT_PATH)
    supported, blockers, covered = _core_coverage_state(manifest, contract)

    assert supported <= covered, (
        "supported V1 core cells without conformance case: "
        + ", ".join(
            f"{provider}:{operation}"
            for provider, operation in sorted(supported - covered)
        )
    )
    assert not (covered & blockers), (
        "blocking V1 cells were misrepresented as passing conformance cases: "
        + ", ".join(
            f"{provider}:{operation}"
            for provider, operation in sorted(covered & blockers)
        )
    )


def test_missing_supported_core_case_is_detected_not_skipped():
    manifest = _load(MANIFEST_PATH)
    contract = _load(CONTRACT_PATH)
    target = ("youtrack", "backlog-read")

    stripped = {
        **manifest,
        "cases": [
            {
                **case,
                "operations": [
                    operation
                    for operation in case.get("operations", [])
                    if not (
                        "youtrack" in case["providers"]
                        and operation == "backlog-read"
                    )
                ],
            }
            for case in manifest["cases"]
        ],
    }
    supported, _blockers, covered = _core_coverage_state(stripped, contract)

    assert target in supported
    assert target not in covered


def test_each_v1_provider_exercises_every_required_failure_category():
    manifest = _load(MANIFEST_PATH)
    contract = _load(CONTRACT_PATH)
    providers = {
        name for name, provider in contract["providers"].items()
        if provider.get("v1_core") is True
    }
    required = {
        "core-operations", "explicit-refusals", "pagination",
        "permissions", "conflicts",
    }

    for provider in providers:
        categories = {
            category
            for case in manifest["cases"]
            if provider in case["providers"]
            for category in case.get("categories", [])
        }
        assert required <= categories, (
            provider,
            sorted(required - categories),
        )


def test_public_ci_runs_the_named_conformance_suite():
    workflow = CI_PATH.read_text(encoding="utf-8")
    assert "pytest -q -m tracker_conformance tests" in workflow


def test_missing_secret_backed_tracker_credentials_fail_before_transport(
    monkeypatch: pytest.MonkeyPatch,
):
    def missing(key: str) -> str:
        raise SystemExit(f"Config manquante : {key}")

    monkeypatch.setattr(config, "require", missing)

    with pytest.raises(SystemExit, match="YOUTRACK_URL"):
        YouTrackTracker()
    with pytest.raises(SystemExit, match="LINEAR_API_TOKEN"):
        LinearTracker()


def test_youtrack_comment_conformance_uses_bound_target_then_single_post(
    monkeypatch: pytest.MonkeyPatch,
):
    tracker = object.__new__(YouTrackTracker)
    calls: list[tuple] = []

    def request(method, path, body=None, fields=None, top=None):
        calls.append((method, path, body, fields, top))
        if method == "GET" and path == "/issues/T-1":
            return {"project": {"id": "0-1", "shortName": "T"}}
        if method == "POST" and path == "/issues/T-1/comments":
            return {"id": "comment-1"}
        raise AssertionError(f"unexpected provider request: {method} {path}")

    tracker._req = request
    monkeypatch.setattr(registry, "load", lambda: {"youtrack": {}})

    tracker.add_comment(
        "T-1",
        "bounded progress",
        project=Project(key="T", id="0-1"),
    )

    assert calls == [
        ("GET", "/issues/T-1", None, "project(id,shortName)", None),
        ("POST", "/issues/T-1/comments", {"text": "bounded progress"}, "id", None),
    ]
