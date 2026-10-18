"""Offline tests: fake REA, lipo, otool, npm, Gradle and downloads. No network, no model, no analysis."""
from __future__ import annotations

import hashlib
import io
import json
import os
import plistlib
import stat
import struct
import sys
import tarfile
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "tooling"))

import blueprint_cli
from blueprint import inventory, paths, pins, registry, setup

ARM64_HEADER = struct.pack("<II", 0xFEEDFACF, 0x0100000C) + b"\0" * 24
FAT_HEADER = struct.pack(">II", 0xCAFEBABE, 2) + b"\0" * 24


def cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "argv", ["blueprint_cli.py", *argv]), redirect_stdout(out), redirect_stderr(err):
        try:
            rc = blueprint_cli.main()
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.data = self.tmp / "data"
        patch = mock.patch.dict(os.environ, {paths.ENV_DATA_DIR: str(self.data)})
        patch.start()
        self.addCleanup(patch.stop)


class PinsTest(unittest.TestCase):
    def test_lockfile_is_fully_pinned_and_in_sync_with_pins(self):
        lock = json.loads((ROOT / "tooling/rea/package-lock.json").read_text())
        pkg = json.loads((ROOT / "tooling/rea/package.json").read_text())
        self.assertEqual(lock["packages"][""]["dependencies"], pkg["dependencies"])
        self.assertEqual(pkg["dependencies"], {"rea-agents": pins.REA_VERSION})
        for name, p in lock["packages"].items():
            if name:
                self.assertTrue(p["resolved"].startswith("https://registry.npmjs.org/"), name)
                self.assertTrue(p["integrity"].startswith("sha512-"), name)
        rea = lock["packages"]["node_modules/rea-agents"]
        self.assertEqual(rea["version"], pins.REA_VERSION)
        self.assertEqual(rea["resolved"], pins.REA_TARBALL_URL)
        self.assertEqual(rea["integrity"], pins.REA_INTEGRITY)

    def test_nothing_is_latest(self):
        for f in (ROOT / "tooling/rea/package.json", ROOT / "tooling/rea/package-lock.json",
                  ROOT / "tooling/blueprint/pins.py", ROOT / "tooling/blueprint/setup.py"):
            self.assertNotIn("@latest", f.read_text().lower(), f.name)

    def test_artefacts_are_https_with_sha256(self):
        for url, digest in ((pins.GHIDRA_URL, pins.GHIDRA_SHA256), (pins.JDK_URL, pins.JDK_SHA256)):
            self.assertTrue(url.startswith("https://"))
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertRegex(pins.GRADLE_DIST_SHA256, r"^[0-9a-f]{64}$")


class PlanTest(Base):
    def test_plan_lists_every_download_and_does_nothing(self):
        text = setup.render_plan(self.data)
        for needle in (pins.REA_TARBALL_URL, pins.REA_INTEGRITY, pins.GHIDRA_URL, pins.GHIDRA_SHA256,
                       pins.JDK_URL, pins.JDK_SHA256, pins.GRADLE_DIST_SHA256, "buildNatives", "npm ci"):
            self.assertIn(needle, text)
        self.assertFalse(self.data.exists())

    def test_setup_without_yes_performs_nothing(self):
        with mock.patch.object(setup, "perform") as perform:
            rc, out, _ = cli("setup")
            self.assertEqual(rc, 0)
            self.assertIn("Nothing was done", out)
            rc, _, _ = cli("setup", "--plan")
            self.assertEqual(rc, 0)
        perform.assert_not_called()
        self.assertFalse(self.data.exists())


class FetchTest(Base):
    def _serve(self, payload: bytes):
        return mock.patch.object(setup.urllib.request, "urlopen", return_value=io.BytesIO(payload))

    def test_checksum_mismatch_deletes_the_file(self):
        dest = self.data / "a.bin"
        with self._serve(b"payload"), self.assertRaises(setup.SetupError) as cm:
            setup.fetch("https://example.invalid/a.bin", dest, "0" * 64)
        self.assertIn("checksum mismatch", str(cm.exception))
        self.assertEqual(list(dest.parent.iterdir()), [])

    def test_matching_checksum_keeps_the_file(self):
        dest = self.data / "a.bin"
        with self._serve(b"payload"):
            setup.fetch("https://example.invalid/a.bin", dest, hashlib.sha256(b"payload").hexdigest())
        self.assertEqual(dest.read_bytes(), b"payload")

    def test_refuses_http(self):
        with self.assertRaises(setup.SetupError):
            setup.fetch("http://example.invalid/a", self.data / "a", "0" * 64)


