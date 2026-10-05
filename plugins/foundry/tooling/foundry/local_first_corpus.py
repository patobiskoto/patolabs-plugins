"""PAT-107: replayable corpus for the PAT-19 local-first qualification (protocol v1).

Pure local git + pytest. No model is launched, no cloud/tracker/network call is made:
the candidate PRs come from a committed snapshot, the draw is a seeded hash ranking over
that snapshot, a bundle is a disposable git worktree at the base SHA without the protected
tests, and the judge restores the protected tests from the merged SHA and runs only them.

Run as ``python3 -m foundry.local_first_corpus --help`` from ``plugins/foundry/tooling``.
See ``docs/qualification/pat-19-corpus-v1.md``.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

SNAPSHOT_SCHEMA = "foundry.local-first-corpus-snapshot.v1"
MANIFEST_SCHEMA = "foundry.local-first-corpus-manifest.v1"
DRAW_ALGORITHM = "sha256-rank-v1"  # rank = sha256(f"{seed}:{pr_number}"), ascending
MAX_CHANGED_LINES = 300
COMPARISON_SIZE = 6
SCREENING_SIZE = 6
PLUGIN_PREFIX = "plugins/foundry/"
TESTS_PREFIX = "plugins/foundry/tests/"
SOURCE_PREFIXES = ("plugins/foundry/tooling/", "plugins/foundry/hooks/")
PYTEST_TIMEOUT_SECONDS = 900

# Topic exclusions of the protocol (section 3). Each is an explicit rule over the changed
# non-documentation paths and the PR title; judgment calls go in the committed manual list.
TOPIC_RULES: dict[str, dict[str, re.Pattern[str]]] = {
    "gates_evaluation": {
        "path": re.compile(r"(escalation|benchmark|measurement|harness|guard_|delivery_contract|"
                           r"cost_attribution|conformance|routing)", re.I),
        "title": re.compile(r"(\bgates?\b|merge-pr|revue créditée|remédiation|diagnostics? "
                            r"techniques?|benchmark|conformité)", re.I),
    },
    "authority": {
        "path": re.compile(r"(auth|oauth|permission|waiver|authority)", re.I),
        "title": re.compile(r"(authent|autorit|permission|dérogation|identit|sécurité)", re.I),
    },
    "data_migration": {
        "path": re.compile(r"(migrat|cutover)", re.I),
        "title": re.compile(r"(migr|basculer|bascule|cutover)", re.I),
    },
    "sensitive_concurrency": {
        "path": re.compile(r"(lock|lease|reservation|concurren|worker|devhub_host|handoff)", re.I),
        "title": re.compile(r"(concurren|verrou|lease|idempot|crash|worker|handoff)", re.I),
    },
}


class CorpusError(ValueError):
    pass


# --------------------------------------------------------------------------- git helpers

def _git(repo: Path, *args: str, check: bool = True, text: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=text,
                          check=False)
    if check and proc.returncode != 0:
        err = proc.stderr if text else proc.stderr.decode("utf-8", "replace")
        raise CorpusError(f"git {' '.join(args[:3])} failed: {err.strip()[:300]}")
    return proc.stdout


def _blob(repo: Path, sha: str, path: str) -> str:
    return _git(repo, "show", f"{sha}:{path}")


def _is_doc(path: str) -> bool:
    return path.endswith((".md", ".json", ".txt")) or "/docs/" in path


# ------------------------------------------------------------------ protected tests (d)

def _function_index(source: str) -> tuple[dict[str, ast.AST], dict[str, str], str]:
    """Qualified name -> node for top-level functions and class methods, plus a dump."""
    tree = ast.parse(source)
    nodes: dict[str, ast.AST] = {}
    dumps: dict[str, str] = {}
    other: list[str] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes[stmt.name] = stmt
        elif isinstance(stmt, ast.ClassDef):
            for sub in stmt.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    nodes[f"{stmt.name}::{sub.name}"] = sub
        elif not isinstance(stmt, (ast.Import, ast.ImportFrom)):
            other.append(ast.dump(stmt))
    for name, node in nodes.items():
        dumps[name] = ast.dump(node)
    return nodes, dumps, "\n".join(other)


def changed_test_nodes(base_source: str | None, head_source: str) -> dict[str, Any]:
    """Added/modified test functions of one test module (base_source None = new file)."""
    if base_source is None:
        return {"selection": "file", "nodes": []}
    _, base_dumps, base_other = _function_index(base_source)
    _, head_dumps, head_other = _function_index(head_source)
    changed = sorted(name for name, dump in head_dumps.items()
                     if base_dumps.get(name) != dump)
    tests = [n for n in changed if n.rsplit("::", 1)[-1].startswith("test")]
    helpers = [n for n in changed if n not in tests]
    whole_file = bool(helpers) or head_other != base_other
    return {"selection": "file" if whole_file else "nodes", "nodes": tests, "helper_changed": helpers,
            "module_level_changed": head_other != base_other}


def protected_tests(repo: Path, base: str, head: str,
                    files: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Protected tests = test modules/functions ADDED or MODIFIED by the PR.

    ``kind`` is ``test`` for ``tests/test_*.py`` (selected by node id or whole file) and
    ``support`` for every other added/modified file under ``tests/`` (conftest, fixtures,
    helpers): restored by the judge from the merged SHA, never run on their own."""
    entries: list[dict[str, Any]] = []
    for item in files:
        path, status = item["path"], item["status"]
        if not path.startswith(TESTS_PREFIX) or status == "D":
            continue
        rel = path[len(TESTS_PREFIX):]
        if not (rel.startswith("test_") and rel.endswith(".py") and "/" not in rel):
            entries.append({"path": path, "status": status, "kind": "support",
                            "selection": "file", "nodes": []})
        elif status == "A":
            entries.append({"path": path, "status": "A", "kind": "test",
                            "selection": "file", "nodes": []})
        else:
            info = changed_test_nodes(_blob(repo, base, path), _blob(repo, head, path))
            if info["nodes"] or info["selection"] == "file":
                entries.append({"path": path, "status": "M", "kind": "test", **info})
    return {"entries": entries}


