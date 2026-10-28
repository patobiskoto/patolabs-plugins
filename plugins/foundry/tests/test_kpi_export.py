"""PLP-319: offline export in the devhub-observations.v2 format, on synthetic local state only."""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from foundry import escalation, invocation_log, kpi_export
from foundry.escalation import EscalationStore
from foundry.routing import RoutingConfigError
from test_review_turns import _attempt, _hex, _proof, _receipt, _snapshot
from test_token_attribution import TOOL_USE, _claude_request, _claude_tree

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO = "synthetic-repo"
ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,119}\Z")
COMMON = {"schema_version", "record_id", "kind", "anchor", "attempt_id", "attribution", "revision",
          "supersedes_id", "started_at", "ended_at", "observed_at", "source"}
BODY = {
    "attempt": {"role", "host", "tier", "effort", "model", "status", "failure_class", "tokens", "amount"},
    "review_round": {"head_sha", "rank", "review_type", "verdict", "ac_total", "ac_pass", "remark_count",
                     "implementer_model", "reviewer_model", "base_sha", "pr_number"},
    "escalation": {"role", "signal", "from_tier", "to_tier", "rank"},
}


def _check(record):
    """The strict shape of contract devhub-observations.v2 (the parts a producer controls)."""
    assert set(record) == COMMON | BODY[record["kind"]]
    assert record["schema_version"] == "devhub-observations.v2"
    assert ID.fullmatch(record["record_id"]) and ID.fullmatch(record["attempt_id"])
    assert record["anchor"]["type"] == "issue" and record["anchor"]["command_id"] is None
    assert record["attribution"] in {"exact", "window", "approximate", "unattributed"}
    assert record["revision"] == 1 and record["supersedes_id"] is None
    assert type(record["started_at"]) is int and type(record["observed_at"]) is int
    source = record["source"]
    assert set(source) == {"system", "producer", "schema", "fingerprint"} and source["system"] == "foundry"
    assert re.fullmatch(r"[0-9a-f]{64}", source["fingerprint"]) and ID.fullmatch(source["producer"])
    if record["kind"] == "attempt":
        assert (record["status"] == "completed") == (record["failure_class"] == "none")
        if record["role"] == "corrector":
            assert record["attribution"] in {"window", "approximate"}
        assert set(record["tokens"]) == {"input", "cached_input", "cache_write_input", "output", "reasoning_output"}
        amount = record["amount"]
        if amount is not None:
            assert amount["method"] == "estimated" and amount["rate_version"] and amount["currency"] == "USD"
            assert amount["unit"] == "micros" and re.fullmatch(r"[0-9]+", amount["amount_micros"])
            assert ID.fullmatch(amount["rate_version"])
    if record["kind"] == "review_round":
        assert re.fullmatch(r"[0-9a-f]{40}", record["head_sha"]) and record["rank"] >= 1
        assert record["review_type"] in {"full", "partial"} and record["verdict"] in {"mergeable", "blocked"}
        for name in ("implementer_model", "reviewer_model"):
            assert record[name] is None or ID.fullmatch(record[name])
    if record["kind"] == "escalation":
        assert record["rank"] >= 1 and ID.fullmatch(record["to_tier"])


def _build(state, issues=("PLP-1",), **kw):
    return kpi_export.build(list(issues), REPO, state_dir=state, **kw)


def _records(document):
    data = json.loads(document)
    return data, [r for b in data["batches"] for r in b["records"]]


def _routed(state, issue, role, ident, *, host="claude", model="claude-sonnet-5", tier="frontier",
            effort="high", at="2026-10-01T00:00:00.000000Z", status="routed", head=None, correction=None):
    invocation_log._append(invocation_log._path(REPO, state), {
        "event": "routed", "id": ident, "issue": issue, "host": host, "role": role,
        "model_requested": model, "effort": effort, "tier": tier, "at": at, "status": status,
        "head": head, "base": _hex("b", 40) if head else None, "correction": correction})


