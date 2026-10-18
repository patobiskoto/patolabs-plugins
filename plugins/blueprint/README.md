# blueprint

Reverse-engineering inventory for Claude Code and Codex, restricted to targets the
maintainer authorised. It prepares pinned REA, Ghidra and JDK in its own data directory
(never globally), refuses any target absent from a registry, and writes a reproducible
inventory with REA evidence, groups it into candidate screens and features, decompiles the entry
points of one group, and turns a product-language specification into Foundry payloads.
Stdlib-only Python. Version 0.2.0 (PAT-252, PAT-253, decisions of PAT-181).

```
python3 plugins/blueprint/tooling/blueprint_cli.py setup [--plan|--yes]
python3 plugins/blueprint/tooling/blueprint_cli.py targets add <target> --reason TEXT [--date YYYY-MM-DD]
python3 plugins/blueprint/tooling/blueprint_cli.py targets list
python3 plugins/blueprint/tooling/blueprint_cli.py inventory <target> [--output FILE]
python3 plugins/blueprint/tooling/blueprint_cli.py groups <inventory.json> [--output FILE]
python3 plugins/blueprint/tooling/blueprint_cli.py decompile <target> --inventory FILE (--group NAME | --entry ADDR) [--list] [--max N] [--budget SECONDS] [--output FILE]
python3 plugins/blueprint/tooling/blueprint_cli.py spec check <spec.json> <evidence.json>...
python3 plugins/blueprint/tooling/blueprint_cli.py spec to-foundry <spec.json> <evidence.json>... [--output DIR] [--epic-id ID]
```

Skills: `/blueprint:setup`, `/blueprint:targets`, `/blueprint:inventory`, `/blueprint:decompile`,
`/blueprint:spec` (Claude Code); `$blueprint:setup`, `$blueprint:targets`, `$blueprint:inventory`,
`$blueprint:decompile`, `$blueprint:spec` (Codex).

Exit codes (`EXIT_*` in `tooling/blueprint_cli.py`): 0 done, 2 usage or registry error,
3 target refused (not authorised, or unreadable registry), 4 tooling not prepared,
5 setup or inventory failed.

## Data directory

Everything blueprint writes goes under one directory outside any repository
(`data_dir` in `tooling/blueprint/paths.py`): `$BLUEPRINT_DATA_DIR` if set, else
`~/Library/Application Support/blueprint` on macOS, else `~/.local/share/blueprint`.
Layout: `downloads/`, `rea/` (with `node_modules/`), `jdk/`, `ghidra/`, `gradle-home/`,
`npm-cache/`, `work/` (thin slices), `inventories/`, `decompilations/`, `payloads/`, `targets.json`
(registry).

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
  (`fetch`). npm verifies each package against the integrity of the lockfile.
- `natives` compiles with `./gradlew --no-daemon buildNatives`, run from the Ghidra copy's
  `support/gradle` directory (`GRADLE_DIR`), only when `NATIVE_DECOMPILER` is absent; it
  first adds `distributionSha256Sum` to the extracted copy's wrapper properties
  (`pin_gradle`), then copies each `build/os/mac_arm_64` output of `NATIVE_MODULES` to
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
  authorisation, and completed after the two fixes described in the pull request.
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

## inventory

`inventory <target>` checks the registry first and exits 3 before reading the target or
running anything (`cmd_inventory`). It then needs the prepared tooling (`tools_ready`,
exit 4), and writes `<data dir>/inventories/<name>-<sha256[:16]>.json` unless `--output`
is given (mode 600).

Target: an app bundle (the executable named by `CFBundleExecutable`, or
`<executable>.debug.dylib` when present: a Debug iOS build keeps the code there) or a
Mach-O file (`locate_binary`, `tooling/blueprint/inventory.py`). A universal Mach-O is
reduced to its arm64 slice with `lipo -thin arm64` (`thin`) because REA refuses a universal
binary with Ghidra (observed in the 2026-10-18 trials). Every external process has a
timeout (`TIMEOUT_*` in `tooling/blueprint/pins.py`).

The document has no timestamp, no absolute path and sorted lists; two runs on the same
inputs give the same bytes by construction (checked on fakes only,
`test_reproducible_and_no_local_path`). Whether REA's own `evidence_id` is stable across
runs: unknown, not observed.

| Section | Source | Evidence per item |
|---|---|---|
| `analysis` | `rea analyze --provider ghidra` on the slice | counts, provider and version, evidence id, slice sha256 |
| `modules` | `otool -L` on the slice (linked libraries) | slice sha256 |
| `types` | `rea inspect-native-dispatch-metadata` (Objective-C classes and protocols, Swift conformances) | evidence id, slice sha256, address, file offset |
| `ui_strings` | `rea search --kind strings --mode regex --pattern .` (`STRING_PATTERN`) | evidence id, slice sha256, address |
| `resources` | files of the bundle with sha256; `rea inspect-asset-catalog` and `rea decode-interface-builder` | sha256, evidence id |

