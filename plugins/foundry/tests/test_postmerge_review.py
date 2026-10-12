"""Fixture-only postmerge capability and refusal coverage."""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from foundry import postmerge_review as pm
from foundry.models import Issue, Project, PullRequest
from foundry.review_queries import _git
from foundry.routing import (
    AcceptanceProofStore,
    ReviewDeduplicator,
    RoutingConfigError,
    acceptance_criteria,
)
from test_review_packet import git


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "remote.origin.url", "fixture/repository")
    (root / "source").write_text("base\n")
    git(root, "add", "source")
    git(root, "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    (root / "source").write_text("head\n")
    git(root, "commit", "-qam", "head")
    head = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "--detach", base)
    (root / "source").write_text("head\n")
    git(root, "commit", "-qam", "squash")
    merge = git(root, "rev-parse", "HEAD")
    pr = PullRequest(
        1,
        "https://example.invalid/pr/1",
        "feature",
        "main",
        base_sha=base,
        sha=head,
        merged=True,
        merge_sha=merge,
        state="closed",
    )
    coords = dict(
        repository="fixture/repository",
        pr=1,
        base=base,
        head=head,
        merge=merge,
        diff_hash=hashlib.sha256(
            _git(
                root,
                "diff",
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
                f"{base}...{head}",
            )
        ).hexdigest(),
    )
    issue = Issue("FIX-1", "Historical audit", pr_url=pr.url, comments=[])
    tr = SimpleNamespace(
        name="linear",
        observe_issue=lambda _: copy.deepcopy(issue),
        resolve_checkout_project=lambda _: Project("FIX", "fixture"),
        validate_issue_binding=lambda *_: None,
    )
    tr.add_comment = lambda _, text: issue.comments.append({"text": text})
    ch = SimpleNamespace(
        resolve_repo=lambda _: coords["repository"], get_pr=lambda *_: copy.deepcopy(pr)
    )
    history = {
        "provenance": "coordinator_supplied_historical_snapshot",
        "issue": {
            "id": "FIX-2",
            "version": "historical-1",
            "body": "- [ ] Preserve source\n",
            "source": "retained premerge snapshot",
        },
        "adrs": [
            {
                "id": "FIX-ADR-0001",
                "version": "historical-2",
                "body": "Historical constraint",
                "source": "retained ADR version",
            }
        ],
    }
    auth = {"id": "a" * 64, "issue": issue.id, "coordinates": coords}
    args = dict(
        root=root,
        issue_id=issue.id,
        coordinates=coords,
        authorization=auth,
        history=history,
        output_dir=tmp_path / "packet",
        host="codex",
        tracker_port=tr,
        codehost_port=ch,
    )
    return SimpleNamespace(
        root=root, pr=pr, issue=issue, coords=coords, args=args, tr=tr, ch=ch
    )


def prepare(c, host="codex"):
    c.args["host"] = host
    return pm.prepare(**c.args)


def evidence(attempt, report):
    return {
        "type": "coordinator_transcribed_native_call_return",
        "attempt_id": attempt["attempt_id"],
        "packet_hash": attempt["packet_hash"],
        "call": {
            "id": "native-call-1",
            "tool": attempt["tool"],
            "arguments": copy.deepcopy(attempt["arguments"]),
            "source": "actual host tool invocation",
        },
        "child": {"id": "native-child-1", "call_id": "native-call-1"},
        "return": {
            "child_id": "native-child-1",
            "call_id": "native-call-1",
            "status": "completed",
            "report_hash": pm.digest(report),
            "source": "distinct child tool return",
        },
        "observed": {"model": None, "effort": None, "profile_provenance": None},
    }


def record(c, a, e, report):
    return pm.record(
        root=c.root,
        issue_id=c.issue.id,
        attempt_id=a["attempt_id"],
        evidence=e,
        report=report,
        tracker_port=c.tr,
        codehost_port=c.ch,
    )


@pytest.fixture
def report():
    return {
        "findings": [],
        "evidence": [{"source": "diff.patch", "detail": "inspected historical diff"}],
    }


