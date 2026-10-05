# Writing a Linear ADR body

Guide for the author of an ADR body stored in a Linear Document (`adr create`, body edit,
historical import). Reference: [linear-tracker.md](linear-tracker.md) (PAT-94, PAT-103, PAT-106).
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

A top-level `- ` bullet list written directly under a paragraph line (PAT-106) is read back
with one empty line inserted between the paragraph and the list (and `- ` rewritten to
`* `, as for every bullet):

```text
Pour chaque tâche :\n- a\n- b   ->   Pour chaque tâche :\n\n* a\n* b
```

The adapter models only what was observed: one paragraph line starting with a letter and
ending with ` :`. Narrowed recognised shape: the paragraph is a single LF-terminated line
that starts with a letter (not a digit, `*`, backtick, `[`, `=`, `\`, `#`, `<`, `|`, quote or
punctuation), has no pipe, trailing space or backslash, is not a link reference definition,
is preceded by an empty line and is not at the start of the body. Generalised from that one
observation (not separately observed): the list may have 1..n plain one-line `- ` items
(ended by an empty line or the end of the body), and any block may precede the empty line.
Prefer writing the stored form directly (an empty line before the list). The neighbours
below are refused before any write; a list glued to any non-recognised paragraph line (pipe
line, `#tag`, line starting with a digit, `**`, backtick, `[`, `=`, `<`...) is refused as
"outside the observed shape":

| Shape glued to a paragraph line | Cause (after `unsupported list Markdown in ADR body (line N)`) |
| --- | --- |
| `1.` / `1)` numbered list | `numbered list glued to a paragraph` |
| `*` or `+` bullet, `-` followed by a tab | `bullet list with an unobserved marker glued to a paragraph` |
| a line `-` or `- ` alone (a setext underline, not an empty item) | `dash line glued to a paragraph (setext underline)` |
| list in or next to a `>` quote: under a quoted line that is not an item of the same quote (`> P\n> - a`, also after an empty quote line: `> P\n>\n> - a`), at another quote depth than the line above (`> - a\n> > - b`, `P\n> - a`, a closing fence then `> - a`), or next to a `>` placed behind 4 or more columns, a tab or a list marker (`- a\n    > - b`, `- a\n- > b`); also any `>` line directly under a list line when it opens or changes the quote (`- a\n  > b`, `- a\n> b`) | `list glued in a blockquote context` |
| list right under an indented line that is not inside a list item: indented code, also when the code line starts with a marker (`    - x`, a tab, `    1. x`), an indented paragraph, or a line indented less than the item content (`- a\n cont`, `1. a\n  cont`), in or out of a quote | `list glued under an indented line` |
| list marker indented by one or more columns right under a paragraph (1 to 3 spaces is still a top-level list, 4 or more is paragraph text) | `indented list glued to a paragraph` |
| list under a `--` line (two dashes are a paragraph or a setext underline, not a thematic break) | `list glued under a dash-only line` |
| under a heading, a thematic break or a closing fence: a marker line that is not a top-level item (4 or more columns or a tab is indented code: `## H\n    - a`; more than 9 digits is a paragraph line) | `marker line that is not a top-level list item glued under a heading, thematic break or closing fence` |
| a `- ` list outside the exact shape (paragraph line not starting with a letter or holding a pipe, multi-line or hard-break paragraph, paragraph at the start or right after a heading, CRLF, reference definition, nested or continued or lazy or task or empty item in the run, fence in the run) | `bullet list glued to a paragraph outside the observed shape` |

The check is a closed whitelist (PAT-ADR-0002): for every line that looks like a list item
and has a non-empty line right above it, that previous line must be in one of three accepted
sets, and anything else is refused.

1. Inside a list, unchanged: the previous line is a list item (0 to 3 spaces, then `-`, `*`,
   `+`, or 1 to 9 digits and `.`/`)`), or a line directly under one and indented at least to
   the content of that item (its continuation or a nested item). `A\n\n- a\n  cont\n- b` is
   therefore not refused. In a quote, only a previous item line of the same quote depth
   counts (`> - a\n> - b`).
