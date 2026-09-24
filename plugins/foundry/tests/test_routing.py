import hashlib
import json
import multiprocessing
from threading import Event, Thread
from pathlib import Path

import pytest

from foundry.escalation import EscalationStore, EscalationTechnicalBlockedError
from foundry.routing import (
    DEFAULT_MAPPINGS,
    GATE_EFFORT_FLOORS,
    GATE_FLOORS,
    LEVELS,
    ROLE_DEFAULTS,
    ReviewDeduplicator,
    AcceptanceProofStore,
    acceptance_digest,
    acceptance_criteria,
    RoutingConfigError,
    RoutingPolicy,
    RoutingUnavailableError,
    UserRouteRequest,
    claimed_review_diff,
    git_diff,
    host_override_warnings,
    main,
    review_diff_hash,
    synchronize_acceptance_body,
)
from foundry.routing_facades import detect_claude_overrides, detect_codex_overrides


BASE_SHA = "1" * 40
CLAIM_ATTEMPT_TOKEN = "a" * 64
OTHER_CLAIM_ATTEMPT_TOKEN = "b" * 64


def _review_coordinates(root: Path) -> dict[str, str]:
    return {"root": str(root.resolve()), "base": BASE_SHA}


@pytest.fixture(autouse=True)
def _synthetic_git_head(monkeypatch):
    """Keep Git-native ledger unit fixtures independent of a real worktree."""
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "a" * 40)


def _write_config(root: Path, payload: dict) -> Path:
    path = root / ".foundry" / "model-routing.json"
    path.parent.mkdir()
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_resolve_cli_rejects_an_untranslatable_claude_model(tmp_path, capsys):
    _write_config(tmp_path, {
        "mappings": {"claude": {"balanced": {"model": "untranslated-model"}}},
    })

    with pytest.raises(SystemExit, match="untranslated-model.*traduction hôte"):
        main([
            "resolve", "implementer", "--host", "claude", "--root", str(tmp_path),
        ])
    assert capsys.readouterr().out == ""


def _claim_once(args):
    state_dir, repository, diff = args
    return ReviewDeduplicator(repository, state_dir).claim(diff)


def _claim_git_once(args):
    state_dir, repository, diff, coordinates = args
    import foundry.routing as routing
    routing.git_diff = lambda *_args, **_kwargs: diff
    routing.git_head = lambda *_args, **_kwargs: "a" * 40
    try:
        return ReviewDeduplicator(repository, state_dir).claim_git(
            review_diff_hash(diff), coordinates=coordinates,
            claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
        ).to_dict()
    except RoutingConfigError as exc:
        return {"error": str(exc)}


def _replay_interrupted_claim_once(args):
    state_dir, repository, diff, coordinates, claim_attempt_token = args
    import foundry.routing as routing
    routing.git_diff = lambda *_args, **_kwargs: diff
    routing.git_head = lambda *_args, **_kwargs: "9" * 40
    try:
        return ReviewDeduplicator(repository, state_dir).claim_git(
            review_diff_hash(diff),
            coordinates=coordinates,
            claim_attempt_token=claim_attempt_token,
            replay_interrupted=True,
        ).to_dict()
    except RoutingConfigError as exc:
        return {"error": str(exc)}


def _technical_route_state(tmp_path, role, *, consume):
    store = EscalationStore.for_root(tmp_path)
    issue = "FOUNDRY-108"
    for tier in (
        "economy", "economy", "balanced", "balanced", "frontier", "frontier",
    ):
        store.record_failure(issue, role, "test_red", tier)
    generation = store.status(issue)["halt_generation"]
    store.resume_technical_remediation(issue, generation, "d" * 64)
    if consume:
        store.claim_technical_remediation_route(
            issue, role, generation, f"f108-{role}-route-0001",
        )
    return store, issue, generation


def _recover_once(args):
    state_dir, repository, diff_hash, generation, claim_id, reason, diff = args
    try:
        import foundry.routing as routing
        routing.git_diff = lambda *_args, **_kwargs: diff
        result = ReviewDeduplicator(repository, state_dir).recover(
            diff_hash, generation, claim_id, reason,
            coordinates=_review_coordinates(Path(state_dir)),
        )
        return result.to_dict()
    except RoutingConfigError as exc:
        return {"error": str(exc)}


def test_semantic_levels_roles_and_host_mappings_are_the_adr_contract(tmp_path):
    assert LEVELS == ("economy", "balanced", "frontier", "apex")
    assert ROLE_DEFAULTS == {
        "scout": "economy",
        "implementer": "balanced",
        "coordinator": "balanced",
        "reviewer": "frontier",
        "architect": "apex",
    }
    assert GATE_FLOORS == {"reviewer": "frontier", "architect": "apex"}
    assert GATE_EFFORT_FLOORS == {"reviewer": "high", "architect": "high"}
    assert {
        host: {tier: (target.model, target.effort) for tier, target in mapping.items()}
        for host, mapping in DEFAULT_MAPPINGS.items()
    } == {
        "claude": {
            "economy": ("haiku-4.5", "low"),
            "balanced": ("sonnet-5", "medium"),
            "frontier": ("opus-5", "high"),
            "apex": ("fable-5", "high"),
        },
        "codex": {
            "economy": ("gpt-5.6-luna", "low"),
            "balanced": ("gpt-5.6-terra", "medium"),
            "frontier": ("gpt-5.6-sol", "high"),
            "apex": ("gpt-5.6-sol", "max"),
        },
    }


def test_project_can_override_role_and_partial_mapping(tmp_path):
    _write_config(tmp_path, {
        "version": 1,
        "roles": {"implementer": "frontier"},
        "mappings": {"codex": {"frontier": {"model": "project-sol"}}},
    })

    route = RoutingPolicy.load(tmp_path).resolve("implementer", "codex")

    assert (route.requested_tier, route.model, route.effort) == (
        "frontier", "project-sol", "high",
    )
    assert route.sources == {"tier": "project", "model": "project", "effort": "default"}


def test_user_request_wins_over_project_then_defaults_fill_the_rest(tmp_path):
    _write_config(tmp_path, {
        "roles": {"implementer": "frontier"},
        "mappings": {"codex": {"apex": {"model": "project-apex", "effort": "high"}}},
    })

    route = RoutingPolicy.load(tmp_path).resolve(
        "implementer",
        "codex",
        user=UserRouteRequest(tier="apex", model="user-model", effort="max"),
    )

    assert (route.requested_tier, route.selected_tier) == ("apex", "apex")
    assert (route.model, route.effort) == ("user-model", "max")
    assert route.sources == {"tier": "user", "model": "user", "effort": "user"}


def test_invalid_config_reports_the_exact_actionable_path(tmp_path):
    path = _write_config(tmp_path, {"roles": {"reviewer": "balanced"}})

    with pytest.raises(RoutingConfigError, match=(
        rf"{path}\.roles\.reviewer: le rôle gate 'reviewer' ne peut pas "
        r"descendre sous 'frontier'"
    )):
        RoutingPolicy.load(tmp_path)


def test_invalid_json_reports_line_and_column(tmp_path):
    path = tmp_path / ".foundry" / "model-routing.json"
    path.parent.mkdir()
    path.write_text('{"roles":', encoding="utf-8")

    with pytest.raises(RoutingConfigError, match=r"JSON invalide à la ligne 1, colonne 10"):
        RoutingPolicy.load(tmp_path)


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_schema_version_requires_the_integer_one(tmp_path, version):
    path = _write_config(tmp_path, {"version": version})

    with pytest.raises(RoutingConfigError, match=rf"{path}\.version: l'entier 1 est requis"):
        RoutingPolicy.load(tmp_path)


def test_non_gate_fallback_goes_down_and_is_visible(tmp_path):
    policy = RoutingPolicy.load(tmp_path)

    route = policy.resolve(
        "implementer", "codex", available_models={"gpt-5.6-luna"},
    )

    assert (route.requested_tier, route.selected_tier) == ("balanced", "economy")
    assert route.fallback_direction == "down"
    assert route.fallback_candidates == ("balanced", "economy")
    assert route.fallback_path == ("balanced", "economy")
    assert route.warnings[0].code == "MODEL_FALLBACK_DOWN"


def test_explicit_user_effort_survives_model_fallback(tmp_path):
    route = RoutingPolicy.load(tmp_path).resolve(
        "implementer",
        "codex",
        user=UserRouteRequest(effort="max"),
        available_models={"gpt-5.6-luna"},
    )

    assert (route.selected_tier, route.model, route.effort) == (
        "economy", "gpt-5.6-luna", "max",
    )
    assert route.sources == {"tier": "fallback", "model": "default", "effort": "user"}


def test_gate_fallback_goes_up_and_never_down(tmp_path):
    policy = RoutingPolicy.load(tmp_path)

    route = policy.resolve("reviewer", "claude", available_models={"fable-5", "haiku-4.5"})

    assert (route.requested_tier, route.selected_tier) == ("frontier", "apex")
    assert route.fallback_direction == "up"
    assert route.fallback_candidates == ("frontier", "apex")
    assert route.fallback_path == ("frontier", "apex")
    assert route.gate_floor == "frontier"
    assert route.model == "fable-5"
    assert route.warnings[0].code == "GATE_MODEL_FALLBACK_UP"


def test_gate_fails_loudly_when_no_model_at_or_above_floor(tmp_path):
    policy = RoutingPolicy.load(tmp_path)

    with pytest.raises(RoutingUnavailableError, match=(
        r"gate 'reviewer'.*Niveaux supérieurs essayés : frontier, apex"
    )):
        policy.resolve("reviewer", "claude", available_models={"haiku-4.5", "sonnet-5"})


def test_architect_cannot_be_downgraded_by_an_explicit_user_request(tmp_path):
    policy = RoutingPolicy.load(tmp_path)

    with pytest.raises(RoutingConfigError, match=r"architect.*sous 'apex'"):
        policy.resolve("architect", "codex", user=UserRouteRequest(tier="frontier"))


@pytest.mark.parametrize("role", ["reviewer", "architect"])
def test_gate_rejects_an_unclassifiable_direct_model_override(tmp_path, role):
    with pytest.raises(RoutingConfigError, match=r"modèle direct.*plancher d'un gate"):
        RoutingPolicy.load(tmp_path).resolve(
            role, "claude", user=UserRouteRequest(model="haiku-4.5"),
        )


