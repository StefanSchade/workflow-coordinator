from dataclasses import fields, replace
import inspect
import json
from pathlib import Path
import subprocess

import pytest

from workflow_coordinator.model import (
    ConvergenceAssessment,
    ConvergenceAssessmentResult,
    ConvergenceFact,
    ConvergenceRole,
    CorrectionObligation,
    CorrectionObligationOwner,
    CoverageClaim,
    CoverageDisposition,
    DiagnosisClassification,
    DiagnosisReport,
    Escalation,
    ExistingFindingDelta,
    Finding,
    FindingAuthority,
    FindingState,
    MechanicalFacts,
    Run,
    RunStatus,
    StrategyAssessment,
    StrategyAssessmentResult,
    StrategyRevision,
    StrategyRevisionKind,
    TrustedQualificationIndex,
    WorkflowRole,
)
from workflow_coordinator.reducer import (
    ReductionError,
    apply_finding_deltas,
    authorize_prerequisite,
    confirm_supervisor_route,
    derive_convergence_facts,
    permitted_route_keys,
    reduce_result,
    require_reconcile,
    resume_after_reconcile,
    validate_convergence_route_history,
)
from workflow_coordinator.serialization import run_from_json, run_to_json
from workflow_coordinator.persistence import checkpoint_projection
from workflow_coordinator.vertical import convergence_prompt_context
from workflow_coordinator import operator
from tests.support import (
    assessment,
    charge_hop,
    convergence_cycle,
    convergence_routing_profile,
    coverage,
    existing_delta,
    new_delta,
    phase,
    profile,
    qualification_evidence,
    result,
    route,
    run_for,
    tracked_convergence_run,
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
                role="independent_reviewer",
                review="gate",
                finding_authority=("resolve", "residual", "supersede", "reopen"),
            ),
            "correction": phase({"review": route("goto", "review")}),
        }
    )


def convergence_profile(*, evaluator_authority=("resolve", "residual", "supersede")):
    return profile(
        {
            "evaluate": phase(
                {
                    "accept": route("complete"),
                    "correct": route("goto", "correct"),
                },
                role="independent_reviewer",
                review="gate",
                finding_authority=evaluator_authority,
                convergence_role="evaluator",
            ),
            "correct": phase(
                {"evaluate": route("goto", "evaluate"), "complete": route("complete")},
                convergence_role="corrector",
            ),
        }
    )


def introduce_obligation(workflow=None):
    workflow = workflow or convergence_profile()
    run, hop_id = charge_hop(
        run_for(workflow, initial_phase="evaluate", run_schema_version=3),
        role="independent_reviewer",
    )
    reduced = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(new_delta("open"),),
        ),
        workflow,
        hop_id=hop_id,
    )
    return workflow, reduced.run


def test_finding_lifecycle_requires_gate_or_human_authority():
    workflow = gate_profile()
    run, _ = charge_hop(run_for(workflow))
    run = apply_finding_deltas(
        run,
        (new_delta("open"),),
        hop_id="H001",
        authority=FindingAuthority.AGENT,
    )
    with pytest.raises(ReductionError, match="agent cannot perform"):
        apply_finding_deltas(
            run,
            (existing_delta("F001", "resolved"),),
            hop_id="H001",
            authority=FindingAuthority.AGENT,
        )

    resolved = apply_finding_deltas(
        run,
        (existing_delta("F001", "resolved"),),
        hop_id="H001",
        authority=FindingAuthority.GATE,
        permitted_gate_operations=("resolve",),
    )
    assert resolved.findings[0].state is FindingState.RESOLVED


def test_finding_ids_are_allocated_for_null_and_unknown_non_null_ids_fail_closed():
    workflow = gate_profile()
    run, _ = charge_hop(run_for(workflow))

    created = apply_finding_deltas(
        run,
        (new_delta("open", invariant="A genuinely new current-Run Finding."),),
        hop_id="H001",
        authority=FindingAuthority.AGENT,
    )
    assert created.findings[0].finding_id == "F001"

    with pytest.raises(ReductionError, match="unknown Finding ID: M-5"):
        apply_finding_deltas(
            run,
            (existing_delta("M-5", "resolved"),),
            hop_id="H001",
            authority=FindingAuthority.GATE,
            permitted_gate_operations=("resolve",),
        )


def test_reopen_requires_authority_and_new_material_evidence():
    workflow = gate_profile()
    run, _ = charge_hop(run_for(workflow))
    opened = apply_finding_deltas(
        run, (new_delta("open"),), hop_id="H001", authority=FindingAuthority.AGENT
    )
    resolved = apply_finding_deltas(
        opened,
        (existing_delta("F001", "resolved"),),
        hop_id="H001",
        authority=FindingAuthority.HUMAN,
    )
    with pytest.raises(ReductionError, match="new material evidence"):
        apply_finding_deltas(
            resolved,
            (existing_delta("F001", "open"),),
            hop_id="H001",
            authority=FindingAuthority.HUMAN,
        )
    reopened = apply_finding_deltas(
        resolved,
        (existing_delta("F001", "open", evidence="new", new_material=True),),
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
            (new_delta("residual", blocking=True),),
            hop_id="H001",
            authority=FindingAuthority.HUMAN,
        )
    with pytest.raises(ReductionError, match="successor_ref"):
        apply_finding_deltas(
            run,
            (new_delta("superseded"),),
            hop_id="H001",
            authority=FindingAuthority.HUMAN,
        )


def test_existing_finding_derives_immutable_and_carried_forward_ledger_values():
    workflow = gate_profile()
    run, _ = charge_hop(run_for(workflow))
    opened = apply_finding_deltas(
        run, (new_delta("open", invariant="Ledger-owned invariant."),),
        hop_id="H001", authority=FindingAuthority.AGENT,
    )
    resolved = apply_finding_deltas(
        opened, (ExistingFindingDelta("F001", FindingState.RESOLVED, ("evidence-1",)),),
        hop_id="H001", authority=FindingAuthority.GATE,
        permitted_gate_operations=("resolve",),
    )
    assert resolved.findings[0].invariant == "Ledger-owned invariant."
    assert resolved.findings[0].blocking is True
    residual = apply_finding_deltas(
        opened, (ExistingFindingDelta("F001", FindingState.RESIDUAL, ("evidence-1",)),),
        hop_id="H001", authority=FindingAuthority.GATE,
        permitted_gate_operations=("residual",),
    )
    assert residual.findings[0].blocking is False
    with pytest.raises(TypeError, match="invariant"):
        ExistingFindingDelta(
            "F001", FindingState.RESOLVED, ("evidence-1",), invariant="attempted rewrite"
        )


@pytest.mark.parametrize(
    "state, operation, blocking, successor_ref",
    [
        ("resolved", "resolve", True, None),
        ("residual", "residual", False, None),
        ("superseded", "supersede", True, "F-successor"),
    ],
)
def test_new_finding_creation_uses_explicit_authority_operation(
    state, operation, blocking, successor_ref
):
    workflow = gate_profile()
    run, _ = charge_hop(run_for(workflow))
    proposal = new_delta(
        state,
        blocking=blocking,
        successor_ref=successor_ref,
    )

    with pytest.raises(ReductionError, match="cannot create Finding"):
        apply_finding_deltas(
            run,
            (proposal,),
            hop_id="H001",
            authority=FindingAuthority.GATE,
            permitted_gate_operations=(),
        )
    created = apply_finding_deltas(
        run,
        (proposal,),
        hop_id="H001",
        authority=FindingAuthority.GATE,
        permitted_gate_operations=(operation,),
    )
    assert created.findings[0].state.value == state


