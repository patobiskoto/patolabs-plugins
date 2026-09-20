# Model-routing pilot protocol v1

| Field | Frozen value |
|---|---|
| Protocol ID | `FOUNDRY-MR-PILOT-v1` |
| Status | `FROZEN` |
| Frozen on | 2026-08-17, before pilot issue P-01 |
| Pilot | FOUNDRY-11 |
| Sample | Ten valid issues |
| Collection | Manual only |

This document is the normative measurement protocol for the first ten-issue Foundry
pilot. The companion [results sheet](model-routing-pilot-results-v1.md) contains only
the frozen price snapshot, invocation rows, and issue summaries.

## Freeze and invalidation rule

Protocol ID, formulas, token categories, column schemas, retry definition, validity
rules, and price-snapshot policy are immutable from P-01 through P-10. Filling data rows
does not change the protocol. Any other change invalidates the whole series: create a
new versioned protocol and empty results sheet, take a new price snapshot, and restart a
new ten-issue pilot at P-01. Do not reinterpret earlier rows in place.

Before P-01, complete the price snapshot in the results sheet for the declared primary
model and every model the routing mappings may select. Record the authoritative source
and capture timestamp. Prices are USD per million tokens for five **disjoint** billable
categories: `uncached_input`, `cache_read`, `cache_write_standard`,
`cache_write_extended`, and `output`. Standard means the provider-default cache-write
rate; extended means an opt-in longer-TTL write rate such as Claude's one-hour cache.
A supported but free category is `0`; an unsupported category is `N/A` and its token
count must be zero. A missing count or price is `TBD`, never `0`, and makes the issue
invalid. The snapshot does not change during the series, even if a provider changes its
public prices.

Translate host usage into those bins exactly once:

- Claude: `input_tokens` is uncached input, `cache_read_input_tokens` is a cache read,
  and `cache_creation_input_tokens` is a cache write. Assign creation tokens to standard
  or extended from the active TTL; if one invocation mixes TTLs and the usage surface
  does not split them, that invocation is invalid.
- GPT-5.6: `input_tokens` is the aggregate input count. `cached_tokens` and
  `cache_write_tokens` are its detailed cache bins; subtract both once to derive
  uncached input, record cached tokens as reads, and cache-write tokens as standard
  writes. Never also price the aggregate input count.

These field/rate distinctions are pinned from the official
[Claude Code caching](https://code.claude.com/docs/en/prompt-caching) and
[OpenAI GPT-5.6](https://developers.openai.com/api/docs/guides/latest-model)
documentation. If the host's manual usage surface cannot provide a disjoint breakdown,
the issue is invalid rather than estimated.

## Manual collection procedure

For each pilot issue:

1. Before work begins, assign the next slot P-01…P-10 and record issue ID, type,
   estimate, host, declared primary-loop model, and its effort/profile.
2. After every main-loop or delegated invocation, manually transcribe the host's usage
   counts. Record component (`main_loop` or `subagent`), semantic role, resolved model,
   effort, deterministic escalation count/signal, the five disjoint token counts, and
   actual cost. Never copy prompts, responses, diffs, environment values, or secrets.
3. A retry is a repeated invocation of the same role after a conclusively red test or a
   blocking review verdict. Agent doubt, a timeout, or a prose judgment is not a retry
   for this measurement, matching the escalation contract.
4. At issue completion, record the final review verdict (`PASS` or `BLOCK`), aggregate
   main-loop and subagent costs separately, then calculate the counterfactual.
5. Mark the issue valid only when all invocation rows, frozen prices, token counts,
   costs, escalation signals, retries, and the final review verdict are present.

Collection is deliberately manual. The MVP adds no usage hook, provider API call,
background telemetry, prompt capture, or automatic upload. The committed sheet may
contain issue IDs, non-sensitive routing metadata, aggregate usage/cost, verdicts, and a
concise evidence note only; sensitive data is forbidden.

## Frozen formulas

Let `j` be every observed invocation in issue `i`, and `k` one of the five frozen
billable token categories. `T(i,j,k)` is the observed token count, `P(model,k)` the
frozen USD-per-million price, `M(i,j)` the model actually used, and `Primary(i)` the
declared model of that issue's main loop.

```text
actual_cost(i) = Σ(j,k) T(i,j,k) × P(M(i,j),k) / 1,000,000
counterfactual_cost(i) = Σ(j,k) T(i,j,k) × P(Primary(i),k) / 1,000,000
main_loop_cost(i) = Σ(j in main_loop,k) T(i,j,k) × P(M(i,j),k) / 1,000,000
subagent_cost(i) = Σ(j in subagent,k) T(i,j,k) × P(M(i,j),k) / 1,000,000
savings_usd(i) = counterfactual_cost(i) - actual_cost(i)
savings_pct(i) = savings_usd(i) / counterfactual_cost(i) × 100
pilot_savings_pct = (Σi counterfactual_cost(i) - Σi actual_cost(i))
                    / Σi counterfactual_cost(i) × 100
```

The counterfactual therefore holds the observed invocations and token counts constant
and reprices every agent at the issue's primary model. It is compared with actual cost;
it does not invent a hypothetical number of calls or tokens.

## Known bias

Tokens are not equivalent across models, and neither are turns. An economy model may
need more calls, retries, or tokens for the same task; a stronger model might have
finished in fewer. Conversely, the observed routed path may be unusually efficient.
Holding observed tokens and invocations constant isolates price mix, not causal model
productivity. The retry/escalation columns expose part of that bias but do not remove it.

The main-loop/subagent cost split is mandatory. It shows whether routing delegated roles
alone can reach the target saving or whether the recommended primary-loop profile must
also change. Report both the aggregate saving and the stratified issue mix; never report
only the most favorable subset.
