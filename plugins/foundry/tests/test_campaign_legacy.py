from __future__ import annotations

import gc
import stat
import sqlite3
import subprocess
import sys
import shutil
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from foundry.campaign_coordinator import (
    LEGACY_F89_BUDGET_SPENT,
    LEGACY_F89_CAMPAIGN_ID,
    LEGACY_F89_CHILDREN,
    CampaignCoordinator,
    CampaignObservation,
    CampaignRevalidationError,
    CampaignSpec,
    CampaignStore,
    EffectEnvelope,
    StepReceipt,
    _digest,
)
from foundry.campaign_legacy import (
    LegacyCampaignRequalificationError,
    _MAX_BTREE_CELLS_PER_PAGE,
    _ReadOnlySQLiteSnapshot,
    main as campaign_legacy_main,
    requalify_legacy_f89,
)
from foundry.campaign_terminal import (
    TerminalCampaignReconciliationError,
    _devhub_closure_digest,
    reconcile_legacy_f89_terminal,
)
from foundry.campaign_runtime import (
    CampaignCommandEffectProvider,
    CampaignEffectStore,
    FoundryPrimitiveRunner,
)
from foundry.command_worker import (
    EffectReceipt,
    ExecutionAuthorization,
    RevalidationObservation,
    _binding_digest,
)
from foundry.devhub_commands import (
    COMMAND_EVENT_CONTRACT,
    CommandEvent,
    CommandEventPage,
    DevHubCommand,
    TerminalReconciliation,
)
from foundry.models import EpicClosureChild, EpicClosureOutcome, EpicClosureReceipt, Project
from foundry.routing import acceptance_criteria


def _spec() -> CampaignSpec:
    return CampaignSpec(
        campaign_id=LEGACY_F89_CAMPAIGN_ID, command_id=LEGACY_F89_CAMPAIGN_ID,
        project="F89E2E", epic_id="F89E2E-9",
        preview_id="preview_438b0084-5d4f-4f60-b8ed-9a7ba9fcaab9",
        approval_id="approval_8ea5d09b-4d9a-4151-9063-624e2231a804",
        planning_version_id="F89E2E-VERSION-3",
        preview_digest="5c4be4c05e3ddc5a6ca301b223fdd2da5690e8bf7ba3284cd8e73a9e23ca6c2e",
        snapshot_digest="fc84d9df90542cc8753b521614c049f6948d06bdad8a8958a41e3dd571392567",
        policy_digest="f7cb84508884682b048e3158d36bb61f351c69ac1e9a0b7c51e8719e39c5108f",
        waves=(("F89E2E-10",), ("F89E2E-11", "F89E2E-12")), dependencies=(),
        max_concurrency=2, budget_cents=50_000, expires_at=1_788_602_439_410,
        minimum_tier="frontier",
    )


class LegacyTracker:
    name = "devhub"
    epic_closure_supported = True

    def __init__(self):
        self.project = Project("F89E2E", "project-f89")
        self.body = (
            "## Critères d acceptation\n"
            "- [x] enfant dix livre\n"
            "- [x] enfant onze livre\n"
            "- [x] enfant douze livre\n"
        )
        self.parent = SimpleNamespace(
            id="F89E2E-9", title="F89", type="Epic", state="done", version=6,
            body=self.body, ac_done=3, ac_total=3, pr_url=None,
            links=[
                SimpleNamespace(type="parent-of", target=issue_id)
                for issue_id in LEGACY_F89_CHILDREN
            ],
        )
        self.children = {
            issue_id: SimpleNamespace(id=issue_id, state="done", version=2)
            for issue_id in LEGACY_F89_CHILDREN
        }
        receipt = EpicClosureReceipt(
            self.project.key, self.project.id, self.parent.id, 5, "Epic", 3, 3,
            tuple(EpicClosureChild(issue_id, 2, "done") for issue_id in LEGACY_F89_CHILDREN),
            10_000, "nonce_legacy_f89_123456",
        )
        self.closure = EpicClosureOutcome(receipt, 6, "audit:f89:close")
        self.mutations = 0

    def get_issue(self, issue_id):
        if issue_id == self.parent.id:
            return self.parent
        return self.children[issue_id]

    def resolve_project(self, _repo):
        return self.project

    def get_epic_closure(self, project, parent_id):
        assert project == self.project and parent_id == self.parent.id
        return replace(self.closure, replayed=True)


def _legacy_campaign(tmp_path):
    spec = _spec()
    directory = tmp_path / LEGACY_F89_CAMPAIGN_ID
    store = CampaignStore(directory / "campaign.sqlite3")
    effects = CampaignEffectStore(directory / "primitive-receipts.sqlite3")
    store.initialize(spec, now_ms=1_000)
    attempts = {"F89E2E-10": 3, "F89E2E-11": 1, "F89E2E-12": 1}
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE campaigns SET state='suspended', reason='host_or_effect_failure', "
            "wave_index=2, budget_spent=? WHERE campaign_id=?",
            (LEGACY_F89_BUDGET_SPENT, spec.campaign_id),
        )
        for issue_id, attempt in attempts.items():
            connection.execute(
                "UPDATE campaign_issues SET attempt=?, state='merged', next_step='done' "
                "WHERE campaign_id=? AND issue_id=?",
                (attempt, spec.campaign_id, issue_id),
            )
    for issue_id, attempt in attempts.items():
        effect_id, work_id = CampaignCoordinator._identity(spec, issue_id, attempt, "merge")
        envelope = EffectEnvelope(
            spec.campaign_id, spec.command_id, spec.project, spec.epic_id,
            issue_id, attempt, "merge", effect_id, work_id, "legacy-lease", 20_000,
            spec.binding_digest, spec.snapshot_digest, spec.policy_digest, None, 0,
        )
        store.begin_effect(envelope, selected_tier=None, cost_ceiling=0)
        receipt = StepReceipt(effect_id, "completed", _digest([effect_id, "legacy-merge"]))
        store.finish_effect(envelope, status="completed", proof_digest=receipt.proof_digest)
        effects.record(envelope, receipt, now_ms=10_000)
    tracker = LegacyTracker()
    criteria = acceptance_criteria(tracker.body)
    mapping = (
        (criteria[0]["id"], criteria[0]["digest"], "F89E2E-10"),
        (criteria[1]["id"], criteria[1]["digest"], "F89E2E-11"),
        (criteria[2]["id"], criteria[2]["digest"], "F89E2E-12"),
    )
    return directory, store, effects, tracker, mapping


