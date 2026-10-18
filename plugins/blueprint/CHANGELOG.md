# Changelog

## Unreleased

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
  included), and its candidates include computed getters (a SwiftUI view's `body`) without property-wrapper storage;
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
- Run once on 2026-10-18 on the authorised target: first `analyze` 94.1 s in total (analysis 48.9 s), a second one reused
  the analysis; 4180 app functions decompiled; tests use fakes. The maintainer's judgement of the spec ("can the app be
  rebuilt from it alone") is not part of this entry.

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
  2026-10-18 with the maintainer's authorisation (README, "Inventory"); the tests use fakes.
