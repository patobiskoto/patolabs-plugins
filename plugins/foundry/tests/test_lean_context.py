import pytest
from types import MappingProxyType

from foundry import local_code, local_scout
from foundry.lean_context import (
    BASELINE_V1, LEAN_V1, ClaudeCacheSession, ContextCapture, ContextPolicyError,
    ContextPacket, build_host_context, capture_context, cloud_fragments, build_packet,
    inspect_materialized, LeanContextPolicy, SourceCap,
)


def _capture(tmp_path):
    paths = {
        "tree.py": "tree", "symbols.py": "symbols", "imports.py": "imports",
        "references.py": "references", "rg.py": "needle", "multi.py": "multi",
    }
    for path, content in paths.items():
        (tmp_path / path).write_text(content * 10, encoding="utf-8")
    manifest = local_code.build_code_manifest(tmp_path, {
        "rg": ("rg.py",), "tree": ("tree.py", "multi.py"),
        "symbols": ("symbols.py", "multi.py"), "imports": ("imports.py", "multi.py"),
        "references": ("references.py", "multi.py"),
    })
    bundle = local_code.materialize_code_bundle(
        tmp_path, manifest, local_code.CodeSelection(tuple(sorted(paths)), ("needle",)),
    )
    return capture_context(
        diff=local_scout.capture_diff("diff --git a/a b/a\n+line\n"),
        logs=local_scout.capture_logs("log" * 2000),
        tests=local_scout.capture_tests("test" * 2000),
        manifest=manifest, code=bundle,
    )


def test_fixed_sources_are_wrapper_derived_deterministic_and_bounded(tmp_path):
    capture = _capture(tmp_path)
    first, second = build_packet("lean-v1", capture), build_packet("lean-v1", capture)
    assert first == second
    assert set(first.source_counts) == {"diff", "tree", "symbols", "imports", "references", "rg", "tests", "logs"}
    assert all(first.source_counts[source] >= 1 for source in first.source_counts)
    assert first.metrics["sent_bytes"]["value"] <= LEAN_V1.max_packet_bytes
    assert first.metrics["collected_bytes"]["value"] >= first.metrics["sent_bytes"]["value"]


def test_forged_provenance_or_capture_never_becomes_packet_input(tmp_path):
    with pytest.raises(ContextPolicyError):
        build_packet("lean-v1", ContextCapture((), 0))
    forged = local_scout.DiffCapture("0" * 64, "0" * 64, 1, 1, False, (), "diff")
    with pytest.raises(ContextPolicyError):
        capture_context(diff=forged)
    assert build_packet("lean-v1", _capture(tmp_path)).evidence_ids


def test_cloud_view_is_exactly_bounded_and_packet_cannot_bypass_raw(tmp_path):
    capture = _capture(tmp_path)
    packet = build_packet(LEAN_V1, capture)
    fragments = cloud_fragments(packet)
    assert tuple(fragment.evidence_id for fragment in fragments) == packet.evidence_ids
    assert sum(len(fragment.fragment) for fragment in fragments) == packet.metrics["sent_bytes"]["value"]
    assert all(len(fragment.fragment) <= LEAN_V1.source_caps[fragment.source].max_bytes for fragment in fragments)
    raw = inspect_materialized(capture, packet.evidence_ids)
    assert any(len(record.raw) > len(fragment.fragment) for record, fragment in zip(raw, fragments))
    with pytest.raises(ContextPolicyError):
        cloud_fragments(ContextPacket(packet.policy, packet.evidence_ids, packet.source_counts, packet.metrics, packet.truncated))


def test_telemetry_projection_has_no_raw_content_path_or_provenance(tmp_path):
    packet = build_packet("lean-v1", _capture(tmp_path))
    projection = repr(packet.telemetry_projection())
    assert "needle" not in projection
    assert str(tmp_path) not in projection
    assert "wrapper" not in projection


def test_claude_effort_is_constant_and_cache_metrics_are_separate_and_nullable():
    session = ClaudeCacheSession("medium").observe(
        effort="medium", cache_read_tokens=12, cache_creation_tokens=None,
    )
    assert session.metrics()["cache_read_tokens"]["value"] == 12
    assert session.metrics()["cache_creation_tokens"]["value"] is None
    with pytest.raises(ContextPolicyError):
        session.observe(effort="high", cache_read_tokens=1, cache_creation_tokens=2)


@pytest.mark.parametrize("selected_policy", (BASELINE_V1, LEAN_V1))
def test_source_byte_caps_are_aggregate_and_policies_are_frozen(tmp_path, selected_policy):
    packet = build_packet(selected_policy, _capture(tmp_path))
    fragments = cloud_fragments(packet)
    for source, cap in selected_policy.source_caps.items():
        assert sum(len(item.fragment) for item in fragments if item.source == source) <= cap.max_bytes
    with pytest.raises(TypeError):
        selected_policy.source_caps["logs"] = SourceCap(99, 999999)
    forged = LeanContextPolicy("lean", 1, MappingProxyType({
        source: SourceCap(99, 999999) for source in selected_policy.source_caps
    }), 999999)
    with pytest.raises(ContextPolicyError):
        build_packet(forged, _capture(tmp_path))


