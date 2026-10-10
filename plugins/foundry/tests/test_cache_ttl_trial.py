"""PAT-134: the bounded native cache-lifetime trial tool (``claude_profile_trial --cache-ttl-trial``).

Offline only: no host, no model, no network. The trial itself is never run here (fake launcher, synthetic logs).
Every value below is a generic placeholder: no name, no home path, no raw session identifier."""
import getpass
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from foundry import claude_profile_trial as trial

SONNET, OPUS = "claude-sonnet-5-5", "claude-opus-5-5"
MODIFIED, CONTROL = "routed-readonly-low-sonnet-5.5", "routed-worker-medium-opus-5.5"
ROOT = Path(__file__).resolve().parents[1]
_REAL_INSIDE_REPOSITORY = trial._inside_repository


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Valid even when pytest's tmp_path lies under a git repository; the shipped profiles are not read here."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.setattr(trial, "_inside_repository", lambda work: _REAL_INSIDE_REPOSITORY(work, ceiling=tmp_path))
    monkeypatch.setattr(trial, "profile_cache_ttl", lambda root, profile: "1h" if profile == MODIFIED else None)


def _usage(write_1h=0, write_5m=0, read=0):
    return {"input_tokens": 1, "output_tokens": 5, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": write_1h + write_5m,
            "cache_creation": {"ephemeral_1h_input_tokens": write_1h, "ephemeral_5m_input_tokens": write_5m}}


def _child_log(model, requests):
    return "\n".join(json.dumps({"type": "assistant", "timestamp": f"2026-10-10T10:00:{10 * n:02d}Z",
                                 "message": {"id": f"m{n}", "model": model, "usage": usage}})
                     for n, usage in enumerate(requests)) + "\n"


def _args(tmp_path, **kw):
    return SimpleNamespace(work_dir=str(tmp_path / "work"), out=str(tmp_path / "result.json"), claude="claude",
                           parent_model="sonnet", projects_dir=str(tmp_path / "projects"), dry_run=False, **kw)


FIRST = _usage(write_1h=900), _usage(read=900, write_1h=50)
FIRST_5M = _usage(write_5m=900), _usage(read=900, write_5m=50)


def _fake_host(tmp_path, *, modified=FIRST, control=FIRST_5M, modified_model=SONNET, control_model=OPUS,
               types=(f"foundry:{MODIFIED}", f"foundry:{CONTROL}"), extra_child=False, version="2.1.294",
               exit_code=0, calls=None):
    def launch(command, *, cwd, env, stdout, stderr):
        if calls is not None:
            calls.append((list(command), dict(env)))
        session = command[command.index("--session-id") + 1]
        stdout.write_text("\n".join(json.dumps(e) for e in (
            {"type": "system", "subtype": "init", "claude_code_version": version},
            {"type": "result", "subtype": "success", "is_error": False, "result": "done"})))
        folder = tmp_path / "projects" / "slug" / session / "subagents"
        folder.mkdir(parents=True)
        logs = [(modified_model, modified, types[0]), (control_model, control, types[1])]
        if extra_child:
            logs.append((OPUS, control, types[1]))
        for number, (model, requests, agent_type) in enumerate(logs):
            log = folder / f"agent-{number}.jsonl"
            log.write_text(_child_log(model, requests))
            log.with_suffix(".meta.json").write_text(json.dumps({"agentType": agent_type}))
        return {"exit_code": exit_code, "timed_out": False, "seconds": 1.0}
    return launch


def _run(tmp_path, **kw):
    code = trial.run_cache_ttl(_args(tmp_path), launch=_fake_host(tmp_path, **kw))
    return code, json.loads((tmp_path / "result.json").read_text())


def test_conforming_needs_1h_only_on_the_modified_and_5m_only_on_the_control(tmp_path):
    code, body = _run(tmp_path)
    assert code == 0 and body["verdict"] == "conforming" and body["schema"] == trial.CACHE_TTL_TRIAL_SCHEMA
    subject = body["subjects"]["modified"]
    # requested, transmitted and observed are three separate values
    assert subject["requested"]["profile_field_experimental_cacheTtl"] == "1h"
    assert subject["transmitted"] == {"profile": MODIFIED, "model": SONNET, "basis": subject["transmitted"]["basis"]}
    observed = body["observed"]["by_subject"]
    assert (observed["modified"]["cache_write_tokens_1h"], observed["modified"]["cache_write_tokens_5m"]) == (950, 0)
    assert (observed["control"]["cache_write_tokens_1h"], observed["control"]["cache_write_tokens_5m"]) == (0, 950)
    assert body["subjects"]["control"]["requested"]["profile_field_experimental_cacheTtl"] is None
    assert "usage_credits_drawn" in body["host"] and body["host"]["usage_credits_drawn"].startswith("unknown")
    assert body["bounds"]["executions"] == 1 and body["bounds"]["replays"] == 0


