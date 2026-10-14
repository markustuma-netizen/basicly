from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import tomllib
import uuid
from pathlib import Path, PurePath
from typing import Any

import yaml

from .catalog import bundled_catalog_root
from .checkout import main_checkout, worktrees_root
from .projection import atomic_write_text

CONFIG_PATH = Path(".codex/config.toml")
PROFILE_NAME = "basicly-workspace"
PROJECT_PROFILE_PREFIX = PROFILE_NAME + "-"


def managed_settings() -> dict[str, Any]:
    source = bundled_catalog_root() / "permissions" / "codex-profile.yaml"
    settings = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        raise ValueError(f"{source}: Codex settings must be a mapping")
    return settings


def repository_profile(text: str) -> str | None:
    settings = tomllib.loads(text)
    profiles = settings.get("permissions", {})
    if not isinstance(profiles, dict):
        raise ValueError("Codex permissions must be a table")
    found = [
        name
        for name, profile in profiles.items()
        if name.startswith(PROJECT_PROFILE_PREFIX)
        and isinstance(profile, dict)
        and profile.get("extends") == PROFILE_NAME
    ]
    if len(found) > 1:
        raise ValueError("Multiple basicly-workspace repository profiles; keep exactly one")
    if (
        found
        and re.fullmatch(re.escape(PROJECT_PROFILE_PREFIX) + r"[0-9a-f]{32}", found[0]) is None
    ):
        raise ValueError(
            "Invalid basicly-workspace repository identity; restore the shared profile"
        )
    selected = settings.get("default_permissions", "")
    if (
        isinstance(selected, str)
        and selected.startswith(PROJECT_PROFILE_PREFIX)
        and selected not in found
    ):
        raise ValueError("Selected basicly-workspace repository profile is missing its definition")
    return found[0] if found else None


def sibling_settings(root: PurePath, profile: str) -> dict[str, Any]:
    if not root.is_absolute() or not root.name.endswith(".worktrees"):
        raise ValueError("Codex sibling root must be an absolute .worktrees directory")
    return {
        "permissions": {
            profile: {"extends": ":workspace", "workspace_roots": {root.as_posix(): True}}
        }
    }


def user_settings(text: str, root: PurePath, profile: str) -> dict[str, Any]:
    parsed = tomllib.loads(text)
    if "sandbox_mode" in parsed or "sandbox_workspace_write" in parsed:
        raise ValueError(
            "Legacy Codex sandbox settings conflict with profile registration; reconcile them first"
        )
    settings = sibling_settings(root, profile)
    if "default_permissions" not in parsed:
        settings = {"default_permissions": ":workspace", **settings}
    return settings


def _tables(settings: dict[str, Any], path: tuple[str, ...] = ()) -> dict[tuple[str, ...], dict]:
    tables: dict[tuple[str, ...], dict] = {}
    for key, value in settings.items():
        if isinstance(value, dict):
            tables.update(_tables(value, (*path, key)))
        else:
            tables.setdefault(path, {})[key] = value
    return tables


