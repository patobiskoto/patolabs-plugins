#!/usr/bin/env python3
"""SessionStart hook (PAT-249): present the last resume state, re-verified. Never blocks.

Bounded by ``load.HOOK_DEADLINE_S`` for the re-verification and by a join on a daemon
thread; whatever is not done is reported unknown. Always exit 0.
"""
import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tooling"))

JOIN_MARGIN_S = 2.0  # beyond the re-verification budget; still below the hooks.json timeout (10 s)


def main() -> None:
    text = None
    try:
        import relay_cli
        from relay import load

        box: dict = {}

        def work() -> None:
            try:
                root = relay_cli.repo_root()
                if root is None:
                    return  # not a repository: nothing to reload, say nothing
                state, message = load.read_state(relay_cli.state_path(root))
                if state is None:
                    box["text"] = message
                    return
                box["partial"] = load.guard("\n".join([load.HEADER] + load.render_state(state)))
                box["text"] = load.present(state, root, load.HOOK_DEADLINE_S)
            except BaseException:  # noqa: BLE001 - reported below, never a traceback
                box["text"] = "relay: resume state unknown (hook error)."

        t = threading.Thread(target=work, daemon=True)
        t.start()
        t.join(load.HOOK_DEADLINE_S + JOIN_MARGIN_S)
        if "text" in box:
            text = box["text"]
        elif "partial" in box:
            text = (box["partial"] + "\nrelay: re-verification did not finish in time: "
                    "every divergence is unknown; run `relay show` or `relay load`.")
        elif t.is_alive():
            text = "relay: resume state unknown (read did not finish in time)."
    except BaseException:  # noqa: BLE001 - never block a session, never print a traceback
        text = "relay: resume state unknown (hook error)."
    if text:
        print(json.dumps({
            "systemMessage": text,
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text},
        }))
    sys.stdout.flush()
    os._exit(0)  # do not wait for a thread still running


if __name__ == "__main__":
    main()
