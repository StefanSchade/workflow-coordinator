"""Explicit workflow reduction: preemption, then finite deterministic routing."""

from __future__ import annotations

from dataclasses import replace

from .model import (
    Finding,
    FindingAuthority,
    FindingDelta,
    FindingState,
    MechanicalFacts,
    Reduction,
    ReviewAuthority,
    Route,
    RouteEffect,
    RoutingResult,
    Run,
    RunStatus,
    WorkflowProfile,
)


class ReductionError(ValueError):
    """State and profile cannot be reduced without guessing."""


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


def _with_hop_decision(reduction: Reduction, hop_id: str) -> Reduction:
    """Correlate the reducer decision back into the compact accepted-Hop record."""

    updated_hops = tuple(
        (
            replace(
                hop,
                applied_route=reduction.route_key,
                stop_reason=reduction.run.stop_reason,
            )
            if hop.hop_id == hop_id
            else hop
        )
        for hop in reduction.run.hops
    )
    return replace(reduction, run=replace(reduction.run, hops=updated_hops))


def _profile_phase(run: Run, profile: WorkflowProfile):
    if run.schema_version != 1 or profile.schema_version != 1:
        return None, "unsupported state/profile schema version"
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
    deltas: tuple[FindingDelta, ...],
    *,
    hop_id: str,
    authority: FindingAuthority,
    permitted_gate_operations: tuple[str, ...] = (),
) -> Run:
    """Apply proposals while keeping authoritative disposition out of supervisor APIs."""

    if authority is FindingAuthority.SUPERVISOR and deltas:
        raise ReductionError("supervisor cannot authoritatively change Finding state")

    findings = list(run.findings)
    by_id = {finding.finding_id: index for index, finding in enumerate(findings)}
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
            continue

        index = by_id.get(delta.finding_id)
        if index is None:
            raise ReductionError(f"unknown Finding ID: {delta.finding_id}")
        previous = findings[index]
        if delta.invariant != previous.invariant:
            raise ReductionError(f"Finding {previous.finding_id} invariant cannot be rewritten")
        if delta.blocking != previous.blocking:
            allowed_downgrade = (
                previous.blocking
                and not delta.blocking
                and delta.proposed_state is FindingState.RESIDUAL
                and _may_dispose(authority, "residual", permitted_gate_operations)
            )
            if not allowed_downgrade:
                raise ReductionError(
                    f"Finding {previous.finding_id} blocking flag change is unauthorized"
                )

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
        if delta.proposed_state is FindingState.RESIDUAL and delta.blocking:
            raise ReductionError("residual Finding must be non-blocking")
        if delta.proposed_state is FindingState.SUPERSEDED and not delta.successor_ref:
            raise ReductionError("superseded Finding requires successor_ref")

        findings[index] = replace(
            previous,
            blocking=delta.blocking,
            state=delta.proposed_state,
            changed_by_hop=hop_id,
            evidence_refs=tuple(dict.fromkeys((*previous.evidence_refs, *delta.evidence_refs))),
            disposition=f"{authority.value}: {delta.proposed_state.value}",
            successor_ref=delta.successor_ref,
        )
    return replace(run, findings=tuple(findings))


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


def reduce_result(
    run: Run,
    result: RoutingResult,
    profile: WorkflowProfile,
    *,
    hop_id: str,
    facts: MechanicalFacts = MechanicalFacts(),
) -> Reduction:
    """Apply mandatory preemption before any Finding or route authority."""

    def finish(reduction: Reduction) -> Reduction:
        return _with_hop_decision(reduction, hop_id)

    phase, profile_error = _profile_phase(run, profile)
    correlated_hop = bool(run.hops and run.hops[-1].hop_id == hop_id)
    result_state_error = None
    if result.schema_version != 1:
        result_state_error = "unsupported result schema version"
    elif run.status is not RunStatus.RUNNING or not correlated_hop:
        result_state_error = "result is not correlated to the current accepted Hop"
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
            return finish(_stop_for_human(run, f"mechanical_preemption:{error}"))
    if result.scope_changed:
        return finish(_stop_for_human(run, "mechanical_preemption:scope_changed"))
    if result.requires_human or result.escalation is not None:
        question = (
            result.escalation.question if result.escalation else "Agent requested human authority."
        )
        suffix = f":{result.escalation.kind}" if result.escalation else ""
        return finish(
            _stop_for_human(run, f"mechanical_preemption:human_authority{suffix}", question)
        )

    assert phase is not None
    route = phase.routes.get(result.requested_route)
    if route is None:
        return finish(
            _stop_for_human(
                run,
                f"mechanical_preemption:unknown_route:{result.requested_route}",
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
        updated = apply_finding_deltas(
            run,
            result.findings,
            hop_id=hop_id,
            authority=authority,
            permitted_gate_operations=permitted_operations,
        )
    except ReductionError as error:
        return finish(_stop_for_human(run, f"mechanical_preemption:invalid_finding_delta:{error}"))

    invocation_needed = route.supervisor or route.effect is RouteEffect.GOTO
    if invocation_needed and updated.hop_used >= updated.hop_limit:
        return finish(
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
    if route.supervisor:
        return finish(
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
    return finish(_apply_route(updated, result.requested_route, route))


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
    return tuple(sorted(phase.routes))
