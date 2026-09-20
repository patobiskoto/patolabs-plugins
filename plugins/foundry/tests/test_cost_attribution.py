import json

import pytest

from foundry.cost_attribution import (
    KNOWN_MODELS, CostAttributionError, aggregate, cost_record, load_price_grid, price_for,
    main, read_host_log, reconcile_pilot_v1, resolve_project,
)


def _grid(tmp_path, entries):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"schema_version": 1, "currency": "USD", "entries": entries}))
    return load_price_grid(path)


def _entry(**extra):
    value = {
        "host": "codex", "model": "gpt-test", "aliases": ["gpt-test-*"],
        "billing_mode": "api", "source": "https://example.test/pricing",
        "captured_at": "2026-09-11T00:00:00Z", "effective_from": "2026-09-01",
        "effective_to": "2026-09-30",
        "rates": {"input": 2, "cached_input": 1, "cache_write_input": 3, "output": 4},
    }
    value.update(extra)
    return value


def _record(**extra):
    value = {
        "host": "codex", "model": "gpt-test-v2", "occurred_on": "2026-09-11",
        "tokens": {"input_tokens": 10, "cached_input_tokens": 2,
                   "cache_write_input_tokens": 3, "output_tokens": 5,
                   "reasoning_output_tokens": 4},
        "project": "FOUNDRY", "epic": "FOUNDRY-1", "issue": "FOUNDRY-121",
        "provenance": "host_reported",
    }
    value.update(extra)
    return value


def test_versioned_dated_prefix_price_and_threshold_excess_are_deterministic(tmp_path):
    grid = _grid(tmp_path, [_entry(
        context_threshold_tokens=12,
        over_threshold_rates={"input": 5, "cached_input": 1,
                              "cache_write_input": 3, "output": 4},
    )])
    assert price_for(grid, "codex", "gpt-test-v2", "2026-09-11")["model"] == "gpt-test"
    row = cost_record(_record(), grid)
    assert row["cost_micros"] == 81
    assert row["excess_threshold_cost_micros"] == 30
    assert row["cost_provenance"] == "pricing_derived"


def test_missing_price_preserves_model_and_is_unavailable_not_zero(tmp_path):
    grid = _grid(tmp_path, [_entry()])
    result = aggregate([_record(model="unpriced-model")], grid)
    assert result["unpriced_models"] == [{"host": "codex", "model": "unpriced-model"}]
    assert result["aggregates"][0]["cost_micros"] is None
    assert result["aggregates"][0]["cost_provenance"] == "unavailable"


def test_subscription_price_is_an_explicit_api_equivalent_not_an_invoice(tmp_path):
    grid = _grid(tmp_path, [_entry(billing_mode="subscription")])
    row = cost_record(_record(), grid)
    assert row["billing_mode"] == "subscription"
    assert row["cost_micros"] is not None
    assert row["cost_provenance"] == "pricing_derived"


def test_overlapping_active_aliases_and_incomplete_threshold_rates_fail_closed(tmp_path):
    with pytest.raises(CostAttributionError, match="overlapping"):
        _grid(tmp_path, [_entry(), _entry(model="gpt-other", aliases=["gpt-test-*"])])
    with pytest.raises(CostAttributionError, match="threshold"):
        _grid(tmp_path, [_entry(context_threshold_tokens=10)])


def test_reader_extracts_only_five_counters_and_resolves_codex_session_cwd(tmp_path):
    log = tmp_path / "rollout.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"cwd": "/work/claude-plugins", "session_id": "session-1"}}),
        json.dumps({"type": "turn_context", "payload": {"model": "gpt-test-v2"}}),
        json.dumps({"type": "token_usage_record", "timestamp": "2026-09-11T10:00:00Z",
                    "payload": {"thread_token_usage": {"input_tokens": 3, "cached_input_tokens": 2,
                    "cache_write_input_tokens": 3, "output_tokens": 4, "reasoning_output_tokens": 3},
                    "prompt": "must never be returned"}}),
    ]))
    rows = read_host_log("codex", log, {"youtrack": {"claude-plugins": {"key": "FOUNDRY", "codex_cwd": "/work/claude-plugins"}}})
    assert rows == [{
        "host": "codex", "session_id": "session-1", "model": "gpt-test-v2", "occurred_on": "2026-09-11",
        "tokens": {"input_tokens": 1, "cached_input_tokens": 2,
                   "cache_write_input_tokens": 3, "output_tokens": 4,
                   "reasoning_output_tokens": 3},
        "token_requests": [{"input_tokens": 1, "cached_input_tokens": 2,
                            "cache_write_input_tokens": 3, "output_tokens": 4,
                            "reasoning_output_tokens": 3}],
        "request_ranks": [],
        "request_granularity": False,
        "project": "FOUNDRY", "epic": None, "issue": None,
        "provenance": "host_reported", "plan_usage_percent": "unavailable",
    }]
    assert "prompt" not in json.dumps(rows)


def test_claude_reader_sums_native_cache_and_thinking_subsets(tmp_path):
    folder = tmp_path / "-Users-pato-workspace-claude-plugins"
    folder.mkdir()
    log = folder / "session.jsonl"
    log.write_text(json.dumps({"type": "assistant", "sessionId": "claude-1",
        "timestamp": "2026-09-11T10:00:00Z", "message": {"model": "claude-test",
        "usage": {"input_tokens": 1, "cache_read_input_tokens": 2, "output_tokens": 7,
        "cache_creation": {"ephemeral_5m_input_tokens": 3, "ephemeral_1h_input_tokens": 4},
        "output_tokens_details": {"thinking_tokens": 5}}, "text": "must never return"}}))
    rows = read_host_log("claude", log, {"youtrack": {"claude-plugins": {"key": "FOUNDRY"}}})
    assert rows[0]["tokens"] == {"input_tokens": 1, "cached_input_tokens": 2,
        "cache_write_input_tokens": 7, "output_tokens": 7, "reasoning_output_tokens": 5}
    assert rows[0]["session_id"] == "claude-1"
    assert "text" not in json.dumps(rows)


@pytest.fixture
def claude_four_forms_logs(tmp_path):
    folder = tmp_path / "-Users-pato-workspace-claude-plugins"
    folder.mkdir()
    mixed = folder / "mixed.jsonl"
    all_missing = folder / "all-missing.jsonl"
    complete_usage = {"input_tokens": 1, "cache_read_input_tokens": 2,
                      "cache_creation_input_tokens": 7, "output_tokens": 7,
                      "output_tokens_details": {"thinking_tokens": 5}}
    missing_reasoning = {"input_tokens": 2, "cache_read_input_tokens": 0,
                         "cache_creation_input_tokens": 0, "output_tokens": 2}
    incoherent_reasoning = {"input_tokens": 3, "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0, "output_tokens": 0,
                            "output_tokens_details": {"thinking_tokens": 573}}
    mixed.write_text("\n".join(json.dumps(row) for row in [
        {"type": "assistant", "sessionId": "claude-partial", "timestamp": "2026-09-11T10:00:00Z",
         "message": {"model": "claude-test", "usage": complete_usage, "text": "PRIVATE_COMPLETE"}},
        {"type": "assistant", "sessionId": "claude-partial", "timestamp": "2026-09-11T10:01:00Z",
         "message": {"model": "claude-test", "usage": missing_reasoning, "text": "PRIVATE_MISSING"}},
        {"type": "assistant", "sessionId": "claude-partial", "timestamp": "2026-09-11T10:02:00Z",
         "message": {"model": "claude-test", "usage": incoherent_reasoning, "text": "PRIVATE_INCOHERENT"}},
        {"type": "assistant", "sessionId": "claude-partial", "timestamp": "2026-09-11T10:02:00Z",
         "message": {"model": "<synthetic>", "usage": {"private": "PRIVATE_SYNTHETIC"}}},
    ]))
    all_missing.write_text("\n".join(json.dumps({
        "type": "assistant", "sessionId": "claude-all-missing",
        "timestamp": f"2026-09-11T10:0{rank}:00Z",
        "message": {"model": "claude-test", "usage": missing_reasoning},
    }) for rank in range(2)))
    return mixed, all_missing


