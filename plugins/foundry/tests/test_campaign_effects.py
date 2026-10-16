"""PAT-217: effects of an Epic campaign; doubles only, no network, no real claude."""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from foundry import campaign_approval as ca
from foundry import campaign_cadence as cc
from foundry import campaign_effects as ce
from foundry.models import PullRequest
from test_campaign_cadence import FakeCodeHost, FakeTracker, check, approve
from test_epic_preview import graph, node

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
PROJECT = SimpleNamespace(key="APP")
BASE = "b" * 40
CLI = str(Path(ce.__file__).resolve().parents[2] / "tooling" / "foundry_cli.py")


class World:
    """Shared state of the tracker, git and the code host, moved only by the fake runner."""

    def __init__(self):
        self.states = {"APP-2": "ready", "APP-3": "ready"}
        self.heads, self.ahead, self.prs = {}, {}, []
        self.proof, self.checks = "none", []
        self.calls = []  # (argv, cwd, env, input)
        self.session_commits = True

    def raw(self):
        return graph([node(i, state=s) for i, s in self.states.items()],
                     dependencies=[("APP-3", "APP-2")])

    @staticmethod
    def branch(ticket):
        return f"chore/{ticket.lower()}-x"

    def branches(self):
        return [self.branch(t) for t in self.heads]

    def runner(self, argv, cwd=None, input=None, env=None, **kwargs):
        self.calls.append((list(argv), cwd, env, input))
        ok = lambda out="": SimpleNamespace(returncode=0, stdout=out, stderr="")  # noqa: E731
        if argv[0] == "git":
            after = argv[argv.index("add") + 1:]
            Path(next(a for a in after if not a.startswith("-"))).mkdir(parents=True)
            return ok()
        if argv[0] == "claude":
            if argv[1] == "--version":
                return ok("2.1.300 (Claude Code)")
            ticket = Path(cwd).name
            if self.session_commits:
                self.heads[ticket] = "c" * 40
                self.ahead[ticket] = 1
            return ok(json.dumps({
                "session_id": argv[argv.index("--session-id") + 1], "total_cost_usd": 0.5,
                "duration_ms": 1200, "is_error": False, "result": f"## Summary\n{ticket}",
            }))
        assert argv[0] == sys.executable and argv[1] == CLI and argv[2] == "issue", argv
        verb, ticket = argv[3], argv[4]
        if verb == "start":
            self.states[ticket] = "in-progress"
            self.heads[ticket], self.ahead[ticket] = "a" * 40, 0
        elif verb == "openpr":
            number = 10 + int(ticket.split("-")[1])
            self.prs.append(PullRequest(
                number=number, url=f"https://x/o/r/pull/{number}", head=self.branch(ticket),
                base="main", base_sha=BASE, sha=self.heads[ticket]))
            self.states[ticket] = "review"
        elif verb == "merge":
            assert self.proof == "valid" and self.checks  # the gates of `issue merge`
            self.prs[-1].merged, self.prs[-1].state = True, "closed"
            self.states[ticket] = "done"
            self.proof, self.checks = "none", []  # a proof and CI belong to one exact head
        return ok()


class WorldProofs:
    def __init__(self, world):
        self.world = world

    def current_for_sync(self, **kw):
        from foundry.routing import RoutingConfigError

        if self.world.proof in ("valid", "blocked"):
            return {}
        raise RoutingConfigError("absent")

    def valid_for_merge(self, **kw):
        from foundry.routing import RoutingConfigError

        if self.world.proof != "valid":
            raise RoutingConfigError("no")
        return {}


class WorldTracker(FakeTracker):
    def __init__(self, world):
        super().__init__(None)
        self.world = world

    def get_epic_subgraph(self, project, epic, *, depth, nodes):
        self.raw = self.world.raw()
        return super().get_epic_subgraph(project, epic, depth=depth, nodes=nodes)


