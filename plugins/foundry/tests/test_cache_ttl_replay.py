"""PAT-132: offline replay of host session logs, 1-hour cache against a simulated 5-minute cache (synthetic logs)."""
import datetime as dt
import getpass
import json
import re
import socket
from decimal import Decimal
from pathlib import Path

import pytest

from foundry import cache_ttl_replay as ttl
from foundry.cost_breakdown import BreakdownError

DOCS = Path(__file__).resolve().parents[1] / "docs" / "qualification"
T0 = dt.datetime(2026, 10, 7, 12, 0, 0, tzinfo=dt.timezone.utc)
def _personal_strings():
    """What a committed file must not carry, derived at run time (no name is written in this file)."""
    names = {Path.home().name, getpass.getuser()}
    return ("/Users/", "/home/", "/.claude", *(n for n in names if len(n) >= 4))


SECRET = "SECRET-PROMPT-/Users/someone/private"


def _rec(mid, at, i=0, r=0, w=0, o=0, model="claude-sonnet-5-5", w5=0):
    usage = {"input_tokens": i, "cache_read_input_tokens": r, "cache_creation_input_tokens": w, "output_tokens": o,
             "cache_creation": {"ephemeral_1h_input_tokens": w - w5, "ephemeral_5m_input_tokens": w5}}
    return {"type": "assistant", "timestamp": (T0 + dt.timedelta(seconds=at)).isoformat().replace("+00:00", "Z"),
            "message": {"id": mid, "model": model, "usage": usage,
                        "content": [{"type": "text", "text": SECRET}]}, "cwd": "/Users/someone/work"}


def _log(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _campaign(tmp_path, sessions, name="c1"):
    """sessions: list of (role, session id, records, ledger premium tokens or None for the true total)."""
    events = []
    for role, sid, records, premium in sessions:
        _log(tmp_path / "logs" / "proj" / f"{sid}.jsonl", records)
        seen = {}
        for r in (r for r in records if r["type"] == "assistant"):
            u = r["message"]["usage"]
            seen[r["message"]["id"]] = sum(u[k] for k in ("input_tokens", "cache_read_input_tokens",
                                                            "cache_creation_input_tokens", "output_tokens"))
        events.append({"kind": "cloud_started", "dry_run": False, "role": role, "session_id": sid})
        events.append({"kind": "settled", "session_id": sid, "premium_tokens": sum(seen.values()) if premium is None else premium})
    ledger = tmp_path / f"ledger-{name}.jsonl"
    ledger.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return ledger, tmp_path / "logs"


def _four(gap2, gap3):
    """Four requests; the instants are first record times; each response lasts 10 s (last record)."""
    t1 = 10
    t2 = t1 + gap2
    t3 = t2 + gap3
    return [_rec("m0", 0, i=10, w=1000, o=5), _rec("m0", 10, i=10, w=1000, o=5),
            _rec("m1", t1, r=1000, w=100, o=5), _rec("m2", t2, r=1100, w=100, o=5), _rec("m3", t3, r=1200, w=100, o=5)]


# --- reader

def test_reader_counts_a_request_once_and_keeps_only_permitted_fields(tmp_path):
    _log(tmp_path / "s.jsonl", _four(20, 20))
    requests = ttl.read_host_requests(tmp_path / "s.jsonl")
    assert len(requests) == 4
    assert set(requests[0]) == {"model", "first", "last", "tokens", "before"}
    assert set(requests[1]) == {"model", "first", "last", "tokens"}
    assert requests[0]["first"] != requests[0]["last"]
    assert SECRET not in json.dumps(requests, default=str)


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r[1]["message"]["usage"].pop("cache_read_input_tokens"), "counter_absent"),
    (lambda r: r[1]["message"]["usage"]["cache_creation"].update(ephemeral_1h_input_tokens=1), "cache_write_split_differs"),
    (lambda r: r[1]["message"]["usage"].update(output_tokens=6), "duplicate_differs"),
    (lambda r: r[2].update(timestamp="not a date"), "timestamp_unreadable"),
    (lambda r: r[2].update(timestamp="2026-10-07T12:00:00"), "timestamp_unreadable"),
    (lambda r: r[2]["message"].pop("id"), "request_identity_absent"),
])
def test_reader_refuses_an_unusable_log_with_a_fixed_code(tmp_path, mutate, code):
    records = _four(20, 20)
    mutate(records)
    _log(tmp_path / "s.jsonl", records)
    with pytest.raises(BreakdownError, match=code):
        ttl.read_host_requests(tmp_path / "s.jsonl")


