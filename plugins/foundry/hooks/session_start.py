#!/usr/bin/env python3
"""SessionStart hook — inject the Foundry contract into every session of a
REGISTERED repo, so the process is active without anyone invoking a skill.

This is the mechanism behind ADR-0001's "contracts declared per consumer
project": instead of hand-editing an AGENTS.md in each repo, the plugin
injects the contract itself. Registry read only, plus one write of the
interpreter record `hook-python.json` (`python_floor.record_interpreter`) — NO
network call at session start. It always exits 0 (a hook must never break a
session): an invalid tracker binding is reported as additionalContext, any other
exception in main() as one systemMessage naming its type; an unreadable stdin and
a failed interpreter record stay silent.

The launcher path is INLINED (not "$CLAUDE_PLUGIN_ROOT"): that variable is
substituted in hook/skill command strings but is not exported to the agent's
Bash sessions, so a literal reference would hand out a broken command.
"""
import json
import os
import sys

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PLUGIN_ROOT, "tooling"))

import python_floor  # noqa: E402

# PAT-265: record the interpreter that runs the hooks (read by `doctor`), then, if it is
# below the minimum, fail open with a visible message before any foundry import.
python_floor.record_interpreter()
_UNSUPPORTED = python_floor.hook_unsupported_output("session_start")
if _UNSUPPORTED:
    print(_UNSUPPORTED)
    raise SystemExit(0)

_CONTRACT = """\
Ce repo est piloté par le plugin Foundry (projet tracker : {key}, provider : {tracker}).
Contrat de processus — ces règles sont des invariants, pas des suggestions :
- Toute PR s'ouvre via le skill foundry:open-pr et se merge via foundry:merge-pr
  (Claude : /foundry:<skill> ; Codex : $foundry:<skill> ; gate CI en code).
  `gh pr create` / `gh pr merge` et le push direct sur la branche par défaut sont
  bloqués par hook.
- Avant tout choix d'architecture : scanne l'index des ADR (`python3 "{cli}" query adrs`)
  puis charge le texte de ceux qui touchent le sujet (`python3 "{cli}" query adr <ID>`) ;
  traite les `accepted` comme acquis — on ne re-litige pas, on supersède explicitement.
- Pour choisir le travail : foundry:next-issue. Pour démarrer : foundry:start-issue
  (arbre propre requis). Pour reprendre un chantier : foundry:resume-issue.
- Une idée en cours de route passe par foundry:intake, jamais directement au code."""


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    try:
        from foundry import registry
        cwd = payload.get("cwd") or os.getcwd()
        found = registry.entry_for(cwd, use_env=False)
        if not found:
            return  # unregistered repo: stay silent
        tracker, _repo, entry = found
        root = (os.environ.get("PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT")
                or _PLUGIN_ROOT)
        cli = os.path.join(root, "tooling", "foundry_cli.py")
        ctx = _CONTRACT.format(key=entry.get("key", "?"), tracker=tracker, cli=cli)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": ctx,
        }}, ensure_ascii=False))
    except ValueError as exc:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"Foundry : binding tracker invalide — {exc}",
        }}, ensure_ascii=False))
    except Exception as exc:
        # never break a session start, but say so (PAT-265, exception type name only)
        print(python_floor.hook_failure_output("session_start", exc))


if __name__ == "__main__":
    main()
