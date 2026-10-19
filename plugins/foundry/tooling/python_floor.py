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
import shutil
import subprocess
import sys

MINIMUM_PYTHON = (3, 11)

# PAT-267: closed search list used when the running interpreter is below the minimum.
# 1. ``python3.N`` for N from NEWEST_MINOR_TRIED down to the minimum minor, then ``python3``,
#    each resolved on the current PATH; 2. the fixed absolute paths below, then
#    ``~/.local/bin/python3.N`` for the same N. No other directory is scanned and no
#    environment variable chooses the interpreter.
NEWEST_MINOR_TRIED = 20
FIXED_PATHS = ("/opt/homebrew/bin/python3", "/usr/local/bin/python3")
LOCAL_BIN = "~/.local/bin"
PROBE_TIMEOUT_SECONDS = 2
REEXEC_GUARD = "FOUNDRY_PYTHON_REEXEC"
REEXEC_FROM = "FOUNDRY_PYTHON_REEXEC_FROM"  # "<dotted version> <executable>", display only


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


def _candidates():
    """Yield candidate interpreter paths in the fixed order, without duplicates."""
    minor_range = range(NEWEST_MINOR_TRIED, MINIMUM_PYTHON[1] - 1, -1)
    names = [f"python{MINIMUM_PYTHON[0]}.{minor}" for minor in minor_range]
    seen = set()
    local = os.path.expanduser(LOCAL_BIN)
    ordered = [shutil.which(name) for name in names + ["python3"]]
    ordered += list(FIXED_PATHS) + [os.path.join(local, name) for name in names]
    for path in ordered:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
                yield path


def _probe(path):
    """Return the (major, minor) the candidate reports, or None. stdin is not touched."""
    try:
        answer = subprocess.run(
            [path, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=PROBE_TIMEOUT_SECONDS)
        major, minor = answer.stdout.split()
        return (int(major), int(minor)) if answer.returncode == 0 else None
    except Exception:
        return None


_attempted = False


def reexec_if_unsupported(argv=None) -> None:
    """PAT-267: below the minimum, re-run this same script under a supported interpreter.

    Returns (so the PAT-265 message path runs) when the interpreter is supported, when a
    re-execution was already made (``REEXEC_GUARD``, no loop), when no candidate of the
    closed list reports a supported version, or when ``os.execv`` fails. Nothing is read
    from stdin before the exec.
    """
    global _attempted
    if unsupported_message() is None or _attempted or os.environ.get(REEXEC_GUARD):
        return
    _attempted = True
    args = list(sys.argv if argv is None else argv)
    current = os.path.realpath(sys.executable) if sys.executable else None
    for path in _candidates():
        if os.path.realpath(path) == current:
            continue
        version = _probe(path)
        if version is None or version < MINIMUM_PYTHON:
            continue
        os.environ[REEXEC_GUARD] = "1"
        os.environ[REEXEC_FROM] = f"{_dotted(sys.version_info)} {sys.executable or '?'}"
        try:
            os.execv(path, [path] + args)
        except Exception:
            pass
        os.environ.pop(REEXEC_GUARD, None)
        os.environ.pop(REEXEC_FROM, None)
        return


def _reexec_origin():
    """Interpreter this process was re-executed from (display only), or None."""
    try:
        version, executable = os.environ[REEXEC_FROM].split(" ", 1)
        parts = tuple(int(part) for part in version.split("."))
        return {"executable": executable, "version": parts} if len(parts) == 3 else None
    except Exception:
        return None


def require_for_cli() -> None:
    """CLI boundary: exit non-zero with the explicit message."""
    reexec_if_unsupported()
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
        record = {"executable": sys.executable,
                  "version": list(tuple(sys.version_info)[:3]),
                  "recorded_at": datetime.datetime.now(datetime.timezone.utc)
                  .strftime("%Y-%m-%dT%H:%M:%SZ")}
        origin = _reexec_origin()
        if origin:
            record["reexec_from"] = {"executable": origin["executable"],
                                     "version": list(origin["version"])}
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        os.replace(temp, target)
    except Exception:
        return


def read_observed():
    """Return ``{"executable", "version" (3 ints), "recorded_at" (str or None), "reexec_from"
    ({"executable", "version"} or None, absent from records older than PAT-267)}`` or None."""
    try:
        with open(os.path.join(data_dir(), OBSERVED_FILE), encoding="utf-8") as handle:
            data = json.load(handle)
        version = tuple(data["version"])
        if (len(version) != 3 or not all(type(part) is int for part in version)
                or not isinstance(data["executable"], str)):
            return None
        recorded = data.get("recorded_at")
        origin = data.get("reexec_from")
        try:
            origin_version = tuple(origin["version"])
            valid = (isinstance(origin["executable"], str) and len(origin_version) == 3
                     and all(type(part) is int for part in origin_version))
            origin = ({"executable": origin["executable"], "version": origin_version}
                      if valid else None)
        except Exception:
            origin = None
        return {"executable": data["executable"], "version": version,
                "recorded_at": recorded if isinstance(recorded, str) else None,
                "reexec_from": origin}
    except Exception:
        return None
