#!/usr/bin/env python3
"""SessionStart hook (PAT-247): warn about quota thresholds. Never blocks: always exit 0.

Bounded by ``check.HOOK_DEADLINE_S``; a reader still running then is reported inconnu.
Prints nothing when every monitored counter is read and below its threshold.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tooling"))


def main() -> None:
    try:
        from gauge import check

        cfg, problems = check.load_config(check.config_path())
        warnings, _ = check.check(cfg, check.HOOK_DEADLINE_S, check.HOOK_CODEX_TIMEOUT_S)
        lines = [f"gauge config: {p}" for p in problems] + [f"gauge: {w}" for w in warnings]
        if lines:
            text = "Quota alert (gauge, non blocking):\n" + "\n".join(lines)
            print(json.dumps({
                "systemMessage": text,
                "hookSpecificOutput": {"hookEventName": "SessionStart",
                                       "additionalContext": text},
            }))
    except BaseException:  # noqa: BLE001, S110 - never block a session
        pass
    sys.stdout.flush()
    os._exit(0)  # do not wait for a reader thread still running


if __name__ == "__main__":
    main()
