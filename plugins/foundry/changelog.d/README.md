# Changelog fragments

One file per issue, `<ISSUE-ID>.md`, holding one Markdown list item (the text style of
the entries in `CHANGELOG.md`) whose first line starts with `- <ISSUE-ID>:` (the file's own
issue id), ends with a single newline. A PR adds its own fragment
here and never edits `## Unreleased` in `CHANGELOG.md`, which stays empty between
releases. The release step assembles the fragments with `scripts/changelog_assemble.py`
into the dated section and deletes them (`skills/release/SKILL.md`).

Order: the assembled section lists the fragments by issue number, descending. Sections
published before fragments existed follow merge order instead (the 1.3.0 section reads
PAT-197, PAT-204, PAT-202, PAT-200, ...); they are frozen and not reordered. `assemble`
refuses a fragment that holds more than one list item (a line starting with `- ` after the
first line; an indented sub-list is part of the entry), and a version or a date that is not
`X.Y.Z` / a valid `YYYY-MM-DD`, the heading shape that `tests/test_release_contract.py` parses.

Transition: a pull request opened before this layout that still adds an entry under
`## Unreleased` conflicts with `main` once a fragment-based change is merged, and fails
`test_shipped_fragments_assemble_and_unreleased_may_be_empty` (`tests/test_changelog_assemble.py`),
which requires `## Unreleased` to stay empty. Move the entry to a fragment after merging `main`.
