"""Controlled Linear transport proof; no test contacts a Linear workspace."""
from __future__ import annotations

import copy

import pytest

import foundry
from foundry.models import Adr, Project
from foundry.routing import acceptance_criteria, acceptance_digest
from foundry.trackers.base import (
    BodyUpdateUnavailableError,
    TrackerCapabilityUnavailableError,
    TrackerConflictError,
)
from foundry.trackers.linear import (
    LinearBindingError,
    LinearTracker,
    LinearTrackerError,
)


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
            {"id": "label-pilot", "name": "pilot"},
        ]),
        "parent": None, "children": connection([]), "relations": connection([]),
        "inverseRelations": connection([]), "attachments": connection([]),
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
        self.after_update = None

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
        if "FoundryLinearIssueUpdate" in document:
            issue = self._by_native(variables["id"])
            self._apply(issue, variables["input"])
            if self.after_update is not None:
                self.after_update(issue)
            return {"data": {"issueUpdate": {
                "success": True,
                "issue": {"id": issue["id"], "identifier": issue["identifier"]},
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
        if "FoundryLinearAttachmentCreate" in document:
            value = variables["input"]
            issue = self._by_native(value["issueId"])
            attachment = {
                "id": "attachment-1", "url": value["url"], "metadata": value["metadata"],
                "issue": {"id": issue["id"], "identifier": issue["identifier"]},
            }
            issue["attachments"]["nodes"].append(copy.deepcopy(attachment))
            return {"data": {"attachmentCreate": {"success": True, "attachment": attachment}}}
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
                "label-pilot": "pilot", "label-api": "api",
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


def test_factory_recognizes_linear_without_changing_existing_providers(monkeypatch):
    monkeypatch.setattr(foundry.config, "require", lambda key: "linear-secret")
    assert isinstance(foundry.tracker("linear"), LinearTracker)
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


def test_controlled_round_trip_retains_ids_links_state_pr_and_acceptance_text(tracker):
    instance, wire = tracker
    listed = instance.search(PROJECT)
    assert listed[0].id == "LIN-1"
    assert listed[0].state == "ready"
    assert listed[0].milestone == "M1"
    assert listed[0].type == "Feature"

    created = instance.create_issue(
        PROJECT, "Round trip", "- [ ] exact acceptance",
        fields={
            "State": "ready", "Priority": "P1", "Estimate": 5,
            "Milestone": "M1", "Type": "Feature", "Labels": ["pilot"],
        },
        parent="LIN-1",
    )
    assert created.id == "LIN-3"
    assert any(link.type == "subtask-of" and link.target == "LIN-1" for link in created.links)

    updated = instance.update_fields(
        created.id,
        {"Priority": "P0", "Estimate": 8, "Type": "Bug", "Labels": ["api"]},
        project=PROJECT,
    )
    assert (updated.priority, updated.estimate, updated.type, updated.labels) == (
        "P0", 8, "Bug", ["api"],
    )
    instance.set_state(created.id, "review", project=PROJECT)
    instance.link(created.id, "depends-on", "LIN-2", project=PROJECT)
    instance.add_comment(created.id, "bounded progress", project=PROJECT)
    instance.update_fields(
        created.id, {"GitHub PR": "https://github.com/acme/widgets/pull/17"},
        project=PROJECT,
    )

    expected = "- [ ] exact acceptance"
    wanted = "- [x] exact acceptance"
    assert instance.sync_acceptance_body(
        created.id, expected, wanted, proof(created.id, expected), project=PROJECT,
    ) is True
    final = instance.get_issue(created.id)
    assert final.state == "review"
    assert final.pr_url == "https://github.com/acme/widgets/pull/17"
    assert final.body == wanted and (final.ac_done, final.ac_total) == (1, 1)
    assert any(link.type == "depends-on" and link.target == "LIN-2" for link in final.links)
    assert wire.comments["comment-1"]["body"] == "bounded progress"


def test_update_conflict_is_detected_after_one_write_without_retry(tracker):
    instance, wire = tracker
    instance.search(PROJECT)
    wire.after_update = lambda issue: issue.update(priority=4)
    before = len([call for call in wire.calls if "FoundryLinearIssueUpdate" in call[0]])
    with pytest.raises(TrackerConflictError, match="no retry"):
        instance.update_fields("LIN-2", {"Priority": "P0"}, project=PROJECT)
    after = len([call for call in wire.calls if "FoundryLinearIssueUpdate" in call[0]])
    assert after - before == 1


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
    with pytest.raises(BodyUpdateUnavailableError, match="ADR"):
        instance.update_body(
            Adr(id="LIN-ADR-0001", title="ADR"), "old", "new", project=PROJECT,
        )
    assert wire.calls == []


def test_pr_projection_rejects_cross_repository_url_before_write(tracker):
    instance, wire = tracker
    instance.search(PROJECT)
    before = len(wire.calls)
    with pytest.raises(ValueError, match="outside"):
        instance.update_fields(
            "LIN-2", {"GitHub PR": "https://github.com/other/widgets/pull/1"},
            project=PROJECT,
        )
    assert not any("FoundryLinearAttachmentCreate" in call[0] for call in wire.calls[before:])
