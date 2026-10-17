"""Human approval of an Epic campaign preview (PAT-215, PAT-ADR-0026).

One append-only JSONL journal in Foundry's local data directory
(``registry.data_dir()``, outside any repository) records who-approved-what: the Epic, the
exact ``preview_digest``, the ``structure_digest``, an expiry and session ceilings.  The
format and the file name name no tracker.

This module calls no tracker, starts no campaign and writes nothing else than that journal.
``check`` is a pure read: a cadencer calls it before any effect and gets either the valid
approval or a typed refusal.  A preview never approves itself: ``epic_preview`` has no
approval code path.

An agent session cannot approve by an accidental or cooperative call: ``approve`` reads its
confirmation from the controlling terminal only and refuses under an agent-session
environment marker, and the Claude ``PreToolUse`` Bash guard denies the command; a
deliberate bypass is not stopped.

Limit: this is not cryptography.  A process running as the same OS user can append to the
journal directly.  ``check`` refuses a line whose ``record_digest`` does not verify (a
malformed hand-append), but cannot detect a well-formed forged line.  An approval expires at
the latest when the approved preview's ``policy.expires_at`` expires.  See
``docs/campaign-approval.md``.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from foundry import registry
from foundry.epic_preview import ISSUE_ID, PREVIEW_CONTRACT, _canonical, _digest

APPROVAL_CONTRACT = "foundry-campaign-approval.v1"
JOURNAL_NAME = "campaign-approvals.jsonl"
APPROVER_KIND = "human-tty"
DEFAULT_TTL = timedelta(hours=24)
MAX_TTL = timedelta(days=7)
MAX_SESSIONS_CEILING = 1000
CONFIRMATION_LENGTH = 12
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_KEYS = {
    "schema_version", "epic", "preview_contract", "preview_digest", "structure_digest",
    "issued_at", "expires_at", "max_sessions", "max_sessions_per_issue", "approved_at",
    "approver", "record_digest",
}


class ApprovalError(ValueError):
    """Base class of every refusal; ``code`` is the stable machine-readable cause."""

    code = "refused"


class ApprovalAbsent(ApprovalError):
    code = "absent"


class ApprovalExpired(ApprovalError):
    code = "expired"


class PreviewDigestMismatch(ApprovalError):
    code = "preview_digest_mismatch"


class StructureDigestMismatch(ApprovalError):
    code = "structure_digest_mismatch"


class JournalInvalid(ApprovalError):
    code = "journal_invalid"


class ApproverRefused(ApprovalError):
    """The caller is not a human on a controlling terminal."""

    code = "approver_refused"


def journal_path() -> Path:
    return Path(registry.data_dir()) / JOURNAL_NAME


def _aware(now: datetime | None) -> datetime:
    """``now`` or the current UTC time; a naive ``now`` is refused, never read as local."""
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ApprovalError("horodatage naïf refusé : un fuseau est requis")
    return now


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_stamp(value: object) -> datetime:
    if not isinstance(value, str) or _UTC.fullmatch(value) is None:
        raise JournalInvalid("horodatage hors contrat")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def record_digest(record: dict) -> str:
    return hashlib.sha256(
        _canonical({key: value for key, value in record.items() if key != "record_digest"})
    ).hexdigest()


def _validate_record(record: object) -> dict:
    if not isinstance(record, dict) or set(record) != _KEYS:
        raise JournalInvalid("enregistrement d'approbation incomplet ou clés inconnues")
    if record["schema_version"] != APPROVAL_CONTRACT or record["preview_contract"] != PREVIEW_CONTRACT:
        raise JournalInvalid("contrat d'approbation inconnu")
    if not isinstance(record["epic"], str) or ISSUE_ID.fullmatch(record["epic"]) is None:
        raise JournalInvalid("identifiant d'Epic hors contrat")
    for key in ("preview_digest", "structure_digest", "record_digest"):
        if not isinstance(record[key], str) or _HEX64.fullmatch(record[key]) is None:
            raise JournalInvalid(f"{key} hors contrat")
    for key in ("issued_at", "expires_at", "approved_at"):
        _parse_stamp(record[key])
    sessions, per_issue = record["max_sessions"], record["max_sessions_per_issue"]
    if type(sessions) is not int or not 0 < sessions <= MAX_SESSIONS_CEILING:
        raise JournalInvalid("max_sessions hors contrat")
    if per_issue is not None and (type(per_issue) is not int or not 0 < per_issue <= sessions):
        raise JournalInvalid("max_sessions_per_issue hors contrat")
    if record["approver"] != {"kind": APPROVER_KIND}:
        raise JournalInvalid("approbateur hors contrat")
    if record_digest(record) != record["record_digest"]:
        raise JournalInvalid("empreinte d'enregistrement invalide")
    return record


def read_journal(path: Path | None = None) -> list[dict]:
    """Every record, or ``JournalInvalid`` when any line fails: nothing is trusted then."""
    target = path or journal_path()
    try:
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise JournalInvalid("journal d'approbation illisible") from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)  # waits for an append in progress
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read()
    except OSError as exc:
        raise JournalInvalid("journal d'approbation illisible") from exc
    finally:
        os.close(descriptor)
    records = []
    for line in raw.split(b"\n"):
        if not line:
            continue
        try:
            records.append(_validate_record(json.loads(line.decode("utf-8"))))
        except (ValueError, UnicodeDecodeError) as exc:
            if isinstance(exc, JournalInvalid):
                raise
            raise JournalInvalid("ligne de journal illisible") from exc
    return records


def check(
    epic: str, preview_digest: str, structure_digest: str, now: datetime | None = None,
    path: Path | None = None,
) -> dict:
    """The valid approval for exactly these digests, or a typed refusal.  No effect."""
    moment = _aware(now)
    own = [record for record in read_journal(path) if record["epic"] == epic]
    if not own:
        raise ApprovalAbsent(f"aucune approbation pour {epic}")
    same_preview = [r for r in own if r["preview_digest"] == preview_digest]
    if not same_preview:
        raise PreviewDigestMismatch(f"empreinte d'aperçu différente de celle approuvée pour {epic}")
    exact = [r for r in same_preview if r["structure_digest"] == structure_digest]
    if not exact:
        raise StructureDigestMismatch(
            f"empreinte de structure différente de celle approuvée pour {epic}"
        )
    live = [r for r in exact if _parse_stamp(r["expires_at"]) > moment]
    if not live:
        raise ApprovalExpired(f"approbation expirée pour {epic}")
    return max(live, key=lambda r: (r["approved_at"], r["record_digest"]))


def digests_from_preview(preview: object, epic: str) -> tuple[str, str]:
    """Validate a ``foundry-epic-preview.v1`` result offline; return its two digests."""
    if not isinstance(preview, dict) or preview.get("contract") != PREVIEW_CONTRACT:
        raise ApprovalError("fichier d'aperçu hors contrat")
    snapshot = preview.get("snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("root") != epic:
        raise ApprovalError("l'aperçu ne porte pas sur cet Epic")
    declared, structure = preview.get("preview_digest"), preview.get("structure_digest")
    for value in (declared, structure):
        if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
            raise ApprovalError("empreintes d'aperçu absentes ou invalides")
    material = {k: v for k, v in preview.items() if k not in ("preview_digest", "structure_digest")}
    if _digest(material) != declared:
        raise ApprovalError("l'empreinte d'aperçu ne correspond pas au contenu du fichier")
    return declared, structure


def preview_expiry(preview: dict) -> datetime:
    """``policy.expires_at`` (epoch milliseconds) of a validated preview, as a UTC second."""
    policy = preview.get("policy")
    millis = policy.get("expires_at") if isinstance(policy, dict) else None
    if type(millis) is not int or millis <= 0:
        raise ApprovalError("l'aperçu ne porte pas d'expiration policy.expires_at valide")
    try:
        return datetime.fromtimestamp(millis // 1000, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ApprovalError("expiration d'aperçu hors bornes") from exc


def _load_preview(path: str, epic: str) -> tuple[dict, str, str]:
    try:
        with open(path, encoding="utf-8") as handle:
            preview = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ApprovalError("fichier d'aperçu illisible") from exc
    return (preview, *digests_from_preview(preview, epic))


def agent_markers(environ=None) -> list[str]:
    """Names (never values) of agent-session markers: ``CLAUDECODE``, ``CODEX_*``."""
    env = os.environ if environ is None else environ
    return sorted(name for name in env if name == "CLAUDECODE" or name.startswith("CODEX_"))


def _open_tty():
    """(reader, writer) on the controlling terminal; ``OSError`` when there is none."""
    return open("/dev/tty", "r", encoding="utf-8"), open("/dev/tty", "w", encoding="utf-8")


def _stdio_is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _confirm_on_tty(summary: str, expected: str) -> None:
    if not _stdio_is_tty():
        raise ApproverRefused("entrée et sortie standard doivent être un terminal")
    try:
        reader, writer = _open_tty()
    except OSError as exc:
        raise ApproverRefused("aucun terminal de contrôle (/dev/tty)") from exc
    with reader, writer:
        writer.write(f"{summary}\nRetape les {CONFIRMATION_LENGTH} premiers caractères de "
                     f"l'empreinte d'aperçu pour approuver : ")
        writer.flush()
        answer = reader.readline().strip()
    if answer != expected:
        raise ApproverRefused("confirmation différente : rien n'est enregistré")


def _append(record: dict, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise JournalInvalid("journal d'approbation non inscriptible, rien n'est écrit") from exc
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise JournalInvalid("journal d'approbation non inscriptible, rien n'est écrit") from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        data = memoryview(_canonical(record) + b"\n")
        while data:  # os.write may write fewer bytes than asked
            data = data[os.write(descriptor, data):]
        os.fsync(descriptor)
    except OSError as exc:
        raise JournalInvalid(
            "écriture du journal d'approbation non confirmée, l'enregistrement a pu être écrit"
        ) from exc
    finally:
        os.close(descriptor)


def approve(
    epic: str, preview_path: str, *, max_sessions: int, max_sessions_per_issue: int | None,
    ttl: timedelta = DEFAULT_TTL, now: datetime | None = None, path: Path | None = None,
) -> dict:
    markers = agent_markers()
    if markers:
        raise ApproverRefused("session d'agent détectée (" + ", ".join(markers) + ")")
    if not timedelta(0) < ttl <= MAX_TTL:
        raise ApprovalError(f"durée hors bornes (maximum {MAX_TTL.days} jours)")
    if not 0 < max_sessions <= MAX_SESSIONS_CEILING:
        raise ApprovalError(f"max-sessions hors bornes (1 à {MAX_SESSIONS_CEILING})")
    if max_sessions_per_issue is not None and not 0 < max_sessions_per_issue <= max_sessions:
        raise ApprovalError("max-sessions-per-issue hors bornes (1 à max-sessions)")
    preview, preview_digest, structure = _load_preview(preview_path, epic)
    moment = _aware(now).replace(microsecond=0)
    preview_end = preview_expiry(preview)
    if preview_end <= moment:
        raise ApprovalError("l'aperçu est déjà expiré (policy.expires_at) : rien n'est écrit")
    expiry = min(moment + ttl, preview_end)
    record = {
        "schema_version": APPROVAL_CONTRACT, "epic": epic, "preview_contract": PREVIEW_CONTRACT,
        "preview_digest": preview_digest, "structure_digest": structure,
        "issued_at": _stamp(moment), "expires_at": _stamp(expiry),
        "max_sessions": max_sessions, "max_sessions_per_issue": max_sessions_per_issue,
        "approved_at": _stamp(moment), "approver": {"kind": APPROVER_KIND},
    }
    record["record_digest"] = record_digest(record)
    try:
        _validate_record(record)
    except JournalInvalid as exc:
        raise ApprovalError(f"enregistrement d'approbation refusé, rien n'est écrit : {exc}") from exc
    summary = (f"Epic {epic}\n  preview_digest   {preview_digest}\n"
               f"  structure_digest {structure}\n  expire {_stamp(expiry)}"
               + (" (plafonné par l'expiration de l'aperçu)" if expiry < moment + ttl else "")
               + f", {max_sessions} sessions au plus"
               + (f", {max_sessions_per_issue} par issue" if max_sessions_per_issue else ""))
    _confirm_on_tty(summary, preview_digest[:CONFIRMATION_LENGTH])
    _append(record, path or journal_path())
    return record


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("entier strictement positif attendu")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Approbation humaine d'une campagne d'Epic")
    sub = parser.add_subparsers(dest="verb", required=True)
    approve_parser = sub.add_parser("approve")
    approve_parser.add_argument("epic")
    approve_parser.add_argument("--preview-file", required=True)
    approve_parser.add_argument("--max-sessions", required=True, type=_positive_int)
    approve_parser.add_argument("--max-sessions-per-issue", type=_positive_int)
    approve_parser.add_argument("--ttl-hours", type=_positive_int, default=24)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("epic")
    check_parser = sub.add_parser("check")
    check_parser.add_argument("epic")
    check_parser.add_argument("--preview-file", required=True)
    args = parser.parse_args(argv)
    try:
        if args.verb == "approve":
            result = approve(
                args.epic, args.preview_file, max_sessions=args.max_sessions,
                max_sessions_per_issue=args.max_sessions_per_issue,
                ttl=timedelta(hours=args.ttl_hours),
            )
        elif args.verb == "status":
            result = [r for r in read_journal() if r["epic"] == args.epic]
        else:
            _, preview_digest, structure = _load_preview(args.preview_file, args.epic)
            result = check(args.epic, preview_digest, structure)
    except ApprovalError as exc:
        print(json.dumps({"refused": exc.code, "message": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