def protected_count(entries: Sequence[Mapping[str, Any]]) -> int:
    """Selected test functions, counting a whole-file selection as one unit."""
    return sum(len(e["nodes"]) if e["selection"] == "nodes" else 1 for e in entries
               if e["kind"] == "test")


# ------------------------------------------------------------------------- snapshot (a)

_ISSUE = (re.compile(r"^\[([A-Z]+-\d+)\]"), re.compile(r"\(([A-Z]+-\d+)\)"),
          re.compile(r"\b([A-Z]{2,}-\d+)\b"))


def issue_of(title: str) -> str | None:
    for pattern in _ISSUE:
        found = pattern.search(title)
        if found:
            return found.group(1)
    return None


def ac_text_of(body: str) -> str:
    """The PR description up to its Validation/Documentation sections (AC proxy)."""
    cut = re.split(r"^##\s+(Validation|Documentation status|Test plan)\b", body or "",
                   maxsplit=1, flags=re.M)[0]
    cut = re.split(r"^Closes (tracker issue|PAT-\d+)", cut, maxsplit=1, flags=re.M)[0]
    return cut.strip()


def build_snapshot(repo: Path, prs: Sequence[Mapping[str, Any]], ref: str = "HEAD",
                   default_branch: str = "main",
                   ac_overrides: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Freeze merged-PR facts. ``prs`` is the JSON of ``gh pr list --state merged``.

    The acceptance criteria default to the PR description (``pr_body_summary``); an
    ``ac_overrides`` entry ``{"<pr>": "<AC text>"}`` replaces it (``tracker_issue``)."""
    first_parent = set(_git(repo, "log", "--first-parent", "--format=%H", ref).split())
    tasks = []
    for pr in sorted(prs, key=lambda p: p["number"]):
        merge = (pr.get("mergeCommit") or {}).get("oid")
        parents = (_git(repo, "rev-list", "--parents", "-n1", merge, check=False).split()[1:]
                   if merge else [])
        squash = len(parents) == 1
        kind = ("squash_on_main" if squash and merge in first_parent
                and pr.get("baseRefName") == default_branch
                else "squash_on_stacked_branch" if squash else "not_replayable")
        record: dict[str, Any] = {
            "pr": pr["number"], "title": pr["title"], "issue": issue_of(pr["title"]),
            "merge_kind": kind, "base_sha": None, "head_sha": merge,
            "pr_head_sha": pr.get("headRefOid"), "files": [],
            "ac_text": ac_text_of(pr.get("body", "")), "ac_source": "pr_body_summary"}
        if (ac_overrides or {}).get(str(pr["number"])):
            record["ac_text"] = ac_overrides[str(pr["number"])]  # type: ignore[index]
            record["ac_source"] = "tracker_issue"
        if squash:
            base = parents[0]
            record["base_sha"] = base
            names = _git(repo, "diff", "--no-renames", "--name-status", base, merge)
            stats = _git(repo, "diff", "--no-renames", "--numstat", base, merge)
            status = {line.split("\t")[1]: line.split("\t")[0][0]
                      for line in names.splitlines() if line}
            for line in stats.splitlines():
                added, deleted, path = line.split("\t", 2)
                record["files"].append({
                    "path": path, "status": status[path],
                    "added": int(added) if added != "-" else 0,
                    "deleted": int(deleted) if deleted != "-" else 0})
            record["protected"] = protected_tests(repo, base, merge, record["files"])
        tasks.append(record)
    return {"schema": SNAPSHOT_SCHEMA, "ref_sha": _git(repo, "rev-parse", ref).strip(),
            "default_branch": default_branch, "prs": tasks}


# ------------------------------------------------------------ criteria and draw (b)(c)

def changed_lines(task: Mapping[str, Any]) -> int:
    return sum(f["added"] + f["deleted"] for f in task["files"])


def exclusion_reasons(task: Mapping[str, Any],
                      manual: Mapping[str, str] | None = None) -> list[str]:
    """Every protocol criterion the PR fails; empty list = eligible. Never silent."""
    reasons: list[str] = []
    if task.get("merge_kind") == "not_replayable":
        return ["not_single_parent_squash_commit"]
    if not task.get("issue"):
        reasons.append("no_issue_id")
    if changed_lines(task) > MAX_CHANGED_LINES:
        reasons.append("over_300_changed_lines")
    paths = [f["path"] for f in task["files"]]
    source = [p for p in paths if p.startswith(SOURCE_PREFIXES) and p.endswith(".py")]
    tests = [p for p in paths if p.startswith(TESTS_PREFIX) and p.endswith(".py")]
    if not source:
        reasons.append("documentation_only" if all(_is_doc(p) for p in paths)
                       else "no_source_code_under_plugins_foundry")
    if not tests:
        reasons.append("no_tests_under_plugins_foundry")
    prot = task.get("protected", {})
    if not any(e["kind"] == "test" for e in prot.get("entries", [])):
        reasons.append("no_protected_tests")
    if not task.get("ac_text"):
        reasons.append("empty_acceptance_criteria")
    code_paths = [p for p in paths if p.startswith(SOURCE_PREFIXES) and p.endswith(".py")]
    for topic, rule in TOPIC_RULES.items():
        if rule["title"].search(task["title"]) or any(rule["path"].search(p) for p in code_paths):
            reasons.append(f"topic:{topic}")
    note = (manual or {}).get(str(task["pr"]))
    if note:
        reasons.append(f"manual:{note}")
    return reasons


def rank_key(seed: str, pr: int) -> str:
    return hashlib.sha256(f"{seed}:{pr}".encode()).hexdigest()


def draw(snapshot: Mapping[str, Any], seed: str,
         manual: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Deterministic draw: eligible PRs ranked by sha256(seed:pr); the first 6 are the
    comparison set, the next 6 the local screening set (``tamis``, never shared with the
    comparison), the rest is the ordered replacement queue."""
    eligible, excluded = [], []
    for task in snapshot["prs"]:
        reasons = exclusion_reasons(task, manual)
        if reasons:
            excluded.append({"pr": task["pr"], "issue": task["issue"],
                             "changed_lines": changed_lines(task) if task["files"] else None,
                             "reasons": reasons})
        else:
            eligible.append(task["pr"])
    ranked = sorted(eligible, key=lambda pr: (rank_key(seed, pr), pr))
    return {"seed": seed, "algorithm": DRAW_ALGORITHM, "eligible_count": len(eligible),
            "comparison": ranked[:COMPARISON_SIZE],
            "screening": ranked[COMPARISON_SIZE:COMPARISON_SIZE + SCREENING_SIZE],
            "replacement_queue": ranked[COMPARISON_SIZE + SCREENING_SIZE:],
            "excluded": excluded}


# --------------------------------------------------------------- bundle builder (e)

def task_statement(task: Mapping[str, Any]) -> str:
    """The tracker statement (title and body as written), verbatim: never the PR
    description, the merged diff or the protected tests. Fails closed otherwise."""
    if task.get("ac_source") != "tracker_issue":
        raise CorpusError(f"PR {task.get('pr')}: statement is not a tracker issue "
                          f"(ac_source={task.get('ac_source')!r}); refusing to build a bundle")
    return task["ac_text"]


def _remove_functions(source: str, qualnames: Sequence[str]) -> str:
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    edits: list[tuple[int, int, str]] = []  # (start0, end0_exclusive, replacement)
    wanted = set(qualnames)

    def span(node: ast.AST) -> tuple[int, int]:
        first = min([node.lineno] + [d.lineno for d in node.decorator_list])  # type: ignore[attr-defined]
        return first - 1, node.end_lineno  # type: ignore[attr-defined]

    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name in wanted:
            start, end = span(stmt)
            edits.append((start, end, ""))
        elif isinstance(stmt, ast.ClassDef):
            members = [s for s in stmt.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and f"{stmt.name}::{s.name}" in wanted]
            removing_all = members and len(members) == len(stmt.body)
            for idx, sub in enumerate(members):
                start, end = span(sub)
                keep = f"{' ' * sub.col_offset}pass\n" if removing_all and idx == 0 else ""
                edits.append((start, end, keep))
    for start, end, repl in sorted(edits, reverse=True):
        lines[start:end] = [repl] if repl else []
    result = "".join(lines)
    ast.parse(result)
    return result


def build_bundle(repo: Path, task: Mapping[str, Any], dest: Path) -> Path:
    """Disposable worktree at the base SHA without the protected tests (e)."""
    statement = task_statement(task)  # fail closed before touching git
    dest = dest.resolve()
    if dest.exists():
        raise CorpusError(f"bundle destination already exists: {dest}")
    _git(repo, "worktree", "add", "--detach", str(dest), task["base_sha"])
    for entry in task["protected"]["entries"]:
        target = dest / entry["path"]
        if entry["status"] == "A":
            target.unlink(missing_ok=True)
        elif entry["kind"] == "test":
            removed = _remove_functions(target.read_text(encoding="utf-8"), entry["nodes"])
            target.write_text(removed, encoding="utf-8")
    (dest / "TASK.md").write_text(statement, encoding="utf-8")
    return dest


def remove_bundle(repo: Path, dest: Path) -> None:
    _git(repo, "worktree", "remove", "--force", str(dest), check=False)
    shutil.rmtree(dest, ignore_errors=True)
    _git(repo, "worktree", "prune", check=False)


def apply_solution(repo: Path, task: Mapping[str, Any], dest: Path) -> None:
    """Apply the merged diff (minus protected files) onto a bundle."""
    protected = {e["path"] for e in task["protected"]["entries"]}
    paths = [f["path"] for f in task["files"] if f["path"] not in protected]
    if not paths:
        return
    patch = _git(repo, "diff", "--binary", "--no-renames", task["base_sha"], task["head_sha"],
                 "--", *paths, text=False)
    proc = subprocess.run(["git", "-C", str(dest), "apply", "--whitespace=nowarn", "-"],
                          input=patch, capture_output=True, check=False)
    if proc.returncode != 0:
        raise CorpusError(f"merged solution does not apply: {proc.stderr.decode()[:300]}")


# --------------------------------------------------------------------------- judge (f)

def judge(repo: Path, task: Mapping[str, Any], candidate: Path) -> dict[str, Any]:
    """Restore the protected tests from the merged SHA over the candidate worktree, run
    only them and return a mechanical verdict. Tests the candidate wrote never count: the
    protected paths are overwritten and only their node ids are run."""
    candidate = candidate.resolve()
    selected: list[str] = []
    for entry in task["protected"]["entries"]:
        content = _git(repo, "show", f"{task['head_sha']}:{entry['path']}", text=False)
        target = candidate / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        if entry["kind"] == "test":
            if entry["selection"] == "nodes":
                selected.extend(f"{entry['path'][len(PLUGIN_PREFIX):]}::{n}" for n in entry["nodes"])
            else:
                selected.append(entry["path"][len(PLUGIN_PREFIX):])
    # Neutralise candidate edits of the test harness itself (merged-SHA versions).
    for rel in ("tests/conftest.py", "pytest.ini"):
        (candidate / PLUGIN_PREFIX / rel).write_bytes(
            _git(repo, "show", f"{task['head_sha']}:{PLUGIN_PREFIX}{rel}", text=False))
    with tempfile.TemporaryDirectory(prefix="foundry-judge-") as tmp:
        junit = Path(tmp) / "junit.xml"
        env = {k: v for k, v in os.environ.items() if not k.startswith("FOUNDRY_")}
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTEST_ADDOPTS": ""})
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               f"--junitxml={junit}", *selected]
        try:
            proc = subprocess.run(cmd, cwd=candidate / PLUGIN_PREFIX.rstrip("/"), env=env,
                                  capture_output=True, text=True, timeout=PYTEST_TIMEOUT_SECONDS,
                                  check=False)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            return _verdict("timeout", selected, 0, 0, 0, 0, None)
        counts = _junit_counts(junit)
    passed, failed, errors, skipped = counts
    return _verdict(None, selected, passed, failed, errors, skipped, code)


