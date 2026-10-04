"""Small immutable records used by the workflow coordinator reducer."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping


# Reserved workflow-effect action tokens.  ``authorized_actions`` /
# ``RepositoryPolicy.actions`` are an open descriptive capability vocabulary;
# only these two tokens carry normative Coordinator meaning (see FTR
# section 11.4.7).  They name *workflow effects*, never shell commands exposed to
# the agent:
#
# * ``EDIT_ACTION`` -- the semantic worker may mutate authorized worktree content.
# * ``COMMIT_ACTION`` -- a successful invocation may return a CommitIntent that the
#   trusted Coordinator materializes as one bounded ordinary local Git commit.
#
# ``COMMIT_ACTION`` never grants the agent authority to run ``git add`` /
# ``git commit`` or any other Git command; the agent remains forbidden from
# mutating Git metadata and the sandbox is unchanged.
EDIT_ACTION = "edit"
COMMIT_ACTION = "commit"
COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS = 20
COORDINATOR_HARD_MAX_EVIDENCE_TIMEOUT_SECONDS = 900


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


class ConvergenceRole(CoordinatorValue):
    """Closed profile-v3 phase role vocabulary."""

    NONE = "none"
    CORRECTOR = "corrector"
    EVALUATOR = "evaluator"
    DIAGNOSIS = "diagnosis"
    REFRAME = "reframe"
    STRATEGY_GATE = "strategy_gate"


class ConvergenceProtocol(CoordinatorValue):
    REPEATED_COUNTEREXAMPLE_V1 = "repeated_counterexample_v1"


class CorrectionObligationOwner(CoordinatorValue):
    GATE = "gate"
    HUMAN = "human"


class CoverageDisposition(CoordinatorValue):
    ADDRESSED = "addressed"
    BLOCKED = "blocked"
    ESCALATION = "escalation"


class ConvergenceAssessmentResult(CoordinatorValue):
    CLOSED = "closed"
    CARRIED_NEW_COUNTEREXAMPLE = "carried_new_counterexample"
    CARRIED_UNMET = "carried_unmet"
    RESIDUALIZED = "residualized"
    SUPERSEDED = "superseded"


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


class WorkflowRole(CoordinatorValue):
    """Closed phase-function vocabulary; role names grant no authority."""

    WORKER = "worker"
    INDEPENDENT_REVIEWER = "independent_reviewer"


@dataclass(frozen=True)
class ExecutionBinding:
    """One explicit, authority-neutral production execution choice."""

    adapter: str
    model: str
    reasoning: str


@dataclass(frozen=True)
class RunExecutionConfiguration:
    """Immutable role-keyed MVP projection prepared once for one Run."""

    schema_version: int
    bindings: Mapping[WorkflowRole, ExecutionBinding]


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
    personas: Mapping[WorkflowRole, str]
    profile_labels: tuple[str, ...] = ()
    durable_artifacts: Mapping[str, tuple[str, ...]] | None = None


@dataclass(frozen=True)
class EvidenceCommand:
    argv: tuple[str, ...]
    timeout_seconds: int


@dataclass(frozen=True)
class RepositoryPolicy:
    schema_version: int
    policy_id: str
    revision: str
    scope: tuple[str, ...]
    governance_paths: tuple[str, ...]
    work_item_scope_ceiling: tuple[str, ...]
    max_autonomous_hops: int
    actions: tuple[str, ...]
    evidence_commands: Mapping[str, EvidenceCommand]


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
    failed_evidence_route: str | None = None


@dataclass(frozen=True)
class Route:
    effect: RouteEffect
    target: str | None = None
    question: str | None = None
    supervisor: bool = False


@dataclass(frozen=True)
class Phase:
    role: WorkflowRole
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
    convergence_role: ConvergenceRole = ConvergenceRole.NONE
    normal_correction_route: str | None = None
    escape_route: str | None = None


@dataclass(frozen=True)
class ConvergenceConfiguration:
    protocol: ConvergenceProtocol
    qualified_cycle_limit: int


@dataclass(frozen=True)
class WorkflowProfile:
    schema_version: int
    profile_id: str
    revision: str
    phases: Mapping[str, Phase]
    convergence: ConvergenceConfiguration | None = None


@dataclass(frozen=True)
class Escalation:
    kind: str
    question: str


@dataclass(frozen=True)
class SemanticStop:
    """Immutable observation of a correlated request for human authority."""

    kind: str
    question: str
    source_hop_id: str


@dataclass(frozen=True)
class SuccessorProvenance:
    """Backward-only linkage written when a fresh successor Run is created."""

    predecessor_run_id: str
    predecessor_work_item_id: str
    predecessor_final_head: str
    authority_decision_ref: str
    authority_decision_sha256: str
    continuation_descriptor_sha256: str
    salvage_manifest_sha256: str | None


@dataclass(frozen=True)
class ExistingFindingDelta:
    """A proposal about a Finding already owned by the Coordinator ledger."""

    finding_id: str
    proposed_state: FindingState
    evidence_refs: tuple[str, ...]
    successor_ref: str | None = None
    new_material_evidence: bool = False


@dataclass(frozen=True)
class NewFindingDelta:
    """An agent-authored proposal for a new Finding."""

    finding_id: None
    invariant: str
    blocking: bool
    proposed_state: FindingState
    evidence_refs: tuple[str, ...]
    successor_ref: str | None = None
    new_material_evidence: bool = False
    local_ref: str | None = None


FindingDeltaValue = ExistingFindingDelta | NewFindingDelta


@dataclass(frozen=True)
class CommitIntent:
    """An agent's bounded semantic request for one Coordinator-created commit.

    This is deliberately distinct from ``claimed_commits``: it names neither a
    Git command nor a commit that already exists.
    """

    paths: tuple[str, ...]
    subject: str


@dataclass(frozen=True)
class CorrectionObligation:
    owner: CorrectionObligationOwner
    introduced_by_hop: str


@dataclass(frozen=True)
class CoverageClaim:
    finding_id: str
    disposition: CoverageDisposition
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ConvergenceAssessment:
    finding_id: str
    correction_hop_id: str
    result: ConvergenceAssessmentResult
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ConvergenceFact:
    """A replay-derived count for one tracked obligation and strategy generation."""

    finding_id: str
    strategy_generation: int
    qualified_cycle_count: int
    qualified_cycle_limit: int
    goalpost_reached: bool
    source_evaluator_hops: tuple[str, ...]


@dataclass(frozen=True)
class RoutingResult:
    schema_version: int
    outcome: Outcome
    requested_route: str
    scope_changed: bool
    requires_human: bool
    escalation: Escalation | None
    findings: tuple[FindingDeltaValue, ...]
    claimed_commits: tuple[str, ...]
    commit_intent: CommitIntent | None
    evidence_refs: tuple[str, ...]
    summary: str
    coverage_claims: tuple[CoverageClaim, ...] = ()
    convergence_assessments: tuple[ConvergenceAssessment, ...] = ()
    diagnosis: None = None
    strategy_revision: None = None
    strategy_assessment: None = None


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
    correction_obligation: CorrectionObligation | None = None


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
    pre_route_anchor_sha256: str | None = None
    requested_route: str | None = None
    route_reason: str | None = None

    def compact(self) -> "HopSummary":
        return HopSummary(
            hop_id=self.hop_id,
            sequence=self.sequence,
            role=self.role,
            outcome=self.outcome,
            base_head=self.base_head,
            end_head=self.end_head,
            actual_commits=self.actual_commits,
            finding_delta_ids=self.finding_delta_ids,
            evidence_refs=self.evidence_refs,
            applied_route=self.applied_route,
            stop_reason=self.stop_reason,
            summary=self.summary,
            attribution=self.attribution,
            pre_route_anchor_sha256=self.pre_route_anchor_sha256,
            requested_route=self.requested_route,
            route_reason=self.route_reason,
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
    finding_delta_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    applied_route: str | None
    stop_reason: str | None
    summary: str
    attribution: str
    coverage_claims: tuple[CoverageClaim, ...] = ()
    convergence_assessments: tuple[ConvergenceAssessment, ...] = ()
    pre_route_anchor_sha256: str | None = None
    requested_route: str | None = None
    route_reason: str | None = None


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
    semantic_stop: SemanticStop | None = None
    successor_provenance: SuccessorProvenance | None = None

    @property
    def hop_remaining(self) -> int:
        return self.hop_limit - self.hop_used


@dataclass(frozen=True)
class StatFingerprint:
    st_dev: int
    st_ino: int
    st_mode: int
    st_size: int
    st_mtime_ns: int


@dataclass(frozen=True)
class Observation:
    provider: str
    status: str
    command_name: str
    command: tuple[str, ...]
    summary: str
    artifact_ref: str | None
    argv_digest: str
    authored_argv0: str
    timeout_seconds: int
    cwd_policy: str
    invocation_path: str | None
    canonical_path: str | None
    stat_fingerprint: StatFingerprint | None
    environment_policy: str
    path_source: str
    path_digest: str
    exit_code: int | None
    signal: int | None
    error_class: str | None
    stdout_byte_count: int | None
    stdout_sha256: str | None
    stdout_body_omitted: bool | None
    stderr_byte_count: int | None
    stderr_sha256: str | None
    stderr_body_omitted: bool | None


@dataclass(frozen=True)
class MechanicalFacts:
    state_error: str | None = None
    repository_anomaly: str | None = None
    policy_anomaly: str | None = None
    required_evidence_error: str | None = None
    required_evidence_failed: bool = False


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
    pre_route_anchor_sha256: str | None = None


@dataclass(frozen=True)
class ExecutionEvidenceRunIdentity:
    run_id: str
    work_item_id: str
    profile_id: str
    profile_revision: str


@dataclass(frozen=True)
class RequiredEvidenceIdentity:
    command_name: str
    argv_digest: str
    timeout_seconds: int


@dataclass(frozen=True)
class StagedQualificationAttempt:
    """One immutable schema-4 pre-route execution attempt."""

    hop_id: str
    hop_sequence: int
    invocation_id: str
    pre_route_anchor_sha256: str
    phase: str
    workflow_role: str
    persona: str
    persona_sha256: str
    adapter: str
    executable: str
    model: str | None
    reasoning: str | None
    context: str
    timeout_seconds: float
    returncode: int | None
    timed_out: bool
    candidate_result_present: bool
    candidate_result_sha256: str
    publication: str
    outcome: str
    base_head: str
    end_head: str
    actual_commits: tuple[str, ...]
    required_evidence: tuple[RequiredEvidenceIdentity, ...]
    trusted_observations: tuple[Observation, ...]


@dataclass(frozen=True)
class ExecutionEvidenceDocument:
    schema_version: int
    run: ExecutionEvidenceRunIdentity
    attempts: tuple[StagedQualificationAttempt, ...]


@dataclass(frozen=True)
class LegacyExecutionEvidenceDocument:
    """Audit-readable but mechanically ineligible execution evidence."""

    schema_version: int
    attempts: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class TrustedQualificationIndex:
    """Lossless validated schema-4 input, never a persisted qualification cache."""

    run: ExecutionEvidenceRunIdentity | None
    attempts: tuple[StagedQualificationAttempt, ...]

    @classmethod
    def empty(cls) -> "TrustedQualificationIndex":
        return cls(run=None, attempts=())


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
        schema_version=3 if profile.schema_version == 3 else 2,
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
