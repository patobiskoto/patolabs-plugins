"""Make the in-repo Foundry package importable without installing it."""
import ast
import sys
from pathlib import Path

import pytest
from _pytest.mark.expression import Scanner, expression


TOOLING = Path(__file__).resolve().parents[1] / "tooling"
sys.path.insert(0, str(TOOLING))


# FOUNDRY-127: benchmark-campaign and POC tests (FOUNDRY-ADR-0019) are tagged with the
# opt-in `benchmark_campaign` marker (see pytest.ini) purely from this conftest, never
# by editing the test files themselves. Several of these files are hashed byte-for-byte
# as frozen "v1 baseline" evidence by other campaign harnesses under benchmarks/; a
# `pytestmark` line added to their source would change those bytes and fail the very
# campaigns it is meant to gate. Tagging happens at collection time instead.
#
# Membership is decided per file (or per test, for files that mix product and
# benchmark-campaign coverage) on what the test exercises — loading a script under
# `benchmarks/foundry-NN/`, or exercising a module that only benchmark tests import
# (`foundry.benchmark_evidence`, `foundry.measurement_harness[_v2]`,
# `foundry.local_benchmark`) — never on filename alone. In particular
# `test_campaign_*.py` test `foundry.campaign_coordinator` and friends, the production
# Epic-campaign orchestration used by `command_runtime`/`command_worker`; despite the
# name, that is product code and stays out of this list.
_CAMPAIGN_FILES = frozenset(
    {
        "test_agent_lifecycle_canary_v1.py",
        "test_benchmark_evidence.py",
        "test_benchmark_evidence_v2.py",
        "test_benchmark_evidence_v3.py",
        "test_benchmark_evidence_v4.py",
        "test_benchmark_evidence_v4_results.py",
        "test_benchmark_post_run_v4.py",
        "test_benchmark_protocol_v1.py",
        "test_codex_lifecycle_probe_execution_v1.py",
        "test_codex_lifecycle_probe_v1.py",
        "test_codex_native_lifecycle_diagnostic_v1.py",
        "test_context_broker_native_runner_v2.py",
        "test_context_pack_materialization_v2.py",
        "test_f59_context_protocol_v2.py",
        "test_historical_context_corpus_v1.py",
        "test_hybrid_retrieval_v1.py",
        "test_local_benchmark.py",
        "test_local_requalification_phase_a_v2.py",
        "test_local_requalification_phase_b_v1.py",
        "test_local_requalification_phase_b_v2.py",
        "test_local_requalification_phase_b_v2_results.py",
        "test_measurement_harness.py",
        "test_measurement_harness_v2.py",
        "test_model_effort_context_pilot_execution_v2.py",
        "test_model_effort_context_pilot_v1.py",
        "test_model_effort_context_pilot_v2.py",
        "test_repomap_ranking_poc_v1.py",
        "test_repomix_final_packer_poc_v1.py",
        "test_replay_bundles.py",
        "test_serena_structural_poc_v1.py",
        "test_tool_hygiene_v1.py",
    }
)

# Individual tests inside otherwise-product files that exercise benchmark-only
# tooling (e.g. `foundry.local_benchmark`, never imported by product runtime code).
_CAMPAIGN_TESTS = frozenset(
    {
        (
            "test_local_scout.py",
            "test_benchmark_fixture_requires_cold_warm_and_loaded_repetitions_and_reporting",
        ),
    }
)


def _benchmark_campaign_requested(config: pytest.Config) -> bool:
    """Return whether this invocation explicitly asks to collect the replay suite."""
    markexpr = config.getoption("markexpr") or ""
    if not markexpr:
        return False

    parsed = expression(Scanner(markexpr))

    def has_positive_term(node: ast.AST, *, negated: bool = False) -> bool:
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return has_positive_term(node.operand, negated=not negated)
        if isinstance(node, ast.Name):
            return node.id == "$benchmark_campaign" and not negated
        return any(
            has_positive_term(child, negated=negated)
            for child in ast.iter_child_nodes(node)
        )

    return has_positive_term(parsed.body)


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Keep dependency-heavy frozen replay modules out of default collection.

    ``pytest_collection_modifyitems`` runs only after pytest has imported a test
    module.  Some frozen replay modules import optional benchmark dependencies at
    module scope, so deselecting their items there is too late for the default CI
    environment.  This hook runs before that import.  Mixed product/benchmark files
    remain collectable and are filtered item-by-item below.
    """
    if (
        collection_path.name in _CAMPAIGN_FILES
        and not _benchmark_campaign_requested(config)
    ):
        return True
    return None


def pytest_collection_modifyitems(config, items):
    """Tag, then deselect by default, benchmark-campaign/POC tests.

    FOUNDRY-127: tagging happens here (not via `pytestmark` in the files
    themselves — see the module docstring above) and applies the `benchmark_campaign`
    marker declared in pytest.ini. The marker is opt-in: any invocation whose `-m`
    expression does not positively request `benchmark_campaign` (e.g. the default
    `-m "not integration"` or `-m "not benchmark_campaign"`) deselects tagged items
    automatically, so the default suite
    stays fast without requiring every caller to spell out `and not
    benchmark_campaign`. A run that explicitly requests the marker — e.g. `pytest -m
    benchmark_campaign tests` — is left untouched; pytest's own `-m` selection
    already governs it in that case.
    """
    for item in items:
        file_name = item.path.name
        test_name = getattr(item, "originalname", None) or item.name
        if file_name in _CAMPAIGN_FILES or (file_name, test_name) in _CAMPAIGN_TESTS:
            item.add_marker(pytest.mark.benchmark_campaign)

    if _benchmark_campaign_requested(config):
        return
    remaining = []
    deselected = []
    for item in items:
        if item.get_closest_marker("benchmark_campaign") is not None:
            deselected.append(item)
        else:
            remaining.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = remaining
