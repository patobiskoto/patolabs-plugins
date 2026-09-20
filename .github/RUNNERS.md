# GitHub Actions runners

This public repository uses GitHub-hosted runners: `ubuntu-latest` for Foundry and
`macos-latest` for ship-ios. No repository-specific self-hosted runner, local path, or
machine credential is required.

The workflow uses the checkout action's ephemeral workspace and GitHub's job-scoped
token. Configure any optional integration credential only as a repository secret; never
commit it to this repository.
