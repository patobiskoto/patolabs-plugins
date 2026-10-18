# gauge

Read-only quota gauge for Claude Code and Codex. It shows four counters, each with its
source and read time. Anything it cannot read is `unknown` with a reason, never zero.
It never prints or stores a token, login, account identifier, credits balance or
repository name.

```
python3 plugins/gauge/tooling/gauge_cli.py status [--json]
```

Skill: `/gauge:status` (Claude Code), `$gauge:status` (Codex). Stdlib-only Python.

## Install

After adding the marketplace (see the repository README):

```
/plugin install gauge@patolabs          # Claude Code
codex plugin add gauge@patolabs         # Codex
```

## Threshold alert (0.2.0, PAT-247; adjusted in 0.2.1, PAT-251)

```
python3 plugins/gauge/tooling/gauge_cli.py check [--config PATH]
```

Skill: `/gauge:check`, `$gauge:check`. `check` prints one `gauge: ...` line per window (or per month, for GitHub Actions) at
or above its threshold, and one line saying `inconnu` per value it could not read (reason
included) or per expected window missing from a response (`EXPECTED_LABELS` in
`tooling/gauge/check.py`: Codex `primary` (an account may have no `secondary` window: observed on 2026-10-18,
the response carried only the weekly `primary`), Claude `five_hour`/`seven_day`,
Linear `requests`/`complexity`). A value that cannot be read is never reported as below the threshold. It
prints a single "all read and below" line only when every monitored value was read.
It always exits 0 and never blocks. On an unexpected internal error it prints
`gauge: inconnu (internal error)` (`main` in `tooling/gauge_cli.py`).

### Thresholds and configuration file

One threshold per counter, in a JSON file. Location, first match
(`config_path` in `tooling/gauge/check.py`): the `GAUGE_CONFIG` environment variable, else
`$XDG_CONFIG_HOME/gauge/config.json`, else `~/.config/gauge/config.json`; `--config PATH`
overrides all three for `check`. The hook reads the same location (not `--config`). A missing
file at the default location means the defaults, silently; a missing file named by `--config` or
`GAUGE_CONFIG`, or an invalid file, means the defaults plus a `gauge config:` line
(`load_config` in `tooling/gauge/check.py`), and an unknown or
mistyped key (`CONFIG_KEYS` in `tooling/gauge/check.py`) is ignored with such a line.

| Counter key | Default threshold |
|---|---|
| `codex` | 80 |
| `claude` | 80 |
| `linear` | 80 |
| `github-actions` | none (not monitored) |

`codex`, `claude` and `linear` are percent used (Linear: `(limit - remaining) / limit`);
`github-actions` is minutes used in the current UTC month (`evaluate` in
`tooling/gauge/check.py`: earlier months in the response are not judged, and a current month
with no `Minutes` item or no item at all is `inconnu`), with no default because the included
quota is not in the response. A threshold of `null` means the counter is not monitored
and not read. Example:

```json
{"thresholds": {"codex": 70, "linear": null, "github-actions": 1500},
 "claude_statusline_json": "/path/to/statusline.json"}
```

`claude_stream` (list of paths) or `claude_statusline_json` (one path) give the Claude
source; without them the Claude counter is `inconnu` (see below), so set its threshold to
`null` if you do not provide a file. Defaults are `DEFAULT_THRESHOLDS` in
`tooling/gauge/check.py`.

A Claude window whose `resets_at` is already in the past is `inconnu` ("stale"): the
operator-provided file was written before the reset, so its value no longer describes the
current window (`_already_reset` in `tooling/gauge/check.py`). The file age itself is not
judged, only the reset time it carries; a window without `resets_at` is judged as read. An old
value inside a window that has not yet reset is still judged as read: below the threshold it
stays silent, although the true use may be higher (a lower bound only).

### Session-start alert

`hooks/hooks.json` declares one `SessionStart` hook (`startup|resume`) running
`hooks/session_start.py`. It always exits 0, prints nothing when all monitored counters
are read and below threshold, and otherwise prints a `systemMessage` plus
`additionalContext`. It is bounded: readers run in threads, `HOOK_DEADLINE_S` (6 s) in
total, the Codex reader with `HOOK_CODEX_TIMEOUT_S` (4 s); a reader still running then is
`inconnu`. Note that, within that bound, it starts `codex app-server` (as `status` does)
when `codex` is monitored. The `hooks.json` timeout is 10 s. Before it exits the hook calls
`kill_live` (`tooling/gauge/readers.py`), which kills the process group of the `gh` and
`codex app-server` children registered by the readers and still running when it is called;
a child that a reader starts or registers after that call (for example the second `gh` of
`read_github_actions`) is not killed (deduced from the code).

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
| Linear | One `{ viewer { id } }` GraphQL request to `https://api.linear.app/graphql`; reads `x-ratelimit-requests-*` and `x-ratelimit-complexity-*` headers. Token from `LINEAR_API_TOKEN` only. | token absent, request failed, request refused (HTTP n), no header |
| GitHub Actions | `gh api /users/<login>/settings/billing/usage` (needs the `user` scope); sums `quantity` of `product == "actions"` items with unit `Minutes`, plus gross and net amounts, per month. | `gh` absent, scope or right missing (403/404/410), no item |

