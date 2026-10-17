"""PAT-217: effects of an Epic campaign; doubles only, no network, no real claude."""
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from foundry import campaign_approval as ca
from foundry import campaign_cadence as cc
from foundry import campaign_effects as ce
from foundry import review_remarks
from foundry.models import Link, PullRequest
from test_campaign_cadence import FakeCodeHost, FakeTracker, check, approve
from test_epic_preview import graph, node

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
PROJECT = SimpleNamespace(key="APP")
BASE = "b" * 40
CLAIM_ID = "e" * 64  # a secret capability: it must never reach the journal or the output
DIFF_HASH = "d" * 64
CRITERION = {"id": "ac-1", "digest": "a" * 64}
REPOSITORY = "test/repo"
CLI = str(Path(ce.__file__).resolve().parents[2] / "tooling" / "foundry_cli.py")


def neutral(env, hooks) -> bool:
    """``env`` sets, last for git, ``core.hooksPath=<hooks>`` and ``core.fsmonitor=false``."""
    entries = dict((env.get(f"GIT_CONFIG_KEY_{n}"), env.get(f"GIT_CONFIG_VALUE_{n}"))
                   for n in range(int(env.get("GIT_CONFIG_COUNT", "0")))) if env else {}
    return entries.get("core.hooksPath") == hooks and entries.get("core.fsmonitor") == "false"


class World:
    """Shared state of the tracker, git and the code host, moved only by the fake runner."""

    def __init__(self):
        self.states = {"APP-2": "ready", "APP-3": "ready"}
        self.heads, self.ahead, self.prs = {}, {}, []
        self.proof, self.checks = "none", []
        self.calls = []  # (argv, cwd, env, input)
        self.session_commits = True
        self.session_summary = None
        self.head_sha = {}  # worktree HEAD commit overrides (PAT-227)
        self.reviews = []  # canned reviewer results, consumed in order
        self.human_required = False
        self.proofs = []  # outcomes files seen by record-review-proof
        self.carry_ok = False
        self.commits = 0
        # the tracker the `edit` verbs and the reads of the deferral share (PAT-141)
        self.issues = {}
        self.add_issue("APP-1", "Origin epic", "Epic", "in-progress")
        for ticket in self.states:
            self.add_issue(ticket, f"Ticket {ticket}", "Task", "ready", parent="APP-1")
        self.edits = []  # (verb, args, input) of every `edit` write, in order
        self.create_failure = None  # stderr of the next create-issue, or a callable
        self.create_effect_first = False  # the failing create still creates the issue
        self.search_failure = None
        self.search_blind = set()  # titles the search does not list yet (read lag)
        self.hide_clients = False  # the read of a client id finds nothing (yet)
        self.reconcile_failure = None  # stderr of the next reconcile-create
        self.clients = {}  # client id of a failed creation -> the issue it created
        self.write_failures = {}  # verb -> stderr of the next failure of that verb
        self.remarks_receipts = 0
        self.worktree_head = {}  # ticket -> the branch its worktree HEAD names, when moved

    def add_issue(self, issue_id, title, kind, state, parent=None, body="- [ ] done"):
        links = [Link("subtask-of", "inward", parent)] if parent else []
        self.issues[issue_id] = SimpleNamespace(
            id=issue_id, title=title, type=kind, state=state, links=links, comments=[],
            body=body)
        if parent:
            self.issues[parent].links.append(Link("parent-of", "outward", issue_id))
        return self.issues[issue_id]

    def raw(self):
        return graph([node(i, state=s) for i, s in self.states.items()],
                     dependencies=[("APP-3", "APP-2")])

    @staticmethod
    def branch(ticket):
        return f"chore/{ticket.lower()}-x"

    def branches(self):
        return [self.branch(t) for t in self.heads]

    def runner(self, argv, cwd=None, input=None, env=None, **kwargs):
        ok = lambda out="": SimpleNamespace(returncode=0, stdout=out, stderr="")  # noqa: E731
        if "--git-path" in argv:  # PAT-220: the hooks directory of the main checkout, read once
            self.hooks_reads = getattr(self, "hooks_reads", 0) + 1
            self.hooks = f"{Path(cwd).resolve()}/.git/hooks"
            return ok(self.hooks + "\n")
        self.calls.append((list(argv), cwd, env, input))
        if argv[0] != "claude":  # PAT-220: only the main checkout's hooks, no fsmonitor
            assert neutral(env, self.hooks), argv
        if "--abbrev-ref" in argv:  # PAT-220: the branch the worktree HEAD names
            return ok(self.worktree_head.get(Path(cwd).name, self.branch(Path(cwd).name)) + "\n")
        if argv == ["git", "rev-parse", "HEAD"]:  # PAT-227: the commit the worktree HEAD names
            ticket = Path(cwd).name
            return ok(self.head_sha.get(ticket, self.heads.get(ticket, "")) + "\n")
        if argv[:2] == ["git", "rev-parse"]:  # PAT-220: a linked worktree of the stand-in repository
            common = Path(os.environ["FOUNDRY_DATA"]).parent / "repo" / ".git"
            return ok(f"{common}\n{common}/worktrees/{Path(cwd).name}\n")
        if argv[0] == "git":
            after = argv[argv.index("add") + 1:]
            Path(next(a for a in after if not a.startswith("-"))).mkdir(parents=True)
            return ok()
        if argv[0] == "claude":
            if argv[1] == "--version":
                return ok("2.1.300 (Claude Code)")
            ticket = Path(cwd).name
            sid = argv[argv.index("--session-id") + 1]
            if "--tools" in argv:  # the reviewer
                text = self.reviews.pop(0)
                return ok(json.dumps({"session_id": sid, "total_cost_usd": 0.25,
                                      "duration_ms": 900, "is_error": False, "result": text}))
            if self.session_commits:
                self.commits += 1
                self.heads[ticket] = f"{self.commits:x}" * 40 if self.commits < 16 else "f" * 40
                self.ahead[ticket] = 1
            return ok(json.dumps({
                "session_id": sid, "total_cost_usd": 0.5,
                "duration_ms": 1200, "is_error": False, "result": self.session_summary or f"{ce.SUMMARY_MARK}\n## Summary\n{ticket}",
            }))
        assert argv[0] == sys.executable and argv[1] == CLI, argv
        if argv[2] == "routing":
            return self.routing(argv, cwd, ok)
        if argv[2] == "edit":
            return self.edit(argv, input)
        assert argv[2] == "issue", argv
        verb, ticket = argv[3], argv[4]
        if verb == "start":
            self.states[ticket] = "in-progress"
            self.heads[ticket], self.ahead[ticket] = "a" * 40, 0
        elif verb == "openpr" and "## Review rounds" in (input or ""):
            self.bodies = getattr(self, "bodies", []) + [input]  # a body update: nothing moves
        elif verb == "openpr":
            number = 10 + int(ticket.split("-")[1])
            if self.prs and self.prs[-1].state == "open":  # a push to the open PR
                self.prs[-1].sha = self.heads[ticket]
                self.proof = "none"
            else:
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


