from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

HOOK = Path(__file__).resolve().parents[1] / ".basicly" / "core" / "hooks" / "check_runner.py"
SWAP_PHRASE = "the Python behind this run changed while it ran"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_runner_interpreter_hook", HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo(tmp_path: Path, home: Path, command: list[str]) -> Path:

    repo = tmp_path / "repo"
    (repo / ".venv").mkdir(parents=True)
    (repo / ".venv" / "pyvenv.cfg").write_text(f"home = {home}\n", encoding="utf-8")
    (repo / "basicly.toml").write_text(
        f'[[verify.checks]]\nname = "gate"\ncommand = {json.dumps(command)}\nmodes = ["fast"]\n',
        encoding="utf-8",
    )
    return repo


def test_a_failing_run_names_an_interpreter_swap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    home = tmp_path / "cpython-3.14" / "bin"
    home.mkdir(parents=True)
    swap = f"import os, sys; os.rmdir({str(home)!r}); sys.exit(1)"
    repo = _repo(tmp_path, home, [sys.executable, "-c", swap])

    assert _load().run_checks(repo, "fast") == 1

    err = capsys.readouterr().err
    assert SWAP_PHRASE in err
    assert home.as_posix() in err
    assert "Run the checks again." in err


def test_a_failing_run_without_a_swap_names_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    home = tmp_path / "cpython-3.14" / "bin"
    home.mkdir(parents=True)
    repo = _repo(tmp_path, home, [sys.executable, "-c", "import sys; sys.exit(1)"])

    assert _load().run_checks(repo, "fast") == 1

    assert SWAP_PHRASE not in capsys.readouterr().err


def test_without_a_venv_the_base_prefix_is_the_home(tmp_path: Path) -> None:

    assert _load().interpreter_home(tmp_path) == Path(sys.base_prefix)


def test_the_venv_home_line_is_the_home(tmp_path: Path) -> None:

    home = tmp_path / "python" / "bin"
    repo = _repo(tmp_path, home, ["true"])

    assert _load().interpreter_home(repo) == home
