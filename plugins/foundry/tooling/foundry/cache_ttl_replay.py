"""PAT-132: offline replay of HOST session logs, real 1-hour prompt cache against a simulated 5-minute cache.

Reads the ``ledger-*.jsonl`` of finished PAT-19 campaigns (session id and role of each cloud session) and the host
session logs kept OFF the repository (``<dir>/*/<session>.jsonl``). For every session it gives the catalogue cost as
recorded (cache writes at the 1-hour price, as the logs say) and the cost the same requests would have had under a
5-minute cache, with the dated grid ``pricing-breakdown-v1.json``. No model, no ``claude``, no network, no log
modified. API list prices are WEIGHTS under a subscription, never a bill; nothing here says anything about quota.

SIMULATION RULE (first written before any figure was computed; it was then AMENDED TWICE after figures had been seen,
both amendments declared below and in the results document, with the earlier figures kept in the output). TTL = 300 s.
One request = one assistant ``message.id`` (streaming duplicates count once). Per session, in log order, request
``i`` has a first record instant ``f_i`` and a last record instant ``e_i`` (the log timestamps are those of the
records, NOT the start of the request, which is earlier by an unknown latency; the cache lifetime is measured from
the START of the previous request). ``gap_i`` is the distance to the previous request of the same session and the
cache found by request ``i`` is EXPIRED when ``gap_i > TTL``. Two readings, never one unqualified figure:

* prudent (most expiries): ``gap_i = f_i - e_(i-2)`` (the real start of request ``i-1`` cannot precede the end of
  response ``i-2``, the real start of request ``i`` cannot follow ``f_i``: an upper bound of the true gap); for
  the second request of a session, which has no ``e_(i-2)``, ``f_1 - f_0`` (not a bound: said where it matters);
* favourable (fewest expiries): ``gap_i = f_i - e_(i-1)`` (idle time between the end of the previous response and
  the first record of this one; it ignores the duration of the previous request, which the real clock counts, so
  it is not a rigorous lower bound).

Pricing per request (``I`` input, ``R`` cache read, ``W`` cache written, ``O`` output tokens): real = I*input +
R*cache_read + W1h*cache_write_1h + W5m*cache_write_5m + O*output (the logs' own split). Simulated, not expired:
I*input + R*cache_read + W*cache_write_5m + O*output; simulated, expired: I*input + (R+W)*cache_write_5m +
O*output (the tokens it read are written again, at the 5-minute price). The first request of a session has no
predecessor and writes at the 5-minute price. NOT modelled: several cache breakpoints with their own lifetimes,
a cache shared between sessions, anything the host does differently under a 5-minute setting. Subagent requests:
the reader does not read the ``isSidechain`` flag (outside FOUNDRY-ADR-0015); an inline subagent would be merged
into its session's lineage (a limit, checked once by hand on the campaigns read, see the results document).

AMENDMENT (2026-10-09, made AFTER the first figures were seen; found by the coordinator before review; the first
figures stay in the output as ``*_before_correction``): the FIRST request of a session can read a cache entry written
by an EARLIER session (shared prefix), which a 1-hour cache keeps between sessions and a 5-minute cache may not. The
rule above never priced it. Added clause: when the first request reads cache (R > 0) and its entry is EXPIRED, it is
priced like any expired request ((R+W) at the 5-minute write price). Prudent: every first-request read is expired.
Favourable: expired unless the nearest earlier session OF THE SAME CAMPAIGN (earlier = its first record is earlier)
that used the SAME model alias ended its last request of that model at most TTL seconds before this first record
(a negative gap, i.e. overlap, keeps the read); no such session (the first of the campaign) means expired. Which
earlier session really wrote the entry is NOT observable from permitted fields: the favourable reading is an
ASSUMPTION, not a measurement. Unrelated host sessions that ran in between are invisible (limit).

SECOND AMENDMENT (2026-10-09, after the independent review, made after the earlier figures were seen; the figures of the
rule as first written stay as ``*_before_correction`` and those after the first amendment as
``*_after_first_correction``). (B) The prudent gap of the second request was not an upper bound (``f_1 - f_0``): the
reference is now the timestamp of the last timestamped record that precedes the first assistant record of the session
(a lower bound of the real start of request 0 UNDER AN ASSUMPTION the data do not prove: that the host writes no record
between sending request 0 and its first assistant record; only its timestamp is read), else ``f_0`` as before. (D) A request that
neither reads nor writes cache does not refresh the entry: the reference of request i is the last EARLIER request
that read or wrote cache (``j``); favourable gap ``f_i - e_j``; prudent gap ``f_i - (e_(j-1), or the instant of B when
j = 0)``. A request with no earlier request that read or wrote cache takes the entry status of the first amendment.
(C) Caches are per model: a session with several model aliases is LISTED and excluded (``several_models``), not
replayed with merged lineages.

The reader (FOUNDRY-ADR-0015) extracts ONLY token counters, the model alias, the record timestamp (also that of the record
before the first assistant record) and the message
id (to count a request once); never a prompt, a path, a command, a tool name or an excerpt. A session whose log is
missing, ambiguous, unreadable, has an absent counter, unordered timestamps, differing duplicates, an unpriced
model or a token total that differs from the ledger's is LISTED and excluded from BOTH sides (never estimated).
Sessions are named by campaign, role and rank, never by raw id.

Exit codes of ``main``: 0; 2 when an input is refused or unreadable.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from foundry.cost_breakdown import GRID_PATH, BreakdownError, _count, _read_jsonl, _share, _usd, load_grid, price_for

TTL_SECONDS = 300
BOUNDS = ("prudent", "favourable")
BUCKETS = (("le_60", 60), ("le_300", 300), ("le_600", 600), ("le_1800", 1800), ("le_3600", 3600), ("gt_3600", None))
_MILLION = Decimal(1_000_000)


# ------------------------------------------------------------------------------------------------ reader

def _instant(value: object) -> dt.datetime:
    if not isinstance(value, str):
        raise BreakdownError("timestamp_absent")
    try:
        at = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BreakdownError("timestamp_unreadable") from exc
    if at.tzinfo is None:
        raise BreakdownError("timestamp_unreadable")
    return at


def read_host_requests(path: Path) -> list[dict[str, Any]]:
    """Requests of a HOST session log, in order: model alias, first and last record instant, token counters.

    Reads only ``type``, ``timestamp``, ``message.id``, ``message.model`` and ``message.usage`` counters. Every
    refusal is a fixed code (``BreakdownError``), never a message carrying a path or a content."""
    out: dict[str, dict[str, Any]] = {}
    before: dt.datetime | None = None  # timestamp of the last record before the first assistant record
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise BreakdownError("log_unreadable") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BreakdownError("log_unreadable") from exc
        message = event.get("message") if isinstance(event, dict) and event.get("type") == "assistant" else None
        if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
            if not out and isinstance(event, dict):  # only the timestamp of a record that precedes the first request
                try:
                    before = _instant(event.get("timestamp"))
                except BreakdownError:
                    pass
            continue
        usage = message["usage"]
        split = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
        tokens = {"input": _count(usage.get("input_tokens")), "read": _count(usage.get("cache_read_input_tokens")),
                  "write": _count(usage.get("cache_creation_input_tokens")),
                  "write_1h": _count(split.get("ephemeral_1h_input_tokens")),
                  "write_5m": _count(split.get("ephemeral_5m_input_tokens")), "output": _count(usage.get("output_tokens"))}
        if None in tokens.values():
            raise BreakdownError("counter_absent")
        if tokens["write_1h"] + tokens["write_5m"] != tokens["write"]:  # type: ignore[operator]
            raise BreakdownError("cache_write_split_differs")
        mid, model = message.get("id"), message.get("model")
        if not isinstance(mid, str) or not isinstance(model, str):
            raise BreakdownError("request_identity_absent")
        at = _instant(event.get("timestamp"))
        known = out.get(mid)
        if known is None:
            out[mid] = {"model": model, "first": at, "last": at, "tokens": tokens}
        elif known["tokens"] != tokens or known["model"] != model:
            raise BreakdownError("duplicate_differs")
        else:
            known["first"], known["last"] = min(known["first"], at), max(known["last"], at)
    requests = list(out.values())
    if requests:
        requests[0]["before"] = None if before is None else min(before, requests[0]["first"])
    if any(b["first"] < a["first"] or b["first"] < a["last"] for a, b in zip(requests, requests[1:])):
        raise BreakdownError("timestamps_unordered")
    return requests


# ----------------------------------------------------------------------------------------------- the rule

def _refreshes(request: Mapping[str, Any]) -> bool:
    return request["tokens"]["read"] + request["tokens"]["write"] > 0


def gaps(requests: Sequence[Mapping[str, Any]], bound: str) -> list[float | None]:
    """Seconds between each request and the last EARLIER request that read or wrote cache, under ``bound``.

    None when there is none (the request takes the entry status of ``entry_expiry``)."""
    if bound not in BOUNDS:
        raise BreakdownError("unknown bound")
    out: list[float | None] = []
    for i, request in enumerate(requests):
        j = next((k for k in range(i - 1, -1, -1) if _refreshes(requests[k])), None)
        if j is None:
            out.append(None)
        elif bound == "favourable":
            out.append((request["first"] - requests[j]["last"]).total_seconds())
        else:
            ref = requests[j - 1]["last"] if j >= 1 else (requests[0].get("before") or requests[0]["first"])
            out.append((request["first"] - ref).total_seconds())
    return out


def gaps_first_version(requests: Sequence[Mapping[str, Any]], bound: str) -> list[float | None]:
    """The gaps of the rule as first written and after the first amendment (kept only to show the earlier figures)."""
    out: list[float | None] = [None]
    for i in range(1, len(requests)):
        if bound == "prudent":
            ref = requests[i - 2]["last"] if i >= 2 else requests[0]["first"]
        else:
            ref = requests[i - 1]["last"]
        out.append((requests[i]["first"] - ref).total_seconds())
    return out


def entry_expiry(first: Mapping[str, Any], earlier: Sequence[Sequence[Mapping[str, Any]]]) -> dict[str, bool]:
    """Whether the cache read by the first request of a session is expired, per bound (see the AMENDMENT above).

    ``earlier`` holds the requests of the other replayed sessions of the SAME campaign whose first record precedes
    this one. Prudent: always expired. Favourable: expired unless the nearest such session that used the same model
    alias ended that model's last request at most TTL seconds before (overlap counts as kept)."""
    gaps_to = [(first["first"] - max(r["last"] for r in reqs if r["model"] == first["model"])).total_seconds()
               for reqs in earlier if any(r["model"] == first["model"] for r in reqs)]
    return {"prudent": True, "favourable": not gaps_to or min(gaps_to) > TTL_SECONDS}


