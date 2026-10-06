"""PAT-114: pure pieces of the PAT-19 protocol v2 (read-only exploration), used by the launcher.

Ground truth of a task from its merged diff, the explorer report format and its parsing, the
deterministic localization judge (file recall, file precision, function recall), the pre-registered
screening rule, the dedicated-machine check, the quality and economy rules of the comparison and the
rendering of a report into a statement. Pure: git (read-only) is the only external call, in
``ground_truth``. No model, no cloud, no tracker, no network (FOUNDRY-ADR-0007: a campaign tool, not a
product role). See ``docs/qualification/pat-19-protocol-v2.md``.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Sequence

from foundry import local_first_corpus as lfc

TRUTH_SCHEMA = "foundry.local-first-exploration-truth.v2"
REPORT_KEYS = ("files", "functions", "rationale")
MAX_FUNCTIONS = 10  # only the first 10 functions of a report count for recall and reach the implementer
GIT_DIFF_PINS = ("-c", "diff.algorithm=myers", "-c", "diff.indentHeuristic=false", "-c", "diff.renames=false",
                 "-c", "core.quotepath=false")
VERDICTS = ("SCORED", "REFUSED", "CONTAMINATED")


class ExplorationError(lfc.CorpusError):
    """Ground truth that cannot be built, or a rule given something it cannot read."""


class ReportError(ValueError):
    """A report that is missing or unusable: the attempt is a refusal (score 0)."""


# --------------------------------------------------------------------------- ground truth

def is_product_path(path: str) -> bool:
    """Product file of the corpus: under the judge's product prefixes (``lfc.SOURCE_PREFIXES``: the
    tooling and hooks of the plugin), not under the tests and not documentation (``lfc._is_doc``).
    This is the classification the judge already uses (``outside_product``), not a new one."""
    return (path.startswith(lfc.SOURCE_PREFIXES) and not path.startswith(lfc.TESTS_PREFIX)
            and not lfc._is_doc(path))


def _function_spans(source: str, filename: str) -> list[tuple[int, int, str]]:
    """``(first line, last line, qualified name)`` of every top-level function and every method
    (``Class.method``, nested classes dotted); a function nested in a function belongs to the outer one.
    A decorator line belongs to its function."""
    spans: list[tuple[int, int, str]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                first = min([child.lineno, *(d.lineno for d in child.decorator_list)])
                spans.append((first, child.end_lineno or child.lineno, f"{prefix}{child.name}"))
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(lfc._parse(source, filename), "")
    return spans


def _changed_lines(repo: Path, base: str, head: str, path: str) -> tuple[set[int], set[int]]:
    """``(old lines, new lines)`` of ``path`` that the merged diff touches (``git diff -U0``): the old-side
    lines are those removed or replaced (read in the base source), the new-side lines those added or
    replaced (read in the merged source). A pure deletion has no new line: it never borrows the line
    before it, so a deleted function does not blame its neighbour. The diff algorithm and every
    configuration that moves hunks are pinned: the truth does not depend on the user's git configuration."""
    diff = lfc._git(repo, *GIT_DIFF_PINS, "diff", "-U0", "--diff-algorithm=myers", "--no-indent-heuristic",
                    "--no-renames", "--no-ext-diff", "--no-textconv", base, head, "--", path)
    old: set[int] = set()
    new: set[int] = set()
    for found in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", diff, re.M):
        o_start, o_count = int(found.group(1)), int(found.group(2) if found.group(2) is not None else 1)
        n_start, n_count = int(found.group(3)), int(found.group(4) if found.group(4) is not None else 1)
        old.update(range(o_start, o_start + o_count))
        new.update(range(n_start, n_start + n_count))
    return old, new


