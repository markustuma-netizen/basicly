from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from basicly import codex_permissions
from tests import test_codex_permissions as permissions_tests


@pytest.mark.parametrize("quote", ['"', "'"])
def test_guid_migration_preserves_network_policy_and_removes_only_owned_tables(
    tmp_path: Path,
    quote: str,
) -> None:
    repo = permissions_tests._repo(tmp_path / "app")
    target = repo / codex_permissions.CONFIG_PATH
    target.parent.mkdir()
    legacy = "basicly-workspace-" + "a" * 32
    target.write_text(
        "default_permissions=" + quote + legacy + quote + "\n"
        '[permissions.basicly-workspace]\nextends=":workspace"\n'
        "[permissions." + legacy + ']\nextends="basicly-workspace"\n'
        "[permissions." + legacy + ".network]\nenabled=true\nallow_local_binding=true\n"
        "[permissions." + legacy + '.network.domains]\nlocalhost="allow"\n'
        '[permissions.consumer]\nextends=":read-only"\n'
    )
    user = tmp_path / "user.toml"
    user.write_text(
        'default_permissions=":workspace"\n'
        "[permissions." + legacy + ']\nextends=":workspace"\n'
        "[permissions."
        + legacy
        + ".workspace_roots]\n"
        + '"'
        + (tmp_path / "app.worktrees").as_posix()
        + '"=true\n'
    )
    codex_permissions.sync_permissions(repo, user_config=user)
    settings = permissions_tests._config(repo)
    assert settings["default_permissions"] == "basicly-workspace"
    assert set(settings["permissions"]) == {"basicly-workspace", "basicly-read-only", "consumer"}
    assert settings["permissions"]["basicly-workspace"]["network"] == {
        "enabled": True,
        "allow_local_binding": True,
        "domains": {"localhost": "allow"},
    }
    local_profiles = tomllib.loads(user.read_text())["permissions"]
    assert set(local_profiles) == {"basicly-workspace", "basicly-read-only"}
    readonly = settings["permissions"]["basicly-read-only"]
    assert readonly == {
        "extends": ":read-only",
        "filesystem": {
            "glob_scan_max_depth": 16,
            ":workspace_roots": {
                ".env": "deny",
                ".env.*": "deny",
                "**/.env": "deny",
                "**/.env.*": "deny",
            },
        },
        "network": {"enabled": False},
    }
    assert local_profiles["basicly-read-only"] == readonly
    assert settings["permissions"]["consumer"] == {"extends": ":read-only"}
    assert codex_permissions.sync_permissions(repo, user_config=user) == []


def test_guid_with_consumer_fields_is_refused_without_removing_it(tmp_path: Path) -> None:
    repo = permissions_tests._repo(tmp_path / "app")
    target = repo / codex_permissions.CONFIG_PATH
    target.parent.mkdir()
    legacy = "basicly-workspace-" + "a" * 32
    text = "[permissions." + legacy + ']\nextends="consumer"\n'
    target.write_text(text)
    with pytest.raises(ValueError, match="consumer"):
        codex_permissions.sync_permissions(repo)
    assert target.read_text() == text


def test_guid_migration_preserves_following_array_tables(tmp_path: Path) -> None:
    repo = permissions_tests._repo(tmp_path / "app")
    target = repo / codex_permissions.CONFIG_PATH
    target.parent.mkdir()
    legacy = "basicly-workspace-" + "a" * 32
    target.write_text(
        "[permissions."
        + legacy
        + ']\nextends="basicly-workspace"\n[[consumer_entries]]\nvalue="retained"\n'
    )
    codex_permissions.sync_permissions(repo)
    assert permissions_tests._config(repo)["consumer_entries"] == [{"value": "retained"}]