def request_costs(tokens: Mapping[str, int], rates: Mapping[str, Decimal], expired: bool) -> tuple[Decimal, Decimal]:
    """(real 1-hour cost as logged, simulated 5-minute cost) of one request, in USD."""
    fixed = tokens["input"] * rates["input"] + tokens["output"] * rates["output"]
    real = fixed + tokens["read"] * rates["cache_read"] + tokens["write_1h"] * rates["cache_write_1h"] \
        + tokens["write_5m"] * rates["cache_write_5m"]
    if expired:
        sim = fixed + (tokens["read"] + tokens["write"]) * rates["cache_write_5m"]
    else:
        sim = fixed + tokens["read"] * rates["cache_read"] + tokens["write"] * rates["cache_write_5m"]
    return real / _MILLION, sim / _MILLION


def _rows(requests: Sequence[Mapping[str, Any]], grid: Mapping[str, Any], role: str,
          entry_expired: Mapping[str, bool]) -> list[dict[str, Any]]:
    gap = {b: gaps(requests, b) for b in BOUNDS}
    old = {b: gaps_first_version(requests, b) for b in BOUNDS}
    rows = []
    for i, req in enumerate(requests):
        entry = price_for(grid, "claude", req["model"], req["first"].astimezone(dt.timezone.utc).date().isoformat())
        if entry is None or "prompt_tokens_threshold" in entry:
            raise BreakdownError("no_usable_price")
        row: dict[str, Any] = {"role": role, "model": req["model"], "read": req["tokens"]["read"], "first": gap["prudent"][i] is None,
                               "no_cache_activity": not _refreshes(req),
                               "write_1h": req["tokens"]["write_1h"], "write_5m": req["tokens"]["write_5m"], "gap": {}}
        for b in BOUNDS:
            g = gap[b][i]
            expired = entry_expired[b] if g is None else g > TTL_SECONDS
            real, sim = request_costs(req["tokens"], entry["rates"], expired)
            o = old[b][i]
            _, before = request_costs(req["tokens"], entry["rates"], o is not None and o > TTL_SECONDS)
            _, after_first = request_costs(req["tokens"], entry["rates"], entry_expired[b] if o is None else o > TTL_SECONDS)
            row["real"] = real
            row.setdefault("sim", {})[b], row["gap"][b], row.setdefault("expired", {})[b] = sim, g, expired
            row.setdefault("sim_before", {})[b] = before
            row.setdefault("sim_first", {})[b] = after_first
            row.setdefault("changed", {})[b] = o is not None and g is not None and o != g
        rows.append(row)
    return rows


