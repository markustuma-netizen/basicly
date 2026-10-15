from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import yaml

from . import lefthook_binary, lefthook_config
from .catalog import bundled_catalog_root, iter_catalog_files
from .lefthook_config import INTERPRETER as HOOK_PYTHON
from .lefthook_config import HookSpec
from .legacy_hooks import foreign_git_hook, parse_config
from .projection import SyncResult, atomic_write_text, sync_file, write_if_changed
from .schema import ValidationError, technology_selected

HOOKS_MANIFEST = "hooks.yaml"
PRECOMMIT_CONFIG = ".pre-commit-config.yaml"

GIT_MANAGER = "git"
CLAUDE_MANAGER = "claude"
COPILOT_MANAGER = "copilot"
CODEX_MANAGER = "codex"
HOOK_MANAGERS = (GIT_MANAGER, CLAUDE_MANAGER, CODEX_MANAGER, COPILOT_MANAGER)

AGENT_HOOK_HOSTS = {CLAUDE_MANAGER: "claude", CODEX_MANAGER: "codex", COPILOT_MANAGER: "copilot"}

COPILOT_HOOKS_DIR = Path(".github/hooks")
COPILOT_MANAGED_PREFIX = "basicly-"

COPILOT_EVENTS = {
    "posttooluse": "postToolUse",
    "pretooluse": "preToolUse",
    "sessionstart": "sessionStart",
}


def _catalog_hooks_dir() -> Path:
    return bundled_catalog_root() / "hooks"


def load_hook_specs(hooks_dir: Path | None = None) -> list[HookSpec]:
    hooks_dir = hooks_dir or _catalog_hooks_dir()
    manifest = hooks_dir / HOOKS_MANIFEST
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    entries = data.get("hooks")
    if not isinstance(entries, list):
        raise ValueError(f"{manifest}: 'hooks' must be a list")

    specs: list[HookSpec] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{manifest}: each hook must be a mapping")
        missing = [key for key in ("id", "script", "stage") if key not in entry]
        if missing:
            raise ValueError(f"{manifest}: hook entry is missing {', '.join(missing)}")
        manager = str(entry.get("manager", GIT_MANAGER))
        if manager not in HOOK_MANAGERS:
            raise ValueError(
                f"{manifest}: hook '{entry['id']}' has unknown manager {manager!r}; "
                f"allowed: {list(HOOK_MANAGERS)}"
            )
        technologies = entry.get("technologies") or []
        if not isinstance(technologies, list) or not all(
            isinstance(item, str) for item in technologies
        ):
            raise ValueError(
                f"{manifest}: hook '{entry['id']}' technologies must be a list of strings"
            )
        specs.append(
            HookSpec(
                id=str(entry["id"]),
                script=str(entry["script"]),
                stage=str(entry["stage"]),
                pass_filenames=bool(entry.get("pass_filenames", False)),
                always_run=bool(entry.get("always_run", False)),
                files=str(entry.get("files", "")),
                manager=manager,
                technologies=tuple(technologies),
                matcher=str(entry.get("matcher", "")),
            )
        )
    return specs


def selected_hook_specs(specs: list[HookSpec], selection: frozenset[str] | None) -> list[HookSpec]:
    return [spec for spec in specs if technology_selected(spec.technologies, selection)]


def git_hook_specs(specs: list[HookSpec]) -> list[HookSpec]:
    return [spec for spec in specs if spec.manager == GIT_MANAGER]


def claude_hook_specs(specs: list[HookSpec]) -> list[HookSpec]:
    return [spec for spec in specs if spec.manager == CLAUDE_MANAGER]


def copilot_hook_specs(specs: list[HookSpec]) -> list[HookSpec]:
    return [spec for spec in specs if spec.manager == COPILOT_MANAGER]


def agent_hook_surface_present(
    manager: str, *, which: Callable[[str], str | None] | None = None
) -> bool:

    which = which or shutil.which
    host = AGENT_HOOK_HOSTS.get(manager)
    return host is not None and which(host) is not None


