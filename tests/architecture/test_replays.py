"""Executable architecture acceptance replays A--J from the accepted FTR."""

from dataclasses import fields, replace
import json
from pathlib import Path

import pytest
import yaml

import workflow_coordinator as workflow_coordinator
from workflow_coordinator.model import (
    DispatchMarker,
    Escalation,
    FindingAuthority,
    FindingState,
    Run,
    RunStatus,
    TrustedQualificationIndex,
)
from workflow_coordinator.reducer import (
    apply_finding_deltas,
    authorize_prerequisite,
    confirm_supervisor_route,
    derive_convergence_facts,
    reduce_result,
    require_reconcile,
    resume_after_reconcile,
)
from workflow_coordinator.persistence import (
    checkpoint_projection,
    recover_uncertain_dispatch,
)
from workflow_coordinator.serialization import (
    run_from_json,
    run_to_json,
    work_item_from_yaml,
    workflow_profile_from_yaml,
)
from tests.support import (
    charge_hop,
    convergence_cycle,
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

TESTS_ROOT = Path(__file__).resolve().parents[1]
NEUTRAL_FIXTURE = TESTS_ROOT / "fixtures" / "slice5b_non_dmc.yaml"
PRE_SLICE1_V2_RUN_JSON = '{"anomalies":[],"baseline_head":"abc","child_run_id":null,"current_head":"abc","findings":[],"hop_limit":1,"hop_used":0,"hops":[],"human_question":null,"phase":"implement","policy_id":"core-policy","policy_revision":"policy-r1","profile_id":"core","profile_revision":"profile-r1","reconcile_anchor":null,"reconcile_reason":null,"repository":"repo","run_id":"RUN-v2","schema_version":2,"semantic_stop":null,"status":"ready","stop_reason":null,"successor_provenance":null,"suspension_head":null,"work_item_id":"WI-1"}\n'


def process(run, workflow, routing_result, *, role="worker", attribution="agent"):
    charged, hop_id = charge_hop(run, role=role, attribution=attribution)
    return reduce_result(charged, routing_result, workflow, hop_id=hop_id)


def test_pre_slice1_v2_replay_is_byte_preserving_and_fabricates_no_v3_records():
    run = run_from_json(PRE_SLICE1_V2_RUN_JSON)
    workflow = profile(
        {"implement": phase({"done": route("complete")})}, profile_id="core"
    )
    assert run_to_json(run) == PRE_SLICE1_V2_RUN_JSON
    assert run.schema_version == 2
    assert all(finding.correction_obligation is None for finding in run.findings)
    assert all(
        not hop.coverage_claims and not hop.convergence_assessments
        for hop in run.hops
    )
    assert derive_convergence_facts(
        run, TrustedQualificationIndex.empty(), 2
    ) == ()
    assert checkpoint_projection(
        run, workflow, qualified_cycle_limit=2
    )["convergence_facts"] == []
    assert process(run, workflow, result("done")).run.status is RunStatus.COMPLETED


def test_slice2_convergence_projection_is_replay_deterministic_and_ignores_uncertain_hops():
    workflow, run = tracked_convergence_run()
    run, _, first_evaluator_hop = convergence_cycle(
        run, workflow, evidence_prefix="first-replay"
    )
    run, _, second_evaluator_hop = convergence_cycle(
        run, workflow, evidence_prefix="second-replay"
    )
    run, qualification, evidence_json = qualification_evidence(
        run,
        {
            first_evaluator_hop: ("pass",),
            second_evaluator_hop: ("pass",),
        },
    )
    replayed = run_from_json(run_to_json(run))

    expected = derive_convergence_facts(run, qualification, 2)
    assert derive_convergence_facts(replayed, qualification, 2) == expected
    assert expected[0].source_evaluator_hops == (
        first_evaluator_hop,
        second_evaluator_hop,
    )
    assert json.dumps(
        checkpoint_projection(
            run,
            workflow,
            qualified_cycle_limit=2,
            execution_evidence_json=evidence_json,
        ),
        sort_keys=True,
        separators=(",", ":"),
    ) == json.dumps(
        checkpoint_projection(
            replayed,
            workflow,
            qualified_cycle_limit=2,
            execution_evidence_json=evidence_json,
        ),
        sort_keys=True,
        separators=(",", ":"),
    )

    marker = DispatchMarker(
        schema_version=1,
        run_id=run.run_id,
        hop_id="H006",
        sequence=run.hop_used + 1,
        invocation_id="INV-uncertain",
        base_head=run.current_head,
        budget_reserved=1,
    )
    uncertain = recover_uncertain_dispatch(
        run, marker, current_head=run.current_head
    )
    assert uncertain.hops[-1].outcome == "uncertain"
    assert derive_convergence_facts(uncertain, qualification, 2) == expected


def test_slice3_applied_escape_replay_preserves_facts_sources_and_route_truth():
    workflow, run = tracked_convergence_run()
    run, _, first = convergence_cycle(run, workflow, evidence_prefix="first-escape")
    run, _, second = convergence_cycle(run, workflow, evidence_prefix="second-escape")
    run, qualification, evidence_json = qualification_evidence(
        run, {first: ("pass",), second: ("pass",)}
    )
    run = replace(
        run,
        hops=tuple(
            replace(
                hop,
                requested_route="correct",
                applied_route="diagnose",
                route_reason="convergence_escape:F001",
            )
            if hop.hop_id == second
            else hop
            for hop in run.hops
        ),
    )
    replayed = run_from_json(run_to_json(run))

    assert replayed.hops[-1].requested_route == "correct"
    assert replayed.hops[-1].applied_route == "diagnose"
    assert replayed.hops[-1].route_reason == "convergence_escape:F001"
    assert derive_convergence_facts(replayed, qualification, 2) == derive_convergence_facts(
        run, qualification, 2
    )
    projected = checkpoint_projection(
        replayed,
        workflow,
        qualified_cycle_limit=2,
        execution_evidence_json=evidence_json,
    )
    assert projected["convergence"]["facts"][0]["source_evaluator_hops"] == [
        first,
        second,
    ]
    assert projected["convergence"]["escape_routes"][0][
        "triggering_finding_ids"
    ] == ["F001"]


def test_replay_a_productive_correction_can_continue_beyond_three_loops():
    """A: WFC-REVIEW-05, WFC-FIND-04..06, WFC-AUTO-01/04."""

    workflow = profile(
        {
            "delta_review": phase(
                {
                    "productive": route("goto", "correction", supervisor=True),
                    "stop": route("stop", supervisor=True),
                },
                role="independent_reviewer",
                review="gate",
            ),
            "correction": phase({"rereview": route("goto", "delta_review")}),
        }
    )
    run = run_for(workflow, budget=13)
    run = apply_finding_deltas(
        run,
        (new_delta("open", invariant="F1 remains unresolved."),),
        hop_id="fixture",
        authority=FindingAuthority.AGENT,
    )
    run = apply_finding_deltas(
        run,
        (new_delta("resolved", invariant="F2 stays closed."),),
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
                    existing_delta(
                        "F001",
                        "open",
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


def test_semantic_successor_stop_is_not_eligible_for_same_run_recovery():
    workflow = profile({"work": phase({"done": route("complete")})})
    run, hop_id = charge_hop(run_for(workflow, budget=2))
    stopped = reduce_result(
        run,
        result("done", requires_human=True, escalation=Escalation("scope", "Grant a new scope?")),
        workflow,
        hop_id=hop_id,
    ).run
    assert stopped.status is RunStatus.AWAITING_HUMAN
    with pytest.raises(Exception, match="awaiting_human"):
        require_reconcile(stopped, reason="interruption", current_head=stopped.current_head)


def test_replay_c_broad_fresh_advisory_review_cannot_accept():
    """C: WFC-REVIEW-01..04, WFC-AUTH-03..05."""

    workflow = profile(
        {
            "risk_review": phase(
                {
                    "return": route("goto", "formal_gate"),
                    "accept": route("complete"),
                },
                role="independent_reviewer",
                review="advisory",
                breadth="broad_adversarial",
            ),
            "formal_gate": phase(
                {"accept": route("complete")},
                role="independent_reviewer",
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
        result("return", findings=(new_delta("open", invariant="Probe finding."),)),
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
        result("correct", findings=(new_delta("open", invariant="A shadow path remains."),)),
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
            findings=(new_delta("residual", invariant="Minor remains.", blocking=False),),
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
        result("again", findings=(new_delta("open"),)),
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
                role="independent_reviewer",
                review="gate",
                finding_authority=("resolve",),
            ),
            "correction": phase({"rereview": route("goto", "rereview")}),
            "rereview": phase(
                {"accept": route("complete"), "correct": route("goto", "correction")},
                role="independent_reviewer",
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
        result("fix", findings=(new_delta("open", invariant="Bug reproduces."),)),
        role="investigator",
    ).run
    run = process(run, workflow, result("review"), role="implementer").run
    run = process(
        run,
        workflow,
        result(
            "correct",
            outcome="changes_required",
            findings=(existing_delta("F001", "open", evidence="review"),),
        ),
        role="independent reviewer",
    ).run
    correction = process(
        run,
        workflow,
        result("rereview"),
        role="corrector",
    ).run
    assert correction.findings[0].state is FindingState.OPEN
    assert correction.hops[-1].finding_delta_ids == ()
    run = correction
    run = process(
        run,
        workflow,
        result(
            "accept",
            findings=(existing_delta("F001", "resolved", evidence="rereview"),),
        ),
        role="independent reviewer",
    ).run

    assert run.status is RunStatus.COMPLETED
    assert run.hop_used == 5
    assert len(run.findings) == 1
    assert run.findings[0].finding_id == "F001"
    assert run.findings[0].state is FindingState.RESOLVED
    package_directory = Path(workflow_coordinator.__file__).resolve().parent
    source_files = tuple(sorted(package_directory.glob("*.py")))
    assert source_files
    generic_source = "\n".join(
        path.read_text(encoding="utf-8").lower() for path in source_files
    )
    assert "dmc" not in generic_source
    assert "trusted_ingress" not in generic_source


def test_replay_j_slice5b_declarative_fixture_is_bounded_and_consumer_neutral():
    text = NEUTRAL_FIXTURE.read_text(encoding="utf-8")
    fixture = yaml.safe_load(text)
    item = work_item_from_yaml(yaml.safe_dump(fixture["work_item"], sort_keys=False))
    workflow = workflow_profile_from_yaml(yaml.safe_dump(fixture["profile"], sort_keys=False))

    assert tuple(workflow.phases) == ("investigate", "review", "correct", "rereview")
    assert item.max_autonomous_hops == 4
    assert workflow.phases["review"].review is not None
    assert workflow.phases["rereview"].review is not None
    lowered = text.lower()
    assert "dmc" not in lowered
    assert "spreadsheet" not in lowered
    assert "trusted_ingress" not in lowered
