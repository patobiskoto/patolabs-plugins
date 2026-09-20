"""Offline post-hoc cost attribution for sanitized host usage records.

The module has no provider, tracker, routing, or journal-writing dependency.
It reads only the explicitly selected log and returns no source path or raw row.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
import json
import math
from pathlib import Path
import re
from typing import Iterable, Mapping

PROVENANCE = ("client_observed", "host_reported", "pricing_derived", "unavailable")
TOKEN_KEYS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
              "output_tokens", "reasoning_output_tokens")
BILLING_TOKEN_KEYS = TOKEN_KEYS[:4]
REASONING_DEGRADATION_REASONS = (
    "missing_reasoning_output_tokens",
    "incoherent_reasoning_output_tokens",
)
EXCLUSION_REASONS = ("synthetic_model",)
PLAN_OBSERVATION_RECORD_KEYS = frozenset({
    "record_type", "host", "session_id", "occurred_on",
    "plan_usage_observations", "provenance",
})
PLAN_OBSERVATION_KEYS = frozenset({
    "observation_rank", "observed_on", "window", "plan_usage_percent",
    "plan_usage_window",
})
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PRICE_GRID_PATH = Path(__file__).with_name("pricing-v1.json")


class CostAttributionError(ValueError):
    pass


def _date(value: object) -> str:
    if not isinstance(value, str) or not _DAY.fullmatch(value):
        raise CostAttributionError("date must be ISO calendar date")
    date.fromisoformat(value)
    return value


def _overlap(left: str, right: str) -> bool:
    a, b = left.rstrip("*"), right.rstrip("*")
    return left == right or (left.endswith("*") and b.startswith(a)) or (
        right.endswith("*") and a.startswith(b))


def _matches(pattern: str, model: str) -> bool:
    return model.startswith(pattern[:-1]) if pattern.endswith("*") else model == pattern


def load_price_grid(path: str | Path) -> dict:
    """Load a frozen price grid; any potentially ambiguous price is rejected."""
    try:
        grid = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CostAttributionError("pricing grid is unreadable") from exc
    if set(grid) != {"schema_version", "currency", "entries"} or grid["schema_version"] != 1:
        raise CostAttributionError("pricing grid schema differs")
    if grid["currency"] != "USD" or not isinstance(grid["entries"], list):
        raise CostAttributionError("pricing grid currency or entries differ")
    required = {"host", "model", "aliases", "billing_mode", "source", "captured_at",
                "effective_from", "effective_to", "rates"}
    optional = {"context_threshold_tokens", "over_threshold_rates"}
    entries = []
    for raw in grid["entries"]:
        if not isinstance(raw, dict) or set(raw) - optional != required:
            raise CostAttributionError("pricing entry schema differs")
        if raw["host"] not in {"claude", "codex"} or raw["billing_mode"] not in {
                "api", "subscription", "unknown"}:
            raise CostAttributionError("pricing host or billing mode differs")
        if not isinstance(raw["model"], str) or not raw["model"] or not isinstance(raw["aliases"], list) or not all(isinstance(x, str) and x for x in raw["aliases"]):
            raise CostAttributionError("pricing model aliases differ")
        if not isinstance(raw["source"], str) or not raw["source"] or not isinstance(raw["captured_at"], str) or "T" not in raw["captured_at"]:
            raise CostAttributionError("pricing provenance differs")
        start, end = _date(raw["effective_from"]), _date(raw["effective_to"])
        if end < start:
            raise CostAttributionError("pricing range is inverted")
        rates = raw["rates"]
        if set(rates) != {"input", "cached_input", "cache_write_input", "output"} or any(type(x) not in {int, float} or x < 0 for x in rates.values()):
            raise CostAttributionError("pricing rates differ")
        threshold, elevated = raw.get("context_threshold_tokens"), raw.get("over_threshold_rates")
        if (threshold is None) != (elevated is None):
            raise CostAttributionError("threshold requires matching over-threshold rates")
        if threshold is not None and (type(threshold) is not int or threshold < 1 or not isinstance(elevated, dict) or set(elevated) != set(rates) or any(type(x) not in {int, float} or x < 0 for x in elevated.values())):
            raise CostAttributionError("threshold pricing differs")
        entries.append(dict(raw))
    for index, left in enumerate(entries):
        for right in entries[index + 1:]:
            if left["host"] != right["host"] or left["effective_to"] < right["effective_from"] or right["effective_to"] < left["effective_from"]:
                continue
            if any(_overlap(a, b) for a in [left["model"], *left["aliases"]] for b in [right["model"], *right["aliases"]]):
                raise CostAttributionError("overlapping pricing entries are ambiguous")
    return {"schema_version": 1, "currency": "USD", "entries": entries}


def known_models(grid: Mapping[str, object]) -> list[str]:
    return sorted({entry["model"] for entry in grid["entries"]})


# This vocabulary is public pricing data, not a second hand-maintained allowlist.
KNOWN_MODELS = frozenset(known_models(load_price_grid(PRICE_GRID_PATH)))


def price_for(grid: Mapping[str, object], host: str, model: str, occurred_on: str) -> dict | None:
    _date(occurred_on)
    found = [entry for entry in grid["entries"] if entry["host"] == host and entry["effective_from"] <= occurred_on <= entry["effective_to"] and any(_matches(pattern, model) for pattern in [entry["model"], *entry["aliases"]])]
    if len(found) > 1:
        raise CostAttributionError("pricing selection is ambiguous")
    return dict(found[0]) if found else None


def _integer(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _usage(value: Mapping[str, object]) -> dict | None:
    if not isinstance(value, Mapping):
        return None
    aliases = {
        "input_tokens": ("input_tokens", "inputTokens"),
        "cached_input_tokens": ("cached_input_tokens", "cache_read_input_tokens", "cacheReadInputTokens", "cachedInputTokens"),
        "cache_write_input_tokens": ("cache_write_input_tokens", "cache_creation_input_tokens", "cacheCreationInputTokens"),
        "output_tokens": ("output_tokens", "outputTokens"),
        "reasoning_output_tokens": ("reasoning_output_tokens", "reasoningOutputTokens", "thinking_tokens"),
    }
    result = {}
    for name, names in aliases.items():
        parsed = _integer(next((value[x] for x in names if x in value), None))
        if parsed is None:
            return None
        result[name] = parsed
    return result if result["reasoning_output_tokens"] <= result["output_tokens"] else None


def _normalized_codex_usage(value: Mapping[str, int]) -> dict:
    """Convert native inclusive Codex input into canonical uncached input."""
    uncached_input = value["input_tokens"] - value["cached_input_tokens"]
    if uncached_input < 0:
        raise CostAttributionError("host token usage cannot separate cached input")
    return {**value, "input_tokens": uncached_input}


def _normalized_claude_usage(value: object) -> dict | None:
    """Normalize the documented Claude `message.usage` aliases without retaining data."""
    if not isinstance(value, Mapping):
        return None
    normalized = dict(value)
    created = value.get("cache_creation")
    if "cache_creation_input_tokens" not in value and isinstance(created, Mapping):
        names = ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
        parsed = [_integer(created[name]) for name in names if name in created]
        if any(item is None for item in parsed):
            return None
        writes = sum(parsed)
        normalized["cache_creation_input_tokens"] = writes
    details = value.get("output_tokens_details")
    if ("thinking_tokens" not in value and isinstance(details, Mapping)
            and "thinking_tokens" in details):
        normalized["thinking_tokens"] = details["thinking_tokens"]
    return normalized


def _claude_usage(value: object) -> tuple[dict | None, str | None]:
    """Parse Claude billing counters and degrade only its reasoning breakdown.

    Missing reasoning and a reported subset larger than output are real host forms.
    Both keep the four independently billable counters. A reported malformed value,
    or any malformed billing counter, remains a hard parse failure.
    """
    normalized = _normalized_claude_usage(value)
    if normalized is None:
        return None, None
    billing = {}
    billing_aliases = {
        "input_tokens": ("input_tokens", "inputTokens"),
        "cached_input_tokens": ("cached_input_tokens", "cache_read_input_tokens", "cacheReadInputTokens", "cachedInputTokens"),
        "cache_write_input_tokens": ("cache_write_input_tokens", "cache_creation_input_tokens", "cacheCreationInputTokens"),
        "output_tokens": ("output_tokens", "outputTokens"),
    }
    for name, names in billing_aliases.items():
        parsed = _integer(next((normalized[key] for key in names if key in normalized), None))
        if parsed is None:
            return None, None
        billing[name] = parsed
    reasoning_keys = ("reasoning_output_tokens", "reasoningOutputTokens", "thinking_tokens")
    if not any(key in normalized for key in reasoning_keys):
        return {**billing, "reasoning_output_tokens": "unavailable"}, "missing_reasoning_output_tokens"
    reported = next(normalized[key] for key in reasoning_keys if key in normalized)
    reasoning = _integer(reported)
    if reasoning is None:
        return None, None
    if reasoning > billing["output_tokens"]:
        return {**billing, "reasoning_output_tokens": "unavailable"}, "incoherent_reasoning_output_tokens"
    return {**billing, "reasoning_output_tokens": reasoning}, None


def _reader_completeness(degraded: Mapping[str, int], excluded: Mapping[str, int],
                         billable_records: int) -> dict:
    """Return content-free reasoning completeness and billing-record coverage."""
    affected = [{"reason": reason, "count": degraded[reason]}
                for reason in REASONING_DEGRADATION_REASONS if degraded[reason]]
    exclusions = [{"reason": reason, "count": excluded[reason]}
                  for reason in EXCLUSION_REASONS if excluded[reason]]
    return {"completeness": "unavailable" if affected else "available",
            "billable_records": billable_records,
            "affected_records": affected,
            "excluded_records": exclusions}


def _add_tokens(total: dict, value: Mapping[str, object]) -> None:
    for key in BILLING_TOKEN_KEYS:
        total[key] += value[key]
    if (total["reasoning_output_tokens"] == "unavailable"
            or value["reasoning_output_tokens"] == "unavailable"):
        total["reasoning_output_tokens"] = "unavailable"
    else:
        total["reasoning_output_tokens"] += value["reasoning_output_tokens"]


def _plan_window_snapshot(value: object, window_name: str) -> dict | None:
    """Project one valid Codex account-window observation and nothing else."""
    window = _at(value, "rate_limits", window_name)
    if not isinstance(window, Mapping):
        return None
    percent = window.get("used_percent")
    window_minutes, resets_at = window.get("window_minutes"), window.get("resets_at")
    if (type(percent) not in {int, float} or not math.isfinite(percent) or not 0 <= percent <= 100
            or type(window_minutes) is not int or window_minutes < 1
            or type(resets_at) is not int or resets_at < 0):
        return None
    return {"window": window_name, "plan_usage_percent": percent,
            "plan_usage_window": {"window_minutes": window_minutes, "resets_at": resets_at}}


def _plan_snapshots(value: object) -> list[dict]:
    return [snapshot for name in ("primary", "secondary")
            if (snapshot := _plan_window_snapshot(value, name)) is not None]


def _project_plan_snapshot(value: object, *, default_window: str = "primary") -> dict | None:
    """Validate the additive aggregate input boundary as strictly as the reader."""
    if not isinstance(value, Mapping):
        return None
    percent, window = value.get("plan_usage_percent"), value.get("plan_usage_window")
    name = value.get("window", default_window)
    if name not in {"primary", "secondary"} or type(percent) not in {int, float} or not math.isfinite(percent) or not 0 <= percent <= 100:
        return None
    if not isinstance(window, Mapping):
        return None
    minutes, resets = window.get("window_minutes"), window.get("resets_at")
    if type(minutes) is not int or minutes < 1 or type(resets) is not int or resets < 0:
        return None
    return {"window": name, "plan_usage_percent": percent,
            "plan_usage_window": {"window_minutes": minutes, "resets_at": resets}}


def _project_plan_observation(value: object, *, fallback_date: object = None,
                              exact: bool = False) -> dict | None:
    """Return the complete safe observation whitelist at the aggregate boundary."""
    if not isinstance(value, Mapping) or (exact and set(value) != PLAN_OBSERVATION_KEYS):
        return None
    snapshot = _project_plan_snapshot(value)
    rank = value.get("observation_rank")
    observed_on = value.get("observed_on", fallback_date)
    if snapshot is None or type(rank) is not int or rank < 0:
        return None
    try:
        observed_on = _date(observed_on)
    except ValueError:
        return None
    return {"observation_rank": rank, "observed_on": observed_on, **snapshot}


def _project_plan_record(value: object) -> dict | None:
    """Validate the reader's metadata-only record without accepting extra fields."""
    if not isinstance(value, Mapping) or value.get("record_type") != "plan_usage_observation":
        return None
    if set(value) != PLAN_OBSERVATION_RECORD_KEYS:
        raise CostAttributionError("plan usage observation record differs")
    if (value.get("host") != "codex" or not isinstance(value.get("session_id"), str)
            or not value["session_id"] or value.get("provenance") != "host_reported"):
        raise CostAttributionError("plan usage observation record differs")
    try:
        occurred_on = _date(value.get("occurred_on"))
    except ValueError:
        raise CostAttributionError("plan usage observation record differs") from None
    observations = value.get("plan_usage_observations")
    projected = ([_project_plan_observation(item, exact=True) for item in observations]
                 if isinstance(observations, list) else [])
    if not projected or any(item is None or item["observed_on"] != occurred_on for item in projected):
        raise CostAttributionError("plan usage observation record differs")
    return {"record_type": "plan_usage_observation", "host": "codex",
            "session_id": value["session_id"], "occurred_on": occurred_on,
            "plan_usage_observations": projected, "provenance": "host_reported"}


