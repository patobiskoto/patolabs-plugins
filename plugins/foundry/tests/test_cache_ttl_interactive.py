"""PAT-133: offline replay of INTERACTIVE host sessions (main conversation + subagent logs), both cache directions.

Synthetic logs only: generic placeholders, no name, no home path, no raw session identifier."""
import datetime as dt
import getpass
import json
import re
import socket
from pathlib import Path

import pytest

from foundry import cache_ttl_replay as ttl
from foundry.cost_breakdown import BreakdownError

DOCS = Path(__file__).resolve().parents[1] / "docs" / "qualification"
T0 = dt.datetime(2026, 10, 7, 12, 0, 0, tzinfo=dt.timezone.utc)
SECRET = "SECRET-PROMPT-/Users/someone/private"
SONNET, OPUS, HAIKU = "claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"


def _personal_patterns():
    """What a committed file must not carry, derived at run time (no name is written in this file)."""
    names = {Path.home().name}
    try:
        names.add(getpass.getuser())
    except (KeyError, OSError, ImportError):
        pass
    patterns = [re.escape(f) for f in ("/Users/", "/home/", "/.claude")]
    patterns += [rf"/{re.escape(n)}(?:/|\"|$)" for n in names if len(n) >= 3]
    return patterns


def _at(seconds, base=T0):
    return (base + dt.timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _rec(mid, at, i=0, r=0, w=0, o=0, model=SONNET, one_hour=False, base=T0):
    """An assistant record. ``w`` is written at 5 minutes unless ``one_hour``."""
    usage = {"input_tokens": i, "cache_read_input_tokens": r, "cache_creation_input_tokens": w, "output_tokens": o,
             "cache_creation": {"ephemeral_1h_input_tokens": w if one_hour else 0,
                                "ephemeral_5m_input_tokens": 0 if one_hour else w}}
    return {"type": "assistant", "timestamp": _at(at, base),
            "message": {"id": mid, "model": model, "usage": usage, "content": [{"type": "text", "text": SECRET}]},
            "cwd": "/Users/someone/work"}


def _write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _session(root, main, subs=(), name="sess-RAW-123", project="proj-RAW"):
    """<root>/<project>/<name>.jsonl and <root>/<project>/<name>/subagents/<file>.jsonl (a name may be nested)."""
    base = root / project / name
    _write(base.with_suffix(".jsonl"), main)
    for file_name, records in subs:
        _write(base / "subagents" / f"{file_name}.jsonl", records)
    return base.with_suffix(".jsonl")


def _sub(gap, new=1000, second_read=1_000_000, third_read=0):
    """Subagent log (5-minute writes): s0 writes 1 M; s1 at +100 reads it; s2 ``gap`` s after s1 re-writes."""
    return [_rec("s0", 0, w=1_000_000), _rec("s1", 100, r=1_000_000, w=1000),
            _rec("s2", 100 + gap, r=third_read, w=second_read + 1000 + new - third_read)]


def _kind(out, kind):
    return out["by_kind"][kind]


# --- layout, reader

def test_subagent_logs_are_found_by_position_only_nested_included(tmp_path):
    main = _session(tmp_path, [_rec("m0", 0, w=10)], [("agent-a", [_rec("a", 0, w=1)]), ("deeper/agent-b", [_rec("b", 0, w=1)])])
    (main.parent / main.stem / "subagents" / "note.txt").write_text(SECRET)
    (main.parent / main.stem / "other.jsonl").write_text(SECRET)
    found = ttl.discover_subagent_logs(main)
    assert [p.name for p in found] == ["agent-a.jsonl", "agent-b.jsonl"]
    assert ttl.discover_subagent_logs(tmp_path / "proj-RAW" / "absent.jsonl") == []


def test_interactive_reader_keeps_the_largest_output_of_a_streamed_request(tmp_path):
    _write(tmp_path / "s.jsonl", [_rec("a", 0, w=10, o=1), _rec("a", 1, w=10, o=40), _rec("a", 2, w=10, o=7)])
    assert ttl.read_host_requests(tmp_path / "s.jsonl", interactive=True)[0]["tokens"]["output"] == 40
    with pytest.raises(BreakdownError, match="duplicate_differs"):  # the PAT-132 mode is unchanged
        ttl.read_host_requests(tmp_path / "s.jsonl")


def test_interactive_reader_refuses_any_other_difference_between_records_of_one_id(tmp_path):
    _write(tmp_path / "s.jsonl", [_rec("a", 0, r=5, w=10), _rec("a", 1, r=6, w=10)])
    with pytest.raises(BreakdownError, match="duplicate_differs"):
        ttl.read_host_requests(tmp_path / "s.jsonl", interactive=True)


def test_a_zeroed_copy_of_a_request_is_ignored_whichever_comes_first(tmp_path):
    zero = _rec("a", 5, o=0)
    _write(tmp_path / "s.jsonl", [_rec("a", 0, i=2, r=900, w=10, o=3), zero])
    _write(tmp_path / "t.jsonl", [zero, _rec("a", 6, i=2, r=900, w=10, o=3)])
    for name in ("s.jsonl", "t.jsonl"):
        (request,) = ttl.read_host_requests(tmp_path / name, interactive=True)
        assert request["tokens"]["read"] == 900 and "ambiguous" not in request


def test_a_request_whose_write_classes_do_not_add_up_is_flagged_not_refused(tmp_path):
    bad = _rec("a", 0, w=10)
    bad["message"]["usage"]["cache_creation_input_tokens"] = 4
    _write(tmp_path / "s.jsonl", [bad, _rec("b", 10, r=10)])
    assert ttl.read_host_requests(tmp_path / "s.jsonl", interactive=True)[0]["ambiguous"] is True
    with pytest.raises(BreakdownError, match="cache_write_split_differs"):
        ttl.read_host_requests(tmp_path / "s.jsonl")


def test_unicode_line_separators_inside_a_json_string_do_not_split_a_record(tmp_path):
    record = _rec("a", 0, w=10)
    record["message"]["content"][0]["text"] = "x\u2028y\u0085z"  # valid raw in JSON; str.splitlines() splits on both
    raw = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    assert "\u2028".encode() in raw and "\u0085".encode() in raw  # really present in the written bytes
    assert len(raw.decode().splitlines()) > 1  # the former reader would have cut the record
    (tmp_path / "s.jsonl").write_bytes(raw)
    assert len(ttl.read_host_requests(tmp_path / "s.jsonl")) == 1


def test_until_ignores_later_records_and_a_partial_last_line(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in [_rec("a", 0, w=10), _rec("b", 100, r=10), _rec("c", 900, r=10)])
                    + '\n{"type": "assist')
    cut = T0 + dt.timedelta(seconds=500)
    assert [q["tokens"]["read"] for q in ttl.read_host_requests(path, cut, interactive=True)] == [0, 10]
    with pytest.raises(BreakdownError, match="log_unreadable"):
        ttl.read_host_requests(path)
    path.write_text('{"type": "assist\n' + json.dumps(_rec("a", 0, w=1)) + "\n")
    with pytest.raises(BreakdownError, match="log_unreadable"):  # only the LAST line is tolerated
        ttl.read_host_requests(path, cut)


