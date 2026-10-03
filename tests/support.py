"""Concise typed fixtures for coordinator reducer and replay tests."""

from __future__ import annotations

from dataclasses import replace
import hashlib

from workflow_coordinator.model import (
    ConvergenceAssessment,
    ConvergenceAssessmentResult,
    ConvergenceRole,
    CoverageClaim,
    CoverageDisposition,
    DurableArtifact,
    EvidenceCommand,
    ExecutionEvidenceDocument,
    ExecutionEvidenceRunIdentity,
    Escalation,
    ExistingFindingDelta,
    FindingDeltaValue,
    FindingState,
    Hop,
    Observation,
    Outcome,
    NewFindingDelta,
    Phase,
    RepositoryPolicy,
    RequiredEvidenceIdentity,
    ReviewAuthority,
    ReviewDescriptor,
    Route,
    RouteEffect,
    RoutingResult,
    Run,
    StagedQualificationAttempt,
    StatFingerprint,
    TrustedQualificationIndex,
    WorkItem,
    WorkflowProfile,
    WorkflowRole,
    new_run,
    record_hop,
)
from workflow_coordinator.evidence import (
    CWD_POLICY,
    ENVIRONMENT_POLICY,
    PreparedEvidenceCommand,
    prepare_evidence_commands,
)
from workflow_coordinator.reducer import reduce_result
from workflow_coordinator.persistence import trusted_qualification_index_for_run
from workflow_coordinator.serialization import (
    execution_evidence_from_json,
    execution_evidence_to_json,
    pre_route_anchor_sha256,
)


def route(effect: str, target: str | None = None, *, supervisor: bool = False) -> Route:
    return Route(effect=RouteEffect(effect), target=target, supervisor=supervisor)


def phase(
    routes: dict[str, Route],
    *,
    role: str = "worker",
    review: str | None = None,
    finding_authority: tuple[str, ...] = (),
    breadth: str = "focused",
    convergence_role: str = "none",
) -> Phase:
    descriptor = None
    if review is not None:
        descriptor = ReviewDescriptor(
            purpose="independent falsification",
            breadth=breadth,
            authority=ReviewAuthority(review),
            context="fresh",
            blocking_policy="blocking_only",
            trigger="profile_selected",
            exit="declared_route",
            finding_authority=finding_authority,
        )
    return Phase(
        role=WorkflowRole(role),
        role_components=(role,),
        modifier_components=(),
        repository_policy_components=("testing",),
        durable_artifact=DurableArtifact.NONE,
        authorized_scope=("repo",),
        authorized_actions=("edit", "commit"),
        human_gates=(),
        evidence=(),
        review=descriptor,
        routes=routes,
        convergence_role=ConvergenceRole(convergence_role),
    )


def profile(phases: dict[str, Phase], *, profile_id: str = "fixture") -> WorkflowProfile:
    return WorkflowProfile(
        schema_version=2, profile_id=profile_id, revision="profile-r1", phases=phases
    )


def run_for(
    workflow: WorkflowProfile,
    *,
    initial_phase: str | None = None,
    budget: int = 5,
    run_schema_version: int = 2,
) -> Run:
    selected_phase = initial_phase or next(iter(workflow.phases))
    item = WorkItem(
        schema_version=2,
        work_item_id="WI-1",
        authority_source="accepted fixture",
        profile_ref="fixture.yaml",
        repository="repo",
        scope=("repo",),
        initial_phase=selected_phase,
        max_autonomous_hops=budget,
        personas={
            WorkflowRole.WORKER: "fixture worker",
            WorkflowRole.INDEPENDENT_REVIEWER: "fixture independent reviewer",
        },
    )
    policy = RepositoryPolicy(
        schema_version=3,
        policy_id="policy",
        revision="policy-r1",
        scope=("repo",),
        governance_paths=("governance",),
        work_item_scope_ceiling=item.scope,
        max_autonomous_hops=item.max_autonomous_hops,
        actions=("edit", "commit"),
        evidence_commands={"tests": EvidenceCommand(("git", "--version"), 10)},
    )
    run = new_run(item, workflow, policy, run_id="RUN-1", baseline_head="head-0")
    return replace(run, schema_version=run_schema_version)