def test_direct_model_and_declared_scope_family_require_public_identifiers(tmp_path):
    with pytest.raises(RoutingConfigError, match="identifiant public de modèle"):
        RoutingPolicy.load(tmp_path).resolve(
            "implementer", "codex", user=UserRouteRequest(model="gpt-6-"),
        )

    _write_config(tmp_path, {
        "effort_scopes": {"codex": {"gpt-6-": {
            "version": 1, "levels": ["low", "medium"], "inadmissible": {},
        }}},
    })
    with pytest.raises(RoutingConfigError, match="gpt-6-.*family.*identifiant public"):
        RoutingPolicy.load(tmp_path)


@pytest.mark.parametrize("role", ["reviewer", "architect"])
def test_gate_rejects_effort_below_high_even_from_project_mapping(tmp_path, role):
    tier = ROLE_DEFAULTS[role]
    _write_config(tmp_path, {
        "mappings": {"claude": {tier: {"effort": "low"}}},
    })

    with pytest.raises(RoutingConfigError, match=rf"gate '{role}'.*effort 'high'"):
        RoutingPolicy.load(tmp_path).resolve(role, "claude")


def test_gate_allows_user_effort_at_or_above_its_floor(tmp_path):
    route = RoutingPolicy.load(tmp_path).resolve(
        "reviewer", "claude", user=UserRouteRequest(effort="max"),
    )

    assert route.effort == "max"
    assert route.gate_effort_floor == "high"


def test_host_override_signal_is_value_free_and_decided_once():
    warnings = host_override_warnings(
        "claude", ["CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"],
    )

    assert len(warnings) == 1
    assert warnings[0].code == "HOST_OVERRIDE_NEUTRALIZES_POLICY"
    assert "CLAUDE_CODE_SUBAGENT_MODEL" in warnings[0].message
    assert "masquée" in warnings[0].message


def test_each_host_facade_detects_overrides_without_returning_values():
    claude = detect_claude_overrides({
        "CLAUDE_CODE_SUBAGENT_MODEL": "secret-model-value",
        "CLAUDE_CODE_EFFORT_LEVEL": "secret-effort-value",
    })
    codex = detect_codex_overrides({"model": "secret-model-value",
                                    "model_reasoning_effort": "high", "other": True})

    assert claude == ("CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_EFFORT_LEVEL")
    assert codex == ("profile.model", "profile.model_reasoning_effort")
    assert "secret-model-value" not in repr((claude, codex))
    assert "secret-effort-value" not in repr((claude, codex))


def test_review_hash_uses_exact_diff_bytes_and_claim_is_cross_instance(tmp_path):
    diff = b"diff --git a/a b/a\n+new\n"
    first = ReviewDeduplicator("owner/repo", tmp_path)
    second = ReviewDeduplicator("owner/repo", tmp_path)

    key, should_run = first.claim(diff)
    second_key, should_run_again = second.claim(diff)

    assert key == review_diff_hash(diff)
    assert second_key == key
    assert should_run is True
    assert should_run_again is False
    assert ReviewDeduplicator("other/repo", tmp_path).claim(diff)[1] is True


def test_review_claim_is_atomic_between_host_processes(tmp_path):
    args = (str(tmp_path), "owner/repo", "same diff")
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        results = pool.map(_claim_once, [args, args])

    assert {key for key, _ in results} == {review_diff_hash("same diff")}
    assert sorted(should_run for _, should_run in results) == [False, True]


def test_concurrent_coordinate_mismatch_is_refused_before_reviewer_binding(
    monkeypatch, tmp_path,
):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    store, issue, generation = _technical_route_state(
        tmp_path, "reviewer", consume=False,
    )
    store.claim_technical_remediation_route(
        issue, "reviewer", generation, "f110-reviewer-route-0001",
    )
    repository = "owner/f110-coordinate-race"
    ledger = ReviewDeduplicator(repository, state_dir)
    diff = b"one exact Git review"
    diff_hash = review_diff_hash(diff)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    coordinates = (
        {"root": str((tmp_path / "first").resolve()), "base": BASE_SHA},
        {"root": str((tmp_path / "second").resolve()), "base": BASE_SHA},
    )
    results = []

    def bind(candidate):
        try:
            claim = store.claim_fresh_reviewer_authorization(
                issue,
                diff_hash,
                    validated_claim=lambda: ledger.claim_git(
                        diff_hash, coordinates=candidate,
                        claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
                    ),
            )
            results.append(("claimed", claim, candidate))
        except RoutingConfigError as exc:
            results.append(("refused", str(exc), candidate))

    threads = [Thread(target=bind, args=(candidate,)) for candidate in coordinates]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert sorted(result[0] for result in results) == ["claimed", "refused"]
    assert "coordonnées" in next(result[1] for result in results if result[0] == "refused")
    owner = next(result for result in results if result[0] == "claimed")
    event = store.status(issue)["technical_remediation_audit"][0]
    assert event["review_diff_hash"] == diff_hash
    assert event["review_claimed_at"] is not None
    ledger.assert_coordinates(diff_hash, coordinates=owner[2])
    assert [path.name for path in ledger.directory.iterdir() if path.name != ".ledger.lock"] == [
        diff_hash,
    ]


def test_git_claim_read_recover_and_proof_share_exact_coordinates(monkeypatch, tmp_path):
    repository, state = "owner/f110-full-flow", tmp_path / "state"
    coordinates = _review_coordinates(tmp_path)
    diff = b"exact Git-verifiable review bytes"
    diff_hash = review_diff_hash(diff)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "9" * 40)
    ledger = ReviewDeduplicator(repository, state)

    claim = ledger.claim_git(
        diff_hash, coordinates=coordinates,
        claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
    )
    assert claimed_review_diff(
        diff_hash, repository, claim_id=claim.claim_id,
        root=tmp_path, base=BASE_SHA, state_dir=state, consume=bytes,
    ) == diff
    recovered = ledger.recover(
        diff_hash, claim.generation, claim.claim_id, "reviewer interrupted",
        coordinates=coordinates,
    )
    body = "- [ ] exact Git review remains provable\n"
    outcomes = [{**item, "verdict": "pass"} for item in acceptance_criteria(body)]
    proof = AcceptanceProofStore(repository, state).create(
        issue_id="FOUNDRY-110", issue_body=body, reviewer_role="reviewer",
        outcomes=outcomes, quality="mergeable", diff_hash=diff_hash,
        claim_id=recovered.claim_id, root=tmp_path, base=BASE_SHA, state_dir=state,
    )

    assert proof["diff_hash"] == diff_hash
    assert ledger.completed_proof_id(diff_hash) == proof["proof_id"]


def test_terminal_proof_rearms_one_technical_review_for_a_new_diff(monkeypatch, tmp_path):
    state = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    store, issue, generation = _technical_route_state(tmp_path, "reviewer", consume=False)
    store.claim_technical_remediation_route(
        issue, "reviewer", generation, "f130-reviewer-route-0001",
    )
    repository = "owner/f130-terminal-rearm"
    ledger = ReviewDeduplicator(repository, state)
    coordinates = _review_coordinates(tmp_path)
    first, second = b"review completed before CI compatibility fix", b"CI compatibility fix"
    first_hash, second_hash = review_diff_hash(first), review_diff_hash(second)
    current = first
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: current)
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "a" * 40)

    first_claim = store.claim_fresh_reviewer_authorization(
        issue,
        first_hash,
        validated_claim=lambda: ledger.claim_git(
            first_hash, coordinates=coordinates, claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
        ),
    )
    body = "- [ ] CI compatibility correction is reviewed\n"
    outcomes = [{**item, "verdict": "pass"} for item in acceptance_criteria(body)]
    proof = AcceptanceProofStore(repository, state).create(
        issue_id=issue, issue_body=body, reviewer_role="reviewer", outcomes=outcomes,
        quality="mergeable", diff_hash=first_hash,
        claim_id=first_claim.claim_id,
        root=tmp_path, base=BASE_SHA, state_dir=state,
    )
    current = second

    def validate_claim():
        return ledger.claim_git(
            second_hash, coordinates=coordinates, claim_attempt_token=OTHER_CLAIM_ATTEMPT_TOKEN,
        )

    def validate_rearm(previous_diff_hash):
        binding = ledger.validated_completed_proof_binding(issue, previous_diff_hash)
        assert binding is not None
        return {
            "proof_id": binding["proof_id"],
            "completed_at": binding["completed_at"],
            "quality": binding["quality"],
            "all_pass": binding["all_pass"],
        }

    claim = store.claim_fresh_reviewer_authorization(
        issue, second_hash, validated_claim=validate_claim, validated_rearm=validate_rearm,
    )
    assert claim.diff_hash == second_hash
    audit = store.status(issue)["technical_remediation_audit"][0]["review_rearm_audit"]
    assert audit[0]["previous_diff_hash"] == first_hash
    assert audit[0]["terminal_proof_id"] == proof["proof_id"]


def test_terminal_review_rearm_refuses_a_proof_for_a_different_issue(monkeypatch, tmp_path):
    state = tmp_path / "state"
    repository = "owner/f130-cross-issue-proof"
    ledger = ReviewDeduplicator(repository, state)
    coordinates = _review_coordinates(tmp_path)
    diff = b"terminal proof must identify the current issue"
    diff_hash = review_diff_hash(diff)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "a" * 40)
    claim = ledger.claim_git(
        diff_hash, coordinates=coordinates, claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
    )
    body = "- [ ] cross-issue proof is refused\n"
    outcomes = [{**item, "verdict": "pass"} for item in acceptance_criteria(body)]
    AcceptanceProofStore(repository, state).create(
        issue_id="FOUNDRY-999", issue_body=body, reviewer_role="reviewer",
        outcomes=outcomes, quality="mergeable", diff_hash=diff_hash,
        claim_id=claim.claim_id, root=tmp_path, base=BASE_SHA, state_dir=state,
    )

    with pytest.raises(RoutingConfigError, match="incohérente avec l'issue"):
        ledger.validated_completed_proof_binding("FOUNDRY-130", diff_hash)


