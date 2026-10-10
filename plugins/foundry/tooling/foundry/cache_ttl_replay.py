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

PAT-133 EXTENSION: INTERACTIVE host sessions, both directions (``--host-session``). The rules below were written BEFORE
any figure of the rules was computed on an interactive session, with ONE limit: the coordinator's earlier exploratory,
unpublished observation on one of the replayed sessions (gaps, tokens written right after them) was known, and it bears
on the very quantities direction B depends on, so the rule is not blind to it. A later correction would be declared in the results document in a dated
section, the earlier figures kept. The PAT-132 mode above, its rule and its outputs are unchanged.

Input: the main conversation log ``<dir>/<session>.jsonl`` designated explicitly, no campaign ledger; its subagent logs are
the ``*.jsonl`` files under ``<dir>/<session>/subagents/`` (nested ones included), found by that position only. Fields
read, ADR-0015 list: token counters, model alias (``message.model``), record timestamp; session identity is the file itself
and is never emitted (sessions are named by rank). Fields read BEYOND the ADR list, named: ``type`` (to pick the assistant
records, and the timestamp of the record that precedes the first of them) and ``message.id`` (in-memory de-duplication of
streaming records). Nothing else is read: no prompt, response, path, file name, command, tool name, agent type or excerpt.
Provenance vocabulary (ADR-0015 / ADR-0008): counters, alias and timestamps are ``host_reported``; every USD figure, real or
simulated, is ``pricing_derived`` (grid ``pricing-breakdown-v1.json``, a weight under a subscription, never a bill); a lineage
that cannot be priced (alias without a usable price, e.g. Haiku 5.5 whose price depends on a prompt length the logs do not
carry; unreadable log) is ``unavailable``, keeps its alias when it is known, and leaves BOTH sides of every comparison;
``client_observed`` is empty here (this reader observes nothing by itself).

LINEAGE. A lineage is the requests of ONE log file with ONE model alias, in log order (a cache is per model; the entry of a
main conversation that switches model and comes back is the same lineage, the time away counts in its gap). A subagent log is
one lineage per alias it contains (normally one). A model switch therefore opens a new lineage; a compaction is NOT detected
(the rule only looks at counters and gaps: it appears as a request that writes a large prefix, priced as any other write).
A lineage's first request has no predecessor in it. The kinds are ``main`` and ``subagent`` (the file's position). The
LOGICAL ROLE (implementer, reviewer, ...) is NOT derivable within ADR-0015 (the agent type is not an allowed field and the
counters, alias, timestamps and position do not carry it): figures are given by kind and by model alias, never by role.

READER REPAIRS for interactive logs (declared in the results document, made after a first run refused real logs; none
changes a rule above): lines are split on ``\\n`` only (``str.splitlines`` also splits on U+2028 and others, valid inside a
JSON string); records of one message id may differ in ``output_tokens`` only (it grows while the response streams: the
largest is kept); a later record of an id whose input, cache read and cache write counters are all zero is a zeroed copy
and is ignored (a first zeroed record is replaced by the real one); a request whose two cache-write classes do not add up
to its total is ``ambiguous``: it stays in the sequence (gaps), is never a recovery reference, and its tokens are on
neither side of any figure (counted in ``requests_ambiguous_counters_on_neither_side``); any other difference between
records of one id refuses that file (``duplicate_differs``, listed). A last line that cannot be parsed is ignored with
``--until`` only (a record being written is later than the instant by construction).

PRICE. A lineage is priced only when EVERY request has a usable price on its own day in the dated grid: otherwise it is
``unavailable`` in USD (``no_price_for_alias_on_day``, e.g. a day before the grid begins or an alias the grid does not
name; ``price_depends_on_prompt_length`` for Haiku 5.5), keeps its alias, and is on neither side of any USD comparison.
Its TOKEN and GAP figures (counters and timestamps only, no price needed) stay in the token view. Nothing is estimated.

GAPS. Those of PAT-132, per lineage: ``gaps(lineage, "prudent")`` is the upper reading ``U_i``, ``gaps(lineage, "favourable")``
the lower reading ``L_i`` (``L_i <= U_i``; the lower one is not a rigorous lower bound, see above); the reference request ``j``
of request ``i`` is the last earlier one of the lineage that read or wrote cache. First request of the file: the record that
precedes it is the reference under ``U`` as in PAT-132; the first request of another alias lineage uses its own first record.

DIRECTION A, ``main``: observed cache write duration (whatever the logs say, usually 1 hour) -> simulated 5 minutes. EXACTLY the
PAT-132 in-session rule on each main lineage: request ``i`` finds the cache expired when its gap (reading ``U`` = prudent,
``L`` = favourable) exceeds 300 s; what it read is then written again at the 5-minute write price; every write is priced at
the 5-minute rate; real = the logs' own split. First request of a lineage that reads cache: ``entry_expiry`` with, as earlier
sessions, the MAIN lineages of the other replayed sessions that start earlier (same alias required; a model switch opens a
lineage of another alias so it never matches); none -> expired under both readings (the writer of the entry is not observable).

