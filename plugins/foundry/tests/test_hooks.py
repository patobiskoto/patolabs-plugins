"""Pure tests for the PreToolUse guard decision — the anti-rules as mechanisms.

Only `deny_reason(command, defaults, current)` is pinned here (pure, no git, no
registry): given a Bash command, the set of protected branch names and the current
branch, allow or deny with a reason. The adversarial matrix covers the bypasses and
false positives found in review: git/gh global flags, force refspecs, quoted text,
--all/--mirror, undeterminable default branch.

PAT-42 adds a second layer, exercised end-to-end via `main()`/subprocess: when
`registry.entry_for()` raises `ValueError` (invalid marker, registry-digest drift,
ambiguous binding), R1-candidate commands must still deny — the one state where
every other Foundry command already fails closed must not become the exception
that reopens `gh pr create/merge` or a direct push to the default branch.
"""
import importlib.util
import json
import os
import subprocess

_GUARD = os.path.join(os.path.dirname(__file__), "..", "hooks", "guard_bash.py")
_spec = importlib.util.spec_from_file_location("guard_bash", _GUARD)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

from foundry import registry  # noqa: E402 — guard's sys.path.insert made this importable

_LINEAR_PROJECT_ID = "00000000-0000-4000-8000-000000000000"
_MANIFEST_DIGEST = "sha256:" + "a" * 64


def _deny(cmd, defaults=frozenset({"main"}), current="feat/x-1"):
    return guard.deny_reason(cmd, set(defaults), current)


def test_gh_pr_create_and_merge_denied():
    assert _deny("gh pr create --title x") is not None
    assert _deny("gh pr merge 12 --squash") is not None
    # global flags between gh and the subcommand don't slip past
    assert _deny("gh -R owner/repo pr merge 5") is not None
    assert _deny("gh --repo owner/repo pr create") is not None


def test_foundry_own_rest_path_allowed():
    assert _deny('gh api -X PUT repos/o/r/pulls/12/merge -f merge_method=squash') is None
    assert _deny("gh pr diff 12") is None
    assert _deny("gh pr view 12") is None


def test_quoted_text_never_trips_the_guard():
    # commit messages, grep patterns, echoed strings — tokenized, not substring-matched
    assert _deny('git commit -m "never use gh pr merge"') is None
    assert _deny("git commit -m 'run git push origin main first'") is None
    assert _deny('grep -rn "git push origin main" skills/') is None
    assert _deny('echo "git push origin main"') is None
    assert _deny("echo git push origin main") is None  # echo's args, not a command


def test_push_to_default_denied_explicit():
    assert _deny("git push origin main") is not None
    assert _deny("git push --force-with-lease origin main") is not None
    assert _deny("git push origin feat/x:main") is not None
    assert _deny("git push origin refs/heads/main") is not None
    assert _deny("git push origin +main") is not None            # force refspec
    assert _deny('git push origin "main"') is not None           # quoted ref
    assert _deny("git push --repo=origin main") is not None      # --repo form
    assert _deny("git push --all origin") is not None            # pushes main too
    assert _deny("git push --mirror origin") is not None


def test_git_global_flags_do_not_bypass():
    assert _deny("git -C /home/user/repo push origin main") is not None
    assert _deny("git --no-pager push origin main") is not None
    assert _deny("git -c user.email=x@y push origin main") is not None
    assert _deny("env git push origin main") is not None
    assert _deny("command git push origin main") is not None
    assert _deny("FOO=1 git push origin main") is not None


def test_push_to_work_branch_allowed():
    assert _deny("git push -u origin feat/x-1-slug") is None
    assert _deny("git push origin HEAD") is None          # current is a work branch
    # a branch merely CONTAINING the default name is not the default
    assert _deny("git push origin main-fix") is None
    assert _deny("git push origin maintenance") is None
    assert _deny("git -C /somewhere push -u origin feat/x") is None


def test_bare_push_depends_on_current_branch():
    assert _deny("git push", current="main") is not None
    assert _deny("git push origin", current="main") is not None
    assert _deny("git push", current="feat/x-1") is None
    assert _deny("git push origin HEAD", current="main") is not None


def test_compound_commands_are_scanned():
    assert _deny("git add -A && git commit -m x && git push origin main") is not None
    assert _deny("git add -A && git push -u origin feat/x") is None
    assert _deny("cd /x; git push origin main") is not None


def test_unknown_default_protects_main_and_master():
    both = frozenset({"main", "master"})
    assert _deny("git push origin master", defaults=both) is not None
    assert _deny("git push origin main", defaults=both) is not None
    assert _deny("git push origin develop", defaults=both) is None


def test_master_as_default():
    assert _deny("git push origin master", defaults=frozenset({"master"})) is not None
    assert _deny("git push origin main", defaults=frozenset({"master"}),
                 current="feat/x") is None


