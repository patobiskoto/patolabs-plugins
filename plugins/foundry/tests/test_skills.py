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


def _skill_text(name: str) -> str:
    return open(os.path.join(_SKILLS_DIR, name, "SKILL.md")).read()


def _flat(text: str) -> str:
    return " ".join(text.split())


# PAT-136 / AGENTS.md#R9. These tests prove only that the rule text is written in the
# skills; they do not prove that anyone follows it (that stays a reviewer judgment).
@pytest.mark.parametrize("name", ["start-issue", "resume-issue"])
def test_implementer_packet_requires_r9_and_the_source_of_each_doc_sentence(name):
    text = _flat(_skill_text(name))
    assert "AGENTS.md#R9 in a repository that carries it" in text
    assert "six-point checklist" in text
    assert "points 1 to 5" in text
    assert "(constant, file:line, or page)" in text
    # the six points travel inside the packet, in the order of R9 (a)
    positions = [text.index(f"({n}) ") for n in range(1, 7)]
    assert positions == sorted(positions)
    for fragment in ("cites its constant, file or page", "observed, deduced or unknown",
                     "searched for across the repository", "names the compared sets",
                     "run once, on a copy", "no proper name or user name"):
        assert fragment in text


def test_merge_pr_applies_r9_to_every_correction_commit_including_the_coordinator():
    text = _flat(_skill_text("merge-pr"))
    assert "(a) checklist and (c) minimal-change rule, of a repository that carries them, apply to every correction commit" in text
    assert "including one written by the coordinator itself" in text


def test_merge_pr_describes_the_grouped_deferral_and_its_three_exceptions():
    text = _flat(_skill_text("merge-pr"))
    assert "one follow-up issue per batch" in text
    assert "not one per PR" in text
    assert "through `foundry:intake` before the merge" in text
    assert "cite it in the PR description" in text
    assert 'marks "fix before merge"' in text
    assert "a page frozen after publication" in text
    assert "missing or generic documentation status" in text
    # campaign: the coordinator does not create the follow-up on its own authority
    assert "does not create the follow-up issue on its own authority" in text
    assert "re-review in full" in text
    # standing authorization outside an Epic campaign: two writes, nothing else
    assert "authorized in advance by this skill's rule" in text
    assert "every repository where the plugin is installed" in text
    assert "recorded as rule R9 in the Foundry monorepo" in text
    assert "without asking and report the follow-up issue ID" in text
    assert "every other intake write keeps its confirmation" in text
    # the rule is read after the review and before the merge command
    flat_raw = _skill_text("merge-pr")
    assert (flat_raw.index("### Grouped deferral") < flat_raw.index("### 4. Merge")
            < flat_raw.index("### Correction commits"))


def test_intake_points_to_the_standing_authorization_of_the_two_writes():
    text = _flat(_skill_text("intake"))
    assert "authorizes in advance, in every repository where the plugin is installed, outside an Epic campaign" in text
    assert "`foundry:merge-pr`" in text
    assert "repository that carries it" not in text
    assert "creating the batch follow-up issue" in text


def test_merge_pr_writes_the_per_round_pr_description_line_format():
    text = _skill_text("merge-pr")
    assert ("round <N>; <validated|blocked>; follows <first|blocking|remarks>; "
            "<K> remarks deferred, follow-up <ISSUE-ID|none>") in text


def test_open_pr_cites_the_follow_up_issue_in_the_description():
    text = _flat(_skill_text("open-pr"))
    assert "cites the follow-up issue" in text


def test_fix_before_merge_mark_is_output_only_and_leaves_the_verdict_unchanged():
    review_pr = _flat(_skill_text("review-pr"))
    maigret = _flat(open(os.path.join(_SKILLS_DIR, "..", "agents", "maigret.md")).read())
    assert '"fix before merge"' in review_pr and '"fix before merge"' in maigret
    verdict = "`AC: PASS|BLOCK` · `QUALITY: OK to merge|BLOCK`"
    assert "End with exactly: `AC: PASS|BLOCK · QUALITY: OK to merge|BLOCK`." in review_pr
    assert verdict in maigret
    assert "exactly `outcomes` and `quality`" in review_pr