# --- direction A: main conversation, observed 1 hour -> simulated 5 minutes

def _main_pair(gap):
    return [_rec("m0", 0, w=1_000_000, one_hour=True), _rec("m1", gap, r=1_000_000, w=1000, one_hour=True)]


@pytest.mark.parametrize("gap,expired", [(300, 0), (301, 1)])
def test_direction_a_applies_the_pat_132_rule_with_a_strict_ttl(tmp_path, gap, expired):
    main = _session(tmp_path, _main_pair(gap))
    out = ttl.analyse_interactive([main])
    view = _kind(out, "main")
    assert view["simulated_direction"] == "1h_to_5m" and view["observed_cache_write_duration"] == "1h"
    # the first request reads nothing: no entry clause; m1 is found expired only above 300 s
    assert view["tokens_by_bound"]["favourable"]["requests_found_expired"] == expired  # requests with a predecessor only
    assert view["tokens_by_bound"]["favourable"]["first_requests_found_expired"] == 1  # the first request (no entry)
    assert view["gaps"]["favourable"]["over_ttl"] == expired


def test_direction_a_costs_are_the_documented_ones(tmp_path):
    out = ttl.analyse_interactive([_session(tmp_path, _main_pair(400))])
    usd = _kind(out, "main")["usd"]
    # real: 1 M at 4 + (1 M read at 0.1 + 1000 at 4) ; simulated: every written or re-read token at 2.5
    assert usd["real_usd"] == pytest.approx(4.104, abs=1e-9)
    for bound in ttl.BOUNDS:
        assert usd["bounds"][bound]["simulated_usd"] == pytest.approx(2.5 + 1_001_000 * 2.5 / 1e6, abs=1e-9)
    assert usd["result"] == "net_loss"


def test_a_model_switch_opens_a_lineage_per_alias_and_the_return_keeps_the_time_away(tmp_path):
    main = [_rec("a0", 0, w=1000, model=SONNET, one_hour=True), _rec("b0", 10, w=1000, model=OPUS, one_hour=True),
            _rec("a1", 20, r=1000, model=SONNET, one_hour=True), _rec("b1", 30, r=1000, model=OPUS, one_hour=True),
            _rec("a2", 1000, r=1000, model=SONNET, one_hour=True)]
    out = ttl.analyse_interactive([_session(tmp_path, main)])
    assert out["sessions"][0]["main_model_aliases"] == [OPUS, SONNET] and out["sessions"][0]["main_alias_lineages"] == 2
    by_alias = {k: v for k, v in out["by_kind_and_model"].items()}
    assert set(by_alias) == {f"main/{OPUS}", f"main/{SONNET}"}
    assert by_alias[f"main/{SONNET}"]["gaps"]["favourable"]["over_ttl"] == 1  # a2 after the 980 s away from a1
    assert by_alias[f"main/{OPUS}"]["gaps"]["favourable"]["over_ttl"] == 0
    assert by_alias[f"main/{SONNET}"]["requests"] == 3 and by_alias[f"main/{OPUS}"]["requests"] == 2


def test_a_first_read_of_a_main_lineage_is_expired_without_an_earlier_session_of_the_same_alias(tmp_path):
    out = ttl.analyse_interactive([_session(tmp_path, [_rec("m0", 0, r=5000, w=10, one_hour=True)])])
    for bound in ttl.BOUNDS:
        assert _kind(out, "main")["tokens_by_bound"][bound]["cache_read_tokens_rewritten_at_5m"] == 5000
    # an earlier session of the same alias ending 100 s before keeps the read under the favourable reading only
    early = _session(tmp_path, [_rec("e0", -200, w=100, one_hour=True), _rec("e1", -100, r=100, one_hour=True)],
                     name="sess-other")
    late = _session(tmp_path, [_rec("m0", 0, r=5000, w=10, one_hour=True)], name="sess-late")
    out = ttl.analyse_interactive([late, early])
    assert out["sessions"][1]["by_kind"]["main"]["tokens_by_bound"]["favourable"]["cache_read_tokens_rewritten_at_5m"] == 0
    assert out["sessions"][1]["by_kind"]["main"]["tokens_by_bound"]["prudent"]["cache_read_tokens_rewritten_at_5m"] == 5000


