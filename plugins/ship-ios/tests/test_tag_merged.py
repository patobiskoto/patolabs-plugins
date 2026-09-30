"""Replay behavior of the one-tag release boundary against a local bare remote."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/tag_merged.py"
SPEC = importlib.util.spec_from_file_location("tag_merged", SCRIPT)
tag_merged = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tag_merged)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        check=True,
    )
    return result.stdout.strip()


class TagMergedTests(unittest.TestCase):
    def test_create_replay_and_resume_local_tag_before_push(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote, repo = root / "remote.git", root / "app"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True,
                           capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True,
                           capture_output=True)
            (repo / "README.md").write_text("first\n", encoding="utf-8")
            _git(repo, "add", "README.md")
            _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "commit", "-m", "first")
            merged = _git(repo, "rev-parse", "HEAD")
            _git(repo, "remote", "add", "origin", str(remote))
            _git(repo, "push", "origin", "main")

            self.assertEqual(tag_merged.publish_tag(repo, "1.0.0", merged), "created")
            self.assertEqual(tag_merged.publish_tag(repo, "1.0.0", merged), "already_pushed")
            self.assertEqual(_git(repo, "ls-remote", "--tags", "origin",
                                  "refs/tags/v1.0.0").split()[0], merged)

            _git(repo, "tag", "v1.0.1", merged)
            self.assertEqual(tag_merged.publish_tag(repo, "1.0.1", merged), "resumed")
            self.assertEqual(tag_merged.publish_tag(repo, "1.0.1", merged), "already_pushed")

            (repo / "README.md").write_text("second\n", encoding="utf-8")
            _git(repo, "add", "README.md")
            _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "commit", "-m", "second")
            other = _git(repo, "rev-parse", "HEAD")
            _git(repo, "tag", "v1.0.2", other)
            with self.assertRaisesRegex(ValueError, "local release tag"):
                tag_merged.publish_tag(repo, "1.0.2", merged)
            _git(remote, "tag", "v1.0.3", merged)
            with self.assertRaisesRegex(ValueError, "remote release tag"):
                tag_merged.publish_tag(repo, "1.0.3", other)


if __name__ == "__main__":
    unittest.main()
