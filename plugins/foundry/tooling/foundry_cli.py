#!/usr/bin/env python3
"""Permission-friendly launcher for the Foundry tooling.

Skills use one shell selector inside each command. Claude replaces the exact
`${CLAUDE_PLUGIN_ROOT}` tokens before the skill reaches the agent. In Codex, those
variables are empty for skill commands, so the selector uses `<foundry-root>`, derived
from the active skill path. Allowed-tools rules match on the command PREFIX, so
`Bash(python3:*)` matches a command starting with `python3` but not an env-var assignment.
Dev usage (`PYTHONPATH=tooling python3 -m foundry.<module>`) still works.
"""
import os
import runpy
import sys

_MODULES = ("query", "edit", "issue", "frame", "adr", "doctor", "registry",
            "setup_project", "configure", "routing", "telemetry", "local-scout",
            "local-code", "devhub-smoke", "epic-preview", "command-worker",
            "campaign-gate", "campaign-retry", "campaign-reconcile",
            "campaign-review-remediation", "campaign-legacy", "campaign-terminal",
            "cost-attribution", "delivery-audit")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if len(sys.argv) < 2 or sys.argv[1] not in _MODULES:
    raise SystemExit(f"usage: foundry_cli.py <{'|'.join(_MODULES)}> [args…]")

mod = sys.argv.pop(1)
if mod == "routing":
    # The launcher is the shared Claude/Codex boundary for routing diagnostics.
    # Keep configuration failures concise here, while leaving unexpected errors
    # untouched so their Python traceback remains available to developers.
    from foundry.routing import (
        RoutingConfigError,
        RoutingUnavailableError,
        main as routing_main,
    )

    try:
        routing_main(handle_config_errors=False)
    except (RoutingConfigError, RoutingUnavailableError):
        print("Configuration de routage invalide.", file=sys.stderr)
        raise SystemExit(2) from None
elif mod == "command-worker":
    runpy.run_module("foundry.command_runtime", run_name="__main__", alter_sys=False)
else:
    # Hyphenated CLI names remain shell-friendly while Python modules stay importable.
    runpy.run_module(f"foundry.{mod.replace('-', '_')}", run_name="__main__", alter_sys=False)
