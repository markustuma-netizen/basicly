from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from basicly import codex_permissions


def _repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _config(repo: Path) -> dict:
    return tomllib.loads((repo / codex_permissions.CONFIG_PATH).read_text())


def test_shared_policy_and_repository_identity_are_portable_and_idempotent(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "app")
    codex_permissions.sync_permissions(repo)
    before = (repo / codex_permissions.CONFIG_PATH).read_text()
    settings = _config(repo)
    profile = settings["default_permissions"]
    assert profile.startswith("basicly-workspace-")
    assert settings["permissions"][profile] == {"extends": "basicly-workspace"}
    base = settings["permissions"]["basicly-workspace"]
    assert base["extends"] == ":workspace"
    assert "workspace_roots" not in base
    assert "." not in base["filesystem"][":workspace_roots"]
    assert base["filesystem"][":workspace_roots"]["**/.env"] == "deny"
    assert "network" not in base
    assert settings["approvals_reviewer"] == "auto_review"
    assert str(tmp_path) not in before
    assert "auto_review" not in settings
    assert codex_permissions.sync_permissions(repo) == []
    assert codex_permissions.mismatches(repo) == []
    assert (repo / codex_permissions.CONFIG_PATH).read_text() == before


def test_two_repositories_receive_disjoint_local_sibling_grants(tmp_path: Path) -> None:
    first = _repo(tmp_path / "first")
    second = _repo(tmp_path / "second")
    user = tmp_path / "codex" / "config.toml"
    for repo in (first, second):
        codex_permissions.sync_permissions(repo, user_config=user)
    local = tomllib.loads(user.read_text())
    names = [_config(repo)["default_permissions"] for repo in (first, second)]
    assert names[0] != names[1]
    assert local["default_permissions"] == ":workspace"
    for repo, name in zip((first, second), names, strict=True):
        assert local["permissions"][name] == {
            "extends": ":workspace",
            "workspace_roots": {(tmp_path / (repo.name + ".worktrees")).as_posix(): True},
        }
        assert codex_permissions.sync_permissions(repo, user_config=user) == []
        assert codex_permissions.mismatches(repo, user_config=user) == []
    assert set(local) == {"default_permissions", "permissions"}
    assert not (user.parent / "rules").exists()


def test_clone_on_another_machine_keeps_identity_and_resolves_new_root(tmp_path: Path) -> None:
    first = _repo(tmp_path / "first")
    codex_permissions.sync_permissions(first)
    moved = _repo(tmp_path / "another-location")
    (moved / ".codex").mkdir()
    shutil.copy2(first / codex_permissions.CONFIG_PATH, moved / codex_permissions.CONFIG_PATH)
    user = tmp_path / "machine-two" / "config.toml"
    codex_permissions.sync_permissions(moved, user_config=user)
    profile = _config(first)["default_permissions"]
    assert _config(moved)["default_permissions"] == profile
    local = tomllib.loads(user.read_text())
    assert local["permissions"][profile]["workspace_roots"] == {
        (tmp_path / "another-location.worktrees").as_posix(): True,
    }
    assert (first / codex_permissions.CONFIG_PATH).read_bytes() == (
        moved / codex_permissions.CONFIG_PATH
    ).read_bytes()


