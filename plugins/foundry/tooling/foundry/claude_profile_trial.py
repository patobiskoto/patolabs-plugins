"""PAT-125: a bounded native compatibility trial of the ``haiku-5.5`` / ``medium`` profile (PAT-ADR-0016).

PAT-ADR-0016 authorises ONE trial and no other without a new maintainer decision; two were run on 2026-10-09, the
second under such a decision. The tool enforces only what it can see: a run is refused when its work directory or
its result file already exists, so it blocks a replay by path, not a new run with new paths; whether a run is
authorised is the operator's responsibility. Each run is by hand, on the maintainer's machine, by the coordinator;
never by the tests. One ``claude -p`` parent
loads the Foundry plugin of THIS checkout (``--plugin-dir``, the source mode of the PAT-16 trials), delegates once to
the logical scout ``foundry:lupin`` from an isolated fixture whose project policy maps ``economy`` to ``haiku-5.5`` /
``medium``, and is killed after twenty minutes. The tool then reads the stream and the host's own session logs and
writes ONE result file. That this checkout, not the installed plugin cache, was exercised is read from the result:
the launched agent type is a profile that exists only here (an older plugin has no ``haiku-5.5`` and refuses the
fixture's policy), and the init event's plugin path when the host gives one.

It is a compatibility smoke: not a benchmark, not a comparison, not the basis of the promotion. Requested (policy),
transmitted (profile selected by the hook) and observed (native child metadata) stay three separate values; what was
not observed is ``unknown``, never a guess and never conforming. Nothing is replayed: an existing work directory or
result file is refused. The child environment is the R6 allow-list, so no API key or host override reaches it.

The first trial of 2026-10-09 (``pat-125-native-trial.json``) ran with the default fixture and its verdict stands.
That fixture names its value a "token" in ``token.txt``; under user settings that forbid displaying secrets the
value was not returned, and the host launched the agent in the background. ``--neutral-fixture`` is a separate
option, used by the second trial the maintainer authorised (``pat-125-native-trial-2.json``): a public "fixture
marker" in ``marker.txt``, said not to be a secret, and a parent told to wait for the agent's final answer. It
changes neither the default fixture, nor the verdict rule, nor the first recorded result. Any further trial needs a
new maintainer decision.

PAT-134 adds a separate mode, ``--cache-ttl-trial``, which changes none of the above (default fixture, PAT-125 verdict
rule and recorded results are untouched). It asks one question of the host: does a subagent launched through a Sonnet 5.5
versioned profile that carries ``experimental: {cacheTtl: 1h}`` write its prompt cache in the 1-hour class, while a subagent
of a profile that does NOT carry the field (an Opus 5.5 one, the control) writes in the 5-minute class only? One
``claude -p`` parent, from an isolated fixture whose project policy maps ``economy`` to ``sonnet-5.5`` / ``low`` and
``balanced`` to ``opus-5.5`` / ``medium``, launches two subagents ONE AFTER THE OTHER through the logical roles
``foundry:lupin`` (read-only, the modified profile) and ``foundry:eiffel`` (the control), each reading two fixture files so
that it makes at least two requests. The capabilities differ (read-only against worker) because the two logical roles that
resolve to the two models without a review claim are those two: a limit, named in the result. Requested (the
``cacheTtl`` value read from the profile file of this checkout), transmitted (the profile the hook selects, computed offline
before launch, and the agent type the host logged) and observed (cache-creation tokens per class, from the host's session
logs) stay three separate values. The observer reads, within FOUNDRY-ADR-0015, token counters (with their 1-hour / 5-minute
cache-creation split), the model alias, the record timestamp and the session identifier (the file is found by it; it is never
written to the result); BEYOND that list it reads ``type`` and ``message.id`` of each record (the reader of
``cache_ttl_replay``) and the ``agentType`` of each ``agent-*.meta.json`` (as the PAT-125 observer does). Subagents are
attributed to a profile by their model alias, never by content. ``conforming`` needs everything observed and exact; anything
not observed is ``unknown``, never conforming. Whether the host drew usage credits (documented to make it ignore ``1h``) is
not observable here and is recorded as ``unknown``. No test launches it. The coordinator runs it by hand, once, against the
LAST commit of the branch, which is the one that carries the field.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

from foundry.cache_ttl_replay import read_host_requests
from foundry.command_runtime import _claude_child_environment
from foundry.cost_breakdown import BreakdownError
from foundry.routing_facades import (
    CLAUDE_MODEL_MIN_HOST_VERSION, _load_claude_policy, claude_invocation_binding,
)

TRIAL_SCHEMA = "foundry.pat125-native-trial.v1"
ROLE, CAPABILITY, MODEL, WIRE_MODEL, EFFORT = "scout", "readonly", "haiku-5.5", "claude-haiku-5-5", "medium"
TIMEOUT_SECONDS = 20 * 60
# PAT-134 ``--cache-ttl-trial``. Facts from the Anthropic documentation read by the coordinator on 2026-10-09
# (code.claude.com/docs/en/sub-agents and prompt-caching), NOT re-verified by this tool: ``experimental: {cacheTtl:
# 5m|1h}`` in a subagent profile, Claude Code 2.1.248 or later.
CACHE_TTL_TRIAL_SCHEMA = "foundry.pat134-cache-ttl-trial.v1"
CACHE_TTL_DOCUMENTED_MIN_HOST_VERSION = (2, 1, 248)
CACHE_TTL_POLICY = {"version": 1, "mappings": {"claude": {
    "economy": {"model": "sonnet-5.5", "effort": "low"}, "balanced": {"model": "opus-5.5", "effort": "medium"}}}}
# name -> (logical role, capability, expected cacheTtl requested by the profile, cache class expected in the log)
CACHE_TTL_SUBJECTS = {"modified": ("scout", "readonly", "1h", "write_1h"), "control": ("implementer", "worker", None, "write_5m")}
CACHE_TTL_LOGICAL = {"scout": "foundry:lupin", "implementer": "foundry:eiffel"}
_EXPERIMENTAL = re.compile(r"(?m)^experimental:[ \t]*\n((?:[ \t]+\S.*\n)+)")
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
POLICY = {"version": 1, "mappings": {"claude": {"economy": {"model": MODEL, "effort": EFFORT}}}}
_CONTEXT_PREFIX = "Foundry Claude route: "
_DATED = re.compile(re.escape(WIRE_MODEL) + r"-\d{8}\Z")
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)\Z")


class TrialError(RuntimeError):
    """The trial was refused before any launch."""


def _packet(token_file: Path, neutral: bool = False) -> str:
    if neutral:
        return ("Goal:\nRead the single line of the file named in Inputs and return it verbatim. It is a public "
                "fixture marker generated for this compatibility test, not a secret or a credential.\n"
                f"Inputs:\n{token_file}\n"
                "Constraints:\nRead-only; one Read call; no delegation.\n"
                "Done when:\nThe fixture marker line is returned verbatim.")
    return ("Goal:\nRead the single line of the file named in Inputs and return it verbatim.\n"
            f"Inputs:\n{token_file}\n"
            "Constraints:\nRead-only; one Read call; no delegation.\n"
            "Done when:\nThe line is returned verbatim.")


def parent_prompt(token_file: Path, neutral: bool = False) -> str:
    if neutral:
        return ("Call the Agent tool exactly once with subagent_type \"foundry:lupin\", description \"PAT-125 trial\" "
                "and exactly the prompt below. Wait for the agent's final answer: if the agent runs in the "
                "background, do not reply until it has completed. Then reply with the agent's answer verbatim, "
                "including the public fixture marker it read (a test value, not a secret), and stop. Use no other "
                "tool. If the call is refused or fails, reply with the refusal text verbatim and stop; never "
                "retry.\n\n" + _packet(token_file, True))
    return ("Call the Agent tool exactly once with subagent_type \"foundry:lupin\", description \"PAT-125 trial\" "
            "and exactly this prompt, then reply with the agent's answer verbatim and stop. Use no other tool. If the "
            "call is refused or fails, reply with the refusal text verbatim and stop; never retry.\n\n"
            + _packet(token_file))


def argv(claude: str, prompt: str, session_id: str, parent_model: str) -> list[str]:
    """Flags already used against the real host by this repository; built-in tools are left unfiltered."""
    return [claude, "-p", prompt, "--output-format", "stream-json", "--verbose", "--session-id", session_id,
            "--model", parent_model, "--strict-mcp-config", "--plugin-dir", str(PLUGIN_ROOT)]


def _launch(command: Sequence[str], *, cwd: Path, env: dict[str, str], stdout: Path, stderr: Path) -> dict[str, Any]:
    """One process group, killed as a whole at the bound."""
    started = time.monotonic()
    with stdout.open("wb") as out, stderr.open("wb") as err:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   start_new_session=True)
        try:
            code, timed_out = process.wait(timeout=TIMEOUT_SECONDS), False
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            os.killpg(process.pid, signal.SIGKILL)
            code, timed_out = process.wait(), True
    return {"exit_code": code, "timed_out": timed_out, "seconds": round(time.monotonic() - started, 3)}


def _records(path: Path) -> list[dict[str, Any]]:
    result = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return result
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def _hook_context(value: Any) -> dict[str, Any] | None:
    """The routing hook's own statement, wherever the host kept it in the parent log."""
    if isinstance(value, str):
        start = value.find(_CONTEXT_PREFIX)
        if start < 0:
            return None
        try:
            found, _ = json.JSONDecoder().raw_decode(value[start + len(_CONTEXT_PREFIX):])
        except ValueError:
            return None
        return found if isinstance(found, dict) else None
    for item in (value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()):
        found = _hook_context(item)
        if found is not None:
            return found
    return None


