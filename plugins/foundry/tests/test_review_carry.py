"""PAT-173 / PAT-ADR-0021: carrying a terminal review proof across a clean integration.

Every case uses a real temporary Git repository and a private state directory; no
network, tracker or user configuration is involved.
"""
import json
import subprocess
from pathlib import Path

import pytest

from foundry.routing import (
    AcceptanceProofStore,
    ReviewDeduplicator,
    RoutingConfigError,
    acceptance_criteria,
    git_diff,
    _git_changed_paths,
    git_head,
    review_diff_hash,
)

REPOSITORY = "owner/repo"
ISSUE = "PAT-173"
BODY = "- [ ] first criterion\n- [ ] second criterion\n"
TOKEN = "c" * 64


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=root, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout.strip()


def _commit_staged(root: Path, message: str) -> str:
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _make_executable(root: Path) -> None:
    (root / "b.txt").chmod(0o755)
    _git(root, "add", "b.txt")


def _commit(root: Path, name: str, text: str, message: str) -> str:
    (root / name).write_text(text, encoding="utf-8")
    _git(root, "add", name)
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


class Scenario:
    def __init__(self, tmp_path: Path, *, quality: str = "mergeable", verdict: str = "pass",
                 pr_change=None, unmerged_base: bool = False,
                 unmerged_edit: tuple[str, str] = ("b.txt", "b changed on main\n")):
        self.root = tmp_path / "repo"
        self.root.mkdir(parents=True)
        self.state = tmp_path / "state"
        _git(self.root, "init", "-b", "main")
        lines = "".join(f"line {n}\n" for n in range(1, 21))
        _commit(self.root, "a.txt", lines, "base a")
        _commit(self.root, "b.txt", "b\n", "base b")
        self.old_base = _commit(self.root, "c.txt", "c\n", "base c")
        _git(self.root, "checkout", "-b", "feature")
        (self.root / "a.txt").write_text(
            "line 1 changed\n" + "".join(f"line {n}\n" for n in range(2, 21)),
            encoding="utf-8",
        )
        _git(self.root, "commit", "-am", "feature change")
        if pr_change is not None:
            pr_change(self.root)
            _git(self.root, "commit", "-m", "feature extra change")
        self.old_head = git_head(self.root)
        if unmerged_base:
            _git(self.root, "checkout", "main")
            self.old_base = _commit(self.root, *unmerged_edit, "main unmerged edit")
            _git(self.root, "checkout", "feature")
        diff = git_diff(self.root, self.old_base)
        self.old_hash = review_diff_hash(diff)
        coordinates = {"root": str(self.root.resolve()), "base": self.old_base}
        ledger = ReviewDeduplicator(REPOSITORY, self.state)
        claim = ledger.claim_git(
            self.old_hash, coordinates=coordinates, claim_attempt_token=TOKEN,
        )
        outcomes = [{**item, "verdict": verdict} for item in acceptance_criteria(BODY)]
        self.proof = AcceptanceProofStore(REPOSITORY, self.state).create(
            issue_id=ISSUE, issue_body=BODY, reviewer_role="reviewer",
            outcomes=outcomes, quality=quality, diff_hash=self.old_hash,
            claim_id=claim.claim_id, root=self.root, base=self.old_base,
            state_dir=self.state,
        )
        self.new_base = self.old_base

    def advance_main(self, name: str, text: str) -> str:
        _git(self.root, "checkout", "main")
        self.new_base = _commit(self.root, name, text, f"main {name}")
        _git(self.root, "checkout", "feature")
        return self.new_base

    def merge_main(self) -> None:
        self.merge_branch("main")

    def merge_branch(self, branch: str) -> None:
        _git(self.root, "merge", "--no-edit", branch)

    def carry(self, *, body: str = BODY, base: str | None = None, from_base: str | None = None):
        return AcceptanceProofStore(REPOSITORY, self.state).carry(
            issue_id=ISSUE, issue_body=body, root=self.root,
            base=base or self.new_base, from_base=from_base or self.old_base,
            from_diff_hash=self.old_hash,
        )

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.state)): path.read_bytes()
            for path in self.state.rglob("*")
            if path.is_file() and path.name != ".ledger.lock"
        }


def _namespace() -> str:
    import hashlib
    return hashlib.sha256(REPOSITORY.encode("utf-8")).hexdigest()


def _clean_integration(tmp_path: Path) -> Scenario:
    scenario = Scenario(tmp_path)
    scenario.advance_main("b.txt", "b changed on main\n")
    scenario.merge_main()
    return scenario


