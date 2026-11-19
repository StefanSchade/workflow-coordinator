"""BP4a crash-injection matrix: every documented step boundary and classification.

Uses ``tests/fixtures/bp4/fake_engine.py`` -- a deterministic stand-in for the
real engine's bare-flag entry point that reproduces its documented
persistence contract up to a selected point -- to pin exact, repeatable
classification and resume outcomes without depending on a real provider or
the real reducer (that real integration is covered separately in
``test_lifecycle_controller.py``). It also exercises a genuine controller
process kill (not a simulation) to prove lock survival and ``engine_active``
reporting across a real process boundary.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from workflow_coordinator.frontdoor import journal as J
from workflow_coordinator.frontdoor import lifecycle as L
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
def _clear_fake_engine_mode():
    yield
    os.environ.pop("WFC_FAKE_ENGINE_MODE", None)


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


def _write_request(repository: Path, path: Path, *, commission_id: str = "bp4-crash") -> Path:
    head = git(repository, "rev-parse", "HEAD")
    data = {
        "schema_version": 1, "commission_id": commission_id, "work_item_id": "WI-BP4-CRASH",
        "authority_source": "test authorizes this bounded lifecycle.", "program": {"id": "simple", "revision": "v1"},
        "task": {
            "title": "crash matrix task", "body": "exercise BP4a crash injection",
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


def _inputs(repository: Path, request_path: Path, *, commission_id: str = "bp4-crash") -> L.RunInputs:
    return L.RunInputs(
        repository=repository, config_path=L.DEFAULT_CONFIG_PATH, request_path=request_path,
        target_dir=None, overrides=_OVERRIDES, executable=str(FAKE_PROVIDER),
        hop_timeout_seconds=60.0, confirm=lambda a, b: True,
    )


def _run_with_fake_engine(repository: Path, tmp_path: Path, mode: str, *, commission_id="bp4-crash") -> L.LifecycleResult:
    os.environ["WFC_FAKE_ENGINE_MODE"] = mode
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    request_path = _write_request(repository, tmp_path / "request.yaml", commission_id=commission_id)
    inputs = _inputs(repository, request_path, commission_id=commission_id)
    return L.run_lifecycle(inputs)


# --------------------------------------------------------------------------
# Classification matrix via the fake engine.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mode,expected", [
    ("awaiting_human", "awaiting_human"),
    ("awaiting_human_budget", "awaiting_human_budget"),
    ("reconcile_required", "reconcile_required"),
    ("stopped", "stopped"),
    ("completed_consistent", "completed"),
    ("dispatch_then_crash", "interrupted"),
    ("exit_immediately", "engine_exited_before_state"),
])
def test_classification_matrix(repository: Path, tmp_path: Path, state_home: Path, mode: str, expected: str):
    result = _run_with_fake_engine(repository, tmp_path, mode, commission_id=f"bp4-{mode}")
    assert result.classification == expected
    assert result.exit_code == L.EXIT_CODES[expected]


def test_completed_status_without_summary_file_is_inconsistent_not_completed(repository: Path, tmp_path: Path, state_home: Path):
    """Summary-file existence alone must never prove completion."""

    with pytest.raises(L.LifecycleError) as excinfo:
        _run_with_fake_engine(repository, tmp_path, "completed_missing_summary", commission_id="bp4-missing-summary")
    assert excinfo.value.classification == "inconsistent"


def test_completed_status_with_inconsistent_summary_refuses(repository: Path, tmp_path: Path, state_home: Path):
    with pytest.raises(L.LifecycleError) as excinfo:
        _run_with_fake_engine(repository, tmp_path, "completed_inconsistent_summary", commission_id="bp4-bad-summary")
    assert excinfo.value.classification == "inconsistent"


def test_state_root_present_without_run_json_is_inconsistent(repository: Path, tmp_path: Path, state_home: Path):
    with pytest.raises(L.LifecycleError) as excinfo:
        _run_with_fake_engine(repository, tmp_path, "mkdir_only", commission_id="bp4-mkdir-only")
    assert excinfo.value.classification == "inconsistent"


def test_interrupted_run_is_not_relaunched_and_repository_is_untouched_by_controller(repository: Path, tmp_path: Path, state_home: Path):
    result = _run_with_fake_engine(repository, tmp_path, "dispatch_then_crash", commission_id="bp4-interrupted")
    assert result.classification == "interrupted"
    head_after_classification = git(repository, "rev-parse", "HEAD")
    second = L.resume_lifecycle(repository)
    assert second.classification == "interrupted"
    assert git(repository, "rev-parse", "HEAD") == head_after_classification


# --------------------------------------------------------------------------
# Crash injection at each controller step boundary (resume from journal).
# --------------------------------------------------------------------------

def _authorize_only(repository: Path, request_path: Path, commission_id="bp4-crash"):
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    journal = J.Journal(checkout_dir / "journal.jsonl")
    inputs = _inputs(repository, request_path, commission_id=commission_id)
    authorized, lock = L._authorize(inputs, checkout_dir, journal)
    return checkout_dir, journal, authorized, lock


def test_crash_immediately_after_authorize_resume_continues_to_completion(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout_dir, journal, authorized, lock = _authorize_only(repository, request_path)
    J.release_lock(lock)  # simulate a crash: nothing further has happened.

    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L.resume_lifecycle(repository)
    assert result.classification == "awaiting_human"
    assert journal.latest(("prelaunch_passed",)) is not None
    assert journal.latest(("probe_passed",)) is not None
    assert journal.latest(("commission_committed",)) is not None
    assert journal.latest(("verify_passed",)) is not None


def test_crash_after_prelaunch_resume_continues_without_rechecking_from_scratch(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout_dir, journal, authorized, lock = _authorize_only(repository, request_path)
    prelaunch = L._do_prelaunch(repository, checkout_dir, journal, authorized)
    J.release_lock(lock)

    os.environ["WFC_FAKE_ENGINE_MODE"] = "stopped"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L.resume_lifecycle(repository)
    assert result.classification == "stopped"


def test_crash_after_probe_before_commit_resume_commits_exactly_once(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout_dir, journal, authorized, lock = _authorize_only(repository, request_path)
    prelaunch = L._do_prelaunch(repository, checkout_dir, journal, authorized)
    probe = L._do_probe(repository, checkout_dir, journal, authorized)
    J.release_lock(lock)

    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L.resume_lifecycle(repository)
    assert result.classification == "awaiting_human"
    commission_subjects = [line for line in git(repository, "log", "--oneline").splitlines() if "commission" in line]
    assert len(commission_subjects) == 1


def test_crash_after_verify_before_launch_resume_continues_with_no_recommit(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout_dir, journal, authorized, lock = _authorize_only(repository, request_path)
    prelaunch = L._do_prelaunch(repository, checkout_dir, journal, authorized)
    probe = L._do_probe(repository, checkout_dir, journal, authorized)
    commit_rec = L._do_commit(repository, checkout_dir, journal, authorized)
    L._do_verify(repository, checkout_dir, journal, authorized, commit_rec)
    head_after_commit = commit_rec.data["commit"]
    J.release_lock(lock)

    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L.resume_lifecycle(repository)
    assert result.classification == "awaiting_human"
    assert git(repository, "rev-parse", "HEAD") == head_after_commit
    commission_subjects = [line for line in git(repository, "log", "--oneline").splitlines() if "commission" in line]
    assert len(commission_subjects) == 1


def test_launch_failed_no_engine_proves_no_engine_exists_and_bounds_relaunch(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path)
    L._ENGINE_COMMAND = ("/nonexistent/no-such-engine-binary",)

    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "launch_failed_no_engine"

    for _ in range(2):
        with pytest.raises(L.LifecycleError) as excinfo:
            L.resume_lifecycle(repository)
        assert excinfo.value.classification == "launch_failed_no_engine"

    with pytest.raises(L.LifecycleError) as excinfo:
        L.resume_lifecycle(repository)
    assert excinfo.value.classification == "refused_launch_exhausted"


# --------------------------------------------------------------------------
# A genuine controller process kill: the engine child must survive and keep
# the lock; a fresh resume process must report engine_active and spawn
# nothing new.
# --------------------------------------------------------------------------

_DRIVER_TEMPLATE = """
import sys
sys.path.insert(0, {src!r})
from pathlib import Path
from workflow_coordinator.frontdoor import lifecycle as L

