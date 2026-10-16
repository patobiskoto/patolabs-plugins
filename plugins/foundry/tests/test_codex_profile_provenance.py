"""PAT-196 helper on synthetic fixtures; reads no real Codex home and calls no host."""
import importlib.util
import json
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "codex_profile_provenance", PLUGIN / "scripts/codex_profile_provenance.py")
prov = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prov)

SECRET = "SYNTHETIC-INSTRUCTION-BODY-do-not-print"
PROFILE = f'name = "reviewer"\ndescription = "d"\ndeveloper_instructions = """\n{SECRET}\nsecond line\n"""\n'


def _rollout(path, instructions=SECRET + "\nsecond line", role="reviewer", sandbox="danger-full-access"):
    rows = [
        {"type": "session_meta", "payload": {
            "agent_role": role, "cli_version": "0.0.1", "model_provider": "p",
            "source": {"subagent": {"thread_spawn": {"agent_role": role}}},
            "thread_source": "subagent", "multi_agent_version": "v2",
            "base_instructions": {"text": "base", "provenance": {"type": "model", "model": "m-1"}}}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [
            {"type": "input_text", "text": "x" + instructions + "y"}, {"type": "input_text", "text": "other"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "see plugins/cache/mk/foundry/1.2.0/hooks and plugins/cache/mk/foundry/1.2.0/x"}]}},
        {"type": "turn_context", "payload": {
            "model": "m-1", "effort": "high", "approval_policy": "never",
            "sandbox_policy": {"type": sandbox}, "permission_profile": {"type": "disabled"},
            "collaboration_mode": {"mode": "default", "settings": {
                "model": "m-1", "reasoning_effort": "high", "developer_instructions": None}}}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {}}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_profile_file_hash_and_keys_without_content(tmp_path):
    profile = tmp_path / "reviewer.toml"
    profile.write_text(PROFILE)
    info = prov.inspect_profile(profile)
    assert info["present"] is True
    assert info["keys"] == ["description", "developer_instructions", "name"]
    assert len(info["developer_instructions_sha256"]) == 64
    assert SECRET not in json.dumps(info)
    assert prov.inspect_profile(tmp_path / "absent.toml") == {"present": False}


def test_matching_rollout_reports_location_and_observed_fields(tmp_path):
    profile = tmp_path / "reviewer.toml"
    profile.write_text(PROFILE)
    rollout = _rollout(tmp_path / "r.jsonl")
    report = prov.inspect_rollout(rollout, profile)
    assert report["profile_text_found"] == [{"message": 0, "content_item": 0}]
    # "matched" means the rollout contains the profile text verbatim; a short text could match by
    # chance, so a rollout known not to carry the profile (the parent) is the negative witness.
    assert report["loaded_profile"] == "matched"
    assert report["session_meta"]["agent_role"] == "reviewer"
    assert report["session_meta"]["cli_version"] == "0.0.1"
    assert report["turn_context"]["effort"] == "high"
    assert report["turn_context"]["sandbox_policy_type"] == "danger-full-access"
    assert report["turn_context"]["permission_profile_type"] == "disabled"
    assert report["foundry_versions"] == ["1.2.0"]
    assert report["token_count_events"] == 1
    assert SECRET not in json.dumps(report)


def test_changed_profile_is_not_matched_and_stays_unknown(tmp_path):
    profile = tmp_path / "reviewer.toml"
    profile.write_text(PROFILE.replace(SECRET, "another body"))
    report = prov.inspect_rollout(_rollout(tmp_path / "r.jsonl"), profile)
    assert report["profile_text_found"] == []
    assert report["loaded_profile"] == "not_observed"


def test_values_that_are_not_short_tokens_are_never_echoed(tmp_path):
    rollout = _rollout(tmp_path / "r.jsonl", sandbox="a long value with spaces " + SECRET)
    profile = tmp_path / "reviewer.toml"
    profile.write_text(PROFILE)
    report = prov.inspect_rollout(rollout, profile)
    assert report["turn_context"]["sandbox_policy_type"] == "<not_a_short_token>"
    assert SECRET not in json.dumps(report)