def _seed_reviews(state):
    first = _proof(state, "PLP-1", "1", ["pass", "fail"], "blocked", "2026-10-01T10:00:00Z")
    second = _proof(state, "PLP-1", "2", ["pass", "pass"], "mergeable", "2026-10-02T10:00:00Z", generation=2)
    third = _proof(state, "PLP-1", "3", ["pass"], "mergeable", "2026-10-03T10:00:00Z", generation=3)
    _proof(state, "PLP-1", "n", ["pass"], "mergeable")  # no ledger time: no record
    _receipt(state, second, [{"text": "SECRET-TEXT", "fix_before_merge": False, "target": "diff"}])
    return first, second, third


def test_review_rounds_follow_the_format_with_provenance(tmp_path):
    first, second, third = _seed_reviews(tmp_path)
    _routed(tmp_path, "PLP-1", "implementer", "a" * 16, model="claude-opus-5")
    _routed(tmp_path, "PLP-1", "reviewer", "b" * 16, model="claude-sonnet-5", head=second["coordinates"]["head"])
    _attempt(tmp_path, "PLP-1", third, partial=True)
    data, records = _records(_build(tmp_path))
    rounds = [r for r in records if r["kind"] == "review_round"]
    assert [r["rank"] for r in rounds] == [1, 2, 3]
    one, two, three = rounds[:3]
    for record in records:
        _check(record)
    assert (one["review_type"], one["verdict"], one["ac_total"], one["ac_pass"]) == ("full", "blocked", 2, 1)
    assert two["review_type"] == "full" and two["remark_count"] == 1  # after a blocking round: deduced full
    assert (two["implementer_model"], two["reviewer_model"]) == ("claude-opus-5", "claude-sonnet-5")
    assert three["review_type"] == "partial"  # campaign journal
    assert one["head_sha"] == first["coordinates"]["head"] and one["base_sha"] == _hex("b", 40)
    assert one["pr_number"] is None
    assert (one["started_at"], one["ended_at"], one["observed_at"]) == (1790848800000,) * 3
    prov = data["provenance"][two["record_id"]]
    assert "deduced" in prov["from"]["review_type"] and "AGENTS.md#R9" in prov["from"]["review_type"]
    assert set(prov["from"]) | set(prov["null"]) >= set(BODY["review_round"]) | {"started_at", "ended_at"}
    assert prov["null"]["pr_number"] == "no_campaign_record_of_a_pull_request"
    assert data["provenance"][one["record_id"]]["null"]["reviewer_model"] == "no_reviewer_record_for_head"


def test_unknown_review_type_is_omitted_with_its_reason(tmp_path):
    _proof(tmp_path, "PLP-1", "1", ["pass"], "mergeable", "2026-10-01T10:00:00Z")
    _proof(tmp_path, "PLP-1", "2", ["pass"], "mergeable", "2026-10-02T10:00:00Z", generation=2)
    data, records = _records(_build(tmp_path))
    assert [r["rank"] for r in records] == [1]
    assert data["omitted"] == [{"issue": "PLP-1", "kind": "review_round", "rank": 2,
                                "reason": "review_type_unrecorded:no_campaign_record"}]


def test_pull_request_number_comes_from_the_started_review_record(tmp_path):
    proof = _proof(tmp_path, "PLP-1", "1", ["pass"], "mergeable", "2026-10-01T10:00:00Z")
    line = {"event": "started", "kind": "review", "ticket": "PLP-1", "schema_version": "foundry-campaign-attempts.v1",
            "attempt": "a" * 8, "epic": "PLP-E", "at": "2026-10-01T00:00:00Z", "route": None, "session_id": None,
            "fingerprint": {"state": "review", "branch_sha": proof["coordinates"]["head"], "pr": 42,
                            "pr_head": proof["coordinates"]["head"]}}
    with (tmp_path / "campaign-attempts.jsonl").open("a") as handle:
        handle.write(json.dumps(line) + "\n")
    _, records = _records(_build(tmp_path))
    assert records[0]["pr_number"] == 42


