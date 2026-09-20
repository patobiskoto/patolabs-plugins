"""Mechanical execution-loop steps: start / openpr / merge / close-epic.

Thin, predictable wrappers over the tracker + code-host adapters. The one piece
of judgment-free enforcement: `merge` calls the CI gate and REFUSES on non-green.

CLI: python3 -m foundry.issue <start|openpr|merge|close-epic> [ISSUE-ID] [pr#] [flags]
     openpr: the issue id is derived from the branch name when omitted;
             --summary-stdin reads the PR summary from stdin (use `< file`).
     merge:  --allow-no-ci asks to waive the gate; granted ONLY when both CI
             sources (check-runs + legacy statuses) are empty AND no active
             check-suite signals CI for the commit (see write.ci_gate/ADR-0002).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import foundry
from foundry import execution_receipts, registry, write
from foundry.models import TransitionContext
from foundry.routing import (
    AcceptanceProofStore,
    RoutingConfigError,
    git_diff,
    git_head,
    repository_identity,
    review_diff_hash,
)
from foundry.trackers.base import (
    AcceptanceSyncUnavailableError,
    EpicClosureUnavailableError,
    TrackerConflictError,
)

_BRANCH_TYPE = {"Bug": "fix", "Feature": "feat", "Epic": "feat"}


def _observe_receipt(issue_id: str, kind: str, builder) -> str:
    """Run the bounded receipt projection without adding an execution dependency."""
    try:
        receipt = builder()
    except (AttributeError, TypeError, execution_receipts.ReceiptStoreError):
        return "rejected"
    return execution_receipts.observe(issue_id, kind, receipt)


def _sh(*args, check=True):
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"$ {' '.join(args)}\n{r.stderr or r.stdout}")
    return r.stdout.strip()


def _slug(title):
    s = "-".join(title.lower().split()[:5])
    return "".join(c for c in s if c.isalnum() or c == "-").strip("-")


def _is_linked_worktree() -> bool:
    """Whether cwd is a linked worktree rather than the repository's main checkout."""
    git_dir = _sh("git", "rev-parse", "--absolute-git-dir", check=False)
    common = _sh("git", "rev-parse", "--path-format=absolute", "--git-common-dir",
                 check=False)
    if not git_dir or not common:
        return False
    return os.path.realpath(git_dir) != os.path.realpath(common)


def _prepare_branch(branch: str) -> str:
    """Create the issue branch without checking out main inside a linked worktree."""
    current = _sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    if current == branch:
        return "existing"
    existing = _sh("git", "rev-parse", "--verify", f"refs/heads/{branch}", check=False)
    if existing:
        try:
            _sh("git", "checkout", branch)
        except SystemExit:
            raise SystemExit(
                f"⛔ La branche '{branch}' existe déjà dans un autre worktree — "
                "reprends `issue start` depuis ce worktree au lieu d'en créer une seconde."
            ) from None
        return "existing"
    default = _default_branch()
    if _is_linked_worktree():
        # Codex-managed worktrees cannot check out a branch already held by the main
        # checkout. Refresh the remote ref best-effort and branch from it directly.
        _sh("git", "fetch", "origin", default, check=False)
        remote = f"origin/{default}"
        base = remote if _sh("git", "rev-parse", "--verify", remote, check=False) else default
        _sh("git", "checkout", "-b", branch, base)
        return "linked-worktree"
    _sh("git", "checkout", default)
    _sh("git", "pull", "--ff-only", check=False)
    _sh("git", "checkout", "-b", branch)
    return "main-checkout"


def _cleanup_branch(branch: str) -> str:
    """Clean a merged branch when this checkout owns it; Codex owns its worktrees."""
    if _is_linked_worktree():
        return "linked-worktree"
    _sh("git", "checkout", _default_branch())
    _sh("git", "pull", "--ff-only")
    _sh("git", "branch", "-D", branch, check=False)
    return "main-checkout"


def _require_pr_base_sha(pr) -> str:
    """Return the immutable base commit supplied by the code-host API."""
    base_sha = getattr(pr, "base_sha", None)
    if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise SystemExit(
            "⛔ Preuve bornée refusée — SHA de base GitHub absent ou invalide."
        )
    return base_sha


