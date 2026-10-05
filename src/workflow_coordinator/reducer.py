"""Explicit workflow reduction: preemption, then finite deterministic routing."""

from __future__ import annotations

from dataclasses import replace
import re

from .model import (
    ConvergenceFact,
    ConvergenceAssessmentResult,
    ConvergenceRole,
    CorrectionObligation,
    CorrectionObligationOwner,
    CoverageDisposition,
    Finding,
    FindingAuthority,
    ExistingFindingDelta,
    FindingDeltaValue,
    FindingState,
    MechanicalFacts,
    NewFindingDelta,
    Outcome,
    Reduction,
    ReviewAuthority,
    Route,
    RouteEffect,
    RoutingResult,
    StrategyAssessmentResult,
    Run,
    RunStatus,
    SemanticStop,
    TrustedQualificationIndex,
    WorkflowProfile,
)


class ReductionError(ValueError):
    """State and profile cannot be reduced without guessing."""


def trusted_evaluator_qualification(
    index: TrustedQualificationIndex,
    evaluator_hop_id: str,
) -> bool:
    """Return the mechanical conjunct for one exactly correlated evaluator Hop."""

    matching = tuple(
        attempt for attempt in index.attempts if attempt.hop_id == evaluator_hop_id
    )
    if len(matching) != 1:
        return False
    attempt = matching[0]
    required = {
        (item.command_name, item.argv_digest, item.timeout_seconds)
        for item in attempt.required_evidence
    }
    observations = {
        (item.command_name, item.argv_digest, item.timeout_seconds): item.status
        for item in attempt.trusted_observations
    }
    return set(observations) == required and all(
        observations[identity] == "pass" for identity in required
    )


def derive_convergence_facts(
    run: Run,
    trusted_qualification_index: TrustedQualificationIndex,
    qualified_cycle_limit: int,
) -> tuple[ConvergenceFact, ...]:
    """Derive convergence facts from immutable accepted Hop history.

    Slice 1 admits and validates coverage, assessment correlation, evidence
    freshness, and post-assessment Finding state.  This read-side projection
    deliberately does not duplicate those admission rules or persist a shadow
    counter.  Later slices may add generation advancement; Slice 2 has no
    accepted event that can produce a generation other than zero.
    """

    if (
        isinstance(qualified_cycle_limit, bool)
        or not isinstance(qualified_cycle_limit, int)
        or qualified_cycle_limit <= 0
    ):
        raise ValueError("qualified_cycle_limit must be a positive integer")
    if run.schema_version != 3:
        return ()
    if trusted_qualification_index.run is not None:
        expected_identity = (
            run.run_id,
            run.work_item_id,
            run.profile_id,
            run.profile_revision,
        )
        actual_identity = (
            trusted_qualification_index.run.run_id,
            trusted_qualification_index.run.work_item_id,
            trusted_qualification_index.run.profile_id,
            trusted_qualification_index.run.profile_revision,
        )
        if actual_identity != expected_identity:
            raise ReductionError("trusted qualification index identity mismatch")

    eligible_findings = {
        finding.finding_id: finding
        for finding in run.findings
        if (
            finding.correction_obligation is not None
            and finding.state is FindingState.OPEN
            and finding.blocking
        )
    }
    source_hops: dict[str, list[str]] = {
        finding_id: [] for finding_id in eligible_findings
    }
    generations: dict[str, int] = {finding_id: 0 for finding_id in eligible_findings}
    hops_by_id = {hop.hop_id: hop for hop in run.hops}
    hop_order = {hop.hop_id: index for index, hop in enumerate(run.hops)}

    for evaluator_hop in run.hops:
        for assessment in evaluator_hop.convergence_assessments:
            if (
                assessment.finding_id not in eligible_findings
                or assessment.result
                is not ConvergenceAssessmentResult.CARRIED_NEW_COUNTEREXAMPLE
                or assessment.finding_id not in evaluator_hop.finding_delta_ids
                or not trusted_evaluator_qualification(
                    trusted_qualification_index, evaluator_hop.hop_id
                )
            ):
                continue
            correction_hop = hops_by_id.get(assessment.correction_hop_id)
            if (
                correction_hop is None
                or hop_order[correction_hop.hop_id] >= hop_order[evaluator_hop.hop_id]
            ):
                continue
            matching_claims = tuple(
                claim
                for claim in correction_hop.coverage_claims
                if claim.finding_id == assessment.finding_id
            )
            if (
                len(matching_claims) != 1
                or matching_claims[0].disposition is not CoverageDisposition.ADDRESSED
            ):
                continue
            source_hops[assessment.finding_id].append(evaluator_hop.hop_id)
        # An accepted assessment is the only immutable event that starts the
        # next generation; exact correlation was checked on admission.
        accepted = evaluator_hop.strategy_assessment
        if accepted is not None and accepted.result is StrategyAssessmentResult.ACCEPTED:
            for finding_id in accepted.finding_ids:
                if finding_id in generations:
                    generations[finding_id] += 1
                    source_hops[finding_id] = []

    return tuple(
        ConvergenceFact(
            finding_id=finding_id,
            strategy_generation=generations[finding_id],
            qualified_cycle_count=len(source_hops[finding_id]),
            qualified_cycle_limit=qualified_cycle_limit,
            goalpost_reached=(
                len(source_hops[finding_id]) >= qualified_cycle_limit
            ),
            source_evaluator_hops=tuple(source_hops[finding_id]),
        )
        for finding_id in sorted(eligible_findings)
    )


