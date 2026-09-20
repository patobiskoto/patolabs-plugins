"""Offline execution/reporting tests for the additive FOUNDRY-46 v2 layer."""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from unittest import mock

import pytest

from foundry.effort_policy import EffortScope


ROOT = Path(__file__).parents[1]
PILOT_ROOT = ROOT / "benchmarks" / "foundry-46"
REPOSITORY = ROOT.parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


EXEC = _load_module("foundry46_execution_v2_test", PILOT_ROOT / "run-pilot-v2.py")
REPORT = _load_module("foundry46_report_v2_test", PILOT_ROOT / "compile-pilot-v2.py")


def _version_output(host: str, version: str | None = None) -> bytes:
    version = version or EXEC.expected_host_version(host)
    if host == "claude":
        return f"{version} (Claude Code)\n".encode("ascii")
    return f"codex-cli {version}\n".encode("ascii")


class _VersionProcess:
    def __init__(self, raw):
        self.stdout = io.BytesIO(raw)
        self.returncode = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        assert 0 < timeout <= EXEC.VERSION_PROBE_TIMEOUT_SECONDS
        return self.returncode


def _verified_versions(authorization, *, paths=None, outputs=None):
    paths = paths or {
        host: Path(sys.executable).resolve() for host in EXEC.F61.HOSTS
    }
    outputs = outputs or {}
    calls = []

    def process_factory(command, **_kwargs):
        host = EXEC.F61.HOSTS[len(calls)]
        calls.append(command)
        assert command == (str(paths[host]), "--version")
        return _VersionProcess(outputs.get(host, _version_output(host)))

    with (
        mock.patch.object(
            EXEC.shutil, "which", side_effect=lambda host: str(paths[host]),
        ),
        mock.patch.object(EXEC.subprocess, "Popen", side_effect=process_factory),
    ):
        return EXEC.verify_native_host_versions(authorization)


def _claude_stdout() -> bytes:
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "uuid": "transient-uuid",
        "session_id": "transient-session",
        "duration_ms": 125.0,
        "duration_api_ms": 100.0,
        "is_error": False,
        "num_turns": 1,
        "result": "PRIVATE_RAW_CLAUDE_RESULT",
        "stop_reason": None,
        "total_cost_usd": 0.01,
        "usage": {
            "input_tokens": 12,
            "output_tokens": 8,
            "cache_creation_input_tokens": 3,
            "cache_read_input_tokens": 5,
        },
        "modelUsage": {
            "pinned-model": {
                "inputTokens": 12,
                "outputTokens": 8,
                "cacheReadInputTokens": 5,
                "cacheCreationInputTokens": 3,
                "webSearchRequests": 0,
            },
        },
        "permission_denials": [],
    }, separators=(",", ":")).encode("utf-8")


def _codex_stdout(*, tool: bool = False) -> bytes:
    item = (
        {"id": "tool-1", "type": "command_execution", "text": "PRIVATE_TOOL"}
        if tool else
        {"id": "answer-1", "type": "agent_message", "text": "PRIVATE_CODEX_TEXT"}
    )
    events = [
        {"type": "thread.started", "thread_id": "transient-thread"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": item},
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 12,
                "cached_input_tokens": 5,
                "output_tokens": 8,
                "reasoning_output_tokens": 2,
            },
        },
    ]
    return b"".join(
        json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n"
        for event in events
    )


def _one_context(tmp_path: Path) -> EXEC.PairContext:
    binding = json.loads((EXEC.F61_ROOT / EXEC.F61.PACKETS_FILE).read_text())["bindings"][0]
    return EXEC.PairContext(
        binding["case_id"],
        binding["revision"],
        binding["repetition"],
        binding["packet_id"],
        "baseline-v1",
        "Goal:\nFrozen pair.\nInputs:\nOffline.\nConstraints:\nRead only.\nDone when:\nBound.",
        tmp_path,
        True,
    )


def test_operator_opt_in_is_required_before_any_preflight_or_host_call(tmp_path, monkeypatch):
    calls = []

    def forbidden_preflight(*_args, **_kwargs):
        calls.append("preflight")
        raise AssertionError("preflight must not run")

    monkeypatch.setattr(EXEC.F61, "run_preflight", forbidden_preflight)
    with pytest.raises(EXEC.PilotExecutionV2Error, match="authorization"):
        EXEC.execute_campaign(
            tmp_path / "evidence.jsonl",
            authorization=object(),
            host_runner=lambda *_args: calls.append("host"),
        )

    for execute, operator_opt_in in ((False, False), (True, False), (False, True)):
        with pytest.raises(EXEC.PilotExecutionV2Error, match="opt-ins"):
            EXEC._issue_execution_authorization(
                execute=execute, operator_opt_in=operator_opt_in,
            )

    assert calls == []
    assert not (tmp_path / "evidence.jsonl").exists()
    assert EXEC.main([
        "--execute", "--evidence", str(tmp_path / "cli.jsonl"),
    ]) == 1
    assert not (tmp_path / "cli.jsonl").exists()


