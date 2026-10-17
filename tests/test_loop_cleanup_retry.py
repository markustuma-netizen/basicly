from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from basicly import loop, loop_state, policy, tracker, worktree
from tests import test_integration_loop
from tests.test_integration_loop import _commit, _create_bead, _to_build

if TYPE_CHECKING:
    import pytest

harness_repo = test_integration_loop.harness_repo


def _ready_to_ship(repo: Path) -> tuple[str, Path]:
    issue = _create_bead(repo, "retry ordinary cleanup")
    _to_build(repo, issue)
    state = loop_state.read_node_state(repo, issue)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    _commit(session.path, "app.txt", "complete\n", f"feat: complete the fixture ({issue})")
    assert loop.advance(repo, issue).action == "merged"
    policy.approve_checkpoint(repo, issue, "ship")
    return issue, session.path


def test_cleanup_failure_leaves_an_open_task_and_retry_closes_it_once(
    harness_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue, tree = _ready_to_ship(harness_repo)
    real_cleanup = worktree.cleanup

    def fail(*_args, **_kwargs) -> None:
        raise RuntimeError("fixture cleanup blocker")

    monkeypatch.setattr(worktree, "cleanup", fail)
    blocked = loop.advance(harness_repo, issue)
    assert blocked.blocked and "fixture cleanup blocker" in blocked.detail
    assert tracker.require_record(harness_repo, issue)["status"] == "in_progress"
    assert tree.exists()

    monkeypatch.setattr(worktree, "cleanup", real_cleanup)
    done = loop.advance(harness_repo, issue)
    assert done.to_phase == "done"
    assert tracker.require_record(harness_repo, issue)["status"] == "closed"
    assert not tree.exists()


def test_a_late_close_refusal_after_cleanup_remains_retryable(
    harness_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue, tree = _ready_to_ship(harness_repo)
    real_write = loop._write

    def fail_close(root: Path, args: list[str]):
        if args[0] == "close":
            raise RuntimeError("fixture late close refusal")
        return real_write(root, args)

    monkeypatch.setattr(loop, "_write", fail_close)
    blocked = loop.advance(harness_repo, issue)
    assert blocked.blocked and "late close refusal" in blocked.detail
    assert tracker.require_record(harness_repo, issue)["status"] == "in_progress"
    assert not tree.exists()

    monkeypatch.setattr(loop, "_write", real_write)
    assert loop.advance(harness_repo, issue).to_phase == "done"
    events = tracker.kit(harness_repo, "events")
    closed = [
        event
        for event in events.read_events(tracker.ledger_dir(harness_repo))[0]
        if event.record == issue
        and event.kind == events.KIND_STATUS
        and event.payload.get("status") == "closed"
    ]
    assert len(closed) == 1
