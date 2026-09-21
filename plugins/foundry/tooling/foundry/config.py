"""Config resolution — the one place secrets and provider selection are read.

Priority (highest first):
  1. Direct environment variables (provider URL/credentials, FOUNDRY_*).
  2. Plugin userConfig, exported by Claude Code as CLAUDE_PLUGIN_OPTION_* env vars
     (`sensitive: true` values — the token — live in the OS keychain; the rest are
     plain plugin settings).
  3. Allowlisted patolabs Foundry keychain items (configured by the host-neutral
     `configure credential` command; macOS today).
  4. A dev env file: $FOUNDRY_CONFIG, else ~/.config/foundry/config.env.
  5. Legacy dev fallback: ~/.config/orfeo-poc/youtrack.env (pre-plugin location).

This lets the exact same tooling run under Claude Code, Codex, CI, or in-repo.
"""
from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path


_KEYCHAIN_SERVICE = "patolabs.foundry"
_KEYCHAIN_ACCOUNT = "YOUTRACK_TOKEN"
_KEYCHAIN_SECRETS = {
    "YOUTRACK_TOKEN",
    "LINEAR_API_TOKEN",
    "DEVHUB_TRACKER_TOKEN",
    "DEVHUB_TRACKER_PROOF_SECRET",
    "DEVHUB_COMMAND_TOKEN",
}
_PUBLIC_SETTINGS = {
    "YOUTRACK_URL",
    "DEVHUB_URL",
    "FOUNDRY_TRACKER",
    "FOUNDRY_CODEHOST",
}
RUNTIME_CONFIG_ISOLATION_ENV = "FOUNDRY_RUNTIME_CONFIG_ISOLATED"
_ERR_SEC_ITEM_NOT_FOUND = -25300
_SECURITY_TOOL = "/usr/bin/security"
# Test-only injection point. Production code always uses the user's default
# keychain and the fixed Foundry service namespace.
_TEST_KEYCHAIN_PATH: str | None = None


class _SecKeychainAttribute(ctypes.Structure):
    _fields_ = [
        ("tag", ctypes.c_uint32),
        ("length", ctypes.c_uint32),
        ("data", ctypes.c_void_p),
    ]


class _SecKeychainAttributeList(ctypes.Structure):
    _fields_ = [
        ("count", ctypes.c_uint32),
        ("attr", ctypes.POINTER(_SecKeychainAttribute)),
    ]


class _CFArrayCallBacks(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_long),
        ("retain", ctypes.c_void_p),
        ("release", ctypes.c_void_p),
        ("copy_description", ctypes.c_void_p),
        ("equal", ctypes.c_void_p),
    ]


def _runtime_config_isolated() -> bool:
    """Return whether this process must not resolve host Foundry configuration."""
    return os.environ.get(RUNTIME_CONFIG_ISOLATION_ENV) == "1"


def _keychain_service() -> str:
    """Return Foundry's fixed macOS generic-password service namespace."""
    return _KEYCHAIN_SERVICE


def _keychain_path() -> str | None:
    """Return the private, disposable-keychain injection used by integration tests."""
    return _TEST_KEYCHAIN_PATH


def _security_tool() -> str | None:
    """Return the stable system executable named by Foundry keychain ACLs."""
    return _SECURITY_TOOL if shutil.which("security") else None


def default_config_path() -> Path:
    explicit = os.environ.get("FOUNDRY_CONFIG")
    return Path(explicit).expanduser() if explicit else Path("~/.config/foundry/config.env").expanduser()


def install_marker_path() -> Path:
    """Shared discovery marker; never place it in a plugin-private data directory."""
    return Path("~/.config/foundry/install.json").expanduser()


def _dev_files() -> list[Path]:
    return [
        default_config_path(),
        Path("~/.config/orfeo-poc/youtrack.env").expanduser(),
    ]


def _load_dev_files() -> dict[str, str]:
    cfg: dict[str, str] = {}
    for path in _dev_files():
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg.setdefault(k.strip(), v.strip())
    return cfg


def _dev_file_value(key: str) -> tuple[bool, str | None]:
    """Read one named public value without parsing unrelated config entries.

    In particular, lines carrying legacy plaintext credentials are never split or
    retained in a dictionary on this path. Secret fallback resolution remains owned
    by :func:`get`, after callers have validated any relevant transport boundary.
    """
    for path in _dev_files():
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                separator = stripped.find("=")
                if separator < 0 or stripped[:separator].strip() != key:
                    continue
                return True, stripped[separator + 1:].strip()
    return False, None


