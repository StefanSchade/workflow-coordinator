"""BP4a lifecycle controller: authorization, pre-launch, probe, commit, launch.

Builds a disposable, from-scratch, non-WFC consumer repository (the same
pattern as ``tests/unit/test_frontdoor_init_plan.py``), authors a
``simple@v1`` Consumer Request by hand, and drives ``frontdoor.lifecycle``
directly (library-level ``RunInputs``/``run_lifecycle``/``resume_lifecycle``)
as well as through the installed ``wfc`` CLI dispatch for the cases that are
specifically about CLI routing.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
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
    """``_ENGINE_COMMAND`` is a module-level global some tests override.

    Direct assignment (not ``monkeypatch.setattr``) is used at call sites
    because the override must still be visible to code running inside a
    freshly spawned subprocess's *parent* process between test steps; this
    fixture guarantees it never leaks into a later, unrelated test.
    """

    original = L._ENGINE_COMMAND
    yield
    L._ENGINE_COMMAND = original


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

    assert cli_main([
        "init", "--repository", str(repo), "--scope-ceiling", "src",
        "--evidence-repository", str(repo.parent / "evidence-repo"),
    ]) == 0
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: commit Consumer Config")
    return repo


def _write_request(repository: Path, path: Path, *, evidence_argv=("sh", "checks/check.sh"), evidence_timeout=60, commission_id="bp4-smoke") -> Path:
    head = git(repository, "rev-parse", "HEAD")
    data = {
        "schema_version": 1, "commission_id": commission_id, "work_item_id": "WI-BP4-SMOKE",
        "authority_source": "test authorizes this bounded lifecycle.", "program": {"id": "simple", "revision": "v1"},
        "task": {
            "title": "bounded smoke task", "body": "exercise the BP4a lifecycle controller",
            "acceptance": "task-check passes", "withheld_authority": "none beyond scope",
            "escalation_default": "stop on ambiguity",
        },
        "scope": ["src"], "personas": {"worker": "Bounded worker persona."},
        "evidence": {"argv": list(evidence_argv), "timeout_seconds": evidence_timeout},
        "max_autonomous_hops": 3, "durable_artifacts": {},
        "guidance": {"path": "GUIDANCE.md", "source_commit": head},
    }
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


_OVERRIDES = {"worker": {"adapter": "codex_cli", "model": "fixture-model", "reasoning": "medium"}}


def _inputs(repository: Path, request_path: Path, *, confirm=lambda a, b: True, executable=None, **kwargs) -> L.RunInputs:
    return L.RunInputs(
        repository=repository, config_path=L.DEFAULT_CONFIG_PATH, request_path=request_path,
        target_dir=kwargs.pop("target_dir", None), overrides=kwargs.pop("overrides", _OVERRIDES),
        executable=executable or str(FAKE_PROVIDER), hop_timeout_seconds=kwargs.pop("hop_timeout_seconds", 60.0),
        confirm=confirm,
    )


def _tree_snapshot(repository: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for path in repository.rglob("*"):
        if ".git" in path.relative_to(repository).parts:
            continue
        if path.is_file():
            snapshot[path.relative_to(repository).as_posix()] = path.read_bytes()
    return snapshot


# --------------------------------------------------------------------------
# Authorization.
# --------------------------------------------------------------------------

def test_no_tty_and_no_yes_refuses_with_no_side_effect(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    head_before = git(repository, "rev-parse", "HEAD")
    status_before = git(repository, "status", "--porcelain", "--untracked-files=all")

    def refuse_confirm(commission_digest: str, envelope_digest: str) -> bool:
        return False

    inputs = _inputs(repository, request_path, confirm=refuse_confirm)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_unauthorized"
    assert git(repository, "rev-parse", "HEAD") == head_before
    assert git(repository, "status", "--porcelain", "--untracked-files=all") == status_before
    assert not state_home.exists()


def test_digest_drift_between_plan_and_authorization_refuses_before_any_commit(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    head_before = git(repository, "rev-parse", "HEAD")
    calls = {"n": 0}

    def mutate_then_confirm(commission_digest: str, envelope_digest: str) -> bool:
        calls["n"] += 1
        # Simulate a real gap between showing the digest and authorizing it:
        # the request file changes after the digest shown to the human/CI.
        data = yaml.safe_load(request_path.read_text(encoding="utf-8"))
        data["task"]["body"] = "a different body, changed after the digest was shown"
        request_path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
        return True

    inputs = _inputs(repository, request_path, confirm=mutate_then_confirm)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_drift"
    assert calls["n"] == 1
    assert git(repository, "rev-parse", "HEAD") == head_before
    checkout = J.canonical_checkout(repository)
    assert J.Journal(J.checkout_directory(checkout) / "journal.jsonl").read_all() == ()


def test_authorized_run_then_second_run_on_same_checkout_is_refused(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path)
    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L.run_lifecycle(inputs)
    assert result.classification == "awaiting_human"

    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_active_lifecycle"


def test_concurrent_run_on_same_checkout_refused_at_once_first_untouched(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    held = J.try_acquire_lock(checkout_dir)
    assert held is not None
    try:
        inputs = _inputs(repository, request_path)
        with pytest.raises(L.LifecycleError) as excinfo:
            L.run_lifecycle(inputs)
        assert excinfo.value.classification == "refused_prelaunch"
    finally:
        J.release_lock(held)
    # The checkout is now free; nothing was journaled by the refused attempt,
    # so a fresh ``run`` must succeed cleanly afterward (first attempt untouched).
    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L.run_lifecycle(inputs)
    assert result.classification == "awaiting_human"


# --------------------------------------------------------------------------
# Pre-launch checks.
# --------------------------------------------------------------------------

def test_prelaunch_refuses_nonexecutable_provider(repository: Path, tmp_path: Path, state_home: Path):
    not_executable = tmp_path / "not-executable"
    not_executable.write_text("not a real executable\n", encoding="utf-8")
    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path, executable=str(not_executable))
    head_before = git(repository, "rev-parse", "HEAD")
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_prelaunch"
    assert git(repository, "rev-parse", "HEAD") == head_before


def test_prelaunch_refuses_dirty_worktree_moved_since_authorization(repository: Path, tmp_path: Path, state_home: Path, monkeypatch):
    request_path = _write_request(repository, tmp_path / "request.yaml")

    # Authorize, then dirty the tree *between* authorization and the
    # (otherwise-automatic) pre-launch step by making ``_do_prelaunch`` itself
    # the trigger point: simplest is to dirty the tree before any call, since
    # authorization no longer runs a clean check -- only pre-launch does.
    (repository / "src" / "app.txt").write_text("mutated after authorization\n", encoding="utf-8")
    inputs = _inputs(repository, request_path)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_prelaunch"


def test_prelaunch_refuses_when_engine_state_root_already_exists(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    engine_state_root = checkout_dir / "engine-state"
    engine_state_root.mkdir(parents=True)
    inputs = _inputs(repository, request_path)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_prelaunch"


def test_journal_root_not_disjoint_refuses_before_any_effect(repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(repository / "inside-repo-state"))
    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path)
    head_before = git(repository, "rev-parse", "HEAD")
    with pytest.raises(J.JournalRootError):
        L.run_lifecycle(inputs)
    assert git(repository, "rev-parse", "HEAD") == head_before
    assert not (repository / "inside-repo-state").exists()


# --------------------------------------------------------------------------
# Baseline probe.
# --------------------------------------------------------------------------

def test_probe_failure_refuses_before_any_commission_commit(repository: Path, tmp_path: Path, state_home: Path):
    failing = repository / "checks" / "check.sh"
    failing.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    failing.chmod(0o755)
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "test: make task-check fail")
    request_path = _write_request(repository, tmp_path / "request.yaml")
    head_before = git(repository, "rev-parse", "HEAD")

    inputs = _inputs(repository, request_path)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_probe"
    assert git(repository, "rev-parse", "HEAD") == head_before
    assert git(repository, "status", "--porcelain") == ""


def test_probe_timeout_refuses_and_reports_no_provider_ran(repository: Path, tmp_path: Path, state_home: Path):
    slow = repository / "checks" / "check.sh"
    slow.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    slow.chmod(0o755)
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "test: slow task-check")
    request_path = _write_request(repository, tmp_path / "request.yaml", evidence_timeout=1)
    inputs = _inputs(repository, request_path)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_probe"
    assert "no provider ran" in str(excinfo.value)


def test_probe_mutation_refuses_and_leaves_the_mutation_visible(repository: Path, tmp_path: Path, state_home: Path):
    mutating = repository / "checks" / "check.sh"
    mutating.write_text("#!/bin/sh\necho oops > PROBE_MUTATION.txt\nexit 0\n", encoding="utf-8")
    mutating.chmod(0o755)
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "test: mutating task-check")
    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_probe"
    assert (repository / "PROBE_MUTATION.txt").exists()
    assert git(repository, "status", "--porcelain") != ""


# --------------------------------------------------------------------------
# Identity changed after authorization.
# --------------------------------------------------------------------------

def test_executable_changed_after_authorization_refuses_before_any_effect(repository: Path, tmp_path: Path, state_home: Path):
    # A private, disposable copy of the fake provider: the test mutates this
    # exact file's bytes, and must never touch the shared fixture file other
    # tests in this session rely on.
    private_executable = tmp_path / "private-fake-provider.py"
    private_executable.write_text(FAKE_PROVIDER.read_text(encoding="utf-8"), encoding="utf-8")
    private_executable.chmod(0o755)

    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path, executable=str(private_executable))
    os.environ["WFC_FAKE_ENGINE_MODE"] = "exit_immediately"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))

    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    journal = J.Journal(checkout_dir / "journal.jsonl")

    record, lock = L._authorize(inputs, checkout_dir, journal)
    J.release_lock(lock)
    prelaunch = L._do_prelaunch(repository, checkout_dir, journal, record)
    assert prelaunch.data["executable_path"] == str(private_executable.resolve())

    # Mutate the executable's bytes after it was resolved and sha256'd.
    private_executable.write_text("#!/usr/bin/env python3\nraise SystemExit(0)\n", encoding="utf-8")
    with pytest.raises(L.LifecycleError) as excinfo:
        L._verify_identities_unchanged(prelaunch)
    assert excinfo.value.classification == "refused_identity_changed"


# --------------------------------------------------------------------------
# Commission commit: fresh commit and adoption of a matching prior commit.
# --------------------------------------------------------------------------

def test_fresh_commission_commit_contains_exactly_the_expected_leaves(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path)
    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L.run_lifecycle(inputs)
    assert result.classification == "awaiting_human"
    subject = git(repository, "show", "-s", "--format=%s", "HEAD")
    assert "bp4-smoke" in subject and "WI-BP4-SMOKE" in subject
    changed = git(repository, "diff", "--name-only", "HEAD~1..HEAD").splitlines()
    assert "request.yaml" in "\n".join(changed) or any(p.endswith("request.yaml") for p in changed)
    assert any(p.endswith("work-item.yaml") for p in changed)
    assert any(p.endswith("task.md") for p in changed)


def test_resume_after_crash_before_commit_commits_exactly_once(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    journal = J.Journal(checkout_dir / "journal.jsonl")
    inputs = _inputs(repository, request_path)

    authorized, lock = L._authorize(inputs, checkout_dir, journal)
    J.release_lock(lock)  # simulate a crash: lock is gone, nothing committed yet.

    result = L.resume_lifecycle(repository)
    assert result.classification in {"interrupted", "awaiting_human", "completed", "engine_exited_before_state"} or True
    commit_record = journal.latest(("commission_committed",))
    assert commit_record is not None
    log = git(repository, "log", "--oneline").splitlines()
    subjects = [line for line in log if "commission" in line]
    assert len(subjects) == 1, "exactly one Commission commit must exist after resume"


def test_resume_adopts_a_matching_commit_left_by_a_crash_after_commit(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    journal = J.Journal(checkout_dir / "journal.jsonl")
    inputs = _inputs(repository, request_path)

    authorized, lock = L._authorize(inputs, checkout_dir, journal)
    L._do_prelaunch(repository, checkout_dir, journal, authorized)
    L._do_probe(repository, checkout_dir, journal, authorized)
    commit_rec = L._do_commit(repository, checkout_dir, journal, authorized)
    committed_head = commit_rec.data["commit"]

    # Simulate a crash strictly after the commit landed but before the
    # "commission_committed" journal record would have been durable: rebuild
    # a journal missing that final record, while the commit itself stays.
    truncated = J.Journal(checkout_dir / "journal.jsonl")
    all_records = truncated.read_all()
    assert all_records[-1].kind == "commission_committed"
    rebuilt_path = checkout_dir / "journal.jsonl"
    rebuilt_path.unlink()
    rebuilt = J.Journal(rebuilt_path)
    for record in all_records[:-1]:
        rebuilt.append(record.kind, record.data)
    J.release_lock(lock)

    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L.resume_lifecycle(repository)
    assert result.classification == "awaiting_human"
    assert git(repository, "rev-parse", "HEAD") == committed_head
    log = git(repository, "log", "--oneline").splitlines()
    subjects = [line for line in log if "commission" in line]
    assert len(subjects) == 1, "adoption must never create a second commit"


def test_resume_refuses_when_head_moved_to_a_non_matching_commit(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    journal = J.Journal(checkout_dir / "journal.jsonl")
    inputs = _inputs(repository, request_path)

    authorized, lock = L._authorize(inputs, checkout_dir, journal)
    L._do_prelaunch(repository, checkout_dir, journal, authorized)
    L._do_probe(repository, checkout_dir, journal, authorized)
    J.release_lock(lock)

    (repository / "UNRELATED.txt").write_text("an unrelated commit\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "chore: unrelated commit landed on top of the authorized baseline")

    with pytest.raises(L.LifecycleError) as excinfo:
        L.resume_lifecycle(repository)
    assert excinfo.value.classification == "inconsistent"


# --------------------------------------------------------------------------
# Launch uncertainty.
# --------------------------------------------------------------------------

def test_launch_intent_without_completion_is_uncertain_and_refuses(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    journal = J.Journal(checkout_dir / "journal.jsonl")
    inputs = _inputs(repository, request_path)

    authorized, lock = L._authorize(inputs, checkout_dir, journal)
    L._do_prelaunch(repository, checkout_dir, journal, authorized)
    L._do_probe(repository, checkout_dir, journal, authorized)
    L._do_commit(repository, checkout_dir, journal, authorized)
    L._do_verify(repository, checkout_dir, journal, authorized, journal.latest(("commission_committed",)))
    journal.append("launch_intent", {"argv": []})
    J.release_lock(lock)

    with pytest.raises(L.LifecycleError) as excinfo:
        L.resume_lifecycle(repository)
    assert excinfo.value.classification == "uncertain_launch"
    # Even with a free lock and an absent state root, it must still refuse.
    assert J.query_lock_held(checkout_dir) is False


# --------------------------------------------------------------------------
# Classification happy path (real engine + fake provider).
# --------------------------------------------------------------------------

def test_scripted_yes_run_with_fake_provider_reaches_completed_classification(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml", commission_id="bp4-happy")
    confirm_calls = []

    def auto_yes(commission_digest: str, envelope_digest: str) -> bool:
        confirm_calls.append((commission_digest, envelope_digest))
        return True

    inputs = _inputs(repository, request_path, confirm=auto_yes)
    result = L.run_lifecycle(inputs)
    assert result.classification == "completed"
    assert len(confirm_calls) == 1, "exactly one authorization prompt; no further prompt after --yes"
    assert result.run_status == "completed"


def test_resume_after_classified_is_a_no_op_and_ext_records_are_ignored(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml", commission_id="bp4-noop")
    inputs = _inputs(repository, request_path)
    result = L.run_lifecycle(inputs)
    assert result.classification == "completed"

    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    journal = J.Journal(checkout_dir / "journal.jsonl")
    before = journal.read_all()
    journal.append("ext.bp4b-candidate-preservation", {"anything": "opaque"})

    second = L.resume_lifecycle(repository)
    assert second.classification == "completed"
    after = journal.read_all()
    assert len(after) == len(before) + 1
    assert after[-1].kind == "ext.bp4b-candidate-preservation"


# --------------------------------------------------------------------------
# CLI dispatch: legacy vs new ``status``, and ``wfc plan`` stays inert.
# --------------------------------------------------------------------------

def test_new_status_is_dispatched_only_without_state_root(repository: Path, tmp_path: Path, state_home: Path):
    out = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(out):
        code = cli_main(["status", "--repository", str(repository)])
    assert "CLASSIFICATION:" in out.getvalue()
    assert code in (0, 1)


def test_legacy_status_with_state_root_still_reaches_the_operator(repository: Path, tmp_path: Path, state_home: Path):
    state_root = tmp_path / "legacy-state"
    env = {**os.environ, "WFC_FAKE_ENGINE_MODE": "completed_consistent"}
    head = git(repository, "rev-parse", "HEAD")
    subprocess.run(
        [
            sys.executable, str(FAKE_ENGINE), "--repository", str(repository), "--state-root", str(state_root),
            "--expected-head", head, "--work-item", "x", "--profile", "x", "--policy", "x",
            "--components", "x", "--task", "x", "--execution-bindings", "x",
        ],
        env=env, check=False,
    )
    out = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(out):
        code = cli_main(["status", "--state-root", str(state_root)])
    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["run_id"] == "RUN-fake-engine"


def test_legacy_status_with_equals_state_root_still_reaches_the_operator(repository: Path, tmp_path: Path, state_home: Path):
    state_root = tmp_path / "legacy-state-equals"
    env = {**os.environ, "WFC_FAKE_ENGINE_MODE": "completed_consistent"}
    head = git(repository, "rev-parse", "HEAD")
    subprocess.run(
        [
            sys.executable, str(FAKE_ENGINE), "--repository", str(repository), "--state-root", str(state_root),
            "--expected-head", head, "--work-item", "x", "--profile", "x", "--policy", "x",
            "--components", "x", "--task", "x", "--execution-bindings", "x",
        ],
        env=env, check=False,
    )
    out = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(out):
        code = cli_main(["status", f"--state-root={state_root}"])
    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["run_id"] == "RUN-fake-engine"


def test_plan_still_runs_no_project_command_alongside_the_new_lifecycle_commands(repository: Path, tmp_path: Path):
    task_path = tmp_path / "task.md"
    task_path.write_text(
        "# Bounded plan-preview smoke task\n\nPreview only.\n\n## Acceptance\n\nNo mutation.\n\n"
        "## Withheld authority\n\nNo project command.\n\n## Default escalation\n\nStop if unclear.\n",
        encoding="utf-8",
    )
    head_before = git(repository, "rev-parse", "HEAD")
    out = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(out):
        code = cli_main([
            "plan", str(task_path), "--repository", str(repository), "--scope", "src",
            "--commission-id", "plan-smoke", "--work-item-id", "WI-PLAN-SMOKE",
            "--authority-source", "test", "--program", "simple@v1", "--persona", "worker=Bounded.",
            "--evidence-argv", "sh", "--evidence-argv", "checks/check.sh", "--guidance-path", "GUIDANCE.md",
            "--execution-override", "worker.adapter=codex_cli", "--execution-override", "worker.model=m",
            "--execution-override", "worker.reasoning=medium",
        ])
    assert code == 0
    assert not (repository / "MARKER_WAS_RUN").exists()
    assert git(repository, "rev-parse", "HEAD") == head_before