# --- direction B: subagent logs, observed 5 minutes -> simulated 1 hour

def _b(tmp_path, records):
    main = _session(tmp_path, [_rec("m0", 0, w=10, one_hour=True)], [("agent-x", records)])
    return ttl.analyse_interactive([main])


def test_direction_b_recognises_an_expiry_rewrite_and_gives_both_bounds(tmp_path):
    out = _b(tmp_path, _sub(500))
    sub = _kind(out, "subagent")
    assert sub["simulated_direction"] == "5m_to_1h" and sub["observed_cache_write_duration"] == "5m"
    g = sub["tokens_by_bound"]
    # s2 follows s1 (R_j = 1 M read, W_j = 1000 written): prudent counts what j had read, favourable the whole prefix
    assert g["prudent"] == {"requests_with_recognised_expiry_rewrite": 1, "recovered_read_tokens": 1_000_000}
    assert g["favourable"] == {"requests_with_recognised_expiry_rewrite": 1, "recovered_read_tokens": 1_001_000}
    usd = sub["usd"]
    assert usd["real_usd"] == pytest.approx(2.5 + 0.1025 + 2.505, abs=1e-9)
    assert usd["bounds"]["prudent"]["simulated_usd"] == pytest.approx(4.0 + 0.104 + 0.108, abs=1e-9)
    assert usd["bounds"]["favourable"]["simulated_usd"] == pytest.approx(4.0 + 0.104 + 0.1041, abs=1e-9)
    assert usd["result"] == "net_gain"
    assert usd["bounds"]["prudent"]["delta_usd"] >= usd["bounds"]["favourable"]["delta_usd"]


def test_a_gap_over_an_hour_expires_under_both_durations_so_nothing_is_recovered(tmp_path):
    sub = _kind(_b(tmp_path, _sub(4000)), "subagent")
    assert all(sub["tokens_by_bound"][b]["recovered_read_tokens"] == 0 for b in ttl.BOUNDS)
    assert sub["gaps"]["favourable"]["over_1h"] == 1 and sub["gaps"]["favourable"]["between_ttl_and_1h"] == 0
    assert sub["usd"]["result"] == "net_loss"  # every write now costs 1.6 times more, none is saved


def test_a_gap_within_the_ttl_is_no_expiry_even_with_a_counter_shortfall(tmp_path):
    sub = _kind(_b(tmp_path, _sub(150)), "subagent")
    assert all(sub["tokens_by_bound"][b]["recovered_read_tokens"] == 0 for b in ttl.BOUNDS)


def test_a_write_after_a_gap_without_a_counter_shortfall_is_not_an_expiry_rewrite(tmp_path):
    sub = _kind(_b(tmp_path, [_rec("s0", 0, w=1_000_000), _rec("s1", 100, r=1_000_000, w=1000),
                              _rec("s2", 700, r=1_001_000, w=100)]), "subagent")
    assert all(sub["tokens_by_bound"][b]["recovered_read_tokens"] == 0 for b in ttl.BOUNDS)


def test_the_bounds_disagree_when_the_gap_readings_straddle_the_ttl(tmp_path):
    # s2 at +100+280: lower reading 280 (not over the TTL), upper reading 380 (over it): possible, not certain
    sub = _kind(_b(tmp_path, _sub(280)), "subagent")
    assert sub["tokens_by_bound"]["prudent"]["recovered_read_tokens"] == 0
    assert sub["tokens_by_bound"]["favourable"]["recovered_read_tokens"] == 1_001_000
    assert sub["usd"]["result"] == "undecidable_between_the_bounds"


def test_the_bounds_disagree_when_the_gap_readings_straddle_an_hour(tmp_path):
    # s2 at +100+3550: lower reading 3550 (inside the window), upper reading 3650 (over an hour): possible, not certain
    sub = _kind(_b(tmp_path, _sub(3550)), "subagent")
    assert sub["tokens_by_bound"]["prudent"]["recovered_read_tokens"] == 0
    assert sub["tokens_by_bound"]["favourable"]["recovered_read_tokens"] == 1_001_000
    assert sub["usd"]["result"] == "undecidable_between_the_bounds"


def test_robustness_figures_of_direction_b(tmp_path):
    usd = _kind(_b(tmp_path, _sub(500)), "subagent")["usd"]
    # no recognised re-write real: every write at 1.5 USD/M more than at 5 minutes, nothing saved
    assert usd["delta_usd_if_no_recognised_rewrite_is_real"] == pytest.approx(1_002_000 * 1.5e-6 + 1_000_000 * 1.5e-6 + 1000 * 1.5e-6, abs=1e-6)
    prudent = usd["bounds"]["prudent"]
    assert prudent["recognised_share_of_5m_writes"] == pytest.approx(1_000_000 / 2_003_000, abs=1e-6)
    gain = usd["delta_usd_if_no_recognised_rewrite_is_real"] - prudent["delta_usd"]
    assert prudent["wrong_share_of_recognised_rewrites_that_cancels_the_delta"] == pytest.approx(-prudent["delta_usd"] / gain, abs=1e-5)
    assert usd["cold_start_write_tokens_not_modelled"] == 1_000_000
    assert usd["cold_start_envelope_usd_not_modelled"] == pytest.approx(1_000_000 * (4 - 0.1) / 1e6, abs=1e-9)


