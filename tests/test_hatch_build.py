from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
import zipfile
from pathlib import Path


def test_distributions_bundle_verified_assets_and_sdist_rebuilds_offline(
    work_repo: Path, tmp_path: Path
) -> None:
    output = tmp_path / "distributions"
    (work_repo / "build/lefthook/2.2.2/unrelated.bin").write_bytes(b"not a pinned artifact")
    build = subprocess.run(
        ["uv", "build", "--out-dir", str(output)],
        cwd=work_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheel = next(output.glob("*.whl"))
    sdist = next(output.glob("*.tar.gz"))
    manifest = json.loads((work_repo / "src/basicly/lefthook_assets/manifest.json").read_text())
    with zipfile.ZipFile(wheel) as archive:
        assert not any("catalog/vendor/lefthook" in name for name in archive.namelist())
        assert not any(name.endswith("unrelated.bin") for name in archive.namelist())
        for entry in manifest["assets"]:
            data = archive.read(f"basicly/lefthook_assets/{manifest['version']}/{entry['name']}")
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    extracted = tmp_path / "source"
    with tarfile.open(sdist) as archive:
        assert not any(name.endswith("unrelated.bin") for name in archive.getnames())
        archive.extractall(extracted, filter="data")
    source = next(extracted.iterdir())
    rebuild = subprocess.run(
        ["uv", "build", "--wheel", "--offline", "--out-dir", str(tmp_path / "rebuilt")],
        cwd=source,
        env={**os.environ, "UV_OFFLINE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert rebuild.returncode == 0, rebuild.stdout + rebuild.stderr


def test_unprepared_source_build_refuses_without_implicit_download(
    work_repo: Path, tmp_path: Path
) -> None:
    prepared = work_repo / "build/lefthook"
    prepared.rename(work_repo / "build/previously-prepared")
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path / "dist")],
        cwd=work_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Missing Lefthook artifact" in result.stderr
    assert "--from-dir" in result.stderr
