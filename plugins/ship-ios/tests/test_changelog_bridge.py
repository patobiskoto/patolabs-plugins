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


def _v1_payload(project, *, unavailable=False):
    fact = {
        "id": f"{project}-1", "title": "not proven", "type": "Feature",
        "state": "done", "labels": [], "disposition": "unavailable",
        "references": {},
    }
    categories = {name: [] for name in (
        "accepted", "deviated", "unfinished", "unavailable",
    )}
    if unavailable:
        categories["unavailable"] = [fact]
    return {
        "contract": "foundry.release-scope.v1", "project": project,
        "milestone": "M1", "count": 0, "groups": {},
        "scope_count": int(unavailable),
        "counts": {name: len(rows) for name, rows in categories.items()},
        "categories": categories,
        "release_scope": {
            "provider": "linear", "project_key": project,
            "project_id": "project-id", "release": "M1", "release_id": "release-1",
            "native_state": None, "issues": [fact] if unavailable else [],
            "closure": {
                "mode": "operator", "native_capability": "unavailable",
                "action": "record-scope-freeze", "native_mutation": False,
                "preconditions": ["unfinished=0", "unavailable=0"],
                "verification": "read-release-scope",
            },
            "coordinates": {
                "project_id": "project-id", "team_id": "team-id",
                "project_milestone_id": "release-1",
            },
        },
    }


