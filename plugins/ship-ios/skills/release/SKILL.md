---
name: release
description: >
  Ships a cut version end-to-end (Model A — Xcode Cloud builds, fastlane submits): sets
  the marketing version and tags (Xcode Cloud builds + uploads to TestFlight), gates on
  a real-device beta, drafts user-facing release notes PER App Store locale, then submits
  via fastlane (behind explicit confirmation). USE WHEN the user invokes
  /ship-ios:release (Claude Code), $ship-ios:release (Codex), "on ship la v1.2", or
  "release to the App Store".
argument-hint: "<version> [milestone]"
allowed-tools: Bash(fastlane:*), Bash(bundle:*), Bash(python3:*), Bash(git:*)
---

# ship-ios:release — cut version → shipped (Model A)

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<ship-ios-root>` from this skill's absolute path by removing
`/skills/release/SKILL.md`. Never pass the placeholder literally.

Foundry owns idea→merge; this owns merge→live. Xcode Cloud builds/signs/uploads the
release binary; fastlane owns notes, metadata, screenshots, submit. Run `ship-ios:setup`
first if `fastlane/Fastfile` isn't in the repo.

## 1. Assemble the changelog (facts)
Prefer Foundry when it's wired for this repo; fall back to a file otherwise:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<ship-ios-root>")/scripts/changelog_bridge.py" "<milestone>"   # exit 3 → no Foundry, use a file
```
These are FACTS (id/title/type/labels), not notes.

## 2. Create the release branch — BEFORE touching any file
Branch first: from the second release on, `release_notes.txt` are tracked files, and
foundry:start-issue refuses a dirty worktree — writing anything before branching would
dead-end the Foundry path.

**Foundry drives this repo:** the release change needs a tracker issue, and the branch
must be minted by Foundry (foundry:open-pr derives the issue id from the
`<type>/<id>-<slug>` branch shape that only foundry:start-issue creates — it refuses a
hand-made branch). Create or pick the release chore issue (e.g. "Release v<version>",
via foundry:intake or the tracker), then run foundry:start-issue <ID>.

**No Foundry:** `git switch -c chore/release-v<version>`.

## 3. Draft release notes PER LOCALE (the judgment step)
For EACH configured App Store locale (dirs under `fastlane/metadata/`), rewrite the
shipped issues into user-facing notes IN THAT LANGUAGE — group by benefit, drop
internal-only items (chore/test/refactor), lead with what the user feels. Write natively
per locale, don't translate one canonical string.
```
en-US → "New ambient sounds to help you fall asleep · Fixed a rare launch crash"
fr-FR → "De nouveaux sons d'ambiance pour vous endormir · Correction d'un crash rare au lancement"
```
Show the full set for approval, write each to `fastlane/metadata/<locale>/release_notes.txt`,
and STOP for human edit — this is user-facing copy.

## 4. Land the version bump + notes on the default branch (via PR)
In a Foundry repo the guard hook denies any push of the default branch — and that's the
right discipline everywhere: the release change lands like any other change, through a
PR. Stage explicitly: `commit -a` would miss brand-new files, and the first release's
`release_notes.txt` ARE brand-new.
```bash
# set MARKETING_VERSION to <version> (agvtool, or edit the build setting), then:
git add fastlane/metadata <files-touched-by-the-version-bump>
git diff --cached --stat      # verify: version bump + release notes, nothing else
git commit -m "chore(release): v<version>"
```
**Foundry:** foundry:open-pr, then foundry:merge-pr (CI gate included). Its final
message reports `sha mergé <sha>` — that squash SHA is what step 5 tags.
**No Foundry:** push the branch (`git push -u origin chore/release-v<version>`) and
merge a PR the way this repo usually does; note the PR's merge commit SHA.

## 5. Tag the MERGED commit → triggers the Xcode Cloud build
Xcode Cloud builds from the tagged commit, so tag the squash commit that actually landed
on the default branch — never the local pre-merge commit (the default branch does not
contain it after a squash merge), and never `origin/<default>` blindly (another PR may
have landed since):
```bash
git fetch origin
git tag "v<version>" <merged-sha>   # `sha mergé` from foundry:merge-pr, or the PR's merge commit
git push origin "v<version>"        # push ONLY this tag, nothing else
```
The tag push is allowed by the Foundry guard (only default-branch pushes are denied).
The Xcode Cloud "Release" workflow (tag-triggered) archives, signs (cloud-managed), and
uploads to TestFlight. Nothing to build locally here.

## 6. Beta gate (mandatory) — poll, then validate on device
```bash
bundle exec fastlane testflight_status version:"<version>"   # poll until the build is processed
```
**Right-build check**: a per-merge beta workflow may have ALREADY uploaded builds for
this version — those are archived "TestFlight internal only" and are NOT submittable.
The gate is satisfied only by a build newer than the tag push (the tag-triggered
release workflow's build, prepared "App Store Connect"). Note the build number that was
latest BEFORE tagging; wait until `testflight_status` returns a HIGHER one.
When that build is in TestFlight, STOP: ask the user to validate on a REAL device
(haptics, real audio, HealthKit don't reproduce in the simulator). Do not proceed until
they confirm.

## 7. Screenshots (only if the UI changed this version)
```bash
bundle exec fastlane screenshots      # snapshot (device × locale) + frame — slow, ask first
```

## 8. Submit (human-gated)
```bash
bundle exec fastlane submit version:"<version>" build:"<build_number_from_testflight>"
```
`version:` lets deliver create the App Store version draft in ASC when it doesn't exist
yet: Xcode Cloud uploads builds, but does not create store versions.

**Known failure — build attach refused** (fastlane #19633): deliver can refuse to attach
a valid build with "could not be added" even though metadata and screenshots uploaded.
Do not retry the lane in a loop. Finish once in the ASC UI instead: open the version,
under Build select "+" and choose the verified build, choose automatic release, then
Submit for Review.
`submit` attaches metadata + screenshots to the Xcode-Cloud build and submits for review,
auto-release on approval. Pass the build number verified in step 6 — the tag-triggered
one, never an older internal-only build (Apple rejects those at submission).
**Submission is outward and hard to reverse — get an explicit "go" from the user before
running it.**

## 9. Hand back to the tracker
On success: if Foundry drives this repo, tell the user to close the milestone and route
post-release feedback/crashes through the `foundry:intake` skill. The loop closes on
the tracker.

## Rules
- Never submit without an explicit human go (step 8).
- Never hardcode a language — the locale list is configuration; loop over it.
- The beta gate (step 6) is not skippable for a public app.
- fastlane never builds the release binary — that's Xcode Cloud's job (Model A).
- Never push the default branch or `--tags` — the release lands via PR (step 4) and
  publishes exactly one tag (step 5).
- Branch (step 2) before writing anything — foundry:start-issue requires a clean
  worktree.
