import json

import pytest

from scripts.workflow_coordinator.model import DispatchMarker, Observation, RunStatus
from scripts.workflow_coordinator.persistence import (
    atomic_write_text,
    checkpoint_projection,
    load_dispatch_marker,
    recover_uncertain_dispatch,
    write_dispatch_marker,
    write_run_snapshot,
)
from scripts.workflow_coordinator.serialization import ValidationError, load_run, run_to_json
from tests.utils.workflow_coordinator import phase, profile, route, run_for

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


def marker(run):
    return DispatchMarker(
        schema_version=1,
        run_id=run.run_id,
        hop_id="H001",
        sequence=1,
        invocation_id="INV-1",
        base_head=run.current_head,
        budget_reserved=1,
    )


def test_atomic_run_snapshot_round_trip(tmp_path):
    workflow = profile({"work": phase({"done": route("complete")})})
    run = run_for(workflow)
    path = tmp_path / "run.json"
    write_run_snapshot(path, run)
    assert load_run(path) == run
    assert path.read_text(encoding="utf-8") == run_to_json(run)


def test_interrupted_atomic_replacement_preserves_old_file(tmp_path):
    path = tmp_path / "run.json"
    path.write_text("old\n", encoding="utf-8")

    def interrupt(_source, _target):
        raise RuntimeError("simulated interruption before replace")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        atomic_write_text(path, "new\n", replace_file=interrupt)
    assert path.read_text(encoding="utf-8") == "old\n"
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_dispatch_marker_is_strict_and_correlated(tmp_path):
    run = run_for(profile({"work": phase({"done": route("complete")})}))
    path = tmp_path / "dispatch.json"
    write_dispatch_marker(path, marker(run))
    assert load_dispatch_marker(path) == marker(run)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["untrusted"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValidationError, match="unknown fields"):
        load_dispatch_marker(path)


def test_crash_before_dispatch_marker_consumes_no_hop_and_preserves_snapshot():
    run = run_for(profile({"work": phase({"done": route("complete")})}))
    recovered = recover_uncertain_dispatch(run, None, current_head=run.current_head)
    assert recovered == run
    assert recovered.hop_used == 0


def test_crash_after_dispatch_marker_charges_once_and_requires_reconcile():
    run = run_for(profile({"work": phase({"done": route("complete")})}), budget=1)
    recovered = recover_uncertain_dispatch(run, marker(run), current_head="possibly-mutated")
    assert recovered.hop_used == 1
    assert recovered.hops[0].outcome == "uncertain"
    assert recovered.status is RunStatus.RECONCILE_REQUIRED
    assert recovered.reconcile_reason == "interruption"
    assert recovered.stop_reason == "reconcile_requires_explicit_resume"
    recovered_again = recover_uncertain_dispatch(
        recovered, marker(run), current_head="possibly-mutated"
    )
    assert recovered_again.hop_used == 1


def test_checkpoint_is_deterministic_and_derives_routes_from_profile():
    workflow = profile({"work": phase({"z_route": route("complete"), "a_route": route("stop")})})
    run = run_for(workflow, budget=2)
    observations = (Observation("tests", "pass", ("pytest",), "green", "report", "sha256:x"),)
    first = checkpoint_projection(run, workflow, observations=observations)
    second = checkpoint_projection(run, workflow, observations=observations)
    assert first == second
    assert first["next_permitted_route_keys"] == ["a_route", "z_route"]
    assert first["budget"] == {"used": 0, "remaining": 2}
    assert first["evidence"][0]["status"] == "pass"
    assert "project_memory" not in first


def test_lost_local_state_has_no_automatic_recovery(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_run(tmp_path / "lost-run.json")
