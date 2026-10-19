from __future__ import annotations

import os
import re
import tempfile
import tomllib
import uuid
from pathlib import Path
from typing import Any

from .projection import atomic_write_text

CONFIG_PATH = Path(".codex/config.toml")
RULES_PATH = Path(".codex/rules/consent-floor.rules")
GENERATED_RULES_FILE = Path(__file__).with_name("codex_generated_rules.rules")
PROFILE_NAME = "basicly-workspace"
READ_ONLY_PROFILE_NAME = "basicly-read-only"
GENERATED_PROFILE = re.compile(
    re.escape(PROFILE_NAME) + r"(?:-[0-9a-f]{32})?|" + re.escape(READ_ONLY_PROFILE_NAME)
)
SELECTION_KEY = "default_permissions"
SETTINGS_PRESENT = "generated Codex permission policy still present"
RULES_PRESENT = "generated Codex command rules still present"
UNSAFE_LAYOUT = (
    "Removing the generated Codex policy would change unrelated settings; "
    "reconcile this file by hand"
)


def generated_rules() -> tuple[str, ...]:
    text = GENERATED_RULES_FILE.read_text(encoding="utf-8")
    return tuple(line for line in text.splitlines() if line.strip())


def is_generated_profile(name: str) -> bool:
    return GENERATED_PROFILE.fullmatch(name) is not None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


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


def _string_key(line: str) -> str | None:
    try:
        parsed = tomllib.loads(line)
    except tomllib.TOMLDecodeError:
        return None
    if len(parsed) != 1:
        return None
    key, value = next(iter(parsed.items()))
    return key if isinstance(value, str) else None


def _generated_table(header: tuple[str, ...]) -> bool:
    return len(header) >= 2 and header[0] == "permissions" and is_generated_profile(header[1])


def _generated_keys(parsed: dict[str, Any]) -> set[str]:
    dropped: set[str] = set()
    selected = parsed.get(SELECTION_KEY)
    if isinstance(selected, str) and is_generated_profile(selected):
        dropped.add(SELECTION_KEY)
    if dropped or any(is_generated_profile(name) for name in parsed.get("permissions", {})):
        for key, value in (
            ("approval_policy", "on-request"),
            ("approvals_reviewer", "auto_review"),
        ):
            if parsed.get(key) == value:
                dropped.add(key)
    return dropped


def _without_generated(parsed: dict[str, Any], dropped: set[str]) -> dict[str, Any]:
    kept = {key: value for key, value in parsed.items() if key not in dropped}
    profiles = kept.get("permissions")
    if isinstance(profiles, dict):
        kept["permissions"] = {
            name: profile for name, profile in profiles.items() if not is_generated_profile(name)
        }
    if kept.get("permissions") == {}:
        kept.pop("permissions")
    return kept


def strip_settings(text: str) -> str:
    parsed = tomllib.loads(text)
    for name, profile in parsed.get("permissions", {}).items():
        parent = profile.get("extends") if isinstance(profile, dict) else None
        if (
            not is_generated_profile(name)
            and isinstance(parent, str)
            and is_generated_profile(parent)
        ):
            raise ValueError(
                f"Codex profile {name} extends retired profile {parent}; "
                "select a built-in parent before running permissions-build"
            )
    dropped = _generated_keys(parsed)
    kept_lines: list[str] = []
    in_tables = False
    keep = True
    for line in text.splitlines(keepends=True):
        header = _header(line)
        if header is not None:
            in_tables = True
            keep = not _generated_table(header)
        elif not in_tables and _string_key(line) in dropped:
            continue
        if keep:
            kept_lines.append(line)
    rendered = "".join(kept_lines)
    actual = tomllib.loads(rendered)
    if actual.get("permissions") == {}:
        actual.pop("permissions")
    if actual != _without_generated(parsed, dropped):
        raise ValueError(UNSAFE_LAYOUT)
    return rendered if rendered.strip() else ""


def strip_rules(text: str) -> str:
    managed = generated_rules()
    kept = "".join(
        line for line in text.splitlines(keepends=True) if line.rstrip("\r\n") not in managed
    )
    return kept if kept.strip() else ""


def removals(repo_root: Path, *, user_config: Path | None = None) -> list[tuple[Path, str, str]]:
    planned: list[tuple[Path, str, str]] = []
    project = repo_root / CONFIG_PATH
    project_text = _read(project)
    planned.append((project, project_text, strip_settings(project_text)))
    if user_config is not None:
        if user_config.is_symlink():
            raise ValueError("Refusing to replace a symlinked Codex user config")
        user_text = _read(user_config)
        planned.append((user_config, user_text, strip_settings(user_text)))
    rules = repo_root / RULES_PATH
    rules_text = _read(rules)
    planned.append((rules, rules_text, strip_rules(rules_text)))
    return [step for step in planned if step[1] != step[2]]


def _backup_user_config(path: Path, before: str) -> None:
    backups = path.parent / "permission-backups"
    backups.mkdir(mode=0o700, exist_ok=True)
    backup = backups / ("basicly-workspace-" + uuid.uuid4().hex + ".toml")
    descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(before)


def _rewrite_user_config(path: Path, before: str, after: str) -> None:
    if path.is_symlink():
        raise ValueError("Refusing to replace a symlinked Codex user config")
    if _read(path) != before:
        raise ValueError("Codex user config changed during removal; run permissions-build again")
    _backup_user_config(path, before)
    if not after:
        path.unlink()
        return
    descriptor, name = tempfile.mkstemp(prefix=".basicly-workspace-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(after)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _discard(path: Path) -> None:
    path.unlink(missing_ok=True)
    parent = path.parent
    if parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()


def remove_permissions(repo_root: Path, *, user_config: Path | None = None) -> list[Path]:
    changed: list[Path] = []
    for path, before, after in removals(repo_root, user_config=user_config):
        if path == user_config:
            _rewrite_user_config(path, before, after)
        elif after:
            atomic_write_text(path, after)
        else:
            _discard(path)
        changed.append(path)
    return changed


def mismatches(repo_root: Path, *, user_config: Path | None = None) -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    targets = [repo_root / CONFIG_PATH]
    if user_config is not None:
        targets.append(user_config)
    for path in targets:
        text = _read(path)
        try:
            if strip_settings(text) != text:
                found.append((path, SETTINGS_PRESENT))
        except ValueError as exc:
            found.append((path, str(exc)))
    rules = repo_root / RULES_PATH
    rules_text = _read(rules)
    if strip_rules(rules_text) != rules_text:
        found.append((rules, RULES_PRESENT))
    return found
