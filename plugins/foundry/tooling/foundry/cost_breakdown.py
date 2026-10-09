"""PAT-129: offline, read-only breakdown of where premium work goes in a finished PAT-19 campaign.

Reads the committed ``results-*.jsonl`` / ``ledger-*.jsonl`` (and optionally ``report-*.json``) of a
``compare_exploration`` campaign and gives, per arm, the premium tokens by role, by token class and by
model, unweighted (the sum the frozen rule uses) and weighted by a dated price grid
(``pricing-breakdown-v1.json``). Optionally reads the raw cloud transcripts kept OFF the repository to count
the repository-exploration tool calls of each role. It runs no model, no ``claude``, no ``lms``, no campaign
mode, recomputes no frozen verdict, and prints aggregates only (never a path, a command or an excerpt).

API list prices are WEIGHTS under a subscription, never a bill: nothing here is a saving.
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import re
import shlex
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

GRID_PATH = Path(__file__).with_name("pricing-breakdown-v1.json")
CLASSES = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")
REASONING = "reasoning_output_tokens"  # recorded separately; a SUBSET of output_tokens, never added
RATE_KEYS = ("input", "cache_read", "cache_write_5m", "cache_write_1h", "output")
ROLES = ("implementer", "corrector", "reviewer", "explorer")
UNAVAILABLE = "unavailable"
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class BreakdownError(ValueError):
    pass


# ------------------------------------------------------------------------------------------ price grid

def _day(value: object) -> str:
    if not isinstance(value, str) or not _DAY.fullmatch(value):
        raise BreakdownError("date must be an ISO calendar date")
    dt.date.fromisoformat(value)
    return value


def _rates(value: object) -> dict[str, Decimal]:
    if not isinstance(value, dict) or set(value) != set(RATE_KEYS):
        raise BreakdownError("price rates differ")
    if any(not isinstance(v, (int, Decimal)) or isinstance(v, bool) or v < 0 for v in value.values()):
        raise BreakdownError("price rates differ")
    return {k: Decimal(v) for k, v in value.items()}


def load_grid(path: str | Path = GRID_PATH) -> dict[str, Any]:
    """Load a dated price grid; ambiguous or malformed entries are refused (nothing is estimated)."""
    try:
        grid = json.loads(Path(path).read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, json.JSONDecodeError) as exc:
        raise BreakdownError("price grid is unreadable") from exc
    if not isinstance(grid, dict) or grid.get("schema") != "foundry.price-grid.breakdown.v1" \
            or grid.get("currency") != "USD" or not isinstance(grid.get("entries"), list):
        raise BreakdownError("price grid schema differs")
    required = {"host", "model", "aliases", "source", "captured_on", "effective_from", "effective_to",
                "effective_from_basis", "rates"}
    optional = {"prompt_tokens_threshold", "over_threshold_rates"}
    entries = []
    for raw in grid["entries"]:
        if not isinstance(raw, dict) or set(raw) - optional != required:
            raise BreakdownError("price entry schema differs")
        if not isinstance(raw["model"], str) or not raw["model"] or not isinstance(raw["aliases"], list) \
                or not all(isinstance(a, str) and a for a in raw["aliases"]):
            raise BreakdownError("price model or aliases differ")
        if not isinstance(raw["source"], str) or not raw["source"] or not isinstance(raw["effective_from_basis"], str):
            raise BreakdownError("price provenance differs")
        entry = dict(raw)
        entry["captured_on"] = _day(raw["captured_on"])
        entry["effective_from"], entry["effective_to"] = _day(raw["effective_from"]), _day(raw["effective_to"])
        if entry["effective_to"] < entry["effective_from"]:
            raise BreakdownError("price range is inverted")
        entry["rates"] = _rates(raw["rates"])
        if ("prompt_tokens_threshold" in raw) != ("over_threshold_rates" in raw):
            raise BreakdownError("threshold requires matching over-threshold rates")
        if "over_threshold_rates" in raw:
            entry["over_threshold_rates"] = _rates(raw["over_threshold_rates"])
            if type(raw["prompt_tokens_threshold"]) is not int or raw["prompt_tokens_threshold"] < 1:
                raise BreakdownError("price threshold differs")
        entries.append(entry)
    for index, left in enumerate(entries):
        for right in entries[index + 1:]:
            if left["host"] != right["host"] or left["effective_to"] < right["effective_from"] \
                    or right["effective_to"] < left["effective_from"]:
                continue
            if {left["model"], *left["aliases"]} & {right["model"], *right["aliases"]}:
                raise BreakdownError("overlapping price entries are ambiguous")
    return {"currency": "USD", "entries": entries}


def price_for(grid: Mapping[str, Any], host: str, model: str, day: str) -> dict[str, Any] | None:
    """The entry valid on ``day`` for ``model`` (exact name or declared alias); None when there is none."""
    found = [e for e in grid["entries"] if e["host"] == host and e["effective_from"] <= day <= e["effective_to"]
             and model in (e["model"], *e["aliases"])]
    return found[0] if found else None


_PARTS = ("input", "cached_input", "cache_write_5m", "cache_write_1h", "output")


def weigh(tokens: Mapping[str, int], entry: Mapping[str, Any] | None) -> dict[str, Decimal] | None:
    """USD weight of one token vector, by class, with the cache write at BOTH 5-minute and 1-hour prices.

    None (unavailable) when there is no entry, or when the entry's price depends on the prompt length of each
    request, which the records do not carry (never estimated)."""
    if entry is None or "prompt_tokens_threshold" in entry:
        return None
    r = entry["rates"]
    million = Decimal(1_000_000)
    return {
        "input": tokens["input_tokens"] * r["input"] / million,
        "cached_input": tokens["cached_input_tokens"] * r["cache_read"] / million,
        "cache_write_5m": tokens["cache_write_input_tokens"] * r["cache_write_5m"] / million,
        "cache_write_1h": tokens["cache_write_input_tokens"] * r["cache_write_1h"] / million,
        "output": tokens["output_tokens"] * r["output"] / million,
    }


def _usd(value: Decimal) -> float:
    return float(round(value, 6))


def _finish(parts: Sequence[Mapping[str, Decimal] | None], models: Iterable[str]) -> Any:
    """Sum weighted cells; ``unavailable`` (naming the models) as soon as one cell has no price."""
    if any(p is None for p in parts):
        return {"status": UNAVAILABLE, "models_without_price": sorted(set(models))}
    total = {k: sum((p[k] for p in parts), Decimal(0)) for k in _PARTS}  # type: ignore[index]
    low = total["input"] + total["cached_input"] + total["cache_write_5m"] + total["output"]
    high = total["input"] + total["cached_input"] + total["cache_write_1h"] + total["output"]
    return {"status": "priced", "input_usd": _usd(total["input"]), "cached_input_usd": _usd(total["cached_input"]),
            "cache_write_usd": {"all_5m": _usd(total["cache_write_5m"]), "all_1h": _usd(total["cache_write_1h"])},
            "output_usd": _usd(total["output"]),
            "total_usd": {"cache_write_all_5m": _usd(low), "cache_write_all_1h": _usd(high)}}


# ---------------------------------------------------------------------------------------- campaign data

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise BreakdownError(f"{path.name} is unreadable") from exc


def _vector(value: Mapping[str, Any]) -> dict[str, int]:
    out = {}
    for name in (*CLASSES, REASONING):
        item = value.get(name, 0 if name == REASONING else None)
        if type(item) is not int or item < 0:
            raise BreakdownError("a token class is absent or not a count: an absent datum is never zero")
        out[name] = item
    return out


def _add(into: dict[str, int], value: Mapping[str, int]) -> None:
    for name in (*CLASSES, REASONING):
        into[name] = into.get(name, 0) + value[name]


def _zero() -> dict[str, int]:
    return dict.fromkeys((*CLASSES, REASONING), 0)


def role_models(by_role: Mapping[str, Mapping[str, Any]], by_model: Mapping[str, Mapping[str, Any]]
                ) -> dict[str, str] | None:
    """Which model each role used in ONE record. A record gives tokens by role and tokens by model, not both
    at once: one model means every role used it; several models are resolved only when exactly one assignment
    of roles to models reproduces every model's counters; otherwise None (unavailable, never a guess)."""
    roles = [r for r, v in by_role.items() if any(v.get(c) for c in CLASSES)]
    models = sorted(by_model)
    if not roles:
        return {}
    if len(models) == 1:
        return {r: models[0] for r in roles}
    targets = {m: _vector(by_model[m]) for m in models}
    vectors = {r: _vector(by_role[r]) for r in roles}
    found = []
    for combo in itertools.product(models, repeat=len(roles)):
        sums = {m: _zero() for m in models}
        for role, model in zip(roles, combo):
            _add(sums[model], vectors[role])
        if sums == targets:
            found.append(dict(zip(roles, combo)))
    return found[0] if len(found) == 1 else None