def _nested(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        if name in value:
            return value[name]
        for key in ("payload", "data", "result"):
            found = _nested(value.get(key), name)
            if found is not None:
                return found
    return None


def _at(value: object, *keys: str) -> object:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def resolve_project(host: str, *, claude_path_slug: str | None = None,
                    codex_cwd: str | None = None,
                    registry_data: Mapping[str, object] | None = None) -> str | None:
    """Resolve only an explicit immutable registry binding; never use a basename."""
    if host not in {"claude", "codex"}:
        return None
    matches = []
    for bindings in (registry_data or {}).values():
        if not isinstance(bindings, Mapping):
            continue
        for repo, entry in bindings.items():
            if not isinstance(repo, str) or not isinstance(entry, Mapping):
                continue
            if host == "claude":
                if isinstance(claude_path_slug, str) and entry.get("claude_path_slug") == claude_path_slug:
                    matches.append(entry.get("key"))
                continue
            # The captured snapshot binds the exact observed cwd.  Do not consult
            # a checkout that may have changed or disappeared since the session.
            if isinstance(codex_cwd, str) and entry.get("codex_cwd") == codex_cwd:
                matches.append(entry.get("key"))
    return matches[0] if len(matches) == 1 and isinstance(matches[0], str) else None


def read_host_log(host: str, log_path: str | Path,
                  registry_data: Mapping[str, object] | None = None,
                  diagnostics: dict | None = None) -> list[dict]:
    """Read one native session JSONL into sanitized usage and quota records."""
    if host not in {"claude", "codex"}:
        raise CostAttributionError("host differs")
    try:
        handle = Path(log_path).open(encoding="utf-8")
    except (OSError, UnicodeError):
        raise CostAttributionError("host log is unreadable") from None
    slug, cwd = (Path(log_path).parent.name if host == "claude" else None), None
    session_id, codex_model = None, None
    codex_ranks: dict[str, int] = defaultdict(int)
    codex_plan_rank = 0
    claude_rank = 0
    codex_rows: dict[tuple[str, str, str], dict] = {}
    codex_events: list[dict] = []
    codex_plan_rows: list[dict] = []
    codex_cumulative: dict[str, dict] = {}
    codex_repeated_cumulative: set[str] = set()
    codex_unattested_turn_sessions: set[str] = set()
    codex_usage_seen = False
    claude_rows: dict[tuple[str, str, str], dict] = {}
    claude_degradations = dict.fromkeys(REASONING_DEGRADATION_REASONS, 0)
    claude_exclusions = dict.fromkeys(EXCLUSION_REASONS, 0)
    claude_billable_records = 0
    try:
        for line in handle:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                raise CostAttributionError("host log contains malformed JSON") from None
            if not isinstance(raw, Mapping):
                continue
            payload = raw.get("payload")
            if host == "codex" and raw.get("type") == "session_meta":
                cwd = payload.get("cwd") if isinstance(payload, Mapping) and isinstance(payload.get("cwd"), str) else cwd
                if isinstance(payload, Mapping):
                    candidate_id = payload.get("session_id", payload.get("id"))
                    session_id = candidate_id if isinstance(candidate_id, str) else session_id
                candidate = _at(payload, "model") or _at(payload, "base_instructions", "provenance", "model")
                codex_model = candidate if isinstance(candidate, str) else codex_model
                continue
            if host == "codex":
                if raw.get("type") in {"turn_context", "event_msg"} and isinstance(payload, Mapping):
                    candidate = payload.get("model")
                    if not isinstance(candidate, str):
                        settings = payload.get("thread_settings")
                        candidate = settings.get("model") if isinstance(settings, Mapping) else None
                    codex_model = candidate if isinstance(candidate, str) else codex_model
                event_token_count = raw.get("type") == "event_msg" and isinstance(payload, Mapping) and (
                    isinstance(payload.get("token_count"), Mapping) or payload.get("type") == "token_count")
                if raw.get("type") not in {"token_usage_record", "token_count"} and not event_token_count:
                    continue
                source = (payload.get("token_count") if isinstance(payload.get("token_count"), Mapping) else payload) if event_token_count else payload if isinstance(payload, Mapping) else raw
                plan_snapshots = _plan_snapshots(source)
                info = source.get("info")
                request_present = isinstance(info, Mapping) and "last_token_usage" in info
                request_usage = info.get("last_token_usage") if request_present else None
                if not request_present and "usage" in source:
                    request_present, request_usage = True, source.get("usage")
                # A turn counter can be cumulative across several requests.
                # Without a real request counter, only a session cumulative
                # series can account for it; never infer turn boundaries.
                if not request_present and "turn_token_usage" in source:
                    if not isinstance(session_id, str):
                        raise CostAttributionError("host session identity is incomplete")
                    codex_unattested_turn_sessions.add(session_id)
                tokens = _usage(request_usage)
                cumulative_present = isinstance(info, Mapping) and "total_token_usage" in info
                cumulative_usage = info.get("total_token_usage") if cumulative_present else None
                cumulative_source = "total_token_usage" if cumulative_present else None
                if not cumulative_present and "thread_token_usage" in source:
                    cumulative_present, cumulative_usage = True, source.get("thread_token_usage")
                    cumulative_source = "thread_token_usage"
                cumulative = _usage(cumulative_usage)
                if ((request_present and tokens is None)
                        or (cumulative_present and cumulative is None)):
                    raise CostAttributionError("host token usage is incomplete")
                timestamp = raw.get("timestamp")
                # Quota-only notifications are optional metadata, never billable
                # requests. Invalid/missing optional quota is ignored, while any
                # counter that was actually present has already failed closed above.
                if tokens is None and cumulative is None:
                    if not plan_snapshots or not isinstance(timestamp, str) or not isinstance(session_id, str):
                        continue
                    try:
                        occurred_on = _date(timestamp[:10])
                    except ValueError:
                        continue
                    observations = []
                    for snapshot in plan_snapshots:
                        codex_plan_rank += 1
                        observations.append({"observation_rank": codex_plan_rank,
                                             "observed_on": occurred_on, **snapshot})
                    codex_plan_rows.append({
                        "record_type": "plan_usage_observation", "host": "codex",
                        "session_id": session_id, "occurred_on": occurred_on,
                        "plan_usage_observations": observations,
                        "provenance": "host_reported",
                    })
                    continue
                if not isinstance(timestamp, str):
                    raise CostAttributionError("host token usage date is incomplete")
                try:
                    occurred_on = _date(timestamp[:10])
                except CostAttributionError:
                    raise CostAttributionError("host token usage date is incomplete") from None
                if not isinstance(session_id, str) or not isinstance(codex_model, str):
                    raise CostAttributionError("host token usage identity is incomplete")
                observations = []
                for snapshot in plan_snapshots:
                    codex_plan_rank += 1
                    observations.append({"observation_rank": codex_plan_rank,
                                         "observed_on": occurred_on, **snapshot})
                codex_usage_seen = True
                codex_events.append({"session_id": session_id, "model": codex_model,
                                     "occurred_on": occurred_on, "tokens": tokens,
                                     "cumulative": cumulative,
                                     "cumulative_source": cumulative_source,
                                     "plan_usage_observations": observations})
                continue
            if raw.get("type") != "assistant":
                continue
            message = raw.get("message")
            model = message.get("model") if isinstance(message, Mapping) else None
            if model == "<synthetic>":
                claude_exclusions["synthetic_model"] += 1
                continue
            timestamp = raw.get("timestamp")
            event_session_id = raw.get("sessionId")
            if not isinstance(model, str) or not isinstance(timestamp, str) or not isinstance(event_session_id, str):
                raise CostAttributionError("host token usage is incomplete")
            try:
                occurred_on = _date(timestamp[:10])
            except (CostAttributionError, ValueError):
                raise CostAttributionError("host token usage date is incomplete") from None
            tokens, degradation_reason = _claude_usage(
                message.get("usage") if isinstance(message, Mapping) else None)
            if tokens is None:
                raise CostAttributionError("host token usage is incomplete")
            claude_billable_records += 1
            if degradation_reason is not None:
                claude_degradations[degradation_reason] += 1
            key = (event_session_id, model, occurred_on)
            cwd = raw.get("cwd") if isinstance(raw.get("cwd"), str) else cwd
            row = claude_rows.setdefault(key, {"host": "claude", "session_id": event_session_id,
                                                "model": model, "occurred_on": occurred_on,
                                                "tokens": dict.fromkeys(TOKEN_KEYS, 0), "token_requests": [], "request_ranks": []})
            _add_tokens(row["tokens"], tokens)
            row["token_requests"].append(tokens)
            claude_rank += 1
            row["request_ranks"].append(claude_rank)
    finally:
        handle.close()
    if host == "codex":
        if codex_usage_seen and not isinstance(session_id, str):
            raise CostAttributionError("host session identity is incomplete")
        # Codex may publish the same session usage through two cumulative streams
        # with unrelated baselines. Select one stream for the whole session rather
        # than interpreting alternation between global and thread snapshots as
        # resets. The global total is authoritative when present; thread totals are
        # the fallback. Build deltas in file order before grouping by date/model so
        # a session crossing midnight cannot recharge its prefix in each bucket.
        cumulative_sources = {}
        for event in codex_events:
            session, source = event["session_id"], event["cumulative_source"]
            if source == "total_token_usage":
                cumulative_sources[session] = source
            elif source == "thread_token_usage" and session not in cumulative_sources:
                cumulative_sources[session] = source
        if codex_unattested_turn_sessions.difference(cumulative_sources):
            raise CostAttributionError(
                "host token usage unavailable: turn counters have no request boundary")
        for event in codex_events:
            session = event["session_id"]
            selected_source = cumulative_sources.get(session)
            selected_cumulative = (selected_source is not None
                                   and event["cumulative_source"] == selected_source)
            request_only = selected_source is None and event["tokens"] is not None
            row = None
            if selected_cumulative or request_only:
                key = (session, event["model"], event["occurred_on"])
                row = codex_rows.setdefault(key, {
                    "host": "codex", "session_id": session, "model": event["model"],
                    "occurred_on": event["occurred_on"],
                    "tokens": dict.fromkeys(TOKEN_KEYS, 0), "token_requests": [],
                    "request_ranks": [], "request_granularity": True,
                })
            if selected_cumulative:
                cumulative = event["cumulative"]
                previous = codex_cumulative.get(session)
                reset = previous is not None and any(
                    cumulative[name] < previous[name] for name in TOKEN_KEYS)
                if previous == cumulative:
                    codex_repeated_cumulative.add(session)
                increment = (cumulative if previous is None or reset else {
                    name: cumulative[name] - previous[name] for name in TOKEN_KEYS
                })
                # A concurrent last request attests a position only when it
                # accounts for the whole increment. Compare the shared native
                # vocabulary; cache normalization preserves this equality.
                request_matches_increment = event["tokens"] == increment
                # Reset and difference decisions use the native cumulative counters.
                # Codex reports cached input as a subset of input_tokens, while the
                # canonical billing vocabulary stores only the noncached remainder.
                increment = _normalized_codex_usage(increment)
                _add_tokens(row["tokens"], increment)
                codex_cumulative[session] = cumulative
                # Preserve every cumulative-derived increment as a billable
                # chunk, including one with no matching request counter.
                # Such a chunk cannot support a request position or a context
                # threshold decision, but dropping it from a mixed bucket
                # would make the priced chunks disagree with the row total.
                row["token_requests"].append(increment)
                if request_matches_increment:
                    codex_ranks[session] += 1
                    row["request_ranks"].append(codex_ranks[session])
                else:
                    row["request_granularity"] = False
            elif request_only:
                increment = _normalized_codex_usage(event["tokens"])
                _add_tokens(row["tokens"], increment)
                row["token_requests"].append(increment)
                codex_ranks[session] += 1
                row["request_ranks"].append(codex_ranks[session])
            observations = event["plan_usage_observations"]
            for observation in observations:
                if row is not None:
                    row.setdefault("plan_usage_observations", []).append(observation)
                if row is not None and observation["window"] == "primary":
                    row["plan_usage_percent"] = observation["plan_usage_percent"]
                    row["plan_usage_window"] = observation["plan_usage_window"]
            if row is None and observations:
                codex_plan_rows.append({
                    "record_type": "plan_usage_observation", "host": "codex",
                    "session_id": session, "occurred_on": event["occurred_on"],
                    "plan_usage_observations": observations,
                    "provenance": "host_reported",
                })
        rows = []
        for key, row in sorted(codex_rows.items()):
            # Native totals are cumulative.  When no per-request counter exists,
            # retain the already-derived bucket total as one non-positional chunk.
            # A per-request stream is preferred for threshold pricing.
            if not row["token_requests"]:
                row["token_requests"] = [row["tokens"]]
                row["request_ranks"] = []
                row["request_granularity"] = False
            row.update({"project": resolve_project("codex", codex_cwd=cwd, registry_data=registry_data),
                        "epic": None, "issue": None, "provenance": "host_reported"})
            row.setdefault("plan_usage_percent", "unavailable")
            if row["session_id"] in codex_repeated_cumulative:
                row["position_data_ambiguous"] = True
            rows.append(row)
        return [*rows, *codex_plan_rows]
    rows = list(claude_rows.values())
    if not rows:
        raise CostAttributionError("host log has no exploitable token usage")
    for row in rows:
        row.update({"project": resolve_project("claude", claude_path_slug=slug, codex_cwd=cwd,
                                                 registry_data=registry_data),
                    "epic": None, "issue": None, "provenance": "host_reported",
                    "plan_usage_percent": "unavailable"})
    if diagnostics is not None:
        diagnostics.update(_reader_completeness(
            claude_degradations, claude_exclusions, claude_billable_records))
    return rows


def cost_record(record: Mapping[str, object], grid: Mapping[str, object]) -> dict:
    allow_unavailable_reasoning = record.get("host") == "claude"
    if allow_unavailable_reasoning:
        tokens = record.get("tokens")
        if not isinstance(tokens, Mapping):
            raise CostAttributionError("session token usage differs")
        _validated_usage(tokens, allow_unavailable_reasoning=True,
                         error="session token usage differs")
        claude_chunks = record.get("token_requests", [tokens])
        if (not isinstance(claude_chunks, list)
                or not all(isinstance(chunk, Mapping) for chunk in claude_chunks)):
            raise CostAttributionError("session token requests differ")
        for chunk in claude_chunks:
            _validated_usage(chunk, allow_unavailable_reasoning=True,
                             error="session token requests differ")
    price = price_for(grid, record["host"], record["model"], record["occurred_on"])
    output = dict(record)
    if price is None:
        output.update({"billing_mode": "unknown",
                       "cost_micros": None, "cost_provenance": "unavailable",
                       "excess_threshold_cost_micros": None,
                       "estimated_cost": {"currency": "USD", "amount_micros": None,
                                          "provenance": "unavailable"}})
        return output
    chunks = record.get("token_requests", [record["tokens"]])
    if not isinstance(chunks, list) or not all(isinstance(chunk, Mapping) for chunk in chunks):
        raise CostAttributionError("session token requests differ")
    if "context_threshold_tokens" in price and record.get("request_granularity") is False:
        output.update({"model": price["model"], "billing_mode": price["billing_mode"],
                       "cost_micros": None, "cost_provenance": "unavailable",
                       "excess_threshold_cost_micros": None, "context_price_tier": "unavailable",
                       "estimated_cost": {"currency": "USD", "amount_micros": None,
                                          "provenance": "unavailable"}})
        return output
    priced_chunks = [_price_chunk(
        item, price, allow_unavailable_reasoning=allow_unavailable_reasoning)
        for item in chunks]
    total = sum(item[0] for item in priced_chunks)
    excess = sum(item[1] for item in priced_chunks)
    tiers = [item[2] for item in priced_chunks]
    # A price grid produces an API-equivalent amount even for a subscription. The
    # explicit billing mode prevents that normalized amount being called an invoice.
    output.pop("token_requests", None)
    output.update({"model": price["model"], "billing_mode": price["billing_mode"], "cost_micros": round(total),
                   "cost_provenance": "pricing_derived",
                   "excess_threshold_cost_micros": round(excess),
                   "context_price_tier": tiers[0] if len(set(tiers)) == 1 else "mixed",
                   "estimated_cost": {"currency": "USD", "amount_micros": round(total),
                                      "provenance": "pricing_derived"}})
    return output


def _validated_usage(tokens: Mapping[str, object], *, allow_unavailable_reasoning: bool,
                     error: str) -> dict:
    if set(tokens) != set(TOKEN_KEYS):
        raise CostAttributionError(error)
    billing = {key: _integer(tokens.get(key)) for key in BILLING_TOKEN_KEYS}
    if any(value is None for value in billing.values()):
        raise CostAttributionError(error)
    reasoning = tokens.get("reasoning_output_tokens")
    if reasoning == "unavailable" and allow_unavailable_reasoning:
        return {**billing, "reasoning_output_tokens": "unavailable"}
    parsed_reasoning = _integer(reasoning)
    if parsed_reasoning is None or parsed_reasoning > billing["output_tokens"]:
        raise CostAttributionError(error)
    return {**billing, "reasoning_output_tokens": parsed_reasoning}


def _price_chunk(tokens: Mapping[str, object], price: Mapping[str, object], *,
                 allow_unavailable_reasoning: bool = False) -> tuple[float, float, str]:
    """Apply the frozen grid to one request; shared by totals and position curves."""
    normalized = _validated_usage(
        tokens, allow_unavailable_reasoning=allow_unavailable_reasoning,
        error="session token requests differ")
    standard = sum(normalized[name] * price["rates"][key] for name, key in (
        ("input_tokens", "input"), ("cached_input_tokens", "cached_input"),
        ("cache_write_input_tokens", "cache_write_input"), ("output_tokens", "output")))
    context = sum(normalized[name] for name in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens"))
    if "context_threshold_tokens" in price and context > price["context_threshold_tokens"]:
        current = sum(normalized[name] * price["over_threshold_rates"][key] for name, key in (
            ("input_tokens", "input"), ("cached_input_tokens", "cached_input"),
            ("cache_write_input_tokens", "cache_write_input"), ("output_tokens", "output")))
        return current, current - standard, "long_context"
    return standard, 0, "standard"


def _position_costs(record: Mapping[str, object], grid: Mapping[str, object]) -> list[tuple[int, int, str]] | None:
    """Return rank, increment and tier, preserving the record's total rounding.

    Rounding each request independently can create or lose a micro-unit.  Instead each
    increment is the difference between consecutive rounded *record* prefixes.  Its sum
    therefore equals ``cost_record(record, grid)["cost_micros"]`` exactly.
    """
    ranks, chunks = record.get("request_ranks"), record.get("token_requests")
    if (record.get("position_data_ambiguous") is True or record.get("request_granularity") is False or not isinstance(ranks, list)
            or not isinstance(chunks, list) or len(ranks) != len(chunks) or not ranks):
        return None
    if (any(type(rank) is not int or rank < 1 for rank in ranks)
            or len(set(ranks)) != len(ranks) or not all(isinstance(chunk, Mapping) for chunk in chunks)):
        return None
    price = price_for(grid, record["host"], record["model"], record["occurred_on"])
    if price is None:
        return None
    total, previous, result = 0.0, 0, []
    for rank, tokens in zip(ranks, chunks):
        try:
            current, _excess, tier = _price_chunk(
                tokens, price, allow_unavailable_reasoning=record.get("host") == "claude")
        except CostAttributionError:
            return None
        total += current
        rounded = round(total)
        result.append((rank, rounded - previous, tier))
        previous = rounded
    return result


def _session_curves(records: Iterable[Mapping[str, object]], grid: Mapping[str, object],
                    excluded_sessions: set[tuple[str, str]] = frozenset()) -> list[dict]:
    """Build content-free, globally ranked cost series; incomplete position data stays unavailable."""
    sessions: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in records:
        host, session_id = row.get("host"), row.get("session_id")
        if host in {"claude", "codex"} and isinstance(session_id, str):
            sessions[(host, session_id)].append(row)
    curves = []
    for (host, session_id), rows in sorted(sessions.items()):
        if (host, session_id) in excluded_sessions:
            curves.append({"host": host, "session_id": session_id,
                           "position_data": "unavailable", "points": []})
            continue
        points = []
        for row in rows:
            priced = _position_costs(row, grid)
            if priced is None:
                points = []
                break
            for rank, cost, tier in priced:
                points.append({"rank": rank, "cost_micros": cost, "model": price_for(grid, row["host"], row["model"], row["occurred_on"])["model"],
                               "occurred_on": row["occurred_on"], "context_price_tier": tier})
        ranks = sorted(point["rank"] for point in points)
        if not points or ranks != list(range(1, len(points) + 1)):
            curves.append({"host": host, "session_id": session_id, "position_data": "unavailable", "points": []})
            continue
        cumulative = 0
        ordered = []
        for point in sorted(points, key=lambda item: item["rank"]):
            cumulative += point["cost_micros"]
            ordered.append({**point, "cumulative_cost_micros": cumulative})
        curves.append({"host": host, "session_id": session_id, "position_data": "available", "points": ordered,
                       "total_cost_micros": cumulative})
    return curves


def _comparisons(curves: list[dict], comparisons: Mapping[str, object] | None) -> list[dict]:
    """Compare caller-declared equal work only; no token-count equivalence is inferred."""
    if comparisons is None:
        return []
    if not isinstance(comparisons, Mapping):
        raise CostAttributionError("session comparisons differ")
    indexed = {(curve["host"], curve["session_id"]): curve for curve in curves}
    result = []
    for work_id, raw in sorted(comparisons.items()):
        if not isinstance(work_id, str) or not work_id or not isinstance(raw, Mapping) or set(raw) != {"long_session", "short_sessions"}:
            raise CostAttributionError("session comparisons differ")
        def identity(value: object) -> tuple[str, str] | None:
            return (value.get("host"), value.get("session_id")) if isinstance(value, Mapping) and set(value) == {"host", "session_id"} and value.get("host") in {"claude", "codex"} and isinstance(value.get("session_id"), str) else None
        long_id = identity(raw["long_session"])
        short_ids = [identity(value) for value in raw["short_sessions"]] if isinstance(raw["short_sessions"], list) else []
        if long_id is None or not short_ids or any(value is None for value in short_ids):
            raise CostAttributionError("session comparisons differ")
        if len(set(short_ids)) != len(short_ids) or long_id in short_ids:
            raise CostAttributionError("session comparisons duplicate a session")
        selected = [indexed.get(long_id), *(indexed.get(value) for value in short_ids)]
        available = all(curve is not None and curve["position_data"] == "available" for curve in selected)
        result.append({"work_id": work_id, "work_equivalence": "caller_declared", "long_session": long_id,
                       "short_sessions": short_ids, "status": "available" if available else "unavailable",
                       "long_session_cost_micros": selected[0].get("total_cost_micros") if available else None,
                       "short_sessions_cost_micros": sum(curve["total_cost_micros"] for curve in selected[1:]) if available else None})
    return result


def _model_ids(path: str | Path) -> list[str]:
    """Read a previously captured `/v1/models` response; never fetch it here."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CostAttributionError("model discovery response is unreadable") from exc
    entries = payload.get("data", payload.get("models", [])) if isinstance(payload, Mapping) else []
    if not isinstance(entries, list):
        raise CostAttributionError("model discovery response differs")
    models = [entry.get("id") if isinstance(entry, Mapping) else entry for entry in entries]
    if not all(isinstance(model, str) and model for model in models):
        raise CostAttributionError("model discovery identifier differs")
    return sorted(set(models))


def _attributions(path: str | Path) -> Mapping[str, Mapping[str, object]]:
    """Read explicit session-to-Epic/issue bindings; never infer them from logs."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CostAttributionError("session attribution is unreadable") from exc
    if not isinstance(payload, Mapping) or not all(isinstance(key, str) and isinstance(value, Mapping)
                                                    and set(value) <= {"epic", "issue"}
                                                    and all(isinstance(item, str) for item in value.values())
                                                    for key, value in payload.items()):
        raise CostAttributionError("session attribution differs")
    return payload


def _session_comparisons(path: str | Path) -> Mapping[str, object]:
    """Read caller-declared equal-work groups without inspecting any work content."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CostAttributionError("session comparisons are unreadable") from exc
    if not isinstance(payload, Mapping):
        raise CostAttributionError("session comparisons differ")
    return payload


def _registry_snapshot(path: str | Path) -> Mapping[str, object]:
    """Read a caller-selected registration snapshot for deterministic replay."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CostAttributionError("registry snapshot is unreadable") from exc
    if not isinstance(payload, Mapping):
        raise CostAttributionError("registry snapshot differs")
    return payload


def aggregate(records: Iterable[Mapping[str, object]], grid: Mapping[str, object],
              *, start: str | None = None, end: str | None = None,
              discovered_models: Iterable[tuple[str, str]] = (),
              session_comparisons: Mapping[str, object] | None = None) -> dict:
    if start:
        _date(start)
    if end:
        _date(end)
    if start and end and end < start:
        raise CostAttributionError("date window is inverted")
    records = list(records)
    groups, unpriced = defaultdict(list), set()
    selected_records = []
    excluded_sessions: set[tuple[str, str]] = set()
    selected_sessions: set[tuple[str, str]] = set()
    plan_snapshots_by_session: dict[tuple[str, str], list[dict]] = defaultdict(list)

    def selected(day: str) -> bool:
        return not ((start and day < start) or (end and day > end))

    def retain_plan(host: object, session_id: object, candidates: Iterable[object],
                    fallback_date: object, *, exact: bool = False) -> None:
        if host != "codex" or not isinstance(session_id, str):
            return
        identity = (host, session_id)
        for candidate in candidates:
            observation = _project_plan_observation(
                candidate, fallback_date=fallback_date, exact=exact)
            if observation is None or not selected(observation["observed_on"]):
                continue
            if observation not in plan_snapshots_by_session[identity]:
                plan_snapshots_by_session[identity].append(observation)
            selected_sessions.add(identity)

    for raw in records:
        plan_record = _project_plan_record(raw)
        if plan_record is not None:
            retain_plan(plan_record["host"], plan_record["session_id"],
                        plan_record["plan_usage_observations"],
                        plan_record["occurred_on"], exact=True)
            continue
        row = cost_record(raw, grid)
        host, session_id = row.get("host"), row.get("session_id")
        if host == "codex" and isinstance(session_id, str):
            observations = raw.get("plan_usage_observations")
            if not isinstance(observations, list):
                observations = [{"observation_rank": 0, "observed_on": row["occurred_on"],
                                 "window": "primary",
                                 "plan_usage_percent": raw.get("plan_usage_percent"),
                                 "plan_usage_window": raw.get("plan_usage_window")}]
            retain_plan(host, session_id, observations, row["occurred_on"])
        if not selected(row["occurred_on"]):
            if row.get("host") in {"claude", "codex"} and isinstance(row.get("session_id"), str):
                excluded_sessions.add((row["host"], row["session_id"]))
            continue
        selected_records.append(raw)
        if host in {"claude", "codex"} and isinstance(session_id, str):
            selected_sessions.add((host, session_id))
        if row["cost_micros"] is None and price_for(grid, row["host"], row["model"], row["occurred_on"]) is None:
            unpriced.add((row["host"], row["model"]))
        groups[(row.get("epic"), row.get("issue"), row.get("project"), row["model"], row["billing_mode"])].append(row)
    aggregates = []
    for key, rows in sorted(groups.items(), key=lambda item: tuple("" if x is None else str(x) for x in item[0])):
        complete = all(row["cost_micros"] is not None for row in rows)
        aggregates.append({"epic": key[0], "issue": key[1], "project": key[2],
                           "model": key[3], "billing_mode": key[4], "sessions": len(rows),
                           "cost_micros": sum(row["cost_micros"] for row in rows) if complete else None,
                           "cost_provenance": "pricing_derived" if complete else "unavailable",
                           "excess_threshold_cost_micros": sum(row["excess_threshold_cost_micros"] for row in rows) if complete else None,
                           "context_price_tiers": sorted({row.get("context_price_tier", "unavailable") for row in rows})})
    dimensions = {}
    for dimension in ("epic", "issue", "project", "model"):
        buckets = defaultdict(list)
        for row in (entry for entries in groups.values() for entry in entries):
            buckets[(row.get(dimension), row["billing_mode"])].append(row)
        dimensions[dimension] = [{dimension: key[0], "billing_mode": key[1], "sessions": len({row.get("session_id") for row in rows}),
                                  "cost_micros": sum(row["cost_micros"] for row in rows) if all(row["cost_micros"] is not None for row in rows) else None,
                                  "excess_threshold_cost_micros": sum(row["excess_threshold_cost_micros"] for row in rows) if all(row["cost_micros"] is not None for row in rows) else None,
                                  "context_price_tiers": sorted({row.get("context_price_tier", "unavailable") for row in rows})}
                                 for key, rows in sorted(buckets.items(), key=lambda item: ("" if item[0][0] is None else str(item[0][0]), item[0][1]))]
    for host, model in discovered_models:
        if not any(entry["host"] == host and any(_matches(pattern, model)
                                                   for pattern in [entry["model"], *entry["aliases"]])
                   for entry in grid["entries"]):
            unpriced.add((host, model))
    curves = _session_curves(selected_records, grid, excluded_sessions)
    plan_usage_by_session = []
    for host, session_id in sorted(selected_sessions):
        snapshots = plan_snapshots_by_session[(host, session_id)] if host == "codex" else []
        snapshots.sort(key=lambda item: (item["observation_rank"], item["observed_on"], item["window"]))
        primary = [item for item in snapshots if item["window"] == "primary"]
        plan_usage_by_session.append({"host": host, "session_id": session_id,
                                      "plan_usage_percent": "unavailable" if not primary else primary[-1]["plan_usage_percent"],
                                      "plan_usage_snapshots": snapshots})
    return {"schema_version": 1, "provenance": list(PROVENANCE),
            "known_models": known_models(grid),
            "unpriced_models": [{"host": host, "model": model} for host, model in sorted(unpriced)],
            "aggregates": aggregates, "by": dimensions, "session_curves": curves,
            "plan_usage_by_session": plan_usage_by_session,
            "session_comparisons": _comparisons(curves, session_comparisons)}


def reconcile_pilot_v1(records: Iterable[Mapping[str, object]], grid: Mapping[str, object],
                       results_path: str | Path) -> dict:
    records = [row for row in records if _project_plan_record(row) is None]
    expected_issues = {f"FOUNDRY-{number}" for number in range(13, 23)}
    observed_issues = {row.get("issue") for row in records if isinstance(row.get("issue"), str)}
    identities = {(row.get("session_id"), row.get("model"), row.get("occurred_on")) for row in records}
    if observed_issues != expected_issues or len(identities) != len(records):
        return {"pilot": "FOUNDRY-MR-PILOT-v1", "status": "unavailable", "delta_micros": None,
                "expected_issues": sorted(expected_issues), "observed_issues": sorted(observed_issues),
                "explanation": "ten distinct explicitly attributed pilot records are required"}
    priced = [cost_record(row, grid) for row in records]
    if any(row["cost_micros"] is None for row in priced):
        return {"pilot": "FOUNDRY-MR-PILOT-v1", "status": "unavailable", "delta_micros": None}
    try:
        text = Path(results_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CostAttributionError("pilot-v1 results are unreadable") from exc
    header = "| slot | issue_id | issue_type | estimate | host | primary_model | primary_effort | main_loop_cost_usd | subagent_cost_usd | actual_cost_usd |"
    if header not in text:
        raise CostAttributionError("pilot-v1 issue summary is unavailable")
    expected_by_issue = {}
    for line in text[text.index(header):].splitlines()[2:]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 10 or not re.fullmatch(r"FOUNDRY-(?:1[3-9]|2[0-2])", cells[1]):
            if line.startswith("## "):
                break
            continue
        expected_by_issue[cells[1]] = round(float(cells[9]) * 1_000_000)
    if set(expected_by_issue) != expected_issues:
        raise CostAttributionError("pilot-v1 issue costs are unavailable")
    observed_by_issue = defaultdict(int)
    for row in priced:
        observed_by_issue[row["issue"]] += row["cost_micros"]
    details = [{"issue": issue, "expected_cost_micros": expected_by_issue[issue],
                "observed_cost_micros": observed_by_issue[issue],
                "delta_micros": observed_by_issue[issue] - expected_by_issue[issue]}
               for issue in sorted(expected_issues)]
    expected, observed = sum(expected_by_issue.values()), sum(observed_by_issue.values())
    return {"pilot": "FOUNDRY-MR-PILOT-v1",
            "status": "matched" if all(item["delta_micros"] == 0 for item in details) else "delta",
            "expected_cost_micros": expected, "observed_cost_micros": observed,
            "delta_micros": observed - expected, "by_issue": details}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Offline Foundry host-log cost attribution")
    parser.add_argument("--host", choices=("claude", "codex"), required=True)
    parser.add_argument("--log", required=True, action="append")
    parser.add_argument("--price-grid", default=str(PRICE_GRID_PATH))
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", dest="end")
    parser.add_argument("--pilot-v1-results")
    parser.add_argument("--models-response", help="saved /v1/models JSON; no request is made")
    parser.add_argument("--session-attribution", help="explicit local JSON: session id -> epic/issue")
    parser.add_argument("--session-comparisons", help="local JSON of caller-declared equal-work long/short session groups")
    parser.add_argument("--registry-snapshot", help="explicit local registry JSON used for project binding")
    args = parser.parse_args(argv)
    grid = load_price_grid(args.price_grid)
    registry_data = _registry_snapshot(args.registry_snapshot) if args.registry_snapshot else {}
    reader_degradations = dict.fromkeys(REASONING_DEGRADATION_REASONS, 0)
    reader_exclusions = dict.fromkeys(EXCLUSION_REASONS, 0)
    reader_billable_records = 0
    rows = []
    for log in args.log:
        reader_diagnostics: dict = {}
        rows.extend(read_host_log(args.host, log, registry_data, reader_diagnostics))
        reader_billable_records += reader_diagnostics.get("billable_records", 0)
        for affected in reader_diagnostics.get("affected_records", []):
            reader_degradations[affected["reason"]] += affected["count"]
        for exclusion in reader_diagnostics.get("excluded_records", []):
            reader_exclusions[exclusion["reason"]] += exclusion["count"]
    if args.session_attribution:
        bindings = _attributions(args.session_attribution)
        rows = [row if row.get("record_type") == "plan_usage_observation"
                else {**row, **bindings.get(row["session_id"], {})} for row in rows]
    discovered = [(args.host, model) for model in _model_ids(args.models_response)] if args.models_response else ()
    comparisons = _session_comparisons(args.session_comparisons) if args.session_comparisons else None
    result = reconcile_pilot_v1(rows, grid, args.pilot_v1_results) if args.pilot_v1_results else aggregate(rows, grid, start=args.start, end=args.end, discovered_models=discovered, session_comparisons=comparisons)
    if args.host == "claude":
        result["reader_completeness"] = _reader_completeness(
            reader_degradations, reader_exclusions, reader_billable_records)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