def _require_unchanged_pr_coordinates(ch, repo, pr_number, original, base_sha) -> None:
    """Fail closed when the code-host no longer exposes the reviewed PR coordinates."""
    fresh = ch.get_pr(repo, int(pr_number))
    fresh_base_sha = _require_pr_base_sha(fresh)
    if (fresh.sha != original.sha or fresh_base_sha != base_sha
            or fresh.url != original.url or fresh.merged != original.merged):
        raise SystemExit(
            "⛔ Merge refusé — les coordonnées GitHub de la PR ont changé "
            "depuis la preuve (head/base/état). Relance les gates sur la PR courante."
        )


def _reuse_or_open_pr(ch, repo: str, branch: str, base: str,
                      title: str, body: str, *, update_body: bool = True):
    """Reuse the one valid open PR for this branch, or create it exactly once."""
    candidates = ch.list_prs(repo, branch)
    open_candidates = [candidate for candidate in candidates if candidate.state == "open"]
    if len(open_candidates) > 1:
        numbers = ", ".join(f"#{candidate.number}" for candidate in open_candidates)
        raise SystemExit(
            f"⛔ PR ambiguë pour la branche '{branch}' ({numbers}) — "
            "ferme les candidates en trop puis relance open-pr."
        )
    if open_candidates:
        candidate = open_candidates[0]
        if candidate.base != base:
            raise SystemExit(
                f"⛔ PR #{candidate.number} cible '{candidate.base}' au lieu de "
                f"'{base}' — corrige ou ferme cette PR avant de relancer open-pr."
            )
        # A retry without an explicitly supplied summary is a synchronization
        # operation, not permission to replace a body that may contain human notes.
        fresh = (
            ch.update_pr(repo, candidate.number, title, body)
            if update_body
            else ch.get_pr(repo, candidate.number)
        )
        if fresh.state != "open" or fresh.head != branch or fresh.base != base:
            raise SystemExit(
                f"⛔ PR #{candidate.number} modifiée pendant la reprise — "
                "recharge ses coordonnées puis relance open-pr."
            )
        return fresh, True
    if candidates:
        numbers = ", ".join(f"#{candidate.number}" for candidate in candidates)
        raise SystemExit(
            f"⛔ La branche '{branch}' possède seulement une PR fermée ({numbers}) — "
            "utilise une nouvelle branche ou rouvre explicitement la bonne PR."
        )
    return ch.open_pr(repo, branch, base, title, body), False


def _start_transition_path(tracker, current_state: str) -> tuple[str, ...]:
    """Validate the provider's bounded, provider-neutral start preflight."""
    planner = getattr(tracker, "start_transition_path", None)
    try:
        path = (
            planner(current_state)
            if callable(planner)
            else (() if current_state == "in-progress" else ("in-progress",))
        )
    except Exception:
        raise SystemExit(
            f"⛔ Démarrage refusé depuis l'état '{current_state}' avant toute mutation Git."
        ) from None
    if (not isinstance(path, tuple) or len(path) > 3
            or any(not isinstance(state, str) for state in path)
            or len(set(path)) != len(path)
            or (current_state == "in-progress" and path)
            or (current_state != "in-progress" and (not path or path[-1] != "in-progress"))):
        raise SystemExit(
            "⛔ Plan de démarrage tracker invalide — aucune mutation Git effectuée."
        )
    return tuple(write.validate_state(state) for state in path)


def start(issue_id, flags=()):
    # local guard first — don't pay the tracker round-trip to then refuse
    dirty = _sh("git", "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise SystemExit(f"⛔ Arbre de travail sale — commit ou stash avant de démarrer "
                         f"une issue :\n{dirty}")
    tr = foundry.tracker()
    write.issue_binding(tr, issue_id)
    it = tr.get_issue(issue_id)
    btype = _BRANCH_TYPE.get(it.type, "chore")
    branch = f"{btype}/{issue_id.lower()}-{_slug(it.title)}"
    transition_path = _start_transition_path(tr, it.state)
    branch_mode = _prepare_branch(branch)
    try:
        for state in transition_path:
            write.transition(tr, issue_id, state)
    except (Exception, SystemExit):
        raise SystemExit(
            f"⛔ Démarrage tracker interrompu ; la branche '{branch}' est conservée. "
            f"Depuis son worktree, relance exactement `issue start {issue_id}` : "
            "le preflight reprendra uniquement les transitions restantes."
        ) from None
    suffix = {
        "linked-worktree": " · worktree lié",
        "existing": " · branche reprise",
    }.get(branch_mode, "")
    print(f"🚀 {issue_id} → in-progress · branche {branch}{suffix}\n   {it.title}")