def test_attempts_tokens_and_estimated_amount(tmp_path):
    state = tmp_path / "state"
    ident = invocation_log.claude_invocation_id(TOOL_USE)
    _routed(state, "PLP-1", "implementer", ident, status="routed")
    _routed(state, "PLP-1", "implementer", "c" * 16, correction=True, model="claude-sonnet-5-5",
            at="2026-10-01T01:00:00.000000Z")
    _routed(state, "PLP-1", "reviewer", "d" * 16, model="claude-opus-5", effort="xhigh",
            at="2026-10-01T02:00:00.000000Z")
    invocation_log._append(invocation_log._path(REPO, state), {
        "event": "completed", "id": "d" * 16, "at": "2026-10-01T03:00:00.000000Z",
        "status": "failed", "failure_class": "host"})
    main = _claude_tree(tmp_path, [], [("exact", [_claude_request("2026-10-01T10:40:00.000Z", 5, "sub1", "a")],
                                        {"toolUseId": TOOL_USE}, "plain text")])
    _, without = _records(_build(state))
    _, records = _records(_build(state, claude_sessions=[main]))
    for record in without + records:
        _check(record)
    assert all(r["tokens"]["input"] is None and r["amount"] is None for r in without)
    plain, corrector, reviewer = records
    assert (plain["role"], plain["attribution"], plain["status"], plain["failure_class"]) == (
        "implementer", "exact", "unknown", "unknown")
    assert plain["tokens"] == {"input": 5, "cached_input": 50, "cache_write_input": 500, "output": 5000,
                               "reasoning_output": None}
    assert plain["amount"] == {"amount_micros": "51270", "currency": "USD", "unit": "micros",
                               "method": "estimated", "completeness": "complete",
                               "rate_version": plain["amount"]["rate_version"]}
    assert plain["amount"]["rate_version"].startswith("pricing-v1-")
    assert (corrector["role"], corrector["attribution"]) == ("corrector", "approximate")
    assert corrector["tokens"]["input"] is None and corrector["amount"] is None  # absent from the logs
    assert (reviewer["status"], reviewer["failure_class"], reviewer["model"]) == (
        "failed", "host", {"configured": "claude-opus-5", "served": None})
    assert reviewer["ended_at"] is None and reviewer["effort"] == "xhigh" and reviewer["tier"] == "frontier"


def test_background_launch_attempt_is_unknown_without_end_time_and_says_why(tmp_path):
    """PLP-329: an `unknown` completion is never emitted as completed and has no end time."""
    state = tmp_path / "state"
    _routed(state, "PLP-1", "implementer", "e" * 16)
    invocation_log._append(invocation_log._path(REPO, state), {
        "event": "completed", "id": "e" * 16, "at": "2026-10-01T00:00:01.000000Z",
        "status": "unknown", "failure_class": "unknown"})
    data, records = _records(_build(state))
    _check(records[0])
    assert (records[0]["status"], records[0]["failure_class"], records[0]["ended_at"]) == (
        "unknown", "unknown", None)
    assert "background launch" in data["provenance"][records[0]["record_id"]]["from"]["status"]


def test_unpriced_model_gives_a_null_amount_with_a_reason(tmp_path):
    state = tmp_path / "state"
    ident = invocation_log.claude_invocation_id(TOOL_USE)
    _routed(state, "PLP-1", "implementer", ident, model="sonnet-6")
    main = _claude_tree(tmp_path, [], [("exact", [_claude_request("2026-10-01T10:40:00.000Z", 1, "sub1", "a")],
                                        {"toolUseId": TOOL_USE}, "plain text")])
    data, records = _records(_build(state, claude_sessions=[main]))
    assert records[0]["amount"] is None and records[0]["tokens"]["output"] == 1000
    assert data["provenance"][records[0]["record_id"]]["null"]["amount"] == "no_price_for_model_and_date"