def test_native_boundary_denies_missing_forged_and_mutated_authorization(tmp_path):
    coordinate = EXEC.bind_coordinate(
        "claude", EXEC._profiles()["claude"][0], _one_context(tmp_path),
    )
    process_calls = []

    def forbidden_process(*_args, **_kwargs):
        process_calls.append("process")
        raise AssertionError("authorization must fail before Popen")

    with pytest.raises(EXEC.PilotExecutionV2Error, match="authorization"):
        EXEC.ExecutionAuthorization(object())
    forged = object.__new__(EXEC.ExecutionAuthorization)
    mutated = EXEC._issue_execution_authorization(
        execute=True, operator_opt_in=True,
    )
    object.__setattr__(mutated, "_marker", object())

    for candidate in (None, object(), forged, mutated):
        with pytest.raises(EXEC.PilotExecutionV2Error, match="authorization"):
            EXEC.run_native_host(
                coordinate, 30.0, candidate, process_factory=forbidden_process,
            )

    assert process_calls == []

    authorization = EXEC._issue_execution_authorization(
        execute=True, operator_opt_in=True,
    )
    with pytest.raises(EXEC.PilotExecutionV2Error, match="host version"):
        EXEC.run_native_host(
            coordinate, 30.0, authorization, process_factory=forbidden_process,
        )
    assert process_calls == []


def test_native_version_probe_is_bounded_exact_and_safely_projected(
    tmp_path, monkeypatch,
):
    authorization = EXEC._issue_execution_authorization(
        execute=True, operator_opt_in=True,
    )
    calls = []
    resolver_calls = []
    executables = {}
    for host in EXEC.F61.HOSTS:
        path = tmp_path / host
        path.write_bytes(b"offline test executable")
        path.chmod(0o700)
        executables[host] = path.resolve()

    def process_factory(command, **kwargs):
        calls.append((command, kwargs))
        host = next(
            name for name, path in executables.items()
            if str(path) == command[0]
        )
        return _VersionProcess(_version_output(host))

    def resolver(host):
        resolver_calls.append(host)
        return str(executables[host])

    monkeypatch.setattr(EXEC.shutil, "which", resolver)
    monkeypatch.setattr(EXEC.subprocess, "Popen", process_factory)
    proofs = EXEC.verify_native_host_versions(authorization)

    assert resolver_calls == ["claude", "codex"]
    assert [call[0] for call in calls] == [
        (str(executables["claude"]), "--version"),
        (str(executables["codex"]), "--version"),
    ]
    assert all(call[1]["stderr"] == EXEC.subprocess.DEVNULL for call in calls)
    assert {
        host: EXEC._host_version_projection(host, proofs[host])
        for host in EXEC.F61.HOSTS
    } == {
        "claude": {
            "expected": "2.1.224",
            "observed": "2.1.224",
            "provenance": "native_cli_version_probe",
            "verified_before_provider_invocation": True,
        },
        "codex": {
            "expected": "0.147.0",
            "observed": "0.147.0",
            "provenance": "native_cli_version_probe",
            "verified_before_provider_invocation": True,
        },
    }


def test_path_and_cwd_substitution_cannot_switch_verified_executable(
    tmp_path, monkeypatch,
):
    authorization = EXEC._issue_execution_authorization(
        execute=True, operator_opt_in=True,
    )
    trusted = tmp_path / "trusted"
    attacker = tmp_path / "attacker"
    trusted.mkdir()
    attacker.mkdir()
    trusted_paths = {}
    for host in EXEC.F61.HOSTS:
        path = trusted / host
        path.write_bytes(f"trusted-{host}".encode("ascii"))
        path.chmod(0o700)
        trusted_paths[host] = path.resolve()
        substitute = attacker / host
        substitute.write_bytes(f"attacker-{host}".encode("ascii"))
        substitute.chmod(0o700)
    resolutions = []
    probes = []

    def resolver(host):
        resolutions.append(host)
        return str(trusted_paths[host])

    def probe_process(command, **_kwargs):
        host = EXEC.F61.HOSTS[len(probes)]
        probes.append(command[0])
        return _VersionProcess(_version_output(host))

    monkeypatch.setattr(EXEC.shutil, "which", resolver)
    monkeypatch.setattr(EXEC.subprocess, "Popen", probe_process)
    versions = EXEC.verify_native_host_versions(authorization)
    monkeypatch.setenv("PATH", str(attacker))
    monkeypatch.chdir(attacker)
    monkeypatch.setattr(EXEC, "_trusted_secret_snapshot", lambda: ())
    provider_commands = []

    def provider_process(command, **_kwargs):
        provider_commands.append(command)
        raise OSError("offline refusal after argv capture")

    coordinate = EXEC.bind_coordinate(
        "claude", EXEC._profiles()["claude"][0], _one_context(tmp_path),
    )
    outcome = EXEC.run_native_host(
        coordinate,
        30.0,
        authorization,
        versions["claude"],
        process_factory=provider_process,
    )

    assert resolutions == ["claude", "codex"]
    assert probes == [
        str(trusted_paths["claude"]), str(trusted_paths["codex"]),
    ]
    assert provider_commands[0][0] == str(trusted_paths["claude"])
    assert provider_commands[0][0] != str((attacker / "claude").resolve())
    assert outcome == EXEC.ProcessOutcome(
        "failed", "host", None, outcome.duration_seconds, "HOST_PROCESS_FAILED",
    )


