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

from workflow_coordinator.model import HopSummary, Run, RunStatus  # noqa: E402
from workflow_coordinator.persistence import atomic_write_json, write_run_snapshot, write_dispatch_marker  # noqa: E402
from workflow_coordinator.model import DispatchMarker  # noqa: E402
from workflow_coordinator.serialization import load_run, work_item_from_yaml, workflow_profile_from_yaml  # noqa: E402
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
    parser.add_argument("--execution-bindings-sha256")
    parser.add_argument("--timeout-seconds", default="900")
    parser.add_argument("--executable", default="codex")
    parser.add_argument("--continue-existing", action="store_true")
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


def _continue_existing(args: argparse.Namespace, repository: Path, state_root: Path) -> int:
    """Stand-in for ``operator.py``'s ``--continue-existing`` admission path
    (B, WI-WFC-RECOVERY-ROUTING-001): the state root and Run already exist;
    this never mkdirs or writes a fresh ``operator-plan.json``. The outcome
    is selected by ``WFC_FAKE_ENGINE_CONTINUE_MODE`` so lifecycle recovery
    tests can pin an exact, repeatable continuation result."""

    mode = os.environ.get("WFC_FAKE_ENGINE_CONTINUE_MODE", "completed_consistent")
    association = register_checkout(state_root, repository)
    run_root = checkout_state_root(state_root, association)
    run = load_run(run_root / "run.json")
    if run.status is not RunStatus.READY:
        return 9

    from dataclasses import replace

    terminal_status = {
        "completed_consistent": RunStatus.COMPLETED,
        "awaiting_human_again": RunStatus.AWAITING_HUMAN,
        "reconcile_required_again": RunStatus.RECONCILE_REQUIRED,
        # O8: the continuation spends every newly authorized Hop and stops on
        # the exhausted budget again, so a test can exercise a second
        # extension (and the total-extension bound) on the identical Run.
        "budget_exhausted_again": RunStatus.AWAITING_HUMAN_BUDGET,
    }.get(mode)
    if terminal_status is None:
        return 9
    sequences = (
        range(run.hop_used + 1, run.hop_limit + 1)
        if mode == "budget_exhausted_again"
        else range(run.hop_used + 1, run.hop_used + 2)
    )
    stop_reason = None
    if terminal_status is RunStatus.AWAITING_HUMAN_BUDGET:
        stop_reason = "autonomous_hop_budget_exhausted"
    elif terminal_status is not RunStatus.COMPLETED:
        stop_reason = "second_fixture_stop"
    base_head = run.current_head
    run = replace(
        run, status=terminal_status, current_head=args.expected_head,
        hop_used=max(sequences.stop - 1, run.hop_used),
        stop_reason=stop_reason,
        hops=(*run.hops, *(
            HopSummary(
                hop_id=f"H{sequence:03d}", sequence=sequence, role="independent_reviewer",
                outcome="completed", base_head=base_head, end_head=args.expected_head,
                actual_commits=(), finding_delta_ids=(), evidence_refs=(), applied_route=None,
                stop_reason=None, summary="fixture continuation Hop", attribution="agent",
            )
            for sequence in sequences
        )),
    )
    write_run_snapshot(run_root / "run.json", run)
    if mode == "completed_consistent":
        atomic_write_json(state_root / "operator-summary.json", {
            "run_status": "completed", "worktree_clean": True, "final_head": args.expected_head,
        })
    return 0


