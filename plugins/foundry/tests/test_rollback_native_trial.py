"""PAT-198: the per-line native rollback trial tool. Offline only: no host, no model, no network.

The trial itself is never run here (fake launcher, synthetic logs); no real policy file is read or written."""
import json
import re
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
    # A fresh matrix, never the committed one: its Claude rows are recorded runs, which the tool refuses to repeat.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.setattr(base, "_inside_repository", lambda work: _REAL_INSIDE(work, ceiling=tmp_path))
    _fresh_matrix(tmp_path / "matrix.json")
    (tmp_path / "policy.json").write_text('{"version": 1}\n', encoding="utf-8")
    (tmp_path / "default-policy.json").write_text('{"version": 1, "default": true}\n', encoding="utf-8")
    monkeypatch.setattr(rt, "default_guards", lambda: [tmp_path / "default-policy.json"])
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "data"))  # the reservation ledger never leaves the test
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))  # the real Codex home is never read


def _fresh_matrix(path):
    path.write_text(json.dumps(rt.empty_matrix(), indent=2) + "\n", encoding="utf-8")


def _args(tmp_path, line, **kw):
    values = dict(line=line, work_dir=str(tmp_path / "work"), out=str(tmp_path / "result.json"),
                  matrix=str(tmp_path / "matrix.json"), claude="claude", parent_model="sonnet",
                  projects_dir=str(tmp_path / "projects"), codex="codex", codex_sessions_dir=str(tmp_path / "sessions"),
                  max_budget_usd=1.5, guard_policy=[str(tmp_path / "policy.json")],
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
def test_codex_dry_run_prints_the_plan_and_launches_and_writes_nothing(tmp_path, capsys, role):
    line = f"codex-{role}"
    before = (tmp_path / "matrix.json").read_bytes()
    assert rt.run(_args(tmp_path, line, dry_run=True), codex_launch=_codex_host(tmp_path, calls=None)) == 0
    plan = json.loads(capsys.readouterr().out)
    model, effort = rt.ROLLBACK[("codex", role)]
    assert plan["command"][plan["command"].index("--model") + 1] == model
    assert f'model_reasoning_effort="{effort}"' in plan["command"] and plan["command"][-1] == "-"
    assert "--ephemeral" not in plan["command"] and plan["dry_run"] is True and plan["bounds"]["max_budget_usd"] is None
    assert plan["requested"]["model"] == model and plan["transmitted"]["effort"] == effort
    assert plan["would_write"][-1] == str(tmp_path / "work.backup" / "config.toml")
    assert not (tmp_path / "work").exists() and not (tmp_path / "data").exists()
    assert not (tmp_path / "work.backup").exists() and (tmp_path / "matrix.json").read_bytes() == before


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


RECORDED = {"claude-scout": ("2026-10-17", "unknown", ["claude-haiku-4-5-20251001"], "unknown"),
            "claude-implementer": ("2026-10-17", "conforming", ["claude-sonnet-5"], ["medium"]),
            "claude-reviewer": ("2026-10-17", "conforming", ["claude-opus-5"], ["high"]),
            "claude-architect": ("2026-10-17", "conforming", ["claude-fable-5"], ["high"]),
            "codex-scout": ("2026-10-18", "conforming", ["gpt-5.6-luna"], ["low"]),
            "codex-implementer": ("2026-10-18", "conforming", ["gpt-5.6-terra"], ["medium"]),
            "codex-reviewer": ("2026-10-18", "conforming", ["gpt-5.6-sol"], ["high"]),
            "codex-architect": ("2026-10-18", "conforming", ["gpt-5.6-sol"], ["max"])}


def test_the_committed_matrix_records_the_eight_runs_four_claude_and_four_codex():
    matrix = json.loads((QUAL / "pat-198-native-rollback-2026-10-17.json").read_text())
    pat17 = json.loads((QUAL / "pat-17-config-rollback-2026-10-11.json").read_text())
    expected = {(r["host"], r["role"]): (r["rollback"]["model"], r["rollback"]["effort"]) for r in pat17["rows"]}
    assert expected == rt.ROLLBACK and len(matrix["rows"]) == 8 and matrix["native_calls"] == 8
    assert {(r["host"], r["role"]): (r["rollback"]["model"], r["rollback"]["effort"]) for r in matrix["rows"]} == rt.ROLLBACK
    assert {row["line"] for row in matrix["rows"]} == set(RECORDED)
    for row in matrix["rows"]:
        date, verdict, models, efforts = RECORDED[row["line"]]
        assert (row["status"], row["date"], row["verdict"]) == ("run", date, verdict)
        assert row["native_observation"]["models"] == models and row["native_observation"]["efforts"] == efforts
        assert (row["account_availability"], row["policy_guard"], row["fixture_compliance"], row["errors"]) == (
            "available_observed", "unchanged", "exact", [])
        if row["host"] == "codex":
            assert row["native_observation"]["agent_types"] == "not_applicable"
            assert row["native_observation"]["host_version"] == "0.162.1"
            assert (row["transmitted"]["model"], row["transmitted"]["effort"]) == (
                row["rollback"]["model"], row["rollback"]["effort"])
            assert not any("not run" in item or "not_run" in item for item in row["limits"])
            continue
        assert row["native_observation"]["agent_types"] == [f"foundry:{PROFILE[row['line']]}"]
        assert row["native_observation"]["host_version"] == "2.1.302"
        assert row["transmitted"]["profile"] == PROFILE[row["line"]] and row["transmitted"]["model"] == WIRE[row["line"]]
    text = json.dumps(matrix, ensure_ascii=False)
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


def test_the_codex_command_is_the_measurement_harness_argv_minus_ephemeral_plus_cwd_flags():
    command = rt.codex_command("codex", Path("/w"), "m", "low")
    harness = ["codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only", "--json",
               "--model", "m", "-c", 'model_reasoning_effort="low"', "-"]  # measurement_harness_v2.codex_command_v2
    assert [x for x in command if x not in ("--skip-git-repo-check", "-C", "/w")] == [x for x in harness if x != "--ephemeral"]
    assert command[command.index("--skip-git-repo-check") + 1:command.index("--skip-git-repo-check") + 3] == ["-C", "/w"]


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
    _fresh_matrix(tmp_path / "matrix.json")  # git revert / stash / other worktree
    calls = []
    with pytest.raises(rt.RollbackTrialError, match="ledger"):
        rt.run(_args(tmp_path, "claude-reviewer", work_dir=str(tmp_path / "w2"), out=str(tmp_path / "r2.json")),
               launch=_host(tmp_path, "claude-reviewer", calls=calls))
    assert calls == [] and not (tmp_path / "w2").exists()
    # a matrix copy elsewhere is blocked the same way, and a different line is still free
    other = tmp_path / "copy.json"
    _fresh_matrix(other)
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
    # the journalled effort (medium, the helper's default) is not the line's high: divergent, so the verdict stays
    # not_conforming whatever the availability text says (PAT-244: discriminates `set(failed) <= {"process"}`)
    assert body["account_availability"] == "unavailable_declared"
    assert body["conformity"]["effort"] == "divergent" and body["verdict"] == "not_conforming"


def test_a_refused_fixture_does_not_decide_the_model_verdict(tmp_path):
    rt.run(_args(tmp_path, "claude-implementer"),
           launch=_host(tmp_path, "claude-implementer", result="STOP: no diff_hash"))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["fixture_compliance"] == "divergent" and "fixture" not in body["conformity"]
    assert body["verdict"] == "conforming"


def test_the_result_records_the_anonymised_ledger_location_and_the_data_dir_source(tmp_path, monkeypatch):
    rt.run(_args(tmp_path, "claude-implementer"), launch=_host(tmp_path, "claude-implementer"))
    body = json.loads((tmp_path / "result.json").read_text())
    ledger = body["ledger"]
    assert ledger["path"] == f"<data_dir>/{rt.LEDGER_NAME}" and ledger["data_dir_from"] == "FOUNDRY_DATA"
    assert re.fullmatch(r"[0-9a-f]{12}", ledger["data_dir_fingerprint"])
    assert str(tmp_path) not in json.dumps(ledger)
    monkeypatch.delenv("FOUNDRY_DATA")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    rt.run(_args(tmp_path, "claude-reviewer", work_dir=str(tmp_path / "w2"), out=str(tmp_path / "r2.json"),
                 dry_run=True))
    assert not (tmp_path / "home").exists()  # a dry run reserves and creates nothing, even by default


def test_a_second_concurrent_launch_on_another_matrix_copy_is_refused_by_the_ledger_lock(tmp_path):
    other = tmp_path / "copy.json"
    _fresh_matrix(other)
    inner, calls = [], []

    def launch(command, **kw):
        calls.append(command)
        try:
            rt.run(_args(tmp_path, "claude-reviewer", matrix=str(other), work_dir=str(tmp_path / "w2"),
                         out=str(tmp_path / "r2.json")), launch=_host(tmp_path, "claude-reviewer", calls=inner))
        except rt.RollbackTrialError as exc:
            inner.append(str(exc))
        return _host(tmp_path, "claude-implementer")(command, **kw)

    rt.run(_args(tmp_path, "claude-implementer"), launch=launch)
    assert len(calls) == 1 and len(inner) == 1 and "ledger.jsonl.lock" in inner[0]
    assert not (tmp_path / "w2").exists() and not (tmp_path / "copy.json.lock").exists()
    assert json.loads(other.read_text())["native_calls"] == 0
    ledger = rt.ledger_path()
    assert not ledger.with_name(ledger.name + ".lock").exists() and not (tmp_path / "matrix.json.lock").exists()


def test_the_ledger_lock_is_refused_when_present_and_the_matrix_lock_is_released(tmp_path):
    ledger = rt.ledger_path()
    ledger.parent.mkdir(parents=True)
    stale = ledger.with_name(ledger.name + ".lock")
    stale.write_text("")
    calls = []
    with pytest.raises(rt.RollbackTrialError, match="ledger.jsonl.lock"):
        rt.run(_args(tmp_path, "claude-scout"), launch=_host(tmp_path, "claude-scout", calls=calls))
    assert calls == [] and stale.exists() and not (tmp_path / "matrix.json.lock").exists()


@pytest.mark.parametrize("text", ["Agent type 'foundry:routed-worker-medium-sonnet-5' not found. Available agents: foundry:eiffel",
                                  "PreToolUse hook denied this call: no access to the plugin directory",
                                  "the profile file does not exist"])
def test_a_hook_refusal_or_missing_agent_type_is_not_an_unavailable_model(tmp_path, text):
    rt.run(_args(tmp_path, "claude-implementer"),
           launch=_host(tmp_path, "claude-implementer", children=False, result=text, is_error=True, exit_code=1))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["account_availability"] == "unknown" and body["verdict"] != "unavailable"


@pytest.mark.parametrize("text", ["the model claude-opus-5 does not exist", "you have no access to this model"])
def test_a_model_anchored_does_not_exist_or_no_access_is_still_unavailable(tmp_path, text):
    rt.run(_args(tmp_path, "claude-reviewer"),
           launch=_host(tmp_path, "claude-reviewer", children=False, result=text, is_error=True, exit_code=1))
    assert json.loads((tmp_path / "result.json").read_text())["verdict"] == "unavailable"


def test_a_divergent_profile_with_an_unavailable_text_is_not_conforming(tmp_path):
    text = "model not found"
    rt.run(_args(tmp_path, "claude-implementer"),
           launch=_host(tmp_path, "claude-implementer", result=text, is_error=True, exit_code=1,
                        types="foundry:routed-worker-high-sonnet-5", child_records=[_synthetic(text)]))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["conformity"]["profile"] == "divergent" and body["verdict"] == "not_conforming"
    assert "profile" in body["not_established"]
    assert body["account_availability"] == "unknown"


def test_several_children_with_an_unavailable_text_are_not_conforming(tmp_path):
    # Two children also make the profile divergent (one agent type per child), so this locks the outcome; the
    # `set(failed) <= {"process"}` rule is what keeps `unavailable` off any other divergence.
    text = "model not found"
    inner = _host(tmp_path, "claude-implementer", result=text, is_error=True, exit_code=1,
                  child_records=[_synthetic(text)])

    def launch(command, **kwargs):
        outcome = inner(command, **kwargs)
        for folder in (tmp_path / "projects").glob("*/*/subagents"):
            (folder / "agent-1.jsonl").write_text((folder / "agent-0.jsonl").read_text())
            (folder / "agent-1.meta.json").write_text((folder / "agent-0.meta.json").read_text())
        return outcome

    rt.run(_args(tmp_path, "claude-implementer"), launch=launch)
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["observed"]["children"] == 2 and body["verdict"] == "not_conforming"
    assert "children" in body["not_established"] and body["account_availability"] == "unknown"


def test_a_logged_null_effort_on_haiku_is_an_absence(tmp_path):
    null = [{"type": "assistant", "version": "2.1.300", "message": {"model": "claude-haiku-4-5"}, "effort": None}]
    rt.run(_args(tmp_path, "claude-scout"), launch=_host(tmp_path, "claude-scout", child_records=null))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["conformity"]["effort"] == "unknown" and body["observed"]["efforts"] == [] and body["verdict"] == "unknown"


def test_a_real_effort_logged_on_haiku_stays_divergent(tmp_path):
    rt.run(_args(tmp_path, "claude-scout"), launch=_host(tmp_path, "claude-scout", efforts=("medium",)))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["conformity"]["effort"] == "divergent" and body["verdict"] == "not_conforming"


# --- Codex lines (PAT-245) ---------------------------------------------------------------------------------------
CODEX_LINES = ["codex-scout", "codex-implementer", "codex-reviewer", "codex-architect"]
THREAD = "019d0000-aaaa-bbbb-cccc-000000000001"


def _codex_host(tmp_path, *, model=None, effort=None, completed=True, exit_code=0, stderr_text="", stream_errors=(),
                session=True, timed_out=False, calls=None, reply=None, thread=THREAD):
    """A fake ``codex exec --json``: a stream and, as the host does, a session file under the sessions directory."""
    def launch(command, *, cwd, env, stdout, stderr: Path, stdin: Path):
        calls is not None and calls.append((list(command), dict(env)))
        marker = (cwd / "fixture" / "marker.txt").read_text().strip()
        wanted_model = command[command.index("--model") + 1]
        wanted_effort = command[command.index("-c") + 1].split('"')[1]
        events = [{"type": "thread.started", "thread_id": thread}, {"type": "turn.started"}]
        events += [{"type": "error", "message": m} for m in stream_errors]
        if completed:
            events += [{"type": "item.completed", "item": {"id": "i", "type": "agent_message",
                                                          "text": reply if reply is not None else marker}},
                       {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2, "note": "x"}}]
        else:
            events += [{"type": "turn.failed", "error": {"message": "x"}}] if stream_errors else []
        stdout.write_text("\n".join(json.dumps(e) for e in events) + "\n")
        stderr.write_text(stderr_text)
        if session:
            folder = tmp_path / "sessions" / "2026" / "10" / "18"
            folder.mkdir(parents=True, exist_ok=True)
            records = [{"type": "session_meta", "payload": {"cli_version": "0.162.1", "id": thread}},
                       {"type": "turn_context", "payload": {"model": model or wanted_model, "effort": effort or wanted_effort}}]
            (folder / f"rollout-2026-10-18T00-00-00-{thread}.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return {"exit_code": exit_code, "timed_out": timed_out, "seconds": 1.0}
    return launch


def _codex_row(tmp_path, line):
    return next(r for r in _matrix(tmp_path)["rows"] if r["line"] == line)


@pytest.mark.parametrize("line", CODEX_LINES)
def test_a_conforming_codex_run_records_the_values_and_claims_the_row(tmp_path, line):
    calls = []
    assert rt.run(_args(tmp_path, line), codex_launch=_codex_host(tmp_path, calls=calls)) == 0
    assert len(calls) == 1
    command, env = calls[0]
    model, effort = rt.ROLLBACK[("codex", line.split("-")[1])]
    assert command[:2] == ["codex", "exec"] and "--ignore-user-config" in command and "--ignore-rules" in command
    assert command[command.index("--sandbox") + 1] == "read-only" and "--ephemeral" not in command
    assert set(env) <= set(rt._claude_child_environment()) | {"CODEX_HOME"} and "OPENAI_API_KEY" not in env
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["verdict"] == "conforming" and body["observed"]["models"] == [model] and body["observed"]["efforts"] == [effort]
    assert body["observed"]["usage"] == {"input_tokens": 10, "output_tokens": 2} and body["host"]["codex_cli_version"] == "0.162.1"
    assert body["account_availability"] == "available_observed" and body["fixture_compliance"] == "exact"
    assert THREAD not in json.dumps(body) and str(tmp_path) not in json.dumps(body)
    row = _codex_row(tmp_path, line)
    assert row["status"] == "run" and row["verdict"] == "conforming" and _matrix(tmp_path)["native_calls"] == 1
    assert row["native_observation"]["models"] == [model] and row["transmitted"]["effort"] == effort
    assert (tmp_path / "data" / rt.LEDGER_NAME).read_text().count(line) == 1
    assert not list((tmp_path / "data").glob("*.lock")) and not (tmp_path / "matrix.json.lock").exists()


def test_a_second_codex_run_of_the_same_line_is_refused_with_new_paths(tmp_path):
    rt.run(_args(tmp_path, "codex-scout"), codex_launch=_codex_host(tmp_path))
    calls = []
    again = _args(tmp_path, "codex-scout", work_dir=str(tmp_path / "w2"), out=str(tmp_path / "r2.json"))
    with pytest.raises(rt.RollbackTrialError, match="no retry"):
        rt.run(again, codex_launch=_codex_host(tmp_path, calls=calls))
    (tmp_path / "matrix.json").unlink()
    _fresh_matrix(tmp_path / "matrix.json")  # a reverted matrix: the ledger still refuses
    with pytest.raises(rt.RollbackTrialError, match="ledger"):
        rt.run(again, codex_launch=_codex_host(tmp_path, calls=calls))
    assert calls == [] and not (tmp_path / "w2").exists()


def test_the_ledger_is_shared_with_the_claude_lines_and_caps_the_total(tmp_path):
    rt.run(_args(tmp_path, "claude-scout"), launch=_host(tmp_path, "claude-scout"))
    ledger = tmp_path / "data" / rt.LEDGER_NAME
    ledger.write_text(ledger.read_text() + "".join(json.dumps({"line": f"x{n}"}) + "\n" for n in range(7)))
    calls = []
    with pytest.raises(rt.RollbackTrialError, match="eight"):
        rt.run(_args(tmp_path, "codex-architect", work_dir=str(tmp_path / "w3"), out=str(tmp_path / "r3.json")), codex_launch=_codex_host(tmp_path, calls=calls))
    assert calls == [] and _codex_row(tmp_path, "codex-architect")["status"] == "not_run"


def test_a_codex_lock_refuses_before_any_launch(tmp_path):
    (tmp_path / "matrix.json.lock").write_text("")
    calls = []
    with pytest.raises(rt.RollbackTrialError, match="lock"):
        rt.run(_args(tmp_path, "codex-scout"), codex_launch=_codex_host(tmp_path, calls=calls))
    assert calls == [] and not (tmp_path / "work").exists()


def test_a_divergent_codex_model_is_not_conforming(tmp_path):
    assert rt.run(_args(tmp_path, "codex-reviewer"), codex_launch=_codex_host(tmp_path, model="gpt-5.6-terra")) == 1
    row = _codex_row(tmp_path, "codex-reviewer")
    assert row["verdict"] == "not_conforming" and row["native_observation"]["models"] == ["gpt-5.6-terra"]


def test_a_divergent_codex_effort_is_not_conforming(tmp_path):
    assert rt.run(_args(tmp_path, "codex-architect"), codex_launch=_codex_host(tmp_path, effort="xhigh")) == 1
    row = _codex_row(tmp_path, "codex-architect")
    assert row["verdict"] == "not_conforming" and row["native_observation"]["efforts"] == ["xhigh"]


def test_an_unobserved_codex_session_is_unknown_never_conforming(tmp_path):
    assert rt.run(_args(tmp_path, "codex-implementer"), codex_launch=_codex_host(tmp_path, session=False)) == 1
    row = _codex_row(tmp_path, "codex-implementer")
    assert row["verdict"] == "unknown" and row["native_observation"]["models"] == "unknown"
    assert row["native_observation"]["efforts"] == "unknown" and row["account_availability"] == "unknown"


def test_an_unavailable_codex_model_is_declared_with_the_masked_text_kept(tmp_path):
    text = "The model `gpt-5.6-luna` does not exist or you do not have access to it. user@example.com sk-abcdefgh12345678"
    host = _codex_host(tmp_path, completed=False, exit_code=1, stream_errors=[text], model="gpt-5.6-luna")
    assert rt.run(_args(tmp_path, "codex-scout"), codex_launch=host) == 1
    body = json.loads((tmp_path / "result.json").read_text())
    # the session file records the requested model, but no turn completed: it is not an observed model
    assert body["observed"]["models"] == [] and body["observed"]["unconfirmed_models"] == ["gpt-5.6-luna"]
    assert body["verdict"] == "unavailable" and body["account_availability"] == "unavailable_declared"
    assert any("does not exist" in e for e in body["errors"])
    assert "user@example.com" not in json.dumps(body) and "sk-abcdefgh" not in json.dumps(body)


def test_a_completed_codex_turn_with_an_unrelated_error_text_is_never_unavailable(tmp_path):
    host = _codex_host(tmp_path, stderr_text="model list refresh is not available")
    rt.run(_args(tmp_path, "codex-scout"), codex_launch=host)
    assert _codex_row(tmp_path, "codex-scout")["account_availability"] == "available_observed"


def test_a_local_codex_effort_refusal_is_not_an_unavailable_model(tmp_path):
    text = "Error loading config: model_reasoning_effort: unknown variant `max`, expected one of `low`, `high`"
    host = _codex_host(tmp_path, completed=False, exit_code=2, session=False, stderr_text=text)
    assert rt.run(_args(tmp_path, "codex-architect"), codex_launch=host) == 1
    row = _codex_row(tmp_path, "codex-architect")
    assert row["account_availability"] == "unknown" and row["verdict"] != "unavailable"


def test_a_completed_codex_turn_without_a_session_file_is_never_unavailable(tmp_path):
    host = _codex_host(tmp_path, session=False, stderr_text="Model metadata for `gpt-5.6-luna` not found")
    rt.run(_args(tmp_path, "codex-scout"), codex_launch=host)
    row = _codex_row(tmp_path, "codex-scout")
    assert row["account_availability"] == "unknown" and row["verdict"] == "unknown"


def test_a_codex_usage_limit_text_naming_the_model_is_never_unavailable(tmp_path):
    host = _codex_host(tmp_path, completed=False, exit_code=1, session=False,
                       stream_errors=["gpt-5.6-luna is not available: usage limit reached"])
    rt.run(_args(tmp_path, "codex-scout"), codex_launch=host)
    assert _codex_row(tmp_path, "codex-scout")["account_availability"] == "unknown"


def test_a_missing_codex_binary_is_refused_before_any_reservation(tmp_path):
    args = _args(tmp_path, "codex-scout", codex=str(tmp_path / "no-such-codex"))
    before = (tmp_path / "matrix.json").read_bytes()
    with pytest.raises(rt.RollbackTrialError, match="codex binary"):
        rt.run(args)
    assert (tmp_path / "matrix.json").read_bytes() == before and not (tmp_path / "data" / rt.LEDGER_NAME).exists()
    assert not (tmp_path / "work").exists() and not (tmp_path / "result.json").exists()


def test_the_codex_config_is_copied_beside_the_work_directory_before_the_call_and_never_printed(tmp_path, capsys):
    config = tmp_path / "codex-home" / "config.toml"
    config.parent.mkdir()
    config.write_text("secret_like = 1\n")
    seen = []
    base_host = _codex_host(tmp_path)

    def host(command, *, cwd, **kw):
        seen.append(((tmp_path / "work.backup" / "config.toml").read_text(), sorted(p.name for p in cwd.rglob("*"))))
        return base_host(command, cwd=cwd, **kw)
    rt.run(_args(tmp_path, "codex-scout"), codex_launch=host)
    assert seen == [("secret_like = 1\n", ["fixture", "marker.txt", "packet.txt"])]
    assert "secret_like" not in capsys.readouterr().out and "secret_like" not in (tmp_path / "result.json").read_text()
    assert not list((tmp_path / "work").rglob("auth.json")) and not list((tmp_path / "work.backup").rglob("auth.json"))


def test_an_existing_codex_backup_directory_is_refused_before_any_reservation(tmp_path):
    (tmp_path / "work.backup").mkdir()
    before = (tmp_path / "matrix.json").read_bytes()
    with pytest.raises(rt.RollbackTrialError, match="backup directory"):
        rt.run(_args(tmp_path, "codex-scout"), codex_launch=_codex_host(tmp_path))
    assert (tmp_path / "matrix.json").read_bytes() == before and not (tmp_path / "work").exists()


def test_a_stderr_usage_limit_undoes_a_stream_error_naming_the_model(tmp_path):
    host = _codex_host(tmp_path, completed=False, exit_code=1, session=False,
                       stream_errors=["`gpt-5.6-luna` does not exist or you do not have access to it"],
                       stderr_text="rate limit reached, try again in 20 minutes")
    rt.run(_args(tmp_path, "codex-scout"), codex_launch=host)
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["usage_limit_text"] is True and body["account_availability"] == "unknown"
    assert body["verdict"] != "unavailable" and _codex_row(tmp_path, "codex-scout")["account_availability"] == "unknown"


def test_a_usage_limit_is_a_spent_call_and_flagged_not_unavailable(tmp_path):
    host = _codex_host(tmp_path, completed=False, exit_code=1, session=False,
                       stream_errors=["You've hit your usage limit. Try again at Oct 23rd."])
    assert rt.run(_args(tmp_path, "codex-scout"), codex_launch=host) == 1
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["usage_limit_text"] is True and body["account_availability"] == "unknown"
    assert _matrix(tmp_path)["native_calls"] == 1 and any("usage-limit" in x for x in _codex_row(tmp_path, "codex-scout")["limits"])


def test_a_codex_timeout_is_not_conforming_and_still_blocks_a_retry(tmp_path):
    host = _codex_host(tmp_path, exit_code=-9, timed_out=True)
    assert rt.run(_args(tmp_path, "codex-scout"), codex_launch=host) == 1
    assert json.loads((tmp_path / "result.json").read_text())["verdict"] == "not_conforming"
    with pytest.raises(rt.RollbackTrialError):
        rt.run(_args(tmp_path, "codex-scout", work_dir=str(tmp_path / "w2"), out=str(tmp_path / "r2.json")))


def test_a_changed_codex_config_is_not_conforming(tmp_path):
    config = tmp_path / "codex-home" / "config.toml"
    config.parent.mkdir()
    config.write_text("a = 1\n")
    base_host = _codex_host(tmp_path)

    def host(*a, **kw):
        config.write_text("a = 2\n")
        return base_host(*a, **kw)
    assert rt.run(_args(tmp_path, "codex-scout"), codex_launch=host) == 1
    assert _codex_row(tmp_path, "codex-scout")["policy_guard"] == "CHANGED"
    assert "a = 2" not in (tmp_path / "result.json").read_text()


def test_the_codex_launcher_feeds_the_packet_on_stdin_and_keeps_the_streams(tmp_path):
    (tmp_path / "in.txt").write_text("hello\n")
    process = rt._launch_codex(["cat"], cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, stdout=tmp_path / "o", stderr=tmp_path / "e",
                               stdin=tmp_path / "in.txt")
    assert process["exit_code"] == 0 and (tmp_path / "o").read_text() == "hello\n"


def test_the_codex_launcher_kills_the_process_group_at_the_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "TIMEOUT_SECONDS", 0.5)
    (tmp_path / "in.txt").write_text("")
    process = rt._launch_codex(["sleep", "30"], cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, stdout=tmp_path / "o",
                               stderr=tmp_path / "e", stdin=tmp_path / "in.txt")
    assert process["timed_out"] is True and process["seconds"] < 10


