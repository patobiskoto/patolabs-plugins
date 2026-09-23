from __future__ import annotations

from dataclasses import replace

import pytest

from offline_provider_handoff import (
    CrashPoint,
    HandoffCoordinates,
    HandoffRejected,
    InjectedHandoffCrash,
    OfflineProviderHandoff,
)


def coordinates(**overrides):
    values = {
        "issue_id": "PAT-23",
        "operation_id": "pat23-import-operation-0001",
        "diff_sha256": "a" * 64,
        "ac_sha256": "b" * 64,
        "generation": 1,
        "authority_id": "offline-pat23-authority-snapshot-0001",
    }
    values.update(overrides)
    return HandoffCoordinates(**values)


def handoff(tmp_path, name="case"):
    return OfflineProviderHandoff(
        provider_state_path=tmp_path / name / "provider.json",
        local_ledger_path=tmp_path / name / "ledger.json",
    )


def test_separates_durable_provider_state_from_local_non_authoritative_receipt(tmp_path):
    exact = coordinates()
    double = handoff(tmp_path)
    capability = double.issue_fixture_capability(exact, now=10, ttl=10)

    assert capability.authoritative is False
    assert double.provider.state_path != double.ledger.state_path
    assert double.provider.snapshot()["fixture_authority"] is False
    assert double.ledger.snapshot()["operations"] == {}

    receipt = double.execute(exact, capability=capability, now=11)
    reloaded = handoff(tmp_path)

    assert receipt["identity_sha256"] == exact.identity_sha256
    assert receipt["coordinates"] == exact.frozen()
    assert "authority" not in receipt
    assert "human_verdict" not in receipt
    provider = reloaded.provider.snapshot()
    ledger = reloaded.ledger.snapshot()
    assert provider["effect_count"] == 1
    assert "receipt_count" not in provider
    assert ledger["receipt_count"] == 1
    assert "effects" not in ledger
    assert reloaded.execute(exact, capability=capability, now=1_000) == receipt
    assert reloaded.provider.snapshot()["effect_count"] == 1
    assert reloaded.ledger.snapshot()["receipt_count"] == 1


@pytest.mark.parametrize(
    ("case", "capability_factory", "now", "message"),
    (
        ("absent", lambda _double, _exact: None, 11, "capability required"),
        (
            "expired",
            lambda double, exact: double.issue_fixture_capability(
                exact, now=10, ttl=1
            ),
            11,
            "capability expired",
        ),
        (
            "wrong-context",
            lambda double, exact: double.issue_fixture_capability(
                replace(exact, issue_id="PAT-999"), now=10, ttl=10
            ),
            11,
            "context drift",
        ),
    ),
)
def test_first_effect_refuses_missing_expired_or_context_drifted_capacity(
    tmp_path, case, capability_factory, now, message
):
    exact = coordinates()
    double = handoff(tmp_path, case)
    capability = capability_factory(double, exact)

    with pytest.raises(HandoffRejected, match=message):
        double.execute(exact, capability=capability, now=now)

    assert double.provider.snapshot()["effect_count"] == 0
    ledger = double.ledger.snapshot()
    assert ledger["receipt_count"] == 0
    assert ledger["operations"][exact.operation_id]["status"] == "intent"


def test_consumed_capacity_cannot_authorize_a_first_effect(tmp_path):
    exact = coordinates()
    double = handoff(tmp_path, "consumed")
    capability = double.issue_fixture_capability(exact, now=10, ttl=10)
    double.consume_fixture_capability_without_effect(capability)

    with pytest.raises(HandoffRejected, match="already consumed"):
        double.execute(exact, capability=capability, now=11)

    assert double.provider.snapshot()["effect_count"] == 0
    assert double.ledger.snapshot()["receipt_count"] == 0


