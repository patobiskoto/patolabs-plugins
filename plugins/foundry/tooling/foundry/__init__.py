"""Foundry tooling package.

Provider factories: the pipeline asks for `tracker()` / `codehost()` and gets the
one selected by config — it never imports a concrete adapter. Switching provider
is a config change (FOUNDRY_TRACKER / FOUNDRY_CODEHOST), not a code change.
"""
from __future__ import annotations

from foundry import config
from foundry.codehosts.base import CodeHost
from foundry.trackers.base import Tracker


def tracker(name: str | None = None, cwd: str | None = None) -> Tracker:
    """Return the explicitly active tracker for the current repository."""
    from foundry import registry

    try:
        binding = registry.repository_tracker_binding(cwd)
    except ValueError as exc:
        raise SystemExit(f"Binding tracker du dépôt invalide : {exc}") from None
    if name is None:
        name = binding.tracker if binding is not None else config.tracker_name()
    elif binding is not None and name != binding.tracker:
        raise SystemExit(
            f"Binding tracker refusé : '{binding.repository}' est actif sur "
            f"'{binding.tracker}', pas '{name}'."
        )
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