def test_supervisor_has_no_finding_disposition_surface():
    run, _ = charge_hop(run_for(gate_profile()))
    with pytest.raises(ReductionError, match="supervisor"):
        apply_finding_deltas(
            run,
            (new_delta("open"),),
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


def test_semantic_human_stop_cannot_enter_reconcile_or_resume_escape_path():
    workflow = profile({"work": phase({"done": route("complete")})})
    run, hop_id = charge_hop(run_for(workflow, budget=3))
    stopped = reduce_result(
        run,
        result(
            "done",
            requires_human=True,
            escalation=Escalation(
                "semantic_authority",
                "Who may accept this contract?",
            ),
        ),
        workflow,
        hop_id=hop_id,
    ).run

    with pytest.raises(ReductionError, match="invalid from Run status awaiting_human"):
        require_reconcile(stopped, reason="interruption", current_head="observed")
    assert stopped.status is RunStatus.AWAITING_HUMAN
    assert stopped.human_question == "Who may accept this contract?"
    with pytest.raises(ReductionError, match="does not require reconciliation"):
        resume_after_reconcile(stopped, workflow, current_head=stopped.current_head)


def test_reconcile_reason_has_a_specific_entry_state():
    workflow = gate_profile()
    ready = run_for(workflow)
    with pytest.raises(ReductionError, match="prerequisite_return.*ready"):
        require_reconcile(ready, reason="prerequisite_return", current_head="observed")
    suspended = replace(ready, status=RunStatus.SUSPENDED, suspension_head=ready.current_head)
    with pytest.raises(ReductionError, match="interruption.*suspended"):
        require_reconcile(suspended, reason="interruption", current_head="observed")


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


def test_correlated_semantic_stop_survives_later_mechanical_preemption():
    workflow = profile({"work": phase({"done": route("complete")})})
    run, hop_id = charge_hop(run_for(workflow))
    reduced = reduce_result(
        run,
        result("done", scope_changed=True, requires_human=True,
               escalation=Escalation("scope", "May the WorkItem include the missing test?")),
        workflow,
        hop_id=hop_id,
        facts=MechanicalFacts(repository_anomaly="dirty tracked state; forbidden CommitIntent"),
    )
    assert reduced.run.status is RunStatus.AWAITING_HUMAN
    assert "dirty tracked state" in (reduced.run.stop_reason or "")
    assert reduced.run.anomalies == ("dirty tracked state; forbidden CommitIntent",)
    assert reduced.run.semantic_stop and reduced.run.semantic_stop.kind == "scope"
    assert reduced.run.semantic_stop.question == "May the WorkItem include the missing test?"
    assert reduced.run.semantic_stop.source_hop_id == hop_id


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


def test_reducer_semantics_are_independent_of_role_persona_binding_provider_and_domain():
    worker_profile = profile({"work": phase({"done": route("complete")})})
    reviewer_profile = profile(
        {
            "work": replace(
                worker_profile.phases["work"],
                role=WorkflowRole.INDEPENDENT_REVIEWER,
            )
        }
    )
    worker_run, worker_hop = charge_hop(run_for(worker_profile, budget=1))
    reviewer_run, reviewer_hop = charge_hop(run_for(reviewer_profile, budget=1))

    assert reduce_result(
        worker_run, result("done"), worker_profile, hop_id=worker_hop
    ) == reduce_result(
        reviewer_run, result("done"), reviewer_profile, hop_id=reviewer_hop
    )
    assert {
        "persona",
        "adapter",
        "provider",
        "model",
        "reasoning",
        "subject_domain",
    }.isdisjoint(inspect.signature(reduce_result).parameters)


def test_evaluator_introduces_a_gate_owned_obligation_and_result_v1_stays_ordinary():
    _, tracked = introduce_obligation()
    finding = tracked.findings[0]
    assert finding.finding_id == "F001"
    assert finding.correction_obligation == CorrectionObligation(
        CorrectionObligationOwner.GATE, "H001"
    )

    ordinary = gate_profile()
    legacy, _ = charge_hop(run_for(ordinary))
    created = apply_finding_deltas(
        legacy, (new_delta("open"),), hop_id="H001", authority=FindingAuthority.AGENT
    )
    assert created.findings[0].correction_obligation is None


def test_result_v2_non_evaluator_open_blocker_creation_is_atomic():
    workflow = profile(
        {"correct": phase({"again": route("goto", "correct")}, convergence_role="corrector")}
    )
    run, hop_id = charge_hop(
        run_for(workflow, initial_phase="correct", run_schema_version=3)
    )
    reduced = reduce_result(
        run,
        result("again", schema_version=2, findings=(new_delta("open"),)),
        workflow,
        hop_id=hop_id,
    )
    assert reduced.run.findings == ()
    assert reduced.run.status is RunStatus.AWAITING_HUMAN


def test_corrector_coverage_is_exact_and_addressed_never_disposes_or_completes():
    workflow, run = introduce_obligation()
    run, hop_id = charge_hop(run)
    missing = reduce_result(
        run, result("evaluate", schema_version=2), workflow, hop_id=hop_id
    )
    assert "cover every" in (missing.run.stop_reason or "")
    duplicate = reduce_result(
        run,
        result(
            "evaluate", schema_version=2,
            coverage_claims=(coverage("F001"), coverage("F001")),
        ),
        workflow,
        hop_id=hop_id,
    )
    assert "duplicate coverage" in (duplicate.run.stop_reason or "")
    completed = reduce_result(
        run,
        result(
            "complete", schema_version=2,
            coverage_claims=(coverage("F001", "addressed", "worker-proof"),),
        ),
        workflow,
        hop_id=hop_id,
    )
    assert completed.run.findings[0].state is FindingState.OPEN
    assert completed.run.findings[0].evidence_refs == ("evidence-1",)
    assert "addressed coverage cannot" in (completed.run.stop_reason or "")


@pytest.mark.parametrize("finding_id", ["unknown", "F999"])
def test_corrector_rejects_unknown_coverage(finding_id):
    workflow, run = introduce_obligation()
    run, hop_id = charge_hop(run)
    reduced = reduce_result(
        run,
        result(
            "evaluate", schema_version=2,
            coverage_claims=(coverage(finding_id),),
        ),
        workflow,
        hop_id=hop_id,
    )
    assert reduced.run.status is RunStatus.AWAITING_HUMAN


@pytest.mark.parametrize("state", [FindingState.RESOLVED, FindingState.SUPERSEDED])
def test_corrector_rejects_coverage_of_closed_or_superseded_obligations(state):
    workflow, run = introduce_obligation()
    run = replace(
        run,
        findings=(replace(run.findings[0], state=state),),
    )
    run, hop_id = charge_hop(run)
    reduced = reduce_result(
        run,
        result(
            "evaluate",
            schema_version=2,
            coverage_claims=(coverage("F001"),),
        ),
        workflow,
        hop_id=hop_id,
    )
    assert "closed, superseded, or untracked" in (reduced.run.stop_reason or "")


def test_evaluator_assessment_is_total_and_residualization_is_nonblocking():
    workflow, run = introduce_obligation()
    run, correction_hop = charge_hop(run)
    run = reduce_result(
        run,
        result("evaluate", schema_version=2, coverage_claims=(coverage("F001"),)),
        workflow,
        hop_id=correction_hop,
    ).run
    run, evaluator_hop = charge_hop(run, role="independent_reviewer")
    reduced = reduce_result(
        run,
        result(
            "correct", outcome="changes_required", schema_version=2,
            findings=(existing_delta("F001", "residual", evidence="gate-proof"),),
            convergence_assessments=(
                assessment("F001", correction_hop, "residualized", "gate-proof"),
            ),
        ),
        workflow,
        hop_id=evaluator_hop,
    )
    finding = reduced.run.findings[0]
    assert finding.state is FindingState.RESIDUAL and finding.blocking is False

    missing = reduce_result(
        run,
        result(
            "correct", outcome="changes_required", schema_version=2,
            findings=(existing_delta("F001", "residual"),),
        ),
        workflow,
        hop_id=evaluator_hop,
    )
    assert missing.run.findings[0].state is FindingState.OPEN


def test_evaluator_cannot_reassess_a_consumed_correction_hop():
    workflow = convergence_profile()
    workflow = replace(
        workflow,
        phases={
            **workflow.phases,
            "evaluate": replace(
                workflow.phases["evaluate"],
                routes={
                    **workflow.phases["evaluate"].routes,
                    "again": route("goto", "evaluate"),
                },
            ),
        },
    )
    _, run = introduce_obligation(workflow)
    run = replace(run, hop_limit=8)
    run, first_correction_hop = charge_hop(run)
    run = reduce_result(
        run,
        result(
            "evaluate",
            schema_version=2,
            coverage_claims=(coverage("F001"),),
        ),
        workflow,
        hop_id=first_correction_hop,
    ).run
    run, first_evaluator_hop = charge_hop(run, role="independent_reviewer")
    first_assessment = reduce_result(
        run,
        result(
            "again",
            outcome="changes_required",
            schema_version=2,
            findings=(existing_delta("F001", "open", evidence="first-gate-evidence"),),
            convergence_assessments=(
                assessment("F001", first_correction_hop, "carried_unmet"),
            ),
        ),
        workflow,
        hop_id=first_evaluator_hop,
    )
    accepted = first_assessment.run

    duplicate_input, duplicate_evaluator_hop = charge_hop(
        accepted, role="independent_reviewer"
    )
    duplicate = reduce_result(
        duplicate_input,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(
                existing_delta("F001", "open", evidence="duplicate-gate-evidence"),
            ),
            convergence_assessments=(
                assessment("F001", first_correction_hop, "carried_unmet"),
            ),
        ),
        workflow,
        hop_id=duplicate_evaluator_hop,
    )
    assert "correction Hop already assessed" in (duplicate.run.stop_reason or "")
    assert duplicate.route_key is None
    assert duplicate.run.phase == "evaluate"
    assert duplicate.run.findings == duplicate_input.findings
    assert duplicate.run.hops[-1].finding_delta_ids == ()
    assert duplicate.run.hops[-1].convergence_assessments == ()

    run, transition_hop = charge_hop(accepted, role="independent_reviewer")
    run = reduce_result(
        run,
        result("correct", outcome="changes_required", schema_version=2),
        workflow,
        hop_id=transition_hop,
    ).run
    run, second_correction_hop = charge_hop(run)
    run = reduce_result(
        run,
        result(
            "evaluate",
            schema_version=2,
            coverage_claims=(coverage("F001"),),
        ),
        workflow,
        hop_id=second_correction_hop,
    ).run
    run, second_evaluator_hop = charge_hop(run, role="independent_reviewer")
    second_assessment = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(existing_delta("F001", "open", evidence="second-gate-evidence"),),
            convergence_assessments=(
                assessment("F001", second_correction_hop, "carried_unmet"),
            ),
        ),
        workflow,
        hop_id=second_evaluator_hop,
    )
    assert second_assessment.route_key == "correct"
    assert run_to_json(second_assessment.run)


