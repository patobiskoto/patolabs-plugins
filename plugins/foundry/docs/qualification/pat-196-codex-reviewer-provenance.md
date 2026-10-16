# Provenance of the Codex reviewer profile (PAT-196)

Procedure to establish which reviewer instructions a new Codex session actually loads.
Related: [`reviewer-queries.md`](../reviewer-queries.md) (the Codex reviewer contract and
the template), [`examples/codex-reviewer.toml`](../../examples/codex-reviewer.toml).
Frame: FOUNDRY-ADR-0006, FOUNDRY-ADR-0008, FOUNDRY-ADR-0018. Read-only helper:
[`scripts/codex_profile_provenance.py`](../../scripts/codex_profile_provenance.py)
(tests: `tests/test_codex_profile_provenance.py`, synthetic fixtures only).

The presence of a profile file, or a review that behaves as expected, does not prove which
profile the host loaded. Four things are distinct and are recorded one by one.

## What is recorded, and how

Only hashes (SHA-256 of `developer_instructions` after `strip()`), key names, versions and
short scalar fields. Never the content of a profile, of `config.toml`, of `AGENTS.md`, of a
rollout or of any credential file; never `auth.json`. The helper prints a field value only
when it is a short token (`[A-Za-z0-9._-]`, 64 characters at most) and otherwise prints
`<not_a_short_token>`; an absent field is printed `unknown`
(`_token` in the helper).

## Procedure

Every step except step 4 is read-only on local files and launches no model.

1. **File present.** Run the helper with `--profile <path of the custom reviewer profile>`
   and `--example plugins/foundry/examples/codex-reviewer.toml`. It reports `present`, the
   key names, the file hash and the `developer_instructions` hash of each, and
   `comparison.developer_instructions_equal` for the two named sets (`profile`, `example`).
   Proves: the file exists and whether its instructions equal the example's. Does not prove:
   that any session reads it.
2. **Configuration declaration.** Run the helper with `--config <config.toml>`. It reports
   the key names under `[agents]` (and under `[agents.reviewer]` if present) and whether the
   top-level keys `model`, `model_reasoning_effort`, `sandbox_mode`, `approval_policy`,
   `agents` exist, without their values. Proves: whether `config.toml` declares a reviewer
   role or an explicit profile file. Does not prove: that the host honours the declaration, nor
   which source wins when both a declaration and a file exist.
3. **Effective instruction source.** Not exposed as a field. Method: deduction by
   elimination from step 2 (no declaration in the user configuration file) and step 5
   (the profile file's text is in the session). Other sources (a project `.codex/` layer of
   the checkout, `-c` overrides, configuration profiles) are not examined: `unknown`.
4. **New session.** One native Codex session started after step 1, run by the coordinator
   under a specific prior authorisation (the maintainer authorised one session for PAT-196 on
   2026-10-15). Command: see "Native command". It asks the session to spawn the `reviewer`
   role once with a trivial task. This step is paid; re-running it needs a new specific authorisation.
5. **Profile actually loaded.** Run the helper with `--rollout <new rollout files>`.
   For each rollout it reports `loaded_profile = matched` when the profile's stripped
   `developer_instructions` text is found inside the text of a `developer`-role message item
   of that rollout (`profile_text_found`: ordinal of the message among the rollout's message
   items, and index of the content item), otherwise `not_observed`. Compared sets: the
   stripped `developer_instructions` string of `--profile` against the text of each content
   item of each developer message. Proves: that session was given developer input that
   contains this text verbatim. A short text could match by chance; the parent rollout,
   `not_observed` in the observed run, is the negative witness. Does not prove: that the
   model obeyed it, nor where the host read it from (file, declaration, or built-in). `not_observed` means the loaded profile is unknown,
   not absent (a different profile, an edited file, or a host that does not record it).
   Which rollout is the reviewer's: `session_meta.agent_role` and
   `session_meta.thread_source`, reported as `agent_role` and `thread_source`.

## Provenance of each recorded fact

| Fact | Source | Status |
|---|---|---|
| Host version | `session_meta.cli_version` of the rollout; `codex --version` for the installed binary | observed (both; they can differ if the binary was updated) |
| Foundry version named in the rollout | version segment of any `plugins/cache/<marketplace>/foundry/<version>` path found in the rollout (`foundry_versions`, each value passed through `_token`); a path cited in a prompt is enough, so this is not proof that this version was loaded | deduced from path strings; empty list means unknown |
| Model, effort requested | the flags of the native command (`-m`, `-c model_reasoning_effort`) | requested |
| Model, effort transmitted | `turn_context.model`, `turn_context.effort` and `collaboration_mode.settings` in the rollout | observed as recorded by the host |
| Model, effort observed by the backend | no field of the rollout states what the backend executed; `session_meta.base_instructions.provenance.model` is the base-instructions model, not a backend answer | unknown |
| Approval policy | `turn_context.approval_policy` | observed |
| Sandbox | `turn_context.sandbox_policy.type`, `turn_context.permission_profile.type` | observed (type only) |
| Token counts | `token_count` events (counted, not read) | observed; see FOUNDRY-ADR-0015 |

## Limits: three separate things

