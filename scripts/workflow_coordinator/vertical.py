"""One-Hop Slice-3 vertical composition; Git and profile remain authoritative."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .adapter import Adapter, Invocation, validated_result
from .evidence import run_evidence
from .git_facts import GitPreflightError, observe_hop, preflight_hop
from .model import Hop, MechanicalFacts, Run, WorkflowProfile, record_hop
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


def _validate_subjects(repository: Path, commits: tuple[str, ...], run: Run, hop_id: str) -> None:
    import subprocess
    for commit in commits:
        subject = subprocess.run(("git", "-C", str(repository), "show", "-s", "--format=%s", commit),
                                 check=True, text=True, capture_output=True).stdout.strip()
        if ":" not in subject or run.work_item_id not in subject or hop_id not in subject:
            raise VerticalRunError(f"WFC-GIT-08 commit subject lacks WorkItem/Hop identity: {subject}")


def run_one_hop(
    run: Run, profile: WorkflowProfile, repository: Path, adapter: Adapter, components: dict[str, PromptComponent],
    *, task_payload: str, invocation_id: str, durable_artifacts: tuple[str, ...] = (),
) -> VerticalRun:
    """Execute only the N=1 fake/subprocess boundary and reduce observed facts."""

    phase = profile.phases[run.phase]
    try:
        preflight_hop(repository, expected_repository=repository, expected_head=run.current_head)
    except GitPreflightError as error:
        raise VerticalRunError(str(error)) from error
    hop_id = f"H{run.hop_used + 1:03d}"
    prompt = assemble_prompt(run, phase, components, task_payload=task_payload)
    result = validated_result(adapter.invoke(prompt.text, repository), Invocation(run.run_id, hop_id, invocation_id))
    delta = observe_hop(repository, base_head=run.current_head, claimed_commits=result.claimed_commits,
                        allowed_paths=phase.authorized_scope)
    _validate_subjects(repository, delta.actual_commits, run, hop_id)
    if phase.durable_artifact.value == "required":
        if not durable_artifacts or not delta.actual_commits or any(path not in delta.changed_paths for path in durable_artifacts):
            raise VerticalRunError("required durable artifact is missing, out of scope, or uncommitted")
    observations = tuple(run_evidence(provider, repository) for provider in phase.evidence)
    required_error = next((item.summary for item in observations if item.status in {"fail", "error"}), None)
    accepted = record_hop(run, Hop(hop_id, run.hop_used + 1, run.phase, phase.role, "fresh", "local", "local",
                                   run.current_head, delta.end_head, delta.actual_commits, "accepted", result.outcome.value,
                                   (), result.evidence_refs, None, None, result.summary, "agent"))
    reduction = reduce_result(accepted, result, profile, hop_id=hop_id,
                              facts=MechanicalFacts(repository_anomaly="; ".join(delta.anomalies) or None,
                                                     required_evidence_error=required_error))
    checkpoint = checkpoint_projection(reduction.run, profile, observations=observations)
    terminal = f"{run.work_item_id}/{run.run_id} {reduction.run.status.value} {hop_id} {delta.base_head}..{delta.end_head}"
    return VerticalRun(reduction, prompt, checkpoint, terminal)
