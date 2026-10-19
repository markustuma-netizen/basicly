from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import pyright_inputs, verify
from basicly.config import VerifyCheck


@pytest.fixture(autouse=True)
def posix_recipe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pyright_inputs, "os", SimpleNamespace(name="posix", pathsep=os.pathsep, defpath=os.defpath)
    )


def test_hook_metadata_and_duplicate_paths_do_not_change_the_executed_compiler_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "bin"
    binary.mkdir()
    monkeypatch.setattr(pyright_inputs.shutil, "which", lambda name, **_kw: str(binary / name))
    original = {"PATH": str(binary), "HOME": str(tmp_path), "PYTHONWARNINGS": "default"}
    hooked = {
        **original,
        "PATH": os.pathsep.join([str(tmp_path / "git"), str(binary), str(binary)]),
        "BASICLY_HOOK_SPECS": "hook metadata",
        "CODEX_TOOL_CALL_ID": "another call",
        "HTTP_PROXY": "different proxy",
        "UV_RUN_RECURSION_DEPTH": "2",
    }
    environments: list[dict[str, str]] = []

    def execute(command, **kwargs):
        environments.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(verify.subprocess, "run", execute)
    check = VerifyCheck(
        "types", ("pyright", "--pythonplatform", "Linux"), frozenset({"full"}), reuse="pyright"
    )
    for environment in (original, hooked):
        monkeypatch.setattr(
            verify.affected, "environment", lambda _root, current=environment: current
        )
        assert verify.run_check(check, tmp_path, "full", capture=True).status == "pass"
    assert environments == [original, original]
    assert pyright_inputs.execution_environment(tmp_path, hooked) == environments[0]
    assert "BASICLY_HOOK_SPECS" in hooked


def test_other_checks_keep_their_original_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = {"PATH": "original", "BASICLY_HOOK_SPECS": "metadata", "HTTP_PROXY": "proxy"}
    observed: list[dict[str, str]] = []

    def execute(command, **kwargs):
        observed.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(verify.affected, "environment", lambda _: environment)
    monkeypatch.setattr(verify.subprocess, "run", execute)
    check = VerifyCheck("other", ("other",), frozenset({"full"}))
    assert verify.run_check(check, tmp_path, "full", capture=True).status == "pass"
    assert observed == [environment]


def test_compiler_execution_does_not_depend_on_the_callers_virtual_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "bin"
    binary.mkdir()
    monkeypatch.setattr(pyright_inputs.shutil, "which", lambda tool, **_kw: str(binary / tool))
    original = {"PATH": str(binary), "HOME": str(tmp_path)}
    marked = {**original, "VIRTUAL_ENV": str(tmp_path / ".venv")}
    sibling = tmp_path / "sibling"

    assert pyright_inputs.execution_environment(tmp_path, marked) == original
    assert pyright_inputs.execution_environment(sibling, marked) == original
    assert marked["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_unsupported_platform_keeps_the_fresh_execution_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = {"PATH": "original", "PATHEXT": ".EXE", "LOCALAPPDATA": "local"}
    monkeypatch.setattr(pyright_inputs, "os", SimpleNamespace(name="nt"))
    assert pyright_inputs.execution_environment(tmp_path, environment) == environment


@pytest.mark.parametrize("name", ["PYTHONWARNINGS", "NODE_EXTRA_CA_CERTS", "LC_ALL", "HOME"])
def test_retained_runtime_inputs_change_the_executed_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    binary = tmp_path / "bin"
    binary.mkdir()
    monkeypatch.setattr(pyright_inputs.shutil, "which", lambda tool, **_kw: str(binary / tool))
    original = {"PATH": str(binary), name: "before"}
    changed = {**original, name: "after"}
    assert pyright_inputs.execution_environment(tmp_path, original)[name] == "before"
    assert pyright_inputs.execution_environment(tmp_path, changed)[name] == "after"
