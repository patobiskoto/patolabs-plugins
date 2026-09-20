# Changelog

## 0.2.1 — 2026-08-20

- `submit` now requires a non-empty `version:` and passes it to deliver as
  `app_version`, creating the editable App Store Connect version draft needed to attach
  metadata, screenshots, and the Xcode Cloud build.
- The human-gated release step passes both version and build, and documents the one-shot
  App Store Connect UI fallback if deliver refuses to attach an otherwise valid build.

## 0.2.0 — 2026-07-21

- Native Codex packaging beside the Claude Code package, with the same setup and release
  skills under the Codex `$ship-ios:*` invocation form.
- Skill commands retain Claude's deterministic `CLAUDE_PLUGIN_ROOT` expansion and use
  their own `SKILL.md` path as the fallback required by Codex skill commands.
- Foundry changelog discovery now supports an explicit `FOUNDRY_CLI`, the host-neutral
  install marker, monorepo siblings, PATH, and both Claude and Codex plugin caches.
- Added bridge discovery tests and dual-marketplace validation in CI.
- Restored the non-empty Claude Code release argument hint after verifying that Codex
  0.144.6 installs and invokes skills carrying the unknown key without a related warning.
- **Release flow composes with the Foundry guard hook.** The version bump + release
  notes now land on the default branch through a PR — in a Foundry repo via a release
  chore issue + foundry:start-issue (which mints the branch shape foundry:open-pr
  requires) then foundry:open-pr/merge-pr — the branch is created BEFORE any file is
  written (start-issue refuses a dirty worktree, and release_notes.txt are tracked
  files from the second release on), the tag is created on the MERGED squash
  SHA (reported by foundry:merge-pr), and exactly
  one tag is pushed (`git push origin "v<version>"`) — no more direct `git push` of the
  default branch and no `git push --tags` publishing stray local tags. Staging is
  explicit (`git add` + staged-diff review instead of `commit -am`), so first-release
  `release_notes.txt` files can't be silently left out of the release commit. Form
  invariants pinned by `tests/test_release_skill.py`.
- The Gemfile template no longer claims to pin fastlane: the committed `Gemfile.lock`
  is the pin, and `ship-ios:setup` now requires committing it.

## 0.1.3 — 2026-07-05

- **Fastfile fix: `verify_locales` double-path bug** (caught during Souffle's first live
  submit). Lanes run with the CWD inside `fastlane/`, so the guard's
  `File.exist?("fastlane/metadata/...")` resolved to `fastlane/fastlane/metadata/...`
  and always failed. Paths inside lanes are now anchored on `__dir__`.

## 0.1.2 — 2026-07-05

Hardening from the first live setup+wiring run (SouffleApp):

- **setup: toolchain check first.** `ruby`/`bundle`/`fastlane` are now verified as step 1
  — the live run only discovered the EOL system Ruby (2.6, no fastlane) mid-scaffold.
  Path agreed with the user before any file is written (brew fastlane, or rbenv +
  bundler against the pinned Gemfile).
- **setup: the Xcode Cloud workflow step now teaches submittability.** A build archived
  "TestFlight (internal testing only)" can never be submitted to the App Store — the
  release workflow must archive with distribution preparation "App Store Connect", as a
  separate workflow from any per-merge internal-beta one. Also: a tag trigger is a
  Tag Changes START CONDITION, not the manual-launch tag option.
- **release: right-build guard.** The beta gate and `submit` now insist on a build NEWER
  than the tag push (the tag-triggered workflow's), never an older internal-only build
  that a per-merge beta workflow uploaded for the same version.
- **Fastfile: locale-neutral, no empty interpolation.** `testflight_status` message was
  hardcoded French and printed blank when `version:` was omitted; lane messages are now
  English (dev-facing) and handle the no-version case. `verify_locales` error likewise.
- plugin.json homepage/repository → the claude-plugins monorepo.

## 0.1.1 — 2026-07-02

- **Build model A** — Xcode Cloud builds/signs/uploads the release binary; fastlane no
  longer builds it. Lanes recentered on `screenshots` / `metadata` / `submit`
  (`skip_binary_upload: true`) / `testflight_status`. Zero signing to manage, one build
  system (the same Xcode Cloud that gates PRs).
- Scaffold guardrails: export-compliance step (`ITSAppUsesNonExemptEncryption`),
  `ensure_git_status_clean` before submit, `verify_locales` (no submit if a shipped
  locale lacks release notes), pinned `Gemfile`, marketing-version-then-tag flow that
  triggers the Xcode Cloud build.
- setup guides the Xcode Cloud "Release" workflow (tag-triggered → TestFlight).

## 0.1.0 — 2026-07-02

- Initial release. Skills `/ship-ios:setup` and `/ship-ios:release`; fastlane templates,
  per-locale metadata layout, snapshot UITest scaffold, changelog bridge. Composes with
  Foundry by data; depends on it for nothing.
