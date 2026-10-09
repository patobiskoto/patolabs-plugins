"""PAT-118: the compression protocol of the PAT-19 local-first qualification (protocol c1).

A third local use, WITHOUT a tool loop: the long output of the protected tests of a task is compressed in ONE
non-streaming Chat Completions call to the local OpenAI-compatible endpoint (loopback only, FOUNDRY-ADR-0007: an
unprivileged preprocessing) before it is sent to the cloud. This module holds what is not the launcher's machinery:

* the material: for each corpus task, the REAL output of the protected tests on the unmodified base bundle, produced
  offline (no model, no cloud) with the judge's own interpreter and command, masked, committed with its sha256;
* the deterministic screening judge (recall of the failing tests cited in the summary, size of the summary), the
  selection rule and the stop on "keep the cloud";
* the one local call;
* the downstream diagnosis (the cloud model names one file and one function) and its judge;
* the decision rule, written in advance, and the report sections.

The launcher (``local_first_runner``) reuses its envelope, ledger, resume, dedicated-machine preflight and token reading
for the two modes ``screen_compression`` and ``compare_compression``; see ``docs/qualification/pat-19-protocol-c1.md``.
Nothing here promotes anything (PAT-ADR-0015): the only outputs are verdicts and numbers.

Errors are ``CompressionError`` (a ``lfc.CorpusError``), never a ``RunnerError``: the runner may run as ``__main__`` and
this module imports it under its package name, so the two classes would not be the same object.
"""
from __future__ import annotations

import getpass
import hashlib
import ipaddress
import json
import os
import re
import socket
import statistics
import urllib.error
import urllib.parse
import urllib.request
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Sequence

from foundry import local_first_corpus as lfc
from foundry import local_first_exploration as lfe
from foundry import local_first_runner as lfr  # imported lazily BY the runner: no cycle at import time

CAMPAIGN_SCHEMA_V3 = "foundry.local-first-campaign.v3"
PROTOCOL_C1 = "pat-19-protocol-c1"
PROTOCOL_C1_PILOT = "pat-19-protocol-c1-pilot"
C1_PROTOCOLS = (PROTOCOL_C1, PROTOCOL_C1_PILOT)
C1_TASKS = (26, 38, 25, 42, 33, 37, 30, 83, 27, 24, 48, 19)  # the order of the v5 comparison: 6 comparison, 6 screening
C1_PILOT_TASKS = (27,)
C1_TASK_SET = {PROTOCOL_C1: "pat-19-c1", PROTOCOL_C1_PILOT: "pat-19-c1-pilot"}  # ``task.set`` of the records
# The five local candidates of protocol v1 (``pat-19-campaign-v1.json``, in its order; the sixth entry of that file is
# declared unused).
C1_CANDIDATES = ("qwen3.8-27b-mlx-6bit", "qwen3.8-27b-mlx-4bit", "qwen3.6-35b-a3b-mlx-4bit", "muse-glimmer-30b-gguf",
                 "qwen3-coder-30b-a3b-mlx-4bit")
C1_CLAUDE_CODE = "2.1.294"  # observed on the machine on 2026-10-09
C1_RECALL_MEAN_MIN = Fraction(4, 5)  # validated 2026-10-07
C1_SIZE_RATIO_MAX = Fraction(1, 4)  # validated 2026-10-07
C1_PREMIUM_RATIO_MAX = Fraction(17, 20)  # validated 2026-10-07 (0.85)
C1_CLOUD_EXECUTIONS_MAX = 40  # validated 2026-10-07: the cap of the whole protocol, pilot included
C1_ENVELOPE_SHARE = {PROTOCOL_C1: 36, PROTOCOL_C1_PILOT: 4}  # 36 + 4 = 40: how the cap of 40 is enforced
C1_PAIRED_DECIDED_MIN = {PROTOCOL_C1: 9, PROTOCOL_C1_PILOT: 1}  # of 12 tasks (v5's 9), of the pilot's one task
C1_EFFORT = "medium"  # the effort the v5 implementer arms pinned (Sonnet 5.5)
C1_DIAGNOSER_MODEL = "claude-sonnet-5-5"
SCREEN_PATH = "CS"  # path name of a screening attempt (one local call)
ARMS = ("F", "S")  # F: the full output; S: the summary of the retained candidate
DIAGNOSE_SEGMENT = "diagnose"
MATERIAL_SCHEMA = "foundry.local-first-compression-material.v1"
MATERIAL_DIR = "pat-19-compression-material-v1"
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
FAILED_SUMMARY = "failed_summary"


class CompressionError(lfc.CorpusError):
    """Material that cannot be produced or trusted, or a rule given something it cannot read."""


# ------------------------------------------------------------------------------- failing identifiers

_SUMMARY_MARK = re.compile(r"^=+ short test summary info =+$")
_FAILING = re.compile(r"^(?:FAILED|ERROR) (\S.*)$")


def parse_failing_ids(output: str) -> list[str]:
    """The identifiers of the failing and erroring tests in a pytest output, in order, once each: the node ids of
    the ``FAILED <node id>`` and ``ERROR <node id>`` lines of the section ``short test summary info`` (pytest prints
    it by default, ``-rfE``; its lines are the only ones read, a traceback that says FAILED reads as nothing). A line
    may carry `` - <message>`` after the node id; the node id is what precedes the first `` - `` (a node id that itself
    holds `` - `` would be cut there: the material generator compares the number of identifiers with the junit counts
    of the same run and refuses a material where they differ).

    The judge of the corpus (``lfc.judge``) reads the junit report of the run, not the text; the committed material is
    text, so this is the parser of the text, cross-checked against the junit counts of the generating run."""
    ids: list[str] = []
    inside = False
    for line in output.splitlines():
        if _SUMMARY_MARK.match(line.strip()):
            inside = True
            continue
        if not inside:
            continue
        found = _FAILING.match(line)
        if found:
            node = found.group(1).split(" - ", 1)[0].strip()
            if node and node not in ids:
                ids.append(node)
    return ids


