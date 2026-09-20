# Public snapshot receipt

## Provenance

- Source repository: `https://github.com/patobiskoto/claude-plugins`
- Source ref queried: `refs/heads/main`
- Verified source commit: `967c4d6025fb37828dc6277844f4c785ef74c9f9`
- Source commit timestamp: `2026-09-20T17:48:13+02:00`
- Snapshot method: `git archive` of that exact remote commit; no local branch,
  worktree change, pull-request head, or Git history was copied.

## Source CI verification

GitHub check-runs were queried for the exact source commit before export. The
`catalogue`, `foundry`, and `ship-ios` runs concluded `success`; the conditional
`foundry-benchmark-campaigns` and `foundry-youtrack-smoke` runs concluded `skipped`.
The legacy commit-status endpoint had no statuses. This satisfies the source snapshot
check: at least one real successful check-run is present, and the receipt does not
represent skipped runs as success.

## Public-surface audit

- Excluded: `plugins/foundry/benchmarks/` in its entirety, including evidence, frozen
  references, benchmark fixtures, reports, and replay tooling.
- Excluded: Git metadata and all source-repository history (an archive export has no
  `.git` directory).
- Removed: repository-specific runner labels, personal filesystem paths, and the
  benchmark/YouTrack-smoke workflow configuration. CI now uses GitHub-hosted runners.
- Repointed: packaged plugin manifests and installation documentation to
  `patobiskoto/patolabs-plugins`.
- Audited: no credential file, private-key file, `.env` file, or credential value is
  included. References to credential *names* remain only where needed to document
  runtime configuration and test secret-redaction behavior.

## Snapshot contents

The root Claude and Codex marketplaces are present, along with both Claude and Codex
manifests for `plugins/foundry` and `plugins/ship-ios`. The initial public history is
created in this successor repository only; no GitHub issues are migrated.
