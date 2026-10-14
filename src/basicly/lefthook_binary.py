from __future__ import annotations

import gzip
import platform
from pathlib import Path

from . import lefthook_artifacts
from .projection import SyncResult, sync_file

VERSION = lefthook_artifacts.VERSION


def asset_name(system: str, machine: str) -> str:
    systems = {"Linux": "Linux", "Darwin": "MacOS", "Windows": "Windows"}
    machines = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}
    if system not in systems or machine.lower() not in machines:
        raise ValueError(f"Lefthook {VERSION} does not support {system}/{machine}")
    return f"lefthook_{VERSION}_{systems[system]}_{machines[machine.lower()]}.gz"


def binary_path(repo_root: Path, system: str | None = None) -> Path:
    suffix = ".exe" if (system or platform.system()) == "Windows" else ""
    return repo_root / ".basicly/bin" / f"lefthook{suffix}"


def binary_bytes(system: str | None = None, machine: str | None = None) -> bytes:
    name = asset_name(system or platform.system(), machine or platform.machine())
    compressed = lefthook_artifacts.verified_bytes(lefthook_artifacts.artifact_directory(), name)
    return gzip.decompress(compressed)


def sync_binary(repo_root: Path) -> SyncResult:
    result = SyncResult()
    path = binary_path(repo_root)
    sync_file(path, binary_bytes(), result)
    path.chmod(path.stat().st_mode | 0o111)
    source = lefthook_artifacts.METADATA / "LICENSE"
    sync_file(path.parent / "LICENSE.lefthook", source.read_bytes(), result)
    ignore = path.parent / ".gitignore"
    if not ignore.exists():
        sync_file(ignore, b"*\n", result)
    return result


def binary_mismatches(repo_root: Path) -> list[tuple[Path, str]]:
    path = binary_path(repo_root)
    found = []
    if not path.is_file():
        found.append((path, "missing bundled Lefthook binary"))
    elif path.read_bytes() != binary_bytes():
        found.append((path, "bundled Lefthook binary differs"))
    elif platform.system() != "Windows" and not path.stat().st_mode & 0o111:
        found.append((path, "bundled Lefthook binary is not executable"))
    license_path = path.parent / "LICENSE.lefthook"
    source = lefthook_artifacts.METADATA / "LICENSE"
    if not license_path.is_file() or license_path.read_bytes() != source.read_bytes():
        found.append((license_path, "bundled Lefthook license missing or differs"))
    return found


def remove_binary(repo_root: Path) -> int:
    directory = repo_root / ".basicly/bin"
    removed = 0
    for name in ("lefthook", "lefthook.exe", "LICENSE.lefthook"):
        path = directory / name
        if path.is_file():
            path.unlink()
            removed += 1
    ignore = directory / ".gitignore"
    if ignore.is_file() and ignore.read_bytes() == b"*\n":
        ignore.unlink()
        removed += 1
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()
    return removed
