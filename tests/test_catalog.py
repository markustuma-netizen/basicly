from __future__ import annotations

from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

import pytest

from basicly import catalog
from basicly.catalog import bundled_catalog_root, iter_catalog_files

CATALOG_SUBDIRS = ("fragments", "skills", "hooks", "targets", "templates")


def test_bundled_catalog_root_resolves_to_a_real_catalog() -> None:
    root = bundled_catalog_root()
    assert root.is_dir()
    for sub in CATALOG_SUBDIRS:
        assert (root / sub).is_dir(), f"catalog is missing '{sub}/'"


def test_source_checkout_resolves_to_dogfooded_core() -> None:

    assert bundled_catalog_root().as_posix().endswith(".basicly/core")


def test_iter_catalog_files_skips_bytecode(tmp_path: Path) -> None:
    (tmp_path / "hooks" / "__pycache__").mkdir(parents=True)
    (tmp_path / "hooks" / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"")
    (tmp_path / "hooks" / "stale.pyc").write_bytes(b"")
    (tmp_path / "hooks" / "pre-commit.py").write_text("print()\n", encoding="utf-8")

    files = list(iter_catalog_files(tmp_path))
    assert files == [tmp_path / "hooks" / "pre-commit.py"]


def test_iter_catalog_files_excludes_runtime_without_dropping_usage_resources(
    tmp_path: Path,
) -> None:
    resource = tmp_path / "templates/usage/schema.json"
    resource.parent.mkdir(parents=True)
    resource.write_text("{}", encoding="utf-8")
    runtime = tmp_path / "kit/tracker/.basicly/usage/tool-usage.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("{}", encoding="utf-8")
    assert list(iter_catalog_files(tmp_path)) == [resource]


@pytest.mark.parametrize("path_type", [PurePosixPath, PureWindowsPath])
@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("kit/tracker/.basicly/usage/tool-usage.json", False),
        (".basicly/usage/verify-cache.json", False),
        ("hooks/__pycache__/runtime.pyc", False),
        ("hooks/runtime.pyc", False),
        ("templates/usage/schema.json", True),
        ("templates/.basicly/core/schema.json", True),
        ("kit/tracker/events.py", True),
    ],
)
def test_catalog_resource_policy_is_platform_independent(
    path_type: type[PurePath], relative: str, expected: bool
) -> None:
    assert catalog.is_catalog_resource(path_type(relative)) is expected
