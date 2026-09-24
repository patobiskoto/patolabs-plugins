"""Controlled Linear transport proof; no test contacts a Linear workspace."""
from __future__ import annotations

import copy
import hashlib
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import foundry
from foundry import evidence_plane, issue, query, registry, routing, write
from foundry.models import Adr, Check, Issue, Link, Project, PullRequest, TransitionContext
from foundry.routing import acceptance_criteria, acceptance_digest
from foundry.trackers.base import (
    AcceptanceSyncUnavailableError,
    BodyUpdateUnavailableError,
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
        self.git_automation_states = connection([])

    def _by_native(self, native):
        return next(value for value in self.issues.values() if value["id"] == native)

    def __call__(self, document, variables):
        self.calls.append((document, copy.deepcopy(variables)))
        if "FoundryLinearTeamGitAutomationStates" in document:
            return {"data": {"team": {
                "id": "team-uuid",
                "gitAutomationStates": copy.deepcopy(self.git_automation_states),
            }}}
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
                "id": value.get("id", f"comment-{len(self.comments) + 1}"),
                "body": value["body"],
                "issue": {"id": issue["id"], "identifier": issue["identifier"]},
            }
            if comment["id"] in self.comments:
                return {"errors": [{"message": "duplicate"}], "data": {}}
            self.comments[comment["id"]] = comment
            issue["comments"]["nodes"].append({
                "id": comment["id"], "body": comment["body"],
                "createdAt": "2026-09-20T10:02:00Z",
            })
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


def proof(issue_id, body, *, head="a" * 40, base="b" * 40, diff_hash="c" * 64):
    criteria = acceptance_criteria(body)
    value = {
        "schema_version": 1,
        "issue": {
            "id": issue_id, "ac_digest": acceptance_digest(criteria),
            "criteria": [{**criterion, "verdict": "pass"} for criterion in criteria],
        },
        "review": {
            "role": "reviewer", "generation": 1, "claim_digest": "d" * 64,
        },
        "coordinates": {"head": head, "diff_hash": diff_hash, "base": base},
        "quality": "mergeable",
    }
    import hashlib
    import json
    value["proof_id"] = hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return value


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
        TrackerCapabilityUnavailableError, match="lifecycle-proof",
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
def test_linear_lifecycle_preflight_stops_before_every_codehost_effect_when_disabled(
    tracker, monkeypatch, operation,
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

    with pytest.raises(
        TrackerCapabilityUnavailableError, match="linear.append-only-lifecycle-proof",
    ):
        if operation == "openpr":
            issue.openpr("LIN-2")
        else:
            issue.merge("LIN-2", "17")

    assert effects == []
    assert wire.calls and all("mutation" not in document.lower() for document, _ in wire.calls)


def test_linear_lifecycle_preflight_advertises_only_bounded_operations(tracker):
    instance, wire = tracker
    before = len(wire.calls)

    assert instance.preflight_issue_operation("openpr") is None
    assert instance.preflight_issue_operation("merge") is None
    with pytest.raises(
        TrackerCapabilityUnavailableError, match="linear.lifecycle:close-epic",
    ):
        instance.preflight_issue_operation("close-epic")

    assert len(wire.calls) == before


def test_linear_merge_effect_preflight_allows_safe_team_automation(tracker):
    instance, wire = tracker
    wire.git_automation_states = connection([
        {
            "id": "automation-started", "event": "start",
            "state": {"id": STATE_IDS["in-progress"], "type": "started"},
        },
        {
            "id": "automation-review", "event": "review",
            "state": {"id": STATE_IDS["review"], "type": "started"},
        },
    ])
    instance._activate(PROJECT)

    assert instance.preflight_merge_effect() is None
    assert wire.calls[-1][1] == {"id": "team-uuid"}


def test_linear_merge_effect_preflight_allows_merge_no_action(tracker):
    instance, wire = tracker
    wire.git_automation_states = connection([
        {"id": "automation-merge-no-action", "event": "merge", "state": None},
    ])
    instance._activate(PROJECT)
    merge_calls = []

    write.preflight_merge_effect(instance)
    merge_calls.append("github-merge")

    assert merge_calls == ["github-merge"]


def test_linear_merge_effect_preflight_allows_duplicate_workflow_state(tracker):
    instance, wire = tracker
    wire.git_automation_states = connection([
        {
            "id": "automation-merge-duplicate", "event": "merge",
            "state": {"id": "duplicate-state", "type": "duplicate"},
        },
    ])
    instance._activate(PROJECT)

    assert instance.preflight_merge_effect() is None


def test_linear_merge_effect_preflight_refuses_completed_merge_automation(tracker):
    instance, wire = tracker
    wire.git_automation_states = connection([
        {
            "id": "automation-merge", "event": "merge",
            "state": {"id": STATE_IDS["done"], "type": "completed"},
        },
    ])
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
        "nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "next"},
    }
    instance._activate(PROJECT)

    with pytest.raises(LinearTrackerError, match="nested_pagination_unavailable"):
        instance.preflight_merge_effect()