# --- PAT-244: remarks deferred from the reviews of PAT-198 and PAT-245 -----------------------------------------
def test_the_data_dir_fingerprint_tells_two_data_directories_apart_without_writing_the_path(tmp_path, monkeypatch):
    first = rt.data_dir_fingerprint()
    assert first == rt.data_dir_fingerprint() and str(tmp_path) not in first
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "another-data"))
    assert rt.data_dir_fingerprint() != first
    rt.run(_args(tmp_path, "claude-implementer"), launch=_host(tmp_path, "claude-implementer"))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["ledger"]["data_dir_fingerprint"] == rt.data_dir_fingerprint()


@pytest.mark.parametrize("budget", [float("inf"), float("nan"), 0, -1])
def test_a_budget_that_is_not_finite_and_positive_is_refused_before_the_fixture_reservation_and_claim(tmp_path, budget):
    calls = []
    with pytest.raises(rt.RollbackTrialError, match="finite"):
        rt.run(_args(tmp_path, "claude-scout", max_budget_usd=budget), launch=_host(tmp_path, "claude-scout", calls=calls))
    assert calls == [] and not (tmp_path / "work").exists() and _matrix(tmp_path)["native_calls"] == 0


def test_the_cli_refuses_inf_as_a_budget(tmp_path, capsys):
    argv = ["--line", "claude-scout", "--work-dir", str(tmp_path / "w"), "--out", str(tmp_path / "o.json"),
            "--matrix", str(tmp_path / "matrix.json"), "--max-budget-usd", "inf"]
    assert rt.main(argv) == 2 and "finite" in capsys.readouterr().err and not (tmp_path / "w").exists()


