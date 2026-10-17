from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from basicly import verify, verify_cache
from basicly.config import VerifyCheck, VerifyConfig
from basicly.verify_artifact import RUN_ARTIFACT


@pytest.fixture
def checks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def spawn(command, _root, **_kw):
        calls.append(command[0])
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(verify, "_spawn", spawn)
    monkeypatch.setattr(verify.pyright_inputs, "fingerprint", lambda _root: "a" * 64)
    return calls


def _config() -> VerifyConfig:
    return VerifyConfig(
        (
            VerifyCheck(
                "types",
                ("pyright", "--pythonplatform", "Linux"),
                frozenset({"full"}),
                reuse="pyright",
            ),
            VerifyCheck("tests", ("pytest",), frozenset({"full"})),
        ),
        cache=True,
    )


def test_enabled_cache_reuses_only_proven_results_with_original_evidence(
    tmp_path: Path, checks: list[str]
) -> None:
    first = verify.run_verify(tmp_path, "full", _config())
    second = verify.run_verify(tmp_path, "full", _config())
    third = verify.run_verify(tmp_path, "full", _config())
    assert checks == ["pyright", "pytest", "pytest", "pytest"]
    assert second.results[0].reused_from == third.results[0].reused_from == first.run_id
    assert third.results[0].source_duration_s == first.results[0].duration_s
    evidence = json.loads((tmp_path / RUN_ARTIFACT).read_text(encoding="utf-8"))
    assert evidence["checks"][0]["command"] == ["pyright", "--pythonplatform", "Linux"]
    assert evidence["checks"][0]["disposition"] == "reused"


def test_revalidated_cache_exports_a_transferable_receipt_with_original_execution_provenance(
    tmp_path: Path, checks: list[str]
) -> None:
    cold = verify.run_verify(tmp_path, "full", _config())
    warm = verify.run_verify(tmp_path, "full", _config(), capture_receipts=True)
    landed = verify.run_verify(tmp_path, "full", replace(_config(), cache=False), reuse=warm)

    assert checks == ["pyright", "pytest", "pytest", "pytest"]
    assert warm.receipts
    assert landed.results[0].reused_from == cold.run_id
    assert landed.results[0].source_duration_s == cold.results[0].duration_s


def test_a_transferable_receipt_with_a_mismatched_source_fingerprint_executes_again(
    tmp_path: Path, checks: list[str]
) -> None:
    cold = verify.run_verify(tmp_path, "full", _config(), capture_receipts=True)
    invalid = replace(
        cold, results=(replace(cold.results[0], input_fingerprint="other"), *cold.results[1:])
    )
    landed = verify.run_verify(tmp_path, "full", replace(_config(), cache=False), reuse=invalid)

    assert checks == ["pyright", "pytest", "pyright", "pytest"]
    assert not landed.results[0].reused_from


def test_fresh_control_does_not_read_or_replace_a_valid_cache(
    tmp_path: Path, checks: list[str]
) -> None:
    first = verify.run_verify(tmp_path, "full", _config())
    fresh = verify.run_verify(tmp_path, "full", replace(_config(), cache=False))
    warm = verify.run_verify(tmp_path, "full", _config())
    assert checks == ["pyright", "pytest", "pyright", "pytest", "pytest"]
    assert not fresh.results[0].reused_from
    assert warm.results[0].reused_from == first.run_id


