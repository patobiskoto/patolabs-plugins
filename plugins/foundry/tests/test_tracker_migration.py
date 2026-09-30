from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
from types import SimpleNamespace

import pytest

from foundry import cutover as cutover_cli
from foundry.migration import MigrationError, capture_manifest, copy_and_verify, load_manifest, ready_for_cutover, save_manifest, verify_targets
from foundry.models import Adr, Issue, Link, Project
from foundry.trackers.base import Tracker
from foundry.trackers.base import IssueUnavailableError, TrackerCapabilityUnavailableError, TrackerConflictError
from foundry.trackers.ghprojects import (
    GitHubProjectsTracker,
    _CreateCandidate,
    _FieldBinding,
)
from foundry.trackers.linear import LinearTracker, LinearTrackerError
from foundry.trackers.youtrack import YouTrackTracker, _YouTrackHTTPError


class FakeTransportTracker(Tracker):
    """Small in-memory tracker used by focused orchestrator tests."""
    migration_supported_attributes = frozenset({"type", "priority", "estimate", "state", "parent", "children", "dependencies"})

    def __init__(self, name, issues=(), adrs=(), adr_relations=None):
        self.name = name
        self.issues = {item.id: copy.deepcopy(item) for item in issues}
        self.adrs = {item.id: copy.deepcopy(item) for item in adrs}
        self.issue_refs, self.adr_refs = {}, {}
        self.adr_relations = copy.deepcopy(adr_relations or {})
        self.effects = 0

    def resolve_project(self, repo): return Project("T", "project")
    def verify_project_identity(self, project): return project.id in {"source", "target", "a", "b"}
    def migration_preflight(self, project, records):
        if not self.verify_project_identity(project):
            raise MigrationError("unqualified fake target")
        return {"kind": f"{self.name}-qualified-test-profile"}
    def search(self, project, query=""): return list(self.issues.values())
    def get_issue(self, issue_id): return copy.deepcopy(self.issues[issue_id])
    def create_issue(self, *args, **kwargs): raise AssertionError("migration port required")
    def update_fields(self, *args, **kwargs): raise AssertionError
    def set_state(self, *args, **kwargs): raise AssertionError
    def link(self, src, link_type, dst, project=None):
        self.issues[src].links.append(Link(link_type, "outward", dst))
    def migration_link_issue(self, project, src, link_type, dst):
        self.link(src, link_type, dst, project)
    def add_comment(self, *args, **kwargs): raise AssertionError
    def list_adrs(self, project): return [copy.deepcopy(item) for item in self.adrs.values()]
    def migration_export_adrs(self, project):
        return [{
            "adr": copy.deepcopy(item),
            "relations": copy.deepcopy(self.adr_relations.get(item.id, {
                "supersedes": "unknown", "superseded_by": "unknown",
                "issues": "unknown",
            })),
            "source_created": None, "source_updated": None,
        } for item in self.adrs.values()]
    def create_adr(self, *args, **kwargs): raise AssertionError("migration port required")
    def set_adr_status(self, *args, **kwargs): raise AssertionError
    def migration_find_issue(self, project, source_ref):
        item = self.issue_refs.get(source_ref)
        return copy.deepcopy(item) if item else None
    def migration_import_issue(self, project, snapshot, *, source_ref):
        existing = self.issue_refs.get(source_ref)
        if existing:
            return copy.deepcopy(existing)
        item = Issue("import-" + snapshot["id"], snapshot["title"], body=snapshot["body"],
                     state=snapshot["attributes"]["state"], type=snapshot["attributes"]["type"],
                     priority=snapshot["attributes"]["priority"], estimate=snapshot["attributes"]["estimate"],
                     ac_done=snapshot["acceptance"]["projection"]["done"],
                     ac_total=snapshot["acceptance"]["projection"]["total"])
        self.issues[item.id] = item
        self.issue_refs[source_ref] = item
        self.effects += 1
        return copy.deepcopy(item)
    def migration_find_adr(self, project, source_ref):
        item = self.adr_refs.get(source_ref)
        return copy.deepcopy(item) if item else None
    def migration_import_adr(self, project, snapshot, *, source_ref):
        existing = self.adr_refs.get(source_ref)
        if existing:
            return copy.deepcopy(existing)
        item = Adr(snapshot["id"], snapshot["title"], snapshot["status"], snapshot["body"])
        self.adrs[item.id] = item
        self.adr_relations[item.id] = copy.deepcopy(snapshot["relations"])
        self.adr_refs[source_ref] = item
        self.effects += 1
        return copy.deepcopy(item)


class _YouTrackPairTransport(FakeTransportTracker):
    """YouTrack-shaped fake: source identity lives in one dedicated field."""

    def __init__(self, issues=(), adrs=(), adr_relations=None):
        super().__init__("youtrack", issues, adrs, adr_relations)
        self.provenance_field = {}

    def migration_find_issue(self, project, source_ref):
        candidates = [
            self.issues[issue_id]
            for issue_id, observed in self.provenance_field.items()
            if observed == source_ref
        ]
        if len(candidates) > 1:
            raise TrackerConflictError("provenance issue YouTrack ambiguë")
        return copy.deepcopy(candidates[0]) if candidates else None

    def migration_import_issue(self, project, snapshot, *, source_ref):
        existing = self.migration_find_issue(project, source_ref)
        if existing is not None:
            return existing
        issue_id = f"{project.key}-{len(self.provenance_field) + 1}"
        item = Issue(
            issue_id, snapshot["title"], body=snapshot["body"],
            state=snapshot["attributes"]["state"],
            type=snapshot["attributes"]["type"],
            priority=snapshot["attributes"]["priority"],
            estimate=snapshot["attributes"]["estimate"],
            ac_done=snapshot["acceptance"]["projection"]["done"],
            ac_total=snapshot["acceptance"]["projection"]["total"],
        )
        self.issues[issue_id] = item
        self.provenance_field[issue_id] = source_ref
        self.issue_refs[source_ref] = item
        self.effects += 1
        return copy.deepcopy(item)


