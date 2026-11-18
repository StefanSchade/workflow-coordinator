"""Consumer-neutral preflight and reducer invariants, retained alongside historical tests."""

import os
import shutil
import sys
from pathlib import Path

import pytest

from workflow_coordinator.model import RunStatus
from workflow_coordinator.operator import OperatorPreflightError, _preflight
from workflow_coordinator.reducer import reduce_result
from tests.support import charge_hop, phase, profile, result, route, run_for
from tests.unit.test_slice5b import commit_all, fixture_data, fixture_repository, preflight_args


def _repository(tmp_path):
    data = fixture_data()
    data["repository_policy"]["evidence_commands"]["fake_check"]["argv"] = [
        "python", "-m", "pytest", "-q",
    ]
    return fixture_repository(tmp_path, data=data)


def test_work_item_mapping_cannot_enlarge_review_phase_authority(tmp_path):
    data = fixture_data()
    outside = "review/outside.md"
    data["work_item"]["scope"].append(outside)
    data["work_item"]["durable_artifacts"]["review"] = [outside]
    data["repository_policy"]["work_item_scope_ceiling"].append(outside)
    repo = fixture_repository(tmp_path, data=data)
    commit_all(repo)
    state = tmp_path / "state"
    with pytest.raises(
        OperatorPreflightError,
        match="durable artifact for phase review is outside WorkItem or phase scope",
    ):
        _preflight(preflight_args(repo, state))
    assert not state.exists()


def test_production_preflight_rejects_consumed_isolated_state_root(tmp_path):
    repo = _repository(tmp_path)
    commit_all(repo)
    state = tmp_path / "consumed"
    state.mkdir()
    with pytest.raises(
        OperatorPreflightError,
        match="state root already exists; one-shot operator roots are never overwritten",
    ):
        _preflight(preflight_args(repo, state))


def test_production_preflight_identifies_different_python_interpreter_identity(tmp_path, monkeypatch):
    alternate_bin = tmp_path / "alternate-bin"
    alternate_bin.mkdir()
    alternate_python = alternate_bin / "python"
    shutil.copy2(sys.executable, alternate_python)
    monkeypatch.setenv("PATH", os.pathsep.join((str(alternate_bin), os.environ["PATH"])))
    repo = _repository(tmp_path)
    commit_all(repo)
    state = tmp_path / "state"
    prepared = _preflight(preflight_args(repo, state))[13]["fake_check"]
    assert prepared.admission_resolution.canonical_path == str(alternate_python.resolve())
    assert prepared.admission_resolution.canonical_path != str(Path(sys.executable).resolve())
    assert not state.exists()


def _bootstrap_profile():
    return profile(
        {
            "review": phase(
                {
                    "accept": route("complete"),
                    "correct": route("goto", "correct"),
                },
                role="independent_reviewer",
                review="gate",
                finding_authority=(),
            ),
            "correct": phase({"rereview": route("goto", "review")}),
        }
    )


def test_bootstrap_changes_required_correct_route_is_legal_with_empty_finding_set():
    workflow = _bootstrap_profile()
    run, hop_id = charge_hop(
        run_for(workflow, initial_phase="review"), role="independent_reviewer"
    )
    assert run.findings == ()

    reduction = reduce_result(
        run,
        result("correct", outcome="changes_required", findings=()),
        workflow,
        hop_id=hop_id,
    )

    assert reduction.run.phase == "correct"
    assert reduction.run.status is RunStatus.READY
    assert reduction.route_key == "correct"
    assert reduction.autonomous_route is True
    assert reduction.run.findings == ()
    assert reduction.run.stop_reason is None


def test_bootstrap_rereview_changes_required_correct_route_is_also_legal_with_empty_finding_set():
    workflow = profile(
        {
            "correct": phase({"rereview": route("goto", "rereview")}),
            "rereview": phase(
                {
                    "accept": route("complete"),
                    "correct": route("goto", "correct"),
                },
                role="independent_reviewer",
                review="gate",
                finding_authority=(),
            ),
        }
    )
    run, hop_id = charge_hop(
        run_for(workflow, initial_phase="rereview"), role="independent_reviewer"
    )
    assert run.findings == ()

    reduction = reduce_result(
        run,
        result("correct", outcome="changes_required", findings=()),
        workflow,
        hop_id=hop_id,
    )

    assert reduction.run.phase == "correct"
    assert reduction.run.status is RunStatus.READY
    assert reduction.route_key == "correct"
    assert reduction.run.findings == ()

