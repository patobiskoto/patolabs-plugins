"""Repo → tracker-project registry.

Lives in `FOUNDRY_DATA` when explicitly set, else `~/.config/foundry/registry.json`.
Never in the hosts' plugin-private dirs (`PLUGIN_DATA`/`CLAUDE_PLUGIN_DATA`): those
vars reach hook commands but NOT the bash commands skills run, so honoring them
splits the registry — skills write one file, hooks silently read another (they fail
open). One shared path keeps skills, hooks, other plugins (ship-ios) and in-repo
development on the same data. Editing it is a `register` call, not hand-editing a
Python dict — that was a fragility in the old tooling.

Shape:
  { "youtrack": { "<repo>": {"key": "FOUNDRY", "id": "0-3", "ms_bundle": "…"} },
    "ghprojects": { … } }
(`ms_bundle` is the per-project Milestone enum bundle id, written by setup_project and
read by the YouTrack adapter to add milestone values on the fly.)
"""
from __future__ import annotations

import fcntl
import json
import hashlib
import os
import re
import subprocess
import tempfile
import urllib.parse
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from foundry.models import Project


_REPOSITORY_COMPONENT = re.compile(r"[A-Za-z0-9_.-]+")
_SSH_CONFIG_TIMEOUT_SECONDS = 5
_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_LINEAR_STATE_KEYS = frozenset(
    {"backlog", "ready", "in-progress", "review", "blocked", "done", "dropped"}
)
_LINEAR_TYPE_KEYS = frozenset({"Epic", "Feature", "Bug", "Task"})
_LINEAR_STRUCTURED_EXTRA_KEYS = frozenset(
    {"state_ids", "milestone_ids", "type_label_ids", "label_ids"}
)
_LINEAR_EXTRA_KEYS = frozenset(
    {
        "canonical_repo",
        "team_id",
        "state_ids",
        "milestone_ids",
        "type_label_ids",
        "label_ids",
    }
)
_LINEAR_FORBIDDEN_IDENTIFIER_KEY_PARTS = frozenset(
    {
        "credential",
        "credentials",
        "endpoint",
        "password",
        "passwords",
        "secret",
        "secrets",
        "token",
        "tokens",
        "uri",
        "url",
    }
)
_TRACKER_MARKER_RELATIVE_PATH = Path(".foundry/tracker.json")
_TRACKER_MARKER_MAX_BYTES = 16 * 1024
_TRACKER_MARKER_VERSION = 1
_TRACKER_MARKER_KEYS = frozenset(
    {
        "version",
        "repository",
        "tracker",
        "project",
        "registry_binding_digest",
        "migration_manifest_digest",
        "configuration_digest",
    }
)
_SUPPORTED_MARKER_TRACKERS = frozenset({"youtrack", "ghprojects", "devhub", "linear"})
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class RepositoryTrackerBinding:
    """Validated repository-scoped tracker and its complete registry binding."""

    tracker: str
    repository: str
    project: Project
    registry_binding_digest: str
    migration_manifest_digest: str
    configuration_digest: str


def _expand_ssh_alias(host: str) -> str:
    """Resolve one unqualified SSH alias from local config, without DNS or network."""
    try:
        result = subprocess.run(
            ["ssh", "-G", "-o", "CanonicalizeHostname=no", "--", host],
            capture_output=True,
            text=True,
            timeout=_SSH_CONFIG_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("alias SSH impossible à vérifier") from None
    if result.returncode != 0:
        raise ValueError("alias SSH impossible à vérifier")

    hostnames = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 1)
        if len(fields) == 2 and fields[0].lower() == "hostname":
            hostnames.append(fields[1].strip().lower())
    if len(hostnames) != 1 or hostnames[0] == host:
        raise ValueError("alias SSH impossible à vérifier")

    expanded = hostnames[0]
    if (not expanded or "/" in expanded or "@" in expanded
            or any(ord(character) <= 0x20 or ord(character) == 0x7f
                   for character in expanded)):
        raise ValueError("alias SSH impossible à vérifier")
    return expanded


def data_dir() -> str:
    """Stable writable state across Codex, Claude Code, and in-repo development.
    Deliberately ignores PLUGIN_DATA/CLAUDE_PLUGIN_DATA — see module docstring."""
    return os.environ.get("FOUNDRY_DATA") or os.path.expanduser("~/.config/foundry")