def load_sessions(ledger: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """session id -> role and UTC day, from the ledger's ``cloud_started`` events (real runs only)."""
    out = {}
    for event in ledger:
        if event.get("kind") == "cloud_started" and not event.get("dry_run"):
            at = event["at"]
            out[event["session_id"]] = {
                "role": event["role"],
                "day": dt.datetime.fromtimestamp(at, dt.timezone.utc).date().isoformat()}
    return out


def _cells(records: Sequence[Mapping[str, Any]], sessions: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One cell per (record, role): arm, task, role, model (or None when ambiguous), day, tokens."""
    cells = []
    for rec in records:
        premium = rec["premium"]
        if premium.get("billing_total") is None:
            raise BreakdownError("a record with an unknown premium cannot be broken down")
        days = sorted(sessions[s]["day"] for s in rec.get("cloud_sessions") or [] if s in sessions)
        mapping = role_models(premium["by_role"], premium["by_model"])
        for role, tokens in premium["by_role"].items():
            vec = _vector(tokens)
            if not any(vec[c] for c in CLASSES):
                continue
            cells.append({"arm": rec["path"], "pr": rec["task"]["pr"], "role": role, "tokens": vec,
                          "model": None if mapping is None else mapping[role],
                          "day": days[0] if days else None,
                          "models": sorted(premium["by_model"]), "accepted": rec.get("accepted") is True})
    return cells


def _cell_weight(cell: Mapping[str, Any], grid: Mapping[str, Any]) -> dict[str, Decimal] | None:
    if cell["model"] is None or cell["day"] is None:
        return None
    return weigh(cell["tokens"], price_for(grid, "claude", cell["model"], cell["day"]))


def _share(part: int | Decimal, whole: int | Decimal) -> float | None:
    return None if not whole else float(round(Decimal(part) / Decimal(whole), 4))


def _weighted_group(cells: Sequence[Mapping[str, Any]], grid: Mapping[str, Any]) -> Any:
    return _finish([_cell_weight(c, grid) for c in cells], [m for c in cells for m in (c["models"] if c["model"] is None else [c["model"]])])


def _tokens_view(vec: Mapping[str, int]) -> dict[str, int]:
    return {**{c: vec[c] for c in CLASSES}, REASONING: vec[REASONING], "billing_total": sum(vec[c] for c in CLASSES)}


def breakdown(records: Sequence[Mapping[str, Any]], sessions: Mapping[str, Mapping[str, Any]],
              grid: Mapping[str, Any]) -> dict[str, Any]:
    """Premium work by arm, role, token class and model, unweighted and weighted."""
    attempts = [r for r in records if r.get("record_type") == "attempt" and not r.get("dry_run")]
    cells = _cells(attempts, sessions)
    arms: dict[str, Any] = {}
    for arm in sorted({r["path"] for r in attempts}):
        mine = [c for c in cells if c["arm"] == arm]
        own = [r for r in attempts if r["path"] == arm]
        roles: dict[str, Any] = {}
        grand = _zero()
        by_model: dict[str, dict[str, int]] = {}
        for rec in own:
            for model, tokens in rec["premium"]["by_model"].items():
                _add(by_model.setdefault(model, _zero()), _vector(tokens))
        for role in ROLES:
            group = [c for c in mine if c["role"] == role]
            vec = _zero()
            for c in group:
                _add(vec, c["tokens"])
            _add(grand, vec)
            roles[role] = {"tokens": _tokens_view(vec), "weighted": _weighted_group(group, grid) if group else None}
        extra = sorted({c["role"] for c in mine} - set(ROLES))
        if extra:
            raise BreakdownError(f"unexpected roles in the records: {extra}")
        explorer_records = sum(1 for r in own if r.get("segment") == "explore")
        roles["explorer"]["note"] = (f"{explorer_records} local exploration record(s); local tokens are not premium "
                                     "tokens and are not counted (no premium token recorded)")
        weighted_all = _weighted_group(mine, grid)
        by_class: dict[str, Any] = {}
        for name, key in (("input", "input"), ("cache_read", "cached_input"), ("cache_write", "cache_write"), ("output", "output")):
            by_class[name] = {"tokens": grand[{"input": "input_tokens", "cache_read": "cached_input_tokens",
                                               "cache_write": "cache_write_input_tokens", "output": "output_tokens"}[name]]}
            if weighted_all["status"] == "priced":
                w = weighted_all
                by_class[name]["weighted_usd"] = (
                    w["cache_write_usd"] if key == "cache_write" else w[f"{key}_usd"])
        by_class["reasoning_output_visible"] = {"tokens": grand[REASONING],
                                                "note": "a subset of output, not an extra class"}
        models: dict[str, Any] = {}
        for model, vec in sorted(by_model.items()):
            group = [c for c in mine if c["model"] == model]
            unresolved = [c for c in mine if c["model"] is None and model in c["models"]]
            models[model] = {"tokens": _tokens_view(vec),
                             "weighted": ({"status": UNAVAILABLE, "models_without_price": [model]} if unresolved
                                          else _weighted_group(group, grid))}
        total = _tokens_view(grand)
        for role in ROLES:
            if role != "explorer":
                roles[role]["token_share"] = _share(roles[role]["tokens"]["billing_total"], total["billing_total"])
                w = roles[role]["weighted"]
                if w and w["status"] == "priced" and weighted_all["status"] == "priced":
                    roles[role]["weighted_share"] = {
                        "cache_write_all_5m": _share(Decimal(str(w["total_usd"]["cache_write_all_5m"])),
                                                     Decimal(str(weighted_all["total_usd"]["cache_write_all_5m"]))),
                        "cache_write_all_1h": _share(Decimal(str(w["total_usd"]["cache_write_all_1h"])),
                                                     Decimal(str(weighted_all["total_usd"]["cache_write_all_1h"])))}
        arms[arm] = {"records": len(own), "tokens": total, "by_role": roles, "by_class": by_class,
                     "by_model": models, "weighted": weighted_all}
    return {"arms": arms, "cells": cells}


def check_against_report(result: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    """Whether the recomputed totals equal the committed report's (A = reference, L = arm)."""
    detail = report["exploration_comparison"]["arms"]["L"]["economy_detail"]
    expected = {"L": (detail["premium_billing_tokens"], detail["premium_by_model"]),
                "A": (detail["reference_premium_billing_tokens"], detail["reference_premium_by_model"])}
    out = {}
    for arm, (total, by_model) in expected.items():
        mine = result["arms"][arm]
        ok_models = {m: {k: v for k, v in _tokens_view(_vector(t)).items() if k != "billing_total"}
                     for m, t in by_model.items()} == {
            m: {k: v for k, v in x["tokens"].items() if k != "billing_total"} for m, x in mine["by_model"].items()}
        out[arm] = {"billing_total_equal": mine["tokens"]["billing_total"] == total, "by_model_equal": ok_models}
    out["all_equal"] = all(v["billing_total_equal"] and v["by_model_equal"] for v in out.values())
    return out


def paired_reading(result: Mapping[str, Any], report: Mapping[str, Any],
                   grid: Mapping[str, Any]) -> dict[str, Any] | None:
    """The weighted L/A premium per accepted task on the paired set of the committed report.

    A READING OUTSIDE THE FROZEN RULE: the verdict is the report's, it is neither recomputed nor requalified."""
    rule = report["exploration_comparison"].get("paired_rule")
    if not rule or not rule.get("paired_decided"):
        return None
    prs = set(rule["paired_decided"])
    cells = [c for c in result["cells"] if c["pr"] in prs]
    out: dict[str, Any] = {"paired_tasks": sorted(prs), "outside_the_frozen_rule": True, "arms": {}}
    for arm in ("A", "L"):
        mine = [c for c in cells if c["arm"] == arm]
        # acceptance is per task (any record accepted), as the frozen report counts it
        accepted_prs = {c["pr"] for c in result["cells"] if c["arm"] == arm and c["accepted"]} & prs
        accepted = len(accepted_prs)
        tokens = sum(sum(c["tokens"][k] for k in CLASSES) for c in mine)
        out["arms"][arm] = {
            "accepted": accepted, "billing_tokens": tokens, "weighted": _weighted_group(mine, grid),
            "billing_tokens_by_role": {role: sum(sum(c["tokens"][k] for k in CLASSES) for c in mine if c["role"] == role)
                                       for role in ROLES if role != "explorer"}}
    a, lo = out["arms"]["A"], out["arms"]["L"]
    out["unweighted_ratio_L_over_A_per_accepted"] = (
        None if not a["accepted"] or not lo["accepted"] else float(round(
            (Decimal(lo["billing_tokens"]) / lo["accepted"]) / (Decimal(a["billing_tokens"]) / a["accepted"]), 4)))
    ratio: dict[str, Any] = {}
    if a["weighted"]["status"] == "priced" and lo["weighted"]["status"] == "priced" and a["accepted"] and lo["accepted"]:
        for bound in ("cache_write_all_5m", "cache_write_all_1h"):
            wa, wl = Decimal(str(a["weighted"]["total_usd"][bound])), Decimal(str(lo["weighted"]["total_usd"][bound]))
            ratio[bound] = float(round((wl / lo["accepted"]) / (wa / a["accepted"]), 4))
    else:
        ratio = {"status": UNAVAILABLE}
    out["weighted_ratio_L_over_A_per_accepted"] = ratio
    out["frozen_ratio_in_report"] = rule.get("ratio")
    return out


# -------------------------------------------------------------------------- exploration (raw transcripts)

# Read/search commands: these names, alone or composed with the neutral filters below, make a Bash call
# an exploration call. ``sed`` only with ``-n`` and never in place; ``find`` never with an action.
EXPLORE_COMMANDS = frozenset({"cat", "head", "tail", "grep", "egrep", "fgrep", "rg", "ls", "find", "sed"})
NEUTRAL_COMMANDS = frozenset({"cd", "pwd", "wc", "sort", "uniq", "cut", "tr", "echo", "printf", "true"})
FILE_TOOLS = frozenset({"Read"})
SEARCH_TOOLS = frozenset({"Grep", "Glob"})
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_FIND_ACTIONS = frozenset({"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"})
_NULL_REDIRECT = re.compile(r"(?:\d?>>?|&>)\s*/dev/null")
_REDIRECTS = frozenset({">", ">>", ">&", ">|", "&>", "<<", "<<<", "<<-"})


def _bash_segments(command: str) -> list[list[str]] | None:
    """Simple commands of a shell line, or None when it cannot be read with certainty (substitution,
    heredoc, redirection to a file, unbalanced quote): such a call is never counted as exploration."""
    if "$(" in command or "`" in command:
        return None
    text = _NULL_REDIRECT.sub(" ", command)
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|<>()\n")
    lexer.whitespace, lexer.whitespace_split = " \t\r", True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    segments: list[list[str]] = [[]]
    skip_target = False
    for token in tokens:
        if skip_target:  # the target of ``2>&1``-style descriptor duplication
            skip_target = False
            if token in {"1", "2"}:
                continue
            return None
        if token == ">&":
            skip_target = True
            continue
        if token in _REDIRECTS or set(token) <= set("<>") or token in {"(", ")"}:
            return None
        if token in {"&&", "||", ";", "|", "\n", "&", "|&"} or set(token) <= set("&|;\n") and token:
            segments.append([])
            continue
        segments[-1].append(token)
    return [s for s in segments if s]


def _command_name(segment: Sequence[str]) -> tuple[str, list[str]] | None:
    rest = list(segment)
    while rest and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", rest[0]):
        rest.pop(0)
    if not rest:
        return None
    return rest[0].rsplit("/", 1)[-1], rest[1:]


def classify_tool_use(name: str, tool_input: Mapping[str, Any] | None) -> str:
    """One of ``explore_read`` (Read), ``explore_search`` (Grep/Glob), ``explore_bash`` (a Bash call made only of
    read/search commands), ``bash_other``, ``edit`` (file writes), ``other`` (any other tool name).

    A Bash call is ``explore_bash`` only when it is a pipeline/sequence of simple commands, at least one in
    ``EXPLORE_COMMANDS`` (cat, head, tail, sed -n, grep, rg, find without action, ls) and every other one in
    ``NEUTRAL_COMMANDS`` (cd, pwd, wc, ...), with no command substitution, heredoc or file redirection.
    Anything unreadable is ``bash_other``: the count of exploration is a lower bound by construction."""
    if name in FILE_TOOLS:
        return "explore_read"
    if name in SEARCH_TOOLS:
        return "explore_search"
    if name in EDIT_TOOLS:
        return "edit"
    if name != "Bash":
        return "other"
    command = (tool_input or {}).get("command")
    segments = _bash_segments(command) if isinstance(command, str) else None
    if not segments:
        return "bash_other"
    explored = False
    for segment in segments:
        parsed = _command_name(segment)
        if parsed is None:
            return "bash_other"
        cmd, args = parsed
        if cmd in NEUTRAL_COMMANDS:
            continue
        if cmd not in EXPLORE_COMMANDS:
            return "bash_other"
        if cmd == "sed" and (not any(a == "-n" or (a.startswith("-") and not a.startswith("--") and "n" in a)
                                     for a in args)
                             or any(a.startswith("--in-place") or (a.startswith("-") and not a.startswith("--")
                                                                   and "i" in a) for a in args)):
            return "bash_other"
        if cmd == "find" and any(a in _FIND_ACTIONS for a in args):
            return "bash_other"
        explored = True
    return "explore_bash" if explored else "bash_other"


EXPLORE_KINDS = frozenset({"explore_read", "explore_search", "explore_bash"})


def read_session(path: Path) -> dict[str, Any]:
    """Calls (one per assistant message id, blocks merged), the final ``result`` event, from a session JSONL."""
    calls: dict[str, dict[str, Any]] = {}
    result = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("type") == "result":
            result = event
        elif event.get("type") == "assistant" and isinstance(event.get("message"), dict):
            msg = event["message"]
            call = calls.setdefault(msg["id"], {"model": msg.get("model"), "usage": msg.get("usage") or {},
                                                "tools": {}, "output_seen": 0})
            call["output_seen"] = max(call["output_seen"], int((msg.get("usage") or {}).get("output_tokens") or 0))
            for block in msg.get("content") or []:
                if block.get("type") == "tool_use":
                    call["tools"][block["id"]] = (block.get("name"), block.get("input"))
    return {"calls": list(calls.values()), "result": result}


def _call_kind(call: Mapping[str, Any]) -> str:
    if not call["tools"]:
        return "no_tool"
    kinds = {classify_tool_use(n, i) in EXPLORE_KINDS for n, i in call["tools"].values()}
    return "explore_only" if kinds == {True} else "action"


def _session_tokens(call: Mapping[str, Any]) -> dict[str, int]:
    u = call["usage"]
    return {"input_tokens": int(u.get("input_tokens") or 0), "cached_input_tokens": int(u.get("cache_read_input_tokens") or 0),
            "cache_write_input_tokens": int(u.get("cache_creation_input_tokens") or 0)}


def exploration(records: Sequence[Mapping[str, Any]], sessions: Mapping[str, Mapping[str, Any]],
                streams_dir: Path, grid: Mapping[str, Any], campaign_id: str,
                logs_dir: Path | None = None, only_prs: Iterable[int] | None = None) -> dict[str, Any]:
    """Counts of read/search tool calls per arm and role, and the cost of the API calls made only of them.

    The unit of attribution is one API call (one assistant message id): its own input, cache and output
    tokens. The effect of an exploration result staying in the context of the later calls is NOT measured."""
    keep = None if only_prs is None else set(only_prs)
    arm_of = {s: r["path"] for r in records if r.get("record_type") == "attempt" and not r.get("dry_run")
              and (keep is None or r["task"]["pr"] in keep) for s in r.get("cloud_sessions") or []}
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    cells: list[dict[str, Any]] = []
    for sid, info in sorted(sessions.items()):
        arm, role = arm_of.get(sid), info["role"]
        if arm is None:
            continue
        stream = read_session(streams_dir / f"{campaign_id}-{sid}.jsonl")
        if stream["result"] is None:
            raise BreakdownError("a transcript without a final result event cannot be measured")
        exact = None
        if logs_dir is not None:
            found = sorted(logs_dir.glob(f"*/{sid}.jsonl"))
            if len(found) == 1:
                exact = read_session(found[0])
        usage = stream["result"]["usage"]
        total_out = int(usage["output_tokens"])
        per_call_out = [c["output_seen"] for c in stream["calls"]]
        output_exact = False
        if exact is not None and len(exact["calls"]) == len(stream["calls"]) \
                and sum(c["output_seen"] for c in exact["calls"]) == total_out:
            per_call_out, output_exact = [c["output_seen"] for c in exact["calls"]], True
        slack = max(total_out - sum(per_call_out), 0)
        a = acc.setdefault((arm, role), {
            "sessions": 0, "api_calls": 0, "tool_calls": Counter(), "calls_by_kind": Counter(),
            "sessions_output_exact": 0, "sessions_input_side_matches_result": 0,
            "cache_write_split": {"ephemeral_5m": 0, "ephemeral_1h": 0}, "host_list_cost_usd": Decimal(0),
            "models": Counter()})
        a["sessions"] += 1
        a["api_calls"] += len(stream["calls"])
        a["sessions_output_exact"] += output_exact
        split = usage.get("cache_creation") or {}
        a["cache_write_split"]["ephemeral_5m"] += int(split.get("ephemeral_5m_input_tokens") or 0)
        a["cache_write_split"]["ephemeral_1h"] += int(split.get("ephemeral_1h_input_tokens") or 0)
        a["host_list_cost_usd"] += Decimal(str(stream["result"].get("total_cost_usd") or 0))
        sums = _zero()
        for index, (call, out) in enumerate(zip(stream["calls"], per_call_out)):
            toks = {**_session_tokens(call), "output_tokens": out, REASONING: 0}
            _add(sums, toks)
            for name, tool_input in call["tools"].values():
                kind = classify_tool_use(name, tool_input)
                a["tool_calls"][kind] += 1
            kind = _call_kind(call)
            a["calls_by_kind"][kind] += 1
            a["models"][call["model"]] += 1
            if kind == "explore_only":
                cells.append({"arm": arm, "role": role, "model": call["model"], "day": info["day"], "tokens": toks,
                              "first_call": index == 0, "slack_session": None, "pr": 0, "models": [call["model"]]})
        a["sessions_input_side_matches_result"] += all(
            sums[k] == int(usage[u]) for k, u in (("input_tokens", "input_tokens"),
                                                 ("cached_input_tokens", "cache_read_input_tokens"),
                                                 ("cache_write_input_tokens", "cache_creation_input_tokens")))
        if slack and not output_exact:
            cells.append({"arm": arm, "role": role, "model": stream["calls"][0]["model"], "day": info["day"],
                          "tokens": {**_zero(), "output_tokens": slack}, "first_call": False,
                          "slack_session": True, "pr": 0, "models": [stream["calls"][0]["model"]]})
    out: dict[str, Any] = {}
    for (arm, role), a in sorted(acc.items()):
        mine = [c for c in cells if c["arm"] == arm and c["role"] == role]
        explore_calls = [c for c in mine if not c["slack_session"]]
        slack_cells = [c for c in mine if c["slack_session"]]
        tool_total = sum(a["tool_calls"].values())
        explore_tools = sum(a["tool_calls"][k] for k in EXPLORE_KINDS)
        tok = _zero()
        for c in explore_calls:
            _add(tok, c["tokens"])
        first = _zero()
        for c in explore_calls:
            if c["first_call"]:
                _add(first, c["tokens"])
        slack_out = sum(c["tokens"]["output_tokens"] for c in slack_cells)
        out.setdefault(arm, {})[role] = {
            "sessions": a["sessions"], "api_calls": a["api_calls"],
            "tool_calls": {"total": tool_total, **{k: a["tool_calls"][k] for k in
                                                   ("explore_read", "explore_search", "explore_bash", "bash_other", "edit", "other")},
                           "exploration": explore_tools, "exploration_share": _share(explore_tools, tool_total)},
            "api_calls_by_kind": {k: a["calls_by_kind"][k] for k in ("explore_only", "action", "no_tool")},
            "explore_only_calls": {
                "tokens": {c: tok[c] for c in CLASSES},
                "billing_total": sum(tok[c] for c in CLASSES),
                "of_which_first_call_of_session": {c: first[c] for c in CLASSES},
                "output_unattributed_upper_slack_tokens": slack_out,
                "weighted": _weighted_group(explore_calls, grid) if explore_calls else None,
                "weighted_excluding_first_call": (
                    _weighted_group([c for c in explore_calls if not c["first_call"]], grid)
                    if any(not c["first_call"] for c in explore_calls) else None),
                "weighted_with_output_slack": (_weighted_group(explore_calls + slack_cells, grid)
                                               if explore_calls and slack_cells else None)},
            "sessions_with_exact_per_call_output": a["sessions_output_exact"],
            "sessions_input_side_equal_to_final_result": a["sessions_input_side_matches_result"],
            "cache_write_split_tokens": a["cache_write_split"],
            "host_list_cost_usd": _usd(a["host_list_cost_usd"])}
    return out


# -------------------------------------------------------------------------------------------------- CLI

def analyse(results: Path, *, ledger: Path | None = None, report: Path | None = None, grid: Path = GRID_PATH,
            streams_dir: Path | None = None, logs_dir: Path | None = None) -> dict[str, Any]:
    match = re.fullmatch(r"results-(.+)\.jsonl", results.name)
    if match is None:
        raise BreakdownError("results file must be named results-<campaign>.jsonl")
    campaign_id = match.group(1)
    ledger = ledger or results.with_name(f"ledger-{campaign_id}.jsonl")
    records, events = _read_jsonl(results), _read_jsonl(ledger)
    if any(r.get("campaign_id") != campaign_id for r in records):
        raise BreakdownError("results hold records of another campaign id")
    price_grid = load_grid(grid)
    sessions = load_sessions(events)
    result = breakdown(records, sessions, price_grid)
    doc: dict[str, Any] = {
            "schema": "foundry.cost-breakdown.v1", "campaign_id": campaign_id,
            "reading": "outside the frozen rule; API list prices are weights under a subscription, never a bill",
            "price_grid": {"entries": [{"model": e["model"], "source": e["source"], "captured_on": e["captured_on"],
                                        "effective_from": e["effective_from"], "effective_to": e["effective_to"]}
                                       for e in price_grid["entries"]]},
            "arms": result["arms"]}
    paired = None
    if report is not None:
        rep = json.loads(report.read_text(encoding="utf-8"))
        doc["totals_equal_committed_report"] = check_against_report(result, rep)
        paired = paired_reading(result, rep, price_grid)
        if paired is not None:
            doc["paired_reading"] = paired
    if streams_dir is not None:
        doc["exploration"] = exploration(records, sessions, streams_dir, price_grid, campaign_id, logs_dir)
        if paired is not None:
            doc["exploration_on_paired_set"] = exploration(
                records, sessions, streams_dir, price_grid, campaign_id, logs_dir, paired["paired_tasks"])
    return doc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m foundry.cost_breakdown", description=__doc__.splitlines()[0])
    parser.add_argument("--results", required=True, help="results-<campaign>.jsonl (the ledger is read beside it)")
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--report", default=None, help="report-<campaign>.json: totals are checked against it")
    parser.add_argument("--grid", default=str(GRID_PATH))
    parser.add_argument("--streams-dir", default=None, help="raw cloud transcripts kept off the repository")
    parser.add_argument("--session-logs-dir", default=None,
                        help="host session logs (<dir>/*/<session>.jsonl) for exact per-call output tokens")
    parser.add_argument("--out", default=None, help="write the aggregates here (never overwrites)")
    args = parser.parse_args(argv)
    try:
        doc = analyse(Path(args.results), ledger=Path(args.ledger) if args.ledger else None,
                      report=Path(args.report) if args.report else None, grid=Path(args.grid),
                      streams_dir=Path(args.streams_dir) if args.streams_dir else None,
                      logs_dir=Path(args.session_logs_dir) if args.session_logs_dir else None)
        text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
        if args.out:
            with Path(args.out).open("x", encoding="utf-8") as handle:
                handle.write(text)
        else:
            sys.stdout.write(text)
    except (BreakdownError, OSError, KeyError, ValueError) as exc:
        print(f"refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    check = doc.get("totals_equal_committed_report")
    return 1 if check is not None and not check["all_equal"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
