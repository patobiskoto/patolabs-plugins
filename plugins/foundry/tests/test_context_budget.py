import hashlib

import pytest

from foundry.context_budget import (
    MAX_CONTEXT_ELEMENTS, MAX_CONTEXT_WINDOW_TOKENS, CacheObservation, ContextBudget,
    ContextContractError, ContextElement, ContextPack,
    f44_snapshot_digest,
)
from foundry.lean_context import BASELINE_V1, build_packet, capture_context
from foundry import local_scout


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _pack(*, strategy="first-fit", cache=CacheObservation()):
    return ContextPack(
        "context-pack-v1", _digest("snapshot"),
        ContextBudget(100, 20, 10, 5, 15, 40, 10),
        (ContextElement("caller", _digest("one"), "caller_selected", "local_scout", 30, 4, 0.8),
         ContextElement("caller", _digest("two"), "caller_selected", "manual", 20),
         ContextElement("caller", _digest("three"), "removed_by_caller", "manual", 9, removed=True)),
        strategy, cache,
    )


def test_budget_models_every_partition_and_never_hides_overflow():
    budget = ContextBudget(100, 30, 20, 10, 20, 30, 10)
    assert budget.input_budget_tokens == -20
    assert budget.requested_input_tokens == 80
    assert budget.requested_tokens == 120
    assert budget.overflow_tokens == 20
    assert budget.injectable_capacity_tokens == -20
    assert budget.view()["overflow_tokens"] == 20


def test_pack_requires_element_provenance_digest_reason_source_size_and_truncation():
    with pytest.raises(ContextContractError):
        ContextElement("", _digest("x"), "caller_selected", "manual", 1)
    with pytest.raises(ContextContractError):
        ContextElement("caller", "not-a-digest", "caller_selected", "manual", 1)
    with pytest.raises(ContextContractError):
        ContextElement("caller", _digest("x"), "caller_selected", "manual", 1, 2)
    element = ContextElement("caller", _digest("x"), "caller_selected", "manual", 5, 2, None)
    assert element.injected_tokens == 3


def test_metrics_expose_requested_used_retrieved_injected_mix_truncations_overflows_and_nullable_cache():
    view = _pack().telemetry_projection()
    assert view["requested_budget_tokens"]["value"] == 100
    assert view["used_budget_tokens"]["value"] == 46
    assert view["retrieved_tokens"]["value"] == 59
    assert view["injected_tokens"]["value"] == 46
    assert view["source_mix"] == {"local_scout": 1, "manual": 2}
    assert view["truncations"] == {"count": 1, "tokens": 4}
    assert view["removals"] == {"count": 1, "tokens": 9}
    assert view["overflows"] == {"budget_tokens": 0, "injected_context_tokens": 46}
    assert view["cache"]["read_tokens"] == {"value": None, "provenance": "unavailable"}
    assert _pack(cache=CacheObservation(2, None)).telemetry_projection()["cache"]["read_tokens"]["value"] == 2


@pytest.mark.parametrize("strategy", ("first-fit", "round-robin", "future-f52"))
def test_strategies_are_accepted_neutrally_without_selection_or_precedence(strategy):
    assert _pack(strategy=strategy).inspection()["strategy"] == strategy


def test_projection_is_content_free_and_immutable():
    view = _pack().telemetry_projection()
    rendered = repr(view)
    assert "caller_selected" not in rendered
    assert _digest("one") not in rendered
    with pytest.raises(TypeError):
        view["source_mix"]["other"] = 1
    with pytest.raises(TypeError):
        view["budget"]["window_tokens"] = 0


@pytest.mark.parametrize("score", (float("nan"), float("inf"), float("-inf")))
def test_hostile_or_nonfinite_element_metadata_is_rejected(score):
    with pytest.raises(ContextContractError):
        ContextElement("caller", _digest("x"), "caller_selected", "manual", 1, score=score)
    with pytest.raises(ContextContractError):
        ContextElement("caller", _digest("x"), "caller_selected", "/private/key", 1)
    with pytest.raises(ContextContractError):
        ContextPack("secret-version", _digest("snapshot"), ContextBudget(1, 0, 0, 0, 0, 0, 0), (), "first-fit")
    with pytest.raises(ContextContractError):
        _pack(strategy="secret-path")


