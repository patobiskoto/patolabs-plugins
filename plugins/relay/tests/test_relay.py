"""Offline tests: a scratch git repo, a fake Foundry CLI and a fake gh on PATH. No network, no model call."""
from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tooling"))

import relay_cli
from relay import secrets, verify

# Built by concatenation so that no literal in this file looks like a real credential.
SECRET_SAMPLES = {
    "github-token": "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4",
    "github-token-gho": "gho" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4",
    "github-token-ghu": "ghu" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4",
    "github-token-ghs": "ghs" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4",
    "github-token-ghr": "ghr" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4",
    "github-fine-grained-token": "github" + "_pat_" + "A1b2C3d4E5f6G7h8I9j0K1l2",
    "anthropic-key": "sk" + "-ant-" + "api03-A1b2C3d4E5f6G7h8",
    "openai-style-key": "sk" + "-" + "A1b2C3d4E5f6G7h8I9j0K1l2",
    "linear-key": "lin" + "_api_" + "A1b2C3d4E5f6G7h8I9j0K1l2",
    "slack-token": "xox" + "b-" + "1234567890-abcdefghij",
    "aws-access-key": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "pem-private-key": "-----BEGIN " + "RSA PRIVATE KEY-----\nabc",
    "jwt": "eyJ" + "hbGciOiJIUzI1" + "." + "eyJ" + "zdWIiOiIxMjM0" + "." + "abcdefghij",
    "credential-assignment": "pass" + "word=" + "hunter2hunter2",
    "credential-assignment-pwd": "pw" + "d=" + "hunter2hunter2",
    "authorization-bearer": "Authorization: Bear" + "er " + "abcdef0123456789",
    "url-credentials": "https" + "://user:hunter2@example.invalid/x",
    "youtrack-permanent-token": "pe" + "rm:" + "cm9vdA==.NDQtMQ==.abcdef012345",
}
# Prefixed names that a bare-word boundary missed (B1); each must be refused by "credential-assignment".
PREFIXED_SAMPLES = {
    "db-password": "DB_PASS" + "WORD=" + "hunter2hunter2",
    "access-token": "access_" + "token=" + "hunter2hunter2",
    "client-secret": "client_" + "secret=" + "hunter2hunter2",
    "refresh-token": "refresh_" + "token: " + "hunter2hunter2",
    "aws-secret-access-key": "AWS_SECRET_" + "ACCESS_KEY=" + "hunter2hunter2",
}


def git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def good(**over):
    d = {"summary": "s", "decisions": ["d"], "blockers": [], "pitfalls": ["p"],
         "files": ["a.txt"], "next_step": "n", "issues": [], "prs": []}
    d.update(over)
    return d


class RelayCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "a.txt").write_text("x")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-q", "-m", "init")
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        # fake gh: PR 7 is OPEN, anything else fails
        gh = self.bin / "gh"
        gh.write_text('#!/bin/sh\nif [ "$3" = "7" ]; then echo \'{"state":"OPEN"}\'; else exit 1; fi\n')
        gh.chmod(0o755)
        self.foundry = Path(self.tmp.name) / "foundry_cli.py"
        self.foundry.write_text(
            "import json,sys\n"
            "i=sys.argv[3]\n"
            "if i=='T-1': print(json.dumps({'issue':{'id':i,'state':'Done','pr_url':'https://x/pull/7'}}))\n"
            "else: sys.exit(1)\n")
        path = f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        patcher = mock.patch.dict(os.environ, {"PATH": path, "RELAY_FOUNDRY_CLI": str(self.foundry)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self.cwd)

    def run_cli(self, draft, *args, stdin=None):
        f = Path(self.tmp.name) / "draft.json"
        f.write_text(draft if isinstance(draft, str) else json.dumps(draft))
        out, err = io.StringIO(), io.StringIO()
        argv = ["relay_cli.py", *args] if args else ["relay_cli.py", "write", "--draft", str(f)]
        with mock.patch.object(sys, "argv", argv), redirect_stdout(out), redirect_stderr(err):
            code = relay_cli.main()
        return code, out.getvalue(), err.getvalue()

    def state(self):
        return json.loads((self.repo / ".git" / "relay" / "state.json").read_text())


class WriteTests(RelayCase):
    def test_write_ok_mode_600_in_git_dir_and_untracked(self):
        code, out, _ = self.run_cli(good(branch="main"))
        self.assertEqual(code, 0, out)
        p = self.repo / ".git" / "relay" / "state.json"
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)
        s = self.state()
        self.assertEqual(s["real"]["branch"], "main")
        self.assertEqual(len(s["real"]["head"]), 40)
        self.assertTrue(all(c["status"] == "ok" for c in s["verification"]))
        self.assertEqual(subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                                        text=True, check=False).stdout, "")

    def test_worktrees_share_the_state(self):
        git(self.repo, "worktree", "add", "-q", "-b", "other", str(Path(self.tmp.name) / "wt"))
        os.chdir(Path(self.tmp.name) / "wt")
        self.assertEqual(self.run_cli(good())[0], 0)
        self.assertTrue((self.repo / ".git" / "relay" / "state.json").is_file())

    def test_overwrite_keeps_mode_600(self):
        self.run_cli(good())
        self.run_cli(good(summary="second"))
        p = self.repo / ".git" / "relay" / "state.json"
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(self.state()["state"]["summary"], "second")

    def test_stdin_draft(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["relay_cli.py", "write", "--draft", "-"]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(good()))), \
                redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(relay_cli.main(), 0)

    def test_malformed_refused_and_nothing_written(self):
        for bad in ("not json", json.dumps(good(extra="x")), json.dumps({"summary": "s"}),
                    json.dumps(good(decisions="no")), json.dumps(good(files=[1])),
                    json.dumps([1])):
            code, _, err = self.run_cli(bad)
            self.assertNotEqual(code, 0, bad)
            self.assertTrue(err.strip())
            self.assertFalse((self.repo / ".git" / "relay" / "state.json").exists())

    def test_outside_git_refused(self):
        os.chdir(self.tmp.name)
        code, _, err = self.run_cli(good())
        self.assertEqual(code, 2)
        self.assertIn("git", err)