def path() -> str:
    return os.path.join(data_dir(), "registry.json")


def _sanitize_canonical_repositories(data: dict) -> dict:
    """Canonicalize legacy registry identities in memory without echoing raw data."""
    for bindings in data.values():
        if not isinstance(bindings, dict):
            continue
        for entry in bindings.values():
            if not isinstance(entry, dict) or "canonical_repo" not in entry:
                continue
            try:
                entry["canonical_repo"] = canonical_repository_identity(
                    entry["canonical_repo"],
                )
            except ValueError:
                raise ValueError("canonical_repo invalide dans le registre Foundry") from None
    return data


def load() -> dict:
    """The whole registry as a credential-free, read-only snapshot."""
    registry_path = path()
    if os.path.exists(registry_path):
        with open(registry_path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("registre Foundry invalide")
        return _sanitize_canonical_repositories(data)
    return {}


def _save(data: dict) -> None:
    registry_path = path()
    registry_directory = os.path.dirname(registry_path)
    os.makedirs(registry_directory, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".registry-", suffix=".json.tmp", dir=registry_directory,
    )
    try:
        with os.fdopen(descriptor, "w") as file:
            json.dump(data, file, indent=2, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, registry_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


@contextmanager
def _cutover_lock():
    """Serialize registry writes and repository-marker cutovers across processes."""
    directory = Path(data_dir())
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".registry-cutover.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def canonical_repository_identity(
    value: str, *, ssh_alias_host: str = "github.com",
) -> str:
    """Return the credential-free ``host/owner/repo`` identity of a Git remote.

    Foundry's GitHub code-host adapter already supports HTTPS, URL-style SSH,
    scp-style SSH and ssh-config aliases.  Project binding must accept those same
    syntaxes without resolving DNS or retaining user-info from an authenticated
    URL.  DevHub's registry form (``github.com/owner/repo``) is accepted too.
    """
    if (not isinstance(value, str) or not value
            or any(ord(character) <= 0x20 or ord(character) == 0x7f
                   for character in value)):
        raise ValueError("identité de dépôt Git invalide")

    path = value
    host = None
    if "://" in value:
        try:
            parsed = urllib.parse.urlsplit(value)
        except (TypeError, ValueError, UnicodeError):
            raise ValueError("identité de dépôt Git invalide") from None
        scheme = parsed.scheme.lower()
        if (scheme not in {"http", "https", "ssh", "git"}
                or not parsed.hostname or parsed.query or parsed.fragment):
            raise ValueError("identité de dépôt Git invalide")
        host = parsed.hostname.lower()
        if scheme == "ssh" and host != ssh_alias_host and "." not in host:
            host = _expand_ssh_alias(host)
        path = parsed.path
    elif ":" in value:
        # scp-style remotes: [user@]host:owner/repo.git.  An unqualified host
        # is an alias only when local ssh configuration proves its HostName.
        authority, path = value.split(":", 1)
        host = authority.rsplit("@", 1)[-1].lower()
        if not host or not path:
            raise ValueError("identité de dépôt Git invalide")
        if host != ssh_alias_host and "." not in host:
            host = _expand_ssh_alias(host)

    path = path.strip("/").removesuffix(".git")
    parts = path.split("/") if path else []
    # The registry's documented canonical form includes the code-host name;
    # URL/scp remotes have already separated it from their path.
    if host is None and len(parts) == 3 and "." in parts[0]:
        host = parts[0].lower()
        parts = parts[1:]
    if (len(parts) != 2 or any(
            not _REPOSITORY_COMPONENT.fullmatch(component)
            or component in {".", ".."} for component in parts)):
        raise ValueError("identité de dépôt Git invalide")
    host = (host or ssh_alias_host).lower()
    if not host or "/" in host or "@" in host:
        raise ValueError("identité de dépôt Git invalide")
    return "/".join([host, *(component.lower() for component in parts)])


def checkout_repository_identity(cwd: str | None = None) -> str:
    """Canonical identity of the actual checkout's origin, never an env alias."""
    result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"], cwd=cwd,
        capture_output=True, text=True,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        raise ValueError("identité du remote origin introuvable")
    return canonical_repository_identity(value)


def _checkout_root(cwd: str | None = None) -> Path | None:
    """Return the checkout root, or ``None`` outside a Git working tree."""
    start = Path(cwd or os.getcwd()).resolve()
    if start.is_file():
        start = start.parent
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _json_digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _marker_path(cwd: str | None = None) -> Path | None:
    root = _checkout_root(cwd)
    return None if root is None else root / _TRACKER_MARKER_RELATIVE_PATH


def _matching_registry_bindings(
    data: dict, tracker: str, repository: str, repo_name: str,
) -> list[tuple[str, dict]]:
    entries = data.get(tracker, {})
    if not isinstance(entries, dict):
        return []
    canonical = [
        (name, entry) for name, entry in entries.items()
        if isinstance(entry, dict) and entry.get("canonical_repo") == repository
    ]
    if canonical:
        return canonical
    entry = entries.get(repo_name)
    return [(repo_name, entry)] if isinstance(entry, dict) else []


def _registry_project_for_marker(
    data: dict, *, tracker: str, repository: str, repo_name: str,
    key: str, project_id: str,
) -> tuple[Project, dict, str]:
    matches = _matching_registry_bindings(data, tracker, repository, repo_name)
    if not matches:
        raise ValueError("binding tracker du marqueur absent du registre")
    first = matches[0][1]
    if tracker == "linear" and first.get("canonical_repo") != repository:
        raise ValueError("binding Linear du marqueur incompatible avec le dépôt")
    if any(entry != first for _name, entry in matches[1:]):
        raise ValueError("binding tracker du marqueur ambigu dans le registre")
    if first.get("archive") is True:
        raise ValueError("binding tracker du marqueur archivé")
    if first.get("key") != key or first.get("id") != project_id:
        raise ValueError("coordonnées du marqueur incompatibles avec le registre")
    safe_entry = {name: value for name, value in first.items() if name != "archive"}
    digest = _json_digest(safe_entry)
    project = Project(
        key=key, id=project_id,
        extra={name: value for name, value in safe_entry.items() if name not in {"key", "id"}},
    )
    return project, first, digest


def repository_tracker_binding(cwd: str | None = None) -> RepositoryTrackerBinding | None:
    """Load one strict repository marker and bind it to the full registry entry.

    The marker is executable configuration. A malformed, moved or stale marker is
    refused; it never silently falls back to the host-global tracker.
    """
    marker = _marker_path(cwd)
    if marker is None:
        return None
    try:
        stat = marker.lstat()
    except FileNotFoundError:
        return None
    if (
        marker.parent.is_symlink()
        or marker.is_symlink()
        or not marker.is_file()
        or stat.st_size > _TRACKER_MARKER_MAX_BYTES
    ):
        raise ValueError("marqueur tracker de dépôt invalide")
    try:
        data = json.loads(marker.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("marqueur tracker de dépôt invalide") from None
    if not isinstance(data, dict) or set(data) != _TRACKER_MARKER_KEYS:
        raise ValueError("marqueur tracker de dépôt invalide")
    project_coordinate = data.get("project")
    if (
        data.get("version") != _TRACKER_MARKER_VERSION
        or not isinstance(data.get("repository"), str)
        or data.get("tracker") not in _SUPPORTED_MARKER_TRACKERS
        or not isinstance(project_coordinate, dict)
        or set(project_coordinate) != {"key", "id"}
        or any(
            not isinstance(project_coordinate.get(field), str)
            or not project_coordinate[field]
            for field in ("key", "id")
        )
        or any(
            not isinstance(data.get(field), str)
            or _SHA256.fullmatch(data[field]) is None
            for field in (
                "registry_binding_digest",
                "migration_manifest_digest",
                "configuration_digest",
            )
        )
    ):
        raise ValueError("marqueur tracker de dépôt invalide")
    try:
        repository = canonical_repository_identity(data["repository"])
    except ValueError:
        raise ValueError("marqueur tracker de dépôt invalide") from None
    if repository != data["repository"]:
        raise ValueError("marqueur tracker de dépôt invalide")
    root = marker.parents[1]
    try:
        actual_repository = checkout_repository_identity(str(root))
    except ValueError:
        raise ValueError("marqueur tracker de dépôt invalide") from None
    if actual_repository != repository:
        raise ValueError("marqueur tracker incompatible avec le dépôt courant")
    configuration = {
        name: data[name] for name in _TRACKER_MARKER_KEYS
        if name != "configuration_digest"
    }
    if data["configuration_digest"] != _json_digest(configuration):
        raise ValueError("digest du marqueur tracker invalide")
    project, _entry, registry_digest = _registry_project_for_marker(
        load(), tracker=data["tracker"], repository=repository,
        repo_name=repo_basename(str(root), use_env=False),
        key=project_coordinate["key"], project_id=project_coordinate["id"],
    )
    if data["registry_binding_digest"] != registry_digest:
        raise ValueError("binding tracker du registre modifié depuis le cutover")
    return RepositoryTrackerBinding(
        tracker=data["tracker"], repository=repository, project=project,
        registry_binding_digest=registry_digest,
        migration_manifest_digest=data["migration_manifest_digest"],
        configuration_digest=data["configuration_digest"],
    )


def tracker_name_for_checkout(cwd: str | None = None) -> str:
    """Select the repository marker first, or the legacy global default."""
    binding = repository_tracker_binding(cwd)
    if binding is not None:
        return binding.tracker
    from foundry import config
    return config.tracker_name()


def _prepare_marker(root: Path, payload: dict) -> tuple[int, str, Path]:
    directory = root / _TRACKER_MARKER_RELATIVE_PATH.parent
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("répertoire du marqueur tracker invalide")
    descriptor, temporary = tempfile.mkstemp(
        prefix=".tracker-", suffix=".json.tmp", dir=directory,
    )
    return descriptor, temporary, directory / _TRACKER_MARKER_RELATIVE_PATH.name


def cutover_repository_tracker(
    tracker: str, key: str, project_id: str, *,
    migration_manifest_digest: str, cwd: str | None = None,
) -> RepositoryTrackerBinding:
    """Activate a pre-registered target without ever exposing two writable trackers.

    Provider migration and readback happen before this function. Registry archival
    is published before the marker; an interruption may make the checkout
    temporarily unavailable, but it cannot restore or create dual-write authority.
    Repeating the exact operation completes or replays it idempotently.
    """
    if (
        tracker not in _SUPPORTED_MARKER_TRACKERS
        or any(not isinstance(value, str) or not value for value in (key, project_id))
        or not isinstance(migration_manifest_digest, str)
        or _SHA256.fullmatch(migration_manifest_digest) is None
    ):
        raise ValueError("binding tracker de cutover invalide")
    root = _checkout_root(cwd)
    if root is None:
        raise ValueError("cutover tracker hors dépôt Git")
    repository = checkout_repository_identity(str(root))
    repo_name = repo_basename(str(root), use_env=False)
    with _cutover_lock():
        # Re-read every compare-and-publish input under one process-shared lock.
        # A waiting caller observes the winner instead of replacing it from a
        # stale snapshot with last-writer-wins semantics.
        data = load()
        project, _target_entry, registry_digest = _registry_project_for_marker(
            data, tracker=tracker, repository=repository, repo_name=repo_name,
            key=key, project_id=project_id,
        )
        base_payload = {
            "version": _TRACKER_MARKER_VERSION,
            "repository": repository,
            "tracker": tracker,
            "project": {"key": key, "id": project_id},
            "registry_binding_digest": registry_digest,
            "migration_manifest_digest": migration_manifest_digest,
        }
        payload = {**base_payload, "configuration_digest": _json_digest(base_payload)}
        expected = RepositoryTrackerBinding(
            tracker=tracker, repository=repository, project=project,
            registry_binding_digest=registry_digest,
            migration_manifest_digest=migration_manifest_digest,
            configuration_digest=payload["configuration_digest"],
        )
        current = repository_tracker_binding(str(root))
        if current is not None:
            if current == expected:
                return current
            raise ValueError(
                "cutover tracker refusé : un binding actif différent existe déjà"
            )

        source_bindings = []
        for provider, entries in data.items():
            if provider == tracker or not isinstance(entries, dict):
                continue
            for name, entry in entries.items():
                if (
                    isinstance(entry, dict)
                    and entry.get("archive") is not True
                    and (entry.get("canonical_repo") == repository or name == repo_name)
                ):
                    source_bindings.append((provider, name, entry))
        if len(source_bindings) > 1:
            raise ValueError("cutover tracker refusé : bindings source actifs ambigus")

        descriptor, temporary, marker = _prepare_marker(root, payload)
        try:
            with os.fdopen(descriptor, "w") as file:
                json.dump(payload, file, indent=2, sort_keys=True)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            if source_bindings:
                provider, name, entry = source_bindings[0]
                data[provider][name] = {**entry, "archive": True}
                _save(data)
            os.replace(temporary, marker)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

        published = repository_tracker_binding(str(root))
        if published != expected:
            raise ValueError("cutover tracker refusé : relecture du binding divergente")
        return published


def _require_uuid(value: object, field: str) -> str:
    """Return one normalized UUID without ever echoing invalid input."""
    if not isinstance(value, str) or _UUID.fullmatch(value) is None:
        raise ValueError(f"binding Linear invalide : {field} doit être un UUID")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ValueError(
            f"binding Linear invalide : {field} doit être un UUID"
        ) from None


def _linear_identifier_key_is_forbidden(value: str) -> bool:
    """Reject URL/credential-shaped names without echoing them in an error."""
    camel_case_split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    parts = tuple(
        part for part in re.split(r"[^A-Za-z0-9]+", camel_case_split.casefold())
        if part
    )
    return (
        "://" in value
        or value.startswith("//")
        or any(part in _LINEAR_FORBIDDEN_IDENTIFIER_KEY_PARTS for part in parts)
        or any(
            left in {"api", "client", "private"} and right == "key"
            for left, right in zip(parts, parts[1:])
        )
    )


def _require_uuid_map(
    value: object,
    field: str,
    *,
    expected_keys: frozenset[str] | None = None,
) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"binding Linear invalide : {field} doit être un objet JSON")
    if expected_keys is not None and set(value) != expected_keys:
        raise ValueError(
            f"binding Linear invalide : {field} doit contenir exactement les identifiants requis"
        )
    if not value and expected_keys is None:
        return {}
    if any(
        not isinstance(key, str)
        or not key
        or _linear_identifier_key_is_forbidden(key)
        for key in value
    ):
        raise ValueError(f"binding Linear invalide : {field} contient une clé invalide")
    normalized = {
        key: _require_uuid(identifier, f"{field}.{key}")
        for key, identifier in value.items()
    }
    if len(set(normalized.values())) != len(normalized):
        raise ValueError(f"binding Linear invalide : {field} contient des UUID dupliqués")
    return normalized


def _validate_linear_binding(
    repository: object, project_id: object, extra: dict[str, object],
) -> tuple[str, dict[str, object]]:
    """Validate the complete credential-free Linear binding before persistence.

    This is intentionally structural: it does not discover a workspace or call Linear.
    The separate qualification record proves live observations; FOUNDRY-159 remains the
    only activation/cutover path.
    """
    unsupported = set(extra) - _LINEAR_EXTRA_KEYS
    if unsupported:
        raise ValueError("binding Linear invalide : extra non autorisé")
    if (
        not isinstance(repository, str)
        or _REPOSITORY_COMPONENT.fullmatch(repository) is None
    ):
        raise ValueError("binding Linear invalide : repository invalide")

    canonical_repo = extra.get("canonical_repo")
    try:
        canonical = canonical_repository_identity(canonical_repo)
    except ValueError:
        raise ValueError("binding Linear invalide : canonical_repo invalide") from None
    if canonical_repo != canonical:
        raise ValueError("binding Linear invalide : canonical_repo doit être canonique")

    normalized_project_id = _require_uuid(project_id, "project_id")
    normalized: dict[str, object] = {
        "canonical_repo": canonical,
        "team_id": _require_uuid(extra.get("team_id"), "team_id"),
        "state_ids": _require_uuid_map(
            extra.get("state_ids"), "state_ids", expected_keys=_LINEAR_STATE_KEYS,
        ),
        "type_label_ids": _require_uuid_map(
            extra.get("type_label_ids"),
            "type_label_ids",
            expected_keys=_LINEAR_TYPE_KEYS,
        ),
    }
    for key in ("milestone_ids", "label_ids"):
        if key in extra:
            normalized[key] = _require_uuid_map(extra[key], key)

    repository_identity = repository
    if _UUID.fullmatch(repository) is not None:
        repository_identity = str(uuid.UUID(repository))
    identifiers = [
        repository_identity,
        normalized_project_id,
        normalized["team_id"],
    ]
    for key in ("state_ids", "type_label_ids", "milestone_ids", "label_ids"):
        mapping = normalized.get(key, {})
        assert isinstance(mapping, dict)
        identifiers.extend(mapping.values())
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("binding Linear invalide : identifiant de binding ambigu")
    return normalized_project_id, normalized


def repo_basename(cwd: str | None = None, use_env: bool = True) -> str:
    """The current repo's basename, from $PROJECT_REPO (unless use_env=False) or the
    git remote. Hooks pass use_env=False: their identity must come from the actual
    repo at cwd, not from an inherited env var."""
    if use_env:
        repo = os.environ.get("PROJECT_REPO")
        if repo:
            return repo
    r = subprocess.run(["git", "config", "remote.origin.url"], cwd=cwd,
                       capture_output=True, text=True)
    url = r.stdout.strip()
    return url.split("/")[-1].removesuffix(".git") if url else ""


def entry_for(cwd: str | None = None, use_env: bool = True):
    """(tracker, repo, entry) for the repo at cwd, searched across ALL trackers
    (preferring the configured one) — or None if unregistered. This is THE lookup
    the hooks use: a repo registered under any tracker is a Foundry repo."""
    binding = repository_tracker_binding(cwd)
    repo = repo_basename(cwd, use_env=False if binding is not None else use_env)
    if not repo:
        return None
    data = load()
    if binding is not None:
        entry = {
            "key": binding.project.key,
            "id": binding.project.id,
            **binding.project.extra,
        }
        return binding.tracker, repo, entry
    try:
        repository = checkout_repository_identity(cwd)
    except ValueError:
        repository = None
    if repository is not None:
        providers = {
            provider
            for provider, entries in data.items()
            if isinstance(entries, dict)
            for entry in entries.values()
            if isinstance(entry, dict)
            and entry.get("canonical_repo") == repository
            and entry.get("archive") is not True
        }
        if len(providers) > 1:
            raise ValueError("bindings tracker actifs ambigus pour le dépôt courant")
    from foundry import config
    preferred = config.tracker_name()
    for tracker in [preferred, *sorted(k for k in data if k != preferred)]:
        e = data.get(tracker, {}).get(repo)
        if e and e.get("archive") is not True:
            return tracker, repo, e
    return None


def default_branch(cwd: str | None = None) -> str | None:
    """The remote default branch name, or None when undeterminable (origin/HEAD
    unset — common on single-branch clones). Callers choose their own fallback."""
    r = subprocess.run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=cwd,
                       capture_output=True, text=True)
    head = r.stdout.strip()
    # `git symbolic-ref` answers a full ref path. Strip exactly the remote prefix:
    # splitting on the last "/" truncates a default branch that contains one,
    # turning `codex/fixture-source` into `fixture-source` and branching from a
    # commit that does not exist. An unexpected shape is undeterminable, never a
    # guess, so it answers None like an unset `origin/HEAD` does.
    prefix = "refs/remotes/origin/"
    if not head.startswith(prefix):
        return None
    return head[len(prefix):] or None


