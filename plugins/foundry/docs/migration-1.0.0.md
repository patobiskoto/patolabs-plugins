# Foundry 1.0.0 — setup and migration, 2026-10-03

**Candidate guide; publication and official installed-version checks pending.** Use
the procedures below after the merged 1.0.0 source is published through the release
gates. Both Foundry manifests must report `1.0.0`; Ship-iOS remains independently
`0.3.0`. The catalogues have no version key. See [release-1.0.0.md](release-1.0.0.md)
for qualification refs, client/model limits and pre-merge versus post-install evidence.

## Official installation or upgrade

For a fresh Claude Code installation:

```text
/plugin marketplace add patobiskoto/patolabs-plugins
/plugin install foundry@patolabs
/plugin install ship-ios@patolabs
```

Ship-iOS is optional. For an existing Claude Code installation:

```text
/plugin marketplace update patolabs
/plugin update foundry@patolabs
/reload-plugins
/plugin list
```

The terminal equivalents are `claude plugin marketplace update patolabs`,
`claude plugin update foundry@patolabs -y`, and `claude plugin list --json`.
Reload components or start a new session before checking the loaded plugin.

For a fresh Codex installation:

```sh
codex plugin marketplace add patobiskoto/patolabs-plugins
codex plugin add foundry@patolabs
codex plugin add ship-ios@patolabs
```

For an existing Codex installation, refresh the Git snapshot separately from the
installed package, then start a new task:

```sh
codex plugin marketplace upgrade patolabs
codex plugin remove foundry@patolabs
codex plugin add foundry@patolabs
codex plugin list --marketplace patolabs --json
```

No cache editing or generated development cachebuster is installation evidence.
An installation still pointed at a former private marketplace needs an explicit
source switch using the official remove/add and fresh install/add sequence described
in [migration-0.9.0.md](migration-0.9.0.md). Removing a marketplace removes its plugins;
inventory them before switching and reinstall only those desired from the new source.
Package replacement preserves the shared Foundry data/configuration, credentials,
registry, receipts and routing state; it does not migrate project data.

## A first repository in five steps

Run from the intended application's Git checkout. In the commands below,
`<foundry-root>` is the **actually loaded installed plugin** root: Claude expands
its plugin root in skills; Codex derives it from the loaded `SKILL.md` path. Replace
angle-bracket placeholders with the selected non-secret coordinates before running.

1. **Choose one tracker and an existing project.** Use `youtrack`, `linear` or the
   qualified `ghprojects` private personal Project/private-repository shape. Read
   [the tracker contract](tracker-contract.md), [Linear binding](linear-tracker.md)
   or [GitHub binding](ghprojects-tracker.md) for the provider's exact IDs and fields.
   Bootstrap verifies an existing project; it creates no team, board, workflow,
   provider field or permission. GitHub Issues are used only for a repository that
   explicitly chose GitHub Projects. This marketplace's own checkout remains
   **Linear/PAT**; its GitHub Issues are disabled and its old YouTrack project archived.
2. **Provide credentials outside Git.** Run `/foundry:configure` (Claude Code) or
   `$foundry:configure` (Codex), naming the chosen provider. YouTrack needs its URL
   and `YOUTRACK_TOKEN`; Linear needs `LINEAR_API_TOKEN`. Store credentials through
   Foundry's invisible interactive Keychain prompt on macOS, or an environment/secret
   manager elsewhere. GitHub tracker/code-host use the authenticated `gh` account;
   qualify the selected account's repo/Project access, without copying a token into
   argv, registry, chat or a file. Non-secret settings live in
   `~/.config/foundry/config.env` (or `FOUNDRY_CONFIG`); shared registry/data use
   `FOUNDRY_DATA` or `~/.config/foundry`, independent of host plugin cache directories.
