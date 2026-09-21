import copy
import inspect
import json
import urllib.error

import pytest

from foundry import delivery_contract as delivery


SHA = "a" * 40
OTHER_SHA = "b" * 40
FIXED_TIME = "2026-09-22T10:00:00Z"
CONTRACT = delivery.github_check_runs_pilot_contract()


@pytest.fixture(autouse=True)
def fixed_private_clock(monkeypatch):
    monkeypatch.setattr(delivery, "_utc_now", lambda: FIXED_TIME)


def check_run(*, sha=SHA, status="completed", conclusion="success"):
    return {"head_sha": sha, "status": status, "conclusion": conclusion}


def check_runs_payload(*runs, total_count=None):
    return {
        "total_count": len(runs) if total_count is None else total_count,
        "check_runs": list(runs),
    }


class StubTransport:
    def __init__(self, value=None, *, error=None):
        self.value = value
        self.error = error
        self.calls = []

    def get_check_runs(self, *, project, sha):
        self.calls.append((project, sha))
        if self.error is not None:
            raise self.error
        return self.value


def github_adapter(monkeypatch, value=None, *, error=None):
    transport = StubTransport(value, error=error)
    monkeypatch.setattr(delivery, "_get_public_github_check_runs", transport.get_check_runs)
    return delivery.GitHubCheckRunsAdapter(), transport


def generated_receipt(monkeypatch, value, *, observation_id="receipt1", error=None, sha=SHA):
    adapter, transport = github_adapter(monkeypatch, value, error=error)
    result = delivery.delivery_receipt_from_adapter(
        CONTRACT,
        sha=sha,
        adapter=adapter,
        observation_id=observation_id,
    )
    return result, transport


def proof(**overrides):
    value = {
        "schema": delivery.PROOF_SCHEMA,
        "source": delivery.PILOT_SOURCE_ID,
        "project": delivery.PILOT_PROJECT,
        "sha": SHA,
        "state": "success",
        "provenance": delivery.PILOT_PROVENANCE,
        "facts": {delivery.PILOT_REQUIRED_FACT: True},
    }
    value.update(overrides)
    return value


def private_receipt(observations, **kwargs):
    options = {"observation_id": "receipt1", "observed_at": FIXED_TIME}
    options.update(kwargs)
    return delivery._delivery_receipt_from_observations(
        CONTRACT,
        sha=SHA,
        observations=observations,
        **options,
    )


def journal_append(monkeypatch, journal, value, *, observation_id="receipt1"):
    adapter, transport = github_adapter(monkeypatch, value)
    saved = journal.append(
        contract=CONTRACT,
        sha=SHA,
        adapter=adapter,
        observation_id=observation_id,
    )
    return saved, transport


def test_pilot_contract_is_detached_closed_and_digest_is_canonical():
    first = delivery.github_check_runs_pilot_contract()
    second = delivery.github_check_runs_pilot_contract()
    first["sources"][0]["id"] = "mutated"
    assert second == CONTRACT
    assert delivery.contract_digest(second) == delivery.contract_digest(copy.deepcopy(second))

    for forbidden in ("command", "hook", "credential", "deployment"):
        bad = copy.deepcopy(CONTRACT)
        bad[forbidden] = "anything"
        with pytest.raises(delivery.DeliveryContractError, match="unsupported fields"):
            delivery.validate_contract(bad)


@pytest.mark.parametrize(
    ("field", "substitution"),
    [
        ("id", "github_token"),
        ("adapter", "arbitrary_readonly"),
        ("adapter_version", "personal_access_token"),
        ("provenance", "secret_source"),
    ],
)
def test_pilot_rejects_credential_shaped_and_arbitrary_source_identity_substitutions(
    field,
    substitution,
):
    bad = copy.deepcopy(CONTRACT)
    bad["sources"][0][field] = substitution
    with pytest.raises(delivery.DeliveryContractError, match="unsupported by the delivery pilot"):
        delivery.validate_contract(bad)