def test_executable_replaced_during_secret_preparation_fails_before_popen(
    tmp_path, monkeypatch,
):
    authorization = EXEC._issue_execution_authorization(
        execute=True, operator_opt_in=True,
    )
    paths = {}
    for host in EXEC.F61.HOSTS:
        path = tmp_path / host
        path.write_bytes(f"trusted-{host}".encode("ascii"))
        path.chmod(0o700)
        paths[host] = path.resolve()
    versions = _verified_versions(authorization, paths=paths)
    coordinate = EXEC.bind_coordinate(
        "claude", EXEC._profiles()["claude"][0], _one_context(tmp_path),
    )
    snapshot_calls = []

    def replacing_secret_snapshot():
        snapshot_calls.append("snapshot")
        paths["claude"].write_bytes(b"replacement-during-secret-capture")
        paths["claude"].chmod(0o700)
        return ()

    monkeypatch.setattr(EXEC, "_trusted_secret_snapshot", replacing_secret_snapshot)
    provider_calls = []

    def forbidden_provider(*_args, **_kwargs):
        provider_calls.append("provider")
        raise AssertionError("final identity check must precede Popen")

    with pytest.raises(EXEC.PilotExecutionV2Error, match="identity changed"):
        EXEC.run_native_host(
            coordinate,
            30.0,
            authorization,
            versions["claude"],
            process_factory=forbidden_provider,
        )

    assert snapshot_calls == ["snapshot"]
    assert provider_calls == []


@pytest.mark.parametrize("candidate", [None, "relative/claude"])
def test_missing_or_relative_executable_resolution_fails_closed(candidate, monkeypatch):
    monkeypatch.setattr(EXEC.shutil, "which", lambda _host: candidate)
    with pytest.raises(EXEC.PilotExecutionV2Error, match="resolution"):
        EXEC.resolve_native_host_executable("claude")


def test_unsafe_or_changed_executable_and_mutated_capability_fail_before_provider(
    tmp_path, monkeypatch,
):
    non_executable = tmp_path / "non-executable"
    non_executable.write_bytes(b"not executable")
    non_executable.chmod(0o600)
    monkeypatch.setattr(
        EXEC.shutil, "which", lambda _host: str(non_executable.resolve()),
    )
    with pytest.raises(EXEC.PilotExecutionV2Error, match="unsafe"):
        EXEC.resolve_native_host_executable("claude")

    authorization = EXEC._issue_execution_authorization(
        execute=True, operator_opt_in=True,
    )
    paths = {}
    for host in EXEC.F61.HOSTS:
        path = tmp_path / host
        path.write_bytes(f"original-{host}".encode("ascii"))
        path.chmod(0o700)
        paths[host] = path.resolve()
    monkeypatch.setattr(EXEC.shutil, "which", lambda host: str(paths[host]))
    probe_calls = []

    def probe_process(command, **_kwargs):
        host = EXEC.F61.HOSTS[len(probe_calls)]
        probe_calls.append(command)
        return _VersionProcess(_version_output(host))

    monkeypatch.setattr(EXEC.subprocess, "Popen", probe_process)
    versions = EXEC.verify_native_host_versions(authorization)
    coordinate = EXEC.bind_coordinate(
        "claude", EXEC._profiles()["claude"][0], _one_context(tmp_path),
    )
    monkeypatch.setattr(EXEC, "_trusted_secret_snapshot", lambda: ())
    provider_calls = []

    def forbidden_provider(*_args, **_kwargs):
        provider_calls.append("provider")
        raise AssertionError("identity must fail before provider execution")

    forged = object.__new__(EXEC.VerifiedHostVersion)
    with pytest.raises(EXEC.PilotExecutionV2Error, match="host version"):
        EXEC.VerifiedHostVersion()
    with pytest.raises(EXEC.PilotExecutionV2Error, match="host version"):
        EXEC.run_native_host(
            coordinate, 30.0, authorization, forged,
            process_factory=forbidden_provider,
        )
    assert not hasattr(EXEC, "_issue_verified_host_version")

    class ApparentCapability:
        pass

    apparent = ApparentCapability()
    apparent.host = "claude"
    apparent.expected = EXEC.expected_host_version("claude")
    apparent.observed = apparent.expected
    apparent.executable = paths["claude"]
    apparent._executable_signature = ("forged",)
    with pytest.raises(EXEC.PilotExecutionV2Error, match="host version"):
        EXEC.run_native_host(
            coordinate, 30.0, authorization, apparent,
            process_factory=forbidden_provider,
        )

    mutated = versions["claude"]
    for name, replacement in (
        ("host", "codex"),
        ("expected", EXEC.expected_host_version("codex")),
        ("observed", EXEC.expected_host_version("codex")),
        ("executable", object()),
        ("_executable_signature", ("forged",)),
    ):
        with pytest.raises(AttributeError):
            object.__setattr__(mutated, name, replacement)

    changed = versions["codex"]
    paths["codex"].write_bytes(b"changed-codex-binary-identity")
    paths["codex"].chmod(0o700)
    codex_coordinate = EXEC.bind_coordinate(
        "codex", EXEC._profiles()["codex"][0], _one_context(tmp_path),
    )
    with pytest.raises(EXEC.PilotExecutionV2Error, match="identity changed"):
        EXEC.run_native_host(
            codex_coordinate, 30.0, authorization, changed,
            process_factory=forbidden_provider,
        )
    assert provider_calls == []