def test_masking_keeps_ordinary_words_and_masks_short_openai_identifiers_and_named_secrets():
    text = ("org-AbCdEf1234567890xyz proj_abc123def456ghi789 user-9fG8hJ7k6L5m "
            "api_key=abcd1234 password: hunter2 token=xyz API_TOKEN=abc")
    out = rt.mask(text, home="/")
    for leaked in ("org-AbCdEf", "proj_abc123", "user-9fG8", "abcd1234", "hunter2", "xyz", "abc"):
        assert leaked not in out
    kept = "monkey: yes. Authentication: failed. the user-configuration of an organisation"
    assert rt.mask(kept, home="/") == kept


@pytest.mark.parametrize("name", [
    "db_password", "client_secret", "refresh_token", "private_key", "DB_Password", "x-api-key", "secret_key",
    "aws_secret_access_key", "session_key", "signing_key", "clientSecret", "refreshToken", "dbPassword",
    "password_hash", "token_value", "authorization", "Authorization", "api_key", "apikey", "passwd", "auth",
    "AWS_SECRET_ACCESS_KEY", "API_TOKEN", "MY_AUTH_HEADER"])
@pytest.mark.parametrize("separator", ["=", ": "])
def test_a_name_with_a_secret_segment_is_masked_whatever_its_case_or_shape(name, separator):
    assert rt.mask(f"{name}{separator}v4lue9", home="/") == f"{name}=<masked>"


