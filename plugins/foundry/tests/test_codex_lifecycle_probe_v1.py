"""Offline adversarial tests for the FOUNDRY-66 protocol definition."""
from __future__ import annotations

import ast
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
PROBE_PATH = (
    ROOT / "plugins/foundry/benchmarks/foundry-66/probe-codex-lifecycle-v1.py"
)
PROTOCOL_PATH = ROOT / "plugins/foundry/benchmarks/foundry-66/protocol-v1.json"


def _probe():
    spec = importlib.util.spec_from_file_location("foundry_66_probe_test", PROBE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_frozen_checker_is_definition_only_and_reports_zero_external_calls():
    probe = _probe()
    assert probe.check() == {
        "status": "pass",
        "classification": "offline_definition_validated",
        "protocol_version": 1,
        "lifecycle_verdicts_validated": 4,
        "host_calls": 0,
        "native_calls": 0,
        "provider_calls": 0,
        "campaign_calls": 0,
    }


def test_checker_is_deterministic_and_classification_is_not_a_verdict():
    probe = _probe()
    first = probe.check()
    second = probe.check()
    assert first == second
    assert "verdict" not in first
    assert first["classification"] not in probe.VERDICTS


def test_module_has_no_launch_signing_or_runtime_authorization_surface():
    source = PROBE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".", 1)[0])
    assert imported_modules <= {
        "__future__",
        "argparse",
        "json",
        "pathlib",
        "typing",
    }

    forbidden_parameter_fragments = {
        "factory",
        "launcher",
        "verifier",
        "clock",
        "ledger",
        "nonce",
        "authorization",
        "path",
        "root",
        "store",
    }
    forbidden_callable_fragments = {
        "launch",
        "execute",
        "sign",
        "verify",
        "preflight",
        "authorize",
        "ledger",
        "persist",
    }
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parameter_names = {
            argument.arg
            for argument in (
                node.args.posonlyargs
                + node.args.args
                + node.args.kwonlyargs
            )
        }
        if node.args.vararg:
            parameter_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            parameter_names.add(node.args.kwarg.arg)
        assert not any(
            fragment in name.lower()
            for name in parameter_names
            for fragment in forbidden_parameter_fragments
        )
        assert not any(
            fragment in node.name.lower()
            for fragment in forbidden_callable_fragments
        )

    forbidden_calls = {
        "Popen",
        "check_call",
        "check_output",
        "execv",
        "fork",
        "open",
        "spawn",
        "system",
        "urlopen",
        "write_bytes",
        "write_text",
    }
    called_names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called_names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called_names.add(node.func.attr)
    assert called_names.isdisjoint(forbidden_calls)
    assert "import " + "sub" + "process" not in source
    assert "from " + "sub" + "process" not in source
    assert "Process" + "Factory" not in source
    assert "Authorization" + "Verifier" not in source


def test_protocol_preserves_the_future_invocation_and_authorization_contract():
    probe = _probe()
    protocol = probe.build_protocol()
    invocation = protocol["future_invocation_contract"]
    authorization = protocol["future_authorization_contract"]
    scope = protocol["scope"]

    assert scope["definition_only"] is True
    assert scope["execution_entry_point"] == "absent"
    assert scope["cli_mode"] == "check_only"
    assert scope["f59_campaign_authority_eligible"] is False
    assert scope["f59_campaign_evidence_eligible"] is False
    assert invocation == {
        "maximum_native_invocations": 1,
        "native_timeout_cap_seconds": 300,
        "deadline_covers_full_invocation_boundary": True,
        "timeout_cleanup_requires_process_exit": True,
        "automatic_retries": 0,
        "automatic_corrections": 0,
        "automatic_escalations": 0,
        "second_invocation": "forbidden",
        "execution_mechanics_implementation": probe._DEFERRED,
        "hard_timeout_enforcement_implementation": probe._DEFERRED,
    }
    assert authorization["maximum_lifetime_seconds"] == 900
    assert authorization["maximum_issued_age_seconds"] == 300
    assert authorization[
        "fresh_dedicated_external_f66_authorization_required"
    ] is True
    assert authorization["durable_anti_replay_required"] is True
    assert authorization["nonce_consumed_before_future_invocation"] is True
    assert authorization["nonce_reuse_refused"] is True
    assert authorization["authorization_boundary_implementation"] == probe._DEFERRED
    assert authorization["trusted_verifier_implementation"] == probe._DEFERRED
    assert authorization["nonce_persistence_implementation"] == probe._DEFERRED
    assert authorization["f59_campaign_authority_eligible"] is False
    assert authorization["f59_campaign_evidence_eligible"] is False


