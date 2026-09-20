from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks" / "foundry-59" / "materialize-context-packs-v2.py"


def _module():
    spec = importlib.util.spec_from_file_location("foundry64_context_protocol_v2", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


M = _module()


@pytest.fixture(scope="module")
def matrix():
    return M.build_matrix()


@pytest.fixture(scope="module")
def frozen_inputs():
    return M._frozen_inputs()


def _triples(matrix):
    grouped = {}
    for result in matrix["coordinates"]:
        grouped.setdefault(result["coordinate"]["pair_id"], []).append(result)
    return grouped


def test_complete_f51_matrix_is_materialized_with_full_paired_coordinates(matrix):
    assert matrix["status"] == "pass"
    assert matrix["planned_coordinates"] == 126
    assert matrix["materialized_coordinates"] == 126
    assert matrix["failures"] == []
    assert len(matrix["coordinates"]) == 126
    assert {row["coordinate"]["arm_id"] for row in matrix["coordinates"]} == {
        "A",
        "B",
        "C",
    }
    assert {
        row["coordinate"]["case_id"] for row in matrix["coordinates"]
    } == set(M._load(M.F51_CORPUS)["cases"][index]["id"] for index in range(7))
    grouped = _triples(matrix)
    assert len(grouped) == 42
    for triple in grouped.values():
        assert [row["coordinate"]["arm_id"] for row in triple] == ["A", "B", "C"]
        for field in M.PAIR_INVARIANTS:
            assert len({row["coordinate"][field] for row in triple}) == 1
        for result in triple:
            coordinate = result["coordinate"]
            assert coordinate["role"] == "implementer"
            assert coordinate["model"]
            assert coordinate["reasoning_effort"] == "medium"
            assert coordinate["conditions_id"] == "f59-conditions-v2"
            assert M.SHA256.fullmatch(coordinate["conditions_sha256"])


def test_arm_a_is_explained_current_unbrokered_baseline_and_not_arm_b(matrix):
    for arm_a, arm_b, _arm_c in _triples(matrix).values():
        assert arm_a["common_execution_input"] == arm_b["common_execution_input"]
        assert arm_a["arm_contract"] == {
            "label": "current_foundry_unbrokered",
            "selection_policy": "no_preinjected_candidate_selection",
            "composition_policy": "no_preinjected_retrieved_candidate_context",
            "candidate_universe_consulted": False,
            "preinjected_retrieved_context": False,
            "host_tool_exploration": "natural_current_host_tools_measured_at_execution",
            "tool_result_policy": "current_foundry",
        }
        assert arm_a["selection_reason"] == "no_preinjected_retrieved_candidate_context"
        assert arm_a["context_pack"]["elements"] == []
        assert arm_a["context_pack"]["injected_tokens"] == 0
        assert arm_a["candidate_audit"] == []
        assert arm_a["runtime_observation_contract"]["natural_host_tool_calls"] == {
            "value": None,
            "provenance": "unavailable_before_execution",
        }
        assert arm_b["arm_contract"]["selection_policy"] != arm_a["arm_contract"][
            "selection_policy"
        ]
        assert arm_b["arm_contract"]["candidate_universe_consulted"] is True
        assert arm_b["arm_contract"]["preinjected_retrieved_context"] is True
        assert arm_b["context_pack"]["elements"]
        assert arm_b["context_pack"]["injected_tokens"] > 0


def test_manifest_binds_f51_f56_f58_f61_f59_artifacts_and_exact_plan(matrix):
    manifest = M._validate_manifest()
    for name, path in M.DEPENDENCIES.items():
        assert manifest["source_digests"][name] == M._bytes_sha256(path)
    for name, path in M.ARTIFACTS.items():
        assert manifest["artifact_digests"][name] == M._bytes_sha256(path)
    assert manifest["superseded_f59_v1_digests"] == M.SUPERSEDED_F59_V1_DIGESTS
    plan = [result["coordinate"] for result in matrix["coordinates"]]
    assert M.canonical_sha256(plan) == manifest["plan_sha256"]
    assert matrix["freeze_binding"]["plan_sha256"] == manifest["plan_sha256"]
    assert matrix["freeze_binding"]["source_digests"] == manifest["source_digests"]


def test_dependency_drift_fails_closed(monkeypatch, tmp_path):
    altered = tmp_path / "corpus-v1.json"
    altered.write_text("{}\n", encoding="utf-8")
    monkeypatch.setitem(M.DEPENDENCIES, "f51_corpus", altered)
    with pytest.raises(M.ProtocolFreezeError, match="f51_corpus digest differs"):
        M._validate_manifest()


def test_labels_are_only_post_selection_input(frozen_inputs):
    row = next(item for item in frozen_inputs.plan if item["arm_id"] == "B")
    cache = {}
    before = M._materialize_row(row, frozen_inputs, M.REPOSITORY, cache)
    changed_cases = dict(frozen_inputs.cases)
    changed_case = copy.deepcopy(changed_cases[row["case_id"]])
    for candidate in changed_case["candidates"]:
        candidate["label"] = "noise"
    changed_cases[row["case_id"]] = changed_case
    changed_inputs = M.FrozenInputs(
        frozen_inputs.protocol,
        frozen_inputs.manifest,
        changed_cases,
        frozen_inputs.history,
        frozen_inputs.budget,
        frozen_inputs.plan,
    )
    after = M._materialize_row(row, changed_inputs, M.REPOSITORY, cache)
    assert after["selection_reason"] == before["selection_reason"]
    assert after["context_pack"] == before["context_pack"]
    assert after["candidate_audit"] == before["candidate_audit"]
    assert after["post_selection_evaluation"] != before["post_selection_evaluation"]
    assert after["labels_used_as_selection_input"] is False


def test_arm_a_never_reads_the_f51_candidate_universe(monkeypatch, frozen_inputs):
    row = next(item for item in frozen_inputs.plan if item["arm_id"] == "A")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("arm A consulted candidate source")

    monkeypatch.setattr(M, "_read_revision_source", forbidden)
    result = M._materialize_row(row, frozen_inputs, M.REPOSITORY, {})
    assert result["context_pack"]["elements"] == []
    assert result["candidate_audit"] == []


def test_every_emitted_element_retains_complete_f56_audit_metadata(matrix):
    selected = 0
    for result in matrix["coordinates"]:
        arm = result["coordinate"]["arm_id"]
        assert result["context_pack"]["strategy"] == "first-fit"
        for element in result["context_pack"]["elements"]:
            selected += 1
            assert set(element) == M.ELEMENT_FIELDS
            assert M.SHA256.fullmatch(element["digest"])
            assert M.SHA256.fullmatch(element["source_locator_sha256"])
            assert element["reason"] == "caller_selected"
            assert element["truncated_tokens"] == 0
            assert element["removed"] is False
            assert element["injected_tokens"] == element["size_tokens"]
            if arm == "B":
                assert element["provenance"] == "caller"
                assert element["retriever_source"] == "manual"
                assert element["score"] is None
            else:
                assert arm == "C"
                assert element["provenance"] == "retriever"
                assert element["retriever_source"] == "external"
                assert isinstance(element["score"], float)
        serialized = json.dumps(result, sort_keys=True)
        assert '"locator"' not in serialized
        assert '"raw_content"' not in serialized
    assert selected > 0


def test_complete_serialized_matrix_contains_no_raw_task_source_or_locator(
    matrix,
    frozen_inputs,
):
    serialized = json.dumps(matrix, ensure_ascii=True, sort_keys=True).encode()
    persisted = serialized + M.MANIFEST_FILE.read_bytes() + M.PROTOCOL_FILE.read_bytes()
    assert b'"content"' not in serialized
    assert b'"locator"' not in serialized
    for case in frozen_inputs.cases.values():
        assert case["task"].encode() not in persisted
        for criterion in M._case_criteria(case, frozen_inputs.history):
            assert criterion["text"].encode() not in persisted
        for candidate in case["candidates"]:
            assert candidate["locator"].encode() not in persisted
            source = M._read_revision_source(
                M.REPOSITORY,
                case["revision"],
                candidate["locator"],
            )
            assert source not in persisted


def test_arm_b_preserves_declared_candidate_order_without_ranking(matrix):
    corpus = {
        case["id"]: case for case in M._load(M.F51_CORPUS)["cases"]
    }
    for result in matrix["coordinates"]:
        if result["coordinate"]["arm_id"] != "B":
            continue
        declared = [
            candidate["id"]
            for candidate in corpus[result["coordinate"]["case_id"]]["candidates"]
        ]
        audited = [candidate["candidate_id"] for candidate in result["candidate_audit"]]
        assert audited == declared
        selected = [
            candidate["candidate_id"]
            for candidate in result["candidate_audit"]
            if candidate["status"] == "selected"
        ]
        assert [
            element["candidate_id"] for element in result["context_pack"]["elements"]
        ] == selected


def test_oversize_naive_candidate_is_explicitly_skipped_without_truncation(
    frozen_inputs,
):
    capacity = frozen_inputs.budget.injectable_capacity_tokens
    small = M.Candidate("small", "file", "small.py", b"x" * 400)
    oversize = M.Candidate(
        "oversize",
        "file",
        "oversize.py",
        b"y" * ((capacity + 1) * M.TOKEN_BYTES),
    )
    selected, elements, audit = M._naive_selection((small, oversize), frozen_inputs.budget)
    assert selected == (small,)
    assert elements[0].truncated_tokens == 0
    assert audit[1]["status"] == "skipped"
    assert audit[1]["decision_reason"] == "exceeds_remaining_context_budget"
    assert audit[1]["truncated_tokens"] == 0


def test_f58_drives_arm_c_and_labels_are_absent_from_candidate_audit(matrix):
    arm_c_rows = [
        result for result in matrix["coordinates"] if result["coordinate"]["arm_id"] == "C"
    ]
    assert len(arm_c_rows) == 42
    assert all(
        result["selection_reason"] == "f58_lexical_then_unseen_structural_first_fit"
        and result["context_pack"]["elements"]
        for result in arm_c_rows
    )
    for result in arm_c_rows:
        assert result["candidate_audit"]
        for audit in result["candidate_audit"]:
            assert "label" not in audit
            assert audit["status"] in {"selected", "skipped", "not_ranked"}


def test_arm_c_calls_the_frozen_f58_composer(monkeypatch, frozen_inputs):
    row = next(item for item in frozen_inputs.plan if item["arm_id"] == "C")
    original = M.F58.compose_lexical_first
    observed = []

    def record(*args):
        observed.append(args)
        return original(*args)

    monkeypatch.setattr(M.F58, "compose_lexical_first", record)
    result = M._materialize_row(row, frozen_inputs, M.REPOSITORY, {})
    assert result["context_pack"]["elements"]
    assert len(observed) == 1
    assert observed[0][3] == frozen_inputs.budget.injectable_capacity_tokens


def test_failed_sources_report_every_affected_coordinate_and_reject_globally(
    monkeypatch,
):
    target_revision = "4a9ee8172bd1f1a04c02ea5d3eaacc1049c93e3a"
    original = M._read_revision_source

    def fail_target(repository, revision, locator):
        if revision == target_revision:
            raise M.ProtocolFreezeError("historical candidate source is unavailable")
        return original(repository, revision, locator)

    monkeypatch.setattr(M, "_read_revision_source", fail_target)
    result = M.materialize_all()
    assert result["status"] == "failed"
    assert result["materialized_coordinates"] == 114
    assert len(result["failures"]) == 12
    assert {failure["arm_id"] for failure in result["failures"]} == {"B", "C"}
    assert {failure["revision"] for failure in result["failures"]} == {target_revision}
    for failure in result["failures"]:
        assert set(failure) == M.FAILURE_FIELDS
        assert failure["role"] == "implementer"
        assert failure["model"]
        assert failure["reasoning_effort"] == "medium"
        assert failure["conditions_id"] == "f59-conditions-v2"
        assert failure["cause"] == "historical candidate source is unavailable"
    monkeypatch.setattr(M, "materialize_all", lambda repository=M.REPOSITORY: result)
    with pytest.raises(M.MatrixMaterializationError) as error:
        M.build_matrix()
    assert error.value.failures == tuple(result["failures"])


def test_preflight_reports_the_bound_complete_matrix(matrix, monkeypatch):
    monkeypatch.setattr(M, "build_matrix", lambda repository=M.REPOSITORY: matrix)
    assert M.run_preflight() == {
        "artifact": "foundry.context_broker.paired_pilot_preflight",
        "version": 2,
        "status": "pass",
        "planned_attempts": 126,
        "materialized_attempts": 126,
        "pairs": 42,
        "arms": {"A": 42, "B": 42, "C": 42},
        "plan_sha256": "7881cccf1e77fb9aa2234e74d5718559f57e1e6c75d7dad1aa7910f1f0c5132c",
        "host_calls": 0,
        "provider_calls": 0,
        "f44_calls": 0,
        "labels_used_as_selection_input": False,
    }


def test_f63_public_integration_api_consumes_exact_plan_coordinate(
    monkeypatch,
    frozen_inputs,
):
    monkeypatch.setattr(M, "_frozen_inputs", lambda: frozen_inputs)
    plan = M.build_plan()
    assert len(plan) == 126
    result = M.materialize_coordinate(plan[0])
    assert result["coordinate"] == plan[0]
    assert result["coordinate"]["arm_id"] == "A"
    assert result["context_pack"]["elements"] == []


@pytest.mark.parametrize("arm_id", ["A", "B", "C"])
def test_f63_runtime_api_exposes_only_selected_bytes_with_exact_sanitized_binding(
    arm_id,
    monkeypatch,
    frozen_inputs,
):
    monkeypatch.setattr(M, "_frozen_inputs", lambda: frozen_inputs)
    row = next(item for item in frozen_inputs.plan if item["arm_id"] == arm_id)
    runtime = M.materialize_runtime_coordinate(dict(row))
    sanitized = M.materialize_coordinate(dict(row))
    case = frozen_inputs.cases[row["case_id"]]

    assert runtime.coordinate == row
    assert M._plain(runtime.evidence) == sanitized
    assert runtime.snapshot_digest == sanitized["context_pack"]["snapshot_digest"]
    assert runtime.freeze_binding["plan_sha256"] == frozen_inputs.manifest["plan_sha256"]
    assert runtime.freeze_binding["source_digests"] == frozen_inputs.manifest[
        "source_digests"
    ]
    assert runtime.payload_bytes == tuple(
        payload.content for payload in runtime.selected_payloads
    )
    assert runtime.task == case["task"]
    criteria = M._case_criteria(case, frozen_inputs.history)
    assert runtime.criteria == tuple(item["text"] for item in criteria)
    assert runtime.criterion_ids == tuple(item["id"] for item in criteria)
    assert runtime.context_pack.snapshot_digest == runtime.snapshot_digest
    if arm_id == "A":
        assert runtime.context_pack.elements == ()
    else:
        assert runtime.context_pack.elements
    assert M.SHA256.fullmatch(runtime.composite_binding_sha256)

    if arm_id == "A":
        assert runtime.selected_payloads == ()
        assert runtime.payload_bytes == ()
    else:
        assert runtime.selected_payloads
        assert runtime.payload_bytes
        frozen_candidates = {
            candidate["id"]: candidate for candidate in case["candidates"]
        }
        for payload, element in zip(
            runtime.selected_payloads,
            sanitized["context_pack"]["elements"],
            strict=True,
        ):
            assert payload.candidate_id == element["candidate_id"]
            assert payload.candidate_kind == element["candidate_kind"]
            assert payload.identifier == element["candidate_id"]
            assert payload.kind == element["candidate_kind"]
            assert payload.locator == frozen_candidates[payload.candidate_id]["locator"]
            assert hashlib.sha256(payload.content).hexdigest() == element["digest"]
            assert payload.size_tokens == element["size_tokens"]
            assert payload.content == M._read_revision_source(
                M.REPOSITORY,
                row["revision"],
                frozen_candidates[payload.candidate_id]["locator"],
            )

    serialized = json.dumps(sanitized, ensure_ascii=True, sort_keys=True).encode()
    assert case["task"].encode() not in serialized
    assert b'"content"' not in serialized
    assert b'"locator"' not in serialized
    for candidate in case["candidates"]:
        assert candidate["locator"].encode() not in serialized
    for payload in runtime.selected_payloads:
        assert payload.content not in serialized
    with pytest.raises(TypeError):
        json.dumps(runtime)


def test_runtime_materialization_is_deeply_immutable_and_rejects_rebinding(
    monkeypatch,
    frozen_inputs,
):
    monkeypatch.setattr(M, "_frozen_inputs", lambda: frozen_inputs)
    row = next(item for item in frozen_inputs.plan if item["arm_id"] == "B")
    runtime = M.materialize_runtime_coordinate(dict(row))
    binding = runtime.composite_binding_sha256

    with pytest.raises(TypeError):
        runtime.coordinate["arm_id"] = "C"
    with pytest.raises(TypeError):
        runtime.freeze_binding["source_digests"]["f51_corpus"] = "0" * 64
    with pytest.raises(TypeError):
        runtime.arm_binding["selection_policy"] = "changed"
    with pytest.raises(TypeError):
        runtime.evidence["context_pack"]["elements"][0]["digest"] = "0" * 64
    with pytest.raises(AttributeError):
        runtime.evidence["candidate_audit"].append({})
    with pytest.raises(TypeError):
        runtime.criteria[0] = "changed"
    with pytest.raises(AttributeError):
        runtime.selected_payloads[0].locator = "changed.py"
    with pytest.raises(TypeError):
        runtime.payload_bytes[0][0] = 0
    with pytest.raises(AttributeError):
        runtime.context_pack.elements[0].digest = "0" * 64

    assert runtime.composite_binding_sha256 == binding
    assert runtime.coordinate["arm_id"] == "B"
    assert runtime.evidence["context_pack"]["snapshot_digest"] == (
        runtime.snapshot_digest
    )

    first = runtime.selected_payloads[0]
    rebound = M.RuntimePayload(
        candidate_id=first.candidate_id,
        candidate_kind=first.candidate_kind,
        locator="README.md",
        digest=first.digest,
        size_tokens=first.size_tokens,
        content=first.content,
    )
    with pytest.raises(M.ProtocolFreezeError, match="payload evidence binding"):
        M.RuntimeMaterialization(
            coordinate=M._plain(runtime.coordinate),
            freeze_binding=M._plain(runtime.freeze_binding),
            evidence=M._plain(runtime.evidence),
            snapshot_digest=runtime.snapshot_digest,
            selected_payloads=(rebound, *runtime.selected_payloads[1:]),
            task=runtime.task,
            criteria=runtime.criteria,
            criterion_ids=runtime.criterion_ids,
            arm_binding=M._plain(runtime.arm_binding),
            context_pack=runtime.context_pack,
            skipped_tokens=runtime.skipped_tokens,
            selection_reason=runtime.selection_reason,
            evaluation_supported=runtime.evaluation_supported,
        )


@pytest.mark.parametrize("arm_id", ["B", "C"])
def test_f63_compatible_runtime_pack_is_constructed_without_corpus_reread(
    arm_id,
    monkeypatch,
    frozen_inputs,
):
    @dataclass(frozen=True)
    class F63Candidate:
        identifier: str
        kind: str
        locator: str
        content: bytes

    @dataclass(frozen=True)
    class F63RuntimePack:
        coordinate: object
        task: str
        criteria: tuple[str, ...]
        selected: tuple[F63Candidate, ...]
        context_pack: object
        skipped_tokens: int
        selection_reason: str
        evaluation_supported: bool

    monkeypatch.setattr(M, "_frozen_inputs", lambda: frozen_inputs)
    row = next(item for item in frozen_inputs.plan if item["arm_id"] == arm_id)
    runtime = M.materialize_runtime_coordinate(dict(row))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("adapter reread corpus source")

    monkeypatch.setattr(M, "_read_revision_source", forbidden)
    pack = M.adapt_runtime_materialization(
        runtime,
        candidate_constructor=F63Candidate,
        runtime_pack_constructor=F63RuntimePack,
    )

    assert isinstance(pack, F63RuntimePack)
    assert pack.coordinate is runtime.coordinate
    assert pack.task == runtime.task
    assert pack.criteria == runtime.criteria
    assert pack.context_pack is runtime.context_pack
    assert pack.skipped_tokens == runtime.skipped_tokens
    assert pack.selection_reason == runtime.selection_reason
    assert pack.evaluation_supported is True
    assert tuple(
        (item.identifier, item.kind, item.locator, item.content)
        for item in pack.selected
    ) == tuple(
        (item.identifier, item.kind, item.locator, item.content)
        for item in runtime.selected
    )


def test_f63_runtime_api_rejects_non_frozen_coordinate_and_repository(
    monkeypatch,
    frozen_inputs,
    tmp_path,
):
    monkeypatch.setattr(M, "_frozen_inputs", lambda: frozen_inputs)
    row = dict(next(item for item in frozen_inputs.plan if item["arm_id"] == "B"))
    changed = dict(row)
    changed["arm_id"] = "C"

    with pytest.raises(M.ProtocolFreezeError, match="not in the frozen F59 plan"):
        M.materialize_runtime_coordinate(changed)
    with pytest.raises(M.ProtocolFreezeError, match="repository binding differs"):
        M.materialize_runtime_coordinate(row, repository=tmp_path)


def test_f63_runtime_api_rejects_unsafe_frozen_candidate_locator(
    monkeypatch,
    frozen_inputs,
):
    row = next(item for item in frozen_inputs.plan if item["arm_id"] == "B")
    changed_cases = dict(frozen_inputs.cases)
    changed_case = copy.deepcopy(changed_cases[row["case_id"]])
    changed_case["candidates"][0]["locator"] = "../outside"
    changed_cases[row["case_id"]] = changed_case
    unsafe_inputs = M.FrozenInputs(
        frozen_inputs.protocol,
        frozen_inputs.manifest,
        changed_cases,
        frozen_inputs.history,
        frozen_inputs.budget,
        frozen_inputs.plan,
    )
    monkeypatch.setattr(M, "_frozen_inputs", lambda: unsafe_inputs)

    with pytest.raises(M.MatrixMaterializationError) as error:
        M.materialize_runtime_coordinate(dict(row))
    assert error.value.failures[0]["cause"] == "candidate locator is unsafe"


def test_runtime_api_has_no_filesystem_write_surface(
    monkeypatch,
    frozen_inputs,
):
    monkeypatch.setattr(M, "_frozen_inputs", lambda: frozen_inputs)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("runtime materialization attempted a filesystem write")

    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    for arm_id in ("B", "C"):
        row = next(item for item in frozen_inputs.plan if item["arm_id"] == arm_id)
        assert M.materialize_runtime_coordinate(dict(row)).payload_bytes


@pytest.mark.parametrize("drifted", ["f56_contract", "f58_validator"])
def test_all_executable_dependency_bytes_are_validated_before_top_level_execution(
    drifted,
    monkeypatch,
    tmp_path,
):
    markers = {
        "f56_contract": tmp_path / "f56-executed",
        "f58_validator": tmp_path / "f58-executed",
    }
    paths = {
        name: tmp_path / f"{name}.py"
        for name in markers
    }
    for name, path in paths.items():
        path.write_text(
            "from pathlib import Path\n"
            f"Path({str(markers[name])!r}).write_text('executed')\n",
            encoding="utf-8",
        )
    digests = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in paths.items()
    }
    digests[drifted] = "0" * 64
    monkeypatch.setattr(M, "F56_CONTRACT", paths["f56_contract"])
    monkeypatch.setattr(M, "F58_VALIDATOR", paths["f58_validator"])
    monkeypatch.setattr(M, "CONTRACT", None)
    monkeypatch.setattr(M, "F58", None)
    monkeypatch.setattr(M, "_CODE_DEPENDENCY_BINDING", None)

    with pytest.raises(M.ProtocolFreezeError, match=rf"{drifted} digest differs"):
        M._load_code_dependencies(digests)
    assert all(not marker.exists() for marker in markers.values())
