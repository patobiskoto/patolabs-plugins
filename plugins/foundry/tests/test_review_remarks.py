"""PAT-141 / PAT-ADR-0028: the local receipt of the remarks of a validated review.

A real temporary Git repository and a real proof; no network, tracker or user configuration.
"""
import json

import pytest

import test_review_carry as carry
from foundry import campaign_review as cr
from foundry import review_remarks as rr
from foundry.routing import AcceptanceProofStore
from test_campaign_effects import CRITERION, remarks_of, result_text

REMARKS = [{"text": "fix the typo", "fix_before_merge": True},
           {"text": "rename the helper", "fix_before_merge": False}]


def _proof(tmp_path, **kwargs):
    scenario = carry.Scenario(tmp_path, **kwargs)
    proof = AcceptanceProofStore(carry.REPOSITORY, scenario.state).validated_proof(
        AcceptanceProofStore(carry.REPOSITORY, scenario.state).directory
        / scenario.proof["proof_id"])
    return scenario, proof


def _record(scenario, proof, remarks=REMARKS):
    return rr.ReviewRemarksStore(carry.REPOSITORY, scenario.state).record(
        proof, remarks, issue=carry.ISSUE, diff_hash=scenario.old_hash, base=scenario.old_base)


def test_the_receipt_is_bound_to_the_proof_the_diff_and_the_generation(tmp_path):
    scenario, proof = _proof(tmp_path)
    receipt = _record(scenario, proof)
    assert receipt["proof_id"] == proof["proof_id"] and receipt["generation"] == 1
    assert (receipt["diff_hash"], receipt["base"]) == (scenario.old_hash, scenario.old_base)
    store = rr.ReviewRemarksStore(carry.REPOSITORY, scenario.state)
    assert store.find(carry.ISSUE, scenario.old_hash)["remarks"] == REMARKS
    assert store.find(carry.ISSUE, "0" * 64) is None and store.find("OTHER-1", scenario.old_hash) is None
    path = store.directory / proof["proof_id"]
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_the_receipt_is_append_only_and_leaves_the_proof_untouched(tmp_path):
    scenario, proof = _proof(tmp_path)
    before = scenario.snapshot()
    _record(scenario, proof)
    _record(scenario, proof)  # an identical replay is accepted
    with pytest.raises(rr.ReviewRemarksError):
        _record(scenario, proof, [{"text": "other", "fix_before_merge": False}])
    after = scenario.snapshot()
    assert {k: v for k, v in after.items() if k in before} == before  # nothing existing changed
    assert all(not name.startswith("acceptance-proofs") for name in set(after) - set(before))
    # the merge gate still validates the proof: the receipt is not a part of it
    AcceptanceProofStore(carry.REPOSITORY, scenario.state).validated_proof(
        AcceptanceProofStore(carry.REPOSITORY, scenario.state).directory / proof["proof_id"])


def test_a_blocked_proof_or_divergent_coordinates_carry_no_receipt(tmp_path):
    scenario, proof = _proof(tmp_path / "a", quality="blocked", verdict="fail")
    with pytest.raises(rr.ReviewRemarksError):
        _record(scenario, proof)
    scenario, proof = _proof(tmp_path / "b")
    store = rr.ReviewRemarksStore(carry.REPOSITORY, scenario.state)
    with pytest.raises(rr.ReviewRemarksError):
        store.record(proof, REMARKS, issue="OTHER-1", diff_hash=scenario.old_hash,
                     base=scenario.old_base)
    with pytest.raises(rr.ReviewRemarksError):
        store.record(proof, REMARKS, issue=carry.ISSUE, diff_hash="1" * 64, base=scenario.old_base)


@pytest.mark.parametrize("remarks", [
    "x", [{"text": "", "fix_before_merge": False}], [{"text": "a"}],
    [{"text": "a", "fix_before_merge": 1}], [{"text": "a" * 2001, "fix_before_merge": False}],
    [{"text": "a", "fix_before_merge": False, "x": 1}],
])
def test_remarks_are_a_closed_bounded_shape(remarks):
    with pytest.raises(rr.ReviewRemarksError):
        rr.validate_remarks(remarks)


def test_a_corrupted_receipt_is_a_closed_failure(tmp_path):
    scenario, proof = _proof(tmp_path)
    _record(scenario, proof)
    store = rr.ReviewRemarksStore(carry.REPOSITORY, scenario.state)
    (store.directory / proof["proof_id"]).write_text("{not json", encoding="utf-8")
    with pytest.raises(rr.ReviewRemarksError):
        store.find(carry.ISSUE, scenario.old_hash)


def test_cli_record_review_remarks_binds_to_the_proof(tmp_path, monkeypatch, capsys):
    from foundry import routing

    scenario, proof = _proof(tmp_path)
    monkeypatch.setattr(routing.registry, "data_dir", lambda: str(scenario.state))
    file = tmp_path / "remarks.json"
    file.write_text(json.dumps(REMARKS), encoding="utf-8")
    args = ["record-review-remarks", "--issue", carry.ISSUE, "--root", str(scenario.root),
            "--base", scenario.old_base, "--diff-hash", scenario.old_hash,
            "--proof-id", proof["proof_id"], "--remarks-file", str(file),
            "--repository", carry.REPOSITORY]
    routing.main(args)
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"proof_id": proof["proof_id"], "generation": 1, "remarks": 2}
    with pytest.raises(SystemExit):  # a different proof id has no proof
        routing.main([a if a != proof["proof_id"] else "9" * 64 for a in args])


# ------------------------------------------------------------ the reviewer's output


def _data(**change):
    base = json.loads(result_text(fix=1, nits=2).split(cr.RESULT_SENTINEL + "\n")[1])
    return {**base, **change}


def _parse(data):
    return cr.parse_result("report\n" + cr.RESULT_SENTINEL + "\n" + json.dumps(data))


def test_the_reviewer_output_carries_the_remarks_and_the_counts_agree():
    report, verdict = _parse(_data())
    assert report == "report"
    assert verdict["remarks"] == remarks_of(1, 2)
    assert (verdict["fix_before_merge"], verdict["non_blocking"]) == (1, 2)
    assert CRITERION["id"] == verdict["outcomes"][0]["id"]


def test_the_reviewer_output_is_closed_and_strict_about_remarks():
    for bad in (
        {"remarks": remarks_of(1, 1)},  # counts say 1 + 2
        {"remarks": remarks_of(0, 2) + [{"text": "x", "fix_before_merge": False}]},
        {"remarks": "none"},
        {"remarks": [{"text": "", "fix_before_merge": False}] * 3},
        {"remarks": [{"text": "a", "fix_before_merge": "yes"}] * 3},
        {"remarks": [{"text": "a"}] * 3},
    ):
        with pytest.raises(cr.ReviewOutputInvalid):
            _parse(_data(**bad))
    without = _data()
    del without["remarks"]
    with pytest.raises(cr.ReviewOutputInvalid):  # the old four-key shape is refused
        _parse(without)
    with pytest.raises(cr.ReviewOutputInvalid):
        _parse(_data(extra=1))


def test_the_reviewer_contract_names_the_remarks_key():
    contract = cr.review_contract()
    assert "remarks" in contract and "fix_before_merge" in contract
    assert str(cr.MAX_REMARK_TEXT) in contract
