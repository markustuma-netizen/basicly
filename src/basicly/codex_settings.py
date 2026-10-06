from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .projection import atomic_write_text

if TYPE_CHECKING:
    from .permissions import CodexPermissions

CONFIG_PATH = Path(".codex/config.toml")
RULES_PATH = Path(".codex/rules/consent-floor.rules")
_HEADER = re.compile(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$")


def _read_config(path: Path) -> tuple[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        return text, tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path}: invalid Codex configuration: {error}") from error


def _leaves(
    data: dict[str, Any], parent: tuple[str, ...] = ()
) -> list[tuple[tuple[str, ...], Any]]:
    result = []
    for key, value in data.items():
        path = (*parent, key)
        if isinstance(value, dict):
            result.extend(_leaves(value, path))
        else:
            result.append((path, value))
    return result


def _lookup(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = data
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _header_path(line: str) -> tuple[str, ...] | None:
    match = _HEADER.fullmatch(line)
    if match is None:
        return None
    data = tomllib.loads(line + "\n__basicly_probe__ = true\n")
    path = []
    while "__basicly_probe__" not in data:
        key = next(iter(data))
        path.append(key)
        data = data[key]
    return tuple(path)


def _insert(text: str, path: tuple[str, ...], value: Any) -> str:
    parent, key = path[:-1], path[-1]
    assignment = json.dumps(key) + " = " + json.dumps(value) + "\n"
    lines = text.splitlines(keepends=True)
    section: tuple[str, ...] | None = ()
    position = 0 if not parent else None
    for index, line in enumerate(lines):
        header = _header_path(line.rstrip("\n"))
        if header is not None:
            if section == parent:
                position = index
                break
            section = header
            if header == parent:
                position = index + 1
    if position is None:
        header = ".".join(json.dumps(part) for part in parent)
        return text.rstrip() + "\n\n[" + header + "]\n" + assignment
    lines.insert(position, assignment)
    return "".join(lines)


def _project_config(text: str, data: dict[str, Any], managed: CodexPermissions) -> str:
    wanted = tomllib.loads(managed.config)
    original = _leaves(data)
    if "sandbox_mode" in data:
        raise ValueError(
            ".codex/config.toml: sandbox_mode overrides the consent profile; "
            "remove it and use default_permissions"
        )
    for path, value in _leaves(wanted):
        current = _lookup(data, path)
        if current == value or (path == ("approval_policy",) and current == "never"):
            continue
        if current is not None:
            raise ValueError(
                f".codex/config.toml: {'.'.join(path)} conflicts with the consent floor; "
                "review this setting before running permissions-build"
            )
        text = _insert(text, path, value)
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            raise ValueError(
                ".codex/config.toml: consent projection conflicts with a dotted or inline table; "
                "expand that table before running permissions-build"
            ) from error
    if any(_lookup(data, path) != value for path, value in original):
        raise ValueError(
            ".codex/config.toml: projection would alter consumer settings; "
            "expand conflicting tables before running permissions-build"
        )
    if any(
        _lookup(data, path) != value
        for path, value in _leaves(wanted)
        if path != ("approval_policy",)
    ):
        raise ValueError(
            ".codex/config.toml: unsupported table layout; "
            "expand conflicting tables before running permissions-build"
        )
    return text


def permission_mismatches(repo_root: Path, managed: CodexPermissions) -> list[str]:
    text, data = _read_config(repo_root / CONFIG_PATH)
    projected = _project_config(text, data, managed)
    reasons = ["managed Codex consent configuration missing"] if projected != text else []
    path = repo_root / RULES_PATH
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    missing = [
        line
        for line in managed.rules.splitlines()
        if line.strip() and line not in existing.splitlines()
    ]
    if missing:
        reasons.append("managed Codex command rules missing")
    return reasons


def sync_permissions(repo_root: Path, managed: CodexPermissions) -> bool:
    path = repo_root / CONFIG_PATH
    text, data = _read_config(path)
    projected = _project_config(text, data, managed)
    rules_path = repo_root / RULES_PATH
    existing = rules_path.read_text(encoding="utf-8") if rules_path.exists() else ""
    missing = [
        line
        for line in managed.rules.splitlines()
        if line.strip() and line not in existing.splitlines()
    ]
    changed = projected != text or bool(missing)
    if projected != text:
        atomic_write_text(path, projected)
    if missing:
        atomic_write_text(
            rules_path, existing.rstrip() + ("\n" if existing else "") + "\n".join(missing) + "\n"
        )
    return changed
