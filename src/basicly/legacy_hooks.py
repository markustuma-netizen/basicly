from __future__ import annotations

import shutil
from pathlib import Path

import yaml


def foreign_git_hook(stage: str, content: bytes) -> bytes:
    block = {"post-merge": "compact", "commit-msg": "claim"}.get(stage)
    if block is None:
        return content
    begin = f"# >>> basicly-tracker {block} >>>".encode()
    end = f"# <<< basicly-tracker {block} <<<".encode()
    kept = []
    inside = False
    for line in content.splitlines(keepends=True):
        if line.strip() == begin:
            if inside:
                raise ValueError(f"{stage} has a nested basicly tracker block")
            inside = True
        elif line.strip() == end:
            if not inside:
                raise ValueError(f"{stage} has an unmatched basicly tracker block end")
            inside = False
        elif not inside:
            kept.append(line)
    if inside:
        raise ValueError(f"{stage} has an unterminated basicly tracker block")
    return b"".join(kept)


def parse_config(path: Path, text: str) -> dict:
    value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: not a valid pre-commit config (expected a mapping)")
    return value


def retire_tracker_block(stage: str, saved: Path) -> Path:
    content = saved.read_bytes()
    foreign_body = foreign_git_hook(stage, content)
    if foreign_body == content:
        return saved
    foreign = saved.with_name(f"{stage}.basicly-foreign")
    foreign.write_bytes(foreign_body)
    shutil.copystat(saved, foreign)
    return foreign