def test_residualized_assessment_rejects_mismatch_and_missing_authority_atomically():
    workflow, run = introduce_obligation()
    run, correction_hop = charge_hop(run)
    run = reduce_result(
        run,
        result(
            "evaluate",
            schema_version=2,
            coverage_claims=(coverage("F001"),),
        ),
        workflow,
        hop_id=correction_hop,
    ).run
    run, evaluator_hop = charge_hop(run, role="independent_reviewer")
    mismatch = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(existing_delta("F001", "residual"),),
            convergence_assessments=(
                assessment("F001", correction_hop, "closed"),
            ),
        ),
        workflow,
        hop_id=evaluator_hop,
    )
    assert mismatch.run.findings == run.findings
    assert "does not match Finding delta" in (mismatch.run.stop_reason or "")

    no_residual_authority = convergence_profile(evaluator_authority=("resolve",))
    unauthorized = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(existing_delta("F001", "residual"),),
            convergence_assessments=(
                assessment("F001", correction_hop, "residualized"),
            ),
        ),
        no_residual_authority,
        hop_id=evaluator_hop,
    )
    assert unauthorized.run.findings == run.findings
    assert "cannot perform Finding operation residual" in (
        unauthorized.run.stop_reason or ""
    )


def test_carried_new_counterexample_requires_previously_absent_ledger_evidence():
    workflow, run = introduce_obligation()
    run, correction_hop = charge_hop(run)
    run = reduce_result(
        run,
        result("evaluate", schema_version=2, coverage_claims=(coverage("F001"),)),
        workflow,
        hop_id=correction_hop,
    ).run
    run, evaluator_hop = charge_hop(run, role="independent_reviewer")
    rejected = reduce_result(
        run,
        result(
            "correct", outcome="changes_required", schema_version=2,
            findings=(existing_delta("F001", "open", evidence="evidence-1"),),
            convergence_assessments=(assessment(
                "F001", correction_hop, "carried_new_counterexample", "evidence-1"
            ),),
        ),
        workflow,
        hop_id=evaluator_hop,
    )
    assert rejected.run.findings == run.findings
    assert "new Finding evidence" in (rejected.run.stop_reason or "")


def test_carried_new_counterexample_rejects_all_prior_same_finding_evidence_reuse():
    workflow, run = introduce_obligation()
    run = replace(run, hop_limit=8)
    run, first_correction_hop = charge_hop(run)
    run = reduce_result(
        run,
        result(
            "evaluate",
            schema_version=2,
            coverage_claims=(
                coverage("F001", "addressed", "older-coverage-evidence"),
            ),
        ),
        workflow,
        hop_id=first_correction_hop,
    ).run
    run, first_evaluator_hop = charge_hop(run, role="independent_reviewer")
    run = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(existing_delta("F001", "open", evidence="first-gate-evidence"),),
            convergence_assessments=(
                assessment(
                    "F001",
                    first_correction_hop,
                    "carried_unmet",
                    "older-unmet-assessment-evidence",
                ),
            ),
        ),
        workflow,
        hop_id=first_evaluator_hop,
    ).run
    run, second_correction_hop = charge_hop(run)
    run = reduce_result(
        run,
        result(
            "evaluate",
            schema_version=2,
            coverage_claims=(
                coverage("F001", "addressed", "current-coverage-evidence"),
            ),
        ),
        workflow,
        hop_id=second_correction_hop,
    ).run
    run, second_evaluator_hop = charge_hop(run, role="independent_reviewer")

    reused_coverage = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(
                existing_delta("F001", "open", evidence="older-coverage-evidence"),
                new_delta(
                    "open",
                    invariant="A separately discovered obligation.",
                    local_ref="N2",
                ),
            ),
            convergence_assessments=(
                assessment(
                    "F001",
                    second_correction_hop,
                    "carried_new_counterexample",
                    "older-coverage-evidence",
                ),
            ),
        ),
        workflow,
        hop_id=second_evaluator_hop,
    )
    assert reused_coverage.run.findings == run.findings
    assert reused_coverage.run.phase == run.phase
    assert reused_coverage.run.current_head == run.current_head
    assert reused_coverage.run.hops[-1].convergence_assessments == ()
    assert reused_coverage.run.hops[-1].applied_route is None
    assert "new Finding evidence" in (reused_coverage.run.stop_reason or "")

    reused_assessment = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(
                existing_delta(
                    "F001", "open", evidence="older-unmet-assessment-evidence"
                ),
            ),
            convergence_assessments=(
                assessment(
                    "F001",
                    second_correction_hop,
                    "carried_new_counterexample",
                    "older-unmet-assessment-evidence",
                ),
            ),
        ),
        workflow,
        hop_id=second_evaluator_hop,
    )
    assert reused_assessment.run.findings == run.findings
    assert reused_assessment.run.phase == run.phase
    assert reused_assessment.run.current_head == run.current_head
    assert reused_assessment.run.hops[-1].convergence_assessments == ()
    assert reused_assessment.run.hops[-1].applied_route is None
    assert "new Finding evidence" in (reused_assessment.run.stop_reason or "")

    accepted = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(
                existing_delta("F001", "open", evidence="gate-counterexample"),
            ),
            convergence_assessments=(
                assessment(
                    "F001",
                    second_correction_hop,
                    "carried_new_counterexample",
                    "gate-counterexample",
                ),
            ),
        ),
        workflow,
        hop_id=second_evaluator_hop,
    )
    assert accepted.run.phase == "correct"
    assert accepted.run.findings[0].evidence_refs[-1] == "gate-counterexample"
    assert (
        accepted.run.hops[-1].convergence_assessments[0].evidence_refs
        == ("gate-counterexample",)
    )
    assert '"evidence_refs":["gate-counterexample"]' in run_to_json(accepted.run)


def test_same_result_local_successor_is_resolved_without_prediction():
    workflow, run = introduce_obligation()
    run, correction_hop = charge_hop(run)
    run = reduce_result(
        run,
        result("evaluate", schema_version=2, coverage_claims=(coverage("F001"),)),
        workflow,
        hop_id=correction_hop,
    ).run
    run, evaluator_hop = charge_hop(run, role="independent_reviewer")
    reduced = reduce_result(
        run,
        result(
            "correct", outcome="changes_required", schema_version=2,
            findings=(
                existing_delta("F001", "superseded", successor_ref="local:N7"),
                new_delta("open", invariant="A distinct obligation.", local_ref="N7"),
            ),
            convergence_assessments=(assessment("F001", correction_hop, "superseded"),),
        ),
        workflow,
        hop_id=evaluator_hop,
    )
    assert [finding.finding_id for finding in reduced.run.findings] == ["F001", "F002"]
    assert reduced.run.findings[0].successor_ref == "F002"