def _edit(self, argv, input):
    verb, args = argv[3], argv[4:]
    self.edits.append((verb, args, input))
    ok = lambda out="": SimpleNamespace(returncode=0, stdout=out, stderr="")  # noqa: E731
    fail = lambda err: SimpleNamespace(returncode=1, stdout="", stderr=err)  # noqa: E731
    if verb == "create-issue":
        spec = json.loads(args[0])
        failure = self.create_failure
        if failure is not None:
            self.create_failure = None
            if self.create_effect_first:
                created = self.make_issue(spec)
                failure = failure.replace("<ID>", created.id)
                for client in [] if self.hide_clients else re.findall(r"client_id\W+([0-9a-f-]{36})", failure):
                    self.clients[client] = created.id  # a client id resolves to its issue
            return fail(failure)
        return ok(f"🆕 {self.make_issue(spec).id} — {spec['title']}")
    if verb in self.write_failures:
        return fail(self.write_failures.pop(verb))
    if verb == "comment":
        self.issues[args[0]].comments.append({"text": input, "created": 1})
        return ok("note")
    if verb == "link":
        src, kind, dst = args
        assert kind == "subtask-of"
        self.issues[src].links.append(Link("subtask-of", "inward", dst))
        self.issues[dst].links.append(Link("parent-of", "outward", src))
        return ok()
    if verb == "reconcile-create":
        if self.reconcile_failure is not None:
            failure, self.reconcile_failure = self.reconcile_failure, None
            return fail(failure)
        if re.fullmatch(r"[0-9a-f-]{36}", args[0]) and args[0] not in self.clients:
            return fail("not found")  # a client id no issue was created with
        return ok(json.dumps({"validation": "accepted",
                              "issue_id": self.clients.get(args[0], args[0])}))
    raise AssertionError(argv)


