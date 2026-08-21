#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shaka Packager Detector
=======================

Detect appropriate Shaka Packager and MPD Generator executables
based on OS and architecture.

Author: GitHub Copilot
Date: October 29, 2025
"""

import os
import platform
import shutil
from pathlib import Path
from logger import get_logger

logger = get_logger("packager_detector")

# Packager/MPD generator binaries live in ./lib at the repo root
LIB_DIR = Path(__file__).resolve().parent.parent / "lib"


def _search_dirs() -> list:
    """Directories to look for packager/mpd_generator binaries in, in priority order"""
    dirs = [LIB_DIR, Path.cwd() / "lib", Path.cwd()]
    seen = set()
    unique_dirs = []
    for d in dirs:
        resolved = d.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique_dirs.append(d)
    return unique_dirs


def suggest_packager_download(system: str, arch: str):
    """Provide helpful download suggestions for missing packager"""
    logger.info("💡 Packager Download Suggestions:")
    
    if system == 'darwin':  # macOS
        logger.info("   🍺 Install via Homebrew: brew install shaka-packager")
        logger.info("   📥 Download from: https://github.com/shaka-project/shaka-packager/releases")
        logger.info(f"   🎯 Look for: packager-osx-{arch} (or packager-osx-x64 as fallback)")
    elif system == 'linux':
        logger.info("   📦 Install via package manager or download binary")
        logger.info("   📥 Download from: https://github.com/shaka-project/shaka-packager/releases")
        logger.info(f"   🎯 Look for: packager-linux-{arch} (or packager-linux-x64 as fallback)")
    elif system == 'windows':
        logger.info("   📥 Download from: https://github.com/shaka-project/shaka-packager/releases")
        logger.info(f"   🎯 Look for: packager-win-{arch}.exe (or packager-win-x64.exe as fallback)")
    
    logger.info(f"   📝 Place the downloaded file in: {LIB_DIR}")
    logger.info("   🔧 Make sure the file is executable (chmod +x on Unix systems)")


def detect_packager_executable() -> str:
    """Detect the appropriate packager executable based on OS and architecture"""
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    logger.info(f"🔍 Detecting system: {system} ({machine})")
    
    # Map platform.machine() output to our naming convention
    arch_mapping = {
        'amd64': 'x64',
        'x86_64': 'x64',
        'arm64': 'arm64',
        'aarch64': 'arm64'
    }
    
    arch = arch_mapping.get(machine, 'x64')  # Default to x64
    
    # List of possible packager names to try in order of preference
    possible_packagers = []
    
    if system == 'windows':
        possible_packagers = [
            f"packager-win-{arch}.exe",
            "packager-win-x64.exe",  # Fallback to x64
            "packager.exe",
            "packager"
        ]
    elif system == 'darwin':  # macOS
        possible_packagers = [
            f"packager-osx-{arch}",
            "packager-osx-arm64" if arch == 'x64' else "packager-osx-x64",  # Cross-arch fallback
            "packager-osx-x64",  # Common fallback
            "packager"
        ]
    elif system == 'linux':
        possible_packagers = [
            f"packager-linux-{arch}",
            "packager-linux-arm64" if arch == 'x64' else "packager-linux-x64",  # Cross-arch fallback
            "packager-linux-x64",  # Common fallback
            "packager"
        ]
    else:
        logger.warning(f"⚠️ Unknown system {system}, trying generic names")
        possible_packagers = [
            "packager-linux-x64",
            "packager"
        ]
    
    # Try to find an existing packager in ./lib (repo root), cwd/lib, or cwd
    for search_dir in _search_dirs():
        for packager_name in possible_packagers:
            packager_path = search_dir / packager_name
            if packager_path.exists():
                logger.info(f"Found packager: {packager_path}")
                # Make sure it's executable on Unix systems
                if system != 'windows':
                    try:
                        os.chmod(packager_path, 0o755)
                        logger.info(f"Set executable permission for {packager_name}")
                    except Exception as e:
                        logger.warning(f"Could not set executable permission: {e}")
                return str(packager_path)

    # Try to find packager in system PATH as fallback
    logger.info("Checking system PATH for packager...")
    for packager_name in ["packager", "shaka-packager"]:
        packager_path = shutil.which(packager_name)
        if packager_path:
            logger.info(f"Found packager in PATH: {packager_path}")
            return packager_path

    # If no packager found, list available files and provide helpful error
    logger.error(f"No suitable packager found for {system} {arch}")
    logger.info("📝 Available packager files:")
    packager_files = list(LIB_DIR.glob("packager*")) if LIB_DIR.exists() else []
    if packager_files:
        for p in packager_files:
            logger.info(f"   - {p.name}")
    else:
        logger.info(f"   (No packager files found in {LIB_DIR})")
    
    # Provide download suggestion
    suggest_packager_download(system, arch)
    
    raise FileNotFoundError(f"Packager executable not found for {system} {arch}")


def detect_mpd_generator_executable() -> str:
    """Detect the appropriate MPD generator executable based on OS and architecture"""
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    # Map platform.machine() output to our naming convention
    arch_mapping = {
        'amd64': 'x64',
        'x86_64': 'x64',
        'arm64': 'arm64',
        'aarch64': 'arm64'
    }
    
    arch = arch_mapping.get(machine, 'x64')  # Default to x64
    
    # List of possible MPD generator names to try
    possible_generators = []
    
    if system == 'windows':
        possible_generators = [
            f"mpd_generator-win-{arch}.exe",
            "mpd_generator-win-x64.exe",
            "mpd_generator.exe",
            "mpd_generator"
        ]
    elif system == 'darwin':  # macOS
        possible_generators = [
            f"mpd_generator-osx-{arch}",
            "mpd_generator-osx-arm64" if arch == 'x64' else "mpd_generator-osx-x64",
            "mpd_generator-osx-x64",
            "mpd_generator"
        ]
    elif system == 'linux':
        possible_generators = [
            f"mpd_generator-linux-{arch}",
            "mpd_generator-linux-arm64" if arch == 'x64' else "mpd_generator-linux-x64",
            "mpd_generator-linux-x64",
            "mpd_generator"
        ]
    else:
        logger.warning(f"⚠️ Unknown system {system}, trying generic names")
        possible_generators = [
            "mpd_generator-linux-x64",
            "mpd_generator"
        ]
    
    # Try to find an existing MPD generator in ./lib (repo root), cwd/lib, or cwd
    for search_dir in _search_dirs():
        for generator_name in possible_generators:
            mpd_path = search_dir / generator_name
            if mpd_path.exists():
                logger.info(f"Found MPD generator: {mpd_path}")
                # Make sure it's executable on Unix systems
                if system != 'windows':
                    try:
                        os.chmod(mpd_path, 0o755)
                        logger.info(f"Set executable permission for {generator_name}")
                    except Exception as e:
                        logger.warning(f"Could not set executable permission: {e}")
                return str(mpd_path)

    # Try to find MPD generator in system PATH as fallback
    logger.info("Checking system PATH for MPD generator...")
    for generator_name in ["mpd_generator", "shaka-packager"]:
        generator_path = shutil.which(generator_name)
        if generator_path:
            logger.info(f"Found MPD generator in PATH: {generator_path}")
            return generator_path

    # MPD generator is optional, so just warn if not found
    logger.warning(f"MPD generator not found for {system} {arch}")
    logger.info("Available MPD generator files:")
    mpd_files = list(LIB_DIR.glob("mpd_generator*")) if LIB_DIR.exists() else []
    if mpd_files:
        for p in mpd_files:
            logger.info(f"   - {p.name}")
    else:
        logger.info(f"   (No MPD generator files found in {LIB_DIR})")

    logger.info("💡 MPD generator is optional. You can:")
    logger.info("   📥 Download from: https://github.com/shaka-project/shaka-packager/releases")
    logger.info(f"   🎯 Look for: mpd_generator-{system}-{arch}")

    return None  # MPD generator is optional
