"""Make the in-repo Foundry package importable without installing it."""
import ast
import json
import os
import pwd
import sys
from pathlib import Path

import pytest
from _pytest.mark.expression import Scanner, expression


TOOLING = Path(__file__).resolve().parents[1] / "tooling"
sys.path.insert(0, str(TOOLING))

_CONFORMANCE_MANIFEST_PATH = Path(__file__).resolve().parent / "fixtures" / "tracker-conformance-v1.json"
_CONFORMANCE_MANIFEST = json.loads(_CONFORMANCE_MANIFEST_PATH.read_text(encoding="utf-8"))
_TRACKER_CONFORMANCE_TESTS = frozenset(
    tuple(case["test"].split("::", 1)) for case in _CONFORMANCE_MANIFEST["cases"]
)


# PAT-104: the suite must never read or write the maintainer's REAL Foundry state
# directory. Every state/config resolver in the tooling ends at `~` (`registry.data_dir`,
# `config.default_config_path`, `config.install_marker_path`, the legacy dev env file) or
# at one of the env overrides below, so one autouse fixture redirects `HOME` and clears
# the overrides; an audit hook then makes the real path unreachable even by accident
# (e.g. a module imported with `from module import *` running outside its own fixture).
_REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)  # not `~`: immune to HOME changes
_REAL_STATE_ROOTS = tuple(
    {str(_REAL_HOME / ".config" / "foundry"),
     str(_REAL_HOME.resolve() / ".config" / "foundry"),
     str(_REAL_HOME / ".config" / "orfeo-poc"),
     str(_REAL_HOME.resolve() / ".config" / "orfeo-poc")}
)
# FOUNDRY_DATA is cleared, not set: its presence enables telemetry, so setting it would
# change behaviour under test. With it unset, `data_dir()` follows the redirected HOME.
_STATE_ENV_OVERRIDES = ("FOUNDRY_DATA", "FOUNDRY_CONFIG", "FOUNDRY_EXECUTION_RECEIPTS_DIR")
_GUARDED_AUDIT_EVENTS = frozenset({
    "open", "os.mkdir", "os.rename", "os.remove", "os.rmdir", "os.listdir",
    "os.scandir", "os.chmod", "os.truncate", "os.utime", "os.symlink", "os.link",
    "shutil.copyfile", "shutil.copytree", "shutil.rmtree", "shutil.move",
})
_guard_active = False


def _is_real_state_path(candidate) -> bool:
    if isinstance(candidate, int) or candidate is None:
        return False
    try:
        text = os.path.abspath(os.fsdecode(candidate))
    except (TypeError, ValueError):
        return False
    return any(text == root or text.startswith(root + os.sep) for root in _REAL_STATE_ROOTS)


def _audit_real_state(event: str, args: tuple) -> None:
    if not _guard_active or event not in _GUARDED_AUDIT_EVENTS or not args:
        return
    touched = args[:2] if event in {"os.rename", "os.symlink", "os.link", "shutil.copyfile",
                                    "shutil.copytree", "shutil.move"} else args[:1]
    for candidate in touched:
        if _is_real_state_path(candidate):
            raise RuntimeError(
                f"PAT-104 guard: test touched the REAL Foundry state ({event} "
                f"{os.fsdecode(candidate)!r}); isolate it under tmp_path"
            )


sys.addaudithook(_audit_real_state)


def assert_state_resolvers_isolated(root: Path) -> None:
    """Fail loudly unless every state/config resolver lands under ``root``."""
    from foundry import config, registry

    resolved = {
        "registry.data_dir": Path(registry.data_dir()),
        "registry.path": Path(registry.path()),
        "config.default_config_path": config.default_config_path(),
        "config.install_marker_path": config.install_marker_path(),
        **{f"config._dev_files[{i}]": p for i, p in enumerate(config._dev_files())},
    }
    root = root.resolve()
    for name, value in resolved.items():
        if not value.resolve().is_relative_to(root) or _is_real_state_path(value):
            raise AssertionError(f"PAT-104: {name} resolves outside the test sandbox: {value}")


@pytest.fixture(autouse=True)
def _isolate_foundry_state(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
):
    """Point every Foundry state directory at a per-test temporary home.

    Function scope (not session): tests write real state (intents, proofs, dedup
    ledgers) and must not see each other's leftovers. Explicitly opt-in integration
    tests retain their own configured environment, as for the repository marker.
    """
    global _guard_active
    if request.node.get_closest_marker("integration") is not None:
        yield
        return
    home = tmp_path_factory.mktemp("foundry-home")
    monkeypatch.setenv("HOME", str(home))
    for name in _STATE_ENV_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    assert_state_resolvers_isolated(home)
    _guard_active = True
    try:
        yield home
    finally:
        _guard_active = False


@pytest.fixture(autouse=True)
def _no_linear_retry_sleep(monkeypatch: pytest.MonkeyPatch):
    """PAT-98: Linear read retries never really sleep in tests; delays are recorded."""
    from foundry.trackers import linear

    delays: list[float] = []
    monkeypatch.setattr(linear, "_sleep", delays.append)
    yield delays


@pytest.fixture(autouse=True)
def _isolate_versioned_repository_marker(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, tmp_path: Path,
):
    """Keep unit tests outside this checkout's executable tracker marker.

    Repository-binding tests explicitly ``chdir`` into a temporary Git checkout.
    Explicitly opt-in integration tests retain their own configured environment.
    The ordinary unit suite must never inherit the versioned
    ``.foundry/tracker.json`` of the checkout that happens to run it.
    """
    if request.node.get_closest_marker("integration") is not None:
        return
    monkeypatch.chdir(tmp_path)


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
        if (file_name, test_name) in _TRACKER_CONFORMANCE_TESTS:
            item.add_marker(pytest.mark.tracker_conformance)
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