class WorldCodeHost(FakeCodeHost):
    def __init__(self, world):
        super().__init__()
        self.world = world

    def list_prs(self, repo, head):
        return [p for p in self.world.prs if p.head == head]

    def get_pr(self, repo, number):
        return next(p for p in self.world.prs if p.number == number)

    def check_runs(self, repo, sha):
        return list(self.world.checks)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "data"))
    world = World()
    journal = tmp_path / "approvals.jsonl"
    approve(graph([node("APP-2"), node("APP-3")], dependencies=[("APP-3", "APP-2")]), journal)
    root = tmp_path / "repo"
    root.mkdir()
    runtime = ce.Runtime(root=str(root), worktrees_dir=tmp_path / "wt", runner=world.runner,
                         plugin_root=Path(ce.__file__).resolve().parents[2])
    kwargs = dict(
        attempts=tmp_path / "attempts.jsonl", journal=journal,
        refs=lambda r: world.branches(), diff_reader=lambda r, b, h: b"diff",
        proof_store=WorldProofs(world),
        branch_facts=lambda r, b: (
            world.heads.get(b.split("/")[1].split("-x")[0].upper()),
            world.ahead.get(b.split("/")[1].split("-x")[0].upper())),
    )
    return SimpleNamespace(world=world, runtime=runtime, kwargs=kwargs, tmp=tmp_path,
                           tracker=WorldTracker(world), codehost=WorldCodeHost(world))


def step(env, now=NOW):
    return ce.step(env.tracker, env.codehost, PROJECT, "APP-1", env.runtime, now, **env.kwargs)


def effects(world):
    return [c for c in world.calls if c[0][0] != "git" and "--version" not in c[0]]


def test_start_is_one_issue_start_subprocess_in_the_ticket_worktree(env):
    result = step(env)
    assert (result.decision.kind, result.outcome) == ("start", "executed")
    (argv, cwd, _env, _in), = effects(env.world)
    assert argv == [sys.executable, CLI, "issue", "start", "APP-2"]
    assert cwd == str(env.tmp / "wt" / "APP-2")
    git = [c[0] for c in env.world.calls if c[0][0] == "git"]
    assert git[0][:5] == ["git", "worktree", "add", "--detach", str(env.tmp / "wt" / "APP-2")]


def test_implement_session_argv_env_and_authority(env):
    step(env)  # start
    result = step(env)
    assert result.decision.kind == "implement" and result.outcome == "executed"
    (argv, cwd, child_env, prompt), = effects(env.world)[1:]
    assert argv[0] == "claude" and "--print" in argv
    assert argv[argv.index("--model") + 1]
    assert argv[argv.index("--max-budget-usd") + 1] == "5.00"
    assert re.fullmatch(r"[0-9a-f-]{36}", argv[argv.index("--session-id") + 1])
    assert argv[argv.index("--disallowedTools") + 1:] == list(ce.DISALLOWED_TOOLS)
    assert "Bash(git push:*)" in argv and "Bash(gh:*)" in argv
    assert any("issue" in rule for rule in ce.DISALLOWED_TOOLS)
    assert set(child_env) <= {"HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER",
                              "FOUNDRY_RUNTIME_CONFIG_ISOLATED"}
    assert cwd == str(env.tmp / "wt" / "APP-2")
    assert "never push" in prompt and "APP-2" in prompt
    # The issue text is handed over: the isolated session reads no tracker.
    assert '<issue id="APP-2">' in prompt and "- [ ] done" in prompt
    assert "query issue" not in prompt
    effort = argv[argv.index("--effort") + 1] if "--effort" in argv else None
    from foundry.routing import RoutingPolicy

    assert effort == RoutingPolicy.load(env.runtime.root).resolve("implementer", "claude").effort


def test_open_pr_follows_a_finished_implementation_with_its_summary(env):
    step(env), step(env)
    result = step(env)
    assert (result.decision.kind, result.decision.reason) == ("open-pr", "implemented_without_pr")
    assert result.outcome == "executed"
    argv, cwd, _e, summary = effects(env.world)[2]
    assert argv == [sys.executable, CLI, "issue", "openpr", "APP-2", "--summary-stdin"]
    assert summary == "## Summary\nAPP-2"
    assert step(env).decision.kind == "review"


def test_no_commits_means_implement_again_which_is_refused_as_a_replay(env):
    env.world.session_commits = False
    step(env), step(env)
    again = step(env)
    assert (again.decision.kind, again.decision.reason, again.outcome) == (
        "suspend", "attempt_already_made", "stopped")
    assert len(effects(env.world)) == 2  # start and the one session, never a second session


def test_same_identity_is_never_launched_twice_even_after_a_crash(env):
    step(env)
    env.world.states["APP-2"] = "ready"  # the tracker write was lost: same fingerprint
    env.world.heads.pop("APP-2")
    before = len(env.world.calls)
    # a crash between `started` and `finished`: only the started line exists
    path = env.kwargs["attempts"]
    lines = path.read_text().splitlines()
    path.write_text(lines[0] + "\n")
    result = step(env)
    assert (result.decision.kind, result.decision.reason) == ("suspend", "attempt_already_made")
    assert len(env.world.calls) == before


