"""Deterministic tests for the external trusted-state and local-process boundary."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.workflow_coordinator.slice4 import Slice4Error, invoke_codex, register_checkout, validate_checkout


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(("git", "init", str(repo)), check=True, capture_output=True)
    subprocess.run(("git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"), check=True)
    subprocess.run(("git", "-C", str(repo), "config", "user.name", "Fixture"), check=True)
    (repo / "PROOF.md").write_text("before\n")
    subprocess.run(("git", "-C", str(repo), "add", "PROOF.md"), check=True)
    subprocess.run(("git", "-C", str(repo), "commit", "-m", "docs: baseline"), check=True, capture_output=True)
    return repo


def test_external_association_is_stable_and_fails_closed_for_a_moved_checkout(repository, tmp_path):
    state = tmp_path / "external"
    first = register_checkout(state, repository)
    assert register_checkout(state, repository) == first
    assert validate_checkout(state, repository) == first
    renamed = tmp_path / "renamed"
    repository.rename(renamed)
    with pytest.raises(Slice4Error, match="re-registration"):
        validate_checkout(state, renamed)


def test_real_boundary_rejects_state_inside_agent_workspace(repository):
    association = register_checkout(repository / "bad-state", repository)
    with pytest.raises(Slice4Error, match="outside agent workspace"):
        invoke_codex(repository=repository, state_root=repository / "bad-state", association=association,
                     prompt="x", timeout_seconds=1, executable=sys.executable)


def test_process_timeout_terminates_local_process_group(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    fake = tmp_path / "codex-sleeper"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(0o755)
    outcome = invoke_codex(repository=repository, state_root=state, association=association, prompt="x",
                           timeout_seconds=0.1, executable=str(fake))
    assert outcome.timed_out
    assert outcome.acceptance == "uncertain"
