# Advisory postmerge review

Documentation status: updated for PAT-202: CLI inputs, receipt vocabulary,
publication bound, native evidence, plugin selection, guarded paths and restitution;
follow-up corrections cover concrete execution paths, terminal stops and reader diagnostics.
PAT-ADR-0020 is proposed and will replace PAT-ADR-0019 at this issue's merge;
PAT-ADR-0019 remains accepted until then. The earlier implementation conforms to
PAT-ADR-0019; this delivery is a replacement (PAT-ADR-0020, Context and Decision).

`routing postmerge-review` has three actions: `prepare`, `read`, and `record`
(`tooling/foundry/routing.py`, parser and dispatch; `postmerge_review.py::cli`).
Authorization receipts retain `foundry.postmerge-review.v1`; new stops, reports
and confirmations use `foundry.postmerge-review.v2`, while intact legacy v1 stops
remain readable without rewriting (`SCHEMA`, `TERMINAL_SCHEMA`, `record`, `receipts`). Terminal comments use the existing prefix followed by
`terminal <attempt-id>` and a newline (`TERMINAL_PREFIX`; `comment_text`); retention
of malformed terminals is deduced from the prefix check (`LinearTracker._to_issue`).
Historical v1 reports remain historical and are not requalified by restitution
(`restituted_reports`).
The coordinator prepares and records; the reviewer uses the local reader only (`cli`, `read`).
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
The authorized synthetic probe on 2026-10-14 was observed byte-identical on write
and readback for 12,294 UTF-8 bytes including its prefix (PAT-202 maintainer decision
and probe result, SHA-256 `1e4d6dc62d25595d90b48a94b1f60571c07ed26cf198af0773df94f89fbb6d82`).
Every full serialized comment, including its prefix, is bounded by
`MAX_COMMENT_BYTES=12294` before mutation (`bounded_comment`, `append`).
The serializer is `json.dumps(sort_keys=True, ensure_ascii=False)` with default
separators and no final newline (`comment_text`); private-file bounds do not
qualify publication. The native maximum remains unknown; this dated observation
does not qualify every content or size (PAT-ADR-0020, Qualification).
Injected fixture ports model complete history; they do not qualify another native provider.

