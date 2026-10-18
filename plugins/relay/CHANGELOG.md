# Changelog

## Unreleased

## 0.2.1 — 2026-10-18

Closes the remarks deferred from the reviews of PAT-248 and PAT-249 (PAT-257). Behaviour
changes, each with a test:

- `load`: an unreadable state directory (`EACCES`) or a very deeply nested stored state is
  said as unreadable, exit 0 (`show`: exit 1), instead of a traceback. A git common directory
  that git does not give is no longer reported as "no resume state" or "not inside a git
  repository" (`NO_LOCATION`; `show`: exit 2).
- `load`: a check that was non-ok at writing and is `ok` now is printed `[recovered]` and
  counted in a new `R recovered` field of the summary line; the line format gains that field.
- `load --budget`: `nan`, `inf` and negative values are refused (exit 2).
- `write`: a failing store (`OSError`, lone surrogate): exit 2 with the exception type only;
  a deeply nested draft: exit 2 saying it is nested too deeply; never a traceback.
- Verification (`write` and `load`): a command killed by the budget now reports
  "verification time budget exhausted" (`run`, `verify.py`); ticket identifiers are matched
  with `fullmatch` (a trailing newline is refused).
- Session-start hook: a closed or broken output ends silently, exit 0.
- `load`: a branch or commit divergence recorded at writing (wrong declaration in the draft)
  stays a recorded line, never `[recovered]`.
- Tests: the hook manifest asserts `HOOK_DEADLINE_S + JOIN_MARGIN_S < timeout`; the
  hanging-command, unfinished-thread and recorded-non-ok-absent-now paths are tested; the
  known false negatives and false positives of the secret filter are fixed by tests.
- Documentation: README wording of the summary line, of the budget (the two `git` calls
  that locate the state are bounded by `TIMEOUT` only), of the budget details, and the
  "Not covered" list of the secret filter; manifest and catalogue descriptions mention the
  reload.

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
