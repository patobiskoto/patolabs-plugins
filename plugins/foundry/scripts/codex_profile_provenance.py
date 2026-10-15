"""PAT-196 read-only helper: hashes, key names and short scalar fields, never content.

Compares the `developer_instructions` of a Codex custom-agent profile with a rollout
written by a new Codex session. It reads only the paths it is given, calls no host and
writes nothing. Procedure and limits: docs/qualification/pat-196-codex-reviewer-provenance.md.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tomllib

_TOKEN = re.compile(r"[A-Za-z0-9._:/-]{1,64}")
_FOUNDRY_PATH = re.compile(r"plugins/cache/[A-Za-z0-9._-]+/foundry/([0-9][A-Za-z0-9._+-]*)")
_TOP_LEVEL = ("model", "model_reasoning_effort", "sandbox_mode", "approval_policy", "agents")


def _token(value):
    """Echo only short plain tokens; anything else may be content and is withheld."""
    if value is None:
        return "unknown"
    if isinstance(value, str) and _TOKEN.fullmatch(value):
        return value
    return "<not_a_short_token>"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _instructions(path):
    return tomllib.loads(Path(path).read_text(encoding="utf-8")).get("developer_instructions")


def inspect_profile(path):
    path = Path(path)
    if not path.is_file():
        return {"present": False}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    text = data.get("developer_instructions")
    info = {"present": True, "keys": sorted(data), "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    info["developer_instructions_sha256"] = _sha(text.strip()) if isinstance(text, str) else "unknown"
    return info


def compare_profiles(profile, example):
    a, b = _instructions(profile), _instructions(example)
    equal = isinstance(a, str) and isinstance(b, str) and a.strip() == b.strip()
    return {"compared": ["profile", "example"], "developer_instructions_equal": equal}


def inspect_config(path):
    path = Path(path)
    if not path.is_file():
        return {"present": False}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    agents = data.get("agents") if isinstance(data.get("agents"), dict) else {}
    reviewer = agents.get("reviewer") if isinstance(agents.get("reviewer"), dict) else {}
    return {"present": True, "agents_keys": sorted(agents), "agents_reviewer_keys": sorted(reviewer),
            "top_level_keys_present": {key: key in data for key in _TOP_LEVEL}}


def _dict(value):
    return value if isinstance(value, dict) else {}


def inspect_rollout(rollout, profile):
    text = _instructions(profile)
    needle = text.strip() if isinstance(text, str) else ""
    found, meta, ctx, versions, tokens, message = [], {}, {}, set(), 0, -1
    for line in Path(rollout).read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        payload = _dict(row.get("payload"))
        kind = row.get("type")
        versions.update(_FOUNDRY_PATH.findall(line))
        if kind == "session_meta" and not meta:
            meta = payload
        elif kind == "turn_context" and not ctx:
            ctx = payload
        elif kind == "event_msg" and payload.get("type") == "token_count":
            tokens += 1
        elif kind == "response_item" and payload.get("type") == "message":
            message += 1
            if payload.get("role") == "developer" and needle:
                for item, content in enumerate(payload.get("content") or []):
                    if needle in str(_dict(content).get("text", "")):
                        found.append({"message": message, "content_item": item})
    settings = _dict(_dict(ctx.get("collaboration_mode")).get("settings"))
    base = _dict(meta.get("base_instructions"))
    return {
        "rollout": Path(rollout).name,
        "loaded_profile": "matched" if found else "not_observed",
        "profile_text_found": found,
        "session_meta": {
            "agent_role": _token(meta.get("agent_role")),
            "cli_version": _token(meta.get("cli_version")),
            "model_provider": _token(meta.get("model_provider")),
            "thread_source": _token(meta.get("thread_source")),
            "multi_agent_version": _token(meta.get("multi_agent_version")),
            "base_instructions_provenance_model": _token(_dict(base.get("provenance")).get("model")),
        },
        "turn_context": {
            "model": _token(ctx.get("model")),
            "effort": _token(ctx.get("effort")),
            "collaboration_model": _token(settings.get("model")),
            "collaboration_reasoning_effort": _token(settings.get("reasoning_effort")),
            "approval_policy": _token(ctx.get("approval_policy")),
            "sandbox_policy_type": _token(_dict(ctx.get("sandbox_policy")).get("type")),
            "permission_profile_type": _token(_dict(ctx.get("permission_profile")).get("type")),
        },
        "foundry_versions": sorted(versions),
        "token_count_events": tokens,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="custom-agent profile TOML to compare")
    parser.add_argument("--rollout", action="append", default=[], help="rollout JSONL (repeatable)")
    parser.add_argument("--example", help="versioned example TOML to compare with the profile")
    parser.add_argument("--config", help="config.toml: only key names are reported")
    args = parser.parse_args(argv)
    report = {"profile": inspect_profile(args.profile)}
    if args.example:
        report["example"] = inspect_profile(args.example)
        if report["profile"]["present"]:
            report["comparison"] = compare_profiles(args.profile, args.example)
    if args.config:
        report["config"] = inspect_config(args.config)
    report["rollouts"] = [inspect_rollout(path, args.profile) for path in args.rollout]
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