def test_linear_lifecycle_projects_state_pr_and_acceptance_without_replacement(
    tracker, monkeypatch,
):
    instance, wire = tracker
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40, base_sha="b" * 40, review_digest="c" * 64,
    )
    done = TransitionContext(
        pr_url=review.pr_url, head_sha=review.head_sha, base_sha=review.base_sha,
        review_digest=review.review_digest, merge_sha="e" * 40,
    )

    instance.set_state("LIN-2", "in-progress", project=PROJECT)
    instance.set_state("LIN-2", "review", context=review, project=PROJECT)
    result = write.sync_acceptance(
        instance, "LIN-2", "- [ ] acceptance",
        proof("LIN-2", "- [ ] acceptance"),
    )
    instance.set_state("LIN-2", "done", context=done, project=PROJECT)

    projected = instance.get_issue("LIN-2")
    assert projected.state == "done"
    assert projected.pr_url == review.pr_url
    assert (projected.ac_done, projected.ac_total) == (1, 1)
    assert result == {
        "status": "proof-projected", "checked": 1,
        "audit": "append-only-proof",
    }
    native = wire.issues["LIN-2"]
    assert native["state"]["id"] == STATE_IDS["ready"]
    assert native["description"] == "- [ ] acceptance"
    assert native["priority"] == 2
    assert len(native["comments"]["nodes"]) == 4
    assert all("Foundry lifecycle proof" in row["body"]
               for row in native["comments"]["nodes"])


def test_linear_lifecycle_replay_is_idempotent_and_uses_deterministic_comment_id(tracker):
    instance, wire = tracker
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40, base_sha="b" * 40, review_digest="c" * 64,
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


