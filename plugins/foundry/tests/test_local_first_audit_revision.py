"""PAT-123: the contamination audit, revision 2 (a protocol after v4), and its offline replay.

Everything is offline and fake: no model, no cloud call, no network, never the real HOME or
``~/.config/foundry`` (the home is a directory of the test). Revision 1 is what ran in v1 to v4 and must
not change: every test that shows a false flag removed also shows revision 1 unchanged."""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from foundry import local_first_runner as lfr
from test_local_first_exploration_runner import PLAN, make_runner
from test_local_first_runner import results

QUALIFICATION = Path(__file__).resolve().parents[1] / "docs" / "qualification"
R2 = lfr.AUDIT_REVISION
HOST = lfr.OBSERVED_CLAUDE_CODE[0]
CASES = 150  # generated sequences of calls per shell and per host behaviour of the property test


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

    def claude(self, *commands, results=None, version=HOST, inputs=None):
        """A Claude Code stream as 2.1.285 writes it: the ``init`` event, each command, then its result. A result
        is the text of a call that returned 0 (default ``ok``), or a mapping: ``text``, ``error`` (the call returned
        non-zero: ``is_error`` and the ``Exit code`` line), ``soft`` (a non-zero status the host accepts),
        ``interrupted``, ``detail`` (False: no detail object)."""
        lines = [{"type": "system", "subtype": "init", "cwd": str(self.bundle),
                  **({"claude_code_version": version} if version else {})}]
        for i, c in enumerate(commands):
            got = (results or {}).get(i, "ok")
            got = {"text": got} if isinstance(got, str) else got
            text = (f"Exit code {got['error']}\n" if got.get("error") else "") + got.get("text", "")
            detail = {"stdout": text, "stderr": "", "interrupted": bool(got.get("interrupted")), "isImage": False,
                      "noOutputExpected": False,
                      **({"returnCodeInterpretation": got["soft"]} if got.get("soft") else {})}
            lines.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Bash",
                 "input": {"command": c, **((inputs or {}).get(i) or {})}}]}})
            lines.append({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": text, "is_error": bool(got.get("error"))}]},
                **({"tool_use_result": f"Error: {text}" if got.get("error") else detail}
                   if got.get("detail", True) else {})})
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


def test_an_omp_stream_starts_every_call_from_every_directory_an_earlier_call_may_have_left(tmp_path):
    lay = Layout(tmp_path)  # nothing is known of the shell of a local harness: the root and where a call may have gone
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


# ------------------------------------------- PAT-128: the quoted form of the absent-path answer (omp ``read``)

def _omp_read_missing(lay, given, text, *, tool="read", error=True):
    events = [{"type": "tool_execution_start", "toolCallId": "c1", "toolName": tool, "args": {"path": given}},
              {"type": "tool_execution_end", "toolCallId": "c1", "toolName": tool,
               "result": {"content": [{"type": "text", "text": text}], "details": {}}, **({"isError": True} if error else {})}]
    lay.stream.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return lay


def test_the_quoted_answer_is_an_absent_path_only_with_the_new_coordinate(tmp_path):
    lay = Layout(tmp_path)
    typo = str(lay.work / "private-a-typo/a/bundle/plugins/foundry/tooling/foundry/frame.py")  # the v5 PR 33 / PR 42 form
    _omp_read_missing(lay, typo, f"Path '{typo}' not found")
    flagged = lay.audit(R2)  # as the frozen protocols count it, byte for byte: the quoted answer is not recognised
    assert any(h.endswith("frame.py") for h in flagged) and any(h.startswith("tool_result:") for h in flagged)
    assert lay.audit(R2, absent_forms=False) == flagged and lay.audit(1) == lay.audit(1, absent_forms=True)
    notes: list[str] = []
    assert lay.audit(R2, absent_forms=True, not_found=notes) == []
    assert [n.replace(str(lay.work.resolve()), "W") for n in notes] == [
        "W/private-a-typo/a/bundle/plugins/foundry/tooling/foundry/frame.py"]


def test_the_quoted_answer_stays_exact_and_the_unquoted_one_is_unchanged(tmp_path):
    lay = Layout(tmp_path)
    given = str(lay.work / "private-b" / "b" / "bundle" / "f.md")
    other = str(lay.work / "private-b")
    for text, error in ((f"Path '{given}' not found", False),  # not an error result
                        (f"Path '{other}' not found", True),  # names another path than the call gave
                        (f"Path '{given}' not found\n{lay.work}/private-b/b/bundle/other.md", True),
                        (f"Path '{given}' not found.", True), (f"path '{given}' not found", True),
                        (f"Path \"{given}\" not found", True), (f"No such file: {given}", True)):
        notes: list[str] = []
        assert _omp_read_missing(lay, given, text, error=error).audit(R2, absent_forms=True, not_found=notes), text
        assert notes == [], text
    # the answer of another call, and a command that names the path, are still audited
    assert lay.omp(f"cat {given}").audit(R2, absent_forms=True)
    # the form ``Path not found: <path>`` is recognised with or without the coordinate
    for forms in (False, True):
        notes = []
        assert _omp_missing(lay, given).audit(R2, absent_forms=forms, not_found=notes) == [] and len(notes) == 1


def test_a_second_access_to_a_path_the_quoted_answer_called_absent_is_still_a_read(tmp_path):
    lay = Layout(tmp_path)
    given = str(lay.work / "private-b" / "b" / "bundle" / "f.md")
    events = [{"type": "tool_execution_start", "toolCallId": "c1", "toolName": "read", "args": {"path": given}},
              {"type": "tool_execution_end", "toolCallId": "c1", "isError": True, "toolName": "read",
               "result": {"content": [{"type": "text", "text": f"Path '{given}' not found"}]}},
              {"type": "tool_execution_start", "toolCallId": "c2", "toolName": "read", "args": {"path": given}},
              {"type": "tool_execution_end", "toolCallId": "c2", "toolName": "read",
               "result": {"content": [{"type": "text", "text": "contents"}]}}]
    lay.stream.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    assert lay.audit(R2, absent_forms=True)


def test_the_coordinate_is_accepted_only_after_v5_and_only_with_revision_2(tmp_path):
    key, value = lfr.ABSENT_FORMS_KEY, lfr.ABSENT_FORMS
    assert (key, value) == ("audit_absent_path_forms", "quoted_path")
    for v in (1, 2, 3, 4):  # the frozen configurations do not carry it and refuse it
        config = json.loads((QUALIFICATION / f"pat-19-campaign-v{v}.json").read_text("utf-8"))
        assert key not in (config.get("isolation") or {}), v
        with pytest.raises(lfr.RunnerError, match="audit_absent_path_forms .* after v5"):
            lfr.load_campaign(_config(tmp_path, v, **{key: value}))
    for name in ("v5", "v5-pilot"):  # v5 and its pilot are pinned without it
        data = json.loads((QUALIFICATION / f"pat-19-campaign-{name}.json").read_text("utf-8"))
        assert key not in data["isolation"], name
        path = tmp_path / f"{name}.json"
        data["isolation"][key] = value
        path.write_text(json.dumps(data), encoding="utf-8")
        (tmp_path / data["exploration"]["ground_truth"]["file"]).write_bytes(
            (QUALIFICATION / data["exploration"]["ground_truth"]["file"]).read_bytes())
        with pytest.raises(lfr.RunnerError, match="after v5"):
            lfr.load_campaign(path)
    good = dict(audit_revision=R2, cloud_native_sandbox=True)
    loaded = lfr.load_campaign(_config(tmp_path, 2, protocol="pat-19-protocol-v6", **good, **{key: value}))
    assert loaded["isolation"][key] == value
    with pytest.raises(lfr.RunnerError, match="audit_absent_path_forms is 'quoted_path'"):
        lfr.load_campaign(_config(tmp_path, 2, protocol="pat-19-protocol-v6", **good, **{key: "other"}))
    with pytest.raises(lfr.RunnerError, match="needs isolation.audit_revision"):
        lfr.load_campaign(_config(tmp_path, 2, protocol="pat-19-protocol-v6", **{key: value}))
    assert lfr.FROZEN_PROTOCOLS == tuple(f"pat-19-protocol-v{v}" for v in (1, 2, 3, 4, 5))