def _copilot_hook_path(repo_root: Path, spec: HookSpec) -> Path:
    return repo_root / COPILOT_HOOKS_DIR / f"{COPILOT_MANAGED_PREFIX}{spec.id}.json"


def render_copilot_hook(spec: HookSpec, hooks_relpath: str) -> str:
    event = COPILOT_EVENTS.get(spec.stage)
    if event is None:
        raise ValueError(
            f"copilot hook '{spec.id}' has stage {spec.stage!r}; allowed: {sorted(COPILOT_EVENTS)}"
        )
    script = f"{hooks_relpath}/lefthook-runtime.py"
    entry: dict = {
        "type": "command",
        "bash": f"{HOOK_PYTHON} {shlex.quote(script)} copilot {spec.stage}",
        "powershell": f"{HOOK_PYTHON} '{script}' copilot {spec.stage}",
    }
    return json.dumps({"version": 1, "hooks": {event: [entry]}}, indent=2) + "\n"


def sync_copilot_hooks(
    repo_root: Path, core_hooks_dir: Path, selection: frozenset[str] | None = None
) -> SyncResult:
    selected = selected_hook_specs(copilot_hook_specs(load_hook_specs()), selection)
    specs = list({spec.stage: spec for spec in selected}.values())
    result = SyncResult()
    hooks_relpath = core_hooks_dir.as_posix()

    wanted = {_copilot_hook_path(repo_root, spec) for spec in specs}
    for spec in specs:
        rendered = render_copilot_hook(spec, hooks_relpath)
        sync_file(_copilot_hook_path(repo_root, spec), rendered.encode("utf-8"), result)

    hooks_dir = repo_root / COPILOT_HOOKS_DIR
    if hooks_dir.is_dir():
        for path in sorted(hooks_dir.glob(f"{COPILOT_MANAGED_PREFIX}*.json")):
            if path not in wanted:
                path.unlink()
                result.written.append(path)
    return result


def check_copilot_hooks(
    repo_root: Path, core_hooks_dir: Path, selection: frozenset[str] | None = None
) -> list[tuple[Path, str]]:
    selected = selected_hook_specs(copilot_hook_specs(load_hook_specs()), selection)
    specs = list({spec.stage: spec for spec in selected}.values())
    hooks_relpath = core_hooks_dir.as_posix()
    mismatches: list[tuple[Path, str]] = []

    wanted = {}
    for spec in specs:
        path = _copilot_hook_path(repo_root, spec)
        wanted[path] = render_copilot_hook(spec, hooks_relpath).encode("utf-8")
        if not path.exists():
            mismatches.append((path, "missing"))
        elif path.read_bytes() != wanted[path]:
            mismatches.append((path, "content mismatch"))

    hooks_dir = repo_root / COPILOT_HOOKS_DIR
    if hooks_dir.is_dir():
        mismatches.extend(
            (path, "not in the catalog (stale managed hook file)")
            for path in sorted(hooks_dir.glob(f"{COPILOT_MANAGED_PREFIX}*.json"))
            if path not in wanted
        )
    return mismatches


def remove_copilot_hooks(repo_root: Path) -> int:
    hooks_dir = repo_root / COPILOT_HOOKS_DIR
    if not hooks_dir.is_dir():
        return 0
    removed = 0
    for path in sorted(hooks_dir.glob(f"{COPILOT_MANAGED_PREFIX}*.json")):
        path.unlink()
        removed += 1
    if not any(hooks_dir.iterdir()):
        hooks_dir.rmdir()
    return removed


