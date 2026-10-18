"""Offline tests: fake xcodebuild and simctl, real local git. No simulator, no network, no model."""
from __future__ import annotations

import io
import json
import os
import plistlib
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tooling"))

import witness_cli
from witness import checkout, compare, navigation, proc, simulator, web, xcode

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

    def __init__(self, tests=TEST_OK, devices=None, state="Shutdown", fail=None, tests_rc=None):
        self.calls: list[list[str]] = []
        self.tests, self.state, self.fail, self.tests_rc = tests, state, fail, tests_rc
        self.page, self.attachments, self.version = "", ["01_Session"], "Fake Browser 1.2.3"
        self.devices = devices if devices is not None else {
            RT: [{"name": "Phone A", "udid": UDID, "isAvailable": True, "state": "Shutdown"}]}

    def __call__(self, cmd, timeout, cwd=None, env=None):
        if cmd[0] == "git":
            return real_run(cmd, timeout, cwd, env)
        self.calls.append(cmd)
        if cmd[0] == "xcodebuild" and "-resultBundlePath" in cmd:
            Path(cmd[cmd.index("-resultBundlePath") + 1]).mkdir(parents=True, exist_ok=True)
        if cmd[0] == "FAKE-BROWSER":
            if cmd[1] == "--version":
                return 0, self.version + "\n", ""
            shot = next(a for a in cmd if a.startswith("--screenshot=")).split("=", 1)[1]
            Path(shot).write_bytes(b"WEB" + cmd[-1].encode() + self.page.encode())
            return (1, "", "browser boom") if self.fail == "browser" else (0, "", "")
        if cmd[0] == "xcrun" and cmd[1] == "xcresulttool":
            work = Path(cmd[cmd.index("--output-path") + 1])
            work.mkdir(parents=True, exist_ok=True)
            for i, n in enumerate(self.attachments):
                (work / f"f{i}.png").write_bytes(b"IMG" + n.encode() + self.page.encode())
            (work / "manifest.json").write_text(json.dumps([{"testIdentifier": "T", "attachments": [
                {"exportedFileName": f"f{i}.png", "suggestedHumanReadableName": n}
                for i, n in enumerate(self.attachments)]}]))
            return 0, "", ""
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
            return (self.tests_rc if self.tests_rc is not None else 0 if "SUCCEEDED" in self.tests else 65), \
                self.tests, ""
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

    def nav_file(self, **fields):
        nav = {"schema": navigation.SCHEMA, "screen": "Session", "mechanism": "deeplink", "url": "demo://session"}
        nav.update(fields)
        path = self.tmp / f"nav-{len(list(self.tmp.glob('nav-*.json')))}.json"
        path.write_text(json.dumps(nav))
        return path

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

    def test_refused_reset_exits_5_and_writes_report(self):
        fake = Fake(state="Booted")
        code, _, _ = self.prove(fake)
        self.assertEqual(code, 5)
        rep = json.loads(next((self.tmp / "out").glob("report-*.json")).read_text())
        self.assertEqual(rep["outcome"], "failed at reset")
        self.assertEqual(rep["steps"][0]["status"], "failed")
        self.assertFalse([c for c in fake.calls if c[0] == "xcodebuild"])

    def test_unknown_tests_result_exits_6(self):
        code, _, _ = self.prove(Fake(tests="no summary\n"))
        self.assertEqual(code, 6)

    def test_rc_zero_without_marker_is_unknown_and_exits_6(self):
        # PAT-262: rc 0 and no marker gives "unknown" (the former test fed rc 65, which is "failed")
        code, _, _ = self.prove(Fake(tests="no summary\n", tests_rc=0))
        self.assertEqual(code, 6)
        rep = json.loads(next((self.tmp / "out").glob("report-before-*.json")).read_text())
        self.assertEqual((rep["tests"]["result"], rep["outcome"]), ("unknown", "captured-with-tests-unknown"))

    def test_markdown_shows_tests_selection(self):
        self.prove(Fake(), extra=("--reset", "--only-testing", "T"))
        md = next((self.tmp / "out").glob("report-before-*.md")).read_text()
        self.assertIn("- Starting state: reset (tests: only-testing)", md)

    def test_out_inside_main_checkout_refused_before_anything_runs(self):
        for out in (self.main / "out", self.main / "deep" / "new"):
            fake = Fake()
            code, _, err = self.run_cli(fake, "prove", "--project", str(self.proj), "--scheme", "Demo",
                                        "--simulator", "Phone A", "--out", str(out))
            self.assertEqual(code, 3, err)
            self.assertIn("--out", err)
            self.assertIn("main checkout", err)
            self.assertEqual(fake.calls, [])
            self.assertFalse(out.exists())
        # a linked worktree or a directory outside git stays accepted
        self.assertEqual(checkout.guard_out(self.wt / "newout"), "linked-worktree")
        self.assertEqual(checkout.guard_out(self.tmp / "elsewhere" / "x"), "outside-git")

    def test_label_restricted_to_file_name_characters(self):
        for bad in ("a/b", "../x", "a b", "", "é"):
            with self.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
                self.prove(Fake(), label=bad)
            self.assertEqual(ctx.exception.code, 2, bad)
        self.assertEqual(self.prove(Fake(), label="Before_1.2-x")[0], 0)

    def test_tests_selection_recorded(self):
        for extra, want in ((("--skip-tests",), "skipped"), ((), "all"), (("--only-testing", "T"), "only-testing"),
                            (("--test-plan", "P"), "test-plan"),
                            (("--test-plan", "P", "--only-testing", "T"), "test-plan+only-testing")):
            label = want.replace("+", "_")
            self.prove(Fake(), label=label, extra=("--reset", *extra))
            rep = json.loads(next((self.tmp / "out").glob(f"report-{label}-*.json")).read_text())
            self.assertEqual(rep["start_state"]["tests_selection"], want)

    def test_reset_and_description_exclusive(self):
        code, _, _ = self.prove(Fake(), extra=("--reset", "--state-description", "d"))
        self.assertEqual(code, 2)