def test_pilot_is_bound_to_one_concrete_project_source_and_requirement():
    bad_project = copy.deepcopy(CONTRACT)
    bad_project["project"] = "someone/another-public-repo"
    with pytest.raises(delivery.DeliveryContractError, match="contract project is unsupported"):
        delivery.validate_contract(bad_project)

    extra_source = copy.deepcopy(CONTRACT)
    extra_source["sources"].append(copy.deepcopy(extra_source["sources"][0]))
    with pytest.raises(delivery.DeliveryContractError, match="exactly one pilot source"):
        delivery.validate_contract(extra_source)

    arbitrary_fact = copy.deepcopy(CONTRACT)
    arbitrary_fact["requirements"][0]["required_facts"] = ["provider_says_yes"]
    with pytest.raises(delivery.DeliveryContractError, match="facts are unsupported"):
        delivery.validate_contract(arbitrary_fact)


@pytest.mark.parametrize(
    ("payload", "error", "verdict"),
    [
        (check_runs_payload(check_run()), None, "verified"),
        (check_runs_payload(check_run(sha=OTHER_SHA)), None, "wrong-sha"),
        (check_runs_payload(), None, "missing-proof"),
        (
            check_runs_payload(check_run(status="in_progress", conclusion=None)),
            None,
            "pending",
        ),
        (None, delivery.ProofSourceUnavailable("provider detail"), "inaccessible"),
        (
            check_runs_payload(check_run(conclusion="future_conclusion")),
            None,
            "unsupported-schema",
        ),
    ],
)
def test_concrete_adapter_maps_required_pilot_states_deterministically(
    monkeypatch, payload, error, verdict,
):
    result, transport = generated_receipt(monkeypatch, payload, error=error)
    assert result["verdict"] == verdict
    assert result["proofs"][0]["outcome"] == (
        "success" if verdict == "verified" else verdict
    )
    assert transport.calls == [(delivery.PILOT_PROJECT, SHA)]
    assert "provider detail" not in delivery._canonical(result)


@pytest.mark.parametrize(
    ("payload", "verdict"),
    [
        (
            check_runs_payload(
                check_run(conclusion="success"),
                check_run(conclusion="skipped"),
            ),
            "verified",
        ),
        (check_runs_payload(check_run(conclusion="skipped")), "missing-proof"),
        (check_runs_payload(check_run(conclusion="failure")), "failed-proof"),
        (
            check_runs_payload(
                check_run(conclusion="failure"),
                check_run(status="queued", conclusion=None),
            ),
            "pending",
        ),
        (check_runs_payload(check_run(), total_count=101), "unsupported-schema"),
    ],
)
def test_github_check_run_semantics_are_conservative(monkeypatch, payload, verdict):
    result, _ = generated_receipt(monkeypatch, payload)
    assert result["verdict"] == verdict


def test_required_null_fact_fails_closed_without_inventing_a_negative_fact():
    result = private_receipt(
        {
            delivery.PILOT_SOURCE_ID: proof(
                facts={delivery.PILOT_REQUIRED_FACT: None},
            ),
        },
    )
    assert result["verdict"] == "unavailable"
    assert result["proofs"][0]["outcome"] == "unavailable"


def test_raw_provider_payloads_are_rejected_and_false_facts_do_not_verify():
    invalid = proof(facts={delivery.PILOT_REQUIRED_FACT: "token=secret"})
    assert private_receipt({delivery.PILOT_SOURCE_ID: invalid})["verdict"] == "missing-proof"
    negative = proof(facts={delivery.PILOT_REQUIRED_FACT: False})
    assert private_receipt({delivery.PILOT_SOURCE_ID: negative})["verdict"] == "failed-proof"