PINNED_RATE_VERSIONS = {  # entries of pricing-v1.json before PLP-328: their bytes must never change
    ("claude", "haiku-4.5", "2026-01-01"): "8cc7dd584df5",
    ("claude", "sonnet-5", "2026-01-01"): "95cb73d6355c",
    ("claude", "opus-5", "2026-01-01"): "1ac7c42a691f",
    ("claude", "fable-5", "2026-01-01"): "b15851a07309",
    ("codex", "gpt-5.6-luna", "2026-01-01"): "45f45cd5ba4f",
    ("codex", "gpt-5.6-terra", "2026-01-01"): "da6ec9998579",
    ("codex", "gpt-5.6-sol", "2026-01-01"): "43369fc0b60c",
    ("codex", "gpt-5.6-sol", "2026-09-01"): "7c50998a55b7",
    ("codex", "gpt-6-astra", "2026-01-01"): "003d0e395734",
}


def test_existing_entries_keep_their_rate_version():
    entries = json.loads((PLUGIN_ROOT / "tooling" / "foundry" / "pricing-v1.json").read_text())["entries"]
    seen = {(e["host"], e["model"], e["effective_from"]):
            hashlib.sha256(kpi_export._canonical(e).encode("utf-8")).hexdigest()[:12] for e in entries}
    assert {k: seen[k] for k in PINNED_RATE_VERSIONS} == PINNED_RATE_VERSIONS
    assert set(seen) - set(PINNED_RATE_VERSIONS) == {
        ("claude", m, "2026-10-27") for m in ("sonnet-5.5", "haiku-5.5", "opus-5.5", "fable-5.1")}


def _attempt_amount(tmp_path, name, model, day):
    state = tmp_path / f"state-{name}"
    _routed(state, "PLP-1", "implementer", invocation_log.claude_invocation_id(TOOL_USE), model=model,
            at=f"{day}T00:00:00.000000Z")
    main = _claude_tree(tmp_path / name, [], [("exact", [_claude_request(f"{day}T10:40:00.000Z", 5, "sub1", "a")],
                                                {"toolUseId": TOOL_USE}, "plain text")])
    data, records = _records(_build(state, claude_sessions=[main]))
    return records[0]["amount"], data["provenance"][records[0]["record_id"]]["null"].get("amount")


def test_every_new_model_gets_an_estimated_amount(tmp_path):
    # tokens of the synthetic request: 5 input, 50 cached, 500 cache write, 5000 output
    expected = {"sonnet-5.5": "51265", "claude-sonnet-5-5": "51265", "claude-sonnet-5-5-20261101": "51265",
                "opus-5.5": "102530", "claude-opus-5-5": "102530", "fable-5.1": "256313"}
    for n, (model, micros) in enumerate(expected.items()):
        amount, why = _attempt_amount(tmp_path, f"m{n}", model, "2026-10-28")
        assert (amount["amount_micros"], amount["method"], why) == (micros, "estimated", None), model
        assert amount["rate_version"].startswith("pricing-v1-")
    # haiku-5.5 is priced request by request: context 555 is under the 100000 threshold,
    # 0.5 + 0.5 + 62.5 + 2500 = 2563.5 rounded half up
    amount, why = _attempt_amount(tmp_path, "h", "haiku-5.5", "2026-10-28")
    assert (amount["amount_micros"], amount["method"], why) == ("2564", "estimated", None)
    # the historical models keep their own rate, and the new entries start on 2026-10-27
    assert _attempt_amount(tmp_path, "s", "claude-sonnet-5", "2026-10-28")[0]["amount_micros"] == "51270"
    assert _attempt_amount(tmp_path, "e", "sonnet-5.5", "2026-10-26") == (None, "no_price_for_model_and_date")


