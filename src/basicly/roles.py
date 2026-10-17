from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class HasAgentStyle(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def agent_style(self) -> str | None: ...


ROLE_BY_PHASE: dict[str, str] = {
    "classify": "decider",
    "decompose": "decomposer",
    "build": "implementer",
    "validate": "validator",
    "repair": "implementer",
    "retrospective": "retrospector",
    "ship": "curator",
}


@dataclass(frozen=True)
class RoleDispatch:
    role: str
    lens: str


REVIEW_LENSES: tuple[str, ...] = ("correctness", "security")

LENS_ROLE_BY_PHASE: dict[str, str] = {"validate": "reviewer"}

SUPERSEDED_ROLES: dict[str, str] = {"code-reviewer": "reviewer"}

AGENT_ROOTS: dict[str, tuple[Path, str]] = {
    "claude": (Path(".claude/agents"), ".md"),
    "copilot": (Path(".github/agents"), ".agent.md"),
}


def role_for_phase(phase: str) -> str | None:

    return ROLE_BY_PHASE.get(phase.strip().lower())


def lens_dispatches(phase: str, scope: Sequence[str] = ()) -> tuple[RoleDispatch, ...]:

    role = LENS_ROLE_BY_PHASE.get(phase.strip().lower())
    if role is None:
        return ()
    docs_only = bool(scope) and all(
        (path in {"README.md", "CHANGELOG.md"} or path.startswith(("docs/", "changelog.d/")))
        and path.endswith(".md")
        and not any(marker in path for marker in "*?[]{}")
        and Path(path).name.lower() not in {"agents.md", "claude.md", "skill.md"}
        and not path.lower().endswith("instructions.md")
        for path in scope
    )
    lenses = ("correctness",) if docs_only else REVIEW_LENSES
    return tuple(RoleDispatch(role, lens) for lens in lenses)


def role_is_available(repo_root: Path, family: str, role: str) -> bool:

    entry = AGENT_ROOTS.get(family)
    if entry is None:
        return False
    root, suffix = entry
    return (repo_root / root / f"{role}{suffix}").is_file()


def resolve_role(repo_root: Path, spec: HasAgentStyle, phase: str) -> str | None:

    role = role_for_phase(phase)
    return None if role is None else resolve_named_role(repo_root, spec, role)


def resolve_named_role(repo_root: Path, spec: HasAgentStyle, role: str) -> str | None:

    if spec.agent_style is None:
        return None
    current = SUPERSEDED_ROLES.get(role.strip().lower(), role)
    return current if role_is_available(repo_root, spec.name, current) else None


INHERITING_ROLES: frozenset[str] = frozenset({"implementer"})


def inherits_context(role: str | None) -> bool:

    return role is not None and role.strip().lower() in INHERITING_ROLES


def phase_inherits_context(phase: str) -> bool:

    return inherits_context(role_for_phase(phase))


def portable_role_brief(repo_root: Path, role: str) -> str:
    role = SUPERSEDED_ROLES.get(role.strip().lower(), role)
    for root, suffix in AGENT_ROOTS.values():
        path = repo_root / root / f"{role}{suffix}"
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        parts = text.split("---\n", 2)
        if len(parts) != 3 or parts[0]:
            raise ValueError(f"role {role!r} has invalid frontmatter at {path}")
        return f"<role name={role!r}>\n{parts[2].strip()}\n</role>"
    return ""