class _LinearPairTransport(FakeTransportTracker):
    """Linear-shaped fake: creation is recovered through a deterministic native slot."""

    def __init__(self, issues=(), adrs=(), adr_relations=None):
        super().__init__("linear", issues, adrs, adr_relations)
        self.native_slots = {}

    @staticmethod
    def _slot(project, source_ref):
        return hashlib.sha256(
            f"{project.id}\0{source_ref}".encode("utf-8"),
        ).hexdigest()

    def migration_find_issue(self, project, source_ref):
        slot = self._slot(project, source_ref)
        item = self.native_slots.get(slot)
        return copy.deepcopy(item) if item is not None else None

    def migration_import_issue(self, project, snapshot, *, source_ref):
        slot = self._slot(project, source_ref)
        existing = self.native_slots.get(slot)
        if existing is not None:
            return copy.deepcopy(existing)
        item = Issue(
            f"{project.key}-{len(self.native_slots) + 1}",
            snapshot["title"], body=snapshot["body"],
            state=snapshot["attributes"]["state"],
            type=snapshot["attributes"]["type"],
            priority=snapshot["attributes"]["priority"],
            estimate=snapshot["attributes"]["estimate"],
            ac_done=snapshot["acceptance"]["projection"]["done"],
            ac_total=snapshot["acceptance"]["projection"]["total"],
        )
        self.native_slots[slot] = item
        self.issues[item.id] = item
        self.issue_refs[source_ref] = item
        self.effects += 1
        return copy.deepcopy(item)

    def migration_qualify_adrs(self, project, snapshots):
        del project
        stable = [
            {
                key: value for key, value in snapshot.items()
                if key not in {"target_id", "copy_status"}
            }
            for snapshot in snapshots
        ]
        return {
            "kind": "linear-closed-batch-fake-v1",
            "digest": hashlib.sha256(
                json.dumps(stable, sort_keys=True).encode("utf-8"),
            ).hexdigest(),
        }

    def migration_import_adrs(self, project, snapshots, *, qualification=None):
        expected = self.migration_qualify_adrs(project, snapshots)
        if qualification != expected:
            raise TrackerConflictError("qualification batch Linear divergente")
        return Tracker.migration_import_adrs(self, project, snapshots)


class _GitHubPairTransport(FakeTransportTracker):
    """GitHub-shaped fake: reserved labels carry provenance and ADR ids are remapped."""

    def __init__(self, issues=(), adrs=(), adr_relations=None):
        super().__init__("ghprojects", issues, adrs, adr_relations)
        self.source_labels = {}

    @staticmethod
    def _label(source_ref):
        return "foundry-migration-" + hashlib.sha256(
            source_ref.encode("utf-8"),
        ).hexdigest()[:16]

    def migration_find_issue(self, project, source_ref):
        item = self.source_labels.get(self._label(source_ref))
        return copy.deepcopy(item) if item is not None else None

    def migration_import_issue(self, project, snapshot, *, source_ref):
        label = self._label(source_ref)
        existing = self.source_labels.get(label)
        if existing is not None:
            return copy.deepcopy(existing)
        item = Issue(
            f"{project.key}-{len(self.source_labels) + 1}",
            snapshot["title"], body=snapshot["body"],
            state=snapshot["attributes"]["state"],
            type=snapshot["attributes"]["type"],
            priority=snapshot["attributes"]["priority"],
            estimate=snapshot["attributes"]["estimate"],
            ac_done=snapshot["acceptance"]["projection"]["done"],
            ac_total=snapshot["acceptance"]["projection"]["total"],
        )
        self.source_labels[label] = item
        self.issues[item.id] = item
        self.issue_refs[source_ref] = item
        self.effects += 1
        return copy.deepcopy(item)

    def migration_prepare_adr(self, project, snapshot):
        prepared = copy.deepcopy(snapshot)

        def remap(identifier):
            match = re.fullmatch(r"[A-Z][A-Z0-9_-]*-ADR-(\d{4})", identifier)
            if match is None:
                raise TrackerCapabilityUnavailableError(
                    self.name, "migration_adr_identifier",
                )
            return f"{project.key}-ADR-{match.group(1)}"

        prepared["id"] = remap(snapshot["id"])
        relations = prepared["relations"]
        if relations.get("supersedes") != "unknown":
            relations["supersedes"] = [remap(item) for item in relations["supersedes"]]
        if relations.get("superseded_by") not in {None, "unknown"}:
            relations["superseded_by"] = remap(relations["superseded_by"])
        return prepared


_PAIR_TRANSPORTS = {
    "youtrack": _YouTrackPairTransport,
    "linear": _LinearPairTransport,
    "ghprojects": _GitHubPairTransport,
}