def test_a_runner_applies_the_quoted_form_only_under_the_key_and_says_so_on_the_record(tmp_path):
    plain, _, _, _ = make_runner(tmp_path, "compare_exploration", PLAN,
                                 campaign_over={"isolation": {"audit_revision": R2}})
    (tmp_path / "k").mkdir()
    keyed, _, _, _ = make_runner(tmp_path / "k","compare_exploration", PLAN,
                                 campaign_over={"isolation": {"audit_revision": R2, lfr.ABSENT_FORMS_KEY: lfr.ABSENT_FORMS}})
    assert plain.absent_forms is False and keyed.absent_forms is True
    assert "absent_path_forms" not in plain._audit_note([])["audit"]
    assert keyed._audit_note([])["audit"]["absent_path_forms"] == lfr.ABSENT_FORMS


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
    assert lfr.FROZEN_PROTOCOLS == tuple(f"pat-19-protocol-v{v}" for v in (1, 2, 3, 4, 5))


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
    for protocol in ("pat-19-protocol-v6", "pat-19-protocol-v12"):
        assert lfr.load_campaign(_config(tmp_path, 2, protocol=protocol, audit_revision=R2,
                                          cloud_native_sandbox=True))  # PAT-124: revision 2 needs the native sandbox


def test_a_later_protocol_enables_the_revision_and_only_1_or_2_are_valid(tmp_path):
    loaded = lfr.load_campaign(_config(tmp_path, 2, protocol="pat-19-protocol-v6", audit_revision=R2,
                                         cloud_native_sandbox=True))
    assert loaded["isolation"]["audit_revision"] == R2
    assert lfr.load_campaign(_config(tmp_path, 2, protocol="pat-19-protocol-v6", audit_revision=1))
    for bad in (0, 3, True, "2"):
        with pytest.raises(lfr.RunnerError, match="audit_revision must be"):
            lfr.load_campaign(_config(tmp_path, 2, protocol="pat-19-protocol-v6", audit_revision=bad))


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
    # the barrier (the native sandbox, PAT-124) is not checked by the launcher: the record never reads "confined"
    assert new["audit"] == {"revision": R2, "barrier": "not_verified", "not_found": [],
                            "host_models": ["unverified", "unverified"]}


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
        events = [{"type": "system", "subtype": "init", "cwd": str(bundle), "claude_code_version": HOST}]
        for i, command in enumerate(commands):
            text = (results or {}).get(i, "ok")
            events.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"{session}{i}", "name": "Bash", "input": {"command": command}}]}})
            events.append({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"{session}{i}", "content": text, "is_error": False}]},
                "tool_use_result": {"stdout": text, "stderr": "", "interrupted": False}})
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
    summary = rp.run()["summary"]  # a record flagged by its reviewer only is counted, apart from the arm's flags
    assert summary["flagged_now"] == 0 and summary["reviewer_flagged_now"] == 1
    assert summary["host_models"] == {f"claude-code-{HOST}": 3}
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


