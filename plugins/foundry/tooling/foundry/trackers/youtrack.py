"""YouTrack adapter for the Tracker port.

Translates YouTrack's custom-field / article shapes to and from Foundry's
normalized models. This is the ONLY file in the tooling that knows YouTrack
exists — swapping trackers means writing a sibling of this file, nothing else.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from foundry import config, registry
from foundry.routing import RoutingConfigError, synchronize_acceptance_body
from foundry.models import Adr, Issue, Link, Project
from foundry.trackers.base import IssueUnavailableError, Tracker, TrackerConflictError

# normalized field name -> the YouTrack customField $type to send on writes
_FIELD_TYPES = {
    "State": "StateIssueCustomField",
    "Priority": "SingleEnumIssueCustomField",
    "Type": "SingleEnumIssueCustomField",
    "Milestone": "SingleEnumIssueCustomField",
    "Estimate": "SimpleIssueCustomField",
}
_ENUM_VALUE = {"State", "Priority", "Type", "Milestone"}  # value = {"name": x}, else raw

# link_type -> YouTrack command role phrase (applied to the source issue)
_LINK_ROLE = {
    "subtask-of": "subtask of",
    "parent-of": "parent for",
    "depends-on": "depends on",
    "blocks": "is required for",
    "relates": "relates to",
}
# the reverse, for reads: YouTrack role phrase -> normalized Link.type. Emitting the
# normalized form is the Tracker contract (models.Link) — the query tier keys off it,
# so a raw provider phrase leaking through would silently break blocked_by/unlocks.
_ROLE_TO_TYPE = {phrase: ltype for ltype, phrase in _LINK_ROLE.items()}

_ISSUE_FIELDS = ("idReadable,summary,description,created,updated,"
                 "customFields(name,value(name,minutes)),"
                 "links(direction,linkType(name,sourceToTarget,targetToSource),"
                 "issues(idReadable))")

# Keep this in lockstep with routing._AC_LINE: every checkbox accepted by the
# proof grammar must count towards the tracker progress gate as well.
_AC_CHECKBOX = re.compile(r"(?m)^\s*[-*+]\s+\[(?P<mark>[ xX])\]\s+.+?\s*$")


class _YouTrackHTTPError(RuntimeError):
    """HTTP failure retaining its status for operation-specific translation."""

    def __init__(self, method: str, path: str, status: int, response_body: str):
        self.status = status
        super().__init__(f"{method} {path} -> HTTP {status}: {response_body}")


class _YouTrackRedirectError(RuntimeError):
    """A sanitized refusal raised before following an unsafe redirect."""

    def __init__(self):
        super().__init__("refused cross-origin YouTrack redirect")


def _url_origin(url: str) -> tuple[str, str, int | None]:
    """Normalize an HTTP URL origin, including its effective default port."""
    try:
        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except (AttributeError, TypeError, UnicodeError, ValueError):
        raise _YouTrackRedirectError() from None
    if scheme not in {"http", "https"} or not host:
        raise _YouTrackRedirectError()
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
    return scheme, host, port


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Preserve urllib redirect behavior only inside the request's origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            target_url = urllib.parse.urljoin(req.full_url, newurl)
        except (TypeError, ValueError):
            raise _YouTrackRedirectError() from None
        if _url_origin(req.full_url) != _url_origin(target_url):
            raise _YouTrackRedirectError()
        return super().redirect_request(
            req, fp, code, msg, headers, target_url
        )


class YouTrackTracker(Tracker):
    name = "youtrack"
    requires_mutation_binding = True
    # YouTrack exposes neither an atomic parent+children compare-and-transition nor
    # a provider-verified receipt store. A read-then-command emulation would race.
    epic_closure_supported = False
    # YouTrack exposes no compare-and-swap precondition.  update_body therefore
    # serializes Foundry writers through a local file lock and uses read-verify-write-readback;
    # another client can still race between those requests.
    acceptance_sync_supported = True
    project_provisioning_supported = True

    def provision_project(
        self, name: str, key: str, canonical_repository: str | None = None,
    ) -> Project:
        from foundry.trackers.youtrack_provisioning import provision_youtrack_project

        return provision_youtrack_project(self, name, key)

    def __init__(self, *, url: str | None = None, token: str | None = None):
        if (url is None) != (token is None):
            raise ValueError("url and token must be supplied together")
        self.url = (url if url is not None else config.require("YOUTRACK_URL")).rstrip(
            "/"
        )
        self.token = token if token is not None else config.require("YOUTRACK_TOKEN")

    # ---- raw REST -------------------------------------------------------
    def _req(self, method, path, body=None, fields=None, top=None):
        # YouTrack caps unpaginated collections at a small default page size —
        # list endpoints must pass an explicit $top or they truncate silently.
        params = []
        if fields:
            params.append("fields=" + urllib.parse.quote(fields))
        if top:
            params.append(f"$top={top}")
        q = ("?" + "&".join(params)) if params else ""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.url}/api{path}{q}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            opener = urllib.request.build_opener(_SameOriginRedirectHandler())
            with opener.open(req) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise _YouTrackHTTPError(method, path, e.code, e.read().decode()) from None

    def _search_raw(self, query, fields, top=1000):
        if isinstance(top, bool) or not isinstance(top, int) or top < 1:
            raise RuntimeError("pagination YouTrack invalide : $top doit être positif.")

        results = []
        seen_ids = set()
        skip = 0
        while True:
            qs = urllib.parse.urlencode({
                "query": query,
                "fields": fields,
                "$top": str(top),
                "$skip": str(skip),
            })
            page = self._req("GET", f"/issues?{qs}")
            if not isinstance(page, list):
                raise RuntimeError(
                    "page invalide pendant la pagination YouTrack : liste attendue."
                )

            for issue in page:
                issue_id = issue.get("idReadable") if isinstance(issue, dict) else None
                if not isinstance(issue_id, str) or not issue_id:
                    raise RuntimeError(
                        "page invalide pendant la pagination YouTrack : "
                        "idReadable non vide attendu."
                    )
                if issue_id in seen_ids:
                    raise RuntimeError(
                        "la pagination YouTrack n'avance pas : "
                        f"l'issue {issue_id} a été répétée."
                    )
                seen_ids.add(issue_id)

            results.extend(page)
            if len(page) < top:
                return results
            skip += len(page)

    # ---- normalization --------------------------------------------------
    @staticmethod
    def _cf(raw, name):
        for c in raw.get("customFields", []):
            if c["name"] == name:
                v = c.get("value")
                return v.get("name") if isinstance(v, dict) else v
        return None

    @staticmethod
    def _ac_counts(body):
        if not body:
            return 0, 0
        # routing.acceptance_criteria applies _AC_LINE to split lines.  Match the
        # same way here so ``\s`` can accept indentation without ever consuming a
        # newline and assembling a checkbox from separate Markdown lines.
        marks = [
            match.group("mark")
            for line in body.splitlines()
            if (match := _AC_CHECKBOX.match(line)) is not None
        ]
        done = sum(mark in {"x", "X"} for mark in marks)
        todo = sum(mark == " " for mark in marks)
        return done, done + todo

    @staticmethod
    def _links(raw):
        out = []
        for lk in raw.get("links", []):
            lt = lk.get("linkType") or {}
            direction = lk.get("direction", "")
            role = (lt.get("sourceToTarget") if direction == "OUTWARD"
                    else lt.get("targetToSource")) or lt.get("name", "")
            for tgt in lk.get("issues", []):
                out.append(Link(type=_ROLE_TO_TYPE.get(role, role),
                                direction=direction.lower(),
                                target=tgt["idReadable"]))
        return out

    def _to_issue(self, raw):
        body = raw.get("description") or ""
        done, total = self._ac_counts(body)
        labels = self._cf(raw, "Labels")
        return Issue(
            id=raw["idReadable"], title=raw.get("summary", ""),
            state=self._cf(raw, "State"), priority=self._cf(raw, "Priority"),
            estimate=self._cf(raw, "Estimate"), milestone=self._cf(raw, "Milestone"),
            type=self._cf(raw, "Type"),
            labels=[x.strip() for x in labels.split(",")] if labels else [],
            ac_done=done, ac_total=total, links=self._links(raw),
            pr_url=self._cf(raw, "GitHub PR"), body=body,
            created=raw.get("created"), updated=raw.get("updated"))

    # ---- Tracker port ---------------------------------------------------
    def verify_project_identity(self, project: Project) -> bool:
        """Confirm an explicitly supplied native id and key refer to one project.

        This provider-specific read is used by destructive test infrastructure so a
        mistyped native id cannot redirect writes to a different project.  It is not
        part of the tracker port and does not consult the repository registry.
        """
        project_id = urllib.parse.quote(project.id, safe="")
        raw = self._req(
            "GET", f"/admin/projects/{project_id}", fields="id,shortName"
        )
        return raw.get("id") == project.id and raw.get("shortName") == project.key

    def resolve_project(self, repo: str) -> Project:
        return registry.resolve("youtrack", repo)

    def resolve_checkout_project(
        self, cwd: str | None = None, *, checkout_identity: str | None = None,
    ) -> Project:
        """Resolve the actual checkout without trusting ``PROJECT_REPO``.

        Read resolution deliberately accepts a historical alias. The write tier
        separately invokes :meth:`validate_mutation_project` before any mutation.
        """
        try:
            observed = (
                registry.checkout_repository_identity(cwd)
                if checkout_identity is None else
                registry.canonical_repository_identity(checkout_identity)
            )
        except ValueError:
            raise SystemExit(
                "Binding YouTrack refusé : identité canonique du checkout "
                "invalide ou absente."
            ) from None
        repo = observed.rsplit("/", 1)[-1]
        return registry.resolve(self.name, repo, cwd=cwd)

    def validate_mutation_repository(self, repo: str, checkout_identity: str) -> None:
        del repo
        self.resolve_checkout_project(checkout_identity=checkout_identity)

    def validate_mutation_project(self, project: Project) -> None:
        registry.require_writable_project(self.name, project)

    def search(self, project: Project, query: str = "", page_size: int = 1000) -> list[Issue]:
        """Read every issue page; ``page_size`` is useful for read-only smoke tests."""
        q = f"project: {project.key}" + (f" {query}" if query else "")
        return [self._to_issue(r) for r in self._search_raw(q, _ISSUE_FIELDS, page_size)]

    def get_issue(self, issue_id: str) -> Issue:
        try:
            raw = self._req("GET", f"/issues/{issue_id}",
                            fields=_ISSUE_FIELDS + ",comments(text,created)")
        except _YouTrackHTTPError as exc:
            if exc.status in {403, 404}:
                raise IssueUnavailableError(issue_id) from None
            raise
        issue = self._to_issue(raw)
        issue.comments = [{"text": c.get("text"), "created": c.get("created")}
                          for c in (raw.get("comments") or [])[-10:]]
        return issue

    def _cf_write(self, name, value):
        vtype = _FIELD_TYPES.get(name, "SimpleIssueCustomField")
        v = {"name": value} if name in _ENUM_VALUE else value
        return {"name": name, "$type": vtype, "value": v}

    def _ensure_milestone(self, value, project: Project | None = None):
        """Milestone is a per-project enum; add unseen values on the fly so `frame`
        can name any milestone without a separate admin step."""
        if not value:
            return
        p = project
        if p is None:
            try:
                p = registry.resolve("youtrack", registry.repo_basename())
            except SystemExit:
                return
        bundle = p.extra.get("ms_bundle")
        if not bundle:
            return
        try:
            self._req("POST", f"/admin/customFieldSettings/bundles/enum/{bundle}/values",
                      {"name": value}, "name")
        except RuntimeError as e:
            if "unique" not in str(e):
                raise

    def _prewrite(self, fields, project: Project | None = None):
        if fields and fields.get("Milestone"):
            self._ensure_milestone(fields["Milestone"], project)

    def create_issue(self, project: Project, title: str, body: str,
                     fields: dict | None = None, parent: str | None = None) -> Issue:
        self._prewrite(fields, project)  # the given project, NOT the cwd's
        cfs = [self._cf_write(k, v) for k, v in (fields or {}).items() if v is not None]
        raw = self._req("POST", "/issues",
                        {"project": {"id": project.id}, "summary": title,
                         "description": body, "customFields": cfs}, "idReadable")
        issue = self.get_issue(raw["idReadable"])
        if parent:
            self.link(issue.id, "subtask-of", parent, project=project)
        return issue

    def update_fields(
        self, issue_id: str, fields: dict, project: Project | None = None,
    ) -> Issue:
        self._prewrite(fields, project)
        cfs = [self._cf_write(k, v) for k, v in fields.items() if v is not None]
        self._req("POST", f"/issues/{issue_id}", {"customFields": cfs}, "idReadable")
        return self.get_issue(issue_id)

    def set_state(
        self, issue_id: str, state: str, context=None, project: Project | None = None,
    ) -> None:
        self.update_fields(issue_id, {"State": state}, project=project)

    def link(
        self, src_id: str, link_type: str, dst_id: str,
        project: Project | None = None,
    ) -> None:
        role = _LINK_ROLE.get(link_type, link_type)
        self._req("POST", "/commands",
                  {"query": f"{role} {dst_id}", "issues": [{"idReadable": src_id}]})

    def add_comment(
        self, issue_id: str, text: str, project: Project | None = None,
    ) -> None:
        self._req("POST", f"/issues/{issue_id}/comments", {"text": text}, "id")

    @contextmanager
    def _body_lock(self, resource_type: str, resource_id: str):
        """Serialize local CLI processes mutating one provider resource.

        This advisory ``flock`` is deliberately local to Foundry's state directory;
        it cannot turn YouTrack's non-CAS endpoint into a distributed lock.
        """
        digest = hashlib.sha256(
            f"{resource_type}\0{resource_id}".encode("utf-8")
        ).hexdigest()
        directory = Path(registry.data_dir()) / "youtrack-body-locks"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        descriptor = os.open(directory / f"{digest}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def update_body(
        self, resource: Issue | Adr, expected_body: str, updated_body: str,
        project: Project | None = None,
    ) -> bool:
        """Boundedly replace an issue description or ADR article content.

        YouTrack's public REST API has no provider-side CAS/version precondition.
        Foundry serializes its local writers per resource, then performs exactly
        one read-verify-write-readback sequence.  A concurrent non-Foundry writer
        can still win between requests: divergences refuse with
        ``TrackerConflictError`` and this method never issues a second write.
        """
        if not isinstance(expected_body, str) or not isinstance(updated_body, str):
            raise ValueError("corps attendu et voulu doivent être des chaînes")
        if isinstance(resource, Issue):
            resource_type, resource_id = "issue", resource.id
            if not resource_id:
                raise ValueError("issue sans identifiant")

            def read_body():
                return self.get_issue(resource_id).body

            def write_body():
                self._req("POST", f"/issues/{urllib.parse.quote(resource_id, safe='')}",
                          {"description": updated_body}, "idReadable")
        elif isinstance(resource, Adr):
            resource_type, resource_id = "adr", resource.ref or ""
            if not resource_id:
                raise ValueError(f"ADR {resource.id} sans ref native")

            def read_body():
                current = self._req(
                    "GET", f"/articles/{urllib.parse.quote(resource_id, safe='')}",
                    fields="content",
                )
                return (current.get("content") or "") if isinstance(current, dict) else None

            def write_body():
                self._req("POST", f"/articles/{urllib.parse.quote(resource_id, safe='')}",
                          {"content": updated_body})
        else:
            raise TypeError("update_body attend une Issue ou une Adr")

        with self._body_lock(resource_type, resource_id):
            current_body = read_body()
            if current_body != expected_body:
                raise TrackerConflictError(
                    "corps YouTrack modifié ; recharge puis relance la mutation"
                )
            if current_body == updated_body:
                return False
            write_body()
            if read_body() != updated_body:
                raise TrackerConflictError(
                    "corps YouTrack divergent après écriture ; aucune seconde tentative"
                )
        return True

    def sync_acceptance_body(
        self, issue_id: str, expected_body: str, updated_body: str, proof: dict,
        project: Project | None = None,
    ) -> bool:
        """Apply a proof-authorized checkbox-only change through ``update_body``."""
        proof_id = proof.get("proof_id") if isinstance(proof, dict) else None
        if not isinstance(proof_id, str) or not re.fullmatch(r"[0-9a-f]{64}", proof_id):
            raise ValueError("proof_id AC invalide")
        try:
            authorized_body, _ = synchronize_acceptance_body(issue_id, expected_body, proof)
        except RoutingConfigError as exc:
            raise ValueError("synchronisation AC refusée : preuve invalide") from exc
        if authorized_body != updated_body:
            raise ValueError("synchronisation AC refusée : diff hors marqueurs autorisés")
        return self.update_body(
            Issue(id=issue_id, title=""), expected_body, updated_body, project=project,
        )

    # ---- ADR (YouTrack Knowledge Base articles) -------------------------
    def _adr_prefix(self, project: Project) -> str:
        return f"{project.key}-ADR"

    def _project_articles_raw(self, project: Project, fields: str, top: int = 1000):
        """Return every article from YouTrack's project-scoped, paginated API.

        Article ids are checked while paging, before normalizing any ADRs: a
        malformed or repeating page must fail the whole read rather than cause
        a partial result or an unbounded loop.
        """
        if isinstance(top, bool) or not isinstance(top, int) or top < 1:
            raise RuntimeError("pagination YouTrack invalide : $top doit être positif.")

        results = []
        seen_ids = set()
        skip = 0
        article_path = "/admin/projects/{}/articles".format(
            urllib.parse.quote(project.id, safe="")
        )
        while True:
            qs = urllib.parse.urlencode({
                "fields": fields,
                "$top": str(top),
                "$skip": str(skip),
            })
            page = self._req("GET", f"{article_path}?{qs}")
            if not isinstance(page, list):
                raise RuntimeError(
                    "page invalide pendant la pagination YouTrack : liste attendue."
                )

            for article in page:
                article_id = article.get("idReadable") if isinstance(article, dict) else None
                if not isinstance(article_id, str) or not article_id:
                    raise RuntimeError(
                        "page invalide pendant la pagination YouTrack : "
                        "idReadable non vide attendu."
                    )
                if article_id in seen_ids:
                    raise RuntimeError(
                        "la pagination YouTrack n'avance pas : "
                        f"l'article {article_id} a été répété."
                    )
                seen_ids.add(article_id)

            results.extend(page)
            if len(page) < top:
                return results
            skip += len(page)

    def list_adrs(self, project: Project) -> list[Adr]:
        prefix = self._adr_prefix(project)
        out = []
        # The API endpoint is project-scoped. Keep the anchored prefix check as a
        # client-side invariant, protecting similarly named project keys.
        for a in self._project_articles_raw(project, "idReadable,summary,content"):
            # ANCHORED match: summaries start with the id, and an unanchored search
            # would cross-match a project whose key is a suffix of another
            # ("TOC-ADR" inside "MDTOC-ADR-0001")
            m = re.match(rf"{re.escape(prefix)}-(\d{{4}})", a.get("summary", ""))
            if not m:
                continue
            status = "proposed"
            sm = re.search(r"statut\s*:\s*`?(\w+)`?", a.get("content") or "")
            if sm:
                status = sm.group(1)
            out.append(Adr(id=f"{prefix}-{m.group(1)}", title=a["summary"],
                           status=status, body=a.get("content"), ref=a["idReadable"]))
        return sorted(out, key=lambda x: x.id)

    def create_adr(self, project: Project, title: str, body: str,
                   status: str = "proposed") -> Adr:
        existing = self.list_adrs(project)
        num = max((int(a.id.rsplit("-", 1)[1]) for a in existing), default=0) + 1
        adr_id = f"{self._adr_prefix(project)}-{num:04d}"
        summary = f"{adr_id} — {title}"
        header = f"> **ADR** · statut : `{status}`\n\n"
        art = self._req("POST", "/articles", {"summary": summary, "content": header + body,
                        "project": {"id": project.id}}, "idReadable,summary")
        return Adr(id=adr_id, title=summary, status=status, ref=art["idReadable"])

    def set_adr_status(
        self, adr: Adr, status: str, project: Project | None = None,
    ) -> None:
        if not adr.ref:
            raise RuntimeError(f"ADR {adr.id} sans ref native — impossible de mettre à jour.")
        cur = self._req("GET", f"/articles/{adr.ref}", fields="content")
        content = re.sub(r"(statut\s*:\s*`?)\w+(`?)", rf"\g<1>{status}\g<2>",
                         cur.get("content") or "", count=1)
        self.update_body(adr, cur.get("content") or "", content, project=project)
