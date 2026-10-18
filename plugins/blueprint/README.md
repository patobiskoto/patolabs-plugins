# blueprint

Reverse-engineering inventory for Claude Code and Codex, restricted to targets the
maintainer authorised. It prepares pinned REA, Ghidra and JDK in its own data directory
(never globally), refuses any target absent from a registry, and writes a reproducible
inventory with REA evidence, groups it into candidate screens and features, decompiles the entry
points of one group, runs one Ghidra analysis reused by every decompilation, measures coverage and extracts
constants, the persistent data model, bundle resources and screens (PAT-260), and turns a
product-language specification into Foundry payloads.
Stdlib-only Python. Version 0.3.1 (PAT-252, PAT-253, PAT-260, PAT-259, decisions of PAT-181).

```
python3 plugins/blueprint/tooling/blueprint_cli.py setup [--plan|--yes]
python3 plugins/blueprint/tooling/blueprint_cli.py targets add <target> --reason TEXT [--date YYYY-MM-DD]
python3 plugins/blueprint/tooling/blueprint_cli.py targets list
python3 plugins/blueprint/tooling/blueprint_cli.py inventory <target> [--output FILE]
python3 plugins/blueprint/tooling/blueprint_cli.py groups <inventory.json> [--output FILE]
python3 plugins/blueprint/tooling/blueprint_cli.py decompile <target> --inventory FILE (--group NAME | --entry ADDR) [--analysis FILE] [--list] [--max N] [--budget SECONDS] [--output FILE]
python3 plugins/blueprint/tooling/blueprint_cli.py analyze <target> --inventory FILE [--module NAME] [--output FILE]
python3 plugins/blueprint/tooling/blueprint_cli.py screens <manifest.json> [--output FILE]
python3 plugins/blueprint/tooling/blueprint_cli.py rebuild <analysis.json> --inventory FILE [--screens manifest.json] [--output FILE]
python3 plugins/blueprint/tooling/blueprint_cli.py spec check <spec.json> <evidence.json>...
python3 plugins/blueprint/tooling/blueprint_cli.py spec to-foundry <spec.json> <evidence.json>... [--output DIR] [--epic-id ID]
```

Skills: `/blueprint:setup`, `/blueprint:targets`, `/blueprint:inventory`, `/blueprint:decompile`,
`/blueprint:spec` (Claude Code); `$blueprint:setup`, `$blueprint:targets`, `$blueprint:inventory`,
`$blueprint:decompile`, `$blueprint:spec` (Codex); `/blueprint:rebuild` and `$blueprint:rebuild` for `analyze`, `screens`
and `rebuild`.

Exit codes (`EXIT_*` in `tooling/blueprint_cli.py`): 0 done, 2 usage or registry error,
3 target refused (not authorised, or unreadable registry), 4 tooling not prepared,
5 setup, inventory, decompile or analyze failed (`decompile` also exits 5 when it decompiled nothing, or when the
target is not the one the inventory describes; `cmd_decompile`).

## Data directory

By default blueprint writes under one directory outside any repository
(`data_dir` in `tooling/blueprint/paths.py`): `$BLUEPRINT_DATA_DIR` if set, else
`~/Library/Application Support/blueprint` on macOS, else `~/.local/share/blueprint`. An explicit `--output FILE`
(`inventory`, `groups`, `decompile`, `analyze`, `screens`, `rebuild`) or `--output DIR` (`spec to-foundry`) writes where it is
told, and the processes blueprint launches may write elsewhere (temporary files under `TMPDIR`, and Ghidra under `HOME` and
`/var/tmp`, see `analyze`).
Layout: `downloads/`, `rea/` (with `node_modules/`), `jdk/`, `ghidra/`, `gradle-home/`,
`npm-cache/`, `work/` (thin slices), `inventories/`, `decompilations/`, `payloads/`, `analyses/`,
`ghidra-projects/` (one saved Ghidra project per slice, `ghidra_projects` in `paths.py`), `targets.json` (registry).

## setup

`setup` and `setup --plan` print the plan and do nothing (`render_plan`,
`tooling/blueprint/setup.py`). `setup --yes` performs it (`perform`); each step is skipped
when its result already exists. The plan names, per step, every download (URL, checksum,
size) and every location written. Sizes are not recorded in the code: the plan prints
`unknown (not recorded)`.

| Step | Downloads | Writes | Pin (`tooling/blueprint/pins.py`) |
|---|---|---|---|
| `rea` | `rea-agents` tarball and the packages of the shipped lockfile, through `npm ci --ignore-scripts` | `rea/`, `npm-cache/` | `REA_VERSION`, `REA_INTEGRITY`; lockfile `tooling/rea/package-lock.json` |
| `jdk` | Temurin JDK archive | `downloads/`, `jdk/` | `JDK_URL`, `JDK_SHA256` |
| `ghidra` | Ghidra archive | `downloads/`, `ghidra/` | `GHIDRA_URL`, `GHIDRA_SHA256` |
| `natives` | Gradle distribution, fetched by the wrapper | wrapper properties of the extracted Ghidra, `gradle-home/`, native decompiler | `GRADLE_DIST_SHA256`, `NATIVE_DECOMPILER` |

- A downloaded file whose sha256 differs from the pin is deleted and the step fails
  (`fetch`). `TIMEOUT_DOWNLOAD` bounds each socket operation (`urlopen`) and also the whole transfer, checked between 1 MiB
  blocks (one read can last up to the timeout itself; `fetch`; `test_a_slow_download_is_refused_as_a_whole`). npm verifies each package against the integrity of the
  lockfile.
