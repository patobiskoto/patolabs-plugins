"""PAT-118: protocol c1 of the PAT-19 qualification (DRAFT, phase 1): the one-call compression of a test output.

Fake arms only: no ``claude``, no model, no ``lms``, no cloud, never the real HOME or ``~/.config/foundry``. The one
local call is either an injected callable or a loopback HTTP server of the test; the cloud diagnoser is a stand-in script
that speaks a Claude Code stream. The material is read from the repository; its generator runs pytest on a throwaway
repository of the test. The frozen v1 to v5 files are only read (the v5 tests hash them); everything the c1 keys add is
off, and refused, without them."""
from __future__ import annotations

import datetime as dt
import hashlib
import http.server
import json
import os
import re
import sys
import threading
from pathlib import Path

import pytest

from foundry import local_first_compression as lfk
from foundry import local_first_exploration as lfe
from foundry import local_first_runner as lfr
from test_local_first_corpus import MOD, _make_repo
from test_local_first_exploration_runner import facts_with
from test_local_first_runner import TODAY, ledger_of, results, write_envelope

QUALIFICATION = Path(__file__).resolve().parents[1] / "docs" / "qualification"
C1 = QUALIFICATION / "pat-19-campaign-c1.json"
PILOT = QUALIFICATION / "pat-19-campaign-c1-pilot.json"
MATERIAL = QUALIFICATION / "pat-19-compression-material-v1.json"
TWELVE = [26, 38, 25, 42, 33, 37, 30, 83, 27, 24, 48, 19]
V1_FIVE = ["qwen3.8-27b-mlx-6bit", "qwen3.8-27b-mlx-4bit", "qwen3.6-35b-a3b-mlx-4bit", "muse-glimmer-30b-gguf",
           "qwen3-coder-30b-a3b-mlx-4bit"]
NOT_THE_WORD = ("confin" + "é", "confin" + "ed")  # the two spellings the protocol never writes (assembled: this file avoids them too)
ANSWER = {"file": MOD, "function": "add"}
SWAP = "total = 2048.00M  used = 100.00M  free = 1948.00M"

OUTPUT = """F.F
=================================== FAILURES ===================================
____________________________ test_added ____________________________
E   assert 1 == 2
FAILED inside a traceback is not an identifier
=========================== short test summary info ============================
FAILED tests/test_m.py::test_added - assert 1 == 2
ERROR tests/test_m.py::test_changed[a - b] - boom
2 failed, 1 passed in 0.12s
"""
IDS = ["tests/test_m.py::test_added", "tests/test_m.py::test_changed[a"]  # a node id is cut at the first ' - '


def _load(path):
    return json.loads(Path(path).read_text("utf-8"))


# ------------------------------------------------------------------------------------ the failing identifiers

def test_the_parser_reads_only_the_short_summary_section_and_cuts_at_the_message():
    assert lfk.parse_failing_ids(OUTPUT) == IDS
    assert lfk.parse_failing_ids("FAILED tests/x.py::t - boom\n") == []  # outside the section: not an identifier
    assert lfk.parse_failing_ids("no summary at all") == []
    twice = OUTPUT + "=== short test summary info ===\nFAILED tests/test_m.py::test_added\n"
    assert lfk.parse_failing_ids(twice) == IDS  # once each, in order


def test_a_clean_material_entry_is_masked_checked_and_hashed(tmp_path):
    home, user = str(tmp_path / "home"), "someone"
    raw = (f"== short test summary info ==\nFAILED tests/t.py::test_a - boom\n{tmp_path}/bundle/x.py:3: in f\n"
           f"{home}/.cache/y pytest-of-{user}/pytest-0/z\n1 failed in 0.1s\n")
    captured = {"stdout": raw, "stderr": "", "candidate": str(tmp_path / "bundle"), "tmp_dir": str(tmp_path / "judge"),
                "junit_counts": [0, 1, 0, 0], "returncode": 1}
    text, masked = lfk.mask_output(raw, paths={captured["candidate"]: "<bundle>", captured["tmp_dir"]: "<tmp>"}, home=home)
    assert "<bundle>/x.py:3: in f" in text and "<home>/.cache/y" in text and masked["paths"] == 1 and masked["home"] == 1
    task = {"pr": 7}
    text, entry = lfk.material_entry(task, {**captured, "stdout": text}, home=home)
    assert entry["sha256"] == hashlib.sha256(text.encode()).hexdigest() and entry["bytes"] == len(text.encode())
    assert entry["failing_ids"] == ["tests/t.py::test_a"] and entry["parser_cross_check"] == "ok"
    assert entry["token_estimate"] is None and "unknown" in entry["token_estimate_note"]
    with pytest.raises(lfk.CompressionError, match="junit report"):  # the parser and the judge's report disagree
        lfk.material_entry(task, {**captured, "stdout": text, "junit_counts": [0, 2, 0, 0]}, home=home)
    with pytest.raises(lfk.CompressionError, match="no failing test"):
        lfk.material_entry(task, {**captured, "stdout": "1 passed in 0.1s\n", "junit_counts": [1, 0, 0, 0]}, home=home)
    with pytest.raises(lfk.CompressionError, match="still holds"):  # a leak left after the masking is refused
        lfk.material_entry(task, {**captured, "stdout": text + "/Users/somebody/x\n"}, home=home)


def test_stderr_is_appended_under_a_fixed_line_only_when_there_is_some():
    assert lfk.render_output("out\n", "") == "out\n" and lfk.render_output("out\n", " \n") == "out\n"
    assert lfk.render_output("out\n", "warn\n") == "out\n\n--- stderr ---\nwarn\n"


def test_the_generator_runs_the_protected_tests_of_a_throwaway_repository_offline(tmp_path):
    repo, snap, _, _ = _make_repo(tmp_path)
    manifest, out = tmp_path / "manifest.json", tmp_path / "out"
    manifest.write_text("{}", encoding="utf-8")
    (tmp_path / "snap.json").write_text(json.dumps(snap), encoding="utf-8")
    out.mkdir()
    table = lfk.generate_material(repo, snap["prs"][:1], tmp_path / "work", out, snapshot_path=tmp_path / "snap.json",
                                  manifest_path=manifest, today=dt.date(2026, 10, 9))
    assert table["tasks"]["1"]["failing_tests"] >= 1 and table["sizes"]["tasks"] == 1
    body = _load(out / f"{lfk.MATERIAL_DIR}.json")
    spec = {"file": f"{lfk.MATERIAL_DIR}.json", "sha256": hashlib.sha256((out / f"{lfk.MATERIAL_DIR}.json").read_bytes()).hexdigest()}
    loaded = lfk.load_material(out, spec)
    assert loaded[1]["failing_ids"] == body["tasks"]["1"]["failing_ids"] and loaded[1]["failing_ids"]
    assert all(i.startswith("plugins/foundry/") is False for i in loaded[1]["failing_ids"])  # relative to the plugin root
    assert not list((tmp_path / "work").iterdir())  # the throwaway bundle is removed
    with pytest.raises(lfk.CompressionError, match="never overwritten"):
        lfk.write_material({}, out, provenance={})
    # a text or an index that changed is refused
    text = out / body["tasks"]["1"]["file"]
    text.write_text(text.read_text("utf-8") + "x", encoding="utf-8")
    with pytest.raises(lfk.CompressionError, match="does not match the index"):
        lfk.load_material(out, spec)
    with pytest.raises(lfk.CompressionError, match="does not match the sha256"):
        lfk.load_material(out, {**spec, "sha256": "0" * 64})


# -------------------------------------------------------------------------- the committed material

