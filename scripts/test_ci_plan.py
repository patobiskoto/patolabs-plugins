"""Unit tests for the CI change-detection rule (PAT-142)."""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("ci_plan.py")
SPEC = importlib.util.spec_from_file_location("ci_plan", MODULE_PATH)
assert SPEC and SPEC.loader
ci_plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci_plan)


class PlanRule(unittest.TestCase):
    def test_push_always_runs_everything_whatever_the_files(self):
        self.assertTrue(ci_plan.plan("push", ["plugins/ship-ios/README.md"]).foundry)
        self.assertTrue(ci_plan.plan("push", None).foundry)

    def test_unknown_event_fails_safe(self):
        for event in ("", "workflow_dispatch", "pull_request_target", "schedule"):
            self.assertTrue(ci_plan.plan(event, ["plugins/ship-ios/README.md"]).foundry, event)

    def test_unavailable_or_empty_diff_fails_safe(self):
        self.assertTrue(ci_plan.plan("pull_request", None).foundry)
        self.assertTrue(ci_plan.plan("pull_request", []).foundry)
        self.assertTrue(ci_plan.plan("pull_request", ["", "  "]).foundry)

    def test_only_ship_ios_files_skip_foundry(self):
        decision = ci_plan.plan(
            "pull_request",
            ["plugins/ship-ios/README.md", "plugins/ship-ios/tests/test_tag_merged.py"],
        )
        self.assertFalse(decision.foundry)
        self.assertIn("skipped", decision.reason)

    def test_one_file_outside_ship_ios_runs_foundry(self):
        for other in (
            "plugins/foundry/README.md",
            "README.md",
            ".github/workflows/ci.yml",
            "scripts/ci_plan.py",
            "plugins/ship-ios-extra/x.py",
            "plugins/ship-iosx",
        ):
            paths = ["plugins/ship-ios/README.md", other]
            self.assertTrue(ci_plan.plan("pull_request", paths).foundry, other)

    def test_path_escaping_the_ship_ios_prefix_runs_foundry(self):
        self.assertTrue(
            ci_plan.plan("pull_request", ["plugins/ship-ios/../foundry/x.py"]).foundry
        )

    def test_release_pr_runs_everything_even_inside_ship_ios(self):
        for manifest in (
            "plugins/ship-ios/.claude-plugin/plugin.json",
            "plugins/ship-ios/.codex-plugin/plugin.json",
        ):
            decision = ci_plan.plan(
                "pull_request", ["plugins/ship-ios/CHANGELOG.md", manifest]
            )
            self.assertTrue(decision.foundry, manifest)
            self.assertIn("release", decision.reason)

    def test_release_rule_recognises_manifests_and_catalogues_only(self):
        for path in (
            "plugins/foundry/.claude-plugin/plugin.json",
            "plugins/foundry/.codex-plugin/plugin.json",
            "plugins/ship-ios/.claude-plugin/plugin.json",
            ".claude-plugin/marketplace.json",
            ".agents/plugins/marketplace.json",
        ):
            self.assertTrue(ci_plan.is_manifest(path), path)
        for path in (
            "plugins/foundry/CHANGELOG.md",
            "plugins/foundry/docs/release-1.1.0.md",
            "plugins/foundry/plugin.json",
            "plugins/foundry/.claude-plugin/other.json",
            "plugins/foundry/tests/fixtures/plugin.json",
            "plugins/ship-ios/notes/.claude-plugin-plugin.json",
        ):
            self.assertFalse(ci_plan.is_manifest(path), path)