# ------------------------------------------------------------------------------------------ material

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _user_names() -> set[str]:
    names = {os.environ.get(k, "") for k in ("USER", "LOGNAME")}
    try:
        names.add(getpass.getuser())
    except (KeyError, OSError):
        pass
    return {n for n in names if len(n) >= 3}


def mask_output(text: str, *, paths: Mapping[str, str], home: str) -> tuple[str, dict[str, int]]:
    """The output with the machine's own names hidden, and how many of each were hidden. ``paths`` maps a directory
    the run used to its label (every spelling, the longest first: ``/private/var/x`` before ``/var/x``); then the home
    directory of the user (``<home>``), then the user name (``<user>``). Nothing else is touched: a test's own output
    stays as the judge saw it."""
    counts = {"paths": 0, "home": 0, "user": 0}
    pairs = {(spelled, label) for path, label in paths.items() for spelled in {path, os.path.realpath(path)}}
    for spelled, label in sorted(pairs, key=lambda pair: len(pair[0]), reverse=True):
        counts["paths"] += text.count(spelled)
        text = text.replace(spelled, label)
    for spelled in sorted({home, os.path.realpath(home)} - {"", "/"}, key=len, reverse=True):
        counts["home"] += text.count(spelled)
        text = text.replace(spelled, "<home>")
    for name in sorted(_user_names(), key=len, reverse=True):
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])")
        counts["user"] += len(pattern.findall(text))
        text = pattern.sub("<user>", text)
    return text, counts


def leaks(text: str, *, home: str) -> list[str]:
    """What a committed output must not hold: a home path, a user name, a user directory root. Empty when clean."""
    found = []
    for spelled in sorted({home, os.path.realpath(home)} - {"", "/"}):
        if spelled in text:
            found.append(f"home:{spelled.count('/')}-component path")
    for name in _user_names():
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", text):
            found.append("user name")
    if re.search(r"/(?:Users|home)/[^/\s<]+", text):
        found.append("user directory")
    return found


def capture_output(repo: Path, task: Mapping[str, Any], work_root: Path) -> dict[str, Any]:
    """The protected tests of ``task`` on its UNMODIFIED base bundle, run by the judge itself (``lfc.judge``: its
    interpreter ``sys.executable``, its command, its environment), with the output it saw. Offline: git and pytest
    only, no model, no cloud. The bundle is the one every arm gets (the base tree without the protected tests, in a
    new repository) and is removed afterwards."""
    dest = Path(work_root) / f"material-pr{task['pr']}"
    bundle = lfc.build_bundle(repo, task, dest / "bundle")
    seen: dict[str, Any] = {}
    try:
        verdict = lfc.judge(repo, task, bundle, strict_report=True, capture=seen)
    finally:
        lfc.remove_bundle(bundle)
        # the directory that held it (created above, nothing else in it)
        try:
            dest.rmdir()
        except OSError:
            pass
    if "stdout" not in seen:
        raise CompressionError(f"PR {task['pr']}: the judge did not run pytest ({verdict.get('note')})")
    return {"verdict": verdict, **seen}


def render_output(stdout: str, stderr: str) -> str:
    """The text committed: what pytest wrote on stdout, then what it wrote on stderr under one fixed line (only when
    it wrote something on stderr)."""
    return stdout if not stderr.strip() else f"{stdout}\n--- stderr ---\n{stderr}"


def material_entry(task: Mapping[str, Any], captured: Mapping[str, Any], *, home: str) -> tuple[str, dict[str, Any]]:
    """``(committed text, index entry)`` of one task; refuses (``CompressionError``) a material the protocol cannot
    use: no failing test, a parser that disagrees with the junit counts of the same run, or a leak left after the
    masking."""
    raw = render_output(captured["stdout"], captured["stderr"])
    text, masked = mask_output(raw, paths={captured["candidate"]: "<bundle>", captured["tmp_dir"]: "<tmp>"}, home=home)
    problems = leaks(text, home=home)
    if problems:
        raise CompressionError(f"PR {task['pr']}: the output still holds {', '.join(problems)} after masking")
    passed, failed, errors, skipped = captured["junit_counts"]
    ids = parse_failing_ids(text)
    if not ids:
        raise CompressionError(f"PR {task['pr']}: no failing test in the output (the base must fail its protected tests)")
    if len(ids) != failed + errors:
        raise CompressionError(f"PR {task['pr']}: the text parser found {len(ids)} failing tests, the junit report of "
                               f"the same run {failed + errors} (failed {failed}, errors {errors})")
    data = text.encode("utf-8")
    return text, {
        "pr": task["pr"], "file": f"{MATERIAL_DIR}/pr{task['pr']}.txt", "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data), "lines": len(text.splitlines()), "token_estimate": None,
        "token_estimate_note": "unknown: no exact local tokenizer count was taken",
        "pytest_exit_code": captured["returncode"], "junit": {"passed": passed, "failed": failed, "errors": errors,
                                                               "skipped": skipped},
        "failing_ids": ids, "parser_cross_check": "ok", "masked": masked,
        "stderr_bytes": len(captured["stderr"].encode("utf-8"))}


