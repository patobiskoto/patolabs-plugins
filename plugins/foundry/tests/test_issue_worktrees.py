"""Git-branch behavior that differs inside Codex-managed linked worktrees."""
from foundry import issue


def test_prepare_branch_uses_remote_default_in_linked_worktree(monkeypatch):
    calls = []

    def fake_sh(*args, check=True):
        calls.append((args, check))
        if args[-2:] == ("--abbrev-ref", "HEAD"):
            return "codex/generated"
        if args[-2:] == ("--verify", "origin/main"):
            return "abc123"
        return ""

    monkeypatch.setattr(issue, "_sh", fake_sh)
    monkeypatch.setattr(issue, "_default_branch", lambda: "main")
    monkeypatch.setattr(issue, "_is_linked_worktree", lambda: True)

    assert issue._prepare_branch("feat/demo-1-slug") == "linked-worktree"
    assert (("git", "checkout", "-b", "feat/demo-1-slug", "origin/main"), True) in calls
    assert not any(args[:2] == ("git", "checkout") and args[-1] == "main"
                   for args, _check in calls)


def test_prepare_branch_keeps_main_checkout_flow(monkeypatch):
    calls = []

    def fake_sh(*args, check=True):
        calls.append((args, check))
        return "main" if args[-2:] == ("--abbrev-ref", "HEAD") else ""

    monkeypatch.setattr(issue, "_sh", fake_sh)
    monkeypatch.setattr(issue, "_default_branch", lambda: "main")
    monkeypatch.setattr(issue, "_is_linked_worktree", lambda: False)

    assert issue._prepare_branch("feat/demo-1-slug") == "main-checkout"
    assert (("git", "checkout", "main"), True) in calls
    assert (("git", "checkout", "-b", "feat/demo-1-slug"), True) in calls


def test_cleanup_branch_is_delegated_in_linked_worktree(monkeypatch):
    calls = []
    monkeypatch.setattr(issue, "_sh", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(issue, "_is_linked_worktree", lambda: True)

    assert issue._cleanup_branch("feat/demo-1-slug") == "linked-worktree"
    assert calls == []


def test_cleanup_branch_keeps_normal_checkout_flow(monkeypatch):
    calls = []
    monkeypatch.setattr(issue, "_sh", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(issue, "_default_branch", lambda: "main")
    monkeypatch.setattr(issue, "_is_linked_worktree", lambda: False)

    assert issue._cleanup_branch("feat/demo-1-slug") == "main-checkout"
    assert ("git", "checkout", "main") in calls
    assert ("git", "pull", "--ff-only") in calls
    assert ("git", "branch", "-D", "feat/demo-1-slug") in calls