@pytest.mark.parametrize("name", ["monkey", "Authentication", "keyboard", "author", "tokenizer", "model", "turkey",
                                  "passwords", "secretary"])
def test_a_name_without_a_secret_segment_is_kept(name):
    text = f"{name}: failed {name}=v4lue9"
    assert rt.mask(text, home="/") == text


def test_the_committed_matrix_holds_no_short_openai_identifier():
    text = (QUAL / "pat-198-native-rollback-2026-10-17.json").read_text()
    openai_id = next(p for p, replacement in rt._MASKS if replacement == "<masked-id>")
    assert not openai_id.search(text)


@pytest.mark.parametrize("text", ["model gpt-x: unknown error", "model claude-opus-5 returned an unknown error",
                                  "unknown error while loading the model"])
def test_a_generic_unknown_error_text_near_the_word_model_is_not_unavailable(tmp_path, text):
    rt.run(_args(tmp_path, "claude-reviewer"),
           launch=_host(tmp_path, "claude-reviewer", children=False, result=text, is_error=True, exit_code=1))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["account_availability"] == "unknown" and body["verdict"] != "unavailable"


def test_an_unknown_model_text_is_still_unavailable(tmp_path):
    rt.run(_args(tmp_path, "claude-reviewer"),
           launch=_host(tmp_path, "claude-reviewer", children=False, result="unknown model claude-opus-5",
                        is_error=True, exit_code=1))
    assert json.loads((tmp_path / "result.json").read_text())["verdict"] == "unavailable"


