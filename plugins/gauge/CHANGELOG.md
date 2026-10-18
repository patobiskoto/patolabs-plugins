# Changelog

## Unreleased

## 0.2.1 — 2026-10-18

Deferred review remarks of PAT-246 and PAT-247 (PAT-251). No new CLI verb, option,
configuration key or hook.

- `check` judges GitHub Actions on the current UTC month only; a Claude window whose reset
  time has passed is `inconnu` (stale); a configuration file named by `--config` or
  `GAUGE_CONFIG` that is absent is reported; `check` exits 0 on any internal error, printing
  `gauge: inconnu (internal error)`.
- Linear: only a 2xx or 429 answer is read; any other HTTP status is `unknown`
  (`request refused (HTTP n)`).
- Codex: a present but unreadable window is `inconnu`, an absent one is not. NaN and
  infinity are never a reading. GitHub: gross and net amounts now sum the `Minutes` items
  only, like the minutes. The probe passes the normalised budget to `--max-budget-usd`.
- Before it exits, the session-start hook kills any `gh` or `codex app-server` child registered
  and still running when it calls `kill_live`.
- README: install commands, units of reset times, Codex manifest `Write` capability,
  GitHub minutes definition; CI: the `gauge` job is covered by the fork-guard tests.

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