def test_reader_refuses_unordered_timestamps_and_garbage(tmp_path):
    _log(tmp_path / "s.jsonl", [_rec("a", 100, w=1), _rec("b", 5, w=1)])
    with pytest.raises(BreakdownError, match="timestamps_unordered"):
        ttl.read_host_requests(tmp_path / "s.jsonl")
    (tmp_path / "g.jsonl").write_text("{not json" + SECRET)
    with pytest.raises(BreakdownError) as err:
        ttl.read_host_requests(tmp_path / "g.jsonl")
    assert str(err.value) == "log_unreadable"


# --- the rule

def _gaps(tmp_path, records, bound):
    _log(tmp_path / "g.jsonl", records)
    return ttl.gaps(ttl.read_host_requests(tmp_path / "g.jsonl"), bound)


def test_prudent_and_favourable_gaps_are_derived_as_documented(tmp_path):
    # first records at 0, 10, 10+a, 10+a+b ; last record of m0 at 10 (duplicate), of the others at their own instant
    records = _four(100, 50)
    assert _gaps(tmp_path, records, "favourable") == [None, 0.0, 100.0, 50.0]  # f_i - e_(i-1)
    assert _gaps(tmp_path, records, "prudent") == [None, 10.0, 100.0, 150.0]  # f_1-f_0 ; f_i - e_(i-2)


def test_expired_request_rewrites_what_it_read_at_the_5_minute_price():
    rates = {"input": Decimal(2), "output": Decimal(10), "cache_read": Decimal("0.1"), "cache_write_5m": Decimal("2.5"),
             "cache_write_1h": Decimal(4)}
    tokens = {"input": 1_000_000, "read": 1_000_000, "write": 1_000_000, "write_1h": 1_000_000, "write_5m": 0,
              "output": 1_000_000}
    real, kept = ttl.request_costs(tokens, rates, False)
    _, lost = ttl.request_costs(tokens, rates, True)
    assert real == Decimal("2") + Decimal("0.1") + Decimal("4") + Decimal("10")
    assert kept == Decimal("2") + Decimal("0.1") + Decimal("2.5") + Decimal("10")
    assert lost == Decimal("2") + Decimal("2.5") * 2 + Decimal("10")


def test_a_gap_just_over_the_ttl_expires_only_under_the_reading_that_sees_it(tmp_path):
    # 3rd request: both readings give exactly 300 (not expired, the TTL is strict); 4th: favourable 20, prudent 320
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-1", _four(300, 20), None)])
    out = ttl.analyse([ledger], logs)
    total = out["campaigns"]["c1"]["total"]
    assert total["bounds"]["favourable"]["requests_over_ttl"] == 0
    assert total["bounds"]["prudent"]["requests_over_ttl"] == 1
    assert total["bounds"]["prudent"]["simulated_5m_usd"] > total["bounds"]["favourable"]["simulated_5m_usd"]
    assert total["bounds"]["prudent"]["cache_read_tokens_on_requests_over_ttl"] == 1200
    assert total["bounds"]["prudent"]["max_gap_seconds"] == 320.0


@pytest.mark.parametrize("gap,expired", [(300, 0), (301, 1)])
def test_the_ttl_is_strict_through_the_public_path(tmp_path, gap, expired):
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-1", _four(gap, 20), None)])
    fav = ttl.analyse([ledger], logs)["campaigns"]["c1"]["total"]["bounds"]["favourable"]
    assert fav["requests_over_ttl"] == expired
    assert fav["max_gap_seconds"] == float(gap)