def validate_convergence_route_history(
    run: Run,
    profile: WorkflowProfile,
    trusted_qualification_index: TrustedQualificationIndex,
) -> None:
    """Replay-check every persisted Slice-3 escape decision.

    The Run wire provides syntax and concrete Finding identity checks.  This
    profile/evidence-aware boundary additionally proves the source phase,
    pinned route declarations, and exact post-assessment goalpost set.
    """

    if run.schema_version != 3 or not any(
        hop.route_reason is not None for hop in run.hops
    ):
        return
    if profile.schema_version != 3 or profile.convergence is None:
        raise ReductionError("Run v3 requires its pinned convergence profile")
    if (
        run.profile_id != profile.profile_id
        or run.profile_revision != profile.revision
    ):
        raise ReductionError("pinned profile identity changed")

    hop_order = {hop.hop_id: index for index, hop in enumerate(run.hops)}
    for boundary, hop in enumerate(run.hops):
        if hop.route_reason is None:
            continue
        phase = profile.phases.get(hop.phase or "")
        if phase is None or phase.convergence_role is not ConvergenceRole.EVALUATOR:
            raise ReductionError(
                f"convergence escape Hop {hop.hop_id} is not an evaluator phase"
            )
        if (
            hop.outcome != Outcome.CHANGES_REQUIRED.value
            or hop.requested_route != phase.normal_correction_route
        ):
            raise ReductionError(
                f"convergence escape Hop {hop.hop_id} did not request its normal correction route"
            )
        escape_route = phase.routes.get(phase.escape_route or "")
        if hop.applied_route is None:
            budget_preempted = (
                hop.stop_reason == "autonomous_hop_budget_exhausted"
            )
            supervisor_pending = (
                hop.stop_reason == "supervisor_judgment_required"
                and escape_route is not None
                and escape_route.supervisor
            )
            if not (budget_preempted or supervisor_pending):
                raise ReductionError(
                    f"convergence escape Hop {hop.hop_id} lacks an applied escape route"
                )
        elif hop.applied_route != phase.escape_route:
            raise ReductionError(
                f"convergence escape Hop {hop.hop_id} applied the wrong escape route"
            )

        prefix_hops = run.hops[: boundary + 1]
        boundary_findings: list[Finding] = []
        for finding in run.findings:
            obligation = finding.correction_obligation
            introduced_at = hop_order.get(finding.introduced_by_hop)
            if obligation is None or introduced_at is None or introduced_at > boundary:
                continue
            state = FindingState.OPEN
            blocking = True
            for prior_hop in prefix_hops[introduced_at:]:
                matching = next(
                    (
                        assessment
                        for assessment in prior_hop.convergence_assessments
                        if assessment.finding_id == finding.finding_id
                    ),
                    None,
                )
                if matching is None:
                    continue
                if matching.result is ConvergenceAssessmentResult.CLOSED:
                    state = FindingState.RESOLVED
                elif matching.result is ConvergenceAssessmentResult.RESIDUALIZED:
                    state = FindingState.RESIDUAL
                    blocking = False
                elif matching.result is ConvergenceAssessmentResult.SUPERSEDED:
                    state = FindingState.SUPERSEDED
                else:
                    state = FindingState.OPEN
                    blocking = True
            boundary_findings.append(
                replace(finding, state=state, blocking=blocking)
            )
        boundary_run = replace(
            run,
            hops=prefix_hops,
            hop_used=len(prefix_hops),
            findings=tuple(boundary_findings),
        )
        expected = tuple(
            fact.finding_id
            for fact in derive_convergence_facts(
                boundary_run,
                trusted_qualification_index,
                profile.convergence.qualified_cycle_limit,
            )
            if fact.goalpost_reached
        )
        actual = tuple(hop.route_reason.removeprefix("convergence_escape:").split(","))
        if actual != expected:
            raise ReductionError(
                f"convergence escape Hop {hop.hop_id} trigger set is not replayable"
            )


def _stop_for_human(run: Run, reason: str, question: str | None = None) -> Reduction:
    return Reduction(
        run=replace(
            run,
            status=RunStatus.AWAITING_HUMAN,
            stop_reason=reason,
            human_question=question,
        ),
        route_key=None,
        autonomous_route=False,
        supervisor_required=False,
    )


def _with_hop_decision(
    reduction: Reduction,
    hop_id: str,
    *,
    requested_route: str | None = None,
    route_reason: str | None = None,
) -> Reduction:
    """Correlate the reducer decision back into the compact accepted-Hop record."""

    updated_hops = tuple(
        (
            replace(
                hop,
                applied_route=reduction.route_key,
                stop_reason=reduction.run.stop_reason,
                requested_route=requested_route,
                route_reason=route_reason,
            )
            if hop.hop_id == hop_id
            else hop
        )
        for hop in reduction.run.hops
    )
    return replace(reduction, run=replace(reduction.run, hops=updated_hops))


def _profile_phase(run: Run, profile: WorkflowProfile):
    if run.schema_version not in {1, 2, 3} or profile.schema_version not in {2, 3}:
        return None, "unsupported state/profile schema version"
    if profile.schema_version == 3 and run.schema_version != 3:
        return None, "profile v3 requires Run schema_version 3"
    if run.profile_id != profile.profile_id or run.profile_revision != profile.revision:
        return None, "pinned profile identity changed"
    phase = profile.phases.get(run.phase)
    if phase is None:
        return None, f"Run references unknown phase: {run.phase}"
    return phase, None


def _next_finding_id(findings: tuple[Finding, ...]) -> str:
    used = {finding.finding_id for finding in findings}
    sequence = 1
    while f"F{sequence:03d}" in used:
        sequence += 1
    return f"F{sequence:03d}"


def _finding_operation(previous: FindingState | None, proposed: FindingState) -> str | None:
    creation_operations = {
        FindingState.OPEN: None,
        FindingState.RESOLVED: "resolve",
        FindingState.RESIDUAL: "residual",
        FindingState.SUPERSEDED: "supersede",
    }
    if previous is None:
        return creation_operations[proposed]
    transition_operations = {
        (FindingState.OPEN, FindingState.RESOLVED): "resolve",
        (FindingState.OPEN, FindingState.RESIDUAL): "residual",
        (FindingState.OPEN, FindingState.SUPERSEDED): "supersede",
        (FindingState.RESOLVED, FindingState.OPEN): "reopen",
        (FindingState.RESOLVED, FindingState.SUPERSEDED): "supersede",
    }
    return transition_operations.get((previous, proposed))


def _may_dispose(
    authority: FindingAuthority,
    operation: str,
    permitted_gate_operations: tuple[str, ...],
) -> bool:
    if authority is FindingAuthority.HUMAN:
        return True
    if authority is FindingAuthority.GATE:
        return operation in permitted_gate_operations
    return False


def apply_finding_deltas(
    run: Run,
    deltas: tuple[FindingDeltaValue, ...],
    *,
    hop_id: str,
    authority: FindingAuthority,
    permitted_gate_operations: tuple[str, ...] = (),
    convergence_role: ConvergenceRole = ConvergenceRole.NONE,
    result_schema_version: int = 1,
) -> Run:
    """Apply proposals while keeping authoritative disposition out of supervisor APIs."""

    updated, _ = _apply_finding_deltas(
        run,
        deltas,
        hop_id=hop_id,
        authority=authority,
        permitted_gate_operations=permitted_gate_operations,
        convergence_role=convergence_role,
        result_schema_version=result_schema_version,
    )
    return updated


def _apply_finding_deltas(
    run: Run,
    deltas: tuple[FindingDeltaValue, ...],
    *,
    hop_id: str,
    authority: FindingAuthority,
    permitted_gate_operations: tuple[str, ...] = (),
    convergence_role: ConvergenceRole = ConvergenceRole.NONE,
    result_schema_version: int = 1,
) -> tuple[Run, tuple[str, ...]]:
    """Atomically validate/apply proposals and report truthful changed IDs."""

    if result_schema_version == 1:
        return _apply_legacy_finding_deltas(
            run,
            deltas,
            hop_id=hop_id,
            authority=authority,
            permitted_gate_operations=permitted_gate_operations,
        )
    if result_schema_version != 2:
        raise ReductionError("unsupported result schema version")
    return _apply_v2_finding_deltas(
        run,
        deltas,
        hop_id=hop_id,
        authority=authority,
        permitted_gate_operations=permitted_gate_operations,
        convergence_role=convergence_role,
    )


