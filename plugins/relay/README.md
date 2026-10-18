# relay

Session hand-over for Claude Code and Codex. At the end of a session it writes a resume
state in a fixed format, after checking it against the real state and refusing secrets.
Stdlib-only Python. Reloading the state at the start of the next session is not part of
this version (PAT-249).

```
python3 plugins/relay/tooling/relay_cli.py write --draft FILE|-
python3 plugins/relay/tooling/relay_cli.py show [--json]
```

`write` exits 2 when the draft file does not exist or is not UTF-8 text. `show` exits 1
when no state has been written yet or the stored state is unreadable or malformed, and 2
outside a git repository (`cmd_write`, `cmd_show` in `tooling/relay_cli.py`).

Skills: `/relay:write`, `/relay:show` (Claude Code); `$relay:write`, `$relay:show` (Codex).

## Draft format

A JSON object. Keys are fixed (`KEYS` in `tooling/relay/schema.py`); an unknown key, a
missing required key or a wrong type refuses the draft with every error listed, and
nothing is written (exit 2).

| Key | Type | Required |
|---|---|---|
| `summary`, `next_step` | non-empty string | yes |
| `decisions`, `blockers`, `pitfalls` | list of strings | no (default empty) |
| `files` | list of paths, relative to the repository root, absolute or starting with `~`; `path:line` and `path:line:col` accepted | no |
| `issues` | list of ticket identifiers (`ABC-12`) | no |
| `prs` | list of PR numbers (`7`, `#7`) or PR URLs | no |
| `branch`, `commit` | string; the branch and the commit (full, or a prefix of at least 4 hex characters) the author believes current | no |

## Where the state is written

`<git-common-dir>/relay/state.json` (`state_path` in `tooling/relay_cli.py`), directory
mode 700, file mode 600, written to a temporary file in the same directory then
`os.replace`d (`write_atomic`). Choice, deduced: the git common directory lives inside
`.git`, so git never tracks it (no `.gitignore` needed) and all worktrees of a repository
share one state. There is one state per repository; a new write replaces the previous one.
Outside a git repository the tool refuses to write (exit 2).

## Verification

Run before writing (`verify` in `tooling/relay/verify.py`); each check is `ok`,
`divergence` or `unknown`. The state is written anyway, with the results recorded in it
and printed; a divergence is reported, not blocking.

| Check | Source | Divergence | Unknown |
|---|---|---|---|
| branch, commit | `git symbolic-ref`, `git rev-parse HEAD`, compared with `branch`/`commit` when given; when omitted, recorded as read from git, not declared | differs, or `commit` is not 4 to 64 hex characters | git cannot answer |
| worktree | `git status --porcelain` | uncommitted changes | git cannot answer |
| file pointers | filesystem; a `:line` or `:line:col` suffix is dropped only when the path as written does not exist and the bare path does | path does not exist | the filesystem refuses the check (for example a permission error) |
| tickets | Foundry: `foundry_cli.py query issue <ID>` (tracker-agnostic; reads the tracker bound to the repository) | identifier malformed | Foundry not found, or it cannot read the ticket |
| pull requests | `gh pr view <ref> --json state` (not through Foundry: Foundry's issue read gives only the `pr_url` link) | not a number or URL | `gh` absent or cannot answer |

Locating Foundry (`locate_foundry`), in this order: (1) the `RELAY_FOUNDRY_CLI` environment
variable (path of `foundry_cli.py`); (2) checkout layout: `plugins/foundry/tooling/foundry_cli.py`
next to `plugins/relay`; (3) installed-cache layout (`<marketplace>/relay/<version>/` on
Claude Code and Codex): `<marketplace>/foundry/<version>/tooling/foundry_cli.py`, the highest
numeric version present (the highest present, not proven to be the one the host loads;
directories whose name is not a numeric dotted version are ignored). If none is a file,
every ticket is `unknown` ("Foundry not found"); nothing is guessed and an unreadable fact
is never `ok`. Each ticket check's detail names the Foundry path and how it was found. Each
launched command is bounded at 30 s (`TIMEOUT`) and reads its standard input from `subprocess.DEVNULL`; the
whole verification is bounded at 120 s (`BUDGET`): a command still running then is stopped,
and every check not done by then is `unknown` ("verification time budget exhausted").

## Secrets

The draft is scanned before anything else, every string and every key
(`tooling/relay/secrets.py`, `PATTERNS`). A match refuses the whole draft (exit 3,
nothing written) and names the field and the pattern, never the value. Refusal was chosen
over masking: a masked value would silently change a state that the next session relies
on; the author removes it and retries. Patterns: GitHub tokens (`ghp_`, `gho_`, `ghu_`,
`ghs_`, `ghr_`, `github_pat_`), Anthropic (`sk-ant-`), OpenAI-style (`sk-`), Linear
(`lin_api_`, `lin_oauth_`), Slack (`xox[abprs]-`), AWS access keys (`AKIA`/`ASIA`), PEM
private key headers, JWT-shaped tokens, assignments (`=`, `:` or `:=`) with a value of 8 or
more characters, quoted or not, whose name holds a word ending in `password`, `passwd`,
`pwd`, `secret` or `token`, or the word `key` after a word ending in `api`, `access` or `secret` (`_Assignment`;
the name is split into words at `_`, `-`, `.` and camelCase boundaries: `DB_PASSWORD`,
`accessToken`, `PGPASSWORD`, `secret-key`, `AWS_SECRET_ACCESS_KEY`), `Authorization: Bearer` headers,
credentials in URLs (`scheme://user:pass@host`) and YouTrack permanent tokens (`perm:`).
An assignment is not refused when its value is a shell reference (`$VAR`, `${VAR}`,
`$(cmd …)`), when a `pwd` value starts like a path, or when the credential word is followed
by `count`, `limit`, `budget`, `length` or `size` (`token_count`). Not covered (deduced from
the code): plurals (`tokens`, `passwords`), a credential word glued to the next word in the
same case (`PASSWORDFILE`) and `key` after another word (`private_key`).
The final state, including what is read from git, `gh` and Foundry, is scanned again before
the write (exit 3). One
test per pattern in `tests/test_relay.py`. This is a pattern filter: a secret in an
unknown format passes (deduced limit, not a guarantee).

## Documentation status (AGENTS.md#R5)

| Artifact | Status |
|---|---|
| CLI verbs `write`, `show`, option `--draft`, `--json` | documented here |
| Draft keys and vocabulary | documented here (`schema.py` `KEYS`) |
| `RELAY_FOUNDRY_CLI`, `TIMEOUT`, `BUDGET`, state location, file modes, exit codes | documented here |
| Secret patterns | documented here (`secrets.py` `PATTERNS`) |
| Foundry docs | not necessary: no Foundry CLI, constant or behaviour changed; only its CI jobs list and a changelog fragment were updated |