def _before(at):
    return {"type": "user", "timestamp": (T0 + dt.timedelta(seconds=at)).isoformat().replace("+00:00", "Z"),
            "message": {"content": SECRET}}


def test_prudent_reference_of_the_second_request_is_the_record_before_the_first_one(tmp_path):
    records = [_before(-40), *_four(100, 50)]
    _log(tmp_path / "s.jsonl", records)
    requests = ttl.read_host_requests(tmp_path / "s.jsonl")
    assert requests[0]["before"] == T0 - dt.timedelta(seconds=40)
    assert SECRET not in json.dumps(requests, default=str)
    assert ttl.gaps(requests, "prudent")[1] == 50.0  # f_1 = 10 minus the record at -40, not f_1 - f_0 = 10
    assert ttl.gaps(requests, "favourable")[1] == 0.0
    # without such a record the former value is kept
    _log(tmp_path / "t.jsonl", _four(100, 50))
    assert ttl.gaps(ttl.read_host_requests(tmp_path / "t.jsonl"), "prudent")[1] == 10.0


def test_the_prudent_reference_can_push_the_second_request_over_the_ttl(tmp_path):
    records = [_before(-295), _rec("m0", 0, w=1000), _rec("m1", 10, r=1000)]
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-1", records, None)])
    bounds = ttl.analyse([ledger], logs)["campaigns"]["c1"]["total"]["bounds"]
    assert bounds["prudent"]["requests_over_ttl"] == 1 and bounds["favourable"]["requests_over_ttl"] == 0
    assert bounds["prudent"]["gaps_changed_by_second_correction"] == 1


def test_a_request_without_cache_activity_does_not_refresh_the_entry(tmp_path):
    # m1 reads nothing and writes nothing: m2 is compared with m0, not with m1
    records = [_rec("m0", 0, w=1000), _rec("m1", 200, i=5), _rec("m2", 400, r=1000)]
    assert _gaps(tmp_path, records, "favourable") == [None, 200.0, 400.0]
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-1", records, None)])
    total = ttl.analyse([ledger], logs)["campaigns"]["c1"]["total"]
    assert total["requests_without_cache_read_or_write"] == 1
    assert total["bounds"]["favourable"]["requests_over_ttl"] == 1


def test_a_session_with_several_model_aliases_is_listed_and_excluded(tmp_path):
    records = [_rec("m0", 0, w=1000), _rec("m1", 10, r=1000, model="claude-opus-5-5")]
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-1", records, None)])
    out = ttl.analyse([ledger], logs)["campaigns"]["c1"]
    assert out["sessions_replayed"] == 0 and out["sessions_excluded"][0]["reason"] == "several_models"


def test_the_split_of_the_gap_between_the_bounds_adds_up(tmp_path):
    a, b = _pair(500)
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-a", a, None), ("reviewer", "sid-b", b, None)])
    total = ttl.analyse([ledger], logs)["campaigns"]["c1"]["total"]
    bounds = total["bounds"]
    split = total["gap_between_the_bounds_usd"]
    assert split["total"] == pytest.approx(bounds["prudent"]["simulated_5m_usd"] - bounds["favourable"]["simulated_5m_usd"], abs=1e-5)
    assert split["first_request_reads"] + split["in_session_gaps"] == pytest.approx(split["total"], abs=1e-5)


def test_first_request_has_no_predecessor_and_writes_at_the_5_minute_price(tmp_path):
    ledger, logs = _campaign(tmp_path, [("reviewer", "sid-1", [_rec("m0", 0, w=1_000_000)], None)])
    total = ttl.analyse([ledger], logs)["campaigns"]["c1"]["total"]
    assert total["requests_with_a_predecessor"] == 0
    assert total["real_1h_usd"] == 4.0
    assert total["bounds"]["prudent"]["simulated_5m_usd"] == total["bounds"]["favourable"]["simulated_5m_usd"] == 2.5
    assert total["bounds"]["prudent"]["share_of_requests_over_ttl"] is None


