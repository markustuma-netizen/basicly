from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from basicly import merge, verify
from tests.test_merge import _HAS_WORK, _FakeGit, _patch_git, _Proc, _session


@pytest.fixture(autouse=True)
def _base_ready(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(merge, "load_session", lambda _n, _r: _session())
    monkeypatch.setattr(merge, "current_branch", lambda _r: "main")


def _landing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rerun: verify.VerifyReport):
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: rerun)
    return merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")


def test_a_first_failure_carries_the_original_check_command_and_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    reproduced = verify.VerifyReport(
        "full",
        (
            verify.CheckResult(
                "pyright", "fail", 1, output="src/a.py:1: error", command=("pyright", "--strict")
            ),
            verify.CheckResult("ruff", "pass", 0, command=("ruff", "check")),
        ),
    )

    result = _landing(monkeypatch, tmp_path, reproduced)

    assert result.status == "verify-failed"
    assert [(c.name, c.output, c.command) for c in result.checks] == [
        ("pyright", "src/a.py:1: error", ("pyright", "--strict"))
    ]


def test_a_known_dependency_defect_carries_no_lane_repair_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = verify.VerifyReport(
        "full",
        (
            verify.CheckResult(
                "pytest", "fail", 1, output="LockUnavailableError: another writer holds the ledger"
            ),
        ),
    )
    result = _landing(monkeypatch, tmp_path, report)

    assert result.unreliable is True
    assert result.checks == ()


def test_a_failed_landing_does_not_execute_a_diagnostic_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; p=Path('executions'); "
        "p.write_text(p.read_text()+'x' if p.exists() else 'x'); "
        "print('original failure'); sys.exit(7)",
    )
    config = verify.VerifyConfig((verify.VerifyCheck("failure", command, frozenset({"full"})),))
    monkeypatch.setattr(verify, "load_verify_config", lambda *_a: config)
    monkeypatch.setattr(
        merge, "load_session", lambda *_a: replace(_session(), worktree_path=str(tmp_path))
    )
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert (tmp_path / "executions").read_text() == "x"
    assert result.status == "verify-failed"
    assert result.checks[0].command == command
    assert result.checks[0].output == "original failure\n"
    assert result.checks[0].returncode == 7