def get_public(key: str, default: str | None = None) -> str | None:
    """Resolve one allowlisted non-secret setting without bulk-loading secrets."""
    normalized = key.upper()
    if normalized not in _PUBLIC_SETTINGS:
        raise ValueError("clé de configuration publique non supportée")
    env = os.environ.get(normalized)
    if env:
        return env
    env = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{normalized}")
    if env:
        return env
    if _runtime_config_isolated():
        return default
    found, value = _dev_file_value(normalized)
    return value if found else default


def require_public(key: str) -> str:
    value = get_public(key)
    if not value:
        raise SystemExit(
            f"Config manquante : {key}. Lance le skill foundry:configure, expose "
            f"{key} dans l'environnement, ou configure ~/.config/foundry/config.env."
        )
    return value


def _keychain_secret(account: str) -> str | None:
    security_tool = _security_tool()
    if platform.system() != "Darwin" or not security_tool:
        return None
    # The writer explicitly grants this stable system executable access. Python
    # upgrades therefore do not change the application identity used to decrypt.
    command = [security_tool, "find-generic-password", "-s", _keychain_service(),
               "-a", account, "-w"]
    if keychain_path := _keychain_path():
        command.append(keychain_path)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _keychain_token() -> str | None:
    """Backward-compatible YouTrack keychain accessor."""
    return _keychain_secret(_KEYCHAIN_ACCOUNT)


def _macos_keychain_libraries() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    """Load the documented Security.framework APIs used for confidential writes."""
    security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    core_foundation = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
    )
    return security, core_foundation


def _native_keychain_ref(security: ctypes.CDLL) -> ctypes.c_void_p:
    """Open the private test keychain, or return the default-keychain sentinel."""
    pointer = ctypes.c_void_p
    if not (keychain_path := _keychain_path()):
        return pointer()
    security.SecKeychainOpen.argtypes = [ctypes.c_char_p, ctypes.POINTER(pointer)]
    security.SecKeychainOpen.restype = ctypes.c_int32
    keychain = pointer()
    if security.SecKeychainOpen(os.fsencode(keychain_path), ctypes.byref(keychain)) != 0:
        raise RuntimeError("Échec d'accès au keychain macOS.")
    return keychain


def _stable_keychain_access(
    security: ctypes.CDLL,
    core_foundation: ctypes.CDLL,
) -> ctypes.c_void_p:
    """Create least-privilege access for Foundry's stable system reader.

    The local macOS SDK documents that ``SecAccessCreate`` grants access without
    confirmation only to the ``SecTrustedApplication`` values in its array, and
    that ``SecTrustedApplicationCreateFromPath`` binds one to a tool path.  Using
    ``/usr/bin/security`` avoids tying an item to whichever Python build writes it.
    """
    pointer = ctypes.c_void_p
    status = ctypes.c_int32
    core_foundation.CFStringCreateWithCString.argtypes = [
        pointer, ctypes.c_char_p, ctypes.c_uint32,
    ]
    core_foundation.CFStringCreateWithCString.restype = pointer
    core_foundation.CFArrayCreate.argtypes = [
        pointer, ctypes.POINTER(pointer), ctypes.c_long,
        ctypes.POINTER(_CFArrayCallBacks),
    ]
    core_foundation.CFArrayCreate.restype = pointer
    security.SecTrustedApplicationCreateFromPath.argtypes = [
        ctypes.c_char_p, ctypes.POINTER(pointer),
    ]
    security.SecTrustedApplicationCreateFromPath.restype = status
    security.SecAccessCreate.argtypes = [pointer, pointer, ctypes.POINTER(pointer)]
    security.SecAccessCreate.restype = status
    core_foundation.CFRelease.argtypes = [pointer]
    core_foundation.CFRelease.restype = None

    trusted = pointer()
    trusted_list = pointer()
    descriptor = pointer()
    access = pointer()
    try:
        if security.SecTrustedApplicationCreateFromPath(
            os.fsencode(_SECURITY_TOOL), ctypes.byref(trusted),
        ) != 0:
            raise RuntimeError("Échec de préparation du keychain macOS.")
        values = (pointer * 1)(trusted)
        callbacks = _CFArrayCallBacks.in_dll(
            core_foundation, "kCFTypeArrayCallBacks",
        )
        trusted_list = core_foundation.CFArrayCreate(
            None, values, 1, ctypes.byref(callbacks),
        )
        descriptor = core_foundation.CFStringCreateWithCString(
            None, b"patolabs Foundry credential", 0x08000100,
        )
        if not trusted_list or not descriptor:
            raise RuntimeError("Échec de préparation du keychain macOS.")
        if security.SecAccessCreate(
            descriptor, trusted_list, ctypes.byref(access),
        ) != 0:
            raise RuntimeError("Échec de préparation du keychain macOS.")
        return access
    finally:
        if descriptor:
            core_foundation.CFRelease(descriptor)
        if trusted_list:
            core_foundation.CFRelease(trusted_list)
        if trusted:
            core_foundation.CFRelease(trusted)


