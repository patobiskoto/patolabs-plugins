"""Offline tests: fake xcodebuild and simctl, real local git. No simulator, no network, no model."""
from __future__ import annotations

import io
import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tooling"))

import witness_cli
from witness import checkout, compare, proc, simulator, xcode

real_run = proc.run
UDID = "AAAAAAAA-0000-0000-0000-000000000001"
UDID2 = "BBBBBBBB-0000-0000-0000-000000000002"
RT = "com.apple.CoreSimulator.SimRuntime.iOS-27-0"
RT2 = "com.apple.CoreSimulator.SimRuntime.iOS-26-4"
TEST_OK = "Executed 12 tests, with 0 failures (0 unexpected) in 1.0 (1.1) seconds\n** TEST SUCCEEDED **\n"
TEST_KO = "Executed 12 tests, with 2 failures (0 unexpected) in 1.0 (1.1) seconds\n** TEST FAILED **\n"


def git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    *a], check=True, capture_output=True)


class Fake:
    """Dispatches xcodebuild/xcrun calls; git goes through to the real binary."""

    def __init__(self, tests=TEST_OK, devices=None, state="Shutdown", fail=None):
        self.calls: list[list[str]] = []
        self.tests, self.state, self.fail = tests, state, fail
        self.devices = devices if devices is not None else {
            RT: [{"name": "Phone A", "udid": UDID, "isAvailable": True, "state": "Shutdown"}]}

    def __call__(self, cmd, timeout, cwd=None):
        if cmd[0] == "git":
            return real_run(cmd, timeout, cwd)
        self.calls.append(cmd)
        if cmd[0] == "xcodebuild":
            derived = Path(cmd[cmd.index("-derivedDataPath") + 1])
            if cmd[-1] == "build" and self.fail != "build":
                app = derived / "Build/Products/Debug-iphonesimulator/Demo.app"
                app.mkdir(parents=True, exist_ok=True)
                (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.example.demo"}))
            if self.fail == "build" and cmd[-1] == "build":
                return 65, "", "boom"
            if cmd[-1] == "build":
                return 0, "** BUILD SUCCEEDED **", ""
            return (0 if "SUCCEEDED" in self.tests else 65), self.tests, ""
        sub = cmd[2:]
        if sub[:2] == ["list", "devices"]:
            devs = {k: [dict(d, state=self.state if d["udid"] == UDID else d["state"]) for d in v]
                    for k, v in self.devices.items()}
            return 0, json.dumps({"devices": devs}), ""
        if sub[:2] == ["list", "runtimes"]:
            return 0, json.dumps({"runtimes": [{"identifier": RT, "name": "iOS 27.0", "version": "27.0"}]}), ""
        if sub[0] == "boot":
            self.state = "Booted"
        if sub[0] == "io":
            Path(sub[3]).write_bytes(b"PNG" + cmd[-1].encode())
        if self.fail == sub[0]:
            return 1, "", "failed"
        return 0, "", ""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.main = self.tmp / "main"
        self.main.mkdir()
        git(self.main, "init", "-q", "-b", "main")
        (self.main / "App.xcodeproj").mkdir()
        (self.main / "App.xcodeproj" / "project.pbxproj").write_text("x")
        (self.main / "f.txt").write_text("x")
        git(self.main, "add", "-A")
        git(self.main, "commit", "-qm", "init")
        self.wt = self.tmp / "wt"
        git(self.main, "worktree", "add", "-q", str(self.wt), "-b", "feat")
        self.proj = self.wt / "App.xcodeproj"
        self.sleep = mock.patch("witness.prove.time.sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def run_cli(self, fake, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("witness.proc.run", fake), redirect_stdout(out), redirect_stderr(err):
            code = witness_cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def prove(self, fake, label="before", extra=("--reset",), project=None):
        return self.run_cli(fake, "prove", "--project", str(project or self.proj), "--scheme", "Demo",
                            "--simulator", "Phone A", "--label", label, "--out", str(self.tmp / "out"),
                            *extra)


class GuardTests(Base):
    def test_main_checkout_refused_and_nothing_run(self):
        fake = Fake()
        code, _, err = self.prove(fake, project=self.main / "App.xcodeproj")
        self.assertEqual(code, 3)
        self.assertIn("main checkout", err)
        self.assertEqual(fake.calls, [])

    def test_linked_worktree_and_outside_git_accepted(self):
        self.assertEqual(checkout.guard(self.proj), "linked-worktree")
        loose = self.tmp / "loose"
        loose.mkdir()
        self.assertEqual(checkout.guard(loose), "outside-git")

    def test_describe_commit_and_dirty(self):
        d = checkout.describe(self.proj)
        self.assertEqual(len(d["commit"]), 40)
        self.assertIs(d["dirty"], False)
        (self.wt / "new.txt").write_text("y")
        self.assertIs(checkout.describe(self.proj)["dirty"], True)


class SimulatorTests(Base):
    def test_resolve_by_name_with_runtime_label(self):
        with mock.patch("witness.proc.run", Fake()):
            sim = simulator.resolve("Phone A")
        self.assertEqual((sim["udid"], sim["runtime"]), (UDID, "iOS 27.0 (27.0)"))

    def test_unknown_and_ambiguous_names_refused(self):
        devs = {RT: [{"name": "Phone A", "udid": UDID, "isAvailable": True, "state": "Shutdown"}],
                RT2: [{"name": "Phone A", "udid": UDID2, "isAvailable": True, "state": "Shutdown"}]}
        with mock.patch("witness.proc.run", Fake(devices=devs)):
            with self.assertRaises(simulator.SimulatorError) as ctx:
                simulator.resolve("Phone A")
            self.assertIn("--runtime", str(ctx.exception))
            self.assertEqual(simulator.resolve("Phone A", "26.4")["udid"], UDID2)
            with self.assertRaises(simulator.SimulatorError):
                simulator.resolve("Nope")

    def test_erase_refused_when_booted(self):
        fake = Fake(state="Booted")
        with mock.patch("witness.proc.run", fake), self.assertRaises(simulator.SimulatorError):
            simulator.erase(UDID)
        self.assertFalse([c for c in fake.calls if c[2] == "erase"])


class SummaryTests(unittest.TestCase):
    def test_summaries(self):
        self.assertEqual(xcode.summarize(TEST_OK, 0)["executed"], 12)
        ko = xcode.summarize(TEST_KO, 65)
        self.assertEqual((ko["result"], ko["failures"]), ("failed", 2))
        self.assertEqual(xcode.summarize("nothing", 0)["result"], "unknown")


class ProveTests(Base):
    def test_full_proof_writes_dated_report(self):
        fake = Fake()
        code, out, _ = self.prove(fake)
        self.assertEqual(code, 0, out)
        report = json.loads(next((self.tmp / "out").glob("report-before-*.json")).read_text())
        self.assertEqual(report["outcome"], "proved")
        self.assertEqual(report["simulator"]["udid"], UDID)
        self.assertEqual(report["checkout"]["commit"], checkout.describe(self.proj)["commit"])
        self.assertEqual(report["tests"]["executed"], 12)
        self.assertTrue(report["token_cost"].startswith("unknown"))
        self.assertEqual([s["name"] for s in report["steps"]],
                         ["reset", "test", "build", "find-app", "boot", "install", "launch", "settle",
                          "screenshot"])
        self.assertTrue(Path(report["paths"]["screenshot"]).is_file())
        self.assertTrue(next((self.tmp / "out").glob("report-before-*.md")).is_file())
        verbs = [c[2] for c in fake.calls if c[0] == "xcrun"]
        self.assertLess(verbs.index("erase"), verbs.index("boot"))
        self.assertIn(f"id={UDID}", [a for c in fake.calls if c[0] == "xcodebuild" for a in c])

    def test_failing_tests_still_capture_and_exit_6(self):
        code, _, _ = self.prove(Fake(tests=TEST_KO))
        self.assertEqual(code, 6)
        rep = json.loads(next((self.tmp / "out").glob("report-*.json")).read_text())
        self.assertEqual(rep["outcome"], "captured-with-tests-failed")

    def test_build_failure_writes_report_and_exits_5(self):
        code, _, _ = self.prove(Fake(fail="build"))
        self.assertEqual(code, 5)
        rep = json.loads(next((self.tmp / "out").glob("report-*.json")).read_text())
        self.assertEqual(rep["outcome"], "failed at build")
        self.assertNotIn("screenshot", rep["paths"])

    def test_skip_tests_and_only_testing_options(self):
        fake = Fake()
        code, _, _ = self.prove(fake, extra=("--skip-tests", "--state-description", "fresh install"))
        self.assertEqual(code, 0)
        self.assertFalse([c for c in fake.calls if c[0] == "xcodebuild" and c[-1] == "test"])
        fake = Fake()
        self.prove(fake, label="x", extra=("--only-testing", "T/C", "--test-plan", "P", "--reset"))
        test_cmd = next(c for c in fake.calls if c[0] == "xcodebuild" and c[-1] == "test")
        self.assertIn("-only-testing:T/C", test_cmd)
        self.assertIn("P", test_cmd)

    def test_reset_and_description_exclusive(self):
        code, _, _ = self.prove(Fake(), extra=("--reset", "--state-description", "d"))
        self.assertEqual(code, 2)


class CompareTests(Base):
    def proof(self, label, state_extra=("--reset",), tests=TEST_OK):
        code, _, _ = self.prove(Fake(tests=tests), label=label, extra=state_extra)
        self.assertIn(code, (0, 6))
        return next((self.tmp / "out").glob(f"report-{label}-*.json"))

    def test_compare_same_state_writes_report(self):
        b, a = self.proof("before"), self.proof("after")
        code, out, _ = self.run_cli(Fake(), "compare", "--before", str(b), "--after", str(a))
        self.assertEqual(code, 0, out)
        result = json.loads(next((self.tmp / "out").glob("compare-*.json")).read_text())
        self.assertTrue(result["screenshots_identical"] is not None)
        self.assertEqual(result["before"]["commit"], result["after"]["commit"])
        self.assertIn("share one commit", " ".join(result["notes"]))
        self.assertTrue(next((self.tmp / "out").glob("compare-*.md")).is_file())

    def test_compare_refuses_different_or_missing_state(self):
        b = self.proof("before")
        a = self.proof("after", ("--state-description", "d"))
        code, _, err = self.run_cli(Fake(), "compare", "--before", str(b), "--after", str(a))
        self.assertEqual(code, 3)
        self.assertIn("starting state differs", err)
        u = self.proof("unspec", ("--skip-tests",))
        code, _, err = self.run_cli(Fake(), "compare", "--before", str(u), "--after", str(b))
        self.assertEqual(code, 3)

    def test_compare_refuses_other_simulator_and_junk(self):
        b = self.proof("before")
        data = json.loads(b.read_text())
        data["simulator"]["udid"] = UDID2
        other = self.tmp / "other.json"
        other.write_text(json.dumps(data))
        with self.assertRaises(compare.Incomparable):
            compare.check(compare.load(b), compare.load(other))
        junk = self.tmp / "junk.json"
        junk.write_text("{}")
        code, _, _ = self.run_cli(Fake(), "compare", "--before", str(junk), "--after", str(b))
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
