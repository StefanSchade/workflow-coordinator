"""B, WI-WFC-RECOVERY-ROUTING-001: the regular, authorized `wfc resume`
mechanical-recovery path.

Reproduces, structurally, the two incident shapes the WorkItem fixes: (i) a
*new* Run A now classifies ``reconcile_required`` instead of terminal
``awaiting_human`` for a purely mechanical, consistent, post-acceptance
durable-artifact stop; (ii) an *already archived* terminal ``awaiting_human``
Run stopped before this predicate existed (the real incident's exact shape)
recovers through the identical `wfc resume` path without any manual state
edit. Every predicate element is also violated one at a time to assert a
named refusal that changes nothing. Uses the same fake-engine fixture
pattern as ``test_lifecycle_crash_matrix.py``/``test_postrun_lifecycle.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from workflow_coordinator.frontdoor import journal as J
from workflow_coordinator.frontdoor import lifecycle as L
from workflow_coordinator.frontdoor.cli import main as cli_main
from workflow_coordinator.model import Finding, FindingState, SemanticStop
from workflow_coordinator.persistence import write_run_snapshot
from workflow_coordinator.serialization import load_run

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "bp4"
FAKE_PROVIDER = FIXTURES / "fake_provider.py"
FAKE_ENGINE = FIXTURES / "fake_engine.py"

_MECHANICAL_STOP_REASON = "mechanical_preemption:required durable artifact has no CommitIntent"


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
    for name in (
        "WFC_FAKE_ENGINE_MODE", "WFC_FAKE_ENGINE_CONTINUE_MODE",
        "WFC_FAKE_ENGINE_MECHANICAL_STOP_REASON", "WFC_FAKE_ENGINE_RUN_ID",
    ):
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


def _write_request(repository: Path, path: Path, *, commission_id: str = "wfc-recovery") -> Path:
    head = git(repository, "rev-parse", "HEAD")
    data = {
        "schema_version": 1, "commission_id": commission_id, "work_item_id": "WI-RECOVERY",
        "authority_source": "test authorizes this bounded lifecycle.", "program": {"id": "simple", "revision": "v1"},
        "task": {
            "title": "recovery task", "body": "exercise mechanical-recovery routing",
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


def _inputs(repository: Path, request_path: Path) -> L.RunInputs:
    return L.RunInputs(
        repository=repository, config_path=L.DEFAULT_CONFIG_PATH, request_path=request_path,
        target_dir=None, overrides=_OVERRIDES, executable=str(FAKE_PROVIDER),
        hop_timeout_seconds=60.0, confirm=lambda a, b: True,
    )


def _run_with_fake_engine(
    repository: Path, tmp_path: Path, mode: str, *, commission_id: str = "wfc-recovery",
    stop_reason: str | None = None,
) -> L.LifecycleResult:
    os.environ["WFC_FAKE_ENGINE_MODE"] = mode
    if stop_reason is not None:
        os.environ["WFC_FAKE_ENGINE_MECHANICAL_STOP_REASON"] = stop_reason
    else:
        os.environ.pop("WFC_FAKE_ENGINE_MECHANICAL_STOP_REASON", None)
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    request_path = _write_request(repository, tmp_path / "request.yaml", commission_id=commission_id)
    inputs = _inputs(repository, request_path)
    return L.run_lifecycle(inputs)


def _checkout_dir_and_journal(repository: Path) -> tuple[Path, J.Journal]:
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    return checkout_dir, J.Journal(checkout_dir / "journal.jsonl")


def _run_json_path(result: L.LifecycleResult) -> Path:
    return Path(result.checkout_state_root) / "run.json"


# --------------------------------------------------------------------------
# A's new classification, recovered through B.
# --------------------------------------------------------------------------

def test_new_run_stopped_mechanical_reconcile_required_recovers_to_completion(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _run_with_fake_engine(repository, tmp_path, "mechanical_reconcile_required")
    assert result.classification == "reconcile_required"
    run_before = load_run(_run_json_path(result))
    assert run_before.status.value == "reconcile_required"
    assert run_before.stop_reason == _MECHANICAL_STOP_REASON

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "completed", recovered.message

    _, journal = _checkout_dir_and_journal(repository)
    assert journal.latest(("mechanical_recovery_intent",)) is not None
    assert journal.latest(("mechanical_recovery_resumed",)) is not None
    assert journal.count("classified") == 2


# --------------------------------------------------------------------------
# B(ii): the real incident's exact shape -- already archived, terminal,
# never previously reconcile_required -- recovers without any manual edit.
# --------------------------------------------------------------------------

def test_already_archived_incident_shaped_awaiting_human_is_recovered(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    assert result.classification == "awaiting_human"
    run_before = load_run(_run_json_path(result))
    assert run_before.status.value == "awaiting_human"
    assert run_before.stop_reason == _MECHANICAL_STOP_REASON
    assert run_before.semantic_stop is None
    assert run_before.human_question is None

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "completed"
    assert recovered.run_id == result.run_id  # identical Run, never a new one.

    _, journal = _checkout_dir_and_journal(repository)
    kinds = [record.kind for record in journal.read_all()]
    assert kinds.count("classified") == 2
    assert kinds.index("mechanical_recovery_intent") > kinds.index("classified")


def test_recovery_run_still_needs_its_own_normal_accepting_route(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """Recovery never itself accepts the Run: a further stop is still a stop."""

    result = _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "awaiting_human_again"
    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "awaiting_human"
    run_after = load_run(_run_json_path(result))
    assert run_after.stop_reason == "second_fixture_stop"


# --------------------------------------------------------------------------
# Archive immutability: the predecessor attempt is never rewritten.
# --------------------------------------------------------------------------

def test_predecessor_archived_attempt_is_byte_identical_after_recovery(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    result = _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    assert result.classification == "awaiting_human"
    _, journal = _checkout_dir_and_journal(repository)
    first_archive = journal.latest(("ext.archive.result",))
    assert first_archive is not None
    first_destination = evidence_path / first_archive.data["destination_relative"]
    before_bytes = {
        path.relative_to(first_destination): path.read_bytes()
        for path in first_destination.rglob("*") if path.is_file()
    }
    before_commit = git(evidence_path, "rev-parse", "HEAD")

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "completed"

    after_bytes = {
        path.relative_to(first_destination): path.read_bytes()
        for path in first_destination.rglob("*") if path.is_file()
    }
    assert after_bytes == before_bytes
    assert git(evidence_path, "cat-file", "-e", before_commit) == ""

    second_archive = journal.latest(("ext.archive.result",))
    assert second_archive is not None
    assert second_archive.data["destination_relative"] != first_archive.data["destination_relative"]
    second_destination = evidence_path / second_archive.data["destination_relative"]
    assert second_destination != first_destination
    assert second_destination.is_dir()


def test_recovery_continuation_gets_its_own_preservation_and_archive_facts(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    """A recovery continuation's own classification must get its own
    candidate-preservation step, and its archived Evidence must state its
    own facts, never the predecessor attempt's (reused by inheritance)."""

    terminal_kinds = ("ext.preservation.result", "ext.preservation.not_needed", "ext.preservation.refused")

    result = _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    assert result.classification == "awaiting_human"
    _, journal = _checkout_dir_and_journal(repository)
    preservation_before = [r for r in journal.read_all() if r.kind in terminal_kinds]
    assert len(preservation_before) == 1

    # The continuation stops again at a preservation-triggering
    # classification, so the scoping bug (re-serving the predecessor's
    # already-terminal fact without ever re-running BP4b) is observable.
    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "awaiting_human_again"
    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "awaiting_human"

    records = journal.read_all()
    classified_records = [r for r in records if r.kind == "classified"]
    assert len(classified_records) == 2
    second_classified_seq = classified_records[1].seq

    preservation_after = [r for r in records if r.kind in terminal_kinds]
    # The continuation must get its own terminal preservation fact, not
    # merely re-serve the predecessor attempt's.
    assert len(preservation_after) == 2
    assert preservation_after[1].seq > second_classified_seq

    archive_records = [r for r in records if r.kind == "ext.archive.result"]
    assert len(archive_records) == 2
    second_destination = evidence_path / archive_records[1].data["destination_relative"]
    audit_doc = json.loads((second_destination / "audit.json").read_text(encoding="utf-8"))
    assert audit_doc["lifecycle"]["preservation"] is not None


