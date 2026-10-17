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

from foundry import campaign_cadence as cc
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


def test_a_start_pass_reports_the_requests_of_its_observation(counted):
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


@pytest.fixture
def before(monkeypatch):
    """The deferral reads as they were before PAT-228: no journal memory, and a listing that
    every lookup reads again."""
    def issues(self):
        return cd._read(self.tracker.search, self.project)

    monkeypatch.setattr(cd.Listing, "issues", issues)
    monkeypatch.setattr(cd, "journal_memory", lambda attempts, epic: ([], True))


def test_the_lookups_before_pat_228_listed_the_project_twice(linear, before):
    assert lookups(linear, [], True)[2].total() == 59  # an unstarted child: one listing
    linear.wire.issues["LIN-301"]["state"] = {"id": STATE_IDS["in-progress"], "name": "x"}
    assert lookups(linear, [], True)[2].total() == 116  # none: the orphan scan lists again


def test_an_observation_costs_one_request_per_node(monkeypatch, tmp_path):
    monkeypatch.setattr("foundry.trackers.epic_intent.registry.data_dir", lambda: str(tmp_path))
    for children, prerequisites, expected in ((6, 2, 9), (12, 4, 17), (31, 24, 56)):
        open_ids = [f"LIN-{100 + i}" for i in range(children)]
        tracker, wire, project, _ = _build(children, prerequisites, open_children=open_ids)
        wire.calls.clear()
        observed = cc.observe(tracker, None, project, "LIN-1", "/none", None,
                              refs=lambda r: [], branch_facts=lambda r, b: (None, None))
        assert observed.read_error is None and len(wire.calls) == expected


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


def test_a_journaled_companion_of_another_origin_is_ignored(linear):
    listing = cd.Listing(linear.tracker, linear.project)
    assert cd._find_companion(listing, "Nits LIN-9", ["LIN-300"]) is None
    assert listing.loaded  # the title differs: the listing decides


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

    def field(companion, writes, state="done"):
        return {"state": state, "follow_up": None, "companion": companion, "reason": "x",
                "diff_hash": "d", "remarks": None, "writes": writes, "unknown": []}

    path.write_text("\n".join([
        line("started", "a1"),
        line("finished", "a1", deferral=field("APP-50", [cd.WRITE_COMPANION])),
        line("started", "a2"),  # the current attempt: left out
    ]) + "\n")
    assert cd.journal_memory(path, "APP-1") == (["APP-50"], False)
    assert cd.journal_memory(path, "APP-9") == ([], False)
    path.write_text("\n".join([  # PAT-238: not `done`, so a creation may have been lost
        line("started", "a1"),
        line("finished", "a1", deferral=field("APP-50", [cd.WRITE_COMPANION], "transient")),
        line("started", "a2"),
    ]) + "\n")
    assert cd.journal_memory(path, "APP-1") == (["APP-50"], True)
    path.write_text("\n".join([
        line("started", "a1"),
        line("finished", "a1", deferral=field("APP-50", [cd.WRITE_COMPANION, cd.WRITE_FOLLOW_UP])),
        line("started", "a2"),
    ]) + "\n")
    assert cd.journal_memory(path, "APP-1") == (["APP-50"], True)
    path.write_text(line("started", "a1") + "\n" + line("started", "a2") + "\n")
    assert cd.journal_memory(path, "APP-1") == ([], True)  # no finished line: it may have created


# ---- a replayed campaign: the campaign test world, its reads counted on the Linear double