def test_the_committed_material_is_complete_hashed_clean_and_its_sizes_are_told():
    material = lfk.load_material(QUALIFICATION, {"file": MATERIAL.name, "sha256": hashlib.sha256(MATERIAL.read_bytes()).hexdigest()})
    assert sorted(material) == sorted(TWELVE)
    body = _load(MATERIAL)
    assert body["provenance"]["tooling_commit"] and "token_estimate" in body["tasks"]["26"]
    for pr, entry in body["tasks"].items():
        data = (QUALIFICATION / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"] and len(data) == entry["bytes"]
        assert len(data.decode().splitlines()) == entry["lines"] and entry["token_estimate"] is None
        assert entry["failing_ids"] and entry["parser_cross_check"] == "ok"
        assert len(entry["failing_ids"]) == entry["junit"]["failed"] + entry["junit"]["errors"]
        text = data.decode()
        # no home path, user name or secret-shaped string: the masking told what it hid
        assert not lfk.leaks(text, home=os.path.expanduser("~")), pr
        assert not re.search(r"/Users/|/home/[a-z]|/private/var|/var/folders|lin_api_|ghp_|sk-[A-Za-z0-9]{10}|Bearer ", text), pr
    sizes = lfk.size_table(material)
    assert sizes["tasks"] == 12 and sizes["bytes_min"] == 1274 and sizes["bytes_max"] == 425688
    assert sizes["tasks_under_4096_bytes"] == 4 and sizes["bytes_median"] == (7837 + 10835) / 2
    assert material[48]["bytes"] / sizes["bytes_total"] > 0.7  # one output holds most of the bytes: told in the protocol


# ------------------------------------------------------------------------------------ the screening judge

def test_a_summary_is_judged_on_verbatim_identifiers_and_its_own_size():
    out = "x" * 1000
    ids = ["a::t1", "a::t2", "a::t3", "a::t4"]
    full = lfk.score_summary(out, ids, "a::t1 a::t2 a::t3 a::t4" + "y" * 100)
    assert full["recall"] == 1.0 and full["counts"]["cited"] == 4 and full["size_ok"] is True and full["verdict"] == "SCORED"
    part = lfk.score_summary(out, ids, "a::t1 and a::t2 but 't3' alone, and a::t")
    assert part["counts"]["cited"] == 2 and part["missing"] == ["a::t3", "a::t4"]  # a name alone is not an identifier
    edge = lfk.score_summary(out, ids, "a::t1 " + "z" * (250 - 6))  # exactly 25 % of 1000 bytes
    assert edge["counts"]["summary_bytes"] == 250 and edge["size_ok"] is True
    over = lfk.score_summary(out, ids, "a::t1 " + "z" * (251 - 6))
    assert over["size_ok"] is False
    failed = lfk.score_summary(out, ids, None)
    assert failed["verdict"] == "REFUSED" and failed["recall"] == 0.0 and failed["size_ratio"] is None
    assert failed["size_ok"] is None and failed["counts"]["summary_bytes"] is None
    wide = lfk.score_summary("é" * 100, ["i"], "i")  # sizes are bytes, not characters
    assert wide["counts"]["output_bytes"] == 200
    with pytest.raises(lfk.CompressionError, match="no failing identifier"):
        lfk.score_summary(out, [], "x")


RULE = {"recall_mean_min": 0.8, "size_ratio_max": 0.25}


def _verdict(cited, failing, summary_bytes, output_bytes=1000):
    if summary_bytes is None:
        return lfk.score_summary("x" * output_bytes, [f"i{n}" for n in range(failing)], None)
    summary = " ".join(f"i{n}" for n in range(cited)).ljust(summary_bytes, ".")
    assert len(summary) == summary_bytes
    return lfk.score_summary("x" * output_bytes, [f"i{n}" for n in range(failing)], summary)


def _row(per_task, tasks=(1, 2, 3, 4, 5)):
    return lfk.candidate_row(RULE, dict(zip(tasks, per_task)), list(tasks))


def test_a_candidate_passes_on_the_mean_recall_and_on_every_summary_size():
    good = [_verdict(1, 1, 100)] * 5
    assert _row(good)["passes"] is True
    # mean recall exactly 0.8 passes, just under fails (exact fractions, 4 of 5 tasks fully cited)
    four = [_verdict(1, 1, 100)] * 4 + [_verdict(0, 1, 100)]
    row = _row(four)
    assert row["mean_recall_exact"] == "4/5" and row["recall_ok"] is True and row["passes"] is True
    assert _row([_verdict(1, 1, 100)] * 3 + [_verdict(0, 1, 100)] * 2)["passes"] is False
    # ONE summary over 25 % of ITS output disqualifies, however good the mean recall (the size is per task)
    big = [_verdict(1, 1, 100)] * 4 + [_verdict(1, 1, 251)]
    row = _row(big)
    assert row["recall_ok"] is True and row["tasks_over_size_limit"] == [5] and row["size_ok"] is False and not row["passes"]
    # a short output is held to the same 25 %
    short = [_verdict(1, 1, 100)] * 4 + [lfk.score_summary("x" * 40, ["i0"], "i0 " + "." * 20)]
    assert _row(short)["tasks_over_size_limit"] == [5]
    # a failed summary scores recall 0, has no size, and is not a size violation
    failed = [_verdict(1, 1, 100)] * 4 + [_verdict(0, 1, None)]
    row = _row(failed)
    assert row["failed_summaries"] == [5] and row["tasks_over_size_limit"] == [] and row["passes"] is True  # 4/5 = 0.8
    assert _row([_verdict(1, 1, 100)] * 3 + [_verdict(0, 1, None)] * 2)["passes"] is False
    # incomplete: never passes
    assert _row(good[:3], tasks=(1, 2, 3)) ["complete"] is True
    partial = lfk.candidate_row(RULE, {1: good[0]}, [1, 2])
    assert partial["complete"] is False and partial["passes"] is False and partial["tasks_decided"] == 1


def _rows(**spec):
    return {c: _row(per_task) for c, per_task in spec.items()}


def test_the_selection_rule_and_the_stop_on_keep_the_cloud():
    order = ["c1", "c2", "c3"]
    perfect, ok = [_verdict(1, 1, 100)] * 5, [_verdict(1, 1, 100)] * 4 + [_verdict(0, 1, 100)]
    bad = [_verdict(0, 1, 100)] * 5
    pick = lfk.select_candidate
    assert pick(RULE, {}, order) == {"selected": None, "reason": "no_screening_results", "stop": None}
    assert pick(RULE, _rows(c1=perfect, c2=perfect), order)["reason"] == "incomplete_screening"  # c3 has no row
    stop = pick(RULE, _rows(c1=bad, c2=bad, c3=bad), order)
    assert stop == {"selected": None, "reason": "no_candidate_passes: keep_cloud", "stop": "keep_cloud"}
    assert pick(RULE, _rows(c1=ok, c2=perfect, c3=bad), order) == {"selected": "c2", "reason": "best_mean_recall", "stop": None}
    # ties on the mean recall: the smaller total size, then the order of protocol v1
    small, large = [_verdict(1, 1, 50)] * 5, [_verdict(1, 1, 200)] * 5
    tie = pick(RULE, _rows(c1=large, c2=small, c3=small[:4] + [_verdict(1, 1, 60)]), order)
    assert tie["selected"] == "c2" and tie["reason"] == "tie_broken_by_smaller_total_size"
    tie = pick(RULE, _rows(c1=small, c2=small, c3=large), order)
    assert tie["selected"] == "c1" and tie["reason"] == "tie_broken_by_candidate_order"
    # a candidate that does not pass never wins, even with the best recall of those that fail the size limit
    oversize = [_verdict(1, 1, 251)] * 5
    assert pick(RULE, _rows(c1=oversize, c2=ok, c3=bad), order)["selected"] == "c2"


# --------------------------------------------------------------------------------------- the diagnosis

TRUTH = {"pr": 1, "files": [MOD], "functions": [[MOD, "Tracker.update"], [MOD, "add"]], "created_files": []}


def test_a_diagnosis_is_the_last_json_object_with_a_file_and_a_function():
    text = 'first {"file": "a.py", "function": "x"} then\n```json\n{"file": "./m.py", "function": " f "}\n```\n'
    assert lfk.extract_diagnosis(text) == {"file": "m.py", "function": "f"}
    assert lfk.extract_diagnosis('{"file": "/w/b/m.py", "function": "f"}', root="/w/b") == {"file": "m.py", "function": "f"}
    for bad in ("no json", '{"file": "a.py"}', '{"file": 1, "function": "f"}', '{"file": "", "function": "f"}', "{broken"):
        with pytest.raises(ValueError):
            lfk.extract_diagnosis(bad)


def test_the_diagnosis_judge_needs_a_truth_file_and_a_truth_function_of_that_file():
    judge = lfk.judge_diagnosis
    assert judge(TRUTH, {"file": MOD, "function": "add"}, None)["correct"] is True
    assert judge(TRUTH, {"file": MOD, "function": "update"}, None)["correct"] is True  # the last component of a method
    assert judge(TRUTH, {"file": MOD, "function": "Tracker.update"}, None)["correct"] is True
    wrong_fn = judge(TRUTH, {"file": MOD, "function": "other"}, None)
    assert wrong_fn["correct"] is False and wrong_fn["file_ok"] is True and wrong_fn["function_ok"] is False
    wrong_file = judge(TRUTH, {"file": "plugins/foundry/tooling/foundry/n.py", "function": "add"}, None)
    assert wrong_file["correct"] is False and wrong_file["file_ok"] is False  # the function of another file
    refused = judge(TRUTH, None, "no final message")
    assert refused["verdict"] == "REFUSED" and refused["correct"] is False and refused["note"] == "no final message"
    with pytest.raises(lfk.CompressionError, match="empty ground truth"):
        judge({**TRUTH, "functions": []}, {"file": MOD, "function": "add"}, None)


# ------------------------------------------------------------------------------------ the decision rule

TASKS = list(range(1, 13))
RULE_C = {"premium_per_correct_ratio_max": 0.85, "paired_decided_min": 9}


def _state(correct, premium=100, decided=True, causes=None):
    return {"decided": decided, "correct": correct if decided else None, "premium": premium, "records": 1,
            "causes": causes or ([] if decided else ["diagnose:contaminated"])}


def _states(f, s):
    return {"F": {pr: f[pr - 1] for pr in TASKS}, "S": {pr: s[pr - 1] for pr in TASKS}}


def _apply(f, s, **kw):
    return lfk.apply_rule(kw.pop("rule", RULE_C), TASKS, _states(f, s), kw.pop("unknown", []), kw.pop("warnings", []),
                          kw.pop("sessions", {}))


def test_every_branch_of_the_decision_rule_in_the_pre_registered_order():
    ok = lambda n=12, prem=100: [_state(True, prem)] * n  # noqa: E731
    # retained: same accuracy, premium per correct diagnosis at 0.85 x exactly (the threshold is inclusive)
    d = _apply(ok(), ok(prem=85))
    assert d["verdict"] == "retained" and d["ratio"] == 0.85 and d["failed_criteria"] == []
    assert d["premium_per_correct"] == {"F": 100.0, "S": 85.0} and d["worst_case"]["robust"] is True
    # just above 0.85: keep the cloud, the economy failed
    d = _apply(ok(), ok(prem=86))
    assert d["verdict"] == "keep_cloud" and d["failed_criteria"] == ["economy"] and d["reason"] == "not_retained_on_paired_set"
    # accuracy: S below F -> keep the cloud, whatever the cost
    d = _apply(ok(), [_state(False, 1)] + ok(11, 1))
    assert d["verdict"] == "keep_cloud" and d["failed_criteria"] == ["accuracy"] and d["correct_on_paired"] == {"F": 12, "S": 11}
    # S more accurate than F counts as at least equal; the ratio is per correct diagnosis
    d = _apply([_state(False)] + ok(11), ok(prem=90))  # 90 / 109.09 = 0.825
    assert d["verdict"] == "retained" and d["premium_per_correct"]["F"] == round(1200 / 11, 3)
    assert _apply([_state(False)] + ok(11), ok(prem=95))["verdict"] == "keep_cloud"  # 95 / 109.09 = 0.871
    # nothing correct with the full output: the ratio is undefined, inconclusive, also when S got some right
    none = [_state(False)] * 12
    d = _apply(none, none)
    assert d["verdict"] == "inconclusive" and d["reason"] == "no_correct_diagnosis_in_the_full_arm_ratio_undefined"
    assert _apply(none, ok())["verdict"] == "inconclusive"
    # a premium total unknown on a task of D: inconclusive, never read as zero (and before the accuracy test)
    d = _apply(ok(), [_state(False, None)] + ok(11, 1))
    assert d["verdict"] == "inconclusive" and d["reason"] == "premium_total_unknown"
    # fewer than 9 tasks decided in both arms: inconclusive, whatever the rest says
    cut = [_state(None, decided=False)] * 4 + ok(8)
    d = _apply(cut, ok(prem=1))
    assert d["verdict"] == "inconclusive" and d["reason"] == "paired_decided_set_below_9" and d["paired_decided_count"] == 8
    d = _apply(ok(), [_state(None, decided=False)] * 3 + ok(9, 1))  # 9 decided: the minimum is reached
    assert d["paired_decided_count"] == 9 and d["verdict"] in ("retained", "inconclusive")
    # an undecided task leaves D and its cost is told apart; retained must also hold in the worst case over all tasks
    d = _apply(ok(), [_state(None, 77, decided=False)] + ok(11, 1))
    assert d["paired_decided_count"] == 11 and d["premium_tokens_on_undecided_tasks"] == {"F": 0, "S": 77}
    assert d["worst_case"] == {"F_undecided_counted_correct": 12, "S_undecided_counted_incorrect": 11, "robust": False}
    assert d["verdict"] == "inconclusive" and d["reason"] == "not_robust_to_undecided_tasks"
    assert d["undecided"]["S"] == {"1": ["diagnose:contaminated"]}
    # campaign-level reasons make any verdict inconclusive, a start with no record included
    d = _apply(ok(), ok(prem=1), unknown=["interrupted:sid-1"], sessions={3: ["sid-1"]})
    assert d["verdict"] == "inconclusive" and d["campaign_level_reasons"] == ["interrupted:sid-1"]
    assert d["verdict_before_campaign_level"] == "retained"
    d = _apply(ok(), ok(prem=1), warnings=["attempt_started_without_settled:x"])
    assert d["verdict"] == "inconclusive" and d["campaign_level_reasons"] == ["start_without_record_nor_replay:attempt_started_without_settled:x"]
    # an interrupted execution of a task OUTSIDE D is task-level, not campaign-level
    cut = [_state(None, 5, decided=False)] + ok(11)
    d = _apply(cut, ok(prem=1), unknown=["interrupted:sid-9"], sessions={1: ["sid-9"]})
    assert d["campaign_level_reasons"] == [] and d["paired_decided_count"] == 11
    # the pilot: the same code on one task with a minimum of 1
    one = lfk.apply_rule({**RULE_C, "paired_decided_min": 1}, [1], {"F": {1: _state(True)}, "S": {1: _state(True, 50)}}, [], [], {})
    assert one["verdict"] == "retained"


# ------------------------------------------------------------------------------------------ the local call

class _Server:
    """A loopback HTTP server that plays the local endpoint: it answers from a script and keeps what it received."""

    def __init__(self):
        outer = self
        self.seen, self.script = [], []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.seen.append({"path": self.path, "body": json.loads(body), "content_type": self.headers["Content-Type"]})
                kind, arg = outer.script.pop(0)
                if kind == "drop":
                    self.connection.close()
                    return
                if kind == "slow":
                    threading.Event().wait(arg)
                    return
                status, payload = 200, b""
                if kind == "ok":
                    payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": arg},
                                                       "finish_reason": "stop"}],
                                          "usage": {"prompt_tokens": 11, "completion_tokens": 5, "note": "x"}}).encode()
                elif kind == "status":
                    status, payload = arg, b'{"error": "nope"}'
                elif kind == "raw":
                    payload = arg
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1/chat/completions"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def server():
    s = _Server()
    yield s
    s.close()


def _call_config(url, **over):
    return {"endpoint": url, "temperature": 0, "max_tokens": 64, "timeout_seconds": 1, "target_fraction": 0.2,
            "system": "SYS", "prompt": "SUMMARISE in {budget}:\n{output}\nEND", **over}


