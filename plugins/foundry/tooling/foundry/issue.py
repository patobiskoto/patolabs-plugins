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
from foundry.trackers.linear import LinearQuotaExhaustedError, LinearTrackerError
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


def _bounded_review_predecessor(tracker, observed_issue) -> str | None:
    """Reuse the logical state authenticated by the caller's lifecycle read."""
    if not getattr(tracker, "bounded_state_transitions", False):
        return None
    return getattr(observed_issue, "normalized_state", None)


def _require_unchanged_pr_coordinates(ch, repo, pr_number, original, base_sha) -> None:
    """Fail closed when the code-host no longer exposes the reviewed PR coordinates."""
    fresh = ch.get_pr(repo, int(pr_number))
    fresh_base_sha = _require_pr_base_sha(fresh)
    if (fresh_base_sha != base_sha or any(
        getattr(fresh, key, None) != getattr(original, key, None)
        for key in ("number", "url", "sha", "head", "base", "state", "merged", "merge_sha")
    )):
        raise SystemExit(
            "⛔ Merge refusé — les coordonnées GitHub de la PR ont changé "
            "depuis la preuve (head/base/état). Relance les gates sur la PR courante."
        )


def _require_pr_coordinates(pr, pr_number: int) -> str:
    """Validate the exact code-host readback before any bounded recovery effect."""
    exact = (
        getattr(pr, "number", None) == pr_number
        and isinstance(getattr(pr, "head", None), str)
        and bool(pr.head)
        and isinstance(getattr(pr, "base", None), str)
        and bool(pr.base)
        and re.fullmatch(r"[0-9a-f]{40}", str(getattr(pr, "sha", None)))
        is not None
        and isinstance(getattr(pr, "merged", None), bool)
        and getattr(pr, "state", None) in {"open", "closed"}
        and (pr.merged or pr.state == "open")
        and (
            not pr.merged
            or re.fullmatch(r"[0-9a-f]{40}", str(getattr(pr, "merge_sha", None)))
            is not None
        )
    )
    if not exact:
        raise SystemExit(
            "⛔ Merge refusé — la PR liée au ticket ou ses coordonnées exactes "
            "(numéro/URL/head/base/état) diffèrent de la PR demandée."
        )
    return _require_pr_base_sha(pr)