`prepare` requires a merged, closed PR and exact JSON coordinates with keys
`repository`, `pr`, `base`, `head`, `merge`, `diff_hash`. The SHAs are full lowercase
40-hex strings; the diff hash is SHA-256. It reconstructs `base...head` using
immutable Git objects with replace objects, external diff, textconv, color and
rename detection disabled, explicit prefixes and short submodule output,
including when `merge` differs from `head` after squash. Provider coordinates are
read again before the authorization receipt (`historical_diff`, `prepare`, and
`advisory_git`). Missing objects, coordinate drift and hash divergence
refuse preparation (`historical_diff`). Git 2.45 or later is required and older or
unreadable versions are refused before authorization (`advisory_git`); the option's
introduction is recorded in the [Git 2.45 release notes](https://github.com/git/git/blob/v2.45.0/Documentation/RelNotes/2.45.0.txt).
`--no-lazy-fetch` and `GIT_NO_LAZY_FETCH=1` disable lazy fetching (`advisory_git`). This advisory hash uses its own fixed diff configuration for
algorithm, context, indent heuristic, inter-hunk context and path quoting; it is not
the premerge claim hash contract (`advisory_git`; `routing.py::git_diff`).
Ordering is reset to the empty order file and blank context retains its prefix
(`advisory_git`: `diff.orderFile`, `diff.suppressBlankEmpty=false`).
Any effective `diff` attribute on a changed historical path, including a driver,
set or unset value, refuses preparation and reading in the current checkout
(`validate_diff_attributes`); the advisory path does not rewrite attributes.
The influence of both settings and attribute drivers was observed in disposable
fixtures (`test_current_diff_config_influence_exercised_then_neutralized`,
`test_current_diff_attributes_influence_exercised_and_refused`).

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

`prepare` uses the shared reviewer façade and escalation floor (`invocation`).
Codex uses `codex_spawn_plan(advisory=True, _observe=False)`: packet validation,
profile override detection and policy resolution remain shared, while no premerge
claim is supplied or created (`routing_facades.py::codex_spawn_plan`).
The coordinator supplies known effective-profile override signals through
`--profile-model-active` and `--profile-effort-active`; these Codex-only inputs
are passed to the façade (`routing.py` parser; `postmerge_review.py::cli`).
An absent signal is not an observation of the effective profile; provenance and
sandbox qualification remain unknown (PAT-196; `detect_codex_overrides`).

The private `invocation.json` contains `tool`, `launch_arguments`, expected
rewritten `arguments`, their separate hashes, the expected route, and Claude
`hook_context` with its hash (`prepare`). For Codex, `tool=spawn_agent` and
`launch_arguments` contain `agent_type`, `model`, `reasoning_effort`,
`fork_turns=none`, `task_name` and the bounded `message` (`codex_spawn_plan`).
For Claude, `tool=Agent` and `launch_arguments` contain the logical
`subagent_type=foundry:maigret`, description and prompt; no `resume` is emitted
(`invocation`). Launch only `launch_arguments`, once, in a new native child;
the actual Claude hook controls rewriting (`invocation`; `route_agent.py::route_tool_input`).
The calculated rewritten arguments and context are preparation, not observations.

Claude requires explicit `CLAUDE_PLUGIN_ROOT` before consumption; no source-copy
fallback is inferred (`advisory_plugin`). The selected copy's implementation and
role bytes must equal the emitting source for the files listed in `advisory_plugin`,
including `routing.py` and `effort_policy.py` (`advisory_plugin`).
This explicit selection does not prove which plugin or profile the host loaded;
an incompatible selection refuses before authorization (`advisory_plugin`).
Codex uses the source copy's reader (`advisory_plugin`, `prepare`). Root and packet
paths must pass the exact guard's `is_safe_absolute_root` before consumption;
control characters and shell metacharacters refuse preparation (`prepare`;
`guard_routed_readonly.py::_SHELL_SYNTAX`). The guard rejects CR and LF anywhere
in every Bash reader, including `read-review`, `list-review` and `search-review`
(`guard_routed_readonly.py::allows_verified_diff`).

For `record`, `--packet-dir` must bind the private invocation to its public receipt
and packet digest (`record`, `read`, `public_attempt`). `--evidence-file` has type
`coordinator_transcribed_native_call_return`, the attempt ID and packet hash,
`call={id,tool,arguments,source}`, `child={id,call_id}`,
`return={child_id,call_id,status,report_hash,source}`, and
`observed={model,effort,profile_provenance}` (`native_evidence`). Claude additionally
requires `hook={call_id,source,context}` naming the same logical call and the
observed Foundry Claude route context (`native_evidence`). Transcribe the actual
logical call, hook context, new child identity and distinct return from the host;
a plan, boolean or reviewer assertion is insufficient (PAT-ADR-0020, Independence).
The equality is the closed set `CLAUDE_STABLE_FIELDS`: `role`, `selected_tier`,
`model`, `effort_parameters.requested`, `effort_parameters.transmitted`,
`transmitted_launch.profile`, `transmitted_launch.transmitted_model`, `gate_floor`,
`gate_effort_floor` (`stable_hook_route`; ADR source `route_agent.py::_warning_context`).
Missing or different stable fields and insufficient correlation refuse recording
(`native_evidence`). Other context fields, including escalation, host version,
warnings and availability, are retained privately without equality checks
(`record`; `stable_hook_route`). Visibility of the additional hook context was
reported on three Claude launches on 2026-10-12 and 2026-10-13; `updatedInput`
visibility was reported absent (PAT-ADR-0020, Context). This is a dated observation,
not a durable host guarantee. A transmitted profile does not prove loading, and
native model/effort remain unknown when not exposed (PAT-ADR-0020, Independence).
Foundry checks transcription structurally, not transcript authenticity
(`native_evidence`); missing observations remain null, meaning unknown.
No native sandbox qualification is inferred (PAT-196).

`--report-file` has `findings` and nonempty `evidence` lists. Evidence entries have
`source` and `detail`; findings have `summary` and nonempty `evidence` drawn from
those entries (`record`). Its
canonical JSON SHA-256 is the return's `report_hash` (`digest`, `native_evidence`).
`record` revalidates merged coordinates, validates each finding's evidence, and
archives the bounded submission privately (`record`). An unreadable or malformed
existing `submission.json` produces a controlled stop without replacing it (`record`). Unreadable packet files,
invalid JSON and non-object invocation data produce a controlled stop after the
consumed attempt; packet validation can stop before submission archiving
(`record`, `read`). Historical Git process I/O failures produce the nonsecret
`historical Git process unavailable` reason (`advisory_git`). Other evidence or coordinate refusals also append a
`stopped_without_verdict` digest receipt (`record`).

The v2 terminal report retains the complete publishable report and all its
structured evidence, immutable coordinates, historical identities and launch
metadata; raw arguments and sources, prompt and snapshot bodies remain private
(`record`; `public_attempt`). Strings containing resolved execution paths (home,
repository, output/packet directory, temporary directory and selected plugin),
`file:` or UNC paths, raw task prompts and duplicate snapshot bodies refuse
publication; the private execution-path list is bound by its public digest
(`prepare`, `public_attempt`, `read`, `publishable_report`). Other-machine paths
that do not contain these concrete values, slash prose and repository references
remain permitted; prompt and snapshot duplication uses decoded strings
(`publishable_report`). An overbound full comment refuses before
its write (`publishable_report`, `bounded_comment`). No finding is silently removed
or truncated (`record`). Launch metadata includes correlated call/child hashes,
argument hash, tool, return status, report hash, stable hook route and explicit
observations (`record`: `launch_evidence`).

Report deposition and verdict confirmation are separate writes (`record`).
After exact report-text readback, `advisory_confirmation` binds the attempt,
report hash, canonical receipt hash and full comment hash (`append`, `record`).
A missing, altered or ambiguous confirmation produces no usable verdict
(`restituted_reports`). If report or confirmation readback differs, recording stops
without retry or compensating tracker write; `publication-readback.json` retains
the sent and observed texts privately (`append`, `retain_publication_diff`).
An adapter exception after mutation also triggers one read-only observation and
attempted private retention before the original exception is re-raised; an unavailable
observation is recorded as null and a retention I/O failure preserves the original
exception (`append`, `retain_publication_diff`). Authorization remains
consumed; the authorization receipt and absent valid confirmation carry the
without-verdict state (PAT-ADR-0020, Altered terminal receipt).
V2 terminal envelopes permit isolation even when their JSON is malformed; an old
authorization still cannot be reused and duplicate authorization stays refused
(`receipts`, `prepare`, `record`). Malformed authorization identity or scope remains
fatal; terminal isolation never relaxes that check (`receipts`; PAT-ADR-0020:
“Cette isolation ne permet ni de réutiliser l’ancienne autorisation ni d’ignorer une ambiguïté sur sa consommation.”). Malformed v1
receipts remain fatal for the ticket (`receipts`); the authorization ambiguity is
deduced from their shared envelope, which cannot identify a terminal independently
of its JSON (`SCHEMA`, `PREFIX`, `receipts`). V2 terminal envelopes have a distinct
prefix and can be isolated independently (`TERMINAL_PREFIX`, `receipts`). Unidentifiable
v2 terminal envelopes block recording existing attempts, while previously confirmed
reports remain restorable (`record`, `restituted_reports`); a newly authorized attempt records
the preexisting isolated hashes and remains bound to its own unique authorization
(`unbound_terminals`, `prepare`, `record`).
A distinct authorization can start another
attempt on the same ticket (`prepare`).

`restituted_reports(tracker, issue_id)` recovers only confirmed v2 reports from
tracker comments, checks their report and confirmation hashes and unique
consumption, and needs no private packet (`restituted_reports`). It reconstructs
reports and structured evidence, not raw native sources or snapshot bodies
(PAT-ADR-0020, Conservation). Defects go through `foundry:intake`; the advisory
path grants no delivery authority (PAT-ADR-0020, Qualification).
`AcceptanceProofStore._validate_proof` refuses both advisory schemas for merge
and criteria projection (`routing.py`; `advisory_schema.py`).

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
`PUBLICATION`, `WRITE_EFFECT`; `routing.py` dispatch). For `prepare`, prewrite refusal
reports consumption `no`, a consumed identifier
reports `yes`, and an interrupted authorization write reports `unknown`
(`prepare`, `append`). For `record`, a uniquely located authorization reports `yes`
even after terminal failure; absent attempts report `no`, and unavailable history
reports `unknown` (`record`); unreadable evidence or report inputs also report
`unknown` before any authorization read (`cli`). The local `read` action reports consumption `unknown`.
This uncertainty is deduced from its lack of tracker authority (`read`); it reports publication `no` and writes
nothing, including after a consumed preparation (`routing.py` dispatch; `read`).
Publication describes the current operation's writes: `no` before mutation, `yes`
after faithful readback, `unknown` after a write without faithful readback (`append`).
A failed terminal write does not unconsume an already verified authorization.

Fixture-only procedure, run on a disposable Git copy by
`tests/test_postmerge_review.py::test_cli_fixture_procedure`: invoke `prepare` with
`--root --issue --coordinates-file --authorization-file --history-file --output-dir
--host`; execute the exact local `read` command above; transcribe the fixture tool
call/child return; invoke `record` with `--root --issue --attempt-id --evidence-file
--report-file --packet-dir`. This verifies CLI wiring, not a native reviewer launch.

Exact advisory `diff_hash` procedure, run on a disposable checkout by
`test_cli_fixture_procedure`: construct exact coordinate JSON, call
`advisory_git(root, 'diff', '--binary', '--no-ext-diff', '--no-textconv', '--no-color',
'--no-renames', '--src-prefix=a/', '--dst-prefix=b/', '--no-relative',
'--submodule=short', f'{base}...{head}')`, then use
`hashlib.sha256(data).hexdigest()` for `diff_hash` (`advisory_git`, `historical_diff`).
Use the historical immutable base/head, not the squash commit or a mutable branch
(`historical_diff`). A separate fixture restores the confirmed report
from tracker comments after deleting the private packet
(`test_tracker_only_restitution_survives_private_packet_loss`).
