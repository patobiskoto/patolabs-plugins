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
from foundry.trackers.base import TrackerCapabilityUnavailableError
from foundry.trackers.ghprojects import GitHubProjectsTracker
from foundry.trackers.ghprojects import _Binding as GitHubBinding
from foundry.trackers.ghprojects import _CreateCandidate as GitHubCreateCandidate
from foundry.trackers.ghprojects import _FieldBinding as GitHubFieldBinding
from foundry.trackers.linear import LinearBindingError, LinearTracker
from foundry.trackers.youtrack import YouTrackTracker

from test_linear_tracker import LinearWire
from test_linear_tracker import PROJECT as LINEAR_PROJECT
from test_linear_tracker import connection as linear_connection


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


# --- PAT-139 / PAT-ADR-0018: the three writes of the companion Epic ------------------
# AGENTS.md#R9 (b) authorizes three tracker writes in advance: create the companion
# Epic, create a follow-up issue under it, add deferred remarks to that issue. Each test
# below performs exactly these three writes through the portable ``Tracker`` operations
# (``create_issue`` with ``Type: Epic`` and no parent, ``create_issue(parent=...)``,
# ``add_comment``) on one provider's fake transport, and checks that the origin Epic is
# never written and never linked. They prove that the adapters carry the writes on a
# fake transport; they prove nothing about a real provider project (for example whether
# a YouTrack project's Type field has an ``Epic`` value).
_COMPANION_FIELDS = {"Type": "Epic", "State": "backlog"}
_FOLLOW_UP_BODY = "- [ ] deferred remarks corrected"
_DEFERRED_REMARK = "deferred non-blocking remark"


class _YouTrackCompanionWire:
    """Stateful REST double: issues, the subtask command and comments."""

    def __init__(self, *, epic_type_exists: bool = True):
        self.epic_type_exists = epic_type_exists
        self.posts: list[tuple[str, object]] = []
        self.rows = {"T-1": self._row("T-1", "origin Epic", "", [
            {"name": "Type", "value": {"name": "Epic"}},
            {"name": "State", "value": {"name": "in-progress"}},
        ])}

    @staticmethod
    def _row(identifier, summary, description, custom_fields):
        return {
            "idReadable": identifier, "summary": summary, "description": description,
            "created": 1, "updated": 1, "project": {"id": "0-1", "shortName": "T"},
            "customFields": custom_fields, "links": [], "comments": [],
        }

    def __call__(self, method, path, body=None, fields=None, top=None):
        if method == "GET" and path.startswith("/issues/"):
            return copy.deepcopy(self.rows[path.split("/")[2]])
        self.posts.append((path, copy.deepcopy(body)))
        if method == "POST" and path == "/issues":
            custom = [
                {"name": field["name"], "value": field["value"]}
                for field in body["customFields"]
            ]
            if not self.epic_type_exists and any(
                field == {"name": "Type", "value": {"name": "Epic"}} for field in custom
            ):
                raise RuntimeError("synthetic provider refusal: unknown Type value")
            identifier = f"T-{len(self.rows) + 1}"
            self.rows[identifier] = self._row(
                identifier, body["summary"], body["description"], custom,
            )
            return {"idReadable": identifier}
        if method == "POST" and path == "/commands":
            role, _, parent = body["query"].rpartition(" ")
            assert role == "subtask of"
            link_type = {
                "name": "Subtask", "sourceToTarget": "parent for",
                "targetToSource": "subtask of",
            }
            for issue in body["issues"]:
                child = issue["idReadable"]
                self.rows[child]["links"].append({
                    "direction": "INWARD", "linkType": link_type,
                    "issues": [{"idReadable": parent}],
                })
                self.rows[parent]["links"].append({
                    "direction": "OUTWARD", "linkType": link_type,
                    "issues": [{"idReadable": child}],
                })
            return {}
        if method == "POST" and path.endswith("/comments"):
            self.rows[path.split("/")[2]]["comments"].append(
                {"text": body["text"], "created": 2},
            )
            return {"id": "comment-1"}
        raise AssertionError(f"unexpected provider request: {method} {path}")


def _youtrack_companion_tracker(monkeypatch, wire):
    tracker = object.__new__(YouTrackTracker)
    tracker._req = wire
    monkeypatch.setattr(registry, "load", lambda: {"youtrack": {}})
    return tracker