def test_cross_project_and_provenance_mismatch_are_distinct_and_sanitized():
    cross_project = private_receipt(
        {delivery.PILOT_SOURCE_ID: proof(project="someone/another-repo")},
    )
    assert cross_project["verdict"] == "cross-project"

    mismatch = private_receipt(
        {delivery.PILOT_SOURCE_ID: proof(provenance="credential_shaped_value")},
    )
    assert mismatch["verdict"] == "provenance-mismatch"
    assert "credential_shaped_value" not in delivery._canonical(mismatch)
    assert "provenance" not in mismatch["proofs"][0]


def test_public_path_requires_the_concrete_adapter_not_a_spoofed_protocol():
    class SpoofedAdapter:
        source_id = delivery.PILOT_SOURCE_ID
        adapter = delivery.PILOT_ADAPTER
        adapter_version = delivery.PILOT_ADAPTER_VERSION
        provenance = delivery.PILOT_PROVENANCE

        def read_proof(self, *, project, sha):
            return proof(project=project, sha=sha)

    with pytest.raises(delivery.DeliveryContractError, match="requires GitHubCheckRunsAdapter"):
        delivery.read_delivery_proof(CONTRACT, sha=SHA, adapter=SpoofedAdapter())


def test_adapter_binds_the_exact_project_and_sha_before_transport(monkeypatch):
    adapter, transport = github_adapter(monkeypatch, check_runs_payload(check_run()))
    with pytest.raises(delivery.DeliveryContractError, match="outside the pilot"):
        adapter.read_proof(project="someone/another-repo", sha=SHA)
    with pytest.raises(delivery.DeliveryContractError, match="exact 40-character"):
        delivery.read_delivery_proof(CONTRACT, sha="main", adapter=adapter)
    assert transport.calls == []

    observed = delivery.read_delivery_proof(CONTRACT, sha=SHA, adapter=adapter)
    assert observed["project"] == delivery.PILOT_PROJECT
    assert observed["sha"] == SHA
    assert transport.calls == [(delivery.PILOT_PROJECT, SHA)]


def test_default_network_transport_is_fixed_host_get_only_and_has_no_credentials(monkeypatch):
    requests = []
    handlers = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(check_runs_payload(check_run())).encode("utf-8")

    class Opener:
        def open(self, request, *, timeout):
            requests.append((request, timeout))
            return Response()

    def build_opener(*configured_handlers):
        handlers.extend(configured_handlers)
        return Opener()

    monkeypatch.setattr(delivery.urllib.request, "build_opener", build_opener)
    result = delivery.delivery_receipt_from_adapter(
        CONTRACT,
        sha=SHA,
        adapter=delivery.GitHubCheckRunsAdapter(),
        observation_id="network1",
    )
    assert result["verdict"] == "verified"
    request, timeout = requests[0]
    assert request.get_method() == "GET"
    assert request.full_url == (
        f"https://api.github.com/repos/{delivery.PILOT_PROJECT}/commits/{SHA}"
        "/check-runs?per_page=100"
    )
    assert request.get_header("Authorization") is None
    assert timeout == 10.0
    assert any(
        isinstance(handler, urllib.request.ProxyHandler) and handler.proxies == {}
        for handler in handlers
    )
    parameters = inspect.signature(delivery.GitHubCheckRunsAdapter).parameters
    assert not {"token", "credential", "headers", "url"} & set(parameters)
    adapter = delivery.GitHubCheckRunsAdapter()
    assert not any(hasattr(adapter, name) for name in ("write", "post", "deploy", "merge", "rollback"))


def test_default_transport_maps_http_failure_to_inaccessible(monkeypatch):
    class Opener:
        def open(self, request, *, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, "secret provider text", {}, None)

    monkeypatch.setattr(delivery.urllib.request, "build_opener", lambda *_handlers: Opener())
    result = delivery.delivery_receipt_from_adapter(
        CONTRACT,
        sha=SHA,
        adapter=delivery.GitHubCheckRunsAdapter(),
        observation_id="network2",
    )
    assert result["verdict"] == "inaccessible"
    assert "secret provider text" not in delivery._canonical(result)