- Zip extraction (`extract_zip`) refuses, before writing anything, a member name or a symbolic-link target that is absolute or
  holds a `..` component (`_plain_relative`), because containment alone is bypassed by a link `l -> .` followed by a member
  `l/../escape` (`test_zip_symlink_then_dotdot_member_is_refused`). The pinned Ghidra archive holds no symbolic link and no
  `..` member (checked on the downloaded archive, 2026-10-18).
- `natives` compiles with `./gradlew --no-daemon buildNatives`, run from the Ghidra copy's
  `support/gradle` directory (`GRADLE_DIR`), only when `NATIVE_DECOMPILER` is absent; it
  first adds `distributionSha256Sum` to the extracted copy's wrapper properties
  (`pin_gradle`; a line already there must equal `GRADLE_DIST_SHA256`, otherwise the step fails), then copies each `build/os/mac_arm_64` output of `NATIVE_MODULES` to
  `os/mac_arm_64`, where the installed layout reads it (observed 2026-10-18). It needs the macOS developer command-line tools (a C++ compiler):
  deduced from the build, not checked by blueprint.
- `setup --yes` refuses outside macOS arm64 (`perform`): the JDK and the decompiler are for
  that platform.
- The child environment of npm, Gradle and REA is an allow-list plus `JAVA_HOME`,
  `GHIDRA_INSTALL_DIR`, `GRADLE_USER_HOME`, `npm_config_cache` (`tool_env`).
- `rea setup` is never run and nothing is installed globally. Whether REA itself writes
  outside the data directory (for example caches under `HOME`) when it analyses: unknown,
  not observed.
- The lockfile was copied from the 2026-10-18 trial and its root entry changed to the exact
  version `6.5.0` and the name `blueprint-rea`. The procedure was run once on a copy with
  `npm ci --ignore-scripts --dry-run --offline`, which resolved 178 packages and installed
  nothing. A real install was then run once, on 2026-10-18, with the maintainer's
  authorisation, and completed after two fixes: the Gradle build runs from `support/gradle` (`GRADLE_DIR`), and each
  module's `build/os/mac_arm_64` output is copied to `os/mac_arm_64` (`NATIVE_MODULES`; both described above).
- Prerequisites not installed by blueprint: `npm` and `node` are taken from `PATH`, unpinned
  (`NPM_CI` in `setup.py`), and `rea` runs on that `node` at every inventory; the lockfile's
  `engines` ask for Node `^22.18.0 || >=24.11.0` (the 2026-10-18 run used Node 22.22.3).
  Extracting the JDK uses `tarfile`'s `filter="data"` (`extract_tar`), which needs Python
  3.12 or a 3.8–3.11 security release that backports extraction filters; an older Python
  fails closed (deduced from the Python documentation, not run).

## Registry of authorised targets

`targets.json` holds `{"version": 1, "targets": [{"date", "target", "reason"}]}`
(`tooling/blueprint/registry.py`). Only `targets add` writes it (mode 600, atomic): it needs
an existing target, a non-empty reason and a `YYYY-MM-DD` date (default today), and refuses a
duplicate. The target is stored as its resolved absolute path and matched by equality: a
path inside an authorised bundle, or another path to the same product, is not authorised. An
unreadable or malformed registry authorises nothing (`inventory` exits 3). The skill
`blueprint:targets` runs `add` only on the maintainer's explicit instruction.

