"""Bounded external processes: stdin from /dev/null, always a timeout (PAT-254)."""
from __future__ import annotations

import subprocess
from pathlib import Path


def run(cmd: list[str], timeout: float, cwd: Path | None = None) -> tuple[int | None, str, str]:
    """(returncode, stdout, stderr); (None, "", reason) when it cannot be launched or times out."""
    try:
        cp = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            check=False, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "", f"timed out after {timeout:g}s"
    except OSError as exc:
        return None, "", f"cannot be launched ({type(exc).__name__})"
    return cp.returncode, cp.stdout, cp.stderr
