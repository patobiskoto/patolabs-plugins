# Foundry 1.1.0 — errata

This page corrects four points of the 1.1.0 release material without rewriting it.
[release-1.1.0.md](release-1.1.0.md), [migration-1.1.0.md](migration-1.1.0.md) and the
1.1.0 section of the CHANGELOG are frozen as shipped in the tag `foundry-v1.1.0`
(`04af3595b0f76f760f618867f2a2f4e488e6a904`); their digests are pinned in
`tests/fixtures/release-history.json`. This page is not frozen and may be amended.
The line numbers below are those of `docs/release-1.1.0.md` at the tag. Nothing here
changes a behaviour: the code under `tooling/` is untouched.

## 1. The `--dry-run` sentence of the PAT-19 launcher is incomplete

The note says that `--dry-run` "accepts only drivers marked `fake` and uses canned
machine facts for its preflight; it is not a no-op: it still executes those fake
drivers, and it does not apply the sandbox unless `--sandbox`" (lines 288 to 291). That
is true but incomplete. On the path read for this page (`main` then `Runner` in
`tooling/foundry/local_first_runner.py`), a `--dry-run` of a `screen` or `compare`
attempt also:

- builds a real git bundle for each attempt, through `Runner._bundle`, which calls
  `lfc.build_bundle` (`local_first_corpus.build_bundle`) on the repository given with
  `--repo`, then removes it afterwards (`Runner._discard`);
- runs the judge on that bundle, through `Runner._judge`, which calls `lfc.judge`
  (`local_first_corpus.judge`); it is reached after the fake driver, from `Runner.local_attempt`
  and from `Runner.cloud_path` alike;
- writes records marked `"dry_run": true` to the state directory: the ledger
  (`Ledger.append`, file `ledger-<campaign id>.jsonl`) and the results
  (`Runner._emit`, file `results-<campaign id>.jsonl`). The two files refuse to mix
  dry-run and real records (`RunnerError` "refusing to mix dry-run and real records").

What the sentence already says is confirmed by the same path: `_check_driver_usable`
refuses a driver that is not `fake` under `--dry-run`, and `main` passes
`sandbox=(not args.dry_run) or args.sandbox` and `run=(lambda _argv: None)`.

Not verified here: a dry run of the verbs other than `screen`, `compare`,
`screen-exploration` and `compare-exploration`, and the exact set of files a given
campaign configuration produces.

## 2. "Killed at twenty minutes" is a bound, not a duration

The note says that the native trial tool starts its subagents "killed at twenty
minutes" (line 256). Twenty minutes is the upper bound, not what a run lasts:
`TIMEOUT_SECONDS = 20 * 60` in `tooling/foundry/claude_profile_trial.py` is passed as
`process.wait(timeout=TIMEOUT_SECONDS)` in `_launch`. A session that ends earlier ends
earlier; only a session still running at the bound is killed (the whole process group,
`SIGKILL`), and the result then records `"timed_out": true` with the measured seconds.
Both modes (`run`, `run_cache_ttl`) use `_launch` by default and record the same bound
under `bounds.timeout_seconds`.

## 3. Three lines of the note are not wrapped

Three prose lines of `docs/release-1.1.0.md` exceed the wrapping of the rest of the
page. They are **not corrected**, because the page is frozen:

- line 32 (110 columns): "Read this section before upgrading. Each item gives what
  happens and its remedy, as far as one is established.";
- line 266 (158 columns): "and status of the checkout with `git`); it creates nothing
  under the work directory. That is the current behaviour, since PAT-134: ...";
- line 297 (114 columns): "exhaustive: pilots and bounded trials are recorded in the
  CHANGELOG as well. Loading is done outside the launcher,".

How these three were chosen: the review remark that raised them is recorded on PAT-135
only as a count, not as a list. They are the only lines of 110 columns or more outside
tables (`awk 'length>=110 && !/^\|/'` on the file at the tag prints exactly these
three). The next longest prose line, 409, has 104 columns. Whether these are the three
lines the reviewer meant is not verified.

## 4. Returning to 1.0.0 through an official manager: not tried (non essayé)

The migration guide says "To be confirmed once 1.1.0 is tagged" (line 160). The tag now
exists. The return to 1.0.0 is **not tried** (non essayé): no command for it has been
run, and none is established by this page.

- Reason: the maintainer did not authorise the attempt on 2026-10-10, because it
  changes the plugin installed on the maintainer's workstation while the observation
  window of PAT-134 is open and a session is running.
- When it can be tried: when that window closes (10 issues delivered, or 2026-11-09),
  on the maintainer's explicit agreement, through an issue of its own. Until then the
  guide's wording stands, and the READMEs say the same.

## Other statements made stale by the tag

The release note and the migration guide still read "not tagged, not verified
installed" and "No tag exists for this version at the time of writing". The tag
`foundry-v1.1.0` exists; the per-host state is in the root `README.md` and in
`plugins/foundry/README.md`, and the frozen text is not rewritten.
