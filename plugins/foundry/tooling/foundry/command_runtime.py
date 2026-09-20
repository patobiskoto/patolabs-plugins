"""Concrete outbound runtime for approved DevHub command effects.

The production entry point composes the trusted campaign coordinator with Foundry's
existing issue primitives. ``ClaudeCommandEffectProvider`` remains as a compatibility
fixture for the single-process FOUNDRY-86 contract; it is no longer the production
campaign path.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from foundry import config, registry
from foundry.campaign_coordinator import LEGACY_F89_CAMPAIGN_ID
from foundry.campaign_runtime import CampaignCommandEffectProvider
from foundry.command_worker import (
    MAX_RECEIPT_BYTES,
    RECEIPT_CONTRACT,
    REQUIRED_GATES,
    CommandWorker,
    CommandWorkerError,
    EffectReceipt,
    ExecutionAuthorization,
    ExecutionProfile,
    HostReservationStore,
    PreEffectCapacityError,
    ReceiptStore,
    RevalidationObservation,
    WorkerOutcome,
    _binding_digest,
    _canonical,
    _digest,
    CommandJournalEventTransport,
    revalidate_command,
)
from foundry.devhub_events import PassiveEventPublisher
from foundry.devhub_commands import DevHubCommand, DevHubCommandClient
from foundry.execution_receipts import (
    ATTEMPT_ID_ENV,
    RECEIPT_DIRECTORY_ENV,
    ExecutionReceiptStore,
)
from foundry.routing import LEVELS
from foundry.routing_facades import _load_claude_policy, claude_invocation_model


AUTHORITY_CONTRACT = "foundry-command-authority.v3"
LEGACY_AUTHORITY_CONTRACT = "foundry-command-authority.v2"
MAX_AUTHORITY_BYTES = 64 * 1024
MAX_RUNTIME_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_OBSERVATION_AGE_MS = 5 * 60 * 1000
MAX_EXECUTION_SECONDS = 6 * 60 * 60
_ISSUE_ID = re.compile(r"^[A-Z][A-Z0-9]{1,15}-\d+$")
_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,119}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CLAUDE_CHILD_ENV_ALLOWLIST = (
    "HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER",
)


def _claude_child_environment() -> dict[str, str]:
    """Build the complete non-secret environment accepted by the Claude child."""
    environment = {
        name: os.environ[name]
        for name in _CLAUDE_CHILD_ENV_ALLOWLIST
        if name in os.environ
    }
    environment[config.RUNTIME_CONFIG_ISOLATION_ENV] = "1"
    return environment


@dataclass(frozen=True)
class RuntimeAuthority:
    observation: RevalidationObservation
    waves: tuple[tuple[str, ...], ...]
    blockers: tuple[str, ...]
    acceptance_mapping: tuple[tuple[str, str, str], ...] = ()


def _exact(value: object, keys: set[str], where: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise CommandWorkerError(f"autorité locale {where} invalide")
    return value


def _integer(value: object, where: str, *, minimum: int = 0, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CommandWorkerError(f"autorité locale {where} invalide")
    return value


def _tier(value: object, where: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or value not in LEVELS:
        raise CommandWorkerError(f"autorité locale {where} invalide")
    return value


class FileAuthoritySource:
    """Re-read one bounded, digest-bound local observation on every check."""

    def __init__(
        self, directory: str | os.PathLike, *, now_ms: Callable[[], int] | None = None,
    ):
        self.directory = Path(directory).expanduser().resolve()
        if not self.directory.is_dir():
            raise ValueError("répertoire d'autorité Foundry absent")
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))

    def _path(self, command_id: str) -> Path:
        if _IDENTIFIER.fullmatch(command_id) is None:
            raise CommandWorkerError("identité d'autorité Foundry invalide")
        candidate = self.directory / f"{command_id}.json"
        if candidate.is_symlink():
            raise CommandWorkerError("observation d'autorité Foundry absente")
        path = candidate.resolve()
        try:
            path.relative_to(self.directory)
        except ValueError:
            raise CommandWorkerError("chemin d'autorité Foundry invalide") from None
        if path.is_symlink() or not path.is_file():
            raise CommandWorkerError("observation d'autorité Foundry absente")
        return path

    def load(self, command: DevHubCommand) -> RuntimeAuthority:
        path = self._path(command.id)
        try:
            raw_bytes = path.read_bytes()
            if len(raw_bytes) > MAX_AUTHORITY_BYTES:
                raise ValueError
            raw = json.loads(raw_bytes.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            raise CommandWorkerError("observation d'autorité Foundry invalide") from None
        document = _exact(
            raw, {"contract", "command", "preview", "approval", "policy", "local"},
            "racine",
        )
        if document["contract"] not in {
            AUTHORITY_CONTRACT, LEGACY_AUTHORITY_CONTRACT,
        }:
            raise CommandWorkerError("contrat d'autorité Foundry inconnu")
        legacy_authority = document["contract"] == LEGACY_AUTHORITY_CONTRACT
        bound = _exact(document["command"], {
            "id", "project", "preview_id", "approval_id", "epic_id",
            "planning_version_id", "snapshot_digest", "policy_digest",
            "scheduled_for", "created_at",
        }, "command")
        expected = {
            "id": command.id, "project": command.project,
            "preview_id": command.preview_id, "approval_id": command.approval_id,
            "epic_id": command.epic_id,
            "planning_version_id": command.planning_version_id,
            "snapshot_digest": command.snapshot_digest,
            "policy_digest": command.policy_digest,
            "scheduled_for": command.scheduled_for, "created_at": command.created_at,
        }
        if bound != expected:
            raise CommandWorkerError("binding d'autorité locale modifié")

        preview = _exact(document["preview"], {
            "actor", "epic_version", "expires_at", "limits", "plan",
        }, "preview")
        if (not isinstance(preview["actor"], str) or not 1 <= len(preview["actor"]) <= 120
                or type(preview["epic_version"]) is not int
                or preview["epic_version"] < 1):
            raise CommandWorkerError("autorité locale preview invalide")
        limits = _exact(preview["limits"], {"max_cost_cents", "max_concurrency"}, "limits")
        approved_cost = _integer(
            limits["max_cost_cents"], "limits.max_cost_cents", minimum=1, maximum=1_000_000,
        )
        approved_concurrency = _integer(
            limits["max_concurrency"], "limits.max_concurrency", minimum=1, maximum=16,
        )
        if (approved_cost != command.max_cost_cents
                or approved_concurrency != command.max_concurrency):
            raise CommandWorkerError("limites approuvées locales modifiées")
        raw_plan = preview["plan"]
        legacy_plan_keys = {"waves", "blockers", "required_gates"}
        mapped_plan_keys = legacy_plan_keys | {"acceptance_mapping"}
        if (not isinstance(raw_plan, dict)
                or frozenset(raw_plan) not in {
                    frozenset(legacy_plan_keys), frozenset(mapped_plan_keys),
                }
                or (frozenset(raw_plan) == frozenset(legacy_plan_keys)
                    and command.id != LEGACY_F89_CAMPAIGN_ID)):
            raise CommandWorkerError("autorité locale plan invalide")
        plan = raw_plan
        if (not isinstance(plan["waves"], list) or not 1 <= len(plan["waves"]) <= 50
                or not isinstance(plan["blockers"], list) or len(plan["blockers"]) > 200
                or any(not isinstance(item, str) or _ISSUE_ID.fullmatch(item) is None
                       for item in plan["blockers"])):
            raise CommandWorkerError("plan d'autorité Foundry invalide")
        waves = []
        seen: set[str] = set()
        for raw_wave in plan["waves"]:
            wave = _exact(raw_wave, {"issue_ids"}, "wave")
            ids = wave["issue_ids"]
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 50
                    or any(not isinstance(item, str) or _ISSUE_ID.fullmatch(item) is None
                           for item in ids)
                    or seen.intersection(ids)):
                raise CommandWorkerError("wave d'autorité Foundry invalide")
            seen.update(ids)
            waves.append(tuple(ids))
        gates = plan["required_gates"]
        if (not isinstance(gates, list) or len(gates) != len(set(gates))
                or any(not isinstance(item, str) for item in gates)):
            raise CommandWorkerError("gates d'autorité Foundry invalides")
        required_gates = frozenset(gates)
        if required_gates != REQUIRED_GATES:
            raise CommandWorkerError("gates d'autorité Foundry non canoniques")
        raw_mapping = plan.get("acceptance_mapping", [])
        if (not isinstance(raw_mapping, list) or len(raw_mapping) > 50
                or ("acceptance_mapping" in plan and not raw_mapping)):
            raise CommandWorkerError("mapping AC approuvé invalide")
        acceptance_mapping = []
        criterion_ids: set[str] = set()
        mapped_children: set[str] = set()
        for index, raw_item in enumerate(raw_mapping):
            item = _exact(
                raw_item, {"criterion_id", "criterion_digest", "issue_id"},
                f"acceptance_mapping[{index}]",
            )
            criterion_id = item["criterion_id"]
            criterion_digest = item["criterion_digest"]
            issue_id = item["issue_id"]
            if (not isinstance(criterion_id, str) or _IDENTIFIER.fullmatch(criterion_id) is None
                    or not isinstance(criterion_digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", criterion_digest) is None
                    or not isinstance(issue_id, str) or _ISSUE_ID.fullmatch(issue_id) is None
                    or criterion_id in criterion_ids or issue_id in mapped_children):
                raise CommandWorkerError("mapping AC approuvé invalide")
            criterion_ids.add(criterion_id)
            mapped_children.add(issue_id)
            acceptance_mapping.append((criterion_id, criterion_digest, issue_id))
        if acceptance_mapping and mapped_children != seen:
            raise CommandWorkerError("mapping AC approuvé hors graphe")

        preview_expires = _integer(
            preview["expires_at"], "preview.expires_at", maximum=4_102_444_800_000,
        )
        preview_material = {
            "contract": "devhub-foundry-command.v1",
            "project": command.project,
            "actor": preview["actor"],
            "intent": {"type": "execute-epic"},
            "epic_id": command.epic_id,
            "epic_version": preview["epic_version"],
            "planning_version_id": command.planning_version_id,
            "snapshot_digest": command.snapshot_digest,
            "policy_digest": command.policy_digest,
            "expires_at": preview_expires,
            "limits": limits,
            "plan": plan,
        }
        if _digest(preview_material) != command.preview_digest:
            raise CommandWorkerError("digest preview d'autorité locale modifié")

        approval = _exact(
            document["approval"], {"id", "preview_id", "state", "expires_at"}, "approval",
        )
        if approval["id"] != command.approval_id or approval["preview_id"] != command.preview_id:
            raise CommandWorkerError("binding approval d'autorité locale modifié")
        if approval["state"] not in {"approved", "revoked", "expired"}:
            raise CommandWorkerError("état approval d'autorité locale invalide")
        approval_expires = _integer(
            approval["expires_at"], "approval.expires_at", maximum=4_102_444_800_000,
        )
        policy = _exact(
            document["policy"], {"implementer_minimum_tier", "source"}, "policy",
        )
        approved_floor = _tier(
            policy["implementer_minimum_tier"], "policy.floor", nullable=True,
        )
        if (not isinstance(policy["source"], str) or not policy["source"]
                or len(policy["source"]) > 80 or _digest(policy) != command.policy_digest):
            raise CommandWorkerError("digest policy d'autorité locale modifié")

        local_keys = {
            "observed_at", "valid_until", "minimum_tier", "max_cost_cents",
            "max_concurrency", "budget_remaining_cents", "active_concurrency",
            "host_available",
        }
        raw_local = document["local"]
        expected_local_keys = (
            local_keys if legacy_authority
            else local_keys | {"provider_invocation_ceiling_cents"}
        )
        if (not isinstance(raw_local, dict)
                or set(raw_local) != expected_local_keys):
            if (isinstance(raw_local, dict)
                    and set(raw_local) == local_keys):
                raise PreEffectCapacityError(
                    "plafond par invocation provider absent de l'autorité",
                )
            raise CommandWorkerError("autorité locale local invalide")
        local = raw_local
        invocation_ceiling = None
        if not legacy_authority:
            invocation_ceiling = local["provider_invocation_ceiling_cents"]
            if (type(invocation_ceiling) is not int
                    or not 1 <= invocation_ceiling <= 1_000_000):
                raise PreEffectCapacityError(
                    "plafond par invocation provider invalide dans l'autorité",
                )
        observed = _integer(local["observed_at"], "local.observed_at", maximum=4_102_444_800_000)
        valid_until = _integer(
            local["valid_until"], "local.valid_until", maximum=4_102_444_800_000,
        )
        now = self.now_ms()
        if not observed <= now < valid_until <= observed + MAX_OBSERVATION_AGE_MS:
            raise CommandWorkerError("observation d'autorité Foundry expirée")
        observation = RevalidationObservation(
            command_id=command.id, project=command.project,
            preview_id=command.preview_id, approval_id=command.approval_id,
            approval_state=approval["state"],
            epic_id=command.epic_id, epic_version=preview["epic_version"],
            planning_version_id=command.planning_version_id,
            preview_digest=command.preview_digest, snapshot_digest=command.snapshot_digest,
            policy_digest=command.policy_digest, preview_expires_at=preview_expires,
            approval_expires_at=approval_expires,
            approved_minimum_tier=approved_floor,
            current_minimum_tier=_tier(
                local["minimum_tier"], "local.minimum_tier", nullable=True,
            ),
            local_max_cost_cents=_integer(
                local["max_cost_cents"], "local.max_cost_cents",
                minimum=1, maximum=1_000_000,
            ),
            local_max_concurrency=_integer(
                local["max_concurrency"], "local.max_concurrency",
                minimum=1, maximum=16,
            ),
            budget_remaining_cents=_integer(
                local["budget_remaining_cents"], "local.budget_remaining_cents",
                minimum=0, maximum=1_000_000,
            ),
            active_concurrency=_integer(
                local["active_concurrency"], "local.active_concurrency", maximum=1_000_000,
            ),
            observed_at=observed,
            valid_until=valid_until,
            host_available=local["host_available"],
            required_gates=required_gates,
            provider_invocation_ceiling_cents=invocation_ceiling,
        )
        if type(observation.host_available) is not bool:
            raise CommandWorkerError("disponibilité hôte Foundry invalide")
        return RuntimeAuthority(
            observation, tuple(waves), tuple(plan["blockers"]), tuple(acceptance_mapping),
        )


class ClaudeCommandEffectProvider:
    """Concrete effect provider with a hard pre-effect Claude budget flag."""

    def __init__(
        self, source: FileAuthoritySource, *, root: str | os.PathLike,
        effect_directory: str | os.PathLike,
        runner: Callable[..., object] = subprocess.run,
    ):
        self.source = source
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("racine d'exécution Foundry absente")
        # Resolve every Claude host translation before preparing any effect state.
        self.routing = _load_claude_policy(self.root)
        self.effect_directory = Path(effect_directory).expanduser().resolve()
        self.effect_directory.mkdir(parents=True, exist_ok=True)
        self.effect_directory.chmod(0o700)
        self.runner = runner
        self.plugin_root = Path(__file__).resolve().parents[2]

    def observe(self, command: DevHubCommand) -> RevalidationObservation:
        return self.source.load(command).observation

    def _route_and_cost(self, command: DevHubCommand):
        authority = self.source.load(command)
        observation = authority.observation
        effective = revalidate_command(
            command, observation, now_ms=self.source.now_ms(),
        )
        floors = [item for item in (
            observation.approved_minimum_tier, observation.current_minimum_tier,
        ) if item is not None]
        floor = max(floors, key=LEVELS.index) if floors else None
        route = self.routing.resolve(
            "implementer", "claude", minimum_tier=floor,
            minimum_source="devhub-command" if floor else None,
        )
        return authority, route, effective

    def execution_profile(
        self, command: DevHubCommand, _receipt: EffectReceipt | None,
    ) -> ExecutionProfile:
        _authority, route, effective = self._route_and_cost(command)
        return ExecutionProfile(route.selected_tier, effective.max_cost_cents, 1)

    def _receipt_path(self, command_id: str) -> Path:
        if _IDENTIFIER.fullmatch(command_id) is None:
            raise CommandWorkerError("identité d'effet runtime invalide")
        return self.effect_directory / f"{command_id}.json"

    def resolve(self, command_id: str, binding_digest: str) -> EffectReceipt | None:
        path = self._receipt_path(command_id)
        if not path.exists():
            return None
        try:
            raw_bytes = path.read_bytes()
            if len(raw_bytes) > MAX_RECEIPT_BYTES:
                raise ValueError
            raw = _exact(
                json.loads(raw_bytes.decode("utf-8")),
                {"contract", "binding_digest", "effect"}, "runtime receipt",
            )
            effect = raw["effect"]
            legacy_keys = {
                "effect_id", "status", "proof_digest", "cost_cents", "duration_ms",
            }
            extended_keys = legacy_keys | {"attempt_id", "attempt_started_at"}
            if (not isinstance(effect, dict)
                    or frozenset(effect) not in {frozenset(legacy_keys), frozenset(extended_keys)}):
                raise ValueError
            if (raw["contract"] != RECEIPT_CONTRACT
                    or raw["binding_digest"] != binding_digest):
                raise ValueError
            return EffectReceipt(
                effect["effect_id"], effect["status"], effect["proof_digest"],
                effect["cost_cents"], effect["duration_ms"],
                effect.get("attempt_id"), effect.get("attempt_started_at"),
            )
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            raise CommandWorkerError("reçu runtime Foundry invalide") from None

    def _store(self, binding_digest: str, effect: EffectReceipt) -> None:
        path = self._receipt_path(effect.effect_id)
        payload = {
            "contract": RECEIPT_CONTRACT, "binding_digest": binding_digest,
            "effect": {
                "effect_id": effect.effect_id, "status": effect.status,
                "proof_digest": effect.proof_digest, "cost_cents": effect.cost_cents,
                "duration_ms": effect.duration_ms,
                "attempt_id": effect.attempt_id,
                "attempt_started_at": effect.attempt_started_at,
            },
        }
        encoded = _canonical(payload)
        if len(encoded) > MAX_RECEIPT_BYTES:
            raise CommandWorkerError("reçu runtime Foundry hors borne")
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{effect.effect_id}-", suffix=".tmp", dir=self.effect_directory,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _session_id(command_id: str, binding_digest: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"foundry:{command_id}:{binding_digest}"))

    @staticmethod
    def _prompt(command: DevHubCommand, authority: RuntimeAuthority, *, resume: bool) -> str:
        waves = "\n".join(
            f"Wave {index}: {', '.join(items)}"
            for index, items in enumerate(authority.waves, start=1)
        )
        action = "Resume" if resume else "Execute"
        return (
            "FOUNDRY_DEVHUB_COMMAND_V1\n"
            f"{action} the exact locally revalidated Foundry campaign.\n"
            f"Command: {command.id}\nProject: {command.project}\n"
            f"Epic: {command.epic_id}\nPlanning Version: {command.planning_version_id}\n"
            f"Preview: {command.preview_id} ({command.preview_digest})\n"
            f"Snapshot: {command.snapshot_digest}\nPolicy: {command.policy_digest}\n"
            f"Approved waves:\n{waves}\n"
            "Use the normal Foundry start/resume issue loop for only these issues and in "
            "this dependency order. Preserve tests, independent review and human-test "
            "gates. Do not infer merge, push, PR or tracker authority from this command."
        )

    def _invoke(
        self, command: DevHubCommand, authorization: ExecutionAuthorization, *,
        resume: bool,
        reconcile_capacity: Callable[[RevalidationObservation], None],
    ) -> EffectReceipt:
        authority, route, effective = self._route_and_cost(command)
        if (authorization.command_id != command.id
                or authorization.binding_digest != _binding_digest(command)
                or authorization.selected_tier != route.selected_tier
                or authorization.cost_ceiling_cents != effective.max_cost_cents):
            raise CommandWorkerError("autorisation runtime Foundry contradictoire")
        if (authorization.provider_invocation_ceiling_cents
                > effective.provider_invocation_ceiling_cents):
            raise PreEffectCapacityError(
                "plafond par invocation provider resserré avant effet",
            )
        reconcile_capacity(authority.observation)
        session_id = self._session_id(command.id, authorization.binding_digest)
        attempt_started_at = self.source.now_ms()
        argv = [
            "claude", "--print", "--output-format", "json",
            "--model", claude_invocation_model(
                route.model,
                project_models=getattr(self.routing, "claude_models", {}),
            ),
            "--effort", route.effort,
            "--max-budget-usd",
            f"{authorization.provider_invocation_ceiling_cents / 100:.2f}",
            "--permission-mode", "auto", "--plugin-dir", str(self.plugin_root),
            "--name", f"foundry-{command.id}",
        ]
        argv.extend(["--resume", session_id] if resume else ["--session-id", session_id])
        environment = _claude_child_environment()
        # This deterministic session identity is also the immutable receipt binding.
        # The child may only append passive structured receipts under that identity;
        # it receives no DevHub transport credential or publisher authority.
        environment[ATTEMPT_ID_ENV] = session_id
        environment[RECEIPT_DIRECTORY_ENV] = str(
            self.effect_directory.parent / "execution-receipts"
        )
        try:
            completed = self.runner(
                argv, cwd=self.root, input=self._prompt(command, authority, resume=resume),
                capture_output=True, text=True, timeout=MAX_EXECUTION_SECONDS,
                env=environment, check=False,
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError("runtime Foundry expiré") from None
        except OSError:
            raise
        stdout = getattr(completed, "stdout", None)
        returncode = getattr(completed, "returncode", None)
        if (not isinstance(stdout, str) or len(stdout.encode("utf-8")) > MAX_RUNTIME_OUTPUT_BYTES
                or type(returncode) is not int):
            raise OSError("résultat runtime Foundry ambigu")
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            raise OSError("résultat runtime Foundry ambigu") from None
        if not isinstance(result, dict) or result.get("session_id") != session_id:
            raise OSError("résultat runtime Foundry ambigu")
        total_cost = result.get("total_cost_usd")
        if not isinstance(total_cost, (int, float)) or isinstance(total_cost, bool):
            raise OSError("coût runtime Foundry ambigu")
        cost_cents = math.ceil(float(total_cost) * 100)
        if not 0 <= cost_cents <= authorization.provider_invocation_ceiling_cents:
            raise CommandWorkerError("runtime Foundry a dépassé son plafond")
        duration = result.get("duration_ms")
        if type(duration) is not int or not 0 <= duration <= 604_800_000:
            raise OSError("durée runtime Foundry ambiguë")
        status = (
            "completed"
            if returncode == 0 and result.get("is_error") is False
            else "failed"
        )
        effect = EffectReceipt(
            command.id, status, _digest({
                "session_id": session_id, "result": result,
                "returncode": returncode,
            }), cost_cents, duration, session_id, attempt_started_at,
        )
        self._store(authorization.binding_digest, effect)
        return effect

    def launch(
        self, command: DevHubCommand, authorization: ExecutionAuthorization, *,
        effect_id: str, heartbeat: Callable[[], None],
        reconcile_capacity: Callable[[RevalidationObservation], None],
    ) -> EffectReceipt:
        if effect_id != command.id:
            raise CommandWorkerError("identité de launch runtime contradictoire")
        return self._invoke(
            command, authorization, resume=False,
            reconcile_capacity=reconcile_capacity,
        )

    def resume(
        self, command: DevHubCommand, receipt: EffectReceipt,
        authorization: ExecutionAuthorization, *, heartbeat: Callable[[], None],
        reconcile_capacity: Callable[[RevalidationObservation], None],
    ) -> EffectReceipt:
        if receipt.effect_id != command.id or receipt.status in {"completed", "failed", "cancelled"}:
            raise CommandWorkerError("reçu runtime non reprenable")
        return self._invoke(
            command, authorization, resume=True,
            reconcile_capacity=reconcile_capacity,
        )


def _outcomes_json(outcomes: Sequence[WorkerOutcome]) -> str:
    return json.dumps([
        {"command_id": item.command_id, "status": item.status, "detail": item.detail}
        for item in outcomes
    ], sort_keys=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Worker sortant des commandes DevHub")
    parser.add_argument("project")
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--authority-dir", required=True)
    parser.add_argument("--state-dir")
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--max-commands", type=int, default=16)
    args = parser.parse_args(argv)
    host_state = Path(args.state_dir or registry.data_dir()) / "command-worker"
    state = host_state / args.project
    state.mkdir(parents=True, exist_ok=True)
    host_state.chmod(0o700)
    state.chmod(0o700)
    source = FileAuthoritySource(args.authority_dir)
    execution_receipts = ExecutionReceiptStore(state / "execution-receipts")
    provider = CampaignCommandEffectProvider(
        source, root=args.root, state_directory=state / "campaign-runtime",
        owner_id=args.worker_id,
        execution_receipts=execution_receipts,
    )
    command_client = DevHubCommandClient()
    worker = CommandWorker(
        command_client, ReceiptStore(state / "receipts.sqlite3"), provider,
        worker_id=args.worker_id, lease_seconds=args.lease_seconds,
        page_size=args.page_size, max_pages=args.max_pages,
        max_commands=args.max_commands,
        reservation_store=HostReservationStore(host_state / "host-reservations.sqlite3"),
        event_publisher=PassiveEventPublisher(
            state / "passive-events", CommandJournalEventTransport(command_client),
        ),
        execution_receipts=execution_receipts,
    )
    print(_outcomes_json(worker.run_once(args.project)))


if __name__ == "__main__":
    main()