def write_material(entries: Mapping[int, tuple[str, dict[str, Any]]], directory: Path, *, provenance: Mapping[str, Any]
                   ) -> Path:
    """Write the texts and the index under ``directory`` (the qualification folder); never overwrites. The index is
    keyed by PR, in the order given."""
    index = Path(directory) / f"{MATERIAL_DIR}.json"
    folder = Path(directory) / MATERIAL_DIR
    if index.exists() or folder.exists():
        raise CompressionError(f"{index.name} or {MATERIAL_DIR}/ exists: a material is never overwritten")
    folder.mkdir(parents=True)
    for pr, (text, entry) in entries.items():
        (Path(directory) / entry["file"]).write_bytes(text.encode("utf-8"))
    body = {"schema": MATERIAL_SCHEMA, "provenance": dict(provenance),
            "tasks": {str(pr): entry for pr, (_, entry) in entries.items()}}
    index.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return index


def load_material(directory: Path, spec: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    """The committed material of the tasks (``compression.material`` of the config: ``{file, sha256}``), the single
    source of the launcher: refused when the index or a text is absent or does not match its sha256, or when the
    failing identifiers parsed from a text differ from the ones the index lists. ``{pr: {text, bytes, lines, sha256,
    failing_ids}}``."""
    path = Path(directory) / spec["file"]
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CompressionError(f"material index {spec['file']} unreadable: {exc}") from None
    if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
        raise CompressionError(f"material index {spec['file']} does not match the sha256 recorded in the campaign "
                               "config: the committed material changed")
    body = json.loads(raw)
    if body.get("schema") != MATERIAL_SCHEMA:
        raise CompressionError(f"material index {spec['file']}: unexpected schema")
    out: dict[int, dict[str, Any]] = {}
    for key, entry in body["tasks"].items():
        try:
            data = (Path(directory) / entry["file"]).read_bytes()
        except OSError as exc:
            raise CompressionError(f"material text {entry['file']} unreadable: {exc}") from None
        if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["bytes"]:
            raise CompressionError(f"material text {entry['file']} does not match the index (sha256 or size)")
        text = data.decode("utf-8")
        if parse_failing_ids(text) != entry["failing_ids"] or not entry["failing_ids"]:
            raise CompressionError(f"material text {entry['file']}: the failing tests parsed from the text are not "
                                   "the ones the index lists")
        out[int(key)] = {"text": text, "bytes": entry["bytes"], "lines": entry["lines"], "sha256": entry["sha256"],
                         "failing_ids": list(entry["failing_ids"])}
    return out


def size_table(material: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    """The measured size distribution of the material (bytes), apart from any rule: what there is to compress."""
    sizes = sorted(m["bytes"] for m in material.values())
    if not sizes:
        return {"tasks": 0}
    return {"tasks": len(sizes), "bytes_min": sizes[0], "bytes_median": statistics.median(sizes),
            "bytes_max": sizes[-1], "bytes_total": sum(sizes),
            "tasks_under_4096_bytes": sum(1 for s in sizes if s < 4096)}


# ----------------------------------------------------------------------------------------- local call

class LocalCallUnavailable(CompressionError):
    """The local endpoint could not be reached at all (nothing listens, the connection was refused): the attempt could
    not be made, an instrument failure (void, replayed once like any attempt cut before its verdict), never a failed
    summary of the candidate."""


def check_endpoint(url: Any) -> str:
    """The endpoint, if and only if it is a plain ``http`` URL on a LITERAL loopback address (FOUNDRY-ADR-0007: the
    only network the local use may reach). A name, a proxy, credentials in the URL or another scheme are refused."""
    parsed = urllib.parse.urlsplit(url) if isinstance(url, str) else None
    try:
        host = ipaddress.ip_address(parsed.hostname) if parsed and parsed.hostname else None
        port = parsed.port if parsed else None
    except ValueError:
        host, port = None, None
    if (parsed is None or parsed.scheme != "http" or host is None or not host.is_loopback or parsed.username
            or parsed.password or port is None or not parsed.path.startswith("/")):
        raise CompressionError("compression.local_call.endpoint must be an http URL on a literal loopback address with "
                               "a port and a path (FOUNDRY-ADR-0007: the local use reaches the loopback only)")
    return url


def render(template: str, values: Mapping[str, str]) -> str:
    """``{name}`` placeholders replaced in ONE pass (a value holding braces is not read again)."""
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)


def local_messages(call: Mapping[str, Any], output: str) -> list[dict[str, str]]:
    """The two messages of the one call: the frozen system text and the frozen prompt with the output. ``{budget}`` is
    the size the summary is asked to stay under (``target_fraction`` of the output, in characters)."""
    budget = int(len(output.encode("utf-8")) * call["target_fraction"])
    values = {"output": output, "budget": str(budget)}
    return [{"role": "system", "content": call["system"]},
            {"role": "user", "content": render(call["prompt"], values)}]


def request_body(call: Mapping[str, Any], model: str, output: str) -> dict[str, Any]:
    """The body of the one Chat Completions call: non-streaming, NO ``tools`` (and nothing else than what is listed)."""
    return {"model": model, "messages": local_messages(call, output), "temperature": call["temperature"],
            "max_tokens": call["max_tokens"], "stream": False}


def call_local(call: Mapping[str, Any], model: str, output: str, *,
               opener: urllib.request.OpenerDirector | None = None) -> dict[str, Any]:
    """The one local call, exactly once and never retried. ``{summary, reason, http_status, finish_reason, usage}``:
    ``summary`` is the raw ``content`` of the answer, or ``None`` with a ``reason`` for a failed summary: ``timeout``
    (nothing back within ``timeout_seconds``), ``http_<status>`` (the endpoint refused the request), ``invalid_answer``
    (not a Chat Completions answer), ``empty_answer`` (no text), ``connection_lost`` (cut while answering). An endpoint
    that cannot be reached at all raises ``LocalCallUnavailable``. Proxies are never used."""
    url = check_endpoint(call["endpoint"])
    body = json.dumps(request_body(call, model, output)).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    out: dict[str, Any] = {"summary": None, "reason": None, "http_status": None, "finish_reason": None, "usage": None}
    try:
        with opener.open(request, timeout=call["timeout_seconds"]) as response:
            out["http_status"] = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        out.update(http_status=exc.code, reason=f"http_{exc.code}")
        return out
    except (TimeoutError, socket.timeout):
        out["reason"] = "timeout"
        return out
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            out["reason"] = "timeout"
            return out
        raise LocalCallUnavailable(f"the local endpoint cannot be reached: {type(exc.reason).__name__}") from None
    except (ConnectionError, OSError) as exc:  # cut while the answer came back (http.client errors are OSError too)
        if isinstance(exc, ConnectionRefusedError):
            raise LocalCallUnavailable("the local endpoint refused the connection") from None
        out["reason"] = "connection_lost"
        return out
    except Exception as exc:  # http.client.HTTPException (IncompleteRead, BadStatusLine...): cut or malformed
        out["reason"] = "connection_lost" if type(exc).__module__ == "http.client" else "invalid_answer"
        return out
    try:
        answer = json.loads(raw)
        choice = answer["choices"][0]
        content = choice["message"]["content"]
        if not isinstance(content, str):
            raise TypeError
    except (ValueError, KeyError, IndexError, TypeError):
        out["reason"] = "invalid_answer"
        return out
    out["finish_reason"] = choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None
    usage = answer.get("usage")
    out["usage"] = {k: v for k, v in usage.items() if type(v) is int} if isinstance(usage, dict) else None
    if not content.strip():
        out["reason"] = "empty_answer"
        return out
    out["summary"] = content
    return out


# --------------------------------------------------------------------------------- screening judge

def score_summary(output: str, failing_ids: Sequence[str], summary: str | None) -> dict[str, Any]:
    """The deterministic judge of one summary (no model): ``recall`` = the failing identifiers that appear VERBATIM in
    the summary / the failing identifiers; ``size_ratio`` = bytes of the summary / bytes of the output. A failed
    summary (``None``) scores recall 0 and has no size (neither within the limit nor beyond it). ``counts`` are the
    integers the rule computes exactly with."""
    if not failing_ids:
        raise CompressionError("no failing identifier to cite: the material is unusable")
    output_bytes = len(output.encode("utf-8"))
    cited = [i for i in failing_ids if summary is not None and i in summary]
    counts = {"failing": len(failing_ids), "cited": len(cited), "output_bytes": output_bytes,
              "summary_bytes": None if summary is None else len(summary.encode("utf-8"))}
    ratio = None if summary is None else Fraction(counts["summary_bytes"], output_bytes)
    return {"verdict": "REFUSED" if summary is None else "SCORED", "kind": "compression",
            "recall": round(len(cited) / len(failing_ids), 6),
            "size_ratio": None if ratio is None else round(float(ratio), 6),
            "size_ok": None if ratio is None else ratio <= C1_SIZE_RATIO_MAX,
            "cited": cited, "missing": [i for i in failing_ids if i not in cited], "counts": counts}


def _mean(values: Sequence[Fraction]) -> Fraction | None:
    return sum(values, Fraction(0)) / len(values) if values else None


def candidate_row(rule: Mapping[str, Any], per_task: Mapping[int, Mapping[str, Any]], tasks: Sequence[int]
                  ) -> dict[str, Any]:
    """One candidate's line from the judge verdicts of its tasks (``per_task``: pr -> ``judge``). Exact means. The two
    criteria of the rule: the MEAN recall over the corpus tasks (a failed summary counts 0) at least
    ``recall_mean_min``, and EVERY summary produced at most ``size_ratio_max`` of ITS output (per task, not on the
    total: a summary of a short output is held to the same 25 % as one of a long output; a failed summary has no size
    and is judged on the recall alone)."""
    decided = [pr for pr in tasks if pr in per_task]
    recalls = [Fraction(per_task[pr]["counts"]["cited"], per_task[pr]["counts"]["failing"]) for pr in decided]
    sizes = [per_task[pr]["counts"]["summary_bytes"] or 0 for pr in decided]
    over = [pr for pr in decided if per_task[pr]["size_ok"] is False]
    mean = _mean(recalls)
    complete = len(decided) == len(tasks) and bool(tasks)
    passes = complete and mean >= Fraction(str(rule["recall_mean_min"])) and not over
    return {"tasks_decided": len(decided), "complete": complete,
            "mean_recall": None if mean is None else round(float(mean), 6),
            "mean_recall_exact": None if mean is None else f"{mean.numerator}/{mean.denominator}",
            "recall_ok": None if mean is None else mean >= Fraction(str(rule["recall_mean_min"])),
            "tasks_over_size_limit": over, "size_ok": not over,
            "failed_summaries": [pr for pr in decided if per_task[pr]["verdict"] != "SCORED"],
            "total_summary_bytes": sum(sizes), "passes": passes,
            "per_task": [{"pr": pr, "recall": per_task[pr]["recall"], "size_ratio": per_task[pr]["size_ratio"],
                          "size_ok": per_task[pr]["size_ok"], "verdict": per_task[pr]["verdict"],
                          "summary_bytes": per_task[pr]["counts"]["summary_bytes"],
                          "output_bytes": per_task[pr]["counts"]["output_bytes"]} for pr in decided]}


def select_candidate(rule: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]], order: Sequence[str]
                     ) -> dict[str, Any]:
    """The pre-registered selection. Nothing is selected before every candidate has a verdict on every task
    (``incomplete_screening``). Among the candidates that pass both criteria: the best mean recall, ties by the smaller
    total size of the summaries (bytes, a failed summary counting 0), then by the order of protocol v1. No candidate
    passes: STOP, ``keep_cloud`` - no comparison is played, and that is a conforming result (PAT-ADR-0015: insufficient
    proof keeps the cloud)."""
    if not rows or any(not rows.get(c, {}).get("complete") for c in order):
        return {"selected": None, "reason": "incomplete_screening" if rows else "no_screening_results", "stop": None}
    passing = [c for c in order if rows[c]["passes"]]
    if not passing:
        return {"selected": None, "reason": "no_candidate_passes: keep_cloud", "stop": "keep_cloud"}
    best = max(Fraction(rows[c]["mean_recall_exact"]) for c in passing)
    tied = [c for c in passing if Fraction(rows[c]["mean_recall_exact"]) == best]
    if len(tied) == 1:
        return {"selected": tied[0], "reason": "best_mean_recall", "stop": None}
    smallest = min(rows[c]["total_summary_bytes"] for c in tied)
    sized = [c for c in tied if rows[c]["total_summary_bytes"] == smallest]
    if len(sized) == 1:
        return {"selected": sized[0], "reason": "tie_broken_by_smaller_total_size", "stop": None, "tied": tied}
    return {"selected": sized[0], "reason": "tie_broken_by_candidate_order", "stop": None, "tied": tied}