@pytest.mark.parametrize("source_name,target_name", [
    ("youtrack", "linear"), ("youtrack", "ghprojects"),
    ("linear", "youtrack"), ("linear", "ghprojects"),
    ("ghprojects", "youtrack"), ("ghprojects", "linear"),
])
def test_every_provider_transport_pair_copies_living_work_and_adrs_once(
    source_name, target_name,
):
    source = _PAIR_TRANSPORTS[source_name]([
        Issue("SRC-1", "exact title", state="in-progress", body="- [x] exact AC\n", type="Feature", priority="P1", estimate=3,
              ac_done=1, ac_total=1, links=[Link("depends-on", "outward", "SRC-2")]),
        Issue("SRC-2", "terminal", state="done", body="old"),
    ], [Adr("SRC-ADR-0001", "decision", "accepted", "* exact body\n")])
    target = _PAIR_TRANSPORTS[target_name]()
    source_project = Project("SRC", "source")
    target_project = Project("DST", "target")
    manifest = capture_manifest(source, source_project, target, target_project)
    assert [item["id"] for item in manifest["issues"]] == ["SRC-1"]
    assert manifest["adrs"][0]["relations"]["issues"] == "unknown"
    copy_and_verify(manifest, target, target_project, lambda: None)
    assert ready_for_cutover(manifest)
    digest = manifest["source_digest"]
    effects = target.effects
    copy_and_verify(manifest, target, target_project, lambda: None)
    assert target.effects == effects
    assert manifest["source_digest"] == digest
    assert type(source) is _PAIR_TRANSPORTS[source_name]
    assert type(target) is _PAIR_TRANSPORTS[target_name]
    if target_name == "linear":
        assert len(target.native_slots) == 1
    elif target_name == "ghprojects":
        assert set(target.source_labels) == {
            target._label(f"{source_name}:issue:SRC-1")
        }
        assert "DST-ADR-0001" in target.adrs
    else:
        assert target.provenance_field == {"DST-1": f"{source_name}:issue:SRC-1"}


def test_manifest_records_target_specific_missing_and_unsupported_values(
    monkeypatch, tmp_path,
):
    source = FakeTransportTracker("youtrack", [
        Issue("T-1", "missing", state="ready", body="body", type=None),
        Issue("T-2", "unsupported", state="ready", body="body", type="Story"),
    ])
    target = GitHubProjectsTracker(state_dir=tmp_path)
    monkeypatch.setattr(
        target, "migration_preflight", lambda _project, _records: {"kind": "test"},
    )

    manifest = capture_manifest(
        source,
        Project("T", "source"),
        target,
        Project("T", "PVT_target", {
            "owner": "acme", "number": "1",
            "canonical_repo": "github.com/acme/target",
            "migration_label": "foundry-migration",
        }),
    )

    assert manifest["issues"][0]["exceptions"] == [{
        "attribute": "type",
        "reason": "target requires Task when source value is absent",
    }]
    assert manifest["issues"][1]["exceptions"] == [{
        "attribute": "type",
        "reason": "target value unavailable: Story",
    }]


def test_manifest_records_project_catalog_value_missing_before_first_effect(
    monkeypatch, tmp_path,
):
    source = FakeTransportTracker("youtrack", [
        Issue(
            "T-1", "catalog mismatch", state="ready", body="body",
            type="Feature", priority="P1", estimate=3,
        ),
    ])
    target = GitHubProjectsTracker(state_dir=tmp_path)
    project = Project("T", "PVT_target", {
        "owner": "acme", "number": "1",
        "canonical_repo": "github.com/acme/target",
        "migration_label": "foundry-migration",
    })
    monkeypatch.setattr(target, "verify_project_identity", lambda _project: True)
    monkeypatch.setattr(
        target,
        "_rest",
        lambda *_args: {
            "name": "foundry-migration",
            "description": "Foundry migration provenance profile v1",
        },
    )
    catalog = {
        "type": _FieldBinding(
            "type-id", "Foundry type", "SINGLE_SELECT", {"Task": "task-id"},
        ),
        "priority": _FieldBinding(
            "priority-id", "Foundry priority", "SINGLE_SELECT", {"P1": "p1-id"},
        ),
        "state": _FieldBinding(
            "state-id", "Foundry normalized state", "SINGLE_SELECT",
            {"ready": "ready-id"},
        ),
        "estimate": _FieldBinding(
            "estimate-id", "Foundry estimate", "NUMBER", {},
        ),
    }
    requested = []

    def write_catalog(_binding, values):
        requested.append(values)
        assert values.get("type") != "Feature"
        return catalog

    monkeypatch.setattr(target, "_write_catalog", write_catalog)

    manifest = capture_manifest(source, Project("T", "source"), target, project)

    assert manifest["issues"][0]["exceptions"] == [{
        "attribute": "type",
        "reason": "target native option unavailable: Feature",
    }]
    assert requested == [
        {"type": "Task"},
        {"type": "Task", "priority": "P1", "estimate": 3, "state": "ready"},
    ]


def test_copy_skips_exact_state_readback_only_when_manifest_declares_exception():
    source = FakeTransportTracker("youtrack", [
        Issue("T-1", "state mismatch", state="triage", body="body", type="Task"),
    ])
    target = FakeTransportTracker("linear")
    target.migration_attribute_exceptions = (
        lambda _project, _record: {"state": "target value unavailable: triage"}
    )
    manifest = capture_manifest(
        source, Project("T", "source"), target, Project("T", "target"),
    )

    copy_and_verify(manifest, target, Project("T", "target"), lambda: None)

    assert manifest["issues"][0]["exceptions"] == [{
        "attribute": "state",
        "reason": "target value unavailable: triage",
    }]
    assert target.issues["import-T-1"].state is None


def test_manifest_reports_unsupported_attribute_instead_of_dropping_it():
    source = FakeTransportTracker("youtrack", [Issue("T-1", "x", state="ready", body="body", type="Task")])
    target = FakeTransportTracker("linear")
    target.migration_supported_attributes = frozenset({"state"})
    manifest = capture_manifest(source, Project("T", "a"), target, Project("T", "b"))
    assert {item["attribute"] for item in manifest["issues"][0]["exceptions"]} == {
        "type", "priority", "estimate", "parent", "children", "dependencies"
    }


