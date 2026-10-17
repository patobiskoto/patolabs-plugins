"""PAT-228: provider requests of an Epic campaign, counted per step and per lookup.

Doubles only.  The Linear figures are counted at the GraphQL transport double
(``FaithfulLinearWire``) with one closed Epic in the project, whose replay is what makes the
project listing expensive (``docs/linear-request-cost.md``).
"""
import collections
import copy
import json
import re
from types import SimpleNamespace

import pytest

from foundry import campaign_defer as cd
from foundry import campaign_effects as ce
import test_campaign_effects
from test_campaign_effects import WorldTracker, step
from test_epic_read_cost import _build, _close, _native
from test_linear_tracker import STATE_IDS, connection

env = test_campaign_effects.env  # the shared fixture, under the name pytest looks up
_OPERATION = re.compile(r"(?:query|mutation)\s+(\w+)")
COMPANION = "Nits LIN-1"
FOLLOW_UP = "Review remarks deferred from LIN-1"


class CountingTracker(WorldTracker):
    """The campaign tracker double with a request counter: one request per tracker call."""

    def __init__(self, world):
        super().__init__(world)
        self.requests = 0

    def request_count(self):
        return self.requests

    def get_epic_subgraph(self, project, epic, *, depth, nodes):
        self.requests += 3
        return super().get_epic_subgraph(project, epic, depth=depth, nodes=nodes)

    def get_issue(self, issue_id):
        self.requests += 1
        return super().get_issue(issue_id)

    def search(self, project, query=""):
        self.requests += 10
        return super().search(project, query)


@pytest.fixture
def counted(env):
    env.tracker = CountingTracker(env.world)
    return env


def test_a_waiting_pass_reports_its_observation_and_no_effect(counted):
    result = step(counted)
    assert result.decision.kind == "start"
    assert result.requests == {"observation": 3, "effect": 0, "subprocesses": None}
    assert ce.valid_requests_field(result.requests)
    assert result.to_dict()["requests"] == result.requests


def test_the_finished_line_and_the_subcommand_lines_carry_the_requests(counted):
    run = counted.world.runner

    def runner(argv, **kwargs):
        done = run(argv, **kwargs)
        if argv[:3] == [ce.sys.executable, ce.Runtime.cli.fget(counted.runtime), "issue"]:
            done = SimpleNamespace(returncode=done.returncode, stdout=done.stdout,
                                   stderr="foundry linear_requests=13\n")
        return done

    counted.runtime.runner = runner
    result = step(counted)
    assert result.outcome == "executed"
    assert result.requests["subprocesses"] == 13
    finished = [json.loads(line) for line in counted.kwargs["attempts"].read_text().splitlines()
                if '"finished"' in line]
    assert finished[-1]["requests"] == result.requests
    assert ce.read_attempts(counted.kwargs["attempts"])  # the journal still validates


def test_an_untracked_tracker_counts_nothing(env):
    result = step(env)  # the plain double has no request_count
    assert result.requests == {"observation": None, "effect": None, "subprocesses": None}
    assert "requests" not in env.kwargs["attempts"].read_text()


def test_the_journal_refuses_a_malformed_request_field(tmp_path):
    line = {"schema_version": ce.EFFECTS_CONTRACT, "event": "finished", "attempt": "a",
            "epic": "E", "ticket": "T", "kind": "start", "status": "completed", "exit_code": 0,
            "cost_usd": None, "duration_ms": None, "summary_file": None, "branch_head": None,
            "error": None, "at": "2026-10-17T10:00:00Z",
            "requests": {"observation": "3", "effect": 0, "subprocesses": None}}
    path = tmp_path / "attempts.jsonl"
    path.write_text(json.dumps(line) + "\n")
    with pytest.raises(ce.AttemptJournalInvalid):
        ce.read_attempts(path)


def test_the_run_summary_splits_observation_wait_and_effects():
    decision = SimpleNamespace()
    rows = [
        ce.StepResult(decision, "executed", requests={"observation": 5, "effect": 2, "subprocesses": 13}),
        ce.StepResult(decision, "wait-ci", requests={"observation": 6, "effect": 0, "subprocesses": None}),
        ce.StepResult(decision, "waiting", requests={"observation": 6, "effect": 0, "subprocesses": None}),
        ce.StepResult(decision, "stopped"),
    ]
    assert ce.request_summary(rows) == {"observation": 17, "wait": 12, "effects": 15}
    assert ce.request_summary([ce.StepResult(decision, "stopped")]) == {
        "observation": None, "wait": None, "effects": None}


# ---- the lookups of the deferral on the Linear transport double