def _require_linked_pr_coordinates(current, pr, pr_number: int) -> str:
    """Authenticate the issue-linked PR before a bounded tracker lifecycle effect."""
    base_sha = _require_pr_coordinates(pr, pr_number)
    if getattr(current, "pr_url", None) != getattr(pr, "url", None):
        raise SystemExit("⛔ Merge refusé — la PR liée au ticket diffère de la PR demandée.")
    return base_sha


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
    start_replay = None
    try:
        it = tr.get_issue(issue_id)
    except TrackerConflictError:
        if not getattr(tr, "append_only_lifecycle_supported", False):
            raise
        it = tr.observe_issue(issue_id)
        if (
            it.normalized_state != "in-progress"
            or it.projection_status != "disagreement"
            or it.native_state not in {"backlog", "ready", "blocked"}
        ):
            raise
        # Only the existing authenticated start receipt may repair its missing
        # native State. The adapter revalidates the entire chain and source.
        start_replay = TransitionContext(expected_state=it.native_state)
    if (
        getattr(tr, "append_only_lifecycle_supported", False)
        and it.state == "in-progress"
        and it.projection_status == "native-only"
    ):
        raise SystemExit(
            "⛔ Démarrage refusé : état natif in-progress sans reçu Foundry ; "
            "aucune preuve de transition à reprendre."
        )
    btype = _BRANCH_TYPE.get(it.type, "chore")
    branch = f"{btype}/{issue_id.lower()}-{_slug(it.title)}"
    transition_path = _start_transition_path(tr, it.state)
    branch_mode = _prepare_branch(branch)
    if (
        branch_mode == "existing"
        and transition_path
        and getattr(tr, "bounded_state_transitions", False)
    ):
        raise SystemExit(
            "⛔ Reprise tracker refusée — la branche existe mais l'état "
            "prédécesseur de l'opération d'origine n'est pas disponible ; "
            "aucune transition n'a été tentée."
        )
    try:
        if start_replay is not None:
            write.transition(tr, issue_id, "in-progress", context=start_replay)
        predecessor = it.state
        for state in transition_path:
            context = (
                TransitionContext(expected_state=predecessor)
                if getattr(tr, "bounded_state_transitions", False)
                else None
            )
            if context is None:
                write.transition(tr, issue_id, state)
            else:
                write.transition(tr, issue_id, state, context=context)
            predecessor = state
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
    binding = write.issue_binding(tr, issue_id)
    write.preflight_issue_operation(tr, "openpr")
    try:
        it = tr.get_issue(issue_id)
    except TrackerConflictError:
        observer = getattr(tr, "observe_issue", None)
        if not callable(observer):
            raise
        it = observer(issue_id)
        if not (
            it.normalized_state == "review"
            and it.projection_status == "disagreement"
        ):
            raise
        repair = getattr(tr, "recover_review_projection", None)
        if getattr(tr, "append_only_lifecycle_supported", False) and callable(repair):
            if not repair(issue_id, project=binding):
                raise TrackerConflictError(
                    "review receipt/native State disagreement cannot be repaired"
                )
            it = tr.get_issue(issue_id)
    # Keep the logical predecessor returned by the proof-validating read above.
    # A later native-State observation could adopt unrelated provider drift.
    review_predecessor = _bounded_review_predecessor(tr, it)
    logical_state = (
        review_predecessor
        if getattr(tr, "bounded_state_transitions", False)
        else getattr(it, "state", None)
    )
    if (
        getattr(tr, "bounded_transition_proofs", False)
        or getattr(tr, "bounded_state_transitions", False)
    ) and logical_state not in {"in-progress", "review"}:
        raise SystemExit(
            "⛔ Ouverture PR refusée — l'issue doit être in-progress ou review ; "
            "aucun push ni effet code-host n'a été tenté."
        )
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
            expected_state=(
                review_predecessor if getattr(tr, "bounded_state_transitions", False)
                else None
            ),
            pr_url=pr.url,
            head_sha=head,
            base_sha=base_sha,
            review_digest=review_diff_hash(git_diff(base=base_sha)),
        )
        if not getattr(tr, "append_only_lifecycle_supported", False):
            write.set_field(tr, issue_id, "GitHub PR", pr.url)
        write.transition(tr, issue_id, "review", context=context)
    else:
        if getattr(tr, "bounded_state_transitions", False):
            context = TransitionContext(expected_state=review_predecessor)
        # Preserve the established provider call order for adapters without proofs.
        if context is None:
            write.transition(tr, issue_id, "review")
        else:
            write.transition(tr, issue_id, "review", context=context)
        write.set_field(tr, issue_id, "GitHub PR", pr.url)
    action = "réutilisée" if reused else "ouverte"
    print(f"🔗 PR #{pr.number} {action} : {pr.url}\n   {issue_id} → review")


def _ac_override_reason(flags) -> str:
    """Return the validated public reason code of an explicit human AC override."""
    reason = next((value.split("=", 1)[1] for value in flags
                   if value.startswith("--ac-override-reason=")), "")
    if not reason or not re.fullmatch(r"[a-z0-9_-]{3,80}", reason):
        raise SystemExit("⛔ Override AC refusé — --ac-override-reason=<code-public> requis.")
    return reason


