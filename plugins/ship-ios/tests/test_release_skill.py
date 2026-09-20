"""Form invariants of the release skill's git commands.

The release flow must compose with the Foundry guard hook (which denies any
push whose destination is the default branch) and must not leak unrelated
local state. Pinned here:
- no `git push --tags` (publishes every local tag, and is a bare push the
  guard denies from the default branch) — the release pushes ONE tag;
- no bare `git push` (denied by the guard on the default branch);
- no push refspec targeting the default branch (main/master/HEAD);
- no `git commit -a` (misses brand-new files — first-release release_notes.txt);
- explicit staging is verified (`git diff --cached`) before committing;
- the single-tag push (`git push origin "v<version>"`) is present.
"""
import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills/release/SKILL.md"


def _command_lines():
    text = SKILL.read_text(encoding="utf-8")
    lines = []
    for block in re.findall(r"```bash\n(.*?)```", text, re.DOTALL):
        for ln in block.splitlines():
            ln = ln.split(" #", 1)[0].strip()  # commands here never quote a '#'
            if ln and not ln.startswith("#"):
                lines.append(ln)
    return lines


def _push_args(line):
    """Tokens after `git push`, or None when the line isn't a push."""
    toks = line.split()
    if toks[:2] != ["git", "push"]:
        return None
    return toks[2:]


class ReleaseSkillFormTests(unittest.TestCase):
    def test_no_push_all_tags(self):
        for line in _command_lines():
            self.assertNotIn("--tags", line, f"pushes every local tag: {line}")

    def test_no_bare_push(self):
        for line in _command_lines():
            args = _push_args(line)
            if args is None:
                continue
            positionals = [a for a in args if not a.startswith("-")]
            self.assertGreaterEqual(
                len(positionals), 2,
                f"bare push — the Foundry guard denies it from the default "
                f"branch: {line}")

    def test_no_push_targets_the_default_branch(self):
        for line in _command_lines():
            args = _push_args(line)
            if args is None:
                continue
            for spec in args[1:]:
                dst = spec.split(":", 1)[-1].lstrip("+").strip('"')
                self.assertNotIn(
                    dst.removeprefix("refs/heads/"), {"main", "master", "HEAD"},
                    f"push to the default branch: {line}")

    def test_no_commit_dash_a(self):
        for line in _command_lines():
            if line.startswith("git commit"):
                self.assertNotRegex(
                    line, r"\s-am?\b",
                    f"commit -a misses brand-new release_notes.txt: {line}")

    def test_staged_diff_is_verified_before_commit(self):
        self.assertTrue(
            any(ln.startswith("git diff --cached") for ln in _command_lines()),
            "the staged diff must be reviewed before the release commit")

    def test_single_tag_push_present(self):
        self.assertIn('git push origin "v<version>"', _command_lines())

    def test_foundry_path_goes_through_start_issue(self):
        # foundry:open-pr refuses a hand-minted branch: it derives the issue id
        # from the <type>/<id>-<slug> shape that only foundry:start-issue mints,
        # and needs a real tracker issue behind it — the skill must route the
        # Foundry case through start-issue and keep `git switch -c` out of the
        # unconditional command blocks (it is the no-Foundry fallback only)
        self.assertIn("foundry:start-issue", SKILL.read_text(encoding="utf-8"))
        for line in _command_lines():
            self.assertFalse(
                line.startswith(("git switch", "git checkout")),
                f"unconditional hand-made branch — Foundry repos must branch "
                f"via foundry:start-issue: {line}")

    def test_branch_is_created_before_any_file_is_written(self):
        # foundry:start-issue refuses a dirty worktree — from the second release
        # on, release_notes.txt are tracked files, so writing them before
        # branching leaves a modified worktree that start-issue rejects; both
        # branch paths must come before the notes are written to disk
        text = SKILL.read_text(encoding="utf-8")
        write_notes = text.index("write each to")
        self.assertLess(text.index("foundry:start-issue"), write_notes)
        self.assertLess(text.index("git switch -c"), write_notes)


if __name__ == "__main__":
    unittest.main()