# -------------------------------------------------------------------------------------- diagnosis

DIAGNOSIS_KEYS = ("file", "function")


def extract_diagnosis(text: str, root: str | None = None) -> dict[str, str]:
    """The diagnosis of the final message of the cloud arm: the LAST JSON object (bare or in a code fence) that has a
    ``file`` and a ``function`` key, both non-empty strings; the path is made repo-relative like a report's. Raises
    ``ValueError`` when there is none (an unreadable answer: an incorrect diagnosis, its cost counted)."""
    decoder, found = json.JSONDecoder(), None
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except ValueError:
            continue
        if isinstance(value, dict) and all(isinstance(value.get(k), str) and value[k].strip() for k in DIAGNOSIS_KEYS):
            found = value
    if found is None:
        raise ValueError("no JSON object with a file and a function in the final message")
    return {"file": lfe.normalize_path(found["file"], root), "function": found["function"].strip()}


def load_diagnosis(stream_log: Path, fmt: str, root: str | None = None
                   ) -> tuple[dict[str, str] | None, str | None]:
    """``(diagnosis, refusal)`` from the kept event stream: the final message of the arm."""
    text = lfe.final_message_text(stream_log, fmt)
    if text is None:
        return None, "no final message"
    try:
        return extract_diagnosis(text, root), None
    except ValueError as exc:
        return None, str(exc)[:200]