def profile_cache_ttl(plugin_root: Path, profile: str) -> str | None:
    """The ``experimental.cacheTtl`` a profile file of this checkout carries (the REQUESTED value); ``None`` when absent.

    Reads only the frontmatter block. Anything else under ``experimental`` returns a marker naming it, never ``None``."""
    text = (plugin_root / "agents" / f"{profile}.md").read_text(encoding="utf-8")
    block = _EXPERIMENTAL.search(text.split("\n---\n", 1)[0] + "\n")
    if block is None:
        return None
    values = {}
    for line in block.group(1).splitlines():
        key, _, value = line.strip().partition(":")
        values[key.strip()] = value.strip()
    return values["cacheTtl"] if set(values) == {"cacheTtl"} else f"divergent:{','.join(sorted(values))}"


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def observe(stream: Path, projects_dir: Path, session_id: str, token: str, profile: str) -> dict[str, Any]:
    """Read what happened; every absent observation is ``None`` and named under ``unknown``."""
    events = _records(stream)
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    final = next((e for e in reversed(events) if e.get("type") == "result"), {})
    plugins = init.get("plugins")
    plugin_here = (any(isinstance(p, dict) and p.get("path") and Path(p["path"]).resolve() == PLUGIN_ROOT
                       for p in plugins) if isinstance(plugins, list) else None)
    agents = init.get("agents")
    parents = sorted(projects_dir.glob(f"*/{session_id}.jsonl"))
    children = sorted(projects_dir.glob(f"*/{session_id}/subagents/agent-*.jsonl"))
    hook = next((found for record in (_records(parents[0]) if len(parents) == 1 else [])
                 if (found := _hook_context(record)) is not None), None)
    agent_types, models, versions = [], set(), set()
    efforts: dict[str, set] = {"effort": set(), "perTurnEffort": set()}
    for child in children:
        try:
            agent_types.append(json.loads(child.with_suffix(".meta.json").read_text(encoding="utf-8")).get("agentType"))
        except (OSError, ValueError, AttributeError):
            agent_types.append(None)
        for record in _records(child):
            if isinstance(record.get("version"), str):
                versions.add(record["version"])
            if record.get("type") != "assistant":
                continue
            message = record.get("message")
            if isinstance(message, dict) and isinstance(message.get("model"), str):
                models.add(message["model"])
            for key, seen in efforts.items():
                if key in record:
                    seen.add(record[key])
    observed_efforts = efforts["effort"] | efforts["perTurnEffort"]
    host_versions = versions or ({init["claude_code_version"]} if isinstance(init.get("claude_code_version"), str)
                                 else set())
    minimum = CLAUDE_MODEL_MIN_HOST_VERSION[MODEL]
    parsed = [_VERSION.match(v) for v in host_versions]
    conformity = {
        "profile": ("unknown" if not children or None in agent_types
                    else "exact" if agent_types == [f"foundry:{profile}"] else "divergent"),
        "model": ("unknown" if not models else "exact" if models == {WIRE_MODEL}
                  else "dated_snapshot" if all(_DATED.match(m) for m in models) else "divergent"),
        "effort": ("unknown" if not observed_efforts else "exact" if observed_efforts == {EFFORT} else "divergent"),
        "host_version": ("unknown" if not parsed or None in parsed
                         else "conforming" if all(tuple(map(int, m.groups())) >= minimum for m in parsed)
                         else "below_minimum"),
        "plugin_source": {True: "this_checkout", False: "divergent", None: "unknown"}[plugin_here],
        "fixture": ("unknown" if not isinstance(final.get("result"), str)
                    else "exact" if token in final["result"] else "divergent"),
    }
    return {
        "host": {"claude_code_version": init.get("claude_code_version"),
                 "plugin_from_this_checkout": plugin_here,
                 "profile_listed_at_init": (f"foundry:{profile}" in agents) if isinstance(agents, list) else None},
        "hook": None if hook is None else {key: hook.get(key) for key in
                                           ("model", "effort", "effort_parameters", "transmitted_launch",
                                            "host_version", "sources", "warnings")},
        "observed": {"children": len(children), "agent_types": agent_types, "models": sorted(models),
                     "efforts": sorted(efforts["effort"], key=str),
                     "per_turn_efforts": sorted(efforts["perTurnEffort"], key=str),
                     "child_host_versions": sorted(versions),
                     "log_sha256": {"parent": _sha256(parents[0]) if len(parents) == 1 else None,
                                    "children": [_sha256(child) for child in children]}},
        "parent_result": {"is_error": final.get("is_error"), "subtype": final.get("subtype")},
        "conformity": conformity,
    }