That instruction is the only protection of the registry: no code prevents an agent from running `targets add`, or from
pointing `BLUEPRINT_DATA_DIR` at another directory with its own `targets.json` (`registry.py` and `paths.py` hold no such
guard; read in the code). A guard would be a new capability and goes through `foundry:intake` (AGENTS.md#R3), not through
this page. The registry identifies a path, not a content: a target replaced in place keeps its authorisation, and
`decompile` and `analyze` compare its bytes with the inventory (sha256) before using it.

## inventory

`inventory <target>` checks the registry first and exits 3 before reading the target or
running anything (`cmd_inventory`). It then needs the prepared tooling (`tools_ready`,
exit 4), and writes `<data dir>/inventories/<name>-<sha256[:16]>.json` unless `--output`
is given (mode 600).

Target: an app bundle (the executable named by `CFBundleExecutable`, or
`<executable>.debug.dylib` when present: a Debug iOS build keeps the code there) or a
Mach-O file (`locate_binary`, `tooling/blueprint/inventory.py`; an executable or `.debug.dylib` that is a symbolic link
resolving outside the bundle is refused, `test_executable_symlink_leaving_the_bundle_is_refused`). A universal Mach-O is
reduced to its arm64 slice with `lipo -thin arm64` (`thin`) because REA refuses a universal
binary with Ghidra (observed in the 2026-10-18 trials). Every external process has a
timeout (`TIMEOUT_*` in `tooling/blueprint/pins.py`), runs in its own session and, at the timeout, the whole process group
is killed (`proc.run`; `test_timeout_kills_the_whole_process_group`). A descendant that starts another session of its own
escapes that kill (deduced from `setsid` semantics); `proc.run` then drains the pipes for at most `DRAIN_SECONDS` (2 s) and
gives up on them, so blueprint returns but that descendant keeps running and what it still writes is dropped
(`test_a_descendant_in_its_own_session_holding_the_pipes_does_not_block_blueprint`).

The document has no timestamp, no absolute path and sorted lists; two runs on the same
inputs give the same bytes by construction (checked on fakes only,
`test_reproducible_and_no_local_path`). Whether REA's own `evidence_id` is stable across
runs: unknown, not observed.

| Section | Source | Evidence per item |
|---|---|---|
| `analysis` | `rea analyze --provider ghidra` on the slice | counts, provider and version as REA reports them, evidence id, slice sha256 |
| `modules` | `otool -L` on the slice (linked libraries) | slice sha256 |
| `types` | `rea inspect-native-dispatch-metadata` (Objective-C classes and protocols, Swift conformances) | evidence id, slice sha256, address, file offset |
| `ui_strings` | `rea search --kind strings --mode regex --pattern .` (`STRING_PATTERN`): every string REA lists in the binary, system paths and symbol names included, not only the texts a person reads (`groups` keeps the label-like ones, `human_string`; 21,725 of 22,250 were not on the 2026-10-18 trial, observed) | evidence id, slice sha256, address |
| `resources` | files of the bundle with sha256; `rea inspect-asset-catalog` and `rea decode-interface-builder` | sha256, evidence id |

Each section is `{"status": "ok" | "unknown", "reason", "items"}`. A failed or timed-out
REA call, or an answer without the expected fields, gives `unknown` with the reason; a
partial decode is listed in `incomplete_facets`, and a field REA leaves null is `unknown`
(Swift `module`). An answer that lacks an expected list is not an empty `ok`: `types` is `unknown` when it holds none of
`objc_classes`, `objc_protocols`, `swift_conformances` and otherwise names the absent ones in `absent_keys`; the catalogue
and interface-builder lists of `resources` are `unknown` in `unknown_parts` when absent; `ui_strings` counts in
`dropped_rows` the rows it could not read; a null `subject.digest` leaves the evidence `target_sha256` null instead of
failing (`inventory.subject_sha256`; `test_absent_keys_are_not_an_empty_ok`, `test_null_digest_does_not_raise`).
`ui_strings` keeps at most `MAX_STRINGS` and says `truncated`; `resources`
lists at most `MAX_FILES` files and says what is beyond in `unknown_parts`.

Limits, stated rather than hidden: the extraction of the `types`, `ui_strings` and
`resources` sections follows the field names seen in the 2026-10-18 trial outputs; the
regex `.` for the strings listing and the behaviour of `rea search` on a very large result
were first run through blueprint on 2026-10-18 on a Debug iOS build of an authorised
target: every section `ok` (38 modules, 31 types, 22,250 strings, 34 resources), about
2 minutes, and a second run gave the same modules, types and strings (observed; whether
this holds for other targets is unknown). In that run some accented strings came out
mis-decoded (replacement characters), as they already did in REA's own `search` output of
the earlier trial: the cause is in REA or Ghidra (observed), not in blueprint's reading.
"Modules" are the linked libraries; Swift modules as such are not listed. The `provider` and `provider_version` of the
`analysis` section are what REA answers: they read `rea-workflow` and `1` in the 2026-10-18 run, not Ghidra 12.1.4
(observed), so they do not attest which Ghidra ran. The pinned Ghidra is the one blueprint names in `tools.ghidra` and
passes to REA through `GHIDRA_INSTALL_DIR` (`tool_env`); that REA used it for this call: unknown, not checked.

## groups, decompile, spec (PAT-253)

The real trial of this section ran on 2026-10-18, before the final revision of the code: the exact commit it ran on was not
recorded (unknown), so a current `groups` output can differ from the trial's in details such as the `accounting` note. The
counts quoted in this README were re-checked offline on the same inventory on 2026-10-18 where a sentence says so.

Flow: `inventory` -> `groups` -> `decompile` -> the agent writes the spec (skill `blueprint:spec`)
-> `spec check` -> `spec to-foundry` -> maintainer confirmation -> Foundry commands run by the
coordinator. The evidence files given to `spec check` are the inventory and the decompilation files.

**groups** reads only the inventory (no REA call, no registry, `cmd_groups`) and is deterministic
(`test_deterministic_and_unknown_sections_reported`). A type joins a group by the role suffix of its
name (`SUFFIX_ROLES` in `tooling/blueprint/groups.py`: for example `ViewModel`, `View`, `Controller`
make a `screen`; `Manager`, `Service`, `Store` make a `feature`); records of one type name (Swift
conformance and Objective-C class) are merged; a plain Swift runtime class name `_TtC<n><module><n><class>`
gives the module (`split_swift_class`). A UI string joins the group whose name (4 characters or more,
`MIN_STEM_FOR_STRINGS`) it contains, case-insensitively, the longest name first, and only if it looks like
a label (`human_string`: heuristic, the symbol names, paths and keys it rejects stay in the inventory).
That is a naming and substring heuristic: a group is a candidate, not a fact about the product. Everything
else is in `ungrouped` with a reason (nested or private classes, protocols, types without a role
suffix, strings without a group), and `accounting` gives three counts (in groups, ungrouped, not user-facing) that add up to the number of
strings in the inventory (`test_strings_are_assigned_by_group_name_and_accounted`). A group items list is capped at `MAX_GROUP_STRINGS` (ungrouped strings:
`MAX_UNGROUPED_STRINGS`) and says `truncated`.

**decompile** applies the registry gate like `inventory` (exit 3 before anything runs), needs the
prepared tooling (exit 4), and checks that the target's sha256 and arm64 slice are those of the inventory
(`slice_for`, exit 5 otherwise). `--entry` must be one `0x<hex>` address (`ADDRESS`), `--max` at least 1 and `--budget` a
positive finite number of seconds, otherwise exit 2 before anything runs (`test_max_and_budget_are_validated`). With `--group`, one `rea search --kind procedures --mode regex` finds
the Swift procedures of the group's classes, `entry_points` keeps functions (mangled name ending in `F`)
and initialisers (`fC`) and drops accessors, closures, coroutine helpers and Observation boilerplate
(`BOILERPLATE_METHODS`, `BOILERPLATE_MARKERS`), then one `rea decompile <slice> <address>` per entry
point; a REA row whose address is not `0x<hex>` is skipped. `--list` stops after the search. Bounds: `--max` entry points
(default `MAX_DECOMPILE`), each call at most `TIMEOUT_DECOMPILE` seconds, the procedure search at most `TIMEOUT_REA`
seconds, and the search plus the calls `BUDGET_DECOMPILE` seconds by default (`--budget`), all in `tooling/blueprint/pins.py`.
The budget starts in `decompile.run`: the target hash check and the arm64 extraction before it (`slice_for` ->
`inventory.thin`, `TIMEOUT_LIPO`) are not counted in it; pseudo-code is cut at `MAX_CODE_CHARS` and says `truncated`. Output
(default `<data dir>/decompilations/<name>-<group>.json`, mode 600) keeps per entry the address, the mangled
symbol, the method name, the pseudo-code and the evidence (`rea_evidence_id`, `target_sha256`, `address`);
REA's `local_path` is not copied. `candidates` gives `total`, `chosen`, and `listed` (at most `MAX_LISTED` = the first 200
by address, `listed_truncated` says when more exist). The allocating initialiser of a class (`fC` after the kind letter `C`) or an async function (`YaF`, static `YaFZ`; `async throws` `YaKF`, `YaKFZ`, deduced from the Swift mangling) is a
candidate whose decompilation is an intermediate (observed in the trial for classes and async functions: the allocation
and the call to the real initialiser `fc`, or the switch to partial functions `TY0_`); neither `fc` nor the partial
functions are candidates, and those two kinds carry a `caveat` field saying so. A struct or enum initialiser is a
value-type `fC` with no `fc`: its body is in itself and it has no caveat (deduced from the Swift ABI; not observed in the
trial); an initialiser the app declares in an extension of another module's type (`analysis.extension_of`) is passed no kind and gets no caveat, even for a class (deduced); a generic async function (`…YalF`, `…YaKlF`) is not recognised either (deduced) (`decompile.caveat`; `test_value_type_initialisers_and_static_async_functions`; `test_entry_points_skip_non_hex_addresses_and_mark_intermediate_candidates`).
A failed call is `status: unknown` with the reason; a group whose classes
have no known module (seen only as Swift conformances) has no search hint and says unknown.
Observed on 2026-10-18 on the authorised target: `rea search --kind procedures` and `rea decompile` each
took about 52 s, the first one re-analysing the binary, and `--snapshot` did not shorten a following call;
whether the time depends on the target size is unknown. The pseudo-code is Ghidra output with mangled Swift
names, not source (REA `limitations` field). An evidence id of an inventory identifies one REA call, not one item: all the
strings share the id of the strings listing, all the types that of the metadata call (`test_inventory_evidence_id_is_one_per_rea_call_not_per_item`),
so a spec point citing it passes `spec check` whatever string it is about, and the ticket body keeps the id but not the
address. Prefer the ids of a decompilation (`ev_` per `rea decompile` call, `gh_` per function address) and name the
item in the text of the point; whether a point is about the item it cites is judged by its author and the reviewer.

