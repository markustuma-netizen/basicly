from __future__ import annotations

import base64
import json
from pathlib import Path

from basicly import codex_hooks
from basicly.hooks import HookSpec


def test_windows_command_carries_paths_and_event_without_cmd_shell_metacharacters() -> None:
    spec = HookSpec("guard", "guard.py", "pretooluse", manager="codex")
    settings = codex_hooks.merge_hooks({}, [spec], "agent's config/hooks")
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["commandWindows"]
    prefix = "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand "
    assert command.startswith(prefix)
    payload = command.removeprefix(prefix)
    assert all(character.isalnum() or character in "+/=" for character in payload)
    script = base64.b64decode(payload).decode("utf-16-le")
    assert "agent''s config/hooks/lefthook-runtime.py" in script
    assert '" codex pretooluse' in script
    assert "$start.UseShellExecute = $false" in script
    assert "RedirectStandard" not in script
    assert "$process.WaitForExit(); exit $process.ExitCode" in script


def test_projection_retains_foreign_handlers_in_a_shared_event_group(tmp_path: Path) -> None:
    path = tmp_path / codex_hooks.CONFIG
    path.parent.mkdir()
    foreign = {"type": "command", "command": "consumer-check"}
    old = {"type": "command", "command": ".basicly/core/hooks/lefthook-runtime.py old"}
    current = {"hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [old, foreign]}]}}
    path.write_text(json.dumps(current), encoding="utf-8")
    specs = [HookSpec("guard", "guard.py", "pretooluse", manager="codex")]
    assert codex_hooks.sync_hooks(tmp_path, specs, ".basicly/core/hooks")
    groups = json.loads(path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert groups[0] == {"matcher": "Edit", "hooks": [foreign]}
    assert len(groups) == 2
    assert not codex_hooks.sync_hooks(tmp_path, specs, ".basicly/core/hooks")
    assert not codex_hooks.mismatches(tmp_path, specs, ".basicly/core/hooks")
    assert codex_hooks.sync_hooks(tmp_path, [], ".basicly/core/hooks")
    assert json.loads(path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"] == [groups[0]]