def test_linked_checkout_reuses_main_profile_identity_before_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "app")
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True
    )
    (repo / "initial.txt").write_text("initial\n")
    subprocess.run(["git", "-C", str(repo), "add", "initial.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    codex_permissions.sync_permissions(repo)
    linked = tmp_path / "lane"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-qb", "lane", str(linked)], check=True
    )
    user = tmp_path / "codex" / "config.toml"
    codex_permissions.sync_permissions(linked, user_config=user)
    profile = _config(repo)["default_permissions"]
    assert _config(linked)["default_permissions"] == profile
    assert tomllib.loads(user.read_text())["permissions"][profile]["workspace_roots"] == {
        (tmp_path / "app.worktrees").as_posix(): True,
    }


@pytest.mark.parametrize(
    "root", [PureWindowsPath("D:/projects/app.worktrees"), PurePosixPath("/projects/app.worktrees")]
)
def test_local_registration_accepts_both_platform_path_forms(
    root: PureWindowsPath | PurePosixPath,
) -> None:
    profile = "basicly-workspace-" + "a" * 32
    rendered = codex_permissions.merge_settings(
        "", codex_permissions.sibling_settings(root, profile)
    )
    assert tomllib.loads(rendered)["permissions"][profile]["workspace_roots"] == {
        root.as_posix(): True
    }


def test_explicit_profile_reviewer_and_unrelated_network_settings_are_preserved(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "app")
    target = repo / codex_permissions.CONFIG_PATH
    target.parent.mkdir()
    before = (
        'default_permissions = ":read-only"\napproval_policy = "never"\n'
        'approvals_reviewer = "user"\n[features]\nnetwork_proxy = false\n'
    )
    target.write_text(before)
    user = tmp_path / "user.toml"
    user_before = (
        'default_permissions = ":read-only"\nmodel = "consumer-model"\n'
        'approvals_reviewer = "user"\n'
    )
    user.write_text(user_before)
    codex_permissions.sync_permissions(repo, user_config=user)
    settings = _config(repo)
    assert settings["default_permissions"] == ":read-only"
    assert settings["approval_policy"] == "never"
    assert settings["approvals_reviewer"] == "user"
    assert settings["features"]["network_proxy"] is False
    local = tomllib.loads(user.read_text())
    assert local["default_permissions"] == ":read-only"
    assert local["model"] == "consumer-model"
    assert local["approvals_reviewer"] == "user"
    backups = list((tmp_path / "permission-backups").glob("*.toml"))
    assert len(backups) == 1
    assert backups[0].read_text() == user_before
    assert target.read_text().startswith(before)


def test_conflicting_policy_is_refused_before_project_or_user_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "app")
    target = repo / codex_permissions.CONFIG_PATH
    target.parent.mkdir()
    text = '[permissions.basicly-workspace]\nextends = ":read-only"\n'
    target.write_text(text)
    user = tmp_path / "user.toml"
    with pytest.raises(ValueError, match="conflicts"):
        codex_permissions.sync_permissions(repo, user_config=user)
    assert target.read_text() == text
    assert not user.exists()
    assert not (tmp_path / "app.worktrees").exists()


def test_legacy_user_sandbox_is_refused_before_shared_config_is_written(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "app")
    user = tmp_path / "user.toml"
    text = 'sandbox_mode = "read-only"\n'
    user.write_text(text)
    with pytest.raises(ValueError, match="Legacy"):
        codex_permissions.sync_permissions(repo, user_config=user)
    assert user.read_text() == text
    assert not (repo / codex_permissions.CONFIG_PATH).exists()


def test_cli_registration_honors_codex_home_and_checks_missing_local_roots(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "app")
    source = Path(__file__).resolve().parents[1] / "src"
    home = tmp_path / "custom-codex-home"
    env = {**os.environ, "CODEX_HOME": str(home), "PYTHONPATH": str(source)}
    command = [sys.executable, "-m", "basicly.cli"]
    first = subprocess.run(
        [*command, "permissions-build"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    assert not home.exists()
    absent = subprocess.run(
        [*command, "permissions-check", "--codex-user"],
        check=False,
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert absent.returncode == 1
    assert not home.exists()
    registered = subprocess.run(
        [*command, "permissions-build", "--codex-user"],
        check=False,
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert registered.returncode == 0, registered.stderr
    checked = subprocess.run(
        [*command, "permissions-check", "--codex-user"],
        check=False,
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stderr
    local = tomllib.loads((home / "config.toml").read_text())
    profile = _config(repo)["default_permissions"]
    assert local["permissions"][profile]["workspace_roots"] == {
        (tmp_path / "app.worktrees").as_posix(): True
    }
    assert local["default_permissions"] == ":workspace"


def test_install_registers_only_with_explicit_user_flag_and_replays_without_changes(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "consumer")
    source = Path(__file__).resolve().parents[1] / "src"
    home = tmp_path / "local-codex"
    env = {**os.environ, "CODEX_HOME": str(home), "PYTHONPATH": str(source)}
    command = [sys.executable, "-m", "basicly.cli"]
    installed = subprocess.run(
        [*command, "install"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert installed.returncode == 0, installed.stderr
    assert not home.exists()
    configured = subprocess.run(
        [*command, "install", "--codex-user"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert configured.returncode == 0, configured.stderr
    project = repo / codex_permissions.CONFIG_PATH
    user = home / "config.toml"
    before_project, before_user = project.read_bytes(), user.read_bytes()
    profile = tomllib.loads(before_project.decode())["default_permissions"]
    assert tomllib.loads(before_user.decode())["permissions"][profile]["workspace_roots"] == {
        (tmp_path / "consumer.worktrees").as_posix(): True,
    }
    repeated = subprocess.run(
        [*command, "install", "--codex-user"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert project.read_bytes() == before_project
    assert user.read_bytes() == before_user
    assert not list((home / "permission-backups").glob("*.toml"))