@pytest.mark.parametrize(
    "fault",
    [
        "open",
        "unmerged",
        "repo",
        "pr",
        "base",
        "head",
        "merge",
        "hash",
        "missing-object",
        "auth-none",
        "auth-generic",
        "auth-divergent",
        "history-empty",
        "output-existing",
    ],
)
def test_prepare_refuses_before_authorization(case, fault):
    c = case
    if fault == "open":
        c.pr.state = "open"
    elif fault == "unmerged":
        c.pr.merged = False
    elif fault in ("repo", "pr"):
        c.args["coordinates"] = {
            **c.coords,
            "repository" if fault == "repo" else "pr": "other"
            if fault == "repo"
            else 2,
        }
    elif fault in ("base", "head", "merge"):
        c.pr.__dict__[
            {"base": "base_sha", "head": "sha", "merge": "merge_sha"}[fault]
        ] = "f" * 40
    elif fault == "hash":
        c.coords["diff_hash"] = "f" * 64
    elif fault == "missing-object":
        c.coords["head"] = c.pr.sha = "f" * 40
    elif fault == "auth-none":
        c.args["authorization"] = None
    elif fault == "auth-generic":
        c.args["authorization"] = {"id": "a" * 64, "issue": "*", "coordinates": "*"}
    elif fault == "auth-divergent":
        c.args["authorization"] = {
            **c.args["authorization"],
            "coordinates": {**c.coords, "merge": "f" * 40},
        }
    elif fault == "history-empty":
        c.args["history"]["issue"]["body"] = ""
    else:
        c.args["output_dir"].mkdir()
    with pytest.raises(RoutingConfigError):
        prepare(c)
    assert c.issue.comments == []


def test_squash_exact_diff_single_attempt_and_no_original_ledger_mutation(
    case, report, tmp_path
):
    ledger = ReviewDeduplicator("fixture/repository", state_dir=tmp_path / "ledger")
    diff = _git(
        case.root,
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        f"{case.coords['base']}...{case.coords['head']}",
    )
    git(case.root, "checkout", "-q", "--detach", case.coords["head"])
    claim = ledger.claim_git(
        case.coords["diff_hash"],
        coordinates={"root": str(case.root.resolve()), "base": case.coords["base"]},
        claim_attempt_token="a" * 64,
    )
    body = case.args["history"]["issue"]["body"]
    proof_store = AcceptanceProofStore(
        "fixture/repository", state_dir=tmp_path / "ledger"
    )
    proof_store.create(
        issue_id="FIX-2",
        issue_body=body,
        reviewer_role="reviewer",
        outcomes=[{**item, "verdict": "pass"} for item in acceptance_criteria(body)],
        quality="mergeable",
        diff_hash=claim.diff_hash,
        claim_id=claim.claim_id,
        root=case.root,
        base=case.coords["base"],
        state_dir=tmp_path / "ledger",
    )
    git(case.root, "checkout", "-q", "--detach", case.coords["merge"])
    before = {
        str(p): p.read_bytes() for p in (tmp_path / "ledger").rglob("*") if p.is_file()
    }
    a = prepare(case)
    assert (case.args["output_dir"] / "diff.patch").read_bytes() == diff
    assert case.coords["head"] != case.coords["merge"]
    assert pm.receipts(case.tr, case.issue.id)[0]["type"] == "authorized_attempt"
    e = evidence(a, report)
    result = record(case, a, e, report)
    assert result["type"] == "advisory_report"
    assert result["history"] == case.args["history"]
    assert result["native_evidence"]["observed"]["model"] is None
    assert before == {
        str(p): p.read_bytes() for p in (tmp_path / "ledger").rglob("*") if p.is_file()
    }
    assert ledger.claim(diff).should_run is False
    with pytest.raises(RoutingConfigError):
        record(case, a, e, report)
    case.args["output_dir"] = tmp_path / "retry"
    with pytest.raises(RoutingConfigError):
        prepare(case)
    case.args["authorization"]["id"] = "b" * 64
    assert prepare(case)["attempt_id"] != a["attempt_id"]


