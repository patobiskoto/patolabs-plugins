"""Where the proof may run, and which commit it proves (PAT-254, AC 4)."""
from __future__ import annotations

from pathlib import Path

from . import proc

TIMEOUT_GIT = 30.0


class Refused(Exception):
    pass


def _git(directory: Path, *args: str) -> tuple[int | None, str]:
    rc, out, _ = proc.run(["git", "-C", str(directory), *args], TIMEOUT_GIT)
    return rc, out.strip()


def guard(project: Path) -> str:
    """Return "linked-worktree" or "outside-git"; raise Refused for a main checkout.

    A main checkout has `--git-dir` equal to `--git-common-dir`; a linked worktree does not.
    Any git failure is refused as unreadable, except the explicit "not a git repository" answer.
    """
    directory = project if project.is_dir() else project.parent
    rc, out, _ = proc.run(["git", "-C", str(directory), "rev-parse", "--path-format=absolute",
                           "--git-dir", "--git-common-dir"], TIMEOUT_GIT)
    if rc is None:
        raise Refused("git cannot be run, so the checkout kind is unknown")
    if rc != 0:
        rc2, _, err2 = proc.run(["git", "-C", str(directory), "rev-parse", "--git-dir"], TIMEOUT_GIT)
        if rc2 is not None and "not a git repository" in err2.lower():
            return "outside-git"
        raise Refused("the checkout kind could not be read from git")
    lines = out.strip().split("\n")
    if len(lines) != 2:
        raise Refused("unexpected git answer, the checkout kind is unknown")
    git_dir, common = (Path(x).resolve() for x in lines)
    if git_dir == common:
        raise Refused("the project is in a main checkout; run the proof in a linked git worktree "
                      "(git worktree add) or in a throwaway directory outside any repository")
    return "linked-worktree"


def describe(project: Path) -> dict:
    """Commit and dirty flag of the project's checkout; "unknown" when there is no git."""
    directory = project if project.is_dir() else project.parent
    rc, commit = _git(directory, "rev-parse", "HEAD")
    if rc != 0 or not commit:
        return {"commit": "unknown", "dirty": "unknown"}
    rc, status = _git(directory, "status", "--porcelain")
    return {"commit": commit, "dirty": bool(status) if rc == 0 else "unknown"}