def test_the_one_call_is_non_streaming_without_a_tool_and_returns_the_raw_content(server):
    server.script = [("ok", "  raw {content}\n")]
    out = lfk.call_local(_call_config(server.url), "the/model", "x" * 100 + "{budget}")
    assert out["summary"] == "  raw {content}\n" and out["reason"] is None and out["http_status"] == 200
    assert out["finish_reason"] == "stop" and out["usage"] == {"prompt_tokens": 11, "completion_tokens": 5}
    (seen,) = server.seen
    assert seen["path"] == "/v1/chat/completions" and seen["content_type"] == "application/json"
    body = seen["body"]
    assert set(body) == {"model", "messages", "temperature", "max_tokens", "stream"}  # no tools, no tool_choice
    assert body["stream"] is False and body["temperature"] == 0 and body["max_tokens"] == 64 and body["model"] == "the/model"
    assert [m["role"] for m in body["messages"]] == ["system", "user"] and body["messages"][0]["content"] == "SYS"
    # the budget is a fraction of the bytes; a value that holds braces is not read again
    assert body["messages"][1]["content"] == "SUMMARISE in 21:\n" + "x" * 100 + "{budget}" + "\nEND"


def test_a_failed_summary_is_a_timeout_a_refusal_an_invalid_or_an_empty_answer_and_is_never_retried(server):
    cfg = _call_config(server.url)
    for script, reason, status in ((("slow", 2.5), "timeout", None), (("status", 500), "http_500", 500),
                                   (("status", 400), "http_400", 400), (("raw", b"not json"), "invalid_answer", 200),
                                   (("raw", b'{"choices": []}'), "invalid_answer", 200),
                                   (("raw", b'{"choices": [{"message": {"content": null}}]}'), "invalid_answer", 200),
                                   (("ok", ""), "empty_answer", 200), (("ok", " \n "), "empty_answer", 200),
                                   (("drop", None), "connection_lost", None)):
        server.seen.clear()
        server.script = [script]
        out = lfk.call_local(cfg, "m", "output")
        assert out["summary"] is None and out["reason"] == reason and out["http_status"] == status, (script, out)
        assert len(server.seen) == 1  # one call, no retry
        verdict = lfk.score_summary("output", ["i"], out["summary"])
        assert verdict["verdict"] == "REFUSED" and verdict["recall"] == 0.0


def test_an_endpoint_nobody_listens_on_is_an_instrument_failure_not_a_failed_summary(server):
    url = server.url
    server.close()
    with pytest.raises(lfk.LocalCallUnavailable, match="cannot be reached|refused"):
        lfk.call_local(_call_config(url), "m", "output")


def test_the_endpoint_is_a_literal_loopback_address_and_nothing_else():
    good = ("http://127.0.0.1:1234/v1/chat/completions", "http://127.9.9.9:80/x", "http://[::1]:1234/v1")
    for url in good:
        assert lfk.check_endpoint(url) == url
    for url in ("http://localhost:1234/v1", "https://127.0.0.1:1234/v1", "http://192.168.1.2:1234/v1",
                "http://example.com:1234/v1", "http://127.0.0.1/v1", "http://user:pw@127.0.0.1:1234/v1",
                "http://0.0.0.0:1234/v1", "ftp://127.0.0.1:1234/v1", "", None, 7, "http://127.0.0.1:1234"):
        with pytest.raises(lfk.CompressionError, match="literal loopback"):
            lfk.check_endpoint(url)
    with pytest.raises(lfk.CompressionError, match="literal loopback"):
        lfk.call_local(_call_config("http://10.0.0.1:1/v1"), "m", "o")  # refused before any network use


# ---------------------------------------------------------------------------- the configuration and its pins

def _staged(tmp_path, source=C1):
    data = _load(source)
    for spec in (data["compression"]["ground_truth"], data["compression"]["material"]):
        (tmp_path / spec["file"]).write_bytes((QUALIFICATION / spec["file"]).read_bytes())
    folder = tmp_path / lfk.MATERIAL_DIR
    if not folder.exists():
        folder.mkdir()
        for f in (QUALIFICATION / lfk.MATERIAL_DIR).iterdir():
            (folder / f.name).write_bytes(f.read_bytes())
    return data


def _broken(tmp_path, edit, source=C1):
    data = _staged(tmp_path, source)
    edit(data)
    path = tmp_path / "c.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_the_c1_configs_are_drafts_and_declare_what_the_maintainer_did_not_validate():
    for path in (C1, PILOT):
        data = _load(path)
        assert data["status"].startswith("DRAFT") and data["note"].startswith("DRAFT of PAT-118, phase 1: NOT frozen")
        assert "mandate of 2026-10-08" in data["note"] and "block authorisation of 2026-10-08" in data["note"]
        assert "validated by the maintainer on 2026-10-07" in data["note"] or "Values validated by the maintainer on 2026-10-07" in data["note"]
        text = json.dumps(data, ensure_ascii=False)
        assert NOT_THE_WORD[0] not in text and NOT_THE_WORD[1] not in text
        assert lfr.FROZEN_PROTOCOLS == tuple(f"pat-19-protocol-v{v}" for v in (1, 2, 3, 4, 5))
    assert _load(C1)["protocol"] not in lfr.FROZEN_PROTOCOLS


def test_the_c1_campaign_pins_the_validated_values_and_the_v5_instrument():
    c1, v5, v1 = lfr.load_campaign(C1), _load(QUALIFICATION / "pat-19-campaign-v5.json"), _load(QUALIFICATION / "pat-19-campaign-v1.json")
    assert c1["schema"] == lfr.CAMPAIGN_SCHEMA_V3 == lfk.CAMPAIGN_SCHEMA_V3 and c1["protocol"] == "pat-19-protocol-c1"
    assert c1["compression"]["tasks"] == TWELVE and c1["compression"]["task_set"] == "pat-19-c1"
    assert c1["compression"]["task_set"] not in (lfr.COMPARISON_SET, "pat-19-v5", "screening")
    assert list(c1["candidates"]) == V1_FIVE and all(c1["candidates"][c] == v1["candidates"][c] for c in V1_FIVE)
    assert "devstral-small-2-24b-gguf" not in c1["candidates"] and v1["candidates"]["devstral-small-2-24b-gguf"]["unused"]
    assert c1["rules"]["compression_screening"] == {**c1["rules"]["compression_screening"], "tasks": 12,
                                                    "recall_mean_min": 0.8, "size_ratio_max": 0.25, "candidates": V1_FIVE}
    cmp_ = c1["rules"]["compression_comparison"]
    assert (cmp_["tasks"], cmp_["premium_per_correct_ratio_max"], cmp_["paired_decided_min"], cmp_["cloud_executions_max"]) == (
        12, 0.85, 9, 36)
    iso = c1["isolation"]
    assert iso["private_attempt_root"] is True and iso[lfr.NATIVE_SANDBOX_KEY] is True and iso["audit_revision"] == 2
    assert iso[lfr.AUDIT_POLICY_KEY] == lfr.AUDIT_POLICY and iso[lfr.ABSENT_FORMS_KEY] == lfr.ABSENT_FORMS
    assert iso["allow_read_home"] == [".config/git/ignore"]
    for key in ("frozen_machine", "dedicated_machine", "cloud_bash_deny", "server_process_pattern"):
        assert {k: v for k, v in c1[key].items() if k != "note"} == {k: v for k, v in v5[key].items() if k != "note"} \
            if isinstance(c1[key], dict) else c1[key] == v5[key]
    drv = c1["drivers"]["cloud_diagnoser"]
    assert list(c1["drivers"]) == ["cloud_diagnoser"] and drv["kind"] == "cloud_explorer" and drv["model"] == "claude-sonnet-5-5"
    argv = drv["argv"]
    assert argv[argv.index("--effort") + 1] == "medium" == v5["drivers"]["cloud_implementer_current"]["argv"][
        v5["drivers"]["cloud_implementer_current"]["argv"].index("--effort") + 1]
    denied = argv[argv.index("--disallowedTools") + 1:]
    assert {"Edit", "Write"} <= set(denied) and set(v5["drivers"]["cloud_implementer_current"]["argv"][
        v5["drivers"]["cloud_implementer_current"]["argv"].index("--disallowedTools") + 1:]) <= set(denied)
    assert drv["binary_version"]["version"] == "2.1.294" and drv["binary_version"]["command"] == ["claude", "--version"]
    assert drv["allowed_tools"] == ["Bash", "Glob", "Grep", "Read"] and drv["sandbox"] is False
    lfr._check_driver_usable("cloud_diagnoser", drv, dry_run=False)  # a real run accepts it
    call = c1["compression"]["local_call"]
    assert call["temperature"] == 0 and set(call) == {"endpoint", "temperature", "max_tokens", "timeout_seconds",
                                                      "target_fraction", "system", "prompt", "note"}
    assert call["endpoint"].startswith("http://127.0.0.1:") and "tools" not in json.dumps(call).replace("no tools", "")
    caps = c1["envelope_recommended"]["compression"]["caps"]
    assert caps["cloud_executions"] == cmp_["cloud_executions_max"] == 36 and 36 + 4 == lfk.C1_CLOUD_EXECUTIONS_MAX
    assert c1["compression"]["ground_truth"] == v5["exploration"]["ground_truth"]
    assert c1["compression"]["material"]["sha256"] == hashlib.sha256(MATERIAL.read_bytes()).hexdigest()


def test_the_pilot_is_the_same_instrument_on_one_task_under_names_of_its_own():
    c1, pilot = _load(C1), lfr.load_campaign(PILOT)
    assert pilot["protocol"] == "pat-19-protocol-c1-pilot" != c1["protocol"] and "PILOT" in pilot["note"]
    assert pilot["compression"]["tasks"] == [27] and pilot["compression"]["task_set"] == "pat-19-c1-pilot"
    assert pilot["rules"]["compression_comparison"]["cloud_executions_max"] == 4
    assert pilot["envelope_recommended"]["compression"]["caps"]["cloud_executions"] == 4

    def bare(config):  # everything but the names, the notes, the task list and the pilot's own shares
        out = json.loads(json.dumps(config))
        for k in ("protocol", "note", "envelope_recommended"):
            out.pop(k)
        out["compression"].pop("tasks")
        out["compression"].pop("task_set")
        for name in ("compression_screening", "compression_comparison"):
            for k in ("tasks", "note", "paired_decided_min", "cloud_executions_max"):
                out["rules"][name].pop(k, None)
        return out
    assert bare(pilot) == bare(c1)
    assert lfk.C1_ENVELOPE_SHARE == {"pat-19-protocol-c1": 36, "pat-19-protocol-c1-pilot": 4}