@pytest.mark.parametrize("kw,named", [
    ({"modified": FIRST_5M}, "modified.cache_class"),                                  # 1h not observed
    ({"modified": (_usage(write_1h=900), _usage(read=900, write_5m=50))}, "modified.cache_class"),  # mixed classes
    ({"control": FIRST}, "control.cache_class"),                                       # control wrote 1h
    ({"types": (f"foundry:{CONTROL}", f"foundry:{CONTROL}")}, "modified.profile"),
    ({"extra_child": True}, "children"),
    ({"exit_code": 1}, "process"),
])
def test_any_divergence_is_not_conforming_and_is_named(tmp_path, kw, named):
    code, body = _run(tmp_path, **kw)
    assert code == 1 and body["verdict"] == "not_conforming" and named in body["not_established"]


@pytest.mark.parametrize("kw,named", [
    ({"modified": (_usage(write_1h=900),)}, "modified.cache_class"),                    # a single request
    ({"modified": (_usage(read=10), _usage(read=10))}, "modified.cache_class"),         # no cache write at all
])
def test_anything_not_observed_is_unknown_never_conforming(tmp_path, kw, named):
    code, body = _run(tmp_path, **kw)
    assert code == 1 and body["verdict"] == "unknown" and named in body["not_established"]


def test_a_child_of_an_unexpected_model_is_not_conforming_and_leaves_its_subject_unobserved(tmp_path):
    code, body = _run(tmp_path, control_model="claude-haiku-5-5")
    assert code == 1 and body["verdict"] == "not_conforming"
    assert {"children", "control.child"} <= set(body["not_established"])


def test_a_profile_that_does_not_carry_the_field_cannot_make_the_trial_conforming(tmp_path, monkeypatch):
    monkeypatch.setattr(trial, "profile_cache_ttl", lambda root, profile: None)
    code, body = _run(tmp_path)
    assert code == 1 and body["verdict"] == "not_conforming" and "modified.requested" in body["not_established"]


def test_no_logs_at_all_is_unknown(tmp_path):
    code = trial.run_cache_ttl(_args(tmp_path), launch=lambda command, **k: {"exit_code": 0, "timed_out": False, "seconds": 0})
    body = json.loads((tmp_path / "result.json").read_text())
    assert code == 1 and body["verdict"] == "unknown" and body["observed"]["children"] == 0


def test_a_dated_snapshot_alias_is_attributed_to_its_line(tmp_path):
    code, body = _run(tmp_path, modified_model=SONNET + "-20261001")
    assert code == 0 and body["verdict"] == "conforming"


def test_the_trial_is_never_replayed_and_a_dry_run_launches_nothing(tmp_path, capsys):
    args = _args(tmp_path, cache_ttl_trial=True)
    args.dry_run = True
    assert trial.run_cache_ttl(args, launch=lambda *a, **k: pytest.fail("launched")) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["dry_run"] and "--plugin-dir" in printed["command"] and not (tmp_path / "result.json").exists()
    assert not (tmp_path / "work").exists() and list(tmp_path.iterdir()) == []  # a dry run creates nothing on disk
    args.dry_run = False
    assert trial.run_cache_ttl(args, launch=_fake_host(tmp_path)) == 0  # the same path is still usable for the real run
    (tmp_path / "result.json").unlink()
    with pytest.raises(trial.TrialError, match="never replayed"):  # the real run used that directory
        trial.run_cache_ttl(args, launch=lambda *a, **k: pytest.fail("launched"))
    args.work_dir = str(tmp_path / "work2")
    (tmp_path / "result.json").write_text("{}")
    with pytest.raises(trial.TrialError, match="never replayed"):  # the result file exists
        trial.run_cache_ttl(args, launch=lambda *a, **k: pytest.fail("launched"))


def test_the_work_directory_must_be_outside_a_repository(tmp_path):
    (tmp_path / ".git").mkdir()
    with pytest.raises(trial.TrialError, match="outside any git repository"):
        trial.run_cache_ttl(_args(tmp_path), launch=lambda *a, **k: pytest.fail("launched"))


def test_the_child_environment_is_the_closed_r6_list_and_the_parent_runs_once_in_source_mode(tmp_path):
    calls = []
    _run(tmp_path, calls=calls)
    assert len(calls) == 1
    command, env = calls[0]
    assert "--plugin-dir" in command and command.count("-p") == 1 and "sandbox-exec" not in " ".join(command)
    assert set(env) <= {"HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER",
                        "FOUNDRY_DATA", "FOUNDRY_RUNTIME_CONFIG_ISOLATED"}


def test_the_prompt_runs_the_two_roles_one_after_the_other_with_the_same_read_only_task(tmp_path):
    files = [tmp_path / "a.txt", tmp_path / "b.txt"]
    prompt = trial.cache_ttl_parent_prompt(files)
    assert prompt.index("foundry:lupin") < prompt.index("foundry:eiffel") and "Never run the two at once" in prompt
    assert all(str(f) in prompt for f in files) and "Read-only; no write" in prompt


