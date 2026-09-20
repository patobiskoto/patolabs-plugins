"""Deterministic contract tests for the Fastfile submit lane and release skill."""
import json
import subprocess
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FASTFILE = ROOT / "templates/Fastfile"
SKILL = ROOT / "skills/release/SKILL.md"


def run_submit(version):
    """Execute submit against a minimal Fastlane DSL; no Apple calls are possible."""
    program = textwrap.dedent(
        r'''
        require "json"
        $lanes = {}
        $uploads = []
        class SubmitError < StandardError; end
        module UI
          def self.user_error!(message) = raise SubmitError, message
          def self.message(*) = nil
        end
        def default_platform(*) = nil
        def platform(*) = yield
        def desc(*) = nil
        def lane(name, &block) = $lanes[name] = block
        def ensure_git_status_clean = nil
        def app_store_connect_api_key(*) = :api_key
        def upload_to_app_store(**arguments) = $uploads << arguments
        load ARGV.fetch(0)
        Object.send(:define_method, :verify_locales) { nil }
        begin
          $lanes.fetch(:submit).call(version: ARGV.fetch(1), build: "42")
          puts JSON.generate(status: "ok", uploads: $uploads)
        rescue SubmitError => error
          puts JSON.generate(status: "error", message: error.message, uploads: $uploads)
        end
        '''
    )
    completed = subprocess.run(
        ["ruby", "-e", program, str(FASTFILE), version],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


class SubmitLaneTests(unittest.TestCase):
    def test_missing_version_is_rejected_before_any_app_store_call(self):
        result = run_submit("")
        self.assertEqual(result["status"], "error")
        self.assertIn("non-empty version", result["message"])
        self.assertEqual(result["uploads"], [])

    def test_blank_version_is_rejected_before_any_app_store_call(self):
        result = run_submit(" \t ")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["uploads"], [])

    def test_normalized_version_is_handed_to_deliver_as_app_version(self):
        result = run_submit(" 1.2.3 ")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["uploads"]), 1)
        self.assertEqual(result["uploads"][0]["app_version"], "1.2.3")
        self.assertEqual(result["uploads"][0]["build_number"], "42")


class ReleaseSkillSubmitContractTests(unittest.TestCase):
    def setUp(self):
        self.skill = SKILL.read_text(encoding="utf-8")

    def test_submit_invocation_passes_version_and_build(self):
        self.assertIn(
            'bundle exec fastlane submit version:"<version>" build:"<build_number_from_testflight>"',
            self.skill,
        )

    def test_attach_failure_has_one_shot_ui_fallback_without_retry_loop(self):
        fallback = self.skill[self.skill.index("Known failure — build attach refused"):]
        self.assertIn("ASC UI", fallback)
        self.assertIn("Do not retry the lane in a loop", fallback)
        self.assertIn("Submit for Review", fallback)

    def test_submit_stays_explicitly_human_gated_at_step_eight(self):
        step = self.skill[self.skill.index("## 8. Submit"):self.skill.index("## 9. Hand back")]
        self.assertIn("human-gated", step)
        self.assertIn('explicit "go"', step)


class ReleaseVersionTests(unittest.TestCase):
    def test_manifests_and_changelog_are_aligned_at_0_2_1(self):
        for manifest in (
            ROOT / ".claude-plugin/plugin.json",
            ROOT / ".codex-plugin/plugin.json",
        ):
            self.assertEqual(json.loads(manifest.read_text())["version"], "0.2.1")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## 0.2.1", changelog)
        self.assertIn("## 0.2.0", changelog)


if __name__ == "__main__":
    unittest.main()
