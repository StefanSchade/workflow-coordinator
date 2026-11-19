"""BP4b integration: candidate preservation glued onto the installed lifecycle.

Uses the same fake-provider/fake-engine fixtures as
``test_lifecycle_crash_matrix.py`` to exercise the trigger, idempotency and
crash-recovery behavior of ``frontdoor.preservation`` through real
``wfc run``/``wfc resume``/``wfc status`` calls, while independently pinning
that BP4a's own classification, exit codes and journal records are
unaffected.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from workflow_coordinator import git_facts as GF
from workflow_coordinator.frontdoor import journal as J
from workflow_coordinator.frontdoor import lifecycle as L
from workflow_coordinator.frontdoor import preservation as P
from workflow_coordinator.frontdoor.cli import main as cli_main

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "bp4"
FAKE_PROVIDER = FIXTURES / "fake_provider.py"
FAKE_ENGINE = FIXTURES / "fake_engine.py"

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


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
    os.environ.pop("WFC_FAKE_ENGINE_MODE", None)
    os.environ.pop("WFC_FAKE_ENGINE_DIRTY_FILE", None)


@pytest.fixture
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(home))
    return home / "wfc"


@pytest.fixture
def repository(tmp_path: Path) -> Path:
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

    assert cli_main(["init", "--repository", str(repo), "--scope-ceiling", "src"]) == 0
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: commit Consumer Config")
    return repo


def _write_request(repository: Path, path: Path, *, commission_id: str = "bp4b") -> Path:
    head = git(repository, "rev-parse", "HEAD")
    data = {
        "schema_version": 1, "commission_id": commission_id, "work_item_id": "WI-BP4B",
        "authority_source": "test authorizes this bounded lifecycle.", "program": {"id": "simple", "revision": "v1"},
        "task": {
            "title": "preservation task", "body": "exercise BP4b candidate preservation",
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
    repository: Path, tmp_path: Path, mode: str, *, commission_id: str = "bp4b", dirty_file: str | None = None,
) -> L.LifecycleResult:
    os.environ["WFC_FAKE_ENGINE_MODE"] = mode
    if dirty_file:
        os.environ["WFC_FAKE_ENGINE_DIRTY_FILE"] = dirty_file
    else:
        os.environ.pop("WFC_FAKE_ENGINE_DIRTY_FILE", None)
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    request_path = _write_request(repository, tmp_path / "request.yaml", commission_id=commission_id)
    inputs = _inputs(repository, request_path)
    return L.run_lifecycle(inputs)


def _checkout_dir_and_journal(repository: Path) -> tuple[Path, J.Journal]:
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    return checkout_dir, J.Journal(checkout_dir / "journal.jsonl")


def _classify_without_preservation(
    repository: Path, tmp_path: Path, mode: str, *, commission_id: str, dirty_file: str | None,
) -> tuple[L.LifecycleResult, Path, J.Journal]:
    """Reach ``classified`` via the same ``_advance`` BP4a uses, deliberately
    skipping the ``run_lifecycle``/``resume_lifecycle`` preservation hook, so
    a test can pin an exact pre-crash preservation state before ever letting
    preservation run for the first time."""

    os.environ["WFC_FAKE_ENGINE_MODE"] = mode
    if dirty_file:
        os.environ["WFC_FAKE_ENGINE_DIRTY_FILE"] = dirty_file
    else:
        os.environ.pop("WFC_FAKE_ENGINE_DIRTY_FILE", None)
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    request_path = _write_request(repository, tmp_path / "request.yaml", commission_id=commission_id)
    inputs = _inputs(repository, request_path)
    checkout, checkout_dir, journal = L._prepare_checkout(repository)

    def authorize():
        return L._authorize(inputs, checkout_dir, journal)

    result = L._advance(repository, checkout_dir, journal, authorize=authorize)
    return result, checkout_dir, journal


# --------------------------------------------------------------------------
# Trigger conditions.
# --------------------------------------------------------------------------

def test_interrupted_run_leaves_a_preserved_candidate_and_untouched_worktree_otherwise(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _run_with_fake_engine(
        repository, tmp_path, "dispatch_then_crash", commission_id="bp4b-interrupted", dirty_file="src/wip.txt",
    )
    assert result.classification == "interrupted"
    assert result.exit_code == L.EXIT_CODES["interrupted"]

    _, journal = _checkout_dir_and_journal(repository)
    fact = journal.latest(("ext.preservation.result",))
    assert fact is not None
    assert fact.data["ref"] == f"refs/wfc/preserved/{result.run_id}/1"
    assert fact.data["quiescence"] == "unattested"
    assert git(repository, "show", f"{fact.data['commit']}:src/wip.txt") == "fake engine: unadopted worktree work"
    # The actual worktree/HEAD are untouched by preservation.
    assert git(repository, "rev-parse", "HEAD") == result.end_head
    assert git(repository, "status", "--porcelain=v1") != ""  # the dirty file is still there, uncommitted.


def test_completed_run_has_no_preservation(repository: Path, tmp_path: Path, state_home: Path):
    result = _run_with_fake_engine(repository, tmp_path, "completed_consistent", commission_id="bp4b-completed")
    assert result.classification == "completed"

    _, journal = _checkout_dir_and_journal(repository)
    assert journal.latest((
        "ext.preservation.result", "ext.preservation.not_needed", "ext.preservation.refused", "ext.preservation.intent",
    )) is None


def test_clean_worktree_at_awaiting_human_is_not_needed(repository: Path, tmp_path: Path, state_home: Path):
    result = _run_with_fake_engine(repository, tmp_path, "awaiting_human", commission_id="bp4b-clean-stop")
    assert result.classification == "awaiting_human"

    _, journal = _checkout_dir_and_journal(repository)
    fact = journal.latest(("ext.preservation.not_needed",))
    assert fact is not None
    status = P.latest_preservation_status(repository)
    assert status.kind == "not_needed"


def test_ownership_lock_held_is_refused_and_repository_byte_identical(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result, checkout_dir, journal = _classify_without_preservation(
        repository, tmp_path, "awaiting_human", commission_id="bp4b-lock-held", dirty_file="src/wip.txt",
    )
    assert result.classification == "awaiting_human"
    before_head = git(repository, "rev-parse", "HEAD")
    before_status = git(repository, "status", "--porcelain=v1")

    lock = J.try_acquire_lock(checkout_dir)
    assert lock is not None
    try:
        resumed = L.resume_lifecycle(repository)
    finally:
        J.release_lock(lock)

    assert resumed.classification == "awaiting_human"
    assert resumed.exit_code == L.EXIT_CODES["awaiting_human"]
    status = P.latest_preservation_status(repository)
    assert status.kind == "refused"
    assert status.data["reason"] == "ownership lock held"
    assert git(repository, "rev-parse", "HEAD") == before_head
    assert git(repository, "status", "--porcelain=v1") == before_status
    assert git(repository, "for-each-ref", f"refs/wfc/preserved/{result.run_id}/") == ""


# --------------------------------------------------------------------------
# Idempotency and crash recovery.
# --------------------------------------------------------------------------

def test_repeat_run_and_resume_is_idempotent_no_second_ref(repository: Path, tmp_path: Path, state_home: Path):
    result = _run_with_fake_engine(
        repository, tmp_path, "awaiting_human", commission_id="bp4b-repeat", dirty_file="src/wip.txt",
    )
    assert result.classification == "awaiting_human"
    first = P.latest_preservation_status(repository)
    assert first.kind == "result"

    second_result = L.resume_lifecycle(repository)
    assert second_result.classification == "awaiting_human"
    second = P.latest_preservation_status(repository)
    assert second.kind == "result"
    assert second.data["ref"] == first.data["ref"]

    refs = git(
        repository, "for-each-ref", "--format=%(refname)", f"refs/wfc/preserved/{result.run_id}/",
    )
    assert refs.splitlines() == [f"refs/wfc/preserved/{result.run_id}/1"]
    _, journal = _checkout_dir_and_journal(repository)
    assert journal.count("ext.preservation.result") == 1


def test_crash_after_ref_creation_before_result_record_resume_records_result_no_second_ref(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result, checkout_dir, journal = _classify_without_preservation(
        repository, tmp_path, "awaiting_human", commission_id="bp4b-crash-after-ref", dirty_file="src/wip.txt",
    )
    assert result.classification == "awaiting_human"

    classified = journal.latest(("classified",))
    scope = ("src",)

    # Roll back to the state a crash right after ref creation would leave:
    # the ref already exists, but no ext.preservation.* fact was ever written.
    lock = J.try_acquire_lock(checkout_dir)
    assert lock is not None
    try:
        outcome = GF.preserve_candidate(
            repository, expected_repository=repository, base_head=classified.data["end_head"],
            allowed_paths=scope, run_id=classified.data["run_id"],
        )
    finally:
        J.release_lock(lock)
    assert outcome.status == "preserved"
    assert P.latest_preservation_status(repository).kind == "absent"  # no fact recorded (simulated crash).

    resumed = L.resume_lifecycle(repository)
    assert resumed.classification == "awaiting_human"
    status = P.latest_preservation_status(repository)
    assert status.kind == "result"
    assert status.data["ref"] == outcome.ref
    refs = git(
        repository, "for-each-ref", "--format=%(refname)", f"refs/wfc/preserved/{result.run_id}/",
    )
    assert refs.splitlines() == [f"refs/wfc/preserved/{result.run_id}/1"]


def test_crash_before_ref_creation_resume_captures_once(repository: Path, tmp_path: Path, state_home: Path):
    result, checkout_dir, journal = _classify_without_preservation(
        repository, tmp_path, "awaiting_human", commission_id="bp4b-crash-before-ref", dirty_file="src/wip.txt",
    )
    assert result.classification == "awaiting_human"

    # The state a crash right after the intent record (and before any ref or
    # result) would leave.
    journal.append("ext.preservation.intent", {"run_id": result.run_id})
    assert P.latest_preservation_status(repository).kind == "absent"

    resumed = L.resume_lifecycle(repository)
    assert resumed.classification == "awaiting_human"
    status = P.latest_preservation_status(repository)
    assert status.kind == "result"
    refs = git(
        repository, "for-each-ref", "--format=%(refname)", f"refs/wfc/preserved/{result.run_id}/",
    )
    assert refs.splitlines() == [f"refs/wfc/preserved/{result.run_id}/1"]


# --------------------------------------------------------------------------
# Refusal leaves BP4a classification/exit code unchanged; status is read-only.
# --------------------------------------------------------------------------

def test_preservation_refusal_leaves_bp4a_classification_and_exit_code_unchanged(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _run_with_fake_engine(
        repository, tmp_path, "awaiting_human", commission_id="bp4b-refusal", dirty_file="outside-scope.txt",
    )
    assert result.classification == "awaiting_human"
    assert result.exit_code == L.EXIT_CODES["awaiting_human"]

    status = P.latest_preservation_status(repository)
    assert status.kind == "refused"
    assert "outside authorized scope" in status.data["reason"]
    # BP4a's own classified record is untouched by the refusal.
    _, journal = _checkout_dir_and_journal(repository)
    classified = journal.latest(("classified",))
    assert classified.data["classification"] == "awaiting_human"


def test_status_command_reports_preservation_facts_without_mutating(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _run_with_fake_engine(
        repository, tmp_path, "awaiting_human", commission_id="bp4b-status", dirty_file="src/wip.txt",
    )
    assert result.classification == "awaiting_human"

    _, journal = _checkout_dir_and_journal(repository)
    before = journal.read_all()

    status_result = L.status_lifecycle(repository)
    assert status_result.classification == "awaiting_human"
    reported = P.latest_preservation_status(repository)
    assert reported.kind == "result"

    after = journal.read_all()
    assert before == after  # status is read-only: no new journal record.