def test_claude_four_real_forms_preserve_all_billable_rows(claude_four_forms_logs, tmp_path):
    mixed, _all_missing = claude_four_forms_logs
    diagnostics = {}
    rows = read_host_log("claude", mixed, diagnostics=diagnostics)
    assert len(rows) == 1
    assert rows[0]["tokens"] == {"input_tokens": 6, "cached_input_tokens": 2,
                                  "cache_write_input_tokens": 7, "output_tokens": 9,
                                  "reasoning_output_tokens": "unavailable"}
    assert [request["reasoning_output_tokens"] for request in rows[0]["token_requests"]] == [
        5, "unavailable", "unavailable"]
    assert rows[0]["request_ranks"] == [1, 2, 3]
    assert "position_data_ambiguous" not in rows[0]
    assert diagnostics == {
        "completeness": "unavailable",
        "billable_records": 3,
        "affected_records": [
            {"reason": "missing_reasoning_output_tokens", "count": 1},
            {"reason": "incoherent_reasoning_output_tokens", "count": 1},
        ],
        "excluded_records": [{"reason": "synthetic_model", "count": 1}],
    }
    assert "PRIVATE_" not in json.dumps({"rows": rows, "diagnostics": diagnostics})
    result = aggregate(rows, _grid(tmp_path, [_entry(host="claude", model="claude-test", aliases=[])]))
    assert result["aggregates"][0]["cost_micros"] == 71
    assert result["session_curves"][0]["position_data"] == "available"
    assert result["session_curves"][0]["total_cost_micros"] == 71


def test_claude_all_missing_reasoning_keeps_complete_cost(claude_four_forms_logs, tmp_path):
    _mixed, all_missing = claude_four_forms_logs
    diagnostics = {}
    rows = read_host_log("claude", all_missing, diagnostics=diagnostics)
    assert rows[0]["tokens"]["reasoning_output_tokens"] == "unavailable"
    assert diagnostics == {
        "completeness": "unavailable", "billable_records": 2,
        "affected_records": [{"reason": "missing_reasoning_output_tokens", "count": 2}],
        "excluded_records": [],
    }
    result = aggregate(rows, _grid(tmp_path, [
        _entry(host="claude", model="claude-test", aliases=[])]))
    assert result["aggregates"][0]["cost_micros"] == 24
    assert result["session_curves"][0]["total_cost_micros"] == 24


def test_claude_observed_zero_reasoning_is_distinct_from_unavailable(tmp_path):
    log = tmp_path / "zero-and-missing.jsonl"
    log.write_text("\n".join(json.dumps(row) for row in [
        {"type": "assistant", "sessionId": "zero", "timestamp": "2026-09-11T10:00:00Z",
         "message": {"model": "claude-test", "usage": {"input_tokens": 0,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
             "output_tokens": 0, "output_tokens_details": {"thinking_tokens": 0}}}},
        {"type": "assistant", "sessionId": "zero", "timestamp": "2026-09-11T10:01:00Z",
         "message": {"model": "claude-test", "usage": {"input_tokens": 0,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
             "output_tokens": 0}}},
    ]))
    requests = read_host_log("claude", log)[0]["token_requests"]
    assert [request["reasoning_output_tokens"] for request in requests] == [0, "unavailable"]


def test_synthetic_exclusion_does_not_degrade_real_reasoning_completeness(tmp_path):
    log = tmp_path / "complete-and-synthetic.jsonl"
    log.write_text("\n".join(json.dumps(row) for row in [
        {"type": "assistant", "sessionId": "complete", "timestamp": "2026-09-11T10:00:00Z",
         "message": {"model": "claude-test", "usage": {"input_tokens": 1,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
             "output_tokens": 1, "output_tokens_details": {"thinking_tokens": 0}}}},
        {"type": "assistant", "message": {"model": "<synthetic>"}},
    ]))
    diagnostics = {}
    read_host_log("claude", log, diagnostics=diagnostics)
    assert diagnostics == {
        "completeness": "available", "billable_records": 1,
        "affected_records": [],
        "excluded_records": [{"reason": "synthetic_model", "count": 1}],
    }


@pytest.mark.parametrize(("field", "value", "error"), [
    ("model", None, "incomplete"),
    ("model", 42, "incomplete"),
    ("sessionId", None, "incomplete"),
    ("sessionId", 42, "incomplete"),
    ("timestamp", None, "incomplete"),
    ("timestamp", 42, "incomplete"),
    ("timestamp", "2026-99-99T10:01:00Z", "date is incomplete"),
])
def test_claude_missing_reasoning_row_requires_real_record_identity(tmp_path, field, value, error):
    log = tmp_path / "session.jsonl"
    complete_usage = {"input_tokens": 1, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0, "output_tokens": 1,
                      "output_tokens_details": {"thinking_tokens": 0}}
    missing_reasoning = {"input_tokens": 1, "cache_read_input_tokens": 0,
                         "cache_creation_input_tokens": 0, "output_tokens": 1}
    incomplete_row = {"type": "assistant", "sessionId": "claude-partial",
                      "timestamp": "2026-09-11T10:01:00Z",
                      "message": {"model": "claude-test", "usage": missing_reasoning}}
    if field == "model":
        incomplete_row["message"]["model"] = value
    else:
        incomplete_row[field] = value
    log.write_text("\n".join(json.dumps(row) for row in [
        {"type": "assistant", "sessionId": "claude-partial", "timestamp": "2026-09-11T10:00:00Z",
         "message": {"model": "claude-test", "usage": complete_usage}},
        incomplete_row,
    ]))
    with pytest.raises(CostAttributionError, match=error):
        read_host_log("claude", log)


@pytest.mark.parametrize("rows", [[], [
    {"type": "assistant", "sessionId": "only-synthetic", "message": {"model": "<synthetic>"}},
]])
def test_claude_log_without_exploitable_usage_fails_explicitly(tmp_path, rows):
    log = tmp_path / "session.jsonl"
    log.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(CostAttributionError, match="no exploitable"):
        read_host_log("claude", log)


@pytest.mark.parametrize("usage", [
    {"input_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
     "output_tokens": 1, "output_tokens_details": {"thinking_tokens": -1}},
    {"input_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
     "output_tokens": 1, "output_tokens_details": {"thinking_tokens": True}},
])
def test_claude_reported_invalid_reasoning_remains_fail_closed(tmp_path, usage):
    log = tmp_path / "session.jsonl"
    log.write_text(json.dumps({"type": "assistant", "sessionId": "invalid", "timestamp": "2026-09-11T10:00:00Z",
                               "message": {"model": "claude-test", "usage": usage}}))
    with pytest.raises(CostAttributionError, match="incomplete"):
        read_host_log("claude", log)


