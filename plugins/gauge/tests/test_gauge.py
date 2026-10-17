"""Offline tests on recorded, anonymised responses. No network, no subprocess, no paid call."""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
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


class FakeProc:
    def __init__(self, lines: str):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(lines)
        self.killed = False

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
        sent = proc.stdin.getvalue()
        self.assertIn("account/rateLimits/read", sent)
        self.assertIn('"method": "initialized"', sent)

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
        for budget in (None, "0", "-1", "abc"):
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

    def test_refused_is_unknown(self):
        r = readers.read_github_actions(run=self.runner(usage_rc=1), which=lambda n: "x")
        self.assertEqual(r.status, "unknown")

    def test_no_gh_and_empty_usage_are_unknown(self):
        self.assertEqual(readers.read_github_actions(which=lambda n: None).status, "unknown")
        r = readers.read_github_actions(run=self.runner(usage='{"usageItems":[]}'), which=lambda n: "x")
        self.assertEqual(r.status, "unknown")


class RenderTest(unittest.TestCase):
    def test_unknown_shows_reason_not_zero(self):
        text = render([readers.unknown("linear", "src", "why").as_dict()])
        self.assertIn("linear: unknown", text)
        self.assertIn("reason: why", text)
        self.assertNotIn("used 0", text)

    def test_cli_status_json_runs_offline(self):
        with mock.patch.object(readers, "which", create=True), \
                mock.patch("shutil.which", return_value=None), \
                mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch("builtins.print") as out:
            self.assertEqual(gauge_cli.main(["status", "--json"]), 0)
        docs = json.loads(out.call_args[0][0])
        self.assertEqual([d["counter"] for d in docs], ["codex", "claude", "linear", "github-actions"])
        self.assertTrue(all(d["status"] == "unknown" for d in docs))


if __name__ == "__main__":
    unittest.main()
