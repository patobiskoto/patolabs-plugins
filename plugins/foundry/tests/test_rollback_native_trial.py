"""PAT-198: the per-line native rollback trial tool. Offline only: no host, no model, no network.

The trial itself is never run here (fake launcher, synthetic logs); no real policy file is read or written."""
import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from foundry import claude_profile_trial as base
from foundry import rollback_native_trial as rt

ROOT = Path(__file__).resolve().parents[1]
QUAL = ROOT / "docs" / "qualification"
WIRE = {"claude-scout": "claude-haiku-4-5", "claude-implementer": "claude-sonnet-5",
        "claude-reviewer": "claude-opus-5", "claude-architect": "claude-fable-5"}
PROFILE = {"claude-scout": "routed-readonly-none-haiku-4.5", "claude-implementer": "routed-worker-medium-sonnet-5",
           "claude-reviewer": "routed-readonly-high-opus-5", "claude-architect": "routed-readonly-high-fable-5"}
CLAUDE_LINES = sorted(WIRE)
_REAL_INSIDE = base._inside_repository


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.setattr(base, "_inside_repository", lambda work: _REAL_INSIDE(work, ceiling=tmp_path))
    shutil.copy(QUAL / "pat-198-native-rollback-2026-10-17.json", tmp_path / "matrix.json")
    (tmp_path / "policy.json").write_text('{"version": 1}\n', encoding="utf-8")
    (tmp_path / "default-policy.json").write_text('{"version": 1, "default": true}\n', encoding="utf-8")
    monkeypatch.setattr(rt, "default_guards", lambda: [tmp_path / "default-policy.json"])
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "data"))  # the reservation ledger never leaves the test


def _args(tmp_path, line, **kw):
    values = dict(line=line, work_dir=str(tmp_path / "work"), out=str(tmp_path / "result.json"),
                  matrix=str(tmp_path / "matrix.json"), claude="claude", parent_model="sonnet",
                  projects_dir=str(tmp_path / "projects"), max_budget_usd=1.5, guard_policy=[str(tmp_path / "policy.json")],
                  dry_run=False)
    values.update(kw)
    return SimpleNamespace(**values)


def _host(tmp_path, line, *, model=None, types=None, efforts=("medium",), exit_code=0, result=None, is_error=False,
          children=True, stderr_text="", mutate=None, calls=None, child_records=None):
    def launch(command, *, cwd, env, stdout, stderr: Path):
        calls is not None and calls.append(list(command))
        session = command[command.index("--session-id") + 1]
        marker = (cwd / "fixture" / "marker.txt").read_text().strip()
        stdout.write_text("\n".join(json.dumps(e) for e in (
            {"type": "system", "subtype": "init", "claude_code_version": "2.1.300"},
            {"type": "result", "subtype": "error" if is_error else "success", "is_error": is_error,
             "result": result if result is not None else marker})))
        stderr.write_text(stderr_text)
        if mutate:
            mutate()
        if children:
            folder = tmp_path / "projects" / "slug" / session / "subagents"
            folder.mkdir(parents=True)
            log = folder / "agent-0.jsonl"
            records = child_records if child_records is not None else [
                {"type": "assistant", "version": "2.1.300", "message": {"id": "m", "model": model or WIRE[line]},
                 **({"effort": e} if e else {})} for e in (efforts or (None,))]
            log.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            log.with_suffix(".meta.json").write_text(json.dumps({"agentType": (types or f"foundry:{PROFILE[line]}")}))
        return {"exit_code": exit_code, "timed_out": False, "seconds": 1.0}
    return launch


def _matrix(tmp_path):
    return json.loads((tmp_path / "matrix.json").read_text())


@pytest.mark.parametrize("line", CLAUDE_LINES)
def test_dry_run_prints_the_plan_and_launches_and_writes_nothing(tmp_path, capsys, line):
    before = (tmp_path / "matrix.json").read_bytes()
    assert rt.run(_args(tmp_path, line, dry_run=True, max_budget_usd=None), launch=_host(tmp_path, line, calls=None)) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["dry_run"] is True and plan["transmitted"]["model"] == WIRE[line] and plan["transmitted"]["profile"] == PROFILE[line]
    assert plan["command"][-2:] == ["--max-budget-usd", "<required at run>"]
    assert plan["requested"]["model"] == rt.ROLLBACK[("claude", line.split("-")[1])][0]
    assert not (tmp_path / "work").exists() and not (tmp_path / "result.json").exists()
    assert (tmp_path / "matrix.json").read_bytes() == before