def test_results_are_net_gain_net_loss_or_undecidable_between_the_bounds(tmp_path):
    # a huge read after a long gap: rewriting it at 1.25x the input price costs far more than the 1h write saved
    long = [_rec("m0", 0, w=1000), _rec("m1", 1000, r=100_000_000, o=1)]
    short = [_rec("m0", 0, w=1_000_000), _rec("m1", 10, r=1000, o=1)]
    edge = [_rec("m0", 0, w=1000), _rec("m1", 100, r=5_000_000, o=1), _rec("m2", 395, r=5_000_000, o=1),
            _rec("m3", 400, r=5_000_000, o=1)]
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-a", long, None), ("reviewer", "sid-b", short, None),
                                        ("corrector", "sid-c", edge, None)])
    roles = ttl.analyse([ledger], logs)["campaigns"]["c1"]["by_role"]
    assert roles["implementer"]["result"] == "net_loss"
    assert roles["reviewer"]["result"] == "net_gain"
    assert roles["corrector"]["result"] == "undecidable_between_the_bounds"


# --- the amendment: a first request that reads a cache entry written by an earlier session

def _pair(gap, second_model="claude-sonnet-5-5", first_model="claude-sonnet-5-5"):
    """Session A: one request ending at t=10. Session B starts ``gap`` s after and reads 1 000 000 cached tokens."""
    a = [_rec("a0", 0, w=1000, model=first_model), _rec("a1", 10, r=1000, model=first_model)]
    b = [_rec("b0", 10 + gap, r=1_000_000, w=100, model=second_model)]
    return a, b


def _first_read(tmp_path, gap, **kw):
    a, b = _pair(gap, **kw)
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-a", a, None), ("reviewer", "sid-b", b, None)])
    out = ttl.analyse([ledger], logs)["campaigns"]["c1"]
    return out, {s["role"]: s for s in out["sessions"]}["reviewer"]


def test_prudent_counts_every_first_request_read_as_expired_favourable_keeps_a_near_one(tmp_path):
    out, b = _first_read(tmp_path, 300)  # exactly the TTL: kept under the favourable reading
    fav, pru = b["bounds"]["favourable"], b["bounds"]["prudent"]
    assert pru["first_requests_with_expired_cache_read"] == {"sessions": 1, "cache_read_tokens": 1_000_000}
    assert fav["first_requests_with_expired_cache_read"] == {"sessions": 0, "cache_read_tokens": 0}
    assert b["first_request_cache_reads"] == {"sessions": 1, "cache_read_tokens": 1_000_000}
    # 1 000 000 read at 0.10 kept ; rewritten at 2.50 when expired ; the 100 written tokens are priced the same
    assert pru["simulated_5m_usd"] - fav["simulated_5m_usd"] == pytest.approx(2.4, abs=1e-5)
    # the figures of the rule without the amendment stay available and equal the favourable ones here
    assert pru["simulated_5m_usd_before_correction"] == fav["simulated_5m_usd"]
    assert b["result"] == "undecidable_between_the_bounds"


def test_favourable_expires_a_first_request_read_when_the_nearest_earlier_session_is_too_old(tmp_path):
    out, b = _first_read(tmp_path, 301)
    assert b["bounds"]["favourable"]["first_requests_with_expired_cache_read"]["sessions"] == 1
    assert b["bounds"]["favourable"]["simulated_5m_usd"] == b["bounds"]["prudent"]["simulated_5m_usd"]
    assert b["bounds"]["favourable"]["simulated_5m_usd"] > b["bounds"]["favourable"]["simulated_5m_usd_before_correction"]


def test_favourable_needs_an_earlier_session_with_the_same_model_alias(tmp_path):
    _, b = _first_read(tmp_path, 5, first_model="claude-opus-5-5")
    assert b["bounds"]["favourable"]["first_requests_with_expired_cache_read"]["sessions"] == 1


