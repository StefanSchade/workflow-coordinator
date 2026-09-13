#!/usr/bin/env python3
"""Declarative, one-shot N=1 entry point for the accepted coordinator path."""

from __future__ import annotations

import sys

# Direct ``python scripts/workflow_coordinator/operator.py`` execution would otherwise
# shadow the standard-library ``operator`` module with this file during interpreter startup.
if __package__ in {None, ""}:
    sys.path.pop(0)
    sys.path.insert(0, __file__.rsplit("/scripts/workflow_coordinator/", 1)[0])

import argparse
import hashlib
import re
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Sequence

from scripts.workflow_coordinator.git_facts import GitPreflightError, observe_repository, preflight_hop, repository_identity
from scripts.workflow_coordinator.model import DurableArtifact, RunStatus, new_run
from scripts.workflow_coordinator.persistence import atomic_write_json, checkpoint_projection, write_run_snapshot
from scripts.workflow_coordinator.prompt import ContextItem, PromptComponent
from scripts.workflow_coordinator.serialization import (
    ValidationError,
    component_manifest_from_yaml,
    repository_policy_from_yaml,
    work_item_from_yaml,
    workflow_profile_from_yaml,
)
from scripts.workflow_coordinator.slice4 import Slice4Error, checkout_state_root, register_checkout, run_real_one_hop, validate_checkout


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


def _preflight(args: argparse.Namespace):
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
    for phase_key, phase_value in profile.phases.items():
        excess = sorted(set(phase_value.authorized_actions) - policy_actions)
        if excess:
            raise OperatorPreflightError(
                f"profile phase {phase_key} authorizes actions outside repository policy: {', '.join(excess)}"
            )
        phase_scope = _normalized_scope(phase_value.authorized_scope, context=f"profile phase {phase_key} authorized_scope")
        if any(not _within_scope(path, work_scope) for path in phase_scope):
            raise OperatorPreflightError(f"profile phase {phase_key} authorized scope exceeds WorkItem.scope")
    if work_item.max_autonomous_hops > 2:
        raise OperatorPreflightError("Slice-5A supports at most two autonomous Hops")
    if work_item.initial_phase not in profile.phases:
        raise OperatorPreflightError(f"WorkItem.initial_phase is absent from supplied profile: {work_item.initial_phase}")
    phase = profile.phases[work_item.initial_phase]
    durable_artifacts = tuple(_relative_path(path, context="durable artifact") for path in args.durable_artifact)
    if len(set(durable_artifacts)) != len(durable_artifacts):
        raise OperatorPreflightError("durable artifact arguments must not contain duplicates")
    if any(not _within_scope(path, phase.authorized_scope) for path in durable_artifacts):
        raise OperatorPreflightError("durable artifact is outside the initial phase authorized scope")
    if phase.durable_artifact is DurableArtifact.REQUIRED and not durable_artifacts:
        raise OperatorPreflightError("initial phase requires at least one durable artifact")
    if phase.durable_artifact is DurableArtifact.NONE and durable_artifacts:
        raise OperatorPreflightError("initial phase allows no durable-artifact arguments")
    components: dict[str, PromptComponent] = {}
    governing = {path: _governing_digest(text) for path, text in texts.items()}
    for component_id in _selected_ids(profile):
        reference = manifest.get(component_id)
        if reference is None:
            raise OperatorPreflightError(f"selected component ID is absent from manifest: {component_id}")
        path = _relative_path(reference.path, context=f"component {component_id} path")
        content = _pinned_text(repository, args.expected_head, path, context=f"component {component_id}")
        components[component_id] = PromptComponent(component_id, path, args.expected_head, content)
        governing[path] = _governing_digest(content)
    if args.timeout_seconds <= 0:
        raise OperatorPreflightError("timeout seconds must be positive")
    return repository, state_root, input_paths, texts, work_item, profile, policy, components, durable_artifacts, governing


def _governing_inputs_unchanged(repository: Path, expected_head: str, governing: dict[str, str]) -> bool:
    """Re-read every pre-H001 repository input at B; paths must remain tracked."""
    try:
        return all(
            _governing_digest(_pinned_text(repository, expected_head, path, context="governing input")) == digest
            for path, digest in governing.items()
        )
    except OperatorPreflightError:
        return False


