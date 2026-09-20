#!/usr/bin/env python3
"""Declarative finite-serial entry point for the accepted coordinator path."""

from __future__ import annotations

import sys
import argparse
import hashlib
import json
import re
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence

from .git_facts import GitPreflightError, observe_repository, preflight_hop, repository_identity
from .evidence import (
    EvidenceRunner,
    FAIL_CLOSED_EVIDENCE_RUNNER,
    PRODUCTION_EVIDENCE_RUNNER,
    PreparedEvidenceCommand,
    prepare_evidence_commands,
    snapshot_environment,
)
from .model import (
    COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS,
    DurableArtifact,
    RunStatus,
    new_run,
)
from .persistence import (
    atomic_write_json,
    checkpoint_projection,
    observation_to_data,
    write_run_snapshot,
)
from .prompt import (
    AgentOperatingContract,
    ContextItem,
    PromptComponent,
    load_agent_operating_contract,
)
from .serialization import (
    ValidationError,
    a1_path_shape,
    component_manifest_from_yaml,
    repository_policy_from_yaml,
    work_item_from_yaml,
    workflow_profile_from_yaml,
)
from .slice4 import Slice4Error, checkout_state_root, register_checkout, run_real_one_hop, validate_checkout
from .codex_adapter import CodexCliAdapter


class OperatorPreflightError(RuntimeError):
    """A local rejection that occurred before provider execution."""


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(("git", "-C", str(repository), *args), text=True, capture_output=True)
    if completed.returncode:
        raise OperatorPreflightError(completed.stderr.strip() or "Git fact lookup failed")
    return completed.stdout


def _relative_path(value: str, *, context: str) -> str:
    if not value or value != value.strip() or "\\" in value:
        raise OperatorPreflightError(f"{context} must be a normalized repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise OperatorPreflightError(f"{context} must not escape the repository")
    if path.as_posix() != value:
        raise OperatorPreflightError(f"{context} is ambiguous after normalization")
    return value


def _pinned_text(repository: Path, revision: str, path: str, *, context: str) -> str:
    path = _relative_path(path, context=context)
    tracked = _git(repository, "ls-tree", "-r", "--name-only", revision, "--", path).splitlines()
    if tracked != [path]:
        raise OperatorPreflightError(f"{context} is not tracked at expected HEAD: {path}")
    return _git(repository, "show", f"{revision}:{path}")


def _selected_ids(profile) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        component_id
        for phase in profile.phases.values()
        for component_id in (*phase.role_components, *phase.modifier_components, *phase.repository_policy_components)
    ))


def _within_scope(path: str, scope: tuple[str, ...]) -> bool:
    return any(path == allowed or path.startswith(f"{allowed.rstrip('/')}/") for allowed in scope)


def _normalized_scope(values: tuple[str, ...], *, context: str) -> tuple[str, ...]:
    return tuple(_relative_path(value, context=context) for value in values)


def _admit_a1(work_item, profile, policy, governing_paths) -> None:
    """Enforce the pure A1 authority relations without consulting external state."""
    malformed_work_scope = sorted(path for path in work_item.scope if not a1_path_shape(path))
    if malformed_work_scope:
        raise OperatorPreflightError(
            "WorkItem.scope contains malformed A1 paths: " + ", ".join(malformed_work_scope)
        )
    malformed_phase_scope = sorted(
        (phase_key, path)
        for phase_key, phase in profile.phases.items()
        for path in phase.authorized_scope
        if not a1_path_shape(path)
    )
    if malformed_phase_scope:
        rendered = ", ".join(f"{phase_key}:{path}" for phase_key, path in malformed_phase_scope)
        raise OperatorPreflightError(
            "profile authorized_scope contains malformed A1 paths: " + rendered
        )
    malformed_governing = sorted(path for path in governing_paths if not a1_path_shape(path))
    if malformed_governing:
        raise OperatorPreflightError(
            "governing inputs contain malformed A1 paths: " + ", ".join(malformed_governing)
        )
    outside_governance = sorted(
        path for path in governing_paths if not _within_scope(path, policy.governance_paths)
    )
    if outside_governance:
        raise OperatorPreflightError(
            "governing inputs are outside repository policy governance_paths: "
            + ", ".join(outside_governance)
        )
    overlaps = sorted(
        (phase_key, phase_path, governance_path)
        for phase_key, phase in profile.phases.items()
        for phase_path in phase.authorized_scope
        for governance_path in policy.governance_paths
        if _within_scope(phase_path, (governance_path,))
        or _within_scope(governance_path, (phase_path,))
    )
    if overlaps:
        rendered = ", ".join(
            f"{phase_key}:{phase_path} <-> {governance_path}"
            for phase_key, phase_path, governance_path in overlaps
        )
        raise OperatorPreflightError(
            "profile authorized_scope overlaps repository policy governance_paths: " + rendered
        )
    outside_ceiling = sorted(
        path for path in work_item.scope if not _within_scope(path, policy.work_item_scope_ceiling)
    )
    if outside_ceiling:
        raise OperatorPreflightError(
            "WorkItem.scope exceeds repository policy work_item_scope_ceiling: "
            + ", ".join(outside_ceiling)
        )
    if work_item.max_autonomous_hops > COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS:
        raise OperatorPreflightError("Slice-5B supports at most five autonomous Hops")
    if work_item.max_autonomous_hops > policy.max_autonomous_hops:
        raise OperatorPreflightError(
            "WorkItem.max_autonomous_hops exceeds repository policy maximum: "
            f"{work_item.max_autonomous_hops} > {policy.max_autonomous_hops}"
        )


