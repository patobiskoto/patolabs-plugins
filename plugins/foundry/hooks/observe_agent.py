#!/usr/bin/env python3
"""Silently observe a routed Claude Agent after its host callback has fired.

The pre-hook stores a private, sanitized route projection keyed by an HMAC of
``tool_use_id``. This post-hook consumes it once. No tool input, response, error,
session, repository, issue, user, or machine value enters telemetry. It also appends a
``completed`` status event to the local invocation record (PLP-316, ``invocation_log.py``;
``unknown`` for a background launch, PLP-329); that record is read by no gate. The hook emits no
output or model context on success; an internal failure (or an unsupported Python) prints
one ``systemMessage`` and still fails open (PAT-265).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "tooling"))

import python_floor  # noqa: E402

python_floor.reexec_if_unsupported()  # PAT-267: before any stdin read
_UNSUPPORTED = python_floor.hook_unsupported_output("observe_agent")
if _UNSUPPORTED:  # PAT-265: fail open but visible, before any foundry import
    print(_UNSUPPORTED)
    raise SystemExit(0)

from foundry.routing_facades import claude_invocation_completed  # noqa: E402
from foundry.telemetry import TelemetryObserver  # noqa: E402
from foundry import invocation_log  # noqa: E402


def _is_background_launch(payload: dict) -> bool:
    """True when ``tool_response`` carries the async-launch marker (PLP-329).

    Observed in transcripts: the Agent ``toolUseResult`` of a background launch holds
    ``isAsync: true`` / ``status: "async_launched"``. That the hook payload carries the
    marker was observed on 2026-10-28 (PLP-335); which of the two keys is not observed.
    The evidence chain is in ``docs/model-routing.md``, "Local invocation record".
    """
    response = payload.get("tool_response")
    return isinstance(response, dict) and (
        response.get("isAsync") is True or response.get("status") == "async_launched")


def observe(payload: object, *, environ=None) -> bool:
    """Consume and privately record one real host callback, if it was prepared."""
    if not isinstance(payload, dict):
        return False
    correlation = payload.get("tool_use_id")
    event_name = payload.get("hook_event_name")
    if event_name == "PostToolUse":
        # PLP-329: PostToolUse fires at launch for a background Agent; its real end is
        # not observed (no hook wired), so the status stays unknown, never completed.
        status, failure_class = (("unknown", "unknown") if _is_background_launch(payload)
                                 else ("completed", "none"))
    elif event_name == "PostToolUseFailure":
        status, failure_class = "failed", "host"
    else:
        return False
    if isinstance(correlation, str) and correlation and isinstance(payload.get("cwd"), str):
        # PLP-316: local fail-open status update; read by no gate (invocation_log.py).
        invocation_log.record_completion(
            invocation_log.claude_invocation_id(correlation),
            status=status, failure_class=failure_class, root=payload["cwd"],
        )
    observer = TelemetryObserver.from_environ(environ)
    return bool(claude_invocation_completed(
        observer, correlation=correlation,
        status=status, failure_class=failure_class,
    ))


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        observe(payload, environ=os.environ)
    except Exception as exc:
        # Silent on the nominal path; fail-open per ADR-0005, but an internal failure
        # is made visible (PAT-265, type name only).
        print(python_floor.hook_failure_output("observe_agent", exc))


if __name__ == "__main__":
    main()