def test_the_loader_refuses_a_c1_config_that_drifts_from_its_pins(tmp_path):
    def sub(key, **over):
        return lambda d: d["compression"][key].update(over)

    def call(**over):
        return lambda d: d["compression"]["local_call"].update(over)

    def drop_call(key):
        return lambda d: d["compression"]["local_call"].pop(key)

    def argv_edit(fn):
        return lambda d: fn(d["drivers"]["cloud_diagnoser"]["argv"])

    cases = (
        (lambda d: d["compression"].update(tasks=TWELVE[::-1]), "compression.tasks"),
        (lambda d: d["compression"].update(task_set="comparison"), "compression.task_set"),
        (lambda d: d["compression"].pop("output_kinds"), "output_kinds"),
        (lambda d: d["compression"]["output_kinds"].pop("S"), "output_kinds"),
        (lambda d: d.update(candidates={k: v for k, v in d["candidates"].items() if k != V1_FIVE[0]}), "five candidates"),
        (lambda d: d["rules"]["compression_screening"].update(recall_mean_min=0.7), "recall_mean_min 0.8"),
        (lambda d: d["rules"]["compression_screening"].update(size_ratio_max=0.3), "size_ratio_max 0.25"),
        (lambda d: d["rules"]["compression_screening"].update(candidates=V1_FIVE[::-1]), "five candidates"),
        (lambda d: d["rules"]["compression_comparison"].update(premium_per_correct_ratio_max=0.9), "0.85"),
        (lambda d: d["rules"]["compression_comparison"].update(paired_decided_min=8), "paired_decided_min 9"),
        (lambda d: d["rules"]["compression_comparison"].update(cloud_executions_max=40), "cloud_executions_max 36"),
        (lambda d: d["rules"].pop("compression_comparison"), "compression_screening and rules.compression_comparison"),
        (call(temperature=0.7), "temperature 0"), (call(tools=[]), "nothing else"), (call(stream=True), "nothing else"),
        (drop_call("max_tokens"), "positive integer max_tokens"), (call(max_tokens=0), "positive integer max_tokens"),
        (call(timeout_seconds=-1), "timeout_seconds"), (call(target_fraction=0.5), "target_fraction"),
        (call(target_fraction=0), "target_fraction"), (call(prompt="no placeholders"), "{output}"),
        (call(prompt="{output} only"), "{budget}"), (call(system=" "), "system"),
        (call(endpoint="http://example.com:1234/v1"), "literal loopback"),
        (call(endpoint="https://127.0.0.1:1234/v1"), "literal loopback"),
        (sub("ground_truth", sha256="0" * 64), "sha256"), (sub("material", sha256="0" * 64), "material index"),
        (lambda d: d["compression"].pop("material"), "compression.material"),
        (lambda d: d["isolation"].pop("audit_policy"), "requires isolation.private_attempt_root"),
        (lambda d: d["isolation"].update(cloud_native_sandbox=False), "isolation.cloud_native_sandbox"),
        (lambda d: d["isolation"].pop("audit_absent_path_forms"), "audit_absent_path_forms"),
        (lambda d: d["isolation"].update(allow_read_home=[".ssh"]), "allows reading nothing of the home"),
        (lambda d: d["drivers"].update(extra=d["drivers"]["cloud_diagnoser"]), "one cloud driver"),
        (argv_edit(lambda a: a.remove("Write")), "Edit and Write denied"),
        (argv_edit(lambda a: a.remove("Edit")), "Edit and Write denied"),
        (argv_edit(lambda a: a.__setitem__(a.index("medium"), "high")), "effort medium"),
        (lambda d: d["drivers"]["cloud_diagnoser"].update(model="claude-opus-5-5"), "claude-sonnet-5-5"),
        (lambda d: d["drivers"]["cloud_diagnoser"].update(allowed_tools=["Bash", "Edit", "Read"]), "tools Bash, Glob, Grep and Read"),
        (lambda d: d["drivers"]["cloud_diagnoser"]["binary_version"].update(version="2.1.285"), "pins Claude Code 2.1.294"),
        (lambda d: d["drivers"]["cloud_diagnoser"].pop("binary_version"), "pins Claude Code 2.1.294"),
        (lambda d: d["prompts"].update(diagnose="{statement_file} {output_kind}"), "{test_output}"),
        (lambda d: d["prompts"].pop("diagnose"), "prompts.diagnose"),
        (lambda d: d["bounds"].pop("cloud_max_seconds"), "cloud_max_seconds"),
        (lambda d: d.pop("dedicated_machine"), "dedicated_machine"),
        (lambda d: d["cloud_bash_deny"].append("Bash(rm:*)"), "cloud_bash_deny rule"),
        (lambda d: d.update(protocol="pat-19-protocol-v5"), "schema of protocol pat-19-protocol-c1"),
        (lambda d: d.update(schema=lfr.CAMPAIGN_SCHEMA_V2), "schema"),
    )
    for edit, message in cases:
        with pytest.raises(lfr.RunnerError, match=re.escape(message) if "{" in message else message):
            lfr.load_campaign(_broken(tmp_path, edit))
    lfr.load_campaign(_broken(tmp_path, lambda d: None))  # the untouched copy loads
    # the pilot task must be in the material: an index without PR 27 is refused (re-hashed, so the sha256 is not the cause)
    other = tmp_path / "other"
    other.mkdir()
    data = _staged(other)
    index = _load(MATERIAL)
    index["tasks"].pop("27")
    (other / MATERIAL.name).write_text(json.dumps(index), encoding="utf-8")
    data["compression"]["material"]["sha256"] = hashlib.sha256((other / MATERIAL.name).read_bytes()).hexdigest()
    (other / "c.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(lfr.RunnerError, match="no ground truth or no material"):
        lfr.load_campaign(other / "c.json")


def test_the_pilot_loader_pins_its_own_task_and_shares(tmp_path):
    for edit, message in ((lambda d: d["compression"].update(tasks=[27, 26]), "compression.tasks"),
                          (lambda d: d["compression"].update(task_set="pat-19-c1"), "compression.task_set"),
                          (lambda d: d["rules"]["compression_comparison"].update(cloud_executions_max=36), "cloud_executions_max 4"),
                          (lambda d: d["rules"]["compression_comparison"].update(paired_decided_min=9), "paired_decided_min 1")):
        with pytest.raises(lfr.RunnerError, match=message):
            lfr.load_campaign(_broken(tmp_path, edit, PILOT))


def test_nothing_the_c1_keys_add_is_accepted_by_the_frozen_protocols(tmp_path):
    from test_local_first_exploration_v5 import FROZEN_SHA256
    for name, digest in FROZEN_SHA256.items():  # the frozen files are untouched (their own tests also pin them)
        assert hashlib.sha256((QUALIFICATION / name).read_bytes()).hexdigest() == digest, name
    for version in (1, 2, 3, 4, 5):
        source = QUALIFICATION / f"pat-19-campaign-v{version}.json"
        data = _load(source)
        spec = (data.get("exploration") or {}).get("ground_truth")
        if spec:
            (tmp_path / spec["file"]).write_bytes((QUALIFICATION / spec["file"]).read_bytes())
        assert lfr.load_campaign(source)  # as they load today
        data["compression"] = _load(C1)["compression"]
        path = tmp_path / f"v{version}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(lfr.RunnerError, match="compression key is accepted only by the schema"):
            lfr.load_campaign(path)
    v5 = _load(QUALIFICATION / "pat-19-campaign-v5.json")
    (tmp_path / v5["exploration"]["ground_truth"]["file"]).write_bytes(
        (QUALIFICATION / v5["exploration"]["ground_truth"]["file"]).read_bytes())
    for edit, message in ((lambda d: d.update(schema=lfr.CAMPAIGN_SCHEMA_V3), "schema of protocol pat-19-protocol-c1"),
                          (lambda d: d.update(protocol="pat-19-protocol-c1"), "is a schema .* protocol")):
        broken = json.loads(json.dumps(v5))
        edit(broken)
        (tmp_path / "b.json").write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(lfr.RunnerError, match=message):
            lfr.load_campaign(tmp_path / "b.json")
    # the c1 protocol names carry the v5 instrument keys, no other name does without being after v4
    assert lfr._PROTOCOL_AFTER_V4.fullmatch("pat-19-protocol-c1") and lfr._PROTOCOL_AFTER_V4.fullmatch("pat-19-protocol-c1-pilot")
    assert not lfr._PROTOCOL_AFTER_V4.fullmatch("pat-19-protocol-c10") and not lfr._PROTOCOL_AFTER_V4.fullmatch("pat-19-protocol-c2")
    for mode in lfr.COMPRESSION_MODES:  # a frozen schema never runs a compression mode, nor c1 a frozen mode
        for schema in (lfr.CAMPAIGN_SCHEMA, lfr.CAMPAIGN_SCHEMA_V2):
            with pytest.raises(lfr.RunnerError, match="does not match the campaign schema"):
                lfr.Runner(repo=tmp_path, campaign={"schema": schema}, envelope={"sha256": "x"}, state_dir=tmp_path,
                           work_root=tmp_path / "w", mode=mode, dry_run=True, sandbox=False)
    for mode in ("screen", "compare", *lfr.EXPLORE_MODES):
        with pytest.raises(lfr.RunnerError, match="does not match the campaign schema"):
            lfr.Runner(repo=tmp_path, campaign={"schema": lfr.CAMPAIGN_SCHEMA_V3}, envelope={"sha256": "x"},
                       state_dir=tmp_path, work_root=tmp_path / "w", mode=mode, dry_run=True, sandbox=False)


# ------------------------------------------------------------------------------------------ the runner

FAKE_DIAGNOSER = r'''
import argparse, json, os, subprocess, sys, time
ap = argparse.ArgumentParser()
ap.add_argument("--plan"); ap.add_argument("--workdir"); ap.add_argument("--session-id", default="")
ap.add_argument("--projects-dir", default=""); ap.add_argument("--init-tools", default="")
ap.add_argument("--init-version", default=None); ap.add_argument("--prompt", default="")
ap.add_argument("--settings", default=None); ap.add_argument("--permission-mode", default=None)
ap.add_argument("--disallowedTools", nargs="*", default=[]); ap.add_argument("--model", default="")
ap.add_argument("--effort", default="")
a, _ = ap.parse_known_args()
counter = a.plan + ".count"
n = json.load(open(counter)) if os.path.exists(counter) else 0
json.dump(n + 1, open(counter, "w"))
steps = json.load(open(a.plan))
step = steps[min(n, len(steps) - 1)]
with open(a.plan + ".calls", "a") as handle:  # what the launcher gave this arm
    handle.write(json.dumps({"prompt": a.prompt, "settings": json.loads(a.settings) if a.settings else None,
                             "mode": a.permission_mode, "disallowed": a.disallowedTools, "model": a.model,
                             "effort": a.effort, "env": sorted(os.environ), "cwd": os.getcwd(),
                             "git_global": os.environ.get("GIT_CONFIG_GLOBAL"),
                             "statement": open(os.path.join(a.workdir, "TASK.md")).read()}) + "\n")
def event(kind, **kw):
    print(json.dumps({"type": kind, **kw}), flush=True)
event("system", subtype="init", tools=[t for t in a.init_tools.split(",") if t], permissionMode=a.permission_mode,
      **({"claude_code_version": a.init_version} if a.init_version else {}))
if step.get("peek"):
    event("assistant", message={"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": step["peek"]}}]})
if step.get("write"):
    open(os.path.join(a.workdir, step["write"]), "w").write("x")
if step.get("crash"):
    sys.exit(3)
folder = os.path.join(a.projects_dir, "proj")
os.makedirs(folder, exist_ok=True)
usage = step.get("usage", {"input_tokens": 100, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5,
                           "output_tokens": 20})
line = {"type": "assistant", "timestamp": "2026-10-05T10:00:00Z", "sessionId": a.session_id,
        "message": {"model": "claude-sonnet-5-5", "id": "m1", "stop_reason": "end_turn", "usage": usage}, "requestId": "r1"}
if not step.get("no_log"):
    with open(os.path.join(folder, a.session_id + ".jsonl"), "w") as out:
        out.write(json.dumps(line) + "\n")
if step.get("hang"):  # a bound cuts the arm after the host wrote its usage
    time.sleep(60)
text = step.get("text")
if text is None:
    text = json.dumps(step["answer"])
event("result", subtype="success", is_error=False, result=text)
'''
GOOD = {"answer": ANSWER}
WRONG_FILE = {"answer": {"file": "plugins/foundry/tooling/foundry/elsewhere.py", "function": "add"}}
WRONG_FN = {"answer": {"file": MOD, "function": "subtract"}}
GARBAGE = {"text": "I could not find it."}
SUMMARY = "tests/test_m.py::test_added and tests/test_m.py::test_changed fail in m.add"  # cites both ids (shorter text)
CALL_OK = {"summary": SUMMARY, "reason": None, "http_status": 200, "finish_reason": "stop", "usage": {"prompt_tokens": 9}}
CALL_FAIL = {"summary": None, "reason": "timeout", "http_status": None, "finish_reason": None, "usage": None}


def _material_text(ids=("tests/test_m.py::test_added", "tests/test_m.py::test_changed")):
    body = "F.F\n" + "".join(f"E   a long line of the traceback number {n} of the failing test\n" for n in range(30))
    return (body + "=========================== short test summary info ============================\n"
            + "".join(f"FAILED {i} - assert 1 == 2\n" for i in ids) + "2 failed, 1 passed in 0.12s\n")


def c1_campaign(tmp_path, *, diag=None, pilot=False, over=None):
    """The committed config with the five candidates replaced by two fakes and the diagnoser by a stand-in script."""
    campaign = _load(PILOT if pilot else C1)
    campaign["candidates"] = {"cand-a": {"model": "fake/model-a"}, "cand-b": {"model": "fake/model-b"}}
    campaign["compression"].update(tasks=[1], task_set="pat-19-c1-pilot" if pilot else "pat-19-c1")
    campaign["rules"]["compression_screening"].update(tasks=1, candidates=["cand-a", "cand-b"])
    campaign["rules"]["compression_comparison"].update(tasks=1, paired_decided_min=1)
    campaign["bounds"]["cloud_max_seconds"] = 20
    script = tmp_path / "fake_diagnoser.py"
    script.write_text(FAKE_DIAGNOSER, encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(diag or [GOOD]), encoding="utf-8")
    projects = tmp_path / "claude-projects"
    campaign["drivers"] = {"cloud_diagnoser": {
        "kind": "cloud_explorer", "fake": True, "verified": False, "home": "real", "network": "open", "env_allow": [],
        "stream": {"format": "claude-stream-json"}, "sandbox": False, "sandbox_reason": "test", "model": "claude-sonnet-5-5",
        "allowed_tools": ["Bash", "Glob", "Grep", "Read"],
        "session_log": {"host": "claude", "projects_dir": str(projects), "layout_verified": True},
        "argv": [sys.executable, str(script), "--plan", str(plan), "--workdir", "{workdir}", "--session-id", "{session_id}",
                 "--projects-dir", str(projects), "--init-tools", "Bash,Read", "--init-version", "2.1.294",
                 "--model", "{model}", "--effort", "medium", "--prompt", "{prompt}", "--disallowedTools", "Edit", "Write"]}}
    for key, value in (over or {}).items():
        campaign.setdefault(key, {}).update(value)
    return campaign, plan


def _one_material():
    text = _material_text()
    return {1: {"text": text, "bytes": len(text.encode()), "lines": len(text.splitlines()),
                "sha256": lfk.sha256_text(text), "failing_ids": lfk.parse_failing_ids(text)}}


def make_c1(tmp_path, mode, *, diag=None, calls=None, caps=None, pilot=False, campaign_id=None, repo_bundle=None,
            over=None, material=None, host_env=None, now=None):
    """A protocol c1 runner on a throwaway repository: the committed config with the five candidates replaced by two
    fakes, the diagnoser by a stand-in script, the local call by a scripted callable, the material by one synthetic
    output. ``calls`` is the list of results the local call returns, in order; ``diag`` the steps of the diagnoser."""
    repo, snap, _, _ = repo_bundle or _make_repo(tmp_path)
    campaign, plan = c1_campaign(tmp_path, diag=diag, pilot=pilot, over=over)
    material = material or _one_material()
    envelope_path = tmp_path / "envelope.json"
    write_envelope(tmp_path, campaign_id or ("test-c1-pilot" if pilot else "test-c1"),
                   **{"cloud_executions": 4 if pilot else 36, **(caps or {})})
    body = json.loads(envelope_path.read_text("utf-8"))
    body["allowed_modes"] = list(lfr.COMPRESSION_MODES)
    envelope_path.write_text(json.dumps(body), encoding="utf-8")
    script_calls = list(calls if calls is not None else [CALL_OK])
    given: list[dict] = []

    def local_call(call, model, output):
        given.append({"model": model, "output": output, "call": call})
        return dict(script_calls.pop(0))
    (tmp_path / "shared-tmp").mkdir(exist_ok=True)  # never the user's real temp directory
    env = host_env or {**os.environ, "HOME": str(tmp_path / "home"), "TMPDIR": str(tmp_path / "shared-tmp")}
    runner = lfr.Runner(
        repo=repo, campaign=campaign, envelope=lfr.load_envelope(envelope_path, mode, TODAY),
        state_dir=tmp_path / "state", work_root=tmp_path / "work", mode=mode, dry_run=True, sandbox=False,
        run=lambda argv: SWAP if tuple(argv) == ("sysctl", "-n", "vm.swapusage") else None,
        preflight_run=lambda cid: facts_with(campaign, campaign["candidates"][cid]["model"]),
        disk_free_gib=lambda: 500.0, host_env=env, today=lambda: TODAY, material=material, local_call=local_call,
        **({"now": now} if now else {}))
    runner.quiescent_wait = 0.05
    runner.test_calls = given
    task = snap["prs"][0]
    return runner, campaign, plan, [task]


def _calls(plan):
    path = Path(str(plan) + ".calls")
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()] if path.exists() else []