class ShadowTracker(CountingTracker):
    """The campaign world answers every read; the same read is also made on a ``LinearTracker``
    over the transport double, whose project mirrors the world's issues (``APP-n`` is
    ``LIN-(500+n)`` there) and holds one closed Epic.  The counter is the number of transport
    calls of those shadow reads.  A shadow read that fails is kept in ``shadow_failures``
    (its requests were made and are counted); ``replay`` asserts the list is empty."""

    def __init__(self, world, linear):
        super().__init__(world)
        self.linear = linear
        self.shadow_failures = []

    @staticmethod
    def shadow_id(issue_id):
        return f"LIN-{500 + int(issue_id.split('-')[1])}"

    def request_count(self):
        return len(self.linear.wire.calls)

    def _mirror(self):
        wire, template, sid = self.linear.wire, self.linear.template, self.shadow_id
        ref = lambda i: {"id": _native(int(sid(i).split("-")[1])), "identifier": sid(i)}  # noqa: E731
        for issue in self.world.issues.values():
            parents = [link.target for link in issue.links if link.type == "subtask-of"]
            raw = copy.deepcopy(template)
            raw.update(
                id=ref(issue.id)["id"], identifier=sid(issue.id), title=issue.title,
                description=issue.body or "",
                state={"id": STATE_IDS[issue.state], "name": issue.state},
                labels=connection([{"id": "label-epic", "name": "Epic"}] if issue.type == "Epic" else []),
                comments=connection([]), relations=connection([]),
                inverseRelations=connection(
                    [{"type": "blocks", "issue": ref("APP-2")}] if issue.id == "APP-3" else []),
                children=connection([ref(link.target) for link in issue.links
                                     if link.type == "parent-of"]))
            raw.pop("parent", None)
            if parents:
                raw["parent"] = ref(parents[0])
            wire.issues[sid(issue.id)] = raw

    def _shadow(self, call, *args, **kwargs):
        self._mirror()
        try:
            call(*args, **kwargs)
        except Exception as error:  # noqa: BLE001 - the requests are counted whatever the answer
            self.shadow_failures.append(f"{getattr(call, '__name__', call)}: {error!r}")

    def get_epic_subgraph(self, project, epic, *, depth, nodes):
        self._shadow(self.linear.tracker.get_epic_subgraph, self.linear.project,
                     self.shadow_id(epic), depth=depth, nodes=nodes)
        return WorldTracker.get_epic_subgraph(self, project, epic, depth=depth, nodes=nodes)

    def get_issue(self, issue_id):
        self._shadow(self.linear.tracker.get_issue, self.shadow_id(issue_id))
        return WorldTracker.get_issue(self, issue_id)

    def search(self, project, query=""):
        self._shadow(self.linear.tracker.search, self.linear.project)
        return WorldTracker.search(self, project, query)


def replay(env, linear):
    """Start, implement, open the PR, review (two non-blocking remarks), defer, three passes
    that wait for CI, merge: ``run`` on the campaign world, its tracker reads counted on the
    Linear double.  The fake runner prints no request line, so ``subprocesses`` stays ``None``."""
    from test_campaign_cadence import check
    from test_campaign_review import roomy

    linear.template = copy.deepcopy(linear.wire.issues["LIN-100"])
    env.tracker = ShadowTracker(env.world, linear)
    roomy(env)
    env.world.reviews.append(test_campaign_effects.result_text(nits=2))
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            env.world.checks = [check()]

    results, stopped = ce.run(
        env.tracker, env.codehost, test_campaign_effects.PROJECT, "APP-1", env.runtime,
        max_steps=9, poll_seconds=1, sleep=sleep, clock=lambda: test_campaign_effects.NOW,
        **env.kwargs)
    assert stopped == "max_steps"
    assert env.tracker.shadow_failures == []
    return results


KINDS = ["start", "implement", "open-pr", "review", "defer-remarks", "wait-ci", "wait-ci",
         "wait-ci", "merge"]


def test_a_replayed_campaign_splits_its_requests(env, linear):
    results = replay(env, linear)
    assert [r.decision.kind for r in results] == KINDS
    assert [r.requests["observation"] for r in results] == [3, 3, 3, 4, 6, 4, 4, 4, 4]
    assert [r.requests["effect"] for r in results] == [0, 1, 0, 0, 59, 0, 0, 0, 0]
    assert ce.request_summary(results) == {"observation": 35, "wait": 12, "effects": 60}


def test_the_same_replay_before_pat_228_costs_one_more_listing(env, linear, before):
    results = replay(env, linear)
    assert [r.requests["effect"] for r in results][4] == 116
    assert ce.request_summary(results) == {"observation": 35, "wait": 12, "effects": 117}