def _selected_evidence_names(profile, policy) -> tuple[str, ...]:
    """Check definition/reference coherence and retain first-reference order."""

    selected: list[str] = []
    seen: set[str] = set()
    for phase_key, phase in profile.phases.items():
        for name in phase.evidence:
            if name not in policy.evidence_commands:
                raise OperatorPreflightError(
                    f"profile phase {phase_key} references undefined evidence command: {name}"
                )
            if name not in seen:
                seen.add(name)
                selected.append(name)
    return tuple(selected)


def _governing_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--work-item", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--components", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--durable-artifact", action="append", default=[])
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--executable", default="codex")
    return parser.parse_args(argv)


def _preflight(
    args: argparse.Namespace,
    *,
    evidence_runner: EvidenceRunner = FAIL_CLOSED_EVIDENCE_RUNNER,
):
    agent_operating_contract = load_agent_operating_contract()
    repository = Path(args.repository).resolve()
    if not repository.is_dir():
        raise OperatorPreflightError("repository path is not a directory")
    try:
        if repository_identity(repository) != repository:
            raise OperatorPreflightError("repository path is not the Git top-level")
    except GitPreflightError as error:
        raise OperatorPreflightError(str(error)) from error
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_head):
        raise OperatorPreflightError("expected HEAD must be a full 40-character lowercase commit SHA")
    facts = observe_repository(repository)
    if facts.head != args.expected_head:
        raise OperatorPreflightError("current HEAD does not equal expected HEAD")
    if not facts.clean or facts.unresolved_submodules:
        raise OperatorPreflightError("worktree preconditions fail: repository must be clean with resolved submodules")
    state_root = Path(args.state_root).resolve()
    if state_root.exists():
        raise OperatorPreflightError("state root already exists; one-shot operator roots are never overwritten")
    input_paths = {name: _relative_path(getattr(args, name), context=name.replace("_", " "))
                   for name in ("work_item", "profile", "policy", "components", "task")}
    texts = {name: _pinned_text(repository, args.expected_head, path, context=name.replace("_", " "))
             for name, path in input_paths.items()}
    try:
        work_item = work_item_from_yaml(texts["work_item"])
        profile = workflow_profile_from_yaml(texts["profile"])
        policy = repository_policy_from_yaml(texts["policy"])
        manifest = component_manifest_from_yaml(texts["components"])
    except ValidationError as error:
        raise OperatorPreflightError(str(error)) from error
    if _relative_path(work_item.profile_ref, context="WorkItem.profile_ref") != input_paths["profile"]:
        raise OperatorPreflightError("WorkItem.profile_ref does not identify the supplied profile path")
    policy_actions = set(policy.actions)
    work_scope = _normalized_scope(work_item.scope, context="WorkItem.scope")
    normalized_phase_scopes: dict[str, tuple[str, ...]] = {}
    for phase_key, phase_value in profile.phases.items():
        excess = sorted(set(phase_value.authorized_actions) - policy_actions)
        if excess:
            raise OperatorPreflightError(
                f"profile phase {phase_key} authorizes actions outside repository policy: {', '.join(excess)}"
            )
        phase_scope = _normalized_scope(phase_value.authorized_scope, context=f"profile phase {phase_key} authorized_scope")
        normalized_phase_scopes[phase_key] = phase_scope
        if any(not _within_scope(path, work_scope) for path in phase_scope):
            raise OperatorPreflightError(f"profile phase {phase_key} authorized scope exceeds WorkItem.scope")
    if work_item.max_autonomous_hops > COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS:
        raise OperatorPreflightError("Slice-5B supports at most five autonomous Hops")
    if work_item.initial_phase not in profile.phases:
        raise OperatorPreflightError(f"WorkItem.initial_phase is absent from supplied profile: {work_item.initial_phase}")
    initial_phase = profile.phases[work_item.initial_phase]
    legacy_artifacts = tuple(_relative_path(path, context="durable artifact") for path in args.durable_artifact)
    if len(set(legacy_artifacts)) != len(legacy_artifacts):
        raise OperatorPreflightError("durable artifact arguments must not contain duplicates")
    durable_artifacts: dict[str, tuple[str, ...]] = {}
    if work_item.durable_artifacts is not None:
        if legacy_artifacts:
            raise OperatorPreflightError(
                "WorkItem durable_artifacts and --durable-artifact are mutually exclusive"
            )
        unknown_phases = sorted(set(work_item.durable_artifacts) - set(profile.phases))
        if unknown_phases:
            raise OperatorPreflightError(
                "WorkItem durable_artifacts names undeclared phases: " + ", ".join(unknown_phases)
            )
        durable_artifacts = dict(work_item.durable_artifacts)
        for phase_key, phase_value in profile.phases.items():
            paths = durable_artifacts.get(phase_key, ())
            if phase_value.durable_artifact is DurableArtifact.REQUIRED and not paths:
                raise OperatorPreflightError(
                    f"profile phase {phase_key} requires mapped durable artifacts"
                )
            if phase_value.durable_artifact is DurableArtifact.NONE and paths:
                raise OperatorPreflightError(
                    f"profile phase {phase_key} permits no mapped durable artifacts"
                )
            if any(
                not _within_scope(path, work_scope)
                or not _within_scope(path, normalized_phase_scopes[phase_key])
                for path in paths
            ):
                raise OperatorPreflightError(
                    f"durable artifact for phase {phase_key} is outside WorkItem or phase scope"
                )
    else:
        for phase_key, phase_value in profile.phases.items():
            if phase_key != work_item.initial_phase and phase_value.durable_artifact is DurableArtifact.REQUIRED:
                raise OperatorPreflightError(
                    f"legacy durable-artifact input cannot satisfy later required phase {phase_key}"
                )
        if initial_phase.durable_artifact is DurableArtifact.REQUIRED and not legacy_artifacts:
            raise OperatorPreflightError("initial phase requires at least one durable artifact")
        if initial_phase.durable_artifact is DurableArtifact.NONE and legacy_artifacts:
            raise OperatorPreflightError("initial phase allows no durable-artifact arguments")
        if any(
            not _within_scope(path, work_scope)
            or not _within_scope(path, normalized_phase_scopes[work_item.initial_phase])
            for path in legacy_artifacts
        ):
            raise OperatorPreflightError("durable artifact is outside WorkItem or initial phase scope")
        if initial_phase.durable_artifact is DurableArtifact.REQUIRED and any(
            route.target == work_item.initial_phase
            for phase_value in profile.phases.values()
            for route in phase_value.routes.values()
        ):
            raise OperatorPreflightError(
                "legacy durable-artifact input cannot satisfy direct initial-phase re-entry"
            )
    components: dict[str, PromptComponent] = {}
    governing = {
        input_paths[name]: _governing_digest(text)
        for name, text in texts.items()
    }
    selected_component_paths: list[str] = []
    for component_id in _selected_ids(profile):
        reference = manifest.get(component_id)
        if reference is None:
            raise OperatorPreflightError(f"selected component ID is absent from manifest: {component_id}")
        path = _relative_path(reference.path, context=f"component {component_id} path")
        content = _pinned_text(repository, args.expected_head, path, context=f"component {component_id}")
        components[component_id] = PromptComponent(component_id, path, args.expected_head, content)
        governing[path] = _governing_digest(content)
        selected_component_paths.append(path)
    _admit_a1(
        work_item, profile, policy,
        (*input_paths.values(), *selected_component_paths),
    )
    selected_evidence = _selected_evidence_names(profile, policy)
    environment = snapshot_environment()
    try:
        prepared_evidence = prepare_evidence_commands(
            selected_evidence, policy.evidence_commands, repository,
            environment=environment,
        )
    except ValueError as error:
        raise OperatorPreflightError(str(error)) from error
    if args.timeout_seconds <= 0:
        raise OperatorPreflightError("timeout seconds must be positive")
    return (
        agent_operating_contract, repository, state_root, input_paths, texts, work_item, profile, policy,
        components, durable_artifacts, legacy_artifacts, governing, evidence_runner,
        {command.command_name: command for command in prepared_evidence}, environment,
    )


