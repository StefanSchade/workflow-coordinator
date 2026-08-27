"""Atomic local anchors and generated checkpoint views for Slice 1."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .model import DispatchMarker, Hop, Observation, Run
from .reducer import ReductionError, permitted_route_keys, require_reconcile
from .serialization import (
    dispatch_marker_from_json,
    dispatch_marker_to_data,
    load_run,
    run_to_json,
)
from .model import WorkflowProfile, record_hop

Replace = Callable[[str, str], None]


def atomic_write_text(path: Path, text: str, *, replace_file: Replace = os.replace) -> None:
    """Publish one complete file or leave the previous file untouched."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
        replace_file(temporary_name, str(path))
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(
    path: Path, value: Mapping[str, Any], *, replace_file: Replace = os.replace
) -> None:
    text = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    atomic_write_text(path, text, replace_file=replace_file)


def write_run_snapshot(path: Path, run: Run, *, replace_file: Replace = os.replace) -> None:
    atomic_write_text(path, run_to_json(run), replace_file=replace_file)


def write_dispatch_marker(
    path: Path,
    marker: DispatchMarker,
    *,
    replace_file: Replace = os.replace,
) -> None:
    atomic_write_json(path, dispatch_marker_to_data(marker), replace_file=replace_file)


def load_dispatch_marker(path: Path) -> DispatchMarker:
    return dispatch_marker_from_json(path.read_text(encoding="utf-8"))


def recover_uncertain_dispatch(
    run: Run,
    marker: DispatchMarker | None,
    *,
    current_head: str,
) -> Run:
    """Conservatively charge an uncertain marked invocation and never replay it."""

    if marker is None:
        return run
    if marker.run_id != run.run_id:
        raise ReductionError("dispatch marker Run identity mismatch")
    if marker.budget_reserved != 1:
        raise ReductionError("dispatch marker must reserve exactly one Hop")

    existing = next((hop for hop in run.hops if hop.hop_id == marker.hop_id), None)
    if existing is None:
        if marker.base_head != run.current_head:
            raise ReductionError("dispatch marker base HEAD mismatch")
        if marker.sequence != run.hop_used + 1:
            raise ReductionError("dispatch marker sequence mismatch")
        charged = record_hop(
            run,
            Hop(
                hop_id=marker.hop_id,
                sequence=marker.sequence,
                phase=run.phase,
                role="uncertain_dispatch",
                context="fresh",
                started_at="unknown",
                ended_at=None,
                base_head=marker.base_head,
                end_head=current_head,
                actual_commits=(),
                invocation_status="acceptance_not_disproved",
                outcome="uncertain",
                finding_delta_ids=(),
                evidence_refs=(),
                applied_route=None,
                stop_reason="interrupted_after_dispatch_marker",
                summary="Invocation may have been accepted; charged without replay.",
                attribution="coordinator",
            ),
        )
    else:
        if existing.sequence != marker.sequence:
            raise ReductionError("dispatch marker conflicts with charged Hop")
        charged = replace(run, current_head=current_head)
    return require_reconcile(charged, reason="interruption", current_head=current_head)


def checkpoint_projection(
    run: Run,
    profile: WorkflowProfile,
    *,
    observations: tuple[Observation, ...] = (),
) -> dict[str, Any]:
    """Generate the compact reconstruction view without persisting route authority."""

    return {
        "identity": {
            "work_item_id": run.work_item_id,
            "run_id": run.run_id,
            "profile": f"{run.profile_id}@{run.profile_revision}",
            "policy": f"{run.policy_id}@{run.policy_revision}",
        },
        "heads": {"baseline": run.baseline_head, "current": run.current_head},
        "state": {
            "phase": run.phase,
            "status": run.status.value,
            "reconcile_reason": run.reconcile_reason,
            "reconcile_anchor": run.reconcile_anchor,
            "stop_reason": run.stop_reason,
            "human_question": run.human_question,
            "anomalies": list(run.anomalies),
            "child_run_id": run.child_run_id,
            "suspension_head": run.suspension_head,
        },
        "budget": {"used": run.hop_used, "remaining": run.hop_remaining},
        "hops": [
            {
                "hop_id": hop.hop_id,
                "role": hop.role,
                "outcome": hop.outcome,
                "range": f"{hop.base_head}..{hop.end_head}",
                "finding_delta_ids": list(hop.finding_delta_ids),
                "evidence_refs": list(hop.evidence_refs),
                "route": hop.applied_route,
                "summary": hop.summary,
                "attribution": hop.attribution,
            }
            for hop in run.hops
        ],
        "findings": [
            {
                "finding_id": finding.finding_id,
                "state": finding.state.value,
                "blocking": finding.blocking,
                "introduced_by_hop": finding.introduced_by_hop,
                "changed_by_hop": finding.changed_by_hop,
                "evidence_refs": list(finding.evidence_refs),
            }
            for finding in run.findings
        ],
        "evidence": [
            {
                "provider": observation.provider,
                "status": observation.status,
                "summary": observation.summary,
                "artifact_ref": observation.artifact_ref,
                "digest": observation.digest,
            }
            for observation in observations
        ],
        "next_permitted_route_keys": list(permitted_route_keys(run, profile)),
    }


__all__ = [
    "atomic_write_json",
    "checkpoint_projection",
    "load_dispatch_marker",
    "load_run",
    "recover_uncertain_dispatch",
    "write_dispatch_marker",
    "write_run_snapshot",
]