@pytest.mark.parametrize(
    ("quality", "verdict"),
    [
        ("blocked", "pass"),
        ("mergeable", "fail"),
        ("mergeable", "not_covered"),
        ("mergeable", "contradicted"),
    ],
    ids=("blocked", "failed", "not-covered", "contradicted"),
)
def test_terminal_review_rearm_refuses_blocked_or_non_pass_proof(
    monkeypatch, tmp_path, quality, verdict,
):
    state = tmp_path / "state"
    repository = "owner/f130-unmergeable-proof"
    ledger = ReviewDeduplicator(repository, state)
    coordinates = _review_coordinates(tmp_path)
    diff = b"terminal proof must be mergeable and all-pass"
    diff_hash = review_diff_hash(diff)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "a" * 40)
    claim = ledger.claim_git(
        diff_hash, coordinates=coordinates, claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
    )
    body = "- [ ] invalid terminal proof cannot rearm review\n"
    outcomes = [
        {**item, "verdict": verdict}
        for item in acceptance_criteria(body)
    ]
    AcceptanceProofStore(repository, state).create(
        issue_id="FOUNDRY-130", issue_body=body, reviewer_role="reviewer",
        outcomes=outcomes, quality=quality, diff_hash=diff_hash,
        claim_id=claim.claim_id, root=tmp_path, base=BASE_SHA, state_dir=state,
    )

    with pytest.raises(RoutingConfigError, match="non mergeable ou incomplète"):
        ledger.validated_completed_proof_binding("FOUNDRY-130", diff_hash)


def test_review_claim_records_in_progress_and_crash_requires_explicit_recovery(
    monkeypatch, tmp_path,
):
    ledger = ReviewDeduplicator("owner/repo", tmp_path)
    coordinates = _review_coordinates(tmp_path)
    first = ledger.claim(b"crash after claim", coordinates=coordinates)

    assert first.should_run is True
    assert first.state == "in_progress"
    assert first.claim_id
    assert ledger.claim(b"crash after claim", coordinates=coordinates).should_run is False

    record = json.loads((ledger.directory / first.diff_hash).read_text(encoding="utf-8"))
    assert record["schema_version"] == 1
    assert record["state"] == "in_progress"
    assert record["diff_hash"] == first.diff_hash
    assert record["generation"] == 1
    assert record["claim"]["id"] == first.claim_id
    assert record["claim"]["started_at"].endswith("Z")
    assert record["coordinates"] == coordinates
    assert record["recoveries"] == []

    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: b"crash after claim")
    recovered = ledger.recover(
        first.diff_hash, first.generation, first.claim_id, "reviewer interrupted",
        coordinates=coordinates,
    )
    assert recovered.should_run is True
    assert recovered.state == "in_progress"
    assert recovered.claim_id != first.claim_id

    record = json.loads((ledger.directory / first.diff_hash).read_text(encoding="utf-8"))
    assert record["generation"] == 2
    assert record["claim"]["id"] == recovered.claim_id
    assert record["recoveries"] == [{
        "abandoned_claim_id": first.claim_id,
        "reason": "reviewer interrupted",
        "recovered_at": record["recoveries"][0]["recovered_at"],
    }]
    assert record["recoveries"][0]["recovered_at"].endswith("Z")


def test_only_coordinate_bound_structured_proof_can_terminalize_a_review(monkeypatch, tmp_path):
    ledger = ReviewDeduplicator("owner/repo", tmp_path)
    coordinates = _review_coordinates(tmp_path)
    diff = b"terminal review"
    claim = ledger.claim(diff, coordinates=coordinates)

    with pytest.raises(RoutingConfigError, match="sans preuve AC structurée"):
        ledger.complete(claim.diff_hash, claim.claim_id)
    with pytest.raises(RoutingConfigError, match="identifiant de preuve"):
        ledger.complete_with_proof(claim.diff_hash, claim.claim_id, "a" * 64)
    assert ledger.verdict(claim.diff_hash).state == "in_progress"

    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "a" * 40)
    body = "- [ ] terminal contract\n"
    outcomes = [{**item, "verdict": "pass"} for item in acceptance_criteria(body)]
    proof = AcceptanceProofStore("owner/repo", tmp_path).create(
        issue_id="FOUNDRY-109", issue_body=body, reviewer_role="reviewer",
        outcomes=outcomes, quality="mergeable", diff_hash=claim.diff_hash,
        claim_id=claim.claim_id, root=tmp_path, base=BASE_SHA, state_dir=tmp_path,
    )
    assert ledger.completed_proof_id(claim.diff_hash) == proof["proof_id"]
    duplicate = ledger.claim(b"terminal review")
    assert duplicate.should_run is False
    assert duplicate.state == "completed"
    assert duplicate.claim_id is None
    with pytest.raises(RoutingConfigError, match="review terminée.*récupérée"):
        ledger.recover(
            claim.diff_hash, claim.generation, claim.claim_id, "retry anyway",
            coordinates=coordinates,
        )


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_recovery_requires_a_non_empty_human_reason(tmp_path, reason):
    ledger = ReviewDeduplicator("owner/repo", tmp_path)
    coordinates = _review_coordinates(tmp_path)
    claim = ledger.claim(b"reason required", coordinates=coordinates)

    with pytest.raises(RoutingConfigError, match="raison humaine.*non vide"):
        ledger.recover(
            claim.diff_hash, claim.generation, claim.claim_id, reason,
            coordinates=coordinates,
        )


def test_recovery_requires_exact_hash_and_current_claim_generation(tmp_path):
    ledger = ReviewDeduplicator("owner/repo", tmp_path)
    coordinates = _review_coordinates(tmp_path)
    claim = ledger.claim(b"exact generation", coordinates=coordinates)

    with pytest.raises(RoutingConfigError, match="SHA-256"):
        ledger.recover(
            "not-a-hash", claim.generation, claim.claim_id, "abandoned",
            coordinates=coordinates,
        )
    with pytest.raises(RoutingConfigError, match="génération.*n'est plus active"):
        ledger.recover(
            claim.diff_hash, claim.generation + 1, claim.claim_id, "abandoned",
            coordinates=coordinates,
        )


def test_recovery_requires_the_exact_active_claim_and_immutable_coordinates(tmp_path):
    ledger = ReviewDeduplicator("owner/repo", tmp_path)
    coordinates = _review_coordinates(tmp_path)
    claim = ledger.claim(b"coordinate-bound recovery", coordinates=coordinates)

    with pytest.raises(RoutingConfigError, match="plus active"):
        ledger.recover(
            claim.diff_hash, claim.generation, "0" * 64, "abandoned",
            coordinates=coordinates,
        )
    with pytest.raises(RoutingConfigError, match="coordonnées immuables"):
        ledger.recover(
            claim.diff_hash, claim.generation, claim.claim_id, "abandoned",
            coordinates={**coordinates, "base": "2" * 40},
        )
    assert ledger.verdict(claim.diff_hash).to_dict() == {
        "diff_hash": claim.diff_hash,
        "should_run": False,
        "state": "in_progress",
        "generation": 1,
    }

    marker = ledger.directory / claim.diff_hash
    record = json.loads(marker.read_text(encoding="utf-8"))
    record["acceptance_proof_id"] = "a" * 64
    marker.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(RoutingConfigError, match="preuve de review incomplète"):
        ledger.recover(
            claim.diff_hash, claim.generation, claim.claim_id, "abandoned",
            coordinates=coordinates,
        )
    assert ledger.verdict(claim.diff_hash).generation == 1


def test_old_owner_cannot_read_or_complete_after_recovery(monkeypatch, tmp_path):
    repository = "owner/repo"
    diff = b"same exact bytes"
    ledger = ReviewDeduplicator(repository, tmp_path / "state")
    coordinates = _review_coordinates(tmp_path)
    old = ledger.claim(diff, coordinates=coordinates)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    current = ledger.recover(
        old.diff_hash, old.generation, old.claim_id, "old reviewer disappeared",
        coordinates=coordinates,
    )
    with pytest.raises(RoutingConfigError, match="génération.*n'est plus active"):
        claimed_review_diff(
            old.diff_hash, repository, claim_id=old.claim_id,
            root=tmp_path, base=BASE_SHA, state_dir=tmp_path / "state", consume=bytes,
        )
    with pytest.raises(RoutingConfigError, match="sans preuve AC structurée"):
        ledger.complete(old.diff_hash, old.claim_id)

    assert claimed_review_diff(
        current.diff_hash, repository, claim_id=current.claim_id,
        root=tmp_path, base=BASE_SHA, state_dir=tmp_path / "state", consume=bytes,
    ) == diff
    with pytest.raises(RoutingConfigError, match="sans preuve AC structurée"):
        ledger.complete(current.diff_hash, current.claim_id)


def test_claimed_read_holds_the_ledger_lock_against_a_concurrent_recovery(
    monkeypatch, tmp_path,
):
    """A recovery cannot win after an old owner passed its read preflight."""
    repository, state, diff = "owner/repo", tmp_path / "state", b"locked read"
    ledger = ReviewDeduplicator(repository, state)
    coordinates = _review_coordinates(tmp_path)
    original = ledger.claim(diff, coordinates=coordinates)
    bytes_consumed, release_consumer = Event(), Event()
    recovery_started, recovery_finished = Event(), Event()
    result: dict[str, object] = {}

    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)

    def consume(bytes_to_review: bytes) -> None:
        result["bytes"] = bytes_to_review
        bytes_consumed.set()
        assert release_consumer.wait(timeout=2)

    def read_owner():
        claimed_review_diff(
            original.diff_hash, repository, claim_id=original.claim_id,
            root=tmp_path, base=BASE_SHA, state_dir=state, consume=consume,
        )

    def recover_owner():
        recovery_started.set()
        result["recovered"] = ledger.recover(
            original.diff_hash, original.generation, original.claim_id,
            "the reviewer stopped", coordinates=coordinates,
        )
        recovery_finished.set()

    reader = Thread(target=read_owner)
    reader.start()
    assert bytes_consumed.wait(timeout=2)
    recovery = Thread(target=recover_owner)
    recovery.start()
    assert recovery_started.wait(timeout=2)
    assert not recovery_finished.wait(timeout=0.1)

    release_consumer.set()
    reader.join(timeout=2)
    recovery.join(timeout=2)
    assert not reader.is_alive()
    assert not recovery.is_alive()
    assert result["bytes"] == diff
    assert result["recovered"].generation == 2