def openpr(issue_id=None, base=None, flags=()):
    tr, ch = foundry.tracker(), foundry.codehost()
    branch = _sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    if not issue_id:
        # derive the id ONLY from the shape `start` mints (<type>/<ticker>-<n>-<slug>) —
        # a loose search would turn chore/upgrade-node-20 into issue "NODE-20" and
        # silently write on the wrong issue
        m = re.match(r"(?i)^[a-z]+/([a-z][a-z0-9]*-\d+)(?:-|$)", branch)
        if not m:
            raise SystemExit(f"⛔ Pas d'ID d'issue donné et la branche '{branch}' ne "
                             f"suit pas <type>/<ticker>-<n>-… ; passe l'ID explicitement.")
        issue_id = m.group(1).upper()
    write.issue_binding(tr, issue_id)
    write.preflight_issue_operation(tr, "openpr")
    it = tr.get_issue(issue_id)
    repo = ch.resolve_repo()
    base = base or _default_branch()
    summary = f"## Summary\n- {it.title}"
    summary_supplied = False
    if "--summary-stdin" in flags:  # explicit opt-in: never sniff/block on stdin
        piped = sys.stdin.read().strip()
        if piped:
            summary = piped
            summary_supplied = True
    _sh("git", "push", "-u", "origin", branch)
    btype = _BRANCH_TYPE.get(it.type, "chore")
    title = f"[{issue_id}] {btype}: {it.title}"
    body = (f"{summary}\n\nCloses tracker issue **{issue_id}**.\n\n"
            "🤖 Generated through [patolabs Foundry]"
            "(https://github.com/patobiskoto/patolabs-plugins/tree/main/plugins/foundry)")
    pr, reused = _reuse_or_open_pr(
        ch, repo, branch, base, title, body,
        update_body=summary_supplied,
    )
    pr_source_operation = (
        "codehost.open_pr" if not reused
        else "codehost.update_pr" if summary_supplied
        else "codehost.get_pr"
    )
    _observe_receipt(
        issue_id, "pr",
        lambda: execution_receipts.pr_receipt(
            ch.name, repo, pr, operation=pr_source_operation,
        ),
    )
    context = None
    if getattr(tr, "bounded_transition_proofs", False):
        base_sha = _require_pr_base_sha(pr)
        head = pr.sha or git_head()
        if git_head() != head:
            raise SystemExit("⛔ Transition review refusée — HEAD local différent du SHA de la PR.")
        context = TransitionContext(
            pr_url=pr.url,
            head_sha=head,
            base_sha=base_sha,
            review_digest=review_diff_hash(git_diff(base=base_sha)),
        )
        write.set_field(tr, issue_id, "GitHub PR", pr.url)
        write.transition(tr, issue_id, "review", context=context)
    else:
        # Preserve the established provider call order for adapters without proofs.
        write.transition(tr, issue_id, "review")
        write.set_field(tr, issue_id, "GitHub PR", pr.url)
    action = "réutilisée" if reused else "ouverte"
    print(f"🔗 PR #{pr.number} {action} : {pr.url}\n   {issue_id} → review")


