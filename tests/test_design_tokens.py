from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

from basicly import board_render, board_serve
from tests.test_board_render import render

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_design_tokens.py"
TRACKER = REPO_ROOT / ".basicly" / "core" / "kit" / "board" / "web" / "index.html"

TEXT_ROLES = ("--text", "--text-strong", "--muted", "--accent", "--warn", "--bad", "--held", "--ok")
SURFACES = ("--bg", "--panel", "--raise", "--sunk")
CONTROL_EDGES = ("--edge", "--accent")
FILLS = (
    ("--on-accent", "--accent-fill"),
    ("--on-accent", "--accent-fill-hover"),
    ("--on-accent", "--bad-fill"),
    ("--on-fill", "--warn-fill"),
    ("--on-fill", "--stuck-fill"),
)
TEXT_MINIMUM = 4.5
CONTROL_MINIMUM = 3.0


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_design_tokens")
TOKENS = gate.parse_tokens((REPO_ROOT / gate.TOKENS).read_text(encoding="utf-8"))


def _luminance(colour: str) -> float:

    def channel(value: int) -> float:
        scaled = value / 255
        return scaled / 12.92 if scaled <= 0.04045 else ((scaled + 0.055) / 1.055) ** 2.4

    red, green, blue = (int(colour[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def _ratio(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _failing_pairs(palette: dict[str, str]) -> list[str]:

    pairs = [(role, surface, TEXT_MINIMUM) for role in TEXT_ROLES for surface in SURFACES]
    pairs += [(edge, surface, CONTROL_MINIMUM) for edge in CONTROL_EDGES for surface in SURFACES]
    pairs += [(text, fill, TEXT_MINIMUM) for text, fill in FILLS]
    return [
        f"{front} on {back}: {_ratio(palette[front], palette[back]):.2f} < {minimum}"
        for front, back, minimum in pairs
        if _ratio(palette[front], palette[back]) < minimum
    ]


def test_tracker_and_board_read_one_token_file() -> None:

    tracker = TRACKER.read_text(encoding="utf-8")
    board = render("wall-v1.json")
    tokens = (REPO_ROOT / gate.TOKENS).read_text(encoding="utf-8")

    assert tracker.count('<link rel="stylesheet" href="tokens.css">') == 1
    assert ":root" not in tracker
    assert board.count(tokens) == 1
    assert board_render.tokens_path() == REPO_ROOT / gate.TOKENS


def test_the_dark_theme_passes_wcag_aa_contrast() -> None:

    assert TOKENS.dark["--bg"] != TOKENS.light["--bg"]
    assert _failing_pairs(TOKENS.dark) == []


def test_the_light_theme_passes_wcag_aa_contrast() -> None:

    assert _failing_pairs(TOKENS.light) == []


def test_the_contrast_check_fails_a_pair_below_the_minimum() -> None:

    weak = {**TOKENS.dark, "--muted": "#3a3f4d"}

    assert [line for line in _failing_pairs(weak) if line.startswith("--muted")] != []


def test_the_gate_refuses_a_style_value_that_is_not_a_token() -> None:

    page = (
        "<style>\n"
        "  .a { color: #fff; border: 1px solid white; }\n"
        "  .b { background: var(--nope); --mine: #f00; }\n"
        "</style>\n"
    )
    found = list(gate.declarations("page.html", page))

    details = [finding.detail for finding in gate.colour_findings(found, TOKENS)]

    assert len(details) == 5, details
    assert any("#f00" in detail for detail in details)
    assert any("#fff" in detail for detail in details)
    assert any("var(--nope)" in detail for detail in details)
    assert any("--mine" in detail for detail in details)
    assert gate.collect(REPO_ROOT, gate.load_ratchet(REPO_ROOT)) == []


def test_a_spacing_literal_above_the_page_ceiling_is_refused() -> None:

    found = list(gate.declarations("page.html", "<style>.a { padding: 13px; gap: 7px; }</style>"))
    ratchet = gate.Ratchet(frozen={"page.html": 1}, count=1)

    (finding,) = gate.scale_findings(found, ratchet)

    assert "above the ceiling 1" in finding.detail
    assert "13px" in finding.detail


def test_a_spacing_count_below_the_ceiling_must_be_banked() -> None:

    found = list(gate.declarations("page.html", "<style>.a { padding: var(--space-3); }</style>"))
    ratchet = gate.Ratchet(frozen={"page.html": 2}, count=1)

    (finding,) = gate.scale_findings(found, ratchet)

    assert '"page.html" = -2' in finding.remedy


@pytest.mark.parametrize("literal", ["0", "1px", "2px"])
def test_a_border_width_or_zero_is_not_a_scale_literal(literal: str) -> None:

    page = f"<style>.a {{ border: {literal} solid; }}</style>"

    found = list(gate.declarations("page.html", page))

    assert [gate.scale_literals(item) for item in found] == [[]]


def test_a_bridge_triple_that_drifts_from_its_token_is_refused() -> None:

    theme = (REPO_ROOT / gate.THEME).read_text(encoding="utf-8")
    drifted = theme.replace("--bs-primary-rgb: 79, 70, 229", "--bs-primary-rgb: 1, 2, 3")

    assert gate.bridge_findings(theme, TOKENS) == []
    (finding,) = gate.bridge_findings(drifted, TOKENS)
    assert "--bs-primary-rgb" in finding.detail


def test_the_board_server_restamps_when_the_token_file_changes(tmp_path: Path) -> None:

    template = tmp_path / "board_page.html.j2"
    tokens = tmp_path / "tokens.css"
    template.write_text("page", encoding="utf-8")
    tokens.write_text(":root {}", encoding="utf-8")
    old, new = time.time() - 3600, time.time() + 3600
    os.utime(template, (old, old))
    os.utime(tokens, (new, new))

    assert board_serve.newest_template_mtime(tmp_path, tokens) == new


@pytest.mark.parametrize("fixture", ["wall-v1.json"])
def test_the_rendered_board_reads_only_tokens(fixture: str) -> None:

    tokens = (REPO_ROOT / gate.TOKENS).read_text(encoding="utf-8")
    page = render(fixture)
    assert page.count(tokens) == 1

    found = list(gate.declarations("rendered.html", page.replace(tokens, "")))

    assert found, "the rendered page holds no declaration, so the gate read nothing"
    assert gate.colour_findings(found, TOKENS) == []
