# gauge

Read-only quota gauge for Claude Code and Codex. It shows four counters, each with its
source and read time. Anything it cannot read is `unknown` with a reason, never zero.
It never prints or stores a token, login, account identifier, credits balance or
repository name.

```
python3 plugins/gauge/tooling/gauge_cli.py status [--json]
```

Skill: `/gauge:status` (Claude Code), `$gauge:status` (Codex). Stdlib-only Python.

## Threshold alert (0.2.0, PAT-247)

```
python3 plugins/gauge/tooling/gauge_cli.py check [--config PATH]
```

Skill: `/gauge:check`, `$gauge:check`. `check` prints one `gauge: ...` line per counter at
or above its threshold, and one line saying `inconnu` per value it could not read (reason
included) or per expected window missing from a response (`EXPECTED_LABELS` in
`tooling/gauge/check.py`: Codex `primary`/`secondary`, Claude `five_hour`/`seven_day`,
Linear `requests`/`complexity`). A value that cannot be read is never reported as below the threshold. It
prints a single "all read and below" line only when every monitored value was read.
It always exits 0 and never blocks.

### Thresholds and configuration file

One threshold per counter, in a JSON file. Location, first match
(`config_path` in `tooling/gauge/check.py`): the `GAUGE_CONFIG` environment variable, else
`$XDG_CONFIG_HOME/gauge/config.json`, else `~/.config/gauge/config.json`; `--config PATH`
overrides all three for `check`. The hook reads the same location. A missing file means
the defaults; an invalid file means the defaults plus a `gauge config:` line.

| Counter key | Default threshold |
|---|---|
| `codex` | 80 |
| `claude` | 80 |
| `linear` | 80 |
| `github-actions` | none (not monitored) |

`codex`, `claude` and `linear` are percent used (Linear: `(limit - remaining) / limit`);
`github-actions` is minutes used in a month, with no default because the included quota is
not in the response. A threshold of `null` (or none) means the counter is not monitored
and not read. Example:

```json
{"thresholds": {"codex": 70, "linear": null, "github-actions": 1500},
 "claude_statusline_json": "/path/to/statusline.json"}
```

`claude_stream` (list of paths) or `claude_statusline_json` (one path) give the Claude
source; without them the Claude counter is `inconnu` (see below), so set its threshold to
`null` if you do not provide a file. Defaults are `DEFAULT_THRESHOLDS` in
`tooling/gauge/check.py`.

### Session-start alert

`hooks/hooks.json` declares one `SessionStart` hook (`startup|resume`) running
`hooks/session_start.py`. It always exits 0, prints nothing when all monitored counters
are read and below threshold, and otherwise prints a `systemMessage` plus
`additionalContext`. It is bounded: readers run in threads, `HOOK_DEADLINE_S` (6 s) in
total, the Codex reader with `HOOK_CODEX_TIMEOUT_S` (4 s); a reader still running then is
`inconnu`. Note that, within that bound, it starts `codex app-server` (as `status` does)
when `codex` is monitored. The `hooks.json` timeout is 10 s.

Operational constraints. The hook runs at every session start (`startup|resume`) of the
machine where the plugin is enabled, and injects its text as `additionalContext` into the
model's context. When `linear` is monitored it sends one `{ viewer { id } }` request to
Linear at each session start (`read_linear`), which consumes the monitored quota. With the
defaults the Claude counter is `inconnu` at each start, a permanent warning, until
`claude_stream` or `claude_statusline_json` is set or its threshold is `null`. On an
unexpected internal error the hook prints `gauge: inconnu (hook error)` and exits 0.

Hosts. Claude Code: runs `hooks/hooks.json` of an enabled plugin (same mechanism as
Foundry, `plugins/foundry/hooks/hooks.json`); not run end to end here. Codex: Foundry's
README reports its session-start hook running on Codex 0.155.1 (observed, from the shared
`hooks/hooks.json`, which the Codex manifest does not declare), and `codex features list`
on 0.162.1 shows `hooks` stable. That Codex runs this plugin's hook, passes
`${CLAUDE_PLUGIN_ROOT}` and shows the `systemMessage` to the operator is not verified
here: unknown. Fallback that does not depend on it: run `gauge check` (skill
`$gauge:check`) before a campaign.

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

Offline: subprocess and HTTP are replaced by fakes (one test runs `hooks/session_start.py` with an empty `PATH`); fixtures in `tests/fixtures` are
anonymised recorded shapes (placeholders for account, login, token, repository).

## Documentation status (R5, FOUNDRY-ADR-0018)

- CLI `status` and its options (`--json`, `--claude-stream`, `--claude-statusline-json`,
  `--probe-claude`, `--probe-budget-usd`): documented here.
- Environment key `LINEAR_API_TOKEN`: documented here.
- Operational constraint: the Codex, Claude and `gh` readers use the local authenticated
  session of the machine running them; unverified on a remote or CI runner.
- Skill `gauge:status`: described in `skills/status/SKILL.md`. Skill `gauge:check`:
  `skills/check/SKILL.md`.
- 0.2.0: CLI verb `check` and option `--config`, environment key `GAUGE_CONFIG`,
  configuration keys `thresholds`, `claude_stream`, `claude_statusline_json` and the
  defaults table, `SessionStart` hook and its bounds, the Codex hook coverage left
  unknown: documented in "Threshold alert" above. Foundry docs: not necessary, no Foundry
  constant, CLI or hook changed.
- `--json` output shape (presented by the skill as machine output): documented here. A
  list of four objects `counter`, `status` (`ok` or `unknown`), `source`, `read_at`,
  `reason`, `entries` (`Reading.as_dict` in `tooling/gauge/readers.py`). A quantity that
  could not be read is `null` in an entry, never `0`; for GitHub Actions a month field
  with no numeric value in the response is `null`. Entry keys per counter are those
  built in `readers.py`; they are not a frozen schema (version 0.2.0).
- Foundry docs: `plugins/foundry/README.md` (CI jobs list) updated for the fifth job
  `gauge`; no Foundry constant or CLI surface changed.
