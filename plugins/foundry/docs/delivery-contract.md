# Delivery-proof contract prototype

`foundry.delivery_contract` is an opt-in, read-only prototype for representing a
project's delivery evidence after merge. It has no CLI command, provider client,
credential, hook, remote persistence, tracker transition, merge, deployment, or rollback
operation. It cannot execute or request work from a provider.

The closed `foundry-delivery-contract.v1` schema contains only a version, named
read-only source declarations (`id`, adapter and adapter version, provenance), and
requirements that name a source plus required facts. Unknown fields are rejected, so
commands, hooks, credentials, and deployment instructions cannot enter the contract.
Proof facts are only nullable booleans, so arbitrary provider payloads cannot enter a
receipt. Its digest is SHA-256 over canonical JSON.

`ReadOnlyProofAdapter` is the one-source adapter seam. Its sole operation is
`read_proof(project, sha)`; before it is called, Foundry binds its source id, adapter,
adapter version, and provenance to one declared contract source. The supported
`delivery_receipt_from_adapter`, `claude_delivery_receipt`, and
`codex_delivery_receipt` surfaces always read and evaluate exactly that contract-bound
source; callers cannot supply observations to them. The pure observation evaluator is
private and exists only as a deterministic test seam. Every proof provenance is also
compared with the declared source provenance and a mismatch is refused. A proof for
another project is reported as `cross-project`; a proof for a different SHA is
`wrong-sha`.

An adapter reports expected source unavailability only by raising the bounded
`ProofSourceUnavailable` exception. Foundry converts that declared path to an
`inaccessible` proof whose identity and provenance come from the contract. The
provider exception text is discarded and never persisted. Other exceptions are not
masked as unavailability and remain adapter programming or integration errors.

The resulting `foundry-delivery-receipt.v1` binds the project, exact 40-character SHA,
contract version and digest, adapter/version, proof-source provenance, and a canonical
UTC observation time (`YYYY-MM-DDTHH:MM:SSZ`). Arbitrary text is rejected rather than
being persisted as an observation instant.

`DeliveryReceiptJournal` optionally records generated receipts in a bounded local
append-only JSONL file. It locks appends, rejects duplicate receipt ids, and returns
detached canonical data so callers cannot mutate an already-recorded receipt. Before
append and on every read, it requires non-empty uniquely named proof rows, validates
the closed proof-outcome vocabulary and provenance shape, and recomputes the receipt
verdict from those outcomes. A mismatched positive or negative verdict is rejected.
The journal is local-only and is explicitly not remote durable storage; it holds only
the closed receipt schema, never provider raw output, commands, credentials, or
secrets.

`verified` is possible only when every required proof is successful and every required
fact is non-null. Missing proof, pending proof, inaccessible source, unsupported proof
schema, provenance mismatch, cross-project/wrong-SHA proof, and unavailable required
facts remain separate outcomes;
none is silently converted to true or false. Claude and Codex facades call the same
source-bound canonical core, so their content differs only in caller-provided receipt
identifiers and observation timestamps.

This is distinct from the existing CI gate and does not change merge or tracker-done
semantics (FOUNDRY-ADR-0011 and FOUNDRY-ADR-0002).
