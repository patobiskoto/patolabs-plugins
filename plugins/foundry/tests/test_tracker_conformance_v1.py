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
import copy
import json
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from foundry import config, registry, write
from foundry.models import Adr, Issue, Link, Project
from foundry.trackers.ghprojects import GitHubProjectsTracker
from foundry.trackers.linear import LinearTracker
from foundry.trackers.youtrack import YouTrackTracker


pytestmark = pytest.mark.tracker_conformance

TESTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TESTS_ROOT.parents[2]
MANIFEST_PATH = TESTS_ROOT / "fixtures" / "tracker-conformance-v1.json"
CONTRACT_PATH = REPO_ROOT / "plugins" / "foundry" / "docs" / "tracker-contract.v1.json"
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_BLOCKING = {"gap", "to_qualify"}
_SHARED_PROVIDER_CASES = {
    "test_repository_binding_v1.py::test_adapters_use_the_same_checkout_binding_resolution",
    "test_repository_binding_v1.py::test_existing_adapter_revalidates_marker_before_consuming_interrupted_update",
    "test_tracker_conformance_v1.py::test_provider_bounded_epic_closure_capability_is_declared",
    "test_tracker_conformance_v1.py::test_archived_binding_refusal_is_shared_by_bound_providers",
    "test_tracker_conformance_v1.py::test_missing_secret_backed_tracker_credentials_fail_before_transport",
}


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
    assert all("pending_ticket" not in case for case in manifest["cases"]), (
        "future provider work must be absent until its executable test is merged; "
        "dormant selectors are not conformance evidence"
    )


def test_multi_provider_labels_name_only_tests_that_execute_each_provider():
    manifest = _load(MANIFEST_PATH)

    for case in manifest["cases"]:
        if len(case["providers"]) > 1:
            assert case["test"] in _SHARED_PROVIDER_CASES, (
                f"{case['id']} labels several providers but does not execute a "
                "reviewed shared provider scenario"
            )
        if case["test"].startswith("test_tracker_contract.py::"):
            assert case["providers"] == [], (
                f"{case['id']} is contract metadata, not provider behavior"
            )


def test_conformance_manifest_references_existing_unit_tests_only():
    manifest = _load(MANIFEST_PATH)

    for case in manifest["cases"]:
        file_name, function_name = case["test"].split("::", 1)
        path = TESTS_ROOT / file_name
        assert path.is_file(), case["test"]
        functions = _function_decorators(path)
        assert function_name in functions, (
            f"{case['test']} is listed as conformance evidence but is absent"
        )
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


def _core_gate_failures(manifest: dict, contract: dict) -> list[str]:
    supported, blockers, covered = _core_coverage_state(manifest, contract)
    failures = []
    missing = supported - covered
    misleading = covered & blockers
    if missing:
        failures.append(
            "supported cells without executable provider behavior: "
            + ", ".join(f"{p}:{o}" for p, o in sorted(missing))
        )
    if misleading:
        failures.append(
            "blocking cells misrepresented as passing coverage: "
            + ", ".join(f"{p}:{o}" for p, o in sorted(misleading))
        )
    if blockers:
        rows = {row["id"]: row for row in contract["operations"]}
        failures.append(
            "core capabilities remain unavailable: "
            + ", ".join(
                f"{provider}:{operation}="
                f"{rows[operation]['cells'][provider]['status']}"
                f"({rows[operation]['cells'][provider]['ticket']})"
                for provider, operation in sorted(blockers)
            )
        )
    return failures


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
    failures = _core_gate_failures(stripped, contract)

    assert any(
        "supported cells without executable provider behavior" in failure
        and "youtrack:backlog-read" in failure
        for failure in failures
    )


def test_blocking_core_cell_cannot_be_mapped_as_passing_coverage():
    manifest = _load(MANIFEST_PATH)
    contract = copy.deepcopy(_load(CONTRACT_PATH))
    adr_read = next(row for row in contract["operations"] if row["id"] == "adr-read")
    adr_read["cells"]["ghprojects"] = {"status": "to_qualify", "ticket": "PAT-58"}

    failures = _core_gate_failures(manifest, contract)

    assert any(
        "blocking cells misrepresented as passing coverage" in failure
        and "ghprojects:adr-read" in failure
        for failure in failures
    )


