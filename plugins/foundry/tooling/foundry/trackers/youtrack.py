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
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from foundry import config, registry
from foundry.routing import (
    RoutingConfigError,
    acceptance_criteria,
    acceptance_digest,
    synchronize_acceptance_body,
)
from foundry.models import (
    Adr,
    EpicClosureChild,
    EpicClosureDependency,
    EpicClosureOutcome,
    EpicClosureReceipt,
    Issue,
    Link,
    Project,
    ReleaseIssue,
    ReleaseScope,
    TransitionContext,
)
from foundry.trackers.base import (
    IssueUnavailableError,
    ReleaseScopeUnavailableError,
    Tracker,
    TrackerCapabilityUnavailableError,
    TrackerConflictError,
)

# normalized field name -> the YouTrack customField $type to send on writes
_FIELD_TYPES = {
    "State": "StateIssueCustomField",
    "Priority": "SingleEnumIssueCustomField",
    "Type": "SingleEnumIssueCustomField",
    "Milestone": "SingleEnumIssueCustomField",
    "Estimate": "SimpleIssueCustomField",
}
_ENUM_VALUE = {"State", "Priority", "Type", "Milestone"}  # value = {"name": x}, else raw
_PORTABLE_FIELD_NAMES = frozenset({
    "State", "Priority", "Type", "Milestone", "Estimate", "Labels", "GitHub PR",
})
_EPIC_CLOSURE_INTENT_SCHEMA = "foundry-youtrack-epic-closure-intent.v1"

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

_ISSUE_FIELDS = ("idReadable,id,summary,description,created,updated,"
                 "customFields(name,value(id,name,minutes)),"
                 "links(direction,linkType(name,sourceToTarget,targetToSource),"
                 "issues(idReadable))")