3. **Bootstrap the repository binding.** Select the provider explicitly and provide
   its verified existing project coordinates. For example:

   ```sh
   python3 <foundry-root>/tooling/foundry_cli.py configure set --url <youtrack-url> --tracker youtrack --codehost github
   python3 <foundry-root>/tooling/foundry_cli.py configure token
   python3 <foundry-root>/tooling/foundry_cli.py registry bootstrap youtrack <repo-basename> <KEY> <native-project-id>
   ```

   Linear uses `configure set --tracker linear --codehost github`, interactive
   `configure credential --name LINEAR_API_TOKEN`, then
   `registry bootstrap linear <repo-basename> <KEY> <project-uuid>` with complete
   `team_id`, shell-quoted JSON `state_ids`/`type_label_ids` and any label/milestone
   maps required by the product. GitHub uses `configure set --tracker ghprojects
   --codehost github`, then `registry bootstrap ghprojects <repo-basename> <KEY>
   <Project-node-id> owner=<User-login> number=<Project-number>`.
   The provider documents specify the complete validation rules; do not guess UUIDs
   from names. Bootstrap derives `canonical_repo` from the actual Git remote,
   re-reads provider identity, and publishes the registry plus `.foundry/tracker.json`
   only on a verified match. Missing maps, conflicting aliases or archived bindings
   refuse. Never edit a marker/registry by hand to bypass this verification.
4. **Verify binding and doctor before work.** Run through the installed CLI:

   ```sh
   python3 <foundry-root>/tooling/foundry_cli.py configure show
   python3 <foundry-root>/tooling/foundry_cli.py registry selection --require-v1
   python3 <foundry-root>/tooling/foundry_cli.py doctor
   python3 <foundry-root>/tooling/foundry_cli.py routing show --host claude
   python3 <foundry-root>/tooling/foundry_cli.py routing show --host codex
   ```

   Equivalent doctor skills are `/foundry:doctor` and `$foundry:doctor`.
   `configure show` reports configured/missing, never secrets. Selection must report
   the intended canonical repository, tracker/project, V1 mode and configuration
   digest. Resolve red checks from the reported cause. Hook setup, if needed, is
   repository-local as doctor directs; it never requires changing global hooksPath.
5. **Run the first journey.** Invoke `/foundry:frame` or `$foundry:frame` to record
   the idea, accepted ADR constraints, Epic and precise AC; use `roadmap` and
   `next-issue` for the recommendation, then explicitly elect the issue. Continue
   `start-issue` → implementation → `open-pr` → independent review/CI/human gate →
   `merge-pr`, with `resume-issue` after interruption. Complete a non-code Epic
   through `close-epic` and its exact-graph human verdict. A release milestone uses
   explicit provider release mappings, `query changelog <release>`, and the optional
   Ship-iOS bridge from that same application checkout. App Store submission keeps
   its separate explicit human gate.

## Upgrade 0.9.0 or a legacy YouTrack configuration

Before package replacement, retain a private snapshot of the non-secret config,
registry, repository marker, routing policy (including absence), installed plugin
source/ref/version and relevant evidence. Preserve credentials in their current
secret store, rather than copying them into the snapshot.

An upgrade does not elect a new tracker. An existing valid V1 marker remains
authoritative, including over a contradictory host-global `FOUNDRY_TRACKER` default.
A present invalid/ambiguous marker fails closed; removing it is not a repair.
For **patolabs-plugins**, verify Linear/PAT and its repository-binding digest;
never re-enable the archived YouTrack project or introduce GitHub Issues.

A legacy YouTrack registration may still resolve outside V1. To gain V1 binding,
keep the same active native project and explicitly run from its checkout:

```sh
python3 <foundry-root>/tooling/foundry_cli.py registry upgrade youtrack <legacy-repo-basename>
python3 <foundry-root>/tooling/foundry_cli.py registry selection --require-v1
python3 <foundry-root>/tooling/foundry_cli.py doctor
```

Upgrade freshly verifies the exact historical entry's native project id/key and
checkout identity, compares the same candidate before publication, and adds a V1
marker without altering another alias or tombstone. Missing, foreign, contradictory
or archived sources refuse. A new project uses `bootstrap`, not an invented legacy
entry. Selecting Linear or GitHub instead is an explicit separate cutover governed
by its audited migration contract; installing 1.0.0 does not authorize or perform it.

## Verify the version actually loaded

