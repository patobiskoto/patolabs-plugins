"""GitHub Projects adapter — STUB, present to prove the seam holds.

This is the second tracker adapter. It implements only what's free (repo→project
resolution via the registry) and raises a clear, actionable error for the rest.
Its existence is the proof that adding a provider is a localized job: fill these
methods in against the GitHub Projects v2 API and the pipeline/skills are unchanged.

Note: GitHub Projects v2 is GraphQL-only, which is exactly why the original setup
moved off it (rate limits). It stays a stub on purpose; swap `FOUNDRY_TRACKER` to
`ghprojects` only once these methods are implemented.
"""
from __future__ import annotations

from foundry import registry
from foundry.models import Adr, Issue, Project
from foundry.trackers.base import Tracker


def _todo(method: str):
    raise NotImplementedError(
        f"ghprojects.{method} n'est pas implémenté (adaptateur stub). "
        f"Le seam Tracker est prouvé par ce fichier : implémente les méthodes "
        f"contre l'API GitHub Projects v2 (GraphQL) pour l'activer. "
        f"Aucune autre partie du pipeline ne change.")


class GitHubProjectsTracker(Tracker):
    name = "ghprojects"

    def resolve_project(self, repo: str) -> Project:
        # Resolution is provider-agnostic (registry), so this part is real.
        return registry.resolve("ghprojects", repo)

    def search(self, project: Project, query: str = "") -> list[Issue]:
        _todo("search")

    def get_issue(self, issue_id: str) -> Issue:
        _todo("get_issue")

    def create_issue(self, project, title, body, fields=None, parent=None) -> Issue:
        _todo("create_issue")

    def update_fields(self, issue_id: str, fields: dict, project=None) -> Issue:
        _todo("update_fields")

    def set_state(self, issue_id: str, state: str, context=None, project=None) -> None:
        _todo("set_state")

    def link(self, src_id: str, link_type: str, dst_id: str, project=None) -> None:
        _todo("link")

    def add_comment(self, issue_id: str, text: str, project=None) -> None:
        _todo("add_comment")

    def list_adrs(self, project: Project) -> list[Adr]:
        _todo("list_adrs")

    def create_adr(self, project, title, body, status="proposed") -> Adr:
        _todo("create_adr")

    def set_adr_status(self, adr: Adr, status: str, project=None) -> None:
        _todo("set_adr_status")
