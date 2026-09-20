from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import importlib.util
import json
from pathlib import Path
import pickle
import sys
from threading import Lock
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks" / "foundry-63" / "materialize-context-packs-v2.py"


def _module():
    spec = importlib.util.spec_from_file_location("foundry63_materializer_v2", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


M = _module()


@pytest.fixture(scope="module")
def preflight():
    return M.preflight()


@pytest.fixture(scope="module")
def official_materializations():
    plan = M.build_plan()
    by_arm = {row["arm_id"]: row for row in plan[:3]}
    original_inputs = M.F59._frozen_inputs
    frozen_inputs = original_inputs()
    try:
        M.F59._frozen_inputs = lambda: frozen_inputs
        return {
            arm: (M.materialize_coordinate(row), M.materialize_evidence(row))
            for arm, row in by_arm.items()
        }
    finally:
        M.F59._frozen_inputs = original_inputs


def test_all_126_frozen_coordinates_are_adapted_with_distinct_policies(preflight):
    assert preflight == {
        "artifact": "foundry.context_broker.f63_materialization_preflight",
        "version": 2,
        "status": "pass",
        "planned_coordinates": 126,
        "materialized_coordinates": 126,
        "adapted_runtime_packs": 126,
        "pairs": 42,
        "arms": {"A": 42, "B": 42, "C": 42},
        "snapshot_digests": preflight["snapshot_digests"],
        "host_calls": 0,
        "provider_calls": 0,
        "f44_calls": 0,
        "call_count_scope": "invocations_performed_by_f63_materializer",
        "labels_used_as_selection_input": False,
        "persistent_raw_content": False,
        "runtime_integrity_scope": {
            "purpose": (
                "official_f64_flow_reproducibility_and_accidental_drift_detection"
            ),
            "hostile_same_python_process": "out_of_scope",
            "security_authority": False,
        },
    }
    assert len(preflight["snapshot_digests"]) == 126
    assert all(M.F59.SHA256.fullmatch(item) for item in preflight["snapshot_digests"])


def test_preflight_aggregates_every_coordinate_failure(monkeypatch):
    frozen_inputs = M.F59._frozen_inputs()
    attempted = []
    monkeypatch.setattr(M.F59, "_frozen_inputs", lambda: frozen_inputs)
    monkeypatch.setattr(M.F59, "_validate_repository", lambda _repository: None)

    def fail(row, *_args, **_kwargs):
        attempted.append(row["sequence"])
        raise M.F59.ProtocolFreezeError(f"synthetic cause {row['sequence']}")

    monkeypatch.setattr(M.F59, "_materialize_row_parts", fail)
    with pytest.raises(M.ContextPackMaterializationError) as raised:
        M.preflight()

    assert attempted == list(range(126))
    assert len(raised.value.failures) == 126
    for sequence, failure in enumerate(raised.value.failures):
        assert failure["coordinate"]["sequence"] == sequence
        assert failure["cause"] == f"synthetic cause {sequence}"


def test_runtime_pack_and_sanitized_evidence_share_the_frozen_binding(
    official_materializations,
):
    packs = {arm: values[0] for arm, values in official_materializations.items()}
    evidence = {arm: values[1] for arm, values in official_materializations.items()}

    assert [packs[arm].selection_reason for arm in ("A", "B", "C")] == [
        "no_preinjected_retrieved_candidate_context",
        "f51_declared_order_first_fit_without_ranking",
        "f58_lexical_then_unseen_structural_first_fit",
    ]
    assert packs["A"].selected == ()
    assert packs["B"].selected and packs["C"].selected
    assert packs["A"].evaluation_supported is False
    assert packs["B"].evaluation_supported is True
    assert packs["C"].evaluation_supported is True
    for arm in ("A", "B", "C"):
        assert (
            packs[arm].context_pack.snapshot_digest
            == evidence[arm]["context_pack"]["snapshot_digest"]
        )
        assert evidence[arm]["labels_used_as_selection_input"] is False
        assert (
            evidence[arm]["post_selection_evaluation"]["supported"]
            is packs[arm].evaluation_supported
        )
        assert M.verify_runtime_integrity(packs[arm]) == packs[arm].f64_binding_sha256
        assert M.SHA256.fullmatch(packs[arm].f64_binding_sha256)
        assert (
            packs[arm].binding.plan_sha256
            == M.F59._load(
                M.F59.MANIFEST_FILE,
            )["plan_sha256"]
        )
        assert packs[arm].binding.coordinate_sha256 == M._canonical_sha256(
            packs[arm].coordinate,
        )


def test_evidence_is_content_free_and_runtime_objects_are_not_serializable(
    official_materializations,
):
    pack, evidence = official_materializations["B"]
    serialized = json.dumps(evidence, ensure_ascii=True, sort_keys=True).encode()

    assert b'"locator"' not in serialized
    assert b'"content"' not in serialized
    assert all(candidate.content not in serialized for candidate in pack.selected)
    runtime_repr = repr(pack)
    assert pack.task not in runtime_repr
    assert all(criterion not in runtime_repr for criterion in pack.criteria)
    assert all(candidate.locator not in repr(candidate) for candidate in pack.selected)
    assert all(
        repr(candidate.content) not in repr(candidate) for candidate in pack.selected
    )
    with pytest.raises(TypeError):
        json.dumps(pack)
    with pytest.raises(TypeError, match="ephemeral"):
        pickle.dumps(pack)
    for candidate in pack.selected:
        with pytest.raises(TypeError, match="ephemeral"):
            pickle.dumps(candidate)


def test_runtime_values_are_deeply_immutable_for_normal_callers(
    official_materializations,
):
    pack, _evidence = official_materializations["B"]
    candidate = pack.selected[0]

    with pytest.raises(FrozenInstanceError):
        pack.selection_reason = "changed"
    with pytest.raises(TypeError):
        pack.coordinate["arm_id"] = "C"
    with pytest.raises(FrozenInstanceError):
        pack.context_pack.strategy = "changed"
    with pytest.raises(FrozenInstanceError):
        candidate.identifier = "changed"
    with pytest.raises(TypeError):
        candidate.content[0] = 0


def test_normal_rebinding_fails_the_reproducibility_contract(
    official_materializations,
):
    pack, _evidence = official_materializations["B"]

    with pytest.raises(
        M.ContextPackMaterializationError,
        match="differs from its F64 flow binding",
    ):
        replace(pack, selection_reason=f"{pack.selection_reason}_changed")

    changed_candidate = replace(
        pack.selected[0],
        content=pack.selected[0].content + b"changed",
    )
    with pytest.raises(
        M.ContextPackMaterializationError,
        match="differs from its F64 flow binding",
    ):
        replace(pack, selected=(changed_candidate, *pack.selected[1:]))

    changed_binding = replace(pack.binding, plan_sha256="0" * 64)
    with pytest.raises(
        M.ContextPackMaterializationError,
        match="differs from its F64 flow binding",
    ):
        replace(pack, binding=changed_binding)


def test_threat_model_excludes_hostile_code_in_the_same_python_process():
    """Frozen dataclasses are integrity ergonomics, not a security boundary."""
    assert dict(M.RUNTIME_INTEGRITY_SCOPE) == {
        "purpose": "official_f64_flow_reproducibility_and_accidental_drift_detection",
        "hostile_same_python_process": "out_of_scope",
        "security_authority": False,
    }
    assert "verify_f64_bound" not in vars(M)
    assert not any(
        term in SOURCE.read_text(encoding="utf-8")
        for term in ("weakref", "receipt registry", "must be minted", "capability")
    )

    synthetic = M.Candidate(
        identifier="synthetic",
        kind="file",
        locator="synthetic.py",
        content=b"synthetic",
    )
    object.__setattr__(synthetic, "identifier", "same-process-override")
    assert synthetic.identifier == "same-process-override"


def test_official_f64_callback_path_has_reproducible_integrity(
    official_materializations,
):
    pack, _evidence = official_materializations["C"]

    assert M.verify_runtime_integrity(pack) == pack.f64_binding_sha256
    assert pack.binding.snapshot_digest == pack.context_pack.snapshot_digest


def test_non_frozen_coordinate_fails_closed():
    row = dict(next(row for row in M.build_plan() if row["arm_id"] == "B"))
    row["arm_id"] = "C"
    with pytest.raises(
        M.ContextPackMaterializationError, match="not in the frozen F59 plan"
    ):
        M.materialize_coordinate(row)


def test_matrix_failure_preserves_structured_coordinate_and_cause(monkeypatch):
    row = M.build_plan()[1]

    def fail(*_args, **_kwargs):
        raise M.F59.ProtocolFreezeError("source digest differs")

    monkeypatch.setattr(M.F59, "_materialize_row_parts", fail)
    with pytest.raises(M.ContextPackMaterializationError) as raised:
        M.materialize_coordinate(row)

    assert len(raised.value.failures) == 1
    translated = raised.value.failures[0]
    assert translated["coordinate"] == {
        name: row[name] for name in M.F59.FAILURE_FIELDS if name != "cause"
    }
    assert translated["cause"] == "source digest differs"
    assert "coordinate=" in str(raised.value)
    assert "cause='source digest differs'" in str(raised.value)


def test_normal_adapter_mismatch_has_structured_coordinate_and_cause(monkeypatch):
    row = M.build_plan()[1]
    frozen_inputs = M.F59._frozen_inputs()
    original_adapter = M.F59.adapt_runtime_materialization
    monkeypatch.setattr(M.F59, "_frozen_inputs", lambda: frozen_inputs)

    def mismatch(runtime, *, candidate_constructor, runtime_pack_constructor):
        def changed_constructor(**values):
            values["selection_reason"] = f"{values['selection_reason']}_changed"
            return runtime_pack_constructor(**values)

        return original_adapter(
            runtime,
            candidate_constructor=candidate_constructor,
            runtime_pack_constructor=changed_constructor,
        )

    monkeypatch.setattr(M.F59, "adapt_runtime_materialization", mismatch)
    with pytest.raises(M.ContextPackMaterializationError) as raised:
        M.materialize_coordinate(row)

    assert len(raised.value.failures) == 1
    assert raised.value.failures[0]["coordinate"]["sequence"] == row["sequence"]
    assert raised.value.failures[0]["cause"] == "F64 runtime adapter differs"


def test_concurrent_preflights_do_not_replace_f64_validation_state(monkeypatch):
    frozen_inputs = M.F59._frozen_inputs()
    active_calls = 0
    maximum_active_calls = 0
    attempted = []
    observation_lock = Lock()
    monkeypatch.setattr(M.F59, "_frozen_inputs", lambda: frozen_inputs)
    monkeypatch.setattr(M.F59, "_validate_repository", lambda _repository: None)

    def fail(row, *_args, **_kwargs):
        nonlocal active_calls, maximum_active_calls
        with observation_lock:
            active_calls += 1
            maximum_active_calls = max(maximum_active_calls, active_calls)
            attempted.append(row["sequence"])
        try:
            time.sleep(0.0001)
            raise M.F59.ProtocolFreezeError("synthetic concurrency failure")
        finally:
            with observation_lock:
                active_calls -= 1

    monkeypatch.setattr(M.F59, "_materialize_row_parts", fail)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(M.preflight) for _index in range(2)]
        for future in futures:
            with pytest.raises(M.ContextPackMaterializationError):
                future.result()

    assert attempted == list(range(126)) * 2
    assert maximum_active_calls == 1


def test_preflight_has_no_host_provider_or_filesystem_write_surface(monkeypatch):
    frozen_inputs = M.F59._frozen_inputs()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("unexpected filesystem write")

    def fail(*_args, **_kwargs):
        raise M.F59.ProtocolFreezeError("synthetic offline failure")

    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(M.F59, "_frozen_inputs", lambda: frozen_inputs)
    monkeypatch.setattr(M.F59, "_validate_repository", lambda _repository: None)
    monkeypatch.setattr(M.F59, "_materialize_row_parts", fail)
    with pytest.raises(M.ContextPackMaterializationError) as raised:
        M.preflight()

    assert len(raised.value.failures) == 126
