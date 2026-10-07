# Changelog

## Unreleased

- Added/fixed (PAT-121; fake arms only, no real model/harness/cloud call; v1, v2 and v3 protocols, configs, results and launcher behaviour untouched): PAT-19 protocol v4 frozen before any trial (`docs/qualification/pat-19-protocol-v4.md`, French) to repair the comparison instrument and replay the downstream measurement: two arms only (A, L; no Haiku arm), candidate fixed to `qwen3.6-35b-a3b-mlx-4bit` with no new screening (same 60 steps / 15 min bounds and reload before each task as v3), comparison on the six tasks of the v3 screening (PR 30, 83, 27, 24, 48, 19; already explored locally in the v3 screening, never played downstream; the exploration is re-run), decision rule unchanged (L retained iff acceptance(L) >= acceptance(A) and premium per accepted task <= 0.85 x A; absent data never zero; insufficient evidence keeps the cloud), cap of 80 cloud executions, stated limits (six tasks, tasks already seen by the explorer, no Haiku arm, nothing said of other task families). Values validated by the maintainer on 2026-10-07 except the corrector feedback (at most 20 failing hidden-test names, each message cut to 300 characters, never test source or path, same for both arms) and the 2 correction rounds, fixed by the coordinator before any trial and to be confirmed by the maintainer. Launcher, all off when the keys are absent: `correction_feedback` (after a judge refusal the corrector is told the failing hidden tests; `lfc.judge(..., failures=True)` returns them from the junit report, message cut and bundle path hidden), `isolation.private_attempt_root` (each attempt in `<work-root>/private-<attempt>/<attempt>/`, so `ls ..` and `ls ../..` show no sibling and raise no contamination flag; the work root and every other attempt are sensitive roots, the rest of the audit is unchanged), `exploration.fixed_candidate` (no screening needed, `screen-exploration` refused, another candidate refused) and `exploration.comparison_task_group` (`screening`: manifest group the comparison tasks come from), new config `pat-19-campaign-v4.json` (loader pins its coordinates) and operator loop `pat-19-v4-operator.sh`/`.md` (the v3 script hardcodes the v3 config and arms A,L,E). PAT-120 review nits: dead `FOUNDRY_DATA` exemption removed from the leak-probe test; `FOUNDRY_DATA` imposed by driver kind (every cloud driver kind, not `home != isolated`) and `isolated_environment` drops any host `FOUNDRY_*` (the loader already refused them); "bras cloud" wording fixed in `pat-19-v3-operator.md` and the R5 line of `pat-19-launcher-v1.md`; the operator backup covers both registry locations (`~/.config/foundry` and `$FOUNDRY_DATA` when exported). R5: documented in the protocol v4, `pat-19-v4-operator.md`, the "Protocole v4" and "Audit de contamination" sections of `pat-19-launcher-v1.md`; no `foundry_cli.py` verb, product configuration key, routing table or public routing constant changed. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Fixed/hardening with a declared side effect (PAT-120; fake arms only, no real model/harness/cloud call; frozen v1/v2/v3 protocols and configs untouched, campaign config sha unchanged; applies to every later launch of any protocol): a cloud arm can no longer write the machine's real Foundry registry through the repository's own code or tests (incident of 2026-10-07, PAT-117). The launcher sets `FOUNDRY_DATA` itself to a fresh empty `<attempt scratch>/foundry-data` for every CLOUD execution (implementer, corrections, reviewer, cloud explorer; local arms keep exactly their previous environment), after `env_set`, never from the config or the host; the AGENTS.md R6 variables are untouched. It also fingerprints (sha256 or absent) the REAL `registry.json` at both places (host `FOUNDRY_DATA` when exported, and `<HOME>/.config/foundry`) before and after each cloud execution: on a change the execution is settled in the ledger, the attempt record is `contaminated` (not accepted, never replayed, `billing_total` null) with the fingerprints (never the content), also for the review of a path C local attempt, a `stop` record with reason `foundry_state_changed` is written and the launcher exits 4; it never writes or restores the registry. Comparability limit: `FOUNDRY_DATA` enables the product's telemetry journal, so from PAT-120 the arms' own runs of the repository tests see it set (unlike v1-v3 campaigns); the effect on what they observe is unknown; the judge is unaffected. What the guard misses, the remaining limit (unconfined writes elsewhere in the real HOME, uninventoried test state) and the check that all 12 corpus bases read `FOUNDRY_DATA` first are stated in the launcher doc. Operator doc: back up the Foundry configuration directory before a campaign (`cp -Rp`) and run no registry-writing Foundry command meanwhile. R5: documented in "État Foundry d'un bras cloud" of `pat-19-launcher-v1.md` and `pat-19-v3-operator.md`; no `foundry_cli.py` verb, product configuration key, routing table or public routing constant changed (launcher internals: `registry_fingerprints`, stop reason, exit code 4). Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Docs/evidence (PAT-117; docs and raw evidence only, no code change, no model load by the diff, no cloud call by the diff): real PAT-19 v3 campaigns run on 2026-10-07 (code `main` at `00eee40`, omp 18.6.1, LM Studio 0.4.25+1, model unloaded and reloaded before each one-task launch). Screening `pat-19-x3screen-1` (12 launches, no cloud): both candidates pass the frozen thresholds (qwen3.6-35b-a3b-mlx-4bit mean function recall 0.639, file precision 0.958, 0 refusal; qwen3-coder-30b-a3b-mlx-4bit 0.583 / 0.667, 2 refusals); retained = qwen3.6; v2 on the same six tasks gave 0.25, but budget, candidate set and reload regime changed together and the v3 screening is tuned on those tasks. Comparison `pat-19-x3compare-1` (6 launches, arms A/L/E on the 6 comparison tasks): 51 cloud executions (cap 120), 11,101,326 premium billing tokens; accepted tasks A 0/6, L 0/6, E 1/6; decision `inconclusive`, `campaign_conclusion` `keep_cloud_insufficient_evidence` (L: compatibility pass, quality and economy unavailable), so the cloud is kept by PAT-ADR-0015; no promotion. 7 contamination flags, all of one shape (a cloud arm named the launcher's work root); applied as frozen, a likely false positive that is NOT adjudicated, nothing reclassified. What the data support: reference arm A got no task accepted (3 refused by the hidden tests on all rounds, 3 never judged because of the contamination flags), so the comparison could not measure the downstream effect of exploration; on PR 25 the judge did discriminate (E accepted, A and L refused), and PR 26 and PR 38 have strictly identical test counts across all 9 attempts (a possible instrument defect, reading, not established). Observed model context: 262144 for qwen3.6 and 65536 for qwen3-coder (requested 65536), a fourth uncontrolled variable of the ranking and of the v2 to v3 comparison. Incident: a cloud arm (L, PR 42, 10:03:46) overwrote the maintainer's Foundry registry by running the repository tests (restored the same day; fix tracked in PAT-120); the records are intact but the influence on the attempts played afterwards is unknown; mechanical conclusion unchanged. Two of the seven flags come from the reviewer. Report `docs/qualification/pat-19-exploration-results-v3.md`, raw files `docs/qualification/pat-19-runs/x3screen-1/` and `x3compare-1/` (the 69 raw streams are not committed). R5: report and raw pieces added, link in the "Protocole v3" section of `pat-19-launcher-v1.md`; no CLI verb, configuration key, public constant or routing table changed; frozen protocol v3 and config v3 untouched.
- Added (PAT-116; fake arms only, no real model/harness/cloud call; v1 and v2 protocol, config and results untouched, v1/v2 launcher behaviour unchanged): PAT-19 protocol v3 frozen before any trial (`docs/qualification/pat-19-protocol-v3.md`, French; values and the v2 coordinator rules validated by the maintainer on 2026-10-07): the v2 read-only exploration with three changes that move together (a v2 to v3 screening difference is not attributable to the budget alone): the explorer budget (60 steps, 15 minutes, also for the cloud explorer of arm E), the candidate set (two fast MoE candidates chosen after the v2 results, `qwen3.6-35b-a3b-mlx-4bit` and `qwen3-coder-30b-a3b-mlx-4bit`, both required for a complete screening) and the model reloaded before each task as a rule written in advance; everything else inherited from v2 by reference (prompt, judge, ground truth sha256, thresholds, 0.85 x A rule, cap of 120). Stated limit: the v3 screening replays the same six screening tasks with a budget widened after seeing their results; the evidence that counts is the downstream comparison on the six never-played tasks. Launcher: new config `docs/qualification/pat-19-campaign-v3.json` (schema v2, `protocol` `pat-19-protocol-v3`, loader pins candidates, bounds and the flag) and the optional config key `exploration.one_task_per_launch` (screen-exploration and compare-exploration play at most one undecided task per launch, exit 0 and print `pat19-v3: work_remains=yes|no`; absent = v2 behaviour); operator reload loop `docs/qualification/pat-19-v3-operator.sh` (documented in `pat-19-v3-operator.md`) (the launcher still loads no model; the reload is attested by the operator script and one preflight per task). R5: documented in the protocol v3, the operator doc and the "Protocole v3" section of `pat-19-launcher-v1.md`; no `foundry_cli.py` verb, product configuration key, routing table or public routing constant changed.
- Docs/evidence (PAT-115; docs and raw evidence only, no code change, no cloud call): real PAT-19 v2 exploration screening run on 2026-10-07 (`pat-19-xscreen-1`, code `main` at `3af0932`, 5 candidates x 6 tasks, no cloud): no candidate reaches the frozen threshold (best mean function recall 0.25 < 0.5, no mean file precision >= 0.5), so by the frozen screening rule the campaign stops on "keep the cloud" for read-only exploration; comparison not run, no cloud quota spent. Of the 30 attempts, 23 were cut by a bound (15 time, 8 steps) and 7 answered within the bounds: 1 invalid report (missing keys), 2 empty (zero step) and 4 non-empty, all 4 naming the right file (function recall 1.0, 1.0, 1.0, 0.5); downstream effect not measured; promotion none. The last candidate's result is mixed with an operator intervention (two unload/reload cycles after dedicated-machine preflight refusals, a coordinator decision taken during the campaign by analogy with protocol section 11, maintainer validation requested; the two zero-step answers both follow a reload), see the "Dernier candidat et mémoire de la machine" section. Report `docs/qualification/pat-19-exploration-results-v2.md` and raw files `docs/qualification/pat-19-runs/xscreen-1/` (the 30 raw streams are not committed). The maintainer's validation of the coordinator-set rules of 2026-10-07 is requested in the report. R5: two artefacts added (report, raw pieces) and a link in `pat-19-launcher-v1.md`; no CLI verb, configuration key, public constant or routing table changed; frozen protocol v2 and config v2 untouched.
- Added (PAT-114; the diff's tests use fake arms only, the coordinator ran two real toy trials of the explorer drivers on
  2026-10-06; v1 protocol, campaign config and results untouched): PAT-19 protocol v2 frozen (`docs/qualification/pat-19-protocol-v2.md`, French, values validated by the
  maintainer on 2026-10-06): read-only exploration judged first on localization (local screening, no cloud: retained =
  highest mean FUNCTION recall among candidates with mean file precision >= 0.5, tie on shortest duration, STOP "keep
  the cloud" when the retained function recall is < 0.5, file recall measured but not deciding because each task has a
  single product file shared by 5 of 6 screening tasks; a contaminated or refused attempt counts as 0) then on its downstream effect (arms
  A / L / E on the 6 frozen comparison tasks, premium tokens per accepted task with the explorer included, L retained
  if compatibility, quality (acceptance >= A's) and economy (premium per accepted task <= 0.85 x A's) all pass; E vs A informative; three separate
  verdicts). Review round 3: a start-of-run preflight of `compare-exploration` (ledgered `phase: "start"`) followed by another preflight of the same launch is no longer a false `preflight_without_attempt` (resume after a refused preflight or a void exploration, `--paths L`), and the preflight is not run twice in a row; the screening report gains `campaign_conclusion` (`keep_cloud_insufficient_evidence` when it can no longer be completed under the resume rule) and `not_completable_tasks`; an L/E task with its exploration but no implementer record is undecided (`awaiting_implementer`, `incomplete_campaign`), not rejected; the four verdict-determining rules move to `exploration.protocol_coordinates`; the rules added on 2026-10-07 are dated as set by the coordinator before any corpus trial, their validation by the maintainer being requested in the campaign report; both explorers were re-run for real on the toy repository on 2026-10-07 with the frozen prompt (`final_prompt_trial`). Review round 2: a complete comparison with an `unavailable` verdict and no fail concludes `keep_cloud_insufficient_evidence` (`campaign_conclusion`); the system allow-list of the dedicated-machine check is narrowed to `/System/Library/` (consumer apps under `/System/Applications/` and the Cryptexes are ordinary consumers); the score-determining rules are protocol coordinates; a bound reached by the harness or a start error are handled (refusal / void); the dedicated preflight also runs at the very start of `compare-exploration` when arm L is requested; `main` now returns code 2 with a message (no trace) on a config load error, for v1 configs too. Review round 1 (rules validated 2026-10-06): file precision counts any file the merged diff changed (tests, docs, CHANGELOG
  included); only the first 10 functions of a report count and reach the implementer; dedicated machine free memory >= 35 % (replaces the swap-at-start threshold; the process rule is the real guard) and re-run right before each local exploration; a ground truth loaded from the committed file whose sha256 is in the config; an exploration cut by a bound is refused; the screening needs all five frozen candidates; the threshold is tested before the tie. The launcher gains the modes `screen-exploration` and `compare-exploration`, the type `local_explorer`
  (omp `--tools=read,grep,glob`, bundle read-only in the sandbox profile, 10 min / 25 steps) and `cloud_explorer`
  (Haiku 4.5), a deterministic localization judge (`foundry.local_first_exploration`, ground truth from the merged
  diffs, committed for the 12 tasks in `pat-19-exploration-truth-v2.json`), a dedicated-machine admission at preflight
  (another process over 2 GiB or under 35 % free memory refuses) and the new config `pat-19-campaign-v2.json`; the v1
  modes behave as before. The two explorer drivers were trial-run for real on 2026-10-06 on a toy repository (evidence `pat-19-preflight-v2-2026-10-06.json`) and are `verified: true`; the trial refused omp `--tools=read,grep,find,ls`, so the tool names were corrected to `read,grep,glob` before the freeze (loader accepts only these). The unsandboxed cloud explorer's read-only behaviour is observed, not enforced: the launcher checks the bundle's `git status` before and after each exploration and refuses one that changed it. R5:
  documented in `pat-19-protocol-v2.md` and the "Protocole v2" section of `pat-19-launcher-v1.md` (new CLI verbs
  `screen-exploration`, `compare-exploration`, `preflight --dedicated` of the campaign tool, config schema v2, driver
  types, result paths `XS`/`L`/`E`); no `foundry_cli.py` verb, product configuration key, routing table or public
  routing constant changed.
- Docs/decision (PAT-110; docs only, no code, no model load, no cloud call): PAT-19 v1 decision recorded in
  `docs/qualification/pat-19-decision-v1.md`. Autonomous ticket implementation by a local model: keep the cloud
  (v1 screening 0/30); no adoption, no local model, profile or default activated, no new ADR; v1 is not extended to
  12 tasks. Next usage to qualify: read-only exploration for a cloud implementer (local Lupin) under a protocol v2
  (PAT-114 freezes it, PAT-115 runs it). PAT-87 milestones PAT-88, PAT-89, PAT-90, PAT-91 postponed until the PAT-115
  verdict (none abandoned). PAT-19 stays open. R5: documented in the new decision file, linked from
  `pat-19-screening-results-v1.md` and `pat-19-launcher-v1.md`; no CLI verb, option, public constant or routing table
  changed, no protocol coordinate changed.
- Docs/evidence (PAT-109; the diff contains no code change and no cloud call; the run itself loaded five models): raw results and written report of the real PAT-19 v1
  screening run on 2026-10-06 (`pat-19-screen-1`, code `879e7b7`): 0 accepted of 30 (5 candidates x 6 tasks), so the
  protocol section 4 rule stops the campaign on "keep the cloud" for autonomous ticket implementation; no comparison
  run, no cloud spend. Files: `docs/qualification/pat-19-screening-results-v1.md` and
  `docs/qualification/pat-19-runs/screen-1/` (ledger, results, report, envelope, streams manifest; the 49 MB raw
  streams are not committed). R5: documented in `pat-19-screening-results-v1.md`, linked from `pat-19-launcher-v1.md`
  and `pat-19-protocol-v1.md` (no protocol coordinate changed); no CLI verb, option, public constant or routing table changed.
- Fixed (PAT-112; no model load): the launcher preflight read the LM Studio version from `lms version`, which prints
  only the CLI commit, so every real candidate was refused (`lm_studio_version_differs`). It now reads the app's
  `CFBundleShortVersionString` with the read-only `plutil -extract ... raw` on `/Applications/LM Studio.app/Contents/Info.plist`
  (`LM_STUDIO_VERSION_COMMAND`, in `READ_ONLY_COMMANDS`; `lms version` is no longer allowed); same fact name and
  `contains` comparison against the unchanged frozen 0.4.25; a missing plist refuses as `fact_unavailable:lm_studio_version`.
  R5: documented in `docs/qualification/pat-19-launcher-v1.md` "Préflight"; the public constant `READ_ONLY_COMMANDS`
  swapped `lms version` for the plutil tuple; no CLI verb changed.
- Fixed (PAT-112; fake arms only, no real model/harness/cloud call): the local contamination audit no longer
  hides a sandbox bypass through a system service (`launchctl`, `osascript` are forbidden executables, local and
  cloud; best effort: `crontab`, `at`, an out-of-sandbox `tmux`/`screen` server, `shortcuts run`, `automator` stay unlisted; the "blocked" exemption is decided on the path, not on an observed refusal) and treats the attempt directory
  (the bundle's parent, passed explicitly as `attempt_dir`) as allowed, so `ls ..` is no longer a contamination
  (`ls ../..` still is); a runner-level test proves `sandbox_denied` reaches a local driver's audit and never a
  cloud one. Docs/evidence: the generation_config digest is the only pinned sampling element (effective
  sampling values are unrecorded, limit stated), the 4-bit candidate's missing `loaded_instance_observed` and
  the trials' `pat19-smoke`/`--ttl 1800 -y` load are stated. R5: documented in
  `docs/qualification/pat-19-launcher-v1.md` "Statut documentaire" (`deny_home_trial`,
  `smoke.deny_home_evidence`, `sandbox_denied`, and the new driver key `binary_version`); no CLI verb changed, and the public constant `FORBIDDEN_EXECUTABLES` gained `launchctl` and `osascript`.
- Fixed (PAT-112): the omp version recorded as 18.4.10 was observed before the upgrade; the installed and used one is
  18.6.1 (installed 2026-10-05 17:20, every omp trial ran later), corrected in the evidence, campaign and docs.
  The launcher now runs `omp --version` from PATH and refuses a missing, unparsable or different version
  (driver `binary_version`, checked with the harness executable at the start of `screen`/`compare` and per attempt).
- Fixed (PAT-111, review round 4): a local arm's read that its deny-home sandbox refused (real home outside
  the allow list, or an explicitly denied path) is a blocked attempt, no longer a contamination
  (`audit_transcript(sandbox_denied=...)`); a signal between the bundle discard and the settle of a judged
  local attempt now waits for the record (the attempt is never replayed). Evidence: the four remaining
  candidates passed the toy tool-call trial under the final deny-home profile on 2026-10-05
  (`deny_home_trial` per candidate), plus one cloud control run (heredoc with `gh`/`~/.config` strings,
  audit clean); sampling parameters are declared unpinned. Cloud-only gaps (a call refused by a
  `cloud_bash_deny` rule still counts as contamination; interpreter heredoc bodies are not audited) are
  documented, to be handled before the comparison.
- Fixed (PAT-111, review round 3; fake arms only): the contamination audit no longer reads heredoc bodies given to
  `cat`/`tee`/`python3 -` (a body a shell runs, and the `$(…)` of an unquoted-delimiter body, stay audited),
  `#` comments or separators inside quotes as commands/paths, allows a cloud arm's own Claude Code session
  directory (saved tool outputs), ignores in tool results only the real-home paths the bundle's own files
  contain (`base_literals`, read at bundle build; PR 83's base), expands a local arm's `~` to its isolated HOME, and a cut after the audit keeps
  the contamination on the record (never replayed); the docs no longer claim a cloud arm cannot touch the state files.
- Fixed (PAT-111, review round 2; fake arms only, no real arm run): the contamination audit no longer
  treats text as an access: only path arguments (`path`, `file_path`…, the `pattern` of a Glob/find tool) and
  shell commands are read, not what an arm writes or searches (Edit/Write content, Grep pattern); a tool
  result counts only for a LITERAL absolute path under a sensitive root (`~`/`$HOME` in result text are not
  expanded), and in a command `~` inside quotes or `$HOME` inside single quotes is text
  (`grep -rn '~/.claude' .` is clean; `cat ~/.config/…`, `ls ~/.claude/plugins` stay flagged). A bare `/` is a
  path again as an argument of `find`, `grep -r/-R`, `rg`, `ls`, `du`, `tree`, `cat` (`find / -name x -exec cat
  {} +` is flagged; the heredoc division stays clean). After the arm ran, a git failure taking the patch (stale
  `.git/index.lock`, empty nested repository, unreadable file) or an `OSError` of the judge on a bundle file is
  a `REFUSED` verdict (`candidate_fault`, error recorded, cost kept, never replayed) instead of a void,
  replayable attempt; launcher/environment failures stay void. The reviewer's bundle no longer receives the
  implementer's `.claude/` files (`review_excluded` on the record). A cloud record refused on its `init` tool
  set is still audited (`outcome: contaminated` alongside the `tool_error` status, never replayed). Stated
  limits: the neutral harness's trajectory is not audited (mitigated by the deny-home profile); a local arm can
  signal the launcher (its profile starts from `(allow default)`; the void attempt is replayed at most once
  and listed). Documentation status (AGENTS.md R5): `pat-19-launcher-v1.md` and
  `pat-19-preflight-2026-10-05.json` updated; no CLI option of `foundry_cli.py`, product constant or routing
  table changed.

- Fixed (PAT-111, final real trials): the contamination audit no longer reads a token made only of slashes (`/`, `//`, the division operator of code) as the filesystem root: a legitimate Sonnet 5.5 run whose heredoc held `sum(values) / len(values)` was flagged `/` and would have been recorded `contaminated`. A bare `/` stays a path as an argument of a filesystem reader (see the review-round-2 entry). The final cloud argv (3 drivers) and the deny-home local profile (omp 18.4.10, mini-swe-agent 2.4.6 with `agent.step_limit=40`) are recorded as trial-run on 2026-10-05 in `pat-19-preflight-2026-10-05.json`; the audit is clean on those real streams.

- Changed (PAT-111): launcher corrections left by the PAT-108 reviews in `foundry.local_first_runner`
  (fake arms only; no real arm, no pinning, no `verified` flag changed). SIGTERM, SIGHUP and Ctrl-C are now
  converted for the whole duration of `screen` and `compare` (bundle build, judge, log reading, record
  writing included), not only while a driver runs: the cut attempt leaves an `interrupted` record and a
  `stop`; ledger and results writes are never cut in two; a second signal is never masked; a signal in
  the cleanup of a driver no longer skips the handler restoration or the profile removal; a settled local
  attempt not yet written is recorded as `interrupted`. SIGKILL and power loss remain uncovered (stated in
  `pat-19-launcher-v1.md`). The existing ledger is checked at start against the campaign, manifest and
  envelope digests (a launch refused at preflight no longer lets a modified configuration or envelope
  reuse the campaign id). The rule "economy unavailable as soon as a compared task is undecided" is
  labelled a launcher choice (`non_protocol_choices`). Local attempts are ledgered (`attempt_started`);
  `report` lists every attempt start or passed preflight without a settlement (`ledger.unsettled_starts`),
  warns on the screening (`selected` stays) and makes the comparison `inconclusive`, and refuses a
  comparison whose local candidate is not the one the screening selected (`compare` checks it at start
  when the screening results are in the same state directory; without them the report says so).
  Attempt names carry the launch rank, so a relaunch never overwrites the stream log of a killed attempt.
  A relaunch of `screen`/`compare` under the same campaign id, envelope, config and manifest now
  RESUMES (bounded rule validated by the maintainer on 2026-10-05, before any trial): decided attempts
  (a judge verdict) are skipped and never replayed; an attempt cut before any verdict (`interrupted` or
  `tool_error` without verdict, or an `attempt_started` with no settlement) is void and replayed once on a
  fresh bundle, recorded with `replay_of`; cloud money and the ledger/results invariant are unchanged (a
  void round keeps its cost, each session is named once); `report` lists `void_attempts` and `replays`
  and gives no `selected` while a screening task stays undecided (`undecided_tasks`).

- Changed (PAT-111, review round 1; fake arms only, no real arm run): (1) a cut AFTER a judge verdict or a
  review (local and cloud) now keeps them on the interrupted record, so the attempt is decided and a relaunch
  never replays it (no second chance after a verdict; a cut before the verdict stays void and is replayed
  once); a failure caused by the candidate (git configuration or attributes changed, tree still moving) is a
  `REFUSED` verdict (`candidate_fault`), not a void attempt; a cloud session killed after its settlement and
  before its record is listed in `report.void_attempts`; a contaminated review of path C leaves the task
  undecided with no takeover at first launch and at resume. (2) The unsandboxed cloud arms are documented for
  what they are (bare Claude Code, `bypassPermissions`, real home, open network, implicit credentials, no
  Foundry hook, not the Eiffel/Maigret definitions; the launcher protects neither the tracker, secrets nor
  merge for them): 19 best-effort Bash permission deny rules (`cloud_bash_deny`, data in the campaign config,
  literally in each cloud argv, enforced at load; command-prefix rules, evadable, not a sandbox); the
  post-run audit now covers COMMANDS (`command:` labels, `contamination.commands`), tool results and path
  resolution against the bundle with `cd` tracking (`find ~`, `cd ~ && cat .claude/x`, `src/../../..`); the
  launcher doc and the protocol amendment note are rewritten accordingly and the maintainer's explicit
  acceptance of the residual exposure (2026-10-05) is recorded. (3) Local arms now deny reads under the real home by default
  (`isolation.deny_home_by_default`, true; `isolation.allow_read_home`, empty) with the explicit deny list kept
  as a second layer; unit-tested on the generated profile and under a real `sandbox-exec` with a fake home,
  then tried on 2026-10-05 with the real `omp` 18.4.10 and mini-swe-agent 2.4.6. Also: the preflight refuses a loaded instance with no `modelKey`; the
  neutral harness is given `-c agent.step_limit={max_steps}` and `step_limit_hit` is also set a posteriori
  (attempt refused); `compare` refuses a C/N comparison with no screening results under its campaign id unless
  `--screening-campaign <id>` names a completed matching screening (read-only); the cloud evidence labels the
  command actually tried first (`--disallowedTools Agent` only: 11.6 / 16.3 / 16.5 s) apart from the final
  pinned argv, trial-run afterwards on 2026-10-05 (7.6 / 13.1 / 17.1 s). Documentation status (AGENTS.md R5): `pat-19-launcher-v1.md`, `pat-19-protocol-v1.md`,
  `pat-19-preflight-2026-10-05.json` and the campaign config updated; no CLI option of `foundry_cli.py`,
  product constant or routing table changed.

- Changed (PAT-111, pinning): the five real drivers of the PAT-19 launcher and the five local candidates are
  pinned in `docs/qualification/pat-19-campaign-v1.json` from real toy-task trials (not the corpus) made with
  the maintainer's confirmation of every model load and cloud call; the evidence is committed, sanitised, as
  `pat-19-preflight-2026-10-05.json`. Drivers now `verified: true` with an `evidence` reference (local omp
  18.4.10, neutral mini-swe-agent 2.4.6, Claude Code 2.1.285 for the current implementer `claude-sonnet-5-5`,
  the economy implementer `claude-haiku-4-5-20251001` and the reviewer `claude-opus-5-5`); a verified driver
  without evidence is refused at load. New launcher checks in `foundry.local_first_runner`: the preflight
  reads the loaded instance (`lms ps --json`) and refuses a context below the frozen 65,536, a different
  quantization or model key; `sandbox: false` is accepted only for a cloud driver with a reason and refused for
  any local driver (the cloud drivers run unsandboxed with the AGENTS.md R6 environment, real TMPDIR included,
  because Claude Code cannot authenticate under sandbox-exec); a cloud record is refused (tool error, spend
  kept in the ledger) unless the stream's `system/init` tools are a subset of the driver's `allowed_tools`
  (`Bash`, `Edit`, `Read`, `Write`: observed at the init event of Claude Code 2.1.285 with a 29-name
  `--disallowedTools` list; `--disallowedTools Agent` alone left 25 tools incl. `Task`, web and workflows), and `session_log.layout_verified` is now true on that condition (launcher reading equals the
  host's own usage on three real runs); a post-run audit of every arm's tool calls marks a record
  `contaminated` (undecided, never accepted, never replayed, listed in `report`) when it touched the plugin
  cache, another checkout of this repository or the user's configuration; the neutral harness executable comes
  from the operator variable `PAT19_MINI_BIN` at the pinned version, with its fixed environment, trajectory
  steps and no committed path. Candidates carry LM Studio key, engine, quantization, weights and chat-template
  digests, load command (`-c 65536`) and generation parameters (server defaults, not overridden); Devstral is
  declared unused. Limits stated, not enforced: the cloud and local arms expose different tool sets (recorded,
  not equalised), weights digests are recorded not recomputed; the `lms ps --json` fields (`modelKey`,
  `identifier`, `quantization` {name, bits}, `contextLength`) were observed on 2026-10-05 and are recorded in the
  evidence file (see the review-round-1 entry below for the default-deny of the home and the cloud
  exposure). Documentation status
  (AGENTS.md R5): `pat-19-launcher-v1.md` and `pat-19-protocol-v1.md` updated, no CLI option of
  `foundry_cli.py`, product constant or routing table changed.

- Added (PAT-108): the PAT-19 comparison launcher `foundry.local_first_runner`
  (`python3 -m foundry.local_first_runner {preflight,screen,compare,report}`, documented in
  `docs/qualification/pat-19-launcher-v1.md`, frozen config `docs/qualification/pat-19-campaign-v1.json`).
  It plays a task through three paths from PAT-107 bundles (A current cloud, B economy cloud, C one
  bounded local attempt then cloud review with path A taking over; plus a local-only neutral-harness
  attempt), reads premium tokens per class and role from the host session logs by the session id it
  hands to each cloud execution (unknown stays `null` with a reason, never 0), and records machine
  facts (swap, memory pressure, server memory). A mandatory authorization envelope caps cloud
  executions, tokens and duration (consumption is ledgered before each cloud execution); `screen` can
  never spend cloud; a failed machine preflight refuses to launch and the tool loads no model. The
  candidate runs under a generated `sandbox-exec` profile, a whitelisted environment and an isolated
  HOME; drivers not marked `verified` are refused for a real run (PAT-109 must pin them). `report`
  applies the protocol's pre-registered rules and returns compatibility, quality and economy
  separately, never a promotion. Only fake arms are exercised (dry run). No routing, mapping, role or
  default changed. Review round 1: an interrupted cloud execution (Ctrl-C, SIGTERM, launcher error) kills
  the arm's process group, and a `cloud_started` with no paired `settled` makes tokens unknown and refuses
  any further cloud execution; a local arm cannot read the user's configuration, plugin cache, SSH keys,
  other worktrees or the launcher inputs (explicit read-deny list, a campaign-config coordinate; not a
  default-deny of the home); the patch is taken against the root commit and shows new files; no replay of a
  recorded attempt; results carry `dry_run` and the sha256 of the campaign config, manifest and envelope
  and `report` refuses a mismatch; tool failures are `tool_error` records, an unreadable review is unknown,
  a stop on a cap is `inconclusive`; premium tokens stay unknown until a driver declares its session log
  layout verified; git on a bundle is neutralised and the judged state no longer lives in the candidate's
  tree. The protocol file was amended in place before any trial (candidate 2's repository, three
  downloads), validated by the maintainer on 2026-10-05: see the dated note at the top of
  `pat-19-protocol-v1.md`. `foundry.local_first_corpus` refuses symlinks under the product source
  prefixes; the manifest was regenerated by `verify` (12 of 12 unchanged). Review round 2: the ledger
  and the results can no longer disagree about spent work. An attempt cut by Ctrl-C, SIGTERM/SIGHUP or
  any exception leaves a `status: interrupted` record (premium total `null`) and a `stop` record, and is
  never replayable; each record names its cloud executions (`cloud_sessions`); `report` now requires the
  ledger beside the results and checks that every `cloud_started` is named by exactly one record and
  settled with known tokens: otherwise no economy verdict is given and the decision is `inconclusive`
  (inconsistent files are refused). A cap reached before a correction or a takeover is recorded
  (`stopped_by_cap`) and an undecided task gives no economy verdict; a sandbox configuration error is
  refused before any claim or reservation; signal handlers are restored exactly and a signal during the
  arm's start still kills it; the read-deny list gains credentials and shell history files and the
  generated profile is hidden from the arm; results are synced to disk and a truncated ledger or results
  line is a clean refusal. The only way out of a blocked campaign is a new campaign id and a new envelope.

- Changed (PAT-108): carry-over of the PAT-107 reviews in `foundry.local_first_corpus`: the judge purges
  every `*.pyc`/`__pycache__` of the candidate and redirects bytecode (`PYTHONPYCACHEPREFIX`), refuses a
  candidate inside the developer checkout and a bundle already judged, ignores virtualenv and
  `node_modules` directories the candidate created, and always passes `-c`; `verify` aborts on a tooling
  error instead of replacing the task; `replayability.origin_refs_containing` became a count
  (`origin_refs_containing_count`) so the manifest is reproducible. The 12 tasks are unchanged and
  still judged 12 of 12.

- Added (PAT-107): PAT-19 local-first qualification protocol v1 frozen under
  `docs/qualification/pat-19-protocol-v1.md` and a replayable corpus
  (`docs/qualification/pat-19-corpus-v1.md`): `foundry.local_first_corpus` freezes merged PRs in a
  committed snapshot (tracker statements from the committed, scrubbed
  `pat-19-corpus-statements-v1.json`, sha256 recorded), applies the protocol's inclusion/exclusion
  criteria with a recorded reason per PR (accent-insensitive title rules; manual include/exclude
  overrides with a mandatory reason), draws 6 comparison + 6 screening (`tamis`) tasks with a seeded
  sha256 ranking (the sets never overlap, and are not independent: see the corpus limits), builds a
  bundle as a brand-new one-commit repository at the base tree without the protected tests and with no
  link to the developer repository (no worktree, remote, alternates or merged SHA), and judges a
  candidate mechanically by restoring and running only those tests from the merged SHA in a scrubbed
  environment (temporary HOME, whitelisted variables, `--confcutdir`, REFUSED on harness-file edits or
  symlinks). The judge accepts the merged solution and refuses the base for all 12 drawn tasks. No
  model is run and no cloud/tracker call is made.

- Changed (PAT-106): the Linear ADR readback model recognises one more observed rendering
  (`docs/qualification/pat-106-linear-paragraph-list-observation.json`, native `PAT-ADR-0015`):
  a top-level `- ` list directly after a single-line paragraph line starting with a letter is read
  back with one empty line inserted between them; a list glued to any other non-heading line (pipe,
  `#tag`, digit, `**`, backtick...) is refused. Reads stay additive (the PAT-103 and pre-PAT-94 renderings
  are still accepted) and the pinned profiles are unchanged. A new body is now refused before
  any write, with a precise cause, for the unobserved neighbours: numbered, `*`/`+` or dash-only
  (setext) glued lists, lists glued in or next to a blockquote (the exact quote depth must match
  the line above; a `>` behind 4+ columns, a tab or a list marker, or opened directly under a
  list line, is refused), under a `--` line, under an indented line that is
  not inside a list item (including indented code starting with a marker), indented under a
  paragraph, indented (code) under a heading, thematic break or closing fence, or outside the
  exact shape; the check is a closed whitelist whose default refuses. Replaying `adr create` recognises the orphan Document of
  `PAT-ADR-0015` and writes only its witness. See `docs/linear-adr-body-guide.md`.

- Changed (PAT-103): the Linear ADR readback model and preflight now recognise the blank-line
  rendering observed by a bounded probe
  (`docs/qualification/pat-103-linear-blank-lines-observation.json`): two or more empty lines
  outside fenced code read back as one, a leading empty line and the final newline(s) are
  removed, fenced code is unchanged. A new body is refused before any write for unobserved
  neighbours (whitespace-only or CRLF blank lines, indented or quoted neighbours, a final
  newline after a fence or in an unclosed fence), for a body of empty lines only and for
  every table, whose delimiter row Linear rewrites; the same refusals now also apply before
  any write to the unchanged body of a migrated historical version 0 the model does not predict (status change, link,
  supersession). Pinned `PAT-72`/`PAT-86`/`PAT-16` profiles and the pre-PAT-94 model are
  unchanged.
  New guide: `docs/linear-adr-body-guide.md`.

- Fixed (PAT-101): residuals of the PAT-94 Linear ADR list preflight. A body that starts with an
  empty line next to a list is refused as a gap of two (the Document already adds one); task items
  (`1. [ ] a`, `1. [x] a`) are not plain items of the observed form; an interrupted supersession
  resume no longer refuses a dangling slot the model already validated (only its witness is
  written); the digest-pinned `PAT-72`/`PAT-86`/`PAT-16` profiles now apply before the strict list
  check, so a new ADR identical to a pinned source is accepted. No write path or format changed.
  See `docs/linear-tracker.md`.

- Fixed (PAT-102): residuals of the PAT-95 `close-epic` graph diagnostic. The "Seuls des
  reçus d'override valides bloquent" suggestion appears only when the strict refusal is itself an
  override refusal, otherwise it reads "Parmi les nœuds lus, seuls ...". The diagnostic captures
  the `SystemExit` a tracker (YouTrack) raises for a foreign node, keeps walking and falls back to
  the original refusal. `ValueError` is `foreign-project` only for an identifier or binding error;
  `JSONDecodeError`/`UnicodeError` are a `read-error`. "dérogé" is reserved for an override:
  a `dropped` node is "abandonné". Documentation fixes ("two issue reads", the `SKILL.md`
  cross-reference, one unwrapped line). No write behaviour, audit id or receipt format changed.

- Fixed (PAT-100): `close-epic` interruptions and refusals state the real closure state
  instead of a fixed "le reçu provider permettra la reprise". After a failure it reads,
  read-only, the provider audit and the local intent and says: no audit and an intent store
  actually read and empty (nothing written, re-run safe; any tracker whose intent store is
  not read, e.g. DevHub, is "intention locale non vérifiée", never safe; YouTrack's journal
  is read), intent without visible audit (ambiguous, a re-run is refused until the audit is
  visible, never reposts), audit pending (re-run with the exact `--accept-override` set of
  the audit, never the refused command), audit and Epic done (replay verifies), or state unreadable
  ("état du reçu inconnu", nothing claimed), with the audit id. Without a receipt it names the
  original cause (quota with remaining/reset and no immediate re-run, network/5xx with retry
  count, conflict, or exception type). A provider read error during the strict snapshot is
  intercepted like a conflict. Added `close-epic <EPIC-ID> --status` (read-only: `aucun audit`,
  `audit en attente`, `clos`; exit non-zero only on a failed read). No write, receipt, audit
  identity or replay rule changed. See `docs/linear-tracker.md` and `skills/close-epic/SKILL.md`.

- Changed (PAT-99): reading a closed Epic or taking a graph snapshot reads each node at most
  once per snapshot (`LinearTracker.graph_snapshot()`, used by `bounded_epic_graph_snapshot`
  and the PAT-95 diagnostic): N + 1 reads for N nodes instead of about 5 N (fake graph of 31
  children + 24 prerequisites: snapshot 269 -> 56, closed-Epic read 270 -> 57, diagnostic
  110 -> 55, whole `close-epic` 1118 -> 266). The pre-write S1 and the post-write
  verification stay independent fresh snapshots: no reuse across snapshots, calls or the
  write; closure semantics, receipts and fail-closed detection are unchanged. See
  `docs/linear-tracker.md`.

- Added (PAT-98): the Linear client retries a pure read at most 3 times (deterministic waits
  of 1 s, 2 s, 4 s) on a network failure (`transport_error`) or HTTP 500/502/503/504, never on
  data, binding or authorization errors, and never a write (PAT-ADR-0006 S2). A rate-limit
  failure (HTTP 429, a `RATELIMITED` code, or HTTP 400 with `x-ratelimit-requests-remaining`
  at zero) raises the new `LinearQuotaExhaustedError` carrying the remaining count and the
  reset time, without retry. The number of retries is exposed (`retries`, "after N retries"
  in the message). See `docs/linear-tracker.md` ("Transport errors, read retries and quota").

- Fixed (PAT-105): residuals of the PAT-98 review. A malformed error body, a non-string
  GraphQL error code or an aberrant reset header can no longer replace the typed error; 429
  is no longer in the retryable statuses; a zero remaining header on an HTTP 200 with an
  unrelated GraphQL error is no longer a quota; the quota message names `requests` or
  `complexity`; `tracker.retry_stats` counts retries, recovered and exhausted reads. The
  7 s bound covers waits only: with the 15 s attempt timeout the worst case is about 67 s per
  call. `tracker.rate_limit` and `retry_stats` are diagnostic-only across threads.

- Fixed (PAT-104): the test suite can no longer read or write the maintainer's real
  `~/.config/foundry`. A global autouse fixture in `tests/conftest.py` redirects `HOME` to a
  per-test temporary directory and clears `FOUNDRY_DATA`, `FOUNDRY_CONFIG` and
  `FOUNDRY_EXECUTION_RECEIPTS_DIR`; an audit hook fails any access to the real state
  directory, and `tests/test_state_isolation.py` asserts every resolver is sandboxed.
  Test infrastructure only, no production change.

- Added (PAT-95, PAT-ADR-0014): `close-epic` on Linear accepts the nominative flag
  `--accept-override=ID[,ID...]` (only with `--human-verdict=accepted`) to close an Epic
  whose nodes were delivered under a valid typed `acceptance-override` receipt. Refusals
  now list every non-positive graph node with its cause (read-only) instead of the first
  one, and the generic refusal no longer hides the real cause. The closure receipt binds,
  per waived node, its id, override-receipt digest and reason code (receipts without
  waiver keep their exact historical bytes); exact replay converges, a different set is
  refused, nodes stay `override`, the flag is neither CAS nor acceptance. YouTrack,
  GitHub Projects and DevHub refuse it. New public surface: the flag and
  `Tracker.epic_override_closure_supported`. The node list is carried by refusals of the
  fresh graph read only (not replay, pending-audit, provider-S1, unknown-id-alone or
  snapshot-transport refusals); a node that cannot be read is a `read-error` (sub-graph
  not traversed, the list is a lower bound, re-run first), `foreign-project` is reserved
  for binding refusals and `binding-error` marks a configuration error. Observable
  change: `query issue` `acceptance_coordinates` of a node under override now also carries
  `pr_url`, `head_sha`, `base_sha`, `review_digest`. The id grammar is
  `[A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,8}`.
- Fixed (PAT-94): the Linear ADR readback model now recognises the one observed rewrite
  of a spaced top-level ordered list (`1.` to at most `9.`, +1 per item, one plain LF
  line per item, one empty LF line between items: those empty lines are removed). By
  whitelist, a new ADR body (create, body edit, single historical import) holding any
  other empty line in a list context, or two or more empty lines just before or after a
  list-context paragraph (`PAT-72`: Linear collapses them after a list item), is refused
  before any write with cause `unsupported list Markdown`, followed by the first
  offending line number. Status changes, links, supersessions and `import_adr` relations
  re-use the stored previous body without the strict check only when the closed model
  verifies its stored bytes; a historical version 0 whose bytes only the migration probe
  proves is checked as a new body, so such an ADR with an unmodelled list shape is
  refused before any write instead of leaving an orphan Document; an interrupted
  supersession pair is recovered under the same rule. Other recoveries of an interrupted
  write (an existing exact slot) and the probe-pinned migration batch keep the
  pre-PAT-94 checks only. Reads accept the canonical bytes, the PAT-94 rendering or the
  pre-PAT-94 model output. A replay of an interrupted `adr create` recovers only the
  missing witness.
- Documentation only, 2026-10-04: Foundry 1.0.0 is published (tag `foundry-v1.0.0`,
  2026-10-03). The frozen 1.0.0 entry and release documents below keep their shipped
  "candidate" wording; the published state, installed-copy readbacks and the items not
  yet observed are in the
  [final report](docs/qualification/pat-62-final-report.md) and the
  [post-publication observation](docs/qualification/pat-93-post-publication-observation.json).

## 1.0.0 — 2026-10-03

Prepared release candidate; publication, official installation/upgrade and loaded
version/ref readbacks remain pending. See [release notes](docs/release-1.0.0.md)
and [setup/migration](docs/migration-1.0.0.md). This dated source entry is not a
claim that 1.0.0 is already published or installed.

### Added
- The V1 distribution packages for Claude Code and Codex, both declaring 1.0.0,
  based on accepted PAT-61 (PR #72, merge `16cdaa0`). Its six qualified source
  configurations cover YouTrack, Linear and private personal GitHub Projects on
  both hosts; historical producer refs and unknowns remain unchanged.
- Short per-repository setup, official dual-host upgrade from 0.9.0, explicit
  legacy YouTrack binding upgrade, loaded-version verification, and configuration
  rollback limits. Installing a package performs no tracker migration.
- An explicit compatibility matrix with unchanged Ship-iOS 0.3.0 and its portable
  V1 selection/release-scope capability probes; no App Store effect is included.
- The qualified private personal GitHub Projects adapter now carries Foundry-owned
  in-progress/review/done and acceptance receipts bound to the exact repository,
  Project, Issue, item and PR generation. A live private sandbox PR passed independent
  review and exact-SHA CI before merge; native auto-close alone grants no authority
  (PAT-67).
- `query changelog <release>` now emits `foundry.release-scope.v1` from exact
  repository-scoped release mappings on YouTrack, Linear and GitHub Projects. The
  payload keeps Ship-iOS's `milestone`/`count`/`groups` projection and adds factual
  accepted, deviated, unfinished and unavailable classes; a native Done or PR mention
  alone is never delivery proof (PAT-59).

### Fixed
- R1 remains closed on invalid, drifted or ambiguous tracker binding (PAT-42),
  including before forbidden PR/merge/default-branch push commands can execute.
- YouTrack delivery receipts now distinguish accepted, deviated and unavailable
  release facts (PAT-82); older terminal issues without qualified receipts remain
  unavailable, without fabricated backfill.
- Proof-bound `openpr` and `merge` now pass the original predecessor state to
  bounded-state adapters, so a GitHub Projects PR can resume after a partial
  transition without weakening the common transition guard (PAT-67).
- YouTrack now proves a targeted issue or ADR's native project coordinate before every
  write and rejects it when any alias tombstones that provider project (PAT-43). The
  guard runs before milestone setup, command links, body writes, comments, and
  project-explicit issue or ADR creation, so an unrelated or unregistered checkout
  cannot write to the readable archive.

### Changed
- Bounded targeted grooming writes on YouTrack, Linear and GitHub Projects and
  audited non-atomic Epic closure carry PAT-ADR-0006's named S1→S2 overwrite risk;
  read/write/readback is detection, not provider CAS or concurrency exclusion.
  Linear delivery and AC authority remain proof-bound; native checkbox replacement
  remains refused. GitHub ADR Issue supports use an integrity-checked version chain.
- The delivered PAT-15/PAT-16 technically qualified defaults: Claude Haiku 4.5
  without effort, Sonnet 5.5/medium and Opus 5.5/high for frontier/apex; Codex
  GPT-6 Luna/low and GPT-6.1 Sol/medium/high/max. Minimum Claude client 2.1.284 for
  the complete set, actually tested 2.1.285/firstParty/Pro. Existing pins remain
  exact; observed access, effort and source/install limits are in the release notes.
- Package descriptions now announce only qualified provider variants. DevHub remains
  optional outside V1; Fable/Astra and economic superiority are not promoted.
  PAT-17/PAT-18/PAT-19 remain outside V1. Historical 0.9.0 evidence is preserved.

## 0.9.0 — 2026-09-26

### Added
- A fail-closed Linear tracker adapter using explicit repository, team, project,
  workflow-state, label, and milestone identifiers, resolved from the actual checkout's
  canonical Git remote rather than an environment alias or basename. It supports strict
  issue reads, creation, and non-replacing relations/comments without promising
  exactly-once delivery. Existing issue field/state/parent/body replacement stays
  typed-unavailable because Linear exposes no atomic anti-overwrite precondition.
  `issue openpr` and `issue merge` instead use deterministic-ID, append-only comments;
  Foundry validates and projects their PR/state/AC receipts without changing native
  Linear fields. Complete cockpit Evidence Plane envelopes are append-only advisory
  audit records and never state, AC, CI or merge authority. Linear stores ADRs as
  versioned, witness-bound project Documents (PAT-22); atomic Epic closure remains
  unavailable; this repository is cut over to Linear project PAT (PAT-10).
- `query issue` reports an explicit `conflict` status instead of presenting a
  conflicted embedded ADR index as empty, so a caller can tell "no ADR" apart from
  "the index disagrees with itself" (PAT-41).
- Tolerant Markdown serialization handling for Linear's own rendering of the ADR
  delimiter and angle-bracket content, so its reformatting never masks a real
  alteration, while byte-exact source bodies stay verifiable through witnesses
  (PAT-37/PAT-38/PAT-39).
- `plan_adr_batch_qualification`, a read-only planner that performs no provider write
  and returns the exact version Document and witness bytes, and probe titles, a
  historical batch import would create; the import itself reads every non-authoritative
  qualification probe by ID and checks its complete readback before any effect
  (PAT-40).
- Explicit `missing_relations` on the records of a partial-source historical ADR
  manifest, so each unknown relation family is listed in a canonical, sorted tuple
  rather than omitted or presented as silently empty, and the audited import of the
  repository's 27 historical ADRs into Linear (no private data), which declared all
  three families unknown (PAT-23).
- A repository-scoped `.foundry/tracker.json` marker that takes precedence over the
  host-global tracker default, a `registry cutover` command that binds a project by its
  exact manifest digest, an archive tombstone recording the former YouTrack binding, and
  an operations log capturing `adr_authority` and `incidents` for the cutover (PAT-10).
- A typed `acceptance-override` receipt for a human AC override merge on Linear, with
  recovery replay so an interrupted override merge can resume from its exact receipt
  instead of re-deciding the override (PAT-49).
- Public-repository main-branch protection, read back from the actual GitHub branch
  protection rules rather than assumed (PAT-12).
- A dedicated, audit-bound `rearm-remediation` transition for a just-exhausted bounded
  correction window that is not yet halted. It CAS-binds the issue, recorded role,
  original halt generation, controlled human reason, and 1..3 new credits; preserves
  counters, tier floors, and every consumption event; appends a public link to the
  exhausted window; and exposes identical state through Claude and Codex without
  weakening ordinary halted-only resume.
- An outbound DevHub command worker with exact claim/heartbeat bindings, bounded live
  leases, command-ID ambiguity recovery, monotonic local receipts, last-moment gate and
  authority revalidation, and a concrete Claude execution path whose model floor and
  dollar ceiling are fixed before the effect. A host-shared SQLite ledger now reserves
  observed budget and concurrency atomically across worker processes before launch,
  settles terminal cost durably, and keeps ambiguous/crashed allocations fail-closed.
  The command credential remains limited to claim/event calls and is removed from the
  spawned runtime environment.
- A deterministic, read-only Epic execution preview over Dev Hub's complete versioned
  subgraph. It fails closed on truncated, inconsistent, cyclic, or schema-invalid
  projections; classifies eligible, blocked, omitted and human-gated work; derives
  concurrency-capped dependency waves; resolves planned model tiers with risk floors;
  and binds the snapshot, effective policy, limits, expiry and unknowns in one stable
  digest. The preview exposes no branch, agent, command, budget reservation, tracker
  mutation, merge, or other execution authority.
- A dedicated provider-neutral non-code Epic closure command and Tracker contract. Its
  receipt binds the exact project, parent version and AC, complete canonical child
  id/version/state set, timestamp, and nonce; a supporting provider must verify and
  audit the graph atomically and expose the receipt for interruption recovery. The code
  issue merge-receipt gate is unchanged. DevHub Tracker v1 and YouTrack explicitly
  report this capability unavailable rather than emulate it with a racy read/write.
- An opt-in DevHubTracker v1 provider with normalized project, issue, link, comment,
  and ADR operations; bounded HMAC receipts for review/done/ADR transitions; versioned
  idempotent writes; repository-bound mutation guards; hard-capped pagination; sanitized
  public audit verification; and an explicitly destructive real-provider smoke covering
  optimistic concurrency, replay and link/comment round trips. Mutation binding compares
  the actual credential-free Git remote identity to `canonical_repo`; review and AC
  proofs use GitHub's exact immutable PR base SHA and reject a stale coordinate; doctor
  validates the transport from a narrow public-config read before parsing or reading
  DevHub credentials. Configuration and registry writes validate and canonicalize URLs
  and repository identities before persistence, while legacy unsafe values stay redacted
  and fail closed before transport. An already-done retry
  performs cleanup without a second merge or illegal rewind. YouTrack remains the
  default and no routing, gate, review, or merge authority moves to Dev Hub.

### Fixed
- Linear lifecycle fixes: blocking premature GitHub closure of a Linear-tracked issue
  (PAT-26) and tolerating a Backlog-to-In-Progress transition after a PR is already
  attested (PAT-28).
- Tracker-neutral escalation and bounded-remediation fixes: controlled resumption after
  exhausted technical diagnostics (PAT-27); rearming an exhausted window after a
  technical diagnostic generation (PAT-29); making remediation usable after a review on
  an already-consumed technical route (PAT-30); allowing exactly one new review after a
  credited correction on such a route (PAT-31); and rearming a corrected review across a
  PR base advance (PAT-32).
- Documented and tested contracts without a product-code change: resuming the Linear
  cutover after technical remediation without widening its permissions (PAT-21), and an
  offline test double proving the provider handoff stays idempotent and capability
  stays valid after a crash (PAT-24).

## 0.8.1 — 2026-09-13

### Added
- `query profile <workflow>`, a bounded query projection for `next-issue`, `roadmap`,
  `blockers`, `groom`, and `intake`. `next-issue` now costs 29 761 bytes instead of the
  138 165 bytes of the previous `query backlog` call it replaced for that workflow.
- `cost-attribution`, an offline host-log cost attribution command reading a versioned,
  dated price grid. PROVENANCE is aligned on FOUNDRY-ADR-0008, exposes `billing_mode`,
  and aggregates by epic, issue, project, and model.
- Cost per position within a session and session comparison curves, plus
  `plan_usage_percent` on Codex.

### Fixed
- A Claude host log containing records without a reasoning breakdown is priced instead
  of aborting the whole log; `reader_completeness` reports what is affected.
- Codex cost over-counting: `total_token_usage` is read as cumulative per session, not
  summed per message.

## 0.8.0 — 2026-08-29

### Added
- A structured acceptance-criteria proof for the pre-merge gate. The reviewer returns
  ordered AC outcomes and one quality verdict; Foundry binds them atomically to the
  current issue AC digest, local `HEAD`, exact base diff, active review claim, and
  reviewer generation. Stale, malformed, mixed, or legacy evidence fails closed. The
  explicit human `--allow-incomplete-ac` path remains audited and never edits tracker AC.
- The versioned FOUNDRY-46 dual-host benchmark evidence and FOUNDRY-47 publication
  decision. The 108-coordinate v2 matrix remains separate from product activation:
  Claude and Codex are both `inconclusive`, every unavailable cost/downstream/test/
  review/allocation field remains `null`, and the unweighted cross-host aggregate is
  `null` and forbidden.

### Changed
- Published one shared Foundry implementation as two aligned 0.8.0 host packages. The
  Claude and Codex catalogues keep their supported source-pointer schemas and therefore
  deliberately contain no invented `version` field; version authority remains in the
  two package manifests.
- Updated install, upgrade, configuration, doctor, host-override diagnosis, source
  verification, and exact-ref rollback guidance for both Claude Code and Codex.

### Unchanged production contract
- The recommended ordinary main profile remains Sonnet 5 / medium on Claude Code and
  GPT-5.6 Terra / medium on Codex. Foundry cannot force the already-open main
  conversation. Reviewer stays at least frontier/high, architect at least apex/high,
  and the existing maximum of two tier increases per issue remains in force.
- No model, effort, context policy, route, default, gate, fallback, escalation signal,
  or local authority changes in 0.8.0. No local candidate is promoted. The historical
  7.42% price-snapshot observation is noncausal and is not a savings claim.

## 0.7.0 — 2026-08-22

### Added
- An operator-controlled local preprocessing provider, disabled by default and confined
  to an explicit loopback-only OpenAI-compatible endpoint. The local model is untrusted,
  evidence-bound, read-only, outside Foundry's roles and tiers, and can never approve a
  gate, write to the repository, select a cloud model, or gain merge/release authority.
- Shared `diff`, `logs`, and `tests` capture modes with immutable bounded snapshots,
  sensitive-path filtering, secret redaction, typed locators, wrapper-owned evidence,
  stale-input detection, and strict response-schema validation. The two-pass `code` mode
  exposes a body-free manifest first, then a frozen evidence bundle; it cannot add an
  adaptive third pass.
- Trusted local-scout policy for explicit model and loopback destination selection,
  conservative 2-second connection and 8-second total defaults, hard 10/120-second
  ceilings, project limits that may only tighten budgets, and an opt-in
  `cloud_economy` failure plan. Policy violations and stale input always stop; the
  fallback never performs the cloud call itself and remains subject to the production
  ADR-0006 scout route.
- A read-only doctor view for local preprocessing. It reports
  `disabled`/`configured`/`available`/`unavailable`/`invalid policy` using at most one
  bounded loopback TCP connection and never completes a model or reveals its endpoint,
  identifier, or secrets.
- Passive opt-in routing telemetry with private one-time completion/outcome capabilities
  and a bounded append-only local journal. It is fail-open, never uploads, never records
  prompts, responses, code, paths, URLs, free-form errors, or secrets, and never feeds a
  current or future routing decision.
- The frozen, candidate-agnostic FOUNDRY-35 benchmark campaign, including raw evidence,
  aggregate results, a deterministic post-run addendum, attestation, nullable cost
  semantics, and offline reproduction. No local candidate is promoted: all four failed
  the mandatory product-schema and downstream-correctness gates, achieved only about
  0.32% median cloud-input reduction against a 40% threshold, and had no applicable
  immutable public price, so all cost reductions remain `null` rather than zero.

### Changed
- Model, reasoning effort, and context policy are documented and observed as separate
  dimensions while the production resolver remains exactly the ADR-0006 tier mapping.
  There is no adaptive routing, reasoning ladder, production shadow call, or new routing
  signal in 0.7.0.
- Issue escalation can expose a human-authorized remediation window of one to three
  credits after the existing stop. Credits apply only to the exact stopped correction
  role and `review_blocking_after_fix` signal, are durably audited with controlled
  values, and do not renew the two-escalation cap, lower a floor, or widen capability.
- Claude's logical-agent façade now matches the real Agent wire: short provider aliases,
  dynamic `model` and `max_turns`, static effort-only execution profiles, strict bounded
  task packets, current availability normalization, and explicit host-override warnings.
- The recommended main coordinator remains the balanced profile: Sonnet 5 / medium on
  Claude Code and GPT-5.6 Terra / medium on Codex. Reviewer and architect retain their
  frontier/high and apex/high-or-max floors and upward-only fallback.
- Installation, upgrade, 0.6.x migration, dual-host configuration, architecture, local
  privacy/fallback behavior, benchmark results, limits, and the next required experiment
  are now collected in the README and the 0.7.0 migration/release guides.

### Fixed
- Wired opt-in passive telemetry to Claude's real post-Agent callbacks and Codex's
  required post-spawn skill lifecycle, with private bounded one-time capabilities,
  consume-time 24-hour expiry, one cross-process lock for atomic bounds/replacement/
  consumption, replay/forgery and symlink resistance, correct fallback/override
  counters, and recovery from interrupted append/capability fragments. Claude's real
  hook is now entirely silent and records only its coarse callback status; Codex skills
  must pass an explicit truthful success/failure/timeout/cancellation/unknown class.
  Observation cannot change route, packet, spawn, retry, escalation, gates, results, or
  provider behavior.

## 0.6.2 — 2026-08-20

### Fixed
- Removed the redundant Claude manifest declaration of the conventional
  `hooks/hooks.json` file, which Claude Code already loads automatically and rejected
  as a duplicate during real 0.6.1 installation. The dual-host validator now models
  Claude's automatic hook discovery and optional additional hook file independently
  from Codex while preserving path confinement and semantic-divergence checks.

## 0.6.1 — 2026-08-20

### Added
- An explicitly opt-in real YouTrack smoke test covering the public issue/link/state/
  comment/ADR cycle, with disposable-project guards, per-run audit markers,
  secret-safe diagnostics, provider-local idempotent cleanup, and a separate
  variable-gated CI job.
- A host-neutral semantic model-routing contract with four tiers and five role
  defaults, project and user overrides, field-level precedence, visible downward
  fallback for ordinary roles, and upward-only fallback for reviewer/architect gates.
- A read-only `routing show`/`routing resolve` command, atomic cross-host review
  deduplication keyed by the exact diff hash, and a value-free common warning contract
  for host overrides that can neutralize the resolved policy.
- Claude Code logical roles for scout, implementer, reviewer, and architect, dynamically
  rewritten at Agent invocation time so project/user model policy survives static plugin
  frontmatter. Bounded task packets and turns, static effort-only execution profiles,
  explicit availability fallback, and a fail-closed Bash guard preserve gate/read-only
  guarantees.
- A Codex invocation façade that produces exact fresh-context subagent descriptors with
  resolved model and reasoning effort, explicit availability fallback, value-free
  profile-override warnings, common diff-hash review deduplication before spawn, and a
  disclosed current-context fallback when subagents are unavailable.
- Deterministic cross-host escalation state keyed by repository, issue, and role: only
  red tests/blocking reviews or explicit risk/ADR/user signals count; floors persist for
  the issue, two tier increases are allowed, and further work halts for a human. Dynamic
  floors cannot be demoted by direct model/tier requests or availability fallback and
  never expand agent capabilities or external authority.
- A CI-pinned resolved-invocation contract, minimal project-routing example, documented
  role/primary-profile/host limits, and frozen manual `FOUNDRY-MR-PILOT-v1` worksheet
  for measuring main-loop and subagent cost without automatic telemetry.

### Fixed
- Milestone rollups and dependency analysis now treat Foundry `done`/`dropped` and
  YouTrack `Fixed` as terminal without depending on provider capitalization, while
  changelog generation remains restricted to work explicitly marked `done`.
- Fall back from GitHub's unavailable aggregate check-runs endpoint to paginated
  check-suite runs without weakening or waiving the merge gate.
- Repo renames can now add a safe registry alias without provisioning a duplicate
  tracker project; `doctor` groups aliases by distinct project identity.
- Bootstrap ADR import requires and is idempotent by filename ID rather than mutable
  title, and preflights unnumbered files and numbering gaps before any tracker write,
  preventing shifted ADR IDs.

## 0.6.0 — 2026-07-21

### Added
- Native Codex packaging (`.codex-plugin/plugin.json`) beside the unchanged Claude Code
  package, with one shared set of skills, hooks, tooling, tests, and release history.
- `foundry:configure`: host-neutral non-secret configuration, interactive macOS Keychain
  token storage, redacted diagnostics, and an install marker sibling plugins can resolve.
- Portable `foundry:review-pr` skill mirroring the Claude reviewer agent's two-stage
  acceptance-criteria + quality gate.
- Linked-worktree-aware issue start/merge behavior for Codex-managed worktrees.

### Changed
- Skill commands use Claude's deterministic `CLAUDE_PLUGIN_ROOT` expansion when present,
  with a `SKILL.md`-path fallback required by Codex (which exposes plugin-root variables
  to hooks, but not to skill-initiated shell commands). Docs and hook guidance expose
  both Claude `/foundry:*` and Codex `$foundry:*` invocation forms.
- Configuration now resolves direct environment variables, Claude plugin options,
  Keychain, then the Foundry config file. Registry data lives in one shared path —
  `FOUNDRY_DATA` when explicitly set, else `~/.config/foundry` — and deliberately
  ignores the hosts' plugin-private `PLUGIN_DATA`/`CLAUDE_PLUGIN_DATA` dirs: hosts
  inject those into hook commands but not skill commands, which would split the
  registry into two silently diverging views.
- Restored non-empty Claude Code argument hints after verifying that Codex 0.144.6
  installs and invokes skills carrying the unknown key without a related warning; the
  shared validator now accepts only short, quoted, one-line hint strings.
- `SessionStart` hook matcher covers `resume` too: a resumed session gets the Foundry
  contract injected exactly like a fresh one.
- `issue merge` now reports the squash SHA that landed on the default branch
  (`sha mergé <sha>`): release tooling (ship-ios) tags exactly that commit, and
  `origin/<default>` would be racy if another PR lands in between.

## 0.5.0 — 2026-07-02

### Added
- **`query changelog <milestone>`** — the shipped (`done`) issues of a milestone,
  grouped by type, as locale-neutral facts (id / title / type / labels). It is the
  clean SOURCE a release tool consumes to write user-facing release notes per target
  locale; Foundry emits facts, never prose, never a hardcoded language. Platform- and
  locale-neutral: any project cutting a version wants "what landed in vX". `dropped`
  issues are excluded (they never shipped).

## 0.4.1 — 2026-07-02

### Fixed
- `list_adrs`: the ADR prefix match is ANCHORED and escaped (`re.match` +
  `re.escape`) — a project key that is a suffix of another ("TOC-ADR" inside
  "MDTOC-ADR-0001") cross-matched and polluted the index/numbering. Found by
  external blank-context review (F1); suffix case pinned by test. The
  instance-wide article scan and its 1000 ceiling are documented inline
  (proper server-side scoping tracked in #17; secret-gated adapter smoke in #18).

## 0.4.0 — 2026-07-02

### Changed (token posture: summary first, detail on demand)
- **Lean list payloads**: `query backlog`/`candidates` no longer carry issue bodies
  (fields, links, AC counts, ranks and statuses carry the reasoning signal); the
  free text comes via `query issue <ID>`. In `query issue`, related issues are lean
  too — only the requested issue keeps its body and progress notes.
- **ADR index + drill-down**: `query adrs` returns id/title/status/ref only; new
  `query adr <ADR-ID>` returns one full ADR. Retrieval-before-reasoning becomes
  constant-cost: scan the index always, load only the ADRs touching the topic.
  The `adrs` section of `query issue` is the index as well.
- Skills updated to the index → detail pattern (frame, intake, adr, blockers,
  roadmap, start-issue, resume-issue; groom and intake's refine path explicitly
  drill down before ruling on AC text — never judge unloaded text).
- **FOUNDRY-ADR-0003** records the decision and amends ADR-0001's "JSON riche" /
  "on charge tout" clauses (inline notes + `amended_by`), matching the ADR-0002
  precedent — the lean posture no longer silently contradicts the founding ADR.

### Hardened in review
- `query issue` tolerates unreachable related issues (deleted / cross-project →
  marked entry instead of a crash) and dedupes link targets; `query adr`/`issue`
  without an id exit with a clean usage line; `query adrs <ID>` (the natural
  near-miss) delegates to the drill-down; the ADR miss path no longer re-fetches
  the whole knowledge base; SessionStart contract teaches index → detail.

## 0.3.1 — 2026-07-02

### Fixed
- `ci_expected` no longer deadlocks the `--allow-no-ci` waiver: a stale
  queued-empty check-suite (schedule-only workflows keep one forever) doesn't
  count as active CI — only suites in progress, with runs, or queued for less
  than 15 minutes (the post-push window) refuse the waiver.
- `issue merge` no longer recommends `--allow-no-ci` in the very message that
  refuses it.
- Docs aligned with the gate's reality: merge-pr skill (two sources, ≥1 success),
  CodeHost port header (three methods), waiver wording (active-CI signal, not
  "no suite exists"), issue CLI docstring; ADR-0001 ↔ ADR-0002 now cross-linked
  (`amends`/`amended_by` + inline note on the amended clause); ADR-0002 documents
  that legacy-only CIs have an undetectable post-push window.

## 0.3.0 — 2026-07-01

### Added
- **CI gate reads both sources** (ADR-0002, closes the Jenkins blind spot): new
  `CodeHost.commit_statuses()` (legacy status API, paginated) merged with check-runs
  in `ci_gate` — a red legacy status blocks even with zero check-runs, and
  `--allow-no-ci` waives only when BOTH sources are empty AND no active check-suite
  signals CI for the commit (`CodeHost.ci_expected` — the post-push window where CI
  is queued but invisible is not waivable). Pagination of both reads terminates on the
  response's own `total_count`, never on batch size. Port change: code-host
  adapters must now implement `commit_statuses` and `ci_expected`.
- **FOUNDRY-ADR-0002** — the gate semantics, decided and written down: green means
  proven green (≥ 1 `success`, nothing red/pending, `neutral`/`skipped` tolerated
  alongside, never alone); amends ADR-0001's literal "all success" clause.
- **`rank_in_view`** in query payloads: dense 0..n-1 rank over the returned view,
  alongside the global (gappy-when-filtered) `rank_index`.

## 0.2.0 — 2026-07-01

### Added
- **Plugin hooks** (`hooks/hooks.json`) — the process becomes active without invoking a
  skill, in registered repos only:
  - `SessionStart` injects the Foundry contract (PR/merge only through the pipeline,
    ADRs settled unless superseded, which skills to use) into every session.
  - `PreToolUse` (Bash) denies `gh pr create` / `gh pr merge` and `git push` targeting
    the default branch — explicit refspec or bare push from it. The anti-rules are now
    mechanisms; unregistered repos are untouched and the guard fails open.
- **Two-stage pre-merge review**: the `reviewer` agent first verifies each acceptance
  criterion against the diff (covered / not covered / contradicted), then does the
  quality pass; `merge-pr` feeds it the AC and blocks on either stage.
- **Mid-flight memory**: `Tracker.add_comment` + `edit comment <ID> < note.md`,
  recent progress notes surfaced in `query issue` (`comments`), and a new
  `/foundry:resume-issue` skill that reconstructs tracker + git + PR state and
  continues without a human re-briefing.
- `setup_project … --import-adrs <dir>`: replicates repo bootstrap ADRs into the
  tracker KB, idempotent — closes ADR-0001's "répliqué en KB au provisioning".
- **Skill linter in CI** (`tests/test_skills.py`): every SKILL.md command must start
  with a binary its `allowed-tools` covers (no env-var prefixes, no `echo … |` pipes,
  no `${N:-…}`), and foundry invocations must go through the launcher.

### Hardened in review
- Guard: shlex tokenization (quoted text never trips it; `git -C`/`gh -R` global
  flags can't bypass it), `+main`/`--all`/`--mirror`/`--repo=` forms denied,
  {main, master} protected when the default branch is undeterminable, repo identity
  from the git remote across ALL trackers, cheap prefilter before any IO.
- CI gate: `passed` now requires at least one check-run concluding `success` —
  all-`skipped`/`neutral` (path-filtered workflows) proves nothing and refuses.
- `openpr` derives the issue id only from the `<type>/<ticker>-<n>-…` branch shape;
  head-moved detection matches `HTTP 409`, not any '409' substring; `import_adrs`
  dedups by exact title; SessionStart inlines the real launcher path and skips
  `resume`; docs aligned (keychain claims, Milestone enum, registry shape).

## 0.1.1 — 2026-07-01

### Fixed
- `query`: `blocked_by`/`unblocked` computed over the full graph before status
  filtering (a `ready` issue blocked by an `in-progress` one was reported unblocked);
  `milestones()` populates `blocked_ids`; a known issue with an unset State still
  blocks its dependents.
- YouTrack adapter emits normalized `Link.type` (`depends-on`, `blocks`, …) per the
  `models.Link` contract; the query tier keys off normalized forms.
- CI gate: zero check-runs refuses (empty proves nothing green); `--allow-no-ci`
  waives the zero-check case only, decided inside `ci_gate` (`waived` marker);
  merges are pinned to the gated sha (409 if the head moved); `check_runs` paginates.
- Skills invoke `python3 …/tooling/foundry_cli.py` (launcher) so commands match
  `allowed-tools: Bash(python3:*)` prefix rules; `< file` redirects instead of pipes.
- Assorted: scp-style ssh aliases in `parse_owner_repo`, strict CLI flags, issue id
  derived from the branch in `openpr`, `$top` on YouTrack list endpoints, dirty-tree
  guard in `start`, `.githooks/pre-push` shipped, marketplace source `./`.

## 0.1.0 — 2026-07-01

- Bootstrap: pipeline (frame → roadmap → next/start/open-pr/merge-pr → intake),
  query/write tiers, YouTrack + GitHub adapters (+ ghprojects stub), registry,
  reviewer agent, pure-logic tests, own-method dogfooding (FOUNDRY-ADR-0001).
