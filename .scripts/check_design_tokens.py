from __future__ import annotations

import re
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from ratchet import (  # noqa: E402 - the path above comes first
    MAY_ONLY_TRACK,
    Finding,
    Ratchet,
    RatchetError,
    compose_ratchet,
    count_delta_remedy,
    fragment,
    report,
)

_GATE = "design_tokens"
_LABEL = "design-tokens"
FROZEN_FRAGMENT = fragment(f"{_GATE}.frozen")
RERUN = "uv run pytest tests/test_design_tokens.py -q"

TOKENS = Path(".basicly/core/kit/board/web/tokens.css")
THEME = Path(".basicly/core/templates/board/board_theme.css.j2")
PAGE_GLOBS = (
    ".basicly/core/kit/board/web/*.html",
    ".basicly/core/kit/board/web/*.css",
    ".basicly/core/templates/board/*.j2",
)

COLOUR_PROPERTIES = frozenset({
    "color",
    "background",
    "background-color",
    "border",
    "border-top",
    "border-right",
    "border-bottom",
    "border-left",
    "border-color",
    "outline",
    "outline-color",
    "fill",
    "stroke",
    "box-shadow",
    "text-decoration-color",
    "caret-color",
    "accent-color",
})
SCALE_PROPERTIES = frozenset({
    "margin",
    "margin-top",
    "margin-right",
    "margin-bottom",
    "margin-left",
    "padding",
    "padding-top",
    "padding-right",
    "padding-bottom",
    "padding-left",
    "gap",
    "row-gap",
    "column-gap",
    "font",
    "font-size",
    "font-weight",
    "line-height",
    "font-family",
    "letter-spacing",
    "border-radius",
    "box-shadow",
    "transition",
    "transition-duration",
    "animation",
    "animation-duration",
})
BORDER_WIDTH_PROPERTIES = ("border", "outline")
BRIDGED_SCALE = ("padding", "font-size", "font-weight", "line-height", "border-radius", "gap")
NAMED_COLOURS = frozenset(
    {"white", "black", "red", "green", "blue", "gray", "grey", "silver", "orange", "yellow"}
    | {"purple", "pink", "navy", "teal", "maroon", "olive", "lime", "aqua", "fuchsia"}
)

_STYLE_BLOCK = re.compile(r"<style>(.*?)</style>", re.DOTALL)
_STYLE_ATTR = re.compile(r'style="([^"]*)"')
_JINJA = re.compile(r"\{#.*?#\}|\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_DECLARATION = re.compile(r"(?<![\w-])(-{0,2}[a-z][a-z0-9-]*)\s*:\s*([^;{}]*)(?=[;}]|$)")
_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_FUNCTION_COLOUR = re.compile(r"\b(?:rgba?|hsla?)\(")
_WORD = re.compile(r"[a-z]+")
_USED = re.compile(r"var\((--[a-z0-9-]+)")
_LENGTH = re.compile(r"(?<![\w.#-])(\d*\.?\d+)(px|rem|em)\b")
_DURATION = re.compile(r"(?<![\w.#-])(\d*\.?\d+)(ms|s)\b")
_BARE_NUMBER = re.compile(r"(?<![\w.#-])(\d*\.?\d+)(?![\w%.])")
_TOKEN_DEFINED = re.compile(r"^\s*(--[a-z0-9-]+)\s*:\s*([^;]+);", re.MULTILINE)
_DARK_BLOCK = re.compile(r"@media \(prefers-color-scheme: dark\)\s*\{(.*?)\n\s*\}\s*\n", re.DOTALL)
_TRIPLE = re.compile(r"^(\d{1,3}),\s*(\d{1,3}),\s*(\d{1,3})$")


@dataclass(frozen=True)
class Declaration:
    path: str
    line: int
    name: str
    value: str


@dataclass(frozen=True)
class Tokens:
    light: dict[str, str]
    dark: dict[str, str]

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self.light) | frozenset(self.dark)