@pytest.mark.parametrize(
    "deltas, message",
    [
        ((new_delta("open", local_ref="N1"), new_delta("open", local_ref="N1")), "duplicate local_ref"),
        ((new_delta("open", local_ref="F002"),), "local_ref must match"),
        ((existing_delta("F001", "superseded", successor_ref="local:N404"),), "unknown local successor"),
        ((existing_delta("F001", "superseded", successor_ref="local:N2"), new_delta("open", blocking=False, local_ref="N2")), "open blocking tracked obligation"),
        ((existing_delta("F001", "superseded", successor_ref="local:N2"), new_delta("resolved", local_ref="N2")), "open blocking tracked obligation"),
        ((existing_delta("F001", "superseded", successor_ref="F002"),), "unknown concrete successor"),
        ((new_delta("superseded", successor_ref="local:N1", local_ref="N1"),), "only on existing-Finding deltas"),
        ((existing_delta("F001", "superseded", successor_ref="F001"),), "cycle"),
    ],
)
def test_local_reference_failures_are_atomic_and_consume_no_id(deltas, message):
    _, run = introduce_obligation()
    with pytest.raises(ReductionError, match=message):
        apply_finding_deltas(
            run, deltas, hop_id="H002", authority=FindingAuthority.GATE,
            permitted_gate_operations=("supersede", "resolve"),
            convergence_role=ConvergenceRole.EVALUATOR, result_schema_version=2,
        )
    accepted = apply_finding_deltas(
        run, (new_delta("open", local_ref="N9"),), hop_id="H002",
        authority=FindingAuthority.GATE,
        convergence_role=ConvergenceRole.EVALUATOR, result_schema_version=2,
    )
    assert accepted.findings[-1].finding_id == "F002"


def test_new_blocker_gets_a_distinct_identity_without_mutating_unrelated_obligation():
    _, run = introduce_obligation()
    original = run.findings[0]
    updated = apply_finding_deltas(
        run,
        (
            new_delta(
                "open",
                invariant="A separately discovered obligation.",
                evidence="new-obligation-evidence",
                local_ref="N2",
            ),
        ),
        hop_id="H002",
        authority=FindingAuthority.GATE,
        convergence_role=ConvergenceRole.EVALUATOR,
        result_schema_version=2,
    )
    assert [finding.finding_id for finding in updated.findings] == ["F001", "F002"]
    assert updated.findings[0] == original
    assert updated.findings[1].evidence_refs == ("new-obligation-evidence",)


def test_direct_and_transitive_supersession_cycles_reject_atomically():
    workflow = convergence_profile()
    run, hop_id = charge_hop(
        run_for(workflow, initial_phase="evaluate", run_schema_version=3),
        role="independent_reviewer",
    )
    run = apply_finding_deltas(
        run,
        (new_delta("open"), new_delta("open", invariant="Second"), new_delta("open", invariant="Third")),
        hop_id=hop_id, authority=FindingAuthority.GATE,
        convergence_role=ConvergenceRole.EVALUATOR, result_schema_version=2,
    )
    cases = (
        (existing_delta("F001", "superseded", successor_ref="F002"), existing_delta("F002", "superseded", successor_ref="F001")),
        (existing_delta("F001", "superseded", successor_ref="F002"), existing_delta("F002", "superseded", successor_ref="F003"), existing_delta("F003", "superseded", successor_ref="F001")),
    )
    for deltas in cases:
        with pytest.raises(ReductionError, match="cycle"):
            apply_finding_deltas(
                run, deltas, hop_id="H002", authority=FindingAuthority.GATE,
                permitted_gate_operations=("supersede",),
                convergence_role=ConvergenceRole.EVALUATOR, result_schema_version=2,
            )
        assert all(finding.state is FindingState.OPEN for finding in run.findings)


def test_evaluator_reopen_authority_is_rejected_but_simple_reopen_remains():
    workflow = convergence_profile(evaluator_authority=("resolve", "reopen"))
    run, hop_id = charge_hop(
        run_for(workflow, initial_phase="evaluate", run_schema_version=3),
        role="independent_reviewer",
    )
    rejected = reduce_result(
        run, result("correct", outcome="changes_required", schema_version=2),
        workflow, hop_id=hop_id,
    )
    assert "reopen authority" in (rejected.run.stop_reason or "")

    simple = gate_profile()
    legacy, _ = charge_hop(run_for(simple))
    opened = apply_finding_deltas(legacy, (new_delta("open"),), hop_id="H001", authority=FindingAuthority.AGENT)
    resolved = apply_finding_deltas(opened, (existing_delta("F001", "resolved"),), hop_id="H001", authority=FindingAuthority.GATE, permitted_gate_operations=("resolve",))
    reopened = apply_finding_deltas(resolved, (existing_delta("F001", "open", evidence="new", new_material=True),), hop_id="H001", authority=FindingAuthority.GATE, permitted_gate_operations=("reopen",))
    assert reopened.findings[0].state is FindingState.OPEN


def test_new_core_vocabulary_is_domain_neutral():
    names = {
        field.name
        for record in (CorrectionObligation, CoverageClaim, ConvergenceAssessment)
        for field in fields(record)
    }
    values = {
        item.value
        for enum_type in (
            ConvergenceRole,
            CorrectionObligationOwner,
            CoverageDisposition,
            ConvergenceAssessmentResult,
        )
        for item in enum_type
    }
    text = " ".join(sorted(names | values)).lower()
    for forbidden in ("refactor", "python test", "cyclomatic complexity"):
        assert forbidden not in text


def test_derived_convergence_fact_counts_qualified_cycles_at_threshold_without_routing():
    workflow, run = tracked_convergence_run()

    run, _, first_evaluator_hop = convergence_cycle(
        run, workflow, evidence_prefix="first"
    )
    run, qualification, _ = qualification_evidence(
        run, {first_evaluator_hop: ("pass",)}
    )
    assert derive_convergence_facts(run, qualification, 2) == (
        ConvergenceFact(
            finding_id="F001",
            strategy_generation=0,
            qualified_cycle_count=1,
            qualified_cycle_limit=2,
            goalpost_reached=False,
            source_evaluator_hops=(first_evaluator_hop,),
        ),
    )
    assert run.phase == "correct"

    run, _, second_evaluator_hop = convergence_cycle(
        run, workflow, evidence_prefix="second"
    )
    run, qualification, _ = qualification_evidence(
        run,
        {
            first_evaluator_hop: ("pass",),
            second_evaluator_hop: ("pass",),
        },
    )
    assert derive_convergence_facts(run, qualification, 2) == (
        ConvergenceFact(
            finding_id="F001",
            strategy_generation=0,
            qualified_cycle_count=2,
            qualified_cycle_limit=2,
            goalpost_reached=True,
            source_evaluator_hops=(first_evaluator_hop, second_evaluator_hop),
        ),
    )
    assert run.phase == "correct"
    assert {hop.applied_route for hop in run.hops} <= {None, "correct", "evaluate"}


def test_derived_convergence_facts_exclude_carried_unmet_and_new_finding_allocation():
    workflow, run = tracked_convergence_run()
    run, _, _ = convergence_cycle(
        run,
        workflow,
        assessment_result="carried_unmet",
        evidence_prefix="unmet",
    )
    assert derive_convergence_facts(
        run, TrustedQualificationIndex.empty(), 2
    )[0].qualified_cycle_count == 0

    run, _, evaluator_hop = convergence_cycle(
        run,
        workflow,
        evidence_prefix="qualified",
        new_findings=(
            new_delta(
                "open",
                invariant="A newly discovered independent invariant remains open.",
                evidence="new-finding-evidence",
            ),
        ),
    )
    run, qualification, _ = qualification_evidence(
        run, {evaluator_hop: ("pass",)}
    )
    assert derive_convergence_facts(run, qualification, 2) == (
        ConvergenceFact("F001", 0, 1, 2, False, (evaluator_hop,)),
        ConvergenceFact("F002", 0, 0, 2, False, ()),
    )


def test_stale_evidence_rejection_cannot_fabricate_a_derived_cycle():
    workflow, run = tracked_convergence_run()
    run, _, _ = convergence_cycle(
        run,
        workflow,
        assessment_result="carried_unmet",
        evidence_prefix="older",
    )
    run, correction_hop = charge_hop(run)
    run = reduce_result(
        run,
        result(
            "evaluate",
            schema_version=2,
            coverage_claims=(coverage("F001", "addressed", "current-coverage"),),
        ),
        workflow,
        hop_id=correction_hop,
    ).run
    run, evaluator_hop = charge_hop(run, role="independent_reviewer")
    rejected = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(
                existing_delta("F001", "open", evidence="older-evaluation-F001"),
            ),
            convergence_assessments=(
                assessment(
                    "F001",
                    correction_hop,
                    "carried_new_counterexample",
                    "older-evaluation-F001",
                ),
            ),
        ),
        workflow,
        hop_id=evaluator_hop,
    )
    assert "new Finding evidence" in (rejected.run.stop_reason or "")
    assert derive_convergence_facts(
        rejected.run, TrustedQualificationIndex.empty(), 2
    )[0].qualified_cycle_count == 0