# --------------------------------------------------------------------------------------------- aggregates

def _bucket(gap: float) -> str:
    return next(name for name, edge in BUCKETS if edge is None or gap <= edge)


def _verdict(prudent: Decimal, favourable: Decimal) -> str:
    """Sign of simulated minus real under both readings: a gain needs both below zero, a loss both above."""
    if prudent < 0 and favourable < 0:
        return "net_gain"
    if prudent > 0 and favourable > 0:
        return "net_loss"
    return "undecidable_between_the_bounds"


def aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    real = sum((r["real"] for r in rows), Decimal(0))
    firsts = [r for r in rows if r["first"] and r["read"] > 0]
    out: dict[str, Any] = {
        "requests": len(rows), "requests_with_a_predecessor": sum(1 for r in rows if r["gap"]["prudent"] is not None),
        "cache_read_tokens": sum(r["read"] for r in rows),
        "cache_write_tokens_as_logged": {"ephemeral_1h": sum(r["write_1h"] for r in rows),
                                         "ephemeral_5m": sum(r["write_5m"] for r in rows)},
        "real_1h_usd": _usd(real),
        "first_request_cache_reads": {"requests": len(firsts), "cache_read_tokens": sum(r["read"] for r in firsts)},
        "bounds": {}}
    deltas, before, first_fix = {}, {}, {}
    for b in BOUNDS:
        sim = sum((r["sim"][b] for r in rows), Decimal(0))
        late = [r for r in rows if r["expired"][b] and not r["first"]]
        late_first = [r for r in firsts if r["expired"][b]]
        sim_before = sum((r["sim_before"][b] for r in rows), Decimal(0))
        before[b] = sim_before - real
        sim_first = sum((r["sim_first"][b] for r in rows), Decimal(0))
        first_fix[b] = sim_first - real
        hist = dict.fromkeys((name for name, _ in BUCKETS), 0)
        for r in rows:
            if r["gap"][b] is not None:
                hist[_bucket(r["gap"][b])] += 1
        deltas[b] = sim - real
        out["bounds"][b] = {
            "simulated_5m_usd": _usd(sim), "delta_usd": _usd(sim - real), "delta_share_of_real": _share(sim - real, real),
            "simulated_5m_usd_before_correction": _usd(sim_before),
            "delta_usd_before_correction": _usd(sim_before - real),
            "delta_share_of_real_before_correction": _share(sim_before - real, real),
            "simulated_5m_usd_after_first_correction": _usd(sim_first),
            "delta_usd_after_first_correction": _usd(sim_first - real),
            "delta_share_of_real_after_first_correction": _share(sim_first - real, real),
            "gaps_changed_by_second_correction": sum(1 for r in rows if r["changed"][b]),
            "first_requests_with_expired_cache_read": {
                "requests": len(late_first), "cache_read_tokens": sum(r["read"] for r in late_first)},
            "requests_over_ttl": len(late),
            "share_of_requests_over_ttl": _share(len(late), out["requests_with_a_predecessor"]),
            "cache_read_tokens_on_requests_over_ttl": sum(r["read"] for r in late),
            "cache_read_tokens_on_requests_over_ttl_share": _share(sum(r["read"] for r in late),
                                                                    out["cache_read_tokens"]),
            "max_gap_seconds": max((r["gap"][b] for r in rows if r["gap"][b] is not None), default=None),
            "gap_distribution_seconds": hist}
    out["result"] = _verdict(deltas["prudent"], deltas["favourable"])
    out["result_after_first_correction"] = _verdict(first_fix["prudent"], first_fix["favourable"])
    split = {"first_request_reads": Decimal(0), "in_session_gaps": Decimal(0)}
    for r in rows:
        split["first_request_reads" if r["first"] else "in_session_gaps"] += r["sim"]["prudent"] - r["sim"]["favourable"]
    out["gap_between_the_bounds_usd"] = {"total": _usd(sum(split.values(), Decimal(0))),
                                         **{k: _usd(v) for k, v in split.items()}}
    out["requests_without_cache_read_or_write"] = sum(1 for r in rows if r["no_cache_activity"])
    out["result_before_correction"] = _verdict(before["prudent"], before["favourable"])
    return out


