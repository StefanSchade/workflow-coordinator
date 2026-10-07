"""One-Hop Slice-3 vertical composition; Git and profile remain authoritative."""

from __future__ import annotations

import subprocess
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .adapter import (
    ROUTING_RESULT_ENVELOPE_CONTRACT,
    AgentExecutionPort,
    AgentExecutionRequest,
    AgentExecutionOutcome,
    ExecutionNotStartedError,
    ExecutionResultValidationError,
    Invocation,
    ResultValidationError,
    validated_result,
)
from .evidence import (
    EvidenceRunner,
    FAIL_CLOSED_EVIDENCE_RUNNER,
    PreparedEvidenceCommand,
    observation_error,
)
from .git_facts import (
    GitDelta,
    GitPreflightError,
    TrustedCommitError,
    commit_subject_error,
    observe_repository,
    observe_hop,
    preflight_hop,
    trusted_commit,
)
from .model import (
    COMMIT_ACTION,
    EDIT_ACTION,
    Hop,
    ExecutionEvidenceDocument,
    MechanicalFacts,
    Observation,
    Phase,
    ReviewAuthority,
    Run,
    RequiredEvidenceIdentity,
    StagedQualificationAttempt,
    TrustedQualificationIndex,
    WorkflowProfile,
    record_hop,
    result_contract_version,
    select_result_contract,
)
from .persistence import (
    checkpoint_projection,
    execution_evidence_run_identity,
    trusted_qualification_index_for_run,
)
from .prompt import (
    AgentOperatingContract,
    ContextItem,
    PromptComponent,
    PromptPackage,
    assemble_prompt,
)
from .reducer import (
    Reduction,
    derive_convergence_facts,
    reduce_result,
    validate_convergence_route_history,
)
from .serialization import (
    canonical_candidate_result_sha256,
    execution_evidence_from_json,
    execution_evidence_to_json,
    pre_route_anchor_sha256,
)


class VerticalRunError(RuntimeError):
    """A required Slice-3 boundary failed closed before route reduction."""


@dataclass(frozen=True)
class VerticalRun:
    reduction: Reduction
    prompt: PromptPackage
    checkpoint: dict[str, object]
    terminal: str
    execution_outcome: AgentExecutionOutcome
    observations: tuple[Observation, ...]
    staged_attempt: StagedQualificationAttempt | None
    qualification_index: TrustedQualificationIndex
    execution_evidence_json: str | None


