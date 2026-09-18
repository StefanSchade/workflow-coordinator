"""Concise typed fixtures for coordinator reducer and replay tests."""

from __future__ import annotations

from dataclasses import replace
import hashlib

from scripts.workflow_coordinator.model import (
    DurableArtifact,
    EvidenceCommand,
    Escalation,
    FindingDelta,
    FindingState,
    Hop,
    Observation,
    Outcome,
    Phase,
    RepositoryPolicy,
    ReviewAuthority,
    ReviewDescriptor,
    Route,
    RouteEffect,
    RoutingResult,
    Run,
    WorkItem,
    WorkflowProfile,
    new_run,
    record_hop,
)
from scripts.workflow_coordinator.evidence import (
    CWD_POLICY,
    ENVIRONMENT_POLICY,
    PreparedEvidenceCommand,
    prepare_evidence_commands,
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
        role=role,
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
    )


def profile(phases: dict[str, Phase], *, profile_id: str = "fixture") -> WorkflowProfile:
    return WorkflowProfile(
        schema_version=1, profile_id=profile_id, revision="profile-r1", phases=phases
    )


def run_for(
    workflow: WorkflowProfile,
    *,
    initial_phase: str | None = None,
    budget: int = 5,
) -> Run:
    selected_phase = initial_phase or next(iter(workflow.phases))
    item = WorkItem(
        schema_version=1,
        work_item_id="WI-1",
        authority_source="accepted fixture",
        profile_ref="fixture.yaml",
        repository="repo",
        scope=("repo",),
        initial_phase=selected_phase,
        max_autonomous_hops=budget,
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
    return new_run(item, workflow, policy, run_id="RUN-1", baseline_head="head-0")


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
    findings: tuple[FindingDelta, ...] = (),
    requires_human: bool = False,
    escalation: Escalation | None = None,
    scope_changed: bool = False,
) -> RoutingResult:
    return RoutingResult(
        schema_version=1,
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
    )


def delta(
    finding_id: str | None,
    state: str,
    *,
    invariant: str = "The accepted invariant must hold.",
    blocking: bool = True,
    evidence: str = "evidence-1",
    successor_ref: str | None = None,
    new_material: bool = False,
) -> FindingDelta:
    return FindingDelta(
        finding_id=finding_id,
        invariant=invariant,
        blocking=blocking,
        proposed_state=FindingState(state),
        evidence_refs=(evidence,),
        successor_ref=successor_ref,
        new_material_evidence=new_material,
    )


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
        exit_code=0 if status in {"pass", "fail"} else None,
        signal=None,
        error_class=None if status in {"pass", "fail"} else status,
        stdout_byte_count=0,
        stdout_sha256=empty_digest,
        stdout_body_omitted=False,
        stderr_byte_count=0,
        stderr_sha256=empty_digest,
        stderr_body_omitted=False,
    )
