# Changelog fragments

One file per issue, `<ISSUE-ID>.md`, holding one Markdown list item (the text style of
the entries in `CHANGELOG.md`) whose first line starts with `- <ISSUE-ID>:` (the file's own
issue id), ends with a single newline. A PR adds its own fragment
here and never edits `## Unreleased` in `CHANGELOG.md`, which stays empty between
releases. The release step assembles the fragments with `scripts/changelog_assemble.py`
into the dated section and deletes them (`skills/release/SKILL.md`).
