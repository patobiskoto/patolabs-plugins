# Foundry 1.0.0 — release candidate, 2026-10-03

**Prepared, not yet publicly published or verified installed from the public Git marketplace.** Both host manifests declare
`1.0.0`. Ship-iOS retains its independent `0.3.0` version: this release changes no
bridge, skill, template or App Store API. The versionless marketplace catalogues
continue to point at `./plugins/foundry` and `./plugins/ship-ios`.

V1 delivers the bounded idea-to-merge journey on YouTrack, Linear and the qualified
private personal GitHub Projects variant, on Claude Code and Codex. The release
inherits the accepted PAT-61 qualification and delivered PAT-15/PAT-16 model slices.
It does not certify additional provider variants, renew any native-call budget or
establish economic/quality superiority. Setup, upgrade and rollback instructions are
in [migration-1.0.0.md](migration-1.0.0.md).

## Qualification and source identity

PAT-61 was delivered by [PR #72](https://github.com/patobiskoto/patolabs-plugins/pull/72):
reviewed head `307cc420dd6088e64be078a92f6bd2c00333e4fb`, structured review proof
`eaec5ab5bb4151a1a3a3982d2a58052e1a615740ea6a90facb04fb2ac3891932`,
three successful check-runs and empty legacy commit statuses at that exact head,
merged baseline `16cdaa0a3f172a1549fa9fde3f82995c23f7878c`; tracker readback
`done`, `accepted`, 9/9 AC. These are PAT-61 coordinates, not PAT-62 CI or a 1.0.0
installation claim. PAT-61 includes prerequisite readbacks and PAT-42's delivered
binding-error guard; closing the parent Epic cannot substitute for a missing proof.

The retained [final report](qualification/pat-61-final-qualification-report.md),
[matrix](qualification/pat-61-six-configuration-v1.json),
[integrated observation](qualification/pat-61-post-integration-observation.json)
and [native ADR evolution observation](qualification/pat-61-native-adr-evolution-observation.json)
keep their original producer refs, source package **0.9.0**, failure history and
pre-review statuses. Their historical wording is intentional. The accepted delivery
above is a later lifecycle observation; it does not rewrite those raw observations.

## Six qualified configurations

B = checkout binding; A = ADR create/evolve; G = graph/grooming; D = gated delivery;
C = human-verdict Epic closure; R = release scope/changelog/Ship-iOS bridge.
`passed` refers to PAT-61's composite bounded source journey and its original
host/operator attribution, not six new autonomous replays or six package installs.

| Tracker variant | Claude Code | Codex | Qualified scope |
| --- | --- | --- | --- |
| YouTrack | B/A/G/D/C/R passed | B/A/G/D/C/R passed | Explicit native project id/key and canonical repository binding; qualified delivery receipts |
| Linear | B/A/G/D/C/R passed | B/A/G/D/C/R passed | Explicit repository/team/product/state/type/release IDs; native versioned ADR Documents |
| GitHub Projects (`ghprojects`) | B/A/G/D/C/R passed | B/A/G/D/C/R passed | Private personal Project V2 plus linked private repository with the same User owner; qualified field catalogues and ADR Issue supports |

GitHub public repositories/projects, organization ownership, GitHub Apps and
Discussions remain unqualified. The recovery Project #10 is qualified; frozen
Project #9 is not. A repository uses GitHub Issues only when it explicitly elects
the GitHub Projects tracker. **patolabs-plugins remains Linear/PAT**, with GitHub
as code-host and its former YouTrack project a read-only archive.

The three adapters expose [the V1 core contract](tracker-contract.md). Grooming
changes only targeted fields/body/parent and preserves foreign properties where
the provider permits it. Without provider CAS, read/write/readback is bounded
detection: an external write in S1→S2 can be overwritten and hidden by the final
read. Epic closure binds the human verdict and exact complete terminal graph to an
audit, with recovery and full readback; it is non-atomic on these three providers.
The optional DevHub adapter retains its separate contract outside this V1 matrix.

Linear's proof-bound append-only projection remains delivery/AC authority, with
targeted native state synchronization; native AC checkbox replacement is refused.
GitHub ADR Issues carry `foundry:adr` and are excluded from delivery Kanban/backlog;
version comments are integrity-checked, not provider-immutable. Native Done, a PR
link or checked boxes alone never create an accepted delivery receipt. Historical
YouTrack issues without qualified receipts remain `unavailable`, with no fabricated
backfill. See [Linear](linear-tracker.md), [GitHub Projects](ghprojects-tracker.md),
and [release scope](release-scope.md) for exact contracts and limits.

## Foundry ↔ Ship-iOS matrix

| Foundry | Ship-iOS | Status on both hosts |
| --- | --- | --- |
| PAT-60/PAT-61 source 0.9.0 at recorded producer refs | 0.3.0 | Qualified historical/integration bridge evidence retained |
| Candidate 1.0.0 derived from accepted PAT-61 | 0.3.0 | Compatible V1 capabilities; official installed pair readback remains pending |
| Earlier/unobserved package pair | Any | No additional compatibility claim |

Compatibility is checked by `ship-ios.foundry-changelog-bridge.v1`, using
`registry selection --require-v1` and `query changelog` with
`foundry.release-scope.v1` from the application checkout. Source version metadata
alone cannot replace those probes. The bridge preserves accepted, deviated,
unfinished and unavailable classes. A malformed/broken explicit CLI, provider error
or binding failure stops; exit 3 alone means no compatible Foundry discovered.
Standalone mode is an explicit `--standalone` choice using a reviewed changelog file.

The shared PAT-61 Apple observation proves the recorded Xcode Cloud/TestFlight
internal build and the maintainer's real iPhone validation for the synthetic app.
It is not six App Store publications or authority to submit another app. Ship-iOS
retains its real-device beta and explicit submission gates.

## Retained model profiles and client limits

| Tier / role | Claude policy → Agent alias → transmitted ID | Effort | Codex model / effort |
| --- | --- | --- | --- |
| economy / Lupin | `haiku-4.5` → `haiku` → `claude-haiku-4-5` (observed dated ID `claude-haiku-4-5-20251001`) | null, not applicable; none transmitted | `gpt-6-luna` / low |
| balanced / Eiffel, coordinator guidance | `sonnet-5.5` → `sonnet` → `claude-sonnet-5-5` | medium | `gpt-6.1-sol` / medium |
| frontier / Maigret minimum | `opus-5.5` → `opus` → `claude-opus-5-5` | high | `gpt-6.1-sol` / high |
| apex / Vauban minimum | `opus-5.5` → `opus` → `claude-opus-5-5` | high | `gpt-6.1-sol` / max |

The PAT-16 contract records a **Claude Code minimum 2.1.284 for the complete
retained profile set** (Sonnet 5.5); Opus 5.5 individually requires 2.1.280.
The client actually tested was **2.1.285**, `claude.ai` / firstParty / Pro.
These are the frozen contract's declared minimums and observed client, not tests
on every intermediate client or other provider. Fable 5/5.1 requires usage credits
on that observed Pro account and was excluded. No paid/API qualification was authorized.

[PAT-16 promotion](qualification/pat-16-claude-default-promotion.md) preserves
seven parents/six children, failures, bounded complement and explicit human approval.
Exact model metadata and requested/transmitted efforts are separated from effective
runtime effort: values the host did not expose remain `unknown`. The PAT-61
integration parent observed Sonnet 5.5; that source read is not a fresh Eiffel/Maigret
cycle. The [PAT-15 report](qualification/pat-15-codex-qualification-v1.md) retains
four completed Codex smokes with observed model/effort; Standard/Fast, isolated quota
and cost remain unavailable. The later PAT-61 inventory recorded embedded Codex
0.159.2 (app 26.928.31416/build 12553) versus PATH 0.155.1; it does not retrospectively
assign versions to older smokes that did not capture them.

Project pins and overrides are preserved byte-for-byte; historical Sonnet 5,
Opus 5, Fable 5 and GPT-5.6 mappings are not silently rewritten or newly qualified.
Unavailable account/model access fails through the declared resolver, never an
invented cross-generation fallback. `ultra` remains inadmissible for Codex delegation;
Haiku remains effort-free. Main conversations and personal restrictions are not
reconfigured. See [model routing](model-routing.md) and the
[migration contract](model-migration-2026-10.md).

## Prepublication official-manager observation

The [PAT-62 install observation](qualification/pat-62-install-observation.json)
records official-manager installation/replacement and zero-model-turn loader reads
on this machine. Candidate source `c9089a13be5a69bd1ffb0feb5fc2472c2b00c445`
was installed clean and upgraded from the compatible exact baseline `16cdaa0a…`,
on Claude Code 2.1.285 and PATH Codex 0.155.1. Both manifests at the actually
resolved roots reported 1.0.0; installed CLI selection, doctor and both route
reads passed. This is a separate local qualification marketplace, not publication
or a public Git installation. Claude resolves its official local source directly;
Codex resolves its installed cache. The original `patolabs` installations remain
0.9.0 and the temporary package/marketplace were removed through the managers.

A fresh immutable archive of exact baseline `16cdaa0a…` was reinstalled as 0.9.0
and observed through both loaders and probes. Configuration, registry and marker
hashes were unchanged. This tests compatible package rollback, not restoration
of older model defaults or an earlier public 0.9 release; receipts and tracker data
were not modified. Diagnostics and the local catalogue limitations are preserved
in the observation. No model/API turn or renewed historical qualification budget
was used. Canonical Git marketplace installation/upgrade, public source/ref and
desktop loaded-version readbacks remain open postpublication checks.

## Release phases and evidence still required

1. **Before merge:** candidate manifests/docs, proportional existing package/process
   tests and PAT-61 source evidence; independent review on the exact PAT-62 diff,
   then exact-head check-runs plus legacy statuses. PR/merge use only
   `foundry:open-pr` / `foundry:merge-pr`. This page does not supply that fresh review
   or CI result.
2. **After the gated merge:** publish from the exact merged SHA under the release
   contract. Record package versions, source ref/tag and publication readback;
   do not label an unpushed tag or branch a published release.
3. **After publication:** clean installation and 0.9.0 upgrade via each official
   manager, restart/reload, actual client binary/version and loaded manifest/path/ref
   readback, installed CLI doctor/binding and bridge probes. Record each host and
   route separately, with any unavailable observation preserved.
4. **Final report:** package/ref/SHA, the six source configurations and attribution,
   four host installation/upgrade outcomes, actual rollback scope, optional limits
   and remaining work. PAT-62 publication/installation AC remain open until those
   effects and readbacks exist. A pre-merge source test cannot pass a post-install AC.

The current merge gate requires every AC covered unless an explicit audited human
incomplete-AC override is provided. Merely documenting the postpublication phase does
not grant that override. If PAT-62's current AC cannot pass before its source is
published, the coordinator must obtain the contract's explicit release-phase
arbitration before merge; no reviewer may turn pending installation into a PASS.

PAT-17 (extended economic/model benchmark), PAT-18 and PAT-19 are outside V1 and
remain V1.1 work. No cockpit, new tracker, general data migration, local-model
promotion or App Store submission is part of this package release.

## Documentation status (AGENTS.md#R5)

| Touched public artifact | Status / pointer |
| --- | --- |
| Both Foundry manifest versions/descriptions and Claude provider/model/failure-policy descriptions | Updated here and in migration-1.0.0.md; schema/source pointers unchanged |
| Root/Foundry README setup, supported variants, routing guidance | Updated links to these release/migration pages; current model table above |
| Dated Foundry changelog | Updated 1.0.0 candidate entry; historical 0.9.0 sections and qualification producers preserved |
| Ship-iOS 0.3.0 compatibility announcement | Updated README/changelog compatibility note and matrix here; no executable package change or new version |

No CLI, runtime constant, hook or skill implementation is changed by PAT-62 package
preparation. The future mechanical documentation-status gate is not claimed shipped.
