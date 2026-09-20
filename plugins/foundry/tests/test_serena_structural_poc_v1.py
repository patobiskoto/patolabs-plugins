"""Adversarial regression tests for the isolated FOUNDRY-52 Serena POC."""
from __future__ import annotations

import ast
import base64
import copy
import hashlib
import importlib.metadata as metadata
import importlib.util
import json
import os
import py_compile
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-52"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POC = _load("foundry52_serena_poc", ROOT / "serena-poc-v1.py")
RUNNER = _load("foundry52_serena_runner", ROOT / "run-serena-poc-v1.py")
_FROZEN: dict[str, object] | None = None


def _artifact(name: str) -> dict[str, object]:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _frozen() -> dict[str, object]:
    global _FROZEN
    if _FROZEN is None:
        _FROZEN = POC.validate_f51_freeze()
    return _FROZEN


def _evidence() -> dict[str, object]:
    return POC._validate_evidence(_artifact("evidence-v1.json"), _frozen())


def _attestation() -> dict[str, object]:
    return POC._validate_attestation(
        _artifact("attestation-v1.json"), _frozen(), _evidence(),
    )


def _runtime(digest: str = "a") -> dict[str, object]:
    value = digest * 64
    return {
        "serena": {
            "version": POC.SERENA_VERSION,
            "executable_sha256": value,
            "distribution_sha256": value,
            "direct_url_sha256": value,
        },
        "pyright": {
            "version": POC.PYRIGHT_VERSION,
            "executable_sha256": value,
            "langserver_executable_sha256": value,
            "distribution_sha256": value,
            "direct_url_sha256": value,
        },
        "python_executable_sha256": value,
        "sandbox": {
            "executable_sha256": value,
            "profile_sha256": value,
            "host_probe_sha256": value,
        },
        "runner_sha256": value,
    }


def _verified_attempts(frozen: dict[str, object], *, latency_ms: int = 1) -> list[dict[str, object]]:
    attempts = []
    for row in POC.build_execution_plan(frozen):
        attempts.append({
            **row,
            "state": "observed",
            "selected_ids": [],
            "selection_bytes": POC.selection_bytes([]),
            "latency_ms": latency_ms,
            "failure_class": None,
            "measurement_contract_sha256": POC.measurement_contract_sha256(),
            "server_identity_sha256": "b" * 64 if row["candidate"] == "serena" else None,
        })
    return attempts


def test_corrected_artifacts_revalidate_exact_f51_slots_with_no_measurement_claim():
    result = POC.validate_artifacts()

    assert result["plan_count"] == 63
    assert result["fixture"]["plan"] == POC.build_execution_plan(_frozen())
    assert result["evidence"]["f51_binding"]["candidate_count"] == 29
    assert result["evidence"]["campaign_state"] == "invalidated_no_reexecution"
    assert len(result["evidence"]["attempts"]) == 63
    for attempt in result["evidence"]["attempts"]:
        assert attempt["state"] == "unavailable"
        assert attempt["selected_ids"] is None
        assert attempt["selection_bytes"] is None
        assert attempt["latency_ms"] is None
        assert attempt["failure_class"] == "historical_contract_unverified"
        assert attempt["measurement_contract_sha256"] is None
    comparison = result["report"]["comparison"]
    assert comparison["decision"] == "inconclusive_no_promotion"
    assert all(value is None for value in comparison["marginal_deltas"].values())
    assert all(value is None for value in comparison["predicates"].values())
    assert all(value is None for value in comparison["comparability"].values())
    for report in result["report"]["candidate_reports"]:
        assert report["observed_attempts"] == 0
        assert report["unavailable_attempts"] == 21
        assert report["measurement_contract_sha256"] is None
        assert all(metric == POC.unavailable_metric() for metric in report["metrics"].values())


@pytest.mark.parametrize("version", ["1.6.9", "1.7", "1.7.0rc1"])
def test_version_guard_rejects_older_or_malformed_serena(version: str):
    with pytest.raises(POC.SerenaPocError, match="Serena version"):
        POC.validate_serena_version(version)

    protocol = _artifact("protocol-v1.json")
    protocol["candidate"]["version"] = version
    with pytest.raises(POC.SerenaPocError, match="candidate guard"):
        POC._validate_protocol(protocol, _frozen())


def test_protocol_pins_stdio_lsp_filesystem_and_homogeneous_measurement_contract():
    protocol = POC._validate_protocol(_artifact("protocol-v1.json"), _frozen())

    assert protocol["candidate"]["release_commit"] == POC.SERENA_RELEASE_COMMIT
    assert protocol["lsp"]["wheel_sha256"] == POC.PYRIGHT_WHEEL_SHA256
    assert protocol["execution"]["filesystem"] == "deny_by_default_host_verified_or_fail_closed"
    assert protocol["execution"]["mcp_client"] == POC.MCP_CLIENT_CONTRACT
    assert protocol["measurement_contract"] == POC.MEASUREMENT_CONTRACT
    assert protocol["promotion"]["decision"] == "derived_by_explicit_predicates"

    for field, invalid in (
        (("candidate", "transport"), "http"),
        (("candidate", "dashboard"), "enabled"),
        (("execution", "filesystem"), "network_only"),
        (("measurement_contract", "bytes"), "raw candidate response bytes"),
    ):
        forged = _artifact("protocol-v1.json")
        forged[field[0]][field[1]] = invalid
        with pytest.raises(POC.SerenaPocError):
            POC._validate_protocol(forged, _frozen())