@pytest.mark.parametrize(
    ("host", "raw"),
    [
        ("claude", b"2.1.223 (Claude Code)\n"),
        ("codex", b"codex-cli 0.146.0\n"),
        ("claude", b"PRIVATE unexpected output"),
        ("codex", b"x" * (EXEC.MAX_VERSION_OUTPUT_BYTES + 1)),
    ],
)
def test_native_version_mismatch_or_unbounded_output_fails_closed(
    host, raw, monkeypatch,
):
    authorization = EXEC._issue_execution_authorization(
        execute=True, operator_opt_in=True,
    )

    calls = []

    def probe_process(_command, **_kwargs):
        candidate = EXEC.F61.HOSTS[len(calls)]
        calls.append(candidate)
        return _VersionProcess(raw if candidate == host else _version_output(candidate))

    monkeypatch.setattr(EXEC.shutil, "which", lambda _host: sys.executable)
    monkeypatch.setattr(EXEC.subprocess, "Popen", probe_process)
    with pytest.raises(EXEC.PilotExecutionV2Error, match="version"):
        EXEC.verify_native_host_versions(authorization)


def test_version_mismatch_refuses_campaign_before_provider_invocation(tmp_path):
    authorization = EXEC._issue_execution_authorization(
        execute=True, operator_opt_in=True,
    )
    host_calls = []
    probe_calls = []

    def probe_process(_command, **_kwargs):
        host = EXEC.F61.HOSTS[len(probe_calls)]
        probe_calls.append(host)
        version = "0.146.0" if host == "codex" else None
        return _VersionProcess(_version_output(host, version))

    def mismatching_verifier(supplied):
        with (
            mock.patch.object(EXEC.shutil, "which", return_value=sys.executable),
            mock.patch.object(EXEC.subprocess, "Popen", side_effect=probe_process),
        ):
            return EXEC.verify_native_host_versions(supplied)

    with pytest.raises(EXEC.PilotExecutionV2Error, match="frozen contract"):
        EXEC.execute_campaign(
            tmp_path / "evidence.jsonl",
            authorization=authorization,
            version_verifier=mismatching_verifier,
            host_runner=lambda *_args: host_calls.append("provider"),
        )

    assert host_calls == []
    assert not (tmp_path / "evidence.jsonl").exists()


def test_configured_and_structural_secrets_are_redacted_before_transmission(monkeypatch):
    configured_secret = "configured-secret-value-46"
    structural_secret = "structural-secret-value-46"
    source = (
        "diff --git a/example.txt b/example.txt\n"
        "--- a/example.txt\n"
        "+++ b/example.txt\n"
        "@@ -0,0 +1,2 @@\n"
        f"+plain={configured_secret}\n"
        f"+API_TOKEN={structural_secret}\n"
    ).encode("utf-8")
    monkeypatch.setattr(
        EXEC.F61.local_scout,
        "configured_secret_values",
        lambda: (configured_secret,),
    )

    capture, secrets = EXEC._capture_redacted_diff(source)
    captured = EXEC.F61.lean_context.capture_context(diff=capture)
    packet = EXEC.F61.lean_context.build_packet(
        EXEC.F61.lean_context.BASELINE_V1, captured,
    )
    task_packet = EXEC.F61._private_packet(
        EXEC.F61.lean_context.cloud_fragments(packet),
    )
    EXEC._validate_transmission_packet(task_packet, secrets)

    materialized = "".join(item.raw_excerpt for item in capture.evidence)
    assert configured_secret not in materialized
    assert structural_secret not in materialized
    assert configured_secret not in task_packet
    assert structural_secret not in task_packet
    assert "[REDACTED]" in task_packet


