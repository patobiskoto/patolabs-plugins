import json
import subprocess

import pytest

from foundry import doctor
from foundry.escalation import EscalationStore


def _make_hook(repo, relative_dir):
    """Create an executable pre-push hook under ``repo/relative_dir``."""
    hook_dir = repo / relative_dir
    hook_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hook_dir / "pre-push"
    hook_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hook_path.chmod(0o755)
    return hook_path


def _make_runner(hooks_path_stdout, hooks_path_returncode=0, toplevel=None, toplevel_returncode=0):
    """Build a fake ``runner`` that answers both ``git config --get core.hooksPath``
    and ``git rev-parse --show-toplevel`` distinctly, the way real git does."""
    commands = []

    def runner(command, **kwargs):
        commands.append((command, kwargs))
        if command[3:5] == ["rev-parse", "--show-toplevel"]:
            stdout = "" if toplevel is None else f"{toplevel}\n"
            return subprocess.CompletedProcess(command, toplevel_returncode, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(command, hooks_path_returncode, stdout=hooks_path_stdout, stderr="")

    runner.commands = commands
    return runner


def test_doctor_reports_missing_local_hook_path_as_actionable_warning(tmp_path, capsys):
    runner = _make_runner("", hooks_path_returncode=1, toplevel=str(tmp_path))

    payload = doctor.local_hook_diagnostic(tmp_path, runner=runner)
    doctor._print_local_hook(payload)

    assert (
        ["git", "-C", str(tmp_path), "config", "--get", "core.hooksPath"],
        {"capture_output": True, "text": True, "check": False},
    ) in runner.commands
    assert payload["status"] == "warning"
    assert payload["detail"] == "core.hooksPath absent, vide ou illisible"
    output = capsys.readouterr().out
    assert "🟠 Hook local pre-push" in output
    assert "git config core.hooksPath .githooks" in output


def test_doctor_falls_back_to_monorepo_hook_dir_in_fix_command(tmp_path, capsys):
    """When only the monorepo path exists, the fix command must point at it, not .githooks."""
    _make_hook(tmp_path, "plugins/foundry/.githooks")
    runner = _make_runner("", hooks_path_returncode=1, toplevel=str(tmp_path))

    payload = doctor.local_hook_diagnostic(tmp_path, runner=runner)
    doctor._print_local_hook(payload)

    assert payload["status"] == "warning"
    output = capsys.readouterr().out
    assert "git config core.hooksPath plugins/foundry/.githooks" in output


def test_doctor_accepts_monorepo_hook_dir_as_healthy(tmp_path, capsys):
    """AGENTS.md#R1's documented monorepo hook path is a healthy configuration too."""
    _make_hook(tmp_path, "plugins/foundry/.githooks")
    runner = _make_runner("plugins/foundry/.githooks\n", toplevel=str(tmp_path))

    payload = doctor.local_hook_diagnostic(tmp_path, runner=runner)
    doctor._print_local_hook(payload)

    assert payload["status"] == "healthy"
    output = capsys.readouterr().out
    assert "🟢 Hook local pre-push" in output


def test_doctor_rejects_known_candidate_missing_hook_file(tmp_path, capsys):
    """A candidate directory that exists but has no pre-push hook stays orange."""
    (tmp_path / ".githooks").mkdir()
    runner = _make_runner(".githooks\n", toplevel=str(tmp_path))

    payload = doctor.local_hook_diagnostic(tmp_path, runner=runner)

    assert payload["status"] == "warning"
    assert "aucun hook pre-push exécutable" in payload["detail"]


def test_doctor_resolves_healthy_hook_from_a_subdirectory(tmp_path, capsys):
    """core.hooksPath is resolved by Git relative to the toplevel, not the cwd doctor
    happens to run from (e.g. plugins/foundry inside this monorepo)."""
    _make_hook(tmp_path, "plugins/foundry/.githooks")
    subdirectory = tmp_path / "plugins" / "foundry"
    runner = _make_runner("plugins/foundry/.githooks\n", toplevel=str(tmp_path))

    payload = doctor.local_hook_diagnostic(subdirectory, runner=runner)
    doctor._print_local_hook(payload)

    assert payload["status"] == "healthy"
    output = capsys.readouterr().out
    assert "🟢 Hook local pre-push" in output


def test_doctor_falls_back_to_repository_when_toplevel_resolution_fails(tmp_path, capsys):
    """If `git rev-parse --show-toplevel` fails, fall back to the given root rather
    than erroring the whole diagnostic."""
    _make_hook(tmp_path, ".githooks")
    runner = _make_runner(".githooks\n", toplevel=None, toplevel_returncode=128)

    payload = doctor.local_hook_diagnostic(tmp_path, runner=runner)

    assert payload["status"] == "healthy"


def test_local_scout_doctor_is_non_invasive_and_redacted(tmp_path):
    sentinel = "NEVER_SHOW_ENDPOINT_OR_SECRET"
    calls = []
    payload = doctor.local_scout_diagnostic(tmp_path, environ={
        "FOUNDRY_LOCAL_SCOUT_ENABLED": "1",
        "FOUNDRY_LOCAL_SCOUT_BASE_URL": f"http://127.0.0.1:11434{sentinel}",
    }, transport_probe=lambda *_args: calls.append(True))
    assert payload["status"] == "invalid policy"
    assert sentinel not in str(payload)
    assert calls == []


def test_doctor_reports_incorrect_local_hook_path_as_actionable_warning(tmp_path, capsys):
    runner = _make_runner(".other-hooks\n", toplevel=str(tmp_path))

    payload = doctor.local_hook_diagnostic(tmp_path, runner=runner)
    doctor._print_local_hook(payload)

    assert payload["status"] == "warning"
    assert "attendu '.githooks' ou 'plugins/foundry/.githooks'" in payload["detail"]
    assert "git config core.hooksPath .githooks" in capsys.readouterr().out


def test_doctor_preserves_surrounding_spaces_in_local_hook_path(tmp_path):
    runner = _make_runner(" .githooks \n", toplevel=str(tmp_path))

    payload = doctor.local_hook_diagnostic(tmp_path, runner=runner)

    assert payload["status"] == "warning"
    assert payload["detail"] == (
        "core.hooksPath=' .githooks ', attendu '.githooks' ou 'plugins/foundry/.githooks'"
    )


def test_doctor_treats_empty_local_hook_path_as_missing(tmp_path):
    runner = _make_runner("\n", toplevel=str(tmp_path))

    payload = doctor.local_hook_diagnostic(tmp_path, runner=runner)

    assert payload["status"] == "warning"
    assert payload["detail"] == "core.hooksPath absent, vide ou illisible"


def test_doctor_reports_correct_local_hook_path_as_healthy(tmp_path, capsys):
    _make_hook(tmp_path, ".githooks")
    runner = _make_runner(".githooks\n", toplevel=str(tmp_path))

    payload = doctor.local_hook_diagnostic(tmp_path, runner=runner)
    doctor._print_local_hook(payload)

    assert payload["status"] == "healthy"
    output = capsys.readouterr().out
    assert "🟢 Hook local pre-push" in output
    assert "frontière de sécurité" in output


def test_doctor_accepts_crlf_terminated_local_hook_path(tmp_path):
    _make_hook(tmp_path, ".githooks")
    runner = _make_runner(".githooks\r\n", toplevel=str(tmp_path))

    payload = doctor.local_hook_diagnostic(tmp_path, runner=runner)

    assert payload["status"] == "healthy"


def test_doctor_accepts_absolute_hooks_path_matching_toplevel_candidate(tmp_path, capsys):
    """A trivially-resolvable absolute core.hooksPath (<toplevel>/<candidate>) is healthy too."""
    _make_hook(tmp_path, ".githooks")
    absolute_value = str(tmp_path / ".githooks")
    runner = _make_runner(f"{absolute_value}\n", toplevel=str(tmp_path))

    payload = doctor.local_hook_diagnostic(tmp_path, runner=runner)

    assert payload["status"] == "healthy"


@pytest.mark.parametrize("returncode", [2, 128])
def test_doctor_reports_unexpected_git_config_exit_as_redacted_error(tmp_path, returncode):
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode, stdout="", stderr="sensitive git failure",
        )

    payload = doctor.local_hook_diagnostic(tmp_path, runner=runner)

    assert payload["status"] == "error"
    assert payload["ok"] is False
    assert payload["detail"] == "lecture de core.hooksPath impossible"
    assert "sensitive" not in payload["detail"]


