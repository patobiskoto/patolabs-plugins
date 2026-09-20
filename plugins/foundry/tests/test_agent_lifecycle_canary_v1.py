"""Adversarial offline tests for FOUNDRY-69's passive observation contract."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "plugins/foundry/benchmarks/foundry-69/agent_lifecycle_observation_v1.py"
FIXTURES_PATH = ROOT / "plugins/foundry/benchmarks/foundry-69/fixtures-v1.json"
SMOKE_EVIDENCE_PATH = ROOT / "plugins/foundry/benchmarks/foundry-69/smoke-evidence-v1.json"
PROTOCOL_PATH = ROOT / "plugins/foundry/benchmarks/foundry-69/protocol-v1.json"


def _contract():
    spec = importlib.util.spec_from_file_location("foundry_69_contract_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _observation(contract, terminal=None):
    facts = {
        field: {"value": False, "provenance": "client_observed"}
        for field in contract.FACT_FIELDS
    }
    facts["planned"] = {"value": True, "provenance": "client_observed"}
    facts["delivered_to_host"] = {"value": True, "provenance": "host_reported"}
    if terminal:
        facts[terminal] = {"value": True, "provenance": "host_reported"}
    return {
        "schema_version": 1,
        "event": "agent_lifecycle_observation",
        "host": "claude",
        "facts": facts,
    }


@pytest.mark.parametrize("field", ("terminal_observed", "timeout_observed", "cancellation_observed"))
def test_each_explicit_terminal_classification(field):
    contract = _contract()
    assert contract.classify(_observation(contract, field)) == field


def test_unknown_is_bounded_and_silence_never_becomes_terminal():
    contract = _contract()
    record = _observation(contract)
    for field in contract.FACT_FIELDS:
        record["facts"][field] = {"value": None, "provenance": "unavailable"}
    assert contract.classify(record) == "unknown"
    assert contract.build_report(record)["limitation"] == "no_terminal_event_is_inferred_from_silence"


def test_unavailable_requires_null_and_observed_null_is_refused():
    contract = _contract()
    record = _observation(contract)
    record["facts"]["terminal_observed"] = {"value": False, "provenance": "unavailable"}
    with pytest.raises(contract.ContractError, match="must be null"):
        contract.validate_observation(record)
    record["facts"]["terminal_observed"] = {"value": None, "provenance": "client_observed"}
    with pytest.raises(contract.ContractError, match="require unavailable"):
        contract.validate_observation(record)


@pytest.mark.parametrize("invalid", (0, 1))
@pytest.mark.parametrize("field", ("planned", "delivered_to_host", "terminal_observed", "timeout_observed", "cancellation_observed"))
def test_facts_reject_integer_boolean_lookalikes(field, invalid):
    contract = _contract()
    record = _observation(contract)
    record["facts"][field] = {"value": invalid, "provenance": "client_observed"}
    with pytest.raises(contract.ContractError, match="controlled vocabulary"):
        contract.validate_observation(record)


def test_unknown_field_and_privacy_content_are_refused_recursively():
    contract = _contract()
    record = _observation(contract)
    record["facts"]["extra"] = {"value": True, "provenance": "client_observed"}
    with pytest.raises(contract.ContractError, match="recursively allowlisted"):
        contract.validate_observation(record)
    record = _observation(contract)
    record["prompt"] = "private text"
    with pytest.raises(contract.ContractError, match="recursively allowlisted"):
        contract.validate_observation(record)


def test_terminal_facts_are_mutually_exclusive():
    contract = _contract()
    record = _observation(contract, "terminal_observed")
    record["facts"]["timeout_observed"] = {"value": True, "provenance": "host_reported"}
    with pytest.raises(contract.ContractError, match="at most one"):
        contract.validate_observation(record)


@pytest.mark.parametrize("terminal", ("terminal_observed", "timeout_observed", "cancellation_observed"))
@pytest.mark.parametrize("parent", ("planned", "delivered_to_host"))
def test_terminal_facts_refuse_explicit_false_preceding_parent(terminal, parent):
    contract = _contract()
    record = _observation(contract, terminal)
    record["facts"][parent] = {"value": False, "provenance": "client_observed"}
    if parent == "planned":
        record["facts"]["delivered_to_host"] = {"value": None, "provenance": "unavailable"}
    with pytest.raises(contract.ContractError, match="false parent"):
        contract.validate_observation(record)


def test_versioned_host_matrix_covers_every_host_and_canonical_fact():
    contract = _contract()
    matrix = contract.validate_host_fact_matrix()
    assert set(matrix) == set(contract.HOSTS)
    for host in contract.HOSTS:
        assert set(matrix[host]) == set(contract.FACT_FIELDS)
        assert matrix[host]["timeout_observed"]["availability"] == "unavailable"
        assert matrix[host]["cancellation_observed"]["availability"] == "unavailable"
    incomplete = deepcopy(contract.HOST_FACT_MATRIX)
    del incomplete["codex"]["planned"]
    with pytest.raises(contract.ContractError, match="every canonical fact"):
        contract.validate_host_fact_matrix(incomplete)
    uncontrolled = deepcopy(contract.HOST_FACT_MATRIX)
    uncontrolled["claude"]["planned"]["sources"] = ("untrusted-source",)
    with pytest.raises(contract.ContractError, match="controlled values"):
        contract.validate_host_fact_matrix(uncontrolled)


def test_protocol_matrix_is_versioned_and_exhaustive():
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    assert protocol["host_fact_matrix_version"] == 1
    assert set(protocol["host_fact_matrix"]) == {"claude", "codex"}
    for host in protocol["host_fact_matrix"].values():
        assert set(host) == {
            "planned", "delivered_to_host", "terminal_observed", "timeout_observed", "cancellation_observed",
        }


def test_deterministic_count_discrepancy_is_unexplained_without_proof():
    contract = _contract()
    fixture = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    assert contract.reconcile_counts(fixture["reconciliation"]) == "unexplained"
    report = contract.build_report(fixture["observation"], fixture["reconciliation"])
    assert report["classification"] == "delivered_to_host"
    assert report["count_reconciliation"]["conclusion"] == "unexplained"
    proven = deepcopy(fixture["reconciliation"])
    proven["stale_orphan_proof"] = {"value": True, "provenance": "host_reported"}
    assert contract.reconcile_counts(proven) == "stale_orphan_proven"


def test_import_and_validation_have_no_external_mutation_or_calls(monkeypatch):
    contract = _contract()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("external boundary was called")

    monkeypatch.setattr("builtins.open", forbidden)
    result = contract.validate_observation(_observation(contract))
    assert result["host"] == "claude"
    assert set(sys.modules["foundry_69_contract_test"].__dict__) >= {
        "validate_observation", "build_report",
    }


def test_bounded_dual_host_smoke_evidence_is_content_free_and_terminal_only():
    contract = _contract()
    evidence = json.loads(SMOKE_EVIDENCE_PATH.read_text(encoding="utf-8"))
    clean = contract.validate_smoke_evidence(evidence)
    assert clean["content_retained"] is False
    assert set(clean["hosts"]) == {"claude", "codex"}
    for host in clean["hosts"].values():
        assert contract.classify(host["observation"]) == "terminal_observed"
        assert host["canary_invocations"] == 1
        assert host["automatic_retries"] == host["automatic_escalations"] == 0
        assert host["runtime_mutations"] == {
            "routing": False, "effort": False, "context": False, "gate": False, "result": False,
        }


def test_smoke_evidence_refuses_uncontrolled_source_or_content():
    contract = _contract()
    evidence = json.loads(SMOKE_EVIDENCE_PATH.read_text(encoding="utf-8"))
    evidence["hosts"]["codex"]["terminal_sources"] = "untrusted_text"
    with pytest.raises(contract.ContractError, match="controlled vocabulary"):
        contract.validate_smoke_evidence(evidence)
    evidence = json.loads(SMOKE_EVIDENCE_PATH.read_text(encoding="utf-8"))
    evidence["hosts"]["claude"]["prompt"] = "private text"
    with pytest.raises(contract.ContractError, match="recursively allowlisted"):
        contract.validate_smoke_evidence(evidence)
    evidence = json.loads(SMOKE_EVIDENCE_PATH.read_text(encoding="utf-8"))
    evidence["hosts"]["codex"]["canary_invocations"] = 2
    with pytest.raises(contract.ContractError, match="controlled vocabulary"):
        contract.validate_smoke_evidence(evidence)
    evidence = json.loads(SMOKE_EVIDENCE_PATH.read_text(encoding="utf-8"))
    evidence["hosts"]["claude"]["runtime_mutations"]["result"] = True
    with pytest.raises(contract.ContractError, match="controlled vocabulary"):
        contract.validate_smoke_evidence(evidence)
    evidence = json.loads(SMOKE_EVIDENCE_PATH.read_text(encoding="utf-8"))
    evidence["hosts"]["claude"]["runtime_mutations"]["path"] = "private-path"
    with pytest.raises(contract.ContractError, match="recursively allowlisted"):
        contract.validate_smoke_evidence(evidence)