def _persistent_state(directory):
    return {
        path.name: (
            path.lstat().st_dev, path.lstat().st_ino, path.lstat().st_mode,
            path.lstat().st_size, path.lstat().st_mtime_ns,
        )
        for path in directory.iterdir()
    }


def _reject_before_sidecars_or_sqlite(
    directory, mapping, tracker, monkeypatch, *, match,
    allow_existing_wal=False,
):
    before = _persistent_state(directory)
    sqlite_attempts = 0
    original_connect = sqlite3.connect

    def observe_connect(*args, **kwargs):
        nonlocal sqlite_attempts
        sqlite_attempts += 1
        return original_connect(*args, **kwargs)

    def forbidden_sidecar(*_args, **_kwargs):
        pytest.fail("WAL/SHM opened or created before preflight rejection")

    monkeypatch.setattr(
        "foundry.campaign_legacy.sqlite3.connect", observe_connect,
    )
    if not allow_existing_wal:
        monkeypatch.setattr(
            "foundry.campaign_legacy._open_existing_sidecar", forbidden_sidecar,
        )
    monkeypatch.setattr(
        "foundry.campaign_legacy._open_or_create_sidecar", forbidden_sidecar,
    )

    with pytest.raises(LegacyCampaignRequalificationError, match=match):
        requalify_legacy_f89(
            directory, mapping, actor="operator-f102", tracker=tracker,
            now_ms=lambda: 11_000,
        )

    assert sqlite_attempts == 0
    assert _persistent_state(directory) == before


def test_f89_requalification_verifies_three_merges_and_preserves_spent_budget(tmp_path):
    directory, store, _effects, tracker, mapping = _legacy_campaign(tmp_path)

    record = requalify_legacy_f89(
        directory, mapping, actor="operator-f102", tracker=tracker,
        now_ms=lambda: 11_000,
    )

    assert record.budget_spent_cents == 39
    assert record.actor == "operator-f102"
    assert (record.from_version, record.acceptance_version, record.closed_version) == (4, 5, 6)
    assert len(record.criteria) == 3
    assert store.campaign(LEGACY_F89_CAMPAIGN_ID).budget_spent == 39
    assert store.legacy_parent_requalification(LEGACY_F89_CAMPAIGN_ID) == record
    assert tracker.mutations == 0


def test_f89_requalification_retry_reuses_audit_for_reordered_equivalent_mapping(
    tmp_path,
):
    directory, store, _effects, tracker, mapping = _legacy_campaign(tmp_path)
    first = requalify_legacy_f89(
        directory, mapping, actor="operator-f102", tracker=tracker,
        now_ms=lambda: 11_000,
    )
    store.set_state(
        LEGACY_F89_CAMPAIGN_ID, "completed", None, now_ms=11_500,
        wave_index=len(_spec().waves),
    )

    # Model a transport loss after the first transaction committed: the caller
    # retries after the worker reached its terminal state, with the same criterion
    # map in a different serialization order.
    recovered = requalify_legacy_f89(
        directory, tuple(reversed(mapping)), actor="operator-f102", tracker=tracker,
        now_ms=lambda: 12_000,
    )

    assert recovered == first
    assert recovered.recorded_at == 11_000
    assert recovered.audit_digest == first.audit_digest
    assert store.legacy_parent_requalification(LEGACY_F89_CAMPAIGN_ID) == first

    altered = (
        (*mapping[0][:2], mapping[1][2]),
        (*mapping[1][:2], mapping[0][2]),
        mapping[2],
    )
    with pytest.raises(
        LegacyCampaignRequalificationError,
        match="requalification legacy F89 modifiee",
    ):
        requalify_legacy_f89(
            directory, altered, actor="operator-f102", tracker=tracker,
            now_ms=lambda: 13_000,
        )


def test_f89_requalification_resumes_paused_host_failure_to_completed(
    tmp_path, monkeypatch,
):
    directory, store, effects, tracker, mapping = _legacy_campaign(tmp_path)
    spec = _spec()
    record = requalify_legacy_f89(
        directory, mapping, actor="operator-f102", tracker=tracker,
        now_ms=lambda: 11_000,
    )
    monkeypatch.setattr("foundry.campaign_runtime.foundry.tracker", lambda: tracker)
    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=tmp_path / "worktrees", receipts=effects,
        now_ms=lambda: 12_000,
    )

    class LegacyPipeline:
        def __init__(self):
            self.verifications = 0

        def observe(self, candidate):
            assert candidate == spec
            return CampaignObservation(
                campaign_id=spec.campaign_id, command_id=spec.command_id,
                project=spec.project, epic_id=spec.epic_id,
                preview_id=spec.preview_id, approval_id=spec.approval_id,
                approval_state="approved",
                planning_version_id=spec.planning_version_id,
                preview_digest=spec.preview_digest,
                snapshot_digest=spec.snapshot_digest,
                policy_digest=spec.policy_digest, expires_at=spec.expires_at,
                observed_at=11_999, valid_until=13_000,
                max_concurrency=spec.max_concurrency,
                budget_remaining_cents=spec.budget_cents,
                minimum_tier=spec.minimum_tier,
                provider_invocation_ceiling_cents=1_000,
                host_available=True,
                epic_version=record.closed_version,
            )

        def observe_blockers(self, candidate):
            assert candidate.blockers == ()
            return ()

        def verify_legacy_parent_requalification(self, candidate):
            self.verifications += 1
            runner.verify_legacy_parent_requalification(candidate)

    pipeline = LegacyPipeline()
    coordinator = CampaignCoordinator(
        store, pipeline, object(), owner_id="worker-f89-requalification",
        now_ms=lambda: 12_000,
    )
    before = store.campaign(spec.campaign_id)
    assert (before.state, before.reason) == ("suspended", "host_or_effect_failure")
    assert store.parent_snapshot_advancement(spec.campaign_id) is None
    assert store.effect(spec.campaign_id, spec.epic_id, 1, "close-epic") is None

    assert coordinator.revalidate_paused_projection(spec) is True
    resumed = store.campaign(spec.campaign_id)
    assert (resumed.state, resumed.reason) == ("running", "campaign_revalidated")

    outcome = coordinator.run(spec)

    assert outcome.state == "completed"
    assert outcome.budget_spent_cents == LEGACY_F89_BUDGET_SPENT
    assert pipeline.verifications == 2
    assert store.parent_snapshot_advancement(spec.campaign_id) is None
    assert store.effect(spec.campaign_id, spec.epic_id, 1, "close-epic") is None
    assert tracker.mutations == 0


