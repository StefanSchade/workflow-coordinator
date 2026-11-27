"""O10B, WI-WFC-BETA2-O10B-001: the pure quiescent-interruption predicate.

Every admitted case is built by driving the *production* interruption path
(``persistence.recover_uncertain_dispatch`` -> ``reducer.require_reconcile``)
rather than by hand-assembling a Hop, so the predicate is pinned against the
exact shape the boundary writes -- the behavioural half of the drift guard
the shared constants give statically. Every conjunct is then violated one at
a time and must produce a *named* refusal, and every stop outside the class
must produce the scope-gate answer (``None, None``), which is what keeps
every other class's behaviour unchanged.

The design and the per-class evaluation are
``docs/backlog/FTR-WFC-ATOMIC-HOP-RECOVERY.adoc``'s ``Design`` section.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from workflow_coordinator.model import (
    DispatchMarker,
    Finding,
    FindingState,
    RunStatus,
    SemanticStop,
)
from workflow_coordinator.persistence import recover_uncertain_dispatch
from workflow_coordinator.reducer import (
    EXPLICIT_RESUME_STOP_REASON,
    INTERRUPTION_RECONCILE_QUESTION,
    INTERRUPTION_RECONCILE_REASON,
    UNCERTAIN_DISPATCH_ATTRIBUTION,
    UNCERTAIN_DISPATCH_HOP_STOP_REASON,
    UNCERTAIN_DISPATCH_OUTCOME,
    UNCERTAIN_DISPATCH_ROLE,
    diagnose_quiescent_interruption_recovery_eligibility,
    require_reconcile,
    resume_after_reconcile,
)

from tests.support import charge_hop, phase, profile, route, run_for

pytestmark = [pytest.mark.ftr("FTR-WFC-ATOMIC-HOP-RECOVERY")]


def gate_profile():
    return profile(
        {
            "implement": phase({"review": route("goto", "review")}),
            "review": phase(
                {
                    "accept": route("complete"),
                    "correct": route("goto", "implement"),
                },
                role="independent_reviewer",
                review="gate",
                finding_authority=("resolve", "residual", "supersede", "reopen"),
            ),
        }
    )


def interrupted_run(*, hops_before: int = 0, budget: int = 5):
    """A Run interrupted after its dispatch marker existed, written by the
    production path: ``hops_before`` ordinary charged Hops, then the
    Coordinator's own charged uncertain Hop and ``reconcile_required``."""

    run = run_for(gate_profile(), budget=budget)
    for _ in range(hops_before):
        run, _ = charge_hop(run, end_head=run.current_head)
        # What the reducer's own route application does between Hops; this
        # fixture charges Hops directly, so it restores the same precondition.
        run = replace(run, status=RunStatus.READY)
    marker = DispatchMarker(
        schema_version=1, run_id=run.run_id, hop_id=f"H{run.hop_used + 1:03d}",
        sequence=run.hop_used + 1, invocation_id="INV-interrupted",
        base_head=run.current_head, budget_reserved=1,
    )
    interrupted = recover_uncertain_dispatch(
        run, marker, current_head=run.current_head, actual_commits=(),
    )
    return interrupted, marker


# --------------------------------------------------------------------------
# The admitted class.
# --------------------------------------------------------------------------

def test_production_interruption_path_is_admitted_and_names_its_hop():
    run, marker = interrupted_run()
    assert run.status is RunStatus.RECONCILE_REQUIRED
    assert run.reconcile_reason == INTERRUPTION_RECONCILE_REASON
    assert run.stop_reason == EXPLICIT_RESUME_STOP_REASON
    assert run.human_question == INTERRUPTION_RECONCILE_QUESTION

    hop_id, refusal = diagnose_quiescent_interruption_recovery_eligibility(run)
    assert (hop_id, refusal) == (marker.hop_id, None)

    hop = run.hops[-1]
    assert hop.role == UNCERTAIN_DISPATCH_ROLE
    assert hop.outcome == UNCERTAIN_DISPATCH_OUTCOME
    assert hop.stop_reason == UNCERTAIN_DISPATCH_HOP_STOP_REASON
    assert hop.attribution == UNCERTAIN_DISPATCH_ATTRIBUTION


