# PAT-201 - issue links of two historical native ADR bodies

Status: qualified for exactly two source bodies; every other body keeps the PAT-85 profile.
Profile: `_PAT_201_PROFILES` and `_linear_pat_201_readback_body`
(`tooling/foundry/trackers/linear.py`). Tests: `pat201` in `tests/test_linear_tracker.py`,
fixtures `tests/fixtures/pat201_foundry_adr_{0002,0018}_{source,readback}.md`.

## Census (read-only)

Method (observed): one `LinearTracker._adr_snapshot` read of the Foundry project, then
`_preflight_adr_body_readback` run offline on the latest version of each ADR with the
arguments `link_adr_issue` passes. Snapshot taken 2026-10-15T02:50:41Z (04:50 Paris);
51 ADRs (27 `FOUNDRY-ADR`, 24 `PAT-ADR`), identical to the 51 rows of `query adrs` read just
before. The index has no version or hash of its own: the snapshot identity is the latest
version Document id of each refused ADR below. No `link-issue`, `edit` or other write was
made; "pre-check refused" is the preflight verdict, and **no link was attempted** for any
row (`link_adr_issue` runs this preflight before its first write, `_create_adr_issue_link`).

| ADR | status | latest version (document id) | source SHA-256 | pre-check | form |
| --- | --- | --- | --- | --- | --- |
| `FOUNDRY-ADR-0002` | accepted | v0 `e24493f1-23b6-4a5c-b087-51525ab3608e` | `abb31172...c4661465` | refused, strong inline-code | line 16 `**tous `skipped`**` (a second `**` span on the line); line 46 `**Un statut legacy `success` posé par un bot non-CI**` (code inside the span) |
| `FOUNDRY-ADR-0003` | accepted | v0 `af4c4af3-d205-4eec-b1d0-ab395addd9df` | `bd7092a5...aae97db5` | refused, strong inline-code | line 52 `**Un flag `--full` sur les listes**` (code inside the span) |
| `FOUNDRY-ADR-0018` | accepted | v0 `54d8c108-5cb0-48a1-a4d1-3054d022725d` | `ce9869d7...8e14c5ea` | refused, strong inline-code | line 104 `**Un gate binaire « `tooling/` modifié ⇒ `docs/` modifié »**` (two code spans inside the span) |
| `FOUNDRY-ADR-0015` | accepted | v0 `f5935b49-cc6f-46a9-a95c-c228f8224bf4` | `31f3b61a...` (prefix) | refused, **multiline inline** (another refusal) | not the strong/code form; out of scope |
| `FOUNDRY-ADR-0017` | accepted | v0 `b40ee7e6-8940-4c47-a240-fbaabb1e598b` | `c4185372...` (prefix) | refused, **multiline inline** (another refusal) | not the strong/code form; out of scope |

The other 46 ADRs pass the preflight. Line numbers are 1-based in the source body. The reason
each line fires is `deduced` from the profile's rules (a code span inside a strong span other
than a lone trailing one, and the one-line context rule), not from a trace of every clause.
`FOUNDRY-ADR-0003` is hit by the same refusal but was not in the PAT-201 scope: it is not
qualified here and stays refused (decision for a follow-up).

## Observed Linear rendering

Observed: the stored version-0 Document of each ADR holds the bytes Linear returned after the
import; the fixtures `*_readback.md` are those bytes after the envelope. Against the source:
`- ` list markers become `* `; a bold span with a trailing code keeps `**text** `code``
(PAT-85); a bold span containing code is split into one bold span per text fragment with the
code outside (`**Un statut legacy** `success` **posé par un bot non-CI**`); the text
`(AGENTS.md/CLAUDE.md)` becomes `([AGENTS.md/CLAUDE.md](<http://AGENTS.md/CLAUDE.md>))` (the
same autolink form as PAT-86); a bold span crossing a newline is split (PAT-71).

Deduced, not observed: that Linear renders the version-1 Document written by `adr link-issue`
the same way. The real link was not run. If the rendering differs, the pre-check still passes
through the profile, the link comment and the version-1 Document are written
(`link_adr_issue`, `_append_adr_version` in `trackers/linear.py`), and the divergence is only
seen at read-back, after those writes.

## Profile

Each entry binds a full source SHA-256 to its rendered SHA-256 (`8cd2a8f7...` and `54947bd7...`).
The profile rewrites the exact source fragments listed in `_PAT_201_PROFILES` (each must occur
exactly once), runs the general model on the result, applies the autolink rewrite after it, and
requires the final digest. The three fragments:

- FOUNDRY-ADR-0002: line 16 and line 46 above;
- FOUNDRY-ADR-0018: line 104 above, then the autolink after rendering.

Limits: a body differing by one byte, a different ADR, or the same form elsewhere is refused
before any write (tests `altered_and_unknown_forms_stay_refused`, `altered_body_is_refused`).
The profile has no inverse; the witness keeps the source bytes (PAT-ADR-0002). No status,
proof, or body is rewritten; no global normalisation is added. `FOUNDRY-ADR-0003`,
`-0015` and `-0017` are unchanged.

Documentation status (AGENTS.md#R5): `_PAT_201_PROFILES` (new private constant) is documented
here and in the pointer paragraph of `docs/linear-tracker.md`; the changelog fragment
`changelog.d/PAT-201.md` records it; no CLI verb, option or configuration key changed.