def test_claim_is_atomic_per_identity(tmp_path):
    path = tmp_path / "a.jsonl"
    record = {"schema_version": ce.EFFECTS_CONTRACT, "event": "started", "attempt": "x" * 64,
              "epic": "APP-1", "ticket": "APP-2", "kind": "start", "fingerprint": {},
              "session_id": None, "route": None, "at": "2026-10-16T12:00:00Z"}
    assert ce.claim(path, record, None) is None
    assert ce.claim(path, record, None) == "attempt_already_made"
    assert len(path.read_text().splitlines()) == 1


def test_invalid_attempt_journal_stops_before_any_effect(env):
    env.kwargs["attempts"].write_text("not json\n")
    result = step(env)
    assert (result.decision.reason, result.outcome) == ("attempts_journal_invalid", "stopped")
    assert env.world.calls == []


def test_session_ceilings_refuse_before_launch(env):
    # the approval allows 5 sessions, 2 per issue
    path = env.kwargs["attempts"]
    for index, ticket in enumerate(["APP-2", "APP-2"]):
        ce.claim(path, {"schema_version": ce.EFFECTS_CONTRACT, "event": "started",
                        "attempt": f"{index}" * 64, "epic": "APP-1", "ticket": ticket,
                        "kind": "implement", "fingerprint": {}, "session_id": "s", "route": None,
                        "at": "2026-10-16T12:00:00Z"}, None)
    step(env)  # start
    result = step(env)
    assert (result.decision.kind, result.decision.reason) == (
        "refuse", "session_ceiling_per_issue_reached")
    assert [c[0][0] for c in effects(env.world)] == [sys.executable]  # no claude launch


def test_review_and_correct_are_not_executed(env):
    step(env), step(env), step(env)
    calls = len(env.world.calls)
    for proof in ("none", "blocked"):
        env.world.proof = proof
        result = step(env)
        assert result.outcome == "awaiting_review_execution" and result.effect is None
    assert len(env.world.calls) == calls


def test_merge_only_on_a_merge_decision(env):
    step(env), step(env), step(env)
    env.world.proof = "valid"  # valid review, CI not proven
    result = step(env)
    assert (result.decision.kind, result.outcome) == ("wait-ci", "wait-ci")
    assert not [c for c in env.world.calls if c[0][3:4] == ["merge"]]
    env.world.checks = [check()]
    result = step(env)
    assert result.decision.kind == "merge" and result.outcome == "executed"
    assert effects(env.world)[-1][0] == [sys.executable, CLI, "issue", "merge", "APP-2", "12"]
    for argv, *_ in env.world.calls:
        assert not any(flag.startswith("--allow") for flag in argv)


def test_unapproved_campaign_launches_nothing(env):
    env.kwargs["journal"] = env.tmp / "none.jsonl"
    result = step(env)
    assert (result.decision.kind, result.outcome) == ("refuse", "stopped")
    assert env.world.calls == []


def test_epic_of_two_tickets_reaches_the_verdict_awaited(env):
    sleeps = []
    kw = dict(max_steps=20, poll_seconds=3, sleep=sleeps.append, clock=lambda: NOW, **env.kwargs)

    def go():
        return ce.run(env.tracker, env.codehost, PROJECT, "APP-1", env.runtime, **kw)

    results, stopped = go()
    assert stopped == "awaiting_review_execution"
    assert [r.decision.kind for r in results] == ["start", "implement", "open-pr", "review"]
    env.world.proof, env.world.checks = "valid", [check()]  # injected review proof, green CI
    results, stopped = go()
    assert stopped == "awaiting_review_execution"
    assert [r.decision.kind for r in results] == [
        "merge", "start", "implement", "open-pr", "review"]
    assert env.world.states == {"APP-2": "done", "APP-3": "review"}
    env.world.proof, env.world.checks = "valid", [check()]
    results, stopped = go()
    assert stopped == "terminal" and results[-1].decision.kind == "await-verdict"
    assert env.world.states == {"APP-2": "done", "APP-3": "done"}
    verbs = [c[0][3] for c in effects(env.world) if c[0][0] == sys.executable]
    assert verbs == ["start", "openpr", "merge", "start", "openpr", "merge"]
    assert sum(c[0][0] == "claude" for c in effects(env.world)) == 2
    records = ce.read_attempts(env.kwargs["attempts"])
    assert len({r["attempt"] for r in records if r["event"] == "started"}) == 8
    assert all(r["status"] == "completed" for r in records if r["event"] == "finished")


