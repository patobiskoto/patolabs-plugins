# ship-ios

**The release loop for iOS apps.** A Claude Code + Codex plugin that takes a *cut
version* and ships it to the App Store — the mechanical, repeatable half that lives
after the dev loop ends:

> cut version → **per-locale release notes** → **TestFlight beta gate** → **metadata +
> screenshots + submit** → shipped — the same way, every version, on every app.

ship-ios owns **merge → live**. Foundry (its sibling) owns **idea → merge**. They
**compose by data, not code**: Foundry emits a locale-neutral changelog, ship-ios turns
it into user-facing release notes and drives the store. ship-ios **never depends** on
Foundry — no Foundry, and it takes a changelog file instead.

## Why a separate plugin

Foundry is platform- and tracker-agnostic on purpose. Bolting iOS/fastlane specifics
onto it would leak a domain into a tool meant to serve every project. So the release
loop is its own plugin, scoped to iOS, that *consumes* Foundry's public output. Same
reasoning Foundry itself used to stay out of `claude-project-template`: separate
artifacts, separate lifecycles.

## What it is (and isn't)

- It **is** the orchestration (skills) + a fastlane **template** + a per-locale metadata
  layout + a screenshot UITest scaffold.
- It is **not** your app's `Fastfile`. A Fastfile is per-project config (bundle id,
  scheme, team, device matrix) and must live in the app repo to run in CI / Xcode Cloud.
  `ship-ios:setup` scaffolds it there and fills the blanks. The plugin ships the mould;
  each app repo owns its casting.

## Install

Claude Code:

```
/plugin marketplace add patobiskoto/patolabs-plugins
/plugin install ship-ios@patolabs
```

Codex:

```
codex plugin marketplace add patobiskoto/patolabs-plugins
codex plugin add ship-ios@patolabs
```

Use `/ship-ios:<skill>` in Claude Code and `$ship-ios:<skill>` in Codex.

## Prerequisites

- **fastlane** installed (`brew install fastlane` or a `Gemfile`).
- An **App Store Connect API key** with the **App Manager** role (least privilege — it
  can manage builds/TestFlight/submission, not users or finance). You get a `.p8` (once),
  a Key ID, and an Issuer ID. Never commit the `.p8`; reference it by path / CI secret.
- Optional: **Foundry** (`foundry@patolabs`) if you want release notes auto-assembled
  from your tracker milestone. Optional: **XcodeBuildMCP** for build/sim from the agent.

## Build model — A: Xcode Cloud builds, fastlane submits

fastlane **never builds the release binary**. Xcode Cloud archives, signs (cloud-managed
signing — zero certs to manage), and uploads to TestFlight on a tag. fastlane owns what
it does best: per-locale release notes, metadata, screenshots, and submission. The only
local build left is the UITest run for screenshots (unsigned). This keeps one build
system (the same Xcode Cloud that gates your PRs), no signing to maintain, and the
in-code part (notes/metadata/submit) in the Fastfile.

## The two skills

| Skill | When | What |
|---|---|---|
| `ship-ios:setup` | once per app repo | detect scheme + bundle id, scaffold `fastlane/` + Gemfile, wire the ASC key, set export compliance, create the per-locale metadata tree, guide the Xcode Cloud "Release" workflow |
| `ship-ios:release` | per version | changelog → release notes **per locale** (human-edited) → version + notes land via PR → tag the merged SHA, push that one tag (triggers the Xcode Cloud build → TestFlight) → **beta gate on a real device** → screenshots (if UI changed) → `fastlane submit` (**human-gated**) → hand back to the tracker |

## Locales are configuration, never hardcoded

There is **no default language**. The set of directories under `fastlane/metadata/`
(matched by `Snapfile`'s `languages([...])`) is the source of truth. `ship-ios:release`
loops over them and writes native release notes per locale — it does not translate one
canonical string. Add a locale = add a dir + a line; the pipeline follows.

## The fastlane lanes (scaffolded into your repo)

- `screenshots` — `snapshot` (device × locale matrix, from your UITest) + `frameit`.
- `metadata` — push store text only (no binary, no screenshots) to iterate between versions.
- `submit` — attach metadata + screenshots to the Xcode-Cloud TestFlight build and submit
  for review (`skip_binary_upload: true` — fastlane builds nothing).
- `testflight_status` — report the latest TestFlight build for a version (the beta gate polls it).

Build + TestFlight upload are the **Xcode Cloud** "Release" workflow's job (tag-triggered).
`submit` is set to **auto-release on approval** (`automatic_release: true`) — a project
decision, one line to switch to manual + phased rollout. A `verify_locales` guard refuses
to push if any shipped locale is missing its release notes.

## Human gates (non-negotiable)

- **Beta on a real device** before submit — the simulator doesn't reproduce haptics,
  real-audio, or HealthKit.
- **Submission** is outward and hard to reverse — the skill always asks for an explicit
  "go" before `fastlane release`.

## How it composes

```
Foundry          ship-ios                    Xcode Cloud            fastlane / ASC
idea→merge  ──►  query changelog          ──► tag → build+sign  ──► (build in TestFlight)
                 per-locale notes,            → TestFlight
                 release PR → tag merged SHA
                 beta gate (real device)  ◄────────────────────
                 fastlane submit          ──────────────────────► metadata+screens+submit
                 ◄── close milestone / intake feedback ──────────── shipped
```

## Layout

```
.claude-plugin/plugin.json   Claude Code manifest
.codex-plugin/plugin.json    Codex manifest
skills/setup/SKILL.md        provision fastlane into an iOS repo
skills/release/SKILL.md      cut version → shipped, per-locale, gated
templates/Fastfile           parameterized lanes (beta / screenshots / release)
templates/Appfile Snapfile   identity + screenshot matrix
templates/UITests/…          snapshot UITest scaffold
templates/metadata_example/  the per-locale metadata layout (illustrative)
scripts/changelog_bridge.py  thin: pulls Foundry's changelog facts (optional)
```

## Status

v0.2.0 — dual Claude Code/Codex packaging over the live-tested Model A flow (Xcode
Cloud builds, fastlane submits). Lanes: screenshots / metadata / submit /
testflight_status; per-locale release notes; export-compliance + clean-tree +
locale-completeness guards; portable Foundry changelog discovery; screenshot scaffold.
