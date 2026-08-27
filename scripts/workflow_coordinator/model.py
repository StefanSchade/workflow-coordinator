"""Small immutable records used by the workflow coordinator reducer."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping


class CoordinatorValue(str, Enum):
    """String enum whose values serialize without a custom JSON protocol."""


class DurableArtifact(CoordinatorValue):
    REQUIRED = "required"
    OPTIONAL = "optional"
    NONE = "none"


class ReviewAuthority(CoordinatorValue):
    ADVISORY = "advisory"
    GATE = "gate"


class FindingState(CoordinatorValue):
    OPEN = "open"
    RESOLVED = "resolved"
    RESIDUAL = "residual"
    SUPERSEDED = "superseded"


class FindingAuthority(CoordinatorValue):
    AGENT = "agent"
    SUPERVISOR = "supervisor"
    GATE = "gate"
    HUMAN = "human"


class RouteEffect(CoordinatorValue):
    GOTO = "goto"
    COMPLETE = "complete"
    AWAIT_HUMAN = "await_human"
    SUSPEND_FOR_PREREQUISITE = "suspend_for_prerequisite"
    STOP = "stop"


class RunStatus(CoordinatorValue):
    READY = "ready"
    RUNNING = "running"
    AWAITING_SUPERVISOR = "awaiting_supervisor"
    AWAITING_HUMAN = "awaiting_human"
    AWAITING_HUMAN_BUDGET = "awaiting_human_budget"
    SUSPENDED = "suspended"
    RECONCILE_REQUIRED = "reconcile_required"
    COMPLETED = "completed"
    STOPPED = "stopped"


class Outcome(CoordinatorValue):
    COMPLETED = "completed"
    CHANGES_REQUIRED = "changes_required"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class WorkItem:
    schema_version: int
    work_item_id: str
    authority_source: str
    profile_ref: str
    repository: str
    scope: tuple[str, ...]
    initial_phase: str
    max_autonomous_hops: int
    profile_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepositoryPolicy:
    schema_version: int
    policy_id: str
    revision: str
    scope: tuple[str, ...]
    actions: tuple[str, ...]
    evidence_states: tuple[str, ...]


@dataclass(frozen=True)
class ReviewDescriptor:
    purpose: str
    breadth: str
    authority: ReviewAuthority
    context: str
    blocking_policy: str
    trigger: str
    exit: str
    finding_authority: tuple[str, ...] = ()


@dataclass(frozen=True)
class Route:
    effect: RouteEffect
    target: str | None = None
    question: str | None = None
    supervisor: bool = False


@dataclass(frozen=True)
class Phase:
    role: str
    role_components: tuple[str, ...]
    modifier_components: tuple[str, ...]
    repository_policy_components: tuple[str, ...]
    durable_artifact: DurableArtifact
    authorized_scope: tuple[str, ...]
    authorized_actions: tuple[str, ...]
    human_gates: tuple[str, ...]
    evidence: tuple[str, ...]
    review: ReviewDescriptor | None
    routes: Mapping[str, Route]


@dataclass(frozen=True)
class WorkflowProfile:
    schema_version: int
    profile_id: str
    revision: str
    phases: Mapping[str, Phase]


@dataclass(frozen=True)
class Escalation:
    kind: str
    question: str


@dataclass(frozen=True)
class FindingDelta:
    finding_id: str | None
    invariant: str
    blocking: bool
    proposed_state: FindingState
    evidence_refs: tuple[str, ...]
    successor_ref: str | None = None
    new_material_evidence: bool = False


@dataclass(frozen=True)
class RoutingResult:
    schema_version: int
    outcome: Outcome
    requested_route: str
    scope_changed: bool
    requires_human: bool
    escalation: Escalation | None
    findings: tuple[FindingDelta, ...]
    claimed_commits: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class Finding:
    finding_id: str
    invariant: str
    blocking: bool
    state: FindingState
    introduced_by_hop: str
    changed_by_hop: str
    evidence_refs: tuple[str, ...]
    disposition: str
    successor_ref: str | None = None
    external_work_item_ref: str | None = None


@dataclass(frozen=True)
class Hop:
    hop_id: str
    sequence: int
    phase: str
    role: str
    context: str
    started_at: str
    ended_at: str | None
    base_head: str
    end_head: str
    actual_commits: tuple[str, ...]
    invocation_status: str
    outcome: str
    finding_delta_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    applied_route: str | None
    stop_reason: str | None
    summary: str
    attribution: str

    def compact(self) -> "HopSummary":
        return HopSummary(
            hop_id=self.hop_id,
            sequence=self.sequence,
            role=self.role,
            outcome=self.outcome,
            base_head=self.base_head,
            end_head=self.end_head,
            actual_commits=self.actual_commits,
            applied_route=self.applied_route,
            stop_reason=self.stop_reason,
            summary=self.summary,
            attribution=self.attribution,
        )


@dataclass(frozen=True)
class HopSummary:
    hop_id: str
    sequence: int
    role: str
    outcome: str
    base_head: str
    end_head: str
    actual_commits: tuple[str, ...]
    applied_route: str | None
    stop_reason: str | None
    summary: str
    attribution: str


@dataclass(frozen=True)
class Run:
    schema_version: int
    run_id: str
    work_item_id: str
    profile_id: str
    profile_revision: str
    policy_id: str
    policy_revision: str
    repository: str
    baseline_head: str
    current_head: str
    phase: str
    status: RunStatus
    hop_limit: int
    hop_used: int
    hops: tuple[HopSummary, ...] = ()
    findings: tuple[Finding, ...] = ()
    child_run_id: str | None = None
    suspension_head: str | None = None
    reconcile_reason: str | None = None
    reconcile_anchor: str | None = None
    stop_reason: str | None = None
    human_question: str | None = None
    anomalies: tuple[str, ...] = ()

    @property
    def hop_remaining(self) -> int:
        return self.hop_limit - self.hop_used


@dataclass(frozen=True)
class Observation:
    provider: str
    status: str
    command: tuple[str, ...]
    summary: str
    artifact_ref: str | None
    digest: str | None


@dataclass(frozen=True)
class MechanicalFacts:
    state_error: str | None = None
    repository_anomaly: str | None = None
    policy_anomaly: str | None = None
    required_evidence_error: str | None = None


@dataclass(frozen=True)
class Reduction:
    run: Run
    route_key: str | None
    autonomous_route: bool
    supervisor_required: bool


@dataclass(frozen=True)
class DispatchMarker:
    schema_version: int
    run_id: str
    hop_id: str
    sequence: int
    invocation_id: str
    base_head: str
    budget_reserved: int


def new_run(
    work_item: WorkItem,
    profile: WorkflowProfile,
    policy: RepositoryPolicy,
    *,
    run_id: str,
    baseline_head: str,
) -> Run:
    """Create one attempt after its human-authored inputs have been validated."""

    if work_item.initial_phase not in profile.phases:
        raise ValueError(f"unknown initial phase: {work_item.initial_phase}")
    if work_item.repository not in policy.scope:
        raise ValueError("work item repository is outside repository policy scope")
    return Run(
        schema_version=1,
        run_id=run_id,
        work_item_id=work_item.work_item_id,
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        repository=work_item.repository,
        baseline_head=baseline_head,
        current_head=baseline_head,
        phase=work_item.initial_phase,
        status=RunStatus.READY,
        hop_limit=work_item.max_autonomous_hops,
        hop_used=0,
    )


def record_hop(run: Run, hop: Hop) -> Run:
    """Charge one accepted invocation and retain only its compact Run summary."""

    allowed_statuses = {RunStatus.READY, RunStatus.AWAITING_SUPERVISOR}
    if run.status not in allowed_statuses:
        raise ValueError(f"Run status prohibits invocation: {run.status.value}")
    expected_sequence = run.hop_used + 1
    if hop.sequence != expected_sequence:
        raise ValueError(f"expected Hop sequence {expected_sequence}, got {hop.sequence}")
    if run.hop_used >= run.hop_limit:
        raise ValueError("autonomous Hop budget exhausted")
    if any(existing.hop_id == hop.hop_id for existing in run.hops):
        raise ValueError(f"duplicate Hop ID: {hop.hop_id}")
    return replace(
        run,
        current_head=hop.end_head,
        status=(
            RunStatus.AWAITING_SUPERVISOR
            if run.status is RunStatus.AWAITING_SUPERVISOR
            else RunStatus.RUNNING
        ),
        hop_used=expected_sequence,
        hops=(*run.hops, hop.compact()),
        stop_reason=None,
        human_question=None,
    )