def test_run_waits_on_ci_without_an_effect(env):
    step(env), step(env), step(env)
    env.world.proof = "valid"
    sleeps = []
    results, stopped = ce.run(
        env.tracker, env.codehost, PROJECT, "APP-1", env.runtime, max_steps=3,
        poll_seconds=7, sleep=sleeps.append, clock=lambda: NOW, **env.kwargs)
    assert (stopped, sleeps) == ("max_steps", [7, 7, 7])
    assert not [c for c in env.world.calls if c[0][3:4] == ["merge"]]


def test_failed_foundry_command_is_recorded_and_stops(env):
    base = env.world.runner

    def failing(argv, **kw):
        if argv[0] == sys.executable:
            env.world.calls.append((argv, kw.get("cwd"), None, None))
            return SimpleNamespace(returncode=1, stdout="", stderr="refused")
        return base(argv, **kw)

    env.runtime.runner = failing
    result = step(env)
    assert (result.outcome, result.effect["status"]) == ("failed", "failed")
    finished = [r for r in ce.read_attempts(env.kwargs["attempts"]) if r["event"] == "finished"]
    assert finished[0]["exit_code"] == 1
    assert step(env).decision.reason == "attempt_already_made"


def test_effect_primitives_are_not_referenced_by_the_module():
    source = open(ce.__file__, encoding="utf-8").read()
    for name in ("open_pr", "merge_pr", "update_pr", "delete_branch", "create_issue",
                 "update_fields", "add_comment", ".transition(", "record_delivery_receipt",
                 "--allow-no-ci", "--allow-incomplete-ac", "Popen"):
        assert name not in source


def test_cli_step_prints_one_result(monkeypatch, capsys, env):
    import subprocess

    import foundry

    subprocess.run(["git", "init", "-q", str(env.tmp / "repo")], check=True)
    env.tracker.resolve_checkout_project = lambda root: PROJECT
    monkeypatch.setattr(foundry, "tracker", lambda *a, **k: env.tracker)
    monkeypatch.setattr(foundry, "codehost", lambda *a, **k: env.codehost)
    assert cc.main(["step", "APP-1", "--root", str(env.tmp / "repo")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["contract"] == ce.EFFECTS_CONTRACT
    assert out["steps"][0]["decision"]["kind"] == "refuse"  # no approval in the fresh data dir
    with pytest.raises(SystemExit):
        cc.main(["run", "APP-1", "--budget-usd", "1000"])


def test_attempt_journal_lives_in_the_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "d"))
    assert ce.journal_path() == tmp_path / "d" / "campaign-attempts.jsonl"
    assert ca.journal_path().parent == ce.journal_path().parent
    assert ce.default_worktrees_dir(str(tmp_path / "repo")) == tmp_path / "repo-campaign"
    assert timedelta(0) < timedelta(seconds=ce.IMPLEMENT_TIMEOUT_SECONDS)


def test_real_git_branch_facts_and_worktree(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    git = lambda *a, cwd=repo: subprocess.run(  # noqa: E731
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a],
        cwd=cwd, check=True, capture_output=True, text=True)
    repo.mkdir()
    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "base")
    git("branch", "chore/app-2-x")
    assert cc._git_branch_facts(str(repo), "chore/app-2-x")[1] == 0
    runtime = ce.Runtime(root=str(repo), worktrees_dir=tmp_path / "wt")
    path = ce.ensure_worktree(runtime, "APP-2", cc._git_refs)
    assert path == tmp_path / "wt" / "APP-2" and git("branch", "--show-current", cwd=path).stdout.strip() == "chore/app-2-x"
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=path)
    head, ahead = cc._git_branch_facts(str(repo), "chore/app-2-x")
    assert ahead == 1 and head == git("rev-parse", "chore/app-2-x").stdout.strip()
    assert cc._git_branch_facts(str(repo), "chore/app-9-x") == (None, None)
    fresh = ce.ensure_worktree(runtime, "APP-3", cc._git_refs)  # no branch yet: detached
    assert fresh.is_dir() and git("branch", "--show-current", cwd=fresh).stdout.strip() == ""
