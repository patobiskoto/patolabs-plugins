"""Where blueprint writes (PAT-252): one data directory outside any repository."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_DATA_DIR = "BLUEPRINT_DATA_DIR"


def data_dir() -> Path:
    env = os.environ.get(ENV_DATA_DIR)
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "blueprint"
    return Path.home() / ".local" / "share" / "blueprint"


def downloads(d: Path) -> Path: return d / "downloads"
def rea_dir(d: Path) -> Path: return d / "rea"
def rea_bin(d: Path) -> Path: return d / "rea" / "node_modules" / ".bin" / "rea"
def ghidra_dir(d: Path, dirname: str) -> Path: return d / "ghidra" / dirname
def jdk_dir(d: Path, dirname: str) -> Path: return d / "jdk" / dirname
def gradle_home(d: Path) -> Path: return d / "gradle-home"
def registry_file(d: Path) -> Path: return d / "targets.json"
def inventories(d: Path) -> Path: return d / "inventories"
def work(d: Path) -> Path: return d / "work"
def decompilations(d: Path) -> Path: return d / "decompilations"
def payloads(d: Path) -> Path: return d / "payloads"