## analyze, rebuild, screens (PAT-260)

Flow: `inventory` -> `analyze` (once) -> `groups`/`decompile --analysis` (as many times as wanted, no tool launched)
-> witness captures -> `screens` -> `rebuild` -> the agent writes a format 2 spec (skill `blueprint:rebuild`)
-> `spec check` -> `spec to-foundry`.

**analyze** applies the registry gate (exit 3 before anything runs), needs the prepared tooling (exit 4), checks the
target against the inventory like `decompile` (`slice_for`, exit 5), then `analysis.build`:

1. `ghidra.ensure_analysis`: `analyzeHeadless <project> -import <slice> -overwrite` once, into the project
   `<data dir>/ghidra-projects/<slice sha256[:16]>`. It writes `blueprint-analysis.json` there (`MARKER`: slice
   sha256, Ghidra version, program name, analysis seconds) only after the log says `Analysis succeeded` and
   `Import succeeded`. A later call with the same slice sha256 and `GHIDRA_VERSION` runs nothing and reports
   `analysis_reused: true`; another slice or version is analysed again. Timeout `TIMEOUT_GHIDRA_IMPORT`.
2. `ghidra.export`: `analyzeHeadless <project> -process <program> -noanalysis -readOnly -postScript BlueprintExport.java`
   (`tooling/ghidra_scripts/BlueprintExport.java`, compiled by Ghidra with the pinned JDK). It decompiles every
   function whose symbol starts with the app module prefix (`_$s<len><module>`; the module is the one owning most plain
   Swift classes of the inventory, or `--module`) or carries the module as extension context, `<len><module>` after
   the extended type and then `E`, or generic requirements ending in `l` and then `E` (a member of the app's extension,
   constrained or not, of another module's type; the `include` pattern built in `analysis.build`, then `analysis.extension_of`
   keeps only symbols whose extended type reads as a clean type), each limited to `DECOMPILE_FUNCTION_SECONDS`, and also reads the
   Swift type descriptors (`__swift5_types`) with their field descriptors (a record of kind 0, a direct type descriptor, is
   read; the other three kinds in the two low bits of the slot are skipped), the module's `vpZ` data symbols, the
   references to the strings that equal a bundle file name, and the UTF-8 bytes behind the labels the decompiler prints for
   non-ASCII string literals (the label address is read as 8 to 16 hex digits, so that an image loaded at `0x100000000`
   is not skipped: deduced from the label format, untried; the observed Debug image uses 8). A Swift symbolic reference
   inside a field's type name is read as a 4-byte relative offset for the marker bytes `0x01`..`0x17` and skipped as an
   8-byte absolute pointer for `0x18`..`0x1f` (Swift ABI, deduced, not observed on the target; a field type may still be
   misread where this is wrong, the data model takes its types from the getter signatures). For every function it walks the instructions once (`walk` in the script: a forward walk following
   immediate moves, register copies, `adrp`/`add` addresses, floating-point `fmov`/`movi` and loads from read-only
   constants, and stores to and loads from frame-pointer slots). What it forgets: at a branch target (`resetState`), after an
   unconditional jump or a return, every known register, frame slot and the outgoing-argument area and its size
   (`outArgsSize`); at a call, `x0`..`x18`, `x30`, `d0`..`d7`, `d16`..`d31`, the outgoing-argument area and its size; for
   good, the frame slots once an `add`/`sub` forms a frame- or stack-pointer address or a `mov` copies `x29` into another
   register (`escaped`); and every register an instruction writes that the walk does not model, including a second
   loaded register (`ldp d0, d1`), the status register of an exclusive store (`stxr`), a written-back base register and the
   registers loaded by an instruction that also writes `sp` (`ldp x19, x20, [sp], #32`) (`forgetResults`). A `mov xN, sp` is not counted as such a copy, because doing so dropped the correct value of `x6` in six
   one-time initialisers of the 2026-10-18 target (observed on a run compared with the previous script); a frame slot
   reached through a copy of `sp` could keep a stale value (deduced, not observed). Consequence (deduced from `outArgsSize`
   being reset at a call): when a function's `sub sp, sp, #imm` comes before another call, the stack arguments of a later
   constructor call are "not delimited" (`stack_arguments_known` false), which is safe but loses them; this is likely in a
   Release build, not tried. In a one-time initialiser (`_WZ`) it records, per call of a
   constructor (`...fC`), the integer registers `x0`..`x7` that hold a constant at the call and the constants stored to
   the outgoing stack-argument area (only after a `sub sp, sp, #imm`, offsets below that size: `call_sites`). In any
   function it records, per call (at most `MAX_FLOAT_CALLS` per function), the constants in `d0`..`d7`/`s0`..`s7` and
   the integer registers whose value is the bit pattern of a Double in the range of layout numbers (`float_calls`; Swift
   passes an optional or non-plain CGFloat in integer registers). The export always
   re-runs (`ghidra.export`); the saved analysis marker covers only the import (`ghidra.read_marker`: slice sha256 and
   Ghidra version), so a changed script is run at the next `analyze` (observed on 2026-10-18: three script edits, each
   used by the next run). Timeout `TIMEOUT_GHIDRA_EXPORT`. The script's arguments are checked against the ones Python
   passes (`test_script_arguments_match_what_python_passes`).