def parse_tokens(text: str) -> Tokens:

    dark_match = _DARK_BLOCK.search(text)
    dark_text = dark_match.group(1) if dark_match else ""
    light_text = text.replace(dark_text, "") if dark_text else text
    light = {name: value.strip() for name, value in _TOKEN_DEFINED.findall(light_text)}
    dark = dict(light)
    dark.update({name: value.strip() for name, value in _TOKEN_DEFINED.findall(dark_text)})
    return Tokens(light=light, dark=dark)


def _blank(match: re.Match[str]) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def css_regions(path: str, text: str) -> Iterator[tuple[int, str]]:

    text = _JINJA.sub(_blank, text)
    if path.endswith(".css") or path.endswith(".css.j2"):
        yield 0, text
        return
    for pattern in (_STYLE_BLOCK, _STYLE_ATTR):
        for match in pattern.finditer(text):
            yield match.start(1), match.group(1)


def declarations(path: str, text: str) -> Iterator[Declaration]:

    for offset, raw in css_regions(path, text):
        region = _CSS_COMMENT.sub(_blank, raw)
        for match in _DECLARATION.finditer(region):
            line = text.count("\n", 0, offset + match.start()) + 1
            yield Declaration(path, line, match.group(1), match.group(2).strip())


def _colour_literals(name: str, value: str) -> list[str]:

    found = _HEX.findall(value) + [m.group(0) for m in _FUNCTION_COLOUR.finditer(value)]
    if name in COLOUR_PROPERTIES or name.startswith("--bs-"):
        found += [word for word in _WORD.findall(value) if word in NAMED_COLOURS]
    return found


def scale_literals(declaration: Declaration) -> list[str]:

    name, value = declaration.name, re.sub(r"var\([^)]*\)", "", declaration.value)
    if name.startswith("--bs-"):
        name = next((part for part in BRIDGED_SCALE if part in name), name)
    if name not in SCALE_PROPERTIES:
        return []
    found: list[str] = []
    for number, unit in _LENGTH.findall(value):
        literal = f"{number}{unit}"
        if float(number) == 0:
            continue
        if name.startswith(BORDER_WIDTH_PROPERTIES) and literal in ("1px", "2px"):
            continue
        found.append(literal)
    if name.startswith(("transition", "animation")):
        found += [f"{number}{unit}" for number, unit in _DURATION.findall(value)]
    if name in ("font-weight", "line-height"):
        found += [number for number in _BARE_NUMBER.findall(value) if float(number) != 0]
    if name == "font-family" and "var(" not in declaration.value:
        found.append(declaration.value)
    return found


def colour_findings(found: Iterable[Declaration], tokens: Tokens) -> list[Finding]:

    findings: list[Finding] = []
    for item in found:
        where = f"{item.path}:{item.line}"
        if item.name.startswith("--") and not item.name.startswith("--bs-"):
            findings.append(
                Finding(
                    where,
                    f"defines {item.name}, a custom property outside {TOKENS}",
                    f"add it to {TOKENS} and read it with var(), then rerun `{RERUN}`",
                )
            )
        findings.extend(
            Finding(
                where,
                f"{item.name} reads var({used}), which {TOKENS} does not define",
                f"use a token from {TOKENS}, then rerun `{RERUN}`",
            )
            for used in _USED.findall(item.value)
            if used not in tokens.names and not used.startswith("--bs-")
        )
        findings.extend(
            Finding(
                where,
                f"{item.name}: {item.value} carries the colour literal {literal}",
                f"use a colour token from {TOKENS}, then rerun `{RERUN}`",
            )
            for literal in _colour_literals(item.name, item.value)
        )
    return findings


