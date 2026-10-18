# Changelog

## Unreleased

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