def test_each_v1_provider_exercises_every_required_failure_category():
    manifest = _load(MANIFEST_PATH)
    contract = _load(CONTRACT_PATH)
    providers = {
        name for name, provider in contract["providers"].items()
        if provider.get("v1_core") is True
    }
    required = set(manifest["required_categories"])

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


def test_all_core_provider_cells_are_supported_and_covered():
    """The CI gate stays red until every core cell has provider behavior."""
    failures = _core_gate_failures(_load(MANIFEST_PATH), _load(CONTRACT_PATH))
    assert failures == [], "Tracker V1 conformance gate failed:\n- " + "\n- ".join(
        failures
    )


def test_public_ci_runs_the_named_conformance_suite():
    workflow = CI_PATH.read_text(encoding="utf-8")
    assert "pytest -q -m tracker_conformance tests" in workflow


def _tracker_with_failing_transport(provider: str):
    def unexpected_transport(*_args, **_kwargs):
        pytest.fail(f"{provider} refusal reached transport")

    if provider == "youtrack":
        tracker = object.__new__(YouTrackTracker)
        tracker._req = unexpected_transport
        return tracker
    if provider == "linear":
        return LinearTracker(
            token="synthetic-token-never-sent",
            transport=unexpected_transport,
        )
    return GitHubProjectsTracker(runner=unexpected_transport)


@pytest.mark.parametrize("provider", ["youtrack", "linear", "ghprojects"])
def test_provider_bounded_epic_closure_capability_is_declared(provider):
    tracker = _tracker_with_failing_transport(provider)
    assert tracker.bounded_epic_closure_supported is True
    # The distinct DevHub atomic capability is still intentionally unavailable.
    assert tracker.epic_closure_supported is False


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


@pytest.mark.parametrize("provider", ["youtrack", "linear"])
def test_archived_binding_refusal_is_shared_by_bound_providers(
    provider: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    state = tmp_path / "state"
    checkout = tmp_path / "acme" / "widgets"
    checkout.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "remote",
            "add",
            "origin",
            "https://github.com/acme/widgets.git",
        ],
        check=True,
    )
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    extra = {"canonical_repo": "github.com/acme/widgets"}
    project_id = "0-1"
    if provider == "linear":
        project_id = "00000000-0000-4000-8000-000000000001"
        extra.update(
            team_id="00000000-0000-4000-8000-000000000002",
            state_ids={
                name: f"00000000-0000-4000-8000-{index:012d}"
                for index, name in enumerate(registry._LINEAR_STATE_KEYS, start=10)
            },
            type_label_ids={
                name: f"00000000-0000-4000-8000-{index:012d}"
                for index, name in enumerate(registry._LINEAR_TYPE_KEYS, start=30)
            },
        )
    registry.register(provider, "widgets", "WID", project_id, **extra)
    data = registry.load()
    data[provider]["widgets"]["archive"] = True
    registry._save(data)

    with pytest.raises(SystemExit, match="archiv"):
        registry.repository_tracker_selection(str(checkout))


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


def test_youtrack_adr_status_conformance_performs_real_bounded_transition(
    monkeypatch: pytest.MonkeyPatch,
):
    tracker = object.__new__(YouTrackTracker)
    content = "> **ADR** · statut : `proposed`\n\nDecision"
    calls: list[tuple] = []

    def request(method, path, body=None, fields=None, top=None):
        nonlocal content
        calls.append((method, path, body, fields, top))
        if method == "GET" and fields == "project(id,shortName)":
            return {"project": {"id": "0-1", "shortName": "T"}}
        if method == "GET" and fields == "content":
            return {"content": content}
        if method == "POST" and path == "/articles/A-1":
            content = body["content"]
            return {}
        raise AssertionError(f"unexpected provider request: {method} {path}")

    tracker._req = request
    tracker._body_lock = lambda *_args: nullcontext()
    monkeypatch.setattr(registry, "load", lambda: {"youtrack": {}})

    tracker.set_adr_status(
        Adr(id="T-ADR-0001", title="Decision", status="proposed", ref="A-1"),
        "accepted",
        project=Project(key="T", id="0-1"),
    )

    posts = [call for call in calls if call[0] == "POST"]
    assert len(posts) == 1
    assert "statut : `accepted`" in posts[0][2]["content"]
    assert content == posts[0][2]["content"]