def test_clean_integration_of_other_files_carries_the_proof(tmp_path):
    scenario = _clean_integration(tmp_path)
    result = scenario.carry()

    new_head = git_head(scenario.root)
    new_diff = git_diff(scenario.root, scenario.new_base)
    new_hash = review_diff_hash(new_diff)
    # ``carry`` recomputed the final diff on the frozen head: the bytes are identical.
    assert new_hash == scenario.old_hash
    assert new_head != scenario.old_head
    assert result["diff_hash"] == new_hash
    assert result["carried"] is True
    assert result["carried_from"] == scenario.proof["proof_id"]

    store = AcceptanceProofStore(REPOSITORY, scenario.state)
    proof = store.valid_for_merge(
        issue_id=ISSUE, issue_body=BODY, head=new_head, diff=new_diff,
        base=scenario.new_base,
    )
    assert proof["proof_id"] == result["proof_id"]
    assert proof["carried_from"] == {
        "proof_id": scenario.proof["proof_id"],
        "head": scenario.old_head,
        "base": scenario.old_base,
        "diff_hash": scenario.old_hash,
    }
    ledger = ReviewDeduplicator(REPOSITORY, scenario.state)
    claim = ledger.claim_git(
        new_hash,
        coordinates={"root": str(scenario.root.resolve()), "base": scenario.new_base},
        claim_attempt_token="d" * 64,
    )
    assert (claim.should_run, claim.state) == (False, "completed")
    assert ledger.completed_proof_id(new_hash) == result["proof_id"]
    record = json.loads((ledger.directory / new_hash).read_text(encoding="utf-8"))
    assert [item["from_proof_id"] for item in record["carried"]] == [scenario.proof["proof_id"]]
    assert record["carried"][0]["from_head"] == scenario.old_head
    assert record["carried"][0]["from_coordinates"]["base"] == scenario.old_base


def test_main_changing_a_file_of_the_pr_is_refused(tmp_path):
    scenario = Scenario(tmp_path)
    # A clean, non-overlapping hunk of the very file the PR changed.
    text = "".join(f"line {n}\n" for n in range(1, 21)).replace(
        "line 20\n", "line 20 main\n",
    )
    scenario.advance_main("a.txt", text)
    scenario.merge_main()
    with pytest.raises(RoutingConfigError, match="fichiers"):
        scenario.carry()


def test_a_conflicting_integration_is_refused(tmp_path):
    scenario = Scenario(tmp_path)
    scenario.advance_main("a.txt", "main side\n" + "".join(
        f"line {n}\n" for n in range(2, 21)))
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "merge", "--no-edit", "main"],
        cwd=scenario.root, capture_output=True, text=True,
    )
    assert result.returncode != 0
    # Locale-independent: an unmerged index entry exists (stage 1 to 3), unlike a
    # translated "CONFLICT" message.
    assert _git(scenario.root, "ls-files", "-u"), result.stderr + result.stdout
    (scenario.root / "a.txt").write_text("resolved by hand\n", encoding="utf-8")
    _git(scenario.root, "add", "a.txt")
    _git(scenario.root, "commit", "--no-edit")
    with pytest.raises(RoutingConfigError, match="conflit"):
        scenario.carry()


def test_an_extra_edit_after_the_integration_is_refused(tmp_path):
    scenario = _clean_integration(tmp_path)
    _commit(scenario.root, "d.txt", "slipped in\n", "extra")
    with pytest.raises(RoutingConfigError, match="arbre"):
        scenario.carry()


@pytest.mark.parametrize("kwargs", [
    {"quality": "blocked"},
    {"verdict": "fail"},
])
def test_a_proof_that_is_not_mergeable_all_pass_is_refused(tmp_path, kwargs):
    scenario = Scenario(tmp_path, **kwargs)
    scenario.advance_main("b.txt", "b changed on main\n")
    scenario.merge_main()
    with pytest.raises(RoutingConfigError, match="non mergeable"):
        scenario.carry()


def test_changed_issue_criteria_are_refused(tmp_path):
    scenario = _clean_integration(tmp_path)
    with pytest.raises(RoutingConfigError, match="critères"):
        scenario.carry(body=BODY + "- [ ] a third criterion\n")
    with pytest.raises(RoutingConfigError, match="critères"):
        scenario.carry(body=BODY.replace("first", "rewritten"))