def judge_diagnosis(truth: Mapping[str, Any], diagnosis: Mapping[str, str] | None, refusal: str | None
                    ) -> dict[str, Any]:
    """The deterministic judge of a diagnosis (no model). CORRECT when the named file is a product file of the truth
    (``truth["files"]``) AND the named function is a truth function OF THAT FILE, matched as the localization judge
    does (the qualified name or its last component). An unreadable or absent answer is a refusal and an incorrect
    diagnosis: it counts, with its cost."""
    if not truth.get("files") or not truth.get("functions"):
        raise CompressionError(f"PR {truth.get('pr')}: empty ground truth, nothing to judge")
    if diagnosis is None:
        return {"verdict": "REFUSED", "kind": "diagnosis", "correct": False, "file_ok": False, "function_ok": False,
                "note": refusal or "no diagnosis"}
    file_ok = diagnosis["file"] in truth["files"]
    function_ok = any(lfe._function_match({"file": diagnosis["file"], "name": diagnosis["function"]}, t)
                      for t in truth["functions"])
    return {"verdict": "SCORED", "kind": "diagnosis", "correct": file_ok and function_ok, "file_ok": file_ok,
            "function_ok": function_ok, "note": None}


# --------------------------------------------------------------------------------------- the report

def _decided(rec: Mapping[str, Any]) -> bool:
    return bool(rec.get("judge"))


