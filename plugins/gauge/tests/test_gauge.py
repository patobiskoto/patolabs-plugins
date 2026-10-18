"""Offline tests on recorded, anonymised responses. No network, no paid call. Tests that start a process: three run the hook script (two with an empty PATH, one with `kill_live` replaced), two start a `sleep` child and kill it."""
from __future__ import annotations

import io
import json
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tooling"))

import gauge_cli
from gauge import readers
from gauge.render import render

FIX = HERE / "fixtures"
SECRETS = ("ACCOUNT-ID-PLACEHOLDER", "CREDITS-PLACEHOLDER", "TOKEN-PLACEHOLDER",
           "LOGIN-PLACEHOLDER", "REPO", "ORG")


def has_found(text: str) -> bool:
    return any(re.search(rf"\b{s}\b", text) for s in SECRETS)


class _Stdin(io.StringIO):
    """Keeps what was written after close(), as the real pipe is closed on cleanup."""

    def close(self):
        self.sent = self.getvalue()
        super().close()


class FakeProc:
    def __init__(self, lines: str):
        self.stdin = _Stdin()
        self.stdout = io.StringIO(lines)
        self.killed = False

    pid = None

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


class CodexTest(unittest.TestCase):
    def test_reads_windows_and_drops_secrets(self):
        proc = FakeProc((FIX / "codex_rate_limits_response.jsonl").read_text())
        r = readers.read_codex(popen=lambda *a, **k: proc, which=lambda n: "/x/codex")
        self.assertEqual(r.status, "ok")
        self.assertEqual([e["label"] for e in r.entries], ["primary", "secondary"])
        self.assertEqual(r.entries[0]["used_percent"], 42)
        self.assertEqual(r.entries[0]["window_minutes"], 300)
        self.assertTrue(r.entries[0]["resets_at"].startswith("2026-"))
        self.assertFalse(has_found(json.dumps(r.as_dict()) + render([r.as_dict()])))
        self.assertTrue(proc.killed)
        sent = proc.stdin.sent
        self.assertIn("account/rateLimits/read", sent)
        self.assertIn('"method": "initialized"', sent)

    def test_timeout_kills_the_process_group_and_closes_stdin(self):
        class Hanging(FakeProc):
            pid = 4242

            def __init__(self):
                super().__init__("")
                self.stdout = self  # readline blocks until the group is killed

            def __iter__(self):
                return self

            def __next__(self):
                self.group_killed.wait(5)
                raise StopIteration

            group_killed = threading.Event()

        proc = Hanging()
        seen = {}

        def killpg(pid, sig):
            seen["call"] = (pid, sig)
            proc.group_killed.set()

        start = time.monotonic()
        with mock.patch.object(readers.os, "killpg", side_effect=killpg):
            r = readers.read_codex(popen=lambda *a, **k: seen.update(kw=k) or proc,
                                   which=lambda n: "x", timeout=0.05)
        # Without the timer the reader only ends after the 5 s wait of the fake.
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertEqual(r.status, "unknown")
        self.assertEqual(seen["call"], (4242, signal.SIGKILL))
        self.assertTrue(seen["kw"]["start_new_session"])
        self.assertTrue(proc.stdin.closed)

    def test_group_kill_never_targets_an_unusable_pid(self):
        proc = FakeProc("")
        with mock.patch.object(readers.os, "killpg") as killpg:
            readers._kill_group(proc)
        killpg.assert_not_called()
        self.assertTrue(proc.killed)

    def test_missing_executable_is_unknown(self):
        r = readers.read_codex(which=lambda n: None)
        self.assertEqual(r.status, "unknown")
        self.assertEqual(r.entries, [])

    def test_error_and_empty_are_unknown(self):
        for body in ('{"id":2,"error":{"code":-1}}\n', '{"id":2,"result":{}}\n', ""):
            proc = FakeProc(body)
            r = readers.read_codex(popen=lambda *a, p=proc, **k: p, which=lambda n: "x")
            self.assertEqual(r.status, "unknown", body)

    def test_zero_percent_is_a_value_not_unknown(self):
        body = '{"id":2,"result":{"rateLimits":{"primary":{"usedPercent":0,"resetsAt":1790000000}}}}\n'
        r = readers.read_codex(popen=lambda *a, **k: FakeProc(body), which=lambda n: "x")
        self.assertEqual(r.entries[0]["used_percent"], 0)

    def test_missing_percent_is_unknown_not_zero(self):
        body = '{"id":2,"result":{"rateLimits":{"primary":{"resetsAt":1790000000}}}}\n'
        r = readers.read_codex(popen=lambda *a, **k: FakeProc(body), which=lambda n: "x")
        self.assertEqual(r.status, "unknown")


