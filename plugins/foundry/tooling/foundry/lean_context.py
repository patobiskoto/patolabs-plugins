"""Versioned, deterministic and privacy-safe context packet policy.

This is an observation-only boundary.  It neither resolves routes nor invokes a
provider.  Raw evidence stays in the caller-owned materialized store; the packet and
its telemetry projection contain only controlled evidence ids and aggregate counts.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import threading
import weakref
from types import MappingProxyType
from typing import Mapping

from foundry import local_code, local_scout


SOURCES = ("diff", "tree", "symbols", "imports", "references", "rg", "tests", "logs")
_SOURCE_ORDER = {name: index for index, name in enumerate(SOURCES)}
_CAPTURE_REGISTRY: dict[int, tuple[weakref.ReferenceType[object], str]] = {}
_PACKET_REGISTRY: dict[int, tuple[weakref.ReferenceType[object], str, tuple["CloudFragment", ...]]] = {}
_REGISTRY_LOCK = threading.RLock()


class ContextPolicyError(ValueError):
    """Raised before unbounded or non-materialized evidence can enter a packet."""


def _metric(value: int | None) -> dict[str, object]:
    return MappingProxyType({"value": value,
        "provenance": "client_observed" if value is not None else "unavailable"})


@dataclass(frozen=True)
class SourceCap:
    max_items: int
    max_bytes: int

    def __post_init__(self) -> None:
        if type(self.max_items) is not int or type(self.max_bytes) is not int or self.max_items < 1 or self.max_bytes < 1:
            raise ContextPolicyError("source caps must be positive integers")


@dataclass(frozen=True)
class LeanContextPolicy:
    name: str
    version: int
    source_caps: Mapping[str, SourceCap]
    max_packet_bytes: int

    def __post_init__(self) -> None:
        if self.name not in {"baseline", "lean"} or type(self.version) is not int or self.version < 1:
            raise ContextPolicyError("policy name or version is invalid")
        try:
            source_caps = dict(self.source_caps)
        except (TypeError, ValueError):
            raise ContextPolicyError("policy must define every allowed source in canonical order") from None
        if tuple(source_caps) != SOURCES or any(type(cap) is not SourceCap for cap in source_caps.values()):
            raise ContextPolicyError("policy must define every allowed source in canonical order")
        if type(self.max_packet_bytes) is not int or self.max_packet_bytes < 1:
            raise ContextPolicyError("packet cap is invalid")
        # A public value may be useful for inspection, but it must not retain a
        # caller-owned mutable/spoofable mapping.  Packet admission below remains
        # stricter and accepts only the shipped canonical object identities.
        object.__setattr__(self, "source_caps", MappingProxyType(source_caps))

    @property
    def identifier(self) -> str:
        return f"{self.name}-v{self.version}"


BASELINE_V1 = LeanContextPolicy("baseline", 1, MappingProxyType({
    source: SourceCap(8, 8 * 1024) for source in SOURCES
}), 48 * 1024)
LEAN_V1 = LeanContextPolicy("lean", 1, MappingProxyType({
    source: SourceCap(4, 2 * 1024) for source in SOURCES
}), 12 * 1024)
_REGISTERED_POLICIES = (BASELINE_V1, LEAN_V1)
_POLICY_BY_IDENTIFIER = MappingProxyType({
    registered.identifier: registered for registered in _REGISTERED_POLICIES
})
POLICIES = _POLICY_BY_IDENTIFIER


@dataclass(frozen=True)
class _EvidenceRecord:
    """Private wrapper record; public construction cannot make it trusted."""

    evidence_id: str
    source: str
    raw: bytes


@dataclass(frozen=True)
class ContextCapture:
    """A registered composition of existing wrapper-owned captures/bundles."""

    records: tuple[_EvidenceRecord, ...]
    files_read: int


@dataclass(frozen=True)
class CloudFragment:
    """The only raw bytes a cloud caller may obtain for a context packet."""

    evidence_id: str
    source: str
    fragment: bytes


def _signature(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def _register(registry: dict, value: object, signature: str, *extra: object) -> object:
    identifier = id(value)
    def cleanup(reference: weakref.ReferenceType[object]) -> None:
        with _REGISTRY_LOCK:
            current = registry.get(identifier)
            if current is not None and current[0] is reference:
                registry.pop(identifier, None)
    reference = weakref.ref(value, cleanup)
    with _REGISTRY_LOCK:
        registry[identifier] = (reference, signature, *extra)
    return value


def _registered_capture(capture: object) -> ContextCapture:
    if type(capture) is not ContextCapture:
        raise ContextPolicyError("context capture is not wrapper-created")
    with _REGISTRY_LOCK:
        registered = _CAPTURE_REGISTRY.get(id(capture))
    if registered is None or registered[0]() is not capture or registered[1] != _signature(capture):
        raise ContextPolicyError("context capture is not wrapper-created")
    return capture


def capture_context(*, diff: local_scout.DiffCapture | None = None,
                    logs: local_scout.DiffCapture | None = None,
                    tests: local_scout.DiffCapture | None = None,
                    manifest: local_code.CodeManifest | None = None,
                    code: local_code.CodeEvidenceBundle | None = None) -> ContextCapture:
    """Compose only captures/bundles whose existing provenance verifier accepts them."""
    records: list[_EvidenceRecord] = []
    for expected, capture in (("diff", diff), ("logs", logs), ("tests", tests)):
        if capture is None:
            continue
        try:
            validated = local_scout._validated_capture(capture)
        except local_scout.LocalScoutError as exc:
            raise ContextPolicyError("context input is not wrapper-materialized") from exc
        if validated.mode != expected:
            raise ContextPolicyError("context capture has the wrong controlled source")
        records.extend(_EvidenceRecord(item.evidence_id, expected, item.raw_excerpt.encode("utf-8"))
                       for item in validated.evidence)
    files_read = 0
    if code is not None:
        if manifest is None:
            raise ContextPolicyError("code evidence requires its wrapper-created manifest")
        try:
            bundle, _root = local_code._validated_bundle(code)
            validated_manifest, manifest_root = local_code._validated_manifest(manifest)
        except local_scout.LocalScoutError as exc:
            raise ContextPolicyError("context code input is not wrapper-materialized") from exc
        if manifest_root != _root or bundle.manifest_sha256 != validated_manifest.sha256:
            raise ContextPolicyError("code evidence and manifest roots differ")
        try:
            local_code.ensure_code_bundle_current(bundle, manifest_root)
        except local_scout.LocalScoutError as exc:
            raise ContextPolicyError("context code evidence is stale") from exc
        signal_by_path = {entry.path: entry.signals for entry in validated_manifest.entries}
        for item in bundle.evidence:
            signals = signal_by_path.get(item.path, ())
            controlled_sources = ("rg",) if item.source == "rg" else tuple(
                signal for signal in SOURCES if signal in signals
            )
            if not controlled_sources:
                raise ContextPolicyError("code evidence lacks a controlled source")
            for source in controlled_sources:
                evidence_id = "ctx_" + hashlib.sha256(
                    f"{item.evidence_id}\0{source}".encode("ascii")
                ).hexdigest()[:24]
                records.append(_EvidenceRecord(evidence_id, source, item.raw_excerpt.encode("utf-8")))
        files_read = len(bundle.selected_paths)
    if not records or len({item.evidence_id for item in records}) != len(records):
        raise ContextPolicyError("context capture has no unique materialized evidence")
    capture = ContextCapture(tuple(records), files_read)
    return _register(_CAPTURE_REGISTRY, capture, _signature(capture))


def inspect_materialized(capture: ContextCapture, evidence_ids: tuple[str, ...]) -> tuple[_EvidenceRecord, ...]:
    """Trusted inspection only; packets never expose this capability or these bytes."""
    capture = _registered_capture(capture)
    records = {record.evidence_id: record for record in capture.records}
    try:
        return tuple(records[item] for item in evidence_ids)
    except KeyError as exc:
        raise ContextPolicyError("inspection references unknown materialized evidence") from exc


@dataclass(frozen=True)
class ContextPacket:
    policy: str
    evidence_ids: tuple[str, ...]
    source_counts: Mapping[str, int]
    metrics: Mapping[str, object]
    truncated: bool

    def telemetry_projection(self) -> dict[str, object]:
        """The only packet representation suitable for local telemetry."""
        _registered_packet(self)
        return MappingProxyType({"policy": self.policy, "evidence_count": len(self.evidence_ids),
            "source_counts": MappingProxyType(dict(self.source_counts)),
            "metrics": MappingProxyType({name: MappingProxyType(dict(value))
                for name, value in self.metrics.items()}), "truncated": self.truncated})


def policy(identifier: str) -> LeanContextPolicy:
    if type(identifier) is not str:
        raise ContextPolicyError("unknown context policy")
    try:
        return _POLICY_BY_IDENTIFIER[identifier]
    except KeyError as exc:
        raise ContextPolicyError("unknown context policy") from exc


def _canonical_policy(value: object) -> LeanContextPolicy:
    if type(value) is str:
        return policy(value)
    if type(value) is LeanContextPolicy:
        for registered in _REGISTERED_POLICIES:
            if value is registered:
                return registered
    raise ContextPolicyError("packet inputs are invalid")


def _registered_packet(packet: object) -> ContextPacket:
    if type(packet) is not ContextPacket:
        raise ContextPolicyError("context packet is invalid")
    with _REGISTRY_LOCK:
        registered = _PACKET_REGISTRY.get(id(packet))
    if registered is None or registered[0]() is not packet or registered[1] != _signature(packet):
        raise ContextPolicyError("context packet is not wrapper-created")
    return packet


def _utf8_prefix(raw: bytes, cap: int) -> bytes:
    """Return a byte-bounded UTF-8 prefix without splitting a code point."""
    # Every record originated as strict UTF-8.  Ignoring only an incomplete suffix
    # preserves the exact byte prefix through the last complete code point.
    bounded = raw[:cap]
    return bounded.decode("utf-8", errors="ignore").encode("utf-8")


def build_packet(policy_value: LeanContextPolicy | str, capture: ContextCapture) -> ContextPacket:
    """Preselect deterministically using every allowed signal class and fixed caps."""
    selected_policy = _canonical_policy(policy_value)
    capture = _registered_capture(capture)
    records = sorted(capture.records, key=lambda item: (_SOURCE_ORDER[item.source], item.evidence_id))
    counts = {source: 0 for source in SOURCES}
    source_bytes = {source: 0 for source in SOURCES}
    collected_bytes = sum(len(record.raw) for record in records)
    files_read = capture.files_read
    packet_bytes = 0
    ids: list[str] = []
    fragments: list[CloudFragment] = []
    truncated = False
    for record in records:
        cap = selected_policy.source_caps[record.source]
        if counts[record.source] >= cap.max_items:
            truncated = True
            continue
        remaining_source = cap.max_bytes - source_bytes[record.source]
        if remaining_source <= 0:
            truncated = True
            continue
        bounded = _utf8_prefix(record.raw, remaining_source)
        if len(record.raw) > len(bounded) or packet_bytes + len(bounded) > selected_policy.max_packet_bytes:
            truncated = True
        remaining = selected_policy.max_packet_bytes - packet_bytes
        if remaining <= 0:
            continue
        sent = _utf8_prefix(bounded, remaining)
        if not sent:
            continue
        ids.append(record.evidence_id)
        fragments.append(CloudFragment(record.evidence_id, record.source, sent))
        counts[record.source] += 1
        source_bytes[record.source] += len(sent)
        packet_bytes += len(sent)
    compression = 0 if collected_bytes == 0 else (packet_bytes * 10000) // collected_bytes
    packet = ContextPacket(selected_policy.identifier, tuple(ids), MappingProxyType(counts), MappingProxyType({
        "collected_bytes": _metric(collected_bytes), "sent_bytes": _metric(packet_bytes),
        "files_read": _metric(files_read), "compression_basis_points": _metric(compression),
    }), truncated)
    return _register(_PACKET_REGISTRY, packet, _signature(packet), tuple(fragments))


def cloud_fragments(packet: ContextPacket) -> tuple[CloudFragment, ...]:
    """Return exactly the registered, per-source/global-capped fragments for cloud use."""
    _registered_packet(packet)
    with _REGISTRY_LOCK:
        registered = _PACKET_REGISTRY[id(packet)]
    return registered[2]


@dataclass(frozen=True)
class ClaudeCacheSession:
    """Observation-only cache accounting; effort may not vary inside a session."""

    effort: str
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.effort not in {"low", "medium", "high", "max"}:
            raise ContextPolicyError("Claude effort is invalid")
        for value in (self.cache_read_tokens, self.cache_creation_tokens):
            if value is not None and (type(value) is not int or value < 0):
                raise ContextPolicyError("cache metric is invalid")

    def observe(self, *, effort: str, cache_read_tokens: int | None, cache_creation_tokens: int | None) -> "ClaudeCacheSession":
        if effort != self.effort:
            raise ContextPolicyError("Claude effort must remain constant in a cacheable session")
        return ClaudeCacheSession(effort, cache_read_tokens, cache_creation_tokens)

    def metrics(self) -> dict[str, dict[str, object]]:
        return {"cache_read_tokens": _metric(self.cache_read_tokens),
                "cache_creation_tokens": _metric(self.cache_creation_tokens)}


@dataclass(frozen=True)
class HostContext:
    """Content-free result of applying the shared packet contract for one host."""

    host: str
    packet: ContextPacket
    cache_metrics: Mapping[str, Mapping[str, object]]


def build_host_context(
    host: str,
    policy_value: LeanContextPolicy | str,
    capture: ContextCapture,
    *,
    claude_cache: ClaudeCacheSession | None = None,
) -> HostContext:
    """Apply one host-neutral policy without routing or invoking either host.

    Claude may contribute its separate nullable cache observations.  Codex keeps
    those Claude-specific observations explicitly unavailable; it cannot accept a
    Claude cache session and thereby spoof metric availability.
    """
    if type(host) is not str or host not in {"claude", "codex"}:
        raise ContextPolicyError("context host is invalid")
    if claude_cache is not None and type(claude_cache) is not ClaudeCacheSession:
        raise ContextPolicyError("Claude cache session is invalid")
    if host == "codex" and claude_cache is not None:
        raise ContextPolicyError("Codex cannot consume Claude cache observations")

    packet = build_packet(policy_value, capture)
    observed = claude_cache.metrics() if claude_cache is not None else {
        "cache_read_tokens": _metric(None),
        "cache_creation_tokens": _metric(None),
    }
    cache_metrics = MappingProxyType({
        name: MappingProxyType(dict(metric)) for name, metric in observed.items()
    })
    return HostContext(host, packet, cache_metrics)