def test_youtrack_existing_child_reparent_uses_bound_command_and_readback(
    monkeypatch: pytest.MonkeyPatch,
):
    tracker = object.__new__(YouTrackTracker)
    project = Project(key="T", id="0-1")
    calls: list[tuple] = []
    linked = False
    reads = {"T-1": 0, "T-2": 0}

    def request(method, path, body=None, fields=None, top=None):
        nonlocal linked
        calls.append((method, path, body, fields))
        if method == "GET" and path in {"/issues/T-1", "/issues/T-2"}:
            assert fields == "project(id,shortName)"
            return {"project": {"id": "0-1", "shortName": "T"}}
        if method == "POST" and path == "/commands":
            assert body == {
                "query": "subtask of T-2",
                "issues": [{"idReadable": "T-1"}],
            }
            linked = True
            return {}
        raise AssertionError((method, path, body, fields, top))

    def issue(issue_id):
        reads[issue_id] += 1
        if issue_id == "T-1":
            parent = "T-2" if linked else "T-OLD"
            return Issue(issue_id, "existing child", links=[
                Link("subtask-of", "inward", parent),
            ])
        return Issue(issue_id, "new parent", type="Epic")

    tracker._req = request
    tracker.get_issue = issue
    monkeypatch.setattr(registry, "load", lambda: {"youtrack": {}})
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)

    write.link(tracker, "T-1", "subtask-of", "T-2")

    assert reads == {"T-1": 3, "T-2": 2}
    assert [call for call in calls if call[0] == "POST"] == [
        ("POST", "/commands", {
            "query": "subtask of T-2", "issues": [{"idReadable": "T-1"}],
        }, None),
    ]
    assert all(call[3] == "project(id,shortName)" for call in calls if call[0] == "GET")


def test_ghprojects_reparent_conformance_uses_reciprocal_readback(
    monkeypatch: pytest.MonkeyPatch,
):
    tracker = GitHubProjectsTracker()
    child = Issue(id="GH-1", title="child")
    parent = Issue(id="GH-2", title="parent", type="Epic")
    child_after = Issue(
        id="GH-1",
        title="child",
        links=[Link("subtask-of", "inward", "GH-2")],
    )
    parent_after = Issue(
        id="GH-2",
        title="parent",
        type="Epic",
        links=[Link("parent-of", "outward", "GH-1")],
    )
    reads = iter([child, parent, child, parent, child_after, parent_after])
    writes = []
    monkeypatch.setattr(
        tracker,
        "_authoritative_binding",
        lambda _project: SimpleNamespace(repo="acme/widgets"),
    )
    monkeypatch.setattr(
        tracker,
        "_native_issue",
        lambda issue_id, *_args: (1, 1001) if issue_id == "GH-1" else (2, 1002),
    )
    monkeypatch.setattr(tracker, "get_issue", lambda _issue_id: next(reads))
    monkeypatch.setattr(
        tracker,
        "_rest_write",
        lambda *args: writes.append(args) or {},
    )

    tracker.link(
        "GH-1",
        "subtask-of",
        "GH-2",
        project=Project(key="GH", id="PVT-1"),
    )

    assert writes == [
        (
            "POST",
            "repos/acme/widgets/issues/2/sub_issues",
            {"sub_issue_id": 1001, "replace_parent": True},
            "issue.link_write",
        )
    ]
