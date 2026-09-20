"""Reproducible, provider-neutral measurement helpers for query-profile evidence.

Metrics describe serialized CLI stdout, never provider billing. A command sequence
keeps each command/page separate, so pagination costs cannot be hidden by wrapping
several results in a synthetic JSON object.
"""
from __future__ import annotations

import time
from typing import Callable, Iterable

from foundry.query import serialize_stdout


TOKENIZER = None


Command = Callable[[], object] | tuple[str, Callable[[], object]]


def _counter_delta(counter: Callable[[], int] | None, before: int | None) -> int | None:
    return counter() - before if counter is not None and before is not None else None


def _capture(
    commands: Iterable[Command],
    tracker_calls: Callable[[], int] | None,
    http_calls: Callable[[], int] | None,
) -> dict:
    rows = []
    for index, item in enumerate(commands, 1):
        if isinstance(item, tuple):
            identifier, command = item
        else:
            command = item
            identifier = getattr(command, "__name__", None) or f"command-{index}"
        before_tracker = tracker_calls() if tracker_calls else None
        before_http = http_calls() if http_calls else None
        started = time.perf_counter()
        output = serialize_stdout(command())
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        rows.append({
            "command": identifier,
            "serialized_bytes": len(output.encode("utf-8")),
            "lines": output.count("\n"),
            "estimated_tokens": None,
            "tokenizer": TOKENIZER,
            "tracker_calls": _counter_delta(tracker_calls, before_tracker),
            "http_calls": _counter_delta(http_calls, before_http),
            "duration_ms": duration_ms,
        })
    return {
        "serialized_bytes": sum(row["serialized_bytes"] for row in rows),
        "lines": sum(row["lines"] for row in rows),
        "estimated_tokens": None,
        "tokenizer": TOKENIZER,
        "tracker_calls": (sum(row["tracker_calls"] for row in rows)
                          if tracker_calls else None),
        "http_calls": (sum(row["http_calls"] for row in rows) if http_calls else None),
        "duration_ms": round(sum(row["duration_ms"] for row in rows), 3),
        "command_count": len(rows),
        "commands": rows,
    }


def measure_sequence(
    workflow: str,
    before: Callable[[], Iterable[Command]],
    after: Callable[[], Iterable[Command]],
    *,
    tracker_calls: Callable[[], int] | None = None,
    http_calls: Callable[[], int] | None = None,
) -> dict:
    """Measure the exact initial and updated command sequences for one workflow."""
    return {"workflow": workflow, "tokenizer": TOKENIZER,
            "before": _capture(before(), tracker_calls, http_calls),
            "after": _capture(after(), tracker_calls, http_calls)}


def measure(workflow: str, before: Callable[[], object], after: Callable[[], object]) -> dict:
    """Compatibility wrapper for one-command callers."""
    return measure_sequence(workflow, lambda: (before,), lambda: (after,))