def test_a_screening_makes_one_call_per_candidate_and_task_and_never_a_cloud_execution(tmp_path):
    runner, campaign, plan, tasks = make_c1(tmp_path, "screen_compression", calls=[CALL_OK, CALL_FAIL],
                                            caps={"cloud_executions": 0})
    spent = []
    runner.cloud_execution = lambda *a, **k: spent.append(a)
    out = runner.screen_compression(tasks, ["cand-a", "cand-b"])
    assert not spent and not _calls(plan) and lfr.Ledger.totals(runner.ledger)["cloud_started"] == 0
    assert [r["local"]["candidate"] for r in out] == ["cand-a", "cand-b"]
    ok, failed = out
    assert ok["path"] == "CS" and ok["segment"] == "local" and ok["task"] == {"pr": 1, "issue": "PAT-1", "set": "pat-19-c1"}
    assert ok["judge"]["verdict"] == "SCORED" and ok["judge"]["recall"] == 1.0 and ok["judge"]["size_ok"] is True
    assert ok["compression"]["summary"] == SUMMARY and ok["compression"]["summary_sha256"] == lfk.sha256_text(SUMMARY)
    assert ok["cloud_executions"] == 0 and ok["premium"]["billing_total"] == 0 and ok["accepted"] is None
    assert ok["local"]["endpoint"].startswith("http://127.0.0.1:") and ok["local"]["http_status"] == 200
    assert ok["machine"]["dedicated"]["free_percent"] == 80  # the dedicated-machine admission ran before the call
    assert failed["judge"]["verdict"] == "REFUSED" and failed["judge"]["recall"] == 0.0 and failed["compression"]["summary"] is None
    assert failed["local"]["failure"] == "timeout" and "failed summary: timeout" in failed["unknown"]["compression.summary"]
    assert [g["model"] for g in runner.test_calls] == ["fake/model-a", "fake/model-b"]
    assert all(g["output"] == runner.material[1]["text"] for g in runner.test_calls)  # the committed output, as it is
    kinds = [e["kind"] for e in ledger_of(runner)]
    assert kinds.count("preflight") == 2 and kinds.count("attempt_started") == 2 and "cloud_started" not in kinds
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    table = rep["compression_screening"]
    assert rep["promotion"] is False and "exploration_screening" not in rep and "screening" not in rep
    assert table["candidates"]["cand-a"]["passes"] is True and table["candidates"]["cand-b"]["passes"] is False
    assert table["selected"] == "cand-a" and table["reason"] == "best_mean_recall" and table["complete"] is True
    assert table["candidates"]["cand-b"]["failed_summaries"] == [1]
    assert rep["compression_sizes"]["output_bytes_by_task"] == {"1": runner.material[1]["bytes"]}


def test_a_screening_resumes_without_replaying_a_decided_call_and_never_retries_a_failed_summary(tmp_path):
    runner, _, plan, tasks = make_c1(tmp_path, "screen_compression", calls=[CALL_FAIL])
    runner.screen_compression(tasks, ["cand-a"])
    second, _, _, _ = make_c1(tmp_path, "screen_compression", calls=[CALL_OK], repo_bundle=(runner.repo, {"prs": tasks}, None, None))
    out = second.screen_compression(tasks, ["cand-a", "cand-b"])
    assert [r["local"]["candidate"] for r in out] == ["cand-b"]  # cand-a is decided (a failed summary is a verdict)
    assert len(results(second)) == 2 and second.test_calls[0]["model"] == "fake/model-b"


def test_an_unreachable_endpoint_is_a_void_attempt_replayed_once_never_a_failed_summary(tmp_path):
    def unreachable(*_a, **_k):
        raise lfk.LocalCallUnavailable("the local endpoint refused the connection")
    runner, campaign, _, tasks = make_c1(tmp_path, "screen_compression")
    runner.local_call = unreachable
    with pytest.raises(lfk.LocalCallUnavailable):
        runner.screen_compression(tasks, ["cand-a"])
    (void, stop) = results(runner)
    assert void["outcome"] == "tool_error" and not void.get("judge") and void["local"]["candidate"] == "cand-a"
    assert stop["record_type"] == "stop"
    again, _, _, _ = make_c1(tmp_path, "screen_compression", calls=[CALL_OK], repo_bundle=(runner.repo, {"prs": tasks}, None, None))
    out = again.screen_compression(tasks, ["cand-a"])  # replayed once, with its reference
    assert out[0]["replay_of"]["outcome"] == "tool_error" and out[0]["judge"]["verdict"] == "SCORED"
    rep = lfr.report(campaign, results(again), ledger_of(again))
    assert rep["compression_screening"]["candidates"]["cand-a"]["complete"] is True
    assert rep["replays"][0]["outcome"] == "scored" or rep["replays"][0]["replay_of"]["outcome"] == "tool_error"


def test_a_local_call_the_envelope_would_cut_below_its_bound_is_not_started(tmp_path):
    runner, _, _, tasks = make_c1(tmp_path, "screen_compression", caps={"wall_clock_seconds": 599})  # the bound is 600 s
    assert runner.screen_compression(tasks, ["cand-a"]) == [] and runner.stopped == "cap_reached:wall_clock_seconds"
    assert not runner.test_calls and "attempt_started" not in [e["kind"] for e in ledger_of(runner)]


def test_the_screening_stops_on_keep_the_cloud_and_the_comparison_is_then_refused(tmp_path):
    runner, campaign, plan, tasks = make_c1(tmp_path, "screen_compression", calls=[CALL_FAIL, CALL_FAIL])
    runner.screen_compression(tasks, ["cand-a", "cand-b"])
    table = lfr.report(campaign, results(runner), ledger_of(runner))["compression_screening"]
    assert table["selected"] is None and table["stop"] == "keep_cloud" and table["reason"] == "no_candidate_passes: keep_cloud"
    comparison, _, _, _ = make_c1(tmp_path, "compare_compression", repo_bundle=(runner.repo, {"prs": tasks}, None, None))
    with pytest.raises(lfr.RunnerError, match="not the one the screening selected"):
        comparison.compare_compression(tasks, "cand-a")
    assert not _calls(plan) and "cloud_started" not in [e["kind"] for e in ledger_of(comparison)]
    rep = lfr.report(campaign, results(comparison), ledger_of(comparison))["compression_comparison"]
    assert rep["decision"] == "inconclusive" and rep["campaign_conclusion"] == "keep_cloud_screening_stop"
    # arm records after a screening that stopped on keep_cloud are a contradiction: the report refuses them
    forged = [*results(runner), {**results(runner)[0], "path": "F", "segment": "diagnose", "attempt": 0,
                                 "diagnosis": {"compared_candidate": "cand-a"}}]
    with pytest.raises(lfk.CompressionError, match="although the screening stopped on keep_cloud"):
        lfr.report(campaign, forged, ledger_of(runner))


def _screened(tmp_path, **kw):
    """A campaign whose screening selected ``cand-a`` (one task), and the runner of its comparison."""
    first, campaign, plan, tasks = make_c1(tmp_path, "screen_compression", calls=[CALL_OK, CALL_FAIL], over=kw.get("over"),
                                            caps=kw.get("caps"))
    first.screen_compression(tasks, ["cand-a", "cand-b"])
    runner, campaign, plan, tasks = make_c1(tmp_path, "compare_compression",
                                            repo_bundle=(first.repo, {"prs": tasks}, None, None), **kw)
    return runner, campaign, plan, tasks