def _apply_legacy_finding_deltas(
    run: Run,
    deltas: tuple[FindingDeltaValue, ...],
    *,
    hop_id: str,
    authority: FindingAuthority,
    permitted_gate_operations: tuple[str, ...] = (),
) -> tuple[Run, tuple[str, ...]]:
    """The unchanged result-v1 Finding transaction."""

    if authority is FindingAuthority.SUPERVISOR and deltas:
        raise ReductionError("supervisor cannot authoritatively change Finding state")

    findings = list(run.findings)
    by_id = {finding.finding_id: index for index, finding in enumerate(findings)}
    changed_ids: list[str] = []

    def changed(finding_id: str) -> None:
        if finding_id not in changed_ids:
            changed_ids.append(finding_id)

    for delta in deltas:
        if delta.finding_id is None:
            finding_id = _next_finding_id(tuple(findings))
            operation = _finding_operation(None, delta.proposed_state)
            if operation is not None and not _may_dispose(
                authority, operation, permitted_gate_operations
            ):
                raise ReductionError(
                    f"{authority.value} cannot create Finding {finding_id} as {delta.proposed_state.value}"
                )
            if delta.proposed_state is FindingState.RESIDUAL and delta.blocking:
                raise ReductionError("residual Finding must be non-blocking")
            if delta.proposed_state is FindingState.SUPERSEDED and not delta.successor_ref:
                raise ReductionError("superseded Finding requires successor_ref")
            if delta.proposed_state is not FindingState.SUPERSEDED and delta.successor_ref is not None:
                raise ReductionError("successor_ref is valid only for supersession")
            findings.append(
                Finding(
                    finding_id=finding_id,
                    invariant=delta.invariant,
                    blocking=delta.blocking,
                    state=delta.proposed_state,
                    introduced_by_hop=hop_id,
                    changed_by_hop=hop_id,
                    evidence_refs=delta.evidence_refs,
                    disposition=f"{authority.value}: {delta.proposed_state.value}",
                    successor_ref=delta.successor_ref,
                )
            )
            by_id[finding_id] = len(findings) - 1
            changed(finding_id)
            continue

        if not isinstance(delta, ExistingFindingDelta):
            raise ReductionError("existing Finding must use the existing-Finding delta form")
        index = by_id.get(delta.finding_id)
        if index is None:
            raise ReductionError(f"unknown Finding ID: {delta.finding_id}")
        previous = findings[index]

        same_state = delta.proposed_state is previous.state
        if same_state:
            appended = tuple(
                dict.fromkeys(
                    reference
                    for reference in delta.evidence_refs
                    if reference not in previous.evidence_refs
                )
            )
            if appended:
                findings[index] = replace(
                    previous,
                    changed_by_hop=hop_id,
                    evidence_refs=(*previous.evidence_refs, *appended),
                )
                changed(previous.finding_id)
            continue

        operation = _finding_operation(previous.state, delta.proposed_state)
        if operation is None and delta.proposed_state is not previous.state:
            raise ReductionError(
                f"invalid Finding transition: {previous.state.value} -> {delta.proposed_state.value}"
            )
        if operation is not None and not _may_dispose(
            authority, operation, permitted_gate_operations
        ):
            raise ReductionError(f"{authority.value} cannot perform Finding operation {operation}")
        if operation == "reopen" and not delta.new_material_evidence:
            raise ReductionError("resolved Finding reopening requires new material evidence")
        if delta.proposed_state is FindingState.SUPERSEDED and not delta.successor_ref:
            raise ReductionError("superseded Finding requires successor_ref")
        if delta.proposed_state is not FindingState.SUPERSEDED and delta.successor_ref is not None:
            raise ReductionError("successor_ref is valid only for supersession")

        findings[index] = replace(
            previous,
            blocking=False if delta.proposed_state is FindingState.RESIDUAL else previous.blocking,
            state=delta.proposed_state,
            changed_by_hop=hop_id,
            evidence_refs=tuple(dict.fromkeys((*previous.evidence_refs, *delta.evidence_refs))),
            disposition=f"{authority.value}: {delta.proposed_state.value}",
            successor_ref=delta.successor_ref,
        )
        changed(previous.finding_id)
    return replace(run, findings=tuple(findings)), tuple(changed_ids)


