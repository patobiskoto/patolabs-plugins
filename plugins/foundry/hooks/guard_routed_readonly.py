#!/usr/bin/env python3
"""Fail-closed Bash guard for Foundry's internally routed read-only agents."""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHELL_SYNTAX = re.compile(r"[\x00-\x1f\x7f;&|`$<>()[\]{}*?!#~]")
_READONLY_PREFIXES = ("routed-readonly-", "foundry:routed-readonly-")


def is_routed_readonly(agent_type: object) -> bool:
    return isinstance(agent_type, str) and agent_type.startswith(_READONLY_PREFIXES)


def is_safe_absolute_root(value: str) -> bool:
    return Path(value).is_absolute() and not _SHELL_SYNTAX.search(value)


def allows_verified_diff(command: str) -> bool:
    """Allow exactly the shared read-review verifier, with no shell composition."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    expected_launcher = PLUGIN_ROOT / "tooling" / "foundry_cli.py"
    supplied_launcher = tokens[1] if len(tokens) > 1 else ""
    launcher_matches = supplied_launcher == "${CLAUDE_PLUGIN_ROOT}/tooling/foundry_cli.py"
    if not launcher_matches:
        try:
            launcher_matches = Path(supplied_launcher).resolve() == expected_launcher
        except (OSError, RuntimeError):
            launcher_matches = False
    return (
        len(tokens) == 12
        and tokens[0] == "python3"
        and launcher_matches
        and tokens[2:4] == ["routing", "read-review"]
        and tokens[4] == "--diff-hash"
        and bool(_SHA256.fullmatch(tokens[5]))
        and tokens[6] == "--claim-id"
        and bool(_SHA256.fullmatch(tokens[7]))
        and tokens[8] == "--root"
        and is_safe_absolute_root(tokens[9])
        and tokens[10] == "--base"
        and bool(_GIT_SHA.fullmatch(tokens[11]))
    )


def decision(agent_type: object, command: object) -> str | None:
    if not is_routed_readonly(agent_type):
        return None
    if isinstance(command, str) and allows_verified_diff(command):
        return None
    return (
        "Cet agent Foundry est read-only : Bash est limité à `routing read-review "
        "--diff-hash <sha256> --claim-id <claim-id> --root <root-absolue> "
        "--base <sha-git>`. Utilise Read/Grep/Glob pour le reste."
    )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        command = (payload.get("tool_input") or {}).get("command")
        reason = decision(payload.get("agent_type"), command)
        if reason:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }}, ensure_ascii=False))
    except Exception:
        return  # ADR-0005: hooks are portable discipline, never a security boundary


if __name__ == "__main__":
    main()