def _junit_counts(path: Path) -> tuple[int, int, int, int]:
    if not path.exists():
        return 0, 0, 0, 0
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return 0, 0, 0, 0
    total, failed = int(suite.get("tests", 0)), int(suite.get("failures", 0))
    errors, skipped = int(suite.get("errors", 0)), int(suite.get("skipped", 0))
    return total - failed - errors - skipped, failed, errors, skipped


def _verdict(note: str | None, selected: Sequence[str], passed: int, failed: int,
             errors: int, skipped: int, exit_code: int | None) -> dict[str, Any]:
    accepted = (note is None and exit_code == 0 and passed >= 1 and failed == 0
                and errors == 0 and skipped == 0)
    return {"verdict": "ACCEPTED" if accepted else "REFUSED",
            "selected": list(selected), "passed": passed, "failed": failed,
            "errors": errors, "skipped": skipped, "pytest_exit_code": exit_code,
            "note": note}


# ----------------------------------------------------------------- verification (g)

def verify_task(repo: Path, task: Mapping[str, Any], workdir: Path) -> dict[str, Any]:
    """The judge must ACCEPT the merged solution and REFUSE the base without it."""
    result: dict[str, Any] = {"pr": task["pr"], "issue": task["issue"]}
    for label, with_solution in (("with_merged_diff", True), ("without_merged_diff", False)):
        dest = workdir / f"pr{task['pr']}-{label}"
        try:
            build_bundle(repo, task, dest)
            if with_solution:
                apply_solution(repo, task, dest)
            result[label] = judge(repo, task, dest)
        except CorpusError as exc:
            result[label] = {"verdict": "ERROR", "note": str(exc)[:300]}
        finally:
            remove_bundle(repo, dest)
    ok = (result["with_merged_diff"]["verdict"] == "ACCEPTED"
          and result["without_merged_diff"]["verdict"] == "REFUSED")
    result["discarded_reason"] = None
    if not ok:
        if result["with_merged_diff"]["verdict"] != "ACCEPTED":
            result["discarded_reason"] = "judge_does_not_accept_merged_solution"
        else:
            result["discarded_reason"] = "judge_accepts_base_without_solution"
    return result


