from __future__ import annotations

import json
import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from foundry import config
from foundry.command_runtime import (
    AUTHORITY_CONTRACT,
    LEGACY_AUTHORITY_CONTRACT,
    ClaudeCommandEffectProvider,
    FileAuthoritySource,
    _claude_child_environment,
)
from foundry.command_worker import (
    CommandWorkerError,
    ExecutionAuthorization,
    PreEffectCapacityError,
    _binding_digest,
    _digest,
)
from foundry.devhub_commands import CommandLease, DevHubCommand
from foundry.execution_receipts import ATTEMPT_ID_ENV, RECEIPT_DIRECTORY_ENV
from foundry.routing import RoutingConfigError


DIGEST_B = "b" * 64
_DEFAULT_MAPPING = object()


def authority_material(
    *, blockers=None, acceptance_mapping=_DEFAULT_MAPPING,
    provider_invocation_ceiling_cents=300,
):
    policy = {"implementer_minimum_tier": "balanced", "source": "approved-policy"}
    policy_digest = _digest(policy)
    plan = {
        "waves": [{"issue_ids": ["DEVHUB-21", "DEVHUB-22"]}],
        "blockers": list(blockers or []),
        "required_gates": ["tests", "independent-review", "human-test"],
    }
    if acceptance_mapping is _DEFAULT_MAPPING:
        acceptance_mapping = [
            {
                "criterion_id": "ac-1", "criterion_digest": "1" * 64,
                "issue_id": "DEVHUB-21",
            },
            {
                "criterion_id": "ac-2", "criterion_digest": "2" * 64,
                "issue_id": "DEVHUB-22",
            },
        ]
    if acceptance_mapping is not None:
        plan["acceptance_mapping"] = acceptance_mapping
    preview = {
        "actor": "foundry-service", "epic_version": 7, "expires_at": 20_000,
        "limits": {"max_cost_cents": 500, "max_concurrency": 4},
        "plan": plan,
    }
    preview_digest = _digest({
        "contract": "devhub-foundry-command.v1", "project": "DEVHUB",
        "actor": preview["actor"], "intent": {"type": "execute-epic"},
        "epic_id": "DEVHUB-20", "epic_version": preview["epic_version"],
        "planning_version_id": "DEVHUB-VERSION-1", "snapshot_digest": DIGEST_B,
        "policy_digest": policy_digest, "expires_at": preview["expires_at"],
        "limits": preview["limits"], "plan": plan,
    })
    command = DevHubCommand(
        id="command-1", project="DEVHUB", preview_id="preview-1",
        approval_id="approval-1", preview_digest=preview_digest,
        snapshot_digest=DIGEST_B, policy_digest=policy_digest,
        epic_id="DEVHUB-20", planning_version_id="DEVHUB-VERSION-1",
        max_cost_cents=500, max_concurrency=4, state="accepted",
        projection_stale=False, next_event_sequence=1, scheduled_for=None,
        lease=CommandLease("lease-1", "worker-1", 30_000, 10_000),
        version=4, created_at=100, updated_at=10_000,
    )
    document = {
        "contract": AUTHORITY_CONTRACT,
        "command": {
            "id": command.id, "project": command.project,
            "preview_id": command.preview_id, "approval_id": command.approval_id,
            "epic_id": command.epic_id,
            "planning_version_id": command.planning_version_id,
            "snapshot_digest": command.snapshot_digest,
            "policy_digest": command.policy_digest,
            "scheduled_for": command.scheduled_for, "created_at": command.created_at,
        },
        "preview": preview,
        "approval": {
            "id": "approval-1", "preview_id": "preview-1", "state": "approved",
            "expires_at": 19_000,
        },
        "policy": policy,
        "local": {
            "observed_at": 9_000, "valid_until": 15_000,
            "minimum_tier": "frontier", "max_cost_cents": 400,
            "max_concurrency": 3, "budget_remaining_cents": 300,
            "active_concurrency": 1, "host_available": True,
            "provider_invocation_ceiling_cents": (
                provider_invocation_ceiling_cents
            ),
        },
    }
    return command, document