def test_doctor_reports_local_hook_read_failure_without_raising(tmp_path):
    def runner(command, **kwargs):
        raise FileNotFoundError

    payload = doctor.local_hook_diagnostic(tmp_path, runner=runner)

    assert payload["status"] == "error"
    assert payload["ok"] is False


def test_doctor_routing_reports_both_hosts_and_redacts_override_values(tmp_path, monkeypatch):
    (tmp_path / ".foundry").mkdir()
    (tmp_path / ".foundry" / "model-routing.json").write_text(
        json.dumps({"roles": {"implementer": "frontier"}}), encoding="utf-8",
    )
    sentinel = "MODEL_OVERRIDE_VALUE_MUST_NEVER_APPEAR"
    environ = {
        "CLAUDE_CODE_SUBAGENT_MODEL": sentinel,
        "FOUNDRY_CLAUDE_AVAILABLE_MODELS": "haiku-4.5,sonnet-5,opus-5,fable-5",
        "FOUNDRY_CODEX_AVAILABLE_MODELS": "gpt-5.6-luna,gpt-5.6-terra,gpt-5.6-sol",
    }

    payload = doctor.routing_diagnostics(tmp_path, environ=environ)
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["config"]["valid"] is True
    assert set(payload["hosts"]) == {"claude", "codex"}
    assert all(len(host["routes"]) == 5 for host in payload["hosts"].values())
    assert payload["hosts"]["claude"]["overrides"] == ["CLAUDE_CODE_SUBAGENT_MODEL"]
    assert sentinel not in rendered
    assert all(sentinel not in str(value) for value in payload.values())
    expected = {
        "claude": ("fable-5", "high"),
        "codex": ("gpt-5.6-sol", "max"),
    }
    for host, (model, effort) in expected.items():
        architect = next(r for r in payload["hosts"][host]["routes"] if r["role"] == "architect")
        assert architect["ok"] is True
        assert architect["model"] == model
        assert architect["effort"] == effort
        assert architect["gate_floor"] == "apex"
        assert architect["gate_effort_floor"] == "high"


