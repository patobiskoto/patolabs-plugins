"""PAT-125 / PAT-ADR-0016: Haiku 5.5 declaration, minimum host version, rollback and the trial tool.

Offline only: no host, no model. The trial itself is never run here (fake launcher, fake logs).
"""
import importlib.util
import io
import json
import re
import subprocess
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

from foundry import claude_profile_trial as trial
from foundry import routing
from foundry.effort_policy import scope_for
from foundry.routing import (
    ModelTarget, RoutingConfigError, RoutingPolicy, RoutingUnavailableError, UserRouteRequest,
)
from foundry.routing_facades import (
    CLAUDE_MODEL_MIN_HOST_VERSION, claude_headless_host_version_requirement, claude_host_version,
    claude_invocation_model,
    claude_pin_profile_documents, claude_policy_model,
)

ROOT = Path(__file__).resolve().parents[1]
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _load_hook(name):
    spec = importlib.util.spec_from_file_location(f"pat125_{name}", ROOT / f"hooks/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hook = _load_hook("route_agent")
observe = _load_hook("observe_agent")
PACKET = "Goal:\nInspect fixture.\nInputs:\nFixture.\nConstraints:\nBounded; no delegation.\nDone when:\nReturn evidence."


def _config(root, economy, **extra):
    path = root / ".foundry/model-routing.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"mappings": {"claude": {"economy": economy}}, **extra}))


def _route(root, *, host_version=None, environ=None, prompt=PACKET, role="scout", **kw):
    updated, context = hook.route_tool_input({"subagent_type": f"foundry:{role}", "prompt": prompt},
                                             cwd=root, environ=environ or {}, host_version=host_version, **kw)
    return updated, json.loads(context.removeprefix("Foundry Claude route: "))


_PRODUCTION_INSIDE_REPOSITORY = trial._inside_repository


@pytest.fixture(autouse=True)
def _isolated_from_an_enclosing_repository(tmp_path, monkeypatch):
    """Keep these tests valid when pytest's ``tmp_path`` itself lies under a git repository."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.setattr(trial, "_inside_repository",
                        lambda work: _PRODUCTION_INSIDE_REPOSITORY(work, ceiling=tmp_path))


@pytest.fixture
def promoted():
    """The shipped PAT-ADR-0016 default: asserted on the real mapping, never simulated."""
    assert routing.DEFAULT_MAPPINGS["claude"]["economy"] == ModelTarget("haiku-5.5", "medium")


# ---------------------------------------------------------------------------------------------- declaration

def test_haiku_55_is_a_canonical_pin_with_its_own_effort_scope_and_versioned_profiles():
    assert claude_invocation_model("haiku-5.5") == claude_invocation_model("claude-haiku-5-5") == "claude-haiku-5-5"
    assert claude_policy_model("claude-haiku-5-5") == "haiku-5.5"
    scope = scope_for("claude", "haiku-5.5")
    assert (scope.family, scope.levels, dict(scope.inadmissible)) == ("haiku-5.5", EFFORTS, {})
    generated = claude_pin_profile_documents(ROOT)
    names = {name for name in generated if name.endswith("-haiku-5.5")}
    assert names == {f"routed-{capability}-{effort}-haiku-5.5"
                     for capability in ("readonly", "worker") for effort in EFFORTS}
    for name in names:
        text = (ROOT / "agents" / f"{name}.md").read_text()
        assert text == generated[name] and "\nmodel: claude-haiku-5-5\n" in text
        assert f"\neffort: {name.split('-')[2]}\n" in text


def test_haiku_45_stays_an_exact_effort_free_historical_pin_and_the_alias_is_not_promoted(tmp_path):
    assert claude_invocation_model("haiku-4.5") == "claude-haiku-4-5"
    assert claude_invocation_model("haiku-4.5-20251001") == "claude-haiku-4-5-20251001"
    assert claude_invocation_model("haiku") == "haiku"
    historical = {name for name in claude_pin_profile_documents(ROOT) if "-haiku-4.5" in name}
    assert historical == {f"routed-{capability}-none-haiku-4.5{suffix}"
                          for capability in ("readonly", "worker") for suffix in ("", "-20251001")}
    _config(tmp_path, {"model": "haiku", "effort": None})
    updated, visible = _route(tmp_path, host_version="2.1.285")
    assert updated["model"] == "haiku" and updated["subagent_type"] == "foundry:routed-readonly-none"
    assert "CLAUDE_ALIAS_VERSION_UNOBSERVED" in {warning["code"] for warning in visible["warnings"]}
    assert visible["host_version"] is None  # the alias names no version: nothing to require, nothing certified


@pytest.mark.parametrize("effort", EFFORTS)
def test_every_declared_effort_selects_its_own_profile_and_keeps_three_effort_values_apart(tmp_path, effort):
    _config(tmp_path, {"model": "haiku-5.5", "effort": effort})
    updated, visible = _route(tmp_path, host_version="2.1.293")
    assert updated["subagent_type"] == f"foundry:routed-readonly-{effort}-haiku-5.5" and "model" not in updated
    assert visible["effort_parameters"] == {"requested": effort, "transmitted": effort,
                                            "status": "requested", "observed": None}
    assert visible["execution_observation"] == {"model": None, "effort": None, "status": "unknown"}


def test_haiku_55_needs_an_effort_and_never_inherits_the_effort_free_rule(tmp_path):
    _config(tmp_path, {"model": "haiku-5.5", "effort": None})
    with pytest.raises(RoutingConfigError, match="effort null réservé à Haiku 4.5"):
        RoutingPolicy.load(tmp_path).resolve("scout", "claude")
    _config(tmp_path, {"model": "haiku-5.5", "effort": "ultra"})
    with pytest.raises(RoutingConfigError, match="haiku-5.5.*low, medium, high, xhigh, max"):
        RoutingPolicy.load(tmp_path).resolve("scout", "claude")


def test_passive_telemetry_keeps_the_new_canonical_identity(tmp_path):
    _config(tmp_path, {"model": "haiku-5.5", "effort": "medium"})
    environ = {"FOUNDRY_DATA": str(tmp_path / "state")}
    (tmp_path / "state").mkdir()
    updated, _ = _route(tmp_path, environ=environ, correlation="child", host_version="2.1.293")
    assert observe.observe({"hook_event_name": "PostToolUse", "tool_use_id": "child", "tool_input": updated},
                           environ=environ)
    event = json.loads((tmp_path / "state/telemetry/journal.ndjson").read_text())
    assert (event["model"], event["effort"]) == ("haiku-5.5", "medium")


# ------------------------------------------------------------------------------------- minimum host version

def test_the_minimum_host_version_is_declared_once():
    assert CLAUDE_MODEL_MIN_HOST_VERSION == {"haiku-5.5": (2, 1, 293)}


@pytest.mark.parametrize("version", ["2.1.292", "2.1.285", "2.0.999", "1.9.9"])
@pytest.mark.parametrize("available", [None, "haiku-4.5,haiku,haiku-5.5"])
def test_below_the_minimum_fails_closed_names_the_version_and_substitutes_nothing(tmp_path, version, available):
    _config(tmp_path, {"model": "haiku-5.5", "effort": "medium"})
    environ = {"FOUNDRY_DATA": str(tmp_path / "state")}
    if available:
        environ["FOUNDRY_CLAUDE_AVAILABLE_MODELS"] = available
    with pytest.raises(RoutingConfigError, match=r"Claude Code 2\.1\.293 ou supérieur requis") as error:
        _route(tmp_path, host_version=version, environ=environ, correlation="never-launched")
    assert version in str(error.value) and "Aucun repli automatique" in str(error.value)
    assert not (tmp_path / "state/telemetry/pending").exists()


@pytest.mark.parametrize("version", ["2.1.293", "2.1.294", "2.2.0", "3.0.0", "2.10.0"])
def test_at_or_above_the_minimum_is_observed_conforming(tmp_path, version):
    _config(tmp_path, {"model": "haiku-5.5", "effort": "medium"})
    _, visible = _route(tmp_path, host_version=version)
    assert visible["host_version"] == {"required": "2.1.293", "observed": version, "status": "conforming"}
    assert "CLAUDE_HOST_VERSION_UNOBSERVED" not in {warning["code"] for warning in visible["warnings"]}


@pytest.mark.parametrize("version", [None, "", "unknown", "2.1", "2.1.293-beta", "v2.1.293", 21293])
def test_an_unobserved_version_is_unknown_and_said_so_never_conforming(tmp_path, version):
    _config(tmp_path, {"model": "haiku-5.5", "effort": "medium"})
    updated, visible = _route(tmp_path, host_version=version)
    assert updated["subagent_type"] == "foundry:routed-readonly-medium-haiku-5.5"
    assert visible["host_version"] == {"required": "2.1.293", "observed": None, "status": "unknown"}
    assert [w["code"] for w in visible["warnings"]].count("CLAUDE_HOST_VERSION_UNOBSERVED") == 1


def test_a_project_translation_to_the_pin_carries_the_same_requirement(tmp_path):
    _config(tmp_path, {"model": "project-haiku", "effort": "medium"},
            claude_models={"project-haiku": "claude-haiku-5-5"})
    with pytest.raises(RoutingConfigError, match=r"2\.1\.293"):
        _route(tmp_path, host_version="2.1.292")


@pytest.mark.parametrize("economy", [{"model": "haiku-4.5", "effort": None}, {"model": "sonnet-5.5", "effort": "low"}])
def test_other_pins_carry_no_requirement_on_an_older_host(tmp_path, economy):
    _config(tmp_path, economy)
    _, visible = _route(tmp_path, host_version="2.1.285")
    assert visible["host_version"] is None


def test_the_host_version_is_read_from_the_last_versioned_transcript_record(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join([
        json.dumps({"type": "user", "version": "2.1.286"}),
        json.dumps({"type": "assistant", "version": "2.1.293",
                    "toolUseResult": {"version": "9.9.9"}, "message": {"content": '{"version":"8.8.8"}'}}),
        json.dumps({"type": "mode"}), "not json", json.dumps(["version"]), json.dumps({"version": 3}), "",
    ]))
    assert claude_host_version(path) == claude_host_version(str(path)) == "2.1.293"


@pytest.mark.parametrize("content", ["", "not json\n", '{"type":"mode"}\n', '{"toolUseResult":{"version":"2.1.293"}}\n',
                                     '{"version":"2.1.293-beta"}\n'])
def test_a_transcript_without_a_top_level_version_is_unknown(tmp_path, content):
    path = tmp_path / "session.jsonl"
    path.write_text(content)
    assert claude_host_version(path) is None


def test_an_absent_or_unnamed_transcript_is_unknown_and_only_a_bounded_tail_is_read(tmp_path):
    assert claude_host_version(None) is None and claude_host_version(tmp_path / "missing.jsonl") is None
    assert claude_host_version(tmp_path) is None
    path = tmp_path / "session.jsonl"
    filler = json.dumps({"type": "user", "pad": "x" * 4096})
    path.write_text("\n".join([json.dumps({"version": "2.1.200"}), *[filler] * 400, json.dumps({"version": "2.1.294"})]))
    assert claude_host_version(path) == "2.1.294"
    path.write_text("\n".join([json.dumps({"version": "2.1.294"}), *[filler] * 400]))
    assert claude_host_version(path) is None  # older than the tail: not searched, stays unknown


@pytest.mark.parametrize("version,decision", [("2.1.292", "deny"), ("2.1.293", "allow"), (None, "allow")])
def test_the_hook_takes_the_version_from_its_payload_transcript_not_from_the_environment(
        tmp_path, monkeypatch, capsys, version, decision):
    _config(tmp_path, {"model": "haiku-5.5", "effort": "medium"})
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"type": "user", **({"version": version} if version else {})}) + "\n")
    # another host's variables, inherited by a host started from its shell, are never a source
    for name, value in (("AI_AGENT", "claude-code_2-1-100_agent"), ("CLAUDE_AGENT_SDK_VERSION", "0.3.100"),
                        ("CLAUDE_CODE_EXECPATH", "/opt/claude-code/2.1.100/claude")):
        monkeypatch.setenv(name, value)
    for name in ("FOUNDRY_CLAUDE_AVAILABLE_MODELS", "CLAUDE_PLUGIN_OPTION_FOUNDRY_CLAUDE_AVAILABLE_MODELS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "cwd": str(tmp_path), "transcript_path": str(transcript),
        "tool_input": {"subagent_type": "foundry:lupin", "prompt": PACKET}})))
    hook.main()
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert output["permissionDecision"] == decision
    if decision == "deny":
        assert "2.1.293" in output["permissionDecisionReason"] and "updatedInput" not in output
    else:
        visible = json.loads(output["additionalContext"].removeprefix("Foundry Claude route: "))
        assert visible["host_version"]["status"] == ("conforming" if version else "unknown")


# --------------------------------------------------------------------------- promoted default and rollback

def test_the_shipped_default_is_per_tier_and_leaves_gates_and_other_tiers_alone(tmp_path, promoted):
    updated, visible = _route(tmp_path, host_version="2.1.293")
    assert updated["subagent_type"] == "foundry:routed-readonly-medium-haiku-5.5"
    assert (visible["model"], visible["effort"], visible["sources"]) == (
        "haiku-5.5", "medium", {"tier": "default", "model": "default", "effort": "default"})
    policy = RoutingPolicy.load(tmp_path)
    assert [(r, policy.resolve(r, "claude").model, policy.resolve(r, "claude").effort)
            for r in ("implementer", "coordinator", "reviewer", "architect")] == [
        ("implementer", "sonnet-5.5", "medium"), ("coordinator", "sonnet-5.5", "medium"),
        ("reviewer", "opus-5.5", "high"), ("architect", "opus-5.5", "high")]
    assert routing.GATE_FLOORS == {"reviewer": "frontier", "architect": "apex"}
    assert routing.GATE_EFFORT_FLOORS == {"reviewer": "high", "architect": "high"}
    assert {tier: (t.model, t.effort) for tier, t in routing.DEFAULT_MAPPINGS["codex"].items()} == {
        "economy": ("gpt-6-luna", "low"), "balanced": ("gpt-6.1-sol", "medium"),
        "frontier": ("gpt-6.1-sol", "high"), "apex": ("gpt-6.1-sol", "max")}
    # every non-gate role that falls back to economy gets the tier's target too
    fallen = policy.resolve("implementer", "claude", available_models={"haiku-5.5"})
    assert (fallen.selected_tier, fallen.model, fallen.effort) == ("economy", "haiku-5.5", "medium")
    assert fallen.warnings[0].code == "MODEL_FALLBACK_DOWN"
    with pytest.raises(RoutingConfigError, match="gate 'reviewer' ne peut pas descendre"):
        policy.resolve("reviewer", "claude", user=UserRouteRequest(tier="economy"))


def test_the_shipped_default_below_the_minimum_loses_the_tier_and_falls_back_to_nothing(tmp_path, promoted):
    with pytest.raises(RoutingConfigError, match=r"Claude Code 2\.1\.293 ou supérieur requis"):
        _route(tmp_path, host_version="2.1.285", environ={"FOUNDRY_CLAUDE_AVAILABLE_MODELS": "haiku-4.5,haiku-5.5"})


def test_project_rollback_with_an_explicit_null_effort_restores_the_incumbent_on_any_host(tmp_path, promoted):
    _config(tmp_path, {"model": "haiku-4.5", "effort": None})
    updated, visible = _route(tmp_path, host_version="2.1.285")
    assert updated["subagent_type"] == "foundry:routed-readonly-none-haiku-4.5" and "model" not in updated
    assert visible["transmitted_launch"]["transmitted_model"] == "claude-haiku-4-5"
    assert visible["effort_parameters"] == {"requested": None, "transmitted": None,
                                            "status": "not_applicable", "observed": None}
    assert visible["sources"] == {"tier": "default", "model": "project", "effort": "project"}
    assert visible["host_version"] is None


def test_project_rollback_without_an_effort_key_fails_with_the_fix_and_guesses_nothing(tmp_path, promoted):
    """The key-less form inherits the promoted default effort ``medium``, which Haiku 4.5 does not accept.

    It is refused rather than read as "not applicable": resolution is field-aware and never guesses an effort,
    the same rule that refuses a model-only override inheriting ``null`` for a model that needs one."""
    _config(tmp_path, {"model": "haiku-4.5"})
    with pytest.raises(RoutingConfigError) as error:
        _route(tmp_path, host_version="2.1.293")
    message = str(error.value)
    assert "Haiku 4.5 : effort rejeté, non applicable" in message
    assert "reçu : 'medium', source : default" in message
    assert '"effort": null dans mappings.claude.economy' in message


def test_restoring_the_product_default_is_the_other_rollback(tmp_path, monkeypatch, promoted):
    monkeypatch.setitem(routing.DEFAULT_MAPPINGS["claude"], "economy", ModelTarget("haiku-4.5", None))
    updated, _ = _route(tmp_path, host_version="2.1.285")
    assert updated["subagent_type"] == "foundry:routed-readonly-none-haiku-4.5"
    _config(tmp_path, {"model": "haiku-5.5", "effort": "medium"})  # the declaration stays in place
    assert _route(tmp_path, host_version="2.1.293")[0]["subagent_type"] == "foundry:routed-readonly-medium-haiku-5.5"


# ------------------------------------------------------------------------------------------ trial tool

PROFILE = "routed-readonly-medium-haiku-5.5"


def _args(tmp_path, **kw):
    return SimpleNamespace(work_dir=str(tmp_path / "work"), out=str(tmp_path / "result.json"), claude="claude",
                           parent_model="sonnet", projects_dir=str(tmp_path / "projects"), dry_run=False, **kw)


def _fake_host(tmp_path, *, model="claude-haiku-5-5", effort="medium", agent_type=f"foundry:{PROFILE}",
               version="2.1.294", child=True, answer=True, exit_code=0, timed_out=False, calls=None):
    def launch(command, *, cwd, env, stdout, stderr):
        if calls is not None:
            calls.append((list(command), dict(env)))
        session = command[command.index("--session-id") + 1]
        token = next((cwd / "fixture").iterdir()).read_text().strip()
        stdout.write_text("\n".join(json.dumps(event) for event in (
            {"type": "system", "subtype": "init", "claude_code_version": version,
             "plugins": [{"name": "foundry", "path": command[-1]}], "agents": [f"foundry:{PROFILE}"]},
            {"type": "result", "subtype": "success", "is_error": False, "result": token if answer else "refused"})))
        project = tmp_path / "projects" / "slug"
        (project / session / "subagents").mkdir(parents=True)
        context = "Foundry Claude route: " + json.dumps({
            "model": "haiku-5.5", "effort": "medium",
            "host_version": {"required": "2.1.293", "observed": version, "status": "conforming"}})
        (project / f"{session}.jsonl").write_text(json.dumps(
            {"type": "attachment", "version": version, "attachment": {"content": [context]}}) + "\n")
        if child:
            log = project / session / "subagents" / "agent-a1.jsonl"
            log.write_text("\n".join(json.dumps(record) for record in (
                {"type": "user", "version": version},
                {"type": "assistant", "version": version, "message": {"model": model},
                 **({} if effort == "absent" else {"effort": effort, "perTurnEffort": effort})})))
            log.with_suffix(".meta.json").write_text(json.dumps({"agentType": agent_type}))
        return {"exit_code": exit_code, "timed_out": timed_out, "seconds": 1.0}
    return launch


def test_trial_writes_one_conforming_result_keeping_requested_transmitted_observed_apart(tmp_path):
    calls = []
    assert trial.run(_args(tmp_path), launch=_fake_host(tmp_path, calls=calls)) == 0
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["schema"] == trial.TRIAL_SCHEMA and body["verdict"] == "conforming"
    assert body["requested"] == {"role": "scout", "tier": "economy", "model": "haiku-5.5", "effort": "medium"}
    assert (body["transmitted"]["profile"], body["transmitted"]["model"], body["transmitted"]["effort"]) == (
        PROFILE, "claude-haiku-5-5", "medium")
    assert body["observed"]["models"] == ["claude-haiku-5-5"] and body["observed"]["efforts"] == ["medium"]
    assert body["observed"]["agent_types"] == [f"foundry:{PROFILE}"] and body["observed"]["children"] == 1
    assert body["hook"]["host_version"]["observed"] == "2.1.294"
    assert set(body["conformity"].values()) == {"exact", "conforming", "this_checkout"}
    assert body["bounds"]["timeout_seconds"] == 1200 and body["bounds"]["replays"] == 0
    (command, env), = calls  # one parent, launched once
    assert command[-2:] == ["--plugin-dir", str(ROOT)] and "--tools" not in command
    assert command[:2] == ["claude", "-p"] and command[command.index("--model") + 1] == "sonnet"
    assert set(env) <= {"HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER",
                        "FOUNDRY_DATA", "FOUNDRY_RUNTIME_CONFIG_ISOLATED"}
    session = command[command.index("--session-id") + 1]
    assert session not in json.dumps(body) and "<session id>" in body["argv"]
    assert str(tmp_path) not in json.dumps({k: v for k, v in body.items() if k != "source"})


@pytest.mark.parametrize("host,state,named", [
    ({"model": "claude-haiku-4-5-20251001"}, "not_conforming", "model"),
    ({"model": "haiku"}, "not_conforming", "model"),
    ({"effort": "low"}, "not_conforming", "effort"),
    ({"effort": None}, "not_conforming", "effort"),
    ({"agent_type": "foundry:routed-readonly-none"}, "not_conforming", "profile"),
    ({"version": "2.1.292"}, "not_conforming", "host_version"),
    ({"answer": False}, "not_conforming", "fixture"),
    ({"exit_code": 1}, "not_conforming", "process"),
    ({"timed_out": True}, "not_conforming", "process"),
    ({"child": False}, "unknown", "model"),
    ({"effort": "absent"}, "unknown", "effort"),
    ({"model": "claude-haiku-5-5-20261007"}, "unknown", "model"),
])
def test_trial_never_calls_a_divergent_or_absent_observation_conforming(tmp_path, host, state, named):
    assert trial.run(_args(tmp_path), launch=_fake_host(tmp_path, **host)) == 1
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["verdict"] == state and named in body["not_established"]


def test_trial_is_never_replayed_and_a_dry_run_launches_nothing(tmp_path, capsys):
    launched = []
    args = _args(tmp_path)
    args.dry_run = True
    assert trial.run(args, launch=lambda *a, **k: launched.append(1)) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["dry_run"] is True and printed["command"][0] == "claude" and not launched
    assert not (tmp_path / "result.json").exists()
    assert not (tmp_path / "work").exists()  # PAT-134: a dry run creates nothing, so a real run may use the same path
    args.dry_run = False
    assert trial.run(args, launch=_fake_host(tmp_path)) == 0
    (tmp_path / "result.json").unlink()
    with pytest.raises(trial.TrialError, match="never replayed"):  # the real run used that directory
        trial.run(args, launch=_fake_host(tmp_path))
    args.work_dir = str(tmp_path / "second")
    assert trial.run(args, launch=_fake_host(tmp_path)) == 0
    args.work_dir = str(tmp_path / "third")
    with pytest.raises(trial.TrialError, match="never replayed"):  # the result file exists
        trial.run(args, launch=_fake_host(tmp_path))


def test_trial_refuses_a_work_directory_inside_a_repository(tmp_path):
    (tmp_path / ".git").mkdir()
    with pytest.raises(trial.TrialError, match="outside any git repository"):
        trial.run(_args(tmp_path), launch=lambda *a, **k: pytest.fail("launched"))
    assert trial.main(["--work-dir", str(tmp_path / "work"), "--out", str(tmp_path / "r.json"),
                       "--claude", str(tmp_path / "no-such-binary")]) == 2


def test_the_neutral_fixture_is_a_separate_option_that_leaves_the_default_trial_as_recorded(tmp_path):
    default = trial.parent_prompt(Path("/f/token.txt"))
    assert "token.txt" in default and "marker" not in default and "background" not in default
    calls = []
    args = _args(tmp_path, neutral_fixture=True)
    assert trial.run(args, launch=_fake_host(tmp_path, calls=calls)) == 0
    body = json.loads((tmp_path / "result.json").read_text())
    assert body["fixture_variant"] == "neutral" and body["verdict"] == "conforming"
    prompt = calls[0][0][2]
    marker = (tmp_path / "work/fixture/marker.txt").read_text().strip()
    assert marker.startswith("marqueur-de-fixture-") and marker[-8:].isdigit()
    assert "marker.txt" in prompt and "not a secret" in prompt and "do not reply until it has completed" in prompt
    for word in ("token", "jeton", "password", "credential\""):
        assert word not in prompt.lower() and word not in marker
    assert not (tmp_path / "work/fixture/token.txt").exists()
    # the verdict rule is the same: a marker that does not come back is still not conforming
    args.work_dir, args.out = str(tmp_path / "w2"), str(tmp_path / "r2.json")
    assert trial.run(args, launch=_fake_host(tmp_path, answer=False)) == 1
    assert json.loads((tmp_path / "r2.json").read_text())["verdict"] == "not_conforming"
    # the default run carries no variant key: its result shape is the recorded one
    plain = _args(tmp_path)
    plain.work_dir, plain.out = str(tmp_path / "w3"), str(tmp_path / "r3.json")
    assert trial.run(plain, launch=_fake_host(tmp_path)) == 0
    assert "fixture_variant" not in json.loads((tmp_path / "r3.json").read_text())


def test_the_recorded_trial_result_is_kept_as_written_and_names_nothing_personal():
    text = (ROOT / "docs/qualification/pat-125-native-trial.json").read_text()
    body = json.loads(text)
    assert body["schema"] == trial.TRIAL_SCHEMA and body["verdict"] == "not_conforming"
    assert body["not_established"] == ["fixture"] and body["conformity"]["fixture"] == "divergent"
    assert {k: v for k, v in body["conformity"].items() if k != "fixture"} == {
        "profile": "exact", "model": "exact", "effort": "exact", "host_version": "conforming",
        "plugin_source": "this_checkout"}
    assert "/Users/" not in text and "/home/" not in text and "<session id>" in body["argv"]
    assert "fixture_variant" not in body


def test_the_second_trial_result_is_a_separate_file_with_its_own_verdict():
    text = (ROOT / "docs/qualification/pat-125-native-trial-2.json").read_text()
    body = json.loads(text)
    assert body["schema"] == trial.TRIAL_SCHEMA and body["fixture_variant"] == "neutral"
    assert body["verdict"] == "conforming" and body["not_established"] == []
    assert body["conformity"] == {"profile": "exact", "model": "exact", "effort": "exact",
                                  "host_version": "conforming", "plugin_source": "this_checkout", "fixture": "exact"}
    assert (body["observed"]["models"], body["observed"]["efforts"]) == (["claude-haiku-5-5"], ["medium"])
    assert "/Users/" not in text and "/home/" not in text and "<session id>" in body["argv"]


# ------------------------------------------------------------------------------------ headless runners

def _version_runner(answer, calls):
    def runner(argv, **kwargs):
        calls.append((list(argv), kwargs))
        if isinstance(answer, BaseException):
            raise answer
        return SimpleNamespace(returncode=answer[0], stdout=answer[1])
    return runner


def test_headless_check_asks_the_launched_binary_only_for_a_pin_with_a_minimum():
    calls = []
    runner = _version_runner((0, "2.1.294 (Claude Code)\n"), calls)
    env = {"PATH": "/bin"}
    for model in ("claude-haiku-4-5", "claude-sonnet-5-5", "haiku", "project-wire-alias"):
        assert claude_headless_host_version_requirement(model, "claude", runner=runner, env=env) is None
    assert calls == []
    assert claude_headless_host_version_requirement("claude-haiku-5-5", "claude", runner=runner, env=env) == {
        "required": "2.1.293", "observed": "2.1.294", "status": "conforming"}
    (argv, kwargs), = calls
    assert argv == ["claude", "--version"] and kwargs["env"] is env and kwargs["check"] is False
    assert kwargs["cwd"] is None  # no directory given: the runner's own default, as before
    claude_headless_host_version_requirement("claude-haiku-5-5", "claude", runner=runner, env=env, cwd="/launch/dir")
    assert calls[-1][1]["cwd"] == "/launch/dir"


@pytest.mark.parametrize("answer", [(0, "2.1.292 (Claude Code)"), (0, "2.1.285 (Claude Code)\n")])
def test_headless_check_fails_closed_below_the_minimum(answer):
    with pytest.raises(RoutingConfigError, match=r"Claude Code 2\.1\.293 ou supérieur requis.*observée : 2\.1\.2"):
        claude_headless_host_version_requirement(
            "claude-haiku-5-5", "claude", runner=_version_runner(answer, []), env={})


@pytest.mark.parametrize("answer", [
    (1, "2.1.294 (Claude Code)"), (0, ""), (0, "2.1.294"), (0, "claude 2.1.294"), (0, None),
    FileNotFoundError("claude"), subprocess.TimeoutExpired("claude", 30),
])
def test_headless_check_keeps_an_unreadable_version_unknown_and_says_so_on_stderr(answer, capsys):
    assert claude_headless_host_version_requirement(
        "claude-haiku-5-5", "claude", runner=_version_runner(answer, []), env={}) == {
        "required": "2.1.293", "observed": None, "status": "unknown"}
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.count("\n") == 1
    assert re.search(r"CLAUDE_HOST_VERSION_UNOBSERVED.*2\.1\.293.*jamais présumé conforme", captured.err)


def test_a_headless_launch_of_the_shipped_default_obeys_the_version_rule(tmp_path, promoted):
    """A non-gate role that falls back down to ``economy`` reaches a headless runner with the Haiku 5.5 pin."""
    route = RoutingPolicy.load(tmp_path).resolve("implementer", "claude", available_models={"haiku-5.5"})
    assert (route.selected_tier, route.model, route.effort) == ("economy", "haiku-5.5", "medium")
    wire = claude_invocation_model(route.model)
    with pytest.raises(RoutingConfigError, match=r"Claude Code 2\.1\.293 ou supérieur requis"):
        claude_headless_host_version_requirement(
            wire, "claude", runner=_version_runner((0, "2.1.292 (Claude Code)"), []), env={})
    assert claude_headless_host_version_requirement(
        wire, "claude", runner=_version_runner((0, "2.1.293 (Claude Code)"), []), env={})["status"] == "conforming"
    assert claude_headless_host_version_requirement(
        wire, "claude", runner=_version_runner(FileNotFoundError("claude"), []), env={})["status"] == "unknown"


# ------------------------------------------------------------------ breaking shapes of the promotion (review)

def test_an_availability_list_without_haiku_55_refuses_the_economy_tier_even_on_a_conforming_host(tmp_path, promoted):
    """BREAKING: the list documented before PAT-125 names ``haiku-4.5``; the fix is to add ``haiku-5.5``."""
    former = {"FOUNDRY_CLAUDE_AVAILABLE_MODELS": "haiku-4.5,sonnet-5.5,opus-5.5"}
    with pytest.raises(RoutingUnavailableError) as error:
        _route(tmp_path, host_version="2.1.294", environ=former)
    assert "Aucun modèle disponible pour le rôle 'scout' sur claude" in str(error.value)
    assert "Niveaux inférieurs essayés : economy" in str(error.value)
    # no substitution by the listed Haiku 4.5; a non-gate downward fallback ends the same way
    with pytest.raises(RoutingUnavailableError, match="essayés : balanced, economy"):
        _route(tmp_path, host_version="2.1.294", role="implementer",
               environ={"FOUNDRY_CLAUDE_AVAILABLE_MODELS": "haiku-4.5,opus-5.5"})
    fixed = {"FOUNDRY_CLAUDE_AVAILABLE_MODELS": "haiku-5.5,sonnet-5.5,opus-5.5"}
    assert _route(tmp_path, host_version="2.1.294", environ=fixed)[0]["subagent_type"] == (
        "foundry:routed-readonly-medium-haiku-5.5")


def test_an_effort_only_economy_override_now_applies_to_haiku_55(tmp_path, promoted):
    """BREAKING: ``{"effort": "low"}`` used to mean Haiku 4.5 without effort; ``{"effort": null}`` used to pass."""
    _config(tmp_path, {"effort": "low"})
    updated, visible = _route(tmp_path, host_version="2.1.294")
    assert updated["subagent_type"] == "foundry:routed-readonly-low-haiku-5.5"  # declared, not qualified
    assert (visible["model"], visible["effort"], visible["sources"]["model"], visible["sources"]["effort"]) == (
        "haiku-5.5", "low", "default", "project")
    _config(tmp_path, {"effort": None})
    with pytest.raises(RoutingConfigError, match="effort null réservé à Haiku 4.5 ; pour 'haiku-5.5'"):
        _route(tmp_path, host_version="2.1.294")


@pytest.mark.parametrize("economy,asked,source,fix,fixed_economy,fixed_asked", [
    # project names the model, effort inherited from the default: add the null effort
    ({"model": "haiku-4.5"}, {}, "default", 'écrivez explicitement "effort": null dans mappings.claude.economy',
     {"model": "haiku-4.5", "effort": None}, {}),
    # direct user request, nothing mapped: the mapping must name the model too
    (None, {"model": "haiku-4.5"}, "default",
     'écrivez explicitement {"model": "haiku-4.5", "effort": null} dans mappings.claude.economy',
     {"model": "haiku-4.5", "effort": None}, {"model": "haiku-4.5"}),
    (None, {"model": "haiku"}, "default",
     'écrivez explicitement {"model": "haiku", "effort": null} dans mappings.claude.economy',
     {"model": "haiku", "effort": None}, {"model": "haiku"}),
    ({"model": "haiku-4.5", "effort": "high"}, {}, "project", "remplacez cet effort par null dans mappings.claude.economy",
     {"model": "haiku-4.5", "effort": None}, {}),
    ({"model": "haiku-4.5", "effort": None}, {"effort": "low"}, "user", "retirez l'effort de la demande utilisateur",
     {"model": "haiku-4.5", "effort": None}, {}),
])
def test_the_haiku_45_effort_refusal_names_a_fix_that_really_resolves(
        tmp_path, economy, asked, source, fix, fixed_economy, fixed_asked):
    if economy is not None:
        _config(tmp_path, economy)
    with pytest.raises(RoutingConfigError) as error:
        RoutingPolicy.load(tmp_path).resolve("scout", "claude", user=UserRouteRequest(**asked))
    message = str(error.value)
    assert f"source : {source}" in message and fix in message
    if source == "user":
        assert "écrivez explicitement" not in message and "remplacez" not in message
    # the suggested fix, applied, resolves to the effort-free pin or alias
    _config(tmp_path, fixed_economy)
    route = RoutingPolicy.load(tmp_path).resolve("scout", "claude", user=UserRouteRequest(**fixed_asked))
    assert (route.model, route.effort) == (fixed_economy["model"], None)
    # and it does not break the default scout the way an effort-only null would
    _config(tmp_path, {"effort": None})
    with pytest.raises(RoutingConfigError, match="effort null réservé à Haiku 4.5 ; pour 'haiku-5.5'"):
        RoutingPolicy.load(tmp_path).resolve("scout", "claude")


def test_a_dated_haiku_55_identifier_is_refused_by_the_hook_for_its_missing_profile_not_its_host(tmp_path):
    _config(tmp_path, {"model": "project-dated", "effort": "medium"},
            claude_models={"project-dated": "claude-haiku-5-5-20261007"})
    for version in ("2.1.285", "2.1.294", None):
        with pytest.raises(RoutingConfigError, match="aucun profil épinglé préchargé") as error:
            _route(tmp_path, host_version=version)
        assert "Mettez à jour Claude Code" not in str(error.value)


def test_the_production_repository_check_sees_an_ancestor_git_and_nothing_else(tmp_path):
    work = tmp_path / "a" / "b" / "work"
    work.mkdir(parents=True)
    check = _PRODUCTION_INSIDE_REPOSITORY  # the real function, not the bounded one of the autouse fixture
    assert check(work, ceiling=tmp_path) is False
    (tmp_path / "a" / ".git").mkdir()
    assert check(work, ceiling=tmp_path) is True and check(tmp_path / "a", ceiling=tmp_path) is True
    assert check(tmp_path / "other", ceiling=tmp_path) is False
    (tmp_path / "a" / ".git").rmdir()
    (work / ".git").write_text("gitdir: elsewhere\n")  # a worktree's .git file counts too
    assert check(work, ceiling=tmp_path) is True
    # unbounded, as production calls it: an ancestor repository above the ceiling is seen
    (work / ".git").unlink()
    (tmp_path / ".git").mkdir()
    assert check(work) is True and check(work, ceiling=tmp_path / "a") is False


def test_the_hook_refusal_mentions_a_resumed_session_and_the_headless_one_does_not(tmp_path):
    _config(tmp_path, {"model": "haiku-5.5", "effort": "medium"})
    with pytest.raises(RoutingConfigError, match="session REPRISE.*démarrez une nouvelle session"):
        _route(tmp_path, host_version="2.1.292")
    with pytest.raises(RoutingConfigError) as error:
        claude_headless_host_version_requirement(
            "claude-haiku-5-5", "claude", runner=_version_runner((0, "2.1.292 (Claude Code)"), []), env={})
    assert "session" not in str(error.value)


def test_a_dated_snapshot_of_the_haiku_55_line_carries_the_same_minimum_in_a_headless_launch():
    dated = "claude-haiku-5-5-20261007"
    with pytest.raises(RoutingConfigError, match=r"claude-haiku-5-5-20261007.*2\.1\.293 ou supérieur requis"):
        claude_headless_host_version_requirement(
            dated, "claude", runner=_version_runner((0, "2.1.292 (Claude Code)"), []), env={})
    assert claude_headless_host_version_requirement(
        dated, "claude", runner=_version_runner((0, "2.1.293 (Claude Code)"), []), env={})["status"] == "conforming"
    calls = []
    for other in ("claude-haiku-5-5-preview", "claude-haiku-5-50", "claude-haiku-4-5-20251001", "xclaude-haiku-5-5"):
        assert claude_headless_host_version_requirement(
            other, "claude", runner=_version_runner((0, "2.1.1 (Claude Code)"), calls), env={}) is None
    assert calls == []


def test_the_unobserved_version_line_is_written_at_every_headless_launch_without_global_state(capsys):
    runner = _version_runner(FileNotFoundError("claude"), [])
    filters, registry = list(warnings.filters), dict(globals().get("__warningregistry__", {}))
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # an operator's -W error is neither overridden nor triggered
        for _ in range(3):
            claude_headless_host_version_requirement("claude-haiku-5-5", "claude", runner=runner, env={})
    lines = capsys.readouterr().err.splitlines()
    assert [line.split(" ")[0] for line in lines] == ["CLAUDE_HOST_VERSION_UNOBSERVED"] * 3
    assert list(warnings.filters) == filters and dict(globals().get("__warningregistry__", {})) == registry


def test_a_single_record_larger_than_the_tail_leaves_the_version_unknown(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({"version": "2.1.294"}) + "\n"
                    + json.dumps({"type": "user", "version": "2.1.294", "pad": "x" * (1 << 20)}))
    assert claude_host_version(path) is None


def test_a_refusal_after_policy_resolution_creates_nothing_on_disk(tmp_path, monkeypatch):
    """PAT-134: the work directory is created only after every refusal, so a refused run does not burn the path."""
    def refuse(*args, **kwargs):
        raise RoutingConfigError("divergent")
    monkeypatch.setattr(trial, "claude_invocation_binding", refuse)
    with pytest.raises(RoutingConfigError):
        trial.run(_args(tmp_path), launch=lambda *a, **k: pytest.fail("launched"))
    assert list(tmp_path.iterdir()) == []
