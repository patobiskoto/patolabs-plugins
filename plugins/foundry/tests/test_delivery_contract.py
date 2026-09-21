import copy

import pytest

from foundry import delivery_contract as delivery


SHA = "a" * 40
CONTRACT = {
    "schema": delivery.CONTRACT_SCHEMA, "version": "v1",
    "sources": [{"id": "github", "adapter": "github_readonly", "adapter_version": "v1", "provenance": "github_api"}],
    "requirements": [{"id": "release", "source": "github", "required_facts": ["artifact"]}],
}


def proof(**overrides):
    value = {"schema": delivery.PROOF_SCHEMA, "source": "github", "project": "foundry",
             "sha": SHA, "state": "success", "provenance": "github_api", "facts": {"artifact": True}}
    value.update(overrides)
    return value


def receipt(observations, **kwargs):
    return delivery.delivery_receipt(CONTRACT, project="foundry", sha=SHA,
                                     observations=observations, observation_id="receipt1",
                                     observed_at="2026-09-22T10:00:00Z", **kwargs)


def test_contract_is_closed_declarative_and_digest_is_canonical():
    assert delivery.contract_digest(CONTRACT) == delivery.contract_digest(copy.deepcopy(CONTRACT))
    for forbidden in ("command", "hook", "credential", "deployment"):
        bad = copy.deepcopy(CONTRACT)
        bad[forbidden] = "anything"
        with pytest.raises(delivery.DeliveryContractError):
            delivery.validate_contract(bad)


@pytest.mark.parametrize(("observation", "verdict"), [
    (proof(), "verified"),
    (proof(sha="b" * 40), "wrong-sha"),
    ({}, "missing-proof"),
    (proof(state="pending"), "pending"),
    (proof(state="unavailable"), "inaccessible"),
    (proof(schema="future-proof.v2"), "unsupported-schema"),
])
def test_receipt_covers_all_required_terminal_and_nonterminal_states(observation, verdict):
    observations = observation if observation == {} else {"github": observation}
    assert receipt(observations)["verdict"] == verdict


def test_null_required_fact_fails_closed_without_inventing_a_negative_fact():
    result = receipt({"github": proof(facts={"artifact": None})})
    assert result["verdict"] == "unavailable"
    assert result["proofs"][0]["outcome"] == "unavailable"


def test_raw_provider_payloads_are_rejected_and_false_facts_do_not_verify():
    # Invalid proof content stays a missing proof; it is never serialized into the receipt.
    assert receipt({"github": proof(facts={"artifact": "provider output token=secret"})})["verdict"] == "missing-proof"
    assert receipt({"github": proof(facts={"artifact": False})})["verdict"] == "failed-proof"


def test_facades_are_identical_except_receipt_identifier_and_observation_time():
    shared = dict(project="foundry", sha=SHA, observations={"github": proof()})
    claude = delivery.claude_delivery_receipt(CONTRACT, observation_id="claude1", observed_at="one", **shared)
    codex = delivery.codex_delivery_receipt(CONTRACT, observation_id="codex1", observed_at="two", **shared)
    for item in (claude, codex):
        item.pop("receipt_id")
        item.pop("observed_at")
    assert claude == codex


def test_receipt_is_exact_sha_bound_and_does_not_expose_a_mutation_path():
    result = receipt({"github": proof()})
    assert result["sha"] == SHA
    assert result["contract"]["digest"] == delivery.contract_digest(CONTRACT)
    assert not {"command", "deployment", "rollback", "merge", "tracker"} & set(result)