def ground_truth(repo: Path, task: Mapping[str, Any]) -> dict[str, Any]:
    """What an explorer should have found for ``task``, from its merged diff.

    ``files``: product files the diff modifies or deletes (they exist at the base SHA). ``created_files``:
    product files it adds (an explorer cannot find what does not exist: not part of recall, accepted by
    precision). ``all_changed_files``: every path the merged diff touches, product or not (tests, docs,
    changelog): a reported file is correct for the precision when it is there. ``functions``:
    ``[file, qualified name]`` of the functions and methods of a modified Python file that enclose a
    changed line (a removed line in the base source, an added line in the merged source) and exist at the
    base SHA; a changed line outside any function (module level) gives none (file-level only);
    ``new_functions``: the ones that do not exist at the base (not part of recall). Recall uses the product
    files and functions only. Non-Python product files are file-level only."""
    repo = Path(lfc._git(repo, "rev-parse", "--show-toplevel").strip())  # the diff paths are root-relative
    files, created = [], []
    for item in task["files"]:
        path = item["path"]
        if not is_product_path(path):
            continue
        (files if item["status"] in ("M", "D") else created).append(path)
    functions: list[list[str]] = []
    fresh: list[list[str]] = []
    base, head = task["base_sha"], task["head_sha"]
    for path in sorted(p for p in files if p.endswith(".py")):
        status = next(i["status"] for i in task["files"] if i["path"] == path)
        if status != "M":
            continue
        spans = _function_spans(lfc._blob(repo, head, path), path)
        base_spans = _function_spans(lfc._blob(repo, base, path), path)
        at_base = {name for _, _, name in base_spans}
        old_lines, new_lines = _changed_lines(repo, base, head, path)
        touched = {name for line in new_lines for first, last, name in spans if first <= line <= last}
        touched |= {name for line in old_lines for first, last, name in base_spans if first <= line <= last}
        functions += [[path, name] for name in sorted(touched & at_base)]
        fresh += [[path, name] for name in sorted(touched - at_base)]
    return {"schema": TRUTH_SCHEMA, "pr": task["pr"], "base_sha": base, "head_sha": head,
            "files": sorted(files), "created_files": sorted(created),
            "all_changed_files": sorted(item["path"] for item in task["files"]), "functions": functions,
            "new_functions": fresh}


# ------------------------------------------------------------------------------- report

def normalize_path(path: str, root: str | None = None) -> str:
    """A repo-relative POSIX path: ``./`` stripped, an absolute path under ``root`` made relative."""
    text = path.strip().replace("\\", "/")
    if root and text.startswith(root.rstrip("/") + "/"):
        text = text[len(root.rstrip("/")) + 1:]
    while text.startswith("./"):
        text = text[2:]
    return text


def validate_report(data: Any, root: str | None = None) -> dict[str, Any]:
    """The normalized report, or a ``ReportError``: ``{"files": [str], "functions": [{"file", "name"}],
    "rationale": str}``. A missing key or a wrong type is unusable (no partial credit)."""
    if not isinstance(data, dict) or not all(k in data for k in REPORT_KEYS):
        raise ReportError(f"the report needs the keys {', '.join(REPORT_KEYS)}")
    if not isinstance(data["files"], list) or not all(isinstance(f, str) for f in data["files"]):
        raise ReportError("files must be a list of paths")
    if not isinstance(data["functions"], list) or not all(
            isinstance(f, dict) and isinstance(f.get("file"), str) and isinstance(f.get("name"), str)
            for f in data["functions"]):
        raise ReportError("functions must be a list of {file, name}")
    if not isinstance(data["rationale"], str):
        raise ReportError("rationale must be a string")
    files = list(dict.fromkeys(p for p in (normalize_path(f, root) for f in data["files"]) if p))
    functions: list[dict[str, str]] = []
    for item in data["functions"]:
        entry = {"file": normalize_path(item["file"], root), "name": item["name"].strip()}
        if entry["file"] and entry["name"] and entry not in functions:
            functions.append(entry)
    return {"files": files, "functions": functions, "rationale": data["rationale"].strip()}


def extract_report(text: str, root: str | None = None) -> dict[str, Any]:
    """The report object of a free text (the final message of an explorer): the last JSON object
    that has a ``files`` key, bare or inside a code fence."""
    decoder, found = json.JSONDecoder(), None
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except ValueError:
            continue
        if isinstance(value, dict) and "files" in value:
            found = value
    if found is None:
        raise ReportError("no JSON report with a files key in the final message")
    return validate_report(found, root)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    return ""


def final_message_text(stream_log: Path, fmt: str) -> str | None:
    """The explorer's final answer in the kept event stream: the ``result`` event of a Claude stream
    (else its last assistant text), the last assistant ``message_end`` of an omp stream. The shapes were
    observed in the coordinator's toy trials of 2026-10-06 (``pat-19-preflight-v2-2026-10-06.json``)."""
    result = last = None
    try:
        lines = Path(stream_log).read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        if fmt == "claude-stream-json":
            if event.get("type") == "result" and isinstance(event.get("result"), str):
                result = event["result"]
            elif event.get("type") == "assistant" and _text_of(message.get("content")):
                last = _text_of(message["content"])
        elif event.get("type") == "message_end" and message.get("role") == "assistant" \
                and _text_of(message.get("content")):
            last = _text_of(message["content"])
    return result if result is not None else last


