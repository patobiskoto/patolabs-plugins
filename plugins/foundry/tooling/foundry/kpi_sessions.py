"""Find the host session logs of an issue for ``routing kpi-export`` (PLP-342, PLP-ADR-0062).

Read only, offline. A log is selected only when it holds an invocation that the local invocation
record (``invocation_log``) carries for one of the issues, and only when it lies in the hosts' log
directories of this repository: Claude ``<projects_dir>/<slug of a checkout path>/*.jsonl`` and
Codex rollouts whose ``session_meta`` ``cwd`` is a checkout path. The checkouts are the worktrees of
the repository (``git worktree list``). An invocation id found in more than one log is ambiguous:
none of those logs is selected for it. Nothing is guessed; nothing found leaves the tokens
``unavailable`` in the export. Only closed-vocabulary identifiers are read (``token_attribution``).
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from foundry import invocation_log, token_attribution

CLAUDE_PROJECTS = "~/.claude/projects"
CODEX_SESSIONS = "~/.codex/sessions"


def repository_roots(root: str | os.PathLike | None) -> list[Path]:
    """Checkout paths of the repository: the worktrees listed by git, else the given root."""
    start = Path(root or Path.cwd()).expanduser().resolve()
    found: list[Path] = []
    try:
        done = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=start,
                              capture_output=True, text=True)
        if done.returncode == 0:
            found = [Path(line[9:]) for line in done.stdout.splitlines() if line.startswith("worktree ")]
    except OSError:
        pass
    return found or [start]


def claude_slug(path: Path) -> str:
    """Directory name Claude Code uses for a project path: every non-alphanumeric becomes ``-``."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _issue_ids(issues, repository: str, state_dir) -> set[str]:
    records = invocation_log.records_by_id(repository=repository, state_dir=state_dir) or {}
    return {i for i, r in records.items() if r.get("issue") in set(issues)}


def _unambiguous(found: dict[str, set[Path]]) -> list[str]:
    """Logs that hold at least one id found in no other log; sorted for a stable result."""
    return sorted({str(next(iter(paths))) for paths in found.values() if len(paths) == 1})


def discover(issues, repository: str, *, roots, claude_projects=None, codex_sessions=None,
             state_dir=None) -> tuple[list[str], list[str]]:
    """(claude main session logs, codex rollout logs) for ``issues``; either list may be empty."""
    wanted = _issue_ids(issues, repository, state_dir)
    if not wanted:
        return [], []
    roots = [Path(r) for r in roots]
    claude: dict[str, set[Path]] = {}
    base = Path(claude_projects or os.path.expanduser(CLAUDE_PROJECTS))
    for slug in sorted({claude_slug(r) for r in roots}):
        directory = base / slug
        if not directory.is_dir():
            continue
        for main in sorted(directory.glob("*.jsonl")):
            subagents = main.parent / main.stem / "subagents"
            for sub in sorted(subagents.rglob("*.jsonl")) if subagents.is_dir() else []:
                found = token_attribution._meta_id(sub)
                if found in wanted:
                    claude.setdefault(found, set()).add(main)
    codex: dict[str, set[Path]] = {}
    sessions = Path(codex_sessions or os.path.expanduser(CODEX_SESSIONS))
    if sessions.is_dir():
        for rollout in sorted(sessions.rglob("rollout-*.jsonl")):
            meta = token_attribution._codex_meta(rollout)
            if not meta or meta.get("execution_id") not in wanted:
                continue
            if _in_roots(_codex_cwd(rollout), roots):
                codex.setdefault(meta["execution_id"], set()).add(rollout)
    return _unambiguous(claude), _unambiguous(codex)


def _codex_cwd(path: Path) -> Path | None:
    try:
        first = next(token_attribution._json_lines(path), None)
    except (OSError, UnicodeDecodeError):
        return None
    payload = first.get("payload") if first and first.get("type") == "session_meta" else None
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    return Path(cwd) if isinstance(cwd, str) and cwd else None


def _in_roots(cwd: Path | None, roots: list[Path]) -> bool:
    return cwd is not None and any(cwd == r or r in cwd.parents for r in roots)
