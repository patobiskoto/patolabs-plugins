"""Tests of the corrections that settle the deferred review remarks of PAT-252, PAT-253 and PAT-260 (PAT-259).

Offline: fakes only, no network, no model, no Ghidra or REA, except `ProcTest`, which runs real local `sh` and `python3`
processes to check the timeout. Each test names the remark it settles."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import plistlib
import re
import signal
import struct
import sys
import tempfile
import time
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tooling"))

import blueprint_cli
from blueprint import (
    decompile,
    ghidra,
    inventory,
    paths,
    pins,
    proc,
    rebuild,
    registry,
    resources,
    setup,
    spec,
)

ARM64 = struct.pack("<II", 0xFEEDFACF, 0x0100000C) + b"\0" * 24
SHA = "ab" * 32
TARGET_BYTES = b"target"
TARGET_SHA = hashlib.sha256(TARGET_BYTES).hexdigest()


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


class ArchiveAndDownloadTest(Base):
    def test_zip_symlink_then_dotdot_member_is_refused(self):  # PAT-252 round 1, remark 4
        bad = self.tmp / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            link = zipfile.ZipInfo("l")
            link.external_attr = (0o120777 << 16)
            zf.writestr(link, ".")
            zf.writestr("l/../escape", "x")
        with self.assertRaises(setup.SetupError):
            setup.extract_zip(bad, self.tmp / "out")
        self.assertFalse((self.tmp / "escape").exists())
        self.assertFalse((self.tmp / "out" / "l").exists(), "nothing is written when a member is refused")

    def test_zip_link_target_and_absolute_names_are_refused(self):  # PAT-252 round 1, remark 4
        for member, link in (("a", "../x"), ("a", "/etc"), ("/abs", None), ("d\\..\\e", None)):
            bad = self.tmp / "bad2.zip"
            with zipfile.ZipFile(bad, "w") as zf:
                info = zipfile.ZipInfo(member)
                if link is not None:
                    info.external_attr = (0o120777 << 16)
                zf.writestr(info, link or "x")
            with self.subTest(member=member, link=link), self.assertRaises(setup.SetupError):
                setup.extract_zip(bad, self.tmp / "out2")

    def test_nothing_is_written_when_a_later_link_target_is_bad(self):  # PAT-259 review 1, remark 3
        bad = self.tmp / "late.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("top/first", "x")
            link = zipfile.ZipInfo("top/alias")
            link.external_attr = (0o120777 << 16)
            zf.writestr(link, "../../outside")
        with self.assertRaises(setup.SetupError):
            setup.extract_zip(bad, self.tmp / "out4")
        self.assertEqual(list((self.tmp / "out4").iterdir()), [])

    def test_zip_inner_symlink_without_dotdot_is_kept(self):  # the safe case stays supported
        good = self.tmp / "good.zip"
        with zipfile.ZipFile(good, "w") as zf:
            zf.writestr("top/real", "x")
            link = zipfile.ZipInfo("top/alias")
            link.external_attr = (0o120777 << 16)
            zf.writestr(link, "real")
        setup.extract_zip(good, self.tmp / "out3")
        self.assertEqual((self.tmp / "out3/top/alias").read_text(), "x")

    def test_a_slow_download_is_refused_as_a_whole(self):  # PAT-252 round 1, remark 8 (urlopen timeout is per operation)
        class Slow(io.BytesIO):
            def read(self, n=-1):
                clock[0] += 100
                return super().read(1)
        clock = [0.0]
        dest = self.data / "a.bin"
        with mock.patch.object(setup.urllib.request, "urlopen", return_value=Slow(b"abcdef")), \
                mock.patch.object(setup.time, "monotonic", side_effect=lambda: clock[0]), \
                self.assertRaises(setup.SetupError) as cm:
            setup.fetch("https://example.invalid/a.bin", dest, "0" * 64, timeout=250)
        self.assertIn("download failed", str(cm.exception))
        self.assertEqual(list(dest.parent.iterdir()), [], "the partial file is deleted")

    def test_pin_gradle_accepts_the_pin_and_refuses_another_checksum(self):  # PAT-252 round 1, remark 9
        p = self.tmp / "g.properties"
        p.write_text(f"a=b\ndistributionSha256Sum={pins.GRADLE_DIST_SHA256}\n")
        setup.pin_gradle(p)
        self.assertEqual(p.read_text().count("distributionSha256Sum"), 1)
        p.write_text("a=b\ndistributionSha256Sum=" + "0" * 64 + "\n")
        with self.assertRaises(setup.SetupError):
            setup.pin_gradle(p)
        p.write_text("a=b\n#distributionSha256Sum=" + "0" * 64 + "\n")
        setup.pin_gradle(p)  # a comment is not a pin: the pinned line is added
        self.assertIn(f"\ndistributionSha256Sum={pins.GRADLE_DIST_SHA256}", p.read_text())


class ProcTest(unittest.TestCase):
    def test_timeout_kills_the_whole_process_group(self):  # PAT-252 round 1, remark 8
        with tempfile.TemporaryDirectory() as t:
            pidfile = Path(t) / "child.pid"
            script = f"sleep 60 & echo $! > '{pidfile}'; wait"
            rc, _, why = proc.run(["sh", "-c", script], 1)
            self.assertIsNone(rc)
            self.assertIn("timed out", why)
            child = int(pidfile.read_text())
            for _ in range(50):  # the grandchild is gone (signal 0 raises once it is reaped)
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                os.kill(child, signal.SIGKILL)
                self.fail("the child of the timed-out process survived")

    def test_a_descendant_in_its_own_session_holding_the_pipes_does_not_block_blueprint(self):  # PAT-259 review 1, remark 1
        with tempfile.TemporaryDirectory() as t:
            pidfile = Path(t) / "grandchild.pid"
            code = ("import subprocess,sys,time;"
                    "p=subprocess.Popen(['sleep','60'],start_new_session=True);"
                    f"open({str(pidfile)!r},'w').write(str(p.pid));time.sleep(60)")
            t0 = time.monotonic()
            try:
                rc, _, why = proc.run([sys.executable, "-c", code], 1)
                elapsed = time.monotonic() - t0
            finally:
                if pidfile.exists():
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(int(pidfile.read_text()), signal.SIGKILL)
            self.assertIsNone(rc)
            self.assertIn("timed out", why)
            self.assertLess(elapsed, 1 + proc.DRAIN_SECONDS + 5)

    def test_normal_run_and_launch_failure(self):
        rc, out, _ = proc.run(["sh", "-c", "echo hi; exit 3"], 5)
        self.assertEqual((rc, out.strip()), (3, "hi"))
        rc, _, why = proc.run(["/nonexistent/binary"], 5)
        self.assertIsNone(rc)
        self.assertIn("cannot be launched", why)


def answer(op: str, normalized, *, subject=None) -> dict:
    return {"evidence_id": "ev_" + op, "subject": subject if subject is not None else {"digest": {"sha256": SHA}},
            "provider": {"id": "p", "version": "1"}, "normalized_result": normalized}


class InventoryRobustnessTest(Base):
    def _bundle(self, link_to: Path | None):
        app = self.tmp / "Demo.app"
        app.mkdir()
        (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Demo"}))
        if link_to is None:
            (app / "Demo").write_bytes(ARM64)
        else:
            (app / "Demo").symlink_to(link_to)
        return app

    def test_executable_symlink_leaving_the_bundle_is_refused(self):  # PAT-252 round 1, remark 6
        outside = self.tmp / "outside"
        outside.write_bytes(ARM64)
        with self.assertRaises(inventory.InventoryError) as cm:
            inventory.locate_binary(self._bundle(outside))
        self.assertIn("outside the bundle", str(cm.exception))
        (self.tmp / "Demo.app" / "Demo").unlink()
        (self.tmp / "Demo.app" / "real").write_bytes(ARM64)
        (self.tmp / "Demo.app" / "Demo").symlink_to("real")
        self.assertEqual(inventory.locate_binary(self.tmp / "Demo.app")[0].name, "Demo")

    def test_debug_dylib_symlink_leaving_the_bundle_is_refused(self):  # PAT-252 round 1, remark 6
        outside = self.tmp / "outside"
        outside.write_bytes(ARM64)
        app = self._bundle(None)
        (app / "Demo.debug.dylib").symlink_to(outside)
        with self.assertRaises(inventory.InventoryError):
            inventory.locate_binary(app)

    def test_absent_keys_are_not_an_empty_ok(self):  # PAT-252 round 1, remark 7
        class R:
            def __init__(self, doc):
                self.doc = doc

            def call(self, *a, **k):
                return self.doc, None
        none = inventory.section_types(R(answer("d", {"result": {"coverage": []}})), Path("x"))
        self.assertEqual(none["status"], "unknown")
        some = inventory.section_types(R(answer("d", {"result": {"objc_classes": [], "coverage": []}})), Path("x"))
        self.assertEqual(some["status"], "ok")
        self.assertEqual(some["absent_keys"], ["objc_protocols", "swift_conformances"])
        full = inventory.section_types(R(answer("d", {"result": {"objc_classes": [], "objc_protocols": [],
                                                                  "swift_conformances": []}})), Path("x"))
        self.assertEqual(full["absent_keys"], [])

    def test_missing_catalog_list_is_unknown_and_unexpected_string_rows_are_counted(self):  # PAT-252 round 1, remark 7
        class R:
            def call(self, args, *a, **k):
                if args[0] == "search":
                    return answer("s", [{"address": "0x1", "value": "ok"}, {"address": "0x2", "value": None}, "junk"]), None
                return answer(args[0], {"unexpected": []}), None
        s = inventory.section_strings(R(), Path("x"))
        self.assertEqual((len(s["items"]), s["dropped_rows"]), (1, 2))
        app = self.tmp / "A.app"
        app.mkdir()
        res = inventory.section_resources(R(), app, [], app / "A")
        self.assertIn("no `catalogs` list", res["unknown_parts"]["asset_catalogs"])
        self.assertIn("no `documents` list", res["unknown_parts"]["interface_builder"])

    def test_null_digest_does_not_raise(self):  # PAT-252 round 1, remark 7
        for subject in ({"digest": None}, {"digest": {"sha256": None}}, None, "x", {}):
            doc = answer("a", {}, subject=subject)
            doc["subject"] = subject
            self.assertIsNone(inventory.subject_sha256(doc), subject)
            self.assertIsNone(decompile._evidence(doc, "0x1")["target_sha256"])
        self.assertEqual(inventory.subject_sha256(answer("a", {})), SHA)

    def test_inventory_evidence_id_is_one_per_rea_call_not_per_item(self):  # PAT-253 round 1, remark 4 (documented limit)
        class R:
            def call(self, *a, **k):
                return answer("s", [{"address": "0x1", "value": "One"}, {"address": "0x2", "value": "Two"}]), None
        items = inventory.section_strings(R(), Path("x"))["items"]
        self.assertEqual({i["evidence"]["rea_evidence_id"] for i in items}, {"ev_s"})
        self.assertEqual(len({i["evidence"]["address"] for i in items}), 2)


def inv_doc(sha=SHA, target_sha=TARGET_SHA) -> dict:
    return {"format": inventory.FORMAT,
            "target": {"name": "D.app", "sha256": target_sha, "arm64_sha256": sha, "analysed_binary": "D"},
            "types": {"status": "ok", "reason": None, "items": []},
            "ui_strings": {"status": "ok", "reason": None, "items": []}}


class DecompileInputsTest(Base):
    def test_entry_points_skip_non_hex_addresses_and_mark_intermediate_candidates(self):  # PAT-253 round 1, remarks 3, 7
        m = "DemoApp"
        base = f"_$s{len(m)}{m}6Widget"
        rows = [{"address": "0x20", "value": base + "C5startyyF"},
                {"address": "0x30", "value": base + "C4makeyyYaF"},
                {"address": "0x40", "value": base + "C4name" + "fC"},
                {"address": "0x50", "value": base + "C5otheryyF"},
                {"address": "zz", "value": base + "C6brokenyyF"},
                {"address": "0x60 junk", "value": base + "C6brokeyyF"}]
        out = decompile.entry_points(rows, m, ["Widget"])
        self.assertEqual([e["address"] for e in out], ["0x20", "0x30", "0x40", "0x50"])
        by = {e["address"]: e for e in out}
        self.assertNotIn("caveat", by["0x20"])
        self.assertIn("partial functions", by["0x30"]["caveat"])
        self.assertIn("`fc` initialiser", by["0x40"]["caveat"])

    def test_value_type_initialisers_and_static_async_functions(self):  # PAT-259 review 1, remarks 2, 8
        def an(*rows):
            return {"entries": [{"address": a, "owner": "TipManager", "symbol": s, "demangled": ""} for a, s in rows]}
        out = decompile.analysis_entry_points(an(
            ("0x10", "_$s1A10TipManagerV3hex5scaleACSS_SdtcfC"),     # struct initialiser: the body is in itself
            ("0x20", "_$s1A10TipManagerC3hex5scaleACSS_SdtcfC"),     # class allocating initialiser
            ("0x30", "_$s1A10TipManagerV4loadyyYaFZ"),               # static async function
            ("0x40", "_$s1A10TipManagerO4loadyyYaF")), "Tip")        # async function on an enum
        by = {c["address"]: c for c in out}
        self.assertNotIn("caveat", by["0x10"])
        self.assertIn("`fc` initialiser", by["0x20"]["caveat"])
        self.assertIn("partial functions", by["0x30"]["caveat"])
        self.assertIn("partial functions", by["0x40"]["caveat"])
        self.assertIsNone(decompile.caveat("_$s1A10TipManagerV3hexfC", None))

    def test_candidates_summary_says_when_the_listing_is_cut(self):  # PAT-253 round 1, remark 8
        cands = [{"address": hex(i)} for i in range(decompile.MAX_LISTED + 5)]
        s = decompile.candidates_summary(cands, cands[:6])
        self.assertEqual((s["total"], len(s["listed"]), s["listed_truncated"], s["chosen"]),
                         (decompile.MAX_LISTED + 5, decompile.MAX_LISTED, True, 6))
        self.assertFalse(decompile.candidates_summary(cands[:3], cands[:3])["listed_truncated"])

    def test_analysis_candidates_carry_the_same_caveat(self):  # PAT-253 round 1, remark 3
        an = {"entries": [{"address": "0x10", "owner": "TipManager", "symbol": "_$s1A10TipManagerC4makeyyYaF",
                           "demangled": "A.TipManager.make() async -> ()"},
                          {"address": "0x20", "owner": "TipManager", "symbol": "_$s1A10TipManagerC4makeyyF",
                           "demangled": "A.TipManager.make() -> ()"}]}
        out = decompile.analysis_entry_points(an, "Tip")
        self.assertEqual([bool(c.get("caveat")) for c in out], [True, False])

    def test_max_and_budget_are_validated(self):  # PAT-253 round 2, R2
        target = self.tmp / "T"
        target.write_bytes(TARGET_BYTES)
        registry.add(paths.registry_file(self.data), str(target), "own product")
        inv = self.tmp / "inv.json"
        inv.write_text(json.dumps(inv_doc()))
        with mock.patch.object(setup, "tools_ready", return_value=[]), \
                mock.patch.object(inventory.proc, "run") as run, mock.patch("subprocess.Popen") as sub:
            for bad in (["--max", "-1"], ["--max", "0"], ["--budget", "0"], ["--budget", "-5"], ["--budget", "nan"],
                        ["--budget", "inf"]):
                with self.subTest(bad=bad):
                    rc, _, err = cli("decompile", str(target), "--inventory", str(inv), "--entry", "0x10", *bad)
                    self.assertEqual(rc, blueprint_cli.EXIT_USAGE)
                    self.assertIn(bad[0], err)
            run.assert_not_called()
            sub.assert_not_called()

    def test_analysis_of_a_modified_target_is_refused_without_launching_anything(self):  # PAT-260 round 1, remark 2
        target = self.tmp / "T"
        target.write_bytes(TARGET_BYTES)
        registry.add(paths.registry_file(self.data), str(target), "own product")
        inv, an = self.tmp / "inv.json", self.tmp / "an.json"
        inv.write_text(json.dumps(inv_doc()))
        an.write_text(json.dumps({"swift_types": [], "entries": [], "source": {}}))
        self.assertEqual(decompile.check_target(target, json.loads(inv.read_text())), None)
        target.write_bytes(b"other bytes")
        with mock.patch.object(inventory.proc, "run") as run, mock.patch("subprocess.Popen") as sub:
            rc, _, err = cli("decompile", str(target), "--inventory", str(inv), "--analysis", str(an), "--list",
                             "--group", "X")
            run.assert_not_called()
            sub.assert_not_called()
        self.assertEqual(rc, blueprint_cli.EXIT_FAILED)
        self.assertIn("sha256 differs", err)

    def test_malformed_analysis_is_a_usage_error_not_a_traceback(self):  # PAT-260 round 1, remark 9
        target = self.tmp / "T"
        target.write_bytes(TARGET_BYTES)
        registry.add(paths.registry_file(self.data), str(target), "own product")
        inv, an = self.tmp / "inv.json", self.tmp / "an.json"
        inv.write_text(json.dumps(inv_doc()))
        src = {"sha256": TARGET_SHA, "arm64_sha256": SHA}
        for bad, want in (([], blueprint_cli.EXIT_USAGE),  # not an object
                          ({"swift_types": [], "entries": [{"address": "0x10"}], "source": src},
                           blueprint_cli.EXIT_USAGE),  # an entry without its fields
                          ({"swift_types": [], "entries": [], "source": src}, blueprint_cli.EXIT_USAGE),  # no `ghidra`
                          ({"swift_types": [], "entries": [], "source": None, "ghidra": None},
                           blueprint_cli.EXIT_FAILED)):  # readable, but not made from this target
            an.write_text(json.dumps(bad))
            with self.subTest(bad=bad):
                rc, _, err = cli("decompile", str(target), "--inventory", str(inv), "--analysis", str(an),
                                 "--entry", "0x10")
                self.assertEqual(rc, want, err)
                self.assertNotIn("Traceback", err)

    def test_rebuild_malformed_analysis_exits_2(self):  # PAT-260 round 1, remark 9
        inv, an = self.tmp / "inv.json", self.tmp / "an.json"
        inv.write_text(json.dumps(inv_doc()))
        for bad in ([], {"format": 1, "source": None}, {"format": 1, "source": {"arm64_sha256": SHA},
                                                       "swift_types": None, "entries": []},
                    {"format": 1, "source": {"arm64_sha256": SHA}, "swift_types": [], "entries": [{"owner": 5}]}):
            an.write_text(json.dumps(bad))
            with self.subTest(bad=bad):
                rc, _, err = cli("rebuild", str(an), "--inventory", str(inv))
                self.assertEqual(rc, blueprint_cli.EXIT_USAGE, err)
                self.assertIn("rebuild failed", err)


class ResourcesTest(unittest.TestCase):
    def test_mp4_is_not_a_sound(self):  # PAT-260 round 1, remark 7
        self.assertEqual(resources.kind_of("intro.mp4"), "other")
        self.assertEqual(resources.kind_of("chime.MP3"), "sound")

    def test_usage_cut_is_stated_not_silent(self):  # PAT-260 round 1, remark 4
        n = resources.MAX_USAGE + 3
        inv = inv_doc()
        inv["resources"] = {"status": "ok", "reason": None, "items": [
            {"kind": "file", "path": "a.png", "size": 1, "evidence": {"source": "sha256", "sha256": "11" * 32}},
            {"kind": "file", "path": "b.png", "size": 1, "evidence": {"source": "sha256", "sha256": "22" * 32}}]}
        inv["ui_strings"]["items"] = (
            [{"value": "a.png", "evidence": {"address": f"0x{i:x}"}} for i in range(1, n + 1)]
            + [{"value": "b", "evidence": {"address": "0x900"}}])
        an = {"xrefs": [], "source": {"arm64_sha256": SHA}}
        items = {i["path"]: i for i in rebuild.resource_section(an, inv)["items"]}
        a, b = items["a.png"], items["b.png"]
        self.assertEqual((len(a["usage"]), a["usage_strings_total"], a["usage_truncated"]), (resources.MAX_USAGE, n, True))
        self.assertEqual((b["usage_strings_total"], b["usage_truncated"], len(b["usage"])), (1, False, 1))
        res = rebuild.resource_section(an, inv)
        an_min = {"entries": [], "functions": {"truncated": 0, "total": 0}, "timing": {}, "unknown_parts": {}}
        cov = rebuild.coverage(an_min, {}, None, {"unknown_count": 0}, res)
        self.assertTrue(any("more matching strings than the" in x for x in cov["not_covered"]), cov["not_covered"])


class SpecInputsTest(unittest.TestCase):
    def _spec(self, **over):
        s = {"format": 1, "title": "Timer app", "summary": "A timer.",
             "features": [{"name": "Timer", "summary": "Counts down.",
                           "points": [{"text": "It counts down.", "evidence": "unknown"}]}]}
        s.update(over)
        return s

    def test_title_summary_and_feature_names_are_checked_for_leaks(self):  # PAT-253 round 1, remark 6
        self.assertEqual(spec.check(self._spec(), set()), [])
        self.assertTrue(any("`title`" in e for e in spec.check(self._spec(title="About _$s7DemoApp"), set())))
        self.assertTrue(any("`summary`" in e for e in spec.check(self._spec(summary="At 0x1000abcd"), set())))
        s = self._spec()
        s["features"][0]["name"] = "Fn _$s4Main"
        self.assertTrue(any("name" in e and "not product language" in e for e in spec.check(s, set())))

    def test_feature_summary_is_checked_once_not_once_per_point(self):  # PAT-253 round 1, remark 6
        s = self._spec()
        s["features"][0]["summary"] = "Calls _$s1AB"
        s["features"][0]["points"] = [{"text": f"Point {i}.", "evidence": "unknown"} for i in range(4)]
        errs = [e for e in spec.check(s, set()) if "not product language" in e]
        self.assertEqual(len(errs), 1)
        self.assertIn("summary", errs[0])

    def test_evidence_files_of_two_targets_are_refused(self):  # PAT-253 round 1, remark 5
        a, b = inv_doc(sha="aa" * 32), inv_doc(sha="bb" * 32)
        dec = {"source": {"arm64_sha256": "aa" * 32}, "entries": []}
        self.assertEqual(spec.evidence_targets(a, dec, {"screens": []}, [], "x"), {"aa" * 32})
        self.assertEqual(len(spec.evidence_targets(a, b)), 2)
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "spec.json").write_text(json.dumps(self._spec()))
            (t / "a.json").write_text(json.dumps(a))
            (t / "b.json").write_text(json.dumps(b))
            (t / "d.json").write_text(json.dumps(dec))
            rc, _, err = cli("spec", "check", str(t / "spec.json"), str(t / "a.json"), str(t / "b.json"))
            self.assertEqual(rc, blueprint_cli.EXIT_USAGE)
            self.assertIn("different targets", err)
            rc, _, err = cli("spec", "check", str(t / "spec.json"), str(t / "a.json"), str(t / "d.json"))
            self.assertEqual(rc, 0, err)


class ExportScriptTest(unittest.TestCase):
    """The Java script cannot run without Ghidra: these tests only guard its text. Its behaviour was checked by a real
    `analyze` of the registered target on 2026-10-18 (README, `analyze`); they do not prove it."""

    java = (ghidra.SCRIPTS_DIR / pins.GHIDRA_SCRIPT).read_text()

    def test_label_address_accepts_a_loaded_release_image(self):  # PAT-260 round 1, remark 5
        m = re.search(r'LABEL_ADDRESS = Pattern\.compile\("([^"]+)"\)', self.java)
        self.assertIsNotNone(m)
        rx = re.compile(m.group(1).replace("\\\\", "\\"))
        for label, want in (("s_H_llo_0018eb30", "0018eb30"), ("s_H_llo_100001234", "100001234"),
                            ("s_x_0000000100001234", "0000000100001234")):
            self.assertEqual(rx.search(label).group(1), want, label)
        self.assertIsNone(rx.search("s_H_llo_123"))

    def test_absolute_symbolic_references_and_record_kinds(self):  # PAT-260 round 1, remark 6
        self.assertRegex(self.java, r"c >= 0x18 && c <= 0x1f\)\s*\{[^}]*i \+= 9;")
        self.assertRegex(self.java, r"c >= 0x02 && c <= 0x17\)\s*\{[^}]*i \+= 5;")
        self.assertIn("(raw & 3) != 0", self.java)

    def test_sp_writing_instructions_forget_their_other_results(self):  # PAT-259 review 1, remark 4
        branch = self.java.split("if (writesSp) {", 1)[1].split("continue;", 1)[0]
        self.assertIn("forgetResults(in, -1)", branch)

    def test_walk_forgets_every_written_register(self):  # PAT-260 round 3, remark 2
        self.assertIn("private void forgetResults(Instruction in, int keepFp)", self.java)
        self.assertEqual(self.java.count("forgetResults(in,"), 3)  # floatMove, the sp branch and the store branch
        self.assertIn("plain && i < count", self.java)
        self.assertIn("copiesFrame", self.java)