def test_an_old_base_that_is_not_an_ancestor_of_the_new_base_is_refused(tmp_path):
    # Every other condition holds: the new base has the same content as the old one plus a
    # file of its own, and HEAD is the clean integration of it.  Only the ancestry of the
    # old base is wrong (the new base is a sibling commit, not a descendant).
    scenario = Scenario(tmp_path)
    _git(scenario.root, "checkout", "-b", "sibling", f"{scenario.old_base}~1")
    _commit(scenario.root, "c.txt", "c\n", "sibling c")
    sibling = _commit(scenario.root, "d.txt", "d\n", "sibling d")
    _git(scenario.root, "checkout", "feature")
    scenario.merge_branch("sibling")
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", scenario.old_base, sibling],
        cwd=scenario.root,
    ).returncode == 1
    with pytest.raises(RoutingConfigError, match="ancêtre de la nouvelle"):
        scenario.carry(base=sibling)


def test_a_new_base_that_is_not_an_ancestor_of_head_is_refused(tmp_path):
    scenario = Scenario(tmp_path)
    scenario.advance_main("b.txt", "b changed on main\n")  # never merged into the PR
    with pytest.raises(RoutingConfigError, match="n'est pas ancêtre de HEAD"):
        scenario.carry()


def test_an_unchanged_base_is_refused_even_when_head_has_the_same_tree(tmp_path):
    scenario = Scenario(tmp_path)
    _git(scenario.root, "commit", "--allow-empty", "-m", "same tree")
    with pytest.raises(RoutingConfigError, match="rien à reporter"):
        scenario.carry()


def test_a_rebase_onto_the_new_base_with_the_same_tree_is_carried(tmp_path):
    scenario = Scenario(tmp_path)
    scenario.advance_main("b.txt", "b changed on main\n")
    _git(scenario.root, "rebase", "main")
    assert git_head(scenario.root) != scenario.old_head
    result = scenario.carry()
    assert result["carried"] is True
    assert result["diff_hash"] == scenario.old_hash


def test_a_pr_renaming_a_file_is_carried(tmp_path):
    scenario = Scenario(tmp_path, pr_change=lambda root: _git(root, "mv", "b.txt", "b2.txt"))
    scenario.advance_main("c.txt", "c changed on main\n")
    scenario.merge_main()
    assert scenario.carry()["diff_hash"] == scenario.old_hash


def test_main_renaming_a_file_the_pr_changed_is_refused(tmp_path):
    scenario = Scenario(tmp_path)
    _git(scenario.root, "checkout", "main")
    _git(scenario.root, "mv", "a.txt", "renamed.txt")
    scenario.new_base = _commit_staged(scenario.root, "main renames a")
    _git(scenario.root, "checkout", "feature")
    scenario.merge_main()  # git follows the rename: clean integration
    with pytest.raises(RoutingConfigError, match="fichiers"):
        scenario.carry()


def test_a_mode_change_is_carried_and_a_mode_change_on_main_of_a_pr_file_is_refused(tmp_path):
    scenario = Scenario(tmp_path, pr_change=_make_executable)
    scenario.advance_main("c.txt", "c changed on main\n")
    scenario.merge_main()
    assert scenario.carry()["diff_hash"] == scenario.old_hash

    other = Scenario(tmp_path / "other", pr_change=_make_executable)
    _git(other.root, "checkout", "main")
    _make_executable(other.root)
    other.new_base = _commit_staged(other.root, "main chmod b")
    _git(other.root, "checkout", "feature")
    other.merge_main()
    with pytest.raises(RoutingConfigError, match="fichiers"):
        other.carry()


def test_a_submodule_pointer_change_is_carried(tmp_path):
    gitlink = "160000," + "a" * 40 + ",vendor/lib"
    scenario = Scenario(
        tmp_path, pr_change=lambda root: _git(root, "update-index", "--add", "--cacheinfo", gitlink),
    )
    scenario.advance_main("c.txt", "c changed on main\n")
    scenario.merge_main()
    assert b"160000" in git_diff(scenario.root, scenario.new_base)
    assert scenario.carry()["diff_hash"] == scenario.old_hash


def test_a_binary_file_is_carried(tmp_path):
    def binary(root):
        (root / "blob.bin").write_bytes(bytes(range(256)) + b"\x00\xff")
        _git(root, "add", "blob.bin")

    scenario = Scenario(tmp_path, pr_change=binary)
    scenario.advance_main("c.txt", "c changed on main\n")
    scenario.merge_main()
    assert b"GIT binary patch" in git_diff(scenario.root, scenario.new_base)
    assert scenario.carry()["diff_hash"] == scenario.old_hash


