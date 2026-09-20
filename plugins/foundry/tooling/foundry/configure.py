"""Host-neutral Foundry configuration for Claude Code, Codex, and local use."""
from __future__ import annotations

import argparse
import getpass
import json
import urllib.parse
from pathlib import Path

from foundry import config, registry


def _normalize_devhub_url(value: str) -> str:
    from foundry.trackers.devhub import validate_base_url

    normalized = value.strip().rstrip("/")
    validate_base_url(normalized)
    return normalized


def _safe_url_for_show(value: str | None, *, devhub: bool = False) -> str | None:
    if not value:
        return value
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        return _normalize_devhub_url(value) if devhub else value
    except (TypeError, ValueError, UnicodeError):
        return "invalid"


def _record_installation() -> Path:
    marker = config.install_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    cli = Path(__file__).resolve().parents[1] / "foundry_cli.py"
    marker.write_text(json.dumps({"foundry_cli": str(cli)}, indent=2) + "\n", encoding="utf-8")
    marker.chmod(0o600)
    return marker


def set_values(url: str | None, tracker: str, codehost: str,
               devhub_url: str | None = None) -> None:
    values = {"FOUNDRY_TRACKER": tracker, "FOUNDRY_CODEHOST": codehost}
    if url:
        values["YOUTRACK_URL"] = url.strip().rstrip("/")
    if devhub_url:
        values["DEVHUB_URL"] = _normalize_devhub_url(devhub_url)
    path = config.write_settings(values)
    marker = _record_installation()
    print(f"✅ configuration non sensible : {path}")
    print(f"📍 installation Foundry enregistrée : {marker}")


def token() -> None:
    value = getpass.getpass("YouTrack permanent token: ").strip()
    if not value:
        raise SystemExit("⛔ Token vide — aucun changement.")
    try:
        config.store_token(value)
    except RuntimeError as exc:
        raise SystemExit(f"⛔ {exc}") from None
    _record_installation()
    print("✅ token YouTrack stocké dans le keychain.")


def credential(name: str) -> None:
    value = getpass.getpass(f"{name}: ").strip()
    if not value:
        raise SystemExit("⛔ Credential vide — aucun changement.")
    try:
        config.store_secret(name, value)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"⛔ {exc}") from None
    _record_installation()
    print(f"✅ {name} stocké dans le keychain.")


def show() -> None:
    # Resolve and validate public URLs first. A malformed legacy value is reported
    # by state only, never echoed (including URL user-info credentials).
    youtrack_url = _safe_url_for_show(config.get_public("YOUTRACK_URL"))
    devhub_url = _safe_url_for_show(
        config.get_public("DEVHUB_URL"), devhub=True,
    )
    print(json.dumps({
        "YOUTRACK_URL": youtrack_url,
        "YOUTRACK_TOKEN": "configured" if config.get("YOUTRACK_TOKEN") else "missing",
        "LINEAR_API_TOKEN": "configured" if config.get("LINEAR_API_TOKEN") else "missing",
        "DEVHUB_URL": devhub_url,
        "DEVHUB_TRACKER_TOKEN": "configured" if config.get("DEVHUB_TRACKER_TOKEN") else "missing",
        "DEVHUB_TRACKER_PROOF_SECRET": "configured" if config.get("DEVHUB_TRACKER_PROOF_SECRET") else "missing",
        "DEVHUB_COMMAND_TOKEN": "configured" if config.get("DEVHUB_COMMAND_TOKEN") else "missing",
        "FOUNDRY_TRACKER": config.tracker_name(),
        "FOUNDRY_CODEHOST": config.codehost_name(),
        "config_file": str(config.default_config_path()),
        "data_dir": registry.data_dir(),
    }, indent=2))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Configure Foundry across Claude Code and Codex")
    sub = parser.add_subparsers(dest="command", required=True)
    set_parser = sub.add_parser("set", help="write non-secret settings")
    set_parser.add_argument("--url")
    set_parser.add_argument("--devhub-url")
    set_parser.add_argument("--tracker", default="youtrack")
    set_parser.add_argument("--codehost", default="github")
    sub.add_parser("token", help="store the YouTrack token interactively in the OS keychain")
    credential_parser = sub.add_parser("credential", help="store an allowlisted provider credential")
    credential_parser.add_argument(
        "--name", required=True,
        choices=[
            "YOUTRACK_TOKEN", "DEVHUB_TRACKER_TOKEN",
            "DEVHUB_TRACKER_PROOF_SECRET", "DEVHUB_COMMAND_TOKEN",
            "LINEAR_API_TOKEN",
        ],
    )
    sub.add_parser("show", help="show resolved settings with the token redacted")
    args = parser.parse_args(argv)
    if args.command == "set":
        try:
            set_values(args.url, args.tracker, args.codehost, args.devhub_url)
        except ValueError as exc:
            raise SystemExit(f"⛔ {exc}") from None
    elif args.command == "token":
        token()
    elif args.command == "credential":
        credential(args.name)
    else:
        show()


if __name__ == "__main__":
    main()