def test_internal_graph_acceptance_and_known_adr_relations_are_read_back_exactly():
    source = FakeTransportTracker(
        "youtrack",
        [
            Issue(
                "SRC-1", "parent", state="in-progress",
                body="- [x] shipped\n- [ ] pending\n", type="Epic",
                priority="P1", estimate=3,
                links=[Link("parent-of", "outward", "SRC-2")],
            ),
            Issue(
                "SRC-2", "child", state="ready", body="body", type="Task",
                priority="P2", estimate=2,
                links=[Link("subtask-of", "inward", "SRC-1")],
            ),
        ],
        [
            Adr("SRC-ADR-0001", "old", "superseded", "old bytes\n"),
            Adr("SRC-ADR-0002", "new", "accepted", "new bytes\n"),
        ],
        {
            "SRC-ADR-0001": {
                "supersedes": [], "superseded_by": "SRC-ADR-0002",
                "issues": ["SRC-1"],
            },
            "SRC-ADR-0002": {
                "supersedes": ["SRC-ADR-0001"], "superseded_by": None,
                "issues": ["SRC-2"],
            },
        },
    )
    target = FakeTransportTracker("linear")
    project = Project("DST", "target")
    manifest = capture_manifest(source, Project("SRC", "source"), target, project)
    copy_and_verify(manifest, target, project, lambda: None)
    verify_targets(manifest, target, project)

    assert target.issues["import-SRC-1"].body == "- [x] shipped\n- [ ] pending\n"
    assert ("parent-of", "import-SRC-2") in {
        (link.type, link.target)
        for link in target.issues["import-SRC-1"].links
    }
    assert target.adr_relations["SRC-ADR-0002"] == {
        "supersedes": ["SRC-ADR-0001"], "superseded_by": None,
        "issues": ["import-SRC-2"],
    }
    target.adr_relations["SRC-ADR-0002"]["issues"] = []
    with pytest.raises(MigrationError, match="relecture ADR divergente"):
        verify_targets(manifest, target, project)


def test_known_adr_relation_to_terminal_issue_fails_before_target_effect():
    source = FakeTransportTracker(
        "youtrack",
        [Issue("SRC-1", "done", state="done", body="body")],
        [Adr("SRC-ADR-0001", "decision", "accepted", "bytes")],
        {"SRC-ADR-0001": {
            "supersedes": [], "superseded_by": None, "issues": ["SRC-1"],
        }},
    )
    target = FakeTransportTracker("linear")
    with pytest.raises(MigrationError, match="hors périmètre vivant"):
        capture_manifest(
            source, Project("SRC", "source"), target, Project("DST", "target"),
        )
    assert target.effects == 0


class _PhaseQualificationTracker(FakeTransportTracker):
    def __init__(self, *, fail_qualification=False, fail_import=False):
        super().__init__("linear")
        self.fail_qualification = fail_qualification
        self.fail_import = fail_import
        self.qualification_calls = 0

    def migration_qualify_adrs(self, project, snapshots):
        self.qualification_calls += 1
        if self.fail_qualification:
            raise TrackerConflictError("phase-2 qualification failed")
        return {
            "kind": "fake-linear-exact-batch-v1",
            "relations": [
                copy.deepcopy(snapshot["relations"]) for snapshot in snapshots
            ],
        }

    def migration_import_adrs(
        self, project, snapshots, *, qualification=None,
    ):
        expected = {
            "kind": "fake-linear-exact-batch-v1",
            "relations": [
                copy.deepcopy(snapshot["relations"]) for snapshot in snapshots
            ],
        }
        if qualification != expected:
            raise TrackerConflictError("phase-2 profile is not exact")
        if self.fail_import:
            self.fail_import = False
            raise TrackerConflictError("interrupted after qualification")
        return Tracker.migration_import_adrs(self, project, snapshots)


def _known_relation_source():
    return FakeTransportTracker(
        "youtrack",
        [Issue("SRC-1", "living", state="ready", body="exact issue")],
        [Adr("SRC-ADR-0001", "decision", "accepted", "exact ADR bytes\n")],
        {"SRC-ADR-0001": {
            "supersedes": [], "superseded_by": None, "issues": ["SRC-1"],
        }},
    )


def test_phase_two_qualification_failure_keeps_source_digest_and_copied_issues():
    source = _known_relation_source()
    target = _PhaseQualificationTracker(fail_qualification=True)
    project = Project("DST", "target")
    manifest = capture_manifest(source, Project("SRC", "source"), target, project)
    digest = manifest["source_digest"]

    with pytest.raises(TrackerConflictError, match="phase-2 qualification failed"):
        copy_and_verify(manifest, target, project, lambda: None)

    assert manifest["source_digest"] == digest
    assert manifest["issues"][0]["status"] == "verified"
    assert manifest["adrs"][0]["copy_status"] == "pending"
    assert manifest["adr_qualification"] == {"complete": False, "profile": None}
    assert target.effects == 1
    assert target.adr_refs == {}


def test_phase_two_profile_is_persisted_and_reused_after_interrupted_adr_import(
    tmp_path,
):
    source = _known_relation_source()
    target = _PhaseQualificationTracker(fail_import=True)
    project = Project("DST", "target")
    manifest = capture_manifest(source, Project("SRC", "source"), target, project)
    digest = manifest["source_digest"]
    persisted = []

    with pytest.raises(TrackerConflictError, match="interrupted after qualification"):
        copy_and_verify(
            manifest, target, project,
            lambda: persisted.append(copy.deepcopy(manifest)),
        )

    assert manifest["phase"] == "adr-qualified"
    assert manifest["adr_qualification"]["complete"] is True
    assert target.qualification_calls == 1
    assert persisted[-1]["adr_qualification"] == manifest["adr_qualification"]

    path = tmp_path / "cutover.json"
    save_manifest(path, manifest)
    resumed = load_manifest(path)
    copy_and_verify(resumed, target, project, lambda: save_manifest(path, resumed))

    assert ready_for_cutover(resumed)
    assert resumed["source_digest"] == digest
    assert target.qualification_calls == 1
    assert target.effects == 2
    assert target.adr_relations["SRC-ADR-0001"]["issues"] == ["import-SRC-1"]