def _governing_inputs_unchanged(repository: Path, expected_head: str, governing: dict[str, str]) -> bool:
    """Re-read every pre-H001 repository input at B; paths must remain tracked."""
    try:
        return all(
            _governing_digest(_pinned_text(repository, expected_head, path, context="governing input")) == digest
            for path, digest in governing.items()
        )
    except OperatorPreflightError:
        return False


def _reconstructed_context(
    run,
    profile,
    repository: Path,
    durable_artifacts: tuple[str, ...],
    prior_observations: tuple[object, ...],
) -> tuple[ContextItem, ...]:
    """Project bounded fresh context while preserving origin labels."""

    def agent_literal(value: object) -> str:
        """Keep agent-originated values on one unambiguous context line."""

        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))

    phase = profile.phases[run.phase]
    lines = [
        "provenance=coordinator_derived",
        f"current_phase={run.phase}",
        f"current_head={run.current_head}",
        f"durable_artifacts={','.join(durable_artifacts)}",
    ]
    if phase.review is not None:
        lines.extend(
            (
                f"review_authority={phase.review.authority.value}",
                f"blocking_policy={phase.review.blocking_policy}",
                f"finding_authority={','.join(phase.review.finding_authority)}",
            )
        )
    for finding in run.findings:
        lines.extend(
            (
                f"finding={finding.finding_id}|state={finding.state.value}|blocking={str(finding.blocking).lower()}"
                f"|introduced_by={finding.introduced_by_hop}|changed_by={finding.changed_by_hop}"
                f"|disposition={finding.disposition}|ledger_authority=coordinator"
                "|blocking_origin=agent_proposal",
                "agent_attributed_finding_prose="
                + agent_literal(
                    {"finding_id": finding.finding_id, "value": finding.invariant}
                ),
                "agent_attributed_evidence_refs="
                + agent_literal(
                    {
                        "finding_id": finding.finding_id,
                        "values": finding.evidence_refs,
                    }
                ),
            )
        )
        if finding.successor_ref is not None:
            lines.append(
                "agent_attributed_successor_ref="
                + agent_literal(
                    {
                        "finding_id": finding.finding_id,
                        "value": finding.successor_ref,
                    }
                )
            )
    if run.hops:
        hop = run.hops[-1]
        changed_paths = _git(
            repository, "diff", "--name-only", f"{hop.base_head}..{hop.end_head}"
        ).splitlines()
        lines.extend(
            (
                f"prior_hop={hop.hop_id}",
                f"agent_attributed_prior_outcome={agent_literal(hop.outcome)}",
                f"prior_range={hop.base_head}..{hop.end_head}",
                f"prior_commits={','.join(hop.actual_commits)}",
                f"prior_changed_paths={','.join(changed_paths)}",
                f"prior_applied_route={hop.applied_route or ''}",
                f"agent_attributed_prior_summary={agent_literal(hop.summary)}",
            )
        )
    for observation in prior_observations:
        lines.append(
            f"trusted_evidence={observation.provider}|status={observation.status}"
            f"|summary={json.dumps(observation.summary, ensure_ascii=True)}"
        )
    return (
        ContextItem(
            reference=f"Coordinator reconstruction for {run.run_id}/{run.phase}",
            content="\n".join(lines),
        ),
    )