def charge_hop(
    run: Run,
    *,
    role: str = "worker",
    attribution: str = "agent",
    end_head: str | None = None,
    finding_delta_ids: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = ("tests",),
) -> tuple[Run, str]:
    sequence = run.hop_used + 1
    hop_id = f"H{sequence:03d}"
    end = end_head or f"head-{sequence}"
    hop = Hop(
        hop_id=hop_id,
        sequence=sequence,
        phase=run.phase,
        role=role,
        context="fresh",
        started_at=f"t{sequence}",
        ended_at=f"t{sequence}-done",
        base_head=run.current_head,
        end_head=end,
        actual_commits=(end,) if end != run.current_head else (),
        invocation_status="accepted",
        outcome="completed",
        finding_delta_ids=finding_delta_ids,
        evidence_refs=evidence_refs,
        applied_route=None,
        stop_reason=None,
        summary=f"{role} fixture Hop",
        attribution=attribution,
    )
    return record_hop(run, hop), hop_id


def result(
    route_key: str,
    *,
    outcome: str = "completed",
    findings: tuple[FindingDeltaValue, ...] = (),
    requires_human: bool = False,
    escalation: Escalation | None = None,
    scope_changed: bool = False,
    schema_version: int = 1,
    coverage_claims: tuple[CoverageClaim, ...] = (),
    convergence_assessments: tuple[ConvergenceAssessment, ...] = (),
) -> RoutingResult:
    return RoutingResult(
        schema_version=schema_version,
        outcome=Outcome(outcome),
        requested_route=route_key,
        scope_changed=scope_changed,
        requires_human=requires_human,
        escalation=escalation,
        findings=findings,
        claimed_commits=(),
        commit_intent=None,
        evidence_refs=("tests",),
        summary="fixture decision",
        coverage_claims=coverage_claims,
        convergence_assessments=convergence_assessments,
    )


def new_delta(
    state: str,
    *,
    invariant: str = "The accepted invariant must hold.",
    blocking: bool = True,
    evidence: str = "evidence-1",
    successor_ref: str | None = None,
    new_material: bool = False,
    local_ref: str | None = None,
) -> NewFindingDelta:
    return NewFindingDelta(
        finding_id=None,
        invariant=invariant,
        blocking=blocking,
        proposed_state=FindingState(state),
        evidence_refs=(evidence,),
        successor_ref=successor_ref,
        new_material_evidence=new_material,
        local_ref=local_ref,
    )


def coverage(
    finding_id: str,
    disposition: str = "addressed",
    *evidence_refs: str,
) -> CoverageClaim:
    return CoverageClaim(
        finding_id,
        CoverageDisposition(disposition),
        evidence_refs or ("coverage-evidence",),
    )


def assessment(
    finding_id: str,
    correction_hop_id: str,
    assessment_result: str,
    *evidence_refs: str,
) -> ConvergenceAssessment:
    return ConvergenceAssessment(
        finding_id,
        correction_hop_id,
        ConvergenceAssessmentResult(assessment_result),
        evidence_refs or ("assessment-evidence",),
    )


def existing_delta(
    finding_id: str,
    state: str,
    *,
    evidence: str = "evidence-1",
    successor_ref: str | None = None,
    new_material: bool = False,
) -> ExistingFindingDelta:
    return ExistingFindingDelta(
        finding_id=finding_id,
        proposed_state=FindingState(state),
        evidence_refs=(evidence,),
        successor_ref=successor_ref,
        new_material_evidence=new_material,
    )


def convergence_fixture_profile() -> WorkflowProfile:
    """Return the in-memory-only Slice-1 topology used by Slice-2 tests."""

    return profile(
        {
            "evaluate": phase(
                {
                    "accept": route("complete"),
                    "correct": route("goto", "correct"),
                },
                role="independent_reviewer",
                review="gate",
                finding_authority=("resolve", "residual", "supersede"),
                convergence_role="evaluator",
            ),
            "correct": phase(
                {"evaluate": route("goto", "evaluate")},
                convergence_role="corrector",
            ),
        }
    )