def _store_secret_macos(account: str, value: str) -> None:
    """Write through Security.framework, keeping password bytes out of argv.

    Apple's ``security add-generic-password -w`` command accepts its password as
    an argument (security(1)); its stdin is not the password transport.  The
    native API accepts an explicit byte buffer instead. New items receive an
    explicit ACL in the same creation call. Existing entries keep their established
    ACL while SecKeychainItemModifyAttributesAndData writes their permanent data
    store as one operation (SecKeychainItem.h).
    """
    security, core_foundation = _macos_keychain_libraries()
    pointer = ctypes.c_void_p
    status = ctypes.c_int32
    uint32 = ctypes.c_uint32
    security.SecKeychainOpen.argtypes = [ctypes.c_char_p, ctypes.POINTER(pointer)]
    security.SecKeychainOpen.restype = status
    security.SecKeychainFindGenericPassword.argtypes = [
        pointer, uint32, ctypes.c_char_p, uint32, ctypes.c_char_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(pointer),
    ]
    security.SecKeychainFindGenericPassword.restype = status
    security.SecKeychainItemCreateFromContent.argtypes = [
        uint32, ctypes.POINTER(_SecKeychainAttributeList), uint32,
        ctypes.c_void_p, pointer, pointer, ctypes.POINTER(pointer),
    ]
    security.SecKeychainItemCreateFromContent.restype = status
    security.SecKeychainItemModifyAttributesAndData.argtypes = [
        pointer, ctypes.c_void_p, uint32, ctypes.c_void_p,
    ]
    security.SecKeychainItemModifyAttributesAndData.restype = status
    security.SecKeychainGetUserInteractionAllowed.argtypes = [
        ctypes.POINTER(ctypes.c_ubyte),
    ]
    security.SecKeychainGetUserInteractionAllowed.restype = status
    security.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_ubyte]
    security.SecKeychainSetUserInteractionAllowed.restype = status
    core_foundation.CFRelease.argtypes = [pointer]
    core_foundation.CFRelease.restype = None

    keychain = _native_keychain_ref(security)

    service = _keychain_service().encode("utf-8")
    account_bytes = account.encode("utf-8")
    secret = value.encode("utf-8")
    secret_buffer = ctypes.create_string_buffer(secret, len(secret))
    service_buffer = ctypes.create_string_buffer(service, len(service))
    account_buffer = ctypes.create_string_buffer(account_bytes, len(account_bytes))
    attributes = (_SecKeychainAttribute * 2)(
        _SecKeychainAttribute(
            int.from_bytes(b"svce", "big"), len(service),
            ctypes.cast(service_buffer, pointer),
        ),
        _SecKeychainAttribute(
            int.from_bytes(b"acct", "big"), len(account_bytes),
            ctypes.cast(account_buffer, pointer),
        ),
    )
    attribute_list = _SecKeychainAttributeList(2, attributes)
    item = pointer()
    access = pointer()
    interaction = ctypes.c_ubyte()
    interaction_status = security.SecKeychainGetUserInteractionAllowed(
        ctypes.byref(interaction),
    )
    if interaction_status != 0:
        raise RuntimeError("Échec de préparation du keychain macOS.")
    try:
        if security.SecKeychainSetUserInteractionAllowed(0) != 0:
            raise RuntimeError("Échec de préparation du keychain macOS.")
        access = _stable_keychain_access(security, core_foundation)
        result = security.SecKeychainFindGenericPassword(
            keychain, len(service), service, len(account_bytes), account_bytes,
            None, None, ctypes.byref(item),
        )
        if result == 0:
            result = security.SecKeychainItemModifyAttributesAndData(
                item, None, len(secret), secret_buffer,
            )
        elif result == _ERR_SEC_ITEM_NOT_FOUND:
            result = security.SecKeychainItemCreateFromContent(
                int.from_bytes(b"genp", "big"), ctypes.byref(attribute_list),
                len(secret), secret_buffer, keychain, access, ctypes.byref(item),
            )
        if result != 0:
            raise RuntimeError("Échec d'écriture dans le keychain macOS.")
    finally:
        security.SecKeychainSetUserInteractionAllowed(interaction)
        if access:
            core_foundation.CFRelease(access)
        if item:
            core_foundation.CFRelease(item)
        if keychain:
            core_foundation.CFRelease(keychain)


