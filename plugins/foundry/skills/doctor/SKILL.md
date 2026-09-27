---
name: doctor
description: >
  Health-checks the Foundry wiring (read-only): config + secrets resolve, the tracker
  authenticates, registered projects are reachable, the code-host resolves, the
  current repo maps to a project, and model-aware routing resolves for Claude and
  Codex. USE WHEN the user invokes /foundry:doctor (Claude
  Code), $foundry:doctor (Codex), "check setup", "diagnostique", or something in the
  pipeline misbehaves.
allowed-tools: Bash(python3:*)
---

# doctor

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<foundry-root>` from this skill's absolute path by removing
`/skills/doctor/SKILL.md`. Never pass the placeholder literally.

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" doctor
```
Reports each point green/red: model-routing JSON validity; the effective Claude and
Codex role matrix (including reviewer/architect floors), known override names only,
availability/fallback failures, config (tracker/codehost/URL), tracker auth + registered
projects with issue counts, code-host repo resolution, and current-repo → project.
It also reports local scout as disabled/configured/available/unavailable/invalid policy
without completing a model, starting a daemon, or revealing an endpoint, model or secret.
The CLI's availability distinction is a bounded loopback TCP connect only—never HTTP.
The doctor is read-only. Hooks remain fail-open operational helpers, not a security
boundary. To inspect one issue's escalation/stop state without creating local state:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" doctor --issue FOUNDRY-42
```

Claude overrides are observed automatically from its host environment. In Codex, when
the effective profile reports a `model` or `model_reasoning_effort` override, pass only
the corresponding presence flag—never its value—to keep the diagnostic redacted:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" doctor --profile-model-active --profile-effort-active
```

- Red on config → run `foundry:configure`, set Claude install options, or provide
  environment variables.
- Red after a canonical remote rename → suspend and reconcile the approved binding and
  marker through the recovery procedure. `registry alias` copies the old canonical
  identity and cannot authorize a different remote. For a legacy basename-only binding,
  an additional local alias can preserve project metadata without provisioning:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" registry alias youtrack <old-repo> <new-repo>
```

- Red for a new checkout targeting an existing provider project → use
  `registry bootstrap <tracker> <repo> <KEY> <project-id> [k=v …]`;
  it verifies provider coordinates before publishing a canonical binding and marker.
- Administrative project creation remains the historical optional setup path:
  `setup_project "<Name>" <KEY> <repo>`, only on a proven unbound checkout and a provider
  supporting provisioning. `registry register` is a legacy registry API, not the V1
  bootstrap or marker-recovery path.