def test_a_codex_generic_unknown_error_naming_the_model_is_not_unavailable_and_unknown_model_is(tmp_path):
    host = _codex_host(tmp_path, completed=False, exit_code=1, stream_errors=["gpt-5.6-luna: unknown error"], session=False)
    rt.run(_args(tmp_path, "codex-scout"), codex_launch=host)
    assert _codex_row(tmp_path, "codex-scout")["account_availability"] == "unknown"
    other = tmp_path / "other"
    other.mkdir()
    _fresh_matrix(other / "matrix.json")
    host = _codex_host(tmp_path, completed=False, exit_code=1, stream_errors=["unknown model gpt-5.6-terra"], session=False)
    rt.run(_args(tmp_path, "codex-implementer", matrix=str(other / "matrix.json"), work_dir=str(other / "w"),
                 out=str(other / "o.json")), codex_launch=host)
    row = next(r for r in json.loads((other / "matrix.json").read_text())["rows"] if r["line"] == "codex-implementer")
    assert row["account_availability"] == "unavailable_declared"


def test_an_unwritable_ledger_refuses_before_any_launch_or_matrix_claim(tmp_path, monkeypatch):
    real_open, calls = rt.os.open, []

    def refuse(path, *a, **k):
        if Path(path).name == rt.LEDGER_NAME:
            raise PermissionError("denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr(rt.os, "open", refuse)
    with pytest.raises(rt.RollbackTrialError, match="cannot be written"):
        rt.run(_args(tmp_path, "claude-implementer"), launch=_host(tmp_path, "claude-implementer", calls=calls))
    assert calls == [] and _matrix(tmp_path)["native_calls"] == 0
    assert next(r for r in _matrix(tmp_path)["rows"] if r["line"] == "claude-implementer")["status"] == "not_run"
    assert not (tmp_path / "matrix.json.lock").exists() and not list((tmp_path / "data").glob("*.lock"))


def test_a_data_dir_that_cannot_be_created_refuses_before_any_launch(tmp_path, monkeypatch):
    (tmp_path / "blocker").write_text("a file, not a directory")
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "blocker" / "data"))
    calls = []
    with pytest.raises(rt.RollbackTrialError, match="cannot be created"):
        rt.run(_args(tmp_path, "claude-implementer"), launch=_host(tmp_path, "claude-implementer", calls=calls))
    assert calls == [] and _matrix(tmp_path)["native_calls"] == 0 and not (tmp_path / "matrix.json.lock").exists()


