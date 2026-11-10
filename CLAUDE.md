# Process contract & documentation doctrine

Versioned process contract of the `claude-plugins` monorepo (FOUNDRY-ADR-0018). Claude Code
reads `CLAUDE.md`, Codex reads `AGENTS.md` (FOUNDRY-ADR-0005): the two files are
byte-identical, enforced by `plugins/foundry/tests/test_process_contract.py`. Edit
`AGENTS.md`, copy it verbatim into `CLAUDE.md`. Accepted ADRs are settled: this contract
points at them and does not restate them; load the full text before touching an area an
ADR governs:

```
python3 plugins/foundry/tooling/foundry_cli.py query adrs
python3 plugins/foundry/tooling/foundry_cli.py query adr <ADR-ID>
```

Rules are numbered (`R1`, `R2`, …) so a review finding can cite one (`AGENTS.md#R<n>`).
Since PLP-362 the long rules are summarised here; the full text is in
`plugins/foundry/docs/process-contract-r1-r6.md` (header, R1, R5, R6),
`process-contract-r7.md` and `process-contract-r9.md` (same folder). The full text is the
detail of the rule; a summary below never overrides it.

## R1 — PR and merge only through the Foundry skills

Every PR opens through `foundry:open-pr` and merges through `foundry:merge-pr` — never
`gh pr create`, `gh pr merge`, or a direct push to the default branch. Where the Foundry
plugin is active, the `PreToolUse` Bash guard (`plugins/foundry/hooks/guard_bash.py::deny_reason`)
and the `plugins/foundry/.githooks/` pre-push hook deny them (FOUNDRY-ADR-0001). Detail:
`plugins/foundry/docs/process-contract-r1-r6.md`, section R1.

## R2 — Scan the ADR index before an architecture choice

Before deciding anything that shapes architecture, scan the ADR index and load the full
text of any ADR that touches the subject:

```
python3 plugins/foundry/tooling/foundry_cli.py query adrs
python3 plugins/foundry/tooling/foundry_cli.py query adr <ADR-ID>
```

Treat every `accepted` ADR as settled: do not re-litigate it. Supersede a decision only
through an explicit new ADR, never through silent drift in code or in this file. This is
FOUNDRY-ADR-0001's retrieval-before-reasoning mechanism.

## R3 — An idea in flight goes through intake, not straight to code

A new idea that surfaces mid-task (scope not already covered by the current issue's
acceptance criteria) goes through `foundry:intake` — never straight into an
implementation diff. `foundry:intake` decides whether it becomes an issue, a refinement,
an ADR, or is rejected against an existing ADR.

## R4 — CI green means proven green, on two sources

A reviewer or a merge decision may cite "CI is green" only when it is proven green per
FOUNDRY-ADR-0002: the merge gate reads both check-runs and the legacy commit-status API,
requires at least one real `success`, and refuses zero checks or an all-`skipped` run.
The only override is an explicit, audited `--allow-no-ci`, and only when both sources are
empty and nothing signals active CI for the commit. The gate pins the merge to the exact
sha it verified. This is enforced in `plugins/foundry/tooling/foundry/write.py`, not in
prose.

## R5 — Documentation doctrine (FOUNDRY-ADR-0018)

Documentation is judged by an explicit status per touched documented artifact (CLI verbs,
configuration keys, public constants, operational constraints, symbols already cited in
`docs/`, surfaces a skill describes), not by a diff under `docs/`. State it in the PR or
issue with a pointer to the page updated. An acceptable "not necessary" gives a reason tied
to the specific artifact, never a generic one. The mechanical detector and merge gate are
not shipped (FOUNDRY-123): the status is checked by the reviewer, not machine-enforced.
Detail: `plugins/foundry/docs/process-contract-r1-r6.md`, section R5.

## R6 — The Claude Code OAuth identity is bound to the machine (FOUNDRY-101)

The child process Foundry launches for a Claude session forwards only a fixed allow-list of
environment variables (`plugins/foundry/tooling/foundry/command_runtime.py::_CLAUDE_CHILD_ENV_ALLOWLIST`,
FOUNDRY-101). Any scheduler or runner that drives a Claude Code host session, including the
cadencer of FOUNDRY-ADR-0017, must run on the same authenticated developer machine, with its
real user/session identity intact. Detail: `plugins/foundry/docs/process-contract-r1-r6.md`,
section R6.

## R7 — Claude model resolution at CLI boundaries (current state)

Effort levels for a Claude route are not a hardcoded tuple (`EFFORTS` derives from
`DEFAULT_EFFORT_SCOPES["claude"]["default"].levels`, FOUNDRY-125, which opened the closed
table of FOUNDRY-100). `_CLAUDE_MODEL_DECLARATION` declares the canonical versions;
`claude_invocation_model()` resolves a model through the built-in table or the project's own
`claude_models`, and raises `RoutingConfigError` for any other: resolution fails closed, it
never guesses or substitutes. Pins, aliases, Haiku 4.5 and 5.5, the host minimum version and
the cache lifetime are in `plugins/foundry/docs/process-contract-r7.md` (agent profiles:
`plugins/foundry/docs/model-routing.md`, section "Agent profiles and why each family is kept").

## R8 — Using this contract in review

`foundry:maigret`'s "project conventions" quality criterion is this document. A finding
that cites a convention breach must point at a rule here (`AGENTS.md#R<n>` /
`CLAUDE.md#R<n>`) or at an accepted ADR by ID — never an unwritten preference. A diff
that does not touch any rule of this contract passes this criterion without further
comment.

## R9 — Fewer review rounds: author checklist, grouped deferral, minimal corrections (PAT-136, PAT-139)

Applies to the implementer, every correction commit and the coordinator when it writes
files. It changes nothing about the reviewer's minimum tier (FOUNDRY-ADR-0006), the first
full review, or the full review after any blocking round. Detail:
`plugins/foundry/docs/process-contract-r9.md`; baseline and windows:
`plugins/foundry/docs/review-rounds.md`.

**(a) Checklist before handing work back.** The author checks each point on every
sentence of documentation it adds or changes, and reports for each sentence that falls
under points 1 to 5 its source (constant, `file:line`, or page):

1. A sentence about what a tool launches, loads, calls or writes cites its constant,
   file or page; otherwise it points to that page without summarising it.
2. A cause is stated as `observed`, `deduced` or `unknown`.
3. A rule, threshold or quantity is copied from the code or the source report, then
   searched for across the rest of the repository to correct statements it made false.
4. A comparison names the compared sets and checks that they are equal.
5. A documented procedure has been run once, on a copy.
6. No proper name or user name appears in any file, tests included.

**(b) Non-blocking remarks: grouped deferral to a companion Epic (PAT-ADR-0018).** After a
fully validated round, non-blocking remarks go to a companion Epic (`Nits <ORIGIN-EPIC-ID>`,
or `Nits` with no origin Epic, PAT-ADR-0029), not into the PR. Remarks marked "fix before
merge", on a page frozen after publication, a missing or generic documentation status under
R5, the PR of a companion Epic and a durable refusal of a write are corrected before the
merge. Four writes are pre-authorized, and the review after a validated round is partial
(PAT-ADR-0022). The rule is the detail page, section (b). No gate checks (a) or (b): the reviewer judges them.

**(c) Correction commits.** A correction commit changes as few sentences as possible, and
every new sentence is re-checked against the code before the work is handed back.