@pytest.mark.parametrize("values", [["secret"], ("\ud800",)])
def test_invalid_secret_snapshot_fails_closed(values, monkeypatch):
    monkeypatch.setattr(
        EXEC.F61.local_scout, "configured_secret_values", lambda: values,
    )

    with pytest.raises(EXEC.PilotExecutionV2Error, match="secret snapshot"):
        EXEC._trusted_secret_snapshot()


def test_redaction_mutation_fails_closed_before_packet_transmission(monkeypatch):
    configured_secret = "configured-secret-value-46"
    source = (
        "diff --git a/example.txt b/example.txt\n"
        "--- a/example.txt\n"
        "+++ b/example.txt\n"
        "@@ -0,0 +1 @@\n"
        f"+plain={configured_secret}\n"
    ).encode("utf-8")
    monkeypatch.setattr(
        EXEC.F61.local_scout,
        "configured_secret_values",
        lambda: (configured_secret,),
    )
    capture, _secrets = EXEC._capture_redacted_diff(source)
    leaked = replace(
        capture.evidence[0], raw_excerpt=f"+plain={configured_secret}\n",
    )
    forged = replace(capture, evidence=(leaked, *capture.evidence[1:]))
    monkeypatch.setattr(EXEC.F61.local_scout, "capture_diff", lambda *_args, **_kwargs: forged)

    with pytest.raises(EXEC.PilotExecutionV2Error, match="redaction"):
        EXEC._capture_redacted_diff(source)
    with pytest.raises(EXEC.PilotExecutionV2Error, match="redaction"):
        EXEC._validate_transmission_packet(
            f"Goal:\n{configured_secret}", (configured_secret,),
        )

    context = replace(
        _one_context(Path(".")),
        task_packet=f"Goal:\nplain={configured_secret}",
    )
    coordinate = EXEC.bind_coordinate(
        "claude", EXEC._profiles()["claude"][0], context,
    )
    authorization = EXEC._issue_execution_authorization(
        execute=True, operator_opt_in=True,
    )
    host_version = _verified_versions(authorization)["claude"]
    process_calls = []

    def forbidden_process(*_args, **_kwargs):
        process_calls.append("process")
        raise AssertionError("redaction must fail before Popen")

    with pytest.raises(EXEC.PilotExecutionV2Error, match="redaction"):
        EXEC.run_native_host(
            coordinate, 30.0, authorization, host_version,
            process_factory=forbidden_process,
        )
    assert process_calls == []


def test_every_native_command_composes_the_exact_frozen_binding(tmp_path):
    context = _one_context(tmp_path)
    profiles = EXEC._profiles()

    coordinates = [
        EXEC.bind_coordinate(host, profile, context)
        for host in EXEC.F61.HOSTS
        for profile in profiles[host]
    ]

    assert len(coordinates) == 9
    for coordinate in coordinates:
        assert coordinate.context.task_packet is context.task_packet
        if coordinate.host == "claude":
            assert coordinate.command[0:5] == (
                "claude", "-p", "--safe-mode", "--output-format", "json",
            )
            assert coordinate.command[5:9] == (
                "--model", coordinate.profile["invocation_model"],
                "--effort", coordinate.profile["reasoning_effort"],
            )
            assert coordinate.command[-3:] == ("--tools", "", "--no-session-persistence")
        else:
            assert coordinate.command[0:2] == ("codex", "exec")
            assert "--json" in coordinate.command
            assert coordinate.command[-1] == "-"
        assert coordinate.context.revision == coordinate.binding.revision
        assert coordinate.context.context_policy == coordinate.binding.context_policy


@pytest.mark.parametrize(
    "changes",
    [
        {"packet_id": "f61-packet-002"},
        {"revision": "0" * 40},
        {"context_policy": "lean-v1"},
        {"task_packet": "\ud800"},
        {"isolated_claim_verified": False},
    ],
)
def test_coordinate_and_context_drift_fail_before_execution(tmp_path, changes):
    context = _one_context(tmp_path)
    values = {name: getattr(context, name) for name in context.__dataclass_fields__}
    values.update(changes)
    candidate = EXEC.PairContext(**values)
    profile = EXEC._profiles()["claude"][0]

    if changes == {"isolated_claim_verified": False}:
        with pytest.raises(EXEC.PilotExecutionV2Error, match="paired context"):
            EXEC._validate_contexts([candidate] * 12)
    else:
        with pytest.raises(
            (EXEC.PilotExecutionV2Error, EXEC.F61.PairedPilotV2Error),
            match="packet|context|binding",
        ):
            EXEC.bind_coordinate("claude", profile, candidate)