def test_recovery_holds_validation_and_generation_replacement_in_one_section(
    monkeypatch, tmp_path,
):
    """Concurrent drift rejects both recoveries without creating a new owner."""
    repository, state, diff = "owner/repo", tmp_path / "state", b"exact recovery"
    ledger = ReviewDeduplicator(repository, state)
    coordinates = _review_coordinates(tmp_path)
    original = ledger.claim(diff, coordinates=coordinates)
    first_diff_read, release_diff = Event(), Event()
    second_started, second_finished = Event(), Event()
    failures: list[str] = []

    def changed_diff(*_args, **_kwargs):
        if not first_diff_read.is_set():
            first_diff_read.set()
            assert release_diff.wait(timeout=2)
        return b"concurrently changed diff"

    monkeypatch.setattr("foundry.routing.git_diff", changed_diff)

    def recover_once(started=None, finished=None):
        if started is not None:
            started.set()
        try:
            ledger.recover(
                original.diff_hash, original.generation, original.claim_id,
                "the reviewer stopped", coordinates=coordinates,
            )
        except RoutingConfigError as exc:
            failures.append(str(exc))
        finally:
            if finished is not None:
                finished.set()

    first = Thread(target=recover_once)
    first.start()
    assert first_diff_read.wait(timeout=2)
    second = Thread(target=recover_once, args=(second_started, second_finished))
    second.start()
    assert second_started.wait(timeout=2)
    assert not second_finished.wait(timeout=0.1)

    release_diff.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive()
    assert not second.is_alive()
    assert len(failures) == 2
    assert all("diff a changé" in failure for failure in failures)
    verdict = ledger.verdict(original.diff_hash)
    assert verdict.state == "in_progress"
    assert verdict.generation == original.generation


def test_two_concurrent_recoveries_create_only_one_new_owner(tmp_path):
    repository = "owner/repo"
    state_dir = tmp_path / "shared"
    coordinates = _review_coordinates(state_dir)
    original = ReviewDeduplicator(repository, state_dir).claim(
        b"contended recovery", coordinates=coordinates,
    )
    args = (
        state_dir, repository, original.diff_hash, original.generation,
        original.claim_id, "human recovery", b"contended recovery",
    )

    with multiprocessing.get_context("spawn").Pool(2) as pool:
        results = pool.map(_recover_once, [args, args])

    successes = [result for result in results if "error" not in result]
    failures = [result for result in results if "error" in result]
    assert len(successes) == 1
    assert len(failures) == 1
    assert "génération" in failures[0]["error"]
    record = json.loads(
        (ReviewDeduplicator(repository, state_dir).directory / original.diff_hash)
        .read_text(encoding="utf-8")
    )
    assert record["generation"] == 2
    assert record["claim"]["id"] == successes[0]["claim_id"]


def test_f106_legacy_claim_is_quarantined_before_a_fresh_git_claim(
    monkeypatch, tmp_path,
):
    repository, state = "owner/f106-rebased", tmp_path / "state"
    diff = b"same bytes after rebase"
    coordinates = _review_coordinates(tmp_path)
    ledger = ReviewDeduplicator(repository, state)
    legacy = ledger.claim(diff)
    legacy_marker = ledger.directory / legacy.diff_hash
    legacy_record = json.loads(legacy_marker.read_text(encoding="utf-8"))
    legacy_capability = legacy.claim_id
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "9" * 40)

    fresh = ledger.claim_git(
        legacy.diff_hash, coordinates=coordinates,
        claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
    )

    assert fresh.should_run is True
    assert fresh.generation == 1
    assert fresh.claim_id != legacy_capability
    active = json.loads(legacy_marker.read_text(encoding="utf-8"))
    assert active["coordinates"] == coordinates
    assert active["claim"]["id"] == fresh.claim_id
    quarantine_marker, = (ledger.directory / "legacy-quarantine").iterdir()
    quarantine_before = quarantine_marker.read_bytes()
    quarantine = json.loads(quarantine_before)
    assert quarantine["migration_id"] == quarantine_marker.name
    assert quarantine["reason"] == "active_legacy_claim_without_git_coordinates"
    assert quarantine["legacy_record"] == legacy_record
    assert active["legacy_quarantine"] == {
        "migration_id": quarantine["migration_id"],
        "reason": quarantine["reason"],
    }

    with pytest.raises(RoutingConfigError, match="plus active"):
        claimed_review_diff(
            legacy.diff_hash, repository, claim_id=legacy_capability,
            root=tmp_path, base=BASE_SHA, state_dir=state, consume=bytes,
        )
    with pytest.raises(RoutingConfigError, match="plus active"):
        ledger.recover(
            legacy.diff_hash, legacy.generation, legacy_capability,
            "legacy reviewer interrupted", coordinates=coordinates,
        )

    body = "- [ ] the rebased review remains attributable\n"
    outcomes = [{**item, "verdict": "pass"} for item in acceptance_criteria(body)]
    with pytest.raises(RoutingConfigError, match="plus active"):
        AcceptanceProofStore(repository, state).create(
            issue_id="FOUNDRY-106", issue_body=body, reviewer_role="reviewer",
            outcomes=outcomes, quality="mergeable", diff_hash=legacy.diff_hash,
            claim_id=legacy_capability, root=tmp_path, base=BASE_SHA,
            state_dir=state,
        )
    proof = AcceptanceProofStore(repository, state).create(
        issue_id="FOUNDRY-106", issue_body=body, reviewer_role="reviewer",
        outcomes=outcomes, quality="mergeable", diff_hash=fresh.diff_hash,
        claim_id=fresh.claim_id, root=tmp_path, base=BASE_SHA, state_dir=state,
    )
    assert proof["generation"] == fresh.generation
    assert quarantine_marker.read_bytes() == quarantine_before


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda record: record.update({"acceptance_proof_id": "a" * 64}), "preuve"),
        (lambda record: record.update({"unknown": None}), "ambigu"),
        (lambda record: record.update({"state": "completed"}), "non actif"),
    ],
)
def test_legacy_quarantine_refuses_proof_and_ambiguous_or_terminal_schema(
    monkeypatch, tmp_path, mutation, expected,
):
    diff = b"legacy fail closed"
    ledger = ReviewDeduplicator("owner/f111-invalid", tmp_path / "state")
    legacy = ledger.claim(diff)
    marker = ledger.directory / legacy.diff_hash
    record = json.loads(marker.read_text(encoding="utf-8"))
    mutation(record)
    marker.write_text(json.dumps(record), encoding="utf-8")
    before = marker.read_bytes()
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)

    with pytest.raises(RoutingConfigError, match=expected):
        ledger.claim_git(legacy.diff_hash, coordinates=_review_coordinates(tmp_path))

    assert marker.read_bytes() == before
    assert not (ledger.directory / "legacy-quarantine").exists()


def test_legacy_quarantine_refuses_invalid_coordinates_and_diff_drift(
    monkeypatch, tmp_path,
):
    diff = b"legacy immutable before migration"
    ledger = ReviewDeduplicator("owner/f111-drift", tmp_path / "state")
    legacy = ledger.claim(diff)
    marker = ledger.directory / legacy.diff_hash
    before = marker.read_bytes()

    with pytest.raises(RoutingConfigError, match="coordonnées immuables"):
        ledger.claim_git(
            legacy.diff_hash,
            coordinates={"root": str(tmp_path), "base": "ambiguous"},
            claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
        )
    monkeypatch.setattr(
        "foundry.routing.git_diff", lambda *_args, **_kwargs: b"drifted Git bytes",
    )
    with pytest.raises(RoutingConfigError, match="diff a changé avant le claim"):
        ledger.claim_git(
            legacy.diff_hash,
            coordinates=_review_coordinates(tmp_path),
            claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
        )

    assert marker.read_bytes() == before
    assert not (ledger.directory / "legacy-quarantine").exists()