def test_doctor_rejects_unexecutable_claude_policy(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "mappings": {"claude": {"balanced": {"model": "untranslated-model"}}},
    }), encoding="utf-8")

    payload = doctor.routing_diagnostics(tmp_path, environ={})
    assert payload["config"]["valid"] is False
    assert "untranslated-model" in payload["config"]["detail"]


def test_doctor_never_emits_override_values_in_payload_or_output(tmp_path, monkeypatch, capsys):
    sentinel = "SENTINEL_OVERRIDE_VALUE_NEVER_EMIT"
    payload = doctor.routing_diagnostics(
        tmp_path, environ={}, codex_profile={"model": sentinel, "model_reasoning_effort": sentinel},
    )
    assert payload["hosts"]["codex"]["overrides"] == [
        "profile.model", "profile.model_reasoning_effort",
    ]
    assert sentinel not in str(payload)

    monkeypatch.setenv("CLAUDE_CODE_SUBAGENT_MODEL", sentinel)
    monkeypatch.chdir(tmp_path)
    doctor.main(["--profile-model-active", "--profile-effort-active"])
    captured = capsys.readouterr()
    assert "CLAUDE_CODE_SUBAGENT_MODEL" in captured.out
    assert "profile.model" in captured.out
    assert "profile.model_reasoning_effort" in captured.out
    assert sentinel not in captured.out
    assert sentinel not in captured.err


def test_doctor_keeps_invalid_routing_and_unavailable_gate_visible_when_tracker_fails(
    tmp_path, monkeypatch, capsys,
):
    (tmp_path / ".foundry").mkdir()
    (tmp_path / ".foundry" / "model-routing.json").write_text("{invalid", encoding="utf-8")
    monkeypatch.setattr(doctor.config, "require", lambda _key: "value")
    monkeypatch.setattr(doctor.foundry, "tracker", lambda: (_ for _ in ()).throw(RuntimeError("tracker down")))
    monkeypatch.chdir(tmp_path)

    doctor.main([])
    output = capsys.readouterr().out

    assert "Routage model-aware" in output
    assert "JSON invalide" in output
    assert "Tracker" in output and "tracker down" in output