def test_the_comparison_plays_both_arms_read_only_with_the_same_prompt_and_judges_the_answers(tmp_path):
    runner, campaign, plan, tasks = _screened(tmp_path, diag=[GOOD, GOOD])
    out = runner.compare_compression(tasks, "cand-a")
    assert [r["path"] for r in out] == ["F", "S"] and all(r["segment"] == "diagnose" for r in out)
    f, s = out
    for rec in out:
        assert rec["outcome"] == "diagnosed" and rec["judge"]["correct"] is True and rec["cloud_executions"] == 1
        assert rec["premium"]["billing_total"] == 135 and rec["premium"]["by_role"]["diagnoser"] is not None
        assert rec["audit"]["barrier"] == lfr.BARRIER_OBSERVED and rec["diagnosis"]["bundle_modified"] is False
        assert rec["diagnosis"]["answer"] == ANSWER and rec["accepted"] is None and rec["review"] == {"rounds": 0, "verdicts": []}
    assert f["diagnosis"]["output_kind"] == "full" and s["diagnosis"]["output_kind"] == "summary"
    assert s["diagnosis"]["summary_of"]["candidate"] == "cand-a"
    assert s["diagnosis"]["output_sha256"] == lfk.sha256_text(SUMMARY) == s["diagnosis"]["summary_of"]["summary_sha256"]
    assert f["diagnosis"]["output_sha256"] == runner.material[1]["sha256"]
    given = _calls(plan)
    assert len(given) == 2
    kinds = campaign["compression"]["output_kinds"]
    assert runner.material[1]["text"] in given[0]["prompt"] and SUMMARY in given[1]["prompt"]
    assert SUMMARY not in given[0]["prompt"] and runner.material[1]["text"] not in given[1]["prompt"]
    # the two prompts differ by what was sent and how it is introduced, and by nothing else
    plain = [re.sub(r"\S*attempt-l\d+-\d+-pr1-[FS]-diagnose\S*", "<statement>",
                    g["prompt"].replace(runner.material[1]["text"], "<T>").replace(SUMMARY, "<T>")
                    .replace(kinds["F"], "<K>").replace(kinds["S"], "<K>")) for g in given]
    assert plain[0] == plain[1] and "<statement>" in plain[0]
    assert given[0]["statement"] == given[1]["statement"]  # the same statement and footer, identical for the arms
    assert campaign["statement_footer"] in given[0]["statement"]
    for g in given:  # the instrument of v5: dontAsk, settings, a fresh git environment, the R6 environment untouched
        assert g["mode"] == "dontAsk" and g["settings"]["sandbox"]["enabled"] is True and g["git_global"] == os.devnull
        assert {"HOME", "LANG", "PATH", "TMPDIR", "USER", "FOUNDRY_DATA"} <= set(g["env"]) | {"LANG"} and "HOME" in g["env"]
        assert "Edit" in g["disallowed"] and "Write" in g["disallowed"] and g["model"] == "claude-sonnet-5-5"
        fs = g["settings"]["sandbox"]["filesystem"]
        bundle = fs["allowWrite"][0] + "/bundle"
        assert bundle in fs["denyWrite"] and f"Edit(//{bundle.lstrip('/')}/**)" in g["settings"]["permissions"]["deny"]
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    cmp_ = rep["compression_comparison"]
    assert cmp_["complete"] is True and cmp_["decision"] == "keep_cloud" and cmp_["recommendation"] == "F"
    assert cmp_["summary_of_candidate"] == "cand-a" and cmp_["arms"]["F"]["correct"] == cmp_["arms"]["S"]["correct"] == 1
    assert cmp_["paired_rule"]["premium_on_paired"] == {"F": 135, "S": 135}
    assert cmp_["paired_rule"]["verdict"] == "keep_cloud" and cmp_["campaign_conclusion"] == "keep_cloud"  # 1.0 > 0.85
    assert rep["promotion"] is False and not rep["ledger"]["unknown_spent_work"]


def _records(runner):
    return {(r["path"], r["segment"]): r for r in results(runner) if r.get("record_type") == "attempt"}


def test_an_unreadable_or_wrong_answer_is_an_incorrect_diagnosis_with_its_cost_counted(tmp_path):
    cases = ((WRONG_FILE, False, "SCORED"), (WRONG_FN, False, "SCORED"), (GARBAGE, False, "REFUSED"),
             ({"text": '{"file": "a.py"}'}, False, "REFUSED"), (GOOD, True, "SCORED"))
    for index, (step, correct, verdict) in enumerate(cases):
        folder = tmp_path / f"case{index}"
        folder.mkdir()
        runner, campaign, plan, tasks = _screened(folder, diag=[step, step])
        out = runner.compare_compression(tasks, "cand-a")
        assert [r["judge"]["correct"] for r in out] == [correct, correct] and out[0]["judge"]["verdict"] == verdict
        assert [r["premium"]["billing_total"] for r in out] == [135, 135]  # the cost is counted either way
        assert out[0]["outcome"] == "diagnosed" and out[0]["cloud_executions"] == 1
        if verdict == "REFUSED":
            assert out[0]["unknown"]["diagnosis"] and out[0]["diagnosis"]["answer"] is None
        rep = lfr.report(campaign, results(runner), ledger_of(runner))["compression_comparison"]
        assert rep["arms"]["F"]["decided"] == rep["arms"]["S"]["decided"] == 1  # decided: never undecided for being wrong


def test_a_crashed_arm_has_an_unknown_cost_never_zero_and_stops_the_later_executions(tmp_path):
    runner, campaign, _, tasks = _screened(tmp_path, diag=[{"crash": True, "no_log": True}, GOOD])
    (f,) = runner.compare_compression(tasks, "cand-a")
    assert f["judge"]["correct"] is False and f["judge"]["verdict"] == "REFUSED"
    assert f["premium"]["billing_total"] is None and "premium.diagnoser" in f["unknown"]  # unknown, not 0
    assert runner.stopped == "cap_reached:premium_tokens_unmeasurable"  # no later execution runs on an unknown spend
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    assert any(e.startswith("tokens_unknown:") for e in rep["ledger"]["unknown_spent_work"])
    cmp_ = rep["compression_comparison"]
    assert cmp_["decision"] == "inconclusive" and cmp_["arms"]["F"]["tasks"]["1"]["premium"] is None
    assert cmp_["arms"]["S"]["tasks"]["1"]["causes"] == ["diagnose:stopped_by_cap"]
    # the same crash on a task of D (both arms decided) is read as an unknown premium, never as zero
    states = {"F": {1: _state(True, None)}, "S": {1: _state(True, 10)}}
    detail = lfk.apply_rule({**RULE_C, "paired_decided_min": 1}, [1], states, ["tokens_unknown:sid"], [], {1: ["sid"]})
    assert detail["reason_before_campaign_level"] == "premium_total_unknown" and detail["verdict"] == "inconclusive"
    assert detail["campaign_level_reasons"] == ["tokens_unknown:sid"]


def test_a_flagged_arm_is_undecided_and_a_modified_bundle_or_a_timeout_refuses_the_diagnosis(tmp_path):
    runner, campaign, plan, tasks = _screened(tmp_path, diag=[{"peek": str(tmp_path / "home" / ".ssh" / "id"), **GOOD}, GOOD])
    f, s = runner.compare_compression(tasks, "cand-a")
    assert f["outcome"] == "contaminated" and f["judge"] is None and f["contaminated"] is True
    assert f["diagnosis"]["answer"] is None and s["outcome"] == "diagnosed"
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    assert rep["contaminated"][0]["path"] == "F"
    cmp_ = rep["compression_comparison"]
    assert cmp_["arms"]["F"]["tasks"]["1"]["decided"] is False and cmp_["arms"]["F"]["tasks"]["1"]["causes"] == ["diagnose:contaminated"]
    assert cmp_["decision"] == "inconclusive" and cmp_["paired_rule"]["reason"] == "paired_decided_set_below_1"
    other = tmp_path / "w"
    other.mkdir()
    runner, _, _, tasks = _screened(other, diag=[{"write": "stray.txt", **GOOD}, GOOD])
    f, _ = runner.compare_compression(tasks, "cand-a")
    assert f["diagnosis"]["bundle_modified"] is True and f["judge"]["correct"] is False and f["judge"]["verdict"] == "REFUSED"
    assert "read-only" in f["judge"]["note"]
    third = tmp_path / "t"
    third.mkdir()
    runner, _, _, tasks = _screened(third, diag=[{"hang": True}, GOOD], over={"bounds": {"cloud_max_seconds": 2}})
    f, _ = runner.compare_compression(tasks, "cand-a")
    assert f["judge"]["verdict"] == "REFUSED" and "time bound" in f["judge"]["note"] and f["judge"]["correct"] is False


def test_the_cap_of_the_envelope_cuts_the_comparison_and_the_cut_task_is_undecided(tmp_path):
    runner, campaign, plan, tasks = _screened(tmp_path, diag=[GOOD, GOOD], caps={"cloud_executions": 1})
    out = runner.compare_compression(tasks, "cand-a")
    assert [r["path"] for r in out] == ["F"] and runner.stopped == "cap_reached:cloud_executions"
    recs = _records(runner)
    assert recs[("S", "diagnose")]["outcome"] == "stopped_by_cap" and recs[("S", "diagnose")]["judge"] is None
    assert len(_calls(plan)) == 1
    rep = lfr.report(campaign, results(runner), ledger_of(runner))["compression_comparison"]
    assert rep["arms"]["S"]["tasks"]["1"]["causes"] == ["diagnose:stopped_by_cap"] and rep["decision"] == "inconclusive"
    # the cap is never replayed: a relaunch (the envelope cannot change) plays nothing for the cut arm
    again, _, _, _ = make_c1(tmp_path, "compare_compression", diag=[GOOD], caps={"cloud_executions": 1},
                             repo_bundle=(runner.repo, {"prs": tasks}, None, None))
    assert again.compare_compression(tasks, "cand-a") == []