class ClaudeTest(unittest.TestCase):
    def test_last_event_wins(self):
        r = readers.read_claude_streams([str(FIX / "claude_stream.jsonl")])
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.entries[0]["label"], "five_hour")
        self.assertEqual(r.entries[0]["used_percent"], 30.0)
        self.assertEqual(r.entries[1]["used_percent"], 52.0)
        self.assertNotIn("out_of_credits", json.dumps(r.as_dict()))

    def test_no_file_is_unknown(self):
        self.assertEqual(readers.read_claude_streams([]).status, "unknown")
        self.assertEqual(readers.read_claude_streams(["/nonexistent/x"]).status, "unknown")

    def test_stream_without_event_is_unknown(self):
        p = HERE / "fixtures" / "codex_rate_limits_response.jsonl"
        self.assertEqual(readers.read_claude_streams([str(p)]).status, "unknown")

    def test_statusline(self):
        r = readers.read_claude_statusline(str(FIX / "claude_statusline.json"))
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.entries[0]["used_percent"], 23.5)
        self.assertEqual(r.entries[1]["label"], "seven_day")

    def test_statusline_without_rate_limits(self):
        r = readers.read_claude_statusline(str(FIX / "linear_headers.json"))
        self.assertEqual(r.status, "unknown")

    def test_probe_refused_without_budget_and_runs_nothing(self):
        run = mock.Mock()
        for budget in (None, "0", "-1", "abc", "nan", "inf", "-inf"):
            r = readers.read_claude_probe(budget, run=run, which=lambda n: "x")
            self.assertEqual(r.status, "unknown")
        run.assert_not_called()

    def test_probe_with_budget_uses_cap_and_parses(self):
        out = (FIX / "claude_stream.jsonl").read_text()
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, out, ""))
        r = readers.read_claude_probe("0.05", run=run, which=lambda n: "x")
        self.assertEqual(r.status, "ok")
        argv = run.call_args[0][0]
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "0.05")

    def test_cli_never_probes_by_default(self):
        with mock.patch.object(readers, "read_claude_probe") as probe:
            args = mock.Mock(probe_claude=False, claude_statusline_json=None, claude_stream=[],
                             probe_budget_usd=None)
            with mock.patch.object(readers, "read_codex"), \
                    mock.patch.object(readers, "read_linear"), \
                    mock.patch.object(readers, "read_github_actions"):
                gauge_cli.collect(args)
            probe.assert_not_called()