def write_authority(tmp_path, document):
    directory = tmp_path / "authority"
    directory.mkdir()
    (directory / "command-1.json").write_text(json.dumps(document), encoding="utf-8")
    return directory


def test_file_authority_recomputes_digests_and_rejects_stale_or_changed_input(tmp_path):
    command, document = authority_material(blockers=["DEVHUB-23"])
    directory = write_authority(tmp_path, document)
    source = FileAuthoritySource(directory, now_ms=lambda: 10_000)

    observed = source.load(command)
    assert observed.waves == (("DEVHUB-21", "DEVHUB-22"),)
    assert observed.blockers == ("DEVHUB-23",)
    assert observed.observation.provider_invocation_ceiling_cents == 300
    assert observed.observation.required_gates == frozenset({
        "tests", "independent-review", "human-test",
    })

    changed = replace(command, max_cost_cents=499)
    with pytest.raises(CommandWorkerError, match="limites approuvées"):
        source.load(changed)
    document["local"]["valid_until"] = 10_000
    (directory / "command-1.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CommandWorkerError, match="expirée"):
        source.load(command)


@pytest.mark.parametrize("legacy_contract", [False, True])
def test_runtime_rejects_authority_without_explicit_provider_ceiling_before_process(
    tmp_path, legacy_contract,
):
    command, document = authority_material()
    del document["local"]["provider_invocation_ceiling_cents"]
    if legacy_contract:
        document["contract"] = LEGACY_AUTHORITY_CONTRACT
    calls = []
    provider = ClaudeCommandEffectProvider(
        FileAuthoritySource(write_authority(tmp_path, document), now_ms=lambda: 10_000),
        root=tmp_path, effect_directory=tmp_path / "effects",
        runner=lambda *_args, **_kwargs: calls.append(True),
    )

    with pytest.raises(PreEffectCapacityError, match="plafond par invocation"):
        provider.execution_profile(command, None)

    assert calls == []


def test_file_authority_rejects_noncanonical_gates_and_symlink(tmp_path):
    command, document = authority_material()
    document["preview"]["plan"]["required_gates"].append("optional-gate")
    directory = write_authority(tmp_path, document)
    source = FileAuthoritySource(directory, now_ms=lambda: 10_000)

    with pytest.raises(CommandWorkerError, match="non canoniques"):
        source.load(command)

    command_path = directory / "command-1.json"
    target_path = directory / "target.json"
    target_path.write_text(json.dumps(document), encoding="utf-8")
    command_path.unlink()
    command_path.symlink_to(target_path.name)
    with pytest.raises(CommandWorkerError, match="absente"):
        source.load(command)


def test_file_authority_rejects_missing_mapping_outside_exact_legacy_campaign(tmp_path):
    command, document = authority_material(acceptance_mapping=None)
    source = FileAuthoritySource(
        write_authority(tmp_path, document), now_ms=lambda: 10_000,
    )

    with pytest.raises(CommandWorkerError, match="plan invalide"):
        source.load(command)


def test_file_authority_persists_an_exact_approved_ac_child_mapping(tmp_path):
    mapping = [
        {"criterion_id": "ac-2", "criterion_digest": "2" * 64, "issue_id": "DEVHUB-22"},
        {"criterion_id": "ac-1", "criterion_digest": "1" * 64, "issue_id": "DEVHUB-21"},
    ]
    command, document = authority_material(acceptance_mapping=mapping)
    source = FileAuthoritySource(
        write_authority(tmp_path, document), now_ms=lambda: 10_000,
    )

    authority = source.load(command)

    assert authority.acceptance_mapping == (
        ("ac-2", "2" * 64, "DEVHUB-22"),
        ("ac-1", "1" * 64, "DEVHUB-21"),
    )
    document["preview"]["plan"]["acceptance_mapping"][0]["issue_id"] = "DEVHUB-21"
    (source.directory / "command-1.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CommandWorkerError, match="mapping AC approuvé invalide"):
        source.load(command)

    empty_command, empty_document = authority_material(acceptance_mapping=[])
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    empty_source = FileAuthoritySource(
        write_authority(empty_root, empty_document), now_ms=lambda: 10_000,
    )
    with pytest.raises(CommandWorkerError, match="mapping AC approuvé invalide"):
        empty_source.load(empty_command)


def test_concrete_claude_runtime_enforces_budget_floor_and_scrubs_authority_secrets(
    tmp_path, monkeypatch,
):
    command, document = authority_material()
    directory = write_authority(tmp_path, document)
    routing_config = tmp_path / ".foundry" / "model-routing.json"
    routing_config.parent.mkdir()
    routing_config.write_text(json.dumps({
        "mappings": {"claude": {"frontier": {"model": "runtime-model"}}},
        "claude_models": {"runtime-model": "runtime-wire-alias"},
    }), encoding="utf-8")
    calls = []

    def runner(argv, **kwargs):
        keychain_calls = []

        def keychain_secret(account):
            keychain_calls.append(account)
            return f"keychain-{account}-secret"

        with (
            patch.dict(os.environ, kwargs["env"], clear=True),
            patch.object(config, "_keychain_secret", side_effect=keychain_secret),
            patch.object(config, "_load_dev_files", wraps=config._load_dev_files) as load_files,
        ):
            assert os.environ["HOME"] == str(child_home)
            assert "FOUNDRY_CONFIG" not in os.environ
            assert os.environ[config.RUNTIME_CONFIG_ISOLATION_ENV] == "1"
            assert config.default_config_path() == fallback_config
            assert fallback_config.is_file()
            assert config.get_public("DEVHUB_URL") is None
            for name in (
                "YOUTRACK_TOKEN", "DEVHUB_TRACKER_TOKEN",
                "DEVHUB_TRACKER_PROOF_SECRET", "DEVHUB_COMMAND_TOKEN",
            ):
                assert config.get(name) is None
            assert keychain_calls == []
            load_files.assert_not_called()
        calls.append((argv, kwargs))
        session_flag = "--resume" if "--resume" in argv else "--session-id"
        session_id = argv[argv.index(session_flag) + 1]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "session_id": session_id, "is_error": False,
                "total_cost_usd": 2.99, "duration_ms": 2_000,
                "result": "done",
            }),
        )

    authority_secrets = {
        "YOUTRACK_TOKEN",
        "DEVHUB_TRACKER_TOKEN",
        "DEVHUB_TRACKER_PROOF_SECRET",
        "DEVHUB_COMMAND_TOKEN",
        "CLAUDE_PLUGIN_OPTION_YOUTRACK_TOKEN",
        "CLAUDE_PLUGIN_OPTION_DEVHUB_TRACKER_TOKEN",
        "CLAUDE_PLUGIN_OPTION_DEVHUB_TRACKER_PROOF_SECRET",
        "CLAUDE_PLUGIN_OPTION_DEVHUB_COMMAND_TOKEN",
        "GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN", "UNREVIEWED_FUTURE_PROVIDER_CREDENTIAL",
    }
    child_home = tmp_path / "child-home"
    fallback_config = child_home / ".config" / "foundry" / "config.env"
    fallback_config.parent.mkdir(parents=True)
    fallback_config.write_text(
        "YOUTRACK_TOKEN=file-youtrack-secret\n"
        "DEVHUB_TRACKER_TOKEN=file-tracker-secret\n"
        "DEVHUB_TRACKER_PROOF_SECRET=file-proof-secret\n"
        "DEVHUB_COMMAND_TOKEN=file-command-secret\n",
        encoding="utf-8",
    )
    explicit_config = tmp_path / "explicit-foundry.env"
    explicit_config.write_text(
        "DEVHUB_TRACKER_TOKEN=explicit-tracker-secret\n",
        encoding="utf-8",
    )
    for name in authority_secrets:
        monkeypatch.setenv(name, f"{name}-secret")
    monkeypatch.setenv("HOME", str(child_home))
    monkeypatch.setenv("USER", "foundry-child")
    monkeypatch.setenv("LOGNAME", "foundry-child")
    monkeypatch.setenv("FOUNDRY_CONFIG", str(explicit_config))
    monkeypatch.setenv("DEVHUB_URL", "https://runtime-public.example")
    monkeypatch.setenv("FOUNDRY_CLAUDE_RUNTIME_KEEP", "required-setting")
    provider = ClaudeCommandEffectProvider(
        FileAuthoritySource(directory, now_ms=lambda: 10_000),
        root=tmp_path, effect_directory=tmp_path / "effects", runner=runner,
    )
    profile = provider.execution_profile(command, None)
    assert (profile.selected_tier, profile.cost_ceiling_cents) == ("frontier", 300)
    authorization = ExecutionAuthorization(
        command_id=command.id, binding_digest=_binding_digest(command),
        selected_tier="frontier", cost_ceiling_cents=300, concurrency_units=1,
        approved_max_cost_cents=300, approved_max_concurrency=3,
        minimum_tier="frontier",
        provider_invocation_ceiling_cents=300,
    )
    heartbeats = []
    reconciled = []

    def reconcile_capacity(observation):
        assert calls == []
        reconciled.append(observation)

    effect = provider.launch(
        command, authorization, effect_id=command.id,
        heartbeat=lambda: heartbeats.append(True),
        reconcile_capacity=reconcile_capacity,
    )

    assert effect.status == "completed"
    assert effect.cost_cents == 299
    assert effect.attempt_id == ClaudeCommandEffectProvider._session_id(
        command.id, authorization.binding_digest,
    )
    assert effect.attempt_id != command.id
    assert effect.attempt_started_at == 10_000
    assert provider.resolve(command.id, authorization.binding_digest) == effect
    assert heartbeats == []
    assert len(reconciled) == 1
    assert reconciled[0].budget_remaining_cents == 300
    argv, kwargs = calls[0]
    assert argv[argv.index("--max-budget-usd") + 1] == "3.00"
    assert argv[argv.index("--model") + 1] == "runtime-wire-alias"
    assert "runtime-model" not in argv
    assert authority_secrets.isdisjoint(kwargs["env"])
    assert "DEVHUB_URL" not in kwargs["env"]
    assert "FOUNDRY_CLAUDE_RUNTIME_KEEP" not in kwargs["env"]
    assert kwargs["env"]["USER"] == "foundry-child"
    assert kwargs["env"]["LOGNAME"] == "foundry-child"
    session_id = argv[argv.index("--session-id") + 1]
    assert kwargs["env"][ATTEMPT_ID_ENV] == session_id
    assert kwargs["env"][RECEIPT_DIRECTORY_ENV] == str(
        tmp_path / "execution-receipts"
    )
    assert set(kwargs["env"]) <= {
        "HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER",
        "FOUNDRY_RUNTIME_CONFIG_ISOLATED", ATTEMPT_ID_ENV, RECEIPT_DIRECTORY_ENV,
    }
    assert "DEVHUB-21, DEVHUB-22" in kwargs["input"]


