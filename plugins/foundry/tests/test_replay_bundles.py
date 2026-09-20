"""Offline ADR-0022 replay bundle provisioning tests."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parents[1] / "benchmarks/foundry-117"
spec = importlib.util.spec_from_file_location(
    "replay_bundles", HERE / "replay_bundles.py"
)
bundles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundles)

PROVISIONING_ID = "0123456789abcdef0123456789abcdef"
PER_CALL_CAP_CENTS = 125


@pytest.fixture
def snapshot(tmp_path):
    root = (tmp_path / "fixture").resolve()
    root.mkdir()
    (root / "prompt.txt").write_text("frozen fixture", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "config.json").write_text('{"v":1}', encoding="utf-8")
    return root


@pytest.fixture
def session_graphs():
    """Small declared graphs; these are test fixtures, not production bounds."""
    return {
        "single-session": {
            "artifact": bundles.SESSION_GRAPH_ARTIFACT,
            "version": bundles.SESSION_GRAPH_VERSION,
            "arm": "single-session",
            "entry": "delivery",
            "stages": [{"name": "delivery", "max_visits": 1}],
            "transitions": [],
        },
        "chained": {
            "artifact": bundles.SESSION_GRAPH_ARTIFACT,
            "version": bundles.SESSION_GRAPH_VERSION,
            "arm": "chained",
            "entry": "start",
            "stages": [
                {"name": "start", "max_visits": 1},
                {"name": "work", "max_visits": 2},
                {"name": "finish", "max_visits": 1},
            ],
            "transitions": [
                ["start", "work"],
                ["work", "work"],
                ["work", "finish"],
            ],
        },
    }


@pytest.fixture
def manifest(tmp_path, snapshot, session_graphs):
    return bundles.build_manifest(
        fixture_snapshot=snapshot,
        destination=(tmp_path / "out").resolve(),
        session_graphs=session_graphs,
        per_call_cap_cents=PER_CALL_CAP_CENTS,
        provisioning_id=PROVISIONING_ID,
    )


def test_provisions_the_complete_two_arm_three_tier_matrix_offline(manifest):
    handoff = bundles.provision(manifest)
    assert handoff["artifact"] == bundles.RUNNER_HANDOFF_ARTIFACT
    assert {
        (
            entry["coordinate"]["arm"],
            entry["coordinate"]["tier"],
            entry["coordinate"]["replay"],
        )
        for entry in handoff["bundles"]
    } == {(arm, tier, 1) for arm in bundles.ARMS for tier in bundles.TIERS}
    assert Path(manifest["manifest_path"]).is_file()


def test_graph_caps_and_all_budgets_are_derived_before_provisioning(manifest):
    single = next(
        item for item in manifest["bundles"] if item["arm"] == "single-session"
    )
    chained = next(item for item in manifest["bundles"] if item["arm"] == "chained")
    assert single["session_graph"]["native_session_cap"] == 1
    assert single["budget"] == {
        "per_call_cap_cents": 125,
        "native_session_cap": 1,
        "replay_cap_cents": 125,
    }
    assert chained["session_graph"]["native_session_cap"] == 4
    assert chained["budget"] == {
        "per_call_cap_cents": 125,
        "native_session_cap": 4,
        "replay_cap_cents": 500,
    }
    assert manifest["aggregate_budget"] == {
        "per_call_cap_cents": 125,
        "per_replay_cap_cents": {"single-session": 125, "chained": 500},
        "per_arm_cap_cents": {"single-session": 375, "chained": 1500},
        "pair_cap_cents": 1875,
    }


@pytest.mark.parametrize("corruption", ["graph", "replay-budget", "aggregate-budget"])
def test_corrupted_graph_or_derived_budget_is_rejected(manifest, corruption):
    if corruption == "graph":
        manifest["bundles"][3]["session_graph"]["stages"][1]["max_visits"] = 3
    elif corruption == "replay-budget":
        manifest["bundles"][3]["budget"]["replay_cap_cents"] += 1
    else:
        manifest["aggregate_budget"]["pair_cap_cents"] += 1
    with pytest.raises(bundles.BundleRefusal, match="graph|budget"):
        bundles.validate_manifest(manifest)


@pytest.mark.parametrize(
    "corruption", ["boolean-version", "unhashable-replay", "traversal"]
)
def test_corrupted_manifest_types_and_inventory_refuse_closed(manifest, corruption):
    if corruption == "boolean-version":
        manifest["version"] = True
    elif corruption == "unhashable-replay":
        manifest["bundles"][0]["replay"] = []
    else:
        snapshot = manifest["bundles"][0]["snapshot"]
        file_digest = next(iter(snapshot["files"].values()))
        snapshot["files"] = {"../escape": file_digest}
        snapshot["sha256"] = bundles._digest(snapshot["files"])
    with pytest.raises(bundles.BundleRefusal):
        bundles.validate_manifest(manifest)


@pytest.mark.parametrize("corruption", ["zero-bound", "unreachable", "no-terminal"])
def test_declared_graph_must_be_finite_connected_and_versioned(
    tmp_path, snapshot, session_graphs, corruption
):
    graphs = copy.deepcopy(session_graphs)
    graph = graphs["chained"]
    if corruption == "zero-bound":
        graph["stages"][1]["max_visits"] = 0
    elif corruption == "unreachable":
        graph["stages"].append({"name": "orphan", "max_visits": 1})
    else:
        graph["transitions"].append(["finish", "start"])
    with pytest.raises(bundles.BundleRefusal, match="session graph"):
        bundles.build_manifest(
            fixture_snapshot=snapshot,
            destination=(tmp_path / corruption).resolve(),
            session_graphs=graphs,
            per_call_cap_cents=PER_CALL_CAP_CENTS,
            provisioning_id=PROVISIONING_ID,
        )


def test_empty_explicit_provisioning_identity_is_not_replaced(
    tmp_path, snapshot, session_graphs
):
    with pytest.raises(bundles.BundleRefusal, match="provisioning identity"):
        bundles.build_manifest(
            fixture_snapshot=snapshot,
            destination=(tmp_path / "invalid-id").resolve(),
            session_graphs=session_graphs,
            per_call_cap_cents=PER_CALL_CAP_CENTS,
            provisioning_id="",
        )


def test_bundles_roots_paths_and_provisioned_identities_are_globally_isolated(
    manifest,
):
    roots = [Path(entry["root"]) for entry in manifest["bundles"]]
    assert not any(
        left == right or left in right.parents or right in left.parents
        for index, left in enumerate(roots)
        for right in roots[index + 1 :]
    )
    paths = [
        Path(entry[key])
        for entry in manifest["bundles"]
        for key in ("checkout", "escalation_root", "receipts_root")
    ]
    assert len(paths) == len(set(paths)) == 18
    identities = [
        entry[key]
        for entry in manifest["bundles"]
        for key in ("tracker_issue", "pull_request")
    ]
    assert len({identity["token"] for identity in identities}) == 12
    assert all(
        identity["artifact"] == bundles.PROVISIONED_IDENTITY_ARTIFACT
        and "live_id" not in identity
        for identity in identities
    )


def test_identity_namespace_is_collision_safe_across_manifests(
    tmp_path, snapshot, session_graphs
):
    shared_destination = (tmp_path / "shared").resolve()
    first = bundles.build_manifest(
        fixture_snapshot=snapshot,
        destination=shared_destination,
        session_graphs=session_graphs,
        per_call_cap_cents=PER_CALL_CAP_CENTS,
        provisioning_id="a" * 32,
    )
    second = bundles.build_manifest(
        fixture_snapshot=snapshot,
        destination=shared_destination,
        session_graphs=session_graphs,
        per_call_cap_cents=PER_CALL_CAP_CENTS,
        provisioning_id="b" * 32,
    )
    third = bundles.build_manifest(
        fixture_snapshot=snapshot,
        destination=(tmp_path / "two").resolve(),
        session_graphs=session_graphs,
        per_call_cap_cents=PER_CALL_CAP_CENTS,
        provisioning_id="a" * 32,
    )
    first_tokens = {
        entry[kind]["token"]
        for entry in first["bundles"]
        for kind in ("tracker_issue", "pull_request")
    }
    second_tokens = {
        entry[kind]["token"]
        for entry in second["bundles"]
        for kind in ("tracker_issue", "pull_request")
    }
    third_tokens = {
        entry[kind]["token"]
        for entry in third["bundles"]
        for kind in ("tracker_issue", "pull_request")
    }
    assert first_tokens.isdisjoint(second_tokens)
    assert first_tokens.isdisjoint(third_tokens)
    assert second_tokens.isdisjoint(third_tokens)
    bundles.provision(first)
    bundles.provision(second)


def test_runner_restitution_is_typed_exact_and_bound_to_materialized_manifest(manifest):
    handoff = bundles.provision(manifest)
    one = bundles.restitution(manifest, arm="single-session", tier="balanced")
    assert one == handoff["bundles"][0]
    assert one["coordinate"] == {
        "arm": "single-session",
        "tier": "balanced",
        "replay": 1,
    }
    assert bundles.validate_runner_handoff(handoff, manifest) is handoff
    corrupted = copy.deepcopy(handoff)
    corrupted["bundles"][0]["budget"]["replay_cap_cents"] += 1
    with pytest.raises(bundles.BundleRefusal, match="handoff"):
        bundles.validate_runner_handoff(corrupted, manifest)


def test_collision_refuses_without_overwriting_an_existing_namespace(manifest):
    provision_root = Path(manifest["manifest_path"]).parent
    provision_root.mkdir(parents=True)
    sentinel = provision_root / "existing-evidence"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(bundles.BundleRefusal, match="collision"):
        bundles.provision(manifest)
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert not Path(manifest["manifest_path"]).exists()


def test_non_directory_namespace_parent_refuses_as_a_collision(manifest):
    namespace_parent = Path(manifest["manifest_path"]).parent.parent
    namespace_parent.parent.mkdir(parents=True)
    namespace_parent.write_text("foreign", encoding="utf-8")
    with pytest.raises(bundles.BundleRefusal, match="collision"):
        bundles.provision(manifest)
    assert namespace_parent.read_text(encoding="utf-8") == "foreign"


def test_symlink_and_snapshot_drift_refuse_before_materialization(manifest, snapshot):
    (snapshot / "link").symlink_to(snapshot / "prompt.txt")
    with pytest.raises(bundles.BundleRefusal, match="symlink|drift"):
        bundles.provision(manifest)
    assert not Path(manifest["manifest_path"]).parent.exists()


def test_snapshot_drift_refuses_runner_restitution_after_provisioning(
    manifest, snapshot
):
    bundles.provision(manifest)
    (snapshot / "prompt.txt").write_text("changed fixture", encoding="utf-8")
    with pytest.raises(bundles.BundleRefusal, match="snapshot drift"):
        bundles.restitute_for_runner(manifest)


@pytest.mark.parametrize("identity_kind", ["tracker_issue", "pull_request"])
def test_reused_or_missing_tracker_and_pr_identity_is_rejected(manifest, identity_kind):
    manifest["bundles"][1][identity_kind] = copy.deepcopy(
        manifest["bundles"][0][identity_kind]
    )
    with pytest.raises(bundles.BundleRefusal, match="missing or reused"):
        bundles.validate_manifest(manifest)
    del manifest["bundles"][1][identity_kind]
    with pytest.raises(bundles.BundleRefusal, match="schema"):
        bundles.validate_manifest(manifest)


def test_bundle_roots_cannot_be_nested_even_in_a_self_consistent_corruption(manifest):
    first_root = Path(manifest["bundles"][0]["root"])
    nested = first_root / "nested-bundle"
    target = manifest["bundles"][1]
    target["root"] = str(nested)
    target["checkout"] = str(nested / "checkout")
    target["escalation_root"] = str(nested / "escalation")
    target["receipts_root"] = str(nested / "receipts")
    with pytest.raises(bundles.BundleRefusal, match="root differs|overlap"):
        bundles.validate_manifest(manifest)


def test_destination_and_snapshot_are_never_nested(tmp_path, session_graphs):
    outer_snapshot = (tmp_path / "outer-snapshot").resolve()
    outer_snapshot.mkdir()
    (outer_snapshot / "fixture.txt").write_text("fixture", encoding="utf-8")
    with pytest.raises(bundles.BundleRefusal, match="overlaps fixture snapshot"):
        bundles.build_manifest(
            fixture_snapshot=outer_snapshot,
            destination=(outer_snapshot / "output").resolve(),
            session_graphs=session_graphs,
            per_call_cap_cents=PER_CALL_CAP_CENTS,
            provisioning_id=PROVISIONING_ID,
        )

    destination = (tmp_path / "outer-destination").resolve()
    nested_snapshot = destination / "fixture"
    nested_snapshot.mkdir(parents=True)
    (nested_snapshot / "fixture.txt").write_text("fixture", encoding="utf-8")
    with pytest.raises(bundles.BundleRefusal, match="overlaps fixture snapshot"):
        bundles.build_manifest(
            fixture_snapshot=nested_snapshot,
            destination=destination,
            session_graphs=session_graphs,
            per_call_cap_cents=PER_CALL_CAP_CENTS,
            provisioning_id=PROVISIONING_ID,
        )


def test_snapshot_path_cannot_be_corrupted_under_a_bundle_root(manifest):
    nested_snapshot = str(Path(manifest["bundles"][0]["root"]) / "source")
    for bundle in manifest["bundles"]:
        bundle["snapshot"]["path"] = nested_snapshot
    with pytest.raises(bundles.BundleRefusal, match="overlaps fixture snapshot"):
        bundles.validate_manifest(manifest)


def test_materialized_manifest_corruption_refuses_runner_handoff(manifest):
    bundles.provision(manifest)
    Path(manifest["manifest_path"]).write_text('{"artifact":"wrong"}', encoding="utf-8")
    with pytest.raises(bundles.BundleRefusal, match="materialized manifest"):
        bundles.restitute_for_runner(manifest)


def test_contaminated_root_or_receipt_space_refuses_runner_handoff(manifest):
    bundles.provision(manifest)
    receipts = Path(manifest["bundles"][0]["receipts_root"])
    (receipts / "foreign.json").write_text("{}", encoding="utf-8")
    with pytest.raises(bundles.BundleRefusal, match="contaminated"):
        bundles.restitute_for_runner(manifest)


def test_requested_bundle_outside_the_manifest_refuses(manifest):
    bundles.provision(manifest)
    with pytest.raises(bundles.BundleRefusal, match="not in manifest|tier differs"):
        bundles.restitution(manifest, arm="single-session", tier="unknown")