def test_the_first_session_of_a_campaign_has_no_earlier_session_so_its_read_is_expired(tmp_path):
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-a", [_rec("a0", 0, r=1000, w=10)], None)])
    first = ttl.analyse([ledger], logs)["campaigns"]["c1"]["sessions"][0]["bounds"]["favourable"]
    assert first["first_requests_with_expired_cache_read"]["cache_read_tokens"] == 1000


def test_an_overlapping_earlier_session_keeps_the_read_and_is_counted(tmp_path):
    a = [_rec("a0", 0, w=1000), _rec("a1", 100, r=1000)]
    b = [_rec("b0", 50, r=1000, w=10)]
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-a", a, None), ("reviewer", "sid-b", b, None)])
    out = ttl.analyse([ledger], logs)["campaigns"]["c1"]
    assert out["sessions_started_while_an_earlier_one_was_still_running"] == 1
    assert {s["role"]: s for s in out["sessions"]}["reviewer"]["bounds"]["favourable"][
        "first_requests_with_expired_cache_read"]["sessions"] == 0


def test_a_first_request_without_cache_read_is_unaffected_by_the_clause(tmp_path):
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-a", [_rec("a0", 0, w=1000, o=3)], None)])
    s = ttl.analyse([ledger], logs)["campaigns"]["c1"]["sessions"][0]
    assert s["first_request_cache_reads"]["sessions"] == 0
    assert all(b["simulated_5m_usd"] == b["simulated_5m_usd_before_correction"] for b in s["bounds"].values())


# --- exclusion, comparison like with like

def test_unusable_sessions_are_listed_and_excluded_from_both_sides(tmp_path):
    good = _four(20, 20)
    ledger, logs = _campaign(tmp_path, [
        ("implementer", "sid-ok", good, None), ("reviewer", "sid-bad-total", good, 12345),
        ("corrector", "sid-no-price", [_rec("m0", 0, w=10, model="claude-unknown-9")], None),
        ("corrector", "sid-haiku", [_rec("m0", 0, w=10, model="claude-haiku-5-5")], None)])
    events = [json.loads(line) for line in ledger.read_text().splitlines()]
    events += [{"kind": "cloud_started", "dry_run": False, "role": "reviewer", "session_id": "sid-gone"},
               {"kind": "cloud_started", "dry_run": True, "role": "reviewer", "session_id": "sid-dry"}]
    ledger.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    out = ttl.analyse([ledger], logs)["campaigns"]["c1"]
    assert out["cloud_sessions_in_ledger"] == 5 and out["sessions_replayed"] == 1
    assert {(e["session"], e["reason"]) for e in out["sessions_excluded"]} == {
        ("c1/reviewer-01", "tokens_differ_from_ledger"), ("c1/corrector-01", "no_usable_price"),
        ("c1/corrector-02", "no_usable_price"), ("c1/reviewer-02", "log_missing")}
    assert out["total"]["requests"] == len(good) - 1  # only the replayed session is on either side
    assert [s["session"] for s in out["sessions"]] == ["c1/implementer-01"]


def test_ambiguous_log_is_excluded(tmp_path):
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-1", _four(20, 20), None)])
    _log(logs / "other" / "sid-1.jsonl", _four(20, 20))
    out = ttl.analyse([ledger], logs)["campaigns"]["c1"]
    assert out["sessions_replayed"] == 0 and out["sessions_excluded"][0]["reason"] == "log_ambiguous"


def test_a_model_without_a_price_on_the_day_is_not_estimated(tmp_path):
    records = [_rec("m0", 0, w=10)]
    records[0]["timestamp"] = "2027-03-01T00:00:00Z"
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-1", records, None)])
    assert ttl.analyse([ledger], logs)["campaigns"]["c1"]["sessions_excluded"][0]["reason"] == "no_usable_price"


# --- privacy of the output

