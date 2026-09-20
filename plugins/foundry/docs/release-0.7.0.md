# Foundry 0.7.0 release report

Release decision: **publish the shared Claude Code/Codex implementation, but promote no
local model and leave every production resolver, routing profile, gate, and default
unchanged.** FOUNDRY-35, FOUNDRY-37, and FOUNDRY-40 are complete. The release contains
the non-privileged local preprocessing boundary, passive telemetry, deterministic
remediation credits, and the corrected Claude Agent façade; it does not contain adaptive
routing or a model recommendation.

## Agent architecture shipped in 0.7.0

Foundry delegates four logical roles through one deterministic cross-host policy. The
main coordinator is a documented primary profile, not a delegated role the plugin can
force on an already-open conversation.

| Tier | Claude mapping | Codex mapping | Default role / floor |
|---|---|---|---|
| `economy` | Haiku 4.5 / low | GPT-5.6 Luna / low | scout |
| `balanced` | Sonnet 5 / medium | GPT-5.6 Terra / medium | implementer and coordinator |
| `frontier` | Opus 5 / high | GPT-5.6 Sol / high | reviewer minimum gate |
| `apex` | Fable 5 / high | GPT-5.6 Sol / max | architect minimum gate |

The recommended primary profile for ordinary feature/fix/chore coordination is
**balanced**: Sonnet 5 / medium on Claude Code or GPT-5.6 Terra / medium on Codex. Move
the primary loop to frontier only for a disclosed no-subagent fallback or genuinely
cross-cutting risk; reserve apex for explicit architecture decisions. This guidance is
not a runtime default mutation.

A resolved decision has three separate dimensions:

- `model`: selected provider capability;
- `reasoning_effort`: ordinal budget within a declared host/model-family/policy scope;
- `context_policy`: deterministic collection, filtering, truncation, and packet rules.

The delivered tier mapping remains the production resolution. User request > project
configuration > Foundry defaults. Ordinary availability fallback walks downward unless
bounded by an active issue floor. Reviewer and architect never cross below their
frontier/high and apex/high-or-max floors and fall back upward only. Deterministic red
tests, blocking reviews, or explicit risk/ADR/user signals may raise an issue-scoped
floor; free-form agent judgment cannot. At most two tier increases are allowed per issue,
after which both hosts require a human. A human remediation window can authorize one to
three exact post-fix review attempts, but does not renew that cap, lower any floor, add a
routing signal, or widen tools and authority.

There is no auto-learning resolver, dynamic effort ladder, automatic context transition,
production shadow call, telemetry feedback, or new gate behavior in 0.7.0. The complete
contract and host-specific context boundaries are in
[`model-routing.md`](model-routing.md).

## Local scout boundary

Local preprocessing is disabled by default and has no implicit model. The operator must
choose an already-managed model in `.foundry/local-scout.json` and separately enable an
exact loopback-only endpoint through trusted user configuration. A repository cannot
activate it, select a destination, or authorize cloud spend. Foundry never downloads a
model, installs a runtime, starts a daemon, uses proxy configuration, follows redirects,
or grants the local model tools.

The local model is an untrusted, non-privileged preprocessor, never a Foundry host, tier,
role, reviewer, architect, or gate. `diff`, `logs`, and `tests` use one immutable,
filtered, redacted, bounded capture. `code` uses exactly two local calls: a body-free
path manifest, then wrapper-owned frozen evidence. The model may cite only evidence IDs
created by deterministic code; the cloud packet carries the filtered raw excerpts,
locators, provenance, digests, limits, and truncation markers. No adaptive third cycle is
available.

The default failure policy is `error`. Explicit trusted `cloud_economy` consent permits
only one sanitized plan after `LOCAL_SCOUT_UNAVAILABLE` or
`LOCAL_SCOUT_INVALID_OUTPUT`; it never makes the call. Policy violations and stale input
cannot fall back. The planned cloud scout remains governed by the unchanged ADR-0006
route, floors, fallback, escalation, and read-only capability. Doctor reports only
`disabled`, `configured`, `available`, `unavailable`, or `invalid policy`; its sole
availability probe is a bounded loopback TCP connect, not a completion.

The detailed trusted configuration, privacy boundary, hard request/response/data limits,
2/8-second default and 10/120-second hard timeout budgets, commands, fallback, and doctor
behavior are in [`local-scout.md`](local-scout.md).

## Frozen FOUNDRY-35 benchmark protocol

The v4 campaign reused the exact frozen v3 intrinsic/product/cloud matrices, fixtures,
goldens, prompt, runtime profile, thresholds, and fail-closed evidence semantics. V4
changed only the storage strategy after v3 failed before checkpoint or download. The
freeze commit was `4c7aed539583b597b12fbc71f4b99cffb577c916`; all model/runtime,
artifact SHA-256, quantization, prompt, hardware, request, generation, timeout, and
authorization inputs were fixed before execution.

The environment was Python 3.14.7, Foundry 0.6.2, `mlx-lm` 0.31.3, and Codex CLI 0.147.0
on one arm64 Mac17,9 with 64 GiB unified memory and an Apple M5 Pro integrated GPU.
Local inference was sequential at temperature 0, top-p 1, seed 35, and 192 maximum output
tokens. The controlled matrix contained:

- 108 non-promotional intrinsic rows: four candidates × nine cases × three runs;
- 48 promotion-authoritative real-product rows: four candidates ×
  `code`/`diff`/`logs`/`tests` × three runs;
- seven deterministic wrapper probes for root escape, symlink, invalid JSON, oversize,
  timeout, missing endpoint, and stale input;
- 60 shared cloud baselines across Sol/high, Luna/low, and Terra low/medium/high;
- 48 candidate-to-Terra/medium pipelines paired to the exact 12 shared controls.

The ordered audit ledger contains 346 attempts: 156 local-model, 108 downstream-cloud,
12 façade-resolution, and 70 technical-setup attempts. It recorded 247 completions and
99 typed errors. Downstream execution occurred exactly once per row and added no retry.
Historical raw evidence and aggregate remain immutable; the deterministic post-run
addendum is normative only for corrected raw-trace linkage, throughput, and packet-null
interpretation. [`RESULTS-CONTRACT-v4.md`](../benchmarks/foundry-35/RESULTS-CONTRACT-v4.md)
defines that boundary and its offline, no-network reproduction check.

## Results

Every candidate failed the mandatory product-schema boundary on all 12 product rows, so
no wrapper-owned packet crossed into its candidate pipeline. Evidence binding and product
safety were therefore non-evaluable and fail closed, not zero incidents. Every candidate
pipeline failed its downstream golden while all paired Terra/medium controls passed.

| Candidate | Product schema | Downstream candidate / control | Median throughput | Median / p95 preprocessing | Peak RSS | Median input reduction | Tokens avoided | Extra retries |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Qwen3.8 27B | 0/12 | 0/12 / 12/12 | 15.926 tok/s | 12.055 / 15.413 s | 9.507 GiB | 0.3185% | 674 | 0 |
| gpt-oss 20B | 0/12 | 0/12 / 12/12 | 76.633 tok/s | 2.380 / 4.334 s | 11.359 GiB | 0.3247% | 831 | 0 |
| Devstral Small 2 24B | 0/12 | 0/12 / 12/12 | 15.516 tok/s | 4.482 / 5.581 s | 10.272 GiB | 0.3185% | 664 | 0 |
| Gemma 4 26B-A4B | 0/12 | 0/12 / 12/12 | 50.997 tok/s | 3.765 / 7.789 s | 8.978 GiB | 0.3246% | 829 | 0 |

The common incompatibility was at the frozen product response allowlist: every MLX reply
included unsupported `usage.prompt_tokens_details`; Qwen and Gemma also emitted an
unsupported reasoning field, and some omitted string message content. This is a real
frozen-boundary result, not evidence about how their otherwise-valid content would behave
after a future compatibility change.

Cloud baselines, derived from the linked Codex JSONL host events, were:

| Frozen profile | Correct | Input tokens | Output tokens | Cached input tokens |
|---|---:|---:|---:|---:|
| Sol/high, no preprocessing | 12/12 | 194,331 | 1,417 | 73,216 |
| Luna/low, economy façade | 9/12 | 169,078 | 1,771 | 97,792 |
| Terra/low | 12/12 | 193,923 | 850 | 138,240 |
| Terra/medium shared control | 12/12 | 194,092 | 958 | 140,288 |
| Terra/high | 12/12 | 194,321 | 1,108 | 128,000 |

Total and reasoning-token fields were unavailable and remain `null`. The frozen price
grid had no applicable immutable public prices for these profiles, so estimated cloud
cost, cloud-cost reduction, and net-cost reduction are all `null`, never zero. A roughly
0.32% median input reduction is far below the frozen 40% threshold and cannot support
the required 25% cloud/net cost-reduction gates even if a future price source exists.

The full raw chronology, hashes, corrected safety interpretation, intrinsic diagnostics,
and evidence links are in
[`FINAL-RECOMMENDATION-v4.md`](../benchmarks/foundry-35/FINAL-RECOMMENDATION-v4.md).

## Limits and next necessary test

This campaign covers one machine, one frozen runtime stack, synthetic versioned fixtures,
and four pinned MLX artifacts. It cannot generalize latency or memory to other hardware,
establish unavailable prices, compare ordinal effort numerically across hosts, or infer
post-compatibility model quality from absent product packets. Passive telemetry is also
associational and currently lacks deterministic actual token, duration, packet-size,
files-touched, and cost values at some host boundaries; those fields remain unknown.

Before another benchmark, make a separate reviewed product decision on whether the strict
OpenAI-compatible response allowlist should accept standardized nested usage metadata and
reasoning-channel variants. If that boundary changes, freeze a new protocol and rerun the
uncensored real-product matrix with the same wrapper safety probes, paired Terra controls,
raw trace ledger, retry accounting, nullable-cost rule, and fail-closed promotion gates.
Only that new evidence could support a local-model recommendation. Until then, local
preprocessing stays disabled and every 0.7.0 production default remains unchanged.
