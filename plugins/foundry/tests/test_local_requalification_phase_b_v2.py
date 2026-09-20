from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-45"
SOURCE_REPOSITORY = ROOT.parents[3]
PHASE_B_COMMIT = "b" * 40


def _module():
    spec = importlib.util.spec_from_file_location(
        "foundry45_phase_b_v2_test", ROOT / "run-phase-b-v2.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PHASE_B = _module()


def _phase_a_validator(_directory: Path, _repository: Path):
    return (
        json.loads((ROOT / "phase-a-v2.json").read_text(encoding="utf-8")),
        dict(PHASE_B.PHASE_A_SOURCE_DIGESTS),
    )


def _runtime_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    (repository / "plugins/foundry/benchmarks/foundry-45").mkdir(parents=True)
    for relative in PHASE_B.F35_SOURCE_DIGESTS:
        source = SOURCE_REPOSITORY / relative
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return repository


def _git_runner(calls: list[tuple[str, ...]]):
    def run(_repository: Path, arguments):
        args = tuple(arguments)
        calls.append(args)
        if args == ("rev-parse", f"{PHASE_B.PHASE_A_COMMIT}^{{commit}}"):
            return PHASE_B.PHASE_A_COMMIT.encode()
        if args == ("rev-parse", f"{PHASE_B_COMMIT}^{{commit}}"):
            return PHASE_B_COMMIT.encode()
        if args == ("rev-parse", "HEAD^{commit}"):
            return b"c" * 40
        if args[:2] == ("merge-base", "--is-ancestor"):
            return b""
        if args == ("show", "-s", "--format=%cI", PHASE_B.PHASE_A_COMMIT):
            return b"2026-08-25T12:31:00+00:00"
        if args == ("show", "-s", "--format=%cI", PHASE_B_COMMIT):
            return b"2026-08-25T12:45:00+00:00"
        if len(args) == 2 and args[0] == "show" and ":" in args[1]:
            commit, relative = args[1].split(":", 1)
            if commit in {PHASE_B.PHASE_A_COMMIT, PHASE_B_COMMIT}:
                return (SOURCE_REPOSITORY / relative).read_bytes()
        raise AssertionError(f"unexpected Git call: {args!r}")

    return run


def _authorization(snapshot_root: Path) -> dict[str, object]:
    source_manifest = PHASE_B._load_json(ROOT / "phase-b-manifest-v2.json")[
        "repository_files"
    ]
    return {
        "artifact": PHASE_B.AUTHORIZATION_ARTIFACT,
        "version": 2,
        "issue": "FOUNDRY-45",
        "campaign": "foundry-45-phase-b-v2",
        "authorization_id": "123e4567-e89b-42d3-a456-426614174000",
        "authorized_at": "2026-08-25T13:00:00Z",
        "phase_a_commit": PHASE_B.PHASE_A_COMMIT,
        "phase_a_manifest_sha256": PHASE_B.PHASE_A_MANIFEST_SHA256,
        "phase_a_source_digests": dict(PHASE_B.PHASE_A_SOURCE_DIGESTS),
        "phase_b_commit": PHASE_B_COMMIT,
        "phase_b_manifest_sha256": PHASE_B._sha_file(ROOT / "phase-b-manifest-v2.json"),
        "phase_b_source_digests": source_manifest,
        "candidate_source_digests": dict(PHASE_B.F35_SOURCE_DIGESTS),
        "candidate_snapshots": {
            candidate_id: str((snapshot_root / candidate_id).resolve())
            for candidate_id in PHASE_B.CANDIDATE_IDS
        },
        "local_ports": {
            candidate_id: 23000 + index
            for index, candidate_id in enumerate(PHASE_B.CANDIDATE_IDS)
        },
        "evidence_write_root": PHASE_B.EVIDENCE_ROOT,
        "cleanup_plan": PHASE_B._cleanup_plan(),
        "max_cloud_cost_usd": 20.0,
        "cloud_opt_in": True,
        "cloud_120_confirmation": True,
        "cloud_executions": 120,
    }


def _accept_snapshot(calls: list[str]):
    def accept(candidate, _path):
        calls.append(candidate["identifier"])

    return accept


def _preflight(repository: Path, authorization: dict[str, object], *, snapshot_validator=None, git_calls=None, product_api_validator=lambda _root: None):
    git_calls = [] if git_calls is None else git_calls
    return PHASE_B.validate_phase_b_preflight(
        authorization,
        enable_cloud=True,
        confirm_cloud_120=True,
        repository=repository,
        git_runner=_git_runner(git_calls),
        phase_a_validator=_phase_a_validator,
        product_api_validator=product_api_validator,
        snapshot_validator=snapshot_validator or _accept_snapshot([]),
    )


class FakeProcess:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", *, live: bool = False, timeout: bool = False):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = None if live else 0
        self.live = live
        self.timeout = timeout
        self.pid = None
        self.terminated = False

    def poll(self):
        return None if self.live and not self.terminated else self.returncode

    def wait(self, timeout=None):
        if self.timeout:
            raise subprocess.TimeoutExpired("fake", timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.terminated = True
        self.returncode = -9


def _documented_claude_output() -> bytes:
    """The pinned 2.1.224 Claude success envelope, including discarded metadata."""
    return json.dumps(
        {
            "type": "result", "subtype": "success", "uuid": "123e4567-e89b-42d3-a456-426614174000",
            "is_error": False, "api_error_status": None,
            "duration_ms": 1234.5, "duration_api_ms": 1200.25, "num_turns": 1,
            "result": "CM-1 CM-2 CM-3", "session_id": "transient-session-id",
            "stop_reason": "tool_deferred", "ttft_ms": 42.0, "total_cost_usd": 0.003,
            "usage": {
                "input_tokens": 3, "output_tokens": 4, "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 1, "service_tier": "standard", "speed": "standard",
                "inference_geo": None,
            },
            "modelUsage": {
                "claude-sonnet-4-6": {
                    "inputTokens": 3, "outputTokens": 4, "cacheReadInputTokens": 1,
                    "cacheCreationInputTokens": 0, "webSearchRequests": 0,
                },
            },
            "permission_denials": [{
                "tool_name": "Read", "tool_use_id": "toolu-transient",
                "tool_input": {"path": "PRIVATE_PATH_MUST_NOT_PERSIST"},
            }],
            "structured_output": {"PRIVATE_STRUCTURED": ["SECRET_RAW_OUTPUT"]},
            "deferred_tool_use": {
                "id": "toolu-deferred", "name": "Read",
                "input": {"path": "PRIVATE_DEFERRED_PATH"},
            },
            "terminal_reason": "tool_deferred", "fast_mode_state": "off",
            "origin": {"kind": "human"},
        },
        separators=(",", ":"),
    ).encode()


def _claude_output() -> bytes:
    value = json.loads(_documented_claude_output())
    value["result"] = "CM-1 CM-2 CM-3 DF-1 DF-2 DF-3 LG-1 LG-2 LG-3 TS-1 TS-2 TS-3 SECRET_RAW_OUTPUT"
    return json.dumps(value, separators=(",", ":")).encode()


def _codex_output() -> bytes:
    events = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.started"},
        {"type": "item.updated", "item": {"id": "item-1", "type": "agent_message", "text": "SECRET_RAW_OUTPUT"}},
        {"type": "item.completed", "item": {"id": "item-1", "type": "agent_message", "text": "CM-1 CM-2 CM-3 DF-1 DF-2 DF-3 LG-1 LG-2 LG-3 TS-1 TS-2 TS-3 SECRET_RAW_OUTPUT"}},
        {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 6, "cached_input_tokens": 1, "reasoning_output_tokens": 2}},
    ]
    return b"\n".join(json.dumps(event, separators=(",", ":")).encode() for event in events) + b"\n"