def test_tag_push_allowed_even_from_default_branch():
    # the ship-ios release flow ends with `git push origin "v<version>"` while
    # checked out on the default branch — the guard must let that single-tag
    # push through (only BRANCH destinations are protected)
    assert _deny("git push origin v1.2.3", current="main") is None
    assert _deny('git push origin "v1.2.3"', current="main") is None
    assert _deny("git push origin refs/tags/v1.2.3", current="main") is None
    # …without loosening anything else from the same checkout
    assert _deny("git push origin main", current="main") is not None
    assert _deny("git push", current="main") is not None
    assert _deny("git push --tags", current="main") is not None  # bare push form


def test_session_start_matcher_covers_every_session_entry():
    # Codex/Claude fire SessionStart with source resume too — a resumed session
    # must get the contract injected exactly like a fresh one
    import json
    hooks_json = os.path.join(os.path.dirname(_GUARD), "hooks.json")
    with open(hooks_json) as f:
        matcher = json.load(f)["hooks"]["SessionStart"][0]["matcher"]
    assert set(matcher.split("|")) == {"startup", "resume", "clear", "compact"}


# ---------------------------------------------------------------------------
# PAT-42 — fail-closed on a broken tracker binding, end-to-end through main()
# ---------------------------------------------------------------------------

def _clear_data_env(monkeypatch):
    for key in ("FOUNDRY_DATA", "PLUGIN_DATA", "CLAUDE_PLUGIN_DATA", "PROJECT_REPO"):
        monkeypatch.delenv(key, raising=False)


def _make_repo(tmp_path, name="demo", remote="acme/demo"):
    repo = tmp_path / name
    repo.mkdir()

    def run(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)

    run("init", "-b", "main")
    run("config", "remote.origin.url", f"https://github.com/{remote}.git")
    run("checkout", "-b", "feat/x-1")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit",
        "--allow-empty", "-m", "init")
    return repo


def _linear_binding(canonical_repo):
    identifiers = iter(
        f"00000000-0000-4000-8000-{index:012d}" for index in range(1, 15)
    )
    return {
        "canonical_repo": canonical_repo,
        "team_id": next(identifiers),
        "state_ids": {
            state: next(identifiers)
            for state in (
                "backlog", "ready", "in-progress", "review", "blocked", "done", "dropped",
            )
        },
        "type_label_ids": {
            kind: next(identifiers) for kind in ("Epic", "Feature", "Bug", "Task")
        },
        "milestone_ids": {"M1": next(identifiers)},
        "label_ids": {"pilot": next(identifiers)},
    }


def _hook_env(home):
    """The env Codex gives hook commands: plugin-private dirs set, but the guard
    (like the registry) must ignore them — only HOME/FOUNDRY_DATA matter."""
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "PLUGIN_DATA": str(home / "plugin-private"),
        "CLAUDE_PLUGIN_DATA": str(home / "claude-private"),
    }
    for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _run_guard(repo, command, home):
    r = subprocess.run(
        ["python3", _GUARD],
        input=json.dumps({"cwd": str(repo), "tool_input": {"command": command}}),
        env=_hook_env(home),
        capture_output=True, text=True, check=True,
    )
    return r.stdout


def _decision(stdout):
    if not stdout.strip():
        return None
    return json.loads(stdout)["hookSpecificOutput"]


def _cutover_to_linear(monkeypatch, tmp_path, repo):
    """A valid registered+cutover-over repo — the nominal broken-binding starting
    point every PAT-42 fixture corrupts from here."""
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    registry.register("youtrack", "demo", "FOUNDRY", "0-3")
    extra = _linear_binding("github.com/acme/demo")
    registry.register("linear", "demo", "PAT", _LINEAR_PROJECT_ID, **extra)
    registry.cutover_repository_tracker(
        "linear", "PAT", _LINEAR_PROJECT_ID,
        migration_manifest_digest=_MANIFEST_DIGEST, cwd=str(repo),
    )


def test_nominal_registered_repo_still_denies(monkeypatch, tmp_path):
    repo = _make_repo(tmp_path)
    _cutover_to_linear(monkeypatch, tmp_path, repo)

    decision = _decision(_run_guard(repo, "gh pr create --title x", tmp_path))
    assert decision["permissionDecision"] == "deny"
    decision = _decision(_run_guard(repo, "git push origin main", tmp_path))
    assert decision["permissionDecision"] == "deny"


def test_nominal_unregistered_repo_allows(monkeypatch, tmp_path):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _make_repo(tmp_path)

    assert _decision(_run_guard(repo, "gh pr create --title x", tmp_path)) is None
    assert _decision(_run_guard(repo, "git push origin main", tmp_path)) is None


def test_nominal_non_candidate_command_allowed_when_registered(monkeypatch, tmp_path):
    repo = _make_repo(tmp_path)
    _cutover_to_linear(monkeypatch, tmp_path, repo)

    assert _decision(_run_guard(repo, "git status", tmp_path)) is None
    assert _decision(_run_guard(repo, "gh pr view 12", tmp_path)) is None


