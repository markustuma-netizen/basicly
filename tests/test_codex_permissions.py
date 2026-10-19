from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from basicly import codex_permissions

APPROVAL_SETTINGS = '"approval_policy" = "on-request"\n"approvals_reviewer" = "auto_review"\n'

GENERATED_CONFIG = """\
"default_permissions" = "basicly-workspace"

["permissions"."basicly-workspace"]
"extends" = ":workspace"

["permissions"."basicly-workspace"."filesystem".":workspace_roots"]
".env" = "deny"
"**/.env" = "deny"
".git" = "write"

["permissions"."basicly-workspace"."workspace_roots"]
"../app.worktrees" = true

["permissions"."basicly-read-only"]
"extends" = ":read-only"

["permissions"."basicly-read-only"."network"]
"enabled" = false
"""

GENERATED_USER_CONFIG = """\
"default_permissions" = ":workspace"

["permissions"."basicly-workspace"]
"extends" = ":workspace"

["permissions"."basicly-read-only"]
"extends" = ":read-only"
"""


def repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def config(root: Path) -> Path:
    return root / codex_permissions.CONFIG_PATH


def rules(root: Path) -> Path:
    return root / codex_permissions.RULES_PATH


def generated(root: Path, settings: str = GENERATED_CONFIG) -> Path:
    target = config(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(settings, encoding="utf-8")
    managed = rules(root)
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_text("\n".join(codex_permissions.generated_rules()) + "\n", encoding="utf-8")
    return target


def test_upgrade_removes_every_generated_profile_selection_and_rule_file(tmp_path: Path) -> None:
    root = repo(tmp_path / "app")
    generated(root)

    changed = codex_permissions.remove_permissions(root)

    assert set(changed) == {config(root), rules(root)}
    assert not config(root).exists()
    assert not rules(root).exists()
    assert not rules(root).parent.exists()


def test_a_second_upgrade_changes_nothing_and_reports_no_mismatch(tmp_path: Path) -> None:
    root = repo(tmp_path / "app")
    generated(root)
    codex_permissions.remove_permissions(root)

    assert codex_permissions.remove_permissions(root) == []
    assert codex_permissions.mismatches(root) == []


def test_a_repository_that_never_had_the_policy_is_left_alone(tmp_path: Path) -> None:
    root = repo(tmp_path / "app")

    assert codex_permissions.remove_permissions(root) == []
    assert codex_permissions.mismatches(root) == []
    assert not (root / ".codex").exists()


def test_consumer_settings_beside_and_between_the_generated_tables_survive(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path / "app")
    extra = '\n[features]\nnetwork_proxy = true\n\n[permissions.consumer]\nextends = ":read-only"\n'
    generated(root, GENERATED_CONFIG + extra)

    codex_permissions.remove_permissions(root)

    settings = tomllib.loads(config(root).read_text(encoding="utf-8"))
    assert settings["features"] == {"network_proxy": True}
    assert settings["permissions"] == {"consumer": {"extends": ":read-only"}}
    assert codex_permissions.SELECTION_KEY not in settings


def test_generated_approval_settings_are_removed_with_the_profiles(tmp_path: Path) -> None:
    root = repo(tmp_path / "app")
    generated(root, APPROVAL_SETTINGS + GENERATED_CONFIG)

    codex_permissions.remove_permissions(root)

    assert not config(root).exists()


def test_consumer_approval_settings_survive_profile_removal(tmp_path: Path) -> None:
    root = repo(tmp_path / "app")
    generated(root, 'approval_policy="never"\napprovals_reviewer="human"\n' + GENERATED_CONFIG)

    codex_permissions.remove_permissions(root)

    assert tomllib.loads(config(root).read_text(encoding="utf-8")) == {
        "approval_policy": "never",
        "approvals_reviewer": "human",
    }


def test_a_consumer_selection_that_names_no_generated_profile_is_preserved(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path / "app")
    generated(
        root,
        GENERATED_CONFIG.replace('"basicly-workspace"\n', '":danger-full-access"\n', 1),
    )

    codex_permissions.remove_permissions(root)

    settings = tomllib.loads(config(root).read_text(encoding="utf-8"))
    assert settings["default_permissions"] == ":danger-full-access"
    assert "permissions" not in settings


def test_consumer_command_rules_survive_while_the_generated_rules_go(tmp_path: Path) -> None:
    root = repo(tmp_path / "app")
    generated(root)
    custom = 'prefix_rule(pattern = ["consumer-tool"], decision = "prompt")'
    rules(root).write_text(
        custom + "\n" + "\n".join(codex_permissions.generated_rules()) + "\n", encoding="utf-8"
    )

    codex_permissions.remove_permissions(root)

    assert rules(root).read_text(encoding="utf-8") == custom + "\n"
    assert codex_permissions.mismatches(root) == []


def test_an_array_table_after_a_generated_table_is_retained(tmp_path: Path) -> None:
    root = repo(tmp_path / "app")
    generated(
        root,
        '[permissions.basicly-workspace]\nextends = ":workspace"\n'
        '[[consumer_entries]]\nvalue = "retained"\n',
    )

    codex_permissions.remove_permissions(root)

    assert tomllib.loads(config(root).read_text(encoding="utf-8")) == {
        "consumer_entries": [{"value": "retained"}]
    }


def test_a_dotted_generated_key_is_refused_before_any_file_changes(tmp_path: Path) -> None:
    root = repo(tmp_path / "app")
    text = 'permissions.basicly-workspace.extends = ":workspace"\n'
    target = generated(root, text)
    managed = rules(root).read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="unrelated settings"):
        codex_permissions.remove_permissions(root)

    assert target.read_text(encoding="utf-8") == text
    assert rules(root).read_text(encoding="utf-8") == managed
    assert codex_permissions.mismatches(root) == [
        (target, codex_permissions.UNSAFE_LAYOUT),
        (rules(root), codex_permissions.RULES_PRESENT),
    ]


def test_an_unreadable_consumer_config_is_refused_before_the_rules_are_touched(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path / "app")
    target = generated(root, "broken = [\n")

    with pytest.raises(tomllib.TOMLDecodeError):
        codex_permissions.remove_permissions(root)

    assert target.read_text(encoding="utf-8") == "broken = [\n"
    assert rules(root).exists()


def test_mismatches_name_each_file_that_still_holds_the_policy(tmp_path: Path) -> None:
    root = repo(tmp_path / "app")
    target = generated(root)
    user = tmp_path / "codex" / "config.toml"
    user.parent.mkdir()
    user.write_text(GENERATED_USER_CONFIG, encoding="utf-8")

    assert codex_permissions.mismatches(root, user_config=user) == [
        (target, codex_permissions.SETTINGS_PRESENT),
        (user, codex_permissions.SETTINGS_PRESENT),
        (rules(root), codex_permissions.RULES_PRESENT),
    ]

    codex_permissions.remove_permissions(root, user_config=user)

    assert codex_permissions.mismatches(root, user_config=user) == []
