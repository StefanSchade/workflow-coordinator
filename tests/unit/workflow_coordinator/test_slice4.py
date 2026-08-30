"""Deterministic Slice-4 external-state and real-process composition tests."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.workflow_coordinator.adapter import Invocation
from scripts.workflow_coordinator.slice4 import (
    Slice4Error, invoke_codex, register_checkout, run_real_one_hop, validate_checkout,
)
from tests.utils.workflow_coordinator import phase, profile, route, run_for
from scripts.workflow_coordinator.prompt import PromptComponent


pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(("git", "init", str(repo)), check=True, capture_output=True)
    for key, value in (("user.email", "fixture@example.invalid"), ("user.name", "Fixture")):
        subprocess.run(("git", "-C", str(repo), "config", key, value), check=True)
    (repo / "PROOF.md").write_text("before\n")
    subprocess.run(("git", "-C", str(repo), "add", "PROOF.md"), check=True)
    subprocess.run(("git", "-C", str(repo), "commit", "-m", "docs: baseline"), check=True, capture_output=True)
    return repo


def _components() -> dict[str, PromptComponent]:
    return {"worker": PromptComponent("worker", "worker.txt", "PIN", "worker"),
            "testing": PromptComponent("testing", "testing.txt", "PIN", "testing")}


def _fake(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake-codex"
    executable.write_text("#!/usr/bin/env python3\n" + body)
    executable.chmod(0o755)
    return executable


def _expected() -> Invocation:
    return Invocation("RUN-1", "H001", "INV-1")


def test_state_root_nesting_is_rejected_before_any_trusted_write(repository, tmp_path):
    inside = repository / "bad-state"
    with pytest.raises(Slice4Error, match="disjoint"):
        register_checkout(inside, repository)
    assert not (inside / "checkout-association.json").exists()
    assert not (inside / "checkouts").exists()
    with pytest.raises(Slice4Error, match="disjoint"):
        register_checkout(tmp_path, repository)


def test_association_is_stable_and_malformed_or_git_mismatched_forms_fail_closed(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    assert validate_checkout(state, repository) == association
    mapping = state / "checkout-association.json"
    for malformed in ("{", json.dumps({"schema_version": 2}), json.dumps({"schema_version": 1, "checkout_id": 1})):
        mapping.write_text(malformed)
        with pytest.raises(Slice4Error):
            validate_checkout(state, repository)
    mapping.write_text(json.dumps({"schema_version": 1, **association.__dict__, "common_dir": "/wrong"}))
    with pytest.raises(Slice4Error, match="mismatch"):
        validate_checkout(state, repository)


def test_linked_worktree_git_dir_and_common_dir_are_recorded_and_checked(repository, tmp_path):
    linked = tmp_path / "linked"
    subprocess.run(("git", "-C", str(repository), "worktree", "add", "-b", "linked", str(linked)), check=True, capture_output=True)
    association = register_checkout(tmp_path / "external", linked)
    assert association.git_dir != association.common_dir
    assert validate_checkout(tmp_path / "external", linked) == association


@pytest.mark.parametrize("payload", ["{", '{"schema_version":1,"run_id":"wrong"}'])
def test_nonempty_invalid_result_is_not_established(repository, tmp_path, payload):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    executable = _fake(tmp_path, "import pathlib,sys\np=pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]); p.write_text(" + repr(payload) + ")\n")
    outcome = invoke_codex(repository=repository, state_root=state, association=association, prompt="x", expected=_expected(), timeout_seconds=2, executable=str(executable))
    assert outcome.acceptance == "uncertain" and outcome.result is None


def test_nonzero_without_result_is_uncertain(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    executable = _fake(tmp_path, "import sys\nsys.exit(7)\n")
    outcome = invoke_codex(repository=repository, state_root=state, association=association, prompt="x", expected=_expected(), timeout_seconds=2, executable=str(executable))
    assert outcome.returncode == 7 and outcome.acceptance == "uncertain"


def test_real_vertical_composition_uses_fake_cli_and_existing_reducer(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    script = '''import json, pathlib, subprocess, sys
args=sys.argv[1:]; repo=args[args.index("--cd")+1]; output=pathlib.Path(args[args.index("--output-last-message")+1])
(output.parent / "argv.json").write_text(json.dumps(args))
pathlib.Path(repo, "PROOF.md").write_text("after\\n")
subprocess.run(("git","-C",repo,"add","PROOF.md"),check=True)
subprocess.run(("git","-C",repo,"commit","-m","docs(workflow): WI-1 H001 fixture"),check=True)
commit=subprocess.run(("git","-C",repo,"rev-parse","HEAD"),text=True,capture_output=True,check=True).stdout.strip()
output.write_text(json.dumps({"schema_version":1,"run_id":"RUN-1","hop_id":"H001","invocation_id":"INV-1","result":{"schema_version":1,"outcome":"completed","requested_route":"done","scope_changed":False,"requires_human":False,"escalation":None,"findings":[],"claimed_commits":[commit],"evidence_refs":[],"summary":"done"}}))
'''
    executable = _fake(tmp_path, script)
    workflow = profile({"work": phase({"done": route("complete")})})
    workflow = replace(workflow, phases={"work": replace(workflow.phases["work"], authorized_scope=("PROOF.md",), evidence=("git_version",))})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)
    result = run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, executable=str(executable))
    assert result.reduction.run.status.value == "completed"
    assert result.reduction.run.hop_used == 1 and result.reduction.run.hops[0].actual_commits
    assert result.checkpoint["state"]["status"] == "completed" and (repository / "PROOF.md").read_text() == "after\n"
    argv = json.loads((state / "checkouts" / association.checkout_id / "argv.json").read_text())
    assert argv[:3] == ["--ask-for-approval", "never", "exec"]
    for token in ("--ephemeral", "--json", "--color", "never", "--sandbox", "workspace-write", "--model", "gpt-5.6-terra", "-"):
        assert token in argv
    assert argv[argv.index("--cd") + 1] == str(repository.resolve())
    for option in ("--output-schema", "--output-last-message"):
        assert Path(argv[argv.index(option) + 1]).is_relative_to(state.resolve())


def test_timeout_kills_process_group_descendant(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    child_pid = tmp_path / "descendant.pid"
    executable = _fake(tmp_path, f"import subprocess,time\np=subprocess.Popen(('sleep','30'))\nopen({str(child_pid)!r},'w').write(str(p.pid))\ntime.sleep(30)\n")
    outcome = invoke_codex(repository=repository, state_root=state, association=association, prompt="x", expected=_expected(), timeout_seconds=0.1, executable=str(executable))
    assert outcome.timed_out
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(int(child_pid.read_text()), 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("process-group descendant survived timeout")


def test_real_driver_recovers_post_spawn_uncertainty_with_external_anchors(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    executions = tmp_path / "executions"
    executable = _fake(tmp_path, f"import pathlib,sys\np=pathlib.Path({str(executions)!r})\np.write_text(str(int(p.read_text())+1) if p.exists() else '1')\nsys.exit(7)\n")
    workflow = profile({"work": phase({"done": route("complete")})})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)
    outcome = run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, executable=str(executable))
    run_root = state / "checkouts" / association.checkout_id
    assert outcome.vertical is None and outcome.run.hop_used == 1
    assert outcome.run.status.value == "reconcile_required"
    assert outcome.run.stop_reason == "reconcile_requires_explicit_resume"
    assert outcome.checkpoint["state"]["status"] == "reconcile_required"
    assert executions.read_text() == "1" and (repository / "PROOF.md").read_text() == "before\n"
    assert subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip() == head
    assert (run_root / "dispatch.json").exists() and (run_root / "run.json").exists()
    assert (run_root / "checkpoint.json").exists()


def test_real_driver_preserves_no_hop_for_unlaunchable_executable(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    workflow = profile({"work": phase({"done": route("complete")})})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)
    with pytest.raises(Slice4Error, match="pre-acceptance"):
        run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, executable=str(tmp_path / "missing-codex"))
    assert run.hop_used == 0
    assert not (state / "checkouts" / association.checkout_id / "dispatch.json").exists()
