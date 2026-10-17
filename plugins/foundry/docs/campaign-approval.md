# Epic campaign approval (PAT-215, PAT-ADR-0026)

A campaign may start only after a human explicitly approved its preview. This page covers
the record of that approval and the check a later cadencer calls. Nothing here starts a
campaign, reads or writes a tracker, or changes the preview
([`epic-preview.md`](epic-preview.md)): `epic_preview.py` has no approval code path, so a
preview never approves itself.

## Commands

```text
python3 tooling/foundry_cli.py campaign-approval approve PAT-100 \
  --preview-file preview.json --max-sessions 20 [--max-sessions-per-issue 3] [--ttl-hours 24]
python3 tooling/foundry_cli.py campaign-approval status PAT-100
python3 tooling/foundry_cli.py campaign-approval check PAT-100 --preview-file preview.json
```

`preview.json` is the output of `epic-preview` (`PREVIEW_CONTRACT`). `approve` reads that
file only: it recomputes `preview_digest` over the file's material with
`epic_preview._digest` and refuses a hand-edited file, a file for another Epic, an Epic identifier outside the issue-id format (the complete record is validated before any append, nothing is written otherwise) and missing
or non-hex digests (`digests_from_preview`, `campaign_approval.py`). It then prints the
digests, expiry and ceilings and requires the human to retype the first 12 hex characters
of `preview_digest` (`CONFIRMATION_LENGTH`). Bounds: `--max-sessions` 1 to
`MAX_SESSIONS_CEILING`, `--max-sessions-per-issue` 1 to `--max-sessions`, a lifetime of at
most `MAX_TTL` (default `DEFAULT_TTL`). `check` and `status` exit 2 with a JSON refusal on
stderr (`refused` is one of `absent`, `expired`, `preview_digest_mismatch`,
`structure_digest_mismatch`, `journal_invalid`, `approver_refused`).

`check(epic, preview_digest, structure_digest, now)` is the function a cadencer calls
before any effect. It only reads the journal, and returns the latest unexpired matching
record or raises a typed refusal: absent (no record for the Epic), a different preview
digest, a different structure digest, expired, or an invalid journal. One line that fails
validation makes the whole journal invalid: nothing is trusted then.

## Record and storage

Schema `foundry-campaign-approval.v1` (`APPROVAL_CONTRACT`), one canonical JSON object per
line: `epic`, `preview_contract`, `preview_digest`, `structure_digest`, `issued_at`,
`expires_at`, `max_sessions`, `max_sessions_per_issue` (nullable), `approved_at`,
`approver` (`{"kind": "human-tty"}`) and `record_digest` (SHA-256 over every other key).
Neither the format nor the file name names a tracker.

The journal is `campaign-approvals.jsonl` (`JOURNAL_NAME`) in `registry.data_dir()`:
`FOUNDRY_DATA`, else `~/.config/foundry`, outside any repository (the local store of
FOUNDRY-ADR-0016). It is append-only: `_append` opens it `O_APPEND`, mode 0600, under
`flock`, and never rewrites or truncates. A second approval adds a line. There is no
revocation record yet: an approval ends by expiry.

## An agent cannot approve (layers and limit)

1. `approve` reads the confirmation from `/dev/tty` only, never from stdin, arguments or
   the environment, and refuses when stdin or stdout is not a terminal or `/dev/tty` cannot
   be opened (`_confirm_on_tty`, `_stdio_is_tty`). That an agent tool shell has neither a
   terminal on stdin/stdout nor a controlling terminal is deduced, not observed on every host;
   a shell started under a pseudo-terminal allocator (`script`, `pty`) passes this layer.
2. `approve` refuses when an agent-session marker exists in the environment: `CLAUDECODE`
   or any name starting with `CODEX_` (`agent_markers`). The refusal names the variable,
   never its value.
3. The Claude `PreToolUse` Bash hook denies a command invoking `campaign-approval approve`
   or `campaign_approval approve` (`approval_deny_reason`, `hooks/guard_bash.py`), in any
   repository, registered or not. `bash -c '...'` indirection is out of scope, as for R1.
   Codex: `.codex-plugin/plugin.json` declares no hook (observed); whether Codex runs this
   guard on its shell through the shared `hooks/hooks.json` is unknown.

Tests: `tests/test_campaign_approval.py` (refusal without terminal, under each marker,
success with a fake terminal, no tracker access) and `tests/test_hooks.py`.

Limit, stated plainly: the three layers stop an accidental or cooperative call by an agent,
not a deliberate bypass (unsetting the marker, a pseudo-terminal, `bash -c`); like
`guard_bash.py`, they are a discipline tool, not a sandbox. This is not a cryptographic
guarantee either. A process running as the same
OS user can append to the journal file directly. `check` refuses a line whose
`record_digest` does not verify, so a malformed or edited hand-append is refused, but a
well-formed forged line, with a correct digest, cannot be detected.