def test_the_mode_is_separate_from_the_default_and_the_neutral_fixture(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_:
        trial.main(["--cache-ttl-trial", "--neutral-fixture", "--work-dir", str(tmp_path / "w"),
                    "--out", str(tmp_path / "r.json")])
    assert exit_.value.code == 2 and "separate modes" in capsys.readouterr().err and not (tmp_path / "w").exists()
    default = trial.parent_prompt(Path("/f/token.txt"))
    assert "foundry:lupin" in default and "PAT-134" not in default and trial.TRIAL_SCHEMA == "foundry.pat125-native-trial.v1"


def test_the_documented_minimum_host_version_is_recorded_not_enforced(tmp_path):
    _, body = _run(tmp_path, version="2.1.200")
    assert body["host"]["version_against_documented_minimum"] == "below" and body["verdict"] == "conforming"
    _, again = (tmp_path / "x").mkdir() or _run(tmp_path / "x", version="2.1.294")
    assert again["host"]["version_against_documented_minimum"] == "at_or_above"


def test_profile_cache_ttl_reads_only_the_experimental_map_of_the_frontmatter(tmp_path, monkeypatch):
    monkeypatch.undo()  # the real reader, not the autouse stub
    agents = tmp_path / "agents"
    agents.mkdir()
    body = "---\nname: p\nmodel: m\ntools: Read\neffort: low\n{}---\n\nexperimental:\n  cacheTtl: 5m\n"
    for name, extra, expected in [("none", "", None), ("one", "experimental:\n  cacheTtl: 1h\n", "1h"),
                                  ("two", "experimental:\n  cacheTtl: 1h\n  other: x\n", "divergent:cacheTtl,other"),
                                  ("top", "cacheTtl: 1h\n", None)]:
        (agents / f"{name}.md").write_text(body.format(extra))
        assert trial.profile_cache_ttl(tmp_path, name) == expected


def test_a_committed_result_would_carry_no_personal_trace(tmp_path):
    _, body = _run(tmp_path)
    text = json.dumps(body)
    assert str(tmp_path) not in text and "slug" not in text and not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text)
    assert "/Users/" not in text and "/home/" not in text


def test_the_shipped_checkout_requests_1h_for_the_modified_profile_and_nothing_for_the_control(monkeypatch):
    monkeypatch.undo()  # the real reader on the shipped profiles
    assert trial.profile_cache_ttl(ROOT, MODIFIED) == "1h"
    assert trial.profile_cache_ttl(ROOT, CONTROL) is None


def _personal_patterns():
    """What a committed file must not carry, derived at run time (no name is written in this file)."""
    names = {Path.home().name}
    try:
        names.add(getpass.getuser())
    except (KeyError, OSError, ImportError):
        pass
    patterns = [re.escape(f) for f in ("/Users/", "/home/", "/.claude")]
    patterns += [rf"/{re.escape(n)}(?:/|\"|$)" for n in names if len(n) >= 3]
    patterns += [rf"\b{re.escape(n)}\b" for n in names if len(n) >= 3]
    return patterns


def test_the_committed_native_trial_result_is_clean_and_consistent_with_its_own_verdict_rule():
    text = (ROOT / "docs/qualification/pat-134-native-trial.json").read_text(encoding="utf-8")
    body = json.loads(text)
    assert not any(re.search(pattern, text, re.M) for pattern in _personal_patterns())
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)  # a raw session id
    assert "session_id" not in text and ".jsonl" not in text and "projects" not in text
    assert body["schema"] == trial.CACHE_TTL_TRIAL_SCHEMA and body["bounds"]["executions"] == 1
    assert body["source"]["dirty"] is False and re.fullmatch(r"[0-9a-f]{40}", body["source"]["commit"])
    # the verdict is the tool's rule applied to the recorded facts
    facts = {key: body[key] for key in ("host", "observed", "conformity")}
    assert trial.cache_ttl_verdict(body["process"], facts) == (body["verdict"], body["not_established"])
    observed = body["observed"]["by_subject"]
    modified, control = observed["modified"], observed["control"]
    expected = body["verdict"] == "conforming"
    assert expected == (modified["cache_write_tokens_1h"] > 0 and modified["cache_write_tokens_5m"] == 0
                        and control["cache_write_tokens_5m"] > 0 and control["cache_write_tokens_1h"] == 0
                        and min(modified["requests"], control["requests"]) >= 2)
    assert body["subjects"]["modified"]["requested"]["profile_field_experimental_cacheTtl"] == "1h"
    assert body["subjects"]["control"]["requested"]["profile_field_experimental_cacheTtl"] is None
    assert body["host"]["usage_credits_drawn"].startswith("unknown")
