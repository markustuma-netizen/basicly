from __future__ import annotations

from pathlib import Path

import pytest

from basicly import merge, process_confirmation, verify
from tests.test_merge import _HAS_WORK, _FakeGit, _patch_git, _Proc, _session


@pytest.mark.parametrize("matching", [False, True])
def test_missing_confirmation_refuses_before_any_landing_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, matching: bool
) -> None:
    bead = "demo-1"
    fake = _patch_git(
        monkeypatch,
        _FakeGit({
            **_HAS_WORK,
            "status": _Proc(0, ""),
            "rebase": _Proc(0),
            "merge": _Proc(0),
            "rev-parse": _Proc(0, "landed"),
            "merge-base": _Proc(0),
        }),
    )
    monkeypatch.setattr(merge, "load_session", lambda *_: _session())
    monkeypatch.setattr(merge, "current_branch", lambda *_: "main")
    monkeypatch.setattr(merge, "known_bead_ids", lambda *_: {bead})
    report = verify.VerifyReport(
        "full",
        (verify.CheckResult("actual", "pass", 0, output="observed result", command=("check",)),),
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: report)
    monkeypatch.setattr(
        process_confirmation.tracker,
        "confirmation_evidence",
        lambda *_: (
            {
                "owed": [],
                "review": {
                    "checks": [
                        {
                            "criterion": "Consumer sees the result",
                            "command": ["check"] if matching else ["missing-check"],
                        }
                    ]
                },
            },
            not matching,
        ),
    )
    written = []

    def confirm(_root, argv):
        assert not fake.ran("merge")
        written.append(argv)

    monkeypatch.setattr(process_confirmation.tracker, "write", confirm)
    result = merge.merge_worktree(tmp_path, "feat", bead=bead)
    assert result.merged is matching
    assert fake.ran("merge") is matching
    if matching:
        assert not written
    else:
        assert result.status == "not-ready"
        assert "missing-check" in result.detail
        assert "tracker write -- confirm demo-1" in result.detail
        assert not written
        assert not fake.ran("rebase")
        assert not fake.ran("commit")
