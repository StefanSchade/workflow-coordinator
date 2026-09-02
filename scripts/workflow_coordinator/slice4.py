"""Bounded Slice-4 local Codex boundary for the one disposable proof.

This is intentionally a concrete Codex CLI call, not an adapter registry.  Its
state root is external to the repository passed to Codex.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from .adapter import (
    AgentExecutionOutcome,
    AgentExecutionRequest,
    ExecutionNotStartedError,
    ExecutionResultValidationError,
    Invocation,
    ROUTING_RESULT_ENVELOPE_CONTRACT,
)
from .codex_adapter import CodexCliAdapter, CodexCliError
from .git_facts import GitPreflightError, observe_hop, preflight_hop
from .model import DispatchMarker, Run, WorkflowProfile
from .persistence import (
    atomic_write_json, checkpoint_projection, recover_uncertain_dispatch,
    write_dispatch_marker, write_run_snapshot,
)
from .prompt import PromptComponent
from .vertical import VerticalRun, run_one_hop


class Slice4Error(RuntimeError):
    """The real-agent boundary cannot safely proceed."""


@dataclass(frozen=True)
class CheckoutAssociation:
    checkout_id: str
    canonical_path: str
    git_dir: str
    common_dir: str


@dataclass(frozen=True)
class RealHopRun:
    """A completed vertical Hop or an honestly unreduced uncertain recovery."""

    vertical: VerticalRun | None
    run: Run
    checkpoint: dict[str, object]
    outcome: AgentExecutionOutcome | None

    @property
    def reduction(self):
        return self.vertical.reduction if self.vertical is not None else None


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(("git", "-C", str(repository), *args), text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode:
        raise Slice4Error(completed.stderr.strip() or "Git fact lookup failed")
    return completed.stdout.strip()


def _facts(repository: Path) -> tuple[str, str, str]:
    root = Path(_git(repository, "rev-parse", "--show-toplevel")).resolve()
    git_dir = Path(_git(root, "rev-parse", "--absolute-git-dir")).resolve()
    common_dir = Path(_git(root, "rev-parse", "--git-common-dir"))
    if not common_dir.is_absolute():
        common_dir = (root / common_dir).resolve()
    return str(root), str(git_dir), str(common_dir)


def _require_external_state_root(state_root: Path, repository: Path) -> None:
    """Reject both nesting directions before coordinator state can be written."""
    root = state_root.resolve()
    checkout = Path(_facts(repository)[0])
    if root == checkout or checkout in root.parents or root in checkout.parents:
        raise Slice4Error("trusted state root must be disjoint from agent workspace")


def register_checkout(state_root: Path, repository: Path) -> CheckoutAssociation:
    """Create the one opaque association, or validate its existing exact facts."""
    state_root = state_root.resolve()
    _require_external_state_root(state_root, repository)
    canonical_path, git_dir, common_dir = _facts(repository)
    mapping = state_root / "checkout-association.json"
    if mapping.exists():
        return validate_checkout(state_root, repository)
    association = CheckoutAssociation(str(uuid.uuid4()), canonical_path, git_dir, common_dir)
    atomic_write_json(mapping, {"schema_version": 1, **association.__dict__})
    (state_root / "checkouts" / association.checkout_id).mkdir(parents=True, exist_ok=True)
    return association


def validate_checkout(state_root: Path, repository: Path) -> CheckoutAssociation:
    """Fail closed; a moved checkout needs explicit maintainer re-registration."""
    state_root = state_root.resolve()
    _require_external_state_root(state_root, repository)
    mapping = state_root / "checkout-association.json"
    try:
        data = json.loads(mapping.read_text(encoding="utf-8"))
        expected = CheckoutAssociation(**{key: data[key] for key in CheckoutAssociation.__dataclass_fields__})
    except (FileNotFoundError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise Slice4Error("external checkout association is absent or malformed") from error
    if (
        set(data) != {"schema_version", *CheckoutAssociation.__dataclass_fields__}
        or data.get("schema_version") != 1
        or not all(isinstance(value, str) and value for value in expected.__dict__.values())
    ):
        raise Slice4Error("external checkout association has unsupported schema")
    try:
        uuid.UUID(expected.checkout_id)
    except ValueError as error:
        raise Slice4Error("external checkout association has malformed checkout ID") from error
    actual = _facts(repository)
    if actual != (expected.canonical_path, expected.git_dir, expected.common_dir):
        raise Slice4Error("checkout/Git association mismatch; explicit maintainer re-registration required")
    return expected


def checkout_state_root(state_root: Path, association: CheckoutAssociation) -> Path:
    return state_root.resolve() / "checkouts" / association.checkout_id


def invoke_codex(
    *, repository: Path, state_root: Path, association: CheckoutAssociation,
    prompt: str, expected: Invocation, timeout_seconds: float, executable: str = "codex",
) -> AgentExecutionOutcome:
    """Compatibility wrapper for the concrete Codex execution port."""
    _require_external_state_root(state_root, repository)
    validate_checkout(state_root, repository)
    run_root = checkout_state_root(state_root, association)
    try:
        return CodexCliAdapter(run_root, executable=executable).execute(
            AgentExecutionRequest(expected, prompt, repository, ROUTING_RESULT_ENVELOPE_CONTRACT, timeout_seconds)
        )
    except CodexCliError as error:
        raise Slice4Error(str(error)) from error


def run_real_one_hop(
    run: Run, profile: WorkflowProfile, repository: Path, components: dict[str, PromptComponent],
    *, state_root: Path, association: CheckoutAssociation, task_payload: str,
    invocation_id: str, timeout_seconds: float, executable: str = "codex",
    durable_artifacts: tuple[str, ...] = (),
) -> RealHopRun:
    """Plug the concrete process boundary into the sole accepted vertical path."""
    hop_id = f"H{run.hop_used + 1:03d}"
    try:
        preflight_hop(repository, expected_repository=repository, expected_head=run.current_head)
    except GitPreflightError as error:
        raise Slice4Error(str(error)) from error
    if validate_checkout(state_root, repository) != association:
        raise Slice4Error("provided checkout association does not match trusted state")
    run_root = checkout_state_root(state_root, association)
    marker = DispatchMarker(1, run.run_id, hop_id, run.hop_used + 1, invocation_id, run.current_head, 1)
    dispatch_path = run_root / "dispatch.json"
    write_run_snapshot(run_root / "run.json", run)
    write_dispatch_marker(dispatch_path, marker)
    adapter = CodexCliAdapter(run_root, executable=executable)

    def recover_uncertain(outcome: AgentExecutionOutcome | None) -> RealHopRun:
        """Retain dispatch authority unless execution was proven absent."""
        delta = observe_hop(
            repository, base_head=run.current_head,
            allowed_paths=profile.phases[run.phase].authorized_scope,
        )
        recovered = recover_uncertain_dispatch(
            run, marker, current_head=delta.end_head, actual_commits=delta.actual_commits,
        )
        if delta.anomalies:
            recovered = replace(recovered, anomalies=(*recovered.anomalies, *delta.anomalies))
        checkpoint = checkpoint_projection(recovered, profile)
        write_run_snapshot(run_root / "run.json", recovered)
        atomic_write_json(run_root / "checkpoint.json", checkpoint)
        return RealHopRun(None, recovered, checkpoint, outcome)

    try:
        vertical = run_one_hop(
            run, profile, repository, adapter, components, task_payload=task_payload,
            invocation_id=invocation_id, durable_artifacts=durable_artifacts,
            execution_timeout_seconds=timeout_seconds,
        )
    except ExecutionResultValidationError as error:
        return recover_uncertain(error.outcome)
    except CodexCliError as error:
        dispatch_path.unlink(missing_ok=True)
        raise Slice4Error(str(error)) from error
    except ExecutionNotStartedError:
        dispatch_path.unlink(missing_ok=True)
        raise
    except Exception:
        return recover_uncertain(None)
    write_run_snapshot(run_root / "run.json", vertical.reduction.run)
    atomic_write_json(run_root / "checkpoint.json", vertical.checkpoint)
    dispatch_path.unlink(missing_ok=True)
    return RealHopRun(vertical, vertical.reduction.run, vertical.checkpoint, vertical.execution_outcome)