def test_legacy_quarantine_refuses_an_orphaned_acceptance_proof(tmp_path):
    repository, state = "owner/f111-proof", tmp_path / "state"
    diff = b"legacy claim with orphaned proof"
    ledger = ReviewDeduplicator(repository, state)
    legacy = ledger.claim(diff)
    body = "- [ ] legacy review was already attested\n"
    criteria = acceptance_criteria(body)
    proof = {
        "schema_version": 1,
        "issue": {
            "id": "FOUNDRY-111",
            "ac_digest": acceptance_digest(criteria),
            "criteria": [{**item, "verdict": "pass"} for item in criteria],
        },
        "review": {
            "role": "reviewer",
            "generation": legacy.generation,
            "claim_digest": hashlib.sha256(
                legacy.claim_id.encode("ascii")
            ).hexdigest(),
        },
        "coordinates": {
            "head": "9" * 40,
            "diff_hash": legacy.diff_hash,
            "base": BASE_SHA,
        },
        "quality": "mergeable",
    }
    proof["proof_id"] = hashlib.sha256(
        json.dumps(proof, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    AcceptanceProofStore(repository, state)._write(proof)
    marker = ledger.directory / legacy.diff_hash
    before = marker.read_bytes()

    with pytest.raises(RoutingConfigError, match="preuve de review incomplète"):
        ledger.claim_git(legacy.diff_hash, coordinates=_review_coordinates(tmp_path))

    assert marker.read_bytes() == before
    assert not (ledger.directory / "legacy-quarantine").exists()


def test_coordinated_claim_never_enters_legacy_quarantine(monkeypatch, tmp_path):
    diff = b"already coordinate bound"
    first_coordinates = _review_coordinates(tmp_path)
    other_coordinates = {
        "root": str((tmp_path / "other").resolve()),
        "base": BASE_SHA,
    }
    ledger = ReviewDeduplicator("owner/f111-coordinated", tmp_path / "state")
    existing = ledger.claim(diff, coordinates=first_coordinates)
    marker = ledger.directory / existing.diff_hash
    before = marker.read_bytes()
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)

    duplicate = ledger.claim_git(existing.diff_hash, coordinates=first_coordinates)
    assert duplicate.should_run is False
    assert duplicate.claim_id is None
    with pytest.raises(RoutingConfigError, match="coordonnées immuables"):
        ledger.claim_git(existing.diff_hash, coordinates=other_coordinates)

    assert marker.read_bytes() == before
    assert not (ledger.directory / "legacy-quarantine").exists()


def test_legacy_quarantine_is_atomic_under_concurrency_and_crash_replay(
    monkeypatch, tmp_path,
):
    repository, state = "owner/f111-race", tmp_path / "shared"
    diff = b"one legacy claim, one fresh owner"
    coordinates = _review_coordinates(tmp_path)
    legacy = ReviewDeduplicator(repository, state).claim(diff)
    args = (state, repository, diff, coordinates)

    with multiprocessing.get_context("spawn").Pool(2) as pool:
        results = pool.map(_claim_git_once, [args, args])

    successes = [result for result in results if result.get("should_run") is True]
    duplicates = [result for result in results if result.get("should_run") is False]
    assert len(successes) == 1
    assert len(duplicates) == 1
    assert successes[0]["claim_id"] != legacy.claim_id
    assert "claim_id" not in duplicates[0]
    ledger = ReviewDeduplicator(repository, state)
    quarantine_markers = tuple((ledger.directory / "legacy-quarantine").iterdir())
    assert len(quarantine_markers) == 1

    replay_state = tmp_path / "replay"
    replay_ledger = ReviewDeduplicator(repository, replay_state)
    replay_legacy = replay_ledger.claim(diff)
    original_atomic_write = ReviewDeduplicator._atomic_write

    def interrupt_after_quarantine(self, marker, record):
        if marker.name == replay_legacy.diff_hash and "coordinates" in record:
            raise KeyboardInterrupt()
        return original_atomic_write(self, marker, record)

    monkeypatch.setattr(ReviewDeduplicator, "_atomic_write", interrupt_after_quarantine)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    with pytest.raises(KeyboardInterrupt):
        replay_ledger.claim_git(
            replay_legacy.diff_hash,
            coordinates=coordinates,
            claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
        )
    replay_quarantine, = (
        replay_ledger.directory / "legacy-quarantine"
    ).iterdir()
    quarantine_before = replay_quarantine.read_bytes()
    assert json.loads(
        (replay_ledger.directory / replay_legacy.diff_hash).read_text(encoding="utf-8")
    )["claim"]["id"] == replay_legacy.claim_id

    monkeypatch.setattr(ReviewDeduplicator, "_atomic_write", original_atomic_write)
    fresh = replay_ledger.claim_git(
        replay_legacy.diff_hash,
        coordinates=coordinates,
        claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
    )
    duplicate = replay_ledger.claim_git(replay_legacy.diff_hash, coordinates=coordinates)
    assert fresh.should_run is True
    assert fresh.claim_id != replay_legacy.claim_id
    assert duplicate.should_run is False
    assert replay_quarantine.read_bytes() == quarantine_before
    assert len(tuple(replay_quarantine.parent.iterdir())) == 1


def test_f111_interrupted_fresh_publication_replays_to_one_usable_owner(
    monkeypatch, tmp_path,
):
    repository = "owner/f111-interrupted-publication"
    state = tmp_path / "state"
    diff = b"fresh coordinated claim published before its caller receives it"
    diff_hash = review_diff_hash(diff)
    coordinates = _review_coordinates(tmp_path)
    claim_attempt_token = CLAIM_ATTEMPT_TOKEN
    ledger = ReviewDeduplicator(repository, state)
    original_atomic_write = ReviewDeduplicator._atomic_write
    interrupted = False

    def interrupt_after_durable_publication(self, marker, record):
        nonlocal interrupted
        original_atomic_write(self, marker, record)
        if marker.name == diff_hash and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()

    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "9" * 40)
    monkeypatch.setattr(
        ReviewDeduplicator, "_atomic_write", interrupt_after_durable_publication,
    )
    with pytest.raises(KeyboardInterrupt):
        ledger.claim_git(
            diff_hash,
            coordinates=coordinates,
            claim_attempt_token=claim_attempt_token,
        )

    marker = ledger.directory / diff_hash
    interrupted_record = json.loads(marker.read_text(encoding="utf-8"))
    original_claim_id = interrupted_record["claim"]["id"]
    assert interrupted_record["claim_publication"] == {
        "attempt_digest": hashlib.sha256(
            claim_attempt_token.encode("ascii")
        ).hexdigest(),
        "generation": 1,
    }
    assert claim_attempt_token not in marker.read_text(encoding="utf-8")

    monkeypatch.setattr(ReviewDeduplicator, "_atomic_write", original_atomic_write)
    ordinary_duplicate = ledger.claim_git(diff_hash, coordinates=coordinates)
    assert ordinary_duplicate.should_run is False
    assert ordinary_duplicate.claim_id is None
    token_without_replay = ledger.claim_git(
        diff_hash,
        coordinates=coordinates,
        claim_attempt_token=claim_attempt_token,
    )
    assert token_without_replay.should_run is False
    assert token_without_replay.claim_id is None

    before_replays = marker.read_bytes()
    replay_args = (state, repository, diff, coordinates, claim_attempt_token)
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        replay_results = pool.map(
            _replay_interrupted_claim_once, [replay_args, replay_args],
        )

    owners = [result for result in replay_results if result.get("should_run") is True]
    assert len(owners) == 2
    assert {owner["generation"] for owner in owners} == {1}
    assert {owner["claim_id"] for owner in owners} == {original_claim_id}
    assert marker.read_bytes() == before_replays

    with pytest.raises(KeyboardInterrupt):
        replayed_but_not_delivered = ledger.claim_git(
            diff_hash,
            coordinates=coordinates,
            claim_attempt_token=claim_attempt_token,
            replay_interrupted=True,
        )
        assert replayed_but_not_delivered.claim_id == original_claim_id
        raise KeyboardInterrupt()
    replayed_again = ledger.claim_git(
        diff_hash,
        coordinates=coordinates,
        claim_attempt_token=claim_attempt_token,
        replay_interrupted=True,
    )
    assert replayed_again.claim_id == original_claim_id
    assert marker.read_bytes() == before_replays

    current = ledger.recover(
        diff_hash, 1, original_claim_id, "reviewer process stopped",
        coordinates=coordinates,
    )
    with pytest.raises(RoutingConfigError, match="plus active"):
        ledger.claim_git(
            diff_hash,
            coordinates=coordinates,
            claim_attempt_token=claim_attempt_token,
            replay_interrupted=True,
        )

    with pytest.raises(RoutingConfigError, match="plus active"):
        claimed_review_diff(
            diff_hash, repository, claim_id=original_claim_id,
            root=tmp_path, base=BASE_SHA, state_dir=state, consume=bytes,
        )
    with pytest.raises(RoutingConfigError, match="plus active"):
        ledger.recover(
            diff_hash, 1, original_claim_id, "stale owner retries",
            coordinates=coordinates,
        )
    body = "- [ ] interrupted publication keeps the current owner usable\n"
    outcomes = [
        {**criterion, "verdict": "pass"}
        for criterion in acceptance_criteria(body)
    ]
    with pytest.raises(RoutingConfigError, match="plus active"):
        AcceptanceProofStore(repository, state).create(
            issue_id="FOUNDRY-111", issue_body=body, reviewer_role="reviewer",
            outcomes=outcomes, quality="mergeable", diff_hash=diff_hash,
            claim_id=original_claim_id, root=tmp_path, base=BASE_SHA,
            state_dir=state,
        )
    assert claimed_review_diff(
        diff_hash, repository, claim_id=current.claim_id,
        root=tmp_path, base=BASE_SHA, state_dir=state, consume=bytes,
    ) == diff
    proof = AcceptanceProofStore(repository, state).create(
        issue_id="FOUNDRY-111", issue_body=body, reviewer_role="reviewer",
        outcomes=outcomes, quality="mergeable", diff_hash=diff_hash,
        claim_id=current.claim_id, root=tmp_path, base=BASE_SHA,
        state_dir=state,
    )
    assert ledger.completed_proof_id(diff_hash) == proof["proof_id"]


def test_f111_publication_replay_rejects_malformed_or_unknown_attempts_without_mutation(
    monkeypatch, tmp_path,
):
    repository = "owner/f111-invalid-replay"
    state = tmp_path / "state"
    diff = b"coordinate-bound claim with a replay identity"
    diff_hash = review_diff_hash(diff)
    coordinates = _review_coordinates(tmp_path)
    ledger = ReviewDeduplicator(repository, state)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)

    with pytest.raises(RoutingConfigError, match="token secret de tentative requis"):
        ledger.claim_git(diff_hash, coordinates=coordinates)
    with pytest.raises(RoutingConfigError, match="token secret de tentative"):
        ledger.claim_git(
            diff_hash, coordinates=coordinates, claim_attempt_token="short",
        )
    with pytest.raises(RoutingConfigError, match="token secret.*requis"):
        ledger.claim_git(
            diff_hash, coordinates=coordinates, replay_interrupted=True,
        )
    assert ledger.is_claimed(diff_hash) is False

    owner = ledger.claim_git(
        diff_hash,
        coordinates=coordinates,
        claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
    )
    marker = ledger.directory / diff_hash
    before = marker.read_bytes()
    malformed = json.loads(before)
    malformed["claim_publication"]["attempt_digest"] = "not-a-digest"
    marker.write_text(json.dumps(malformed), encoding="utf-8")
    malformed_before = marker.read_bytes()
    with pytest.raises(RoutingConfigError, match="publication de claim invalide"):
        ledger.claim_git(diff_hash, coordinates=coordinates)
    assert marker.read_bytes() == malformed_before
    marker.write_bytes(before)

    with pytest.raises(RoutingConfigError, match="token de tentative inconnu"):
        ledger.claim_git(
            diff_hash,
            coordinates=coordinates,
            claim_attempt_token=OTHER_CLAIM_ATTEMPT_TOKEN,
            replay_interrupted=True,
        )
    assert marker.read_bytes() == before
    ledger.assert_active(diff_hash, owner.claim_id)


def test_legacy_marker_is_migrated_fail_closed_to_completed(tmp_path):
    ledger = ReviewDeduplicator("owner/repo", tmp_path)
    diff = b"legacy claimed bytes"
    diff_hash = review_diff_hash(diff)
    ledger.directory.mkdir(parents=True)
    marker = ledger.directory / diff_hash
    marker.write_text(json.dumps({"diff_hash": diff_hash}), encoding="utf-8")

    duplicate = ledger.claim(diff)

    assert duplicate.should_run is False
    assert duplicate.state == "completed"
    migrated = json.loads(marker.read_text(encoding="utf-8"))
    assert migrated["state"] == "completed"
    assert migrated["legacy_migrated"] is True
    with pytest.raises(RoutingConfigError, match="review terminée.*récupérée"):
        ledger.recover(
            diff_hash, duplicate.generation, "0" * 64, "legacy recovery",
            coordinates=_review_coordinates(tmp_path),
        )