def convergence_prompt_context(
    run: Run,
    profile: WorkflowProfile,
    qualification_index: TrustedQualificationIndex,
) -> ContextItem | None:
    """Build the complete required pre-dispatch v3 convergence projection."""

    if run.schema_version != 3 or profile.convergence is None:
        return None
    validate_convergence_route_history(run, profile, qualification_index)
    facts = derive_convergence_facts(
        run,
        qualification_index,
        profile.convergence.qualified_cycle_limit,
    )
    latest_coverage = {}
    latest_assessment = {}
    for hop in run.hops:
        for claim in hop.coverage_claims:
            latest_coverage[claim.finding_id] = {
                "source_hop_id": hop.hop_id,
                "disposition": claim.disposition.value,
                "evidence_refs": list(claim.evidence_refs),
                "attribution": "agent_attributed",
            }
        for assessment in hop.convergence_assessments:
            latest_assessment[assessment.finding_id] = {
                "source_hop_id": hop.hop_id,
                "correction_hop_id": assessment.correction_hop_id,
                "result": assessment.result.value,
                "evidence_refs": list(assessment.evidence_refs),
                "attribution": "agent_attributed",
            }
    obligations = []
    for finding in sorted(run.findings, key=lambda item: item.finding_id):
        if (
            finding.correction_obligation is None
            or finding.state.value != "open"
            or not finding.blocking
        ):
            continue
        obligations.append(
            {
                "finding_id": finding.finding_id,
                "owner": finding.correction_obligation.owner.value,
                "introduced_by_hop": finding.correction_obligation.introduced_by_hop,
                "state": finding.state.value,
                "blocking": finding.blocking,
                "coordinator_attribution": "coordinator_derived",
                "invariant": finding.invariant,
                "evidence_refs": list(finding.evidence_refs),
                "semantic_attribution": "agent_attributed",
                "latest_coverage": latest_coverage.get(finding.finding_id),
                "latest_evaluation": latest_assessment.get(finding.finding_id),
            }
        )
    escapes = []
    accepted_escapes = {
        hop.strategy_assessment.escape_hop_id for hop in run.hops
        if hop.strategy_assessment is not None and hop.strategy_assessment.result.value == "accepted"
    }
    generations: dict[str, int] = {}
    for hop in run.hops:
        if hop.route_reason and hop.route_reason.startswith("convergence_escape:"):
            triggers = tuple(
                item for item in hop.route_reason.partition(":")[2].split(",") if item
            )
            escapes.append(
                {
                    "evaluator_hop_id": hop.hop_id,
                    "triggering_finding_ids": list(triggers),
                    "trigger_generations": {item: generations.get(item, 0) for item in triggers},
                    "state": "consumed" if hop.hop_id in accepted_escapes else "unconsumed",
                    "requested_route": hop.requested_route,
                    "applied_route": hop.applied_route,
                    "route_reason": hop.route_reason,
                    "attribution": "coordinator_derived",
                    "diagnosis": next(({"hop_id": item.hop_id, "classification": item.diagnosis.classification.value, "analysis": item.diagnosis.analysis, "evidence_refs": list(item.diagnosis.evidence_refs), "attribution": "agent_attributed"} for item in run.hops if item.diagnosis is not None and item.diagnosis.escape_hop_id == hop.hop_id), None),
                    "strategy_revision": next(({"hop_id": item.hop_id, "kind": item.strategy_revision.kind.value, "plan": item.strategy_revision.plan, "evidence_refs": list(item.strategy_revision.evidence_refs), "attribution": "agent_attributed"} for item in reversed(run.hops) if item.strategy_revision is not None and item.strategy_revision.escape_hop_id == hop.hop_id), None),
                    "strategy_assessment": next(({"hop_id": item.hop_id, "result": item.strategy_assessment.result.value, "evidence_refs": list(item.strategy_assessment.evidence_refs), "attribution": "agent_attributed"} for item in reversed(run.hops) if item.strategy_assessment is not None and item.strategy_assessment.escape_hop_id == hop.hop_id), None),
                }
            )
        if hop.strategy_assessment is not None and hop.strategy_assessment.result.value == "accepted":
            for finding_id in hop.strategy_assessment.finding_ids:
                generations[finding_id] = generations.get(finding_id, 0) + 1
    payload = {
        "provenance": "coordinator_derived",
        "protocol": profile.convergence.protocol.value,
        "qualified_cycle_limit": profile.convergence.qualified_cycle_limit,
        "open_tracked_obligations": obligations,
        "convergence_facts": [
            {
                "finding_id": fact.finding_id,
                "strategy_generation": fact.strategy_generation,
                "qualified_cycle_count": fact.qualified_cycle_count,
                "qualified_cycle_limit": fact.qualified_cycle_limit,
                "goalpost_reached": fact.goalpost_reached,
                "source_evaluator_hops": list(fact.source_evaluator_hops),
                "attribution": "coordinator_derived",
            }
            for fact in facts
        ],
        "escape_routes": escapes,
    }
    return ContextItem(
        reference=f"Coordinator convergence projection for {run.run_id}",
        content="[convergence_context]\n" + json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ),
        required=True,
    )


