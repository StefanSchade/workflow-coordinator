"""BP6c integration: archive/audit/report wired onto the installed lifecycle.

Uses the same fake-engine/fake-provider fixtures as
``test_lifecycle_crash_matrix.py``/``test_preservation_lifecycle.py`` to
drive real ``wfc run``/``wfc resume``/``wfc status`` calls through to a
terminal classification, then asserts the post-run archive/audit/report step
behaves as specified -- while independently pinning that BP4a's own
classification and exit codes are unaffected. Every Consumer Config here
names an explicit, ``tmp_path``-scoped ``evidence.repository`` so this file
never resolves the real default Evidence location -- except the one test
that deliberately exercises that resolution failing (no ``evidence.repository``
configured, ``$HOME``/``$XDG_DATA_HOME`` both unset), which never reads or
writes anything under a real default location since resolution itself
refuses first. The Attempt identity
used throughout is the maintainer-decided, Run-derived one
(``<repository_name>-<run_id>``, lower-cased), never the engine state root's
own ``engine-state`` basename.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from workflow_coordinator.frontdoor import archive as A
from workflow_coordinator.frontdoor import journal as J
from workflow_coordinator.frontdoor import lifecycle as L
from workflow_coordinator.frontdoor import postrun as PR
from workflow_coordinator.frontdoor.cli import main as cli_main

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "bp4"
FAKE_PROVIDER = FIXTURES / "fake_provider.py"
FAKE_ENGINE = FIXTURES / "fake_engine.py"


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
    os.environ.pop("WFC_FAKE_ENGINE_RUN_ID", None)


@pytest.fixture
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(home))
    return home / "wfc"


def _seed_repository(tmp_path: Path, *, name: str = "consumer-repo") -> Path:
    repo = tmp_path / name
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
    return repo


@pytest.fixture
def evidence_path(tmp_path: Path) -> Path:
    return tmp_path / "evidence"


@pytest.fixture
def repository(tmp_path: Path, evidence_path: Path) -> Path:
    """A fresh consumer repository with a Consumer Config naming an explicit,
    ``tmp_path``-scoped Evidence repository (``raw: local``, the default) --
    never the real default Evidence location."""

    repo = _seed_repository(tmp_path)
    assert cli_main([
        "init", "--repository", str(repo), "--scope-ceiling", "src",
        "--evidence-repository", str(evidence_path),
    ]) == 0
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: commit Consumer Config")
    return repo


def _repository_with_raw(tmp_path: Path, evidence_path: Path, raw: str, *, name: str = "consumer-repo") -> Path:
    repo = _seed_repository(tmp_path, name=name)
    args = [
        "init", "--repository", str(repo), "--scope-ceiling", "src",
        "--evidence-repository", str(evidence_path),
    ]
    if raw != "local":
        args += ["--evidence-raw", raw]
    assert cli_main(args) == 0
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: commit Consumer Config")
    return repo


def _write_request(repository: Path, path: Path, *, commission_id: str = "bp6c") -> Path:
    head = git(repository, "rev-parse", "HEAD")
    data = {
        "schema_version": 1, "commission_id": commission_id, "work_item_id": "WI-BP6C",
        "authority_source": "test authorizes this bounded lifecycle.", "program": {"id": "simple", "revision": "v1"},
        "task": {
            "title": "postrun task", "body": "exercise BP6c archive/audit/report wiring",
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
    repository: Path, tmp_path: Path, mode: str, *, commission_id: str = "bp6c", dirty_file: str | None = None,
    run_id: str | None = None,
) -> L.LifecycleResult:
    os.environ["WFC_FAKE_ENGINE_MODE"] = mode
    if dirty_file:
        os.environ["WFC_FAKE_ENGINE_DIRTY_FILE"] = dirty_file
    else:
        os.environ.pop("WFC_FAKE_ENGINE_DIRTY_FILE", None)
    if run_id:
        os.environ["WFC_FAKE_ENGINE_RUN_ID"] = run_id
    else:
        os.environ.pop("WFC_FAKE_ENGINE_RUN_ID", None)
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    request_path = _write_request(repository, tmp_path / "request.yaml", commission_id=commission_id)
    inputs = _inputs(repository, request_path)
    return L.run_lifecycle(inputs)


def _checkout_dir_and_journal(repository: Path) -> tuple[Path, J.Journal]:
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    return checkout_dir, J.Journal(checkout_dir / "journal.jsonl")


def _attempt_identity(repository: Path, run_id: str = "RUN-fake-engine") -> str:
    from workflow_coordinator.frontdoor.config import consumer_config_from_yaml

    config_text = (repository / L.DEFAULT_CONFIG_PATH).read_text(encoding="utf-8")
    config = consumer_config_from_yaml(config_text)
    return PR._attempt_identity(config.repository_name, run_id)


def _dest(evidence_path: Path, repository: Path, *, run_id: str = "RUN-fake-engine") -> Path:
    return evidence_path / "archive" / "unclassified" / _attempt_identity(repository, run_id)


def _read_audit(evidence_path: Path, repository: Path, *, run_id: str = "RUN-fake-engine") -> dict:
    return json.loads((_dest(evidence_path, repository, run_id=run_id) / "audit.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Identity decision: never the engine state root's own basename.
# --------------------------------------------------------------------------

def test_attempt_identity_is_run_derived_never_the_state_root_basename(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    result = _run_with_fake_engine(repository, tmp_path, "completed_consistent", commission_id="bp6c-identity")
    assert result.classification == "completed"

    _, journal = _checkout_dir_and_journal(repository)
    fact = journal.latest(("ext.archive.result",))
    assert fact is not None
    # Never the BP4a state-root basename, which is always literally "engine-state".
    assert "engine-state" not in fact.data["destination_relative"]
    assert fact.data["destination_relative"] == f"archive/unclassified/{_attempt_identity(repository)}"


# --------------------------------------------------------------------------
# Acceptance 1/7: a completed Run is archived once, audit carries lifecycle
# facts, the report is printed, BP4a's exit code is unchanged.
# --------------------------------------------------------------------------

def test_completed_run_archives_once_with_audit_and_unchanged_exit_code(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    result = _run_with_fake_engine(repository, tmp_path, "completed_consistent", commission_id="bp6c-completed")
    assert result.classification == "completed"
    assert result.exit_code == L.EXIT_CODES["completed"]
    assert result.postrun_report_text is not None
    assert "WFC RUN" in result.postrun_report_text

    _, journal = _checkout_dir_and_journal(repository)
    fact = journal.latest(("ext.archive.result",))
    assert fact is not None
    assert fact.data["retention"] == "local"
    assert journal.count("ext.archive.result") == 1

    audit = _read_audit(evidence_path, repository)
    assert audit["lifecycle"]["classification"] == "completed"
    assert audit["lifecycle"]["preservation"] is None  # never triggered for a completed Run.
    assert git(evidence_path, "status", "--porcelain=v1") == ""  # the archive commit left it clean.


# --------------------------------------------------------------------------
# Acceptance 2: stopped/interrupted Runs (and the budget/reconcile variants)
# are archived and truthfully reported, never as success, with BP4a's own
# exit code unchanged.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mode,expected", [
    ("awaiting_human", "awaiting_human"),
    ("awaiting_human_budget", "awaiting_human_budget"),
    ("reconcile_required", "reconcile_required"),
    ("stopped", "stopped"),
    ("dispatch_then_crash", "interrupted"),
])
def test_stopped_and_interrupted_runs_are_archived_and_reported_truthfully(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path, mode: str, expected: str,
):
    result = _run_with_fake_engine(repository, tmp_path, mode, commission_id=f"bp6c-{mode}")
    assert result.classification == expected
    assert result.exit_code == L.EXIT_CODES[expected]  # BP4a's own exit code, unchanged.

    audit = _read_audit(evidence_path, repository)
    assert audit["lifecycle"]["classification"] == expected
    report_text = result.postrun_report_text
    assert report_text is not None
    assert f"outcome     {expected}" in report_text
    assert "outcome     completed" not in report_text


# --------------------------------------------------------------------------
# Acceptance 7: the audit carries the BP4b preservation fact, distinctly, for
# all three of its outcomes (result, refused, not_needed) -- each driven
# through the real lifecycle so the specific shape BP4b actually produces
# lands, unmodified, under the archive's own ``lifecycle.preservation``.
# --------------------------------------------------------------------------

def test_audit_preservation_is_not_needed_for_a_clean_stopped_run(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    result = _run_with_fake_engine(repository, tmp_path, "stopped", commission_id="bp6c-preservation-not-needed")
    assert result.classification == "stopped"

    _, journal = _checkout_dir_and_journal(repository)
    assert journal.latest(("ext.preservation.not_needed",)) is not None

    audit = _read_audit(evidence_path, repository)
    assert audit["lifecycle"]["preservation"] == {
        "outcome": "not_needed", "ref": None, "commit": None, "tree": None,
        "file_count": None, "bytes": None, "quiescence": None, "reason_code": None,
    }


def test_audit_preservation_is_refused_for_an_out_of_scope_dirty_worktree(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    result = _run_with_fake_engine(
        repository, tmp_path, "awaiting_human", commission_id="bp6c-preservation-refused",
        dirty_file="outside-scope.txt",
    )
    assert result.classification == "awaiting_human"

    _, journal = _checkout_dir_and_journal(repository)
    preserved = journal.latest(("ext.preservation.refused",))
    assert preserved is not None
    assert "outside authorized scope" in preserved.data["reason"]

    audit = _read_audit(evidence_path, repository)
    assert audit["lifecycle"]["preservation"] == {
        "outcome": "refused", "ref": None, "commit": None, "tree": None,
        "file_count": None, "bytes": None, "quiescence": None, "reason_code": preserved.data["reason"],
    }


def test_audit_preservation_is_result_for_an_interrupted_run_with_an_in_scope_dirty_worktree(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    result = _run_with_fake_engine(
        repository, tmp_path, "dispatch_then_crash", commission_id="bp6c-preservation-result",
        dirty_file="src/wip.txt",
    )
    assert result.classification == "interrupted"

    _, journal = _checkout_dir_and_journal(repository)
    preserved = journal.latest(("ext.preservation.result",))
    assert preserved is not None

    audit = _read_audit(evidence_path, repository)
    assert audit["lifecycle"]["preservation"]["outcome"] == "result"
    assert audit["lifecycle"]["preservation"]["ref"] == preserved.data["ref"]
    assert audit["lifecycle"]["preservation"]["commit"] == preserved.data["commit"]
    assert audit["lifecycle"]["preservation"]["quiescence"] == "unattested"


# --------------------------------------------------------------------------
# Acceptance 3: no engine state -> no archive at all.
# --------------------------------------------------------------------------

def test_engine_exited_before_state_has_no_archive(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    result = _run_with_fake_engine(repository, tmp_path, "exit_immediately", commission_id="bp6c-no-state")
    assert result.classification == "engine_exited_before_state"
    assert result.exit_code == L.EXIT_CODES["engine_exited_before_state"]
    assert result.postrun_notice is None
    assert result.postrun_report_text is None

    _, journal = _checkout_dir_and_journal(repository)
    assert journal.latest(("ext.archive.intent", "ext.archive.result", "ext.archive.refused", "ext.archive.skipped")) is None
    assert not evidence_path.joinpath("archive").exists() or not any((evidence_path / "archive").rglob("audit.json"))


def test_absent_classified_record_is_a_postrun_no_op():
    # A non-terminal lifecycle (never classified) must never reach for the
    # Consumer Config or the Evidence repository at all.
    class _ExplodingJournal:
        def latest(self, kinds):
            assert kinds == ("classified",)
            return None

    status = PR.run_pending_postrun(Path("/nonexistent"), Path("/nonexistent"), _ExplodingJournal())
    assert status.kind == "absent"


# --------------------------------------------------------------------------
# Acceptance 4: Evidence repository absent is a loud skip, never a failure.
# --------------------------------------------------------------------------

def test_evidence_repository_absent_is_skipped_not_a_failure(
    tmp_path: Path, state_home: Path,
):
    repo = _seed_repository(tmp_path)
    absent_evidence = tmp_path / "never-created-evidence"
    assert cli_main([
        "init", "--repository", str(repo), "--scope-ceiling", "src",
        "--evidence-repository", str(absent_evidence), "--no-evidence-repository",
    ]) == 0
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: commit Consumer Config")

    result = _run_with_fake_engine(repo, tmp_path, "completed_consistent", commission_id="bp6c-absent")
    assert result.classification == "completed"
    assert result.exit_code == L.EXIT_CODES["completed"]  # unchanged: a skip is not a failure.
    assert result.postrun_notice is not None
    assert "ARCHIVE SKIPPED" in result.postrun_notice
    assert "wfc init" in result.postrun_notice
    assert not absent_evidence.exists()

    _, journal = _checkout_dir_and_journal(repo)
    fact = journal.latest(("ext.archive.skipped",))
    assert fact is not None
    assert fact.data == {"reason": "evidence_repository_absent"}


# --------------------------------------------------------------------------
# Acceptance 5/6: a genuine destination conflict refuses with the additive
# exit code, the engine classification stands, and `wfc resume` retries and
# succeeds once the cause is removed.
# --------------------------------------------------------------------------

def test_archive_conflict_is_refused_then_resume_succeeds_once_removed(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    # Seed a conflicting prior archive at the exact destination this Run will
    # use, with different content, so the real archive step must refuse.
    identity = _attempt_identity(repository)
    foreign_state_root = tmp_path / "foreign" / "engine-state"
    foreign_state_root.mkdir(parents=True)
    (foreign_state_root / "operator-plan.json").write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    (foreign_state_root / "operator-summary.json").write_text(
        json.dumps({"run_id": "RUN-foreign", "run_status": "completed"}), encoding="utf-8",
    )
    checkouts = foreign_state_root / "checkouts" / "11111111-2222-3333-4444-555555555555"
    checkouts.mkdir(parents=True)
    (checkouts / "run.json").write_text(json.dumps({"run_id": "RUN-foreign", "status": "completed"}), encoding="utf-8")
    # ``repository`` already caused ``wfc init`` to create ``evidence_path``.
    A.archive_run(
        state_root=foreign_state_root, evidence_repo=evidence_path, raw_retention="local",
        attempt_identity=identity,
    )

    result = _run_with_fake_engine(repository, tmp_path, "completed_consistent", commission_id="bp6c-conflict")
    assert result.classification == "completed"  # the engine outcome stands.
    assert result.exit_code == L.ARCHIVE_FAILED_EXIT_CODE
    assert result.postrun_notice is not None
    assert "ARCHIVE FAILED" in result.postrun_notice

    _, journal = _checkout_dir_and_journal(repository)
    fact = journal.latest(("ext.archive.refused",))
    assert fact is not None
    assert fact.data == {"reason_code": "archive_refused"}
    for key in fact.data.values():
        assert "/" not in str(key)  # no absolute path or path fragment in the record.

    # Remove the cause, then `wfc resume` retries and succeeds.
    shutil.rmtree(_dest(evidence_path, repository))
    git(evidence_path, "add", "-A")
    git(evidence_path, "commit", "-q", "-m", "chore: remove conflicting foreign archive")

    resumed = L.resume_lifecycle(repository)
    assert resumed.classification == "completed"
    assert resumed.exit_code == L.EXIT_CODES["completed"]
    _, journal = _checkout_dir_and_journal(repository)
    assert journal.latest(("ext.archive.result",)) is not None


# --------------------------------------------------------------------------
# Acceptance 5: every failure shape the post-run step can hit is a named
# ``ext.archive.refused``, never an unhandled exception that would hide the
# engine's own completed classification behind an unrelated exit code.
# --------------------------------------------------------------------------

def test_non_writable_evidence_repository_is_refused_not_an_uncaught_exception(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    # The destination is a real, existing Evidence repository, but this
    # process cannot write into it (a non-writable directory, not a missing
    # one -- the ``ARCHIVE SKIPPED`` path does not apply).
    original_mode = evidence_path.stat().st_mode
    os.chmod(evidence_path, 0o500)
    try:
        result = _run_with_fake_engine(repository, tmp_path, "completed_consistent", commission_id="bp6c-no-write")
    finally:
        os.chmod(evidence_path, original_mode)

    assert result.classification == "completed"  # the engine outcome stands.
    assert result.exit_code == L.ARCHIVE_FAILED_EXIT_CODE
    assert result.postrun_notice is not None
    assert "ARCHIVE FAILED" in result.postrun_notice

    _, journal = _checkout_dir_and_journal(repository)
    fact = journal.latest(("ext.archive.refused",))
    assert fact is not None
    assert fact.data == {"reason_code": "archive_io_error"}

    resumed = L.resume_lifecycle(repository)
    assert resumed.classification == "completed"
    assert resumed.exit_code == L.EXIT_CODES["completed"]
    _, journal = _checkout_dir_and_journal(repository)
    assert journal.latest(("ext.archive.result",)) is not None


def test_unresolvable_default_evidence_location_is_refused_not_an_uncaught_exception(
    tmp_path: Path, state_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    # No ``evidence.repository`` configured at all, so the step must resolve
    # the default location (``<data home>/wfc/evidence/<repository_name>``)
    # itself -- and that resolution fails with neither $XDG_DATA_HOME nor
    # $HOME set, exactly as ``frontdoor.config.resolve_evidence`` documents.
    repo = _seed_repository(tmp_path)
    assert cli_main([
        "init", "--repository", str(repo), "--scope-ceiling", "src", "--no-evidence-repository",
    ]) == 0
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: commit Consumer Config")

    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    result = _run_with_fake_engine(repo, tmp_path, "completed_consistent", commission_id="bp6c-no-home")
    assert result.classification == "completed"  # the engine outcome stands.
    assert result.exit_code == L.ARCHIVE_FAILED_EXIT_CODE
    assert result.postrun_notice is not None
    assert "ARCHIVE FAILED" in result.postrun_notice

    _, journal = _checkout_dir_and_journal(repo)
    fact = journal.latest(("ext.archive.refused",))
    assert fact is not None
    assert fact.data == {"reason_code": "evidence_configuration_error"}
    # No intent was ever recorded: resolution failed before any archive attempt.
    assert journal.latest(("ext.archive.intent",)) is None

    # `wfc resume` retries and succeeds once the cause (an unresolvable
    # default Evidence location) is actually removed: $HOME set to a real
    # path, and the default repository it now resolves to already exists.
    restored_home = tmp_path / "restored-home"
    default_evidence = restored_home / ".local" / "share" / "wfc" / "evidence" / repo.name
    from workflow_coordinator.frontdoor.init import ensure_evidence_repository
    assert ensure_evidence_repository(default_evidence) == "created"
    monkeypatch.setenv("HOME", str(restored_home))

    resumed = L.resume_lifecycle(repo)
    assert resumed.classification == "completed"
    assert resumed.exit_code == L.EXIT_CODES["completed"]
    _, journal = _checkout_dir_and_journal(repo)
    assert journal.latest(("ext.archive.result",)) is not None


# --------------------------------------------------------------------------
# Acceptance 6: idempotency and crash recovery.
# --------------------------------------------------------------------------

def test_repeat_resume_is_idempotent_no_second_commit(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    result = _run_with_fake_engine(repository, tmp_path, "completed_consistent", commission_id="bp6c-repeat")
    assert result.classification == "completed"
    before_count = int(git(evidence_path, "rev-list", "--count", "HEAD"))

    second = L.resume_lifecycle(repository)
    assert second.classification == "completed"
    assert second.exit_code == L.EXIT_CODES["completed"]
    assert second.postrun_report_text is not None  # the read-only print still happens.

    after_count = int(git(evidence_path, "rev-list", "--count", "HEAD"))
    assert after_count == before_count

    _, journal = _checkout_dir_and_journal(repository)
    assert journal.count("ext.archive.result") == 1


def test_crash_after_commit_before_result_record_resumes_without_second_commit(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    checkout, checkout_dir, journal = L._prepare_checkout(repository)

    def authorize():
        return L._authorize(
            _inputs(repository, _write_request(repository, tmp_path / "request.yaml", commission_id="bp6c-crash-after-commit")),
            checkout_dir, journal,
        )

    os.environ["WFC_FAKE_ENGINE_MODE"] = "completed_consistent"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L._advance(repository, checkout_dir, journal, authorize=authorize)
    assert result.classification == "completed"

    classified = journal.latest(("classified",))
    config = PR._cached_config(checkout_dir)
    identity = PR._attempt_identity(config.repository_name, classified.data["run_id"])
    # Simulate a crash right after the archive commit, before the result
    # record: commit the archive directly, bypassing ``run_pending_postrun``.
    archived = A.archive_run(
        state_root=Path(classified.data["state_root"]), evidence_repo=evidence_path,
        raw_retention="local", lifecycle={"classification": "completed", "preservation": None},
        attempt_identity=identity,
    )
    assert archived.outcome == "archived"
    before_count = int(git(evidence_path, "rev-list", "--count", "HEAD"))
    assert journal.latest(("ext.archive.result", "ext.archive.refused", "ext.archive.skipped")) is None

    resumed = L.resume_lifecycle(repository)
    assert resumed.classification == "completed"
    assert resumed.exit_code == L.EXIT_CODES["completed"]
    after_count = int(git(evidence_path, "rev-list", "--count", "HEAD"))
    assert after_count == before_count  # no second commit.

    fact = journal.latest(("ext.archive.result",))
    assert fact is not None
    assert fact.data["outcome"] == "no_op"


def test_crash_before_archive_resumes_and_archives_once(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    checkout, checkout_dir, journal = L._prepare_checkout(repository)

    def authorize():
        return L._authorize(
            _inputs(repository, _write_request(repository, tmp_path / "request.yaml", commission_id="bp6c-crash-before-archive")),
            checkout_dir, journal,
        )

    os.environ["WFC_FAKE_ENGINE_MODE"] = "completed_consistent"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L._advance(repository, checkout_dir, journal, authorize=authorize)
    assert result.classification == "completed"

    # The state a crash right after the intent record (before any archive
    # call at all) would leave.
    journal.append("ext.archive.intent", {"run_id": result.run_id})
    assert PR.latest_postrun_status(repository).kind == "absent"

    resumed = L.resume_lifecycle(repository)
    assert resumed.classification == "completed"
    fact = journal.latest(("ext.archive.result",))
    assert fact is not None
    assert fact.data["outcome"] == "archived"
    assert journal.count("ext.archive.result") == 1


# --------------------------------------------------------------------------
# Acceptance 8: `wfc status` is read-only.
# --------------------------------------------------------------------------

def test_status_reports_archive_facts_without_mutating(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    result = _run_with_fake_engine(repository, tmp_path, "completed_consistent", commission_id="bp6c-status")
    assert result.classification == "completed"

    _, journal = _checkout_dir_and_journal(repository)
    before = journal.read_all()
    evidence_head_before = git(evidence_path, "rev-parse", "HEAD")

    status_result = L.status_lifecycle(repository)
    assert status_result.classification == "completed"
    reported = PR.latest_postrun_status(repository)
    assert reported.kind == "result"

    after = journal.read_all()
    assert before == after
    assert git(evidence_path, "rev-parse", "HEAD") == evidence_head_before


def test_cmd_status_prints_archive_facts(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path, capsys,
):
    _run_with_fake_engine(repository, tmp_path, "completed_consistent", commission_id="bp6c-cmd-status")
    capsys.readouterr()
    exit_code = L.cmd_status(["--repository", str(repository)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "ARCHIVE: result" in captured.out
    assert "retention: local" in captured.out


def test_state_ops_and_scratch_are_byte_identical_before_and_after(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    result = _run_with_fake_engine(repository, tmp_path, "completed_consistent", commission_id="bp6c-no-mutation")
    assert result.classification == "completed"
    state_root = Path(result.state_root)
    files_before = {
        p.relative_to(state_root): p.read_bytes() for p in sorted(state_root.rglob("*")) if p.is_file()
    }

    L.status_lifecycle(repository)
    L.resume_lifecycle(repository)

    files_after = {
        p.relative_to(state_root): p.read_bytes() for p in sorted(state_root.rglob("*")) if p.is_file()
    }
    assert files_before == files_after


# --------------------------------------------------------------------------
# Acceptance 9: every retention mode, verified through ``git ls-files``.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected_names", [
    ("local", {"audit.json"}),
    ("commit", {"audit.json", "manifest.json", ".gitignore", "run/operator-plan.json", "run/operator-summary.json"}),
    ("discard", {"audit.json"}),
])
def test_every_retention_mode_through_the_lifecycle(
    tmp_path: Path, state_home: Path, evidence_path: Path, raw: str, expected_names: set,
):
    repo = _repository_with_raw(tmp_path, evidence_path, raw)
    result = _run_with_fake_engine(repo, tmp_path, "completed_consistent", commission_id=f"bp6c-retention-{raw}")
    assert result.classification == "completed"

    dest = _dest(evidence_path, repo)
    tracked = set(
        git(evidence_path, "ls-files", "--", str(dest.relative_to(evidence_path))).splitlines()
    )
    tracked_relative = {Path(path).relative_to(dest.relative_to(evidence_path)).as_posix() for path in tracked}
    if raw == "commit":
        assert tracked_relative >= {"audit.json", "manifest.json", ".gitignore"}
        assert any(name.startswith("run/") for name in tracked_relative)
    else:
        assert tracked_relative == {"audit.json"}


# --------------------------------------------------------------------------
# Acceptance 10: no absolute path or provider prose in any ext.* record.
# --------------------------------------------------------------------------

def test_ext_records_carry_no_absolute_path_or_provider_prose(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    result = _run_with_fake_engine(repository, tmp_path, "completed_consistent", commission_id="bp6c-no-leak")
    assert result.classification == "completed"

    _, journal = _checkout_dir_and_journal(repository)
    for record in journal.read_all():
        if not record.kind.startswith("ext.archive."):
            continue
        for key, value in record.data.items():
            text = str(value)
            assert not text.startswith("/"), f"{record.kind}.{key} carries an absolute path: {text}"
            assert str(tmp_path) not in text, f"{record.kind}.{key} leaks the test scratch path: {text}"


# --------------------------------------------------------------------------
# Acceptance 13a: two Runs of one consumer, one shared Evidence repository,
# archive without collision, each exactly once.
# --------------------------------------------------------------------------

def test_two_runs_of_the_same_consumer_share_one_evidence_repository_without_collision(
    tmp_path: Path, state_home: Path, evidence_path: Path,
):
    repo_a = _repository_with_raw(tmp_path, evidence_path, "local", name="consumer-repo")
    result_a = _run_with_fake_engine(
        repo_a, tmp_path, "completed_consistent", commission_id="bp6c-two-runs-a", run_id="RUN-" + "a" * 20,
    )
    assert result_a.classification == "completed"

    # A second, distinct checkout of a consumer with the *same* repository
    # name, authorized as a *different* Run (its own Run id), sharing the
    # exact same Evidence repository.
    repo_b_root = tmp_path / "checkout-two"
    repo_b_root.mkdir()
    repo_b = _seed_repository(repo_b_root, name="consumer-repo")
    assert cli_main([
        "init", "--repository", str(repo_b), "--scope-ceiling", "src",
        "--evidence-repository", str(evidence_path), "--repository-name", "consumer-repo",
    ]) == 0
    git(repo_b, "add", ".")
    git(repo_b, "commit", "-q", "-m", "chore: commit Consumer Config")

    result_b = _run_with_fake_engine(
        repo_b, tmp_path, "completed_consistent", commission_id="bp6c-two-runs-b", run_id="RUN-" + "b" * 20,
    )
    assert result_b.classification == "completed"

    dest_a = _dest(evidence_path, repo_a, run_id="RUN-" + "a" * 20)
    dest_b = _dest(evidence_path, repo_b, run_id="RUN-" + "b" * 20)
    assert dest_a != dest_b
    assert (dest_a / "audit.json").is_file()
    assert (dest_b / "audit.json").is_file()
    audit_a = json.loads((dest_a / "audit.json").read_text(encoding="utf-8"))
    audit_b = json.loads((dest_b / "audit.json").read_text(encoding="utf-8"))
    assert audit_a["run"]["run_id"] == "RUN-" + "a" * 20
    assert audit_b["run"]["run_id"] == "RUN-" + "b" * 20

    # A repeated resume of either consumer's checkout is idempotent: no second commit.
    before = int(git(evidence_path, "rev-list", "--count", "HEAD"))
    resumed_a = L.resume_lifecycle(repo_a)
    assert resumed_a.classification == "completed"
    resumed_b = L.resume_lifecycle(repo_b)
    assert resumed_b.classification == "completed"
    after = int(git(evidence_path, "rev-list", "--count", "HEAD"))
    assert after == before
