"""Offline tests of the rebuild-grade analysis (PAT-260): fake Ghidra and fake REA, no network, no model."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tooling"))

import blueprint_cli
from blueprint import (
    analysis,
    constants,
    decompile,
    ghidra,
    inventory,
    paths,
    pins,
    rebuild,
    resources,
    screens,
    setup,
    spec,
)

SHA = "ab" * 32
MOD = "DemoApp"


def cli(*argv: str) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with mock.patch.object(sys, "argv", ["blueprint_cli.py", *argv]), redirect_stdout(out), redirect_stderr(err):
        try:
            rc = blueprint_cli.main()
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def sym(owner: str, rest: str, kind: str = "C") -> str:
    return f"_$s{len(MOD)}{MOD}{len(owner)}{owner}{kind}{rest}"


def make_inv() -> dict:
    def ev(a):
        return {"rea_evidence_id": "ev_s", "target_sha256": SHA, "address": a, "file_offset": None}

    def f(path, size, sha):
        return {"kind": "file", "path": path, "size": size, "evidence": {"source": "sha256", "sha256": sha}}
    return {"format": inventory.FORMAT,
            "target": {"name": "Demo.app", "sha256": "cd" * 32, "arm64_sha256": SHA, "analysed_binary": "Demo"},
            "types": {"status": "ok", "reason": None, "items": [
                {"kind": "objc-class", "name": f"_TtC{len(MOD)}{MOD}7Session", "evidence": ev("0x1")}]},
            "ui_strings": {"status": "ok", "reason": None, "items": [
                {"value": "chime", "evidence": ev("0x500")}, {"value": "Hello", "evidence": ev("0x510")}]},
            "resources": {"status": "ok", "reason": None, "items": [
                f("chime.mp3", 10, "11" * 32), f("hush.m4a", 20, "22" * 32), f("icon.png", 5, "33" * 32),
                f("Assets.car", 7, "44" * 32), f("README.md", 3, "55" * 32)]}}


def entry(symbol, demangled, code, addr, owner=None, string_literals=None, status="ok"):
    return {"address": addr, "symbol": symbol, "demangled": demangled, "owner": owner, "size": 10, "status": status,
            "reason": None if status == "ok" else "no", "pseudo_code": code if status == "ok" else None,
            "truncated": False, "string_literals": string_literals or {},
            "evidence": analysis.ghidra_evidence(SHA, addr, "prog1")}


def getter(name, typ, code, addr, path="DemoApp.Constants.Timer"):
    return entry(f"_$s7DemoApp9ConstantsV5TimerV{len(name)}{name}SdvgZ", f"static {path}.{name}.getter : {typ}",
                 code, addr)


def body(stmt):
    return f"\nlong f(void)\n\n{{\n  {stmt}\n}}\n"


def make_analysis() -> dict:
    es = [
        getter("tick", "Swift.Double", body("return 0x3fb999999999999a;"), "0x10"),
        getter("fps", "Swift.Int", body("return 10;"), "0x20"),
        getter("neg", "Swift.Int", body("return 0xffffffffffffffff;"), "0x28"),
        getter("weird", "Swift.Int", body("foo();\n  return 1;"), "0x30"),
        entry(sym("Constants", "5TimerV8greetingSSvgZ", "V"), "static DemoApp.Constants.Timer.greeting.getter : Swift.String",
              body("__sSS3foo();\n  return 1;"), "0x40"),
        entry("_$s7DemoApp9ConstantsV5TimerV8greeting_WZ", None,
              body('__sSS21_builtinStringLiteral17utf8CodeUnitCount7isASCIISSBp_BwBi1_tcfC(s_H_llo_0018eb30,6,0);'),
              "0x44", string_literals={"s_H_llo_0018eb30": "Héllo"}),
        entry("_$s7DemoApp14PresetProgramsV7sommeilAA3PgmCvgZ", "static DemoApp.PresetPrograms.sommeil.getter : DemoApp.Pgm",
              body("sommeilvau();\n  return 1;"), "0x50"),
        entry("_$s7DemoApp14PresetProgramsV7sommeil_WZ", None,
              body('__sSS21_builtinStringLiteral17utf8CodeUnitCount7isASCIISSBp_BwBi1_tcfC("Sleep",5,1);\n'
                   '  *(long *)(p + 8) = 4;\n  __s7DemoApp3PgmC4name6inhalecfC(a,b,7);'), "0x54"),
        entry("_$s7DemoApp3PgmC4name6inhalecfC", "DemoApp.Pgm.__allocating_init(name: Swift.String, inhale: Swift.Int)",
              body("return;"), "0x58"),
        entry("_$s7DemoApp9ConstantsV17PreviewRegistryV4lineSivgZ",
              "static DemoApp.$s7DemoApp0Preview.PreviewRegistry.line.getter : Swift.Int", body("return 3;"), "0x60"),
        entry(sym("SessionView", "4bodyQrvg"), "DemoApp.SessionView.body.getter : some", body("return;"), "0x70", "SessionView"),
        entry(sym("SessionViewModel", "5startyyF"), "DemoApp.SessionViewModel.start() -> ()", body("return;"), "0x80",
              "SessionViewModel"),
        entry(sym("SessionViewModel", "4stopyyF"), "DemoApp.SessionViewModel.stop() -> ()", None, "0x90",
              "SessionViewModel", status="unknown"),
        entry(sym("Session", "4nameSSvg"), "DemoApp.Session.name.getter : Swift.String", body("return;"), "0xa0", "Session"),
        entry(sym("Session", "8durationSdvg"), "DemoApp.Session.duration.getter : Swift.Double", body("return;"), "0xb0",
              "Session"),
        entry(sym("Player", "4playyyF"), "DemoApp.Player.play() -> ()", body("return;"), "0xc0", "Player"),
    ]
    field = lambda n: {"name": n, "type": "[_NoType]", "var": True}
    types = [
        {"address": "0x1000", "kind": "struct", "name": "SessionView", "fields": []},
        {"address": "0x1010", "kind": "class", "name": "SessionViewModel", "fields": []},
        {"address": "0x1020", "kind": "class", "name": "Session",
         "fields": [field("_name"), field("_duration"), field("_$backingData"), field("_$observationRegistrar")]},
        {"address": "0x1030", "kind": "class", "name": "Player", "fields": [field("_player")]},
        {"address": "0x1040", "kind": "enum", "name": "SchemaV1", "fields": []},
        {"address": "0x1050", "kind": "struct", "name": "$s7DemoApp0PreviewView", "fields": []},
        {"address": "0x1060", "kind": "struct", "name": "OrphanView", "fields": []},
    ]
    types = [{**t, "evidence": analysis.ghidra_evidence(SHA, t["address"], "prog1")} for t in types]
    return {"format": analysis.FORMAT,
            "source": {"name": "Demo.app", "sha256": "cd" * 32, "arm64_sha256": SHA, "analysed_binary": "Demo"},
            "tools": {"ghidra": pins.GHIDRA_VERSION, "rea": pins.REA_VERSION, "jdk": pins.JDK_VERSION},
            "ghidra": {"program_id": "prog1", "project": SHA[:16], "program": "Demo.arm64"}, "module": MOD,
            "timing": {"analysis_seconds": 50.0, "analysis_reused": True, "export_seconds": 5.0, "demangle_seconds": 1.0,
                       "total_seconds": 7.0},
            "functions": {"total": 30, "app": len(es), "decompiled": len(es) - 1, "failed": 1, "truncated": 0,
                          "decompile_seconds": 1.0},
            "entries": es, "swift_types": types, "data_symbols": [],
            "xrefs": [{"address": "0x500", "functions": [{"address": "0x70", "name": sym("SessionView", "4bodyQrvg"),
                                                          "demangled": "DemoApp.SessionView.body.getter : some"}]}],
            "unknown_parts": {}}


class ConstantsTest(unittest.TestCase):
    def test_decode_literals(self):
        self.assertEqual(constants.decode_literal("0x3fb999999999999a", "Swift.Double"), (0.1, None))
        self.assertEqual(constants.decode_literal("0x3f800000", "Swift.Float"), (1.0, None))
        self.assertEqual(constants.decode_literal("0", "CoreGraphics.CGFloat"), (0.0, None))
        self.assertEqual(constants.decode_literal("7", "Swift.Double")[0], None)
        self.assertEqual(constants.decode_literal("0xffffffffffffffff", "Swift.Int"), (-1, None))
        self.assertEqual(constants.decode_literal("0xffffffff", "Swift.UInt32"), (4294967295, None))
        self.assertEqual(constants.decode_literal("1", "Swift.Bool"), (True, None))
        self.assertIsNone(constants.decode_literal("1", "Foo.Bar")[0])

    def test_extract_values_unknowns_objects_and_previews(self):
        c = constants.extract(make_analysis()["entries"])
        by = {i["name"]: i for i in c["items"]}
        self.assertEqual(by["DemoApp.Constants.Timer.tick"]["value"], 0.1)
        self.assertEqual(by["DemoApp.Constants.Timer.fps"]["value"], 10)
        self.assertEqual(by["DemoApp.Constants.Timer.neg"]["value"], -1)
        self.assertEqual(by["DemoApp.Constants.Timer.weird"]["value"], "unknown")
        self.assertIn("not a plain literal", by["DemoApp.Constants.Timer.weird"]["reason"])
        self.assertEqual(by["DemoApp.Constants.Timer.greeting"]["value"], "Héllo")
        obj = by["DemoApp.PresetPrograms.sommeil"]["value"]
        self.assertEqual((obj["string_literals"], obj["integer_literals_in_order"], obj["mapping_to_parameters"]),
                         (["Sleep"], [4], "unknown"))
        self.assertEqual(obj["constructor_call_arguments"], ["not a literal", "not a literal", 7])
        self.assertIn("partial list", by["DemoApp.PresetPrograms.sommeil"]["reason"])
        self.assertIn("name: Swift.String", obj["constructor"])
        self.assertNotIn("PreviewRegistry", " ".join(by))
        self.assertEqual(c["excluded_preview_records"], 1)
        self.assertEqual(c["unknown_count"], 1)
        self.assertEqual(by["DemoApp.Constants.Timer.tick"]["evidence"][0]["evidence_id"], "gh_" + SHA[:16] + "_10")

    def test_register_arguments_are_read_from_the_constructor_call(self):
        self.assertEqual(constants.split_args("a, f(b, c), \"x,y\", 5"), ["a", "f(b, c)", "\"x,y\"", "5"])
        self.assertEqual(constants.call_arguments("p,q,4,7,0x8,-1,uVar1"),
                         ["not a literal", "not a literal", 4, 7, 8, -1, "not a literal"])
        e = entry("_$s7DemoApp14PresetProgramsV5calmeAA3PgmCvgZ", "static DemoApp.PresetPrograms.calme.getter : DemoApp.Pgm",
                  body("calme();\n  return 1;"), "0x52")
        z = entry("_$s7DemoApp14PresetProgramsV5calme_WZ", None,
                  body('__sSS21_builtinStringLiteral17utf8CodeUnitCount7isASCIISSBp_BwBi1_tcfC("Calm",4,1);\n'
                       '  __s7DemoApp3PgmC4name6inhalecfC(a,\n b,5,4,v);'), "0x56")
        ctor = entry("_$s7DemoApp3PgmC4name6inhalecfC", "DemoApp.Pgm.init", body("return;"), "0x58")
        obj = {i["name"]: i for i in constants.extract([e, z, ctor])["items"]}["DemoApp.PresetPrograms.calme"]["value"]
        self.assertEqual(obj["constructor_call_arguments"], ["not a literal", "not a literal", 5, 4, "not a literal"])
        self.assertEqual(obj["integer_literals_in_order"], [])

    def test_private_scope_discriminator_is_moved_out_of_the_name(self):
        h = "9BBA4E908B17CF21996F356C5D461E56" * 1
        self.assertEqual(constants.readable_name(f"DemoApp.(Step in _{h}).prog"), ("DemoApp.Step.prog", "_" + h))
        self.assertEqual(constants.readable_name("DemoApp.Plain.prog"), ("DemoApp.Plain.prog", None))
        g = getter("prog", "Swift.Int", body("return 3;"), "0x10", path=f"DemoApp.(Step in _{h})")
        item = constants.extract([g])["items"][0]
        self.assertEqual((item["name"], item["private_scope"]), ("DemoApp.Step.prog", "_" + h))

    def test_string_length_mismatch_is_unknown_not_guessed(self):
        lits = constants.string_literals('__sSS21_builtinStringLiteral17utf8CodeUnitCount7isASCIISSBp_BwBi1_tcfC(s_x_0018eb30,9,0);',
                                         {"s_x_0018eb30": "Héllo"})
        self.assertEqual(lits[0]["value"], "unknown")
        self.assertEqual(constants.string_literals("builtinStringLiteral(s_y_0018eb31,4,0)", {})[0]["value"], "unknown")


class RebuildTest(unittest.TestCase):
    def test_coverage_is_counted_and_what_remains_is_listed(self):
        doc = rebuild.build(make_analysis(), make_inv())
        c = doc["coverage"]
        self.assertEqual((c["functions"]["detected"], c["functions"]["analysed"], c["functions"]["failed"]), (16, 15, 1))
        # Groups by role suffix: Session (view + view model: a screen), Orphan (a view with no function).
        self.assertEqual({g["name"]: (g["kind"], g["functions"], g["analysed"]) for g in doc["groups"]},
                         {"Orphan": ("screen", 0, 0), "Session": ("screen", 3, 2)})
        self.assertEqual((c["groups"]["detected"], c["groups"]["analysed"], c["groups"]["not_analysed"]), (2, 1, ["Orphan"]))
        self.assertEqual((c["screens"]["detected"], c["screens"]["analysed"]), (2, 1))
        self.assertEqual(c["functions"]["outside_app_module"], 30 - 16)
        text = "\n".join(c["not_covered"])
        for needle in ("Orphan", "1 app function(s) not decompiled", "outside the app module", "extensions of types that belong to another module",
                       "without a captured screenshot",
                       "static analysis only"):
            self.assertIn(needle, text)

    def test_data_model_properties_come_from_the_macro_marker_and_getters(self):
        dm = rebuild.build(make_analysis(), make_inv())["data_model"]
        self.assertEqual([m["name"] for m in dm["items"]], ["Session"])
        props = {p["name"]: p["type"] for p in dm["items"][0]["properties"]}
        self.assertEqual(props, {"name": "Swift.String", "duration": "Swift.Double"})
        self.assertEqual(dm["items"][0]["constraints_and_relationships"], "unknown")
        self.assertEqual([m["name"] for m in dm["schema_and_migration_types"]], ["SchemaV1"])
        an = make_analysis()
        an["swift_types"] = [t for t in an["swift_types"] if t["name"] != "Session"]
        self.assertEqual(rebuild.build(an, make_inv())["data_model"]["status"], "unknown")

    def test_resources_have_fingerprint_kind_and_known_usage_or_unknown(self):
        res = {r["path"]: r for r in rebuild.build(make_analysis(), make_inv())["resources"]["items"]}
        self.assertEqual({k: v["kind"] for k, v in res.items()}, {
            "chime.mp3": "sound", "hush.m4a": "sound", "icon.png": "image", "Assets.car": "asset-catalog",
            "README.md": "other"})
        u = res["chime.mp3"]["usage"][0]
        self.assertEqual((u["string"], u["string_address"]), ("chime", "0x500"))
        self.assertIn("DemoApp.SessionView.body.getter", u["referenced_from"][0]["function"])
        self.assertEqual(u["referenced_from"][0]["evidence"]["address"], "0x70")
        self.assertEqual(res["hush.m4a"]["usage"], "unknown")
        self.assertEqual(res["chime.mp3"]["evidence_id"], "res_" + "11" * 32)
        self.assertEqual(res["chime.mp3"]["sha256"], "11" * 32)

    def test_refuses_an_analysis_of_another_slice(self):
        inv = make_inv()
        inv["target"]["arm64_sha256"] = "ee" * 32
        with self.assertRaises(ValueError):
            rebuild.build(make_analysis(), inv)

    def test_resource_matching_names_stem_and_file(self):
        self.assertEqual(resources.names_of("a/b/chime.mp3"), {"chime.mp3", "chime"})
        self.assertEqual(resources.string_addresses(make_inv()), ["0x500"])
        self.assertEqual(resources.kind_of("x.PNG"), "image")


class ScreensTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.tmp = Path(t.name)
        self.shot = self.tmp / "shot.png"
        self.shot.write_bytes(b"png-bytes")
        self.sha = hashlib.sha256(b"png-bytes").hexdigest()

    def _report(self, **over) -> Path:
        rep = {"schema": "witness-proof.v1", "platform": "ios", "outcome": "proved", "checkout": {"commit": "c0ffee"},
               "navigation": {"screen": "Session"}, "paths": {"screenshot": str(self.shot)},
               "screenshot_sha256": self.sha} | over
        p = self.tmp / "report.json"
        p.write_text(json.dumps(rep))
        return p

    def _manifest(self, name="session", report="report.json") -> Path:
        m = self.tmp / "manifest.json"
        m.write_text(json.dumps({"format": 1, "screens": [{"name": name, "report": report}]}))
        return m

    def test_valid_manifest_links_screen_to_screenshot_with_an_evidence_id(self):
        self._report()
        doc = screens.build(self._manifest())
        s = doc["screens"][0]
        self.assertEqual((s["name"], s["screenshot_sha256"], s["evidence_id"], s["commit"]),
                         ("session", self.sha, "wit_" + self.sha, "c0ffee"))

    def test_every_problem_is_refused(self):
        cases = {
            "missing report": (lambda: self._manifest(report="nope.json"), "missing"),
            "wrong sha": (lambda: (self._report(screenshot_sha256="00" * 32), self._manifest())[1], "sha256 differs"),
            "no screenshot": (lambda: (self._report(paths={}), self._manifest())[1], "no screenshot"),
            "wrong schema": (lambda: (self._report(schema="other"), self._manifest())[1], "not a Witness"),
            "declared other screen": (lambda: (self._report(), self._manifest(name="Settings"))[1], "declares the screen"),
        }
        for label, (make, needle) in cases.items():
            with self.subTest(label), self.assertRaises(screens.ScreensError) as cm:
                screens.build(make())
            self.assertIn(needle, str(cm.exception))
        self._report()
        self.shot.unlink()
        with self.assertRaises(screens.ScreensError) as cm:
            screens.build(self._manifest())
        self.assertIn("does not exist", str(cm.exception))

    def test_rebuild_matches_captures_to_detected_screens(self):
        self._report()
        scr = screens.build(self._manifest())
        doc = rebuild.build(make_analysis(), make_inv(), scr)
        self.assertEqual(doc["coverage"]["screens_captured"], {"detected": 2, "captured": 1, "not_captured": ["Orphan"]})
        cap = {s["name"]: s["capture"] for s in doc["screens"]["detected"]}
        self.assertEqual(cap, {"Orphan": "unknown", "Session": "wit_" + self.sha})
        self.assertEqual(doc["screens"]["captures_not_matched_to_a_detected_screen"], [])

    def test_cli_screens_exit_codes(self):
        self._report()
        out = self.tmp / "screens.json"
        rc, stdout, _ = cli("screens", str(self._manifest()), "--output", str(out))
        self.assertEqual(rc, 0, stdout)
        self.assertEqual(json.loads(out.read_text())["screens"][0]["evidence_id"], "wit_" + self.sha)
        rc, _, err = cli("screens", str(self._manifest(report="nope.json")))
        self.assertEqual(rc, blueprint_cli.EXIT_USAGE)
        self.assertIn("missing", err)


class GhidraFake:
    """Fake analyzeHeadless and REA: records every command, writes the export document the script would write."""

    def __init__(self, export_functions=None):
        self.calls: list[list[str]] = []
        self.export_functions = export_functions if export_functions is not None else [
            {"address": "0x10", "name": sym("Session", "4nameSSvg"), "symbol": sym("Session", "4nameSSvg"), "size": 8,
             "pseudo_code": "code", "string_literals": {}, "error": None}]

    def __call__(self, cmd, timeout, cwd=None, env=None):
        assert timeout and timeout > 0
        self.calls.append(cmd)
        if Path(cmd[0]).name == "analyzeHeadless":
            if "-import" in cmd:
                return 0, "REPORT: Analysis succeeded for file\nREPORT: Import succeeded\n", ""
            out = Path(next(a for a in cmd if a.startswith("out="))[4:])
            out.write_text(json.dumps({
                "format": 1, "program": "Demo.arm64", "program_id": "prog1", "ghidra_version": pins.GHIDRA_VERSION,
                "functions_total": 9, "functions_matched": len(self.export_functions), "decompiled": len(self.export_functions),
                "decompile_failed": 0, "decompile_seconds": 0.5, "functions": self.export_functions, "data_symbols": [],
                "swift_types_status": "ok", "swift_types": [{"address": "0x20", "kind": "class", "name": "Session",
                                                              "fields": []}], "xrefs": []}))
            return 0, "", ""
        if cmd[1] == "demangle-swift":
            rows = [{"input": s, "output": "DemoApp.Session.name.getter : Swift.String", "status": "demangled"}
                    for s in cmd[3:-2]]
            return 0, json.dumps({"evidence_id": "ev_d", "normalized_result": {"symbols": rows}}), ""
        raise AssertionError(cmd)

    def headless(self, flag):
        return [c for c in self.calls if Path(c[0]).name == "analyzeHeadless" and flag in c]


class GhidraTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.tmp = Path(t.name)
        self.data = self.tmp / "data"
        self.slice = self.tmp / "Demo.arm64"
        self.slice.write_bytes(b"slice")

    def test_analysis_is_run_once_and_reused_by_every_export(self):
        fake = GhidraFake()
        clock = iter(range(0, 1000, 7)).__next__
        m1 = ghidra.ensure_analysis(self.data, self.slice, SHA, fake, lambda: float(clock()))
        m2 = ghidra.ensure_analysis(self.data, self.slice, SHA, fake)
        self.assertEqual((m1["reused"], m2["reused"]), (False, True))
        self.assertEqual(m1["analysis_seconds"], m2["analysis_seconds"])
        self.assertEqual(len(fake.headless("-import")), 1)
        for i in range(3):
            ghidra.export(self.data, m2, self.tmp / f"e{i}.json", include="^x", runner=fake)
        proc_calls = fake.headless("-process")
        self.assertEqual(len(proc_calls), 3)
        for c in proc_calls:  # no export analyses again, none may change the project
            self.assertIn("-noanalysis", c)
            self.assertIn("-readOnly", c)
            self.assertNotIn("-import", c)
            self.assertIn(pins.GHIDRA_SCRIPT, c)
        self.assertEqual(len(fake.headless("-import")), 1)

    def test_another_slice_or_ghidra_version_is_analysed_again(self):
        fake = GhidraFake()
        ghidra.ensure_analysis(self.data, self.slice, SHA, fake)
        ghidra.ensure_analysis(self.data, self.slice, "cd" * 32, fake)
        self.assertEqual(len(fake.headless("-import")), 2)
        marker = ghidra.project_dir(self.data, SHA) / ghidra.MARKER
        doc = json.loads(marker.read_text())
        marker.write_text(json.dumps(doc | {"ghidra_version": "0.0"}))
        self.assertIsNone(ghidra.read_marker(marker.parent, SHA))

    def test_failed_analysis_leaves_no_marker(self):
        def bad(cmd, timeout, cwd=None, env=None):
            return 0, "REPORT: Import succeeded\n", ""
        with self.assertRaises(ghidra.GhidraError):
            ghidra.ensure_analysis(self.data, self.slice, SHA, bad)
        self.assertIsNone(ghidra.read_marker(ghidra.project_dir(self.data, SHA), SHA))

        def timeout(cmd, timeout, cwd=None, env=None):
            return None, "", "timed out after 1s"
        with self.assertRaises(ghidra.GhidraError):
            ghidra.ensure_analysis(self.data, self.slice, SHA, timeout)

    def test_export_without_a_document_is_an_error(self):
        marker = {"slice_sha256": SHA, "program": "Demo.arm64"}
        with self.assertRaises(ghidra.GhidraError):
            ghidra.export(self.data, marker, self.tmp / "none.json", include="x", runner=lambda *a, **k: (1, "", "boom"))

    def test_script_arguments_match_what_python_passes(self):
        java = (ghidra.SCRIPTS_DIR / pins.GHIDRA_SCRIPT).read_text()
        accepted = set(re.findall(r'case "(\w+)":', java))
        fake = GhidraFake()
        marker = ghidra.ensure_analysis(self.data, self.slice, SHA, fake)
        ghidra.export(self.data, marker, self.tmp / "o.json", include="^x", data_pattern="d", xrefs_file=self.tmp / "x",
                      maximum=3, runner=fake)
        passed = {a.split("=", 1)[0] for a in fake.headless("-process")[0] if re.match(r"^\w+=", a)}
        self.assertEqual(passed, accepted)


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.tmp = Path(t.name)
        self.data = self.tmp / "data"
        self.slice = self.tmp / "Demo.arm64"
        self.slice.write_bytes(b"slice")

    def test_module_and_owner_are_derived_from_symbols(self):
        self.assertEqual(analysis.app_module(make_inv()), MOD)
        self.assertEqual(analysis.owner_of(sym("Session", "4nameSSvg"), MOD), "Session")
        self.assertIsNone(analysis.owner_of("_$s7DemoApp0abB0V", MOD))
        self.assertIsNone(analysis.owner_of("_$sOther", MOD))

    def test_build_runs_ghidra_once_demangles_and_measures_total_time(self):
        fake = GhidraFake()
        t = iter(x * 2.0 for x in range(100))
        doc = analysis.build(make_inv(), self.slice, self.data, runner=fake, clock=lambda: next(t))
        again = analysis.build(make_inv(), self.slice, self.data, runner=fake)
        self.assertEqual(len(fake.headless("-import")), 1)
        self.assertFalse(doc["timing"]["analysis_reused"])
        self.assertTrue(again["timing"]["analysis_reused"])
        self.assertGreater(doc["timing"]["total_seconds"], 0)
        e = doc["entries"][0]
        self.assertEqual((e["demangled"], e["owner"], e["status"]), ("DemoApp.Session.name.getter : Swift.String", "Session", "ok"))
        self.assertEqual(e["evidence"]["evidence_id"], "gh_" + SHA[:16] + "_10")
        self.assertEqual(e["evidence"]["slice_sha256"], SHA)
        self.assertEqual(doc["ghidra"]["program_id"], "prog1")
        self.assertEqual(doc["swift_types"][0]["evidence"]["address"], "0x20")
        include = next(a for a in fake.headless("-process")[0] if a.startswith("include="))
        self.assertTrue(include.startswith("include=^_"))

    def test_long_pseudo_code_is_cut_and_says_so(self):
        fns = [{"address": "0x10", "name": "n", "symbol": sym("A", "1fyyF"), "size": 1,
                "pseudo_code": "x" * (analysis.MAX_CODE_CHARS + 5), "string_literals": {}, "error": None}]
        doc = analysis.build(make_inv(), self.slice, self.data, runner=GhidraFake(fns))
        self.assertTrue(doc["entries"][0]["truncated"])
        self.assertEqual(len(doc["entries"][0]["pseudo_code"]), analysis.MAX_CODE_CHARS)
        self.assertEqual(doc["functions"]["truncated"], 1)


class CliTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.tmp = Path(t.name)
        self.data = self.tmp / "data"
        p = mock.patch.dict(os.environ, {paths.ENV_DATA_DIR: str(self.data)})
        p.start()
        self.addCleanup(p.stop)
        self.inv = self.tmp / "inv.json"
        self.inv.write_text(json.dumps(make_inv()))
        self.target = self.tmp / "Demo"
        self.target.write_bytes(b"x")

    def test_analyze_is_refused_for_an_unregistered_target_before_anything_runs(self):
        with mock.patch.object(inventory.proc, "run") as run, mock.patch("subprocess.run") as sub:
            rc, _, err = cli("analyze", str(self.target), "--inventory", str(self.inv))
        self.assertEqual(rc, blueprint_cli.EXIT_REFUSED)
        self.assertIn("not in the registry", err)
        run.assert_not_called()
        sub.assert_not_called()

    def test_analyze_needs_the_prepared_tooling(self):
        from blueprint import registry
        registry.add(paths.registry_file(self.data), str(self.target), "own product")
        rc, _, err = cli("analyze", str(self.target), "--inventory", str(self.inv))
        self.assertEqual(rc, blueprint_cli.EXIT_NO_TOOLS)
        self.assertIn("setup", err)

    def test_rebuild_and_spec_check_end_to_end(self):
        an, out = self.tmp / "an.json", self.tmp / "rb.json"
        an.write_text(json.dumps(make_analysis()))
        rc, _, err = cli("rebuild", str(an), "--inventory", str(self.inv), "--output", str(out))
        self.assertEqual(rc, 0, err)
        self.assertIn("coverage groups 1/2", err)
        rb = json.loads(out.read_text())
        s = make_spec(rb)
        sp = self.tmp / "spec.json"
        sp.write_text(json.dumps(s))
        rc, _, err = cli("spec", "check", str(sp), str(out), str(self.inv))
        self.assertEqual(rc, 0, err)
        s["coverage"]["groups"]["analysed"] = 2
        sp.write_text(json.dumps(s))
        rc, _, err = cli("spec", "check", str(sp), str(out), str(self.inv))
        self.assertEqual(rc, blueprint_cli.EXIT_USAGE)
        self.assertIn("differs from the rebuild file", err)

    def test_decompile_from_analysis_launches_nothing(self):
        from blueprint import registry
        registry.add(paths.registry_file(self.data), str(self.target), "own product")
        inv = make_inv()
        inv["types"]["items"].append({"kind": "objc-class", "name": f"_TtC{len(MOD)}{MOD}16SessionViewModel",
                                      "evidence": {"rea_evidence_id": "ev_t", "address": "0x2"}})
        self.inv.write_text(json.dumps(inv))
        an, out = self.tmp / "an.json", self.tmp / "dec.json"
        an.write_text(json.dumps(make_analysis()))
        with mock.patch.object(inventory.proc, "run") as run, mock.patch("subprocess.run") as sub:
            rc, _, err = cli("decompile", str(self.target), "--inventory", str(self.inv), "--analysis", str(an),
                                  "--group", "Session", "--output", str(out))
            run.assert_not_called()
            sub.assert_not_called()
        self.assertEqual(rc, 0, err)
        doc = json.loads(out.read_text())
        self.assertEqual(doc["from_analysis"]["ghidra_runs"], 0)
        self.assertEqual([(e["method"], e["status"]) for e in doc["entries"]], [("body", "ok"), ("start", "ok"), ("stop", "unknown")])
        self.assertEqual(doc["entries"][1]["evidence"]["evidence_id"], "gh_" + SHA[:16] + "_80")

    def _auth(self):
        from blueprint import registry
        registry.add(paths.registry_file(self.data), str(self.target), "own product")
        an = self.tmp / "an.json"
        an.write_text(json.dumps(make_analysis()))
        return an

    def test_group_of_the_analysis_is_accepted_without_an_inventory_class(self):
        an = self._auth()
        out = self.tmp / "d.json"
        rc, _, err = cli("decompile", str(self.target), "--inventory", str(self.inv), "--analysis", str(an),
                         "--group", "Session", "--output", str(out))
        self.assertEqual(rc, 0, err)
        self.assertEqual([e["method"] for e in json.loads(out.read_text())["entries"]], ["body", "start", "stop"])
        rc, _, err = cli("decompile", str(self.target), "--inventory", str(self.inv), "--analysis", str(an),
                         "--group", "Nope")
        self.assertEqual(rc, blueprint_cli.EXIT_USAGE)
        self.assertIn("Session", err)

    def test_view_body_and_computed_getters_are_candidates_but_not_wrapper_storage(self):
        v = decompile._view_code
        self.assertTrue(v("_$s1A4HomeV4bodyQrvg", "A.Home.body.getter : some"))
        self.assertTrue(v("_$s1A4HomeV4hero33_XLLSbvg", "A.Home.(hero in _X).getter : Swift.Bool"))
        self.assertTrue(v("_$s1A4HomeV4bodyQrvgyyXEfU_", "closure #1 () -> () in A.Home.body.getter : some"))
        self.assertFalse(v("_$s1A4HomeV5_flag7SwiftUI5StateVySbGvg", "A.Home._flag.getter : SwiftUI.State<Swift.Bool>"))
        self.assertFalse(v("_$s1A4HomeV5_flagvs", "A.Home._flag.setter : Swift.Bool"))
        self.assertFalse(v("_$s1A4HomeV3runyyF", "A.Home.run() -> ()"))

    def test_private_type_and_static_functions_are_candidates(self):
        h = "D46F2947CCA3928677AC0B74CE72F766"
        an = {"entries": [
            {"address": "0x10", "owner": "RootView", "symbol": f"_$s1A8RootView33_{h}LLV4bodyQrvg",
             "demangled": f"A.(RootView in _{h}).body.getter : some"},
            {"address": "0x20", "owner": "TipManager", "symbol": "_$s1A10TipManagerV4markyyFZ",
             "demangled": "static A.TipManager.mark() -> ()"},
            {"address": "0x30", "owner": "RootView", "symbol": f"_$s1A8RootView33_{h}LLVMa",
             "demangled": f"type metadata accessor for A.(RootView in _{h})"}]}
        self.assertEqual([c["method"] for c in decompile.analysis_entry_points(an, "Root")], ["body"])
        self.assertEqual([c["method"] for c in decompile.analysis_entry_points(an, "Tip")], ["mark"])

    def test_list_writes_nothing(self):
        an = self._auth()
        rc, out, err = cli("decompile", str(self.target), "--inventory", str(self.inv), "--analysis", str(an),
                           "--group", "Session", "--list")
        self.assertEqual(rc, 0, err)
        self.assertIn("start", out)
        self.assertFalse(paths.decompilations(self.data).exists())
        self.assertIn("nothing written", err)

    def test_bad_entry_address_is_a_usage_error_and_unknown_is_not_success(self):
        an = self._auth()
        base = ["decompile", str(self.target), "--inventory", str(self.inv), "--analysis", str(an), "--output",
                str(self.tmp / "e.json")]
        rc, _, _err = cli(*base, "--entry", "0x80 junk")
        self.assertEqual(rc, blueprint_cli.EXIT_USAGE)
        self.assertFalse((self.tmp / "e.json").exists())
        rc, out, _ = cli(*base, "--entry", "0xdead")
        self.assertEqual(rc, blueprint_cli.EXIT_FAILED)
        self.assertIn("0 of 1 entry point(s) decompiled, 1 unknown", out)
        rc, out, _ = cli(*base, "--entry", "0x80")
        self.assertEqual(rc, 0)
        self.assertIn("1 of 1 entry point(s) decompiled, 0 unknown", out)

    def test_decompile_from_another_targets_analysis_is_refused(self):
        inv = make_inv()
        inv["target"]["sha256"] = "ee" * 32
        with self.assertRaises(decompile.DecompileError):
            decompile.from_analysis(make_analysis(), inv, group=None, entry="0x80", list_only=False, maximum=1)

    def test_tools_ready_is_not_needed_for_offline_commands(self):
        with mock.patch.object(setup, "tools_ready", side_effect=AssertionError("must not be called")):
            an = self.tmp / "an.json"
            an.write_text(json.dumps(make_analysis()))
            rc, _, _ = cli("rebuild", str(an), "--inventory", str(self.inv), "--output", str(self.tmp / "o.json"))
        self.assertEqual(rc, 0)


def make_spec(rb: dict) -> dict:
    c = rb["coverage"]
    tick = next(i for i in rb["constants"]["items"] if i["name"].endswith("tick"))
    return {"format": spec.FORMAT_REBUILD, "title": "Rebuild the demo app", "summary": "A demo.",
            "features": [{"name": "Session", "summary": "A session.", "points": [
                {"text": "A session can be started.",
                 "evidence": [rb["data_model"]["items"][0]["evidence"]["evidence_id"]]}]}],
            "coverage": {k: {"analysed": c[k]["analysed"], "detected": c[k]["detected"]} for k in ("groups", "screens", "functions")}
            | {"not_covered": c["not_covered"][:2]},
            "constants": [{"name": "tick interval", "value": 0.1, "evidence": [tick["evidence"][0]["evidence_id"]]},
                          {"name": "default volume", "value": "unknown", "evidence": "unknown"}],
            "data_model": [{"name": "Session", "evidence": [rb["data_model"]["items"][0]["evidence"]["evidence_id"]],
                            "properties": [{"name": "name", "type": "String", "evidence": "unknown"}]}],
            "resources": [{"path": "chime.mp3", "sha256": "11" * 32, "kind": "sound", "usage": "A chime sound.",
                           "evidence": ["res_" + "11" * 32]}],
            "screens": [{"name": "Session", "summary": "The session screen.", "navigation": "unknown",
                         "capture": "unknown"}]}


class SpecSectionsTest(unittest.TestCase):
    def setUp(self):
        self.rb = rebuild.build(make_analysis(), make_inv())
        self.known = spec.evidence_ids(self.rb, make_inv())

    def errs(self, s, rb=True):
        return spec.check(s, self.known, self.rb if rb else None)

    def test_valid_rebuild_spec(self):
        self.assertEqual(self.errs(make_spec(self.rb)), [])

    def test_format_one_needs_no_section_and_format_two_needs_all(self):
        s = make_spec(self.rb)
        s["format"] = 1
        for k in spec.SECTIONS:
            s.pop(k)
        self.assertEqual(self.errs(s), [])
        s["format"] = 2
        self.assertEqual(len(self.errs(s)), len(spec.SECTIONS))

    def test_coverage_rules(self):
        cases = [
            (lambda s: s["coverage"]["groups"].update(analysed=99), "exceeds detected"),
            (lambda s: s["coverage"].update(not_covered=[]), "not_covered"),
            (lambda s: s["coverage"]["screens"].update(detected="2"), "non-negative integers"),
            (lambda s: s["coverage"]["functions"].update(analysed=1), "differs from the rebuild file"),
        ]
        for mutate, needle in cases:
            s = make_spec(self.rb)
            mutate(s)
            self.assertTrue(any(needle in e for e in self.errs(s)), needle)

    def test_constants_data_model_resources_screens_rules(self):
        cases = [
            (lambda s: s["constants"][0].update(evidence=["gh_missing"]), "gh_missing"),
            (lambda s: s["constants"][0].update(evidence="unknown"), "known value needs evidence"),
            (lambda s: s["constants"][0].pop("value"), "`value` is required"),
            (lambda s: s["data_model"][0]["properties"][0].pop("type"), "`type`"),
            (lambda s: s["resources"][0].update(sha256="zz"), "64-hex"),
            (lambda s: s["resources"][0].update(sha256="99" * 32), "not in the rebuild file with this sha256"),
            (lambda s: s["resources"][0].update(usage=""), "`usage` is required"),
            (lambda s: s["resources"][0].update(evidence=["res_nope"]), "res_nope"),
            (lambda s: s["screens"][0].update(navigation="Home"), "`navigation`"),
            (lambda s: s["screens"][0].update(capture=["wit_nope"]), "wit_nope"),
            (lambda s: s["screens"][0].update(summary="calls _$s7DemoApp5startyyF"), "product language"),
        ]
        for mutate, needle in cases:
            s = make_spec(self.rb)
            mutate(s)
            self.assertTrue(any(needle in e for e in self.errs(s)), (needle, self.errs(s)))

    def test_private_scope_hash_in_a_constant_name_is_not_product_language(self):
        s = make_spec(self.rb)
        s["constants"][0]["name"] = "Demo.(Step in _9BBA4E908B17CF21996F356C5D461E56).prog"
        self.assertTrue(any("not product language" in e for e in self.errs(s)))
        s["constants"][0]["name"] = "Demo.Step.prog"
        self.assertEqual(self.errs(s), [])

    def test_epic_payload_lists_what_is_not_covered(self):
        epic, _ = spec.payloads(make_spec(self.rb), None)
        self.assertIn("## Not covered by the analysis", epic["body"])


if __name__ == "__main__":
    unittest.main()