DIRECTION B, ``subagent``: observed 5 minutes -> simulated 1 hour. The expiry really happened in these logs, so what is
simulated is the re-write it caused. Real cost = the logs' own split. Simulated cost of request ``i`` (``I, O`` input and
output, ``R, W`` read and written tokens, ``W5`` the part written at 5 minutes, ``rho_i`` the tokens recognised as expiry
re-writes): ``I*input + (R + rho_i)*cache_read + (W - rho_i)*cache_write_1h + O*output``. Every write is repriced at the
1-hour rate; ``rho_i`` of them are instead reads, because the entry would have been alive. Recognition from counters and
gaps only, with the reference request ``j`` (``P = R_j + W_j`` tokens cached after it; ``R_j`` the part ``j`` had itself
read, i.e. confirmed re-sent verbatim by an observed read):
  * a gap over 3,600 s expires under both durations: ``rho_i = 0``; a gap up to 300 s is no expiry: ``rho_i = 0``; a
    lineage's first request (no ``j``): ``rho_i = 0`` (its cold-start writes may be an entry shared with a sibling log: NOT
    modelled, reported as ``first_request_cache.cache_write_tokens`` and, for priced lineages, with an upper envelope in USD:
    unknown);
  BOTH bounds assume a context that only grows (append-only): the prefix cached before the gap is re-sent unchanged. A
  shortfall after a pause can also come from a compaction, a context edit or an invalidation unrelated to lifetime, in
  which case a 1-hour cache would have missed too and new content is counted as recoverable. Neither bound is therefore
  a lower bound of the gain: "prudent" is prudent only relative to "favourable".
  * prudent bound (smaller gain of the two): the expiry is recognised only when both gap readings put it in the window,
    i.e. ``L_i > 300`` and ``U_i <= 3600``, and ``rho_i = min(W5, max(0, R_j - R_i))``: only what ``j`` was observed to
    read counts, what ``j`` wrote itself is not confirmed re-sent by any read before the pause;
  * favourable bound (larger gain of the two): the expiry is recognised as soon as one reading puts it in the window,
    i.e. ``U_i > 300`` and ``L_i <= 3600``, and ``rho_i = min(W5, max(0, P - R_i))``: the whole prefix cached before the
    gap is assumed re-sent verbatim and is the part of the write that the 1-hour setting would have read.
  The counter shortfall ``P - R_i`` (or ``R_j - R_i``) must be positive: a write after a gap with no shortfall is not an
  expiry re-write and stays a 1-hour write. UNKNOWABLE from the permitted fields: which part of a write after a gap is new
  content and which is the old prefix re-sent (the formulas cap it by the prefix size under the growing-context assumption, they do not measure it); whether a
  prefix is shared with another log; the true start instant of a request. Neither bound is a measurement.

RESULT (both directions): ``simulated - real`` under each bound; ``net_gain`` when both are negative, ``net_loss`` when both are
positive, otherwise ``undecidable_between_the_bounds`` (PAT-132 ``_verdict``). Like with like: the same requests on both
sides; an unavailable lineage is on neither. NOT modelled: several cache breakpoints, a cache shared between logs, anything
the host does differently under another duration (the documented ``1h`` ignore while usage credits are drawn is unobservable
here), the subscription quota (unknown, concluded on nothing). ``--until`` ignores every record after the instant, so a log
still being written can be replayed reproducibly.

PAT-134 EXTENSION (``--subagent-1h-to-5m``, with ``--host-session``, default off, so every existing input gives the same
output). Once the subagent profiles carry a 1-hour cache lifetime, a subagent lineage is OBSERVED at 1 hour; its real effect
is then measured by replaying it towards 5 minutes, direction A, with the rule already written for the main conversation
(above): no new rule, the same function. A subagent lineage qualifies when every cache write of its non-ambiguous requests is
in the 1-hour class and there is at least one (``observed_one_hour_only``); a lineage with any 5-minute write, or none, stays
in direction B. Qualifying lineages form their own group ``subagent_1h`` (never merged with the 5-minute ``subagent`` group,
whose direction differs); their first request that reads cache uses ``entry_expiry`` against the earlier MAIN lineages, as a
main lineage does (the writer of such an entry is not observable: a limit). The lineage is chosen by the observed counters
only, never by the agent type or the profile (outside FOUNDRY-ADR-0015), so a subagent that ran at 1 hour for another
reason is replayed too. Output gains ``subagent_1h_to_5m`` (the rule and the number of lineages replayed) only with the flag.
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

from foundry.routing_facades import CLAUDE_CACHE_TTL_1H_MODELS
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


def _is_after(value: object, until: dt.datetime) -> bool:
    try:
        return _instant(value) > until
    except BreakdownError:
        return False


def _has_record(path: Path, until: dt.datetime | None) -> bool:
    """Whether the log has any record (whatever its type) at or before ``until`` (any record when ``until`` is None)."""
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError):
        return False
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if until is None:
            return True
        try:
            if _instant(event.get("timestamp")) <= until:
                return True
        except BreakdownError:
            continue
    return False


def _mid_seen(out: Mapping[str, Any], mid: object) -> bool:
    return isinstance(mid, str) and mid in out