def test_claude_cli_merges_per_log_degradation_counts_without_exposing_content(tmp_path, capsys):
    price_grid = tmp_path / "prices.json"
    price_grid.write_text(json.dumps({"schema_version": 1, "currency": "USD", "entries": [
        _entry(host="claude", model="claude-test", aliases=[])]}))
    logs = []
    for number in (1, 2):
        log = tmp_path / f"session-{number}.jsonl"
        log.write_text("\n".join(json.dumps(row) for row in [
            {"type": "assistant", "sessionId": f"usable-{number}", "timestamp": "2026-09-11T10:00:00Z",
             "message": {"model": "claude-test", "usage": {"input_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0, "output_tokens": 0,
             "output_tokens_details": {"thinking_tokens": 0}}}},
            {"type": "assistant", "sessionId": f"partial-{number}", "timestamp": "2026-09-11T10:01:00Z", "message": {
             "model": "claude-test", "usage": {"input_tokens": 1, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0, "output_tokens": 1}, "text": "PRIVATE_CLI"}},
        ]))
        logs.append(log)
    main(["--host", "claude", "--price-grid", str(price_grid),
          "--log", str(logs[0]), "--log", str(logs[1])])
    output = json.loads(capsys.readouterr().out)
    assert output["reader_completeness"] == {
        "completeness": "unavailable", "billable_records": 4,
        "affected_records": [{"reason": "missing_reasoning_output_tokens", "count": 2}],
        "excluded_records": [],
    }
    assert output["aggregates"][0]["cost_micros"] == 12
    assert "PRIVATE_CLI" not in json.dumps(output)


def test_codex_cli_prices_native_cached_input_once(tmp_path, capsys):
    price_grid = tmp_path / "prices.json"
    price_grid.write_text(json.dumps({
        "schema_version": 1, "currency": "USD", "entries": [_entry()]}))
    usage = {"input_tokens": 100, "cached_input_tokens": 80,
             "cache_write_input_tokens": 0, "output_tokens": 0,
             "reasoning_output_tokens": 0}
    log = tmp_path / "rollout.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {
            "session_id": "cached-cli", "model": "gpt-test-v2"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:00Z",
                    "payload": {"type": "token_count", "info": {
                        "last_token_usage": usage}}}),
    ]))

    main(["--host", "codex", "--price-grid", str(price_grid), "--log", str(log)])

    output = json.loads(capsys.readouterr().out)
    # 20 noncached * 2 + 80 cached * 1; the inclusive 100 is not billed again.
    assert output["aggregates"][0]["cost_micros"] == 120
    assert output["session_curves"][0]["total_cost_micros"] == 120


def test_unavailable_reasoning_is_accepted_only_for_claude_with_strict_billing(tmp_path):
    claude_grid = _grid(tmp_path, [
        _entry(host="claude", model="claude-test", aliases=[]),
    ])
    unavailable = {**_record()["tokens"], "reasoning_output_tokens": "unavailable"}
    claude = _record(host="claude", model="claude-test", tokens=unavailable,
                     token_requests=[unavailable])
    assert cost_record(claude, claude_grid)["cost_micros"] == 51

    codex_grid = _grid(tmp_path, [_entry()])
    with pytest.raises(CostAttributionError, match="token requests differ"):
        cost_record(_record(tokens=unavailable), codex_grid)
    with pytest.raises(CostAttributionError, match="token requests differ"):
        cost_record(_record(token_requests=[unavailable]), codex_grid)


def test_unknown_price_does_not_bypass_malformed_billing_counters(tmp_path):
    grid = _grid(tmp_path, [_entry(host="claude", model="claude-test", aliases=[])])
    malformed = {**_record()["tokens"], "input_tokens": True}
    with pytest.raises(CostAttributionError, match="token usage differs"):
        cost_record(_record(host="claude", model="unpriced", tokens=malformed), grid)


def test_unpriced_codex_record_preserves_existing_unavailable_boundary(tmp_path):
    grid = _grid(tmp_path, [_entry()])
    malformed = {**_record()["tokens"], "input_tokens": True}
    row = cost_record(_record(model="unpriced", tokens=malformed), grid)
    assert row["cost_micros"] is None
    assert row["cost_provenance"] == "unavailable"


@pytest.mark.parametrize("usage", [
    {"input_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": True,
     "output_tokens": 1},
    {"input_tokens": 1, "cache_read_input_tokens": 0,
     "cache_creation": {"ephemeral_5m_input_tokens": -1}, "output_tokens": 1},
    {"input_tokens": 1, "cache_read_input_tokens": 0,
     "cache_creation_input_tokens": 0, "output_tokens": False},
])
def test_claude_malformed_billing_counters_remain_fail_closed(tmp_path, usage):
    log = tmp_path / "malformed-billing.jsonl"
    log.write_text(json.dumps({
        "type": "assistant", "sessionId": "malformed", "timestamp": "2026-09-11T10:00:00Z",
        "message": {"model": "claude-test", "usage": usage},
    }))
    with pytest.raises(CostAttributionError, match="token usage is incomplete"):
        read_host_log("claude", log)


def test_reader_accepts_documented_codex_token_count_shape(tmp_path):
    log = tmp_path / "rollout.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"cwd": "/work/claude-plugins", "session_id": "session-2", "model": "gpt-test-v2"}}),
        json.dumps({"type": "token_count", "timestamp": "2026-09-11T10:00:00Z", "info": {"total_token_usage": {"input_tokens": 3, "cached_input_tokens": 2, "cache_write_input_tokens": 3, "output_tokens": 4, "reasoning_output_tokens": 3}}}),
    ]))
    assert read_host_log("codex", log, {"youtrack": {"claude-plugins": {"key": "FOUNDRY", "codex_cwd": "/work/claude-plugins"}}})[0]["model"] == "gpt-test-v2"


def test_reader_uses_nested_event_message_delta_per_model_and_date(tmp_path):
    native_usage = {"input_tokens": 5, "cached_input_tokens": 3,
                    "cache_write_input_tokens": 4, "output_tokens": 5,
                    "reasoning_output_tokens": 1}
    expected_usage = {**native_usage, "input_tokens": 2}
    log = tmp_path / "rollout.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"cwd": "/work/claude-plugins", "session_id": "session-3", "model": "gpt-test-v2"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:00Z", "payload": {"token_count": {"info": {"last_token_usage": native_usage}}}}),
        json.dumps({"type": "turn_context", "payload": {"model": "gpt-test-v3"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-12T10:00:00Z", "payload": {"token_count": {"info": {"last_token_usage": native_usage}}}}),
    ]))
    rows = read_host_log("codex", log, {"youtrack": {"claude-plugins": {"key": "FOUNDRY", "codex_cwd": "/work/claude-plugins"}}})
    assert [(row["model"], row["occurred_on"], row["tokens"]) for row in rows] == [
        ("gpt-test-v2", "2026-09-11", expected_usage),
        ("gpt-test-v3", "2026-09-12", expected_usage)]