class ExtractTest(Base):
    def test_zip_member_outside_is_refused_and_exec_bit_kept(self):
        bad = self.tmp / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("../escape", "x")
        with self.assertRaises(setup.SetupError):
            setup.extract_zip(bad, self.tmp / "out")
        self.assertFalse((self.tmp / "escape").exists())
        good = self.tmp / "good.zip"
        with zipfile.ZipFile(good, "w") as zf:
            info = zipfile.ZipInfo("top/run")
            info.external_attr = 0o755 << 16
            zf.writestr(info, "#!/bin/sh\n")
        setup.extract_zip(good, self.tmp / "out2")
        self.assertTrue(os.stat(self.tmp / "out2/top/run").st_mode & stat.S_IXUSR)

    def test_tar_escape_is_refused(self):
        bad = self.tmp / "bad.tar.gz"
        with tarfile.open(bad, "w:gz") as tf:
            data = b"x"
            ti = tarfile.TarInfo("../escape")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        with self.assertRaises(tarfile.FilterError):
            setup.extract_tar(bad, self.tmp / "out")
        self.assertFalse((self.tmp / "escape").exists())


class PerformTest(Base):
    def _fetcher(self):
        calls = []

        def fetcher(url, dest, checksum):
            calls.append((url, checksum))
            dest.parent.mkdir(parents=True, exist_ok=True)
            if url == pins.JDK_URL:
                with tarfile.open(dest, "w:gz") as tf:
                    data = b"java"
                    ti = tarfile.TarInfo(f"{pins.JDK_DIRNAME}/Contents/Home/bin/java")
                    ti.size, ti.mode = len(data), 0o755
                    tf.addfile(ti, io.BytesIO(data))
            else:
                with zipfile.ZipFile(dest, "w") as zf:
                    zf.writestr(f"{pins.GHIDRA_DIRNAME}/support/analyzeHeadless", "x")
                    zf.writestr(f"{pins.GHIDRA_DIRNAME}/{pins.GRADLE_PROPERTIES}", "distributionUrl=u\n")
                    info = zipfile.ZipInfo(f"{pins.GHIDRA_DIRNAME}/{pins.GRADLEW}")
                    info.external_attr = 0o644 << 16
                    zf.writestr(info, "#!/bin/sh\n")
        return fetcher, calls

    def _runner(self, log):
        def runner(cmd, timeout, cwd=None, env=None):
            log.append((cmd, cwd, env))
            if cmd[0] == "npm":
                rea = paths.rea_bin(self.data)
                rea.parent.mkdir(parents=True)
                rea.write_text("#!/bin/sh\n")
            else:
                # Like the real build: outputs under build/os/<platform>, not os/<platform>.
                gh = paths.ghidra_dir(self.data, pins.GHIDRA_DIRNAME)
                dec = gh / "Ghidra/Features/Decompiler/build/os" / pins.NATIVE_PLATFORM / "decompile"
                dec.parent.mkdir(parents=True)
                dec.write_text("bin")
            return 0, "", ""
        return runner

    def test_full_run_then_idempotent(self):
        fetcher, calls = self._fetcher()
        log: list = []
        setup.perform(self.data, log=lambda _m: None, fetcher=fetcher, runner=self._runner(log), check_platform=False)
        self.assertEqual(setup.tools_ready(self.data), [])
        self.assertEqual(calls, [(pins.JDK_URL, pins.JDK_SHA256), (pins.GHIDRA_URL, pins.GHIDRA_SHA256)])
        npm_cmd, npm_cwd, _ = log[0]
        self.assertEqual(npm_cmd[:3], ["npm", "ci", "--ignore-scripts"])
        self.assertEqual(npm_cwd, paths.rea_dir(self.data))
        gr_cmd, gr_cwd, env = log[1]
        self.assertEqual(gr_cmd[1:], ["--no-daemon", "buildNatives"])
        self.assertEqual(gr_cwd, paths.ghidra_dir(self.data, pins.GHIDRA_DIRNAME) / pins.GRADLE_DIR)
        self.assertEqual(env["JAVA_HOME"], str(setup.java_home(self.data)))
        self.assertEqual(env["GRADLE_USER_HOME"], str(paths.gradle_home(self.data)))
        props = (paths.ghidra_dir(self.data, pins.GHIDRA_DIRNAME) / pins.GRADLE_PROPERTIES).read_text()
        self.assertIn(f"distributionSha256Sum={pins.GRADLE_DIST_SHA256}", props)
        self.assertEqual((paths.rea_dir(self.data) / "package.json").read_text(),
                         (ROOT / "tooling/rea/package.json").read_text())
        # Second run: everything exists, nothing is fetched or run.
        fetcher2, calls2 = self._fetcher()
        log2: list = []
        setup.perform(self.data, log=lambda _m: None, fetcher=fetcher2, runner=self._runner(log2), check_platform=False)
        self.assertEqual((calls2, log2), ([], []))

    def test_failed_build_is_reported(self):
        fetcher, _ = self._fetcher()

        def runner(cmd, timeout, cwd=None, env=None):
            if cmd[0] == "npm":
                paths.rea_bin(self.data).parent.mkdir(parents=True)
                paths.rea_bin(self.data).write_text("x")
                return 0, "", ""
            return 1, "", "boom"
        with self.assertRaises(setup.SetupError):
            setup.perform(self.data, log=lambda _m: None, fetcher=fetcher, runner=runner, check_platform=False)

    def test_pin_gradle_is_idempotent(self):
        p = self.tmp / "g.properties"
        p.write_text("a=b\n")
        setup.pin_gradle(p)
        setup.pin_gradle(p)
        self.assertEqual(p.read_text().count("distributionSha256Sum"), 1)