def test_the_replay_measures_the_quoted_form_without_changing_the_default_result(tmp_path):
    """PAT-128: a local exploration whose omp ``read`` got the quoted answer is a flag as recorded (and as replayed by default);
    with the measurement on it is an absent path apart; every other record and every field of the default result is the same."""
    rp = Replay(tmp_path)
    name = "attempt-l01-0001-pr1-L-explore-cand"
    bundle = rp.attempt_dir(name) / "bundle"
    bundle.mkdir(parents=True)
    typo = str(rp.work.resolve() / "private-attempt-l01-0001-pr1-L-explore-typo" / "x" / "bundle" / "frame.py")
    events = [{"type": "session", "cwd": str(bundle)},
              {"type": "tool_execution_start", "toolCallId": "c1", "toolName": "read", "args": {"path": typo}},
              {"type": "tool_execution_end", "toolCallId": "c1", "toolName": "read", "isError": True,
               "result": {"content": [{"type": "text", "text": f"Path '{typo}' not found"}]}}]
    (rp.streams / f"cid-{name}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    rp.ledger.append({"kind": "attempt_started", "attempt_dir": name, "path": "L", "pr": 1, "segment": "explore", "attempt": 0})
    rp.records.append({"record_type": "attempt", "campaign_id": "cid", "path": "L", "task": {"pr": 1}, "segment": "explore",
                       "attempt": 0, "outcome": "contaminated", "contaminated": True,
                       "contamination": {"paths": [typo, f"tool_result:{typo}"], "commands": []}})
    rp.cloud(2, "s2", ["ls"])
    rp.record("A", 2, ["s2"], [])
    default, measured = rp.run(), rp.run(absent_forms=True)
    assert "absent_path_forms" not in default and measured["absent_path_forms"] == lfr.ABSENT_FORMS
    assert default == rp.run(absent_forms=False)
    d, m = ({r["pr"]: r for r in out["records"]} for out in (default, measured))
    assert d[1]["new"]["contaminated"] is True and d[1]["new"]["not_found"] == []
    assert m[1]["new"]["contaminated"] is False and m[1]["new"]["hits"] == [] and len(m[1]["new"]["not_found"]) == 1
    assert m[1]["classification"] == "flag_moved_to_not_found" and m[1]["recorded"] == d[1]["recorded"]
    assert m[2] == d[2]  # nothing else moves


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


# ------------------------------- B1: a real shell as the oracle (property: the candidates hold the real directory)

# The host shell is zsh. A machine without it (a CI image may only have bash) SKIPS the zsh half of the two tests
# below, visibly in the test report: the half is then not covered there, never silently dropped.
SHELLS = ["/bin/bash", shutil.which("zsh") or "zsh"]
SHELL_PARAMS = [pytest.param(x, id=Path(x).name, marks=pytest.mark.skipif(
    not os.path.exists(x), reason=f"{Path(x).name} is not installed: this half of the shell oracle is not run here"))
    for x in SHELLS]
_DIRS = ("plugins", "plugins/foundry", "d", "..", "../..", "nope", "plugins/..", ".", "link", "link/..", "up", "/",
         "{B}/plugins", "{W}")
_ARGS = ("../x", "../../x", "../../../x", "../../../../x", "./y", "plugins/../../x", "../../../private-b/x",
         "link/../../../x", "up/x")
WRAPPERS = {  # two ways a host may read the directory a call ended in: whenever its shell ends, or after the script
    "at-exit": 'trap \'pwd -P > "$PWD_OUT"\' EXIT\neval "$PAT_SCRIPT"\n',
    "after-eval": 'eval "$PAT_SCRIPT"\nPAT_STATUS=$?\npwd -P > "$PWD_OUT"\nexit $PAT_STATUS\n'}
HOSTS = [(w, k) for w in WRAPPERS for k in (True, False)]


def _statement(rng):
    d, e, a, b = rng.choice(_DIRS), rng.choice(_DIRS), rng.choice(_ARGS), rng.choice(_ARGS)
    if rng.random() < 0.35:  # the plain forms, where the audit believes a cd: the only place it can flag less
        return rng.choice([f"cd {d}", f"probe {a}", f"cd {d} && probe {a}", f"cd {d}; probe {a}", f"probe {a} | cat",
                           f"cd {d} && probe {a} | cat; probe {b}", "true", "false", f"X={a}; cd {d}; probe $X"]
                          ).replace("{B}", "$B_ROOT_LITERAL").replace("{W}", "$W_ROOT_LITERAL")
    return rng.choice([
        # the forms of the first property test
        f"cd {d}", f"probe {a}", f"cd {d} && probe {a}", f"cd {d} || probe {a}", f"false && cd {d}", f"true && cd {d}",
        f"(cd {d}; probe {a})", f"(cd {d})", f"cd {d} | cat", f"cd {d} & wait", f"if true; then cd {d}; fi",
        f"X=$(cd {d} && pwd)", f"bash -c 'cd {d}'", f"{{ cd {d}; }}", f"cd {d} 2>/dev/null", f"cd {d}\nprobe {a}",
        f"probe {a}; cd {d}; probe {b}", "true",
        # loops and functions: a cd that runs more than once
        f"for i in 1 2; do cd {d}; done", f"for i in 1 2 3; do cd {d}; probe {a}; done",
        f"n=0; while [ $n -lt 2 ]; do cd {d}; n=$((n+1)); done", f"f() {{ cd {d}; }}; f", f"f() {{ cd {d}; }}; f; f",
        f"f() {{ cd {d}; probe \"$1\"; }}; f {a}; f {b}", f"function g {{ cd {d}; }}\ng\ng",
        # a script that stops before its cd, or ends with status 0 before it
        f"set -e; false; cd {d}", f"set -e; cd {d}; false; probe {a}", f"set -e; true; cd {d}", f"exit 0; cd {d}",
        f"exit 1; cd {d}", f"cd {d}; exit 0; cd {e}", f"true || exit; cd {d}", f"false || exit 0; cd {d}",
        f"return 0 2>/dev/null; cd {d}", f"cd {d}; exec true; cd {e}",
        f"echo ${{NOPE?}}; cd {d}", f"echo $((1/0)); cd {d}", f"ls *.nomatch; cd {d}", f"echo =nosuch; cd {d}",
        # child shells and their positional arguments
        f"sh -c 'probe \"$1\"' _ {a}", f"bash -c 'cd {d}; probe \"$1\"' _ {a}", f"sh -c 'cd {d} && probe {a}'",
        f"echo {a} | xargs probe", f"echo {a} | xargs sh -c 'cd {d}; probe \"$1\"' _",
        f"bash <<'E'\ncd {d}\nprobe {a}\nE", f"cat <<E\ncd {d}\nE",
        # options, chains, pipes
        f"cd -P {d}", f"cd -L {d}", f"cd -- {d}", f"cd -P -- {d}", f"cd {d} {e}", "cd -", "cd", "cd ''",
        f"cd {d} && cd {e} || probe {a}", f"false || cd {d}", f"true || cd {d}", f"cd {d} && false || cd {e}",
        f"[ -d {d} ] && cd {d}", f"test -d {d} && cd {d}; probe {a}", f"! cd {d}", f"echo x | cd {d}",
        f"cd {d} | cd {e}", f"true | probe {a}", f"cd {d} && probe {a} | cat; probe {b}",
        # quotes that hide an operator, line continuations, comments
        f"echo \"a; cd {d}\"", f"echo 'x && cd {d}'; probe {a}", f"cd \"{d}\"", f"cd '{d}' ; probe \"{a}\"",
        f"echo \"(\" ; cd {d}", f"echo \\; cd {d}", f"echo \"#\"; cd {d}", f"cd \\\n{d}", f"cd {d} &&\\\n probe {a}",
        f"cd {d} \\\n&& probe {a}", f"cd {d} # cd {e}", f"# cd {d}\nprobe {a}", f"echo $'it\\'s'; cd {d}; echo 'x'",
        # variables, a deferred path, hidden movers
        f"X={a}; cd {d}; probe $X", f"D={d}; cd $D", "cd $NOPE", f"pushd {d}; popd", f"pushd {d}",
        f"eval \"cd {d}\"", f"eval 'cd {d}; probe {a}'", f"x=cd; $x {d}", f"command cd {d}", f"builtin cd {d}",
        f"export V=1; cd {d}", "CDPATH=..; cd plugins", f"export CDPATH=plugins; cd foundry; probe {a}",
        f"alias k=cd\nk {d}", "set -o physical 2>/dev/null; cd link; cd ..",
        # a fresh temporary directory
        f"T=$(mktemp -d); cd $T; probe {a}", "T=$(mktemp -d) && cd \"$T\" && probe y", f"T=/; cd $T; probe {a}",
        f"T=$(mktemp -d); T={d}; cd $T", "T=$(mktemp -d); mkdir -p $T/s; cd $T/s; probe ../../../x",
    ]).replace("{B}", "$B_ROOT_LITERAL").replace("{W}", "$W_ROOT_LITERAL")


def _oracle(lay, calls, shell, host, rng):
    """Run the calls in a real shell and return ``(paths probe was given, results, real start of each call)``.

    OBSERVED from the shell: what runs, in which directory, with which status and messages. ENCODED, since no host
    is run (H1, H2): the command goes through ``eval``; the next call starts in the physical directory the host
    read at the end of the call, when that is inside the project, and at the root with ``Shell cwd was reset to
    ...`` otherwise; ``host`` picks one of four behaviours the audit must hold under: the directory is read
    whenever the shell ends or only when the script comes back to the wrapper (``WRAPPERS``), and it is kept, or
    not, after a non-zero status; a non-zero status is an error result or, sometimes for status 1, an accepted
    one (``returnCodeInterpretation``)."""
    wrapper, keep_after_failure = WRAPPERS[host[0]], host[1]
    cwd, paths, results, starts = lay.bundle.resolve(), set(), {}, []
    probe, final = lay.tmp / "probe.out", lay.tmp / "pwd.out"
    env = {"PATH": f"{lay.tmp / 'bin'}:/usr/bin:/bin", "HOME": str(lay.home), "TMPDIR": str(lay.tmp / "fresh"),
           "PROBE_OUT": str(probe), "PWD_OUT": str(final)}
    argv = [shell, "-f", "-c", wrapper] if shell.endswith("zsh") else [shell, "--noprofile", "--norc", "-c", wrapper]
    for i, line in enumerate(calls):
        starts.append(cwd)
        probe.write_text("", encoding="utf-8")
        final.write_text("", encoding="utf-8")
        run = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
                             env={**env, "PAT_SCRIPT": line})
        paths |= {os.path.realpath(x) for x in probe.read_text("utf-8").splitlines()}
        text = (run.stdout + run.stderr) or "ok"
        ended = final.read_text("utf-8").strip()
        if ended and (run.returncode == 0 or keep_after_failure):
            end = Path(os.path.realpath(ended))
            if end == lay.bundle.resolve() or lay.bundle.resolve() in end.parents:
                cwd = end
            else:
                cwd = lay.bundle.resolve()
                text += f"\nShell cwd was reset to {lay.bundle}"
        if run.returncode == 0:
            results[i] = text
        elif run.returncode == 1 and rng.random() < 0.3:
            results[i] = {"text": text, "soft": "No matches found"}
        else:
            results[i] = {"text": text, "error": run.returncode}
    return paths, results, starts


