# FOUNDRY-43 measurement harness

This is an explicit operator runner for the frozen FOUNDRY-41 corpus. It has no
production call site: it never observes ordinary telemetry, changes routing,
escalation or gates, and starts no host unless the operator invokes this module with
`FOUNDRY_MEASUREMENT_HARNESS=1`.

The operator supplies a bounded task-packet file and a JSON command-array file. The
runner sends the packet only to the invoked command's standard input, enforces the
configured subprocess timeout, and never stores either input, stderr, or raw stdout.
It starts its deadline and stdout (16 KiB)/stderr (4 KiB) bounded readers before an
isolated daemon writes stdin, so a child that never reads its packet cannot block the
caller; an overflow or timeout terminates the dedicated process group and closes its
pipes.
It writes one canonical JSON object plus LF to the host-specific append-only files
`claude.trace.jsonl` or `codex.trace.jsonl`. A trace is capped at 100 rows and 64 KiB;
packet, command and result projections are each capped at 16 KiB.
Before `Popen`, a host-specific trace lock reserves and validates the frozen attempt key
and remaining row/byte capacity. A duplicate or full trace is therefore rejected before
the host starts and writes no new JSONL row; the lock remains held until the launched
attempt is appended, then releases even on a collection failure.

Every subprocess runs with `cwd` equal to the resolved, existing `--root`; no Codex
`-C` option is used. Claude command arrays begin with `claude` and contain the literal
placeholders `{model}` and `{effort}`. The Codex command file is exactly
`["codex", "exec", "--json"]`: the runner, not the operator template, constructs the
native argv `codex exec --ephemeral --json --model <model> -c
model_reasoning_effort="<effort>" -`. `--ephemeral` prevents Codex session rollout
persistence; the final `-` makes Codex read the bounded packet from standard input.
This prevents unsupported `--effort` or
`--descriptor` flags and prevents a command template from silently selecting a frozen
model or effort.

For Claude, the runner itself resolves the frozen profile, launches the `claude`
command, and accepts a successful result only when its stdout JSON callback exactly
matches the resolved invocation model, effort, observed CLI version and override state.
`FOUNDRY_ROUTE_REQUEST=` is rejected when it is the first packet line, before the
Claude facade can read or create an `EscalationStore`; measurement packets cannot carry
an issue/escalation request. For Codex, it creates the actual `codex_spawn_plan`
internally and derives a digest from a safe projection of that descriptor plus the exact
UTF-8 bytes supplied to stdin, including leading and trailing whitespace. The digest is
runner-owned trace binding only: it is neither an environment variable nor a Codex CLI
argument, and no process echo is trusted. Codex stdout must instead be the
native `codex exec --json` JSONL stream for Codex 0.147.0: `thread.started`,
`turn.started`, `turn.completed`, `turn.failed`, `item.started`, `item.updated`,
`item.completed` and `error` are the only accepted types. The first two events are
`thread.started` with its string `thread_id`, then `turn.started`; every `item.*` event
has an `item.id`, concrete `item.type` and `item.text`. Exactly one final
`turn.completed` (with the complete native usage) or `turn.failed` (with
`error.message`) is required. One or more top-level `error.message` notifications may
occur only during the active turn before `turn.failed`; they are not terminals. An
error notification without `turn.failed`, a malformed order, terminal duplication, or
usage on any other event becomes `unknown/unknown`, never a success. Process
SIGTERM/SIGINT remains cancellation. Only a count/terminal/usage summary is persisted.
The raw packet is only
represented inside the descriptor digest; it is never persisted. A non-zero process,
timeout, SIGTERM/SIGINT cancellation or valid terminal event maps only to the five
closed terminal classifications in the protocol. An absent, malformed or ambiguous
Codex stream creates one `unknown/unknown` JSONL row with `stream.unknown`, no host
metrics and no output text; it never becomes a false success. A forged plan is rejected
before launch.

After a successful `Popen` launch, a capped stream, malformed collection result or
invalid/missing Claude callback also produces exactly one sanitized
`unknown/unknown` JSONL row. Its host metrics stay null/unavailable and no raw packet,
callback, stdout or stderr enters the trace. A deterministic process timeout,
cancellation or non-zero host exit preserves its closed terminal classification.
Validation that fails before `Popen` produces no row because it did not launch an
attempt.
The same applies to a bounded hostile JSON integer that causes CPython's decoder to
raise `ValueError`: it is caught at the Claude/Codex host boundary and persists only the
sanitized `unknown/unknown` row.

The v3 trace schema carries every native usage value represented by the common metric
contract: `input_tokens`, `cached_input_tokens`, optional `cache_write_input_tokens`,
`output_tokens`, and `reasoning_output_tokens`. Observed values are stored with
`host_reported` provenance. An omitted optional cache-write value remains null with
`unavailable` provenance; the harness never synthesizes zeroes. Item and error content
is hostile data and never enters the trace.

Claude must emit the documented strict JSON callback. Codex must use `codex exec --json`;
it may not supply a generic success envelope. This keeps native CLI/host integration
outside the evidence store while letting the shared runner own timeout, parsing,
provenance, F41 binding and persistence. It deliberately does not run repository tests:
`test_outcome` and estimated cost therefore remain null with `unavailable` provenance.

For an intentionally authorized run, use the module CLI (the example is not a command
to run from this document):

```sh
PYTHONPATH=<plugin-root>/tooling FOUNDRY_MEASUREMENT_HARNESS=1 \
  python -m foundry.measurement_harness claude \
  --trace-dir <private-trace-dir> --root <repository-root> \
  --profile-id claude-sonnet-medium --case-id FOUNDRY-31 \
  --revision 3fec3f1677ae6bd3ab5ef069afd6ca3073901b46 --repetition 1 \
  --timeout-seconds 900 --packet-file <bounded-packet-file> \
  --command-file <json-command-array-file>
```

`reproduce_jsonl` validates a supplied JSONL byte stream offline and returns its
canonical SHA-256 digest. It starts no CLI, model, daemon, network, test or benchmark.
The frozen [`protocol-v1.json`](../benchmarks/foundry-43/protocol-v1.json) binds every
row to the FOUNDRY-41 manifest and case/revision/repetition universe.

`routing_facades.py` retains only two F43-native, one-way delegates to this shared
runner. They establish Claude/Codex façade parity but have no production call site and
cannot observe telemetry or mutate routing, escalation, gates or passive behavior.

FOUNDRY-55 can consume only these already-sanitized, validated rows to build an
available-only context/tool-result baseline. It adds no observer or command to this
harness: see [the F55 baseline contract](context-usage-baseline.md). In particular, it
cannot publish a Context Broker gain conclusion before a later complete comparison is
separately frozen.