@pytest.fixture
def linear(monkeypatch, tmp_path):
    monkeypatch.setattr("foundry.trackers.epic_intent.registry.data_dir", lambda: str(tmp_path))
    tracker, wire, project, _children = _build(31, 24)
    _close(tracker, monkeypatch, project)
    template = wire.issues["LIN-100"]

    def add(identifier, number, title, labels, state, parent=None, children=()):
        raw = copy.deepcopy(template)
        raw.update(
            id=_native(number), identifier=identifier, title=title, description="- [ ] a",
            state={"id": STATE_IDS[state], "name": state}, labels=connection(labels),
            comments=connection([]), relations=connection([]), inverseRelations=connection([]),
            children=connection([{"id": _native(n), "identifier": i} for n, i in children]))
        raw.pop("parent", None)
        if parent:
            raw["parent"] = {"id": _native(parent[0]), "identifier": parent[1]}
        wire.issues[identifier] = raw

    add("LIN-300", 300, COMPANION, [{"id": "label-epic", "name": "Epic"}], "backlog",
        children=[(301, "LIN-301")])
    add("LIN-301", 301, FOLLOW_UP, [], "backlog", parent=(300, "LIN-300"))
    return SimpleNamespace(tracker=tracker, wire=wire, project=project)


def lookups(linear, known, maybe_created):
    """The reads of one deferral attempt before its first write, by GraphQL operation."""
    linear.wire.calls.clear()
    listing = cd.Listing(linear.tracker, linear.project)
    companion = cd._find_companion(listing, COMPANION, known)
    found = cd._find_follow_up(listing, companion, FOLLOW_UP, maybe_created or listing.loaded)
    operations = collections.Counter(
        _OPERATION.search(document).group(1) for document, _v in linear.wire.calls)
    return companion, found, operations


def test_the_first_lookup_lists_the_project_once(linear):
    companion, found, operations = lookups(linear, [], False)
    assert (companion, found) == ("LIN-300", ("LIN-301", False))
    assert operations["FoundryLinearIssues"] == 1
    assert sum(operations.values()) == 59  # 1 page + 56 issues of the closed Epic + 2 reads


def test_a_journaled_companion_costs_no_listing(linear):
    companion, found, operations = lookups(linear, ["LIN-300"], False)
    assert (companion, found) == ("LIN-300", ("LIN-301", False))
    assert operations == {"FoundryLinearIssue": 3}


def test_a_journaled_companion_without_open_follow_up_scans_only_when_a_creation_is_possible(linear):
    linear.wire.issues["LIN-301"]["state"] = {"id": STATE_IDS["in-progress"], "name": "x"}
    assert lookups(linear, ["LIN-300"], False)[1:] == ((None, False), {"FoundryLinearIssue": 3})
    _c, found, operations = lookups(linear, ["LIN-300"], True)
    assert found == (None, False) and operations["FoundryLinearIssues"] == 1


def test_a_journaled_companion_that_changed_is_not_trusted(linear):
    linear.wire.issues["LIN-300"]["title"] = "Something else"
    linear.wire.calls.clear()
    listing = cd.Listing(linear.tracker, linear.project)
    assert cd._find_companion(listing, COMPANION, ["LIN-300"]) is None  # the creation follows
    assert listing.loaded


def test_the_journal_names_the_companions_and_a_possible_creation(env):
    path = env.kwargs["attempts"]

    def line(event, attempt, **extra):
        base = {"schema_version": ce.EFFECTS_CONTRACT, "event": event, "attempt": attempt,
                "epic": "APP-1", "ticket": "APP-2", "kind": "defer-remarks",
                "at": "2026-10-17T10:00:00Z"}
        if event == "started":
            base.update(fingerprint={}, session_id=None, route=None)
        else:
            base.update(status="failed", exit_code=None, cost_usd=None, duration_ms=None,
                        summary_file=None, branch_head=None, error=None)
        return json.dumps({**base, **extra})

    def field(companion, writes):
        return {"state": "transient", "follow_up": None, "companion": companion, "reason": "x",
                "diff_hash": "d", "remarks": None, "writes": writes, "unknown": []}

    path.write_text("\n".join([
        line("started", "a1"),
        line("finished", "a1", deferral=field("APP-50", [cd.WRITE_COMPANION])),
        line("started", "a2"),  # the current attempt: left out
    ]) + "\n")
    assert cd.journal_memory(path, "APP-1") == (["APP-50"], False)
    assert cd.journal_memory(path, "APP-9") == ([], False)
    path.write_text("\n".join([
        line("started", "a1"),
        line("finished", "a1", deferral=field("APP-50", [cd.WRITE_COMPANION, cd.WRITE_FOLLOW_UP])),
        line("started", "a2"),
    ]) + "\n")
    assert cd.journal_memory(path, "APP-1") == (["APP-50"], True)
    path.write_text(line("started", "a1") + "\n" + line("started", "a2") + "\n")
    assert cd.journal_memory(path, "APP-1") == ([], True)  # no finished line: it may have created