def _apply_v2_finding_deltas(
    run: Run,
    deltas: tuple[FindingDeltaValue, ...],
    *,
    hop_id: str,
    authority: FindingAuthority,
    permitted_gate_operations: tuple[str, ...],
    convergence_role: ConvergenceRole,
) -> tuple[Run, tuple[str, ...]]:
    """Validate and publish one result-v2 Finding graph transaction."""

    if authority is FindingAuthority.SUPERVISOR and deltas:
        raise ReductionError("supervisor cannot authoritatively change Finding state")
    if convergence_role in {
        ConvergenceRole.DIAGNOSIS,
        ConvergenceRole.REFRAME,
        ConvergenceRole.STRATEGY_GATE,
    }:
        if deltas:
            raise ReductionError(
                f"{convergence_role.value} result cannot mutate Findings"
            )
        return run, ()

    originals = {finding.finding_id: finding for finding in run.findings}
    local_refs: dict[str, NewFindingDelta] = {}
    existing_delta_ids: set[str] = set()

    # Pass 1: validate proposal-local facts and authority without allocating IDs.
    for delta in deltas:
        if delta.finding_id is None:
            if not isinstance(delta, NewFindingDelta):
                raise ReductionError("new Finding must use the new-Finding delta form")
            if delta.successor_ref is not None and delta.successor_ref.startswith("local:"):
                raise ReductionError(
                    "local successor references are valid only on existing-Finding deltas"
                )
            if delta.local_ref is not None:
                if re.fullmatch(r"N[1-9][0-9]*", delta.local_ref) is None:
                    raise ReductionError("local_ref must match N[1-9][0-9]*")
                if delta.local_ref in local_refs:
                    raise ReductionError(f"duplicate local_ref: {delta.local_ref}")
                local_refs[delta.local_ref] = delta
            if delta.proposed_state is FindingState.OPEN and delta.blocking:
                if convergence_role is not ConvergenceRole.EVALUATOR:
                    raise ReductionError(
                        "only an evaluator may create an open blocking Finding in result v2"
                    )
            operation = _finding_operation(None, delta.proposed_state)
            if operation is not None and not _may_dispose(
                authority, operation, permitted_gate_operations
            ):
                raise ReductionError(
                    f"{authority.value} cannot create Finding as {delta.proposed_state.value}"
                )
            if delta.proposed_state is FindingState.RESIDUAL and delta.blocking:
                raise ReductionError("residual Finding must be non-blocking")
            if delta.proposed_state is FindingState.SUPERSEDED and not delta.successor_ref:
                raise ReductionError("superseded Finding requires successor_ref")
            if delta.proposed_state is not FindingState.SUPERSEDED and delta.successor_ref is not None:
                raise ReductionError("successor_ref is valid only for supersession")
            continue

        if not isinstance(delta, ExistingFindingDelta):
            raise ReductionError("existing Finding must use the existing-Finding delta form")
        if delta.finding_id in existing_delta_ids:
            raise ReductionError(f"duplicate Finding delta: {delta.finding_id}")
        existing_delta_ids.add(delta.finding_id)
        previous = originals.get(delta.finding_id)
        if previous is None:
            raise ReductionError(f"unknown Finding ID: {delta.finding_id}")
        if (
            convergence_role is ConvergenceRole.CORRECTOR
            and previous.correction_obligation is not None
        ):
            raise ReductionError(
                "corrector must use coverage_claims rather than a tracked Finding delta"
            )
        same_state = delta.proposed_state is previous.state
        operation = None if same_state else _finding_operation(previous.state, delta.proposed_state)
        if not same_state and operation is None:
            raise ReductionError(
                f"invalid Finding transition: {previous.state.value} -> {delta.proposed_state.value}"
            )
        if operation is not None and not _may_dispose(
            authority, operation, permitted_gate_operations
        ):
            raise ReductionError(f"{authority.value} cannot perform Finding operation {operation}")
        if operation == "reopen":
            if convergence_role is ConvergenceRole.EVALUATOR:
                raise ReductionError("evaluator result v2 cannot reopen a Finding")
            if not delta.new_material_evidence:
                raise ReductionError("resolved Finding reopening requires new material evidence")
        if delta.proposed_state is FindingState.SUPERSEDED and not delta.successor_ref:
            raise ReductionError("superseded Finding requires successor_ref")
        if delta.proposed_state is not FindingState.SUPERSEDED and delta.successor_ref is not None:
            raise ReductionError("successor_ref is valid only for supersession")

    # Pass 2: allocate prospective concrete IDs in result order.
    allocated: dict[int, str] = {}
    local_to_concrete: dict[str, str] = {}
    reserved = list(run.findings)
    for index, delta in enumerate(deltas):
        if delta.finding_id is not None:
            continue
        finding_id = _next_finding_id(tuple(reserved))
        allocated[index] = finding_id
        if delta.local_ref is not None:
            local_to_concrete[delta.local_ref] = finding_id
        obligation = (
            CorrectionObligation(CorrectionObligationOwner.GATE, hop_id)
            if delta.proposed_state is FindingState.OPEN and delta.blocking
            else None
        )
        reserved.append(
            Finding(
                finding_id=finding_id,
                invariant=delta.invariant,
                blocking=delta.blocking,
                state=delta.proposed_state,
                introduced_by_hop=hop_id,
                changed_by_hop=hop_id,
                evidence_refs=delta.evidence_refs,
                disposition=f"{authority.value}: {delta.proposed_state.value}",
                successor_ref=delta.successor_ref,
                correction_obligation=obligation,
            )
        )

    prospective = list(run.findings)
    by_id = {finding.finding_id: index for index, finding in enumerate(prospective)}
    changed_ids: list[str] = []

    # Pass 3: resolve successors and build the complete prospective ledger.
    def resolved_successor(raw: str | None) -> str | None:
        if raw is None:
            return None
        if raw.startswith("local:"):
            token = raw.removeprefix("local:")
            concrete = local_to_concrete.get(token)
            if concrete is None:
                raise ReductionError(f"unknown local successor reference: {raw}")
            target = reserved[next(
                idx for idx, finding in enumerate(reserved) if finding.finding_id == concrete
            )]
            if (
                target.state is not FindingState.OPEN
                or not target.blocking
                or target.correction_obligation is None
            ):
                raise ReductionError(
                    "local successor must target a same-result open blocking tracked obligation"
                )
            return concrete
        if raw not in originals:
            raise ReductionError(f"unknown concrete successor Finding ID: {raw}")
        return raw

    for index, delta in enumerate(deltas):
        if delta.finding_id is None:
            finding_id = allocated[index]
            staged = next(item for item in reserved if item.finding_id == finding_id)
            staged = replace(staged, successor_ref=resolved_successor(delta.successor_ref))
            prospective.append(staged)
            by_id[finding_id] = len(prospective) - 1
            changed_ids.append(finding_id)
            continue
        previous = prospective[by_id[delta.finding_id]]
        successor_ref = resolved_successor(delta.successor_ref)
        same_state = delta.proposed_state is previous.state
        appended = tuple(
            reference
            for reference in dict.fromkeys(delta.evidence_refs)
            if reference not in previous.evidence_refs
        )
        if same_state and not appended and successor_ref == previous.successor_ref:
            continue
        prospective[by_id[delta.finding_id]] = replace(
            previous,
            blocking=(
                False if delta.proposed_state is FindingState.RESIDUAL else previous.blocking
            ),
            state=delta.proposed_state,
            changed_by_hop=hop_id,
            evidence_refs=(*previous.evidence_refs, *appended),
            disposition=(
                previous.disposition
                if same_state
                else f"{authority.value}: {delta.proposed_state.value}"
            ),
            successor_ref=successor_ref,
        )
        changed_ids.append(delta.finding_id)

    prospective_by_id = {finding.finding_id: finding for finding in prospective}
    for finding in prospective:
        if finding.state is FindingState.OPEN and finding.blocking:
            if finding.correction_obligation is None:
                raise ReductionError(
                    f"open blocking Finding {finding.finding_id} is not a tracked obligation"
                )
        if finding.successor_ref is not None:
            successor = prospective_by_id.get(finding.successor_ref)
            if successor is None:
                raise ReductionError(f"unknown successor Finding ID: {finding.successor_ref}")
            if (
                finding.correction_obligation is not None
                and successor.correction_obligation is None
            ):
                raise ReductionError("tracked obligation successor must also be tracked")

    edges = {
        finding.finding_id: finding.successor_ref
        for finding in prospective
        if finding.successor_ref is not None
    }
    for start in edges:
        seen: set[str] = set()
        cursor: str | None = start
        while cursor in edges:
            if cursor in seen:
                raise ReductionError("Finding supersession graph contains a cycle")
            seen.add(cursor)
            cursor = edges[cursor]

    return replace(run, findings=tuple(prospective)), tuple(changed_ids)


def _has_open_blocker(run: Run) -> bool:
    return any(
        finding.state is FindingState.OPEN and finding.blocking
        for finding in run.findings
    )


def _apply_route(run: Run, route_key: str, route: Route) -> Reduction:
    if route.effect is RouteEffect.GOTO:
        return Reduction(
            run=replace(
                run,
                phase=route.target or run.phase,
                status=RunStatus.READY,
                stop_reason=None,
                human_question=None,
            ),
            route_key=route_key,
            autonomous_route=True,
            supervisor_required=False,
        )
    if route.effect is RouteEffect.COMPLETE:
        if _has_open_blocker(run):
            return _stop_for_human(run, "completion_blocked_by_open_blocking_findings")
        return Reduction(
            run=replace(run, status=RunStatus.COMPLETED, stop_reason=None, human_question=None),
            route_key=route_key,
            autonomous_route=True,
            supervisor_required=False,
        )
    if route.effect is RouteEffect.AWAIT_HUMAN:
        return _stop_for_human(run, f"route:{route_key}", route.question)
    if route.effect is RouteEffect.SUSPEND_FOR_PREREQUISITE:
        return _stop_for_human(
            run,
            "prerequisite_requires_explicit_human_authorization",
            "Authorize and scope a separate prerequisite Run?",
        )
    return Reduction(
        run=replace(run, status=RunStatus.STOPPED, stop_reason=f"route:{route_key}"),
        route_key=route_key,
        autonomous_route=False,
        supervisor_required=False,
    )


def _escape_trigger_ids(hop) -> tuple[str, ...] | None:
    if not hop.route_reason or not hop.route_reason.startswith("convergence_escape:"):
        return None
    values = tuple(hop.route_reason.partition(":")[2].split(","))
    return values if values and all(values) else None