def test_admitted_mid_run_interruption_keeps_the_charge_and_resumes_purely():
    """The record's Case A: an interruption after real Hops. The charge is
    never refunded and the pure resume returns the identical Run to ``ready``
    at its unchanged phase, Hop count and governing identity."""

    run, _ = interrupted_run(hops_before=2)
    hop_id, refusal = diagnose_quiescent_interruption_recovery_eligibility(run)
    assert (hop_id, refusal) == ("H003", None)
    assert run.hop_used == 3

    resumed = resume_after_reconcile(run, gate_profile(), current_head=run.current_head)
    assert resumed.status is RunStatus.READY
    assert resumed.hop_used == 3  # the uncertain Hop stays charged.
    assert resumed.hops == run.hops
    assert resumed.phase == run.phase
    assert resumed.stop_reason is None and resumed.human_question is None


def test_open_blocking_finding_is_admitted_and_never_touched():
    """Deliberately *not* a refusal (unlike the durable-artifact class): the
    record's Case A is an interrupted correction Hop with an open Finding.
    Resume changes no Finding state; disposition stays the reviewer's."""

    run, _ = interrupted_run(hops_before=1)
    finding = Finding(
        finding_id="F001", invariant="fixture invariant", blocking=True,
        state=FindingState.OPEN, introduced_by_hop="H001", changed_by_hop="H001",
        evidence_refs=("tests",), disposition="opened by the independent reviewer",
    )
    with_finding = replace(run, findings=(finding,))
    hop_id, refusal = diagnose_quiescent_interruption_recovery_eligibility(with_finding)
    assert (hop_id, refusal) == ("H002", None)

    resumed = resume_after_reconcile(
        with_finding, gate_profile(), current_head=with_finding.current_head,
    )
    assert resumed.findings == (finding,)


def test_a_resumed_run_bounces_back_while_the_marker_is_not_retired():
    """Why the boundary must retire the already-reconciled marker: this is
    the exact call ``slice4.run_real_one_hop``'s stale-marker branch makes
    on re-entry. It charges nothing twice -- and returns the Run straight
    back to ``reconcile_required``, so without the retirement a resumed Run
    could never progress."""

    run, marker = interrupted_run()
    resumed = resume_after_reconcile(run, gate_profile(), current_head=run.current_head)
    assert resumed.status is RunStatus.READY

    bounced = recover_uncertain_dispatch(
        resumed, marker, current_head=resumed.current_head, actual_commits=(),
    )
    assert bounced.status is RunStatus.RECONCILE_REQUIRED
    assert bounced.hop_used == resumed.hop_used == 1


def test_idempotent_re_entry_of_the_same_marker_stays_admitted():
    """The engine's own stale-marker re-entry charges nothing twice; the
    predicate must still admit the identical Run."""

    run, marker = interrupted_run()
    again = recover_uncertain_dispatch(
        run, marker, current_head=run.current_head, actual_commits=(),
    )
    assert again.hop_used == run.hop_used == 1
    assert diagnose_quiescent_interruption_recovery_eligibility(again) == (marker.hop_id, None)


# --------------------------------------------------------------------------
# The scope gate: never in play, so every other class keeps its behaviour.
# --------------------------------------------------------------------------

def test_scope_gate_for_a_run_that_was_never_interrupted():
    run = run_for(gate_profile())
    assert diagnose_quiescent_interruption_recovery_eligibility(run) == (None, None)


def test_scope_gate_for_a_prerequisite_return_reconcile():
    run = run_for(gate_profile())
    suspended = replace(
        run, status=RunStatus.SUSPENDED, suspension_head=run.current_head,
        child_run_id="RUN-child",
    )
    returned = require_reconcile(
        suspended, reason="prerequisite_return", current_head=run.current_head,
    )
    assert returned.reconcile_reason == "prerequisite_return"
    assert diagnose_quiescent_interruption_recovery_eligibility(returned) == (None, None)