def test_serena_plan_is_explicit_stdio_scratch_only_and_never_executes(tmp_path):
    scratch = tmp_path / "scratch"
    workspace = scratch / "snapshot"
    workspace.mkdir(parents=True)

    plan = POC.build_serena_stdio_plan(workspace, scratch)

    assert plan["argv"] == [
        "serena", "start-mcp-server", "--transport", "stdio", "--project",
        str(workspace.resolve()), "--enable-web-dashboard", "False",
        "--open-web-dashboard", "False", "--mode", "planning",
    ]
    assert plan["execution"] == "not_started"
    with pytest.raises(POC.SerenaPocError, match="controlled scratch"):
        POC.build_serena_stdio_plan(tmp_path, scratch)
    (workspace / ".serena").mkdir()
    with pytest.raises(POC.SerenaPocError, match="project configuration"):
        POC.build_serena_stdio_plan(workspace, scratch)


def test_fixture_retains_exact_63_slot_f51_binding_and_fails_closed():
    fixture = _artifact("fixtures-v1.json")
    assert len(fixture["plan"]) == 63
    assert len({
        (row["candidate"], row["case_id"], row["revision"], row["repetition"])
        for row in fixture["plan"]
    }) == 63

    forged = copy.deepcopy(fixture)
    forged["plan"].pop()
    with pytest.raises(POC.SerenaPocError, match="fixture plan"):
        POC._validate_fixture(forged, _frozen())
    forged = copy.deepcopy(fixture)
    forged["f51_binding"]["candidate_count"] = 28
    with pytest.raises(POC.SerenaPocError, match="F51 binding"):
        POC._validate_fixture(forged, _frozen())


def test_controlled_probe_enforces_canonical_selection_bytes():
    probe = {
        "candidate": "serena",
        "case_id": "benchmark-voluminous-logs",
        "revision": "13e3869f3495f9e5856d2a553341ac85cb379f05",
        "repetition": 1,
        "candidate_ids": ["C35-raw", "C35-protocol", "C35-noise"],
        "selection_bytes": POC.selection_bytes(["C35-raw", "C35-protocol", "C35-noise"]),
        "latency_ms": 12,
        "source": "controlled_offline_fixture",
    }
    result = POC.evaluate_sanitized_probe(probe, _frozen())

    assert result["metrics"]["recall"] == {"state": "fixture_derived", "value": 2 / 3}
    assert result["metrics"]["tokens"] == POC.unavailable_metric()
    forged = copy.deepcopy(probe)
    forged["selection_bytes"] += 1
    with pytest.raises(POC.SerenaPocError, match="homogeneous byte"):
        POC.evaluate_sanitized_probe(forged, _frozen())


def test_unavailable_attempt_requires_null_measurements_never_empty_selection():
    evidence = _artifact("evidence-v1.json")
    evidence["attempts"][0]["selected_ids"] = []
    with pytest.raises(POC.SerenaPocError, match="must remain null"):
        POC._validate_evidence(evidence, _frozen())

    evidence = _artifact("evidence-v1.json")
    evidence["attempts"][0]["selection_bytes"] = 0
    with pytest.raises(POC.SerenaPocError, match="must remain null"):
        POC._validate_evidence(evidence, _frozen())

    evidence = _artifact("evidence-v1.json")
    evidence["attempts"][0]["measurement_contract_sha256"] = POC.measurement_contract_sha256()
    with pytest.raises(POC.SerenaPocError, match="measurement contract"):
        POC._validate_evidence(evidence, _frozen())


class _ToolResult:
    def __init__(self, text: str, *, is_error: bool = False):
        self.isError = is_error
        self.content = [SimpleNamespace(text=text)]


@pytest.mark.parametrize(
    ("result", "failure"),
    [
        (_ToolResult("[]", is_error=True), "mcp_tool_error"),
        (_ToolResult("not-json"), "malformed_result"),
        (_ToolResult("{}"), "malformed_result"),
        (_ToolResult(json.dumps([{"relative_path": "not-in-f51.py"}])), "candidate_absent"),
        (_ToolResult("x" * (POC.MAX_RAW_RESULT_BYTES + 1)), "byte_cap_breach"),
    ],
)
def test_mcp_tool_errors_malformed_results_and_caps_are_unavailable(result, failure):
    state, selected, failure_class = RUNNER._parse_tool_result(
        result, {"plugins/foundry/example.py": "C1-required"},
    )

    assert state == "unavailable"
    assert selected is None
    assert failure_class == failure