def _h1_context(run) -> tuple[ContextItem, ...]:
    hop = run.hops[-1]
    return (ContextItem(
        reference=f"Hop {hop.hop_id} coordinator summary",
        content=(f"prior_hop={hop.hop_id}\nprior_outcome={hop.outcome}\n"
                 f"prior_range={hop.base_head}..{hop.end_head}\nprior_summary={hop.summary}"),
    ),)


def _execution_evidence(run, outcome, *, invocation_id: str, executable: str, timeout_seconds: float) -> dict[str, object]:
    hop = run.hops[-1]
    return {"hop_id": hop.hop_id, "invocation_id": invocation_id,
            "adapter": "local" if executable == "local" else "codex_cli",
            "executable": executable, "model": None, "reasoning": None,
            "context": "fresh", "timeout_seconds": timeout_seconds,
            "returncode": outcome.returncode, "timed_out": outcome.timed_out,
            "outcome": hop.outcome, "base_head": hop.base_head, "end_head": hop.end_head,
            "actual_commits": list(hop.actual_commits)}


def run_serial_hops(
    run, profile, repository: Path, components: dict[str, PromptComponent], *, state_root: Path,
    association, task_payload: str, governing: dict[str, str], timeout_seconds: float,
    executable: str = "codex", adapter=None, durable_artifacts: tuple[str, ...] = (),
):
    """The Slice-5A serial driver: one H001 and, only after a fresh gate, H002."""
    evidence: list[dict[str, object]] = []

    def publish_execution_evidence() -> None:
        atomic_write_json(
            checkout_state_root(state_root, association) / "execution-evidence.json",
            {"schema_version": 1, "attempts": evidence},
        )

    invocation_id = f"INV-{uuid.uuid4().hex}"
    first = run_real_one_hop(
        run, profile, repository, components, state_root=state_root, association=association,
        task_payload=task_payload, invocation_id=invocation_id, timeout_seconds=timeout_seconds,
        executable=executable, adapter=adapter, durable_artifacts=durable_artifacts,
    )
    run = first.run
    if first.outcome is not None and run.hops:
        evidence.append(_execution_evidence(run, first.outcome, invocation_id=invocation_id,
                                            executable=executable, timeout_seconds=timeout_seconds))
        publish_execution_evidence()
    if run.hop_limit != 2 or run.status is not RunStatus.READY:
        return run, evidence
    # H001 has already completed its independent publication sequence.  No
    # speculative route or context is retained: validate all facts again first.
    def stop_continuation(reason: str, question: str):
        stopped = replace(run, status=RunStatus.AWAITING_HUMAN,
                          stop_reason=reason, human_question=question)
        run_root = checkout_state_root(state_root, association)
        write_run_snapshot(run_root / "run.json", stopped)
        atomic_write_json(run_root / "checkpoint.json", checkpoint_projection(stopped, profile))
        return stopped, evidence

    expected_head = run.current_head
    try:
        validate_checkout(state_root, repository)
        preflight_hop(repository, expected_repository=repository, expected_head=expected_head)
    except (GitPreflightError, Slice4Error):
        return stop_continuation(
            "inter_hop_git_validation_failed",
            "Inter-Hop Git validation failed; validate current reality before continuation.",
        )
    if not _governing_inputs_unchanged(repository, expected_head, governing):
        return stop_continuation(
            "governing_input_changed", "Pinned governing input changed after H001."
        )
    invocation_id = f"INV-{uuid.uuid4().hex}"
    second = run_real_one_hop(
        run, profile, repository, components, state_root=state_root, association=association,
        task_payload=task_payload, invocation_id=invocation_id, timeout_seconds=timeout_seconds,
        executable=executable, adapter=adapter, context_items=_h1_context(run),
    )
    run = second.run
    if second.outcome is not None and run.hops:
        evidence.append(_execution_evidence(run, second.outcome, invocation_id=invocation_id,
                                            executable=executable, timeout_seconds=timeout_seconds))
        publish_execution_evidence()
    return run, evidence