def sync_hooks(
    repo_root: Path, core_hooks_dir: Path, selection: frozenset[str] | None = None
) -> SyncResult:
    dst = repo_root / core_hooks_dir
    if not dst.is_dir():
        raise ValidationError("core hooks are not materialized; run `basicly install` first", dst)
    specs = selected_hook_specs(load_hook_specs(), selection)
    legacy = repo_root / PRECOMMIT_CONFIG
    secret_exclude = None
    if legacy.exists():
        parsed = parse_config(legacy, legacy.read_text(encoding="utf-8"))
        secret_exclude = parsed.get("exclude", "")
        ids = {spec.id for spec in load_hook_specs()}
        for block in parsed.get("repos", []):
            if block.get("repo") != "local" or any(
                hook.get("id") not in ids
                and f"{core_hooks_dir.as_posix()}/" not in hook.get("entry", "")
                for hook in block.get("hooks", [])
            ):
                raise ValueError(
                    "foreign pre-commit hooks need migration to lefthook.yml; "
                    "move those checks first, then run `basicly hooks-build`; no hooks changed"
                )
    result = lefthook_config.sync_config(
        repo_root, specs, core_hooks_dir.as_posix(), secret_exclude
    )
    binary = lefthook_binary.sync_binary(repo_root)
    result.written.extend(binary.written)
    result.unchanged.extend(binary.unchanged)
    if legacy.exists():
        legacy.unlink()
        result.written.append(legacy)
    return result


def hook_stages(specs: list[HookSpec]) -> list[str]:

    stages: list[str] = []
    for spec in git_hook_specs(specs):
        if spec.stage not in stages:
            stages.append(spec.stage)
    return stages


def _git_hooks_dir(repo_root: Path) -> Path:

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],  # noqa: S607 — PATH git, see above
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return repo_root / ".git" / "hooks"
    rel = result.stdout.strip()
    if not rel:
        return repo_root / ".git" / "hooks"
    path = Path(rel)
    return path if path.is_absolute() else repo_root / path


HOOK_MARKER = lefthook_config.GIT_HOOK_MARKER


def missing_hook_installations(repo_root: Path, stages: list[str]) -> list[str]:
    directory = _git_hooks_dir(repo_root)
    return [
        stage
        for stage in stages
        if not (directory / stage).is_file()
        or HOOK_MARKER.encode() not in (directory / stage).read_bytes()
    ]


def _hook_installation_plan(
    repo_root: Path, stages: list[str], core_hooks_dir: Path
) -> list[tuple[Path, bytes, int]]:
    directory = _git_hooks_dir(repo_root)
    writes: list[tuple[Path, bytes, int]] = []
    for stage in stages:
        path = directory / stage
        saved = directory / f"{stage}.basicly-original"
        foreign = directory / f"{stage}.basicly-foreign"
        current = path.read_bytes() if path.exists() else b""
        managed = HOOK_MARKER.encode() in current
        if path.exists() and saved.exists() and not managed:
            raise ValueError(f"{saved.name} already exists; resolve the hook conflict first")
        if foreign.exists() and not managed:
            raise ValueError(f"{foreign.name} already exists; resolve the hook conflict first")
        saved_body = saved.read_bytes() if saved.exists() else current if not managed else b""
        cleaned = foreign_git_hook(stage, saved_body)
        mode = (
            saved.stat().st_mode
            if saved.exists()
            else path.stat().st_mode
            if path.exists()
            else 0o755
        )
        if current and not managed:
            writes.append((saved, current, mode))
        if cleaned != saved_body and b"# File generated by pre-commit" not in saved_body:
            writes.append((foreign, cleaned, mode))
        launcher_mode = (path.stat().st_mode if path.exists() else 0o644) | 0o111
        writes.append((
            path,
            lefthook_config.render_git_launcher(stage, core_hooks_dir, saved_body),
            launcher_mode,
        ))
    return [
        (path, content, mode)
        for path, content, mode in writes
        if not path.is_file()
        or path.read_bytes() != content
        or (os.name != "nt" and (path.stat().st_mode & 0o777) != (mode & 0o777))
    ]


def hook_installation_writes(
    repo_root: Path, stages: list[str], core_hooks_dir: Path
) -> list[Path]:
    if not (repo_root / ".git").exists():
        return []
    return [
        path for path, _content, _mode in _hook_installation_plan(repo_root, stages, core_hooks_dir)
    ]