Each section is `{"status": "ok" | "unknown", "reason", "items"}`. A failed or timed-out
REA call, or an answer without the expected fields, gives `unknown` with the reason; a
partial decode is listed in `incomplete_facets`, and a field REA leaves null is `unknown`
(Swift `module`). `ui_strings` keeps at most `MAX_STRINGS` and says `truncated`; `resources`
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
"Modules" are the linked libraries; Swift modules as such are not listed.

## groups, decompile, spec (PAT-253)

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
(`slice_for`, exit 5 otherwise). With `--group`, one `rea search --kind procedures --mode regex` finds
the Swift procedures of the group's classes, `entry_points` keeps functions (mangled name ending in `F`)
and initialisers (`fC`) and drops accessors, closures, coroutine helpers and Observation boilerplate
(`BOILERPLATE_METHODS`, `BOILERPLATE_MARKERS`), then one `rea decompile <slice> <address>` per entry
point. `--list` stops after the search. Bounds: `--max` entry points (default `MAX_DECOMPILE`), each call
at most `TIMEOUT_DECOMPILE` seconds, the procedure search at most `TIMEOUT_REA` seconds, and the whole run (search
included) `BUDGET_DECOMPILE` seconds by default (`--budget`),
all in `tooling/blueprint/pins.py`; pseudo-code is cut at `MAX_CODE_CHARS` and says `truncated`. Output
(default `<data dir>/decompilations/<name>-<group>.json`, mode 600) keeps per entry the address, the mangled
symbol, the method name, the pseudo-code and the evidence (`rea_evidence_id`, `target_sha256`, `address`);
REA's `local_path` is not copied. A failed call is `status: unknown` with the reason; a group whose classes
have no known module (seen only as Swift conformances) has no search hint and says unknown.
Observed on 2026-10-18 on the authorised target: `rea search --kind procedures` and `rea decompile` each
took about 52 s, the first one re-analysing the binary, and `--snapshot` did not shorten a following call;
whether the time depends on the target size is unknown. The pseudo-code is Ghidra output with mangled Swift
names, not source (REA `limitations` field).

**spec** file format: `{"format": 1, "title", "summary", "features": [{"name", "summary", "points":
[{"text", "evidence": ["ev_..."] | "unknown"}]}]}` (`tooling/blueprint/spec.py`). `spec check`
(`spec.check`) refuses a point whose evidence id is not found (any `rea_evidence_id` or `evidence_id` key,
at any depth) in the evidence files passed, a point with neither a non-empty id list nor `unknown`, a
duplicate feature name, and a text or feature summary containing a mangled symbol or a raw hex address
(`_LEAK`). It does not and cannot prove that a point is true or that an id supports the sentence it is
attached to: the author and the reviewer judge that. `spec to-foundry` runs the check first, writes
`epic.json` (fields `Type: Epic`, `State: backlog`, as `campaign_defer.py` creates its companion Epic)
and prints the `edit create-issue` command; with `--epic-id` it writes one `ticket-NN-<slug>.json` per
feature with `parent` set, whose acceptance criteria are the evidenced points and whose body cites their
ids; points with `unknown` go to an "Unknown" section, not to acceptance criteria. A feature without any
evidenced point is refused. The payload keys `title`, `body`, `fields`, `parent` are those read by
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
| CLI verbs and options of `blueprint_cli.py` | documented here and in the three skills |
| `BLUEPRINT_DATA_DIR`, data directory layout | documented above |
| Pins (`pins.py`), lockfile | documented above and in `CHANGELOG.md` |
| Registry file format, inventory sections and limits | documented above |
| Operational constraint: macOS arm64 only for `setup --yes` | documented above |
| Foundry `README.md` CI job list (seven jobs) | updated; fragment `plugins/foundry/changelog.d/PAT-252.md` |
| CLI verbs `groups`, `decompile`, `spec check`, `spec to-foundry` and their options | documented above and in the skills `decompile` and `spec` |
| Constants `SUFFIX_ROLES`, `MAX_GROUP_STRINGS`, `MAX_DECOMPILE`, `TIMEOUT_DECOMPILE`, `BUDGET_DECOMPILE`, `MAX_CODE_CHARS` | documented above (names; values are in the code) |
| Spec file format, groups and decompilation output | documented above |
| Operational constraint: one REA call is about 52 s | documented above (observed once) |
| Foundry docs | not necessary: no Foundry CLI, constant or skill changed; blueprint only prints `edit create-issue` commands whose payload keys are those `edit.py` already reads |
