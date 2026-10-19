from __future__ import annotations

import os
import subprocess
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from basicly import owned_write, tracker
from tests import flipped_tracker, ledger_guard

NODE = "tests/test_conftest_guard.py::test_the_writer_is_named"

RECORD = "vkh-51"

PROBE_LINE = b'{"probe": "this is not an event"}\n'


def temp_copy(tmp_path: Path, live: Path) -> tuple[Path, Path]:

    root = tmp_path / "ledger"
    root.mkdir()
    logs = sorted(live.glob(ledger_guard.LOG_GLOB))
    copy = root / (logs[-1].name if logs else "events-0001.jsonl")
    if logs:
        with logs[-1].open("rb") as stream:
            copy.write_bytes(stream.readline())
    else:
        copy.write_bytes(b'{"stand-in": "no live log in this checkout"}\n')
    return root, copy


@pytest.fixture
def live_copy(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[Path, Path]:
    return temp_copy(tmp_path, Path(request.config.rootpath) / ".basicly" / "ledger")


APPEND_SCRIPT = (
    "import pathlib, sys\n"
    "with pathlib.Path(sys.argv[1]).open('ab') as log:\n"
    "    log.write(sys.argv[2].encode())\n"
)


def append_from_another_process(path: Path, line: bytes) -> None:

    subprocess.run(
        [sys.executable, "-c", APPEND_SCRIPT, str(path), line.decode()],
        check=True,
    )


def test_a_test_that_appends_to_the_watched_store_is_named_and_nothing_is_undone(
    live_copy: tuple[Path, Path],
) -> None:
    root, log = live_copy
    before = log.read_bytes()

    with ledger_guard.watching(root, NODE) as watch, log.open("ab") as stream:
        stream.write(PROBE_LINE)

    with pytest.raises(pytest.fail.Exception) as blame:
        ledger_guard.report(watch)
    assert NODE in str(blame.value)
    assert log.name in str(blame.value)
    assert log.read_bytes() == before + PROBE_LINE


def test_a_write_from_another_process_is_reported_and_not_blamed(
    live_copy: tuple[Path, Path],
) -> None:
    root, log = live_copy
    before = log.read_bytes()

    with ledger_guard.watching(root, NODE) as watch:
        append_from_another_process(log, PROBE_LINE)

    assert watch.written == ()
    assert watch.unexplained == (str(log),)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ledger_guard.report(watch)
    assert [warned.category for warned in caught] == [ledger_guard.LedgerChangedWarning]
    notice = str(caught[0].message)
    assert "no write from this process explains it" in notice
    assert "No test is blamed" in notice
    assert log.read_bytes() == before + PROBE_LINE


def test_a_log_another_process_created_is_reported_and_left_alone(
    live_copy: tuple[Path, Path],
) -> None:

    root, _ = live_copy
    appeared = root / "events-0002.jsonl"

    with ledger_guard.watching(root, NODE) as watch:
        append_from_another_process(appeared, PROBE_LINE)

    assert watch.written == ()
    assert watch.unexplained == (str(appeared),)
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        ledger_guard.report(watch)
    assert appeared.read_bytes() == PROBE_LINE


def test_reading_the_watched_store_is_not_a_write(live_copy: tuple[Path, Path]) -> None:

    root, log = live_copy

    with ledger_guard.watching(root, NODE) as watch:
        assert log.read_bytes()

    assert watch.written == ()
    assert watch.unexplained == ()
    ledger_guard.report(watch)


def test_the_original_incident_shape_still_reaches_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, RECORD, title="the incident shape")
    monkeypatch.chdir(repo)

    with ledger_guard.watching(tracker.ledger_dir(repo), NODE) as watch:
        owned_write.append(Path(), ["update", RECORD, "-t", "bug"])

    assert len(flipped_tracker.ledger_events(repo)) > 1
    logs = sorted(tracker.ledger_dir(repo).glob(ledger_guard.LOG_GLOB))
    assert [Path(name).name for name in watch.written if name.endswith(".jsonl")] == [logs[0].name]
    with pytest.raises(pytest.fail.Exception) as blame:
        ledger_guard.report(watch)
    assert NODE in str(blame.value)


def test_repeated_unchanged_observations_do_not_reread_supported_files(
    live_copy: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, log = live_copy
    read_bytes = Path.read_bytes
    reads: list[bytes] = []

    def read(path: Path) -> bytes:
        content = read_bytes(path)
        if path == log:
            reads.append(content)
        return content

    monkeypatch.setattr(Path, "read_bytes", read)
    for _ in range(5):
        with ledger_guard.watching(root, NODE) as watch:
            pass
        assert watch.written == watch.unexplained == ()
    supported = os.name == "posix" and log.stat().st_ctime_ns % 1000 != 0
    assert len(reads) == (1 if supported else 10)


def test_a_same_size_external_edit_with_restored_mtime_is_still_reported(
    live_copy: tuple[Path, Path],
) -> None:
    root, log = live_copy
    original = log.read_bytes()
    state = log.stat()
    program = (
        "import os,pathlib,sys; p=pathlib.Path(sys.argv[1]);"
        "p.write_bytes(b'x'*len(p.read_bytes()));"
        "os.utime(p,ns=(int(sys.argv[2]),int(sys.argv[3])))"
    )
    with ledger_guard.watching(root, NODE) as watch:
        subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                str(log),
                str(state.st_atime_ns),
                str(state.st_mtime_ns),
            ],
            check=True,
        )
    assert log.stat().st_size == len(original)
    assert log.stat().st_mtime_ns == state.st_mtime_ns
    assert watch.written == ()
    assert watch.unexplained == (str(log),)