def finalize(snapshot: Mapping[str, Any], drawn: Mapping[str, Any], verify: Any,
             ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Replace each failing slot by the next passing entry of the replacement queue.

    ``verify(pr)`` returns a verification result (see ``verify_task``); results are
    returned too so the evidence keeps every task that was tried."""
    queue = list(drawn["replacement_queue"])
    tried: dict[int, Mapping[str, Any]] = {}
    discarded: list[dict[str, Any]] = []

    def settle(pr: int) -> int:
        current: int | None = pr
        while current is not None:
            tried[current] = verify(current)
            if tried[current]["discarded_reason"] is None:
                return current
            discarded.append({"pr": current, "reason": tried[current]["discarded_reason"]})
            current = queue.pop(0) if queue else None
        raise CorpusError("replacement queue exhausted")

    chosen = {group: [settle(pr) for pr in drawn[group]] for group in ("comparison", "screening")}
    by_pr = {t["pr"]: t for t in snapshot["prs"]}
    if set(chosen["comparison"]) & set(chosen["screening"]):
        raise CorpusError("a comparison task must never be a screening task")
    corpus = {group: [_task_entry(by_pr[p]) for p in prs] for group, prs in chosen.items()}
    corpus["discarded_at_verification"] = discarded
    return corpus, [tried[k] for k in sorted(tried)]


def _task_entry(task: Mapping[str, Any]) -> dict[str, Any]:
    return {"issue": task["issue"], "pr": task["pr"], "title": task["title"],
            "base_sha": task["base_sha"], "head_sha": task["head_sha"],
            "pr_head_sha": task["pr_head_sha"], "changed_lines": changed_lines(task),
            "protected_tests_count": protected_count(task["protected"]["entries"]),
            "acceptance_criteria": task["ac_text"], "ac_source": task["ac_source"],
            "protected_tests": task["protected"]["entries"]}


# ------------------------------------------------------------------------------- CLI

def _load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(data: Any, out: str | None) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    if out:
        Path(out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="foundry.local_first_corpus", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="freeze merged PRs from `gh pr list --state merged "
                       "--json number,title,body,mergeCommit,baseRefOid,headRefOid,baseRefName`")
    s.add_argument("--repo", default=".")
    s.add_argument("--prs-json", required=True)
    s.add_argument("--ref", default="HEAD")
    s.add_argument("--ac-overrides", help="JSON {\"<pr>\": \"AC text\"} captured from the tracker")
    s.add_argument("--out")
    d = sub.add_parser("draw", help="apply the criteria and draw 6 comparison + 6 screening tasks and the queue")
    d.add_argument("--snapshot", required=True)
    d.add_argument("--seed", required=True)
    d.add_argument("--manual-exclusions", help="JSON {\"<pr>\": \"reason\"}")
    d.add_argument("--out")
    b = sub.add_parser("bundle", help="disposable worktree at the base SHA without protected tests")
    b.add_argument("--repo", default=".")
    b.add_argument("--snapshot", required=True)
    b.add_argument("--pr", type=int, required=True)
    b.add_argument("--dest", required=True)
    j = sub.add_parser("judge", help="mechanical verdict for a candidate worktree")
    j.add_argument("--repo", default=".")
    j.add_argument("--snapshot", required=True)
    j.add_argument("--pr", type=int, required=True)
    j.add_argument("--candidate", required=True)
    v = sub.add_parser("verify", help="judge ACCEPTS merged diff, REFUSES base; replace failures")
    v.add_argument("--repo", default=".")
    v.add_argument("--snapshot", required=True)
    v.add_argument("--seed", required=True)
    v.add_argument("--manual-exclusions")
    v.add_argument("--workdir", required=True, help="scratch directory for disposable worktrees")
    v.add_argument("--out")
    args = parser.parse_args(argv)
    repo = Path(getattr(args, "repo", ".")).resolve()

    if args.cmd == "snapshot":
        overrides = _load(args.ac_overrides) if args.ac_overrides else None
        _dump(build_snapshot(repo, _load(args.prs_json), args.ref, ac_overrides=overrides),
              args.out)
        return 0
    snapshot = _load(args.snapshot)
    by_pr = {t["pr"]: t for t in snapshot["prs"]}
    if args.cmd == "draw":
        manual = _load(args.manual_exclusions) if args.manual_exclusions else {}
        _dump(draw(snapshot, args.seed, manual), args.out)
    elif args.cmd == "bundle":
        print(build_bundle(repo, by_pr[args.pr], Path(args.dest)))
    elif args.cmd == "judge":
        result = judge(repo, by_pr[args.pr], Path(args.candidate))
        _dump(result, None)
        return 0 if result["verdict"] == "ACCEPTED" else 1
    elif args.cmd == "verify":
        manual = _load(args.manual_exclusions) if args.manual_exclusions else {}
        drawn = draw(snapshot, args.seed, manual)
        workdir = Path(args.workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        corpus, evidence = finalize(snapshot, drawn,
                                    lambda pr: verify_task(repo, by_pr[pr], workdir))
        digest = hashlib.sha256(Path(args.snapshot).read_bytes()).hexdigest()
        _dump({"schema": MANIFEST_SCHEMA, "snapshot_sha256": digest, "seed": args.seed, "algorithm": DRAW_ALGORITHM,
               "criteria": {"max_changed_lines": MAX_CHANGED_LINES},
               "draw": drawn, **corpus, "verification": evidence}, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
