#!/usr/bin/env python3
"""Decide which CI suites a workflow run needs (PAT-142).

The decision is a pure function of the event name and the changed paths, so it
is unit-tested instead of living in workflow expressions. Only the ``foundry``
suite is conditional; ``ship-ios`` and ``catalogue`` always run.

Fail safe: any doubt (unknown event, unreadable diff, empty file list, a path
outside ``plugins/ship-ios/``) runs everything.
"""
from __future__ import annotations

import os
import posixpath
import subprocess
import sys
from typing import NamedTuple

SHIP_IOS_PREFIX = "plugins/ship-ios/"
# A release PR is recognised by the files it changes, not by its title or branch:
# every release so far (foundry 0.9.0, 1.0.0, 1.1.0) bumped a plugin manifest
# version, and a version bump needs a manifest on one of these paths.
RELEASE_MANIFEST_NAMES = (
    "plugin.json",
    "marketplace.json",
)
RELEASE_MANIFEST_DIRS = (".claude-plugin", ".codex-plugin", ".agents/plugins")


class Plan(NamedTuple):
    foundry: bool
    reason: str


def _normalise(path: str) -> str:
    return posixpath.normpath(path.strip())


def is_manifest(path: str) -> bool:
    """True for a plugin manifest or a marketplace catalogue."""
    directory, name = posixpath.split(_normalise(path))
    if name not in RELEASE_MANIFEST_NAMES:
        return False
    return any(
        directory == candidate or directory.endswith("/" + candidate)
        for candidate in RELEASE_MANIFEST_DIRS
    )


def is_release_change(paths: list[str]) -> bool:
    """A change is a release change when it touches any manifest or catalogue."""
    return any(is_manifest(path) for path in paths)


def plan(event: str, changed_paths: list[str] | None) -> Plan:
    """Return the suites to run. ``None`` means the diff was unavailable."""
    if event == "push":
        return Plan(True, "push: full suite")
    if event != "pull_request":
        return Plan(True, f"unknown event {event!r}: full suite")
    if changed_paths is None:
        return Plan(True, "diff unavailable: full suite")
    paths = [_normalise(p) for p in changed_paths if p.strip()]
    if not paths:
        return Plan(True, "empty change list: full suite")
    if is_release_change(paths):
        return Plan(True, "release change (plugin manifest or catalogue touched): full suite")
    if all(p.startswith(SHIP_IOS_PREFIX) and ".." not in p.split("/") for p in paths):
        return Plan(False, "only plugins/ship-ios/ changed: foundry suite skipped")
    return Plan(True, "files outside plugins/ship-ios/ changed: full suite")


def changed_paths_from_git(base: str, head: str) -> list[str] | None:
    """Files changed by the PR since the merge base, or ``None`` if git cannot say."""
    if not base or not head:
        return None
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "-z", f"{base}...{head}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return [p for p in result.stdout.split("\0") if p]


def main() -> int:
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    paths = None
    if event == "pull_request":
        paths = changed_paths_from_git(
            os.environ.get("PR_BASE_SHA", ""), os.environ.get("PR_HEAD_SHA", "")
        )
    decision = plan(event, paths)
    line = f"run_foundry={'true' if decision.foundry else 'false'}"
    print(line)
    print(f"reason: {decision.reason}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
