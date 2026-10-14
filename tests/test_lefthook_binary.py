from __future__ import annotations

from pathlib import Path

import pytest

from basicly import lefthook_binary


def test_binary_selection_covers_six_platforms_and_refuses_unsupported() -> None:
    for system in ("Linux", "Darwin", "Windows"):
        for machine in ("x86_64", "arm64"):
            assert lefthook_binary.binary_bytes(system, machine)
    assert lefthook_binary.asset_name("Windows", "AMD64").endswith("Windows_x86_64.gz")
    with pytest.raises(ValueError, match="does not support"):
        lefthook_binary.asset_name("Linux", "riscv64")


def test_installed_binary_checks_license_and_preserves_foreign_ignore(tmp_path: Path) -> None:
    lefthook_binary.sync_binary(tmp_path)
    license_path = tmp_path / ".basicly/bin/LICENSE.lefthook"
    license_path.unlink()
    assert lefthook_binary.binary_mismatches(tmp_path) == [
        (license_path, "bundled Lefthook license missing or differs")
    ]
    ignore = license_path.parent / ".gitignore"
    ignore.write_text("consumer-specific\n", encoding="utf-8")
    lefthook_binary.remove_binary(tmp_path)
    assert ignore.read_text(encoding="utf-8") == "consumer-specific\n"


def test_install_replaces_the_previous_release_binary_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_download(*_args, **_kwargs):
        pytest.fail("Consumer binary upgrade attempted a network download")

    monkeypatch.setattr(
        lefthook_binary.lefthook_artifacts.urllib.request, "urlopen", refuse_download
    )
    lefthook_binary.sync_binary(tmp_path)
    path = lefthook_binary.binary_path(tmp_path)
    previous = path.read_bytes()
    consumer_file = path.parent / "consumer.txt"
    consumer_file.write_text("retain this", encoding="utf-8")
    replacement = b"the executable bundled in the next basicly release"
    assert previous != replacement
    monkeypatch.setattr(lefthook_binary, "binary_bytes", lambda *_args: replacement)

    lefthook_binary.sync_binary(tmp_path)
    lefthook_binary.sync_binary(tmp_path)

    assert path.read_bytes() == replacement
    assert consumer_file.read_text(encoding="utf-8") == "retain this"
    assert lefthook_binary.binary_mismatches(tmp_path) == []