def test_only_explicit_successful_json_empty_list_is_observed_empty_selection():
    state, selected, failure = RUNNER._parse_tool_result(_ToolResult("[]"), {})
    assert (state, selected, failure) == ("observed", [], None)

    missing_error_flag = SimpleNamespace(content=[SimpleNamespace(text="[]")])
    state, selected, failure = RUNNER._parse_tool_result(missing_error_flag, {})
    assert (state, selected, failure) == ("unavailable", None, "malformed_result")


def test_mcp_server_identity_must_bind_the_loaded_serena_version():
    valid = SimpleNamespace(
        serverInfo=SimpleNamespace(name="Serena", version=POC.SERENA_VERSION),
    )
    assert len(RUNNER._server_identity(valid)) == 64

    wrong = SimpleNamespace(
        serverInfo=SimpleNamespace(name="Serena", version="1.7.1"),
    )
    with pytest.raises(RUNNER.RunnerError, match="server identity"):
        RUNNER._server_identity(wrong)


def test_sandbox_profile_is_deny_default_scratch_write_only_and_path_bounded(tmp_path):
    venv = tmp_path / "foundry-52-venv"
    scratch = tmp_path / "foundry-52-run"
    workspace = scratch / "workspace"
    serena = venv / "bin" / "serena"
    serena.parent.mkdir(parents=True)
    serena.write_text("placeholder", encoding="utf-8")
    workspace.mkdir(parents=True)

    profile = RUNNER._sandbox_profile(venv, scratch)
    command = RUNNER.sandbox_command(serena, workspace, scratch, venv)

    assert "(deny default)" in profile
    assert "(allow default)" not in profile
    assert profile.count("(allow file-write*") == 1
    assert str(scratch.resolve()) in profile
    assert str(venv.resolve()) in profile
    assert str(ROOT.resolve()) not in profile
    assert command[:3] == ["/usr/bin/sandbox-exec", "-p", profile]
    assert command[3:] == [
        str(serena.resolve()), "start-mcp-server", "--transport", "stdio",
        "--project", str(workspace.resolve()), "--enable-web-dashboard", "False",
        "--open-web-dashboard", "False", "--mode", "planning",
    ]
    policy = RUNNER._command_policy()
    assert policy["filesystem"]["default"] == "deny"
    assert policy["filesystem"]["ambient_user_or_project"] == "read_and_write_denied"
    assert policy["mcp_client"] == {
        "parent_import_or_execution": "forbidden",
        "controlled_boundary": "unavailable_fail_closed",
        "external_components": [],
    }


def test_host_probe_requires_network_project_read_project_write_and_non_scratch_denials(
    monkeypatch,
    tmp_path,
):
    venv = tmp_path / "foundry-52-venv"
    scratch = tmp_path / "foundry-52-run"
    denied_root = tmp_path / "foundry-52-denied"
    project = tmp_path / "project-file.py"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("python", encoding="utf-8")
    scratch.mkdir()
    denied_root.mkdir()
    project.write_text("project", encoding="utf-8")
    commands = []

    def successful_probe(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).touch()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(RUNNER.subprocess, "run", successful_probe)
    profile_digest, outcome_digest = RUNNER._verify_sandbox_policy(
        venv=venv,
        scratch=scratch,
        denied_root=denied_root,
        repository_probe=project,
    )

    assert len(profile_digest) == 64
    assert outcome_digest == RUNNER._canonical_sha256({
        "scratch_read": "allowed",
        "scratch_write": "allowed",
        "ambient_network_connection": "denied",
        "project_read": "denied",
        "project_write": "denied",
        "non_scratch_write": "denied",
    })
    assert commands[0][:2] == ["/usr/bin/sandbox-exec", "-p"]
    source = RUNNER.SANDBOX_PROBE_SOURCE
    assert 'connection.connect(("127.0.0.1", 9))' in source
    assert 'project.open("rb")' in source
    assert 'project.open("ab")' in source
    assert 'denied.open("ab")' in source
    assert "errno.EACCES, errno.EPERM" in source
    assert str(project) not in outcome_digest
    assert str(scratch) not in outcome_digest