class NavigationTests(Base):
    def test_load_validates(self):
        nav = navigation.load(self.nav_file())
        self.assertEqual((nav["mechanism"], nav["url"]), ("deeplink", "demo://session"))
        self.assertEqual(len(nav["sha256"]), 64)
        for bad in ({"schema": "x"}, {"mechanism": "tap"}, {"url": "demo://a b"}, {"url": "-x"}, {"screen": ""}):
            with self.assertRaises(navigation.NavigationError):
                navigation.load(self.nav_file(**bad))
        with self.assertRaises(navigation.NavigationError):
            navigation.load(self.nav_file(mechanism="uitest", test="a b"))
        junk = self.tmp / "junk.json"
        junk.write_text("[")
        with self.assertRaises(navigation.NavigationError):
            navigation.load(junk)

    def test_deeplink_proof_opens_url_between_launch_and_screenshot(self):
        fake = Fake()
        code, out, _ = self.prove(fake, extra=("--reset", "--skip-tests", "--navigation", str(self.nav_file())))
        self.assertEqual(code, 0, out)
        rep = json.loads(next((self.tmp / "out").glob("report-before-*.json")).read_text())
        self.assertEqual(rep["platform"], "ios")
        self.assertEqual(rep["navigation"]["screen"], "Session")
        names = [s["name"] for s in rep["steps"]]
        self.assertLess(names.index("launch"), names.index("deeplink"))
        self.assertLess(names.index("deeplink"), names.index("screenshot"))
        verbs = [c[2] for c in fake.calls if c[0] == "xcrun"]
        self.assertLess(verbs.index("launch"), verbs.index("openurl"))
        self.assertIn("demo://session", next(c for c in fake.calls if c[:3] == ["xcrun", "simctl", "openurl"]))
        self.assertIn("deeplink `demo://session`", next((self.tmp / "out").glob("report-before-*.md")).read_text())

    def uitest_nav(self, **extra):
        return self.nav_file(mechanism="uitest", test="UITests/Flow/testSession", **extra)

    def test_uitest_proof_extracts_attachment(self):
        fake = Fake()
        code, out, _ = self.prove(fake, extra=("--reset", "--navigation", str(self.uitest_nav())))
        self.assertEqual(code, 0, out)
        rep = json.loads(next((self.tmp / "out").glob("report-before-*.json")).read_text())
        self.assertEqual([s["name"] for s in rep["steps"]], ["reset", "boot", "uitest", "extract-screenshot"])
        self.assertEqual(rep["start_state"]["tests_selection"], "navigation-uitest")
        cmd = next(c for c in fake.calls if c[0] == "xcodebuild" and c[-1] == "test")
        self.assertIn("-only-testing:UITests/Flow/testSession", cmd)
        self.assertIn("-resultBundlePath", cmd)
        self.assertTrue(rep["paths"]["screenshot"].endswith(".png"))
        self.assertEqual(len(rep["screenshot_sha256"]), 64)
        self.assertFalse([c for c in fake.calls if c[0] == "xcodebuild" and c[-1] == "build"])

    def test_uitest_failure_still_captures_and_exits_6(self):
        code, _, _ = self.prove(Fake(tests=TEST_KO), extra=("--reset", "--navigation", str(self.uitest_nav())))
        self.assertEqual(code, 6)

    def test_uitest_attachment_selection(self):
        fake = Fake()
        fake.attachments = ["a_one", "b_two"]
        code, _, _ = self.prove(fake, extra=("--reset", "--navigation", str(self.uitest_nav())))
        self.assertEqual(code, 5)
        rep = json.loads(next((self.tmp / "out").glob("report-before-*.json")).read_text())
        self.assertIn("set 'attachment'", rep["steps"][-1]["detail"])
        fake2 = Fake()
        fake2.attachments = ["a_one", "b_two"]
        code, _, _ = self.prove(fake2, label="x", extra=("--reset", "--navigation", str(self.uitest_nav(attachment="b_"))))
        self.assertEqual(code, 0)
        fake3 = Fake()
        fake3.attachments = []
        self.assertEqual(self.prove(fake3, label="y", extra=("--reset", "--navigation", str(self.uitest_nav())))[0], 5)

    def test_uitest_refuses_test_selection_options_and_bad_file(self):
        fake = Fake()
        code, _, _ = self.prove(fake, extra=("--reset", "--skip-tests", "--navigation", str(self.uitest_nav())))
        self.assertEqual(code, 2)
        self.assertEqual(fake.calls, [])
        code, _, _ = self.prove(fake, extra=("--reset", "--navigation", str(self.tmp / "missing.json")))
        self.assertEqual(code, 3)
        self.assertEqual(fake.calls, [])

    def test_compare_requires_same_navigation(self):
        def proof(label, nav):
            extra = ("--reset", "--skip-tests") + (("--navigation", str(nav)) if nav else ())
            self.assertEqual(self.prove(Fake(), label=label, extra=extra)[0], 0)
            return next((self.tmp / "out").glob(f"report-{label}-*.json"))
        n1 = self.nav_file()
        b, a = proof("before", n1), proof("after", n1)
        self.assertEqual(self.run_cli(Fake(), "compare", "--before", str(b), "--after", str(a))[0], 0)
        md = next((self.tmp / "out").glob("compare-*.md")).read_text()
        self.assertIn("Shared navigation: `ios:deeplink:", md)
        other = proof("other", self.nav_file(url="demo://other"))
        code, _, err = self.run_cli(Fake(), "compare", "--before", str(b), "--after", str(other))
        self.assertEqual((code, "navigation differs" in err), (3, True))
        none = proof("none", None)
        self.assertEqual(self.run_cli(Fake(), "compare", "--before", str(b), "--after", str(none))[0], 3)

    def test_report_without_platform_is_ios(self):
        self.prove(Fake(), extra=("--reset", "--skip-tests"))
        path = next((self.tmp / "out").glob("report-before-*.json"))
        data = json.loads(path.read_text())
        for key in ("platform", "navigation"):
            data.pop(key)
        old = self.tmp / "old.json"
        old.write_text(json.dumps(data))
        compare.check(compare.load(old), compare.load(path))


