"""Bounded external processes: stdin from /dev/null, always a timeout (PAT-252).

Each process runs in its own session (process group): on a timeout the whole group is killed, so a child the tool
started (a JVM behind a launcher script, for example) does not outlive the deadline. After the kill the pipes are drained
for at most `DRAIN_SECONDS`: a descendant that opened a session of its own and keeps the pipes open is left running, and
whatever it still writes is dropped.
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from pathlib import Path

DRAIN_SECONDS = 2


def _reap(p: subprocess.Popen) -> None:
    """Kill the group, then collect the output for a bounded time; never wait for a descendant that escaped the group."""
    _kill_group(p)
    try:
        p.communicate(timeout=DRAIN_SECONDS)
    except subprocess.TimeoutExpired:
        for pipe in (p.stdout, p.stderr):
            with contextlib.suppress(OSError, ValueError):
                if pipe:
                    pipe.close()
        p.wait()


def _kill_group(p: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(p.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        p.kill()


def run(cmd: list[str], timeout: float, cwd: Path | None = None,
        env: dict[str, str] | None = None) -> tuple[int | None, str, str]:
    """(returncode, stdout, stderr); (None, "", reason) when it cannot be launched or times out."""
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                             start_new_session=True)
    except OSError as exc:
        return None, "", f"cannot be launched ({type(exc).__name__})"
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _reap(p)
        return None, "", f"timed out after {timeout:g}s"
    except BaseException:
        _reap(p)
        raise
    return p.returncode, out, err