@pytest.mark.parametrize("role", ["scout", "implementer", "reviewer", "architect"])
def test_codex_lines_have_a_dry_run_plan_and_no_real_run(tmp_path, capsys, role):
    line = f"codex-{role}"
    assert rt.run(_args(tmp_path, line, dry_run=True)) == 0
    plan = json.loads(capsys.readouterr().out)
    model, effort = rt.ROLLBACK[("codex", role)]
    assert plan["command"][plan["command"].index("--model") + 1] == model
    assert f'model_reasoning_effort="{effort}"' in plan["command"]
    with pytest.raises(rt.RollbackTrialError):
        rt.run(_args(tmp_path, line))
    assert _matrix(tmp_path)["native_calls"] == 0


def test_a_run_records_the_four_separate_values_and_claims_its_row(tmp_path):
    calls = []
    assert rt.run(_args(tmp_path, "claude-implementer"), launch=_host(tmp_path, "claude-implementer", calls=calls)) == 0
    assert len(calls) == 1 and calls[0][-2:] == ["--max-budget-usd", "1.5"]
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["verdict"] == "conforming" and body["requested"]["model"] == "sonnet-5"
    assert body["transmitted"]["model"] == "claude-sonnet-5" and body["observed"]["models"] == ["claude-sonnet-5"]
    assert body["account_availability"] == "available_observed"
    matrix = _matrix(tmp_path)
    row = next(r for r in matrix["rows"] if r["line"] == "claude-implementer")
    assert matrix["native_calls"] == 1 and row["status"] == "run" and row["verdict"] == "conforming"
    others = [r for r in matrix["rows"] if r["line"] != "claude-implementer"]
    assert all(r["status"] == "not_run" and r["verdict"] == "not_run" for r in others)


def test_a_line_already_recorded_is_never_run_again_even_with_new_paths(tmp_path):
    rt.run(_args(tmp_path, "claude-reviewer"), launch=_host(tmp_path, "claude-reviewer"))
    calls = []
    again = _args(tmp_path, "claude-reviewer", work_dir=str(tmp_path / "w2"), out=str(tmp_path / "r2.json"))
    with pytest.raises(rt.RollbackTrialError, match="no retry"):
        rt.run(again, launch=_host(tmp_path, "claude-reviewer", calls=calls))
    assert calls == [] and not (tmp_path / "w2").exists()


def test_a_failed_run_still_blocks_a_retry(tmp_path):
    rt.run(_args(tmp_path, "claude-architect"), launch=_host(tmp_path, "claude-architect", exit_code=1, children=False))
    assert next(r for r in _matrix(tmp_path)["rows"] if r["line"] == "claude-architect")["status"] == "run"
    with pytest.raises(rt.RollbackTrialError):
        rt.run(_args(tmp_path, "claude-architect", work_dir=str(tmp_path / "w2"), out=str(tmp_path / "r2.json")))


def test_a_real_run_needs_a_budget_and_stops_at_eight_calls(tmp_path):
    with pytest.raises(rt.RollbackTrialError, match="max-budget"):
        rt.run(_args(tmp_path, "claude-scout", max_budget_usd=None))
    matrix = _matrix(tmp_path)
    matrix["native_calls"] = 8
    (tmp_path / "matrix.json").write_text(json.dumps(matrix))
    with pytest.raises(rt.RollbackTrialError, match="ceiling"):
        rt.run(_args(tmp_path, "claude-scout"))
    assert not (tmp_path / "work").exists()