def test_v2_contract_binds_exact_120_call_matrix_and_run_ready_hosts():
    contract = PHASE_B.validate_phase_b_contract()
    plan = contract["plan"]

    assert PHASE_B.paired_plan_sha256() == "400421fb980bb498daf8c871378cfa6c0c784f8ce287c7e17049434e49b1dc88"
    assert [len(plan[name]) for name in ("local_product_rows", "shared_cloud_controls", "local_packet_pipelines")] == [48, 24, 96]
    assert len(plan["shared_cloud_controls"]) + len(plan["local_packet_pipelines"]) == 120
    assert PHASE_B.build_cloud_argv("claude") == PHASE_B.CLAUDE_ARGV
    assert PHASE_B.build_cloud_argv("codex") == PHASE_B.CODEX_ARGV
    assert "--safe-mode" in PHASE_B.CLAUDE_ARGV
    assert "--max-turns" not in PHASE_B.CLAUDE_ARGV
    assert PHASE_B.CLAUDE_ARGV[-2:] == ("--max-budget-usd", "0.10")
    assert PHASE_B.build_mlx_argv("/not-persisted/snapshot", 23000) == (
        "mlx_lm.server", "--model", "/not-persisted/snapshot", "--host", "127.0.0.1", "--port", "23000",
    )


