#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — the anti-rules as MECHANISMS.

In a repo registered to Foundry (any tracker), this denies:
- `gh pr create` / `gh pr merge` (the tracker must stay in sync: use the
  foundry:open-pr and foundry:merge-pr skills — their `gh api` calls pass),
- `git push` whose destination is the default branch: explicit refspec
  (`main`, `+main`, `src:main`, `refs/heads/main`, `HEAD` from it),
  `--all`/`--mirror`/`--branches`, or a bare push while the current branch
  IS the default. When the default branch is undeterminable, both `main`
  and `master` are protected.

The command is TOKENIZED (shlex), not substring-matched: quoted text —
commit messages, grep patterns — never trips the guard, and git/gh global
flags (`git -C dir push`, `gh -R o/r pr merge`) can't slip past it.

PAT-215 adds one unconditional denial, independent of the registry: a command invoking
`campaign-approval approve` / `campaign_approval approve` (`approval_deny_reason()`), because
a campaign approval is a human act (docs/campaign-approval.md).

Everything else passes, unregistered repos are untouched, and any internal
error fails OPEN (exit 0): a guard hook must never break normal work — EXCEPT
one case (PAT-42, AGENTS.md#R1): `registry.entry_for()` raising `ValueError`
means the repo's tracker binding is invalid, drifted, or ambiguous — i.e. the
one state where every other Foundry command already fails closed. Failing
open here too would silently drop the R1 guard exactly when it matters, so an
R1-candidate command (`gh pr create/merge`, `git push`) is still denied, naming
the binding error as the cause; a non-candidate command is unaffected (the
cheap `_is_candidate` prefilter runs first and never touches the registry).
A malformed global registry (`registry.json`: invalid JSON or encoding, a
non-object root, a provider or an entry that is not an object, an invalid
`canonical_repo`; PAT-63) raises `registry.RegistryFileError`, a `ValueError`
subclass: the same fail-closed denial, but the reason names the global registry
and its path instead of the repository binding, never file content. Those
commands are then denied in any git checkout with an `origin` remote (an
unregistered repo cannot be proven) until the registry is repaired. The guard
path never reads `config.env`/`$FOUNDRY_CONFIG`, so a non-UTF-8 config file
changes nothing here. A registry that cannot be read at all (OSError) and any
OTHER unexpected failure keep failing OPEN; "no-op outside registered repos"
means a well-formed registry with no entry for the checkout. The pure
`deny_reason()` is what the tests pin. This is a discipline tool, not a sandbox:
`bash -c '…'` indirection is out of scope by design.
"""
import json
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tooling"))

_CONTROL = {";", "&&", "||", "|", "&", "(", ")", "{", "}", "<", ">", ">>", "<<"}
_WRAPPERS = {"env", "command", "nohup", "time", "sudo", "xargs", "stdbuf", "timeout"}
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GIT_VALUE_FLAGS = {"-C", "-c", "--git-dir", "--work-tree", "--exec-path", "--namespace"}
_GH_VALUE_FLAGS = {"-R", "--repo", "--hostname"}
_PUSH_ALL_FLAGS = {"--all", "--mirror", "--branches"}
_PUSH_VALUE_FLAGS = {"-o", "--push-option", "--receive-pack", "--exec"}


def _tokens(command: str) -> list[str]:
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes etc. — degrade to a plain split
        return command.split()


def _segments(tokens):
    """Split a token stream into command segments at shell control operators."""
    seg = []
    for t in tokens:
        if t in _CONTROL:
            if seg:
                yield seg
            seg = []
        else:
            seg.append(t)
    if seg:
        yield seg


def _command_and_args(seg):
    """The (command, args) of a segment, skipping VAR=… assignments, wrapper
    commands (env, xargs…) and the wrappers' own flags."""
    i, wrapped = 0, False
    while i < len(seg):
        t = seg[i]
        if _ASSIGN.match(t) or t in _WRAPPERS:
            wrapped = True
            i += 1
            continue
        if wrapped and t.startswith("-"):
            i += 1
            continue
        return t, seg[i + 1:]
    return None, []


def _after_global_flags(args, value_flags):
    """(subword, rest) after a tool's global flags (skipping their values)."""
    i = 0
    while i < len(args):
        t = args[i]
        if t in value_flags:
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return t, args[i + 1:]
    return None, []


def _gh_pr_denies(args) -> bool:
    sub, rest = _after_global_flags(args, _GH_VALUE_FLAGS)
    if sub != "pr":
        return False
    action, _ = _after_global_flags(rest, _GH_VALUE_FLAGS)
    return action in {"create", "merge"}


def _push_denies(args, defaults: set, current: str) -> bool:
    positionals = []
    i = 0
    while i < len(args):
        t = args[i]
        if t in _PUSH_ALL_FLAGS:
            return True  # --all/--mirror/--branches push the default branch too
        if t in _PUSH_VALUE_FLAGS:
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        positionals.append(t)
        i += 1
    # explicit refspecs: everything after the remote — plus the single positional
    # when there's only one (with --repo=… it's a refspec, not a remote)
    candidates = positionals[1:] if len(positionals) >= 2 else positionals
    for spec in candidates:
        dst = spec.split(":", 1)[-1].lstrip("+")
        dst = dst.removeprefix("refs/heads/")
        if dst in defaults or (dst == "HEAD" and current in defaults):
            return True
    if len(positionals) <= 1:  # bare push (at most a remote): destination = current
        return current in defaults
    return False


_APPROVAL_MODULES = {"campaign-approval", "campaign_approval", "foundry.campaign_approval"}


def _approval_invocation(seg) -> bool:
    """True when the segment *runs* `campaign-approval approve`: the program (after
    wrappers, then `python*` and its flags) is the module or `foundry_cli`, and `approve`
    is its verb.  The words in an argument of another program (grep, git commit...) do not
    match."""
    cmd, args = _command_and_args(seg)
    if cmd is None:
        return False
    chain = [cmd, *args]
    if os.path.basename(cmd).startswith("python"):
        chain = chain[1:]
        while chain and chain[0].startswith("-"):
            chain = chain[1:]
    if not chain:
        return False
    program, rest = os.path.basename(chain[0]).removesuffix(".py"), chain[1:]
    if program == "foundry_cli":
        return len(rest) >= 2 and rest[0] in _APPROVAL_MODULES and rest[1] == "approve"
    return program in _APPROVAL_MODULES and bool(rest) and rest[0] == "approve"


def approval_deny_reason(command: str) -> str | None:
    """PAT-215: `campaign-approval approve` is a human act; deny it in an agent's Bash.

    Independent of any repository binding (no registry read): the approval journal is
    global to the user. Layer 3 of docs/campaign-approval.md; `bash -c` indirection stays
    out of scope, as for R1. PAT-234: only a segment that runs the program is denied."""
    for seg in _segments(_tokens(command)):
        if _approval_invocation(seg):
            return ("campaign-approval approve est un acte humain : lance-le toi-même "
                    "dans un terminal, pas depuis une session d'agent "
                    "(docs/campaign-approval.md).")
    return None


def deny_reason(command: str, defaults: set, current: str) -> str | None:
    """Pure decision: the reason to deny `command`, or None to allow."""
    approval = approval_deny_reason(command)
    if approval:
        return approval
    for seg in _segments(_tokens(command)):
        cmd, args = _command_and_args(seg)
        if cmd == "gh" and _gh_pr_denies(args):
            return ("gh pr create/merge est bloqué dans un repo Foundry : utilise "
                    "foundry:open-pr puis foundry:merge-pr "
                    "(Claude /foundry:<skill> · Codex $foundry:<skill> ; "
                    "gate CI + tracker synchronisé).")
        if cmd == "git":
            sub, rest = _after_global_flags(args, _GIT_VALUE_FLAGS)
            if sub == "push" and _push_denies(rest, defaults, current):
                return (f"push direct vers la branche par défaut "
                        f"({', '.join(sorted(defaults))}) bloqué : le travail atterrit "
                        f"via foundry:open-pr puis foundry:merge-pr.")
    return None


def _is_candidate(command: str) -> bool:
    """Cheap prefilter: does the command even contain a gh-pr or git-push segment?
    Runs BEFORE any registry read or git subprocess — this hook fires on every
    Bash call and must stay near-free on the common path."""
    for seg in _segments(_tokens(command)):
        cmd, args = _command_and_args(seg)
        if cmd == "gh" and _gh_pr_denies(args):
            return True
        if cmd == "git" and _after_global_flags(args, _GIT_VALUE_FLAGS)[0] == "push":
            return True
    return False


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return r.stdout.strip()


def _emit_deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False))