def test_a_synthetic_record_without_text_keeps_a_placeholder_not_an_empty_error(tmp_path):
    empty = {"type": "assistant", "isApiErrorMessage": True, "message": {"model": "<synthetic>", "content": []}}
    rt.run(_args(tmp_path, "claude-implementer"),
           launch=_host(tmp_path, "claude-implementer", child_records=[empty]))
    errors = json.loads((tmp_path / "result.json").read_text())["errors"]
    assert errors == ["<API-error record without text>"] and "" not in errors


def test_the_monkey_and_authentication_words_survive_in_a_kept_error_text(tmp_path):
    text = "monkey: patch failed. Authentication: failed for the model gpt-x"
    rt.run(_args(tmp_path, "claude-implementer"),
           launch=_host(tmp_path, "claude-implementer", children=False, result=text, is_error=True, exit_code=1))
    assert text in json.loads((tmp_path / "result.json").read_text())["errors"]


def test_a_launch_that_raises_still_counts_as_a_reservation_and_blocks_the_line(tmp_path):
    def boom(command, **kw):
        raise FileNotFoundError("claude")

    with pytest.raises(FileNotFoundError):
        rt.run(_args(tmp_path, "claude-implementer"), launch=boom)
    matrix = _matrix(tmp_path)
    row = next(r for r in matrix["rows"] if r["line"] == "claude-implementer")
    assert matrix["native_calls"] == 1 and row["status"] == "started"  # a reservation, not a call that ran
    assert (tmp_path / "data" / rt.LEDGER_NAME).read_text().count("claude-implementer") == 1
    with pytest.raises(rt.RollbackTrialError, match="no retry"):
        rt.run(_args(tmp_path, "claude-implementer", work_dir=str(tmp_path / "w2"), out=str(tmp_path / "r2.json")))


