# Advisory postmerge review

Documentation status: added for PAT-200. This implements PAT-ADR-0019; it does not
repair the original review, authorize PAT-195, or replace a delivery gate.

`routing postmerge-review` has three actions: `prepare`, `read`, and `record`
(`tooling/foundry/routing.py`, parser and dispatch; `postmerge_review.py::cli`).
Receipts use `foundry.postmerge-review.v1` and the `Foundry postmerge advisory receipt`
comment prefix (`SCHEMA`, `PREFIX` in the neutral `advisory_schema.py`).
The coordinator prepares and records; the reviewer uses the local reader only.
Coordinator operations resolve the checkout project from explicit `--root` and
validate the canonical audit issue binding before issue reads (`ports`;
`Tracker.resolve_checkout_project`, `Tracker.validate_issue_binding`).
Comment writes use `write.add_comment(cwd=root)` and its normal repository, project
and issue mutation binding checks before the adapter receives `project=`
(`append`; `write.py::{mutation_project,issue_binding,add_comment}`).
Default coordinator ports are qualified for Linear only; every other provider is
refused before history reads or comment writes (`ports`). Linear retains all advisory
receipts and ten recent ordinary comments from the already complete bounded comment
connection; `hasNextPage` still refuses history reads (`LinearTracker._to_issue`,
`linear.py::_connection`, `_ISSUE_FIELDS`). Existing response and connection bounds
are the 8 MiB `_MAX_RESPONSE_BYTES` transport cap and `comments(first: 100)`
query with incomplete connections refused by `_connection`; they do not establish
a native maximum comment size (`linear.py`). That native maximum remains unknown.
One authorized 430-byte comment probe was observed identical on creation, readback
and independent reread; it does not establish larger receipt fidelity or a size limit. Injected fixture ports model complete
history; they do not qualify another native provider.

`prepare` requires a merged, closed PR and exact JSON coordinates with keys
`repository`, `pr`, `base`, `head`, `merge`, `diff_hash`. The SHAs are full lowercase
40-hex strings; the diff hash is SHA-256. It reconstructs `base...head` using
immutable Git objects with replace objects, external diff, textconv, color and
rename detection disabled, explicit prefixes and short submodule output,
including when `merge` differs from `head` after squash. Provider coordinates are
read again before the authorization receipt (`historical_diff`, `prepare`, and
`advisory_git`). Missing objects, coordinate drift and hash divergence
refuse preparation; `--no-lazy-fetch` and `GIT_NO_LAZY_FETCH=1` disable lazy fetching
(`advisory_git`). This advisory hash uses its own fixed diff configuration for
algorithm, context, indent heuristic, inter-hunk context and path quoting; it is not
the premerge claim hash contract (`advisory_git`; `routing.py::git_diff`).

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
`source`, all nonempty strings. Public IDs match the issue/ADR identifier pattern,
and versions are single tokens of 1–160 letters, digits, dot, underscore, colon or
hyphen, starting with a letter or digit (`validate_history`). The issue body must
contain acceptance criteria; ADR IDs must be distinct. These are retained historical snapshots supplied by the
coordinator, not independently reconstructed tracker history. Current issue/ADR
text must not be substituted and reported as historical. Full snapshots and criteria
stay in the existing private packet; the public
receipt binds their digest and issue/ADR identities and versions (`history_identity`,
`public_attempt`, `prepare`). Their historical authenticity is coordinator-supplied.

`prepare` uses the existing reviewer resolver and escalation floor. Codex receives
`agent_type`, `model`, `reasoning_effort`, and `fork_turns=none`; Claude receives a
logical `foundry:maigret` `Agent` invocation without `resume`, routed through
`route_tool_input` with the role contract, task bounds, turn cap and host checks
(`invocation`; `hooks/route_agent.py::route_tool_input`). The private descriptor
contains logical `launch_arguments` and the exact expected rewritten `arguments`;
only the former are launched, and the actual rewritten call must match the latter
(`invocation`, `native_evidence`). The Claude route comes from the facade callback,
including its host overrides and escalation floor (`invocation`; `claude_route_plan`).
The current native schema is not re-qualified by these fixture tests. Execute the exact
returned launch descriptor once. A prepared plan is not launch evidence.
For Claude, `CLAUDE_PLUGIN_ROOT` selects the equipped plugin copy, defaulting to the
source copy; code and role bytes checked by `advisory_plugin` must agree before
preparation. Its absolute reader matches that copy's guard (`advisory_plugin`,
`prepare`; `guard_routed_readonly.py::allows_verified_diff`). An older installed
plugin is not qualified by checkout tests and requires a separately authorized
installation; preparation does not copy or modify it. Codex uses the emitting
source copy's absolute reader without a Claude environment variable (`prepare`).
All 43 `agents/routed-readonly-*.md` profiles carry the same advisory Bash policy
(`claude_pin_profile_text`; profile inventory).

For `record`, `--packet-dir` must bind the private invocation to its public receipt
and packet digest (`record`, `read`, `public_attempt`). `--evidence-file` has type
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
A stop publishes only the submitted evidence digest and hashed child identity;
for a verified packet, its bounded submission is written privately before native
validation (`record`). A divergent or unreadable packet can stop without archiving
the new submission (`record`, `read`).
Public attempt fields are schema/type, issue, attempt, authorization, timestamp,
immutable coordinates, host/tool, packet/history/argument/route hashes and historical
identities/versions; reports add report/native-evidence/child hashes and control
statuses (`public_attempt`, `record`). They bind authority, immutable inputs, routing,
correlation and outcome without publishing prompts, source paths or historical bodies.
Full descriptors, snapshots, native evidence and reports are private packet files,
not an approval store; authority remains the tracker receipt (`prepare`, `record`).
Defects are submitted to `foundry:intake`; this grants no correction, merge, closure,
publication or model promotion authority. `AcceptanceProofStore._validated_proof`
explicitly refuses the advisory schema for both merge and criteria projection.

The reviewer command is exactly `routing postmerge-review read --root <ROOT>
--packet-dir <PACKET> --attempt-id <ID> --packet-hash <HASH>`. It checks packet
identity, checkout root and the immutable local diff without tracker access,
network, claims or ledger writes (`read`; `hooks/guard_routed_readonly.py`).
The diff and supplied snapshots are historical, while project contracts, skills and
surrounding checkout context are current and must be reported as such; advisory
mode provides only `read`, not claimed `search-review` or `list-review`
(`prepare`; `routing_facades.py::CODEX_ROLES`; `review_queries.py::query_review`).
Input JSON is bounded by `review_packet.py::MAX_FILE_BYTES`; the diff by
`MAX_PACKET_BYTES`. These bounds are 256 KiB and 1 MiB respectively.

CLI failures expose a controlled reason, `authorization_consumed=no|yes|unknown`
and `publication=no|yes|unknown`, never arbitrary exception text (`require`,
`PUBLICATION`, `WRITE_EFFECT`; `routing.py` dispatch). Prewrite refusal has no effect;
verified authorization publication consumes the attempt, while an interrupted or
failed write without verified readback leaves publication unknown (`append`).
A failed terminal write does not unconsume an already verified authorization.

Fixture-only procedure, run on a disposable Git copy by
`tests/test_postmerge_review.py::test_cli_fixture_procedure`: invoke `prepare` with
`--root --issue --coordinates-file --authorization-file --history-file --output-dir
--host`; execute the exact local `read` command above; transcribe the fixture tool
call/child return; invoke `record` with `--root --issue --attempt-id --evidence-file
--report-file --packet-dir`. This verifies CLI wiring, not a native reviewer launch.
