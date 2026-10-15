"""Per-issue review-round report (PAT-175), read-only.

Rounds and outcomes come from the local acceptance-proof store
(``AcceptanceProofStore`` in ``routing.py``); timestamps from the review ledger record
(``ReviewDeduplicator``) bound to each proof by its diff hash.  Tokens come only from host
session logs the caller points at, read with ``cost_attribution.read_host_log`` outside the
invocation path (FOUNDRY-ADR-0015).  A Claude log under a ``subagents`` directory is a
sub-agent log, by position only (as ``cache_ttl_replay`` does).  Every value that cannot be
read is ``None`` (``unknown``), never 0, and no claim id, proof id or log content is emitted.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from foundry.cost_attribution import BILLING_TOKEN_KEYS, CostAttributionError, read_host_log
from foundry.routing import AcceptanceProofStore, ReviewDeduplicator, RoutingConfigError

_TOKEN_NAMES = {
    "input_tokens": "input",
    "cached_input_tokens": "cache_read",
    "cache_write_input_tokens": "cache_write",
    "output_tokens": "output",
}


def _seconds(start: str, end: str) -> float:
    def parse(value: str) -> datetime:
        return datetime.fromisoformat(value[:-1] + "+00:00")

    return (parse(end) - parse(start)).total_seconds()


def _ledger_times(ledger_dir: Path, diff_hash: str) -> tuple[str | None, str | None]:
    try:
        record = json.loads((ledger_dir / diff_hash).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(record, dict):
        return None, None
    claim = record.get("claim")
    started = claim.get("started_at") if isinstance(claim, dict) else None
    valid = ReviewDeduplicator._valid_utc_timestamp
    return (started if valid(started) else None,
            record.get("completed_at") if valid(record.get("completed_at")) else None)


def _rounds(issue: str, repository: str, state_dir) -> tuple[list[dict], int, int]:
    store = AcceptanceProofStore(repository, state_dir)
    ledger_dir = ReviewDeduplicator(repository, state_dir).directory
    rounds, carried, unreadable = [], 0, 0
    markers = sorted(p for p in store.directory.iterdir() if not p.name.startswith(".")) \
        if store.directory.is_dir() else []
    for marker in markers:
        try:
            proof = store._validated_proof(marker)
        except RoutingConfigError:
            unreadable += 1
            continue
        if proof["issue"]["id"] != issue:
            continue
        if "carried_from" in proof:
            carried += 1
            continue
        verdicts = [c["verdict"] for c in proof["issue"]["criteria"]]
        started, completed = _ledger_times(ledger_dir, proof["coordinates"]["diff_hash"])
        all_pass = all(v == "pass" for v in verdicts)
        rounds.append({
            "generation": proof["review"]["generation"],
            "quality": proof["quality"],
            "all_pass": all_pass,
            "validated": proof["quality"] == "mergeable" and all_pass,
            "criteria": len(verdicts),
            "started_at": started,
            "completed_at": completed,
        })
    rounds.sort(key=lambda r: (r["completed_at"] is None, r["completed_at"] or ""))
    for number, entry in enumerate(rounds, start=1):
        entry["round"] = number
    return rounds, carried, unreadable


def _claude_files(paths) -> list[tuple[Path, str]]:
    found: dict[Path, str] = {}
    for raw in paths:
        main = Path(raw)
        found.setdefault(main, "coordinator")
        for sub in sorted((main.parent / main.stem / "subagents").rglob("*.jsonl")):
            found.setdefault(sub, "subagents")
    return list(found.items())


def _tokens(files: list[tuple[Path, str, str]]) -> dict:
    """files: (path, host, class).  Unread classes stay None."""
    totals: dict[str, dict | None] = {"coordinator": None, "subagents": None, "codex_unclassified": None}
    unreadable = 0
    for path, host, klass in files:
        try:
            rows = read_host_log(host, path)
        except CostAttributionError:
            unreadable += 1
            continue
        bucket = totals[klass] or {name: 0 for name in _TOKEN_NAMES.values()}
        for row in rows:
            if "tokens" not in row:
                continue
            for key in BILLING_TOKEN_KEYS:
                bucket[_TOKEN_NAMES[key]] += row["tokens"][key]
        totals[klass] = bucket
    return {**totals, "unreadable_logs": unreadable}


def report(issue: str, repository: str, *, state_dir=None, claude_sessions=(), codex_sessions=()) -> dict:
    rounds, carried, unreadable = _rounds(issue, repository, state_dir)
    spans = [(r["started_at"], r["completed_at"]) for r in rounds if r["started_at"] and r["completed_at"]]
    duration = (_seconds(min(s for s, _ in spans), max(e for _, e in spans)) if spans else None)
    files = [(p, "claude", k) for p, k in _claude_files(claude_sessions)]
    files += [(Path(p), "codex", "codex_unclassified") for p in codex_sessions]
    return {
        "issue": issue,
        "rounds": len(rounds),
        "validated_rounds": sum(r["validated"] for r in rounds),
        "blocked_rounds": sum(r["quality"] == "blocked" for r in rounds),
        "carried_proofs": carried,
        "unreadable_proofs": unreadable,
        "outcome": ("validated" if rounds and rounds[-1]["validated"] else
                    "blocked" if rounds else "unknown"),
        "duration_seconds": duration,
        "duration_rounds_timed": len(spans),
        "per_round": rounds,
        "tokens": _tokens(files),
    }


def render(result: dict) -> str:
    def unknown(value):
        return "unknown" if value is None else value

    lines = [f"issue {result['issue']}: {result['rounds']} round(s), outcome {result['outcome']}",
             f"validated {result['validated_rounds']}, blocked {result['blocked_rounds']}, "
             f"carried proofs {result['carried_proofs']}, unreadable proofs {result['unreadable_proofs']}",
             f"duration_seconds {unknown(result['duration_seconds'])} "
             f"({result['duration_rounds_timed']} round(s) timed)"]
    for r in result["per_round"]:
        lines.append(f"  round {r['round']}: quality {r['quality']}, all_pass {r['all_pass']}, "
                     f"generation {r['generation']}")
    for klass in ("coordinator", "subagents", "codex_unclassified"):
        value = result["tokens"][klass]
        lines.append(f"tokens {klass}: " + ("unknown" if value is None else
                     ", ".join(f"{k} {v}" for k, v in value.items())))
    lines.append(f"unreadable logs {result['tokens']['unreadable_logs']}")
    return "\n".join(lines)
