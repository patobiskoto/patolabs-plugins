"""PAT-107: replayable corpus for the PAT-19 local-first qualification (protocol v1).

Pure local git + pytest. No model is launched, no cloud/tracker/network call is made:
the candidate PRs come from a committed snapshot, the draw is a seeded hash ranking over
that snapshot, a bundle is an export of the base-SHA tree without the protected tests inside a
brand-new one-commit repository (no link to the developer repository), and the judge restores the
protected tests from the merged SHA and runs only them in a scrubbed environment.

Run as ``python3 -m foundry.local_first_corpus --help`` from ``plugins/foundry/tooling``.
See ``docs/qualification/pat-19-corpus-v1.md``.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
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
DEFAULT_BRANCH_FOR_REPLAY = "main"
STATEMENTS_FILE = "pat-19-corpus-statements-v1.json"
STATEMENTS_SCHEMA = "foundry.local-first-corpus-statements.v1"
OVERRIDE_DECISIONS = ("include", "exclude")
# A candidate that creates or edits one of these (anywhere in its bundle) is REFUSED: they
# change how pytest collects, configures or boots, not what the product does.
TRIPWIRE_NAMES = frozenset({"conftest.py", "pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg",
                            "sitecustomize.py", "usercustomize.py"})
TRIPWIRE_SUFFIXES = (".pth",)
BUNDLE_AUTHOR = {"GIT_AUTHOR_NAME": "Corpus Bundle", "GIT_AUTHOR_EMAIL": "corpus@example.invalid",
                 "GIT_COMMITTER_NAME": "Corpus Bundle", "GIT_COMMITTER_EMAIL": "corpus@example.invalid",
                 "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
                 "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000"}
CORPUS_LIMITS = (
    "Comparison and screening sets are disjoint by ticket but not independent: PAT-33 (screening), "
    "PAT-34 and PAT-35 (comparison) and PAT-36 (screening) are consecutive commits of one stacked "
    "branch, PAT-35 and PAT-36 are twin fixes of import_adr_batch, PAT-39 (screening) precedes "
    "PAT-41 (comparison), and 8 of the 12 tasks touch tests/test_linear_tracker.py.",
    "Statements are the tracker issues as of 2026-10-05 (checkboxes reset), not as of the base SHA.",
    "The judge does not isolate product code: it is imported in the pytest process and could "
    "tamper with pytest; non-protected support files (fixtures, data) can be edited by the "
    "candidate and are only reported.",
)

# Topic exclusions of the protocol (section 3). Each is an explicit rule over the changed
# non-documentation paths and the PR title; judgment calls go in the committed overrides list.
# Title patterns are written without accents and matched against the folded title (``_fold``).
TOPIC_RULES: dict[str, dict[str, re.Pattern[str]]] = {
    "gates_evaluation": {
        "path": re.compile(r"(escalation|benchmark|measurement|harness|guard_|delivery_contract|"
                           r"cost_attribution|conformance|routing)", re.I),
        "title": re.compile(r"(\bgates?\b|merge-pr|revue creditee|remediation|diagnostics? "
                            r"techniques?|benchmark|conformite)", re.I),
    },
    "authority": {
        "path": re.compile(r"(auth|oauth|permission|waiver|authority)", re.I),
        "title": re.compile(r"(authent|autorit|permission|derogation|identit|securite)", re.I),
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


def _fold(text: str) -> str:
    """Accent- and case-insensitive form used for title matching."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


# ----------------------------------------------------------------- statements and scrub (N10/N11)

_LINEAR_LINK = re.compile(r"\[([^\]]*)\]\(\s*<?https?://(?:[\w.-]+\.)?linear\.app/[^)\s]*>?\s*\)")
_LINEAR_URL = re.compile(r"<?https?://(?:[\w.-]+\.)?linear\.app/[^\s)>\]]*>?")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
UUID_PLACEHOLDER = "<uuid removed>"


def scrub_statement(text: str) -> str:
    """Public-repository scrub: Markdown links to linear.app keep their link text (issue ids
    stay), bare linear.app URLs and every UUID are removed; everything else is verbatim."""
    text = _LINEAR_LINK.sub(r"\1", text)
    text = _LINEAR_URL.sub("", text)
    return _UUID.sub(UUID_PLACEHOLDER, text)


