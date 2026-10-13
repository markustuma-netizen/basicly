from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / ".basicly" / "core" / "hooks" / "tracker-commit-msg.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _shard(path: Path, *records: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"record": r}) + "\n" for r in records), encoding="utf-8")


def _repo(tmp_path: Path) -> Path:

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _shard(repo / ".basicly" / "ledger" / "pending-main.jsonl", "acme-aa11")
    _git(repo, "add", ".basicly/ledger/pending-main.jsonl")
    return repo


def _run(repo: Path, message: str) -> subprocess.CompletedProcess[str]:

    note = repo / "MSG"
    note.write_text(message, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(HOOK), str(note)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def test_an_id_only_in_an_unstaged_shard_is_refused_by_file(tmp_path: Path) -> None:

    repo = _repo(tmp_path)
    _shard(repo / ".basicly" / "ledger" / "pending-agents.jsonl", "acme-bb22")

    result = _run(repo, "feat(x): add a thing (acme-bb22)")

    assert result.returncode == 1
    assert (
        "acme-bb22 is in .basicly/ledger/pending-agents.jsonl, which is not staged" in result.stderr
    )
    assert "git add .basicly/ledger/pending-agents.jsonl" in result.stderr


def test_a_staged_shard_passes(tmp_path: Path) -> None:

    repo = _repo(tmp_path)
    _shard(repo / ".basicly" / "ledger" / "pending-agents.jsonl", "acme-bb22")
    _git(repo, "add", ".basicly/ledger/pending-agents.jsonl")

    result = _run(repo, "feat(x): add a thing (acme-bb22)")

    assert result.returncode == 0, result.stderr


def test_a_redirected_ledger_is_read_from_its_files(tmp_path: Path) -> None:

    other = tmp_path / "other"
    _shard(other / ".basicly" / "ledger" / "events-0001.jsonl", "acme-cc33")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".basicly" / "ledger").mkdir(parents=True)
    (repo / ".basicly" / "ledger" / "redirect").write_text(str(other), encoding="utf-8")

    result = _run(repo, "feat(x): add a thing (acme-cc33)")

    assert result.returncode == 0, result.stderr
