"""`default_branch()` must answer the whole branch name, slashes included.

A default branch named `codex/fixture-source` was truncated to `fixture-source`,
so `issue start` branched from a commit that does not exist. These tests drive a
real repository rather than a stubbed `subprocess`: a stub would only prove the
stub, and the point is to fail against the truncation.
"""
import subprocess

from foundry import issue, registry


def _repo(tmp_path, default=None):
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True,
                              text=True, check=True).stdout.strip()

    git("init", "-b", "main")
    (root / "demo.txt").write_text("base\n", encoding="utf-8")
    git("add", "demo.txt")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-m", "base")
    if default is not None:
        head = git("rev-parse", "HEAD")
        git("update-ref", f"refs/remotes/origin/{default}", head)
        git("symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{default}")
    return root


def test_default_branch_keeps_a_slashed_name_whole(tmp_path):
    root = _repo(tmp_path, default="codex/fixture-source")

    assert registry.default_branch(str(root)) == "codex/fixture-source"


def test_default_branch_is_unchanged_for_a_simple_name(tmp_path):
    root = _repo(tmp_path, default="main")

    assert registry.default_branch(str(root)) == "main"


def test_default_branch_answers_none_when_origin_head_is_unset(tmp_path):
    root = _repo(tmp_path)

    assert registry.default_branch(str(root)) is None


def test_issue_start_branches_from_the_whole_default_name(tmp_path, monkeypatch):
    """The base handed to git is the real branch, not its last path segment."""
    root = _repo(tmp_path, default="codex/fixture-source")
    monkeypatch.chdir(root)
    calls = []

    def fake_sh(*args, check=True):
        calls.append(args)
        if args[-2:] == ("--abbrev-ref", "HEAD"):
            return "codex/generated"
        if args[-2:] == ("--verify", "origin/codex/fixture-source"):
            return "abc123"
        return ""

    monkeypatch.setattr(issue, "_sh", fake_sh)
    monkeypatch.setattr(issue, "_is_linked_worktree", lambda: True)

    assert issue._prepare_branch("fix/demo-1-slug") == "linked-worktree"
    assert ("git", "checkout", "-b", "fix/demo-1-slug",
            "origin/codex/fixture-source") in calls
    # The truncated name would have produced `origin/fixture-source`.
    assert not any("origin/fixture-source" in arg for args in calls for arg in args)