- **Read-only instructions** are prompt discipline in `developer_instructions`
  (`examples/codex-reviewer.toml`: "prompt discipline, not a native tool allow-list or
  sandbox"). A rollout match proves the text was delivered, not that it was followed.
- **Tool permissions** are not set by the template (`CODEX_ROLES["reviewer"]` in
  `routing_facades.py` requests checks and installs no allow-list). `approval_policy` and
  `permission_profile.type` are reported as separate fields and are not an allow-list.
- **Native sandbox** is `sandbox_policy.type`. The template omits `sandbox_mode`, so a
  spawned reviewer inherits it (documented inheritance, `reviewer-queries.md`). The type
  recorded for the reviewer rollout is what was applied to that rollout; it says nothing
  about the other two. Whether a read-only native sandbox allows the Foundry ledger lock
  remains unknown (`reviewer-queries.md`).

A previous rollout can be used as a read-only illustration of the field names (it was found
to carry all the fields above), but it is not the PAT-196 observation: only the new session
of step 4 is.

## Native command

Prepare, then run once from the checkout root (replace the placeholder with the checkout path; the helper and example paths below are relative to the root, so the working directory matters). The marker file is outside the Codex home:

```
touch "$TMPDIR/pat196.marker"
codex exec -C <path of the repository checkout> -s read-only \
  -m gpt-6.1-sol -c 'model_reasoning_effort="low"' \
  "Spawn exactly one subagent with the custom agent role named reviewer. Tell it: reply with the single word READY, call no tool. Wait for it, then reply with the single word DONE. Do nothing else."
```

Then read-only:

```
codex --version
find ~/.codex/sessions -name 'rollout-*.jsonl' -newer "$TMPDIR/pat196.marker"
python3 plugins/foundry/scripts/codex_profile_provenance.py \
  --profile ~/.codex/agents/reviewer.toml \
  --example plugins/foundry/examples/codex-reviewer.toml \
  --config ~/.codex/config.toml \
  --rollout <each rollout listed by find>
ls ~/.codex/plugins/cache/*/foundry
```

In the observed run the command was exactly this one, with `-C` set to the PAT-196 worktree
and that worktree as the working directory.

The command overrides the model, effort and sandbox for that one invocation only
(`-m`, `-c`, `-s` apply to the session; they write nothing to the configuration). The
reviewer's own model, effort and sandbox are whatever the host transmits to it, read from
its rollout, not from these flags.

## Rollback

Described, not executed by this page. Step 4 writes session rollouts under `~/.codex/sessions` (observed, read back in step 5);
other writes of the host under the Codex home were not examined (unknown).
If a profile was installed from the example during qualification: restore the saved copy of
the previous custom reviewer file in the profile directory, or remove the file (whether the host then has a built-in `reviewer` role is unknown here;
steps 1 and 5 observe the effect), then start a new session and repeat steps 1 and 5 to see the
effect. The prior file must be backed up first (`reviewer-queries.md`).

## Observed run

Observed run (2026-10-15, 06:51 Europe/Paris, the single native session authorised by the
maintainer for this issue; parent run with `codex exec -C <the PAT-196 worktree> -s read-only
-m gpt-6.1-sol -c 'model_reasoning_effort="low"'` as in step 4, from that worktree as working
directory, exit 0, the parent and the child each replied
with the requested single word). Readback with `scripts/codex_profile_provenance.py` on the
two rollouts newer than the marker (hashes and field values only):

| Fact | Parent rollout | Reviewer rollout | Status |
|---|---|---|---|
| `session_meta.agent_role` / `thread_source` | `unknown` / `user` | `reviewer` / `subagent` | observed |
| `session_meta.cli_version` | `0.162.1` | `0.162.1` | observed |
| Loaded profile (instructions text found in the first developer message) | `not_observed` | `matched` (message 0, item 0) | observed |
| Model / effort requested | `gpt-6.1-sol` / `low` (`-m`, `-c` of the command) | none stated by the spawn task or the profile (its keys name no model or effort) | requested |
| `turn_context.model` / `effort` (transmitted) | `gpt-6.1-sol` / `low` | `gpt-6.1-sol` / `low` | observed as recorded by the host |
| `collaboration_mode.settings` model / reasoning effort | `gpt-6.1-sol` / `low` | `gpt-6.1-sol` / `low` | observed (helper fields `collaboration_model`, `collaboration_reasoning_effort`, run on the two rollouts on 2026-10-15) |
| `codex --version` | `codex-cli 0.162.1` | same installed binary | observed |
| Codex plugin cache listing (`ls ~/.codex/plugins/cache/*/foundry`) | foundry `1.3.0` | same listing | observed at that time |
| `turn_context.sandbox_policy.type` / `approval_policy` / `permission_profile.type` | `read-only` / `never` / `managed` | `read-only` / `never` / `managed` | observed |
| Foundry version named in rollout paths | `1.3.0` | `1.3.0` | deduced from `plugins/cache/<marketplace>/foundry/<version>` (not proof of what was loaded) |
| Model and effort executed by the backend | unknown | unknown | not exposed |

Profile `developer_instructions` SHA-256 `2cf9ff55…dd4d`; example `e42dd0cb…c8d4`; equal:
`false` (the installed profile is the maintainer's current reviewer patch). `config.toml`
declares no `[agents.reviewer]` key (`agents` keys: `max_concurrent_threads_per_session`):
deduced, by elimination, that the profile is loaded by file discovery (step 3 names the
sources not examined). A built-in `reviewer` source is not excluded by observation: the
matched text equals the installed profile and differs from the versioned example (`equal:
false`), which makes a built-in carrying that same text unlikely (deduced), not ruled out
(unknown). The child's values equal the parent's; the profile sets none of them (its keys
are `description`, `developer_instructions`, `name`). Two explanations fit and are not told
apart: inheritance by the child or transmission by the parent at spawn; both are consistent
with what was observed and neither is established (unknown: no field of the rollout names
the origin). The loaded profile is established for this host version and this session only;
another host version or a session started differently is not covered.

## Documentation status (R5)

New page; new script `scripts/codex_profile_provenance.py` with options `--profile`,
`--rollout`, `--example`, `--config`, documented above; pointer added to
`examples/codex-reviewer.toml` and to `reviewer-queries.md`. No routing constant, model or
effort vocabulary, or configuration key changed.
