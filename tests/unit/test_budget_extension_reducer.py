"""O8, WI-WFC-BETA2-O8-001: the pure reduction, the closed wire form and the
checkpoint projection of a recorded maintainer Hop-budget extension.

Every refusal of ``reducer.extend_hop_budget`` is exercised one at a time,
and the two invariants the record names are pinned on the wire: used Hops
are never reset or refunded (`WFC-AUTO-05`), and an extension chain must add
up to the Run's own ``hop_limit`` so the admitted ceiling stays
reconstructible. The lifecycle, its live-fact refusals and the visibility
surfaces are in ``test_budget_extension_lifecycle.py``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from workflow_coordinator.model import (
    COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS,
    Finding,
    FindingState,
    HopSummary,
    Run,
    RunStatus,
    SemanticStop,
)
from workflow_coordinator.persistence import checkpoint_projection
from workflow_coordinator.reducer import (
    BUDGET_EXTENSION_MAX_PER_CALL,
    BUDGET_EXTENSION_MIN_AUTHORITY_CHARACTERS,
    ReductionError,
    budget_extension_total_bound,
    diagnose_budget_extension_amount,
    diagnose_budget_extension_state,
    extend_hop_budget,
    refuse_degenerate_authority,
)
from workflow_coordinator.serialization import (
    ValidationError,
    run_from_json,
    run_to_json,
)
from tests.support import phase, profile, route

AUTHORITY = (
    "Maintainer instruction in this session on 2026-11-27: extend by one "
    "correction cycle so the open broad review can finish."
)
GRANTED_AT = "2026-11-27T09:30:00Z"
HEAD = "b" * 40


def _hop(sequence: int) -> HopSummary:
    return HopSummary(
        hop_id=f"H{sequence:03d}", sequence=sequence,
        role="worker" if sequence == 1 else "independent_reviewer",
        outcome="completed", base_head="a" * 40, end_head=HEAD, actual_commits=(),
        finding_delta_ids=(), evidence_refs=(), applied_route=None, stop_reason=None,
        summary="fixture Hop", attribution="agent",
    )


def budget_stopped_run(*, hop_limit: int = 10, schema_version: int = 2, **overrides) -> Run:
    run = Run(
        schema_version=schema_version, run_id="RUN-o8", work_item_id="WI-O8",
        profile_id="profile", profile_revision="1", policy_id="policy", policy_revision="1",
        repository="consumer", baseline_head="a" * 40, current_head=HEAD, phase="broad_review",
        status=RunStatus.AWAITING_HUMAN_BUDGET, hop_limit=hop_limit, hop_used=hop_limit,
        hops=tuple(_hop(sequence) for sequence in range(1, hop_limit + 1)),
        stop_reason="autonomous_hop_budget_exhausted",
    )
    return replace(run, **overrides) if overrides else run


def _granted(run: Run, *, additional_hops: int = 3, authority: str = AUTHORITY) -> Run:
    return extend_hop_budget(
        run, additional_hops=additional_hops, authority=authority,
        granted_at=GRANTED_AT, policy_max_autonomous_hops=32,
    )


# --------------------------------------------------------------------------
# The accepted reduction.
# --------------------------------------------------------------------------

class TestAcceptedExtension:
    def test_raises_the_ceiling_and_returns_the_run_to_ready_at_its_phase(self):
        run = budget_stopped_run()
        extended = _granted(run)

        assert extended.status is RunStatus.READY
        assert extended.stop_reason is None
        assert extended.hop_limit == 13
        assert extended.phase == run.phase

    def test_never_resets_or_refunds_a_charged_hop(self):
        run = budget_stopped_run()
        extended = _granted(run)

        assert extended.hop_used == run.hop_used == 10
        assert extended.hops == run.hops
        assert extended.hop_remaining == 3
        assert extended.budget_extensions[0].hop_used_at_grant == 10

    def test_keeps_the_admitted_ceiling_reconstructible_from_the_run_alone(self):
        extended = _granted(budget_stopped_run())

        assert extended.admitted_hop_limit == 10
        assert extended.extended_hops == 3

    def test_records_who_authorised_it_when_how_much_and_against_which_state(self):
        run = budget_stopped_run()
        grant = _granted(run).budget_extensions[0]

        assert grant.sequence == 1
        assert grant.granted_at == GRANTED_AT
        assert grant.additional_hops == 3
        assert grant.hop_limit_before == 10
        assert grant.hop_limit_after == 13
        assert grant.hop_used_at_grant == 10
        assert grant.run_status_at_grant == "awaiting_human_budget"
        assert grant.stop_reason_at_grant == "autonomous_hop_budget_exhausted"
        assert grant.current_head_at_grant == HEAD
        assert grant.authority == AUTHORITY
        assert grant.authority_sha256 == hashlib.sha256(AUTHORITY.encode("utf-8")).hexdigest()

    def test_an_open_blocking_finding_does_not_block_an_extension(self):
        """The whole point of the slice (friction entry 19): the Run must be
        able to continue its own reviewed correction/review chain, which is
        the only thing that may dispose of that Finding. The reduction
        neither reads nor changes Finding state (D7b)."""

        finding = Finding(
            finding_id="F001", invariant="an unused import remains", blocking=True,
            state=FindingState.OPEN, introduced_by_hop="H010", changed_by_hop="H010",
            evidence_refs=("lint",), disposition="open",
        )
        run = budget_stopped_run(findings=(finding,))
        extended = _granted(run)

        assert extended.findings == (finding,)

    def test_a_second_extension_continues_the_chain(self):
        first = _granted(budget_stopped_run(hop_limit=8), additional_hops=2)
        stopped_again = replace(
            first, status=RunStatus.AWAITING_HUMAN_BUDGET,
            stop_reason="autonomous_hop_budget_exhausted", hop_used=10,
            hops=tuple(_hop(sequence) for sequence in range(1, 11)),
        )
        second = _granted(stopped_again, additional_hops=2)

        assert [grant.sequence for grant in second.budget_extensions] == [1, 2]
        assert second.budget_extensions[1].hop_limit_before == 10
        assert second.hop_limit == 12
        assert second.admitted_hop_limit == 8


# --------------------------------------------------------------------------
# Every refusal, one at a time.
# --------------------------------------------------------------------------

class TestStateRefusals:
    @pytest.mark.parametrize("status", sorted(
        {status for status in RunStatus if status is not RunStatus.AWAITING_HUMAN_BUDGET},
        key=lambda status: status.value,
    ))
    def test_every_other_run_state_is_refused_by_name(self, status):
        run = replace(budget_stopped_run(), status=status)

        assert diagnose_budget_extension_state(run) == (
            "the Run is not stopped on an exhausted autonomous Hop budget: " + status.value
        )
        with pytest.raises(ReductionError, match="not stopped on an exhausted autonomous Hop budget"):
            _granted(run)

    def test_a_non_budget_stop_reason_is_refused_even_in_the_budget_state(self):
        run = replace(budget_stopped_run(), stop_reason="mechanical_preemption:something else")

        with pytest.raises(ReductionError, match="stop reason is not an exhausted autonomous Hop budget"):
            _granted(run)

    def test_a_run_that_is_not_actually_out_of_budget_is_refused(self):
        run = replace(
            budget_stopped_run(), hop_limit=12,
        )

        with pytest.raises(ReductionError, match="not actually out of autonomous Hop budget"):
            _granted(run)

    def test_a_frozen_semantic_stop_is_refused(self):
        run = budget_stopped_run(semantic_stop=SemanticStop(
            kind="scope", question="may I touch a file outside scope?", source_hop_id="H010",
        ))

        with pytest.raises(ReductionError, match="frozen semantic stop"):
            _granted(run)


class TestAmountRefusals:
    @pytest.mark.parametrize("amount", [0, -1, -4])
    def test_a_non_positive_amount_is_refused(self, amount):
        with pytest.raises(ReductionError, match="must be a positive integer"):
            _granted(budget_stopped_run(), additional_hops=amount)

    @pytest.mark.parametrize("amount", [True, 1.5, "3", None])
    def test_a_non_integer_amount_is_refused(self, amount):
        with pytest.raises(ReductionError, match="must be a positive integer"):
            _granted(budget_stopped_run(), additional_hops=amount)

    def test_the_per_call_bound_is_refused_by_name(self):
        with pytest.raises(ReductionError, match=f"exceeds the per-call bound of {BUDGET_EXTENSION_MAX_PER_CALL} Hops"):
            _granted(budget_stopped_run(), additional_hops=BUDGET_EXTENSION_MAX_PER_CALL + 1)

    def test_the_total_bound_is_half_the_admitted_ceiling_rounded_up(self):
        assert budget_extension_total_bound(budget_stopped_run(hop_limit=10)) == 5
        assert budget_extension_total_bound(budget_stopped_run(hop_limit=9)) == 5
        assert budget_extension_total_bound(budget_stopped_run(hop_limit=2)) == 1

    def test_repeated_small_extensions_cannot_creep_past_the_total_bound(self):
        run = budget_stopped_run(hop_limit=4)  # total bound 2.
        first = _granted(run, additional_hops=2)
        stopped_again = replace(
            first, status=RunStatus.AWAITING_HUMAN_BUDGET,
            stop_reason="autonomous_hop_budget_exhausted", hop_used=6,
            hops=tuple(_hop(sequence) for sequence in range(1, 7)),
        )

        with pytest.raises(ReductionError, match="exceeds this Run's total extension bound of 2 Hops"):
            _granted(stopped_again, additional_hops=1)

    def test_the_admitted_policy_ceiling_is_refused_by_name(self):
        run = budget_stopped_run()

        assert diagnose_budget_extension_amount(
            run, 3, policy_max_autonomous_hops=12,
        ) == (
            "the resulting ceiling of 13 Hops exceeds the admitted repository policy "
            "ceiling of 12 Hops"
        )
        with pytest.raises(ReductionError, match="admitted repository policy ceiling"):
            extend_hop_budget(
                run, additional_hops=3, authority=AUTHORITY, granted_at=GRANTED_AT,
                policy_max_autonomous_hops=12,
            )

    def test_the_coordinator_hard_maximum_is_refused_by_name(self):
        run = budget_stopped_run(hop_limit=COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS)

        with pytest.raises(ReductionError, match="Coordinator hard maximum"):
            extend_hop_budget(
                run, additional_hops=1, authority=AUTHORITY, granted_at=GRANTED_AT,
                policy_max_autonomous_hops=COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS + 10,
            )


class TestAuthorityRefusals:
    def test_an_empty_statement_is_refused(self):
        assert refuse_degenerate_authority("   ") == (
            "the recorded maintainer authority statement is empty"
        )
        with pytest.raises(ReductionError, match="authority statement is empty"):
            _granted(budget_stopped_run(), authority="")

    @pytest.mark.parametrize("statement", [
        "yes", "--yes", "OK", "approved", "maintainer approved",
        "--yes --yes --yes --yes --yes --yes --yes --yes --yes --yes",
        "Yes, approved. Granted!",
    ])
    def test_a_bare_authorisation_gesture_is_refused_by_name(self, statement):
        """The realistic failure mode the record names: an agent reusing the
        launch ritual's own ``--yes`` as if it were authority. Long repeated
        gestures are refused too, so the length rule cannot be padded out
        with them."""

        assert "bare authorisation token" in refuse_degenerate_authority(statement)
        with pytest.raises(ReductionError, match="bare authorisation token"):
            _granted(budget_stopped_run(), authority=statement)

    def test_a_statement_too_short_to_name_an_instruction_is_refused(self):
        statement = "maintainer said do it"
        assert len(statement) < BUDGET_EXTENSION_MIN_AUTHORITY_CHARACTERS

        with pytest.raises(ReductionError, match="too short to name the maintainer's instruction"):
            _granted(budget_stopped_run(), authority=statement)

    def test_an_extension_must_record_the_time_it_was_granted(self):
        with pytest.raises(ReductionError, match="record the time it was granted"):
            extend_hop_budget(
                budget_stopped_run(), additional_hops=1, authority=AUTHORITY,
                granted_at="   ", policy_max_autonomous_hops=32,
            )


# --------------------------------------------------------------------------
# The closed wire form.
# --------------------------------------------------------------------------

class TestWireForm:
    def test_an_unextended_run_writes_no_budget_extensions_key_at_all(self):
        document = json.loads(run_to_json(budget_stopped_run()))

        assert "budget_extensions" not in document

    def test_an_extended_run_round_trips_exactly(self):
        extended = _granted(budget_stopped_run())

        assert run_from_json(run_to_json(extended)) == extended

    def test_a_run_written_before_this_slice_still_loads(self):
        document = json.loads(run_to_json(budget_stopped_run()))
        assert "budget_extensions" not in document

        assert run_from_json(json.dumps(document)).budget_extensions == ()

    def _tampered(self, **changes) -> str:
        document = json.loads(run_to_json(_granted(budget_stopped_run())))
        for key, value in changes.items():
            document[key] = value
        return json.dumps(document)

    def test_refunding_a_charged_hop_is_refused_on_the_wire(self):
        """`WFC-AUTO-05` as a persistence invariant: a Run whose ``hop_used``
        fell below a recorded grant's own charge cannot be loaded at all."""

        document = json.loads(run_to_json(_granted(budget_stopped_run())))
        document["hop_used"] = 5
        document["hops"] = document["hops"][:5]

        with pytest.raises(ValidationError, match="used Hops are never refunded"):
            run_from_json(json.dumps(document))

    def test_a_ceiling_that_does_not_match_the_chain_is_refused(self):
        with pytest.raises(ValidationError, match="does not add up to Run.hop_limit"):
            run_from_json(self._tampered(hop_limit=20))

    def test_a_forged_authority_digest_is_refused(self):
        document = json.loads(run_to_json(_granted(budget_stopped_run())))
        document["budget_extensions"][0]["authority"] = "something else entirely"

        with pytest.raises(ValidationError, match="does not digest its own authority statement"):
            run_from_json(json.dumps(document))

    @pytest.mark.parametrize("key, value, message", [
        ("run_status_at_grant", "ready", "not granted against a budget-stopped Run"),
        ("stop_reason_at_grant", "some other stop", "not granted against an exhausted Hop budget"),
        ("sequence", 7, "not its own position in the grant order"),
    ])
    def test_a_grant_against_another_state_is_refused(self, key, value, message):
        document = json.loads(run_to_json(_granted(budget_stopped_run())))
        document["budget_extensions"][0][key] = value

        with pytest.raises(ValidationError, match=message):
            run_from_json(json.dumps(document))

    def test_an_arithmetically_inconsistent_grant_is_refused(self):
        document = json.loads(run_to_json(_granted(budget_stopped_run())))
        document["budget_extensions"][0]["additional_hops"] = 2

        with pytest.raises(ValidationError, match="does not add its own additional Hops"):
            run_from_json(json.dumps(document))

    def test_a_grant_list_that_is_not_a_list_is_refused(self):
        with pytest.raises(ValidationError, match="Run.budget_extensions must be a list"):
            run_from_json(self._tampered(budget_extensions={"sequence": 1}))


# --------------------------------------------------------------------------
# The checkpoint projection.
# --------------------------------------------------------------------------

def test_the_checkpoint_states_the_effective_ceiling_and_what_was_added():
    workflow = profile({"broad_review": phase({"accept": route("complete")})})
    extended = _granted(budget_stopped_run())
    projection = checkpoint_projection(extended, workflow)

    assert projection["budget"] == {
        "used": 10, "remaining": 3, "limit": 13, "extended_by": 3,
    }
