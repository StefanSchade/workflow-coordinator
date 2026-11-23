#!/usr/bin/env python3
"""A deterministic stand-in for the real engine's bare-flag entry point.

Accepts the *same* argv shape ``frontdoor.lifecycle`` gives the real engine
(``--repository --state-root --expected-head --work-item --profile --policy
--components --task --execution-bindings --timeout-seconds --executable``)
and reproduces exactly the documented persistence contract -- exclusive
state-root creation, ``operator-plan.json``, checkout registration, then
``run.json``/a dispatch marker -- up to a point selected by the
``WFC_FAKE_ENGINE_MODE`` environment variable, so lifecycle crash-matrix
tests can pin an exact, repeatable interruption point without depending on
a real provider or the real reducer. This is test-only fixture code; the
production lifecycle controller always launches the real, unchanged engine.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from workflow_coordinator.model import Run, RunStatus  # noqa: E402
from workflow_coordinator.persistence import atomic_write_json, write_run_snapshot, write_dispatch_marker  # noqa: E402
from workflow_coordinator.model import DispatchMarker  # noqa: E402
from workflow_coordinator.slice4 import register_checkout, checkout_state_root  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--work-item", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--components", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--execution-bindings", required=True)
    parser.add_argument("--timeout-seconds", default="900")
    parser.add_argument("--executable", default="codex")
    return parser.parse_args()


def _leave_dirty_worktree_content(repository: Path) -> None:
    """BP4b opt-in only: write one file simulating unadopted worktree work.

    Off unless ``WFC_FAKE_ENGINE_DIRTY_FILE`` is set, so every pre-existing
    BP4a fixture behavior (a clean worktree at classification time) is
    unchanged by default.
    """

    relative = os.environ.get("WFC_FAKE_ENGINE_DIRTY_FILE")
    if not relative:
        return
    target = repository / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("fake engine: unadopted worktree work\n", encoding="utf-8")


def main() -> int:
    mode = os.environ.get("WFC_FAKE_ENGINE_MODE", "exit_immediately")
    args = _parse_args()
    repository = Path(args.repository).resolve()
    state_root = Path(args.state_root).resolve()

    if mode == "exit_immediately":
        return 9

    state_root.mkdir(parents=True)
    atomic_write_json(state_root / "operator-plan.json", {
        "schema_version": 2, "coordinator": "fake-engine",
        # The real engine (operator.py) always records the exact argv value
        # it was launched with here. The installed product lifecycle passes
        # its own per-checkout cached envelope file (never named
        # ``execution-bindings.yaml``, which only self-development's own
        # preparation sequence writes) -- reproducing that shape is load-
        # bearing: ``frontdoor.archive.resolve_ops_dir`` must not mistake it
        # for recorded-but-missing OPS evidence (see BP6c's identity note).
        "execution_bindings_source": args.execution_bindings,
    })
    if mode == "mkdir_only":
        return 9

    association = register_checkout(state_root, repository)
    run_root = checkout_state_root(state_root, association)

    run_id = os.environ.get("WFC_FAKE_ENGINE_RUN_ID", "RUN-fake-engine")
    run = Run(
        schema_version=2, run_id=run_id, work_item_id="WI-FAKE", profile_id="fake-profile",
        profile_revision="1", policy_id="fake-policy", policy_revision="1", repository=str(repository),
        baseline_head=args.expected_head, current_head=args.expected_head, phase="implement",
        status=RunStatus.RUNNING, hop_limit=5, hop_used=0,
    )

    if mode in ("dispatch_then_crash", "dispatch_then_hang"):
        _leave_dirty_worktree_content(repository)
        write_run_snapshot(run_root / "run.json", run)
        marker = DispatchMarker(
            schema_version=1, run_id=run.run_id, hop_id="H001", sequence=1, invocation_id="INV-fake",
            base_head=args.expected_head, budget_reserved=1,
        )
        write_dispatch_marker(run_root / "dispatch.json", marker)
        sentinel = state_root / "fake-engine.pid"
        sentinel.write_text(str(os.getpid()), encoding="utf-8")
        if mode == "dispatch_then_hang":
            while True:
                time.sleep(3600)
        return 9

    terminal_status = {
        "awaiting_human": RunStatus.AWAITING_HUMAN,
        "awaiting_human_budget": RunStatus.AWAITING_HUMAN_BUDGET,
        "reconcile_required": RunStatus.RECONCILE_REQUIRED,
        "stopped": RunStatus.STOPPED,
        "completed_consistent": RunStatus.COMPLETED,
        "completed_missing_summary": RunStatus.COMPLETED,
        "completed_inconsistent_summary": RunStatus.COMPLETED,
    }.get(mode)
    if terminal_status is None:
        return 9

    from dataclasses import replace

    run = replace(run, status=terminal_status)
    _leave_dirty_worktree_content(repository)
    write_run_snapshot(run_root / "run.json", run)

    if mode == "completed_consistent":
        atomic_write_json(state_root / "operator-summary.json", {
            "run_status": "completed", "worktree_clean": True, "final_head": args.expected_head,
        })
    elif mode == "completed_inconsistent_summary":
        atomic_write_json(state_root / "operator-summary.json", {
            "run_status": "completed", "worktree_clean": False, "final_head": args.expected_head,
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
