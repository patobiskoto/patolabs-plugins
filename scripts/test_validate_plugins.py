"""Stdlib fixtures for the dual-runtime semantic-parity validator."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("validate_plugins.py")
SPEC = importlib.util.spec_from_file_location("validate_plugins", MODULE_PATH)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class SemanticParityFixtures(unittest.TestCase):
    def make_plugin(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "example"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".codex-plugin").mkdir()
        (root / "skills" / "alpha").mkdir(parents=True)
        (root / "skills" / "beta").mkdir()
        (root / "skills" / "alpha" / "SKILL.md").write_text("alpha", encoding="utf-8")
        (root / "skills" / "beta" / "SKILL.md").write_text("beta", encoding="utf-8")
        (root / "hooks").mkdir()
        (root / "hooks" / "run.py").write_text("# fixture", encoding="utf-8")
        (root / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {
            "SessionStart": [{"matcher": "startup", "hooks": [{
                "type": "command",
                "command": 'python3 "${CLAUDE_PLUGIN_ROOT}"/hooks/run.py',
            }]}],
        }}), encoding="utf-8")
        self.write_claude_manifest(root)
        self.write_codex_manifest(root)
        return root

    @staticmethod
    def write_claude_manifest(root: Path, *, hooks: str | None = None) -> None:
        manifest: dict[str, object] = {}
        if hooks is not None:
            manifest["hooks"] = hooks
        (root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    @staticmethod
    def write_codex_manifest(root: Path, *, skills: str = "./skills/",
                             capabilities: list[str] | None = None) -> None:
        (root / ".codex-plugin" / "plugin.json").write_text(json.dumps({
            "skills": skills,
            "interface": {"capabilities": capabilities or ["Read", "Write"]},
        }), encoding="utf-8")

    def test_claude_auto_loads_conventional_hooks_without_manifest_field(self) -> None:
        root = self.make_plugin()
        validator.validate_plugin_semantics(root)
        manifest = json.loads(
            (root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("hooks", manifest)
        inventory = validator.normalise_facade(root, "claude")
        self.assertEqual(inventory["hooks"][0][3], "python3 <plugin-root>/hooks/run.py")

    def test_claude_rejects_manifest_declaration_of_auto_loaded_hooks(self) -> None:
        root = self.make_plugin()
        for declared_hooks in ("./hooks/hooks.json", "./hooks/../hooks/hooks.json"):
            with self.subTest(declared_hooks=declared_hooks):
                self.write_claude_manifest(root, hooks=declared_hooks)
                with self.assertRaisesRegex(
                    AssertionError,
                    r"example: claude hooks path: duplicate conventional hook file .* "
                    r"is loaded automatically; remove manifest\.hooks",
                ):
                    validator.validate_plugin_semantics(root)

    def test_missing_skill_on_codex_facade_is_actionable(self) -> None:
        root = self.make_plugin()
        (root / "codex-skills" / "alpha").mkdir(parents=True)
        (root / "codex-skills" / "alpha" / "SKILL.md").write_text("alpha", encoding="utf-8")
        self.write_codex_manifest(root, skills="./codex-skills/")
        with self.assertRaisesRegex(AssertionError, r"example: claude/codex skills: .*beta"):
            validator.validate_plugin_semantics(root)

    def test_missing_hook_script_is_actionable(self) -> None:
        root = self.make_plugin()
        (root / "hooks" / "run.py").unlink()
        with self.assertRaisesRegex(AssertionError, r"example: claude hooks path: missing hooks/run.py"):
            validator.validate_plugin_semantics(root)

    def test_external_hook_command_is_rejected(self) -> None:
        root = self.make_plugin()
        hooks_path = root / "hooks" / "hooks.json"
        document = json.loads(hooks_path.read_text(encoding="utf-8"))
        document["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "python3 /tmp/run.py"
        hooks_path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(
            AssertionError,
            r"example: claude hooks path: command must reference <plugin-root>/\.\.\.",
        ):
            validator.validate_plugin_semantics(root)

    def test_additional_claude_hook_creates_actionable_divergence(self) -> None:
        root = self.make_plugin()
        (root / "hooks" / "claude-hooks.json").write_text(json.dumps({"hooks": {
            "SessionEnd": [{"hooks": [{
                "type": "command",
                "command": 'python3 "${CLAUDE_PLUGIN_ROOT}"/hooks/run.py',
            }]}],
        }}), encoding="utf-8")
        self.write_claude_manifest(root, hooks="./hooks/claude-hooks.json")
        claude_events = [
            action[0] for action in validator.normalise_facade(root, "claude")["hooks"]
        ]
        self.assertEqual(claude_events, ["SessionEnd", "SessionStart"])
        with self.assertRaisesRegex(AssertionError, r"example: claude/codex hooks: .*SessionEnd"):
            validator.validate_plugin_semantics(root)

    def test_divergent_capability_is_actionable(self) -> None:
        root = self.make_plugin()
        self.write_codex_manifest(root, capabilities=["Read"])
        with self.assertRaisesRegex(AssertionError, r"example: claude/codex capabilities: .*Write"):
            validator.validate_plugin_semantics(root)

    def test_missing_declared_skills_path_is_actionable(self) -> None:
        root = self.make_plugin()
        self.write_codex_manifest(root, skills="./not-there/")
        with self.assertRaisesRegex(AssertionError, r"example: codex skills path: missing"):
            validator.validate_plugin_semantics(root)

    def test_path_escape_is_rejected(self) -> None:
        root = self.make_plugin()
        self.write_codex_manifest(root, skills="../outside")
        with self.assertRaisesRegex(
            AssertionError, r"example: codex skills path: path escapes plugin root: ../outside"
        ):
            validator.validate_plugin_semantics(root)

    def test_invalid_json_is_actionable(self) -> None:
        root = self.make_plugin()
        (root / ".codex-plugin" / "plugin.json").write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(
            AssertionError, r"example: codex manifest: invalid JSON at"
        ):
            validator.validate_plugin_semantics(root)

    def test_duplicate_capability_is_actionable(self) -> None:
        root = self.make_plugin()
        self.write_codex_manifest(root, capabilities=["Read", "Write", "Read"])
        with self.assertRaisesRegex(
            AssertionError, r"example: codex capabilities: duplicate entries: \['Read'\]"
        ):
            validator.validate_plugin_semantics(root)


class FoundryAgentContractFixtures(unittest.TestCase):
    def make_foundry_agents(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "foundry"
        agents = root / "agents"
        agents.mkdir(parents=True)
        for identity, phrases in validator.FOUNDRY_AGENT_CONTRACTS.items():
            contract = "\n".join(
                validator.FOUNDRY_AGENT_COMMON_CONTRACT + phrases + (f"`foundry:{identity}`",)
            )
            (agents / f"{identity}.md").write_text(
                f"---\nname: {identity}\ndescription: fixture\ntools: Read\n---\n{contract}\n",
                encoding="utf-8",
            )
        return root

    def test_expected_identity_agents_with_contracts_are_accepted(self) -> None:
        validator.validate_foundry_agent_contracts(self.make_foundry_agents())

    def test_missing_identity_agent_is_actionable(self) -> None:
        root = self.make_foundry_agents()
        (root / "agents" / "maigret.md").unlink()
        with self.assertRaisesRegex(
            AssertionError, r"foundry: agents maigret: missing identity file maigret\.md"
        ):
            validator.validate_foundry_agent_contracts(root)

    def test_misnamed_identity_frontmatter_is_actionable(self) -> None:
        root = self.make_foundry_agents()
        path = root / "agents" / "eiffel.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace("name: eiffel", "name: worker", 1),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            AssertionError, r"foundry: agents eiffel: frontmatter name must match identity filename"
        ):
            validator.validate_foundry_agent_contracts(root)

    def test_materially_divergent_contract_is_actionable(self) -> None:
        root = self.make_foundry_agents()
        path = root / "agents" / "lupin.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace("strictly read-only and must not delegate", "may edit"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            AssertionError, r"foundry: agents lupin: missing required contract: 'strictly read-only and must not delegate'"
        ):
            validator.validate_foundry_agent_contracts(root)

class PublicSurfaceContract(unittest.TestCase):
    def text(self, relative_path: str) -> str:
        return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    def test_apache_license_and_third_party_notice_are_explicit(self) -> None:
        license_text = self.text("LICENSE")
        notice = self.text("NOTICE")

        self.assertIn("SPDX-License-Identifier: Apache-2.0", license_text)
        self.assertIn("Apache License", license_text)
        self.assertIn("Notwithstanding the above, nothing herein shall supersede", license_text)
        self.assertIn("APPENDIX: How to apply the Apache License to your work.", license_text)
        self.assertIn("does not bundle or redistribute third-party", notice)
        self.assertIn("future vendored or redistributed third-party material", notice)

    def test_private_security_reporting_never_promises_automatic_disclosure(self) -> None:
        security = " ".join(self.text("SECURITY.md").split())

        self.assertIn("private vulnerability reporting form", security)
        self.assertIn("does not trigger an automatic public disclosure", security)
        self.assertIn("GitHub Issues are disabled", security)

    def test_readme_covers_both_hosts_without_promising_unshipped_integrations(self) -> None:
        readme = self.text("README.md")

        for command in (
            "/plugin marketplace add patobiskoto/patolabs-plugins",
            "/plugin install foundry@patolabs",
            "/plugin install ship-ios@patolabs",
            "codex plugin marketplace add patobiskoto/patolabs-plugins",
            "codex plugin add foundry@patolabs",
            "codex plugin add ship-ios@patolabs",
            "/foundry:frame",
            "$foundry:frame",
        ):
            self.assertIn(command, readme)
        self.assertIn("does not yet ship a Linear adapter or a ChatGPT MCP integration", readme)

    def test_external_contributions_are_explicitly_declined(self) -> None:
        self.assertIn("not accepting external contributions", self.text("README.md"))
        self.assertIn("not for\nexternal contribution", self.text("CONTRIBUTING.md"))


if __name__ == "__main__":
    unittest.main()