def test_activation_readback_rejects_target_drift_and_manifest_redirect():
    source = FakeTransportTracker("youtrack", [Issue("T-1", "exact", state="ready", body="body")])
    target = FakeTransportTracker("linear")
    project = Project("T", "target")
    manifest = capture_manifest(source, Project("T", "source"), target, project)
    copy_and_verify(manifest, target, project, lambda: None)
    verify_targets(manifest, target, project)
    manifest["issues"][0]["target_id"] = "another-issue"
    with pytest.raises(MigrationError, match="coordonnée issue divergente"):
        verify_targets(manifest, target, project)
    manifest["issues"][0]["target_id"] = "import-T-1"
    target.issues["import-T-1"].body = "changed after copy"
    with pytest.raises(MigrationError, match="relecture issue divergente"):
        verify_targets(manifest, target, project)


def test_preflight_rejects_unqualified_target_before_any_import_effect():
    source = FakeTransportTracker(
        "youtrack", [Issue("T-1", "x", state="ready", body="body")]
    )
    target = FakeTransportTracker("linear")
    target.migration_preflight = Tracker.migration_preflight.__get__(target, Tracker)
    with pytest.raises(TrackerCapabilityUnavailableError, match="migration_provenance_profile"):
        capture_manifest(source, Project("T", "source"), target, Project("T", "target"))
    assert target.effects == 0


def test_activate_replay_finishes_manifest_after_binding_was_already_published(
    monkeypatch, tmp_path, capsys,
):
    target_project = Project("DST", "target", {
        "profile": "qualified",
        "migration_adr_qualification_project_id": (
            "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a"
        ),
    })
    active_project = Project("DST", "target", {"profile": "qualified"})
    manifest = {
        "schema": "foundry.tracker-cutover.v1",
        "source": {"tracker": "youtrack", "project": {
            "key": "SRC", "id": "source", "extra": {},
        }},
        "target": {"tracker": "linear", "project": {
            "key": "DST", "id": "target", "extra": target_project.extra,
        }, "migration_profile": {"kind": "test"}},
        "issues": [], "adrs": [], "phase": "verified", "source_digest": "",
    }
    # Derive the immutable digest through the public capture/save invariant.
    from foundry.migration import _digest, _source_view
    manifest["source_digest"] = _digest(_source_view(manifest))
    manifest_path = tmp_path / "manifest.json"
    config_path = tmp_path / "target.json"
    save_manifest(manifest_path, manifest)
    config_path.write_text(json.dumps({
        "key": "DST", "id": "target", "extra": target_project.extra,
    }))
    target = FakeTransportTracker("linear")
    monkeypatch.setattr(cutover_cli, "_target", lambda name: target)
    monkeypatch.setattr(
        cutover_cli.registry, "repository_tracker_binding",
        lambda: SimpleNamespace(
            tracker="linear", project=active_project,
            migration_manifest_digest=manifest["source_digest"],
        ),
    )
    verified = []
    monkeypatch.setattr(
        cutover_cli, "verify_targets",
        lambda observed, adapter, project: verified.append((adapter, project)),
    )

    cutover_cli.main([
        "activate", "linear", str(config_path), str(manifest_path),
    ])

    assert load_manifest(manifest_path)["phase"] == "activated"
    assert verified == [(target, target_project)]
    assert json.loads(capsys.readouterr().out)["tracker"] == "linear"


@pytest.mark.parametrize("source_v1", [True, False])
def test_activate_cli_replays_after_registry_promotion_before_marker_replace(
    monkeypatch, tmp_path, capsys, source_v1,
):
    state = tmp_path / "state"
    repo = tmp_path / "public"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo, check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "remote.origin.url", "https://github.com/acme/public.git"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        [
            "git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "--allow-empty", "-m", "init",
        ],
        cwd=repo, check=True, capture_output=True,
    )
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    monkeypatch.chdir(repo)
    canonical = "github.com/acme/public"
    source_extra = {"canonical_repo": canonical} if source_v1 else {}
    cutover_cli.registry.register(
        "youtrack", "public", "SRC", "0-1", **source_extra,
    )
    if source_v1:
        cutover_cli.registry.bootstrap_repository_binding(
            "youtrack", "public", "SRC", "0-1", cwd=str(repo),
        )
    target_project = Project("DST", "PVT_target", {
        "canonical_repo": canonical,
        "owner": "acme",
        "number": "1",
        "migration_label": "foundry-migration",
    })
    manifest = {
        "schema": "foundry.tracker-cutover.v1",
        "source": {
            "tracker": "youtrack",
            "project": {"key": "SRC", "id": "0-1", "extra": {
                "canonical_repo": canonical,
            }},
        },
        "target": {
            "tracker": "ghprojects",
            "project": {
                "key": target_project.key,
                "id": target_project.id,
                "extra": target_project.extra,
            },
            "migration_profile": {"kind": "test"},
        },
        "issues": [],
        "adrs": [],
        "phase": "verified",
        "adr_qualification": {"complete": False, "profile": None},
        "source_digest": "",
    }
    from foundry.migration import _digest, _source_view
    manifest["source_digest"] = _digest(_source_view(manifest))
    cutover_cli.registry.stage_repository_cutover_target(
        "ghprojects", "public", target_project.key, target_project.id,
        migration_manifest_digest=manifest["source_digest"],
        cwd=str(repo), **target_project.extra,
    )
    manifest_path = tmp_path / "manifest.json"
    config_path = tmp_path / "target.json"
    save_manifest(manifest_path, manifest)
    config_path.write_text(json.dumps({
        "key": target_project.key,
        "id": target_project.id,
        "extra": target_project.extra,
    }), encoding="utf-8")
    target = FakeTransportTracker("ghprojects")
    source_calls = []

    def source_tracker(*_args, **_kwargs):
        source_calls.append(True)
        if len(source_calls) > 1:
            raise AssertionError("replay must repair the promoted binding before source resolution")
        source = FakeTransportTracker("youtrack")
        source.resolve_checkout_project = lambda: Project(
            "SRC", "0-1", {"canonical_repo": canonical},
        )
        return source

    monkeypatch.setattr(cutover_cli, "_target", lambda _name: target)
    monkeypatch.setattr(cutover_cli.foundry, "tracker", source_tracker)
    monkeypatch.setattr(cutover_cli, "source_is_unchanged", lambda *_args: True)
    monkeypatch.setattr(cutover_cli, "verify_targets", lambda *_args: None)
    real_replace = cutover_cli.registry.os.replace

    def interrupted(source, destination):
        if str(destination).endswith(".foundry/tracker.json"):
            raise OSError("simulated marker interruption")
        return real_replace(source, destination)

    monkeypatch.setattr(cutover_cli.registry.os, "replace", interrupted)
    with pytest.raises(OSError, match="marker interruption"):
        cutover_cli.main([
            "activate", "ghprojects", str(config_path), str(manifest_path),
        ])
    assert cutover_cli.registry.load()["youtrack"]["public"]["archive"] is True
    assert load_manifest(manifest_path)["phase"] == "verified"

    monkeypatch.setattr(cutover_cli.registry.os, "replace", real_replace)
    cutover_cli.main([
        "activate", "ghprojects", str(config_path), str(manifest_path),
    ])

    assert len(source_calls) == 1
    assert load_manifest(manifest_path)["phase"] == "activated"
    assert cutover_cli.registry.repository_tracker_binding(str(repo)).tracker == "ghprojects"
    assert json.loads(capsys.readouterr().out)["tracker"] == "ghprojects"


