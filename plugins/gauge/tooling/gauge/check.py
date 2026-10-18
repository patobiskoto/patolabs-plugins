"""Threshold check over the readings (PAT-247). Never blocks: callers always exit 0.

An unreadable value is reported as ``inconnu``; it is never read as zero and never as
"below threshold". A counter whose threshold is ``null`` in the configuration is not
monitored and is not read.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import readers

# Percent used for codex, claude and linear; minutes used per month for github-actions.
DEFAULT_THRESHOLDS: dict[str, float | None] = {
    "codex": 80,
    "claude": 80,
    "linear": 80,
    "github-actions": None,
}
# Windows a monitored counter is expected to return; one missing is reported inconnu.
EXPECTED_LABELS = {
    "codex": ("primary", "secondary"),
    "claude": ("five_hour", "seven_day"),
    "linear": ("requests", "complexity"),
}
UNITS = {"codex": "%", "claude": "%", "linear": "%", "github-actions": " min"}
CONFIG_ENV = "GAUGE_CONFIG"
HOOK_DEADLINE_S = 6.0
HOOK_CODEX_TIMEOUT_S = 4.0


def config_path(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get(CONFIG_ENV):
        return Path(env[CONFIG_ENV])
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "gauge" / "config.json"


def load_config(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Return (config, problems). A missing file is the defaults, silently; a broken one
    is the defaults plus a problem line, never an exception."""
    cfg: dict[str, Any] = {"thresholds": dict(DEFAULT_THRESHOLDS)}
    if not path.exists():
        return cfg, []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise TypeError("root is not an object")
    except (OSError, ValueError, TypeError):
        return cfg, ["configuration file unreadable or invalid: defaults used"]
    problems = []
    given = doc.get("thresholds", {})
    if isinstance(given, dict):
        for key, value in given.items():
            if key not in DEFAULT_THRESHOLDS:
                problems.append(f"unknown threshold key ignored: {key}")
            elif value is None:
                cfg["thresholds"][key] = None
            elif isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                cfg["thresholds"][key] = value
            else:
                problems.append(f"invalid threshold for {key}: default kept")
    else:
        problems.append("thresholds is not an object: defaults used")
    streams = doc.get("claude_stream")
    if isinstance(streams, list) and all(isinstance(v, str) for v in streams):
        cfg["claude_stream"] = streams
    if isinstance(doc.get("claude_statusline_json"), str):
        cfg["claude_statusline_json"] = doc["claude_statusline_json"]
    return cfg, problems


def used_amount(counter: str, entry: dict[str, Any]) -> float | None:
    if counter == "linear":
        limit, remaining = entry.get("limit"), entry.get("remaining")
        if limit is None or remaining is None or limit <= 0:
            return None
        return (limit - remaining) * 100.0 / limit
    if counter == "github-actions":
        return entry.get("minutes_used")
    return entry.get("used_percent")


def evaluate(counter: str, reading: dict[str, Any], threshold: float) -> list[str]:
    """Warning lines for one counter; empty only when every value is read and below."""
    unit = UNITS[counter]
    if reading["status"] != "ok":
        return [f"{counter}: inconnu ({reading['reason']})"]
    lines = []
    for entry in reading["entries"]:
        value = used_amount(counter, entry)
        label = entry.get("label", "?")
        if value is None:
            lines.append(f"{counter} {label}: inconnu (value missing in the response)")
        elif value >= threshold:
            lines.append(f"{counter} {label}: {round(value, 1)}{unit} used, threshold "
                         f"{threshold}{unit}")
    seen = {entry.get("label") for entry in reading["entries"]}
    for label in EXPECTED_LABELS.get(counter, ()):
        if label not in seen:
            lines.append(f"{counter} {label}: inconnu (window missing in the response)")
    return lines


def _readers_for(cfg: dict[str, Any], codex_timeout: float) -> dict[str, Callable[[], Any]]:
    def claude() -> readers.Reading:
        if cfg.get("claude_statusline_json"):
            return readers.read_claude_statusline(cfg["claude_statusline_json"])
        if not cfg.get("claude_stream"):
            return readers.unknown(
                "claude", "gauge configuration",
                "no Claude source: set claude_stream or claude_statusline_json, or the claude threshold to null")
        return readers.read_claude_streams(cfg["claude_stream"])

    return {
        "codex": lambda: readers.read_codex(timeout=codex_timeout),
        "claude": claude,
        "linear": readers.read_linear,
        "github-actions": readers.read_github_actions,
    }


def check(
    cfg: dict[str, Any],
    deadline: float | None = None,
    codex_timeout: float = readers.CODEX_TIMEOUT_S,
    reader_map: dict[str, Callable[[], Any]] | None = None,
) -> tuple[list[str], list[str]]:
    """Return (warnings, monitored counters). Each reader runs in a daemon thread; with a
    deadline, a reader still running when it expires is reported inconnu."""
    rmap = reader_map or _readers_for(cfg, codex_timeout)
    monitored = [c for c in DEFAULT_THRESHOLDS if cfg["thresholds"].get(c) is not None]
    results: dict[str, Any] = {}

    def run(counter: str) -> None:
        try:
            results[counter] = rmap[counter]().as_dict()
        except Exception:  # noqa: BLE001 - a reader bug must degrade to inconnu, not to a crash
            results[counter] = None

    threads = {c: threading.Thread(target=run, args=(c,), daemon=True) for c in monitored}
    for t in threads.values():
        t.start()
    end = None if deadline is None else time.monotonic() + deadline
    for t in threads.values():
        t.join(None if end is None else max(0.0, end - time.monotonic()))
    warnings = []
    for counter in monitored:
        reading = results.get(counter)
        if reading is None:
            reading = {"status": "unknown", "reason": "reader failed or timed out"}
        warnings.extend(evaluate(counter, reading, cfg["thresholds"][counter]))
    return warnings, monitored