def verdict(process: dict[str, Any], facts: dict[str, Any]) -> tuple[str, list[str]]:
    """``conforming`` only when everything was observed and exact; anything unobserved keeps the incumbent."""
    conformity = facts["conformity"]
    failed = [name for name, state in conformity.items() if state in ("divergent", "below_minimum")]
    if process["timed_out"] or process["exit_code"] != 0:
        failed.append("process")
    if facts["observed"]["children"] > 1:
        failed.append("children")
    open_points = [name for name, state in conformity.items() if state in ("unknown", "dated_snapshot")]
    if facts["hook"] is None:
        open_points.append("hook")
    if failed:
        return "not_conforming", failed + open_points
    # The profile exists only in this checkout: the launched agent type establishes the source even when the
    # init event names no plugin path, and the host need not keep the hook's context in its log.
    blocking = [point for point in open_points if point not in ("hook", "plugin_source")]
    return ("unknown" if blocking else "conforming"), open_points


def _head() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        done = subprocess.run(["git", "-C", str(PLUGIN_ROOT), *args], capture_output=True, text=True, check=False)
        return done.stdout.strip() if done.returncode == 0 else None
    status = git("status", "--porcelain", "--", ".")
    return {"commit": git("rev-parse", "HEAD"), "dirty": None if status is None else bool(status)}