def _defaults_for(registry, cwd) -> set:
    """Best-effort default branch, falling back to {main, master} — including
    when the binding itself is what's broken (registry.default_branch() only
    reads git, never the registry, but stays defensive: a binding error must
    never turn into an unguarded push)."""
    try:
        default = registry.default_branch(cwd)
    except Exception:
        default = None
    return {default} if default else {"main", "master"}


def binding_error_deny_reason(command: str, defaults: set, current: str, cause: str,
                              source: str = "binding") -> str | None:
    """Same decision as `deny_reason`, prefixed with the binding error that put
    the repo in this state — PAT-42: an invalid/drifted/ambiguous tracker
    binding must not silently reopen R1 for gh pr create/merge or a push to
    the default branch. `source` names what is at fault (PAT-63): "binding" for
    the repo's tracker binding, "registry" for the global registry file; the
    cause is a path-bearing message, never file content."""
    reason = deny_reason(command, defaults, current)
    if reason is None:
        return None
    if source == "registry":
        return (f"registre global Foundry illisible ou invalide ({cause}) — {reason} "
                f"Ce n'est pas le binding du dépôt : répare ou restaure le registre "
                f"global avant de continuer.")
    return (f"binding tracker Foundry invalide ou en dérive ({cause}) — {reason} "
            f"Corrige d'abord le binding (registry/.foundry/tracker.json) avant "
            f"de continuer.")