def test_doctor_rejects_unsafe_devhub_url_before_any_secret_accessor(
    tmp_path, monkeypatch, capsys,
):
    secret_reads = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(doctor.config, "tracker_name", lambda: "devhub")
    monkeypatch.setenv("DEVHUB_URL", "http://devhub.example")
    monkeypatch.delenv("DEVHUB_TRACKER_TOKEN", raising=False)
    monkeypatch.delenv("DEVHUB_TRACKER_PROOF_SECRET", raising=False)
    monkeypatch.setattr(
        doctor.config, "_keychain_secret",
        lambda account: secret_reads.append(account) or "secret-must-not-be-read",
    )

    doctor.main([])

    captured = capsys.readouterr()
    assert secret_reads == []
    assert "HTTPS hors loopback" in captured.out
    assert "secret-must-not-be-read" not in captured.out
    assert "secret-must-not-be-read" not in captured.err


def test_doctor_file_fallback_validates_transport_before_bulk_or_secret_read(
    tmp_path, monkeypatch, capsys,
):
    sentinel = "PLAINTEXT_SENTINEL_MUST_NOT_BE_MATERIALIZED"
    config_path = tmp_path / "config.env"
    config_path.write_text(
        f"DEVHUB_TRACKER_TOKEN={sentinel}\n"
        "FOUNDRY_TRACKER=devhub\n"
        "DEVHUB_URL=http://devhub.example\n"
        f"DEVHUB_TRACKER_PROOF_SECRET={sentinel}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FOUNDRY_CONFIG", str(config_path))
    for key in (
        "FOUNDRY_TRACKER", "DEVHUB_URL", "DEVHUB_TRACKER_TOKEN",
        "DEVHUB_TRACKER_PROOF_SECRET", "CLAUDE_PLUGIN_OPTION_FOUNDRY_TRACKER",
        "CLAUDE_PLUGIN_OPTION_DEVHUB_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        doctor.config, "_load_dev_files",
        lambda: pytest.fail("doctor must not bulk-parse before transport validation"),
    )
    monkeypatch.setattr(
        doctor.config, "_keychain_secret",
        lambda _account: pytest.fail("doctor must not read a secret before validation"),
    )

    doctor.main([])

    captured = capsys.readouterr()
    assert "HTTPS hors loopback" in captured.out
    assert sentinel not in captured.out
    assert sentinel not in captured.err


