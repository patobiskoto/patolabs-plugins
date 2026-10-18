# Changelog

## Unreleased

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