def install_hooks(
    repo_root: Path, stages: list[str], core_hooks_dir: Path = Path(".basicly/core/hooks")
) -> tuple[bool, str]:
    if not (repo_root / ".git").exists():
        return False, "not a git repository; run `git init`, then `basicly hooks-build`"
    if shutil.which("uv") is None:
        return False, "uv is not on PATH; install uv, then `basicly hooks-build`"
    try:
        writes = _hook_installation_plan(repo_root, stages, core_hooks_dir)
    except ValueError as exc:
        return False, str(exc)
    lefthook_binary.sync_binary(repo_root)
    for path, content, mode in writes:
        write_if_changed(path, content)
        path.chmod(mode & 0o777)
    return True, "repository-local Lefthook hooks installed"


def uninstall_hooks(repo_root: Path, stages: list[str]) -> tuple[bool, str]:
    directory = _git_hooks_dir(repo_root)
    for stage in stages:
        path = directory / stage
        if not path.exists() or HOOK_MARKER.encode() not in path.read_bytes():
            continue
        saved = directory / f"{stage}.basicly-original"
        if saved.exists():
            content = saved.read_bytes()
            legacy = directory / f"{stage}.legacy"
            if b"# File generated by pre-commit" in content:
                if legacy.exists():
                    shutil.copy2(legacy, path)
                    legacy.unlink()
                else:
                    path.unlink()
            else:
                shutil.copy2(saved, path)
                foreign_body = foreign_git_hook(stage, content)
                if foreign_body != content:
                    path.write_bytes(foreign_body)
            saved.unlink()
        else:
            path.unlink()
        foreign = directory / f"{stage}.basicly-foreign"
        if foreign.exists():
            foreign.unlink()
    return True, "managed Lefthook hooks uninstalled"


def remove_managed_hooks(repo_root: Path) -> str | None:
    config = repo_root / lefthook_config.CONFIG
    if not config.exists():
        return None
    parsed = yaml.safe_load(config.read_text(encoding="utf-8"))
    extends = parsed.get("extends", [])
    if lefthook_config.MANAGED_CONFIG not in extends:
        return None
    parsed["extends"] = [path for path in extends if path != lefthook_config.MANAGED_CONFIG]
    if not parsed["extends"]:
        parsed.pop("extends")
    if parsed:
        atomic_write_text(config, yaml.safe_dump(parsed, sort_keys=False))
    else:
        config.unlink()
    managed = repo_root / lefthook_config.MANAGED_CONFIG
    if managed.exists():
        managed.unlink()
    uninstall_hooks(repo_root, hook_stages(load_hook_specs()))
    return "Removed managed Lefthook configuration (foreign hooks preserved)"


def _tracked_identity(path: Path, cwd: Path) -> tuple[Path, str] | None:

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir", "--show-toplevel"],  # noqa: S607 — as above
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if proc.returncode != 0 or len(lines) != 2:
        return None
    common = Path(lines[0])
    if not common.is_absolute():
        common = cwd / common
    try:
        relative = path.resolve().relative_to(Path(lines[1]).resolve())
    except ValueError:
        return None
    return common.resolve(), relative.as_posix()


def _is_same_tracked_path(src: Path, dst: Path, repo_root: Path) -> bool:

    if src.resolve() == dst.resolve():
        return True
    catalog = _tracked_identity(src, src if src.is_dir() else src.parent)
    return catalog is not None and catalog == _tracked_identity(dst, repo_root)


def check_hooks(
    repo_root: Path, core_hooks_dir: Path, selection: frozenset[str] | None = None
) -> list[tuple[Path, str]]:
    mismatches: list[tuple[Path, str]] = []
    src = _catalog_hooks_dir()
    dst = repo_root / core_hooks_dir

    if not _is_same_tracked_path(src, dst, repo_root):
        for path in iter_catalog_files(src):
            target = dst / path.relative_to(src)
            if not target.exists():
                mismatches.append((target, "missing"))
            elif target.read_bytes() != path.read_bytes():
                mismatches.append((target, "differs from catalog"))

    specs = selected_hook_specs(load_hook_specs(src), selection)
    mismatches.extend(
        lefthook_config.config_mismatches(repo_root, specs, core_hooks_dir.as_posix())
    )
    mismatches.extend(lefthook_binary.binary_mismatches(repo_root))
    return mismatches
