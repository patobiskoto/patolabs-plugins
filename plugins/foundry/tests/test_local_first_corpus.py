"""PAT-107: deterministic tests of the PAT-19 corpus tooling (no model, no network).

The end-to-end cases build a throwaway git repository in a temp directory; nothing reads
the real history of this repository."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from foundry import local_first_corpus as lfc

QUALIFICATION = Path(__file__).resolve().parents[1] / "docs" / "qualification"
BASE_MODULE = "def add(a, b):\n    return a - b\n"
FIXED_MODULE = "def add(a, b):\n    return a + b\n"
BASE_TESTS = (
    "from foundry.m import add\n\n\n"
    "def test_existing():\n    assert add(0, 0) == 0\n\n\n"
    "def test_changed():\n    assert add(1, 1) == 0\n")
HEAD_TESTS = (
    "from foundry.m import add\n\n\n"
    "def test_existing():\n    assert add(0, 0) == 0\n\n\n"
    "def test_changed():\n    assert add(1, 1) == 2\n\n\n"
    "def test_added():\n    assert add(2, 3) == 5\n")
CONFTEST = ("import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tooling'))\n")
STATEMENT = "# add adds\n\n## Objectif\n\n- [ ] add(a, b) must return the sum.\n"
MOD = "plugins/foundry/tooling/foundry/m.py"
TEST = "plugins/foundry/tests/test_m.py"


def _task(pr, title="[PAT-1] fix: x", files=None, **extra):
    base = {"pr": pr, "title": title, "issue": lfc.issue_of(title),
            "merge_kind": "squash_on_main", "ac_text": "some AC", "ac_source": "x",
            "files": files if files is not None else [
                {"path": MOD, "status": "M", "added": 5, "deleted": 1},
                {"path": TEST, "status": "M", "added": 10, "deleted": 0}],
            "protected": {"entries": [{"path": TEST, "status": "M", "kind": "test",
                                       "selection": "nodes", "nodes": ["test_a"]}]}}
    base.update(extra)
    return base


# ------------------------------------------------------------------ criteria and draw

def test_exclusion_reasons_are_explicit_and_complete():
    assert lfc.exclusion_reasons(_task(1)) == []
    big = _task(2, files=[{"path": MOD, "status": "M", "added": 301, "deleted": 0},
                          {"path": TEST, "status": "M", "added": 0, "deleted": 0}])
    assert "over_300_changed_lines" in lfc.exclusion_reasons(big)
    docs = _task(3, files=[{"path": "plugins/foundry/docs/a.md", "status": "M",
                            "added": 3, "deleted": 0}], protected={"entries": []})
    reasons = lfc.exclusion_reasons(docs)
    assert {"documentation_only", "no_tests_under_plugins_foundry",
            "no_protected_tests"} <= set(reasons)
    assert lfc.exclusion_reasons(_task(4, merge_kind="not_replayable")) == [
        "not_single_parent_squash_commit"]
    assert lfc.exclusion_reasons(_task(5, title="[PAT-5] fix: Basculer le dépôt")) == [
        "topic:data_migration"]
    assert lfc.exclusion_reasons(_task(6, title="[PAT-6] fix: merge-pr refuse")) == [
        "topic:gates_evaluation"]
    assert lfc.exclusion_reasons(_task(7), {"7": "judgment call"}) == ["manual:judgment call"]
    assert lfc.exclusion_reasons(_task(8, ac_text="")) == ["empty_acceptance_criteria"]


def test_topic_path_rule_ignores_tests_and_docs():
    files = [{"path": "plugins/foundry/tooling/foundry/escalation.py", "status": "M",
              "added": 1, "deleted": 0},
             {"path": TEST, "status": "M", "added": 1, "deleted": 0}]
    assert lfc.exclusion_reasons(_task(1, files=files)) == ["topic:gates_evaluation"]
    only_test_name = [{"path": MOD, "status": "M", "added": 1, "deleted": 0},
                      {"path": "plugins/foundry/tests/test_escalation.py", "status": "M",
                       "added": 1, "deleted": 0}]
    assert lfc.exclusion_reasons(_task(2, files=only_test_name)) == []


def _snapshot(count=20):
    prs = [_task(n, title=f"[PAT-{n}] fix: t{n}") for n in range(1, count + 1)]
    prs.append(_task(99, title="[PAT-99] fix: merge-pr gate"))
    return {"prs": prs}


def test_draw_is_deterministic_seeded_and_complete():
    snap = _snapshot()
    first = lfc.draw(snap, "seed-a")
    assert first == lfc.draw(json.loads(json.dumps(snap)), "seed-a")
    assert first["algorithm"] == lfc.DRAW_ALGORITHM
    assert len(first["comparison"]) == 6 and len(first["screening"]) == 6
    assert len(first["replacement_queue"]) == 8
    everyone = first["comparison"] + first["screening"] + first["replacement_queue"]
    assert sorted(everyone) == list(range(1, 21))
    assert [e["pr"] for e in first["excluded"]] == [99]
    assert first["excluded"][0]["reasons"] == ["topic:gates_evaluation"]
    assert lfc.draw(snap, "seed-b")["comparison"] != first["comparison"]
    expected = sorted(range(1, 21), key=lambda n: hashlib.sha256(f"seed-a:{n}".encode()).hexdigest())
    assert everyone == expected
    assert first["comparison"] == expected[:6] and first["screening"] == expected[6:12]
    assert not set(first["comparison"]) & set(first["screening"])


def test_finalize_replaces_failures_from_the_queue_in_order():
    snap = _snapshot(14)
    for t in snap["prs"]:
        t.update(base_sha="b" * 40, head_sha="h" * 40, pr_head_sha="p" * 40)
    drawn = lfc.draw(snap, "s")
    bad = {drawn["comparison"][1], drawn["screening"][0]}
    calls = []

    def verify(pr):
        calls.append(pr)
        return {"pr": pr, "discarded_reason": "x" if pr in bad else None}

    corpus, evidence = lfc.finalize(snap, drawn, verify)
    queue = drawn["replacement_queue"]
    assert [t["pr"] for t in corpus["comparison"]][1] == queue[0]
    assert [t["pr"] for t in corpus["screening"]][0] == queue[1]
    assert {d["pr"] for d in corpus["discarded_at_verification"]} == bad
    assert sorted(e["pr"] for e in evidence) == sorted(set(calls))


def test_finalize_fails_when_the_queue_is_exhausted():
    snap = _snapshot(12)
    for t in snap["prs"]:
        t.update(base_sha="b", head_sha="h", pr_head_sha="p")
    with pytest.raises(lfc.CorpusError):
        lfc.finalize(snap, lfc.draw(snap, "s"), lambda pr: {"discarded_reason": "x"})


# -------------------------------------------------------------- protected-test analysis

def test_changed_test_nodes_selects_added_and_modified_functions():
    info = lfc.changed_test_nodes(BASE_TESTS, HEAD_TESTS)
    assert info["selection"] == "nodes"
    assert info["nodes"] == ["test_added", "test_changed"]


def test_changed_helper_or_module_constant_selects_whole_file():
    base = "X = 1\n\n\ndef helper():\n    return 1\n\n\ndef test_a():\n    assert helper()\n"
    assert lfc.changed_test_nodes(base, base.replace("return 1", "return 2"))["selection"] == "file"
    assert lfc.changed_test_nodes(base, base.replace("X = 1", "X = 2"))["selection"] == "file"
    assert lfc.changed_test_nodes(None, base)["selection"] == "file"
    only_format = base.replace("assert helper()", "assert  helper()")
    assert lfc.changed_test_nodes(base, only_format)["nodes"] == []


def test_class_methods_are_qualified_and_removed_without_breaking_syntax():
    source = ("import pytest\n\n\nclass TestA:\n    @pytest.mark.parametrize('x', [1])\n"
              "    def test_one(self, x):\n        assert x\n\n"
              "    def test_two(self):\n        assert True\n\n\ndef test_top():\n    assert 1\n")
    head = source.replace("assert True", "assert 2")
    assert lfc.changed_test_nodes(source, head)["nodes"] == ["TestA::test_two"]
    out = lfc._remove_functions(source, ["TestA::test_one", "TestA::test_two", "test_top"])
    assert "pass" in out and "test_one" not in out and "parametrize" not in out
    assert "test_top" not in out


def test_task_statement_is_the_tracker_text_verbatim_and_fails_closed():
    text = "# Do the thing\n\n## Objectif\n\n- [ ] It must work.\n"
    assert lfc.task_statement(_task(1, ac_text=text, ac_source="tracker_issue")) == text
    with pytest.raises(lfc.CorpusError):
        lfc.task_statement(_task(1, ac_source="pr_body_summary"))


# --------------------------------------------------------------- end-to-end, temp repo

def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    *args], check=True, capture_output=True)


@pytest.fixture()
def mini_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")

    def write(rel, text):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    write("plugins/foundry/pytest.ini", "[pytest]\n")
    write("plugins/foundry/tests/conftest.py", CONFTEST)
    write("plugins/foundry/tooling/foundry/__init__.py", "")
    write(MOD, BASE_MODULE)
    write(TEST, BASE_TESTS)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    write(MOD, FIXED_MODULE)
    write(TEST, HEAD_TESTS)
    write("plugins/foundry/docs/x.md", "doc\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "[PAT-1] fix: add adds (#1)")
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    prs = [{"number": 1, "title": "[PAT-1] fix: add adds", "baseRefName": "main",
            "body": "## Summary\n\nadd(a, b) must return the sum.\n\n## Validation\n\nsecret details",
            "mergeCommit": {"oid": head}, "headRefOid": head}]
    snap = lfc.build_snapshot(repo, prs, "HEAD", ac_overrides={"1": STATEMENT})
    return repo, snap, base, head


def test_snapshot_freezes_shas_files_ac_and_protected_tests(mini_repo):
    _, snap, base, head = mini_repo
    task = snap["prs"][0]
    assert (task["base_sha"], task["head_sha"]) == (base, head)
    assert task["merge_kind"] == "squash_on_main" and task["issue"] == "PAT-1"
    assert task["ac_text"] == STATEMENT and task["ac_source"] == "tracker_issue"
    assert {f["path"] for f in task["files"]} == {MOD, TEST, "plugins/foundry/docs/x.md"}
    assert lfc.changed_lines(task) > 0
    entry, = task["protected"]["entries"]
    assert entry["nodes"] == ["test_added", "test_changed"]
    assert lfc.exclusion_reasons(task) == []


def test_bundle_has_no_protected_tests_and_no_solution(mini_repo, tmp_path):
    repo, snap, _, _ = mini_repo
    task = snap["prs"][0]
    dest = lfc.build_bundle(repo, task, tmp_path / "bundle")
    try:
        text = (dest / TEST).read_text(encoding="utf-8")
        assert "test_existing" in text and "test_changed" not in text and "test_added" not in text
        assert (dest / MOD).read_text(encoding="utf-8") == BASE_MODULE
        statement = (dest / "TASK.md").read_text(encoding="utf-8")
        assert statement == STATEMENT and "secret details" not in statement
    finally:
        lfc.remove_bundle(repo, dest)
    assert not dest.exists()


def test_bundle_refuses_a_pr_description_as_statement(mini_repo, tmp_path):
    repo, _, _, head = mini_repo
    prs = [{"number": 1, "title": "[PAT-1] fix: x", "baseRefName": "main", "body": "## Summary\n\nhi",
            "mergeCommit": {"oid": head}, "headRefOid": head}]
    task = lfc.build_snapshot(repo, prs, "HEAD")["prs"][0]
    assert task["ac_source"] == "pr_body_summary"
    with pytest.raises(lfc.CorpusError):
        lfc.build_bundle(repo, task, tmp_path / "nope")
    assert not (tmp_path / "nope").exists()
    with pytest.raises(lfc.CorpusError):
        lfc.main(["bundle", "--repo", str(repo), "--snapshot", str(_write(tmp_path, task)),
                  "--pr", "1", "--dest", str(tmp_path / "nope2")])


def _write(tmp_path, task):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"prs": [task]}), encoding="utf-8")
    return path


def test_verify_accepts_merged_solution_and_refuses_base(mini_repo, tmp_path):
    repo, snap, _, _ = mini_repo
    result = lfc.verify_task(repo, snap["prs"][0], tmp_path / "work")
    assert result["with_merged_diff"]["verdict"] == "ACCEPTED"
    assert result["with_merged_diff"]["passed"] == 2
    assert result["without_merged_diff"]["verdict"] == "REFUSED"
    assert result["without_merged_diff"]["failed"] == 2
    assert result["discarded_reason"] is None
    assert not list((tmp_path / "work").iterdir())


def test_candidate_written_tests_never_count(mini_repo, tmp_path):
    repo, snap, _, _ = mini_repo
    task = snap["prs"][0]
    dest = lfc.build_bundle(repo, task, tmp_path / "cheat")
    try:
        # The candidate neuters the protected path and adds a passing test elsewhere.
        (dest / TEST).write_text("def test_changed():\n    assert True\n\n\n"
                                 "def test_added():\n    assert True\n", encoding="utf-8")
        (dest / "plugins/foundry/tests/test_mine.py").write_text(
            "def test_mine():\n    assert True\n", encoding="utf-8")
        verdict = lfc.judge(repo, task, dest)
    finally:
        lfc.remove_bundle(repo, dest)
    assert verdict["verdict"] == "REFUSED"
    assert verdict["selected"] == [f"tests/test_m.py::{n}" for n in ("test_added", "test_changed")]


def test_a_task_the_judge_cannot_refuse_without_solution_is_discarded(mini_repo, tmp_path):
    repo, snap, _, _ = mini_repo
    task = json.loads(json.dumps(snap["prs"][0]))
    task["protected"]["entries"][0]["nodes"] = ["test_existing"]  # passes on the base too
    result = lfc.verify_task(repo, task, tmp_path / "work")
    assert result["discarded_reason"] == "judge_accepts_base_without_solution"


def test_cli_draw_matches_library(mini_repo, tmp_path, capsys):
    _, snap, _, _ = mini_repo
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(snap), encoding="utf-8")
    assert lfc.main(["draw", "--snapshot", str(path), "--seed", "s"]) == 0
    assert json.loads(capsys.readouterr().out) == json.loads(json.dumps(lfc.draw(snap, "s")))


# ------------------------------------------------------------- committed corpus files

def test_committed_manifest_is_the_deterministic_draw_of_the_committed_snapshot():
    snapshot_path = QUALIFICATION / "pat-19-corpus-snapshot-v1.json"
    manifest = json.loads((QUALIFICATION / "pat-19-corpus-manifest-v1.json").read_text("utf-8"))
    manual = json.loads((QUALIFICATION / "pat-19-corpus-manual-exclusions-v1.json").read_text("utf-8"))
    snapshot = json.loads(snapshot_path.read_text("utf-8"))
    assert manifest["snapshot_sha256"] == hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    assert manifest["draw"] == lfc.draw(snapshot, manifest["seed"], manual)
    by_pr = {t["pr"]: t for t in snapshot["prs"]}
    assert len(manifest["comparison"]) == 6 and len(manifest["screening"]) == 6
    assert not {t["pr"] for t in manifest["comparison"]} & {t["pr"] for t in manifest["screening"]}
    assert "filter" not in manifest and "extension" not in manifest
    for task in manifest["comparison"] + manifest["screening"]:
        assert task["ac_source"] == "tracker_issue" and by_pr[task["pr"]]["ac_text"] == task["acceptance_criteria"]
        frozen = by_pr[task["pr"]]
        assert (task["base_sha"], task["head_sha"]) == (frozen["base_sha"], frozen["head_sha"])
        assert task["issue"] and task["acceptance_criteria"] and task["protected_tests"]
        assert task["changed_lines"] <= lfc.MAX_CHANGED_LINES
    for item in manifest["verification"]:
        if item["discarded_reason"] is None:
            assert item["with_merged_diff"]["verdict"] == "ACCEPTED"
            assert item["without_merged_diff"]["verdict"] == "REFUSED"
    text = json.dumps(manifest) + snapshot_path.read_text("utf-8")
    assert "/Users/" not in text and "/home/" not in text