2. The observed shape above: one empty line is inserted.
3. Still unobserved and NOT refused (behaviour unchanged, no insertion modelled): the previous
   line is an ATX heading (`## H\n- a`), a thematic break (`---`, `***`, `___`, `- - -`;
   not `--`), or a closing fence, and the list line is an unquoted top-level item (0 to 3
   spaces, at most 9 digits).

Everything else is refused with one of the causes of the table; a previous line that no rule
names is refused as `list glued to an unclassified line`. A list context does not survive an
empty line, a fenced block, a line indented less than the item content or a tab in a quoted
indentation: an item glued under such a line is refused. Quotes are closed by construction:
the quote depth is the number of `>` in the 0 to 3 column prefix of a line and must be the
same on a list line and on the line above it; a `>` behind 4 or more columns, a tab or a list
marker is never accepted next to a list. A quote that holds no list line and is not directly
under a list line is not refused by this check. A continuation, nested, lazy, task or
empty item is refused only inside the run glued to a paragraph (set 2), where the shape must
be exact. A line of more than 9 digits and `.` (`1234567890. x`) is a paragraph line, not an
item. `P\n--` / `P\n---` are not list lines and are not handled here. A `<` line is refused
earlier by the ambiguous-HTML rule. Put an empty line between the paragraph and the list to
avoid all of these.

## Refused, with the cause

| Shape | Cause prefix | Do instead |
| --- | --- | --- |
| any table (delimiter row such as `\|---\|---\|`, with or without alignment colons or outer pipes, also in a quote) | `unsupported table Markdown` | Linear rewrites the delimiter row (`\| -- \| -- \|`), which is not modelled: use a list or a fenced block |
| two or more empty lines whose lines are not exactly `\n` (CRLF `\r\n`, a line with spaces or tabs) | `unsupported blank lines` (or an earlier whitespace refusal) | use LF only and truly empty lines |
| one empty line at the start or the end, or two or more between blocks, next to an indented (4 columns or a tab) or `>` quoted line | `unsupported blank lines` | keep one empty line between two ordinary blocks |
| a body made only of empty lines | `unsupported empty body` | write content |
| a final newline after a closing fence, inside an unclosed fence, after `\r` or a trailing tab, or when the last line is indented or quoted | `unsupported final newline` | end the body on a prose line without trailing newline, or close the fence and add a closing sentence |
| list blank lines outside the observed form | `unsupported list Markdown` | tight list |

Trailing spaces before a newline, hard breaks and raw HTML were already refused by the
earlier model. The strict check applies to a new or edited body, and also to the unchanged
body of a migrated historical version 0 whose stored bytes the model does not predict: a
status change, link or supersession on such an ADR is refused for the table, final
newline, unobserved blank-line, empty-body and indented or quoted last-line cases above, and
for every PAT-106 glued-list refusal (the causes of the PAT-106 table above: numbered,
`*`/`+`, setext dash, blockquote context, indented line, `--` line, non-top-level marker under
a heading, thematic break or closing fence, outside the observed shape, unclassified line). This is
better than before: the refusal comes before any write, instead of a write followed by a
failed verification that left an orphan Document. Reads stay additive: a stored Document
matches if it equals the canonical bytes, the PAT-106 output, the PAT-103 output (no empty line
inserted before a glued list) or the pre-PAT-94 output (the PAT-94-only rendering is no longer
accepted for the generalised cases).
A body that Linear would not leave stable under the blank-line merge is not expected to
exist as stored, since Linear merges or removes those lines.

Evidence covers 13 observed cases. How the neighbours are handled (merge around a `---`
separator, setext headings, lazy blockquote continuation, `~~~` or info-string fences,
final newline removed after a heading or list item) is a generalisation consistent with
the CommonMark tree, where repeated empty lines do not exist; it is not an exact
observation of each case.

## Check a body before writing

`adr create` (or a body edit) runs the check before any write: a refusal raises
`Linear ADR body has unsupported Markdown serialization` with the cause above and
creates nothing. Compare your body with the causes in the tables, there is no separate
public checker.

Unobserved shapes stay refused until a new bounded probe qualifies them; do not
rely on the rendering of anything this guide does not list.