def report_screening(rule: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]], tasks: Sequence[int],
                     order: Sequence[str], warnings: Sequence[str] = (), *, pilot: bool = False) -> dict[str, Any]:
    """The screening table and the pre-registered selection, from the records of the local calls. A task whose call
    was cut (tool failure, interruption) and not replayed is undecided: nothing is selected before every candidate has
    a verdict on every task."""
    mine = [r for r in attempts if r["path"] == SCREEN_PATH]
    gone = lfr._superseded(mine)
    per: dict[str, dict[int, Mapping[str, Any]]] = {}
    undecided: list[dict[str, Any]] = []
    for rec in mine:
        cid = (rec.get("local") or {}).get("candidate")
        if _decided(rec):
            per.setdefault(cid, {})[rec["task"]["pr"]] = rec["judge"]
        elif rec.get("outcome") in lfr.LOST_OUTCOMES and id(rec) not in gone:
            undecided.append({"candidate": cid, "pr": rec["task"]["pr"], "outcome": rec["outcome"],
                              "replayed": bool(rec.get("replay_of"))})
    rows = {cid: candidate_row(rule, per[cid], tasks) for cid in order if cid in per}
    chosen = select_candidate(rule, rows, order) if not undecided and not warnings else {
        "selected": None, "reason": "incomplete_screening", "stop": None}
    if pilot:  # one task: the rule is printed, the pilot's candidate is named by the operator, nothing is selected
        chosen = {"selected": None, "reason": "pilot: no selection (the candidate is named by the operator)",
                  "stop": None}
    unknown = [c for c in per if c not in order]
    if unknown:
        raise CompressionError(f"screening records of candidate(s) {sorted(unknown)} that the protocol does not list")
    return {"candidates": rows, **chosen, "complete": bool(rows) and all(r["complete"] for r in rows.values())
            and len(rows) == len(order) and not undecided, "undecided_tasks": undecided,
            "warnings": list(warnings), "rule": {"recall_mean_min": rule["recall_mean_min"],
                                                 "size_ratio_max": rule["size_ratio_max"]}}


def _premium(records: Sequence[Mapping[str, Any]]) -> int | None:
    """Premium tokens of an arm on a task: every record of it (a replayed one included); unknown when a record's total
    is unknown, EXCEPT an interrupted record that names no cloud execution (it spent nothing)."""
    return lfr._sum_known([0 if lfr._cut_without_cloud(r) else r["premium"]["billing_total"] for r in records])


def arm_state(records: Sequence[Mapping[str, Any]], gone: set[int]) -> dict[str, Any]:
    """The state of one arm on one task from its records: decided (a judge verdict: correct or not), or undecided with
    the causes (the outcomes that leave it so), and its premium (unknown stays ``None``, never 0)."""
    decided = [r for r in records if _decided(r) and not r.get("contaminated")]
    out: dict[str, Any] = {"records": len(records), "premium": _premium(records) if records else 0}
    if decided:
        out.update(decided=True, correct=bool(decided[0]["judge"]["correct"]), causes=[])
    else:
        causes = [f"{r.get('segment')}:{r['outcome']}" for r in records
                  if id(r) not in gone and r.get("outcome") in lfr.UNKNOWN_OUTCOMES]
        out.update(decided=False, correct=None, causes=causes or ["not_played"])
    return out


def apply_rule(rule: Mapping[str, Any], tasks: Sequence[int], states: Mapping[str, Mapping[int, Mapping[str, Any]]],
               unknown_detail: Sequence[str], warnings: Sequence[str], sessions_of: Mapping[int, Sequence[str]]
               ) -> dict[str, Any]:
    """The decision rule of the comparison, written in advance and applied in this order (pilot: the same code on its
    one task, ``paired_decided_min`` 1).

    D = the tasks DECIDED in both arms (an arm is decided on a task when a record carries a diagnosis verdict, correct
    or not: an unreadable answer is a decided, incorrect one, its cost counted); an undecided task (contaminated,
    tool error, interruption, cap, summary absent) only leaves D. 1. Fewer than ``paired_decided_min`` tasks in D:
    inconclusive. 2. A premium total unknown on a task of D: inconclusive (never zero). 3. correct_S < correct_F on D:
    keep_cloud. 4. correct_F = 0: inconclusive (the ratio is undefined; also when correct_S > 0). 5. premium per
    correct diagnosis: S <= ``premium_per_correct_ratio_max`` x F, retained; else keep_cloud. A retained verdict must
    also hold in the worst case over ALL the tasks (undecided tasks of S counted incorrect, those of F correct), else
    inconclusive. Last, campaign-level reasons (a cloud execution the ledger started and never settled, one no record
    names, an interrupted or unknown-token execution of a task of D or of no task, a start with neither record nor
    replay) make the verdict inconclusive whatever D says. Nothing is promoted."""
    f, s = states["F"], states["S"]
    paired = [pr for pr in tasks if f[pr]["decided"] and s[pr]["decided"]]
    corr = {a: sum(1 for pr in paired if states[a][pr]["correct"]) for a in ARMS}
    prem = {a: lfr._sum_known([states[a][pr]["premium"] for pr in paired]) for a in ARMS}
    ratio_max = Fraction(str(rule["premium_per_correct_ratio_max"]))
    wasted = {a: [states[a][pr]["premium"] for pr in tasks if not states[a][pr]["decided"]] for a in ARMS}
    detail: dict[str, Any] = {
        "paired_decided_min": rule["paired_decided_min"], "paired_decided": paired, "paired_decided_count": len(paired),
        "undecided": {a: {str(pr): states[a][pr]["causes"] for pr in tasks if not states[a][pr]["decided"]}
                      for a in ARMS},
        "premium_tokens_on_undecided_tasks": {a: (None if any(v is None for v in vals) else sum(vals))
                                              for a, vals in wasted.items()},
        "correct_on_paired": corr, "premium_on_paired": prem,
        "premium_per_correct": {a: None if prem[a] is None or not corr[a] else round(prem[a] / corr[a], 3)
                                for a in ARMS},
        "ratio_max": float(ratio_max), "ratio": None, "failed_criteria": []}
    worst_f = sum(1 for pr in tasks if f[pr]["correct"] or not f[pr]["decided"])
    worst_s = sum(1 for pr in tasks if s[pr]["correct"])
    detail["worst_case"] = {"F_undecided_counted_correct": worst_f, "S_undecided_counted_incorrect": worst_s,
                            "robust": None}
    verdict, reason = "inconclusive", None
    if len(paired) < rule["paired_decided_min"]:
        reason = f"paired_decided_set_below_{rule['paired_decided_min']}"
    elif prem["F"] is None or prem["S"] is None:
        reason = "premium_total_unknown"
    elif corr["S"] < corr["F"]:
        detail["failed_criteria"] = ["accuracy"]
        verdict, reason = "keep_cloud", "not_retained_on_paired_set"
    elif not corr["F"]:
        reason = "no_correct_diagnosis_in_the_full_arm_ratio_undefined"
    else:
        detail["ratio"] = round(float(Fraction(prem["S"] * corr["F"], prem["F"] * corr["S"])), 4) if prem["F"] else None
        economy = prem["S"] * corr["F"] <= ratio_max * prem["F"] * corr["S"]
        if not economy:
            detail["failed_criteria"] = ["economy"]
            verdict, reason = "keep_cloud", "not_retained_on_paired_set"
        else:
            detail["worst_case"]["robust"] = worst_s >= worst_f
            verdict, reason = ("retained", None) if worst_s >= worst_f else (
                "inconclusive", "not_robust_to_undecided_tasks")
    outside = {sid for pr in tasks if pr not in paired for sid in sessions_of.get(pr, ())}
    campaign_level = sorted(e for e in unknown_detail if not (
        e.partition(":")[0] in ("interrupted", "tokens_unknown") and e.partition(":")[2] in outside))
    campaign_level += [f"start_without_record_nor_replay:{w}" for w in warnings]
    detail.update(verdict_before_campaign_level=verdict, reason_before_campaign_level=reason,
                  campaign_level_reasons=campaign_level)
    if campaign_level:
        verdict, reason = "inconclusive", "campaign_level_unknown_work"
    detail.update(verdict=verdict, reason=reason)
    return detail


