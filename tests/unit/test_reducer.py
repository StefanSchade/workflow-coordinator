from dataclasses import fields, replace
import inspect

import pytest

from workflow_coordinator.model import (
    ConvergenceAssessmentResult,
    ConvergenceRole,
    CorrectionObligation,
    CorrectionObligationOwner,
    CoverageDisposition,
    Escalation,
    ExistingFindingDelta,
    FindingAuthority,
    FindingState,
    MechanicalFacts,
    Run,
    RunStatus,
    WorkflowRole,
)
from workflow_coordinator.reducer import (
    ReductionError,
    apply_finding_deltas,
    authorize_prerequisite,
    confirm_supervisor_route,
    permitted_route_keys,
    reduce_result,
    require_reconcile,
    resume_after_reconcile,
)
from tests.support import (
    assessment,
    charge_hop,
    coverage,
    existing_delta,
    new_delta,
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
        ((existing_delta("F001", "superseded", successor_ref="F001"),), "cycle"),
    ],
)
def test_local_reference_failures_are_atomic_and_consume_no_id(deltas, message):
    _, run = introduce_obligation()
    with pytest.raises(ReductionError, match=message):
        apply_finding_deltas(
            run, deltas, hop_id="H002", authority=FindingAuthority.GATE,
            permitted_gate_operations=("supersede",),
            convergence_role=ConvergenceRole.EVALUATOR, result_schema_version=2,
        )
    accepted = apply_finding_deltas(
        run, (new_delta("open", local_ref="N9"),), hop_id="H002",
        authority=FindingAuthority.GATE,
        convergence_role=ConvergenceRole.EVALUATOR, result_schema_version=2,
    )
    assert accepted.findings[-1].finding_id == "F002"


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
    names = {field.name for field in fields(CorrectionObligation)}
    values = {
        item.value
        for enum_type in (ConvergenceRole, CoverageDisposition, ConvergenceAssessmentResult)
        for item in enum_type
    }
    text = " ".join(sorted(names | values)).lower()
    for forbidden in ("refactor", "python test", "cyclomatic complexity"):
        assert forbidden not in text
