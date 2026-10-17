from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from basicly import worktree, worktree_preservation
from tests import test_worktree_safety

safety_repo = test_worktree_safety.safety_repo


def _fixture_cache(root: Path) -> Path:
    directory = root / ".pytest_cache"
    (directory / "v/cache").mkdir(parents=True)
    (directory / "CACHEDIR.TAG").write_bytes(b"Signature: 8a477f597d28d172789f06886806bc55\n")
    (directory / ".gitignore").write_bytes(b"# Created by pytest automatically.\n*\n")
    (directory / "v/cache/nodeids").write_text("[]", encoding="utf-8")
    return directory


@pytest.mark.parametrize("change", ["new", "changed"])
def test_cleanup_refuses_generated_files_changed_after_preservation(
    safety_repo: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    session = worktree.create("late-cache", repo_root=safety_repo)
    cache = _fixture_cache(session.path)
    preserve = worktree_preservation.preserve

    def preserve_then_change(main: Path, root: Path, paths: tuple[Path, ...]) -> Path | None:
        manifest = preserve(main, root, paths)
        name = "lastfailed" if change == "new" else "nodeids"
        (cache / "v/cache" / name).write_text('["late"]', encoding="utf-8")
        return manifest

    monkeypatch.setattr(worktree_preservation, "preserve", preserve_then_change)
    with pytest.raises(SystemExit, match="changed during"):
        worktree.cleanup(session.name, repo_root=safety_repo)
    assert session.path.exists()
    assert (cache / "v/cache/nodeids").exists()
    monkeypatch.setattr(worktree_preservation, "preserve", preserve)
    worktree.cleanup(session.name, repo_root=safety_repo)
    assert not session.path.exists()


def test_partial_preservation_failure_leaves_sources_intact_and_cleanup_retryable(
    safety_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = worktree.create("partial-copy", repo_root=safety_repo)
    cache = _fixture_cache(session.path)
    before = {p.relative_to(cache): p.read_bytes() for p in cache.rglob("*") if p.is_file()}
    copy = shutil.copyfile
    calls = 0

    def interrupted_copy(source: Path, target: Path) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("fixture interruption")
        return str(copy(source, target))

    monkeypatch.setattr(shutil, "copyfile", interrupted_copy)
    with pytest.raises(OSError, match="fixture interruption"):
        worktree.cleanup(session.name, repo_root=safety_repo)
    assert {p.relative_to(cache): p.read_bytes() for p in cache.rglob("*") if p.is_file()} == before
    monkeypatch.setattr(shutil, "copyfile", copy)
    worktree.cleanup(session.name, repo_root=safety_repo)
    assert not session.path.exists()
