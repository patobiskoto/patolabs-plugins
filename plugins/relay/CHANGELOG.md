# Changelog

## Unreleased

## 0.1.0 — 2026-10-18

- First brick (PAT-248): `relay_cli.py write --draft FILE|-` validates a JSON resume state
  (fixed keys, malformed input refused), refuses secrets, verifies it against the real
  state and writes it atomically, mode 600, under `<git-common-dir>/relay/`.
  `relay_cli.py show [--json]` prints it. Skills `relay:write` and `relay:show`.
- Verification: branch, HEAD, dirty tree, pointed files, tickets (through Foundry's CLI,
  `unknown` when Foundry is not found) and pull requests (through `gh`, `unknown` when it
  cannot answer).
- Not included: reload at session start (PAT-249).
