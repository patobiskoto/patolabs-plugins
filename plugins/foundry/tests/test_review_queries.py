"""Real Git/CLI fixture for the constrained reviewer query capability."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tomllib

import pytest

from foundry.routing import ReviewDeduplicator, RoutingConfigError, git_diff, git_head, main, review_diff_hash
from foundry.review_queries import query_review

ROOT = Path(__file__).resolve().parents[1]


def hook(name):
    spec = importlib.util.spec_from_file_location(f"review_query_{name}", ROOT / "hooks" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GUARD = hook("guard_routed_readonly")
ROUTE = hook("route_agent")


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root).decode().strip()


@pytest.fixture
def claimed(tmp_path, monkeypatch):
    root, state = tmp_path / "repository", tmp_path / "state"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / "source.txt").write_text("old text\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    base = git_head(root)
    (root / "source.txt").write_text("first line\nverified needle\n")
    (root / "binary.bin").write_bytes(b"\x00needle")
    (root / "outside-link").symlink_to(tmp_path / "outside")
    (tmp_path / "outside").write_text("secret needle\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "change")
    (root / "untracked.txt").write_text("untracked needle\n")
    # Queries must use immutable blobs even when working-file context differs.
    (root / "source.txt").write_text("working needle\n")
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    ledger = ReviewDeduplicator(str(root), state)
    claim = ledger.claim_git(review_diff_hash(git_diff(root, base)),
                             coordinates={"root": str(root), "base": base},
                             claim_attempt_token="d" * 64)
    return root, base, claim, ledger


def command(fixture, verb, text=None):
    root, base, claim, _ = fixture
    args = ["python3", str(ROOT / "tooling/foundry_cli.py"), "routing", verb,
            "--diff-hash", claim.diff_hash, "--claim-id", claim.claim_id,
            "--root", str(root), "--base", base]
    if text is not None:
        args.extend(["--text", text])
    return shlex.join(args)


def test_real_guarded_profile_search_and_list_have_no_repository_or_ledger_mutation(claimed):
    root, _, claim, ledger = claimed
    packet = "Goal:\nVerify text.\nInputs:\nFixture.\nConstraints:\nRead only.\nDone when:\nReport evidence."
    routed, _ = ROUTE.route_tool_input({"subagent_type": "foundry:reviewer", "prompt": packet},
                                     cwd=root, environ={})
    profile = (ROOT / "agents" / (routed["subagent_type"].removeprefix("foundry:") + ".md")).read_text()
    assert "tools: Read, Grep, Glob, Bash" in profile
    assert "Write" not in profile and "Edit" not in profile
    marker = ledger.directory / claim.diff_hash
    before_marker = marker.read_bytes()
    before_files = {p.relative_to(root): p.read_bytes() for p in root.rglob("*")
                    if p.is_file() and not p.is_symlink() and ".git" not in p.parts}
    search = command(claimed, "search-review", "needle")
    listing = command(claimed, "list-review")
    assert GUARD.decision(routed["subagent_type"], search) is None
    assert GUARD.decision(routed["subagent_type"], listing) is None
    # Exactly the accepted argv, no arbitrary shell execution.
    results = [json.loads(subprocess.check_output(shlex.split(c), cwd=root, env=os.environ))
               for c in (search, listing)]
    assert results[0]["matches"] == [{"path": "source.txt", "line": 2, "text": "verified needle"}]
    assert results[0]["head"] == git_head(root)
    assert results[0]["skipped"] == [{"path": "binary.bin", "reason": "binary"}]
    assert results[0]["truncated"] is False
    assert results[1]["paths"] == ["binary.bin", "source.txt"]
    assert marker.read_bytes() == before_marker
    assert {p: (root / p).read_bytes() for p in before_files} == before_files


@pytest.mark.parametrize("suffix", ["; touch sentinel", " && touch sentinel", " > sentinel",
                                    " | tee sentinel", "\n touch sentinel"])
def test_guard_denies_shell_composition(claimed, suffix):
    assert GUARD.decision("foundry:routed-readonly-high", command(claimed, "search-review", "needle") + suffix)
    assert not (claimed[0] / "sentinel").exists()


@pytest.mark.parametrize("forbidden", ["git add .", "git commit -m mutation", "touch sentinel",
    "python3 -c 'print(1)'", "pytest", "curl https://example.invalid", "gh pr diff 1",
    "python3 LAUNCHER edit state DEMO-1 done", "python3 LAUNCHER query issue DEMO-1"])
def test_guard_denies_mutations_tracker_tests_network_and_raw_diff(forbidden):
    assert GUARD.decision("foundry:routed-readonly-high", forbidden.replace("LAUNCHER", str(ROOT / "tooling/foundry_cli.py")))


def test_real_pretool_hook_denies_tracker_and_write_commands(claimed):
    root = claimed[0]
    for forbidden in ("touch sentinel", f"python3 {ROOT}/tooling/foundry_cli.py edit state DEMO-1 done"):
        payload = {"agent_type": "foundry:routed-readonly-high", "tool_input": {"command": forbidden}}
        result = subprocess.check_output(["python3", str(ROOT / "hooks/guard_routed_readonly.py")],
                                         input=json.dumps(payload).encode(), cwd=root)
        assert json.loads(result)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert not (root / "sentinel").exists()


@pytest.mark.parametrize("text", ["$(touch sentinel)", "`touch sentinel`", "needle; touch sentinel",
                                      "needle\n touch sentinel", "", "x" * 513])
def test_guard_denies_unsafe_search_text(claimed, text):
    assert GUARD.decision("foundry:routed-readonly-high", command(claimed, "search-review", text))


def test_query_cli_never_acquires_tracker(claimed, monkeypatch, capsys):
    import foundry
    def forbidden(*_args, **_kwargs):
        pytest.fail("review queries must not acquire tracker credentials")
    monkeypatch.setattr(foundry, "tracker", forbidden)
    main(shlex.split(command(claimed, "search-review", "needle"))[3:])
    assert json.loads(capsys.readouterr().out)["matches"][0]["line"] == 2


@pytest.mark.parametrize("drift", ["head", "claim", "root", "base", "hash"])
def test_queries_reject_stale_or_wrong_coordinates_before_output(claimed, drift, tmp_path):
    root, base, claim, _ = claimed
    values = dict(claim_id=claim.claim_id, root=root, base=base)
    expected_hash = claim.diff_hash
    if drift == "head":
        git(root, "commit", "--allow-empty", "-qm", "head drift")
    elif drift == "claim":
        values["claim_id"] = "a" * 64
    elif drift == "root":
        values["root"] = tmp_path
    elif drift == "base":
        values["base"] = "b" * 40
    else:
        expected_hash = "c" * 64
    output = []
    with pytest.raises(RoutingConfigError):
        query_review(expected_hash, str(root), **values, text="needle", consume=output.append)
    assert output == []


def test_query_rejects_recovered_owner(claimed):
    root, base, claim, ledger = claimed
    ledger.recover(claim.diff_hash, claim.generation, claim.claim_id, "fixture owner stopped",
                   coordinates={"root": str(root), "base": base})
    output = []
    with pytest.raises(RoutingConfigError):
        query_review(claim.diff_hash, str(root), claim_id=claim.claim_id,
                     root=root, base=base, text="needle", consume=output.append)
    assert output == []


def test_codex_compatibility_template_is_parseable_and_honest():
    template = tomllib.loads((ROOT / "examples/codex-reviewer.toml").read_text())
    assert template["name"] == "reviewer"
    assert "sandbox_mode" not in template
    contract = template["developer_instructions"]
    assert "search-review/list-review" in contract
    assert "prompt discipline" in contract
    assert "ledger lock" in contract


def test_search_reports_skips_and_truncation(claimed):
    root, base, _claim, ledger = claimed
    (root / "source.txt").write_text("needle\n" * 1001)
    (root / "oversize.txt").write_bytes(b"needle" + b"x" * 1_048_576)
    (root / "nonutf8.bin").write_bytes(b"needle\xff")
    git(root, "add", ".")
    git(root, "commit", "-qm", "limits")
    claim = ledger.claim_git(review_diff_hash(git_diff(root, base)),
        coordinates={"root": str(root), "base": base}, claim_attempt_token="e" * 64)
    result = query_review(claim.diff_hash, str(root), claim_id=claim.claim_id,
        root=root, base=base, text="needle", consume=lambda result: result)
    assert len(result["matches"]) == 1000 and result["truncated"] is True
    assert {entry["reason"] for entry in result["skipped"]} == {"binary", "non_utf8", "oversize"}


@pytest.mark.parametrize("text", ["", "x" * 513, "a\nb", "a\rb", "a\x00b"])
def test_query_rejects_invalid_text_before_claim_read(text):
    with pytest.raises(RoutingConfigError, match="texte littéral"):
        query_review("a" * 64, "fixture", claim_id="b" * 64,
                     root="/absent", base="c" * 40, text=text, consume=lambda _: None)