def test_intent_does_not_renew_expired_capacity_after_crash_before_effect(tmp_path):
    exact = coordinates()
    double = handoff(tmp_path, "before")
    capability = double.issue_fixture_capability(exact, now=10, ttl=5)

    with pytest.raises(InjectedHandoffCrash, match="before provider effect"):
        double.execute(
            exact,
            capability=capability,
            now=11,
            crash_at=CrashPoint.BEFORE_EFFECT,
        )

    assert double.provider.snapshot()["effect_count"] == 0
    assert double.ledger.snapshot()["operations"][exact.operation_id]["status"] == "intent"
    assert double.ledger.snapshot()["receipt_count"] == 0

    reloaded = handoff(tmp_path, "before")
    with pytest.raises(HandoffRejected, match="capability expired"):
        reloaded.execute(exact, capability=capability, now=15)

    assert reloaded.provider.snapshot()["effect_count"] == 0
    assert reloaded.ledger.snapshot()["receipt_count"] == 0


def test_replay_after_post_effect_crash_reconstructs_one_receipt_after_expiry(tmp_path):
    exact = coordinates()
    double = handoff(tmp_path, "after")
    capability = double.issue_fixture_capability(exact, now=10, ttl=5)

    with pytest.raises(InjectedHandoffCrash, match="before local receipt"):
        double.execute(
            exact,
            capability=capability,
            now=11,
            crash_at=CrashPoint.AFTER_EFFECT_BEFORE_RECEIPT,
        )

    provider = double.provider.snapshot()
    ledger = double.ledger.snapshot()
    assert provider["effect_count"] == 1
    assert provider["capabilities"][capability.capability_id]["status"] == "consumed"
    assert ledger["operations"][exact.operation_id]["status"] == "intent"
    assert ledger["receipt_count"] == 0

    reloaded = handoff(tmp_path, "after")
    receipt = reloaded.execute(exact, capability=capability, now=1_000)

    assert receipt["provider_effect_replayed"] is True
    assert reloaded.provider.snapshot()["effect_count"] == 1
    assert reloaded.ledger.snapshot()["receipt_count"] == 1
    assert reloaded.ledger.snapshot()["operations"][exact.operation_id] == {
        "capability_id": capability.capability_id,
        "coordinates": exact.frozen(),
        "identity_sha256": exact.identity_sha256,
        "receipt": receipt,
        "status": "completed",
    }
    assert reloaded.execute(exact, capability=capability, now=2_000) == receipt
    assert reloaded.provider.snapshot()["effect_count"] == 1
    assert reloaded.ledger.snapshot()["receipt_count"] == 1


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("issue_id", "PAT-999"),
        ("operation_id", "pat23-import-operation-9999"),
        ("diff_sha256", "c" * 64),
        ("ac_sha256", "d" * 64),
        ("generation", 2),
        ("authority_id", "offline-pat23-authority-snapshot-9999"),
    ),
)
def test_durable_effect_replay_fails_closed_on_any_identity_drift(
    tmp_path, field, value
):
    exact = coordinates()
    name = f"drift-{field}"
    double = handoff(tmp_path, name)
    capability = double.issue_fixture_capability(exact, now=10, ttl=5)
    with pytest.raises(InjectedHandoffCrash):
        double.execute(
            exact,
            capability=capability,
            now=11,
            crash_at=CrashPoint.AFTER_EFFECT_BEFORE_RECEIPT,
        )

    reloaded = handoff(tmp_path, name)
    with pytest.raises(HandoffRejected, match="conflict|context drift|consumed"):
        reloaded.execute(replace(exact, **{field: value}), capability=capability, now=20)

    assert reloaded.provider.snapshot()["effect_count"] == 1
    assert reloaded.ledger.snapshot()["receipt_count"] == 0


def test_replay_requires_the_same_fixture_capability_identity(tmp_path):
    exact = coordinates()
    double = handoff(tmp_path, "wrong-capability")
    capability = double.issue_fixture_capability(exact, now=10, ttl=5)
    other_capability = double.issue_fixture_capability(exact, now=10, ttl=50)
    with pytest.raises(InjectedHandoffCrash):
        double.execute(
            exact,
            capability=capability,
            now=11,
            crash_at=CrashPoint.AFTER_EFFECT_BEFORE_RECEIPT,
        )

    reloaded = handoff(tmp_path, "wrong-capability")
    with pytest.raises(HandoffRejected, match="local operation identity conflict"):
        reloaded.execute(exact, capability=other_capability, now=12)

    assert reloaded.provider.snapshot()["effect_count"] == 1
    assert reloaded.ledger.snapshot()["receipt_count"] == 0
