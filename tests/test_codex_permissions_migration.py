from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from basicly import codex_permissions
from tests.test_codex_permissions import (
    GENERATED_USER_CONFIG,
    config,
    generated,
    repo,
)

LEGACY_PROFILE = codex_permissions.PROFILE_NAME + "-" + "a" * 32


@pytest.mark.parametrize("quote", ['"', "'"])
def test_a_legacy_guid_profile_and_its_selection_are_removed(tmp_path: Path, quote: str) -> None:
    root = repo(tmp_path / "app")
    generated(
        root,
        "default_permissions = "
        + quote
        + LEGACY_PROFILE
        + quote
        + "\n[permissions."
        + LEGACY_PROFILE
        + ']\nextends = "basicly-workspace"\n[permissions.'
        + LEGACY_PROFILE
        + ".network]\nenabled = true\n"
        + '[permissions.consumer]\nextends = ":read-only"\n',
    )

    codex_permissions.remove_permissions(root)

    assert tomllib.loads(config(root).read_text(encoding="utf-8")) == {
        "permissions": {"consumer": {"extends": ":read-only"}}
    }


def test_a_legacy_guid_profile_with_consumer_fields_is_removed_too(tmp_path: Path) -> None:
    root = repo(tmp_path / "app")
    generated(
        root,
        "[permissions." + LEGACY_PROFILE + ']\nextends = "consumer"\nnetwork = "kept"\n',
    )

    codex_permissions.remove_permissions(root)

    assert not config(root).exists()


def test_user_configuration_cleanup_is_optional_and_keeps_a_backup(tmp_path: Path) -> None:
    root = repo(tmp_path / "app")
    generated(root)
    user = tmp_path / "codex" / "config.toml"
    user.parent.mkdir()
    before = 'model = "consumer-model"\n' + GENERATED_USER_CONFIG
    user.write_text(before, encoding="utf-8")

    assert user not in codex_permissions.remove_permissions(root)
    assert user.read_text(encoding="utf-8") == before

    assert user in codex_permissions.remove_permissions(root, user_config=user)

    assert tomllib.loads(user.read_text(encoding="utf-8")) == {
        "model": "consumer-model",
        "default_permissions": ":workspace",
    }
    backups = list((user.parent / "permission-backups").glob("*.toml"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == before
    assert codex_permissions.remove_permissions(root, user_config=user) == []


def test_a_user_configuration_holding_only_the_policy_is_removed_after_its_backup(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path / "app")
    user = tmp_path / "codex" / "config.toml"
    user.parent.mkdir()
    user.write_text(
        GENERATED_USER_CONFIG.replace(":workspace", codex_permissions.PROFILE_NAME, 1),
        encoding="utf-8",
    )

    codex_permissions.remove_permissions(root, user_config=user)

    assert not user.exists()
    assert len(list((user.parent / "permission-backups").glob("*.toml"))) == 1


def test_a_symlinked_user_config_is_refused_before_the_project_is_touched(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path / "app")
    target = generated(root)
    before = target.read_text(encoding="utf-8")
    real = tmp_path / "real.toml"
    real.write_text(GENERATED_USER_CONFIG, encoding="utf-8")
    link = tmp_path / "link.toml"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("this platform does not let the test user create a symlink")

    with pytest.raises(ValueError, match="symlinked"):
        codex_permissions.remove_permissions(root, user_config=link)

    assert target.read_text(encoding="utf-8") == before
    assert real.read_text(encoding="utf-8") == GENERATED_USER_CONFIG
