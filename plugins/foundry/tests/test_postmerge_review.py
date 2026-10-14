"""Fixture-only postmerge capability and refusal coverage."""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from pathlib import Path
from foundry import registry

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
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(Path(__file__).parents[1]))
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
    result = {
        "type": "coordinator_transcribed_native_call_return",
        "attempt_id": attempt["attempt_id"],
        "packet_hash": attempt["packet_hash"],
        "call": {
            "id": "native-call-1",
            "tool": attempt["tool"],
            "arguments": copy.deepcopy(attempt["launch_arguments"]),
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

    if attempt['host'] == 'claude':
        result['hook'] = {'call_id': result['call']['id'], 'source': 'actual hook context',
                          'context': copy.deepcopy(attempt.get('hook_context', {}))}
    return result


def record(c, a, e, report):
    return pm.record(
        root=c.root,
        issue_id=c.issue.id,
        attempt_id=a["attempt_id"],
        packet_dir=c.args["output_dir"],
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
    reasons = {"repo": "repository mismatch", "hash": "historical diff unavailable or divergent",
               "missing-object": "historical Git objects unavailable", "history-empty": "historical identity/version/body/source required",
               "output-existing": "packet destination must be new"}
    reason = reasons.get(fault, "exact explicit authorization required" if fault.startswith("auth-") else "merged PR coordinates disagree")
    with pytest.raises(RoutingConfigError, match=reason):
        prepare(c)
    assert c.issue.comments == []


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_squash_exact_diff_single_attempt_and_no_original_ledger_mutation(
    case, report, tmp_path, host
):
    ledger = ReviewDeduplicator("fixture/repository")
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
        "fixture/repository"
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
    )
    git(case.root, "checkout", "-q", "--detach", case.coords["merge"])
    before = {
        str(p): p.read_bytes() for p in Path(registry.data_dir()).rglob("*") if p.is_file()
    }
    a = prepare(case, host)
    assert (case.args["output_dir"] / "diff.patch").read_bytes() == diff
    assert case.coords["head"] != case.coords["merge"]
    assert pm.receipts(case.tr, case.issue.id)[0]["type"] == "authorized_attempt"
    e = evidence(a, report)
    result = record(case, a, e, report)
    assert result["type"] == "advisory_report"
    assert result["history_hash"] == pm.digest(case.args["history"])
    assert result["native_evidence_hash"] == pm.digest(e)
    assert before == {
        str(p): p.read_bytes() for p in Path(registry.data_dir()).rglob("*") if p.is_file()
    }
    assert ledger.claim(diff).should_run is False
    with pytest.raises(RoutingConfigError, match="attempt absent, ambiguous or terminal"):
        record(case, a, e, report)
    case.args["output_dir"] = tmp_path / "retry"
    with pytest.raises(RoutingConfigError, match="authorization already consumed"):
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
    reason = ("typed bound native evidence required" if fault in {"plan-only", "boolean", "reviewer-assertion", "attempt", "packet", "return-absent"}
              else "actual routed native call required" if fault in {"tool", "arguments", "history", "resume", "source-absent"}
              else "new native child identity" if fault in {"child", "correlation"}
              else "distinct correlated child return required")
    with pytest.raises(RoutingConfigError, match=reason):
        record(case, a, e, report)
    assert [r["type"] for r in pm.receipts(case.tr, case.issue.id)] == [
        "authorized_attempt",
        "stopped_without_verdict",
    ]
    with pytest.raises(RoutingConfigError, match="attempt absent, ambiguous or terminal"):
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
        with pytest.raises(RoutingConfigError, match="merged PR coordinates disagree"):
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
        with pytest.raises(RoutingConfigError, match="merged PR coordinates disagree"):
            prepare(case)
    assert not any(
        r["type"] == "advisory_report" for r in pm.receipts(case.tr, case.issue.id)
    )
    if phase == "prepare-final":
        assert case.issue.comments == []


def test_merge_and_acceptance_projection_refuse_advisory_record(case, report, tmp_path):
    a = prepare(case)
    advisory = record(case, a, evidence(a, report), report)
    store = AcceptanceProofStore("fixture/repository", state_dir=tmp_path / "proofs")
    ledger = ReviewDeduplicator("fixture/repository", state_dir=tmp_path / "proofs")
    diff = _git(case.root, "diff", "--binary", "--no-ext-diff", "--no-textconv", f"{case.coords['base']}...{case.coords['merge']}")
    claim = ledger.claim_git(hashlib.sha256(diff).hexdigest(), coordinates={"root": str(case.root), "base": case.coords["base"]}, claim_attempt_token="c" * 64)
    store.create(issue_id=case.issue.id, issue_body="- [ ] Preserve source\n", reviewer_role="reviewer", outcomes=[{**item, "verdict": "pass"} for item in acceptance_criteria("- [ ] Preserve source\n")], quality="mergeable", diff_hash=claim.diff_hash, claim_id=claim.claim_id, root=case.root, base=case.coords["base"], state_dir=tmp_path / "proofs")
    (store.directory / ("a" * 64)).write_text(json.dumps(advisory))
    kwargs = dict(
        issue_id=case.issue.id,
        issue_body="- [ ] Preserve source\n",
        head=case.coords["head"],
        base=case.coords["base"],
        diff=diff,
    )
    for method in (store.current_for_sync, store.valid_for_merge):
        with pytest.raises(RoutingConfigError, match="advisory postmerge"):
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
            value["prepared_arguments_hash" if "prepared_arguments_hash" in value else "arguments_hash"] = pm.digest(value["arguments"])
        else:
            value["type"] = "advisory_report"
        path.write_text(json.dumps(value))
    elif fault == "attempt":
        args["attempt_id"] = "f" * 64
    elif fault == "hash":
        args["packet_hash"] = "f" * 64
    else:
        (case.root / ".git" / "objects").rename(case.root / ".git" / "removed-objects")
    with pytest.raises(RoutingConfigError, match="prepared native invocation divergent" if fault == "route" else "historical Git objects unavailable" if fault == "git" else "prepared packet identity mismatch"):
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
        command = a["arguments"].get("message", a["arguments"].get("prompt", "")).split("Advisory verifier: ", 1)[1].splitlines()[0]
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
    with pytest.raises(RoutingConfigError, match="native child already used"):
        record(case, second, evidence(second, report), report)


def test_local_reader_root_binding(case, tmp_path):
    import shutil

    a = prepare(case)
    other = tmp_path / "other-checkout"
    shutil.copytree(case.root, other)
    with pytest.raises(RoutingConfigError, match="prepared packet identity mismatch"):
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
    exact = pm.advisory_git(case.root, 'diff', '--binary', '--no-ext-diff', '--no-textconv', '--no-color',
                            '--no-renames', '--src-prefix=a/', '--dst-prefix=b/', '--no-relative',
                            '--submodule=short', f"{case.coords['base']}...{case.coords['head']}")
    assert hashlib.sha256(exact).hexdigest() == case.coords['diff_hash']
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
            "--packet-dir",
            str(case.args["output_dir"]),
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
    with pytest.raises(RoutingConfigError, match="each finding requires" if fault == "finding-without-proof" else "report evidence requires" if fault == "evidence-without-source" else "distinct correlated child return"):
        record(case, a, e, report)
    assert pm.receipts(case.tr, case.issue.id)[-1]["type"] == "stopped_without_verdict"


def test_stopped_attempt_child_cannot_be_resumed(case, report, tmp_path):
    a = prepare(case)
    failed = evidence(a, report)
    failed["return"]["status"] = "failed"
    with pytest.raises(RoutingConfigError, match="distinct correlated child return"):
        record(case, a, failed, report)
    case.args["authorization"]["id"] = "b" * 64
    case.args["output_dir"] = tmp_path / "new-attempt"
    second = prepare(case)
    with pytest.raises(RoutingConfigError, match="native child already used"):
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
        packet_dir=case.args["output_dir"],
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
        "advisory_confirmation",
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


def test_public_receipts_keep_private_packet_and_arguments_private(case, report):
    a = prepare(case)
    record(case, a, evidence(a, report), report)
    public = json.dumps(pm.receipts(case.tr, case.issue.id))
    assert str(case.root) not in public
    assert str(case.args["output_dir"]) not in public
    assert "Goal:" not in public
    assert "retained premerge snapshot" not in public
    assert "inspected historical diff" in public
    assert "arguments_hash" in public and "history_hash" in public


def test_claude_descriptor_uses_shared_logical_facade(case):
    a = prepare(case, "claude")
    assert a["launch_arguments"]["subagent_type"] == "foundry:maigret"
    assert "BEGIN ROLE CONTRACT" in a["arguments"]["prompt"]
    assert a["arguments"]["max_turns"] == 24


def test_guard_rejects_naked_newlines_in_emitted_reader(case):
    import importlib.util
    spec = importlib.util.spec_from_file_location("guard_newline", Path(__file__).parents[1] / "hooks/guard_routed_readonly.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    a = prepare(case)
    command = a["arguments"]["message"].split("Advisory verifier: ", 1)[1].splitlines()[0]
    for separator in ("\n", "\r", "\r\n"):
        assert not guard.allows_verified_diff(command.replace("--root ", "--root" + separator))


def test_prepare_collision_is_consumed_and_never_returns_packet(case):
    original = case.tr.add_comment
    def collide(issue_id, text):
        original(issue_id, text)
        original(issue_id, text)
    case.tr.add_comment = collide
    with pytest.raises(RoutingConfigError, match="readback ambiguous"):
        prepare(case)
    assert not case.args["output_dir"].exists()


def test_record_collision_stops_without_verdict(case, report):
    a = prepare(case)
    duplicate = json.loads(case.issue.comments[0]["text"][len(pm.PREFIX):])
    duplicate["attempt_id"] = "f" * 64
    case.issue.comments.append({"text": pm.PREFIX + json.dumps(duplicate)})
    with pytest.raises(RoutingConfigError, match="authorization collision"):
        record(case, a, evidence(a, report), report)
    assert pm.receipts(case.tr, case.issue.id)[-1]["type"] == "stopped_without_verdict"


def test_advisory_git_is_deterministic_and_disables_lazy_fetch(case, monkeypatch):
    git(case.root, "config", "diff.noprefix", "true")
    git(case.root, "config", "diff.mnemonicPrefix", "true")
    git(case.root, "config", "diff.algorithm", "minimal")
    a = prepare(case)
    assert pm.read(root=case.root, packet_dir=case.args["output_dir"], attempt_id=a["attempt_id"], packet_hash=a["packet_hash"])
    import subprocess
    real = subprocess.run
    observed = []
    def capture(*args, **kwargs):
        observed.append(kwargs.get("env", {}).get("GIT_NO_LAZY_FETCH"))
        return real(*args, **kwargs)
    monkeypatch.setattr(subprocess, "run", capture)
    pm.historical_diff(case.root, case.coords, case.ch)
    assert observed and set(observed) == {"1"}


def test_real_linear_comment_requires_project_and_advisory_uses_write_tier(case, monkeypatch):
    from test_linear_tracker import PROJECT, LinearWire
    from foundry.trackers.linear import LinearTracker, LinearBindingError
    wire = LinearWire()
    tracker = LinearTracker(token="fixture-secret", transport=wire)
    tracker._activate(PROJECT)
    with pytest.raises(LinearBindingError, match="mutation_project_required"):
        tracker.add_comment("LIN-1", "fixture")
    case.tr = tracker
    case.args["tracker_port"] = tracker
    case.args["issue_id"] = "LIN-1"
    case.args["authorization"]["issue"] = "LIN-1"
    git(case.root, "config", "remote.origin.url", "https://github.com/acme/widgets.git")
    calls = []
    monkeypatch.setattr(tracker, "resolve_checkout_project", lambda *args, **kwargs: PROJECT)
    monkeypatch.setattr(tracker, "validate_mutation_repository", lambda repo, identity: calls.append((repo, identity)))
    monkeypatch.setattr(tracker, "validate_mutation_project", lambda project: calls.append(project))
    a = prepare(case)
    assert a["issue"] == "LIN-1"
    assert PROJECT in calls
    assert any(isinstance(item, tuple) and item[1] == PROJECT.extra["canonical_repo"] for item in calls)
    assert pm.receipts(tracker, "LIN-1")[0]["type"] == "authorized_attempt"


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_emitted_reader_executes_with_selected_plugin(case, host, monkeypatch, tmp_path):
    import os
    import shutil
    import subprocess
    plugin = Path(__file__).parents[1]
    installed = tmp_path / "selected-plugin"
    shutil.copytree(plugin, installed)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(installed))
    a = prepare(case, host)
    prompt = a["arguments"].get("message", a["arguments"].get("prompt", ""))
    command = prompt.split("Advisory verifier: ", 1)[1].splitlines()[0]
    import importlib.util
    spec = importlib.util.spec_from_file_location("selected_guard", installed / "hooks/guard_routed_readonly.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    if host == "claude":
        assert guard.allows_verified_diff(command)
    env = dict(os.environ)
    if host == "codex":
        env.pop("CLAUDE_PLUGIN_ROOT", None)
    output = subprocess.run(command, shell=True, cwd=case.root, env=env, capture_output=True)
    assert output.returncode == 0, output.stderr
    assert output.stdout == (case.args["output_dir"] / "diff.patch").read_bytes()


@pytest.mark.parametrize("fault,consumed,publication,reason", [
    ("coordinates", "no", "no", "merged PR coordinates disagree"),
    ("write-interrupted", "unknown", "unknown", "controlled operation failure"),
    ("packet-failure", "yes", "yes", "controlled operation failure"),
])
def test_cli_safe_reason_and_effect_status(case, monkeypatch, capsys, fault, consumed, publication, reason):
    from foundry.routing import main
    def cli(_args):
        if fault == "coordinates":
            case.pr.merge_sha = "f" * 40
        elif fault == "write-interrupted":
            original = case.tr.add_comment
            def fail(issue_id, text):
                original(issue_id, text)
                raise KeyboardInterrupt("private-path-secret-payload")
            case.tr.add_comment = fail
        else:
            monkeypatch.setattr(pm.os, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("private-path-secret-payload")))
        return prepare(case)
    monkeypatch.setattr(pm, "cli", cli)
    with pytest.raises(SystemExit) as failure:
        main(["postmerge-review", "prepare", "--root", str(case.root), "--issue", case.issue.id,
              "--coordinates-file", "fixture", "--authorization-file", "fixture", "--history-file", "fixture",
              "--output-dir", "fixture", "--host", "codex"])
    assert failure.value.code == 2
    text = capsys.readouterr().err
    assert reason in text
    assert f"authorization_consumed={consumed}" in text
    assert f"publication={publication}" in text
    assert "private-path-secret-payload" not in text


def test_prepare_collision_after_verified_write(case):
    original = case.tr.observe_issue
    reads = 0
    def observe(issue_id):
        nonlocal reads
        reads += 1
        result = original(issue_id)
        if reads == 3:
            duplicate = json.loads(result.comments[0]["text"][len(pm.PREFIX):])
            duplicate["attempt_id"] = "f" * 64
            result.comments.append({"text": pm.PREFIX + json.dumps(duplicate)})
        return result
    case.tr.observe_issue = observe
    with pytest.raises(RoutingConfigError, match="authorization collision"):
        prepare(case)
    assert not case.args["output_dir"].exists()
    assert pm.PUBLICATION.get() == "yes"


def test_stopped_submission_is_retained_privately(case, report):
    a = prepare(case)
    e = evidence(a, report)
    e["return"]["status"] = "failed"
    e["call"]["source"] = str(case.root / "private-native-log")
    with pytest.raises(RoutingConfigError, match="distinct correlated child return"):
        record(case, a, e, report)
    submitted = json.loads((case.args["output_dir"] / "submission.json").read_text())
    assert submitted == {"evidence": e, "report": report}
    assert str(case.root) not in json.dumps(pm.receipts(case.tr, case.issue.id))


def test_claude_facade_selected_route_overrides_independent_policy_resolution(case, monkeypatch):
    from foundry import routing_facades as rf
    from dataclasses import replace
    original = rf.claude_route_plan
    def plan(*args, **kwargs):
        callback = kwargs["_preclaim_validate"]
        def capture(resolution):
            resolution = dict(resolution)
            resolution["route"] = replace(resolution["route"], model="opus-5.5", effort="xhigh")
            callback(resolution)
        kwargs["_preclaim_validate"] = capture
        return original(*args, **kwargs)
    monkeypatch.setattr(rf, "claude_route_plan", plan)
    a = prepare(case, "claude")
    assert a["route"]["model"] == "opus-5.5" and a["route"]["effort"] == "xhigh"
    assert a["arguments"]["subagent_type"] == "foundry:routed-readonly-xhigh-opus-5.5"


def test_advisory_diff_ignores_color_and_rename_configuration(case):
    git(case.root, "checkout", "-q", "--detach", case.coords["head"])
    base = case.coords["head"]
    git(case.root, "mv", "source", "renamed")
    git(case.root, "commit", "-qm", "rename")
    head = git(case.root, "rev-parse", "HEAD")
    case.coords.update(base=base, head=head, merge=head)
    case.pr.base_sha, case.pr.sha, case.pr.merge_sha = base, head, head
    git(case.root, "config", "diff.renames", "false")
    initial = pm.advisory_git(case.root, "diff", "--binary", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/", "--no-relative", "--submodule=short", f"{base}...{head}")
    case.coords["diff_hash"] = hashlib.sha256(initial).hexdigest()
    git(case.root, "config", "diff.renames", "true")
    git(case.root, "config", "color.ui", "always")
    assert pm.historical_diff(case.root, case.coords, case.ch) == initial


def test_reused_authorization_diagnostic_is_verified_consumed(case, tmp_path):
    prepare(case)
    case.args["output_dir"] = tmp_path / "retry"
    with pytest.raises(RoutingConfigError, match="authorization already consumed"):
        prepare(case)
    assert pm.PUBLICATION.get() == "yes"
    assert pm.WRITE_EFFECT.get() == "no"


def test_public_history_identities_refuse_paths(case):
    case.args["history"]["issue"]["version"] = str(case.root / "private-history")
    with pytest.raises(RoutingConfigError, match="public historical identity/version invalid"):
        prepare(case)
    assert case.issue.comments == []


@pytest.mark.parametrize("fault", ["missing", "json", "object", "oserror"])
def test_unreadable_packet_records_controlled_stop(case, report, fault, monkeypatch):
    a = prepare(case)
    path = case.args["output_dir"] / "invocation.json"
    if fault == "missing":
        path.unlink()
    elif fault == "json":
        path.write_text("{")
    elif fault == "object":
        path.write_text("[]")
    else:
        monkeypatch.setattr(pm, "_load", lambda _: (_ for _ in ()).throw(OSError("private")))
    with pytest.raises(RoutingConfigError, match="private packet unreadable"):
        record(case, a, evidence(a, report), report)
    assert pm.receipts(case.tr, case.issue.id)[-1]["type"] == "stopped_without_verdict"
    assert pm.PUBLICATION.get() == "yes"


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("suffix", [";unsafe", "\nunsafe", "$unsafe", "[unsafe]", "#unsafe"])
def test_unsafe_packet_path_refused_before_effect(case, host, suffix):
    case.args["output_dir"] = case.args["output_dir"].with_name("packet" + suffix)
    with pytest.raises(RoutingConfigError, match="reader path refused by read-only guard"):
        prepare(case, host)
    assert not case.issue.comments


def test_claude_copy_selection_requires_explicit_root_before_effect(case, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT")
    with pytest.raises(RoutingConfigError, match="explicit Claude plugin root required"):
        prepare(case, "claude")
    assert not case.issue.comments


@pytest.mark.parametrize("field", ["role", "selected_tier", "model", "effort_parameters.requested",
                                  "effort_parameters.transmitted", "transmitted_launch.profile",
                                  "transmitted_launch.transmitted_model", "gate_floor", "gate_effort_floor"])
@pytest.mark.parametrize("mutation", ["missing", "different"])
def test_claude_stable_hook_fields_refuse(case, report, field, mutation):
    a = prepare(case, "claude")
    e = evidence(a, report)
    context = copy.deepcopy(a.get("hook_context", {}))
    keys = field.split(".")
    container = context
    for key in keys[:-1]:
        container = container.setdefault(key, {})
    if mutation == "missing":
        container.pop(keys[-1], None)
    else:
        container[keys[-1]] = "divergent"
    e["hook"] = {"call_id": e["call"]["id"], "source": "actual hook context", "context": context}
    with pytest.raises(RoutingConfigError, match="observed Claude hook route divergent"):
        record(case, a, e, report)
    assert pm.receipts(case.tr, case.issue.id)[-1]["type"] == "stopped_without_verdict"


@pytest.mark.parametrize("fault", ["absent", "uncorrelated", "no-source", "route-copy"])
def test_claude_hook_requires_correlated_observation(case, report, fault):
    a = prepare(case, "claude")
    e = evidence(a, report)
    e["hook"] = {"call_id": e["call"]["id"], "source": "actual hook context", "context": a.get("hook_context", {})}
    if fault == "absent":
        e.pop("hook")
    elif fault == "uncorrelated":
        e["hook"]["call_id"] = "other"
    elif fault == "no-source":
        e["hook"]["source"] = ""
    else:
        e["hook"]["context"] = a["route"]
    with pytest.raises(RoutingConfigError, match="observed Claude hook"):
        record(case, a, e, report)


def test_codex_advisory_shared_facade_packet_and_override_refusals(case):
    from foundry.routing_facades import codex_spawn_plan
    with pytest.raises(RoutingConfigError, match="task packet"):
        codex_spawn_plan("reviewer", "incomplete", root=case.root, advisory=True, _observe=False)
    with pytest.raises(RoutingConfigError, match="profil Codex résolu"):
        codex_spawn_plan("reviewer", "Goal:\nx\nInputs:\nx\nConstraints:\nx\nDone when:\nx", root=case.root,
                         advisory=True, effective_profile=[], _observe=False)


@pytest.mark.parametrize("fault", ["prompt", "absolute", "history"])
def test_report_unpublishable_refused_before_terminal_write(case, report, fault):
    a = prepare(case)
    if fault == "prompt":
        report["evidence"][0]["detail"] = a["arguments"]["message"]
    elif fault == "absolute":
        report["evidence"][0]["source"] = str(case.root / "source")
    else:
        report["evidence"][0]["detail"] = case.args["history"]["issue"]["body"]
    with pytest.raises(RoutingConfigError, match="report not publishable"):
        record(case, a, evidence(a, report), report)
    assert not any(r["type"] == "advisory_report" for r in pm.receipts(case.tr, case.issue.id))


def test_report_publication_bound_counts_utf8_and_prefix(case, report):
    a = prepare(case)
    report["evidence"][0]["detail"] = "é" * 6200
    with pytest.raises(RoutingConfigError, match="published receipt exceeds qualified bound"):
        record(case, a, evidence(a, report), report)
    assert [r["type"] for r in pm.receipts(case.tr, case.issue.id)] == ["authorized_attempt", "stopped_without_verdict"]


@pytest.mark.parametrize("kind", ["authorized_attempt", "stopped_without_verdict", "advisory_confirmation"])
def test_every_overbound_comment_refused_before_transport(case, kind):
    before = copy.deepcopy(case.issue.comments)
    with pytest.raises(RoutingConfigError, match="published receipt exceeds qualified bound"):
        pm.append(case.tr, case.issue.id, {"schema": pm.TERMINAL_SCHEMA if kind == "advisory_confirmation" else pm.SCHEMA,
                                         "attempt_id": "a" * 64, "type": kind, "issue": case.issue.id,
                                         "padding": "é" * 6200}, case.root)
    assert case.issue.comments == before


@pytest.mark.parametrize("mutation", ["changed", "malformed", "wrong-type"])
def test_terminal_written_read_different_no_retry_and_new_authorization(case, report, tmp_path, mutation):
    a = prepare(case)
    original = case.tr.add_comment
    writes = []
    def alter(issue_id, text):
        writes.append(text)
        if '"type": "advisory_report"' in text:
            value = json.loads(text.split("\n", 2)[2])
            prefix = text[:text.index("\n", len(pm.PREFIX)) + 1]
            if mutation == "changed":
                value["report_hash"] = "f" * 64
                text = prefix + json.dumps(value)
            elif mutation == "malformed":
                text = prefix + "{broken"
            else:
                value["type"] = "unexpected"
                text = prefix + json.dumps(value)
        original(issue_id, text)
    case.tr.add_comment = alter
    with pytest.raises(RoutingConfigError, match="receipt readback ambiguous"):
        record(case, a, evidence(a, report), report)
    assert len(writes) == 1  # No retry, confirmation or compensating stop.
    assert pm.PUBLICATION.get() == "yes"
    assert not pm.restituted_reports(case.tr, case.issue.id)
    assert (case.args["output_dir"] / "publication-readback.json").is_file()
    case.tr.add_comment = original
    case.args["output_dir"] = tmp_path / "new"
    with pytest.raises(RoutingConfigError, match="authorization already consumed"):
        prepare(case)
    case.args["authorization"]["id"] = "b" * 64
    second = prepare(case)
    e = evidence(second, report)
    e["child"]["id"] = e["return"]["child_id"] = "native-child-2"
    record(case, second, e, report)
    assert len(pm.restituted_reports(case.tr, case.issue.id)) == 1


def test_reader_consumption_is_unknown_even_after_consumed_prepare(case, capsys):
    from foundry.routing import main
    a = prepare(case)
    with pytest.raises(SystemExit):
        main(["postmerge-review", "read", "--root", str(case.root), "--packet-dir", str(case.args["output_dir"]),
              "--attempt-id", a["attempt_id"], "--packet-hash", "f" * 64])
    assert "authorization_consumed=unknown; publication=no" in capsys.readouterr().err
    assert len(case.issue.comments) == 1


def test_unsupported_git_lazy_fetch_refused_explicitly(case, monkeypatch):
    import subprocess
    real = subprocess.run
    def old_git(args, **kwargs):
        if args[:2] == ["git", "--version"]:
            return SimpleNamespace(returncode=0, stdout=b"git version 2.44.0", stderr=b"")
        return real(args, **kwargs)
    monkeypatch.setattr(subprocess, "run", old_git)
    with pytest.raises(RoutingConfigError, match="Git 2.45 or later required"):
        prepare(case)
    assert not case.issue.comments


def changed_diff_case(case):
    git(case.root, "checkout", "-q", "--detach", case.coords["head"])
    (case.root / "source").write_text("head\n\ncontext\n")
    git(case.root, "commit", "-qam", "blank context base")
    base = git(case.root, "rev-parse", "HEAD")
    case.coords["base"] = case.pr.base_sha = base
    (case.root / "source").write_text("changed\n\ncontext\n")
    (case.root / "zzz").write_text("extra\n")
    git(case.root, "add", "source", "zzz")
    git(case.root, "commit", "-qm", "extended fixture")
    head = git(case.root, "rev-parse", "HEAD")
    case.coords.update(head=head, merge=head)
    case.pr.sha = case.pr.merge_sha = head
    spec = f"{case.coords['base']}...{head}"
    initial = pm.advisory_git(case.root, "diff", "--binary", "--no-ext-diff", "--no-textconv", spec)
    case.coords["diff_hash"] = hashlib.sha256(initial).hexdigest()
    return spec, initial


@pytest.mark.parametrize("config", ["diff.orderFile", "diff.suppressBlankEmpty"])
def test_current_diff_config_influence_exercised_then_neutralized(case, config):
    spec, initial = changed_diff_case(case)
    if config == "diff.orderFile":
        order = case.root / "order"
        order.write_text("zzz\nsource\n")
        git(case.root, "config", config, str(order))
    else:
        git(case.root, "config", config, "true")
    assert _git(case.root, "diff", "--binary", "--no-ext-diff", "--no-textconv", spec) != initial
    assert pm.historical_diff(case.root, case.coords, case.ch) == initial


@pytest.mark.parametrize("attribute", ["diff=fixture", "-diff"])
def test_current_diff_attributes_influence_exercised_and_refused(case, attribute):
    spec, initial = changed_diff_case(case)
    (case.root / ".gitattributes").write_text("source " + attribute + "\n")
    git(case.root, "config", "diff.fixture.xfuncname", "head")
    git(case.root, "config", "diff.fixture.binary", "true")
    assert _git(case.root, "diff", "--binary", "--no-ext-diff", "--no-textconv", spec) != initial
    with pytest.raises(RoutingConfigError, match="historical diff attributes unsupported"):
        prepare(case)
    assert not case.issue.comments


@pytest.mark.parametrize("reader", ["read-review", "list-review", "search-review"])
@pytest.mark.parametrize("separator", ["\n", "\r", "\r\n"])
def test_premerge_readers_refuse_all_newlines(reader, separator):
    import importlib.util
    spec = importlib.util.spec_from_file_location("guard_premerge", Path(__file__).parents[1] / "hooks/guard_routed_readonly.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    command = f'python3 ${{CLAUDE_PLUGIN_ROOT}}/tooling/foundry_cli.py routing {reader} --diff-hash {"a" * 64} --claim-id {"b" * 64} --root /fixture --base {"c" * 40}'
    if reader == "search-review":
        command += " --text source"
    assert guard.allows_verified_diff(command)
    assert not guard.allows_verified_diff(command.replace("--root ", "--root" + separator))


def test_claude_volatile_hook_fields_do_not_change_closed_equality(case, report):
    a = prepare(case, "claude")
    e = evidence(a, report)
    for key in ("escalation", "host_version", "warnings", "availability_probed", "extra"):
        e["hook"]["context"][key] = "different volatile observation"
    assert record(case, a, e, report)["type"] == "advisory_report"
    assert pm.restituted_reports(case.tr, case.issue.id)[0]["report"] == report


def test_tracker_only_restitution_survives_private_packet_loss(case, report):
    import shutil
    report["findings"] = [{"summary": "Finding retained", "evidence": copy.deepcopy(report["evidence"])}]
    a = prepare(case)
    expected = record(case, a, evidence(a, report), report)
    shutil.rmtree(case.args["output_dir"])
    [restored] = pm.restituted_reports(case.tr, case.issue.id)
    assert restored == expected
    assert restored["report"] == report
    assert pm.digest(restored["report"]) == restored["report_hash"]
    public = json.dumps(restored)
    assert "Goal:" not in public and str(case.root) not in public


def test_codex_advisory_detects_supplied_profile_and_creates_no_claim(case):
    case.args["effective_profile"] = {"model": True, "model_reasoning_effort": True}
    before = {str(p): p.read_bytes() for p in Path(registry.data_dir()).rglob("*") if p.is_file()}
    a = prepare(case)
    assert len(a["route"]["warnings"]) >= 2
    assert before == {str(p): p.read_bytes() for p in Path(registry.data_dir()).rglob("*") if p.is_file()}
    assert a["arguments"]["fork_turns"] == "none"


def test_historical_v1_reports_are_retained_without_requalification(case, report):
    a = prepare(case)
    historical = {"schema": pm.SCHEMA, "type": "advisory_report", "issue": case.issue.id,
                  "attempt_id": a["attempt_id"], "report_hash": pm.digest(report)}
    case.issue.comments.append({"text": pm.comment_text(historical)})
    assert historical in pm.receipts(case.tr, case.issue.id)
    assert pm.restituted_reports(case.tr, case.issue.id) == []


def test_linear_retains_malformed_terminal_outside_recent_window(linear_normalized_case, report):
    c = linear_normalized_case
    a = prepare(c)
    c.add_comment(c.issue.id, pm.TERMINAL_PREFIX + a["attempt_id"] + "\n{altered")
    later_ordinary_comments(c)
    assert pm.receipts(c.tr, c.issue.id)[-1]["type"] == "isolated_terminal"
    assert not pm.restituted_reports(c.tr, c.issue.id)


def test_malformed_terminal_envelope_isolates_prior_authority_only(case, report, tmp_path):
    a = prepare(case)
    case.issue.comments.append({"text": pm.TERMINAL_PREFIX + "altered-envelope\n{broken"})
    with pytest.raises(RoutingConfigError, match="attempt absent, ambiguous or terminal"):
        record(case, a, evidence(a, report), report)
    case.args["output_dir"] = tmp_path / "new-after-broken-envelope"
    with pytest.raises(RoutingConfigError, match="authorization already consumed"):
        prepare(case)
    case.args["authorization"]["id"] = "b" * 64
    second = prepare(case)
    e = evidence(second, report)
    e["child"]["id"] = e["return"]["child_id"] = "native-child-2"
    assert record(case, second, e, report)["type"] == "advisory_report"


@pytest.mark.parametrize("mutation", ["missing", "generic", "duplicate"])
def test_terminal_isolation_never_relaxes_consumption(case, report, mutation):
    a = prepare(case)
    case.issue.comments.append({"text": pm.TERMINAL_PREFIX + a["attempt_id"] + "\n{altered"})
    receipt = json.loads(case.issue.comments[0]["text"][len(pm.PREFIX):])
    if mutation == "missing":
        receipt.pop("authorization")
        case.issue.comments[0]["text"] = pm.comment_text(receipt)
    elif mutation == "generic":
        receipt["authorization"]["coordinates"] = "*"
        case.issue.comments[0]["text"] = pm.comment_text(receipt)
    else:
        case.issue.comments.append(copy.deepcopy(case.issue.comments[0]))
    before = copy.deepcopy(case.issue.comments)
    with pytest.raises(RoutingConfigError, match="malformed advisory authorization" if mutation != "duplicate" else "authorization already consumed"):
        prepare(case)
    assert case.issue.comments == before


@pytest.mark.parametrize("phase", ["create", "readback"])
def test_real_linear_transport_exception_after_terminal_write_saves_local_diff(case, report, monkeypatch, phase):
    from test_linear_tracker import PROJECT, LinearWire
    from foundry.trackers.linear import LinearTracker, LinearTrackerError
    from foundry.trackers.base import TrackerConflictError
    class AlteredWire(LinearWire):
        def __call__(self, document, variables):
            result = super().__call__(document, variables)
            if "FoundryLinearCommentCreate" in document and '"type": "advisory_report"' in variables['input']['body']:
                comment = result['data']['commentCreate']['comment']
                altered = comment['body'] + ' altered'
                self.comments[comment['id']]['body'] = altered
                self.issues['LIN-1']['comments']['nodes'][-1]['body'] = altered
                if phase == 'create':
                    comment['body'] = altered
            return result
    wire = AlteredWire()
    tracker = LinearTracker(token="fixture-secret", transport=wire)
    tracker._activate(PROJECT)
    case.tr = tracker
    case.args['tracker_port'] = tracker
    case.args['issue_id'] = case.issue.id = case.args['authorization']['issue'] = 'LIN-1'
    git(case.root, 'config', 'remote.origin.url', 'https://github.com/acme/widgets.git')
    monkeypatch.setattr(tracker, 'resolve_checkout_project', lambda *a, **k: PROJECT)
    monkeypatch.setattr(tracker, 'validate_mutation_repository', lambda *a, **k: None)
    monkeypatch.setattr(tracker, 'validate_mutation_project', lambda *a, **k: None)
    a = prepare(case)
    error = LinearTrackerError if phase == 'create' else TrackerConflictError
    with pytest.raises(error, match='invalid_response' if phase == 'create' else 'divergent after write'):
        record(case, a, evidence(a, report), report)
    assert len(wire.comments) == 2  # Authorization and terminal only, no compensating stop.
    assert not pm.restituted_reports(tracker, case.issue.id)
    assert pm.PUBLICATION.get() == 'yes'
    diff = json.loads((case.args['output_dir'] / 'publication-readback.json').read_text())
    assert diff['sent'] != diff['read'][-1]
    assert diff['read'][-1].endswith(' altered')
    terminal_writes = [variables['input']['body'] for document, variables in wire.calls
                       if 'FoundryLinearCommentCreate' in document and '"type": "advisory_report"' in variables['input']['body']]
    assert terminal_writes == [diff['sent']]


@pytest.mark.parametrize("input_kind", ["evidence", "report"])
@pytest.mark.parametrize("fault", ["missing", "malformed", "unreadable"])
def test_record_cli_input_failure_keeps_consumption_unknown(case, report, tmp_path, monkeypatch, capsys, input_kind, fault):
    import foundry
    from foundry.routing import main
    a = prepare(case)
    monkeypatch.setattr(foundry, "tracker", lambda **_: case.tr)
    monkeypatch.setattr(foundry, "codehost", lambda **_: case.ch)
    paths = {}
    for kind, value in (("evidence", evidence(a, report)), ("report", report)):
        path = tmp_path / (kind + ".json")
        path.write_text(json.dumps(value))
        paths[kind] = path
    broken = paths[input_kind]
    if fault == "missing":
        broken.unlink()
    elif fault == "malformed":
        broken.write_text("{")
    else:
        load = pm._load
        def refuse(path):
            if Path(path) == broken:
                raise OSError("private input")
            return load(path)
        monkeypatch.setattr(pm, "_load", refuse)
    with pytest.raises(SystemExit) as error:
        main(["postmerge-review", "record", "--root", str(case.root), "--issue", case.issue.id,
              "--attempt-id", a["attempt_id"], "--packet-dir", str(case.args["output_dir"]),
              "--evidence-file", str(paths["evidence"]), "--report-file", str(paths["report"])])
    assert error.value.code == 2
    assert "authorization_consumed=unknown; publication=no" in capsys.readouterr().err
    assert len(case.issue.comments) == 1


@pytest.mark.parametrize("text", ["https://github.com/example/repository/pull/141", "choice / alternative", "docs/page.md"])
def test_public_report_url_and_relative_reference_allowed(case, report, text):
    a = prepare(case)
    report["evidence"][0]["detail"] = text
    assert record(case, a, evidence(a, report), report)["report"] == report


@pytest.mark.parametrize("text", ["file:///Users/x", r"\\server\share"])
def test_public_report_absolute_reference_refused(case, report, text):
    a = prepare(case)
    report["evidence"][0]["detail"] = text
    with pytest.raises(RoutingConfigError, match="absolute local path"):
        record(case, a, evidence(a, report), report)
    assert pm.receipts(case.tr, case.issue.id)[-1]["type"] == "stopped_without_verdict"


@pytest.mark.parametrize("body", ['Historical "quoted" constraint', r"Historical \ constraint", "Historical\tconstraint"])
@pytest.mark.parametrize("snapshot", ["issue", "adr"])
def test_escaped_snapshot_duplication_refused(case, report, body, snapshot):
    item = case.args["history"]["issue"] if snapshot == "issue" else case.args["history"]["adrs"][0]
    item["body"] = "- [ ] " + body if snapshot == "issue" else body
    a = prepare(case)
    report["evidence"][0]["detail"] = "Quoted snapshot: " + item["body"]
    with pytest.raises(RoutingConfigError, match="private prompt or historical snapshot"):
        record(case, a, evidence(a, report), report)
    assert pm.receipts(case.tr, case.issue.id)[-1]["type"] == "stopped_without_verdict"


@pytest.mark.parametrize("fault", ["malformed", "unreadable"])
def test_existing_submission_unreadable_records_controlled_stop(case, report, monkeypatch, fault):
    a = prepare(case)
    path = case.args["output_dir"] / "submission.json"
    path.write_text("{")
    original = path.read_bytes()
    if fault == "unreadable":
        load = pm._load
        def refuse(candidate):
            if Path(candidate) == path:
                raise OSError("private submission")
            return load(candidate)
        monkeypatch.setattr(pm, "_load", refuse)
    with pytest.raises(RoutingConfigError, match="private submission unreadable"):
        record(case, a, evidence(a, report), report)
    assert path.read_bytes() == original
    assert pm.PUBLICATION.get() == "yes"
    assert pm.receipts(case.tr, case.issue.id)[-1]["type"] == "stopped_without_verdict"


def test_unbound_terminal_preserves_previously_confirmed_report(case, report):
    a = prepare(case)
    confirmed = record(case, a, evidence(a, report), report)
    before = copy.deepcopy(case.issue.comments)
    case.issue.comments.append({"text": pm.TERMINAL_PREFIX + "unidentifiable\n{broken"})
    assert pm.restituted_reports(case.tr, case.issue.id) == [confirmed]
    assert case.issue.comments[:-1] == before


@pytest.mark.parametrize("body", ["{broken", "[]", '{"schema":"foundry.postmerge-review.v1","type":"unexpected"}'])
def test_unidentifiable_v1_receipt_fatal_before_consumption_read(case, body, tmp_path):
    prepare(case)
    case.issue.comments.append({"text": pm.PREFIX + body})
    before = copy.deepcopy(case.issue.comments)
    case.args["output_dir"] = tmp_path / "distinct-packet"
    case.args["authorization"]["id"] = "b" * 64
    with pytest.raises(RoutingConfigError, match="malformed advisory receipt"):
        prepare(case)
    assert case.issue.comments == before
    assert not case.args["output_dir"].exists()


@pytest.mark.parametrize("relative", ["tooling/foundry/routing.py", "tooling/foundry/effort_policy.py"])
def test_selected_plugin_policy_divergence_refused_before_consumption(case, tmp_path, monkeypatch, relative):
    import shutil
    source = Path(pm.__file__).parents[2]
    selected = tmp_path / "selected-plugin"
    shutil.copytree(source, selected)
    path = selected / relative
    path.write_bytes(path.read_bytes() + b"\n# divergent fixture\n")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(selected))
    with pytest.raises(RoutingConfigError, match="selected plugin advisory implementation incompatible"):
        prepare(case, "claude")
    assert case.issue.comments == []
    assert pm.PUBLICATION.get() == "no"


def test_prefix_alone_pushes_terminal_above_publication_bound(case):
    receipt = {"schema": pm.TERMINAL_SCHEMA, "type": "advisory_confirmation", "issue": case.issue.id,
               "attempt_id": "a" * 64, "padding": ""}
    size = len(json.dumps(receipt, sort_keys=True, ensure_ascii=False).encode())
    receipt["padding"] = "x" * (pm.MAX_COMMENT_BYTES - size - 1)
    assert len(json.dumps(receipt, sort_keys=True, ensure_ascii=False).encode()) == pm.MAX_COMMENT_BYTES - 1
    assert pm.comment_text(receipt).startswith(pm.TERMINAL_PREFIX)
    with pytest.raises(RoutingConfigError, match="published receipt exceeds qualified bound"):
        pm.append(case.tr, case.issue.id, receipt, case.root)
    assert case.issue.comments == []


def test_actual_claude_hook_main_output_accepts_logical_proof(case, report, monkeypatch, capsys):
    import importlib.util
    import io
    import sys
    a = prepare(case, "claude")
    hook = Path(pm.__file__).parents[2] / "hooks/route_agent.py"
    spec = importlib.util.spec_from_file_location("advisory_actual_hook_fixture", hook)
    facade = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, facade)
    spec.loader.exec_module(facade)
    payload = {"cwd": str(case.root), "tool_name": "Agent", "tool_use_id": "native-call-1",
               "tool_input": copy.deepcopy(a["launch_arguments"])}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    facade.main()
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    context = json.loads(output["additionalContext"].removeprefix("Foundry Claude route: "))
    assert pm.stable_hook_route(context) == pm.stable_hook_route(a["hook_context"])
    e = evidence(a, report)
    e["hook"]["context"] = context
    e["hook"]["source"] = "fixture route_agent.main emitted additionalContext"
    emitted = output["updatedInput"]
    assert emitted["subagent_type"] == "foundry:" + context["transmitted_launch"]["profile"]
    assert emitted == a["arguments"]
    assert emitted != payload["tool_input"]
    assert record(case, a, e, report)["type"] == "advisory_report"


@pytest.mark.parametrize("text", ["--root=/Users/x", "root:/Users/x", "[/Users/x]", "</tmp/x>", ",/home/x", "run /foundry:open-pr", "ratio 3 /4"])
def test_nonexecution_path_strings_publishable(case, report, text, monkeypatch, tmp_path):
    monkeypatch.setenv("TMPDIR", str(tmp_path / "private-temp"))
    a = prepare(case)
    report["evidence"][0]["detail"] = text
    assert record(case, a, evidence(a, report), report)["report"] == report


@pytest.mark.parametrize("kind", ["home", "root", "packet", "tmp", "plugin"])
def test_concrete_execution_paths_private_even_inside_json(case, report, monkeypatch, tmp_path, kind):
    home = tmp_path / "private-home"
    temporary = tmp_path / "private-tmp"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(temporary))
    a = prepare(case)
    paths = {"home": home, "root": case.root, "packet": case.args["output_dir"],
             "tmp": temporary, "plugin": Path(pm.__file__).parents[2]}
    text = json.dumps({"embedded": "--root=" + str(paths[kind].resolve()) + "/child"})
    report["evidence"][0]["detail"] = text
    with pytest.raises(RoutingConfigError, match="absolute local path"):
        record(case, a, evidence(a, report), report)
    assert text not in json.dumps(case.issue.comments)
    assert str(home) not in json.dumps(case.issue.comments)


@pytest.mark.parametrize("altered", [False, True])
def test_new_stop_terminal_isolates_and_allows_distinct_authorization(case, report, tmp_path, altered):
    a = prepare(case)
    with pytest.raises(RoutingConfigError):
        record(case, a, {}, report)
    stop = pm.receipts(case.tr, case.issue.id)[-1]
    assert stop["schema"] == pm.TERMINAL_SCHEMA
    assert case.issue.comments[-1]["text"].startswith(pm.TERMINAL_PREFIX + a["attempt_id"] + "\n")
    if altered:
        case.issue.comments[-1]["text"] = pm.TERMINAL_PREFIX + a["attempt_id"] + "\n{broken"
        assert pm.receipts(case.tr, case.issue.id)[-1]["type"] == "isolated_terminal"
    else:
        assert stop["type"] == "stopped_without_verdict"
    before = copy.deepcopy(case.issue.comments)
    case.args["output_dir"] = tmp_path / "second-packet"
    with pytest.raises(RoutingConfigError, match="authorization already consumed"):
        prepare(case)
    case.args["authorization"]["id"] = "b" * 64
    second = prepare(case)
    assert case.issue.comments[:len(before)] == before
    assert record(case, second, evidence(second, report), report)["type"] == "advisory_report"


def test_record_missing_git_reports_historical_process_failure(case, report, monkeypatch):
    a = prepare(case)
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("private executable path")
    monkeypatch.setattr(pm.subprocess, "run", unavailable)
    with pytest.raises(RoutingConfigError, match="historical Git process unavailable") as error:
        record(case, a, evidence(a, report), report)
    assert "private executable path" not in str(error.value)
    assert pm.receipts(case.tr, case.issue.id)[-1]["type"] == "stopped_without_verdict"


def test_legacy_intact_v1_stop_remains_readable_without_rewrite(case, report, tmp_path):
    a = prepare(case)
    legacy = {"schema": pm.SCHEMA, "type": "stopped_without_verdict", "issue": case.issue.id,
              "attempt_id": a["attempt_id"], "submitted_evidence_hash": pm.digest({})}
    case.issue.comments.append({"text": pm.comment_text(legacy)})
    before = copy.deepcopy(case.issue.comments)
    assert pm.receipts(case.tr, case.issue.id)[-1] == legacy
    case.args["output_dir"] = tmp_path / "new-after-legacy-stop"
    case.args["authorization"]["id"] = "b" * 64
    second = prepare(case)
    assert record(case, second, evidence(second, report), report)["type"] == "advisory_report"
    assert case.issue.comments[:len(before)] == before


def test_private_execution_path_binding_cannot_be_removed(case, report):
    a = prepare(case)
    path = case.args["output_dir"] / "invocation.json"
    altered = json.loads(path.read_text())
    altered["execution_paths"] = []
    path.write_text(json.dumps(altered))
    with pytest.raises(RoutingConfigError, match="private execution paths digest mismatch"):
        record(case, a, evidence(a, report), report)
