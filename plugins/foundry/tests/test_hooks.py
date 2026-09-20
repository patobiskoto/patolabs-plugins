"""Pure tests for the PreToolUse guard decision — the anti-rules as mechanisms.

Only `deny_reason(command, defaults, current)` is pinned here (pure, no git, no
registry): given a Bash command, the set of protected branch names and the current
branch, allow or deny with a reason. The adversarial matrix covers the bypasses and
false positives found in review: git/gh global flags, force refspecs, quoted text,
--all/--mirror, undeterminable default branch.
"""
import importlib.util
import os

_GUARD = os.path.join(os.path.dirname(__file__), "..", "hooks", "guard_bash.py")
_spec = importlib.util.spec_from_file_location("guard_bash", _GUARD)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


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