class RegistryTest(Base):
    def test_add_list_find_and_duplicate(self):
        target = self.tmp / "App.app"
        target.mkdir()
        reg = paths.registry_file(self.data)
        self.assertIsNone(registry.find(reg, str(target)))
        entry = registry.add(reg, str(target), "own product", "2026-10-18")
        self.assertEqual(entry["date"], "2026-10-18")
        self.assertEqual(registry.find(reg, str(target))["reason"], "own product")
        self.assertEqual(stat.S_IMODE(os.stat(reg).st_mode), 0o600)
        with self.assertRaises(registry.RegistryError):
            registry.add(reg, str(target), "again")

    def test_refuses_empty_reason_bad_date_missing_target(self):
        reg = paths.registry_file(self.data)
        t = self.tmp / "T"
        t.mkdir()
        for args in ((str(t), " "), (str(t), "r", "18/10/2026"), (str(self.tmp / "none"), "r")):
            with self.assertRaises(registry.RegistryError):
                registry.add(reg, *args)
        self.assertFalse(reg.exists())

    def test_malformed_registry_authorises_nothing(self):
        reg = paths.registry_file(self.data)
        reg.parent.mkdir(parents=True)
        reg.write_text("{not json")
        with self.assertRaises(registry.RegistryError):
            registry.find(reg, "x")

    def test_target_inside_an_authorised_directory_is_not_authorised(self):
        t = self.tmp / "App.app"
        (t / "sub").mkdir(parents=True)
        reg = paths.registry_file(self.data)
        registry.add(reg, str(t), "r")
        self.assertIsNone(registry.find(reg, str(t / "sub")))

    def test_cli_add_and_list(self):
        t = self.tmp / "T"
        t.mkdir()
        self.assertEqual(cli("targets", "add", str(t), "--reason", "mine", "--date", "2026-10-18")[0], 0)
        rc, out, _ = cli("targets", "list")
        self.assertEqual(rc, 0)
        self.assertIn("2026-10-18", out)
        self.assertIn("mine", out)


