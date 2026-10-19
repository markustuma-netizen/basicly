from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CheckReceipt:
    name: str
    command: tuple[str, ...]
    mode: str
    inputs: str
    origin: str


def supported_command(command: tuple[str, ...], mode: str) -> bool:
    return (
        mode == "full"
        and len(command) == 3
        and command[:2] == ("pyright", "--pythonplatform")
        and command[2] in {"Linux", "Darwin", "Windows"}
    )


@dataclass(frozen=True)
class CachedCheck:
    name: str
    command: tuple[str, ...]
    mode: str
    inputs: str
    origin: str
    duration_s: float