def _grouped(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    return {k: aggregate([r for r in rows if r[key] == k]) for k in sorted({r[key] for r in rows})}


# --------------------------------------------------------------------------------------------------- CLI

def analyse(ledgers: Sequence[Path], logs_dir: Path, grid: Path = GRID_PATH) -> dict[str, Any]:
    price_grid = load_grid(grid)
    campaigns: dict[str, Any] = {}
    all_rows: list[dict[str, Any]] = []
    for ledger in ledgers:
        match = re.fullmatch(r"ledger-(.+)\.jsonl", ledger.name)
        if match is None:
            raise BreakdownError("ledger file must be named ledger-<campaign>.jsonl")
        events = _read_jsonl(ledger)
        settled = {e["session_id"]: e.get("premium_tokens") for e in events if e.get("kind") == "settled"}
        started = [e for e in events if e.get("kind") == "cloud_started" and not e.get("dry_run")]
        rank: dict[str, int] = {}
        sessions, excluded, rows, replayed = [], [], [], []
        for event in started:
            role = event["role"]
            rank[role] = rank.get(role, 0) + 1
            name = f"{match.group(1)}/{role}-{rank[role]:02d}"
            found = sorted(logs_dir.glob(f"*/{event['session_id']}.jsonl"))
            try:
                if len(found) != 1:
                    raise BreakdownError("log_missing" if not found else "log_ambiguous")
                requests = read_host_requests(found[0])
                if not requests:
                    raise BreakdownError("no_request")
                if len({q["model"] for q in requests}) > 1:
                    raise BreakdownError("several_models")
                total = sum(q["tokens"][k] for q in requests for k in ("input", "output", "read", "write"))
                if settled.get(event["session_id"]) != total:
                    raise BreakdownError("tokens_differ_from_ledger")
                for q in requests:  # a priced model on the day is required, as before
                    _rows([q], price_grid, role, {b: True for b in BOUNDS})
            except BreakdownError as exc:
                excluded.append({"session": name, "role": role, "reason": str(exc)})
                continue
            replayed.append((name, role, requests))
        replayed.sort(key=lambda x: x[2][0]["first"])
        overlaps = 0
        for index, (name, role, requests) in enumerate(replayed):
            earlier = [r for _, _, r in replayed[:index]]
            overlaps += any(r[-1]["last"] > requests[0]["first"] for r in earlier)
            mine = _rows(requests, price_grid, role, entry_expiry(requests[0], earlier))
            sessions.append({"session": name, "role": role, **aggregate(mine)})
            rows.extend(mine)
        campaigns[match.group(1)] = {
            "cloud_sessions_in_ledger": len(started), "sessions_replayed": len(sessions), "sessions_excluded": excluded,
            "sessions_started_while_an_earlier_one_was_still_running": overlaps,
            "total": aggregate(rows), "by_role": _grouped(rows, "role"), "by_model": _grouped(rows, "model"),
            "sessions": sessions}
        all_rows.extend(rows)
    return {
        "schema": "foundry.cache-ttl-replay.v1", "ttl_seconds": TTL_SECONDS,
        "reading": "outside the frozen rule; API list prices are weights under a subscription, never a bill; "
                   "nothing here concerns the subscription quota (effect unknown)",
        "rule": "a request finds the cache expired when its gap to the last earlier request of its session that read or "
                "wrote cache exceeds the TTL (300 s), the tokens it read are then written again at the 5-minute price; "
                "two readings of the gap, prudent (f_i minus the end of response j-1, or the record preceding the "
                "first request when j = 0) and favourable (f_i minus the end of response j), never one figure; "
                "first-amendment clause: a first request that reads cache is expired under the prudent reading, and "
                "under the favourable one unless the nearest earlier session of the same campaign and model ended "
                "at most 300 s before (an assumption, not observable); second amendment: sessions with several "
                "models are excluded; figures of the earlier versions are kept as *_before_correction (rule as "
                "first written) and *_after_first_correction; full text in the module docstring",
        "price_grid": {"entries": [{"model": e["model"], "source": e["source"], "captured_at": e["captured_at"],
                                    "effective_from": e["effective_from"], "effective_to": e["effective_to"]}
                                   for e in price_grid["entries"]]},
        "campaigns": campaigns, "all_campaigns": {"total": aggregate(all_rows), "by_role": _grouped(all_rows, "role"),
                                                  "by_model": _grouped(all_rows, "model")}}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m foundry.cache_ttl_replay", description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", action="append", required=True, metavar="LEDGER",
                        help="ledger-<campaign>.jsonl of a finished campaign; repeatable")
    parser.add_argument("--session-logs-dir", required=True, help="host session logs (<dir>/*/<session>.jsonl), off repo")
    parser.add_argument("--grid", default=str(GRID_PATH))
    parser.add_argument("--out", default=None, help="write the aggregates here (never overwrites)")
    args = parser.parse_args(argv)
    try:
        doc = analyse([Path(p) for p in args.ledger], Path(args.session_logs_dir), Path(args.grid))
        text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
        if args.out:
            with Path(args.out).open("x", encoding="utf-8") as handle:
                handle.write(text)
        else:
            sys.stdout.write(text)
    except OSError as exc:  # its message names an operator path: only the kind is printed
        print(f"refused: {type(exc).__name__} (an input is unreadable or the output cannot be created)", file=sys.stderr)
        return 2
    except (BreakdownError, KeyError, ValueError) as exc:
        print(f"refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
