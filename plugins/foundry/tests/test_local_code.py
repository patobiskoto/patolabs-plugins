"""Security and determinism contract for the two-pass local code mapper."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from foundry import local_code, local_scout


class FakeResponse:
    def __init__(self, body: bytes):
        self.status = 200
        self.body = body
        self.offset = 0

    def getheader(self, _name):
        return None

    def read(self, amount):
        chunk = self.body[self.offset:self.offset + amount]
        self.offset += len(chunk)
        return chunk

    def close(self):
        pass


class FakeSocket:
    def settimeout(self, _timeout):
        pass

    def shutdown(self, _how):
        pass


class FakeConnection:
    def __init__(self, provider, record, *args, **kwargs):
        self.provider = provider
        self.record = record
        self.request_args = None
        self.sock = FakeSocket()
        self.record.append(self)

    def request(self, *args, **kwargs):
        self.request_args = (args, kwargs)

    def getresponse(self):
        return FakeResponse(self.provider(self.request_args))

    def close(self):
        pass


class SequenceFactory:
    def __init__(self, *providers):
        self.providers = list(providers)
        self.connections = []

    def __call__(self, *args, **kwargs):
        if not self.providers:
            raise AssertionError("unexpected third local model call")
        return FakeConnection(self.providers.pop(0), self.connections, *args, **kwargs)


def _completion(value) -> bytes:
    content = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.dumps({
        "object": "chat.completion",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
            "logprobs": None,
        }],
    }, separators=(",", ":")).encode()


def _provider(value):
    return lambda _request: _completion(value)


def _request_prompt(connection: FakeConnection) -> str:
    body = connection.request_args[1]["body"]
    return json.loads(body)["messages"][0]["content"]


def _settings(*, request_bytes=24 * 1024):
    return local_scout.LocalScoutSettings(
        enabled=True,
        endpoint=local_scout.LocalEndpoint("127.0.0.1", 11434),
        model="fixture-model",
        limits=local_scout.LocalScoutLimits(max_request_bytes=request_bytes),
    )


@pytest.fixture(autouse=True)
def _isolate_secrets(monkeypatch):
    monkeypatch.setattr(local_scout, "configured_secret_values", lambda: ())


@pytest.fixture
def code_root(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alpha.py").write_text(
        "def alpha():\n    return 'needle'\n", encoding="utf-8",
    )
    (tmp_path / "src" / "beta.py").write_text(
        "from .alpha import alpha\n", encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# fixture\n", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"not text")
    return tmp_path


def _manifest(code_root, signals=None, limits=local_code.CodeMapLimits()):
    return local_code.build_code_manifest(
        code_root,
        signals or {
            "tree": ("src/beta.py", "src/alpha.py", "image.png"),
            "imports": ("src/alpha.py",),
            "git_diff": ("README.md",),
        },
        limits,
    )


def _selection_response(paths=("src/alpha.py",), terms=("needle",)):
    return {"paths": list(paths), "search_terms": list(terms)}


def _selection(code_root, manifest=None, **kwargs):
    manifest = manifest or _manifest(code_root)
    return local_code.CodeSelection(
        tuple(sorted(kwargs.get("paths", ("src/alpha.py",)))),
        tuple(sorted(kwargs.get("terms", ("needle",)))),
    )


def test_manifest_is_sorted_bounded_body_free_and_signal_owned(code_root, monkeypatch):
    monkeypatch.setattr(
        local_code, "_read_relative_file",
        lambda *_args, **_kwargs: pytest.fail("manifest read a file body"),
    )
    manifest = _manifest(code_root)

    assert [item.path for item in manifest.entries] == [
        "README.md", "src/alpha.py", "src/beta.py",
    ]
    assert manifest.entries[1].signals == ("imports", "tree")
    assert "image.png" not in {item.path for item in manifest.entries}
    assert manifest.sha256 == local_code.build_code_manifest(
        code_root, {
            "tree": ("image.png", "src/alpha.py", "src/beta.py"),
            "imports": ("src/alpha.py",),
            "git_diff": ("README.md",),
        },
    ).sha256


def test_manifest_truncation_is_deterministic(code_root):
    limits = local_code.CodeMapLimits(max_manifest_files=2, max_selected_paths=2)
    first = _manifest(code_root, limits=limits)
    second = _manifest(code_root, limits=limits)
    assert first.truncated is True
    assert first.entries == second.entries
    assert first.sha256 == second.sha256


@pytest.mark.parametrize("path", [
    "../outside.py", "/tmp/outside.py", "src\\alpha.py", ".env",
    "config/api-key.json", "keys/id_ed25519", "src/../alpha.py",
])
def test_manifest_rejects_traversal_absolute_sensitive_and_noncanonical_paths(
    code_root, path,
):
    with pytest.raises(local_scout.LocalScoutError) as raised:
        _manifest(code_root, {"tree": (path,)})
    assert raised.value.code == "LOCAL_SCOUT_POLICY_VIOLATION"


def test_manifest_rejects_parent_and_final_symlinks(code_root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("secret", encoding="utf-8")
    (code_root / "linked").symlink_to(outside, target_is_directory=True)
    (code_root / "final.py").symlink_to(outside / "secret.py")
    for path in ("linked/secret.py", "final.py"):
        with pytest.raises(local_scout.LocalScoutError) as raised:
            _manifest(code_root, {"tree": (path,)})
        assert raised.value.code == "LOCAL_SCOUT_POLICY_VIOLATION"


def test_first_call_sees_paths_and_metadata_but_no_file_body(code_root):
    secret_body = "BODY_MUST_NOT_REACH_PASS_ONE"
    (code_root / "src" / "alpha.py").write_text(secret_body, encoding="utf-8")
    factory = SequenceFactory(_provider(_selection_response(terms=())))
    manifest = _manifest(code_root)

    selection = local_code.select_code_paths(
        "find alpha", manifest, _settings(), connection_factory=factory,
    )

    prompt = _request_prompt(factory.connections[0])
    assert selection.paths == ("src/alpha.py",)
    assert "src/alpha.py" in prompt
    assert "size_bytes" in prompt
    assert secret_body not in prompt


@pytest.mark.parametrize(("value", "message"), [
    ({"paths": ["outside.py"], "search_terms": []}, "outside the manifest"),
    ({"paths": ["src/alpha.py", "src/alpha.py"], "search_terms": []}, "bounds"),
    ({"paths": ["src/alpha.py"], "search_terms": ["--glob"]}, "literal term"),
    ({"paths": ["src/alpha.py"], "search_terms": ["x" * 129]}, "literal term"),
    ({"paths": ["src/alpha.py"], "search_terms": ["ok"], "command": "rg"}, "schema"),
])
def test_first_call_rejects_outside_duplicates_options_overlong_and_extra_keys(
    code_root, value, message,
):
    factory = SequenceFactory(_provider(value))
    with pytest.raises(local_scout.LocalScoutError) as raised:
        local_code.select_code_paths(
            "find alpha", _manifest(code_root), _settings(), connection_factory=factory,
        )
    assert raised.value.code == "LOCAL_SCOUT_INVALID_OUTPUT"
    assert message in raised.value.message


def test_literal_search_uses_argv_fixed_string_and_never_executes_shell(
    code_root, monkeypatch,
):
    term = "$(touch pwned)"
    captured = []
    real_popen = subprocess.Popen

    def recording_popen(command, *args, **kwargs):
        captured.append((command, kwargs))
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(local_code.subprocess, "Popen", recording_popen)
    manifest = _manifest(code_root)
    bundle = local_code.materialize_code_bundle(
        code_root, manifest, _selection(code_root, manifest, terms=(term,)),
    )

    assert bundle.search_terms == (term,)
    assert (code_root / "pwned").exists() is False
    command, kwargs = captured[0]
    assert command[:3] == ["rg", "--json", "-F"]
    assert "--" in command and term in command
    assert kwargs.get("shell") is not True


def test_bundle_is_deterministic_wrapper_owned_redacted_and_bounded(
    code_root, monkeypatch,
):
    secret = "portable-secret-value"
    monkeypatch.setattr(local_scout, "configured_secret_values", lambda: (secret,))
    (code_root / "src" / "alpha.py").write_text(
        f"TOKEN={secret}\ndef alpha(): return 'needle'\n", encoding="utf-8",
    )
    manifest = _manifest(code_root)
    selection = _selection(code_root, manifest)
    first = local_code.materialize_code_bundle(code_root, manifest, selection)
    second = local_code.materialize_code_bundle(code_root, manifest, selection)

    assert first == second
    assert first.filtered_bytes <= local_code.CodeMapLimits().max_bundle_bytes
    assert all(secret not in item.raw_excerpt for item in first.evidence)
    assert any("[REDACTED]" in item.raw_excerpt for item in first.evidence)
    forged = replace(first, filtered_bytes=first.filtered_bytes + 1)
    with pytest.raises(local_scout.LocalScoutError) as raised:
        local_code.ensure_code_bundle_current(forged, code_root)
    assert raised.value.code == "LOCAL_SCOUT_POLICY_VIOLATION"


def test_truncation_is_based_on_source_coverage_not_overlapping_excerpt_sum(tmp_path):
    content = "needle " + ("a" * 493) + "\n" + ("b" * 999)
    (tmp_path / "overlap.py").write_text(content, encoding="utf-8")
    manifest = local_code.build_code_manifest(tmp_path, {"tree": ("overlap.py",)})
    bundle = local_code.materialize_code_bundle(
        tmp_path, manifest,
        local_code.CodeSelection(("overlap.py",), ("needle",)),
    )

    assert sum(len(item.raw_excerpt.encode()) for item in bundle.evidence) >= len(
        content.encode(),
    )
    assert bundle.truncated is True


def test_tightened_rg_match_limit_is_global_per_term_across_files(tmp_path):
    paths = []
    for index in range(10):
        path = f"file-{index}.py"
        (tmp_path / path).write_text("prefix\nneedle\n", encoding="utf-8")
        paths.append(path)
    limits = local_code.CodeMapLimits(max_rg_matches_per_term=1)
    manifest = local_code.build_code_manifest(tmp_path, {"tree": tuple(paths)}, limits)
    bundle = local_code.materialize_code_bundle(
        tmp_path, manifest,
        local_code.CodeSelection(tuple(paths), ("needle",)), limits=limits,
    )

    assert len([item for item in bundle.evidence if item.source == "rg"]) == 1
    assert bundle.limits.max_rg_matches_per_term == 1


def test_snapshot_change_before_second_call_is_stale_and_no_request_is_sent(code_root):
    manifest = _manifest(code_root)
    bundle = local_code.materialize_code_bundle(
        code_root, manifest, _selection(code_root, manifest),
    )
    (code_root / "src" / "alpha.py").write_text("changed\n", encoding="utf-8")
    factory = SequenceFactory(_provider({"hypotheses": []}))

    with pytest.raises(local_scout.LocalScoutError) as raised:
        local_code.analyze_code_bundle(
            "find alpha", bundle, _settings(), root=code_root,
            connection_factory=factory,
        )
    assert raised.value.code == "LOCAL_SCOUT_STALE_INPUT"
    assert factory.connections == []


def test_missing_or_forged_second_pass_citation_is_rejected(code_root):
    manifest = _manifest(code_root)
    bundle = local_code.materialize_code_bundle(
        code_root, manifest, _selection(code_root, manifest),
    )
    for evidence_ids in ([], ["code_forged"]):
        factory = SequenceFactory(_provider({
            "hypotheses": [{"summary": "proposal", "evidence_ids": evidence_ids}],
        }))
        with pytest.raises(local_scout.LocalScoutError) as raised:
            local_code.analyze_code_bundle(
                "find alpha", bundle, _settings(), root=code_root,
                connection_factory=factory,
            )
        assert raised.value.code == "LOCAL_SCOUT_INVALID_OUTPUT"


def test_second_pass_summary_is_redacted_before_cloud_packet(code_root, monkeypatch):
    secret = "model-output-secret"
    monkeypatch.setattr(local_scout, "configured_secret_values", lambda: (secret,))
    manifest = _manifest(code_root)
    bundle = local_code.materialize_code_bundle(
        code_root, manifest, _selection(code_root, manifest),
    )
    factory = SequenceFactory(_provider({
        "hypotheses": [{
            "summary": f"TOKEN={secret}",
            "evidence_ids": [bundle.evidence[0].evidence_id],
        }],
    }))
    result = local_code.analyze_code_bundle(
        "find alpha", bundle, _settings(), root=code_root,
        connection_factory=factory,
    )

    packet = local_code.code_cloud_packet(result, root=code_root)
    assert secret not in json.dumps(packet)
    assert packet["hypotheses"][0]["summary"] == "TOKEN=[REDACTED]"


def test_exactly_two_calls_produce_raw_evidence_packet_and_no_verdict(code_root):
    def second_provider(request):
        prompt = json.loads(request[1]["body"])["messages"][0]["content"]
        evidence_id = prompt.split('"evidence_id":"', 1)[1].split('"', 1)[0]
        return _completion({
            "hypotheses": [{
                "summary": "alpha is likely relevant",
                "evidence_ids": [evidence_id],
            }],
        })

    factory = SequenceFactory(
        _provider(_selection_response()), second_provider,
    )
    result = local_code.run_code_map(
        "find alpha", code_root,
        {"tree": ("src/alpha.py", "src/beta.py")},
        _settings(), connection_factory=factory,
    )
    packet = local_code.code_cloud_packet(result, root=code_root)

    assert len(factory.connections) == 2
    assert factory.providers == []
    assert packet["local_calls"] == 2
    assert packet["source"] == "local" and packet["untrusted"] is True
    assert packet["authority"] == "proposal_only_cloud_role_must_judge_raw_evidence"
    evidence = packet["hypotheses"][0]["evidence"][0]
    assert evidence["raw_excerpt"]
    assert evidence["sha256"]
    assert evidence["locator"].startswith("L")
    assert "verdict" not in json.dumps(packet).casefold()


def test_second_pass_prompt_is_bounded_by_shared_request_limit(code_root):
    limits = local_code.CodeMapLimits(max_bundle_bytes=4 * 1024)
    manifest = _manifest(code_root, limits=limits)
    bundle = local_code.materialize_code_bundle(
        code_root, manifest, _selection(code_root, manifest), limits=limits,
    )
    evidence_id = bundle.evidence[0].evidence_id
    factory = SequenceFactory(_provider({
        "hypotheses": [{"summary": "proposal", "evidence_ids": [evidence_id]}],
    }))

    result = local_code.analyze_code_bundle(
        "find alpha", bundle, _settings(), root=code_root, limits=limits,
        connection_factory=factory,
    )

    request = factory.connections[0].request_args[1]["body"]
    assert len(request) <= _settings().limits.max_request_bytes
    assert result.local_calls == 2


def test_maximum_shape_second_pass_stays_inside_shared_request_limit(tmp_path):
    paths = []
    for index in range(10):
        path = f"src/{index:02d}-" + ("x" * 116) + ".py"
        target = tmp_path / path
        target.parent.mkdir(exist_ok=True)
        target.write_text(("evidence line\n" * 400), encoding="utf-8")
        paths.append(path)
    limits = local_code.CodeMapLimits()
    manifest = local_code.build_code_manifest(tmp_path, {"tree": tuple(paths)}, limits)
    selection = local_code.CodeSelection(tuple(paths), tuple(f"term-{i}" for i in range(6)))
    bundle = local_code.materialize_code_bundle(tmp_path, manifest, selection, limits=limits)
    factory = SequenceFactory(_provider({
        "hypotheses": [{
            "summary": "bounded proposal",
            "evidence_ids": [bundle.evidence[0].evidence_id],
        }],
    }))

    local_code.analyze_code_bundle(
        "g" * limits.max_goal_chars, bundle, _settings(), root=tmp_path,
        limits=limits, connection_factory=factory,
    )

    assert len(factory.connections[0].request_args[1]["body"]) <= 24 * 1024


def test_cloud_packet_rejects_a_forged_result(code_root):
    manifest = _manifest(code_root)
    bundle = local_code.materialize_code_bundle(
        code_root, manifest, _selection(code_root, manifest),
    )
    evidence_id = bundle.evidence[0].evidence_id
    factory = SequenceFactory(_provider({
        "hypotheses": [{"summary": "proposal", "evidence_ids": [evidence_id]}],
    }))
    result = local_code.analyze_code_bundle(
        "find alpha", bundle, _settings(), root=code_root,
        connection_factory=factory,
    )

    with pytest.raises(local_scout.LocalScoutError) as raised:
        local_code.code_cloud_packet(replace(result, model="forged"), root=code_root)
    assert raised.value.code == "LOCAL_SCOUT_POLICY_VIOLATION"


def test_signal_file_is_strict_bounded_path_metadata_only(tmp_path):
    signal_file = tmp_path / "signals.json"
    signal_file.write_text(json.dumps({
        "symbols": ["src/a.py"],
        "imports": ["src/b.py"],
        "references": [],
        "rg": ["src/a.py"],
    }), encoding="utf-8")

    assert local_code._read_signal_file(str(signal_file)) == {
        "symbols": ("src/a.py",),
        "imports": ("src/b.py",),
        "references": (),
        "rg": ("src/a.py",),
    }
    signal_file.write_text('{"symbols":["a.py"],"command":"rg"}', encoding="utf-8")
    with pytest.raises(local_scout.LocalScoutError) as raised:
        local_code._read_signal_file(str(signal_file))
    assert raised.value.code == "LOCAL_SCOUT_POLICY_VIOLATION"


def test_shared_launcher_exposes_local_code_without_host_specific_entrypoint():
    launcher = Path(__file__).parents[1] / "tooling" / "foundry_cli.py"
    completed = subprocess.run(
        ["python3", str(launcher), "local-code", "--help"],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0
    assert "--query-file" in completed.stdout
    assert "--signals-file" in completed.stdout
    assert completed.stderr == ""


def test_collect_signals_uses_body_free_argv_and_preserves_extra_signals(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("print('a')\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("print('b')\n", encoding="utf-8")

    signals = local_code.collect_code_signals(
        tmp_path, base="HEAD", extra={"symbols": ("a.py",)},
    )

    assert signals["tree"] == ("a.py",)
    assert signals["git_status"] == ("a.py",)
    assert signals["symbols"] == ("a.py",)
    assert all("print" not in value for values in signals.values() for value in values)


def test_porcelain_status_parser_keeps_destinations_and_skips_rename_copy_sources():
    payload = (
        b" M ordinary.py\0"
        b"R  renamed.py\0ab source with space.py\0"
        b"C  copied.py\0source.py\0"
        b"?? untracked.py\0"
    )
    assert local_code._decode_porcelain_status_paths(payload) == (
        "copied.py", "ordinary.py", "renamed.py", "untracked.py",
    )


def test_collect_signals_real_git_rename_never_promotes_the_source(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    source = tmp_path / "ab source.py"
    source.write_text("print('source')\n", encoding="utf-8")
    subprocess.run(["git", "add", source.name], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    subprocess.run(["git", "mv", source.name, "renamed.py"], cwd=tmp_path, check=True)

    signals = local_code.collect_code_signals(tmp_path, base="HEAD")

    assert signals["git_status"] == ("renamed.py",)
    assert "ab source.py" not in signals["git_status"]


@pytest.mark.parametrize("payload", [
    b"R  renamed.py\0",
    b"source-without-status.py\0",
    b" M \0",
    b"\xff\0",
])
def test_porcelain_status_parser_rejects_malformed_records(payload):
    with pytest.raises(local_scout.LocalScoutError) as raised:
        local_code._decode_porcelain_status_paths(payload)
    assert raised.value.code == "LOCAL_SCOUT_POLICY_VIOLATION"


@pytest.mark.parametrize(
    ("name", "too_large"),
    [(name, maximum + 1) for name, maximum in local_code._CODE_LIMIT_MAXIMA.items()],
)
def test_every_code_map_v1_limit_is_a_hard_maximum(name, too_large):
    with pytest.raises(local_scout.LocalScoutError) as raised:
        local_code.CodeMapLimits(**{name: too_large})
    assert raised.value.code == "LOCAL_SCOUT_POLICY_VIOLATION"


def test_code_map_limits_accept_exact_maxima_and_reject_invalid_types():
    assert local_code.CodeMapLimits(**local_code._CODE_LIMIT_MAXIMA)
    with pytest.raises(local_scout.LocalScoutError):
        local_code.CodeMapLimits(max_candidates=True)
