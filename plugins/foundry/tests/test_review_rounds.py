"""PAT-175: per-issue review-round report, on synthetic stores and logs only."""
import hashlib
import json

from foundry import review_rounds
from foundry.routing import AcceptanceProofStore, ReviewDeduplicator

REPO = "synthetic-repo"


def _hex(seed, length):
    return (hashlib.sha256(seed.encode()).hexdigest() * 2)[:length]


def _proof(store, issue, seed, verdicts, quality, carried=False, ledger=None):
    diff = _hex("diff" + seed, 64)
    proof = {
        "schema_version": 1,
        "issue": {"id": issue, "ac_digest": _hex("ac", 64), "criteria": [
            {"id": f"ac-{i}", "digest": _hex(f"c{i}", 64), "verdict": v}
            for i, v in enumerate(verdicts, start=1)]},
        "review": {"role": "reviewer", "generation": 1, "claim_digest": _hex("claim" + seed, 64)},
        "coordinates": {"head": _hex("head" + seed, 40), "diff_hash": diff, "base": _hex("b", 40)},
        "quality": quality,
    }
    if carried:
        proof["carried_from"] = {"proof_id": _hex("p", 64), "head": _hex("h", 40),
                                 "base": _hex("b", 40), "diff_hash": _hex("d", 64)}
    proof["proof_id"] = hashlib.sha256(
        json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    store._write(proof)
    if ledger:
        directory = ReviewDeduplicator(REPO, store.directory.parents[1]).directory
        directory.mkdir(parents=True, exist_ok=True)
        (directory / diff).write_text(json.dumps({
            "claim": {"id": "x", "started_at": ledger[0]}, "completed_at": ledger[1]}))


def _store(tmp_path):
    return AcceptanceProofStore(REPO, tmp_path)


def _claude_line(session, tokens, agent=None):
    return json.dumps({
        "type": "assistant", "sessionId": session, "timestamp": "2026-10-01T10:00:00Z",
        "requestId": "r" + str(tokens), "agentId": agent, "cwd": "/w",
        "message": {"id": "m" + str(tokens), "model": "claude-sonnet-5-5", "stop_reason": "end_turn",
                    "usage": {"input_tokens": tokens, "cache_read_input_tokens": 2 * tokens,
                              "cache_creation_input_tokens": 3 * tokens, "output_tokens": 4 * tokens,
                              "thinking_tokens": 0}}})


def test_rounds_outcome_and_duration(tmp_path):
    store = _store(tmp_path)
    _proof(store, "PAT-1", "a", ["pass", "fail"], "blocked",
           ledger=("2026-10-01T10:00:00Z", "2026-10-01T10:30:00Z"))
    _proof(store, "PAT-1", "b", ["pass", "pass"], "mergeable",
           ledger=("2026-10-01T11:00:00Z", "2026-10-01T11:10:00Z"))
    _proof(store, "PAT-1", "c", ["pass", "pass"], "mergeable", carried=True)
    _proof(store, "PAT-2", "d", ["pass"], "mergeable")
    result = review_rounds.report("PAT-1", REPO, state_dir=tmp_path)
    assert result["rounds"] == 2 and result["carried_proofs"] == 1
    assert (result["validated_rounds"], result["blocked_rounds"]) == (1, 1)
    assert result["outcome"] == "validated"
    assert result["duration_seconds"] == 4200.0
    assert [r["quality"] for r in result["per_round"]] == ["blocked", "mergeable"]
    assert result["tokens"]["coordinator"] is None  # nothing pointed at: unknown, never 0
    text = json.dumps(result)
    assert _hex("claim" + "a", 64) not in text


def test_unknown_without_timestamps_or_proofs(tmp_path):
    store = _store(tmp_path)
    _proof(store, "PAT-3", "e", ["pass"], "mergeable")
    result = review_rounds.report("PAT-3", REPO, state_dir=tmp_path)
    assert result["duration_seconds"] is None and result["duration_rounds_timed"] == 0
    empty = review_rounds.report("PAT-9", REPO, state_dir=tmp_path)
    assert empty["rounds"] == 0 and empty["outcome"] == "unknown"
    assert "unknown" in review_rounds.render(empty)


def test_tokens_split_coordinator_and_subagents(tmp_path):
    main = tmp_path / "sess.jsonl"
    main.write_text(_claude_line("s", 10) + "\n")
    sub = tmp_path / "sess" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-1.jsonl").write_text(_claude_line("s", 1, "a1") + "\n" + _claude_line("s", 2, "a1") + "\n")
    result = review_rounds.report("PAT-1", REPO, state_dir=tmp_path, claude_sessions=[main])
    assert result["tokens"]["coordinator"] == {
        "input": 10, "cache_read": 20, "cache_write": 30, "output": 40}
    assert result["tokens"]["subagents"] == {
        "input": 3, "cache_read": 6, "cache_write": 9, "output": 12}
    assert result["tokens"]["codex_unclassified"] is None


def test_unreadable_log_is_unknown(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n")
    result = review_rounds.report("PAT-1", REPO, state_dir=tmp_path, claude_sessions=[bad])
    assert result["tokens"]["coordinator"] is None and result["tokens"]["unreadable_logs"] == 1


def test_cli_json(tmp_path, monkeypatch, capsys):
    from foundry import routing
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path))
    _proof(_store(tmp_path), "PAT-1", "a", ["pass"], "mergeable")
    routing.main(["review-rounds", "PAT-1", "--repository", REPO, "--json"])
    assert json.loads(capsys.readouterr().out)["rounds"] == 1
