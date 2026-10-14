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


def _commit(root: Path, name: str, text: str, message: str) -> str:
    (root / name).write_text(text, encoding="utf-8")
    _git(root, "add", name)
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


class Scenario:
    def __init__(self, tmp_path: Path, *, quality: str = "mergeable", verdict: str = "pass"):
        self.root = tmp_path / "repo"
        self.root.mkdir()
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
        self.old_head = git_head(self.root)
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
        _git(self.root, "merge", "--no-edit", "main")

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
    # The reviewed content is the PR's own files: the diff bytes are identical.
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
    assert "CONFLICT" in result.stdout, result.stderr + result.stdout
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
    scenario = _clean_integration(tmp_path)
    _git(scenario.root, "checkout", "--orphan", "unrelated")
    _git(scenario.root, "rm", "-rf", ".")
    unrelated = _commit(scenario.root, "z.txt", "z\n", "unrelated root")
    _git(scenario.root, "checkout", "feature")
    with pytest.raises(RoutingConfigError, match="ancêtre"):
        scenario.carry(base=unrelated)


def test_a_wrong_from_base_is_refused(tmp_path):
    scenario = _clean_integration(tmp_path)
    with pytest.raises(RoutingConfigError, match="base d'origine"):
        scenario.carry(from_base=scenario.new_base)


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
    monkeypatch.setattr(
        routing, "git_diff",
        lambda root, base, head="HEAD": original(root, base, head) + (
            b"" if head != "HEAD" else b"x"),
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