def _make_issue(self, spec):
    number = 100 + len(self.issues)
    fields = spec.get("fields") or {}
    return self.add_issue(f"APP-{number}", spec["title"], fields.get("Type", "Task"),
                          fields.get("State", "backlog"), parent=spec.get("parent"),
                          body=spec.get("body", ""))


World.edit, World.make_issue = _edit, _make_issue


def _opt(argv, name):
    return argv[argv.index(name) + 1]


def _routing(self, argv, cwd, ok):
    verb = argv[3]
    if verb == "claim-review":
        assert len(_opt(argv, "--claim-attempt-token")) == 64
        return ok(json.dumps({"diff_hash": DIFF_HASH, "should_run": True, "state": "claimed",
                              "generation": 1, "claim_id": CLAIM_ID}))
    if verb == "prepare-review":
        claim = json.loads(Path(_opt(argv, "--claim-file")).read_text())
        assert claim["claim_id"] == CLAIM_ID
        assert oct(Path(_opt(argv, "--claim-file")).stat().st_mode & 0o777) == "0o600"
        out = Path(_opt(argv, "--output-dir"))
        out.mkdir(mode=0o700)
        (out / "reviewer-send.txt").write_text(
            f"Goal:\nRun the independent Foundry two-stage review.\n"
            f"coordinates: diff_hash {DIFF_HASH} claim_id {CLAIM_ID}\n")
        if "--declarations-file" in argv:
            self.declarations = json.loads(Path(_opt(argv, "--declarations-file")).read_text())
        return ok(json.dumps({"schema": "foundry.review-packet.v1", "status": "prepared",
                              "files": 7}))
    if verb == "record-review-proof":
        assert _opt(argv, "--claim-id") == CLAIM_ID
        submitted = json.loads(Path(_opt(argv, "--outcomes-file")).read_text())
        assert set(submitted) == {"outcomes", "quality"} and isinstance(submitted["outcomes"], list)
        self.proofs.append(submitted)
        self.proof = "valid" if submitted["quality"] == "mergeable" else "blocked"
        self.proof_id = hashlib.sha256(f"proof-{len(self.proofs)}".encode()).hexdigest()
        return ok(json.dumps({"proof_id": self.proof_id}))
    if verb == "record-review-remarks":
        assert _opt(argv, "--proof-id") == self.proof_id and self.proof == "valid"
        proof = {"proof_id": self.proof_id, "quality": "mergeable", "issue": {"id": _opt(argv, "--issue")},
                 "coordinates": {"diff_hash": _opt(argv, "--diff-hash"), "base": _opt(argv, "--base")},
                 "review": {"generation": 1}}
        remarks = json.loads(Path(_opt(argv, "--remarks-file")).read_text())
        if getattr(self, "refuse_remarks", False):
            return SimpleNamespace(returncode=1, stdout="", stderr="refus")
        review_remarks.ReviewRemarksStore(REPOSITORY).record(
            proof, remarks, issue=_opt(argv, "--issue"), diff_hash=_opt(argv, "--diff-hash"),
            base=_opt(argv, "--base"))
        self.remarks_receipts += 1
        return ok(json.dumps({"proof_id": self.proof_id}))
    if verb == "escalation":
        self.escalations = getattr(self, "escalations", []) + [argv]
        out = {"human_required": self.human_required, "action": "escalated",
               "final_tier": "frontier"}
        return SimpleNamespace(returncode=2 if self.human_required else 0,
                               stdout=json.dumps(out), stderr="")
    if verb == "carry-review-proof":
        self.carried = getattr(self, "carried", []) + [argv]
        if not self.carry_ok:
            return SimpleNamespace(returncode=1, stdout="", stderr="refus")
        self.proof = "valid"
        return ok(json.dumps({"proof_id": "q" * 8, "carried": True}))
    raise AssertionError(argv)


World.routing = _routing


def remarks_of(fix=0, nits=0, description=0):
    """``description`` of the ``fix`` remarks target the PR description (PAT-227)."""
    return ([{"text": f"fix before merge: remark {n}", "fix_before_merge": True,
              "target": "description" if n < description else "diff"} for n in range(fix)]
            + [{"text": f"nit: remark {n}", "fix_before_merge": False, "target": "diff"}
               for n in range(nits)])


