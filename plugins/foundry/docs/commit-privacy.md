# Commit identity privacy

The existing pre-push hook now checks the author email, committer email and full
message (including co-author trailers) of commits newly published to the destination.
It rejects the two retired maintainer identities declared in
`.githooks/pre-push`. Other identities, including GitHub and Anthropic no-reply
addresses, remain allowed. Diagnostics identify the commit and field without printing
the private email or message.

Enable it from the monorepo root:

```sh
git config core.hooksPath plugins/foundry/.githooks
```

New branches, updates, force pushes and commit-bearing tags are checked. Annotated
tag objects are also checked. Deleting a work branch is allowed; the existing R1 ban
on direct main/master pushes remains. A malformed hook input, failed Git command,
unreachable destination or missing advertised object refuses the push: fetch the
destination, then retry. No history is rewritten by this hook.

The destination's currently advertised branch and tag ancestry is excluded from the
scan. Previously published history can contain immutable evidence and provenance;
it must be audited separately rather than erased to unblock a normal work branch.
Local remote-tracking refs alone are insufficient to decide what is already public.

The hook does not override Git identity. Set the correct email in each applicable
Git configuration and remove conflicting author/committer environment overrides.
It cannot control server-created squash commits, bypasses using `--no-verify`, other
machines without the hook, or concurrent changes after its remote observation.
Enable GitHub email privacy and its private-email push protection as a second layer.
GitHub-generated squash trailers can retain authors from PR commits; inspect older
unmerged PR commits before merging them.

Foundry reviews, test/CI receipts and merge gates keep their exact SHA semantics.
The hook neither migrates proofs nor asserts that an old proof validates a new SHA.
