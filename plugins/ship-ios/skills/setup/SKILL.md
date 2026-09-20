---
name: setup
description: >
  Provisions the fastlane release scaffold into an iOS app repo (once per project, Model
  A — Xcode Cloud builds, fastlane submits): detects the scheme + bundle id, writes
  fastlane/ (Fastfile, Appfile, Snapfile) + Gemfile from templates, wires the App Store
  Connect API key, sets export compliance, and creates the per-locale metadata tree. USE
  WHEN the user invokes /ship-ios:setup (Claude Code), $ship-ios:setup (Codex),
  "prépare la release", or before the first release.
allowed-tools: Bash(ruby:*), Bash(fastlane:*), Bash(bundle:*), Bash(xcodebuild:*), Bash(xcrun:*), Bash(cp:*), Bash(mkdir:*), Bash(git:*)
---

# ship-ios:setup — scaffold the release tooling (Model A)

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<ship-ios-root>` from this skill's absolute path by removing
`/skills/setup/SKILL.md`. Never pass the placeholder literally.

The Fastfile is per-project config, so it lives in THIS repo (versioned, runs in CI),
not in the plugin. This copies the plugin's templates in and fills the project values.
Run once. Model A: Xcode Cloud builds/signs/uploads; fastlane does metadata/screenshots/
submit — so there is NO signing/`match` setup here.

## 1. Check the toolchain FIRST (don't discover this mid-scaffold)
```bash
ruby -v ; bundle -v ; fastlane -v 2>/dev/null || echo "no fastlane"
```
The lanes need fastlane on a supported Ruby (3.x). macOS system Ruby (2.6, EOL) won't
run it. If missing, agree on a path with the user BEFORE writing any file:
- `brew install fastlane` (self-contained, zero Ruby management), or
- rbenv/Homebrew Ruby 3.x + `bundle install` against the scaffolded Gemfile (pinned,
  reproducible — better if a CI will run the lanes).
Model A note: this is LOCAL-only tooling; Xcode Cloud needs none of it.

## 2. Discover the project
```bash
xcodebuild -list -json 2>/dev/null
```
Resolve the app **scheme**, **bundle id**, **team id**, and the **UITest scheme** (for
screenshots). Confirm with the user.

## 3. Ask the release parameters (confirm each)
- **App Store locales** you ship — e.g. `en-US`, `fr-FR`, `es-ES`. Drives the metadata
  tree, the Snapfile `languages`, and the release-notes loop. NO default; ask.
- **App Store Connect API key**: Key ID, Issuer ID, path to the `.p8` (or CI secret env
  vars). Role **App Manager**. Never commit the `.p8`.
- **TestFlight** internal group for the mandatory beta.

## 4. Write the scaffold
```bash
mkdir -p fastlane
cp "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<ship-ios-root>")/templates/Fastfile" fastlane/Fastfile
cp "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<ship-ios-root>")/templates/Appfile"  fastlane/Appfile
cp "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<ship-ios-root>")/templates/Snapfile" fastlane/Snapfile
cp "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<ship-ios-root>")/templates/Gemfile"  Gemfile
mkdir -p fastlane/metadata/<locale-1> fastlane/metadata/<locale-2>
```
Then Edit each copied file to replace the `{{…}}` placeholders (scheme, bundle id, team,
locales, ASC key). Add `templates/UITests/ScreenshotUITests.swift` + fastlane's
`SnapshotHelper.swift` to the UITest target — **respect the pbxproj rule if present**: a
new test file must be in `membershipExceptions` or it silently isn't compiled.

## 5. Export compliance (kills a recurring submit-time question)
If the app uses no non-exempt encryption (a 100%-local app with no custom crypto),
set `ITSAppUsesNonExemptEncryption = NO` in the app's Info.plist. Confirm with the user
first — it's a legal declaration.

## 6. Xcode Cloud "Release" workflow (Model A build side)
This lives in App Store Connect, not the repo. The user may already have workflows —
audit them before creating one. The release workflow must have:
- **start condition: Tag Changes** matching `v*` (a manual-launch tag option is NOT a
  trigger — it must be a Tag Changes start condition),
- **action: Archive** the app scheme with **distribution preparation "App Store
  Connect"** — this is what makes the build SUBMITTABLE. A build archived as
  "TestFlight (internal testing only)" can never be submitted to the App Store or to
  external testers; a per-merge internal-beta workflow typically uses that mode, so the
  release workflow must be a separate one,
- **post-action: TestFlight** group(s) — the internal group is the mandatory beta gate;
  external groups go through Apple's Beta App Review first.
That workflow is what the `ship-ios:release` skill tags to trigger. "What to Test" beta notes are
set there (or in App Store Connect), since fastlane doesn't upload the build in Model A.

## 7. Verify
```bash
bundle install
bundle exec fastlane lanes    # lists screenshots / metadata / submit / testflight_status
```
Add `fastlane/report.xml`, `fastlane/Preview.html`, `**/*.p8` to `.gitignore`.
Commit `Gemfile.lock` — it, not the Gemfile's loose constraint, is what pins fastlane
to the same version locally and in CI; never gitignore it.
Tell the user the `ship-ios:release` skill is now available.
