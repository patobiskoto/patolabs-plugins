"""Contract tests for the additive FOUNDRY-41 dual-runtime benchmark freeze."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-41"


def _module():
    spec = importlib.util.spec_from_file_location(
        "foundry41_benchmark_protocol", ROOT / "benchmark-protocol-v1.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROTOCOL = _module()


def test_frozen_protocol_is_additive_host_separated_and_pre_run():
    frozen = PROTOCOL.validate_freeze(ROOT)
    protocol = frozen["protocol"]
    assert protocol["status"] == "frozen_before_run"
    assert protocol["frozen_at"] == "2026-08-22T18:41:03+02:00"
    assert protocol["hosts"]["claude"]["cli"]["version"] == "2.1.224"
    assert protocol["hosts"]["codex"]["cli"]["version"] == "0.147.0"
    assert [profile["id"] for profile in protocol["hosts"]["claude"]["profiles"]] == [
        "claude-sonnet-medium", "claude-opus-high", "claude-fable-high",
    ]
    assert [profile["id"] for profile in protocol["hosts"]["codex"]["profiles"]] == [
        "codex-terra-medium", "codex-sol-high",
    ]
    assert [profile["model_version"] for profile in protocol["hosts"]["codex"]["profiles"]] == [
        "gpt-5.6-terra", "gpt-5.6-sol",
    ]
    assert protocol["aggregation"] == {
        "publish_by_host": True,
        "cross_host_aggregate": "forbidden unless weights are frozen in a later protocol version",
        "weights": None,
    }
    assert protocol["counterfactual"]["comparisons"] == {
        "claude": [
            {
                "candidate_profile_id": "claude-sonnet-medium",
                "control_profile_id": "claude-opus-high",
            },
            {
                "candidate_profile_id": "claude-fable-high",
                "control_profile_id": "claude-opus-high",
            },
        ],
        "codex": [
            {
                "candidate_profile_id": "codex-terra-medium",
                "control_profile_id": "codex-sol-high",
            },
        ],
    }
    assert len(frozen["corpus_keys"]) == 12
    assert {case["case_id"] for case in frozen["corpus"]["cases"]} == {
        "FOUNDRY-31", "FOUNDRY-33", "FOUNDRY-35", "FOUNDRY-40",
    }


def test_freeze_fails_closed_when_manifest_is_incomplete(tmp_path):
    target = tmp_path / "freeze-manifest-v1.json"
    target.write_text((ROOT / "freeze-manifest-v1.json").read_text(encoding="utf-8"), encoding="utf-8")
    import json

    value = json.loads(target.read_text(encoding="utf-8"))
    value["files"].pop("goldens-v1.json")
    target.write_text(json.dumps(value), encoding="utf-8")
    for name in PROTOCOL.REQUIRED_FILES:
        source = ROOT / name
        destination = tmp_path / name
        destination.write_bytes(source.read_bytes())
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="manifest is incomplete"):
        PROTOCOL.validate_freeze(tmp_path)


def test_mixed_host_profile_fails_closed():
    frozen = PROTOCOL.validate_freeze(ROOT)
    protocol = copy.deepcopy(frozen["protocol"])
    protocol["hosts"]["claude"]["profiles"][0]["id"] = "codex-terra-medium"
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="wrong host"):
        PROTOCOL._validate_protocol(protocol)


@pytest.mark.parametrize(("mutation", "message"), [
    (lambda value: value["hosts"]["claude"]["cli"].update(version="2.1.19"), "CLI version"),
    (
        lambda value: value["hosts"]["claude"]["cli"].update(
            version_source="/private/operator/path"
        ),
        "CLI identity/version/source",
    ),
    (
        lambda value: value.update(scope="production benchmark execution permitted"),
        "protocol scope",
    ),
    (lambda value: value.update(frozen_at="2026-08-22T18:41:02+02:00"), "freeze timestamp"),
    (lambda value: value["hosts"]["codex"]["profiles"][0].update(model_version="gpt-5.6"), "profiles"),
    (lambda value: value["hosts"]["codex"]["profiles"][0].update(model_url="https://developers.openai.com/api/docs/models/gpt-5.6-sol"), "profiles"),
])
def test_observed_versions_timestamp_and_host_model_identity_cannot_regress(mutation, message):
    frozen = PROTOCOL.validate_freeze(ROOT)
    protocol = copy.deepcopy(frozen["protocol"])
    mutation(protocol)
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match=message):
        PROTOCOL._validate_protocol(protocol)


def test_unpaired_result_rows_fail_closed():
    frozen = PROTOCOL.validate_freeze(ROOT)
    rows = _complete_rows(frozen)
    rows.pop()
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="complete host/profile matrix"):
        PROTOCOL.validate_result_rows(rows, frozen)


def test_result_rows_cannot_bind_to_a_reduced_corpus_key_set():
    frozen = PROTOCOL.validate_freeze(ROOT)
    omitted_key = next(iter(frozen["corpus_keys"]))
    rows = [
        row
        for row in _complete_rows(frozen)
        if (row["case_id"], row["revision"], row["repetition"]) != omitted_key
    ]
    forged = copy.deepcopy(frozen)
    forged["corpus_keys"].remove(omitted_key)

    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="validated frozen corpus"):
        PROTOCOL.validate_result_rows(rows, forged)


def test_result_rows_cannot_bind_to_an_arbitrary_corpus_key():
    frozen = PROTOCOL.validate_freeze(ROOT)
    rows = _complete_rows(frozen)
    source_key = next(iter(frozen["corpus_keys"]))
    arbitrary_key = ("FOUNDRY-999", "f" * 40, 1)
    arbitrary_rows = []
    for row in rows:
        if (row["case_id"], row["revision"], row["repetition"]) != source_key:
            continue
        arbitrary_row = copy.deepcopy(row)
        arbitrary_row.update(
            case_id=arbitrary_key[0],
            revision=arbitrary_key[1],
            repetition=arbitrary_key[2],
        )
        arbitrary_rows.append(arbitrary_row)
    rows.extend(arbitrary_rows)
    forged = copy.deepcopy(frozen)
    forged["corpus_keys"].add(arbitrary_key)

    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="validated frozen corpus"):
        PROTOCOL.validate_result_rows(rows, forged)


def test_result_rows_reject_a_coordinated_replacement_corpus_and_rows():
    frozen = PROTOCOL.validate_freeze(ROOT)
    forged = copy.deepcopy(frozen)
    forged["corpus"]["cases"][0].update(
        case_id="FOUNDRY-999",
        revision="f" * 40,
    )
    forged["corpus_keys"] = PROTOCOL._validate_corpus(forged["corpus"])
    rows = _complete_rows(forged)

    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="immutable validated freeze"):
        PROTOCOL.validate_result_rows(rows, forged)


@pytest.mark.parametrize("mutation", [
    lambda value: value["manifest_identity"].update(phase="run"),
    lambda value: value["artifact_digests"].update(
        {"corpus-v1.json": "0" * 64}
    ),
])
def test_result_rows_require_the_validated_manifest_identity_and_digests(mutation):
    frozen = PROTOCOL.validate_freeze(ROOT)
    forged = copy.deepcopy(frozen)
    mutation(forged)

    with pytest.raises(
        PROTOCOL.BenchmarkProtocolError,
        match="manifest identity or artifact digests",
    ):
        PROTOCOL.validate_result_rows(_complete_rows(frozen), forged)


def test_privacy_prohibited_evidence_fails_closed():
    frozen = PROTOCOL.validate_freeze(ROOT)
    corpus = copy.deepcopy(frozen["corpus"])
    corpus["cases"][0]["prompt"] = "must not be retained"
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="privacy-prohibited"):
        PROTOCOL._validate_corpus(corpus)


@pytest.mark.parametrize(("location", "key", "message"), [
    (("hosts", "claude"), "notes", "host section schema"),
    (("hosts", "claude", "cli"), "build", "host CLI identity"),
    (("hosts", "claude", "profiles", 0), "temperature", "profile schema"),
    (("hosts", "codex", "profiles", 0), "prompt", "privacy-prohibited"),
])
def test_protocol_host_cli_and_profile_unknown_fields_fail_closed(location, key, message):
    frozen = PROTOCOL.validate_freeze(ROOT)
    protocol = copy.deepcopy(frozen["protocol"])
    target = protocol
    for part in location:
        target = target[part]
    target[key] = "prohibited"
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match=message):
        PROTOCOL._validate_protocol(protocol)


@pytest.mark.parametrize(("key", "message"), [
    ("region", "price row schema"),
    ("secret", "privacy-prohibited"),
])
def test_price_grid_unknown_and_forbidden_fields_fail_closed(key, message):
    frozen = PROTOCOL.validate_freeze(ROOT)
    grid = copy.deepcopy(frozen["price_grid"])
    grid["profiles"][0][key] = "prohibited"
    profiles = PROTOCOL._validate_protocol(frozen["protocol"])
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match=message):
        PROTOCOL._validate_price_grid(grid, profiles)


@pytest.mark.parametrize(("artifact_name", "validator", "message"), [
    ("protocol", "_validate_protocol", "protocol schema"),
    ("corpus", "_validate_corpus", "corpus schema"),
    ("goldens", "_validate_goldens", "goldens schema"),
    ("price_grid", "_validate_price_grid", "price grid schema"),
])
def test_frozen_artifact_unknown_field_fails_closed(artifact_name, validator, message):
    frozen = PROTOCOL.validate_freeze(ROOT)
    artifact = copy.deepcopy(frozen[artifact_name])
    artifact["operator_note"] = "not part of the frozen schema"
    function = getattr(PROTOCOL, validator)
    if artifact_name == "price_grid":
        with pytest.raises(PROTOCOL.BenchmarkProtocolError, match=message):
            function(artifact, PROTOCOL._validate_protocol(frozen["protocol"]))
        return
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match=message):
        function(artifact)


def test_freeze_manifest_unknown_or_private_field_fails_closed(tmp_path):
    for name in PROTOCOL.REQUIRED_FILES | {"freeze-manifest-v1.json"}:
        (tmp_path / name).write_bytes((ROOT / name).read_bytes())
    import json

    manifest_path = tmp_path / "freeze-manifest-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["secret"] = "not allowed"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="privacy-prohibited"):
        PROTOCOL.validate_freeze(tmp_path)


@pytest.mark.parametrize("mutation", [
    lambda value: value["counterfactual"]["comparisons"]["claude"][0].update(
        control_profile_id="claude-fable-high"
    ),
    lambda value: value["counterfactual"]["comparisons"]["codex"][0].update(
        control_profile_id="claude-opus-high"
    ),
    lambda value: value["counterfactual"]["comparisons"]["claude"][1].update(
        candidate_profile_id="claude-sonnet-medium"
    ),
])
def test_counterfactual_mapping_change_cross_host_pair_and_duplicate_candidate_fail_closed(mutation):
    frozen = PROTOCOL.validate_freeze(ROOT)
    protocol = copy.deepcopy(frozen["protocol"])
    mutation(protocol)
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="counterfactual"):
        PROTOCOL._validate_protocol(protocol)


@pytest.mark.parametrize(("mutation", "message"), [
    (lambda value: value["execution"].update(operator="unexpected"), "execution schema"),
    (lambda value: value["execution"]["duration"].update(unit="seconds"), "duration schema"),
    (
        lambda value: value["execution"]["duration"].update(
            record="any non-empty duration description"
        ),
        "execution/duration policy",
    ),
    (lambda value: value["execution"].update(retries="one retry"), "execution/duration policy"),
    (
        lambda value: value["execution"]["duration"].update(wall_clock_seconds_max=901),
        "execution/duration policy",
    ),
])
def test_execution_and_duration_schema_values_and_types_fail_closed(mutation, message):
    frozen = PROTOCOL.validate_freeze(ROOT)
    protocol = copy.deepcopy(frozen["protocol"])
    mutation(protocol)
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match=message):
        PROTOCOL._validate_protocol(protocol)


@pytest.mark.parametrize(("section", "mutation", "message"), [
    (
        "pairing",
        lambda value: value.update(rule="Cross-host rows may be paired."),
        "pairing policy",
    ),
    (
        "aggregation",
        lambda value: value.update(method="weighted"),
        "aggregation policy",
    ),
    (
        "aggregation",
        lambda value: value.pop("publish_by_host"),
        "aggregation policy",
    ),
    (
        "aggregation",
        lambda value: value.update(publish_by_host=1),
        "aggregation policy",
    ),
    (
        "privacy",
        lambda value: value.update(retention="none"),
        "privacy policy",
    ),
    (
        "privacy",
        lambda value: value.pop("allowed_evidence"),
        "privacy policy",
    ),
    (
        "privacy",
        lambda value: value.update(allowed_evidence="sanitized evidence"),
        "privacy policy",
    ),
    (
        "counterfactual",
        lambda value: value.update(formula="candidate_cost_usd - paired_control_cost_usd"),
        "counterfactual formula",
    ),
    (
        "counterfactual",
        lambda value: value.update(rule="Unavailable values may be treated as zero."),
        "counterfactual formula",
    ),
    (
        "counterfactual",
        lambda value: value.pop("formula"),
        "counterfactual schema",
    ),
    (
        "counterfactual",
        lambda value: value.update(operator_note="not frozen"),
        "counterfactual schema",
    ),
])
def test_protocol_nested_policies_are_exact_and_fail_closed(section, mutation, message):
    frozen = PROTOCOL.validate_freeze(ROOT)
    protocol = copy.deepcopy(frozen["protocol"])
    mutation(protocol[section])
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match=message):
        PROTOCOL._validate_protocol(protocol)


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(content_policy="Historical metadata only."),
    lambda value: value.pop("content_policy"),
])
def test_corpus_content_policy_is_exact_and_required(mutation):
    frozen = PROTOCOL.validate_freeze(ROOT)
    corpus = copy.deepcopy(frozen["corpus"])
    mutation(corpus)
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="corpus (content policy|schema)"):
        PROTOCOL._validate_corpus(corpus)


@pytest.mark.parametrize(("mutation", "message"), [
    (
        lambda value: value["metrics"][0].update(type="number"),
        "metric definitions differ",
    ),
    (
        lambda value: value["metrics"].append(copy.deepcopy(value["metrics"][0])),
        "metric definitions differ",
    ),
    (
        lambda value: value["metrics"][0].update(description="tokens"),
        "metric definition schema",
    ),
])
def test_metric_definitions_are_exact_and_fail_closed(mutation, message):
    frozen = PROTOCOL.validate_freeze(ROOT)
    protocol = copy.deepcopy(frozen["protocol"])
    mutation(protocol)
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match=message):
        PROTOCOL._validate_protocol(protocol)


@pytest.mark.parametrize(("mutation", "message"), [
    (
        lambda value: value["required_row_fields"].append("operator_note"),
        "required row fields",
    ),
    (
        lambda value: value["host_reports"]["claude"].update(note="not run"),
        "host report schema",
    ),
    (
        lambda value: value["host_reports"]["claude"].update(gain=0),
        "host report differs",
    ),
    (
        lambda value: value["aggregate"].update(method="weighted"),
        "aggregate schema",
    ),
    (
        lambda value: value["aggregate"].update(rule="weights unavailable"),
        "aggregate must match",
    ),
    (
        lambda value: value["expected_outcomes"].update(operator_note=[]),
        "expected outcomes schema",
    ),
    (
        lambda value: value["expected_outcomes"]["test_outcome"].append("skipped"),
        "expected outcomes differ",
    ),
])
def test_goldens_nested_schemas_and_frozen_values_fail_closed(mutation, message):
    frozen = PROTOCOL.validate_freeze(ROOT)
    goldens = copy.deepcopy(frozen["goldens"])
    mutation(goldens)
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match=message):
        PROTOCOL._validate_goldens(goldens)


@pytest.mark.parametrize(("field", "value"), [
    ("retrieved_at", "2026-08-22T12:00:01+02:00"),
    ("retrieved_at", True),
    ("currency", "EUR"),
    ("unit", "per_token"),
    ("policy", "Unavailable exact-profile prices may be treated as zero."),
])
def test_price_grid_metadata_is_exact_and_typed(field, value):
    frozen = PROTOCOL.validate_freeze(ROOT)
    grid = copy.deepcopy(frozen["price_grid"])
    grid[field] = value
    profiles = PROTOCOL._validate_protocol(frozen["protocol"])
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match=f"price grid {field}"):
        PROTOCOL._validate_price_grid(grid, profiles)


def test_privacy_prohibited_key_in_nested_goldens_fails_closed():
    frozen = PROTOCOL.validate_freeze(ROOT)
    goldens = copy.deepcopy(frozen["goldens"])
    goldens["host_reports"]["claude"]["response"] = "must not be retained"
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="privacy-prohibited"):
        PROTOCOL._validate_goldens(goldens)


def _complete_rows(frozen):
    metrics = {
        "input_tokens": {"value": 0, "provenance": "host_reported"},
        "output_tokens": {"value": 0, "provenance": "host_reported"},
        "cached_input_tokens": {"value": None, "provenance": "unavailable"},
        "estimated_cost_usd": {"value": None, "provenance": "unavailable"},
        "duration_seconds": {"value": 1.0, "provenance": "client_observed"},
        "test_outcome": {"value": "pass", "provenance": "client_observed"},
        "review_outcome": {"value": "approved", "provenance": "host_reported"},
    }
    return [
        {
            "host": host,
            "profile_id": profile["id"],
            "case_id": case_id,
            "revision": revision,
            "repetition": repetition,
            "metrics": copy.deepcopy(metrics),
            "cache_state": None,
        }
        for host, section in frozen["protocol"]["hosts"].items()
        for profile in section["profiles"]
        for case_id, revision, repetition in frozen["corpus_keys"]
    ]


def test_complete_typed_host_profile_matrix_is_accepted():
    frozen = PROTOCOL.validate_freeze(ROOT)
    PROTOCOL.validate_result_rows(_complete_rows(frozen), frozen)


@pytest.mark.parametrize("duration", [0, 901, float("nan"), float("inf"), float("-inf")])
def test_duration_must_be_finite_observed_and_within_the_frozen_bound(duration):
    frozen = PROTOCOL.validate_freeze(ROOT)
    rows = _complete_rows(frozen)
    rows[0]["metrics"]["duration_seconds"] = {
        "value": duration,
        "provenance": "client_observed",
    }

    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="duration_seconds"):
        PROTOCOL.validate_result_rows(rows, frozen)


@pytest.mark.parametrize(("value", "provenance"), [
    (None, "unavailable"),
    (0.001, "client_observed"),
    (900, "client_observed"),
])
def test_duration_null_and_observed_boundaries_are_accepted(value, provenance):
    frozen = PROTOCOL.validate_freeze(ROOT)
    rows = _complete_rows(frozen)
    rows[0]["metrics"]["duration_seconds"] = {
        "value": value,
        "provenance": provenance,
    }

    PROTOCOL.validate_result_rows(rows, frozen)


@pytest.mark.parametrize(("name", "value", "provenance"), [
    ("input_tokens", 1, "client_observed"),
    ("output_tokens", 1, "pricing_derived"),
    ("cached_input_tokens", 1, "client_observed"),
    ("estimated_cost_usd", 1.0, "host_reported"),
    ("duration_seconds", 1.0, "host_reported"),
    ("test_outcome", "pass", "pricing_derived"),
    ("review_outcome", "approved", "pricing_derived"),
])
def test_metric_specific_provenance_rejects_incompatible_sources(
    name, value, provenance
):
    frozen = PROTOCOL.validate_freeze(ROOT)

    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="metric-specific provenance"):
        PROTOCOL._validate_metric(
            name,
            {"value": value, "provenance": provenance},
            frozen["goldens"]["expected_outcomes"],
        )


@pytest.mark.parametrize(("name", "value", "provenance"), [
    ("input_tokens", 1, "host_reported"),
    ("output_tokens", 1, "host_reported"),
    ("cached_input_tokens", 1, "host_reported"),
    ("estimated_cost_usd", 1.0, "pricing_derived"),
    ("duration_seconds", 1.0, "client_observed"),
    ("test_outcome", "pass", "client_observed"),
    ("review_outcome", "approved", "host_reported"),
])
def test_metric_specific_observed_provenance_is_accepted(name, value, provenance):
    frozen = PROTOCOL.validate_freeze(ROOT)

    PROTOCOL._validate_metric(
        name,
        {"value": value, "provenance": provenance},
        frozen["goldens"]["expected_outcomes"],
    )


@pytest.mark.parametrize("name", [
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "estimated_cost_usd",
    "duration_seconds",
    "test_outcome",
    "review_outcome",
])
def test_every_metric_keeps_unavailable_as_explicit_null(name):
    frozen = PROTOCOL.validate_freeze(ROOT)

    PROTOCOL._validate_metric(
        name,
        {"value": None, "provenance": "unavailable"},
        frozen["goldens"]["expected_outcomes"],
    )


@pytest.mark.parametrize(("value", "provenance", "message"), [
    (0, "pricing_derived", "exact-profile pricing"),
    (1.25, "pricing_derived", "exact-profile pricing"),
    (0, "host_reported", "metric-specific provenance"),
])
def test_unavailable_exact_profile_price_rejects_non_null_cost(
    value, provenance, message
):
    frozen = PROTOCOL.validate_freeze(ROOT)
    rows = _complete_rows(frozen)
    rows[0]["metrics"]["estimated_cost_usd"] = {
        "value": value,
        "provenance": provenance,
    }
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match=message):
        PROTOCOL.validate_result_rows(rows, frozen)


@pytest.mark.parametrize(("mutation", "message"), [
    (lambda rows: rows.append(copy.deepcopy(rows[0])), "duplicate result row"),
    (lambda rows: rows[0].update(prompt="private"), "privacy-prohibited"),
    (lambda rows: rows[0]["metrics"]["input_tokens"].update(value=None), "nullability/provenance"),
    (lambda rows: rows[0]["metrics"]["input_tokens"].update(provenance="unavailable"), "nullability/provenance"),
    (lambda rows: rows[0]["metrics"]["test_outcome"].update(value="free text"), "categorical metric differs"),
])
def test_result_schema_privacy_nullability_and_uniqueness_fail_closed(mutation, message):
    frozen = PROTOCOL.validate_freeze(ROOT)
    rows = _complete_rows(frozen)
    mutation(rows)
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match=message):
        PROTOCOL.validate_result_rows(rows, frozen)


@pytest.mark.parametrize("repetition", [True, 1.0])
def test_corpus_repetition_rejects_bool_and_numeric_equivalence(repetition):
    frozen = PROTOCOL.validate_freeze(ROOT)
    corpus = copy.deepcopy(frozen["corpus"])
    corpus["cases"][0]["repetitions"][0] = repetition
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="exact integer repetitions"):
        PROTOCOL._validate_corpus(corpus)


@pytest.mark.parametrize("estimate", [True, 5.0])
def test_corpus_estimate_requires_an_exact_integer(estimate):
    frozen = PROTOCOL.validate_freeze(ROOT)
    corpus = copy.deepcopy(frozen["corpus"])
    corpus["cases"][0]["estimate"] = estimate
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="real issue identifier"):
        PROTOCOL._validate_corpus(corpus)


@pytest.mark.parametrize("repetition", [True, 1.0])
def test_result_repetition_rejects_bool_and_numeric_equivalence(repetition):
    frozen = PROTOCOL.validate_freeze(ROOT)
    rows = _complete_rows(frozen)
    row = next(row for row in rows if row["repetition"] == 1)
    row["repetition"] = repetition
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="result pairing key types"):
        PROTOCOL.validate_result_rows(rows, frozen)


@pytest.mark.parametrize("repetition", [True, 1.0])
def test_supplied_corpus_pairing_key_rejects_bool_and_numeric_equivalence(repetition):
    frozen = PROTOCOL.validate_freeze(ROOT)
    forged = copy.deepcopy(frozen)
    forged["corpus_keys"] = {
        (case_id, revision, repetition if frozen_repetition == 1 else frozen_repetition)
        for case_id, revision, frozen_repetition in frozen["corpus_keys"]
    }
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="corpus pairing key types"):
        PROTOCOL.validate_result_rows(_complete_rows(frozen), forged)


def test_synthetic_case_metadata_fails_closed():
    frozen = PROTOCOL.validate_freeze(ROOT)
    corpus = copy.deepcopy(frozen["corpus"])
    corpus["cases"][0]["case_id"] = "synthetic-r41-feature"
    with pytest.raises(PROTOCOL.BenchmarkProtocolError, match="real issue identifier"):
        PROTOCOL._validate_corpus(corpus)