def test_reader_rejects_partial_native_usage_instead_of_silent_zero(tmp_path):
    log = tmp_path / "rollout.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"session_id": "session-4", "model": "gpt-test-v2"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:00Z", "payload": {"token_count": {"info": {"last_token_usage": {"input_tokens": 1}}}}}),
    ]))
    with pytest.raises(CostAttributionError, match="incomplete"):
        read_host_log("codex", log)


def test_registered_project_resolution_never_guesses():
    registry = {"youtrack": {"claude-plugins": {"key": "FOUNDRY", "codex_cwd": "/work/claude-plugins", "claude_path_slug": "-Users-pato-workspace-claude-plugins"}}}
    assert resolve_project("claude", claude_path_slug="-Users-pato-workspace-claude-plugins", registry_data=registry) == "FOUNDRY"
    assert resolve_project("claude", claude_path_slug="-Users-pato-workspace-other", registry_data=registry) is None
    assert resolve_project("codex", codex_cwd="/work/claude-plugins", registry_data=registry) == "FOUNDRY"
    assert resolve_project("codex", codex_cwd="/work/not-registered", registry_data=registry) is None


def test_known_models_is_derived_from_the_committed_price_grid():
    assert KNOWN_MODELS == {"haiku-4.5", "sonnet-5", "opus-5", "fable-5", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra"}


def test_aggregate_reports_unpriced_models_from_explicit_discovery_response(tmp_path):
    grid = _grid(tmp_path, [_entry()])
    result = aggregate([], grid, discovered_models=[("codex", "gpt-test-v2"), ("codex", "unpriced")])
    assert result["unpriced_models"] == [{"host": "codex", "model": "unpriced"}]


def test_aggregate_keeps_threshold_tiers_visible_in_every_rollup(tmp_path):
    grid = _grid(tmp_path, [_entry(context_threshold_tokens=12, over_threshold_rates={"input": 5, "cached_input": 1, "cache_write_input": 3, "output": 4})])
    result = aggregate([_record()], grid)
    assert result["aggregates"][0]["context_price_tiers"] == ["long_context"]
    assert result["by"]["issue"][0]["context_price_tiers"] == ["long_context"]


def test_pilot_reconciliation_compares_each_issue_and_rejects_duplicate_records(tmp_path):
    grid = _grid(tmp_path, [_entry()])
    records = []
    lines = ["# results", "| slot | issue_id | issue_type | estimate | host | primary_model | primary_effort | main_loop_cost_usd | subagent_cost_usd | actual_cost_usd |", "|---|---|---|---|---|---|---|---|---|---|"]
    for number in range(13, 23):
        record = _record(issue=f"FOUNDRY-{number}", session_id=f"session-{number}")
        records.append(record)
        lines.append(f"| P-{number} | FOUNDRY-{number} | fix | 1 | codex | gpt-test | low | 0 | 0 | 0.000051 |")
    results = tmp_path / "pilot.md"
    results.write_text("\n".join(lines))
    result = reconcile_pilot_v1(records, grid, results)
    assert result["status"] == "matched"
    assert len(result["by_issue"]) == 10
    quota = {"record_type": "plan_usage_observation", "host": "codex",
             "session_id": "session-13", "occurred_on": "2026-09-11",
             "plan_usage_observations": [{"observation_rank": 1,
                 "observed_on": "2026-09-11", "window": "primary",
                 "plan_usage_percent": 20, "plan_usage_window": {
                     "window_minutes": 300, "resets_at": 1}}],
             "provenance": "host_reported"}
    assert reconcile_pilot_v1([*records, quota], grid, results)["status"] == "matched"
    duplicate = reconcile_pilot_v1([*records, records[0]], grid, results)
    assert duplicate["status"] == "unavailable"
    assert duplicate["explanation"] == "ten distinct explicitly attributed pilot records are required"


def test_cumulative_codex_usage_never_guesses_a_long_context_request(tmp_path):
    grid = _grid(tmp_path, [_entry(context_threshold_tokens=12, over_threshold_rates={"input": 5, "cached_input": 1, "cache_write_input": 3, "output": 4})])
    row = cost_record(_record(token_requests=[_record()["tokens"]], request_granularity=False), grid)
    assert row["cost_micros"] is None
    assert row["context_price_tier"] == "unavailable"


def test_codex_cumulative_usage_uses_cache_normalized_global_increments_across_date_and_model_buckets(tmp_path):
    def cumulative(input_tokens, cached_input_tokens):
        return {"input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "cache_write_input_tokens": 0, "output_tokens": 0,
                "reasoning_output_tokens": 0}

    log = tmp_path / "monotone-multi-date.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {
            "session_id": "monotone", "model": "gpt-test-v2"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:00Z",
                    "payload": {"type": "token_count", "info": {
                        "total_token_usage": cumulative(100, 60)}}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-12T10:00:00Z",
                    "payload": {"type": "token_count", "info": {
                        "total_token_usage": cumulative(150, 90)}}}),
        json.dumps({"type": "turn_context", "payload": {"model": "gpt-test-v3"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-12T11:00:00Z",
                    "payload": {"type": "token_count", "info": {
                        "total_token_usage": cumulative(230, 150)}}}),
    ]))

    rows = read_host_log("codex", log)
    assert [(row["model"], row["occurred_on"], row["tokens"]["input_tokens"],
             row["tokens"]["cached_input_tokens"])
            for row in rows] == [
        ("gpt-test-v2", "2026-09-11", 40, 60),
        ("gpt-test-v2", "2026-09-12", 20, 30),
        ("gpt-test-v3", "2026-09-12", 20, 60),
    ]
    result = aggregate(rows, _grid(tmp_path, [_entry()]))
    assert sum(row["cost_micros"] for row in result["aggregates"]) == 310
    assert sum(row["tokens"]["input_tokens"] for row in rows) == 80
    assert sum(row["tokens"]["cached_input_tokens"] for row in rows) == 150


def test_codex_cumulative_decrease_starts_a_new_cache_normalized_session(tmp_path):
    def cumulative(input_tokens, cached_input_tokens):
        return {"input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "cache_write_input_tokens": 0, "output_tokens": 0,
                "reasoning_output_tokens": 0}

    log = tmp_path / "concatenated-resets.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {
            "session_id": "concatenated", "model": "gpt-test-v2"}}),
        *[json.dumps({"type": "event_msg", "timestamp": timestamp,
                      "payload": {"type": "token_count", "info": {
                          "total_token_usage": cumulative(tokens, cached)}}})
          for timestamp, tokens, cached in [
              ("2026-09-11T10:00:00Z", 100, 60),
              ("2026-09-11T11:00:00Z", 150, 90),
              ("2026-09-12T10:00:00Z", 20, 10),
              ("2026-09-12T11:00:00Z", 60, 45),
          ]],
    ]))

    rows = read_host_log("codex", log)
    assert [(row["occurred_on"], row["tokens"]["input_tokens"],
             row["tokens"]["cached_input_tokens"]) for row in rows] == [
        ("2026-09-11", 60, 90), ("2026-09-12", 15, 45)]
    result = aggregate(rows, _grid(tmp_path, [_entry()]))
    assert result["aggregates"][0]["cost_micros"] == 285