def test_public_generation_captures_time_internally_and_rejects_caller_timestamp(
    monkeypatch, tmp_path,
):
    adapter, _ = github_adapter(monkeypatch, check_runs_payload(check_run()))
    result = delivery.delivery_receipt_from_adapter(
        CONTRACT,
        sha=SHA,
        adapter=adapter,
        observation_id="clock1",
    )
    assert result["observed_at"] == FIXED_TIME

    adapter, _ = github_adapter(monkeypatch, check_runs_payload(check_run()))
    with pytest.raises(TypeError, match="observed_at"):
        delivery.delivery_receipt_from_adapter(
            CONTRACT,
            sha=SHA,
            adapter=adapter,
            observation_id="clock2",
            observed_at="2000-01-01T00:00:00Z",
        )

    adapter, _ = github_adapter(monkeypatch, check_runs_payload(check_run()))
    journal = delivery.DeliveryReceiptJournal(tmp_path / "delivery-receipts.jsonl")
    with pytest.raises(TypeError, match="observed_at"):
        journal.append(
            contract=CONTRACT,
            sha=SHA,
            adapter=adapter,
            observation_id="clock3",
            observed_at="2000-01-01T00:00:00Z",
        )


@pytest.mark.parametrize(
    "observed_at",
    ["one", "2026-09-22T10:00:00+02:00", "2026-99-22T10:00:00Z"],
)
def test_private_evaluator_still_rejects_noncanonical_timestamps(observed_at):
    with pytest.raises(delivery.DeliveryContractError, match="canonical UTC"):
        private_receipt(
            {delivery.PILOT_SOURCE_ID: proof()},
            observed_at=observed_at,
        )


def test_journal_derives_from_adapter_and_is_append_only_unique_bounded_and_detached(
    monkeypatch, tmp_path,
):
    journal = delivery.DeliveryReceiptJournal(tmp_path / "delivery-receipts.jsonl", max_receipts=2)
    first, first_transport = journal_append(
        monkeypatch, journal, check_runs_payload(check_run()),
    )
    assert first_transport.calls == [(delivery.PILOT_PROJECT, SHA)]
    first["verdict"] = "injected"
    assert journal.receipts()[0]["verdict"] == "verified"

    with pytest.raises(delivery.DeliveryContractError, match="already exists"):
        journal_append(monkeypatch, journal, check_runs_payload(check_run()))
    journal_append(
        monkeypatch,
        journal,
        check_runs_payload(check_run(conclusion="failure")),
        observation_id="receipt2",
    )
    with pytest.raises(delivery.DeliveryContractError, match="full"):
        journal_append(
            monkeypatch,
            journal,
            check_runs_payload(check_run()),
            observation_id="receipt3",
        )


def test_journal_cannot_append_a_caller_built_raw_receipt(tmp_path):
    journal = delivery.DeliveryReceiptJournal(tmp_path / "delivery-receipts.jsonl")
    forged = private_receipt({delivery.PILOT_SOURCE_ID: proof()})
    with pytest.raises(TypeError):
        journal.append(forged)
    with pytest.raises(TypeError, match="receipt"):
        journal.append(receipt=forged)
    assert not journal.path.exists()


