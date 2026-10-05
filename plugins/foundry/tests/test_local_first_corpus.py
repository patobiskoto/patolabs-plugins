"""PAT-107: deterministic tests of the PAT-19 corpus tooling (no model, no network).

The end-to-end cases build a throwaway git repository in a temp directory; nothing reads
the real history of this repository."""
from __future__ import annotations

import hashlib
import json
import os
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
    for title in ("[PAT-5] fix: dépôt basculé", "[PAT-5] fix: DÉPÔT BASCULÉ", "[PAT-5] fix: dépôt basculé".replace("é", "e\u0301")):
        assert lfc.exclusion_reasons(_task(5, title=title)) == ["topic:data_migration"]
    assert lfc.exclusion_reasons(_task(5, title="[PAT-5] fix: Dérogation")) == ["topic:authority"]
    assert lfc.exclusion_reasons(_task(6, title="[PAT-6] fix: merge-pr refuse")) == [
        "topic:gates_evaluation"]
    assert lfc.exclusion_reasons(_task(7), {"7": {"decision": "exclude", "reason": "judgment call"}}
                                 ) == ["manual:judgment call"]
    assert lfc.exclusion_reasons(_task(8, ac_text="")) == ["empty_acceptance_criteria"]


def test_include_override_lifts_only_topic_rules_and_needs_a_reason():
    migr = _task(5, title="[PAT-5] fix: dépôt basculé")
    include = {"5": {"decision": "include", "reason": "recovery fix, not a migration"}}
    assert lfc.exclusion_reasons(migr, include) == []
    big = _task(6, title="[PAT-6] fix: dépôt basculé",
                files=[{"path": MOD, "status": "M", "added": 400, "deleted": 0},
                       {"path": TEST, "status": "M", "added": 1, "deleted": 0}], ac_text="")
    assert lfc.exclusion_reasons(big, {"6": include["5"]}) == [
        "over_300_changed_lines", "empty_acceptance_criteria"]
    assert lfc.exclusion_reasons(_task(7, merge_kind="not_replayable"), {"7": include["5"]}) == [
        "not_single_parent_squash_commit"]
    for bad in ({"decision": "include", "reason": " "}, {"decision": "maybe", "reason": "x"}, "x"):
        with pytest.raises(lfc.CorpusError):
            lfc.exclusion_reasons(migr, {"5": bad})
    drawn = lfc.draw({"prs": [migr, _task(8)]}, "s", include)
    assert drawn["overrides"] == [{"pr": 5, "issue": "PAT-5", "decision": "include",
                                   "reason": "recovery fix, not a migration",
                                   "lifted_reasons": ["topic:data_migration"]}]
    assert drawn["excluded"] == [] and drawn["eligible_count"] == 2


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
    with pytest.raises(lfc.CorpusError, match="tests/test_bad.py"):
        lfc.changed_test_nodes(base, "def test_a(:\n", "tests/test_bad.py")
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
    with pytest.raises(lfc.CorpusError, match="tests/test_bad.py"):
        lfc._remove_functions("def test_a(:\n", ["test_a"], "tests/test_bad.py")


def test_comment_and_decorator_block_above_a_removed_function_goes_with_it():
    source = ("import pytest\n\n# keeps: module comment\n\n\n# describes test_a: secret detail\n"
              "# second line\n@pytest.mark.parametrize('x', [1])\ndef test_a(x):\n    assert x\n\n\n"
              "def test_b():\n    assert 1\n")
    out = lfc._remove_functions(source, ["test_a"])
    assert "secret detail" not in out and "second line" not in out and "parametrize" not in out
    assert "keeps: module comment" in out and "def test_b" in out


def test_scrub_statement_rule():
    text = ("See [PAT-61](https://linear.app/patolabs/issue/PAT-61/slug-x) and "
            "https://linear.app/patolabs/issue/PAT-5/y plus <https://linear.app/a/b>. id=460f66ac-bb2a-448d-ae41-588b3b275892 "
            "[docs](https://example.com/keep) PAT-7 stays.")
    out = lfc.scrub_statement(text)
    assert out == ("See PAT-61 and  plus . id=<uuid removed> "
                   "[docs](https://example.com/keep) PAT-7 stays.")
    assert lfc.scrub_statement(out) == out


def test_task_statement_is_the_tracker_text_verbatim_and_fails_closed():
    text = "# Do the thing\n\n## Objectif\n\n- [ ] It must work.\n"
    assert lfc.task_statement(_task(1, ac_text=text, ac_source="tracker_issue")) == text
    with pytest.raises(lfc.CorpusError):
        lfc.task_statement(_task(1, ac_source="pr_body_summary"))


# --------------------------------------------------------------- end-to-end, temp repo

def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           *args], check=True, capture_output=True, text=True).stdout