def merge(issue_id, pr_number, flags=()):
    allow_no_ci = "--allow-no-ci" in flags
    tr, ch = foundry.tracker(), foundry.codehost()
    write.issue_binding(tr, issue_id)
    write.preflight_issue_operation(tr, "merge")
    repo = ch.resolve_repo()
    pr = ch.get_pr(repo, int(pr_number))
    _observe_receipt(
        issue_id, "pr",
        lambda: execution_receipts.pr_receipt(
            ch.name, repo, pr, operation="codehost.get_pr",
        ),
    )
    current = tr.get_issue(issue_id)
    transition_context = None
    review_diff = None
    pr_base_sha = None
    acceptance_sync = {"status": "already-complete", "checked": 0}
    proof = None
    if getattr(tr, "bounded_transition_proofs", False):
        if getattr(current, "state", None) == "done":
            if not pr.merged or not pr.merge_sha or current.pr_url != pr.url:
                raise SystemExit(
                    "⛔ Reprise tracker refusée — l'issue est done mais le reçu exact "
                    "de cette PR fusionnée n'est pas disponible."
                )
            _observe_receipt(
                issue_id, "merge",
                lambda: execution_receipts.merge_receipt(
                    ch.name, repo, int(pr_number), pr, pr.merge_sha,
                    operation="codehost.get_pr",
                ),
            )
            ch.delete_branch(repo, pr.head)
            cleanup_mode = _cleanup_branch(pr.head)
            cleanup = (" · nettoyage local délégué à Codex"
                       if cleanup_mode == "linked-worktree" else "")
            print(
                f"✅ Reprise PR #{pr_number} · reçu déjà durable {pr.merge_sha} · "
                f"{issue_id} déjà done{cleanup}"
            )
            return
        head = git_head()
        if head != pr.sha:
            raise SystemExit(
                f"⛔ Preuve tracker refusée — HEAD local différent du SHA de la PR ({pr.sha[:9]})."
            )
        pr_base_sha = _require_pr_base_sha(pr)
        review_diff = git_diff(base=pr_base_sha)
        transition_context = TransitionContext(
            pr_url=pr.url,
            head_sha=head,
            base_sha=pr_base_sha,
            review_digest=review_diff_hash(review_diff),
        )

    # AC checkboxes remain the normal tracker signal.  When they lag behind, only a
    # structured, current review proof can replace that administrative signal.
    if getattr(current, "ac_done", 0) < getattr(current, "ac_total", 0):
        try:
            head = git_head()
            if head != pr.sha:
                raise RoutingConfigError("HEAD local différent du SHA de la PR")
            if pr_base_sha is None:
                pr_base_sha = _require_pr_base_sha(pr)
            if review_diff is None:
                review_diff = git_diff(base=pr_base_sha)
            proof = AcceptanceProofStore(repository_identity()).valid_for_merge(
                issue_id=current.id, issue_body=current.body, head=head,
                diff=review_diff, base=pr_base_sha,
            )
        except RoutingConfigError as exc:
            if "--allow-incomplete-ac" not in flags:
                raise SystemExit(
                    "⛔ Merge refusé — AC tracker incomplètes et preuve structurée invalide "
                    f"({exc}). Utilise l'override humain explicite si nécessaire.") from None
            reason = next((value.split("=", 1)[1] for value in flags
                           if value.startswith("--ac-override-reason=")), "")
            if not reason or not re.fullmatch(r"[a-z0-9_-]{3,80}", reason):
                raise SystemExit("⛔ Override AC refusé — --ac-override-reason=<code-public> requis.")
            # This is deliberately an audit-only human escape hatch; it never modifies
            # checkboxes or turns prose into evidence.
            try:
                write.add_comment(
                    tr,
                    issue_id, f"Audit merge: override humain explicite des AC incomplètes ({reason}).",
                )
            except Exception as exc:
                raise SystemExit("⛔ Merge refusé — écriture de la note d'audit AC impossible.") from exc
            acceptance_sync = {"status": "human-override", "checked": 0}
        else:
            _observe_receipt(
                issue_id, "review",
                lambda: execution_receipts.review_receipt(ch.name, repo, pr, proof),
            )
            try:
                acceptance_sync = write.sync_acceptance(
                    tr, issue_id, current.body, proof,
                )
            except AcceptanceSyncUnavailableError:
                raise SystemExit(
                    "⛔ Merge refusé — ce tracker ne sait pas synchroniser les AC "
                    "atomiquement. Coche-les manuellement puis relance le merge."
                ) from None
            except TrackerConflictError:
                raise SystemExit(
                    "⛔ Merge refusé — le corps ou la version de l'issue a changé. "
                    "Recharge l'issue, vérifie ses AC puis relance le merge."
                ) from None
            except RoutingConfigError as exc:
                raise SystemExit(
                    f"⛔ Merge refusé — synchronisation AC incompatible ({exc}). "
                    "Relance la review sur l'issue courante."
                ) from None
            except Exception:
                raise SystemExit(
                    "⛔ Merge refusé — synchronisation AC indisponible. "
                    "Vérifie le tracker puis relance le merge."
                ) from None
            # Audit before any irreversible code-host operation.  The proof identifier
            # is a digest, so neither the diff nor the claim capability is disclosed.
            # The provider mutation is already durably proof-bound; this human-facing
            # note is best-effort and cannot orphan an otherwise valid receipt.
            try:
                write.add_comment(
                    tr, issue_id,
                    f"Audit merge: preuve AC structurée {proof['proof_id']} validée ; "
                    f"synchronisation {acceptance_sync['status']} "
                    f"({acceptance_sync['checked']} case(s)).",
                )
                acceptance_sync["audit_note"] = "recorded"
            except Exception:
                acceptance_sync["audit_note"] = "unavailable"

    # --- INVARIANT: CI must be green. This refuses; it is not a suggestion. ---
    # The only waiver (zero checks + explicit flag) is decided INSIDE the gate.
    gate = write.ci_gate(ch, repo, pr.sha, allow_no_ci=allow_no_ci)
    _observe_receipt(
        issue_id, "ci",
        lambda: execution_receipts.ci_receipt(ch.name, repo, pr.sha, gate),
    )
    if not gate["passed"]:
        # suggest the flag only when it was NOT passed — when the gate just refused
        # it (active check-suite), recommending it again would be contradictory
        hint = ("\n   Aucun check trouvé : si un push vient d'avoir lieu, la CI n'a "
                "probablement pas encore démarré — attends et relance. --allow-no-ci "
                "UNIQUEMENT si le repo n'a réellement aucune CI."
                if gate["total"] == 0 and not allow_no_ci else "")
        raise SystemExit(
            f"⛔ Merge refusé — CI non verte ({gate['reason']}). "
            f"pending={gate['pending']} failing={gate['failing']}. "
            f"Le gate CI est un invariant, pas un conseil." + hint)
    if gate["waived"]:
        print("⚠️  merge SANS aucun check CI (--allow-no-ci) — assumé explicitement.")

    if pr_base_sha is not None and not pr.merged:
        # Preserve the early refusal: a PR already stale must not rewrite tracker
        # receipts. A second read below closes the race introduced by those writes.
        _require_unchanged_pr_coordinates(ch, repo, pr_number, pr, pr_base_sha)

    if transition_context is not None:
        # A correction may have moved the PR head after openpr recorded its first
        # receipt. Refresh the provider receipt on the exact head that just passed
        # review/CI, without giving the tracker any review or merge authority.
        write.transition(tr, issue_id, "in-progress")
        write.transition(tr, issue_id, "review", context=transition_context)

    branch = pr.head
    if transition_context is not None and pr.merged:
        if not pr.merge_sha:
            raise SystemExit("⛔ Reprise tracker refusée — SHA de merge GitHub indisponible.")
        merged_sha = pr.merge_sha
        merged_observation = pr
        merged_operation = "codehost.get_pr"
    else:
        if pr_base_sha is not None and not pr.merged:
            # GitHub's merge endpoint can pin the reviewed head but has no equivalent
            # expected-base parameter. This exact-coordinate re-read must remain the
            # final network operation before merge_pr.
            _require_unchanged_pr_coordinates(ch, repo, pr_number, pr, pr_base_sha)
        try:
            # pass the gated sha: GitHub refuses if the head moved since the CI verdict
            merged = ch.merge_pr(repo, int(pr_number), sha=pr.sha)
        except RuntimeError as e:
            # match gh's "(HTTP 409)" marker, not a bare "409" (a PR numbered 409 would
            # put that digit string in every error message via the command line)
            if "HTTP 409" in str(e):
                raise SystemExit(
                    f"⛔ La branche a bougé depuis le verdict CI (head ≠ {pr.sha[:9]}). "
                    f"Relance le merge : le gate doit re-passer sur le nouveau head.") from None
            raise
        if not merged.merged:
            raise SystemExit(f"⛔ Le merge n'a pas abouti pour PR #{pr_number}.")
        merged_sha = merged.sha
        merged_observation = merged
        merged_operation = "codehost.merge_pr"
    _observe_receipt(
        issue_id, "merge",
        lambda: execution_receipts.merge_receipt(
            ch.name, repo, int(pr_number), merged_observation, merged_sha,
            operation=merged_operation,
        ),
    )
    done_context = None
    if transition_context is not None:
        done_context = TransitionContext(
            pr_url=transition_context.pr_url,
            head_sha=transition_context.head_sha,
            base_sha=transition_context.base_sha,
            review_digest=transition_context.review_digest,
            merge_sha=merged_sha,
        )
    if done_context is not None:
        # Preserve the remote branch until the proof-bound tracker receipt is durable.
        # If the tracker is unavailable after the irreversible merge, a retry still
        # has the exact reviewed branch coordinates needed for recovery.
        write.transition(tr, issue_id, "done", context=done_context)
        ch.delete_branch(repo, branch)
    else:
        # Existing adapters retain their historical observable call order.
        ch.delete_branch(repo, branch)
        write.transition(tr, issue_id, "done")
    cleanup_mode = _cleanup_branch(branch)
    ci_note = ("⚠️ sans check CI — waiver --allow-no-ci" if gate["waived"]
               else f"CI verte: {gate['total']} checks")
    cleanup = (" · nettoyage local délégué à Codex"
               if cleanup_mode == "linked-worktree" else "")
    ac_note = (
        f"AC sync: {acceptance_sync['status']}"
        f"/{acceptance_sync['checked']}"
    )
    if acceptance_sync.get("audit_note") == "unavailable":
        ac_note += "/note-audit-indisponible"
    # the squash SHA that landed on the default branch — release tooling (ship-ios)
    # tags exactly this commit; origin/<default> would be racy if another PR lands
    print(f"✅ PR #{pr_number} mergée ({ci_note}) · {ac_note} · sha mergé {merged_sha} · "
          f"{issue_id} → done{cleanup}")


