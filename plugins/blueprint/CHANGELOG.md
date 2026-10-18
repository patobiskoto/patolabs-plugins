# Changelog

## Unreleased

## 0.3.1 — 2026-10-18

Settles the non-blocking remarks deferred from the reviews of PAT-252, PAT-253 and PAT-260 (PAT-259). No new verb.

- `decompile`: `--max` must be at least 1 and `--budget` a positive finite number (exit 2); `--analysis` also compares the
  target's sha256 with the inventory without launching a tool (exit 5 on a difference); a malformed analysis file is never a
  traceback (exit 2 when unreadable as an analysis, exit 5 when not made from the target); a REA row with a non-hex address is skipped; `candidates.listed` is capped at `MAX_LISTED` and says so
  (`listed_truncated`); allocating initialisers of classes and async functions carry a `caveat` (their decompilation is an intermediate);
  the README states that the budget does not include the target check and the arm64 extraction.
- `rebuild`: a malformed analysis or inventory exits 2; resource usage lists state `usage_strings_total` and `usage_truncated`
  when `MAX_USAGE` cuts them, and `coverage.not_covered` counts the files cut; `.mp4` is kind `other`, not `sound`.
- `spec check`: the title, the global summary and the feature names are checked for leaks like the point texts (the feature
  summary once, not once per point); evidence files that describe different targets are refused.
- `inventory`: an executable or `.debug.dylib` that is a symbolic link leaving the bundle is refused; an answer that lacks
  expected lists is not an empty `ok` (`absent_keys`, `dropped_rows`, `unknown_parts`), and a null `subject.digest` no longer
  raises.
- `setup`: zip extraction refuses absolute names and `..` components in member names and link targets; `pin_gradle` fails
  when the wrapper properties already pin another checksum; a download slower than `TIMEOUT_DOWNLOAD` as a whole is refused;
  every external process runs in its own session and its whole process group is killed at the timeout, the pipes then being
  drained for at most `DRAIN_SECONDS`; the zip link targets are checked before anything is written.
- `BlueprintExport.java`: the walk forgets every register an instruction writes (second loaded register, exclusive-store
  status register, written-back base register) and `mov xN, x29` ends frame-slot tracking; the string-label address accepts 8
  to 16 hex digits; symbolic references `0x18`..`0x1f` are skipped as 8-byte pointers; only direct type records are read. The walk also forgets the other registers written by an `sp`-writing instruction. A real
  `analyze` of the authorised target before and after gave the same functions, call sites, floating-point calls and
  Swift types (observed, 2026-10-18); the tests guard the script text only.
- Documentation: README corrected where the review found it inexact or incomplete (walk, data directory, `--output`,
  registry protection, evidence ids per REA call, provider, `ui_strings`, trial numbers, exit code 5, skills count, trial
  revision); skill `decompile` (`argument-hint`, exit 5); skill `spec`; root README row. The 0.1.0 reference
  `(README, "Inventory")` and the line wrapping of the 0.3.0 "Run on" bullet are corrected in place (no meaning changed); the
  Codex manifest description is now the one of the Claude manifest (they were identical until 0.2.0).
- Not changed, stated rather than hidden: a frame slot reached through a copy of `sp` could keep a stale value (deduced); the
  decompiler pseudo-code cut of the REA path (`decompile.MAX_CODE_CHARS`, 30000) stays; register-passed constructor arguments
  that are not literals stay `not a literal`; Witness recording the taps of a UI test as navigation evidence is a Witness
  matter, not done here.

## 0.3.0 — 2026-10-18

- `blueprint_cli.py analyze <target> --inventory FILE`: same registry gate; imports and analyses the arm64 slice in
  Ghidra once into a saved project (`ghidra-projects/`), then exports every app function (decompiled C, address, demangled
  name), the Swift type descriptors with their fields, string references and the bytes behind non-ASCII literals through
  a batch script (`tooling/ghidra_scripts/BlueprintExport.java`). The analysis is not repeated for the same slice and
  Ghidra version, and the times (analysis, export, total) are recorded. Evidence is Ghidra's: slice sha256, address,
  program id (`gh_` ids).
- `decompile --analysis FILE`: decompilations read from that analysis, no Ghidra or REA process.
- `blueprint_cli.py rebuild`: measured coverage (groups, screens, functions analysed over detected, and what is not
  covered), static constants and predefined objects, the persistent (SwiftData) data model with property types, and the
  bundle resource inventory with sha256, kind and known usage, each with its evidence or `unknown`.
