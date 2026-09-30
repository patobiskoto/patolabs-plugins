"""Fetch the locale-neutral changelog for a milestone from Foundry, if present.

Deliberately THIN: it only pulls FACTS (the `query changelog` payload) and prints them.
It does NOT write release notes — turning facts into user-facing copy, per locale, is a
language task the ship-ios:release skill does, not a script. Foundry is an OPTIONAL
input: no Foundry, no problem — the skill takes a changelog file instead.

Usage:
  changelog_bridge.py <MILESTONE> [--foundry-cli /path/to/foundry_cli.py] [--repo /app]
  changelog_bridge.py <MILESTONE> --standalone

Exit codes: 0 ok · 2 configured Foundry/binding/payload failure · 3 no Foundry
installation · 4 not an application Git checkout.  Only exit 3 permits the caller to
offer the explicit standalone file path; a configured Foundry failure must be fixed.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BRIDGE_CONTRACT = "ship-ios.foundry-changelog-bridge.v1"
_V1_TRACKERS = frozenset({"youtrack", "linear", "ghprojects"})
_DISPOSITIONS = ("accepted", "deviated", "unfinished", "unavailable")


def _marker_path(home: Path) -> Path:
    # Cross-plugin discovery must not use PLUGIN_DATA/CLAUDE_PLUGIN_DATA: each plugin
    # gets a private directory, so ship-ios cannot read Foundry's marker there.
    return home / ".config/foundry/install.json"


def _marked_cli(home: Path) -> str | None:
    marker = _marker_path(home)
    try:
        value = json.loads(marker.read_text(encoding="utf-8")).get("foundry_cli")
    except (OSError, ValueError, TypeError):
        return None
    return value if value and os.path.isfile(value) else None


def _find_cli(explicit: str | None, *, home: Path | None = None,
              plugin_root: Path | None = None) -> str | None:
    """Find Foundry across dev checkout, Claude cache, Codex cache, or PATH."""
    home = home or Path.home()
    plugin_root = plugin_root or Path(__file__).resolve().parents[1]
    direct = [
        explicit,
        os.environ.get("FOUNDRY_CLI"),
        _marked_cli(home),
        str(plugin_root.parent / "foundry/tooling/foundry_cli.py"),
        str(home / ".claude/plugins/marketplaces/patolabs/plugins/foundry/tooling/foundry_cli.py"),
        shutil.which("foundry_cli.py"),
    ]
    cache_patterns = (
        home / ".claude/plugins/cache/patolabs/foundry/*/tooling/foundry_cli.py",
        home / ".codex/plugins/cache/patolabs/foundry/*/tooling/foundry_cli.py",
    )
    cached = [Path(path) for pattern in cache_patterns for path in glob.glob(str(pattern))]
    cached.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    seen = set()
    for guess in [*direct, *(str(path) for path in cached)]:
        if guess and guess not in seen and os.path.isfile(guess):
            return guess
        seen.add(guess)
    return None


def _repository_root(repository: str | None) -> Path | None:
    """Return the app checkout root, never the directory holding this plugin."""
    candidate = Path(repository or os.getcwd()).expanduser()
    try:
        result = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip()).resolve()


def _foundry_version(cli: str) -> str | None:
    """Read an adjacent package manifest when the installation exposes one.

    The capability probe below remains authoritative: development checkouts and a
    launcher copied by a host need not have a distributable manifest beside them.
    """
    root = Path(cli).resolve().parent.parent
    versions = set()
    for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        try:
            version = json.loads((root / relative).read_text(encoding="utf-8")).get("version")
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(version, str) and version:
            versions.add(version)
    return next(iter(versions)) if len(versions) == 1 else None


def _validate_selection(value: object) -> dict[str, object]:
    """Validate the public V1 repository-selection response, not an adapter shape."""
    if not isinstance(value, dict):
        raise TypeError("selection is not an object")
    project = value.get("project")
    if (
        value.get("mode") != "v1"
        or value.get("tracker") not in _V1_TRACKERS
        or not isinstance(project, dict)
        or not isinstance(project.get("key"), str)
        or not project["key"]
        or not isinstance(project.get("id"), str)
        or not project["id"]
        or not isinstance(value.get("repository"), str)
        or not value["repository"]
        or not isinstance(value.get("configuration_digest"), str)
        or not value["configuration_digest"]
    ):
        raise ValueError("invalid V1 repository selection")
    return value


def _validate_changelog(value: object, *, milestone: str,
                        selection: dict[str, object]) -> dict[str, object]:
    """Validate the whole release-scope contract, including excluded issue facts."""
    if not isinstance(value, dict):
        raise TypeError("payload is not an object")
    required = {
        "contract", "project", "milestone", "count", "groups",
        "scope_count", "counts", "categories", "release_scope",
    }
    if not required.issubset(value):
        raise ValueError("payload misses required fields")
    count, groups = value["count"], value["groups"]
    if (
        value["contract"] != "foundry.release-scope.v1"
        or value["project"] != selection["project"]["key"]
        or value["milestone"] != milestone
        or type(count) is not int
        or count < 0
        or not isinstance(groups, dict)
    ):
        raise ValueError("payload has inconsistent project, milestone, count, or groups")
    scope_count, counts, categories = (
        value["scope_count"], value["counts"], value["categories"]
    )
    if (
        type(scope_count) is not int or scope_count < 0
        or not isinstance(counts, dict) or set(counts) != set(_DISPOSITIONS)
        or any(type(counts[name]) is not int or counts[name] < 0 for name in _DISPOSITIONS)
        or sum(counts.values()) != scope_count
        or count != counts["accepted"] + counts["deviated"]
        or not isinstance(categories, dict)
        or set(categories) != set(_DISPOSITIONS)
    ):
        raise ValueError("payload counts are inconsistent")
    facts = {}
    for name in _DISPOSITIONS:
        rows = categories[name]
        if not isinstance(rows, list) or len(rows) != counts[name]:
            raise ValueError("payload category is inconsistent")
        for issue in rows:
            if (
                not isinstance(issue, dict)
                or not isinstance(issue.get("id"), str) or not issue["id"]
                or not isinstance(issue.get("title"), str)
                or issue.get("type") is not None and not isinstance(issue["type"], str)
                or issue.get("state") is not None and not isinstance(issue["state"], str)
                or not isinstance(issue.get("labels"), list)
                or any(not isinstance(label, str) for label in issue["labels"])
                or issue.get("disposition") != name
                or not isinstance(issue.get("references"), dict)
                or issue["id"] in facts
            ):
                raise ValueError("payload issue fact is malformed or duplicated")
            facts[issue["id"]] = issue
    if len(facts) != scope_count:
        raise ValueError("payload scope count is inconsistent")
    shipped = set()
    for group, issues in groups.items():
        if not isinstance(group, str) or not group or not isinstance(issues, list):
            raise ValueError("payload group is malformed")
        for issue in issues:
            if (
                not isinstance(issue, dict)
                or issue.get("id") not in facts
                or issue["id"] in shipped
                or facts[issue["id"]]["disposition"] not in {"accepted", "deviated"}
                or group != (facts[issue["id"]]["type"] or "(sans type)")
                or any(issue.get(key) != facts[issue["id"]][key] for key in (
                    "title", "state", "labels", "disposition", "references"
                ))
            ):
                raise ValueError("payload issue is malformed")
            shipped.add(issue["id"])
    if shipped != {
        issue["id"] for name in ("accepted", "deviated") for issue in categories[name]
    }:
        raise ValueError("payload groups omit or add shipped issues")
    scope = value["release_scope"]
    if (
        not isinstance(scope, dict)
        or scope.get("provider") != selection["tracker"]
        or scope.get("project_key") != value["project"]
        or scope.get("project_id") != selection["project"]["id"]
        or scope.get("release") != milestone
        or not isinstance(scope.get("release_id"), str) or not scope["release_id"]
        or not isinstance(scope.get("closure"), dict)
        or not isinstance(scope.get("issues"), list)
        or len(scope["issues"]) != scope_count
        or any(
            not isinstance(issue, dict)
            or not isinstance(issue.get("id"), str)
            or issue != facts.get(issue["id"])
            for issue in scope["issues"]
        )
        or {issue["id"] for issue in scope["issues"]} != set(facts)
    ):
        raise ValueError("payload release scope is inconsistent")
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("milestone")
    ap.add_argument("--foundry-cli")
    ap.add_argument("--repo", help="application Git checkout (defaults to current directory)")
    ap.add_argument(
        "--standalone", action="store_true",
        help="explicitly elect the no-Foundry file workflow; do not discover Foundry",
    )
    ap.add_argument(
        "--require-v1-binding", action="store_true",
        help="refuse a legacy repository binding before querying the changelog",
    )
    args = ap.parse_args()

    if args.standalone:
        print(json.dumps({"contract": BRIDGE_CONTRACT, "mode": "standalone",
                          "milestone": args.milestone}, ensure_ascii=False, indent=2))
        return 0

    repository = _repository_root(args.repo)
    if repository is None:
        print("Le pont doit être exécuté depuis le checkout Git de l'application "
              "(ou avec --repo).", file=sys.stderr)
        return 4

    if args.foundry_cli and not os.path.isfile(args.foundry_cli):
        print("Chemin Foundry explicite introuvable.", file=sys.stderr)
        return 2
    cli = _find_cli(args.foundry_cli)
    if not cli:
        print("Foundry introuvable — passe un fichier changelog au skill à la place.",
              file=sys.stderr)
        return 3

    selection_command = ["python3", cli, "registry", "selection"]
    if args.require_v1_binding:
        selection_command.append("--require-v1")
    selection = subprocess.run(selection_command, cwd=repository, capture_output=True,
                               text=True, check=False)
    if selection.returncode != 0:
        print(selection.stderr or selection.stdout, file=sys.stderr)
        return 2
    try:
        selected = _validate_selection(json.loads(selection.stdout))
    except (TypeError, ValueError, json.JSONDecodeError):
        print("Sélection V1 du tracker Foundry invalide.", file=sys.stderr)
        return 2

    r = subprocess.run(
        ["python3", cli, "query", "changelog", args.milestone],
        cwd=repository, capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        print(r.stderr or r.stdout, file=sys.stderr)
        return 2
    try:
        data = _validate_changelog(json.loads(r.stdout), milestone=args.milestone,
                                   selection=selected)
    except (TypeError, ValueError, json.JSONDecodeError):
        print("Payload changelog Foundry invalide.", file=sys.stderr)
        return 2
    # Re-emit validated, locale-neutral facts with the stable bridge contract.  Never
    # carry provider output beyond this public shape or turn an error into an empty set.
    print(json.dumps({"contract": BRIDGE_CONTRACT, "mode": "foundry-v1",
                      "foundry_version": _foundry_version(cli),
                      "selection": selected, "project": data["project"],
                      "milestone": data["milestone"], "count": data["count"],
                      "groups": data["groups"], "scope_count": data["scope_count"],
                      "counts": data["counts"], "categories": data["categories"],
                      "release_scope": data["release_scope"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
