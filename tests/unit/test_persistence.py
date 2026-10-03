import json
from dataclasses import replace

import pytest

from workflow_coordinator.model import DispatchMarker, RunStatus
from workflow_coordinator.reducer import ReductionError, require_reconcile, resume_after_reconcile
from workflow_coordinator.persistence import (
    atomic_write_text,
    checkpoint_projection,
    load_dispatch_marker,
    publish_execution_evidence,
    recover_uncertain_dispatch,
    write_dispatch_marker,
    write_run_snapshot,
    trusted_qualification_index_for_run,
    validate_checkpoint_inputs,
)
from workflow_coordinator.serialization import (
    ValidationError,
    execution_evidence_from_json,
    load_run,
    run_from_json,
    run_to_json,
)
from tests.support import (
    charge_hop,
    convergence_cycle,
    phase,
    prepared_evidence,
    profile,
    qualification_evidence,
    route,
    run_for,
    simulated_observation,
    tracked_convergence_run,
)

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


@pytest.mark.parametrize("schema_version", [2, 3])
def test_persistence_never_upgrades_v2_in_place_and_preserves_explicit_v3(
    tmp_path, schema_version
):
    workflow = profile({"work": phase({"done": route("complete")})})
    run = replace(run_for(workflow), schema_version=schema_version)
    path = tmp_path / "run.json"
    write_run_snapshot(path, run)
    persisted = load_run(path)
    assert persisted.schema_version == schema_version
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == schema_version


def test_persisting_legacy_run_upgrades_atomically_without_inventing_facts(tmp_path):
    run = replace(run_for(profile({"work": phase({"done": route("complete")})})), schema_version=1)
    path = tmp_path / "run.json"
    write_run_snapshot(path, run)
    upgraded = load_run(path)
    assert upgraded.schema_version == 2
    assert upgraded.semantic_stop is None and upgraded.successor_provenance is None


def test_failed_legacy_upgrade_preserves_the_exact_v1_snapshot(tmp_path):
    legacy = replace(run_for(profile({"work": phase({"done": route("complete")})})), schema_version=1)
    path = tmp_path / "run.json"
    path.write_text(run_to_json(legacy), encoding="utf-8")
    original = path.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("injected replacement failure")

    with pytest.raises(OSError, match="injected replacement failure"):
        write_run_snapshot(path, legacy, replace_file=fail_replace)
    assert path.read_bytes() == original


def test_legacy_reconcile_resume_transition_writes_v2_only_at_the_persistence_boundary(tmp_path):
    """Loading/replaying v1 is read-only; its next real transition upgrades it."""
    workflow = profile({"work": phase({"done": route("complete")})})
    legacy = replace(run_for(workflow), schema_version=1)
    path = tmp_path / "run.json"
    path.write_text(run_to_json(legacy), encoding="utf-8")
    original = path.read_bytes()

    loaded = load_run(path)
    assert loaded.schema_version == 1 and path.read_bytes() == original
    reconciled = require_reconcile(loaded, reason="interruption", current_head=loaded.current_head)
    resumed = resume_after_reconcile(reconciled, workflow, current_head=loaded.current_head)
    assert resumed.status is RunStatus.READY and resumed.schema_version == 1
    assert path.read_bytes() == original

    write_run_snapshot(path, resumed)
    persisted = load_run(path)
    assert persisted.schema_version == 2
    assert persisted.semantic_stop is None and persisted.successor_provenance is None


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


def test_checkpoint_is_deterministic_and_derives_routes_from_profile(tmp_path):
    workflow = profile({"work": phase({"z_route": route("complete"), "a_route": route("stop")})})
    run = run_for(workflow, budget=2)
    command = prepared_evidence(tmp_path)["tests"]
    observations = (simulated_observation(command),)
    first = checkpoint_projection(run, workflow, observations=observations)
    second = checkpoint_projection(run, workflow, observations=observations)
    assert first == second
    assert first["next_permitted_route_keys"] == ["a_route", "z_route"]
    assert first["budget"] == {"used": 0, "remaining": 2}
    assert first["evidence"][0]["status"] == "pass"
    assert "project_memory" not in first
    assert "convergence_facts" not in first


def test_checkpoint_optionally_projects_derived_convergence_facts_and_sources():
    workflow, run = tracked_convergence_run()
    run, _, evaluator_hop = convergence_cycle(
        run, workflow, evidence_prefix="checkpoint"
    )
    run, _, evidence_json = qualification_evidence(
        run, {evaluator_hop: ("pass",)}
    )

    projected = checkpoint_projection(
        run,
        workflow,
        qualified_cycle_limit=2,
        execution_evidence_json=evidence_json,
    )
    assert projected["convergence_facts"] == [
        {
            "finding_id": "F001",
            "strategy_generation": 0,
            "qualified_cycle_count": 1,
            "qualified_cycle_limit": 2,
            "goalpost_reached": False,
            "source_evaluator_hops": [evaluator_hop],
        }
    ]
    assert "convergence_facts" not in checkpoint_projection(run, workflow)
    assert "convergence_facts" not in json.loads(run_to_json(run))