@pytest.fixture(scope="module")
def shell_layout(tmp_path_factory):
    lay = Layout(tmp_path_factory.mktemp("oracle"))
    for d in ("plugins/foundry", "d"):
        (lay.bundle / d).mkdir(parents=True, exist_ok=True)
    (lay.bundle / "link").symlink_to(lay.bundle / "plugins" / "foundry")  # a symbolic link inside the project
    (lay.bundle / "up").symlink_to(lay.work)  # and one that leaves the zone
    (lay.tmp / "fresh").mkdir()
    (lay.tmp / "bin").mkdir()
    (lay.tmp / "bin" / "probe").write_text(  # a program, as any command of an arm: it resolves from where it runs
        '#!/bin/sh\nfor a in "$@"; do printf \'%s\\n\' "$(pwd -P)/$a" >> "$PROBE_OUT"; done\n', encoding="utf-8")
    (lay.tmp / "bin" / "probe").chmod(0o755)
    (lay.tmp / "bin" / "mktemp").write_text(  # keeps the directories of the generated scripts inside the test tree
        '#!/bin/sh\nexec /usr/bin/mktemp -d "$TMPDIR/tmp.XXXXXX"\n', encoding="utf-8")
    (lay.tmp / "bin" / "mktemp").chmod(0o755)
    return lay


@pytest.mark.parametrize("host", HOSTS, ids=[f"{w}-{'kept' if k else 'not-kept'}-after-failure" for w, k in HOSTS])
@pytest.mark.parametrize("shell", SHELL_PARAMS)
def test_the_candidates_hold_the_real_directory_and_every_real_excursion_is_flagged(shell_layout, shell, host):
    """The invariant of revision 2, against a real bash and a real zsh: the directories the audit starts a call
    from contain the directory the shell really was in, and a path a command really resolved outside the zone is
    flagged (by its name, or by ``UNKNOWN_CWD`` when the audit gave up naming the directory)."""
    lay = shell_layout
    zone = [Path(os.path.realpath(p)) for p in (lay.bundle, lay.scratch, lay.attempt, lay.private)]
    unknown = str(lfr.UNKNOWN_CWD)
    rng = random.Random(123 + 10 * SHELLS.index(shell) + HOSTS.index(host))  # other sequences for each pairing
    stats = {"outside": 0, "named": 0, "clean": 0, "moved": 0, "sure": 0}
    for _ in range(CASES):
        calls = [rng.choice(["\n", "; ", " && "]).join(_statement(rng) for _ in range(rng.randint(1, 3)))
                 .replace("$B_ROOT_LITERAL", str(lay.bundle)).replace("$W_ROOT_LITERAL", str(lay.work))
                 for _ in range(rng.randint(1, 3))]
        paths, results, starts = _oracle(lay, calls, shell, host, rng)
        outside = {p for p in paths if not any(Path(p) == z or z in Path(p).parents for z in zone)}
        trace: list = []
        hits = set(lay.claude(*calls, results=results).audit(R2, trace=trace))
        context = (shell, calls, results)
        assert len(trace) == len(starts), context
        for real, candidates in zip(starts, trace):
            assert unknown in map(str, candidates) or real in {Path(os.path.realpath(c)) for c in candidates}, \
                (context, str(real), sorted(map(str, candidates)))
        shown = {p.replace(str(lay.home.resolve()), "~", 1) for p in outside}
        assert unknown in hits or shown <= hits, (context, sorted(shown), sorted(hits))
        stats["outside"] += bool(outside)
        stats["named"] += bool(outside) and unknown not in hits
        stats["clean"] += not hits
        stats["moved"] += any(s != lay.bundle.resolve() for s in starts)
        stats["sure"] += any(len(c) == 1 and next(iter(c)) != lay.bundle.resolve() for c in trace)
    # the generator reaches outside the zone, the audit does not flag everything nor give up everywhere, the shell
    # does move between calls and the audit does believe some of those moves
    assert stats["outside"] > CASES // 10 and stats["named"] > CASES // 40, stats
    assert stats["clean"] > CASES // 20 and stats["moved"] > CASES // 10 and stats["sure"] > CASES // 50, stats


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


# ------------------- B1bis: the three under-flags of the second review of PAT-123, and their variants

HIDDEN = "cat ../../../tests/test_hidden.py"  # from the bundle: the hidden tests; from plugins/foundry: the bundle's own


def _climb(lay, *calls, **kw):
    """Whether revision 2 names the hidden tests of the work root for these calls."""
    return "W/tests/test_hidden.py" in _shown(lay.claude(*calls, **kw).audit(R2), lay)


STOPPED_BEFORE_THE_CD = {  # first call -> its result: nothing shows that the cd ran
    "set -e, then a failing command": ("set -e; false; cd plugins/foundry", {"error": 1}),
    "exit": ("exit 0; cd plugins/foundry", "ok"),
    "exit in a chain": ("true && exit; cd plugins/foundry", "ok"),
    "return": ("return 0; cd plugins/foundry", "ok"),
    "exec": ("exec true; cd plugins/foundry", "ok"),
    "logout": ("logout; cd plugins/foundry", "ok"),
    "an expansion error": ("echo ${NOPE?}; cd plugins/foundry", {"error": 1, "text": "NOPE: parameter not set"}),
    "a non-zero status": ("cd plugins/foundry; false", {"error": 1}),
    "a non-zero status the host accepts": ("cd plugins/foundry; grep -q x f", {"soft": "No matches found"}),
    "a call cut by the timeout": ("sleep 600; cd plugins/foundry", {"interrupted": True, "text": "Command timed out"}),
    "a result without the host's detail": ("cd plugins/foundry", {"detail": False}),
    "a result too long to be whole": ("cd plugins/foundry", "x" * (lfr._RESULT_WHOLE + 1)),
    "a truncated result": ("cd plugins/foundry", "a\n... [12 lines truncated] ...\nb"),
    "a persisted result": ("cd plugins/foundry", "<persisted-output>\nOutput too large (48KB)"),
    "an Exit code line without the error flag": ("cd plugins/foundry", "Exit code 2\nboom"),
}


@pytest.mark.parametrize("name", sorted(STOPPED_BEFORE_THE_CD))
def test_under_flag_1_a_cd_the_script_may_not_have_reached_is_not_believed(tmp_path, name):
    lay = Layout(tmp_path)
    first, result = STOPPED_BEFORE_THE_CD[name]
    assert _climb(lay, first, HIDDEN, results={0: result}), name


