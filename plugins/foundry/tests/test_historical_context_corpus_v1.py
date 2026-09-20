"""Offline contract tests for the FOUNDRY-51 historical context corpus."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-51"


def _module():
    spec = importlib.util.spec_from_file_location(
        "foundry51_historical_context", ROOT / "validate-corpus-v1.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORPUS = _module()


def test_historical_corpus_is_frozen_stratified_and_explicitly_incomplete():
    frozen = CORPUS.validate_freeze(ROOT)
    corpus = frozen["corpus"]
    assert len(corpus["cases"]) == 7
    assert frozen["high_estimate_cases"] == 5
    assert frozen["candidate_count"] >= 28
    assert {case["issue_type"] for case in corpus["cases"]} == {"feature", "fix", "chore"}
    assert {candidate["label"] for case in corpus["cases"] for candidate in case["candidates"]} == {
        "required", "useful", "noise",
    }
    assert corpus["provenance"]["ground_truth"] == "incomplete_mixed_review"


def test_missing_revision_fails_closed_before_any_experiment():
    frozen = CORPUS.validate_freeze(ROOT)
    case = copy.deepcopy(frozen["corpus"]["cases"][0])
    case.pop("revision")
    with pytest.raises(CORPUS.HistoricalCorpusError, match="corpus case schema"):
        CORPUS._validate_case(case, ROOT.parents[3], CORPUS._load(ROOT, "historical-issues-v1.json"))


def test_incoherent_label_and_forbidden_evidence_fail_closed():
    frozen = CORPUS.validate_freeze(ROOT)
    case = copy.deepcopy(frozen["corpus"]["cases"][0])
    case["candidates"][0]["label"] = "ambiguous"
    with pytest.raises(CORPUS.HistoricalCorpusError, match="label invalid"):
        CORPUS._validate_case(case, ROOT.parents[3], CORPUS._load(ROOT, "historical-issues-v1.json"))

    corpus = copy.deepcopy(frozen["corpus"])
    corpus["cases"][0]["candidates"][0]["prompt"] = "forbidden"
    with pytest.raises(CORPUS.HistoricalCorpusError, match="candidate schema"):
        CORPUS._validate_case(corpus["cases"][0], ROOT.parents[3], CORPUS._load(ROOT, "historical-issues-v1.json"))


def test_incomplete_freeze_manifest_fails_closed(tmp_path):
    for name in CORPUS.REQUIRED_FILES:
        (tmp_path / name).write_bytes((ROOT / name).read_bytes())
    manifest = json.loads((ROOT / "freeze-manifest-v1.json").read_text(encoding="utf-8"))
    manifest["files"].pop("README.md")
    (tmp_path / "freeze-manifest-v1.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CORPUS.HistoricalCorpusError, match="freeze manifest is incomplete"):
        CORPUS.validate_freeze(tmp_path, repository=ROOT.parents[3])


def test_criterion_provenance_universe_and_symbol_fragments_fail_closed():
    frozen = CORPUS.validate_freeze(ROOT)
    snapshot = CORPUS._load(ROOT, "historical-issues-v1.json")
    case = copy.deepcopy(frozen["corpus"]["cases"][0])
    case["success_criteria"]["criterion_ids"].pop()
    with pytest.raises(CORPUS.HistoricalCorpusError, match="every frozen tracker criterion"):
        CORPUS._validate_case(case, ROOT.parents[3], snapshot)

    case = copy.deepcopy(frozen["corpus"]["cases"][0])
    case["candidate_universe"]["member_ids"].pop()
    with pytest.raises(CORPUS.HistoricalCorpusError, match="candidate universe membership"):
        CORPUS._validate_case(case, ROOT.parents[3], snapshot)

    case = copy.deepcopy(frozen["corpus"]["cases"][0])
    case["candidates"][0]["locator"] = "plugins/foundry/tooling/foundry/routing_facades.py#missing_fragment"
    with pytest.raises(CORPUS.HistoricalCorpusError, match="symbol fragment"):
        CORPUS._validate_case(case, ROOT.parents[3], snapshot)


def test_selection_audit_cannot_be_rebound_after_freeze():
    frozen = CORPUS.validate_freeze(ROOT)
    audit = CORPUS._load(ROOT, "selection-audit-v1.json")
    pool = CORPUS._validate_selection_pool(CORPUS._load(ROOT, "selection-pool-v1.json"), ROOT.parents[3])
    audit["selected"][0]["issue"] = "FOUNDRY-999"
    with pytest.raises(CORPUS.HistoricalCorpusError, match="deterministic selection"):
        CORPUS._validate_selection_audit(audit, frozen["corpus"], pool)


def test_selection_pool_and_deterministic_rule_cannot_be_rebound():
    frozen = CORPUS.validate_freeze(ROOT)
    pool = CORPUS._load(ROOT, "selection-pool-v1.json")
    pool["entries"].pop()
    with pytest.raises(CORPUS.HistoricalCorpusError, match="selection pool population"):
        CORPUS._validate_selection_pool(pool, ROOT.parents[3])

    pool = CORPUS._load(ROOT, "selection-pool-v1.json")
    pool["entries"][1]["matched_families"] = ["large_logs"]
    pool["entries"][1]["exclusion_reasons"].pop("large_logs")
    with pytest.raises(CORPUS.HistoricalCorpusError, match="objective classification"):
        CORPUS._validate_selection_pool(pool, ROOT.parents[3])

    pool = CORPUS._validate_selection_pool(CORPUS._load(ROOT, "selection-pool-v1.json"), ROOT.parents[3])
    audit = CORPUS._load(ROOT, "selection-audit-v1.json")
    audit["selected"][0]["issue"] = "FOUNDRY-25"
    with pytest.raises(CORPUS.HistoricalCorpusError, match="deterministic selection"):
        CORPUS._validate_selection_audit(audit, frozen["corpus"], pool)


def test_selection_pool_lower_bound_is_repository_origin_not_curator_revision():
    pool = CORPUS._load(ROOT, "selection-pool-v1.json")
    assert pool["window"] == {
        "first_parent_end_inclusive": "6e28842f046fb3b16b7d7e817a00e87f314c5e08",
        "lower_bound_rule": "repository_origin_to_end_inclusive",
        "subject_pattern": "[FOUNDRY-N] (feat|fix|chore)... (#PR)",
    }
    assert len(pool["entries"]) == 33
    assert [entry["issue"] for entry in pool["entries"][:4]] == [
        "FOUNDRY-12", "FOUNDRY-5", "FOUNDRY-6", "FOUNDRY-7",
    ]

    pool["window"]["first_parent_start_exclusive"] = pool["entries"][2]["revision"]
    with pytest.raises(CORPUS.HistoricalCorpusError, match="selection pool window"):
        CORPUS._validate_selection_pool(pool, ROOT.parents[3])


def test_objective_git_rules_prove_invalid_oracle_large_logs_and_multiple_eligibility():
    pool = CORPUS._validate_selection_pool(
        CORPUS._load(ROOT, "selection-pool-v1.json"), ROOT.parents[3]
    )
    corpus = CORPUS.validate_freeze(ROOT)["corpus"]
    by_family = {case["family"]: case for case in corpus["cases"]}
    broken = by_family["broken_tests"]
    logs = by_family["large_logs"]

    assert broken["issue"] == "FOUNDRY-40"
    broken_facts = CORPUS._objective_git_facts(ROOT.parents[3], broken["revision"])
    assert {
        "test_path": "plugins/foundry/tests/test_agent_routing.py",
        "parent_value": "claude-opus-5",
        "current_value": "opus",
        "implementation_path": "plugins/foundry/tooling/foundry/routing_facades.py",
    } in broken_facts["invalid_test_oracle_corrections"]
    matched, _ = CORPUS._objective_classification(broken_facts)
    assert "broken_tests" in matched
    assert logs["issue"] == "FOUNDRY-35"
    log_facts = CORPUS._objective_git_facts(ROOT.parents[3], logs["revision"])
    assert log_facts["largest_added_artifact_lines"] >= 10_000
    assert any(
        row["locator"] == "plugins/foundry/benchmarks/foundry-35/raw-evidence-v4.json"
        for row in logs["candidates"]
    )

    localized = [
        entry for entry in pool["entries"]
        if "small_localized_bug" in entry["matched_families"]
    ]
    assert [entry["issue"] for entry in localized][:2] == ["FOUNDRY-15", "FOUNDRY-25"]


def test_test_churn_without_a_demonstrably_invalid_oracle_cannot_qualify():
    facts = CORPUS._objective_git_facts(
        ROOT.parents[3], "4a9ee8172bd1f1a04c02ea5d3eaacc1049c93e3a"
    )
    facts["test_added_lines"] = 100_000
    facts["test_deleted_lines"] = 100_000
    facts["invalid_test_oracle_corrections"] = []

    matched, exclusions = CORPUS._objective_classification(facts)

    assert "broken_tests" not in matched
    assert exclusions["broken_tests"] == "demonstrably_invalid_test_oracle_correction"


def test_broken_test_case_binds_the_corrected_oracle_to_the_frozen_criterion():
    frozen = CORPUS.validate_freeze(ROOT)
    snapshot = CORPUS._load(ROOT, "historical-issues-v1.json")
    broken = copy.deepcopy(next(
        case for case in frozen["corpus"]["cases"] if case["family"] == "broken_tests"
    ))
    tests = next(row for row in broken["candidates"] if row["id"] == "C40-useful")
    tests["evidence"]["criterion"] = "AC-40-9"

    with pytest.raises(CORPUS.HistoricalCorpusError, match="broken-tests criterion binding"):
        CORPUS._validate_case(broken, ROOT.parents[3], snapshot)


def test_manual_eligibility_cannot_choose_an_arbitrary_case():
    pool = CORPUS._load(ROOT, "selection-pool-v1.json")
    pool["entries"][1]["eligibility"] = "eligible"
    with pytest.raises(CORPUS.HistoricalCorpusError, match="selection pool entry schema"):
        CORPUS._validate_selection_pool(pool, ROOT.parents[3])

    validated = CORPUS._validate_selection_pool(
        CORPUS._load(ROOT, "selection-pool-v1.json"), ROOT.parents[3]
    )
    assert validated["selected"]["small_localized_bug"]["issue"] == "FOUNDRY-15"
    assert validated["eligible"]["small_localized_bug"] == ["FOUNDRY-15", "FOUNDRY-25"]


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repository, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def _downstream_repo(tmp_path: Path) -> tuple[Path, Path, str, str]:
    repository = tmp_path / "repository"
    artifacts = repository / "benchmarks" / "foundry-51"
    artifacts.mkdir(parents=True)
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "config", "user.name", "Foundry tests")
    files = {}
    for name in CORPUS.REQUIRED_FILES:
        (artifacts / name).write_text(f"frozen {name}\n", encoding="utf-8")
        files[name] = hashlib.sha256((artifacts / name).read_bytes()).hexdigest()
    (artifacts / "freeze-manifest-v1.json").write_text(json.dumps({
        "artifact": "foundry.historical_context.freeze_manifest", "version": 1,
        "files": files,
    }), encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "freeze")
    freeze = _git(repository, "rev-parse", "HEAD")
    (repository / "result.txt").write_text("result\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "result")
    return repository, artifacts, freeze, _git(repository, "rev-parse", "HEAD")


def test_downstream_result_requires_freeze_ancestor_and_digest_revalidation(tmp_path):
    repository, artifacts, freeze, result = _downstream_repo(tmp_path)
    CORPUS.validate_downstream_freeze(freeze, result, repository, artifact_directory=artifacts)

    _git(repository, "checkout", "-qb", "forged", freeze)
    manifest = artifacts / "freeze-manifest-v1.json"
    forged = json.loads(manifest.read_text(encoding="utf-8"))
    forged["files"]["corpus-v1.json"] = "0" * 64
    manifest.write_text(json.dumps(forged), encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "forged manifest")
    forged_freeze = _git(repository, "rev-parse", "HEAD")
    (repository / "forged-result.txt").write_text("result\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "forged result")
    with pytest.raises(CORPUS.HistoricalCorpusError, match="digest is forged"):
        CORPUS.validate_downstream_freeze(forged_freeze, _git(repository, "rev-parse", "HEAD"), repository, artifact_directory=artifacts)

    _git(repository, "checkout", "-q", result)
    (artifacts / "corpus-v1.json").write_text('{"changed":true}\n', encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "changed artifact")
    with pytest.raises(CORPUS.HistoricalCorpusError, match="artifact changed downstream"):
        CORPUS.validate_downstream_freeze(freeze, _git(repository, "rev-parse", "HEAD"), repository, artifact_directory=artifacts)

    with pytest.raises(CORPUS.HistoricalCorpusError, match="descend from freeze"):
        CORPUS.validate_downstream_freeze(result, freeze, repository, artifact_directory=artifacts)
    with pytest.raises(CORPUS.HistoricalCorpusError, match="distinct commit"):
        CORPUS.validate_downstream_freeze(freeze, freeze, repository, artifact_directory=artifacts)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_downstream_manifest_requires_exact_frozen_file_set(tmp_path, mutation):
    repository, artifacts, freeze, _ = _downstream_repo(tmp_path)
    _git(repository, "checkout", "-qb", mutation, freeze)
    manifest = artifacts / "freeze-manifest-v1.json"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if mutation == "missing":
        value["files"].pop("README.md")
    else:
        (artifacts / "extra.txt").write_text("extra\n", encoding="utf-8")
        value["files"]["extra.txt"] = hashlib.sha256(
            (artifacts / "extra.txt").read_bytes()
        ).hexdigest()
    manifest.write_text(json.dumps(value), encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", f"{mutation} freeze")
    bad_freeze = _git(repository, "rev-parse", "HEAD")
    (repository / "result.txt").write_text("result\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "result")
    with pytest.raises(CORPUS.HistoricalCorpusError, match="exact required file set"):
        CORPUS.validate_downstream_freeze(
            bad_freeze, _git(repository, "rev-parse", "HEAD"), repository,
            artifact_directory=artifacts,
        )


def test_explicit_acceptance_tests_are_required_not_useful():
    frozen = CORPUS.validate_freeze(ROOT)
    snapshot = CORPUS._load(ROOT, "historical-issues-v1.json")
    for case, candidate_id in (
        (next(case for case in frozen["corpus"]["cases"] if case["issue"] == "FOUNDRY-40"), "C40-useful"),
        (next(case for case in frozen["corpus"]["cases"] if case["issue"] == "FOUNDRY-49"), "C49-useful"),
        (next(case for case in frozen["corpus"]["cases"] if case["issue"] == "FOUNDRY-31"), "C31-useful"),
        (next(case for case in frozen["corpus"]["cases"] if case["issue"] == "FOUNDRY-15"), "C15-useful"),
        (next(case for case in frozen["corpus"]["cases"] if case["issue"] == "FOUNDRY-32"), "C32-useful"),
    ):
        candidate = next(row for row in case["candidates"] if row["id"] == candidate_id)
        assert candidate["label"] == "required"

    invalid = copy.deepcopy(next(
        case for case in frozen["corpus"]["cases"] if case["issue"] == "FOUNDRY-32"
    ))
    candidate = next(row for row in invalid["candidates"] if row["id"] == "C32-useful")
    candidate["label"] = "useful"
    candidate["evidence"]["criterion"] = "AC-32-6"
    candidate["label_history"] = [{
        "role": "curator", "label": "useful", "rationale": "Convenient rebind."
    }]
    candidate["disagreement"] = {"status": "not_observed", "arbitration": "not_required"}
    with pytest.raises(CORPUS.HistoricalCorpusError, match="required artifact cannot be useful"):
        CORPUS._validate_case(invalid, ROOT.parents[3], snapshot)

    superseded_f33_snapshot = {"issues": {"FOUNDRY-33": {"criteria": [{
        "id": "AC-33-6",
        "text": "Doctor reports local state without model call or sensitive disclosure.",
    }]}}}
    assert CORPUS._artifact_required_by_issue(
        superseded_f33_snapshot,
        "FOUNDRY-33",
        "plugins/foundry/tooling/foundry/doctor.py",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("repository", None), ("repository", "not a repository"),
     ("history_cutoff", None), ("history_cutoff", "2026-99-99")],
)
def test_provenance_repository_and_history_cutoff_fail_closed(field, value):
    corpus = CORPUS._load(ROOT, "corpus-v1.json")
    corpus["provenance"][field] = value
    with pytest.raises(CORPUS.HistoricalCorpusError, match="corpus provenance"):
        CORPUS._validate_corpus_schema(corpus)


def test_case_estimate_type_and_complexity_are_snapshot_and_git_derived():
    frozen = CORPUS.validate_freeze(ROOT)
    snapshot = CORPUS._load(ROOT, "historical-issues-v1.json")
    case = copy.deepcopy(next(
        row for row in frozen["corpus"]["cases"] if row["issue"] == "FOUNDRY-40"
    ))
    assert snapshot["issues"]["FOUNDRY-40"]["estimate"] == 5
    assert snapshot["issues"]["FOUNDRY-40"]["issue_type"] == "fix"

    changed_snapshot = copy.deepcopy(snapshot)
    changed_snapshot["issues"]["FOUNDRY-40"]["estimate"] = 6
    with pytest.raises(CORPUS.HistoricalCorpusError, match="tracker issue estimate invalid"):
        CORPUS._validate_tracker_snapshot(changed_snapshot)

    changed = copy.deepcopy(case)
    changed["estimate"] = 6
    with pytest.raises(CORPUS.HistoricalCorpusError, match="historical estimate differs"):
        CORPUS._validate_case(changed, ROOT.parents[3], snapshot)

    changed = copy.deepcopy(case)
    changed["issue_type"] = "feature"
    with pytest.raises(CORPUS.HistoricalCorpusError, match="historical issue type differs"):
        CORPUS._validate_case(changed, ROOT.parents[3], snapshot)

    changed = copy.deepcopy(case)
    changed["complexity"] = "high"
    with pytest.raises(CORPUS.HistoricalCorpusError, match="deterministic complexity differs"):
        CORPUS._validate_case(changed, ROOT.parents[3], snapshot)


def test_f54_is_frozen_as_isolated_unstarted_repomix_diagnostic():
    protocol = CORPUS.validate_freeze(ROOT)["protocol"]
    assert protocol["f54_recommendation"] == {
        "decision": "GO",
        "qualification": "diagnostic_only",
        "candidate": "Repomix",
        "comparison": "independent_candidate_comparison",
        "prerequisite": "final_FOUNDRY-51_freeze",
        "runtime_scope": "isolated_no_runtime",
        "execution_state": "not_started",
    }

    protocol["f54_recommendation"]["decision"] = "ADJUST"
    with pytest.raises(CORPUS.HistoricalCorpusError, match="F54 recommendation differs"):
        CORPUS._validate_protocol(protocol)


def test_candidate_labels_cannot_authorize_repository_wide_or_promotion_claims():
    protocol = CORPUS.validate_freeze(ROOT)["protocol"]
    limits = protocol["metrics"]["claim_limits"]
    assert limits == {
        "recall_precision_scope": "declared_candidate_universe_only",
        "repository_wide_recall_precision": "forbidden",
        "promotion_evidence": "forbidden",
    }

    limits["promotion_evidence"] = "allowed"
    with pytest.raises(CORPUS.HistoricalCorpusError, match="metric claim limits differ"):
        CORPUS._validate_protocol(protocol)


def test_constitution_cost_has_auditable_time_and_invocation_counts():
    protocol = CORPUS.validate_freeze(ROOT)["protocol"]
    constitution = protocol["metrics"]["constitution"]
    assert constitution["wall_clock"]["elapsed_seconds_lower_bound"] > 0
    assert constitution["wall_clock"]["elapsed_seconds_upper_bound"] is None
    assert constitution["agent_invocations"] == {
        "curation_or_correction": 4,
        "independent_review": 3,
        "total": 7,
    }
    assert constitution["provider_input_tokens"] is None
    assert constitution["provider_output_tokens"] is None
    assert constitution["provider_cost_usd"] is None


def test_review_state_and_unknown_privacy_fields_fail_closed():
    frozen = CORPUS.validate_freeze(ROOT)
    snapshot = CORPUS._load(ROOT, "historical-issues-v1.json")
    invalid = copy.deepcopy(frozen["corpus"]["cases"][0])
    candidate = invalid["candidates"][0]
    candidate["label_history"].append({"role": "independent_reviewer", "label": "noise", "rationale": "Different assessment."})
    candidate["disagreement"] = {"status": "not_observed", "arbitration": "not_required"}
    with pytest.raises(CORPUS.HistoricalCorpusError, match="review disagreement requires an arbiter"):
        CORPUS._validate_case(invalid, ROOT.parents[3], snapshot)

    invalid = copy.deepcopy(frozen["corpus"]["cases"][0])
    candidate = next(row for row in invalid["candidates"] if row["id"] == "C40-noise")
    candidate["label_history"].append({"role": "independent_reviewer", "label": "noise", "rationale": "Same assessment."})
    candidate["label"] = "useful"
    with pytest.raises(CORPUS.HistoricalCorpusError, match="review agreement/final label differs"):
        CORPUS._validate_case(invalid, ROOT.parents[3], snapshot)

    invalid = copy.deepcopy(frozen["corpus"]["cases"][0])
    invalid["candidates"][0]["responses"] = ["plural unknown field"]
    with pytest.raises(CORPUS.HistoricalCorpusError, match="candidate schema differs"):
        CORPUS._validate_case(invalid, ROOT.parents[3], snapshot)
