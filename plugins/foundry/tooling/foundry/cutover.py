"""CLI for the single PAT-64 tracker cutover command.

Provider credentials and project qualification remain adapter responsibilities;
this command never invents a project from a title or repository basename.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import foundry
from foundry import registry
from foundry.migration import capture_manifest, copy_and_verify, load_manifest, ready_for_cutover, save_manifest, source_is_unchanged, verify_targets
from foundry.models import Project


def _usage() -> str:
    return ("usage: cutover <preflight|copy|activate> <target-tracker> "
            "<target-config.json> <manifest-path>")


def _target(name: str):
    # Explicit construction outside the checkout is intentional: the checkout is
    # still bound to the source until the final registry primitive.  No provider
    # operation is authorized by this construction alone.
    return foundry.tracker(name, cwd="/")


def _target_project(path: Path) -> Project:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise SystemExit("configuration cible de cutover invalide") from None
    if (
        not isinstance(value, dict)
        or set(value) != {"key", "id", "extra"}
        or not isinstance(value["key"], str)
        or not value["key"]
        or not isinstance(value["id"], str)
        or not value["id"]
        or not isinstance(value["extra"], dict)
    ):
        raise SystemExit("configuration cible de cutover invalide")
    return Project(value["key"], value["id"], value["extra"])


def _registry_target_project(project: Project) -> Project:
    """Remove source-specific qualification evidence from the durable binding."""
    return Project(
        project.key, project.id,
        {
            key: value for key, value in project.extra.items()
            if key not in {
                "migration_adr_batch_profile",
                "migration_adr_qualification_project_id",
            }
        },
    )


def main(argv=None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 4 or args[0] not in {"preflight", "copy", "activate"}:
        raise SystemExit(_usage())
    action, target_name, raw_config, raw_path = args
    path = Path(raw_path)
    target_project = _target_project(Path(raw_config))
    key, project_id = target_project.key, target_project.id
    target = _target(target_name)
    manifest = load_manifest(path) if path.exists() else None

    # The binding switch and the final local manifest replacement are separate
    # files.  If the process stops between them, the migration marker is the
    # durable authority and an exact replay finishes only the local phase write.
    if action == "activate" and manifest is not None:
        if (
            manifest["target"]["tracker"] != target.name
            or manifest["target"]["project"] != asdict(target_project)
        ):
            raise SystemExit("manifeste de cutover incompatible avec les coordonnées actives")
        binding_error: ValueError | None = None
        try:
            active = registry.repository_tracker_binding()
        except ValueError as exc:
            binding_error = exc
            active = None
        if active is not None and active.tracker == target.name:
            if (
                active.project != _registry_target_project(target_project)
                or active.migration_manifest_digest != manifest["source_digest"]
                or manifest["target"]["tracker"] != target.name
                or manifest["target"]["project"] != asdict(target_project)
            ):
                raise SystemExit("binding actif incompatible avec le manifeste de cutover")
            verify_targets(manifest, target, target_project)
            manifest["phase"] = "activated"
            save_manifest(path, manifest)
            print(json.dumps({
                "tracker": active.tracker,
                "project": {"key": key, "id": project_id},
                "manifest_digest": manifest["source_digest"],
            }, indent=2))
            return
        if active is None:
            try:
                verify_targets(manifest, target, target_project)
                recovered = registry.cutover_repository_tracker(
                    target.name,
                    key,
                    project_id,
                    migration_manifest_digest=manifest["source_digest"],
                    marker_recovery_only=True,
                )
            except registry.CutoverRecoveryUnavailableError:
                if binding_error is not None:
                    raise SystemExit(str(binding_error)) from None
            else:
                manifest["phase"] = "activated"
                save_manifest(path, manifest)
                print(json.dumps({
                    "tracker": recovered.tracker,
                    "project": {"key": key, "id": project_id},
                    "manifest_digest": manifest["source_digest"],
                }, indent=2))
                return

    source = foundry.tracker()
    source_project = source.resolve_checkout_project()
    if source.name == target.name:
        raise SystemExit("la cible doit être un autre tracker")
    if action == "preflight":
        if manifest is None:
            manifest = capture_manifest(source, source_project, target, target_project)
            save_manifest(path, manifest)
        elif (
            manifest.get("phase") != "preflight"
            or manifest["source"]["tracker"] != source.name
            or manifest["source"]["project"] != asdict(source_project)
            or manifest["target"]["tracker"] != target.name
            or manifest["target"]["project"] != asdict(target_project)
        ):
            raise SystemExit("manifeste de cutover incompatible avec les coordonnées actives")
        registry_project = _registry_target_project(target_project)
        registry.stage_repository_cutover_target(
            target.name,
            registry.repo_basename(use_env=False),
            key,
            project_id,
            migration_manifest_digest=manifest["source_digest"],
            **registry_project.extra,
        )
    else:
        if manifest is None:
            raise SystemExit("manifeste de cutover absent")
        if (manifest["source"]["tracker"] != source.name
                or manifest["source"]["project"] != {"key": source_project.key, "id": source_project.id, "extra": source_project.extra}
                or manifest["target"]["tracker"] != target.name
                or manifest["target"]["project"] != {
                    "key": key, "id": project_id, "extra": target_project.extra,
                }):
            raise SystemExit("manifeste de cutover incompatible avec les coordonnées actives")
        if action == "copy":
            copy_and_verify(manifest, target, target_project, lambda: save_manifest(path, manifest))
        else:
            if not ready_for_cutover(manifest):
                raise SystemExit("manifeste de cutover non vérifié")
            if not source_is_unchanged(manifest, source, source_project, target, target_project):
                raise SystemExit("source modifiée depuis la pré-vérification")
            verify_targets(manifest, target, target_project)
            binding = registry.cutover_repository_tracker(
                target.name, key, project_id, migration_manifest_digest=manifest["source_digest"],
            )
            manifest["phase"] = "activated"
            save_manifest(path, manifest)
            print(json.dumps({"tracker": binding.tracker, "project": {"key": key, "id": project_id}, "manifest_digest": manifest["source_digest"]}, indent=2))
            return
    print(json.dumps({"phase": manifest["phase"], "manifest_digest": manifest["source_digest"], "issues": len(manifest["issues"]), "adrs": len(manifest["adrs"])}, indent=2))


if __name__ == "__main__":
    main()
