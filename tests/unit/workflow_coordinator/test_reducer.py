from dataclasses import fields, replace

import pytest

from scripts.workflow_coordinator.model import (
    Escalation,
    FindingAuthority,
    FindingState,
    MechanicalFacts,
    Run,
    RunStatus,
)
from scripts.workflow_coordinator.reducer import (
    ReductionError,
    apply_finding_deltas,
    authorize_prerequisite,
    confirm_supervisor_route,
    permitted_route_keys,
    reduce_result,
    require_reconcile,
    resume_after_reconcile,
)
from tests.utils.workflow_coordinator import (
    charge_hop,
    delta,
    phase,
    profile,
    result,
    route,
    run_for,
)

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


def gate_profile():
    return profile(
        {
            "review": phase(
                {
                    "accept": route("complete"),
                    "correct": route("goto", "correction"),
                },
                role="reviewer",
                review="gate",
                finding_authority=("resolve", "residual", "supersede", "reopen"),
            ),
            "correction": phase({"review": route("goto", "review")}),
        }
    )


def test_finding_lifecycle_requires_gate_or_human_authority():
    workflow = gate_profile()
    run, _ = charge_hop(run_for(workflow))
    run = apply_finding_deltas(
        run,
        (delta(None, "open"),),
        hop_id="H001",
        authority=FindingAuthority.AGENT,
    )
    with pytest.raises(ReductionError, match="agent cannot perform"):
        apply_finding_deltas(
            run,
            (delta("F001", "resolved"),),
            hop_id="H001",
            authority=FindingAuthority.AGENT,
        )

    resolved = apply_finding_deltas(
        run,
        (delta("F001", "resolved"),),
        hop_id="H001",
        authority=FindingAuthority.GATE,
        permitted_gate_operations=("resolve",),
    )
    assert resolved.findings[0].state is FindingState.RESOLVED


def test_reopen_requires_authority_and_new_material_evidence():
    workflow = gate_profile()
    run, _ = charge_hop(run_for(workflow))
    opened = apply_finding_deltas(
        run, (delta(None, "open"),), hop_id="H001", authority=FindingAuthority.AGENT
    )
    resolved = apply_finding_deltas(
        opened,
        (delta("F001", "resolved"),),
        hop_id="H001",
        authority=FindingAuthority.HUMAN,
    )
    with pytest.raises(ReductionError, match="new material evidence"):
        apply_finding_deltas(
            resolved,
            (delta("F001", "open"),),
            hop_id="H001",
            authority=FindingAuthority.HUMAN,
        )
    reopened = apply_finding_deltas(
        resolved,
        (delta("F001", "open", evidence="new", new_material=True),),
        hop_id="H001",
        authority=FindingAuthority.HUMAN,
    )
    assert reopened.findings[0].state is FindingState.OPEN
    assert reopened.findings[0].evidence_refs == ("evidence-1", "new")


def test_residual_and_superseded_states_have_required_invariants():
    workflow = gate_profile()
    run, _ = charge_hop(run_for(workflow))
    with pytest.raises(ReductionError, match="non-blocking"):
        apply_finding_deltas(
            run,
            (delta(None, "residual", blocking=True),),
            hop_id="H001",
            authority=FindingAuthority.HUMAN,
        )
    with pytest.raises(ReductionError, match="successor_ref"):
        apply_finding_deltas(
            run,
            (delta(None, "superseded"),),
            hop_id="H001",
            authority=FindingAuthority.HUMAN,
        )


def test_supervisor_has_no_finding_disposition_surface():
    run, _ = charge_hop(run_for(gate_profile()))
    with pytest.raises(ReductionError, match="supervisor"):
        apply_finding_deltas(
            run,
            (delta(None, "open"),),
            hop_id="H001",
            authority=FindingAuthority.SUPERVISOR,
        )


@pytest.mark.parametrize(
    "facts, expected",
    [
        (MechanicalFacts(state_error="malformed state"), "malformed state"),
        (MechanicalFacts(repository_anomaly="unexpected HEAD"), "unexpected HEAD"),
        (MechanicalFacts(policy_anomaly="profile changed"), "profile changed"),
        (MechanicalFacts(required_evidence_error="tests errored"), "tests errored"),
    ],
)
def test_mechanical_facts_preempt_routing(facts, expected):
    workflow = gate_profile()
    run, hop_id = charge_hop(run_for(workflow))
    reduced = reduce_result(run, result("accept"), workflow, hop_id=hop_id, facts=facts)
    assert reduced.run.status is RunStatus.AWAITING_HUMAN
    assert expected in (reduced.run.stop_reason or "")
    assert reduced.autonomous_route is False


