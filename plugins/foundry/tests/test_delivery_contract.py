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


class StaticReadOnlyAdapter:
    source_id = "github"
    adapter = "github_readonly"
    adapter_version = "v1"
    provenance = "github_api"

    def __init__(self, value):
        self.value = value
        self.calls = []

    def read_proof(self, *, project, sha):
        self.calls.append((project, sha))
        return self.value


class UnavailableReadOnlyAdapter(StaticReadOnlyAdapter):
    def read_proof(self, *, project, sha):
        self.calls.append((project, sha))
        raise delivery.ProofSourceUnavailable("provider secret detail must not persist")


class BrokenReadOnlyAdapter(StaticReadOnlyAdapter):
    def read_proof(self, *, project, sha):
        raise RuntimeError("adapter programming error")


def receipt(observations, **kwargs):
    options = {"observation_id": "receipt1", "observed_at": "2026-09-22T10:00:00Z"}
    options.update(kwargs)
    return delivery._delivery_receipt_from_observations(
        CONTRACT, project="foundry", sha=SHA, observations=observations, **options,
    )


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


def test_one_source_readonly_adapter_is_contract_bound_and_provenance_is_checked():
    adapter = StaticReadOnlyAdapter(proof())
    observed = delivery.read_delivery_proof(
        CONTRACT, project="foundry", sha=SHA, source="github", adapter=adapter,
    )
    assert observed == proof()
    assert adapter.calls == [("foundry", SHA)]
    result = delivery.delivery_receipt_from_adapter(
        CONTRACT, project="foundry", sha=SHA, source="github", adapter=adapter,
        observation_id="adapter1", observed_at="2026-09-22T10:00:00Z",
    )
    assert result["verdict"] == "verified"
    assert adapter.calls == [("foundry", SHA), ("foundry", SHA)]
    assert receipt({"github": proof(provenance="other_provider")})["verdict"] == "provenance-mismatch"

    adapter.provenance = "other_provider"
    with pytest.raises(delivery.DeliveryContractError, match="does not match"):
        delivery.read_delivery_proof(CONTRACT, project="foundry", sha=SHA, source="github", adapter=adapter)


def test_declared_adapter_unavailability_is_inaccessible_without_raw_error_text():
    adapter = UnavailableReadOnlyAdapter(proof())
    result = delivery.delivery_receipt_from_adapter(
        CONTRACT, project="foundry", sha=SHA, source="github", adapter=adapter,
        observation_id="adapter1", observed_at="2026-09-22T10:00:00Z",
    )
    assert result["verdict"] == "inaccessible"
    assert result["proofs"][0]["outcome"] == "inaccessible"
    assert "provider secret detail" not in delivery._canonical(result)
    assert adapter.calls == [("foundry", SHA)]


def test_adapter_programming_errors_are_not_masked_as_unavailability():
    with pytest.raises(RuntimeError, match="programming error"):
        delivery.delivery_receipt_from_adapter(
            CONTRACT, project="foundry", sha=SHA, source="github",
            adapter=BrokenReadOnlyAdapter(proof()), observation_id="adapter1",
            observed_at="2026-09-22T10:00:00Z",
        )


@pytest.mark.parametrize("observed_at", ["one", "2026-09-22T10:00:00+02:00", "2026-99-22T10:00:00Z"])
def test_observed_at_is_a_canonical_utc_timestamp(observed_at):
    with pytest.raises(delivery.DeliveryContractError, match="canonical UTC"):
        receipt({"github": proof()}, observed_at=observed_at)


def test_cross_project_is_not_misreported_as_wrong_sha():
    result = receipt({"github": proof(project="another")})
    assert result["verdict"] == "cross-project"
    assert result["proofs"][0]["outcome"] == "cross-project"


@pytest.mark.parametrize("facts", [{"not": "a-list"}, ["artifact", {"bad": "fact"}]])
def test_malformed_required_facts_raises_delivery_contract_error(facts):
    contract = copy.deepcopy(CONTRACT)
    contract["requirements"][0]["required_facts"] = facts
    with pytest.raises(delivery.DeliveryContractError):
        delivery.validate_contract(contract)


def test_bounded_local_receipt_journal_is_append_only_unique_and_detached(tmp_path):
    journal = delivery.DeliveryReceiptJournal(tmp_path / "delivery-receipts.jsonl", max_receipts=2)
    first = receipt({"github": proof()})
    saved = journal.append(first)
    first["verdict"] = "injected"
    assert saved["verdict"] == "verified"
    assert journal.receipts() == (saved,)
    with pytest.raises(delivery.DeliveryContractError, match="already exists"):
        journal.append(saved)

    second = receipt({"github": proof()}, observation_id="receipt2")
    journal.append(second)
    with pytest.raises(delivery.DeliveryContractError, match="full"):
        journal.append(receipt({"github": proof()}, observation_id="receipt3"))


def test_journal_refuses_provider_payloads_in_receipt_rows(tmp_path):
    unsafe = receipt({"github": proof()})
    unsafe["proofs"][0]["raw_output"] = "token=secret"
    with pytest.raises(delivery.DeliveryContractError, match="unsupported fields"):
        delivery.DeliveryReceiptJournal(tmp_path / "delivery-receipts.jsonl").append(unsafe)


