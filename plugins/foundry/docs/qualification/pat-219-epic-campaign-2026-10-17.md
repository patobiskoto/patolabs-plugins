# PAT-219 — first real Epic campaign on this repository (2026-10-17)

Qualification record for PAT-219 (criterion 1 of PAT-154, PAT-ADR-0026). The campaign ran the
cadencer of `tooling/foundry/campaign_effects.py` (`campaign-cadence run`) on a dedicated
qualification Epic, PAT-221, whose three tickets were short fixes deferred to PAT-214 during
the 1.5.0 reviews: PAT-222 (validate the approval record before appending it), PAT-223 (mask
credentials in the session summary), PAT-224 (correct three statements of the campaign
documentation). Host: Claude Code, version during the campaign unknown (2.1.302 read after it), tracker Linear, code host GitHub.

## Command and approval

```
FOUNDRY_TRACKER=linear FOUNDRY_REQUEST_COUNT=stderr python3 plugins/foundry/tooling/foundry_cli.py \
  campaign-cadence run PAT-221 --root <repo> --budget-usd 3 --max-steps 80 --poll-seconds 90
```

The maintainer approved the preview in a terminal (`campaign-approval approve`, approver
`human-tty`, 18 sessions at most, 6 per issue, 48 hours). The same approval covered the three
runs below.

## What happened (observed)

