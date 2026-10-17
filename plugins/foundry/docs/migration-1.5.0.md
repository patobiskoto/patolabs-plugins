# Foundry 1.5.0 — upgrade and rollback, 2026-10-17

**This guide is written for the release pull request and is frozen from it.** It says
how to upgrade from 1.4.0 and what is known about going back. It states nothing about
a tag, an installation or a version loaded by a host: those states come after the
merge and are recorded on the release ticket (`skills/release/SKILL.md`, step 9),
not here. Both Foundry manifests must report `1.5.0`; Ship-iOS remains independently `0.3.0`.
The catalogues have no version key. See [release-1.5.0.md](release-1.5.0.md) for what
the version contains and for its behaviour changes. A first installation and a first
repository are unchanged: follow [migration-1.0.0.md](migration-1.0.0.md). An upgrade
from 1.3.0 or earlier must also read [migration-1.4.0.md](migration-1.4.0.md) and the
guides it points to: their checks are not repeated here and are not changed by 1.5.0.

The catalogues name no ref, so an official update is expected to deliver 1.5.0 from the
merge of the release pull request (derived, not observed; see "Release phases and
evidence" in the release note). The changes themselves have been on `main` since their
own merges, and whether a manager refreshes a package whose version does not change is
unknown: a host updated before this release may already carry them under the `1.4.0`
label (derived, not observed).

## Before upgrading from 1.4.0

Six checks, for what the release note announces. None of them edits a cache.

1. **You run no Epic campaign.** The campaign verbs `campaign-approval`,
   `campaign-cadence next`, `campaign-cadence step|run` and `routing record-review-remarks`
   are new (release note, item 1; changelog entries of PAT-215, PAT-216, PAT-217 and PAT-141). Checks 2 and 3 concern
   campaigns only; whether any other verb calls them was not searched for this guide:
   unknown.
2. **You run an Epic campaign: a human approval is required first.** `campaign-approval
   approve` asks for a confirmation typed on the terminal, is refused to an agent session
   and is denied by the Claude `PreToolUse` Bash guard in every repository
   ([campaign-approval.md](campaign-approval.md), "Commands" and "An agent cannot approve
   (layers and limit)"; `AGENTS.md#R1`). Without a valid approval, a pass returns
   `refuse` and launches nothing ([campaign-effects.md](campaign-effects.md), "Commands").
   The ceilings and lifetime are bounded by `MAX_SESSIONS_CEILING` and `MAX_TTL`, and the
   lifetime defaults to `DEFAULT_TTL` (`tooling/foundry/campaign_approval.py`). `check` refuses a
   different preview digest, structure digest, an expired or an absent approval
   ([campaign-approval.md](campaign-approval.md), "Commands").
3. **Campaign sessions run in the native Claude Code sandbox (PAT-220).** Which paths are
   writable or denied is in [campaign-effects.md](campaign-effects.md), "The native
   sandbox". Known limits: the live trial of 2026-10-17 observed a set of refusals once
   and did not record the refusing layer; Glob/Grep, reads, `commondir`,
   `config.worktree`, `refs/replace`, the network and three user files are untested
   ([qualification/pat-220-sandbox-trial-2026-10-17.json](qualification/pat-220-sandbox-trial-2026-10-17.json);
   changelog entry of PAT-220). The reviewer session stays outside the sandbox. A hooks
   directory inside the worktrees directory makes a pass `refuse`
   `git_hooks_dir_refused`, and a worktree whose branch is not the ticket's makes it
   `suspend` `worktree_branch_mismatch` before `open-pr`, `review` and `defer-remarks`
   (same page and changelog entry). Campaign worktrees are created at `<repo>-campaign/<ID>`
   next to the repository (`default_worktrees_dir`).
4. **Linear: the `start` Git automation of the team.** On Linear, `issue openpr` now
   accepts a native State that went back to In Progress right after Foundry set In
   Review, under the proof described in [linear-tracker.md](linear-tracker.md) (PAT-230
   paragraph). That the team's `start` automation causes it is deduced, not observed; no
   Linear configuration is changed by this release, and the team's automation rules were
   read once on 2026-10-17 (same paragraph). Any other divergence still fails.
5. **Scripts that read the Epic preview.** On Linear `epic-preview` now succeeds and its
   result gains `structure_digest`; `authority.tracker` already existed in 1.4.0 and now
   carries the tracker name (changelog entry of PAT-178; [epic-preview.md](epic-preview.md)).
6. **Scripts that read `adr link-issue` refusals.** Three more stored ADR bodies are
   accepted (changelog entry of PAT-209); any other body is refused as before.

No project data, routing policy, credential or configuration key needs to change. The
changelog entries name no configuration key. The environment variable
`FOUNDRY_REQUEST_COUNT=stderr` is named by the PAT-229 entry and by
[linear-request-cost.md](linear-request-cost.md); no other environment read was searched
for in the code for this guide.

Retain a private snapshot of the non-secret configuration, the registry, the repository
marker and the routing policy (including its absence) before replacing the package, as
for 1.0.0. Credentials stay in their secret store. The shared Foundry state lives under
`FOUNDRY_DATA` or `~/.config/foundry`, independently of any host plugin cache; the 1.0.0
guide ([migration-1.0.0.md](migration-1.0.0.md)) states that package replacement
preserves the shared data, and does not migrate project data. That has not been
observed for an update from 1.4.0 to 1.5.0.

## New local files

A campaign creates these entries in `registry.data_dir()` (`FOUNDRY_DATA`, else
`~/.config/foundry`), outside any repository. Their names are constants of the code;
the behaviour of each is on the page cited, not summarised here.

| Entry | Constant | Page |
| --- | --- | --- |
| `campaign-approvals.jsonl` | `JOURNAL_NAME`, `campaign_approval.py` | [campaign-approval.md](campaign-approval.md), "Record and storage" |
| `campaign-attempts.jsonl` | `ATTEMPTS_NAME`, `campaign_effects.py` | [campaign-effects.md](campaign-effects.md), "The attempt journal" |
| `campaign-summaries/` | `SUMMARIES_NAME`, `campaign_effects.py` | [campaign-effects.md](campaign-effects.md) (PAT-223 filter, "The native sandbox") |
| `review-remarks/` | `STORE_DIR`, `review_remarks.py` | [campaign-effects.md](campaign-effects.md), "The remarks receipt" |
| `campaign-reviews/` | `REVIEWS_DIR`, `campaign_review.py` | [campaign-effects.md](campaign-effects.md), "The review effect" |

The native sandbox denies the data directory to the implementation and correction sessions (PAT-220). Whether the entries are safe to
delete between campaigns was not established for this guide: unknown. Keep them, as the
1.0.0 guide says for newer receipts and history.

## Official upgrade

These are the sequences of [migration-1.0.0.md](migration-1.0.0.md), unchanged, through
the official manager of each host. This guide records no run of them from 1.4.0 to
1.5.0: such a run cannot exist before the merge, and none of these commands was run in
this release.

Claude Code:

```text
/plugin marketplace update patolabs
/plugin update foundry@patolabs
/reload-plugins
/plugin list
```

The terminal equivalents are `claude plugin marketplace update patolabs`,
`claude plugin update foundry@patolabs -y`, and `claude plugin list --json`. Reload
components or start a new session before checking the loaded plugin: a session already
open keeps what it loaded. Since 1.4.0, `hooks/guard_bash.py` (the
`campaign-approval approve` denial, PAT-215) and `skills/merge-pr/SKILL.md` changed and
no file under `agents/` did (`git diff --stat foundry-v1.4.0..cffe064` on those
directories); reload components to pick them up.

Codex refreshes the Git snapshot separately from the installed package; start a new
task afterwards:

```sh
codex plugin marketplace upgrade patolabs
codex plugin remove foundry@patolabs
codex plugin add foundry@patolabs
codex plugin list --marketplace patolabs --json
```

No cache editing or generated development cachebuster is installation evidence.
Ship-iOS needs no action: its version does not change.

## Configuration and doctor after the upgrade

No new credential or configuration key is required. `/foundry:configure` (Claude Code)
and `$foundry:configure` (Codex) remain the way to change the trusted configuration.
Then check the binding and the routes with the installed CLI, from the intended
checkout (`<foundry-root>` is the actually loaded plugin root, as in the 1.0.0 guide):

```sh
python3 <foundry-root>/tooling/foundry_cli.py configure show
python3 <foundry-root>/tooling/foundry_cli.py registry selection --require-v1
python3 <foundry-root>/tooling/foundry_cli.py doctor
python3 <foundry-root>/tooling/foundry_cli.py routing show --host claude
python3 <foundry-root>/tooling/foundry_cli.py routing show --host codex
```

The equivalent doctor skills are `/foundry:doctor` and `$foundry:doctor`. The routes
are those of 1.4.0 on both hosts: no routing default changes (release note, "What 1.5.0 contains since 1.4.0").
`routing show` and `doctor` bind nothing and observe no host version, as the 1.1.0 guide
says.

These commands were not run for this guide: they need an installed 1.5.0, which does not
exist at the time of writing. The new verbs have no installed-plugin check here either;
their procedures are in [campaign-approval.md](campaign-approval.md),
[campaign-cadence.md](campaign-cadence.md), [campaign-effects.md](campaign-effects.md),
[epic-preview.md](epic-preview.md) and [linear-tracker.md](linear-tracker.md) (`issue
accept`), each described by its page. `campaign-approval status|check` and
`campaign-cadence next` are read-only on the tracker (their pages); `approve`,
`campaign-cadence step|run` and `issue accept` write, so they are not part of a readback.

## Verify the version actually loaded

After reload or a new task, verify and retain for each host:

- The host binary, path and version actually invoked.
- The official manager listing, the marketplace revision, the loaded skill and hook
  root and both manifest versions at that resolved root. They must identify Foundry
  `1.5.0` and the source revision it was resolved from. A manager listing or a source
  checkout alone is insufficient when a running session still loaded an old cache; if
  the loaded ref is unavailable, report it unavailable and leave the check open.
- The installed CLI readbacks above, never this repository's source CLI in their place.

Nothing is "installed" until these readbacks exist. They are made after the merge, the
tag and an official update, and recorded on the release ticket, not here
(`skills/release/SKILL.md`, step 9).

## Rollbacks

### Without leaving 1.5.0

No per-project key or option returns to the 1.4.0 behaviours listed in the release note.
A correction of a 1.5.0 behaviour is an ordinary Foundry pull request delivered by a
later version; never a hand-edited cache. Not running a campaign is always possible: no
verb of 1.5.0 is required by the verbs of 1.4.0 (deduced from the changelog entries, not
tested).

### Return to 1.4.0

**Not tried.** No command for it has been run, and none is established by this guide.
That includes the rollback to an earlier plugin version, which stays an untried
procedure tracked by PAT-162 (still open): a rollback through an official manager is not
qualified.

What is known:

- The tag `foundry-v1.4.0` exists in this repository (`git tag`, read when this guide
  was written); `git ls-remote --tags origin 'foundry-v1.4.0^{}'` lists commit `b2e2c0c`,
  the squash commit of PR #158.
- No manager command that installs a named tag of this marketplace is established in
  this repository; whether either manager accepts a tag was not exercised
  ([migration-1.3.0.md](migration-1.3.0.md), "Return to 1.2.0").
- Under a restored 1.4.0, `campaign-approval`, `campaign-cadence next|step|run`, `issue
  accept` and `routing record-review-remarks` do not exist (deduced from the changelog
  entries, not tested as a downgrade).
- Records written under 1.5.0 are not removed by a package change: the entries of "New
  local files", an `acceptance-decision` lifecycle receipt written by `issue accept`
  (PAT-206), and a Linear issue set to Done by it. How a restored 1.4.0 reads them is
  unknown. Epic-closure behaviour on an issue accepted this way is stated for 1.5.0 in the
  changelog entry of PAT-206, not for 1.4.0.

What is unknown:

- Whether an official manager of either host can install 1.4.0 once `main` carries
  1.5.0, and with which command.
- How each manager behaves when the version it is offered is lower than the one
  installed.
- Anything about a downgrade that only a run would show: none was made.

Keep all newer receipts, reviews, telemetry and qualification history, as in the
[1.0.0 guide](migration-1.0.0.md); do not restore an old registry wholesale over newer
bindings.

## Known unknowns carried by this release

- **PAT-220:** the sandbox of campaign sessions, see check 3; the refusing layer of the
  observed refusals is not recorded.
- **PAT-219:** where the excess of Linear requests of one real campaign comes from is
  unknown; PAT-228 measures and reduces it, and states its own limit (an orphan
  follow-up created outside the journal of the campaign Epic is not seen when the scan is
  skipped).
- **PAT-223:** a credential of an unknown shape is not masked in a stored summary.
- **PAT-230:** the cause of the State return is deduced, not observed.
- **PAT-227:** a PR description corrected by a campaign is not re-reviewed.
- **PAT-198:** native availability of the rollback profiles of PAT-17: unknown.
- **PAT-162:** plugin rollback: untried, see above.