def test_claimed_review_diff_requires_ledger_claim_and_exact_current_hash(
    monkeypatch, tmp_path,
):
    repository = "owner/repo"
    diff = b"claimed bytes"
    claim = ReviewDeduplicator(repository, tmp_path).claim(
        diff, coordinates=_review_coordinates(tmp_path),
    )
    monkeypatch.setattr("foundry.routing.git_diff", lambda _root, _base: diff)

    assert claimed_review_diff(
        claim.diff_hash, repository, claim_id=claim.claim_id,
        root=tmp_path, base=BASE_SHA, state_dir=tmp_path, consume=bytes,
    ) == diff

    with pytest.raises(RoutingConfigError, match="coordonnées immuables"):
        claimed_review_diff(
            claim.diff_hash, repository, claim_id=claim.claim_id,
            root=tmp_path / "alternate-worktree", base=BASE_SHA, state_dir=tmp_path,
            consume=bytes,
        )
    with pytest.raises(RoutingConfigError, match="coordonnées immuables"):
        claimed_review_diff(
            claim.diff_hash, repository, claim_id=claim.claim_id,
            root=tmp_path, base="2" * 40, state_dir=tmp_path, consume=bytes,
        )

    monkeypatch.setattr("foundry.routing.git_diff", lambda _root, _base: b"changed bytes")
    with pytest.raises(RoutingConfigError, match="le diff a changé depuis le claim"):
        claimed_review_diff(
            claim.diff_hash, repository, claim_id=claim.claim_id,
            root=tmp_path, base=BASE_SHA, state_dir=tmp_path, consume=bytes,
        )

    missing = review_diff_hash(b"never claimed")
    with pytest.raises(RoutingConfigError, match="n'est pas réservé"):
        claimed_review_diff(
            missing, repository, claim_id="0" * 64,
            root=tmp_path, base=BASE_SHA, state_dir=tmp_path, consume=bytes,
        )


def test_claim_review_cli_refuses_diff_file_before_preflight_or_ledger_write(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    preflighted = False

    def unexpected_preflight(*_args, **_kwargs):
        nonlocal preflighted
        preflighted = True

    monkeypatch.setattr(EscalationStore, "active_floor", unexpected_preflight)
    with pytest.raises(SystemExit) as error:
        main([
            "claim-review", "--issue", "FOUNDRY-42", "--repository", "owner/repo",
            "--diff-file", str(tmp_path / "change.diff"),
            "--root", str(tmp_path), "--base", BASE_SHA,
        ])

    assert error.value.code == 2
    assert preflighted is False
    assert not (tmp_path / "state").exists()


def test_claim_review_cli_replays_an_interrupted_publication_with_same_attempt(
    monkeypatch, tmp_path, capsys,
):
    state = tmp_path / "state"
    repository = "owner/f111-cli-replay"
    diff = b"cli claim capability delivery interrupted"
    diff_hash = review_diff_hash(diff)
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    original_atomic_write = ReviewDeduplicator._atomic_write
    interrupted = False

    def interrupt_after_durable_publication(self, marker, record):
        nonlocal interrupted
        original_atomic_write(self, marker, record)
        if marker.name == diff_hash and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()

    argv = [
        "claim-review", "--issue", "FOUNDRY-111", "--repository", repository,
        "--git-diff", "--root", str(tmp_path), "--base", BASE_SHA,
        "--claim-attempt-token", CLAIM_ATTEMPT_TOKEN,
    ]
    monkeypatch.setattr(
        ReviewDeduplicator, "_atomic_write", interrupt_after_durable_publication,
    )
    with pytest.raises(KeyboardInterrupt):
        main(argv)
    marker = ReviewDeduplicator(repository, state).directory / diff_hash
    published = json.loads(marker.read_text(encoding="utf-8"))
    published_bytes = marker.read_bytes()

    monkeypatch.setattr(ReviewDeduplicator, "_atomic_write", original_atomic_write)
    main(argv)
    duplicate = json.loads(capsys.readouterr().out)
    assert duplicate["should_run"] is False
    assert "claim_id" not in duplicate

    main([*argv, "--replay-interrupted"])
    owner = json.loads(capsys.readouterr().out)
    assert owner["should_run"] is True
    assert owner["generation"] == 1
    assert owner["claim_id"] == published["claim"]["id"]
    assert CLAIM_ATTEMPT_TOKEN not in json.dumps(owner)

    main([*argv, "--replay-interrupted"])
    repeated = json.loads(capsys.readouterr().out)
    assert repeated == owner
    assert marker.read_bytes() == published_bytes


def test_claude_claim_review_rejects_pending_route_before_claim_then_allows_consumed_reviewer(
    monkeypatch, tmp_path, capsys,
):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    store, issue, generation = _technical_route_state(
        tmp_path, "reviewer", consume=False,
    )
    repository = "owner/claude-pending-route"
    diff = b"review after local implementation"
    current_diff = {"value": diff}
    argv = [
        "claim-review", "--issue", issue, "--repository", repository,
        "--root", str(tmp_path), "--git-diff", "--base", BASE_SHA,
        "--claim-attempt-token", CLAIM_ATTEMPT_TOKEN,
    ]
    deduplicator = ReviewDeduplicator(repository, state_dir)
    reads = []
    monkeypatch.setattr(
        "foundry.routing.git_diff",
        lambda *_args, **_kwargs: reads.append(True) or current_diff["value"],
    )

    with pytest.raises(EscalationTechnicalBlockedError, match="explicitement demandée"):
        main(argv, handle_config_errors=False)
    assert reads == []
    assert deduplicator.is_claimed(review_diff_hash(diff)) is False

    store.claim_technical_remediation_route(
        issue, "reviewer", generation, "f108-reviewer-route-0001",
    )
    main(argv)
    claim = json.loads(capsys.readouterr().out)
    assert reads == [True, True]
    assert claim["should_run"] is True
    assert claim["diff_hash"] == review_diff_hash(diff)

    second_diff = b"a distinct review after the same remediation"
    current_diff["value"] = second_diff
    with pytest.raises(EscalationTechnicalBlockedError, match="déjà autorisé"):
        main(
            [
                "claim-review", "--issue", issue, "--repository", repository,
                "--root", str(tmp_path), "--git-diff",
                "--base", BASE_SHA,
            ],
            handle_config_errors=False,
        )
    assert deduplicator.is_claimed(review_diff_hash(second_diff)) is False


@pytest.mark.parametrize("route_role", ["scout", "coordinator", "architect"])
def test_claude_claim_review_rejects_consumed_non_reviewer_route_before_claim(
    monkeypatch, tmp_path, route_role,
):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    _, issue, _ = _technical_route_state(tmp_path, route_role, consume=True)
    repository = f"owner/claude-{route_role}-route"
    diff = f"invalid {route_role} remediation route".encode()
    deduplicator = ReviewDeduplicator(repository, state_dir)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)

    with pytest.raises(EscalationTechnicalBlockedError, match="explicitement demandée"):
        main(
            [
                "claim-review", "--issue", issue, "--repository", repository,
                "--root", str(tmp_path), "--git-diff", "--base", BASE_SHA,
            ],
            handle_config_errors=False,
        )
    assert deduplicator.is_claimed(review_diff_hash(diff)) is False


def test_recover_cli_is_redacted_and_complete_review_refuses_without_mutation(
    tmp_path, capsys, monkeypatch,
):
    state = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    repository = "owner/repo"
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: b"sensitive diff body")

    main([
        "claim-review", "--issue", "FOUNDRY-42", "--repository", repository,
        "--git-diff", "--root", str(tmp_path), "--base", BASE_SHA,
        "--claim-attempt-token", CLAIM_ATTEMPT_TOKEN,
    ])
    original = json.loads(capsys.readouterr().out)
    reason = "human says the previous reviewer crashed"
    main([
        "recover-review", "--repository", repository,
        "--diff-hash", original["diff_hash"],
        "--generation", str(original["generation"]),
        "--claim-id", original["claim_id"],
        "--root", str(tmp_path), "--base", BASE_SHA, "--reason", reason,
    ])
    recovered_output = capsys.readouterr().out
    recovered = json.loads(recovered_output)

    assert recovered == {
        "diff_hash": original["diff_hash"],
        "should_run": True,
        "state": "in_progress",
        "generation": 2,
        "claim_id": recovered["claim_id"],
    }
    assert "sensitive diff body" not in recovered_output
    assert reason not in recovered_output

    before = ReviewDeduplicator(repository, state).verdict(recovered["diff_hash"])
    with pytest.raises(SystemExit, match="sans preuve AC structurée"):
        main([
            "complete-review", "--repository", repository,
            "--diff-hash", recovered["diff_hash"],
            "--claim-id", recovered["claim_id"],
        ])
    assert ReviewDeduplicator(repository, state).verdict(recovered["diff_hash"]) == before
    assert reason not in capsys.readouterr().out


def test_recover_review_cli_requires_active_claim_coordinates_and_unchanged_diff(
    monkeypatch, tmp_path, capsys,
):
    state = tmp_path / "state"
    repository = "owner/repo"
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    diff = {"value": b"exact original review diff"}
    reads = []
    monkeypatch.setattr(
        "foundry.routing.git_diff",
        lambda *_args, **_kwargs: reads.append(True) or diff["value"],
    )
    claim_argv = [
        "claim-review", "--issue", "FOUNDRY-42", "--repository", repository,
        "--git-diff", "--root", str(tmp_path), "--base", BASE_SHA,
        "--claim-attempt-token", CLAIM_ATTEMPT_TOKEN,
    ]
    main(claim_argv)
    original = json.loads(capsys.readouterr().out)
    reads.clear()
    recover_argv = [
        "recover-review", "--repository", repository,
        "--diff-hash", original["diff_hash"],
        "--generation", str(original["generation"]),
        "--claim-id", original["claim_id"],
        "--root", str(tmp_path), "--base", BASE_SHA,
        "--reason", "human confirms the reviewer stopped",
    ]

    wrong_claim_argv = [*recover_argv]
    wrong_claim_argv[8] = "0" * 64
    with pytest.raises(RoutingConfigError, match="plus active"):
        main(wrong_claim_argv, handle_config_errors=False)
    assert reads == []
    wrong_coordinates_argv = [*recover_argv]
    wrong_coordinates_argv[12] = "2" * 40
    with pytest.raises(RoutingConfigError, match="coordonnées immuables"):
        main(wrong_coordinates_argv, handle_config_errors=False)
    assert reads == []

    diff["value"] = b"changed review diff"
    with pytest.raises(RoutingConfigError, match="diff a changé"):
        main(recover_argv, handle_config_errors=False)
    assert reads == [True]
    assert ReviewDeduplicator(repository, state).verdict(original["diff_hash"]).generation == 1

    diff["value"] = b"exact original review diff"
    main(recover_argv)
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["generation"] == 2