def main() -> int:
    mode = os.environ.get("WFC_FAKE_ENGINE_MODE", "exit_immediately")
    args = _parse_args()
    repository = Path(args.repository).resolve()
    state_root = Path(args.state_root).resolve()

    if args.continue_existing:
        return _continue_existing(args, repository, state_root)

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
    work_item_id = "WI-FAKE"
    profile_id = "fake-profile"
    profile_revision = "1"
    if mode in (
        "mechanical_reconcile_required", "mechanical_awaiting_human", "budget_exhausted",
        "interrupted_reconcile_required",
    ):
        # B, WI-WFC-RECOVERY-ROUTING-001: a mechanical-recovery continuation
        # re-admits through the real ``operator.py`` pipeline, which requires
        # the Run's own ``profile_id``/``profile_revision``/``work_item_id``
        # to match the *real* compiled profile/work-item exactly -- read
        # them for real only for the modes that exercise that continuation;
        # every other (legacy) caller may still pass a non-existent
        # ``--profile``/``--work-item`` placeholder path unchanged.
        # O8: ``budget_exhausted`` drives the same real continuation path
        # after `wfc extend-budget`, so it needs the identical real ids.
        real_profile = workflow_profile_from_yaml((repository / args.profile).read_text(encoding="utf-8"))
        real_work_item = work_item_from_yaml((repository / args.work_item).read_text(encoding="utf-8"))
        work_item_id = real_work_item.work_item_id
        profile_id = real_profile.profile_id
        profile_revision = real_profile.revision
    run = Run(
        schema_version=2, run_id=run_id, work_item_id=work_item_id,
        profile_id=profile_id, profile_revision=profile_revision,
        policy_id="fake-policy", policy_revision="1", repository=str(repository),
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
        # B, WI-WFC-RECOVERY-ROUTING-001: reproduce the honest recoverable
        # mechanical stop shape -- a real recorded Hop and a
        # ``mechanical_preemption:`` stop reason naming a durable-artifact
        # anomaly -- for both the "new Run" (A) and "already-archived
        # terminal awaiting_human" (B(ii), the incident shape) cases.
        "mechanical_reconcile_required": RunStatus.RECONCILE_REQUIRED,
        "mechanical_awaiting_human": RunStatus.AWAITING_HUMAN,
        # O10B, WI-WFC-BETA2-O10B-001: the honest *post-dispatch
        # interruption* shape, written exactly as
        # ``persistence.recover_uncertain_dispatch`` ->
        # ``reducer.require_reconcile(reason="interruption")`` writes it: one
        # charged ``uncertain_dispatch`` Hop with no commit and no anchor,
        # the ``interruption`` reconcile reason and its boilerplate question,
        # and the predecessor dispatch marker still in place.
        # ``WFC_FAKE_ENGINE_INTERRUPTION_*`` variables let a test violate one
        # element at a time.
        "interrupted_reconcile_required": RunStatus.RECONCILE_REQUIRED,
        # O8, WI-WFC-BETA2-O8-001: the honest *budget-exhausted* shape --
        # every authorized Hop actually charged and recorded, and the
        # reducer's own ``autonomous_hop_budget_exhausted`` stop reason. The
        # pre-existing ``awaiting_human_budget`` mode above keeps its own
        # (deliberately unexhausted) shape for the crash-matrix and postrun
        # classification tests that use it.
        "budget_exhausted": RunStatus.AWAITING_HUMAN_BUDGET,
    }.get(mode)
    if terminal_status is None:
        return 9

    from dataclasses import replace

    mechanical_stop_reason = os.environ.get(
        "WFC_FAKE_ENGINE_MECHANICAL_STOP_REASON",
        "mechanical_preemption:required durable artifact has no CommitIntent",
    )
    if mode == "budget_exhausted":
        charged = int(os.environ.get("WFC_FAKE_ENGINE_BUDGET_HOPS", "2"))
        run = replace(
            run,
            hop_limit=charged,
            hop_used=charged,
            hops=tuple(
                HopSummary(
                    hop_id=f"H{sequence:03d}", sequence=sequence,
                    role="worker" if sequence == 1 else "independent_reviewer",
                    outcome="completed", base_head=args.expected_head,
                    end_head=args.expected_head, actual_commits=(), finding_delta_ids=(),
                    evidence_refs=(), applied_route=None, stop_reason=None,
                    summary="fixture Hop", attribution="agent",
                )
                for sequence in range(1, charged + 1)
            ),
            stop_reason="autonomous_hop_budget_exhausted",
        )
    elif mode in ("mechanical_reconcile_required", "mechanical_awaiting_human"):
        run = replace(
            run,
            hops=(HopSummary(
                hop_id="H006", sequence=1, role="independent_reviewer", outcome="completed",
                base_head=args.expected_head, end_head=args.expected_head, actual_commits=(),
                finding_delta_ids=(), evidence_refs=(), applied_route=None, stop_reason=None,
                summary="fixture Hop", attribution="agent",
            ),),
            hop_used=1,
            stop_reason=mechanical_stop_reason,
        )
    elif mode == "interrupted_reconcile_required":
        hop_id = os.environ.get("WFC_FAKE_ENGINE_INTERRUPTION_HOP_ID", "H001")
        commits = tuple(
            filter(None, os.environ.get("WFC_FAKE_ENGINE_INTERRUPTION_COMMITS", "").split(","))
        )
        anchor = os.environ.get("WFC_FAKE_ENGINE_INTERRUPTION_ANCHOR") or None
        run = replace(
            run,
            hops=(HopSummary(
                hop_id=hop_id, sequence=1, role=os.environ.get(
                    "WFC_FAKE_ENGINE_INTERRUPTION_ROLE", "uncertain_dispatch",
                ),
                outcome="uncertain", base_head=args.expected_head,
                end_head=args.expected_head, actual_commits=commits,
                finding_delta_ids=(), evidence_refs=(), applied_route=None,
                stop_reason="interrupted_after_dispatch_marker",
                summary="Invocation may have been accepted; charged without replay.",
                attribution="coordinator", pre_route_anchor_sha256=anchor,
            ),),
            hop_used=1,
            reconcile_reason=os.environ.get(
                "WFC_FAKE_ENGINE_INTERRUPTION_RECONCILE_REASON", "interruption",
            ),
            reconcile_anchor=args.expected_head,
            stop_reason="reconcile_requires_explicit_resume",
            human_question="Validate current reality and explicitly authorize resume.",
            anomalies=tuple(
                filter(None, os.environ.get("WFC_FAKE_ENGINE_INTERRUPTION_ANOMALIES", "").split(","))
            ),
        )
        marker_hop_id = os.environ.get("WFC_FAKE_ENGINE_INTERRUPTION_MARKER_HOP_ID", hop_id)
        if os.environ.get("WFC_FAKE_ENGINE_INTERRUPTION_NO_MARKER") != "1":
            write_dispatch_marker(run_root / "dispatch.json", DispatchMarker(
                schema_version=1, run_id=run.run_id, hop_id=marker_hop_id, sequence=1,
                invocation_id="INV-fake-interrupted", base_head=args.expected_head,
                budget_reserved=1, pre_route_anchor_sha256=anchor,
            ))

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
