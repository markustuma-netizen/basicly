from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def _subject() -> Any:
    path = Path(__file__).resolve().parents[2] / ".basicly/core/hooks/affected_checks.py"
    spec = importlib.util.spec_from_file_location("affected_checks_subject", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=True, text=True
    ).stdout.strip()


@pytest.fixture
def staged_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "old.py").write_text("value = 1\n")
    _git(tmp_path, "add", "--all")
    _git(tmp_path, "commit", "-qm", "fixture baseline")
    (tmp_path / "old.py").rename(tmp_path / "renamed.txt")
    _git(tmp_path, "add", "--all")
    return tmp_path


def _provider(subject: Any, response: str) -> dict:
    code = "import json,sys; request=json.load(sys.stdin); " + response
    return subject.parse_provider(
        {
            "command": [sys.executable, "-c", code],
            "checks": ["python", "web"],
            "timeout_seconds": 5,
        },
        ["python", "web", "guard"],
    )


def test_complete_provider_preserves_rename_sources_and_unscoped_checks(staged_repo: Path) -> None:
    subject = _subject()
    provider = _provider(
        subject,
        "assert request['changes'] == "
        "[{'status':'R100','old_path':'old.py','path':'renamed.txt'}]; "
        "print(json.dumps({**request,'complete':True,'selected':['python'],"
        "'reasons':{'python':'old Python owner','web':'unrelated web project'},'unverified':[]}))",
    )
    result = subject.select(
        staged_repo,
        provider,
        ["python", "web", "guard"],
        snapshot_tree=_git(staged_repo, "write-tree"),
    )
    assert result["selected"] == ["python", "guard"]
    assert result["reasons"]["web"] == "unrelated web project"
    assert not result["fallback"]
    assert result["snapshot_tree"] != result["base_tree"]


@pytest.mark.parametrize(
    "alteration",
    [
        "'complete':False",
        "'snapshot_tree':'stale'",
        "'base_tree':'stale'",
        "'selected':['unknown']",
        "'checks':['python']",
        "'unverified':['schema consumer unknown']",
        "'reasons':{}",
        "'selected':['python','python']",
        "'changes':[]",
    ],
)
def test_uncertain_or_mismatched_provider_runs_broad(staged_repo: Path, alteration: str) -> None:
    subject = _subject()
    provider = _provider(
        subject,
        "response={**request,'complete':True,'selected':['python'],"
        "'reasons':{'python':'changed','web':'unchanged'},'unverified':[]}; response.update({"
        + alteration
        + "}); print(json.dumps(response))",
    )
    result = subject.select(
        staged_repo,
        provider,
        ["python", "web", "guard"],
        snapshot_tree=_git(staged_repo, "write-tree"),
    )
    assert result["selected"] == ["python", "web", "guard"]
    assert result["fallback"]


@pytest.mark.parametrize(
    "program",
    [
        "print('not json')",
        "raise SystemExit(2)",
        "import time; time.sleep(30)",
        "print('x'*1100000)",
    ],
)
def test_provider_process_failures_run_broad(staged_repo: Path, program: str) -> None:
    subject = _subject()
    provider = _provider(subject, program)
    provider["timeout_seconds"] = 0.1
    result = subject.select(
        staged_repo,
        provider,
        ["python", "web", "guard"],
        snapshot_tree=_git(staged_repo, "write-tree"),
    )
    assert result["selected"] == ["python", "web", "guard"]
    assert result["fallback"]


@pytest.mark.parametrize("condition", ["missing-context", "changed-worktree", "untracked-source"])
def test_selection_refuses_unproven_snapshot(staged_repo: Path, condition: str) -> None:
    subject = _subject()
    provider = _provider(subject, "raise AssertionError('provider must not run')")
    tree = _git(staged_repo, "write-tree")
    if condition == "changed-worktree":
        (staged_repo / "renamed.txt").write_text("unstaged repair\n")
    if condition == "untracked-source":
        (staged_repo / "untracked.py").write_text("value = 2\n")
    result = subject.select(
        staged_repo,
        provider,
        ["python", "web", "guard"],
        snapshot_tree=None if condition == "missing-context" else tree,
    )
    assert result["selected"] == ["python", "web", "guard"]
    assert result["fallback"]
    assert "provider must not run" not in result["fallback"]


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"command": [], "checks": ["python"]},
        {"command": ["python"], "checks": ["unknown"]},
        {"command": ["python"], "checks": ["python"], "timeout_seconds": False},
        {"command": ["python"], "checks": ["python"], "extra": True},
    ],
)
def test_bad_provider_configuration_fails_by_name(raw: dict) -> None:
    with pytest.raises(ValueError, match=r"verify\.affected"):
        _subject().parse_provider(raw, ["python"])


@pytest.mark.parametrize("valid_response", [False, True])
def test_provider_cannot_change_the_verified_snapshot(
    staged_repo: Path, valid_response: bool
) -> None:
    subject = _subject()
    response = (
        "print(json.dumps({**request,'complete':True,'selected':['python'],'reasons':{'python':'changed','web':'unchanged'},'unverified':[]}))"
        if valid_response
        else "print('invalid response')"
    )
    provider = _provider(
        subject,
        "from pathlib import Path; Path('renamed.txt').write_text('provider repair'); " + response,
    )
    result = subject.select(
        staged_repo,
        provider,
        ["python", "web", "guard"],
        snapshot_tree=_git(staged_repo, "write-tree"),
    )
    assert result["invalid_snapshot"]
    assert result["selected"] == ["python", "web", "guard"]
    assert "provider changed snapshot" in result["fallback"]
