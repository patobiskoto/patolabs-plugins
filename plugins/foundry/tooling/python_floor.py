"""Single declaration of Foundry's minimum Python (PAT-265) and its startup check.

This module is deliberately outside the ``foundry`` package and written for Python 3.9
syntax, so that the CLI launcher and every hook can import it and report an explicit
message BEFORE importing any ``foundry`` module (which uses constructs newer than 3.9,
such as ``zip(strict=True)``, and launches ``python -P``, 3.11+). Nothing here imports ``foundry``.
"""
from __future__ import annotations

import datetime
import json
import os
import sys

MINIMUM_PYTHON = (3, 11)


def _dotted(version) -> str:
    return ".".join(str(part) for part in tuple(version)[:3])


def unsupported_message(version_info=None, executable=None) -> str | None:
    """Return an explicit message when the interpreter is below the minimum, else None."""
    info = sys.version_info if version_info is None else version_info
    if tuple(info[:2]) >= MINIMUM_PYTHON:
        return None
    path = sys.executable if executable is None else executable
    return (
        f"Foundry requires Python {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} or later; "
        f"the interpreter {path or '?'} is Python {_dotted(info)}. "
        f"Make `python3` resolve to Python {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} "
        "or later first on PATH."
    )


def require_for_cli() -> None:
    """CLI boundary: exit non-zero with the explicit message."""
    message = unsupported_message()
    if message:
        raise SystemExit(message)


def system_message(text: str) -> str:
    """Claude Code hook JSON carrying a user-visible message and no decision."""
    return json.dumps({"systemMessage": text}, ensure_ascii=False)


def hook_unsupported_output(hook: str) -> str | None:
    """Fail-open hook boundary: visible message (no deny) when the interpreter is too old."""
    message = unsupported_message()
    if message is None:
        return None
    return system_message(f"Foundry hook {hook} inactive: {message}")


def hook_failure_output(hook: str, exc: BaseException) -> str:
    """Fail-open diagnostic for an internal hook error: exception type name only."""
    return system_message(
        f"Foundry hook {hook} failed open (internal error {type(exc).__name__}); "
        "its checks did not run for this call. Run `foundry_cli.py doctor`."
    )


# The hooks' interpreter is what the host resolves as ``python3`` when it runs
# hooks/hooks.json; ``doctor`` cannot see it from its own process, so SessionStart records it.
OBSERVED_FILE = "hook-python.json"


def data_dir() -> str:
    """Same rule as ``foundry.registry.data_dir`` (asserted equal by a test)."""
    return os.environ.get("FOUNDRY_DATA") or os.path.expanduser("~/.config/foundry")


def record_interpreter() -> None:
    """Best effort: store the interpreter path and version running this hook (no secret)."""
    try:
        directory = data_dir()
        os.makedirs(directory, exist_ok=True)
        target = os.path.join(directory, OBSERVED_FILE)
        temp = f"{target}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump({"executable": sys.executable,
                       "version": list(tuple(sys.version_info)[:3]),
                       "recorded_at": datetime.datetime.now(datetime.timezone.utc)
                       .strftime("%Y-%m-%dT%H:%M:%SZ")}, handle)
        os.replace(temp, target)
    except Exception:
        return


def read_observed():
    """Return ``{"executable", "version" (3 ints), "recorded_at" (str or None)}`` or None."""
    try:
        with open(os.path.join(data_dir(), OBSERVED_FILE), encoding="utf-8") as handle:
            data = json.load(handle)
        version = tuple(data["version"])
        if (len(version) != 3 or not all(type(part) is int for part in version)
                or not isinstance(data["executable"], str)):
            return None
        recorded = data.get("recorded_at")
        return {"executable": data["executable"], "version": version,
                "recorded_at": recorded if isinstance(recorded, str) else None}
    except Exception:
        return None
