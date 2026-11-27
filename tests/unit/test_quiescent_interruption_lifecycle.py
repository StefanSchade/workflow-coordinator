"""O10B, WI-WFC-BETA2-O10B-001: `wfc resume` for the proven-quiescent
post-dispatch interruption, and `wfc resume --preview`.

Reproduces, structurally, the stop shape the friction log records (an
invocation interrupted after its dispatch marker existed: one charged
``uncertain_dispatch`` Hop, ``reconcile_required``, the marker still in
place) and asserts the whole contract of the class: it recovers the
identical Run with the interrupted Hop still charged and never replayed, it
retires exactly the already-reconciled predecessor marker, every predicate
element violated one at a time refuses by name and changes nothing, the
preview changes nothing at all, the recovery is visible on
status/report/audit, and no other stop class's behaviour changes.

Uses the same fake-engine fixture pattern as
``test_mechanical_recovery_lifecycle.py``; the per-element violations are
driven by the fixture's own ``WFC_FAKE_ENGINE_INTERRUPTION_*`` variables so
the Run shape stays the one the fixture writes rather than a hand-edited one
wherever that is possible.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest
import yaml

from workflow_coordinator.frontdoor import journal as J
from workflow_coordinator.frontdoor import lifecycle as L
from workflow_coordinator.frontdoor.audit import project_audit
from workflow_coordinator.frontdoor.cli import main as cli_main
from workflow_coordinator.frontdoor.report import build_report
from workflow_coordinator.persistence import write_run_snapshot
from workflow_coordinator.serialization import load_run

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "bp4"
FAKE_PROVIDER = FIXTURES / "fake_provider.py"
FAKE_ENGINE = FIXTURES / "fake_engine.py"

pytestmark = [pytest.mark.ftr("FTR-WFC-ATOMIC-HOP-RECOVERY")]

_INTERRUPTION_ENVIRONMENT = (
    "WFC_FAKE_ENGINE_MODE", "WFC_FAKE_ENGINE_CONTINUE_MODE",
    "WFC_FAKE_ENGINE_RUN_ID", "WFC_FAKE_ENGINE_DIRTY_FILE",
    "WFC_FAKE_ENGINE_INTERRUPTION_HOP_ID", "WFC_FAKE_ENGINE_INTERRUPTION_COMMITS",
    "WFC_FAKE_ENGINE_INTERRUPTION_ANCHOR", "WFC_FAKE_ENGINE_INTERRUPTION_ROLE",
    "WFC_FAKE_ENGINE_INTERRUPTION_ANOMALIES", "WFC_FAKE_ENGINE_INTERRUPTION_NO_MARKER",
    "WFC_FAKE_ENGINE_INTERRUPTION_MARKER_HOP_ID",
    "WFC_FAKE_ENGINE_INTERRUPTION_RECONCILE_REASON",
)


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _restore_engine_command():
    original = L._ENGINE_COMMAND
    yield
    L._ENGINE_COMMAND = original


@pytest.fixture(autouse=True)
def _clear_fake_engine_env():
    yield
    for name in _INTERRUPTION_ENVIRONMENT:
        os.environ.pop(name, None)


@pytest.fixture
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(home))
    return home / "wfc"


@pytest.fixture
def evidence_path(tmp_path: Path) -> Path:
    return tmp_path / "evidence"


@pytest.fixture
def repository(tmp_path: Path, evidence_path: Path) -> Path:
    repo = tmp_path / "consumer-repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", str(repo)), check=True)
    git(repo, "config", "user.email", "consumer@example.invalid")
    git(repo, "config", "user.name", "Consumer test")
    (repo / "GUIDANCE.md").write_text("Keep changes small and reviewed.\n", encoding="utf-8")
    (repo / "checks").mkdir()
    check = repo / "checks" / "check.sh"
    check.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    check.chmod(0o755)
    (repo / "src").mkdir()
    (repo / "src" / "app.txt").write_text("hello\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: seed disposable consumer repository")

    assert cli_main([
        "init", "--repository", str(repo), "--scope-ceiling", "src",
        "--evidence-repository", str(evidence_path),
    ]) == 0
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: commit Consumer Config")
    return repo


def _write_request(repository: Path, path: Path) -> Path:
    head = git(repository, "rev-parse", "HEAD")
    data = {
        "schema_version": 1, "commission_id": "wfc-interruption", "work_item_id": "WI-INTERRUPTION",
        "authority_source": "test authorizes this bounded lifecycle.",
        "program": {"id": "simple", "revision": "v1"},
        "task": {
            "title": "interruption task", "body": "exercise quiescent-interruption recovery",
            "acceptance": "task-check passes", "withheld_authority": "none beyond scope",
            "escalation_default": "stop on ambiguity",
        },
        "scope": ["src"], "personas": {"worker": "Bounded worker persona."},
        "evidence": {"argv": ["sh", "checks/check.sh"], "timeout_seconds": 60},
        "max_autonomous_hops": 3, "durable_artifacts": {},
        "guidance": {"path": "GUIDANCE.md", "source_commit": head},
    }
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


_OVERRIDES = {"worker": {"adapter": "codex_cli", "model": "fixture-model", "reasoning": "medium"}}


def _interrupted_run(repository: Path, tmp_path: Path, **environment: str) -> L.LifecycleResult:
    """Drive the fake engine to the recorded post-dispatch interruption shape."""

    os.environ["WFC_FAKE_ENGINE_MODE"] = "interrupted_reconcile_required"
    for name, value in environment.items():
        os.environ[name] = value
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    request_path = _write_request(repository, tmp_path / "request.yaml")
    result = L.run_lifecycle(L.RunInputs(
        repository=repository, config_path=L.DEFAULT_CONFIG_PATH, request_path=request_path,
        target_dir=None, overrides=_OVERRIDES, executable=str(FAKE_PROVIDER),
        hop_timeout_seconds=60.0, confirm=lambda a, b: True,
    ))
    assert result.classification == "reconcile_required", result.message
    return result


def _checkout_dir_and_journal(repository: Path) -> tuple[Path, J.Journal]:
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    return checkout_dir, J.Journal(checkout_dir / "journal.jsonl")


def _run_json_path(result: L.LifecycleResult) -> Path:
    return Path(result.checkout_state_root) / "run.json"


def _marker_path(result: L.LifecycleResult) -> Path:
    return Path(result.checkout_state_root) / "dispatch.json"


# --------------------------------------------------------------------------
# The admitted class, end to end.
# --------------------------------------------------------------------------

def test_quiescent_interruption_recovers_the_identical_run(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _interrupted_run(repository, tmp_path)
    run_before = load_run(_run_json_path(result))
    assert run_before.reconcile_reason == "interruption"
    assert run_before.hops[-1].role == "uncertain_dispatch"
    assert run_before.hop_used == 1
    assert _marker_path(result).is_file()

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "completed", recovered.message
    assert recovered.run_id == result.run_id  # the identical Run, never a new one.
    assert "quiescent_interruption" in recovered.message

    run_after = load_run(_run_json_path(result))
    # The interrupted Hop stays charged and is never replayed: it is still
    # the uncertain Hop, and the continuation took the *next* sequence.
    assert run_after.hops[0] == run_before.hops[-1]
    assert run_after.hop_used == 2
    assert run_after.hop_limit == run_before.hop_limit

    # The already-reconciled predecessor marker is retired, so the engine
    # cannot re-enter its stale-marker branch.
    assert not _marker_path(result).exists()

    _, journal = _checkout_dir_and_journal(repository)
    intent = journal.latest(("mechanical_recovery_intent",))
    assert intent is not None
    assert intent.data["recovery_class"] == "quiescent_interruption"
    assert intent.data["interrupted_hop_id"] == "H001"
    assert intent.data["anomaly"] is None
    assert intent.data["hop_used_at_resume"] == 1
    retired = journal.latest(("mechanical_recovery_marker_retired",))
    assert retired is not None and retired.data["interrupted_hop_id"] == "H001"
    assert journal.count("classified") == 2


def test_recovery_without_any_remaining_marker_is_still_admitted(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """Nothing to retire is not a refusal: the predicate reasons about the
    charged Hop, and a marker that is already gone carries nothing else."""

    _interrupted_run(repository, tmp_path, WFC_FAKE_ENGINE_INTERRUPTION_NO_MARKER="1")
    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "completed", recovered.message
    _, journal = _checkout_dir_and_journal(repository)
    assert journal.latest(("mechanical_recovery_marker_retired",)) is None


def test_recovery_never_itself_accepts_the_run(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _interrupted_run(repository, tmp_path)
    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "awaiting_human_again"
    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "awaiting_human"
    assert load_run(_run_json_path(result)).stop_reason == "second_fixture_stop"


def test_a_resumed_run_that_spends_its_last_hop_stops_awaiting_human_budget(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """Composition with O8 in the other direction: recovery changes no budget
    rule, so a continuation that spends the ceiling stops on the honest budget
    stop, where `wfc extend-budget` -- and only it -- is the channel."""

    result = _interrupted_run(repository, tmp_path)
    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "budget_exhausted_again"
    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "awaiting_human_budget", recovered.message

    run_after = load_run(_run_json_path(result))
    assert run_after.hop_used == run_after.hop_limit
    assert run_after.stop_reason == "autonomous_hop_budget_exhausted"
    # The interrupted Hop is still charged among them, never refunded.
    assert run_after.hops[0].role == "uncertain_dispatch"

    # `wfc resume` does not answer a budget stop -- neither recovery class
    # admits it -- and says so by no-opping, exactly as before this slice.
    again = L.resume_lifecycle(repository)
    assert again.classification == "awaiting_human_budget"
    assert again.message == "lifecycle already classified (no-op)"

    # The Run is not a dead end: it reaches O8's command, which answers with
    # its own named bound (here the admitted policy ceiling is already met).
    extension = L.extend_budget_lifecycle(
        repository, additional_hops=1,
        authority="Maintainer instruction of this test session: finish the residue.",
        preview=True, out=StringIO(),
    )
    assert extension.classification == "refused_budget_extension"
    assert "policy ceiling" in extension.message
    # Nothing was changed by either command; the charged history stands.
    assert load_run(_run_json_path(result)).hop_used == run_after.hop_used


def test_an_open_blocking_finding_does_not_block_the_recovery(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """The record's Case A. Resume preserves the Finding untouched; only the
    independent reviewer ever disposes of one."""

    from workflow_coordinator.model import Finding, FindingState

    result = _interrupted_run(repository, tmp_path)
    run_path = _run_json_path(result)
    finding = Finding(
        finding_id="F001", invariant="fixture invariant", blocking=True,
        state=FindingState.OPEN, introduced_by_hop="H001", changed_by_hop="H001",
        evidence_refs=(), disposition="open",
    )
    write_run_snapshot(run_path, replace(load_run(run_path), findings=(finding,)))

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "awaiting_human_again"
    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "awaiting_human", recovered.message
    assert load_run(run_path).findings == (finding,)


# --------------------------------------------------------------------------
# `wfc resume --preview`: changes nothing at all.
# --------------------------------------------------------------------------

def test_preview_states_the_decision_and_changes_nothing(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _interrupted_run(repository, tmp_path)
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()
    marker_bytes = _marker_path(result).read_bytes()
    checkout_dir, journal = _checkout_dir_and_journal(repository)
    before_records = len(journal.read_all())

    out = StringIO()
    preview = L.resume_lifecycle(repository, preview=True, out=out)
    assert preview.classification == L.RECOVERY_PREVIEW_CLASSIFICATION
    assert "would proceed for class quiescent_interruption" in preview.message
    text = out.getvalue()
    assert "RECOVERY PREVIEW (nothing was changed)" in text
    assert "recovery class    quiescent_interruption" in text
    assert "quiescent post-dispatch interruption at charged uncertain Hop H001" in text
    assert "retire the already-reconciled predecessor dispatch marker" in text
    assert "1 of 5 (charged; never refunded)" in text
    assert "live worktree     clean" in text

    assert run_path.read_bytes() == before_bytes
    assert _marker_path(result).read_bytes() == marker_bytes
    assert len(journal.read_all()) == before_records
    assert J.try_acquire_lock(checkout_dir) is not None  # no lock was held or left behind.


def test_preview_names_the_element_that_would_refuse(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _interrupted_run(
        repository, tmp_path, WFC_FAKE_ENGINE_INTERRUPTION_ANOMALIES="dirty tracked state",
    )
    out = StringIO()
    preview = L.resume_lifecycle(repository, preview=True, out=out)
    assert preview.classification == L.RECOVERY_PREVIEW_CLASSIFICATION
    assert "would refuse" in preview.message
    text = out.getvalue()
    assert "would refuse      the interrupted invocation left recorded repository" in text
    assert "nothing at all; the Run keeps its stop and every charged Hop" in text
    assert load_run(_run_json_path(result)).status.value == "reconcile_required"


def test_preview_of_a_classification_that_admits_no_recovery(
    repository: Path, tmp_path: Path, state_home: Path,
):
    os.environ["WFC_FAKE_ENGINE_MODE"] = "completed_consistent"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    request_path = _write_request(repository, tmp_path / "request.yaml")
    assert L.run_lifecycle(L.RunInputs(
        repository=repository, config_path=L.DEFAULT_CONFIG_PATH, request_path=request_path,
        target_dir=None, overrides=_OVERRIDES, executable=str(FAKE_PROVIDER),
        hop_timeout_seconds=60.0, confirm=lambda a, b: True,
    )).classification == "completed"

    out = StringIO()
    preview = L.resume_lifecycle(repository, preview=True, out=out)
    assert preview.classification == L.RECOVERY_PREVIEW_CLASSIFICATION
    assert "admits no same-Run recovery" in preview.message
    assert "none -- this classification admits no same-Run recovery" in out.getvalue()


def test_preview_exits_zero_through_the_cli(repository: Path, tmp_path: Path, state_home: Path):
    _interrupted_run(repository, tmp_path)
    assert cli_main(["resume", "--repository", str(repository), "--preview"]) == 0


# --------------------------------------------------------------------------
# Every predicate element violated one at a time: a named refusal that
# mutates nothing, including the marker.
# --------------------------------------------------------------------------

def _refuses(repository: Path, result: L.LifecycleResult, expected: str) -> None:
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()
    marker_before = (
        _marker_path(result).read_bytes() if _marker_path(result).exists() else None
    )
    _, journal = _checkout_dir_and_journal(repository)
    before_records = len(journal.read_all())

    refused = L.resume_lifecycle(repository)
    assert refused.classification == "refused_not_recoverable", refused.message
    assert expected in refused.message
    assert run_path.read_bytes() == before_bytes
    assert (
        _marker_path(result).read_bytes() if _marker_path(result).exists() else None
    ) == marker_before
    assert len(journal.read_all()) == before_records


def test_refuses_a_recorded_repository_anomaly(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """Friction entry 16's shape: the interruption left a dirty worktree, so
    the Coordinator's own post-interruption observation recorded it."""

    result = _interrupted_run(
        repository, tmp_path,
        WFC_FAKE_ENGINE_INTERRUPTION_ANOMALIES="dirty tracked state,non-ignored untracked state",
    )
    _refuses(repository, result, "left recorded repository anomalies: dirty tracked state")