def test_codex_changing_cache_ratio_cannot_turn_an_incoherent_delta_into_a_reset(tmp_path):
    def cumulative(input_tokens, cached_input_tokens):
        return {"input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "cache_write_input_tokens": 0, "output_tokens": 0,
                "reasoning_output_tokens": 0}

    log = tmp_path / "incoherent-cache-ratio.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {
            "session_id": "cache-ratio", "model": "gpt-test-v2"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:00Z",
                    "payload": {"type": "token_count", "info": {
                        "total_token_usage": cumulative(100, 20)}}}),
        # Both native cumulative counters increase, so this is not a reset. Their
        # delta would imply -10 noncached tokens and must fail closed.
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:01:00Z",
                    "payload": {"type": "token_count", "info": {
                        "total_token_usage": cumulative(150, 80)}}}),
    ]))

    with pytest.raises(CostAttributionError, match="cannot separate cached input"):
        read_host_log("codex", log)


def test_codex_request_only_rejects_cached_input_larger_than_inclusive_input(tmp_path):
    usage = {"input_tokens": 4, "cached_input_tokens": 5,
             "cache_write_input_tokens": 0, "output_tokens": 0,
             "reasoning_output_tokens": 0}
    log = tmp_path / "impossible-request.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {
            "session_id": "impossible", "model": "gpt-test-v2"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:00Z",
                    "payload": {"type": "token_count", "info": {
                        "last_token_usage": usage}}}),
    ]))

    with pytest.raises(CostAttributionError, match="cannot separate cached input"):
        read_host_log("codex", log)


def test_codex_request_cache_normalization_drives_context_tiers_and_position_costs(tmp_path):
    def request(input_tokens, cached_input_tokens):
        return {"input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "cache_write_input_tokens": 0, "output_tokens": 1,
                "reasoning_output_tokens": 0}

    log = tmp_path / "cached-requests.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {
            "session_id": "cached-requests", "model": "gpt-test-v2"}}),
        *[json.dumps({"type": "event_msg", "timestamp": timestamp,
                      "payload": {"type": "token_count", "info": {
                          "last_token_usage": request(total, cached)}}})
          for timestamp, total, cached in [
              ("2026-09-11T10:00:00Z", 10, 8),
              ("2026-09-11T10:01:00Z", 12, 9),
          ]],
    ]))

    rows = read_host_log("codex", log)
    assert rows[0]["tokens"] == {
        "input_tokens": 5, "cached_input_tokens": 17,
        "cache_write_input_tokens": 0, "output_tokens": 2,
        "reasoning_output_tokens": 0,
    }
    grid = _grid(tmp_path, [_entry(
        context_threshold_tokens=9,
        over_threshold_rates={"input": 5, "cached_input": 1,
                              "cache_write_input": 3, "output": 4})])
    result = aggregate(rows, grid)
    aggregate_row = result["aggregates"][0]
    assert aggregate_row["cost_micros"] == 50
    assert aggregate_row["excess_threshold_cost_micros"] == 15
    assert aggregate_row["context_price_tiers"] == ["long_context"]
    curve = result["session_curves"][0]
    assert [point["cost_micros"] for point in curve["points"]] == [22, 28]
    assert [point["context_price_tier"] for point in curve["points"]] == [
        "long_context", "long_context"]
    assert curve["total_cost_micros"] == 50


def test_codex_mixed_cumulative_and_request_usage_keeps_every_increment_billable(tmp_path):
    def usage(input_tokens):
        return {"input_tokens": input_tokens, "cached_input_tokens": 0,
                "cache_write_input_tokens": 0, "output_tokens": 0,
                "reasoning_output_tokens": 0}

    log = tmp_path / "mixed-cumulative.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {
            "session_id": "mixed", "model": "gpt-test"}}),
        *[json.dumps({"type": "event_msg", "timestamp": "2026-09-13T10:00:00Z",
                      "payload": {"type": "token_count", "info": {
                          "total_token_usage": usage(total),
                          **({"last_token_usage": usage(request)}
                             if request is not None else {})}}})
          for total, request in [(100, 100), (150, None), (230, 80)]],
    ]))

    rows = read_host_log("codex", log)
    assert sum(row["tokens"]["input_tokens"] for row in rows) == 230
    assert sum(chunk["input_tokens"] for row in rows
               for chunk in row["token_requests"]) == 230
    assert rows[0]["request_granularity"] is False
    assert aggregate(rows, _grid(tmp_path, [_entry()]))["aggregates"][0]["cost_micros"] == 460
    threshold_grid = _grid(tmp_path, [_entry(
        context_threshold_tokens=12,
        over_threshold_rates={"input": 5, "cached_input": 1,
                              "cache_write_input": 3, "output": 4})])
    assert cost_record(rows[0], threshold_grid)["cost_micros"] is None


@pytest.mark.parametrize("last_input,last_cached,last_output,available", [
    (130, 80, 0, True),
    (80, 50, 0, False),
    (140, 90, 0, False),
    (130, 80, 1, False),
])
def test_codex_request_positions_require_the_whole_cumulative_delta(
        tmp_path, last_input, last_cached, last_output, available):
    def usage(input_tokens, cached_tokens, output_tokens=0):
        return {"input_tokens": input_tokens, "cached_input_tokens": cached_tokens,
                "cache_write_input_tokens": 0, "output_tokens": output_tokens,
                "reasoning_output_tokens": 0}

    log = tmp_path / "missing-request-interval.jsonl"
    log.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {
            "session_id": "missing-interval", "model": "gpt-test"}},
        *[{"type": "event_msg", "timestamp": "2026-09-13T10:00:00Z",
           "payload": {"type": "token_count", "info": {
               "total_token_usage": total, "last_token_usage": last}}}
          for total, last in [
              (usage(100, 60), usage(100, 60)),
              (usage(230, 140), usage(last_input, last_cached, last_output)),
          ]],
    ]))

    rows = read_host_log("codex", log)
    assert rows[0]["tokens"]["input_tokens"] == 90
    assert rows[0]["tokens"]["cached_input_tokens"] == 140
    assert [chunk["input_tokens"] for chunk in rows[0]["token_requests"]] == [40, 50]
    assert rows[0]["request_granularity"] is available
    assert rows[0]["request_ranks"] == ([1, 2] if available else [1])
    result = aggregate(rows, _grid(tmp_path, [_entry()]))
    assert result["aggregates"][0]["cost_micros"] == 320
    assert result["session_curves"][0]["position_data"] == (
        "available" if available else "unavailable")
    threshold_grid = _grid(tmp_path, [_entry(
        context_threshold_tokens=120,
        over_threshold_rates={"input": 5, "cached_input": 1,
                              "cache_write_input": 3, "output": 4})])
    threshold_cost = cost_record(rows[0], threshold_grid)
    assert (threshold_cost["cost_micros"] is not None) is available


