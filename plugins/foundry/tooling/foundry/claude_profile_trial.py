"""PAT-125: the ONE bounded native compatibility trial of the ``haiku-5.5`` / ``medium`` profile (PAT-ADR-0016).

Run once, by hand, on the maintainer's machine, by the coordinator; never by the tests. One ``claude -p`` parent
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

from foundry.command_runtime import _claude_child_environment
from foundry.routing_facades import (
    CLAUDE_MODEL_MIN_HOST_VERSION, _load_claude_policy, claude_invocation_binding,
)

TRIAL_SCHEMA = "foundry.pat125-native-trial.v1"
ROLE, CAPABILITY, MODEL, WIRE_MODEL, EFFORT = "scout", "readonly", "haiku-5.5", "claude-haiku-5-5", "medium"
TIMEOUT_SECONDS = 20 * 60
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
POLICY = {"version": 1, "mappings": {"claude": {"economy": {"model": MODEL, "effort": EFFORT}}}}
_CONTEXT_PREFIX = "Foundry Claude route: "
_DATED = re.compile(re.escape(WIRE_MODEL) + r"-\d{8}\Z")
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)\Z")


class TrialError(RuntimeError):
    """The trial was refused before any launch."""


def _packet(token_file: Path) -> str:
    return ("Goal:\nRead the single line of the file named in Inputs and return it verbatim.\n"
            f"Inputs:\n{token_file}\n"
            "Constraints:\nRead-only; one Read call; no delegation.\n"
            "Done when:\nThe line is returned verbatim.")


def parent_prompt(token_file: Path) -> str:
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


def run(args: Any, *, launch: Callable[..., dict[str, Any]] = _launch, today: dt.date | None = None) -> int:
    work, out = Path(args.work_dir).expanduser().resolve(), Path(args.out).expanduser().resolve()
    if work.exists() or out.exists():
        raise TrialError("the work directory and the result file must not exist: the trial is never replayed")
    if any((parent / ".git").exists() for parent in (work, *work.parents)):
        raise TrialError("the work directory must be outside any git repository: the fixture carries its own policy")
    token, session_id = f"PAT125-{secrets.token_hex(8)}", str(uuid.uuid4())
    (work / ".foundry").mkdir(parents=True)
    (work / "fixture").mkdir()
    (work / ".foundry" / "model-routing.json").write_text(json.dumps(POLICY), encoding="utf-8")
    token_file = work / "fixture" / "token.txt"
    token_file.write_text(token + "\n", encoding="utf-8")
    route = _load_claude_policy(work).resolve(ROLE, "claude")
    binding = claude_invocation_binding(route, CAPABILITY, plugin_root=PLUGIN_ROOT)  # absent/divergent: refused here
    if (route.model, route.effort, binding["transmitted_model"]) != (MODEL, EFFORT, WIRE_MODEL):
        raise TrialError("this checkout does not resolve the fixture to haiku-5.5 / medium")
    command = argv(args.claude, parent_prompt(token_file), session_id, args.parent_model)
    environment = {**_claude_child_environment(), "FOUNDRY_DATA": str(work / "foundry-data")}
    body: dict[str, Any] = {
        "schema": TRIAL_SCHEMA,
        "kind": "bounded native compatibility smoke (PAT-ADR-0016); not a benchmark, comparison, receipt or basis "
                "of the promotion",
        "date": (today or dt.date.today()).isoformat(),
        "bounds": {"parents": 1, "children_expected": 1, "timeout_seconds": TIMEOUT_SECONDS, "concurrency": 1,
                   "replays": 0, "environment_names": sorted(environment)},
        "source": _head(),
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


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m foundry.claude_profile_trial", description=__doc__.split("\n")[0])
    parser.add_argument("--work-dir", required=True, help="new directory outside any git repository (raw stream kept there)")
    parser.add_argument("--out", required=True, help="result file to write; must not exist")
    parser.add_argument("--claude", default="claude", help="host binary")
    parser.add_argument("--parent-model", default="sonnet", help="model of the parent session (not under trial)")
    parser.add_argument("--projects-dir", default="~/.claude/projects", help="host session logs")
    parser.add_argument("--dry-run", action="store_true", help="prepare the fixture and print the command; launch nothing")
    try:
        return run(parser.parse_args(arguments))
    except TrialError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