@pytest.mark.parametrize("change", ["inputs", "command", "recipe", "repository", "config"])
def test_changed_identity_executes_fresh(
    tmp_path: Path, checks: list[str], monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    config = _config()
    verify.run_verify(tmp_path, "full", config)
    if change == "inputs":
        monkeypatch.setattr(verify.pyright_inputs, "fingerprint", lambda _root: "b" * 64)
    elif change == "command":
        config = replace(
            config,
            checks=(replace(config.checks[0], command=("pyright", "--pythonplatform", "Darwin")),),
        )
    elif change == "recipe":
        monkeypatch.setattr(verify_cache, "_identity", lambda _root: "new-engine")
    elif change == "repository":
        other = tmp_path / "other"
        (other / verify_cache.CACHE_PATH).parent.mkdir(parents=True)
        (other / verify_cache.CACHE_PATH).write_bytes(
            (tmp_path / verify_cache.CACHE_PATH).read_bytes()
        )
        tmp_path = other
    else:
        config = replace(config, cache=False)
    report = verify.run_verify(tmp_path, "full", config)
    assert checks.count("pyright") == 2
    assert not report.results[0].reused_from


@pytest.mark.parametrize(
    "bad", ["json", "identity", "checks", "duration", "duplicate", "oversized"]
)
def test_malformed_cache_falls_back_to_real_execution(
    tmp_path: Path, checks: list[str], bad: str
) -> None:
    verify.run_verify(tmp_path, "full", _config())
    path = tmp_path / verify_cache.CACHE_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    if bad == "identity":
        payload["identity"] = "other"
    elif bad == "checks":
        payload["checks"] = [None]
    elif bad == "duration":
        payload["checks"][0]["duration_s"] = float("nan")
    elif bad == "duplicate":
        payload["checks"] *= 2
    path.write_text(
        "broken{"
        if bad == "json"
        else " " * (2 * 1024 * 1024 + 1)
        if bad == "oversized"
        else json.dumps(payload),
        encoding="utf-8",
    )
    report = verify.run_verify(tmp_path, "full", _config())
    assert checks.count("pyright") == 2
    assert report.passed
    assert not report.results[0].reused_from


def test_failed_verification_cannot_populate_cache(
    tmp_path: Path, checks: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(command, _root, **_kw):
        checks.append(command[0])
        return subprocess.CompletedProcess(command, 1, "failed", "")

    monkeypatch.setattr(verify, "_spawn", fail)
    report = verify.run_verify(tmp_path, "full", _config())
    assert not report.passed
    assert not (tmp_path / verify_cache.CACHE_PATH).exists()


def test_mutation_revokes_cached_result_and_keeps_real_failure(
    tmp_path: Path, checks: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    verify.run_verify(tmp_path, "full", _config())
    inputs = iter(("a" * 64, "b" * 64))
    monkeypatch.setattr(verify.pyright_inputs, "fingerprint", lambda _root: next(inputs))

    def fail_types(command, _root, **_kw):
        checks.append(command[0])
        return subprocess.CompletedProcess(command, int(command[0] == "pyright"), "bad", "")

    monkeypatch.setattr(verify, "_spawn", fail_types)
    report = verify.run_verify(tmp_path, "full", _config())
    assert checks == ["pyright", "pytest", "pytest", "pyright"]
    assert report.failures == ("types",)
    assert not report.results[0].reused_from


def test_unsupported_recipe_runs_without_cache(tmp_path: Path, checks: list[str]) -> None:
    config = replace(_config(), checks=(VerifyCheck("other", ("pyright",), frozenset({"full"})),))
    verify.run_verify(tmp_path, "full", config)
    verify.run_verify(tmp_path, "full", config)
    assert checks == ["pyright", "pyright"]
    assert not (tmp_path / verify_cache.CACHE_PATH).exists()


def test_unwritable_cache_preserves_actual_verification_result(
    tmp_path: Path,
    checks: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def denied(_source, _target):
        raise PermissionError("read-only cache")

    monkeypatch.setattr(Path, "replace", denied)
    first = verify.run_verify(tmp_path, "full", _config())
    second = verify.run_verify(tmp_path, "full", _config())
    assert first.passed and second.passed
    assert checks == ["pyright", "pytest", "pyright", "pytest"]
    assert "verification cache not saved" in capsys.readouterr().err


def test_changed_inputs_during_fresh_run_cannot_seed_cache(
    tmp_path: Path, checks: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fingerprints = iter(("a" * 64, "b" * 64))
    monkeypatch.setattr(verify.pyright_inputs, "fingerprint", lambda _root: next(fingerprints))
    assert verify.run_verify(tmp_path, "full", _config()).passed
    assert checks == ["pyright", "pytest"]
    assert not (tmp_path / verify_cache.CACHE_PATH).exists()