def _statements(tmp_path, mapping):
    folder = tmp_path / "statements"
    folder.mkdir(exist_ok=True)
    path = folder / lfc.STATEMENTS_FILE
    path.write_text(json.dumps({"schema": lfc.STATEMENTS_SCHEMA, "captured_on": "2026-10-05",
                                "statements": mapping}), encoding="utf-8")
    return lfc.load_statements(path)


def _write(repo, rel, text):
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _make_repo(tmp_path, head_files=None, base_files=None, prs_extra=None):
    """Base commit, then a ``[PAT-1] ... (#1)`` squash-like commit on ``main``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    base_files = {"plugins/foundry/pytest.ini": "[pytest]\n", "plugins/foundry/tests/conftest.py": CONFTEST,
                  "plugins/foundry/tooling/foundry/__init__.py": "", MOD: BASE_MODULE, TEST: BASE_TESTS,
                  **(base_files or {})}
    head_files = {MOD: FIXED_MODULE, TEST: HEAD_TESTS, "plugins/foundry/docs/x.md": "doc\n",
                  **(head_files if head_files is not None else {})}
    for rel, text in base_files.items():
        _write(repo, rel, text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    for rel, text in head_files.items():
        _write(repo, rel, text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "[PAT-1] fix: add adds (#1)")
    head = _git(repo, "rev-parse", "HEAD").strip()
    prs = [{"number": 1, "title": "[PAT-1] fix: add adds", "baseRefName": "main",
            "body": "## Summary\n\nadd(a, b) must return the sum.\n\n## Validation\n\nsecret details",
            "mergeCommit": {"oid": head}, "headRefOid": head, **(prs_extra or {})}]
    snap = lfc.build_snapshot(repo, prs, "HEAD", statements=_statements(tmp_path, {"1": STATEMENT}))
    return repo, snap, base, head


@pytest.fixture()
def mini_repo(tmp_path):
    return _make_repo(tmp_path)


def test_snapshot_freezes_shas_files_ac_and_protected_tests(mini_repo):
    _, snap, base, head = mini_repo
    task = snap["prs"][0]
    assert (task["base_sha"], task["head_sha"]) == (base, head)
    assert task["merge_kind"] == "squash_on_main" and task["issue"] == "PAT-1"
    assert task["ac_text"] == STATEMENT and task["ac_source"] == "tracker_issue"
    assert snap["statements"]["file"] == lfc.STATEMENTS_FILE
    assert {f["path"] for f in task["files"]} == {MOD, TEST, "plugins/foundry/docs/x.md"}
    assert lfc.changed_lines(task) > 0
    entry, = task["protected"]["entries"]
    assert entry["nodes"] == ["test_added", "test_changed"]
    assert lfc.exclusion_reasons(task) == []


def test_tracker_label_only_comes_from_the_committed_statements_file(tmp_path, mini_repo):
    repo, _, _, head = mini_repo
    prs = [{"number": 1, "title": "[PAT-1] fix: x", "baseRefName": "main", "body": "## Summary\n\nhi",
            "mergeCommit": {"oid": head}, "headRefOid": head}]
    with pytest.raises(lfc.CorpusError):
        lfc.build_snapshot(repo, prs, "HEAD", statements={"statements": {"1": "text"}})
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"schema": lfc.STATEMENTS_SCHEMA, "captured_on": "x",
                                 "statements": {"1": "text"}}), encoding="utf-8")
    with pytest.raises(lfc.CorpusError, match="committed"):
        lfc.load_statements(other)
    unscrubbed = _statements(tmp_path, {"1": "ok"})  # loads
    bad = tmp_path / "statements" / lfc.STATEMENTS_FILE
    bad.write_text(json.dumps({"schema": lfc.STATEMENTS_SCHEMA, "captured_on": "x", "statements": {
        "1": "[PAT-2](https://linear.app/x/issue/PAT-2/y)"}}), encoding="utf-8")
    assert unscrubbed["sha256"] != hashlib.sha256(bad.read_bytes()).hexdigest()
    with pytest.raises(lfc.CorpusError, match="not scrubbed"):
        lfc.load_statements(bad)
    snap = lfc.build_snapshot(repo, prs, "HEAD")
    assert snap["prs"][0]["ac_source"] == "pr_body_summary" and snap["statements"] is None


def test_snapshot_kinds_stacked_branch_merge_commit_and_rebase_merge(tmp_path):
    repo, snap, base, head = _make_repo(tmp_path)
    pr = {"number": 1, "title": "[PAT-1] fix: add adds", "body": "b",
          "mergeCommit": {"oid": head}, "headRefOid": head}
    stacked = lfc.build_snapshot(repo, [{**pr, "baseRefName": "feat/stack"}], "HEAD")["prs"][0]
    assert stacked["merge_kind"] == "squash_on_stacked_branch" and stacked["base_ref"] == "feat/stack"
    assert stacked["base_sha"] == base and stacked["files"]
    assert lfc.exclusion_reasons({**stacked, "ac_text": "x"}) == []
    # a merge commit (two parents) is not replayable
    _git(repo, "checkout", "-q", "-b", "side", base)
    _write(repo, "side.txt", "s")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "side")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge side (#2)", "side")
    merge = _git(repo, "rev-parse", "HEAD").strip()
    merged = lfc.build_snapshot(repo, [{**pr, "number": 2, "baseRefName": "main",
                                        "mergeCommit": {"oid": merge}}], "HEAD")["prs"][0]
    assert merged["merge_kind"] == "not_replayable" and merged["base_sha"] is None
    assert lfc.exclusion_reasons(merged) == ["not_single_parent_squash_commit"]
    # one parent but not a squash of this PR: rebase-merge of several commits, subject without (#n)
    wrong = lfc.build_snapshot(repo, [{**pr, "number": 7, "baseRefName": "main"}], "HEAD")["prs"][0]
    assert wrong["merge_kind"] == "not_replayable"
    assert lfc.exclusion_reasons(wrong) == ["merge_commit_not_a_squash_of_this_pr"]
    _write(repo, "more.txt", "m")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second commit of the PR (#9)")
    _write(repo, "more2.txt", "m")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "last commit of the PR (#9)")
    rebased = lfc.build_snapshot(repo, [{**pr, "number": 9, "baseRefName": "main", "mergeCommit": {
        "oid": _git(repo, "rev-parse", "HEAD").strip()}}], "HEAD")["prs"][0]
    assert lfc.exclusion_reasons(rebased) == ["rebase_merge_of_several_commits"]


def test_replayability_records_local_presence_without_network(mini_repo):
    repo, snap, base, head = mini_repo
    task = snap["prs"][0]
    info = lfc.replayability(repo, task)
    assert info["base_sha"]["present_locally"] and info["head_sha"]["present_locally"]
    assert info["public_reachability"] == "unknown" and "git fetch origin" in info["fetch_hint"]
    assert info["base_sha"]["origin_refs_containing_count"] == 0  # a count, never ref names (N-G)
    assert "origin_refs_containing" not in info["base_sha"]
    gone = lfc.replayability(repo, {**task, "base_sha": "0" * 40})
    assert gone["base_sha"]["present_locally"] is False


# ---- bundle: no link to the developer repository (PAT-ADR-0015)

def _dev_state(repo):
    return {name: _git(repo, *args) for name, args in {
        "head": ("rev-parse", "HEAD"), "refs": ("for-each-ref",), "worktrees": ("worktree", "list"),
        "count": ("count-objects", "-v"), "reflog": ("reflog", "--all"),
        "status": ("status", "--porcelain")}.items()}


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
        lfc.remove_bundle(dest)
    assert not dest.exists()


def test_bundle_holds_no_link_to_the_developer_repository(mini_repo, tmp_path):
    repo, snap, base, head = mini_repo
    task = snap["prs"][0]
    secrets = {head, _git(repo, "rev-parse", f"{head}^{{tree}}").strip(),
               _git(repo, "rev-parse", f"{head}:{TEST}").strip(),
               _git(repo, "rev-parse", f"{base}:{TEST}").strip(),
               _git(repo, "rev-parse", f"{head}:{MOD}").strip(), base}
    before = _dev_state(repo)
    dest = lfc.build_bundle(repo, task, tmp_path / "bundle")

    def bundle_git(*args, check=True):
        return subprocess.run(["git", "-C", str(dest), "-c", "user.name=c", "-c", "user.email=c@example.invalid",
                               *args], capture_output=True, text=True, check=check)

    # one root commit, a neutral author, none of the developer objects
    assert len(bundle_git("rev-list", "--all").stdout.split()) == 1
    assert bundle_git("log", "--format=%an <%ae>").stdout.strip() == "Corpus Bundle <corpus@example.invalid>"
    objects = {line.split()[0] for line in bundle_git(
        "cat-file", "--batch-all-objects", "--batch-check").stdout.splitlines()}
    assert not secrets & objects
    for secret in secrets:
        assert bundle_git("cat-file", "-e", secret, check=False).returncode != 0
        assert bundle_git("show", secret, check=False).returncode != 0
    # no remote, alternates, worktree link or foreign reflog
    assert bundle_git("remote").stdout.strip() == "" and "origin" not in bundle_git("branch", "-a").stdout
    assert (dest / ".git").is_dir() and not (dest / ".git" / "objects" / "info" / "alternates").exists()
    assert bundle_git("worktree", "list").stdout.count("\n") == 1
    assert len(bundle_git("reflog").stdout.strip().splitlines()) == 1
    assert bundle_git("config", "--get-regexp", r"^(remote|branch)\.", check=False).stdout == ""
    # the developer path appears nowhere in the bundle
    needle = str(repo).encode()
    for path in dest.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert needle not in path.read_bytes(), path
        assert needle not in str(path.readlink() if path.is_symlink() else "").encode()
    # the candidate may commit, branch, stash and gc: the developer repository is untouched
    _write(dest, "plugins/foundry/tooling/foundry/m.py", FIXED_MODULE)
    bundle_git("add", "-A")
    bundle_git("commit", "-q", "-m", "solution")
    bundle_git("branch", "scratch")
    _write(dest, "junk.txt", "x")
    bundle_git("stash", "-u", "-q")
    bundle_git("gc", "-q", "--prune=now")
    assert _dev_state(repo) == before
    assert "worktrees" not in os.listdir(repo / ".git")
    lfc.remove_bundle(dest)


def test_bundle_destination_is_never_deleted_unless_created_by_this_run(mini_repo, tmp_path):
    repo, snap, _, _ = mini_repo
    task = snap["prs"][0]
    existing = tmp_path / "keep"
    existing.mkdir()
    (existing / "mine.txt").write_text("precious", encoding="utf-8")
    with pytest.raises(lfc.CorpusError, match="already exists"):
        lfc.build_bundle(repo, task, existing)
    with pytest.raises(lfc.CorpusError, match="did not create"):
        lfc.remove_bundle(existing)
    inside = repo / "plugins" / "bundle-here"
    with pytest.raises(lfc.CorpusError, match="inside the developer checkout"):
        lfc.build_bundle(repo, task, inside)
    assert not inside.exists() and (existing / "mine.txt").read_text(encoding="utf-8") == "precious"
    # verify_task with a pre-existing destination aborts (PAT-108 N-D) and leaves it alone
    work = tmp_path / "work"
    (work / "pr1-with_merged_diff").mkdir(parents=True)
    (work / "pr1-with_merged_diff" / "mine.txt").write_text("precious", encoding="utf-8")
    with pytest.raises(lfc.CorpusError, match="already exists"):
        lfc.verify_task(repo, task, work)
    assert (work / "pr1-with_merged_diff" / "mine.txt").read_text(encoding="utf-8") == "precious"
    assert not (work / "pr1-without_merged_diff").exists()
    # a failed build cleans up what it created
    broken = json.loads(json.dumps(task))
    broken["protected"]["entries"][0]["nodes"] = ["test_existing"]
    _git(repo, "checkout", "-q", "-b", "other")
    with pytest.raises(lfc.CorpusError):
        lfc.build_bundle(repo, {**broken, "base_sha": "f" * 40}, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()


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
        lfc.main(["bundle", "--repo", str(repo), "--snapshot", str(_write_snapshot(tmp_path, task)),
                  "--pr", "1", "--dest", str(tmp_path / "nope2")])


def _write_snapshot(tmp_path, task):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"prs": [task]}), encoding="utf-8")
    return path


def test_bundle_with_unparsable_test_file_is_a_corpus_error_and_leaves_nothing(mini_repo, tmp_path):
    repo, snap, _, _ = mini_repo
    task = json.loads(json.dumps(snap["prs"][0]))
    _write(repo, TEST, "def test_x(:\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "broken")
    task["base_sha"] = _git(repo, "rev-parse", "HEAD").strip()
    with pytest.raises(lfc.CorpusError, match="test_m.py"):
        lfc.build_bundle(repo, task, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


# ---- judge

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
        lfc.remove_bundle(dest)
    assert verdict["verdict"] == "REFUSED"
    assert verdict["selected"] == [f"tests/test_m.py::{n}" for n in ("test_added", "test_changed")]
    assert verdict["changed_outside_product"] == ["plugins/foundry/tests/test_mine.py"]


@pytest.mark.parametrize("rel", [
    "plugins/foundry/conftest.py", "plugins/foundry/tests/conftest.py", "plugins/foundry/pytest.ini",
    "plugins/foundry/pyproject.toml", "plugins/foundry/tox.ini", "plugins/foundry/setup.cfg",
    "plugins/foundry/tests/sub/conftest.py", "sitecustomize.py", "usercustomize.py",
    "plugins/foundry/tooling/evil.pth", "plugins/foundry/tooling/foundry/conftest.py"])
def test_judge_refuses_a_candidate_that_touches_the_pytest_harness(mini_repo, tmp_path, rel):
    repo, snap, _, _ = mini_repo
    task = snap["prs"][0]
    control = lfc.build_bundle(repo, task, tmp_path / "control")  # a bundle is judged only once
    try:
        lfc.apply_solution(repo, task, control)
        assert lfc.judge(repo, task, control)["verdict"] == "ACCEPTED"  # control: honest solution
    finally:
        lfc.remove_bundle(control)
    dest = lfc.build_bundle(repo, task, tmp_path / "tamper")
    try:
        lfc.apply_solution(repo, task, dest)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("import pytest\n" if rel.endswith(".py") else "[pytest]\n",
                          encoding="utf-8") if not target.exists() else target.write_text(
            target.read_text(encoding="utf-8") + "\n# edit\n", encoding="utf-8")
        verdict = lfc.judge(repo, task, dest)
    finally:
        lfc.remove_bundle(dest)
    assert verdict["verdict"] == "REFUSED" and verdict["note"].startswith("harness_files_changed:")
    assert rel in verdict["tripwire"] and verdict["passed"] == 0 and verdict["pytest_exit_code"] is None


def test_judge_refuses_a_candidate_that_deletes_a_harness_file(mini_repo, tmp_path):
    repo, snap, _, _ = mini_repo
    task = snap["prs"][0]
    dest = lfc.build_bundle(repo, task, tmp_path / "del")
    try:
        lfc.apply_solution(repo, task, dest)
        (dest / "plugins/foundry/pytest.ini").unlink()
        verdict = lfc.judge(repo, task, dest)
    finally:
        lfc.remove_bundle(dest)
    assert verdict["verdict"] == "REFUSED" and verdict["tripwire"] == ["plugins/foundry/pytest.ini"]


def test_judge_reports_support_edits_without_refusing(mini_repo, tmp_path):
    repo, snap, _, _ = mini_repo
    task = snap["prs"][0]
    dest = lfc.build_bundle(repo, task, tmp_path / "report")
    try:
        lfc.apply_solution(repo, task, dest)
        _write(dest, "plugins/foundry/tests/helper_data.py", "X = 1\n")
        _write(dest, "plugins/foundry/tooling/foundry/extra.py", "Y = 1\n")
        verdict = lfc.judge(repo, task, dest)
    finally:
        lfc.remove_bundle(dest)
    assert verdict["verdict"] == "ACCEPTED" and verdict["tripwire"] == []
    assert verdict["changed_outside_product"] == ["plugins/foundry/docs/x.md",
                                                  "plugins/foundry/tests/helper_data.py"]


def test_judge_refuses_symlinks_at_protected_paths_and_never_writes_through_them(mini_repo, tmp_path):
    repo, snap, _, _ = mini_repo
    task = snap["prs"][0]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.py").write_text("untouched\n", encoding="utf-8")
    dest = lfc.build_bundle(repo, task, tmp_path / "links")
    try:
        (dest / TEST).unlink()
        (dest / TEST).symlink_to(outside / "victim.py")
        file_link = lfc.judge(repo, task, dest)
        (dest / TEST).unlink()
        tests_dir = dest / "plugins/foundry/tests"
        (outside / "tests").mkdir()
        for child in tests_dir.iterdir():
            child.rename(outside / "tests" / child.name)
        tests_dir.rmdir()
        tests_dir.symlink_to(outside / "tests")
        dir_link = lfc.judge(repo, task, dest)
    finally:
        lfc.remove_bundle(dest)
    for verdict in (file_link, dir_link):
        assert verdict["verdict"] == "REFUSED" and verdict["note"].startswith("symlink_at_protected_path:")
    assert (outside / "victim.py").read_text(encoding="utf-8") == "untouched\n"
    assert not (outside / "tests" / "test_m.py").exists()  # nothing restored through the link


def test_judge_with_zero_selected_tests_refuses_instead_of_running_everything(mini_repo, tmp_path):
    repo, snap, _, _ = mini_repo
    task = json.loads(json.dumps(snap["prs"][0]))
    task["protected"]["entries"] = [{"path": "plugins/foundry/tests/conftest.py", "status": "M",
                                     "kind": "support", "selection": "file", "nodes": []}]
    dest = lfc.build_bundle(repo, snap["prs"][0], tmp_path / "none")
    try:
        verdict = lfc.judge(repo, task, dest)
    finally:
        lfc.remove_bundle(dest)
    assert verdict["verdict"] == "REFUSED" and verdict["note"] == "no_selected_tests"
    assert verdict["pytest_exit_code"] is None


PROBE_TEST = (
    "import os\nfrom pathlib import Path\n\n\n"
    "def test_environment_is_scrubbed():\n"
    "    home = Path(os.environ['HOME'])\n"
    "    assert home.name == 'home' and home.parent.name.startswith('foundry-judge-')\n"
    "    assert str(home) != {real_home!r}\n"
    "    assert Path(os.environ['TMPDIR']).parent == home.parent\n"
    "    assert not [k for k in os.environ if k.startswith(('FOUNDRY_', 'LINEAR', 'GITHUB', 'ANTHROPIC', 'SECRET'))]\n"
    "    assert Path('~').expanduser() == home\n")


def test_judge_runs_pytest_with_a_temporary_home_and_a_whitelisted_environment(tmp_path, monkeypatch):
    real_home = os.environ.get("HOME", "")
    for name in ("FOUNDRY_STATE_DIR", "LINEAR_API_KEY", "GITHUB_TOKEN", "SECRET_THING", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "leak")
    repo, snap, _, _ = _make_repo(tmp_path, {"plugins/foundry/tests/test_probe.py": PROBE_TEST.format(
        real_home=real_home)})
    task = snap["prs"][0]
    probe = [e for e in task["protected"]["entries"] if e["path"].endswith("test_probe.py")]
    assert probe and probe[0]["selection"] == "file"
    dest = lfc.build_bundle(repo, task, tmp_path / "env")
    try:
        verdict = lfc.judge(repo, task, dest)
    finally:
        lfc.remove_bundle(dest)
    # the probe test passes (and the solution-dependent tests fail): the failure count proves it ran
    assert "tests/test_probe.py" in verdict["selected"]
    assert verdict["failed"] == 2 and verdict["passed"] == 1, verdict


def test_judge_timeout_is_a_refusal(tmp_path, monkeypatch):
    repo, snap, _, _ = _make_repo(tmp_path, {
        "plugins/foundry/tests/test_slow.py": "import time\n\n\ndef test_slow():\n    time.sleep(120)\n"})
    task = json.loads(json.dumps(snap["prs"][0]))
    task["protected"]["entries"] = [e for e in task["protected"]["entries"] if "test_slow" in e["path"]]
    monkeypatch.setattr(lfc, "PYTEST_TIMEOUT_SECONDS", 3)
    dest = lfc.build_bundle(repo, snap["prs"][0], tmp_path / "slow")
    try:
        verdict = lfc.judge(repo, task, dest)
    finally:
        lfc.remove_bundle(dest)
    assert verdict["verdict"] == "REFUSED" and verdict["note"] == "timeout"
    assert verdict["pytest_exit_code"] is None and verdict["passed"] == 0


def test_whole_added_files_and_support_entries(tmp_path):
    conftest_head = CONFTEST + "\nimport pytest\n\n\n@pytest.fixture\ndef value():\n    return 5\n"
    new_test = ("from foundry.m import add\n\n\ndef test_new_file(value):\n"
                "    assert add(2, 3) == value\n")
    repo, snap, _, _ = _make_repo(tmp_path, {
        "plugins/foundry/tests/conftest.py": conftest_head,
        "plugins/foundry/tests/test_new.py": new_test,
        "plugins/foundry/tests/helper_support.py": "VALUE = 5\n"})
    task = snap["prs"][0]
    kinds = {e["path"].rsplit("/", 1)[-1]: (e["kind"], e["status"], e["selection"])
             for e in task["protected"]["entries"]}
    assert kinds == {"test_m.py": ("test", "M", "nodes"), "test_new.py": ("test", "A", "file"),
                     "conftest.py": ("support", "M", "file"), "helper_support.py": ("support", "A", "file")}
    assert lfc.protected_count(task["protected"]["entries"]) == 3  # 2 nodes + 1 whole file
    dest = lfc.build_bundle(repo, task, tmp_path / "bundle")
    try:
        tests_dir = dest / "plugins/foundry/tests"
        assert not (tests_dir / "test_new.py").exists() and not (tests_dir / "helper_support.py").exists()
        assert (tests_dir / "conftest.py").read_text(encoding="utf-8") == CONFTEST  # base version
    finally:
        lfc.remove_bundle(dest)
    result = lfc.verify_task(repo, task, tmp_path / "work")
    assert result["with_merged_diff"]["verdict"] == "ACCEPTED", result
    assert result["with_merged_diff"]["passed"] == 3
    assert result["without_merged_diff"]["verdict"] == "REFUSED"
    assert result["discarded_reason"] is None


def test_a_task_the_judge_cannot_refuse_without_solution_is_discarded(mini_repo, tmp_path):
    repo, snap, _, _ = mini_repo
    task = json.loads(json.dumps(snap["prs"][0]))
    task["protected"]["entries"][0]["nodes"] = ["test_existing"]  # passes on the base too
    result = lfc.verify_task(repo, task, tmp_path / "work")
    assert result["discarded_reason"] == "judge_accepts_base_without_solution"


# ---- PAT-108 carry-over of the PAT-107 reviews (N-A .. N-G)

def test_judge_purges_precompiled_bytecode_planted_by_the_candidate(mini_repo, tmp_path):
    """N-A: an unchecked-hash .pyc of the FIXED module next to the BASE source would make the
    broken product pass; the judge deletes every .pyc/__pycache__ and redirects bytecode."""
    import importlib.util
    import py_compile
    repo, snap, _, _ = mini_repo
    task = snap["prs"][0]
    dest = lfc.build_bundle(repo, task, tmp_path / "pyc")
    try:
        module = dest / MOD
        fixed = tmp_path / "fixed_m.py"
        fixed.write_text(FIXED_MODULE, encoding="utf-8")
        cached = Path(importlib.util.cache_from_source(str(module)))
        cached.parent.mkdir(parents=True, exist_ok=True)
        py_compile.compile(str(fixed), cfile=str(cached), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        (dest / "plugins/foundry/tooling/foundry/orphan.pyc").write_bytes(b"x")
        verdict = lfc.judge(repo, task, dest)
        assert not cached.exists() and not list(dest.rglob("*.pyc"))
    finally:
        lfc.remove_bundle(dest)
    assert verdict["verdict"] == "REFUSED" and verdict["failed"] == 2, verdict


def test_judge_environment_redirects_bytecode_to_its_temporary_directory(tmp_path, monkeypatch):
    seen = {}
    real_run = subprocess.run

    def spy(cmd, *a, **kw):
        if isinstance(cmd, list) and "pytest" in cmd:
            seen.update(kw["env"])
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(lfc.subprocess, "run", spy)
    repo, snap, _, _ = _make_repo(tmp_path)
    dest = lfc.build_bundle(repo, snap["prs"][0], tmp_path / "b")
    try:
        lfc.judge(repo, snap["prs"][0], dest)
    finally:
        lfc.remove_bundle(dest)
    assert "foundry-judge-" in seen["PYTHONPYCACHEPREFIX"]
    assert "PYTHONPATH" not in seen


def test_judge_refuses_a_candidate_inside_the_developer_checkout(mini_repo):
    """N-B"""
    repo, snap, _, _ = mini_repo
    inside = repo / "plugins" / "elsewhere"
    inside.mkdir(parents=True)
    with pytest.raises(lfc.CorpusError, match="inside the developer checkout"):
        lfc.judge(repo, snap["prs"][0], inside)
    assert not (inside / "plugins").exists()  # nothing was restored into it


def test_a_bundle_is_judged_once_fresh_bundle_per_attempt(mini_repo, tmp_path):
    """N-C: a judged bundle holds the protected tests and is never judged (or handed) again."""
    repo, snap, _, _ = mini_repo
    task = snap["prs"][0]
    dest = lfc.build_bundle(repo, task, tmp_path / "once")
    try:
        assert not lfc.is_judged(dest)
        assert lfc.judge(repo, task, dest)["verdict"] == "REFUSED"
        assert lfc.is_judged(dest)
        with pytest.raises(lfc.CorpusError, match="already judged"):
            lfc.judge(repo, task, dest)
    finally:
        lfc.remove_bundle(dest)


def test_candidate_environments_are_ignored_but_product_dirs_are_not(mini_repo, tmp_path):
    """N-E: a venv / node_modules the candidate created does not refuse an honest candidate."""
    repo, snap, _, _ = mini_repo
    task = snap["prs"][0]
    dest = lfc.build_bundle(repo, task, tmp_path / "env")
    try:
        (dest / MOD).write_text(FIXED_MODULE, encoding="utf-8")
        for name in (".venv", "venv", "node_modules", "custom_env"):
            site = dest / name / "lib" / "site-packages"
            site.mkdir(parents=True)
            (site / "conftest.py").write_text("", encoding="utf-8")
            (site / "x.pth").write_text("", encoding="utf-8")
        (dest / "custom_env" / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
        verdict = lfc.judge(repo, task, dest)
    finally:
        lfc.remove_bundle(dest)
    assert verdict["verdict"] == "ACCEPTED", verdict
    assert verdict["tripwire"] == []
    # the same files outside an environment directory still trip the wire
    dest = lfc.build_bundle(repo, task, tmp_path / "env2")
    try:
        (dest / MOD).write_text(FIXED_MODULE, encoding="utf-8")
        (dest / "docs_site").mkdir()
        (dest / "docs_site" / "conftest.py").write_text("", encoding="utf-8")
        verdict = lfc.judge(repo, task, dest)
    finally:
        lfc.remove_bundle(dest)
    assert verdict["verdict"] == "REFUSED" and verdict["tripwire"] == ["docs_site/conftest.py"]


def test_judge_always_passes_an_explicit_pytest_ini(tmp_path, monkeypatch):
    """N-F: even when the merged SHA has no pytest.ini, ``-c`` points at a judge-owned file."""
    repo, snap, _, _ = _make_repo(tmp_path)
    real_run = subprocess.run
    seen = {}

    def spy(cmd, *a, **kw):
        if isinstance(cmd, list) and cmd[:2] == ["git", "-C"] and "show" in cmd and cmd[-1].endswith(
                "pytest.ini"):
            return subprocess.CompletedProcess(cmd, 128, b"", b"missing")
        if isinstance(cmd, list) and "pytest" in cmd:
            seen["cmd"] = list(cmd)
            seen["ini"] = Path(cmd[cmd.index("-c") + 1]).read_text("utf-8") if "-c" in cmd else None
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(lfc.subprocess, "run", spy)
    dest = lfc.build_bundle(repo, snap["prs"][0], tmp_path / "b")
    (dest / "plugins/foundry/pytest.toml").write_text("[pytest]\naddopts = '-k nothing'\n", encoding="utf-8")
    try:
        lfc.judge(repo, snap["prs"][0], dest)
    finally:
        lfc.remove_bundle(dest)
    assert "-c" in seen["cmd"] and seen["ini"] == "[pytest]\n"


def test_verify_aborts_on_a_tooling_error_instead_of_replacing_the_task(mini_repo, tmp_path):
    """N-D"""
    repo, snap, _, _ = mini_repo
    task = {**snap["prs"][0], "base_sha": "f" * 40}
    with pytest.raises(lfc.CorpusError):
        lfc.verify_task(repo, task, tmp_path / "work")
    drawn = {"comparison": [1], "screening": [], "replacement_queue": [2]}
    with pytest.raises(lfc.CorpusError):
        lfc.finalize(snap, drawn, lambda pr: lfc.verify_task(repo, task, tmp_path / "work2"))


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
    overrides = json.loads((QUALIFICATION / "pat-19-corpus-classification-overrides-v1.json").read_text("utf-8"))
    snapshot = json.loads(snapshot_path.read_text("utf-8"))
    assert manifest["snapshot_sha256"] == hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    drawn = lfc.draw(snapshot, manifest["seed"], overrides)
    assert manifest["draw"] == drawn
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
        assert task["replayability"]["merge_kind"] == frozen["merge_kind"]
        for key in ("base_sha", "head_sha"):
            assert isinstance(task["replayability"][key]["present_locally"], bool)
            assert isinstance(task["replayability"][key]["origin_refs_containing_count"], int)
            assert "origin_refs_containing" not in task["replayability"][key]  # reproducible (N-G)
    # verification covers all 12 tasks and the manifest lists are the draw recomputed
    chosen = [t["pr"] for t in manifest["comparison"] + manifest["screening"]]
    recorded = {item["pr"]: item for item in manifest["verification"]}
    assert len(set(chosen)) == 12 and set(chosen) <= set(recorded)
    for pr in chosen:
        item = recorded[pr]
        assert item["discarded_reason"] is None
        assert item["with_merged_diff"]["verdict"] == "ACCEPTED"
        assert item["without_merged_diff"]["verdict"] == "REFUSED"
    corpus, _ = lfc.finalize(snapshot, drawn, lambda pr: recorded[pr])
    assert [t["pr"] for t in manifest["comparison"]] == [t["pr"] for t in corpus["comparison"]]
    assert [t["pr"] for t in manifest["screening"]] == [t["pr"] for t in corpus["screening"]]
    assert manifest["discarded_at_verification"] == corpus["discarded_at_verification"]
    if not corpus["discarded_at_verification"]:
        assert [t["pr"] for t in manifest["comparison"]] == drawn["comparison"]
        assert [t["pr"] for t in manifest["screening"]] == drawn["screening"]
    # limits and provenance
    assert any("not independent" in limit for limit in manifest["limits"])
    assert manifest["limits"] == list(lfc.CORPUS_LIMITS)
    assert [o["pr"] for o in drawn["overrides"] if o["decision"] == "include"] == [42]


def test_committed_statements_are_scrubbed_and_are_the_only_tracker_source():
    statements = lfc.load_statements(QUALIFICATION / lfc.STATEMENTS_FILE)
    snapshot = json.loads((QUALIFICATION / "pat-19-corpus-snapshot-v1.json").read_text("utf-8"))
    manifest = json.loads((QUALIFICATION / "pat-19-corpus-manifest-v1.json").read_text("utf-8"))
    assert snapshot["statements"] == {
        "file": lfc.STATEMENTS_FILE, "sha256": statements["sha256"], "captured_on": "2026-10-05",
        "as_of": "capture date, not the base SHA"}
    assert manifest["statements"] == snapshot["statements"]
    tracker = {str(t["pr"]): t["ac_text"] for t in snapshot["prs"] if t["ac_source"] == "tracker_issue"}
    assert tracker == statements["statements"] and len(tracker) == 13
    for name in QUALIFICATION.glob("pat-19-*.json"):
        text = name.read_text("utf-8")
        assert not lfc._LINEAR_URL.search(text) and not lfc._UUID.search(text), name.name
        assert "/Users/" not in text and "/home/" not in text, name.name
