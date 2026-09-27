"""Foundry tooling package.

Provider factories: the pipeline asks for `tracker()` / `codehost()` and gets the
one selected by the repository binding — it never imports a concrete adapter.
Host configuration selects the code host and the unbound historical DevHub pilot;
it never replaces an existing repository tracker selection.
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
    try:
        selection = registry.repository_tracker_selection(cwd, allow_unbound=True)
    except SystemExit as exc:
        # The historical pilot also has host diagnostics outside a Git checkout.
        # No repository state exists there to be replaced by a global provider.
        if "checkout Git introuvable" in str(exc) and config.tracker_name() == "devhub":
            return "devhub", None
        raise
    if selection["mode"] != "unbound":
        return selection["tracker"], selection["binding"]
    if config.tracker_name() == "devhub":
        # Only a proven unbound checkout can use the host-selected pilot.
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