def test_contract_manifest_tampering_fails_closed(monkeypatch):
    actual_load = PHASE_B._load_json

    def forged_load(path, **kwargs):
        value = actual_load(path, **kwargs)
        if path.name == "phase-b-manifest-v2.json":
            value = {**value, "repository_files": {**value["repository_files"]}}
            value["repository_files"]["plugins/foundry/benchmarks/foundry-45/run-phase-b-v2.py"] = "0" * 64
        return value

    monkeypatch.setattr(PHASE_B, "_load_json", forged_load)
    with pytest.raises(PHASE_B.PhaseBContractError, match="current source digest"):
        PHASE_B.validate_phase_b_contract()


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda value: value.__setitem__("version", 1), "fresh FOUNDRY-45 Phase-B v2"),
        (lambda value: value.__setitem__("artifact", "foundry.local_scout.phase_b_authorization"), "fresh FOUNDRY-45 Phase-B v2"),
        (lambda value: value.__setitem__("cloud_executions", 119), "120 cloud executions"),
        (lambda value: value.__setitem__("phase_a_manifest_sha256", "0" * 64), "manifest or source digests"),
        (lambda value: value["phase_b_source_digests"].__setitem__(next(iter(value["phase_b_source_digests"])), "0" * 64), "manifest or source digests"),
        (lambda value: value.__setitem__("max_cloud_cost_usd", 21.0), "price"),
    ],
)
def test_v1_f35_counts_and_manifest_tampering_fail_before_any_popen(tmp_path, mutate, error):
    repository = _runtime_repository(tmp_path)
    authorization = _authorization(tmp_path / "snapshots")
    mutate(authorization)

    def no_process(*_args, **_kwargs):
        raise AssertionError("failed preflight must precede Popen")

    with pytest.raises(PHASE_B.PhaseBAuthorizationError, match=error):
        PHASE_B.execute_campaign(
            authorization,
            enable_cloud=True,
            confirm_cloud_120=True,
            repository=repository,
            git_runner=_git_runner([]),
            phase_a_validator=_phase_a_validator,
            product_api_validator=lambda _root: None,
            snapshot_validator=_accept_snapshot([]),
            process_factory=no_process,
        )


@pytest.mark.parametrize(("enabled", "confirmed"), [(False, True), (True, False), (False, False)])
def test_each_cli_confirmation_is_required(tmp_path, enabled, confirmed):
    authorization = _authorization(tmp_path / "snapshots")
    with pytest.raises(PHASE_B.PhaseBAuthorizationError, match="both explicit confirmations"):
        PHASE_B.validate_authorization(
            authorization,
            source_manifest=PHASE_B._load_json(ROOT / "phase-b-manifest-v2.json")["repository_files"],
            manifest_sha256=PHASE_B._sha_file(ROOT / "phase-b-manifest-v2.json"),
            enable_cloud=enabled,
            confirm_cloud_120=confirmed,
        )


def test_v2_commit_and_timestamp_are_required_before_snapshot_or_popen(tmp_path):
    repository = _runtime_repository(tmp_path)
    authorization = _authorization(tmp_path / "snapshots")
    authorization["authorized_at"] = "2026-08-25T12:45:00Z"
    snapshot_calls: list[str] = []

    with pytest.raises(PHASE_B.PhaseBAuthorizationError, match="after both committed freezes"):
        _preflight(repository, authorization, snapshot_validator=_accept_snapshot(snapshot_calls))
    assert snapshot_calls == []


def test_missing_snapshot_is_typed_unavailable_and_never_downloads_or_falls_back(tmp_path):
    repository = _runtime_repository(tmp_path)
    authorization = _authorization(tmp_path / "missing")

    with pytest.raises(PHASE_B.CandidateSnapshotUnavailable) as raised:
        _preflight(repository, authorization, snapshot_validator=PHASE_B.validate_candidate_snapshot)
    assert raised.value.evidence == {
        "status": "unavailable", "provenance": "unavailable",
        "code": "CANDIDATE_SNAPSHOT_UNAVAILABLE", "candidate_id": "qwen3.8-27b-4bit-mlx",
    }
    assert "path" not in raised.value.evidence