def main():
    try:
        payload = json.load(sys.stdin)
        command = (payload.get("tool_input") or {}).get("command") or ""
        cwd = payload.get("cwd") or os.getcwd()
        if not command:
            return
        if "campaign" in command:
            reason = approval_deny_reason(command)
            if reason:
                _emit_deny(reason)
                return
        if "git" not in command and "gh" not in command:
            return
        if not _is_candidate(command):
            return
        from foundry import registry
        # identity from the repo at cwd itself, across all trackers — never from env
        try:
            entry = registry.entry_for(cwd, use_env=False)
        except ValueError as exc:
            # PAT-42: an invalid marker, a registry-digest drift or an ambiguous
            # binding must fail CLOSED for R1-candidate commands — every other
            # Foundry command already fails closed in this state; the guard must
            # not become the exception that lets `gh pr create/merge` or a push
            # to the default branch slip through.
            defaults = _defaults_for(registry, cwd)
            current = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD") or ""
            source = "registry" if isinstance(exc, registry.RegistryFileError) else "binding"
            reason = binding_error_deny_reason(command, defaults, current, str(exc), source)
            if reason:
                _emit_deny(reason)
            return
        if not entry:
            return
        defaults = _defaults_for(registry, cwd)
        current = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD") or ""
        reason = deny_reason(command, defaults, current)
        if reason:
            _emit_deny(reason)
    except Exception:
        return  # fail open: a broken guard must not block normal work


if __name__ == "__main__":
    main()
