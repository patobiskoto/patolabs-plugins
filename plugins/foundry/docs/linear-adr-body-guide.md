# Writing a Linear ADR body

Guide for the author of an ADR body stored in a Linear Document (`adr create`, body edit,
historical import). Reference: [linear-tracker.md](linear-tracker.md) (PAT-94, PAT-103).
Evidence: `qualification/pat-103-linear-blank-lines-observation.json` (probe of
2026-10-05 on a throwaway project; bytes sent and read back, ids and SHA-256).

Why a body can be refused: Linear re-serializes the Markdown it stores. The adapter
predicts that rendering with a closed model of **observed** transformations only
(PAT-ADR-0002) and verifies the read-back against it. A shape it cannot predict is
refused **before any write**, so no Document is created and no orphan is left. The
refusal is `Linear ADR body has unsupported Markdown serialization`; the cause (the
exception `__cause__`) names the family.

## Accepted, and what Linear stores

| You write (outside fenced code) | Linear stores |
| --- | --- |
| two or more empty lines between blocks | exactly one empty line |
| empty lines at the start | removed |
| one or more final newlines | removed |
| empty lines inside a fenced block (``` or ~~~) | unchanged |
| a single empty line | unchanged |

Prose, headings and fenced blocks are all covered. The adapter applies the same
rendering when it verifies the read-back, so none of this is an error. Prefer
writing the stored form directly (one empty line, no final newline) so that what you
see is what is stored.

```text
# Title\n\n\nText\n\n\n## Sub\n   ->   # Title\n\nText\n\n## Sub
```

Lists keep their own rules (PAT-94/PAT-101): ordered `1.` to `9.` items separated by one
empty line are read back tight; any other list blank line, or a gap of two or more
empty lines next to a list, is refused with `unsupported list Markdown in ADR body
(line N)`. Write tight lists (no empty line between items).

## Refused, with the cause

| Shape | Cause prefix | Do instead |
| --- | --- | --- |
| any table (delimiter row such as `\|---\|---\|`, with or without alignment colons or outer pipes, also in a quote) | `unsupported table Markdown` | Linear rewrites the delimiter row (`\| -- \| -- \|`), which is not modelled: use a list or a fenced block |
| two or more empty lines whose lines are not exactly `\n` (CRLF `\r\n`, a line with spaces or tabs) | `unsupported blank lines` (or an earlier whitespace refusal) | use LF only and truly empty lines |
| two or more empty lines next to an indented (4 columns or a tab) or `>` quoted line | `unsupported blank lines` | keep one empty line there |
| a final newline after a closing fence, inside an unclosed fence, after `\r`, or when the last line is indented or quoted | `unsupported final newline` | end the body on a prose line without trailing newline, or close the fence and add a closing sentence |
| list blank lines outside the observed form | `unsupported list Markdown` | tight list |

Trailing spaces before a newline, hard breaks and raw HTML were already refused by the
earlier model. An existing stored ADR is not re-checked: only a new or edited body
is strict; reads stay additive and accept what was accepted before.

## Check a body before writing

```text
python3 - <<'PY'
import sys; sys.path.insert(0, "plugins/foundry/tooling")
from foundry.trackers.linear import _preflight_adr_body_readback as check
check(open("body.md", encoding="utf-8").read())   # raises if refused, with __cause__
PY
```

Unobserved shapes stay refused until a new bounded probe qualifies them; do not
rely on the rendering of anything this guide does not list.