class _YouTrackMigrationTransport(YouTrackTracker):
    def __init__(self, *, lose_issue=False, lose_adr=False):
        self.url, self.token = "https://youtrack.invalid", "test"
        self.issues, self.articles = {}, []
        self.lose_issue, self.lose_adr = lose_issue, lose_adr
        self.issue_posts = self.adr_posts = 0

    def _search_raw(self, query, fields, top=1000):
        return list(self.issues.values())

    def _project_articles_raw(self, project, fields, top=1000):
        return list(self.articles)

    def _req(self, method, path, body=None, fields=None, top=None):
        if method == "POST" and path == "/issues":
            self.issue_posts += 1
            issue_id = "DST-1"
            raw = {
                "idReadable": issue_id, "summary": body["summary"],
                "description": body["description"], "customFields": body["customFields"],
                "links": [],
            }
            self.issues[issue_id] = raw
            if self.lose_issue:
                self.lose_issue = False
                raise _YouTrackHTTPError("POST", path, 503, "lost")
            return {"idReadable": issue_id}
        if method == "GET" and path.startswith("/issues/"):
            return self.issues[path.rsplit("/", 1)[1]]
        if method == "POST" and path == "/articles":
            self.adr_posts += 1
            raw = {
                "idReadable": "DST-A-1", "summary": body["summary"],
                "content": body["content"], "created": 1, "updated": 2,
            }
            self.articles.append(raw)
            if self.lose_adr:
                self.lose_adr = False
                raise _YouTrackHTTPError("POST", path, 503, "lost")
            return raw
        raise AssertionError((method, path, body, fields, top))


def _issue_record():
    return {
        "kind": "issue", "id": "SRC-1", "title": "exact title",
        "body": "- [x] exact byte body\n", "source_ref": "linear:issue:SRC-1",
        "attributes": {
            "type": "Feature", "priority": "P1", "estimate": 3, "state": "ready",
        },
        "acceptance": {"checkboxes": ["x"], "projection": {"done": 1, "total": 1}},
    }


def test_youtrack_adapter_recovers_issue_and_adr_lost_responses_without_duplicates():
    tracker = _YouTrackMigrationTransport(lose_issue=True, lose_adr=True)
    project = Project(
        "DST", "target", extra={"migration_source_field": "Foundry Migration Source"}
    )
    issue = tracker.migration_import_issue(
        project, _issue_record(), source_ref="linear:issue:SRC-1"
    )
    replay = tracker.migration_import_issue(
        project, _issue_record(), source_ref="linear:issue:SRC-1"
    )
    assert issue.id == replay.id == "DST-1"
    assert tracker.issue_posts == 1

    adr_record = {
        "id": "SRC-ADR-0001", "title": "Exact decision", "status": "accepted",
        "body": "* byte exact\n", "relations": {
            "issues": "unknown", "supersedes": [], "superseded_by": None,
        },
        "source_created": 1, "source_updated": 2,
    }
    adr = tracker.migration_import_adr(
        project, adr_record, source_ref="linear:adr:SRC-ADR-0001"
    )
    again = tracker.migration_import_adr(
        project, adr_record, source_ref="linear:adr:SRC-ADR-0001"
    )
    assert adr == again
    assert adr.body == "* byte exact\n"
    assert adr.status == "accepted"
    exported = tracker.migration_export_adrs(project)
    assert exported == [{
        "adr": adr,
        "relations": {
            "issues": "unknown", "supersedes": [], "superseded_by": None,
        },
        "source_created": 1, "source_updated": 2,
    }]
    assert tracker.adr_posts == 1