def test_invalid_marker_denies_gh_pr_and_default_push(monkeypatch, tmp_path):
    repo = _make_repo(tmp_path)
    _cutover_to_linear(monkeypatch, tmp_path, repo)
    marker = repo / ".foundry" / "tracker.json"
    marker.write_text("{not valid json", encoding="utf-8")

    decision = _decision(_run_guard(repo, "gh pr create --title x", tmp_path))
    assert decision["permissionDecision"] == "deny"
    assert "marqueur tracker de dépôt invalide" in decision["permissionDecisionReason"]

    decision = _decision(_run_guard(repo, "git push origin main", tmp_path))
    assert decision["permissionDecision"] == "deny"
    assert "marqueur tracker de dépôt invalide" in decision["permissionDecisionReason"]


def test_registry_digest_drift_denies_gh_pr_and_default_push(monkeypatch, tmp_path):
    repo = _make_repo(tmp_path)
    _cutover_to_linear(monkeypatch, tmp_path, repo)
    data = registry.load()
    data["linear"]["demo"]["team_id"] = "00000000-0000-4000-8000-000000000099"
    registry._save(data)

    decision = _decision(_run_guard(repo, "gh pr create --title x", tmp_path))
    assert decision["permissionDecision"] == "deny"
    assert "modifié depuis le cutover" in decision["permissionDecisionReason"]

    decision = _decision(_run_guard(repo, "git push origin main", tmp_path))
    assert decision["permissionDecision"] == "deny"
    assert "modifié depuis le cutover" in decision["permissionDecisionReason"]


def test_ambiguous_binding_denies_gh_pr_and_default_push(monkeypatch, tmp_path):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _make_repo(tmp_path)
    registry.register("youtrack", "demo", "FOUNDRY", "0-3",
                       canonical_repo="github.com/acme/demo")
    registry.register("devhub", "other", "OTHER", "42",
                       canonical_repo="github.com/acme/demo")

    decision = _decision(_run_guard(repo, "gh pr create --title x", tmp_path))
    assert decision["permissionDecision"] == "deny"
    assert "ambigus" in decision["permissionDecisionReason"]

    decision = _decision(_run_guard(repo, "git push origin main", tmp_path))
    assert decision["permissionDecision"] == "deny"
    assert "ambigus" in decision["permissionDecisionReason"]


def test_broken_binding_push_to_non_default_branch_still_allowed(monkeypatch, tmp_path):
    """A broken binding fails closed only for what deny_reason() would already
    deny — a push to a work branch (not the default) must stay allowed."""
    repo = _make_repo(tmp_path)
    _cutover_to_linear(monkeypatch, tmp_path, repo)
    marker = repo / ".foundry" / "tracker.json"
    marker.write_text("{not valid json", encoding="utf-8")

    assert _decision(_run_guard(repo, "git push -u origin feat/x-1", tmp_path)) is None


def test_broken_binding_non_candidate_command_allowed(monkeypatch, tmp_path):
    """Non-candidate commands stay allowed even with a broken binding — the guard
    only ever reasons about R1-candidate segments."""
    repo = _make_repo(tmp_path)
    _cutover_to_linear(monkeypatch, tmp_path, repo)
    marker = repo / ".foundry" / "tracker.json"
    marker.write_text("{not valid json", encoding="utf-8")

    assert _decision(_run_guard(repo, "git status", tmp_path)) is None
    assert _decision(_run_guard(repo, "gh pr view 12", tmp_path)) is None


def test_non_candidate_command_never_touches_the_registry(monkeypatch):
    """The cheap `_is_candidate` prefilter must run before any registry read —
    even in a broken-binding repo, `git status`/`gh pr view` never call
    `registry.entry_for` at all."""

    def _boom(*_args, **_kwargs):
        raise AssertionError("non-candidate command touched the registry")

    # `guard.main()` does `from foundry import registry` internally — that binds
    # to the SAME cached module object imported at the top of this file, so
    # patching it here is visible inside main() too.
    monkeypatch.setattr(registry, "entry_for", _boom)
    import io
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(json.dumps({
        "cwd": "/nonexistent", "tool_input": {"command": "git status"},
    })))
    guard.main()  # must not raise / must not print a deny


def test_binding_error_deny_names_the_cause():
    # binding_error_deny_reason() is the pure decision the end-to-end tests above
    # exercise through main() — pin it directly too.
    reason = guard.binding_error_deny_reason(
        "gh pr create --title x", {"main"}, "feat/x-1", "marqueur tracker de dépôt invalide",
    )
    assert reason is not None
    assert "marqueur tracker de dépôt invalide" in reason
    assert "gh pr create/merge" in reason

    assert guard.binding_error_deny_reason(
        "git status", {"main"}, "feat/x-1", "marqueur tracker de dépôt invalide",
    ) is None