def test_equal_distinct_and_spoofable_public_policies_are_never_packet_authority(tmp_path):
    capture = _capture(tmp_path)
    caller_caps = {source: cap for source, cap in LEAN_V1.source_caps.items()}
    equal_but_distinct = LeanContextPolicy("lean", 1, caller_caps, 12 * 1024)
    assert equal_but_distinct == LEAN_V1
    assert equal_but_distinct is not LEAN_V1
    assert equal_but_distinct.source_caps is not caller_caps
    with pytest.raises(TypeError):
        equal_but_distinct.source_caps["logs"] = SourceCap(99, 999999)
    caller_caps["logs"] = SourceCap(99, 999999)
    assert equal_but_distinct.source_caps["logs"] == LEAN_V1.source_caps["logs"]
    with pytest.raises(ContextPolicyError):
        build_packet(equal_but_distinct, capture)

    class SpoofedIdentifier(str):
        pass

    with pytest.raises(ContextPolicyError):
        build_packet(SpoofedIdentifier("lean-v1"), capture)
    assert build_packet("lean-v1", capture).policy == "lean-v1"


@pytest.mark.parametrize(
    "selected_policy",
    (LEAN_V1, BASELINE_V1),
)
def test_cloud_fragments_remain_valid_utf8_when_source_cap_straddles_codepoint(
    selected_policy,
):
    byte_cap = selected_policy.source_caps["logs"].max_bytes
    capture = capture_context(logs=local_scout.capture_logs("€" * (byte_cap // 3 + 10)))
    packet = build_packet(selected_policy, capture)
    fragments = cloud_fragments(packet)
    payload = b"".join(item.fragment for item in fragments)

    assert packet.truncated is True
    assert len(payload) == byte_cap - byte_cap % 3
    assert len(payload) <= byte_cap
    assert payload.decode("utf-8") == "€" * (len(payload) // 3)
    assert packet.metrics["sent_bytes"]["value"] == len(payload)


def test_multisignal_manifest_expands_in_canonical_order_without_id_collision(tmp_path):
    capture = _capture(tmp_path)
    multi = [record for record in capture.records if record.raw.startswith(b"multi")]
    assert [record.source for record in multi] == ["tree", "symbols", "imports", "references"]
    assert len({record.evidence_id for record in multi}) == 4


def test_bundle_must_match_exact_manifest_and_fresh_snapshot(tmp_path):
    paths = {"one.py": "one", "two.py": "two"}
    for path, content in paths.items():
        (tmp_path / path).write_text(content, encoding="utf-8")
    first = local_code.build_code_manifest(tmp_path, {"tree": ("one.py",)})
    bundle = local_code.materialize_code_bundle(
        tmp_path, first, local_code.CodeSelection(("one.py",), ()),
    )
    crossed = local_code.build_code_manifest(tmp_path, {"symbols": ("one.py",)})
    with pytest.raises(ContextPolicyError):
        capture_context(manifest=crossed, code=bundle)
    (tmp_path / "one.py").write_text("changed", encoding="utf-8")
    with pytest.raises(ContextPolicyError):
        capture_context(manifest=first, code=bundle)


def test_projection_and_nested_data_are_registered_immutable_and_sanitized(tmp_path):
    packet = build_packet("lean-v1", _capture(tmp_path))
    projection = packet.telemetry_projection()
    with pytest.raises(TypeError):
        packet.source_counts["diff"] = 999
    with pytest.raises(TypeError):
        packet.metrics["sent_bytes"]["value"] = 999
    with pytest.raises(TypeError):
        projection["secret"] = "TOP_SECRET"
    forged = ContextPacket("lean-v1", (), {"TOP_SECRET": 1}, {
        "path": {"value": 1, "provenance": "client_observed"},
    }, False)
    with pytest.raises(ContextPolicyError):
        forged.telemetry_projection()


def test_shared_host_facade_is_offline_and_preserves_metric_availability(monkeypatch, tmp_path):
    invocations = []

    def forbidden_invocation(*args, **kwargs):
        invocations.append((args, kwargs))
        raise AssertionError("host contract must remain offline")

    monkeypatch.setattr(local_scout, "request_local_completion", forbidden_invocation)
    capture = _capture(tmp_path)
    claude = build_host_context(
        "claude", "baseline-v1", capture,
        claude_cache=ClaudeCacheSession("medium").observe(
            effort="medium", cache_read_tokens=12, cache_creation_tokens=None,
        ),
    )
    codex = build_host_context("codex", BASELINE_V1, capture)

    assert invocations == []
    assert claude.packet == codex.packet
    assert cloud_fragments(claude.packet) == cloud_fragments(codex.packet)
    assert claude.cache_metrics["cache_read_tokens"] == {
        "value": 12, "provenance": "client_observed",
    }
    assert claude.cache_metrics["cache_creation_tokens"] == {
        "value": None, "provenance": "unavailable",
    }
    assert codex.cache_metrics == {
        "cache_read_tokens": {"value": None, "provenance": "unavailable"},
        "cache_creation_tokens": {"value": None, "provenance": "unavailable"},
    }
    with pytest.raises(ContextPolicyError):
        build_host_context(
            "codex", "baseline-v1", capture,
            claude_cache=ClaudeCacheSession("medium"),
        )
