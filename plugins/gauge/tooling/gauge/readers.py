"""Read-only quota readers (PAT-246).

Every reader returns a ``Reading``: ``status`` is ``ok`` or ``unknown``. Anything that
cannot be read (missing right, session, field, or tool) is ``unknown`` with a reason,
never zero. Readers keep only whitelisted numeric fields: no token, login, account
identifier, credits balance or repository name is ever copied into a Reading.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LINEAR_URL = "https://api.linear.app/graphql"
CODEX_TIMEOUT_S = 15.0
SUBPROCESS_TIMEOUT_S = 30.0
PROBE_TIMEOUT_S = 180.0
PROBE_PROMPT = "Reply with the single word: ok"
GAUGE_VERSION = "0.1.0"


@dataclass
class Reading:
    counter: str
    status: str  # "ok" | "unknown"
    source: str
    read_at: str
    reason: str = ""
    entries: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "counter": self.counter,
            "status": self.status,
            "source": self.source,
            "read_at": self.read_at,
            "reason": self.reason,
            "entries": self.entries,
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def unknown(counter: str, source: str, reason: str) -> Reading:
    return Reading(counter, "unknown", source, now_iso(), reason=reason)


def _num(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def epoch_to_iso(value: Any) -> str | None:
    """Unix seconds, or milliseconds when above 1e11, to a UTC ISO string."""
    number = _num(value)
    if number is None or number <= 0:
        return None
    if number > 1e11:
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, timezone.utc).replace(microsecond=0).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _window(label: str, used_percent: Any, resets_at: Any, minutes: Any = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"label": label}
    used = _num(used_percent)
    entry["used_percent"] = round(used, 2) if used is not None else None
    entry["resets_at"] = epoch_to_iso(resets_at)
    mins = _num(minutes)
    if mins is not None:
        entry["window_minutes"] = mins
    return entry


# ---------------------------------------------------------------- Codex

def parse_codex_rate_limits(result: Any) -> list[dict[str, Any]]:
    limits = result.get("rateLimits") if isinstance(result, dict) else None
    if not isinstance(limits, dict):
        return []
    entries = []
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if isinstance(window, dict) and _num(window.get("usedPercent")) is not None:
            entries.append(
                _window(
                    key,
                    window.get("usedPercent"),
                    window.get("resetsAt"),
                    window.get("windowDurationMins"),
                )
            )
    return entries


def read_codex(
    popen: Callable[..., Any] = subprocess.Popen,
    timeout: float = CODEX_TIMEOUT_S,
    which: Callable[[str], str | None] = shutil.which,
) -> Reading:
    source = "codex app-server account/rateLimits/read"
    if which("codex") is None:
        return unknown("codex", source, "codex executable not found")
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientInfo": {"name": "gauge", "version": GAUGE_VERSION}}},
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"},
    ]
    try:
        proc = popen(
            ["codex", "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return unknown("codex", source, "codex app-server could not start")
    result: Any = None
    error = ""
    try:
        for message in requests:
            proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + timeout
        # A blocking readline cannot honour the deadline alone: a timer kills the child.
        timer = threading.Timer(max(0.0, deadline - time.monotonic()), proc.kill)
        timer.start()
        try:
            for line in proc.stdout:
                try:
                    doc = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(doc, dict) and doc.get("id") == 2:
                    if "error" in doc:
                        error = "app-server returned an error (no rate limits)"
                    else:
                        result = doc.get("result")
                    break
        finally:
            timer.cancel()
    except (OSError, ValueError):
        error = "app-server pipe failed"
    finally:
        with contextlib.suppress(Exception):  # best-effort cleanup
            proc.kill()
            proc.wait(timeout=5)
    if error:
        return unknown("codex", source, error)
    entries = parse_codex_rate_limits(result)
    if not entries:
        return unknown("codex", source, "no rate-limit window in the response (or timeout)")
    return Reading("codex", "ok", source, now_iso(), entries=entries)


# ---------------------------------------------------------------- Claude

def parse_claude_stream_text(text: str) -> dict[str, Any] | None:
    """The ``rate_limit_info`` of the last ``rate_limit_event`` line, else None."""
    last = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict) and doc.get("type") == "rate_limit_event":
            info = doc.get("rate_limit_info")
            if isinstance(info, dict):
                last = info
    return last


def claude_entries_from_event(info: dict[str, Any]) -> list[dict[str, Any]]:
    entries = []
    windows = info.get("unifiedWindows")
    if isinstance(windows, dict):
        for name in ("five_hour", "seven_day"):
            window = windows.get(name)
            if not isinstance(window, dict):
                continue
            utilization = _num(window.get("utilization"))
            if utilization is None:
                continue
            entries.append(_window(name, utilization * 100, window.get("resetsAt")))
    return entries


def _file_mtime_iso(path: Path) -> str | None:
    try:
        return epoch_to_iso(path.stat().st_mtime)
    except OSError:
        return None


def read_claude_streams(paths: list[str]) -> Reading:
    source = "claude stream-json rate_limit_event (operator-provided file)"
    if not paths:
        return unknown("claude", source, "no stream file given (--claude-stream)")
    best: tuple[float, dict[str, Any], str | None] | None = None
    for raw in paths:
        path = Path(raw)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            mtime = path.stat().st_mtime
        except OSError:
            continue
        info = parse_claude_stream_text(text)
        if info is not None and (best is None or mtime >= best[0]):
            best = (mtime, info, epoch_to_iso(mtime))
    if best is None:
        return unknown("claude", source, "no readable rate_limit_event in the given files")
    entries = claude_entries_from_event(best[1])
    if not entries:
        return unknown("claude", source, "rate_limit_event has no unifiedWindows utilization")
    for key in ("status", "rateLimitType", "isUsingOverage"):
        value = best[1].get(key)
        if isinstance(value, (str, bool)):
            entries[0].setdefault("event", {})[key] = value
    reading = Reading("claude", "ok", source, now_iso(), entries=entries)
    reading.reason = f"event observed no later than {best[2]}"
    return reading


def read_claude_statusline(path: str) -> Reading:
    source = "claude statusLine JSON rate_limits (operator-provided file)"
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return unknown("claude", source, "statusline file unreadable")
    doc = None
    candidates = [text] + text.splitlines()[::-1]
    for chunk in candidates:
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            doc = parsed
            break
    limits = doc.get("rate_limits") if isinstance(doc, dict) else None
    if not isinstance(limits, dict):
        return unknown("claude", source, "no rate_limits field (absent before the first response)")
    entries = []
    for name in ("five_hour", "seven_day"):
        window = limits.get(name)
        if isinstance(window, dict) and _num(window.get("used_percentage")) is not None:
            entries.append(_window(name, window.get("used_percentage"), window.get("resets_at")))
    if not entries:
        return unknown("claude", source, "rate_limits has no used_percentage")
    reading = Reading("claude", "ok", source, now_iso(), entries=entries)
    mtime = _file_mtime_iso(Path(path))
    if mtime:
        reading.reason = f"file written no later than {mtime}"
    return reading


def read_claude_probe(
    budget_usd: str | None,
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> Reading:
    """The only paid read: one minimal ``claude -p`` call, capped by ``--max-budget-usd``."""
    source = "claude -p probe (paid, opt-in)"
    try:
        amount = float(budget_usd) if budget_usd is not None else 0.0
    except ValueError:
        amount = 0.0
    if amount <= 0:
        return unknown("claude", source, "probe refused: a positive --probe-budget-usd is required")
    if which("claude") is None:
        return unknown("claude", source, "claude executable not found")
    try:
        done = run(
            ["claude", "-p", "--output-format", "stream-json", "--verbose",
             "--max-budget-usd", str(budget_usd), PROBE_PROMPT],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return unknown("claude", source, "probe failed to run")
    info = parse_claude_stream_text(done.stdout or "")
    entries = claude_entries_from_event(info) if info else []
    if not entries:
        return unknown("claude", source, "probe stream carried no rate_limit_event windows")
    return Reading("claude", "ok", source, now_iso(), entries=entries)


# ---------------------------------------------------------------- Linear

def _header_number(headers: Any, name: str) -> float | None:
    raw = headers.get(name) if headers is not None else None
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def read_linear(
    env: dict[str, str] | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> Reading:
    source = "linear api x-ratelimit-* headers"
    token = (env if env is not None else os.environ).get("LINEAR_API_TOKEN", "")
    if not token:
        return unknown("linear", source, "LINEAR_API_TOKEN is not set")
    request = urllib.request.Request(
        LINEAR_URL,
        data=json.dumps({"query": "{ viewer { id } }"}).encode(),
        headers={"Content-Type": "application/json", "Authorization": token},
        method="POST",
    )
    headers = None
    try:
        with opener(request, timeout=SUBPROCESS_TIMEOUT_S) as response:
            headers = response.headers
    except urllib.error.HTTPError as exc:  # a 429 still carries the counters
        headers = exc.headers
    except (urllib.error.URLError, OSError):
        return unknown("linear", source, "request failed")
    entries = []
    for kind in ("requests", "complexity"):
        limit = _header_number(headers, f"x-ratelimit-{kind}-limit")
        remaining = _header_number(headers, f"x-ratelimit-{kind}-remaining")
        reset = _header_number(headers, f"x-ratelimit-{kind}-reset")
        if limit is None and remaining is None:
            continue
        entry: dict[str, Any] = {"label": kind, "limit": limit, "remaining": remaining}
        entry["resets_at"] = epoch_to_iso(reset)
        entries.append(entry)
    if not entries:
        return unknown("linear", source, "no x-ratelimit-* header in the response")
    return Reading("linear", "ok", source, now_iso(), entries=entries)


# ---------------------------------------------------------------- GitHub Actions

def parse_github_usage(doc: Any) -> list[dict[str, Any]]:
    items = doc.get("usageItems") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return []
    months: dict[str, dict[str, float]] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("product") != "actions":
            continue
        date = item.get("date")
        month = date[:7] if isinstance(date, str) and len(date) >= 7 else "unknown-month"
        bucket = months.setdefault(month, {"minutes": 0.0, "gross": 0.0, "net": 0.0})
        if item.get("unitType") == "Minutes" and _num(item.get("quantity")) is not None:
            bucket["minutes"] += item["quantity"]
        for key, field_name in (("gross", "grossAmount"), ("net", "netAmount")):
            value = _num(item.get(field_name))
            if value is not None:
                bucket[key] += value
    return [
        {
            "label": month,
            "minutes_used": round(b["minutes"], 2),
            "gross_usd": round(b["gross"], 4),
            "net_usd": round(b["net"], 4),
            "included_quota": None,
        }
        for month, b in sorted(months.items())
    ]


def read_github_actions(
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> Reading:
    source = "gh api /users/<login>/settings/billing/usage"
    if which("gh") is None:
        return unknown("github-actions", source, "gh executable not found")

    def call(args: list[str]) -> str | None:
        try:
            done = run(["gh", *args], capture_output=True, text=True,
                       timeout=SUBPROCESS_TIMEOUT_S, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    login = (call(["api", "user", "--jq", ".login"]) or "").strip()
    if not login:
        return unknown("github-actions", source, "gh login unreadable (not authenticated)")
    body = call(["api", f"/users/{login}/settings/billing/usage"])
    if body is None:
        return unknown("github-actions", source,
                       "billing usage refused or unavailable (needs the `user` scope)")
    try:
        entries = parse_github_usage(json.loads(body))
    except json.JSONDecodeError:
        entries = []
    if not entries:
        return unknown("github-actions", source, "no Actions usage item in the response")
    return Reading("github-actions", "ok", source, now_iso(), entries=entries)