def test_journal_revalidates_loaded_entries_and_refuses_append_after_tampering(
    monkeypatch, tmp_path,
):
    journal = delivery.DeliveryReceiptJournal(tmp_path / "delivery-receipts.jsonl")
    saved, _ = journal_append(monkeypatch, journal, check_runs_payload(check_run()))
    forged = copy.deepcopy(saved)
    forged["verdict"] = "failed-proof"
    journal.path.write_text(delivery._canonical(forged) + "\n", encoding="ascii")
    before = journal.path.read_bytes()

    with pytest.raises(delivery.DeliveryContractError, match="does not match proof outcomes"):
        journal.receipts()
    adapter, _ = github_adapter(monkeypatch, check_runs_payload(check_run()))
    with pytest.raises(delivery.DeliveryContractError, match="does not match proof outcomes"):
        journal.append(
            contract=CONTRACT,
            sha=SHA,
            adapter=adapter,
            observation_id="receipt2",
        )
    assert journal.path.read_bytes() == before


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda row: row["contract"].update(digest="0" * 64), "not the pilot contract"),
        (lambda row: row["proofs"][0].update(adapter="arbitrary_readonly"), "outside the pilot"),
        (lambda row: row["proofs"][0].update(raw_output="token=secret"), "unsupported fields"),
        (lambda row: row["proofs"][0].update(outcome="provider_says_yes"), "outcome is unsupported"),
    ],
)
def test_journal_loaded_schema_is_closed_and_pilot_bound(tmp_path, mutation, message):
    row = private_receipt({delivery.PILOT_SOURCE_ID: proof()})
    mutation(row)
    path = tmp_path / "delivery-receipts.jsonl"
    path.write_text(delivery._canonical(row) + "\n", encoding="ascii")
    with pytest.raises(delivery.DeliveryContractError, match=message):
        delivery.DeliveryReceiptJournal(path).receipts()


def test_journal_rejects_malformed_json_fail_closed(tmp_path):
    path = tmp_path / "delivery-receipts.jsonl"
    path.write_text("{not-json}\n", encoding="ascii")
    with pytest.raises(delivery.DeliveryContractError, match="journal is malformed"):
        delivery.DeliveryReceiptJournal(path).receipts()


def test_facades_are_canonically_identical_except_receipt_identifier(monkeypatch):
    claude_adapter, _ = github_adapter(monkeypatch, check_runs_payload(check_run()))
    codex_adapter, _ = github_adapter(monkeypatch, check_runs_payload(check_run()))
    claude = delivery.claude_delivery_receipt(
        CONTRACT,
        sha=SHA,
        adapter=claude_adapter,
        observation_id="claude1",
    )
    codex = delivery.codex_delivery_receipt(
        CONTRACT,
        sha=SHA,
        adapter=codex_adapter,
        observation_id="codex1",
    )
    claude.pop("receipt_id")
    codex.pop("receipt_id")
    assert claude == codex


@pytest.mark.parametrize(
    "forbidden",
    [
        {"observations": {delivery.PILOT_SOURCE_ID: proof()}},
        {"project": delivery.PILOT_PROJECT},
        {"source": delivery.PILOT_SOURCE_ID},
        {"observed_at": FIXED_TIME},
    ],
)
@pytest.mark.parametrize(
    "facade",
    [delivery.claude_delivery_receipt, delivery.codex_delivery_receipt],
)
def test_host_facades_reject_caller_evidence_identity_and_timestamp(
    monkeypatch, facade, forbidden,
):
    adapter, _ = github_adapter(monkeypatch, check_runs_payload(check_run()))
    with pytest.raises(TypeError):
        facade(
            CONTRACT,
            sha=SHA,
            adapter=adapter,
            observation_id="forged1",
            **forbidden,
        )


def test_pure_observation_evaluator_is_not_a_public_surface():
    assert not hasattr(delivery, "delivery_receipt")


def test_receipt_is_exact_sha_bound_and_has_no_authority_surface(monkeypatch):
    result, _ = generated_receipt(monkeypatch, check_runs_payload(check_run()))
    assert result["project"] == delivery.PILOT_PROJECT
    assert result["sha"] == SHA
    assert result["contract"]["digest"] == delivery.contract_digest(CONTRACT)
    assert result["proofs"][0] == {
        "requirement": delivery.PILOT_REQUIREMENT_ID,
        "source": delivery.PILOT_SOURCE_ID,
        "adapter": delivery.PILOT_ADAPTER,
        "adapter_version": delivery.PILOT_ADAPTER_VERSION,
        "source_provenance": delivery.PILOT_PROVENANCE,
        "provenance": delivery.PILOT_PROVENANCE,
        "outcome": "success",
    }
    assert not {"command", "deployment", "rollback", "merge", "tracker", "credential"} & set(result)
