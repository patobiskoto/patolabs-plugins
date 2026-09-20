"""Offline freeze and preflight regression tests for FOUNDRY-61."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).parents[1]
PILOT_ROOT = ROOT / "benchmarks" / "foundry-61"
REPOSITORY = ROOT.parents[1]


def _load_pilot():
    path = PILOT_ROOT / "paired-pilot-v2.py"
    spec = importlib.util.spec_from_file_location("foundry61_pilot", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pilot = _load_pilot()


def _load(name: str):
    return json.loads((PILOT_ROOT / name).read_text(encoding="utf-8"))


def test_complete_v2_freeze_restores_every_required_arm_and_pair():
    counts = pilot.validate_bundle(PILOT_ROOT, REPOSITORY)
    protocol = _load(pilot.PROTOCOL_FILE)

    assert counts == {
        "cases": 4,
        "pair_keys": 12,
        "profiles": 9,
        "future_calls": 108,
        "packet_bindings": 12,
        "v1_files": 22,
    }
    assert [row["id"] for row in protocol["matrix"]["claude"]] == [
        "claude-sonnet-low", "claude-sonnet-medium", "claude-sonnet-high",
        "claude-opus-high", "claude-fable-high",
    ]
    assert [row["id"] for row in protocol["matrix"]["codex"]] == [
        "codex-terra-low", "codex-terra-medium", "codex-terra-high",
        "codex-sol-high",
    ]
    assert protocol["matrix"]["future_attempts"]["executed"] == 0
    assert protocol["provenance"]["historical_v1_disposition"] == "immutable_and_inconclusive"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["matrix"]["claude"].pop(0),
        lambda value: value["matrix"]["codex"][0].__setitem__("effort_scope", "global"),
        lambda value: value["gates"]["reviewer"].__setitem__("minimum_effort", "medium"),
        lambda value: value["execution"].__setitem__("enabled", True),
        lambda value: value["matrix"]["future_attempts"].__setitem__("executed", 1),
    ],
)
def test_protocol_matrix_scope_gate_and_execution_drift_fail_closed(mutation):
    protocol = _load(pilot.PROTOCOL_FILE)
    mutation(protocol)
    with pytest.raises(pilot.PairedPilotV2Error):
        pilot.validate_protocol(protocol)


def test_packet_binding_must_be_one_to_one_with_exact_corpus_coordinates():
    corpus_keys = pilot.validate_corpus(_load(pilot.CORPUS_FILE))
    packets = _load(pilot.PACKETS_FILE)
    packets["bindings"][0]["revision"] = "0" * 40

    with pytest.raises(pilot.PairedPilotV2Error, match="one-to-one"):
        pilot.validate_packets(packets, corpus_keys)


def test_trace_contract_cannot_opt_legacy_jsonl_in_or_weaken_unknown_usage():
    contract = _load(pilot.TRACE_CONTRACT_FILE)
    contract["binding"]["legacy_or_unbound_jsonl"] = "accept"
    with pytest.raises(pilot.PairedPilotV2Error, match="binding"):
        pilot.validate_trace_contract(contract)
    contract = _load(pilot.TRACE_CONTRACT_FILE)
    contract["usage"]["unknown_is_never_zero"] = False
    with pytest.raises(pilot.PairedPilotV2Error, match="usage"):
        pilot.validate_trace_contract(contract)
    contract = _load(pilot.TRACE_CONTRACT_FILE)
    contract["usage"]["required_fields"].append("cache_write_input_tokens")
    contract["usage"]["optional_fields"] = []
    with pytest.raises(pilot.PairedPilotV2Error, match="usage"):
        pilot.validate_trace_contract(contract)
    contract = _load(pilot.TRACE_CONTRACT_FILE)
    contract["events"]["allowed_body"].remove("item.updated")
    with pytest.raises(pilot.PairedPilotV2Error, match="event"):
        pilot.validate_trace_contract(contract)


def test_unavailable_price_grid_cannot_invent_zero_or_partial_cost():
    protocol = _load(pilot.PROTOCOL_FILE)
    profile_ids = {
        row["id"]
        for host in pilot.HOSTS
        for row in protocol["matrix"][host]
    }
    prices = _load(pilot.PRICE_GRID_FILE)
    prices["profiles"][0]["input"] = 0
    with pytest.raises(pilot.PairedPilotV2Error, match="remain null"):
        pilot.validate_price_grid(prices, profile_ids)


@pytest.mark.parametrize(
    ("profile_id", "model", "effort"),
    [
        ("claude-sonnet-low", "sonnet", "low"),
        ("claude-sonnet-medium", "sonnet", "medium"),
        ("claude-sonnet-high", "sonnet", "high"),
        ("claude-opus-high", "opus", "high"),
        ("claude-fable-high", "fable", "high"),
    ],
)
def test_every_claude_arm_has_a_concrete_snapshot_packet_policy_command_binding(
    profile_id, model, effort,
):
    revision = "3fec3f1677ae6bd3ab5ef069afd6ca3073901b46"
    task_packet = "Goal:\nFrozen.\nInputs:\nPair.\nConstraints:\nOffline.\nDone when:\nBound."
    binding = pilot.ClaudeCommandBindingV2(
        pilot.CLAUDE_BINDING_ARTIFACT,
        pilot.CLAUDE_BINDING_VERSION,
        pilot.CLAUDE_COMMAND_CONTRACT_ID,
        pilot.CLAUDE_HOST_VERSION,
        profile_id,
        "f61-packet-001",
        hashlib.sha256(task_packet.encode("utf-8")).hexdigest(),
        revision,
        "baseline-v1",
    )

    assert pilot.claude_command_v2(
        binding,
        packet_id="f61-packet-001",
        task_packet=task_packet,
        revision=revision,
        context_policy="baseline-v1",
    ) == ("claude", "--model", model, "--effort", effort)


def test_claude_binding_rejects_profile_packet_snapshot_and_policy_drift():
    revision = "3fec3f1677ae6bd3ab5ef069afd6ca3073901b46"
    task_packet = "Goal:\nFrozen.\nInputs:\nPair.\nConstraints:\nOffline.\nDone when:\nBound."
    binding = pilot.ClaudeCommandBindingV2(
        pilot.CLAUDE_BINDING_ARTIFACT,
        pilot.CLAUDE_BINDING_VERSION,
        pilot.CLAUDE_COMMAND_CONTRACT_ID,
        pilot.CLAUDE_HOST_VERSION,
        "claude-sonnet-low",
        "f61-packet-001",
        hashlib.sha256(task_packet.encode("utf-8")).hexdigest(),
        revision,
        "baseline-v1",
    )
    cases = [
        (binding._replace(profile_id="claude-unknown"), {}, "profile"),
        (binding._replace(packet_id="f61-packet-002"), {}, "packet"),
        (binding._replace(packet_sha256="0" * 64), {}, "packet bytes"),
        (binding._replace(revision="0" * 40), {}, "snapshot"),
        (binding._replace(context_policy="lean-v1"), {}, "context policy"),
        (binding, {"packet_id": "f61-packet-002"}, "packet"),
        (binding, {"task_packet": "different"}, "packet bytes"),
        (binding, {"revision": "0" * 40}, "snapshot"),
        (binding, {"context_policy": "lean-v1"}, "context policy"),
    ]
    defaults = {
        "packet_id": "f61-packet-001",
        "task_packet": task_packet,
        "revision": revision,
        "context_policy": "baseline-v1",
    }
    for candidate, overrides, match in cases:
        with pytest.raises(pilot.PairedPilotV2Error, match=match):
            pilot.claude_command_v2(candidate, **{**defaults, **overrides})


def test_freeze_manifest_rejects_any_post_freeze_artifact_edit(tmp_path):
    copied = tmp_path / "foundry-61"
    shutil.copytree(PILOT_ROOT, copied)
    protocol = copied / pilot.PROTOCOL_FILE
    protocol.write_bytes(protocol.read_bytes() + b"\n")

    with pytest.raises(pilot.PairedPilotV2Error, match="frozen artifact changed"):
        pilot.validate_freeze(copied)


def test_preflight_is_offline_complete_sanitized_and_removes_disposable_claims(monkeypatch):
    original_run = subprocess.run
    original_claude_command = pilot.claude_command_v2
    original_codex_command = pilot.trace_v2.codex_command_v2
    observed = []
    claude_bindings = []
    codex_bindings = []

    def git_only(command, *args, **kwargs):
        assert command[0] == "git"
        observed.append(tuple(command))
        return original_run(command, *args, **kwargs)

    def observe_claude_binding(binding, **coordinates):
        command = original_claude_command(binding, **coordinates)
        claude_bindings.append(binding)
        return command

    def observe_codex_binding(binding, **coordinates):
        command = original_codex_command(binding, **coordinates)
        assert binding.packet_sha256 == hashlib.sha256(
            coordinates["task_packet"].encode("utf-8"),
        ).hexdigest()
        codex_bindings.append(binding)
        return command

    monkeypatch.setattr(subprocess, "run", git_only)
    monkeypatch.setattr(pilot, "claude_command_v2", observe_claude_binding)
    monkeypatch.setattr(pilot.trace_v2, "codex_command_v2", observe_codex_binding)
    result = pilot.run_preflight(REPOSITORY, PILOT_ROOT)

    assert result["status"] == "pass"
    assert result["cloud_calls"] == 0
    assert result["future_calls"] == 108
    assert result["concrete_profile_bindings"] == 108
    assert result["claude_command_bindings"] == 60
    assert result["codex_command_bindings"] == 48
    assert result["shared_contexts"] == 12
    assert result["exact_diff_claims"] == 12
    assert result["sol_reviewer_plans"] == 12
    assert result["disposable_state_removed"] is True
    assert result["production_state_mutations"] == 0
    assert result["v2_execution_status"] == "not_run"
    assert set(result["unavailable_metrics"].values()) == {None}
    assert result["raw_retained"] is False
    assert observed and all(command[0] == "git" for command in observed)
    assert len(claude_bindings) == 60
    assert len({(binding.profile_id, binding.packet_id) for binding in claude_bindings}) == 60
    assert {binding.packet_id for binding in claude_bindings} == {
        f"f61-packet-{ordinal:03d}" for ordinal in range(1, 13)
    }
    assert {binding.context_policy for binding in claude_bindings} == {"baseline-v1"}
    assert len(codex_bindings) == 48
    assert len({(binding.profile_id, binding.packet_id) for binding in codex_bindings}) == 48
    assert {binding.packet_id for binding in codex_bindings} == {
        f"f61-packet-{ordinal:03d}" for ordinal in range(1, 13)
    }
    assert {binding.context_policy for binding in codex_bindings} == {"baseline-v1"}
    assert {binding.revision for binding in codex_bindings} == {
        case[3] for case in pilot.EXPECTED_CASES
    }
    encoded = json.dumps(result)
    assert "diff_hash" not in encoded
    assert "claim_id" not in encoded
    assert "thread_id" not in encoded
    assert "packet_sha256" not in encoded


def test_v1_baseline_remains_byte_identical_and_f46_remains_inconclusive():
    baseline = _load(pilot.V1_BASELINE_FILE)
    assert pilot.validate_v1_baseline(baseline, REPOSITORY) == 22
    report = (ROOT / "benchmarks" / "foundry-46" / "REPORT-v1.md").read_text(
        encoding="utf-8",
    )
    assert "inconclusive" in report.lower()


def test_durable_v2_json_uses_exact_privacy_allowlists():
    for name in (
        pilot.PROTOCOL_FILE,
        pilot.CORPUS_FILE,
        pilot.PACKETS_FILE,
        pilot.TRACE_CONTRACT_FILE,
        pilot.PRICE_GRID_FILE,
    ):
        pilot._privacy_safe(_load(name), name)


def test_v2_is_additive_and_not_imported_by_the_v1_harness():
    source = (ROOT / "tooling" / "foundry" / "measurement_harness.py").read_text(
        encoding="utf-8",
    )
    assert "measurement_harness_v2" not in source
