from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from pathlib import Path
from typing import Any

from .catalog import bundled_catalog_root

_STATE: ContextVar[Any] = ContextVar("affected_snapshot", default=None)


@cache
def _module(name: str) -> Any:
    source = bundled_catalog_root() / "hooks" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"basicly_affected_{name}", source)
    if spec is None or spec.loader is None:
        raise ValueError("affected verification runtime is missing; reinstall basicly")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_provider(section: object, names: list[str]) -> dict[str, Any] | None:
    return _module("affected_checks").parse_provider(section, names)


def environment(root: Path) -> dict[str, str]:
    state = _STATE.get()
    return state.env if state is not None and state.tree == root else dict(os.environ)


def active(root: Path) -> bool:
    state = _STATE.get()
    return state is not None and state.tree == root


def select(root: Path, provider: dict[str, Any] | None, names: list[str]) -> dict[str, Any]:
    env = environment(root)
    if provider is None:
        return {
            "selected": names,
            "fallback": "no verify.affected provider configured; broad verification retained",
            "invalid_snapshot": False,
        }
    return _module("affected_checks").select(
        root, provider, names, snapshot_tree=env.get("BASICLY_SNAPSHOT_TREE"), env=env
    )


@contextmanager
def snapshot(root: Path) -> Iterator[Path]:
    if _STATE.get() is not None or os.environ.get("BASICLY_SNAPSHOT_TREE"):
        raise ValueError("already inside a staged snapshot; configure check commands directly")
    with tempfile.TemporaryDirectory(prefix="basicly-affected-") as directory:
        state = _module("staged-snapshot").prepare(root, Path(directory))
        token = _STATE.set(state)
        try:
            yield state.tree
        finally:
            _STATE.reset(token)