def resolve(tracker: str, repo: str, cwd: str | None = None) -> Project:
    binding = repository_tracker_binding(cwd)
    if binding is not None:
        if tracker != binding.tracker:
            raise SystemExit(
                f"Binding tracker refusé : '{binding.repository}' est actif sur "
                f"'{binding.tracker}', pas '{tracker}'."
            )
        root = _checkout_root(cwd)
        expected_repo = repo_basename(str(root), use_env=False) if root else ""
        if repo != expected_repo:
            raise SystemExit("Binding tracker refusé : alias de dépôt incompatible.")
        return binding.project
    all_data = load()
    try:
        repository = checkout_repository_identity(cwd)
    except ValueError:
        repository = None
    if repository is not None:
        providers = {
            provider
            for provider, entries in all_data.items()
            if isinstance(entries, dict)
            for entry in entries.values()
            if isinstance(entry, dict)
            and entry.get("canonical_repo") == repository
            and entry.get("archive") is not True
        }
        if len(providers) > 1:
            raise SystemExit("Binding tracker refusé : bindings actifs ambigus.")
    data = all_data.get(tracker, {})
    if repo not in data:
        known = ", ".join(data) or "(aucun)"
        raise SystemExit(
            f"Repo '{repo}' non enregistré pour le tracker '{tracker}'. "
            f"Connus : {known}. Enregistre-le : "
            f"python3 <plugin-root>/tooling/foundry_cli.py registry register "
            f"{tracker} {repo} <KEY> <project-id> — ou lance le skill foundry:doctor.")
    e = data[repo]
    if e.get("archive") is True:
        raise SystemExit(
            f"Binding tracker archivé : '{repo}' ne peut plus recevoir "
            f"d'écriture via '{tracker}'."
        )
    return Project(key=e["key"], id=e["id"],
                   extra={
                       k: v for k, v in e.items()
                       if k not in ("key", "id", "archive")
                   })