def _summary(repository: Path, run, outcome, error: str | None = None, *, execution_evidence: list[dict[str, object]] | None = None) -> dict[str, object]:
    facts = observe_repository(repository)
    commits = []
    for commit in run.hops[-1].actual_commits if run.hops else ():
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
    return {"schema_version": 1, "work_item_id": run.work_item_id, "run_id": run.run_id,
            "invocation_id": getattr(outcome, "invocation_id", None), "baseline_head": run.baseline_head,
            "final_head": facts.head, "run_status": run.status.value, "hop_used": run.hop_used,
            "hop_limit": run.hop_limit, "stop_reason": run.stop_reason, "human_question": run.human_question,
            "anomalies": list(run.anomalies), "provider": provider, "commits": commits,
            "changed_paths": list(run.hops[-1].actual_commits and _git(repository, "diff", "--name-only", f"{run.baseline_head}..{facts.head}").splitlines() if run.hops else []),
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
        "schema_version": 1, "work_item_id": run.work_item_id, "run_id": run.run_id,
        "invocation_id": invocation_id, "baseline_head": run.baseline_head,
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
        repository, state_root, paths, texts, item, profile, policy, components, durable, governing = _preflight(args)
        run_id = f"RUN-{uuid.uuid4().hex}"
        run = new_run(item, profile, policy, run_id=run_id, baseline_head=args.expected_head)
        state_root.mkdir(parents=True)
        plan = {"schema_version": 1, "repository": str(repository), "expected_baseline": args.expected_head,
                "inputs": {**paths, "pinned_revision": args.expected_head},
                "components": {key: {"path": value.path, "digest": value.digest} for key, value in components.items()},
                "durable_artifacts": list(durable), "state_root": str(state_root), "run_id": run_id,
                "executable": args.executable, "timeout_seconds": args.timeout_seconds,
                "max_autonomous_hops": item.max_autonomous_hops, "retry": False,
                "governing_inputs": governing}
        atomic_write_json(state_root / "operator-plan.json", plan)
    except SystemExit as error:
        return 0 if error.code == 0 else 2
    except Exception as error:
        print(f"OPERATOR ERROR: {error}", file=sys.stderr)
        return 2

    # From checkout registration onward a provider-capable attempt exists.  Do not
    # reclassify failures by exception type after this boundary.
    association = None
    real = None
    try:
        association = register_checkout(state_root, repository)
        final_run, execution_evidence = run_serial_hops(
            run, profile, repository, components, state_root=state_root, association=association,
            task_payload=texts["task"], governing=governing, timeout_seconds=args.timeout_seconds,
            executable=args.executable, durable_artifacts=durable,
        )
        real = None
        summary = _summary(repository, final_run, None, execution_evidence=execution_evidence)
        summary["invocation_id"] = execution_evidence[-1]["invocation_id"] if execution_evidence else None
        summary["checkout_id"] = association.checkout_id
        atomic_write_json(state_root / "operator-summary.json", summary)
        print(f"STATE ROOT: {state_root}\nRUN ROOT: {state_root / 'checkouts' / association.checkout_id}\nSUMMARY: {state_root / 'operator-summary.json'}")
        print(f"RUN STATUS: {final_run.status.value}\nHOP USED: {final_run.hop_used}\nLAST COMMIT: {(final_run.hops[-1].actual_commits[-1] if final_run.hops and final_run.hops[-1].actual_commits else 'none')}")
        facts = observe_repository(repository)
        print(f"CURRENT HEAD: {facts.head}\nWORKTREE CLEAN: {facts.clean and not facts.unresolved_submodules}")
        return 0 if final_run.status.value == "completed" and facts.clean and not facts.unresolved_submodules else 1
    except Exception as error:
        known_run = real.run if real is not None else run
        known_outcome = real.outcome if real is not None else None
        _write_failure_summary(
            state_root, repository, known_run, invocation_id="unknown",
            checkout_id=None if association is None else association.checkout_id,
            outcome=known_outcome, error=error,
        )
        print(f"OPERATOR ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