def test_an_already_carried_proof_is_carried_again_on_a_later_base(tmp_path):
    scenario = _clean_integration(tmp_path)
    first = scenario.carry()
    first_base = scenario.new_base
    scenario.advance_main("c.txt", "c changed on main\n")
    scenario.merge_main()
    second = scenario.carry(from_base=first_base)
    assert second["carried"] is True
    assert second["carried_from"] == first["proof_id"]
    assert second["proof_id"] != first["proof_id"]
    # The replay of the second carry is idempotent and still checks --from-base.
    assert scenario.carry(from_base=first_base)["carried"] is False
    with pytest.raises(RoutingConfigError, match="base d'origine"):
        scenario.carry(from_base=scenario.new_base)


def test_a_non_utf8_path_does_not_raise_a_decode_error(tmp_path):
    scenario = Scenario(tmp_path)
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=scenario.root, input=b"x\n",
        capture_output=True, check=True,
    ).stdout.decode().strip()
    subprocess.run(
        ["git", "update-index", "--index-info"], cwd=scenario.root, check=True,
        input=f"100644 {blob}\t".encode() + b"\xff-name.txt\n",
    )
    _git(scenario.root, "commit", "-m", "non utf-8 name")
    paths = _git_changed_paths(scenario.root, scenario.old_head, "HEAD")
    assert len(paths) == 1 and next(iter(paths)).endswith("-name.txt")


def test_a_wrong_from_base_is_refused(tmp_path):
    scenario = _clean_integration(tmp_path)
    with pytest.raises(RoutingConfigError, match="base d'origine"):
        scenario.carry(from_base=scenario.new_base)


@pytest.mark.parametrize("key", ["head", "coordinates"])
def test_a_ledger_record_without_head_or_coordinates_is_a_closed_refusal(tmp_path, key):
    # Not a raw KeyError: the binding check refuses first, before any write.
    scenario = _clean_integration(tmp_path)
    marker = ReviewDeduplicator(REPOSITORY, scenario.state).directory / scenario.old_hash
    record = json.loads(marker.read_text(encoding="utf-8"))
    del record[key]
    marker.write_text(json.dumps(record), encoding="utf-8")
    before = scenario.snapshot()
    with pytest.raises(RoutingConfigError):
        scenario.carry()
    assert scenario.snapshot() == before


@pytest.mark.parametrize("key", ["head", "coordinates"])
def test_a_record_losing_head_or_coordinates_after_the_binding_read_is_a_closed_refusal(
        tmp_path, monkeypatch, key):
    # The binding read passed; the key disappears before the ledger re-read under the lock.
    from foundry import routing
    scenario = _clean_integration(tmp_path)
    marker = ReviewDeduplicator(REPOSITORY, scenario.state).directory / scenario.old_hash
    real = routing.review_diff_hash

    def strip_after_diff_check(diff):
        result = real(diff)
        record = json.loads(marker.read_text(encoding="utf-8"))
        record.pop(key, None)
        marker.write_text(json.dumps(record), encoding="utf-8")
        return result

    monkeypatch.setattr(routing, "review_diff_hash", strip_after_diff_check)
    proofs = scenario.snapshot()
    with pytest.raises(RoutingConfigError):
        scenario.carry()
    # Nothing was written besides the test's own edit of the ledger entry.
    after = scenario.snapshot()
    assert {k: v for k, v in after.items() if not k.endswith(scenario.old_hash)} == {
        k: v for k, v in proofs.items() if not k.endswith(scenario.old_hash)}


def test_replay_is_idempotent_and_preserves_every_existing_byte(tmp_path):
    scenario = _clean_integration(tmp_path)
    before = scenario.snapshot()
    first = scenario.carry()
    after_first = scenario.snapshot()
    # (i) the original proof and every other file are byte-unchanged; the only existing
    # file that changes is the ledger entry of the (identical) diff hash, rebound.
    changed = {name for name, data in before.items() if after_first[name] != data}
    assert changed == {f"review-dedup/{_namespace()}/{scenario.old_hash}"}
    assert before[f"acceptance-proofs/{_namespace()}/{scenario.proof['proof_id']}"] == \
        after_first[f"acceptance-proofs/{_namespace()}/{scenario.proof['proof_id']}"]
    assert set(after_first) - set(before) == {
        f"acceptance-proofs/{_namespace()}/{first['proof_id']}"}
    second = scenario.carry()
    assert second["proof_id"] == first["proof_id"]
    assert second["carried"] is False
    assert scenario.snapshot() == after_first


