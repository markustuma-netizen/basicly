import subprocess
import tomllib
from pathlib import Path

from basicly import codex_permissions


def test_linked_registration_explicitly_grants_shared_and_private_git_metadata(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "initial",
        ],
        check=True,
    )
    lane = tmp_path / "app.worktrees" / "lane"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-qb", "lane", str(lane)], check=True
    )
    user = tmp_path / "user.toml"
    codex_permissions.sync_permissions(lane, user_config=user)
    settings = tomllib.loads((lane / codex_permissions.CONFIG_PATH).read_text())
    profile = settings["permissions"]["basicly-workspace"]
    assert profile["filesystem"][":workspace_roots"][".git"] == "write"
    roots = {
        (lane / root).resolve() for root, enabled in profile["workspace_roots"].items() if enabled
    }
    assert repo / ".git" in roots
    assert repo / ".git" / "worktrees" / "lane" in roots
    assert repo.with_name("app.worktrees") in roots
    assert all(not Path(root).is_absolute() for root in profile["workspace_roots"])
    assert (
        "workspace_roots" not in tomllib.loads(user.read_text())["permissions"]["basicly-workspace"]
    )
    assert codex_permissions.sync_permissions(lane, user_config=user) == []


def test_explicit_consumer_git_read_protection_is_preserved(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    target = repo / codex_permissions.CONFIG_PATH
    target.parent.mkdir()
    text = '[permissions.basicly-workspace.filesystem.":workspace_roots"]\n".git"="read"\n'
    target.write_text(text)
    user = tmp_path / "user.toml"
    user.write_text(text)
    codex_permissions.sync_permissions(repo, user_config=user)
    for path in (target, user):
        assert (
            tomllib.loads(path.read_text())["permissions"]["basicly-workspace"]["filesystem"][
                ":workspace_roots"
            ][".git"]
            == "read"
        )