def close_epic(issue_id, flags=()):
    """Close a non-code Epic through the tracker's atomic audited capability."""
    tracker = foundry.tracker()
    try:
        outcome = write.close_epic(tracker, issue_id)
    except EpicClosureUnavailableError:
        raise SystemExit(
            f"⛔ Clôture Epic indisponible pour le tracker {tracker.name} : "
            "un endpoint atomique parent+enfants avec audit est requis."
        ) from None
    except TrackerConflictError:
        raise SystemExit(
            "⛔ Clôture Epic refusée : le parent ou son ensemble d'enfants a changé. "
            "Recharge le graphe puis relance la commande."
        ) from None
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(
            f"⛔ Clôture Epic interrompue pour {issue_id}. Relance exactement "
            f"`issue close-epic {issue_id}` : le reçu provider permettra la reprise."
        ) from None
    _observe_receipt(
        issue_id, "epic_closure",
        lambda: execution_receipts.epic_closure_receipt(
            tracker.name, issue_id, outcome,
        ),
    )
    replay = " · reçu existant repris" if outcome.replayed else ""
    print(
        f"✅ Epic {issue_id} → done · audit provider {outcome.audit_id}{replay}"
    )


def _default_branch():
    return registry.default_branch() or "main"


_KNOWN_FLAGS = {"start": set(), "openpr": {"--summary-stdin"},
                "merge": {"--allow-no-ci", "--allow-incomplete-ac"},
                "close-epic": set()}

if __name__ == "__main__":
    cmd, rest = sys.argv[1], sys.argv[2:]
    flags = {a for a in rest if a.startswith("--")}
    reason_flags = {flag for flag in flags if flag.startswith("--ac-override-reason=")}
    unknown = flags - _KNOWN_FLAGS.get(cmd, set()) - reason_flags
    if unknown:  # a misspelled flag must fail loudly, not run as a normal gated call
        raise SystemExit(f"⛔ Flag(s) inconnus pour '{cmd}' : {', '.join(sorted(unknown))}. "
                         f"Autorisés : {', '.join(sorted(_KNOWN_FLAGS.get(cmd, set()))) or '(aucun)'}.")
    args = [a for a in rest if not a.startswith("--")]
    {"start": start, "openpr": openpr, "merge": merge,
     "close-epic": close_epic}[cmd](*args, flags=flags)