def test_under_flag_1_a_call_in_the_background_or_without_a_result_is_not_believed(tmp_path):
    lay = Layout(tmp_path)
    assert _climb(lay, "cd plugins/foundry", HIDDEN, inputs={0: {"run_in_background": True}})
    lay.claude("cd plugins/foundry", HIDDEN)
    lines = lay.stream.read_text("utf-8").splitlines()
    lay.stream.write_text("\n".join(x for x in lines if '"tool_use_id": "t0"' not in x) + "\n", encoding="utf-8")
    assert "W/tests/test_hidden.py" in _shown(lay.audit(R2), lay)  # the result of the first call is missing
    assert not _climb(lay, "cd plugins/foundry", HIDDEN)  # and the same two calls, the first one proven: the repair


REPEATED_CD = ("for i in 1 2; do cd ..; done", "while [ ! -d .git ]; do cd ..; done",
               "until [ -d .git ]; do cd ..; done", "up() { cd ..; }; up; up", "function up { cd ..; }\nup\nup",
               "a() { cd ..; b; }; b() { cd ..; }; a", "for d in .. ..; do cd $d; done", "repeat 2 cd ..",
               "for i in 1 2; do (true); cd ..; done", "up() { cd ..; }\nup && up")


@pytest.mark.parametrize("second", REPEATED_CD)
def test_under_flag_2_a_cd_that_may_run_more_than_once_is_applied_to_a_fixed_point(tmp_path, second):
    lay = Layout(tmp_path)  # plugins/foundry, then two levels up: the bundle again, so the climb is the hidden tests
    hits = _shown(lay.claude("cd plugins/foundry", second, HIDDEN).audit(R2), lay)
    assert "W/tests/test_hidden.py" in hits or str(lfr.UNKNOWN_CWD) in hits, hits
    if "$" not in second and "repeat" not in second:  # a target the audit can read: the directory is named
        trace: list = []
        lay.audit(R2, trace=trace)
        assert {lay.bundle.resolve(), lay.bundle.resolve() / "plugins"} <= set(trace[2]), second


def test_under_flag_2_a_loop_that_goes_down_without_end_gives_up_naming_the_directory(tmp_path):
    lay = Layout(tmp_path)
    trace: list = []
    lay.claude("for d in a b c; do cd sub; done", "cat ./x").audit(R2, trace=trace)
    assert lfr.UNKNOWN_CWD in trace[1]  # sub, sub/sub, ...: past the cap, any directory
    assert lay.audit(R2) == [str(lfr.UNKNOWN_CWD)]


POSITIONAL = ("bash -c 'cat \"$1\"' _ ../../../tests/test_hidden.py",
              "sh -c 'cat $0' ../../../tests/test_hidden.py",
              "env X=1 bash -c 'cat \"$@\"' _ ../../../tests/test_hidden.py",
              "echo ../../../tests/test_hidden.py | xargs cat",
              "find . -name x -exec sh -c 'cat \"$1\"' _ ../../../tests/test_hidden.py \\;",
              "f() { cat \"$1\"; }; f ../../../tests/test_hidden.py",
              "for f in ../../../tests/test_hidden.py; do cat $f; done",
              "set -- ../../../tests/test_hidden.py; cat \"$1\"",
              "X=../../../tests/test_hidden.py; cat $X", "X=../../../tests/test_hidden.py cat",
              "case x in *) cat ../../../tests/test_hidden.py;; esac",
              "[[ -f ../../../tests/test_hidden.py ]] && echo yes")


@pytest.mark.parametrize("command", POSITIONAL)
def test_under_flag_3_a_relative_path_the_walk_does_not_place_is_still_resolved(tmp_path, command):
    lay = Layout(tmp_path)
    assert _climb(lay, command), command
    assert "W/tests/test_hidden.py" in _shown(lay.omp(command).audit(R2), lay), command


def test_under_flag_3_a_path_is_resolved_wherever_the_call_may_use_it_after_naming_it(tmp_path):
    lay = Layout(tmp_path)
    # named in the bundle (the private root: allowed), used one level up (the work root)
    for command in ("X=../../x; cd ..; cat $X", "bash -c 'cd ..; cat \"$1\"' _ ../../x",
                    "f() { cd ..; cat \"$1\"; }; f ../../x", "for f in ../../x; do cd ..; cat $f; done"):
        assert "W/x" in _shown(lay.claude(command).audit(R2), lay), command
    assert lay.claude("X=../../x; cat $X").audit(R2) == []  # the same path, used where it is named
    # the target of a certain cd is used by that cd, where it stands, and nowhere else
    assert lay.claude("cd ../..; ls").audit(R2) == []


# --------------------------------------------- what a cd must be for the audit to believe it (allow-list)

D = "plugins/foundry"
BELIEVED = (f"cd {D}", f"cd {D} && ls", f"cd {D}; ls | head -3", f"cd -P {D}", f"cd -L -- {D}", f"cd '{D}'",
            f"cd \"{D}\"", "cd plugins; cd foundry", f"ls; cd {D}", f"ls -la && true; cd {D}; ls",
            f"set -e; cd {D}; true", f"set -euo pipefail\ncd {D}", f"export X=1; cd {D}", f"X=$HOME; cd {D}",
            f"cd {D} && python3 - <<'E'\nprint(1)\nE", f"cd \\\n{D}", f"cd {D} # then look", f"echo 'a; b' \"c | d\"; cd {D}",
            f"grep -c x f 2>/dev/null >out.txt; cd {D}", f"find . -name '*.py' -exec ls {{}} \\; ; cd {D}",
            f"printf '%s\\n' \"$X\"; cd {D}", f"command -v python3; cd {D}", f"[ -d {D} ]; cd {D}")
NOT_BELIEVED = (f"true && cd {D}", f"cd {D} || true", f"cd {D} | cat", f"cat x | cd {D}", f"cd {D} 2>/dev/null",
                f"X=1 cd {D}", f"cd {D}; exit 0", f"exit 0; cd {D}", f"exec 2>/dev/null; cd {D}", f"({D}; cd {D})",
                f"(cd {D})", f"{{ cd {D}; }}", "cd $D", "cd plugins/*", "cd ~/x", "cd -", "cd", "cd ''", f"cd {D} extra",
                f"command cd {D}", f"builtin cd {D}", f"CDPATH=..; cd {D}", f"export CDPATH=..; cd {D}",
                f"read CDPATH; cd {D}", f"eval true; cd {D}", f"source x.sh; cd {D}", f". ./x.sh; cd {D}",
                f"trap '' INT; cd {D}", f"alias k=ls; cd {D}", f"setopt autocd; cd {D}", f"set -o physical; cd {D}",
                f"shopt -s cdable_vars; cd {D}", f"cd {D} &", f"if true; then cd {D}; fi", f"cd {D}; echo $(date)",
                f"cd {D}; echo `date`", f"$X; cd {D}", f"*; cd {D}", f"pushd {D}", f"cd {D}; popd", f"chdir {D}",
                "cd +1", "cd -- -x", f"disable cd; cd {D}", f"enable -n cd; cd {D}", f"declare -n r=CDPATH; cd {D}",
                f"export \"$N=..\"; cd {D}", f"printf -v CDPATH ..; cd {D}", f"echo \"cd {D}\"; cd {D}",
                f"echo $'a\\'b'; cd {D}; echo 'c'", f"time cd {D}", f"! cd {D}", f"cd {D};; ls", f"cd {D} <<E\nx")