def test_codex_global_cumulative_stream_wins_over_alternating_thread_totals(tmp_path):
    def usage(input_tokens):
        return {"input_tokens": input_tokens, "cached_input_tokens": 0,
                "cache_write_input_tokens": 0, "output_tokens": 0,
                "reasoning_output_tokens": 0}

    log = tmp_path / "dual-cumulative-streams.jsonl"
    log.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {
            "session_id": "dual", "model": "gpt-test-v2"}},
        {"type": "event_msg", "timestamp": "2026-09-11T10:00:00Z",
         "payload": {"type": "token_count", "info": {
             "last_token_usage": usage(1000), "total_token_usage": usage(1000)}}},
        {"type": "token_usage_record", "timestamp": "2026-09-11T10:01:00Z",
         "thread_token_usage": usage(100), "turn_token_usage": usage(100),
         "usage": usage(100)},
        {"type": "event_msg", "timestamp": "2026-09-12T10:01:00Z",
         "payload": {"type": "token_count", "info": {
             "last_token_usage": usage(100), "total_token_usage": usage(1100)}}},
        {"type": "token_usage_record", "timestamp": "2026-09-12T10:02:00Z",
         "thread_token_usage": usage(150), "turn_token_usage": usage(150),
         "usage": usage(50)},
        {"type": "event_msg", "timestamp": "2026-09-12T10:02:00Z",
         "payload": {"type": "token_count", "info": {
             "last_token_usage": usage(50), "total_token_usage": usage(1150)}}},
    ]))

    rows = read_host_log("codex", log)
    assert [(row["occurred_on"], row["tokens"]["input_tokens"])
            for row in rows] == [("2026-09-11", 1000), ("2026-09-12", 150)]
    assert sum(chunk["input_tokens"] for row in rows
               for chunk in row["token_requests"]) == 1150
    assert sum(cost_record(row, _grid(tmp_path, [_entry()]))["cost_micros"]
               for row in rows) == 2300


def test_codex_request_usage_wins_over_turn_cumulative_fallback(tmp_path):
    def usage(input_tokens):
        return {"input_tokens": input_tokens, "cached_input_tokens": 0,
                "cache_write_input_tokens": 0, "output_tokens": 0,
                "reasoning_output_tokens": 0}

    log = tmp_path / "request-counter-precedence.jsonl"
    log.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {
            "session_id": "request-precedence", "model": "gpt-test-v2"}},
        {"type": "token_usage_record", "timestamp": "2026-09-13T10:00:00Z",
         "turn_token_usage": usage(150), "usage": usage(50)},
    ]))

    row = read_host_log("codex", log)[0]
    assert row["tokens"]["input_tokens"] == 50
    assert row["token_requests"][0]["input_tokens"] == 50
    assert cost_record(row, _grid(tmp_path, [_entry()]))["cost_micros"] == 100


@pytest.mark.parametrize("include_request", [False, True])
def test_codex_turn_only_usage_refuses_an_unattested_session_total(tmp_path, include_request):
    def usage(n):
        return {"input_tokens": n, "cached_input_tokens": 0,
                "cache_write_input_tokens": 0, "output_tokens": 0,
                "reasoning_output_tokens": 0}

    log = tmp_path / "turn-only.jsonl"
    records = [{"type": "session_meta", "payload": {
        "session_id": "turn-only", "model": "gpt-test"}}]
    if include_request:
        records.append({"type": "token_usage_record", "timestamp": "2026-09-13T10:00:00Z",
                        "usage": usage(20)})
    records.extend({"type": "token_usage_record", "timestamp": "2026-09-13T10:00:00Z",
                    "turn_token_usage": usage(n)} for n in (100, 150))
    log.write_text("\n".join(map(json.dumps, records)))
    with pytest.raises(CostAttributionError, match="unavailable: turn counters"):
        main(["--host", "codex", "--log", str(log)])


@pytest.mark.parametrize("source", ["total_token_usage", "thread_token_usage"])
def test_codex_turn_counters_do_not_replace_session_cumulatives(tmp_path, source):
    def usage(n):
        return {"input_tokens": n, "cached_input_tokens": 0,
                "cache_write_input_tokens": 0, "output_tokens": 0,
                "reasoning_output_tokens": 0}

    records = [{"type": "session_meta", "payload": {
        "session_id": "session-cumulative", "model": "gpt-test"}}]
    for n in (100, 150):
        records.append({"type": "token_usage_record", "timestamp": "2026-09-13T10:00:00Z",
                        "turn_token_usage": usage(n),
                        **({"info": {source: usage(n)}} if source == "total_token_usage"
                           else {source: usage(n)})})
    log = tmp_path / "turn-and-session.jsonl"
    log.write_text("\n".join(map(json.dumps, records)))
    rows = read_host_log("codex", log)
    assert rows[0]["tokens"]["input_tokens"] == 150
    assert rows[0]["request_granularity"] is False
    result = aggregate(rows, _grid(tmp_path, [_entry()]))
    assert result["aggregates"][0]["cost_micros"] == 300
    assert result["session_curves"][0]["position_data"] == "unavailable"


@pytest.mark.parametrize("cumulative", [False, True])
def test_codex_request_ranks_follow_each_session_identity(tmp_path, cumulative):
    def usage(n):
        return {"input_tokens": n, "cached_input_tokens": 0,
                "cache_write_input_tokens": 0, "output_tokens": 0,
                "reasoning_output_tokens": 0}

    records = []
    for sid, request, total in [("one", 100, 100), ("two", 200, 200), ("one", 30, 130)]:
        records.extend([
            {"type": "session_meta", "payload": {"session_id": sid, "model": "gpt-test"}},
            {"type": "token_usage_record", "timestamp": "2026-09-13T10:00:00Z",
             "usage": usage(request),
             **({"thread_token_usage": usage(total)} if cumulative else {})},
        ])
    log = tmp_path / "multiple-sessions.jsonl"
    log.write_text("\n".join(map(json.dumps, records)))
    rows = read_host_log("codex", log)
    assert {r["session_id"]: r["request_ranks"] for r in rows} == {"one": [1, 2], "two": [1]}
    result = aggregate(rows, _grid(tmp_path, [_entry()]))
    assert result["aggregates"][0]["cost_micros"] == 660
    assert all(c["position_data"] == "available" for c in result["session_curves"])


def test_malformed_json_fails_closed_without_source_text(tmp_path):
    log = tmp_path / "rollout.jsonl"
    log.write_text("not json")
    with pytest.raises(CostAttributionError, match="malformed JSON"):
        read_host_log("codex", log)


def test_actual_codex_event_envelope_keeps_rank_and_safe_plan_snapshot(tmp_path):
    usage = {"input_tokens": 3, "cached_input_tokens": 2, "cache_write_input_tokens": 3,
             "output_tokens": 4, "reasoning_output_tokens": 3}
    log = tmp_path / "rollout.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"id": "native-1", "cwd": "/work/claude-plugins", "model": "gpt-test-v2"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:00Z", "payload": {
            "type": "token_count", "info": {"last_token_usage": usage},
            "rate_limits": {"plan_type": "secret-plan", "limit_name": "private", "primary": {
                "used_percent": 0, "window_minutes": 300, "resets_at": 1234}}}}),
    ]))
    row = read_host_log("codex", log)[0]
    assert row["request_ranks"] == [1]
    assert row["plan_usage_percent"] == 0
    assert row["plan_usage_window"] == {"window_minutes": 300, "resets_at": 1234}
    assert "secret-plan" not in json.dumps(row) and "private" not in json.dumps(row)


