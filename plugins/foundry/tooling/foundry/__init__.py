"""Foundry tooling package.

Provider factories: the pipeline asks for `tracker()` / `codehost()` and gets the
one selected by config — it never imports a concrete adapter. Switching provider
is a config change (FOUNDRY_TRACKER / FOUNDRY_CODEHOST), not a code change.
"""
from __future__ import annotations

from foundry import config
from foundry.codehosts.base import CodeHost
from foundry.trackers.base import Tracker


def _effective_tracker_selection(cwd: str | None = None):
    """Return the implicit provider name and any strict repository binding."""
    from foundry import registry

    try:
        binding = registry.repository_tracker_binding(cwd)
    except ValueError as exc:
        raise SystemExit(f"Binding tracker du dépôt invalide : {exc}") from None
    if binding is not None:
        return binding.tracker, binding
    if config.tracker_name() == "devhub":
        # DevHub is the pre-V1 internal pilot.  Keep its host-selected mode
        # outside the three repository-binding providers.
        return "devhub", None
    return registry.tracker_name_for_checkout(cwd), None


def effective_tracker_name(cwd: str | None = None) -> str:
    """Resolve the provider exactly as the implicit tracker factory does."""
    return _effective_tracker_selection(cwd)[0]


def tracker(name: str | None = None, cwd: str | None = None) -> Tracker:
    """Return the explicitly active tracker for the current repository."""
    if name is None:
        name, binding = _effective_tracker_selection(cwd)
    else:
        from foundry import registry

        try:
            binding = registry.repository_tracker_binding(cwd)
        except ValueError as exc:
            raise SystemExit(f"Binding tracker du dépôt invalide : {exc}") from None
    if binding is not None and name != binding.tracker:
        raise SystemExit(
            f"Binding tracker refusé : '{binding.repository}' est actif sur "
            f"'{binding.tracker}', pas '{name}'."
        )
    if name == "youtrack":
        from foundry.trackers.youtrack import YouTrackTracker
        instance = YouTrackTracker()
        if binding is not None:
            instance.requires_mutation_binding = True
        return instance
    if name == "ghprojects":
        from foundry.trackers.ghprojects import GitHubProjectsTracker
        instance = GitHubProjectsTracker()
        if binding is not None:
            instance.requires_mutation_binding = True
        return instance
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