def merge(issue_id, pr_number, flags=()):
    allow_no_ci = "--allow-no-ci" in flags
    tr, ch = foundry.tracker(), foundry.codehost()
    binding = write.issue_binding(tr, issue_id)
    write.preflight_issue_operation(tr, "merge")
    repo = ch.resolve_repo()
    pr = ch.get_pr(repo, int(pr_number))
    bounded_state_transitions = getattr(tr, "bounded_state_transitions", False)
    bounded_lifecycle = bounded_state_transitions or getattr(
        tr, "bounded_transition_proofs", False,
    )
    if not bounded_lifecycle:
        # Preserve legacy receipt ordering. Bounded lifecycle providers delay
        # this local effect until ticket-linked PR coordinates are authenticated.
        _observe_receipt(
            issue_id, "pr",
            lambda: execution_receipts.pr_receipt(
                ch.name, repo, pr, operation="codehost.get_pr",
            ),
        )
    if bounded_lifecycle:
        _require_pr_coordinates(pr, int(pr_number))
    override_recovered = False
    try:
        current = tr.get_issue(issue_id)
    except TrackerConflictError:
        # A confirmed GitHub merge can outlive only the native Linear State
        # projection.  Ask the adapter to authenticate its existing receipt and
        # repair exactly that effect before considering any override recovery.
        observer = getattr(tr, "observe_issue", None)
        if not callable(observer):
            # Providers that have not opted into the shared observation/recovery
            # port keep their original typed refusal.  Do not turn that boundary
            # into an AttributeError or manufacture an observation from native
            # state alone.
            raise
        observed = observer(issue_id)
        if (
            pr.merged
            and pr.merge_sha
            and observed.normalized_state == "done"
            and observed.projection_status == "disagreement"
            and observed.pr_url == pr.url
        ):
            base_sha = _require_linked_pr_coordinates(observed, pr, int(pr_number))
            if tr.recover_done_projection(
                issue_id,
                pr_url=pr.url,
                head_sha=pr.sha,
                base_sha=base_sha,
                merge_sha=pr.merge_sha,
                project=binding,
            ):
                current = tr.get_issue(issue_id)
            else:
                raise
        else:
            current = None
        # Bounded recovery of an issue merged under the human AC override before the
        # typed receipt existed: only the exact replay of that override on the
        # already-merged PR may append the missing receipt; ordinary reads stay strict.
        if current is not None:
            pass
        elif not ("--allow-incomplete-ac" in flags
                and getattr(tr, "acceptance_override_projection_supported", False)
                and pr.merged and pr.merge_sha):
            raise
        if current is None:
            reason = _ac_override_reason(flags)
            try:
                override_recovered = write.recover_acceptance_override(
                    tr, issue_id, reason,
                    pr_url=pr.url, head_sha=pr.sha, base_sha=_require_pr_base_sha(pr),
                    merge_sha=pr.merge_sha,
                )
            except TrackerConflictError as exc:
                raise SystemExit(
                    "⛔ Reprise override AC refusée — aucun reçu done exact de cette PR "
                    f"fusionnée sans preuve AC ({exc}) ; aucune écriture effectuée."
                ) from None
            current = tr.get_issue(issue_id)
    if (
        getattr(tr, "append_only_lifecycle_supported", False)
        and current.ac_total < 1
    ):
        raise SystemExit(
            "⛔ Merge refusé — aucun critère d’acceptation à attester ; "
            "aucun merge GitHub tenté."
        )
    if bounded_lifecycle:
        _require_linked_pr_coordinates(current, pr, int(pr_number))
        if (getattr(current, "state", None) == "done"
                and getattr(tr, "append_only_lifecycle_supported", False)):
            if not pr.merged or not tr.recover_done_projection(
                issue_id, pr_url=pr.url, head_sha=pr.sha,
                base_sha=_require_pr_base_sha(pr), merge_sha=pr.merge_sha,
                project=binding,
            ):
                raise SystemExit(
                    "⛔ Reprise tracker refusée — coordonnées du reçu done exact divergentes."
                )
        _observe_receipt(
            issue_id, "pr",
            lambda: execution_receipts.pr_receipt(
                ch.name, repo, pr, operation="codehost.get_pr",
            ),
        )
    transition_context = None
    review_diff = None
    pr_base_sha = _require_pr_base_sha(pr) if bounded_lifecycle else None
    acceptance_sync = {"status": "already-complete", "checked": 0}
    proof = None
    proof_store = None
    acceptance_override_reason = None
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
            recovered = " · reçu override AC ajouté" if override_recovered else ""
            print(
                f"✅ Reprise PR #{pr_number} · reçu déjà durable {pr.merge_sha} · "
                f"{issue_id} déjà done{recovered}{cleanup}"
            )
            return
        head = git_head()
        if head != pr.sha:
            raise SystemExit(
                f"⛔ Preuve tracker refusée — HEAD local différent du SHA de la PR ({pr.sha[:9]})."
            )
        pr_base_sha = _require_pr_base_sha(pr)
        review_diff = git_diff(base=pr_base_sha)
        # ``current`` is the proof-validated logical issue read captured before
        # the gates.  Reuse it rather than sampling native State as a new authority.
        review_predecessor = _bounded_review_predecessor(tr, current)
        transition_context = TransitionContext(
            expected_state=(
                review_predecessor if bounded_state_transitions
                else None
            ),
            pr_url=pr.url,
            head_sha=head,
            base_sha=pr_base_sha,
            review_digest=review_diff_hash(review_diff),
        )
        if getattr(tr, "append_only_lifecycle_supported", False):
            # Publish the exact current PR generation before deciding whether its
            # AC are complete. A previous head's acceptance receipt must never
            # suppress review-proof validation for this head.
            write.transition(tr, issue_id, "review", context=transition_context)
            current = tr.get_issue(issue_id)

    if (
        transition_context is None
        and getattr(tr, "delivery_receipt_supported", False)
        and binding is not None
    ):
        head = git_head()
        if head != pr.sha:
            raise SystemExit(
                f"⛔ Preuve tracker refusée — HEAD local différent du SHA de la PR ({pr.sha[:9]})."
            )
        pr_base_sha = _require_pr_base_sha(pr)
        review_diff = git_diff(base=pr_base_sha)
        if str(getattr(current, "state", "") or "").casefold() not in {
            "review", "done",
        }:
            raise SystemExit(
                "⛔ Reçu de livraison refusé — l'issue YouTrack n'est pas en review."
            )
        transition_context = TransitionContext(
            expected_state=getattr(current, "normalized_state", None),
            pr_url=pr.url,
            head_sha=head,
            base_sha=pr_base_sha,
            review_digest=review_diff_hash(review_diff),
        )

    if getattr(tr, "delivery_receipt_supported", False) and binding is not None:
        proof_store = AcceptanceProofStore(repository_identity())

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
            proof = (proof_store or AcceptanceProofStore(repository_identity())).valid_for_merge(
                issue_id=current.id, issue_body=current.body, head=head,
                diff=review_diff, base=pr_base_sha,
            )
        except RoutingConfigError as exc:
            if "--allow-incomplete-ac" not in flags:
                raise SystemExit(
                    "⛔ Merge refusé — AC tracker incomplètes et preuve structurée invalide "
                    f"({exc}). Utilise l'override humain explicite si nécessaire.") from None
            reason = _ac_override_reason(flags)
            acceptance_override_reason = reason
            if proof_store is not None:
                try:
                    proof = proof_store.current_for_sync(
                        issue_id=current.id,
                        issue_body=current.body,
                        head=head,
                        diff=review_diff,
                        base=pr_base_sha,
                    )
                except RoutingConfigError:
                    raise SystemExit(
                        "⛔ Override AC refusé — aucune preuve de review exacte et "
                        "courante ne peut être liée au reçu de livraison."
                    ) from None
            # This is deliberately an audit-only human escape hatch; it never modifies
            # checkboxes or turns prose into evidence.
            audit_text = f"Audit merge: override humain explicite des AC incomplètes ({reason})."
            if getattr(tr, "acceptance_override_projection_supported", False):
                # The typed receipt, bound to this exact review generation, is the only
                # authority; it is durable before any code-host effect so a replay can
                # finish the merge. The prose note is best-effort and never evidence.
                if transition_context is None:
                    raise SystemExit("⛔ Override AC refusé — coordonnées de review bornées absentes.")
                try:
                    created = write.project_acceptance_override(
                        tr, issue_id, reason, transition_context,
                    )
                except Exception as exc:
                    raise SystemExit(
                        "⛔ Merge refusé — reçu typé d'override AC impossible ; "
                        "aucun merge tenté."
                    ) from exc
                if created:
                    try:
                        write.add_comment(tr, issue_id, audit_text)
                    except Exception:
                        pass
                acceptance_sync = {"status": "human-override", "checked": 0,
                                   "audit": "append-only-override"}
            else:
                try:
                    write.add_comment(tr, issue_id, audit_text)
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
    elif proof_store is not None:
        try:
            proof = proof_store.valid_for_merge(
                issue_id=current.id,
                issue_body=current.body,
                head=transition_context.head_sha,
                diff=review_diff,
                base=transition_context.base_sha,
            )
        except RoutingConfigError as exc:
            raise SystemExit(
                "⛔ Merge refusé — preuve de review structurée invalide pour le "
                f"reçu de livraison ({exc})."
            ) from None

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

    if (getattr(tr, "bounded_transition_proofs", False)
            and transition_context is not None
            and not getattr(tr, "append_only_lifecycle_supported", False)
            and getattr(current, "state", None) != "done"):
        # A correction may have moved the PR head after openpr recorded its first
        # receipt. Refresh the provider receipt on the exact head that just passed
        # review/CI, without giving the tracker any review or merge authority.
        write.transition(tr, issue_id, "in-progress")
        write.transition(tr, issue_id, "review", context=transition_context)

    branch = pr.head
    if pr.merged:
        if not pr.merge_sha:
            raise SystemExit("⛔ Reprise tracker refusée — SHA de merge GitHub indisponible.")
        merged_sha = pr.merge_sha
        merged_observation = pr
        merged_operation = "codehost.get_pr"
    else:
        if getattr(tr, "append_only_lifecycle_supported", False):
            projected = tr.get_issue(issue_id)
            if (projected.state != "review" or projected.pr_url != pr.url
                    or (projected.ac_done != projected.ac_total
                        and acceptance_sync["status"] != "human-override")):
                raise SystemExit(
                    "⛔ Merge refusé — la projection tracker de la génération "
                    "courante est incomplète ou a divergé."
                )
        if pr_base_sha is not None and not pr.merged:
            # The provider guard belongs in this final pre-merge segment.  Re-read
            # the GitHub coordinates after it so that this remains the last network
            # observation before merge_pr.
            write.preflight_merge_effect(tr)
            # GitHub's merge endpoint can pin the reviewed head but has no equivalent
            # expected-base parameter. This exact-coordinate re-read must remain the
            # final network operation before merge_pr.
            _require_unchanged_pr_coordinates(ch, repo, pr_number, pr, pr_base_sha)
        else:
            # A Linear team can otherwise react to this GitHub merge by completing
            # every linked issue.  No coordinate re-read is available for this legacy
            # path, but generic trackers retain their normal behavior.
            write.preflight_merge_effect(tr)
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
    if getattr(tr, "delivery_receipt_supported", False) and binding is not None:
        if transition_context is None:
            raise SystemExit(
                "⛔ Reçu de livraison refusé — coordonnées de review bornées absentes."
            )
        try:
            write.record_delivery_receipt(
                tr,
                issue_id,
                project=binding,
                codehost=ch.name,
                repository=repo,
                pr_number=int(pr_number),
                pr_url=pr.url,
                head_sha=transition_context.head_sha,
                base_sha=transition_context.base_sha,
                review_digest=transition_context.review_digest,
                merge_sha=merged_sha,
                acceptance=(
                    "deviated"
                    if acceptance_sync["status"] == "human-override"
                    else "accepted"
                ),
                override_reason=acceptance_override_reason,
                review_proof_id=proof["proof_id"],
                review_generation=proof["review"]["generation"],
                proof_ac_digest=proof["issue"]["ac_digest"],
            )
        except TrackerConflictError as exc:
            raise SystemExit(
                "⛔ Merge effectué mais reçu de livraison YouTrack non qualifié ; "
                "relance exactement la même commande après diagnostic, sans créer "
                "de preuve manuelle."
            ) from exc
    done_context = None
    if transition_context is not None:
        done_context = TransitionContext(
            expected_state=(
                "review" if getattr(tr, "bounded_state_transitions", False)
                else None
            ),
            pr_url=transition_context.pr_url,
            head_sha=transition_context.head_sha,
            base_sha=transition_context.base_sha,
            review_digest=transition_context.review_digest,
            merge_sha=merged_sha,
        )
    elif getattr(tr, "bounded_state_transitions", False):
        if not (
            re.fullmatch(r"[0-9a-f]{40}", str(pr.sha))
            and re.fullmatch(r"[0-9a-f]{40}", str(merged_sha))
        ):
            raise SystemExit(
                "⛔ Reprise tracker refusée — coordonnées exactes de la PR "
                "fusionnée indisponibles ou différentes du tracker."
            )
        _require_pr_base_sha(pr)
        if pr.merged and getattr(current, "pr_url", None) != pr.url:
            raise SystemExit(
                "⛔ Reprise tracker refusée — coordonnées exactes de la PR "
                "fusionnée indisponibles ou différentes du tracker."
            )
        done_context = TransitionContext(expected_state="review")
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