def test_youtrack_companion_epic_child_issue_and_comment(
    monkeypatch: pytest.MonkeyPatch,
):
    wire = _YouTrackCompanionWire()
    tracker = _youtrack_companion_tracker(monkeypatch, wire)
    project = Project(key="T", id="0-1")
    origin_before = copy.deepcopy(wire.rows["T-1"])

    companion = tracker.create_issue(project, "Nits T-1", "", fields=_COMPANION_FIELDS)
    follow_up = tracker.create_issue(
        project, "Deferred remarks, batch 1", _FOLLOW_UP_BODY, parent=companion.id,
    )
    tracker.add_comment(follow_up.id, _DEFERRED_REMARK, project=project)

    assert (companion.id, companion.type, companion.links) == ("T-2", "Epic", [])
    assert tracker.get_issue(companion.id).links == [
        Link("parent-of", "outward", follow_up.id),
    ]
    final = tracker.get_issue(follow_up.id)
    assert final.links == [Link("subtask-of", "inward", companion.id)]
    assert [comment["text"] for comment in final.comments] == [_DEFERRED_REMARK]
    assert wire.rows["T-1"] == origin_before
    assert [path for path, _body in wire.posts] == [
        "/issues", "/issues", "/commands", "/issues/T-3/comments",
    ]
    assert wire.posts[0][1]["customFields"] == [
        {"name": "Type", "$type": "SingleEnumIssueCustomField",
         "value": {"name": "Epic"}},
        {"name": "State", "$type": "StateIssueCustomField",
         "value": {"name": "backlog"}},
    ]


def test_youtrack_companion_epic_refusal_leaves_no_later_write(
    monkeypatch: pytest.MonkeyPatch,
):
    # The YouTrack adapter does not check that the project's Type field has an ``Epic``
    # value before it posts. The refusal below is SIMULATED by the double: what a real
    # project answers is not verified. The test shows only that the adapter then makes
    # no further write, which is what the fallback of R9 (b) relies on.
    wire = _YouTrackCompanionWire(epic_type_exists=False)
    tracker = _youtrack_companion_tracker(monkeypatch, wire)
    origin_before = copy.deepcopy(wire.rows["T-1"])

    with pytest.raises(RuntimeError, match="synthetic provider refusal"):
        tracker.create_issue(
            Project(key="T", id="0-1"), "Nits T-1", "", fields=_COMPANION_FIELDS,
        )

    assert [path for path, _body in wire.posts] == ["/issues"]
    assert wire.rows == {"T-1": origin_before}


class _LinearCompanionWire(LinearWire):
    """The existing GraphQL double, taught the ``Epic`` type label."""

    _TYPE_LABEL = {"id": "label-epic", "name": "Epic display"}

    def _apply(self, issue, values):
        values = dict(values)
        label_ids = values.pop("labelIds", None)
        super()._apply(issue, values)
        if label_ids is not None:
            assert label_ids == [self._TYPE_LABEL["id"]]
            issue["labels"] = linear_connection([dict(self._TYPE_LABEL)])

    def mutations(self) -> list[str]:
        return [
            name
            for document, _variables in self.calls
            for name in ("IssueCreate", "IssueUpdate", "IssueRelationCreate",
                         "CommentCreate")
            if f"FoundryLinear{name}" in document
        ]


def _linear_companion_tracker(type_label_ids: dict[str, str]):
    wire = _LinearCompanionWire()
    project = Project(
        key=LINEAR_PROJECT.key,
        id=LINEAR_PROJECT.id,
        extra={**LINEAR_PROJECT.extra, "type_label_ids": type_label_ids},
    )
    tracker = LinearTracker(token="synthetic-token-never-sent", transport=wire)
    tracker._activate(project)
    return tracker, wire, project


def test_linear_companion_epic_child_issue_and_comment():
    tracker, wire, project = _linear_companion_tracker(
        {"Epic": "label-epic", "Feature": "label-feature"},
    )
    origin_before = copy.deepcopy(wire.issues["LIN-1"])

    companion = tracker.create_issue(project, "Nits LIN-1", "", fields=_COMPANION_FIELDS)
    follow_up = tracker.create_issue(
        project, "Deferred remarks, batch 1", _FOLLOW_UP_BODY, parent=companion.id,
    )
    tracker.add_comment(follow_up.id, _DEFERRED_REMARK, project=project)

    assert (companion.id, companion.type, companion.links) == ("LIN-3", "Epic", [])
    assert tracker.get_issue(companion.id).links == [
        Link("parent-of", "outward", follow_up.id),
    ]
    assert tracker.get_issue(follow_up.id).links == [
        Link("subtask-of", "inward", companion.id),
    ]
    assert [
        comment["body"] for comment in wire.issues[follow_up.id]["comments"]["nodes"]
    ] == [_DEFERRED_REMARK]
    assert wire.issues["LIN-1"] == origin_before
    assert wire.mutations() == ["IssueCreate", "IssueCreate", "CommentCreate"]


