"""Offline adversarial tests for FOUNDRY-62's bounded native runner."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import io
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = ROOT / "plugins/foundry/benchmarks/foundry-62/native-runner-v2.py"


def _runner():
    spec = importlib.util.spec_from_file_location("f62_runner_test", RUNNER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner_and_preflight():
    runner = _runner()
    return runner, runner.offline_preflight()


def _authority(runner, preflight, private: Ed25519PrivateKey, *, now: datetime, **overrides):
    value = {
        "artifact": "foundry.context_broker.campaign_authorization",
        "version": 1,
        "campaign_id": "f59-context-broker-v2",
        "issue": "FOUNDRY-59",
        "f59_plan_sha256": preflight["plan_sha256"],
        "f59_protocol_sha256": preflight["protocol_sha256"],
        "f59_freeze_manifest_sha256": runner._file_sha256(runner.F59_FREEZE_MANIFEST_PATH),
        "f62_runner_sha256": runner._file_sha256(RUNNER_PATH),
        "hosts": ["claude", "codex"],
        "planned_slots": 126,
        "maximum_total_usd": 1000,
        "maximum_per_host_usd": 500,
        "maximum_per_call_usd": 10,
        "pre_call_cost_upper_bound_usd": 7,
        "cost_observation_source": "operator_bound",
        "issued_at": (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nonce": "a" * 64,
        "public_key_id": "operator-test",
    }
    value.update(overrides)
    signed = dict(value)
    value["signature_b64"] = base64.b64encode(
        private.sign(runner.canonical_json(signed))
    ).decode("ascii")
    return value


def _public_key(private: Ed25519PrivateKey):
    return {
        "operator-test": private.public_key().public_bytes_raw(),
    }


def _configure_runtime(runner, monkeypatch, tmp_path, private: Ed25519PrivateKey):
    ledger_root = tmp_path / "ledger"
    campaign_root = tmp_path / "campaign"
    runtime = runner.CampaignRuntimeConfiguration(
        MappingProxyType(_public_key(private)), runner.DurableNonceLedger(ledger_root),
        campaign_root,
        MappingProxyType({
            "claude": tmp_path / "configured-claude",
            "codex": tmp_path / "configured-codex",
        }),
    )
    monkeypatch.setattr(runner, "_configured_runtime", lambda: runtime)
    return ledger_root, campaign_root


def _coordinate(host, version):
    return {
        "host": host,
        "native_contract_version": version,
        "model": "sonnet-5" if host == "claude" else "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "profile_id": "claude-sonnet-medium" if host == "claude" else "codex-terra-medium",
        "revision": "a" * 40,
        "context_policy": "f59-current-unbrokered-v2",
        "pack_binding_id": f"f59-test-pack-{host}",
    }


def _terminal_output(host):
    if host == "claude":
        return b'{"type":"result","subtype":"success","is_error":false,"usage":{"input_tokens":1,"output_tokens":2,"cache_read_input_tokens":0},"total_cost_usd":0.01}'
    return (
        b'{"type":"thread.started","thread_id":"t"}\n'
        b'{"type":"turn.started"}\n'
        b'{"type":"item.completed","item":{"id":"m1","type":"agent_message","text":"done"}}\n'
        b'{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":2,"reasoning_output_tokens":3}}\n'
    )


def _live_authority(runner):
    cap = runner.Decimal("1")
    return runner.CampaignAuthority(
        campaign_id="test-campaign", nonce="a" * 64,
        maximum_total_usd=cap, maximum_per_host_usd=cap,
        maximum_per_call_usd=cap, pre_call_cost_upper_bound_usd=cap,
        cost_observation_source="operator_bound",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        public_key_id="operator-test", authority_sha256="b" * 64,
    )


def _codex_trace_binding(runner):
    return runner.CodexTraceBindingV2(
        artifact="foundry.codex_exec.trace_contract", version=2,
        command_contract_id="codex-exec-json-v2", host_version="0.147.0",
        profile_id="codex-terra-medium", packet_id="f59-test-pack-codex",
        packet_sha256="a" * 64, revision="a" * 40,
        context_policy="f59-current-unbrokered-v2",
    )


def test_preflight_is_full_matrix_and_offline(runner_and_preflight):
    _runner_module, result = runner_and_preflight
    assert result["status"] == "pass"
    assert result["planned_slots"] == 126
    assert result["pairs"] == 42
    assert result["host_calls"] == result["provider_calls"] == 0


def test_runtime_ignores_caller_selected_environment_anchors(monkeypatch, tmp_path):
    runner = _runner()
    fake_public_keys = MappingProxyType({"operator-test": b"x" * 32})
    monkeypatch.setenv("HOME", str(tmp_path / "attacker-home"))
    monkeypatch.setenv("FOUNDRY_F59_PUBLIC_KEYS_FILE", str(tmp_path / "attacker-key.json"))
    monkeypatch.setenv("FOUNDRY_F59_LEDGER_ROOT", str(tmp_path / "attacker-ledger"))
    monkeypatch.setenv("FOUNDRY_F59_WORKTREE_ROOT", str(tmp_path / "attacker-worktrees"))
    monkeypatch.setenv("FOUNDRY_F59_CLAUDE_EXECUTABLE", str(tmp_path / "attacker-claude"))
    monkeypatch.setenv("FOUNDRY_F59_CODEX_EXECUTABLE", str(tmp_path / "attacker-codex"))
    monkeypatch.setattr(runner, "_trusted_public_keys", lambda: fake_public_keys)
    monkeypatch.setattr(runner, "_installed_executable", lambda host: Path(f"/trusted/{host}"))
    runtime = runner._configured_runtime()
    assert runtime.public_keys is fake_public_keys
    assert runtime.ledger.root == runner._runtime_data_root() / "ledger"
    assert runtime.campaign_root == runner._runtime_data_root() / "worktrees"
    assert not str(runtime.ledger.root).startswith(str(tmp_path / "attacker-home"))
    assert runtime.executables == {"claude": Path("/trusted/claude"), "codex": Path("/trusted/codex")}


def test_signed_external_authority_requires_exact_binding_and_full_headroom(runner_and_preflight):
    runner, preflight = runner_and_preflight
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    private = Ed25519PrivateKey.generate()
    value = _authority(runner, preflight, private, now=now)
    authority = runner.validate_authority(
        value, public_keys=_public_key(private),
        expected_plan_sha256=preflight["plan_sha256"],
        expected_protocol_sha256=preflight["protocol_sha256"], now=now,
    )
    assert authority.maximum_per_host_usd == 500
    too_large = _authority(runner, preflight, private, now=now, pre_call_cost_upper_bound_usd=8)
    with pytest.raises(runner.NativeRunnerError, match="headroom"):
        runner.validate_authority(
            too_large, public_keys=_public_key(private),
            expected_plan_sha256=preflight["plan_sha256"],
            expected_protocol_sha256=preflight["protocol_sha256"], now=now,
        )
    wrong_manifest = _authority(
        runner, preflight, private, now=now, f59_freeze_manifest_sha256="b" * 64,
    )
    with pytest.raises(runner.NativeRunnerError, match="frozen binding"):
        runner.validate_authority(
            wrong_manifest, public_keys=_public_key(private),
            expected_plan_sha256=preflight["plan_sha256"],
            expected_protocol_sha256=preflight["protocol_sha256"], now=now,
        )


def test_signature_expiry_and_replay_fail_before_adapter_resolution(runner_and_preflight, tmp_path):
    runner, preflight = runner_and_preflight
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    private = Ed25519PrivateKey.generate()
    value = _authority(runner, preflight, private, now=now)
    invalid = dict(value)
    invalid["signature_b64"] = base64.b64encode(b"x" * 64).decode("ascii")
    with pytest.raises(runner.NativeRunnerError, match="signature"):
        runner.validate_authority(
            invalid, public_keys=_public_key(private),
            expected_plan_sha256=preflight["plan_sha256"],
            expected_protocol_sha256=preflight["protocol_sha256"], now=now,
        )
    expired = _authority(
        runner, preflight, private, now=now,
        issued_at="2026-08-27T00:00:00Z", expires_at="2026-08-27T01:00:00Z",
    )
    with pytest.raises(runner.NativeRunnerError, match="expiry"):
        runner.validate_authority(
            expired, public_keys=_public_key(private),
            expected_plan_sha256=preflight["plan_sha256"],
            expected_protocol_sha256=preflight["protocol_sha256"], now=now,
        )
    authority = runner.validate_authority(
        value, public_keys=_public_key(private),
        expected_plan_sha256=preflight["plan_sha256"],
        expected_protocol_sha256=preflight["protocol_sha256"], now=now,
    )
    ledger = runner.DurableNonceLedger(tmp_path / "ledger")
    ledger.consume(authority, plan_sha256=preflight["plan_sha256"])
    with pytest.raises(runner.NativeRunnerError, match="already consumed"):
        ledger.consume(authority, plan_sha256=preflight["plan_sha256"])


def test_missing_opt_in_refuses_before_any_native_adapter(runner_and_preflight):
    runner, preflight = runner_and_preflight
    private = Ed25519PrivateKey.generate()
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    value = _authority(runner, preflight, private, now=now)

    class UnexpectedAdapter:
        def resolve(self):
            raise AssertionError("native adapter must not be resolved")

    with pytest.raises(runner.NativeRunnerError, match="opt-in"):
        runner.execute_campaign(value)


def test_nonce_is_consumed_before_a_native_version_refusal(monkeypatch, runner_and_preflight, tmp_path):
    runner, preflight = runner_and_preflight
    private = Ed25519PrivateKey.generate()
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    value = _authority(runner, preflight, private, now=now)
    ledger_root, _campaign_root = _configure_runtime(runner, monkeypatch, tmp_path, private)
    monkeypatch.setattr(runner, "_utc_now", lambda: now)
    frozen_rows = (
        {"host": "claude", "native_contract_version": "2.1.224"},
        {"host": "codex", "native_contract_version": "0.147.0"},
    )
    monkeypatch.setattr(
        runner, "_verified_frozen_inputs",
        lambda: (object(), frozen_rows, preflight["plan_sha256"], preflight["protocol_sha256"]),
    )

    class VersionRefusal:
        def __init__(self, host, expected_version):
            self.host = host
            self.expected_version = expected_version

        def resolve(self):
            raise runner.NativeRunnerError("native version differs")

    monkeypatch.setattr(
        runner, "_build_adapters",
        lambda _runtime, _versions: {
            "claude": VersionRefusal("claude", "2.1.224"),
            "codex": VersionRefusal("codex", "0.147.0"),
        },
    )
    with pytest.raises(runner.NativeRunnerError, match="version"):
        runner.execute_campaign(value, operator_opt_in=True)
    assert list(ledger_root.glob("*.json"))


@pytest.mark.parametrize(
    ("host", "expected_version", "version_output", "authority_remaining", "expected_timeout"),
    [
        ("claude", "2.1.224", b"2.1.224 (Claude Code)\\n", 1_000.0, 90.0),
        ("codex", "0.147.0", b"codex-cli 0.147.0\\n", 1_000.0, 300.0),
        ("claude", "2.1.224", b"2.1.224 (Claude Code)\\n", 42.5, 42.5),
        ("codex", "0.147.0", b"codex-cli 0.147.0\\n", 42.5, 42.5),
    ],
)
def test_adapter_resolves_versions_and_uses_shell_false(
    monkeypatch, tmp_path, host, expected_version, version_output,
    authority_remaining, expected_timeout,
):
    runner = _runner()
    executable = tmp_path / host
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    calls = []

    class FakeProcess:
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))
            self.stdout = io.BytesIO(version_output)
            self.returncode = None

        def wait(self, timeout):
            self.returncode = 0
            return 0

        def kill(self):
            self.returncode = -9

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(runner.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    adapter = runner.NativeAdapter(
        host=host, executable=executable, expected_version=expected_version,
    )
    identity = adapter.resolve()
    invocation_calls = []
    monkeypatch.setattr(
        adapter, "_run_bounded",
        lambda command, **kwargs: (invocation_calls.append((command, kwargs)) or (_terminal_output(host), 1)),
    )
    monkeypatch.setattr(runner, "_require_unexpired", lambda _authority: authority_remaining)
    outcome = adapter.invoke(
        identity, prompt="not persisted", cwd=tmp_path,
        coordinate=_coordinate(host, expected_version), maximum_call_cost=runner.Decimal("0.1"),
        authority=_live_authority(runner),
    )
    assert outcome["success"] is True
    assert all(kwargs["shell"] is False for _command, kwargs in calls)
    assert all("not persisted" not in command for command, _kwargs in calls)
    invocation = invocation_calls[0]
    assert invocation[0][0] == str(executable.resolve())
    assert invocation[0][invocation[0].index("--model") + 1] == (
        "sonnet" if host == "claude" else "gpt-5.6-terra"
    )
    assert "medium" in " ".join(invocation[0])
    assert "not persisted" not in invocation[0]
    assert invocation[1]["timeout_seconds"] == expected_timeout


def test_native_timeout_caps_are_frozen_and_reject_expansion(monkeypatch, tmp_path):
    runner = _runner()
    assert dict(runner.NATIVE_TIMEOUT_CAP_SECONDS) == {"claude": 90.0, "codex": 300.0}
    with pytest.raises(runner.NativeRunnerError, match="timeout host"):
        runner._native_timeout_cap("unknown")

    adapter = runner.NativeAdapter(
        host="codex", executable=tmp_path / "codex", expected_version="0.147.0",
    )
    monkeypatch.setattr(
        runner.subprocess, "Popen",
        lambda *_args, **_kwargs: pytest.fail("expanded timeout reached native boundary"),
    )
    with pytest.raises(runner.NativeRunnerError, match="timeout differs"):
        adapter._run_bounded(
            ("content-free",), prompt="bounded", cwd=tmp_path, timeout_seconds=300.1,
        )

    monkeypatch.setattr(
        runner, "NATIVE_TIMEOUT_CAP_SECONDS",
        MappingProxyType({"claude": 90.0, "codex": float("nan")}),
    )
    with pytest.raises(runner.NativeRunnerError, match="timeout contract"):
        runner._native_timeout_cap("codex")


def test_native_timeout_cleans_up_process_group_without_exposing_output(monkeypatch, tmp_path):
    runner = _runner()
    processes = []

    class TimedOutProcess:
        def __init__(self, _command, **kwargs):
            assert kwargs["shell"] is False
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b"")
            self.returncode = None
            self.pid = None
            self.terminated = False
            self.wait_calls = 0
            processes.append(self)

        def wait(self, timeout):
            assert timeout > 0
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("content-free", timeout)
            self.returncode = -15
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(runner.subprocess, "Popen", TimedOutProcess)
    adapter = runner.NativeAdapter(
        host="claude", executable=tmp_path / "claude", expected_version="2.1.224",
    )
    with pytest.raises(runner.NativeInvocationTimeout, match="native invocation timed out"):
        adapter._run_bounded(
            ("content-free",), prompt="bounded", cwd=tmp_path,
            timeout_seconds=90.0, clock=lambda: 0.0,
        )
    assert processes[0].terminated is True


def test_adapter_rejects_nonzero_invocation_without_declaring_success(monkeypatch, tmp_path):
    runner = _runner()
    executable = tmp_path / "claude"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)

    class FakeProcess:
        def __init__(self, *_args, **_kwargs):
            self.stdout = io.BytesIO(b"2.1.224\n")
            self.returncode = None

        def wait(self, timeout):
            assert timeout > 0
            self.returncode = 0
            return 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(runner.subprocess, "Popen", FakeProcess)
    adapter = runner.NativeAdapter(host="claude", executable=executable, expected_version="2.1.224")
    identity = adapter.resolve()
    monkeypatch.setattr(
        adapter, "_run_bounded",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            runner.NativeRunnerError("native invocation returned nonzero"),
        ),
    )
    with pytest.raises(runner.NativeRunnerError, match="nonzero"):
        adapter.invoke(
            identity, prompt="not persisted", cwd=tmp_path,
            coordinate=_coordinate("claude", "2.1.224"),
            maximum_call_cost=runner.Decimal("0.1"), authority=_live_authority(runner),
        )


def test_adapter_executes_the_verified_binary_locally(tmp_path):
    runner = _runner()
    executable = tmp_path / "claude"
    executable.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then\n"
        "  printf '2.1.224\\n'\n"
        "  exit 0\n"
        "fi\n"
        "cat >/dev/null\n"
        "printf '%s\\n' '{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false,\"usage\":{\"input_tokens\":1,\"output_tokens\":2,\"cache_read_input_tokens\":0},\"total_cost_usd\":0.01}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    adapter = runner.NativeAdapter(host="claude", executable=executable, expected_version="2.1.224")
    identity = adapter.resolve()
    outcome = adapter.invoke(
        identity, prompt="local-only", cwd=tmp_path,
        coordinate=_coordinate("claude", "2.1.224"),
        maximum_call_cost=runner.Decimal("0.1"), authority=_live_authority(runner),
    )
    assert outcome["success"] is True


def test_version_probe_bounds_output_and_terminates_the_process(monkeypatch, tmp_path):
    runner = _runner()
    executable = tmp_path / "claude"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    process_instances = []

    class OverflowingProcess:
        def __init__(self, *_args, **_kwargs):
            self.stdout = io.BytesIO(b"x" * (runner.MAX_VERSION_OUTPUT_BYTES + 1))
            self.returncode = None
            self.terminated = False
            process_instances.append(self)

        def wait(self, timeout):
            assert timeout > 0
            self.returncode = 0 if not self.terminated else -15
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

    monkeypatch.setattr(runner.subprocess, "Popen", OverflowingProcess)
    adapter = runner.NativeAdapter(host="claude", executable=executable, expected_version="2.1.224")
    with pytest.raises(runner.NativeRunnerError, match="output exceeds"):
        adapter.resolve()
    assert process_instances[0].terminated is True


def test_closed_evidence_rejects_all_forbidden_content_and_cost_stays_null(tmp_path):
    runner = _runner()
    projected = runner.project_host_metrics({}, success=None)
    assert projected["effective_cost_usd"] is None
    assert projected["economic_verdict"] == "unavailable"
    forbidden = ("prompt", "code", "path", "command", "output", "secret", "alias")
    for field in forbidden:
        with pytest.raises(runner.NativeRunnerError, match="unapproved"):
            runner.project_host_metrics({field: "unapproved-content"}, success=True)
    valid_evidence = {
        "artifact": "foundry.context_broker.native_campaign_evidence",
        "version": 1,
        "campaign_id_sha256": "a" * 64,
        "plan_sha256": "b" * 64,
        "host": "claude",
        "coordinate_sha256": "c" * 64,
        "arm_id": "A",
        "worktree_attestation": {
            "clean_before": True, "head_matches": True, "removed": True,
        },
        "metrics": {field: None for field in runner.METRIC_FIELDS},
        "metric_provenance": {field: "unavailable" for field in runner.METRIC_FIELDS},
        "effective_cost_usd": None,
        "economic_verdict": "unavailable",
        "success": True,
        "status": "complete",
    }
    ledger = runner.DurableNonceLedger(tmp_path / "ledger")
    for field in forbidden:
        invalid = dict(valid_evidence)
        invalid[field] = "unapproved-content"
        with pytest.raises(runner.NativeRunnerError, match="schema"):
            ledger.persist_evidence(invalid)


def test_codex_success_requires_the_f61_v2_agent_message_lifecycle():
    runner = _runner()
    no_message = (
        b'{"type":"thread.started","thread_id":"t"}\n'
        b'{"type":"turn.started"}\n'
        b'{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":2,"reasoning_output_tokens":3}}\n'
    )
    with pytest.raises(runner.NativeRunnerError, match="terminal envelope"):
        runner._parse_host_output(
            "codex", no_message, duration_ms=1, codex_binding=_codex_trace_binding(runner),
        )
    assert runner._parse_host_output(
        "codex", _terminal_output("codex"), duration_ms=1,
        codex_binding=_codex_trace_binding(runner),
    )["success"] is True
    oversized_thread = _terminal_output("codex").replace(b'"thread_id":"t"', b'"thread_id":"' + b"x" * 257 + b'"')
    with pytest.raises(runner.NativeRunnerError, match="terminal envelope"):
        runner._parse_host_output(
            "codex", oversized_thread, duration_ms=1,
            codex_binding=_codex_trace_binding(runner),
        )
    with pytest.raises(runner.NativeRunnerError, match="binding"):
        runner._parse_host_output("codex", _terminal_output("codex"), duration_ms=1)


def test_worktree_disposal_forces_cleanup_after_an_attestation_error(monkeypatch, tmp_path):
    runner = _runner()
    root = tmp_path / "campaign"
    root.mkdir()
    target = root / "target"
    target.mkdir()
    worktrees = runner.DisposableWorktrees(
        campaign_root=root, development_checkout=runner.REPOSITORY,
    )
    monkeypatch.setattr(
        worktrees, "_is_clean",
        lambda _target: (_ for _ in ()).throw(runner.NativeRunnerError("status output differs")),
    )
    removed = []

    def force_remove(path):
        removed.append(path)
        path.rmdir()

    monkeypatch.setattr(worktrees, "_force_remove", force_remove)
    with pytest.raises(runner.NativeRunnerError, match="status output"):
        worktrees.dispose(target, revision="a" * 40)
    assert removed == [target]
    assert not target.exists()


def test_worktree_revision_is_a_git_oid_not_a_content_digest(monkeypatch, tmp_path):
    runner = _runner()
    root = tmp_path / "campaign"
    root.mkdir()
    target = root / "target"
    target.mkdir()
    worktrees = runner.DisposableWorktrees(
        campaign_root=root, development_checkout=runner.REPOSITORY,
    )
    monkeypatch.setattr(
        worktrees, "_git",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, b"a" * 40 + b"\n", b""),
    )
    assert worktrees._has_expected_revision(target, "a" * 40) is True
    with pytest.raises(runner.NativeRunnerError, match="revision"):
        worktrees._has_expected_revision(target, "a" * 64)


def test_campaign_root_can_never_be_development_checkout():
    runner = _runner()
    with pytest.raises(runner.NativeRunnerError, match="unsafe"):
        runner.DisposableWorktrees(campaign_root=runner.REPOSITORY)


def test_worktree_cleanliness_rejects_untracked_content(monkeypatch, tmp_path):
    runner = _runner()
    worktrees = runner.DisposableWorktrees(
        campaign_root=tmp_path / "campaign", development_checkout=runner.REPOSITORY,
    )
    monkeypatch.setattr(
        worktrees, "_git_output_bounded",
        lambda _args, *, cwd: (0, b"?? unexpected.txt\n"),
    )
    assert worktrees._is_clean(tmp_path) is False


def _campaign_fakes(
    runner, tmp_path, monkeypatch, preflight, *, nonzero: bool = False,
    native_timeout: bool = False, immutable_plan: bool = False,
    materialized_coordinates=None,
):
    rows = []
    for host, version in (("claude", "2.1.224"), ("codex", "0.147.0")):
        for pair in range(21):
            for arm in ("A", "B", "C"):
                rows.append({
                    "pair_id": f"pair-{host}-{pair:02d}", "arm_id": arm, "host": host,
                    "revision": "a" * 40, "native_contract_version": version,
                    "reasoning_effort": "medium", "case_id": f"case-{pair % 7}",
                    "role": "implementer", "model": "sonnet-5" if host == "claude" else "gpt-5.6-terra",
                    "conditions_id": "frozen", "repetition": pair // 7 + 1,
                })

    class FakeF63:
        @staticmethod
        def materialize_coordinate(coordinate, *, repository):
            assert repository == runner.REPOSITORY
            if materialized_coordinates is not None:
                materialized_coordinates.append(coordinate)
            return SimpleNamespace(task="bounded task", criteria=("bounded AC",), selected=())

    class FakeWorktrees:
        def __init__(self, *, campaign_root, development_checkout=runner.REPOSITORY):
            self.root = campaign_root

        def create(self, coordinate):
            target = self.root / sha256_for_test(runner, coordinate)[:16]
            target.mkdir(parents=True)
            return target

        @staticmethod
        def dispose(target, *, revision):
            assert revision == "a" * 40
            target.rmdir()
            return {"clean_before": True, "head_matches": True, "removed": True}

    class FakeAdapter:
        def __init__(self, host, expected_version):
            self.host = host
            self.expected_version = expected_version

        @staticmethod
        def resolve():
            return object()

        def invoke(self, _identity, *, prompt, cwd, coordinate, maximum_call_cost, authority):
            assert prompt == "TASK\nbounded task\n\nACCEPTANCE CRITERIA\nbounded AC"
            assert cwd.is_dir()
            assert coordinate["host"] == self.host
            assert maximum_call_cost > 0 and authority.expires_at.tzinfo is not None
            if native_timeout:
                raise runner.NativeInvocationTimeout("native invocation timed out")
            if nonzero:
                raise runner.NativeRunnerError("native invocation returned nonzero")
            return runner.project_host_metrics({"duration_ms": 1}, success=True)

    plan_sha256 = runner.sha256(rows)
    plan = tuple(MappingProxyType(row) for row in rows) if immutable_plan else tuple(rows)
    monkeypatch.setattr(
        runner, "_verified_frozen_inputs",
        lambda: (FakeF63, plan, plan_sha256, preflight["protocol_sha256"]),
    )
    monkeypatch.setattr(runner, "DisposableWorktrees", FakeWorktrees)
    monkeypatch.setattr(
        runner, "_build_adapters",
        lambda _runtime, _versions: {
            "claude": FakeAdapter("claude", "2.1.224"),
            "codex": FakeAdapter("codex", "0.147.0"),
        },
    )
    return {
        "plan_sha256": plan_sha256,
        "protocol_sha256": preflight["protocol_sha256"],
        "plan": plan,
    }


def sha256_for_test(runner, coordinate):
    return runner.sha256(dict(coordinate))


def test_execute_campaign_persists_separate_closed_host_evidence(
    monkeypatch, runner_and_preflight, tmp_path,
):
    runner, preflight = runner_and_preflight
    private = Ed25519PrivateKey.generate()
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    frozen = _campaign_fakes(runner, tmp_path, monkeypatch, preflight)
    authority = _authority(runner, frozen, private, now=now)
    ledger_root, _campaign_root = _configure_runtime(runner, monkeypatch, tmp_path, private)
    monkeypatch.setattr(runner, "_utc_now", lambda: now)
    result = runner.execute_campaign(authority, operator_opt_in=True)
    assert result["status"] == "complete"
    assert result["complete_matrix"] is True
    assert result["completed_slots"] == 126
    assert set(result["host_reports"]) == {"claude", "codex"}
    assert all(len(rows) == 63 for rows in result["host_reports"].values())
    evidence = list(ledger_root.glob("evidence-*.json"))
    assert len(evidence) == 126
    assert all("bounded task" not in item.read_text(encoding="ascii") for item in evidence)


def test_execute_campaign_materializes_a_plain_copy_of_each_immutable_coordinate(
    monkeypatch, runner_and_preflight, tmp_path,
):
    runner, preflight = runner_and_preflight
    private = Ed25519PrivateKey.generate()
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    materialized = []
    frozen = _campaign_fakes(
        runner, tmp_path, monkeypatch, preflight,
        immutable_plan=True, materialized_coordinates=materialized,
    )
    authority = _authority(runner, frozen, private, now=now)
    _configure_runtime(runner, monkeypatch, tmp_path, private)
    monkeypatch.setattr(runner, "_utc_now", lambda: now)

    result = runner.execute_campaign(authority, operator_opt_in=True)

    assert result["status"] == "complete"
    assert len(materialized) == len(frozen["plan"]) == 126
    for original, copy in zip(frozen["plan"], materialized, strict=True):
        assert type(original) is MappingProxyType
        assert type(copy) is dict
        assert copy == original
        assert copy is not original


def test_execute_campaign_nonzero_is_inconclusive(monkeypatch, runner_and_preflight, tmp_path):
    runner, preflight = runner_and_preflight
    private = Ed25519PrivateKey.generate()
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    frozen = _campaign_fakes(runner, tmp_path, monkeypatch, preflight, nonzero=True)
    authority = _authority(runner, frozen, private, now=now)
    _configure_runtime(runner, monkeypatch, tmp_path, private)
    monkeypatch.setattr(runner, "_utc_now", lambda: now)
    result = runner.execute_campaign(authority, operator_opt_in=True)
    assert result["status"] == "inconclusive"
    assert result["complete_matrix"] is False
    assert result["completed_slots"] == 0
    assert result["failure_class"] == "guard_refusal"


def test_execute_campaign_preserves_sanitized_native_timeout_category(
    monkeypatch, runner_and_preflight, tmp_path,
):
    runner, preflight = runner_and_preflight
    private = Ed25519PrivateKey.generate()
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    frozen = _campaign_fakes(
        runner, tmp_path, monkeypatch, preflight, native_timeout=True,
    )
    authority = _authority(runner, frozen, private, now=now)
    _configure_runtime(runner, monkeypatch, tmp_path, private)
    monkeypatch.setattr(runner, "_utc_now", lambda: now)

    result = runner.execute_campaign(authority, operator_opt_in=True)

    assert result["status"] == "inconclusive"
    assert result["complete_matrix"] is False
    assert result["completed_slots"] == 0
    assert result["failure_class"] == "native_timeout"


def test_execute_campaign_refuses_expiry_before_a_host_call(
    monkeypatch, runner_and_preflight, tmp_path,
):
    runner, preflight = runner_and_preflight
    private = Ed25519PrivateKey.generate()
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    frozen = _campaign_fakes(runner, tmp_path, monkeypatch, preflight)
    authority = _authority(
        runner, frozen, private, now=now,
        expires_at="2026-08-28T00:00:04Z",
    )
    _configure_runtime(runner, monkeypatch, tmp_path, private)
    observed = iter((now, now, now, now + timedelta(seconds=1), now + timedelta(seconds=5)))
    monkeypatch.setattr(runner, "_utc_now", lambda: next(observed))
    result = runner.execute_campaign(authority, operator_opt_in=True)
    assert result["status"] == "inconclusive"
    assert result["complete_matrix"] is False
    assert result["completed_slots"] == 0
