from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import verify, verify_cache
from basicly.config import VerifyCheck, VerifyConfig, load_verify_config
from basicly.verify_artifact import RUN_ARTIFACT

ROOT = Path(__file__).resolve().parents[1]


def _config(root: Path, setting: str, code: int) -> None:
    checks = [
        ("first", [sys.executable, "-c", f"raise SystemExit({code})"]),
        ("later", [sys.executable, "-c", "from pathlib import Path; Path('later').touch()"]),
    ]
    (root / "basicly.toml").write_text(
        setting
        + "\n".join(
            f'[[verify.checks]]\nname={json.dumps(name)}\ncommand={json.dumps(command)}\nmodes=["full"]\n'
            for name, command in checks
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("kind", ["engine", "portable"])
@pytest.mark.parametrize(
    "setting", ["", "[verify]\nfail_fast=false\n", "[verify]\nfail_fast=true\n"]
)
@pytest.mark.parametrize("code", [0, 7])
def test_fail_fast_stops_only_after_enabled_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str, setting: str, code: int
) -> None:
    _config(tmp_path, setting, code)
    if kind == "engine":
        report = verify.run_verify(tmp_path, "full")
        assert report.passed is (code == 0)
        recorded = json.loads((tmp_path / RUN_ARTIFACT).read_text(encoding="utf-8"))
    else:
        runner = runpy.run_path(str(ROOT / ".basicly/core/hooks/check_runner.py"))
        assert runner["run_checks"](tmp_path, "full") == (1 if code else 0)
        recorded = None
    blocked = code != 0 and "true" in setting
    assert (tmp_path / "later").exists() is not blocked
    output = capsys.readouterr()
    if blocked:
        assert "later" in output.out and "NOT RUN" in output.out
        if recorded is not None:
            assert recorded["checks"][1]["status"] == "blocked"
            assert recorded["checks"][1]["disposition"] == "not-run"
            assert not recorded["passed"]
    elif kind == "engine":
        assert recorded is not None
        assert [r["name"] for r in recorded["checks"]] == ["first", "later"]
        assert all(r["disposition"] == "executed" for r in recorded["checks"])


@pytest.mark.parametrize("kind", ["engine", "portable"])
@pytest.mark.parametrize("value", ['"true"', "1", "[]", "{}"])
def test_fail_fast_refuses_malformed_values(tmp_path: Path, kind: str, value: str) -> None:
    _config(tmp_path, f"[verify]\nfail_fast={value}\n", 0)
    if kind == "engine":
        with pytest.raises(ValueError, match=r"verify.fail_fast must be true or false"):
            load_verify_config(tmp_path)
    else:
        runner = runpy.run_path(str(ROOT / ".basicly/core/hooks/check_runner.py"))
        with pytest.raises(SystemExit, match=r"verify.fail_fast must be true or false"):
            runner["run_checks"](tmp_path, "full")
    assert not (tmp_path / "later").exists()


@pytest.mark.parametrize("recovered", [False, True])
def test_an_explicit_new_run_reaches_blocked_checks_only_after_failure_recovers(
    tmp_path: Path, recovered: bool
) -> None:
    _config(tmp_path, "[verify]\nfail_fast=true\n", 7)
    report = verify.run_verify(tmp_path, "full")
    assert not report.passed
    assert not (tmp_path / "later").exists()
    _config(tmp_path, "[verify]\nfail_fast=true\n", 0 if recovered else 7)
    retry = verify.run_verify(tmp_path, "full")
    assert retry.passed is recovered
    assert (tmp_path / "later").exists() is recovered
    assert [r.status for r in retry.results] == (
        ["pass", "pass"] if recovered else ["fail", "blocked"]
    )


def test_configuration_changes_do_not_rewrite_the_original_blocked_evidence(tmp_path: Path) -> None:
    _config(tmp_path, "[verify]\nfail_fast=true\n", 7)
    report = verify.run_verify(tmp_path, "full")
    _config(tmp_path, "[verify]\nfail_fast=true\n", 0)
    path = tmp_path / "basicly.toml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text[: text.rindex("[[verify.checks]]")], encoding="utf-8")
    assert not report.passed
    assert [(r.name, r.status) for r in report.results] == [("first", "fail"), ("later", "blocked")]
    recorded = json.loads((tmp_path / RUN_ARTIFACT).read_text())
    assert not recorded["passed"]
    assert recorded["checks"][1]["name"] == "later"
    assert recorded["checks"][1]["status"] == "blocked"


@pytest.mark.parametrize("kind", ["engine", "portable"])
def test_fail_fast_local_override_preserves_continue_all(tmp_path: Path, kind: str) -> None:
    _config(tmp_path, "[verify]\nfail_fast=true\n", 7)
    (tmp_path / "basicly.local.toml").write_text("[verify]\nfail_fast=false\n", encoding="utf-8")
    if kind == "engine":
        assert not load_verify_config(tmp_path).fail_fast
        assert not verify.run_verify(tmp_path, "full").passed
    else:
        runner = runpy.run_path(str(ROOT / ".basicly/core/hooks/check_runner.py"))
        assert runner["run_checks"](tmp_path, "full") == 1
    assert (tmp_path / "later").exists()


@pytest.mark.parametrize("compiler_first", [False, True])
def test_incomplete_failure_does_not_issue_receipts_or_refresh_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compiler_first: bool
) -> None:
    failing = False
    inputs = "a" * 64
    calls: list[str] = []

    def spawn(command, _root, **_kw):
        nonlocal inputs
        calls.append(command[0])
        if failing and command[0] == "early":
            inputs = "b" * 64
            return subprocess.CompletedProcess(command, 7, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(verify, "_spawn", spawn)
    monkeypatch.setattr(verify.pyright_inputs, "fingerprint", lambda _root: inputs)
    early = VerifyCheck("early", ("early",), frozenset({"full"}))
    compiler = VerifyCheck(
        "compiler", ("pyright", "--pythonplatform", "Linux"), frozenset({"full"}), reuse="pyright"
    )
    config = VerifyConfig(
        (compiler, early) if compiler_first else (early, compiler), cache=True, fail_fast=True
    )
    assert verify.run_verify(tmp_path, "full", config, capture_receipts=True).passed
    before = (tmp_path / verify_cache.CACHE_PATH).read_bytes()
    calls.clear()
    failing = True
    report = verify.run_verify(tmp_path, "full", config, capture_receipts=True)
    assert calls == ["early"]
    assert not report.passed and not report.receipts
    assert (tmp_path / verify_cache.CACHE_PATH).read_bytes() == before
    result = next(r for r in report.results if r.name == "compiler")
    assert result.status == "blocked" and not result.reused_from
