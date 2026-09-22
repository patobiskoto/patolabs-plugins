# Delivery-proof contract prototype

`foundry.delivery_contract` is an opt-in, read-only prototype for observing one
existing project-native proof after merge. It is deliberately closed to the public
repository `patobiskoto/patolabs-plugins` and GitHub's check-runs REST endpoint. It has
no CLI, hook, command execution, credential parameter, GitHub write, tracker
transition, merge, deployment, smoke trigger, rollback, or remote receipt store.

## Closed pilot contract

`github_check_runs_pilot_contract()` returns a detached copy of the only supported
`foundry-delivery-contract.v1` declaration. The project, contract version, source id,
adapter id/version, provenance, requirement id, and required fact are closed literals:

- project: `patobiskoto/patolabs-plugins`;
- source: `github_check_runs`;
- adapter/version: `github_check_runs_readonly` / `v1`;
- provenance: `github_rest_check_runs`;
- requirement/fact: `github_checks` / `check_runs_success`.

The validator requires exactly that single source and requirement. Arbitrary
identifier-shaped substitutions, including credential-shaped source, adapter, version,
or provenance strings, are rejected. Unknown fields are also rejected, so commands,
hooks, credentials, and deployment instructions cannot enter the contract. The digest
is SHA-256 over its validated canonical JSON. Project identity is a canonical lowercase
GitHub `owner/repository`, then constrained to the literal pilot repository; every
observed revision is an exact 40-character lowercase SHA.

## Concrete read-only observation

`GitHubCheckRunsAdapter` is the sole supported adapter. Its production transport sends
only this unauthenticated request, with a fixed GitHub API host and fixed headers:

```text
GET https://api.github.com/repos/patobiskoto/patolabs-plugins/commits/<sha>/check-runs?per_page=100
```

There is no URL, HTTP method, header, token, credential, or transport argument. Redirects
are not followed, and environment proxy configuration is disabled so proxy credentials
cannot be inherited. The response is size-bounded. Tests patch the module-private GET
function rather than injecting a transport into the adapter, so the production adapter
has no caller-supplied execution or observation seam. Provider payloads, names, URLs,
messages, and errors are never copied into a proof or receipt.

The adapter checks every returned `head_sha` against the requested SHA and maps only the
following closed cases:

- at least one completed `success`, no failure, and optional `neutral`/`skipped` gives
  `success`;
- any known non-completed run gives `pending`;
- an empty response, or only `neutral`/`skipped`, gives `missing-proof`;
- a known failing conclusion gives `failed-proof` when no run is pending;
- a returned different valid `head_sha` gives `wrong-sha`;
- malformed, partial, oversized, invalid-JSON, or future provider shapes give
  `unsupported-schema`;
- HTTP, network, timeout, or fixed-source availability failures give `inaccessible`.

This check-runs observation is not the CI merge gate. In particular, it does not read
the legacy commit-status API and cannot satisfy, bypass, weaken, or retroactively
change the two-source CI semantics of FOUNDRY-ADR-0002. It proves only what this one
declared source reported for the exact SHA.

## Canonical generation and journal

`delivery_receipt_from_adapter`, `claude_delivery_receipt`, and
`codex_delivery_receipt` accept the closed contract, exact SHA, concrete adapter, and a
bounded receipt identifier. They do not accept project/source overrides, caller-built
observations, or an observation timestamp. After the adapter read, the shared core
captures canonical UTC time internally (`YYYY-MM-DDTHH:MM:SSZ`) and derives the
receipt. Tests replace only the private clock. Claude and Codex therefore use the same
canonical core; only their receipt identifiers and independently captured timestamps
may differ.

`DeliveryReceiptJournal.append(...)` likewise accepts no receipt mapping. Its public
path performs a fresh adapter observation, derives the receipt, validates it against
the exact pilot contract and closed receipt schema, then appends it once to a bounded
local JSONL file. Every existing row is revalidated before an append and on every
read; an unsupported field, identity, digest, vocabulary value, provenance shape, or
verdict/outcome combination fails closed. Returned values are detached copies.
When the concrete source was reached but returned no proving run or an unsupported
shape, the receipt retains the adapter's fixed declared provenance; a wholly absent or
invalid proof may omit it, while a provenance mismatch must omit the untrusted value.

The journal is local append-only behavior, not signed or remote durable storage. An
actor that can replace the file with another fully schema-valid row is outside the
prototype's trust boundary; the receipt makes no cryptographic authenticity claim.
Filesystem protection remains the operator's responsibility.

The receipt binds the literal project, requested SHA, contract schema/version/digest,
adapter/version, source/provenance, internally captured observation time, and closed
outcome. A required null/absent fact yields `unavailable`, never true or false. The
prototype remains distinct from merge, tracker `done`, routing, model calls, and every
delivery authority governed by FOUNDRY-ADR-0011, FOUNDRY-ADR-0002,
FOUNDRY-ADR-0005, and FOUNDRY-ADR-0008.