def test_until_counts_only_subagent_logs_with_a_record_at_or_before_the_instant(tmp_path):
    main = _session(tmp_path, [_rec("m0", 0, w=10, one_hour=True)], [
        ("agent-ok", _sub(500)),
        ("agent-late", [_rec("l0", 5000, w=10)]),  # created after the instant: not counted, not listed
        ("agent-user-only", [{"type": "user", "timestamp": _at(10), "message": {"content": SECRET}}])])
    out = ttl.analyse_interactive([main], until=T0 + dt.timedelta(seconds=1000))
    assert out["sessions"][0]["subagent_logs_found"] == 2
    assert [(u["kind"], u["model"], u["reason"]) for u in out["lineages_unavailable_in_usd_or_unreadable"]] == [
        ("subagent", "unavailable", "no_request")]
    assert SECRET not in json.dumps(out)
    full = ttl.analyse_interactive([main])
    assert full["sessions"][0]["subagent_logs_found"] == 3


def test_returns_to_an_earlier_alias_are_counted_and_the_day_is_utc(tmp_path):
    main = [_rec("a0", 0, w=10, model=SONNET, one_hour=True), _rec("b0", 10, w=10, model=OPUS, one_hour=True),
            _rec("a1", 20, r=10, model=SONNET, one_hour=True), _rec("b1", 30, r=10, model=OPUS, one_hour=True),
            _rec("b2", 40, r=10, model=OPUS, one_hour=True)]
    out = ttl.analyse_interactive([_session(tmp_path, main)])
    assert out["sessions"][0]["main_returns_to_an_earlier_alias"] == 2  # a1 and b1
    late = _rec("z0", 0, w=10, one_hour=True)
    late["timestamp"] = "2026-10-07T00:30:00+02:00"  # 2026-10-06 in UTC
    assert ttl.analyse_interactive([_session(tmp_path, [late], name="sess-tz")])["sessions"][0]["first_day_utc"] == "2026-10-06"


def test_the_first_request_of_a_subagent_log_is_never_a_recovery_and_is_reported(tmp_path):
    sub = _kind(_b(tmp_path, _sub(500)), "subagent")
    assert sub["first_request_cache"] == {"requests": 1, "cache_read_tokens": 0, "cache_write_tokens": 1_000_000}


def test_a_one_hour_write_already_in_a_subagent_log_is_not_counted_as_recoverable(tmp_path):
    records = [_rec("s0", 0, w=1000, one_hour=True), _rec("s1", 100, r=1000, w=10, one_hour=True),
               _rec("s2", 700, r=0, w=1010, one_hour=True)]
    sub = _kind(_b(tmp_path, records), "subagent")
    assert sub["observed_cache_write_duration"] == "1h"
    assert all(sub["tokens_by_bound"][b]["recovered_read_tokens"] == 0 for b in ttl.BOUNDS)


def test_an_ambiguous_request_is_on_neither_side_and_never_a_reference(tmp_path):
    records = _sub(500)
    records[1]["message"]["usage"]["cache_creation_input_tokens"] = 7  # classes add up to 1000
    sub = _kind(_b(tmp_path, records), "subagent")
    assert sub["requests_ambiguous_counters_on_neither_side"] == 1 and sub["requests"] == 2
    assert all(sub["tokens_by_bound"][b]["recovered_read_tokens"] == 0 for b in ttl.BOUNDS)
    assert sub["tokens"]["read"] == 0


# --- unavailable lineages, comparison like with like

def test_haiku_55_and_unpriced_days_are_unavailable_keep_their_alias_and_leave_both_sides(tmp_path):
    day = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    main = _session(tmp_path, [_rec("m0", 0, w=1000, one_hour=True)], [
        ("agent-haiku", [_rec("h0", 0, w=500, model=HAIKU), _rec("h1", 10, r=500, model=HAIKU)]),
        ("agent-old", [_rec("o0", 0, w=500, base=day), _rec("o1", 10, r=500, base=day)]),
        ("agent-ok", _sub(500))])
    out = ttl.analyse_interactive([main])
    listed = {(u["model"], u["reason"], u["kind"]) for u in out["lineages_unavailable_in_usd_or_unreadable"]}
    assert listed == {(HAIKU, "price_depends_on_prompt_length", "subagent"), (SONNET, "no_price_for_alias_on_day", "subagent")}
    assert all(u["provenance"] == "unavailable" for u in out["lineages_unavailable_in_usd_or_unreadable"])
    sub = _kind(out, "subagent")
    assert sub["requests"] == 3 + 2 + 2  # token view keeps them ...
    assert sub["usd"]["requests"] == 3  # ... USD is the priced lineage only, on both sides
    assert sub["usd"]["real_usd"] == pytest.approx(2.5 + 0.1025 + 2.505, abs=1e-9)
    models = {v["model"]: v for v in out["lineages"]}
    assert models[HAIKU]["usd"] == "unavailable" and models[HAIKU]["usd_reason"] == "price_depends_on_prompt_length"


def test_an_unreadable_subagent_log_is_listed_with_a_fixed_code_and_the_rest_is_replayed(tmp_path):
    main = _session(tmp_path, [_rec("m0", 0, w=10, one_hour=True)], [("agent-ok", _sub(500))])
    bad = main.parent / main.stem / "subagents" / "agent-bad.jsonl"
    bad.write_text("{not json" + SECRET)
    out = ttl.analyse_interactive([main])
    assert out["sessions"][0]["subagent_logs_found"] == 2
    assert [(u["model"], u["reason"]) for u in out["lineages_unavailable_in_usd_or_unreadable"]] == [
        ("unavailable", "log_unreadable")]
    assert SECRET not in json.dumps(out) and _kind(out, "subagent")["requests"] == 3