def test_the_real_policy_is_untouched_byte_for_byte_and_a_change_is_not_conforming(tmp_path):
    policy = tmp_path / "policy.json"
    before = policy.read_bytes()
    rt.run(_args(tmp_path, "claude-scout", efforts=None), launch=_host(tmp_path, "claude-scout", efforts=None))
    body = json.loads((tmp_path / "result.json").read_text())
    assert policy.read_bytes() == before and body["policy_guard"]["unchanged"] is True
    # a run that (wrongly) alters it is flagged, never conforming
    rt.run(_args(tmp_path, "claude-implementer", work_dir=str(tmp_path / "w2"), out=str(tmp_path / "r2.json")),
           launch=_host(tmp_path, "claude-implementer", mutate=lambda: policy.write_text("{}")))
    body = json.loads((tmp_path / "r2.json").read_text())
    assert body["verdict"] == "not_conforming" and "policy_guard" in body["not_established"]
    assert next(r for r in _matrix(tmp_path)["rows"] if r["line"] == "claude-implementer")["policy_guard"] == "CHANGED"


def test_an_absent_policy_stays_absent(tmp_path):
    absent = tmp_path / "none.json"
    rt.run(_args(tmp_path, "claude-implementer", guard_policy=[str(absent)]), launch=_host(tmp_path, "claude-implementer"))
    assert not absent.exists()
    assert json.loads((tmp_path / "result.json").read_text())["policy_guard"]["real_policy_digests_after"] == {
        "guard-1:default-policy.json": rt.policy_digest(tmp_path / "default-policy.json"), "guard-2:none.json": "absent"}


def test_an_unavailable_model_is_declared_verbatim_masked_and_never_substituted(tmp_path):
    secret = "sk-" + "a1b2c3d4e5f6a7b8c9"
    text = f"There was an issue with the model claude-opus-5: model not found (key {secret})"
    code = rt.run(_args(tmp_path, "claude-reviewer"),
                  launch=_host(tmp_path, "claude-reviewer", children=False, exit_code=1, is_error=True, result=text))
    body = json.loads((tmp_path / "result.json").read_text())
    assert code == 1 and body["verdict"] == "unavailable" and body["account_availability"] == "unavailable_declared"
    assert secret not in json.dumps(body) and "model not found" in body["errors"][0]
    assert "model not found" in body["parent_result"]["reply"]
    row = next(r for r in _matrix(tmp_path)["rows"] if r["line"] == "claude-reviewer")
    assert row["account_availability"] == "unavailable_declared" and row["native_observation"]["models"] == "unknown"


def test_a_substituted_model_is_not_conforming(tmp_path):
    rt.run(_args(tmp_path, "claude-architect"), launch=_host(tmp_path, "claude-architect", model="claude-opus-5-5"))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["verdict"] == "not_conforming" and "model" in body["not_established"]


def test_what_the_host_does_not_log_stays_unknown_never_conforming(tmp_path):
    rt.run(_args(tmp_path, "claude-implementer"), launch=_host(tmp_path, "claude-implementer", efforts=None))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["conformity"]["effort"] == "unknown" and body["verdict"] == "unknown"
    row = next(r for r in _matrix(tmp_path)["rows"] if r["line"] == "claude-implementer")
    assert row["native_observation"]["efforts"] == "unknown"


def test_masking_removes_secrets_home_and_opaque_tokens_and_bounds_the_length():
    home = "/home/someone"
    text = (f"open {home}/x sk-abcdefghijklmnop Bearer abcdefghijkl1234 API_TOKEN=hunter22 a.b@example.org "
            + "A" * 40 + " " + "z" * 5000)
    out = rt.mask(text, home=home)
    for leaked in (home, "sk-abcdefghij", "abcdefghijkl1234", "hunter22", "a.b@example.org", "A" * 40):
        assert leaked not in out
    assert out.startswith("open ~/x") and len(out) <= 2000


def test_the_committed_matrix_has_the_eight_pat17_lines_and_every_field_unknown_or_not_run():
    matrix = json.loads((QUAL / "pat-198-native-rollback-2026-10-17.json").read_text())
    pat17 = json.loads((QUAL / "pat-17-config-rollback-2026-10-11.json").read_text())
    expected = {(r["host"], r["role"]): (r["rollback"]["model"], r["rollback"]["effort"]) for r in pat17["rows"]}
    assert expected == rt.ROLLBACK and len(matrix["rows"]) == 8 and matrix["native_calls"] == 0
    for row in matrix["rows"]:
        assert (row["status"], row["verdict"], row["account_availability"]) == ("not_run", "not_run", "unknown")
        assert row["resolved_route"] == row["transmitted"] == row["errors"] == "unknown"
        assert set(row["native_observation"].values()) == {"unknown"}
    assert matrix == rt.empty_matrix()
    text = json.dumps(matrix)
    assert not re.search(r"/Users/|/home/|@", text)