def test_threshold_pricing_is_applied_per_request(tmp_path):
    state = tmp_path / "state"
    _routed(state, "PLP-1", "implementer", invocation_log.claude_invocation_id(TOOL_USE), model="haiku-5.5",
            at="2026-10-28T00:00:00.000000Z")
    # request 1 (n=5 of _claude_request): context 555, base rates; request 2 (n=1000): context 111000 > 100000, over rates
    requests = [_claude_request("2026-10-28T10:40:00.000Z", 5, "sub1", "a"),
                _claude_request("2026-10-28T10:41:00.000Z", 1000, "sub1", "b")]
    main = _claude_tree(tmp_path / "t", [], [("exact", requests, {"toolUseId": TOOL_USE}, "plain text")])
    _, records = _records(_build(state, claude_sessions=[main]))
    # base: 0.5 + 0.5 + 62.5 + 2500 = 2563.5 ; over: 500 + 500 + 62500 + 2500000 = 2563500 ; sum 2566063.5
    assert records[0]["amount"]["amount_micros"] == "2566064"
    # the same totals priced as one request are over the threshold as a whole: 2576317.5, not the sum above
    grid = json.loads((PLUGIN_ROOT / "tooling" / "foundry" / "pricing-v1.json").read_text())
    totals = {"input_tokens": 1005, "cached_input_tokens": 10050, "cache_write_input_tokens": 100500,
              "output_tokens": 1005000}
    one, _ = kpi_export._amount(grid, "claude", "haiku-5.5", "2026-10-28", totals, [totals])
    assert one["amount_micros"] == "2576318"


def test_amount_is_partial_only_when_a_one_hour_cache_write_may_be_included():
    grid = json.loads((PLUGIN_ROOT / "tooling" / "foundry" / "pricing-v1.json").read_text())
    tokens = {"input_tokens": 5, "cached_input_tokens": 50, "cache_write_input_tokens": 500, "output_tokens": 5000}
    no_write = {**tokens, "cache_write_input_tokens": 0}

    def completeness(model, usage):
        return kpi_export._amount(grid, "claude", model, "2026-10-28", usage)[0]["completeness"]

    # the sonnet-5.5 profiles ask for 1h cache writes (PAT-134), priced at the 5m rate: a lower bound
    assert completeness("sonnet-5.5", tokens) == "partial"
    assert completeness("claude-sonnet-5-5", tokens) == "partial"
    assert completeness("sonnet-5.5", no_write) == "complete"
    # the other pins carry no 1h request; the number itself does not change
    assert completeness("opus-5.5", tokens) == "complete"
    assert completeness("claude-sonnet-5", tokens) == "complete"
    assert kpi_export._amount(grid, "claude", "sonnet-5.5", "2026-10-28", tokens)[0]["amount_micros"] == "51265"


def test_partial_amount_says_so_in_its_provenance(tmp_path):
    state = tmp_path / "state"
    _routed(state, "PLP-1", "implementer", invocation_log.claude_invocation_id(TOOL_USE), model="sonnet-5.5",
            at="2026-10-28T00:00:00.000000Z")
    main = _claude_tree(tmp_path / "t", [], [("exact", [_claude_request("2026-10-28T10:40:00.000Z", 5, "sub1", "a")],
                                              {"toolUseId": TOOL_USE}, "plain text")])
    data, records = _records(_build(state, claude_sessions=[main]))
    assert records[0]["amount"]["completeness"] == "partial"
    assert "lower bound" in data["provenance"][records[0]["record_id"]]["from"]["amount"]


def test_attempt_provenance_tells_a_routed_attempt_from_a_background_launch(tmp_path):
    state = tmp_path / "state"
    _routed(state, "PLP-1", "implementer", "a" * 16, host="codex", at="2026-10-28T00:00:00.000000Z")
    _routed(state, "PLP-1", "implementer", "b" * 16, at="2026-10-28T00:01:00.000000Z")
    invocation_log._append(invocation_log._path(REPO, state), {
        "event": "completed", "id": "b" * 16, "at": "2026-10-28T00:01:01.000000Z",
        "status": "unknown", "failure_class": "unknown"})
    data, records = _records(_build(state))
    by_host = {r["host"]: data["provenance"][r["record_id"]]["from"]["status"] for r in records}
    assert "no completion event" in by_host["codex"] and "background" not in by_host["codex"]
    assert "background launch" in by_host["claude"] and "no completion event" not in by_host["claude"]
    assert {r["status"] for r in records} == {"unknown"}


