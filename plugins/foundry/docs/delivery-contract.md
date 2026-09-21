# Delivery-proof contract prototype

`foundry.delivery_contract` is an opt-in, read-only prototype for representing a
project's delivery evidence after merge. It has no CLI command, provider client,
credential, hook, persistence, tracker transition, merge, deployment, or rollback
operation. It cannot execute or request work from a provider.

The closed `foundry-delivery-contract.v1` schema contains only a version, named
read-only source declarations (`id`, adapter and adapter version, provenance), and
requirements that name a source plus required facts. Unknown fields are rejected, so
commands, hooks, credentials, and deployment instructions cannot enter the contract.
Proof facts are only nullable booleans, so arbitrary provider payloads cannot enter a
receipt. Its digest is SHA-256 over canonical JSON.

The caller supplies an already-read `foundry-delivery-proof.v1` observation. The
resulting `foundry-delivery-receipt.v1` binds the project, exact 40-character SHA,
contract version and digest, adapter/version, proof-source provenance, and observation
time. A new observation produces a new caller-supplied receipt id and timestamp.

`verified` is possible only when every required proof is successful and every required
fact is non-null. Missing proof, pending proof, inaccessible source, unsupported proof
schema, wrong project/SHA, and unavailable required facts remain separate outcomes;
none is silently converted to true or false. Claude and Codex facades call the same
canonical core, so their content differs only in caller-provided receipt identifiers
and observation timestamps.

This is distinct from the existing CI gate and does not change merge or tracker-done
semantics (FOUNDRY-ADR-0011 and FOUNDRY-ADR-0002).
