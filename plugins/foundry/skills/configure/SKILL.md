---
name: configure
description: >
  Configures Foundry for Claude Code, Codex, CI, or local development: writes
  non-secret YouTrack/provider settings, stores the YouTrack token in the macOS
  keychain, and verifies the resolved configuration. USE WHEN installing Foundry,
  migrating hosts, rotating credentials, or when configuration is missing.
allowed-tools: Bash(python3:*)
---

# configure — one setup for Claude Code and Codex

## Runtime path
Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<foundry-root>` from this skill's absolute path by removing
`/skills/configure/SKILL.md`. Never pass the placeholder literally.

## 1. Write non-secret settings
Ask for the YouTrack URL and confirm the providers, then run:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" configure set --url "<youtrack-url>" --tracker youtrack --codehost github
```

This writes only non-secret values to `~/.config/foundry/config.env` (or
`$FOUNDRY_CONFIG`) and records the installed CLI path for sibling plugins.

## 2. Store the token without exposing it to the chat
Do not ask the user to paste a permanent token into the conversation and never put it
in a command argument or scratch file. Ask them to run this exact command in their own
interactive terminal:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" configure token
```

On macOS it prompts invisibly and stores the token in Keychain. On other systems,
Foundry deliberately refuses plaintext storage; the user must expose
`YOUTRACK_TOKEN` through their environment or secret manager.

Provider credentials use the same protected path, for example:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" configure credential --name DEVHUB_TRACKER_TOKEN
```

For Linear, select `--tracker linear`, then store `LINEAR_API_TOKEN` with the same
interactive `configure credential` command. Register the explicit repository/team/
project/state IDs described in `docs/linear-tracker.md`; configuration never discovers
them by name. That document is also authoritative for the bounded write subset: existing
issue replacement and AC synchronization remain unavailable without provider CAS. Live
cutover is not part of configuration and remains FOUNDRY-159.

## 3. Verify
After the user confirms the token step:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" configure show
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" doctor
```

Never print the token: `configure show` reports only `configured` or `missing`.

## Recovery: a credential was reported stored but resolves as `missing`

First repeat the interactive `configure token` or `configure credential --name …` command,
then run `configure show`. Foundry only reports state, never a secret value. The repaired
writer replaces an existing item atomically, including an empty item left by an older
non-interactive build.

New entries grant password access only to the stable macOS system tool
`/usr/bin/security`. Foundry invokes that fixed reader without putting the credential
in its arguments, so changing or upgrading the Python executable used by Claude Code
or Codex does not require a new Keychain approval. Existing entries created by
`security` keep their established compatible ACL when they are replaced.

Only if that replacement fails, remove the affected item in **Keychain Access** (service
`patolabs.foundry`, account equal to the credential name), then repeat the interactive
command and verify with `configure show`.

For manual removal in a terminal, the equivalent command is
`security delete-generic-password -s patolabs.foundry -a DEVHUB_TRACKER_TOKEN`.
It keeps the secret out of the command line; use it only after checking the service and
account match the affected Foundry credential.

Deletion continues to remove that one matching generic-password item. It does not create
a plaintext fallback; a failed re-entry remains `missing` until a secret manager or a
new interactive Keychain entry supplies the value.