def test_a_changed_diff_hash_is_refused(tmp_path, monkeypatch):
    scenario = _clean_integration(tmp_path)
    real = AcceptanceProofStore  # the new diff is computed by the shared git_diff
    from foundry import routing

    original = routing.git_diff
    new_head = git_head(scenario.root)
    monkeypatch.setattr(
        routing, "git_diff",
        lambda root, base, head="HEAD": original(root, base, head) + (
            b"x" if head == new_head else b""),
    )
    with pytest.raises(RoutingConfigError, match="diff a changé"):
        real(REPOSITORY, scenario.state).carry(
            issue_id=ISSUE, issue_body=BODY, root=scenario.root, base=scenario.new_base,
            from_base=scenario.old_base, from_diff_hash=scenario.old_hash,
        )


def test_cli_carry_review_proof_prints_the_new_proof(tmp_path, monkeypatch, capsys):
    from foundry import routing

    scenario = _clean_integration(tmp_path)

    class _Issue:
        id = ISSUE
        body = BODY

    class _Tracker:
        def resolve_checkout_project(self, _root):
            return None

        def observe_issue(self, _issue):
            return _Issue()

    monkeypatch.setattr("foundry.tracker", lambda cwd=None: _Tracker())
    monkeypatch.setattr(routing.registry, "data_dir", lambda: str(scenario.state))
    routing.main([
        "carry-review-proof", "--issue", ISSUE, "--root", str(scenario.root),
        "--base", scenario.new_base, "--from-base", scenario.old_base,
        "--from-diff-hash", scenario.old_hash, "--repository", REPOSITORY,
    ])
    printed = json.loads(capsys.readouterr().out)
    assert printed["carried"] is True
    assert printed["diff_hash"] == scenario.old_hash


def _scenario_with_unmerged_base(tmp_path: Path) -> Scenario:
    # PAT-288: the PR is reviewed against a base the branch does not contain yet.
    return Scenario(tmp_path, unmerged_base=True)


def test_an_unchanged_base_not_contained_in_the_reviewed_head_is_carried(tmp_path):
    scenario = _scenario_with_unmerged_base(tmp_path)
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", scenario.old_base, scenario.old_head],
        cwd=scenario.root,
    ).returncode == 1
    scenario.merge_main()
    result = scenario.carry()
    assert result["carried"] is True
    new_head = git_head(scenario.root)
    new_diff = git_diff(scenario.root, scenario.new_base)
    assert review_diff_hash(new_diff) == scenario.old_hash
    store = AcceptanceProofStore(REPOSITORY, scenario.state)
    proof = store.valid_for_merge(
        issue_id=ISSUE, issue_body=BODY, head=new_head, diff=new_diff,
        base=scenario.new_base,
    )
    assert proof["carried_from"]["head"] == scenario.old_head


def test_an_unchanged_base_not_contained_is_refused_when_head_is_not_the_clean_merge(tmp_path):
    scenario = _scenario_with_unmerged_base(tmp_path)
    scenario.merge_main()
    (scenario.root / "e.txt").write_text("extra\n", encoding="utf-8")
    _git(scenario.root, "add", "e.txt")
    _git(scenario.root, "commit", "-m", "extra after merge")
    with pytest.raises(RoutingConfigError, match="arbre de HEAD"):
        scenario.carry()


def test_an_unmerged_base_changing_a_file_of_the_pr_is_refused(tmp_path):
    # PAT-290: main changes a file of the PR (clean hunk, no conflict) in the unchanged base.
    text = "".join(f"line {n}\n" for n in range(1, 21)).replace("line 20\n", "line 20 main\n")
    scenario = Scenario(tmp_path, unmerged_base=True, unmerged_edit=("a.txt", text))
    scenario.merge_main()
    # Observed: refused by the diff-hash identity check (the diff of the merged head against
    # the unchanged base now carries main's hunk), not by the "shared paths" check.
    with pytest.raises(RoutingConfigError, match="le diff a changé"):
        scenario.carry()


def test_replaying_a_carry_of_an_unmerged_base_returns_carried_false(tmp_path):
    scenario = _scenario_with_unmerged_base(tmp_path)
    scenario.merge_main()
    first = scenario.carry()
    assert first["carried"] is True
    after_first = scenario.snapshot()
    second = scenario.carry()
    assert second["carried"] is False
    assert second["proof_id"] == first["proof_id"]
    assert scenario.snapshot() == after_first


def test_an_unmerged_base_with_an_unchanged_head_is_refused(tmp_path):
    scenario = _scenario_with_unmerged_base(tmp_path)
    # No integration happened: head is the reviewed head and still lacks the base.
    with pytest.raises(RoutingConfigError, match="n'est pas ancêtre de HEAD"):
        scenario.carry()