| Run | Steps | Outcome |
|-----|-------|---------|
| 1 | 14 | PAT-222: start, implement, open-pr, review, defer-remarks (one transient failure, then done), merge (PR #172). PAT-223: start, implement, open-pr, review, defer-remarks, correct, then `suspend` `attempt_already_made`. |
| 2 | 3 | PAT-224: start, implement, then `open-pr` failed: `TrackerConflictError: Linear issue divergent after bounded write`. |
| 3 | 12 | PAT-224: review, defer-remarks, correct, open-pr, review, correct, open-pr, review, wait-ci ×2, merge (PR #174), then `await-verdict`. |

- Run 1 stop: the only remark marked "fix before merge" on PAT-223 concerned the PR
  description (a stray session message instead of a summary). The correction session made no
  commit, so the next pass decided the same correction again and the attempt journal refused
  it. The coordinator replaced the description through `issue openpr --summary-stdin`; the
  review proof of round 1 covered the unchanged diff (`routing claim-review` answered
  `should_run: false`), and PR #173 merged through `issue merge`. PAT-223 was therefore not
  merged by the campaign.
- Run 2 stop: the Linear state history of PAT-224 shows In Review at 09:25:12.746Z and back to
  In Progress at 09:25:13.038Z (UTC). The author of that change is not recorded here
  (unknown). Cause: deduced, not verified: the team's Git automation reacting to the PR
  opening (`docs/linear-tracker.md`, "GitHub merge automation interlock", allows the `start`
  and `review` rules; whether they are active in the team was not read).
- Run 3: the correction loop ran end to end: two corrections, each followed by a partial
  review (PAT-ADR-0022), then CI wait and merge.
- Deferred remarks: 8 in total (3 + 2 + 3) to follow-up issue PAT-226 under the companion
  Epic PAT-225 ("Nits PAT-221"). PAT-226 was created by the second deferral attempt of
  run 1 (`writes` of its report); PAT-225 by the first, failed attempt (deduced: the second
  attempt reports no companion creation). One companion and one follow-up issue exist
  (Linear listing).
- The Epic was closed with `issue close-epic PAT-221 --human-verdict=accepted` after the
  test procedure of PAT-221. The maintainer delegated both the procedure and the command to
  the agent explicitly; the verdict comment on PAT-221 records this.

## Cost

Session cost (`total_cost_usd` of the sessions, journal `campaign-attempts.jsonl`): run 1
2.13 USD, run 2 0.43 USD, run 3 2.44 USD, about 5.0 USD in total for 11 paid sessions
(3 implementations, 5 reviews, 3 corrections; the correction stopped by
`attempt_already_made` launched no session).

### Linear requests, per step

`linear_requests` is counted per process (`REQUEST_COUNT_ENV`, `trackers/linear.py`;
`docs/linear-request-cost.md`). The cadencer prints its own total at exit; each Foundry
subcommand it runs prints its own line, which the effect records in `detail`. The Claude
sessions do not receive the variable (`_CLAUDE_CHILD_ENV_ALLOWLIST`,
`command_runtime.py`), so the tracker reads made by a session or by the reviewer's read verbs
are not counted.

| Measure | Run 1 | Run 2 | Run 3 |
|---------|-------|-------|-------|
| Cadencer process (one observation per step, plus every tracker read made in that process: plan reads, `get_issue` of `implement`, `plan_correct`, and the reads of the deferral effect) | 1723 | 13 | 479 |
| Steps | 14 | 3 | 12 |
| Average per step, cadencer process | 123 | 4 | 40 |

Effects, from the subcommand lines: `issue start` 13 (×3), `issue openpr` 13 (×2) and 10
(×2, the re-push after a correction), `issue merge` 33 (×2), first failed deferral 2, failed
`openpr` of run 2 12. The review, correction and successful deferral effects recorded no
count.

Run 2 (three observations, no review, no deferral) gives about 4 requests per observation, so
the observation alone does not explain runs 1 and 3. Deduced, not verified: the excess follows
the deferral effect, which runs in the cadencer process and reads the whole project
(`tracker.search(project)` in `_find_companion` and `_find_follow_up`, `campaign_defer.py`):
run 1 has three deferral attempts and 1723 requests, run 3 one attempt and 479. The breakdown
per effect is unknown. A pass of `campaign-cadence next` was measured at 13 requests by
PAT-216.

### Comparison with the PAT-177 estimate

The estimate (comment on PAT-177, 2026-10-16) is about 75 requests per ticket with one review
round and about 120 with three rounds and two corrections. It counts start 13, open-pr 11 to
15, merge 20 to 30, and a revalidation of about ⌈P/100⌉ before each effect.

- The effects measured by their subcommand are close to their estimates: `start` 13 (estimate
  13) and the first `open-pr` 13 (estimate 11 to 15) fall inside; the re-push after a
  correction, 10, is below; `merge` 33 is above (estimate 20 to 30).
- The total does not: run 1 alone is 1723 requests in the cadencer for less than two tickets,
  against about 150 to 200 estimated. Where the excess comes from is unknown (deduced lead: the
  deferral effect, above); measuring it is the subject of PAT-228.
- The estimate's recommendation (no tracker re-read while waiting for CI) is not followed:
  `run` observes at every step, `wait-ci` included (`campaign_effects.run`).

## Deviations recorded as issues (under PAT-154)

- PAT-227: a remark that targets only the PR description has no path in the correction
  loop. The implementer's summary may be a stray message and becomes the PR body as is.
  Observed in the attempt journal: the PAT-223 implementation reports 2.2 seconds for
  0.32 USD, and the correction 6.2 seconds while its summary describes a full test run; the
  implementation summary opens on a reply to the notification of a background command.
  Deduced, not verified: the session ran the tests as a background command, and the
  `--print` result kept by the cadencer is the last turn, the reply to that notification,
  with that turn's duration only.
- PAT-228: measure, then reduce, the Linear requests of a campaign. Measurements and the
  reduction of the deferral reads: `docs/linear-request-cost.md`, "Epic campaign (PAT-228)".
- PAT-229: the deferral effect reads the last line of the standard error, which is the
  request counter when `FOUNDRY_REQUEST_COUNT=stderr` is set. The first failure of run 1
  recorded `foundry linear_requests=2` as its reason, and `companion: null`, although the
  companion Epic was created at that moment (deduced from its creation time).
- PAT-230: the Linear Git automation can move an issue back after `issue openpr`, which
  fails the `open-pr` effect.

Also observed, not filed:

- The session summaries of PR #172 and #174 say "nothing pushed": the session does not know
  that the cadencer pushes. Covered by PAT-227's criterion on unusable summaries.
- In the sandbox, the full test suite fails on keychain and frozen-release tests that pass in
  CI. The cause is unknown; CI stays the gate.

## Not established

- The tracker cost of the observation and of the review, correction and deferral effects,
  per step.
- Whether the campaign would finish without the coordinator on a ticket like PAT-223 (it
  cannot today: PAT-227).
- Behaviour on another tracker, host or version.