def test_recovery_refuses_a_collision_at_an_existing_destination(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    """A pre-existing, unrelated directory at the derived recovery destination
    is refused, never overwritten (the general archive collision rule)."""

    _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    _, journal = _checkout_dir_and_journal(repository)
    first_archive = journal.latest(("ext.archive.result",))
    collision = evidence_path / "archive" / "unclassified" / (
        Path(first_archive.data["destination_relative"]).name + "-recovery-1"
    )
    collision.mkdir(parents=True)
    (collision / "unexpected.txt").write_text("not ours\n", encoding="utf-8")

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "completed"  # the engine outcome still stands.
    assert recovered.postrun_notice is not None
    assert "ARCHIVE FAILED" in recovered.postrun_notice
    assert (collision / "unexpected.txt").read_text(encoding="utf-8") == "not ours\n"


# --------------------------------------------------------------------------
# Every predicate element, violated one at a time: a named refusal, no
# mutation.
# --------------------------------------------------------------------------

def test_recovery_refuses_on_open_blocking_finding(repository: Path, tmp_path: Path, state_home: Path):
    result = _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    run_path = _run_json_path(result)
    run = load_run(run_path)
    run = replace(run, findings=(
        Finding(
            finding_id="F001", invariant="fixture invariant", blocking=True, state=FindingState.OPEN,
            introduced_by_hop="H006", changed_by_hop="H006", evidence_refs=(), disposition="open",
        ),
    ))
    write_run_snapshot(run_path, run)
    before_bytes = run_path.read_bytes()

    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "refused_not_recoverable"
    assert "blocking Finding" in recovered.message
    assert run_path.read_bytes() == before_bytes


def test_recovery_refuses_on_semantic_stop_present(repository: Path, tmp_path: Path, state_home: Path):
    result = _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    run_path = _run_json_path(result)
    run = load_run(run_path)
    run = replace(run, semantic_stop=SemanticStop(kind="operational", question="Which branch?", source_hop_id="H006"))
    write_run_snapshot(run_path, run)

    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "refused_not_recoverable"
    assert "semantic stop" in recovered.message


def test_recovery_refuses_on_human_question_present(repository: Path, tmp_path: Path, state_home: Path):
    result = _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    run_path = _run_json_path(result)
    run = load_run(run_path)
    run = replace(run, human_question="Please decide.")
    write_run_snapshot(run_path, run)

    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "refused_not_recoverable"
    assert "human question" in recovered.message


def test_recovery_refuses_on_non_recoverable_anomaly_class(repository: Path, tmp_path: Path, state_home: Path):
    """WFC-GIT-08 (commit subject) anomalies are deliberately excluded."""

    result = _run_with_fake_engine(
        repository, tmp_path, "mechanical_awaiting_human",
        stop_reason="mechanical_preemption:WFC-GIT-08 commit subject lacks WorkItem/Hop identity: oops",
    )
    assert result.classification == "awaiting_human"

    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "refused_not_recoverable"
    assert "recoverable durable-artifact anomaly class" in recovered.message


def test_recovery_refuses_on_dirty_worktree(repository: Path, tmp_path: Path, state_home: Path):
    _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    (repository / "src" / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")

    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "refused_not_recoverable"
    assert "not clean" in recovered.message


def test_recovery_refuses_on_moved_head(repository: Path, tmp_path: Path, state_home: Path):
    _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    (repository / "src" / "extra.txt").write_text("more\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "chore: move HEAD after the mechanical stop")

    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "refused_not_recoverable"
    assert "HEAD" in recovered.message


def test_recovery_refuses_on_no_hop_budget_remaining(repository: Path, tmp_path: Path, state_home: Path):
    """F-C: a mechanical stop on the Run's last authorized Hop must refuse
    the continuation by name, not erase the honest stop reason by writing
    the Run to ``ready`` with no Hop left to drive it."""

    result = _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    run_path = _run_json_path(result)
    run = load_run(run_path)
    run = replace(run, hop_limit=run.hop_used)
    write_run_snapshot(run_path, run)
    before_bytes = run_path.read_bytes()

    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "refused_not_recoverable"
    assert "no autonomous Hop budget remains" in recovered.message
    assert run_path.read_bytes() == before_bytes


def test_recovery_refuses_on_no_recorded_hop_outcome(repository: Path, tmp_path: Path, state_home: Path):
    result = _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    run_path = _run_json_path(result)
    run = load_run(run_path)
    run = replace(run, hops=(), hop_used=0)
    write_run_snapshot(run_path, run)
    before_bytes = run_path.read_bytes()

    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "refused_not_recoverable"
    assert "no recorded Hop outcome" in recovered.message
    assert run_path.read_bytes() == before_bytes


def test_recovery_refuses_on_changed_commission_identity(repository: Path, tmp_path: Path, state_home: Path):
    result = _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    checkout_dir, _ = _checkout_dir_and_journal(repository)
    request_path = checkout_dir / "commission" / "request.yaml"
    document = yaml.safe_load(request_path.read_text(encoding="utf-8"))
    document["task"]["title"] = document["task"]["title"] + " (tampered since authorization)"
    request_path.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()

    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "refused_not_recoverable"
    assert "Commission or execution envelope identity changed since authorization" in recovered.message
    assert run_path.read_bytes() == before_bytes


def test_recovery_refuses_on_incomplete_launch_provenance(
    repository: Path, tmp_path: Path, state_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Both ``authorized`` and ``prelaunch_passed`` are always written before
    any Run can reach a classified mechanical stop in production; this
    boundary check is exercised directly against the predicate it guards --
    the one thing standing between a recovery and continuing under
    incomplete lifecycle provenance."""

    result = _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    checkout_dir, journal = _checkout_dir_and_journal(repository)
    classified = journal.latest(("classified",))
    assert classified is not None

    real_latest = J.Journal.latest

    def _patched_latest(self, kinds=None):
        if kinds in (("authorized",), ("prelaunch_passed",)):
            return None
        return real_latest(self, kinds)

    monkeypatch.setattr(J.Journal, "latest", _patched_latest)
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()

    recovered = L._attempt_mechanical_recovery(repository, checkout_dir, journal, classified)
    assert recovered is not None
    assert recovered.classification == "refused_not_recoverable"
    assert "lifecycle authorization or launch provenance is incomplete" in recovered.message
    assert run_path.read_bytes() == before_bytes


def test_recovery_refuses_on_missing_engine_executable_and_a_later_resume_still_recovers(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """F004: a since-removed/renamed engine console script must be a named
    refusal with no mutation at all, not a Run rewritten to ``ready`` with
    its mechanical diagnosis erased and no process to drive it. A later
    resume, with the engine available again, must still recover the Run."""

    result = _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()
    _, journal = _checkout_dir_and_journal(repository)
    before_record_count = len(journal.read_all())

    L._ENGINE_COMMAND = (str(tmp_path / "no-such-engine-binary"),)
    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "refused_not_recoverable"
    assert "no engine executable is available for a continuation" in recovered.message
    assert run_path.read_bytes() == before_bytes
    assert len(journal.read_all()) == before_record_count

    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    second = L.resume_lifecycle(repository)
    assert second.classification == "completed", second.message


def test_recovery_refuses_on_active_lifecycle_lock_and_changes_nothing(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """F004: a concurrently held checkout ownership lock must refuse the
    continuation before any mutation, not after the Run has already been
    rewritten to ``ready``."""

    result = _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()
    checkout_dir, journal = _checkout_dir_and_journal(repository)
    before_record_count = len(journal.read_all())

    lock = J.try_acquire_lock(checkout_dir)
    assert lock is not None
    try:
        recovered = L.resume_lifecycle(repository)
        assert recovered.classification == "refused_not_recoverable"
        assert "active lifecycle already running for this checkout" in recovered.message
        assert run_path.read_bytes() == before_bytes
        assert len(journal.read_all()) == before_record_count
    finally:
        J.release_lock(lock)

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    second = L.resume_lifecycle(repository)
    assert second.classification == "completed", second.message


# --------------------------------------------------------------------------
# Out of scope entirely: no change to any other classification's behaviour.
# --------------------------------------------------------------------------

def test_ordinary_semantic_awaiting_human_keeps_the_old_no_op_behaviour(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _run_with_fake_engine(repository, tmp_path, "awaiting_human")
    assert result.classification == "awaiting_human"
    second = L.resume_lifecycle(repository)
    assert second.classification == "awaiting_human"
    assert second.message == "lifecycle already classified (no-op)"
    _, journal = _checkout_dir_and_journal(repository)
    assert journal.latest(("mechanical_recovery_intent",)) is None


def test_completed_classification_is_unaffected(repository: Path, tmp_path: Path, state_home: Path):
    result = _run_with_fake_engine(repository, tmp_path, "completed_consistent")
    assert result.classification == "completed"
    second = L.resume_lifecycle(repository)
    assert second.classification == "completed"
    assert second.message == "lifecycle already classified (no-op)"