def test_f89_provider_requalification_projects_running_then_completes_without_fast_path(
    tmp_path, monkeypatch,
):
    state = tmp_path / "state"
    directory, store, effects, tracker, mapping = _legacy_campaign(
        state / "campaigns",
    )
    spec = _spec()
    requalify_legacy_f89(
        directory, mapping, actor="operator-f102", tracker=tracker,
        now_ms=lambda: 11_000,
    )
    monkeypatch.setattr("foundry.campaign_runtime.foundry.tracker", lambda: tracker)
    command = DevHubCommand(
        id=spec.command_id, project=spec.project,
        preview_id=spec.preview_id, approval_id=spec.approval_id,
        preview_digest=spec.preview_digest, snapshot_digest=spec.snapshot_digest,
        policy_digest=spec.policy_digest, epic_id=spec.epic_id,
        planning_version_id=spec.planning_version_id,
        max_cost_cents=spec.budget_cents, max_concurrency=spec.max_concurrency,
        state="paused", projection_stale=False, next_event_sequence=1,
        scheduled_for=spec.scheduled_for, lease=None, version=1,
        created_at=1_000, updated_at=11_000,
    )
    observation = RevalidationObservation(
        command_id=command.id, project=command.project,
        preview_id=command.preview_id, approval_id=command.approval_id,
        approval_state="approved", epic_id=command.epic_id,
        epic_version=6, planning_version_id=command.planning_version_id,
        preview_digest=command.preview_digest,
        snapshot_digest=command.snapshot_digest,
        policy_digest=command.policy_digest,
        preview_expires_at=spec.expires_at,
        approval_expires_at=spec.expires_at,
        approved_minimum_tier="frontier", current_minimum_tier="frontier",
        local_max_cost_cents=spec.budget_cents,
        local_max_concurrency=spec.max_concurrency,
        budget_remaining_cents=spec.budget_cents, active_concurrency=0,
        observed_at=11_999, valid_until=13_000, host_available=True,
        required_gates=frozenset({"tests", "independent-review", "human-test"}),
        provider_invocation_ceiling_cents=1_000,
    )

    class Source:
        now_ms = staticmethod(lambda: 12_000)

        def load(self, candidate):
            assert candidate.id == command.id
            return SimpleNamespace(
                observation=observation, waves=spec.waves, blockers=(),
                acceptance_mapping=(),
            )

    class Routing:
        def resolve(self, *_args, **_kwargs):
            return SimpleNamespace(selected_tier="frontier")

    verifications = 0
    original_verify = FoundryPrimitiveRunner.verify_legacy_parent_requalification

    def verify(self, record):
        nonlocal verifications
        verifications += 1
        return original_verify(self, record)

    monkeypatch.setattr(
        FoundryPrimitiveRunner, "verify_legacy_parent_requalification", verify,
    )
    with patch("foundry.campaign_runtime.RoutingPolicy.load", return_value=Routing()):
        provider = CampaignCommandEffectProvider(
            Source(), root=tmp_path, state_directory=state,
            owner_id="worker-f89-provider",
        )
        profile = provider.execution_profile(command, None)
        authorization = ExecutionAuthorization(
            command.id, _binding_digest(command), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        paused = EffectReceipt(
            command.id, "paused", _digest([command.id, "paused"]),
            LEGACY_F89_BUDGET_SPENT, 1,
        )
        running = provider.resume(
            command, paused, authorization, heartbeat=lambda: command,
            reconcile_capacity=lambda _observation: None,
        )
        running_command = replace(command, state="running", updated_at=12_001)
        completed = provider.resume(
            running_command, running, authorization,
            heartbeat=lambda: running_command,
            reconcile_capacity=lambda _observation: None,
        )

    assert running.status == "running"
    assert completed.status == "completed"
    assert completed.cost_cents == LEGACY_F89_BUDGET_SPENT
    assert verifications == 2
    assert store.parent_snapshot_advancement(spec.campaign_id) is None
    assert effects.command_parent_snapshot_advancement(spec.campaign_id) is None
    assert store.effect(spec.campaign_id, spec.epic_id, 1, "close-epic") is None
    assert tracker.mutations == 0


@pytest.mark.parametrize(
    "tamper", ("merge-receipt", "merge-binding", "graph", "version"),
)
def test_f89_requalification_rejects_missing_or_third_party_evidence(tmp_path, tamper):
    directory, _store, effects, tracker, mapping = _legacy_campaign(tmp_path)
    if tamper == "merge-receipt":
        with sqlite3.connect(effects.path) as connection:
            connection.execute(
                "DELETE FROM primitive_receipts WHERE issue_id='F89E2E-11' AND step='merge'"
            )
    elif tamper == "merge-binding":
        with sqlite3.connect(effects.path) as connection:
            connection.execute(
                "UPDATE primitive_receipts SET binding_digest=? "
                "WHERE issue_id='F89E2E-11' AND step='merge'",
                ("f" * 64,),
            )
    elif tamper == "graph":
        tracker.parent.links.append(
            SimpleNamespace(type="parent-of", target="F89E2E-13"),
        )
    else:
        tracker.parent.version = 7

    with pytest.raises(LegacyCampaignRequalificationError):
        requalify_legacy_f89(
            directory, mapping, actor="operator-f102", tracker=tracker,
            now_ms=lambda: 11_000,
        )


@pytest.mark.parametrize("invalid_evidence", ("merge-receipt", "close-audit"))
def test_invalid_downstream_f89_evidence_is_rejected_before_locked_directory_or_sqlite(
    tmp_path, monkeypatch, invalid_evidence,
):
    directory, _store, effects, tracker, mapping = _legacy_campaign(tmp_path)
    if invalid_evidence == "merge-receipt":
        with sqlite3.connect(effects.path) as connection:
            connection.execute(
                "DELETE FROM primitive_receipts "
                "WHERE issue_id='F89E2E-11' AND step='merge'"
            )
        expected = "recu merge legacy F89 invalide"
    else:
        tracker.closure = replace(tracker.closure, closed_parent_version=7)
        expected = "transition de cloture legacy F89 invalide"

    before = _persistent_state(directory)
    sqlite_attempts = 0
    locked_attempts = 0

    def forbidden_sqlite(*_args, **_kwargs):
        nonlocal sqlite_attempts
        sqlite_attempts += 1
        pytest.fail("SQLite opened before downstream F89 evidence was rejected")

    def forbidden_locked_directory(*_args, **_kwargs):
        nonlocal locked_attempts
        locked_attempts += 1
        pytest.fail("campaign directory locked before F89 evidence was staged")

    monkeypatch.setattr(
        "foundry.campaign_legacy.sqlite3.connect", forbidden_sqlite,
    )
    monkeypatch.setattr(
        "foundry.campaign_legacy._validated_campaign_directory_locked",
        forbidden_locked_directory,
    )

    with pytest.raises(LegacyCampaignRequalificationError, match=expected):
        requalify_legacy_f89(
            directory, mapping, actor="operator-f102", tracker=tracker,
            now_ms=lambda: 11_000,
        )

    assert sqlite_attempts == locked_attempts == 0
    assert _persistent_state(directory) == before


def test_f89_requalification_rejects_campaign_directory_with_symlink_ancestor(tmp_path):
    real = tmp_path / "real"
    directory, _store, _effects, tracker, mapping = _legacy_campaign(real)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(LegacyCampaignRequalificationError, match="lie symboliquement"):
        requalify_legacy_f89(
            linked / directory.name, mapping, actor="operator-f102", tracker=tracker,
            now_ms=lambda: 11_000,
        )


def test_f89_requalification_rejects_symlink_database(tmp_path):
    directory, _store, _effects, tracker, mapping = _legacy_campaign(tmp_path)
    database = directory / "campaign.sqlite3"
    target = tmp_path / "campaign-target.sqlite3"
    database.replace(target)
    database.symlink_to(target)

    with pytest.raises(LegacyCampaignRequalificationError, match="lie symboliquement"):
        requalify_legacy_f89(
            directory, mapping, actor="operator-f102", tracker=tracker,
            now_ms=lambda: 11_000,
        )


@pytest.mark.parametrize("invalid_request", ("directory", "coordinates"))
def test_invalid_f89_request_is_rejected_before_sqlite_open_or_filesystem_effect(
    tmp_path, monkeypatch, invalid_request,
):
    directory, _store, _effects, tracker, mapping = _legacy_campaign(tmp_path)
    if invalid_request == "directory":
        invalid_directory = tmp_path / "not-the-f89-campaign"
        directory.rename(invalid_directory)
        directory = invalid_directory
    else:
        mapping = (*mapping[:-1], (*mapping[-1][:2], "F89E2E-13"))
    before = {
        path.name: (
            path.stat().st_dev, path.stat().st_ino, path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in directory.iterdir()
    }
    connection_attempts = 0
    original_connect = sqlite3.connect

    def observe_connect(*args, **kwargs):
        nonlocal connection_attempts
        connection_attempts += 1
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(
        "foundry.campaign_legacy.sqlite3.connect", observe_connect,
    )

    with pytest.raises(LegacyCampaignRequalificationError):
        requalify_legacy_f89(
            directory, mapping, actor="operator-f102", tracker=tracker,
            now_ms=lambda: 11_000,
        )

    after = {
        path.name: (
            path.stat().st_dev, path.stat().st_ino, path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in directory.iterdir()
    }
    assert connection_attempts == 0
    assert after == before


def test_invalid_stored_f89_binding_is_rejected_before_every_sqlite_effect(
    tmp_path, monkeypatch,
):
    directory, _store, _effects, tracker, mapping = _legacy_campaign(tmp_path)
    gc.collect()
    connection = sqlite3.connect(directory / "campaign.sqlite3")
    connection.execute(
        "UPDATE campaigns SET binding_digest = ? WHERE campaign_id = ?",
        ("f" * 64, LEGACY_F89_CAMPAIGN_ID),
    )
    connection.commit()

    paths = tuple(
        directory / f"{database}{suffix}"
        for database in ("campaign.sqlite3", "primitive-receipts.sqlite3")
        for suffix in ("", "-wal", "-shm")
    )

    def persistent_state():
        state = {}
        for path in paths:
            try:
                observed = path.stat()
            except FileNotFoundError:
                state[path.name] = None
            else:
                state[path.name] = (
                    observed.st_dev, observed.st_ino, observed.st_size,
                    observed.st_mtime_ns,
                )
        return state

    before = persistent_state()
    connection_attempts = 0
    original_connect = sqlite3.connect

    def observe_connect(*args, **kwargs):
        nonlocal connection_attempts
        connection_attempts += 1
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(
        "foundry.campaign_legacy.sqlite3.connect", observe_connect,
    )

    try:
        with pytest.raises(
            LegacyCampaignRequalificationError,
            match="binding campagne legacy F89 invalide",
        ):
            requalify_legacy_f89(
                directory, mapping, actor="operator-f102", tracker=tracker,
                now_ms=lambda: 11_000,
            )

        assert connection_attempts == 0
        assert persistent_state() == before
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("database", "header_offset"),
    (
        ("campaign.sqlite3", 18),
        ("campaign.sqlite3", 19),
        ("primitive-receipts.sqlite3", 18),
        ("primitive-receipts.sqlite3", 19),
    ),
)
def test_f89_preflight_rejects_non_wal_header_without_any_sidecar_or_sqlite_effect(
    tmp_path, monkeypatch, database, header_offset,
):
    directory, _store, _effects, tracker, mapping = _legacy_campaign(tmp_path)
    gc.collect()
    path = directory / database
    payload = bytearray(path.read_bytes())
    assert payload[18:20] == bytes((2, 2))
    payload[header_offset] = 1
    path.write_bytes(payload)

    _reject_before_sidecars_or_sqlite(
        directory, mapping, tracker, monkeypatch,
        match="mode WAL journal legacy F89 invalide",
    )


@pytest.mark.parametrize("database", ("campaign.sqlite3", "primitive-receipts.sqlite3"))
def test_f89_preflight_rejects_noncanonical_header_before_sidecar_effects(
    tmp_path, monkeypatch, database,
):
    directory, _store, _effects, tracker, mapping = _legacy_campaign(tmp_path)
    gc.collect()
    path = directory / database
    payload = bytearray(path.read_bytes())
    assert payload[72:92] == bytes(20)
    payload[72] = 1
    path.write_bytes(payload)

    _reject_before_sidecars_or_sqlite(
        directory, mapping, tracker, monkeypatch,
        match="entete journal legacy F89 non canonique",
    )


@pytest.mark.parametrize("database", ("campaign.sqlite3", "primitive-receipts.sqlite3"))
def test_f89_preflight_rejects_rollback_journal_before_wal_shm_or_sqlite(
    tmp_path, monkeypatch, database,
):
    directory, _store, _effects, tracker, mapping = _legacy_campaign(tmp_path)
    gc.collect()
    (directory / f"{database}-journal").write_bytes(b"rollback-journal")

    _reject_before_sidecars_or_sqlite(
        directory, mapping, tracker, monkeypatch,
        match="journal rollback legacy F89 present",
    )


@pytest.mark.parametrize("corruption", ("duplicate-pointer", "excessive-cell-count"))
def test_f89_binary_preflight_rejects_amplifying_btree_corruption_without_effects(
    tmp_path, monkeypatch, corruption,
):
    directory, _store, _effects, tracker, mapping = _legacy_campaign(tmp_path)
    gc.collect()
    database = directory / "campaign.sqlite3"
    payload = bytearray(database.read_bytes())
    page_size = int.from_bytes(payload[16:18], "big")
    page_size = 65_536 if page_size == 1 else page_size
    if corruption == "duplicate-pointer":
        pending = [1]
        target_header = None
        while pending:
            page_number = pending.pop()
            page_offset = (page_number - 1) * page_size
            header = page_offset + (100 if page_number == 1 else 0)
            page_type = payload[header]
            assert page_type in {0x05, 0x0d}
            header_size = 12 if page_type == 0x05 else 8
            cell_count = int.from_bytes(payload[header + 3:header + 5], "big")
            pointer_start = header + header_size
            if cell_count >= 2:
                target_header = header
                break
            if page_type == 0x05:
                pointers = (
                    int.from_bytes(payload[offset:offset + 2], "big")
                    for offset in range(pointer_start, pointer_start + 2 * cell_count, 2)
                )
                pending.extend(
                    int.from_bytes(payload[pointer:pointer + 4], "big")
                    for pointer in pointers
                )
                pending.append(int.from_bytes(payload[header + 8:header + 12], "big"))
        assert target_header is not None
        page_type = payload[target_header]
        pointer_start = target_header + (12 if page_type == 0x05 else 8)
        payload[pointer_start + 2:pointer_start + 4] = payload[
            pointer_start:pointer_start + 2
        ]
    else:
        payload[103:105] = (_MAX_BTREE_CELLS_PER_PAGE + 1).to_bytes(2, "big")
    database.write_bytes(payload)

    _reject_before_sidecars_or_sqlite(
        directory, mapping, tracker, monkeypatch,
        match="binding campagne legacy F89 invalide",
        allow_existing_wal=True,
    )


def test_f89_binary_reader_rejects_overlapping_cell_spans_before_records():
    database = bytearray(512)
    database[:16] = b"SQLite format 3\0"
    database[16:18] = (512).to_bytes(2, "big")
    database[18:20] = bytes((2, 2))
    database[21:24] = bytes((64, 32, 32))
    database[28:32] = (1).to_bytes(4, "big")
    database[44:48] = (4).to_bytes(4, "big")
    database[56:60] = (1).to_bytes(4, "big")
    database[100] = 0x0d
    database[103:105] = (2).to_bytes(2, "big")
    database[105:107] = (480).to_bytes(2, "big")
    database[108:110] = (480).to_bytes(2, "big")
    database[110:112] = (481).to_bytes(2, "big")
    database[480:484] = bytes((0, 1, 1, 0))
    snapshot = _ReadOnlySQLiteSnapshot(bytes(database), b"")

    with pytest.raises(ValueError, match="cellules SQLite chevauchantes"):
        snapshot.table_rows(1)


def test_f89_requalification_preserves_clean_wal_and_shm_sidecars(tmp_path):
    directory, _store, _effects, tracker, mapping = _legacy_campaign(tmp_path)
    campaign = sqlite3.connect(directory / "campaign.sqlite3")
    receipts = sqlite3.connect(directory / "primitive-receipts.sqlite3")
    try:
        campaign.execute("PRAGMA schema_version").fetchone()
        receipts.execute("PRAGMA schema_version").fetchone()
        sidecars = tuple(
            directory / f"{database}{suffix}"
            for database in ("campaign.sqlite3", "primitive-receipts.sqlite3")
            for suffix in ("-wal", "-shm")
        )
        identities = {
            path: (path.stat().st_dev, path.stat().st_ino)
            for path in sidecars
        }

        record = requalify_legacy_f89(
            directory, mapping, actor="operator-f102", tracker=tracker,
            now_ms=lambda: 11_000,
        )

        assert record.campaign_id == LEGACY_F89_CAMPAIGN_ID
        assert {
            path: (path.stat().st_dev, path.stat().st_ino)
            for path in sidecars
        } == identities
    finally:
        receipts.close()
        campaign.close()


@pytest.mark.parametrize(
    ("database", "suffix"),
    (
        ("campaign.sqlite3", "-wal"),
        ("campaign.sqlite3", "-shm"),
        ("primitive-receipts.sqlite3", "-wal"),
        ("primitive-receipts.sqlite3", "-shm"),
    ),
)
def test_f89_requalification_rejects_symlink_sidecar_before_sqlite_open(
    tmp_path, monkeypatch, database, suffix,
):
    directory, _store, _effects, tracker, mapping = _legacy_campaign(tmp_path)
    gc.collect()
    sidecar = directory / f"{database}{suffix}"
    target = tmp_path / f"malicious{suffix}"
    target.touch()
    sidecar.symlink_to(target)
    original_connect = sqlite3.connect
    connection_attempts = 0

    def observe_connect(*args, **kwargs):
        nonlocal connection_attempts
        connection_attempts += 1
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(
        "foundry.campaign_legacy.sqlite3.connect", observe_connect,
    )

    with pytest.raises(
        LegacyCampaignRequalificationError,
        match="journal auxiliaire legacy F89 lie symboliquement",
    ):
        requalify_legacy_f89(
            directory, mapping, actor="operator-f102", tracker=tracker,
            now_ms=lambda: 11_000,
        )
    assert connection_attempts == 0


@pytest.mark.parametrize(
    ("database", "suffix"),
    (
        ("campaign.sqlite3", "-wal"),
        ("campaign.sqlite3", "-shm"),
        ("primitive-receipts.sqlite3", "-wal"),
        ("primitive-receipts.sqlite3", "-shm"),
    ),
)
def test_f89_requalification_rejects_sidecar_substitution_on_reconnect(
    tmp_path, monkeypatch, database, suffix,
):
    directory, _store, _effects, tracker, mapping = _legacy_campaign(tmp_path)
    database_path = directory / database
    sidecar = directory / f"{database}{suffix}"
    original_connect = sqlite3.connect
    connection_attempts = 0
    swapped = False

    def swap_after_guard(path, *args, **kwargs):
        nonlocal connection_attempts, swapped
        if Path(path) == database_path:
            connection_attempts += 1
            if connection_attempts == 2:
                original_sidecar = tmp_path / f"original{suffix}"
                replacement = tmp_path / f"replacement{suffix}"
                shutil.copy2(sidecar, replacement)
                sidecar.replace(original_sidecar)
                replacement.replace(sidecar)
                swapped = True
                connection = original_connect(path, *args, **kwargs)
                if suffix == "-wal":
                    # Restore the lexical path after SQLite has retained the
                    # replacement.  The descriptor provenance check, rather than
                    # the path recheck alone, must still refuse the connection.
                    connection.execute("PRAGMA schema_version").fetchone()
                    sidecar.replace(tmp_path / "opened-replacement-wal")
                    original_sidecar.replace(sidecar)
                return connection
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(
        "foundry.campaign_coordinator.sqlite3.connect", swap_after_guard,
    )

    with pytest.raises(
        LegacyCampaignRequalificationError,
        match=(
            "identite connexion SQLite modifiee" if suffix == "-wal"
            else "identite journal auxiliaire legacy F89 modifiee"
        ),
    ):
        requalify_legacy_f89(
            directory, mapping, actor="operator-f102", tracker=tracker,
            now_ms=lambda: 11_000,
        )
    assert swapped
    assert connection_attempts == 2


def test_f89_requalification_revalidates_database_identity_during_sqlite_open(
    tmp_path, monkeypatch,
):
    directory, _store, _effects, tracker, mapping = _legacy_campaign(tmp_path)
    database = directory / "campaign.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    shutil.copy2(database, replacement)
    original_connect = sqlite3.connect
    swapped = False

    def swap_before_actual_open(path, *args, **kwargs):
        nonlocal swapped
        if not swapped and Path(path) == database:
            swapped = True
            original = tmp_path / "original.sqlite3"
            database.replace(original)
            replacement.replace(database)
            connection = original_connect(path, *args, **kwargs)
            database.replace(tmp_path / "opened-replacement.sqlite3")
            original.replace(database)
            return connection
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(
        "foundry.campaign_coordinator.sqlite3.connect", swap_before_actual_open,
    )

    with pytest.raises(LegacyCampaignRequalificationError, match="identite connexion"):
        requalify_legacy_f89(
            directory, mapping, actor="operator-f102", tracker=tracker,
            now_ms=lambda: 11_000,
        )
    assert swapped


def test_f89_guarded_store_does_not_chmod_post_connect_symlink_target(
    tmp_path, monkeypatch,
):
    directory, _store, _effects, tracker, mapping = _legacy_campaign(tmp_path)
    database = directory / "campaign.sqlite3"
    original_database = tmp_path / "original-campaign.sqlite3"
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    outside.chmod(0o640)
    outside_mode = stat.S_IMODE(outside.stat().st_mode)
    assert stat.S_IMODE(database.stat().st_mode) == 0o600

    original_connect = CampaignStore._connect
    swapped = False

    @contextmanager
    def swap_after_verified_connect(store):
        nonlocal swapped
        connection = original_connect(store)
        try:
            with connection:
                yield connection
        finally:
            connection.close()
        if not swapped and store.path == database:
            database.replace(original_database)
            database.symlink_to(outside)
            swapped = True

    monkeypatch.setattr(CampaignStore, "_connect", swap_after_verified_connect)
    try:
        with pytest.raises(
            LegacyCampaignRequalificationError,
            match="journal legacy F89 lie symboliquement",
        ):
            requalify_legacy_f89(
                directory, mapping, actor="operator-f102", tracker=tracker,
                now_ms=lambda: 11_000,
            )
        assert swapped
        assert stat.S_IMODE(outside.stat().st_mode) == outside_mode
    finally:
        if database.is_symlink():
            database.unlink()
        if original_database.exists():
            original_database.replace(database)
        outside.chmod(outside_mode)


def test_campaign_legacy_cli_rejects_symlink_mapping_before_tracker_access(tmp_path):
    target = tmp_path / "mapping.json"
    target.write_text("[]\n", encoding="utf-8")
    linked = tmp_path / "mapping-link.json"
    linked.symlink_to(target)

    with pytest.raises(SystemExit, match="lie symboliquement"):
        campaign_legacy_main([
            "--campaign-dir", str(tmp_path / LEGACY_F89_CAMPAIGN_ID),
            "--mapping", str(linked), "--actor", "operator-f102",
        ])


def test_campaign_legacy_cli_rejects_mapping_with_symlink_ancestor(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    mapping = real / "mapping.json"
    mapping.write_text("[]\n", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(SystemExit, match="lie symboliquement"):
        campaign_legacy_main([
            "--campaign-dir", str(tmp_path / LEGACY_F89_CAMPAIGN_ID),
            "--mapping", str(linked / mapping.name), "--actor", "operator-f102",
        ])


def test_f89_runtime_revalidation_reads_merge_receipts_with_the_exact_binding(
    tmp_path, monkeypatch,
):
    directory, _store, effects, tracker, mapping = _legacy_campaign(tmp_path)
    record = requalify_legacy_f89(
        directory, mapping, actor="operator-f102", tracker=tracker,
        now_ms=lambda: 11_000,
    )
    with sqlite3.connect(effects.path) as connection:
        connection.execute(
            "UPDATE primitive_receipts SET binding_digest = ? "
            "WHERE issue_id = 'F89E2E-11' AND step = 'merge'",
            ("f" * 64,),
        )
    monkeypatch.setattr("foundry.campaign_runtime.foundry.tracker", lambda: tracker)
    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=tmp_path / "worktrees", receipts=effects,
        now_ms=lambda: 12_000,
    )

    with pytest.raises(CampaignRevalidationError, match="legacy_merge_receipt_invalid"):
        runner.verify_legacy_parent_requalification(record)


def _terminal_command(**changes):
    spec = _spec()
    values = {
        "id": spec.command_id, "project": spec.project,
        "preview_id": spec.preview_id, "approval_id": spec.approval_id,
        "preview_digest": spec.preview_digest,
        "snapshot_digest": spec.snapshot_digest,
        "policy_digest": spec.policy_digest, "epic_id": spec.epic_id,
        "planning_version_id": spec.planning_version_id,
        "max_cost_cents": spec.budget_cents,
        "max_concurrency": spec.max_concurrency, "state": "completed",
        "projection_stale": False, "next_event_sequence": 3,
        "scheduled_for": spec.scheduled_for, "lease": None,
        "version": 9, "created_at": 1_000, "updated_at": 11_000,
    }
    values.update(changes)
    return DevHubCommand(**values)


def _terminal_closure():
    value = {
        "epic_id": "F89E2E-9", "state": "closed",
        "receipt_id": "audit:f89:close", "parent_version": 6,
        "children": [
            {"issue_id": issue_id, "version": 2, "state": "done"}
            for issue_id in LEGACY_F89_CHILDREN
        ],
    }
    return {**value, "closure_digest": _devhub_closure_digest(value)}


def _terminal_campaign(tmp_path):
    directory, store, effects, tracker, mapping = _legacy_campaign(tmp_path)
    record = requalify_legacy_f89(
        directory, mapping, actor="operator-f102", tracker=tracker,
        now_ms=lambda: 10_500,
    )
    return directory, store, effects, tracker, mapping, record


class TerminalClient:
    def __init__(self, *, command=None, closure=None):
        self.command = command or _terminal_command()
        self.closure = closure or _terminal_closure()
        self.calls = []

    def get_command(self, command_id):
        self.calls.append(("get", command_id))
        return self.command

    def list_events(self, command_id, *, cursor, limit):
        self.calls.append(("events", command_id, cursor, limit))
        event = CommandEvent(
            id="event-terminal-f89", command_id=command_id, sequence=2,
            type="completed", actor="foundry-worker", lease_id="lease-f89",
            occurred_at=10_000, received_at=10_001,
            cost_cents=None, duration_ms=None,
            schema_version=COMMAND_EVENT_CONTRACT,
            provenance={
                "command_id": command_id, "attempt_id": "attempt-f89",
                "issue_id": "F89E2E-9", "source": "foundry",
            },
            receipts={
                "pr": None, "review": None, "ci": None, "merge": None,
                "epic_closure": self.closure,
            },
        )
        return CommandEventPage((event,), None, False, False, 3)

    def reconcile_terminal(
        self, command, *, reconciliation_id, terminal_event_id, epic_version,
    ):
        self.calls.append((
            "reconcile", reconciliation_id, terminal_event_id, epic_version,
        ))
        return TerminalReconciliation(
            reconciliation_id, command.id, terminal_event_id, command.epic_id,
            4, epic_version, self.closure["closure_digest"],
            None, None, 0, 1, 12_000,
        )


def test_terminal_f89_projection_is_zero_effect_and_idempotent(tmp_path):
    directory, store, _effects, tracker, mapping, record = _terminal_campaign(tmp_path)
    client = TerminalClient()

    first = reconcile_legacy_f89_terminal(
        directory, mapping, actor="operator-f103",
        terminal_event_id="event-terminal-f89", tracker=tracker,
        client=client, now_ms=lambda: 11_000,
    )
    second = reconcile_legacy_f89_terminal(
        directory, mapping, actor="operator-f103",
        terminal_event_id="event-terminal-f89", tracker=tracker,
        client=client, now_ms=lambda: 11_001,
    )

    assert first == second
    reconciliations = [call for call in client.calls if call[0] == "reconcile"]
    assert len(reconciliations) == 2
    assert reconciliations[0][1] == reconciliations[1][1]
    assert first.provider_effects == 0
    assert first.cost_cents is first.duration_ms is None
    assert {call[0] for call in client.calls} == {"get", "events", "reconcile"}
    assert store.legacy_parent_requalification(LEGACY_F89_CAMPAIGN_ID) == record
    assert record.actor == "operator-f102"


def test_terminal_f89_without_original_closure_receipt_stays_incomplete(tmp_path):
    directory, store, _effects, tracker, mapping, record = _terminal_campaign(tmp_path)
    tracker.get_epic_closure = lambda _project, _parent_id: None
    client = TerminalClient()

    with pytest.raises(
        TerminalCampaignReconciliationError,
        match="recu atomique de cloture legacy F89 invalide",
    ):
        reconcile_legacy_f89_terminal(
            directory, mapping, actor="operator-f103",
            terminal_event_id="event-terminal-f89", tracker=tracker,
            client=client, now_ms=lambda: 11_000,
        )

    assert client.calls == []
    assert tracker.mutations == 0
    assert store.legacy_parent_requalification(LEGACY_F89_CAMPAIGN_ID) == record


def test_terminal_projection_refuses_stale_ordinary_command_without_mutation(tmp_path):
    directory, _store, _effects, tracker, mapping, _record = _terminal_campaign(tmp_path)
    client = TerminalClient(command=_terminal_command(projection_stale=True))

    with pytest.raises(
        TerminalCampaignReconciliationError,
        match="non terminale ou projection stale",
    ):
        reconcile_legacy_f89_terminal(
            directory, mapping, actor="operator-f103",
            terminal_event_id="event-terminal-f89", tracker=tracker,
            client=client, now_ms=lambda: 11_000,
        )

    assert not any(call[0] == "reconcile" for call in client.calls)


def test_terminal_projection_refuses_missing_merge_receipt_before_http(tmp_path):
    directory, _store, effects, tracker, mapping, _record = _terminal_campaign(tmp_path)
    with sqlite3.connect(effects.path) as connection:
        connection.execute(
            "DELETE FROM primitive_receipts WHERE issue_id = 'F89E2E-11' AND step = 'merge'"
        )
    client = TerminalClient()

    with pytest.raises(TerminalCampaignReconciliationError):
        reconcile_legacy_f89_terminal(
            directory, mapping, actor="operator-f103",
            terminal_event_id="event-terminal-f89", tracker=tracker,
            client=client, now_ms=lambda: 11_000,
        )

    assert client.calls == []


@pytest.mark.parametrize(
    "mutation", ("incomplete-closure", "digest-conflict", "non-string-receipt-id"),
)
def test_terminal_projection_refuses_incomplete_or_conflicting_event_receipt(
    tmp_path, mutation,
):
    directory, _store, _effects, tracker, mapping, _record = _terminal_campaign(tmp_path)
    closure = _terminal_closure()
    if mutation == "incomplete-closure":
        closure.pop("children")
    elif mutation == "digest-conflict":
        closure["closure_digest"] = "f" * 64
    else:
        closure["receipt_id"] = 1
    client = TerminalClient(closure=closure)

    with pytest.raises(
        TerminalCampaignReconciliationError,
        match="attestation terminale DevHub (invalide|contradictoire)",
    ):
        reconcile_legacy_f89_terminal(
            directory, mapping, actor="operator-f103",
            terminal_event_id="event-terminal-f89", tracker=tracker,
            client=client, now_ms=lambda: 11_000,
        )

    assert not any(call[0] == "reconcile" for call in client.calls)


@pytest.mark.parametrize("mutation", ("parent-body", "graph", "child-version"))
def test_terminal_projection_refuses_altered_current_parent_or_graph(
    tmp_path, mutation,
):
    directory, _store, _effects, tracker, mapping, _record = _terminal_campaign(tmp_path)
    if mutation == "parent-body":
        tracker.parent.body += "\naltéré"
    elif mutation == "graph":
        tracker.parent.links.append(
            SimpleNamespace(type="parent-of", target="F89E2E-99")
        )
    else:
        tracker.children["F89E2E-11"].version = 3
    client = TerminalClient()

    with pytest.raises(TerminalCampaignReconciliationError):
        reconcile_legacy_f89_terminal(
            directory, mapping, actor="operator-f103",
            terminal_event_id="event-terminal-f89", tracker=tracker,
            client=client, now_ms=lambda: 11_000,
        )

    assert not any(call[0] == "reconcile" for call in client.calls)


def test_terminal_projection_rejects_path_substitution_without_local_write(
    tmp_path, monkeypatch,
):
    directory, _store, _effects, tracker, mapping, _record = _terminal_campaign(tmp_path)
    client = TerminalClient()
    original_get = client.get_command
    displaced = directory / "campaign.sqlite3.proven"
    substituted_target = tmp_path / "substituted.sqlite3"

    def substitute_after_snapshot(command_id):
        (directory / "campaign.sqlite3").rename(displaced)
        (directory / "campaign.sqlite3").symlink_to(substituted_target)
        return original_get(command_id)

    client.get_command = substitute_after_snapshot

    def forbidden_connect(*_args, **_kwargs):
        pytest.fail("terminal verification must not initialize SQLite")

    monkeypatch.setattr("foundry.campaign_legacy.sqlite3.connect", forbidden_connect)

    with pytest.raises(TerminalCampaignReconciliationError):
        reconcile_legacy_f89_terminal(
            directory, mapping, actor="operator-f102",
            terminal_event_id="event-terminal-f89", tracker=tracker,
            client=client, now_ms=lambda: 11_000,
        )

    assert not substituted_target.exists()
    assert not any(call[0] == "reconcile" for call in client.calls)


def test_foundry_cli_exposes_campaign_legacy_launcher():
    launcher = Path(__file__).parents[1] / "tooling" / "foundry_cli.py"

    completed = subprocess.run(
        [sys.executable, str(launcher), "campaign-legacy", "--help"],
        check=False, capture_output=True, text=True,
    )

    assert completed.returncode == 0
    assert "--campaign-dir" in completed.stdout


def test_foundry_cli_exposes_campaign_terminal_launcher():
    launcher = Path(__file__).parents[1] / "tooling" / "foundry_cli.py"

    completed = subprocess.run(
        [sys.executable, str(launcher), "campaign-terminal", "--help"],
        check=False, capture_output=True, text=True,
    )

    assert completed.returncode == 0
    assert "--terminal-event-id" in completed.stdout