def _execution_binding(adapter, *, executable: str) -> dict[str, object]:
    """Resolve the concrete binding for this invocation without selecting one."""
    selected = adapter
    if selected is None:
        selected = CodexCliAdapter(Path("."), executable=executable)
    if isinstance(selected, CodexCliAdapter):
        return {"adapter": "codex_cli", "executable": selected.executable,
                "model": selected.model, "reasoning": selected.reasoning_effort}
    return {"adapter": "local", "executable": executable,
            "model": None, "reasoning": None}


def _prepared_evidence_plan(
    commands: dict[str, PreparedEvidenceCommand],
    environment,
) -> dict[str, object]:
    selected = []
    for command in commands.values():
        resolution = command.admission_resolution
        fingerprint = resolution.stat_fingerprint
        selected.append({
            "command_name": command.command_name,
            "argv_digest": command.argv_digest,
            "timeout_seconds": command.timeout_seconds,
            "authored_argv0": command.authored_argv[0],
            "invocation_path": resolution.invocation_path,
            "canonical_path": resolution.canonical_path,
            "stat_fingerprint": {
                "st_dev": fingerprint.st_dev,
                "st_ino": fingerprint.st_ino,
                "st_mode": fingerprint.st_mode,
                "st_size": fingerprint.st_size,
                "st_mtime_ns": fingerprint.st_mtime_ns,
            },
        })
    return {
        "environment_policy": "coordinator-inherited-environment-v1",
        "path_source": environment.path_source,
        "path_digest": environment.path_digest,
        "selected_commands": selected,
    }