@pytest.mark.parametrize("host", ["codex", "claude"])
@pytest.mark.parametrize(
    "fault",
    [
        "plan-only",
        "boolean",
        "reviewer-assertion",
        "attempt",
        "packet",
        "tool",
        "arguments",
        "history",
        "resume",
        "child",
        "correlation",
        "distinct-return",
        "return-absent",
        "source-absent",
        "report",
    ],
)
def test_native_independence_refusals_consume_attempt_without_verdict(
    case, report, host, fault
):
    a = prepare(case, host)
    e = evidence(a, report)
    if fault == "plan-only":
        e = {"independent_context": True, "plan": a}
    elif fault == "boolean":
        e = True
    elif fault == "reviewer-assertion":
        e["type"] = "reviewer_assertion"
    elif fault == "attempt":
        e["attempt_id"] = "f" * 64
    elif fault == "packet":
        e["packet_hash"] = "f" * 64
    elif fault == "tool":
        e["call"]["tool"] = "current_context"
    elif fault == "arguments":
        e["call"]["arguments"]["model"] = "wrong"
    elif fault == "history":
        e["call"]["arguments"]["fork_turns"] = "all"
    elif fault == "resume":
        e["call"]["arguments"]["resume"] = "previous-child"
    elif fault == "child":
        e["child"]["id"] = ""
    elif fault == "correlation":
        e["child"]["call_id"] = "other-call"
    elif fault == "distinct-return":
        e["return"]["child_id"] = "coordinator"
    elif fault == "return-absent":
        e.pop("return")
    elif fault == "source-absent":
        e["call"]["source"] = ""
    else:
        e["return"]["report_hash"] = "f" * 64
    with pytest.raises(RoutingConfigError):
        record(case, a, e, report)
    assert [r["type"] for r in pm.receipts(case.tr, case.issue.id)] == [
        "authorized_attempt",
        "stopped_without_verdict",
    ]
    with pytest.raises(RoutingConfigError):
        record(case, a, evidence(a, report), report)


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_both_hosts_accept_bound_actual_call_return_transcription(case, report, host):
    a = prepare(case, host)
    assert (
        record(case, a, evidence(a, report), report)["verification"]
        == "structural_only_coordinator_transcription"
    )


@pytest.mark.parametrize("phase", ["prepare-final", "record"])
def test_coordinates_revalidated(case, report, phase):
    if phase == "record":
        a = prepare(case)
        case.pr.merge_sha = "f" * 40
        with pytest.raises(RoutingConfigError):
            record(case, a, evidence(a, report), report)
    else:
        reads = 0

        def get_pr(*_):
            nonlocal reads
            reads += 1
            if reads > 1:
                case.pr.merge_sha = "f" * 40
            return copy.deepcopy(case.pr)

        case.ch.get_pr = get_pr
        with pytest.raises(RoutingConfigError):
            prepare(case)
    assert not any(
        r["type"] == "advisory_report" for r in pm.receipts(case.tr, case.issue.id)
    )


def test_merge_and_acceptance_projection_refuse_advisory_record(case, report, tmp_path):
    a = prepare(case)
    advisory = record(case, a, evidence(a, report), report)
    store = AcceptanceProofStore("fixture/repository", state_dir=tmp_path / "proofs")
    store.directory.mkdir(parents=True)
    (store.directory / ("a" * 64)).write_text(json.dumps(advisory))
    kwargs = dict(
        issue_id=case.issue.id,
        issue_body="- [ ] Preserve source\n",
        head=case.coords["head"],
        base=case.coords["base"],
        diff=b"diff",
    )
    for method in (store.current_for_sync, store.valid_for_merge):
        with pytest.raises(RoutingConfigError):
            method(**kwargs)


