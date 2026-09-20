"""YouTrack implementation of the optional Tracker project-provisioning capability."""
from __future__ import annotations

from foundry.models import Project


_STATES = [
    ("backlog", False), ("ready", False), ("in-progress", False),
    ("review", False), ("blocked", False), ("done", True), ("dropped", True),
]
_PRIORITIES = ["P0", "P1", "P2", "P3"]
_TYPES = ["Epic", "Feature", "Bug", "Task"]
_TOP = 500
_STRING_FIELDS = ["Labels", "GitHub PR"]


def _find_global(tracker, name):
    for field in tracker._req(
        "GET", "/admin/customFieldSettings/customFields",
        fields="id,name,fieldType(id),instances(bundle(id,$type))", top=_TOP,
    ):
        if field["name"] == name:
            instances = field.get("instances") or []
            bundle = instances[0].get("bundle") if instances else None
            return field["id"], bundle
    return None, None


def _ensure_bundle_values(tracker, bundle, wanted):
    kind = "state" if bundle["$type"] == "StateBundle" else "enum"
    have = {
        value["name"] for value in tracker._req(
            "GET", f"/admin/customFieldSettings/bundles/{kind}/{bundle['id']}/values",
            fields="name", top=_TOP,
        ) or []
    }
    for item in wanted:
        name, resolved = item if isinstance(item, tuple) else (item, None)
        if name in have:
            continue
        body = {"name": name}
        if resolved is not None:
            body["isResolved"] = resolved
        try:
            tracker._req(
                "POST",
                f"/admin/customFieldSettings/bundles/{kind}/{bundle['id']}/values",
                body, "name",
            )
        except RuntimeError as error:
            if "unique" not in str(error):
                raise


def _project_id(tracker, key):
    for project in tracker._req(
        "GET", "/admin/projects", fields="id,shortName", top=_TOP,
    ):
        if project["shortName"] == key:
            return project["id"]
    return None


def _attached(tracker, project_id):
    project_fields = tracker._req(
        "GET", f"/admin/projects/{project_id}/customFields",
        fields="field(name)", top=_TOP,
    )
    return {item["field"]["name"] for item in project_fields}


def _attach(tracker, project_id, body, name, attached):
    if name in attached:
        return
    tracker._req("POST", f"/admin/projects/{project_id}/customFields", body, "id")
    print(f"  + champ '{name}'")


def _ensure_string_field(tracker, name):
    field_id, _bundle = _find_global(tracker, name)
    if not field_id:
        field_id = tracker._req(
            "POST", "/admin/customFieldSettings/customFields",
            {"name": name, "fieldType": {"id": "string"}, "isAutoAttached": False},
            "id",
        )["id"]
    return field_id


def provision_youtrack_project(tracker, name: str, key: str) -> Project:
    """Create/recover a YouTrack project and its Foundry field vocabulary."""
    project_id = _project_id(tracker, key)
    if project_id:
        print(f"projet {key} déjà présent ({project_id}) — je complète les champs.")
    else:
        leader_id = tracker._req("GET", "/users/me", fields="id")["id"]
        project_id = tracker._req(
            "POST", "/admin/projects",
            {"name": name, "shortName": key, "leader": {"id": leader_id}}, "id",
        )["id"]
        print(f"🏗️  projet {key} créé ({project_id})")

    attached = _attached(tracker, project_id)
    for field_name, values, project_type, bundle_type in [
        ("State", _STATES, "StateProjectCustomField", "StateBundle"),
        ("Priority", _PRIORITIES, "EnumProjectCustomField", "EnumBundle"),
        ("Type", _TYPES, "EnumProjectCustomField", "EnumBundle"),
    ]:
        field_id, bundle = _find_global(tracker, field_name)
        if not field_id or not bundle:
            print(f"  ! champ global '{field_name}' introuvable — ignoré")
            continue
        _ensure_bundle_values(tracker, bundle, values)
        _attach(
            tracker, project_id,
            {"$type": project_type, "field": {"id": field_id},
             "bundle": {"id": bundle["id"], "$type": bundle_type},
             "canBeEmpty": True, "emptyFieldText": "—"},
            field_name, attached,
        )

    estimate_id, _bundle = _find_global(tracker, "Estimate")
    if not estimate_id:
        estimate_id = tracker._req(
            "POST", "/admin/customFieldSettings/customFields",
            {"name": "Estimate", "fieldType": {"id": "integer"},
             "isAutoAttached": False}, "id",
        )["id"]
    _attach(
        tracker, project_id,
        {"$type": "SimpleProjectCustomField", "field": {"id": estimate_id},
         "canBeEmpty": True, "emptyFieldText": "—"},
        "Estimate", attached,
    )

    for field_name in _STRING_FIELDS:
        field_id = _ensure_string_field(tracker, field_name)
        _attach(
            tracker, project_id,
            {"$type": "SimpleProjectCustomField", "field": {"id": field_id},
             "canBeEmpty": True, "emptyFieldText": "—"},
            field_name, attached,
        )

    milestone_bundle = None
    if "Milestone" not in attached:
        bundle_name = f"{key} milestones"
        milestone_bundle = next((
            bundle["id"] for bundle in tracker._req(
                "GET", "/admin/customFieldSettings/bundles/enum",
                fields="id,name", top=_TOP,
            ) if bundle.get("name") == bundle_name
        ), None)
        if not milestone_bundle:
            milestone_bundle = tracker._req(
                "POST", "/admin/customFieldSettings/bundles/enum",
                {"name": bundle_name}, "id",
            )["id"]
        milestone_id, _bundle = _find_global(tracker, "Milestone")
        if not milestone_id:
            milestone_id = tracker._req(
                "POST", "/admin/customFieldSettings/customFields",
                {"name": "Milestone", "fieldType": {"id": "enum[1]"},
                 "isAutoAttached": False}, "id",
            )["id"]
        _attach(
            tracker, project_id,
            {"$type": "EnumProjectCustomField", "field": {"id": milestone_id},
             "bundle": {"id": milestone_bundle, "$type": "EnumBundle"},
             "canBeEmpty": True, "emptyFieldText": "—"},
            "Milestone", attached,
        )
    else:
        for project_field in tracker._req(
            "GET", f"/admin/projects/{project_id}/customFields",
            fields="field(name),bundle(id)", top=_TOP,
        ):
            if project_field["field"]["name"] == "Milestone":
                milestone_bundle = (project_field.get("bundle") or {}).get("id")

    extra = {"ms_bundle": milestone_bundle} if milestone_bundle else {}
    return Project(key=key, id=project_id, extra=extra)