def _execution_evidence(run, outcome, *, invocation_id: str, binding: dict[str, object], timeout_seconds: float,
                        observations: tuple[object, ...] = ()) -> dict[str, object]:
    hop = run.hops[-1]
    return {"hop_id": hop.hop_id, "invocation_id": invocation_id, **binding,
            "context": "fresh", "timeout_seconds": timeout_seconds,
            "returncode": outcome.returncode, "timed_out": outcome.timed_out,
            "candidate_result_present": outcome.candidate_result is not None,
            "outcome": hop.outcome, "base_head": hop.base_head, "end_head": hop.end_head,
            "actual_commits": list(hop.actual_commits),
            "trusted_observations": [
                observation_to_data(item)
                for item in observations
            ]}


def run_serial_hops(
    run, profile, repository: Path, components: dict[str, PromptComponent], *, state_root: Path,
    association, agent_operating_contract: AgentOperatingContract, task_payload: str,
    governing: dict[str, str], timeout_seconds: float,
    executable: str = "codex", adapter=None, durable_artifacts: tuple[str, ...] = (),
    durable_artifacts_by_phase: dict[str, tuple[str, ...]] | None = None,
    evidence_runner: EvidenceRunner = FAIL_CLOSED_EVIDENCE_RUNNER,
    evidence_commands: dict[str, PreparedEvidenceCommand] | None = None,
    progress: Callable[[object, str, object | None], None] | None = None,
):
    """Run the finite Slice-5B serial loop without reserving a future Hop."""
    if run.hop_limit > COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS:
        raise OperatorPreflightError("Slice-5B serial driver refuses a Hop limit above five")
    evidence: list[dict[str, object]] = []
    artifact_map = dict(durable_artifacts_by_phase or {})
    prior_observations: tuple[object, ...] = ()

    def publish_execution_evidence() -> None:
        atomic_write_json(
            checkout_state_root(state_root, association) / "execution-evidence.json",
            {"schema_version": 2, "attempts": evidence},
        )

    def stop_continuation(reason: str, question: str):
        stopped = replace(run, status=RunStatus.AWAITING_HUMAN,
                          stop_reason=reason, human_question=question)
        run_root = checkout_state_root(state_root, association)
        write_run_snapshot(run_root / "run.json", stopped)
        atomic_write_json(run_root / "checkpoint.json", checkpoint_projection(stopped, profile))
        return stopped, evidence

    while run.status is RunStatus.READY:
        if run.hop_used >= run.hop_limit:
            break
        if run.hop_used:
            expected_head = run.current_head
            try:
                validate_checkout(state_root, repository)
                preflight_hop(
                    repository,
                    expected_repository=repository,
                    expected_head=expected_head,
                )
            except (GitPreflightError, Slice4Error):
                return stop_continuation(
                    "inter_hop_git_validation_failed",
                    "Inter-Hop Git validation failed; validate current reality before continuation.",
                )
            if not _governing_inputs_unchanged(repository, expected_head, governing):
                return stop_continuation(
                    "governing_input_changed", "Pinned governing input changed after the prior Hop."
                )
        invocation_id = f"INV-{uuid.uuid4().hex}"
        binding = _execution_binding(adapter, executable=executable)
        phase_artifacts = (
            durable_artifacts
            if run.hop_used == 0 and durable_artifacts
            else artifact_map.get(run.phase, ())
        )
        hop_run = run_real_one_hop(
            run, profile, repository, components,
            agent_operating_contract=agent_operating_contract,
            state_root=state_root, association=association,
            task_payload=task_payload, invocation_id=invocation_id,
            timeout_seconds=timeout_seconds, executable=executable, adapter=adapter,
            durable_artifacts=phase_artifacts,
            context_items=_reconstructed_context(
                run, profile, repository, phase_artifacts, prior_observations
            ),
            evidence_runner=evidence_runner,
            evidence_commands=evidence_commands or {},
        )
        run = hop_run.run
        if progress is not None:
            progress(run, invocation_id, hop_run.outcome)
        if hop_run.outcome is not None and run.hops:
            observations = (
                hop_run.vertical.observations if hop_run.vertical is not None else ()
            )
            evidence.append(
                _execution_evidence(
                    run, hop_run.outcome, invocation_id=invocation_id,
                    binding=binding, timeout_seconds=timeout_seconds,
                    observations=observations,
                )
            )
            prior_observations = observations
            publish_execution_evidence()
    return run, evidence