def test_git_diff_is_the_exact_review_claim_input(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = b"exact review diff"
        stderr = b""

    calls = []
    monkeypatch.setattr("foundry.routing._project_root", lambda _root: tmp_path)
    monkeypatch.setattr("foundry.routing.subprocess.run",
                        lambda args, **kwargs: calls.append((args, kwargs)) or Result())

    assert git_diff(tmp_path, "origin/trunk") == b"exact review diff"
    assert calls[0][0] == [
        "git", "diff", "--binary", "--no-ext-diff", "origin/trunk...HEAD",
    ]


def test_git_diff_rejects_option_injection_before_running_git(monkeypatch, tmp_path):
    called = False

    def unexpected_run(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("foundry.routing.subprocess.run", unexpected_run)

    with pytest.raises(RoutingConfigError, match="base Git invalide"):
        git_diff(tmp_path, "--output=/tmp/probe")
    assert called is False


def test_structured_ac_proof_is_exact_redacted_and_fails_closed_when_stale(monkeypatch, tmp_path):
    repository = "owner/repo"
    state = tmp_path / "state"
    body = "## Critères\n- [ ] first contract\n- [ ] second contract\n"
    diff = b"exact reviewed diff"
    base_sha = "c" * 40
    claim = ReviewDeduplicator(repository, state).claim(
        diff, coordinates={"root": str(tmp_path.resolve()), "base": base_sha},
    )
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "a" * 40)
    store = AcceptanceProofStore(repository, state)
    criteria = acceptance_criteria(body)
    outcome = [{**item, "verdict": "pass"} for item in criteria]

    with pytest.raises(RoutingConfigError, match="coordonnées immuables"):
        store.create(
            issue_id="DEMO-9", issue_body=body, reviewer_role="reviewer", outcomes=outcome,
            quality="mergeable", diff_hash=claim.diff_hash, claim_id=claim.claim_id,
            root=tmp_path / "alternate-worktree", base=base_sha, state_dir=state,
        )
    with pytest.raises(RoutingConfigError, match="coordonnées immuables"):
        store.create(
            issue_id="DEMO-9", issue_body=body, reviewer_role="reviewer", outcomes=outcome,
            quality="mergeable", diff_hash=claim.diff_hash, claim_id=claim.claim_id,
            root=tmp_path, base="d" * 40, state_dir=state,
        )
    assert ReviewDeduplicator(repository, state).verdict(claim.diff_hash).state == "in_progress"

    result = store.create(
        issue_id="DEMO-9", issue_body=body, reviewer_role="reviewer", outcomes=outcome,
        quality="mergeable", diff_hash=claim.diff_hash, claim_id=claim.claim_id,
        root=tmp_path, base=base_sha, state_dir=state,
    )
    assert result["proof_id"] not in json.dumps(outcome)
    valid = store.valid_for_merge(
        issue_id="DEMO-9", issue_body=body, head="a" * 40, diff=diff,
        base=base_sha,
    )
    assert valid["proof_id"] == result["proof_id"]
    with pytest.raises(RoutingConfigError, match="absente, périmée ou ambiguë"):
        store.valid_for_merge(
            issue_id="DEMO-9", issue_body=body, head="b" * 40, diff=diff,
            base=base_sha,
        )
    with pytest.raises(RoutingConfigError, match="absente, périmée ou ambiguë"):
        store.valid_for_merge(
            issue_id="DEMO-9", issue_body=body, head="a" * 40, diff=diff,
            base="d" * 40,
        )
    with pytest.raises(RoutingConfigError, match="digest invalide"):
        store.create(
            issue_id="DEMO-9", issue_body=body, reviewer_role="reviewer",
            outcomes=[{**outcome[0], "digest": "0" * 64}, outcome[1]], quality="mergeable",
            diff_hash=claim.diff_hash, claim_id=claim.claim_id, root=tmp_path,
            base=base_sha, state_dir=state,
        )


def test_acceptance_sync_checks_only_passed_boxes_and_preserves_literal_body():
    body = (
        "Intro\r\n"
        "- [ ] first contract\r\n"
        "- [ ] second **literal** contract\r\n"
        "- [x] already complete\r\n"
        "Footer\r\n"
    )
    criteria = acceptance_criteria(body)
    proof = {
        "proof_id": "a" * 64,
        "issue": {
            "id": "DEMO-9",
            "ac_digest": acceptance_digest(criteria),
            "criteria": [
                {**criteria[0], "verdict": "pass"},
                {**criteria[1], "verdict": "fail"},
                {**criteria[2], "verdict": "pass"},
            ],
        },
    }

    updated, changed = synchronize_acceptance_body("DEMO-9", body, proof)

    assert changed == 1
    assert updated == body.replace("- [ ] first contract", "- [x] first contract")
    assert acceptance_criteria(updated) == criteria


def test_acceptance_sync_refuses_stale_or_foreign_proof_before_mutation():
    body = "- [ ] current contract\n"
    criteria = acceptance_criteria(body)
    proof = {
        "proof_id": "b" * 64,
        "issue": {
            "id": "OTHER-9",
            "ac_digest": acceptance_digest(criteria),
            "criteria": [{**criteria[0], "verdict": "pass"}],
        },
    }

    with pytest.raises(RoutingConfigError, match="preuve incompatible"):
        synchronize_acceptance_body("DEMO-9", body, proof)

    proof["issue"]["id"] = "DEMO-9"
    proof["issue"]["criteria"][0]["digest"] = "0" * 64
    with pytest.raises(RoutingConfigError, match="verdict structuré invalide"):
        synchronize_acceptance_body("DEMO-9", body, proof)


@pytest.mark.parametrize("base", [None, "origin/main", "A" * 40, "a" * 39])
def test_ac_proof_requires_an_exact_lowercase_base_sha(tmp_path, base):
    store = AcceptanceProofStore("owner/repo", tmp_path / "state")

    with pytest.raises(RoutingConfigError, match="SHA de base Git exact"):
        store.create(
            issue_id="DEMO-1", issue_body="- [ ] contract\n",
            reviewer_role="reviewer", outcomes=[], quality="mergeable",
            diff_hash="b" * 64, claim_id="c" * 64, root=tmp_path, base=base,
        )
    with pytest.raises(RoutingConfigError, match="SHA de base Git exact"):
        store.valid_for_merge(
            issue_id="DEMO-1", issue_body="- [ ] contract\n",
            head="d" * 40, diff=b"diff", base=base,
        )


@pytest.mark.parametrize("indent", ["  ", ""])
def test_multiline_ac_mutation_invalidates_an_otherwise_current_proof(
    monkeypatch, tmp_path,
    indent,
):
    repository, state, diff = "owner/repo", tmp_path / "state", b"reviewed bytes"
    body = f"- [ ] first line\n{indent}material continuation\n"
    claim = ReviewDeduplicator(repository, state).claim(
        diff, coordinates=_review_coordinates(tmp_path),
    )
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "d" * 40)
    store = AcceptanceProofStore(repository, state)
    outcomes = [{**item, "verdict": "pass"} for item in acceptance_criteria(body)]
    store.create(issue_id="DEMO-2", issue_body=body, reviewer_role="reviewer",
                 outcomes=outcomes, quality="mergeable", diff_hash=claim.diff_hash,
                 claim_id=claim.claim_id, root=tmp_path, base=BASE_SHA,
                 state_dir=state)

    with pytest.raises(RoutingConfigError, match="absente, périmée ou ambiguë"):
        store.valid_for_merge(issue_id="DEMO-2",
                              issue_body=f"- [ ] first line\n{indent}changed continuation\n",
                              head="d" * 40, diff=diff, base=BASE_SHA)


def test_lazy_ac_continuation_stops_at_checkbox_heading_and_independent_content():
    original = (
        "- [ ] first\n"
        "lazy continuation\n"
        "- [ ] second\n"
        "## Separate heading\n"
        "independent paragraph\n"
        "- [ ] third\n"
        "\n"
        "independent after a blank\n"
        "- [ ] fourth\n"
    )
    changed = original.replace("lazy continuation", "changed continuation")
    independent_changed = original.replace(
        "independent after a blank", "changed independent paragraph",
    )

    original_criteria = acceptance_criteria(original)
    changed_criteria = acceptance_criteria(changed)

    assert [item["id"] for item in original_criteria] == [
        "ac-1", "ac-2", "ac-3", "ac-4",
    ]
    assert original_criteria[0]["digest"] == review_diff_hash("first\nlazy continuation")
    assert changed_criteria[0]["digest"] != original_criteria[0]["digest"]
    assert changed_criteria[1:] == original_criteria[1:]
    assert acceptance_criteria(independent_changed) == original_criteria


def test_proof_write_is_rolled_back_when_ledger_terminalization_fails(monkeypatch, tmp_path):
    repository, state, diff = "owner/repo", tmp_path / "state", b"sensitive review"
    claim = ReviewDeduplicator(repository, state).claim(
        diff, coordinates=_review_coordinates(tmp_path),
    )
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "e" * 40)
    store = AcceptanceProofStore(repository, state)
    body = "- [ ] contract\n"
    outcomes = [{**item, "verdict": "pass"} for item in acceptance_criteria(body)]
    original_write = ReviewDeduplicator._atomic_write

    def fail_terminal(self, marker, record):
        if record.get("acceptance_proof_id"):
            raise OSError("simulated ledger failure")
        return original_write(self, marker, record)

    monkeypatch.setattr(ReviewDeduplicator, "_atomic_write", fail_terminal)
    with pytest.raises(OSError, match="simulated ledger failure"):
        store.create(issue_id="DEMO-3", issue_body=body, reviewer_role="reviewer",
                     outcomes=outcomes, quality="mergeable", diff_hash=claim.diff_hash,
                     claim_id=claim.claim_id, root=tmp_path, base=BASE_SHA,
                     state_dir=state)
    assert not store.directory.exists() or not list(store.directory.iterdir())

    monkeypatch.setattr(ReviewDeduplicator, "_atomic_write", original_write)
    proof = store.create(issue_id="DEMO-3", issue_body=body, reviewer_role="reviewer",
                         outcomes=outcomes, quality="mergeable", diff_hash=claim.diff_hash,
                         claim_id=claim.claim_id, root=tmp_path, base=BASE_SHA,
                         state_dir=state)
    assert proof["proof_id"]