_SECRET = re.compile(
    r"(?:lin_api_|gh[opusr]_|github_pat_|xox[a-z]-)[A-Za-z0-9_\-]+"
    r"|perm:[A-Za-z0-9_.\-:=+/]+"
    r"|(?i:authorization)\s*[=:]\s*(?:(?i:bearer|basic|token)\s+)?\S+"
    r"|(?i:bearer)\s+\S+"
    r"|(?i:(?:token|api[_-]?key)\s*[=:]\s*)\S+"
    r"|(?<=://)[^\s/@:]+:[^\s/@]+(?=@)"
)


def _redact(text) -> str:
    return _SECRET.sub("[redacted]", str(text))


def _provider_cause(exc):
    """The typed Linear failure in the exception chain, if any (PAT-98/PAT-100)."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, LinearTrackerError):
            return exc
        seen.add(id(exc))
        if exc.__cause__ is not None:
            exc = exc.__cause__
        elif exc.__suppress_context__:
            exc = None  # `raise ... from None`: the context is deliberately hidden
        else:
            exc = exc.__context__
    return None


def _cause_text(exc) -> tuple[str, str]:
    """(cause line, kind) with kind in quota/transient/provider/other; never a token."""
    typed = _provider_cause(exc)
    if isinstance(typed, LinearQuotaExhaustedError):
        reset = typed.reset_at if typed.reset_at is not None else "inconnu"
        remaining = typed.remaining if typed.remaining is not None else "inconnu"
        return (f"quota Linear épuisé (restant {remaining}, réinitialisation {reset}, "
                f"{typed.retries} relecture(s) déjà tentée(s)) : {_redact(typed)}", "quota")
    if typed is not None:
        status = typed.status
        if typed.code == "transport_error":
            label = "erreur réseau"
        elif isinstance(status, int) and status >= 500:
            label = f"erreur serveur Linear {status}"
        else:
            label = "erreur provider Linear"
        kind = "transient" if label != "erreur provider Linear" else "provider"
        return (f"{label}, {typed.retries} relecture(s) déjà tentée(s) : {_redact(typed)}",
                kind)
    return f"{type(exc).__name__}: {_redact(exc)}", "other"


def _audit_command(tracker, issue_id, state) -> str:
    """The command carrying exactly the --accept-override set bound by the audit."""
    flags = set()
    if state.waived:
        flags.add("--accept-override=" + ",".join(state.waived))
    return _epic_command(tracker, issue_id, flags)


def _state_lines(
    state, command, kind="other", *, refused=False, audit_command=None, status_command="",
) -> list[str]:
    """Truthful closure state and re-run advice from a read-only epic_closure_state.

    "rien n'a été écrit" is only said when the tracker's intent store was READ and empty
    (state.kind "none"); every unverified store lands in the unknown branch.  After a
    refusal, or when the command's waived set differs from the audit's, the refused
    command is never advised again as such.
    """
    if state.intent in {"pending", "complete"}:
        intent = f"intention locale présente ({state.intent}, audit attendu {state.intent_audit_id})"
    elif state.intent == "unreadable":
        intent = "intention locale illisible"
    elif state.intent == "unverified":
        intent = "intention locale non vérifiée pour ce tracker"
    else:
        intent = "aucune intention locale"
    if kind == "quota":
        wait = ("Attends la réinitialisation du quota ; ne relance pas maintenant. ")
        rerun = "Après la réinitialisation, relance exactement"
    else:
        wait = ""
        rerun = "Relance exactement"
    if state.kind == "none":
        return [f"État : aucun audit provider et {intent} ; rien n'a été écrit. "
                f"{wait}{rerun} `{command}` : c'est sûr."]
    if state.kind == "intent-only":
        return [f"État : {intent}, mais aucun audit visible côté provider : effet ambigu "
                "(l'écriture a pu aboutir sans être encore visible). "
                f"{wait}Une relance régénère un nonce et un horodatage, donc un autre "
                "identifiant d'audit que celui attendu : elle est REFUSÉE (no second POST) "
                "tant que l'audit attendu n'est pas visible côté provider ; elle ne "
                "reprend qu'une fois l'audit apparu et ne reposte jamais. S'il n'apparaît "
                "jamais, une intervention manuelle est nécessaire (inspecter les "
                "commentaires de l'Epic et le fichier d'intention locale). "
                f"`{status_command}` relit cet état."]
    if state.kind in {"pending", "closed"}:
        if state.kind == "pending":
            head = (f"État : audit provider {state.audit_id} présent, Epic pas encore done : "
                    "clôture en attente.")
            tail = "elle reprend la clôture à partir de l'audit."
        else:
            head = (f"État : audit provider {state.audit_id} présent et Epic done : "
                    "clôture effective.")
            tail = "elle rejoue et vérifie le reçu (aucun second audit)."
        bound = ", ".join(state.waived) or "aucun"
        if refused:
            return [f"{head} {wait}Ne relance pas la commande refusée telle quelle. "
                    f"Ensemble --accept-override lié à l'audit : {bound}. Si ta commande "
                    f"en différait, relance avec `{audit_command}` ; sinon résous d'abord la "
                    f"cause du refus. `{status_command}` relit l'état."]
        if audit_command is not None and audit_command != command:
            return [f"{head} {wait}Ta commande ne porte pas l'ensemble --accept-override "
                    f"lié à l'audit ({bound}) et serait refusée : relance avec "
                    f"`{audit_command}` ({tail[:-1]})."]
        return [f"{head} {wait}{rerun} `{command}` : {tail}"]
    known = f" ({intent})" if state.intent is not None else ""
    quota = (" Attends la réinitialisation du quota ; ne relance pas maintenant."
             if kind == "quota" else "")
    return [f"État du reçu inconnu: lecture impossible ({_redact(state.cause)}){known}. "
            "Vérifie les commentaires de l'Epic pour un audit « Foundry Epic closure audit » "
            f"avant de relancer ; ne suppose pas qu'une relance est sûre.{quota}"]


def _epic_command(tracker, issue_id, flags) -> str:
    verdict_flag = (
        " --human-verdict=accepted"
        if getattr(tracker, "bounded_epic_closure_supported", False)
        else ""
    )
    verdict_flag += "".join(
        f" {flag}" for flag in sorted(flags) if flag.startswith("--accept-override=")
    )
    return f"issue close-epic {issue_id}{verdict_flag}"


def _epic_status(tracker, issue_id):
    """Read-only `close-epic <ID> --status`: no audit / audit pending / closed."""
    if not (getattr(tracker, "epic_closure_supported", False)
            or getattr(tracker, "bounded_epic_closure_supported", False)):
        raise SystemExit(
            f"⛔ Clôture Epic indisponible pour le tracker {tracker.name} : "
            "aucune capacité de clôture auditée n'est qualifiée."
        )
    state = write.epic_closure_state(tracker, issue_id)
    if state.kind == "unknown":
        raise SystemExit(
            f"⛔ Statut de clôture illisible pour {issue_id}. "
            + _state_lines(state, "")[0]
        )
    waived = ", ".join(state.waived) or "aucun"
    if state.kind in {"none", "intent-only"}:
        extra = (
            f" · intention locale {state.intent} (audit attendu {state.intent_audit_id}) "
            "sans audit visible : effet ambigu ; une relance est refusée tant que "
            "l'audit attendu n'est pas visible"
            if state.kind == "intent-only" else ""
        )
        print(f"aucun audit · Epic {issue_id}{extra}")
        return
    label = "clos" if state.kind == "closed" else "audit en attente"
    print(f"{label} · Epic {issue_id} · audit {state.audit_id} · dérogations liées au "
          f"reçu : {waived}")


def close_epic(issue_id, flags=()):
    """Close a non-code Epic through a qualified audited tracker capability."""
    tracker = foundry.tracker()
    if "--status" in flags:
        if flags != {"--status"}:
            raise SystemExit(
                "⛔ --status est en lecture seule et n'accepte aucun autre flag : le reçu "
                "d'audit se lit sans --human-verdict ni --accept-override."
            )
        return _epic_status(tracker, issue_id)
    verdicts = [flag.removeprefix("--human-verdict=") for flag in flags
                if flag.startswith("--human-verdict=")]
    if len(verdicts) > 1:
        raise SystemExit("⛔ Clôture Epic refusée : verdict humain ambigu.")
    waivers = [flag.removeprefix("--accept-override=") for flag in flags
               if flag.startswith("--accept-override=")]
    if len(waivers) > 1:
        raise SystemExit("⛔ Clôture Epic refusée : --accept-override ambigu.")
    command = _epic_command(tracker, issue_id, flags)
    status_command = f"issue close-epic {issue_id} --status"
    human_verdict = verdicts[0] if verdicts else None
    accept_overrides = write.parse_accept_overrides(waivers[0]) if waivers else None
    try:
        # Pure local validation: a refusal here happened before any provider read or
        # write, so it carries no closure state (and costs no provider call).
        write.validate_epic_closure_request(
            tracker, issue_id, human_verdict, accept_overrides,
        )
    except EpicClosureUnavailableError:
        pass  # reported once below by write.close_epic itself
    try:
        outcome = write.close_epic(
            tracker, issue_id, human_verdict=human_verdict,
            accept_overrides=accept_overrides,
        )
    except EpicClosureUnavailableError:
        raise SystemExit(
            f"⛔ Clôture Epic indisponible pour le tracker {tracker.name} : "
            "aucune capacité de clôture auditée n'est qualifiée."
        ) from None
    except SystemExit as exc:
        if not isinstance(exc.code, str):
            raise
        state = write.epic_closure_state(tracker, issue_id)
        raise SystemExit(
            exc.code + "\n" + "\n".join(_state_lines(
                state, command, refused=True,
                audit_command=_audit_command(tracker, issue_id, state),
                status_command=status_command,
            ))
        ) from None
    except Exception as exc:
        typed = _provider_cause(exc)
        if isinstance(exc, TrackerConflictError) and typed is None:
            # Never mask the real cause (PAT-95): name it, with its direct cause.
            cause = _redact(str(exc) or type(exc).__name__)
            if exc.__cause__ is not None and str(exc.__cause__):
                cause += f" <- {_redact(exc.__cause__)}"
            state = write.epic_closure_state(tracker, issue_id)
            raise SystemExit(
                f"⛔ Clôture Epic refusée. Cause : {cause}\n"
                "Si le parent, ses preuves ou son graphe complet a changé (ou si une lecture "
                "a échoué), recharge le graphe puis relance la commande.\n"
                + "\n".join(_state_lines(
                    state, command, refused=True,
                    audit_command=_audit_command(tracker, issue_id, state),
                    status_command=status_command,
                ))
            ) from None
        cause, kind = _cause_text(exc)
        state = write.epic_closure_state(tracker, issue_id)
        lines = _state_lines(
            state, command, kind,
            audit_command=_audit_command(tracker, issue_id, state),
            status_command=status_command,
        )
        if kind == "transient" and state.kind in {"none", "intent-only", "pending", "closed"}:
            lines[0] += " L'erreur est possiblement transitoire."
        raise SystemExit(
            f"⛔ Clôture Epic interrompue pour {issue_id}. Cause : {cause}\n"
            + "\n".join(lines)
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
    epic_verdict_flags = (
        {flag for flag in flags
         if flag == "--status"
         or flag.startswith(("--human-verdict=", "--accept-override="))}
        if cmd == "close-epic" else set()
    )
    unknown = flags - _KNOWN_FLAGS.get(cmd, set()) - reason_flags - epic_verdict_flags
    if unknown:  # a misspelled flag must fail loudly, not run as a normal gated call
        raise SystemExit(f"⛔ Flag(s) inconnus pour '{cmd}' : {', '.join(sorted(unknown))}. "
                         f"Autorisés : {', '.join(sorted(_KNOWN_FLAGS.get(cmd, set()))) or '(aucun)'}.")
    args = [a for a in rest if not a.startswith("--")]
    {"start": start, "openpr": openpr, "merge": merge,
     "close-epic": close_epic}[cmd](*args, flags=flags)