def test_youtrack_duplicate_provenance_fails_closed():
    tracker = _YouTrackMigrationTransport()
    field = {"name": "Foundry Migration Source", "value": "linear:issue:SRC-1"}
    tracker.issues = {
        "DST-1": {"idReadable": "DST-1", "summary": "one", "description": "", "customFields": [field], "links": []},
        "DST-2": {"idReadable": "DST-2", "summary": "two", "description": "", "customFields": [field], "links": []},
    }
    project = Project("DST", "target", {"migration_source_field": field["name"]})
    with pytest.raises(TrackerConflictError, match="ambiguë"):
        tracker.migration_find_issue(project, "linear:issue:SRC-1")


class _LinearMigrationTransport(LinearTracker):
    def __init__(self, *, lose=False):
        self._active_project = None
        self.rows = {}
        self.posts = 0
        self.lose = lose

    def verify_project_identity(self, project):
        return True

    def _desired_update(self, raw, project, fields):
        return ({}, {})

    def _read_raw(self, issue_id):
        if issue_id not in self.rows:
            raise IssueUnavailableError(issue_id)
        return self.rows[issue_id]

    def _graphql(self, document, variables, operation):
        self.posts += 1
        value = variables["input"]
        self.rows[value["id"]] = {
            "id": value["id"], "identifier": "DST-1", "title": value["title"],
            "description": value["description"], "team": {"id": "team-uuid"},
            "project": {"id": "project-uuid"},
        }
        if self.lose:
            self.lose = False
            raise LinearTrackerError(operation, None, "transport_error")
        return {"issueCreate": {"success": True, "issue": {
            "id": value["id"], "identifier": "DST-1",
        }}}

    def _to_issue(self, raw, project=None, *, observe_lifecycle=False):
        return Issue(
            raw["identifier"], raw["title"], state="ready", normalized_state="ready",
            body=raw["description"], type="Feature", priority="P1", estimate=3,
        )

    def _raw_matches(self, raw, expected):
        return True


def test_linear_adapter_deterministic_creation_recovers_lost_response():
    tracker = _LinearMigrationTransport(lose=True)
    project = Project(
        "DST", "project-uuid", extra={
            "canonical_repo": "github.com/acme/dst", "team_id": "team-uuid",
            "state_ids": {name: f"state-{name}" for name in (
                "backlog", "ready", "in-progress", "review", "blocked", "done", "dropped"
            )},
            "type_label_ids": {}, "label_ids": {}, "milestone_ids": {},
            "migration_identity_profile": "foundry-linear-deterministic-v1",
        },
    )
    created = tracker.migration_import_issue(
        project, _issue_record(), source_ref="youtrack:issue:SRC-1"
    )
    replay = tracker.migration_import_issue(
        project, _issue_record(), source_ref="youtrack:issue:SRC-1"
    )
    assert created.id == replay.id == "DST-1"
    assert tracker.posts == 1
    assert len(tracker.rows) == 1


def test_linear_deterministic_slot_with_wrong_native_identity_fails_closed():
    tracker = _LinearMigrationTransport()
    project = Project("DST", "project-uuid", extra={
        "canonical_repo": "github.com/acme/dst", "team_id": "team-uuid",
        "state_ids": {name: f"state-{name}" for name in (
            "backlog", "ready", "in-progress", "review", "blocked", "done", "dropped"
        )},
        "type_label_ids": {}, "label_ids": {}, "milestone_ids": {},
    })
    from foundry.trackers.linear import _migration_issue_id
    slot = _migration_issue_id(project.id, "youtrack:issue:SRC-1")
    tracker.rows[slot] = {
        "id": "different-native-id", "identifier": "DST-1", "title": "x",
        "description": "body", "team": {"id": "team-uuid"},
        "project": {"id": "project-uuid"},
    }
    with pytest.raises(TrackerConflictError, match="identité"):
        tracker.migration_find_issue(project, "youtrack:issue:SRC-1")


def test_linear_adr_migration_preserves_exact_source_and_relation_knowledge(monkeypatch):
    tracker = _LinearMigrationTransport()
    project = Project("DST", "project-uuid")
    captured = {}
    monkeypatch.setattr(tracker, "migration_find_adr", lambda *_: None)

    def imported(supplied, **kwargs):
        captured.update(project=supplied, **kwargs)
        return Adr(
            kwargs["adr_id"], kwargs["title"], kwargs["historical_status"],
            kwargs["body"], "doc-1",
        )

    monkeypatch.setattr(tracker, "import_adr", imported)
    snapshot = {
        "id": "SRC-ADR-0002", "title": "Décision exacte",
        "status": "accepted", "body": "octets exacts\n",
        "relations": {
            "supersedes": ["SRC-ADR-0001"], "superseded_by": None,
            "issues": "unknown",
        },
        "source_created": 10, "source_updated": 20,
    }
    result = tracker.migration_import_adr(
        project, snapshot, source_ref="ghprojects:adr:SRC-ADR-0002",
    )
    assert result.body == "octets exacts\n"
    assert captured["source_ref"] == "ghprojects:adr:SRC-ADR-0002"
    assert captured["supersedes"] == ("SRC-ADR-0001",)
    assert captured["superseded_by"] is None
    assert "issue_refs" not in captured


def test_linear_preflight_requires_closed_adr_qualification_before_issue_effects():
    tracker = _LinearMigrationTransport()
    project = Project(
        "DST", "project-uuid", extra={
            "migration_identity_profile": "foundry-linear-deterministic-v1",
        },
    )
    adr = {
        "kind": "adr", "id": "SRC-ADR-0001", "title": "Decision",
        "status": "accepted", "body": "exact", "source_ref": "youtrack:adr:1",
        "relations": {
            "supersedes": [], "superseded_by": None, "issues": "unknown",
        },
        "source_created": None, "source_updated": None,
    }
    with pytest.raises(
        TrackerCapabilityUnavailableError,
        match="migration_adr_batch_qualification",
    ):
        tracker.migration_preflight(project, (adr,))
    assert tracker.posts == 0