class PathologicalNavigationFileTests(Base):
    """PAT-262: the navigation file is read with a bound and only if it is a regular file."""

    def test_file_beyond_max_bytes_refused(self):
        big = self.tmp / "big.json"
        big.write_text(json.dumps({"schema": navigation.SCHEMA, "screen": "S", "mechanism": "deeplink",
                                   "url": "demo://x", "pad": "x" * navigation.MAX_BYTES}))
        with self.assertRaises(navigation.NavigationError) as ctx:
            navigation.load(big)
        self.assertIn("larger than", str(ctx.exception))
        exact = self.tmp / "exact.json"  # a file of exactly MAX_BYTES stays accepted
        body = json.dumps({"schema": navigation.SCHEMA, "screen": "S", "mechanism": "deeplink", "url": "demo://x",
                           "pad": ""})
        exact.write_text(json.dumps({"schema": navigation.SCHEMA, "screen": "S", "mechanism": "deeplink",
                                     "url": "demo://x", "pad": "x" * (navigation.MAX_BYTES - len(body))}))
        self.assertEqual(exact.stat().st_size, navigation.MAX_BYTES)
        self.assertEqual(navigation.load(exact)["url"], "demo://x")

    def test_reads_at_most_max_bytes_plus_one(self):
        big = self.tmp / "big.json"
        big.write_bytes(b" " * (navigation.MAX_BYTES * 4))
        reads = []
        real = open

        class Spy:
            def __init__(self, fh):
                self.fh = fh

            def read(self, n=-1):
                reads.append(n)
                return self.fh.read(n)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("witness.navigation.os.fdopen", lambda *a, **k: Spy(real(*a, **k))), \
                self.assertRaises(navigation.NavigationError):
            navigation.load(big)
        self.assertEqual(reads, [navigation.MAX_BYTES + 1])

    def test_fifo_directory_and_device_refused_without_blocking(self):
        fifo = self.tmp / "nav.fifo"
        os.mkfifo(fifo)
        for path in (fifo, self.tmp, Path("/dev/zero")):
            result = []

            def attempt(path=path, result=result):
                try:
                    navigation.load(path)
                except navigation.NavigationError as exc:
                    result.append(str(exc))

            worker = threading.Thread(target=attempt, daemon=True)  # a regression would block: do not hang the run
            worker.start()
            worker.join(10)
            self.assertFalse(worker.is_alive(), f"{path} blocked the loader")
            self.assertEqual(len(result), 1, path)
            self.assertIn("regular file", result[0])


