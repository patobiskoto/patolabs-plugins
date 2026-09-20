"""Regression coverage for FOUNDRY-127's pre-import benchmark isolation."""

from pathlib import Path
from types import SimpleNamespace

from conftest import pytest_ignore_collect


def _config(markexpr: str):
    return SimpleNamespace(getoption=lambda option: markexpr if option == "markexpr" else None)


def test_default_collection_ignores_dependency_heavy_benchmark_module_before_import():
    assert (
        pytest_ignore_collect(
            Path("tests/test_context_broker_native_runner_v2.py"),
            _config("not integration"),
        )
        is True
    )


def test_explicit_benchmark_replay_collects_the_same_module():
    assert (
        pytest_ignore_collect(
            Path("tests/test_context_broker_native_runner_v2.py"),
            _config("benchmark_campaign"),
        )
        is None
    )


def test_negated_benchmark_marker_keeps_dependency_heavy_module_unimported():
    assert (
        pytest_ignore_collect(
            Path("tests/test_context_broker_native_runner_v2.py"),
            _config("not benchmark_campaign"),
        )
        is True
    )


def test_inclusive_benchmark_marker_expression_collects_the_same_module():
    assert (
        pytest_ignore_collect(
            Path("tests/test_context_broker_native_runner_v2.py"),
            _config("benchmark_campaign or not integration"),
        )
        is None
    )


def test_product_campaign_orchestration_module_is_never_ignored():
    assert (
        pytest_ignore_collect(
            Path("tests/test_campaign_coordinator.py"),
            _config("not integration"),
        )
        is None
    )
