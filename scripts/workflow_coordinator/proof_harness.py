"""File-based, disposable proof harness for a future Slice-4A operator run.

This module only prepares the fixed proof and records its observable outcome.  It
delegates invocation, dispatch handling, and reduction to :mod:`slice4`; it is
not a second coordinator and does not grant a live-proof authorization.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

if __package__ in {None, ""}:  # Allow the documented file-based launcher.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.workflow_coordinator.model import (
    DurableArtifact,
    Phase,
    RepositoryPolicy,
    Route,
    RouteEffect,
    WorkItem,
    WorkflowProfile,
    new_run,
)
from scripts.workflow_coordinator.persistence import atomic_write_json
from scripts.workflow_coordinator.prompt import PromptComponent
from scripts.workflow_coordinator.slice4 import RealHopRun, Slice4Error, register_checkout, run_real_one_hop


WORK_ITEM_ID = "WFC-S4-PROOF"
HOP_ID = "H001"
EXPECTED_SUBJECT = "docs(workflow): WFC-S4-PROOF H001 mark fixture"
DIAGNOSTIC_LIMIT_CHARS = 16_384
ANCESTOR_LIMIT = 8


@dataclass(frozen=True)
class ProofHarnessResult:
    """Paths and final outcome for an operator to inspect from durable files."""

    proof_root: Path
    summary_path: Path
    real_hop: RealHopRun | None
    error: str | None


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *args), text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise Slice4Error(completed.stderr.strip() or "Git fact lookup failed")
    return completed.stdout.strip()


def _proof_profile() -> WorkflowProfile:
    phase = Phase(
        role="worker",
        role_components=("worker",),
        modifier_components=(),
        repository_policy_components=("policy",),
        durable_artifact=DurableArtifact.NONE,
        authorized_scope=("PROOF.md",),
        authorized_actions=("edit", "commit"),
        human_gates=(),
        evidence=("git_version",),
        review=None,
        routes={"complete": Route(RouteEffect.COMPLETE)},
    )
    return WorkflowProfile(1, "slice4-disposable-proof", "proof-r1", {"proof": phase})


def _proof_components() -> dict[str, PromptComponent]:
    return {
        "worker": PromptComponent("worker", "proof-runner", "proof-r1", "Work only on PROOF.md."),
        "policy": PromptComponent("policy", "proof-runner", "proof-r1", "Create exactly one commit."),
    }


def _prepare_repository(repository: Path) -> str:
    repository.mkdir()
    subprocess.run(("git", "init", str(repository)), check=True, capture_output=True)
    for key, value in (("user.email", "proof@example.invalid"), ("user.name", "Slice 4 proof")):
        subprocess.run(("git", "-C", str(repository), "config", key, value), check=True, capture_output=True)
    (repository / "PROOF.md").write_text("WFC-S4-PROOF: pending marker\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(repository), "add", "PROOF.md"), check=True, capture_output=True)
    subprocess.run(
        ("git", "-C", str(repository), "commit", "-m", "docs(workflow): WFC-S4-PROOF baseline"),
        check=True,
        capture_output=True,
    )
    return _git(repository, "rev-parse", "HEAD")


def _bounded(value: str) -> str:
    return value[-DIAGNOSTIC_LIMIT_CHARS:]


def _process_name(pid: int, read_text: Callable[[Path], str]) -> str | None:
    try:
        return _bounded(read_text(Path(f"/proc/{pid}/comm")).strip()) or None
    except OSError:
        return None


def _parent_pid(pid: int, read_text: Callable[[Path], str]) -> int | None:
    """Read only the PPID field needed for the bounded ancestry chain."""
    try:
        fields = read_text(Path(f"/proc/{pid}/stat")).rsplit(") ", 1)[1].split()
        return int(fields[1])
    except (IndexError, OSError, ValueError):
        return None


def collect_launch_provenance(
    *, pid: int | None = None, ppid: int | None = None, read_text: Callable[[Path], str] = Path.read_text,
    environ: dict[str, str] | None = None,
) -> dict[str, object]:
    """Collect small factual launch context, without environment values or argv."""
    current_pid = os.getpid() if pid is None else pid
    current_ppid = os.getppid() if ppid is None else ppid
    environment = os.environ if environ is None else environ
    ancestors: list[dict[str, object]] = []
    observed_pid = current_pid
    observed_ppid = current_ppid
    for _ in range(ANCESTOR_LIMIT):
        ancestors.append({"pid": observed_pid, "process_name": _process_name(observed_pid, read_text)})
        if observed_pid <= 1 or observed_ppid <= 1:
            break
        observed_pid = observed_ppid
        observed_ppid = _parent_pid(observed_pid, read_text) or 1
    return {
        "schema_version": 1,
        "scope": "bounded host/operator launch context; not cryptographic attestation",
        "pid": current_pid,
        "ppid": current_ppid,
        "ancestor_limit": ANCESTOR_LIMIT,
        "ancestors": ancestors,
        "environment_presence": {
            "codex_related": any("CODEX" in key.upper() for key in environment),
            "claude_related": any("CLAUDE" in key.upper() or "ANTHROPIC" in key.upper() for key in environment),
            "openai_related": any("OPENAI" in key.upper() for key in environment),
        },
        "environment_values_recorded": False,
        "argv_recorded": False,
    }


def _git_facts(repository: Path, initial_head: str) -> dict[str, object]:
    final_head = _git(repository, "rev-parse", "HEAD")
    git_dir = Path(_git(repository, "rev-parse", "--absolute-git-dir"))
    commits = _git(repository, "rev-list", "--reverse", f"{initial_head}..{final_head}").splitlines()
    return {
        "initial_head": initial_head,
        "final_head": final_head,
        "actual_commit_range": f"{initial_head}..{final_head}",
        "actual_commits": commits,
        "actual_commit_subjects": [_git(repository, "show", "-s", "--format=%s", commit) for commit in commits],
        "git_status": _git(repository, "status", "--porcelain"),
        "remotes": _git(repository, "remote").splitlines(),
        "tracked_paths": _git(repository, "ls-files").splitlines(),
        "changed_paths": _git(repository, "diff", "--name-only", f"{initial_head}..{final_head}").splitlines(),
        "git_lock_paths": sorted(str(path.relative_to(repository)) for path in git_dir.rglob("*.lock")),
        "proof_md_final_content": _bounded((repository / "PROOF.md").read_text(encoding="utf-8")),
    }


def run_disposable_proof(
    proof_root: Path, *, executable: str = "codex", timeout_seconds: float = 900.0,
    provenance: dict[str, object] | None = None,
) -> ProofHarnessResult:
    """Prepare one new fixed proof attempt and persist evidence for every outcome."""
    root = proof_root.resolve()
    if root.exists():
        raise Slice4Error("proof root must be a new path")
    root.mkdir(parents=True)
    repository = root / "repo"
    state_root = root / "trusted-state"
    initial_head = _prepare_repository(repository)
    association = register_checkout(state_root, repository)
    profile = _proof_profile()
    work_item = WorkItem(
        1, WORK_ITEM_ID, "future maintainer authorization required", "slice4-disposable-proof",
        "disposable-proof", ("disposable-proof",), "proof", 1,
    )
    policy = RepositoryPolicy(1, "slice4-proof-policy", "proof-r1", ("disposable-proof",), ("edit", "commit"), ("git_version",))
    run_id = f"RUN-{uuid.uuid4().hex}"
    invocation_id = f"INV-{uuid.uuid4().hex}"
    run = new_run(work_item, profile, policy, run_id=run_id, baseline_head=initial_head)
    real_hop: RealHopRun | None = None
    error: str | None = None
    try:
        real_hop = run_real_one_hop(
            run, profile, repository, _proof_components(), state_root=state_root, association=association,
            task_payload=("Change PROOF.md to `WFC-S4-PROOF: completed marker`, commit exactly once with "
                          f"subject `{EXPECTED_SUBJECT}`, then return route `complete`. "
                          f"The result invocation_id is `{invocation_id}`."),
            invocation_id=invocation_id, timeout_seconds=timeout_seconds, executable=executable,
        )
    except Exception as caught:  # The summary, not terminal output, remains the operator record.
        error = f"{type(caught).__name__}: {caught}"
    outcome = real_hop.outcome if real_hop is not None else None
    final_run = real_hop.run if real_hop is not None else run
    summary = {
        "schema_version": 1,
        "proof_root": str(root),
        "repository_path": str(repository),
        "trusted_state_path": str(state_root),
        "checkout_id": association.checkout_id,
        "work_item_id": WORK_ITEM_ID,
        "hop_id": HOP_ID,
        "max_autonomous_hops": 1,
        "run_id": run_id,
        "invocation_id": invocation_id,
        "expected_commit_subject": EXPECTED_SUBJECT,
        **_git_facts(repository, initial_head),
        "process_returncode": outcome.returncode if outcome else None,
        "timed_out": outcome.timed_out if outcome else False,
        "acceptance": outcome.acceptance if outcome else "not_started",
        "stdout_tail": outcome.stdout if outcome else "",
        "stderr_tail": outcome.stderr if outcome else "",
        "diagnostic_limit_chars": DIAGNOSTIC_LIMIT_CHARS,
        "vertical_success": real_hop is not None and real_hop.vertical is not None,
        "final_run_status": final_run.status.value,
        "hop_used": final_run.hop_used,
        "stop_reason": final_run.stop_reason,
        "dispatch_marker_present": (state_root / "checkouts" / association.checkout_id / "dispatch.json").exists(),
        "unexpected_mutation_or_anomaly_facts": list(final_run.anomalies),
        "operator_error": error,
        "launch_provenance_path": str(root / "launch-provenance.json"),
    }
    atomic_write_json(root / "launch-provenance.json", provenance or collect_launch_provenance())
    summary_path = root / "proof-summary.json"
    atomic_write_json(summary_path, summary)
    return ProofHarnessResult(root, summary_path, real_hop, error)


def proof_exit_status(result: ProofHarnessResult) -> int:
    """Return shell success only for an established, completed proof."""
    real_hop = result.real_hop
    if (
        result.error is None
        and real_hop is not None
        and real_hop.vertical is not None
        and real_hop.outcome is not None
        and real_hop.outcome.acceptance == "established"
        and real_hop.run.status.value == "completed"
    ):
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fixed disposable Slice-4A proof harness.")
    parser.add_argument("--proof-root", type=Path, required=True, help="new durable directory for this attempt")
    parser.add_argument("--executable", default="codex", help="provider executable; requires separate live authorization")
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    args = parser.parse_args()
    result = run_disposable_proof(args.proof_root, executable=args.executable, timeout_seconds=args.timeout_seconds)
    print(result.summary_path)
    return proof_exit_status(result)


if __name__ == "__main__":
    raise SystemExit(main())
