"""Offline tests of groups, targeted decompilation and spec-to-Foundry (PAT-253): fake REA, no network."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
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
from blueprint import decompile, groups, inventory, paths, registry, setup, spec

ARM64 = struct.pack("<II", 0xFEEDFACF, 0x0100000C) + b"\0" * 24 + b"demo"
SHA = hashlib.sha256(ARM64).hexdigest()


def cli(*argv: str) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with mock.patch.object(sys, "argv", ["blueprint_cli.py", *argv]), redirect_stdout(out), redirect_stderr(err):
        try:
            rc = blueprint_cli.main()
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def ev(n: str, addr: str | None = None) -> dict:
    return {"rea_evidence_id": "ev_" + n, "target_sha256": SHA, "address": addr, "file_offset": None}


def make_inventory() -> dict:
    def sec(items, **kw):
        return {"status": "ok", "reason": None, "items": items, **kw}
    types = [
        {"kind": "objc-class", "name": "_TtC7DemoApp14TimerViewModel", "evidence": ev("t1", "0x10")},
        {"kind": "objc-class", "name": "_TtC7DemoApp14TimerViewModel", "evidence": ev("t2", "0x20")},
        {"kind": "objc-class", "name": "_TtC7DemoApp12TimerManager", "evidence": ev("t3", "0x30")},
        {"kind": "swift-conformance", "name": "TimerManager", "protocol": "TimerScheduling", "module": "unknown",
         "evidence": ev("t4", "0x40")},
        {"kind": "objc-class", "name": "_TtC7DemoApp11AlertSender", "evidence": ev("t5", "0x50")},
        {"kind": "objc-class", "name": "_TtC7DemoApp13SenderService", "evidence": ev("t6", "0x60")},
        {"kind": "objc-class", "name": "_TtC7DemoApp7Profile", "evidence": ev("t7", "0x70")},
        {"kind": "objc-class", "name": "_TtC7DemoAppP33_ABCDEF12ResourceBundleClass", "evidence": ev("t8", "0x80")},
        {"kind": "objc-protocol", "name": "SomeDelegate", "evidence": ev("t9")},
        {"kind": "swift-conformance", "name": "LoneManager", "protocol": "P", "module": "unknown",
         "evidence": ev("t10", "0x90")},
    ]
    strings = [{"value": v, "evidence": ev("s" + str(i), hex(0x100 + i))} for i, v in enumerate([
        "Start the timer now", "Timer finished", "Hello there", "_$s7DemoApp14TimerViewModelC5startyyF",
        "/System/Library/thing", "timerTickCount", "B40@0:8", "Close", "x"])]
    return {"format": inventory.FORMAT,
            "target": {"name": "Demo.app", "sha256": SHA, "arm64_sha256": SHA, "analysed_binary": "Demo"},
            "types": sec(types), "ui_strings": sec(strings)}


class GroupsTest(unittest.TestCase):
    def test_split_swift_class(self):
        self.assertEqual(groups.split_swift_class("_TtC7DemoApp14TimerViewModel"), ("DemoApp", "TimerViewModel"))
        for bad in ("Plain", "_TtC7DemoApp", "_TtC7DemoAppP33_AB5Class", "_TtC7DemoApp3Ab", "_TtC0X1Y"):
            self.assertIsNone(groups.split_swift_class(bad), bad)

    def test_role_longest_suffix_wins(self):
        self.assertEqual(groups.role_of("TimerViewModel"), ("Timer", "screen"))
        self.assertEqual(groups.role_of("TimerView"), ("Timer", "screen"))
        self.assertEqual(groups.role_of("TimerManager"), ("Timer", "feature"))
        self.assertIsNone(groups.role_of("Manager"))
        self.assertIsNone(groups.role_of("Profile"))

    def test_human_string(self):
        for ok in ("Start the timer now", "Close", "Réglages", "[Store] Error: "):
            self.assertTrue(groups.human_string(ok), ok)
        for bad in ("_$s7DemoApp5startyyF", "timerTickCount", "/System/x", "B40@0:8", "x", "A06BreathC0Ov", ""):
            self.assertFalse(groups.human_string(bad), bad)

    def test_groups_merge_and_nothing_is_dropped(self):
        inv = make_inventory()
        doc = groups.build(inv)
        names = {g["name"]: g for g in doc["groups"]}
        self.assertEqual(sorted(names), ["Lone", "Sender", "Timer"])
        timer = names["Timer"]
        self.assertEqual(timer["kind"], "screen")
        self.assertEqual([m["name"] for m in timer["members"]], ["TimerManager", "TimerViewModel"])
        mgr = timer["members"][0]
        self.assertEqual((mgr["module"], mgr["protocols"]), ("DemoApp", ["TimerScheduling"]))
        self.assertEqual(sorted(e["rea_evidence_id"] for e in timer["members"][1]["evidence"]), ["ev_t1", "ev_t2"])
        self.assertEqual(timer["search_hint"], {"module": "DemoApp", "classes": ["TimerManager", "TimerViewModel"]})
        # A type seen only as a conformance has no module: its group's search hint says unknown (asserted below).
        # Every type name of the inventory is in a group or in the ungrouped bucket, with a reason.
        seen = {m["name"] for g in doc["groups"] for m in g["members"]} | {u["name"] for u in doc["ungrouped"]["types"]}
        want = {n if not n.startswith("_TtC") else groups.split_swift_class(n)[1] if groups.split_swift_class(n) else n
                for n in (t["name"] for t in inv["types"]["items"])}
        self.assertEqual(seen, want)
        self.assertTrue(all(u["reason"] for u in doc["ungrouped"]["types"]))
        self.assertEqual(names["Lone"]["search_hint"], {"unknown": "no class of this group has a known module"})

    def test_strings_are_assigned_by_group_name_and_accounted(self):
        inv = make_inventory()
        doc = groups.build(inv)
        timer = next(g for g in doc["groups"] if g["name"] == "Timer")
        self.assertEqual([r["value"] for r in timer["ui_strings"]["items"]], ["Start the timer now", "Timer finished"])
        self.assertEqual(timer["ui_strings"]["items"][0]["evidence"]["rea_evidence_id"], "ev_s0")
        ungrouped = [r["value"] for r in doc["ungrouped"]["ui_strings"]["items"]]
        self.assertEqual(ungrouped, ["Close", "Hello there"])
        a = doc["accounting"]
        self.assertEqual(a["ui_strings_in_groups"] + a["ui_strings_ungrouped"] + a["ui_strings_not_user_facing"],
                         a["ui_strings_in_inventory"])

    def test_deterministic_and_unknown_sections_reported(self):
        inv = make_inventory()
        self.assertEqual(inventory.render(groups.build(inv)), inventory.render(groups.build(make_inventory())))
        inv["ui_strings"] = {"status": "unknown", "reason": "timed out", "items": []}
        doc = groups.build(inv)
        self.assertEqual(doc["unknown_parts"], {"ui_strings": "timed out"})
        with self.assertRaises(groups.GroupsError):
            groups.build({"format": 9})
        bad = make_inventory()
        del bad["types"]
        with self.assertRaises(groups.GroupsError):
            groups.build(bad)


def sym(cls: str, rest: str, mod: str = "DemoApp") -> str:
    return f"_$s{len(mod)}{mod}{len(cls)}{cls}C{rest}"


ROWS = [
    {"address": "0x100", "value": sym("TimerViewModel", "5startyyF")},
    {"address": "0x200", "value": sym("TimerViewModel", "9isRunningSbvg")},
    {"address": "0x300", "value": sym("TimerViewModel", "5pause33_AB12LLyyF")},
    {"address": "0x400", "value": sym("TimerViewModel", "7programACfC")},
    {"address": "0x500", "value": sym("TimerViewModel", "6accessyyF")},
    {"address": "0x600", "value": sym("TimerViewModel", "9isRunningSbvM6$deferL_yyF")},
    {"address": "0x700", "value": "_$s7SwiftUI9LazyStateVy7DemoApp14TimerViewModelCSgGMa"},
    {"address": "0x800", "value": sym("OtherViewModel", "4stopyyF")},
    {"address": "0x100", "value": sym("TimerViewModel", "5startyyF")},
]


class DecompileTest(unittest.TestCase):
    def test_pattern_and_entry_points(self):
        self.assertEqual(decompile.search_pattern("DemoApp", ["A", "BC"]),
                         r"_\$s7DemoApp1AC|_\$s7DemoApp2BCC")
        eps = decompile.entry_points(ROWS, "DemoApp", ["TimerViewModel"])
        self.assertEqual([(e["address"], e["method"]) for e in eps],
                         [("0x100", "start"), ("0x300", "pause"), ("0x400", "program")])
        self.assertTrue(all(e["class"] == "TimerViewModel" for e in eps))

    def _rea(self, calls, *, fail=False, slow=0.0):
        def runner(cmd, timeout, cwd=None, env=None):
            calls.append((cmd, timeout))
            if fail:
                return None, "", f"timed out after {timeout:g}s"
            base = {"evidence_id": "ev_" + cmd[1] + cmd[3 if cmd[1] == "decompile" else 2],
                    "subject": {"digest": {"sha256": SHA}, "local_path": "/nowhere"}}
            if cmd[1] == "search":
                return 0, json.dumps(base | {"evidence_id": "ev_search", "normalized_result": ROWS}), ""
            return 0, json.dumps(base | {"normalized_result": "void f(void) { }"}), ""
        return inventory.Rea(Path("/data"), runner)

    def group(self):
        return next(g for g in groups.build(make_inventory())["groups"] if g["name"] == "Timer")

    def test_run_bounds_count_and_cites_evidence(self):
        calls: list = []
        doc = decompile.run(self._rea(calls), Path("/s"), group=self.group(), entry=None, list_only=False,
                            maximum=2, budget=100)
        self.assertEqual([e["address"] for e in doc["entries"]], ["0x100", "0x300"])
        self.assertEqual(doc["candidates"]["total"], 3)
        self.assertTrue(doc["candidates"]["truncated"])
        self.assertEqual(doc["search_evidence"]["rea_evidence_id"], "ev_search")
        self.assertEqual(doc["entries"][0]["evidence"]["rea_evidence_id"], "ev_decompile0x100")
        self.assertEqual(sum(1 for c, _ in calls if c[1] == "decompile"), 2)
        self.assertNotIn("/nowhere", json.dumps(doc))
        self.assertTrue(all(t <= 300 for c, t in calls if c[1] == "decompile"))
        self.assertEqual([t for c, t in calls if c[1] == "search"], [100])  # the search is within --budget

    def test_list_only_and_single_entry(self):
        calls: list = []
        doc = decompile.run(self._rea(calls), Path("/s"), group=self.group(), entry=None, list_only=True,
                            maximum=6, budget=100)
        self.assertEqual((doc["entries"], len(calls)), ([], 1))
        calls.clear()
        doc = decompile.run(self._rea(calls), Path("/s"), group=None, entry="0xabc", list_only=False,
                            maximum=6, budget=100)
        self.assertEqual([c[0][1] for c in calls], ["decompile"])
        self.assertEqual(doc["entries"][0]["status"], "ok")

    def test_failure_is_unknown_and_budget_stops(self):
        doc = decompile.run(self._rea([], fail=True), Path("/s"), group=self.group(), entry=None,
                            list_only=False, maximum=6, budget=100)
        self.assertEqual(doc["entries"], [])
        self.assertIn("timed out", doc["unknown_parts"]["entry_points"])
        doc = decompile.run(self._rea([], fail=True), Path("/s"), group=None, entry="0x1", list_only=False,
                            maximum=6, budget=100)
        self.assertEqual(doc["entries"][0]["status"], "unknown")
        ticks = iter([0, 0, 500, 500, 500, 500])
        doc = decompile.run(self._rea([]), Path("/s"), group=self.group(), entry=None, list_only=False,
                            maximum=6, budget=100, clock=lambda: next(ticks))
        self.assertEqual(len(doc["entries"]), 1)
        self.assertIn("budget", doc["unknown_parts"])

    def test_group_without_module_is_unknown(self):
        g = {"name": "G", "search_hint": {"unknown": "no class of this group has a known module"}}
        doc = decompile.run(self._rea([]), Path("/s"), group=g, entry=None, list_only=False, maximum=3, budget=9)
        self.assertEqual(doc["entries"], [])
        self.assertIn("known module", doc["unknown_parts"]["entry_points"])


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.data = self.tmp / "data"
        p = mock.patch.dict(os.environ, {paths.ENV_DATA_DIR: str(self.data)})
        p.start()
        self.addCleanup(p.stop)


class CliDecompileTest(Base):
    def _target(self) -> Path:
        t = self.tmp / "Demo"
        t.write_bytes(ARM64)
        return t

    def _inv_file(self) -> Path:
        p = self.tmp / "inv.json"
        p.write_text(json.dumps(make_inventory()))
        return p

    def test_unregistered_target_is_refused_before_anything_runs(self):
        t = self._target()
        with mock.patch.object(inventory.proc, "run") as run:
            rc, _, err = cli("decompile", str(t), "--inventory", str(self._inv_file()), "--entry", "0x1")
        self.assertEqual(rc, blueprint_cli.EXIT_REFUSED)
        self.assertIn("not in the registry", err)
        run.assert_not_called()
        self.assertFalse(paths.decompilations(self.data).exists())

    def test_unknown_group_and_missing_tools(self):
        t = self._target()
        registry.add(paths.registry_file(self.data), str(t), "r")
        rc, _, err = cli("decompile", str(t), "--inventory", str(self._inv_file()), "--group", "Timer")
        self.assertEqual(rc, blueprint_cli.EXIT_NO_TOOLS)
        with mock.patch.object(setup, "tools_ready", return_value=[]):
            rc, _, err = cli("decompile", str(t), "--inventory", str(self._inv_file()), "--group", "Nope")
        self.assertEqual(rc, blueprint_cli.EXIT_USAGE)
        self.assertIn("Timer", err)

    def test_other_binary_than_the_inventory_is_refused(self):
        t = self._target()
        registry.add(paths.registry_file(self.data), str(t), "r")
        inv = make_inventory()
        inv["target"]["sha256"] = "0" * 64
        f = self.tmp / "other.json"
        f.write_text(json.dumps(inv))
        with mock.patch.object(setup, "tools_ready", return_value=[]), mock.patch.object(inventory.proc, "run") as run:
            rc, _, err = cli("decompile", str(t), "--inventory", str(f), "--entry", "0x1")
        self.assertEqual(rc, blueprint_cli.EXIT_FAILED)
        self.assertIn("sha256 differs", err)
        run.assert_not_called()

    def test_group_run_writes_output(self):
        t = self._target()
        registry.add(paths.registry_file(self.data), str(t), "r")
        calls: list = []

        def runner(cmd, timeout, cwd=None, env=None):
            calls.append(cmd)
            doc = {"evidence_id": "ev_x", "subject": {"digest": {"sha256": SHA}}}
            if cmd[1] == "search":
                return 0, json.dumps(doc | {"normalized_result": ROWS}), ""
            return 0, json.dumps(doc | {"normalized_result": "code"}), ""
        out = self.tmp / "d.json"
        with mock.patch.object(setup, "tools_ready", return_value=[]), mock.patch.object(inventory.proc, "run", runner):
            rc, stdout, _ = cli("decompile", str(t), "--inventory", str(self._inv_file()), "--group", "Timer",
                                "--max", "1", "--output", str(out))
        self.assertEqual(rc, 0, stdout)
        doc = json.loads(out.read_text())
        self.assertEqual([e["address"] for e in doc["entries"]], ["0x100"])
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        self.assertEqual(sum(1 for c in calls if c[1] == "decompile"), 1)


def make_spec() -> dict:
    return {"format": 1, "title": "Rebuild the timer app", "summary": "A breathing timer.",
            "features": [
                {"name": "Guided session", "summary": "The user runs a timed session.",
                 "points": [{"text": "A session can be started, paused and resumed.", "evidence": ["ev_t1", "ev_s0"]},
                            {"text": "The session length is chosen by the user.", "evidence": "unknown"}]},
                {"name": "Reminders", "summary": "Daily reminders.",
                 "points": [{"text": "A reminder is scheduled once a day.", "evidence": ["ev_t3"]}]}]}


class SpecTest(Base):
    known = frozenset({"ev_t1", "ev_s0", "ev_t3"})

    def test_valid_spec(self):
        self.assertEqual(spec.check(make_spec(), self.known), [])

    def test_evidence_ids_collects_from_nested_documents(self):
        self.assertEqual(spec.evidence_ids(make_inventory(), {"search_evidence": {"rea_evidence_id": "ev_z"},
                                                              "a": [{"evidence_id": "ev_y"}]}) & {"ev_t1", "ev_z", "ev_y"},
                         {"ev_t1", "ev_z", "ev_y"})

    def test_refuses_unknown_evidence_id_and_points_without_evidence(self):
        s = make_spec()
        s["features"][0]["points"][0]["evidence"] = ["ev_t1", "ev_missing"]
        s["features"][0]["points"][1].pop("evidence")
        s["features"][1]["points"][0]["evidence"] = []
        errs = spec.check(s, self.known)
        self.assertEqual(len(errs), 3, errs)
        self.assertIn("ev_missing", errs[0])
        self.assertIn("non-empty list of REA evidence ids, or \"unknown\"", errs[1])

    def test_refuses_symbols_and_addresses_in_the_text(self):
        for leak in ("calls _$s7DemoApp5startyyF", "see 0x3df0c", "class _TtC7DemoApp3Foo"):
            s = make_spec()
            s["features"][0]["points"][0]["text"] = leak
            self.assertTrue(any("product language" in e for e in spec.check(s, self.known)), leak)

    def test_refuses_malformed_specs(self):
        self.assertTrue(spec.check([], self.known))
        self.assertTrue(spec.check({"format": 1}, self.known))
        s = make_spec()
        s["features"].append(dict(s["features"][0]))
        self.assertTrue(any("duplicate" in e for e in spec.check(s, self.known)))

    def test_payloads_epic_first_then_tickets_with_acceptance_and_evidence(self):
        epic, tickets = spec.payloads(make_spec(), None)
        self.assertEqual(epic["fields"], {"Type": "Epic", "State": "backlog"})
        self.assertNotIn("parent", tickets[0])
        _, tickets = spec.payloads(make_spec(), "PAT-900")
        body = tickets[0]["body"]
        self.assertEqual(tickets[0]["parent"], "PAT-900")
        self.assertIn("- [ ] A session can be started, paused and resumed.", body)
        self.assertIn("`ev_t1`, `ev_s0`", body)
        ac = body.split("## REA evidence")[0]
        self.assertNotIn("session length", ac)
        self.assertIn("## Unknown (no REA evidence; not acceptance criteria)\n- The session length", body)

    def test_feature_with_only_unknown_points_has_nothing_testable(self):
        s = make_spec()
        s["features"][1]["points"][0]["evidence"] = "unknown"
        with self.assertRaises(spec.SpecError):
            spec.payloads(s, None)

    def _files(self, s=None):
        sp, inv = self.tmp / "spec.json", self.tmp / "inv.json"
        sp.write_text(json.dumps(s or make_spec()))
        inv.write_text(json.dumps(make_inventory() | {"extra": {"rea_evidence_id": "ev_t3"}}))
        return sp, inv

    def test_cli_check(self):
        sp, inv = self._files()
        rc, out, _ = cli("spec", "check", str(sp), str(inv))
        self.assertEqual(rc, 0, out)
        s = make_spec()
        s["features"][0]["points"][0]["evidence"] = ["ev_nope"]
        sp2, _ = self._files(s)
        rc, _, err = cli("spec", "check", str(sp2), str(inv))
        self.assertEqual(rc, blueprint_cli.EXIT_USAGE)
        self.assertIn("ev_nope", err)

    def test_cli_to_foundry_writes_payloads_prints_commands_and_never_calls_anything(self):
        sp, inv = self._files()
        out = self.tmp / "pl"
        with mock.patch.object(inventory.proc, "run") as run, mock.patch("subprocess.run") as sub:
            rc, stdout, _ = cli("spec", "to-foundry", str(sp), str(inv), "--output", str(out))
            self.assertEqual(rc, 0)
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["epic.json"])
            self.assertIn("Nothing was sent to the tracker", stdout)
            self.assertIn("explicit confirmation", stdout)
            self.assertIn("edit create-issue", stdout)
            self.assertIn("--epic-id", stdout)
            rc, stdout, _ = cli("spec", "to-foundry", str(sp), str(inv), "--output", str(out), "--epic-id", "PAT-900")
            self.assertEqual(rc, 0)
            run.assert_not_called()
            sub.assert_not_called()
        names = sorted(p.name for p in out.iterdir())
        self.assertEqual(names, ["epic.json", "ticket-01-guided-session.json", "ticket-02-reminders.json"])
        t = json.loads((out / names[1]).read_text())
        self.assertEqual((t["parent"], t["title"]), ("PAT-900", "Guided session"))
        self.assertEqual(stat.S_IMODE(os.stat(out / names[1]).st_mode), 0o600)
        self.assertEqual(stdout.count("edit create-issue"), 2)

    def test_cli_to_foundry_refuses_an_unsupported_spec_before_writing(self):
        s = make_spec()
        s["features"][0]["points"][0]["evidence"] = ["ev_nope"]
        sp, inv = self._files(s)
        out = self.tmp / "pl"
        rc, _, _ = cli("spec", "to-foundry", str(sp), str(inv), "--output", str(out))
        self.assertEqual(rc, blueprint_cli.EXIT_USAGE)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