def tracked_convergence_run(
    workflow: WorkflowProfile | None = None,
    *,
    budget: int = 8,
) -> tuple[WorkflowProfile, Run]:
    """Introduce one accepted gate-owned obligation without profile-v3 YAML."""

    selected = workflow or convergence_fixture_profile()
    run, hop_id = charge_hop(
        run_for(
            selected,
            initial_phase="evaluate",
            budget=budget,
            run_schema_version=3,
        ),
        role="independent_reviewer",
    )
    introduced = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(new_delta("open"),),
        ),
        selected,
        hop_id=hop_id,
    )
    return selected, introduced.run


def convergence_cycle(
    run: Run,
    workflow: WorkflowProfile,
    *,
    assessment_result: str = "carried_new_counterexample",
    evidence_prefix: str = "cycle",
    new_findings: tuple[NewFindingDelta, ...] = (),
) -> tuple[Run, str, str]:
    """Reduce one accepted corrector/evaluator cycle for every open obligation."""

    tracked_ids = tuple(
        finding.finding_id
        for finding in run.findings
        if (
            finding.correction_obligation is not None
            and finding.state is FindingState.OPEN
            and finding.blocking
        )
    )
    corrected, correction_hop_id = charge_hop(run)
    corrected = reduce_result(
        corrected,
        result(
            "evaluate",
            schema_version=2,
            coverage_claims=tuple(
                coverage(
                    finding_id,
                    "addressed",
                    f"{evidence_prefix}-coverage-{finding_id}",
                )
                for finding_id in tracked_ids
            ),
        ),
        workflow,
        hop_id=correction_hop_id,
    ).run
    evaluated, evaluator_hop_id = charge_hop(
        corrected, role="independent_reviewer"
    )
    proposed_state = "resolved" if assessment_result == "closed" else "open"
    evidence_by_id = {
        finding_id: f"{evidence_prefix}-evaluation-{finding_id}"
        for finding_id in tracked_ids
    }
    evaluated = reduce_result(
        evaluated,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(
                *(
                    existing_delta(
                        finding_id,
                        proposed_state,
                        evidence=evidence_by_id[finding_id],
                    )
                    for finding_id in tracked_ids
                ),
                *new_findings,
            ),
            convergence_assessments=tuple(
                assessment(
                    finding_id,
                    correction_hop_id,
                    assessment_result,
                    evidence_by_id[finding_id],
                )
                for finding_id in tracked_ids
            ),
        ),
        workflow,
        hop_id=evaluator_hop_id,
    ).run
    return evaluated, correction_hop_id, evaluator_hop_id


def with_phase(run: Run, phase_key: str) -> Run:
    return replace(run, phase=phase_key)


def prepared_evidence(
    repository,
    names: tuple[str, ...] = ("tests",),
) -> dict[str, PreparedEvidenceCommand]:
    definitions = {
        name: EvidenceCommand(("git", "--version"), 10) for name in names
    }
    return {
        command.command_name: command
        for command in prepare_evidence_commands(names, definitions, repository)
    }


def simulated_observation(
    command: PreparedEvidenceCommand,
    *,
    status: str = "pass",
    summary: str = "exit 0",
) -> Observation:
    resolution = command.admission_resolution
    empty_digest = hashlib.sha256(b"").hexdigest()
    return Observation(
        provider=command.command_name,
        status=status,
        command_name=command.command_name,
        command=(resolution.invocation_path, *command.authored_argv[1:]),
        summary=summary,
        artifact_ref=None,
        argv_digest=command.argv_digest,
        authored_argv0=command.authored_argv[0],
        timeout_seconds=command.timeout_seconds,
        cwd_policy=CWD_POLICY,
        invocation_path=resolution.invocation_path,
        canonical_path=resolution.canonical_path,
        stat_fingerprint=resolution.stat_fingerprint,
        environment_policy=ENVIRONMENT_POLICY,
        path_source=command.environment.path_source,
        path_digest=command.environment.path_digest,
        exit_code=0 if status == "pass" else 1 if status == "fail" else None,
        signal=None,
        error_class=None if status in {"pass", "fail"} else status,
        stdout_byte_count=0,
        stdout_sha256=empty_digest,
        stdout_body_omitted=False,
        stderr_byte_count=0,
        stderr_sha256=empty_digest,
        stderr_body_omitted=False,
    )