def _inside_repository(work: Path, ceiling: Path | None = None) -> bool:
    """Whether ``work`` or an ancestor holds ``.git``; ``ceiling`` (tests) bounds the walk to that directory."""
    return any((parent / ".git").exists() for parent in (work, *work.parents)
               if ceiling is None or parent == ceiling or ceiling in parent.parents)


def run(args: Any, *, launch: Callable[..., dict[str, Any]] = _launch, today: dt.date | None = None) -> int:
    work, out = Path(args.work_dir).expanduser().resolve(), Path(args.out).expanduser().resolve()
    if work.exists() or out.exists():
        raise TrialError("the work directory and the result file must not exist: the trial is never replayed")
    if _inside_repository(work):
        raise TrialError("the work directory must be outside any git repository: the fixture carries its own policy")
    neutral = bool(getattr(args, "neutral_fixture", False))
    token = f"marqueur-de-fixture-{secrets.randbelow(10**8):08d}" if neutral else f"PAT125-{secrets.token_hex(8)}"
    session_id = str(uuid.uuid4())
    (work / ".foundry").mkdir(parents=True)
    (work / "fixture").mkdir()
    (work / ".foundry" / "model-routing.json").write_text(json.dumps(POLICY), encoding="utf-8")
    token_file = work / "fixture" / ("marker.txt" if neutral else "token.txt")
    token_file.write_text(token + "\n", encoding="utf-8")
    route = _load_claude_policy(work).resolve(ROLE, "claude")
    binding = claude_invocation_binding(route, CAPABILITY, plugin_root=PLUGIN_ROOT)  # absent/divergent: refused here
    if (route.model, route.effort, binding["transmitted_model"]) != (MODEL, EFFORT, WIRE_MODEL):
        raise TrialError("this checkout does not resolve the fixture to haiku-5.5 / medium")
    command = argv(args.claude, parent_prompt(token_file, neutral), session_id, args.parent_model)
    environment = {**_claude_child_environment(), "FOUNDRY_DATA": str(work / "foundry-data")}
    body: dict[str, Any] = {
        "schema": TRIAL_SCHEMA,
        "kind": "bounded native compatibility smoke (PAT-ADR-0016); not a benchmark, comparison, receipt or basis "
                "of the promotion",
        "date": (today or dt.date.today()).isoformat(),
        "bounds": {"parents": 1, "children_expected": 1, "timeout_seconds": TIMEOUT_SECONDS, "concurrency": 1,
                   "replays": 0, "environment_names": sorted(environment)},
        "source": _head(),
        **({"fixture_variant": "neutral"} if neutral else {}),
        "argv": [part if part != session_id else "<session id>" for part in
                 (command[0], "-p", "<parent prompt>", *command[3:-1], "<plugins/foundry of this checkout>")],
        "requested": {"role": ROLE, "tier": route.selected_tier, "model": route.model, "effort": route.effort},
        "transmitted": {"profile": binding["profile"], "model": binding["transmitted_model"],
                        "effort": binding["effort_parameters"]["transmitted"], "source": binding["model_source"],
                        "basis": "computed offline from this checkout before launch; the hook's own statement, when "
                                 "the host kept it, is under hook"},
    }
    if args.dry_run:
        print(json.dumps({**body, "dry_run": True, "command": command, "cwd": str(work)}, indent=2,
                         ensure_ascii=False))
        return 0
    process = launch(command, cwd=work, env=environment, stdout=work / "stream.ndjson", stderr=work / "stderr.txt")
    facts = observe(work / "stream.ndjson", Path(args.projects_dir).expanduser(), session_id, token,
                    binding["profile"])
    state, open_points = verdict(process, facts)
    body.update({"process": process, **facts, "verdict": state, "not_established": open_points})
    out.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{state}: {out}")
    return 0 if state == "conforming" else 1