@pytest.mark.parametrize("failed_before_acceptance", [False, True])
def test_uncorrelated_or_failed_evaluator_result_cannot_fabricate_a_cycle(
    failed_before_acceptance,
):
    workflow, run = tracked_convergence_run()
    run, correction_hop = charge_hop(run)
    run = reduce_result(
        run,
        result(
            "evaluate",
            schema_version=2,
            coverage_claims=(coverage("F001", "addressed", "coverage"),),
        ),
        workflow,
        hop_id=correction_hop,
    ).run
    run, evaluator_hop = charge_hop(run, role="independent_reviewer")
    rejected = reduce_result(
        run,
        result(
            "correct",
            outcome="failed" if failed_before_acceptance else "changes_required",
            schema_version=2,
            findings=(existing_delta("F001", "open", evidence="counterexample"),),
            convergence_assessments=(
                assessment(
                    "F001",
                    correction_hop if failed_before_acceptance else "H999",
                    "carried_new_counterexample",
                    "counterexample",
                ),
            ),
        ),
        workflow,
        hop_id=evaluator_hop,
    )
    assert rejected.run.hops[-1].convergence_assessments == ()
    assert derive_convergence_facts(
        rejected.run, TrustedQualificationIndex.empty(), 2
    )[0].qualified_cycle_count == 0


def test_closed_obligation_has_no_still_open_convergence_fact():
    workflow, run = tracked_convergence_run()
    run, _, _ = convergence_cycle(run, workflow, evidence_prefix="qualified")
    run, _, _ = convergence_cycle(
        run,
        workflow,
        assessment_result="closed",
        evidence_prefix="closed",
    )
    assert derive_convergence_facts(
        run, TrustedQualificationIndex.empty(), 2
    ) == ()


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "2"])
def test_derived_convergence_facts_require_a_positive_integer_limit(limit):
    _, run = tracked_convergence_run()
    with pytest.raises(ValueError, match="positive integer"):
        derive_convergence_facts(run, TrustedQualificationIndex.empty(), limit)


def test_convergence_fact_is_not_mutable_run_or_finding_shadow_state():
    forbidden = {"qualified_cycle_count", "qualified_cycle_limit", "goalpost_reached"}
    assert forbidden.isdisjoint(field.name for field in fields(Run))
    assert forbidden.isdisjoint(field.name for field in fields(Finding))


@pytest.mark.parametrize("status", ["fail", "error", "not_run"])
def test_semantic_cycle_requires_exact_passing_evaluator_evidence(status):
    workflow, run = tracked_convergence_run()
    run, _, evaluator_hop = convergence_cycle(
        run, workflow, evidence_prefix=f"mechanical-{status}"
    )
    run, qualification, _ = qualification_evidence(
        run, {evaluator_hop: (status,)}
    )
    fact = derive_convergence_facts(run, qualification, 2)[0]
    assert fact.qualified_cycle_count == 0
    assert fact.source_evaluator_hops == ()


def test_missing_partial_and_multi_command_evidence_do_not_vacuously_qualify():
    workflow, run = tracked_convergence_run()
    run, _, evaluator_hop = convergence_cycle(
        run, workflow, evidence_prefix="multi-command"
    )
    assert derive_convergence_facts(
        run, TrustedQualificationIndex.empty(), 2
    )[0].qualified_cycle_count == 0

    run, partial, _ = qualification_evidence(
        run,
        {evaluator_hop: ("pass",)},
        command_names=("tests", "lint"),
    )
    assert derive_convergence_facts(run, partial, 2)[0].qualified_cycle_count == 0

    for second_status in ("fail", "error", "not_run"):
        run, mixed, _ = qualification_evidence(
            run,
            {evaluator_hop: ("pass", second_status)},
            command_names=("tests", "lint"),
        )
        assert derive_convergence_facts(run, mixed, 2)[0].qualified_cycle_count == 0

    run, passing, _ = qualification_evidence(
        run,
        {evaluator_hop: ("pass", "pass")},
        command_names=("tests", "lint"),
    )
    assert derive_convergence_facts(run, passing, 2)[0].source_evaluator_hops == (
        evaluator_hop,
    )


def test_exact_empty_required_plan_qualifies_but_absent_attempt_does_not():
    workflow, run = tracked_convergence_run()
    run, _, evaluator_hop = convergence_cycle(
        run, workflow, evidence_prefix="empty-plan"
    )
    absent = derive_convergence_facts(run, TrustedQualificationIndex.empty(), 2)[0]
    assert absent.qualified_cycle_count == 0
    run, empty_plan, _ = qualification_evidence(
        run, {evaluator_hop: ()}, command_names=()
    )
    assert derive_convergence_facts(run, empty_plan, 2)[0].source_evaluator_hops == (
        evaluator_hop,
    )


def test_mechanical_pass_cannot_create_a_semantic_cycle_or_disposition():
    workflow, run = tracked_convergence_run()
    run, _, evaluator_hop = convergence_cycle(
        run,
        workflow,
        assessment_result="carried_unmet",
        evidence_prefix="semantic-unmet",
    )
    before = run.findings
    run, qualification, _ = qualification_evidence(
        run, {evaluator_hop: ("pass",)}
    )
    fact = derive_convergence_facts(run, qualification, 2)[0]
    assert fact.qualified_cycle_count == 0
    assert run.findings == before
    assert run.findings[0].state is FindingState.OPEN


def test_one_failed_attempt_removes_only_its_source_hop_without_off_by_one():
    workflow, run = tracked_convergence_run()
    run, _, first = convergence_cycle(run, workflow, evidence_prefix="first-pair")
    run, _, second = convergence_cycle(run, workflow, evidence_prefix="second-pair")
    run, qualification, _ = qualification_evidence(
        run, {first: ("pass",), second: ("fail",)}
    )
    fact = derive_convergence_facts(run, qualification, 2)[0]
    assert fact.qualified_cycle_count == 1
    assert fact.goalpost_reached is False
    assert fact.source_evaluator_hops == (first,)


def _reduce_qualified_slice3_evaluator(
    run,
    workflow,
    *,
    evidence_prefix,
    assessment_result="carried_new_counterexample",
    facts=MechanicalFacts(),
    current_evidence_status="pass",
    new_findings=(),
):
    tracked_ids = tuple(
        finding.finding_id
        for finding in run.findings
        if finding.state is FindingState.OPEN and finding.blocking
    )
    corrected, correction_hop = charge_hop(run)
    corrected = reduce_result(
        corrected,
        result(
            "evaluate",
            schema_version=2,
            coverage_claims=tuple(
                coverage(finding_id, "addressed", f"{evidence_prefix}-coverage-{finding_id}")
                for finding_id in tracked_ids
            ),
        ),
        workflow,
        hop_id=correction_hop,
    ).run
    evaluated, evaluator_hop = charge_hop(
        corrected,
        role="independent_reviewer",
        outcome="changes_required",
    )
    qualifying_hops = {
        hop.hop_id: ("pass",)
        for hop in evaluated.hops
        if hop.convergence_assessments
    }
    qualifying_hops[evaluator_hop] = (current_evidence_status,)
    evaluated, qualification, _ = qualification_evidence(
        evaluated, qualifying_hops
    )
    counterexamples = {
        finding_id: f"{evidence_prefix}-evaluation-{finding_id}"
        for finding_id in tracked_ids
    }
    proposed_state = "resolved" if assessment_result == "closed" else "open"
    reduction = reduce_result(
        evaluated,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=tuple(
                existing_delta(
                    finding_id, proposed_state, evidence=counterexamples[finding_id]
                )
                for finding_id in tracked_ids
            ) + tuple(new_findings),
            convergence_assessments=tuple(
                assessment(
                    finding_id,
                    correction_hop,
                    assessment_result,
                    counterexamples[finding_id],
                )
                for finding_id in tracked_ids
            ),
        ),
        workflow,
        hop_id=evaluator_hop,
        facts=facts,
        trusted_qualification_index=qualification,
    )
    return reduction, evaluator_hop