@pytest.mark.parametrize("line", ["claude-implementer", "claude-reviewer", "claude-architect"])
def test_a_logged_null_effort_is_an_absence_on_every_line_never_conforming(tmp_path, line):
    model = WIRE[line]
    null = [{"type": "assistant", "version": "2.1.300", "message": {"model": model}, "effort": None}]
    rt.run(_args(tmp_path, line), launch=_host(tmp_path, line, child_records=null))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["conformity"]["effort"] == "unknown" and body["verdict"] == "unknown"


def test_agent_types_that_could_not_be_read_are_unknown_not_a_list_of_null(tmp_path):
    def launch(command, **kw):
        outcome = _host(tmp_path, "claude-implementer")(command, **kw)
        for meta in (tmp_path / "projects").glob("*/*/subagents/*.meta.json"):
            meta.write_text("{not json")
        return outcome

    rt.run(_args(tmp_path, "claude-implementer"), launch=launch)
    row = next(r for r in _matrix(tmp_path)["rows"] if r["line"] == "claude-implementer")
    assert row["native_observation"]["agent_types"] == "unknown"
    assert json.loads((tmp_path / "result.json").read_text())["conformity"]["profile"] == "unknown"


def test_several_children_with_an_unreadable_meta_and_an_unavailable_text_reset_the_availability(tmp_path):
    text = "model not found"
    inner = _host(tmp_path, "claude-implementer", result=text, is_error=True, exit_code=1,
                  child_records=[_synthetic(text)])

    def launch(command, **kwargs):
        outcome = inner(command, **kwargs)
        for folder in (tmp_path / "projects").glob("*/*/subagents"):
            (folder / "agent-1.jsonl").write_text((folder / "agent-0.jsonl").read_text())
            (folder / "agent-1.meta.json").write_text("{not json")
            (folder / "agent-0.meta.json").write_text("{not json")
        return outcome

    rt.run(_args(tmp_path, "claude-implementer"), launch=launch)
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["conformity"]["profile"] == "unknown" and body["observed"]["children"] == 2
    assert body["verdict"] == "not_conforming" and body["account_availability"] == "unknown"


