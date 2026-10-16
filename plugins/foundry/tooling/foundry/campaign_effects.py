"""Effects of an Epic campaign: one pass, at most one effect (PAT-217, PAT-ADR-0026).

``campaign_cadence.decide`` says what the next step is; this module executes it.  Every
effect on the tracker or the code host goes through a Foundry command run as a subprocess
(``issue start``, ``issue openpr``, ``issue merge``); the only other launch is one fresh
headless ``claude --print`` session that writes code in the ticket worktree.  Nothing here
calls a tracker or code-host write primitive directly, and the merge gates (proven green CI
and the review proof of the exact diff) stay inside ``issue merge``.

An attempt is never replayed: a local append-only journal records a ``started`` line, under
a lock, before the effect and a ``finished`` line after it.  See ``docs/campaign-effects.md``.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from foundry import campaign_approval as approval
from foundry import campaign_cadence as cadence
from foundry import campaign_review
from foundry import registry
from foundry.epic_preview import _canonical

EFFECTS_CONTRACT = "foundry-campaign-attempts.v1"
ATTEMPTS_NAME = "campaign-attempts.jsonl"
SUMMARIES_NAME = "campaign-summaries"
EFFECT_KINDS = ("start", "implement", "open-pr", "review", "correct", "merge")
# The kinds that launch a model session; the approval ceilings count them all (PAT-218).
SESSION_KINDS = ("implement", "review", "correct")
IMPLEMENT_BUDGET_USD = 5.0  # default ceiling of one session; not measured (unknown)
MAX_BUDGET_USD = 25.0
IMPLEMENT_TIMEOUT_SECONDS = 2 * 60 * 60
COMMAND_TIMEOUT_SECONDS = 30 * 60
GIT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_STEPS = 50
MAX_STEPS = 500
DEFAULT_POLL_SECONDS = 60
MAX_POLL_SECONDS = 600
MAX_SUMMARY_BYTES = 32 * 1024
# The session may commit locally; it holds no push, code-host or Foundry write verb.  These
# are tool rules of the host, a discipline boundary and not a sandbox (docs/campaign-effects.md).
DISALLOWED_TOOLS = (
    "Bash(git push:*)", "Bash(gh:*)",
    "Bash(python3 *foundry_cli.py issue *)", "Bash(python3 *foundry_cli.py edit *)",
    "Bash(python3 *foundry_cli.py adr *)", "Bash(python3 *foundry_cli.py campaign-approval approve*)",
    "Bash(python *foundry_cli.py issue *)", "Bash(python *foundry_cli.py edit *)",
    "Bash(python *foundry_cli.py adr *)", "Bash(python *foundry_cli.py campaign-approval approve*)",
)
_KEYS = {
    "started": {"schema_version", "event", "attempt", "epic", "ticket", "kind", "fingerprint",
                "session_id", "route", "at"},
    "finished": {"schema_version", "event", "attempt", "epic", "ticket", "kind", "status",
                 "exit_code", "cost_usd", "duration_ms", "summary_file", "branch_head",
                 "error", "at"},
}
_OPTIONAL = {"started": set(), "finished": {"review"}}  # PAT-218: the verdict of a review


class AttemptJournalInvalid(ValueError):
    """The attempt journal cannot be trusted: no effect is launched."""


def journal_path() -> Path:
    return Path(registry.data_dir()) / ATTEMPTS_NAME


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def identity(epic: str, ticket: str, kind: str, fingerprint: dict) -> str:
    """sha256 of the epic, ticket, step kind and observed fingerprint (AC3)."""
    return hashlib.sha256(_canonical(
        {"epic": epic, "ticket": ticket, "kind": kind, "fingerprint": fingerprint}
    )).hexdigest()


def fingerprint(ticket: cadence.TicketFacts) -> dict:
    """Tracker state, branch head, PR number and PR head sha of the ticket in focus."""
    return {
        "state": ticket.state, "branch_sha": ticket.branch_sha,
        "pr": ticket.pr.number if ticket.pr else None,
        "pr_head": ticket.pr.head_sha if ticket.pr else None,
    }


def read_attempts(path: Path | None = None) -> list[dict]:
    """Every record, or ``AttemptJournalInvalid`` when any line fails."""
    target = path or journal_path()
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise AttemptJournalInvalid("journal des tentatives illisible") from exc
    records = []
    for line in raw.split(b"\n"):
        if not line:
            continue
        try:
            record = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise AttemptJournalInvalid("ligne de journal illisible") from exc
        if (not isinstance(record, dict) or record.get("schema_version") != EFFECTS_CONTRACT
                or record.get("event") not in _KEYS or not isinstance(record.get("attempt"), str)
                or not _KEYS[record["event"]] <= set(record)
                <= _KEYS[record["event"]] | _OPTIONAL[record["event"]]):
            raise AttemptJournalInvalid("enregistrement de tentative hors contrat")
        if "review" in record:
            if record["kind"] != "review" or not campaign_review.valid_review_field(
                    record["review"]):
                raise AttemptJournalInvalid("verdict de relecture hors contrat")
        records.append(record)
    return records


def _open_locked(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def _write(descriptor: int, record: dict) -> None:
    os.write(descriptor, _canonical(record) + b"\n")
    os.fsync(descriptor)


def claim(path: Path, record: dict, ceilings: tuple[int, int | None] | None) -> str | None:
    """Append the ``started`` record unless the identity exists or a ceiling is reached.

    The read and the append happen under one exclusive lock, so two passes cannot both
    claim an identity.  Returns ``None`` when claimed, else the refusal code.
    """
    descriptor = _open_locked(path)
    try:
        records = read_attempts(path)
        started = [r for r in records if r["event"] == "started"]
        if any(r["attempt"] == record["attempt"] for r in started):
            return "attempt_already_made"
        if ceilings is not None and record["kind"] in SESSION_KINDS:
            sessions = [r for r in started if r["epic"] == record["epic"]
                        and r["kind"] in SESSION_KINDS]
            maximum, per_issue = ceilings
            if len(sessions) >= maximum:
                return "session_ceiling_reached"
            if per_issue is not None and sum(
                    r["ticket"] == record["ticket"] for r in sessions) >= per_issue:
                return "session_ceiling_per_issue_reached"
        _write(descriptor, record)
        return None
    finally:
        os.close(descriptor)


def finish(path: Path, record: dict) -> None:
    descriptor = _open_locked(path)
    try:
        _write(descriptor, record)
    finally:
        os.close(descriptor)


def last_implementation(path: Path, epic: str, ticket: str, branch_sha: str | None,
                        kinds: tuple[str, ...] = ("implement", "correct")) -> dict | None:
    """The last completed session of ``kinds`` that ended on exactly this branch head, if any.

    ``correct`` (PAT-218) counts: a correction that committed leaves the branch ahead of the PR.
    """
    if not branch_sha:
        return None
    found = None
    for record in read_attempts(path):
        if (record["event"] == "finished" and record["kind"] in kinds
                and record["epic"] == epic and record["ticket"] == ticket
                and record["status"] == "completed" and record["branch_head"] == branch_sha):
            found = record
    return found


@dataclass
class StepResult:
    decision: cadence.Decision
    outcome: str  # executed | failed | wait-ci | terminal | stopped
    effect: dict | None = None

    def to_dict(self) -> dict:
        from dataclasses import asdict

        return {"outcome": self.outcome, "decision": asdict(self.decision), "effect": self.effect}


@dataclass
class Runtime:
    root: str
    worktrees_dir: Path
    runner: Callable = subprocess.run
    budget_usd: float = IMPLEMENT_BUDGET_USD
    plugin_root: Path = Path(__file__).resolve().parents[2]
    environ: Callable[[], dict] = lambda: dict(os.environ)  # noqa: E731

    @property
    def cli(self) -> str:
        return str(self.plugin_root / "tooling" / "foundry_cli.py")

    def command(self, *args: str) -> list[str]:
        return [sys.executable, self.cli, *args]


def default_worktrees_dir(root: str) -> Path:
    top = Path(root).resolve()
    return top.parent / f"{top.name}-campaign"


def implement_prompt(epic: str, ticket: str, title: str, body: str) -> str:
    # The session runs with an isolated Foundry configuration (no tracker credential), so
    # the coordinator reads the issue and hands its text over; the session reads no tracker.
    return (
        "FOUNDRY_CAMPAIGN_IMPLEMENT_V1\n"
        f"Epic campaign {epic}, issue {ticket}. You are a fresh session in the worktree of "
        "this issue's branch.\n"
        f"1. The issue, as read by the coordinator (do not query the tracker):\n"
        f"<issue id=\"{ticket}\">\n# {title}\n\n{body}\n</issue>\n"
        "2. Implement exactly its acceptance criteria, nothing else; follow AGENTS.md.\n"
        "3. Run the tests that prove it.\n"
        "4. Commit locally on the current branch.\n"
        "You hold no authority over the tracker or the code host: never push, never open or "
        "merge a pull request, never write the tracker, never run `issue`, `edit`, `adr` or "
        "`campaign-approval`. The coordinator does all of that from your commits.\n"
        "Finish with a short pull-request summary in Markdown (what changed, tests run, "
        "remaining risk). It is the last thing you print."
    )


def _run(runtime: Runtime, argv: list[str], cwd, *, input=None, timeout=COMMAND_TIMEOUT_SECONDS,
         env=None):
    return runtime.runner(
        argv, cwd=str(cwd), input=input, capture_output=True, text=True, timeout=timeout,
        env=env if env is not None else runtime.environ(), check=False,
    )


def ensure_worktree(runtime: Runtime, ticket: str, refs: Callable[[str], list[str]]) -> Path:
    """A linked worktree of the ticket outside the repository (a local git operation)."""
    path = runtime.worktrees_dir / ticket
    if path.is_dir():
        return path
    runtime.worktrees_dir.mkdir(parents=True, exist_ok=True)
    known = refs(runtime.root)
    branch = cadence._branch_for(known, ticket)
    default = registry.default_branch(runtime.root) or "main"
    if branch in known:
        args = ["worktree", "add", str(path), branch]
    elif branch:  # remote only
        args = ["worktree", "add", "--track", "-b", branch, str(path), f"origin/{branch}"]
    else:
        base = f"origin/{default}" if f"origin/{default}" in known else default
        args = ["worktree", "add", "--detach", str(path), base]
    done = _run(runtime, ["git", *args], runtime.root, timeout=GIT_TIMEOUT_SECONDS)
    if done.returncode != 0:
        raise OSError("création du worktree refusée par git")
    return path


def _parse_session(stdout: object, session_id: str) -> dict:
    try:
        result = json.loads(stdout) if isinstance(stdout, str) else None
    except json.JSONDecodeError:
        result = None
    if not isinstance(result, dict) or result.get("session_id") != session_id:
        raise OSError("résultat de session ambigu")
    cost, duration = result.get("total_cost_usd"), result.get("duration_ms")
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or type(duration) is not int:
        raise OSError("coût ou durée de session ambigus")
    return result


def _save_summary(path: Path, attempt: str, text: str) -> str:
    target = path.parent / SUMMARIES_NAME / f"{attempt[:16]}.md"
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(text.encode("utf-8")[:MAX_SUMMARY_BYTES])
    return str(target)


def session_plan(runtime: Runtime, route, policy) -> dict:
    """Model, effort, child environment and host-version check of one routed session."""
    from foundry.command_runtime import _claude_child_environment
    from foundry.routing_facades import (
        claude_effort_parameters, claude_headless_host_version_requirement,
        claude_invocation_model,
    )

    model = claude_invocation_model(route.model, project_models=policy.claude_models)
    effort = claude_effort_parameters(route, invocation_model=model)["transmitted"]
    env = _claude_child_environment()
    claude_headless_host_version_requirement(
        model, "claude", runner=runtime.runner, env=env, cwd=runtime.root,
    )
    return {"kind": "session", "model": model, "effort": effort, "env": env,
            "route": {"tier": route.selected_tier, "model": model, "effort": effort}}


def _plan(runtime: Runtime, kind: str, epic: str, ticket: cadence.TicketFacts,
          attempts: Path) -> dict:
    """Everything that can fail on configuration, resolved before the attempt is claimed."""
    if kind == "start":
        return {"argv": runtime.command("issue", "start", ticket.id), "route": None}
    if kind == "merge":
        return {"argv": runtime.command("issue", "merge", ticket.id, str(ticket.pr.number)),
                "route": None}
    if kind == "open-pr":
        argv = runtime.command("issue", "openpr", ticket.id)
        summary = None
        if ticket.pr is None:  # a push to an open PR (a correction) never replaces its body
            record = last_implementation(attempts, epic, ticket.id, ticket.branch_sha,
                                         ("implement",))
            if record and record["summary_file"]:
                summary = Path(record["summary_file"]).read_text(encoding="utf-8")
        if summary:
            argv.append("--summary-stdin")
        return {"argv": argv, "route": None, "input": summary or None}
    from foundry.routing import RoutingPolicy

    policy = RoutingPolicy.load(runtime.root)
    return session_plan(runtime, policy.resolve("implementer", "claude"), policy)


def _session_id(attempt: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"foundry:campaign:{attempt}"))


def session_argv(runtime: Runtime, plan: dict, epic: str, ticket: str, session_id: str) -> list[str]:
    argv = [
        "claude", "--print", "--output-format", "json", "--model", plan["model"],
        "--max-budget-usd", f"{runtime.budget_usd:.2f}", "--permission-mode", "auto",
        "--plugin-dir", str(runtime.plugin_root), "--name", f"foundry-campaign-{epic}-{ticket}",
    ]
    if plan["effort"] is not None:
        argv.extend(["--effort", plan["effort"]])
    argv.extend(["--session-id", session_id, "--disallowedTools", *DISALLOWED_TOOLS])
    return argv


def _launch(runtime: Runtime, plan: dict, epic: str, ticket: cadence.TicketFacts,
            attempt: str, attempts: Path, refs, branch_facts) -> dict:
    """Run the one effect; return the fields of the ``finished`` record."""
    worktree = ensure_worktree(runtime, ticket.id, refs)
    if plan.get("kind") != "session":
        done = _run(runtime, plan["argv"], worktree, input=plan.get("input"))
        return {"status": "completed" if done.returncode == 0 else "failed",
                "exit_code": done.returncode, "cost_usd": None, "duration_ms": None,
                "summary_file": None, "branch_head": None, "error": None,
                "detail": (done.stderr or "")[-400:]}
    if plan.get("role") == "reviewer":
        from foundry.campaign_review import launch_review

        return launch_review(runtime, plan, epic, ticket, attempt, attempts, worktree)
    session_id = _session_id(attempt)
    argv = session_argv(runtime, plan, epic, ticket.id, session_id)
    done = _run(runtime, argv, worktree, input=plan["prompt"], timeout=IMPLEMENT_TIMEOUT_SECONDS,
                env=plan["env"])
    result = _parse_session(done.stdout, session_id)
    summary = result.get("result")
    saved = _save_summary(attempts, attempt, summary) if isinstance(summary, str) and summary else None
    branch = cadence._branch_for(refs(runtime.root), ticket.id)
    head = branch_facts(runtime.root, branch)[0] if branch else None
    ok = done.returncode == 0 and result.get("is_error") is False
    return {"status": "completed" if ok else "failed", "exit_code": done.returncode,
            "cost_usd": float(result["total_cost_usd"]), "duration_ms": result["duration_ms"],
            "summary_file": saved, "branch_head": head, "error": None, "detail": ""}


def _hide_hex(text: str) -> str:
    """Bound an error text and drop every 64-hex string: a claim capability or attempt token
    can sit in the argv a failed subprocess reports (``TimeoutExpired``)."""
    return re.sub(r"[0-9a-f]{64}", "[hex]", text)[:400]


def _stopped(decision: cadence.Decision) -> StepResult:
    outcome = {"wait-ci": "wait-ci", "await-verdict": "terminal"}.get(decision.kind, "stopped")
    return StepResult(decision, outcome)


def step(tracker, codehost, project, epic: str, runtime: Runtime, now: datetime | None = None, *,
         attempts: Path | None = None, journal: Path | None = None,
         **observe_kwargs) -> StepResult:
    """One pass and at most one effect.  ``journal`` is the approval journal."""
    path = attempts or journal_path()
    moment = now or datetime.now(timezone.utc)
    try:
        read_attempts(path)
    except AttemptJournalInvalid as exc:
        return StepResult(
            cadence.Decision("suspend", reason="attempts_journal_invalid",
                             evidence={"error": str(exc)}), "stopped")
    obs = cadence.observe(
        tracker, codehost, project, epic, runtime.root, moment, journal=journal,
        implemented=lambda ticket, sha: last_implementation(path, epic, ticket, sha) is not None,
        history=campaign_review.history(path, epic),
        **observe_kwargs,
    )
    decision = cadence.decide(obs)
    if decision.kind not in EFFECT_KINDS:
        return _stopped(decision)
    if decision.kind == "correct" and decision.reason == "ci_failing":
        # Correcting a red CI needs the CI logs, which no effect here reads (PAT-218 scope).
        return StepResult(cadence.Decision(
            "suspend", decision.ticket, "ci_failing_not_executed", decision.evidence), "stopped")
    ticket = next(t for t in obs.tickets if t.id == decision.ticket)
    refs = observe_kwargs.get("refs", cadence._git_refs)
    branch_facts = observe_kwargs.get("branch_facts", cadence._git_branch_facts)
    try:  # the approval is checked again at the moment of the effect, with its ceilings
        own = [r for r in approval.read_journal(journal) if r["epic"] == epic]
        latest = max(own, key=lambda r: (r["approved_at"], r["record_digest"]), default=None)
        granted = approval.check(
            epic, latest["preview_digest"] if latest else "0" * 64, obs.structure_observed,
            moment, journal,
        )
    except approval.ApprovalError as exc:
        return StepResult(cadence.Decision("refuse", reason=f"approval_{exc.code}"), "stopped")
    try:
        if decision.kind == "review":
            plan = campaign_review.plan_review(runtime, epic, ticket, path)
        elif decision.kind == "correct":
            plan = campaign_review.plan_correct(runtime, decision, epic, ticket, path, tracker)
            if "suspend" in plan:
                reason, evidence = plan["suspend"]
                return StepResult(cadence.Decision("suspend", ticket.id, reason, evidence),
                                  "stopped")
        else:
            plan = _plan(runtime, decision.kind, epic, ticket, path)
        if decision.kind == "implement":
            issue = tracker.get_issue(ticket.id)
            plan["prompt"] = implement_prompt(
                epic, ticket.id, getattr(issue, "title", "") or "", issue.body or "",
            )
    except Exception as exc:  # noqa: BLE001 - configuration refused before any attempt
        return StepResult(decision, "failed", {"phase": "plan", "error": type(exc).__name__,
                                               "detail": _hide_hex(str(exc))})
    attempt = identity(epic, ticket.id, decision.kind, fingerprint(ticket))
    started = {
        "schema_version": EFFECTS_CONTRACT, "event": "started", "attempt": attempt,
        "epic": epic, "ticket": ticket.id, "kind": decision.kind,
        "fingerprint": fingerprint(ticket),
        "session_id": _session_id(attempt) if decision.kind in SESSION_KINDS else None,
        "route": plan["route"], "at": _stamp(moment),
    }
    refusal = claim(path, started, (granted["max_sessions"], granted["max_sessions_per_issue"]))
    if refusal is not None:
        kind = "suspend" if refusal == "attempt_already_made" else "refuse"
        return StepResult(cadence.Decision(
            kind, ticket.id, refusal, {"attempt": attempt, "step": decision.kind}), "stopped")
    outcome = {"exit_code": None, "cost_usd": None, "duration_ms": None, "summary_file": None,
               "branch_head": None, "error": None, "detail": ""}
    try:
        outcome = _launch(runtime, plan, epic, ticket, attempt, path, refs, branch_facts)
    except Exception as exc:  # noqa: BLE001 - recorded; the attempt is not replayed
        outcome.update(status="failed", error=type(exc).__name__, detail=_hide_hex(str(exc)))
    detail = outcome.pop("detail")
    review = outcome.get("review")
    finish(path, {
        "schema_version": EFFECTS_CONTRACT, "event": "finished", "attempt": attempt,
        "epic": epic, "ticket": ticket.id, "kind": decision.kind, "at": _stamp(moment),
        **outcome,
    })
    effect = {"attempt": attempt, "status": outcome["status"], "exit_code": outcome["exit_code"],
              "cost_usd": outcome["cost_usd"], "detail": detail}
    if review is not None:  # non-blocking remarks stay in the report; PAT-141 carries them
        effect.update(review=review, report_file=outcome["summary_file"])
    return StepResult(decision, "executed" if outcome["status"] == "completed" else "failed",
                      effect)


def run(tracker, codehost, project, epic: str, runtime: Runtime, *, max_steps: int,
        poll_seconds: int, sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        **step_kwargs) -> tuple[list[StepResult], str]:
    """Repeat ``step`` until a non-progressing outcome; re-reading while CI runs is no effect."""
    results: list[StepResult] = []
    for _ in range(max_steps):
        result = step(tracker, codehost, project, epic, runtime, clock(), **step_kwargs)
        results.append(result)
        if result.outcome == "wait-ci":
            sleep(poll_seconds)
        elif result.outcome != "executed":
            return results, result.outcome
    return results, "max_steps"


def _bounded(low: float, high: float, kind=float):
    def parse(value: str):
        parsed = kind(value)
        if not low < parsed <= high:
            raise argparse.ArgumentTypeError(f"valeur hors bornes ({low}, {high}]")
        return parsed
    return parse


def add_parsers(sub) -> None:
    for name, text in (("step", "one pass and at most one effect"),
                       ("run", "repeat step until a non-progressing outcome")):
        parser = sub.add_parser(name, help=text)
        parser.add_argument("epic_id")
        parser.add_argument("--root", default=".")
        parser.add_argument("--worktrees-dir")
        parser.add_argument("--budget-usd", type=_bounded(0, MAX_BUDGET_USD),
                            default=IMPLEMENT_BUDGET_USD)
        if name == "run":
            parser.add_argument("--max-steps", type=_bounded(0, MAX_STEPS, int),
                                default=DEFAULT_MAX_STEPS)
            parser.add_argument("--poll-seconds", type=_bounded(0, MAX_POLL_SECONDS, int),
                                default=DEFAULT_POLL_SECONDS)


def cli(args) -> int:
    import foundry

    tracker, project = cadence._context(args.root)
    if project is None:
        obs = cadence.Observation(
            epic=args.epic_id, missing_capabilities=cadence.missing_capabilities(tracker))
        print(cadence.report(obs, cadence.decide(obs)))
        return 2
    root = str(Path(args.root).resolve())
    runtime = Runtime(
        root=root, budget_usd=args.budget_usd,
        worktrees_dir=Path(args.worktrees_dir) if args.worktrees_dir
        else default_worktrees_dir(root),
    )
    codehost = foundry.codehost()
    if args.verb == "step":
        results = [step(tracker, codehost, project, args.epic_id, runtime)]
        stopped = results[0].outcome
    else:
        results, stopped = run(
            tracker, codehost, project, args.epic_id, runtime, max_steps=args.max_steps,
            poll_seconds=args.poll_seconds,
        )
    print(json.dumps({
        "contract": EFFECTS_CONTRACT, "epic": args.epic_id, "stopped": stopped,
        "steps": [r.to_dict() for r in results],
    }, ensure_ascii=False, sort_keys=True, indent=2))
    return 1 if any(r.outcome == "failed" for r in results) else 0
