# Process contract: full text of the header and of rules R1, R5 and R6

Since PLP-362 the repository contract (`AGENTS.md`, identical in `CLAUDE.md`, at the
repository root) keeps a short version of these rules to limit the context loaded at every
session (measurement: `session-context-load.md`). The headings `R1`, `R5` and `R6` stay in the contract, so `AGENTS.md#R<n>`
citations still resolve. This page holds the text that those short versions point to, moved
verbatim from `AGENTS.md` (header, R1, R5, R6); it is the detail of the rule, and the
contract's short version is a summary of it. Paths are relative to the repository root.


## Header of the contract

This file is the versioned process contract for the `claude-plugins` monorepo. It
implements the "referential" half of FOUNDRY-ADR-0018: the contract that used to live
only in host configuration on one machine now lives in the repository, so it is
identical for a fresh clone with no user configuration (no `~/.claude`, no
`~/.config/foundry`), for Claude Code, and for Codex.

**Dual-host, single source.** Per FOUNDRY-ADR-0005, Claude Code reads `CLAUDE.md` and
Codex reads `AGENTS.md`. This repository keeps one implementation of the contract: the
two files are byte-identical, enforced by
`plugins/foundry/tests/test_process_contract.py`. Edit this file (`AGENTS.md`); copy the
result verbatim into `CLAUDE.md`. Never let the two diverge.

**Accepted ADRs are settled.** This contract states operating rules and points at the
ADR that decided them; it does not re-argue or restate any ADR decision. Load the full
text before touching an area an ADR governs:

```
python3 plugins/foundry/tooling/foundry_cli.py query adrs
python3 plugins/foundry/tooling/foundry_cli.py query adr <ADR-ID>
```

Rules below are numbered (`R1`, `R2`, …) so a review finding can cite one precisely,
e.g. "violates AGENTS.md#R1" — this is the concrete anchor the `foundry:maigret`
reviewer contract's "project conventions (AGENTS.md/CLAUDE.md)" criterion checks
against.

## R1 — PR and merge only through the Foundry skills

Every PR opens through `foundry:open-pr` and merges through `foundry:merge-pr`
(`/foundry:<skill>` in Claude Code, `$foundry:<skill>` in Codex) — never `gh pr create`,
`gh pr merge`, or a direct push to the default branch. In a repo where the Foundry
plugin is active, this is not just policy: `gh pr create` / `gh pr merge` and a direct
push to the default branch are denied by the `PreToolUse` Bash guard
(`plugins/foundry/hooks/guard_bash.py::deny_reason`), and a git `pre-push` hook in
`plugins/foundry/.githooks/` gives the same guarantee outside either agent (enable it
with `git config core.hooksPath plugins/foundry/.githooks`). See FOUNDRY-ADR-0001 for
why the tracker must stay authoritative over the merge path. The guard's usual fail-open
on internal errors has one exception (PAT-42): an invalid, drifted, or ambiguous repo
tracker binding (`registry.entry_for()` raising `ValueError`) still denies these same
commands, naming the cause, instead of silently reopening the guard in exactly
the state where every other Foundry command already fails closed. The named source is
the repo binding, or the global registry file and its path when `registry.json` is
malformed (PAT-63: invalid JSON or encoding, non-object root, provider or entry, invalid
`canonical_repo`; `registry.RegistryFileError`); file content is never echoed. A registry
that cannot be read at all (OSError) and any other internal error still fail open, and
"no-op outside registered repos" means a well-formed registry with no entry for the
checkout.
The guard has one denial outside that rule: a command invoking `campaign-approval approve`
is denied in every repository, registered or not, before any registry read (PAT-215;
`guard_bash.py::approval_deny_reason`, `plugins/foundry/docs/campaign-approval.md`).

## R5 — Documentation doctrine (FOUNDRY-ADR-0018)

FOUNDRY-ADR-0018 decided that documentation is judged by explicit status per touched
documented artifact, not by the presence of a diff under `docs/`. The operating rule
this contract adds:

- **What must carry a status**: CLI verbs/options, configuration keys, public constants
  and vocabularies (for example the Claude model translation table in
  `plugins/foundry/tooling/foundry/routing_facades.py`), operational constraints (for
  example the OAuth machine-binding constraint in R6), symbols already cited in
  `docs/`, and surfaces a skill describes to a caller.
- **Where**: the relevant file under `plugins/foundry/docs/` (or this contract, for a
  process/operating rule with no natural docs page), plus a one-line pointer in the PR
  or issue saying what was updated.
- **What counts as an acceptable "not necessary"**: a reason tied to the specific
  candidate artifact — e.g. "internal refactor, no public constant/CLI surface
  changed" — never a generic or empty reason, and never a blanket "not necessary" that
  covers an artifact the reviewer can independently show is real and undocumented.
- **Current enforcement state, honestly**: the deterministic candidate detector and the
  merge-time status gate described by FOUNDRY-ADR-0018 are a separate, not-yet-shipped
  issue (FOUNDRY-123). Until it ships, this status is asserted in the PR description and
  checked by the reviewer against this rule, not machine-enforced. Do not claim
  mechanical enforcement that does not exist yet — that is exactly the false-convention
  failure FOUNDRY-ADR-0018 exists to prevent.

## R6 — The Claude Code OAuth identity is bound to the machine (FOUNDRY-101)

Claude Code's OAuth token refresh depends on a stable local identity: the child process
Foundry launches for a Claude session only forwards a fixed allow-list of environment
variables — `HOME`, `LANG`, `LC_ALL`, `LOGNAME`, `PATH`, `TMPDIR`, `USER`
(`plugins/foundry/tooling/foundry/command_runtime.py::_CLAUDE_CHILD_ENV_ALLOWLIST`,
FOUNDRY-101). Strip or spoof those, and the host's OAuth refresh can fail.

**Operational consequence**: any scheduler or runner that drives a Claude Code host
session — including the deterministic cadencer of FOUNDRY-ADR-0017 — must run on the
same authenticated developer machine, with that machine's real user/session identity
intact. It cannot be relocated to an arbitrary remote or ephemeral CI runner without
first re-establishing that OAuth identity there. FOUNDRY-ADR-0017 names this constraint
explicitly as the reason its cadencer runs on the development machine rather than a
remote server.
