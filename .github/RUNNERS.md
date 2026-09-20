# GitHub Actions runners

This public repository uses GitHub-hosted ephemeral runners: `ubuntu-24.04` for
Foundry and catalogue, and `macos-14` for ship-ios. No repository-specific
self-hosted runner, local path, or machine credential is required.

Fork pull requests match no executable job. Internal pull requests run the three
checks above with `contents: read`, no repository secret reference, and no
`pull_request_target` trigger. The workflow pins `actions/checkout` v4.2.2 and
`actions/setup-python` v5.6.0 to audited immutable SHAs.

## Required GitHub settings before public visibility

The workflow cannot set repository policy. The read-only baseline observed on
2026-09-20 found a public repository with Issues disabled and secret scanning/push
protection enabled, but with no protection on `main`, all Actions allowed, and SHA
pinning enforcement disabled. Before public visibility, an administrator must:

1. Protect `main`: require pull requests, require the `foundry`, `ship-ios`, and
   `catalogue` checks, require an up-to-date branch, prevent direct pushes, and
   restrict bypass actors.
2. Keep the workflow token read-only and Actions PR-review approval disabled.
3. Require SHA pinning for Actions, retain the existing immutable pins, and audit
   every future Action update.
4. Keep Issues disabled and accept public vulnerability reports through
   `SECURITY.md`. Forking is currently allowed; the tested workflow condition must
   continue to leave fork PRs jobless.
