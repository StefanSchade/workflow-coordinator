"""One-Hop Slice-3 vertical composition; Git and profile remain authoritative."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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
from .evidence import run_evidence
from .git_facts import (
    GitDelta,
    GitPreflightError,
    TrustedCommitError,
    commit_subject_error,
    observe_hop,
    preflight_hop,
    trusted_commit,
)
from .model import Hop, MechanicalFacts, Phase, Run, WorkflowProfile, record_hop
from .persistence import checkpoint_projection
from .prompt import PromptComponent, PromptPackage, assemble_prompt
from .reducer import Reduction, reduce_result


class VerticalRunError(RuntimeError):
    """A required Slice-3 boundary failed closed before route reduction."""


@dataclass(frozen=True)
class VerticalRun:
    reduction: Reduction
    prompt: PromptPackage
    checkpoint: dict[str, object]
    terminal: str
    execution_outcome: AgentExecutionOutcome


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
    phase: Phase, delta: GitDelta, durable_artifacts: tuple[str, ...]
) -> tuple[str, ...]:
    """Keep durable policy failures in the post-acceptance mechanical-facts path."""

    if phase.durable_artifact.value != "required":
        return ()
    if not durable_artifacts or not delta.actual_commits:
        return ("required durable artifact is missing, out of scope, or uncommitted",)
    if any(path not in delta.changed_paths for path in durable_artifacts):
        return ("required durable artifact is missing, out of scope, or uncommitted",)
    return ()


def run_one_hop(
    run: Run, profile: WorkflowProfile, repository: Path, adapter: AgentExecutionPort, components: dict[str, PromptComponent],
    *, task_payload: str, invocation_id: str, durable_artifacts: tuple[str, ...] = (),
    execution_timeout_seconds: float = 0.0,
    registered_checkout_validator: Callable[[], None] | None = None,
) -> VerticalRun:
    """Execute only the N=1 fake/subprocess boundary and reduce observed facts."""

    phase = profile.phases[run.phase]
    try:
        preflight_hop(repository, expected_repository=repository, expected_head=run.current_head)
    except GitPreflightError as error:
        raise ExecutionNotStartedError(str(error)) from error
    hop_id = f"H{run.hop_used + 1:03d}"
    try:
        prompt = assemble_prompt(run, phase, components, task_payload=task_payload)
    except Exception as error:
        raise ExecutionNotStartedError(str(error)) from error
    outcome = adapter.execute(AgentExecutionRequest(
        Invocation(run.run_id, hop_id, invocation_id), prompt.text, repository,
        ROUTING_RESULT_ENVELOPE_CONTRACT, execution_timeout_seconds,
    ))
    if outcome.candidate_result is None:
        raise ExecutionResultValidationError(outcome, "agent execution produced no candidate structured result")
    try:
        result = validated_result(outcome.candidate_result, Invocation(run.run_id, hop_id, invocation_id))
    except ResultValidationError as error:
        raise ExecutionResultValidationError(outcome, str(error)) from error
    delta: GitDelta | None = None
    trusted_commit_error: str | None = None
    if result.commit_intent is not None:
        permitted_intent = (
            result.outcome.value == "completed"
            and not result.scope_changed
            and not result.requires_human
            and result.escalation is None
        )
        if not permitted_intent:
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
        *_durable_artifact_anomalies(phase, delta, durable_artifacts),
    )
    observations = tuple(run_evidence(provider, repository) for provider in phase.evidence)
    required_error = next((item.summary for item in observations if item.status in {"fail", "error"}), None)
    accepted = record_hop(run, Hop(hop_id, run.hop_used + 1, run.phase, phase.role, "fresh", "local", "local",
                                   run.current_head, delta.end_head, delta.actual_commits, "accepted", result.outcome.value,
                                   (), result.evidence_refs, None, None, result.summary, "agent"))
    reduction = reduce_result(
        accepted,
        result,
        profile,
        hop_id=hop_id,
        facts=MechanicalFacts(
            repository_anomaly="; ".join(
                (*delta.anomalies, *post_acceptance_anomalies,
                 f"trusted_commit:{trusted_commit_error}" if trusted_commit_error else "")
            ).strip("; ") or None,
            required_evidence_error=required_error,
        ),
    )
    checkpoint = checkpoint_projection(reduction.run, profile, observations=observations)
    terminal = f"{run.work_item_id}/{run.run_id} {reduction.run.status.value} {hop_id} {delta.base_head}..{delta.end_head}"
    return VerticalRun(reduction, prompt, checkpoint, terminal, outcome)
