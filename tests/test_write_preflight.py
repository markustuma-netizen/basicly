from __future__ import annotations

import errno
from pathlib import Path

import pytest

from basicly import write_preflight


def test_writable_destination_probe_preserves_existing_bytes_and_directory_entries(
    tmp_path: Path,
) -> None:
    target = tmp_path / "settings" / "hooks.json"
    target.parent.mkdir()
    target.write_bytes(b"consumer configuration\n")
    before = set(tmp_path.rglob("*"))

    write_preflight.require_writable([target], command="basicly install")

    assert target.read_bytes() == b"consumer configuration\n"
    assert set(tmp_path.rglob("*")) == before


def test_missing_destination_probe_does_not_create_parent_directories(tmp_path: Path) -> None:
    write_preflight.require_writable(
        [tmp_path / ".agents" / "skills" / "new" / "SKILL.md"], command="basicly install"
    )

    assert list(tmp_path.iterdir()) == []


def test_denial_names_target_and_authorized_command_without_mutating_the_destination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / ".codex" / "hooks.json"
    target.parent.mkdir()
    target.write_bytes(b"foreign hook\n")
    before = set(tmp_path.rglob("*"))

    def refuse(*_args, **_kwargs):
        raise OSError(errno.EROFS, "Read-only file system")

    monkeypatch.setattr(write_preflight.tempfile, "TemporaryFile", refuse)

    with pytest.raises(write_preflight.WriteDeniedError) as caught:
        write_preflight.require_writable([target], command="basicly install --technologies python")

    assert str(target) in str(caught.value)
    assert "basicly install --technologies python" in str(caught.value)
    assert "execution review" in str(caught.value)
    assert "before" in str(caught.value)
    cause = caught.value.__cause__
    assert isinstance(cause, OSError)
    assert cause.errno == errno.EROFS
    assert target.read_bytes() == b"foreign hook\n"
    assert set(tmp_path.rglob("*")) == before


def test_empty_plan_never_probes_a_protected_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*_args, **_kwargs):
        pytest.fail("an unchanged destination was probed")

    monkeypatch.setattr(write_preflight.tempfile, "TemporaryFile", unexpected)
    write_preflight.require_writable([], command="basicly install")


def test_missing_protected_parent_is_checked_even_when_the_repository_root_is_writable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original = Path.mkdir

    def mkdir(path, *args, **kwargs):
        if path == tmp_path / ".codex":
            raise OSError(errno.EACCES, "Permission denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    with pytest.raises(write_preflight.WriteDeniedError, match=r"hooks\.json"):
        write_preflight.require_writable(
            [tmp_path / "ordinary.txt", tmp_path / ".codex" / "hooks.json"],
            command="basicly install",
        )
    assert list(tmp_path.iterdir()) == []


def test_non_permission_filesystem_failure_keeps_its_actual_cause(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(write_preflight.tempfile, "TemporaryFile", full)
    with pytest.raises(OSError) as caught:
        write_preflight.require_writable([tmp_path / "output.txt"], command="basicly install")
    assert caught.value.errno == errno.ENOSPC
    assert not isinstance(caught.value, write_preflight.WriteDeniedError)