def qualification_evidence(
    run: Run,
    statuses_by_hop: dict[str, tuple[str, ...]],
    *,
    command_names: tuple[str, ...] = ("tests",),
) -> tuple[Run, TrustedQualificationIndex, str]:
    """Build one strictly parsed schema-4 document for focused Slice-2 tests."""

    identity = ExecutionEvidenceRunIdentity(
        run.run_id, run.work_item_id, run.profile_id, run.profile_revision
    )
    plans = tuple(
        RequiredEvidenceIdentity(
            name, hashlib.sha256(name.encode("utf-8")).hexdigest(), 10
        )
        for name in command_names
    )
    attempts: list[StagedQualificationAttempt] = []
    anchors: dict[str, str] = {}
    empty_digest = hashlib.sha256(b"").hexdigest()
    for ordinal, (hop_id, statuses) in enumerate(statuses_by_hop.items(), start=1):
        hop = next(item for item in run.hops if item.hop_id == hop_id)
        observations = tuple(
            Observation(
                provider=plan.command_name,
                status=status,
                command_name=plan.command_name,
                command=("/usr/bin/fixture",),
                summary=status,
                artifact_ref=None,
                argv_digest=plan.argv_digest,
                authored_argv0="fixture",
                timeout_seconds=plan.timeout_seconds,
                cwd_policy=CWD_POLICY,
                invocation_path="/usr/bin/fixture",
                canonical_path="/usr/bin/fixture",
                stat_fingerprint=StatFingerprint(1, 2, 0o100755, 1, 1),
                environment_policy=ENVIRONMENT_POLICY,
                path_source="inherited",
                path_digest=empty_digest,
                exit_code=0 if status == "pass" else 1 if status == "fail" else None,
                signal=None,
                error_class=status if status == "error" else None,
                stdout_byte_count=0,
                stdout_sha256=empty_digest,
                stdout_body_omitted=False,
                stderr_byte_count=0,
                stderr_sha256=empty_digest,
                stderr_body_omitted=False,
            )
            for plan, status in zip(plans, statuses)
        )
        candidate_digest = hashlib.sha256(
            f"candidate:{hop_id}".encode("utf-8")
        ).hexdigest()
        invocation_id = f"INV-fixture-{ordinal}"
        anchor = pre_route_anchor_sha256(
            identity,
            hop_id=hop.hop_id,
            hop_sequence=hop.sequence,
            invocation_id=invocation_id,
            phase="evaluate",
            workflow_role=hop.role,
            outcome=hop.outcome,
            base_head=hop.base_head,
            end_head=hop.end_head,
            actual_commits=hop.actual_commits,
            candidate_result_sha256=candidate_digest,
            required_evidence=plans,
        )
        anchors[hop_id] = anchor
        persona = "fixture evaluator"
        attempts.append(
            StagedQualificationAttempt(
                hop_id=hop.hop_id,
                hop_sequence=hop.sequence,
                invocation_id=invocation_id,
                pre_route_anchor_sha256=anchor,
                phase="evaluate",
                workflow_role=hop.role,
                persona=persona,
                persona_sha256=hashlib.sha256(persona.encode("utf-8")).hexdigest(),
                adapter="fixture",
                executable="fixture",
                model=None,
                reasoning=None,
                context="fresh",
                timeout_seconds=10.0,
                returncode=0,
                timed_out=False,
                candidate_result_present=True,
                candidate_result_sha256=candidate_digest,
                publication="staged",
                outcome=hop.outcome,
                base_head=hop.base_head,
                end_head=hop.end_head,
                actual_commits=hop.actual_commits,
                required_evidence=plans,
                trusted_observations=observations,
            )
        )
    anchored = replace(
        run,
        hops=tuple(
            replace(hop, pre_route_anchor_sha256=anchors.get(hop.hop_id))
            if hop.hop_id in anchors
            else hop
            for hop in run.hops
        ),
    )
    document = ExecutionEvidenceDocument(4, identity, tuple(attempts))
    text = execution_evidence_to_json(document)
    parsed = execution_evidence_from_json(text)
    assert isinstance(parsed, ExecutionEvidenceDocument)
    return anchored, trusted_qualification_index_for_run(anchored, parsed), text
