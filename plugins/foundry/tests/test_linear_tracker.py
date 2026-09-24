"""Controlled Linear transport proof; no test contacts a Linear workspace."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import foundry
from foundry import evidence_plane, issue, query, registry, routing, write
from foundry.trackers import linear as linear_module
from foundry.models import (
    Adr,
    Check,
    Issue,
    Link,
    Project,
    PullRequest,
    TransitionContext,
)
from foundry.routing import acceptance_criteria, acceptance_digest
from foundry.trackers.base import (
    AcceptanceSyncUnavailableError,
    AdrUnavailableError,
    BodyUpdateUnavailableError,
    IssueUnavailableError,
    Tracker,
    TrackerCapabilityUnavailableError,
    TrackerConflictError,
)
from foundry.trackers.devhub import DevHubTracker
from foundry.trackers.linear import (
    LinearBindingError,
    LinearTracker,
    LinearTrackerError,
)
from foundry.trackers.youtrack import YouTrackTracker


STATE_IDS = {
    "backlog": "state-backlog",
    "ready": "state-ready",
    "in-progress": "state-in-progress",
    "review": "state-review",
    "blocked": "state-blocked",
    "done": "state-done",
    "dropped": "state-dropped",
}
ISSUE_1_ID = "00000000-0000-4000-8000-000000000001"
ISSUE_2_ID = "00000000-0000-4000-8000-000000000002"
PROJECT = Project(
    key="LIN",
    id="project-uuid",
    extra={
        "canonical_repo": "github.com/acme/widgets",
        "team_id": "team-uuid",
        "state_ids": STATE_IDS,
        "milestone_ids": {"M1": "milestone-m1"},
        "type_label_ids": {"Feature": "label-feature", "Bug": "label-bug"},
        "label_ids": {"pilot": "label-pilot", "api": "label-api"},
    },
)


def connection(nodes):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": False, "endCursor": None}}


def raw_issue(identifier, native_id, *, title="Issue", body="- [ ] acceptance"):
    return {
        "id": native_id,
        "identifier": identifier,
        "title": title,
        "description": body,
        "priority": 2,
        "estimate": 3,
        "createdAt": "2026-09-20T10:00:00Z",
        "updatedAt": "2026-09-20T10:01:00Z",
        "state": {"id": STATE_IDS["ready"], "name": "Ready"},
        "team": {"id": "team-uuid", "key": "LIN"},
        "project": {"id": "project-uuid"},
        "projectMilestone": {"id": "milestone-m1", "name": "ignored"},
        "labels": connection(
            [
            {"id": "label-feature", "name": "Feature display"},
            {"id": "label-pilot", "name": "Pilot display"},
            ]
        ),
        "parent": None,
        "children": connection([]),
        "relations": connection([]),
        "inverseRelations": connection([]),
        "comments": connection([]),
    }


class LinearWire:
    """Stateful GraphQL fake retaining exact native identifiers across mutations."""

    def __init__(self):
        self.issues = {
            "LIN-1": raw_issue("LIN-1", ISSUE_1_ID, title="Parent"),
            "LIN-2": raw_issue("LIN-2", ISSUE_2_ID, title="Existing"),
        }
        self.comments = {}
        self.documents = {}
        self.calls = []
        self.git_automation_states = connection([])

    def _by_native(self, native):
        return next(value for value in self.issues.values() if value["id"] == native)

    def _by_reference(self, reference):
        if not isinstance(reference, str):
            return None
        matches = [
            value
            for identifier, value in self.issues.items()
            if value["id"] == reference
            or identifier.casefold() == reference.casefold()
        ]
        if len(matches) > 1:
            raise AssertionError("ambiguous fake Linear issue reference")
        return matches[0] if matches else None

    def __call__(self, document, variables):
        self.calls.append((document, copy.deepcopy(variables)))
        if "FoundryLinearAdrDocuments" in document:
            nodes = [
                copy.deepcopy(value)
                for value in self.documents.values()
                if value["project"]["id"] == variables["projectId"]
            ]
            return {"data": {"documents": connection(nodes)}}
        # Linear's singular document(id:) rejects an unknown UUID with GraphQL
        # "Entity not found", rather than returning null.  ADR slot reads must
        # consequently use the collection query below.
        if "document(id:" in document:
            return {"errors": [{"message": "Entity not found: Document"}], "data": {}}
        if "FoundryLinearAdrDocumentById" in document:
            document_id = variables["id"]
            nodes = [
                copy.deepcopy(value)
                for value in self.documents.values()
                if value["id"] == document_id
            ]
            return {"data": {"documents": connection(nodes)}}
        if "FoundryLinearAdrDocumentCreate" in document:
            value = variables["input"]
            try:
                valid_uuid = uuid.UUID(value["id"]).version == 4
            except (KeyError, TypeError, ValueError):
                valid_uuid = False
            if not valid_uuid:
                return {"errors": [{"message": "Document id must be UUID v4"}], "data": {}}
            if value["id"] in self.documents:
                return {"errors": [{"message": "duplicate id"}], "data": {}}
            created = {
                "id": value["id"],
                "title": value["title"],
                "content": value["content"],
                "archivedAt": None,
                "project": {"id": value["projectId"]},
            }
            self.documents[value["id"]] = copy.deepcopy(created)
            return {
                "data": {
                    "documentCreate": {
                        "success": True,
                        "document": copy.deepcopy(created),
                    }
                }
            }
        if "FoundryLinearTeamGitAutomationStates" in document:
            return {
                "data": {
                    "team": {
                "id": "team-uuid",
                        "gitAutomationStates": copy.deepcopy(
                            self.git_automation_states
                        ),
                    }
                }
            }
        if "FoundryLinearIssues" in document:
            return {
                "data": {
                    "issues": connection(
                        [copy.deepcopy(v) for v in self.issues.values()]
                    )
                }
            }
        if "FoundryLinearIssue(" in document:
            return {
                "data": {
                    "issue": copy.deepcopy(self._by_reference(variables["id"]))
                }
            }
        if "FoundryLinearIssueCreate" in document:
            value = variables["input"]
            identifier = f"LIN-{len(self.issues) + 1}"
            created = raw_issue(
                identifier, value["id"], title=value["title"], body=value["description"]
            )
            self._apply(created, value)
            if parent_id := value.get("parentId"):
                parent = self._by_native(parent_id)
                created["parent"] = {
                    "id": parent["id"],
                    "identifier": parent["identifier"],
                }
                parent["children"]["nodes"].append(
                    {"id": created["id"], "identifier": created["identifier"]}
                )
            self.issues[identifier] = created
            return {
                "data": {
                    "issueCreate": {
                        "success": True,
                        "issue": {"id": created["id"], "identifier": identifier},
                    }
                }
            }
        if "FoundryLinearIssueRelationCreate" in document:
            value = variables["input"]
            source, target = (
                self._by_native(value["issueId"]),
                self._by_native(value["relatedIssueId"]),
            )
            source["relations"]["nodes"].append(
                {
                "type": value["type"],
                    "relatedIssue": {
                        "id": target["id"],
                        "identifier": target["identifier"],
                    },
                }
            )
            target["inverseRelations"]["nodes"].append(
                {
                "type": value["type"],
                "issue": {"id": source["id"], "identifier": source["identifier"]},
                }
            )
            relation = {
                "id": "relation-1",
                "type": value["type"],
                "issue": {"id": source["id"], "identifier": source["identifier"]},
                "relatedIssue": {
                    "id": target["id"],
                    "identifier": target["identifier"],
                },
            }
            return {
                "data": {
                    "issueRelationCreate": {"success": True, "issueRelation": relation}
                }
            }
        if "FoundryLinearCommentCreate" in document:
            value = variables["input"]
            if "id" in value:
                try:
                    valid_uuid = uuid.UUID(value["id"]).version == 4
                except (TypeError, ValueError):
                    valid_uuid = False
                if not valid_uuid:
                    return {"errors": [{"message": "Comment id must be UUID v4"}], "data": {}}
            issue = self._by_native(value["issueId"])
            comment = {
                "id": value.get("id", f"comment-{len(self.comments) + 1}"),
                "body": value["body"],
                "issue": {"id": issue["id"], "identifier": issue["identifier"]},
            }
            if comment["id"] in self.comments:
                return {"errors": [{"message": "duplicate"}], "data": {}}
            self.comments[comment["id"]] = comment
            issue["comments"]["nodes"].append(
                {
                    "id": comment["id"],
                    "body": comment["body"],
                "createdAt": "2026-09-20T10:02:00Z",
                }
            )
            return {
                "data": {
                    "commentCreate": {
                        "success": True,
                        "comment": copy.deepcopy(comment),
                    }
                }
            }
        if "FoundryLinearCommentsById(" in document:
            comment = self.comments.get(variables["id"])
            return {
                "data": {
                    "comments": connection(
                [] if comment is None else [copy.deepcopy(comment)]
                    )
                }
            }
        raise AssertionError("unexpected GraphQL document")

    @staticmethod
    def _apply(issue, values):
        if "description" in values:
            issue["description"] = values["description"]
        if "stateId" in values:
            issue["state"] = {
                "id": values["stateId"],
                "name": "provider display ignored",
            }
        if "priority" in values:
            issue["priority"] = values["priority"]
        if "estimate" in values:
            issue["estimate"] = values["estimate"]
        if "projectMilestoneId" in values:
            issue["projectMilestone"] = (
                {"id": values["projectMilestoneId"], "name": "provider display ignored"}
                if values["projectMilestoneId"]
                else None
            )
        if "labelIds" in values:
            names = {
                "label-feature": "Feature display",
                "label-bug": "Bug display",
                "label-pilot": "Pilot display",
                "label-api": "API display",
            }
            issue["labels"] = connection(
                [{"id": item, "name": names[item]} for item in values["labelIds"]]
            )
        if "parentId" in values:
            parent = values["parentId"]
            issue["parent"] = (
                None
                if parent is None
                else {
                    "id": parent,
                    "identifier": "LIN-1",
            }
            )


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    wire = LinearWire()
    return LinearTracker(token="linear-test-secret", transport=wire), wire


def proof(issue_id, body, *, head="a" * 40, base="b" * 40, diff_hash="c" * 64):
    criteria = acceptance_criteria(body)
    value = {
        "schema_version": 1,
        "issue": {
            "id": issue_id,
            "ac_digest": acceptance_digest(criteria),
            "criteria": [{**criterion, "verdict": "pass"} for criterion in criteria],
        },
        "review": {
            "role": "reviewer",
            "generation": 1,
            "claim_digest": "d" * 64,
        },
        "coordinates": {"head": head, "diff_hash": diff_hash, "base": base},
        "quality": "mergeable",
    }
    import hashlib
    import json

    value["proof_id"] = hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return value


def project_entry(project=PROJECT):
    return {"key": project.key, "id": project.id, **copy.deepcopy(project.extra)}


def seed_linear_adr(
    wire,
    *,
    adr_id,
    status,
    supersedes=(),
    superseded_by=None,
):
    binding = {"project_id": PROJECT.id, "team_id": PROJECT.extra["team_id"]}
    body = f"seed body for {adr_id}"
    digest = hashlib.sha256(body.encode()).hexdigest()
    metadata = {
        "schema": linear_module._ADR_SCHEMA,
        "project_id": binding["project_id"],
        "team_id": binding["team_id"],
        "id": adr_id,
        "title": f"Seed {adr_id}",
        "status": status,
        "sequence": 0,
        "previous_id": None,
        "previous_sha256": None,
        "body_sha256": digest,
        "origin": {
            "kind": "migration",
            "source_tracker": "youtrack",
            "source_ref": f"YT-{adr_id}",
            "source_created": None,
            "source_updated": None,
            "source_body_sha256": digest,
        },
        "relations": {
            "supersedes": list(supersedes),
            "superseded_by": superseded_by,
            "issues": [],
        },
    }
    raw = {
        "id": linear_module._adr_document_id(PROJECT.id, adr_id, 0),
        "title": linear_module._adr_document_title(metadata),
        "content": linear_module._adr_document_content(metadata, body),
        "project": {"id": PROJECT.id},
        "archivedAt": None,
    }
    linear_module._parse_adr_document(raw, binding)
    witness = linear_module._adr_witness_document(binding, metadata, raw)
    wire.documents[raw["id"]] = raw
    wire.documents[witness["id"]] = witness
    return Adr(
        id=adr_id,
        title=metadata["title"],
        status=status,
        body=raw["content"],
        ref=raw["id"],
    )


def seed_linear_adr_relation_boundary(wire, relation_count):
    replacement_id = "LIN-ADR-9000"
    target_ids = tuple(
        f"LIN-ADR-{number:04d}" for number in range(1, relation_count + 1)
    )
    for target_id in target_ids:
        seed_linear_adr(
            wire,
            adr_id=target_id,
            status="superseded",
            superseded_by=replacement_id,
        )
    replacement = seed_linear_adr(
        wire,
        adr_id=replacement_id,
        status="accepted",
        supersedes=target_ids,
    )
    source = seed_linear_adr(
        wire,
        adr_id="LIN-ADR-8000",
        status="accepted",
    )
    return source, replacement


def test_factory_recognizes_linear_without_changing_youtrack_devhub_or_stub(
    monkeypatch,
):
    secrets = {
        "YOUTRACK_URL": "https://example.youtrack.cloud",
        "YOUTRACK_TOKEN": "youtrack-secret",
        "LINEAR_API_TOKEN": "linear-secret",
        "DEVHUB_TRACKER_TOKEN": "t" * 24,
        "DEVHUB_TRACKER_PROOF_SECRET": "p" * 32,
    }
    monkeypatch.setattr(foundry.config, "require", secrets.__getitem__)
    monkeypatch.setattr(
        foundry.config,
        "require_public",
        lambda key: "https://devhub.example.test",
    )
    assert isinstance(foundry.tracker("linear"), LinearTracker)
    assert isinstance(foundry.tracker("youtrack"), YouTrackTracker)
    assert isinstance(foundry.tracker("devhub"), DevHubTracker)
    assert foundry.tracker("ghprojects").name == "ghprojects"


def test_binding_requires_explicit_repository_team_project_and_all_state_ids(tracker):
    instance, _ = tracker
    broken = Project(key="LIN", id="project-uuid", extra={"team_id": "team-uuid"})
    with pytest.raises(LinearBindingError, match="canonical_repo_invalid"):
        instance.search(broken)

    missing_state = copy.deepcopy(PROJECT)
    missing_state.extra["state_ids"] = {"ready": "state-ready"}
    with pytest.raises(LinearBindingError, match="state_ids_incomplete"):
        instance.search(missing_state)

    overlapping_labels = copy.deepcopy(PROJECT)
    overlapping_labels.extra["label_ids"]["pilot"] = "label-feature"
    with pytest.raises(LinearBindingError, match="label_ids_overlap"):
        instance.search(overlapping_labels)


def test_controlled_round_trip_retains_ids_and_safe_additive_writes_on_fresh_reader(
    tracker,
    monkeypatch,
):
    instance, wire = tracker
    listed = instance.search(PROJECT)
    assert listed[0].id == "LIN-1"
    assert listed[0].state == "ready"
    assert listed[0].milestone == "M1"
    assert listed[0].type == "Feature"
    assert listed[0].labels == ["pilot"]

    created = instance.create_issue(
        PROJECT,
        "Round trip",
        "- [ ] exact acceptance",
        fields={
            "State": "ready",
            "Priority": "P1",
            "Estimate": 5,
            "Milestone": "M1",
            "Type": "Feature",
            "Labels": ["pilot"],
        },
        parent="LIN-1",
    )
    assert created.id == "LIN-3"
    assert Link("subtask-of", "inward", "LIN-1") in created.links

    instance.link(created.id, "depends-on", "LIN-2", project=PROJECT)
    instance.add_comment(created.id, "bounded progress", project=PROJECT)

    fresh = LinearTracker(token="linear-test-secret", transport=wire)
    with pytest.raises(LinearBindingError, match="project_not_resolved"):
        fresh.get_issue(created.id)
    monkeypatch.setenv("PROJECT_REPO", "decoy")
    monkeypatch.setattr(
        registry,
        "checkout_repository_identity",
        lambda cwd=None: "github.com/acme/widgets",
    )
    monkeypatch.setattr(
        registry,
        "load",
        lambda: {
            "linear": {
        "actual-checkout": project_entry(),
            }
        },
    )
    fresh.resolve_project("decoy")
    final = fresh.get_issue(created.id)
    assert final.state == "ready"
    assert final.pr_url is None
    assert (final.priority, final.estimate, final.type, final.labels) == (
        "P1",
        5,
        "Feature",
        ["pilot"],
    )
    assert final.body == "- [ ] exact acceptance" and (
        final.ac_done,
        final.ac_total,
    ) == (0, 1)
    assert Link("depends-on", "outward", "LIN-2") in final.links
    assert wire.comments["comment-1"]["body"] == "bounded progress"


def test_relation_reads_normalize_hierarchy_and_both_blocking_sides(tracker):
    instance, wire = tracker
    parent, child = wire.issues["LIN-1"], wire.issues["LIN-2"]
    parent["children"] = connection(
        [
        {"id": child["id"], "identifier": child["identifier"]},
        ]
    )
    child["parent"] = {"id": parent["id"], "identifier": parent["identifier"]}
    parent["relations"] = connection(
        [
            {
        "type": "blocks",
        "relatedIssue": {"id": child["id"], "identifier": child["identifier"]},
            }
        ]
    )
    child["inverseRelations"] = connection(
        [
            {
        "type": "blocks",
        "issue": {"id": parent["id"], "identifier": parent["identifier"]},
            }
        ]
    )

    issues = {item.id: item for item in instance.search(PROJECT)}

    assert issues["LIN-1"].links == [
        Link("parent-of", "outward", "LIN-2"),
        Link("blocks", "inward", "LIN-2"),
    ]
    assert issues["LIN-2"].links == [
        Link("subtask-of", "inward", "LIN-1"),
        Link("depends-on", "outward", "LIN-1"),
    ]


@pytest.mark.parametrize("relation_type", ["duplicate", "similar"])
@pytest.mark.parametrize(
    ("collection_name", "target_name", "operation"),
    [
        ("relations", "relatedIssue", "normalize.relations"),
        ("inverseRelations", "issue", "normalize.inverse-relations"),
    ],
)
def test_relation_reads_refuse_unsupported_native_types_without_partial_issue(
    tracker,
    relation_type,
    collection_name,
    target_name,
    operation,
):
    instance, wire = tracker
    wire.issues["LIN-1"][collection_name] = connection(
        [
            {
        "type": relation_type,
        target_name: {"id": ISSUE_2_ID, "identifier": "LIN-2"},
            }
        ]
    )

    with pytest.raises(LinearTrackerError) as raised:
        instance.search(PROJECT)

    assert raised.value.operation == operation
    assert raised.value.status is None
    assert raised.value.code == "unsupported_relation_type"


def test_existing_issue_replacements_are_unavailable_before_provider_write(tracker):
    instance, wire = tracker
    before = len(wire.calls)
    assert instance.acceptance_sync_supported is False
    with pytest.raises(
        TrackerCapabilityUnavailableError,
        match="existing-issue-field-replacement",
    ):
        instance.update_fields("LIN-2", {"Priority": "P0"}, project=PROJECT)
    with pytest.raises(
        TrackerCapabilityUnavailableError,
        match="lifecycle-proof",
    ):
        instance.set_state("LIN-2", "review", project=PROJECT)
    with pytest.raises(
        TrackerCapabilityUnavailableError,
        match="existing-issue-parent-replacement",
    ):
        instance.link("LIN-2", "subtask-of", "LIN-1", project=PROJECT)
    with pytest.raises(BodyUpdateUnavailableError, match="anti-écrasement"):
        instance.update_body(
            Issue(id="LIN-2", title="Existing"),
            "old",
            "new",
            project=PROJECT,
        )
    with pytest.raises(AcceptanceSyncUnavailableError, match="anti-écrasement"):
        instance.sync_acceptance_body(
            "LIN-2",
            "- [ ] AC",
            "- [x] AC",
            proof("LIN-2", "- [ ] AC"),
            project=PROJECT,
        )
    assert len(wire.calls) == before


def test_normalization_rejects_missing_or_unknown_label_identifiers(tracker):
    instance, wire = tracker
    del wire.issues["LIN-1"]["labels"]["nodes"][0]["id"]
    with pytest.raises(LinearTrackerError) as missing:
        instance.search(PROJECT)
    assert missing.value.code == "invalid_response"

    wire.issues["LIN-1"] = raw_issue("LIN-1", ISSUE_1_ID, title="Parent")
    wire.issues["LIN-1"]["labels"]["nodes"][0]["id"] = "label-unknown"
    with pytest.raises(LinearBindingError) as unknown:
        instance.search(PROJECT)
    assert unknown.value.code == "label_id_unmapped"


def test_normalization_rejects_unmapped_milestone_identifier(tracker):
    instance, wire = tracker
    wire.issues["LIN-1"]["projectMilestone"] = {
        "id": "milestone-unknown",
        "name": "M1",
    }
    with pytest.raises(LinearBindingError) as error:
        instance.search(PROJECT)
    assert error.value.code == "milestone_id_unmapped"


def test_query_issue_resolves_fresh_binding_and_projects_linear_adrs(
    tracker,
    monkeypatch,
):
    _, wire = tracker
    fresh = LinearTracker(token="linear-test-secret", transport=wire)
    checkout_reads = []
    decoy = copy.deepcopy(project_entry())
    decoy.update(
        {
            "key": "WRONG",
            "id": "wrong-project",
        "canonical_repo": "github.com/acme/not-widgets",
        }
    )

    monkeypatch.setattr(foundry, "tracker", lambda name=None: fresh)
    monkeypatch.setenv("PROJECT_REPO", "widgets")
    monkeypatch.setattr(
        registry,
        "checkout_repository_identity",
        lambda cwd=None: checkout_reads.append(cwd) or "github.com/acme/widgets",
    )
    monkeypatch.setattr(
        registry,
        "load",
        lambda: {
            "linear": {
        "widgets": decoy,
        "actual-checkout": project_entry(),
        "actual-alias": project_entry(),
            }
        },
    )
    monkeypatch.setattr(
        registry,
        "resolve",
        lambda *_args: pytest.fail("Linear query must not resolve from a basename"),
    )

    result = query.issue("LIN-2")

    assert checkout_reads == [None]
    assert fresh._active_project == PROJECT
    assert result["issue"]["id"] == "LIN-2"
    assert result["adrs"] == []


def test_record_review_proof_resolves_fresh_binding_before_issue_read(
    tracker,
    monkeypatch,
    tmp_path,
    capsys,
):
    _, wire = tracker
    fresh = LinearTracker(token="linear-test-secret", transport=wire)
    checkout_reads = []
    captured = {}

    class ProofStore:
        def __init__(self, repository):
            captured["repository"] = repository

        def create(self, **kwargs):
            captured.update(kwargs)
            return {"proof_id": "f" * 64}

    outcomes = tmp_path / "outcomes.json"
    outcomes.write_text(
        '{"outcomes": [], "quality": {"verdict": "pass"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(foundry, "tracker", lambda name=None: fresh)
    monkeypatch.setenv("PROJECT_REPO", "decoy")
    monkeypatch.setattr(
        registry,
        "checkout_repository_identity",
        lambda cwd=None: checkout_reads.append(cwd) or "github.com/acme/widgets",
    )
    monkeypatch.setattr(
        registry,
        "load",
        lambda: {
            "linear": {
        "decoy": {
                    **project_entry(),
                    "key": "WRONG",
                    "id": "wrong-project",
            "canonical_repo": "github.com/acme/other",
        },
        "canonical": project_entry(),
            }
        },
    )
    monkeypatch.setattr(
        registry,
        "resolve",
        lambda *_args: pytest.fail("Linear proof must not resolve from a basename"),
    )
    monkeypatch.setattr(
        routing, "repository_identity", lambda root=None: "acme/widgets"
    )
    monkeypatch.setattr(routing, "AcceptanceProofStore", ProofStore)

    routing.main(
        [
        "record-review-proof",
            "--issue",
            "LIN-2",
            "--diff-hash",
            "d" * 64,
            "--claim-id",
            "c" * 64,
            "--outcomes-file",
            str(outcomes),
            "--base",
            "b" * 40,
            "--root",
            str(tmp_path),
        ]
    )

    assert checkout_reads == [str(tmp_path)]
    assert captured["repository"] == "acme/widgets"
    assert captured["issue_id"] == "LIN-2"
    assert captured["issue_body"] == "- [ ] acceptance"
    assert '"proof_id": "' + "f" * 64 + '"' in capsys.readouterr().out


def test_checkout_resolution_refuses_without_canonical_binding_before_linear_read(
    tracker,
    monkeypatch,
):
    _, wire = tracker
    fresh = LinearTracker(token="linear-test-secret", transport=wire)
    monkeypatch.setattr(foundry, "tracker", lambda name=None: fresh)
    monkeypatch.setenv("PROJECT_REPO", "widgets")
    monkeypatch.setattr(
        registry,
        "checkout_repository_identity",
        lambda cwd=None: "github.com/acme/widgets",
    )
    monkeypatch.setattr(
        registry,
        "load",
        lambda: {
            "linear": {
        "widgets": {
                    **project_entry(),
                    "canonical_repo": "github.com/acme/other",
        },
            }
        },
    )

    with pytest.raises(SystemExit, match="canonical_repo du checkout"):
        query.issue("LIN-2")

    assert all(
        "FoundryLinearAdrDocumentCreate" not in document for document, _ in wire.calls
    )


def test_checkout_resolution_refuses_contradictory_canonical_bindings(
    tracker,
    monkeypatch,
):
    _, wire = tracker
    fresh = LinearTracker(token="linear-test-secret", transport=wire)
    contradictory = project_entry()
    contradictory.update({"key": "OTHER", "id": "other-project"})
    monkeypatch.setattr(
        registry,
        "load",
        lambda: {
            "linear": {
        "first": project_entry(),
        "second": contradictory,
            }
        },
    )

    with pytest.raises(SystemExit, match="ambigus"):
        fresh.resolve_checkout_project(
            checkout_identity="github.com/acme/widgets",
        )

    assert wire.calls == []


@pytest.mark.parametrize("provider_class", [YouTrackTracker, DevHubTracker])
def test_existing_provider_operation_preflights_remain_noop(provider_class):
    provider = object.__new__(provider_class)
    assert provider.preflight_issue_operation("openpr") is None
    assert provider.preflight_issue_operation("merge") is None


@pytest.mark.parametrize("operation", ["openpr", "merge"])
def test_linear_lifecycle_preflight_stops_before_every_codehost_effect_when_disabled(
    tracker,
    monkeypatch,
    operation,
):
    instance, wire = tracker
    instance.append_only_lifecycle_supported = False
    instance.acceptance_proof_projection_supported = False
    effects = []
    codehost = SimpleNamespace(
        resolve_repo=lambda: effects.append("resolve-repo") or "acme/widgets",
        list_prs=lambda *_args: effects.append("list-prs") or [],
        open_pr=lambda *_args: effects.append("open-pr"),
        update_pr=lambda *_args: effects.append("update-pr"),
        get_pr=lambda *_args: effects.append("get-pr"),
        merge_pr=lambda *_args, **_kwargs: effects.append("merge-pr"),
        delete_branch=lambda *_args: effects.append("delete-branch"),
    )
    monkeypatch.setattr(foundry, "tracker", lambda name=None: instance)
    monkeypatch.setattr(foundry, "codehost", lambda name=None, cwd=None: codehost)
    monkeypatch.setenv("PROJECT_REPO", "decoy")
    monkeypatch.setattr(
        registry,
        "checkout_repository_identity",
        lambda cwd=None: "github.com/acme/widgets",
    )
    monkeypatch.setattr(
        registry,
        "load",
        lambda: {
            "linear": {
        "decoy": {
                    **project_entry(),
                    "key": "WRONG",
                    "id": "wrong-project",
            "canonical_repo": "github.com/acme/other",
        },
        "canonical": project_entry(),
            }
        },
    )

    def command(*args, **_kwargs):
        if args[-2:] == ("--abbrev-ref", "HEAD"):
            return "feat/lin-2-safe"
        effects.append(("git", args))
        return ""

    monkeypatch.setattr(issue, "_sh", command)

    with pytest.raises(
        TrackerCapabilityUnavailableError,
        match="linear.append-only-lifecycle-proof",
    ):
        if operation == "openpr":
            issue.openpr("LIN-2")
        else:
            issue.merge("LIN-2", "17")

    assert effects == []
    assert wire.calls and all(
        "mutation" not in document.lower() for document, _ in wire.calls
    )


def test_linear_lifecycle_preflight_advertises_only_bounded_operations(tracker):
    instance, wire = tracker
    before = len(wire.calls)

    assert instance.preflight_issue_operation("openpr") is None
    assert instance.preflight_issue_operation("merge") is None
    with pytest.raises(
        TrackerCapabilityUnavailableError,
        match="linear.lifecycle:close-epic",
    ):
        instance.preflight_issue_operation("close-epic")

    assert len(wire.calls) == before


def test_linear_merge_effect_preflight_allows_safe_team_automation(tracker):
    instance, wire = tracker
    wire.git_automation_states = connection(
        [
        {
                "id": "automation-started",
                "event": "start",
            "state": {"id": STATE_IDS["in-progress"], "type": "started"},
        },
        {
                "id": "automation-review",
                "event": "review",
            "state": {"id": STATE_IDS["review"], "type": "started"},
        },
        ]
    )
    instance._activate(PROJECT)

    assert instance.preflight_merge_effect() is None
    assert wire.calls[-1][1] == {"id": "team-uuid"}


def test_linear_merge_effect_preflight_allows_merge_no_action(tracker):
    instance, wire = tracker
    wire.git_automation_states = connection(
        [
        {"id": "automation-merge-no-action", "event": "merge", "state": None},
        ]
    )
    instance._activate(PROJECT)
    merge_calls = []

    write.preflight_merge_effect(instance)
    merge_calls.append("github-merge")

    assert merge_calls == ["github-merge"]


def test_linear_merge_effect_preflight_allows_duplicate_workflow_state(tracker):
    instance, wire = tracker
    wire.git_automation_states = connection(
        [
        {
                "id": "automation-merge-duplicate",
                "event": "merge",
            "state": {"id": "duplicate-state", "type": "duplicate"},
        },
        ]
    )
    instance._activate(PROJECT)

    assert instance.preflight_merge_effect() is None


def test_linear_merge_effect_preflight_refuses_completed_merge_automation(tracker):
    instance, wire = tracker
    wire.git_automation_states = connection(
        [
        {
                "id": "automation-merge",
                "event": "merge",
            "state": {"id": STATE_IDS["done"], "type": "completed"},
        },
        ]
    )
    instance._activate(PROJECT)
    linked_pr = SimpleNamespace(linked_issue_ids=("LIN-2", "LIN-1"))
    merge_calls = []

    def external_merge_effect():
        # Mirrors the final Foundry pre-merge boundary: the code-host effect is
        # external, and must not run when Linear would complete both linked issues.
        write.preflight_merge_effect(instance)
        merge_calls.append(linked_pr.linked_issue_ids)

    with pytest.raises(
        TrackerConflictError,
        match="merge maps to completed state",
    ):
        external_merge_effect()

    assert linked_pr.linked_issue_ids == ("LIN-2", "LIN-1")
    assert merge_calls == []


def test_linear_merge_effect_preflight_fails_closed_on_unreadable_team_config(tracker):
    instance, wire = tracker
    wire.git_automation_states = {
        "nodes": [],
        "pageInfo": {"hasNextPage": True, "endCursor": "next"},
    }
    instance._activate(PROJECT)

    with pytest.raises(LinearTrackerError, match="nested_pagination_unavailable"):
        instance.preflight_merge_effect()


def test_linear_lifecycle_projects_state_pr_and_acceptance_without_replacement(
    tracker,
    monkeypatch,
):
    instance, wire = tracker
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40,
        base_sha="b" * 40,
        review_digest="c" * 64,
    )
    done = TransitionContext(
        pr_url=review.pr_url,
        head_sha=review.head_sha,
        base_sha=review.base_sha,
        review_digest=review.review_digest,
        merge_sha="e" * 40,
    )

    instance.set_state("LIN-2", "in-progress", project=PROJECT)
    instance.set_state("LIN-2", "review", context=review, project=PROJECT)
    result = write.sync_acceptance(
        instance,
        "LIN-2",
        "- [ ] acceptance",
        proof("LIN-2", "- [ ] acceptance"),
    )
    instance.set_state("LIN-2", "done", context=done, project=PROJECT)

    projected = instance.get_issue("LIN-2")
    assert projected.state == "done"
    assert projected.pr_url == review.pr_url
    assert (projected.ac_done, projected.ac_total) == (1, 1)
    assert result == {
        "status": "proof-projected",
        "checked": 1,
        "audit": "append-only-proof",
    }
    native = wire.issues["LIN-2"]
    assert native["state"]["id"] == STATE_IDS["ready"]
    assert native["description"] == "- [ ] acceptance"
    assert native["priority"] == 2
    assert len(native["comments"]["nodes"]) == 4
    assert all(
        "Foundry lifecycle proof" in row["body"] for row in native["comments"]["nodes"]
    )


def test_linear_lifecycle_replay_is_idempotent_and_uses_deterministic_comment_id(
    tracker,
):
    instance, wire = tracker
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40,
        base_sha="b" * 40,
        review_digest="c" * 64,
    )

    instance.set_state("LIN-2", "review", context=review, project=PROJECT)
    comment_ids = tuple(wire.comments)
    instance.set_state("LIN-2", "review", context=review, project=PROJECT)

    assert tuple(wire.comments) == comment_ids
    assert len(comment_ids) == 1
    comment_id = comment_ids[0]
    parsed = uuid.UUID(comment_id)
    assert comment_id == "e16710eb-46d7-43d8-ba0d-0931e9882d7b"
    assert parsed.version == 4
    assert parsed.variant == uuid.RFC_4122


def test_linear_lifecycle_refuses_comment_with_mismatched_deterministic_id(tracker):
    instance, wire = tracker
    instance.set_state("LIN-2", "in-progress", project=PROJECT)
    persisted = wire.issues["LIN-2"]["comments"]["nodes"][0]
    persisted["id"] = str(uuid.UUID(int=uuid.UUID(persisted["id"]).int ^ 1))

    with pytest.raises(TrackerConflictError, match="comment id invalid"):
        instance.get_issue("LIN-2")


def test_linear_zero_receipt_native_done_fails_closed_for_search_and_get_issue(tracker):
    instance, wire = tracker
    wire.issues["LIN-2"]["state"]["id"] = STATE_IDS["done"]
    instance._activate(PROJECT)

    with pytest.raises(TrackerConflictError, match="outside lifecycle"):
        instance.get_issue("LIN-2")
    with pytest.raises(TrackerConflictError, match="outside lifecycle"):
        instance.search(PROJECT)


def test_linear_zero_receipt_nonterminal_native_state_remains_readable(tracker):
    instance, _wire = tracker
    instance._activate(PROJECT)

    assert instance.get_issue("LIN-2").state == "ready"
    assert {item.id: item.state for item in instance.search(PROJECT)} == {
        "LIN-1": "ready",
        "LIN-2": "ready",
    }


def test_linear_exact_comment_lookup_returns_none_only_for_empty_filter_result(tracker):
    instance, wire = tracker

    assert (
        instance._read_comment(
            "00000000-0000-4000-8000-000000000099",
            "lifecycle.comment.read",
        )
        is None
    )
    document, variables = wire.calls[-1]
    assert "comments(filter: { id: { eq: $id } }, first: 1)" in document
    assert "comment(id:" not in document
    assert variables == {"id": "00000000-0000-4000-8000-000000000099"}


def test_linear_exact_comment_lookup_rejects_wrong_filtered_id(tracker):
    instance, wire = tracker

    def transport(document, variables):
        result = wire(document, variables)
        if "FoundryLinearCommentsById(" in document:
            result["data"]["comments"]["nodes"] = [
                {
                    "id": "wrong-id",
                    "body": "wrong",
                    "issue": {"id": ISSUE_2_ID, "identifier": "LIN-2"},
                }
            ]
        return result

    instance._transport = transport
    with pytest.raises(LinearTrackerError, match="invalid_response"):
        instance._read_comment(
            "00000000-0000-4000-8000-000000000099",
            "lifecycle.comment.read",
        )


@pytest.mark.parametrize(
    "page_info",
    [
    {"hasNextPage": True, "endCursor": "next"},
    {"endCursor": None},
    ],
)
def test_linear_lifecycle_refuses_unbounded_or_malformed_exact_comment_page(
    tracker,
    page_info,
):
    instance, wire = tracker

    def transport(document, variables):
        result = wire(document, variables)
        if "FoundryLinearCommentsById(" in document:
            result["data"]["comments"]["pageInfo"] = page_info
        return result

    instance._transport = transport
    with pytest.raises(LinearTrackerError):
        instance.set_state("LIN-2", "in-progress", project=PROJECT)
    assert wire.comments == {}


def test_linear_lifecycle_refuses_exact_comment_id_collision_before_create(tracker):
    instance, wire = tracker
    _marker, body, comment_id = instance._lifecycle_marker(
        "state-in-progress",
        "LIN-2",
        {
            "state": "in-progress",
            "native_state_id": STATE_IDS["ready"],
        },
    )
    wire.comments[comment_id] = {
        "id": comment_id,
        "body": body + "\ndivergent",
        "issue": {"id": ISSUE_2_ID, "identifier": "LIN-2"},
    }

    with pytest.raises(TrackerConflictError, match="comment id collision"):
        instance.set_state("LIN-2", "in-progress", project=PROJECT)
    assert wire.issues["LIN-2"]["comments"]["nodes"] == []


def test_linear_native_checked_ac_requires_append_only_review_proof(
    tracker, monkeypatch
):
    instance, wire = tracker
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    body = "- [x] acceptance"
    wire.issues["LIN-2"]["description"] = body
    instance._activate(PROJECT)
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40,
        base_sha="b" * 40,
        review_digest="c" * 64,
    )

    assert instance.get_issue("LIN-2").ac_done == 0
    instance.set_state("LIN-2", "review", context=review, project=PROJECT)
    result = write.sync_acceptance(instance, "LIN-2", body, proof("LIN-2", body))

    assert result["status"] == "proof-projected"
    assert result["checked"] == 1
    assert instance.get_issue("LIN-2").ac_done == 1


def test_linear_corrected_pr_creates_chained_review_generation(tracker, monkeypatch):
    instance, _wire = tracker
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    body = "- [ ] acceptance"
    first = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40,
        base_sha="b" * 40,
        review_digest="c" * 64,
    )
    second = TransitionContext(
        pr_url=first.pr_url,
        head_sha="d" * 40,
        base_sha=first.base_sha,
        review_digest="e" * 64,
    )

    instance.set_state("LIN-2", "review", context=first, project=PROJECT)
    write.sync_acceptance(instance, "LIN-2", body, proof("LIN-2", body))
    instance.set_state("LIN-2", "review", context=second, project=PROJECT)

    corrected = instance.get_issue("LIN-2")
    assert corrected.state == "review"
    assert corrected.ac_done == 0
    second_proof = proof(
        "LIN-2",
        body,
        head=second.head_sha,
        base=second.base_sha,
        diff_hash=second.review_digest,
    )
    write.sync_acceptance(instance, "LIN-2", body, second_proof)
    instance.set_state(
        "LIN-2",
        "done",
        context=TransitionContext(
            pr_url=second.pr_url,
            head_sha=second.head_sha,
            base_sha=second.base_sha,
            review_digest=second.review_digest,
            merge_sha="f" * 40,
        ),
        project=PROJECT,
    )

    completed = instance.get_issue("LIN-2")
    assert completed.state == "done"
    assert completed.ac_done == 1
    # The PR's delivery issue is the only one Foundry projects done.  A safe team
    # config cannot let Linear complete a separately linked prerequisite.
    assert instance.get_issue("LIN-1").state == "ready"


def test_linear_review_generation_uses_one_atomic_provider_slot(tracker):
    _instance, wire = tracker
    barrier = threading.Barrier(2)
    seen_threads = set()
    seen_lock = threading.Lock()

    def transport(document, variables):
        result = wire(document, variables)
        if "FoundryLinearIssue(" in document:
            thread_id = threading.get_ident()
            with seen_lock:
                first_read = thread_id not in seen_threads
                seen_threads.add(thread_id)
            if first_read:
                barrier.wait(timeout=5)
        return result

    first = LinearTracker(token="linear-test-secret", transport=transport)
    second = LinearTracker(token="linear-test-secret", transport=transport)
    contexts = (
        TransitionContext(
            pr_url="https://github.com/acme/widgets/pull/17",
            head_sha="a" * 40,
            base_sha="b" * 40,
            review_digest="c" * 64,
        ),
        TransitionContext(
            pr_url="https://github.com/acme/widgets/pull/17",
            head_sha="d" * 40,
            base_sha="b" * 40,
            review_digest="e" * 64,
        ),
    )

    def publish(instance, context):
        try:
            instance.set_state("LIN-2", "review", context=context, project=PROJECT)
            return "created"
        except (LinearTrackerError, TrackerConflictError):
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(publish, (first, second), contexts))

    assert sorted(results) == ["created", "refused"]
    assert len(wire.comments) == 1
    first._transport = wire
    winner = first.get_issue("LIN-2")
    assert winner.state == "review"


def test_linear_merge_with_two_linked_issues_rebinds_delivery_proof_without_completing_prerequisite(
    tracker,
    monkeypatch,
):
    instance, wire = tracker
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    monkeypatch.setattr(issue, "_observe_receipt", lambda *_args: None)
    monkeypatch.setattr(issue, "_cleanup_branch", lambda _branch: "linked-worktree")
    old = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40,
        base_sha="b" * 40,
        review_digest="c" * 64,
    )
    instance.set_state("LIN-2", "review", context=old, project=PROJECT)
    write.sync_acceptance(
        instance,
        "LIN-2",
        "- [ ] acceptance",
        proof("LIN-2", "- [ ] acceptance"),
    )

    new_diff = b"corrected-diff"
    new_digest = hashlib.sha256(new_diff).hexdigest()
    pr = PullRequest(
        number=17,
        url=old.pr_url,
        head="feat/lin-2",
        base="main",
        base_sha="b" * 40,
        sha="d" * 40,
    )
    # This is GitHub/Linear linkage metadata owned by the external integration, not
    # a Foundry authority: one delivery PR is linked to its own issue and a separate
    # prerequisite.  Foundry receives only LIN-2 as its requested merge target.
    pr.linked_issue_ids = ("LIN-2", "LIN-1")
    landed = SimpleNamespace(sha="f" * 40, head=pr.head, merged=True)
    merge_calls = []

    def merge_pr(*_args, **_kwargs):
        merge_calls.append(pr.linked_issue_ids)
        return landed

    codehost = SimpleNamespace(
        name="github",
        resolve_repo=lambda: "acme/widgets",
        get_pr=lambda *_args: pr,
        merge_pr=merge_pr,
        delete_branch=lambda *_args: None,
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: instance)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "git_head", lambda: pr.sha)
    monkeypatch.setattr(issue, "git_diff", lambda **_kwargs: new_diff)
    monkeypatch.setattr(issue, "repository_identity", lambda: "github.com/acme/widgets")
    monkeypatch.setattr(
        write,
        "ci_gate",
        lambda *_args, **_kwargs: {
            "passed": True,
            "waived": False,
            "total": 1,
            "pending": [],
            "failing": [],
        },
    )
    requested = []

    class Store:
        def __init__(self, _repository):
            pass

        def valid_for_merge(self, **coordinates):
            requested.append(coordinates)
            return proof(
                "LIN-2",
                "- [ ] acceptance",
                head=pr.sha,
                base=pr.base_sha,
                diff_hash=new_digest,
            )

    monkeypatch.setattr(issue, "AcceptanceProofStore", Store)

    issue.merge("LIN-2", "17")

    assert len(requested) == 1
    assert requested[0]["head"] == pr.sha
    assert merge_calls == [("LIN-2", "LIN-1")]
    completed = instance.get_issue("LIN-2")
    assert completed.state == "done"
    assert completed.ac_done == 1
    # Under the safe automation configuration, no external GitHub event changes the
    # prerequisite and Foundry writes lifecycle receipts only for its target issue.
    assert instance.get_issue("LIN-1").state == "ready"
    assert {comment["issue"]["identifier"] for comment in wire.comments.values()} == {
        "LIN-2"
    }


def test_linear_lifecycle_readback_revalidates_native_state(tracker):
    instance, wire = tracker
    changed = False

    def transport(document, variables):
        nonlocal changed
        result = wire(document, variables)
        if "FoundryLinearCommentCreate" in document and not changed:
            changed = True
            wire.issues["LIN-2"]["state"]["id"] = STATE_IDS["blocked"]
        return result

    instance._transport = transport
    with pytest.raises(TrackerConflictError, match="native state changed"):
        instance.set_state("LIN-2", "in-progress", project=PROJECT)

    assert changed is True


def test_linear_lifecycle_accepts_only_receipted_forward_native_evolution(
    tracker,
    monkeypatch,
):
    instance, wire = tracker
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40,
        base_sha="b" * 40,
        review_digest="c" * 64,
    )
    done = TransitionContext(
        pr_url=review.pr_url,
        head_sha=review.head_sha,
        base_sha=review.base_sha,
        review_digest=review.review_digest,
        merge_sha="e" * 40,
    )

    instance.set_state("LIN-2", "in-progress", project=PROJECT)
    wire.issues["LIN-2"]["state"]["id"] = STATE_IDS["in-progress"]
    instance.set_state("LIN-2", "review", context=review, project=PROJECT)
    assert instance.get_issue("LIN-2").state == "review"

    wire.issues["LIN-2"]["state"]["id"] = STATE_IDS["review"]
    write.sync_acceptance(
        instance,
        "LIN-2",
        "- [ ] acceptance",
        proof("LIN-2", "- [ ] acceptance"),
    )
    assert instance.get_issue("LIN-2").ac_done == 1

    wire.issues["LIN-2"]["state"]["id"] = STATE_IDS["done"]
    with pytest.raises(TrackerConflictError, match="native state changed"):
        instance.get_issue("LIN-2")

    instance.set_state("LIN-2", "done", context=done, project=PROJECT)
    projected = instance.get_issue("LIN-2")
    assert projected.state == "done"
    assert projected.ac_done == 1


def test_linear_lifecycle_refuses_unrelated_native_drift_even_for_next_review(tracker):
    instance, wire = tracker
    first = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40,
        base_sha="b" * 40,
        review_digest="c" * 64,
    )
    second = TransitionContext(
        pr_url=first.pr_url,
        head_sha="d" * 40,
        base_sha=first.base_sha,
        review_digest="e" * 64,
    )
    instance.set_state("LIN-2", "review", context=first, project=PROJECT)
    wire.issues["LIN-2"]["state"]["id"] = STATE_IDS["blocked"]

    with pytest.raises(TrackerConflictError, match="native state changed"):
        instance.set_state("LIN-2", "review", context=second, project=PROJECT)
    with pytest.raises(TrackerConflictError, match="native state changed"):
        instance.get_issue("LIN-2")
    assert len(wire.comments) == 1


def test_linear_native_done_requires_well_formed_foundry_done_receipt(
    tracker,
    monkeypatch,
):
    instance, wire = tracker
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40,
        base_sha="b" * 40,
        review_digest="c" * 64,
    )
    done = TransitionContext(
        pr_url=review.pr_url,
        head_sha=review.head_sha,
        base_sha=review.base_sha,
        review_digest=review.review_digest,
        merge_sha="e" * 40,
    )
    instance.set_state("LIN-2", "review", context=review, project=PROJECT)
    write.sync_acceptance(
        instance,
        "LIN-2",
        "- [ ] acceptance",
        proof("LIN-2", "- [ ] acceptance"),
    )
    wire.issues["LIN-2"]["state"]["id"] = STATE_IDS["done"]
    instance.set_state("LIN-2", "done", context=done, project=PROJECT)

    row = next(
        item
        for item in wire.issues["LIN-2"]["comments"]["nodes"]
        if ":state-done:" in item["body"]
    )
    decoded = instance._decode_lifecycle_comment("LIN-2", row["body"])
    assert decoded is not None
    malformed = dict(decoded[1])
    malformed.pop("merge_sha")
    _marker, row["body"], row["id"] = instance._lifecycle_marker(
        "state-done",
        "LIN-2",
        malformed,
    )

    with pytest.raises(TrackerConflictError, match="state proof malformed"):
        instance.get_issue("LIN-2")


def test_linear_lifecycle_refuses_unmapped_historical_native_state_receipt(tracker):
    instance, wire = tracker
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40,
        base_sha="b" * 40,
        review_digest="c" * 64,
    )
    instance.set_state("LIN-2", "review", context=review, project=PROJECT)
    row = wire.issues["LIN-2"]["comments"]["nodes"][0]
    decoded = instance._decode_lifecycle_comment("LIN-2", row["body"])
    assert decoded is not None
    malformed = {**decoded[1], "native_state_id": "state-unrelated"}
    _marker, row["body"], row["id"] = instance._lifecycle_marker(
        "state-review",
        "LIN-2",
        malformed,
    )

    with pytest.raises(TrackerConflictError, match="native state proof malformed"):
        instance.get_issue("LIN-2")


def test_linear_lifecycle_recovers_interruption_after_provider_comment_effect(tracker):
    instance, wire = tracker
    interrupted = False

    def transport(document, variables):
        nonlocal interrupted
        result = wire(document, variables)
        if "FoundryLinearCommentCreate" in document and not interrupted:
            interrupted = True
            raise LinearTrackerError("lifecycle.comment.create", 503, "transport_error")
        return result

    instance._transport = transport
    instance.set_state("LIN-2", "in-progress", project=PROJECT)

    assert interrupted is True
    assert instance.get_issue("LIN-2").state == "in-progress"
    assert len(wire.comments) == 1


def test_linear_lifecycle_refuses_divergent_issue_readback_after_comment_effect(
    tracker,
):
    instance, wire = tracker
    altered = False

    def transport(document, variables):
        nonlocal altered
        result = wire(document, variables)
        if "FoundryLinearCommentCreate" in document and not altered:
            altered = True
            issue_row = wire.issues["LIN-2"]["comments"]["nodes"][0]
            issue_row["body"] += "\ndivergent"
        return result

    instance._transport = transport
    with pytest.raises(TrackerConflictError, match="Linear lifecycle comment"):
        instance.set_state("LIN-2", "in-progress", project=PROJECT)

    assert altered is True
    assert len(wire.comments) == 1


def test_linear_lifecycle_concurrent_or_divergent_projection_fails_closed(tracker):
    instance, wire = tracker
    instance.set_state("LIN-2", "in-progress", project=PROJECT)
    native = wire.issues["LIN-2"]
    original = copy.deepcopy(native["comments"]["nodes"][0])
    native["comments"]["nodes"].append(original)

    with pytest.raises(TrackerConflictError, match="duplicate projection"):
        instance.get_issue("LIN-2")

    native["comments"]["nodes"] = [original]
    native["state"]["id"] = STATE_IDS["blocked"]
    with pytest.raises(TrackerConflictError, match="native state changed"):
        instance.get_issue("LIN-2")


def test_linear_acceptance_projection_refuses_incomplete_or_stale_proof_before_write(
    tracker,
):
    instance, wire = tracker
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40,
        base_sha="b" * 40,
        review_digest="c" * 64,
    )
    instance.set_state("LIN-2", "review", context=review, project=PROJECT)
    before = len(wire.comments)
    invalid = proof("LIN-2", "- [ ] acceptance")
    invalid["quality"] = "blocked"

    with pytest.raises(TrackerConflictError, match="malformed or stale"):
        instance.project_acceptance_proof(
            "LIN-2",
            "- [ ] acceptance",
            invalid,
            checked=1,
            project=PROJECT,
        )

    assert len(wire.comments) == before


def test_linear_cockpit_projection_requires_complete_go_and_never_changes_lifecycle(
    tracker,
):
    instance, wire = tracker
    repository = "github.com/acme/widgets"
    issue_id = "LIN-2"
    body = "- [ ] acceptance"
    head, base, diff_hash = "a" * 40, "b" * 40, "c" * 64
    pr = PullRequest(
        number=17,
        url="https://github.com/acme/widgets/pull/17",
        head="feat/lin-2",
        base="main",
        base_sha=base,
        sha=head,
    )
    criteria = acceptance_criteria(body)
    test_receipt = evidence_plane.create_test_receipt(
        repository=repository,
        issue_id=issue_id,
        head_sha=head,
        diff_hash=diff_hash,
        status="passed",
        result_digest="f" * 64,
    )
    observed_at = evidence_plane._now_ms()
    checks = evidence_plane._create_ci_receipt(
        source="check_runs",
        repository=repository,
        head_sha=head,
        observed_at=observed_at,
        checks=[Check(name="tests", status="completed", conclusion="success")],
    )
    statuses = evidence_plane._create_ci_receipt(
        source="commit_statuses",
        repository=repository,
        head_sha=head,
        observed_at=observed_at,
        checks=[],
    )
    envelope = evidence_plane.evidence_envelope(
        repository=repository,
        issue_id=issue_id,
        ac_digest=acceptance_digest(criteria),
        pr=pr,
        diff_hash=diff_hash,
        review_proof=proof(issue_id, body),
        test_receipt=test_receipt,
        check_runs_receipt=checks,
        commit_statuses_receipt=statuses,
    )

    before = len(wire.comments)
    with pytest.raises(
        TrackerCapabilityUnavailableError,
        match="cockpit-evidence-complete-go",
    ):
        instance.project_cockpit_evidence(
            issue_id,
            {},
            repository=repository,
            ac_digest=acceptance_digest(criteria),
            pr=pr,
            diff_hash=diff_hash,
            project=PROJECT,
        )
    assert len(wire.comments) == before

    assert (
        instance.project_cockpit_evidence(
            issue_id,
            envelope,
            repository=repository,
            ac_digest=acceptance_digest(criteria),
            pr=pr,
            diff_hash=diff_hash,
            project=PROJECT,
        )
        is True
    )
    assert (
        instance.project_cockpit_evidence(
            issue_id,
            envelope,
            repository=repository,
            ac_digest=acceptance_digest(criteria),
            pr=pr,
            diff_hash=diff_hash,
            project=PROJECT,
        )
        is False
    )

    projected = instance.get_issue(issue_id)
    assert projected.state == "ready"
    assert projected.pr_url is None
    assert (projected.ac_done, projected.ac_total) == (0, 1)
    assert len(wire.comments) == before + 1


def test_malformed_and_provider_error_payloads_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    sentinel = "LINEAR_SECRET_MUST_NOT_LEAK"
    malformed = LinearTracker(
        token=sentinel,
        transport=lambda *_: {"data": {"issue": "bad"}},
    )
    malformed._active_project = PROJECT
    with pytest.raises(LinearTrackerError) as error:
        malformed.get_issue("LIN-1")
    assert sentinel not in str(error.value)

    graphql_error = LinearTracker(
        token=sentinel,
        transport=lambda *_: {"errors": [{"message": sentinel}], "data": {}},
    )
    with pytest.raises(LinearTrackerError, match="graphql_error") as error:
        graphql_error.search(PROJECT)
    assert sentinel not in str(error.value)


def test_unsupported_issue_replacement_capabilities_remain_typed(tracker):
    instance, wire = tracker
    assert instance.list_adrs(PROJECT) == []
    with pytest.raises(
        TrackerCapabilityUnavailableError, match="provider-native-search-query"
    ):
        instance.search(PROJECT, "display name lookup")
    with pytest.raises(TrackerConflictError, match="body changed"):
        instance.update_body(
            Adr(id="LIN-ADR-0001", title="ADR"),
            "old",
            "new",
            project=PROJECT,
        )
    assert all(
        "FoundryLinearAdrDocumentCreate" not in document for document, _ in wire.calls
        )


def test_pr_projection_is_typed_unavailable_before_any_provider_write(tracker):
    instance, wire = tracker
    before = len(wire.calls)
    with pytest.raises(
        TrackerCapabilityUnavailableError,
        match="linear.github-pr-projection",
    ):
        instance.update_fields(
            "LIN-2",
            {"GitHub PR": "https://github.com/acme/widgets/pull/1"},
            project=PROJECT,
        )
    assert wire.calls[before:] == []


def test_linear_adr_create_uses_filtered_absence_read_and_indexes_native_document(
    tracker,
):
    instance, wire = tracker

    created = instance.create_adr(PROJECT, "Use native documents", "decision body")

    assert (created.id, created.status) == ("LIN-ADR-0001", "proposed")
    assert created.ref in wire.documents
    assert [adr.id for adr in instance.list_adrs(PROJECT)] == [created.id]
    assert any("FoundryLinearAdrDocumentById" in query for query, _ in wire.calls)
    assert not any("document(id:" in query for query, _ in wire.calls)
    assert not any(
        "youtrack" in query.lower() or "git" in query.lower() for query, _ in wire.calls
    )


def test_linear_adr_versions_append_replay_and_refuse_stale_concurrent_writer(tracker):
    instance, wire = tracker
    created = instance.create_adr(PROJECT, "Append only", "first")
    replay = instance.create_adr(PROJECT, "Append only", "first")
    assert replay.ref == created.ref

    instance.set_adr_status(created, "accepted", project=PROJECT)
    accepted = instance.list_adrs(PROJECT)[0]
    replacement = accepted.body.replace("first", "second")
    assert instance.update_body(accepted, accepted.body, replacement, project=PROJECT)
    latest = instance.list_adrs(PROJECT)[0]
    assert latest.status == "accepted"
    assert len(wire.documents) == 6

    with pytest.raises(TrackerConflictError, match="snapshot is stale"):
        instance.set_adr_status(accepted, "deprecated", project=PROJECT)


def test_linear_adr_supersession_and_historical_import_preserve_relations_origin(
    tracker,
):
    instance, wire = tracker
    first = instance.create_adr(PROJECT, "First", "first")
    second = instance.create_adr(PROJECT, "Second", "second")
    instance.set_adr_status(first, "accepted", project=PROJECT)
    instance.set_adr_status(second, "accepted", project=PROJECT)
    accepted = {adr.id: adr for adr in instance.list_adrs(PROJECT)}
    instance.supersede_adr(accepted[first.id], second.id, project=PROJECT)

    imported = instance.import_adr(
        PROJECT,
        adr_id="LIN-ADR-0099",
        title="Historical",
        body="old",
        historical_status="superseded",
        source_ref="YT-ADR-9",
        source_created=1,
        source_updated=2,
        expected_source_sha256=hashlib.sha256(b"old").hexdigest(),
        superseded_by=second.id,
        issue_refs=("LIN-2",),
    )
    models = {adr.id: adr for adr in instance.list_adrs(PROJECT)}
    assert models[first.id].status == "superseded"
    assert imported.status == "superseded"
    raw = wire.documents[imported.ref]
    metadata, _ = linear_module._parse_adr_document(
        raw,
        {
            "project_id": PROJECT.id,
            "team_id": PROJECT.extra["team_id"],
        },
    )
    assert metadata["origin"]["kind"] == "migration"
    assert metadata["relations"] == {
        "supersedes": [],
        "superseded_by": second.id,
        "issues": ["LIN-2"],
    }
    replacement_raw = wire.documents[models[second.id].ref]
    replacement_metadata, _ = linear_module._parse_adr_document(
        replacement_raw,
        {
            "project_id": PROJECT.id,
            "team_id": PROJECT.extra["team_id"],
        },
    )
    assert replacement_metadata["relations"]["supersedes"] == [
        first.id,
        imported.id,
    ]


@pytest.mark.parametrize(
    "issue_refs",
    [
        ("LIN-2", "lin-2"),
        ("Lin-2", ISSUE_2_ID),
    ],
)
def test_linear_adr_import_refuses_issue_alias_identity_collision_before_effect(
    tracker, issue_refs
):
    instance, wire = tracker
    call_offset = len(wire.calls)

    with pytest.raises(TrackerConflictError, match="duplicate provider identity"):
        instance.import_adr(
            PROJECT,
            adr_id="LIN-ADR-0098",
            title="Alias collision",
            body="old",
            historical_status="deprecated",
            source_ref="YT-ADR-98",
            source_created=1,
            source_updated=2,
            expected_source_sha256=hashlib.sha256(b"old").hexdigest(),
            issue_refs=issue_refs,
        )

    assert not any(
        "FoundryLinearAdrDocumentCreate" in document
        or "FoundryLinearCommentCreate" in document
        for document, _variables in wire.calls[call_offset:]
    )
    assert wire.documents == {}
    assert wire.comments == {}


def test_linear_adr_import_canonicalizes_single_issue_alias_and_replays_by_uuid(
    tracker,
):
    instance, wire = tracker
    kwargs = {
        "adr_id": "LIN-ADR-0098",
        "title": "Canonical issue relation",
        "body": "old",
        "historical_status": "deprecated",
        "source_ref": "YT-ADR-98",
        "source_created": 1,
        "source_updated": 2,
        "expected_source_sha256": hashlib.sha256(b"old").hexdigest(),
    }

    imported = instance.import_adr(PROJECT, **kwargs, issue_refs=("lin-2",))
    binding = instance._binding(PROJECT)
    metadata, _body = linear_module._parse_adr_document(
        wire.documents[imported.ref], binding
    )
    assert metadata["relations"]["issues"] == ["LIN-2"]
    comment_id, expected_body = linear_module._adr_issue_link(
        binding, imported.id, "LIN-2"
    )
    assert wire.comments[comment_id] == {
        "id": comment_id,
        "body": expected_body,
        "issue": {"id": ISSUE_2_ID, "identifier": "LIN-2"},
    }

    call_offset = len(wire.calls)
    replay = instance.import_adr(PROJECT, **kwargs, issue_refs=(ISSUE_2_ID,))

    assert replay.ref == imported.ref
    assert [adr.id for adr in instance.list_adrs(PROJECT)] == [imported.id]
    assert len(wire.comments) == 1
    assert not any(
        "FoundryLinearAdrDocumentCreate" in document
        or "FoundryLinearCommentCreate" in document
        for document, _variables in wire.calls[call_offset:]
    )


def test_linear_adr_import_refuses_malformed_provider_issue_identifier_before_effect(
    tracker,
):
    instance, wire = tracker
    wire.issues["LIN-2"]["identifier"] = "LIN-two"
    call_offset = len(wire.calls)

    with pytest.raises(LinearTrackerError) as raised:
        instance.import_adr(
            PROJECT,
            adr_id="LIN-ADR-0098",
            title="Malformed provider identifier",
            body="old",
            historical_status="deprecated",
            source_ref="YT-ADR-98",
            source_created=1,
            source_updated=2,
            expected_source_sha256=hashlib.sha256(b"old").hexdigest(),
            issue_refs=("LIN-2",),
        )

    assert raised.value.operation == "adr.issue.resolve"
    assert raised.value.code == "invalid_response"
    assert not any(
        "FoundryLinearAdrDocumentCreate" in document
        or "FoundryLinearCommentCreate" in document
        for document, _variables in wire.calls[call_offset:]
    )
    assert wire.documents == {}
    assert wire.comments == {}


def test_linear_adr_issue_relation_limit_accepts_100_and_refuses_101_before_effect(
    tracker,
):
    instance, wire = tracker
    for number in range(3, 102):
        identifier = f"LIN-{number}"
        native_id = str(uuid.UUID(int=number, version=4))
        wire.issues[identifier] = raw_issue(identifier, native_id)
    over_limit = tuple(f"LIN-{number}" for number in range(1, 102))
    call_offset = len(wire.calls)

    with pytest.raises(ValueError, match="historical import invalid"):
        instance.import_adr(
            PROJECT,
            adr_id="LIN-ADR-0098",
            title="Over issue relation bound",
            body="old",
            historical_status="deprecated",
            source_ref="YT-ADR-98",
            source_created=1,
            source_updated=2,
            expected_source_sha256=hashlib.sha256(b"old").hexdigest(),
            issue_refs=over_limit,
        )
    assert not any(
        "FoundryLinearAdrDocumentCreate" in document
        or "FoundryLinearCommentCreate" in document
        for document, _variables in wire.calls[call_offset:]
    )

    at_limit = over_limit[:-1]
    imported = instance.import_adr(
        PROJECT,
        adr_id="LIN-ADR-0099",
        title="Exact issue relation bound",
        body="old",
        historical_status="deprecated",
        source_ref="YT-ADR-99",
        source_created=1,
        source_updated=2,
        expected_source_sha256=hashlib.sha256(b"old").hexdigest(),
        issue_refs=at_limit,
    )
    metadata, _body = linear_module._parse_adr_document(
        wire.documents[imported.ref], instance._binding(PROJECT)
    )
    assert len(metadata["relations"]["issues"]) == 100
    assert set(metadata["relations"]["issues"]) == set(at_limit)
    assert len(wire.comments) == 100


@pytest.mark.parametrize("operation", ["supersede", "historical_import"])
@pytest.mark.parametrize(("existing_relations", "accepted"), [(99, True), (100, False)])
def test_linear_adr_supersedes_limit_is_prevalidated_before_provider_effect(
    tracker,
    operation,
    existing_relations,
    accepted,
):
    instance, wire = tracker
    source, replacement = seed_linear_adr_relation_boundary(
        wire, existing_relations
    )
    before_documents = copy.deepcopy(wire.documents)
    before_comments = copy.deepcopy(wire.comments)
    call_offset = len(wire.calls)

    def mutate():
        if operation == "supersede":
            instance.supersede_adr(source, replacement.id, project=PROJECT)
            return
        instance.import_adr(
            PROJECT,
            adr_id="LIN-ADR-7000",
            title="Imported boundary ADR",
            body="historical body",
            historical_status="superseded",
            source_ref="YT-ADR-boundary",
            source_created=1,
            source_updated=2,
            expected_source_sha256=hashlib.sha256(b"historical body").hexdigest(),
            superseded_by=replacement.id,
            issue_refs=("LIN-2",),
        )

    if accepted:
        mutate()
        models = {adr.id: adr for adr in instance.list_adrs(PROJECT)}
        replacement_metadata, _ = linear_module._parse_adr_document(
            wire.documents[models[replacement.id].ref], instance._binding(PROJECT)
        )
        assert len(replacement_metadata["relations"]["supersedes"]) == 100
        return

    with pytest.raises(TrackerConflictError, match="derived candidate is invalid"):
        mutate()

    provider_creates = [
        document
        for document, _variables in wire.calls[call_offset:]
        if "FoundryLinearAdrDocumentCreate" in document
        or "FoundryLinearCommentCreate" in document
    ]
    assert provider_creates == []
    assert wire.documents == before_documents
    assert wire.comments == before_comments


@pytest.mark.parametrize("damage", ["archive", "tamper", "delete_previous"])
def test_linear_adr_chain_refuses_archived_tampered_or_deleted_history(tracker, damage):
    instance, wire = tracker
    created = instance.create_adr(PROJECT, "Guard chain", "body")
    instance.set_adr_status(created, "accepted", project=PROJECT)
    documents = list(wire.documents.values())
    if damage == "archive":
        documents[-1]["archivedAt"] = "2026-09-24T00:00:00Z"
    elif damage == "tamper":
        documents[-1]["content"] += "tampered"
    else:
        del wire.documents[documents[0]["id"]]

    with pytest.raises((LinearTrackerError, TrackerConflictError)):
        instance.list_adrs(PROJECT)


@pytest.mark.parametrize("composite", ["status_body", "supersedes_body"])
def test_linear_adr_chain_refuses_witnessed_composite_version_delta(
    tracker, composite
):
    instance, wire = tracker
    if composite == "status_body":
        original = instance.create_adr(PROJECT, "Status delta", "original body")
        instance.set_adr_status(original, "accepted", project=PROJECT)
        target_id = original.id
    else:
        source = instance.create_adr(PROJECT, "Source delta", "source body")
        replacement = instance.create_adr(PROJECT, "Replacement delta", "old body")
        instance.set_adr_status(source, "accepted", project=PROJECT)
        instance.set_adr_status(replacement, "accepted", project=PROJECT)
        accepted = {adr.id: adr for adr in instance.list_adrs(PROJECT)}
        instance.supersede_adr(accepted[source.id], replacement.id, project=PROJECT)
        target_id = replacement.id

    latest = {adr.id: adr for adr in instance.list_adrs(PROJECT)}[target_id]
    binding = instance._binding(PROJECT)
    raw = wire.documents[latest.ref]
    metadata, _ = linear_module._parse_adr_document(raw, binding)
    changed_body = "externally combined body edit"
    metadata["body_sha256"] = hashlib.sha256(changed_body.encode()).hexdigest()
    raw["content"] = linear_module._adr_document_content(metadata, changed_body)
    witness_id = linear_module._adr_witness_id(
        PROJECT.id, target_id, metadata["sequence"]
    )
    wire.documents[witness_id] = linear_module._adr_witness_document(
        binding, metadata, raw
    )
    assert linear_module._parse_adr_document(raw, binding)[0] == metadata
    assert linear_module._parse_adr_witness(wire.documents[witness_id], binding)

    with pytest.raises(TrackerConflictError, match="version chain diverged"):
        instance.list_adrs(PROJECT)


@pytest.mark.parametrize("history", ["unique", "head"])
def test_linear_adr_witness_refuses_isolated_unique_or_head_deletion(tracker, history):
    instance, wire = tracker
    created = instance.create_adr(PROJECT, "Witnessed", "body")
    if history == "head":
        instance.set_adr_status(created, "accepted", project=PROJECT)
        created = instance.list_adrs(PROJECT)[0]

    del wire.documents[created.ref]

    with pytest.raises(
        TrackerConflictError, match="witness has no matching version"
    ):
        instance.list_adrs(PROJECT)


@pytest.mark.parametrize("reencoding", ["pretty", "duplicate_key"])
def test_linear_adr_witness_refuses_semantically_equal_reencoding(
    tracker, reencoding
):
    instance, wire = tracker
    created = instance.create_adr(PROJECT, "Canonical witness", "body")
    assert [adr.id for adr in instance.list_adrs(PROJECT)] == [created.id]

    witness_id = linear_module._adr_witness_id(PROJECT.id, created.id, 0)
    witness = wire.documents[witness_id]
    content = witness["content"]
    encoded = content[len(linear_module._ADR_WITNESS_HEADER) : -len("\n-->")]
    payload = json.loads(encoded)
    if reencoding == "pretty":
        changed = json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        changed = (
            f'{{"schema":{json.dumps(payload["schema"])},' + encoded[1:]
        )
    assert changed != encoded
    assert json.loads(changed) == payload
    witness["content"] = f"{linear_module._ADR_WITNESS_HEADER}{changed}\n-->"
    call_offset = len(wire.calls)

    with pytest.raises(LinearTrackerError, match="invalid_response"):
        instance.list_adrs(PROJECT)
    assert not any(
        "FoundryLinearAdrDocumentCreate" in document
        or "FoundryLinearCommentCreate" in document
        for document, _variables in wire.calls[call_offset:]
    )


def test_linear_adr_client_ids_are_stable_distinct_uuid_v4():
    binding = {"project_id": PROJECT.id, "team_id": PROJECT.extra["team_id"]}
    ids = (
        linear_module._adr_document_id(PROJECT.id, "LIN-ADR-7000", 0),
        linear_module._adr_witness_id(PROJECT.id, "LIN-ADR-7000", 0),
        linear_module._adr_issue_link(binding, "LIN-ADR-7000", "LIN-2")[0],
    )
    assert len(set(ids)) == 3
    assert all(uuid.UUID(value).version == 4 for value in ids)
    assert all(uuid.UUID(value).variant == uuid.RFC_4122 for value in ids)
    assert ids[0] == linear_module._adr_document_id(PROJECT.id, "LIN-ADR-7000", 0)
    assert ids[1] == linear_module._adr_witness_id(PROJECT.id, "LIN-ADR-7000", 0)
    assert ids[2] == linear_module._adr_issue_link(
        binding, "LIN-ADR-7000", "LIN-2"
    )[0]


@pytest.mark.parametrize("mutation", ["document", "comment"])
def test_linear_fake_rejects_uuid_v5_creation_ids(mutation):
    wire = LinearWire()
    bad_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "production-invalid"))
    if mutation == "document":
        response = wire(
            linear_module._ADR_DOCUMENT_CREATE,
            {"input": {"id": bad_id, "title": "ADR", "content": "body", "projectId": PROJECT.id}},
        )
    else:
        response = wire(
            linear_module._COMMENT_CREATE,
            {"input": {"id": bad_id, "body": "relation", "issueId": ISSUE_2_ID}},
        )
    assert response["errors"]
    assert wire.documents == {}
    assert wire.comments == {}


def test_linear_adr_missing_witness_fails_closed_and_exact_pair_replays(tracker):
    instance, wire = tracker
    created = instance.create_adr(PROJECT, "Replay pair", "body")
    before = len(wire.documents)

    replay = instance.create_adr(PROJECT, "Replay pair", "body")
    assert replay.ref == created.ref
    assert len(wire.documents) == before

    witness_id = linear_module._adr_witness_id(PROJECT.id, created.id, 0)
    del wire.documents[witness_id]
    with pytest.raises(TrackerConflictError, match="version witness is missing"):
        instance.list_adrs(PROJECT)
    recovered = instance.create_adr(PROJECT, "Replay pair", "body")
    assert recovered.ref == created.ref
    assert [adr.id for adr in instance.list_adrs(PROJECT)] == [created.id]


@pytest.mark.parametrize("interrupted_slot", ["version", "witness"])
def test_linear_adr_pair_recovers_accepted_effect_after_each_interruption(
    tracker, interrupted_slot
):
    instance, wire = tracker
    original = wire.__call__
    interrupted = False

    def transport(document, variables):
        nonlocal interrupted
        response = original(document, variables)
        title = variables.get("input", {}).get("title", "")
        matching_slot = (
            title.startswith("[Foundry ADR] ")
            if interrupted_slot == "version"
            else title.startswith("[Foundry ADR witness] ")
        )
        if (
            "FoundryLinearAdrDocumentCreate" in document
            and matching_slot
            and not interrupted
        ):
            interrupted = True
            raise OSError("connection dropped after provider accepted document")
        return response

    instance._transport = transport
    created = instance.create_adr(PROJECT, "Recovered witness", "body")

    assert [adr.id for adr in instance.list_adrs(PROJECT)] == [created.id]
    assert len(wire.documents) == 2


def test_linear_adr_witness_create_unavailable_leaves_detectable_partial_pair(tracker):
    instance, wire = tracker
    original = wire.__call__

    def transport(document, variables):
        if (
            "FoundryLinearAdrDocumentCreate" in document
            and variables["input"]["title"].startswith("[Foundry ADR witness]")
        ):
            raise OSError("provider unavailable before witness create")
        return original(document, variables)

    instance._transport = transport
    with pytest.raises(LinearTrackerError, match="transport_error"):
        instance.create_adr(PROJECT, "Partial pair", "body")
    instance._transport = original

    assert len(wire.documents) == 1
    with pytest.raises(TrackerConflictError, match="version witness is missing"):
        instance.list_adrs(PROJECT)
    recovered = instance.create_adr(PROJECT, "Partial pair", "body")
    assert recovered.status == "proposed"
    assert len(wire.documents) == 2


def test_linear_adr_relations_are_reciprocal_and_project_bounded(tracker):
    instance, wire = tracker
    first = instance.create_adr(PROJECT, "First reciprocal", "first")
    second = instance.create_adr(PROJECT, "Second reciprocal", "second")
    instance.set_adr_status(first, "accepted", project=PROJECT)
    instance.set_adr_status(second, "accepted", project=PROJECT)
    accepted = {adr.id: adr for adr in instance.list_adrs(PROJECT)}
    instance.supersede_adr(accepted[first.id], second.id, project=PROJECT)

    models = {adr.id: adr for adr in instance.list_adrs(PROJECT)}
    first_metadata, _ = linear_module._parse_adr_document(
        wire.documents[models[first.id].ref], instance._binding(PROJECT)
    )
    second_metadata, _ = linear_module._parse_adr_document(
        wire.documents[models[second.id].ref], instance._binding(PROJECT)
    )
    assert first_metadata["relations"]["superseded_by"] == second.id
    assert first.id in second_metadata["relations"]["supersedes"]

    wire.issues["LIN-2"]["project"] = {"id": "other-project"}
    with pytest.raises(LinearBindingError, match="issue_outside_binding"):
        instance.import_adr(
            PROJECT,
            adr_id="LIN-ADR-0098",
            title="Cross project",
            body="old",
            historical_status="deprecated",
            source_ref="YT-ADR-98",
            source_created=1,
            source_updated=2,
            expected_source_sha256=hashlib.sha256(b"old").hexdigest(),
            issue_refs=("LIN-2",),
        )


def test_linear_adr_read_refuses_internally_witnessed_unilateral_relation(tracker):
    instance, wire = tracker
    first = instance.create_adr(PROJECT, "Unilateral first", "first")
    second = instance.create_adr(PROJECT, "Unilateral second", "second")
    binding = instance._binding(PROJECT)
    raw = wire.documents[second.ref]
    metadata, body = linear_module._parse_adr_document(raw, binding)
    metadata["relations"]["supersedes"] = [first.id]
    raw["title"] = linear_module._adr_document_title(metadata)
    raw["content"] = linear_module._adr_document_content(metadata, body)
    witness_id = linear_module._adr_witness_id(PROJECT.id, second.id, 0)
    wire.documents[witness_id] = linear_module._adr_witness_document(
        binding, metadata, raw
    )

    with pytest.raises(TrackerConflictError, match="not reciprocal"):
        instance.list_adrs(PROJECT)


def test_linear_adr_issue_relation_requires_reciprocal_project_comment(tracker):
    instance, wire = tracker
    imported = instance.import_adr(
        PROJECT,
        adr_id="LIN-ADR-0097",
        title="Issue relation",
        body="old",
        historical_status="deprecated",
        source_ref="YT-ADR-97",
        source_created=1,
        source_updated=2,
        expected_source_sha256=hashlib.sha256(b"old").hexdigest(),
        issue_refs=("LIN-2",),
    )
    binding = instance._binding(PROJECT)
    comment_id, expected_body = linear_module._adr_issue_link(
        binding, imported.id, "LIN-2"
    )
    assert wire.comments[comment_id]["body"] == expected_body

    del wire.comments[comment_id]
    with pytest.raises(TrackerConflictError, match="issue relation is not reciprocal"):
        instance.list_adrs(PROJECT)


@pytest.mark.parametrize("damage", ["stored_alias", "comment_alias"])
def test_linear_adr_issue_relation_read_refuses_alias_identity_drift(
    tracker, damage
):
    instance, wire = tracker
    imported = instance.import_adr(
        PROJECT,
        adr_id="LIN-ADR-0097",
        title="Canonical issue relation",
        body="old",
        historical_status="deprecated",
        source_ref="YT-ADR-97",
        source_created=1,
        source_updated=2,
        expected_source_sha256=hashlib.sha256(b"old").hexdigest(),
        issue_refs=("LIN-2",),
    )
    binding = instance._binding(PROJECT)
    comment_id, _expected_body = linear_module._adr_issue_link(
        binding, imported.id, "LIN-2"
    )
    if damage == "stored_alias":
        raw = wire.documents[imported.ref]
        metadata, body = linear_module._parse_adr_document(raw, binding)
        metadata["relations"]["issues"] = ["lin-2"]
        raw["content"] = linear_module._adr_document_content(metadata, body)
        witness_id = linear_module._adr_witness_id(PROJECT.id, imported.id, 0)
        wire.documents[witness_id] = linear_module._adr_witness_document(
            binding, metadata, raw
        )
        match = "identifier is not canonical"
    else:
        wire.comments[comment_id]["issue"]["identifier"] = "lin-2"
        match = "issue relation is not reciprocal"
    call_offset = len(wire.calls)

    with pytest.raises(TrackerConflictError, match=match):
        instance.list_adrs(PROJECT)
    assert not any(
        "FoundryLinearAdrDocumentCreate" in document
        or "FoundryLinearCommentCreate" in document
        for document, _variables in wire.calls[call_offset:]
    )


def test_linear_adr_missing_relations_are_distinct_from_provider_outage(tracker):
    instance, wire = tracker
    digest = hashlib.sha256(b"old").hexdigest()
    kwargs = {
        "adr_id": "LIN-ADR-0099",
        "title": "Missing relations",
        "body": "old",
        "historical_status": "deprecated",
        "source_ref": "YT-ADR-99",
        "source_created": 1,
        "source_updated": 2,
        "expected_source_sha256": digest,
    }
    with pytest.raises(AdrUnavailableError, match="LIN-ADR-0007"):
        instance.import_adr(
            PROJECT,
            **{**kwargs, "historical_status": "accepted"},
            supersedes=("LIN-ADR-0007",),
        )
    with pytest.raises(IssueUnavailableError, match="LIN-404"):
        instance.import_adr(PROJECT, **kwargs, issue_refs=("LIN-404",))

    original = wire.__call__

    def unavailable(document, variables):
        if "FoundryLinearIssue(" in document:
            raise OSError("provider unavailable")
        return original(document, variables)

    instance._transport = unavailable
    with pytest.raises(LinearTrackerError) as error:
        instance.import_adr(PROJECT, **kwargs, issue_refs=("LIN-2",))
    assert error.value.code == "transport_error"


def test_linear_historical_proposed_adr_cannot_supersede_accepted_adr(tracker):
    instance, wire = tracker
    old = instance.create_adr(PROJECT, "Old accepted", "old")
    instance.set_adr_status(old, "accepted", project=PROJECT)
    before = len(wire.documents)

    with pytest.raises(ValueError, match="historical import invalid"):
        instance.import_adr(
            PROJECT,
            adr_id="LIN-ADR-0096",
            title="Proposed replacement",
            body="proposal",
            historical_status="proposed",
            source_ref="YT-ADR-96",
            source_created=None,
            source_updated=None,
            expected_source_sha256=hashlib.sha256(b"proposal").hexdigest(),
            supersedes=(old.id,),
        )
    assert len(wire.documents) == before


def test_tracker_import_port_refuses_when_provider_does_not_implement_it():
    unsupported = SimpleNamespace(name="unsupported")
    with pytest.raises(
        TrackerCapabilityUnavailableError, match="unsupported.adr_historical_import"
    ):
        Tracker.import_adr(
            unsupported,
            PROJECT,
            adr_id="LIN-ADR-0099",
            title="Historical",
            body="body",
            historical_status="deprecated",
            source_ref="source",
            source_created=None,
            source_updated=None,
            expected_source_sha256=hashlib.sha256(b"body").hexdigest(),
        )


def test_linear_adr_storage_survives_actual_youtrack_adapter_outage(
    tracker, monkeypatch
):
    instance, _wire = tracker
    youtrack = YouTrackTracker(
        url="https://unavailable.example.invalid", token="offline-test-token"
    )

    def unavailable(*_args, **_kwargs):
        raise OSError("YouTrack unavailable")

    monkeypatch.setattr(youtrack, "_req", unavailable)
    with pytest.raises(OSError, match="YouTrack unavailable"):
        youtrack.list_adrs(PROJECT)

    created = instance.create_adr(PROJECT, "No fallback", "body")

    assert [adr.id for adr in instance.list_adrs(PROJECT)] == [created.id]


def test_linear_adr_read_refuses_collection_collision_and_bad_page_info(tracker):
    instance, wire = tracker
    created = instance.create_adr(PROJECT, "Collision", "body")
    original = wire.__call__

    def collision(document, variables):
        response = original(document, variables)
        if "FoundryLinearAdrDocumentById" in document:
            raw = wire.documents[created.ref]
            response["data"]["documents"] = connection(
                [copy.deepcopy(raw), copy.deepcopy(raw)]
            )
        return response

    instance._transport = collision
    with pytest.raises(TrackerConflictError, match="slot collision"):
        instance._read_adr_document(created.ref)

    def terminal_cursor(document, variables):
        response = original(document, variables)
        if "FoundryLinearAdrDocumentById" in document:
            response["data"]["documents"]["pageInfo"]["endCursor"] = "last-node"
        return response

    instance._transport = terminal_cursor
    assert instance._read_adr_document(created.ref)["id"] == created.ref

    def malformed_cursor(document, variables):
        response = terminal_cursor(document, variables)
        if "FoundryLinearAdrDocumentById" in document:
            response["data"]["documents"]["pageInfo"]["endCursor"] = 42
        return response

    instance._transport = malformed_cursor
    with pytest.raises(LinearTrackerError):
        instance._read_adr_document(created.ref)