class RefusalTest(Base):
    def test_unregistered_target_is_refused_before_anything_runs(self):
        t = self.tmp / "App.app"
        t.mkdir()
        with mock.patch.object(inventory, "build") as build, mock.patch.object(inventory.proc, "run") as run:
            rc, _, err = cli("inventory", str(t))
        self.assertEqual(rc, blueprint_cli.EXIT_REFUSED)
        self.assertIn("not in the registry", err)
        build.assert_not_called()
        run.assert_not_called()
        self.assertFalse(paths.inventories(self.data).exists())

    def test_authorised_target_without_tooling_stops(self):
        t = self.tmp / "App.app"
        t.mkdir()
        registry.add(paths.registry_file(self.data), str(t), "r")
        rc, _, err = cli("inventory", str(t))
        self.assertEqual(rc, blueprint_cli.EXIT_NO_TOOLS)
        self.assertIn("setup", err)


def rea_answer(op: str, *, evidence: str, sha: str) -> dict:
    base = {"evidence_id": evidence, "subject": {"name": "x", "digest": {"sha256": sha}},
            "provider": {"id": "ghidra", "version": pins.GHIDRA_VERSION}}
    if op == "analyze":
        return base | {"normalized_result": {"procedure_count": 3, "string_count": 2, "segment_count": 5}}
    if op == "inspect-native-dispatch-metadata":
        loc = lambda a: {"address": a, "file_offset": int(a, 16) + 100}
        return base | {"normalized_result": {"result": {
            "objc_classes": [{"name": "ClassB", "super_class": "Base", "location": loc("0x20")},
                             {"name": "ClassA", "super_class": None, "location": loc("0x10")}],
            "objc_protocols": [{"name": "ProtoA"}],
            "swift_conformances": [{"type_name": "ClassA", "protocol_name": "ProtoA", "module": None,
                                    "location": loc("0x30")}],
            "coverage": [{"facet": "objc", "status": "complete"}, {"facet": "swift", "status": "partial"}]}}}
    if op == "search":
        return base | {"normalized_result": [{"address": "0x40", "value": "Hello"}, {"address": "0x50", "value": "Bye"}]}
    if op == "inspect-asset-catalog":
        return base | {"normalized_result": {"catalogs": [{"path": "Assets.car", "sha256": "ab" * 32, "record_count": 5}]}}
    if op == "decode-interface-builder":
        return base | {"normalized_result": {"documents": []}}
    raise AssertionError(op)