def _starts(lay, *calls, **kw):
    trace: list = []
    lay.claude(*calls, **kw).audit(R2, trace=trace)
    return trace


@pytest.mark.parametrize("first", BELIEVED)
def test_a_cd_of_the_allow_list_with_a_clean_result_replaces_the_candidates(tmp_path, first):
    lay = Layout(tmp_path)
    assert _starts(lay, first, "ls")[1] == {lay.bundle.resolve() / "plugins" / "foundry"}, first


@pytest.mark.parametrize("first", NOT_BELIEVED)
def test_a_cd_outside_the_allow_list_only_adds_candidates(tmp_path, first):
    lay = Layout(tmp_path)
    after = _starts(lay, first, "ls")[1]
    assert lay.bundle.resolve() in after or lfr.UNKNOWN_CWD in after, (first, after)  # never fewer than before


@pytest.mark.parametrize("line", ["(eval):cd:1: no such file or directory: plugins/foundry",
                                  "bash: line 0: cd: plugins/foundry: No such file or directory",
                                  "/bin/sh: 1: cd: can't cd to plugins/foundry", "cd: Aucun fichier ou dossier de ce type",
                                  "cd:cd:1: too many arguments", "x\n  cd: string not in pwd: a\ny"])
def test_any_cd_line_of_the_result_is_a_failed_cd(tmp_path, line):
    lay = Layout(tmp_path)
    assert _climb(lay, f"cd {D}", HIDDEN, results={0: line}), line


def test_a_line_that_only_ends_in_cd_is_not_a_failed_cd(tmp_path):
    lay = Layout(tmp_path)
    for text in ("abcd: x", "src/cd: x", "my-cd: y", "cd.py:12: z"):
        assert not _climb(lay, f"cd {D}", HIDDEN, results={0: text}), text


def test_the_grammar_is_an_allow_list(tmp_path):
    simple = ("ls", "ls -la | head -3; pwd", "a && b || c\nd", "X=1 Y=\"$Z\" cmd arg >out 2>&1", "cat <<'E' | wc -l",
              "echo 'a;b' \"c|d\" e\\;f", "cmd \\\n arg", "a &&\n b", "a |\n b", "echo ${HOME} $1 $? \"$@\"", "> f",
              "find . -exec ls {} \\;", "[ -d x ] && ls", "ls *.py ~/x")
    for script in simple:
        assert lfr._simple_script(script) is not None, script
    not_simple = ("(ls)", "{ ls; }", "ls &", "ls & pwd", "a; ; b", "; a", "a &&", "a | ", "a ;; b", "if x; then y; fi",
                  "for x in a; do y; done", "while x; do y; done", "f() { x; }", "function f { x; }", "! x", "time x",
                  "[[ -d x ]]", "echo $(x)", "echo `x`", "echo ${x:-y}", "echo ${x?}", "echo $((1+1))", "echo $'x'",
                  "echo \"a", "echo 'a", "cat <(x)", "x=(a b)", "T=$(mktemp -d)", "T=\"$(mktemp -d)\" && ls", "echo \"`x`\"", "echo \"$(x)\"", "case x in a) y;; esac", "coproc x", "a |& b &")
    for script in not_simple:
        with pytest.raises(lfr._NotSimple):
            lfr._simple_script(script)
    chains = lfr._simple_script("a && b | c || d; e")
    assert [[(link, [cmd.words[0].text for cmd in pipe]) for link, pipe in chain] for chain in chains] == \
        [[("", ["a"]), ("&&", ["b", "c"]), ("||", ["d"])], [("", ["e"])]]


@pytest.mark.parametrize("shell", SHELL_PARAMS)
def test_every_builtin_and_reserved_word_of_the_installed_shells_is_known(shell):
    """H4: a command name outside ``_SHELL_NAMES`` is taken for a program. Observed, not encoded: the list is read
    from the shell itself (skipped, visibly, for a shell that is not installed)."""
    script = "print -l ${(k)builtins} ${(k)reswords}" if shell.endswith("zsh") else "compgen -b; compgen -k"
    out = subprocess.run([shell, "-f", "-c", script] if shell.endswith("zsh") else [shell, "--norc", "-c", script],
                         capture_output=True, text=True, check=True, env={"PATH": "/usr/bin:/bin"}).stdout.split()
    assert len(out) > 50 and set(out) <= lfr._SHELL_NAMES, sorted(set(out) - lfr._SHELL_NAMES)
    assert {"cd", "chdir", "pushd", "popd", "eval", "source", ".", "exec", "exit", "return", "trap", "alias", "set",
            "setopt", "shopt", "enable", "disable"}.isdisjoint(lfr._INERT)


# --------------------------------------------------------------- the host the audit can assume (H1, H9)

def test_an_unobserved_host_version_carries_no_directory_and_believes_no_cd(tmp_path):
    lay = Layout(tmp_path)
    calls = (f"cd {D}", HIDDEN)
    for version in (None, "2.1.286", "3.0.0", ""):
        models: list = []
        hits = lay.claude(*calls, version=version).audit(R2, host_model=models)
        assert "W/tests/test_hidden.py" in _shown(hits, lay) and models == ["unverified"], version
        assert lay.claude(f"cd {D}; {HIDDEN}", version=version).audit(R2), version  # not even inside one line
        trace: list = []
        lay.claude(*calls, "ls", version=version).audit(R2, trace=trace)
        assert trace[2] == {lay.bundle.resolve(), lay.bundle.resolve() / "plugins" / "foundry"}  # the union
    models = []
    assert lay.claude(*calls).audit(R2, host_model=models) == [] and models == [f"claude-code-{HOST}"]
    models = []
    assert lay.omp(*calls).audit(R2, host_model=models) and models == ["unverified"]
    # two init events that disagree: unverified
    lines = lay.claude(*calls).stream.read_text("utf-8").splitlines()
    other = json.dumps({"type": "system", "subtype": "init", "claude_code_version": "9.9.9"})
    lay.stream.write_text("\n".join([lines[0], other, *lines[1:]]) + "\n", encoding="utf-8")
    assert lay.audit(R2)
    assert lay.audit(1) == lay.claude(*calls, version=None).audit(1)  # revision 1 reads no version


def test_calls_whose_order_of_execution_is_unknown_prove_nothing(tmp_path):
    lay = Layout(tmp_path)
    use = [{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": c}}]}}
        for i, c in enumerate((f"cd {D}", HIDDEN, "ls"))]
    got = [{"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok", "is_error": False}]},
        "tool_use_result": {"interrupted": False}} for i in range(3)]
    init = {"type": "system", "subtype": "init", "claude_code_version": HOST}

    def audit(events):
        lay.stream.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        return _shown(lay.audit(R2), lay)

    assert audit([init, use[0], got[0], use[1], got[1]]) == []  # one after the other: the cd is believed
    assert audit([init, use[0], use[1], got[0], got[1]])  # sent together: it is not
    assert audit([init, use[1], use[0], got[1], got[0]])  # whichever came first in the stream
    assert audit([init, use[2], use[1], got[2], got[1]]) == ["W/tests/test_hidden.py"]  # no cd among them: the root


# ------------------------------------------- a fresh mktemp directory is NOT modelled (decision of PAT-123)