def test_threshold_amount_stays_null_without_request_granularity():
    grid = json.loads((PLUGIN_ROOT / "tooling" / "foundry" / "pricing-v1.json").read_text())
    tokens = {"input_tokens": 5, "cached_input_tokens": 50, "cache_write_input_tokens": 500, "output_tokens": 5000}
    assert kpi_export._amount(grid, "claude", "haiku-5.5", "2026-10-28", tokens, None) == (
        None, "threshold_pricing_needs_per_request_tokens")
    assert kpi_export._amount(grid, "claude", "haiku-5.5", "2026-10-28", tokens, [tokens])[0]["amount_micros"] == "2564"


def _escalate(state, issue="PLP-1"):
    store = EscalationStore(REPO, state_dir=state)
    for index in range(2):
        store.record_failure(issue, "implementer", "test_red", "balanced", idempotency_key=f"key-0000000{index}")


def _strip_decided_at(state, issue="PLP-1"):
    """Rewrite the ledger as an older release wrote it: receipts without ``decided_at``."""
    path = EscalationStore(REPO, state_dir=state).directory / f"{issue}.json"
    ledger = json.loads(path.read_text())
    for receipt in ledger["failure_receipts"].values():
        receipt.pop("decided_at", None)
    path.write_text(json.dumps(ledger))


def test_escalation_comes_from_the_ledger_and_is_timed_by_the_first_use_of_the_tier(tmp_path):
    _escalate(tmp_path)
    _strip_decided_at(tmp_path)
    _, records = _records(_build(tmp_path))
    assert records == []  # no invocation at the new tier: no recorded time
    data, _ = _records(_build(tmp_path))
    assert data["omitted"] == [{"issue": "PLP-1", "kind": "escalation", "rank": 1, "role": "implementer",
                                "reason": "no_recorded_time"}]
    _routed(tmp_path, "PLP-1", "implementer", "e" * 16, tier="balanced", at="2026-10-01T00:00:00.000000Z")
    _routed(tmp_path, "PLP-1", "implementer", "f" * 16, tier="frontier", at="2026-10-02T00:00:00.000000Z")
    data, records = _records(_build(tmp_path))
    record = [r for r in records if r["kind"] == "escalation"][0]
    _check(record)
    assert (record["role"], record["signal"], record["from_tier"], record["to_tier"], record["rank"]) == (
        "implementer", "test_red", "balanced", "frontier", 1)
    assert record["started_at"] == record["observed_at"] == 1790899200000
    assert "deduced" in data["provenance"][record["record_id"]]["from"]["started_at"]
    # the idempotency key does not depend on the order or the time of the export
    assert record["attempt_id"] == "es-" + hashlib.sha256(b"escalation|PLP-1|implementer|1").hexdigest()[:32]


def test_new_escalation_receipt_is_timed_by_its_observed_decision_time(tmp_path, monkeypatch):
    monkeypatch.setattr(escalation, "_now_utc", lambda: "2026-10-05T12:00:00.250000Z")
    _escalate(tmp_path)
    _routed(tmp_path, "PLP-1", "implementer", "f" * 16, tier="frontier", at="2026-10-09T00:00:00.000000Z")
    data, records = _records(_build(tmp_path))
    record = [r for r in records if r["kind"] == "escalation"][0]
    _check(record)
    assert record["started_at"] == record["observed_at"] == 1791201600250
    source = data["provenance"][record["record_id"]]["from"]["started_at"]
    assert source.startswith("observed") and "deduced" not in source
    # an escalation without any routed invocation is now exported too
    state = tmp_path / "other"
    _escalate(state)
    _, records = _records(_build(state))
    assert [r["kind"] for r in records] == ["escalation"]
    # the stamp changes neither the record identity nor the idempotency key
    _strip_decided_at(tmp_path)
    _, old = _records(_build(tmp_path))
    old_record = [r for r in old if r["kind"] == "escalation"][0]
    assert (old_record["record_id"], old_record["attempt_id"]) == (record["record_id"], record["attempt_id"])
    assert old_record["started_at"] == 1791504000000