def test_an_attempt_cut_before_its_record_is_replayed_once_and_a_recorded_one_never(tmp_path):
    runner, campaign, plan, tasks = _screened(tmp_path, diag=[GOOD, GOOD], now=None)
    original = runner.cloud_execution
    state = {"n": 0}

    def flaky(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise lfr.RunnerError("a tool failure before any verdict")
        return original(*a, **k)
    runner.cloud_execution = flaky
    with pytest.raises(lfr.RunnerError):
        runner.compare_compression(tasks, "cand-a")
    assert _records(runner)[("F", "diagnose")]["outcome"] == "tool_error"
    again, _, _, _ = make_c1(tmp_path, "compare_compression", diag=[GOOD, GOOD], repo_bundle=(runner.repo, {"prs": tasks}, None, None))
    out = again.compare_compression(tasks, "cand-a")
    assert [r["path"] for r in out] == ["F", "S"] and out[0]["replay_of"]["outcome"] == "tool_error"
    third, _, _, _ = make_c1(tmp_path, "compare_compression", diag=[GOOD], repo_bundle=(runner.repo, {"prs": tasks}, None, None))
    assert third.compare_compression(tasks, "cand-a") == []  # both recorded: nothing is played again
    rep = lfr.report(campaign, results(third), ledger_of(third))["compression_comparison"]
    assert rep["complete"] is True and rep["arms"]["F"]["tasks"]["1"]["decided"] is True


def test_the_summary_arm_is_not_played_where_the_retained_candidate_has_no_summary(tmp_path):
    # the pilot has no selection: its candidate is the operator's; here cand-b, whose call failed, is compared
    first, campaign, plan, tasks = make_c1(tmp_path, "screen_compression", pilot=True, calls=[CALL_OK, CALL_FAIL])
    first.screen_compression(tasks, ["cand-a", "cand-b"])
    runner, _, _, _ = make_c1(tmp_path, "compare_compression", pilot=True, diag=[GOOD], repo_bundle=(first.repo, {"prs": tasks}, None, None))
    out = runner.compare_compression(tasks, "cand-b")
    assert [r["path"] for r in out] == ["F"] and len(_calls(plan)) == 1
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    cmp_ = rep["compression_comparison"]
    assert cmp_["complete"] is True and cmp_["arms"]["S"]["tasks"]["1"]["causes"] == ["no_summary"]
    assert cmp_["paired_rule"]["reason"] == "paired_decided_set_below_1" and cmp_["decision"] == "inconclusive"
    assert rep["compression_screening"]["selected"] is None and "pilot" in rep["compression_screening"]["reason"]


def test_the_pilot_needs_a_screening_result_for_its_candidate_and_has_no_selection(tmp_path):
    runner, _, plan, tasks = make_c1(tmp_path, "compare_compression", pilot=True)
    with pytest.raises(lfr.RunnerError, match="no screening result of candidate cand-a"):
        runner.compare_compression(tasks, "cand-a")
    assert not _calls(plan)
    campaign_runner, _, _, tasks = make_c1(tmp_path / "c", "compare_compression") if (tmp_path / "c").mkdir() is None else None
    with pytest.raises(lfr.RunnerError, match="no screening results under campaign id"):
        campaign_runner.compare_compression(tasks, "cand-a")


def test_the_envelope_holds_the_cap_of_40_by_shares_and_the_pilot_is_never_the_campaign(tmp_path):
    with pytest.raises(lfr.EnvelopeError, match="above the 36"):
        make_c1(tmp_path, "compare_compression", caps={"cloud_executions": 37})
    other = tmp_path / "p"
    other.mkdir()
    with pytest.raises(lfr.EnvelopeError, match="above the 4"):
        make_c1(other, "compare_compression", pilot=True, caps={"cloud_executions": 5})
    third = tmp_path / "ok"
    third.mkdir()
    make_c1(third, "compare_compression", caps={"cloud_executions": 36})
    fourth = tmp_path / "ok2"
    fourth.mkdir()
    make_c1(fourth, "compare_compression", pilot=True, caps={"cloud_executions": 4})
    assert 36 + 4 == lfk.C1_CLOUD_EXECUTIONS_MAX == 40
    for sub, kwargs in (("a", {"pilot": True, "campaign_id": "test-c1"}), ("b", {"pilot": False, "campaign_id": "test-c1-pilot"})):
        folder = tmp_path / sub
        folder.mkdir()
        with pytest.raises(lfr.RunnerError, match="campaign id"):
            make_c1(folder, "screen_compression", **kwargs)
    # the screening makes no cloud execution: its envelope may name none, and the cap check is the comparison's
    fifth = tmp_path / "scr"
    fifth.mkdir()
    runner, _, _, tasks = make_c1(fifth, "screen_compression", caps={"cloud_executions": 45})
    assert runner.screen_compression(tasks, ["cand-a"])
    with pytest.raises(lfr.EnvelopeError, match="can never start a cloud execution"):
        runner.ledger.reserve_cloud("diagnoser", "sid")


def test_a_screening_cannot_reach_the_cloud_and_a_comparison_needs_the_pinned_claude_code(tmp_path):
    runner, _, plan, tasks = make_c1(tmp_path, "screen_compression")
    with pytest.raises(lfr.EnvelopeError, match="can never start a cloud execution"):
        runner.cloud_execution("diagnoser", "cloud_diagnoser", tasks[0], tmp_path, "diagnose")
    for version, accepted in (("2.1.294", True), ("2.1.285", False), ("2.1.295", False)):
        folder = tmp_path / version
        folder.mkdir()
        bindir = folder / "bin"
        bindir.mkdir()
        (bindir / "claude").write_text(f"#!/bin/sh\necho '{version} (Claude Code)'\n", encoding="utf-8")
        (bindir / "claude").chmod(0o755)
        env = {**os.environ, "HOME": str(folder / "home"), "TMPDIR": str(folder / "shared-tmp"),
               "PATH": f"{bindir}:{os.environ['PATH']}"}
        first, _, _, tasks = make_c1(folder, "screen_compression", calls=[CALL_OK, CALL_FAIL])
        first.screen_compression(tasks, ["cand-a", "cand-b"])
        pin = {"binary_version": {"command": ["claude", "--version"], "pattern": r"^(\d+\.\d+\.\d+) \(Claude Code\)$",
                                  "version": "2.1.294"}}
        runner, campaign, plan, tasks = make_c1(folder, "compare_compression", diag=[GOOD, GOOD], host_env=env,
                                                repo_bundle=(first.repo, {"prs": tasks}, None, None))
        runner.campaign["drivers"]["cloud_diagnoser"].update(pin)
        if accepted:
            assert len(runner.compare_compression(tasks, "cand-a")) == 2
        else:
            with pytest.raises(lfr.RunnerError, match=f"claude on PATH is {version}, the pinned version is 2.1.294"):
                runner.compare_compression(tasks, "cand-a")
            assert not _calls(plan) and "cloud_started" not in [e["kind"] for e in ledger_of(runner)]  # nothing claimed or spent


def test_the_diagnoser_keeps_the_claude_environment_of_r6_and_runs_unwrapped(tmp_path):
    runner, _, plan, tasks = _screened(tmp_path, diag=[GOOD, GOOD])
    runner.compare_compression(tasks, "cand-a")
    for g in _calls(plan):
        for name in ("HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER"):
            assert (name in runner.host_env) == (name in g["env"]), name  # forwarded as the host has it, nothing else added
        assert os.path.realpath(g["cwd"]).startswith(os.path.realpath(runner.work_root))
    driver = runner.campaign["drivers"]["cloud_diagnoser"]
    assert driver["sandbox"] is False and not runner._sandboxed(driver)  # never wrapped by sandbox-exec
    committed = _load(C1)["drivers"]["cloud_diagnoser"]
    assert committed["sandbox"] is False and "sandbox-exec" not in json.dumps(committed["argv"])
    # the audit's findings never read as a guarantee of that kind
    flat = json.dumps(_load(C1), ensure_ascii=False).lower()
    assert NOT_THE_WORD[0] not in flat and NOT_THE_WORD[1] not in flat


# ------------------------------------------------------------------------------------- the command line

def _cli_files(tmp_path, monkeypatch, *, pilot=False, calls=None):
    """A configuration directory the CLI can read (config, ground truth, material), a repository, a snapshot, a
    manifest and an envelope; the local call is scripted (never the network), the temp directory the test's own."""
    repo, snap, _, _ = _make_repo(tmp_path)
    campaign, plan = c1_campaign(tmp_path, diag=[GOOD, GOOD], pilot=pilot)
    truth = {"schema": "x", "tasks": {"1": lfe.ground_truth(repo, snap["prs"][0])}}
    (tmp_path / "truth.json").write_text(json.dumps(truth), encoding="utf-8")
    campaign["compression"]["ground_truth"] = {"file": "truth.json", "sha256": hashlib.sha256((tmp_path / "truth.json").read_bytes()).hexdigest()}
    material = _one_material()[1]
    (tmp_path / "pr1.txt").write_text(material["text"], encoding="utf-8")
    entry = {"pr": 1, "file": "pr1.txt", "sha256": material["sha256"], "bytes": material["bytes"], "lines": material["lines"],
             "failing_ids": material["failing_ids"]}
    (tmp_path / "material.json").write_text(json.dumps({"schema": lfk.MATERIAL_SCHEMA, "tasks": {"1": entry}}), encoding="utf-8")
    campaign["compression"]["material"] = {"file": "material.json", "sha256": hashlib.sha256((tmp_path / "material.json").read_bytes()).hexdigest()}
    (tmp_path / "c.json").write_text(json.dumps(campaign), encoding="utf-8")
    (tmp_path / "snap.json").write_text(json.dumps(snap), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"comparison": [{"pr": 1}], "screening": []}), encoding="utf-8")
    envelope = tmp_path / "envelope.json"
    write_envelope(tmp_path, "cli-c1-pilot" if pilot else "cli-c1", cloud_executions=4 if pilot else 36)
    body = json.loads(envelope.read_text("utf-8"))
    body["allowed_modes"] = list(lfr.COMPRESSION_MODES)
    envelope.write_text(json.dumps(body), encoding="utf-8")
    (tmp_path / "shared-tmp").mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp_path / "shared-tmp"))
    # the loader's pins are the committed config's (tested above); the fake campaign is read as it is
    monkeypatch.setattr(lfr, "load_campaign", lambda path: json.loads(Path(path).read_text("utf-8")))
    scripted = list(calls if calls is not None else [CALL_OK, CALL_FAIL])
    monkeypatch.setattr(lfk, "call_local", lambda call, model, output: dict(scripted.pop(0)))

    def args(verb, *extra):
        return [verb, "--campaign", str(tmp_path / "c.json"), "--dry-run", "--state-dir", str(tmp_path / "state"),
                "--work-root", str(tmp_path / "work"), "--repo", str(repo), "--snapshot", str(tmp_path / "snap.json"),
                "--manifest", str(tmp_path / "manifest.json"), "--envelope", str(envelope), *extra]
    return args, plan


def test_the_cli_runs_the_screening_then_the_comparison_then_the_report(tmp_path, monkeypatch, capsys):
    args, plan = _cli_files(tmp_path, monkeypatch)
    assert lfr.main(args("screen-compression", "--candidate", "cand-a", "cand-b"), today=TODAY) == 0
    assert capsys.readouterr().out.strip() == ""  # no work_remains line: the screening is not a one-task-per-launch mode
    assert lfr.main(args("screen-compression", "--candidate", "cand-a", "cand-b"), today=TODAY) == 0  # a resume plays nothing
    assert lfr.main(args("compare-compression", "--candidate", "cand-b"), today=TODAY) == 2  # not the selected one
    assert "not the one the screening selected" in capsys.readouterr().err and not _calls(plan)
    assert lfr.main(args("compare-compression", "--candidate", "cand-a", "--paths", "A"), today=TODAY) == 2
    assert "unknown path" in capsys.readouterr().err
    assert lfr.main(args("compare-compression", "--candidate", "cand-a"), today=TODAY) == 0  # default arms F,S
    assert len(_calls(plan)) == 2
    state = tmp_path / "state"
    assert lfr.main(["report", "--campaign", str(tmp_path / "c.json"), "--results", str(state / "results-cli-c1.jsonl")]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["promotion"] is False and rep["compression_screening"]["selected"] == "cand-a"
    assert rep["compression_comparison"]["complete"] is True and rep["compression_comparison"]["decision"] == "keep_cloud"
    assert [len(json.loads(line)) for line in (state / "results-cli-c1.jsonl").read_text().splitlines()][:1]
    assert lfr.main(args("compare-compression", "--candidate", "cand-a"), today=TODAY) == 0  # nothing is played again
    assert len(_calls(plan)) == 2


def test_the_cli_pilot_names_its_candidate_and_the_dry_run_refuses_the_real_diagnoser(tmp_path, monkeypatch, capsys):
    real_load = lfr.load_campaign
    args, plan = _cli_files(tmp_path, monkeypatch, pilot=True, calls=[CALL_OK])
    assert lfr.main(args("screen-compression", "--candidate", "cand-a"), today=TODAY) == 0
    assert lfr.main(args("compare-compression", "--candidate", "cand-a"), today=TODAY) == 0
    assert len(_calls(plan)) == 2
    capsys.readouterr()
    monkeypatch.setattr(lfr, "load_campaign", real_load)  # the real loader again, for the committed config
    # the committed config's diagnoser is the real claude: a dry run never starts it
    other = tmp_path / "real"
    other.mkdir()
    monkeypatch.setattr(lfk, "call_local", lambda *a: pytest.fail("a local call was made"))
    argv = ["compare-compression", "--campaign", str(C1), "--dry-run", "--state-dir", str(other / "state"), "--work-root",
            str(other / "work"), "--repo", str(tmp_path / "repo"), "--snapshot", str(QUALIFICATION / "pat-19-corpus-snapshot-v1.json"),
            "--manifest", str(QUALIFICATION / "pat-19-corpus-manifest-v1.json"), "--candidate", V1_FIVE[2],
            "--envelope", str(tmp_path / "envelope.json")]
    other_env = tmp_path / "envelope.json"
    body = json.loads(other_env.read_text("utf-8"))
    body.update(campaign_id="real-c1", caps={**body["caps"], "cloud_executions": 36})
    (other / "envelope.json").write_text(json.dumps(body), encoding="utf-8")
    argv[argv.index("--envelope") + 1] = str(other / "envelope.json")
    assert lfr.main(argv, today=TODAY) == 2
    err = capsys.readouterr().err
    assert "dry run refuses the non-fake driver cloud_diagnoser" in err
    assert not (other / "state").exists() or not list((other / "state").glob("results-*"))


def test_the_new_verbs_exist_and_the_old_ones_are_unchanged(capsys):
    with pytest.raises(SystemExit):
        lfr.main(["--help"])
    help_text = capsys.readouterr().out
    for verb in ("screen-compression", "compare-compression", "compression-material", "screen-exploration", "golden-check"):
        assert verb in help_text
    assert lfr.COMPRESSION_MODES == ("screen_compression", "compare_compression")
    assert lfr.MODES == ("screen", "compare", "screen_exploration", "compare_exploration", *lfr.COMPRESSION_MODES)
    assert lfr.CLOUD_MODES == ("compare", "compare_exploration", "compare_compression")  # screening never reaches the cloud


def test_the_material_verb_is_offline_and_never_overwrites(tmp_path, capsys):
    repo, snap, _, _ = _make_repo(tmp_path)
    # the verb reads the 12 tasks of the protocol from the snapshot: a snapshot without them is refused, nothing written
    (tmp_path / "snap.json").write_text(json.dumps(snap), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"comparison": [{"pr": 1}], "screening": []}), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    code = lfr.main(["compression-material", "--repo", str(repo), "--work-root", str(tmp_path / "work"), "--snapshot",
                     str(tmp_path / "snap.json"), "--manifest", str(tmp_path / "manifest.json"), "--out-dir", str(out)])
    assert code == 2 and "not in the corpus manifest and the snapshot" in capsys.readouterr().err
    assert list(out.iterdir()) == []