def test_unknown_route_fails_closed_with_diagnostic():
    workflow = gate_profile()
    run, hop_id = charge_hop(run_for(workflow))
    reduced = reduce_result(run, result("invented"), workflow, hop_id=hop_id)
    assert reduced.run.status is RunStatus.AWAITING_HUMAN
    assert reduced.run.stop_reason == "mechanical_preemption:unknown_route:invented"


def test_supervisor_can_only_confirm_declared_finite_route():
    workflow = profile(
        {
            "review": phase(
                {
                    "productive": route("goto", "correction", supervisor=True),
                    "halt": route("stop", supervisor=True),
                    "invented": route("complete"),
                }
            ),
            "correction": phase({"done": route("complete")}),
        }
    )
    run, hop_id = charge_hop(run_for(workflow, budget=3))
    pending = reduce_result(run, result("productive"), workflow, hop_id=hop_id)
    assert pending.supervisor_required is True
    assert permitted_route_keys(pending.run, workflow) == ("halt", "productive")

    supervisor_run, _ = charge_hop(pending.run, role="routing supervisor", attribution="supervisor")
    rejected = confirm_supervisor_route(supervisor_run, workflow, "invented")
    assert rejected.run.status is RunStatus.AWAITING_HUMAN
    assert rejected.autonomous_route is False


def test_prerequisite_requires_human_and_uses_common_reconcile_resume():
    workflow = profile(
        {"work": phase({"child": route("suspend_for_prerequisite"), "done": route("complete")})}
    )
    run, hop_id = charge_hop(run_for(workflow, budget=3))
    stopped = reduce_result(run, result("child"), workflow, hop_id=hop_id).run
    assert stopped.status is RunStatus.AWAITING_HUMAN

    suspended = authorize_prerequisite(stopped, child_run_id="CHILD-1")
    assert suspended.status is RunStatus.SUSPENDED
    assert suspended.suspension_head == stopped.current_head
    returned = require_reconcile(
        suspended,
        reason="prerequisite_return",
        current_head="child-head",
    )
    assert returned.status is RunStatus.RECONCILE_REQUIRED
    assert returned.reconcile_reason == "prerequisite_return"
    assert "next_route" not in {field.name for field in fields(Run)}
    resumed = resume_after_reconcile(returned, workflow, current_head="child-head")
    assert resumed.status is RunStatus.READY
    assert resumed.reconcile_reason == "prerequisite_return"


def test_reconcile_rejects_stale_reality_and_unresolved_anomaly():
    workflow = gate_profile()
    run = require_reconcile(
        run_for(workflow),
        reason="interruption",
        current_head="observed",
        reality_error="dirty worktree",
    )
    with pytest.raises(ReductionError, match="anomalies"):
        resume_after_reconcile(run, workflow, current_head="observed")
    clean = replace(run, anomalies=())
    with pytest.raises(ReductionError, match="HEAD changed"):
        resume_after_reconcile(clean, workflow, current_head="stale")


def test_budget_exhaustion_blocks_goto_but_not_deterministic_completion():
    workflow = profile({"work": phase({"again": route("goto", "work"), "done": route("complete")})})
    exhausted, hop_id = charge_hop(run_for(workflow, budget=1))
    stopped = reduce_result(exhausted, result("again"), workflow, hop_id=hop_id)
    assert stopped.run.status is RunStatus.AWAITING_HUMAN_BUDGET
    assert stopped.run.phase == "work"
    completed = reduce_result(exhausted, result("done"), workflow, hop_id=hop_id)
    assert completed.run.status is RunStatus.COMPLETED


def test_scope_change_and_escalation_preempt_valid_completion():
    workflow = profile({"work": phase({"done": route("complete")})})
    run, hop_id = charge_hop(run_for(workflow))
    scope_stop = reduce_result(run, result("done", scope_changed=True), workflow, hop_id=hop_id)
    assert scope_stop.run.status is RunStatus.AWAITING_HUMAN
    semantic_stop = reduce_result(
        run,
        result(
            "done",
            requires_human=True,
            escalation=Escalation("semantic_authority", "Choose the owner."),
        ),
        workflow,
        hop_id=hop_id,
    )
    assert semantic_stop.autonomous_route is False


def test_stopped_run_cannot_accept_another_invocation_even_with_budget_remaining():
    workflow = profile({"work": phase({"ask": route("complete")})})
    run, hop_id = charge_hop(run_for(workflow, budget=3))
    stopped = reduce_result(
        run,
        result(
            "ask",
            requires_human=True,
            escalation=Escalation("operational", "Inspect state."),
        ),
        workflow,
        hop_id=hop_id,
    ).run
    with pytest.raises(ValueError, match="status prohibits invocation"):
        charge_hop(stopped)