class WebTests(Base):
    def setUp(self):
        super().setUp()
        self.web = self.wt / "site"
        self.web.mkdir()
        self.browser = self.tmp / "FAKE-BROWSER"
        self.browser.write_text("#!/bin/sh\n")
        self.browser.chmod(0o755)
        env = mock.patch.dict("os.environ", {web.ENV_BROWSER: str(self.browser)})
        env.start()
        self.addCleanup(env.stop)
        self.probe = mock.patch("witness.web.probe", return_value=200)
        self.probe_mock = self.probe.start()
        self.addCleanup(self.probe.stop)

    def fake(self, page=""):
        f = Fake()
        f.page = page

        def call(cmd, timeout, cwd=None, env=None):
            if cmd[0] == str(self.browser):
                cmd = ["FAKE-BROWSER", *cmd[1:]]
            return f(cmd, timeout, cwd, env)
        call.inner = f
        return call

    def prove_web(self, fake, label="before", url="http://localhost:8000/page?x=1", extra=(), project=None):
        return self.run_cli(fake, "web", "--project", str(project or self.web), "--url", url, "--label", label,
                            "--out", str(self.tmp / "out"), *extra)

    def test_web_proof_report_and_command_line(self):
        fake = self.fake()
        code, out, _ = self.prove_web(fake)
        self.assertEqual(code, 0, out)
        rep = json.loads(next((self.tmp / "out").glob("report-before-*.json")).read_text())
        self.assertEqual((rep["platform"], rep["viewport"], rep["navigation"]["path"]),
                         ("web", "1280x800", "/page?x=1"))
        self.assertEqual(rep["browser"]["version"], "Fake Browser 1.2.3")
        self.assertTrue(rep["token_cost"].startswith("unknown"))
        cmd = next(c for c in fake.inner.calls if any(a.startswith("--screenshot=") for a in c))
        self.assertIn("--headless=new", cmd)
        self.assertIn("--window-size=1280,800", cmd)
        profile = next(a for a in cmd if a.startswith("--user-data-dir=")).split("=", 1)[1]
        self.assertFalse(Path(profile).exists())
        self.assertIn("Platform: web", next((self.tmp / "out").glob("report-before-*.md")).read_text())

    def test_non_local_urls_and_bad_viewport_refused_before_running(self):
        for url in ("http://example.com/", "https://localhost/", "http://localhost.evil.com/",
                    "http://user@localhost/", "file:///etc/passwd", "http://[::1]:80/"):
            fake = self.fake()
            with mock.patch("witness.checkout.guard", side_effect=AssertionError("git ran")):
                code, _, _ = self.prove_web(fake, url=url)
            self.assertEqual(code, 3, url)
            self.assertEqual(fake.inner.calls, [])
        for viewport in ("wide", "1280x800x1", "0x0", "123456x800", "1280X800"):
            fake = self.fake()
            with mock.patch("witness.checkout.guard", side_effect=AssertionError("git ran")), \
                    mock.patch("witness.web.locate_browser", side_effect=AssertionError("browser located")):
                code, _, err = self.prove_web(fake, extra=("--viewport", viewport))
            self.assertEqual((code, "viewport" in err), (3, True), viewport)
            self.assertEqual(fake.inner.calls, [])
            self.probe_mock.assert_not_called()
        self.assertEqual(self.prove_web(self.fake(), url="http://127.0.0.1:3000/")[0], 0)

    def test_main_checkout_refused(self):
        self.assertEqual(self.prove_web(self.fake(), project=self.main)[0], 3)

    def test_probe_refuses_silent_or_erroring_server_before_the_browser_runs(self):
        for exc in (web.WebRefused("nothing usable answers"), web.WebRefused("HTTP 404")):
            self.probe_mock.side_effect = exc
            fake = self.fake()
            code, _, err = self.prove_web(fake)
            self.assertEqual(code, 3, err)
            self.assertEqual([c for c in fake.inner.calls if any(a.startswith("--screenshot=") for a in c)], [])
        self.probe_mock.side_effect = None
        self.probe_mock.return_value = 302
        self.assertEqual(self.prove_web(self.fake(), label="redirect")[0], 0)
        rep = json.loads(next((self.tmp / "out").glob("report-redirect-*.json")).read_text())
        self.assertEqual(rep["navigation"]["probe_status"], 302)

    def test_browser_failure_removes_the_profile_and_exits_5(self):
        fake = self.fake()
        fake.inner.fail = "browser"
        code, _, _ = self.prove_web(fake)
        self.assertEqual(code, 5)
        cmd = next(c for c in fake.inner.calls if any(a.startswith("--screenshot=") for a in c))
        profile = next(a for a in cmd if a.startswith("--user-data-dir=")).split("=", 1)[1]
        self.assertFalse(Path(profile).exists())

    def test_profile_left_in_place_is_reported(self):
        fake = self.fake()
        with mock.patch("witness.web.shutil.rmtree"):  # the removal does not happen
            code, _, _ = self.prove_web(fake)
        self.assertEqual(code, 0)
        cmd = next(c for c in fake.inner.calls if any(a.startswith("--screenshot=") for a in c))
        profile = next(a for a in cmd if a.startswith("--user-data-dir=")).split("=", 1)[1]
        self.addCleanup(lambda: __import__("shutil").rmtree(profile, ignore_errors=True))
        rep = json.loads(next((self.tmp / "out").glob("report-before-*.json")).read_text())
        self.assertIn("NOT removed", rep["browser"]["profile"])
        self.assertIn(profile, rep["browser"]["profile"])
        self.assertIn("NOT removed", next((self.tmp / "out").glob("report-before-*.md")).read_text())

    def test_profile_left_in_place_is_reported_on_browser_failure_too(self):
        fake = self.fake()
        fake.inner.fail = "browser"
        with mock.patch("witness.web.shutil.rmtree"):
            code, _, _ = self.prove_web(fake)
        self.assertEqual(code, 5)
        cmd = next(c for c in fake.inner.calls if any(a.startswith("--screenshot=") for a in c))
        profile = next(a for a in cmd if a.startswith("--user-data-dir=")).split("=", 1)[1]
        self.addCleanup(lambda: __import__("shutil").rmtree(profile, ignore_errors=True))
        rep = json.loads(next((self.tmp / "out").glob("report-before-*.json")).read_text())
        self.assertEqual(rep["outcome"], "failed at screenshot")
        self.assertIn("NOT removed", rep["browser"]["profile"])
        self.assertIn(profile, rep["browser"]["profile"])

    def test_markdown_shows_probe_status(self):
        self.probe_mock.return_value = 302
        self.prove_web(self.fake())
        md = next((self.tmp / "out").glob("report-before-*.md")).read_text()
        self.assertIn("- Probe HTTP status before the capture: 302", md)

    def test_compare_out_inside_main_checkout_refused(self):
        self.prove_web(self.fake(), label="a")
        self.prove_web(self.fake(), label="b")
        a, b = (next((self.tmp / "out").glob(f"report-{n}-*.json")) for n in ("a", "b"))
        code, _, err = self.run_cli(Fake(), "compare", "--before", str(a), "--after", str(b),
                                    "--out", str(self.main / "cmp"))
        self.assertEqual((code, "--out" in err), (3, True))
        self.assertFalse((self.main / "cmp").exists())

    def test_out_inside_main_checkout_refused(self):
        fake = self.fake()
        code, _, err = self.prove_web(fake, extra=("--out", str(self.main / "o")))
        self.assertEqual((code, "--out" in err), (3, True))
        self.assertEqual(fake.inner.calls, [])
        self.probe_mock.assert_not_called()

    def test_compare_refuses_unknown_or_different_browser(self):
        def proof(label, version="Fake Browser 1.2.3", browser=None):
            fake = self.fake()
            fake.inner.version = version
            if browser:
                with mock.patch.dict("os.environ", {web.ENV_BROWSER: str(browser)}):
                    self.prove_web(fake, label=label)
            else:
                self.prove_web(fake, label=label)
            return next((self.tmp / "out").glob(f"report-{label}-*.json"))
        # an unreadable --version answer is "unknown" in the report
        u1, u2 = proof("u1", version=""), proof("u2", version="")
        self.assertEqual(json.loads(u1.read_text())["browser"]["version"], "unknown")
        code, _, err = self.run_cli(Fake(), "compare", "--before", str(u1), "--after", str(u2))
        self.assertEqual((code, "unknown" in err), (3, True))
        known = proof("known")
        code, _, err = self.run_cli(Fake(), "compare", "--before", str(known), "--after", str(u2))
        self.assertEqual(code, 3)
        # same version string, another executable
        other = self.tmp / "OTHER-BROWSER"
        other.write_text("#!/bin/sh\n")
        other.chmod(0o755)
        with mock.patch.object(self, "browser", other):  # the fake dispatches on self.browser
            elsewhere = proof("elsewhere", browser=other)
        code, _, err = self.run_cli(Fake(), "compare", "--before", str(known), "--after", str(elsewhere))
        self.assertEqual((code, "browser path differs" in err), (3, True))
        self.assertEqual(self.run_cli(Fake(), "compare", "--before", str(known), "--after", str(proof("again")))[0], 0)

    def test_browser_location(self):
        with mock.patch.dict("os.environ", {web.ENV_BROWSER: str(self.tmp / "nope")}), \
                self.assertRaises(web.WebRefused):
            web.locate_browser()
        with mock.patch.dict("os.environ", {web.ENV_BROWSER: ""}), \
                mock.patch.object(web, "KNOWN_BROWSERS", ()), mock.patch("shutil.which", return_value=None):
            with self.assertRaises(web.WebRefused) as ctx:
                web.locate_browser()
            self.assertIn("Chromium-family", str(ctx.exception))
            self.assertIn("no agent-host browser tool", str(ctx.exception))

    def test_browser_failure_exits_5_with_report(self):
        fake = self.fake()
        fake.inner.fail = "browser"
        code, _, _ = self.prove_web(fake)
        self.assertEqual(code, 5)
        rep = json.loads(next((self.tmp / "out").glob("report-before-*.json")).read_text())
        self.assertEqual(rep["outcome"], "failed at screenshot")

    def test_web_compare(self):
        self.prove_web(self.fake("a"))
        self.prove_web(self.fake("b"), label="after")
        b, a = (next((self.tmp / "out").glob(f"report-{n}-*.json")) for n in ("before", "after"))
        code, out, _ = self.run_cli(Fake(), "compare", "--before", str(b), "--after", str(a))
        self.assertEqual(code, 0, out)
        res = json.loads(next((self.tmp / "out").glob("compare-*.json")).read_text())
        self.assertEqual((res["platform"], res["screenshots_identical"], res["navigation"]),
                         ("web", False, "web:/page?x=1"))
        self.prove_web(self.fake(), label="other", url="http://localhost:8000/else")
        self.prove_web(self.fake(), label="wide", extra=("--viewport", "800x600"))
        for label, why in (("other", "navigation differs"), ("wide", "viewport differs")):
            o = next((self.tmp / "out").glob(f"report-{label}-*.json"))
            code, _, err = self.run_cli(Fake(), "compare", "--before", str(b), "--after", str(o))
            self.assertEqual((code, why in err), (3, True))
        self.prove(Fake(), label="phone", extra=("--reset", "--skip-tests"))
        p = next((self.tmp / "out").glob("report-phone-*.json"))
        code, _, err = self.run_cli(Fake(), "compare", "--before", str(b), "--after", str(p))
        self.assertEqual((code, "platform differs" in err), (3, True))


