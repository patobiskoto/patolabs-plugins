"""Pinned artefacts (PAT-252). Observed in the 2026-10-18 trials; checksums verified then.

Every artefact has an exact version and a checksum.
"""
from __future__ import annotations

REA_VERSION = "6.5.0"
REA_TARBALL_URL = "https://registry.npmjs.org/rea-agents/-/rea-agents-6.5.0.tgz"
REA_INTEGRITY = ("sha512-C6BG7LyrAdR74mSjertM6PrxJkcNZ2bfQNOJornPo9erDEp2tyPRVYPnV5Yodr+1RqewCQla68WulXlVnveD+A==")

GHIDRA_VERSION = "12.1.4"
GHIDRA_URL = ("https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.4_build/"
              "ghidra_12.1.4_PUBLIC_20260921.zip")
GHIDRA_SHA256 = "ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db"
GHIDRA_DIRNAME = "ghidra_12.1.4_PUBLIC"

JDK_VERSION = "21.0.12.1+1"
JDK_URL = ("https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/"
           "OpenJDK21U-jdk_aarch64_mac_hotspot_21.0.12.1_1.tar.gz")
JDK_SHA256 = "3623232f33a9c3baadf304480b2535f9a3cba8a58d42ecbb438ba267315d9998"
JDK_DIRNAME = "jdk-21.0.12.1+1"

GRADLE_VERSION = "9.7.1"
GRADLE_DIST_SHA256 = "acd53f1edaf02f1a8ff99879f8a34b302661a057d9b063ae9e35b552f804d20a"
GRADLE_PROPERTIES = "support/gradle/gradle-wrapper.properties"
GRADLE_DIR = "support/gradle"
GRADLEW = GRADLE_DIR + "/gradlew"
NATIVE_PLATFORM = "mac_arm_64"
NATIVE_DECOMPILER = "Ghidra/Features/Decompiler/os/mac_arm_64/decompile"
# Modules whose buildNatives outputs are copied from build/os/<platform> to os/<platform>.
NATIVE_MODULES = ("Ghidra/Features/Decompiler", "GPL/DemanglerGnu", "Ghidra/Features/FileFormats")

# Seconds, per external process.
TIMEOUT_DOWNLOAD = 1800
TIMEOUT_NPM = 900
TIMEOUT_BUILD = 1800
TIMEOUT_LIPO = 120
TIMEOUT_OTOOL = 60
TIMEOUT_REA = 900