def test_missing_fields_are_reported_unknown(tmp_path):
    rollout = tmp_path / "r.jsonl"
    rollout.write_text(json.dumps({"type": "session_meta", "payload": {}}) + "\n")
    profile = tmp_path / "reviewer.toml"
    profile.write_text(PROFILE)
    report = prov.inspect_rollout(rollout, profile)
    assert report["turn_context"]["effort"] == "unknown"
    assert report["session_meta"]["agent_role"] == "unknown"
    assert report["foundry_versions"] == []
    assert report["loaded_profile"] == "not_observed"


def test_config_reports_key_names_only(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(f'model = "{SECRET}"\n[agents]\nmax_concurrent_threads_per_session = 3\n'
                      '[agents.reviewer]\nconfig_file = "x"\n')
    info = prov.inspect_config(config)
    assert info["agents_keys"] == ["max_concurrent_threads_per_session", "reviewer"]
    assert info["agents_reviewer_keys"] == ["config_file"]
    assert info["top_level_keys_present"]["model"] is True
    assert info["top_level_keys_present"]["sandbox_mode"] is False
    assert SECRET not in json.dumps(info)
    assert prov.inspect_config(tmp_path / "none.toml") == {"present": False}


def test_example_equality_names_compared_sets(tmp_path):
    profile = tmp_path / "reviewer.toml"
    profile.write_text(PROFILE)
    example = tmp_path / "example.toml"
    example.write_text(PROFILE)
    result = prov.compare_profiles(profile, example)
    assert result == {"compared": ["profile", "example"], "developer_instructions_equal": True}
    example.write_text(PROFILE.replace(SECRET, "other"))
    assert prov.compare_profiles(profile, example)["developer_instructions_equal"] is False


def test_cli_prints_json_without_content(tmp_path, capsys):
    profile = tmp_path / "reviewer.toml"
    profile.write_text(PROFILE)
    rollout = _rollout(tmp_path / "r.jsonl")
    assert prov.main(["--profile", str(profile), "--rollout", str(rollout)]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["rollouts"][0]["loaded_profile"] == "matched"
    assert SECRET not in out


def test_absent_profile_with_rollout_is_not_observed_not_an_error(tmp_path):
    report = prov.inspect_rollout(_rollout(tmp_path / "r.jsonl"), tmp_path / "absent.toml")
    assert report["loaded_profile"] == "not_observed"
    assert report["profile_text_found"] == []
    assert prov.main(["--profile", str(tmp_path / "absent.toml"), "--rollout", str(tmp_path / "r.jsonl")]) == 0


def test_json_lines_that_are_not_objects_are_skipped(tmp_path):
    profile = tmp_path / "reviewer.toml"
    profile.write_text(PROFILE)
    rollout = _rollout(tmp_path / "r.jsonl")
    rollout.write_text('[1, 2]\n"text"\n42\nnull\n' + rollout.read_text(), encoding="utf-8")
    report = prov.inspect_rollout(rollout, profile)
    assert report["loaded_profile"] == "matched"
    assert report["session_meta"]["agent_role"] == "reviewer"


def test_foundry_versions_go_through_the_token_filter(tmp_path):
    profile = tmp_path / "reviewer.toml"
    profile.write_text(PROFILE)
    long_version = "1." + "9" * 70
    rollout = tmp_path / "r.jsonl"
    rollout.write_text(json.dumps({"type": "x", "payload": {
        "t": f"plugins/cache/mk/foundry/1.4.0 plugins/cache/mk/foundry/{long_version}"}}) + "\n")
    versions = prov.inspect_rollout(rollout, profile)["foundry_versions"]
    assert versions == ["1.4.0", "<not_a_short_token>"]


def test_token_refuses_path_and_url_like_values():
    assert prov._token("read-only") == "read-only"
    assert prov._token("a/b") == "<not_a_short_token>"
    assert prov._token("a:b") == "<not_a_short_token>"