def test_journal_requires_non_empty_unique_proof_rows(tmp_path):
    journal = delivery.DeliveryReceiptJournal(tmp_path / "delivery-receipts.jsonl")
    empty = receipt({"github": proof()})
    empty["proofs"] = []
    with pytest.raises(delivery.DeliveryContractError, match="non-empty"):
        journal.append(empty)

    duplicate = receipt({"github": proof()})
    duplicate["proofs"].append(copy.deepcopy(duplicate["proofs"][0]))
    with pytest.raises(delivery.DeliveryContractError, match="must be unique"):
        journal.append(duplicate)


def test_journal_rejects_unknown_proof_outcome(tmp_path):
    forged = receipt({"github": proof()})
    forged["proofs"][0]["outcome"] = "provider-says-yes"
    with pytest.raises(delivery.DeliveryContractError, match="outcome is unsupported"):
        delivery.DeliveryReceiptJournal(tmp_path / "delivery-receipts.jsonl").append(forged)


@pytest.mark.parametrize(("field", "value", "message"), [
    ("verdict", ["verified"], "receipt verdict is unsupported"),
    ("outcome", ["success"], "receipt proof outcome is unsupported"),
])
def test_journal_rejects_malformed_closed_vocabulary_fields_deterministically(
    tmp_path, field, value, message,
):
    forged = receipt({"github": proof()})
    if field == "verdict":
        forged[field] = value
    else:
        forged["proofs"][0][field] = value
    with pytest.raises(delivery.DeliveryContractError, match=message):
        delivery.DeliveryReceiptJournal(tmp_path / "delivery-receipts.jsonl").append(forged)


@pytest.mark.parametrize(("observation", "forged_verdict"), [
    (proof(), "failed-proof"),
    (proof(facts={"artifact": False}), "verified"),
])
def test_journal_recomputes_and_rejects_forged_positive_or_negative_verdicts(
    tmp_path, observation, forged_verdict,
):
    forged = receipt({"github": observation})
    forged["verdict"] = forged_verdict
    with pytest.raises(delivery.DeliveryContractError, match="does not match proof outcomes"):
        delivery.DeliveryReceiptJournal(tmp_path / "delivery-receipts.jsonl").append(forged)


def test_journal_revalidates_verdict_coherence_when_reading(tmp_path):
    forged = receipt({"github": proof()})
    forged["verdict"] = "failed-proof"
    path = tmp_path / "delivery-receipts.jsonl"
    path.write_text(delivery._canonical(forged) + "\n", encoding="ascii")
    with pytest.raises(delivery.DeliveryContractError, match="does not match proof outcomes"):
        delivery.DeliveryReceiptJournal(path).receipts()

    with pytest.raises(delivery.DeliveryContractError, match="does not match proof outcomes"):
        delivery.DeliveryReceiptJournal(path).append(
            receipt({"github": proof()}, observation_id="receipt2"),
        )


@pytest.mark.parametrize("observations", [
    {"github": proof()},
    {"github": proof(facts={"artifact": False})},
    {"github": proof(state="pending")},
    {},
])
def test_journal_accepts_receipts_coherent_with_their_proof_outcomes(tmp_path, observations):
    expected = receipt(observations)
    saved = delivery.DeliveryReceiptJournal(tmp_path / "delivery-receipts.jsonl").append(expected)
    assert saved == expected


def test_facades_are_identical_except_receipt_identifier_and_observation_time():
    shared = dict(project="foundry", sha=SHA, source="github")
    claude = delivery.claude_delivery_receipt(
        CONTRACT, adapter=StaticReadOnlyAdapter(proof()), observation_id="claude1",
        observed_at="2026-09-22T10:00:00Z", **shared,
    )
    codex = delivery.codex_delivery_receipt(
        CONTRACT, adapter=StaticReadOnlyAdapter(proof()), observation_id="codex1",
        observed_at="2026-09-22T10:01:00Z", **shared,
    )
    for item in (claude, codex):
        item.pop("receipt_id")
        item.pop("observed_at")
    assert claude == codex


@pytest.mark.parametrize("facade", [delivery.claude_delivery_receipt, delivery.codex_delivery_receipt])
def test_supported_facades_do_not_accept_caller_supplied_observations(facade):
    with pytest.raises(TypeError, match="observations"):
        facade(
            CONTRACT, project="foundry", sha=SHA, source="github",
            observations={"github": proof()}, observation_id="forged1",
            observed_at="2026-09-22T10:00:00Z",
        )


def test_pure_observation_evaluator_is_not_a_public_surface():
    assert not hasattr(delivery, "delivery_receipt")


def test_receipt_is_exact_sha_bound_and_does_not_expose_a_mutation_path():
    result = receipt({"github": proof()})
    assert result["sha"] == SHA
    assert result["contract"]["digest"] == delivery.contract_digest(CONTRACT)
    assert not {"command", "deployment", "rollback", "merge", "tracker"} & set(result)
