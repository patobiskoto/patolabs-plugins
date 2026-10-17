"""Plain-text rendering of Readings, times in the local zone."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def local(iso: str | None) -> str:
    if not iso:
        return "unknown"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    except ValueError:
        return "unknown"


def _fmt(value: Any, suffix: str = "") -> str:
    return "unknown" if value is None else f"{value}{suffix}"


def entry_line(entry: dict[str, Any]) -> str:
    label = entry.get("label", "?")
    parts = []
    if "used_percent" in entry:
        parts.append(f"used {_fmt(entry['used_percent'], '%')}")
    if "window_minutes" in entry:
        parts.append(f"window {entry['window_minutes']} min")
    if "limit" in entry:
        parts.append(f"remaining {_fmt(entry.get('remaining'))}/{_fmt(entry['limit'])}")
    if "minutes_used" in entry:
        parts.append(
            f"{_fmt(entry['minutes_used'])} min, gross {_fmt(entry['gross_usd'])} USD, "
            f"net {_fmt(entry['net_usd'])} USD, included quota unknown"
        )
    if "resets_at" in entry:
        parts.append(f"resets {local(entry['resets_at'])}")
    return f"  {label}: " + ", ".join(parts)


def render(readings: list[dict[str, Any]]) -> str:
    lines = []
    for r in readings:
        lines.append(f"{r['counter']}: {'unknown' if r['status'] != 'ok' else 'ok'}")
        if r["status"] != "ok":
            lines.append(f"  reason: {r['reason']}")
        else:
            lines.extend(entry_line(e) for e in r["entries"])
            if r["reason"]:
                lines.append(f"  note: {r['reason']}")
        lines.append(f"  source: {r['source']}; read {local(r['read_at'])}")
    return "\n".join(lines)