def test_precrash_canonical_proof_is_reused_but_hostile_artifact_is_refused(monkeypatch, tmp_path):
    repository, state, diff = "owner/repo", tmp_path / "state", b"crash-sensitive diff"
    claim = ReviewDeduplicator(repository, state).claim(
        diff, coordinates=_review_coordinates(tmp_path),
    )
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "f" * 40)
    store = AcceptanceProofStore(repository, state)
    body = "- [ ] durable contract\n"
    outcomes = [{**item, "verdict": "pass"} for item in acceptance_criteria(body)]
    original_write = ReviewDeduplicator._atomic_write

    def interrupted_terminal(self, marker, record):
        if record.get("acceptance_proof_id"):
            raise KeyboardInterrupt()
        return original_write(self, marker, record)

    monkeypatch.setattr(ReviewDeduplicator, "_atomic_write", interrupted_terminal)
    with pytest.raises(KeyboardInterrupt):
        store.create(issue_id="DEMO-4", issue_body=body, reviewer_role="reviewer",
                     outcomes=outcomes, quality="mergeable", diff_hash=claim.diff_hash,
                     claim_id=claim.claim_id, root=tmp_path, base=BASE_SHA,
                     state_dir=state)
    marker = next(store.directory.iterdir())
    assert ReviewDeduplicator(repository, state).verdict(claim.diff_hash).state == "in_progress"

    monkeypatch.setattr(ReviewDeduplicator, "_atomic_write", original_write)
    result = store.create(issue_id="DEMO-4", issue_body=body, reviewer_role="reviewer",
                          outcomes=outcomes, quality="mergeable", diff_hash=claim.diff_hash,
                          claim_id=claim.claim_id, root=tmp_path, base=BASE_SHA,
                          state_dir=state)
    assert result["proof_id"] == marker.name
    assert ReviewDeduplicator(repository, state).verdict(claim.diff_hash).state == "completed"

    hostile_state = tmp_path / "hostile-state"
    hostile_claim = ReviewDeduplicator(repository, hostile_state).claim(
        diff, coordinates=_review_coordinates(tmp_path),
    )
    hostile_store = AcceptanceProofStore(repository, hostile_state)
    monkeypatch.setattr(ReviewDeduplicator, "_atomic_write", interrupted_terminal)
    with pytest.raises(KeyboardInterrupt):
        hostile_store.create(issue_id="DEMO-4", issue_body=body, reviewer_role="reviewer",
                             outcomes=outcomes, quality="mergeable", diff_hash=hostile_claim.diff_hash,
                             claim_id=hostile_claim.claim_id, root=tmp_path, base=BASE_SHA,
                             state_dir=hostile_state)
    hostile_marker = next(hostile_store.directory.iterdir())
    hostile = json.loads(hostile_marker.read_text())
    hostile["quality"] = "blocked"
    hostile_marker.write_text(json.dumps(hostile))
    monkeypatch.setattr(ReviewDeduplicator, "_atomic_write", original_write)
    with pytest.raises(RoutingConfigError, match="persistante non canonique"):
        hostile_store.create(issue_id="DEMO-4", issue_body=body, reviewer_role="reviewer",
                             outcomes=outcomes, quality="mergeable", diff_hash=hostile_claim.diff_hash,
                             claim_id=hostile_claim.claim_id, root=tmp_path, base=BASE_SHA,
                             state_dir=hostile_state)


def test_recovery_refuses_integral_or_malformed_orphaned_proof(
    monkeypatch, tmp_path,
):
    repository, state, diff = "owner/repo", tmp_path / "state", b"recovered proof diff"
    ledger = ReviewDeduplicator(repository, state)
    coordinates = _review_coordinates(tmp_path)
    original_claim = ledger.claim(diff, coordinates=coordinates)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "9" * 40)
    store = AcceptanceProofStore(repository, state)
    body = "- [ ] recovered contract\n"
    outcomes = [{**item, "verdict": "pass"} for item in acceptance_criteria(body)]
    original_write = ReviewDeduplicator._atomic_write

    def interrupt_first_terminal(self, marker, record):
        if record.get("acceptance_proof_id"):
            raise KeyboardInterrupt()
        return original_write(self, marker, record)

    monkeypatch.setattr(ReviewDeduplicator, "_atomic_write", interrupt_first_terminal)
    with pytest.raises(KeyboardInterrupt):
        store.create(issue_id="DEMO-5", issue_body=body, reviewer_role="reviewer",
                     outcomes=outcomes, quality="mergeable",
                     diff_hash=original_claim.diff_hash, claim_id=original_claim.claim_id,
                     root=tmp_path, base=BASE_SHA, state_dir=state)
    orphan = next(store.directory.iterdir())

    monkeypatch.setattr(ReviewDeduplicator, "_atomic_write", original_write)
    with pytest.raises(RoutingConfigError, match="preuve de review incomplète"):
        ledger.assert_recoverable(
            original_claim.diff_hash, original_claim.generation,
            original_claim.claim_id, coordinates=coordinates,
        )
    with pytest.raises(RoutingConfigError, match="preuve de review incomplète"):
        ledger.recover(
            original_claim.diff_hash, original_claim.generation,
            original_claim.claim_id, "first reviewer interrupted",
            coordinates=coordinates,
        )
    assert ledger.verdict(original_claim.diff_hash).generation == 1

    orphan.write_text("{malformed", encoding="utf-8")
    with pytest.raises(RoutingConfigError, match="JSON invalide"):
        ledger.recover(
            original_claim.diff_hash, original_claim.generation,
            original_claim.claim_id, "first reviewer interrupted",
            coordinates=coordinates,
        )


def test_ac_proof_refuses_hostile_json_and_mixed_verdict(monkeypatch, tmp_path):
    repository, state, diff = "owner/repo", tmp_path / "state", b"proof"
    claim = ReviewDeduplicator(repository, state).claim(
        diff, coordinates=_review_coordinates(tmp_path),
    )
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "c" * 40)
    body = "- [ ] one\n- [ ] two\n"
    outcomes = [{**item, "verdict": "pass"} for item in acceptance_criteria(body)]
    store = AcceptanceProofStore(repository, state)
    result = store.create(issue_id="DEMO-1", issue_body=body, reviewer_role="reviewer",
                          outcomes=outcomes, quality="mergeable", diff_hash=claim.diff_hash,
                          claim_id=claim.claim_id, root=tmp_path, base=BASE_SHA,
                          state_dir=state)
    marker = store.directory / result["proof_id"]
    hostile = json.loads(marker.read_text())
    hostile["unexpected"] = "secret-value"
    marker.write_text(json.dumps(hostile))
    with pytest.raises(RoutingConfigError, match="legacy ou incomplète") as error:
        store.valid_for_merge(
            issue_id="DEMO-1", issue_body=body, head="c" * 40, diff=diff,
            base=BASE_SHA,
        )
    assert "secret-value" not in str(error.value)

    mixed_diff = b"mixed"
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: mixed_diff)
    mixed = [{**item, "verdict": "pass"} for item in acceptance_criteria(body)]
    mixed[1]["verdict"] = "fail"
    mixed_store = AcceptanceProofStore("owner/other", state)
    mixed_claim = ReviewDeduplicator("owner/other", state).claim(
        mixed_diff, coordinates=_review_coordinates(tmp_path),
    )
    mixed_store.create(issue_id="DEMO-1", issue_body=body, reviewer_role="reviewer",
                       outcomes=mixed, quality="blocked", diff_hash=mixed_claim.diff_hash,
                       claim_id=mixed_claim.claim_id, root=tmp_path, base=BASE_SHA,
                       state_dir=state)
    with pytest.raises(RoutingConfigError, match="non mergeable"):
        mixed_store.valid_for_merge(
            issue_id="DEMO-1", issue_body=body, head="c" * 40, diff=mixed_diff,
            base=BASE_SHA,
        )


def test_read_review_cli_emits_the_verified_bytes(monkeypatch, capsys):
    monkeypatch.setattr(
        "foundry.routing.claimed_review_diff",
        lambda *_args, consume, **_kwargs: consume(b"exact verified diff\n"),
    )

    main([
        "read-review",
        "--diff-hash", "a" * 64,
        "--claim-id", "b" * 64,
        "--repository", "owner/repo",
        "--root", "/tmp/review-root",
        "--base", BASE_SHA,
    ])

    assert capsys.readouterr().out == "exact verified diff\n"


def test_read_review_cli_holds_claim_until_stdout_write_and_flush(monkeypatch, tmp_path):
    """Recovery cannot replace the owner before the CLI has emitted its bytes."""
    repository, state, diff = "owner/repo", tmp_path / "state", b"stdout-locked diff"
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    ledger = ReviewDeduplicator(repository, state)
    coordinates = _review_coordinates(tmp_path)
    claim = ledger.claim(diff, coordinates=coordinates)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    write_started, release_write, flushed = Event(), Event(), Event()
    recovery_started, recovery_finished = Event(), Event()
    result: dict[str, object] = {}

    class BlockingBuffer:
        def __init__(self):
            self.written: list[bytes] = []

        def write(self, value: bytes) -> int:
            self.written.append(value)
            write_started.set()
            assert release_write.wait(timeout=2)
            return len(value)

        def flush(self) -> None:
            flushed.set()

    buffer = BlockingBuffer()

    class BlockingStdout:
        def __init__(self):
            self.buffer = buffer

    monkeypatch.setattr("foundry.routing.sys.stdout", BlockingStdout())

    def read_owner():
        try:
            main([
                "read-review", "--repository", repository,
                "--diff-hash", claim.diff_hash, "--claim-id", claim.claim_id,
                "--root", str(tmp_path), "--base", BASE_SHA,
            ])
        except BaseException as exc:  # pragma: no cover - asserted after synchronization
            result["read_error"] = exc

    def recover_owner():
        recovery_started.set()
        result["recovered"] = ledger.recover(
            claim.diff_hash, claim.generation, claim.claim_id,
            "the reviewer stopped", coordinates=coordinates,
        )
        result["flushed_before_recovery"] = flushed.is_set()
        recovery_finished.set()

    reader = Thread(target=read_owner)
    reader.start()
    assert write_started.wait(timeout=2)
    recovery = Thread(target=recover_owner)
    recovery.start()
    assert recovery_started.wait(timeout=2)
    assert not recovery_finished.wait(timeout=0.1)

    release_write.set()
    reader.join(timeout=2)
    recovery.join(timeout=2)
    assert not reader.is_alive()
    assert not recovery.is_alive()
    assert "read_error" not in result
    assert buffer.written == [diff]
    assert flushed.is_set()
    assert result["flushed_before_recovery"] is True
    assert result["recovered"].generation == 2