def test_ambiguous_host_probe_result_fails_closed(monkeypatch, tmp_path):
    venv = tmp_path / "foundry-52-venv"
    scratch = tmp_path / "foundry-52-run"
    denied_root = tmp_path / "foundry-52-denied"
    project = tmp_path / "project-file.py"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("python", encoding="utf-8")
    scratch.mkdir()
    denied_root.mkdir()
    project.write_text("project", encoding="utf-8")

    def ambiguous_probe(command, **_kwargs):
        Path(command[-1]).write_text("untrusted probe text", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(RUNNER.subprocess, "run", ambiguous_probe)
    with pytest.raises(RUNNER.RunnerError, match="ambiguous"):
        RUNNER._verify_sandbox_policy(
            venv=venv,
            scratch=scratch,
            denied_root=denied_root,
            repository_probe=project,
        )


def test_sandbox_host_verification_failure_prevents_all_persistence(monkeypatch, tmp_path):
    output = tmp_path / "output"
    parent = tmp_path / "parent"
    venv = tmp_path / "foundry-52-venv"
    output.mkdir()
    parent.mkdir()
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "serena").write_text("placeholder", encoding="utf-8")
    frozen = _frozen()

    monkeypatch.setattr(RUNNER.VALIDATOR, "validate_f51_freeze", lambda repository: frozen)
    monkeypatch.setattr(RUNNER, "_runtime_attestation", lambda selected: _runtime())
    monkeypatch.setattr(RUNNER, "_resolve_venv_file", lambda *args, **kwargs: venv / "bin" / "serena")
    monkeypatch.setattr(RUNNER, "HERE", output)
    monkeypatch.setattr(
        RUNNER,
        "_verify_sandbox_policy",
        lambda **kwargs: (_ for _ in ()).throw(RUNNER.RunnerError("sandbox unverified")),
    )

    with pytest.raises(RUNNER.RunnerError, match="sandbox unverified"):
        RUNNER.execute_campaign(
            venv=venv, scratch_parent=parent, output=output, repository=ROOT.parents[2],
        )

    assert not (output / "evidence-v1.json").exists()
    assert not (output / "attestation-v1.json").exists()
    assert not list(parent.glob("foundry-52-*-probe-*"))
    assert not list(parent.glob("foundry-52-run-*"))