def test_profile_v3_reserved_escape_is_hidden_and_direct_request_fails_closed():
    workflow = convergence_routing_profile()
    _, run = tracked_convergence_run()
    run = replace(run, phase="evaluate")
    assert permitted_route_keys(run, workflow) == ("accept", "correct")

    charged, hop_id = charge_hop(
        run, role="independent_reviewer", outcome="changes_required"
    )
    charged, qualification, _ = qualification_evidence(
        charged, {hop_id: ("pass",)}
    )
    before = (charged.findings, charged.hop_used, charged.phase)
    rejected = reduce_result(
        charged,
        result("diagnose", outcome="changes_required", schema_version=2),
        workflow,
        hop_id=hop_id,
        trusted_qualification_index=qualification,
    )
    assert "reserved_route:diagnose" in (rejected.run.stop_reason or "")
    assert (rejected.run.findings, rejected.run.hop_used, rejected.run.phase) == before
    assert rejected.run.hops[-1].applied_route is None


def test_qualified_cycle_below_limit_uses_normal_route_and_threshold_substitutes_escape():
    workflow = convergence_routing_profile(qualified_cycle_limit=2)
    _, run = tracked_convergence_run()

    below, first_evaluator = _reduce_qualified_slice3_evaluator(
        run, workflow, evidence_prefix="first"
    )
    assert below.route_key == "correct"
    assert below.run.hops[-1].requested_route == "correct"
    assert below.run.hops[-1].applied_route == "correct"
    assert below.run.hops[-1].route_reason is None

    threshold, second_evaluator = _reduce_qualified_slice3_evaluator(
        below.run, workflow, evidence_prefix="second"
    )
    assert threshold.route_key == "diagnose"
    assert threshold.run.phase == "diagnose"
    assert threshold.run.hops[-1].hop_id == second_evaluator
    assert threshold.run.hops[-1].requested_route == "correct"
    assert threshold.run.hops[-1].applied_route == "diagnose"
    assert threshold.run.hops[-1].route_reason == "convergence_escape:F001"
    replayed = run_to_json(threshold.run)
    assert '"requested_route":"correct"' in replayed
    assert '"applied_route":"diagnose"' in replayed
    assert first_evaluator in {
        source
        for fact in derive_convergence_facts(
            threshold.run,
            qualification_evidence(
                threshold.run,
                {first_evaluator: ("pass",), second_evaluator: ("pass",)},
            )[1],
            2,
        )
        for source in fact.source_evaluator_hops
    }

    anchored, qualification, _ = qualification_evidence(
        threshold.run,
        {first_evaluator: ("pass",), second_evaluator: ("pass",)},
    )
    validate_convergence_route_history(anchored, workflow, qualification)


def test_supervisor_gated_threshold_escape_is_replay_valid_while_pending():
    workflow = convergence_routing_profile(
        qualified_cycle_limit=2,
        escape_supervisor=True,
    )
    _, run = tracked_convergence_run()
    first, first_evaluator = _reduce_qualified_slice3_evaluator(
        run, workflow, evidence_prefix="first-supervised"
    )

    pending, second_evaluator = _reduce_qualified_slice3_evaluator(
        first.run, workflow, evidence_prefix="second-supervised"
    )
    assert pending.route_key is None
    assert pending.supervisor_required is True
    assert pending.run.status is RunStatus.AWAITING_SUPERVISOR
    assert pending.run.stop_reason == "supervisor_judgment_required"
    assert pending.run.hops[-1].requested_route == "correct"
    assert pending.run.hops[-1].applied_route is None
    assert pending.run.hops[-1].route_reason == "convergence_escape:F001"

    anchored, qualification, _ = qualification_evidence(
        pending.run,
        {first_evaluator: ("pass",), second_evaluator: ("pass",)},
    )
    replayed = run_from_json(run_to_json(anchored))
    validate_convergence_route_history(replayed, workflow, qualification)

    supervised, _ = charge_hop(
        replayed,
        role="routing supervisor",
        attribution="supervisor",
    )
    confirmed = confirm_supervisor_route(supervised, workflow, "diagnose")
    assert confirmed.route_key == "diagnose"
    assert confirmed.run.phase == "diagnose"
    assert confirmed.run.hops[-2].applied_route is None
    validate_convergence_route_history(confirmed.run, workflow, qualification)


@pytest.mark.parametrize(
    ("profile_changes", "hop_changes"),
    [
        ({"escape_supervisor": False}, {}),
        ({"escape_supervisor": True}, {"stop_reason": "arbitrary_stop"}),
    ],
)
def test_pending_escape_history_rejects_non_supervisor_route_or_wrong_stop_reason(
    profile_changes, hop_changes
):
    supervised = convergence_routing_profile(
        qualified_cycle_limit=2,
        escape_supervisor=True,
    )
    _, run = tracked_convergence_run()
    first, first_evaluator = _reduce_qualified_slice3_evaluator(
        run, supervised, evidence_prefix="first-invalid-pending"
    )
    pending, second_evaluator = _reduce_qualified_slice3_evaluator(
        first.run, supervised, evidence_prefix="second-invalid-pending"
    )
    anchored, qualification, _ = qualification_evidence(
        pending.run,
        {first_evaluator: ("pass",), second_evaluator: ("pass",)},
    )
    corrupted = replace(
        anchored,
        hops=tuple(
            replace(hop, **hop_changes)
            if hop.hop_id == second_evaluator
            else hop
            for hop in anchored.hops
        ),
    )
    workflow = convergence_routing_profile(
        qualified_cycle_limit=2,
        **profile_changes,
    )

    with pytest.raises(ReductionError, match="lacks an applied escape route"):
        validate_convergence_route_history(corrupted, workflow, qualification)


def test_threshold_finding_resolved_in_evaluator_hop_does_not_escape():
    workflow = convergence_routing_profile(qualified_cycle_limit=2)
    _, run = tracked_convergence_run()
    first, _ = _reduce_qualified_slice3_evaluator(
        run, workflow, evidence_prefix="first"
    )

    closed, _ = _reduce_qualified_slice3_evaluator(
        first.run,
        workflow,
        evidence_prefix="closed",
        assessment_result="closed",
    )
    assert closed.route_key == "correct"
    assert closed.run.hops[-1].requested_route == "correct"
    assert closed.run.hops[-1].applied_route == "correct"
    assert closed.run.hops[-1].route_reason is None
    assert closed.run.findings[0].state is FindingState.RESOLVED


def test_missing_current_evaluator_qualification_preempts_before_mutation():
    workflow = convergence_routing_profile()
    _, run = tracked_convergence_run()
    run, correction_hop = charge_hop(run)
    run = reduce_result(
        run,
        result(
            "evaluate",
            schema_version=2,
            coverage_claims=(coverage("F001", "addressed", "coverage"),),
        ),
        workflow,
        hop_id=correction_hop,
    ).run
    run, evaluator_hop = charge_hop(
        run, role="independent_reviewer", outcome="changes_required"
    )
    before = run.findings
    rejected = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(existing_delta("F001", "open", evidence="new-counterexample"),),
            convergence_assessments=(
                assessment(
                    "F001",
                    correction_hop,
                    "carried_new_counterexample",
                    "new-counterexample",
                ),
            ),
        ),
        workflow,
        hop_id=evaluator_hop,
    )
    assert "lacks trusted qualification input" in (rejected.run.stop_reason or "")
    assert rejected.run.findings == before
    assert rejected.run.hops[-1].finding_delta_ids == ()
    assert rejected.run.hops[-1].applied_route is None


def test_inconsistent_qualification_identity_preempts_before_finding_transaction():
    workflow = convergence_routing_profile()
    _, run = tracked_convergence_run()
    run, correction_hop = charge_hop(run)
    run = reduce_result(
        run,
        result(
            "evaluate",
            schema_version=2,
            coverage_claims=(coverage("F001", "addressed", "coverage"),),
        ),
        workflow,
        hop_id=correction_hop,
    ).run
    run, evaluator_hop = charge_hop(
        run, role="independent_reviewer", outcome="changes_required"
    )
    run, qualification, _ = qualification_evidence(
        run, {evaluator_hop: ("pass",)}
    )
    assert qualification.run is not None
    inconsistent = replace(
        qualification,
        run=replace(qualification.run, run_id="RUN-other"),
    )
    before = run.findings
    rejected = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(existing_delta("F001", "open", evidence="counterexample"),),
            convergence_assessments=(
                assessment(
                    "F001",
                    correction_hop,
                    "carried_new_counterexample",
                    "counterexample",
                ),
            ),
        ),
        workflow,
        hop_id=evaluator_hop,
        trusted_qualification_index=inconsistent,
    )
    assert "qualification index identity mismatch" in (rejected.run.stop_reason or "")
    assert rejected.run.findings == before
    assert rejected.run.hops[-1].finding_delta_ids == ()


