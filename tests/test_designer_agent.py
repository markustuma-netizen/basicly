from __future__ import annotations

from pathlib import Path

from basicly.agents import (
    AgentDefinition,
    compose_body,
    default_agent_roots,
    discover_agents,
    discover_blocks,
)
from basicly.copilot_tools import WRITE_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENTS = ("Terminal", "Web", "Desktop", "Mobile")
OUTPUT_SECTIONS = (
    "**Mocks**",
    "**Commands and keyboard access**",
    "**Migration**",
    "**Open questions**",
    "**Failure modes**",
)


def _designer() -> AgentDefinition:
    roots = default_agent_roots(REPO_ROOT)
    (agent,) = [agent for agent in discover_agents(roots) if agent.slug == "designer"]
    return agent


def _body() -> str:
    return compose_body(_designer(), discover_blocks(default_agent_roots(REPO_ROOT)))


def test_the_designer_agent_is_read_only_at_tier_high() -> None:

    agent = _designer()

    assert agent.tier == "high"
    assert agent.tools == ("Read", "Grep", "Glob")
    assert set(agent.tools).isdisjoint(WRITE_TOOLS)
    assert agent.posture == "Read-only."


def test_the_designer_body_gives_one_section_per_environment() -> None:

    headings = [line for line in _body().splitlines() if line.startswith("## ")]

    assert [heading for heading in headings if heading[3:] in ENVIRONMENTS] == [
        f"## {name}" for name in ENVIRONMENTS
    ]


def test_the_designer_output_contract_names_every_required_section() -> None:

    contract = "\n".join(item.value for item in _designer().slot("output_contract"))

    positions = [contract.find(section) for section in OUTPUT_SECTIONS]

    assert -1 not in positions
    assert positions == sorted(positions)
    assert "answer you recommend" in contract