def test_native_schema_drift_is_sanitized_unknown_with_null_metrics(tmp_path):
    context = _one_context(tmp_path)
    profiles = EXEC._profiles()
    claude = EXEC.bind_coordinate("claude", profiles["claude"][0], context)
    codex = EXEC.bind_coordinate("codex", profiles["codex"][0], context)

    claude_observation = EXEC.parse_claude_outcome(EXEC.ProcessOutcome(
        "completed", "none", b'{"result":"PRIVATE_INVALID"}', 0.25,
    ))
    codex_observation = EXEC.parse_codex_outcome(
        EXEC.ProcessOutcome("completed", "none", _codex_stdout(tool=True), 0.25),
        codex.binding,
    )
    rows = [
        EXEC.build_evidence_row(0, claude, claude_observation),
        EXEC.build_evidence_row(1, codex, codex_observation),
    ]

    for row in rows:
        assert row["terminal"] == {"status": "unknown", "failure_class": "unknown"}
        assert all(row["metrics"][name] == {
            "value": None, "provenance": "unavailable",
        } for name in EXEC.TOKEN_METRICS)
    encoded = json.dumps(rows)
    assert "PRIVATE_INVALID" not in encoded
    assert "PRIVATE_TOOL" not in encoded


@pytest.mark.parametrize(
    "raw",
    [
        b'{"type":"result","type":"result"}',
        b'{"type":"result","total_cost_usd":NaN}',
        json.dumps({**json.loads(_claude_stdout()), "result": "\ud800"}).encode("utf-8"),
    ],
)
def test_claude_duplicate_nonstandard_and_surrogate_json_fail_closed(raw):
    observation = EXEC.parse_claude_outcome(EXEC.ProcessOutcome(
        "completed", "none", raw, 0.25,
    ))

    assert observation.terminal == {"status": "unknown", "failure_class": "unknown"}
    assert observation.diagnostic_code == "CLAUDE_OUTPUT_SCHEMA_INVALID"
    assert set(observation.tokens.values()) == {None}


def test_complete_mocked_campaign_and_report_are_reproducible_and_private(tmp_path):
    evidence = tmp_path / "evidence-v2.jsonl"
    calls = []
    authorization = EXEC._issue_execution_authorization(
        execute=True, operator_opt_in=True,
    )
    versions = _verified_versions(authorization)

    def fake_host(
        coordinate, timeout_seconds, supplied_authorization, supplied_version,
    ):
        assert timeout_seconds == 30.0
        assert supplied_authorization is authorization
        EXEC._validate_execution_authorization(supplied_authorization)
        assert supplied_version is versions[coordinate.host]
        EXEC._validate_verified_host_version(coordinate.host, supplied_version)
        assert coordinate.context.checkout_root.is_dir()
        calls.append(coordinate)
        raw = _claude_stdout() if coordinate.host == "claude" else _codex_stdout()
        return EXEC.ProcessOutcome("completed", "none", raw, 0.25)

    summary = EXEC.execute_campaign(
        evidence,
        authorization=authorization,
        timeout_seconds=30.0,
        host_runner=fake_host,
        version_verifier=lambda _authorization: versions,
    )
    rows = EXEC.read_evidence(evidence, require_complete=True)

    assert summary == {
        "artifact": "foundry.model_effort_context.paired_pilot_execution_summary",
        "version": 2,
        "status": "complete",
        "attempts": 108,
        "claude_attempts": 60,
        "codex_attempts": 48,
        "host_versions": {
            "claude": {
                "expected": "2.1.224",
                "observed": "2.1.224",
                "provenance": "native_cli_version_probe",
                "verified_before_provider_invocation": True,
            },
            "codex": {
                "expected": "0.147.0",
                "observed": "0.147.0",
                "provenance": "native_cli_version_probe",
                "verified_before_provider_invocation": True,
            },
        },
        "automatic_retries": 0,
        "corrections": 0,
        "escalations": 0,
        "production_state_mutations": 0,
        "raw_retained": False,
    }
    assert len(calls) == len(rows) == 108
    assert {
        packet_id: len({id(call.context.task_packet) for call in calls
                        if call.context.packet_id == packet_id})
        for packet_id in {call.context.packet_id for call in calls}
    } == {f"f61-packet-{ordinal:03d}": 1 for ordinal in range(1, 13)}
    raw = evidence.read_bytes()
    for forbidden in (
        b"PRIVATE_RAW_CLAUDE_RESULT", b"PRIVATE_CODEX_TEXT", b"transient-thread",
        b"transient-session", b"packet_sha256", b'"command"', b'"path"',
        b'"executable"', str(Path(sys.executable).resolve()).encode("utf-8"),
    ):
        assert forbidden not in raw
    assert all(row["metrics"]["cumulative_cost_usd"] == {
        "value": None, "provenance": "unavailable",
    } for row in rows)
    assert all(row["loop"]["escalations"]["value"] == 0 for row in rows)
    assert all(row["gate"]["preserved"] is True for row in rows)
    assert all(
        row["host_version"]["observed"]
        == EXEC.expected_host_version(row["host"])
        for row in rows
    )

    results = REPORT.build_results(rows)
    assert results["status"] == "observed_complete_matrix"
    assert results["matrix"]["complete_frozen_v2_matrix"] is True
    assert results["host_reports"]["claude"]["attempts"] == 60
    assert results["host_reports"]["codex"]["attempts"] == 48
    assert results["runtime_contract"]["status"] == "verified"
    assert results["runtime_contract"]["diagnostic_attempts"] == 0
    assert results["host_reports"]["claude"]["provider_reported_cost_usd"]["complete"] is True
    assert results["host_reports"]["codex"]["provider_reported_cost_usd"] == {
        "value": None, "provenance": "unavailable", "complete": False,
    }
    assert results["host_reports"]["claude"]["cumulative_cost_usd"]["value"] is None
    assert results["aggregate"]["value"] is None
    assert results["verdict"] == "inconclusive"
    assert all(
        comparison["causal_claim"] is False
        for host in results["comparisons"].values()
        for comparison in host
    )

    output = tmp_path / "compiled"
    written, report = REPORT.write_outputs(evidence, output)
    checked, checked_report = REPORT.check_outputs(evidence, output)
    assert written == checked == results
    assert report == checked_report == REPORT.render_report(results)
    assert "INCONCLUSIVE" in report
    assert "aggregate is `null`" in report


