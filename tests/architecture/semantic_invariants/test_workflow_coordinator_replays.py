"""Executable architecture acceptance replays A--J from the accepted FTR."""

from dataclasses import fields
from pathlib import Path

import pytest

from scripts.workflow_coordinator.model import (
    Escalation,
    FindingAuthority,
    FindingState,
    Run,
    RunStatus,
)
from scripts.workflow_coordinator.reducer import (
    apply_finding_deltas,
    authorize_prerequisite,
    confirm_supervisor_route,
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


def process(run, workflow, routing_result, *, role="worker", attribution="agent"):
    charged, hop_id = charge_hop(run, role=role, attribution=attribution)
    return reduce_result(charged, routing_result, workflow, hop_id=hop_id)


def test_replay_a_productive_correction_can_continue_beyond_three_loops():
    """A: WFC-REVIEW-05, WFC-FIND-04..06, WFC-AUTO-01/04."""

    workflow = profile(
        {
            "delta_review": phase(
                {
                    "productive": route("goto", "correction", supervisor=True),
                    "stop": route("stop", supervisor=True),
                },
                role="delta reviewer",
                review="gate",
            ),
            "correction": phase({"rereview": route("goto", "delta_review")}),
        }
    )
    run = run_for(workflow, budget=13)
    run = apply_finding_deltas(
        run,
        (delta(None, "open", invariant="F1 remains unresolved."),),
        hop_id="fixture",
        authority=FindingAuthority.AGENT,
    )
    run = apply_finding_deltas(
        run,
        (delta(None, "resolved", invariant="F2 stays closed."),),
        hop_id="fixture",
        authority=FindingAuthority.HUMAN,
    )

    for loop in range(4):
        pending = process(
            run,
            workflow,
            result(
                "productive",
                findings=(
                    delta(
                        "F001",
                        "open",
                        invariant="F1 remains unresolved.",
                        evidence=f"mechanism-{loop}",
                    ),
                ),
            ),
            role="delta reviewer",
        )
        assert pending.supervisor_required is True
        supervised, _ = charge_hop(
            pending.run,
            role="routing supervisor",
            attribution="supervisor",
        )
        run = confirm_supervisor_route(supervised, workflow, "productive").run
        run = process(run, workflow, result("rereview"), role="corrector").run

    by_id = {finding.finding_id: finding for finding in run.findings}
    assert run.phase == "delta_review"
    assert run.hop_used == 12
    assert run.hop_remaining == 1
    assert by_id["F001"].state is FindingState.OPEN
    assert by_id["F002"].state is FindingState.RESOLVED
    assert len(by_id["F001"].evidence_refs) == 5


def test_replay_b_semantic_prerequisite_suspends_then_reconciles():
    """B: WFC-AUTH-05/06, WFC-FIND-05/06, WFC-FAIL-06/07."""

    workflow = profile(
        {
            "correction": phase(
                {"child": route("suspend_for_prerequisite"), "retry": route("goto", "correction")}
            )
        }
    )
    run = run_for(workflow, budget=3)
    stopped = process(
        run,
        workflow,
        result(
            "child",
            outcome="completed",
            requires_human=True,
            escalation=Escalation("prerequisite", "Which contract owner must decide?"),
        ),
    ).run
    assert stopped.status is RunStatus.AWAITING_HUMAN
    assert stopped.child_run_id is None

    suspended = authorize_prerequisite(stopped, child_run_id="CHILD-1")
    returned = require_reconcile(
        suspended, reason="prerequisite_return", current_head="child-result-head"
    )
    interruption = require_reconcile(
        run, reason="interruption", current_head="child-result-head"
    )
    assert returned.status == interruption.status == RunStatus.RECONCILE_REQUIRED
    assert returned.stop_reason == interruption.stop_reason
    assert returned.human_question == interruption.human_question
    assert returned.reconcile_reason == "prerequisite_return"
    assert (
        resume_after_reconcile(returned, workflow, current_head="child-result-head").phase
        == "correction"
    )
    assert "next_route" not in {field.name for field in fields(Run)}


def test_replay_c_broad_fresh_advisory_review_cannot_accept():
    """C: WFC-REVIEW-01..04, WFC-AUTH-03..05."""

    workflow = profile(
        {
            "risk_review": phase(
                {
                    "return": route("goto", "formal_gate"),
                    "accept": route("complete"),
                },
                role="independent reviewer",
                review="advisory",
                breadth="broad_adversarial",
            ),
            "formal_gate": phase(
                {"accept": route("complete")},
                role="formal reviewer",
                review="gate",
                finding_authority=("resolve",),
            ),
        }
    )
    run = run_for(workflow, budget=2)
    descriptor = workflow.phases["risk_review"].review
    assert descriptor and descriptor.breadth == "broad_adversarial"
    assert descriptor.context == "fresh"
    returned = process(
        run,
        workflow,
        result("return", findings=(delta(None, "open", invariant="Probe finding."),)),
        role="independent reviewer",
    )
    assert returned.run.phase == "formal_gate"
    assert returned.run.status is RunStatus.READY

    second_run = run_for(workflow, budget=2)
    rejected = process(second_run, workflow, result("accept"), role="independent reviewer")
    assert rejected.run.status is RunStatus.AWAITING_HUMAN
    assert rejected.run.stop_reason == "advisory_review_cannot_accept_or_complete"


def test_replay_d_empty_known_findings_does_not_skip_closure_search():
    """D: WFC-REVIEW-04, WFC-FIND-01/03/06."""

    workflow = profile(
        {
            "counterexample_search": phase(
                {"correct": route("goto", "correction"), "accept": route("complete")},
                review="gate",
                finding_authority=("resolve",),
                breadth="counterexample",
            ),
            "correction": phase({"search": route("goto", "counterexample_search")}),
        }
    )
    run = run_for(workflow, budget=2)
    assert run.findings == ()
    discovered = process(
        run,
        workflow,
        result("correct", findings=(delta(None, "open", invariant="A shadow path remains."),)),
        role="closure reviewer",
    ).run
    assert discovered.phase == "correction"
    assert discovered.findings[0].state is FindingState.OPEN
    assert discovered.findings[0].blocking is True


def test_replay_e_deletion_authority_child_is_human_governed_not_run_memory():
    """E: WFC-AUTH-06, WFC-FAIL-06/07, WFC-PM-01."""

    workflow = profile(
        {
            "work": phase(
                {"prerequisite": route("suspend_for_prerequisite"), "done": route("complete")}
            )
        }
    )
    stopped = process(run_for(workflow, budget=2), workflow, result("prerequisite")).run
    assert stopped.status is RunStatus.AWAITING_HUMAN
    assert stopped.child_run_id is None
    parent = authorize_prerequisite(stopped, child_run_id="AUTHORIZED-CHILD")
    assert parent.status is RunStatus.SUSPENDED
    returned = require_reconcile(parent, reason="prerequisite_return", current_head="new-head")
    assert returned.status is RunStatus.RECONCILE_REQUIRED
    assert "project_memory" not in {field.name for field in fields(Run)}


def test_replay_f_tiny_change_completes_in_one_hop_without_ceremony():
    """F: WFC-SYS-04, WFC-REVIEW-04, WFC-AUTO-01, WFC-PM-02."""

    workflow = profile({"implement": phase({"done": route("complete")})})
    completed = process(run_for(workflow, budget=1), workflow, result("done")).run
    assert completed.status is RunStatus.COMPLETED
    assert completed.hop_used == 1
    assert tuple(workflow.phases) == ("implement",)
    assert "project_memory" not in {field.name for field in fields(Run)}


def test_replay_g_declared_gate_accepts_residual_without_correction():
    """G: WFC-FIND-01/03, WFC-REVIEW-02."""

    workflow = profile(
        {
            "gate": phase(
                {"accept": route("complete")},
                review="gate",
                finding_authority=("residual",),
            )
        }
    )
    completed = process(
        run_for(workflow, budget=1),
        workflow,
        result(
            "accept",
            findings=(delta(None, "residual", invariant="Minor remains.", blocking=False),),
        ),
        role="gate reviewer",
    ).run
    assert completed.status is RunStatus.COMPLETED
    assert completed.findings[0].state is FindingState.RESIDUAL
    assert completed.findings[0].disposition.startswith("gate:")
    assert "correction" not in workflow.phases


def test_replay_h_one_hop_still_processes_state_then_stops_before_second():
    """H: WFC-AUTO-01..05, WFC-OBS-01..04."""

    workflow = profile({"work": phase({"again": route("goto", "work")})})
    stopped = process(
        run_for(workflow, budget=1),
        workflow,
        result("again", findings=(delta(None, "open"),)),
    ).run
    assert stopped.hop_used == 1
    assert stopped.findings[0].finding_id == "F001"
    assert stopped.status is RunStatus.AWAITING_HUMAN_BUDGET
    with pytest.raises(ValueError, match="prohibits invocation"):
        charge_hop(stopped)


@pytest.mark.parametrize("effect,target", [("complete", None), ("goto", "next")])
def test_replay_i_completed_result_with_escalation_is_mechanically_preempted(effect, target):
    """I: R1 regression -- completed plus valid route must still stop."""

    phases = {"work": phase({"requested": route(effect, target, supervisor=True)})}
    if target:
        phases[target] = phase({"done": route("complete")})
    workflow = profile(phases)
    dangerous = result(
        "requested",
        outcome="completed",
        requires_human=True,
        escalation=Escalation("semantic_authority", "Who may accept this contract?"),
    )
    reduced = process(run_for(workflow, budget=3), workflow, dangerous)
    assert reduced.run.status is RunStatus.AWAITING_HUMAN
    assert reduced.run.phase == "work"
    assert reduced.route_key is None
    assert reduced.autonomous_route is False
    assert reduced.supervisor_required is False
    assert reduced.run.hop_used == 1


def test_replay_j_non_domain_specific_complex_profile_converges_generically():
    """J: WFC-SYS-03 and WFC-PROFILE/REVIEW/FIND genericity falsification."""

    workflow = profile(
        {
            "bug_investigation": phase({"fix": route("goto", "fix")}),
            "fix": phase({"review": route("goto", "independent_review")}),
            "independent_review": phase(
                {"correct": route("goto", "correction"), "accept": route("complete")},
                role="independent reviewer",
                review="gate",
                finding_authority=("resolve",),
            ),
            "correction": phase({"rereview": route("goto", "rereview")}),
            "rereview": phase(
                {"accept": route("complete"), "correct": route("goto", "correction")},
                role="independent reviewer",
                review="gate",
                finding_authority=("resolve", "reopen"),
            ),
        },
        profile_id="bug_fixture",
    )
    run = run_for(workflow, budget=5)
    run = process(
        run,
        workflow,
        result("fix", findings=(delta(None, "open", invariant="Bug reproduces."),)),
        role="investigator",
    ).run
    run = process(run, workflow, result("review"), role="implementer").run
    run = process(
        run,
        workflow,
        result(
            "correct",
            outcome="changes_required",
            findings=(delta("F001", "open", invariant="Bug reproduces.", evidence="review"),),
        ),
        role="independent reviewer",
    ).run
    run = process(
        run,
        workflow,
        result(
            "rereview",
            findings=(delta("F001", "open", invariant="Bug reproduces.", evidence="correction"),),
        ),
        role="corrector",
    ).run
    run = process(
        run,
        workflow,
        result(
            "accept",
            findings=(delta("F001", "resolved", invariant="Bug reproduces.", evidence="rereview"),),
        ),
        role="independent reviewer",
    ).run

    assert run.status is RunStatus.COMPLETED
    assert run.hop_used == 5
    assert len(run.findings) == 1
    assert run.findings[0].finding_id == "F001"
    assert run.findings[0].state is FindingState.RESOLVED
    generic_source = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in Path("scripts/workflow_coordinator").glob("*.py")
    )
    assert "dmc" not in generic_source
    assert "trusted_ingress" not in generic_source