def test_only_escalated_receipts_carry_decided_at_and_old_ledgers_stay_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(escalation, "_now_utc", lambda: "2026-10-05T12:00:00Z")
    _escalate(tmp_path)
    store = EscalationStore(REPO, state_dir=tmp_path)
    receipts = json.loads((store.directory / "PLP-1.json").read_text())["failure_receipts"]
    stamped = {r["decision"]["action"]: r.get("decided_at") for r in receipts.values()}
    assert stamped == {"failure_recorded": None, "escalated": "2026-10-05T12:00:00Z"}
    _strip_decided_at(tmp_path)
    assert store._normalize_state(json.loads((store.directory / "PLP-1.json").read_text()), "PLP-1")
    # replaying a key returns the same decision and does not restamp the receipt
    monkeypatch.setattr(escalation, "_now_utc", lambda: "2027-01-01T00:00:00Z")
    store.record_failure("PLP-1", "implementer", "test_red", "balanced", idempotency_key="key-00000001")
    assert "decided_at" not in json.loads((store.directory / "PLP-1.json").read_text())["failure_receipts"][
        "key-00000001"]


@pytest.mark.parametrize("action, stamp", [
    ("failure_recorded", "2026-10-05T12:00:00Z"),  # a receipt that is not an escalation
    ("escalated", "2026-10-05 12:00:00"),  # malformed timestamp
    ("escalated", 1790899200),  # not a string
])
def test_ledger_with_a_misplaced_or_malformed_decided_at_is_invalid(tmp_path, action, stamp):
    _escalate(tmp_path)
    store = EscalationStore(REPO, state_dir=tmp_path)
    path = store.directory / "PLP-1.json"
    ledger = json.loads(path.read_text())
    target = [r for r in ledger["failure_receipts"].values() if r["decision"]["action"] == action]
    assert len(target) == 1
    target[0]["decided_at"] = stamp
    with pytest.raises(RoutingConfigError, match="état d'escalade invalide pour PLP-1"):
        store._normalize_state(ledger, "PLP-1")


def test_no_decision_path_reads_decided_at():
    root = PLUGIN_ROOT / "tooling" / "foundry"
    readers = {p.name for p in root.rglob("*.py") if "decided_at" in p.read_text()}
    assert readers == {"escalation.py", "kpi_export.py"}
    # in escalation.py the field is only validated on load, copied by the normalizer and written by the recorder
    uses = [line.strip() for line in (root / "escalation.py").read_text().splitlines() if "decided_at" in line]
    assert uses == [
        '_FAILURE_RECEIPT_OPTIONAL_KEYS = {"decided_at"}',
        'decided_at = receipt.get("decided_at")',
        'if "decided_at" in receipt and (',
        'decision.action != "escalated" or _utc_timestamp(decided_at) is None',
        'if "decided_at" in receipt:',
        'failure_receipts[idempotency_key]["decided_at"] = decided_at',
        'state["failure_receipts"][receipt_key]["decided_at"] = _now_utc()',
    ]
    # kpi_export reads it in one function only
    assert "decided_at" not in (root / "kpi_export.py").read_text().split("def _escalations")[0]