def test_two_codex_session_files_for_one_thread_leave_the_model_and_effort_unknown(tmp_path):
    inner = _codex_host(tmp_path)

    def launch(command, **kwargs):
        outcome = inner(command, **kwargs)
        first = next((tmp_path / "sessions").glob("**/rollout-*.jsonl"))
        twin = first.parent.parent / "19" / first.name
        twin.parent.mkdir(parents=True, exist_ok=True)
        twin.write_text(first.read_text())
        return outcome

    assert rt.run(_args(tmp_path, "codex-implementer"), codex_launch=launch) == 1
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["observed"]["session_files"] == 2 and body["observed"]["models"] == []
    assert body["verdict"] == "unknown" and body["account_availability"] == "unknown"
    row = _codex_row(tmp_path, "codex-implementer")
    assert row["native_observation"]["models"] == "unknown" and row["native_observation"]["efforts"] == "unknown"


def test_the_codex_environment_is_the_allow_list_plus_the_isolation_marker_and_codex_home(tmp_path, monkeypatch):
    from foundry import config
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    env = rt.codex_environment()
    assert set(env) <= set(rt._claude_child_environment()) | {"CODEX_HOME"} and "OPENAI_API_KEY" not in env
    assert env[config.RUNTIME_CONFIG_ISOLATION_ENV] == "1" and env["CODEX_HOME"] == str(tmp_path / "codex-home")
    claude_env = rt._claude_child_environment()
    assert set(claude_env) - {config.RUNTIME_CONFIG_ISOLATION_ENV} <= {
        "HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER"}


def test_the_claude_child_environment_names_add_foundry_data_to_the_allow_list_and_the_marker(tmp_path):
    calls = []
    rt.run(_args(tmp_path, "claude-implementer"), launch=_host(tmp_path, "claude-implementer", calls=calls))
    names = json.loads((tmp_path / "result.json").read_text())["bounds"]["environment_names"]
    assert "FOUNDRY_DATA" in names and "FOUNDRY_RUNTIME_CONFIG_ISOLATED" in names
    assert set(names) <= {"FOUNDRY_DATA", "FOUNDRY_RUNTIME_CONFIG_ISOLATED", "HOME", "LANG", "LC_ALL", "LOGNAME",
                          "PATH", "TMPDIR", "USER"}


def test_a_stderr_unavailability_text_gives_unavailable_only_when_nothing_else_diverges(tmp_path):
    # `set(failed) <= {"process"}` in verdict(): with the effort the line wants (high) only the exit diverges.
    rt.run(_args(tmp_path, "claude-architect"),
           launch=_host(tmp_path, "claude-architect", exit_code=1, model="<synthetic>", efforts=("high",),
                        stderr_text="the model is unavailable"))
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["conformity"]["effort"] == "exact" and body["verdict"] == "unavailable"