Record four independent outcomes: clean Claude install, Claude 0.9.0 upgrade,
clean Codex install and Codex 0.9.0 upgrade. After reload/new task, retain:

- Actual invoked host binary/path/version (`claude --version`, `codex --version` or
  embedded executor metadata). A PATH binary is not proof of the desktop executor.
- Official manager source/installed listing, enabled scope, marketplace revision,
  loaded skill/hook root and both manifest versions at that resolved root. They must
  identify Foundry `1.0.0` from the exact published source ref. A manager listing or
  source checkout alone is insufficient when a running session still loaded an old
  cache; if the loaded ref is unavailable, report unavailable and leave that check open.
- Installed CLI `configure show`, `registry selection --require-v1`, doctor and route
  readbacks from the intended checkout, plus Ship-iOS 0.3.0's capability bridge where
  used. Never substitute this repository's source CLI for the installed CLI evidence.

Claude Code **2.1.284 minimum** is declared for the complete retained model set;
**2.1.285 actually tested**, firstParty/claude.ai Pro. Opus 5.5 individually needs
2.1.280. See [release profiles](release-1.0.0.md) for exact canonical names, aliases,
IDs and observed limits. Other providers/clients remain unqualified. A successful
install is not proof that every model is included on the user's account.

## Overrides and rollback scope

Explicit request → `.foundry/model-routing.json` → Foundry defaults remains the
field-aware precedence. Existing exact pins are not rewritten. Review `routing show`
for both hosts and keep personal/managed restrictions; doctor exposes override names
without values. Codex effective profile presence can be passed as
`--profile-model-active --profile-effort-active`, never as private profile values.
Reviewer/architect floors, escalation state and historical proofs remain intact.

The rollback evidence is deliberately bounded:

| Operation | Existing tested evidence | Limit |
| --- | --- | --- |
| Restore a compatible repository config/marker/policy snapshot | PAT-61 adversarial recipe: exact saved bytes restored after drift preflight; archive remains readable/non-writable | Local configuration restoration, no official package downgrade |
| Deliberately restore GPT-5.6 routing | PAT-15 `test_codex_rollback.py`, explicit [rollback example](../examples/codex-gpt-5.6-rollback.json), 13 checks after promotion | Current source resolution/floors/history; old models must still be accessible |
| Restore Claude policy absence or an exact historical pin | PAT-16 isolated policy rollback/readback and existing regression tests | After promotion, absence selects new defaults; old Sonnet/Opus/Fable are not newly qualified |
| Reinstall an older packaged Foundry | No official downgrade observation claimed | Unqualified/unavailable until an exact compatible trusted source, gates and operational readback exist |

For configuration rollback, pause affected work, compare the saved binding's tracker,
canonical repository/project, marker schema and archive tombstones against the current
state, then restore only compatible operator settings/pins. Re-run selection, doctor
and both route reads. Keep all newer receipts, reviews, telemetry, escalation state
and qualification history. Do not restore an old registry wholesale over newer
bindings/tombstones; never reactivate an archived provider, delete its marker or move
project data. A live cutover is forward-only after target writes: repair forward if
the old snapshot points at the archived source.

The explicit GPT-5.6 example is opt-in and should be merged with existing project
policy only after preserving it. Restoring previous Claude defaults requires an
explicit compatible mapping (Haiku 4.5/legacy-low, Sonnet 5/medium, Opus 5/high,
Fable 5/high) or compatible previous code/profiles; those native versions/access were
not qualified by PAT-16 and may be unavailable. Restoring policy absence alone does
not undo promoted defaults. Missing access is not permission to guess a model.

Do not run an arbitrary old 0.9.0-labelled source against newer receipts: a matching
manifest number does not prove its reader/guard capabilities. A trustworthy exact
source and qualified compatibility are required for any source/package rollback;
there is no verified older public release pin asserted here. Releases below 0.9.0
do not read the repository marker and are forbidden for a Linear-bound repository.
The 1.0.0 preparation certifies configuration rollback at the scopes above, not a
successful official downgrade installation. Extended benchmark PAT-17 and V1.1
PAT-18/PAT-19 remain separate work.
