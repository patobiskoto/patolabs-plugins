"""Controlled Linear transport proof; no test contacts a Linear workspace."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

import foundry
from foundry import issue, query, registry, routing
from foundry.models import Adr, Issue, Link, Project
from foundry.routing import acceptance_criteria, acceptance_digest
from foundry.trackers.base import (
    AcceptanceSyncUnavailableError,
    BodyUpdateUnavailableError,
    TrackerCapabilityUnavailableError,
)
from foundry.trackers.devhub import DevHubTracker
from foundry.trackers.linear import (
    LinearBindingError,
    LinearTracker,
    LinearTrackerError,
)
from foundry.trackers.youtrack import YouTrackTracker


STATE_IDS = {
    "backlog": "state-backlog", "ready": "state-ready",
    "in-progress": "state-in-progress", "review": "state-review",
    "blocked": "state-blocked", "done": "state-done", "dropped": "state-dropped",
}
PROJECT = Project(
    key="LIN", id="project-uuid",
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
        "id": native_id, "identifier": identifier, "title": title,
        "description": body, "priority": 2, "estimate": 3,
        "createdAt": "2026-09-20T10:00:00Z", "updatedAt": "2026-09-20T10:01:00Z",
        "state": {"id": STATE_IDS["ready"], "name": "Ready"},
        "team": {"id": "team-uuid", "key": "LIN"},
        "project": {"id": "project-uuid"},
        "projectMilestone": {"id": "milestone-m1", "name": "ignored"},
        "labels": connection([
            {"id": "label-feature", "name": "Feature display"},
            {"id": "label-pilot", "name": "Pilot display"},
        ]),
        "parent": None, "children": connection([]), "relations": connection([]),
        "inverseRelations": connection([]),
        "comments": connection([]),
    }


class LinearWire:
    """Stateful GraphQL fake retaining exact native identifiers across mutations."""

    def __init__(self):
        self.issues = {
            "LIN-1": raw_issue("LIN-1", "issue-uuid-1", title="Parent"),
            "LIN-2": raw_issue("LIN-2", "issue-uuid-2", title="Existing"),
        }
        self.comments = {}
        self.calls = []

    def _by_native(self, native):
        return next(value for value in self.issues.values() if value["id"] == native)

    def __call__(self, document, variables):
        self.calls.append((document, copy.deepcopy(variables)))
        if "FoundryLinearIssues" in document:
            return {"data": {"issues": connection([copy.deepcopy(v) for v in self.issues.values()])}}
        if "FoundryLinearIssue(" in document:
            return {"data": {"issue": copy.deepcopy(self.issues.get(variables["id"]))}}
        if "FoundryLinearIssueCreate" in document:
            value = variables["input"]
            identifier = f"LIN-{len(self.issues) + 1}"
            created = raw_issue(identifier, value["id"], title=value["title"], body=value["description"])
            self._apply(created, value)
            if parent_id := value.get("parentId"):
                parent = self._by_native(parent_id)
                created["parent"] = {"id": parent["id"], "identifier": parent["identifier"]}
                parent["children"]["nodes"].append(
                    {"id": created["id"], "identifier": created["identifier"]}
                )
            self.issues[identifier] = created
            return {"data": {"issueCreate": {
                "success": True, "issue": {"id": created["id"], "identifier": identifier},
            }}}
        if "FoundryLinearIssueRelationCreate" in document:
            value = variables["input"]
            source, target = self._by_native(value["issueId"]), self._by_native(value["relatedIssueId"])
            source["relations"]["nodes"].append({
                "type": value["type"],
                "relatedIssue": {"id": target["id"], "identifier": target["identifier"]},
            })
            target["inverseRelations"]["nodes"].append({
                "type": value["type"],
                "issue": {"id": source["id"], "identifier": source["identifier"]},
            })
            relation = {
                "id": "relation-1", "type": value["type"],
                "issue": {"id": source["id"], "identifier": source["identifier"]},
                "relatedIssue": {"id": target["id"], "identifier": target["identifier"]},
            }
            return {"data": {"issueRelationCreate": {"success": True, "issueRelation": relation}}}
        if "FoundryLinearCommentCreate" in document:
            value = variables["input"]
            issue = self._by_native(value["issueId"])
            comment = {
                "id": f"comment-{len(self.comments) + 1}", "body": value["body"],
                "issue": {"id": issue["id"], "identifier": issue["identifier"]},
            }
            self.comments[comment["id"]] = comment
            return {"data": {"commentCreate": {"success": True, "comment": copy.deepcopy(comment)}}}
        if "FoundryLinearComment(" in document:
            return {"data": {"comment": copy.deepcopy(self.comments.get(variables["id"]))}}
        raise AssertionError("unexpected GraphQL document")

    @staticmethod
    def _apply(issue, values):
        if "description" in values:
            issue["description"] = values["description"]
        if "stateId" in values:
            issue["state"] = {"id": values["stateId"], "name": "provider display ignored"}
        if "priority" in values:
            issue["priority"] = values["priority"]
        if "estimate" in values:
            issue["estimate"] = values["estimate"]
        if "projectMilestoneId" in values:
            issue["projectMilestone"] = (
                {"id": values["projectMilestoneId"], "name": "provider display ignored"}
                if values["projectMilestoneId"] else None
            )
        if "labelIds" in values:
            names = {
                "label-feature": "Feature display", "label-bug": "Bug display",
                "label-pilot": "Pilot display", "label-api": "API display",
            }
            issue["labels"] = connection([{"id": item, "name": names[item]} for item in values["labelIds"]])
        if "parentId" in values:
            parent = values["parentId"]
            issue["parent"] = None if parent is None else {
                "id": parent, "identifier": "LIN-1",
            }


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    wire = LinearWire()
    return LinearTracker(token="linear-test-secret", transport=wire), wire


def proof(issue_id, body):
    criteria = acceptance_criteria(body)
    return {
        "proof_id": "a" * 64,
        "issue": {
            "id": issue_id, "ac_digest": acceptance_digest(criteria),
            "criteria": [{**criterion, "verdict": "pass"} for criterion in criteria],
        },
    }


def project_entry(project=PROJECT):
    return {"key": project.key, "id": project.id, **copy.deepcopy(project.extra)}


def test_factory_recognizes_linear_without_changing_youtrack_devhub_or_stub(monkeypatch):
    secrets = {
        "YOUTRACK_URL": "https://example.youtrack.cloud",
        "YOUTRACK_TOKEN": "youtrack-secret",
        "LINEAR_API_TOKEN": "linear-secret",
        "DEVHUB_TRACKER_TOKEN": "t" * 24,
        "DEVHUB_TRACKER_PROOF_SECRET": "p" * 32,
    }
    monkeypatch.setattr(foundry.config, "require", secrets.__getitem__)
    monkeypatch.setattr(
        foundry.config, "require_public", lambda key: "https://devhub.example.test",
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
    tracker, monkeypatch,
):
    instance, wire = tracker
    listed = instance.search(PROJECT)
    assert listed[0].id == "LIN-1"
    assert listed[0].state == "ready"
    assert listed[0].milestone == "M1"
    assert listed[0].type == "Feature"
    assert listed[0].labels == ["pilot"]

    created = instance.create_issue(
        PROJECT, "Round trip", "- [ ] exact acceptance",
        fields={
            "State": "ready", "Priority": "P1", "Estimate": 5,
            "Milestone": "M1", "Type": "Feature", "Labels": ["pilot"],
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
        registry, "checkout_repository_identity",
        lambda cwd=None: "github.com/acme/widgets",
    )
    monkeypatch.setattr(registry, "load", lambda: {"linear": {
        "actual-checkout": project_entry(),
    }})
    fresh.resolve_project("decoy")
    final = fresh.get_issue(created.id)
    assert final.state == "ready"
    assert final.pr_url is None
    assert (final.priority, final.estimate, final.type, final.labels) == (
        "P1", 5, "Feature", ["pilot"],
    )
    assert final.body == "- [ ] exact acceptance" and (final.ac_done, final.ac_total) == (0, 1)
    assert Link("depends-on", "outward", "LIN-2") in final.links
    assert wire.comments["comment-1"]["body"] == "bounded progress"


def test_relation_reads_normalize_hierarchy_and_both_blocking_sides(tracker):
    instance, wire = tracker
    parent, child = wire.issues["LIN-1"], wire.issues["LIN-2"]
    parent["children"] = connection([
        {"id": child["id"], "identifier": child["identifier"]},
    ])
    child["parent"] = {"id": parent["id"], "identifier": parent["identifier"]}
    parent["relations"] = connection([{
        "type": "blocks",
        "relatedIssue": {"id": child["id"], "identifier": child["identifier"]},
    }])
    child["inverseRelations"] = connection([{
        "type": "blocks",
        "issue": {"id": parent["id"], "identifier": parent["identifier"]},
    }])

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
    tracker, relation_type, collection_name, target_name, operation,
):
    instance, wire = tracker
    wire.issues["LIN-1"][collection_name] = connection([{
        "type": relation_type,
        target_name: {"id": "issue-uuid-2", "identifier": "LIN-2"},
    }])

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
        TrackerCapabilityUnavailableError, match="existing-issue-field-replacement",
    ):
        instance.update_fields("LIN-2", {"Priority": "P0"}, project=PROJECT)
    with pytest.raises(
        TrackerCapabilityUnavailableError, match="existing-issue-field-replacement",
    ):
        instance.set_state("LIN-2", "review", project=PROJECT)
    with pytest.raises(
        TrackerCapabilityUnavailableError, match="existing-issue-parent-replacement",
    ):
        instance.link("LIN-2", "subtask-of", "LIN-1", project=PROJECT)
    with pytest.raises(BodyUpdateUnavailableError, match="anti-écrasement"):
        instance.update_body(
            Issue(id="LIN-2", title="Existing"), "old", "new", project=PROJECT,
        )
    with pytest.raises(AcceptanceSyncUnavailableError, match="anti-écrasement"):
        instance.sync_acceptance_body(
            "LIN-2", "- [ ] AC", "- [x] AC", proof("LIN-2", "- [ ] AC"),
            project=PROJECT,
        )
    assert len(wire.calls) == before


def test_normalization_rejects_missing_or_unknown_label_identifiers(tracker):
    instance, wire = tracker
    del wire.issues["LIN-1"]["labels"]["nodes"][0]["id"]
    with pytest.raises(LinearTrackerError) as missing:
        instance.search(PROJECT)
    assert missing.value.code == "invalid_response"

    wire.issues["LIN-1"] = raw_issue("LIN-1", "issue-uuid-1", title="Parent")
    wire.issues["LIN-1"]["labels"]["nodes"][0]["id"] = "label-unknown"
    with pytest.raises(LinearBindingError) as unknown:
        instance.search(PROJECT)
    assert unknown.value.code == "label_id_unmapped"


def test_normalization_rejects_unmapped_milestone_identifier(tracker):
    instance, wire = tracker
    wire.issues["LIN-1"]["projectMilestone"] = {
        "id": "milestone-unknown", "name": "M1",
    }
    with pytest.raises(LinearBindingError) as error:
        instance.search(PROJECT)
    assert error.value.code == "milestone_id_unmapped"


def test_query_issue_resolves_fresh_binding_and_projects_typed_adr_unavailability(
    tracker, monkeypatch,
):
    _, wire = tracker
    fresh = LinearTracker(token="linear-test-secret", transport=wire)
    checkout_reads = []
    decoy = copy.deepcopy(project_entry())
    decoy.update({
        "key": "WRONG", "id": "wrong-project",
        "canonical_repo": "github.com/acme/not-widgets",
    })

    monkeypatch.setattr(foundry, "tracker", lambda name=None: fresh)
    monkeypatch.setenv("PROJECT_REPO", "widgets")
    monkeypatch.setattr(
        registry, "checkout_repository_identity",
        lambda cwd=None: checkout_reads.append(cwd) or "github.com/acme/widgets",
    )
    monkeypatch.setattr(registry, "load", lambda: {"linear": {
        "widgets": decoy,
        "actual-checkout": project_entry(),
        "actual-alias": project_entry(),
    }})
    monkeypatch.setattr(
        registry, "resolve",
        lambda *_args: pytest.fail("Linear query must not resolve from a basename"),
    )

    result = query.issue("LIN-2")

    assert checkout_reads == [None]
    assert fresh._active_project == PROJECT
    assert result["issue"]["id"] == "LIN-2"
    assert result["adrs"] == {
        "status": "unavailable",
        "tracker": "linear",
        "capability": "adr-knowledge-base",
    }


def test_record_review_proof_resolves_fresh_binding_before_issue_read(
    tracker, monkeypatch, tmp_path, capsys,
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
        '{"outcomes": [], "quality": {"verdict": "pass"}}', encoding="utf-8",
    )
    monkeypatch.setattr(foundry, "tracker", lambda name=None: fresh)
    monkeypatch.setenv("PROJECT_REPO", "decoy")
    monkeypatch.setattr(
        registry, "checkout_repository_identity",
        lambda cwd=None: checkout_reads.append(cwd) or "github.com/acme/widgets",
    )
    monkeypatch.setattr(registry, "load", lambda: {"linear": {
        "decoy": {
            **project_entry(), "key": "WRONG", "id": "wrong-project",
            "canonical_repo": "github.com/acme/other",
        },
        "canonical": project_entry(),
    }})
    monkeypatch.setattr(
        registry, "resolve",
        lambda *_args: pytest.fail("Linear proof must not resolve from a basename"),
    )
    monkeypatch.setattr(routing, "repository_identity", lambda root=None: "acme/widgets")
    monkeypatch.setattr(routing, "AcceptanceProofStore", ProofStore)

    routing.main([
        "record-review-proof",
        "--issue", "LIN-2",
        "--diff-hash", "d" * 64,
        "--claim-id", "c" * 64,
        "--outcomes-file", str(outcomes),
        "--base", "b" * 40,
        "--root", str(tmp_path),
    ])

    assert checkout_reads == [str(tmp_path)]
    assert captured["repository"] == "acme/widgets"
    assert captured["issue_id"] == "LIN-2"
    assert captured["issue_body"] == "- [ ] acceptance"
    assert '"proof_id": "' + "f" * 64 + '"' in capsys.readouterr().out


def test_checkout_resolution_refuses_without_canonical_binding_before_linear_read(
    tracker, monkeypatch,
):
    _, wire = tracker
    fresh = LinearTracker(token="linear-test-secret", transport=wire)
    monkeypatch.setattr(foundry, "tracker", lambda name=None: fresh)
    monkeypatch.setenv("PROJECT_REPO", "widgets")
    monkeypatch.setattr(
        registry, "checkout_repository_identity",
        lambda cwd=None: "github.com/acme/widgets",
    )
    monkeypatch.setattr(registry, "load", lambda: {"linear": {
        "widgets": {
            **project_entry(), "canonical_repo": "github.com/acme/other",
        },
    }})

    with pytest.raises(SystemExit, match="canonical_repo du checkout"):
        query.issue("LIN-2")

    assert wire.calls == []


def test_checkout_resolution_refuses_contradictory_canonical_bindings(
    tracker, monkeypatch,
):
    _, wire = tracker
    fresh = LinearTracker(token="linear-test-secret", transport=wire)
    contradictory = project_entry()
    contradictory.update({"key": "OTHER", "id": "other-project"})
    monkeypatch.setattr(registry, "load", lambda: {"linear": {
        "first": project_entry(),
        "second": contradictory,
    }})

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
def test_linear_lifecycle_preflight_stops_before_every_codehost_effect(
    tracker, monkeypatch, operation,
):
    instance, wire = tracker
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
        registry, "checkout_repository_identity",
        lambda cwd=None: "github.com/acme/widgets",
    )
    monkeypatch.setattr(registry, "load", lambda: {"linear": {
        "decoy": {
            **project_entry(), "key": "WRONG", "id": "wrong-project",
            "canonical_repo": "github.com/acme/other",
        },
        "canonical": project_entry(),
    }})

    def command(*args, **_kwargs):
        if args[-2:] == ("--abbrev-ref", "HEAD"):
            return "feat/lin-2-safe"
        effects.append(("git", args))
        return ""

    monkeypatch.setattr(issue, "_sh", command)

    expected = (
        "linear.github-pr-projection" if operation == "openpr"
        else "linear.existing-issue-state-replacement"
    )
    with pytest.raises(TrackerCapabilityUnavailableError, match=expected):
        if operation == "openpr":
            issue.openpr("LIN-2")
        else:
            issue.merge("LIN-2", "17")

    assert effects == []
    assert wire.calls and all("mutation" not in document.lower() for document, _ in wire.calls)


def test_malformed_and_provider_error_payloads_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    sentinel = "LINEAR_SECRET_MUST_NOT_LEAK"
    malformed = LinearTracker(
        token=sentinel, transport=lambda *_: {"data": {"issue": "bad"}},
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


def test_unsupported_capabilities_are_typed_and_never_fall_back(tracker):
    instance, wire = tracker
    with pytest.raises(TrackerCapabilityUnavailableError, match="adr-knowledge-base"):
        instance.list_adrs(PROJECT)
    with pytest.raises(TrackerCapabilityUnavailableError, match="provider-native-search-query"):
        instance.search(PROJECT, "display name lookup")
    with pytest.raises(BodyUpdateUnavailableError, match="anti-écrasement"):
        instance.update_body(
            Adr(id="LIN-ADR-0001", title="ADR"), "old", "new", project=PROJECT,
        )
    assert wire.calls == []


def test_pr_projection_is_typed_unavailable_before_any_provider_write(tracker):
    instance, wire = tracker
    before = len(wire.calls)
    with pytest.raises(
        TrackerCapabilityUnavailableError, match="linear.github-pr-projection",
    ):
        instance.update_fields(
            "LIN-2", {"GitHub PR": "https://github.com/acme/widgets/pull/1"},
            project=PROJECT,
        )
    assert wire.calls[before:] == []
