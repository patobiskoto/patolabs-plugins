"""GitHub Projects adapter — STUB, present to prove the seam holds.

This is the second tracker adapter. It implements only what's free (repo→project
resolution via the registry) and raises a clear, actionable error for the rest.
Its existence is the proof that adding a provider is a localized job: fill these
methods in against the GitHub Projects v2 API and the pipeline/skills are unchanged.

Note: GitHub Projects v2 is GraphQL-only, which is exactly why the original setup
moved off it (rate limits). It stays a stub on purpose; once these methods are
implemented, activate it through the repository's verified tracker binding.
"""
from __future__ import annotations

import json
import subprocess

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

    def __init__(self, *, runner=subprocess.run):
        self._runner = runner

    def verify_project_identity(self, project: Project) -> bool:
        """Read one existing Project v2 and its linked repository; never provision it."""
        owner = project.extra.get("owner")
        number = project.extra.get("number")
        canonical = project.extra.get("canonical_repo")
        if (
            not isinstance(owner, str)
            or not owner
            or not str(number).isdigit()
            or not isinstance(canonical, str)
            or not canonical.startswith("github.com/")
        ):
            return False
        query = (
            "query($login:String!,$number:Int!,$cursor:String){"
            "organization(login:$login){projectV2(number:$number){id number "
            "repositories(first:100,after:$cursor){nodes{nameWithOwner} "
            "pageInfo{hasNextPage endCursor}}}}"
            "user(login:$login){projectV2(number:$number){id number "
            "repositories(first:100,after:$cursor){nodes{nameWithOwner} "
            "pageInfo{hasNextPage endCursor}}}}}"
        )
        expected_repo = canonical.removeprefix("github.com/").casefold()
        cursor = None
        for _page in range(10):
            command = [
                "gh", "api", "graphql", "-f", f"query={query}",
                "-f", f"login={owner}", "-F", f"number={number}",
                "-F" if cursor is None else "-f",
                "cursor=null" if cursor is None else f"cursor={cursor}",
            ]
            result = self._runner(
                command, capture_output=True, text=True, check=False,
            )
            if result.returncode != 0:
                return False
            try:
                payload = json.loads(result.stdout)
                # GraphQL can return HTTP 200 with partial data and errors.  A
                # partial response is not sufficient evidence for publication.
                if payload.get("errors"):
                    return False
                data = payload["data"]
                containers = [data.get("organization"), data.get("user")]
                projects = [
                    container.get("projectV2") for container in containers
                    if isinstance(container, dict) and container.get("projectV2") is not None
                ]
                if len(projects) != 1:
                    return False
                raw = projects[0]
                repositories = raw["repositories"]
                page_info = repositories["pageInfo"]
            except (KeyError, TypeError, ValueError):
                return False
            if raw.get("id") != project.id or raw.get("number") != int(number):
                return False
            if any(
                isinstance(node, dict)
                and str(node.get("nameWithOwner", "")).casefold() == expected_repo
                for node in repositories.get("nodes", [])
            ):
                return True
            if page_info.get("hasNextPage") is not True:
                return False
            cursor = page_info.get("endCursor")
            if not isinstance(cursor, str) or not cursor:
                return False
        return False

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
