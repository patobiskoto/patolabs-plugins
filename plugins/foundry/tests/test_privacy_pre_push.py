"""Real unpublished pushes to disposable bare repositories through the hook."""
import os
from pathlib import Path
import subprocess
import shutil

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SAFE = "1763337+patobiskoto@users.noreply.github.com"
BAD = ("bpdiv@pm.me", "baptiste.prealpato@pm.me")


def git(repo, *args, env=None, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], env=env,
                          capture_output=True, text=True, check=check)


def commit(repo, *, author=SAFE, committer=SAFE, message="fixture"):
    env = os.environ.copy()
    env.update(GIT_AUTHOR_NAME="Pato", GIT_AUTHOR_EMAIL=author,
               GIT_COMMITTER_NAME="Pato", GIT_COMMITTER_EMAIL=committer)
    git(repo, "commit", "--allow-empty", "-m", message, env=env)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture(params=["monorepo", "consumer"])
def repositories(tmp_path, request):
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "-b", "feature", str(local))
    git(local, "config", "user.name", "Pato")
    git(local, "config", "user.email", SAFE)
    git(local, "config", "commit.gpgsign", "false")
    git(local, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    git(local, "remote", "add", "origin", str(remote))
    # Historical leak already published, deliberately retained as evidence.
    commit(local, author=BAD[0])
    git(local, "push", "origin", "feature:main")
    hooks = PLUGIN / ".githooks"
    if request.param == "consumer":
        hooks = local / ".githooks"
        hooks.mkdir()
        shutil.copy2(PLUGIN / ".githooks/pre-push", hooks / "pre-push")
    git(local, "config", "core.hooksPath", str(hooks))
    return local, remote


@pytest.mark.parametrize("email", BAD)
@pytest.mark.parametrize("field", ["author", "committer", "message"])
def test_rejects_new_identity(repositories, email, field):
    local, remote = repositories
    kwargs = {field: email if field != "message" else f"change\n\nCo-authored-by: Pato <{email}>"}
    sha = commit(local, **kwargs)
    result = git(local, "push", "origin", "feature", check=False)
    assert result.returncode != 0
    assert sha in result.stderr
    assert email not in result.stderr
    assert not git(remote, "show-ref", "refs/heads/feature", check=False).stdout


@pytest.mark.parametrize("email", [SAFE, "noreply@github.com", "noreply@anthropic.com"])
def test_allows_legitimate_identity_and_retained_history(repositories, email):
    local, remote = repositories
    sha = commit(local, author=email, committer=email,
                 message=f"fixture\n\nCo-authored-by: Pato <{email}>")
    git(local, "push", "origin", "feature")
    assert git(remote, "rev-parse", "feature").stdout.strip() == sha


def test_update_scans_intermediate_commits_and_force_push(repositories):
    local, _ = repositories
    old_tip = commit(local, message="published feature tip")
    git(local, "push", "origin", "feature")
    git(local, "reset", "--hard", "origin/main")
    commit(local, committer=BAD[1], message="divergent leaked identity")
    new_tip = commit(local, message="clean divergent tip")
    assert git(local, "merge-base", "--is-ancestor", old_tip, new_tip, check=False).returncode == 1
    assert git(local, "push", "--force", "origin", "feature", check=False).returncode != 0


def test_multi_ref_push_rejects_everything(repositories):
    local, remote = repositories
    commit(local)
    git(local, "branch", "clean")
    commit(local, message=BAD[0])
    assert git(local, "push", "origin", "clean", "feature", check=False).returncode != 0
    assert not git(remote, "show-ref", "refs/heads/clean", check=False).stdout


def test_tag_commit_and_annotated_tag_messages(repositories):
    local, _ = repositories
    commit(local, author=BAD[0])
    git(local, "tag", "v-leak")
    assert git(local, "push", "origin", "v-leak", check=False).returncode != 0
    git(local, "reset", "--hard", "origin/main")
    commit(local)
    git(local, "tag", "-a", "v-message", "-m", BAD[1])
    assert git(local, "push", "origin", "v-message", check=False).returncode != 0
    git(local, "tag", "-a", "v-clean", "-m", "release")
    git(local, "push", "origin", "v-clean")


@pytest.mark.parametrize("field", ["tagger", "message"])
@pytest.mark.parametrize("email", BAD)
def test_nested_annotated_tags(repositories, field, email):
    local, _ = repositories
    commit(local, message="nested tag target")
    env = os.environ.copy()
    if field == "tagger":
        env["GIT_COMMITTER_EMAIL"] = email
    git(local, "tag", "-a", "inner", "-m", email if field == "message" else "release", env=env)
    git(local, "tag", "-a", "outer", "inner", "-m", "clean outer release")
    assert git(local, "push", "origin", "outer", check=False).returncode != 0
    git(local, "tag", "-a", "inner-clean", "-m", "safe release")
    git(local, "tag", "-a", "outer-clean", "inner-clean", "-m", "safe outer")
    git(local, "push", "origin", "outer-clean")


def test_deletion_and_default_branch_contract(repositories):
    local, remote = repositories
    commit(local)
    git(local, "push", "origin", "feature")
    git(local, "push", "origin", ":feature")
    assert not git(remote, "show-ref", "refs/heads/feature", check=False).stdout
    assert git(local, "push", "origin", "feature:main", check=False).returncode != 0
    assert git(local, "push", "origin", ":main", check=False).returncode != 0


def test_missing_remote_object_fails_closed(repositories, tmp_path):
    local, remote = repositories
    other = tmp_path / "other"
    git(tmp_path, "clone", "--branch", "main", str(remote), str(other))
    commit(other, message="foreign remote advance")
    git(other, "-c", "core.hooksPath=/dev/null", "push", "origin", "HEAD:foreign")
    commit(local)
    result = git(local, "push", "origin", "feature", check=False)
    assert result.returncode != 0
    assert "fetch destination" in result.stderr
    git(local, "fetch", "origin")
    git(local, "push", "origin", "feature")


def test_new_remote_requires_scanning_ancestry(repositories, tmp_path):
    local, _ = repositories
    empty = tmp_path / "empty.git"
    git(tmp_path, "init", "--bare", str(empty))
    commit(local)
    assert git(local, "push", str(empty), "feature", check=False).returncode != 0


def test_inspection_error_and_malformed_input_fail_closed(repositories):
    local, _ = repositories
    hook = Path(git(local, "config", "core.hooksPath").stdout.strip()) / "pre-push"
    result = subprocess.run([str(hook), "origin", "/nonexistent/privacy-remote"],
                            cwd=local, input="invalid\n", capture_output=True, text=True)
    assert result.returncode == 1
    sha = git(local, "rev-parse", "HEAD").stdout.strip()
    result = subprocess.run([str(hook), "origin", "/nonexistent/privacy-remote"],
                            cwd=local, input=f"refs/heads/feature {sha} refs/heads/feature {'0'*40}\n",
                            capture_output=True, text=True)
    assert result.returncode == 1