def _validate_strategy_chain(run: Run, result: RoutingResult, role: ConvergenceRole) -> None:
    """Validate Slice-4's single active escape chain before any state changes."""

    accepted_escapes = {
        hop.strategy_assessment.escape_hop_id
        for hop in run.hops
        if hop.strategy_assessment is not None
        and hop.strategy_assessment.result is StrategyAssessmentResult.ACCEPTED
    }
    escapes = [(hop, _escape_trigger_ids(hop)) for hop in run.hops]
    active = [(hop, ids) for hop, ids in escapes if ids is not None and hop.hop_id not in accepted_escapes]
    if len(active) != 1:
        raise ReductionError("strategy record requires exactly one unconsumed escape")
    escape, trigger_ids = active[0]
    assert trigger_ids is not None
    if tuple(sorted(trigger_ids)) != trigger_ids or len(set(trigger_ids)) != len(trigger_ids):
        raise ReductionError("escape trigger set is not canonical")
    open_ids = {
        finding.finding_id for finding in run.findings
        if finding.correction_obligation is not None
        and finding.state is FindingState.OPEN and finding.blocking
    }
    if not set(trigger_ids).issubset(open_ids):
        raise ReductionError("escape trigger Finding is no longer open and tracked")
    hop_by_id = {hop.hop_id: hop for hop in run.hops}
    if role is ConvergenceRole.DIAGNOSIS:
        report = result.diagnosis
        assert report is not None
        if report.escape_hop_id != escape.hop_id or report.finding_ids != trigger_ids:
            raise ReductionError("diagnosis does not exactly correlate to the active escape")
        return
    if role is ConvergenceRole.REFRAME:
        revision = result.strategy_revision
        assert revision is not None
        diagnosis = hop_by_id.get(revision.diagnosis_hop_id)
        if (revision.escape_hop_id != escape.hop_id or revision.finding_ids != trigger_ids
                or diagnosis is None or diagnosis.diagnosis is None
                or diagnosis.diagnosis.escape_hop_id != escape.hop_id
                or diagnosis.diagnosis.finding_ids != trigger_ids):
            raise ReductionError("revision does not exactly correlate to a reduced diagnosis")
        return
    assessment = result.strategy_assessment
    assert assessment is not None
    diagnosis = hop_by_id.get(assessment.diagnosis_hop_id)
    revision = hop_by_id.get(assessment.revision_hop_id)
    if (assessment.escape_hop_id != escape.hop_id or assessment.finding_ids != trigger_ids
            or diagnosis is None or diagnosis.diagnosis is None
            or diagnosis.diagnosis.escape_hop_id != escape.hop_id
            or diagnosis.diagnosis.finding_ids != trigger_ids
            or revision is None or revision.strategy_revision is None
            or revision.strategy_revision.escape_hop_id != escape.hop_id
            or revision.strategy_revision.diagnosis_hop_id != diagnosis.hop_id
            or revision.strategy_revision.finding_ids != trigger_ids):
        raise ReductionError("strategy assessment does not exactly correlate to the active chain")
    revisions = [hop for hop in run.hops if hop.strategy_revision is not None
                 and hop.strategy_revision.escape_hop_id == escape.hop_id
                 and hop.strategy_revision.diagnosis_hop_id == diagnosis.hop_id]
    if not revisions or revisions[-1].hop_id != revision.hop_id:
        raise ReductionError("strategy assessment must reference the latest revision")