class ChangelogBridgeDiscoveryTests(unittest.TestCase):
    def test_v1_scope_distinguishes_empty_release_from_unavailable_facts(self):
        selection = {"tracker": "linear", "project": {"key": "APP", "id": "project-id"}}
        empty = bridge._validate_changelog(
            _v1_payload("APP"), milestone="M1", selection=selection,
        )
        unknown = bridge._validate_changelog(
            _v1_payload("APP", unavailable=True), milestone="M1", selection=selection,
        )
        self.assertEqual(empty["scope_count"], 0)
        self.assertEqual(unknown["count"], 0)
        self.assertEqual(unknown["counts"]["unavailable"], 1)

    def test_v1_scope_rejects_partial_or_contradictory_payloads(self):
        selection = {"tracker": "linear", "project": {"key": "APP", "id": "project-id"}}
        for mutation in (
            lambda value: value.pop("categories"),
            lambda value: value.update(contract="foundry.legacy"),
            lambda value: value.update(scope_count=0),
            lambda value: value["release_scope"].update(provider="ghprojects"),
            lambda value: value["release_scope"].pop("coordinates"),
            lambda value: value["release_scope"].pop("native_state"),
            lambda value: value["release_scope"].update(native_state="closed"),
            lambda value: value["release_scope"].update(closure={}),
            lambda value: value["release_scope"]["coordinates"].update(
                project_milestone_id="other-release"
            ),
            lambda value: value["release_scope"]["issues"].clear(),
            lambda value: value["release_scope"]["issues"].append(
                value["release_scope"]["issues"][0]
            ),
        ):
            value = _v1_payload("APP", unavailable=True)
            mutation(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                bridge._validate_changelog(value, milestone="M1", selection=selection)

        selection_bad_digest = {
            "mode": "v1", "tracker": "linear",
            "project": {"key": "APP", "id": "project-id"},
            "repository": "github.com/example/app", "configuration_digest": "x",
        }
        with self.assertRaises(ValueError):
            bridge._validate_selection(selection_bad_digest)

    def test_provider_coordinates_are_bound_to_the_selected_release(self):
        base = {"project": {"key": "APP", "id": "project-id"}}
        self.assertTrue(bridge._valid_coordinates({
            "project_id": "project-id", "milestone_bundle_id": "bundle-id",
            "enum_value_id": "release-1",
        }, {**base, "tracker": "youtrack"}, "release-1"))
        github_selection = {
            **base, "tracker": "ghprojects", "repository": "github.com/example/app",
        }
        github_coordinates = {
            "project_id": "project-id", "repository": "example/app",
            "milestone_number": 7, "milestone_id": 700,
            "milestone_node_id": "node-7",
        }
        self.assertTrue(bridge._valid_coordinates(
            github_coordinates, github_selection, "7",
        ))
        self.assertFalse(bridge._valid_coordinates(
            {**github_coordinates, "repository": "other/app"}, github_selection, "7",
        ))
        self.assertFalse(bridge._valid_coordinates(
            github_coordinates, github_selection, "8",
        ))

    def test_all_three_provider_closure_shapes_validate(self):
        base = {"project": {"key": "APP", "id": "project-id"}}
        youtrack = _v1_payload("APP")
        youtrack["release_scope"].update(
            provider="youtrack",
            coordinates={
                "project_id": "project-id", "milestone_bundle_id": "bundle-id",
                "enum_value_id": "release-1",
            },
        )
        self.assertEqual(bridge._validate_changelog(
            youtrack, milestone="M1", selection={**base, "tracker": "youtrack"},
        ), youtrack)
        github = _v1_payload("APP")
        github["release_scope"].update(
            provider="ghprojects", release_id="7", native_state="open",
            closure={
                "mode": "operator", "action": "close-repository-milestone",
                "native_mutation": True,
                "preconditions": ["unfinished=0", "unavailable=0"],
                "verification": "read-release-scope",
            },
            coordinates={
                "project_id": "project-id", "repository": "example/app",
                "milestone_number": 7, "milestone_id": 700,
                "milestone_node_id": "node-7",
            },
        )
        self.assertEqual(bridge._validate_changelog(
            github, milestone="M1", selection={
                **base, "tracker": "ghprojects",
                "repository": "github.com/example/app",
            },
        ), github)

    def test_explicit_cli_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "foundry_cli.py"
            cli.write_text("# test\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(
                    bridge._find_cli(str(cli), home=Path(tmp), plugin_root=Path(tmp)),
                    str(cli),
                )

    def test_missing_explicit_cli_is_not_treated_as_no_foundry(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(bridge.sys, "argv", [
                "changelog_bridge.py", "M1", "--foundry-cli", str(Path(tmp) / "absent"),
            ]),
            mock.patch.object(bridge, "_repository_root", return_value=Path(tmp)),
            mock.patch.object(bridge, "_cli_candidates", side_effect=ValueError(
                "configured Foundry CLI is unavailable"
            )) as finder,
        ):
            self.assertEqual(bridge.main(), 2)
            finder.assert_called_once_with(str(Path(tmp) / "absent"))

    def test_broken_configured_cli_or_marker_is_not_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with (
                mock.patch.dict(os.environ, {"FOUNDRY_CLI": str(home / "absent")}, clear=True),
                self.assertRaises(ValueError),
            ):
                bridge._find_cli(None, home=home, plugin_root=home)
            marker = bridge._marker_path(home)
            marker.parent.mkdir(parents=True)
            for body in ('{invalid', json.dumps({"foundry_cli": str(home / "absent")})):
                marker.write_text(body, encoding="utf-8")
                with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
                    bridge._find_cli(None, home=home, plugin_root=home)

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

    def test_adjacent_matching_manifests_expose_foundry_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "foundry"
            cli = root / "tooling/foundry_cli.py"
            cli.parent.mkdir(parents=True)
            cli.write_text("# test\n", encoding="utf-8")
            for directory in (".claude-plugin", ".codex-plugin"):
                manifest = root / directory / "plugin.json"
                manifest.parent.mkdir()
                manifest.write_text(json.dumps({"version": "0.9.0"}), encoding="utf-8")
            self.assertEqual(bridge._foundry_version(str(cli)), "0.9.0")

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
                        "repository": "github.com/example/app",
                        "configuration_digest": "sha256:" + "a" * 64,
                    }),
                    stderr="",
                ),
                mock.Mock(
                    returncode=0,
                    stdout=json.dumps(_v1_payload("PAT", unavailable=True)),
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
                mock.patch.object(bridge, "_repository_root", return_value=Path(tmp)),
            ):
                self.assertEqual(bridge.main(), 0)

            self.assertEqual(
                run.call_args_list[0].args[0],
                ["python3", str(cli), "registry", "selection", "--require-v1"],
            )
            self.assertEqual(run.call_args_list[0].kwargs["cwd"], Path(tmp))
            self.assertEqual(
                run.call_args_list[1].args[0],
                ["python3", str(cli), "query", "changelog", "M1"],
            )

    def test_autodiscovery_skips_legacy_cli_but_not_provider_failure(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(bridge.sys, "argv", [
                "changelog_bridge.py", "M1", "--require-v1-binding",
            ]),
            mock.patch.object(bridge, "_repository_root", return_value=Path(tmp)),
            mock.patch.object(bridge, "_cli_candidates", return_value=(
                ["old-cli", "new-cli"], False,
            )),
        ):
            selected = {
                "mode": "v1", "tracker": "linear",
                "project": {"key": "APP", "id": "project-id"},
                "repository": "github.com/example/app",
                "configuration_digest": "sha256:" + "a" * 64,
            }
            replies = [
                mock.Mock(returncode=1, stdout="usage: registry [register <tracker> "
                          "<repo> <KEY> <project-id> [k=v …] | alias <tracker> "
                          "<source-repo> <alias-repo>]\n", stderr=""),
                mock.Mock(returncode=0, stdout=json.dumps(selected), stderr=""),
                mock.Mock(returncode=0, stdout=json.dumps(_v1_payload("APP")), stderr=""),
            ]
            with mock.patch.object(bridge.subprocess, "run", side_effect=replies) as run:
                self.assertEqual(bridge.main(), 0)
            self.assertEqual([call.args[0][1] for call in run.call_args_list], [
                "old-cli", "new-cli", "new-cli",
            ])

            failed = mock.Mock(returncode=2, stdout="", stderr="binding invalid")
            with mock.patch.object(bridge.subprocess, "run", return_value=failed) as run:
                self.assertEqual(bridge.main(), 2)
                run.assert_called_once()

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
                mock.patch.object(bridge, "_repository_root", return_value=Path(tmp)),
            ):
                self.assertEqual(bridge.main(), 2)
            self.assertEqual(run.call_count, 1)

    def test_main_never_queries_from_the_plugin_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "app"
            app.mkdir()
            cli = Path(tmp) / "foundry_cli.py"
            cli.write_text("# test\n", encoding="utf-8")
            replies = [
                mock.Mock(returncode=0, stdout=json.dumps({
                    "mode": "v1", "tracker": "linear",
                    "project": {"key": "APP", "id": "project-id"},
                    "repository": "github.com/example/app",
                    "configuration_digest": "sha256:" + "a" * 64,
                }), stderr=""),
                mock.Mock(returncode=0, stdout=json.dumps(_v1_payload("APP")), stderr=""),
            ]
            with (
                mock.patch.object(bridge.sys, "argv", [
                    "changelog_bridge.py", "M1", "--foundry-cli", str(cli),
                    "--require-v1-binding",
                ]),
                mock.patch.object(bridge, "_repository_root", return_value=app),
                mock.patch.object(bridge.subprocess, "run", side_effect=replies) as run,
            ):
                self.assertEqual(bridge.main(), 0)
            self.assertEqual([call.kwargs["cwd"] for call in run.call_args_list], [app, app])

    def test_malformed_payload_is_not_an_empty_changelog(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "foundry_cli.py"
            cli.write_text("# test\n", encoding="utf-8")
            replies = [
                mock.Mock(returncode=0, stdout=json.dumps({
                    "mode": "v1", "tracker": "linear",
                    "project": {"key": "APP", "id": "project-id"},
                    "repository": "github.com/example/app",
                    "configuration_digest": "sha256:" + "a" * 64,
                }), stderr=""),
                mock.Mock(returncode=0, stdout=json.dumps({
                    "project": "APP", "milestone": "M1", "count": 0,
                    "groups": {"Feature": [{"id": "APP-1", "title": "bad", "labels": []}]},
                }), stderr=""),
            ]
            with (
                mock.patch.object(bridge.sys, "argv", [
                    "changelog_bridge.py", "M1", "--foundry-cli", str(cli),
                ]),
                mock.patch.object(bridge, "_repository_root", return_value=Path(tmp)),
                mock.patch.object(bridge.subprocess, "run", side_effect=replies),
            ):
                self.assertEqual(bridge.main(), 2)

    def test_provider_failure_is_not_a_file_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "foundry_cli.py"
            cli.write_text("# test\n", encoding="utf-8")
            with (
                mock.patch.object(bridge.sys, "argv", [
                    "changelog_bridge.py", "M1", "--foundry-cli", str(cli),
                ]),
                mock.patch.object(bridge, "_repository_root", return_value=Path(tmp)),
                mock.patch.object(bridge.subprocess, "run", return_value=mock.Mock(
                    returncode=1, stdout="", stderr="provider inaccessible",
                )),
            ):
                self.assertEqual(bridge.main(), 2)

    def test_standalone_mode_does_not_discover_or_call_foundry(self):
        with (
            mock.patch.object(bridge.sys, "argv", [
                "changelog_bridge.py", "M1", "--standalone",
            ]),
            mock.patch.object(bridge, "_find_cli") as find_cli,
            mock.patch.object(bridge.subprocess, "run") as run,
        ):
            self.assertEqual(bridge.main(), 0)
        find_cli.assert_not_called()
        run.assert_not_called()

    def test_non_git_repository_is_distinct_from_no_foundry(self):
        with (
            mock.patch.object(bridge.sys, "argv", ["changelog_bridge.py", "M1"]),
            mock.patch.object(bridge, "_repository_root", return_value=None),
            mock.patch.object(bridge, "_find_cli") as find_cli,
        ):
            self.assertEqual(bridge.main(), 4)
        find_cli.assert_not_called()


if __name__ == "__main__":
    unittest.main()