def test_a_session_without_subagent_logs_or_with_only_synthetic_usage_is_replayed(tmp_path):
    out = ttl.analyse_interactive([_session(tmp_path, [_rec("m0", 0, w=10, one_hour=True), _rec("n0", 5, model="<synthetic>")])])
    assert out["sessions"][0]["subagent_logs_found"] == 0
    assert {u["model"] for u in out["lineages_unavailable_in_usd_or_unreadable"]} == {"<synthetic>"}


# --- naming, privacy, determinism, CLI

def _two_sessions(tmp_path):
    first = _session(tmp_path, [_rec("m0", 0, w=1_000_000, one_hour=True), _rec("m1", 400, r=1_000_000, one_hour=True)],
                     [("agent-a", _sub(500)), ("agent-b", _sub(100))], name="sess-RAW-123", project="proj-RAW-1")
    second = _session(tmp_path, [_rec("n0", 0, w=10, one_hour=True, base=T0 + dt.timedelta(days=1))],
                      [("agent-c", _sub(500))], name="sess-RAW-456", project="proj-RAW-2")
    return first, second


def test_sessions_and_lineages_are_named_by_rank_whatever_the_argument_order(tmp_path):
    first, second = _two_sessions(tmp_path)
    forward = ttl.analyse_interactive([first, second])
    assert forward == ttl.analyse_interactive([second, first])
    assert [s["session"] for s in forward["sessions"]] == ["session-01", "session-02"]
    assert forward["sessions"][0]["first_day_utc"] == "2026-10-07" and forward["sessions"][1]["first_day_utc"] == "2026-10-08"
    assert [v["lineage"] for v in forward["lineages"] if v["lineage"].startswith("session-01")] == [
        "session-01/main-01", "session-01/subagent-01", "session-01/subagent-02"]


def test_output_has_no_raw_identifier_file_name_prompt_or_path_and_never_overwrites(tmp_path, capsys):
    first, second = _two_sessions(tmp_path)
    target = tmp_path / "out.json"
    argv = ["--host-session", str(first), "--host-session", str(second), "--until", "2026-12-31T00:00:00Z"]
    assert ttl.main([*argv, "--out", str(target)]) == 0
    text = target.read_text()
    for forbidden in ("sess-RAW", "proj-RAW", "agent-a", "agent-b", "agent-c", SECRET, "someone", str(tmp_path)):
        assert forbidden not in text
    assert not any(re.search(pattern, text, re.M) for pattern in _personal_patterns())
    data = json.loads(text)
    assert data["schema"] == "foundry.cache-ttl-replay.interactive.v1" and data["sessions_designated"] == 2
    assert data["fields_read_beyond_adr_0015"] and set(data["provenance"]) >= {"token_counters", "usd_real_and_simulated"}
    assert {data["provenance"][k] for k in ("token_counters", "usd_real_and_simulated", "lineage_without_usable_price")} == {
        "host_reported", "pricing_derived", "unavailable"}
    assert ttl.main([*argv, "--out", str(target)]) == 2
    assert "refused: FileExistsError" in capsys.readouterr().err


def test_role_is_not_reported_the_figures_are_by_kind_and_alias(tmp_path):
    data = ttl.analyse_interactive(list(_two_sessions(tmp_path)))
    assert "not derivable" in data["logical_role"] and "role" not in data["by_kind_and_model"]
    assert set(data["by_kind"]) == {"main", "subagent"}
    assert set(data["by_kind_and_model"]) == {f"main/{SONNET}", f"subagent/{SONNET}"}


def test_cli_modes_do_not_mix_and_the_old_mode_keeps_its_errors(tmp_path, capsys):
    main = _session(tmp_path, [_rec("m0", 0, w=10)])
    for argv, text in [(["--host-session", str(main), "--ledger", "x"], "replaces --ledger"),
                       (["--host-session", str(main), "--session-logs-dir", "x"], "replaces --ledger"),
                       (["--ledger", "ledger-x.jsonl", "--session-logs-dir", "x", "--until", "2026-10-07T00:00:00Z"],
                        "--until needs --host-session"),
                       ([], "the following arguments are required: --ledger, --session-logs-dir"),
                       (["--ledger", "x"], "the following arguments are required: --session-logs-dir"),
                       (["--session-logs-dir", "x"], "the following arguments are required: --ledger")]:
        with pytest.raises(SystemExit) as exit_:
            ttl.main(argv)
        assert exit_.value.code == 2 and text in capsys.readouterr().err
    assert ttl.main(["--host-session", str(main), "--until", "not a date"]) == 2
    assert ttl.main(["--host-session", str(tmp_path / "absent.jsonl")]) == 0  # an unreadable main is listed, not fatal


