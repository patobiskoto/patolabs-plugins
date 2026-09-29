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

    def test_main_preflights_the_same_v1_selection_before_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "foundry_cli.py"
            cli.write_text("# test\n", encoding="utf-8")
            replies = [
                mock.Mock(
                    returncode=0,
                    stdout=json.dumps({
                        "mode": "v1", "tracker": "linear",
                        "project": {"key": "PAT", "id": "project-id"},
                    }),
                    stderr="",
                ),
                mock.Mock(
                    returncode=0,
                    stdout=json.dumps({
                        "contract": "foundry.release-scope.v1",
                        "milestone": "M1", "count": 0, "groups": {},
                        "scope_count": 1,
                        "counts": {"accepted": 0, "deviated": 0,
                                   "unfinished": 0, "unavailable": 1},
                    }),
                    stderr="",
                ),
            ]
            with (
                mock.patch.object(
                    bridge.sys, "argv",
                    ["changelog_bridge.py", "M1", "--foundry-cli", str(cli),
                     "--require-v1-binding"],
                ),
                mock.patch.object(bridge.subprocess, "run", side_effect=replies) as run,
            ):
                self.assertEqual(bridge.main(), 0)

            self.assertEqual(
                run.call_args_list[0].args[0],
                ["python3", str(cli), "registry", "selection", "--require-v1"],
            )
            self.assertEqual(
                run.call_args_list[1].args[0],
                ["python3", str(cli), "query", "changelog", "M1"],
            )

    def test_main_stops_when_v1_selection_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "foundry_cli.py"
            cli.write_text("# test\n", encoding="utf-8")
            with (
                mock.patch.object(
                    bridge.sys, "argv",
                    ["changelog_bridge.py", "M1", "--foundry-cli", str(cli),
                     "--require-v1-binding"],
                ),
                mock.patch.object(
                    bridge.subprocess,
                    "run",
                    return_value=mock.Mock(
                        returncode=2, stdout="", stderr="binding absent",
                    ),
                ) as run,
            ):
                self.assertEqual(bridge.main(), 2)
            self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
