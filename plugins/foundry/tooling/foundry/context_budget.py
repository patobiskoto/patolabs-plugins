"""Experimental, provider-orthogonal context budget and pack metadata contract.

This module deliberately has no retrieval, ranking, routing, gate, merge, or
provider invocation capability.  Callers supply already-selected metadata; this
contract only validates it and makes accounting (including overflow) observable.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType
from typing import Mapping


class ContextContractError(ValueError):
    """Raised when externally supplied pack metadata is malformed."""


PACK_VERSIONS = ("context-pack-v1",)
STRATEGIES = ("first-fit", "round-robin", "future-f52", "future-f53", "future-f54")
PROVENANCES = ("caller", "f44_packet", "retriever")
REASONS = ("caller_selected", "f44_adapted", "removed_by_caller")
RETRIEVER_SOURCES = ("f44", "local_scout", "manual", "external")
MAX_CONTEXT_WINDOW_TOKENS = 1_000_000
MAX_CONTEXT_ELEMENTS = 256


def _non_negative(
    value: object, name: str, *, nullable: bool = False, maximum: int | None = None,
) -> int | None:
    if value is None and nullable:
        return None
    if type(value) is not int or value < 0:
        raise ContextContractError(f"{name} must be a non-negative integer")
    if maximum is not None and value > maximum:
        raise ContextContractError(f"{name} must not exceed {maximum}")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ContextContractError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _metric(value: int | None) -> Mapping[str, object]:
    return MappingProxyType({"value": value, "provenance": "observed" if value is not None else "unavailable"})


@dataclass(frozen=True)
class ContextBudget:
    """Declared window partition; overflow is reported, never silently repaired."""

    window_tokens: int
    reserved_output_tokens: int
    system_role_tokens: int
    tools_tokens: int
    task_tokens: int
    working_state_tokens: int
    margin_tokens: int

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            _non_negative(value, name, maximum=MAX_CONTEXT_WINDOW_TOKENS)

    @property
    def requested_tokens(self) -> int:
        return (
            self.reserved_output_tokens + self.system_role_tokens + self.tools_tokens
            + self.task_tokens + self.working_state_tokens + self.margin_tokens
        )

    @property
    def input_budget_tokens(self) -> int:
        return self.injectable_capacity_tokens

    @property
    def reserved_partition_tokens(self) -> int:
        return self.requested_tokens

    @property
    def injectable_capacity_tokens(self) -> int:
        """Unclipped capacity left after every declared partition is reserved."""
        return self.window_tokens - self.reserved_partition_tokens

    @property
    def requested_input_tokens(self) -> int:
        return self.system_role_tokens + self.tools_tokens + self.task_tokens + self.working_state_tokens

    @property
    def overflow_tokens(self) -> int:
        return max(0, -self.injectable_capacity_tokens)

    def view(self) -> Mapping[str, int]:
        """Content-free declared budget view suitable for packets or telemetry."""
        return MappingProxyType({
            "window_tokens": self.window_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "system_role_tokens": self.system_role_tokens,
            "tools_tokens": self.tools_tokens,
            "task_tokens": self.task_tokens,
            "working_state_tokens": self.working_state_tokens,
            "margin_tokens": self.margin_tokens,
            "requested_tokens": self.requested_tokens,
            "requested_input_tokens": self.requested_input_tokens,
            "reserved_partition_tokens": self.reserved_partition_tokens,
            "injectable_capacity_tokens": self.injectable_capacity_tokens,
            "input_budget_tokens": self.input_budget_tokens,
            "overflow_tokens": self.overflow_tokens,
        })


@dataclass(frozen=True)
class ContextElement:
    """Metadata for one externally selected context element; never its content."""

    provenance: str
    digest: str
    reason: str
    retriever_source: str
    size_tokens: int
    truncated_tokens: int = 0
    score: float | None = None
    removed: bool = False

    def __post_init__(self) -> None:
        for name, allowed in (("provenance", PROVENANCES), ("reason", REASONS),
                              ("retriever_source", RETRIEVER_SOURCES)):
            if getattr(self, name) not in allowed:
                raise ContextContractError(f"{name} must be a controlled vocabulary value")
        _digest(self.digest, "digest")
        _non_negative(self.size_tokens, "size_tokens", maximum=MAX_CONTEXT_WINDOW_TOKENS)
        _non_negative(self.truncated_tokens, "truncated_tokens", maximum=MAX_CONTEXT_WINDOW_TOKENS)
        if self.truncated_tokens > self.size_tokens:
            raise ContextContractError("truncated_tokens cannot exceed size_tokens")
        if self.score is not None and (
            type(self.score) not in (int, float) or isinstance(self.score, bool) or not math.isfinite(self.score)
        ):
            raise ContextContractError("score must be finite numeric or null")
        if type(self.removed) is not bool:
            raise ContextContractError("removed must be boolean")

    @property
    def injected_tokens(self) -> int:
        return 0 if self.removed else self.size_tokens - self.truncated_tokens


@dataclass(frozen=True)
class CacheObservation:
    """Nullable observed cache usage; absence is never coerced to zero."""

    read_tokens: int | None = None
    write_tokens: int | None = None

    def __post_init__(self) -> None:
        _non_negative(self.read_tokens, "cache read_tokens", nullable=True)
        _non_negative(self.write_tokens, "cache write_tokens", nullable=True)

    def view(self) -> Mapping[str, Mapping[str, object]]:
        return MappingProxyType({"read_tokens": _metric(self.read_tokens), "write_tokens": _metric(self.write_tokens)})


@dataclass(frozen=True)
class ContextPack:
    """Versioned snapshot of supplied context metadata, not a context broker."""

    version: str
    snapshot_digest: str
    budget: ContextBudget
    elements: tuple[ContextElement, ...]
    strategy: str
    cache: CacheObservation = CacheObservation()

    def __post_init__(self) -> None:
        if self.version not in PACK_VERSIONS:
            raise ContextContractError("version must be a controlled contract version")
        _digest(self.snapshot_digest, "snapshot_digest")
        if type(self.budget) is not ContextBudget or type(self.cache) is not CacheObservation:
            raise ContextContractError("budget and cache must be contract values")
        if self.strategy not in STRATEGIES:
            raise ContextContractError("strategy must be a controlled neutral label")
        if not isinstance(self.elements, tuple) or any(type(item) is not ContextElement for item in self.elements):
            raise ContextContractError("elements must be ContextElement values")
        if len(self.elements) > MAX_CONTEXT_ELEMENTS:
            raise ContextContractError(f"elements must contain at most {MAX_CONTEXT_ELEMENTS} values")
        if sum(item.size_tokens for item in self.elements) > MAX_CONTEXT_WINDOW_TOKENS:
            raise ContextContractError(
                f"retrieved context must not exceed {MAX_CONTEXT_WINDOW_TOKENS} tokens"
            )

    @property
    def retrieved_tokens(self) -> int:
        return sum(item.size_tokens for item in self.elements)

    @property
    def injected_tokens(self) -> int:
        return sum(item.injected_tokens for item in self.elements)

    @property
    def truncation_count(self) -> int:
        return sum(item.truncated_tokens > 0 for item in self.elements)

    @property
    def truncated_tokens(self) -> int:
        return sum(item.truncated_tokens for item in self.elements)

    @property
    def removal_count(self) -> int:
        return sum(item.removed for item in self.elements)

    @property
    def removed_tokens(self) -> int:
        return sum(item.size_tokens for item in self.elements if item.removed)

    @property
    def element_overflow_tokens(self) -> int:
        # Reservation overflow and injected-context overflow are distinct signals.
        # A negative capacity is already reported by ContextBudget.overflow_tokens;
        # no injected element means no injected-context overflow.
        return max(0, self.injected_tokens - max(0, self.budget.injectable_capacity_tokens))

    def inspection(self) -> Mapping[str, object]:
        """Immutable pack API for trusted inspection; unlike telemetry it retains the snapshot digest."""
        return MappingProxyType({
            "version": self.version,
            "snapshot_digest": self.snapshot_digest,
            "budget": self.budget.view(),
            "elements": self.elements,
            "strategy": self.strategy,
            "cache": self.cache.view(),
        })

    def telemetry_projection(self) -> Mapping[str, object]:
        """Content-free aggregate view; element content and identifiers never escape."""
        source_mix: dict[str, int] = {}
        for element in self.elements:
            source_mix[element.retriever_source] = source_mix.get(element.retriever_source, 0) + 1
        return MappingProxyType({
            "budget": self.budget.view(),
            "requested_budget_tokens": _metric(self.budget.requested_tokens),
            "used_budget_tokens": _metric(self.injected_tokens),
            "retrieved_tokens": _metric(self.retrieved_tokens),
            "injected_tokens": _metric(self.injected_tokens),
            "source_mix": MappingProxyType(dict(source_mix)),
            "truncations": MappingProxyType({"count": self.truncation_count, "tokens": self.truncated_tokens}),
            "removals": MappingProxyType({"count": self.removal_count, "tokens": self.removed_tokens}),
            "overflows": MappingProxyType({
                "budget_tokens": self.budget.overflow_tokens,
                "injected_context_tokens": self.element_overflow_tokens,
            }),
            "cache": self.cache.view(),
        })


def f44_snapshot_digest(policy: str, evidence_ids: tuple[str, ...]) -> str:
    """Stable metadata digest for F44 packet identifiers, without reading fragments."""
    if not isinstance(policy, str) or not policy or not isinstance(evidence_ids, tuple) or any(not isinstance(item, str) for item in evidence_ids):
        raise ContextContractError("F44 packet metadata is invalid")
    canonical = json.dumps(
        {"evidence_ids": evidence_ids, "policy": policy},
        ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