def test_replay_makes_no_network_call_modifies_no_log_and_is_idempotent(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network used")
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    first, second = _two_sessions(tmp_path)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*.jsonl")}
    one = ttl.analyse_interactive([first, second])
    assert ttl.analyse_interactive([first, second]) == one
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*.jsonl")} == before


# --- PAT-134: a subagent lineage observed at 1 hour, replayed towards 5 minutes (direction A)

def _one_hour_sub(gap):
    """A subagent log whose writes are all 1-hour: s0 writes 1 M; s1 at +100 reads it; s2 ``gap`` s later reads it."""
    return [_rec("s0", 0, w=1_000_000, one_hour=True), _rec("s1", 100, r=1_000_000, w=1000, one_hour=True),
            _rec("s2", 100 + gap, r=1_001_000, w=10, one_hour=True)]


def _replayed(tmp_path, records, flag=True):
    main = _session(tmp_path, [_rec("m0", 0, w=10, one_hour=True)], [("agent-x", records)])
    return ttl.analyse_interactive([main], subagent_1h_to_5m=flag)


# Digests of the default (flag off) outputs for two fixed inputs, recorded BEFORE the PAT-134 decision input was added to the
# rows: any change of the default output of an existing input breaks this test.
RECORDED_DEFAULT_OUTPUT_SHA256 = {
    "one_hour": "d841a549e43c622d5cfa109b5b25642100ef6c306b70cd00c0f6894da78f314e",
    "five_minutes": "82985c9a74ee73a826c491be1deb2eb8b193f923abac9ef33be448a3dbfd4e4d"}


def test_the_flag_is_off_by_default_and_changes_nothing_for_existing_inputs(tmp_path):
    import hashlib
    for name, records in (("one_hour", _one_hour_sub(500)), ("five_minutes", _sub(500))):
        main = _session(tmp_path / name, [_rec("m0", 0, w=10, one_hour=True)], [("agent-x", records)])
        default = ttl.analyse_interactive([main])
        digest = hashlib.sha256(json.dumps(default, sort_keys=True, default=str).encode()).hexdigest()
        assert digest == RECORDED_DEFAULT_OUTPUT_SHA256[name]
        assert "subagent_1h_to_5m" not in default and "subagent_1h" not in default["by_kind"]
    five_minutes = _replayed(tmp_path / "five", _sub(500))  # a 5-minute subagent stays in direction B with the flag
    assert set(five_minutes["by_kind"]) == {"main", "subagent"} and five_minutes["subagent_1h_to_5m"]["lineages_replayed"] == 0
    assert _kind(five_minutes, "subagent")["simulated_direction"] == "5m_to_1h"


@pytest.mark.parametrize("gap,expired", [(300, 0), (301, 1)])
def test_a_one_hour_subagent_lineage_gets_the_main_conversation_rule_with_a_strict_ttl(tmp_path, gap, expired):
    out = _replayed(tmp_path, _one_hour_sub(gap))
    view = _kind(out, "subagent_1h")
    assert view["simulated_direction"] == "1h_to_5m" and view["observed_cache_write_duration"] == "1h"
    assert out["subagent_1h_to_5m"]["lineages_replayed"] == 1
    assert view["tokens_by_bound"]["favourable"]["requests_found_expired"] == expired
    assert view["gaps"]["favourable"]["over_ttl"] == expired
    assert out["lineages"][-1]["kind"] == "subagent_1h" and out["lineages"][-1]["lineage"].endswith("/subagent-01")


def test_the_subagent_1h_replay_is_the_same_function_as_the_main_one(tmp_path):
    """Same requests, same costs and same expiry counts whether the log is read as a main or as a subagent lineage."""
    sub = _kind(_replayed(tmp_path / "a", _one_hour_sub(400)), "subagent_1h")
    main = _kind(ttl.analyse_interactive([_session(tmp_path / "b", _one_hour_sub(400))]), "main")
    sub["usd"].pop("entry_reads_not_expired")  # the PAT-134 decision input, an addition of the subagent_1h group
    for key in ("tokens", "usd", "gaps", "tokens_by_bound", "requests"):
        assert sub[key] == main[key], key


def test_a_one_hour_subagent_is_priced_documented_way_and_split_from_five_minute_ones(tmp_path):
    main = _session(tmp_path, [_rec("m0", 0, w=10, one_hour=True)],
                    [("agent-a", _one_hour_sub(400)), ("agent-b", _sub(500))])
    out = ttl.analyse_interactive([main], subagent_1h_to_5m=True)
    assert set(out["by_kind"]) == {"main", "subagent", "subagent_1h"}
    assert _kind(out, "subagent")["simulated_direction"] == "5m_to_1h"
    assert _kind(out, "subagent_1h")["usd"]["result"] == "net_loss"  # 1 h written, then everything re-written at 5 min
    assert out["subagent_1h_to_5m"]["lineages_replayed"] == 1
    assert set(out["by_kind_and_model"]) == {f"main/{SONNET}", f"subagent/{SONNET}", f"subagent_1h/{SONNET}"}


def test_a_mixed_or_cacheless_subagent_lineage_is_not_replayed_towards_5_minutes(tmp_path):
    mixed = [_rec("s0", 0, w=1000, one_hour=True), _rec("s1", 100, r=1000, w=10)]
    none = [_rec("s0", 0, i=5, o=5)]
    assert not ttl.observed_one_hour_only([]) and not ttl.observed_one_hour_only(
        ttl.read_host_requests(_session(tmp_path / "m", mixed), interactive=True))
    assert _replayed(tmp_path / "x", none)["subagent_1h_to_5m"]["lineages_replayed"] == 0
    assert _replayed(tmp_path / "y", mixed)["subagent_1h_to_5m"]["lineages_replayed"] == 0


def test_cli_accepts_the_flag_only_with_host_session_and_writes_the_group(tmp_path, capsys):
    main = _session(tmp_path, [_rec("m0", 0, w=10, one_hour=True)], [("agent-x", _one_hour_sub(400))])
    with pytest.raises(SystemExit) as exit_:
        ttl.main(["--ledger", "ledger-x.jsonl", "--session-logs-dir", "x", "--subagent-1h-to-5m"])
    assert exit_.value.code == 2 and "--subagent-1h-to-5m needs --host-session" in capsys.readouterr().err
    target = tmp_path / "out.json"
    assert ttl.main(["--host-session", str(main), "--subagent-1h-to-5m", "--out", str(target)]) == 0
    assert "subagent_1h" in json.loads(target.read_text())["by_kind"]
    assert not any(re.search(pattern, target.read_text(), re.M) for pattern in _personal_patterns())


# --- PAT-134: the rollback rule, read from a replay output

def _sessions(tmp_path, per_session):
    """One session per entry; each entry is a list of subagent logs (record lists)."""
    mains = [_session(tmp_path, [_rec("m0", 0, w=10, one_hour=True)],
                      [(f"agent-{k}", records) for k, records in enumerate(logs)], name=f"s{n}", project=f"p{n}")
             for n, logs in enumerate(per_session)]
    return ttl.analyse_interactive(mains, subagent_1h_to_5m=True)


def _decide(replay):
    """The rule for the Sonnet 5.5 model, named explicitly: these tests hold whatever ``ROLLBACK_MODEL`` currently is."""
    return ttl.rollback_decision(replay, SONNET)


def _three_sessions(tmp_path, records, count=3):
    return _sessions(tmp_path, [[records]] * count)


def _straddling():
    """L = 250 s (not expired) and U = 350 s (expired) at the third request: the bounds disagree."""
    return [_rec("s0", 0, w=1_000_000, one_hour=True), _rec("s1", 100, r=1_000_000, w=1000, one_hour=True),
            _rec("s2", 350, r=1_001_000, w=10, one_hour=True)]


def _first_request_reads_a_prefix():
    """The first request reads a prefix written by a sibling (not observable); nothing else is a 5-minute expiry."""
    return [_rec("s0", 0, r=1_000_000, w=1000, one_hour=True), _rec("s1", 100, r=1_001_000, w=10, one_hour=True)]


@pytest.mark.parametrize("records,decision,result", [
    (_one_hour_sub(400), "keep", "net_loss"),               # 5 minutes would have cost more under both bounds
    (_one_hour_sub(200), "roll_back", "net_gain"),          # no expiry at 5 minutes: the 1-hour write premium is lost
    (_straddling(), "roll_back", "undecidable_between_the_bounds"),
    (_first_request_reads_a_prefix(), "roll_back", "net_loss"),  # the repricing of the first request ALONE makes it a loss
])
def test_the_rollback_rule_reads_the_quantity_without_first_request_repricing(tmp_path, records, decision, result):
    out = _three_sessions(tmp_path, records)
    usd = _kind(out, "subagent_1h")["usd"]
    kept = usd["entry_reads_not_expired"]
    assert usd["result"] == result
    # never above the replay's own delta, on each bound; exact strings agree with the rounded figures
    for bound in ttl.BOUNDS:
        assert float(kept["delta_usd_exact"][bound]) <= usd["bounds"][bound]["delta_usd"] + 1e-6
        assert kept["delta_usd"][bound] == pytest.approx(float(kept["delta_usd_exact"][bound]), abs=1e-6)
    verdict = _decide(out)
    assert verdict["decision"] == decision and verdict["contributing_sessions"] == 3
    if verdict["decision"] == "keep":
        assert usd["result"] == "net_loss"  # keep implies net_loss, not the converse


def test_the_first_request_repricing_alone_flips_the_sign(tmp_path):
    usd = _kind(_three_sessions(tmp_path, _first_request_reads_a_prefix()), "subagent_1h")["usd"]
    assert all(usd["bounds"][b]["delta_usd"] > 0 for b in ttl.BOUNDS)  # the replay's own delta says the 1 hour paid
    assert all(float(usd["entry_reads_not_expired"]["delta_usd_exact"][b]) < 0 for b in ttl.BOUNDS)


def test_the_floor_counts_contributing_sessions_not_designated_ones(tmp_path):
    two_of_three = _sessions(tmp_path / "a", [[_one_hour_sub(400)], [_one_hour_sub(400)], [_sub(500)]])
    assert _decide(two_of_three) == {
        "decision": "unknown", "reason": "fewer_contributing_sessions_than_the_minimum", "contributing_sessions": 2}
    one_session_many_logs = _sessions(tmp_path / "b", [[_one_hour_sub(400)] * 3, [_sub(500)], [_sub(500)]])
    assert _decide(one_session_many_logs)["decision"] == "unknown"
    assert _decide(_three_sessions(tmp_path / "c", _one_hour_sub(400), count=2))["reason"] == \
        "fewer_designated_sessions_than_the_minimum"


def test_an_unpriced_lineage_observed_at_1h_makes_the_decision_unknown(tmp_path):
    day_before_the_grid = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
    unpriced = [_rec(f"u{n}", 100 * n, w=1000, one_hour=True, base=day_before_the_grid) for n in range(2)]
    out = _sessions(tmp_path, [[_one_hour_sub(400)], [_one_hour_sub(400)], [_one_hour_sub(400), unpriced]])
    assert _decide(out) == {"decision": "unknown", "reason": "a_lineage_observed_at_1h_is_not_priced"}


def test_an_unmodified_profile_observed_at_1h_makes_the_decision_unknown(tmp_path):
    other = [_rec("o0", 0, w=1000, one_hour=True, model=OPUS), _rec("o1", 100, r=1000, w=10, one_hour=True, model=OPUS)]
    out = _sessions(tmp_path, [[_one_hour_sub(400)], [_one_hour_sub(400)], [_one_hour_sub(400), other]])
    assert _decide(out) == {"decision": "unknown", "reason": "a_lineage_of_another_model_is_observed_at_1h"}


def test_the_rollback_rule_is_unknown_without_the_option_and_rolls_back_when_the_field_has_no_effect(tmp_path):
    plain = ttl.analyse_interactive([_session(tmp_path / "b", [_rec("m0", 0, w=10, one_hour=True)])])
    assert _decide(plain) == {"decision": "unknown", "reason": "replay_not_made_with_subagent_1h_to_5m"}
    assert _decide(_three_sessions(tmp_path / "c", _sub(500))) == {
        "decision": "roll_back", "reason": "no_lineage_of_the_model_observed_at_1h"}


def test_the_assumed_asymmetry_no_sonnet_subagent_at_all_rolls_back_while_two_contributing_sessions_are_unknown(tmp_path):
    """Stated in the PAT-134 page as assumed: sessions that ran no subagent of the model give ``roll_back`` too."""
    no_subagent_of_the_model = _sessions(tmp_path / "a", [[]] * 3)
    assert _decide(no_subagent_of_the_model) == {"decision": "roll_back", "reason": "no_lineage_of_the_model_observed_at_1h"}
    two = _sessions(tmp_path / "b", [[_one_hour_sub(400)], [_one_hour_sub(400)], []])
    assert _decide(two)["decision"] == "unknown"


def test_no_model_carrying_the_field_makes_the_decision_unknown_and_the_default_model_is_the_declared_one(tmp_path):
    out = _three_sessions(tmp_path, _one_hour_sub(400))
    assert ttl.rollback_decision(out, None) == {"decision": "unknown", "reason": "no_model_carries_the_field"}
    assert ttl.rollback_decision(out) == ttl.rollback_decision(out, ttl.ROLLBACK_MODEL)  # None once rolled back
    assert ttl.ROLLBACK_MODEL in (SONNET, None)


def test_the_decision_uses_the_unrounded_quantity_and_the_smaller_bound():
    lineages = [{"lineage": f"session-0{n}/subagent-01", "kind": "subagent_1h", "model": SONNET,
                 "usd": {"real_usd": 1.0}} for n in range(1, 4)]

    def replay(prudent, favourable):
        return {"subagent_1h_to_5m": {}, "sessions": [{}] * 3, "lineages": lineages, "by_kind_and_model": {
            f"subagent_1h/{SONNET}": {"usd": {"entry_reads_not_expired": {
                "delta_usd": {"prudent": 0.0, "favourable": 0.0},
                "delta_usd_exact": {"prudent": prudent, "favourable": favourable}}}}}}
    assert _decide(replay("0.0000001", "0.0000002"))["decision"] == "keep"  # 0 < delta < 5e-7: rounds to 0.0
    assert _decide(replay("0.5", "0")) ["decision"] == "roll_back"
    assert _decide(replay("-0.0000001", "0.5"))["decision"] == "roll_back"  # the smaller bound decides


# --- committed evidence

def test_committed_interactive_aggregates_are_clean_and_consistent():
    text = (DOCS / "pat-133-cache-ttl-interactive-v1.json").read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["schema"] == "foundry.cache-ttl-replay.interactive.v1"
    assert data["sessions_designated"] >= 3 and len(data["sessions"]) == data["sessions_designated"]
    assert not any(re.search(pattern, text, re.M) for pattern in _personal_patterns())
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)  # a raw session id
    assert "session_id" not in text and ".jsonl" not in text and not re.search(r"(?<![a-z])agent-", text)
    assert data["until"] and data["fields_read_beyond_adr_0015"]
    lineages = [v for v in data["lineages"]]
    classes = ("input", "read", "write_1h", "write_5m", "output")

    def recompute(rows):
        out = {"requests": sum(v["requests"] for v in rows), "tokens": {c: sum(v["tokens"][c] for v in rows) for c in classes}}
        priced = [v for v in rows if v["usd"] != "unavailable"]
        out["priced"] = {"real": sum(v["usd"]["real_usd"] for v in priced),
                         **{b: sum(v["usd"]["bounds"][b]["simulated_usd"] for v in priced) for b in ttl.BOUNDS}}
        for b in ttl.BOUNDS:
            for key in sorted({k for v in rows for k in v["tokens_by_bound"][b]}):
                out[(b, key)] = sum(v["tokens_by_bound"][b].get(key, 0) for v in rows)
        return out

    def check(view, rows):
        want = recompute(rows)
        assert view["requests"] == want["requests"] and view["tokens"] == want["tokens"]
        for b in ttl.BOUNDS:
            for key, value in view["tokens_by_bound"][b].items():
                if (b, key) in want and key not in ("first_requests_found_expired", "requests_found_expired"):
                    assert value == want[(b, key)]
        if view["usd"] == "unavailable":
            assert all(v["usd"] == "unavailable" for v in rows)
            return
        assert view["usd"]["real_usd"] == pytest.approx(want["priced"]["real"], abs=1e-4)
        for b in ttl.BOUNDS:
            assert view["usd"]["bounds"][b]["simulated_usd"] == pytest.approx(want["priced"][b], abs=1e-4)

    for kind, view in data["by_kind"].items():
        check(view, [v for v in lineages if v["kind"] == kind])
        prudent, favourable = (view["usd"]["bounds"][b]["delta_usd"] for b in ttl.BOUNDS)
        assert prudent >= favourable
        assert view["usd"]["result"] in {"net_gain", "net_loss", "undecidable_between_the_bounds"}
    for key, view in data["by_kind_and_model"].items():
        kind, alias = key.split("/", 1)
        check(view, [v for v in lineages if (v["kind"], v["model"]) == (kind, alias)])
    assert sum(s["by_kind"][k]["requests"] for s in data["sessions"] for k in s["by_kind"]) == sum(
        v["requests"] for v in data["by_kind"].values())
    assert all(u["provenance"] == "unavailable" for u in data["lineages_unavailable_in_usd_or_unreadable"])
    assert all(v["model"] for v in data["lineages"]) and all(
        u["model"] for u in data["lineages_unavailable_in_usd_or_unreadable"])
