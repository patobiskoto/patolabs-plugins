"""Offline export of attempt, review-round and escalation records in the DevHub format (PLP-319).

Builds the bytes of one JSON document from local state that other modules already read:
the review turns (``review_turns``), the PLP-316 invocation record (``invocation_log``), the
per-invocation host-log tokens (``token_attribution``), the price grid (``cost_attribution``,
FOUNDRY-ADR-0015) and the escalation ledger (``escalation``). Read side only: nothing is
written here (the CLI verb writes the single file the user names), no network, tracker, clock or
git call, no routing or gate input. Same inputs, same bytes. The records follow contract
``devhub-observations.v2``; the provenance of every value is kept beside the records, never
inside them. See ``docs/kpi-export.md``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from foundry import campaign_effects, cost_attribution, invocation_log, registry, review_turns, token_attribution
from foundry.escalation import EscalationStore, _strict_json_loads
from foundry.routing import RoutingConfigError
from foundry.routing_facades import CLAUDE_CACHE_TTL_1H_PINS

EXPORT_SCHEMA = "foundry-kpi-export.v1"
RECORD_SCHEMA = "devhub-observations.v2"
PRODUCER = "foundry-kpi-export"
BATCH_SIZE = 100  # DevHub accepts 1 to 100 records per batch
_KIND_ORDER = {"attempt": 0, "review_round": 1, "escalation": 2}
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,119}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_ISSUE = re.compile(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*\Z")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
ESCALATION_ROLES = ("coordinator", "implementer", "reviewer", "scout", "architect")
ESCALATION_SIGNALS = ("test_red", "review_blocking", "review_blocking_after_fix")
# Host task notification word -> (status, failure_class). `stopped` and `killed` are not mapped: what ended the
# call is not proved (PLP-358, docs/kpi-export.md). A `failed` notice does not name the class.
_NOTICE_STATUS = {"completed": ("completed", "none"), "failed": ("failed", "unknown")}
_TOKEN_FIELDS = (("input", "input_tokens"), ("cached_input", "cached_input_tokens"),
                 ("cache_write_input", "cache_write_input_tokens"), ("output", "output_tokens"))


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def _epoch_ms(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return (parsed - _EPOCH) // timedelta(milliseconds=1)


def _id(value: object) -> str | None:
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


class _Record:
    """One record body plus its value-by-value provenance (never part of the body)."""

    def __init__(self, issue: str, kind: str, attempt_id: str, source_schema: str, material: dict):
        self.issue, self.kind, self.attempt_id = issue, kind, attempt_id
        self.fingerprint = hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()
        self.source_schema = source_schema
        self.fields: dict[str, str] = {}
        self.nulls: dict[str, str] = {}
        self.body: dict = {}

    def put(self, path: str, value: object, source: str, reason: str | None = None) -> object:
        if value is None:
            self.nulls[path] = reason or "unrecorded"
        else:
            self.fields[path] = source
        return value

    def finish(self, started_at: int, ended_at: int | None, observed_at: int, attribution: str,
               time_source: str, body: dict) -> dict:
        record_id = "kpi-" + _digest(self.issue, self.kind, self.attempt_id, self.fingerprint)
        record = {
            "schema_version": RECORD_SCHEMA, "record_id": record_id, "kind": self.kind,
            "anchor": {"type": "issue", "command_id": None, "issue_id": self.issue},
            "attempt_id": self.attempt_id, "attribution": attribution, "revision": 1,
            "supersedes_id": None, "started_at": started_at, "ended_at": ended_at,
            "observed_at": observed_at,
            "source": {"system": "foundry", "producer": PRODUCER, "schema": self.source_schema,
                       "fingerprint": self.fingerprint},
            **body,
        }
        self.fields.update({
            "record_id": "sha256 of issue, kind, attempt_id and source.fingerprint",
            "anchor": "constant: the issue anchor (no command plan is recorded locally)",
            "attempt_id": "derived by sha256 (see docs/kpi-export.md); no source identifier is emitted",
            "attribution": "fixed by the record kind and its source (docs/kpi-export.md)",
            "revision": "constant 1 (no revision exists in the sources)",
            "started_at": time_source, "observed_at": time_source,
            "source.fingerprint": "sha256 of the canonical source material (docs/kpi-export.md)",
        })
        if ended_at is not None:
            self.fields["ended_at"] = time_source
        else:
            self.nulls["ended_at"] = "no end time is recorded in the source"
        self.nulls["supersedes_id"] = "constant null (revision 1): the export cannot know what was published"
        self.body = record
        return record

    def provenance(self) -> dict:
        return {"from": dict(sorted(self.fields.items())), "null": dict(sorted(self.nulls.items()))}


def _amount(grid: dict, host: str, model: str | None, at: str, tokens: dict | None,
            requests: list | None = None, lower_bound: int = 0,
            causes: dict | None = None) -> tuple[dict | None, str | None]:
    """An ``estimated`` amount from the frozen price grid, or None with a reason."""
    if tokens is None:
        return None, "no_tokens"
    if model is None:
        return None, "no_configured_model"
    day = at[:10]
    try:
        price = cost_attribution.price_for(grid, host, model, day)
    except cost_attribution.CostAttributionError:
        return None, "price_grid_ambiguous"
    if price is None:
        return None, "no_price_for_model_and_date"
    if "context_threshold_tokens" in price:
        if requests is None:
            return None, "threshold_pricing_needs_per_request_tokens"
        total = sum(Decimal(str(request[name]))
                    * Decimal(str(price["over_threshold_rates" if cost_attribution.over_threshold(request, price)
                                   else "rates"][key]))
                    for request in requests for key, name in _TOKEN_FIELDS)
    else:
        total = sum(Decimal(str(tokens[name])) * Decimal(str(price["rates"][key]))
                    for key, name in _TOKEN_FIELDS)
    version = "pricing-v1-" + hashlib.sha256(_canonical(price).encode("utf-8")).hexdigest()[:12]
    # The host-log reader sums 5m and 1h cache writes (``cost_attribution._normalized_claude_usage``) and the
    # grid has the 5m write rate only; the profiles of these pins ask for 1h writes (PAT-134), so a write
    # on one of them may be underpriced: the amount is then a lower bound, ``partial`` (PLP-14).
    one_hour = (host == "claude" and price["model"] in CLAUDE_CACHE_TTL_1H_PINS
                and tokens["cache_write_input_tokens"] > 0)
    if causes is not None:  # the caller words the provenance from the cause, not from ``completeness``
        causes["one_hour"] = one_hour
    return {"amount_micros": str(total.quantize(Decimal(1), rounding=ROUND_HALF_UP)),
            "currency": "USD", "unit": "micros", "method": "estimated",
            "completeness": "partial" if one_hour or lower_bound else "complete", "rate_version": version}, None


def _combine(priced: list[dict], partial: bool = False) -> dict:
    """One amount from the amounts of several (model, day) groups: micros summed, ``rate_version`` of the one
    group or a digest of the sorted versions; ``partial`` when asked or when any group is."""
    versions = [a["rate_version"] for a in priced]
    return {"amount_micros": str(sum(int(a["amount_micros"]) for a in priced)), "currency": "USD",
            "unit": "micros", "method": "estimated",
            "completeness": "partial" if partial or any(a["completeness"] == "partial" for a in priced) else "complete",
            "rate_version": versions[0] if len(set(versions)) == 1 else
            "pricing-v1-" + hashlib.sha256(_canonical(sorted(set(versions))).encode("utf-8")).hexdigest()[:12]}


def _served_amount(grid: dict, host: str | None, usage: dict) -> tuple[dict | None, str | None, list]:
    """Price an attempt whose requested model has no price by the model(s) its host log served (PLP-358).

    ``usage`` is the ``per_invocation`` entry. The model is the one the log requests name (observed), never
    deduced from the requested alias. Returns (amount, reason, [(model, day)]); one group without a price makes
    the whole amount null, as for the coordinator record.
    """
    groups = sorted(usage.get("groups", {}).items())
    priced = []
    for (model, day), group in groups:
        amount, why = _amount(grid, host, _id(model), day, group["tokens"],
                              group["token_requests"] if usage["request_granularity"] else None, group["lower_bound"])
        if amount is None:
            return None, why, []
        priced.append(amount)
    return (_combine(priced), None, [list(key) for key, _ in groups]) if priced else (None, None, [])


def _attempts(issue: str, records: dict | None, usage: dict | None, grid: dict,
              completions: dict | None = None) -> tuple[list, list]:
    if records is None:
        return [], [{"issue": issue, "kind": "attempt", "reason": "invocation_log_unreadable"}]
    out, omitted = [], []
    for item in (r for r in records.values() if r.get("issue") == issue):
        started = _epoch_ms(item.get("at"))
        if started is None:
            omitted.append({"issue": issue, "kind": "attempt", "reason": "no_recorded_time"})
            continue
        role = "corrector" if item["role"] == "implementer" and item.get("correction") is True else item["role"]
        raw_status = status = item.get("status")
        status, failure = (("completed", "none") if status == "completed"
                           else ("failed", "host") if status == "failed" else ("unknown", "unknown"))
        log_source = "invocation record (invocation_log.py)"
        notice = (completions or {}).get(item["id"]) if item.get("host") == "claude" and status == "unknown" else None
        ended = _epoch_ms(notice[1].isoformat()) if notice else None
        if status != "unknown":
            status_source = log_source + " (last completion event)"
        elif raw_status == "unknown":  # written only by hooks/observe_agent.py for a background launch
            status_source = (log_source + ": background launch whose end the PostToolUse hook "
                             "does not observe (PLP-329)")
        else:  # `routed`: no completion event, every Codex attempt included
            status_source = log_source + ": no completion event"
        if notice and notice[0] in _NOTICE_STATUS and ended is not None and ended >= started:
            status, failure = _NOTICE_STATUS[notice[0]]
            status_source = ("host notification of the background Agent call in the main session log "
                             "(token_attribution.claude_completions, queue-operation status '%s'); observed" % notice[0])
        else:
            ended = None
            if notice:
                status_source += "; the host notification '%s' is not mapped to a status" % notice[0]
            elif item.get("host") == "claude":
                status_source += "; no host notification of the call in the session logs " + (
                    "given" if completions is not None else "(none given)")
            status_source += "; no end time is emitted"
        material = {k: item.get(k) for k in invocation_log._FIELDS if k != "event"}
        attempt_id = "at-" + _digest("attempt", issue, item["id"])[:32]
        tokens = None
        if usage is not None and item["id"] in usage:
            tokens = {name: usage[item["id"]]["tokens"][name] for _, name in _TOKEN_FIELDS}
        material["tokens"] = tokens
        bounded = usage[item["id"]].get("lower_bound_requests", 0) if tokens is not None else 0
        if bounded:  # PLP-344: only an affected record changes its fingerprint
            material["tokens_lower_bound_requests"] = bounded
        reported = usage[item["id"]].get("parent_reported_requests", 0) if tokens is not None else 0
        if reported:  # PLP-345: the last request's counters come from the parent tool result
            material["tokens_parent_reported_requests"] = reported
        if ended is not None:  # PLP-358: only an attempt with a mapped host notification changes its fingerprint
            material["completion"] = [notice[0], ended]
        model = _id(item.get("model_requested"))
        seen = usage[item["id"]] if tokens is not None else {}
        requests = seen["token_requests"] if seen.get("request_granularity") is True else None
        causes: dict = {}
        amount, why = _amount(grid, item.get("host"), model, item["at"], tokens, requests, bounded, causes)
        served, by_served = None, []
        if why == "no_price_for_model_and_date" and seen.get("groups"):
            # PLP-358: the requested model (a short alias, for example) has no price; the model the host log
            # served is observed, so it prices the attempt. The alias is never read as a version.
            priced, why_served, by_served = _served_amount(grid, item.get("host"), seen)
            if priced is not None:
                amount, why = priced, None
                names = {_id(m) for m, _ in seen["groups"]}
                served = names.pop() if len(names) == 1 and None not in names else None
                material["priced_by_served_model"] = by_served
                causes["one_hour"] = amount["completeness"] == "partial" and not bounded
            else:
                why = why_served or why
        rec = _Record(issue, "attempt", attempt_id, "foundry-invocation-log", material)
        log, host = "invocation record (invocation_log.py)", item.get("host")
        no_tokens = ("no session log given" if usage is None else "invocation absent from the given session logs")
        body = {
            "role": rec.put("role", role, log),
            "host": rec.put("host", host if host in ("claude", "codex") else None, log, "host_not_claude_or_codex"),
            "tier": rec.put("tier", _id(item.get("tier")), log, "tier_not_recorded"),
            "effort": rec.put("effort", _id(item.get("effort")), log, "effort_not_recorded"),
            "model": {"configured": rec.put("model.configured", model, log + " (model requested)", "model_not_recorded"),
                      "served": rec.put("model.served", served, "host log of the invocation: model of its requests "
                                "(token_attribution), emitted where it prices an attempt whose requested model has no price",
                                "the served model is not recorded in the invocation record; it is emitted only "
                                "where it prices an attempt whose requested model has no price (PLP-358)")},
            "status": rec.put("status", status, status_source),
            "failure_class": rec.put("failure_class", failure, status_source),
            "tokens": {
                "input": None, "cached_input": None, "cache_write_input": None, "output": None,
                "reasoning_output": rec.put("tokens.reasoning_output", None, "",
                                            "per-invocation attachment carries billing keys only"),
            },
            "amount": rec.put("amount", amount, "pricing-v1.json grid applied to the tokens (cost_attribution.price_for)"
                              + ("; the requested model has no price, so the grid is applied per (model, day) of the "
                                 "requests of the host log, the model it served (PLP-358)" if by_served else "")
                              + "; API-equivalent estimate, not an invoice"
                              + ("; partial: a 1h cache write of this model would be priced at the 5m rate, "
                                 "so the amount is a lower bound" if amount and causes.get("one_hour")
                                 else "")
                              + ("; partial: the tokens are a lower bound (see tokens.*), so the amount is too"
                                 if amount and bounded else ""), why),
        }
        for key, name in _TOKEN_FIELDS:
            body["tokens"][key] = rec.put(
                f"tokens.{key}", None if tokens is None else tokens[name],
                "host log slice of the invocation (token_attribution, method exact)"
                + (f"; lower bound: {bounded} request(s) of the log have no terminal snapshot, counted with "
                   "the largest value observed" if bounded else "")
                + (f"; {reported} request(s) of the log, the last one of the subagent, take their counters from the "
                   "parent tool result (toolUseResult.usage, host_reported)" if reported else ""), no_tokens)
        corrector = role == "corrector"
        out.append((rec, rec.finish(started, ended, started, "approximate" if corrector else "exact",
                                    "at of the routed event of the invocation record", body)))
        if ended is not None:
            rec.fields["ended_at"] = ("observed: timestamp of the host notification that the call stopped (an upper "
                                      "bound of its end), main session log")
        if corrector:
            rec.fields["attribution"] = "approximate: the corrector role is a claim made at routing (correction flag)"
    return out, omitted


def _coordinators(issue: str, coordinator: dict | None, windows: list, grid: dict) -> tuple[list, list]:
    """Records of the coordinator session cost of ``issue`` (PLP-357, ``docs/kpi-export.md``).

    One record per (issue, host session): the requests of the session inside the one ``issue start`` ->
    ``issue merge`` window of the issue (method ``window``) that no other window, closed or open, also contains.
    Requests in two or more windows are in no record (``omitted``, never split); requests outside any window of the
    issue are not this issue's and are not mentioned.
    """
    if coordinator is None:
        return [], []
    out, omitted = [], []
    mine = [w for w in windows if w["issue"] == issue]
    omitted.extend({"issue": issue, "kind": "attempt", "role": "coordinator", "reason": "coordinator_window_unclosed"}
                   for w in mine if not w["closed"])
    if not mine:
        omitted.append({"issue": issue, "kind": "attempt", "role": "coordinator",
                        "reason": "no_coordinator_window_in_the_given_logs"})
    for (named, host, session), seen in sorted(coordinator.items(), key=lambda kv: (kv[0][0], kv[0][1], _digest(kv[0][2]))):
        if named != issue:
            continue
        if seen["overlap_requests"]:
            omitted.append({"issue": issue, "kind": "attempt", "role": "coordinator", "requests": seen["overlap_requests"],
                            "reason": "coordinator_requests_in_overlapping_windows"})
        if not seen["requests"]:
            continue
        started, ended = _epoch_ms(seen["first"].isoformat()), _epoch_ms(seen["last"].isoformat())
        groups = sorted(seen["groups"].items())
        digest = _digest("coordinator", issue, host, session)[:32]
        material = {"issue": issue, "host": host, "session": digest, "requests": seen["requests"],
                    "first": started, "last": ended, "overlap_requests": seen["overlap_requests"],
                    "lower_bound_requests": seen["lower_bound"],
                    "groups": [[model, day, g["tokens"], g["lower_bound"]] for (model, day), g in groups]}
        rec = _Record(issue, "attempt", "at-" + digest, "token-attribution.v1", material)
        priced, why = [], None
        for (model, day), group in groups:
            amount, why = _amount(grid, host, _id(model), day, group["tokens"],
                                  group["token_requests"] if seen["granular"] else None, group["lower_bound"])
            if amount is None:
                break
            priced.append(amount)
        amount = None
        if why is None and priced:
            amount = _combine(priced, bool(seen["overlap_requests"] or seen["lower_bound"]))
        models = {_id(m) for (m, _), _g in groups}
        served = models.pop() if len(models) == 1 and None not in models else None
        log, how = "token_attribution (routing token-attribution)", "coordinator requests of the issue window"
        body = {
            "role": rec.put("role", "coordinator", "constant: the main host session that drives the issue"),
            "host": rec.put("host", host if host in ("claude", "codex") else None, log + " host of the log",
                            "host_not_claude_or_codex"),
            "tier": rec.put("tier", None, "", "the coordinator tier is not recorded locally"),
            "effort": rec.put("effort", None, "", "the coordinator effort is not recorded locally"),
            "model": {"configured": rec.put("model.configured", None, "", "the coordinator model is not routed by Foundry"),
                      "served": rec.put("model.served", served, log + ": model of the host log requests",
                                        "no_single_valid_model_in_the_window")},
            "status": rec.put("status", "unknown", "constant: no completion of a coordinator session is observed"),
            "failure_class": rec.put("failure_class", "unknown", "constant: see status"),
            "tokens": {"input": None, "cached_input": None, "cache_write_input": None, "output": None,
                       "reasoning_output": rec.put("tokens.reasoning_output", None, "",
                                                   "per-invocation attachment carries billing keys only")},
            "amount": rec.put("amount", amount, "pricing-v1.json grid applied per model and day to the window requests "
                              "(cost_attribution.price_for); API-equivalent estimate, not an invoice"
                              + ("; partial: " + ", ".join(c for c, on in (
                                  ("requests of the session in overlapping windows are not counted", seen["overlap_requests"]),
                                  ("some requests are a lower bound", seen["lower_bound"]),
                                  ("a 1h cache write may be priced at the 5m rate",
                                   any(a["completeness"] == "partial" for a in priced) and not seen["lower_bound"])) if on)
                                 if amount and amount["completeness"] == "partial" else ""), why),
        }
        for key, name in _TOKEN_FIELDS:
            body["tokens"][key] = rec.put(f"tokens.{key}", seen["tokens"][name], log + " (method window): " + how
                                          + ("; lower bound: %d request(s) have no terminal snapshot"
                                             % seen["lower_bound"] if seen["lower_bound"] else ""))
        out.append((rec, rec.finish(started, ended, ended, "window",
                                    "host log request times: first and last request of the window", body)))
        rec.fields["attribution"] = "window: " + how + " (issue start to issue merge, one window of the issue)"
        rec.fields["ended_at"] = "observed: time of the last request of the window in the host log"
    return out, omitted


def _implementer_model(issue: str, head: str, base: str | None, repository: str, state_dir) -> tuple[str | None, str]:
    found = invocation_log.invocations_for_diff(issue, head, base, repository=repository, state_dir=state_dir)
    if found is None:
        return None, "no_reviewer_record_for_head"
    produced = found["invocations"]
    if not produced:
        return None, "no_implementer_invocation_in_window"
    models = {_id(r.get("model_requested")) for r in produced}
    if len(models) != 1 or None in models:
        return None, "implementer_models_differ_or_unrecorded"
    return models.pop(), ""


def _pull_requests(journal: Path) -> list[dict] | None:
    try:
        return [r for r in campaign_effects.read_attempts(journal)
                if r.get("event") == "started" and r.get("kind") == "review"
                and isinstance(r.get("fingerprint"), dict)]
    except (campaign_effects.AttemptJournalInvalid, OSError, ValueError):
        return None


def _pr_number(started: list[dict] | None, issue: str, head: str) -> tuple[int | None, str]:
    if started is None:
        return None, "attempts_journal_unreadable"
    # a list, not a set: a journal value that is not hashable must not raise (PLP-321)
    numbers = [r["fingerprint"].get("pr") for r in started
               if r.get("ticket") == issue and r["fingerprint"].get("pr_head") == head]
    numbers = [n for n in numbers if n is not None]
    if not numbers:
        return None, "no_campaign_record_of_a_pull_request"
    if not all(type(n) is int and n >= 1 for n in numbers) or len(set(numbers)) != 1:
        return None, "ambiguous"
    return numbers[0], ""


def _rounds(issue: str, repository: str, state_dir, journal: Path) -> tuple[list, list]:
    turns = review_turns.report(issue, repository, state_dir=state_dir)["turns"]
    started_reviews = _pull_requests(journal)
    out, omitted = [], []
    previous = None
    for turn in turns:
        before, previous = previous, turn
        unrecorded = dict(turn["unrecorded"])
        review_type, type_source = turn["type"], "review_turns type (campaign attempts journal)"
        if review_type is None:
            if turn["rank"] == 1 or (before is not None and before["verdict"] == "blocked"):
                review_type = "full"
                type_source = ("deduced: the first review and the review after a blocking round are full "
                               "(AGENTS.md#R9)")
            else:
                omitted.append({"issue": issue, "kind": "review_round", "rank": turn["rank"],
                                "reason": "review_type_unrecorded:" + unrecorded.get("type", "unknown")})
                continue
        completed = _epoch_ms(turn["completed_at"])
        if completed is None:
            omitted.append({"issue": issue, "kind": "review_round", "rank": turn["rank"],
                            "reason": "no_recorded_time"})
            continue
        started = _epoch_ms(turn["started_at"])
        base = turn["base"] if _SHA.fullmatch(turn["base"]) else None
        implementer, why_impl = _implementer_model(issue, turn["head"], base, repository, state_dir)
        pr, why_pr = _pr_number(started_reviews, issue, turn["head"])
        attempt_id = "rr-" + _digest("review_round", issue, turn["head"], turn["rank"])[:32]
        material = {"issue": issue, "head": turn["head"], "base": base, "rank": turn["rank"],
                    "review_type": review_type, "verdict": turn["verdict"], "criteria": turn["criteria"],
                    "criteria_pass": turn["criteria_pass"], "remarks": turn["remarks"],
                    "started_at": turn["started_at"], "completed_at": turn["completed_at"],
                    "implementer_model": implementer, "reviewer_model": _id(turn["reviewer_model"]),
                    "pr_number": pr}
        rec = _Record(issue, "review_round", attempt_id, review_turns.SCHEMA, material)
        src = "review_turns (routing review-turns): "
        body = {
            "head_sha": rec.put("head_sha", turn["head"], src + review_turns.SOURCES["head"]),
            "rank": rec.put("rank", turn["rank"], src + review_turns.SOURCES["rank"]),
            "review_type": rec.put("review_type", review_type, type_source),
            "verdict": rec.put("verdict", "mergeable" if turn["verdict"] == "validated" else "blocked",
                               src + review_turns.SOURCES["verdict"]),
            "ac_total": rec.put("ac_total", turn["criteria"], src + review_turns.SOURCES["criteria"]),
            "ac_pass": rec.put("ac_pass", turn["criteria_pass"], src + review_turns.SOURCES["criteria_pass"]),
            "remark_count": rec.put("remark_count", turn["remarks"], src + review_turns.SOURCES["remarks"],
                                    unrecorded.get("remarks")),
            "implementer_model": rec.put("implementer_model", implementer,
                                         "implementer invocations of the window before the first reviewer "
                                         "invocation for this head (invocation_log.invocations_for_diff)", why_impl),
            "reviewer_model": rec.put("reviewer_model", _id(turn["reviewer_model"]),
                                      src + review_turns.SOURCES["reviewer_model"], unrecorded.get("reviewer_model")),
            "base_sha": rec.put("base_sha", base, src + review_turns.SOURCES["base"], "base_not_a_40_hex_sha"),
            "pr_number": rec.put("pr_number", pr, "campaign attempts journal: pr of the started review record whose "
                                 "pr_head is this head", why_pr),
        }
        out.append((rec, rec.finish(started if started is not None else completed, completed, completed, "exact",
                                    "ledger times of the review (review_turns started_at/completed_at)", body)))
        if started is None:
            rec.fields["started_at"] = "deduced: equal to completed_at, the ledger records no claim start time"
    return out, omitted


def _escalations(issue: str, repository: str, state_dir, records: dict | None) -> tuple[list, list]:
    store = EscalationStore(repository, state_dir=state_dir)
    path = store.directory / f"{issue}.json"
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return [], []
    except OSError:
        return [], [{"issue": issue, "kind": "escalation", "reason": "escalation_ledger_unreadable"}]
    try:
        # Private calls kept on purpose (PLP-321): the public ``EscalationStore.status`` does not return
        # the failure receipts or their ``decided_at``, and a new public reader is outside this change.
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            state = store._normalize_state(_strict_json_loads(handle.read()), issue)
    except (OSError, ValueError, RecursionError, RoutingConfigError):
        return [], [{"issue": issue, "kind": "escalation", "reason": "escalation_ledger_unreadable"}]
    found: dict[tuple[str, int], dict | None] = {}
    decided: dict[tuple[str, int], int] = {}  # observed decision time (receipt decided_at), when written
    for key in sorted(state["failure_receipts"]):
        receipt = state["failure_receipts"][key]
        decision = receipt["decision"]
        if decision.get("action") != "escalated":
            continue
        item = {"role": decision["role"], "signal": decision["signal"], "from_tier": decision["initial_tier"],
                "to_tier": decision["final_tier"], "rank": decision["role_escalations"]}
        stamp = _epoch_ms(receipt.get("decided_at"))
        slot = (item["role"], item["rank"])
        found[slot] = item if found.get(slot, item) == item else None
        if stamp is not None:
            decided[slot] = min(stamp, decided.get(slot, stamp))
    out, omitted = [], []
    for (role, rank), item in sorted(found.items()):
        label = {"issue": issue, "kind": "escalation", "role": role, "rank": rank}
        if (item is None or role not in ESCALATION_ROLES or item["signal"] not in ESCALATION_SIGNALS
                or type(rank) is not int or rank < 1 or _id(item["to_tier"]) is None):
            omitted.append({**label, "reason": "receipt_ambiguous_or_outside_the_format"})
            continue
        observed = decided.get((role, rank))
        first = None if observed is not None else next((r for r in (records or {}).values()
                      if r.get("issue") == issue and r.get("role") == role and r.get("tier") == item["to_tier"]
                      and _epoch_ms(r.get("at")) is not None), None)
        if first is None and observed is None:
            omitted.append({**label, "reason": "no_recorded_time"})
            continue
        when = observed if observed is not None else _epoch_ms(first["at"])
        rec = _Record(issue, "escalation", "es-" + _digest("escalation", issue, role, rank)[:32],
                      "escalation-ledger.v1", {"issue": issue, **item})
        src = "escalation ledger failure receipt (escalation.py, action escalated): "
        body = {
            "role": rec.put("role", role, src + "role"),
            "signal": rec.put("signal", item["signal"], src + "decision.signal"),
            "from_tier": rec.put("from_tier", _id(item["from_tier"]), src + "decision.initial_tier", "tier_not_recorded"),
            "to_tier": rec.put("to_tier", item["to_tier"], src + "decision.final_tier"),
            "rank": rec.put("rank", rank, src + "decision.role_escalations"),
        }
        time_source = ("observed: decided_at of the escalation ledger failure receipt (escalation.py), written when "
                       "the decision was recorded" if observed is not None else
                       "deduced estimate (neither a lower nor an upper bound): at of the first routed "
                       "invocation of the role in this issue at to_tier (the receipt has no decided_at)")
        out.append((rec, rec.finish(when, None, when, "exact", time_source, body)))
    return out, omitted


def build(issues: list[str], repository: str, *, claude_sessions=(), codex_sessions=(),
          state_dir=None) -> bytes:
    """The export document for ``issues`` as canonical JSON bytes (sorted keys, trailing newline)."""
    for issue in issues:
        if not _ISSUE.fullmatch(issue):
            raise ValueError("issue identifier differs")
    issues = sorted(set(issues))
    journal = Path(state_dir if state_dir is not None else registry.data_dir()) / campaign_effects.ATTEMPTS_NAME
    grid = cost_attribution.load_price_grid(cost_attribution.PRICE_GRID_PATH)
    records = invocation_log.records_by_id(repository=repository, state_dir=state_dir)
    usage = coordinator = completions = None
    windows: list = []
    if claude_sessions:  # PLP-358: the host notifications that end the background Agent calls
        completions = token_attribution.claude_completions(claude_sessions)
    if claude_sessions or codex_sessions:
        usage, coordinator = {}, {}
        windows = token_attribution.report(claude_sessions=claude_sessions, codex_sessions=codex_sessions,
                                           repository=repository, state_dir=state_dir, per_invocation=usage,
                                           per_coordinator=coordinator)["windows"]
    entries, omitted = [], []
    for issue in issues:
        for found, skipped in (_attempts(issue, records, usage, grid, completions),
                               _coordinators(issue, coordinator, windows, grid),
                               _rounds(issue, repository, state_dir, journal),
                               _escalations(issue, repository, state_dir, records)):
            entries.extend(found)
            omitted.extend(skipped)
    entries.sort(key=lambda e: (e[1]["anchor"]["issue_id"], _KIND_ORDER[e[1]["kind"]],
                                e[1]["started_at"], e[1]["record_id"]))
    ids = [e[1]["record_id"] for e in entries]
    if len(set(ids)) != len(ids):
        raise ValueError("record identifiers collide")
    omitted.sort(key=lambda o: _canonical(o))
    document = {
        "export_schema": EXPORT_SCHEMA,
        "batches": [{"schema_version": RECORD_SCHEMA, "records": [e[1] for e in entries[i:i + BATCH_SIZE]]}
                    for i in range(0, len(entries), BATCH_SIZE)],
        "provenance": {e[1]["record_id"]: e[0].provenance() for e in entries},
        "omitted": omitted,
        "issues": issues,
        "counts": {kind: sum(e[1]["kind"] == kind for e in entries) for kind in _KIND_ORDER},
    }
    return (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