class FakeResponse:
    def __init__(self, headers):
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class LinearTest(unittest.TestCase):
    def headers(self):
        return json.loads((FIX / "linear_headers.json").read_text())

    def test_reads_headers(self):
        r = readers.read_linear({"LINEAR_API_TOKEN": "TOKEN-PLACEHOLDER"},
                                opener=lambda req, timeout: FakeResponse(self.headers()))
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.entries[0]["remaining"], 4990.0)
        self.assertEqual(r.entries[1]["label"], "complexity")
        self.assertTrue(r.entries[0]["resets_at"].startswith("2026-"))
        self.assertFalse(has_found(json.dumps(r.as_dict())))

    def test_token_sent_only_in_header(self):
        seen = {}

        def opener(req, timeout):
            seen["auth"] = req.get_header("Authorization")
            seen["body"] = req.data.decode()
            return FakeResponse(self.headers())

        readers.read_linear({"LINEAR_API_TOKEN": "TOKEN-PLACEHOLDER"}, opener=opener)
        self.assertEqual(seen["auth"], "TOKEN-PLACEHOLDER")
        self.assertNotIn("TOKEN-PLACEHOLDER", seen["body"])

    def test_missing_token_makes_no_request(self):
        opener = mock.Mock()
        r = readers.read_linear({}, opener=opener)
        self.assertEqual(r.status, "unknown")
        opener.assert_not_called()

    def test_429_still_carries_counters(self):
        def opener(req, timeout):
            raise urllib.error.HTTPError("u", 429, "x", self.headers(), None)

        r = readers.read_linear({"LINEAR_API_TOKEN": "t"}, opener=opener)
        self.assertEqual(r.status, "ok")

    def test_network_error_and_no_headers_are_unknown(self):
        def boom(req, timeout):
            raise urllib.error.URLError("down")

        self.assertEqual(readers.read_linear({"LINEAR_API_TOKEN": "t"}, opener=boom).status, "unknown")
        r = readers.read_linear({"LINEAR_API_TOKEN": "t"}, opener=lambda q, timeout: FakeResponse({}))
        self.assertEqual(r.status, "unknown")


class GithubTest(unittest.TestCase):
    def runner(self, usage_rc=0, usage=None):
        body = usage if usage is not None else (FIX / "github_billing_usage.json").read_text()

        def run(argv, **kw):
            if argv[2] == "user":
                return subprocess.CompletedProcess(argv, 0, "LOGIN-PLACEHOLDER\n", "")
            return subprocess.CompletedProcess(argv, usage_rc, body if usage_rc == 0 else "", "")

        return run

    def test_sums_actions_minutes_only(self):
        r = readers.read_github_actions(run=self.runner(), which=lambda n: "x")
        self.assertEqual(r.status, "ok")
        e = r.entries[0]
        self.assertEqual(e["label"], "2026-10")
        self.assertEqual(e["minutes_used"], 150.5)
        self.assertAlmostEqual(e["gross_usd"], 1.204)
        self.assertAlmostEqual(e["net_usd"], 0.304)
        self.assertIsNone(e["included_quota"])
        text = json.dumps(r.as_dict()) + render([r.as_dict()])
        self.assertFalse(has_found(text))
        self.assertIn("included quota unknown", text)

    def test_missing_fields_are_unknown_not_zero(self):
        body = json.dumps({"usageItems": [
            {"product": "actions", "date": "2026-10-01", "unitType": "Minutes", "quantity": 10},
            {"product": "actions", "date": "2026-10-02", "unitType": "Minutes"},
            {"product": "actions", "date": "2026-09-01", "unitType": "Minutes", "quantity": 5,
             "grossAmount": 0.5},
            {"product": "actions", "date": "2026-08-01", "unitType": "Hours", "quantity": 3,
             "netAmount": 0},
            {"product": "actions", "date": "2026-07-01", "unitType": "Minutes", "quantity": 4,
             "netAmount": 0},
        ]})
        r = readers.read_github_actions(run=self.runner(usage=body), which=lambda n: "x")
        by = {e["label"]: e for e in r.entries}
        self.assertEqual(by["2026-10"]["minutes_used"], 10)
        self.assertIsNone(by["2026-10"]["gross_usd"])
        self.assertIsNone(by["2026-10"]["net_usd"])
        self.assertEqual(by["2026-09"]["gross_usd"], 0.5)
        self.assertIsNone(by["2026-09"]["net_usd"])
        self.assertIsNone(by["2026-08"]["minutes_used"])  # unexpected unit: unknown
        self.assertIsNone(by["2026-08"]["net_usd"])  # amounts of non-minute items are not summed
        self.assertEqual(by["2026-07"]["net_usd"], 0)  # a real zero stays a value
        text = render([r.as_dict()])
        self.assertIn("gross unknown USD", text)
        self.assertIn("unknown min", text)

    def test_unreadable_login_is_unknown(self):
        def run(argv, **kw):
            return subprocess.CompletedProcess(argv, 0, "  \n", "")

        r = readers.read_github_actions(run=run, which=lambda n: "x")
        self.assertEqual(r.status, "unknown")
        self.assertIn("login unreadable", r.reason)

    def test_refused_is_unknown(self):
        r = readers.read_github_actions(run=self.runner(usage_rc=1), which=lambda n: "x")
        self.assertEqual(r.status, "unknown")

    def test_no_gh_and_empty_usage_are_unknown(self):
        self.assertEqual(readers.read_github_actions(which=lambda n: None).status, "unknown")
        r = readers.read_github_actions(run=self.runner(usage='{"usageItems":[]}'), which=lambda n: "x")
        self.assertEqual(r.status, "unknown")


    def test_amounts_follow_minutes_items_only(self):
        body = json.dumps({"usageItems": [
            {"product": "actions", "date": "2026-10-01", "unitType": "Minutes", "quantity": 10,
             "grossAmount": 1, "netAmount": 0.5},
            {"product": "actions", "date": "2026-10-01", "unitType": "GigabyteHours", "quantity": 99,
             "grossAmount": 7, "netAmount": 7},
        ]})
        r = readers.read_github_actions(run=self.runner(usage=body), which=lambda n: "x")
        e = r.entries[0]
        self.assertEqual((e["minutes_used"], e["gross_usd"], e["net_usd"]), (10, 1, 0.5))