def test_frozen_protocol_is_exact_and_recursively_rejects_unknown_fields():
    probe = _probe()
    frozen = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    probe.validate_protocol(frozen)
    assert frozen == probe.build_protocol()

    changed = deepcopy(frozen)
    changed["scope"]["benign_marker"] = False
    with pytest.raises(probe.ProtocolError, match="recursively allowlisted"):
        probe.validate_protocol(changed)

    changed = deepcopy(frozen)
    changed["future_authorization_contract"]["nested"] = {}
    with pytest.raises(probe.ProtocolError, match="recursively allowlisted"):
        probe.validate_protocol(changed)

    changed = deepcopy(frozen)
    changed["limitations"][0] = "private/path"
    with pytest.raises(probe.ProtocolError, match="forbidden content"):
        probe.validate_protocol(changed)


def test_protocol_has_six_milestones_and_exactly_four_future_verdicts():
    probe = _probe()
    protocol = probe.build_protocol()
    assert tuple(protocol["content_free_milestones"]) == probe.MILESTONE_FIELDS
    assert len(set(protocol["content_free_milestones"])) == 6
    assert tuple(protocol["future_verdict_contract"]["allowed"]) == (
        "terminal_observed",
        "native_timeout",
        "guard_refusal",
        "inconclusive",
    )
    assert set(protocol["future_verdict_contract"]["allowed"]) == probe.VERDICTS
    assert probe.CHECK_CLASSIFICATION not in probe.VERDICTS


@pytest.mark.parametrize("verdict", [
    "terminal_observed",
    "native_timeout",
    "guard_refusal",
    "inconclusive",
])
def test_offline_observation_fixtures_are_content_free_and_non_evidentiary(verdict):
    probe = _probe()
    fixture = probe.build_observation_fixture(verdict)
    probe.validate_observation_fixture(fixture)

    assert fixture["status"] == "offline_schema_fixture"
    assert fixture["evidence_scope"] == "not_execution_evidence"
    assert set(fixture["milestones"]) == set(probe.MILESTONE_FIELDS)
    assert all(type(value) is bool for value in fixture["milestones"].values())
    assert fixture["execution"] == {
        "maximum_native_invocations": 1,
        "native_invocation_attempts": 0,
        "host_calls": 0,
        "provider_calls": 0,
        "campaign_calls": 0,
        "automatic_retries": 0,
        "automatic_corrections": 0,
        "automatic_escalations": 0,
        "second_invocations": 0,
    }
    assert all(value is None for value in fixture["metrics"].values())
    assert fixture["metric_provenance"] == {
        name: "unavailable" for name in probe.METRIC_FIELDS
    }
    assert all(
        value is False
        for key, value in fixture["privacy"].items()
        if key != "closed_schema"
    )


def test_observation_schema_recursively_rejects_unknown_persisted_fields():
    probe = _probe()
    fixture = probe.build_observation_fixture("guard_refusal")

    changed = deepcopy(fixture)
    changed["milestones"]["benign_marker"] = False
    with pytest.raises(probe.ProtocolError, match="recursively allowlisted"):
        probe.validate_observation_fixture(changed)

    changed = deepcopy(fixture)
    changed["metrics"]["extra_metric"] = None
    with pytest.raises(probe.ProtocolError, match="recursively allowlisted"):
        probe.validate_observation_fixture(changed)

    changed = deepcopy(fixture)
    changed["privacy"]["extra_flag"] = False
    with pytest.raises(probe.ProtocolError, match="recursively allowlisted"):
        probe.validate_observation_fixture(changed)


def test_every_unavailable_metric_must_remain_null():
    probe = _probe()
    fixture = probe.build_observation_fixture("inconclusive")
    for metric in probe.METRIC_FIELDS:
        changed = deepcopy(fixture)
        changed["metrics"][metric] = 0
        with pytest.raises(probe.ProtocolError, match="unavailable metric"):
            probe.validate_observation_fixture(changed)


def test_observation_verdict_milestone_relationships_remain_closed():
    probe = _probe()
    terminal = probe.build_observation_fixture("terminal_observed")
    terminal["milestones"]["process_exit_seen"] = False
    with pytest.raises(probe.ProtocolError, match="terminal_observed"):
        probe.validate_observation_fixture(terminal)

    timed_out = probe.build_observation_fixture("native_timeout")
    timed_out["milestones"]["timeout_cleanup_applied"] = False
    with pytest.raises(probe.ProtocolError, match="native_timeout"):
        probe.validate_observation_fixture(timed_out)


def test_strict_frozen_json_reader_refuses_duplicate_keys():
    probe = _probe()
    with pytest.raises(probe.ProtocolError, match="invalid"):
        probe._strict_json_document('{"version":1,"version":1}')


def test_cli_exposes_only_check(capsys):
    probe = _probe()
    assert probe.main(["--check"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == probe.check()

    with pytest.raises(SystemExit) as absent:
        probe.main([])
    assert absent.value.code == 2

    unsupported_mode = "--" + "execute"
    with pytest.raises(SystemExit) as unsupported:
        probe.main([unsupported_mode])
    assert unsupported.value.code == 2