def load_statements(path: Path) -> dict[str, Any]:
    """The committed tracker statements file (the only source of ``tracker_issue``).

    Fails closed on a wrong file name, schema or an unscrubbed text; the returned dict carries
    the sha256 of the file bytes, recorded in the snapshot."""
    path = Path(path)
    if path.name != STATEMENTS_FILE:
        raise CorpusError(f"statements must come from the committed {STATEMENTS_FILE}, not {path.name}")
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if data.get("schema") != STATEMENTS_SCHEMA or not isinstance(data.get("statements"), dict):
        raise CorpusError(f"{path.name}: unexpected schema")
    for pr, text in data["statements"].items():
        if not isinstance(text, str) or not text or scrub_statement(text) != text:
            raise CorpusError(f"{path.name}: statement of PR {pr} is empty or not scrubbed")
    return {"file": STATEMENTS_FILE, "captured_on": data["captured_on"],
            "sha256": hashlib.sha256(raw).hexdigest(), "statements": data["statements"]}


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

def _parse(source: str, filename: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        raise CorpusError(f"{filename}: cannot parse ({exc.msg}, line {exc.lineno})") from exc


def _function_index(source: str, filename: str = "<source>"
                    ) -> tuple[dict[str, ast.AST], dict[str, str], str]:
    """Qualified name -> node for top-level functions and class methods, plus a dump."""
    tree = _parse(source, filename)
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


def changed_test_nodes(base_source: str | None, head_source: str,
                       filename: str = "<source>") -> dict[str, Any]:
    """Added/modified test functions of one test module (base_source None = new file)."""
    if base_source is None:
        return {"selection": "file", "nodes": []}
    _, base_dumps, base_other = _function_index(base_source, filename)
    _, head_dumps, head_other = _function_index(head_source, filename)
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
            info = changed_test_nodes(_blob(repo, base, path), _blob(repo, head, path), path)
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


def _replay_problem(repo: Path, number: int, merge: str | None, parents: Sequence[str]) -> str | None:
    """Why a merge commit is not a replayable squash of PR ``number`` (None = replayable).

    One parent is not enough: a rebase-merge also has one parent. A GitHub squash subject
    ends with ``(#<n>)`` and its parent does not."""
    if not merge or len(parents) != 1:
        return "not_single_parent_squash_commit"
    marker = f"(#{number})"
    if not _git(repo, "log", "-1", "--format=%s", merge).strip().endswith(marker):
        return "merge_commit_not_a_squash_of_this_pr"
    if _git(repo, "log", "-1", "--format=%s", parents[0]).strip().endswith(marker):
        return "rebase_merge_of_several_commits"
    return None


def build_snapshot(repo: Path, prs: Sequence[Mapping[str, Any]], ref: str = "HEAD",
                   default_branch: str = "main",
                   statements: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Freeze merged-PR facts. ``prs`` is the JSON of ``gh pr list --state merged``.

    The acceptance criteria default to the PR description (``pr_body_summary``, scrubbed). A
    PR present in ``statements`` (the result of ``load_statements`` on the committed file) takes
    the tracker statement instead (``tracker_issue``); nothing else can set that label."""
    if statements is not None and not statements.get("sha256"):
        raise CorpusError("statements must come from load_statements() on the committed file")
    first_parent = set(_git(repo, "log", "--first-parent", "--format=%H", ref).split())
    tasks = []
    for pr in sorted(prs, key=lambda p: p["number"]):
        merge = (pr.get("mergeCommit") or {}).get("oid")
        parents = (_git(repo, "rev-list", "--parents", "-n1", merge, check=False).split()[1:]
                   if merge else [])
        problem = _replay_problem(repo, pr["number"], merge, parents)
        squash = problem is None
        kind = ("squash_on_main" if squash and merge in first_parent
                and pr.get("baseRefName") == default_branch
                else "squash_on_stacked_branch" if squash else "not_replayable")
        record: dict[str, Any] = {
            "pr": pr["number"], "title": pr["title"], "issue": issue_of(pr["title"]),
            "merge_kind": kind, "replay_problem": problem, "base_ref": pr.get("baseRefName"),
            "base_sha": None, "head_sha": merge,
            "pr_head_sha": pr.get("headRefOid"), "files": [],
            "ac_text": scrub_statement(ac_text_of(pr.get("body", ""))),
            "ac_source": "pr_body_summary"}
        if statements and statements["statements"].get(str(pr["number"])):
            record["ac_text"] = statements["statements"][str(pr["number"])]
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
    provenance = ({"file": statements["file"], "sha256": statements["sha256"],
                   "captured_on": statements["captured_on"],
                   "as_of": "capture date, not the base SHA"} if statements else None)
    return {"schema": SNAPSHOT_SCHEMA, "ref_sha": _git(repo, "rev-parse", ref).strip(),
            "default_branch": default_branch, "statements": provenance, "prs": tasks}


# ------------------------------------------------------------ criteria and draw (b)(c)

def changed_lines(task: Mapping[str, Any]) -> int:
    return sum(f["added"] + f["deleted"] for f in task["files"])


def _override_for(overrides: Mapping[str, Any] | None, pr: Any) -> Mapping[str, str] | None:
    item = (overrides or {}).get(str(pr))
    if item is None:
        return None
    if (not isinstance(item, Mapping) or item.get("decision") not in OVERRIDE_DECISIONS
            or not str(item.get("reason", "")).strip()):
        raise CorpusError(f"override of PR {pr} needs a decision {OVERRIDE_DECISIONS} and a reason")
    return item


def _criteria_reasons(task: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if task.get("merge_kind") == "not_replayable":
        return [task.get("replay_problem") or "not_single_parent_squash_commit"]
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
    title = _fold(task["title"])
    for topic, rule in TOPIC_RULES.items():
        if rule["title"].search(title) or any(rule["path"].search(p) for p in source):
            reasons.append(f"topic:{topic}")
    return reasons


def exclusion_reasons(task: Mapping[str, Any],
                      overrides: Mapping[str, Any] | None = None) -> list[str]:
    """Every protocol criterion the PR fails; empty list = eligible. Never silent.

    A recorded override ``{"decision": "exclude"|"include", "reason": ...}`` adds a
    ``manual:`` reason, or lifts the ``topic:*`` (keyword/topic) reasons only: size, code,
    tests, replayability and statement criteria are never lifted."""
    reasons = _criteria_reasons(task)
    override = _override_for(overrides, task["pr"])
    if override and override["decision"] == "exclude":
        reasons.append(f"manual:{override['reason']}")
    elif override:
        reasons = [r for r in reasons if not r.startswith("topic:")]
    return reasons


def rank_key(seed: str, pr: int) -> str:
    return hashlib.sha256(f"{seed}:{pr}".encode()).hexdigest()


def draw(snapshot: Mapping[str, Any], seed: str,
         overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Deterministic draw: eligible PRs ranked by sha256(seed:pr); the first 6 are the
    comparison set, the next 6 the local screening set (``tamis``, never shared with the
    comparison), the rest is the ordered replacement queue."""
    eligible, excluded, applied = [], [], []
    for task in snapshot["prs"]:
        override = _override_for(overrides, task["pr"])
        if override:
            lifted = ([r for r in _criteria_reasons(task) if r.startswith("topic:")]
                      if override["decision"] == "include" else [])
            applied.append({"pr": task["pr"], "issue": task["issue"],
                            "decision": override["decision"], "reason": override["reason"],
                            "lifted_reasons": lifted})
        reasons = exclusion_reasons(task, overrides)
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
            "overrides": applied, "excluded": excluded}


# --------------------------------------------------------------- bundle builder (e)

def task_statement(task: Mapping[str, Any]) -> str:
    """The tracker statement (title and body as written), verbatim: never the PR
    description, the merged diff or the protected tests. Fails closed otherwise."""
    if task.get("ac_source") != "tracker_issue":
        raise CorpusError(f"PR {task.get('pr')}: statement is not a tracker issue "
                          f"(ac_source={task.get('ac_source')!r}); refusing to build a bundle")
    return task["ac_text"]


def _remove_functions(source: str, qualnames: Sequence[str], filename: str = "<source>") -> str:
    """Drop the named test functions with their decorators and the comment lines directly
    above them (a comment may describe the removed test)."""
    tree = _parse(source, filename)
    lines = source.splitlines(keepends=True)
    edits: list[tuple[int, int, str]] = []  # (start0, end0_exclusive, replacement)
    wanted = set(qualnames)

    def span(node: ast.AST) -> tuple[int, int]:
        first = min([node.lineno] + [d.lineno for d in node.decorator_list])  # type: ignore[attr-defined]
        start = first - 1
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        return start, node.end_lineno  # type: ignore[attr-defined]

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
    _parse(result, filename)
    return result


_CREATED_BUNDLES: set[Path] = set()  # directories build_bundle created in this process


def _checkout_roots(repo: Path) -> list[Path]:
    top = Path(_git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    common = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    return [top, common.resolve().parent]


def _bundle_git(dest: Path, *args: str) -> None:
    """git inside a bundle with a neutral identity and no user/system configuration."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", **BUNDLE_AUTHOR}
    proc = subprocess.run(["git", "-C", str(dest), "-c", "commit.gpgsign=false", *args],
                          capture_output=True, text=True, env=env, check=False)
    if proc.returncode != 0:
        raise CorpusError(f"bundle git {args[0]} failed: {proc.stderr.strip()[:300]}")


def build_bundle(repo: Path, task: Mapping[str, Any], dest: Path) -> Path:
    """Bundle (e): the base-SHA tree without the protected tests, in a NEW repository.

    The tree is exported (``git archive``) and committed as the single root commit of a fresh
    repository: no remote, no alternates, no worktree link, no reflog or object of the developer
    repository, so the candidate can reach neither the merged solution nor the evaluators."""
    statement = task_statement(task)  # fail closed before touching anything
    dest = dest.resolve()
    if dest.exists() or dest.is_symlink():
        raise CorpusError(f"bundle destination already exists: {dest}")
    for root in _checkout_roots(repo):
        if dest == root or root in dest.parents:
            raise CorpusError(f"bundle destination is inside the developer checkout: {dest}")
    archive = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", task["base_sha"]],
                             capture_output=True, check=False)
    if archive.returncode != 0:
        raise CorpusError(f"git archive failed: {archive.stderr.decode('utf-8', 'replace')[:300]}")
    dest.mkdir(parents=True)
    _CREATED_BUNDLES.add(dest)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(dest, filter="data")
        for entry in task["protected"]["entries"]:
            target = dest / entry["path"]
            if entry["status"] == "A":
                target.unlink(missing_ok=True)
            elif entry["kind"] == "test":
                removed = _remove_functions(target.read_text(encoding="utf-8"), entry["nodes"],
                                            entry["path"])
                target.write_text(removed, encoding="utf-8")
        (dest / "TASK.md").write_text(statement, encoding="utf-8")
        _bundle_git(dest, "init", "-q", "--template=", "-b", "main")
        _bundle_git(dest, "add", "-A", "-f")
        _bundle_git(dest, "commit", "-q", "--no-verify", "-m", "Task bundle base")
    except BaseException:
        remove_bundle(dest)
        raise
    return dest


def remove_bundle(dest: Path) -> None:
    """Delete a bundle, and only a directory ``build_bundle`` created in this run."""
    dest = dest.resolve()
    if dest not in _CREATED_BUNDLES:
        raise CorpusError(f"refusing to delete a directory this run did not create: {dest}")
    shutil.rmtree(dest, ignore_errors=True)
    _CREATED_BUNDLES.discard(dest)


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

def _has_symlink(candidate: Path, rel: str) -> bool:
    current = candidate
    for part in Path(rel).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _is_tripwire(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in TRIPWIRE_NAMES or name.endswith(TRIPWIRE_SUFFIXES)


def _candidate_changes(repo: Path, task: Mapping[str, Any], candidate: Path,
                       exempt: set[str]) -> dict[str, list[str]]:
    """Files the candidate changed against the base tree (``exempt`` = restored by the judge).

    ``tripwire`` lists created/modified/deleted harness files anywhere; ``outside_product``
    lists every changed file outside the product tree (tests, fixtures, docs)."""
    algo = _git(repo, "rev-parse", "--show-object-format").strip() or "sha1"
    base: dict[str, str] = {}
    for line in _git(repo, "ls-tree", "-r", "-z", task["base_sha"]).split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            base[path] = meta.split()[2]
    seen: set[str] = set()
    changed: list[str] = []
    for root, dirs, files in os.walk(candidate):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache")]
        for name in files:
            full = Path(root) / name
            rel = full.relative_to(candidate).as_posix()
            seen.add(rel)
            if rel in exempt or name.endswith(".pyc") or rel == "TASK.md":
                continue
            if rel.startswith(SOURCE_PREFIXES) and not _is_tripwire(rel):
                continue
            if full.is_symlink():
                changed.append(rel)
                continue
            data = full.read_bytes()
            oid = hashlib.new(algo, b"blob %d\0" % len(data) + data).hexdigest()
            if base.get(rel) != oid:
                changed.append(rel)
    deleted = [p for p in base if p not in seen and _is_tripwire(p) and p not in exempt
               and not _has_symlink(candidate, p)]
    return {"tripwire": sorted(p for p in changed + deleted if _is_tripwire(p)),
            "outside_product": sorted(p for p in changed if not p.startswith(SOURCE_PREFIXES))}


def judge(repo: Path, task: Mapping[str, Any], candidate: Path) -> dict[str, Any]:
    """Restore the protected tests from the merged SHA over the candidate bundle, run only
    them and return a mechanical verdict. Tests the candidate wrote never count: the protected
    paths are overwritten and only their node ids are run.

    Prevented: candidate copies of the protected paths, conftest/pytest.ini/pyproject/tox/
    setup.cfg/sitecustomize/usercustomize/``*.pth`` edits (REFUSED), symlinks at protected
    paths, ambient HOME/environment, conftest files above ``tests/``. Not prevented: product
    code tampering with pytest in-process, edits of non-protected support files (reported in
    ``changed_outside_product``)."""
    candidate = candidate.resolve()
    entries = task["protected"]["entries"]
    selected: list[str] = []
    for entry in entries:
        if entry["kind"] == "test":
            if entry["selection"] == "nodes":
                selected.extend(f"{entry['path'][len(PLUGIN_PREFIX):]}::{n}" for n in entry["nodes"])
            else:
                selected.append(entry["path"][len(PLUGIN_PREFIX):])
    extra: dict[str, Any] = {"tripwire": [], "changed_outside_product": []}
    if not selected:
        return _verdict("no_selected_tests", selected, 0, 0, 0, 0, None, extra)
    if not candidate.is_dir():
        return _verdict("candidate_is_not_a_directory", selected, 0, 0, 0, 0, None, extra)
    for entry in entries:
        if _has_symlink(candidate, entry["path"]):
            return _verdict(f"symlink_at_protected_path:{entry['path']}", selected, 0, 0, 0, 0,
                            None, extra)
    changes = _candidate_changes(repo, task, candidate, {e["path"] for e in entries})
    extra = {"tripwire": changes["tripwire"],
             "changed_outside_product": changes["outside_product"]}
    if changes["tripwire"]:
        return _verdict("harness_files_changed:" + ",".join(changes["tripwire"]), selected,
                        0, 0, 0, 0, None, extra)
    for entry in entries:
        content = _git(repo, "show", f"{task['head_sha']}:{entry['path']}", text=False)
        target = candidate / entry["path"]
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)  # never write through a hard link
            target.write_bytes(content)
        except OSError as exc:
            return _verdict(f"cannot_restore:{entry['path']}:{type(exc).__name__}", selected,
                            0, 0, 0, 0, None, extra)
    plugin_root = candidate / PLUGIN_PREFIX.rstrip("/")
    with tempfile.TemporaryDirectory(prefix="foundry-judge-") as tmp:
        tmp_dir = Path(tmp)
        home, scratch = tmp_dir / "home", tmp_dir / "tmp"
        home.mkdir()
        scratch.mkdir()
        junit = tmp_dir / "junit.xml"
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
               "TMPDIR": str(scratch), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
        env.update({k: os.environ[k] for k in ("LANG", "LC_ALL") if k in os.environ})
        ini = []
        merged_ini = subprocess.run(
            ["git", "-C", str(repo), "show", f"{task['head_sha']}:{PLUGIN_PREFIX}pytest.ini"],
            capture_output=True, check=False)
        if merged_ini.returncode == 0:
            (tmp_dir / "pytest.ini").write_bytes(merged_ini.stdout)
            ini = ["-c", str(tmp_dir / "pytest.ini")]
        cmd = [sys.executable, "-P", "-m", "pytest", "-q", "-p", "no:cacheprovider",
               f"--junitxml={junit}", f"--confcutdir={plugin_root / 'tests'}",
               f"--rootdir={plugin_root}", *ini, *selected]
        try:
            proc = subprocess.run(cmd, cwd=plugin_root, env=env, capture_output=True, text=True,
                                  timeout=PYTEST_TIMEOUT_SECONDS, check=False)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            return _verdict("timeout", selected, 0, 0, 0, 0, None, extra)
        counts = _junit_counts(junit)
    passed, failed, errors, skipped = counts
    return _verdict(None, selected, passed, failed, errors, skipped, code, extra)


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
             errors: int, skipped: int, exit_code: int | None,
             extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    accepted = (note is None and exit_code == 0 and passed >= 1 and failed == 0
                and errors == 0 and skipped == 0)
    return {"verdict": "ACCEPTED" if accepted else "REFUSED",
            "selected": list(selected), "passed": passed, "failed": failed,
            "errors": errors, "skipped": skipped, "pytest_exit_code": exit_code,
            "note": note, **(extra or {})}


# ----------------------------------------------------------------- verification (g)

def verify_task(repo: Path, task: Mapping[str, Any], workdir: Path) -> dict[str, Any]:
    """The judge must ACCEPT the merged solution and REFUSE the base without it."""
    result: dict[str, Any] = {"pr": task["pr"], "issue": task["issue"]}
    for label, with_solution in (("with_merged_diff", True), ("without_merged_diff", False)):
        dest = workdir / f"pr{task['pr']}-{label}"
        built = None
        try:
            built = build_bundle(repo, task, dest)  # refuses an existing destination
            if with_solution:
                apply_solution(repo, task, built)
            result[label] = judge(repo, task, built)
        except CorpusError as exc:
            result[label] = {"verdict": "ERROR", "note": str(exc)[:300]}
        finally:
            if built is not None:  # only what this call created
                remove_bundle(built)
    ok = (result["with_merged_diff"]["verdict"] == "ACCEPTED"
          and result["without_merged_diff"]["verdict"] == "REFUSED")
    result["discarded_reason"] = None
    if not ok:
        if result["with_merged_diff"]["verdict"] != "ACCEPTED":
            result["discarded_reason"] = "judge_does_not_accept_merged_solution"
        else:
            result["discarded_reason"] = "judge_accepts_base_without_solution"
    return result


def _ok(repo: Path, *args: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          check=False).returncode == 0


def replayability(repo: Path, task: Mapping[str, Any]) -> dict[str, Any]:
    """Local facts about replaying a task (no network): SHAs present, and which
    ``origin`` refs known locally contain them. Environment-dependent: recorded as observed."""
    out: dict[str, Any] = {"merge_kind": task["merge_kind"], "base_ref": task.get("base_ref")}
    for key in ("base_sha", "head_sha"):
        sha = task[key]
        present = _ok(repo, "cat-file", "-e", f"{sha}^{{commit}}")
        refs = (_git(repo, "for-each-ref", "--contains", sha, "--format=%(refname)",
                     "refs/remotes/origin", check=False).split() if present else [])
        out[key] = {"present_locally": present, "origin_refs_containing": sorted(refs)}
    on_default = _ok(repo, "merge-base", "--is-ancestor", task["head_sha"],
                     f"refs/remotes/origin/{DEFAULT_BRANCH_FOR_REPLAY}")
    out["head_on_origin_default_branch"] = on_default
    reachable = on_default or bool(out["head_sha"]["origin_refs_containing"])
    out["public_reachability"] = "origin_refs" if reachable else "unknown"
    out["fetch_hint"] = None if reachable else (
        f"git fetch origin {task.get('base_ref')}  # only if that branch still exists on the "
        "remote; not verified (no network used); a squash commit of a stacked PR is on no other "
        "public ref")
    return out


def finalize(snapshot: Mapping[str, Any], drawn: Mapping[str, Any], verify: Any,
             replay: Any = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Replace each failing slot by the next passing entry of the replacement queue.

    ``verify(pr)`` returns a verification result (see ``verify_task``); results are
    returned too so the evidence keeps every task that was tried. ``replay(task)``, when
    given, adds the replayability facts of each chosen task."""
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
    corpus = {group: [_task_entry(by_pr[p], replay) for p in prs] for group, prs in chosen.items()}
    corpus["discarded_at_verification"] = discarded
    return corpus, [tried[k] for k in sorted(tried)]


def _task_entry(task: Mapping[str, Any], replay: Any = None) -> dict[str, Any]:
    entry = {"issue": task["issue"], "pr": task["pr"], "title": task["title"],
            "base_sha": task["base_sha"], "head_sha": task["head_sha"],
            "pr_head_sha": task["pr_head_sha"], "changed_lines": changed_lines(task),
            "protected_tests_count": protected_count(task["protected"]["entries"]),
            "acceptance_criteria": task["ac_text"], "ac_source": task["ac_source"],
            "protected_tests": task["protected"]["entries"]}
    if replay:
        entry["replayability"] = replay(task)
    return entry


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
    s.add_argument("--statements", help=f"the committed {STATEMENTS_FILE} (tracker statements)")
    s.add_argument("--out")
    d = sub.add_parser("draw", help="apply the criteria and draw 6 comparison + 6 screening tasks and the queue")
    d.add_argument("--snapshot", required=True)
    d.add_argument("--seed", required=True)
    d.add_argument("--overrides", help="JSON {\"<pr>\": {\"decision\": \"include|exclude\", "
                   "\"reason\": \"...\"}}")
    d.add_argument("--out")
    b = sub.add_parser("bundle", help="new one-commit repository at the base tree without protected "
                       "tests (no link to --repo)")
    b.add_argument("--repo", default=".")
    b.add_argument("--snapshot", required=True)
    b.add_argument("--pr", type=int, required=True)
    b.add_argument("--dest", required=True)
    j = sub.add_parser("judge", help="mechanical verdict for a candidate bundle")
    j.add_argument("--repo", default=".")
    j.add_argument("--snapshot", required=True)
    j.add_argument("--pr", type=int, required=True)
    j.add_argument("--candidate", required=True)
    v = sub.add_parser("verify", help="judge ACCEPTS merged diff, REFUSES base; replace failures")
    v.add_argument("--repo", default=".")
    v.add_argument("--snapshot", required=True)
    v.add_argument("--seed", required=True)
    v.add_argument("--overrides")
    v.add_argument("--workdir", required=True, help="scratch directory for disposable bundles")
    v.add_argument("--out")
    args = parser.parse_args(argv)
    repo = Path(getattr(args, "repo", ".")).resolve()

    if args.cmd == "snapshot":
        statements = load_statements(Path(args.statements)) if args.statements else None
        _dump(build_snapshot(repo, _load(args.prs_json), args.ref, statements=statements),
              args.out)
        return 0
    snapshot = _load(args.snapshot)
    by_pr = {t["pr"]: t for t in snapshot["prs"]}
    if args.cmd == "draw":
        overrides = _load(args.overrides) if args.overrides else {}
        _dump(draw(snapshot, args.seed, overrides), args.out)
    elif args.cmd == "bundle":
        print(build_bundle(repo, by_pr[args.pr], Path(args.dest)))
    elif args.cmd == "judge":
        result = judge(repo, by_pr[args.pr], Path(args.candidate))
        _dump(result, None)
        return 0 if result["verdict"] == "ACCEPTED" else 1
    elif args.cmd == "verify":
        overrides = _load(args.overrides) if args.overrides else {}
        drawn = draw(snapshot, args.seed, overrides)
        workdir = Path(args.workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        corpus, evidence = finalize(snapshot, drawn,
                                    lambda pr: verify_task(repo, by_pr[pr], workdir),
                                    lambda task: replayability(repo, task))
        digest = hashlib.sha256(Path(args.snapshot).read_bytes()).hexdigest()
        _dump({"schema": MANIFEST_SCHEMA, "snapshot_sha256": digest, "seed": args.seed, "algorithm": DRAW_ALGORITHM,
               "criteria": {"max_changed_lines": MAX_CHANGED_LINES},
               "statements": snapshot.get("statements"), "limits": list(CORPUS_LIMITS),
               "draw": drawn, **corpus, "verification": evidence}, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