def load_report(scratch: Path, stream_log: Path, fmt: str, root: str | None = None
                ) -> tuple[dict[str, Any] | None, str, str | None]:
    """``(report, source, refusal)``. The arm-written ``<scratch>/report.json`` first, else the final
    message of the stream. A missing or unusable report is ``(None, source, reason)``: a refusal, never
    a void attempt (the arm ran)."""
    path = Path(scratch) / "report.json"
    try:
        if path.is_file():
            try:
                return validate_report(json.loads(path.read_text("utf-8")), root), "file", None
            except (OSError, ValueError, ReportError) as exc:
                raise ReportError(f"report.json unusable: {exc}"[:200]) from None
        text = final_message_text(stream_log, fmt)
        if text is None:
            raise ReportError("no report file and no final message")
        return extract_report(text, root), "final_message", None
    except ReportError as exc:
        return None, "file" if path.is_file() else "final_message", str(exc)[:200]


# ------------------------------------------------------------------------------- judge

def _function_match(reported: Mapping[str, str], truth: Sequence[str]) -> bool:
    """A reported function matches a true one in the same file when its name is the qualified name or
    its last component (``update`` matches ``Tracker.update``: lenient on the class, strict on the file)."""
    return reported["file"] == truth[0] and reported["name"] in (truth[1], truth[1].rsplit(".", 1)[-1])


def score_report(truth: Mapping[str, Any], report: Mapping[str, Any] | None) -> dict[str, Any]:
    """Deterministic localization metrics. ``function_recall`` (the MAIN metric of the screening): true
    functions found among the FIRST ``MAX_FUNCTIONS`` (10) functions of the report / true functions
    (``None`` when the diff touches no function: undefined, never 0). ``file_precision``: reported files
    that the merged diff really changed or created, product or not (tests, docs, changelog included) /
    reported files (0 when nothing is reported). ``file_recall`` (measured and reported, does not decide):
    true product files found / true product files. A missing report scores 0 on every metric. ``counts``
    are the integers the screening rule computes exactly with."""
    if not truth["files"]:
        raise ExplorationError(f"PR {truth.get('pr')}: empty ground truth, nothing to score")
    reported = list(report["files"]) if report else []
    counted = list(report["functions"])[:MAX_FUNCTIONS] if report else []
    changed = set(truth.get("all_changed_files", [])) | set(truth["files"]) | set(truth["created_files"])
    right = [f for f in reported if f in truth["files"]]
    correct = [f for f in reported if f in changed]
    found = [t for t in truth["functions"] if any(_function_match(r, t) for r in counted)]
    counts = {"truth_files": len(truth["files"]), "found_files": len(right), "reported_files": len(reported),
              "correct_files": len(correct), "truth_functions": len(truth["functions"]),
              "found_functions": len(found), "counted_functions": len(counted)}
    return {"file_recall": round(len(right) / len(truth["files"]), 6),
            "file_precision": round(len(correct) / len(reported), 6) if reported else 0.0,
            "function_recall": (round(len(found) / len(truth["functions"]), 6)
                                if truth["functions"] else None), "counts": counts}


def verdict_of(truth: Mapping[str, Any], report: Mapping[str, Any] | None, refusal: str | None,
               contaminated: bool = False) -> dict[str, Any]:
    """The ``judge`` field of an exploration attempt. A missing or unusable report is ``REFUSED``; a
    contaminated attempt is ``CONTAMINATED`` and counts as 0 (the v2 rule, fixed before any result)."""
    if contaminated:
        report, refusal = None, None
    scored = score_report(truth, report)
    kind = "CONTAMINATED" if contaminated else ("SCORED" if report is not None else "REFUSED")
    note = ("contaminated: counted as 0 (protocol v2 rule)" if contaminated
            else None if report is not None else (refusal or "no report"))
    return {"verdict": kind, "kind": "exploration", "note": note, **scored}


# ----------------------------------------------------------------------- screening rule