def store_secret(account: str, value: str) -> None:
    """Store one non-empty allowlisted provider secret without exposing it."""
    if account not in _KEYCHAIN_SECRETS:
        raise ValueError("credential Foundry non supporté")
    if not value:
        raise ValueError("credential Foundry vide")
    if platform.system() != "Darwin" or not _security_tool():
        raise RuntimeError(
            "Stockage keychain automatique disponible sur macOS uniquement. "
            f"Expose {account} dans l'environnement sur cet hôte."
        )
    try:
        _store_secret_macos(account, value)
        # Do not claim success until the resolver's stable system reader sees the
        # exact bytes just written. This catches a host-level silent empty write.
        if _keychain_secret(account) != value:
            raise RuntimeError("keychain readback mismatch")
    except (OSError, RuntimeError):
        # Native framework errors can contain implementation details; never pass
        # through a message that might have been assembled near the secret bytes.
        raise RuntimeError("Échec d'écriture dans le keychain macOS.") from None


def store_token(token: str) -> None:
    """Backward-compatible YouTrack token writer."""
    store_secret(_KEYCHAIN_ACCOUNT, token)


def _secret_setting(key: str) -> bool:
    normalized = key.upper()
    return "TOKEN" in normalized or "SECRET" in normalized


def write_settings(values: dict[str, str]) -> Path:
    """Merge non-secret settings into Foundry's env file with private permissions."""
    invalid = {k for k, v in values.items()
               if not re.fullmatch(r"[A-Z][A-Z0-9_]*", k) or "\n" in v or "\r" in v}
    if invalid:
        raise ValueError(f"Clé ou valeur de configuration invalide : {', '.join(sorted(invalid))}")
    forbidden = {k for k in values if _secret_setting(k)}
    if forbidden:
        raise ValueError(f"Refus d'écrire des secrets en clair : {', '.join(sorted(forbidden))}")
    path = default_config_path()
    current: dict[str, str] = {}
    existing_secrets = set()
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    separator = stripped.find("=")
                    key = stripped[:separator].strip()
                    if _secret_setting(key):
                        existing_secrets.add(key)
                    else:
                        current[key] = stripped[separator + 1:].strip()
    if existing_secrets:
        raise ValueError(
            "Refus de réécrire config.env tant que des secrets en clair y sont présents : "
            + ", ".join(sorted(existing_secrets))
        )
    current.update({k: v for k, v in values.items() if v is not None})
    if "DEVHUB_URL" in current:
        from foundry.trackers.devhub import validate_base_url

        candidate = current["DEVHUB_URL"].strip().rstrip("/")
        validate_base_url(candidate)
        current["DEVHUB_URL"] = candidate
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in sorted(current.items())), encoding="utf-8")
    path.chmod(0o600)
    return path


def get(key: str, default: str | None = None) -> str | None:
    """Resolve a config key across both plugin hosts, keychain, and dev config."""
    normalized = key.upper()
    if _runtime_config_isolated() and _secret_setting(normalized):
        return default
    env = os.environ.get(normalized)
    if env:
        return env
    env = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{normalized}")
    if env:
        return env
    if _runtime_config_isolated():
        return default
    if normalized == "YOUTRACK_TOKEN":
        token = _keychain_token()
        if token:
            return token
    if normalized in _KEYCHAIN_SECRETS - {"YOUTRACK_TOKEN"}:
        value = _keychain_secret(normalized)
        if value:
            return value
    dev = _load_dev_files()
    if key in dev:
        return dev[key]
    return default


def require(key: str) -> str:
    v = get(key)
    if not v:
        raise SystemExit(
            f"Config manquante : {key}. Lance le skill foundry:configure, expose "
            f"{key} dans l'environnement, ou configure ~/.config/foundry/config.env.")
    return v


# Active providers are parameters, not constants.
def tracker_name() -> str:
    return get_public("FOUNDRY_TRACKER", "youtrack")


def codehost_name() -> str:
    return get_public("FOUNDRY_CODEHOST", "github")