def _validate_convergence_records_before_apply(
    run: Run,
    result: RoutingResult,
    *,
    phase,
    route: Route,
) -> None:
    role = phase.convergence_role
    typed = (result.diagnosis, result.strategy_revision, result.strategy_assessment)
    if role is ConvergenceRole.NONE:
        if any(item is not None for item in typed):
            raise ReductionError("ordinary phase cannot submit strategy records")
        if result.coverage_claims or result.convergence_assessments:
            raise ReductionError("ordinary phase cannot submit convergence records")
        if run.schema_version == 3 and any(
            isinstance(delta, NewFindingDelta)
            and delta.proposed_state is FindingState.OPEN
            and delta.blocking
            for delta in result.findings
        ):
            raise ReductionError(
                "Run v3 ordinary phase cannot create an untracked open blocker"
            )
        return
    if run.schema_version != 3:
        raise ReductionError("result v2 convergence reduction requires Run schema_version 3")

    tracked_open = {
        finding.finding_id: finding
        for finding in run.findings
        if (
            finding.correction_obligation is not None
            and finding.state is FindingState.OPEN
            and finding.blocking
        )
    }
    if role is ConvergenceRole.CORRECTOR:
        if any(item is not None for item in typed):
            raise ReductionError("corrector cannot submit strategy records")
        if result.convergence_assessments:
            raise ReductionError("corrector cannot submit convergence assessments")
        claim_ids = [claim.finding_id for claim in result.coverage_claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ReductionError("duplicate coverage claim")
        for claim in result.coverage_claims:
            finding = next(
                (item for item in run.findings if item.finding_id == claim.finding_id),
                None,
            )
            if (
                finding is None
                or finding.correction_obligation is None
                or finding.state is not FindingState.OPEN
                or not finding.blocking
            ):
                raise ReductionError(
                    f"coverage references unknown, closed, superseded, or untracked Finding: {claim.finding_id}"
                )
            if (
                claim.disposition is CoverageDisposition.BLOCKED
                and result.outcome is not Outcome.BLOCKED
            ):
                raise ReductionError("blocked coverage requires outcome blocked")
            if claim.disposition is CoverageDisposition.ESCALATION and not (
                result.requires_human and result.escalation is not None
            ):
                raise ReductionError("escalation coverage requires a result escalation")
            if (
                claim.disposition is CoverageDisposition.ADDRESSED
                and route.effect is RouteEffect.COMPLETE
            ):
                raise ReductionError("addressed coverage cannot take a complete route")
        if set(claim_ids) != set(tracked_open):
            raise ReductionError("coverage claims must cover every open tracked obligation exactly once")
        return

    if role in {
        ConvergenceRole.DIAGNOSIS,
        ConvergenceRole.REFRAME,
        ConvergenceRole.STRATEGY_GATE,
    }:
        if result.coverage_claims or result.convergence_assessments:
            raise ReductionError(
                f"{role.value} cannot submit Slice-1 convergence records"
            )
        expected = {
            ConvergenceRole.DIAGNOSIS: (True, False, False),
            ConvergenceRole.REFRAME: (False, True, False),
            ConvergenceRole.STRATEGY_GATE: (False, False, True),
        }[role]
        present = tuple(item is not None for item in typed)
        if present != expected:
            raise ReductionError(f"{role.value} requires exactly its typed strategy record")
        _validate_strategy_chain(run, result, role)
        if result.findings:
            raise ReductionError(f"{role.value} result cannot mutate Findings")
        if role is ConvergenceRole.STRATEGY_GATE:
            assessment = result.strategy_assessment
            assert assessment is not None
            if route.effect is RouteEffect.COMPLETE:
                raise ReductionError("strategy gate cannot complete the Run")
            if assessment.result is StrategyAssessmentResult.ACCEPTED and result.outcome is not Outcome.COMPLETED:
                raise ReductionError("accepted strategy assessment requires accepted outcome")
            if assessment.result is StrategyAssessmentResult.CHANGES_REQUIRED and result.outcome is not Outcome.CHANGES_REQUIRED:
                raise ReductionError("changes_required strategy assessment requires changes_required outcome")
        return

    if result.coverage_claims:
        raise ReductionError("evaluator cannot submit coverage claims")
    if any(item is not None for item in typed):
        raise ReductionError("evaluator cannot submit strategy records")
    if phase.review is None or phase.review.authority is not ReviewAuthority.GATE:
        raise ReductionError("evaluator must be a gate review")
    if "reopen" in phase.review.finding_authority:
        raise ReductionError("evaluator result v2 cannot advertise reopen authority")
    assessment_ids = [item.finding_id for item in result.convergence_assessments]
    if len(set(assessment_ids)) != len(assessment_ids):
        raise ReductionError("duplicate convergence assessment")
    prior_hops = run.hops[:-1]
    assessed_correction_hop_ids = {
        assessment.correction_hop_id
        for hop in prior_hops
        for assessment in hop.convergence_assessments
    }
    requested_correction_hop_ids = {
        assessment.correction_hop_id
        for assessment in result.convergence_assessments
    }
    reused_correction_hop_ids = (
        requested_correction_hop_ids & assessed_correction_hop_ids
    )
    if reused_correction_hop_ids:
        reused = ", ".join(sorted(reused_correction_hop_ids))
        raise ReductionError(f"correction Hop already assessed: {reused}")
    unassessed_correction_hops = [
        hop
        for hop in prior_hops
        if (
            hop.coverage_claims
            and hop.hop_id not in assessed_correction_hop_ids
        )
    ]
    expected_correction = (
        unassessed_correction_hops[-1] if unassessed_correction_hops else None
    )
    if expected_correction is not None:
        expected_ids = {claim.finding_id for claim in expected_correction.coverage_claims}
        if set(assessment_ids) != expected_ids:
            raise ReductionError("convergence assessments must be total for the correction Hop")
    elif assessment_ids:
        raise ReductionError("convergence assessment references no recorded correction Hop")
    delta_by_id = {
        delta.finding_id: delta
        for delta in result.findings
        if isinstance(delta, ExistingFindingDelta)
    }
    for assessment in result.convergence_assessments:
        if (
            expected_correction is None
            or assessment.correction_hop_id != expected_correction.hop_id
        ):
            raise ReductionError("assessment correction_hop_id does not match correction coverage")
        finding = tracked_open.get(assessment.finding_id)
        if finding is None:
            raise ReductionError(
                f"assessment references unknown, closed, or superseded Finding: {assessment.finding_id}"
            )
        if assessment.finding_id not in delta_by_id:
            raise ReductionError("assessment requires a matching Finding delta")
    assessed = set(assessment_ids)
    for delta in result.findings:
        if (
            isinstance(delta, ExistingFindingDelta)
            and delta.finding_id in tracked_open
            and delta.finding_id not in assessed
        ):
            raise ReductionError("tracked evaluator Finding delta requires an assessment")


def _validate_assessments_after_apply(
    before: Run, after: Run, result: RoutingResult
) -> None:
    before_by_id = {finding.finding_id: finding for finding in before.findings}
    after_by_id = {finding.finding_id: finding for finding in after.findings}
    delta_by_id = {
        delta.finding_id: delta
        for delta in result.findings
        if isinstance(delta, ExistingFindingDelta)
    }
    expected_states = {
        ConvergenceAssessmentResult.CLOSED: FindingState.RESOLVED,
        ConvergenceAssessmentResult.RESIDUALIZED: FindingState.RESIDUAL,
        ConvergenceAssessmentResult.SUPERSEDED: FindingState.SUPERSEDED,
        ConvergenceAssessmentResult.CARRIED_NEW_COUNTEREXAMPLE: FindingState.OPEN,
        ConvergenceAssessmentResult.CARRIED_UNMET: FindingState.OPEN,
    }
    for assessment in result.convergence_assessments:
        delta = delta_by_id[assessment.finding_id]
        after_finding = after_by_id[assessment.finding_id]
        expected = expected_states[assessment.result]
        if delta.proposed_state is not expected or after_finding.state is not expected:
            raise ReductionError(
                f"{assessment.result.value} assessment does not match Finding delta"
            )
        if assessment.result in {
            ConvergenceAssessmentResult.CARRIED_NEW_COUNTEREXAMPLE,
            ConvergenceAssessmentResult.CARRIED_UNMET,
        } and not after_finding.blocking:
            raise ReductionError("carried assessment must leave Finding open and blocking")
        if (
            assessment.result is ConvergenceAssessmentResult.RESIDUALIZED
            and after_finding.blocking
        ):
            raise ReductionError("residualized assessment must leave Finding non-blocking")
        if assessment.result is ConvergenceAssessmentResult.SUPERSEDED:
            if after_finding.successor_ref is None:
                raise ReductionError("superseded assessment requires a valid successor")
        if assessment.result is ConvergenceAssessmentResult.CARRIED_NEW_COUNTEREXAMPLE:
            previous_refs = set(before_by_id[assessment.finding_id].evidence_refs)
            for hop in before.hops:
                previous_refs.update(
                    evidence_ref
                    for claim in hop.coverage_claims
                    if claim.finding_id == assessment.finding_id
                    for evidence_ref in claim.evidence_refs
                )
                previous_refs.update(
                    evidence_ref
                    for prior_assessment in hop.convergence_assessments
                    if prior_assessment.finding_id == assessment.finding_id
                    for evidence_ref in prior_assessment.evidence_refs
                )
            new_refs = set(assessment.evidence_refs) - previous_refs
            if not new_refs or not new_refs.issubset(set(delta.evidence_refs)):
                raise ReductionError(
                    "carried_new_counterexample requires new Finding evidence"
                )


def reduce_result(
    run: Run,
    result: RoutingResult,
    profile: WorkflowProfile,
    *,
    hop_id: str,
    facts: MechanicalFacts = MechanicalFacts(),
    trusted_qualification_index: TrustedQualificationIndex | None = None,
) -> Reduction:
    """Apply mandatory preemption before any Finding or route authority."""

    route_reason: str | None = None

    def finish(reduction: Reduction) -> Reduction:
        return _with_hop_decision(
            reduction,
            hop_id,
            requested_route=result.requested_route,
            route_reason=route_reason,
        )

    phase, profile_error = _profile_phase(run, profile)
    correlated_hop = bool(run.hops and run.hops[-1].hop_id == hop_id)
    result_state_error = None
    expected_result_version = (
        2
        if profile.schema_version == 3
        or (
            phase is not None
            and phase.convergence_role is not ConvergenceRole.NONE
        )
        else 1
    )
    if result.schema_version != expected_result_version:
        result_state_error = "unsupported result schema version"
    elif run.status is not RunStatus.RUNNING or not correlated_hop:
        result_state_error = "result is not correlated to the current accepted Hop"
    elif (
        profile.schema_version == 3
        and phase is not None
        and phase.convergence_role is ConvergenceRole.EVALUATOR
        and trusted_qualification_index is None
    ):
        result_state_error = "current evaluator lacks trusted qualification input"
    elif trusted_qualification_index is not None and run.schema_version == 3:
        current_hop = run.hops[-1]
        expected_identity = (
            run.run_id,
            run.work_item_id,
            run.profile_id,
            run.profile_revision,
        )
        actual_identity = (
            None
            if trusted_qualification_index.run is None
            else (
                trusted_qualification_index.run.run_id,
                trusted_qualification_index.run.work_item_id,
                trusted_qualification_index.run.profile_id,
                trusted_qualification_index.run.profile_revision,
            )
        )
        matching = tuple(
            attempt
            for attempt in trusted_qualification_index.attempts
            if attempt.hop_id == hop_id
            and attempt.hop_sequence == current_hop.sequence
            and attempt.pre_route_anchor_sha256
            == current_hop.pre_route_anchor_sha256
        )
        if actual_identity != expected_identity:
            result_state_error = "trusted qualification index identity mismatch"
        elif len(matching) != 1:
            result_state_error = (
                "current Hop lacks one exact staged qualification attempt"
            )
    # Capture the correlated semantic observation *before* selecting the
    # controlling mechanical stop.  It is observation only: no route, Finding,
    # commit, or scope authority flows from it.
    observed_run = run
    if result_state_error is None and (result.requires_human or result.escalation is not None):
        question = result.escalation.question if result.escalation else "Agent requested human authority."
        kind = result.escalation.kind if result.escalation else "operational"
        observed_run = replace(
            run,
            semantic_stop=SemanticStop(kind=kind, question=question, source_hop_id=hop_id),
        )
    mechanical_errors = (
        profile_error,
        result_state_error,
        facts.state_error,
        facts.repository_anomaly,
        facts.policy_anomaly,
        facts.required_evidence_error,
    )
    for error in mechanical_errors:
        if error:
            # A controlling mechanical fact is not merely an explanatory stop
            # string.  It is durable diagnostic state too, so a semantic stop
            # captured above cannot hide the dirty/evidence/policy condition
            # which preempted it.
            diagnosed = replace(
                observed_run,
                anomalies=tuple(dict.fromkeys((*observed_run.anomalies, error))),
            )
            return finish(_stop_for_human(diagnosed, f"mechanical_preemption:{error}"))
    if result.requires_human or result.escalation is not None:
        question = (
            result.escalation.question if result.escalation else "Agent requested human authority."
        )
        suffix = f":{result.escalation.kind}" if result.escalation else ""
        return finish(
            _stop_for_human(observed_run, f"mechanical_preemption:human_authority{suffix}", question)
        )

    # A correlated semantic failure is terminal and discards every proposal.
    # It deliberately precedes scope/route/Finding guards (11.5.2 P5B-C6).
    if result.outcome is Outcome.FAILED:
        return finish(_stop_for_human(run, "result_failed"))

    if result.scope_changed:
        return finish(_stop_for_human(run, "mechanical_preemption:scope_changed"))

    assert phase is not None
    if (
        phase.convergence_role is ConvergenceRole.EVALUATOR
        and result.requested_route == phase.escape_route
    ):
        return finish(
            _stop_for_human(
                run,
                f"mechanical_preemption:reserved_route:{result.requested_route}",
            )
        )
    route = phase.routes.get(result.requested_route)
    if route is None:
        return finish(
            _stop_for_human(
                run,
                f"mechanical_preemption:unknown_route:{result.requested_route}",
            )
        )
    if result.outcome is Outcome.CHANGES_REQUIRED and phase.review is None:
        return finish(
            _stop_for_human(
                run, "mechanical_preemption:changes_required_requires_review"
            )
        )
    if phase.review and phase.review.authority is ReviewAuthority.ADVISORY:
        if route.effect is RouteEffect.COMPLETE:
            return finish(_stop_for_human(run, "advisory_review_cannot_accept_or_complete"))
        authority = FindingAuthority.AGENT
        permitted_operations: tuple[str, ...] = ()
    elif phase.review and phase.review.authority is ReviewAuthority.GATE:
        authority = FindingAuthority.GATE
        permitted_operations = phase.review.finding_authority
    else:
        authority = FindingAuthority.AGENT
        permitted_operations = ()

    try:
        _validate_convergence_records_before_apply(
            run,
            result,
            phase=phase,
            route=route,
        )
        updated, finding_delta_ids = _apply_finding_deltas(
            run,
            result.findings,
            hop_id=hop_id,
            authority=authority,
            permitted_gate_operations=permitted_operations,
            convergence_role=phase.convergence_role,
            result_schema_version=result.schema_version,
        )
        if phase.convergence_role is ConvergenceRole.EVALUATOR:
            _validate_assessments_after_apply(run, updated, result)
    except ReductionError as error:
        return finish(_stop_for_human(run, f"mechanical_preemption:invalid_finding_delta:{error}"))

    updated = _with_convergence_records(updated, hop_id, result)
    updated = replace(
        updated,
        hops=tuple(
            replace(hop, finding_delta_ids=finding_delta_ids)
            if hop.hop_id == hop_id
            else hop
            for hop in updated.hops
        ),
    )

    if result.outcome is Outcome.BLOCKED:
        reduction = _stop_for_human(updated, "result_blocked")
        return finish(_with_finding_delta_ids(reduction, hop_id, finding_delta_ids))

    route_key = result.requested_route
    if facts.required_evidence_failed:
        failure_key = phase.review.failed_evidence_route if phase.review else None
        failure_route = phase.routes.get(failure_key) if failure_key else None
        if (
            phase.review is None
            or phase.review.authority is not ReviewAuthority.GATE
            or failure_route is None
            or failure_route.effect is not RouteEffect.GOTO
        ):
            reduction = _stop_for_human(
                updated, "required_evidence_failed_without_route"
            )
            return finish(_with_finding_delta_ids(reduction, hop_id, finding_delta_ids))
        route_key = failure_key
        route = failure_route

    if (
        profile.schema_version == 3
        and profile.convergence is not None
        and phase.convergence_role is ConvergenceRole.EVALUATOR
        and result.outcome is Outcome.CHANGES_REQUIRED
        and result.requested_route == phase.normal_correction_route
        and route_key == result.requested_route
        and not facts.required_evidence_failed
    ):
        assert trusted_qualification_index is not None
        try:
            convergence_facts = derive_convergence_facts(
                updated,
                trusted_qualification_index,
                profile.convergence.qualified_cycle_limit,
            )
        except (ReductionError, ValueError) as error:
            reduction = _stop_for_human(
                updated,
                f"mechanical_preemption:invalid_qualification_input:{error}",
            )
            return finish(
                _with_finding_delta_ids(reduction, hop_id, finding_delta_ids)
            )
        triggering_ids = tuple(
            fact.finding_id
            for fact in convergence_facts
            if fact.goalpost_reached
        )
        if triggering_ids:
            escape_key = phase.escape_route
            escape = phase.routes.get(escape_key or "")
            if escape_key is None or escape is None:
                reduction = _stop_for_human(
                    updated, "mechanical_preemption:invalid_convergence_profile"
                )
                return finish(
                    _with_finding_delta_ids(reduction, hop_id, finding_delta_ids)
                )
            route_key = escape_key
            route = escape
            route_reason = "convergence_escape:" + ",".join(triggering_ids)

    if result.outcome is Outcome.CHANGES_REQUIRED and route.effect is RouteEffect.COMPLETE:
        reduction = _stop_for_human(updated, "changes_required_cannot_complete")
        return finish(_with_finding_delta_ids(reduction, hop_id, finding_delta_ids))

    invocation_needed = route.supervisor or route.effect is RouteEffect.GOTO
    if invocation_needed and updated.hop_used >= updated.hop_limit:
        reduction = finish(
            Reduction(
                run=replace(
                    updated,
                    status=RunStatus.AWAITING_HUMAN_BUDGET,
                    stop_reason="autonomous_hop_budget_exhausted",
                ),
                route_key=None,
                autonomous_route=False,
                supervisor_required=False,
            )
        )
        return _with_finding_delta_ids(reduction, hop_id, finding_delta_ids)
    if route.supervisor:
        reduction = finish(
            Reduction(
                run=replace(
                    updated,
                    status=RunStatus.AWAITING_SUPERVISOR,
                    stop_reason="supervisor_judgment_required",
                ),
                route_key=None,
                autonomous_route=False,
                supervisor_required=True,
            )
        )
        return _with_finding_delta_ids(reduction, hop_id, finding_delta_ids)
    reduction = finish(_apply_route(updated, route_key, route))
    return _with_finding_delta_ids(reduction, hop_id, finding_delta_ids)


def _with_finding_delta_ids(
    reduction: Reduction, hop_id: str, finding_delta_ids: tuple[str, ...]
) -> Reduction:
    updated_hops = tuple(
        replace(hop, finding_delta_ids=finding_delta_ids)
        if hop.hop_id == hop_id
        else hop
        for hop in reduction.run.hops
    )
    return replace(reduction, run=replace(reduction.run, hops=updated_hops))


def _with_convergence_records(run: Run, hop_id: str, result: RoutingResult) -> Run:
    if result.schema_version != 2:
        return run
    return replace(
        run,
        hops=tuple(
            replace(
                hop,
                coverage_claims=result.coverage_claims,
                convergence_assessments=result.convergence_assessments,
                diagnosis=result.diagnosis,
                strategy_revision=result.strategy_revision,
                strategy_assessment=result.strategy_assessment,
            )
            if hop.hop_id == hop_id
            else hop
            for hop in run.hops
        ),
    )


def confirm_supervisor_route(run: Run, profile: WorkflowProfile, route_key: str) -> Reduction:
    """Confirm one already-authorized route; no Finding mutation is accepted here."""

    recorded_supervisor = bool(run.hops and run.hops[-1].attribution == "supervisor")
    if run.status is not RunStatus.AWAITING_SUPERVISOR or not recorded_supervisor:
        raise ReductionError("Run is not awaiting supervisor judgment")
    phase, error = _profile_phase(run, profile)
    if error:
        return _stop_for_human(run, f"mechanical_preemption:{error}")
    assert phase is not None
    route = phase.routes.get(route_key)
    if route is None or not route.supervisor:
        return _stop_for_human(run, f"supervisor selected unauthorized route:{route_key}")
    if route.effect is RouteEffect.GOTO and run.hop_used >= run.hop_limit:
        return Reduction(
            run=replace(
                run,
                status=RunStatus.AWAITING_HUMAN_BUDGET,
                stop_reason="autonomous_hop_budget_exhausted",
            ),
            route_key=None,
            autonomous_route=False,
            supervisor_required=False,
        )
    return _apply_route(run, route_key, route)


def authorize_prerequisite(run: Run, *, child_run_id: str) -> Run:
    if run.status is not RunStatus.AWAITING_HUMAN:
        raise ReductionError("prerequisite authorization requires an awaiting-human Run")
    prerequisite_reasons = {
        "prerequisite_requires_explicit_human_authorization",
        "mechanical_preemption:human_authority:prerequisite",
    }
    if run.stop_reason not in prerequisite_reasons:
        raise ReductionError("Run is not stopped for prerequisite authorization")
    return replace(
        run,
        status=RunStatus.SUSPENDED,
        child_run_id=child_run_id,
        suspension_head=run.current_head,
        stop_reason="suspended_for_prerequisite",
        human_question=None,
    )


def require_reconcile(
    run: Run,
    *,
    reason: str,
    current_head: str,
    reality_error: str | None = None,
) -> Run:
    """Use the same current-reality boundary for interruption and child return."""

    if reason not in {"interruption", "prerequisite_return"}:
        raise ReductionError(f"unknown reconcile reason: {reason}")
    allowed_statuses = {
        "interruption": {
            RunStatus.READY,
            RunStatus.RUNNING,
            RunStatus.AWAITING_SUPERVISOR,
        },
        "prerequisite_return": {RunStatus.SUSPENDED},
    }
    idempotent_reentry = (
        run.status is RunStatus.RECONCILE_REQUIRED and run.reconcile_reason == reason
    )
    if run.status not in allowed_statuses[reason] and not idempotent_reentry:
        raise ReductionError(
            f"{reason} reconciliation is invalid from Run status {run.status.value}"
        )
    anomalies = run.anomalies
    if reality_error:
        anomalies = (*anomalies, reality_error)
    anchor = run.suspension_head if reason == "prerequisite_return" else run.current_head
    return replace(
        run,
        current_head=current_head,
        status=RunStatus.RECONCILE_REQUIRED,
        reconcile_reason=reason,
        reconcile_anchor=anchor,
        stop_reason="reconcile_requires_explicit_resume",
        human_question=(
            run.human_question or "Validate current reality and explicitly authorize resume."
        ),
        anomalies=anomalies,
    )


def resume_after_reconcile(run: Run, profile: WorkflowProfile, *, current_head: str) -> Run:
    if run.status is not RunStatus.RECONCILE_REQUIRED:
        raise ReductionError("Run does not require reconciliation")
    _, error = _profile_phase(run, profile)
    if error:
        raise ReductionError(error)
    if run.anomalies:
        raise ReductionError("reconcile anomalies must be resolved before resume")
    if current_head != run.current_head:
        raise ReductionError("current HEAD changed after reconciliation")
    if run.hop_used > run.hop_limit:
        raise ReductionError("Hop limit cannot be below used Hops")
    return replace(
        run,
        status=RunStatus.READY,
        stop_reason=None,
        human_question=None,
    )


def permitted_route_keys(run: Run, profile: WorkflowProfile) -> tuple[str, ...]:
    """Derive finite choices; route authority is never read from Run state."""

    phase, error = _profile_phase(run, profile)
    if error or phase is None:
        return ()
    paused_or_terminal = {
        RunStatus.AWAITING_HUMAN,
        RunStatus.AWAITING_HUMAN_BUDGET,
        RunStatus.RECONCILE_REQUIRED,
        RunStatus.SUSPENDED,
        RunStatus.COMPLETED,
        RunStatus.STOPPED,
    }
    if run.status in paused_or_terminal:
        return ()
    if run.status is RunStatus.AWAITING_SUPERVISOR:
        return tuple(sorted(key for key, route in phase.routes.items() if route.supervisor))
    reserved = (
        {phase.escape_route}
        if phase.convergence_role is ConvergenceRole.EVALUATOR
        and phase.escape_route is not None
        else set()
    )
    return tuple(sorted(key for key in phase.routes if key not in reserved))
