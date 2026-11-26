"""Dispatch-level preflight: no silent fallback when the boundary cannot be
established (N14). This exercises ``operator.run_serial_hops``'s own
``adapter is None`` production path -- the real grant derivation and
preflight checks -- without needing a real `bwrap` run: an unestablishable
boundary is refused before any provider process starts, which is exactly
the property this case proves, and is reachable deterministically wherever
this suite runs.
"""
from __future__ import annotations

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
from workflow_coordinator.operator import OperatorPreflightError, run_serial_hops
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
    return RunExecutionConfiguration(
        schema_version=1,
        bindings=MappingProxyType({WorkflowRole.WORKER: ExecutionBinding("codex_cli", "fixture-model", "high")}),
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