def test_claude_cost_requires_host_provenance_and_compiler_never_reclassifies(tmp_path):
    coordinate = EXEC.bind_coordinate(
        "claude", EXEC._profiles()["claude"][0], _one_context(tmp_path),
    )
    observation = EXEC.parse_claude_outcome(EXEC.ProcessOutcome(
        "completed", "none", _claude_stdout(), 0.25,
    ))
    row = EXEC.build_evidence_row(0, coordinate, observation)

    assert row["metrics"]["provider_reported_cost_usd"] == {
        "value": 0.01, "provenance": "host_reported",
    }
    assert REPORT._complete_total([row], "provider_reported_cost_usd") == {
        "value": 0.01, "provenance": "host_reported", "complete": True,
    }
    forged_observation = replace(
        observation,
        provider_reported_cost_usd={
            "value": 0.01, "provenance": "client_observed",
        },
    )
    with pytest.raises(EXEC.PilotExecutionV2Error, match="cost provenance"):
        EXEC.build_evidence_row(0, coordinate, forged_observation)

    forged_row = copy.deepcopy(row)
    forged_row["metrics"]["provider_reported_cost_usd"]["provenance"] = (
        "client_observed"
    )
    with pytest.raises(EXEC.PilotExecutionV2Error, match="cost provenance"):
        EXEC.validate_evidence_row(forged_row)

    unavailable_row = copy.deepcopy(row)
    unavailable_row["metrics"]["provider_reported_cost_usd"] = {
        "value": None, "provenance": "unavailable",
    }
    with pytest.raises(REPORT.PilotReportV2Error, match="provenance"):
        REPORT._complete_total(
            [forged_row, unavailable_row], "provider_reported_cost_usd",
        )


