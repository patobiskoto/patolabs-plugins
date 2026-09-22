# Public repository security boundary

`github.com/patobiskoto/patolabs-plugins` is public but does not accept an external
contribution path. GitHub Issues is disabled and Linear project `PAT` is the repository
tracker selected by `.foundry/tracker.json`.

The `main` branch is protected server-side. Every merge requires a pull request, a
branch current with `main`, and successful `foundry`, `catalogue`, and `ship-ios`
checks. The rule applies to administrators, requires linear history and resolved
conversations, and rejects force-pushes and branch deletion. No concurrent repository
ruleset was present at the recorded readback.

The credential-free readback is recorded in `github-main-protection.json`. It is an
operational snapshot, not a GitHub-signed attestation; re-read GitHub before relying on
it for a later merge. The bounded rollback is to remove the classic protection only
after an explicitly reviewed operational failure, verify that removal, correct the
configuration, and restore protection before another merge attempt. It never changes
the Linear or YouTrack binding.

This implements the accepted trust boundary in FOUNDRY-ADR-0025. Workflow-side fork
isolation remains implemented in `.github/workflows/ci.yml`; branch protection is the
server-side complement that makes the merge gates unavoidable.
