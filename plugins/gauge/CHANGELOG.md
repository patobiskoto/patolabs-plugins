# Changelog

## Unreleased

## 0.1.0 — 2026-10-18

- First brick (PAT-246): `gauge_cli.py status [--json]` reads four quota counters, read
  only, stdlib only: Codex (`codex app-server`, `account/rateLimits/read`), Claude (last
  `rate_limit_event` of a stream file, or a statusLine JSON file), Linear (`x-ratelimit-*`
  headers) and GitHub Actions (`gh api /users/<login>/settings/billing/usage`).
- Every value carries its source and read time. Unreadable data is `unknown` with a
  reason, never zero.
- `--probe-claude --probe-budget-usd <amount>` is the only paid read; it is opt-in and
  refused without a positive amount.
- Skill `gauge:status`. Offline tests on anonymised recorded responses.
