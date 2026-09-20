# FOUNDRY-55 context and tool-result baseline

FOUNDRY-55 adds a benchmark-only baseline for interpreting later Context Broker work.
It consumes validated FOUNDRY-43 measurement projections and is disabled by design:
there is no runner, provider call, telemetry hook, routing choice, escalation, gate,
merge action or runtime behavior attached to it.

The baseline embeds a validated, sanitized F43 source row and deterministically
rederives its F55 metrics from it. It preserves host/provider separation and binds a
row to F41/F43 provenance, an execution digest, task, immutable revision, role, model
and repetition. It records input, output, cached-input and cache-write-input tokens,
tool results, retries, escalations, models, duration, tests, review and outcome as
independent metrics. Each is either a typed observed value, an explicit null, or
unavailable; unavailable means JSON `null` and never an inferred zero.

The current available-only constructor can prove only values present in the sanitized
F43 trace. It retains input, output, cached-input and optional cache-write-input tokens
only when each F43 metric is genuinely `host_reported`; otherwise it retains F43's
unavailable/null state. It leaves data F43 does not observe — including tool results,
retries, escalations, test/merge outcomes and provider cost — unavailable. Cost per
success or merge consequently remains unavailable in v1. The formula is only retained
as a controlled internal semantic for a later frozen protocol with concrete trusted
sources. It must divide every comparable attempt cost by only the successful or merged
outcomes, and returns unavailable if any cost/outcome is null, unavailable or unknown.
This is intentional: a self-declared source/digest cannot make a synthetic metric
observed, and no raw host or provider data is retained.

No FOUNDRY-55 report can state a Context Broker gain. The frozen protocol contains a
fixed prohibition until a later, separately frozen baseline/comparison protocol is
available. See [the F55 protocol](../benchmarks/foundry-55/protocol-v1.json) for the
exact allowlist and [its README](../benchmarks/foundry-55/README.md) for the offline
validation contract.