def test_bounded_command_caps_streams_and_times_out_with_owned_cleanup(tmp_path):
    cleanup: list[FakeProcess] = []

    def overflow_factory(*_args, **_kwargs):
        return FakeProcess(b"x" * (PHASE_B.LIMITS["stdout_bytes_max"] + 1))

    overflow = PHASE_B.run_bounded_command(
        PHASE_B.CODEX_ARGV, "packet", cwd=tmp_path, timeout_seconds=1,
        process_factory=overflow_factory, cleanup_process=cleanup.append,
    )
    assert (overflow.status, overflow.failure_class, overflow.stdout) == ("unknown", "unknown", None)
    assert len(cleanup) == 1

    def timeout_factory(*_args, **_kwargs):
        return FakeProcess(b"{}", timeout=True)

    timeout = PHASE_B.run_bounded_command(
        PHASE_B.CLAUDE_ARGV, "packet", cwd=tmp_path, timeout_seconds=1,
        process_factory=timeout_factory, cleanup_process=cleanup.append,
    )
    assert (timeout.status, timeout.failure_class) == ("failed", "timeout")
    assert len(cleanup) == 2


def test_malformed_or_ambiguous_host_output_is_never_success():
    malformed = PHASE_B.ProcessOutcome("completed", "none", b"not-json", 0.1)
    assert PHASE_B.parse_claude_output(malformed).diagnostic_code == "CLAUDE_OUTPUT_SCHEMA_INVALID"
    assert PHASE_B.parse_codex_output(malformed).diagnostic_code == "CODEX_OUTPUT_SCHEMA_INVALID"
    bad_claude = PHASE_B.ProcessOutcome("completed", "none", b'{"type":"result","subtype":"success","is_error":false,"result":"x","usage":{"input_tokens":"bad"}}', 0.1)
    assert PHASE_B.parse_claude_output(bad_claude).status == "unknown"
    bad_codex = PHASE_B.ProcessOutcome("completed", "none", b'{"type":"thread.started","thread_id":"t"}\n{"type":"turn.started"}\n{"type":"turn.completed"}\n', 0.1)
    assert PHASE_B.parse_codex_output(bad_codex).status == "unknown"


def test_changed_diagnostic_manifest_rejects_the_consumed_v2_authorization(tmp_path):
    authorization = _authorization(tmp_path / "snapshots")
    authorization["phase_b_manifest_sha256"] = "1e131200f70d4d38233c64e5a5ae217c0236c9b10d886b745606f40bdee21e21"

    with pytest.raises(PHASE_B.PhaseBAuthorizationError, match="manifest or source digests"):
        PHASE_B.validate_authorization(
            authorization,
            source_manifest=PHASE_B._load_json(ROOT / "phase-b-manifest-v2.json")["repository_files"],
            manifest_sha256=PHASE_B._sha_file(ROOT / "phase-b-manifest-v2.json"),
            enable_cloud=True,
            confirm_cloud_120=True,
        )


def test_claude_21224_full_json_result_envelope_is_normalized_not_persisted():
    observed = PHASE_B.parse_claude_output(
        PHASE_B.ProcessOutcome("completed", "none", _documented_claude_output(), 0.1),
    )

    assert (observed.status, observed.failure_class, observed.output) == (
        "completed", "none", "CM-1 CM-2 CM-3",
    )
    assert observed.usage == {
        "input_tokens": 3, "output_tokens": 4, "cached_input_tokens": 1,
    }
    row = PHASE_B.sanitized_evidence_row(
        kind="cloud_control", candidate_id=None, host="claude", product_case_id="code-map",
        repetition=1, status=observed.status, failure_class=observed.failure_class,
        classification="passed", usage=observed.usage,
        duration_seconds=observed.duration_seconds, diagnostic_code=observed.diagnostic_code,
    )
    serialized = json.dumps(row, sort_keys=True)
    for private_metadata in (
        "transient-session-id", "total_cost_usd", "duration_api_ms", "PRIVATE_PATH_MUST_NOT_PERSIST",
        "PRIVATE_STRUCTURED", "PRIVATE_DEFERRED_PATH", "toolu-transient", "claude-sonnet-4-6",
    ):
        assert private_metadata not in serialized


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__("vendor_extension", True),
        lambda value: value.pop("duration_api_ms"),
        lambda value: value.__setitem__("total_cost_usd", float("nan")),
        lambda value: value.__setitem__("session_id", ""),
        lambda value: value["modelUsage"]["claude-sonnet-4-6"].__setitem__("vendor_extension", True),
        lambda value: value["usage"].__setitem__("vendor_extension", True),
    ],
)
def test_claude_unknown_or_ambiguous_metadata_remains_unknown(mutate):
    value = json.loads(_documented_claude_output())
    mutate(value)
    observed = PHASE_B.parse_claude_output(
        PHASE_B.ProcessOutcome("completed", "none", json.dumps(value).encode(), 0.1),
    )

    assert (observed.status, observed.failure_class, observed.diagnostic_code) == (
        "unknown", "unknown", "CLAUDE_OUTPUT_SCHEMA_INVALID",
    )