_RELEASE_ISSUE_FIELDS = (
    _ISSUE_FIELDS.replace(
        "customFields(name,value(id,name,minutes))",
        "customFields(name,projectCustomField(bundle(id)),value(id,name,minutes))",
    )
    + ",project(id,shortName)"
)

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
    # YouTrack exposes neither an atomic parent+children compare-and-transition nor
    # a provider-verified receipt store. A read-then-command emulation would race.
    epic_closure_supported = False
    # PAT-ADR-0006 explicitly authorizes this bounded path.  It is not a
    # provider transaction and retains the named S1->S2 residual race.
    bounded_epic_closure_supported = True
    # YouTrack exposes no compare-and-swap precondition.  update_body therefore
    # serializes Foundry writers through a local file lock and uses read-verify-write-readback;
    # another client can still race between those requests.
    acceptance_sync_supported = True
    # The transition itself is a targeted S1-S5 projection.  It does not turn a
    # native state into acceptance evidence; review/done still receive their
    # code-host coordinates through TransitionContext in the shared write tier.
    bounded_state_transitions = True
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
    def _cf_coordinate(raw: dict, name: str) -> tuple[str | None, str | None]:
        for field in raw.get("customFields", []):
            if field.get("name") != name:
                continue
            value = field.get("value")
            if value is None:
                return None, None
            if not isinstance(value, dict):
                raise RuntimeError(f"champ YouTrack invalide : {name}")
            identifier, title = value.get("id"), value.get("name")
            if not isinstance(identifier, str) or not isinstance(title, str):
                raise RuntimeError(f"champ YouTrack invalide : {name}")
            return identifier, title
        return None, None

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
        native_state = self._cf(raw, "State")
        acceptance_status = "unknown"
        acceptance_source = None
        acceptance_coordinates = None
        if total > 0 and done == total:
            try:
                criteria = acceptance_criteria(body)
            except RoutingConfigError:
                criteria = []
            if len(criteria) == total:
                acceptance_status = "accepted"
                acceptance_source = "youtrack-checked-body"
                acceptance_coordinates = json.dumps(
                    {
                        "ac_digest": acceptance_digest(criteria),
                        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                        "native_state": native_state,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
        return Issue(
            id=raw["idReadable"], title=raw.get("summary", ""),
            state=native_state, priority=self._cf(raw, "Priority"),
            estimate=self._cf(raw, "Estimate"), milestone=self._cf(raw, "Milestone"),
            type=self._cf(raw, "Type"),
            labels=[x.strip() for x in labels.split(",")] if labels else [],
            ac_done=done, ac_total=total, links=self._links(raw),
            pr_url=self._cf(raw, "GitHub PR"), body=body,
            created=raw.get("created"), updated=raw.get("updated"),
            # YouTrack does not expose a separate revision through this endpoint;
            # its server-issued update timestamp is the bounded closure coordinate.
            version=raw.get("updated"),
            normalized_state=native_state, native_state=native_state,
            projection_status="native-only",
            acceptance_status=acceptance_status,
            acceptance_source=acceptance_source,
            acceptance_coordinates=acceptance_coordinates)

    # ---- Tracker port ---------------------------------------------------
    def verify_project_identity(self, project: Project) -> bool:
        """Confirm an explicitly supplied native id and key refer to one project.

        The shared tracker port uses this read before V1 binding publication.
        Destructive test infrastructure also uses it to prevent a mistyped native
        id from redirecting writes. It does not consult the repository registry.
        """
        project_id = urllib.parse.quote(project.id, safe="")
        raw = self._req(
            "GET", f"/admin/projects/{project_id}", fields="id,shortName"
        )
        if raw.get("id") != project.id or raw.get("shortName") != project.key:
            return False
        mapping = project.extra.get("release_ids", {})
        bundle = project.extra.get("ms_bundle")
        if not isinstance(mapping, dict) or mapping and not isinstance(bundle, str):
            return False
        for release, release_id in mapping.items():
            try:
                native = self._req(
                    "GET",
                    "/admin/customFieldSettings/bundles/enum/"
                    f"{urllib.parse.quote(bundle, safe='')}/values/"
                    f"{urllib.parse.quote(release_id, safe='')}",
                    fields="id,name",
                )
            except _YouTrackHTTPError as exc:
                if exc.status in {403, 404}:
                    return False
                raise
            if (
                not isinstance(native, dict)
                or native.get("id") != release_id
                or native.get("name") != release
            ):
                return False
        return True

    def resolve_project(self, repo: str) -> Project:
        return registry.resolve("youtrack", repo)

    def validate_legacy_mutation(self) -> None:
        """Refuse a lifecycle write through a cutover tombstone; keep legacy resolution.

        YouTrack writes address issues directly and historically resolve no binding,
        so an unregistered checkout keeps that behaviour. When the checkout's
        historical binding (basename or ``PROJECT_REPO`` alias, case preserved) is
        itself archived, or addresses a project another alias archived, the write is
        refused before any provider effect.
        """
        repo = registry.repo_basename()
        entry = registry.load().get(self.name, {}).get(repo)
        if not isinstance(entry, dict):
            return
        if entry.get("archive") is True:
            raise SystemExit(
                f"Binding tracker archivé : '{repo}' ne peut plus recevoir "
                f"d'écriture via '{self.name}'."
            )
        registry.require_writable_project(
            self.name, Project(key=entry.get("key"), id=entry.get("id")),
        )

    def search(self, project: Project, query: str = "", page_size: int = 1000) -> list[Issue]:
        """Read every issue page; ``page_size`` is useful for read-only smoke tests."""
        q = f"project: {project.key}" + (f" {query}" if query else "")
        return [self._to_issue(r) for r in self._search_raw(q, _ISSUE_FIELDS, page_size)]

    def read_release_scope(self, project: Project, release: str) -> ReleaseScope:
        mapping = project.extra.get("release_ids")
        bundle = project.extra.get("ms_bundle")
        if not isinstance(mapping, dict) or release not in mapping:
            raise ReleaseScopeUnavailableError(self.name, release, "unmapped")
        release_id = mapping[release]
        if (
            not isinstance(release_id, str)
            or not release_id
            or not isinstance(bundle, str)
            or not bundle
        ):
            raise ReleaseScopeUnavailableError(self.name, release, "invalid_mapping")

        def braced_search_value(value: str) -> str:
            # YouTrack documents braces for attribute values that contain spaces,
            # but no escaping for a literal brace inside a complex value.  Refuse
            # any value that cannot be represented without changing its meaning.
            if (
                not isinstance(value, str)
                or not value
                or any(char in "{}" or (char.isspace() and char != " ") for char in value)
            ):
                raise ReleaseScopeUnavailableError(
                    self.name, release, "query_value_unrepresentable"
                )
            return f"{{{value}}}"

        project_query = braced_search_value(project.key)
        release_query = braced_search_value(release)
        try:
            native = self._req(
                "GET",
                "/admin/customFieldSettings/bundles/enum/"
                f"{urllib.parse.quote(bundle, safe='')}/values/"
                f"{urllib.parse.quote(release_id, safe='')}",
                fields="id,name",
            )
        except _YouTrackHTTPError as exc:
            reason = "inaccessible" if exc.status == 403 else "absent" if exc.status == 404 else "provider_error"
            raise ReleaseScopeUnavailableError(self.name, release, reason) from None
        if (
            not isinstance(native, dict)
            or native.get("id") != release_id
            or native.get("name") != release
        ):
            raise ReleaseScopeUnavailableError(self.name, release, "mapping_mismatch")
        try:
            raws = self._search_raw(
                f"project: {project_query} Milestone: {release_query}",
                _RELEASE_ISSUE_FIELDS,
            )
        except _YouTrackHTTPError as exc:
            reason = "inaccessible" if exc.status in {401, 403} else "provider_error"
            raise ReleaseScopeUnavailableError(self.name, release, reason) from None
        issues: list[ReleaseIssue] = []
        for raw in raws:
            native_project = raw.get("project") if isinstance(raw, dict) else None
            milestone = next(
                (
                    field
                    for field in raw.get("customFields", [])
                    if isinstance(field, dict) and field.get("name") == "Milestone"
                ),
                None,
            )
            project_custom_field = (
                milestone.get("projectCustomField")
                if isinstance(milestone, dict)
                else None
            )
            observed_bundle = (
                project_custom_field.get("bundle")
                if isinstance(project_custom_field, dict)
                else None
            )
            observed_id, observed_name = self._cf_coordinate(raw, "Milestone")
            if (
                not isinstance(native_project, dict)
                or native_project.get("id") != project.id
                or native_project.get("shortName") != project.key
                or not isinstance(observed_bundle, dict)
                or observed_bundle.get("id") != bundle
                or observed_id != release_id
                or observed_name != release
            ):
                raise ReleaseScopeUnavailableError(
                    self.name, release, "membership_mismatch"
                )
            issue = self._to_issue(raw)
            terminal = str(issue.state or "").casefold() in {
                "done", "completed", "fixed", "dropped",
            }
            references = {"provider_issue_id": raw.get("id")}
            if issue.pr_url:
                references["pr_url"] = issue.pr_url
            issues.append(ReleaseIssue(
                id=issue.id,
                title=issue.title,
                type=issue.type,
                state=issue.state,
                labels=tuple(issue.labels),
                # Native terminal state and a PR field are observations, not an
                # exact merged/accepted receipt.
                disposition="unavailable" if terminal else "unfinished",
                references={key: value for key, value in references.items() if value},
            ))
        return ReleaseScope(
            provider=self.name,
            project_key=project.key,
            project_id=project.id,
            release=release,
            release_id=release_id,
            native_state=None,
            issues=tuple(issues),
            closure={
                "mode": "operator",
                "native_capability": "unavailable",
                "action": "record-scope-freeze",
                "native_mutation": False,
                "preconditions": ["unfinished=0", "unavailable=0"],
                "verification": "read-release-scope",
            },
            coordinates={
                "project_id": project.id,
                "milestone_bundle_id": bundle,
                "enum_value_id": release_id,
            },
        )

    def get_issue(self, issue_id: str) -> Issue:
        try:
            raw = self._req("GET", f"/issues/{issue_id}",
                            fields=_ISSUE_FIELDS + ",comments(text,created)")
        except _YouTrackHTTPError as exc:
            if exc.status in {403, 404}:
                raise IssueUnavailableError(issue_id) from None
            raise
        issue = self._to_issue(raw)
        # Closure receipts are durable authority, so do not discard older rows
        # during normalization.  A bounded closure must either find its exact
        # audit or fail closed; a display-oriented last-ten window is unsound.
        issue.comments = [{"text": c.get("text"), "created": c.get("created")}
                          for c in (raw.get("comments") or [])]
        return issue

    def _cf_write(self, name, value):
        vtype = _FIELD_TYPES.get(name, "SimpleIssueCustomField")
        if name == "Labels" and isinstance(value, list):
            if any(not isinstance(label, str) for label in value):
                raise ValueError("YouTrack Labels must be a list of strings")
            # The portable port carries a list; the historical native custom field
            # stores that vocabulary as one comma-separated string.
            value = ",".join(value)
        v = {"name": value} if name in _ENUM_VALUE else value
        return {"name": name, "$type": vtype, "value": v}

    @staticmethod
    def _native_project(raw, resource: str) -> Project:
        """Decode one provider project coordinate without inferring it from an id.

        Readable issue and ADR identifiers are not project authority: keys can be
        ambiguous and callers may run from an unrelated (or unregistered) checkout.
        Every targeted mutation therefore derives its project from YouTrack's native
        resource coordinate before consulting the archive tombstone.
        """
        candidate = raw.get("project") if isinstance(raw, dict) else None
        if (
            not isinstance(candidate, dict)
            or not isinstance(candidate.get("id"), str)
            or not candidate["id"]
            or not isinstance(candidate.get("shortName"), str)
            or not candidate["shortName"]
        ):
            raise SystemExit(
                f"Mutation YouTrack refusée : projet natif absent pour {resource}."
            )
        return Project(key=candidate["shortName"], id=candidate["id"])

    @staticmethod
    def _require_writable_target(target: Project) -> Project:
        """Apply YouTrack's exact-coordinate archive tombstone.

        A native project can be renamed while retaining its id, and a native id can
        be replaced while the archived key is still addressed.  For this provider,
        either exact coordinate is therefore sufficient to make the tombstone
        opposable.  Keep this at the YouTrack boundary: the shared registry rule
        deliberately remains stricter for its other callers and providers.
        """
        bindings = registry.load().get("youtrack", {})
        if not isinstance(bindings, dict):
            raise SystemExit("Mutation YouTrack refusée : registre de binding invalide.")
        for entry in bindings.values():
            if not isinstance(entry, dict) or entry.get("archive") is not True:
                continue
            if entry.get("key") == target.key or entry.get("id") == target.id:
                raise SystemExit(
                    f"Mutation tracker refusée : le projet '{target.key}' est une "
                    "archive lisible via 'youtrack'."
                )
        return target

    def _issue_target_project(
        self, issue_id: str,
    ) -> Project:
        raw = self._req(
            "GET", f"/issues/{urllib.parse.quote(issue_id, safe='')}",
            fields="project(id,shortName)",
        )
        return self._require_writable_target(
            self._native_project(raw, f"l'issue '{issue_id}'"),
        )

    def validate_issue_binding(self, project: Project, *issue_ids: str) -> None:
        """Bind common write-tier issue operations to the repository project.

        A V1 repository marker makes the factory enable ``requires_mutation_binding``;
        the shared write tier then calls this hook before invoking the mutation.  Direct
        adapter calls outside that bound path retain YouTrack's historical native
        cross-project relation capability.
        """
        for issue_id in issue_ids:
            target = self._issue_target_project(issue_id)
            if not self._same_native_project(target, project):
                raise SystemExit(
                    "Mutation YouTrack refusée : "
                    f"l'issue '{issue_id}' appartient au projet natif "
                    f"'{target.key}', pas au projet canonique '{project.key}'."
                )

    def _adr_target_project(
        self, ref: str,
    ) -> Project:
        raw = self._req(
            "GET", f"/articles/{urllib.parse.quote(ref, safe='')}",
            fields="project(id,shortName)",
        )
        return self._require_writable_target(
            self._native_project(raw, f"l'ADR '{ref}'"),
        )

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

    @staticmethod
    def _same_native_project(left: Project, right: Project) -> bool:
        return left.key == right.key and left.id == right.id

    def _milestone_target_project(
        self, target: Project, explicit: Project | None,
    ) -> Project:
        """Restore a corroborated Milestone bundle after native target proof.

        ``_issue_target_project`` deliberately trusts only YouTrack's resource
        coordinates.  Those coordinates carry no registry metadata, so retain the
        bundle only from an explicit or historical project that names that exact
        native target.  A foreign checkout must not lend its bundle to the issue.
        """
        candidates: list[tuple[str, Project]] = []
        if explicit is not None and self._same_native_project(explicit, target):
            candidates.append(("explicite", explicit))

        try:
            historical = registry.resolve("youtrack", registry.repo_basename())
        except registry.ProjectNotRegisteredError:
            historical = None
        if historical is not None and self._same_native_project(historical, target):
            candidates.append(("historique", historical))

        bundles = {
            candidate.extra.get("ms_bundle")
            for _source, candidate in candidates
            if candidate.extra.get("ms_bundle")
        }
        if len(bundles) > 1:
            sources = ", ".join(source for source, _candidate in candidates)
            raise SystemExit(
                "Mutation YouTrack refusée : mappings Milestone contradictoires "
                f"pour le projet natif '{target.key}' ({sources})."
            )
        bundle = next(iter(bundles), None)
        return Project(
            key=target.key,
            id=target.id,
            extra={"ms_bundle": bundle} if bundle else {},
        )

    def _prewrite(self, fields, project: Project | None = None):
        if fields and fields.get("Milestone"):
            self._ensure_milestone(fields["Milestone"], project)

    def create_issue(self, project: Project, title: str, body: str,
                     fields: dict | None = None, parent: str | None = None) -> Issue:
        self._require_writable_target(project)
        if parent:
            # ``create_issue`` will link the newly-created child afterwards.  Prove
            # the existing target first so an archived parent cannot leave an
            # unrelated child behind before the command endpoint is refused.
            parent_target = self._issue_target_project(parent)
            if (
                self.requires_mutation_binding
                and not self._same_native_project(parent_target, project)
            ):
                raise SystemExit(
                    "Mutation YouTrack refusée : "
                    f"l'issue '{parent}' appartient au projet natif "
                    f"'{parent_target.key}', pas au projet canonique '{project.key}'."
                )
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
        return self._update_fields_bounded(issue_id, fields, project=project)

    def _update_fields_bounded(
        self,
        issue_id: str,
        fields: dict,
        *,
        project: Project | None,
        expected_snapshot: dict | None = None,
    ) -> Issue:
        null_fields = sorted(name for name, value in fields.items() if value is None)
        if null_fields:
            raise TrackerCapabilityUnavailableError(
                self.name, f"portable-field-clear:{null_fields[0]}"
            )
        unknown = sorted(set(fields) - _PORTABLE_FIELD_NAMES)
        if unknown:
            if project is not None and self.requires_mutation_binding:
                raise TrackerCapabilityUnavailableError(
                    self.name, f"portable-field:{unknown[0]}"
                )
            # Preserve the pre-V1 native custom-field escape hatch.  The normalized
            # Issue model cannot observe an arbitrary field, so this legacy path does
            # not claim the bounded snapshot/readback guarantee below.
            target = self._issue_target_project(issue_id)
            milestone_target = (
                self._milestone_target_project(target, project)
                if fields.get("Milestone")
                else target
            )
            self._prewrite(fields, milestone_target)
            cfs = [self._cf_write(name, value) for name, value in fields.items()]
            self._req(
                "POST", f"/issues/{issue_id}", {"customFields": cfs}, "idReadable"
            )
            return self.get_issue(issue_id)
        target = self._issue_target_project(issue_id)
        milestone_target = (
            self._milestone_target_project(target, project)
            if fields.get("Milestone")
            else target
        )
        desired = self._field_snapshot_from_values(fields)
        if expected_snapshot is None:
            # Generic field writes own their predecessor snapshot.  Lifecycle
            # transitions instead pass the caller-owned predecessor below so a
            # third-party edit cannot be adopted between the two public reads.
            before = self.get_issue(issue_id)
            expected = self._field_snapshot(before, fields)
            if expected == desired:
                return before
        else:
            expected = expected_snapshot
        # This fresh transport read is S1.  Only a write landing after this point
        # falls in the named S1 -> S2 residual window; an earlier divergence is
        # refused before POST.
        fresh = self.get_issue(issue_id)
        if self._field_snapshot(fresh, fields) != expected:
            raise TrackerConflictError(
                "champs YouTrack modifiés avant écriture bornée"
            )
        # A missing Milestone enum value is an existing, separate provider create.
        # Preserve it only after S1, then re-read the issue before its own POST.
        self._prewrite(fields, milestone_target)
        if fields.get("Milestone"):
            fresh = self.get_issue(issue_id)
            if self._field_snapshot(fresh, fields) != expected:
                raise TrackerConflictError(
                    "champs YouTrack modifiés pendant la préparation bornée"
                )
        cfs = [self._cf_write(k, v) for k, v in fields.items() if v is not None]
        self._req("POST", f"/issues/{issue_id}", {"customFields": cfs}, "idReadable")
        readback = self.get_issue(issue_id)
        if self._field_snapshot(readback, fields) != desired:
            raise TrackerConflictError(
                "champs YouTrack divergents après écriture ; aucune seconde tentative"
            )
        return readback

    @staticmethod
    def _field_snapshot(issue: Issue, fields: dict) -> dict:
        names = {
            "State": "state", "Priority": "priority", "Estimate": "estimate",
            "Milestone": "milestone", "Type": "type", "Labels": "labels",
            "GitHub PR": "pr_url",
        }
        snapshot = {name: getattr(issue, attribute)
                    for name, attribute in names.items() if name in fields}
        if "Labels" in snapshot:
            snapshot["Labels"] = tuple(sorted(snapshot["Labels"] or []))
        return snapshot

    @staticmethod
    def _field_snapshot_from_values(fields: dict) -> dict:
        desired = {name: value for name, value in fields.items() if name in {
            "State", "Priority", "Estimate", "Milestone", "Type", "Labels",
            "GitHub PR",
        }}
        if isinstance(desired.get("Labels"), str):
            desired["Labels"] = [
                label.strip() for label in desired["Labels"].split(",") if label.strip()
            ]
        if "Labels" in desired:
            desired["Labels"] = tuple(sorted(desired["Labels"] or []))
        return desired

    def set_state(
        self, issue_id: str, state: str, context=None, project: Project | None = None,
    ) -> None:
        if not isinstance(context, TransitionContext) or not context.expected_state:
            raise TrackerConflictError(
                "transition YouTrack sans état prédécesseur borné"
            )
        expected = context.expected_state
        before = self.get_issue(issue_id)
        if before.state == state:
            # The caller-owned predecessor plus the requested target identify
            # the operation.  A fresh invocation carrying the same coordinates
            # therefore converges after an ambiguous response without another
            # native write.
            return
        if before.state != expected:
            raise TrackerConflictError(
                "état YouTrack modifié avant transition bornée"
            )
        # Preserve the operation's original predecessor through the effective S1
        # transport read.  Only an edit after that read remains the documented
        # no-CAS S1 -> S2 residual risk.
        readback = self._update_fields_bounded(
            issue_id,
            {"State": state},
            project=project,
            expected_snapshot={"State": expected},
        )
        if readback.state != state:
            raise TrackerConflictError(
                "état YouTrack divergent après transition ; aucune seconde tentative"
            )

    def link(
        self, src_id: str, link_type: str, dst_id: str,
        project: Project | None = None,
    ) -> None:
        self._issue_target_project(src_id)
        self._issue_target_project(dst_id)
        before_source = self.get_issue(src_id)
        before_target = self.get_issue(dst_id)
        if self._has_link(before_source, link_type, dst_id):
            return
        fresh_source = self.get_issue(src_id)
        fresh_target = self.get_issue(dst_id)
        if (
            self._has_link(fresh_source, link_type, dst_id)
            or fresh_source.links != before_source.links
            or fresh_target.links != before_target.links
        ):
            raise TrackerConflictError("liens YouTrack modifiés avant écriture bornée")
        role = _LINK_ROLE.get(link_type, link_type)
        self._req("POST", "/commands",
                  {"query": f"{role} {dst_id}", "issues": [{"idReadable": src_id}]})
        readback = self.get_issue(src_id)
        if not self._has_link(readback, link_type, dst_id):
            raise TrackerConflictError(
                "lien YouTrack divergent après écriture ; aucune seconde tentative"
            )

    @staticmethod
    def _has_link(issue: Issue, link_type: str, target: str) -> bool:
        return any(link.type == link_type and link.target == target for link in issue.links)

    def add_comment(
        self, issue_id: str, text: str, project: Project | None = None,
    ) -> None:
        self._issue_target_project(issue_id)
        self._req("POST", f"/issues/{issue_id}/comments", {"text": text}, "id")

    @staticmethod
    def _epic_closure_audit(receipt: EpicClosureReceipt) -> tuple[str, str]:
        """Canonical append-only audit payload; the digest is its replay identity."""
        value = {"schema": "foundry-epic-closure.v1", "receipt": receipt.to_dict()}
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        return f"foundry-epic-closure.v1:{digest}", (
            "Foundry Epic closure audit (append-only).\n"
            f"marker: foundry-epic-closure.v1:{digest}\ncoordinates: {canonical}"
        )

    @classmethod
    def _closure_from_issue(
        cls, issue: Issue, project: Project, *, require_done: bool = True,
    ) -> EpicClosureOutcome | None:
        matches = []
        for row in issue.comments:
            text = row.get("text") if isinstance(row, dict) else None
            if not isinstance(text, str) or not text.startswith("Foundry Epic closure audit (append-only).\n"):
                continue
            lines = text.splitlines()
            if len(lines) != 3 or not lines[1].startswith("marker: ") or not lines[2].startswith("coordinates: "):
                raise TrackerConflictError("audit de clôture YouTrack malformé")
            try:
                value = json.loads(lines[2].removeprefix("coordinates: "))
                raw = value["receipt"]
                children = tuple(EpicClosureChild(**child) for child in raw["children"])
                dependencies = tuple(
                    EpicClosureDependency(
                        source_id=item["source_id"],
                        target=EpicClosureChild(**item["target"]),
                    )
                    for item in raw.get("dependencies", ())
                )
                receipt = EpicClosureReceipt(**{
                    **raw,
                    "children": children,
                    "dependencies": dependencies,
                })
            except (KeyError, TypeError, ValueError):
                raise TrackerConflictError("audit de clôture YouTrack malformé") from None
            marker, expected = cls._epic_closure_audit(receipt)
            if text != expected or lines[1] != f"marker: {marker}":
                raise TrackerConflictError("audit de clôture YouTrack divergent")
            if receipt.project_key != project.key or receipt.project_id != project.id or receipt.parent_id != issue.id:
                raise TrackerConflictError("audit de clôture YouTrack hors coordonnées")
            matches.append((receipt, marker))
        if len(matches) > 1:
            raise TrackerConflictError("audit de clôture YouTrack dupliqué")
        if not matches:
            return None
        receipt, marker = matches[0]
        from foundry.write import epic_parent_validation_digest
        if receipt.parent_validation_digest != epic_parent_validation_digest(issue):
            raise TrackerConflictError("Epic YouTrack modifié depuis le verdict humain")
        if require_done and (
            issue.state != "done"
            or issue.version is None
            or issue.version <= receipt.parent_version
        ):
            raise TrackerConflictError("audit de clôture YouTrack sans parent clôturé")
        return EpicClosureOutcome(
            receipt,
            issue.version if require_done else receipt.parent_version + 1,
            marker,
            replayed=True,
        )

    def get_epic_closure(self, project: Project, parent_id: str) -> EpicClosureOutcome | None:
        self.validate_issue_binding(project, parent_id)
        parent = self.get_issue(parent_id)
        outcome = self._closure_from_issue(parent, project)
        if outcome is None:
            return None
        from foundry.write import bounded_epic_graph_snapshot

        try:
            current, dependencies = bounded_epic_graph_snapshot(
                self, project, parent,
            )
        except (SystemExit, TrackerConflictError) as exc:
            raise TrackerConflictError(
                "graphe Epic YouTrack divergent au rejeu"
            ) from exc
        if (
            current != outcome.receipt.children
            or dependencies != outcome.receipt.dependencies
        ):
            raise TrackerConflictError("graphe Epic YouTrack divergent au rejeu")
        return outcome

    def get_pending_epic_closure(
        self, project: Project, parent_id: str,
    ) -> EpicClosureReceipt | None:
        self.validate_issue_binding(project, parent_id)
        parent = self.get_issue(parent_id)
        if parent.state == "done":
            return None
        pending = self._closure_from_issue(parent, project, require_done=False)
        if pending is None:
            return None
        receipt = pending.receipt
        if (
            parent.version is None
            or parent.version < receipt.parent_version
            or parent.state != receipt.parent_state
        ):
            raise TrackerConflictError(
                "audit pending YouTrack séparé de son prédécesseur original"
            )
        return receipt

    @staticmethod
    def _epic_closure_intent_directory() -> Path:
        return Path(registry.data_dir()) / "youtrack-epic-closure-intents"

    @staticmethod
    def _epic_closure_scope_fingerprint(project: Project, parent_id: str) -> str:
        # A replay may generate a new timestamp and nonce before the old audit
        # becomes visible.  Lock the Epic scope, not the individual receipt.
        return hashlib.sha256(
            f"{project.id}\0{project.key}\0{parent_id}".encode("utf-8")
        ).hexdigest()

    @classmethod
    def _epic_closure_intent_path(cls, fingerprint: str) -> Path:
        return cls._epic_closure_intent_directory() / f"{fingerprint}.json"

    @contextmanager
    def _epic_closure_intent_lock(self, fingerprint: str):
        directory = self._epic_closure_intent_directory()
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
            descriptor = os.open(
                directory / f".{fingerprint}.lock",
                os.O_RDWR | os.O_CREAT,
                0o600,
            )
        except OSError as exc:
            raise TrackerConflictError(
                "journal local d'audit Epic YouTrack indisponible"
            ) from exc
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_epic_closure_intent(self, fingerprint: str) -> dict[str, str] | None:
        try:
            record = json.loads(
                self._epic_closure_intent_path(fingerprint).read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise TrackerConflictError(
                "journal local d'audit Epic YouTrack invalide"
            ) from exc
        if (
            not isinstance(record, dict)
            or set(record) != {"schema", "fingerprint", "audit_id", "state"}
            or record.get("schema") != _EPIC_CLOSURE_INTENT_SCHEMA
            or record.get("fingerprint") != fingerprint
            or not isinstance(record.get("audit_id"), str)
            or re.fullmatch(
                r"foundry-epic-closure\.v1:[0-9a-f]{64}", record["audit_id"],
            ) is None
            or record.get("state") not in {"pending", "complete"}
        ):
            raise TrackerConflictError(
                "journal local d'audit Epic YouTrack invalide"
            )
        return record

    def _write_epic_closure_intent(
        self, fingerprint: str, audit_id: str, state: str,
    ) -> None:
        directory = self._epic_closure_intent_directory()
        payload = json.dumps(
            {
                "schema": _EPIC_CLOSURE_INTENT_SCHEMA,
                "fingerprint": fingerprint,
                "audit_id": audit_id,
                "state": state,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=directory,
                prefix=f".{fingerprint}.",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                os.chmod(temporary_name, 0o600)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self._epic_closure_intent_path(fingerprint))
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise TrackerConflictError(
                "journal local d'audit Epic YouTrack indisponible"
            ) from exc
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass

    def close_epic(self, project: Project, receipt: EpicClosureReceipt) -> EpicClosureOutcome:
        self.validate_issue_binding(project, receipt.parent_id, *(child.id for child in receipt.children))
        before = self.get_issue(receipt.parent_id)
        observed = self._closure_from_issue(before, project, require_done=False)
        if observed is not None and before.state == "done":
            existing = self._closure_from_issue(before, project)
            if existing is None or existing.receipt != receipt:
                raise TrackerConflictError("audit de clôture YouTrack divergent")
            return existing
        if observed is not None and observed.receipt != receipt:
            raise TrackerConflictError("audit pending YouTrack divergent")
        if before.state == "done":
            raise TrackerConflictError("Epic YouTrack done sans audit récupérable")
        from foundry.write import bounded_epic_graph_snapshot, epic_parent_validation_digest

        try:
            expected_children, expected_dependencies = bounded_epic_graph_snapshot(
                self, project, before,
            )
        except SystemExit as exc:
            raise TrackerConflictError(
                "graphe Epic YouTrack divergent avant écriture"
            ) from exc
        if (
            ((observed is None and before.version != receipt.parent_version)
             or (observed is not None and (
                 before.version is None or before.version < receipt.parent_version
             )))
            or before.type != receipt.parent_type
            or before.ac_done != receipt.parent_ac_done or before.ac_total != receipt.parent_ac_total
            or epic_parent_validation_digest(before) != receipt.parent_validation_digest
            or before.state != receipt.parent_state
            or expected_children != receipt.children
            or expected_dependencies != receipt.dependencies
        ):
            raise TrackerConflictError("graphe Epic YouTrack divergent avant écriture")
        audit_id, audit = self._epic_closure_audit(receipt)
        fingerprint = self._epic_closure_scope_fingerprint(
            project, receipt.parent_id,
        )
        with self._epic_closure_intent_lock(fingerprint):
            intent = self._read_epic_closure_intent(fingerprint)
            if intent is not None and intent["audit_id"] != audit_id:
                raise TrackerConflictError(
                    "reçu d'audit Epic YouTrack différent de l'effet local "
                    "incertain ; no second POST"
                )
            current_pending = self._closure_from_issue(
                self.get_issue(receipt.parent_id), project, require_done=False,
            )
            if current_pending is None:
                if observed is not None or intent is not None:
                    raise TrackerConflictError(
                        "effet de l'audit Epic YouTrack inconnu ou invisible ; "
                        "no second POST"
                    )
                # Persist the exact receipt identity before the only append.  This
                # machine-local journal prevents an explicit replay from turning an
                # unresolved response into a second non-idempotent POST.  It is not
                # provider CAS and does not claim exactly-once delivery.
                self._write_epic_closure_intent(fingerprint, audit_id, "pending")
                try:
                    self.add_comment(receipt.parent_id, audit, project)
                except Exception as error:
                    # The provider may have committed the append-only audit before
                    # losing the response. Recover by exact read only; never retry.
                    current_pending = self._closure_from_issue(
                        self.get_issue(receipt.parent_id), project,
                        require_done=False,
                    )
                    if (
                        current_pending is None
                        or current_pending.receipt != receipt
                    ):
                        raise error
                if current_pending is None:
                    current_pending = self._closure_from_issue(
                        self.get_issue(receipt.parent_id), project,
                        require_done=False,
                    )
            if current_pending is None or current_pending.receipt != receipt:
                raise TrackerConflictError("audit de clôture YouTrack divergent")
            self._write_epic_closure_intent(fingerprint, audit_id, "complete")
        # Read the pending audit before the single parent write.  An ambiguous
        # audit response is never retried; absence is a closed failure.
        pending = self._closure_from_issue(
            self.get_issue(receipt.parent_id), project, require_done=False,
        )
        if pending is None or pending.receipt != receipt:
            raise TrackerConflictError("audit de clôture YouTrack absent avant écriture")
        try:
            self.set_state(
                receipt.parent_id, "done",
                TransitionContext(expected_state=receipt.parent_state), project,
            )
        except Exception as error:
            # Same bounded recovery for a response lost after the targeted State
            # effect: exact done + exact audit is success, anything else fails.
            recovered_parent = self.get_issue(receipt.parent_id)
            recovered = self._closure_from_issue(recovered_parent, project)
            if recovered is None or recovered.receipt != receipt:
                raise error
        closed = self.get_issue(receipt.parent_id)
        if closed.state != "done" or closed.version is None or closed.version <= receipt.parent_version:
            raise TrackerConflictError("Epic YouTrack divergent après écriture")
        recovered = self._closure_from_issue(closed, project)
        if recovered is None or recovered.receipt != receipt:
            raise TrackerConflictError("audit de clôture YouTrack absent après écriture")
        try:
            closed_children, closed_dependencies = bounded_epic_graph_snapshot(
                self, project, closed,
            )
        except (SystemExit, TrackerConflictError) as exc:
            raise TrackerConflictError(
                "graphe Epic YouTrack divergent après écriture"
            ) from exc
        if (
            closed_children != receipt.children
            or closed_dependencies != receipt.dependencies
        ):
            raise TrackerConflictError(
                "graphe Epic YouTrack divergent après écriture"
            )
        return EpicClosureOutcome(receipt, closed.version, audit_id)

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
            self._issue_target_project(resource_id)

            def read_body():
                return self.get_issue(resource_id).body

            def write_body():
                self._req("POST", f"/issues/{urllib.parse.quote(resource_id, safe='')}",
                          {"description": updated_body}, "idReadable")
        elif isinstance(resource, Adr):
            resource_type, resource_id = "adr", resource.ref or ""
            if not resource_id:
                raise ValueError(f"ADR {resource.id} sans ref native")
            self._adr_target_project(resource_id)

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
        self._require_writable_target(project)
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
        self._adr_target_project(adr.ref)
        cur = self._req("GET", f"/articles/{adr.ref}", fields="content")
        content = re.sub(r"(statut\s*:\s*`?)\w+(`?)", rf"\g<1>{status}\g<2>",
                         cur.get("content") or "", count=1)
        self.update_body(adr, cur.get("content") or "", content, project=project)