3. REA `demangle-swift`, in batches of `DEMANGLE_BATCH` (`TIMEOUT_DEMANGLE` each); REA runs no analysis for it.

Output `<data dir>/analyses/<name>-<sha256[:16]>/analysis.json` (mode 600) plus the raw `ghidra-export.json`: tool versions,
Ghidra project and program ids, `timing` (`analysis_seconds` of the saved analysis, `analysis_reused`,
`export_seconds`, `demangle_seconds`, `total_seconds` of this run), function counts, one entry per app function (address,
symbol, demangled name, `extension` (`extended_type`, `member`, for a member of an extension of another module's type,
`analysis.extension_of`), `call_sites`, `float_calls` (callee mangled and demangled, `floats`, `integer_registers_as_double`; integer patterns are
kept only when the demangled callee mentions CGFloat, Double or Float; the register is not mapped to a parameter),
status, pseudo-code cut at `MAX_CODE_CHARS` with `truncated`, evidence), the Swift types, the
references and `unknown_parts`. Evidence of an item is Ghidra evidence: slice sha256 and address (plus the Ghidra program
id), with `evidence_id = gh_<slice sha256[:16]>_<address without 0x>` (`analysis.ghidra_evidence`), not a REA id.

`decompile --analysis FILE` reads the pseudo-code from that document: same registry gate, the target's own bytes compared
with the inventory (sha256 of the analysed binary, `decompile.check_target`, exit 5 on a difference; the arm64 slice is not
extracted), same output shape, no Ghidra or REA process (`decompile.from_analysis`; `test_decompile_from_analysis_launches_nothing`), `from_analysis.ghidra_runs: 0`,
and the evidence is the entry's Ghidra evidence. It refuses an analysis made from another target (sha256 differs). A malformed analysis file is never a traceback: exit 2 when it cannot be read
as an analysis, exit 5 when it is readable but was not made from this inventory's target
(`test_malformed_analysis_is_a_usage_error_not_a_traceback`, one assertion per case).
With `--analysis`, `--group` accepts every group that `rebuild` detects (`rebuild.detect_groups`: the Swift types of the
image, structs and enums included, not only the inventory's Objective-C classes), and the entry points are the functions
(mangled name ending in `F`, static `FZ`, private types included), initialisers (`fC`), getters (a SwiftUI view's `body` included) and the closures
of a `body` of that group's types, without property-wrapper storage and key paths (`decompile._view_code`,
`_WRAPPERS`) and minus the same boilerplate (`decompile.analysis_entry_points`; `test_group_of_the_analysis_is_accepted_without_an_inventory_class`). `--list` prints the
candidates and writes no file unless `--output` is given (`test_list_writes_nothing`). `--entry` must be one address
`0x<hex>` (`decompile.ADDRESS`; anything else exits 2, `test_bad_entry_address_is_a_usage_error_and_unknown_is_not_success`).
The summary line counts entries decompiled and unknown (`decompile.counts`), lists each unknown entry with its reason, and the
exit code is 5 when none was decompiled (`return 0 if ok else EXIT_FAILED`; the skill `blueprint:decompile` says so too) (an address with no app function in the analysis is `unknown`). Checked once on
2026-10-18 on the authorised target with an analysis: `--group Home` (14 candidates, 6 of 6 decompiled at the default maximum), `--group Breathing --list`
(88 candidates, no file written), `--entry` with trailing text (exit 2), `--entry 0xdeadbeef` (0 of 1, exit 5).

Observed on 2026-10-18 on the authorised target (Debug simulator build, code in `.debug.dylib`; Ghidra 12.1.4):
first `analyze`: analysis 48.9 s, export 17.6 s, demangle 27.6 s, total 94.1 s; a second `analyze` reused the analysis
(total 45.8 s, nothing re-imported); 4180 app functions decompiled, 0 failed, 14 cut at the then limit of 30000 characters
(`MAX_CODE_CHARS` is now 200000, above the longest text seen, 73725 characters), of 6813 functions
in the binary; `decompile --analysis --group` took 0.1 s of wall time. Before, one `rea decompile` cost about 52 s per call
(see the `decompile` section). Ghidra also writes under `HOME` and `/var/tmp` (its script cache and packed database
cache: observed in the log and the directories, not confined to the data directory). Whether the times hold on another target: unknown.

**screens** reads a manifest `{"format": 1, "screens": [{"name", "report"}]}` (a relative `report` is relative to the manifest)
and validates (`screens.build`): the report is a `witness-proof.v1` report written by `witness_cli.py prove` (see
`plugins/witness/README.md`, "Report" and "Scripted navigation"), its screenshot path exists, the file's sha256 equals
`screenshot_sha256` of the report, names are unique, and when the report's navigation file declares a `screen` it must equal
the manifest name (the screen is declared by that file, not verified by Witness). Every problem is listed and exit is 2.
`--output` writes the normalised document, with `evidence_id = wit_<screenshot sha256>` per screen. Blueprint never runs
the simulator or Witness.

**rebuild** is offline (reads the analysis, the inventory and optionally the manifest; no registry, no process;
`rebuild.build`). It refuses an analysis and an inventory of different slices. Sections:

| Section | Content | Rule (code) |
|---|---|---|
| `coverage` | detected and analysed counts for `groups`, `screens`, `functions`, screens captured, timing, `not_covered` sentences | groups are role-suffix stems (`SUFFIX_ROLES`) over the Swift types of the image (not the inventory's Objective-C classes, which miss value types), a screen is a group with a `View`, `ViewModel`, `Controller` or `Screen` type, a group is analysed when one of its functions is decompiled (`detect_groups`); preview records (names starting `$s`) are ignored |
| `constants` | one item per static property getter of the app module: decoded value, type, evidence, or `unknown` with the reason | `constants.extract`: a getter that is a plain `return <literal>;` is decoded by its demangled type (`decode_literal`); a lazily initialised value is read from the `_WZ` one-time initialiser (string literals, and for an object the string literals, the stacked integer literals in code order, the constructor call arguments and, when the export holds one call site of the constructor, `constructor_parameters`); a static member of an extension of another module's type keeps its path without the `(extension in <module>):` prefix and the module in `extension_in_module`; SwiftUI preview records are excluded and counted (`excluded_preview_records`) |
| `data_model` | classes carrying the field `_$backingData` (SwiftData `@Model` macro output; deduced, observed on one target), stored properties with the type of their getter signature or `unknown`; schema and migration type names | `rebuild.data_model`; constraints and relationships are always `unknown` |
| `resources` | every bundle file of the inventory with sha256, size, kind (`resources.kind_of`: `image`, `sound`, `asset-catalog`, else `other`; `.mp4` is `other`, it may hold video) and usage | usage is known only when a binary string equals the file name or its stem (a name match, not proof of use), with the referencing functions when Ghidra finds references; otherwise `unknown`, never `unused`. At most `MAX_USAGE` (5) matching strings are listed per file, by address; `usage_strings_total` and `usage_truncated` give the real count and `coverage.not_covered` counts the files cut (none was cut on the 2026-10-18 trial target: offline `rebuild`, observed) |
| `screens` | detected screens with their capture `evidence_id` or `unknown`; captures not matched to a detected screen | the manifest name is compared, case-insensitively, with the group stem or one of its type names |

Known limits, stated rather than hidden. The integer literals of a predefined object are listed in code order and which one
feeds which parameter is `unknown` (the decompiler drops or reorders arguments: observed on the five predefined
programs of the target). `integer_literals_in_order` holds only the integers stored on the stack, a partial list
(observed: the argument 5 of the cardiac-coherence preset and 4-7-8 of the relaxation preset are not in it);
`constructor_call_arguments` lists every argument of the pseudo-code call in order: an integer for a literal, an
integer for a variable whose argument register holds a constant at the call in the instructions (the decompiler's n-th
argument is taken as `x<n>`, deduced; ignored for the call when a pseudo-code literal contradicts its register), else
`"not a literal"`; `constructor_call_argument_sources` says which (`pseudo-code literal`, `instruction immediate` or
null) (`constants.call_arguments`). `constructor_parameters` maps the registers to the constructor's declared
parameters by the Swift arm64 convention (`constants.constructor_parameters`): `x0`..`x7` take the integer arguments
in order, a `String` two registers, a `Foundation.UUID` one (by address, deduced), a floating-point type none; the
mapping stops, with a reason, at a type of unknown width. Parameters after `x7` are on the stack, each at its natural
alignment from offset 0 in declaration order (deduced for integer and Bool types) and are read from the stack stores
of `call_sites.stack_arguments`; with no store at the offset, or no delimited area, they stay `not a literal` with the
reason. `constructor_call_arguments` lists the registers up to the last `x0`..`x7` that holds a constant, also when the
decompiler dropped that argument from its call. Observed on 2026-10-18 on the authorised target (five presets and the
discovery program): every parameter after the three strings is read from instructions, except `isPreset` of the
discovery program. Functions defined in extensions of types of another
module (standard library `Array` or `Date`, frameworks) are exported when their symbol reads
`<extended type><len><module>[<requirements>]E` (`analysis.extension_of`); they join the group whose stem is contained
in, or contains, the member or extended type name (`groups.extension_stem`, a name match), else the group named by the
first app type of the generic requirements (`Array where Element == T` joins `T`; the type is kept when the demangled
text qualifies it with the module, `analysis.refine_member`), else the group `extensions`, and are counted in
`coverage.functions.in_extensions_of_other_modules_types`. The member name is cut in the symbol when it uses Swift word
substitution; it is completed from the demangled text when that extends it. A private type is named by Swift `(Type in _<32 hex>)`:
`constants.readable_name` writes `Type` in the constant name and keeps the hash in the item field `private_scope`; `spec
check` also refuses a constant name that still carries it (`_LEAK`). Screen navigation is `unknown` (nothing extracts links between screens). The contents of an asset
catalog are `unknown` (the inventory holds record counts). Pseudo-code is Ghidra output: a decoded constant is as sound
as it (deduced). The `not_covered` list states that analysis is static only, followed by the analysis's `unknown_parts` when there are
any (`rebuild.coverage`). On the
2026-10-18 trial `rebuild` reported groups 37/38, screens 29/29 (0 captured: `rebuild` without a screens manifest always reports 0,
observed on a re-run of 2026-10-18; whether the trial run had one: unknown), functions 4180/4180, 46 constants (20
`unknown`, 75 preview records excluded), 3 data models, 33 resources (14 with unknown usage). After the extension and
call-site changes, on the same target: groups 38/39 (the group `extensions` included), screens 29/29, functions
4204/4204 (24 in extensions of other modules' types: 3 on `Date`, 16 on `Color` (5 palette colours with 3 functions
each and an initialiser from a hex string), 5 resource initialisers), 0 cut, 51 constants (21 `unknown`, 75 preview records excluded);
`analysis.json` 12.3 MB against 11.9 MB. After the constrained-extension, stack-argument and floating-point changes:
groups 39/40, functions 4236/4236 (56 in extensions of other modules' types: 31 in the group `SessionStat`, the members
of `Array where Element == SessionStat` that compute streaks and weekly figures; 25 in `extensions`), 179 calls with
floating-point constants in 117 functions, 0 cut, `analysis.json` 12.6 MB. With a screens manifest, the same analysis gives
`captured 7` of the 29 screens for the first manifest of Witness captures and `captured 12` for a later one (offline `rebuild` runs
of 2026-10-18 on a copy of the data; the manifests are the maintainer's trial files, not shipped).

**spec** file format: `{"format": 1, "title", "summary", "features": [{"name", "summary", "points":
[{"text", "evidence": ["ev_..."] | "unknown"}]}]}` (`tooling/blueprint/spec.py`). `spec check`
(`spec.check`) refuses a point whose evidence id is not found (any `rea_evidence_id` or `evidence_id` key,
at any depth) in the evidence files passed, a point with neither a non-empty id list nor `unknown`, a
duplicate feature name, a title, global summary, feature name, feature summary or point text containing a mangled symbol or
a raw hex address (`_LEAK`; each is checked once), and evidence files that describe different targets (the arm64 slice
sha256 of the files that carry one: inventory, groups, decompilation, analysis, rebuild; `spec.evidence_targets`; a
screens file carries none and is not compared; `test_evidence_files_of_two_targets_are_refused`). It does not and cannot prove that a point is true or that an id supports the sentence it is
attached to: the author and the reviewer judge that. `spec to-foundry` runs the check first, writes
`epic.json` (fields `Type: Epic`, `State: backlog`, as `campaign_defer.py` creates its companion Epic)
and prints the `edit create-issue` command; with `--epic-id` it writes one `ticket-NN-<slug>.json` per
feature with `parent` set, whose acceptance criteria are the evidenced points and whose body cites their
ids; points with `unknown` go to an "Unknown" section, not to acceptance criteria. A feature without any
evidenced point is refused.

**spec format 2** (PAT-260) is the format 1 plus five required sections, validated by `spec.check_sections`: `coverage` (integer `analysed` and `detected` for `groups`, `screens`, `functions`, `analysed` not above `detected`, and a non-empty `not_covered` list; when a rebuild file is among the evidence files the numbers must equal its `coverage`), `constants` (`name`, `value` or `unknown`, evidence ids or `unknown`; a known value needs ids), `data_model` (entities with `properties` of `name` and `type`), `resources` (`path`, 64-hex `sha256`, `usage`, evidence; with a rebuild file, path and sha256 must be in it) and `screens` (`name`, `navigation` list or `unknown`, `capture` ids or `unknown`). Accepted ids come from the evidence files: `ev_` (REA), `gh_` (Ghidra), `res_` (bundle file), `wit_` (Witness screenshot). A format 1 spec stays valid unchanged. `spec to-foundry` adds the `coverage.not_covered` sentences to the Epic body. The payload keys `title`, `body`, `fields`, `parent` are those read by
`edit.create_issue` (`plugins/foundry/tooling/foundry/edit.py`). The command never calls the tracker and never
runs Foundry (`test_cli_to_foundry_writes_payloads_prints_commands_and_never_calls_anything`): the
commands are printed with `<foundry-root>` and run only after the maintainer's explicit confirmation, by
the coordinating Foundry skill (`foundry:intake`, "3. Apply (human-confirmed)"). The two phases exist
because a ticket's parent identifier is known only once the Epic is created.

The trial on the compiled authorised target and the real tracker writes are not part of this release
note: see the pull request of PAT-253.

## Data and confidentiality

REA evidence stays on the machine. It leaves it only to Claude Code or Codex under the
maintainer's account (PAT-181 decision). REA's own skill runs `npx -y rea-agents@latest`:
it is not used.

## Documentation status (R5, FOUNDRY-ADR-0018)

| Artefact | Status |
|---|---|
| CLI verbs and options of `blueprint_cli.py` | documented here and in the six skills (`setup`, `targets`, `inventory`, `decompile`, `rebuild`, `spec`) |
| `BLUEPRINT_DATA_DIR`, data directory layout | documented above |
| Pins (`pins.py`), lockfile | documented above and in `CHANGELOG.md` |
| Registry file format, inventory sections and limits | documented above |
| Operational constraint: macOS arm64 only for `setup --yes` | documented above |
| Foundry `README.md` CI job list (seven jobs) | updated; fragment `plugins/foundry/changelog.d/PAT-252.md` |
| CLI verbs `groups`, `decompile`, `spec check`, `spec to-foundry` and their options | documented above and in the skills `decompile` and `spec` |
| Constants `SUFFIX_ROLES`, `MAX_GROUP_STRINGS`, `MAX_DECOMPILE`, `TIMEOUT_DECOMPILE`, `BUDGET_DECOMPILE`, `MAX_CODE_CHARS` | documented above (names; values are in the code) |
| Spec file format, groups and decompilation output | documented above |
| Operational constraint: one REA call is about 52 s | documented above (observed once); superseded for repeated decompilation by `analyze` and `decompile --analysis` |
| CLI verbs `analyze`, `screens`, `rebuild`, option `decompile --analysis`, exit code 5 for analyze | documented above and in the skills `rebuild` and `decompile` |
| Constants `TIMEOUT_GHIDRA_IMPORT`, `TIMEOUT_GHIDRA_EXPORT`, `TIMEOUT_DEMANGLE`, `DECOMPILE_FUNCTION_SECONDS`, `DEMANGLE_BATCH`, `GHIDRA_PROJECT_NAME`, `GHIDRA_SCRIPT`, `MARKER`, `MODEL_MARKER_FIELD` | documented above (names; values are in the code) |
| Data directory entries `analyses/`, `ghidra-projects/`; evidence id prefixes `gh_`, `res_`, `wit_` | documented above |
| Analysis, rebuild, screens manifest and spec format 2 | documented above |
| Operational constraint: Ghidra writes outside the data directory (`HOME`, `/var/tmp`) | documented above (observed) |
| PAT-259 (deferred remarks of PAT-252, PAT-253, PAT-260): `decompile` options `--max` (at least 1) and `--budget` (positive), `decompile --analysis` target check, exit 5 for nothing decompiled | documented above and in the skill `decompile` |
| Constant `MAX_LISTED`; candidate field `caveat`; `candidates.listed_truncated`; `decompile.check_target`; `spec.evidence_targets` | documented above (names; values are in the code) |
| Resource item fields `usage_strings_total`, `usage_truncated` (constant `MAX_USAGE`); `.mp4` kind `other` | documented above |
| Inventory fields `absent_keys` (`types`), `dropped_rows` (`ui_strings`); `inventory.subject_sha256`; symbolic-link refusal in `locate_binary` | documented above |
| Operational constraints: process-group kill at a timeout (`proc.run`), whole-transfer download deadline (`fetch`), zip names and link targets without `..` (`extract_zip`), `pin_gradle` refuses another checksum | documented above (`setup`, `inventory`) |
| Operational constraint: the registry is protected only by the skill instruction (no code guard) | documented above (Registry of authorised targets) |
| Limits of `BlueprintExport.java` `walk`, label address width, symbolic references 0x18..0x1f, record kinds | documented above (`analyze`), each stated as observed, deduced or untried |
| Foundry docs | not necessary: no Foundry CLI, constant or skill changed; blueprint only prints `edit create-issue` commands whose payload keys are those `edit.py` already reads |