def _mean(values: Sequence[Fraction]) -> Fraction | None:
    return sum(values, Fraction(0)) / len(values) if values else None


def _as_float(value: Fraction | None) -> float | None:
    return None if value is None else round(float(value), 6)


def candidate_row(tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One candidate's screening line from its per-task ``{judge, wall_seconds, pr}``: exact means of
    function recall (the main metric), file precision and file recall, and the total duration. A task
    whose truth has no function is excluded from the function-recall mean (none of the 12 corpus tasks
    today); refused and contaminated attempts count 0 on every metric. The function recall is ``None``
    when no task has a function in its truth."""
    counts = [t["judge"]["counts"] for t in tasks]
    recalls = [Fraction(c["found_files"], c["truth_files"]) for c in counts]
    precisions = [Fraction(c["correct_files"], c["reported_files"]) if c["reported_files"] else Fraction(0)
                  for c in counts]
    functions = [Fraction(c["found_functions"], c["truth_functions"]) for c in counts if c["truth_functions"]]
    return {"tasks": len(tasks), "mean_function_recall": _as_float(_mean(functions)),
            "function_tasks": len(functions),
            "mean_file_precision": _as_float(_mean(precisions)),
            "mean_file_recall": _as_float(_mean(recalls)),
            "total_seconds": round(sum(t["wall_seconds"] for t in tasks), 3),
            "refused": sum(1 for t in tasks if t["judge"]["verdict"] == "REFUSED"),
            "contaminated": sum(1 for t in tasks if t["judge"]["verdict"] == "CONTAMINATED"),
            "per_task": [{"pr": t["pr"], "verdict": t["judge"]["verdict"],
                          "function_recall": t["judge"]["function_recall"],
                          "file_recall": t["judge"]["file_recall"],
                          "file_precision": t["judge"]["file_precision"],
                          "wall_seconds": t["wall_seconds"]} for t in tasks],
            "_function": _mean(functions), "_precision": _mean(precisions)}


def apply_screening_rule(rule: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]],
                         complete: bool) -> dict[str, Any]:
    """The pre-registered rule of the v2 screening, on exact fractions: retained = highest mean FUNCTION
    recall over the tasks among the candidates whose mean file precision is at least
    ``file_precision_min``. The threshold comes BEFORE the tie: STOP on keep-cloud when no candidate meets
    the precision floor or when the best mean function recall (tied or not) is under
    ``retained_function_recall_min``. Then a tie goes to the shortest total duration, and a tie still
    unresolved goes to the candidate listed first in ``rule["candidates"]`` (the frozen order): no choice is
    left after the results. File recall is measured and reported but does not decide (the truth of each
    task is a single product file, shared by 5 of the 6 screening tasks). Nothing is selected before every
    frozen candidate has a decided record for every task (``complete``)."""
    if not rows:
        return {"selected": None, "reason": "no_screening_results", "stop": None}
    if not complete:
        return {"selected": None, "reason": "incomplete_screening", "stop": None}
    floor = Fraction(str(rule["file_precision_min"]))
    minimum = Fraction(str(rule["retained_function_recall_min"]))
    eligible = {c: r for c, r in rows.items() if r["_precision"] >= floor}
    if not eligible:
        return {"selected": None, "reason": "no_candidate_meets_precision_floor: keep_cloud",
                "stop": "keep_cloud"}
    if any(r["_function"] is None for r in eligible.values()):  # no function in any truth: no main metric
        return {"selected": None, "reason": "no_function_in_the_ground_truth", "stop": None}
    best = max(r["_function"] for r in eligible.values())
    tied = sorted(c for c, r in eligible.items() if r["_function"] == best)
    if best < minimum:
        return {"selected": None, "reason": "retained_candidate_function_recall_below_minimum: keep_cloud",
                "stop": "keep_cloud", "best_candidate": tied[0], **({"tied": tied} if len(tied) > 1 else {})}
    if len(tied) == 1:
        return {"selected": tied[0], "reason": "highest_mean_function_recall", "stop": None}
    fastest = min(eligible[c]["total_seconds"] for c in tied)
    quickest = [c for c in tied if eligible[c]["total_seconds"] == fastest]
    if len(quickest) == 1:
        return {"selected": quickest[0], "reason": "tie_broken_by_total_duration", "stop": None}
    order = list(rule.get("candidates", []))
    first = min(quickest, key=lambda c: order.index(c) if c in order else len(order))
    return {"selected": first, "reason": "tie_broken_by_frozen_candidate_order", "stop": None,
            "tied": quickest}


def load_truth_set(directory: Path, spec: Mapping[str, Any]) -> dict[str, Any]:
    """The committed ground truth of the 12 tasks (``exploration.ground_truth`` of the campaign:
    ``{file, sha256}``), the single source of the launcher: refused when absent or when its sha256 is not
    the recorded one, so the truth cannot drift between the freeze and the run."""
    path = Path(directory) / spec["file"]
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ExplorationError(f"ground truth file {spec['file']} unreadable: {exc}") from None
    if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
        raise ExplorationError(f"ground truth file {spec['file']} does not match the sha256 recorded in the "
                               "campaign config: the frozen truth changed")
    return json.loads(raw)["tasks"]


# --------------------------------------------------------------------- dedicated machine

_FREE_PERCENT = re.compile(r"free percentage:\s*(\d+)%")
_APP_NAME = re.compile(r"/([^/]+)\.app/")


def process_name(command: str) -> str:
    """A name for the report that carries no argument (a command line may hold a secret): the
    application name of a bundle path, else the basename of the first word."""
    app = _APP_NAME.search(command)
    return app.group(1) if app else os.path.basename(command.split(None, 1)[0]) if command.split() else ""


def check_dedicated_machine(config: Mapping[str, Any], ps_text: str | None,
                            pressure_text: str | None) -> dict[str, Any]:
    """The dedicated-machine admission of the v2 runs from the output of ``ps -axo rss=,command=`` and
    ``memory_pressure`` (read-only commands the launcher already runs). Refuses when any process other
    than LM Studio, the launcher and the system (the process patterns of ``allowed_command_patterns``)
    holds more than ``max_other_process_rss_gib`` of resident memory, or when the free memory
    percentage is under ``min_free_percent``. An unreadable output refuses (unknown is not a pass).
    The observed values are returned; only a process NAME is recorded, never an argument."""
    limit_kib = int(Fraction(str(config["max_other_process_rss_gib"])) * 1024 * 1024)
    patterns = {cat: [re.compile(p) for p in pats] for cat, pats in config["allowed_command_patterns"].items()}
    refusals: list[str] = []
    others: list[tuple[int, str]] = []
    allowed_kib = dict.fromkeys(patterns, 0)
    unparsed = processes = 0
    if ps_text is None:
        refusals.append("dedicated_machine_ps_unavailable")
    for line in (ps_text or "").splitlines():
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        if len(parts) != 2 or not parts[0].isdigit():
            unparsed += 1
            continue
        processes += 1
        rss, command = int(parts[0]), parts[1]
        category = next((c for c, pats in patterns.items() if any(p.search(command) for p in pats)), None)
        if category:
            allowed_kib[category] += rss
        else:
            others.append((rss, process_name(command)))
    offenders = sorted((o for o in others if o[0] > limit_kib), reverse=True)
    refusals += [f"dedicated_machine_process_over_{config['max_other_process_rss_gib']}gib:{name}:"
                 f"{rss // 1024}MiB" for rss, name in offenders[:10]]
    found = _FREE_PERCENT.search(pressure_text or "")
    free = int(found.group(1)) if found else None
    if free is None:
        refusals.append("dedicated_machine_memory_pressure_unavailable")
    elif free < config["min_free_percent"]:
        refusals.append(f"dedicated_machine_free_memory_below_minimum:{free}<{config['min_free_percent']}")
    return {"ok": not refusals, "refusals": refusals,
            "observed": {"processes": processes, "unparsed_lines": unparsed, "free_percent": free,
                         "largest_other_processes": [{"process": n, "rss_mib": r // 1024}
                                                     for r, n in sorted(others, reverse=True)[:5]],
                         "allowed_rss_mib": {c: v // 1024 for c, v in allowed_kib.items()},
                         "limits": {"max_other_process_rss_gib": config["max_other_process_rss_gib"],
                                    "min_free_percent": config["min_free_percent"]}}}


# ----------------------------------------------------------------- comparison rules (L, E vs A)

def render_report_section(report: Mapping[str, Any], limits: Mapping[str, int]) -> str:
    """The text appended to the statement of an assisted arm. Deterministic; a long report is cut at
    the limits and says so, so the cut is visible and the same for every arm."""
    files, functions = report["files"], report["functions"]
    rationale = report["rationale"][:limits["max_rationale_chars"]]
    lines = ["", "", "---",
             "Exploration report (made before this task by a read-only explorer that could not change "
             "anything; it can be incomplete or wrong, check it against the code):", "", "Files:"]
    lines += [f"- {f}" for f in files[:limits["max_files"]]] or ["- (none reported)"]
    if len(files) > limits["max_files"]:
        lines.append(f"- ... {len(files) - limits['max_files']} more not shown")
    lines += ["", "Functions:"]
    lines += [f"- {f['file']}: {f['name']}" for f in functions[:limits["max_functions"]]] or ["- (none reported)"]
    if len(functions) > limits["max_functions"]:
        lines.append(f"- ... {len(functions) - limits['max_functions']} more not shown")
    lines += ["", "Rationale:", rationale or "(none)", ""]
    return "\n".join(lines)


def quality_verdict(a_tasks: Sequence[Mapping[str, Any]], x_tasks: Sequence[Mapping[str, Any]]) -> str:
    """Acceptance rate of an assisted arm at least that of A on the same tasks, with undecided tasks
    bounding the count: ``pass`` when even the worst case holds, ``fail`` when even the best case
    falls short, else ``unavailable`` (an undecided task is unknown, never a failure)."""
    if not a_tasks or len(a_tasks) != len(x_tasks):
        return "unavailable"
    a_min = sum(1 for t in a_tasks if t["accepted"])
    a_max = a_min + sum(1 for t in a_tasks if not t["accepted"] and t["unknown"])
    x_min = sum(1 for t in x_tasks if t["accepted"])
    x_max = x_min + sum(1 for t in x_tasks if not t["accepted"] and t["unknown"])
    return "pass" if x_min >= a_max else "fail" if x_max < a_min else "unavailable"


def economy_verdict(rule: Mapping[str, Any], a_tasks: Sequence[Mapping[str, Any]],
                    x_tasks: Sequence[Mapping[str, Any]], unknown_work: bool) -> dict[str, Any]:
    """Premium tokens per accepted task, explorer included, against A: ``pass`` when the assisted arm's
    is at most ``premium_per_accepted_ratio_max`` times A's. Unavailable (never zero, never a guess)
    when a compared task is undecided, spent work is unknown, a premium total is unknown or an arm
    accepted nothing (no per-accepted figure)."""
    a_prem = None if any(t["premium_billing_tokens"] is None for t in a_tasks) else sum(
        t["premium_billing_tokens"] for t in a_tasks)
    x_prem = None if any(t["premium_billing_tokens"] is None for t in x_tasks) else sum(
        t["premium_billing_tokens"] for t in x_tasks)
    a_acc, x_acc = sum(1 for t in a_tasks if t["accepted"]), sum(1 for t in x_tasks if t["accepted"])
    cut = unknown_work or any(t["unknown"] for t in [*a_tasks, *x_tasks])
    detail: dict[str, Any] = {
        "premium_billing_tokens": x_prem, "reference_premium_billing_tokens": a_prem,
        "accepted": x_acc, "reference_accepted": a_acc,
        "premium_per_accepted": None if x_prem is None or not x_acc else round(x_prem / x_acc, 3),
        "reference_premium_per_accepted": None if a_prem is None or not a_acc else round(a_prem / a_acc, 3),
        "ratio_max": rule["premium_per_accepted_ratio_max"], "ratio": None, "premium_pass": None,
        "premium_definition": "unweighted sum of the four billing token classes over all models, "
                              "explorer included (an economy model counts as premium; local tokens do not)"}
    if not a_tasks or not x_tasks or cut or a_prem is None or x_prem is None or not a_acc or not x_acc:
        return {"verdict": "unavailable", "detail": detail}
    ratio_max = Fraction(str(rule["premium_per_accepted_ratio_max"]))
    # x_prem / x_acc <= ratio_max * a_prem / a_acc, in integers
    passed = x_prem * a_acc <= ratio_max * a_prem * x_acc
    detail.update(ratio=round((x_prem * a_acc) / (a_prem * x_acc), 4) if a_prem else None,
                  premium_pass=bool(passed))
    return {"verdict": "pass" if passed else "fail", "detail": detail}
