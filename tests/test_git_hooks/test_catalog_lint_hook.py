from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


def _load_hook():
    script_path = (
        Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "catalog-lint.py"
    )
    spec = importlib.util.spec_from_file_location("catalog_lint_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def _installed_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / ".basicly/state/install.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"basicly_version": "0.24.0"}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_prefers_basicly_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_hook()
    monkeypatch.setattr(
        module.shutil, "which", lambda name: "/usr/bin/basicly" if name == "basicly" else None
    )
    assert module._cli_command() == ["/usr/bin/basicly", "catalog", "lint"]


def test_falls_back_to_importable_module(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_hook()
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    assert module._cli_command() == [sys.executable, "-m", "basicly.cli", "catalog", "lint"]


def test_falls_back_to_uvx(_installed_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_hook()
    monkeypatch.setattr(
        module.shutil, "which", lambda name: "/usr/bin/uvx" if name == "uvx" else None
    )
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda _name: None)
    assert module._cli_command() == [
        "/usr/bin/uvx",
        "--from",
        module.dist_source(),
        "basicly",
        "catalog",
        "lint",
    ]


def test_dist_source_pins_the_installed_version(tmp_path: Path) -> None:
    module = _load_hook()
    state = tmp_path / module.INSTALL_STATE
    state.parent.mkdir(parents=True)
    state.write_text('{"basicly_version": "0.24.0"}', encoding="utf-8")

    assert module.dist_source(tmp_path) == (
        "https://github.com/niksavis/basicly/releases/download/v0.24.0/"
        "basicly-0.24.0-py3-none-any.whl"
    )


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "{}",
        "not json",
        '{"basicly_version": ""}',
        '{"basicly_version": 7}',
        "[]",
        "null",
        '{"basicly_version": "main"}',
        '{"basicly_version": "0.24.0/../../other"}',
    ],
    ids=[
        "empty",
        "no-version",
        "malformed",
        "blank-version",
        "non-string",
        "array",
        "null",
        "branch",
        "path-traversal",
    ],
)
def test_dist_source_refuses_invalid_install_state(tmp_path: Path, payload: str) -> None:
    module = _load_hook()
    state = tmp_path / module.INSTALL_STATE
    state.parent.mkdir(parents=True)
    state.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match="rerun the basicly install command"):
        module.dist_source(tmp_path)


def test_dist_source_refuses_missing_install_state(tmp_path: Path) -> None:
    module = _load_hook()

    with pytest.raises(ValueError, match="rerun the basicly install command"):
        module.dist_source(tmp_path)


def test_advisory_skip_when_no_channel(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    module = _load_hook()
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda _name: None)

    assert module.main() == 0
    err = capsys.readouterr().err
    assert "catalog-lint skipped" in err
    assert "basicly-gates.yml" in err


def _stub_run(monkeypatch: pytest.MonkeyPatch, module, returncode: int, stderr: str) -> None:

    result = subprocess.CompletedProcess(["uvx"], returncode, stdout="", stderr=stderr)
    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_kw: result)


def test_a_failed_distribution_fetch_is_not_a_passing_gate(
    _installed_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    module = _load_hook()
    monkeypatch.setattr(
        module.shutil, "which", lambda name: "/usr/bin/uvx" if name == "uvx" else None
    )
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda _name: None)
    _stub_run(monkeypatch, module, 1, "fatal: couldn't find remote ref refs/tags/v9.9.9\n")

    assert module.main() == 1
    assert "couldn't find remote ref" in capsys.readouterr().err


def test_a_real_catalog_violation_still_fails(
    _installed_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_hook()
    monkeypatch.setattr(
        module.shutil, "which", lambda name: "/usr/bin/uvx" if name == "uvx" else None
    )
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda _name: None)
    _stub_run(monkeypatch, module, 1, "catalog lint: FAILED\n  no 'invocation' declared\n")

    assert module.main() == 1
    assert "no 'invocation' declared" in capsys.readouterr().err


def test_invalid_state_reports_a_remedy_without_running_a_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_hook()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        module.shutil, "which", lambda name: "/usr/bin/uvx" if name == "uvx" else None
    )
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda _name: None)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **_kwargs: calls.append(args))

    assert module.main() == 1
    assert "rerun the basicly install command" in capsys.readouterr().err
    assert calls == []
