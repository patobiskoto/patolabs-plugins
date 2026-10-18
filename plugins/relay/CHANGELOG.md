# Changelog

## Unreleased

## 0.2.0 — 2026-10-18

- Reload at session start (PAT-249): `relay_cli.py load [--budget SECONDS]` presents the
  last state and re-verifies it now (branch, HEAD, working tree, pointed files, tickets,
  pull requests), listing every change since writing; an absent or unreadable state is said
  as such; always exit 0. `SessionStart` hook (`hooks/hooks.json`, `hooks/session_start.py`)
  bounded to 6 s of re-verification (`HOOK_DEADLINE_S`); a check not done in time is
  `unknown`. The text is scanned for secrets before it is printed. Skill `relay:load`.
  Codex hook execution is not verified (see README); the skill is the fallback.

## 0.1.0 — 2026-10-18

- First brick (PAT-248): `relay_cli.py write --draft FILE|-` validates a JSON resume state
  (fixed keys, malformed input refused), refuses secrets, verifies it against the real
  state and writes it atomically, mode 600, under `<git-common-dir>/relay/`.
  `relay_cli.py show [--json]` prints it. Skills `relay:write` and `relay:show`.
- Verification: branch, HEAD, dirty tree, pointed files, tickets (through Foundry's CLI,
  `unknown` when Foundry is not found) and pull requests (through `gh`, `unknown` when it
  cannot answer).
- Not included: reload at session start (done in 0.2.0, PAT-249).
