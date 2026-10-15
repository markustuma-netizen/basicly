from __future__ import annotations

import errno
import tempfile
from collections.abc import Iterable
from contextlib import ExitStack
from pathlib import Path


class WriteDeniedError(OSError):
    pass


def require_writable(destinations: Iterable[Path], *, command: str) -> None:
    checked: set[Path] = set()
    for target in destinations:
        parent = target.parent
        if parent in checked:
            continue
        try:
            with ExitStack() as cleanup:
                missing: list[Path] = []
                ancestor = parent
                while not ancestor.exists():
                    missing.append(ancestor)
                    ancestor = ancestor.parent
                for directory in reversed(missing):
                    try:
                        directory.mkdir()
                    except FileExistsError:
                        if not directory.is_dir():
                            raise
                    else:
                        cleanup.callback(directory.rmdir)
                with tempfile.TemporaryFile(dir=parent, prefix="basicly-write-preflight-"):
                    pass
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EPERM, errno.EROFS}:
                raise
            raise WriteDeniedError(
                f"Cannot write planned destination {target}: {exc.strerror}. "
                "Stopped before changing the repository. For an already-authorized operation, "
                f"retry `{command}` in this checkout through task-scoped execution review. "
                "The standalone CLI cannot request sandbox escalation itself; "
                "an agent must request the reviewed execution, or run the command "
                "from a terminal with access to this destination."
            ) from exc
        checked.add(parent)
