"""`issue openpr` pushes the issue branch under a pinned lease (PAT-273)."""
import subprocess

import pytest

from foundry import issue

BR = "feat/pat-1-x"


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def commit(cwd, name):
    (cwd / name).write_text(name)
    git(cwd, "add", name)
    git(cwd, "commit", "-m", name)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    local = tmp_path / "local"
    git(tmp_path, "clone", str(bare), str(local))
    commit(local, "base")
    git(local, "push", "origin", "main")
    git(local, "checkout", "-b", BR)
    commit(local, "one")
    other = tmp_path / "other"
    git(tmp_path, "clone", str(bare), str(other))
    monkeypatch.chdir(local)
    monkeypatch.setattr(issue, "_default_branch", lambda: "main")
    return bare, local, other


def other_pushes(other, base_ref=None):
    git(other, "fetch", "origin")
    if base_ref:
        git(other, "checkout", "-B", BR, base_ref)
    else:
        git(other, "checkout", "-b", BR)
    commit(other, "theirs")
    git(other, "push", "origin", BR)
    return git(other, "rev-parse", "HEAD")


def test_new_branch_is_pushed_with_an_empty_lease(repos, monkeypatch):
    bare, local, _other = repos
    seen = []
    real = subprocess.run
    monkeypatch.setattr(issue.subprocess, "run",
                        lambda cmd, **kw: (seen.append(cmd), real(cmd, **kw))[1])
    issue._push_branch(BR)
    push = next(c for c in seen if "push" in c)
    assert f"--force-with-lease=refs/heads/{BR}:" in push
    assert git(bare, "rev-parse", BR) == git(local, "rev-parse", "HEAD")


def test_rebased_branch_is_pushed_with_a_pinned_lease(repos, monkeypatch):
    bare, local, _other = repos
    issue._push_branch(BR)
    known = git(local, "rev-parse", f"refs/remotes/origin/{BR}")
    git(local, "checkout", "main")
    commit(local, "main2")
    git(local, "checkout", BR)
    git(local, "rebase", "main")
    seen = []
    real = subprocess.run
    monkeypatch.setattr(issue.subprocess, "run",
                        lambda cmd, **kw: (seen.append(cmd), real(cmd, **kw))[1])
    issue._push_branch(BR)
    push = next(c for c in seen if "push" in c)
    assert f"--force-with-lease=refs/heads/{BR}:{known}" in push
    assert "--force" not in push and "-f" not in push
    assert git(bare, "rev-parse", BR) == git(local, "rev-parse", "HEAD")


def test_remote_moved_by_another_clone_is_refused(repos):
    bare, local, other = repos
    issue._push_branch(BR)
    theirs = other_pushes(other, f"origin/{BR}")
    git(local, "commit", "--amend", "-m", "rewritten")
    with pytest.raises(SystemExit) as exc:
        issue._push_branch(BR)
    assert "rien n'a été écrasé" in str(exc.value)
    assert git(bare, "rev-parse", BR) == theirs


def test_remote_branch_unknown_locally_is_refused(repos):
    bare, _local, other = repos
    theirs = other_pushes(other)
    with pytest.raises(SystemExit) as exc:
        issue._push_branch(BR)
    assert "rien n'a été écrasé" in str(exc.value)
    assert git(bare, "rev-parse", BR) == theirs


def test_default_branch_is_refused_before_any_git_call(repos, monkeypatch):
    monkeypatch.setattr(issue, "_sh", lambda *a, **k: pytest.fail("git called"))
    monkeypatch.setattr(issue.subprocess, "run", lambda *a, **k: pytest.fail("git called"))
    with pytest.raises(SystemExit) as exc:
        issue._push_branch("main")
    assert "branche par défaut" in str(exc.value)
