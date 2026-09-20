"""Offline adversarial contract tests for FOUNDRY-57."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
from functools import lru_cache
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-57"


def _module():
    spec = importlib.util.spec_from_file_location(
        "foundry57_hygiene", ROOT / "tool-hygiene-v1.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HYGIENE = _module()


@lru_cache(maxsize=1)
def _fixtures():
    value = json.loads((ROOT / "fixtures-v1.json").read_text(encoding="utf-8"))
    return value, HYGIENE.validate_fixtures(value)


def test_offline_report_is_deterministic_and_all_six_families_are_structural():
    result = HYGIENE.validate_artifacts()
    report = result["report"]
    _, fixtures = _fixtures()
    assert HYGIENE.build_report(fixtures) == report
    assert report["volume"]["input_lines"] > report["volume"]["output_lines"]
    assert report["noise"]["removed_lines"] > 0
    assert set(report["fixture_output_sha256"]) == {
        "massive-log", "repeated-diagnostic", "structured-json", "unified-diff",
        "search-results", "red-test",
    }


def test_f51_binding_is_persisted_and_derived_from_the_trusted_freeze():
    frozen = HYGIENE.validate_f51_freeze()
    raw, _ = _fixtures()
    protocol = json.loads((ROOT / "protocol-v1.json").read_text(encoding="utf-8"))
    report = json.loads((ROOT / "report-v1.json").read_text(encoding="utf-8"))
    assert frozen["binding"] == HYGIENE.F51_BINDING
    assert raw["f51_freeze_binding"] == protocol["f51_freeze_binding"]
    assert raw["f51_freeze_binding"] == report["f51_freeze_binding"]
    assert frozen["required_ids"] == {
        binding["f51_candidate_id"]
        for fixture in raw["fixtures"]
        for binding in fixture["diagnostic_bindings"]
    }


def test_altered_f51_corpus_and_manifest_fail_closed(tmp_path):
    copied = tmp_path / "foundry-51"
    shutil.copytree(HYGIENE.F51_ROOT, copied)
    corpus = copied / "corpus-v1.json"
    corpus.write_bytes(corpus.read_bytes() + b" ")
    with pytest.raises(HYGIENE.HygieneError, match="frozen artifact digest differs: corpus"):
        HYGIENE.validate_f51_freeze(f51_root=copied)

    shutil.rmtree(copied)
    shutil.copytree(HYGIENE.F51_ROOT, copied)
    manifest = copied / "freeze-manifest-v1.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(HYGIENE.HygieneError, match="freeze manifest digest differs"):
        HYGIENE.validate_f51_freeze(f51_root=copied)


def test_missing_f51_relevant_digest_and_non_descendant_fail_closed(monkeypatch):
    manifest = json.loads(
        (HYGIENE.F51_ROOT / "freeze-manifest-v1.json").read_text(encoding="utf-8"),
    )
    manifest["files"].pop("corpus-v1.json")
    with pytest.raises(HYGIENE.HygieneError, match="relevant digest is missing"):
        HYGIENE._validate_f51_manifest(manifest)

    parent = "6e28842f046fb3b16b7d7e817a00e87f314c5e08"
    monkeypatch.setattr(HYGIENE, "_head_revision", lambda _repository: parent)
    with pytest.raises(HYGIENE.HygieneError, match="does not descend from freeze"):
        HYGIENE.validate_f51_freeze()


def test_removed_adjacent_duplicate_resolves_only_by_explicit_equivalence():
    _, fixtures = _fixtures()
    repeated = next(item for item in fixtures if item["id"] == "repeated-diagnostic")
    output = HYGIENE.transform(repeated)
    row = next(
        item for item in output["required_diagnostics"]
        if item["f51_candidate_id"] == "C40-hooks"
    )
    assert row["availability"] == "equivalent"
    assert row["equivalence"] == {
        "reason": "adjacent_duplicate_same_canonical_line_and_binding_marker",
        "removed_source_line": 2,
        "retained_source_line": 1,
    }
    locator = row["locator"]
    assert locator["visible_line"] == 1
    assert locator["line_sha256"] == HYGIENE.digest(output["visible_lines"][1])
    assert locator["binding_marker"] in output["visible_lines"][1]


def test_non_equivalent_cap_loss_stays_missing_even_when_marker_exists_elsewhere():
    raw, _ = _fixtures()
    capped = copy.deepcopy(raw)
    binding = capped["fixtures"][0]["diagnostic_bindings"][0]
    binding["source_line"] = 11
    binding["binding_marker"] = "event=stage"
    capped_fixtures = HYGIENE.validate_fixtures(capped)
    report = HYGIENE.build_report(capped_fixtures)
    output = HYGIENE.transform(capped_fixtures[0])
    assert "event=stage" in output["visible_lines"][0]
    assert output["required_diagnostics"][0] == {
        "f51_candidate_id": "C35-raw", "availability": "missing",
    }
    assert report["conservation"]["state"] == "fail"
    assert report["false_negatives"] == {"count": 1, "state": "detected"}
    with pytest.raises(HYGIENE.HygieneError, match="required diagnostics lost"):
        HYGIENE.validate_report(report, capped_fixtures)


def test_unified_diff_hunk_counts_are_coherent_and_range_mismatch_fails_closed():
    raw, _ = _fixtures()
    diff = next(item for item in raw["fixtures"] if item["id"] == "unified-diff")
    assert diff["lines"][2] == "@@ -1 +1,2 @@"
    mismatched = copy.deepcopy(raw)
    bad = next(item for item in mismatched["fixtures"] if item["id"] == "unified-diff")
    bad["lines"][2] = "@@ -1 +1 @@"
    with pytest.raises(HYGIENE.HygieneError, match="hunk range count differs"):
        HYGIENE.validate_fixtures(mismatched)


def test_character_canonicalization_is_not_a_zero_count_line_removal():
    _, fixtures = _fixtures()
    output = HYGIENE.transform(fixtures[0])
    canonicalization = output["events"][0]
    assert canonicalization["kind"] == "character_canonicalization"
    assert canonicalization["affected_lines"] == 1
    assert canonicalization["changed_characters"] == 2
    assert canonicalization["input_sha256"] != canonicalization["output_sha256"]
    assert all(
        event.get("removed_lines", 1) > 0
        for event in output["events"] if event["kind"] == "line_removal"
    )


@pytest.mark.parametrize(
    "key",
    ["Token", "API_KEY", "accessToken", "client-password", "PrivateKey"],
)
def test_privacy_secret_key_names_are_case_insensitive(key):
    with pytest.raises(HYGIENE.HygieneError, match="privacy-forbidden field"):
        HYGIENE._privacy({key: "SAFE_MARKER"})


@pytest.mark.parametrize(
    "key",
    [
        "token_value", "password_hash", "api_key_value",
        "private_key_material", "credential_data",
    ],
)
def test_privacy_normalized_compound_secret_key_names_fail_closed(key):
    with pytest.raises(
        HYGIENE.HygieneError, match=r"^privacy-forbidden field$",
    ):
        HYGIENE._privacy({key: "SYNTHETIC_REJECTION_MARKER"})


@pytest.mark.parametrize("key", ["raw_output_value", "artifact_path_hint"])
def test_privacy_normalized_compound_raw_content_key_names_fail_closed(key):
    with pytest.raises(
        HYGIENE.HygieneError, match=r"^privacy-forbidden field$",
    ):
        HYGIENE._privacy({key: "SYNTHETIC_REJECTION_MARKER"})


def test_privacy_allows_benign_key_and_value():
    assert HYGIENE._privacy({"diagnostic_summary": "synthetic benign marker"}) is None


@pytest.mark.parametrize(
    "marker",
    [
        "xoxb-SAFE_MARKER", "xoxa-SAFE_MARKER", "xoxp-SAFE_MARKER",
        "xoxr-SAFE_MARKER", "xoxs-SAFE_MARKER", "AIzaSAFE_MARKER",
        "sk_SAFE_MARKER", "ghp_SAFE_MARKER", "github_pat_SAFE_MARKER",
        "Bearer SAFE_MARKER", "-----BEGIN PRIVATE KEY-----",
        "password=SAFE_MARKER", "api_key:SAFE_MARKER", "Token=SAFE_MARKER",
    ],
)
def test_privacy_generic_secret_markers_fail_closed(marker):
    with pytest.raises(HYGIENE.HygieneError, match="privacy-forbidden text"):
        HYGIENE._privacy(f"synthetic rejection specimen {marker}")


def test_f43_metric_provenance_is_per_metric_and_contract_tampering_fails_closed(
    tmp_path, monkeypatch,
):
    _, fixtures = _fixtures()
    report = HYGIENE.build_report(fixtures)
    latency = report["latency_ms"]
    cost = report["cost_usd"]
    assert (latency["state"], latency["value"]) == ("unavailable", None)
    assert latency["f43_measurement_contract"] == {
        "artifact": "foundry.dual_host.measurement_protocol",
        "version": 1,
        "protocol_sha256": HYGIENE.F43_PROTOCOL_SHA256,
        "source": "client_observed",
        "source_metric": "duration_seconds",
        "availability_reason": "offline_no_client_observation",
        "comparison": "not_claimed",
    }
    assert (cost["state"], cost["value"]) == ("unavailable", None)
    assert cost["f43_measurement_contract"]["source"] == "always_unavailable"
    assert cost["f43_measurement_contract"]["source_metric"] == "estimated_cost_usd"
    assert cost["f43_measurement_contract"]["comparison"] == "not_claimed"

    altered = tmp_path / "protocol-v1.json"
    altered.write_bytes(HYGIENE.F43_PROTOCOL.read_bytes() + b" ")
    monkeypatch.setattr(HYGIENE, "F43_PROTOCOL", altered)
    with pytest.raises(HYGIENE.HygieneError, match="contract digest differs"):
        HYGIENE.build_report(fixtures)


def test_fabricated_reports_fail_closed():
    _, fixtures = _fixtures()
    report = HYGIENE.build_report(fixtures)
    forged = copy.deepcopy(report)
    forged["fixture_output_sha256"].pop("red-test")
    with pytest.raises(HYGIENE.HygieneError, match="deterministic or derived"):
        HYGIENE.validate_report(forged, fixtures)

    forged = copy.deepcopy(report)
    forged["latency_ms"] = {"state": "observed", "value": 0}
    with pytest.raises(HYGIENE.HygieneError, match="deterministic or derived"):
        HYGIENE.validate_report(forged, fixtures)


def test_f51_manifest_sha_constant_matches_checked_in_bytes():
    manifest = (HYGIENE.F51_ROOT / "freeze-manifest-v1.json").read_bytes()
    assert hashlib.sha256(manifest).hexdigest() == HYGIENE.F51_MANIFEST_SHA256
