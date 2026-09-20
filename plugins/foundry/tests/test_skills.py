"""Skill linter — pins the FORM invariants of every SKILL.md, in CI.

Two classes of skill bugs were fixed in PR #1 and nothing but this file stops
them from coming back:
- a command whose prefix isn't the binary (env-var assignment, `echo … |` pipe)
  doesn't match the skill's `allowed-tools: Bash(<bin>:*)` prefix rule, so every
  call prompts for permission;
- `${N:-default}` bash expansion isn't supported by skill argument substitution.
"""
import json
import os
import re

import pytest

_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "skills")
_SKILL_FILES = sorted(
    os.path.join(_SKILLS_DIR, d, "SKILL.md")
    for d in os.listdir(_SKILLS_DIR)
    if os.path.isfile(os.path.join(_SKILLS_DIR, d, "SKILL.md"))
)


def _frontmatter(text: str) -> dict:
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    assert m, "missing frontmatter"
    fm = {}
    for line in m.group(1).splitlines():
        km = re.match(r"^([a-z-]+):\s*(.*)$", line)
        if km:
            fm[km.group(1)] = km.group(2)
    return fm


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)```", text, re.S)


def _command_lines(block: str) -> list[str]:
    return [ln.strip() for ln in block.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _allowed_binaries(fm: dict) -> set[str]:
    return set(re.findall(r"Bash\((\w+)", fm.get("allowed-tools", "")))


@pytest.mark.parametrize("path", _SKILL_FILES, ids=lambda p: p.split(os.sep)[-2])
def test_skill_form(path):
    text = open(path).read()
    fm = _frontmatter(text)

    # frontmatter contract
    assert fm.get("name"), "frontmatter must set name"
    assert fm["name"] == os.path.basename(os.path.dirname(path)), "name must match dir"
    assert "description" in fm or "\ndescription:" in text, "missing description"
    allowed = _allowed_binaries(fm)
    assert allowed, "allowed-tools must allow at least one Bash(<bin>:*)"

    for block in _bash_blocks(text):
        for line in _command_lines(block):
            # unsupported bash-default substitution
            assert not re.search(r"\$\{\d+:-", line), \
                f"${{N:-…}} isn't supported by skill substitution: {line}"
            # the permission rule matches the command PREFIX: it must be the binary
            first = line.split()[0]
            assert "=" not in first, \
                f"env-var prefix won't match allowed-tools Bash(<bin>:*): {line}"
            assert first != "echo", \
                f"echo-pipe defeats the allowed-tools prefix match — use `< file`: {line}"
            assert first in allowed, \
                f"'{first}' not in allowed-tools {sorted(allowed)}: {line}"
            # foundry tooling goes through the launcher
            if "foundry" in line and first == "python3":
                assert "foundry_cli.py" in line, \
                    f"foundry invocations go through foundry_cli.py: {line}"


def test_every_skill_is_linted():
    # the launcher pattern only holds if every skill is actually covered
    assert len(_SKILL_FILES) >= 12, _SKILL_FILES


def test_review_pr_requires_claim_capability_before_diff_access():
    """The reviewer has an authoritative, semantic gate before it may read a diff."""
    review_pr = os.path.join(_SKILLS_DIR, "review-pr", "SKILL.md")
    text = open(review_pr).read()
    block = re.search(
        r"^```json\n(?P<contract>\{[^\n]+\})\n```$", text, re.M,
    )
    assert block, "missing authoritative reviewer capability gate"
    contract = json.loads(block.group("contract"))

    assert contract == {
        "required_before_diff_read": ["diff_hash", "claim_id"],
        "on_missing": "STOP",
    }


def test_merge_pr_normal_review_materializes_proof_before_terminal_completion():
    merge_pr = os.path.join(_SKILLS_DIR, "merge-pr", "SKILL.md")
    text = open(merge_pr).read()

    assert "routing record-review-proof" in text
    assert "--outcomes-file /private/path/review-proof.json" in text
    assert "A pass is all AC `pass`" in text and "with `quality=mergeable`" in text
    assert "do not fabricate a proof or mark the claim completed" in text
    assert "routing complete-review" not in text