def test_scope_gate_for_a_resumed_run_no_longer_requiring_reconciliation():
    run, _ = interrupted_run()
    resumed = resume_after_reconcile(run, gate_profile(), current_head=run.current_head)
    assert diagnose_quiescent_interruption_recovery_eligibility(resumed) == (None, None)


# --------------------------------------------------------------------------
# Every conjunct violated one at a time: a named refusal, never an admission
# and never the scope gate.
# --------------------------------------------------------------------------

def _refusal(run) -> str:
    hop_id, refusal = diagnose_quiescent_interruption_recovery_eligibility(run)
    assert hop_id is None
    assert refusal is not None
    return refusal


def test_refuses_a_recorded_repository_anomaly():
    run, _ = interrupted_run()
    dirty = replace(run, anomalies=("dirty tracked state", "non-ignored untracked state"))
    assert "dirty tracked state" in _refusal(dirty)


def test_refuses_an_open_semantic_stop():
    run, _ = interrupted_run()
    frozen = replace(run, semantic_stop=SemanticStop(
        kind="scope", question="May I widen the scope?", source_hop_id="H001",
    ))
    assert "semantic stop" in _refusal(frozen)


def test_refuses_an_open_human_question_of_its_own():
    run, _ = interrupted_run()
    asked = replace(run, human_question="Which test fails?")
    assert "human question" in _refusal(asked)


def test_refuses_another_stop_reason():
    run, _ = interrupted_run()
    assert "stop reason" in _refusal(replace(run, stop_reason="something else"))


def test_refuses_a_commit_left_by_the_interrupted_invocation():
    run, _ = interrupted_run()
    hop = replace(run.hops[-1], actual_commits=("c" * 40,))
    assert "left a commit" in _refusal(replace(run, hops=(hop,)))


def test_refuses_head_movement_during_the_interrupted_hop():
    run, _ = interrupted_run()
    hop = replace(run.hops[-1], end_head="head-moved")
    moved = replace(run, hops=(hop,))
    assert "left a commit" in _refusal(moved)  # base_head != end_head is proof enough.


def test_refuses_head_movement_after_the_charge():
    run, _ = interrupted_run()
    moved = replace(run, current_head="head-later")
    assert "HEAD moved" in _refusal(moved)


def test_refuses_a_published_staged_qualification_attempt():
    run, _ = interrupted_run()
    hop = replace(run.hops[-1], pre_route_anchor_sha256="f" * 64)
    assert "staged qualification attempt" in _refusal(replace(run, hops=(hop,)))


def test_refuses_a_last_hop_that_is_not_the_charged_uncertain_hop():
    run, _ = interrupted_run()
    hop = replace(run.hops[-1], role="worker")
    assert "not the Coordinator's charged uncertain Hop" in _refusal(replace(run, hops=(hop,)))


@pytest.mark.parametrize("field,value", [
    ("outcome", "completed"),
    ("stop_reason", None),
    ("attribution", "agent"),
])
def test_refuses_a_hop_without_the_recorded_interruption_shape(field, value):
    run, _ = interrupted_run()
    hop = replace(run.hops[-1], **{field: value})
    assert "recorded post-dispatch interruption shape" in _refusal(replace(run, hops=(hop,)))


@pytest.mark.parametrize("field,value", [
    ("applied_route", "review"),
    ("finding_delta_ids", ("F001",)),
    ("evidence_refs", ("tests",)),
])
def test_refuses_a_hop_claiming_reduction_authority(field, value):
    run, _ = interrupted_run()
    hop = replace(run.hops[-1], **{field: value})
    assert "claims a route, a Finding delta or evidence" in _refusal(replace(run, hops=(hop,)))


def test_refuses_a_reconcile_without_any_recorded_hop():
    run = run_for(gate_profile())
    empty = require_reconcile(
        run, reason=INTERRUPTION_RECONCILE_REASON, current_head=run.current_head,
    )
    assert empty.hops == ()
    assert _refusal(empty) == "the Run has no recorded Hop outcome"
