from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from basicly import checkout, pyright_inputs, pytest_inputs, verify


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    checkout.git(["init", "-q"], cwd=tmp_path)
    (tmp_path / "source.py").write_text("value = 1\n")
    checkout.git(["add", "source.py"], cwd=tmp_path)
    return tmp_path


def test_tracker_only_changes_preserve_the_workspace_proof(repository: Path) -> None:
    before = pytest_inputs._workspace(repository)
    ledger = repository / ".basicly/ledger"
    ledger.mkdir(parents=True)
    (ledger / "pending-main.jsonl").write_text("tracker metadata\n")
    checkout.git(["add", ".basicly/ledger"], cwd=repository)
    assert pytest_inputs._workspace(repository) == before


@pytest.mark.parametrize("name", ["source.py", "tests/test_source.py", "README.md", "uv.lock"])
def test_changed_source_tests_documentation_and_dependencies_change_proof(
    repository: Path, name: str
) -> None:
    before = pytest_inputs._workspace(repository)
    path = repository / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed\n")
    assert pytest_inputs._workspace(repository) != before


def test_deleted_tracked_input_refuses_reuse(repository: Path) -> None:
    (repository / "source.py").unlink()
    with pytest.raises(OSError):
        pytest_inputs._workspace(repository)


def test_ignored_build_artifacts_remain_test_inputs(repository: Path) -> None:
    (repository / ".gitignore").write_text("build/\n")
    before = pytest_inputs._workspace(repository)
    artifacts = repository / "build/lefthook"
    artifacts.mkdir(parents=True)
    (artifacts / "archive.tar.gz").write_bytes(b"changed")
    assert pytest_inputs._workspace(repository) != before


def test_protected_inventory_is_refused_before_opening(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        checkout, "git", lambda *_a, **_kw: subprocess.CompletedProcess([], 0, ".env\0", "")
    )
    with pytest.raises(pyright_inputs.UnsupportedInputsError, match="protected"):
        pytest_inputs._workspace(repository)


def test_only_the_reviewed_full_suite_command_is_eligible() -> None:
    command = ("pytest", "-q", "-n", "4", "--durations=25", "-m", "not live_ledger")
    assert pytest_inputs.supported_command(command, "full")
    assert not pytest_inputs.supported_command(command, "fast")
    assert not pytest_inputs.supported_command(("pytest", "-q"), "full")
    assert not pytest_inputs.supported_command((*command, "-k", "one_test"), "full")


def test_execution_and_proof_share_the_scrubbed_environment() -> None:
    environment = {"PATH": "tools", "GIT_DIR": "elsewhere", "SHLVL": "9", "CUSTOM": "kept"}
    assert pytest_inputs.execution_environment(environment) == {"PATH": "tools", "CUSTOM": "kept"}


def test_external_import_paths_refuse_runtime_reuse(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "external.pth").write_text(str(tmp_path / "external") + "\n")
    with pytest.raises(pyright_inputs.UnsupportedInputsError, match="external pytest import path"):
        pytest_inputs._dependency(site, tmp_path / "workspace")


def test_changed_runtime_package_changes_the_runtime_proof(tmp_path: Path) -> None:
    package = tmp_path / "package.py"
    package.write_text("value = 1\n")
    before = pytest_inputs._dependency(tmp_path)
    package.write_text("value = 2\n")
    assert pytest_inputs._dependency(tmp_path) != before


def test_empty_launcher_runs_fresh_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / "pytest"
    launcher.write_bytes(b"")
    monkeypatch.setattr(pytest_inputs.shutil, "which", lambda *_a, **_kw: str(launcher))
    inputs, reason = verify._inputs(tmp_path, "pytest")
    assert inputs == ""
    assert "empty pytest launcher" in reason


def test_hook_dispatch_and_nested_uv_environment_do_not_change_execution_inputs() -> None:
    base = {"PATH": os.pathsep.join(["venv", "tools"]), "CUSTOM": "kept"}
    nested = {
        **base,
        "PATH": os.pathsep.join(["venv", "venv", "tools"]),
        "UV_RUN_RECURSION_DEPTH": "3",
        "BASICLY_HOOK_RESULT": "temporary",
        "BASICLY_HOOK_SPECS": "dispatcher",
        "LEFTHOOK_VERBOSE": "1",
        "PWD": "shell",
    }
    assert pytest_inputs.execution_environment(base) == pytest_inputs.execution_environment(nested)
