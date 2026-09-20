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
    MechanicalFacts,
    Observation,
    Phase,
    ReviewAuthority,
    Run,
    WorkflowProfile,
    record_hop,
)
from .persistence import checkpoint_projection
from .prompt import (
    AgentOperatingContract,
    ContextItem,
    PromptComponent,
    PromptPackage,
    assemble_prompt,
)
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
    observations: tuple[Observation, ...]


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
    invocation_id: str, durable_artifacts: tuple[str, ...] = (),
    execution_timeout_seconds: float = 0.0,
    registered_checkout_validator: Callable[[], None] | None = None,
    context_items: tuple[ContextItem, ...] = (),
    evidence_runner: EvidenceRunner = FAIL_CLOSED_EVIDENCE_RUNNER,
    evidence_commands: dict[str, PreparedEvidenceCommand] | None = None,
) -> VerticalRun:
    """Execute only the N=1 fake/subprocess boundary and reduce observed facts."""

    phase = profile.phases[run.phase]
    try:
        preflight_hop(repository, expected_repository=repository, expected_head=run.current_head)
    except GitPreflightError as error:
        raise ExecutionNotStartedError(str(error)) from error
    hop_id = f"H{run.hop_used + 1:03d}"
    invocation = Invocation(run.run_id, hop_id, invocation_id)
    try:
        prompt = assemble_prompt(
            run, phase, components, agent_operating_contract=agent_operating_contract,
            invocation=invocation, task_payload=task_payload,
            context=context_items,
        )
    except Exception as error:
        raise ExecutionNotStartedError(str(error)) from error
    outcome = adapter.execute(AgentExecutionRequest(
        invocation, prompt.text, repository,
        ROUTING_RESULT_ENVELOPE_CONTRACT, execution_timeout_seconds,
    ))
    if outcome.candidate_result is None:
        raise ExecutionResultValidationError(outcome, "agent execution produced no candidate structured result")
    try:
        result = validated_result(outcome.candidate_result, invocation)
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
                (*delta.anomalies, *post_acceptance_anomalies, *post_evidence_anomalies,
                 f"trusted_commit:{trusted_commit_error}" if trusted_commit_error else "")
            ).strip("; ") or None,
            required_evidence_error=required_error,
            required_evidence_failed=required_failed,
        ),
    )
    checkpoint = checkpoint_projection(reduction.run, profile, observations=observations)
    terminal = f"{run.work_item_id}/{run.run_id} {reduction.run.status.value} {hop_id} {delta.base_head}..{delta.end_head}"
    return VerticalRun(reduction, prompt, checkpoint, terminal, outcome, observations)
