# Independent reviewer queries (PAT-176)

Decision under accepted FOUNDRY-ADR-0006 and FOUNDRY-ADR-0013: the reviewer
may search literal text and list paths in its already claimed repository snapshot.
This implements the existing read-only review authority; it grants no Git, tracker,
network, test/build or arbitrary shell/Python authority. Tier/effort floors, claim
ownership and delivery gates remain governed by `routing.py` and `routing_facades.py`.

The CLI verbs `routing search-review` and `routing list-review` require the same
`--diff-hash`, `--claim-id`, exact absolute `--root` and immutable 40-hex `--base`
as `routing read-review`. `search-review` additionally requires `--text`.
Their parser is in `routing.main`; `review_queries.query_review` uses
`routing.claimed_review_diff` to retain the active-generation lock through checks
of root/base, immutable HEAD and current diff hash, query execution and stdout flush.
The commands neither acquire tracker credentials nor modify the repository or claim
record. The existing ledger lock can create/open a synchronization file outside the
repository (`ReviewDeduplicator._locked`); business read-only authority does not
mean zero filesystem effects.

Run these commands with the active coordinates supplied to the reviewer:

```bash
python3 <plugin-root>/tooling/foundry_cli.py routing search-review --diff-hash <hash> --claim-id <id> --root <absolute-root> --base <base-sha> --text "literal text"
python3 <plugin-root>/tooling/foundry_cli.py routing list-review --diff-hash <hash> --claim-id <id> --root <absolute-root> --base <base-sha>
```

`review_queries.py` reads the immutable claimed HEAD with Git `ls-tree` and
`cat-file`, via fixed argv without a shell and with replacement objects disabled.
Only regular tracked blobs (modes `100644`, `100755`) are queried: no symlinks,
submodule traversal, untracked files or working-file modifications. Search is
case-sensitive literal substring matching, one result per matching line, with
one-based line numbers. `MAX_TEXT_CHARS` is 512, `MAX_BLOB_BYTES` is 1,048,576 and
`MAX_MATCHES` is 1,000. Search reports `skipped` for oversized, binary (NUL) or
non-UTF-8 blobs, and `truncated` if more matching lines exist. Listing includes
regular binary/oversized blobs too. The JSON response records `source: claimed_head`,
`head` and `diff_hash`; search emits `matches`, listing emits `paths`.

The reviewer may label a check "verified by me" only after executing it and reading
the returned evidence. Cite path, line and HEAD for text matches. An absence claim
must name this scope and account for skipped/truncated results. Working-file reads
for surrounding context are a separate evidence source. A coordinator's query output
or a deterministic fixture run is not a native reviewer observation.

## Host enforcement and reproducible profile compatibility

Claude's routed read-only profiles expose `Read, Grep, Glob, Bash` without edit/write
or delegation tools. `guard_routed_readonly.py` permits only the three claimed CLI
readers, with fixed argument ordering; it denies raw diff, tracker commands, arbitrary
shell/Python, tests/builds, redirection, composition and substitution. For Bash search,
the guard additionally refuses shell metacharacters and control characters in the
literal text. Use the available Grep tool for such patterns; do not construct a shell
bypass. These portable hooks are discipline, not an OS security boundary; their
exception handler retains its existing fail-open behaviour.

Codex `CODEX_ROLES["reviewer"].contract` requests these checks but does not dynamically
install a tool allow-list. A developer-priority custom reviewer that mandates
`gh pr diff` conflicts with the claimed verifier. The shipped
[`examples/codex-reviewer.toml`](../examples/codex-reviewer.toml) is a reproducible
compatible role: back up the custom role, compare its instructions with this template,
and replace the conflicting diff instruction with its Foundry branch. Load a fresh
role/session before qualifying an actual reviewer invocation. No model or effort is
set by the template; routing retains those decisions.

The template deliberately inherits sandbox configuration. OpenAI's
[custom agent configuration](https://learn.chatgpt.com/docs/agent-configuration/subagents)
documents `sandbox_mode = "read-only"` and inheritance of omitted keys. Such a native
sandbox may deny the existing ledger lock; compatibility of that lock path is unknown
until observed on the host. The shipped template is prompt discipline, not proof of
native sandbox or tool enforcement. If the host refuses the verifier or its lock, stop
and report the limitation. Never fall back to an unverified diff. No installed custom
role or cache is changed by generating Claude profiles.

The disposable procedure in `tests/test_review_queries.py` routes a real Claude
profile, submits the accepted commands through its guard and runs the shared CLI
against a two-commit Git fixture. It verifies line evidence from the committed blob,
path scope, unchanged repository bytes/claim record and denials of forbidden commands.
It also rejects stale HEAD/claim/root/base/hash before emitting results. This is
deterministic capability evidence; native independent review requires its own observed
reviewer report.
