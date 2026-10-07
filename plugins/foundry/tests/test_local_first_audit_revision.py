"""PAT-123: the contamination audit, revision 2 (a protocol after v4), and its offline replay.

Everything is offline and fake: no model, no cloud call, no network, never the real HOME or
``~/.config/foundry`` (the home is a directory of the test). Revision 1 is what ran in v1 to v4 and must
not change: every test that shows a false flag removed also shows revision 1 unchanged."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from foundry import local_first_runner as lfr
from test_local_first_exploration_runner import PLAN, make_runner
from test_local_first_runner import results

QUALIFICATION = Path(__file__).resolve().parents[1] / "docs" / "qualification"
R2 = lfr.AUDIT_REVISION


# ------------------------------------------------------------------------------------ the v4 layout

class Layout:
    """``work/private-a/a/{bundle,scratch}`` (PAT-121 private root) and a second attempt next to it."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.work = tmp_path / "work"
        self.private = self.work / "private-a"
        self.attempt = self.private / "a"
        self.bundle, self.scratch = self.attempt / "bundle", self.attempt / "scratch"
        for d in (self.bundle / "plugins" / "foundry" / "tooling" / "foundry" / "trackers",
                  self.bundle / "plugins" / "foundry" / "tests", self.scratch,
                  self.work / "private-b" / "b" / "bundle", self.work / "tests"):
            d.mkdir(parents=True, exist_ok=True)
        self.home = tmp_path / "home"
        (self.home / ".config" / "foundry").mkdir(parents=True, exist_ok=True)
        self.sensitive = [self.home / ".config" / "foundry", self.work]
        self.stream = tmp_path / "stream.jsonl"

    def claude(self, *commands, results=None):
        """A Claude Code stream: each command, then its result (default ``ok``: the command printed no error)."""
        lines = []
        for i, c in enumerate(commands):
            lines.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": c}}]}})
            lines.append({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": (results or {}).get(i, "ok")}]}})
        self.stream.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
        return self

    def omp(self, *commands):
        self.stream.write_text("\n".join(json.dumps({"type": "tool_execution_start", "toolCallId": f"t{i}",
                                                     "toolName": "bash", "args": {"command": c}})
                                         for i, c in enumerate(commands)) + "\n", encoding="utf-8")
        return self

    def audit(self, revision, **kw):
        return lfr.audit_transcript(self.stream, bundle=self.bundle, scratch=self.scratch,
                                    sensitive=self.sensitive, home=str(self.home), attempt_dir=self.attempt,
                                    private_root=self.private, revision=revision, **kw)

    def both(self):
        return self.audit(1), self.audit(R2)


def _shown(items, layout):
    return sorted(i.replace(str(layout.work.resolve()), "W") for i in items)


# ----------------------------------------------------------- AC1: the working directory is followed

def test_a_relative_climb_from_a_subfolder_is_the_bundles_own_file_not_the_work_root(tmp_path):
    lay = Layout(tmp_path)  # the A83 / L27 form: ``../../../tests/...`` from plugins/foundry/tooling/foundry/trackers
    command = "cd plugins/foundry/tooling/foundry/trackers; grep -n x linear.py ../../../tests/test_linear.py"
    old, new = lay.claude(command).both()
    assert _shown(old, lay) == ["W/tests/test_linear.py"]  # the false flag of v4
    assert new == []
    # and across two calls, as L27 did it
    old, new = lay.claude("cd plugins/foundry", "cd tooling/foundry/trackers; sed -n 1,2p ../../../tests/t.py").both()
    assert _shown(old, lay) == ["W/tests/t.py"] and new == []


def test_the_directory_of_a_call_is_kept_for_the_next_one_and_reset_when_it_ends_outside_the_project(tmp_path):
    lay = Layout(tmp_path)
    # A48: call 1 stays in plugins/foundry, call 2 climbs back to the bundle, lists its attempt directory and scratch
    old, new = lay.claude("cd plugins/foundry && ls", "cd ../..; ls ..; ls ../scratch").both()
    assert _shown(old, lay) == ["W", "W/scratch"] and new == []
    # ``cd ..`` ends in the attempt directory, outside the project: Claude Code resets to the bundle and says so in the
    # result, so the next relative path starts in the bundle again
    reset = {0: f"Shell cwd was reset to {lay.bundle}"}
    old, new = lay.claude("cd ..; tail scratch/stderr.log", "cat ../scratch/x", results=reset).both()
    assert new == [] and old == []
    # the reset is real: from the bundle ``../../../x`` is the work root, from the attempt directory it would not be
    assert _shown(lay.claude("cd ..", "cat ../../../x", results=reset).audit(R2), lay) == ["W/x"]
    # without that line the behaviour is not the observed one: both directories are candidates, the stricter one wins
    assert _shown(lay.claude("cd ..", "cat ../../../x").audit(R2), lay) == [str(lay.tmp.resolve()) + "/x", "W/x"]


def test_an_omp_stream_starts_every_call_in_the_bundle(tmp_path):
    lay = Layout(tmp_path)  # nothing is known of the shell of a local harness: no carry-over
    calls = ("cd plugins/foundry", "cat ../../../x")
    assert lay.claude(*calls).audit(R2) == []  # carried: bundle/plugins/foundry/../../.. is the attempt directory
    assert _shown(lay.omp(*calls).audit(R2), lay) == ["W/x"]
    assert _shown(lay.omp(*calls).audit(1), lay) == ["W/x"]


def test_the_raw_token_pass_follows_cd_but_still_sees_a_climb_hidden_in_a_word(tmp_path):
    lay = Layout(tmp_path)
    assert lay.claude("cd plugins/foundry; ls ./../../x").audit(R2) == []
    # an assignment or an option value is read in the walk of the line, in the directory it has there
    for command in ("cd plugins; X=../../../../x; cat $X", "cd plugins; cat --file=../../../../x",
                    "cd plugins; python3 -c \"open('../../../../x')\"", "cd plugins; cat ../../../../x"):
        assert "W/x" in _shown(lay.claude(command).audit(R2), lay), command


def test_the_listed_real_excursions_are_still_flagged_by_revision_2(tmp_path):
    lay = Layout(tmp_path)
    (lay.work / "private-b" / "b" / "bundle" / "x").write_text("t", encoding="utf-8")
    for command in ("ls ../../..",  # the work root, which lists the other attempts
                    "cd ../../..; ls",
                    "cd plugins; cd ../../../..; ls",
                    "cat ../../../private-b/b/bundle/x", f"cat {lay.work}/private-b/b/bundle/x",
                    f"grep -r secret {lay.work}", f"find {lay.work} -name x", "find / -name x",
                    f"cat {lay.work}/tests/test_hidden.py", "cat ../../../tests/test_hidden.py",  # the hidden tests
                    f"cat {lay.work}/truth.json", "cat ~/.config/foundry/registry.json",
                    f"cat {lay.home}/.config/foundry/registry.json", "cd ~/.config; ls",
                    "cat ../../../../x"):  # above the work root
        assert lay.claude(command).audit(R2), command
        assert lay.omp(command).audit(R2), command
    # a deeper persisted directory needs more ``..`` and the extra ones are still flagged
    assert lay.claude("cd plugins/foundry", "cat ../../../../../../x").audit(R2)
    # a later call cannot be rescued by an earlier cd that left the project (reset)
    assert lay.claude("cd ../../..", "ls ../../..").audit(R2)
    # forbidden commands do not depend on the directory
    assert lay.claude("cd plugins", "curl -s http://example.invalid").audit(R2) == ["command:curl"]
    assert lay.claude("cd plugins; git push origin x").audit(R2) == ["command:git push"]


def test_a_single_command_without_cd_is_judged_the_same_by_both_revisions(tmp_path):
    """The revisions differ where a directory is carried or changed (cd, then a relative path); on a line without
    ``cd``, in the first call of a session, they agree, so revision 2 neither hides nor adds anything there."""
    lay = Layout(tmp_path)
    for command in ("ls", "ls ..", "ls ../scratch", "ls ../..", "ls ../../..", "cat ../../x", "cat ../../../x", "ls ./",
                    "cat plugins/foundry/tests/x.py", "cat ~/.config/foundry/x", "find / -name x",
                    "cat ../../../tests/x.py", "sh -c 'cat ../../../x'", "grep -rn y ../../../tests"):
        old, new = lay.claude(command).both()
        assert old == new, command
    assert lay.claude("ls", "ls ..", "ls ../scratch").audit(R2) == []


def test_path_keys_of_a_tool_call_are_resolved_as_before(tmp_path):
    lay = Layout(tmp_path)
    lay.stream.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t", "name": "Read", "input": {"file_path": "../../../tests/x.py"}}]}}) + "\n",
        encoding="utf-8")
    assert lay.both()[0] == lay.both()[1] and lay.both()[0]


# --------------------------------------------------------------- AC2: a path that does not exist

def _omp_missing(lay, given, shown=None, *, error=True, text=None, same_id=True):
    events = [{"type": "tool_execution_start", "toolCallId": "c1", "toolName": "grep",
               "args": {"path": given, "pattern": "x"}},
              {"type": "tool_execution_end", "toolCallId": "c1" if same_id else "c2", "toolName": "grep",
               "result": {"content": [{"type": "text", "text": text or f"Path not found: {shown or given}"}],
                          "details": {}}, **({"isError": True} if error else {})}]
    lay.stream.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return lay


def test_a_path_the_tool_could_not_find_is_recorded_apart_and_is_not_a_read(tmp_path):
    lay = Layout(tmp_path)
    typo = str(lay.work / "private-a-typo/a/bundle/plugins/foundry/tests/fixtures/f.md")  # one character off
    _omp_missing(lay, typo)
    old = lay.audit(1)
    assert any(h.endswith("f.md") for h in old) and any(h.startswith("tool_result:") for h in old)
    notes: list[str] = []
    assert lay.audit(R2, not_found=notes) == []
    assert [n.replace(str(lay.work.resolve()), "W") for n in notes] == ["W/private-a-typo/a/bundle/plugins/foundry/tests/fixtures/f.md"]


def test_only_the_exact_not_found_answer_to_the_same_call_is_excused(tmp_path):
    lay = Layout(tmp_path)
    given = str(lay.work / "private-b" / "b" / "bundle" / "f.md")
    for case in ({"error": False},  # not an error result
                 {"text": f"No such file or directory: {given}"},  # another failure text
                 {"shown": str(lay.work / "private-b")},  # names another path than the call gave
                 {"same_id": False},  # the answer of another call
                 {"text": f"Path not found: {given}\n{lay.work}/private-b/b/bundle/other.md"}):
        notes: list[str] = []
        assert _omp_missing(lay, given, **case).audit(R2, not_found=notes), case
        assert notes == [], case
    # a command that names a missing path is still a read attempt of that path
    assert lay.omp(f"cat {given}").audit(R2)


def test_a_missing_path_does_not_hide_a_second_access_to_the_same_path(tmp_path):
    lay = Layout(tmp_path)
    given = str(lay.work / "private-b" / "b" / "bundle" / "f.md")
    events = [{"type": "tool_execution_start", "toolCallId": "c1", "toolName": "read", "args": {"path": given}},
              {"type": "tool_execution_end", "toolCallId": "c1", "isError": True, "toolName": "read",
               "result": {"content": [{"type": "text", "text": f"Path not found: {given}"}]}},
              {"type": "tool_execution_start", "toolCallId": "c2", "toolName": "read", "args": {"path": given}},
              {"type": "tool_execution_end", "toolCallId": "c2", "toolName": "read",
               "result": {"content": [{"type": "text", "text": "contents"}]}}]
    lay.stream.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    assert lay.audit(R2), "the second call found the file: it is a read"


# ------------------------------------------------------- the configuration key and the frozen configs

def _config(tmp_path, version, protocol=None, **iso):
    """A frozen configuration of the repository with ``isolation.audit_revision`` (and ``protocol``) edited."""
    data = json.loads((QUALIFICATION / f"pat-19-campaign-v{version}.json").read_text("utf-8"))
    spec = (data.get("exploration") or {}).get("ground_truth")
    if spec:
        (tmp_path / spec["file"]).write_bytes((QUALIFICATION / spec["file"]).read_bytes())
    data.setdefault("isolation", {}).update(iso)
    if protocol:
        data["protocol"] = protocol
    path = tmp_path / "c.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_the_frozen_configurations_do_not_carry_the_key_and_refuse_it(tmp_path):
    for v in (1, 2, 3, 4):
        config = json.loads((QUALIFICATION / f"pat-19-campaign-v{v}.json").read_text("utf-8"))
        assert "audit_revision" not in (config.get("isolation") or {}), v
        assert lfr.load_campaign(QUALIFICATION / f"pat-19-campaign-v{v}.json")  # as they load today
        with pytest.raises(lfr.RunnerError, match="audit_revision .* after v4"):
            lfr.load_campaign(_config(tmp_path, v, audit_revision=R2))
    assert lfr.FROZEN_PROTOCOLS == tuple(f"pat-19-protocol-v{v}" for v in (1, 2, 3, 4))


def test_the_revision_is_gated_by_an_allow_list_of_later_protocols(tmp_path):
    for protocol in (None, "", "pat-19-protocol-v4x", "pat-19-protocol-v10x", "other", "pat-19-protocol-v0"):
        path = _config(tmp_path, 2, protocol="x", audit_revision=R2)
        data = json.loads(path.read_text("utf-8"))
        if protocol is None:
            data.pop("protocol")
        else:
            data["protocol"] = protocol
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(lfr.RunnerError, match="after v4"):
            lfr.load_campaign(path)
    for protocol in ("pat-19-protocol-v5", "pat-19-protocol-v12"):
        assert lfr.load_campaign(_config(tmp_path, 2, protocol=protocol, audit_revision=R2))


def test_a_later_protocol_enables_the_revision_and_only_1_or_2_are_valid(tmp_path):
    loaded = lfr.load_campaign(_config(tmp_path, 2, protocol="pat-19-protocol-v5", audit_revision=R2))
    assert loaded["isolation"]["audit_revision"] == R2
    assert lfr.load_campaign(_config(tmp_path, 2, protocol="pat-19-protocol-v5", audit_revision=1))
    for bad in (0, 3, True, "2"):
        with pytest.raises(lfr.RunnerError, match="audit_revision must be"):
            lfr.load_campaign(_config(tmp_path, 2, protocol="pat-19-protocol-v5", audit_revision=bad))


def test_without_the_key_a_runner_keeps_revision_1_and_the_old_patch_capture(tmp_path):
    runner, _, _, _ = make_runner(tmp_path, "compare_exploration", PLAN)
    assert runner.audit_revision == 1 and runner.patch_excludes == lfr._PATCH_EXCLUDES
    assert lfr._PATCH_EXCLUDES_V2[:len(lfr._PATCH_EXCLUDES)] == lfr._PATCH_EXCLUDES  # revision 2 only adds


# --------------------------------------------------- AC3: where `.pytest_cache/` came from

def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", *args], check=True, capture_output=True).stdout


def test_the_capture_forces_ignored_files_in_so_the_tool_caches_reached_the_patch(tmp_path):
    repo = tmp_path / "bundle"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text(".pytest_cache/\n.ruff_cache/\n__pycache__/\n", encoding="utf-8")
    (repo / "m.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    root = _git(repo, "rev-parse", "HEAD").decode().strip()
    for cache in (".pytest_cache", "plugins/foundry/.pytest_cache", ".ruff_cache"):  # what pytest and ruff leave behind
        (repo / cache / "v").mkdir(parents=True)
        (repo / cache / ".gitignore").write_text("*\n", encoding="utf-8")  # pytest and ruff add their own
        (repo / cache / "v" / "lastfailed").write_text("{}", encoding="utf-8")
    (repo / "m.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "new.py").write_text("y = 1\n", encoding="utf-8")
    old = lfr._capture_patch(repo, root).decode()
    assert ".pytest_cache/v/lastfailed" in old and ".ruff_cache/v/lastfailed" in old  # v4: the bulk of the diff
    assert "plugins/foundry/.pytest_cache" in old
    _git(repo, "reset", "-q")
    new = lfr._capture_patch(repo, root, lfr._PATCH_EXCLUDES_V2).decode()
    assert "cache" not in new and "m.py" in new and "new.py" in new  # the arm's own change is kept


def test_a_runner_captures_with_the_caches_excluded_only_under_revision_2(tmp_path):
    (tmp_path / "x").mkdir()
    runner, _, _, _ = make_runner(tmp_path / "x", "compare_exploration", PLAN,
                                  campaign_over={"isolation": {"audit_revision": R2}})
    assert runner.audit_revision == R2 and runner.patch_excludes == lfr._PATCH_EXCLUDES_V2


# ------------------------------------- AC2 at the runner: the reviewer's flag stays on the review

REVIEWER_PEEKS = {**PLAN, "implementer": ["fix"], "reviewer": ["cmd:ls ../../.."]}


def _run_a(tmp_path, name, **iso):
    (tmp_path / name).mkdir()
    runner, _, _, tasks = make_runner(tmp_path / name, "compare_exploration", REVIEWER_PEEKS,
                                      campaign_over={"isolation": {"private_attempt_root": True, **iso}})
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    return [r for r in results(runner) if r["path"] == "A" and r["segment"] == "cloud"][0]


def test_revision_1_marks_the_arm_when_the_reviewer_is_flagged_revision_2_marks_the_review(tmp_path):
    old = _run_a(tmp_path, "r1")
    assert old["contaminated"] is True and old["outcome"] == "contaminated" and "audit" not in old
    assert old["unknown"]["contaminated"].startswith("the reviewer's")
    new = _run_a(tmp_path, "r2", audit_revision=R2)
    assert "contaminated" not in new and new["outcome"] == "review_unreadable" and new["accepted"] is None
    assert new["judge"]["verdict"] == "ACCEPTED"  # the judge's verdict is readable
    assert new["review"]["verdicts"] == ["PASS"]  # so is the review's, which cannot accept on its own
    assert new["review"]["contamination"]["paths"] and new["review"]["contamination"]["commands"] == []
    assert new["unknown"]["review.contaminated"].startswith("the reviewer's")
    assert new["audit"] == {"revision": R2, "not_found": []}


def test_the_arms_own_flag_still_marks_the_attempt_under_revision_2(tmp_path):
    (tmp_path / "x").mkdir()
    runner, _, _, tasks = make_runner(tmp_path / "x", "compare_exploration",
                                      {**PLAN, "implementer": ["cmd:ls ../../.."], "reviewer": ["PASS"]},
                                      campaign_over={"isolation": {"private_attempt_root": True,
                                                                   "audit_revision": R2}})
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    first = [r for r in results(runner) if r["path"] == "A"][0]
    assert first["contaminated"] is True and first["outcome"] == "contaminated" and first["contamination"]["paths"]


# ------------------------------------------------------------------- informative_arms (report defect)

def test_the_report_names_only_the_informative_arms_that_were_played(tmp_path):
    for sub, arms, expected in (("a", ("A", "L"), []), ("b", ("A", "L", "E"), ["E"])):
        (tmp_path / sub).mkdir()
        runner, campaign, _, tasks = make_runner(tmp_path / sub, "compare_exploration",
                                                 {**PLAN, "reviewer": ["PASS"]})
        runner.compare_exploration(tasks[:1], "cand-a", arms)
        ledger = [json.loads(x) for x in runner.ledger.path.read_text("utf-8").splitlines()]
        report = lfr.report(campaign, results(runner), ledger)["exploration_comparison"]
        assert report["informative_arms"] == expected, arms


# ------------------------------------------------------------------------------- the offline replay

def _campaign(tmp_path, *, private=True):
    data = json.loads((QUALIFICATION / "pat-19-campaign-v4.json").read_text("utf-8"))
    data["isolation"]["private_attempt_root"] = private
    return data


class Replay:
    """A finished campaign on disk: records, a ledger and raw streams, as the runner leaves them."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.work = tmp_path / "work"
        self.state = tmp_path / "state"
        self.streams = self.state / "streams"
        self.streams.mkdir(parents=True)
        self.home = tmp_path / "home"
        (self.home / ".config" / "foundry").mkdir(parents=True)
        self.records, self.ledger = [], []
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        (self.repo / "f").write_text("x", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", "base")

    def attempt_dir(self, name):
        return self.work / f"private-{name}" / name

    def cloud(self, pr, session, commands, *, results=None, review=None, recorded=None, name=None):
        name = name or f"attempt-l01-{pr:04d}-pr{pr}-A-implementer0"
        stream = self.streams / f"cid-{session}.jsonl"
        bundle = self.attempt_dir(name) / "bundle"
        bundle.mkdir(parents=True, exist_ok=True)
        events = [{"type": "system", "subtype": "init", "cwd": str(bundle)}]
        for i, command in enumerate(commands):
            events.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"{session}{i}", "name": "Bash", "input": {"command": command}}]}})
            events.append({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"{session}{i}", "content": (results or {}).get(i, "ok")}]}})
        stream.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        return session, recorded

    def record(self, path, pr, sessions, recorded, *, segment="cloud", attempt=0):
        rec = {"record_type": "attempt", "campaign_id": "cid", "path": path, "task": {"pr": pr},
               "segment": segment, "attempt": attempt, "outcome": "contaminated" if recorded else "accepted",
               "cloud_sessions": sessions}
        if recorded:
            rec.update(contaminated=True, contamination={"paths": recorded, "commands": []})
        self.records.append(rec)

    def run(self, **kw):
        return lfr.replay_audit(_campaign(self.tmp), self.records, self.ledger, streams_dir=self.streams,
                                work_root=self.work, repo=self.repo, state_dir=self.state, home=str(self.home), **kw)


def test_the_replay_gives_old_and_new_classification_per_record_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(lfr, "execute_driver", lambda *a, **k: pytest.fail("the replay ran a driver"))
    rp = Replay(tmp_path)
    work = str(rp.work.resolve())
    rp.cloud(1, "s1", ["cd plugins/foundry; grep -n x ../../../tests/t.py"])  # the A83 / L27 false flag
    rp.cloud(2, "s2", ["cd plugins", "ls ../../../.."])  # a real excursion to the work root, after a carried cd
    rp.cloud(3, "s3", ["find / -name x"])
    rp.cloud(4, "s4", ["cd plugins/foundry && ls", "cd ../..; ls ..; ls ../scratch"])  # the A48 false flag
    rp.cloud(5, "s5", ["ls"])
    rp.record("A", 1, ["s1"], [work + "/tests/t.py"])
    rp.record("A", 2, ["s2"], [str(rp.work.resolve().parent)])  # revision 1 restarted in the bundle: one level off
    rp.record("A", 3, ["s3"], ["/"])
    rp.record("A", 4, ["s4"], [work, work + "/scratch"])
    rp.record("A", 5, ["s5"], [])
    rp.record("A", 6, ["s-missing"], [work])
    result = rp.run()
    by_pr = {r["pr"]: r for r in result["records"]}
    assert by_pr[1]["classification"] == "flag_removed" and by_pr[1]["new"]["hits"] == []
    assert by_pr[2]["classification"] == "flag_kept_changed" and by_pr[2]["new"]["hits"] == [work]  # real directory
    assert by_pr[3]["classification"] == "flag_kept" and by_pr[3]["new"]["hits"] == ["/"]
    assert by_pr[4]["classification"] == "flag_removed" and by_pr[4]["old"]["session"] == "arm"
    assert by_pr[4]["old"]["hits"] == sorted([work, work + "/scratch"]) and by_pr[4]["new"]["hits"] == []
    assert all(by_pr[n]["fidelity"] == "match" for n in range(1, 6))
    assert by_pr[5]["classification"] == "clean"
    assert by_pr[6]["classification"] == "unavailable" and "stream missing" in by_pr[6]["unavailable"]  # never clean
    assert result["summary"]["classification"] == {"clean": 1, "flag_kept": 1, "flag_kept_changed": 1, "flag_removed": 2,
                                                   "unavailable": 1}
    assert result["summary"]["flagged_recorded"] == 5 and result["summary"]["flagged_now"] == 2
    assert str(rp.home) not in json.dumps(result)


def test_a_flag_in_the_reviewers_session_is_told_apart_and_a_replay_difference_is_not_hidden(tmp_path):
    rp = Replay(tmp_path)
    work = str(rp.work.resolve())
    rp.cloud(7, "arm", ["ls"], name="attempt-l01-0007-pr7-A-implementer0")
    rp.cloud(7, "rev", ["ls ../../.."], name="attempt-l01-0008-pr7-A-1-review")  # the reviewer climbs to the work root
    rp.record("A", 7, ["arm", "rev"], [work])
    rp.cloud(8, "arm8", ["ls"], name="attempt-l01-0009-pr8-A-implementer0")
    rp.record("A", 8, ["arm8"], [work])  # recorded flag the replay cannot reproduce
    result = {r["pr"]: r for r in rp.run()["records"]}
    assert result[7]["old"]["session"] == "reviewer" and result[7]["new"]["hits"] == []
    assert result[7]["classification"] == "flag_moved_to_review" and result[7]["new"]["reviewer_hits"] == [work]
    assert result[7]["new"]["contaminated"] is False
    assert result[8]["fidelity"] == "mismatch" and result[8]["classification"] == "not_comparable"


def test_the_replay_does_not_copy_a_name_from_the_home(tmp_path):
    rp = Replay(tmp_path)
    secret = rp.home / ".config" / "foundry" / "registry-private-name.json"
    rp.cloud(9, "s9", [f"cat {secret}"])
    rp.record("A", 9, ["s9"], ["~/.config/foundry/registry-private-name.json"])
    result = rp.run()
    text = json.dumps(result)
    assert "registry-private-name" not in text and str(rp.home) not in text
    assert result["records"][0]["new"]["hits"] == ["~/<hidden>"] and result["records"][0]["classification"] == "flag_kept"


def test_the_cli_replays_a_finished_campaign_and_never_overwrites_a_result(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lfr, "execute_driver", lambda *a, **k: pytest.fail("the replay ran a driver"))
    rp = Replay(tmp_path)
    rp.cloud(1, "s1", ["ls"])
    rp.record("A", 1, ["s1"], [])
    spec = json.loads((QUALIFICATION / "pat-19-campaign-v4.json").read_text("utf-8"))["exploration"]["ground_truth"]
    (tmp_path / spec["file"]).write_bytes((QUALIFICATION / spec["file"]).read_bytes())
    (tmp_path / "campaign.json").write_text(json.dumps(_campaign(tmp_path)), encoding="utf-8")
    state = tmp_path / "state"
    (state / "results-cid.jsonl").write_text("\n".join(json.dumps(r) for r in rp.records) + "\n", encoding="utf-8")
    (state / "ledger-cid.jsonl").write_text("", encoding="utf-8")
    out = tmp_path / "out.json"
    argv = ["replay-audit", "--campaign", str(tmp_path / "campaign.json"), "--results", str(state / "results-cid.jsonl"),
            "--streams-dir", str(rp.streams), "--work-root", str(rp.work), "--repo", str(rp.repo),
            "--home", str(rp.home), "--out", str(out)]
    assert lfr.main(argv) == 0
    written = json.loads(out.read_text("utf-8"))
    assert written["schema"] == lfr.REPLAY_SCHEMA and written["summary"]["classification"] == {"clean": 1}
    assert written["results_sha256"] and written["records"][0]["streams"][0]["sha256"]
    assert lfr.main(argv) == 2 and "refused" in capsys.readouterr().err  # the result is not rewritten
    assert json.loads(out.read_text("utf-8")) == written
    assert lfr.main([a for a in argv if a != "--out" and a != str(out)]) == 0  # stdout
    assert "replay_limits" in capsys.readouterr().out


# ------------------------------------------- B1: the audit never trusts a directory it is not sure of

UNSURE_FORMS = {  # each line really leaves the shell in the bundle (or where the next line starts), so ``../../../x``
    # below is the work root, while a tracked ``cd`` would put the audit in a deeper directory
    "subshell": ("(cd plugins/foundry && pytest -q); cat ../../../tests/test_hidden.py", {}),
    "other attempt": ("(cd plugins && ls); cat ../../../private-b/b/bundle/x", {}),
    "failed cd then ;": ("cd nope; cat ../../../x", {0: "cd: no such file or directory: nope"}),
    "failed cd then newline": ("cd nope\ncat ../../../x", {0: "cd: no such file or directory: nope"}),
    "failed cd, error hidden": ("cd nope 2>/dev/null; cat ../../../x", {}),
    "false && cd": ("false && cd plugins/foundry; cat ../../../x", {}),
    "cd || ...": ("cd nope || cat ../../../x", {}),
    "if then cd": ("if true; then cd plugins/foundry; fi; cat ../../../x", {}),
    "substitution": ("X=$(cd plugins/foundry && pwd); cat ../../../x", {}),
    "sh -c": ("bash -c 'cd plugins/foundry'; cat ../../../x", {}),
    "pipe": ("cd plugins/foundry | cat; cat ../../../x", {}),
    "background": ("cd plugins/foundry & cat ../../../x", {}),
    "group": ("{ cd plugins/foundry; }; cat ../../../x", {}),
    "variable target": ("cd $WHERE; cat ../../../x", {}),
    "cd -": ("cd plugins/foundry; cd -; cat ../../../x", {}),
    "pushd": ("pushd plugins/foundry; popd; cat ../../../x", {}),
}


@pytest.mark.parametrize("name", sorted(UNSURE_FORMS))
def test_a_cd_the_audit_cannot_be_sure_of_never_hides_a_climb(tmp_path, name):
    lay = Layout(tmp_path)
    command, results = UNSURE_FORMS[name]
    hits = lay.claude(command, results=results).audit(R2)
    assert hits, name  # the false-negative of the first revision-2 draft: a deeper cwd made the climb land inside


def test_an_uncertain_cd_is_carried_across_calls_as_candidates_not_as_the_deeper_directory(tmp_path):
    lay = Layout(tmp_path)
    assert lay.claude("(cd plugins/foundry && pytest)", "cat ../../../tests/x").audit(R2)
    assert lay.claude("cd nope", "cat ../../../tests/x", results={0: "cd: no such file or directory: nope"}).audit(R2)
    # a certain cd (top level, unconditional, no failure in the result) IS carried: that is the repair
    assert lay.claude("cd plugins/foundry", "cat ../../../tests/x").audit(R2) == []


def test_a_heredoc_body_a_shell_runs_starts_in_any_directory_the_line_went_through(tmp_path):
    lay = Layout(tmp_path)
    command = "cd ../..; bash <<'E'\ncat ../../x\nE"  # the body runs in the private root: ``../../x`` leaves the work root
    assert lay.claude(command).audit(R2)
    assert lay.claude("cd plugins/foundry; bash <<'E'\ncat ../../x\nE").audit(R2) == []  # attempt directory: allowed


def test_the_tool_result_decides_the_directory_when_it_says_so_and_a_spoofed_line_only_adds_strictness(tmp_path):
    lay = Layout(tmp_path)
    reset = {0: f"Shell cwd was reset to {lay.bundle}"}
    assert lay.claude("cd plugins/foundry", "cat ../../../x", results=reset).audit(R2)  # root is the shallowest
    assert lay.claude("cd plugins/foundry", "cat ../../../x").audit(R2) == []


# ------------------------------- B1: a real shell as the oracle (property: revision 2 flags a superset)

PROBE = 'probe() { printf "%s\\n" "$PWD/$1" >> "$PROBE_OUT"; }; trap \'pwd -P > "$PWD_OUT"\' EXIT\n'
_DIRS = ("plugins", "plugins/foundry", "d", "..", "../..", "nope", "plugins/..", ".")
_ARGS = ("../x", "../../x", "../../../x", "../../../../x", "./y", "plugins/../../x", "../../../private-b/x")


def _statement(rng):
    d, a = rng.choice(_DIRS), rng.choice(_ARGS)
    return rng.choice([
        f"cd {d}", f"probe {a}", f"cd {d} && probe {a}", f"cd {d} || probe {a}", f"false && cd {d}", f"true && cd {d}",
        f"(cd {d}; probe {a})", f"(cd {d})", f"cd {d} | cat", f"cd {d} &", f"if true; then cd {d}; fi",
        f"X=$(cd {d} && pwd)", f"bash -c 'cd {d}'", f"{{ cd {d}; }}", f"cd {d} 2>/dev/null", f"cd {d}\nprobe {a}",
        f"probe {a}; cd {d}; probe {a}", "true"])


def _oracle(lay, calls):
    """Run the calls in a real bash from the bundle, the directory kept between calls as Claude Code keeps it, and
    return (the paths ``probe`` was given, resolved, the result text of each call)."""
    import os
    import subprocess as sp
    cwd, paths, texts = lay.bundle, set(), {}
    for i, line in enumerate(calls):
        probe, final = lay.tmp / "probe.out", lay.tmp / "pwd.out"
        probe.write_text("", encoding="utf-8")
        final.write_text(str(cwd), encoding="utf-8")
        run = sp.run(["/bin/bash", "-c", PROBE + line + "\nwait"], cwd=cwd, stdin=sp.DEVNULL, capture_output=True,
                     text=True, env={"PATH": "/usr/bin:/bin", "PROBE_OUT": str(probe), "PWD_OUT": str(final)})
        paths |= {os.path.realpath(x) for x in probe.read_text("utf-8").splitlines()}
        end = Path(os.path.realpath(final.read_text("utf-8").strip()))
        text = run.stdout + run.stderr
        if end == lay.bundle.resolve() or lay.bundle.resolve() in end.parents:
            cwd = end
        else:
            cwd = lay.bundle
            text += f"\nShell cwd was reset to {lay.bundle}"
        texts[i] = text or "ok"
    return paths, texts


def test_revision_2_flags_a_superset_of_what_a_real_shell_does_outside_the_zone(tmp_path):
    import os
    import random
    lay = Layout(tmp_path)
    for d in ("plugins/foundry", "d"):
        (lay.bundle / d).mkdir(parents=True, exist_ok=True)
    zone = [Path(os.path.realpath(p)) for p in (lay.bundle, lay.scratch, lay.attempt, lay.private)]
    rng, checked, outside_total = random.Random(123), 0, 0
    for _ in range(250):
        calls = ["\n".join(_statement(rng) for _ in range(rng.randint(1, 3))) for _ in range(rng.randint(1, 3))]
        paths, texts = _oracle(lay, calls)
        outside = {p for p in paths if not any(Path(p) == z or z in Path(p).parents for z in zone)}
        hits = set(lay.claude(*calls, results=texts).audit(R2))
        assert {str(p) for p in outside} <= hits, (calls, sorted(outside), sorted(hits))
        checked += 1
        outside_total += bool(outside)
    assert checked == 250 and outside_total > 20  # the generator does reach outside the zone


# --------------------------------------------- B2: a flagged reviewer decides nothing, whatever it said

def _flagged_review(tmp_path, name, verdict):
    (tmp_path / name).mkdir()
    runner, _, plan, tasks = make_runner(
        tmp_path / name, "compare_exploration",
        {**PLAN, "implementer": ["fix", "fix", "fix"], "reviewer": ["cmd:ls ../../.."]},
        campaign_over={"isolation": {"private_attempt_root": True, "audit_revision": R2}})
    real = runner._review

    def review(task, patch, label):  # the real (flagged) review, with the verdict the test wants it to have
        got = real(task, patch, label)
        return (verdict, "FINDINGS-FROM-A-FLAGGED-REVIEWER", *got[2:]) if verdict else got

    runner._review = review
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    return runner, plan


@pytest.mark.parametrize("verdict", ["PASS", "BLOCK"])
def test_a_flagged_reviewer_yields_an_undecided_attempt_stops_the_loop_and_passes_no_findings(tmp_path, verdict):
    from test_local_first_exploration_v4 import _feedbacks
    runner, plan = _flagged_review(tmp_path, verdict, verdict)
    first = [r for r in results(runner) if r["path"] == "A" and r["segment"] == "cloud"]
    assert [r["attempt"] for r in first] == [0]  # no corrector round followed
    rec = first[0]
    assert rec["outcome"] == "review_unreadable" and rec["accepted"] is None and "contaminated" not in rec
    assert rec["judge"]["verdict"] == "ACCEPTED" and rec["review"]["verdicts"] == [verdict]  # both readable
    assert rec["review"]["contamination"]["paths"] and rec["unknown"]["review.contaminated"]
    texts_seen = [str(x) for x in _feedbacks(plan)]
    assert not any("FINDINGS-FROM-A-FLAGGED-REVIEWER" in x for x in texts_seen)  # nothing flowed to a corrector
    assert lfr.json.loads(plan.with_name(plan.name + ".count").read_text("utf-8")).get("implementer") == 1


def test_an_unflagged_block_still_feeds_the_corrector_under_revision_2(tmp_path):
    (tmp_path / "u").mkdir()
    runner, _, plan, tasks = make_runner(
        tmp_path / "u", "compare_exploration", {**PLAN, "implementer": ["fix"], "reviewer": ["BLOCK", "PASS"]},
        campaign_over={"isolation": {"private_attempt_root": True, "audit_revision": R2}})
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    rows = [r for r in results(runner) if r["path"] == "A" and r["segment"] == "cloud"]
    assert [r["outcome"] for r in rows][:2] == ["review_block", "accepted"]


# ------------------------------------------------------ replay classes and the scope measurement

def test_replay_classes_tell_a_changed_kept_flag_and_a_not_found_path_apart(tmp_path):
    rp = Replay(tmp_path)
    work = str(rp.work.resolve())
    rp.cloud(1, "k1", ["ls ../../.."])
    rp.record("A", 1, ["k1"], [work])  # identical before and after
    rp.cloud(2, "k2", ["cd plugins", "ls ../../../.."])
    rp.record("A", 2, ["k2"], [str(rp.work.resolve().parent)])  # kept, with another path
    # an exploration whose only access is a path that does not exist (the flag-8 shape)
    name = "attempt-l01-0003-pr3-L-explore-m"
    bundle = rp.attempt_dir(name) / "bundle"
    bundle.mkdir(parents=True)
    typo = str(rp.work / "private-attempt-l01-0003/pr3-typo/bundle/f.md")
    events = [{"type": "session", "cwd": str(bundle)},
              {"type": "tool_execution_start", "toolCallId": "c1", "toolName": "grep", "args": {"path": typo}},
              {"type": "tool_execution_end", "toolCallId": "c1", "toolName": "grep", "isError": True,
               "result": {"content": [{"type": "text", "text": f"Path not found: {typo}"}]}}]
    (rp.streams / f"cid-{name}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    rp.ledger.append({"kind": "attempt_started", "path": "L", "pr": 3, "segment": "explore", "attempt": 0,
                      "attempt_dir": name})
    rp.record("L", 3, [], [typo, "tool_result:" + typo], segment="explore")
    by_pr = {r["pr"]: r for r in rp.run()["records"]}
    assert by_pr[1]["classification"] == "flag_kept"
    assert by_pr[2]["classification"] == "flag_kept_changed"
    assert by_pr[3]["classification"] == "flag_moved_to_not_found" and len(by_pr[3]["new"]["not_found"]) == 1


def test_the_scope_measurement_leaves_the_work_root_out_of_the_sensitive_list(tmp_path):
    rp = Replay(tmp_path)
    work = str(rp.work.resolve())
    rp.cloud(1, "w1", ["cd plugins/foundry; grep x ../../../tests/t.py"])  # only the sensitive work root made this a hit
    rp.record("A", 1, ["w1"], [work + "/tests/t.py"])
    with_root = rp.run()["records"][0]
    without = rp.run(work_root_sensitive=False)["records"][0]
    assert with_root["old"]["hits"] == [work + "/tests/t.py"] and without["old"]["hits"] == []
    assert without["fidelity"] == "mismatch"  # the recorded flag is no longer reproduced: the measurement says so