# ------------------------------------------------------------------------------------- PAT-134 cache-class trial

def cache_ttl_packet(files: Sequence[Path]) -> str:
    listing = "\n".join(str(f) for f in files)
    return ("Goal:\nRead each file named in Inputs, one Read call per file and one after the other, and return the "
            "first line of each, verbatim. They are public fixture lines generated for this compatibility test.\n"
            f"Inputs:\n{listing}\n"
            "Constraints:\nRead-only; no write; no delegation.\n"
            "Done when:\nThe first line of every file is returned verbatim.")


def cache_ttl_parent_prompt(files: Sequence[Path]) -> str:
    packet = cache_ttl_packet(files)
    first, second = CACHE_TTL_LOGICAL["scout"], CACHE_TTL_LOGICAL["implementer"]
    return (f"Call the Agent tool with subagent_type \"{first}\", description \"PAT-134 trial 1\" and exactly the "
            f"prompt below. Wait for its final answer: if it runs in the background, do not continue until it has "
            f"completed. Only then call the Agent tool a second time with subagent_type \"{second}\", description "
            "\"PAT-134 trial 2\" and exactly the same prompt, and wait for its final answer the same way. Never run "
            "the two at once. Then reply with the two answers verbatim, labelled 1 and 2, and stop. Use no other tool. "
            "If a call is refused or fails, reply with the refusal text verbatim and stop; never retry.\n\n" + packet)


