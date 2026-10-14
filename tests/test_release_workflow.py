from __future__ import annotations

from pathlib import Path

import yaml

_RELEASE_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


def _upload_files_glob() -> str:
    workflow = yaml.safe_load(_RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("softprops/action-gh-release"):
                return step["with"]["files"]
    raise AssertionError("no softprops/action-gh-release step found in release.yml")


def test_upload_glob_is_not_a_bare_wildcard() -> None:
    globs = _upload_files_glob().split()
    assert "dist/*" not in globs


def test_upload_glob_names_the_real_artifacts() -> None:
    globs = _upload_files_glob().split()
    assert "dist/*.whl" in globs
    assert "dist/*.tar.gz" in globs


def test_ci_prepares_pinned_artifacts_and_runtime_before_hook_verification() -> None:
    workflows = _RELEASE_WORKFLOW.parent
    for name, job_name in (("release.yml", "verify"), ("quality-gates.yml", "quality-gates")):
        jobs = yaml.safe_load((workflows / name).read_text(encoding="utf-8"))["jobs"]
        commands = [step.get("run", "") for step in jobs[job_name]["steps"]]
        preparation = next(
            (
                index
                for index, value in enumerate(commands)
                if "lefthook_artifacts.py --download" in value
            ),
            None,
        )
        runtime = next(
            (index for index, value in enumerate(commands) if "sync_binary(Path.cwd())" in value),
            None,
        )
        verification = next(
            index for index, value in enumerate(commands) if ".basicly/core/hooks/pre-" in value
        )
        assert preparation is not None and runtime is not None, name
        assert preparation <= runtime < verification, name


def test_release_smoke_installs_the_prepared_wheel_before_publication() -> None:
    jobs = yaml.safe_load(_RELEASE_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    smoke = "\n".join(step.get("run", "") for step in jobs["smoke-install"]["steps"])
    assert "lefthook_artifacts.py --download" in smoke
    assert "uv build" in smoke
    assert 'uvx --from "$wheel" basicly install' in smoke
    assert 'uvx --from "$wheel" basicly hooks-check' in smoke
    assert 'uvx --from "git+' not in smoke
    assert "smoke-install" in jobs["release"]["needs"]
