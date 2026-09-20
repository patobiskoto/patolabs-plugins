#!/usr/bin/env python3
"""SessionStart hook — inject the Foundry contract into every session of a
REGISTERED repo, so the process is active without anyone invoking a skill.

This is the mechanism behind ADR-0001's "contracts declared per consumer
project": instead of hand-editing an AGENTS.md in each repo, the plugin
injects the contract itself. Registry read only — NO network call at session
start, and any failure exits silently (a hook must never break a session).

The launcher path is INLINED (not "$CLAUDE_PLUGIN_ROOT"): that variable is
substituted in hook/skill command strings but is not exported to the agent's
Bash sessions, so a literal reference would hand out a broken command.
"""
import json
import os
import sys

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PLUGIN_ROOT, "tooling"))

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
    except Exception:
        return  # never break a session start


if __name__ == "__main__":
    main()
