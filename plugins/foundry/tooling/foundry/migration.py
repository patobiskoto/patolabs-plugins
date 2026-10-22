"""Resumable, provider-neutral tracker cutover orchestration (PAT-64).

The module deliberately has no YouTrack/Linear/GitHub-Projects branches.  It
uses only the Tracker migration port and leaves the local atomic binding change
to ``registry.cutover_repository_tracker`` after provider readback succeeds.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from foundry.models import Adr, Issue, Project
from foundry.trackers.base import (
    Tracker, TrackerConflictError, migration_source_only_issue_refs,
)

SCHEMA = "foundry.tracker-cutover.v1"
_TERMINAL = frozenset({"done", "dropped"})
_ISSUE_ATTRIBUTES = ("type", "priority", "estimate", "state", "parent", "children", "dependencies")
_CHECKBOX = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+.+?\s*$")


class MigrationError(RuntimeError):
    pass


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _issue_snapshot(issue: Issue, source_tracker: str) -> dict:
    links = [asdict(link) for link in issue.links]
    internal = [link for link in links if link["target"]]
    return {
        "kind": "issue", "source_ref": f"{source_tracker}:issue:{issue.id}",
        "id": issue.id, "title": issue.title, "body": issue.body or "",
        "attributes": {
            "type": issue.type, "priority": issue.priority,
            "estimate": issue.estimate, "state": issue.normalized_state or issue.state,
        },
        "links": internal,
        "acceptance": {
            "checkboxes": [
                match.group(1).casefold()
                for line in (issue.body or "").splitlines()
                if (match := _CHECKBOX.match(line)) is not None
            ],
            "projection": {
                "done": issue.ac_done,
                "total": issue.ac_total,
                "status": issue.acceptance_status,
                "source": issue.acceptance_source,
            },
        },
        "digest": "", "target_id": None, "status": "pending", "exceptions": [],
    }


def _adr_snapshot(exported: dict, source_tracker: str) -> dict:
    adr = exported["adr"]
    snapshot = {
        "kind": "adr", "source_ref": f"{source_tracker}:adr:{adr.id}",
        "id": adr.id, "title": adr.title, "body": adr.body or "", "status": adr.status,
        "relations": exported["relations"],
        "source_created": exported.get("source_created"),
        "source_updated": exported.get("source_updated"),
        "digest": "", "target_id": None, "copy_status": "pending", "exceptions": [],
    }
    if exported.get("source_only_issue_refs"):
        snapshot["source_only_issue_refs"] = exported["source_only_issue_refs"]
    return snapshot


def capture_manifest(source: Tracker, source_project: Project, target: Tracker, target_project: Project) -> dict:
    """Read all source inputs and return a closed, no-effect manifest."""
    records = []
    not_copied: dict[str, tuple[list, object]] = {}
    for item in source.migration_export_issues(source_project):
        issue = item["issue"]
        record = _issue_snapshot(issue, source.name)
        not_copied[issue.id] = (list(issue.labels or []), issue.milestone)
        if (issue.normalized_state or issue.state) in _TERMINAL:
            if not target.migration_terminal_import_supported:
                continue  # living-only scope of FOUNDRY-ADR-0026 for this target
            # PAT-331: terminal history is imported frozen; ``pending`` names what the
            # target cannot carry yet (never approximated, never dropped silently).
            record["terminal"] = {
                "pr_url": issue.pr_url,
                "source_created": item["source_created"],
                "source_updated": item["source_updated"],
                "source_completed": item["source_completed"],
            }
            record["pending"] = [
                {"item": name, "reason": reason}
                for name, reason in sorted(target.migration_pending_terminal_items.items())
            ]
        if item["archived"]:
            record["archived"] = True
        records.append(record)
    for record in records:
        exceptions = {
            name: "target capability unavailable"
            for name in sorted(
                set(_ISSUE_ATTRIBUTES) - set(target.migration_supported_attributes)
            )
        }
        specific = target.migration_attribute_exceptions(target_project, record)
        if (
            not isinstance(specific, dict)
            or any(
                name not in _ISSUE_ATTRIBUTES
                or not isinstance(reason, str)
                or not reason
                for name, reason in specific.items()
            )
        ):
            raise MigrationError("exceptions d'attribut cible invalides")
        exceptions.update(specific)
        # PAT-ADR-0032: no target copies labels nor milestone; each non-empty source value
        # is a named exception carrying that value (an issue without them gets none).
        labels, milestone = not_copied[record["id"]]
        if labels:
            exceptions["labels"] = (
                "not copied; source value: " + json.dumps(labels, ensure_ascii=False)
            )
        if milestone is not None:
            exceptions["milestone"] = (
                "not copied; source value: " + json.dumps(milestone, ensure_ascii=False)
            )
        if record.get("archived"):
            exceptions["archived"] = "target has no archived state for an issue"
        record["exceptions"] = [
            {"attribute": name, "reason": exceptions[name]}
            for name in sorted(exceptions)
        ]
        record["digest"] = _digest({k: v for k, v in record.items() if k not in {"digest", "target_id", "status", "exceptions", "pending"}})
    adrs = [
        _adr_snapshot(item, source.name)
        for item in source.migration_export_adrs(source_project)
    ]
    living_ids = {record["id"] for record in records}  # every captured issue, terminal included
    for record in adrs:
        issue_refs = record["relations"].get("issues")
        source_only = record.get("source_only_issue_refs", [])
        if (not isinstance(source_only, list)
                or any(not isinstance(ref, str) or not ref for ref in source_only)
                or len(source_only) != len(set(source_only))):
            raise MigrationError(f"références source ADR invalides: {record['source_ref']}")
        if issue_refs != "unknown":
            if (not isinstance(issue_refs, list)
                    or any(not isinstance(ref, str) or not ref for ref in issue_refs)
                    or len(issue_refs) != len(set(issue_refs))):
                raise MigrationError(f"relations issue ADR invalides: {record['source_ref']}")
            record["relations"]["issues"] = [
                ref for ref in issue_refs if ref in living_ids
            ]
            source_only.extend(
                f"{source.name}:issue:{ref}"
                for ref in issue_refs if ref not in living_ids
            )
        try:
            source_only = migration_source_only_issue_refs(
                sorted(source_only),
            )
        except TrackerConflictError as exc:
            raise MigrationError(
                f"références source ADR invalides: {record['source_ref']}"
            ) from exc
        if source_only:
            record["source_only_issue_refs"] = source_only
    if any(record.get("source_only_issue_refs") for record in adrs):
        for record in adrs:
            record.setdefault("source_only_issue_refs", [])
    for record in adrs:
        record["digest"] = _digest({k: v for k, v in record.items() if k not in {"digest", "target_id", "copy_status", "exceptions"}})
    profile = target.migration_preflight(target_project, tuple(records + adrs))
    if not isinstance(profile, dict) or not profile:
        raise MigrationError("profil de provenance cible non qualifié")
    manifest = {
        "schema": SCHEMA, "source": {"tracker": source.name, "project": asdict(source_project)},
        "target": {
            "tracker": target.name,
            "project": asdict(target_project),
            "migration_profile": profile,
        },
        "issues": records, "adrs": adrs, "phase": "preflight",
        "adr_qualification": {"complete": False, "profile": None},
        "source_digest": "",
    }
    # This digest is the immutable binding input.  Progress (target ids and
    # phase) is intentionally excluded so an interrupted resume does not alter
    # the authority that registry.cutover receives.
    manifest["source_digest"] = _digest(_source_view(manifest))
    return manifest


def _source_view(manifest: dict) -> dict:
    def stable(record: dict, volatile: set[str]) -> dict:
        return {key: value for key, value in record.items() if key not in volatile}
    return {
        "schema": manifest.get("schema"), "source": manifest.get("source"),
        "target": manifest.get("target"),
        "issues": [stable(item, {"target_id", "status"}) for item in manifest.get("issues", [])],
        "adrs": [stable(item, {"target_id", "copy_status"}) for item in manifest.get("adrs", [])],
    }


def load_manifest(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != SCHEMA or not isinstance(value.get("source_digest"), str):
        raise MigrationError("manifeste de cutover invalide")
    if value["source_digest"] != _digest(_source_view(value)):
        raise MigrationError("manifeste de cutover modifié")
    return value


def save_manifest(path: Path, manifest: dict) -> None:
    # Atomic replacement makes an interruption resumable from either version.
    if manifest.get("source_digest") != _digest(_source_view(manifest)):
        raise MigrationError("manifeste de cutover modifié")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _terminal_matches(record: dict, actual: Issue) -> bool:
    """Exact readback of the history-only facts of a terminal record (PAT-331)."""
    terminal = record.get("terminal")
    if terminal is None:
        return actual.import_history is None
    return (
        actual.pr_url == terminal["pr_url"]
        and actual.import_history == {
            key: terminal[key]
            for key in ("source_created", "source_updated", "source_completed")
        }
    )


def _deferred_links(manifest: dict) -> set[str]:
    """Ids of the issues whose links the target cannot carry yet (``pending`` links)."""
    return {
        record["id"] for record in manifest["issues"]
        if any(entry["item"] == "links" for entry in record.get("pending", []))
    }


def copy_and_verify(manifest: dict, target: Tracker, target_project: Project, persist: Callable[[], None]) -> None:
    """Complete only missing records and compare every target readback."""
    for record in manifest["issues"]:
        prior = target.migration_find_issue(target_project, record["source_ref"])
        target_record = deepcopy(record)
        for exception in record["exceptions"]:
            attribute = exception["attribute"]
            if attribute in target_record["attributes"]:
                target_record["attributes"][attribute] = None
        imported = prior or target.migration_import_issue(
            target_project, target_record, source_ref=record["source_ref"],
        )
        actual = target.migration_find_issue(target_project, record["source_ref"])
        if actual is None or actual.id != imported.id:
            raise MigrationError(f"coordonnée issue divergente: {record['source_ref']}")
        attributes = record["attributes"]
        unsupported = {entry["attribute"] for entry in record["exceptions"]}
        if (actual.title != record["title"] or (actual.body or "") != record["body"]
                or any(getattr(actual, name) != value for name, value in attributes.items() if name not in unsupported)
                or (
                    "state" not in unsupported
                    and (actual.normalized_state or actual.state) != attributes["state"]
                )
                or [
                    match.group(1).casefold()
                    for line in (actual.body or "").splitlines()
                    if (match := _CHECKBOX.match(line)) is not None
                ] != record["acceptance"]["checkboxes"]
                or not _terminal_matches(record, actual)):
            raise MigrationError(f"relecture issue divergente: {record['source_ref']}")
        record["target_id"], record["status"] = actual.id, "verified"
        persist()
    ids = {record["id"]: record["target_id"] for record in manifest["issues"]}
    deferred = _deferred_links(manifest)
    for record in manifest["issues"]:
        for link in record["links"]:
            target_id = ids.get(link["target"])
            if target_id is None:
                continue  # external target (not an issue of the manifest).
            if record["id"] in deferred or link["target"] in deferred:
                continue  # pending: the target cannot write this link yet.
            target.migration_link_issue(
                target_project, record["target_id"], link["type"], target_id,
            )
        reread = target.migration_find_issue(target_project, record["source_ref"])
        if reread is None:
            raise MigrationError(f"relecture issue absente: {record['source_ref']}")
        expected = {
            (link["type"], ids[link["target"]]) for link in record["links"]
            if link["target"] in ids and record["id"] not in deferred
            and link["target"] not in deferred
        }
        observed = {(link.type, link.target) for link in reread.links}
        if expected != observed:
            raise MigrationError(f"relecture liens divergente: {record['source_ref']}")
    target_adr_snapshots = tuple(
        _target_adr_snapshot(target, target_project, record, ids)
        for record in manifest["adrs"]
    )
    qualification = manifest.setdefault(
        "adr_qualification", {"complete": False, "profile": None},
    )
    if (
        not isinstance(qualification, dict)
        or set(qualification) != {"complete", "profile"}
        or type(qualification["complete"]) is not bool
    ):
        raise MigrationError("qualification ADR du manifeste invalide")
    if target_adr_snapshots and not qualification["complete"]:
        # This is the only effect permitted between issue readback and the first
        # authoritative ADR write.  The provider may create isolated probes and
        # returns the exact evidence needed to bind the closed target batch.
        qualification["profile"] = target.migration_qualify_adrs(
            target_project, target_adr_snapshots,
        )
        qualification["complete"] = True
        manifest["phase"] = "adr-qualified"
        persist()
    imported_adrs = target.migration_import_adrs(
        target_project, target_adr_snapshots,
        qualification=qualification["profile"],
    ) if target_adr_snapshots else []
    if len(imported_adrs) != len(target_adr_snapshots):
        raise MigrationError("import ADR cible incomplet")
    for record, expected, imported in zip(
        manifest["adrs"], target_adr_snapshots, imported_adrs,
    ):
        if imported.id != expected["id"]:
            raise MigrationError(f"coordonnée ADR divergente: {record['source_ref']}")
        record["target_id"] = imported.id
        persist()
    # Validate the closed ADR corpus only after every support exists.  Providers
    # with reciprocal supersession constraints cannot expose a faithful graph
    # after the first half of a pair has been written.
    exported_adrs = target.migration_export_adrs(target_project)
    for record in manifest["adrs"]:
        target_snapshot = _target_adr_snapshot(
            target, target_project, record, ids,
        )
        matches = [item for item in exported_adrs if item["adr"].id == record["target_id"]]
        actual = matches[0]["adr"] if len(matches) == 1 else None
        relations = matches[0]["relations"] if len(matches) == 1 else None
        source_only = matches[0].get("source_only_issue_refs", []) if len(matches) == 1 else None
        if (actual is None or actual.id != target_snapshot["id"]
                or actual.title != record["title"]
                or actual.status != record["status"]
                or (actual.body or "") != record["body"]
                or relations != target_snapshot["relations"]
                or source_only != target_snapshot.get("source_only_issue_refs", [])):
            raise MigrationError(f"relecture ADR divergente: {record['source_ref']}")
        record["copy_status"] = "verified"
        persist()
    manifest["phase"] = "verified"
    persist()


def verify_targets(manifest: dict, target: Tracker, target_project: Project) -> None:
    """Read every imported coordinate again before publishing the binding.

    Progress fields in a local manifest are hints for resumption, not evidence
    that a provider still holds the copied source.  The source reference is
    resolved afresh so a changed target_id cannot redirect the cutover.
    """
    if (
        manifest.get("phase") not in {"verified", "activated"}
        or not all(item.get("status") == "verified" for item in manifest.get("issues", []))
        or not all(item.get("copy_status") == "verified" for item in manifest.get("adrs", []))
    ):
        raise MigrationError("manifeste de cutover non vérifié")
    ids: dict[str, str] = {}
    deferred = _deferred_links(manifest)
    for record in manifest["issues"]:
        found = target.migration_find_issue(target_project, record["source_ref"])
        if found is None or found.id != record["target_id"]:
            raise MigrationError(f"coordonnée issue divergente: {record['source_ref']}")
        actual = found
        attributes = record["attributes"]
        unsupported = {entry["attribute"] for entry in record["exceptions"]}
        if (actual.title != record["title"] or (actual.body or "") != record["body"]
                or any(getattr(actual, name) != value for name, value in attributes.items() if name not in unsupported)
                or (
                    "state" not in unsupported
                    and (actual.normalized_state or actual.state) != attributes["state"]
                )
                or [
                    match.group(1).casefold()
                    for line in (actual.body or "").splitlines()
                    if (match := _CHECKBOX.match(line)) is not None
                ] != record["acceptance"]["checkboxes"]
                or not _terminal_matches(record, actual)):
            raise MigrationError(f"relecture issue divergente: {record['source_ref']}")
        ids[record["id"]] = found.id
    for record in manifest["issues"]:
        actual = target.migration_find_issue(target_project, record["source_ref"])
        if actual is None:
            raise MigrationError(f"relecture issue absente: {record['source_ref']}")
        expected = {
            (link["type"], ids[link["target"]])
            for link in record["links"]
            if link["target"] in ids and record["id"] not in deferred
            and link["target"] not in deferred
        }
        observed = {(link.type, link.target) for link in actual.links}
        if expected != observed:
            raise MigrationError(f"relecture liens divergente: {record['source_ref']}")
    # One export per pass: a per-ADR export made the read-back quadratic.
    exported_adrs = target.migration_export_adrs(target_project) if manifest["adrs"] else []
    for record in manifest["adrs"]:
        found = target.migration_find_adr(target_project, record["source_ref"])
        if found is None or found.id != record["target_id"]:
            raise MigrationError(f"coordonnée ADR divergente: {record['source_ref']}")
        actual, relations, source_only = _readback_adr(exported_adrs, found.id)
        target_snapshot = _target_adr_snapshot(
            target, target_project, record, ids,
        )
        if (actual is None or actual.id != target_snapshot["id"]
                or actual.title != record["title"]
                or actual.status != record["status"] or (actual.body or "") != record["body"]
                or relations != target_snapshot["relations"]
                or source_only != target_snapshot.get("source_only_issue_refs", [])):
            raise MigrationError(f"relecture ADR divergente: {record['source_ref']}")


def _target_adr_snapshot(
    target: Tracker,
    target_project: Project,
    record: dict,
    issue_ids: dict[str, str],
) -> dict:
    snapshot = deepcopy(record)
    relations = snapshot["relations"]
    if relations.get("issues") != "unknown":
        try:
            relations["issues"] = [issue_ids[item] for item in relations["issues"]]
        except KeyError as exc:
            raise MigrationError(
                f"relation ADR hors périmètre vivant: {record['source_ref']}"
            ) from exc
    return target.migration_prepare_adr(target_project, snapshot)


def _readback_adr(
    exported_adrs: list[dict], target_id: str,
) -> tuple[Adr | None, object, object]:
    matches = [
        exported for exported in exported_adrs
        if exported["adr"].id == target_id
    ]
    if len(matches) != 1:
        return None, None, None
    return (
        matches[0]["adr"], matches[0]["relations"],
        matches[0].get("source_only_issue_refs", []),
    )


def ready_for_cutover(manifest: dict) -> bool:
    # A pending item (PAT-331) is history the target cannot carry yet: no cutover.
    return (manifest.get("phase") == "verified"
            and not any(item.get("pending") for item in manifest.get("issues", [])) and all(item.get("status") == "verified" for item in manifest.get("issues", [])) and all(item.get("copy_status") == "verified" for item in manifest.get("adrs", [])))


def source_is_unchanged(manifest: dict, source: Tracker, source_project: Project, target: Tracker, target_project: Project) -> bool:
    """Perform the final no-effect source read required before binding switch."""
    fresh = capture_manifest(source, source_project, target, target_project)
    return fresh["source_digest"] == manifest.get("source_digest")
