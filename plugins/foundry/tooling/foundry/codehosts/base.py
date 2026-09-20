"""The CodeHost port — the only surface the pipeline knows about a code host.

Everything the execution loop needs from GitHub / GitLab / … lives here, in
normalized terms (PullRequest, Check). The CI merge-gate invariant is enforced by
the write tier on top of `check_runs()` + `commit_statuses()` (its two verdict
sources) and `ci_expected()` (the waiver guard) — implement all three faithfully
or the gate silently loses coverage on your host.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from foundry.models import Check, PullRequest


class CodeHost(ABC):
    name: str = "abstract"

    @abstractmethod
    def resolve_repo(self, cwd: str | None = None) -> str:
        """owner/name for the repo at cwd (from its git remote)."""

    @abstractmethod
    def open_pr(self, repo: str, head: str, base: str,
                title: str, body: str) -> PullRequest:
        ...

    @abstractmethod
    def list_prs(self, repo: str, head: str) -> list[PullRequest]:
        """All PRs for one exact head branch, including closed candidates."""

    @abstractmethod
    def update_pr(self, repo: str, number: int, title: str,
                  body: str) -> PullRequest:
        """Update an existing PR without changing its head or base."""

    @abstractmethod
    def get_pr(self, repo: str, number: int) -> PullRequest:
        ...

    @abstractmethod
    def check_runs(self, repo: str, sha: str) -> list[Check]:
        """Check-runs for a commit — one of the CI merge gate's two sources."""

    @abstractmethod
    def commit_statuses(self, repo: str, sha: str) -> list[Check]:
        """Legacy commit-status results for a commit, normalized to Check — some CIs
        (Jenkins, CircleCI…) report ONLY here. The merge gate reads both sources."""

    @abstractmethod
    def ci_expected(self, repo: str, sha: str) -> bool:
        """Whether CI plausibly exists for this commit even with zero runs/statuses
        visible yet (e.g. a queued check-suite right after a push). Guards the no-CI
        waiver: --allow-no-ci must not slip through the post-push window."""

    @abstractmethod
    def merge_pr(self, repo: str, number: int, method: str = "squash",
                 sha: str | None = None) -> PullRequest:
        """Merge. When `sha` is given, the host MUST refuse if the head has moved past
        it — this closes the race between the CI gate and the merge."""

    @abstractmethod
    def delete_branch(self, repo: str, branch: str) -> None:
        ...
