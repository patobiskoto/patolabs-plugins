"""GitHub adapter for the CodeHost port — all via `gh api` REST (never GraphQL).

Rule inherited from the project: `gh pr create` / `gh pr merge` / `gh pr checks`
go through GraphQL and are banned; every call here is `gh api -X … REST`.
"""
from __future__ import annotations

import datetime
import json
import subprocess
import urllib.parse

from foundry.models import Check, PullRequest
from foundry.codehosts.base import CodeHost


class GitHubCodeHost(CodeHost):
    name = "github"
    _MAX_PR_PAGES = 10

    def __init__(self, cwd: str | None = None):
        self.cwd = cwd

    def _gh(self, *args, check=True):
        r = subprocess.run(("gh", *args), cwd=self.cwd, capture_output=True, text=True)
        if check and r.returncode != 0:
            raise RuntimeError(f"$ gh {' '.join(args)}\n{r.stderr or r.stdout}")
        return r.stdout.strip()

    @staticmethod
    def parse_owner_repo(url: str) -> str:
        # robust to https and ssh (incl. ssh-config aliases): drop everything up to a
        # scp-style "host:" separator, then take the last two path segments
        tail = url.split("github.com", 1)[-1].removesuffix(".git")
        if ":" in tail:
            tail = tail.rsplit(":", 1)[-1]
        parts = [p for p in tail.split("/") if p]
        if len(parts) < 2:
            raise RuntimeError(f"Impossible de résoudre owner/repo depuis '{url}'.")
        return "/".join(parts[-2:])

    def resolve_repo(self, cwd: str | None = None) -> str:
        r = subprocess.run(("git", "config", "remote.origin.url"),
                           cwd=cwd or self.cwd, capture_output=True, text=True)
        return self.parse_owner_repo(r.stdout.strip())

    def open_pr(self, repo, head, base, title, body) -> PullRequest:
        out = self._gh("api", "-X", "POST", f"repos/{repo}/pulls",
                       "-f", f"title={title}", "-f", f"head={head}",
                       "-f", f"base={base}", "-f", f"body={body}",
                       "--jq", "{number,html_url,head:.head.sha,base_sha:.base.sha}")
        d = json.loads(out)
        return PullRequest(number=d["number"], url=d["html_url"],
                           head=head, base=base, base_sha=d.get("base_sha"),
                           sha=d.get("head"), state="open")

    @staticmethod
    def _pull_request(value: dict) -> PullRequest:
        return PullRequest(
            number=value["number"], url=value["html_url"],
            head=value["head"], base=value["base"],
            base_sha=value.get("base_sha"), sha=value.get("sha"),
            merged=bool(value.get("merged", False)),
            merge_sha=value.get("merge_sha"), state=value.get("state", "open"),
        )

    def list_prs(self, repo, head) -> list[PullRequest]:
        owner = repo.split("/", 1)[0]
        qualified = urllib.parse.quote(f"{owner}:{head}", safe="")
        values = []
        for page in range(1, self._MAX_PR_PAGES + 1):
            out = self._gh(
                "api", f"repos/{repo}/pulls?state=all&head={qualified}"
                       f"&per_page=100&page={page}",
                "--jq", "[.[] | {number,html_url,head:.head.ref,base:.base.ref,"
                        "base_sha:.base.sha,sha:.head.sha,state,merged,"
                        "merge_sha:.merge_commit_sha}]",
            )
            batch = json.loads(out or "[]")
            values.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise RuntimeError("Liste des PR candidates trop volumineuse ; refus borné.")
        return [self._pull_request(value) for value in values]

    def update_pr(self, repo, number, title, body) -> PullRequest:
        self._gh(
            "api", "-X", "PATCH", f"repos/{repo}/pulls/{number}",
            "-f", f"title={title}", "-f", f"body={body}",
        )
        return self.get_pr(repo, number)

    def get_pr(self, repo, number) -> PullRequest:
        out = self._gh("api", f"repos/{repo}/pulls/{number}",
                       "--jq", "{number,html_url,head:.head.ref,base:.base.ref,"
                               "base_sha:.base.sha,sha:.head.sha,state,merged,"
                               "merge_sha:.merge_commit_sha}")
        d = json.loads(out)
        return PullRequest(number=d["number"], url=d["html_url"], head=d["head"],
                           base=d["base"], base_sha=d.get("base_sha"), sha=d["sha"],
                           merged=d["merged"], merge_sha=d.get("merge_sha"),
                           state=d.get("state", "closed" if d["merged"] else "open"))

    _PER_PAGE = 100

    def _check_run_records(self, endpoint) -> list[dict]:
        """Read every check-run from an endpoint, preserving IDs for deduplication."""
        records, page = [], 1
        while True:
            out = self._gh("api",
                           f"{endpoint}?per_page={self._PER_PAGE}&page={page}",
                           "--jq", "{total: .total_count, "
                                   "batch: [.check_runs[] "
                                   "| {id, name, status, conclusion}]}")
            d = json.loads(out or '{"total": 0, "batch": []}')
            records.extend(d["batch"])
            if len(records) >= d["total"] or not d["batch"]:
                return records
            page += 1

    @staticmethod
    def _not_found(error: RuntimeError) -> bool:
        return "HTTP 404" in str(error)

    def _check_runs_via_suites(self, repo, sha) -> list[Check]:
        """Fallback when the aggregate commit/check-runs route is unavailable.

        A SHA can have several suites for the same app after a workflow rerun. Keep
        the newest run for each (app, check-name), matching the aggregate endpoint's
        latest-only semantics rather than letting an obsolete red run block a newer
        green one.
        """
        suites, page = [], 1
        while True:
            out = self._gh(
                "api",
                f"repos/{repo}/commits/{sha}/check-suites"
                f"?per_page={self._PER_PAGE}&page={page}",
                "--jq", "{total: .total_count, batch: [.check_suites[] "
                        "| {id, app: (.app.slug // .app.name // \"unknown\")}]}",
            )
            d = json.loads(out or '{"total": 0, "batch": []}')
            suites.extend(d["batch"])
            if len(suites) >= d["total"] or not d["batch"]:
                break
            page += 1

        latest = {}
        for suite in suites:
            endpoint = f"repos/{repo}/check-suites/{suite['id']}/check-runs"
            for run in self._check_run_records(endpoint):
                key = (suite["app"], run["name"])
                previous = latest.get(key)
                if previous is None or int(run["id"]) > int(previous["id"]):
                    latest[key] = run
        return [Check(name=run["name"], status=run["status"],
                      conclusion=run.get("conclusion"))
                for run in latest.values()]

    def check_runs(self, repo, sha) -> list[Check]:
        # paginate explicitly (API default: 30/page) and terminate on the response's
        # OWN total_count — trusting the batch size would truncate silently if the
        # server clamps per_page, and a red check would slip past the gate
        endpoint = f"repos/{repo}/commits/{sha}/check-runs"
        try:
            records = self._check_run_records(endpoint)
        except RuntimeError as error:
            if not self._not_found(error):
                raise
            records = None
        if records is None:
            return self._check_runs_via_suites(repo, sha)
        return [Check(name=run["name"], status=run["status"],
                      conclusion=run.get("conclusion"))
                for run in records]

    def commit_statuses(self, repo, sha) -> list[Check]:
        # the legacy status API (Jenkins, CircleCI…) — invisible to check-runs.
        # The combined endpoint returns the LATEST status per context; termination
        # follows the response's own total_count, same rationale as check_runs.
        # state: pending | success | failure | error → normalized to Check.
        checks, page = [], 1
        while True:
            out = self._gh("api",
                           f"repos/{repo}/commits/{sha}/status"
                           f"?per_page={self._PER_PAGE}&page={page}",
                           "--jq", "{total: .total_count, "
                                   "batch: [.statuses[] | {context, state}]}")
            d = json.loads(out or '{"total": 0, "batch": []}')
            for st in d["batch"]:
                if st.get("state") == "pending":
                    checks.append(Check(name=st["context"], status="in_progress",
                                        conclusion=None))
                else:
                    ok = st.get("state") == "success"
                    checks.append(Check(name=st["context"], status="completed",
                                        conclusion="success" if ok else "failure"))
            if len(checks) >= d["total"] or not d["batch"]:
                return checks
            page += 1

    # a queued-empty suite older than this is steady-state noise (schedule-only
    # workflows keep one forever), not the post-push window this guard protects
    _QUEUE_GRACE_S = 900

    @classmethod
    def _suite_signals_ci(cls, suite, now=None) -> bool:
        """Does this check-suite signal ACTIVE CI for the commit? True for a suite
        with runs or in progress, or freshly queued (post-push window). False for
        completed-with-zero-runs and for stale queued-empty suites — refusing the
        waiver on those would deadlock repos whose workflows never match the push."""
        if (suite.get("latest_check_runs_count") or 0) > 0:
            return True
        status = suite.get("status")
        if status == "in_progress":
            return True
        if status != "queued":
            return False  # completed with zero runs: nothing ran, nothing coming
        created = suite.get("created_at")
        try:
            ts = datetime.datetime.fromisoformat((created or "").replace("Z", "+00:00"))
        except ValueError:
            return True  # undatable queued suite: err on the refusing side
        now = now or datetime.datetime.now(datetime.timezone.utc)
        return (now - ts).total_seconds() < cls._QUEUE_GRACE_S

    def ci_expected(self, repo, sha) -> bool:
        # Check suites are the post-push signal used to refuse --allow-no-ci.
        # Consume the complete, counted listing before making that decision: a
        # truncated response would otherwise silently waive an active suite.
        suites, seen_ids, page, total = [], set(), 1, None
        endpoint = f"repos/{repo}/commits/{sha}/check-suites"
        while total is None or len(suites) < total:
            out = self._gh(
                "api", f"{endpoint}?per_page={self._PER_PAGE}&page={page}",
                "--jq", "{total: .total_count, batch: [.check_suites[] "
                        "| {id, status, latest_check_runs_count, created_at}]}",
            )
            try:
                response = json.loads(out)
            except (TypeError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    f"Invalid check-suites response on page {page}."
                ) from error
            if not isinstance(response, dict):
                raise RuntimeError(f"Invalid check-suites response on page {page}.")
            response_total = response.get("total")
            batch = response.get("batch")
            if (not isinstance(response_total, int) or isinstance(response_total, bool)
                    or response_total < 0 or not isinstance(batch, list)):
                raise RuntimeError(f"Invalid check-suites response on page {page}.")
            if total is None:
                total = response_total
            elif response_total != total:
                raise RuntimeError("Check-suites total_count changed during pagination.")
            if not batch and len(suites) < total:
                raise RuntimeError(
                    f"Check-suites page {page} was empty before total_count {total}."
                )
            for suite in batch:
                suite_id = suite.get("id") if isinstance(suite, dict) else None
                if (not isinstance(suite_id, int) or isinstance(suite_id, bool)):
                    raise RuntimeError(
                        f"Invalid check-suite record on page {page}: missing id."
                    )
                status = suite.get("status")
                runs_count = suite.get("latest_check_runs_count")
                created_at = suite.get("created_at")
                if (not isinstance(status, str)
                        or status not in {"queued", "in_progress", "completed"}
                        or not isinstance(runs_count, int)
                        or isinstance(runs_count, bool) or runs_count < 0
                        or created_at is not None and not isinstance(created_at, str)):
                    raise RuntimeError(
                        f"Invalid check-suite record on page {page}."
                    )
                if suite_id in seen_ids:
                    raise RuntimeError(
                        f"Repeated check-suite id {suite_id} on page {page}."
                    )
                seen_ids.add(suite_id)
                suites.append(suite)
            if len(suites) > total:
                raise RuntimeError("Check-suites exceeded total_count during pagination.")
            page += 1
        return any(self._suite_signals_ci(suite) for suite in suites)

    def merge_pr(self, repo, number, method="squash", sha=None) -> PullRequest:
        args = ["api", "-X", "PUT", f"repos/{repo}/pulls/{number}/merge",
                "-f", f"merge_method={method}"]
        if sha:
            # GitHub refuses (409) if the head moved past this sha — the CI-gated
            # commit is exactly the one that merges, never a sneaked-in later push.
            args += ["-f", f"sha={sha}"]
        out = self._gh(*args, "--jq", "{merged,sha}")
        d = json.loads(out)
        pr = self.get_pr(repo, number)
        pr.merged = d.get("merged", False)
        pr.sha = d.get("sha", pr.sha)
        return pr

    def delete_branch(self, repo, branch) -> None:
        self._gh("api", "-X", "DELETE", f"repos/{repo}/git/refs/heads/{branch}", check=False)