def test_replacing_a_log_with_equal_size_and_mtime_is_still_reported(
    live_copy: tuple[Path, Path],
) -> None:
    root, log = live_copy
    state = log.stat()
    program = (
        "import os,pathlib,sys; p=pathlib.Path(sys.argv[1]); q=p.with_suffix('.replacement');"
        "q.write_bytes(b'y'*int(sys.argv[2]));"
        "os.utime(q,ns=(int(sys.argv[3]),int(sys.argv[4]))); q.replace(p)"
    )
    with ledger_guard.watching(root, NODE) as watch:
        subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                str(log),
                str(state.st_size),
                str(state.st_atime_ns),
                str(state.st_mtime_ns),
            ],
            check=True,
        )
    assert watch.written == ()
    assert watch.unexplained == (str(log),)


def test_a_write_that_restores_original_bytes_still_blames_the_test(
    live_copy: tuple[Path, Path],
) -> None:
    root, log = live_copy
    before = log.read_bytes()
    with ledger_guard.watching(root, NODE) as watch:
        log.write_bytes(before)
    assert watch.unexplained == ()
    with pytest.raises(pytest.fail.Exception, match="opened this checkout's own ledger"):
        ledger_guard.report(watch)


def test_unsupported_change_identity_keeps_fresh_byte_comparisons(
    live_copy: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, log = live_copy
    read_bytes = Path.read_bytes
    reads: list[bytes] = []

    def read(path: Path) -> bytes:
        content = read_bytes(path)
        if path == log:
            reads.append(content)
        return content

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(ledger_guard, "_cache_identity", lambda _state, _platform: None)
    for _ in range(3):
        with ledger_guard.watching(root, NODE) as watch:
            pass
        assert watch.written == watch.unexplained == ()
    assert len(reads) == 6


def test_windows_creation_time_never_proves_unchanged_content(
    live_copy: tuple[Path, Path],
) -> None:
    _, log = live_copy
    assert ledger_guard._cache_identity(log.stat(), "nt") is None


def test_a_change_during_a_content_read_does_not_establish_a_cached_snapshot(
    live_copy: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, log = live_copy
    read_bytes = Path.read_bytes
    changed = False

    def read(path: Path) -> bytes:
        nonlocal changed
        content = read_bytes(path)
        if path == log and not changed:
            changed = True
            append_from_another_process(path, PROBE_LINE)
        return content

    monkeypatch.setattr(Path, "read_bytes", read)
    with ledger_guard.watching(root, NODE) as watch:
        pass
    assert changed
    assert watch.written == ()
    assert watch.unexplained == (str(log),)


def test_nested_observations_retain_their_own_before_bytes(
    live_copy: tuple[Path, Path],
) -> None:
    root, log = live_copy
    with ledger_guard.watching(root, NODE) as outer:
        append_from_another_process(log, PROBE_LINE)
        with ledger_guard.watching(root, NODE) as inner:
            append_from_another_process(log, PROBE_LINE)
    assert outer.written == inner.written == ()
    assert outer.unexplained == inner.unexplained == (str(log),)


@pytest.mark.parametrize("content", [b"test", b""])
def test_cached_observations_keep_a_bounded_byte_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes
) -> None:
    root = tmp_path / "bounded"
    root.mkdir()
    for number in range(3):
        (root / f"events-{number:04}.jsonl").write_bytes(content)
    monkeypatch.setattr(ledger_guard, "_CACHE", {})
    monkeypatch.setattr(ledger_guard, "_CACHE_BYTES", 8)
    monkeypatch.setattr(ledger_guard, "_CACHE_FILES", 2)
    with ledger_guard.watching(root, NODE) as watch:
        pass
    assert sum(len(value[1]) for value in ledger_guard._CACHE.values()) <= 8
    assert len(ledger_guard._CACHE) <= 2
    assert watch.written == watch.unexplained == ()


@pytest.mark.parametrize("timestamp", [0, 1_000_000_000, 1_700_000_000_123_456_000])
def test_coarse_change_timestamps_do_not_enable_cached_observations(
    timestamp: int, live_copy: tuple[Path, Path]
) -> None:
    _, log = live_copy
    state = cast(
        "os.stat_result", SimpleNamespace(st_mode=log.stat().st_mode, st_ctime_ns=timestamp)
    )
    assert ledger_guard._cache_identity(state, "posix") is None