def test_claude_child_environment_has_an_exact_non_secret_allowlist(monkeypatch):
    names = ("HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER")
    expected = {name: f"value-{name}" for name in names}
    for name, value in expected.items():
        monkeypatch.setenv(name, value)

    environment = _claude_child_environment()

    assert environment == {
        **expected,
        "FOUNDRY_RUNTIME_CONFIG_ISOLATED": "1",
    }


@pytest.mark.parametrize("missing", ["USER", "LOGNAME"])
def test_claude_child_environment_omits_each_absent_local_identity(
    monkeypatch, missing,
):
    monkeypatch.setenv("USER", "foundry-user")
    monkeypatch.setenv("LOGNAME", "foundry-logname")
    monkeypatch.delenv(missing)

    environment = _claude_child_environment()

    assert missing not in environment
    present = "LOGNAME" if missing == "USER" else "USER"
    assert environment[present] == f"foundry-{present.lower()}"


def test_concrete_runtime_refuses_any_looser_authorization_before_process(tmp_path):
    command, document = authority_material()
    directory = write_authority(tmp_path, document)
    calls = []
    provider = ClaudeCommandEffectProvider(
        FileAuthoritySource(directory, now_ms=lambda: 10_000),
        root=tmp_path, effect_directory=tmp_path / "effects",
        runner=lambda *_args, **_kwargs: calls.append(True),
    )
    authorization = ExecutionAuthorization(
        command_id=command.id, binding_digest=_binding_digest(command),
        selected_tier="frontier", cost_ceiling_cents=301, concurrency_units=1,
        approved_max_cost_cents=301, approved_max_concurrency=3,
        minimum_tier="frontier",
        provider_invocation_ceiling_cents=300,
    )

    with pytest.raises(CommandWorkerError, match="autorisation runtime"):
        provider.launch(
            command, authorization, effect_id=command.id, heartbeat=lambda: None,
            reconcile_capacity=lambda _observation: None,
        )
    assert calls == []