class InventoryTest(Base):
    def _app(self) -> Path:
        app = self.tmp / "Demo.app"
        app.mkdir()
        (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Demo"}))
        (app / "Demo").write_bytes(FAT_HEADER)
        (app / "Demo.debug.dylib").write_bytes(FAT_HEADER + b"debug")
        (app / "Assets.car").write_bytes(b"car")
        return app

    def _runner(self, calls, *, fail_ops=()):
        def runner(cmd, timeout, cwd=None, env=None):
            calls.append(cmd)
            exe = Path(cmd[0]).name
            self.assertIsNotNone(timeout)
            if exe == "lipo":
                Path(cmd[cmd.index("-output") + 1]).write_bytes(ARM64_HEADER + b"slice")
                return 0, "", ""
            if exe == "otool":
                return 0, "file:\n\t/usr/lib/libz.1.dylib (compat 1.0)\n\t/usr/lib/libSystem.B.dylib (compat 1.0)\n", ""
            op = cmd[1]
            if op in fail_ops:
                return 1, json.dumps({"error": "x", "code": "provider_unavailable"}), ""
            sha = hashlib.sha256(Path(cmd[2]).read_bytes()).hexdigest() if Path(cmd[2]).is_file() else "?"
            return 0, json.dumps(rea_answer(op, evidence="ev_" + op, sha=sha)), ""
        return runner

    def test_build_thins_the_debug_dylib_and_records_evidence(self):
        app, calls = self._app(), []
        entry = {"date": "2026-10-18", "reason": "own product", "target": str(app)}
        doc = inventory.build(app, entry, self.data, self._runner(calls))
        self.assertEqual(doc["target"]["analysed_binary"], "Demo.debug.dylib")
        self.assertEqual(doc["target"]["arm64_slice"], "thinned from universal")
        self.assertEqual(doc["authorisation"], {"date": "2026-10-18", "reason": "own product"})
        lipo = next(c for c in calls if c[0] == "lipo")
        self.assertEqual(lipo[2:4], ["-thin", "arm64"])
        rea_calls = [c for c in calls if Path(c[0]).name == "rea"]
        for c in rea_calls:
            self.assertEqual(c[-2:], ["--format", "json"])
        for c in rea_calls:  # deep analysis only ever sees the thin slice
            if c[1] in ("analyze", "search", "inspect-native-dispatch-metadata"):
                self.assertTrue(c[2].endswith(".arm64"))
            if c[1] in ("inspect-asset-catalog", "decode-interface-builder"):  # resources: the bundle itself
                self.assertEqual(c[2], str(app))
        self.assertEqual(doc["analysis"]["procedure_count"], 3)
        types = doc["types"]["items"]
        self.assertEqual([t["name"] for t in types if t["kind"] == "objc-class"], ["ClassA", "ClassB"])
        cls = next(t for t in types if t["name"] == "ClassA" and t["kind"] == "objc-class")
        self.assertEqual(cls["evidence"]["address"], "0x10")
        self.assertEqual(cls["evidence"]["rea_evidence_id"], "ev_inspect-native-dispatch-metadata")
        self.assertEqual(next(t for t in types if t["kind"] == "swift-conformance")["module"], "unknown")
        self.assertEqual(doc["types"]["incomplete_facets"], ["swift"])
        self.assertEqual([s["value"] for s in doc["ui_strings"]["items"]], ["Hello", "Bye"])
        self.assertEqual([m["name"] for m in doc["modules"]["items"]], ["/usr/lib/libSystem.B.dylib", "/usr/lib/libz.1.dylib"])
        res = doc["resources"]["items"]
        self.assertIn("Assets.car", [r["path"] for r in res if r["kind"] == "file"])
        self.assertEqual(next(r for r in res if r["kind"] == "asset_catalogs")["record_count"], 5)

    def test_unreadable_is_unknown_not_empty(self):
        doc = inventory.build(self._app(), {"date": "d", "reason": "r", "target": "t"}, self.data,
                              self._runner([], fail_ops=("inspect-native-dispatch-metadata", "search")))
        for key in ("types", "ui_strings"):
            self.assertEqual(doc[key]["status"], "unknown")
            self.assertIn("provider_unavailable", doc[key]["reason"])
            self.assertEqual(doc[key]["items"], [])
        self.assertEqual(doc["analysis"]["status"], "ok")

    def test_reproducible_and_no_local_path(self):
        app = self._app()
        entry = {"date": "d", "reason": "r", "target": str(app)}
        a = inventory.render(inventory.build(app, entry, self.data, self._runner([])))
        b = inventory.render(inventory.build(app, entry, self.data, self._runner([])))
        self.assertEqual(a, b)
        self.assertNotIn(str(self.tmp), a)
        self.assertNotIn(str(Path.home()), a)

    def test_thin_arm64_is_used_as_is_and_other_formats_refused(self):
        f = self.tmp / "thin"
        f.write_bytes(ARM64_HEADER)
        self.assertEqual(inventory.thin(f, self.tmp / "w", self._runner([]))[0], f)
        f.write_bytes(b"not a macho at all")
        with self.assertRaises(inventory.InventoryError):
            inventory.thin(f, self.tmp / "w", self._runner([]))

    def test_lipo_failure_is_an_error(self):
        f = self.tmp / "fat"
        f.write_bytes(FAT_HEADER)
        with self.assertRaises(inventory.InventoryError):
            inventory.thin(f, self.tmp / "w", lambda *a, **k: (1, "", "no arm64"))

    def test_rea_timeout_is_unknown(self):
        rea = inventory.Rea(self.data, lambda *a, **k: (None, "", "timed out after 900s"))
        doc, why = rea.call(["analyze", "x"])
        self.assertIsNone(doc)
        self.assertIn("timed out", why)

    def test_cli_inventory_writes_file_for_authorised_target(self):
        app = self._app()
        registry.add(paths.registry_file(self.data), str(app), "own product", "2026-10-18")
        out = self.tmp / "inv.json"
        with mock.patch.object(setup, "tools_ready", return_value=[]), \
                mock.patch.object(inventory.proc, "run", self._runner([])):
            rc, stdout, _ = cli("inventory", str(app), "--output", str(out))
        self.assertEqual(rc, 0, stdout)
        doc = json.loads(out.read_text())
        self.assertEqual(doc["authorisation"]["reason"], "own product")
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