def test_doctor_issue_status_is_read_without_creating_state(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    store = EscalationStore.for_root(tmp_path)
    issue_path = store._path("FOUNDRY-42")

    payload = doctor.routing_diagnostics(tmp_path, issue="FOUNDRY-42", environ={})

    assert payload["issue"] == {
        "issue_id": "FOUNDRY-42", "halted": False, "halt_generation": 0,
        "total_escalations": 0,
    }
    assert not issue_path.exists()
    assert not state_dir.exists()


def test_doctor_reports_escalated_and_halted_issue_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    store = EscalationStore.for_root(tmp_path)
    for tier in ("economy", "economy", "balanced", "balanced", "frontier", "frontier"):
        store.record_failure("FOUNDRY-77", "scout", "test_red", tier)

    payload = doctor.routing_diagnostics(tmp_path, issue="FOUNDRY-77", environ={})

    assert payload["issue"]["halted"] is True
    assert payload["issue"]["halt_generation"] == 1
    assert payload["issue"]["total_escalations"] == 2


def test_doctor_renders_successful_fallback_as_a_visible_warning(tmp_path, capsys):
    payload = doctor.routing_diagnostics(
        tmp_path,
        environ={"FOUNDRY_CODEX_AVAILABLE_MODELS": "gpt-5.6-luna,gpt-5.6-sol"},
    )
    implementer = next(
        item for item in payload["hosts"]["codex"]["routes"]
        if item["role"] == "implementer"
    )

    assert implementer["ok"] is True
    assert implementer["warnings"][0]["code"] == "MODEL_FALLBACK_DOWN"

    doctor._print_routing(payload)
    output = capsys.readouterr().out
    assert "MODEL_FALLBACK_DOWN" in output
    assert "Le modèle de balanced est indisponible" in output


def test_doctor_renders_policy_neutralizing_override_as_health_degradation(
    tmp_path, capsys,
):
    sentinel = "MODEL_OVERRIDE_VALUE_MUST_NEVER_APPEAR"
    payload = doctor.routing_diagnostics(
        tmp_path,
        environ={"CLAUDE_CODE_SUBAGENT_MODEL": sentinel},
    )
    routes = payload["hosts"]["claude"]["routes"]

    assert all(
        warning["code"] == "HOST_OVERRIDE_NEUTRALIZES_POLICY"
        for route in routes for warning in route["warnings"]
    )

    doctor._print_routing(payload)
    output = capsys.readouterr().out
    assert "🔴   claude · overrides connus=CLAUDE_CODE_SUBAGENT_MODEL" in output
    assert "HOST_OVERRIDE_NEUTRALIZES_POLICY" in output
    assert sentinel not in output


@pytest.mark.parametrize(
    ("host", "environ", "codex_profile", "override_name"),
    [
        (
            "claude",
            {"CLAUDE_CODE_SUBAGENT_MODEL": "secret", "FOUNDRY_CLAUDE_AVAILABLE_MODELS": " , "},
            None,
            "CLAUDE_CODE_SUBAGENT_MODEL",
        ),
        (
            "codex",
            {"FOUNDRY_CODEX_AVAILABLE_MODELS": " , "},
            {"model": "secret"},
            "profile.model",
        ),
    ],
)
def test_doctor_keeps_override_health_degraded_when_availability_observation_fails(
    tmp_path, capsys, host, environ, codex_profile, override_name,
):
    payload = doctor.routing_diagnostics(
        tmp_path, environ=environ, codex_profile=codex_profile,
    )
    routes = payload["hosts"][host]["routes"]

    assert "secret" not in str(payload)
    assert all(route["ok"] is False for route in routes)
    assert {route["code"] for route in routes} == {"HOST_OBSERVATION_INVALID"}
    assert all(
        [warning["code"] for warning in route["warnings"]]
        == ["HOST_OVERRIDE_NEUTRALIZES_POLICY"]
        for route in routes
    )

    doctor._print_routing(payload)
    output = capsys.readouterr().out
    assert f"🔴   {host} · overrides connus={override_name}" in output
    assert "HOST_OBSERVATION_INVALID" in output
    assert "HOST_OVERRIDE_NEUTRALIZES_POLICY" in output
    assert "secret" not in output


@pytest.mark.parametrize(
    ("host", "environ", "codex_profile", "override_name"),
    [
        (
            "claude",
            {
                "CLAUDE_CODE_SUBAGENT_MODEL": "secret",
                "FOUNDRY_CLAUDE_AVAILABLE_MODELS": "unroutable-model",
            },
            None,
            "CLAUDE_CODE_SUBAGENT_MODEL",
        ),
        (
            "codex",
            {"FOUNDRY_CODEX_AVAILABLE_MODELS": "unroutable-model"},
            {"model": "secret"},
            "profile.model",
        ),
    ],
)
def test_doctor_keeps_override_health_degraded_when_no_route_is_available(
    tmp_path, capsys, host, environ, codex_profile, override_name,
):
    payload = doctor.routing_diagnostics(
        tmp_path, environ=environ, codex_profile=codex_profile,
    )
    routes = payload["hosts"][host]["routes"]

    assert "secret" not in str(payload)
    assert all(route["ok"] is False for route in routes)
    assert {route["code"] for route in routes} == {"ROUTING_UNAVAILABLE"}
    assert all(
        [warning["code"] for warning in route["warnings"]]
        == ["HOST_OVERRIDE_NEUTRALIZES_POLICY"]
        for route in routes
    )

    doctor._print_routing(payload)
    captured = capsys.readouterr()
    assert f"🔴   {host} · overrides connus={override_name}" in captured.out
    assert "ROUTING_UNAVAILABLE" in captured.out
    assert "HOST_OVERRIDE_NEUTRALIZES_POLICY" in captured.out
    assert "secret" not in captured.out
    assert "secret" not in captured.err


@pytest.mark.parametrize(
    ("host", "environ", "codex_profile", "override_name"),
    [
        (
            "claude",
            {"CLAUDE_CODE_SUBAGENT_MODEL": "secret"},
            None,
            "CLAUDE_CODE_SUBAGENT_MODEL",
        ),
        ("codex", {}, {"model": "secret"}, "profile.model"),
    ],
)
def test_doctor_keeps_override_health_degraded_when_route_config_is_invalid(
    tmp_path, capsys, host, environ, codex_profile, override_name,
):
    (tmp_path / ".foundry").mkdir()
    (tmp_path / ".foundry" / "model-routing.json").write_text(
        json.dumps({"mappings": {host: {"apex": {"effort": "low"}}}}),
        encoding="utf-8",
    )

    payload = doctor.routing_diagnostics(
        tmp_path, environ=environ, codex_profile=codex_profile,
    )
    route = next(
        route for route in payload["hosts"][host]["routes"]
        if route["role"] == "architect"
    )

    assert "secret" not in str(payload)
    assert route["ok"] is False
    assert route["code"] == "ROUTING_INVALID"
    assert [warning["code"] for warning in route["warnings"]] == [
        "HOST_OVERRIDE_NEUTRALIZES_POLICY",
    ]

    doctor._print_routing(payload)
    captured = capsys.readouterr()
    assert f"🔴   {host} · overrides connus={override_name}" in captured.out
    assert "ROUTING_INVALID" in captured.out
    assert "HOST_OVERRIDE_NEUTRALIZES_POLICY" in captured.out
    assert "secret" not in captured.out
    assert "secret" not in captured.err


def test_doctor_renders_halted_issue_as_unhealthy(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    store = EscalationStore.for_root(tmp_path)
    for tier in ("economy", "economy", "balanced", "balanced", "frontier", "frontier"):
        store.record_failure("FOUNDRY-78", "scout", "test_red", tier)

    payload = doctor.routing_diagnostics(tmp_path, issue="FOUNDRY-78", environ={})
    doctor._print_routing(payload)
    output = capsys.readouterr().out

    assert payload["issue"]["halted"] is True
    assert "🔴 Escalade FOUNDRY-78" in output
    assert "arrêtée" in output


def test_doctor_reports_unavailable_gate_without_silent_fallback(tmp_path):
    payload = doctor.routing_diagnostics(
        tmp_path,
        environ={"FOUNDRY_CODEX_AVAILABLE_MODELS": "gpt-5.6-luna"},
    )
    reviewer = next(
        item for item in payload["hosts"]["codex"]["routes"]
        if item["role"] == "reviewer"
    )

    assert reviewer["ok"] is False
    assert reviewer["code"] == "ROUTING_UNAVAILABLE"
    assert "gate 'reviewer'" in reviewer["detail"]


def test_local_scout_doctor_transport_states_are_probe_only(tmp_path):
    project = tmp_path / ".foundry"
    project.mkdir()
    (project / "local-scout.json").write_text(json.dumps({"model": "m"}), encoding="utf-8")
    environ = {"FOUNDRY_LOCAL_SCOUT_ENABLED": "1", "FOUNDRY_LOCAL_SCOUT_BASE_URL": "http://127.0.0.1:11434"}
    calls = []
    configured = doctor.local_scout_diagnostic(tmp_path, environ=environ)
    available = doctor.local_scout_diagnostic(tmp_path, environ=environ, transport_probe=lambda *_: calls.append("tcp") or True)
    unavailable = doctor.local_scout_diagnostic(tmp_path, environ=environ, transport_probe=lambda *_: False)
    assert configured["status"] == "configured"
    assert available["status"] == "available" and calls == ["tcp"]
    assert unavailable["status"] == "unavailable"


def test_local_scout_doctor_docstring_matches_pure_helper_and_cli_probe_contract():
    contract = doctor.local_scout_diagnostic.__doc__ or ""

    assert "pure helper" in contract
    assert "CLI supplies" in contract