def test_invalid_or_absent_plan_usage_never_excludes_cost(tmp_path):
    grid = _grid(tmp_path, [_entry(rates={"input": .5, "cached_input": 0, "cache_write_input": 0, "output": 0})])
    records = [_record(session_id="with-no-plan", token_requests=[
        {"input_tokens": 1, "cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0},
        {"input_tokens": 1, "cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}],
        request_ranks=[1, 2], plan_usage_percent="unavailable")]
    result = aggregate(records, grid)
    assert result["aggregates"][0]["cost_micros"] == 1
    curve = result["session_curves"][0]
    assert [point["cost_micros"] for point in curve["points"]] == [0, 1]
    assert curve["points"][-1]["cumulative_cost_micros"] == 1
    assert result["plan_usage_by_session"] == [{"host": "codex", "session_id": "with-no-plan",
                                                   "plan_usage_percent": "unavailable", "plan_usage_snapshots": []}]


@pytest.mark.parametrize("percent", [True, 101, float("inf")])
def test_reader_rejects_invalid_optional_plan_percent_without_rejecting_usage(tmp_path, percent):
    usage = {"input_tokens": 1, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
             "output_tokens": 0, "reasoning_output_tokens": 0}
    log = tmp_path / "rollout.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"session_id": "plan-invalid", "model": "gpt-test-v2"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:00Z", "payload": {
            "type": "token_count", "info": {"last_token_usage": usage},
            "rate_limits": {"primary": {"used_percent": percent, "window_minutes": 5, "resets_at": 1}}}}),
    ]))
    row = read_host_log("codex", log)[0]
    assert row["tokens"] == usage
    assert row["plan_usage_percent"] == "unavailable"


def test_position_curves_keep_global_rank_across_model_date_and_hosts(tmp_path):
    grid = _grid(tmp_path, [_entry(), _entry(host="claude", model="claude-test", aliases=["claude-test"])])
    first = _record(session_id="same", token_requests=[_record()["tokens"]], request_ranks=[1])
    second = _record(session_id="same", model="gpt-test-v3", occurred_on="2026-09-12",
                     token_requests=[_record()["tokens"]], request_ranks=[2])
    claude = _record(host="claude", model="claude-test", session_id="same",
                     token_requests=[_record()["tokens"]], request_ranks=[1], plan_usage_percent=77)
    result = aggregate([second, claude, first], grid)
    curves = {(item["host"], item["session_id"]): item for item in result["session_curves"]}
    assert [item["rank"] for item in curves[("codex", "same")]["points"]] == [1, 2]
    assert curves[("claude", "same")]["position_data"] == "available"
    assert result["plan_usage_by_session"][0]["plan_usage_percent"] == "unavailable"


def test_comparison_uses_only_caller_declared_observed_sessions(tmp_path):
    grid = _grid(tmp_path, [_entry()])
    one = _record(session_id="long", token_requests=[_record()["tokens"]], request_ranks=[1])
    two = _record(session_id="short-a", token_requests=[_record()["tokens"]], request_ranks=[1])
    three = _record(session_id="short-b", token_requests=[_record()["tokens"]], request_ranks=[1])
    result = aggregate([one, two, three], grid, session_comparisons={"work-opaque": {
        "long_session": {"host": "codex", "session_id": "long"},
        "short_sessions": [{"host": "codex", "session_id": "short-a"}, {"host": "codex", "session_id": "short-b"}]}})
    comparison = result["session_comparisons"][0]
    assert comparison["work_equivalence"] == "caller_declared"
    assert comparison["short_sessions_cost_micros"] == 2 * comparison["long_session_cost_micros"]


def test_aggregation_output_contains_no_content_or_network_side_effect(monkeypatch, tmp_path):
    grid = _grid(tmp_path, [_entry()])
    import socket
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("network call"))
    record = _record(session_id="clean", prompt="private prompt", response="private response",
                     path="/private/file", token_requests=[_record()["tokens"]], request_ranks=[1])
    first, second = aggregate([record], grid), aggregate([record], grid)
    assert first == second
    rendered = json.dumps(first)
    assert "private prompt" not in rendered and "private response" not in rendered and "/private/file" not in rendered


def test_plan_observations_keep_native_order_and_window_identity(tmp_path):
    usage = {"input_tokens": 1, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
             "output_tokens": 0, "reasoning_output_tokens": 0}
    log = tmp_path / "rollout.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"session_id": "ordered", "model": "gpt-test"}}),
        *[json.dumps({"type": "event_msg", "timestamp": f"2026-09-11T10:00:0{rank}Z", "payload": {
            "type": "token_count", "info": {"last_token_usage": usage}, "rate_limits": {
                "primary": {"used_percent": percent, "window_minutes": 300, "resets_at": 10},
                "secondary": {"used_percent": percent + 1, "window_minutes": 10080, "resets_at": 20}}}})
          for rank, percent in enumerate((10, 20, 30), 1)],
    ]))
    plan = aggregate(read_host_log("codex", log), _grid(tmp_path, [_entry()]))["plan_usage_by_session"][0]
    assert plan["plan_usage_percent"] == 30
    assert [(item["observation_rank"], item["window"]) for item in plan["plan_usage_snapshots"]] == [
        (1, "primary"), (2, "secondary"), (3, "primary"), (4, "secondary"), (5, "primary"), (6, "secondary")]


def test_quota_only_observation_keeps_its_order_after_usage_observation(tmp_path):
    usage = {"input_tokens": 1, "cached_input_tokens": 0,
             "cache_write_input_tokens": 0, "output_tokens": 0,
             "reasoning_output_tokens": 0}
    log = tmp_path / "interleaved-quota.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {
            "session_id": "quota-order", "model": "gpt-test"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:00Z",
                    "payload": {"type": "token_count", "info": {
                        "last_token_usage": usage}, "rate_limits": {"primary": {
                            "used_percent": 10, "window_minutes": 300,
                            "resets_at": 10}}}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:01:00Z",
                    "payload": {"type": "token_count", "info": None,
                        "rate_limits": {"primary": {"used_percent": 20,
                            "window_minutes": 300, "resets_at": 10}}}}),
    ]))

    plan = aggregate(read_host_log("codex", log),
                     _grid(tmp_path, [_entry()]))["plan_usage_by_session"][0]
    assert [(item["observation_rank"], item["plan_usage_percent"])
            for item in plan["plan_usage_snapshots"]] == [(1, 10), (2, 20)]
    assert plan["plan_usage_percent"] == 20


def test_repeated_cumulative_snapshot_makes_position_curve_unavailable(tmp_path):
    usage = {"input_tokens": 1, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
             "output_tokens": 0, "reasoning_output_tokens": 0}
    log = tmp_path / "rollout.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"session_id": "repeated", "model": "gpt-test"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:01Z", "payload": {
            "type": "token_count", "info": {"last_token_usage": usage, "total_token_usage": usage}}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:02Z", "payload": {
            "type": "token_count", "info": {"last_token_usage": usage, "total_token_usage": usage}}}),
    ]))
    result = aggregate(read_host_log("codex", log), _grid(tmp_path, [_entry()]))
    assert result["session_curves"] == [{"host": "codex", "session_id": "repeated",
                                         "position_data": "unavailable", "points": []}]


def test_date_filter_marks_a_partially_selected_session_curve_unavailable(tmp_path):
    grid = _grid(tmp_path, [_entry()])
    first = _record(session_id="filtered", occurred_on="2026-09-11",
                    token_requests=[_record()["tokens"]], request_ranks=[1])
    later = _record(session_id="filtered", occurred_on="2026-09-12",
                    token_requests=[_record()["tokens"]], request_ranks=[2])
    result = aggregate([first, later], grid, end="2026-09-11")
    assert result["session_curves"] == [{"host": "codex", "session_id": "filtered",
                                         "position_data": "unavailable", "points": []}]


