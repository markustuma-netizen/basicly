from __future__ import annotations

from pathlib import Path

import pytest

from basicly import gate_source, owned_store, owned_write, tracker
from tests.test_owned_write import events_of, owned_repo, seed


def _report(
    repo: Path,
    surface: str,
    passed: bool,
    *,
    stream: tuple[str, str, str] = ("probe-1", "verify", "basicly-verify"),
) -> bool:
    record, gate, provider = stream
    if surface == "engine":
        return bool(
            owned_write.append(
                repo,
                [
                    "gate",
                    "report",
                    record,
                    "--gate",
                    gate,
                    "--provider",
                    provider,
                    "--status",
                    "pass" if passed else "fail",
                ],
            )[1]
        )
    kit = owned_store.kit(repo)
    ledger = owned_store.ledger_dir(repo)
    draft = owned_store.kit(repo, "gates").gate_draft(
        record, gate, provider=provider, passed=passed
    )
    resolved = owned_store.kit(repo, "recurrence").at_the_generation_this_write_needs(
        kit.events, ledger, [draft]
    )
    return bool(kit.events.append(ledger, resolved))


@pytest.mark.parametrize("surface", ["engine", "standalone-kit"])
@pytest.mark.parametrize("first", [False, True])
def test_a_returning_gate_outcome_becomes_the_latest_observation(
    tmp_path: Path, surface: str, first: bool
) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, "probe-1")
    outcomes = [first, not first, first, first, not first, not first, first]
    appended = []

    for outcome in outcomes:
        appended.append(_report(repo, surface, outcome))
        assert gate_source.read_gates(repo, "probe-1")[-1]["passed"] == outcome

    assert appended == [True, True, True, False, True, False, True]
    gates = [event for event in events_of(repo, "probe-1") if event.kind == "gate"]
    assert [event.payload["passed"] for event in gates] == [
        first,
        not first,
        first,
        not first,
        first,
    ]
    assert len({event.id for event in gates}) == 5
    assert tracker.scrub_ledger(repo) == 0


@pytest.mark.parametrize("surface", ["engine", "standalone-kit"])
@pytest.mark.parametrize("other", ["record", "gate", "provider", "comment"])
def test_unrelated_observations_do_not_make_a_current_gate_outcome_new(
    tmp_path: Path, surface: str, other: str
) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, "probe-1", "probe-2")
    assert _report(repo, surface, False)
    assert _report(repo, surface, True)
    assert _report(repo, surface, False)
    if other == "comment":
        owned_write.append(repo, ["comments", "add", "probe-1", "Intervening conversation"])
    else:
        changed = {
            "record": ("probe-2", "verify", "basicly-verify"),
            "gate": ("probe-1", "rubric", "basicly-verify"),
            "provider": ("probe-1", "verify", "other-provider"),
        }
        assert _report(repo, surface, True, stream=changed[other])
    before = [event.id for event in events_of(repo, "probe-1")]

    assert not _report(repo, surface, False)

    assert [event.id for event in events_of(repo, "probe-1")] == before
    results = gate_source.read_gates(repo, "probe-1")
    own = [r for r in results if r["gate"] == "verify" and r["provider"] == "basicly-verify"]
    assert own[-1]["passed"] is False


def test_gate_transitions_in_one_append_keep_the_last_outcome(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, "probe-1")
    kit = owned_store.kit(repo)
    gates = owned_store.kit(repo, "gates")
    ledger = owned_store.ledger_dir(repo)
    drafts = [
        gates.gate_draft("probe-1", "verify", provider="engine", passed=passed)
        for passed in [False, True, False, False, True, False]
    ]

    resolved = owned_store.kit(repo, "recurrence").at_the_generation_this_write_needs(
        kit.events, ledger, drafts
    )
    appended = kit.events.append(ledger, resolved)

    assert [e.payload["passed"] for e in appended] == [False, True, False, True, False]
    assert gate_source.read_gates(repo, "probe-1")[-1]["passed"] is False
    assert tracker.scrub_ledger(repo) == 0


@pytest.mark.parametrize("surface", ["engine", "standalone-kit"])
def test_malformed_historical_gate_tokens_do_not_prevent_a_valid_observation(
    tmp_path: Path, surface: str
) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, "probe-1")
    kit = owned_store.kit(repo)
    kit.events.append(
        owned_store.ledger_dir(repo),
        [
            kit.events.Draft(
                "probe-1", "gate", {"gate": "verify", "provider": ["broken"], "passed": True}
            )
        ],
    )

    assert _report(repo, surface, False)

    assert gate_source.read_gates(repo, "probe-1")[-1]["passed"] is False
