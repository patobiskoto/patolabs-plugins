# Changelog

## Unreleased

## 0.2.0 — 2026-10-18

- Threshold alert (PAT-247): `gauge_cli.py check [--config PATH]`, skill `gauge:check`,
  and a `SessionStart` hook (`hooks/hooks.json`) warn when a counter reaches its
  threshold. One threshold per counter in a JSON file (defaults and location in the
  README). Never blocking (exit 0, bounded at 6 s in the hook). An unreadable value is
  reported `inconnu`, never as below the threshold.
- Codex hook coverage is unverified; `gauge check` is the documented fallback.

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