def _hex_triple(value: str) -> tuple[int, int, int] | None:

    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return None
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def bridge_findings(theme: str, tokens: Tokens) -> list[Finding]:

    dark_match = _DARK_BLOCK.search(theme)
    blocks = {"light": theme.replace(dark_match.group(1), "") if dark_match else theme}
    blocks["dark"] = dark_match.group(1) if dark_match else ""
    sources = dict(_TOKEN_DEFINED.findall(blocks["light"]))
    findings: list[Finding] = []
    for scheme, block in blocks.items():
        palette = tokens.light if scheme == "light" else tokens.dark
        for name, value in _TOKEN_DEFINED.findall(block):
            triple = _TRIPLE.match(value.strip())
            if not name.endswith("-rgb") or not triple:
                continue
            source = _USED.findall(sources.get(name.removesuffix("-rgb"), ""))
            expected = _hex_triple(palette.get(source[0], "")) if source else None
            actual = tuple(int(part) for part in triple.groups())
            if expected != actual:
                findings.append(
                    Finding(
                        f"{THEME} ({scheme})",
                        f"{name}: {value.strip()} is not the {scheme} value of "
                        f"{source[0] if source else 'any token'} {expected}",
                        f"write {', '.join(map(str, expected)) if expected else 'the triple'} "
                        f"so the bridge mirrors {TOKENS}, then rerun `{RERUN}`",
                    )
                )
    return findings


def scale_findings(found: list[Declaration], ratchet: Ratchet[int]) -> list[Finding]:

    counts = Counter(item.path for item in found for _ in scale_literals(item))
    findings: list[Finding] = []
    for path in sorted(set(counts) | set(ratchet.frozen)):
        count, ceiling = counts.get(path, 0), ratchet.frozen.get(path, 0)
        if count > ceiling:
            sites = [
                f"{item.line} {item.name}: {literal}"
                for item in found
                if item.path == path
                for literal in scale_literals(item)
            ]
            findings.append(
                Finding(
                    path,
                    f"{count} spacing, type, radius or motion literals, above the ceiling "
                    f"{ceiling} (lines: {'; '.join(sites[:5])}...)",
                    f"use a token from {TOKENS}, then rerun `{RERUN}`",
                )
            )
        elif count < ceiling:
            findings.append(
                Finding(
                    path,
                    f"{count} literals, down from the ceiling {ceiling}; the ceiling still "
                    f"licenses {ceiling}",
                    f'record `"{path}" = {count - ceiling:+d}` in {FROZEN_FRAGMENT}',
                )
            )
    return findings


def page_paths(repo: Path) -> list[Path]:

    found = {path for pattern in PAGE_GLOBS for path in repo.glob(pattern)}
    return sorted(path for path in found if path.name != TOKENS.name)


def scan(repo: Path) -> list[Declaration]:

    found: list[Declaration] = []
    for path in page_paths(repo):
        relative = path.relative_to(repo).as_posix()
        found.extend(declarations(relative, path.read_text(encoding="utf-8")))
    return found


def load_ratchet(repo: Path) -> Ratchet[int]:
    return compose_ratchet(
        repo, _GATE, count_key="file_count", entry_type=int, may_only=MAY_ONLY_TRACK
    )


def collect(repo: Path, ratchet: Ratchet[int]) -> list[Finding]:

    tokens = parse_tokens((repo / TOKENS).read_text(encoding="utf-8"))
    found = scan(repo)
    findings = colour_findings(found, tokens)
    findings += bridge_findings((repo / THEME).read_text(encoding="utf-8"), tokens)
    findings += scale_findings(found, ratchet)
    files = len(page_paths(repo))
    if files != ratchet.count:
        findings.append(
            Finding(
                "pyproject.toml",
                f"{files} page files are scanned but file_count is {ratchet.count}",
                count_delta_remedy(_GATE, files - ratchet.count),
            )
        )
    return findings


def main() -> int:
    try:
        ratchet = load_ratchet(REPO_ROOT)
    except RatchetError as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1
    findings = collect(REPO_ROOT, ratchet)
    if findings:
        report(_LABEL, findings)
        return 1
    print(f"{_LABEL}: {ratchet.count} page files read only tokens from {TOKENS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
