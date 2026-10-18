"""Prepare the pinned tooling without installing anything globally (PAT-252).

`plan()` describes every step and touches nothing; `perform()` runs them, each download
verified against its pinned checksum (mismatch: the file is deleted and the step fails).
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from . import paths, pins, proc

SHIPPED_REA = Path(__file__).resolve().parent.parent / "rea"
NPM_CI = ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"]


class SetupError(Exception):
    pass


def java_home(d: Path) -> Path:
    return paths.jdk_dir(d, pins.JDK_DIRNAME) / "Contents" / "Home"


def tool_env(d: Path) -> dict[str, str]:
    """Child environment for REA/Gradle: an allow-list plus the pinned JDK and Ghidra."""
    env = {k: os.environ[k] for k in ("HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "LOGNAME") if k in os.environ}
    jh = java_home(d)
    env["JAVA_HOME"] = str(jh)
    env["GHIDRA_INSTALL_DIR"] = str(paths.ghidra_dir(d, pins.GHIDRA_DIRNAME))
    env["GRADLE_USER_HOME"] = str(paths.gradle_home(d))
    env["npm_config_cache"] = str(d / "npm-cache")
    env["PATH"] = os.pathsep.join([str(jh / "bin"), os.environ.get("PATH", "/usr/bin:/bin")])
    return env


def tools_ready(d: Path) -> list[str]:
    """Missing pieces, as short names; [] when ready."""
    checks = {
        "rea": paths.rea_bin(d),
        "ghidra": paths.ghidra_dir(d, pins.GHIDRA_DIRNAME) / "support" / "analyzeHeadless",
        "jdk": java_home(d) / "bin" / "java",
        "native-decompiler": paths.ghidra_dir(d, pins.GHIDRA_DIRNAME) / pins.NATIVE_DECOMPILER,
    }
    return [name for name, p in checks.items() if not p.exists()]


def plan(d: Path) -> list[dict]:
    dl, gh = paths.downloads(d), paths.ghidra_dir(d, pins.GHIDRA_DIRNAME)
    return [
        {"id": "rea", "title": f"REA {pins.REA_VERSION} (exact version, whole dependency tree locked)",
         "downloads": [{"url": pins.REA_TARBALL_URL, "checksum": pins.REA_INTEGRITY,
                        "size": "unknown (not recorded)",
                        "note": "plus every other package of the shipped lockfile, each verified by npm against its integrity"}],
         "writes": [str(paths.rea_dir(d)), str(d / "npm-cache")],
         "commands": [f"copy tooling/rea/package.json and package-lock.json into {paths.rea_dir(d)}",
                      f"cd {paths.rea_dir(d)} && " + " ".join(NPM_CI)]},
        {"id": "jdk", "title": f"Temurin JDK {pins.JDK_VERSION} (macOS aarch64)",
         "downloads": [{"url": pins.JDK_URL, "checksum": "sha256:" + pins.JDK_SHA256, "size": "unknown (not recorded)"}],
         "writes": [str(dl), str(paths.jdk_dir(d, pins.JDK_DIRNAME))],
         "commands": ["verify sha256, then extract the archive (no member may leave the target directory)"]},
        {"id": "ghidra", "title": f"Ghidra {pins.GHIDRA_VERSION}",
         "downloads": [{"url": pins.GHIDRA_URL, "checksum": "sha256:" + pins.GHIDRA_SHA256, "size": "unknown (not recorded)"}],
         "writes": [str(dl), str(gh)],
         "commands": ["verify sha256, then extract the archive (no member may leave the target directory)"]},
        {"id": "natives", "title": "Native macOS arm64 decompiler, compiled only if absent",
         "downloads": [{"url": f"https://services.gradle.org/distributions/gradle-{pins.GRADLE_VERSION}-bin.zip",
                        "checksum": "sha256:" + pins.GRADLE_DIST_SHA256, "size": "unknown (not recorded)",
                        "note": "fetched by the Gradle wrapper; the checksum is added to the wrapper properties of the extracted copy"}],
         "writes": [str(gh / pins.GRADLE_PROPERTIES), str(paths.gradle_home(d)), str(gh / pins.NATIVE_DECOMPILER)],
         "commands": [(f"cd {gh / pins.GRADLE_DIR} && JAVA_HOME={java_home(d)} GRADLE_USER_HOME={paths.gradle_home(d)} "
                      "./gradlew --no-daemon buildNatives")]},
    ]


def render_plan(d: Path) -> str:
    lines = [f"blueprint setup plan (nothing is done without --yes). Data directory: {d}",
             "Nothing is installed globally and nothing is written outside the data directory."]
    for n, s in enumerate(plan(d), 1):
        lines.append(f"\n{n}. [{s['id']}] {s['title']}")
        lines += [f"   downloads: {x['url']}\n     checksum: {x['checksum']}  size: {x['size']}"
                  + (f"\n     note: {x['note']}" if "note" in x else "") for x in s["downloads"]]
        lines += [f"   writes: {w}" for w in s["writes"]]
        lines += [f"   runs: {c}" for c in s["commands"]]
    missing = tools_ready(d)
    lines.append("\nstate now: " + ("ready" if not missing else "missing " + ", ".join(missing)))
    return "\n".join(lines)


def fetch(url: str, dest: Path, checksum: str, timeout: float = pins.TIMEOUT_DOWNLOAD) -> None:
    """Download over https to dest, verifying `checksum` (hex sha256); a mismatch deletes the file."""
    if not url.startswith("https://"):
        raise SetupError("refusing a non-https download")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp, open(part, "wb") as out:
            for chunk in iter(lambda: resp.read(1 << 20), b""):
                h.update(chunk)
                out.write(chunk)
    except (OSError, ValueError) as exc:
        part.unlink(missing_ok=True)
        raise SetupError(f"download failed ({type(exc).__name__})") from None
    if h.hexdigest() != checksum:
        part.unlink(missing_ok=True)
        raise SetupError(f"checksum mismatch for {url.rsplit('/', 1)[-1]}: expected {checksum}, got {h.hexdigest()}")
    os.replace(part, dest)


def _inside(root: Path, name: str) -> bool:
    return (root / name).resolve().is_relative_to(root.resolve())


def extract_tar(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        tf.extractall(dest, filter="data")  # rejects absolute paths, escapes and device files


def extract_zip(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if not _inside(dest, info.filename):
                raise SetupError("archive member outside the target directory")
        for info in zf.infolist():
            target = dest / info.filename
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = (info.external_attr >> 16) & 0o777
            if (info.external_attr >> 16) & 0o170000 == 0o120000:  # symlink
                link = zf.read(info).decode("utf-8")
                if not _inside(target.parent, link):
                    raise SetupError("archive symlink outside the target directory")
                target.symlink_to(link)
                continue
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            if mode:
                os.chmod(target, mode & 0o755 | 0o600)


def pin_gradle(props: Path) -> None:
    text = props.read_text(encoding="utf-8")
    if "distributionSha256Sum" in text:
        return
    props.write_text(text.rstrip("\n") + f"\ndistributionSha256Sum={pins.GRADLE_DIST_SHA256}\n", encoding="utf-8")


def perform(d: Path, log: Callable[[str], None] = print, *, fetcher=fetch, runner=proc.run,
            check_platform: bool = True) -> None:
    """Run the plan; idempotent (a step whose result exists is skipped). Raises SetupError."""
    if check_platform and (sys.platform != "darwin" or platform.machine() != "arm64"):
        raise SetupError("the pinned JDK and native decompiler are for macOS arm64 only")
    dl = paths.downloads(d)
    gh = paths.ghidra_dir(d, pins.GHIDRA_DIRNAME)

    log("[rea] install")
    if not paths.rea_bin(d).exists():
        paths.rea_dir(d).mkdir(parents=True, exist_ok=True)
        for name in ("package.json", "package-lock.json"):
            shutil.copyfile(SHIPPED_REA / name, paths.rea_dir(d) / name)
        rc, _, err = runner(NPM_CI, pins.TIMEOUT_NPM, cwd=paths.rea_dir(d), env=tool_env(d))
        if rc != 0:
            raise SetupError(f"npm ci failed ({err.strip()[-300:] or rc})")
    if not paths.rea_bin(d).exists():
        raise SetupError("the REA binary is absent after install")

    log("[jdk] install")
    if not (java_home(d) / "bin" / "java").exists():
        archive = dl / pins.JDK_URL.rsplit("/", 1)[-1]
        fetcher(pins.JDK_URL, archive, pins.JDK_SHA256)
        extract_tar(archive, paths.jdk_dir(d, pins.JDK_DIRNAME).parent)
    if not (java_home(d) / "bin" / "java").exists():
        raise SetupError("the JDK is absent after extraction")

    log("[ghidra] install")
    if not (gh / "support" / "analyzeHeadless").exists():
        archive = dl / pins.GHIDRA_URL.rsplit("/", 1)[-1]
        fetcher(pins.GHIDRA_URL, archive, pins.GHIDRA_SHA256)
        extract_zip(archive, gh.parent)
    if not (gh / "support" / "analyzeHeadless").exists():
        raise SetupError("Ghidra is absent after extraction")

    log("[natives] build if absent")
    if not (gh / pins.NATIVE_DECOMPILER).exists():
        pin_gradle(gh / pins.GRADLE_PROPERTIES)
        paths.gradle_home(d).mkdir(parents=True, exist_ok=True)
        os.chmod(gh / pins.GRADLEW, 0o755)
        # The build is driven from support/gradle (its own settings file), as in the 2026-10-18 trial; from the
        # Ghidra root Gradle finds no build ("does not contain a Gradle build", observed 2026-10-18).
        rc, _, err = runner([str(gh / pins.GRADLEW), "--no-daemon", "buildNatives"], pins.TIMEOUT_BUILD,
                            cwd=gh / pins.GRADLE_DIR, env=tool_env(d))
        if rc != 0:
            raise SetupError(f"buildNatives failed ({err.strip()[-300:] or rc})")
        # buildNatives leaves its outputs under <module>/build/os/mac_arm_64; the installed layout reads
        # <module>/os/mac_arm_64 (observed 2026-10-18: the trial copied them by hand).
        for module in pins.NATIVE_MODULES:
            built = gh / module / "build" / "os" / pins.NATIVE_PLATFORM
            if built.is_dir():
                shutil.copytree(built, gh / module / "os" / pins.NATIVE_PLATFORM, dirs_exist_ok=True)
    if not (gh / pins.NATIVE_DECOMPILER).exists():
        raise SetupError("the native decompiler is absent after the build")
    log("setup complete")
