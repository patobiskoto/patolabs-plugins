import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/changelog_bridge.py"
SPEC = importlib.util.spec_from_file_location("changelog_bridge", SCRIPT)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class ChangelogBridgeDiscoveryTests(unittest.TestCase):
    def test_explicit_cli_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "foundry_cli.py"
            cli.write_text("# test\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(
                    bridge._find_cli(str(cli), home=Path(tmp), plugin_root=Path(tmp)),
                    str(cli),
                )

    def test_install_marker_is_host_neutral(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cli = home / "cache/patolabs/foundry/0.6.0/tooling/foundry_cli.py"
            cli.parent.mkdir(parents=True)
            cli.write_text("# test\n", encoding="utf-8")
            marker = home / ".config/foundry/install.json"
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({"foundry_cli": str(cli)}), encoding="utf-8")
            private_dirs = {
                "FOUNDRY_DATA": str(home / "foundry-private"),
                "PLUGIN_DATA": str(home / "ship-ios-private"),
                "CLAUDE_PLUGIN_DATA": str(home / "claude-ship-ios-private"),
            }
            with mock.patch.dict(os.environ, private_dirs, clear=True):
                self.assertEqual(
                    bridge._marker_path(home),
                    home / ".config/foundry/install.json",
                )
                self.assertEqual(
                    bridge._find_cli(None, home=home, plugin_root=home / "ship-ios"),
                    str(cli),
                )


if __name__ == "__main__":
    unittest.main()