def test_linear_companion_epic_without_type_mapping_refuses_before_any_write():
    # A Linear binding whose ``type_label_ids`` has no ``Epic`` entry: the adapter
    # refuses in ``_desired_update`` before the create mutation.
    tracker, wire, project = _linear_companion_tracker({"Feature": "label-feature"})
    issues_before = copy.deepcopy(wire.issues)

    with pytest.raises(LinearBindingError, match="type_unmapped"):
        tracker.create_issue(project, "Nits LIN-1", "", fields=_COMPANION_FIELDS)

    assert wire.mutations() == []
    assert wire.issues == issues_before


_GH_BINDING = GitHubBinding(
    owner="acme", number=7, project_id="PVT-1", repo="acme/widgets", key="GH",
)
_GH_PROJECT = Project(key="GH", id="PVT-1")
_GH_API = f"https://api.github.com/repos/{_GH_BINDING.repo}"


class _GitHubCompanionProvider:
    """Stateful double of the adapter's provider seams (REST, Project item, fields).

    Same seams as ``_install_create_harness`` in ``test_ghprojects_tracker.py``, with
    several issues instead of one. ``_write_catalog``, the create-intent journal, the
    create/resume sequence and the parent pairing checks stay the adapter's own code.
    """

    def __init__(self, *, type_options: tuple[str, ...]):
        self.type_options = type_options
        self.writes: list[tuple[str, str]] = []
        self.comments: dict[int, list[dict]] = {}
        self.issues = {1: {
            "title": "origin Epic", "body": "", "item": "item-1", "type": "Epic",
            "state": "in-progress", "parent": None,
        }}

    def install(self, monkeypatch, tracker):
        for name in (
            "_authoritative_binding", "verify_project_identity", "_field_catalog",
            "_create_candidates", "_rest_write", "_partial_project_issue",
            "_add_project_item", "_set_project_field", "_hydrate_issue",
            "_create_parent_snapshot", "_native_issue", "_item_coordinate", "_rows",
        ):
            monkeypatch.setattr(tracker, name, getattr(self, name))

    def _issue(self, number: int) -> Issue:
        row = self.issues[number]
        links = [
            Link("parent-of", "outward", f"GH-{child}")
            for child, other in sorted(self.issues.items())
            if other["parent"] == number
        ]
        if row["parent"] is not None:
            links.append(Link("subtask-of", "inward", f"GH-{row['parent']}"))
        return Issue(
            id=f"GH-{number}", title=row["title"], body=row["body"],
            type=row["type"], state=row["state"], links=links,
        )

    @staticmethod
    def _authoritative_binding(_project):
        return _GH_BINDING

    @staticmethod
    def verify_project_identity(_project):
        return True

    def _field_catalog(self, _binding):
        return {
            "type": GitHubFieldBinding(
                "type", "Foundry type", "SINGLE_SELECT",
                {option: f"type-{option}" for option in self.type_options},
            ),
            "state": GitHubFieldBinding(
                "state", "Foundry normalized state", "SINGLE_SELECT",
                {"backlog": "state-backlog"},
            ),
        }

    def _create_candidates(self, _binding, title, body):
        return [
            GitHubCreateCandidate(f"GH-{number}", number, 1000 + number, f"node-{number}")
            for number, row in sorted(self.issues.items())
            if (row["title"], row["body"]) == (title, body)
        ]

    def _rest_write(self, method, path, payload, operation):
        self.writes.append((method, path))
        prefix = f"repos/{_GH_BINDING.repo}/issues"
        if path == prefix:
            number = len(self.issues) + 1
            self.issues[number] = {
                "title": payload["title"], "body": payload["body"], "item": None,
                "type": None, "state": None, "parent": None,
            }
            return {
                "id": 1000 + number, "number": number, "node_id": f"node-{number}",
                "title": payload["title"], "body": payload["body"], "labels": [],
                "repository_url": _GH_API, "url": f"{_GH_API}/issues/{number}",
                "html_url": f"https://github.com/{_GH_BINDING.repo}/issues/{number}",
            }
        number = int(path.removeprefix(f"{prefix}/").split("/")[0])
        if path.endswith("/sub_issues"):
            assert payload["replace_parent"] is False
            self.issues[payload["sub_issue_id"] - 1000]["parent"] = number
            return {}
        if path.endswith("/comments"):
            rows = self.comments.setdefault(number, [])
            rows.append({"id": len(rows) + 1, "body": payload["body"]})
            return {**rows[-1], "issue_url": f"{_GH_API}/issues/{number}"}
        raise AssertionError((method, path, payload, operation))

    def _partial_project_issue(self, _binding, candidate):
        row = self.issues[candidate.number]
        if row["item"] is None:
            return None
        return row["item"], Issue(
            id=candidate.issue_id, title=row["title"], body=row["body"],
            type=row["type"], state=row["state"],
        )

    def _add_project_item(self, _binding, content_id):
        number = int(content_id.removeprefix("node-"))
        self.writes.append(("GRAPHQL", f"addProjectV2ItemById:{number}"))
        self.issues[number]["item"] = f"item-{number}"
        return f"item-{number}"

    def _set_project_field(self, item_id, _project_id, field, value):
        number = int(item_id.removeprefix("item-"))
        self.writes.append(("GRAPHQL", f"field:{number}:{field.id}={value}"))
        self.issues[number][field.id] = value

    def _hydrate_issue(self, issue, _binding):
        return self._issue(int(issue.id.removeprefix("GH-")))

    def _create_parent_snapshot(self, issue_id, _binding):
        number = int(issue_id.removeprefix("GH-"))
        return number, self._issue(number)

    def _native_issue(self, issue_id, _binding, _operation):
        number = int(issue_id.removeprefix("GH-"))
        return number, 1000 + number

    def _item_coordinate(self, issue_id, _binding):
        number = int(issue_id.removeprefix("GH-"))
        return self.issues[number]["item"], f"node-{number}"

    def _rows(self, path, _operation):
        number = int(path.split("/issues/")[1].split("/")[0])
        return copy.deepcopy(self.comments.get(number, []))