# ------------------------------------------------------------------ the readings apart from the rule (PAT-129)

def test_the_cost_breakdown_reads_a_c1_campaign_apart_from_the_rule_and_earlier_documents_are_unchanged(tmp_path):
    from foundry import cost_breakdown as cb
    at = dt.datetime(2026, 10, 8, 12, tzinfo=dt.timezone.utc).timestamp()
    runner, campaign, _, tasks = _screened(tmp_path, diag=[GOOD, GOOD], now=lambda: at)
    runner.compare_compression(tasks, "cand-a")
    report = lfr.report(campaign, results(runner), ledger_of(runner))
    real = tmp_path / "real"
    real.mkdir()
    for source, name in ((runner.results_path, "results-test-c1.jsonl"), (runner.ledger.path, "ledger-test-c1.jsonl")):
        lines = [json.loads(line) for line in source.read_text("utf-8").splitlines()]
        for x in lines:  # the stand-in's usage line has no reasoning counter: the real host's has one (an int)
            for group in ((x.get("premium") or {}).get("by_role", {}), (x.get("premium") or {}).get("by_model", {})):
                for tokens in group.values():
                    tokens["reasoning_output_tokens"] = tokens.get("reasoning_output_tokens") or 0
        (real / name).write_text("".join(json.dumps({**x, "dry_run": False}) + "\n" for x in lines), encoding="utf-8")
    (real / "report.json").write_text(json.dumps(report), encoding="utf-8")
    doc = cb.analyse(real / "results-test-c1.jsonl", report=real / "report.json")
    assert set(doc["arms"]) == {"F", "S", "CS"}
    for arm in ("F", "S"):
        roles = doc["arms"][arm]["by_role"]
        assert roles["diagnoser"]["tokens"]["billing_total"] == 135 and "weighted_share" in roles["diagnoser"]
        assert doc["arms"][arm]["weighted"]["status"] == "priced"
    cover = doc["task_coverage"]
    assert cover["ratio_S_over_F_of_totals_on_common_tasks"]["unweighted"] == 1.0
    assert cover["ratio_S_over_F_of_totals_on_common_tasks"]["tasks"] == [1]
    assert cover["tasks_with_premium_in_every_arm"] == []  # the screening arm (local calls) spent no premium: told as is
    assert "totals_equal_committed_report" not in doc  # the v1 to v5 check reads an exploration report
    paired = doc["paired_reading"]
    assert paired["outside_the_frozen_rule"] is True and paired["paired_tasks"] == [1]
    assert paired["correct_counts_equal_records"] is True and paired["unweighted_ratio_S_over_F_per_correct"] == 1.0
    assert set(paired["weighted_ratio_S_over_F_per_correct"]) == {"cache_write_all_5m", "cache_write_all_1h"}
    assert paired["frozen_ratio_in_report"] == 1.0
    assert cb.main(["--results", str(real / "results-test-c1.jsonl"), "--report", str(real / "report.json"),
                    "--out", str(tmp_path / "doc.json")]) == 0
    # an earlier campaign's document has no diagnoser role and no S over F reading: it keeps exactly its keys
    old = RUNS_X5 / "results-pat-19-x5compare-1.jsonl"
    before = cb.analyse(old, report=RUNS_X5 / "report-pat-19-x5compare-1.json")
    assert "diagnoser" not in before["arms"]["A"]["by_role"] and "ratio_S_over_F_of_totals_on_common_tasks" not in before["task_coverage"]
    assert "correct_counts_equal_records" not in before["paired_reading"]


RUNS_X5 = QUALIFICATION / "pat-19-runs" / "x5compare-1"


# ------------------------------------------------------------------------------------- the operator script

SCRIPT = QUALIFICATION / "pat-19-c1-operator.sh"


def _operator(tmp_path, mode, *, campaign_id, candidate=None, claude="2.1.294", state_files=(), leftover=False):
    """Run the operator script as far as its refusals: every case below ends BEFORE any `lms` call (a stand-in
    `lms` that fails the test would be on the PATH otherwise); returns the completed process."""
    import shutil
    import subprocess
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash available")
    checkout, runs, work = tmp_path / "checkout", tmp_path / "runs", tmp_path / "work"
    (checkout / "plugins" / "foundry" / "docs").mkdir(parents=True)
    (checkout / "plugins" / "foundry" / "docs" / "qualification").symlink_to(QUALIFICATION)
    (runs / "state").mkdir(parents=True)
    (runs / "envelope.json").write_text(json.dumps({"campaign_id": campaign_id}), encoding="utf-8")
    for name in state_files:
        (runs / "state" / name).write_text("", encoding="utf-8")
    bindir, tmp = tmp_path / "bin", tmp_path / "tmpdir"
    bindir.mkdir()
    tmp.mkdir()
    if leftover:
        (tmp / "rv" / "plugins" / "foundry").mkdir(parents=True)
    for tool, body in (("claude", f"echo '{claude} (Claude Code)'"), ("lms", "echo LMS-WAS-CALLED >&2; exit 99")):
        (bindir / tool).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (bindir / tool).chmod(0o755)
    env = {"PATH": f"{bindir}:{os.environ['PATH']}", "TMPDIR": str(tmp), "HOME": str(tmp_path / "home"), "LANG": "C"}
    args = [bash, str(SCRIPT), mode, *([candidate] if candidate else []), str(checkout), str(runs), str(work), str(tmp_path / "repo")]
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=60, cwd=tmp_path)


def test_the_operator_script_refuses_before_any_model_is_loaded(tmp_path):
    import shutil
    import subprocess
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash available")
    assert subprocess.run([bash, "-n", str(SCRIPT)], capture_output=True).returncode == 0
    assert os.access(SCRIPT, os.X_OK)
    text = SCRIPT.read_text("utf-8")
    assert "lms unload --all" in text and "load_command" in text and "DRAFT" in text.splitlines()[1]
    assert "bypass" not in text and "sandbox-exec" not in text and NOT_THE_WORD[0] not in text.lower()
    for argv in ([], ["nope"], ["screen", "a", "b"], ["compare", "a", "b", "c", "d"], ["pilot"]):
        assert subprocess.run([bash, str(SCRIPT), *argv], capture_output=True).returncode == 64, argv
    cases = (  # (mode, kwargs, exit code, message)
        ("screen", {"campaign_id": "run-pilot-1"}, 65, "must not contain 'pilot'"),
        ("compare", {"campaign_id": "run-pilot-1", "candidate": V1_FIVE[2]}, 65, "must not contain 'pilot'"),
        ("pilot", {"campaign_id": "run-1", "candidate": V1_FIVE[2]}, 65, "must contain 'pilot'"),
        ("pilot", {"campaign_id": "pilot-1", "candidate": "nope"}, 65, "is not in"),
        ("pilot", {"campaign_id": "pilot-1", "candidate": V1_FIVE[2], "state_files": ["results-run-1.jsonl"]}, 65, "is not a pilot file"),
        ("screen", {"campaign_id": "run-1", "state_files": ["ledger-run-pilot-1.jsonl"]}, 65, "is a pilot file"),
        ("screen", {"campaign_id": "run-1", "leftover": True}, 65, "leftover bundle copy"),
        ("compare", {"campaign_id": "run-1", "candidate": V1_FIVE[2], "claude": "2.1.285"}, 65, "config pins 2.1.294"),
        ("pilot", {"campaign_id": "pilot-1", "candidate": V1_FIVE[2], "claude": "2.1.295"}, 65, "config pins 2.1.294"))
    for index, (mode, kwargs, code, message) in enumerate(cases):
        folder = tmp_path / f"case{index}"
        folder.mkdir()
        done = _operator(folder, mode, **kwargs)
        assert done.returncode == code and message in done.stderr, (mode, kwargs, done.stderr)
        assert "LMS-WAS-CALLED" not in done.stderr  # not even the final unload ran: nothing was started
    done = subprocess.run([bash, str(SCRIPT), "screen", str(tmp_path / "nowhere"), str(tmp_path / "r"), str(tmp_path / "w"),
                           str(tmp_path)], capture_output=True, text=True)
    assert done.returncode == 66 and "missing file" in done.stderr  # a missing config or envelope is 66


# ---------------------------------------------------------------------------------- the documents

PROTOCOL_DOC = QUALIFICATION / "pat-19-protocol-c1.md"
NEW_FILES = ("pat-19-protocol-c1.md", "pat-19-c1-operator.md", "pat-19-c1-operator.sh", "pat-19-campaign-c1.json",
             "pat-19-campaign-c1-pilot.json", "pat-19-compression-material-v1.json")


def test_the_draft_says_it_is_a_draft_and_never_the_word_it_must_not_write():
    doc = PROTOCOL_DOC.read_text("utf-8")
    assert doc.splitlines()[0].count("BROUILLON") == 1 and "STATUT : BROUILLON" in doc and "Rien n'est gelé" in doc
    assert "GELÉ" not in doc and "gelé le" not in doc  # nothing is called frozen
    for name in NEW_FILES:
        text = (QUALIFICATION / name).read_text("utf-8").lower()
        assert NOT_THE_WORD[0] not in text and NOT_THE_WORD[1] not in text, name
        assert "/users/" not in text and not re.search(r"/home/[a-z]", text), name  # no home path
        assert os.environ.get("USER", "\0") not in re.findall(r"[\w.-]+", text) or len(os.environ.get("USER", "")) < 3, name
    for name in ("pat-19-c1-operator.md", "pat-19-launcher-v1.md"):
        assert "pat-19-protocol-c1.md" in (QUALIFICATION / name).read_text("utf-8")
    for name in NEW_FILES[3:5]:
        assert "BROUILLON" not in (QUALIFICATION / name).read_text("utf-8") and "DRAFT" in (QUALIFICATION / name).read_text("utf-8")


def test_the_size_table_of_the_protocol_is_the_one_of_the_committed_index():
    doc = PROTOCOL_DOC.read_text("utf-8")
    index = _load(MATERIAL)["tasks"]
    rows = re.findall(r"^\| (\d+) \| ([\d ]+) \| ([\d ]+) \| (\d+) \| (\d+) \| ([\d ]+) \| (\d+/\d+/\d+) \| `([0-9a-f]{12})` \|$", doc, re.M)
    assert [int(r[0]) for r in rows] == TWELVE
    for pr, size, lines, failing, passed, quarter, masked, digest in rows:
        entry = index[pr]
        assert int(size.replace(" ", "")) == entry["bytes"] and int(lines.replace(" ", "")) == entry["lines"]
        assert int(failing) == len(entry["failing_ids"]) and int(passed) == entry["junit"]["passed"]
        assert int(quarter.replace(" ", "")) == entry["bytes"] // 4 and entry["sha256"].startswith(digest)
        assert masked == "/".join(str(entry["masked"][k]) for k in ("paths", "home", "user"))
    total = sum(e["bytes"] for e in index.values())
    assert f"{total:,}".replace(",", " ") in doc and "71,8 %" in doc


def test_the_declared_choices_and_the_validated_values_are_in_the_protocol_and_the_config():
    doc = PROTOCOL_DOC.read_text("utf-8")
    assert "mandat du mainteneur du 2026-10-08" in doc and "autorisation par bloc" in doc
    for needle in ("seuil 0,8", "25 %", "0,85", "40 exécutions", "36 pour la campagne + 4 pour le pilote", "2.1.294",
                   "effort `medium`", "BROUILLON", "L1 ", "L12 ", "keep_cloud", "inconclusive", "aucune promotion"):
        assert needle in doc, needle
    for letter in "abcdefg":
        assert f"({letter})" in doc
    assert doc.count("| C") >= 12
    # the stop criteria of the coordinator are all there, in the words of the brief
    for criterion in ("écarté par l'instrument", "peut pas lire son bundle", "settings_transmitted_version_observed",
                      "registry.json", "appel local ne peut pas être fait", "juge ne peut pas analyser"):
        assert criterion in doc, criterion