def test_schema4_publication_preserves_rows_is_idempotent_and_rejects_conflicts(
    tmp_path,
):
    workflow, run = tracked_convergence_run()
    run, _, first = convergence_cycle(run, workflow, evidence_prefix="first")
    run, _, second = convergence_cycle(run, workflow, evidence_prefix="second")
    run, _, evidence_json = qualification_evidence(
        run, {first: ("pass",), second: ("pass",)}
    )
    document = execution_evidence_from_json(evidence_json)
    path = tmp_path / "execution-evidence.json"

    first_document, first_text = publish_execution_evidence(
        path, document.run, document.attempts[0]
    )
    assert len(first_document.attempts) == 1
    duplicate_document, duplicate_text = publish_execution_evidence(
        path, document.run, document.attempts[0]
    )
    assert duplicate_document == first_document
    assert duplicate_text == first_text == path.read_text(encoding="utf-8")

    before_second = path.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("injected evidence replacement failure")

    with pytest.raises(OSError, match="injected evidence replacement failure"):
        publish_execution_evidence(
            path,
            document.run,
            document.attempts[1],
            replace_file=fail_replace,
        )
    assert path.read_bytes() == before_second

    complete, _ = publish_execution_evidence(
        path, document.run, document.attempts[1]
    )
    assert [attempt.hop_id for attempt in complete.attempts] == [first, second]
    conflicting = replace(
        document.attempts[1],
        persona="different persona",
        persona_sha256="a" * 64,
    )
    with pytest.raises(ReductionError, match="conflicting duplicate"):
        publish_execution_evidence(path, document.run, conflicting)


def test_exact_pair_and_dual_checkpoint_digests_fail_closed_on_mismatch():
    workflow, run = tracked_convergence_run()
    run, _, evaluator_hop = convergence_cycle(run, workflow, evidence_prefix="pair")
    run, index, evidence_json = qualification_evidence(
        run, {evaluator_hop: ("pass",)}
    )
    document = execution_evidence_from_json(evidence_json)
    assert trusted_qualification_index_for_run(run, document) == index
    missing = replace(document, attempts=())
    with pytest.raises(ReductionError, match="lacks paired"):
        trusted_qualification_index_for_run(run, missing)
    cross_run = replace(document, run=replace(document.run, run_id="RUN-other"))
    with pytest.raises(ReductionError, match="identity"):
        trusted_qualification_index_for_run(run, cross_run)

    checkpoint = checkpoint_projection(
        run,
        workflow,
        qualified_cycle_limit=2,
        execution_evidence_json=evidence_json,
    )
    validate_checkpoint_inputs(
        checkpoint, run, execution_evidence_json=evidence_json
    )
    stale_run = json.loads(json.dumps(checkpoint))
    stale_run["identity"]["run_json_sha256"] = "0" * 64
    with pytest.raises(ReductionError, match="Run digest mismatch"):
        validate_checkpoint_inputs(
            stale_run, run, execution_evidence_json=evidence_json
        )
    stale_evidence = json.loads(json.dumps(checkpoint))
    stale_evidence["identity"]["execution_evidence_sha256"] = "0" * 64
    with pytest.raises(ReductionError, match="execution-evidence digest mismatch"):
        validate_checkpoint_inputs(
            stale_evidence, run, execution_evidence_json=evidence_json
        )


def test_checkpoint_limit_does_not_fabricate_facts_for_simple_or_legacy_runs():
    workflow = profile({"work": phase({"done": route("complete")})})
    run = run_for(workflow)
    for schema_version in (1, 2, 3):
        historical = replace(run, schema_version=schema_version)
        assert checkpoint_projection(
            historical, workflow, qualified_cycle_limit=2
        )["convergence_facts"] == []


@pytest.mark.parametrize("evidence_version", [1, 2, 3])
def test_legacy_execution_evidence_is_audit_readable_but_never_proves_pass(
    evidence_version,
):
    workflow, run = tracked_convergence_run()
    run, _, _ = convergence_cycle(run, workflow, evidence_prefix="legacy-evidence")
    legacy = json.dumps({"schema_version": evidence_version, "attempts": []})
    facts = checkpoint_projection(
        run,
        workflow,
        qualified_cycle_limit=2,
        execution_evidence_json=legacy,
    )["convergence_facts"]
    assert facts[0]["qualified_cycle_count"] == 0
    assert facts[0]["source_evaluator_hops"] == []


@pytest.mark.parametrize(
    "status",
    [
        RunStatus.AWAITING_HUMAN,
        RunStatus.AWAITING_HUMAN_BUDGET,
        RunStatus.RECONCILE_REQUIRED,
        RunStatus.SUSPENDED,
        RunStatus.COMPLETED,
        RunStatus.STOPPED,
    ],
)
def test_checkpoint_exposes_no_immediately_permitted_routes_while_paused(status):
    workflow = profile({"work": phase({"done": route("complete"), "halt": route("stop")})})
    paused = replace(run_for(workflow), status=status)
    assert checkpoint_projection(paused, workflow)["next_permitted_route_keys"] == []


def test_checkpoint_keeps_compact_per_hop_finding_and_evidence_references():
    workflow = profile({"work": phase({"done": route("complete")})})
    run, _ = charge_hop(
        run_for(workflow),
        finding_delta_ids=("F001",),
        evidence_refs=("tests", "review-report"),
    )
    row = checkpoint_projection(run, workflow)["hops"][0]
    assert row["finding_delta_ids"] == ["F001"]
    assert row["evidence_refs"] == ["tests", "review-report"]
    assert run_from_json(run_to_json(run)) == run


def test_lost_local_state_has_no_automatic_recovery(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_run(tmp_path / "lost-run.json")