def require_writable_project(tracker: str, project: Project) -> None:
    """Refuse writes once any alias tombstones this provider project.

    A cutover archives the source repository alias while historical aliases remain
    resolvable for reads.  Because those aliases address the same native provider
    project, the archived entry is a project-wide mutation tombstone: current
    Foundry may still query the archive, but it must not write through another alias.
    """
    bindings = load().get(tracker, {})
    if not isinstance(bindings, dict):
        return
    if any(
        isinstance(entry, dict)
        and entry.get("archive") is True
        and entry.get("key") == project.key
        and entry.get("id") == project.id
        for entry in bindings.values()
    ):
        raise SystemExit(
            f"Mutation tracker refusée : le projet '{project.key}' est une "
            f"archive lisible via '{tracker}'."
        )


def resolve_canonical_repository(tracker: str, canonical_repo: str) -> Project:
    """Resolve one provider binding by exact credential-free checkout identity.

    Repository basenames and ``PROJECT_REPO`` are deliberately absent from this
    lookup. Duplicate aliases are accepted only when their complete binding payloads
    are identical; contradictory projects for one canonical repository fail closed.
    """
    try:
        canonical = canonical_repository_identity(canonical_repo)
    except ValueError:
        raise ValueError("identité canonique du checkout invalide") from None
    bindings = load().get(tracker, {})
    matches = [
        entry for entry in bindings.values()
        if isinstance(entry, dict)
        and entry.get("canonical_repo") == canonical
        and entry.get("archive") is not True
    ]
    if not matches:
        raise SystemExit(
            f"Aucun binding '{tracker}' ne correspond au canonical_repo du checkout."
        )
    selected = matches[0]
    if any(entry != selected for entry in matches[1:]):
        raise SystemExit(
            f"Bindings '{tracker}' ambigus pour le canonical_repo du checkout."
        )
    if (not isinstance(selected.get("key"), str) or not selected["key"]
            or not isinstance(selected.get("id"), str) or not selected["id"]):
        raise SystemExit(f"Binding '{tracker}' invalide pour le canonical_repo du checkout.")
    return Project(
        key=selected["key"], id=selected["id"],
        extra={
            k: v for k, v in selected.items()
            if k not in ("key", "id", "archive")
        },
    )


