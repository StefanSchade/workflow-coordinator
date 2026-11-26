"""Dispatch-level preflight: no silent fallback when the boundary cannot be
established (N14). This exercises ``operator.run_serial_hops``'s own
``adapter is None`` production path -- the real grant derivation and
preflight checks -- without needing a real `bwrap` run: an unestablishable
boundary is refused before any provider process starts, which is exactly
the property this case proves, and is reachable deterministically wherever
this suite runs.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from workflow_coordinator.model import (
    ExecutionBinding,
    RepositoryPolicy,
    RunExecutionConfiguration,
    WorkflowRole,
    WorkItem,
    new_run,
)
from workflow_coordinator.operator import OperatorPreflightError, _boundary_authentication_path, run_serial_hops
from workflow_coordinator.prompt import load_agent_operating_contract
from workflow_coordinator.slice4 import register_checkout
from tests.support import phase, profile, route


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), check=True, text=True, capture_output=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(("git", "init", str(repository)), check=True, capture_output=True)
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "user.name", "Fixture")
    (repository / "src").mkdir()
    (repository / "src" / "a.txt").write_text("before\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "test: baseline")
    return repository


def _bubblewrap_configuration() -> RunExecutionConfiguration:
    return _bubblewrap_configuration_for("codex_cli")


def _bubblewrap_configuration_for(adapter: str) -> RunExecutionConfiguration:
    return RunExecutionConfiguration(
        schema_version=1,
        bindings=MappingProxyType({WorkflowRole.WORKER: ExecutionBinding(adapter, "fixture-model", "high")}),
        boundary="bubblewrap",
    )


def _direct_configuration() -> RunExecutionConfiguration:
    return RunExecutionConfiguration(
        schema_version=1,
        bindings=MappingProxyType({WorkflowRole.WORKER: ExecutionBinding("codex_cli", "fixture-model", "high")}),
        boundary="direct",
    )


def _new_run(repository: Path, workflow, baseline: str):
    return new_run(
        WorkItem(
            2, "WI-BOUNDARY", "fixture", "profile.yml", "repo", ("src",), "work", 1,
            {WorkflowRole.WORKER: "fixture worker"},
        ),
        workflow,
        RepositoryPolicy(3, "policy", "1", ("repo",), ("governance",), ("src",), 5, ("edit", "commit"), {}),
        run_id="RUN-BOUNDARY",
        baseline_head=baseline,
        boundary="bubblewrap",
    )


def test_new_run_rejects_an_unrecognised_boundary_mode(tmp_path):
    repository = _repository(tmp_path)
    baseline = _git(repository, "rev-parse", "HEAD")
    workflow = profile({"work": replace(phase({"done": route("complete")}), authorized_scope=("src",))})
    with pytest.raises(ValueError, match="unrecognised boundary mode"):
        new_run(
            WorkItem(
                2, "WI-BOUNDARY", "fixture", "profile.yml", "repo", ("src",), "work", 1,
                {WorkflowRole.WORKER: "fixture worker"},
            ),
            workflow,
            RepositoryPolicy(3, "policy", "1", ("repo",), ("governance",), ("src",), 5, ("edit", "commit"), {}),
            run_id="RUN-BOUNDARY-BAD",
            baseline_head=baseline,
            boundary="other",
        )


def test_unestablishable_boundary_refuses_before_any_provider_process_starts(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    baseline = _git(repository, "rev-parse", "HEAD")
    workflow = profile({"work": replace(phase({"done": route("complete")}), authorized_scope=("src",))})
    run = _new_run(repository, workflow, baseline)
    state = tmp_path / "state"
    association = register_checkout(state, repository)

    from workflow_coordinator import operator as operator_module
    monkeypatch.setattr(operator_module.shutil, "which", lambda name: None)

    with pytest.raises(OperatorPreflightError, match="bwrap"):
        run_serial_hops(
            run, workflow, repository, {},
            state_root=state, association=association,
            agent_operating_contract=load_agent_operating_contract(), task_payload="task",
            governing={}, personas={WorkflowRole.WORKER: "fixture worker"}, timeout_seconds=1,
            executable="fake-codex-never-reached",
            execution_configuration=_bubblewrap_configuration(),
        )
    assert not (state / "checkouts" / association.checkout_id / "dispatch.json").exists()
    assert (repository / "src" / "a.txt").read_text() == "before\n"


def test_boundary_selected_without_channel_or_authentication_refuses_before_dispatch(tmp_path, monkeypatch):
    """Even when `bwrap` itself resolves, an incomplete operator-side
    prerequisite (here: no configured authentication location) is a named
    not-started refusal, never a silent fallback to direct dispatch."""
    repository = _repository(tmp_path)
    baseline = _git(repository, "rev-parse", "HEAD")
    workflow = profile({"work": replace(phase({"done": route("complete")}), authorized_scope=("src",))})
    run = _new_run(repository, workflow, baseline)
    state = tmp_path / "state"
    association = register_checkout(state, repository)

    from workflow_coordinator import operator as operator_module
    monkeypatch.delenv("WFC_BOUNDARY_AUTHENTICATION_PATH", raising=False)
    monkeypatch.setattr(operator_module.shutil, "which", lambda name: "/usr/bin/bwrap" if name == "bwrap" else None)
    monkeypatch.setattr(
        operator_module.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, b"", b""),
    )

    with pytest.raises(OperatorPreflightError, match="authentication"):
        run_serial_hops(
            run, workflow, repository, {},
            state_root=state, association=association,
            agent_operating_contract=load_agent_operating_contract(), task_payload="task",
            governing={}, personas={WorkflowRole.WORKER: "fixture worker"}, timeout_seconds=1,
            executable="fake-codex-never-reached",
            execution_configuration=_bubblewrap_configuration(),
        )
    assert not (state / "checkouts" / association.checkout_id / "dispatch.json").exists()


@pytest.mark.parametrize("adapter_name", ("codex_cli", "claude_cli"), ids=("codex", "claude"))
def test_worker_scope_naming_an_absent_file_is_refused_by_name_before_any_provider_process_starts(
    tmp_path, monkeypatch, adapter_name,
):
    """F5: a worker-phase ``authorized_scope`` naming one not-yet-existing
    file, not declared as a durable artifact, is a named preflight refusal
    on both adapters, reached before the `bwrap`-probing checks in
    ``_preflight_boundary`` even run (no `bwrap`/namespace mocking is
    needed here) -- never a silently pre-created directory. Nothing is
    created in the repository, and the per-invocation provider channel
    directory is never created either, since grant derivation is wrapped
    ahead of that creation."""
    repository = _repository(tmp_path)
    baseline = _git(repository, "rev-parse", "HEAD")
    workflow = profile({"work": replace(
        phase({"done": route("complete")}), authorized_scope=("src", "new-file.txt"),
    )})
    run = _new_run(repository, workflow, baseline)
    state = tmp_path / "state"
    association = register_checkout(state, repository)
    monkeypatch.delenv("WFC_BOUNDARY_AUTHENTICATION_PATH", raising=False)

    with pytest.raises(OperatorPreflightError, match="new-file.txt"):
        run_serial_hops(
            run, workflow, repository, {},
            state_root=state, association=association,
            agent_operating_contract=load_agent_operating_contract(), task_payload="task",
            governing={}, personas={WorkflowRole.WORKER: "fixture worker"}, timeout_seconds=1,
            executable="fake-provider-never-reached",
            execution_configuration=_bubblewrap_configuration_for(adapter_name),
        )
    run_root = state / "checkouts" / association.checkout_id
    assert not (run_root / "dispatch.json").exists()
    assert not (repository / "new-file.txt").exists()
    assert sorted(p.name for p in repository.glob("*") if p.name != ".git") == ["src"]
    channel_root = run_root / "provider-channel"
    assert not channel_root.exists() or not any(channel_root.iterdir())


def test_worker_scope_naming_an_existing_file_is_unaffected_by_the_f5_refusal(tmp_path, monkeypatch):
    """The companion positive: an authorized entry that already exists is
    never subject to the absent-entry refusal, regardless of boundary
    selection -- the F5 rule fires only for a target that does not exist."""
    repository = _repository(tmp_path)
    (repository / "src" / "existing.txt").write_text("already here\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "test: add existing file")
    baseline = _git(repository, "rev-parse", "HEAD")
    workflow = profile({"work": replace(
        phase({"done": route("complete")}), authorized_scope=("src/existing.txt",),
    )})
    run = _new_run(repository, workflow, baseline)
    state = tmp_path / "state"
    association = register_checkout(state, repository)

    from workflow_coordinator import operator as operator_module
    monkeypatch.setattr(operator_module.shutil, "which", lambda name: None)

    # The scope resolves without a refusal; the run still stops at the
    # (mocked-absent) `bwrap` executable check, which is a different, later
    # preflight failure and proves the F5 path was not taken.
    with pytest.raises(OperatorPreflightError, match="bwrap"):
        run_serial_hops(
            run, workflow, repository, {},
            state_root=state, association=association,
            agent_operating_contract=load_agent_operating_contract(), task_payload="task",
            governing={}, personas={WorkflowRole.WORKER: "fixture worker"}, timeout_seconds=1,
            executable="fake-provider-never-reached",
            execution_configuration=_bubblewrap_configuration(),
        )


@pytest.mark.parametrize(
    ("run_boundary", "resumed_configuration"),
    (
        ("bubblewrap", _direct_configuration),
        ("direct", _bubblewrap_configuration),
    ),
    ids=("bubblewrap-run-resumed-direct", "direct-run-resumed-bubblewrap"),
)
def test_resumed_hop_with_boundary_mode_mismatch_refuses_before_any_provider_process_starts(
    tmp_path, run_boundary, resumed_configuration
):
    """A resumed Hop must refuse by name when the boundary mode its
    invocation resolved differs from the mode the Run was created with
    (decomposition step 4, invariants table (f)): the boundary is a
    Run-wide identity property, never something a later Hop can silently
    change. Exercised in both mismatch directions; the check in
    ``run_serial_hops`` fires before ``checkout_state_root`` or any provider
    process is reached, so no `bwrap`/`shutil.which` mocking is needed here."""
    repository = _repository(tmp_path)
    baseline = _git(repository, "rev-parse", "HEAD")
    workflow = profile({"work": replace(phase({"done": route("complete")}), authorized_scope=("src",))})
    run = new_run(
        WorkItem(
            2, "WI-BOUNDARY", "fixture", "profile.yml", "repo", ("src",), "work", 1,
            {WorkflowRole.WORKER: "fixture worker"},
        ),
        workflow,
        RepositoryPolicy(3, "policy", "1", ("repo",), ("governance",), ("src",), 5, ("edit", "commit"), {}),
        run_id="RUN-BOUNDARY-MISMATCH",
        baseline_head=baseline,
        boundary=run_boundary,
    )
    state = tmp_path / "state"
    association = register_checkout(state, repository)

    with pytest.raises(OperatorPreflightError, match="boundary mode mismatch"):
        run_serial_hops(
            run, workflow, repository, {},
            state_root=state, association=association,
            agent_operating_contract=load_agent_operating_contract(), task_payload="task",
            governing={}, personas={WorkflowRole.WORKER: "fixture worker"}, timeout_seconds=1,
            executable="fake-codex-never-reached",
            execution_configuration=resumed_configuration(),
        )
    assert not (state / "checkouts" / association.checkout_id / "dispatch.json").exists()
    assert (repository / "src" / "a.txt").read_text() == "before\n"


def test_boundary_authentication_path_reads_only_the_coordinator_process_environment(monkeypatch):
    """F3, channel 1 of 1 this function could ever read from: it takes no
    argument at all and the Coordinator process reads ``os.environ``
    exactly once per call. Unset means refused (``None``); set means that
    exact value, with nothing else (no file, no repository, no Commission
    content) ever consulted."""
    monkeypatch.delenv("WFC_BOUNDARY_AUTHENTICATION_PATH", raising=False)
    assert _boundary_authentication_path() is None
    monkeypatch.setenv("WFC_BOUNDARY_AUTHENTICATION_PATH", "/some/operator/chosen/path")
    assert _boundary_authentication_path() == Path("/some/operator/chosen/path")


def test_boundary_authentication_path_is_unaffected_by_repository_or_channel_content(tmp_path, monkeypatch):
    """F3, the repository/channel/task-content channels traced and closed:
    a file in the repository, a file inside what would be the provider
    channel, and a value embedded in Commission task text, all named or
    shaped exactly like the environment variable this function reads, must
    have no effect -- the function never opens a file and is never handed
    the repository, the channel path or the task payload."""
    monkeypatch.delenv("WFC_BOUNDARY_AUTHENTICATION_PATH", raising=False)
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "WFC_BOUNDARY_AUTHENTICATION_PATH").write_text("/etc/shadow")
    (repository / ".env").write_text("WFC_BOUNDARY_AUTHENTICATION_PATH=/etc/shadow\n")
    channel = tmp_path / "provider-channel" / "INV-1"
    channel.mkdir(parents=True)
    (channel / "WFC_BOUNDARY_AUTHENTICATION_PATH").write_text("/etc/shadow")
    task_payload = "WFC_BOUNDARY_AUTHENTICATION_PATH=/etc/shadow\nexport WFC_BOUNDARY_AUTHENTICATION_PATH=/etc/shadow\n"
    assert task_payload  # the string exists only to document the attempted channel; never passed below
    assert _boundary_authentication_path() is None


def test_boundary_authentication_path_cannot_be_set_by_a_child_processs_own_environment(tmp_path):
    """F3, the process-model channel: a child process (standing in for a
    dispatched provider) setting this variable in *its own* environment,
    including via `export` in a subshell, has no way to write back into the
    Coordinator's process; a parent's environment is never writable by a
    child on any POSIX system, so this channel is closed structurally, not
    by any check this module could fail to apply. Demonstrated directly
    rather than merely asserted."""
    before = os.environ.get("WFC_BOUNDARY_AUTHENTICATION_PATH")
    try:
        os.environ.pop("WFC_BOUNDARY_AUTHENTICATION_PATH", None)
        subprocess.run(
            ["/bin/sh", "-c", "export WFC_BOUNDARY_AUTHENTICATION_PATH=/etc/shadow; true"],
            check=True,
        )
        assert _boundary_authentication_path() is None
    finally:
        if before is None:
            os.environ.pop("WFC_BOUNDARY_AUTHENTICATION_PATH", None)
        else:
            os.environ["WFC_BOUNDARY_AUTHENTICATION_PATH"] = before