def read_host_requests(path: Path, until: dt.datetime | None = None, interactive: bool = False) -> list[dict[str, Any]]:
    """Requests of a HOST session log, in order: model alias, first and last record instant, token counters.

    Reads only ``type``, ``timestamp``, ``message.id``, ``message.model`` and ``message.usage`` counters. Every
    refusal is a fixed code (``BreakdownError``), never a message carrying a path or a content. With ``until``, a
    record whose timestamp is later is ignored, and so is an unparsable LAST line (a record being written at read time
    is later than ``until`` by construction; PAT-133: replay a log still being written, reproducibly). With
    ``interactive`` (PAT-133, interactive logs): streaming records of one message id may differ in ``output_tokens``
    only (a count that grows while the response streams), the largest is kept, any other difference is refused; and a
    request whose two cache-write classes do not add up to its total is kept FLAGGED ``ambiguous`` (its counters are
    not trusted: it stays in the sequence, its tokens are on neither side of any figure) instead of refusing the log."""
    out: dict[str, dict[str, Any]] = {}
    before: dt.datetime | None = None  # timestamp of the last record before the first assistant record
    try:
        lines = path.read_text(encoding="utf-8").split("\n")  # not splitlines(): U+2028 etc. are valid inside a JSON string
    except (OSError, UnicodeDecodeError) as exc:
        raise BreakdownError("log_unreadable") from exc
    last = max((n for n, line in enumerate(lines) if line.strip()), default=-1)
    for number, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            if until is not None and number == last:  # a record being written at read time is later than ``until``
                continue
            raise BreakdownError("log_unreadable") from exc
        if until is not None and isinstance(event, dict) and _is_after(event.get("timestamp"), until):
            continue
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
        placeholder = interactive and not (tokens["input"] or tokens["read"] or tokens["write"])
        if placeholder and _mid_seen(out, message.get("id")):
            continue  # a later copy of a request with zeroed counters: the record that carries them is kept
        ambiguous = tokens["write_1h"] + tokens["write_5m"] != tokens["write"]  # type: ignore[operator]
        if ambiguous and not interactive:
            raise BreakdownError("cache_write_split_differs")
        mid, model = message.get("id"), message.get("model")
        if not isinstance(mid, str) or not isinstance(model, str):
            raise BreakdownError("request_identity_absent")
        at = _instant(event.get("timestamp"))
        known = out.get(mid)
        if interactive and known is not None and not (known["tokens"]["input"] or known["tokens"]["read"]
                                                      or known["tokens"]["write"]) and not placeholder:
            known["tokens"], known["model"] = tokens, model  # the first record was the zeroed one
            known.pop("ambiguous", None)
            if ambiguous:
                known["ambiguous"] = True
            known["first"], known["last"] = min(known["first"], at), max(known["last"], at)
        elif known is None:
            out[mid] = {"model": model, "first": at, "last": at, "tokens": tokens, **({"ambiguous": True} if ambiguous else {})}
        elif known["model"] != model or (known["tokens"] != tokens if not interactive else
                                         {**known["tokens"], "output": 0} != {**tokens, "output": 0}):
            raise BreakdownError("duplicate_differs")
        else:
            known["tokens"]["output"] = max(known["tokens"]["output"], tokens["output"])
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


# ------------------------------------------------------------------------- interactive sessions (PAT-133)

LONG_TTL_SECONDS = 3600
KINDS = ("main", "subagent")
# PAT-134 (``--subagent-1h-to-5m``): a subagent lineage whose cache writes were ALL observed at 1 hour is replayed in
# direction A, as its own group, so it never mixes with the 5-minute subagent lineages of direction B.
SUBAGENT_1H = "subagent_1h"
ALL_KINDS = (*KINDS, SUBAGENT_1H)
DIRECTION_A = ("main", SUBAGENT_1H)
DIRECTION = {"main": "1h_to_5m", "subagent": "5m_to_1h", SUBAGENT_1H: "1h_to_5m"}
PROVENANCE = {"token_counters": "host_reported", "model_alias": "host_reported", "timestamps": "host_reported",
              "usd_real_and_simulated": "pricing_derived", "lineage_without_usable_price": "unavailable",
              "client_observed": "none (this reader observes nothing by itself)"}
FIELDS_BEYOND_ADR_0015 = ["type (pick assistant records; timestamp of the record before the first one)",
                          "message.id (in-memory de-duplication of streaming records)"]


def discover_subagent_logs(main: Path) -> list[Path]:
    """The ``*.jsonl`` files under ``<dir>/<session>/subagents/`` (nested included): found by position only."""
    root = main.parent / main.stem / "subagents"
    return sorted(root.rglob("*.jsonl")) if root.is_dir() else []