def _summary(repository: Path, run, outcome, error: str | None = None, *, execution_evidence: list[dict[str, object]] | None = None) -> dict[str, object]:
    facts = observe_repository(repository)
    commits = []
    for hop in run.hops:
        for commit in hop.actual_commits:
            commits.append({"hash": commit, "subject": _git(repository, "show", "-s", "--format=%s", commit).strip()})
    provider = None if outcome is None else {
        "returncode": outcome.returncode, "timed_out": outcome.timed_out,
        "candidate_result_present": outcome.candidate_result is not None,
    }
    hop_evidence = execution_evidence or []
    hops = []
    for hop in run.hops:
        changed = _git(repository, "diff", "--name-only", f"{hop.base_head}..{hop.end_head}").splitlines()
        hops.append({"hop_id": hop.hop_id, "base_head": hop.base_head, "end_head": hop.end_head,
                     "actual_commits": list(hop.actual_commits), "changed_paths": changed,
                     "outcome": hop.outcome})
    return {"schema_version": 2, "work_item_id": run.work_item_id, "run_id": run.run_id,
            "invocation_id": getattr(outcome, "invocation_id", None), "baseline_head": run.baseline_head,
            "current_head": run.current_head,
            "final_head": facts.head, "run_status": run.status.value, "hop_used": run.hop_used,
            "hop_limit": run.hop_limit, "stop_reason": run.stop_reason, "human_question": run.human_question,
            "anomalies": list(run.anomalies), "provider": provider, "commits": commits,
            "changed_paths": _git(repository, "diff", "--name-only", f"{run.baseline_head}..{facts.head}").splitlines(),
            "worktree_clean": facts.clean and not facts.unresolved_submodules, "operator_error": error,
            "hops": hops, "execution_evidence": hop_evidence}


def _best_effort_failure_summary(
    repository: Path, run, *, invocation_id: str, checkout_id: str | None,
    outcome=None, error: Exception,
) -> dict[str, object]:
    """Record only operator-known facts when an attempt cannot finish normally."""
    provider = None if outcome is None else {
        "returncode": outcome.returncode, "timed_out": outcome.timed_out,
        "candidate_result_present": outcome.candidate_result is not None,
    }
    summary: dict[str, object] = {
        "schema_version": 2, "work_item_id": run.work_item_id, "run_id": run.run_id,
        "invocation_id": invocation_id, "baseline_head": run.baseline_head,
        "current_head": run.current_head,
        "run_status": run.status.value, "hop_used": run.hop_used,
        "hop_limit": run.hop_limit, "stop_reason": run.stop_reason,
        "human_question": run.human_question, "anomalies": list(run.anomalies),
        "provider": provider, "commits": [], "changed_paths": [],
        "operator_status": "failed", "operator_error": str(error),
    }
    if checkout_id is not None:
        summary["checkout_id"] = checkout_id
    try:
        facts = observe_repository(repository)
    except Exception:
        pass
    else:
        summary["final_head"] = facts.head
        summary["worktree_clean"] = facts.clean and not facts.unresolved_submodules
    return summary


