from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from basicly.hooks import HookSpec
from basicly.lefthook_config import managed_config, root_config, sync_config


def test_merge_preserves_foreign_hooks_and_is_idempotent() -> None:
    current = "pre-commit:\n  commands:\n    consumer:\n      run: echo hi\n"
    result = root_config(current)
    assert yaml.safe_load(result)["pre-commit"]["commands"]["consumer"] == {"run": "echo hi"}
    assert root_config(result) == result


def test_hook_entry_quotes_paths_with_spaces() -> None:
    spec = HookSpec("check", "check.py", "pre-commit")
    config = yaml.safe_load(managed_config([spec], "agent config/hooks"))
    command = config["pre-commit"]["commands"]["basicly-dispatch"]
    assert '"agent config/hooks/lefthook-runtime.py"' in command["run"]
    assert (
        json.loads(command["env"]["BASICLY_HOOK_SPECS"])[0]["script"]
        == "agent config/hooks/check.py"
    )


def test_foreign_comments_and_pins_survive_projection() -> None:
    current = (
        "# pinned consumer check\nremotes:\n"
        "  - git_url: https://example.invalid/checks\n    ref: v1\n"
    )
    result = root_config(current)
    assert "# pinned consumer check" in result
    assert yaml.safe_load(result)["remotes"] == yaml.safe_load(current)["remotes"]
    assert root_config(result) == result


def test_consumer_cannot_overwrite_the_managed_dispatcher(tmp_path: Path) -> None:
    config = tmp_path / "lefthook.yml"
    current = "pre-commit:\n  commands:\n    basicly-dispatch:\n      run: echo bypass\n"
    config.write_text(current, encoding="utf-8")
    with pytest.raises(ValueError, match="overrides managed"):
        sync_config(tmp_path, [HookSpec("check", "check.py", "pre-commit")], ".basicly/core/hooks")
    assert config.read_text(encoding="utf-8") == current
    assert not (tmp_path / ".basicly/lefthook.yml").exists()