def test_same_inputs_same_bytes_and_read_only(tmp_path):
    state = tmp_path / "state"
    _seed_reviews(state)
    _escalate(state)
    _routed(state, "PLP-1", "implementer", "a" * 16, tier="frontier")
    before = _snapshot(state)
    first = _build(state, issues=("PLP-1", "PLP-2"))
    assert _build(state, issues=("PLP-2", "PLP-1", "PLP-1")) == first
    assert _snapshot(state) == before
    assert first.endswith(b"\n") and first == (json.dumps(json.loads(first), sort_keys=True, indent=2) + "\n").encode()
    data = json.loads(first)
    ids = [r["record_id"] for b in data["batches"] for r in b["records"]]
    assert len(ids) == len(set(ids)) == sum(data["counts"].values()) and set(data["provenance"]) == set(ids)


def test_batches_hold_at_most_one_hundred_records(tmp_path, monkeypatch):
    for index in range(1, 6):
        _proof(tmp_path, "PLP-1", str(index), ["fail"], "blocked", f"2026-10-0{index}T10:00:00Z", generation=index)
    monkeypatch.setattr(kpi_export, "BATCH_SIZE", 2)
    data = json.loads(_build(tmp_path))
    assert [len(b["records"]) for b in data["batches"]] == [2, 2, 1]
    assert all(b["schema_version"] == "devhub-observations.v2" for b in data["batches"])


def test_output_is_content_free(tmp_path):
    state = tmp_path / "state"
    first, second, _ = _seed_reviews(state)
    _routed(state, "PLP-1", "implementer", invocation_log.claude_invocation_id(TOOL_USE))
    text = _build(state).decode()
    for forbidden in (first["proof_id"], first["coordinates"]["diff_hash"], "SECRET-TEXT", TOOL_USE,
                      invocation_log.claude_invocation_id(TOOL_USE), "a" * 16, str(tmp_path), "toolu"):
        assert forbidden not in text


def test_rejects_an_invalid_issue_identifier(tmp_path):
    try:
        _build(tmp_path, issues=("../x",))
    except ValueError:
        return
    raise AssertionError("an invalid issue identifier was accepted")


def test_module_has_no_network_tracker_or_write_surface():
    source = (PLUGIN_ROOT / "tooling" / "foundry" / "kpi_export.py").read_text()
    imports = set(re.findall(r"^(?:from|import) ([\w.]+)", source, re.M))
    assert imports <= {"__future__", "hashlib", "json", "os", "re", "datetime", "decimal", "pathlib", "foundry",
                       "foundry.escalation", "foundry.routing",
                       "foundry.routing_facades"}  # only the CLAUDE_CACHE_TTL_1H_PINS constant is read (PLP-14)
    assert not re.search(r"\b(write_text|write_bytes|urlopen|socket|subprocess|record_routed|_append|"
                         r"mkdir|unlink|replace|rename|open\(.*['\"][wax])", source)
    assert "O_RDONLY" in source and "O_WRONLY" not in source and "O_CREAT" not in source


def test_only_the_cli_verb_imports_kpi_export():
    importers = {p.name for p in (PLUGIN_ROOT / "tooling").rglob("*.py")
                 if re.search(r"^\s*(from foundry import .*\bkpi_export\b|from foundry\.kpi_export\b)",
                              p.read_text(), re.M)}
    assert importers == {"routing.py"}


def test_cli_verb_writes_only_the_named_file(tmp_path):
    state = tmp_path / "state"
    _seed_reviews(state)
    out = tmp_path / "out" / "export.json"
    out.parent.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "FOUNDRY_DATA": str(state),
           "PYTHONPATH": str(PLUGIN_ROOT / "tooling")}
    before = _snapshot(state)
    cmd = [sys.executable, "-m", "foundry.routing", "kpi-export", "PLP-1", "--output", str(out),
           "--repository", REPO]
    done = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["review_round"] == 2
    assert [p.name for p in out.parent.iterdir()] == ["export.json"] and _snapshot(state) == before
    first = out.read_bytes()
    assert subprocess.run(cmd, capture_output=True, env=env).returncode == 0 and out.read_bytes() == first
    assert json.loads(first)["export_schema"] == "foundry-kpi-export.v1"