def register(tracker: str, repo: str, key: str, project_id: str, **extra) -> None:
    extra = dict(extra)
    if "archive" in extra:
        raise ValueError(
            "binding archive refusé : utiliser le cutover tracker explicite"
        )
    if tracker == "linear":
        project_id, extra = _validate_linear_binding(repo, project_id, extra)
    elif "canonical_repo" in extra:
        try:
            extra["canonical_repo"] = canonical_repository_identity(
                extra["canonical_repo"],
            )
        except ValueError:
            raise ValueError("canonical_repo invalide") from None
    with _cutover_lock():
        data = load()
        current = data.get(tracker, {}).get(repo)
        if isinstance(current, dict) and current.get("archive") is True:
            raise ValueError(
                f"binding archive '{tracker}/{repo}' immuable sans rollback explicite"
            )
        data.setdefault(tracker, {})[repo] = {"key": key, "id": project_id, **extra}
        _save(data)


def register_alias(tracker: str, source_repo: str, alias_repo: str) -> bool:
    """Bind a renamed repo to an existing tracker project without provisioning one.

    Keep the source binding: old checkouts and hooks may still use it. Return False
    when the exact alias already exists, and refuse to overwrite another project.
    """
    with _cutover_lock():
        data = load()
        bindings = data.get(tracker, {})
        if source_repo not in bindings:
            raise ValueError(
                f"repo source '{source_repo}' non enregistré pour le tracker '{tracker}'"
            )
        source = bindings[source_repo]
        if not isinstance(source, dict) or source.get("archive") is True:
            raise ValueError(
                f"repo source '{source_repo}' archivé pour le tracker '{tracker}'"
            )

        def copy_source() -> dict[str, object]:
            """Return the source binding, revalidating a Linear alias before save."""
            if tracker != "linear":
                return dict(source)
            project_id, extra = _validate_linear_binding(
                alias_repo,
                source.get("id"),
                {
                    name: value for name, value in source.items()
                    if name not in {"key", "id"}
                },
            )
            key = source.get("key")
            if not isinstance(key, str) or not key:
                raise ValueError("binding Linear invalide : key absent")
            return {"key": key, "id": project_id, **extra}

        current = bindings.get(alias_repo)
        if isinstance(current, dict) and current.get("archive") is True:
            raise ValueError(
                f"binding archive '{tracker}/{alias_repo}' immuable sans rollback explicite"
            )
        if current:
            source_identity = (source.get("key"), source.get("id"))
            current_identity = (current.get("key"), current.get("id"))
            if current_identity != source_identity:
                raise ValueError(
                    f"repo alias '{alias_repo}' déjà lié à {current.get('key', '?')} "
                    f"({current.get('id', '?')})"
                )
            if current == source:
                return False
            # Same project, stale metadata: the explicitly named source is canonical.
            data[tracker][alias_repo] = copy_source()
            _save(data)
            return True
        data.setdefault(tracker, {})[alias_repo] = copy_source()
        _save(data)
        return True