def test_linear_native_checked_ac_requires_append_only_review_proof(tracker, monkeypatch):
    instance, wire = tracker
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    body = "- [x] acceptance"
    wire.issues["LIN-2"]["description"] = body
    instance._activate(PROJECT)
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40, base_sha="b" * 40, review_digest="c" * 64,
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
        head_sha="a" * 40, base_sha="b" * 40, review_digest="c" * 64,
    )
    second = TransitionContext(
        pr_url=first.pr_url,
        head_sha="d" * 40, base_sha=first.base_sha, review_digest="e" * 64,
    )

    instance.set_state("LIN-2", "review", context=first, project=PROJECT)
    write.sync_acceptance(instance, "LIN-2", body, proof("LIN-2", body))
    instance.set_state("LIN-2", "review", context=second, project=PROJECT)

    corrected = instance.get_issue("LIN-2")
    assert corrected.state == "review"
    assert corrected.ac_done == 0
    second_proof = proof(
        "LIN-2", body, head=second.head_sha, base=second.base_sha,
        diff_hash=second.review_digest,
    )
    write.sync_acceptance(instance, "LIN-2", body, second_proof)
    instance.set_state(
        "LIN-2", "done",
        context=TransitionContext(
            pr_url=second.pr_url, head_sha=second.head_sha,
            base_sha=second.base_sha, review_digest=second.review_digest,
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
            head_sha="a" * 40, base_sha="b" * 40, review_digest="c" * 64,
        ),
        TransitionContext(
            pr_url="https://github.com/acme/widgets/pull/17",
            head_sha="d" * 40, base_sha="b" * 40, review_digest="e" * 64,
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
    tracker, monkeypatch,
):
    instance, wire = tracker
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    monkeypatch.setattr(issue, "_observe_receipt", lambda *_args: None)
    monkeypatch.setattr(issue, "_cleanup_branch", lambda _branch: "linked-worktree")
    old = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40, base_sha="b" * 40, review_digest="c" * 64,
    )
    instance.set_state("LIN-2", "review", context=old, project=PROJECT)
    write.sync_acceptance(
        instance, "LIN-2", "- [ ] acceptance",
        proof("LIN-2", "- [ ] acceptance"),
    )

    new_diff = b"corrected-diff"
    new_digest = hashlib.sha256(new_diff).hexdigest()
    pr = PullRequest(
        number=17, url=old.pr_url, head="feat/lin-2", base="main",
        base_sha="b" * 40, sha="d" * 40,
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
        name="github", resolve_repo=lambda: "acme/widgets",
        get_pr=lambda *_args: pr,
        merge_pr=merge_pr,
        delete_branch=lambda *_args: None,
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: instance)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "git_head", lambda: pr.sha)
    monkeypatch.setattr(issue, "git_diff", lambda **_kwargs: new_diff)
    monkeypatch.setattr(issue, "repository_identity", lambda: "github.com/acme/widgets")
    monkeypatch.setattr(write, "ci_gate", lambda *_args, **_kwargs: {
        "passed": True, "waived": False, "total": 1,
        "pending": [], "failing": [],
    })
    requested = []

    class Store:
        def __init__(self, _repository):
            pass

        def valid_for_merge(self, **coordinates):
            requested.append(coordinates)
            return proof(
                "LIN-2", "- [ ] acceptance", head=pr.sha,
                base=pr.base_sha, diff_hash=new_digest,
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
    assert {comment["issue"]["identifier"] for comment in wire.comments.values()} == {"LIN-2"}


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
    tracker, monkeypatch,
):
    instance, wire = tracker
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40, base_sha="b" * 40, review_digest="c" * 64,
    )
    done = TransitionContext(
        pr_url=review.pr_url, head_sha=review.head_sha, base_sha=review.base_sha,
        review_digest=review.review_digest, merge_sha="e" * 40,
    )

    instance.set_state("LIN-2", "in-progress", project=PROJECT)
    wire.issues["LIN-2"]["state"]["id"] = STATE_IDS["in-progress"]
    instance.set_state("LIN-2", "review", context=review, project=PROJECT)
    assert instance.get_issue("LIN-2").state == "review"

    wire.issues["LIN-2"]["state"]["id"] = STATE_IDS["review"]
    write.sync_acceptance(
        instance, "LIN-2", "- [ ] acceptance",
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
        head_sha="a" * 40, base_sha="b" * 40, review_digest="c" * 64,
    )
    second = TransitionContext(
        pr_url=first.pr_url,
        head_sha="d" * 40, base_sha=first.base_sha, review_digest="e" * 64,
    )
    instance.set_state("LIN-2", "review", context=first, project=PROJECT)
    wire.issues["LIN-2"]["state"]["id"] = STATE_IDS["blocked"]

    with pytest.raises(TrackerConflictError, match="native state changed"):
        instance.set_state("LIN-2", "review", context=second, project=PROJECT)
    with pytest.raises(TrackerConflictError, match="native state changed"):
        instance.get_issue("LIN-2")
    assert len(wire.comments) == 1


def test_linear_native_done_requires_well_formed_foundry_done_receipt(
    tracker, monkeypatch,
):
    instance, wire = tracker
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40, base_sha="b" * 40, review_digest="c" * 64,
    )
    done = TransitionContext(
        pr_url=review.pr_url, head_sha=review.head_sha, base_sha=review.base_sha,
        review_digest=review.review_digest, merge_sha="e" * 40,
    )
    instance.set_state("LIN-2", "review", context=review, project=PROJECT)
    write.sync_acceptance(
        instance, "LIN-2", "- [ ] acceptance",
        proof("LIN-2", "- [ ] acceptance"),
    )
    wire.issues["LIN-2"]["state"]["id"] = STATE_IDS["done"]
    instance.set_state("LIN-2", "done", context=done, project=PROJECT)

    row = next(
        item for item in wire.issues["LIN-2"]["comments"]["nodes"]
        if ":state-done:" in item["body"]
    )
    decoded = instance._decode_lifecycle_comment("LIN-2", row["body"])
    assert decoded is not None
    malformed = dict(decoded[1])
    malformed.pop("merge_sha")
    _marker, row["body"], row["id"] = instance._lifecycle_marker(
        "state-done", "LIN-2", malformed,
    )

    with pytest.raises(TrackerConflictError, match="state proof malformed"):
        instance.get_issue("LIN-2")