def report_comparison(rule: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]], tasks: Sequence[int],
                      screening: Mapping[str, Any], unknown_detail: Sequence[str], warnings: Sequence[str],
                      *, pilot: bool = False) -> dict[str, Any]:
    """The two arms F (full output) and S (summary of the retained candidate) and the pre-registered decision. The
    decision concerns S only (``retained`` = this compression is worth it for this use); nothing is promoted."""
    arms = [r for r in attempts if r["path"] in ARMS]
    out: dict[str, Any] = {"decision": "inconclusive", "recommendation": None, "arms": {}, "warnings": list(warnings)}
    candidates = {(r.get("diagnosis") or {}).get("compared_candidate") for r in arms}
    candidates.discard(None)
    if not pilot and screening.get("stop") == "keep_cloud":
        if arms:
            raise CompressionError("arm records exist although the screening stopped on keep_cloud")
        out.update(reason="screening_stopped_on_keep_cloud", complete=True,
                   campaign_conclusion="keep_cloud_screening_stop")
        return out
    if not pilot and candidates and candidates != {screening.get("selected")}:
        raise CompressionError(f"the compared candidate(s) {sorted(candidates)} are not the one the screening selected "
                               f"({screening.get('selected')}: {screening.get('reason')})")
    if len(candidates) > 1:
        raise CompressionError(f"several candidates were compared: {sorted(candidates)}")
    gone = lfr._superseded(arms)
    chosen = next(iter(candidates), None)
    states: dict[str, dict[int, dict[str, Any]]] = {a: {} for a in ARMS}
    sessions_of: dict[int, list[str]] = {}
    for pr in tasks:
        for arm in ARMS:
            mine = [r for r in arms if r["path"] == arm and r["task"]["pr"] == pr]
            state = arm_state(mine, gone)
            if not mine and arm == "S" and chosen is not None:
                # nothing to send: the screening call of the retained candidate yielded no summary on this task
                failed = [r for r in attempts if r["path"] == SCREEN_PATH and r["task"]["pr"] == pr
                          and (r.get("local") or {}).get("candidate") == chosen and _decided(r)
                          and r["judge"]["verdict"] != "SCORED"]
                if failed:
                    state["causes"] = ["no_summary"]
            states[arm][pr] = state
            sessions_of.setdefault(pr, []).extend(s for r in mine for s in r.get("cloud_sessions") or [])
    complete = all(states[a][pr]["records"] or states[a][pr]["causes"] == ["no_summary"] for a in ARMS for pr in tasks)
    out["arms"] = {a: {"tasks": {str(pr): {k: states[a][pr][k] for k in ("decided", "correct", "premium", "causes")}
                                  for pr in tasks},
                       "correct": sum(1 for pr in tasks if states[a][pr]["correct"]),
                       "decided": sum(1 for pr in tasks if states[a][pr]["decided"])} for a in ARMS}
    out["summary_of_candidate"] = chosen
    out["complete"] = complete
    paired = apply_rule(rule, tasks, states, unknown_detail, warnings, sessions_of) if complete else None
    if paired is not None:
        out["paired_rule"] = paired
        out["decision"], out["recommendation"] = {"retained": ("retained", "S"), "keep_cloud": ("keep_cloud", "F")}.get(
            paired["verdict"], ("inconclusive", None))
    # PAT-ADR-0015: insufficient proof keeps the cloud. A complete comparison that is neither retained nor failed
    # concludes the campaign on keeping the cloud; an incomplete campaign concludes nothing yet.
    out["campaign_conclusion"] = ("incomplete_campaign" if not complete else
                                  "retain_compression" if out["decision"] == "retained" else
                                  "keep_cloud" if out["decision"] == "keep_cloud" else "keep_cloud_insufficient_evidence")
    return out


