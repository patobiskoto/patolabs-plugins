"""Verification of a draft against the real state (PAT-248).

Each check returns {"status": "ok" | "divergence" | "unknown", ...}. A fact that cannot be
read is "unknown", never "ok".
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

TIMEOUT = 30
FOUNDRY_ENV = "RELAY_FOUNDRY_CLI"
_TICKET = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")


def run(cmd: list[str], cwd: Path) -> tuple[int | None, str]:
    """Run a command; (None, reason) when it cannot be launched or times out."""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT,
                           check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, type(exc).__name__
    return p.returncode, p.stdout


def git_state(root: Path) -> dict[str, Any]:
    rc, branch = run(["git", "symbolic-ref", "--short", "-q", "HEAD"], root)
    branch_v = branch.strip() if rc == 0 else ("(detached)" if rc == 1 else None)
    rc, head = run(["git", "rev-parse", "HEAD"], root)
    head_v = head.strip() if rc == 0 else None
    rc, status = run(["git", "status", "--porcelain"], root)
    dirty = bool(status.strip()) if rc == 0 else None
    return {"branch": branch_v, "head": head_v, "dirty": dirty}


def check_git(draft: dict[str, Any], real: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if "branch" in draft:
        if real["branch"] is None:
            out.append({"check": "branch", "status": "unknown", "detail": "branch unreadable"})
        elif draft["branch"] != real["branch"]:
            out.append({"check": "branch", "status": "divergence",
                        "detail": f"draft says {draft['branch']}, real is {real['branch']}"})
        else:
            out.append({"check": "branch", "status": "ok", "detail": real["branch"]})
    if "commit" in draft:
        head = real["head"]
        if head is None:
            out.append({"check": "commit", "status": "unknown", "detail": "HEAD unreadable"})
        elif not head.startswith(draft["commit"].lower()):
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


def check_files(files: list[str], root: Path) -> list[dict[str, Any]]:
    out = []
    for f in files:
        p = Path(f)
        exists = (p if p.is_absolute() else root / p).exists()
        out.append({"check": f"file {f}", "status": "ok" if exists else "divergence",
                    "detail": "exists" if exists else "does not exist"})
    return out


def locate_foundry() -> tuple[Path | None, str]:
    """RELAY_FOUNDRY_CLI, else the sibling plugin of this checkout; never a guess."""
    env = os.environ.get(FOUNDRY_ENV)
    if env:
        p = Path(env)
        return (p, "env") if p.is_file() else (None, f"{FOUNDRY_ENV} is not a file")
    sibling = Path(__file__).resolve().parents[3] / "foundry" / "tooling" / "foundry_cli.py"
    if sibling.is_file():
        return sibling, "sibling"
    return None, f"{FOUNDRY_ENV} unset and no sibling foundry plugin"


def check_issues(issues: list[str], root: Path) -> list[dict[str, Any]]:
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
        rc, text = run(["python3", str(cli), "query", "issue", iid], root)
        try:
            issue = json.loads(text)["issue"] if rc == 0 else None
        except (ValueError, KeyError, TypeError):
            issue = None
        if not isinstance(issue, dict):
            out.append({"check": label, "status": "unknown",
                        "detail": "Foundry could not read it (not found, tracker unavailable or unbound)"})
        else:
            state = issue.get("state") or "no state"
            pr = f", pr_url {issue['pr_url']}" if issue.get("pr_url") else ""
            out.append({"check": label, "status": "ok", "detail": f"{state}{pr}"})
    return out


def check_prs(prs: list[str], root: Path) -> list[dict[str, Any]]:
    """PR state through `gh pr view <ref> --json state`; no gh or no answer is unknown."""
    out = []
    for ref in prs:
        label = f"pr {ref}"
        if not re.fullmatch(r"#?\d+|https://\S+/pull/\d+", ref):
            out.append({"check": label, "status": "divergence",
                        "detail": "not a PR number or URL"})
            continue
        rc, text = run(["gh", "pr", "view", ref.lstrip("#"), "--json", "state"], root)
        try:
            state = json.loads(text)["state"] if rc == 0 else None
        except (ValueError, KeyError, TypeError):
            state = None
        if not isinstance(state, str):
            out.append({"check": label, "status": "unknown", "detail": "gh could not read it"})
        else:
            out.append({"check": label, "status": "ok", "detail": state})
    return out


def verify(draft: dict[str, Any], root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    real = git_state(root)
    checks = (check_git(draft, real) + check_files(draft["files"], root)
              + check_issues(draft["issues"], root) + check_prs(draft["prs"], root))
    return real, checks
