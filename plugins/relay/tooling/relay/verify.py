"""Verification of a draft against the real state (PAT-248).

Each check returns {"status": "ok" | "divergence" | "unknown", ...}. A fact that cannot be
read is "unknown", never "ok".
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

TIMEOUT = 30  # seconds, per launched command
BUDGET = 120  # seconds, for the whole verification; a check past it is unknown
FOUNDRY_ENV = "RELAY_FOUNDRY_CLI"
EXHAUSTED = "verification time budget exhausted"
_TICKET = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")
_COMMIT = re.compile(r"[0-9A-Fa-f]{4,64}")
_LINE_SUFFIX = re.compile(r"(.+?)(?::\d+){1,2}")


def run(cmd: list[str], cwd: Path, deadline: float | None = None) -> tuple[int | None, str]:
    """Run a command, stdin from /dev/null; (None, reason) when it cannot be launched, times out
    or the deadline (time.monotonic()) has passed."""
    timeout: float = TIMEOUT
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            return None, EXHAUSTED
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, type(exc).__name__
    return p.returncode, p.stdout


def git_state(root: Path, deadline: float | None = None) -> dict[str, Any]:
    rc, branch = run(["git", "symbolic-ref", "--short", "-q", "HEAD"], root, deadline)
    branch_v = branch.strip() if rc == 0 else ("(detached)" if rc == 1 else None)
    rc, head = run(["git", "rev-parse", "HEAD"], root, deadline)
    head_v = head.strip() if rc == 0 else None
    rc, status = run(["git", "status", "--porcelain"], root, deadline)
    dirty = bool(status.strip()) if rc == 0 else None
    return {"branch": branch_v, "head": head_v, "dirty": dirty}


def check_git(draft: dict[str, Any], real: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if real["branch"] is None:
        out.append({"check": "branch", "status": "unknown", "detail": "branch unreadable"})
    elif "branch" not in draft:
        out.append({"check": "branch", "status": "ok",
                    "detail": f"{real['branch']}: read from git, not declared"})
    else:
        if draft["branch"] != real["branch"]:
            out.append({"check": "branch", "status": "divergence",
                        "detail": f"draft says {draft['branch']}, real is {real['branch']}"})
        else:
            out.append({"check": "branch", "status": "ok", "detail": real["branch"]})
    head = real["head"]
    if head is None:
        out.append({"check": "commit", "status": "unknown", "detail": "HEAD unreadable"})
    elif "commit" not in draft:
        out.append({"check": "commit", "status": "ok",
                    "detail": f"{head[:12]}: read from git, not declared"})
    elif not _COMMIT.fullmatch(draft["commit"]):
        out.append({"check": "commit", "status": "divergence",
                    "detail": "malformed: not a commit hash or a prefix of at least 4 hex "
                              "characters"})
    else:
        if not head.startswith(draft["commit"].lower()):
            out.append({"check": "commit", "status": "divergence",
                        "detail": f"draft says {draft['commit']}, real HEAD is {head[:12]}"})
        else:
            out.append({"check": "commit", "status": "ok", "detail": head[:12]})
    if real["dirty"]:
        out.append({"check": "worktree", "status": "divergence",
                    "detail": "uncommitted changes in the working tree"})
    elif real["dirty"] is None:
        out.append({"check": "worktree", "status": "unknown", "detail": "git status unreadable"})
    return out


def _exists(pointer: str, root: Path) -> bool:
    """`~` expanded, relative to root. OSError (other than absence) and RuntimeError propagate."""
    p = Path(pointer).expanduser()
    try:
        os.stat(p if p.is_absolute() else root / p)
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True


def check_files(files: list[str], root: Path,
                deadline: float | None = None) -> list[dict[str, Any]]:
    """A `path:line` or `path:line:col` pointer is ok when the bare path exists."""
    out = []
    for f in files:
        label = f"file {f}"
        if deadline is not None and time.monotonic() >= deadline:
            out.append({"check": label, "status": "unknown", "detail": EXHAUSTED})
            continue
        try:
            exists = _exists(f, root)
            m = None if exists else _LINE_SUFFIX.fullmatch(f)
            if m and _exists(m.group(1), root):
                exists = True
        except (OSError, RuntimeError, ValueError) as exc:
            out.append({"check": label, "status": "unknown",
                        "detail": f"cannot be checked ({type(exc).__name__})"})
            continue
        out.append({"check": label, "status": "ok" if exists else "divergence",
                    "detail": "exists" if exists else "does not exist"})
    return out


_VERSION = re.compile(r"\d+(?:\.\d+)*")
_CLI = ("tooling", "foundry_cli.py")


def locate_foundry(plugin_root: Path | None = None) -> tuple[Path | None, str]:
    """Return (foundry_cli.py or None, how it was found or why not). Never a guess.

    Order: (1) RELAY_FOUNDRY_CLI; (2) checkout layout, `plugins/foundry` next to
    `plugins/relay`; (3) installed-cache layout, `<marketplace>/foundry/<version>/` next to
    `<marketplace>/relay/<version>/`, highest numeric version present (the highest present,
    not proven to be the one the host loads; a directory whose name is not numeric dotted is
    ignored).
    """
    env = os.environ.get(FOUNDRY_ENV)
    if env:
        p = Path(env)
        return (p, FOUNDRY_ENV) if p.is_file() else (None, f"{FOUNDRY_ENV} is not a file")
    root = plugin_root or Path(__file__).resolve().parents[2]
    sibling = root.parent / "foundry" / Path(*_CLI)
    if sibling.is_file():
        return sibling, "checkout layout"
    foundry_dir = root.parent.parent / "foundry"
    versions = []
    if foundry_dir.is_dir():
        for d in foundry_dir.iterdir():
            if _VERSION.fullmatch(d.name) and (d / Path(*_CLI)).is_file():
                versions.append((tuple(int(x) for x in d.name.split(".")), d))
    if versions:
        best = max(versions, key=lambda v: v[0])[1]
        return best / Path(*_CLI), (f"installed cache layout, version {best.name} (highest "
                                    "present, not proven to be the one the host loads)")
    return None, f"{FOUNDRY_ENV} unset and no foundry plugin next to this one"


def check_issues(issues: list[str], root: Path,
                 deadline: float | None = None) -> list[dict[str, Any]]:
    out = []
    cli, why = locate_foundry() if issues else (None, "")
    for iid in issues:
        label = f"issue {iid}"
        if not _TICKET.match(iid):
            out.append({"check": label, "status": "divergence", "detail": "not a ticket identifier"})
            continue
        if cli is None:
            out.append({"check": label, "status": "unknown", "detail": f"Foundry not found: {why}"})
            continue
        rc, text = run(["python3", str(cli), "query", "issue", iid], root, deadline)
        try:
            issue = json.loads(text)["issue"] if rc == 0 else None
        except (ValueError, KeyError, TypeError):
            issue = None
        if text == EXHAUSTED and rc is None:
            out.append({"check": label, "status": "unknown", "detail": EXHAUSTED})
        elif not isinstance(issue, dict):
            out.append({"check": label, "status": "unknown",
                        "detail": "Foundry could not read it (not found, tracker unavailable "
                                  f"or unbound) [Foundry: {cli}, {why}]"})
        else:
            state = issue.get("state") or "no state"
            pr = f", pr_url {issue['pr_url']}" if issue.get("pr_url") else ""
            out.append({"check": label, "status": "ok", "detail": f"{state}{pr} [Foundry: {cli}, {why}]"})
    return out


def check_prs(prs: list[str], root: Path,
              deadline: float | None = None) -> list[dict[str, Any]]:
    """PR state through `gh pr view <ref> --json state`; no gh or no answer is unknown."""
    out = []
    for ref in prs:
        label = f"pr {ref}"
        if not re.fullmatch(r"#?\d+|https://\S+/pull/\d+", ref):
            out.append({"check": label, "status": "divergence",
                        "detail": "not a PR number or URL"})
            continue
        rc, text = run(["gh", "pr", "view", ref.lstrip("#"), "--json", "state"], root, deadline)
        try:
            state = json.loads(text)["state"] if rc == 0 else None
        except (ValueError, KeyError, TypeError):
            state = None
        if text == EXHAUSTED and rc is None:
            out.append({"check": label, "status": "unknown", "detail": EXHAUSTED})
        elif not isinstance(state, str):
            out.append({"check": label, "status": "unknown", "detail": "gh could not read it"})
        else:
            out.append({"check": label, "status": "ok", "detail": state})
    return out


def verify(draft: dict[str, Any], root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    deadline = time.monotonic() + BUDGET
    real = git_state(root, deadline)
    checks = (check_git(draft, real) + check_files(draft["files"], root, deadline)
              + check_issues(draft["issues"], root, deadline)
              + check_prs(draft["prs"], root, deadline))
    return real, checks