def size_table_of(attempts: Sequence[Mapping[str, Any]], tasks: Sequence[int]) -> dict[str, Any]:
    """The measured sizes of the outputs and, per candidate, of the summaries, from the screening records (a reading
    apart from the rule): what there was to compress, and how much of it each candidate kept."""
    sizes = {rec["task"]["pr"]: rec["judge"]["counts"]["output_bytes"] for rec in attempts
             if rec["path"] == SCREEN_PATH and _decided(rec)}
    per_candidate: dict[str, dict[str, Any]] = {}
    for rec in attempts:
        if rec["path"] == SCREEN_PATH and _decided(rec):
            per_candidate.setdefault(rec["local"]["candidate"], {})[str(rec["task"]["pr"])] = \
                rec["judge"]["counts"]["summary_bytes"]
    ordered = sorted(sizes.values())
    return {"output_bytes_by_task": {str(pr): sizes[pr] for pr in tasks if pr in sizes},
            "output_bytes_total": sum(ordered), "output_bytes_median": statistics.median(ordered) if ordered else None,
            "summary_bytes_by_candidate": per_candidate}


def report_sections(campaign: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]], unknown_detail: Sequence[str],
                    warnings: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """The two sections a compression report adds: ``compression_screening`` and ``compression_comparison``, plus the
    size table (a reading apart from the rule)."""
    conf, rules = campaign["compression"], campaign["rules"]
    label, tasks = conf["task_set"], list(conf["tasks"])
    pilot = campaign.get("protocol") == PROTOCOL_C1_PILOT
    mine = [r for r in attempts if r["task"].get("set") == label]
    screening = report_screening(rules["compression_screening"], mine, tasks, rules["compression_screening"]["candidates"],
                                 warnings.get("screen_compression", ()), pilot=pilot)
    comparison = report_comparison(rules["compression_comparison"], mine, tasks, screening, unknown_detail,
                                   warnings.get("compare_compression", ()), pilot=pilot)
    return {"compression_screening": screening, "compression_comparison": comparison,
            "compression_sizes": size_table_of(mine, tasks)}


# ----------------------------------------------------------------------------- material generation

def _digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tool_versions() -> dict[str, str]:
    import platform
    from importlib import metadata
    try:
        pytest_version = metadata.version("pytest")
    except metadata.PackageNotFoundError:
        pytest_version = "unknown"
    return {"python": platform.python_version(), "pytest": pytest_version, "system": platform.system()}


def generate_material(repo: Path, tasks: Sequence[Mapping[str, Any]], work_root: Path, out_dir: Path, *,
                      snapshot_path: Path, manifest_path: Path, today: Any) -> dict[str, Any]:
    """The offline verb ``compression-material``: for each task, the protected tests on the unmodified base bundle
    (``capture_output``), masked and checked (``material_entry``), written under ``out_dir`` with the index and the
    hashes. Nothing is written unless every task passed its checks. Returns the size table (bytes, lines, failing
    tests per task)."""
    import subprocess
    home = os.environ.get("HOME") or str(Path.home())
    entries: dict[int, tuple[str, dict[str, Any]]] = {}
    for task in tasks:
        entries[task["pr"]] = material_entry(task, capture_output(repo, task, work_root), home=home)
    try:
        commit = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=False, timeout=30).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    try:  # uncommitted changes in the tooling: the commit alone then does not describe the code that ran
        dirty = bool(subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "status", "--porcelain", "--", "."],
                                    capture_output=True, text=True, check=False, timeout=30).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        dirty = None
    provenance = {
        "date": today.isoformat(), "tooling_commit": commit, "tooling_dirty": dirty, **_tool_versions(),
        "snapshot_sha256": _digest(snapshot_path), "manifest_sha256": _digest(manifest_path),
        "command": "the judge's own: <python> -P -m pytest -q -p no:cacheprovider --junitxml=<report> "
                   "--confcutdir=<bundle>/plugins/foundry/tests --rootdir=<bundle>/plugins/foundry -c <merged pytest.ini> "
                   "<the protected tests of the task>, in <bundle>/plugins/foundry, on the base tree without the "
                   "protected tests, the protected tests restored from the merged commit (lfc.judge)",
        "masking": "the bundle directory -> <bundle>, the judge's temporary directory -> <tmp>, the home directory -> "
                   "<home>, the user name -> <user>; counts per task in each entry; nothing else is changed",
        "stderr": "appended after a line '--- stderr ---' only when pytest wrote something on stderr"}
    index = write_material(entries, out_dir, provenance=provenance)
    rows = {str(pr): {k: e[k] for k in ("bytes", "lines", "failing_ids", "junit", "masked", "sha256")}
            for pr, (_, e) in entries.items()}
    for row in rows.values():
        row["failing_tests"] = len(row.pop("failing_ids"))
    material = {pr: {"bytes": e["bytes"]} for pr, (_, e) in entries.items()}
    return {"index": index.name, "index_sha256": _digest(index), "tasks": rows, "sizes": size_table(material)}