def test_committed_run_is_requalified_unobserved_with_diagnostics_unattributed():
    evidence = PILOT_ROOT / "evidence-v2" / "pilot-20260825.jsonl"
    raw = evidence.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "e6b9e869b3d808c4d1f0d87994e81fd7a13c247465b651e4b307e33bb022afa2"
    )
    rows = EXEC.read_evidence(evidence, require_complete=True)
    assert all("host_version" not in row for row in rows)

    results = REPORT.build_results(rows)
    runtime = results["runtime_contract"]
    assert runtime["status"] == "host_version_unobserved"
    assert runtime["contract_attribution"] == "unattributed"
    assert runtime["diagnostic_attempts"] == 102
    assert runtime["hosts"]["claude"] == {
        "status": "host_version_unobserved",
        "expected": "2.1.224",
        "observed": None,
        "provenance": "unavailable",
        "verified_before_provider_invocation": False,
        "contract_attribution": "unattributed",
    }
    assert runtime["hosts"]["codex"]["observed"] is None
    assert runtime["diagnostics"]["claude"]["counts"] == {
        "CLAUDE_OUTPUT_SCHEMA_INVALID": 20,
        "HOST_PROCESS_FAILED": 40,
    }
    assert runtime["diagnostics"]["codex"]["counts"] == {
        "CODEX_TRACE_V2_INVALID": 35,
        "HOST_TIMEOUT": 7,
    }
    assert runtime["diagnostics"]["codex"]["without_diagnostic"] == 6
    assert REPORT.DIAGNOSTIC_LIMITATION in results["limitations"]
    assert REPORT.VERSION_LIMITATION in results["limitations"]
    assert "diagnostics_unattributed_to_frozen_contract" in (
        results["host_reports"]["claude"]["reasons"]
    )

    report = REPORT.render_report(results)
    for required in (
        "host_version_unobserved",
        "explicitly unattributed",
        "Claude 2.1.224",
        "Codex 0.147.0",
        "`HOST_PROCESS_FAILED` | 40",
        "`CLAUDE_OUTPUT_SCHEMA_INVALID` | 20",
        "`CODEX_TRACE_V2_INVALID` | 35",
        "`HOST_TIMEOUT` | 7",
    ):
        assert required in report
    checked, checked_report = REPORT.check_outputs(
        evidence, PILOT_ROOT / "report-v2" / "pilot-20260825",
    )
    assert checked == results
    assert checked_report == report


def test_incomplete_duplicate_and_mutated_evidence_fail_closed(tmp_path):
    context = _one_context(tmp_path)
    coordinate = EXEC.bind_coordinate("claude", EXEC._profiles()["claude"][0], context)
    observation = EXEC.parse_claude_outcome(EXEC.ProcessOutcome(
        "completed", "none", _claude_stdout(), 0.25,
    ))
    row = EXEC.build_evidence_row(0, coordinate, observation)

    with pytest.raises(REPORT.EXEC.PilotExecutionV2Error, match="complete frozen matrix"):
        REPORT.build_results([row])
    duplicate = copy.deepcopy(row)
    duplicate["sequence"] = 1
    with pytest.raises(EXEC.PilotExecutionV2Error, match="duplicate"):
        EXEC.validate_evidence_rows([row, duplicate], require_complete=False)
    mutated = copy.deepcopy(row)
    mutated["metrics"]["cumulative_cost_usd"] = {
        "value": 0, "provenance": "host_reported",
    }
    with pytest.raises(EXEC.PilotExecutionV2Error, match="boundary"):
        EXEC.validate_evidence_row(mutated)
    mutated = copy.deepcopy(row)
    mutated["outcomes"]["review"] = {
        "value": "approved", "provenance": "host_reported",
    }
    with pytest.raises(EXEC.PilotExecutionV2Error, match="outcome"):
        EXEC.validate_evidence_row(mutated)
    authorization = EXEC._issue_execution_authorization(
        execute=True, operator_opt_in=True,
    )
    versioned = EXEC.build_evidence_row(
        0, coordinate, observation, _verified_versions(authorization)["claude"],
    )
    mutated = copy.deepcopy(versioned)
    mutated["host_version"]["observed"] = "2.1.223"
    with pytest.raises(EXEC.PilotExecutionV2Error, match="version proof"):
        EXEC.validate_evidence_row(mutated)


def test_v2_layer_composes_f61_without_touching_v1_contracts():
    counts = EXEC.F61.validate_bundle(EXEC.F61_ROOT, REPOSITORY)
    assert counts["future_calls"] == 108
    source = (PILOT_ROOT / "run-pilot-v2.py").read_text(encoding="utf-8")
    assert "measurement_harness as" not in source
    assert "measurement_harness.py" not in source
    assert "measurement_harness_v2" not in (
        ROOT / "tooling" / "foundry" / "measurement_harness.py"
    ).read_text(encoding="utf-8")
    assert "inconclusive" in (PILOT_ROOT / "REPORT-v1.md").read_text(
        encoding="utf-8",
    ).lower()


def test_gate_compares_effort_inside_the_profile_scope(monkeypatch):
    scope = EffortScope(
        "codex", "gpt-5.6", 7, ("low", "max", "high"), {},
    )
    monkeypatch.setattr(EXEC.routing, "scope_for", lambda _host, _model: scope)
    profile = {
        "host": "codex", "model": "gpt-5.6-sol", "role": "reviewer",
        "tier": "frontier", "reasoning_effort": "max",
    }

    assert EXEC._gate("codex", profile)["preserved"] is False
    assert "routing.EFFORTS" not in (PILOT_ROOT / "run-pilot-v2.py").read_text()