def test_a_cd_into_a_mktemp_directory_is_a_cd_the_audit_cannot_read(tmp_path):
    """``T=$(mktemp -d); cd $T/...``: the directory is fresh, but the audit could not see a symbolic link the arm
    puts in it (``ln -s / $T/r; cd $T/r``), so it is not a known place: every relative path after it is a hit."""
    lay = Layout(tmp_path)
    reset = f"ok\nShell cwd was reset to {lay.bundle}"
    for command in (f"B=$(mktemp -d); git archive HEAD | tar -x -C $B; cd $B/{D} && python3 -m pytest -q tests/x.py",
                    f"cd {lay.bundle} && T=$(mktemp -d) && cd $T && cat ./x", "T=$(mktemp -d); ln -s / $T/r; cd $T/r; cat ./etc/x",
                    "T=/Users/x; cd $T; cat ./y", "T=$(mktemp -d -p /Users/x); cd \"$T\"; cat ./y"):
        assert lay.claude(command, results={0: reset}).audit(R2) == [str(lfr.UNKNOWN_CWD)], command
    assert lay.claude("T=$(mktemp -d); cp -R . $T/; ls $T").audit(R2) == []  # used, never entered: nothing to place
    assert lay.claude("cd $T/x", "cat ../scratch/x", results={0: reset}).audit(R2) == []  # the reset line decides


def test_a_command_that_may_run_outside_the_zone_is_flagged_by_itself(tmp_path):
    """A bare name (``cat y``, ``ls``) is not a path token, so the command is flagged by the directory it may run
    in: the role the flagged ``cd`` target plays in revision 1, kept when the target cannot be read."""
    lay = Layout(tmp_path)
    unknown = [str(lfr.UNKNOWN_CWD)]
    assert lay.claude("cd $X; cat y").audit(R2) == unknown and lay.audit(1) == []  # a hole of revision 1
    assert lay.claude("cd $X").audit(R2) == []  # nothing ran there
    assert lay.claude("cd $X", "ls").audit(R2) == unknown  # the next call may still be there
    assert lay.claude("cd $X", "ls", results={0: f"Shell cwd was reset to {lay.bundle}"}).audit(R2) == []
    assert lay.claude("eval \"$Y\"; ls").audit(R2) == unknown and lay.claude("(cd $X; make)").audit(R2) == unknown
    assert lay.claude("cd ..; ls; cat x").audit(R2) == []  # the attempt directory is allowed
    assert _shown(lay.claude("cd ../../..; ls").audit(R2), lay) == ["W"] == _shown(lay.audit(1), lay)
    assert _shown(lay.claude("cd ..", "cd ..", "cd ..", "ls").audit(R2), lay) == ["W"]  # no reset line: kept


def test_a_cd_word_the_audit_did_not_read_as_a_command_gives_up_the_directory(tmp_path):
    lay = Layout(tmp_path)  # H7: the word may be run out of sight (x=cd; $x .., a string given to eval or a tool)
    for first in ("x=cd; $x plugins", f"git commit -m 'cd elsewhere'; cd {D}", f"echo cd; cd {D}",
                  "printf 'cd ..\\n' | sh", "for w in cd pushd; do :; done"):
        assert lfr.UNKNOWN_CWD in _starts(lay, first, "ls")[1], first
    assert _starts(lay, f"cd {D}; ls abcd cd.py ./cd src/cd", "ls")[1] == {lay.bundle.resolve() / "plugins" / "foundry"}


def test_a_heredoc_that_is_never_closed_proves_nothing(tmp_path):
    lay = Layout(tmp_path)  # the shell takes the rest for text and runs none of it; the audit reads it as commands
    after = _starts(lay, "cat <<E\ncd /\nE; cd plugins", "ls")[1]
    assert lay.bundle.resolve() in after or lfr.UNKNOWN_CWD in after


def test_a_cd_word_in_a_comment_or_a_heredoc_body_is_not_trusted_to_be_text(tmp_path):
    lay = Layout(tmp_path)  # H7: ``echo \\ #; cd ..`` is a cd for the shell and a comment for the reader
    for first in (f"cd {D} # then cd back", f"cd {D}; cat > notes.txt <<'E'\nrun: cd plugins\nE",
                  "echo \\ #; cd ..", f"cd {D}\n# cd .."):
        assert lfr.UNKNOWN_CWD in _starts(lay, first, "ls")[1], first
    assert _starts(lay, f"cd {D}; cat > n.txt <<'E'\nno such word here\nE", "ls")[1] == \
        {lay.bundle.resolve() / "plugins" / "foundry"}


def test_a_symbolic_link_keeps_the_logical_and_the_physical_directory(tmp_path):
    lay = Layout(tmp_path)
    (lay.bundle / "link").symlink_to(lay.bundle / "plugins" / "foundry")
    (lay.bundle / "up").symlink_to(lay.work)
    after = _starts(lay, "cd link; cd ..", "ls")[1]  # the bundle (logical, the default) or plugins (physical, -P)
    assert {lay.bundle.resolve(), lay.bundle.resolve() / "plugins"} <= {Path(os.path.realpath(c)) for c in after}
    assert "W/x" in _shown(lay.claude("cd up; cat ./x").audit(R2), lay)  # a link that leaves the zone
    assert "W/x" in _shown(lay.claude("cat up/x").audit(R2), lay)


# ------------------------- the threat model (maintainer, 2026-10-08): a journal, not a barrier; known limits

def test_print_v_sets_a_variable_by_a_built_name_and_taints_the_cd_that_follows(tmp_path):
    """Round-3 finding B2, hardened because it costs one line: zsh ``print -v NAME`` sets a variable as ``printf
    -v`` does, here ``CDPATH`` under a name the script builds, so the ``cd`` that follows is not believed."""
    lay = Layout(tmp_path)
    evasion = "A=CDP; B=ATH; print -v $A$B ..; cd scratch; cat ../../../tests/test_hidden.py"  # really a/scratch
    assert "W/tests/test_hidden.py" in _shown(lay.claude(evasion).audit(R2), lay)
    for first in ("print -v X y; cd scratch", "print -rv X -- y; cd scratch", "builtin print -v X y; cd scratch"):
        after = _starts(lay, first, "ls")[1]
        assert lfr.UNKNOWN_CWD in after and lay.bundle.resolve() in after, first
    for first in ("print done; cd scratch", "print -r -- \"$X\"; cd scratch", "print -l a b; cd scratch"):
        assert _starts(lay, first, "ls")[1] == {lay.bundle.resolve() / "scratch"}, first  # ordinary work: believed


def test_let_integer_and_float_with_an_expanded_argument_taint_the_cd_that_follows(tmp_path):
    """Round-3 finding B2: an arithmetic evaluation assigns the variables its expression names (``integer X=$E``
    with ``E='CDPATH=5'`` sets ``CDPATH``), so with a ``$`` argument the ``cd`` that follows is not believed."""
    lay = Layout(tmp_path)
    for name in ("let", "integer", "float"):
        evasion = f"E=CDPATH=5; {name} X=$E; cd scratch; cat ../../../tests/test_hidden.py"
        assert "W/tests/test_hidden.py" in _shown(lay.claude(evasion).audit(R2), lay), name
        after = _starts(lay, f"{name} X=$E; cd scratch", "ls")[1]
        assert lfr.UNKNOWN_CWD in after and lay.bundle.resolve() in after, name
    for first in ("integer n=3; cd scratch", "float f=1.5; cd scratch", "let n=1+2; cd scratch"):
        assert _starts(lay, first, "ls")[1] == {lay.bundle.resolve() / "scratch"}, first  # no ``$``: believed