class VerifyTests(RelayCase):
    def checks(self, **over):
        code, out, err = self.run_cli(good(**over))
        self.assertEqual(code, 0, err)
        return {c["check"]: c for c in self.state()["verification"]}, out

    def test_branch_and_commit_divergence(self):
        c, out = self.checks(branch="feature", commit="deadbeef")
        self.assertEqual(c["branch"]["status"], "divergence")
        self.assertEqual(c["commit"]["status"], "divergence")
        self.assertIn("divergence", out)

    def test_commit_prefix_ok(self):
        head = self.run_git_head()
        c, _ = self.checks(commit=head[:8])
        self.assertEqual(c["commit"]["status"], "ok")

    def run_git_head(self):
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=False).stdout.strip()

    def test_missing_file_reported(self):
        c, _ = self.checks(files=["a.txt", "gone.txt"])
        self.assertEqual(c["file a.txt"]["status"], "ok")
        self.assertEqual(c["file gone.txt"]["status"], "divergence")

    def test_dirty_worktree_reported(self):
        (self.repo / "a.txt").write_text("changed")
        c, _ = self.checks()
        self.assertEqual(c["worktree"]["status"], "divergence")

    def test_issue_and_pr_through_fakes(self):
        c, _ = self.checks(issues=["T-1", "T-2", "bad id"], prs=["7", "9", "nope"])
        self.assertEqual(c["issue T-1"]["status"], "ok")
        self.assertIn("Done", c["issue T-1"]["detail"])
        self.assertEqual(c["issue T-2"]["status"], "unknown")
        self.assertEqual(c["issue bad id"]["status"], "divergence")
        self.assertEqual(c["pr 7"]["detail"], "OPEN")
        self.assertEqual(c["pr 9"]["status"], "unknown")
        self.assertEqual(c["pr nope"]["status"], "divergence")

    def test_foundry_absent_is_unknown_never_ok(self):
        with mock.patch.dict(os.environ, {"RELAY_FOUNDRY_CLI": str(self.repo / "missing.py")}):
            c, _ = self.checks(issues=["T-1"])
        self.assertEqual(c["issue T-1"]["status"], "unknown")
        self.assertIn("Foundry not found", c["issue T-1"]["detail"])

    def test_gh_absent_is_unknown(self):
        # Minimal PATH: only a directory holding git, never a system dir where gh may be installed.
        minimal = Path(self.tmp.name) / "minimal"
        minimal.mkdir()
        (minimal / "git").symlink_to(shutil.which("git"))
        with mock.patch.dict(os.environ, {"PATH": str(minimal)}):
            c, _ = self.checks(prs=["7"])
        self.assertEqual(c["pr 7"]["status"], "unknown")

    def test_branch_and_commit_checked_when_not_declared(self):
        c, _ = self.checks()
        for k in ("branch", "commit"):
            self.assertEqual(c[k]["status"], "ok")
            self.assertIn("read from git, not declared", c[k]["detail"])

    def test_no_commit_is_unknown_and_show_prints_unknown(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        git(empty, "init", "-q", "-b", "main")
        os.chdir(empty)
        self.repo = empty
        c, _ = self.checks()
        self.assertEqual(c["commit"]["status"], "unknown")
        out = self.run_cli(None, "show")[1]
        self.assertIn("@ unknown", out)
        self.assertNotIn("None", out)

    def test_ticket_check_records_the_foundry_used(self):
        c, _ = self.checks(issues=["T-1"])
        self.assertIn("RELAY_FOUNDRY_CLI", c["issue T-1"]["detail"])
        self.assertIn(str(self.foundry), c["issue T-1"]["detail"])


class LocateFoundryTests(unittest.TestCase):
    """Fake directory trees; RELAY_FOUNDRY_CLI is cleared."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        env = {k: v for k, v in os.environ.items() if k != "RELAY_FOUNDRY_CLI"}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cli(self, *parts):
        p = self.base.joinpath(*parts, "tooling", "foundry_cli.py")
        p.parent.mkdir(parents=True)
        p.write_text("")
        return p

    def test_env_wins_and_must_be_a_file(self):
        mine = self.base / "mine.py"
        mine.write_text("")
        self.cli("plugins", "foundry")
        with mock.patch.dict(os.environ, {"RELAY_FOUNDRY_CLI": str(mine)}):
            self.assertEqual(verify.locate_foundry(self.base / "plugins" / "relay"),
                             (mine, "RELAY_FOUNDRY_CLI"))
        with mock.patch.dict(os.environ, {"RELAY_FOUNDRY_CLI": str(self.base / "nope")}):
            self.assertIsNone(verify.locate_foundry(self.base / "plugins" / "relay")[0])

    def test_checkout_layout(self):
        want = self.cli("plugins", "foundry")
        got, how = verify.locate_foundry(self.base / "plugins" / "relay")
        self.assertEqual((got, how), (want, "checkout layout"))

    def test_installed_cache_layout_picks_highest_numeric_version(self):
        for v in ("1.0.0", "1.5.0", "1.10.0", "1.9.0", "latest"):
            self.cli("cache", "mkt", "foundry", v)
        best = self.base / "cache" / "mkt" / "foundry" / "1.10.0" / "tooling" / "foundry_cli.py"
        (self.base / "cache" / "mkt" / "relay" / "0.1.0").mkdir(parents=True)
        got, how = verify.locate_foundry(self.base / "cache" / "mkt" / "relay" / "0.1.0")
        self.assertEqual(got, best)
        self.assertIn("1.10.0", how)
        self.assertIn("not proven", how)

    def test_cache_version_without_cli_is_ignored(self):
        ok = self.cli("cache", "mkt", "foundry", "1.0.0")
        (self.base / "cache" / "mkt" / "foundry" / "2.0.0").mkdir()
        (self.base / "cache" / "mkt" / "relay" / "0.1.0").mkdir(parents=True)
        got, _ = verify.locate_foundry(self.base / "cache" / "mkt" / "relay" / "0.1.0")
        self.assertEqual(got, ok)

    def test_nothing_found_is_none(self):
        (self.base / "cache" / "mkt" / "relay" / "0.1.0").mkdir(parents=True)
        got, why = verify.locate_foundry(self.base / "cache" / "mkt" / "relay" / "0.1.0")
        self.assertIsNone(got)
        self.assertIn("RELAY_FOUNDRY_CLI", why)


class SecretTests(RelayCase):
    def test_one_test_per_pattern(self):
        for name, sample in SECRET_SAMPLES.items():
            with self.subTest(pattern=name):
                code, out, err = self.run_cli(good(pitfalls=[f"found {sample} here"]))
                self.assertEqual(code, 3)
                self.assertIn(name.split("-gh")[0] if name.startswith("github-token") else name, err)
                self.assertNotIn(sample, out + err)
                self.assertFalse((self.repo / ".git" / "relay" / "state.json").exists())

    def test_all_pattern_names_are_exercised(self):
        names = {n for n, _ in secrets.PATTERNS}
        covered = {n if n in names else "github-token" for n in SECRET_SAMPLES}
        self.assertEqual(names, covered)

    def test_secret_in_any_field_and_key_never_echoed(self):
        s = SECRET_SAMPLES["github-token"]
        for draft in (good(summary=s), good(next_step=s), good(files=[s]), good(**{s: "x"}),
                      good(issues=[s])):
            code, out, err = self.run_cli(draft)
            self.assertEqual(code, 3)
            self.assertNotIn(s, out + err)

    def test_prefixed_credential_names_refused(self):
        for name, sample in PREFIXED_SAMPLES.items():
            with self.subTest(sample=name):
                code, out, err = self.run_cli(good(pitfalls=[f"env {sample}"]))
                self.assertEqual(code, 3)
                self.assertIn("credential-assignment", err)
                self.assertNotIn("hunter2", out + err)

    def test_innocent_text_passes(self):
        code, _, err = self.run_cli(good(summary="the token budget is 1000, password reset flow"))
        self.assertEqual(code, 0, err)

    def test_pwd_with_a_path_is_not_a_credential(self):
        for text in ("pwd: /some/path/here", "PWD=/Users/x/work/dir", "pwd: ~/work/dir"):
            code, _, err = self.run_cli(good(pitfalls=[text]))
            self.assertEqual(code, 0, f"{text}: {err}")

    def test_final_state_is_rescanned(self):
        # A value read from Foundry (here the PR link) is not in the draft: the second scan catches it.
        secret = "https" + "://user:hunter2@example.invalid/pull/7"
        self.foundry.write_text(
            "import json,sys\n"
            f"print(json.dumps({{'issue':{{'id':'T-1','state':'Done','pr_url':{secret!r}}}}}))\n")
        code, out, err = self.run_cli(good(issues=["T-1"]))
        self.assertEqual(code, 3)
        self.assertIn("url-credentials", err)
        self.assertNotIn("hunter2", out + err)
        self.assertFalse((self.repo / ".git" / "relay" / "state.json").exists())


class ShowTests(RelayCase):
    def test_show_before_and_after(self):
        code = self.run_cli(None, "show")[0]
        self.assertEqual(code, 1)
        self.run_cli(good(summary="hello"))
        code, out, _ = self.run_cli(None, "show")
        self.assertEqual(code, 0)
        self.assertIn("hello", out)
        code, out, _ = self.run_cli(None, "show", "--json")
        self.assertEqual(json.loads(out)["state"]["summary"], "hello")

    def test_show_malformed_state(self):
        self.run_cli(good())
        (self.repo / ".git" / "relay" / "state.json").write_text("{")
        self.assertEqual(self.run_cli(None, "show")[0], 1)


if __name__ == "__main__":
    unittest.main()