def split_by_alias(requests: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """One lineage per model alias, first appearance first (a cache is per model)."""
    out: dict[str, list[Mapping[str, Any]]] = {}
    for request in requests:
        out.setdefault(request["model"], []).append(request)
    return list(out.values())


def request_cost_long(tokens: Mapping[str, int], rates: Mapping[str, Decimal], recovered: int) -> Decimal:
    """Simulated 1-hour cost of one request (USD): every write at the 1-hour rate, ``recovered`` of them as reads."""
    fixed = tokens["input"] * rates["input"] + tokens["output"] * rates["output"]
    return (fixed + (tokens["read"] + recovered) * rates["cache_read"]
            + (tokens["write"] - recovered) * rates["cache_write_1h"]) / _MILLION


def recovered_reads(requests: Sequence[Mapping[str, Any]], i: int, j: int | None,
                    low: float | None, high: float | None, bound: str) -> int:
    """Tokens of request ``i`` written because of an expiry that a 1-hour setting would have read (rule, direction B)."""
    if j is None or low is None or high is None:
        return 0
    here, ref = requests[i]["tokens"], requests[j]["tokens"]
    if requests[j].get("ambiguous") or requests[i].get("ambiguous"):
        return 0
    if bound == "prudent":
        recognised, cached = low > TTL_SECONDS and high <= LONG_TTL_SECONDS, ref["read"]
    else:
        recognised, cached = high > TTL_SECONDS and low <= LONG_TTL_SECONDS, ref["read"] + ref["write"]
    return min(here["write_5m"], max(0, cached - here["read"])) if recognised else 0


def lineage_rows(requests: Sequence[Mapping[str, Any]], grid: Mapping[str, Any], kind: str,
                 entry_expired: Mapping[str, bool]) -> tuple[list[dict[str, Any]], str | None]:
    """Per request: gaps, recognised expiries and, when the WHOLE lineage is priced, real cost and simulated cost under
    each bound (direction by kind). Second value: None when priced, else why not (``no_price_for_alias_on_day`` or
    ``price_depends_on_prompt_length``): the lineage is then unavailable in USD (never estimated) but its token
    figures stay."""
    gap = {b: gaps(requests, b) for b in BOUNDS}
    entries = [price_for(grid, "claude", q["model"], q["first"].astimezone(dt.timezone.utc).date().isoformat())
               for q in requests]
    why = "no_price_for_alias_on_day" if any(e is None for e in entries) else \
        "price_depends_on_prompt_length" if any("prompt_tokens_threshold" in e for e in entries if e) else None
    rows = []
    for i, req in enumerate(requests):
        t = req["tokens"]
        j = next((k for k in range(i - 1, -1, -1) if _refreshes(requests[k])), None)
        if req.get("ambiguous"):  # counters not trusted: in the chain, on neither side of any figure
            rows.append({"ambiguous": True, "tokens": t, "first": j is None, "gap": {b: gap[b][i] for b in BOUNDS}})
            continue
        row: dict[str, Any] = {"tokens": t, "first": j is None, "gap": {b: gap[b][i] for b in BOUNDS}, "real": None,
                               "sim": dict.fromkeys(BOUNDS), "expired": {}, "recovered": {}}
        rates = entries[i]["rates"] if why is None and entries[i] else None
        if rates is not None:
            row["real"] = request_costs(t, rates, False)[0]
        for b in BOUNDS:
            if kind in DIRECTION_A:
                expired = entry_expired[b] if gap[b][i] is None else gap[b][i] > TTL_SECONDS
                row["expired"][b] = expired
                if rates is not None:
                    row["sim"][b] = request_costs(t, rates, expired)[1]
            else:
                rho = recovered_reads(requests, i, j, gap["favourable"][i], gap["prudent"][i], b)
                row["recovered"][b] = rho
                if rates is not None:
                    row["sim"][b] = request_cost_long(t, rates, rho)
        if kind == SUBAGENT_1H and rates is not None:
            # PAT-134 decision input, NOT a replay rule: the same simulated cost with the entry read of a FIRST request
            # (the one the rule cannot place: its writer is not observable) counted as not expired, under each bound.
            row["sim_entry_kept"] = {b: request_costs(t, rates, False if j is None else row["expired"][b])[1]
                                     for b in BOUNDS}
        if kind == "subagent" and rates is not None:
            row["sim_no_recovery"] = request_cost_long(t, rates, 0)
            # upper envelope of what prefix sharing between logs could change: every cold-start write a 1-hour read
            row["cold_start_envelope"] = (t["write"] * (rates["cache_write_1h"] - rates["cache_read"]) / _MILLION
                                          if j is None else Decimal(0))
        rows.append(row)
    return rows, why


def _duration(w1h: int, w5m: int) -> str:
    return "none" if w1h + w5m == 0 else "1h" if w5m == 0 else "5m" if w1h == 0 else "mixed"


def summarise(rows: Sequence[Mapping[str, Any]], kind: str) -> dict[str, Any]:
    """Token and gap figures over ALL rows (no price needed); USD figures (``usd``) over the rows of priced lineages only,
    the same requests on both sides."""
    ambiguous = sum(1 for r in rows if r.get("ambiguous"))
    rows = [r for r in rows if not r.get("ambiguous")]
    total = {k: sum(r["tokens"][k] for r in rows) for k in ("input", "read", "write_1h", "write_5m", "output")}
    firsts = [r for r in rows if r["first"]]
    out: dict[str, Any] = {
        "requests_ambiguous_counters_on_neither_side": ambiguous, "requests": len(rows), "requests_with_a_predecessor": len(rows) - len(firsts), "tokens": total,
        "observed_cache_write_duration": _duration(total["write_1h"], total["write_5m"]),
        "first_request_cache": {"requests": len(firsts), "cache_read_tokens": sum(r["tokens"]["read"] for r in firsts),
                                "cache_write_tokens": sum(r["tokens"]["write"] for r in firsts)},
        "gaps": {}, "simulated_direction": DIRECTION[kind], "tokens_by_bound": {}}
    for b in BOUNDS:
        known = [r for r in rows if r["gap"][b] is not None]
        over = [r for r in known if r["gap"][b] > TTL_SECONDS]
        mid = [r for r in over if r["gap"][b] <= LONG_TTL_SECONDS]
        hist = dict.fromkeys((name for name, _ in BUCKETS), 0)
        for r in known:
            hist[_bucket(r["gap"][b])] += 1
        out["gaps"][b] = {
            "over_ttl": len(over), "share_over_ttl": _share(len(over), len(known)), "between_ttl_and_1h": len(mid),
            "over_1h": len(over) - len(mid),
            "cache_read_tokens_on_requests_after_over_ttl": sum(r["tokens"]["read"] for r in over),
            "cache_write_tokens_on_requests_after_over_ttl": sum(r["tokens"]["write"] for r in over),
            "max_gap_seconds": max((r["gap"][b] for r in known), default=None), "distribution_seconds": hist}
        if kind in DIRECTION_A:
            out["tokens_by_bound"][b] = {
                "requests_found_expired": sum(1 for r in known if r["expired"][b]),
                "first_requests_found_expired": sum(1 for r in rows if r["gap"][b] is None and r["expired"][b]),
                "cache_read_tokens_rewritten_at_5m": sum(r["tokens"]["read"] for r in rows if r["expired"][b])}
        else:
            out["tokens_by_bound"][b] = {
                "requests_with_recognised_expiry_rewrite": sum(1 for r in rows if r["recovered"][b]),
                "recovered_read_tokens": sum(r["recovered"][b] for r in rows)}
    priced = [r for r in rows if r["real"] is not None]
    if not priced:
        out["usd"] = "unavailable"
        return out
    real = sum((r["real"] for r in priced), Decimal(0))
    usd: dict[str, Any] = {"requests": len(priced), "real_usd": _usd(real), "bounds": {}}
    deltas = {}
    for b in BOUNDS:
        sim = sum((r["sim"][b] for r in priced), Decimal(0))
        deltas[b] = sim - real
        usd["bounds"][b] = {"simulated_usd": _usd(sim), "delta_usd": _usd(sim - real),
                            "delta_share_of_real": _share(sim - real, real)}
    if kind == "subagent":
        base = sum((r["sim_no_recovery"] for r in priced), Decimal(0)) - real
        usd["delta_usd_if_no_recognised_rewrite_is_real"] = _usd(base)
        usd["cold_start_write_tokens_not_modelled"] = sum(r["tokens"]["write"] for r in priced if r["first"])
        usd["cold_start_envelope_usd_not_modelled"] = _usd(sum((r["cold_start_envelope"] for r in priced), Decimal(0)))
        for b in BOUNDS:
            gain = base - deltas[b]  # what the recognised re-writes are worth
            usd["bounds"][b]["recognised_share_of_5m_writes"] = _share(
                sum(r["recovered"][b] for r in priced), sum(r["tokens"]["write_5m"] for r in priced))
            # share of the recognised re-writes that may be wrong before the delta stops being negative
            usd["bounds"][b]["wrong_share_of_recognised_rewrites_that_cancels_the_delta"] = (
                _share(-deltas[b], gain) if deltas[b] < 0 < gain else None)
    usd["result"] = _verdict(deltas["prudent"], deltas["favourable"])
    if kind == SUBAGENT_1H:
        kept = {b: sum((r["sim_entry_kept"][b] for r in priced), Decimal(0)) - real for b in BOUNDS}
        usd["entry_reads_not_expired"] = {
            "delta_usd": {b: _usd(kept[b]) for b in BOUNDS},
            "delta_usd_exact": {b: str(kept[b]) for b in BOUNDS},  # exact decimal strings: the decision reads these
            "first_requests_priced": sum(1 for r in priced if r["first"])}
    out["usd"] = usd
    return out


def _lineage_view(name: str, kind: str, alias: str, rows: Sequence[Mapping[str, Any]], why: str | None) -> dict[str, Any]:
    full = summarise(rows, kind)
    view = {"lineage": name, "kind": kind, "model": alias, "requests": full["requests"], "tokens": full["tokens"],
            "tokens_by_bound": full["tokens_by_bound"],
            "observed_cache_write_duration": full["observed_cache_write_duration"],
            "gaps_over_ttl": {b: full["gaps"][b]["over_ttl"] for b in BOUNDS}}
    if why is not None:
        return {**view, "usd": "unavailable", "usd_reason": why, "usd_provenance": PROVENANCE["lineage_without_usable_price"]}
    usd = full["usd"]
    return {**view, "usd": {"real_usd": usd["real_usd"], "result": usd["result"],
                            "bounds": {b: {"simulated_usd": e["simulated_usd"], "delta_usd": e["delta_usd"]}
                                       for b, e in usd["bounds"].items()}}}


def observed_one_hour_only(requests: Sequence[Mapping[str, Any]]) -> bool:
    """PAT-134: every cache write the lineage made (ambiguous requests aside) was in the 1-hour class, and there was one."""
    sound = [r["tokens"] for r in requests if not r.get("ambiguous")]
    return sum(t["write_1h"] for t in sound) > 0 and sum(t["write_5m"] for t in sound) == 0


def analyse_interactive(mains: Sequence[Path], grid: Path = GRID_PATH, until: dt.datetime | None = None,
                        subagent_1h_to_5m: bool = False) -> dict[str, Any]:
    price_grid = load_grid(grid)
    loaded: list[dict[str, Any]] = []  # one per designated session: lineages as (kind, alias, requests), refusals
    for main in mains:
        item: dict[str, Any] = {"lineages": [], "refused": [], "subagent_logs": 0, "returns": 0}
        for kind, path in (("main", main), *(("subagent", p) for p in discover_subagent_logs(main))):
            try:
                requests = read_host_requests(path, until, interactive=True)
                if not requests and kind == "subagent":
                    if not _has_record(path, until):  # created after the instant: not part of the frozen replay
                        continue
                    raise BreakdownError("no_request")
            except BreakdownError as exc:
                item["refused"].append({"kind": kind, "reason": str(exc)})
                item["subagent_logs"] += kind == "subagent"
                continue
            item["subagent_logs"] += kind == "subagent"
            if kind == "main":  # returns to an alias already used earlier, after another alias
                seen: set[str] = set()
                for k, q in enumerate(requests):
                    item["returns"] += bool(k and q["model"] != requests[k - 1]["model"] and q["model"] in seen)
                    seen.add(q["model"])
            item["lineages"].extend((kind, lin[0]["model"], lin) for lin in split_by_alias(requests))
        firsts = [lin[0]["first"] for kind, _, lin in item["lineages"] if kind == "main"]
        item["first"] = min(firsts) if firsts else None
        loaded.append(item)
    loaded.sort(key=lambda it: (it["first"] is None, it["first"] or dt.datetime.max.replace(tzinfo=dt.timezone.utc)))
    all_main = [lin for it in loaded for k, _, lin in it["lineages"] if k == "main"]
    sessions, views, unavailable, usable = [], [], [], []
    replayed_1h_to_5m = 0
    for number, item in enumerate(loaded, 1):
        sname = f"session-{number:02d}"
        rank = dict.fromkeys(KINDS, 0)
        ordered = sorted(item["lineages"], key=lambda t: (t[0] != "main", t[2][0]["first"], t[2][-1]["last"],
                                                         sum(q["tokens"]["output"] for q in t[2])))
        session_rows: dict[str, list[dict[str, Any]]] = {k: [] for k in ALL_KINDS}
        for kind, alias, requests in ordered:
            rank[kind] += 1
            name = f"{sname}/{kind}-{rank[kind]:02d}"
            group = SUBAGENT_1H if subagent_1h_to_5m and kind == "subagent" and observed_one_hour_only(requests) else kind
            replayed_1h_to_5m += group == SUBAGENT_1H
            expired = entry_expiry(requests[0], [lin for lin in all_main if lin[0]["first"] < requests[0]["first"]]) \
                if group in DIRECTION_A else {b: True for b in BOUNDS}
            rows, why = lineage_rows(requests, price_grid, group, expired)
            views.append(_lineage_view(name, group, alias, rows, why))
            if why is not None:
                unavailable.append({"lineage": name, "kind": group, "model": alias, "reason": why, "requests": len(rows),
                                    "provenance": PROVENANCE["lineage_without_usable_price"]})
            session_rows[group].extend(rows)
            usable.append((group, alias, rows))
        for refusal in item["refused"]:
            rank[refusal["kind"]] += 1
            unavailable.append({"lineage": f"{sname}/{refusal['kind']}-unreadable-{rank[refusal['kind']]:02d}",
                                "kind": refusal["kind"], "model": "unavailable", "reason": refusal["reason"],
                                "provenance": PROVENANCE["lineage_without_usable_price"]})
        sessions.append({
            "session": sname, "first_day_utc": None if item["first"] is None else item["first"].astimezone(dt.timezone.utc).date().isoformat(),
            "days_utc_with_requests": sorted({q["first"].astimezone(dt.timezone.utc).date().isoformat()
                                              for _, _, lin in item["lineages"] for q in lin}),
            "main_model_aliases": sorted({alias for kind, alias, _ in item["lineages"] if kind == "main"}),
            "main_alias_lineages": sum(1 for k, _, _ in item["lineages"] if k == "main"),
            "main_returns_to_an_earlier_alias": item["returns"],
            "subagent_logs_found": item["subagent_logs"],
            "by_kind": {k: summarise(session_rows[k], k) for k in ALL_KINDS if session_rows[k]}})
    return {
        "schema": "foundry.cache-ttl-replay.interactive.v1", "ttl_seconds": TTL_SECONDS, "long_ttl_seconds": LONG_TTL_SECONDS,
        "until": None if until is None else until.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "reading": "outside the frozen rule; API list prices are weights under a subscription, never a bill; "
                   "nothing here concerns the subscription quota (effect unknown); no generalisation beyond the sessions "
                   "replayed",
        "rule": "see the module docstring (PAT-133 EXTENSION): direction A main 1h -> 5m (PAT-132 in-session rule per "
                "alias lineage), direction B subagent 5m -> 1h (expiry re-write recognised from counters and gaps; prudent "
                "= certain expiry and only the prefix part the reference request had read; favourable = possible expiry "
                "and the whole prefix cached before the gap); gaps over 3600 s expire under both",
        "logical_role": "not derivable within FOUNDRY-ADR-0015 (agent type is not an allowed field): figures are by kind "
                        "(main / subagent) and by model alias",
        "provenance": PROVENANCE, "fields_read_beyond_adr_0015": FIELDS_BEYOND_ADR_0015,
        "price_grid": {"entries": [{"model": e["model"], "source": e["source"], "captured_at": e["captured_at"],
                                    "effective_from": e["effective_from"], "effective_to": e["effective_to"]}
                                   for e in price_grid["entries"]]},
        "sessions_designated": len(mains), "distinct_first_days_utc": sorted({s["first_day_utc"] for s in sessions if s["first_day_utc"]}),
        "sessions": sessions,
        "by_kind": {k: summarise([r for kk, _, rows in usable if kk == k for r in rows], k)
                    for k in ALL_KINDS if any(kk == k for kk, _, _ in usable)},
        "by_kind_and_model": {f"{k}/{a}": summarise([r for kk, aa, rows in usable if (kk, aa) == (k, a) for r in rows], k)
                              for k, a in sorted({(kk, aa) for kk, aa, _ in usable})},
        "lineages": views, "lineages_unavailable_in_usd_or_unreadable": unavailable,
        **({"subagent_1h_to_5m": {
            "rule": "a subagent lineage whose cache writes were all observed at 1 hour is replayed towards 5 minutes with "
                    "the rule of the main conversation (direction A, no new rule), as the group subagent_1h; the other "
                    "subagent lineages stay in direction B",
            "lineages_replayed": replayed_1h_to_5m}} if subagent_1h_to_5m else {})}


# ------------------------------------------------------------------------ PAT-134 observation rule

ROLLBACK_MIN_SESSIONS = 3
# Derived from CLAUDE_CACHE_TTL_1H_PINS (written for its single pin). ``None`` once that tuple is emptied (the rollback): the
# module must still import, because the PAT-132 / PAT-133 replay and the PAT-125 trial mode import it.
ROLLBACK_MODEL = CLAUDE_CACHE_TTL_1H_MODELS[0] if CLAUDE_CACHE_TTL_1H_MODELS else None


def rollback_decision(replay: Mapping[str, Any], model: str | None = ROLLBACK_MODEL) -> dict[str, Any]:
    """PAT-134 rule, read from the output of ``analyse_interactive(..., subagent_1h_to_5m=True)``.

    QUANTITY. The observed setting is 1 hour and the replay simulates 5 minutes, so a delta is simulated 5-minute cost minus
    real 1-hour cost: POSITIVE means the 1 hour was cheaper. The decision does NOT read ``usd.bounds.*.delta_usd`` or
    ``usd.result``: they reprice, as expired, the cache read of the FIRST request of each lineage (the replay cannot place the
    writer of that entry; with a main conversation of another model no earlier lineage of the alias exists, so the entry is
    counted expired under both bounds), although a prefix written by a sibling subagent less than 5 minutes earlier would
    also have been read at 5 minutes. That overestimates the cost of 5 minutes on both bounds. The decision reads instead
    ``usd.entry_reads_not_expired.delta_usd_exact`` (decimal strings, unrounded): the same simulation with those entry reads
    counted as not expired, a reading MORE SEVERE THAN ``delta_usd`` for keeping the 1 hour (never above it), not the most
    severe one in absolute terms (an addition to the output, not a new replay rule). Of its two bounds the SMALLER is read:
    in practice the ``favourable`` one, which simulates fewer expirations, so a smaller 5-minute cost and a smaller delta;
    ``prudent`` simulates more expirations and is the bound most favourable to keeping the 1 hour. The threshold is therefore
    NOT on the prudent bound; the minimum is taken so the decision does not depend on that ordering. Since that quantity is
    never above ``delta_usd``, ``keep`` implies ``usd.result == net_loss`` and not the converse.

    LIMITS OF THE DECISION: two biases towards ``keep`` remain, under BOTH bounds. (a) On an expiry inside a lineage (gap
    over 300 s) the WHOLE read is repriced as a 5-minute write, although the part shared with a sibling subagent of the same
    profile (tools and system prompt) that was active less than 5 minutes earlier would have stayed readable at 5 minutes:
    the simulated 5-minute cost is over-estimated. (b) The ``favourable`` gap is not a rigorous lower bound of the real gap
    (the instant a request is sent is not observable), so an expiry can be counted that did not happen. The threshold has
    no margin (strictly positive), so either bias can turn a marginal ``roll_back`` into ``keep``: a ``keep`` on a delta
    close to zero is not established by this rule.

    DECISION. ``keep`` only if that minimum is strictly positive (unrounded: a delta that rounds to 0.0 in ``delta_usd``
    still counts); ``roll_back`` if it is zero or negative, or if no lineage of ``model`` was observed at 1 hour (the field has
    no observable effect; ASSUMED ASYMMETRY: this holds even when the sessions ran no subagent of ``model`` at all, whereas one
    or two contributing sessions give ``unknown``). ``unknown`` if no model carries the field (``model`` is ``None``: the
    default once ``CLAUDE_CACHE_TTL_1H_PINS`` is empty, nothing is left to decide); if the replay was not made with the
    option; if fewer than ``ROLLBACK_MIN_SESSIONS``
    designated sessions were replayed, or fewer contribute a priced lineage of ``model`` observed at 1 hour; if any such
    lineage is not priced; or if a lineage of ANOTHER model (an unmodified profile) is observed at 1 hour, because an
    outside setting then changed the measure. Only subagent lineages whose writes were ALL in the 1-hour class are decision
    inputs. The subscription quota is not an input.

    OUTPUT. ``decision`` (``keep``, ``roll_back``, ``unknown``) and ``reason``, one of ``no_model_carries_the_field``,
    ``replay_not_made_with_subagent_1h_to_5m``, ``fewer_designated_sessions_than_the_minimum``,
    ``a_lineage_of_another_model_is_observed_at_1h``, ``no_lineage_of_the_model_observed_at_1h``,
    ``a_lineage_observed_at_1h_is_not_priced``, ``fewer_contributing_sessions_than_the_minimum`` (with
    ``contributing_sessions``) and ``least_favourable_entry_reads_not_expired_delta`` (with ``contributing_sessions`` and
    ``delta_usd_entry_reads_not_expired``, the minimum read, an exact decimal string). No CLI: it is called from Python on
    the parsed JSON of a replay output (the call is written in the PAT-134 page)."""
    if model is None:
        return {"decision": "unknown", "reason": "no_model_carries_the_field"}
    if "subagent_1h_to_5m" not in replay:
        return {"decision": "unknown", "reason": "replay_not_made_with_subagent_1h_to_5m"}
    if len(replay.get("sessions", ())) < ROLLBACK_MIN_SESSIONS:
        return {"decision": "unknown", "reason": "fewer_designated_sessions_than_the_minimum"}
    one_hour = [v for v in replay.get("lineages", ()) if v.get("kind") == SUBAGENT_1H]
    if any(v.get("model") != model for v in one_hour):
        return {"decision": "unknown", "reason": "a_lineage_of_another_model_is_observed_at_1h"}
    if not one_hour:
        return {"decision": "roll_back", "reason": "no_lineage_of_the_model_observed_at_1h"}
    if any(v.get("usd") == "unavailable" for v in one_hour):
        return {"decision": "unknown", "reason": "a_lineage_observed_at_1h_is_not_priced"}
    contributing = {v["lineage"].split("/", 1)[0] for v in one_hour}
    if len(contributing) < ROLLBACK_MIN_SESSIONS:
        return {"decision": "unknown", "reason": "fewer_contributing_sessions_than_the_minimum",
                "contributing_sessions": len(contributing)}
    group = replay["by_kind_and_model"][f"{SUBAGENT_1H}/{model}"]
    exact = group["usd"]["entry_reads_not_expired"]["delta_usd_exact"]
    delta = min(Decimal(exact[b]) for b in BOUNDS)
    return {"decision": "keep" if delta > 0 else "roll_back", "reason": "least_favourable_entry_reads_not_expired_delta",
            "delta_usd_entry_reads_not_expired": str(delta), "contributing_sessions": len(contributing)}


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
    parser.add_argument("--ledger", action="append", metavar="LEDGER",
                        help="ledger-<campaign>.jsonl of a finished campaign; repeatable (required without --host-session)")
    parser.add_argument("--session-logs-dir", help="host session logs (<dir>/*/<session>.jsonl), off repo "
                                                   "(required with --ledger)")
    parser.add_argument("--host-session", action="append", metavar="MAIN_LOG",
                        help="PAT-133: main log <dir>/<session>.jsonl of an interactive host session; its subagent logs "
                             "are the *.jsonl under <dir>/<session>/subagents/; repeatable; replaces --ledger")
    parser.add_argument("--until", default=None, metavar="ISO_INSTANT",
                        help="with --host-session: ignore every record after this instant (e.g. 2026-10-09T22:13:00Z)")
    parser.add_argument("--subagent-1h-to-5m", action="store_true",
                        help="PAT-134, with --host-session: replay the subagent lineages whose cache writes were all "
                             "observed at 1 hour towards 5 minutes with the rule of the main conversation (direction A), "
                             "as the group subagent_1h; default off (outputs unchanged)")
    parser.add_argument("--grid", default=str(GRID_PATH))
    parser.add_argument("--out", default=None, help="write the aggregates here (never overwrites)")
    args = parser.parse_args(argv)
    if args.host_session:
        if args.ledger or args.session_logs_dir:
            parser.error("--host-session replaces --ledger and --session-logs-dir")
    else:
        missing = [flag for flag, value in (("--ledger", args.ledger), ("--session-logs-dir", args.session_logs_dir))
                   if not value]
        if missing:  # the message argparse gave when both were declared required
            parser.error(f"the following arguments are required: {', '.join(missing)}")
        if args.until:
            parser.error("--until needs --host-session")
        if args.subagent_1h_to_5m:
            parser.error("--subagent-1h-to-5m needs --host-session")
    try:
        if args.host_session:
            until = _instant(args.until) if args.until else None
            doc = analyse_interactive([Path(p) for p in args.host_session], Path(args.grid), until,
                                         args.subagent_1h_to_5m)
        else:
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
