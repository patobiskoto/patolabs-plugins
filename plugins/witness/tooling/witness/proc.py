"""Bounded external processes: stdin from /dev/null, always a timeout (PAT-254), whole process group killed (PAT-262)."""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

REAP_SECONDS = 5.0


def _kill_group(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)  # the child leads its own session (start_new_session)
    except OSError:
        p.kill()
    try:
        p.communicate(timeout=REAP_SECONDS)  # a survivor outside the group may still hold the pipes
    except subprocess.TimeoutExpired:
        pass


def run(cmd: list[str], timeout: float, cwd: Path | None = None,
        env: dict[str, str] | None = None) -> tuple[int | None, str, str]:
    """(returncode, stdout, stderr); (None, "", reason) when it cannot be launched or times out.

    The child runs in its own process group; on timeout or interruption the whole group is killed, not only the
    direct child, so its own children do not outlive the wait. A child that leaves the group (a daemon it asks
    to start) is out of reach: unknown for xcodebuild and the simulator tools.
    """
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                             start_new_session=True)
    except OSError as exc:
        return None, "", f"cannot be launched ({type(exc).__name__})"
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(p)
        return None, "", f"timed out after {timeout:g}s"
    except BaseException:
        _kill_group(p)
        raise
    return p.returncode, out, err