def test_linear_migration_uses_one_closed_adr_batch(monkeypatch):
    tracker = _LinearMigrationTransport()
    snapshot = {
        "kind": "adr", "id": "SRC-ADR-0001", "title": "Decision",
        "status": "accepted", "body": "exact", "source_ref": "youtrack:adr:1",
        "relations": {
            "supersedes": [], "superseded_by": None, "issues": "unknown",
        },
        "source_created": None, "source_updated": None,
    }
    [base] = tracker._migration_adr_batch_records((snapshot,))
    project = Project(
        "DST", "project-uuid", extra={"migration_adr_batch_profile": [base]},
    )
    called = []
    monkeypatch.setattr(
        tracker, "import_adr_batch",
        lambda supplied, records: called.append((supplied, records)) or [
            Adr("SRC-ADR-0001", "Decision", "accepted", "exact", "doc")
        ],
    )
    result = tracker.migration_import_adrs(project, (snapshot,))
    assert [item.id for item in result] == ["SRC-ADR-0001"]
    assert called == [(project, (base,))]


def test_github_adapter_reuses_provider_label_candidate_after_lost_response(
    monkeypatch, tmp_path,
):
    project = Project(
        "DST", "PVT_project", extra={
            "owner": "acme", "number": "1", "canonical_repo": "github.com/acme/dst",
            "migration_label": "foundry-migration",
        },
    )
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    candidate = _CreateCandidate("DST-7", 7, 1007, "node-7")
    provider = {"candidate": None, "posts": 0}
    monkeypatch.setattr(tracker, "_ensure_migration_label", lambda *args: "foundry-migration-abc")
    monkeypatch.setattr(tracker, "verify_project_identity", lambda *_: True)
    monkeypatch.setattr(tracker, "_write_catalog", lambda *_: {"type": object()})
    monkeypatch.setattr(tracker, "_migration_candidate", lambda *_: provider["candidate"])

    def lost_run(*args, **kwargs):
        provider["posts"] += 1
        provider["candidate"] = candidate
        raise RuntimeError("lost after provider commit")

    monkeypatch.setattr(tracker, "_run", lost_run)
    monkeypatch.setattr(
        tracker, "_resume_created_issue",
        lambda *args: Issue(
            "DST-7", "exact title", state="ready", normalized_state="ready",
            body="- [x] exact byte body\n", type="Feature", priority="P1", estimate=3,
        ),
    )
    with pytest.raises(RuntimeError, match="lost"):
        tracker.migration_import_issue(
            project, _issue_record(), source_ref="youtrack:issue:SRC-1"
        )
    # The provider label is authoritative even if the local process missed the
    # response: a fresh instance/path resumes the same candidate without POST.
    monkeypatch.setattr(tracker, "_run", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("duplicate GitHub POST")
    ))
    result = tracker.migration_import_issue(
        project, _issue_record(), source_ref="youtrack:issue:SRC-1"
    )
    assert result.id == "DST-7"
    assert provider["posts"] == 1


def test_github_duplicate_provider_label_candidates_fail_closed(monkeypatch, tmp_path):
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    binding = tracker._binding(Project(
        "DST", "PVT_project", extra={
            "owner": "acme", "number": "1",
            "canonical_repo": "github.com/acme/dst",
        },
    ))
    monkeypatch.setattr(tracker, "_rows", lambda *_: [
        {"number": 1, "id": 101, "node_id": "N1", "labels": [{"name": "source"}]},
        {"number": 2, "id": 102, "node_id": "N2", "labels": [{"name": "source"}]},
    ])
    monkeypatch.setattr(
        tracker, "_issue_number_from_rest", lambda raw, *_: raw["number"],
    )
    with pytest.raises(TrackerConflictError, match="ambiguë"):
        tracker._migration_candidate(binding, "source")


def test_github_adr_migration_uses_explicit_target_and_incomplete_graph_mode(
    monkeypatch, tmp_path,
):
    project = Project(
        "DST", "PVT_project", extra={
            "owner": "acme", "number": "1",
            "canonical_repo": "github.com/acme/dst",
            "migration_label": "foundry-migration",
        },
    )
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    calls = {}
    monkeypatch.setattr(
        tracker, "_adr_snapshot",
        lambda supplied, **kwargs: (
            tracker._binding(supplied), {},
        ),
    )
    monkeypatch.setattr(
        tracker, "_ensure_migration_label",
        lambda binding, supplied, source_ref: "foundry-migration-deadbeef",
    )

    def create(supplied, title, body, status, **kwargs):
        calls.update(
            project=supplied, title=title, body=body, status=status, **kwargs,
        )
        return Adr("DST-ADR-0002", title, status, body, "7")

    monkeypatch.setattr(tracker, "create_adr", create)
    source_snapshot = {
        "id": "SRC-ADR-0002", "title": "Décision exacte",
        "status": "accepted", "body": "octets exacts\n",
        "relations": {
            "supersedes": ["SRC-ADR-0001"], "superseded_by": None,
            "issues": ["DST-2"],
        },
    }
    snapshot = tracker.migration_prepare_adr(project, source_snapshot)
    assert snapshot["id"] == "DST-ADR-0002"
    assert snapshot["relations"]["supersedes"] == ["DST-ADR-0001"]
    result = tracker.migration_import_adr(
        project, snapshot, source_ref="youtrack:adr:SRC-ADR-0002",
    )
    assert result.body == "octets exacts\n"
    assert calls["project"] == project
    assert calls["status"] == "accepted"
    assert calls["_allow_incomplete_graph"] is True
    assert calls["_migration"]["source_ref"] == "youtrack:adr:SRC-ADR-0002"
    assert calls["_migration"]["relations"] == snapshot["relations"]