def test_codex_0147_native_events_require_sequence_and_terminal_usage():
    completed = PHASE_B.ProcessOutcome("completed", "none", _codex_output(), 0.1)
    assert PHASE_B.parse_codex_output(completed).status == "completed"
    failed = b"\n".join([
        b'{"type":"thread.started","thread_id":"t"}',
        b'{"type":"turn.started"}',
        b'{"type":"error","message":"untrusted"}',
        b'{"type":"turn.failed","error":{"message":"untrusted"}}',
    ]) + b"\n"
    assert PHASE_B.parse_codex_output(PHASE_B.ProcessOutcome("completed", "none", failed, 0.1)).status == "failed"
    missing_usage = b'{"type":"thread.started","thread_id":"t"}\n{"type":"turn.started"}\n{"type":"turn.completed"}\n'
    assert PHASE_B.parse_codex_output(PHASE_B.ProcessOutcome("completed", "none", missing_usage, 0.1)).status == "unknown"


def test_fake_campaign_uses_exact_args_cleans_owned_resources_and_persists_no_raw_data(tmp_path):
    repository = _runtime_repository(tmp_path)
    authorization = _authorization(tmp_path / "snapshots")
    commands: list[tuple[str, ...]] = []
    server_cleanup: list[FakeProcess] = []

    def process_factory(command, **_kwargs):
        command = tuple(command)
        commands.append(command)
        if command[0] == "mlx_lm.server":
            return FakeProcess(live=True)
        if command[0] == "claude":
            return FakeProcess(_claude_output())
        if command[0] == "codex":
            return FakeProcess(_codex_output())
        raise AssertionError(command)

    def product_runner(candidate, case_id, fixture, _port, _temporary, _repository):
        return {
            "untrusted": True, "candidate": candidate["identifier"], "case": case_id,
            "markers": fixture["expected_markers"], "transient_secret": "SECRET_TRANSIENT_PACKET",
        }

    result = PHASE_B.execute_campaign(
        authorization,
        enable_cloud=True,
        confirm_cloud_120=True,
        repository=repository,
        git_runner=_git_runner([]),
        phase_a_validator=_phase_a_validator,
        product_api_validator=lambda _root: None,
        snapshot_validator=_accept_snapshot([]),
        process_factory=process_factory,
        cleanup_process=server_cleanup.append,
        product_runner=product_runner,
        readiness_probe=lambda _port, _process: True,
    )

    assert result["started_cloud_executions"] == 120
    assert result["result"] == "inconclusive"
    assert commands.count(PHASE_B.CLAUDE_ARGV) == 60
    assert commands.count(PHASE_B.CODEX_ARGV) == 60
    assert sum(command[0] == "mlx_lm.server" for command in commands) == 48
    assert len(server_cleanup) == 48
    assert all(command[0] == "mlx_lm.server" for command in commands if command[0] == "mlx_lm.server")

    evidence = repository / PHASE_B.EVIDENCE_ROOT / f"{authorization['authorization_id']}.jsonl"
    lines = evidence.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 168
    payload = evidence.read_text(encoding="utf-8")
    assert "SECRET_RAW_OUTPUT" not in payload
    assert "SECRET_TRANSIENT_PACKET" not in payload
    assert "/snapshots/" not in payload
    checkpoints = [json.loads(line) for line in lines]
    assert [checkpoint["sequence"] for checkpoint in checkpoints] == list(range(168))
    assert checkpoints[0]["previous_record_sha256"] is None
    assert all(checkpoint["record"]["cost"] == {"value": None, "provenance": "unavailable"} for checkpoint in checkpoints)
    assert all(
        checkpoint["record"]["schema_version"] == 2
        and checkpoint["record"]["diagnostic_code"] is None
        for checkpoint in checkpoints
    )
    assert not list((repository / PHASE_B.EVIDENCE_ROOT).glob(".f45-v2-*"))