class GitEnvironmentAndProcessTests(Base):
    """PAT-262: git does not depend on the caller's environment; a timeout kills the whole process group."""

    def test_inherited_git_variables_do_not_orient_the_guard(self):
        loose = self.tmp / "loose"
        loose.mkdir()
        with mock.patch.dict("os.environ", {"GIT_DIR": str(self.main / ".git"),
                                            "GIT_COMMON_DIR": str(self.main / ".git"),
                                            "GIT_WORK_TREE": str(self.main)}):
            self.assertEqual(checkout.guard(loose), "outside-git")
            self.assertIs(checkout.describe(loose)["dirty"], "unknown")
            self.assertEqual(checkout.guard(self.proj), "linked-worktree")

    def test_git_runs_with_the_c_locale(self):
        seen = []

        def spy(cmd, timeout, cwd=None, env=None):
            seen.append(env)
            return real_run(cmd, timeout, cwd, env)

        loose = self.tmp / "loose"
        loose.mkdir()
        with mock.patch.dict("os.environ", {"LC_ALL": "fr_FR.UTF-8", "LANG": "fr_FR.UTF-8", "GIT_DIR": "/nope"}), \
                mock.patch("witness.proc.run", spy):
            self.assertEqual(checkout.guard(loose), "outside-git")
        self.assertTrue(seen)
        for env in seen:
            self.assertEqual(env["LC_ALL"], "C")
            self.assertFalse([k for k in env if k.startswith("GIT_")])

    def test_timeout_kills_grandchildren_too(self):
        pidfile = self.tmp / "grandchild.pid"
        rc, _, err = proc.run(["sh", "-c", f"sleep 60 & echo $! > {pidfile}; wait"], 1.5)
        self.assertEqual((rc, "timed out" in err), (None, True))
        pid = int(pidfile.read_text())
        for _ in range(50):  # the kill is delivered at once; allow the reaping to finish
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            self.fail("the grandchild survived the timeout")

    def test_sigterm_during_a_run_kills_the_group_and_exits_143(self):
        pidfile = self.tmp / "gc.pid"
        timer = threading.Timer(1.0, os.kill, (os.getpid(), signal.SIGTERM))
        self.addCleanup(timer.cancel)
        with witness_cli.terminating_on_signals():
            timer.start()
            with self.assertRaises(SystemExit) as ctx:
                proc.run(["sh", "-c", f"sleep 60 & echo $! > {pidfile}; wait"], 30)
        self.assertEqual(ctx.exception.code, 128 + signal.SIGTERM)
        pid = int(pidfile.read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            self.fail("the grandchild survived SIGTERM")

    def test_handlers_are_restored_after_main(self):
        before = signal.getsignal(signal.SIGTERM)
        with witness_cli.terminating_on_signals():
            self.assertIsNot(signal.getsignal(signal.SIGTERM), before)
        self.assertIs(signal.getsignal(signal.SIGTERM), before)

    def test_kill_group_skips_a_reaped_child(self):
        p = subprocess.Popen(["true"], start_new_session=True)
        p.wait()
        with mock.patch("witness.proc.os.killpg") as killpg:
            proc._kill_group(p)
        killpg.assert_not_called()

    def test_run_still_returns_output_and_launch_errors(self):
        self.assertEqual(proc.run(["sh", "-c", "echo out; echo err >&2; exit 3"], 10), (3, "out\n", "err\n"))
        rc, _, err = proc.run(["/nonexistent-witness-binary"], 10)
        self.assertEqual((rc, "cannot be launched" in err), (None, True))


class ProbeTests(unittest.TestCase):
    """The probe is real here: a throwaway local HTTP server on 127.0.0.1 (loopback only, no network)."""

    def serve(self, status):
        import http.server

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}/p?q=1"

    def test_status_recorded_and_errors_refused(self):
        self.assertEqual(web.probe(self.serve(200)), 200)
        self.assertEqual(web.probe(self.serve(302)), 302)  # redirects are not followed
        with self.assertRaises(web.WebRefused) as ctx:
            web.probe(self.serve(404))
        self.assertIn("404", str(ctx.exception))

    def test_non_ascii_and_space_paths_do_not_crash(self):
        seen = []
        import http.server

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        for path, want in (("/café", "/caf%C3%A9"), ("/a b?x=é y", "/a%20b?x=%C3%A9%20y"), ("/p?q=1&r=%41", "/p?q=1&r=%41")):
            self.assertEqual(web.probe(base + path), 200)
            self.assertEqual(seen[-1], want)

    def test_nothing_listening_is_refused(self):
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with self.assertRaises(web.WebRefused) as ctx:
            web.probe(f"http://127.0.0.1:{port}/")
        self.assertIn("local server", str(ctx.exception))


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

    def test_compare_refuses_different_tests_selection(self):
        b = self.proof("before")
        a = self.proof("after", ("--reset", "--skip-tests"))
        code, _, err = self.run_cli(Fake(), "compare", "--before", str(b), "--after", str(a))
        self.assertEqual(code, 3)
        self.assertIn("tests selection differs", err)
        a2 = self.proof("after2", ("--reset", "--only-testing", "T"))
        code, _, err = self.run_cli(Fake(), "compare", "--before", str(b), "--after", str(a2))
        self.assertEqual(code, 3)
        s1 = self.proof("s1", ("--reset", "--skip-tests"))
        s2 = self.proof("s2", ("--reset", "--skip-tests"))
        self.assertEqual(self.run_cli(Fake(), "compare", "--before", str(s1), "--after", str(s2))[0], 0)

    def test_compare_refuses_other_project(self):
        b = self.proof("before")
        data = json.loads(b.read_text())
        data["project"] = "Other.xcodeproj"
        other = self.tmp / "other-project.json"
        other.write_text(json.dumps(data))
        code, _, err = self.run_cli(Fake(), "compare", "--before", str(b), "--after", str(other))
        self.assertEqual((code, "project differs" in err), (3, True))

    def test_unreadable_fields_exit_3_without_traceback(self):
        b = self.proof("before")
        good = json.loads(b.read_text())
        edits = (lambda d: d.pop("generated_at"), lambda d: d.__setitem__("generated_at", 5),
                 lambda d: d["tests"].pop("result"), lambda d: d["tests"].__setitem__("result", None),
                 lambda d: d["checkout"].pop("dirty"), lambda d: d.pop("paths"),
                 lambda d: d.__setitem__("project", None), lambda d: d["simulator"].pop("name"),
                 lambda d: d.__setitem__("platform", "android"), lambda d: d.__setitem__("schema", "x"),
                 lambda d: d.__setitem__("navigation", {"mechanism": "deeplink"}),
                 lambda d: d.__setitem__("navigation", "x"),
                 lambda d: d.__setitem__("navigation", {"mechanism": 3, "sha256": "ab"}),
                 lambda d: d.__setitem__("navigation", {"mechanism": "deeplink", "sha256": None}))
        for i, edit in enumerate(edits):
            data = json.loads(json.dumps(good))
            edit(data)
            path = self.tmp / f"bad-{i}.json"
            path.write_text(json.dumps(data))
            code, _, err = self.run_cli(Fake(), "compare", "--before", str(path), "--after", str(b))
            self.assertEqual(code, 3, (i, err))
            self.assertIn("not a readable", err)

    def test_schema_check_survives_python_optimize(self):
        b = self.proof("before")
        data = json.loads(b.read_text())
        data["schema"] = "witness-proof.v0"
        bad = self.tmp / "v0.json"
        bad.write_text(json.dumps(data))
        cp = subprocess.run([sys.executable, "-O", str(ROOT / "tooling" / "witness_cli.py"), "compare",
                             "--before", str(bad), "--after", str(b)], capture_output=True, text=True,
                            check=False)
        self.assertEqual((cp.returncode, "not a readable" in cp.stderr), (3, True), cp.stderr)

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
