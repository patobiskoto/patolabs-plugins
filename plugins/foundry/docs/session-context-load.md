# Context loaded at the start of a session: contract, agent profiles, skill (PLP-362)

PLP-362 measures what three repository elements add to the context of a fresh Claude Code
session and reduces the first two. Nothing here changes a routing, a gate or a decision;
the text moved out of the contract is in `process-contract-r1-r6.md`,
`process-contract-r7.md` and `process-contract-r9.md` (same folder). Each figure below is
marked `observed` (read from a file or a transcript), `deduced` (computed from an observed
figure with a stated assumption) or `unknown`.

## Method

- Byte sizes: `wc -c` on the files of the repository at commit `98b43b5` (base of the
  issue) and on the working tree after the change. Observed.
- Agent list size: for each of the 90 files in `plugins/foundry/agents/`, the line
  `- <name>: <description>` built from the frontmatter keys `name` and `description`
  (folded descriptions joined on one line), plus a newline, summed in bytes. The exact
  format the host uses to list agents is unknown; this line shape is an assumption, so the
  figure is deduced.
- Token estimate: bytes divided by 4. The ratio of 4 bytes per token is a rule of thumb for
  English text, not measured for the tokenizer of the host; code spans and paths usually
  lower it, so every token figure is an estimate (deduced), the estimate may undercount.
- Observed total of a first request: the `usage` of the first assistant record of a
  main-thread session transcript under `~/.claude/projects/` (keys `input_tokens`,
  `cache_creation_input_tokens`, `cache_read_input_tokens`; no content read or copied). Two
  sessions of this repository written by host version 2.1.319 show
  72,818 and 72,876 tokens of cache creation with 2 input tokens and no cache read. The
  composition of that total (system prompt, tool definitions, user memory, contract, agent
  list, skill list) is unknown: the transcript does not split it.

## Measurement

| Element | Bytes before | Bytes after | Estimate before | Estimate after |
| --- | --- | --- | --- | --- |
| `AGENTS.md` (read by Codex; same bytes in `CLAUDE.md`, read by Claude Code) | 32,328 | 7,530 | about 8,100 tokens | about 1,900 tokens |
| Agent list (90 profiles, name and description) | 10,003 | 10,003 | about 2,500 tokens | about 2,500 tokens |
| `skills/merge-pr/SKILL.md` | 33,693 | 33,693 | about 8,400 tokens if loaded | unchanged |

Share of the observed first request (about 72,800 tokens, composition unknown): the contract
before the change is about 11 percent and the agent list about 3.4 percent, both deduced
from the ratio above and valid only if each element was fully in that request (unknown: the
transcript does not show which files the host loaded into that request).

`merge-pr/SKILL.md`: the body is deduced not to be in the context of a fresh session. A skill
is listed by its frontmatter (440 bytes for `merge-pr`, observed) and the body is read when the
skill is invoked; that behaviour of the host is not observed here. The body is therefore a
cost of each merge, not of each session, and it is left unchanged by PLP-362.

## What changed

- The contract keeps `R1` to `R9` as headings, so `AGENTS.md#R<n>` citations resolve. R1, R5
  and R6 are shortened; R7 and R9 are summarised, with R9 (a) kept verbatim. The full text,
  moved verbatim, is in the three `process-contract-*.md` pages.
- Tests that asserted a sentence now moved read it from its page
  (`tests/test_process_contract.py`); the assertions are unchanged.
- Agent profiles: none is removed or renamed; the reason per family is in
  `model-routing.md`, section "Agent profiles and why each family is kept".