def result_text(quality="mergeable", fix=0, nits=0, verdict="pass", report="## AC table\nok",
                description=0):
    body = {"outcomes": [{**CRITERION, "verdict": verdict}], "quality": quality,
            "fix_before_merge": fix, "non_blocking": nits, "remarks": remarks_of(fix, nits, description)}
    return f"{report}\nAC: PASS · QUALITY: OK to merge\n{ce.campaign_review.RESULT_SENTINEL}\n" \
        + json.dumps(body)


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

    def get_issue(self, issue_id):
        self.calls.append("get_issue")
        return self.world.issues[issue_id]

    def search(self, project, query=""):
        self.calls.append("search")
        if self.world.search_failure:
            raise self.world.search_failure
        return [i for i in self.world.issues.values() if i.title not in self.world.search_blind]


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
    monkeypatch.setattr("foundry.routing.repository_identity", lambda root=None: REPOSITORY)
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
    assert argv[argv.index("--disallowedTools") + 1:argv.index("--permission-mode")] == list(
        ce.DISALLOWED_TOOLS)
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


def _session(env):
    step(env)  # start
    step(env)
    (argv, cwd, _e, _p), = effects(env.world)[1:]
    return argv, json.loads(argv[argv.index("--settings") + 1]), cwd


def _under(path, entries):
    return any(path == e or path.startswith(e.rstrip("/") + "/") for e in entries)


def test_implement_session_runs_in_the_native_sandbox(env):
    argv, settings, cwd = _session(env)
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk" and "auto" not in argv
    assert argv[argv.index("--setting-sources") + 1] == "user"
    at = argv.index("--strict-mcp-config")
    assert argv[at + 1:at + 3] == ["--mcp-config", '{"mcpServers":{}}']
    assert at < argv.index("--disallowedTools")
    assert argv.count("--settings") == 1 and argv.index("--settings") > argv.index("--disallowedTools")
    sandbox = settings["sandbox"]
    assert sandbox["enabled"] is True and sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False
    worktree = os.path.realpath(cwd)
    writes = sandbox["filesystem"]["allowWrite"]
    assert worktree in writes
    assert settings["permissions"]["allow"] == [f"Read(//{worktree.lstrip('/')}/**)",
                                                f"Edit(//{worktree.lstrip('/')}/**)"]


