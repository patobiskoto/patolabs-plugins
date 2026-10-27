"""PLP-318: per-turn review data of an issue, on synthetic local state only, offline."""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from foundry import invocation_log, review_remarks, review_turns
from foundry.routing import AcceptanceProofStore

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO = "synthetic-repo"


def _hex(seed, length):
    return (hashlib.sha256(seed.encode()).hexdigest() * 2)[:length]


def _proof(state, issue, seed, verdicts, quality, completed=None, carried=False, generation=1):
    store = AcceptanceProofStore(REPO, state)
    diff = _hex("diff" + seed, 64)
    proof = {
        "schema_version": 1,
        "issue": {"id": issue, "ac_digest": _hex("ac", 64), "criteria": [
            {"id": f"ac-{i}", "digest": _hex(f"c{i}", 64), "verdict": v}
            for i, v in enumerate(verdicts, start=1)]},
        "review": {"role": "reviewer", "generation": generation, "claim_digest": _hex("cl" + seed, 64)},
        "coordinates": {"head": _hex("head" + seed, 40), "diff_hash": diff, "base": _hex("b", 40)},
        "quality": quality,
    }
    if carried:
        proof["carried_from"] = {"proof_id": _hex("p", 64), "head": _hex("h", 40),
                                 "base": _hex("b", 40), "diff_hash": _hex("d", 64)}
    proof["proof_id"] = hashlib.sha256(
        json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    store._write(proof)
    if completed:
        from foundry.routing import ReviewDeduplicator
        directory = ReviewDeduplicator(REPO, state).directory
        directory.mkdir(parents=True, exist_ok=True)
        (directory / diff).write_text(json.dumps({"claim": {"id": "x", "started_at": completed},
                                                  "completed_at": completed}))
    return proof


def _receipt(state, proof, remarks):
    store = review_remarks.ReviewRemarksStore(REPO, state)
    store.directory.mkdir(parents=True, exist_ok=True)
    (store.directory / proof["proof_id"]).write_text(json.dumps({
        "schema_version": 1, "proof_id": proof["proof_id"], "issue": proof["issue"]["id"],
        "diff_hash": proof["coordinates"]["diff_hash"], "base": proof["coordinates"]["base"],
        "generation": proof["review"]["generation"], "remarks": remarks}))


def _reviewer(state, issue, head, ident="a" * 16):
    invocation_log._append(invocation_log._path(REPO, state), {
        "event": "routed", "id": ident, "issue": issue, "host": "claude", "role": "reviewer",
        "model_requested": "sonnet-5.5", "effort": "high", "head": head, "base": _hex("b", 40),
        "at": "2026-10-01T00:00:00.000000Z", "status": "routed"})


def _attempt(state, issue, proof, partial=True, carried=False, fix=1, non_blocking=2, head=None, event="finished"):
    record = {"schema_version": "foundry-campaign-attempts.v1", "event": event, "attempt": "a" * 8, "epic": "PLP-E", "ticket": issue,
              "kind": "review", "status": "completed", "exit_code": 0, "cost_usd": None,
              "duration_ms": 1, "summary_file": None, "branch_head": None, "error": None,
              "at": "2026-10-01T00:00:00Z",
              "review": {"verdict": "blocked", "fix_before_merge": fix, "non_blocking": non_blocking,
                         "diff_hash": proof["coordinates"]["diff_hash"], "base": _hex("b", 40),
                         "head": head or proof["coordinates"]["head"], "carried": carried,
                         "partial": partial}}
    with (Path(state) / "campaign-attempts.jsonl").open("a") as handle:
        handle.write(json.dumps(record) + "\n")


def _snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_turns_rank_verdict_remarks_and_reviewer(tmp_path):
    first = _proof(tmp_path, "PLP-1", "1", ["pass", "fail"], "blocked", "2026-10-01T10:00:00Z")
    second = _proof(tmp_path, "PLP-1", "2", ["pass", "pass"], "mergeable", "2026-10-02T10:00:00Z", generation=2)
    _proof(tmp_path, "PLP-1", "c", ["pass"], "mergeable", carried=True)
    _proof(tmp_path, "PLP-2", "o", ["pass"], "mergeable")
    _receipt(tmp_path, second, [{"text": "n", "fix_before_merge": False, "target": "diff"}] * 3)
    _reviewer(tmp_path, "PLP-1", second["coordinates"]["head"])
    result = review_turns.report("PLP-1", REPO, state_dir=tmp_path)
    one, two = result["turns"]
    assert (one["rank"], one["verdict"], one["criteria_pass"], one["criteria"]) == (1, "blocked", 1, 2)
    assert one["started_at"] == one["completed_at"] == "2026-10-01T10:00:00Z"
    assert (two["rank"], two["verdict"], two["remarks"]) == (2, "validated", 3)
    assert two["head"] == second["coordinates"]["head"] and two["base"] == _hex("b", 40)
    assert (two["reviewer_model"], two["reviewer_effort"], two["reviewer_host"]) == ("sonnet-5.5", "high", "claude")
    assert result["carried_proofs"] == 1
    assert first["coordinates"]["head"] == one["head"]


def test_unrecorded_data_is_null_with_reason(tmp_path):
    _proof(tmp_path, "PLP-1", "1", ["pass"], "mergeable")
    turn = review_turns.report("PLP-1", REPO, state_dir=tmp_path)["turns"][0]
    assert turn["type"] is None and turn["remarks"] is None and turn["reviewer_model"] is None
    assert turn["completed_at"] is None
    assert turn["unrecorded"] == {"type": "no_campaign_record", "remarks": "no_campaign_receipt",
                                  "reviewer_model": "no_reviewer_record_for_head"}


def test_malformed_receipt_and_unreadable_log_are_reported(tmp_path):
    proof = _proof(tmp_path, "PLP-1", "1", ["pass"], "mergeable")
    store = review_remarks.ReviewRemarksStore(REPO, tmp_path)
    store.directory.mkdir(parents=True)
    (store.directory / proof["proof_id"]).write_text("{not json")
    path = invocation_log._path(REPO, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * (invocation_log._MAX_BYTES + 1))
    turn = review_turns.report("PLP-1", REPO, state_dir=tmp_path)["turns"][0]
    assert turn["unrecorded"]["remarks"] == "receipt_unreadable"
    assert turn["unrecorded"]["reviewer_model"] == "invocation_log_unreadable"


def test_type_comes_from_the_campaign_journal(tmp_path):
    partial = _proof(tmp_path, "PLP-1", "1", ["pass"], "mergeable", "2026-10-01T10:00:00Z")
    full = _proof(tmp_path, "PLP-1", "2", ["pass", "fail"], "blocked", "2026-10-02T10:00:00Z", generation=2)
    _attempt(tmp_path, "PLP-1", partial, partial=True)
    _attempt(tmp_path, "PLP-1", full, partial=False, fix=2, non_blocking=1)
    _attempt(tmp_path, "PLP-1", full, partial=False, fix=2, non_blocking=1)  # same retry: not ambiguous
    _attempt(tmp_path, "PLP-1", partial, partial=False, carried=True)  # carried: ignored
    _attempt(tmp_path, "PLP-9", full, partial=True)  # another ticket: ignored
    one, two = review_turns.report("PLP-1", REPO, state_dir=tmp_path)["turns"]
    assert (one["type"], two["type"]) == ("partial", "full")
    assert "type" not in one["unrecorded"] and "type" not in two["unrecorded"]
    # no receipt: the journal counts fill the remarks
    assert (one["remarks"], two["remarks"]) == (3, 3)
    assert "remarks" not in one["unrecorded"]


def test_receipt_wins_over_journal_count(tmp_path):
    proof = _proof(tmp_path, "PLP-1", "1", ["pass"], "mergeable")
    _receipt(tmp_path, proof, [{"text": "n", "fix_before_merge": False, "target": "diff"}])
    _attempt(tmp_path, "PLP-1", proof, fix=4, non_blocking=4)
    assert review_turns.report("PLP-1", REPO, state_dir=tmp_path)["turns"][0]["remarks"] == 1


def test_no_matching_record_or_other_head_gives_no_campaign_record(tmp_path):
    proof = _proof(tmp_path, "PLP-1", "1", ["pass"], "mergeable")
    _attempt(tmp_path, "PLP-1", proof, head=_hex("other", 40))
    turn = review_turns.report("PLP-1", REPO, state_dir=tmp_path)["turns"][0]
    assert turn["type"] is None and turn["unrecorded"]["type"] == "no_campaign_record"


def test_unreadable_attempts_journal(tmp_path):
    _proof(tmp_path, "PLP-1", "1", ["pass"], "mergeable")
    (tmp_path / "campaign-attempts.jsonl").write_text("{not json\n")
    turn = review_turns.report("PLP-1", REPO, state_dir=tmp_path)["turns"][0]
    assert turn["type"] is None and turn["unrecorded"]["type"] == "attempts_journal_unreadable"
    assert turn["remarks"] is None and turn["unrecorded"]["remarks"] == "attempts_journal_unreadable"


def test_diverging_campaign_records_are_ambiguous(tmp_path):
    proof = _proof(tmp_path, "PLP-1", "1", ["pass"], "mergeable")
    _attempt(tmp_path, "PLP-1", proof, partial=True)
    _attempt(tmp_path, "PLP-1", proof, partial=False)
    turn = review_turns.report("PLP-1", REPO, state_dir=tmp_path)["turns"][0]
    assert turn["type"] is None and turn["remarks"] is None
    assert turn["unrecorded"]["type"] == "ambiguous" and turn["unrecorded"]["remarks"] == "ambiguous"


def test_receipt_with_another_schema_version_is_unreadable(tmp_path):
    proof = _proof(tmp_path, "PLP-1", "1", ["pass"], "mergeable")
    _receipt(tmp_path, proof, [])
    path = review_remarks.ReviewRemarksStore(REPO, tmp_path).directory / proof["proof_id"]
    path.write_text(path.read_text().replace('"schema_version": 1', '"schema_version": 2'))
    assert review_turns.report("PLP-1", REPO, state_dir=tmp_path)["turns"][0]["unrecorded"]["remarks"] \
        == "receipt_unreadable"


def test_sources_cover_every_emitted_field(tmp_path):
    _proof(tmp_path, "PLP-1", "1", ["pass"], "mergeable")
    turn = review_turns.report("PLP-1", REPO, state_dir=tmp_path)["turns"][0]
    assert set(turn) - {"unrecorded"} <= set(review_turns.SOURCES)


def test_no_proof_gives_no_turn(tmp_path):
    assert review_turns.report("PLP-1", REPO, state_dir=tmp_path)["turns"] == []


def test_read_only_and_content_free(tmp_path):
    proof = _proof(tmp_path, "PLP-1", "1", ["pass"], "mergeable", "2026-10-01T10:00:00Z")
    _receipt(tmp_path, proof, [{"text": "SECRET-TEXT", "fix_before_merge": False, "target": "diff"}])
    _reviewer(tmp_path, "PLP-1", proof["coordinates"]["head"])
    _attempt(tmp_path, "PLP-1", proof)
    before = _snapshot(tmp_path)
    first = json.dumps(review_turns.report("PLP-1", REPO, state_dir=tmp_path), sort_keys=True)
    assert json.dumps(review_turns.report("PLP-1", REPO, state_dir=tmp_path), sort_keys=True) == first
    assert _snapshot(tmp_path) == before
    for forbidden in (proof["proof_id"], proof["coordinates"]["diff_hash"], "SECRET-TEXT", "a" * 16):
        assert forbidden not in first


def test_module_has_no_network_tracker_or_write_surface():
    source = (PLUGIN_ROOT / "tooling" / "foundry" / "review_turns.py").read_text()
    imports = set(re.findall(r"^(?:from|import) ([\w.]+)", source, re.M))
    assert imports <= {"__future__", "json", "re", "pathlib", "foundry", "foundry.review_rounds", "foundry.routing"}
    assert not re.search(r"\b(write_text|write_bytes|urlopen|socket|subprocess|record_routed|_append|"
                         r"\.record\(|mkdir|unlink|replace)\b", source)


def test_only_the_cli_verb_imports_review_turns():
    importers = {p.name for p in (PLUGIN_ROOT / "tooling").rglob("*.py")
                 if re.search(r"^\s*(from foundry import .*\breview_turns\b|from foundry\.review_turns\b)",
                              p.read_text(), re.M)}
    assert importers == {"routing.py", "kpi_export.py"}  # the CLI verb and the PLP-319 export


def test_cli_verb_prints_sorted_json(tmp_path):
    _proof(tmp_path, "PLP-1", "1", ["pass"], "mergeable")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "FOUNDRY_DATA": str(tmp_path),
           "PYTHONPATH": str(PLUGIN_ROOT / "tooling")}
    out = subprocess.run([sys.executable, "-m", "foundry.routing", "review-turns", "PLP-1",
                          "--repository", REPO], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["schema"] == "review-turns.v1"
