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
from .checkout import git, git_common_dir, worktrees_root
from .projection import atomic_write_text

CONFIG_PATH = Path(".codex/config.toml")
PROFILE_NAME = "basicly-workspace"
RULES_PATH = Path(".codex/rules/consent-floor.rules")
PROJECT_PROFILE_PREFIX = PROFILE_NAME + "-"


def managed_settings() -> dict[str, Any]:
    source = bundled_catalog_root() / "permissions" / "codex-profile.yaml"
    settings = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        raise ValueError(f"{source}: Codex settings must be a mapping")
    return settings


def sibling_settings(root: PurePath, profile: str) -> dict[str, Any]:
    if not root.is_absolute() or not root.name.endswith(".worktrees"):
        raise ValueError("Codex sibling root must be an absolute .worktrees directory")
    return {
        "permissions": {
            profile: {"extends": ":workspace", "workspace_roots": {root.as_posix(): True}}
        }
    }


def user_settings(text: str) -> dict[str, Any]:
    parsed = tomllib.loads(text)
    if "sandbox_mode" in parsed or "sandbox_workspace_write" in parsed:
        raise ValueError(
            "Legacy Codex sandbox settings conflict with profile registration; reconcile them first"
        )
    settings = {"permissions": managed_settings()["permissions"]}
    existing = (
        parsed
        .get("permissions", {})
        .get(PROFILE_NAME, {})
        .get("filesystem", {})
        .get(":workspace_roots", {})
    )
    if ".git" in existing:
        settings["permissions"][PROFILE_NAME]["filesystem"][":workspace_roots"].pop(".git", None)
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
    return tuple(path) if parsed in ({}, [{}]) else None


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
    existing = (
        parsed
        .get("permissions", {})
        .get(PROFILE_NAME, {})
        .get("filesystem", {})
        .get(":workspace_roots", {})
    )
    if ".git" in existing:
        settings["permissions"][PROFILE_NAME]["filesystem"][":workspace_roots"].pop(".git", None)
    settings["default_permissions"] = profile
    for key in ("default_permissions", "approval_policy", "approvals_reviewer"):
        if key in parsed:
            settings.pop(key, None)
    return settings


def _remove_table(text: str, path: tuple[str, ...]) -> str:
    lines: list[str] = []
    keep = True
    for line in text.splitlines(keepends=True):
        header = _header(line)
        if header is not None:
            keep = header[: len(path)] != path
        if keep:
            lines.append(line)
    return "".join(lines)


def _migrate_profiles(text: str, root: PurePath | None = None) -> str:
    parsed = tomllib.loads(text)
    profiles = parsed.get("permissions", {})
    for name, profile in profiles.items():
        if re.fullmatch(re.escape(PROJECT_PROFILE_PREFIX) + r"[0-9a-f]{32}", name) is None:
            continue
        if root is not None:
            if profile != sibling_settings(root, name)["permissions"][name]:
                raise ValueError(
                    f"Codex profile {name} has consumer settings or another sibling root"
                )
            additions: dict[str, Any] = {}
        else:
            if profile.get("extends") != PROFILE_NAME or set(profile) - {"extends", "network"}:
                raise ValueError(
                    f"Codex profile {name} has consumer settings; review before migration"
                )
            additions = {
                "permissions": {PROFILE_NAME: {k: v for k, v in profile.items() if k != "extends"}}
            }
        text = _remove_table(text, ("permissions", name))
        if parsed.get("default_permissions") == name:
            text = re.sub(
                r"(?m)^(\s*(?:[\"']?default_permissions[\"']?)\s*=\s*)([\"'])"
                + re.escape(name)
                + r"\2",
                r'\1"' + PROFILE_NAME + '"',
                text,
            )
        text = merge_settings(text, additions)
    return text


def _project_additions(repo_root: Path, text: str, register: bool) -> dict[str, Any]:
    settings = project_settings(text, PROFILE_NAME)
    if register:
        relative = Path(os.path.relpath(worktrees_root(repo_root), repo_root)).as_posix()
        roots = {relative: True}
        git_access = (
            tomllib
            .loads(text)
            .get("permissions", {})
            .get(PROFILE_NAME, {})
            .get("filesystem", {})
            .get(":workspace_roots", {})
            .get(".git", "write")
        )
        if git_access == "write" and (repo_root / ".git").is_file():
            gitdir = Path(git(["rev-parse", "--git-dir"], cwd=repo_root).stdout.strip())
            if not gitdir.is_absolute():
                gitdir = repo_root / gitdir
            for root in (git_common_dir(repo_root), gitdir.resolve()):
                roots[Path(os.path.relpath(root, repo_root)).as_posix()] = True
        settings["permissions"][PROFILE_NAME]["workspace_roots"] = roots
    return settings


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


def _project_rules(repo_root: Path) -> tuple[Path, str, str]:
    source = bundled_catalog_root() / "permissions" / "codex-rules.yaml"
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("rules"), str)
        or not data["rules"].strip()
    ):
        raise ValueError(f"{source}: Codex rules must be a non-empty string")
    path = repo_root / RULES_PATH
    before = _read(path)
    missing = [
        line
        for line in data["rules"].splitlines()
        if line.strip() and line not in before.splitlines()
    ]
    after = (
        before.rstrip() + ("\n" if before else "") + "\n".join(missing) + "\n"
        if missing
        else before
    )
    return path, before, after


def sync_permissions(repo_root: Path, *, user_config: Path | None = None) -> list[Path]:
    project = repo_root / CONFIG_PATH
    text = _read(project)
    migrated = _migrate_profiles(text)
    settings = _project_additions(repo_root, migrated, user_config is not None)
    planned = [(project, text, merge_settings(migrated, settings))]
    if user_config is not None:
        if user_config.is_symlink():
            raise ValueError("Refusing to replace a symlinked Codex user config")
        user_text = _read(user_config)
        migrated_user = _migrate_profiles(user_text, worktrees_root(repo_root))
        desired = user_settings(migrated_user)
        planned.append((user_config, user_text, merge_settings(migrated_user, desired)))
    planned.append(_project_rules(repo_root))
    if user_config is not None:
        worktrees_root(repo_root).mkdir(parents=True, exist_ok=True)
    changed: list[Path] = []
    for path, before, after in planned:
        if before != after:
            if path == user_config or (path == project and before != migrated):
                _write_user_config(path, before, after)
            else:
                atomic_write_text(path, after)
            changed.append(path)
    return changed


def mismatches(repo_root: Path, *, user_config: Path | None = None) -> list[tuple[Path, str]]:
    project = repo_root / CONFIG_PATH
    text = _read(project)
    migrated = _migrate_profiles(text)
    wanted = [(project, text, _project_additions(repo_root, migrated, user_config is not None))]
    if user_config is not None:
        user_text = _read(user_config)
        wanted.append((
            user_config,
            user_text,
            user_settings(user_text),
        ))
    found: list[tuple[Path, str]] = []
    for path, current, desired in wanted:
        try:
            migrated = _migrate_profiles(
                current, worktrees_root(repo_root) if path == user_config else None
            )
            if merge_settings(migrated, desired) != current:
                found.append((path, "managed Codex permissions missing"))
        except ValueError as exc:
            found.append((path, str(exc)))
    rules, before, after = _project_rules(repo_root)
    if before != after:
        found.append((rules, "managed Codex command rules missing"))
    return found