def test_a_directory_moved_under_the_shell_is_a_known_limit_the_audit_does_not_see(tmp_path):
    """KNOWN LIMIT L1 (round-3 finding B1, deliberate evasion, out of scope of PAT-123): NOT a guarantee, the
    behaviour of the audit today, kept here so that a change shows. ``mv`` moves the directory the shell stands
    in: the shell is really in ``<attempt>/d2`` and the climb really reads the hidden tests of the work root (seen
    with zsh 5.9 and bash 3.2), while the audit still resolves from ``bundle/d`` and flags nothing. The barrier
    against this is the native sandbox (PAT-124), not this audit."""
    lay = Layout(tmp_path)
    evasion = "mkdir d; cd d; mv ../d ../../d2; cat ../../../tests/test_hidden.py"
    assert lay.claude(evasion).audit(R2) == []  # the limit: not seen
    assert _starts(lay, evasion, "ls")[1] == {lay.bundle.resolve() / "d"}  # where the audit believes the shell is
    assert not _climb(lay, "mkdir d; cd d; mv ../d ../../d2", "cat ../../../tests/test_hidden.py")  # nor in two calls


def test_a_symbolic_link_removed_after_the_cd_is_a_known_limit_the_audit_does_not_see(tmp_path):
    """KNOWN LIMIT L1 (round-3 finding B1, deliberate evasion, out of scope of PAT-123): NOT a guarantee, the
    behaviour of the audit today, kept here so that a change shows. The audit resolves a link when it reads the
    stream, after the arm ended: a link the arm removed is no longer there, the physical directory (``scratch``,
    one level higher than ``bundle/l``) is not a candidate and the climb, which really reads the hidden tests of
    the work root (seen with zsh 5.9 and bash 3.2), is not flagged. The barrier is the native sandbox (PAT-124)."""
    lay = Layout(tmp_path)
    evasion = "ln -s ../scratch l; cd l; rm ../bundle/l; cat ../../../tests/test_hidden.py"
    assert lay.claude(evasion).audit(R2) == []  # the limit: the link is gone when the audit reads the stream
    (lay.bundle / "l").symlink_to(lay.scratch)
    assert _climb(lay, evasion)  # the same script with the link still there: the physical directory is a candidate


# ----------------------------------- the reviewer's flag on a cut attempt, the report and its listing

@pytest.mark.parametrize("cut", ["refused", "interrupted"])
def test_a_cut_during_a_flagged_implementer_marks_the_arm_once_under_both_revisions(tmp_path, cut):
    """The exception path of ``_tool_error`` when the IMPLEMENTER is cut after its audit: revision 1 reads the
    flags of the sessions (and those the exception carries), revision 2 reads them by role. Both mark the arm's
    attempt ``contaminated`` with the same paths, named once; nothing goes to ``review.contamination``."""
    def run(name, **iso):
        (tmp_path / name).mkdir()
        runner, _, _, tasks = make_runner(tmp_path / name, "compare_exploration",
                                          {**PLAN, "implementer": ["cmd:ls ../../.."], "reviewer": ["PASS"]},
                                          campaign_over={"isolation": {"private_attempt_root": True, **iso}})
        real, carried = runner.cloud_execution, []

        def execution(*args, **kw):  # the implementer ran and was audited (flagged), then the launcher is cut
            got = real(*args, **kw)
            carried.extend(got[0]["contamination"])
            if cut == "refused":  # an exception that carries the audit of the same session (``exc.contamination``)
                raise lfr.ToolsetRefused("refused after the audit", got[0]["contamination"])
            raise KeyboardInterrupt
        runner.cloud_execution = execution
        with pytest.raises(lfr.ToolsetRefused if cut == "refused" else KeyboardInterrupt):
            runner.compare_exploration(tasks[:1], "cand-a", ("A",))
        records = [r for r in results(runner) if r.get("path") == "A" and r.get("segment") == "cloud"]
        assert len(records) == 1 and carried
        # the directories of the two runs differ: compare what is under the work root
        return records[0], [c.split("/work", 1)[-1] for c in carried]

    old, old_carried = run("r1")
    new, new_carried = run("r2", audit_revision=R2)
    for rec, carried in ((old, old_carried), (new, new_carried)):
        assert rec["contaminated"] is True and rec["outcome"] == "contaminated" and rec["accepted"] is None
        named = rec["contamination"]["paths"] + rec["contamination"]["commands"]
        assert named and len(named) == len(set(named)) == len(carried)  # the exception's flags are not added twice
        assert "contamination" not in rec["review"] and "review.contaminated" not in rec["unknown"]
        assert rec["status"] == ("tool_error" if cut == "refused" else "interrupted") and rec["judge"] is None
    assert old_carried == new_carried



def test_a_cut_after_a_flagged_review_keeps_the_flag_on_the_review_under_revision_2(tmp_path):
    def cut(name, **iso):
        (tmp_path / name).mkdir()
        runner, campaign, _, tasks = make_runner(tmp_path / name, "compare_exploration", REVIEWER_PEEKS,
                                                 campaign_over={"isolation": {"private_attempt_root": True, **iso}})
        real = runner._review

        def review(task, patch, label):  # the reviewer ran, was audited (flagged), then the launcher is cut
            real(task, patch, label)
            raise RuntimeError("cut after the review")

        runner._review = review
        with pytest.raises(RuntimeError):
            runner.compare_exploration(tasks[:1], "cand-a", ("A",))
        ledger = [json.loads(x) for x in runner.ledger.path.read_text("utf-8").splitlines()]
        return [r for r in results(runner) if r.get("path") == "A" and r["segment"] == "cloud"][0], \
            lfr.report(campaign, results(runner), ledger)

    old, old_report = cut("r1")
    assert old["contaminated"] is True and old["outcome"] == "contaminated" and "contamination" not in old["review"]
    assert "review_contaminated" not in old_report  # the report of a frozen campaign keeps its keys
    new, new_report = cut("r2", audit_revision=R2)
    assert "contaminated" not in new and new["outcome"] == "interrupted" and new["accepted"] is None
    assert new["review"]["contamination"]["paths"] and new["unknown"]["review.contaminated"].startswith("the reviewer's")
    assert new["judge"]["verdict"] == "ACCEPTED"  # decided: never replayed
    assert new_report["contaminated"] == []
    assert [(r["path"], r["attempt"], r["outcome"], bool(r["paths"])) for r in new_report["review_contaminated"]] == \
        [("A", 0, "interrupted", True)]


def test_the_report_lists_the_records_whose_reviewer_was_flagged(tmp_path):
    (tmp_path / "x").mkdir()
    runner, campaign, _, tasks = make_runner(tmp_path / "x", "compare_exploration", REVIEWER_PEEKS,
                                             campaign_over={"isolation": {"private_attempt_root": True,
                                                                          "audit_revision": R2}})
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    ledger = [json.loads(x) for x in runner.ledger.path.read_text("utf-8").splitlines()]
    report = lfr.report(campaign, results(runner), ledger)
    assert report["contaminated"] == [] and len(report["review_contaminated"]) == 1
    listed = report["review_contaminated"][0]
    assert listed["outcome"] == "review_unreadable" and listed["paths"] and listed["commands"] == []
