"""Foundry tooling package.

Provider factories: the pipeline asks for `tracker()` / `codehost()` and gets the
one selected by config — it never imports a concrete adapter. Switching provider
is a config change (FOUNDRY_TRACKER / FOUNDRY_CODEHOST), not a code change.
"""
from __future__ import annotations

from foundry import config
from foundry.codehosts.base import CodeHost
from foundry.trackers.base import Tracker


def tracker(name: str | None = None) -> Tracker:
    name = name or config.tracker_name()
    if name == "youtrack":
        from foundry.trackers.youtrack import YouTrackTracker
        return YouTrackTracker()
    if name == "ghprojects":
        from foundry.trackers.ghprojects import GitHubProjectsTracker
        return GitHubProjectsTracker()
    if name == "devhub":
        from foundry.trackers.devhub import DevHubTracker
        return DevHubTracker()
    if name == "linear":
        from foundry.trackers.linear import LinearTracker
        return LinearTracker()
    raise SystemExit(
        f"Tracker inconnu : {name}. Connus : youtrack, ghprojects, devhub, linear."
    )


def codehost(name: str | None = None, cwd: str | None = None) -> CodeHost:
    name = name or config.codehost_name()
    if name == "github":
        from foundry.codehosts.github import GitHubCodeHost
        return GitHubCodeHost(cwd=cwd)
    raise SystemExit(f"Code-host inconnu : {name}. Connus : github.")
