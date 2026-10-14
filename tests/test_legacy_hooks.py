from __future__ import annotations

from pathlib import Path

import pytest

from basicly import hooks, legacy_hooks

HOOKS = Path(".basicly/core/hooks")


@pytest.mark.parametrize(
    "content",
    [
        b"# >>> basicly-tracker compact >>>\n",
        b"# <<< basicly-tracker compact <<<\n",
        b"# >>> basicly-tracker compact >>>\n# >>> basicly-tracker compact >>>\n",
    ],
)
def test_malformed_tracker_blocks_refuse_retirement(content: bytes) -> None:
    with pytest.raises(ValueError, match="post-merge"):
        legacy_hooks.foreign_git_hook("post-merge", content)


def legacy_repo(tmp_path: Path, text: str) -> Path:
    (tmp_path / HOOKS).mkdir(parents=True)
    path = tmp_path / ".pre-commit-config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_retired_managed_hook_is_pruned(tmp_path: Path) -> None:
    path = legacy_repo(
        tmp_path,
        "repos:\n- repo: local\n  hooks:\n  - id: beads-commit-msg-script\n"
        "    entry: uv run python .basicly/core/hooks/beads-commit-msg.py\n",
    )
    hooks.sync_hooks(tmp_path, HOOKS)
    assert not path.exists()
    current = (tmp_path / ".basicly/lefthook.yml").read_text(encoding="utf-8")
    assert "beads-commit-msg" not in current
    assert "tracker-commit-msg.py" in current


def test_a_foreign_local_pre_commit_hook_is_refused_without_mutation(tmp_path: Path) -> None:
    text = "repos:\n- repo: local\n  hooks:\n  - id: consumer\n    entry: ./scripts/consumer.sh\n"
    path = legacy_repo(tmp_path, text)
    with pytest.raises(ValueError, match="foreign pre-commit hooks"):
        hooks.sync_hooks(tmp_path, HOOKS)
    assert path.read_text(encoding="utf-8") == text
    assert not (tmp_path / "lefthook.yml").exists()
    assert not (tmp_path / ".basicly/bin").exists()


def test_a_foreign_remote_pre_commit_hook_is_refused_without_mutation(tmp_path: Path) -> None:
    text = "repos:\n- repo: https://example.invalid/checks\n  rev: v1\n  hooks: []\n"
    path = legacy_repo(tmp_path, text)
    with pytest.raises(ValueError, match="foreign pre-commit hooks"):
        hooks.sync_hooks(tmp_path, HOOKS)
    assert path.read_text(encoding="utf-8") == text


def test_a_live_managed_hook_migrates_with_its_exclusion(tmp_path: Path) -> None:
    path = legacy_repo(
        tmp_path,
        "exclude: ^ledger/\nrepos:\n- repo: local\n  hooks:\n"
        "  - id: tracker-commit-msg-script\n"
        "    entry: uv run python .basicly/core/hooks/tracker-commit-msg.py\n",
    )
    hooks.sync_hooks(tmp_path, HOOKS)
    assert not path.exists()
    assert "^ledger/" in (tmp_path / "lefthook.yml").read_text(encoding="utf-8")
    assert not hooks.sync_hooks(tmp_path, HOOKS).written