def test_concrete_runtime_suspends_when_provider_cap_tightens_after_authorization(
    tmp_path,
):
    command, document = authority_material()
    directory = write_authority(tmp_path, document)
    calls = []
    reconciled = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        session_flag = "--resume" if "--resume" in argv else "--session-id"
        session_id = argv[argv.index(session_flag) + 1]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "session_id": session_id, "is_error": False,
                "total_cost_usd": 2.99, "duration_ms": 2_000,
            }),
        )

    provider = ClaudeCommandEffectProvider(
        FileAuthoritySource(directory, now_ms=lambda: 10_000),
        root=tmp_path, effect_directory=tmp_path / "effects", runner=runner,
    )
    profile = provider.execution_profile(command, None)
    authorization = ExecutionAuthorization(
        command_id=command.id, binding_digest=_binding_digest(command),
        selected_tier=profile.selected_tier,
        cost_ceiling_cents=profile.cost_ceiling_cents, concurrency_units=1,
        approved_max_cost_cents=profile.cost_ceiling_cents,
        approved_max_concurrency=3, minimum_tier="frontier",
        provider_invocation_ceiling_cents=300,
    )
    document["local"]["provider_invocation_ceiling_cents"] = 200
    (directory / "command-1.json").write_text(
        json.dumps(document), encoding="utf-8",
    )

    with pytest.raises(PreEffectCapacityError, match="resserré"):
        provider.launch(
            command, authorization, effect_id=command.id, heartbeat=lambda: None,
            reconcile_capacity=reconciled.append,
        )

    assert calls == []
    assert reconciled == []
    assert provider.resolve(command.id, authorization.binding_digest) is None

    document["local"]["provider_invocation_ceiling_cents"] = 300
    (directory / "command-1.json").write_text(
        json.dumps(document), encoding="utf-8",
    )
    effect = provider.launch(
        command, authorization, effect_id=command.id, heartbeat=lambda: None,
        reconcile_capacity=reconciled.append,
    )

    assert effect.status == "completed"
    assert effect.cost_cents == 299
    assert len(calls) == 1
    assert len(reconciled) == 1


def test_command_runtime_rejects_untranslated_claude_model_before_effect_state(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "mappings": {"claude": {"balanced": {"model": "untranslated-model"}}},
    }), encoding="utf-8")
    effect_directory = tmp_path / "effects"

    with pytest.raises(RoutingConfigError, match="sans traduction hôte"):
        ClaudeCommandEffectProvider(
            object(), root=tmp_path, effect_directory=effect_directory,
        )
    assert not effect_directory.exists()