def test_refuses_a_commit_left_by_the_interrupted_invocation(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _interrupted_run(
        repository, tmp_path, WFC_FAKE_ENGINE_INTERRUPTION_COMMITS="c" * 40,
    )
    _refuses(repository, result, "left a commit at Hop H001")


def test_refuses_a_marker_carrying_a_staged_qualification_anchor(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """A published staged qualification attempt is refused from whichever of
    the Coordinator's two records still carries its anchor. A Front Door Run
    is schema 2 (``commissioning.py`` renders every profile at
    ``schema_version: 2``), whose Hop summaries do not persist the anchor at
    all, so here the *marker*'s anchor is the element that refuses; the
    Hop-level conjunct is exercised directly against the pure predicate in
    ``test_quiescent_interruption_reducer.py``."""

    result = _interrupted_run(
        repository, tmp_path, WFC_FAKE_ENGINE_INTERRUPTION_ANCHOR="f" * 64,
    )
    _refuses(repository, result, "does not correlate with the charged uncertain Hop H001")


def test_refuses_a_last_hop_that_is_not_the_charged_uncertain_hop(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _interrupted_run(repository, tmp_path, WFC_FAKE_ENGINE_INTERRUPTION_ROLE="worker")
    _refuses(repository, result, "not the Coordinator's charged uncertain Hop")


def test_refuses_an_uncorrelated_predecessor_dispatch_marker(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """A marker that is not the one the charged Hop was dispatched under is
    refused, never silently retired."""

    result = _interrupted_run(
        repository, tmp_path, WFC_FAKE_ENGINE_INTERRUPTION_MARKER_HOP_ID="H009",
    )
    _refuses(repository, result, "does not correlate with the charged uncertain Hop H001")


def test_refuses_an_unreadable_predecessor_dispatch_marker(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _interrupted_run(repository, tmp_path)
    _marker_path(result).write_text("{not json", encoding="utf-8")
    _refuses(repository, result, "predecessor dispatch marker is not readable")


def test_refuses_an_open_semantic_stop(repository: Path, tmp_path: Path, state_home: Path):
    from workflow_coordinator.model import SemanticStop

    result = _interrupted_run(repository, tmp_path)
    run_path = _run_json_path(result)
    write_run_snapshot(run_path, replace(
        load_run(run_path),
        semantic_stop=SemanticStop(kind="operational", question="Which test fails?", source_hop_id="H001"),
    ))
    _refuses(repository, result, "open semantic stop")


def test_refuses_a_human_question_of_its_own(repository: Path, tmp_path: Path, state_home: Path):
    result = _interrupted_run(repository, tmp_path)
    run_path = _run_json_path(result)
    write_run_snapshot(run_path, replace(load_run(run_path), human_question="Please decide."))
    _refuses(repository, result, "human question of its own")


def test_refuses_a_dirty_live_worktree(repository: Path, tmp_path: Path, state_home: Path):
    result = _interrupted_run(repository, tmp_path)
    (repository / "src" / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
    _refuses(repository, result, "worktree is not clean")


def test_refuses_a_moved_live_head(repository: Path, tmp_path: Path, state_home: Path):
    result = _interrupted_run(repository, tmp_path)
    (repository / "src" / "extra.txt").write_text("more\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "chore: move HEAD after the interruption")
    _refuses(repository, result, "current HEAD differs from the Run's recorded end HEAD")


def test_refuses_when_no_hop_budget_remains(repository: Path, tmp_path: Path, state_home: Path):
    """Composition with the budget rules: the interruption's own charge can
    exhaust the ceiling, and then the recovery refuses before any mutation
    rather than erasing the honest stop."""

    result = _interrupted_run(repository, tmp_path)
    run_path = _run_json_path(result)
    run = load_run(run_path)
    write_run_snapshot(run_path, replace(run, hop_limit=run.hop_used))
    _refuses(repository, result, "no autonomous Hop budget remains for a continuation")


def test_refuses_a_changed_commission_identity(repository: Path, tmp_path: Path, state_home: Path):
    result = _interrupted_run(repository, tmp_path)
    checkout_dir, _ = _checkout_dir_and_journal(repository)
    request_path = checkout_dir / "commission" / "request.yaml"
    document = yaml.safe_load(request_path.read_text(encoding="utf-8"))
    document["task"]["title"] += " (tampered since authorization)"
    request_path.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")
    _refuses(repository, result, "Commission or execution envelope identity changed")


def test_refuses_while_another_lifecycle_holds_the_lock(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _interrupted_run(repository, tmp_path)
    checkout_dir, _ = _checkout_dir_and_journal(repository)
    lock = J.try_acquire_lock(checkout_dir)
    assert lock is not None
    try:
        _refuses(repository, result, "active lifecycle already running for this checkout")
    finally:
        J.release_lock(lock)

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    assert L.resume_lifecycle(repository).classification == "completed"


def test_launch_failure_rolls_back_the_run_and_restores_the_marker(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """The continuation engine can pass both pre-mutation checks and still
    fail to spawn. The Run, its checkpoint *and* the retired marker are
    restored, so a later resume recovers the identical Run."""

    result = _interrupted_run(repository, tmp_path)
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()
    marker_bytes = _marker_path(result).read_bytes()
    _, journal = _checkout_dir_and_journal(repository)
    before_records = journal.read_all()

    broken_engine = tmp_path / "broken-wfc-engine"
    broken_engine.write_text("#!/nonexistent/interpreter-xyz\n", encoding="utf-8")
    broken_engine.chmod(0o755)
    L._ENGINE_COMMAND = (str(broken_engine),)

    with pytest.raises(L.LifecycleError) as excinfo:
        L.resume_lifecycle(repository)
    assert excinfo.value.classification == "refused_not_recoverable"

    assert run_path.read_bytes() == before_bytes
    assert _marker_path(result).read_bytes() == marker_bytes
    assert [r.kind for r in journal.read_all()[len(before_records):]] == [
        "mechanical_recovery_intent", "mechanical_recovery_resumed",
        "mechanical_recovery_marker_retired", "mechanical_recovery_launch_intent",
        "mechanical_recovery_launch_failed", "mechanical_recovery_rolled_back",
    ]
    # A rolled-back attempt performed no recovery, so no RECOVERY block is
    # reported for it; its journal records remain as the attempt's trace.
    assert L.status_lifecycle(repository).recovery_events == ()

    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    second = L.resume_lifecycle(repository)
    assert second.classification == "completed", second.message


# --------------------------------------------------------------------------
# Visibility: status, report and audit.
# --------------------------------------------------------------------------

def test_status_reports_the_recovery_event(repository: Path, tmp_path: Path, state_home: Path):
    _interrupted_run(repository, tmp_path)
    assert L.status_lifecycle(repository).recovery_events == ()

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    assert L.resume_lifecycle(repository).classification == "completed"

    status = L.status_lifecycle(repository)
    assert len(status.recovery_events) == 1
    event = status.recovery_events[0]
    assert event["recovery_class"] == "quiescent_interruption"
    assert event["interrupted_hop_id"] == "H001"
    assert event["from_status"] == "reconcile_required"
    assert cli_main(["status", "--repository", str(repository)]) == 0


def test_audit_and_report_name_the_path_that_exists(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    """The archived Attempt records the reconcile reason, and `wfc report`
    uses it to name `wfc resume --preview` instead of asserting a
    conflicting state the predicate proves is absent."""

    _interrupted_run(repository, tmp_path)
    _, journal = _checkout_dir_and_journal(repository)
    archive = journal.latest(("ext.archive.result",))
    assert archive is not None
    destination = evidence_path / archive.data["destination_relative"]
    audit = json.loads((destination / "audit.json").read_text(encoding="utf-8"))
    assert audit["run"]["reconcile_reason"] == "interruption"
    assert audit["run"]["status"] == "reconcile_required"
    # Projected from the attempt directory itself, not only from the written file.
    assert project_audit(destination)["run"]["reconcile_reason"] == "interruption"

    report = build_report(audit)
    assert report["status"] == "reconcile_required"
    assert "wfc resume --preview" in report["next_action"]


# --------------------------------------------------------------------------
# Out of scope entirely: no change to any other stop class's behaviour.
# --------------------------------------------------------------------------

def test_a_prerequisite_return_reconcile_keeps_the_no_op_behaviour(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """The other `reconcile_required` reason is deliberately untouched: the
    scope gate makes `wfc resume` fall through to its unchanged no-op."""

    result = _interrupted_run(
        repository, tmp_path,
        WFC_FAKE_ENGINE_INTERRUPTION_RECONCILE_REASON="prerequisite_return",
    )
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()

    second = L.resume_lifecycle(repository)
    assert second.classification == "reconcile_required"
    assert second.message == "lifecycle already classified (no-op)"
    assert run_path.read_bytes() == before_bytes
    _, journal = _checkout_dir_and_journal(repository)
    assert journal.latest(("mechanical_recovery_intent",)) is None


def test_an_interruption_shaped_run_without_the_reconcile_reason_is_out_of_scope(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _interrupted_run(repository, tmp_path)
    run_path = _run_json_path(result)
    write_run_snapshot(run_path, replace(load_run(run_path), reconcile_reason=None))

    second = L.resume_lifecycle(repository)
    assert second.classification == "reconcile_required"
    assert second.message == "lifecycle already classified (no-op)"


def test_an_ordinary_semantic_awaiting_human_keeps_the_no_op_behaviour(
    repository: Path, tmp_path: Path, state_home: Path,
):
    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    request_path = _write_request(repository, tmp_path / "request.yaml")
    result = L.run_lifecycle(L.RunInputs(
        repository=repository, config_path=L.DEFAULT_CONFIG_PATH, request_path=request_path,
        target_dir=None, overrides=_OVERRIDES, executable=str(FAKE_PROVIDER),
        hop_timeout_seconds=60.0, confirm=lambda a, b: True,
    ))
    assert result.classification == "awaiting_human"

    second = L.resume_lifecycle(repository)
    assert second.classification == "awaiting_human"
    assert second.message == "lifecycle already classified (no-op)"
    _, journal = _checkout_dir_and_journal(repository)
    assert journal.latest(("mechanical_recovery_intent",)) is None
