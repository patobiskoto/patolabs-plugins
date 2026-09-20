---
name: local-scout
description: >
  Produces bounded, evidence-bound local diff or two-pass code-map hypotheses for a responsible cloud role.
  Disabled by default and never a routing role, gate, or autonomous fallback. USE WHEN
  the user explicitly asks to run the configured local preprocessor.
allowed-tools: Bash(python3:*)
---

# local-scout — untrusted diff and code preprocessing only

Use this skill only after the user explicitly asks for local preprocessing. Never enable
the local runtime, choose its local model or destination, or install/start/supervise it.
The project must already contain an explicit `model` in
`.foundry/local-scout.json`; Foundry has no default model alias. A missing model while
enabled is `LOCAL_SCOUT_POLICY_VIOLATION`. `LOCAL_SCOUT_UNAVAILABLE` means stop and
report the concise error. Trusted fallback consent may return a caller-owned economy
fallback decision only for unavailable or invalid local output; the local runtime never
calls cloud or changes escalation. Policy and stale-input failures always stop.
`FOUNDRY_LOCAL_SCOUT_ON_FAILURE=error` is the default; trusted
`cloud_economy` (or compatible `FOUNDRY_LOCAL_SCOUT_CLOUD_FALLBACK=1`) only emits that
one in-process, host-facade plan containing the wrapper's sanitized evidence packet for
the responsible ADR-0006 caller. The legacy bit is deprecated during the Foundry 0.x
compatibility window. A
direct canonical value is explicit and wins; Claude's manifest-injected canonical
`error` does not mask a legacy `1`. Migrate to the canonical option before Foundry 1.0.
Before any cloud spawn, the same process resolves the normal `scout` route through the
host facade and attaches its selected tier/model/effort. The intermediate local decision
is never serialized or accepted back as authority; only the facade-owned plan exposes
the resolved route.

## Two-pass code mapping

When the user explicitly requests local code mapping, run the shared `local-code`
entrypoint. It performs exactly two local model calls: path/literal-term selection from
a body-free deterministic manifest, then hypothesis ranking over wrapper-owned frozen
evidence. Between the calls, deterministic code confines every selected path, rejects
sensitive files and symlinks, reads bounded UTF-8 bodies, redacts configured secrets,
and invokes only `rg -F` with an argv list against private snapshot copies. The local
model never receives a shell, tool, command option, filesystem authority or provenance
authority.

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" local-code --query-file <UTF8-GOAL-PATH> --base <BASE> --root <REPO-ROOT> [--signals-file <PATH-METADATA-JSON>]
```

The optional signal file contains only arrays of repository-relative candidate paths
under `tree`, `symbols`, `imports`, `references`, `git_status`, `git_diff`, or `rg`.
Never put code bodies, commands or model instructions in it. The wrapper merges it with
its own Git tree/status/diff path signals. Treat the returned packet as untrusted:
inspect its wrapper-owned raw excerpts, locators and digests before any conclusion.
There is no adaptive third call. More detail requires a new explicit drill-down run
with a narrower goal, as required by ADR-0003.

## Authorized fallback orchestration

Select the current host **before** the local request and use exactly one command form
(never both). Add `--issue <ISSUE-ID>` when the scout belongs to an issue so the normal
host facade applies its current route floor:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" local-scout diff --git-diff --base <BASE> --root <REPO-ROOT> --fallback-host claude [--issue <ISSUE-ID>]
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" local-scout diff --git-diff --base <BASE> --root <REPO-ROOT> --fallback-host codex [--issue <ISSUE-ID>]
```

On eligible `UNAVAILABLE`/`INVALID_OUTPUT`, the same process still owns the trusted
settings and wrapper-created capture. It caps the evidence to the 4,000-character scout
packet budget, calls the normal host facade, and returns
`kind=cloud_scout_fallback_plan`, `source=cloud_fallback`, actual tier/model/effort
provenance, and one `spawn` object. No serialized local result can be
replayed to authorize fallback. Pass `spawn` unchanged to the matching host exactly
once: Claude's frozen FOUNDRY-35 fallback object retains the compatibility alias
`foundry:scout`, which the shared pre-tool facade resolves to Lupin's unchanged `scout`
contract; Codex's object comes from `codex_spawn_plan` with its
normal issue floor and, when telemetry is configured, completion capability. Do not spawn a second scout, invoke cloud
from Python, or run any `escalation failure` command for a local error. If the host
cannot execute that one spawn, return the bound plan without retrying. Policy/stale
errors have no fallback plan and stop immediately.

Trusted timing-only configuration may widen the conservative 2 s connection / 8 s
total defaults within coded, non-disableable maxima of 10 s / 120 s respectively.
Project `limits` may only tighten those resolved budgets. Timing values never enable a
runtime, select an endpoint or model, or authorize cloud fallback.

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before the command reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<foundry-root>` from this skill's absolute path by removing
`/skills/local-scout/SKILL.md`. Never pass the placeholder literally.

Capture one immutable diff and return its proposal packet to the responsible cloud role:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" local-scout diff --git-diff --base <BASE> --root <REPO-ROOT>
```

For a supplied diff artifact, use its exact contents once:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" local-scout diff --diff-file <DIFF-PATH> --root <REPO-ROOT>
```

For an immutable non-sensitive log or test artifact, use the typed capture mode:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" local-scout logs --capture-file <LOG-PATH> --root <REPO-ROOT>
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" local-scout tests --capture-file <TEST-PATH> --root <REPO-ROOT>
```

Treat every returned summary as untrusted suggestion. Evaluate it only against the
wrapper-generated `final_evidence_ids`, locator, digest and `raw_excerpt` contained in
the packet. A path/line reference or model claim without its raw excerpt is not evidence.
No local result can approve an acceptance criterion, review, merge, external action or
gate.