def _subject_anomalies(
    repository: Path, commits: tuple[str, ...], run: Run, hop_id: str
) -> tuple[str, ...]:
    """Return post-acceptance diagnostics; a committed Hop must still be recorded."""

    anomalies: list[str] = []
    for commit in commits:
        subject = subprocess.run(
            ("git", "-C", str(repository), "show", "-s", "--format=%s", commit),
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        if commit_subject_error(subject, work_item_id=run.work_item_id, hop_id=hop_id):
            anomalies.append(f"WFC-GIT-08 commit subject lacks WorkItem/Hop identity: {subject}")
    return tuple(anomalies)


def _durable_artifact_anomalies(
    repository: Path,
    phase: Phase,
    delta: GitDelta,
    durable_artifacts: tuple[str, ...],
    result,
) -> tuple[str, ...]:
    """Keep durable policy failures in the post-acceptance mechanical-facts path."""

    if (
        phase.durable_artifact.value != "required"
        or result.outcome.value not in {"completed", "changes_required"}
    ):
        return ()
    intent_paths = () if result.commit_intent is None else result.commit_intent.paths
    if (
        not durable_artifacts
        or result.commit_intent is None
        or not delta.actual_commits
        or any(path not in intent_paths for path in durable_artifacts)
        or any(path not in delta.changed_paths for path in durable_artifacts)
    ):
        return ("required durable artifact is missing, unrepresented, or uncommitted",)
    for path in durable_artifacts:
        completed = subprocess.run(
            ("git", "-C", str(repository), "ls-tree", "-z", delta.end_head, "--", path),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        entries = tuple(filter(None, completed.stdout.split("\0")))
        try:
            metadata, tracked_path = entries[0].split("\t", 1)
            mode, object_type, object_id = metadata.split(" ", 2)
        except (IndexError, ValueError):
            mode = object_type = object_id = tracked_path = ""
        regular_versioned_file = (
            completed.returncode == 0
            and len(entries) == 1
            and tracked_path == path
            and mode in {"100644", "100755"}
            and object_type == "blob"
            and bool(object_id)
            and (repository / path).is_file()
        )
        if not regular_versioned_file:
            return (
                "required durable artifact is absent or not a regular Git-tracked file at end HEAD",
            )
    return ()


def run_one_hop(
    run: Run, profile: WorkflowProfile, repository: Path, adapter: AgentExecutionPort, components: dict[str, PromptComponent],
    *, agent_operating_contract: AgentOperatingContract, task_payload: str,
    invocation_id: str, persona: str, durable_artifacts: tuple[str, ...] = (),
    execution_timeout_seconds: float = 900.0,
    registered_checkout_validator: Callable[[], None] | None = None,
    context_items: tuple[ContextItem, ...] = (),
    evidence_runner: EvidenceRunner = FAIL_CLOSED_EVIDENCE_RUNNER,
    evidence_commands: dict[str, PreparedEvidenceCommand] | None = None,
    execution_binding: Mapping[str, object] | None = None,
    publish_staged_attempt: Callable[
        [StagedQualificationAttempt], tuple[ExecutionEvidenceDocument, str]
    ] | None = None,
) -> VerticalRun:
    """Execute only the N=1 fake/subprocess boundary and reduce observed facts."""

    phase = profile.phases[run.phase]
    selected_result_contract = select_result_contract(profile, phase, run)
    selected_result_contract_version = result_contract_version(
        selected_result_contract, profile, phase, run
    )
    try:
        preflight_hop(repository, expected_repository=repository, expected_head=run.current_head)
    except GitPreflightError as error:
        raise ExecutionNotStartedError(str(error)) from error
    hop_id = f"H{run.hop_used + 1:03d}"
    invocation = Invocation(run.run_id, hop_id, invocation_id)
    try:
        prompt = assemble_prompt(
            run, phase, components, agent_operating_contract=agent_operating_contract,
            invocation=invocation, persona=persona, task_payload=task_payload,
            context=context_items,
            contract_version=selected_result_contract_version,
        )
    except Exception as error:
        raise ExecutionNotStartedError(str(error)) from error
    outcome = adapter.execute(AgentExecutionRequest(
        invocation, prompt.text, repository,
        ROUTING_RESULT_ENVELOPE_CONTRACT, execution_timeout_seconds,
        contract_version=selected_result_contract_version,
        result_contract_kind=selected_result_contract,
    ))
    if outcome.candidate_result is None:
        raise ExecutionResultValidationError(outcome, "agent execution produced no candidate structured result")
    try:
        result = validated_result(
            outcome.candidate_result,
            invocation,
            selected_result_contract_version,
        )
    except ResultValidationError as error:
        raise ExecutionResultValidationError(outcome, str(error)) from error
    delta: GitDelta | None = None
    trusted_commit_error: str | None = None
    if result.commit_intent is not None:
        review_artifact_commit = (
            result.outcome.value == "changes_required"
            and phase.review is not None
            and phase.review.authority is ReviewAuthority.GATE
            and set(result.commit_intent.paths).issubset(durable_artifacts)
        )
        permitted_intent = (
            result.outcome.value in {"completed", "changes_required"}
            and not result.scope_changed
            and not result.requires_human
            and result.escalation is None
            and (
                result.outcome.value == "completed"
                or review_artifact_commit
            )
        )
        missing_actions = tuple(
            action
            for action in (EDIT_ACTION, COMMIT_ACTION)
            if action not in phase.authorized_actions
        )
        if missing_actions:
            # Fail closed before Git is mutated: a trusted commit of agent-created
            # semantic worktree mutation requires BOTH ``edit`` (authorizing the
            # mutation) and ``commit`` (authorizing the Coordinator's landing).  A
            # non-null CommitIntent with either authority missing can never become a
            # commit.  Any worktree mutation the agent already made is left
            # observable and surfaces as a repository anomaly below.
            joined = " and ".join(f"'{action}'" for action in missing_actions)
            noun = "action" if len(missing_actions) == 1 else "actions"
            trusted_commit_error = (
                "commit intent is not authorized: current phase does not grant the "
                f"{joined} {noun}; a trusted commit of agent-created worktree "
                f"mutation requires both '{EDIT_ACTION}' and '{COMMIT_ACTION}'"
            )
        elif not permitted_intent:
            trusted_commit_error = "commit intent is not permitted for this result disposition"
        else:
            try:
                if registered_checkout_validator is not None:
                    registered_checkout_validator()
                committed = trusted_commit(
                    repository,
                    expected_repository=repository,
                    base_head=run.current_head,
                    allowed_paths=phase.authorized_scope,
                    intent=result.commit_intent,
                    work_item_id=run.work_item_id,
                    hop_id=hop_id,
                )
                delta = committed.delta
            except TrustedCommitError as error:
                trusted_commit_error = str(error)
                delta = error.delta
            except Exception as error:
                trusted_commit_error = f"trusted commit boundary failure: {type(error).__name__}: {error}"
    if delta is None:
        delta = observe_hop(
            repository,
            base_head=run.current_head,
            claimed_commits=result.claimed_commits,
            allowed_paths=phase.authorized_scope,
        )
    post_acceptance_anomalies = (
        *_subject_anomalies(repository, delta.actual_commits, run, hop_id),
        *_durable_artifact_anomalies(
            repository, phase, delta, durable_artifacts, result
        ),
    )
    authorized_evidence = evidence_commands or {}
    observations = tuple(
        evidence_runner.observe(authorized_evidence[name]) for name in phase.evidence
    )
    observation_errors = tuple(
        error
        for name, observation in zip(phase.evidence, observations, strict=True)
        if (
            error := observation_error(observation, expected=authorized_evidence[name])
        ) is not None
    )
    post_evidence = observe_repository(repository)
    post_evidence_anomalies: tuple[str, ...] = ()
    if post_evidence.head != delta.end_head:
        post_evidence_anomalies += (
            "post-evidence HEAD differs from trusted pre-evidence Hop end HEAD",
        )
    if not post_evidence.clean or post_evidence.unresolved_submodules:
        post_evidence_anomalies += (
            "post-evidence worktree is not clean with resolved submodules",
        )
    required_error = next(
        (
            item.summary
            for item in observations
            if item.status in {"error", "not_run"}
        ),
        None,
    )
    if observation_errors:
        required_error = observation_errors[0]
    required_failed = any(item.status == "fail" for item in observations)
    required_plan = tuple(
        RequiredEvidenceIdentity(
            command_name=authorized_evidence[name].command_name,
            argv_digest=authorized_evidence[name].argv_digest,
            timeout_seconds=authorized_evidence[name].timeout_seconds,
        )
        for name in phase.evidence
    )
    anchor = None
    attempt = None
    evidence_json = None
    qualification_index = TrustedQualificationIndex.empty()
    if run.schema_version == 3 and not observation_errors:
        run_identity = execution_evidence_run_identity(run)
        candidate_digest = canonical_candidate_result_sha256(outcome.candidate_result)
        anchor = pre_route_anchor_sha256(
            run_identity,
            hop_id=hop_id,
            hop_sequence=run.hop_used + 1,
            invocation_id=invocation_id,
            phase=run.phase,
            workflow_role=phase.role.value,
            outcome=result.outcome.value,
            base_head=run.current_head,
            end_head=delta.end_head,
            actual_commits=delta.actual_commits,
            candidate_result_sha256=candidate_digest,
            required_evidence=required_plan,
        )
        binding = dict(execution_binding or {})
        attempt = StagedQualificationAttempt(
            hop_id=hop_id,
            hop_sequence=run.hop_used + 1,
            invocation_id=invocation_id,
            pre_route_anchor_sha256=anchor,
            phase=run.phase,
            workflow_role=phase.role.value,
            persona=persona,
            persona_sha256=hashlib.sha256(persona.encode("utf-8")).hexdigest(),
            adapter=str(binding.get("adapter") or "local"),
            executable=str(binding.get("executable") or "local"),
            model=(
                binding.get("model")
                if isinstance(binding.get("model"), str)
                else None
            ),
            reasoning=(
                binding.get("reasoning")
                if isinstance(binding.get("reasoning"), str)
                else None
            ),
            context="fresh",
            timeout_seconds=execution_timeout_seconds,
            returncode=outcome.returncode,
            timed_out=outcome.timed_out,
            candidate_result_present=True,
            candidate_result_sha256=candidate_digest,
            publication="staged",
            outcome=result.outcome.value,
            base_head=run.current_head,
            end_head=delta.end_head,
            actual_commits=delta.actual_commits,
            required_evidence=required_plan,
            trusted_observations=observations,
        )
        if publish_staged_attempt is None:
            document = ExecutionEvidenceDocument(4, run_identity, (attempt,))
            evidence_json = execution_evidence_to_json(document)
            parsed = execution_evidence_from_json(evidence_json)
            assert isinstance(parsed, ExecutionEvidenceDocument)
            document = parsed
        else:
            document, evidence_json = publish_staged_attempt(attempt)
    accepted = record_hop(
        run,
        Hop(
            hop_id,
            run.hop_used + 1,
            run.phase,
            phase.role.value,
            "fresh",
            "local",
            "local",
            run.current_head,
            delta.end_head,
            delta.actual_commits,
            "accepted",
            result.outcome.value,
            (),
            result.evidence_refs,
            None,
            None,
            result.summary,
            "agent",
            anchor,
            selected_result_contract,
            selected_result_contract_version,
        ),
    )
    if attempt is not None:
        qualification_index = trusted_qualification_index_for_run(accepted, document)
    reduction = reduce_result(
        accepted,
        result,
        profile,
        hop_id=hop_id,
        facts=MechanicalFacts(
            repository_anomaly="; ".join(
                (*delta.anomalies, *post_acceptance_anomalies, *post_evidence_anomalies,
                 f"trusted_commit:{trusted_commit_error}" if trusted_commit_error else "")
            ).strip("; ") or None,
            required_evidence_error=required_error,
            required_evidence_failed=required_failed,
        ),
        trusted_qualification_index=(
            qualification_index if attempt is not None else None
        ),
    )
    checkpoint = checkpoint_projection(
        reduction.run,
        profile,
        observations=observations,
        qualified_cycle_limit=(
            profile.convergence.qualified_cycle_limit
            if profile.convergence is not None
            else None
        ),
        execution_evidence_json=evidence_json,
    )
    terminal = f"{run.work_item_id}/{run.run_id} {reduction.run.status.value} {hop_id} {delta.base_head}..{delta.end_head}"
    return VerticalRun(
        reduction,
        prompt,
        checkpoint,
        terminal,
        outcome,
        observations,
        attempt,
        qualification_index,
        evidence_json,
    )