def test_escape_selection_still_yields_to_hop_budget_exhaustion():
    workflow = convergence_routing_profile(qualified_cycle_limit=2)
    _, run = tracked_convergence_run()
    first, _ = _reduce_qualified_slice3_evaluator(
        run, workflow, evidence_prefix="first"
    )
    at_last_cycle = replace(first.run, hop_limit=first.run.hop_used + 2)

    exhausted, _ = _reduce_qualified_slice3_evaluator(
        at_last_cycle, workflow, evidence_prefix="second"
    )
    assert exhausted.route_key is None
    assert exhausted.run.status is RunStatus.AWAITING_HUMAN_BUDGET
    assert exhausted.run.stop_reason == "autonomous_hop_budget_exhausted"
    assert exhausted.run.hops[-1].requested_route == "correct"
    assert exhausted.run.hops[-1].applied_route is None
    assert exhausted.run.hops[-1].route_reason == "convergence_escape:F001"
    assert run_from_json(run_to_json(exhausted.run)) == exhausted.run
    qualifying_hops = {
        hop.hop_id: ("pass",)
        for hop in exhausted.run.hops
        if hop.convergence_assessments
    }
    anchored, qualification, _ = qualification_evidence(
        exhausted.run, qualifying_hops
    )
    validate_convergence_route_history(anchored, workflow, qualification)


@pytest.mark.parametrize(
    "facts",
    [
        MechanicalFacts(state_error="state"),
        MechanicalFacts(repository_anomaly="repository"),
        MechanicalFacts(policy_anomaly="policy"),
        MechanicalFacts(required_evidence_error="evidence"),
    ],
)
def test_mechanical_preemptions_remain_stronger_than_escape(facts):
    workflow = convergence_routing_profile(qualified_cycle_limit=2)
    _, run = tracked_convergence_run()
    first, _ = _reduce_qualified_slice3_evaluator(
        run, workflow, evidence_prefix="first"
    )
    preempted, _ = _reduce_qualified_slice3_evaluator(
        first.run,
        workflow,
        evidence_prefix="second",
        facts=facts,
    )
    assert preempted.route_key is None
    assert preempted.run.hops[-1].applied_route is None
    assert preempted.run.hops[-1].route_reason is None
    assert (preempted.run.stop_reason or "").startswith("mechanical_preemption:")


def test_required_evidence_failure_route_precedes_escape_substitution():
    workflow = convergence_routing_profile(qualified_cycle_limit=2)
    evaluator = workflow.phases["evaluate"]
    assert evaluator.review is not None
    workflow = replace(
        workflow,
        phases={
            **workflow.phases,
            "evaluate": replace(
                evaluator,
                review=replace(evaluator.review, failed_evidence_route="correct"),
            ),
        },
    )
    _, run = tracked_convergence_run()
    first, _ = _reduce_qualified_slice3_evaluator(
        run, workflow, evidence_prefix="first"
    )
    failed, _ = _reduce_qualified_slice3_evaluator(
        first.run,
        workflow,
        evidence_prefix="second",
        facts=MechanicalFacts(required_evidence_failed=True),
        current_evidence_status="fail",
    )
    assert failed.route_key == "correct"
    assert failed.run.hops[-1].requested_route == "correct"
    assert failed.run.hops[-1].applied_route == "correct"
    assert failed.run.hops[-1].route_reason is None


def test_new_blocker_starts_at_zero_and_does_not_inherit_triggering_identity():
    workflow = convergence_routing_profile(qualified_cycle_limit=2)
    _, run = tracked_convergence_run()
    first, _ = _reduce_qualified_slice3_evaluator(
        run, workflow, evidence_prefix="first"
    )
    threshold, second_evaluator = _reduce_qualified_slice3_evaluator(
        first.run,
        workflow,
        evidence_prefix="second",
        new_findings=(
            new_delta(
                "open",
                invariant="A distinct newly discovered blocker remains open.",
                evidence="new-blocker",
            ),
        ),
    )
    assert threshold.run.hops[-1].route_reason == "convergence_escape:F001"
    anchored, qualification, _ = qualification_evidence(
        threshold.run,
        {
            hop.hop_id: ("pass",)
            for hop in threshold.run.hops
            if hop.convergence_assessments
        },
    )
    facts = derive_convergence_facts(anchored, qualification, 2)
    assert [(fact.finding_id, fact.qualified_cycle_count) for fact in facts] == [
        ("F001", 2),
        ("F002", 0),
    ]
    assert second_evaluator in facts[0].source_evaluator_hops


def test_multiple_goalpost_findings_produce_sorted_trigger_ids():
    old_workflow, run = tracked_convergence_run()
    run = replace(run, phase="evaluate")
    run, hop_id = charge_hop(run, role="independent_reviewer")
    run = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(
                new_delta(
                    "open",
                    invariant="A second independent obligation remains open.",
                    evidence="second-obligation",
                ),
            ),
        ),
        old_workflow,
        hop_id=hop_id,
    ).run
    workflow = convergence_routing_profile(qualified_cycle_limit=2)
    first, _ = _reduce_qualified_slice3_evaluator(
        run, workflow, evidence_prefix="first"
    )
    threshold, _ = _reduce_qualified_slice3_evaluator(
        first.run, workflow, evidence_prefix="second"
    )
    assert threshold.run.hops[-1].route_reason == "convergence_escape:F001,F002"


def _mark_strategy_escape(run, escape_id, trigger_ids):
    return replace(
        run,
        phase="diagnose",
        hops=tuple(
            replace(
                hop,
                outcome="changes_required",
                requested_route="correct",
                applied_route="diagnose",
                route_reason=f"convergence_escape:{','.join(trigger_ids)}",
            ) if hop.hop_id == escape_id else hop
            for hop in run.hops
        ),
    )


def _reduce_strategy_diagnosis(run, workflow, escape_id, trigger_ids):
    diagnosis_hop, diagnosis_id = charge_hop(run)
    run = reduce_result(
        diagnosis_hop,
        result("reframe", schema_version=2, diagnosis=DiagnosisReport(
            escape_id, trigger_ids, DiagnosisClassification.REGRESSION_CHAIN,
            "The qualified cycle needs a reframe.", ("diagnosis",),
        )), workflow, hop_id=diagnosis_id,
    ).run
    return run, diagnosis_id


def _reduce_strategy_revision(run, workflow, escape_id, diagnosis_id, trigger_ids):
    revision_hop, revision_id = charge_hop(run)
    run = reduce_result(
        revision_hop,
        result("strategy", schema_version=2, strategy_revision=StrategyRevision(
            escape_id, diagnosis_id, trigger_ids,
            StrategyRevisionKind.ADDRESS_REGRESSION_CHAIN,
            "Change the strategy.", ("revision",),
        )), workflow, hop_id=revision_id,
    ).run
    return run, revision_id


def _reduce_strategy_assessment(
    run, workflow, escape_id, diagnosis_id, revision_id, trigger_ids, result_value
):
    assessment_hop, assessment_id = charge_hop(
        run,
        role="independent_reviewer",
        outcome="changes_required" if result_value == "changes_required" else "completed",
    )
    run = reduce_result(
        assessment_hop,
        result("reframe" if result_value == "changes_required" else "correct",
               outcome=("changes_required" if result_value == "changes_required" else "completed"),
               schema_version=2, strategy_assessment=StrategyAssessment(
            escape_id, diagnosis_id, revision_id, trigger_ids,
            StrategyAssessmentResult(result_value), ("assessment",),
        )), workflow, hop_id=assessment_id,
    ).run
    return run, assessment_id


def _all_qualified_evidence(run):
    return qualification_evidence(
        run,
        {
            hop.hop_id: ("pass",)
            for hop in run.hops
            if hop.convergence_assessments
        },
    )


