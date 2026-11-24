"""A and C(iii), WI-WFC-RECOVERY-ROUTING-001: the reducer-level predicate and
classification for a recoverable mechanical stop, and the reducer's
rejection of the contradictory "outcome completed, route = failed_evidence_
route" combination.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from workflow_coordinator.model import (
    Finding,
    FindingState,
    MechanicalFacts,
    RunStatus,
    SemanticStop,
)
from workflow_coordinator.reducer import (
    RECOVERABLE_DURABLE_ARTIFACT_ANOMALIES,
    diagnose_mechanical_recovery_eligibility,
    reduce_result,
    recoverable_mechanical_stop_anomaly,
    resume_after_reconcile,
)
from tests.support import charge_hop, phase, profile, result, route, run_for


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


_ONE_ANOMALY = next(iter(RECOVERABLE_DURABLE_ARTIFACT_ANOMALIES))


# --------------------------------------------------------------------------
# A: a new Run with a recoverable mechanical stop classifies reconcile_
# required, not the terminal awaiting_human -- the stop reason and anomaly
# text are preserved verbatim.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("anomaly", sorted(RECOVERABLE_DURABLE_ARTIFACT_ANOMALIES))
def test_durable_artifact_anomaly_classifies_reconcile_required(anomaly):
    workflow = gate_profile()
    run, hop_id = charge_hop(run_for(workflow))
    reduced = reduce_result(
        run, result("accept"), workflow, hop_id=hop_id,
        facts=MechanicalFacts(repository_anomaly=anomaly),
    )
    assert reduced.run.status is RunStatus.RECONCILE_REQUIRED
    assert reduced.run.reconcile_reason == "mechanical_recoverable"
    assert reduced.run.stop_reason == f"mechanical_preemption:{anomaly}"
    assert anomaly in reduced.run.anomalies
    assert reduced.run.human_question is None
    assert reduced.run.semantic_stop is None


def test_subject_anomaly_class_stays_terminal_awaiting_human():
    """Decision (default, documented in the verification record): the
    WFC-GIT-08 commit-subject anomaly class does NOT belong to the
    recoverable durable-artifact class."""

    workflow = gate_profile()
    run, hop_id = charge_hop(run_for(workflow))
    reduced = reduce_result(
        run, result("accept"), workflow, hop_id=hop_id,
        facts=MechanicalFacts(
            repository_anomaly="WFC-GIT-08 commit subject lacks WorkItem/Hop identity: oops",
        ),
    )
    assert reduced.run.status is RunStatus.AWAITING_HUMAN


def test_an_open_blocking_finding_keeps_the_stop_terminal():
    workflow = gate_profile()
    run, hop_id = charge_hop(run_for(workflow))
    run = replace(run, findings=(
        Finding(
            finding_id="F001", invariant="fixture invariant", blocking=True, state=FindingState.OPEN,
            introduced_by_hop=hop_id, changed_by_hop=hop_id, evidence_refs=(), disposition="open",
        ),
    ))
    reduced = reduce_result(
        run, result("accept"), workflow, hop_id=hop_id,
        facts=MechanicalFacts(repository_anomaly=_ONE_ANOMALY),
    )
    assert reduced.run.status is RunStatus.AWAITING_HUMAN


def test_a_semantic_authority_request_keeps_the_stop_terminal():
    workflow = gate_profile()
    run, hop_id = charge_hop(run_for(workflow))
    reduced = reduce_result(
        run, result("accept", requires_human=True), workflow, hop_id=hop_id,
        facts=MechanicalFacts(repository_anomaly=_ONE_ANOMALY),
    )
    assert reduced.run.status is RunStatus.AWAITING_HUMAN


def test_a_policy_anomaly_alongside_the_repository_anomaly_keeps_the_stop_terminal():
    workflow = gate_profile()
    run, hop_id = charge_hop(run_for(workflow))
    reduced = reduce_result(
        run, result("accept"), workflow, hop_id=hop_id,
        facts=MechanicalFacts(repository_anomaly=_ONE_ANOMALY, policy_anomaly="profile changed"),
    )
    assert reduced.run.status is RunStatus.AWAITING_HUMAN


# --------------------------------------------------------------------------
# A/B's pure predicate functions, exercised directly against a Run.
# --------------------------------------------------------------------------

def test_recoverable_mechanical_stop_anomaly_is_status_agnostic():
    workflow = gate_profile()
    run, hop_id = charge_hop(run_for(workflow))
    reduced = reduce_result(
        run, result("accept"), workflow, hop_id=hop_id,
        facts=MechanicalFacts(repository_anomaly=_ONE_ANOMALY),
    )
    reconcile_required_run = reduced.run
    # The identical predicate must also recognize an already-archived,
    # terminal awaiting_human Run of the exact same shape (B(ii), the real
    # incident) -- status alone never decides eligibility.
    awaiting_human_run = replace(
        reconcile_required_run, status=RunStatus.AWAITING_HUMAN, reconcile_reason=None, reconcile_anchor=None,
    )
    assert recoverable_mechanical_stop_anomaly(reconcile_required_run) == _ONE_ANOMALY
    assert recoverable_mechanical_stop_anomaly(awaiting_human_run) == _ONE_ANOMALY


def test_diagnose_eligibility_scope_gate_vs_named_violation():
    workflow = gate_profile()
    run, hop_id = charge_hop(run_for(workflow))
    ordinary_awaiting_human = replace(run, status=RunStatus.AWAITING_HUMAN, stop_reason="result_blocked")
    anomaly, reason = diagnose_mechanical_recovery_eligibility(ordinary_awaiting_human)
    assert anomaly is None and reason is None  # Scope gate: never in play at all.

    reduced = reduce_result(
        run, result("accept"), workflow, hop_id=hop_id,
        facts=MechanicalFacts(repository_anomaly=_ONE_ANOMALY),
    )
    in_scope_but_violated = replace(
        reduced.run, status=RunStatus.AWAITING_HUMAN, reconcile_reason=None, reconcile_anchor=None,
        semantic_stop=SemanticStop(kind="operational", question="Which way?", source_hop_id=hop_id),
    )
    anomaly, reason = diagnose_mechanical_recovery_eligibility(in_scope_but_violated)
    assert anomaly is None
    assert "semantic stop" in reason


def test_resume_after_reconcile_preserves_mechanical_anomaly_text():
    workflow = gate_profile()
    run, hop_id = charge_hop(run_for(workflow))
    reduced = reduce_result(
        run, result("accept"), workflow, hop_id=hop_id,
        facts=MechanicalFacts(repository_anomaly=_ONE_ANOMALY),
    )
    resumed = resume_after_reconcile(reduced.run, workflow, current_head=reduced.run.current_head)
    assert resumed.status is RunStatus.READY
    assert resumed.stop_reason is None
    assert resumed.human_question is None
    assert _ONE_ANOMALY in resumed.anomalies  # historical provenance, never cleared.
    assert resumed.hop_used == reduced.run.hop_used  # Hop budget and charge unchanged.


# --------------------------------------------------------------------------
# C(iii): outcome=completed + route=failed_evidence_route is contradictory.
# --------------------------------------------------------------------------

def _gate_profile_with_failed_evidence_route():
    workflow = gate_profile()
    review_phase = workflow.phases["review"]
    patched_review = replace(review_phase, review=replace(review_phase.review, failed_evidence_route="correct"))
    return replace(workflow, phases={**workflow.phases, "review": patched_review})


def test_completed_outcome_requesting_the_failed_evidence_route_is_rejected():
    workflow = _gate_profile_with_failed_evidence_route()
    run, hop_id = charge_hop(run_for(workflow))
    reduced = reduce_result(run, result("correct", outcome="completed"), workflow, hop_id=hop_id)
    assert reduced.run.status is RunStatus.AWAITING_HUMAN
    assert "contradictory_route" in (reduced.run.stop_reason or "")
    assert reduced.autonomous_route is False


def test_completed_outcome_requesting_the_normal_accept_route_is_unaffected():
    workflow = _gate_profile_with_failed_evidence_route()
    run, hop_id = charge_hop(run_for(workflow))
    reduced = reduce_result(run, result("accept", outcome="completed"), workflow, hop_id=hop_id)
    assert reduced.run.status is RunStatus.COMPLETED


def test_changes_required_requesting_the_failed_evidence_route_is_unaffected():
    """``correction``/``changes_required`` is its ordinary, legitimate use."""

    workflow = _gate_profile_with_failed_evidence_route()
    run, hop_id = charge_hop(run_for(workflow))
    reduced = reduce_result(run, result("correct", outcome="changes_required"), workflow, hop_id=hop_id)
    assert reduced.run.status is not RunStatus.AWAITING_HUMAN
    assert reduced.route_key == "correct"


def test_required_evidence_failure_legitimately_overrides_the_requested_route():
    """A genuine evidence failure's own forced reroute is not the contradiction."""

    workflow = _gate_profile_with_failed_evidence_route()
    run, hop_id = charge_hop(run_for(workflow))
    reduced = reduce_result(
        run, result("accept", outcome="completed"), workflow, hop_id=hop_id,
        facts=MechanicalFacts(required_evidence_failed=True),
    )
    assert reduced.run.status is not RunStatus.AWAITING_HUMAN
    assert reduced.route_key == "correct"
