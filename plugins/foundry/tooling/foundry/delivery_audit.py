"""Read-only, evidence-bound delivery posture audit for registered projects.

The module deliberately does not reuse Foundry's write-tier code-host port: it needs
provider metadata that is outside that port, and it must make its read-only boundary
plain.  Every GitHub invocation is ``gh api -X GET``.  A missing or inaccessible API
must never be reported as a missing delivery control.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

from foundry import registry


SCHEMA = "foundry-delivery-posture-audit.v1"
CLASSIFICATIONS = (
    "present", "absent-proven", "inaccessible", "unsupported", "disabled", "unknown",
)
_CLASSIFICATION_SET = frozenset(CLASSIFICATIONS)
_SAFE_DETAIL = re.compile(r"[^a-z0-9_.:/ -]+", re.IGNORECASE)
_CI_LABEL = re.compile(
    r"(?:^|[ _./-])(?:ci|continuous[ _-]?integration)(?:$|[ _./-])",
    re.IGNORECASE,
)


def _detail(value: str) -> str:
    """Keep provider diagnostics useful without serializing provider output."""
    return _SAFE_DETAIL.sub("", value.lower()).strip()[:120] or "provider-error"


def _github_repository(value: str) -> str:
    """Return a canonical GitHub owner/repository without retaining raw input."""
    try:
        canonical = registry.canonical_repository_identity(value)
    except ValueError:
        raise ValueError("dépôt GitHub invalide") from None
    prefix = "github.com/"
    if not canonical.startswith(prefix):
        raise ValueError("dépôt GitHub requis")
    return canonical.removeprefix(prefix)


@dataclass(frozen=True)
class Response:
    status: int
    body: Any = None
    detail: str = ""


class GitHubReadonly:
    """Minimal GET-only GitHub REST reader with a sanitized response envelope."""

    def __init__(self, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self._runner = runner

    def get(self, endpoint: str) -> Response:
        command = ("gh", "api", "-X", "GET", endpoint)
        try:
            result = self._runner(command, capture_output=True, text=True, check=False)
        except (OSError, subprocess.SubprocessError):
            return Response(0, detail="client-unavailable")
        if result.returncode == 0:
            try:
                return Response(200, json.loads(result.stdout or "null"))
            except json.JSONDecodeError:
                return Response(200, detail="invalid-json")
        stderr = (result.stderr or result.stdout or "").lower()
        for status in (401, 403, 404, 422, 429):
            if f"http {status}" in stderr or f"status {status}" in stderr:
                return Response(status, detail=f"http-{status}")
        return Response(0, detail="provider-error")


def _observation(classification: str, provenance: str, detail: str) -> dict[str, str]:
    if classification not in _CLASSIFICATION_SET:
        raise ValueError("classification inconnue")
    return {
        "classification": classification,
        "provenance": provenance,
        "detail": _detail(detail),
    }


def _access(response: Response, provenance: str) -> dict[str, str] | None:
    """Return a terminal classification for transport/access failures only."""
    if response.status in {401, 403, 429}:
        # Do not pass provider stderr (or a test double's equivalent) through the
        # report.  The status is enough to explain the classification.
        return _observation("inaccessible", provenance, f"http-{response.status}")
    if response.status != 200:
        return _observation("unknown", provenance, "provider-unavailable")
    if response.detail:
        return _observation("unknown", provenance, response.detail)
    return None


def _list_count(response: Response) -> int | None:
    if isinstance(response.body, list):
        return len(response.body)
    if isinstance(response.body, dict):
        value = response.body.get("total_count")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _valid_workflow_entry(value: Any) -> bool:
    """Require the fields used to establish both CI and smoke observations."""
    return (
        isinstance(value, dict)
        and isinstance(value.get("name"), str)
        and bool(value["name"].strip())
        and isinstance(value.get("path"), str)
        and bool(value["path"].strip())
        and isinstance(value.get("state"), str)
        and bool(value["state"].strip())
    )


def _is_explicit_ci_workflow(workflow: dict[str, Any]) -> bool:
    """Recognize only workflows which explicitly identify themselves as CI.

    GitHub's workflow inventory contains releases, maintenance and any other Actions
    automation.  A complete inventory without a clear CI label is not evidence that
    CI exists (or does not exist), so callers preserve it as unknown.
    """
    return any(_CI_LABEL.search(workflow[field]) for field in ("name", "path"))


def _workflow_observation(
    values: list[dict[str, Any]], provenance: str, *, label: str,
) -> dict[str, str]:
    """Classify a complete workflow subset without promoting disabled entries."""
    if not values:
        return _observation("absent-proven", provenance, f"{label}-count:0")
    states = [workflow["state"].strip().lower() for workflow in values]
    active_count = states.count("active")
    disabled_count = sum(state.startswith("disabled_") for state in states)
    if active_count:
        classification = "present"
    elif disabled_count == len(states):
        classification = "disabled"
    else:
        return _observation("unknown", provenance, f"unknown-{label}-state")
    return _observation(
        classification,
        provenance,
        f"active-{label}-count:{active_count} disabled-{label}-count:{disabled_count}",
    )


def _project_observations(repo: str, client: GitHubReadonly) -> dict[str, dict[str, str]]:
    """Collect only capabilities whose endpoint semantics are known and narrow."""
    base = f"repos/{repo}"
    repository = client.get(base)
    base_access = _access(repository, f"github:{base}")
    if base_access is not None:
        return {name: dict(base_access) for name in (
            "ci_workflows", "default_branch_protection", "dependabot_alerts",
            "secret_scanning", "releases", "artifacts", "smoke_workflow",
        )}
    if not isinstance(repository.body, dict) or not isinstance(repository.body.get("default_branch"), str):
        unknown = _observation("unknown", f"github:{base}", "invalid-repository-metadata")
        return {name: dict(unknown) for name in (
            "ci_workflows", "default_branch_protection", "dependabot_alerts",
            "secret_scanning", "releases", "artifacts", "smoke_workflow",
        )}

    workflows_endpoint = f"{base}/actions/workflows?per_page=100"
    workflows = client.get(workflows_endpoint)
    workflow_access = _access(workflows, f"github:{workflows_endpoint}")
    if workflow_access is not None:
        ci = workflow_access
        smoke = dict(workflow_access)
    else:
        count = _list_count(workflows)
        values = workflows.body.get("workflows") if isinstance(workflows.body, dict) else None
        if count is None or not isinstance(values, list):
            ci = _observation("unknown", f"github:{workflows_endpoint}", "invalid-workflow-response")
            smoke = dict(ci)
        elif count != len(values):
            # The endpoint returns one page.  Until every listed workflow is in the
            # response, neither a zero result nor the smoke label inventory is a
            # complete observation.
            ci = _observation("unknown", f"github:{workflows_endpoint}", "incomplete-workflow-pagination")
            smoke = dict(ci)
        elif not all(_valid_workflow_entry(workflow) for workflow in values):
            # A complete count does not make malformed entries trustworthy.  Both
            # controls depend on the same workflow inventory, so neither may turn a
            # silently skipped entry into an absence claim.
            ci = _observation("unknown", f"github:{workflows_endpoint}", "invalid-workflow-entry")
            smoke = dict(ci)
        else:
            provenance = f"github:{workflows_endpoint}"
            ci_values = [workflow for workflow in values if _is_explicit_ci_workflow(workflow)]
            if not values:
                ci = _workflow_observation(ci_values, provenance, label="ci-workflow")
            elif not ci_values:
                ci = _observation(
                    "unknown", provenance, "no-explicit-ci-workflow-label",
                )
            else:
                ci = _workflow_observation(ci_values, provenance, label="ci-workflow")
            # This is intentionally narrow: it proves only a workflow explicitly
            # named/path-labelled smoke, never that arbitrary tests are a smoke test.
            smoke_values = [
                workflow for workflow in values if
                "smoke" in workflow["name"].lower()
                or "smoke" in workflow["path"].lower()
            ]
            smoke = _workflow_observation(
                smoke_values, provenance, label="labelled-smoke-workflow",
            )

    branch = repository.body["default_branch"]
    protection_endpoint = f"{base}/branches/{quote(branch, safe='')}/protection"
    protection = client.get(protection_endpoint)
    protection_access = _access(protection, f"github:{protection_endpoint}")
    if protection.status == 404:
        # A 404 cannot distinguish an unprotected branch from access or routing
        # ambiguity, even after the repository metadata itself was readable.
        branch_control = _observation(
            "unknown", f"github:{protection_endpoint}",
            "ambiguous-branch-protection-404",
        )
    elif protection_access is not None:
        branch_control = protection_access
    else:
        branch_control = _observation("present", f"github:{protection_endpoint}", "branch-protection-readable")

    def security_control(path: str) -> dict[str, str]:
        endpoint = f"{base}/{path}"
        response = client.get(endpoint)
        terminal = _access(response, f"github:{endpoint}")
        if terminal is not None:
            # A security endpoint 404 alone is ambiguous: GitHub can use it for
            # unavailable features as well as inaccessible resources.  Do not turn
            # it into a disabled-control conclusion without provider-specific,
            # documented evidence.
            return terminal
        count = _list_count(response)
        if count is None:
            return _observation("unknown", f"github:{endpoint}", "invalid-alert-response")
        return _observation("present", f"github:{endpoint}", f"alert-endpoint-readable:{count}")

    def inventory(path: str, label: str) -> dict[str, str]:
        endpoint = f"{base}/{path}"
        response = client.get(endpoint)
        terminal = _access(response, f"github:{endpoint}")
        if terminal is not None:
            return terminal
        count = _list_count(response)
        if count is None:
            return _observation("unknown", f"github:{endpoint}", f"invalid-{label}-response")
        return _observation("present" if count else "absent-proven", f"github:{endpoint}", f"{label}-count:{count}")

    return {
        "ci_workflows": ci,
        "default_branch_protection": branch_control,
        "dependabot_alerts": security_control("dependabot/alerts?state=open&per_page=1"),
        "secret_scanning": security_control(
            "secret-scanning/alerts?state=open&per_page=1&hide_secret=true",
        ),
        "releases": inventory("releases?per_page=1", "release"),
        "artifacts": inventory("actions/artifacts?per_page=1", "artifact"),
        "smoke_workflow": smoke,
    }


def select_pilots(data: dict, *, foundry_repo: str) -> list[dict[str, str]]:
    """Choose Foundry plus one canonically bound other project, deterministically."""
    foundry_repo = _github_repository(foundry_repo)
    candidates: dict[tuple[str, str], dict[str, str]] = {}
    for tracker, bindings in sorted(data.items()):
        if not isinstance(bindings, dict):
            continue
        for local_name, entry in sorted(bindings.items()):
            if not isinstance(entry, dict):
                continue
            key, project_id = entry.get("key"), entry.get("id")
            canonical = entry.get("canonical_repo")
            if not isinstance(key, str) or not isinstance(project_id, str):
                continue
            if key == "FOUNDRY":
                candidates[(key, project_id)] = {"project": key, "project_id": project_id, "repo": foundry_repo, "tracker": tracker}
            elif isinstance(canonical, str):
                try:
                    repository = _github_repository(canonical)
                except ValueError:
                    continue
                candidates.setdefault((key, project_id), {"project": key, "project_id": project_id, "repo": repository, "tracker": tracker})
    foundry = next((item for item in candidates.values() if item["project"] == "FOUNDRY"), None)
    others = sorted((item for item in candidates.values() if item["project"] != "FOUNDRY"), key=lambda item: (item["project"], item["repo"]))
    if foundry is None or not others:
        raise ValueError("deux projets enregistrés auditables requis: FOUNDRY et un binding GitHub canonique")
    return [foundry, others[0]]


def audit(data: dict, *, foundry_repo: str, client: GitHubReadonly | None = None) -> dict[str, Any]:
    client = client or GitHubReadonly()
    projects = []
    for pilot in select_pilots(data, foundry_repo=foundry_repo):
        projects.append({**pilot, "observations": _project_observations(pilot["repo"], client)})
    controls = sorted(projects[0]["observations"])
    reusable = []
    for control in controls:
        classifications = [project["observations"][control]["classification"] for project in projects]
        if all(value == "present" for value in classifications):
            priority = 1
        elif any(value == "present" for value in classifications):
            priority = 2
        else:
            priority = 3
        reusable.append({"control": control, "priority": priority, "classifications": classifications})
    reusable.sort(key=lambda item: (item["priority"], item["control"]))
    return {"schema": SCHEMA, "classification_vocabulary": list(CLASSIFICATIONS), "projects": projects, "reusable_controls": reusable}


def human_summary(payload: dict[str, Any]) -> str:
    lines = ["Foundry delivery posture audit (read-only)"]
    for project in payload["projects"]:
        counts = Counter(item["classification"] for item in project["observations"].values())
        rendered = ", ".join(f"{name}={counts.get(name, 0)}" for name in CLASSIFICATIONS if counts.get(name, 0))
        lines.append(f"- {project['project']} ({project['repo']}): {rendered}")
    top = [item["control"] for item in payload["reusable_controls"] if item["priority"] == 1]
    lines.append("- Reusable controls first: " + (", ".join(top) if top else "none proven across both pilots"))
    lines.append("- All observations are GitHub GET evidence; inaccessible, disabled, unsupported, and unknown are not absences.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Audit two registered projects without mutations")
    parser.add_argument("--foundry-repo", help="must match the active Foundry checkout")
    parser.add_argument("--json-only", action="store_true", help="emit only deterministic sanitized JSON")
    args = parser.parse_args(argv)
    try:
        checkout_repo = _github_repository(registry.checkout_repository_identity())
        requested_repo = (
            _github_repository(args.foundry_repo)
            if args.foundry_repo is not None else checkout_repo
        )
    except ValueError as exc:
        parser.error(str(exc))
    if requested_repo != checkout_repo:
        parser.error("--foundry-repo doit correspondre au checkout Foundry actif")
    payload = audit(registry.load(), foundry_repo=checkout_repo)
    if not args.json_only:
        print(human_summary(payload))
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":  # pragma: no cover - CLI boundary
    main()