def test_live_strategy_chain_changes_required_then_latest_revision_advances_multi_trigger_generation():
    history_workflow, run = tracked_convergence_run(budget=24)
    run = replace(run, phase="evaluate")
    introduced, introduced_id = charge_hop(run, role="independent_reviewer")
    run = reduce_result(
        introduced,
        result("correct", outcome="changes_required", schema_version=2,
               findings=(new_delta("open", invariant="A second tracked blocker."),)),
        history_workflow, hop_id=introduced_id,
    ).run
    run, _, first = convergence_cycle(run, history_workflow, evidence_prefix="first")
    run, _, escape_id = convergence_cycle(run, history_workflow, evidence_prefix="escape")
    workflow = convergence_routing_profile()
    triggers = ("F001", "F002")
    run = _mark_strategy_escape(run, escape_id, triggers)
    run, diagnosis_id = _reduce_strategy_diagnosis(run, workflow, escape_id, triggers)
    run, rejected_revision_id = _reduce_strategy_revision(
        run, workflow, escape_id, diagnosis_id, triggers
    )
    run, rejected_assessment_id = _reduce_strategy_assessment(
        run, workflow, escape_id, diagnosis_id, rejected_revision_id, triggers,
        "changes_required",
    )

    assert run.phase == "reframe"
    assert run.hops[-1].applied_route == "reframe"
    run, latest_revision_id = _reduce_strategy_revision(
        run, workflow, escape_id, diagnosis_id, triggers
    )
    stale_assessment, stale_assessment_id = charge_hop(run, role="independent_reviewer")
    stale = reduce_result(
        stale_assessment,
        result("correct", schema_version=2, strategy_assessment=StrategyAssessment(
            escape_id, diagnosis_id, rejected_revision_id, triggers,
            StrategyAssessmentResult.ACCEPTED, ("stale",),
        )), workflow, hop_id=stale_assessment_id,
    )
    assert stale.run.stop_reason == "mechanical_preemption:invalid_finding_delta:strategy assessment must reference the latest revision"
    run, accepted_assessment_id = _reduce_strategy_assessment(
        run, workflow, escape_id, diagnosis_id, latest_revision_id, triggers, "accepted"
    )
    run, qualification, evidence_json = _all_qualified_evidence(run)

    facts = derive_convergence_facts(run, qualification, 2)
    assert [(fact.finding_id, fact.strategy_generation, fact.qualified_cycle_count)
            for fact in facts] == [("F001", 1, 0), ("F002", 1, 0)]
    assert run.hops[-1].phase == "strategy"
    assert run.hops[-1].role == "independent_reviewer"
    assert run.hops[-1].applied_route == "correct"
    assert rejected_assessment_id != accepted_assessment_id
    assert run.hops[-1].strategy_assessment is not None

    replayed = run_from_json(run_to_json(run))
    validate_convergence_route_history(replayed, workflow, qualification)
    assert derive_convergence_facts(replayed, qualification, 2) == facts

    checkpoint = checkpoint_projection(
        run, workflow, qualified_cycle_limit=2, execution_evidence_json=evidence_json,
    )
    context = convergence_prompt_context(run, workflow, qualification)
    assert context is not None
    prompt_projection = json.loads(context.content.split("\n", 1)[1])
    escape = checkpoint["convergence"]["escape_routes"][-1]
    assert escape["state"] == "consumed"
    assert escape["trigger_generations"] == {"F001": 0, "F002": 0}
    assert [fact["strategy_generation"] for fact in checkpoint["convergence"]["facts"]] == [1, 1]
    assert prompt_projection["escape_routes"][-1]["state"] == "consumed"
    assert [fact["strategy_generation"] for fact in prompt_projection["convergence_facts"]] == [1, 1]
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"), text=True, capture_output=True, check=True
    ).stdout.strip()
    operator_run = replace(
        run,
        baseline_head=head,
        current_head=head,
        hops=tuple(
            replace(hop, base_head=head, end_head=head, actual_commits=())
            for hop in run.hops
        ),
    )
    assert operator._summary(
        Path.cwd(), operator_run, None, convergence_projection=checkpoint["convergence"]
    )["convergence"] == checkpoint["convergence"]


def test_live_strategy_chain_reuses_the_mapping_across_generations_without_touching_non_triggers():
    history_workflow, run = tracked_convergence_run(budget=32)
    run = replace(run, phase="evaluate")
    introduced, introduced_id = charge_hop(run, role="independent_reviewer")
    run = reduce_result(
        introduced,
        result("correct", outcome="changes_required", schema_version=2,
               findings=(new_delta("open", invariant="A non-triggering blocker."),)),
        history_workflow, hop_id=introduced_id,
    ).run
    run, _, first = convergence_cycle(run, history_workflow, evidence_prefix="first")
    run, _, escape_id = convergence_cycle(run, history_workflow, evidence_prefix="escape")
    workflow = convergence_routing_profile()
    run = _mark_strategy_escape(run, escape_id, ("F001",))
    run, diagnosis_id = _reduce_strategy_diagnosis(run, workflow, escape_id, ("F001",))
    run, revision_id = _reduce_strategy_revision(run, workflow, escape_id, diagnosis_id, ("F001",))
    run, _ = _reduce_strategy_assessment(run, workflow, escape_id, diagnosis_id, revision_id, ("F001",), "accepted")

    run, _, second_first = convergence_cycle(run, history_workflow, evidence_prefix="second-first")
    run, _, second_escape = convergence_cycle(run, history_workflow, evidence_prefix="second-escape")
    run = _mark_strategy_escape(run, second_escape, ("F001",))
    run, second_diagnosis_id = _reduce_strategy_diagnosis(run, workflow, second_escape, ("F001",))
    run, second_revision_id = _reduce_strategy_revision(run, workflow, second_escape, second_diagnosis_id, ("F001",))
    run, _ = _reduce_strategy_assessment(run, workflow, second_escape, second_diagnosis_id, second_revision_id, ("F001",), "accepted")
    run, qualification, _ = _all_qualified_evidence(run)

    facts = {fact.finding_id: fact for fact in derive_convergence_facts(run, qualification, 2)}
    assert facts["F001"].strategy_generation == 2
    assert facts["F001"].qualified_cycle_count == 0
    assert facts["F002"].strategy_generation == 0
    assert facts["F002"].qualified_cycle_count == 4
    assert first != second_first


def test_replay_rejects_forged_strategy_authority_and_unapplied_or_inactive_triggers():
    history_workflow, run = tracked_convergence_run(budget=12)
    run, _, _ = convergence_cycle(run, history_workflow, evidence_prefix="first")
    run, _, escape_id = convergence_cycle(run, history_workflow, evidence_prefix="escape")
    workflow = convergence_routing_profile()
    run = _mark_strategy_escape(run, escape_id, ("F001",))
    run, diagnosis_id = _reduce_strategy_diagnosis(run, workflow, escape_id, ("F001",))
    run, revision_id = _reduce_strategy_revision(run, workflow, escape_id, diagnosis_id, ("F001",))
    run, _ = _reduce_strategy_assessment(run, workflow, escape_id, diagnosis_id, revision_id, ("F001",), "accepted")
    run, qualification, _ = _all_qualified_evidence(run)
    replayed = run_from_json(run_to_json(run))

    forged = replace(
        replayed,
        hops=tuple(replace(hop, phase="correct") if hop.hop_id == diagnosis_id else hop for hop in replayed.hops),
    )
    with pytest.raises(ReductionError, match="phase/role authority"):
        validate_convergence_route_history(forged, workflow, qualification)
    for stop_reason in ("supervisor_judgment_required", "autonomous_hop_budget_exhausted"):
        unapplied = replace(
            replayed,
            hops=tuple(replace(hop, applied_route=None, stop_reason=stop_reason)
                       if hop.hop_id == escape_id else hop for hop in replayed.hops),
        )
        with pytest.raises(ReductionError, match="unapplied escape"):
            validate_convergence_route_history(unapplied, workflow, qualification)
    inactive = replace(
        replayed,
        findings=tuple(
            replace(finding, state=FindingState.RESIDUAL, blocking=False, changed_by_hop=escape_id)
            if finding.finding_id == "F001" else finding for finding in replayed.findings
        ),
    )
    with pytest.raises(ReductionError, match="non-open, non-blocking, or untracked"):
        validate_convergence_route_history(inactive, workflow, qualification)


def test_profile_v3_none_role_uses_result_v2_and_keeps_findings_informational():
    workflow = convergence_routing_profile()
    workflow = replace(
        workflow,
        phases={
            **workflow.phases,
            "observe": phase({"halt": route("stop")}),
        },
    )
    run, hop_id = charge_hop(
        run_for(workflow, initial_phase="observe", run_schema_version=3)
    )
    accepted = reduce_result(
        run,
        result(
            "halt",
            schema_version=2,
            findings=(
                new_delta(
                    "open",
                    blocking=False,
                    invariant="An informational observation remains recorded.",
                ),
            ),
        ),
        workflow,
        hop_id=hop_id,
    )
    assert accepted.run.status is RunStatus.STOPPED
    assert accepted.run.findings[0].blocking is False
    assert accepted.run.findings[0].correction_obligation is None

    legacy_contract = reduce_result(
        run,
        result("halt", schema_version=1),
        workflow,
        hop_id=hop_id,
    )
    assert "unsupported result schema version" in (
        legacy_contract.run.stop_reason or ""
    )