def test_parent_has_no_mcp_import_and_campaign_fails_closed_before_candidates(
    monkeypatch,
    tmp_path,
):
    source = (ROOT / "run-serena-poc-v1.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    assert "mcp" not in imported_modules
    with pytest.raises(RUNNER.RunnerError, match="client protocol is unavailable"):
        RUNNER._serena_selection(
            serena=tmp_path,
            venv=tmp_path,
            scratch=tmp_path,
            workspace=tmp_path,
            locations={},
            task="bounded",
        )

    output = tmp_path / "output"
    parent = tmp_path / "parent"
    venv = tmp_path / "foundry-52-venv"
    output.mkdir()
    parent.mkdir()
    (venv / "bin").mkdir(parents=True)
    serena = venv / "bin" / "serena"
    serena.write_text("placeholder", encoding="utf-8")
    frozen = _frozen()
    monkeypatch.setattr(RUNNER.VALIDATOR, "validate_f51_freeze", lambda repository: frozen)
    monkeypatch.setattr(RUNNER, "_runtime_attestation", lambda selected: _runtime())
    monkeypatch.setattr(RUNNER, "_resolve_venv_file", lambda *args, **kwargs: serena)
    monkeypatch.setattr(RUNNER, "_verify_sandbox_policy", lambda **kwargs: ("a" * 64, "b" * 64))
    monkeypatch.setattr(
        RUNNER,
        "_run_case_attempts",
        lambda *args, **kwargs: pytest.fail("candidate matrix must remain unreachable"),
    )
    monkeypatch.setattr(RUNNER, "HERE", output)

    with pytest.raises(RUNNER.RunnerError, match="client protocol is unavailable"):
        RUNNER.execute_campaign(
            venv=venv,
            scratch_parent=parent,
            output=output,
            repository=ROOT.parents[2],
        )

    assert not list(parent.iterdir())
    assert not list(output.iterdir())
    assert _attestation()["required_command_policy"]["mcp_client"]["external_components"] == []


class _DirectUrlDistribution:
    def __init__(self, value: dict[str, object]):
        self.value = value

    def read_text(self, name: str) -> str | None:
        assert name == "direct_url.json"
        return json.dumps(self.value)


def _record_digest(content: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")


def _pip_record_distribution(
    venv: Path,
) -> metadata.PathDistribution:
    site_packages = venv / "lib" / "python3.13" / "site-packages"
    dist_info = site_packages / "serena_agent-1.7.0.dist-info"
    dist_info.mkdir(parents=True)
    values = {
        "serena_agent/cli.py": b"def main():\n    return 0\n",
        "serena_agent-1.7.0.dist-info/METADATA": (
            b"Metadata-Version: 2.1\nName: serena-agent\nVersion: 1.7.0\n"
        ),
        "serena_agent-1.7.0.dist-info/entry_points.txt": (
            b"[console_scripts]\nserena = serena_agent.cli:main\n"
        ),
        "serena_agent-1.7.0.dist-info/direct_url.json": json.dumps({
            "url": "https://github.com/oraios/serena.git",
            "vcs_info": {
                "vcs": "git",
                "commit_id": POC.SERENA_RELEASE_COMMIT,
            },
        }).encode(),
        "../../../bin/serena": (
            f"#!{sys.executable}\nfrom serena_agent.cli import main\n"
        ).encode(),
    }
    for relative, content in values.items():
        target = site_packages / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    bytecode_relative = (
        f"serena_agent/__pycache__/cli.{sys.implementation.cache_tag}.pyc"
    )
    bytecode = site_packages / bytecode_relative
    bytecode.parent.mkdir(parents=True)
    py_compile.compile(
        str(site_packages / "serena_agent" / "cli.py"),
        cfile=str(bytecode),
        doraise=True,
    )
    rows = [
        f"{path},sha256={_record_digest(content)},{len(content)}"
        for path, content in values.items()
    ]
    rows.append(f"{bytecode_relative},,")
    rows.append("serena_agent-1.7.0.dist-info/RECORD,,")
    (dist_info / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return metadata.PathDistribution(dist_info)


def test_canonical_pip_console_script_and_generated_bytecode_record_are_accepted(
    monkeypatch,
    tmp_path,
):
    venv = tmp_path / "foundry-52-real-record"
    distribution = _pip_record_distribution(venv)
    monkeypatch.setattr(RUNNER.metadata, "distribution", lambda _name: distribution)

    binding = RUNNER._distribution_binding(
        POC.SERENA_DISTRIBUTION,
        POC.SERENA_VERSION,
        venv,
        provenance_kind="serena",
    )

    assert (venv / "bin" / "serena").resolve() in binding["resolved_files"]
    assert (
        venv / "lib" / "python3.13" / "site-packages" / "serena_agent" / "cli.py"
    ).resolve() in binding["resolved_files"]
    bytecode = (
        venv / "lib" / "python3.13" / "site-packages" / "serena_agent"
        / "__pycache__" / f"cli.{sys.implementation.cache_tag}.pyc"
    )
    assert bytecode.resolve() not in binding["resolved_files"]
    assert len(RUNNER._console_script_binding(
        distribution,
        binding["resolved_files"],
        venv,
        "serena",
    )) == 64

    distribution_digest = binding["distribution_sha256"]
    bytecode.write_bytes(b"different optional generated bytecode")
    assert RUNNER._distribution_binding(
        POC.SERENA_DISTRIBUTION,
        POC.SERENA_VERSION,
        venv,
        provenance_kind="serena",
    )["distribution_sha256"] == distribution_digest


def test_record_rejects_hashed_traversal_outside_selected_venv(monkeypatch, tmp_path):
    escaped = tmp_path / "outside-record-target"
    escaped.write_text("outside", encoding="utf-8")
    hostile_venv = tmp_path / "nested" / "foundry-52-hostile-record"
    hostile = _pip_record_distribution(hostile_venv)
    site_packages = hostile_venv / "lib" / "python3.13" / "site-packages"
    relative_escape = Path(os.path.relpath(escaped, site_packages)).as_posix()
    record = Path(hostile._path) / "RECORD"
    rows = record.read_text(encoding="utf-8").splitlines()
    rows.insert(
        -1,
        f"{relative_escape},sha256={_record_digest(escaped.read_bytes())},{escaped.stat().st_size}",
    )
    record.write_text("\n".join(rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(RUNNER.metadata, "distribution", lambda _name: hostile)

    with pytest.raises(RUNNER.RunnerError, match="RECORD path is invalid"):
        RUNNER._distribution_binding(
            POC.SERENA_DISTRIBUTION,
            POC.SERENA_VERSION,
            hostile_venv,
            provenance_kind="serena",
        )


def test_record_rejects_noncanonical_traversal_that_resolves_inside_selected_venv(
    monkeypatch,
    tmp_path,
):
    venv = tmp_path / "foundry-52-internal-traversal"
    distribution = _pip_record_distribution(venv)
    site_packages = venv / "lib" / "python3.13" / "site-packages"
    target = site_packages / "serena_agent" / "cli.py"
    record = Path(distribution._path) / "RECORD"
    rows = record.read_text(encoding="utf-8").splitlines()
    rows.insert(
        -1,
        "../site-packages/serena_agent/cli.py,"
        f"sha256={_record_digest(target.read_bytes())},{target.stat().st_size}",
    )
    record.write_text("\n".join(rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(RUNNER.metadata, "distribution", lambda _name: distribution)

    with pytest.raises(RUNNER.RunnerError, match="RECORD path is invalid"):
        RUNNER._distribution_binding(
            POC.SERENA_DISTRIBUTION,
            POC.SERENA_VERSION,
            venv,
            provenance_kind="serena",
        )


def test_record_rejects_symlink_that_resolves_inside_selected_venv(
    monkeypatch,
    tmp_path,
):
    venv = tmp_path / "foundry-52-internal-symlink"
    distribution = _pip_record_distribution(venv)
    package = venv / "lib" / "python3.13" / "site-packages" / "serena_agent"
    linked = package / "cli.py"
    target = package / "cli-real.py"
    linked.rename(target)
    linked.symlink_to(target.name)
    monkeypatch.setattr(RUNNER.metadata, "distribution", lambda _name: distribution)

    with pytest.raises(RUNNER.RunnerError, match="symlink is forbidden"):
        RUNNER._distribution_binding(
            POC.SERENA_DISTRIBUTION,
            POC.SERENA_VERSION,
            venv,
            provenance_kind="serena",
        )


@pytest.mark.parametrize(
    "unhashed_relative",
    [
        "serena_agent/__pycache__/cli.pyc",
        "serena_agent/unverified-data.json",
    ],
)
def test_record_rejects_every_other_unhashed_file(
    monkeypatch,
    tmp_path,
    unhashed_relative,
):
    venv = tmp_path / "foundry-52-unhashed-record"
    distribution = _pip_record_distribution(venv)
    site_packages = venv / "lib" / "python3.13" / "site-packages"
    target = site_packages / unhashed_relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"unverified")
    bytecode_relative = (
        f"serena_agent/__pycache__/cli.{sys.implementation.cache_tag}.pyc"
    )
    record = Path(distribution._path) / "RECORD"
    rows = record.read_text(encoding="utf-8").splitlines()
    rows[rows.index(f"{bytecode_relative},,")] = f"{unhashed_relative},,"
    record.write_text("\n".join(rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(RUNNER.metadata, "distribution", lambda _name: distribution)

    with pytest.raises(RUNNER.RunnerError, match="lacks a RECORD digest"):
        RUNNER._distribution_binding(
            POC.SERENA_DISTRIBUTION,
            POC.SERENA_VERSION,
            venv,
            provenance_kind="serena",
        )


def test_unhashed_bytecode_must_not_escape_lexically_or_through_a_symlink(
    monkeypatch,
    tmp_path,
):
    cache_name = f"escape.{sys.implementation.cache_tag}.pyc"
    outside = tmp_path / "outside" / "__pycache__" / cache_name
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"outside bytecode")

    lexical_venv = tmp_path / "nested" / "foundry-52-bytecode-traversal"
    lexical_distribution = _pip_record_distribution(lexical_venv)
    site_packages = lexical_venv / "lib" / "python3.13" / "site-packages"
    relative_escape = Path(os.path.relpath(outside, site_packages)).as_posix()
    record = Path(lexical_distribution._path) / "RECORD"
    rows = record.read_text(encoding="utf-8").splitlines()
    rows.insert(-1, f"{relative_escape},,")
    record.write_text("\n".join(rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        RUNNER.metadata,
        "distribution",
        lambda _name: lexical_distribution,
    )
    with pytest.raises(RUNNER.RunnerError, match="RECORD path is invalid"):
        RUNNER._distribution_binding(
            POC.SERENA_DISTRIBUTION,
            POC.SERENA_VERSION,
            lexical_venv,
            provenance_kind="serena",
        )

    symlink_venv = tmp_path / "foundry-52-bytecode-symlink"
    symlink_distribution = _pip_record_distribution(symlink_venv)
    bytecode = (
        symlink_venv / "lib" / "python3.13" / "site-packages" / "serena_agent"
        / "__pycache__" / f"cli.{sys.implementation.cache_tag}.pyc"
    )
    bytecode.unlink()
    bytecode.symlink_to(outside)
    monkeypatch.setattr(
        RUNNER.metadata,
        "distribution",
        lambda _name: symlink_distribution,
    )
    with pytest.raises(RUNNER.RunnerError, match="symlink is forbidden"):
        RUNNER._distribution_binding(
            POC.SERENA_DISTRIBUTION,
            POC.SERENA_VERSION,
            symlink_venv,
            provenance_kind="serena",
        )


def test_record_rejects_absolute_missing_malformed_and_invalid_encoding(
    monkeypatch,
    tmp_path,
):
    absolute_venv = tmp_path / "foundry-52-absolute-record"
    absolute_distribution = _pip_record_distribution(absolute_venv)
    absolute = tmp_path / "absolute-target"
    absolute.write_bytes(b"absolute")
    record = Path(absolute_distribution._path) / "RECORD"
    rows = record.read_text(encoding="utf-8").splitlines()
    rows.insert(-1, f"{absolute},,")
    record.write_text("\n".join(rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        RUNNER.metadata,
        "distribution",
        lambda _name: absolute_distribution,
    )
    with pytest.raises(RUNNER.RunnerError, match="path is invalid"):
        RUNNER._distribution_binding(
            POC.SERENA_DISTRIBUTION,
            POC.SERENA_VERSION,
            absolute_venv,
            provenance_kind="serena",
        )

    missing_venv = tmp_path / "foundry-52-missing-record-file"
    missing_distribution = _pip_record_distribution(missing_venv)
    missing_bytecode = (
        missing_venv / "lib" / "python3.13" / "site-packages" / "serena_agent"
        / "__pycache__" / f"cli.{sys.implementation.cache_tag}.pyc"
    )
    missing_bytecode.unlink()
    monkeypatch.setattr(
        RUNNER.metadata,
        "distribution",
        lambda _name: missing_distribution,
    )
    with pytest.raises(RUNNER.RunnerError, match="file is unavailable"):
        RUNNER._distribution_binding(
            POC.SERENA_DISTRIBUTION,
            POC.SERENA_VERSION,
            missing_venv,
            provenance_kind="serena",
        )

    malformed_venv = tmp_path / "foundry-52-malformed-record"
    malformed_distribution = _pip_record_distribution(malformed_venv)
    malformed_record = Path(malformed_distribution._path) / "RECORD"
    malformed_record.write_text("only,two\n", encoding="utf-8")
    monkeypatch.setattr(
        RUNNER.metadata,
        "distribution",
        lambda _name: malformed_distribution,
    )
    with pytest.raises(RUNNER.RunnerError, match="RECORD is malformed"):
        RUNNER._distribution_binding(
            POC.SERENA_DISTRIBUTION,
            POC.SERENA_VERSION,
            malformed_venv,
            provenance_kind="serena",
        )

    encoding_venv = tmp_path / "foundry-52-encoding-record"
    encoding_distribution = _pip_record_distribution(encoding_venv)
    encoding_record = Path(encoding_distribution._path) / "RECORD"
    encoding_record.write_bytes(encoding_record.read_bytes() + b"\xff")
    monkeypatch.setattr(
        RUNNER.metadata,
        "distribution",
        lambda _name: encoding_distribution,
    )
    with pytest.raises(RUNNER.RunnerError, match="encoding is invalid"):
        RUNNER._distribution_binding(
            POC.SERENA_DISTRIBUTION,
            POC.SERENA_VERSION,
            encoding_venv,
            provenance_kind="serena",
        )


def test_declared_source_and_wheel_pins_must_match_installed_distribution_provenance():
    serena = _DirectUrlDistribution({
        "url": "https://github.com/oraios/serena.git",
        "vcs_info": {
            "vcs": "git",
            "commit_id": POC.SERENA_RELEASE_COMMIT,
        },
    })
    assert len(RUNNER._direct_url_binding(serena, "serena")) == 64
    serena.value["vcs_info"]["commit_id"] = "0" * 40
    with pytest.raises(RUNNER.RunnerError, match="source pin"):
        RUNNER._direct_url_binding(serena, "serena")

    pyright = _DirectUrlDistribution({
        "url": "file:///temporary/pyright.whl",
        "archive_info": {"hashes": {"sha256": POC.PYRIGHT_WHEEL_SHA256}},
    })
    assert len(RUNNER._direct_url_binding(pyright, "pyright")) == 64
    pyright.value["archive_info"]["hashes"]["sha256"] = "0" * 64
    with pytest.raises(RUNNER.RunnerError, match="wheel pin"):
        RUNNER._direct_url_binding(pyright, "pyright")


def test_run_binding_uses_current_runner_digest_and_rejects_stale_or_missing_digest():
    frozen = _frozen()
    runtime = _runtime("c")
    runner_digest = hashlib.sha256(
        (ROOT / "run-serena-poc-v1.py").read_bytes()
    ).hexdigest()
    assert POC.RUNNER_SHA256 == runner_digest
    runtime["runner_sha256"] = runner_digest
    binding = RUNNER._run_binding(runtime, POC.build_execution_plan(frozen))
    assert binding["pyright_langserver_executable_sha256"] == "c" * 64

    POC._validate_run_binding(binding, frozen)
    forged = copy.deepcopy(binding)
    forged["runner_sha256"] = "0" * 64
    with pytest.raises(POC.SerenaPocError, match="runner source digest"):
        POC._validate_run_binding(forged, frozen)
    forged = copy.deepcopy(binding)
    forged["pyright_langserver_executable_sha256"] = None
    with pytest.raises(POC.SerenaPocError, match="runtime binding digest"):
        POC._validate_run_binding(forged, frozen)


def _candidate_comparison_report(
    candidate: str,
    *,
    recall: float,
    size: int,
    latency: int,
    contract: str | None = None,
) -> dict[str, object]:
    return {
        "candidate": candidate,
        "planned_attempts": 21,
        "observed_attempts": 21,
        "measurement_contract_sha256": contract or POC.measurement_contract_sha256(),
        "metrics": {
            "recall": {"state": "observed", "value": recall},
            "bytes": {"state": "observed", "value": size},
            "latency_ms": {"state": "observed", "value": latency},
        },
    }


def test_cross_candidate_comparison_rejects_heterogeneous_metric_semantics():
    reports = [
        _candidate_comparison_report("lexical", recall=0.8, size=100, latency=100),
        _candidate_comparison_report("local_code", recall=0.7, size=120, latency=110),
        _candidate_comparison_report(
            "serena", recall=0.8, size=70, latency=75, contract="0" * 64,
        ),
    ]

    comparison = POC._comparison(reports)

    assert comparison["decision"] == "inconclusive_no_promotion"
    assert comparison["comparability"]["measurement_contract_homogeneous"] is False
    assert all(value is None for value in comparison["marginal_deltas"].values())


def test_decision_follows_explicit_predicates_instead_of_a_hard_coded_outcome():
    passing = [
        _candidate_comparison_report("lexical", recall=0.8, size=100, latency=100),
        _candidate_comparison_report("local_code", recall=0.7, size=120, latency=110),
        _candidate_comparison_report("serena", recall=0.8, size=70, latency=75),
    ]
    accepted = POC._comparison(passing)
    assert accepted["predicates"]["all_satisfied"] is True
    assert accepted["decision"] == "diagnostic_candidate_for_separate_authorised_follow_up"
    assert accepted["production_promotion"] == "prohibited"

    failing = copy.deepcopy(passing)
    failing[2]["metrics"]["recall"]["value"] = 0.6
    rejected = POC._comparison(failing)
    assert rejected["predicates"]["all_satisfied"] is False
    assert rejected["decision"] == "rejected_current_f51_evidence"


def test_verified_evidence_attestation_bind_runtime_plan_policy_and_server():
    frozen = _frozen()
    runtime = _runtime("d")
    runtime["runner_sha256"] = POC.RUNNER_SHA256
    attempts = _verified_attempts(frozen)
    evidence = RUNNER.build_evidence(frozen, runtime, attempts)
    attestation = RUNNER.build_attestation(frozen, evidence)

    POC._validate_evidence(evidence, frozen)
    POC._validate_attestation(attestation, frozen, evidence)
    assert evidence["run_binding"]["pyright_langserver_executable_sha256"] == "d" * 64
    assert attestation["attempts_sha256"] == RUNNER._canonical_sha256(attempts)

    forged = copy.deepcopy(evidence)
    forged["attempts"][42]["server_identity_sha256"] = None
    with pytest.raises(POC.SerenaPocError, match="server provenance"):
        POC._validate_evidence(forged, frozen)


def test_cleanup_failure_prevents_new_campaign_bundle(monkeypatch, tmp_path):
    output = tmp_path / "output"
    parent = tmp_path / "parent"
    venv = tmp_path / "foundry-52-venv"
    output.mkdir()
    parent.mkdir()
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "serena").write_text("placeholder", encoding="utf-8")
    frozen = _frozen()

    monkeypatch.setattr(RUNNER.VALIDATOR, "validate_f51_freeze", lambda repository: frozen)
    monkeypatch.setattr(RUNNER, "_runtime_attestation", lambda selected: _runtime())
    monkeypatch.setattr(RUNNER, "_resolve_venv_file", lambda *args, **kwargs: venv / "bin" / "serena")
    monkeypatch.setattr(RUNNER, "_verify_sandbox_policy", lambda **kwargs: ("a" * 64, "b" * 64))
    monkeypatch.setattr(RUNNER, "_run_case_attempts", lambda *args, **kwargs: [])
    monkeypatch.setattr(RUNNER, "_cleanup_owned", lambda paths: False)
    monkeypatch.setattr(RUNNER, "HERE", output)

    with pytest.raises(RUNNER.RunnerError, match="cleanup failed"):
        RUNNER.execute_campaign(
            venv=venv, scratch_parent=parent, output=output, repository=ROOT.parents[2],
        )

    assert not (output / "evidence-v1.json").exists()
    assert not (output / "attestation-v1.json").exists()
    assert not (output / "report-v1.json").exists()


def test_report_and_digest_chain_reject_forged_measurements(tmp_path):
    report = _artifact("report-v1.json")
    report["comparison"]["decision"] = "rejected_current_f51_evidence"
    with pytest.raises(POC.SerenaPocError, match="not derived"):
        POC._validate_report(report, _frozen(), _evidence(), _attestation())

    isolated = tmp_path / "foundry-52"
    shutil.copytree(ROOT, isolated)
    evidence = json.loads((isolated / "evidence-v1.json").read_text(encoding="utf-8"))
    evidence["attempts"][0]["latency_ms"] = 0
    (isolated / "evidence-v1.json").write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(POC.SerenaPocError, match="evidence digest"):
        POC.validate_artifacts(isolated)


def test_persisted_artifacts_contain_only_bounded_sanitised_fields():
    evidence = _artifact("evidence-v1.json")
    forbidden_keys = {
        "prompt", "response", "raw_mcp_output", "stderr", "excerpt", "path",
        "code", "secret", "credential", "token", "user_identifier", "user_data",
    }

    def walk(value):
        if type(value) is dict:
            assert not (set(value) & forbidden_keys)
            for child in value.values():
                walk(child)
        elif type(value) is list:
            for child in value:
                walk(child)

    walk(evidence)
    encoded = json.dumps(evidence, sort_keys=True)
    assert "/Users/" not in encoded
    assert "/private/" not in encoded
    assert "Traceback" not in encoded


def test_runner_is_inert_without_execute():
    with pytest.raises(RUNNER.RunnerError, match="requires --execute"):
        RUNNER.main([])