The included free quota of GitHub Actions is not in the response: it is always `unknown`.
The minutes, gross and net amounts of a month are the sums of the `quantity`, `grossAmount`
and `netAmount` of the `Minutes` items only (`parse_github_usage`): storage and other units
are left out, all SKUs are added as raw minutes and no per-SKU multiplier is applied. Whether
the included quota of a plan weights the SKUs differently: unknown here.

Reset times (`resets_at`) come from `epoch_to_iso` (`tooling/gauge/readers.py`): a number
above 1e11 is read as milliseconds, any other as seconds. The unit of Linear's
`x-ratelimit-*-reset` headers is not verified here; the recorded shape in
`tests/fixtures/linear_headers.json` has 13 digits (milliseconds), which the rule reads
correctly. Linear counters are read only from a 2xx or a 429 answer; any other HTTP status
(401, 403, 5xx, ...) is `unknown` with the status in the reason (`read_linear`).

## Claude: no paid call by default

Observed on 2026-10-18 with Claude Code 2.1.302: none of the 1596 transcript `.jsonl` files
under `~/.claude/projects` contains the text `"type":"rate_limit_event"` (searched with
`grep -l`), so transcripts are not a source. `claude -p --output-format stream-json
--verbose` emits the event (observed in Claude Code 2.1.302, schema undocumented, `resetsAt`
assumed to be Unix seconds). By default gauge reads only files you point to,
for example a stream saved by a campaign. `--probe-claude --probe-budget-usd <amount>`
runs one minimal `claude -p ... --max-budget-usd <amount>` call: it is paid, opt-in,
refused without a positive amount, and never run by the tests. The statusLine alternative
is documented at https://code.claude.com/docs/en/statusline.md (read 2026-10-17, not
re-verified); gauge reads a file your own statusline script may write.

## Tests

```
python3 -m unittest discover -s plugins/gauge/tests -p 'test_*.py'
```

Offline: subprocess and HTTP are replaced by fakes. Tests that start a real process: three run
`hooks/session_start.py` with an empty `PATH`, one of them with its `kill_live` replaced,
and two start a `sleep` child to check that it is killed (`LiveProcessTest`); fixtures in `tests/fixtures` are
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
  built in `readers.py`; they are not a frozen schema (version 0.2.1). Since 0.2.1 GitHub gross and net amounts
  cover the `Minutes` items only, and a present but unreadable Codex window is an entry with
  `used_percent` null.
- Foundry docs: `plugins/foundry/README.md` (CI jobs list) updated for the fifth job
  `gauge`; no Foundry constant or CLI surface changed.
- 0.2.1 (PAT-251): no new CLI verb, option, configuration key or hook. Behaviour changes
  documented above: current-month rule for GitHub Actions, stale Claude windows, explicit
  configuration file that is absent, Linear read only from 2xx and 429, reset-time units, GitHub
  minutes definition, hook child cleanup, `check` internal-error line, install commands. Foundry docs: `plugins/foundry/README.md`
  (CI jobs list: the fork-guard test now covers five of the eight jobs) and
  `plugins/foundry/changelog.d/PAT-251.md` updated; no Foundry constant, CLI or hook changed.
- Codex manifest capability `Write`: gauge is read-only, but `.codex-plugin/plugin.json`
  declares `Read` and `Write` because `validate_semantic_parity`
  (`scripts/validate_plugins.py`) requires the same capabilities on both hosts and Claude's
  implicit set is `Read`, `Write` (`HOST_SCHEMA_ALLOWLIST`, same file); `ship-ios` declares the
  same. Whether Codex shows `Write` to users: reported by the review of PAT-246, not observed
  here.
- Display of the alert in Claude Code and Codex after installing Gauge: not observed (needs an
  interactive host session); unknown, see "Hosts" above. `GAUGE_VERSION` in
  `tooling/gauge/readers.py` is checked against both manifests by `VersionTest`.