def _table(settings: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    current = settings
    for key in path:
        if key not in current:
            return {}
        current = current[key]
        if not isinstance(current, dict):
            raise ValueError(f"Codex configuration: {'.'.join(path)} must be a table")
    return current


def _header(line: str) -> tuple[str, ...] | None:
    if not line.lstrip().startswith("["):
        return None
    try:
        parsed = tomllib.loads(line)
    except tomllib.TOMLDecodeError:
        return None
    path: list[str] = []
    while isinstance(parsed, dict) and len(parsed) == 1:
        key, value = next(iter(parsed.items()))
        path.append(key)
        parsed = value
    return tuple(path) if parsed == {} else None


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    raise ValueError(f"Unsupported managed Codex value: {type(value).__name__}")


def merge_settings(text: str, additions: dict[str, Any]) -> str:
    parsed = tomllib.loads(text)
    expected = copy.deepcopy(parsed)
    rendered = text
    for path, values in _tables(additions).items():
        existing = _table(parsed, path)
        missing: dict[str, Any] = {}
        for key, value in values.items():
            if key in existing and existing[key] != value:
                raise ValueError(
                    f"Codex setting {'.'.join((*path, key))} conflicts with basicly; "
                    "review the existing setting before running permissions-build"
                )
            if key not in existing:
                missing[key] = value
        if not missing:
            continue
        target = expected
        for key in path:
            target = target.setdefault(key, {})
        target.update(missing)
        entries = "".join(
            f"{json.dumps(key)} = {_literal(value)}\n" for key, value in missing.items()
        )
        lines = rendered.splitlines(keepends=True)
        headers = [(index, _header(line)) for index, line in enumerate(lines)]
        position = next((index for index, header in headers if header == path), None)
        if not path:
            end = next((index for index, header in headers if header is not None), len(lines))
            lines.insert(end, entries + "\n")
            rendered = "".join(lines)
        elif position is not None:
            end = next(
                (index for index, header in headers if index > position and header is not None),
                len(lines),
            )
            lines.insert(end, "\n" + entries)
            rendered = "".join(lines)
        else:
            heading = ".".join(json.dumps(key) for key in path)
            rendered = rendered.rstrip() + f"\n\n[{heading}]\n" + entries
    try:
        actual = tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            "Cannot extend this Codex TOML layout safely; use explicit tables"
        ) from exc
    if actual != expected:
        raise ValueError("Codex settings merge would change unrelated settings; refusing")
    return rendered


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def project_settings(text: str, profile: str) -> dict[str, Any]:
    parsed = tomllib.loads(text)
    if "sandbox_mode" in parsed or "sandbox_workspace_write" in parsed:
        raise ValueError(
            "Legacy Codex sandbox settings conflict with project profiles; reconcile them first"
        )
    settings = managed_settings()
    settings["default_permissions"] = profile
    settings["permissions"][profile] = {"extends": PROFILE_NAME}
    for key in ("default_permissions", "approval_policy", "approvals_reviewer"):
        if key in parsed:
            settings.pop(key, None)
    return settings


def _profile(repo_root: Path, text: str) -> str:
    profile = repository_profile(text)
    if profile is not None:
        return profile
    if (repo_root / ".git").is_file():
        main = main_checkout(repo_root)
        profile = repository_profile(_read(main / CONFIG_PATH))
    return profile or PROJECT_PROFILE_PREFIX + uuid.uuid4().hex


def _write_user_config(path: Path, before: str, after: str) -> None:
    if path.is_symlink():
        raise ValueError("Refusing to replace a symlinked Codex user config")
    if _read(path) != before:
        raise ValueError(
            "Codex user config changed during registration; run permissions-build again"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backups = path.parent / "permission-backups"
        backups.mkdir(mode=0o700, exist_ok=True)
        backup = backups / ("basicly-workspace-" + uuid.uuid4().hex + ".toml")
        descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(before)
    descriptor, name = tempfile.mkstemp(prefix=".basicly-workspace-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(after)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def sync_permissions(repo_root: Path, *, user_config: Path | None = None) -> list[Path]:
    project = repo_root / CONFIG_PATH
    text = _read(project)
    profile = _profile(repo_root, text)
    planned = [(project, text, merge_settings(text, project_settings(text, profile)))]
    if user_config is not None:
        if user_config.is_symlink():
            raise ValueError("Refusing to replace a symlinked Codex user config")
        user_text = _read(user_config)
        desired = user_settings(user_text, worktrees_root(repo_root), profile)
        planned.append((user_config, user_text, merge_settings(user_text, desired)))
    if user_config is not None:
        worktrees_root(repo_root).mkdir(parents=True, exist_ok=True)
    changed: list[Path] = []
    for path, before, after in planned:
        if before != after:
            if path == user_config:
                _write_user_config(path, before, after)
            else:
                atomic_write_text(path, after)
            changed.append(path)
    return changed


def mismatches(repo_root: Path, *, user_config: Path | None = None) -> list[tuple[Path, str]]:
    project = repo_root / CONFIG_PATH
    text = _read(project)
    profile = _profile(repo_root, text)
    wanted = [(project, text, project_settings(text, profile))]
    if user_config is not None:
        user_text = _read(user_config)
        wanted.append((
            user_config,
            user_text,
            user_settings(user_text, worktrees_root(repo_root), profile),
        ))
    found: list[tuple[Path, str]] = []
    for path, current, desired in wanted:
        try:
            if merge_settings(current, desired) != current:
                found.append((path, "managed Codex permissions missing"))
        except ValueError as exc:
            found.append((path, str(exc)))
    return found
