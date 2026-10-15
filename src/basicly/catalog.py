from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path, PurePath

CATALOG_DIRNAME = "catalog"


def bundled_catalog_root() -> Path:

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "src" / "basicly" / "catalog.py").is_file():
            source = parent / ".basicly" / "core"
            if source.is_dir():
                return source

    packaged = here.parent / CATALOG_DIRNAME
    if packaged.is_dir():
        return packaged

    raise FileNotFoundError(
        f"bundled catalog not found: no basicly source checkout above {here} "
        f"and no packaged copy at '{packaged}'"
    )


def iter_catalog_files(src: Path) -> Iterator[Path]:

    for path in sorted(src.rglob("*")):
        if path.is_dir():
            continue
        if not is_catalog_resource(path.relative_to(src)):
            continue
        yield path


def is_catalog_resource(path: PurePath) -> bool:
    return (
        "__pycache__" not in path.parts
        and path.suffix != ".pyc"
        and (".basicly", "usage") not in zip(path.parts, path.parts[1:], strict=False)
    )