def test_session_cannot_write_foundry_state_or_user_configuration(env, monkeypatch):
    home = env.tmp / "home"
    (home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("FOUNDRY_CONFIG", raising=False)
    argv, settings, _cwd = _session(env)
    data = os.path.realpath(env.tmp / "data")
    real_home = os.path.realpath(home)
    from foundry import review_remarks
    from foundry.routing import AcceptanceProofStore

    protected = [
        str(AcceptanceProofStore("r/x", state_dir=data).directory),
        os.path.join(data, ca.JOURNAL_NAME), os.path.join(data, ce.ATTEMPTS_NAME),
        str(review_remarks.ReviewRemarksStore("r/x", state_dir=data).directory),
        os.path.join(real_home, ".config", "foundry", "config.env"),
        os.path.join(real_home, ".claude", "settings.json"), os.path.join(real_home, ".codex", "x"),
    ]
    writes = settings["sandbox"]["filesystem"]["allowWrite"]
    edit_deny = [r[len("Edit(//"):-1] for r in settings["permissions"]["deny"]
                 if r.startswith("Edit(//")]
    for path in protected:
        assert not _under(path, writes), path
        assert _under(path, settings["sandbox"]["filesystem"]["denyWrite"]), path
        assert any(_under(path, ["/" + e.removesuffix("/**")]) for e in edit_deny), path
        assert _under(path, settings["sandbox"]["filesystem"]["denyRead"]) or _under(
            path, [real_home]), path
    assert _under(os.path.join(data, ca.JOURNAL_NAME), ["/" + e.removesuffix("/**") for e in edit_deny])


def test_session_protects_user_git_and_host_files_with_work_root_under_home(env, monkeypatch):
    home = env.tmp  # the work root (tmp/wt) is then under the home, as with the default one
    monkeypatch.setenv("HOME", str(home))
    _argv, settings, cwd = _session(env)
    real_home = os.path.realpath(home)
    assert _under(os.path.realpath(cwd), [real_home])
    edit_deny = ["/" + r[len("Edit(//"):-1].removesuffix("/**") for r in settings["permissions"]["deny"]
                 if r.startswith("Edit(//")]
    fs = settings["sandbox"]["filesystem"]
    for name in (".gitconfig", ".config/git", ".claude.json"):
        path = os.path.join(real_home, name)
        assert path in fs["denyWrite"] and not _under(path, fs["allowWrite"]), path
        assert path in edit_deny, path
    for name in (".gitconfig", ".config/git"):  # a commit still finds its identity
        assert os.path.join(real_home, name) in fs["allowRead"]


def test_session_git_writes_exclude_hooks_and_config(env):
    _argv, settings, cwd = _session(env)
    common = os.path.realpath(env.tmp / "repo" / ".git")
    gitdir = os.path.join(common, "worktrees", "APP-2")
    fs = settings["sandbox"]["filesystem"]
    assert set(fs["allowWrite"]) >= {os.path.join(common, d) for d in ("objects", "refs", "logs")} | {gitdir}
    for name in ("hooks", "config", "info", "refs/replace"):
        assert os.path.join(common, name) in fs["denyWrite"]
        assert f"Edit(//{os.path.join(common, name).lstrip('/')}/**)" in settings["permissions"]["deny"]
    worktree = os.path.realpath(cwd)
    for path in (os.path.join(worktree, ".claude"), os.path.join(worktree, ".git"),
                 os.path.join(gitdir, "commondir")):
        assert path in fs["denyWrite"]
    assert common in fs["allowRead"] and not _under(os.path.join(common, "hooks"), fs["allowWrite"])


def test_git_neutral_env_appends_to_existing_entries():
    env = cc.git_neutral_env({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
                              "GIT_CONFIG_VALUE_0": "x", "PATH": "/bin"}, "/h")
    assert env["GIT_CONFIG_COUNT"] == "3" and env["PATH"] == "/bin"
    assert (env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == ("core.hooksPath", "x")
    assert neutral(env, "/h") and neutral(cc.git_neutral_env({}, "/h"), "/h")


def test_cadence_git_reads_run_with_hooks_and_fsmonitor_off(monkeypatch):
    seen = []
    monkeypatch.setattr(cc.subprocess, "run", lambda argv, **kw: seen.append(kw["env"]) or
                        SimpleNamespace(returncode=0, stdout=""))
    cc._git_refs("/r")  # read-only verbs: no hook to run
    assert seen and all(neutral(e, os.devnull) for e in seen)


def _real_repo(tmp_path, monkeypatch):
    """A main checkout, a bare remote and a detached linked worktree of ticket APP-2."""
    import subprocess

    for name, value in (("HOME", str(tmp_path)), ("GIT_CONFIG_NOSYSTEM", "1"),
                        ("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@x"),
                        ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@x")):
        monkeypatch.setenv(name, value)
    for name in ("GIT_CONFIG_COUNT", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL"):
        monkeypatch.delenv(name, raising=False)
    root, wt = tmp_path / "repo", tmp_path / "wt"
    git = lambda cwd, *a: subprocess.run(["git", *a], cwd=cwd, check=True,  # noqa: E731
                                         capture_output=True)
    git(tmp_path, "init", "-q", "--bare", str(tmp_path / "remote.git"))
    git(tmp_path, "init", "-q", str(root))
    git(root, "commit", "-q", "--allow-empty", "-m", "base")
    git(root, "remote", "add", "origin", str(tmp_path / "remote.git"))
    git(root, "worktree", "add", "-q", "--detach", str(wt / "APP-2"))
    return root, wt, git


def _hook(directory, name, marker, status=0):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(f"#!/bin/sh\ntouch {marker}\nexit {status}\n")
    (directory / name).chmod(0o755)


def test_cadencer_push_runs_the_main_checkout_hook_never_one_planted_in_the_worktree(
        tmp_path, monkeypatch):
    """B1: a relative core.hooksPath resolves in the worktree the session writes."""
    root, wt, git = _real_repo(tmp_path, monkeypatch)
    git(root, "config", "core.hooksPath", ".githooks")  # as `doctor` proposes
    _hook(root / ".githooks", "pre-push", tmp_path / "main-ran", status=1)  # a refusing guard
    _hook(wt / "APP-2" / ".githooks", "pre-push", tmp_path / "planted-ran")  # by the session
    runtime = ce.Runtime(root=str(root), worktrees_dir=wt)
    assert runtime.hooks_dir() == os.path.realpath(root / ".githooks")
    done = ce._run(runtime, ["git", "push", "-q", "origin", "HEAD:refs/heads/x"], wt / "APP-2")
    assert done.returncode != 0 and (tmp_path / "main-ran").exists()
    assert not (tmp_path / "planted-ran").exists()
    git(wt / "APP-2", "push", "-q", "origin", "HEAD:refs/heads/x")  # control: plain env runs it
    assert (tmp_path / "planted-ran").exists()


def test_cadencer_git_keeps_an_absolute_global_hooks_path(tmp_path, monkeypatch):
    root, wt, git = _real_repo(tmp_path, monkeypatch)
    hooks = tmp_path / "global-hooks"
    git(root, "config", "--global", "core.hooksPath", str(hooks))
    _hook(hooks, "pre-commit", tmp_path / "global-ran")
    runtime = ce.Runtime(root=str(root), worktrees_dir=wt)
    done = ce._run(runtime, ["git", "commit", "-q", "--allow-empty", "-m", "x"], wt / "APP-2")
    assert done.returncode == 0 and (tmp_path / "global-ran").exists()
    assert runtime.hooks_dir() == os.path.realpath(hooks)


def test_hooks_dir_inside_the_worktrees_directory_is_refused_before_any_effect(env):
    inside = env.tmp / "wt" / "APP-2" / ".githooks"
    env.runtime.runner = lambda argv, **kw: (SimpleNamespace(
        returncode=0, stdout=f"{inside}\n", stderr="") if "--git-path" in argv
        else env.world.runner(argv, **kw))
    result = step(env)
    assert (result.decision.kind, result.decision.reason, result.outcome) == (
        "refuse", "git_hooks_dir_refused", "stopped")
    assert "git_hooks_dir_in_campaign_worktrees" in result.decision.evidence["error"]
    assert env.world.calls == [] and not ce.read_attempts(env.kwargs["attempts"])  # nothing ran


def test_worktree_head_on_the_default_branch_suspends_open_pr_without_a_push(env):
    step(env), step(env)  # start, implement
    env.world.worktree_head["APP-2"] = "main"  # the session switched the worktree HEAD
    before = len(effects(env.world))
    result = step(env)
    assert (result.decision.kind, result.decision.reason, result.outcome) == (
        "suspend", "worktree_branch_mismatch", "stopped")
    assert result.decision.evidence == {"head": "main", "expected": "chore/app-2-x",
                                        "default": "main"}
    assert len(effects(env.world)) == before  # no `issue openpr` subprocess
    assert not [r for r in ce.read_attempts(env.kwargs["attempts"]) if r["kind"] == "open-pr"]
    env.world.worktree_head.clear()  # HEAD back on the ticket branch: the push goes ahead
    assert (step(env).decision.kind, effects(env.world)[-1][0][3]) == ("open-pr", "openpr")
    assert env.world.hooks_reads == 1  # once per runtime


def test_correction_session_uses_the_same_sandbox_and_reviewer_does_not():
    from foundry import campaign_review as cr

    runtime = ce.Runtime(root="/r", worktrees_dir=Path("/w"))
    plan = {"model": "m", "effort": None}
    assert "--settings" not in cr.reviewer_argv(runtime, plan, "E", "T", "sid", Path("/p"))
    assert "--settings" in ce.session_argv(runtime, plan, "E", "T", "sid", {"a": 1})


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


def test_merge_only_on_a_merge_decision(env):
    step(env), step(env), step(env)
    env.world.proof = "valid"  # a valid proof on the exact diff, CI not proven
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


def test_saved_summary_masks_credential_shaped_values(tmp_path):
    text = (
        "remote https://user:s3cret@example.com/org/repo.git and https://tok@host/x\n"
        "http.extraHeader = Authorization: Bearer abc.def-123\n"
        "Authorization: Basic dXNlcjpwYXNz\n"
        "token ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2" + " done; see https://example.com/docs and a@b"
    )
    saved = Path(ce._save_summary(tmp_path / "attempts.jsonl", "a" * 16, text)).read_text()
    for leaked in ("s3cret", "tok@", "abc.def-123", "dXNlcjpwYXNz", "ghp_A1b2"):
        assert leaked not in saved
    assert "https://[REDACTED]@example.com/org/repo.git" in saved
    assert "https://example.com/docs" in saved and "a@b" in saved
