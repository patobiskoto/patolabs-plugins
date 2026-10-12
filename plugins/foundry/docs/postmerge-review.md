# Advisory postmerge review

Documentation status: added for PAT-200. This implements PAT-ADR-0019; it does not
repair the original review, authorize PAT-195, or replace a delivery gate.

`routing postmerge-review` has three actions: `prepare`, `read`, and `record`
(`tooling/foundry/routing.py`, parser and dispatch; `postmerge_review.py::cli`).
Receipts use `foundry.postmerge-review.v1` and the `Foundry postmerge advisory receipt`
comment prefix (`SCHEMA`, `PREFIX` in `postmerge_review.py`).
The coordinator prepares and records; the reviewer uses the local reader only.
Coordinator operations resolve the checkout project from explicit `--root` and
validate the canonical audit issue binding before issue reads or comment writes
(`ports`; `Tracker.resolve_checkout_project`, `Tracker.validate_issue_binding`).
Default coordinator ports are qualified for Linear only; every other provider is
refused before history reads or comment writes (`ports`). Linear retains all advisory
receipts and ten recent ordinary comments from the already complete bounded comment
connection; `hasNextPage` still refuses history reads (`LinearTracker._to_issue`,
`linear.py::_connection`, `_ISSUE_FIELDS`). Injected fixture ports model complete
history; they do not qualify another native provider.

`prepare` requires a merged, closed PR and exact JSON coordinates with keys
`repository`, `pr`, `base`, `head`, `merge`, `diff_hash`. The SHAs are full lowercase
40-hex strings; the diff hash is SHA-256. It reconstructs `base...head` using
immutable Git objects with replace objects, external diff and textconv disabled,
including when `merge` differs from `head` after squash. Provider coordinates are
read again before the authorization receipt (`historical_diff`, `prepare`, and
`review_queries.py::_git`). Missing objects, coordinate drift and hash divergence
refuse preparation; no fetching or mutable branch substitution occurs there.

Authorization JSON contains exactly `id` (a new lowercase 64-hex identifier),
`issue` (the authorized audit ticket), and `coordinates` (the exact object above).
After a direct human decision, the trusted coordinator supplies this explicit
argument. `prepare` appends one `authorized_attempt` comment containing the scope,
date and attempt before returning a launch descriptor. Its readback rejects an
already consumed authorization or ambiguous duplicate. The receipt is in the
existing tracker, with no new approval store (`prepare`, `receipts`, `append`).
This traces the decision; it does not authenticate the human. A fresh attempt
requires a fresh decision and authorization identifier, even without a verdict.
Tracker read/write/readback is not a cross-host transaction or CAS.

`--history-file` contains `provenance=coordinator_supplied_historical_snapshot`,
`issue`, and `adrs`. Each snapshot contains exactly `id`, `version`, `body`, and
`source`, all nonempty strings. The issue body must contain acceptance criteria;
ADR IDs must be distinct. These are retained historical snapshots supplied by the
coordinator, not independently reconstructed tracker history. Current issue/ADR
text must not be substituted and reported as historical. The snapshots, their
versions, criteria and packet digest are retained in the report (`validate_history`,
`prepare`, `record`). Their historical authenticity is coordinator-supplied.

`prepare` uses the existing reviewer resolver and escalation floor. Codex receives
`agent_type`, `model`, `reasoning_effort`, and `fork_turns=none`; Claude receives a
fresh `Agent` invocation with a bounded `description`, namespaced shipped read-only
profile and no `resume` (`invocation`; `routing_facades.py::claude_invocation_binding`;
`hooks/route_agent.py::route_tool_input`; `claude_profile_trial.py::cache_ttl_parent_prompt`).
The current native schema is not re-qualified by these fixture tests. Execute the exact
returned descriptor once. A prepared plan is not launch evidence.

For `record`, `--evidence-file` has type
`coordinator_transcribed_native_call_return`, the attempt ID and packet hash,
`call={id,tool,arguments,source}`, `child={id,call_id}`,
`return={child_id,call_id,status,report_hash,source}`, and
`observed={model,effort,profile_provenance}` (`native_evidence`). Transcribe the
actual tool call, arguments, new child identity and distinct return from the host;
a plan, boolean or reviewer assertion is insufficient. Foundry checks exact routed
arguments, identities and correlation structurally; it does not directly observe
the host or authenticate a native transcript. Missing observations remain null,
meaning unknown; requested/transmitted routing and observed values stay separate.
No profile provenance or native sandbox qualification is inferred (PAT-196).

`--report-file` has `findings` and nonempty `evidence` lists. Evidence entries have
`source` and `detail`; findings have `summary` and nonempty `evidence` drawn from
those entries (`record`). Its
canonical JSON SHA-256 is the return's `report_hash`. `record` revalidates merged PR
coordinates before appending a distinct `advisory_report`; insufficient evidence or
drift appends `stopped_without_verdict` and consumes the attempt (`record`, `digest`).
A stop retains the submitted evidence digest and, within the bound, an explicitly
unverified coordinator copy (`record`).
Defects are submitted to `foundry:intake`; this grants no correction, merge, closure,
publication or model promotion authority. `AcceptanceProofStore._validated_proof`
explicitly refuses the advisory schema for both merge and criteria projection.

The reviewer command is exactly `routing postmerge-review read --root <ROOT>
--packet-dir <PACKET> --attempt-id <ID> --packet-hash <HASH>`. It checks packet
identity, checkout root and the immutable local diff without tracker access,
network, claims or ledger writes (`read`; `hooks/guard_routed_readonly.py`).
Input JSON is bounded by `review_packet.py::MAX_FILE_BYTES`; the diff by
`MAX_PACKET_BYTES`. These bounds are 256 KiB and 1 MiB respectively.

Fixture-only procedure, run on a disposable Git copy by
`tests/test_postmerge_review.py::test_cli_fixture_procedure`: invoke `prepare` with
`--root --issue --coordinates-file --authorization-file --history-file --output-dir
--host`; execute the exact local `read` command above; transcribe the fixture tool
call/child return; invoke `record` with `--root --issue --attempt-id --evidence-file
--report-file`. This verifies CLI wiring, not a native reviewer launch.