def _write_failure_summary(state_root: Path, repository: Path, run, *, invocation_id: str,
                           checkout_id: str | None, outcome, error: Exception) -> None:
    """Never obscure the attempt failure with an evidence-writing failure."""
    try:
        atomic_write_json(
            state_root / "operator-summary.json",
            _best_effort_failure_summary(
                repository, run, invocation_id=invocation_id, checkout_id=checkout_id,
                outcome=outcome, error=error,
            ),
        )
    except Exception:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        (
            agent_operating_contract, repository, state_root, paths, texts, item, profile, policy,
            components, durable, legacy_durable, governing, active_evidence_runner,
            prepared_evidence, evidence_environment,
        ) = _preflight(args, evidence_runner=PRODUCTION_EVIDENCE_RUNNER)
        run_id = f"RUN-{uuid.uuid4().hex}"
        run = new_run(item, profile, policy, run_id=run_id, baseline_head=args.expected_head)
        state_root.mkdir(parents=True)
        plan = {"schema_version": 1, "repository": str(repository), "expected_baseline": args.expected_head,
                "inputs": {**paths, "pinned_revision": args.expected_head},
                "components": {key: {"path": value.path, "digest": value.digest} for key, value in components.items()},
                "durable_artifacts": {
                    phase: list(paths) for phase, paths in durable.items()
                }, "legacy_durable_artifacts": list(legacy_durable),
                "state_root": str(state_root), "run_id": run_id,
                "executable": args.executable, "timeout_seconds": args.timeout_seconds,
                "max_autonomous_hops": item.max_autonomous_hops, "retry": False,
                "agent_operating_contract": agent_operating_contract.provenance(),
                "governing_inputs": governing,
                "evidence": _prepared_evidence_plan(
                    prepared_evidence, evidence_environment
                )}
        atomic_write_json(state_root / "operator-plan.json", plan)
    except SystemExit as error:
        return 0 if error.code == 0 else 2
    except Exception as error:
        print(f"OPERATOR ERROR: {error}", file=sys.stderr)
        return 2

    # From checkout registration onward a provider-capable attempt exists.  Do not
    # reclassify failures by exception type after this boundary.
    association = None
    latest_run = run
    latest_invocation_id: str | None = None
    latest_outcome = None

    def remember_progress(observed_run, invocation_id: str, outcome) -> None:
        nonlocal latest_run, latest_invocation_id, latest_outcome
        latest_run = observed_run
        latest_invocation_id = invocation_id
        latest_outcome = outcome

    try:
        association = register_checkout(state_root, repository)
        final_run, execution_evidence = run_serial_hops(
            run, profile, repository, components, state_root=state_root, association=association,
            agent_operating_contract=agent_operating_contract,
            task_payload=texts["task"], governing=governing, timeout_seconds=args.timeout_seconds,
            executable=args.executable, durable_artifacts=legacy_durable,
            durable_artifacts_by_phase=durable,
            evidence_runner=active_evidence_runner,
            evidence_commands=prepared_evidence,
            progress=remember_progress,
        )
        latest_run = final_run
        summary = _summary(repository, final_run, None, execution_evidence=execution_evidence)
        summary["invocation_id"] = execution_evidence[-1]["invocation_id"] if execution_evidence else None
        summary["checkout_id"] = association.checkout_id
        atomic_write_json(state_root / "operator-summary.json", summary)
        print(f"STATE ROOT: {state_root}\nRUN ROOT: {state_root / 'checkouts' / association.checkout_id}\nSUMMARY: {state_root / 'operator-summary.json'}")
        last_run_commit = next((commit for hop in reversed(final_run.hops) for commit in reversed(hop.actual_commits)), "none")
        print(f"RUN STATUS: {final_run.status.value}\nHOP USED: {final_run.hop_used}\nLAST RUN COMMIT: {last_run_commit}")
        facts = observe_repository(repository)
        print(f"CURRENT HEAD: {facts.head}\nWORKTREE CLEAN: {facts.clean and not facts.unresolved_submodules}")
        return 0 if final_run.status.value == "completed" and facts.clean and not facts.unresolved_submodules else 1
    except Exception as error:
        _write_failure_summary(
            state_root, repository, latest_run,
            invocation_id=latest_invocation_id or "not_dispatched",
            checkout_id=None if association is None else association.checkout_id,
            outcome=latest_outcome, error=error,
        )
        print(f"OPERATOR ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