def _parse_extra_arguments(
    values: list[str], usage: str, *, tracker: str,
) -> dict[str, object]:
    """Parse CLI ``k=v`` extras and decode only Linear's declared JSON maps."""
    try:
        pairs = []
        for argument in values:
            name, value = argument.split("=", 1)
            pairs.append((name, value))
    except ValueError:
        raise SystemExit(
            f"{usage}\nles extras doivent utiliser la forme k=v"
        ) from None

    if tracker != "linear":
        return dict(pairs)

    extras: dict[str, object] = {}
    for name, value in pairs:
        if name in _LINEAR_STRUCTURED_EXTRA_KEYS:
            try:
                structured_value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                raise SystemExit(
                    f"{usage}\nles extras structurés doivent être des objets JSON valides"
                ) from None
            if not isinstance(structured_value, dict):
                raise SystemExit(
                    f"{usage}\nles extras structurés doivent être des objets JSON valides"
                )
            extras[name] = structured_value
        else:
            extras[name] = value
    return extras


def main(argv=None) -> None:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    usage = (
        "usage: registry [register <tracker> <repo> <KEY> <project-id> [k=v …] | "
        "alias <tracker> <source-repo> <alias-repo> | "
        "cutover <tracker> <KEY> <project-id> <migration-manifest-sha256>]"
    )
    if not args:
        print(json.dumps(load(), indent=2))
        return
    if args[0] in ("-h", "--help"):
        print(usage)
        return
    if args[0] == "register":
        if len(args) < 5:
            raise SystemExit(usage)
        _, tracker, repo, key, pid, *rest = args
        extra = _parse_extra_arguments(rest, usage, tracker=tracker)
        register(tracker, repo, key, pid, **extra)
        print(f"registered {repo} -> {key} ({tracker})")
        return
    if args[0] == "alias":
        if len(args) != 4:
            raise SystemExit(usage)
        _, tracker, source_repo, alias_repo = args
        try:
            created = register_alias(tracker, source_repo, alias_repo)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        verb = "registered" if created else "already registered"
        print(f"{verb} alias {alias_repo} -> {source_repo} ({tracker})")
        return
    if args[0] == "cutover":
        if len(args) != 5:
            raise SystemExit(usage)
        _, tracker, key, project_id, manifest_digest = args
        try:
            binding = cutover_repository_tracker(
                tracker, key, project_id,
                migration_manifest_digest=manifest_digest,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        print(json.dumps({
            "repository": binding.repository,
            "tracker": binding.tracker,
            "project": {"key": binding.project.key, "id": binding.project.id},
            "registry_binding_digest": binding.registry_binding_digest,
            "migration_manifest_digest": binding.migration_manifest_digest,
            "configuration_digest": binding.configuration_digest,
        }, indent=2, sort_keys=True))
        return
    raise SystemExit(usage)


if __name__ == "__main__":
    main()