def test_a_guard_policy_adds_to_the_default_guard_and_never_replaces_it(tmp_path):
    assert [g.name for g in rt.guard_list([str(tmp_path / "policy.json")])] == ["default-policy.json", "policy.json"]
    assert [g.name for g in rt.guard_list(None)] == ["default-policy.json"]
    assert [g.name for g in rt.guard_list([str(tmp_path / "default-policy.json")])] == ["default-policy.json"]
    default = tmp_path / "default-policy.json"
    rt.run(_args(tmp_path, "claude-implementer"), launch=_host(tmp_path, "claude-implementer",
                                                               mutate=lambda: default.write_text("{}")))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["verdict"] == "not_conforming" and body["policy_guard"]["unchanged"] is False


def test_the_codex_plan_carries_the_flags_of_the_measurement_harness():
    command = rt.codex_plan("scout", Path("/w"))["command"]
    assert command[:7] == ["codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only"]


def test_a_lock_file_refuses_a_second_launch_and_is_released_after_a_run(tmp_path):
    lock = tmp_path / "matrix.json.lock"
    lock.write_text("")
    calls = []
    with pytest.raises(rt.RollbackTrialError, match="lock"):
        rt.run(_args(tmp_path, "claude-scout"), launch=_host(tmp_path, "claude-scout", calls=calls))
    assert calls == [] and lock.exists() and not (tmp_path / "work").exists() and _matrix(tmp_path)["native_calls"] == 0
    lock.unlink()
    rt.run(_args(tmp_path, "claude-implementer"), launch=_host(tmp_path, "claude-implementer"))
    assert not lock.exists()


def test_the_lock_is_released_when_a_run_is_refused_after_taking_it(tmp_path):
    with pytest.raises(rt.RollbackTrialError, match="max-budget"):
        rt.run(_args(tmp_path, "claude-scout", max_budget_usd=None))
    assert not (tmp_path / "matrix.json.lock").exists()


def test_a_dry_run_takes_no_lock_and_no_reservation(tmp_path):
    rt.run(_args(tmp_path, "claude-scout", dry_run=True))
    assert not (tmp_path / "matrix.json.lock").exists() and not rt.ledger_path().exists()


def test_the_ledger_outside_the_checkout_blocks_a_line_even_if_the_matrix_is_reverted(tmp_path):
    ledger = rt.ledger_path()
    assert not ledger.is_relative_to(ROOT)
    rt.run(_args(tmp_path, "claude-reviewer"), launch=_host(tmp_path, "claude-reviewer"))
    assert [json.loads(x)["line"] for x in ledger.read_text().splitlines()] == ["claude-reviewer"]
    shutil.copy(QUAL / "pat-198-native-rollback-2026-10-17.json", tmp_path / "matrix.json")  # git revert / stash / other worktree
    calls = []
    with pytest.raises(rt.RollbackTrialError, match="ledger"):
        rt.run(_args(tmp_path, "claude-reviewer", work_dir=str(tmp_path / "w2"), out=str(tmp_path / "r2.json")),
               launch=_host(tmp_path, "claude-reviewer", calls=calls))
    assert calls == [] and not (tmp_path / "w2").exists()
    # a matrix copy elsewhere is blocked the same way, and a different line is still free
    other = tmp_path / "copy.json"
    shutil.copy(QUAL / "pat-198-native-rollback-2026-10-17.json", other)
    with pytest.raises(rt.RollbackTrialError, match="ledger"):
        rt.run(_args(tmp_path, "claude-reviewer", matrix=str(other), work_dir=str(tmp_path / "w3"),
                     out=str(tmp_path / "r3.json")), launch=_host(tmp_path, "claude-reviewer", calls=calls))
    assert calls == []
    rt.run(_args(tmp_path, "claude-scout", work_dir=str(tmp_path / "w4"), out=str(tmp_path / "r4.json")),
           launch=_host(tmp_path, "claude-scout", efforts=None))