def test_injectable_capacity_reserves_every_partition_exactly_and_reports_excess():
    exact = ContextBudget(100, 20, 10, 5, 15, 40, 10)
    assert exact.injectable_capacity_tokens == 0
    exact_pack = ContextPack("context-pack-v1", _digest("exact"), exact, (), "first-fit")
    assert exact_pack.element_overflow_tokens == 0
    capacity = ContextBudget(100, 20, 10, 5, 15, 30, 10)
    assert capacity.injectable_capacity_tokens == 10
    over = ContextPack("context-pack-v1", _digest("over"), capacity,
                       (ContextElement("caller", _digest("item"), "caller_selected", "manual", 11),), "first-fit")
    assert over.element_overflow_tokens == 1
    assert over.telemetry_projection()["overflows"]["injected_context_tokens"] == 1


def test_reservation_overflow_does_not_become_injected_context_overflow():
    over_reserved = ContextBudget(100, 30, 20, 10, 20, 30, 10)
    empty = ContextPack("context-pack-v1", _digest("reserve-overflow"), over_reserved, (), "first-fit")
    assert empty.telemetry_projection()["overflows"] == {
        "budget_tokens": 20,
        "injected_context_tokens": 0,
    }
    one = ContextPack(
        "context-pack-v1", _digest("reserve-overflow-one"), over_reserved,
        (ContextElement("caller", _digest("injected"), "caller_selected", "manual", 7),),
        "first-fit",
    )
    assert one.element_overflow_tokens == 7


def test_pack_and_numeric_contract_bounds_are_explicit_and_testable():
    with pytest.raises(ContextContractError, match=str(MAX_CONTEXT_WINDOW_TOKENS)):
        ContextBudget(MAX_CONTEXT_WINDOW_TOKENS + 1, 0, 0, 0, 0, 0, 0)
    with pytest.raises(ContextContractError, match=str(MAX_CONTEXT_WINDOW_TOKENS)):
        ContextElement("caller", _digest("too-large"), "caller_selected", "manual", MAX_CONTEXT_WINDOW_TOKENS + 1)
    element = ContextElement("caller", _digest("repeated"), "caller_selected", "manual", 1)
    with pytest.raises(ContextContractError, match=str(MAX_CONTEXT_ELEMENTS)):
        ContextPack(
            "context-pack-v1", _digest("too-many"), ContextBudget(100, 0, 0, 0, 0, 0, 0),
            (element,) * (MAX_CONTEXT_ELEMENTS + 1), "first-fit",
        )
    with pytest.raises(ContextContractError, match="retrieved context"):
        ContextPack(
            "context-pack-v1", _digest("too-many-tokens"),
            ContextBudget(MAX_CONTEXT_WINDOW_TOKENS, 0, 0, 0, 0, 0, 0),
            (
                ContextElement("caller", _digest("max"), "caller_selected", "manual", MAX_CONTEXT_WINDOW_TOKENS),
                ContextElement("caller", _digest("one-more"), "caller_selected", "manual", 1),
            ), "first-fit",
        )


@pytest.mark.parametrize(
    "partition",
    ("reserved_output_tokens", "system_role_tokens", "tools_tokens", "task_tokens", "working_state_tokens", "margin_tokens"),
)
def test_each_budget_partition_reduces_injectable_capacity(partition):
    values = dict(window_tokens=100, reserved_output_tokens=0, system_role_tokens=0,
                  tools_tokens=0, task_tokens=0, working_state_tokens=0, margin_tokens=0)
    values[partition] = 7
    budget = ContextBudget(**values)
    assert budget.injectable_capacity_tokens == 93
    assert budget.overflow_tokens == 0


def test_pack_inspection_retains_version_and_snapshot_but_telemetry_does_not():
    pack = _pack()
    inspection = pack.inspection()
    telemetry = pack.telemetry_projection()
    assert inspection["version"] == "context-pack-v1"
    assert inspection["snapshot_digest"] == _digest("snapshot")
    assert "version" not in telemetry
    assert "snapshot_digest" not in telemetry
    assert "strategy" not in telemetry
    with pytest.raises(TypeError):
        inspection["elements"] = ()


def test_f44_compatibility_uses_only_packet_metadata_not_fragments():
    capture = capture_context(diff=local_scout.capture_diff("diff --git a/a b/a\n+line\n"))
    packet = build_packet(BASELINE_V1, capture)
    digest = f44_snapshot_digest(packet.policy, packet.evidence_ids)
    assert digest == f44_snapshot_digest(packet.policy, packet.evidence_ids)
    assert digest != f44_snapshot_digest("lean-v1", packet.evidence_ids)
    assert f44_snapshot_digest("a", ("b", "c")) != f44_snapshot_digest("a\0b", ("c",))
    with pytest.raises(ContextContractError):
        f44_snapshot_digest(packet.policy, tuple(packet.evidence_ids) + (1,))