class NonFiniteTest(unittest.TestCase):
    def test_nan_and_infinity_are_not_numbers(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            self.assertIsNone(readers._num(value))
        for raw in ("nan", "inf", "-inf", "abc"):
            self.assertIsNone(readers._header_number({"h": raw}, "h"))
        self.assertEqual(readers._header_number({"h": "12"}, "h"), 12.0)

    def test_codex_nan_percent_is_unknown_entry(self):
        entries = readers.parse_codex_rate_limits(
            {"rateLimits": {"primary": {"usedPercent": float("nan")}}})
        self.assertIsNone(entries[0]["used_percent"])


class CodexWindowTest(unittest.TestCase):
    def test_present_but_unreadable_secondary_is_kept_unknown_and_absent_is_dropped(self):
        base = {"primary": {"usedPercent": 5, "resetsAt": 1790000000}}
        absent = readers.parse_codex_rate_limits({"rateLimits": dict(base)})
        null = readers.parse_codex_rate_limits({"rateLimits": {**base, "secondary": None}})
        bad = readers.parse_codex_rate_limits(
            {"rateLimits": {**base, "secondary": {"usedPercent": "high"}}})
        self.assertEqual([e["label"] for e in absent], ["primary"])
        self.assertEqual([e["label"] for e in null], ["primary"])
        self.assertEqual([e["label"] for e in bad], ["primary", "secondary"])
        self.assertIsNone(bad[1]["used_percent"])

    def test_unreadable_secondary_warns_inconnu(self):
        from gauge import check
        reading = {"status": "ok", "reason": "", "entries": readers.parse_codex_rate_limits(
            {"rateLimits": {"primary": {"usedPercent": 5},
                            "secondary": {"usedPercent": "high"}}})}
        lines = check.evaluate("codex", reading, 80)
        self.assertEqual(len(lines), 1)
        self.assertIn("codex secondary: inconnu", lines[0])


class LinearRefusalTest(unittest.TestCase):
    def test_non_429_error_with_headers_is_unknown(self):
        headers = json.loads((FIX / "linear_headers.json").read_text())
        closed = []

        class Err(urllib.error.HTTPError):
            def close(self):
                closed.append(self.code)

        for code in (400, 401, 403, 500):
            def opener(req, timeout, code=code):
                raise Err("u", code, "x", headers, None)

            r = readers.read_linear({"LINEAR_API_TOKEN": "TOKEN-PLACEHOLDER"}, opener=opener)
            self.assertEqual(r.status, "unknown", code)
            self.assertEqual(r.entries, [])
            self.assertIn(str(code), r.reason)
            self.assertEqual(closed.count(code), 1, code)

    def test_http_client_exception_is_unknown(self):
        import http.client

        for exc in (http.client.BadStatusLine("x"), http.client.IncompleteRead(b"")):
            self.assertNotIsInstance(exc, OSError)  # only the HTTPException clause can catch it

            def opener(req, timeout, exc=exc):
                raise exc

            r = readers.read_linear({"LINEAR_API_TOKEN": "t"}, opener=opener)
            self.assertEqual(r.status, "unknown", type(exc).__name__)

    def test_probe_passes_the_normalised_budget(self):
        out = (FIX / "claude_stream.jsonl").read_text()
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, out, ""))
        readers.read_claude_probe("1_0", run=run, which=lambda n: "x")
        argv = run.call_args[0][0]
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "10.0")


