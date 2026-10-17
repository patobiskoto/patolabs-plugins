# gauge

Read-only quota gauge for Claude Code and Codex. It shows four counters, each with its
source and read time. Anything it cannot read is `unknown` with a reason, never zero.
It never prints or stores a token, login, account identifier, credits balance or
repository name.

```
python3 plugins/gauge/tooling/gauge_cli.py status [--json]
```

Skill: `/gauge:status` (Claude Code), `$gauge:status` (Codex). Stdlib-only Python.

## Counters and sources

| Counter | Source | Unknown when |
|---|---|---|
| Codex | `codex app-server --listen stdio://`, request `account/rateLimits/read` (JSON-RPC over JSONL; no model call). Reads `primary` and `secondary` `usedPercent`, `windowDurationMins`, `resetsAt`. The process group is killed after the read, with a 15 s bound (`CODEX_TIMEOUT_S`). | `codex` absent, error, timeout, no window |
| Claude | Last `rate_limit_event` of a stream file you give with `--claude-stream <path>` (repeatable), or the statusLine JSON `rate_limits.five_hour\|seven_day.used_percentage/resets_at` with `--claude-statusline-json <path>`. | no file given (default), no event, no field |
| Linear | One `{ viewer { id } }` GraphQL request to `https://api.linear.app/graphql`; reads `x-ratelimit-requests-*` and `x-ratelimit-complexity-*` headers. Token from `LINEAR_API_TOKEN` only. | token absent, request failed, no header |
| GitHub Actions | `gh api /users/<login>/settings/billing/usage` (needs the `user` scope); sums `quantity` of `product == "actions"` items with unit `Minutes`, plus gross and net amounts, per month. | `gh` absent, scope or right missing (403/404/410), no item |

The included free quota of GitHub Actions is not in the response: it is always `unknown`.

## Claude: no paid call by default

Claude Code transcripts do not store the `rate_limit_event`; `claude -p --output-format
stream-json --verbose` emits it (observed in Claude Code 2.1.302, schema undocumented,
`resetsAt` assumed to be Unix seconds). By default gauge reads only files you point to,
for example a stream saved by a campaign. `--probe-claude --probe-budget-usd <amount>`
runs one minimal `claude -p ... --max-budget-usd <amount>` call: it is paid, opt-in,
refused without a positive amount, and never run by the tests. The statusLine alternative
is documented at https://code.claude.com/docs/en/statusline.md (read 2026-10-17, not
re-verified); gauge reads a file your own statusline script may write.

## Tests

```
python3 -m unittest discover -s plugins/gauge/tests -p 'test_*.py'
```

Offline: subprocess and HTTP are replaced by fakes; fixtures in `tests/fixtures` are
anonymised recorded shapes (placeholders for account, login, token, repository).

## Documentation status (R5, FOUNDRY-ADR-0018)

- CLI `status` and its options (`--json`, `--claude-stream`, `--claude-statusline-json`,
  `--probe-claude`, `--probe-budget-usd`): documented here.
- Environment key `LINEAR_API_TOKEN`: documented here.
- Operational constraint: the Codex, Claude and `gh` readers use the local authenticated
  session of the machine running them; unverified on a remote or CI runner.
- Skill `gauge:status`: described in `skills/status/SKILL.md`.
- `--json` output shape (presented by the skill as machine output): documented here. A
  list of four objects `counter`, `status` (`ok` or `unknown`), `source`, `read_at`,
  `reason`, `entries` (`Reading.as_dict` in `tooling/gauge/readers.py`). A quantity that
  could not be read is `null` in an entry, never `0`; for GitHub Actions a month field
  with no numeric value in the response is `null`. Entry keys per counter are those
  built in `readers.py`; they are not a frozen schema (version 0.1.0).
- Foundry docs: `plugins/foundry/README.md` (CI jobs list) updated for the fifth job
  `gauge`; no Foundry constant or CLI surface changed.