def test_output_has_no_raw_session_id_prompt_or_path_and_never_overwrites(tmp_path, capsys):
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-RAW-123", _four(20, 20), None)])
    target = tmp_path / "out.json"
    assert ttl.main(["--ledger", str(ledger), "--session-logs-dir", str(logs), "--out", str(target)]) == 0
    text = target.read_text()
    for forbidden in ("sid-RAW-123", SECRET, "someone", str(tmp_path), *_personal_strings()):
        assert forbidden not in text
    assert ttl.main(["--ledger", str(ledger), "--session-logs-dir", str(logs), "--out", str(target)]) == 2
    assert "refused: FileExistsError" in capsys.readouterr().err


def test_refuses_a_misnamed_ledger(tmp_path, capsys):
    (tmp_path / "x.jsonl").write_text("")
    assert ttl.main(["--ledger", str(tmp_path / "x.jsonl"), "--session-logs-dir", str(tmp_path)]) == 2
    assert "ledger-<campaign>" in capsys.readouterr().err


def test_replay_makes_no_network_call_and_does_not_modify_logs(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network used")
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    ledger, logs = _campaign(tmp_path, [("implementer", "sid-1", _four(20, 20), None)])
    before = {p: p.read_bytes() for p in logs.rglob("*.jsonl")}
    ttl.analyse([ledger], logs)
    assert {p: p.read_bytes() for p in logs.rglob("*.jsonl")} == before


# --- committed evidence

def test_committed_aggregates_are_clean_and_consistent():
    path = DOCS / "pat-19-cache-ttl-replay-v1.json"
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["schema"] == "foundry.cache-ttl-replay.v1"
    for forbidden in ("session_id", *_personal_strings()):
        assert forbidden not in text
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)  # a raw session id
    for camp in data["campaigns"].values():
        assert camp["sessions_replayed"] + len(camp["sessions_excluded"]) == camp["cloud_sessions_in_ledger"]
        assert camp["total"]["requests"] == sum(s["requests"] for s in camp["sessions"])
        for bound in ttl.BOUNDS:
            assert abs(camp["total"]["bounds"][bound]["simulated_5m_usd"]
                       - sum(s["bounds"][bound]["simulated_5m_usd"] for s in camp["sessions"])) < 1e-4
        assert camp["total"]["result"] in {"net_gain", "net_loss", "undecidable_between_the_bounds"}
    allc = data["all_campaigns"]["total"]
    for group in ("by_role", "by_model"):
        for scope in (*data["campaigns"].values(), data["all_campaigns"]):
            parts = scope[group].values()
            total = scope["total"]
            assert sum(a["requests"] for a in parts) == total["requests"]
            assert sum(a["real_1h_usd"] for a in parts) == pytest.approx(total["real_1h_usd"], abs=1e-4)
            for bound in ttl.BOUNDS:
                assert sum(a["bounds"][bound]["simulated_5m_usd"] for a in parts) == pytest.approx(
                    total["bounds"][bound]["simulated_5m_usd"], abs=1e-4)
    for camp in data["campaigns"].values():
        assert sum(s["real_1h_usd"] for s in camp["sessions"]) == pytest.approx(camp["total"]["real_1h_usd"], abs=1e-4)
    assert sum(c["total"]["real_1h_usd"] for c in data["campaigns"].values()) == pytest.approx(allc["real_1h_usd"], abs=1e-4)
    split = allc["gap_between_the_bounds_usd"]
    assert split["first_request_reads"] + split["in_session_gaps"] == pytest.approx(split["total"], abs=1e-5)
    assert allc["first_request_cache_reads"]["sessions"] == 102 and allc["first_request_cache_reads"]["cache_read_tokens"] == 340392
    assert allc["bounds"]["prudent"]["simulated_5m_usd"] > allc["bounds"]["prudent"]["simulated_5m_usd_before_correction"]
    assert allc["requests"] == sum(c["total"]["requests"] for c in data["campaigns"].values())
    assert allc["bounds"]["prudent"]["simulated_5m_usd"] >= allc["bounds"]["favourable"]["simulated_5m_usd"]
