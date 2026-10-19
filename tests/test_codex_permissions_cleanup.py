from pathlib import Path

import pytest

from basicly import codex_permissions


def test_cleanup_preserves_explicit_builtin_selection_and_custom_rule_bytes(tmp_path: Path) -> None:
    config = tmp_path / codex_permissions.CONFIG_PATH
    config.parent.mkdir()
    settings = 'approval_policy="on-request"\ndefault_permissions=":danger-full-access"\n'
    config.write_text(settings)
    rules = tmp_path / codex_permissions.RULES_PATH
    rules.parent.mkdir()
    custom = '\n\nprefix_rule(pattern = ["consumer"], decision = "prompt")\n\n'
    rules.write_text(custom)

    assert codex_permissions.remove_permissions(tmp_path) == []
    assert config.read_text() == settings
    assert rules.read_text() == custom
    assert codex_permissions.mismatches(tmp_path) == []


@pytest.mark.parametrize(
    "settings",
    [
        'permissions={basicly-workspace={extends=":workspace"},consumer={extends=":read-only"}}\n',
        'permissions.basicly-workspace.extends=":workspace"\n',
    ],
)
def test_cleanup_refuses_unhandled_layout_before_reporting_success_or_writing(
    tmp_path: Path, settings: str
) -> None:
    config = tmp_path / codex_permissions.CONFIG_PATH
    config.parent.mkdir()
    config.write_text(settings)
    rules = tmp_path / codex_permissions.RULES_PATH
    rules.parent.mkdir()
    before_rules = "\n".join(codex_permissions.generated_rules()) + "\n"
    rules.write_text(before_rules)

    with pytest.raises(ValueError, match="unrelated settings"):
        codex_permissions.remove_permissions(tmp_path)

    assert config.read_text() == settings
    assert rules.read_text() == before_rules
    assert codex_permissions.mismatches(tmp_path)


def test_cleanup_refuses_to_leave_consumer_profile_with_missing_parent(tmp_path: Path) -> None:
    config = tmp_path / codex_permissions.CONFIG_PATH
    config.parent.mkdir()
    settings = (
        '[permissions.basicly-workspace]\nextends=":workspace"\n'
        '[permissions.consumer]\nextends="basicly-workspace"\n'
    )
    config.write_text(settings)

    with pytest.raises(ValueError, match=r"consumer.*extends"):
        codex_permissions.remove_permissions(tmp_path)

    assert config.read_text() == settings
