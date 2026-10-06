from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from basicly import codex_settings, permissions


def test_install_is_idempotent_and_preserves_consumer_settings(tmp_path: Path) -> None:
    path = tmp_path / codex_settings.CONFIG_PATH
    path.parent.mkdir()
    original = 'model = "consumer-model"\n\n[custom]\nvalue = "kept"\n'
    path.write_text(original, encoding="utf-8")
    managed = permissions.load_codex_permissions()
    assert codex_settings.sync_permissions(tmp_path, managed)
    first = path.read_text(encoding="utf-8")
    assert not codex_settings.sync_permissions(tmp_path, managed)
    assert path.read_text(encoding="utf-8") == first
    data = tomllib.loads(first)
    assert data["model"] == "consumer-model"
    assert data["custom"] == {"value": "kept"}
    assert data["default_permissions"] == "basicly-consent"
    assert not codex_settings.permission_mismatches(tmp_path, managed)


def test_malformed_consumer_config_is_refused_without_writing(tmp_path: Path) -> None:
    path = tmp_path / codex_settings.CONFIG_PATH
    path.parent.mkdir()
    path.write_text("broken = [", encoding="utf-8")
    with pytest.raises(ValueError, match=r"config\.toml"):
        codex_settings.sync_permissions(tmp_path, permissions.load_codex_permissions())
    assert path.read_text(encoding="utf-8") == "broken = ["


def test_custom_profile_fields_and_rules_survive_repair(tmp_path: Path) -> None:
    managed = permissions.load_codex_permissions()
    codex_settings.sync_permissions(tmp_path, managed)
    path = tmp_path / codex_settings.CONFIG_PATH
    text = path.read_text(encoding="utf-8").replace(
        '"extends" = ":workspace"', 'description = "consumer choice"\n"extends" = ":workspace"'
    )
    path.write_text(text, encoding="utf-8")
    rules = tmp_path / codex_settings.RULES_PATH
    custom = 'prefix_rule(pattern = ["custom"], decision = "prompt")\n'
    rules.write_text(custom, encoding="utf-8")
    assert codex_settings.sync_permissions(tmp_path, managed)
    assert 'description = "consumer choice"' in path.read_text(encoding="utf-8")
    assert rules.read_text(encoding="utf-8").startswith(custom)
    assert not codex_settings.permission_mismatches(tmp_path, managed)


@pytest.mark.parametrize(
    "setting",
    [
        'approval_policy = "never"\n',
        'model = "consumer"\n[custom]\ntext = "keep"\n',
        'model = "consumer"\n[[custom]]\nvalue = 1\n',
    ],
)
def test_strict_approvals_and_foreign_tables_are_preserved(tmp_path: Path, setting: str) -> None:
    path = tmp_path / codex_settings.CONFIG_PATH
    path.parent.mkdir()
    path.write_text(setting, encoding="utf-8")
    before = tomllib.loads(setting)
    codex_settings.sync_permissions(tmp_path, permissions.load_codex_permissions())
    after = tomllib.loads(path.read_text(encoding="utf-8"))
    assert all(after[key] == value for key, value in before.items())


@pytest.mark.parametrize(
    "setting",
    [
        'default_permissions = "other"\n',
        'sandbox_mode = "workspace-write"\n',
        '[permissions.basicly-consent]\nextends = ":read-only"\n',
    ],
)
def test_conflicting_floor_is_refused_before_any_write(tmp_path: Path, setting: str) -> None:
    path = tmp_path / codex_settings.CONFIG_PATH
    path.parent.mkdir()
    path.write_text(setting, encoding="utf-8")
    with pytest.raises(ValueError, match="consent"):
        codex_settings.sync_permissions(tmp_path, permissions.load_codex_permissions())
    assert path.read_text(encoding="utf-8") == setting
    assert not (tmp_path / codex_settings.RULES_PATH).exists()