class LiveProcessTest(unittest.TestCase):
    def test_kill_live_kills_a_running_tracked_child(self):
        outcome = {}

        def worker():
            try:
                done = readers.run_tracked(["sleep", "30"], timeout=60)
                outcome["rc"] = done.returncode
            except Exception as exc:  # noqa: BLE001
                outcome["exc"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        start = time.monotonic()
        thread.start()
        deadline = start + 5
        while not readers._LIVE and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(readers._LIVE)
        readers.kill_live()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - start, 10)
        self.assertNotIn("exc", outcome)
        self.assertEqual(outcome.get("rc"), -signal.SIGKILL)
        self.assertFalse(readers._LIVE)

    def test_run_tracked_timeout_kills_the_child(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            readers.run_tracked(["sleep", "30"], timeout=0.2)
        self.assertFalse(readers._LIVE)


class VersionTest(unittest.TestCase):
    def test_constant_equals_both_manifests_and_changelog(self):
        root = HERE.parent
        for host in (".claude-plugin", ".codex-plugin"):
            doc = json.loads((root / host / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["version"], readers.GAUGE_VERSION, host)
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## {readers.GAUGE_VERSION} — ", changelog)


class RenderTest(unittest.TestCase):
    def test_unknown_shows_reason_not_zero(self):
        text = render([readers.unknown("linear", "src", "why").as_dict()])
        self.assertIn("linear: unknown", text)
        self.assertIn("reason: why", text)
        self.assertNotIn("used 0", text)

    def test_cli_status_json_runs_offline(self):
        # The readers' default dependencies are bound at definition, so patching
        # shutil.which would miss them: replace the readers themselves (no subprocess, no HTTP).
        with mock.patch.object(readers, "read_codex", return_value=readers.unknown("codex", "s", "r")), \
                mock.patch.object(readers, "read_github_actions",
                                  return_value=readers.unknown("github-actions", "s", "r")), \
                mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch("builtins.print") as out:
            self.assertEqual(gauge_cli.main(["status", "--json"]), 0)
        docs = json.loads(out.call_args[0][0])
        self.assertEqual([d["counter"] for d in docs], ["codex", "claude", "linear", "github-actions"])
        self.assertTrue(all(d["status"] == "unknown" for d in docs))


class CheckTests(unittest.TestCase):
    """PAT-247: thresholds, inconnu, hook exit code. Fake readers only."""

    def _cfg(self, **thresholds):
        from gauge import check
        cfg, _ = check.load_config(Path("/nonexistent/gauge.json"))
        cfg["thresholds"].update(thresholds)
        return cfg

    @staticmethod
    def _ok(counter, entries):
        return lambda: readers.Reading(counter, "ok", "fake", "t", entries=entries)

    def test_defaults_documented_in_readme(self):
        from gauge import check
        readme = (HERE.parent / "README.md").read_text(encoding="utf-8")
        for key, value in check.DEFAULT_THRESHOLDS.items():
            self.assertIn(f"| `{key}` | {'none (not monitored)' if value is None else value} |",
                          readme)

    def test_below_threshold_is_silent_and_at_threshold_warns(self):
        from gauge import check
        cfg = self._cfg(claude=None, linear=None, **{"github-actions": None})
        rmap = {"codex": self._ok("codex", [{"label": "primary", "used_percent": 79.9},
                                                {"label": "secondary", "used_percent": 1}])}
        self.assertEqual(check.check(cfg, reader_map=rmap)[0], [])
        rmap = {"codex": self._ok("codex", [{"label": "primary", "used_percent": 80},
                                                {"label": "secondary", "used_percent": 1}])}
        warnings = check.check(cfg, reader_map=rmap)[0]
        self.assertEqual(len(warnings), 1)
        self.assertIn("primary", warnings[0])

    def test_unknown_reading_is_inconnu_never_below(self):
        from gauge import check
        cfg = self._cfg(claude=None, linear=None, **{"github-actions": None})
        rmap = {"codex": lambda: readers.unknown("codex", "fake", "codex executable not found")}
        warnings = check.check(cfg, reader_map=rmap)[0]
        self.assertEqual(len(warnings), 1)
        self.assertIn("inconnu", warnings[0])

    def test_missing_value_and_crashing_reader_are_inconnu(self):
        from gauge import check
        cfg = self._cfg(claude=None, **{"github-actions": None})

        def boom():
            raise RuntimeError("x")

        rmap = {"codex": self._ok("codex", [{"label": "primary", "used_percent": None},
                                                {"label": "secondary", "used_percent": 1}]),
                "linear": boom}
        warnings = check.check(cfg, reader_map=rmap)[0]
        self.assertEqual(len(warnings), 2)
        self.assertTrue(all("inconnu" in w for w in warnings))

    def test_hanging_reader_is_inconnu_within_deadline(self):
        from gauge import check
        cfg = self._cfg(claude=None, linear=None, **{"github-actions": None})
        release = threading.Event()
        rmap = {"codex": lambda: (release.wait(30), None)[1]}
        warnings = check.check(cfg, deadline=0.2, reader_map=rmap)[0]
        release.set()
        self.assertIn("inconnu", warnings[0])

    def test_deadline_bounds_the_total_with_several_hanging_readers(self):
        from gauge import check
        cfg = self._cfg(**{"github-actions": 100})
        release = threading.Event()
        hang = lambda: (release.wait(30), None)[1]
        rmap = {c: hang for c in ("codex", "claude", "linear", "github-actions")}
        start = time.monotonic()
        warnings = check.check(cfg, deadline=0.5, reader_map=rmap)[0]
        elapsed = time.monotonic() - start
        release.set()
        self.assertLess(elapsed, 0.5 + 0.4)
        self.assertEqual(len(warnings), 4)

    def test_missing_expected_window_is_inconnu(self):
        from gauge import check
        cfg = self._cfg(claude=None, linear=None, **{"github-actions": None})
        rmap = {"codex": self._ok("codex", [{"label": "secondary", "used_percent": 10}])}
        warnings = check.check(cfg, reader_map=rmap)[0]
        self.assertEqual(len(warnings), 1)
        self.assertIn("codex primary: inconnu", warnings[0])

    def test_unknown_or_mistyped_config_keys_are_reported(self):
        from gauge import check
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.json"
            path.write_text('{"claude": null, "claude_stream": "one.ndjson"}', encoding="utf-8")
            cfg, problems = check.load_config(path)
        self.assertEqual(cfg["thresholds"], check.DEFAULT_THRESHOLDS)
        self.assertIn("unknown configuration key ignored: claude", problems)
        self.assertIn("claude_stream is not a list of paths: ignored", problems)

    def test_codex_without_secondary_window_is_not_inconnu(self):
        from gauge import check
        cfg = self._cfg(claude=None, linear=None, **{"github-actions": None})
        rmap = {"codex": self._ok("codex", [{"label": "primary", "used_percent": 10}])}
        self.assertEqual(check.check(cfg, reader_map=rmap)[0], [])

    def test_claude_without_source_names_the_config_keys(self):
        from gauge import check
        cfg = self._cfg(codex=None, linear=None, **{"github-actions": None})
        warnings = check.check(cfg)[0]
        self.assertIn("claude_stream", warnings[0])
        self.assertIn("claude_statusline_json", warnings[0])

    def test_hook_error_prints_minimal_message_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"PATH": "", "GAUGE_CONFIG": tmp, "HOME": tmp}  # a directory: not a file
            env["PYTHONPATH"] = ""
            done = subprocess.run(
                [sys.executable, "-c",
                 (f"import sys; sys.path.insert(0, {str(HERE.parent / 'hooks')!r}); "
                  "import session_start as h; import gauge.check as c; c.check = None; h.main()")],
                capture_output=True, text=True, env=env, timeout=30, check=False)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(json.loads(done.stdout), {"systemMessage": "gauge: inconnu (hook error)"})
        self.assertNotIn("Traceback", done.stderr)

    def test_linear_percent_and_github_minutes(self):
        from datetime import datetime, timezone

        from gauge import check
        month = datetime.now(timezone.utc).strftime("%Y-%m")  # evaluate judges this month only
        cfg = self._cfg(codex=None, claude=None, **{"github-actions": 100})
        rmap = {
            "linear": self._ok("linear", [{"label": "requests", "limit": 100, "remaining": 10},
                                           {"label": "complexity", "limit": 100, "remaining": None}]),
            "github-actions": self._ok("github-actions", [{"label": month, "minutes_used": 150}]),
        }
        warnings = check.check(cfg, reader_map=rmap)[0]
        self.assertEqual(len(warnings), 3)
        self.assertIn("90.0%", warnings[0])
        self.assertIn("inconnu", warnings[1])
        self.assertIn("150 min", warnings[2])

    def test_config_file_override_null_and_problems(self):

        from gauge import check
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps({"thresholds": {"codex": 50, "linear": None,
                                                       "bogus": 1, "claude": -3}}))
            cfg, problems = check.load_config(path)
            self.assertEqual(cfg["thresholds"]["codex"], 50)
            self.assertIsNone(cfg["thresholds"]["linear"])
            self.assertEqual(cfg["thresholds"]["claude"], 80)
            self.assertEqual(len(problems), 2)
            path.write_text("{not json")
            cfg, problems = check.load_config(path)
            self.assertEqual(cfg["thresholds"]["codex"], 80)
            self.assertEqual(len(problems), 1)

    def test_config_path_resolution(self):
        from gauge import check
        self.assertEqual(check.config_path({"GAUGE_CONFIG": "/a/b.json"}), Path("/a/b.json"))
        self.assertEqual(check.config_path({"XDG_CONFIG_HOME": "/x"}), Path("/x/gauge/config.json"))

    def test_hook_exits_zero_and_prints_nothing_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "c.json"
            cfg.write_text("{broken")
            env = {"PATH": "", "GAUGE_CONFIG": str(cfg), "HOME": tmp}
            done = subprocess.run([sys.executable, str(HERE.parent / "hooks" / "session_start.py")],
                                  capture_output=True, text=True, env=env, timeout=30, check=False)
        self.assertEqual(done.returncode, 0)
        doc = json.loads(done.stdout)
        self.assertIn("inconnu", doc["systemMessage"])
        self.assertIn("SessionStart", doc["hookSpecificOutput"]["hookEventName"])
        self.assertFalse(has_found(done.stdout + done.stderr))

    def test_github_only_current_month_is_judged(self):
        from datetime import datetime, timezone

        from gauge import check
        now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        entries = [{"label": "2026-09", "minutes_used": 9999},
                   {"label": "2026-08", "minutes_used": None},
                   {"label": "2026-10", "minutes_used": 50}]
        reading = {"status": "ok", "reason": "", "entries": entries}
        self.assertEqual(check.evaluate("github-actions", reading, 100, now), [])
        entries[2]["minutes_used"] = None
        self.assertIn("inconnu", check.evaluate("github-actions", reading, 100, now)[0])
        reading["entries"] = entries[:2]
        self.assertIn("current month 2026-10", check.evaluate("github-actions", reading, 100, now)[0])

    def test_claude_window_past_its_reset_is_stale_inconnu(self):
        from datetime import datetime, timezone

        from gauge import check
        now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
        reading = {"status": "ok", "reason": "", "entries": [
            {"label": "five_hour", "used_percent": 10, "resets_at": "2026-10-18T10:00:00+00:00"},
            {"label": "seven_day", "used_percent": 10, "resets_at": "2026-10-20T10:00:00+00:00"}]}
        lines = check.evaluate("claude", reading, 80, now)
        self.assertEqual(len(lines), 1)
        self.assertIn("claude five_hour: inconnu", lines[0])
        # other counters are fresh reads: a past reset time is not judged
        reading["entries"][0]["label"] = "primary"
        self.assertEqual(check.evaluate("codex", {"status": "ok", "reason": "",
                                                   "entries": [reading["entries"][0]]}, 80, now), [])

    def test_named_config_file_missing_is_reported_default_one_is_silent(self):
        from gauge import check
        missing = Path(tempfile.gettempdir()) / "gauge-absent-config.json"
        self.assertEqual(check.load_config(missing)[1], [])
        problems = check.load_config(missing, explicit=True)[1]
        self.assertEqual(problems, ["configuration file not found: defaults used"])
        self.assertTrue(check.explicit_config({"GAUGE_CONFIG": "x"}))
        self.assertFalse(check.explicit_config({}))

    def test_deeply_nested_config_is_a_problem_not_a_crash(self):
        from gauge import check
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text("[" * 200000, encoding="utf-8")
            cfg, problems = check.load_config(path)
        self.assertEqual(cfg["thresholds"], check.DEFAULT_THRESHOLDS)
        self.assertEqual(len(problems), 1)

    def test_unreadable_config_path_does_not_raise(self):
        from gauge import check
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("no")):
            problems = check.load_config(Path("/x/c.json"))[1]
        self.assertEqual(len(problems), 1)

    def test_non_utf8_config_is_a_problem_not_a_crash(self):
        from gauge import check
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_bytes(b"\xff")
            cfg, problems = check.load_config(path)
            self.assertEqual(cfg["thresholds"], check.DEFAULT_THRESHOLDS)
            self.assertEqual(problems, ["configuration file unreadable or invalid: defaults used"])
            with mock.patch.object(gauge_cli.gcheck, "check", return_value=([], [])), \
                    mock.patch("builtins.print") as out:
                self.assertEqual(gauge_cli.main(["check", "--config", str(path)]), 0)
        printed = out.call_args[0][0]
        self.assertIn("gauge config: configuration file unreadable or invalid", printed)
        self.assertNotIn("internal error", printed)

    def test_cli_check_exits_zero_even_on_an_internal_error(self):
        with mock.patch.object(gauge_cli.gcheck, "load_config", side_effect=PermissionError("no")), \
                mock.patch("builtins.print") as out:
            self.assertEqual(gauge_cli.main(["check"]), 0)
        self.assertIn("inconnu", out.call_args[0][0])

    def test_cli_check_reports_a_missing_named_config(self):
        missing = str(Path(tempfile.gettempdir()) / "gauge-absent-config.json")
        with mock.patch.object(gauge_cli.gcheck, "check", return_value=([], [])), \
                mock.patch("builtins.print") as out:
            self.assertEqual(gauge_cli.main(["check", "--config", missing]), 0)
        self.assertIn("configuration file not found", out.call_args[0][0])

    def test_hook_kills_live_children_before_exiting(self):
        script = (
            f"import sys; sys.path.insert(0, {str(HERE.parent / 'hooks')!r}); "
            f"sys.path.insert(0, {str(HERE.parent / 'tooling')!r}); "
            "import session_start as h; from gauge import readers, check; "
            "calls = []; readers.kill_live = lambda: calls.append(1); "
            "check.check = lambda *a, **k: ([], []); "
            "import os; os._exit = lambda code: (print('killed', len(calls)), sys.exit(0)); "
            "h.main()")
        with tempfile.TemporaryDirectory() as tmp:
            env = {"PATH": "", "HOME": tmp, "GAUGE_CONFIG": str(Path(tmp) / "none.json")}
            done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                  text=True, env=env, timeout=30, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("killed 1", done.stdout)
        self.assertIn("configuration file not found", done.stdout)

    def test_hook_manifest_is_shared_document(self):
        doc = json.loads((HERE.parent / "hooks" / "hooks.json").read_text())
        self.assertIn("SessionStart", doc["hooks"])


if __name__ == "__main__":
    unittest.main()