- `blueprint_cli.py screens`: validates a manifest linking screens to Witness proof reports and screenshots (files exist,
  sha256 equals the report's); Blueprint never runs the simulator.
- Spec format 2: format 1 plus the sections `coverage`, `constants`, `data_model`, `resources`, `screens`, validated by
  `spec check`; format 1 unchanged. Skill `blueprint:rebuild`.
- Fixes after the real trial: `decompile --analysis --group` accepts every group `rebuild` detects (SwiftUI screens
  included), and its candidates include getters (a SwiftUI view's `body` included) without property-wrapper storage;
  `--list` prints and writes no file; `--entry` must be one `0x` address and the summary counts decompiled and
  unknown entries (exit 5 when none decompiled); preset-object constants gain `constructor_call_arguments` and say that
  `integer_literals_in_order` is partial; `not_covered` states that extensions of other modules' types are not analysed;
  constant names lose the private-scope hash (kept in `private_scope`) and `spec check` refuses it.
- Gaps found by the maintainer's judgement of the trial spec: `analyze` also exports the members of the app's extensions of
  other modules' types (`<len><module>E` in the symbol; `Date`, `Color` and resource initialisers on the trial target),
  `rebuild` attributes them to a related group or to the group `extensions` and counts them in
  `coverage.functions.in_extensions_of_other_modules_types` instead of the "not measurable" sentence; the export records
  the argument registers that hold a constant at each constructor call of a one-time initialiser and `rebuild` uses them
  in `constructor_call_arguments` (with `constructor_call_argument_sources`) and maps them to the declared parameters in
  `constructor_parameters`; `MAX_CODE_CHARS` of the analysis goes from 30000 to 200000 so no SwiftUI `body` is cut.
- Second round of gaps: constrained extensions (`extension Array where Element == T`, requirements between the module
  and `E` in the symbol) are exported, named by their requirement type and counted; `constructor_call_arguments`
  keeps `x7` and `constructor_parameters` reads stack-passed parameters from constant stores before the call
  (`call_sites.stack_arguments`); the export records floating-point constants in `d0`..`d7` and Double bit patterns in
  integer registers per call (`float_calls`, keyed by callee, also in `decompile --analysis` output).
- Run on 2026-10-18 on the authorised target: first `analyze` 94.1 s in total (analysis 48.9 s), later runs reused the
  analysis; 4180 app functions decompiled by the first export, 4236 by the last one; tests use fakes. The maintainer's
  judgement of the spec ("can the app be rebuilt from it alone") is not part of this entry.

## 0.2.0 — 2026-10-18

- `blueprint_cli.py groups <inventory>`: offline, deterministic grouping of an inventory's types and
  UI strings into candidate screens and features; what cannot be grouped is listed in `ungrouped`
  with the reason, and `accounting` makes the string counts add up.
- `blueprint_cli.py decompile <target> --inventory FILE --group NAME | --entry ADDR`: same registry
  gate as `inventory`; finds the group's entry points with one `rea search` and decompiles a bounded
  number of them (`--max`, `--budget`, `TIMEOUT_DECOMPILE`), each with its REA evidence; `--list`
  only lists the candidates.
- `blueprint_cli.py spec check` and `spec to-foundry`: a product-language spec whose every point
  cites REA evidence ids present in the files given, or `unknown`; `to-foundry` writes the
  payloads of `edit create-issue` (the Epic, then the tickets with `--epic-id`) and prints the commands.
  It never calls the tracker: the maintainer confirms before the coordinator runs them.
- Skills `blueprint:decompile` and `blueprint:spec`.
- `groups` and `decompile` (one entry point and one group listing) were run once on 2026-10-18 on the
  authorised target; the tests use fakes.

## 0.1.0 — 2026-10-18

- First brick (PAT-252): `blueprint_cli.py setup [--plan|--yes]` prints, then on `--yes`
  performs, the preparation of pinned tooling in a data directory outside any repository:
  REA 6.5.0 installed with `npm ci --ignore-scripts` from a shipped lockfile (whole tree
  pinned, never `@latest`), Temurin JDK 21.0.12.1+1 and Ghidra 12.1.4 verified by sha256,
  the native macOS arm64 decompiler compiled with Gradle only when absent. Nothing is
  installed globally and `rea setup` is never run.
- `blueprint_cli.py targets add|list`: registry of authorised targets (date, target,
  reason), written only by `add`.
- `blueprint_cli.py inventory <target>`: refused (exit 3) unless the target is in the
  registry; thins a universal Mach-O to arm64 with `lipo`; runs the pinned REA; writes a
  reproducible inventory JSON (modules, types, UI strings, resources) with REA evidence
  (evidence id, target sha256, address, file offset); what cannot be read is `unknown`.
- Skills `blueprint:setup`, `blueprint:targets`, `blueprint:inventory`.
- A real `setup --yes` and a real `inventory` of an authorised target were run once on
  2026-10-18 with the maintainer's authorisation (README, "setup" and "inventory"); the tests use fakes.