def _model_matches(alias: str, wire: str) -> bool:
    return alias == wire or re.fullmatch(re.escape(wire) + r"-\d{8}", alias) is not None


def observe_cache_ttl(stream: Path, projects_dir: Path, session_id: str, subjects: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Cache-creation tokens per class for each child log, attributed to a subject by model alias; absent = ``None``.

    ``subjects[name]`` carries ``wire`` (model), ``profile`` (expected), ``requested`` and ``expected_class``."""
    events = _records(stream)
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    final = next((e for e in reversed(events) if e.get("type") == "result"), {})
    children = sorted(projects_dir.glob(f"*/{session_id}/subagents/agent-*.jsonl"))
    per_child, unreadable = [], 0
    for child in children:
        try:
            agent_type = json.loads(child.with_suffix(".meta.json").read_text(encoding="utf-8")).get("agentType")
        except (OSError, ValueError, AttributeError):
            agent_type = None
        try:
            requests = read_host_requests(child, interactive=True)
        except BreakdownError:
            unreadable += 1
            continue
        sound = [r for r in requests if not r.get("ambiguous")]
        models = sorted({r["model"] for r in requests})
        per_child.append({
            "agent_type": agent_type, "models": models, "requests": len(requests),
            "requests_ambiguous_counters": len(requests) - len(sound),
            "cache_write_tokens_1h": sum(r["tokens"]["write_1h"] for r in sound),
            "cache_write_tokens_5m": sum(r["tokens"]["write_5m"] for r in sound),
            "cache_read_tokens": sum(r["tokens"]["read"] for r in sound),
            "log_sha256": _sha256(child)})
    attributed: dict[str, list[dict[str, Any]]] = {name: [] for name in subjects}
    unattributed = 0
    for child in per_child:
        owners = [name for name, subject in subjects.items()
                  if len(child["models"]) == 1 and _model_matches(child["models"][0], subject["wire"])]
        if len(owners) == 1:
            attributed[owners[0]].append(child)
        else:
            unattributed += 1
    versions = {init["claude_code_version"]} if isinstance(init.get("claude_code_version"), str) else set()
    parsed = [_VERSION.match(v) for v in versions]
    host_state = ("unknown" if not parsed or None in parsed
                  else "at_or_above" if all(tuple(map(int, m.groups())) >= CACHE_TTL_DOCUMENTED_MIN_HOST_VERSION
                                            for m in parsed)
                  else "below")
    conformity: dict[str, dict[str, str]] = {}
    for name, subject in subjects.items():
        found = attributed[name]
        if len(found) != 1:
            conformity[name] = {"child": "unknown" if not found else "divergent"}
            continue
        child = found[0]
        wrote_1h, wrote_5m = child["cache_write_tokens_1h"] > 0, child["cache_write_tokens_5m"] > 0
        wanted_1h = subject["expected_class"] == "write_1h"
        right = (wrote_1h and not wrote_5m) if wanted_1h else (wrote_5m and not wrote_1h)
        conformity[name] = {
            "child": "exact",
            "profile": ("unknown" if child["agent_type"] is None
                        else "exact" if child["agent_type"] == f"foundry:{subject['profile']}" else "divergent"),
            "requested": "exact" if subject["requested"] == subject["requested_expected"] else "divergent",
            "cache_class": ("unknown" if not (wrote_1h or wrote_5m) else "divergent" if not right
                            else "exact" if child["requests"] >= 2 else "unknown")}
    return {
        "host": {"claude_code_version": init.get("claude_code_version"),
                 "documented_minimum_for_cache_ttl": ".".join(map(str, CACHE_TTL_DOCUMENTED_MIN_HOST_VERSION)),
                 "version_against_documented_minimum": host_state,
                 "usage_credits_drawn": "unknown (not observable here; documented to make the host ignore 1h)"},
        "observed": {"children": len(children), "children_unreadable": unreadable, "children_unattributed": unattributed,
                     "by_subject": {name: (attributed[name][0] if len(attributed[name]) == 1
                                           else {"children_found": len(attributed[name])}) for name in subjects}},
        "parent_result": {"is_error": final.get("is_error"), "subtype": final.get("subtype")},
        "conformity": conformity}


def cache_ttl_verdict(process: dict[str, Any], facts: dict[str, Any]) -> tuple[str, list[str]]:
    """``conforming`` only if the modified subagent wrote 1-hour cache and no 5-minute cache AND the control wrote
    5-minute cache and no 1-hour cache, with everything else exact; anything not observed is ``unknown``."""
    failed, open_points = [], []
    for name, states in facts["conformity"].items():
        for key, state in states.items():
            if state in ("divergent", "below_minimum"):
                failed.append(f"{name}.{key}")
            elif state == "unknown":
                open_points.append(f"{name}.{key}")
    observed = facts["observed"]
    if process["timed_out"] or process["exit_code"] != 0:
        failed.append("process")
    if observed["children"] != 2 or observed["children_unreadable"] or observed["children_unattributed"]:
        (failed if observed["children"] > 2 or observed["children_unattributed"] else open_points).append("children")
    if facts["host"]["version_against_documented_minimum"] == "unknown":
        open_points.append("host_version")
    if failed:
        return "not_conforming", failed + open_points
    return ("unknown" if open_points else "conforming"), open_points


def run_cache_ttl(args: Any, *, launch: Callable[..., dict[str, Any]] = _launch, today: dt.date | None = None) -> int:
    work, out = Path(args.work_dir).expanduser().resolve(), Path(args.out).expanduser().resolve()
    if work.exists() or out.exists():
        raise TrialError("the work directory and the result file must not exist: the trial is never replayed")
    if _inside_repository(work):
        raise TrialError("the work directory must be outside any git repository: the fixture carries its own policy")
    session_id = str(uuid.uuid4())
    (work / ".foundry").mkdir(parents=True)
    (work / "fixture").mkdir()
    (work / ".foundry" / "model-routing.json").write_text(json.dumps(CACHE_TTL_POLICY), encoding="utf-8")
    files = []
    for name in ("first.txt", "second.txt"):
        files.append(work / "fixture" / name)
        files[-1].write_text(f"public fixture line {secrets.randbelow(10**8):08d}\n", encoding="utf-8")
    policy = _load_claude_policy(work)
    subjects: dict[str, dict[str, Any]] = {}
    for name, (role, capability, requested_expected, expected_class) in CACHE_TTL_SUBJECTS.items():
        route = policy.resolve(role, "claude")
        binding = claude_invocation_binding(route, capability, plugin_root=PLUGIN_ROOT)  # absent/divergent: refused
        subjects[name] = {"role": role, "logical": CACHE_TTL_LOGICAL[role], "capability": capability, "route": route,
                          "binding": binding, "wire": binding["transmitted_model"], "profile": binding["profile"],
                          "requested": profile_cache_ttl(PLUGIN_ROOT, binding["profile"]),
                          "requested_expected": requested_expected, "expected_class": expected_class}
    command = argv(args.claude, cache_ttl_parent_prompt(files), session_id, args.parent_model)
    environment = {**_claude_child_environment(), "FOUNDRY_DATA": str(work / "foundry-data")}
    body: dict[str, Any] = {
        "schema": CACHE_TTL_TRIAL_SCHEMA,
        "kind": "bounded native trial of the subagent cache lifetime (PAT-134); not a benchmark, a cost measure or a "
                "statement about the subscription quota (unknown)",
        "date": (today or dt.date.today()).isoformat(),
        "bounds": {"executions": 1, "parents": 1, "children_expected": 2, "timeout_seconds": TIMEOUT_SECONDS,
                   "concurrency": 1, "replays": 0, "environment_names": sorted(environment)},
        "limits": ["the control is a worker profile (Opus 5.5) and the modified one a read-only profile (Sonnet 5.5): "
                   "the two roles that resolve to the two models without a review claim",
                   "two subagents of one parent run: not a sample",
                   "usage credits drawn during the run are not observable here"],
        "source": _head(),
        "argv": [part if part != session_id else "<session id>" for part in
                 (command[0], "-p", "<parent prompt>", *command[3:-1], "<plugins/foundry of this checkout>")],
        "subjects": {name: {
            "role": s["role"], "logical_agent": s["logical"],
            "requested": {"tier": s["route"].selected_tier, "model": s["route"].model, "effort": s["route"].effort,
                          "profile_field_experimental_cacheTtl": s["requested"],
                          "expected_by_the_trial": s["requested_expected"]},
            "transmitted": {"profile": s["profile"], "model": s["wire"],
                            "basis": "computed offline from this checkout before launch; the agent type the host "
                                     "logged is under observed"}} for name, s in subjects.items()},
    }
    if args.dry_run:
        print(json.dumps({**body, "dry_run": True, "command": command, "cwd": str(work)}, indent=2, ensure_ascii=False))
        return 0
    process = launch(command, cwd=work, env=environment, stdout=work / "stream.ndjson", stderr=work / "stderr.txt")
    facts = observe_cache_ttl(work / "stream.ndjson", Path(args.projects_dir).expanduser(), session_id, subjects)
    state, open_points = cache_ttl_verdict(process, facts)
    body.update({"process": process, **facts, "verdict": state, "not_established": open_points})
    out.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{state}: {out}")
    return 0 if state == "conforming" else 1


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m foundry.claude_profile_trial", description=__doc__.split("\n")[0])
    parser.add_argument("--work-dir", required=True, help="new directory outside any git repository (raw stream kept there)")
    parser.add_argument("--out", required=True, help="result file to write; must not exist")
    parser.add_argument("--claude", default="claude", help="host binary")
    parser.add_argument("--parent-model", default="sonnet", help="model of the parent session (not under trial)")
    parser.add_argument("--projects-dir", default="~/.claude/projects", help="host session logs")
    parser.add_argument("--dry-run", action="store_true", help="prepare the fixture and print the command; launch nothing")
    parser.add_argument("--neutral-fixture", action="store_true",
                        help="separate fixture: a public fixture marker instead of a value named token, and a "
                             "parent told to wait for the agent's answer")
    parser.add_argument("--cache-ttl-trial", action="store_true",
                        help="PAT-134, separate mode: two subagents (Sonnet 5.5 profile carrying experimental.cacheTtl, "
                             "Opus 5.5 control) from one parent; records the cache class each wrote; default fixture, "
                             "PAT-125 rule and results untouched")
    try:
        parsed = parser.parse_args(arguments)
        if parsed.cache_ttl_trial and parsed.neutral_fixture:
            parser.error("--cache-ttl-trial and --neutral-fixture are separate modes")
        return (run_cache_ttl if parsed.cache_ttl_trial else run)(parsed)
    except TrialError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