class ChangedPathsFromGit(unittest.TestCase):
    def git(self, cwd, *args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
             "-c", "commit.gpgsign=false", *args],
            cwd=cwd, check=True, capture_output=True,
        )

    def test_three_dot_diff_lists_pr_files_and_bad_input_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.git(root, "init", "-q", "-b", "main")
            (root / "a.txt").write_text("a")
            self.git(root, "add", ".")
            self.git(root, "commit", "-q", "-m", "base")
            self.git(root, "checkout", "-q", "-b", "pr")
            (root / "plugins" / "ship-ios").mkdir(parents=True)
            (root / "plugins" / "ship-ios" / "x.md").write_text("x")
            self.git(root, "add", ".")
            self.git(root, "commit", "-q", "-m", "pr")
            self.git(root, "checkout", "-q", "main")
            (root / "b.txt").write_text("b")
            self.git(root, "add", ".")
            self.git(root, "commit", "-q", "-m", "main moved")
            previous = os.getcwd()
            os.chdir(root)
            try:
                self.assertEqual(
                    ci_plan.changed_paths_from_git("main", "pr"),
                    ["plugins/ship-ios/x.md"],
                )
                self.assertIsNone(ci_plan.changed_paths_from_git("nope", "pr"))
                self.assertIsNone(ci_plan.changed_paths_from_git("", "pr"))
            finally:
                os.chdir(previous)

    def changed_between(self, setup):
        """Commit ``setup``'s base state on main, branch ``pr``, apply ``change``, diff."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.git(root, "init", "-q", "-b", "main")
            change = setup(root)
            self.git(root, "add", "-A")
            self.git(root, "commit", "-q", "-m", "base")
            self.git(root, "checkout", "-q", "-b", "pr")
            change(root)
            self.git(root, "add", "-A")
            self.git(root, "commit", "-q", "-m", "pr")
            previous = os.getcwd()
            os.chdir(root)
            try:
                return ci_plan.changed_paths_from_git("main", "pr")
            finally:
                os.chdir(previous)

    def test_move_into_ship_ios_lists_the_source_so_foundry_still_runs(self):
        body = "".join(f"line {i}\n" for i in range(50))  # identical content: rename candidate

        def setup(root):
            (root / "plugins" / "foundry").mkdir(parents=True)
            (root / "plugins" / "ship-ios").mkdir(parents=True)
            (root / "plugins" / "foundry" / "mod.py").write_text(body)
            (root / "plugins" / "ship-ios" / "keep.md").write_text("k")

            def change(root):
                (root / "plugins" / "foundry" / "mod.py").rename(
                    root / "plugins" / "ship-ios" / "mod.py")
            return change

        paths = self.changed_between(setup)
        self.assertEqual(
            sorted(paths), ["plugins/foundry/mod.py", "plugins/ship-ios/mod.py"])
        self.assertTrue(ci_plan.plan("pull_request", paths).foundry)

    def test_deletion_outside_and_edited_move_into_ship_ios_are_listed(self):
        body = "".join(f"line {i}\n" for i in range(50))

        def setup(root):
            (root / "plugins" / "foundry").mkdir(parents=True)
            (root / "plugins" / "ship-ios").mkdir(parents=True)
            (root / "plugins" / "foundry" / "gone.py").write_text(body)
            (root / "plugins" / "foundry" / "other.py").write_text("unrelated\n")

            def change(root):
                # deleted outside, and a similar file (edited move) appears inside
                (root / "plugins" / "foundry" / "gone.py").unlink()
                (root / "plugins" / "ship-ios" / "copy.py").write_text(body + "x\n")
                (root / "plugins" / "foundry" / "other.py").unlink()
            return change

        paths = self.changed_between(setup)
        self.assertEqual(
            sorted(paths),
            ["plugins/foundry/gone.py", "plugins/foundry/other.py", "plugins/ship-ios/copy.py"])
        self.assertTrue(ci_plan.plan("pull_request", paths).foundry)


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
GUARD = "github.event.pull_request.head.repo.fork == false"


def job_text(name: str) -> str:
    tail = WORKFLOW.read_text(encoding="utf-8").split(f"\n  {name}:\n", 1)[1]
    return re.split(r"\n  [A-Za-z][A-Za-z0-9-]*:\n", tail, maxsplit=1)[0]


class WorkflowWiring(unittest.TestCase):
    """The pure rule only matters if the workflow consumes it the way the docs say."""

    def test_required_checks_keep_their_names_and_fork_guard(self):
        for name in ("plan", "foundry", "ship-ios", "catalogue"):
            self.assertIn(GUARD, job_text(name), name)

    def test_only_foundry_is_conditional_on_the_plan(self):
        self.assertIn("needs: plan", job_text("foundry"))
        condition = job_text("foundry").split("if: >-", 1)[1].split("runs-on:", 1)[0]
        # Fail safe: skipped only on an explicit `false`, and a status function so a
        # failed `plan` (empty output) does not skip the job.
        self.assertIn("needs.plan.outputs.run_foundry != 'false'", condition)
        self.assertNotIn("== 'true'", condition)
        self.assertIn("!cancelled()", condition)
        self.assertIn(GUARD, condition)
        for name in ("ship-ios", "catalogue"):
            self.assertNotIn("needs", job_text(name), name)
            self.assertNotIn("run_foundry", job_text(name), name)

    def test_plan_job_has_no_secret_and_no_third_party_action(self):
        text = job_text("plan")
        self.assertNotIn("secrets.", text)
        self.assertEqual(
            [l.split("uses:")[1].split("@")[0].strip() for l in text.splitlines() if "uses:" in l],
            ["actions/checkout"],
        )


if __name__ == "__main__":
    unittest.main()