def test_local_row_requires_its_owned_loopback_listener_before_product_call(tmp_path):
    repository = _runtime_repository(tmp_path)
    authorization = _authorization(tmp_path / "snapshots")
    preflight = _preflight(repository, authorization)
    calls: list[FakeProcess] = []

    def no_product(*_args, **_kwargs):
        raise AssertionError("product API must not run before owned listener readiness")

    diff_row = next(row for row in preflight.plan["local_product_rows"] if row["product_case_id"] == "diff")
    observation = PHASE_B._run_local_row(
        diff_row,
        preflight=preflight,
        authorization=authorization,
        repository=repository,
        process_factory=lambda *_args, **_kwargs: FakeProcess(live=True),
        cleanup_process=calls.append,
        product_runner=no_product,
        readiness_probe=lambda _port, _process: False,
    )

    assert observation == PHASE_B.LocalProductObservation(
        "failed", "local_unavailable", "unavailable", None,
        "LOCAL_MLX_LOOPBACK_UNAVAILABLE",
    )
    assert len(calls) == 1


def test_local_scout_failure_is_preserved_as_one_bounded_diagnostic(tmp_path, monkeypatch):
    repository = _runtime_repository(tmp_path)
    authorization = _authorization(tmp_path / "snapshots")
    preflight = _preflight(repository, authorization)
    preflight.evidence_root.mkdir(mode=0o700)
    tooling = str(SOURCE_REPOSITORY / "plugins/foundry/tooling")
    if tooling not in sys.path:
        sys.path.insert(0, tooling)
    from foundry import local_scout

    def refused(*_args, **_kwargs):
        raise local_scout.LocalScoutError(
            local_scout.LOCAL_SCOUT_INVALID_OUTPUT, "safe local refusal",
        )

    monkeypatch.setattr(PHASE_B, "_product_modules", lambda _repository: (object(), local_scout))
    monkeypatch.setattr(local_scout, "preprocess_diff", refused)
    diff_row = next(row for row in preflight.plan["local_product_rows"] if row["product_case_id"] == "diff")
    observation = PHASE_B._run_local_row(
        diff_row,
        preflight=preflight,
        authorization=authorization,
        repository=repository,
        process_factory=lambda *_args, **_kwargs: FakeProcess(live=True),
        cleanup_process=lambda _process: None,
        product_runner=PHASE_B.run_current_product_api,
        readiness_probe=lambda _port, _process: True,
    )

    assert observation == PHASE_B.LocalProductObservation(
        "failed", "local_invalid_output", "unavailable", None, "LOCAL_SCOUT_INVALID_OUTPUT",
    )
    row = PHASE_B.sanitized_evidence_row(
        kind="local_product", candidate_id=preflight.candidates[0]["identifier"], host=None,
        product_case_id="code-map", repetition=1, status=observation.status,
        failure_class=observation.failure_class, classification=observation.classification,
        usage=None, duration_seconds=None, diagnostic_code=observation.diagnostic_code,
    )
    assert row["diagnostic_code"] == "LOCAL_SCOUT_INVALID_OUTPUT"
    assert "local response" not in json.dumps(row)


def test_forged_or_terminal_mismatched_diagnostic_is_rejected():
    with pytest.raises(PHASE_B.EvidenceWriteError, match="diagnostic"):
        PHASE_B.sanitized_evidence_row(
            kind="cloud_control", candidate_id=None, host="claude", product_case_id="code-map",
            repetition=1, status="completed", failure_class="none", classification="passed",
            usage=None, duration_seconds=0.1, diagnostic_code="CLAUDE_OUTPUT_SCHEMA_INVALID",
        )
    with pytest.raises(PHASE_B.EvidenceWriteError, match="diagnostic"):
        PHASE_B.sanitized_evidence_row(
            kind="cloud_control", candidate_id=None, host="claude", product_case_id="code-map",
            repetition=1, status="unknown", failure_class="unknown", classification="unavailable",
            usage=None, duration_seconds=0.1, diagnostic_code="LOCAL_SCOUT_INVALID_OUTPUT",
        )


def test_write_confinement_rejects_escape_without_creating_evidence(tmp_path):
    repository = _runtime_repository(tmp_path)
    with pytest.raises(PHASE_B.PhaseBPreflightError, match="evidence write root differs"):
        PHASE_B.validate_write_confinement(repository, "plugins/foundry/benchmarks/foundry-35/evidence")
    assert not (repository / PHASE_B.EVIDENCE_ROOT).exists()
