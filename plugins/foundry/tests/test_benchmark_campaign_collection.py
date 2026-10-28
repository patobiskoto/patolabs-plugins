"""Regression coverage for FOUNDRY-127's pre-import benchmark isolation."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from _pytest.mark.expression import Scanner, expression as pytest_expression

import conftest
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


@pytest.mark.parametrize("wrap", [lambda node: node, lambda node: (node, frozenset())], ids=[
    "bare node (pytest before 9.2)", "(node, names) tuple (pytest 9.2)",
])
@pytest.mark.parametrize(("markexpr", "expected"), [
    ("benchmark_campaign", None), ("not benchmark_campaign", True),
])
def test_collection_handles_both_return_shapes_of_the_pytest_expression_parser(
    monkeypatch, wrap, markexpr, expected,
):
    """Whatever pytest is installed, both return shapes of the private parser are exercised."""
    parsed = pytest_expression(Scanner(markexpr))
    node = parsed[0] if isinstance(parsed, tuple) else parsed
    monkeypatch.setattr(conftest, "expression", lambda scanner: wrap(node))
    assert pytest_ignore_collect(
        Path("tests/test_context_broker_native_runner_v2.py"), _config(markexpr),
    ) is expected