def test_ghprojects_companion_epic_child_issue_and_comment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    provider = _GitHubCompanionProvider(type_options=("Task", "Epic"))
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    provider.install(monkeypatch, tracker)
    origin_before = copy.deepcopy(provider.issues[1])

    companion = tracker.create_issue(
        _GH_PROJECT, "Nits GH-1", "", fields=_COMPANION_FIELDS,
    )
    follow_up = tracker.create_issue(
        _GH_PROJECT, "Deferred remarks, batch 1", _FOLLOW_UP_BODY, parent=companion.id,
    )
    tracker.add_comment(follow_up.id, _DEFERRED_REMARK, project=_GH_PROJECT)

    assert (companion.id, companion.type, companion.links) == ("GH-2", "Epic", [])
    assert (follow_up.id, follow_up.type) == ("GH-3", "Task")
    assert follow_up.links == [Link("subtask-of", "inward", "GH-2")]
    assert provider._issue(2).links == [Link("parent-of", "outward", "GH-3")]
    assert provider.comments == {3: [{"id": 1, "body": _DEFERRED_REMARK}]}
    assert provider.issues[1] == origin_before
    prefix = f"repos/{_GH_BINDING.repo}/issues"
    assert provider.writes == [
        ("POST", prefix),
        ("GRAPHQL", "addProjectV2ItemById:2"),
        ("GRAPHQL", "field:2:type=Epic"),
        ("GRAPHQL", "field:2:state=backlog"),
        ("POST", prefix),
        ("GRAPHQL", "addProjectV2ItemById:3"),
        ("GRAPHQL", "field:3:type=Task"),
        ("POST", f"{prefix}/2/sub_issues"),
        ("POST", f"{prefix}/3/comments"),
    ]


def test_ghprojects_companion_epic_without_type_option_refuses_before_any_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    # A Project whose "Foundry type" field has no ``Epic`` option: the adapter's own
    # ``_write_catalog`` refuses before the REST issue is created.
    provider = _GitHubCompanionProvider(type_options=("Task",))
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    provider.install(monkeypatch, tracker)
    issues_before = copy.deepcopy(provider.issues)

    with pytest.raises(TrackerCapabilityUnavailableError, match="field_option:type:Epic"):
        tracker.create_issue(_GH_PROJECT, "Nits GH-1", "", fields=_COMPANION_FIELDS)

    assert provider.writes == []
    assert provider.issues == issues_before
    assert not list(tmp_path.rglob("*.json"))


def test_companion_epic_writes_have_one_conformance_case_per_v1_provider():
    manifest = _load(MANIFEST_PATH)
    contract = _load(CONTRACT_PATH)
    providers = {
        name for name, provider in contract["providers"].items()
        if provider.get("v1_core") is True
    }
    cases = {case["id"]: case for case in manifest["cases"]}

    assert providers == {"youtrack", "linear", "ghprojects"}
    for provider in providers:
        writes = cases[f"{provider}-companion-epic-writes"]
        assert writes["providers"] == [provider]
        assert writes["operations"] == [
            "frame-intake-groom-create", "epics-children-creation", "mid-flight-comment",
        ]
        assert cases[f"{provider}-companion-epic-refusal"]["providers"] == [provider]