def test_quota_only_after_model_and_date_switch_never_creates_cost(tmp_path):
    usage = {"input_tokens": 1, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
             "output_tokens": 0, "reasoning_output_tokens": 0}
    log = tmp_path / "rollout.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {
            "session_id": "quota-after", "model": "gpt-test-v2"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:00Z",
                    "payload": {"type": "token_count", "info": {
                        "last_token_usage": usage, "total_token_usage": usage}}}),
        json.dumps({"type": "turn_context", "payload": {"model": "gpt-test-v3"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-12T10:00:00Z",
                    "payload": {"type": "token_count", "info": None,
                        "rate_limits": {"primary": {"used_percent": 25,
                            "window_minutes": 300, "resets_at": 10}}}}),
    ]))
    rows = read_host_log("codex", log)
    result = aggregate(rows, _grid(tmp_path, [_entry()]))
    assert len([row for row in rows if "tokens" in row]) == 1
    assert result["aggregates"][0]["sessions"] == 1
    assert result["aggregates"][0]["cost_micros"] == 2
    assert result["session_curves"][0]["total_cost_micros"] == 2
    assert result["plan_usage_by_session"][0]["plan_usage_percent"] == 25


@pytest.mark.parametrize("rate_limits", [None, {"primary": {
    "used_percent": True, "window_minutes": 300, "resets_at": 10}}])
def test_missing_or_malformed_quota_only_notification_is_ignored(tmp_path, rate_limits):
    usage = {"input_tokens": 1, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
             "output_tokens": 0, "reasoning_output_tokens": 0}
    payload = {"type": "token_count", "info": None}
    if rate_limits is not None:
        payload["rate_limits"] = rate_limits
    log = tmp_path / "rollout.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {
            "session_id": "quota-invalid", "model": "gpt-test-v2"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:00Z",
                    "payload": {"type": "token_count", "info": {
                        "last_token_usage": usage}}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:01:00Z",
                    "payload": payload}),
    ]))
    rows = read_host_log("codex", log)
    result = aggregate(rows, _grid(tmp_path, [_entry()]))
    assert len(rows) == 1
    assert result["aggregates"][0]["cost_micros"] == 2
    assert result["plan_usage_by_session"][0]["plan_usage_percent"] == "unavailable"


def test_quota_only_before_usage_and_without_any_usage_stays_non_billable(tmp_path):
    quota_event = json.dumps({"type": "event_msg", "timestamp": "2026-09-10T10:00:00Z",
        "payload": {"type": "token_count", "info": None, "rate_limits": {"primary": {
            "used_percent": 15, "window_minutes": 300, "resets_at": 10}}}})
    metadata = json.dumps({"type": "session_meta", "payload": {
        "session_id": "quota-first", "model": "gpt-test-v2"}})
    quota_log = tmp_path / "quota-only.jsonl"
    quota_log.write_text("\n".join([metadata, quota_event]))
    quota_result = aggregate(read_host_log("codex", quota_log), _grid(tmp_path, [_entry()]))
    assert quota_result["aggregates"] == []
    assert quota_result["session_curves"] == []
    assert quota_result["plan_usage_by_session"][0]["plan_usage_percent"] == 15

    usage = {"input_tokens": 1, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
             "output_tokens": 0, "reasoning_output_tokens": 0}
    with_usage = tmp_path / "quota-before-usage.jsonl"
    with_usage.write_text("\n".join([metadata, quota_event, json.dumps({
        "type": "event_msg", "timestamp": "2026-09-11T10:00:00Z", "payload": {
            "type": "token_count", "info": {"last_token_usage": usage}}})]))
    result = aggregate(read_host_log("codex", with_usage), _grid(tmp_path, [_entry()]))
    assert result["aggregates"][0]["sessions"] == 1
    assert result["aggregates"][0]["cost_micros"] == 2
    assert result["plan_usage_by_session"][0]["plan_usage_percent"] == 15


def test_plan_date_filter_uses_observation_date_not_cost_row_date(tmp_path):
    observation = {"observation_rank": 1, "observed_on": "2026-09-12",
                   "window": "primary", "plan_usage_percent": 50,
                   "plan_usage_window": {"window_minutes": 300, "resets_at": 10}}
    record = _record(session_id="dated-plan", token_requests=[_record()["tokens"]],
                     request_ranks=[1], plan_usage_observations=[observation])
    grid = _grid(tmp_path, [_entry()])
    cost_day = aggregate([record], grid, end="2026-09-11")
    assert cost_day["aggregates"][0]["cost_micros"] == 51
    assert cost_day["plan_usage_by_session"][0]["plan_usage_percent"] == "unavailable"
    plan_day = aggregate([record], grid, start="2026-09-12")
    assert plan_day["aggregates"] == []
    assert plan_day["plan_usage_by_session"][0]["plan_usage_percent"] == 50


def test_metadata_only_reader_and_aggregate_boundaries_use_exact_whitelists(tmp_path):
    log = tmp_path / "rollout.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"session_id": "safe-quota"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:00Z",
                    "payload": {"type": "token_count", "info": None,
                        "rate_limits": {"primary": {"used_percent": 5,
                            "window_minutes": 300, "resets_at": 10},
                            "plan_type": "PRIVATE_PLAN"}}}),
    ]))
    row = read_host_log("codex", log)[0]
    assert set(row) == {"record_type", "host", "session_id", "occurred_on",
                        "plan_usage_observations", "provenance"}
    assert set(row["plan_usage_observations"][0]) == {
        "observation_rank", "observed_on", "window", "plan_usage_percent",
        "plan_usage_window"}
    assert "PRIVATE_PLAN" not in json.dumps(row)
    with pytest.raises(CostAttributionError, match="observation record differs"):
        aggregate([{**row, "prompt": "PRIVATE_CONTENT"}], _grid(tmp_path, [_entry()]))


def test_explicitly_malformed_token_counter_still_fails_closed_with_quota(tmp_path):
    log = tmp_path / "rollout.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {
            "session_id": "bad-counter", "model": "gpt-test-v2"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-09-11T10:00:00Z",
                    "payload": {"type": "token_count", "info": {
                        "last_token_usage": None}, "rate_limits": {"primary": {
                            "used_percent": 5, "window_minutes": 300,
                            "resets_at": 10}}}}),
    ]))
    with pytest.raises(CostAttributionError, match="token usage is incomplete"):
        read_host_log("codex", log)


def test_invalid_calendar_date_in_optional_quota_preserves_cost(tmp_path):
    observation = {"observation_rank": 1, "observed_on": "2026-02-30",
                   "window": "primary", "plan_usage_percent": 50,
                   "plan_usage_window": {"window_minutes": 300, "resets_at": 10}}
    grid = _grid(tmp_path, [_entry()])
    result = aggregate([_record(session_id="calendar", plan_usage_observations=[observation])], grid)
    assert result["aggregates"][0]["cost_micros"] == 51
    assert result["plan_usage_by_session"][0]["plan_usage_percent"] == "unavailable"
    log = tmp_path / "calendar.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"session_id": "calendar"}}),
        json.dumps({"type": "event_msg", "timestamp": "2026-02-30T10:00:00Z",
                    "payload": {"type": "token_count", "info": None,
                                "rate_limits": {"primary": {"used_percent": 50,
                                    "window_minutes": 300, "resets_at": 10}}}}),
    ]))
    assert read_host_log("codex", log) == []
