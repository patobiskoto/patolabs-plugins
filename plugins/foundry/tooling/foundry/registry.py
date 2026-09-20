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

import json
import os
import re
import subprocess
import tempfile
import urllib.parse

from foundry.models import Project


_REPOSITORY_COMPONENT = re.compile(r"[A-Za-z0-9_.-]+")
_SSH_CONFIG_TIMEOUT_SECONDS = 5


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
    from foundry import config
    repo = repo_basename(cwd, use_env=use_env)
    if not repo:
        return None
    data = load()
    preferred = config.tracker_name()
    for tracker in [preferred, *sorted(k for k in data if k != preferred)]:
        e = data.get(tracker, {}).get(repo)
        if e:
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


def resolve(tracker: str, repo: str) -> Project:
    data = load().get(tracker, {})
    if repo not in data:
        known = ", ".join(data) or "(aucun)"
        raise SystemExit(
            f"Repo '{repo}' non enregistré pour le tracker '{tracker}'. "
            f"Connus : {known}. Enregistre-le : "
            f"python3 <plugin-root>/tooling/foundry_cli.py registry register "
            f"{tracker} {repo} <KEY> <project-id> — ou lance le skill foundry:doctor.")
    e = data[repo]
    return Project(key=e["key"], id=e["id"],
                   extra={k: v for k, v in e.items() if k not in ("key", "id")})


def register(tracker: str, repo: str, key: str, project_id: str, **extra) -> None:
    extra = dict(extra)
    if "canonical_repo" in extra:
        try:
            extra["canonical_repo"] = canonical_repository_identity(
                extra["canonical_repo"],
            )
        except ValueError:
            raise ValueError("canonical_repo invalide") from None
    data = load()
    data.setdefault(tracker, {})[repo] = {"key": key, "id": project_id, **extra}
    _save(data)


def register_alias(tracker: str, source_repo: str, alias_repo: str) -> bool:
    """Bind a renamed repo to an existing tracker project without provisioning one.

    Keep the source binding: old checkouts and hooks may still use it. Return False
    when the exact alias already exists, and refuse to overwrite another project.
    """
    data = load()
    bindings = data.get(tracker, {})
    if source_repo not in bindings:
        raise ValueError(
            f"repo source '{source_repo}' non enregistré pour le tracker '{tracker}'"
        )
    source = bindings[source_repo]
    current = bindings.get(alias_repo)
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
        data[tracker][alias_repo] = dict(source)
        _save(data)
        return True
    data.setdefault(tracker, {})[alias_repo] = dict(source)
    _save(data)
    return True


def main(argv=None) -> None:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    usage = (
        "usage: registry [register <tracker> <repo> <KEY> <project-id> [k=v …] | "
        "alias <tracker> <source-repo> <alias-repo>]"
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
        try:
            extra = dict(kv.split("=", 1) for kv in rest)
        except ValueError:
            raise SystemExit(f"{usage}\nles extras doivent utiliser la forme k=v") from None
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
    raise SystemExit(usage)


if __name__ == "__main__":
    main()