@pytest.mark.parametrize(
    "fault", ["packet", "attempt", "hash", "git", "terminal", "route"]
)
def test_advisory_local_reader_refusals(case, fault):
    a = prepare(case)
    args = dict(
        root=case.root,
        packet_dir=case.args["output_dir"],
        attempt_id=a["attempt_id"],
        packet_hash=a["packet_hash"],
    )
    if fault in ("packet", "route", "terminal"):
        path = case.args["output_dir"] / (
            "packet.json" if fault == "packet" else "invocation.json"
        )
        value = json.loads(path.read_text())
        if fault == "packet":
            value["history"]["issue"]["body"] = "- [ ] tampered"
        elif fault == "route":
            value["arguments"]["fork_turns"] = "all"
        else:
            value["type"] = "advisory_report"
        path.write_text(json.dumps(value))
    elif fault == "attempt":
        args["attempt_id"] = "f" * 64
    elif fault == "hash":
        args["packet_hash"] = "f" * 64
    else:
        (case.root / ".git" / "objects").rename(case.root / ".git" / "removed-objects")
    with pytest.raises(RoutingConfigError):
        pm.read(**args)


def test_guarded_reader_wire_and_both_native_prompts(case):
    import importlib.util
    from pathlib import Path

    guard_path = Path(__file__).parents[1] / "hooks" / "guard_routed_readonly.py"
    spec = importlib.util.spec_from_file_location("postmerge_guard", guard_path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    for host in ("codex", "claude"):
        case.args["output_dir"] = case.args["output_dir"].parent / host
        case.args["authorization"]["id"] = ("a" if host == "codex" else "b") * 64
        a = prepare(case, host)
        command = f"python3 {guard.PLUGIN_ROOT}/tooling/foundry_cli.py routing postmerge-review read --root {case.root} --packet-dir {case.args['output_dir']} --attempt-id {a['attempt_id']} --packet-hash {a['packet_hash']}"
        assert guard.allows_verified_diff(command)
        assert "routing postmerge-review read" in a["arguments"].get(
            "message", a["arguments"].get("prompt", "")
        )
        assert (
            pm.read(
                root=case.root,
                packet_dir=case.args["output_dir"],
                attempt_id=a["attempt_id"],
                packet_hash=a["packet_hash"],
            )
            == (case.args["output_dir"] / "diff.patch").read_bytes()
        )
        for suffix in ("; git diff", " | cat", " > file", " --extra arbitrary"):
            assert not guard.allows_verified_diff(command + suffix)
        assert not guard.allows_verified_diff(command.replace(" read ", " record "))
        assert not guard.allows_verified_diff(command.replace(a["packet_hash"], "*"))


def test_reused_child_refused_and_new_authorization_required(case, report, tmp_path):
    a = prepare(case)
    record(case, a, evidence(a, report), report)
    case.args["authorization"]["id"] = "b" * 64
    case.args["output_dir"] = tmp_path / "second"
    second = prepare(case)
    with pytest.raises(RoutingConfigError):
        record(case, second, evidence(second, report), report)


def test_local_reader_root_binding(case, tmp_path):
    import shutil

    a = prepare(case)
    other = tmp_path / "other-checkout"
    shutil.copytree(case.root, other)
    with pytest.raises(RoutingConfigError):
        pm.read(
            root=other,
            packet_dir=case.args["output_dir"],
            attempt_id=a["attempt_id"],
            packet_hash=a["packet_hash"],
        )


def test_cli_fixture_procedure(case, report, tmp_path, monkeypatch, capsys):
    import foundry
    from foundry.routing import main

    monkeypatch.setattr(foundry, "tracker", lambda **_: case.tr)
    monkeypatch.setattr(foundry, "codehost", lambda **_: case.ch)
    paths = {}
    for key, value in [
        ("coordinates", case.coords),
        ("authorization", case.args["authorization"]),
        ("history", case.args["history"]),
    ]:
        path = tmp_path / (key + ".json")
        path.write_text(json.dumps(value))
        paths[key] = str(path)
    main(
        [
            "postmerge-review",
            "prepare",
            "--root",
            str(case.root),
            "--issue",
            case.issue.id,
            "--coordinates-file",
            paths["coordinates"],
            "--authorization-file",
            paths["authorization"],
            "--history-file",
            paths["history"],
            "--output-dir",
            str(case.args["output_dir"]),
            "--host",
            "codex",
        ]
    )
    assert json.loads(capsys.readouterr().out)["status"] == "prepared"
    a = json.loads((case.args["output_dir"] / "invocation.json").read_text())
    main(
        [
            "postmerge-review",
            "read",
            "--root",
            str(case.root),
            "--packet-dir",
            str(case.args["output_dir"]),
            "--attempt-id",
            a["attempt_id"],
            "--packet-hash",
            a["packet_hash"],
        ]
    )
    assert (
        capsys.readouterr().out.encode()
        == (case.args["output_dir"] / "diff.patch").read_bytes()
    )
    for key, value in [("evidence", evidence(a, report)), ("report", report)]:
        path = tmp_path / (key + ".json")
        path.write_text(json.dumps(value))
        paths[key] = str(path)
    main(
        [
            "postmerge-review",
            "record",
            "--root",
            str(case.root),
            "--issue",
            case.issue.id,
            "--attempt-id",
            a["attempt_id"],
            "--evidence-file",
            paths["evidence"],
            "--report-file",
            paths["report"],
        ]
    )
    assert json.loads(capsys.readouterr().out)["status"] == "advisory_report"


@pytest.mark.parametrize(
    "fault",
    ["finding-without-proof", "evidence-without-source", "failed-native-return"],
)
def test_report_evidence_and_failed_return_stop_without_verdict(case, report, fault):
    a = prepare(case)
    if fault == "finding-without-proof":
        report["findings"] = [{"summary": "defect"}]
    elif fault == "evidence-without-source":
        report["evidence"] = ["assertion without source"]
    e = evidence(a, report)
    if fault == "failed-native-return":
        e["return"]["status"] = "failed"
    with pytest.raises(RoutingConfigError):
        record(case, a, e, report)
    assert pm.receipts(case.tr, case.issue.id)[-1]["type"] == "stopped_without_verdict"


def test_stopped_attempt_child_cannot_be_resumed(case, report, tmp_path):
    a = prepare(case)
    failed = evidence(a, report)
    failed["return"]["status"] = "failed"
    with pytest.raises(RoutingConfigError):
        record(case, a, failed, report)
    case.args["authorization"]["id"] = "b" * 64
    case.args["output_dir"] = tmp_path / "new-attempt"
    second = prepare(case)
    with pytest.raises(RoutingConfigError):
        record(case, second, evidence(second, report), report)


def test_default_factory_resolves_explicit_root_before_issue_read_and_write(
    case, report, monkeypatch, tmp_path
):
    import foundry
    from foundry.models import Project

    active = {"root": None, "validated": False}
    calls = []

    def resolve(cwd):
        assert cwd == str(case.root.resolve())
        active["root"] = cwd
        active["validated"] = False
        calls.append("resolve")
        return Project("FIX", "fixture")

    def validate(project, issue_id):
        assert project.key == "FIX" and issue_id == case.issue.id and active["root"]
        active["validated"] = True
        calls.append("validate")

    def observe(issue_id):
        assert active["validated"], "project_not_resolved"
        calls.append("observe")
        return copy.deepcopy(case.issue)

    def add(issue_id, text):
        assert active["validated"]
        calls.append("add")
        case.issue.comments.append({"text": text})

    tr = SimpleNamespace(
        name="linear",
        resolve_checkout_project=resolve,
        validate_issue_binding=validate,
        observe_issue=observe,
        add_comment=add,
    )
    monkeypatch.setattr(
        foundry,
        "tracker",
        lambda *, cwd: tr if cwd == str(case.root.resolve()) else None,
    )
    monkeypatch.setattr(
        foundry,
        "codehost",
        lambda *, cwd: case.ch if cwd == str(case.root.resolve()) else None,
    )
    monkeypatch.chdir(tmp_path)
    case.args.pop("tracker_port")
    case.args.pop("codehost_port")
    a = prepare(case)
    assert calls[:3] == ["resolve", "validate", "observe"]
    active["root"] = None
    active["validated"] = False
    pm.record(
        root=case.root,
        issue_id=case.issue.id,
        attempt_id=a["attempt_id"],
        evidence=evidence(a, report),
        report=report,
    )
    assert calls.count("resolve") == calls.count("validate") == 2


def test_claude_wire_matches_retained_native_interface(case):
    a = prepare(case, "claude")
    assert a["arguments"]["description"] == "Advisory historical review"
    assert (
        a["arguments"]["subagent_type"] == "foundry:" + a["route"]["binding"]["profile"]
    )
    assert "resume" not in a["arguments"]


@pytest.fixture
def linear_normalized_case(case):
    from test_linear_tracker import PROJECT, ISSUE_1_ID, LinearWire, raw_issue
    from foundry.trackers.linear import LinearTracker

    tracker = LinearTracker(token="fixture-secret", transport=LinearWire())
    tracker._activate(PROJECT)
    raw = raw_issue("LIN-1", ISSUE_1_ID)
    case.issue.id = "LIN-1"
    case.args["issue_id"] = case.issue.id
    case.args["authorization"]["issue"] = case.issue.id
    case.tr.observe_issue = lambda _: tracker._to_issue(copy.deepcopy(raw), PROJECT)

    def add_comment(_, text):
        raw["comments"]["nodes"].append(
            {
                "id": "comment-" + str(len(raw["comments"]["nodes"])),
                "body": text,
                "createdAt": "2026-09-20T10:01:00Z",
            }
        )

    case.tr.add_comment = add_comment
    case.raw = raw
    case.add_comment = add_comment
    return case


def later_ordinary_comments(case):
    for number in range(11):
        case.add_comment(case.issue.id, f"Ordinary comment {number}")


def test_linear_old_authorization_survives_recent_comment_window(
    linear_normalized_case, tmp_path
):
    c = linear_normalized_case
    a = prepare(c)
    later_ordinary_comments(c)
    c.args["output_dir"] = tmp_path / "duplicate-authorization"
    with pytest.raises(RoutingConfigError, match="authorization already consumed"):
        prepare(c)
    assert [r["attempt_id"] for r in pm.receipts(c.tr, c.issue.id)] == [a["attempt_id"]]
    normalized = c.tr.observe_issue(c.issue.id)
    assert (
        len(
            [
                comment
                for comment in normalized.comments
                if not comment["text"].startswith(pm.PREFIX)
            ]
        )
        == 10
    )


def test_linear_old_attempt_and_terminal_survive_recent_comment_window(
    linear_normalized_case, report
):
    c = linear_normalized_case
    a = prepare(c)
    later_ordinary_comments(c)
    result = record(c, a, evidence(a, report), report)
    assert result["type"] == "advisory_report"
    later_ordinary_comments(c)
    assert [r["type"] for r in pm.receipts(c.tr, c.issue.id)] == [
        "authorized_attempt",
        "advisory_report",
    ]
    with pytest.raises(RoutingConfigError, match="terminal"):
        record(c, a, evidence(a, report), report)


def test_linear_advisory_comments_refuse_incomplete_connection(linear_normalized_case):
    from foundry.trackers.linear import LinearTrackerError

    c = linear_normalized_case
    c.raw["comments"]["pageInfo"]["hasNextPage"] = True
    with pytest.raises(LinearTrackerError, match="nested_pagination_unavailable"):
        pm.receipts(c.tr, c.issue.id)


@pytest.mark.parametrize("provider", ["youtrack", "ghprojects", "devhub", "unknown"])
def test_default_unqualified_provider_refused_before_history_or_write(
    case, provider, monkeypatch
):
    import foundry

    case.tr.name = provider
    case.args.pop("tracker_port")
    case.args.pop("codehost_port")
    monkeypatch.setattr(foundry, "tracker", lambda **_: case.tr)
    monkeypatch.setattr(foundry, "codehost", lambda **_: case.ch)
    with pytest.raises(RoutingConfigError, match="Linear"):
        prepare(case)
    assert case.issue.comments == []