def test_linear_lifecycle_refuses_unmapped_historical_native_state_receipt(tracker):
    instance, wire = tracker
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40, base_sha="b" * 40, review_digest="c" * 64,
    )
    instance.set_state("LIN-2", "review", context=review, project=PROJECT)
    row = wire.issues["LIN-2"]["comments"]["nodes"][0]
    decoded = instance._decode_lifecycle_comment("LIN-2", row["body"])
    assert decoded is not None
    malformed = {**decoded[1], "native_state_id": "state-unrelated"}
    _marker, row["body"], row["id"] = instance._lifecycle_marker(
        "state-review", "LIN-2", malformed,
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


def test_linear_lifecycle_refuses_divergent_issue_readback_after_comment_effect(tracker):
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


def test_linear_acceptance_projection_refuses_incomplete_or_stale_proof_before_write(tracker):
    instance, wire = tracker
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/17",
        head_sha="a" * 40, base_sha="b" * 40, review_digest="c" * 64,
    )
    instance.set_state("LIN-2", "review", context=review, project=PROJECT)
    before = len(wire.comments)
    invalid = proof("LIN-2", "- [ ] acceptance")
    invalid["quality"] = "blocked"

    with pytest.raises(TrackerConflictError, match="malformed or stale"):
        instance.project_acceptance_proof(
            "LIN-2", "- [ ] acceptance", invalid, checked=1, project=PROJECT,
        )

    assert len(wire.comments) == before


def test_linear_cockpit_projection_requires_complete_go_and_never_changes_lifecycle(tracker):
    instance, wire = tracker
    repository = "github.com/acme/widgets"
    issue_id = "LIN-2"
    body = "- [ ] acceptance"
    head, base, diff_hash = "a" * 40, "b" * 40, "c" * 64
    pr = PullRequest(
        number=17, url="https://github.com/acme/widgets/pull/17",
        head="feat/lin-2", base="main", base_sha=base, sha=head,
    )
    criteria = acceptance_criteria(body)
    test_receipt = evidence_plane.create_test_receipt(
        repository=repository, issue_id=issue_id, head_sha=head,
        diff_hash=diff_hash, status="passed", result_digest="f" * 64,
    )
    observed_at = evidence_plane._now_ms()
    checks = evidence_plane._create_ci_receipt(
        source="check_runs", repository=repository, head_sha=head,
        observed_at=observed_at,
        checks=[Check(name="tests", status="completed", conclusion="success")],
    )
    statuses = evidence_plane._create_ci_receipt(
        source="commit_statuses", repository=repository, head_sha=head,
        observed_at=observed_at, checks=[],
    )
    envelope = evidence_plane.evidence_envelope(
        repository=repository, issue_id=issue_id,
        ac_digest=acceptance_digest(criteria), pr=pr, diff_hash=diff_hash,
        review_proof=proof(issue_id, body), test_receipt=test_receipt,
        check_runs_receipt=checks, commit_statuses_receipt=statuses,
    )

    before = len(wire.comments)
    with pytest.raises(
        TrackerCapabilityUnavailableError, match="cockpit-evidence-complete-go",
    ):
        instance.project_cockpit_evidence(
            issue_id, {}, repository=repository,
            ac_digest=acceptance_digest(criteria), pr=pr,
            diff_hash=diff_hash, project=PROJECT,
        )
    assert len(wire.comments) == before

    assert instance.project_cockpit_evidence(
        issue_id, envelope, repository=repository,
        ac_digest=acceptance_digest(criteria), pr=pr,
        diff_hash=diff_hash, project=PROJECT,
    ) is True
    assert instance.project_cockpit_evidence(
        issue_id, envelope, repository=repository,
        ac_digest=acceptance_digest(criteria), pr=pr,
        diff_hash=diff_hash, project=PROJECT,
    ) is False

    projected = instance.get_issue(issue_id)
    assert projected.state == "ready"
    assert projected.pr_url is None
    assert (projected.ac_done, projected.ac_total) == (0, 1)
    assert len(wire.comments) == before + 1


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