L._ENGINE_COMMAND = (sys.executable, {fake_engine!r})
inputs = L.RunInputs(
    repository=Path({repository!r}), config_path=".wfc/config.yaml", request_path=Path({request_path!r}),
    target_dir=None, overrides={{"worker": {{"adapter": "codex_cli", "model": "m", "reasoning": "medium"}}}},
    executable={executable!r}, hop_timeout_seconds=600.0, confirm=lambda a, b: True,
)
result = L.run_lifecycle(inputs)
print("DRIVER RESULT:", result.classification)
"""


def test_killed_controller_leaves_a_surviving_engine_holding_the_lock_and_resume_reports_engine_active(
    repository: Path, tmp_path: Path, state_home: Path,
):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    src_dir = str(Path(__file__).resolve().parents[2] / "src")
    driver = tmp_path / "driver.py"
    driver.write_text(
        _DRIVER_TEMPLATE.format(
            src=src_dir, fake_engine=str(FAKE_ENGINE), repository=str(repository),
            request_path=str(request_path), executable=str(FAKE_PROVIDER),
        ),
        encoding="utf-8",
    )

    env = {**os.environ, "WFC_FAKE_ENGINE_MODE": "dispatch_then_hang"}
    controller = subprocess.Popen([sys.executable, str(driver)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)

    deadline = time.time() + 20
    engine_state_root = checkout_dir / "engine-state"
    sentinel = engine_state_root / "fake-engine.pid"
    while not sentinel.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert sentinel.exists(), "the fake engine never reached its hang point"
    engine_pid = int(sentinel.read_text(encoding="utf-8"))

    try:
        controller.send_signal(signal.SIGKILL)
        controller.wait(timeout=10)

        deadline = time.time() + 5
        while J.query_lock_held(checkout_dir) is False and time.time() < deadline:
            time.sleep(0.05)
        assert J.query_lock_held(checkout_dir) is True, "the surviving engine must still hold the lock"

        result = L.resume_lifecycle(repository)
        assert result.classification == "engine_active"

        # No second engine and no second state root were created.
        assert sum(1 for _ in (checkout_dir).glob("engine-state*")) == 1
    finally:
        try:
            os.kill(engine_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        deadline = time.time() + 10
        while J.query_lock_held(checkout_dir) and time.time() < deadline:
            time.sleep(0.05)

    final = L.resume_lifecycle(repository)
    assert final.classification == "interrupted"