def test_the_ledger_ceiling_and_an_unreadable_ledger_refuse_before_any_launch(tmp_path):
    ledger = rt.ledger_path()
    ledger.parent.mkdir(parents=True)
    ledger.write_text("".join(json.dumps({"line": f"x{n}"}) + "\n" for n in range(8)))
    calls = []
    with pytest.raises(rt.RollbackTrialError, match="ceiling"):
        rt.run(_args(tmp_path, "claude-scout"), launch=_host(tmp_path, "claude-scout", calls=calls))
    ledger.write_text("not json\n")
    with pytest.raises(rt.RollbackTrialError, match="unreadable"):
        rt.run(_args(tmp_path, "claude-scout"), launch=_host(tmp_path, "claude-scout", calls=calls))
    assert calls == [] and not (tmp_path / "work").exists()


def test_the_source_is_taken_before_the_lock_and_the_claim(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(base, "_head", lambda: seen.append((tmp_path / "matrix.json.lock").exists()) or {"commit": "c", "dirty": False})
    rt.run(_args(tmp_path, "claude-implementer"), launch=_host(tmp_path, "claude-implementer"))
    assert seen == [False]


def _synthetic(text):
    return {"type": "assistant", "isApiErrorMessage": True,
            "message": {"model": "<synthetic>", "content": [{"type": "text", "text": text}]}}


def test_a_child_api_error_relayed_by_a_successful_parent_is_unavailable_declared_with_the_text_kept(tmp_path):
    text = "There's an issue with the selected model (claude-opus-5). It may not exist or you may not have access: model not found"
    code = rt.run(_args(tmp_path, "claude-reviewer"),
                  launch=_host(tmp_path, "claude-reviewer", result=text, is_error=False, child_records=[_synthetic(text)]))
    body = json.loads((tmp_path / "result.json").read_text())
    assert code == 1 and body["verdict"] == "unavailable" and body["account_availability"] == "unavailable_declared"
    assert body["observed"]["children"] == 1 and body["observed"]["models"] == []
    assert body["conformity"]["model"] == "unknown" and "model not found" in body["errors"][0]
    assert "model not found" in body["parent_result"]["reply"]
    row = next(r for r in _matrix(tmp_path)["rows"] if r["line"] == "claude-reviewer")
    assert "model not found" in row["parent_reply"] and row["fixture_compliance"] == "divergent"
    assert row["native_observation"]["models"] == "unknown"


def test_a_synthetic_record_is_never_read_as_a_divergent_model(tmp_path):
    records = [_synthetic("rate limited, try later"),
               {"type": "assistant", "version": "2.1.300", "message": {"model": "claude-opus-5"}, "effort": "high"}]
    rt.run(_args(tmp_path, "claude-reviewer"), launch=_host(tmp_path, "claude-reviewer", child_records=records, efforts=("high",)))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["observed"]["models"] == ["claude-opus-5"] and body["conformity"]["model"] == "exact"
    assert body["account_availability"] == "available_observed" and body["errors"] == ["rate limited, try later"]


def test_a_real_model_with_an_unrelated_error_text_is_never_unavailable(tmp_path):
    rt.run(_args(tmp_path, "claude-implementer"),
           launch=_host(tmp_path, "claude-implementer", stderr_text="model not found in cache"))
    assert json.loads((tmp_path / "result.json").read_text())["account_availability"] == "available_observed"


def test_the_stderr_branch_declares_unavailable_whatever_the_children(tmp_path):
    rt.run(_args(tmp_path, "claude-architect"),
           launch=_host(tmp_path, "claude-architect", exit_code=1, model="<synthetic>", stderr_text="the model is unavailable"))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["account_availability"] == "unavailable_declared"


def test_a_refused_fixture_does_not_decide_the_model_verdict(tmp_path):
    rt.run(_args(tmp_path, "claude-implementer"),
           launch=_host(tmp_path, "claude-implementer", result="STOP: no diff_hash"))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["fixture_compliance"] == "divergent" and "fixture" not in body["conformity"]
    assert body["verdict"] == "conforming"
