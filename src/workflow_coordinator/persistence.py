"""Atomic local anchors and generated checkpoint views for Slice 1."""

from __future__ import annotations

import json
import os
import tempfile
import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .model import (
    DispatchMarker,
    ExecutionEvidenceDocument,
    ExecutionEvidenceRunIdentity,
    Hop,
    LegacyExecutionEvidenceDocument,
    Observation,
    Run,
    StagedQualificationAttempt,
    TrustedQualificationIndex,
)
from .reducer import (
    ReductionError,
    derive_convergence_facts,
    permitted_route_keys,
    require_reconcile,
    validate_convergence_route_history,
)
from .serialization import (
    dispatch_marker_from_json,
    dispatch_marker_to_data,
    execution_evidence_from_json,
    execution_evidence_to_json,
    load_run,
    run_to_json,
)
from .model import WorkflowProfile, record_hop

Replace = Callable[[str, str], None]


def observation_to_data(observation: Observation) -> dict[str, Any]:
    """Project the exact bounded A2 attempt record without stream bodies."""

    fingerprint = observation.stat_fingerprint
    return {
        "provider": observation.provider,
        "status": observation.status,
        "command_name": observation.command_name,
        "command": list(observation.command),
        "summary": observation.summary,
        "artifact_ref": observation.artifact_ref,
        "argv_digest": observation.argv_digest,
        "authored_argv0": observation.authored_argv0,
        "timeout_seconds": observation.timeout_seconds,
        "cwd_policy": observation.cwd_policy,
        "invocation_path": observation.invocation_path,
        "canonical_path": observation.canonical_path,
        "stat_fingerprint": None if fingerprint is None else {
            "st_dev": fingerprint.st_dev,
            "st_ino": fingerprint.st_ino,
            "st_mode": fingerprint.st_mode,
            "st_size": fingerprint.st_size,
            "st_mtime_ns": fingerprint.st_mtime_ns,
        },
        "environment_policy": observation.environment_policy,
        "path_source": observation.path_source,
        "path_digest": observation.path_digest,
        "exit_code": observation.exit_code,
        "signal": observation.signal,
        "error_class": observation.error_class,
        "stdout_byte_count": observation.stdout_byte_count,
        "stdout_sha256": observation.stdout_sha256,
        "stdout_body_omitted": observation.stdout_body_omitted,
        "stderr_byte_count": observation.stderr_byte_count,
        "stderr_sha256": observation.stderr_sha256,
        "stderr_body_omitted": observation.stderr_body_omitted,
        "untrusted_diagnostic": (
            None
            if observation.untrusted_diagnostic is None
            else {
                "stdout_tail_base64": observation.untrusted_diagnostic.stdout_tail_base64,
                "stdout_tail_truncated": observation.untrusted_diagnostic.stdout_tail_truncated,
                "stderr_tail_base64": observation.untrusted_diagnostic.stderr_tail_base64,
                "stderr_tail_truncated": observation.untrusted_diagnostic.stderr_tail_truncated,
            }
        ),
    }


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
    # A persistence-causing transition is the sole permitted legacy upgrade.
    # Read-only loads/projections retain v1 exactly; a failed replacement leaves
    # its previous bytes intact through atomic_write_text.
    persisted = replace(run, schema_version=2) if run.schema_version == 1 else run
    atomic_write_text(path, run_to_json(persisted), replace_file=replace_file)


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
    actual_commits: tuple[str, ...] = (),
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
                actual_commits=actual_commits,
                invocation_status="acceptance_not_disproved",
                outcome="uncertain",
                finding_delta_ids=(),
                evidence_refs=(),
                applied_route=None,
                stop_reason="interrupted_after_dispatch_marker",
                summary="Invocation may have been accepted; charged without replay.",
                attribution="coordinator",
                pre_route_anchor_sha256=marker.pre_route_anchor_sha256,
            ),
        )
    else:
        if existing.sequence != marker.sequence:
            raise ReductionError("dispatch marker conflicts with charged Hop")
        if existing.pre_route_anchor_sha256 != marker.pre_route_anchor_sha256:
            raise ReductionError("dispatch marker anchor conflicts with charged Hop")
        charged = replace(run, current_head=current_head)
    return require_reconcile(charged, reason="interruption", current_head=current_head)


def execution_evidence_run_identity(run: Run) -> ExecutionEvidenceRunIdentity:
    return ExecutionEvidenceRunIdentity(
        run_id=run.run_id,
        work_item_id=run.work_item_id,
        profile_id=run.profile_id,
        profile_revision=run.profile_revision,
    )


def load_execution_evidence(
    path: Path,
) -> ExecutionEvidenceDocument | LegacyExecutionEvidenceDocument:
    return execution_evidence_from_json(path.read_text(encoding="utf-8"))


def publish_execution_evidence(
    path: Path,
    run_identity: ExecutionEvidenceRunIdentity,
    attempt: StagedQualificationAttempt,
    *,
    replace_file: Replace = os.replace,
) -> tuple[ExecutionEvidenceDocument, str]:
    """Append one schema-4 row atomically, preserving all predecessor rows."""

    if path.exists():
        original = path.read_text(encoding="utf-8")
        parsed = execution_evidence_from_json(original)
        if not isinstance(parsed, ExecutionEvidenceDocument):
            raise ReductionError("legacy execution evidence cannot be extended as schema 4")
        if parsed.run != run_identity:
            raise ReductionError("execution evidence Run identity conflict")
        for existing in parsed.attempts:
            same_hop = (
                existing.hop_id == attempt.hop_id
                or existing.hop_sequence == attempt.hop_sequence
            )
            same_invocation = existing.invocation_id == attempt.invocation_id
            if existing == attempt:
                return parsed, original
            if same_hop or same_invocation:
                raise ReductionError("conflicting duplicate execution evidence attempt")
        document = replace(parsed, attempts=(*parsed.attempts, attempt))
    else:
        document = ExecutionEvidenceDocument(4, run_identity, (attempt,))
    text = execution_evidence_to_json(document)
    atomic_write_text(path, text, replace_file=replace_file)
    return document, text


def trusted_qualification_index_for_run(
    run: Run,
    document: ExecutionEvidenceDocument,
    *,
    marker: DispatchMarker | None = None,
) -> TrustedQualificationIndex:
    """Validate the exact Run/evidence pair and exclude abandoned staged rows."""

    identity = execution_evidence_run_identity(run)
    if document.run != identity:
        raise ReductionError("execution evidence identity does not match Run")
    if run.schema_version != 3:
        return TrustedQualificationIndex.empty()

    hops_by_id = {hop.hop_id: hop for hop in run.hops}
    paired: list[StagedQualificationAttempt] = []
    paired_hops: set[str] = set()
    for attempt in document.attempts:
        hop = hops_by_id.get(attempt.hop_id)
        if hop is None or hop.sequence != attempt.hop_sequence:
            raise ReductionError("execution evidence row references an unknown Hop")
        if hop.role == "uncertain_dispatch":
            if (
                marker is None
                or marker.run_id != run.run_id
                or marker.hop_id != attempt.hop_id
                or marker.sequence != attempt.hop_sequence
                or marker.invocation_id != attempt.invocation_id
                or marker.pre_route_anchor_sha256 != attempt.pre_route_anchor_sha256
                or hop.pre_route_anchor_sha256 != attempt.pre_route_anchor_sha256
            ):
                raise ReductionError("abandoned staged evidence lacks exact marker correlation")
            continue
        if hop.pre_route_anchor_sha256 is None:
            raise ReductionError("execution evidence row is unpaired")
        if (
            attempt.pre_route_anchor_sha256 != hop.pre_route_anchor_sha256
            or attempt.phase != hop.phase
            or attempt.workflow_role != hop.role
            or attempt.outcome != hop.outcome
            or attempt.base_head != hop.base_head
            or attempt.end_head != hop.end_head
            or attempt.actual_commits != hop.actual_commits
        ):
            raise ReductionError("execution evidence row conflicts with Hop provenance")
        paired.append(attempt)
        paired_hops.add(hop.hop_id)
    missing = [
        hop.hop_id
        for hop in run.hops
        if (
            hop.pre_route_anchor_sha256 is not None
            and hop.role != "uncertain_dispatch"
            and hop.hop_id not in paired_hops
        )
    ]
    if missing:
        raise ReductionError("Run Hop anchor lacks paired execution evidence")
    return TrustedQualificationIndex(identity, tuple(paired))


def _qualification_index_for_inputs(
    run: Run,
    execution_evidence_json: str | None,
    *,
    marker: DispatchMarker | None = None,
) -> TrustedQualificationIndex:
    """Validate pair authority while retaining historical no-anchor semantics."""

    anchored_hops = tuple(
        hop
        for hop in run.hops
        if hop.pre_route_anchor_sha256 is not None
        and hop.role != "uncertain_dispatch"
    )
    if execution_evidence_json is None:
        if run.schema_version == 3 and anchored_hops:
            raise ReductionError("Run Hop anchor lacks paired execution evidence")
        return TrustedQualificationIndex.empty()
    document = execution_evidence_from_json(execution_evidence_json)
    if isinstance(document, ExecutionEvidenceDocument):
        return trusted_qualification_index_for_run(run, document, marker=marker)
    if run.schema_version == 3 and anchored_hops:
        raise ReductionError("anchored Run requires schema-4 execution evidence")
    return TrustedQualificationIndex.empty()


def qualification_index_for_inputs(
    run: Run,
    execution_evidence_json: str | None,
    *,
    marker: DispatchMarker | None = None,
) -> TrustedQualificationIndex:
    """Expose the one strict qualification-input policy to live consumers."""

    return _qualification_index_for_inputs(run, execution_evidence_json, marker=marker)


def checkpoint_projection(
    run: Run,
    profile: WorkflowProfile,
    *,
    observations: tuple[Observation, ...] = (),
    qualified_cycle_limit: int | None = None,
    execution_evidence_json: str | None = None,
    dispatch_marker: DispatchMarker | None = None,
) -> dict[str, Any]:
    """Generate a compact view, optionally including replay-derived convergence facts."""

    evidence_digest = (
        None
        if execution_evidence_json is None
        else hashlib.sha256(execution_evidence_json.encode("utf-8")).hexdigest()
    )
    qualification_index = _qualification_index_for_inputs(
        run, execution_evidence_json, marker=dispatch_marker
    )
    validate_convergence_route_history(run, profile, qualification_index)
    projection = {
        "identity": {
            "work_item_id": run.work_item_id,
            "run_id": run.run_id,
            "profile": f"{run.profile_id}@{run.profile_revision}",
            "policy": f"{run.policy_id}@{run.policy_revision}",
            # This projection is published alongside the immutable Run
            # snapshot.  It records the exact snapshot bytes it projects so a
            # later preparation action cannot silently turn a changed
            # ``run.json`` into new predecessor truth.
            "run_json_sha256": hashlib.sha256(run_to_json(run).encode("utf-8")).hexdigest(),
            "execution_evidence_sha256": evidence_digest,
        },
        "heads": {"baseline": run.baseline_head, "current": run.current_head},
        "state": {
            "phase": run.phase,
            "status": run.status.value,
            "reconcile_reason": run.reconcile_reason,
            "reconcile_anchor": run.reconcile_anchor,
            "stop_reason": run.stop_reason,
            "human_question": run.human_question,
            "semantic_stop": None if run.semantic_stop is None else {
                "kind": run.semantic_stop.kind,
                "question": run.semantic_stop.question,
                "source_hop_id": run.semantic_stop.source_hop_id,
            },
            "successor_provenance": None if run.successor_provenance is None else {
                "predecessor_run_id": run.successor_provenance.predecessor_run_id,
                "predecessor_work_item_id": run.successor_provenance.predecessor_work_item_id,
                "predecessor_final_head": run.successor_provenance.predecessor_final_head,
                "authority_decision_ref": run.successor_provenance.authority_decision_ref,
                "authority_decision_sha256": run.successor_provenance.authority_decision_sha256,
                "continuation_descriptor_sha256": run.successor_provenance.continuation_descriptor_sha256,
                "salvage_manifest_sha256": run.successor_provenance.salvage_manifest_sha256,
            },
            "anomalies": list(run.anomalies),
            "child_run_id": run.child_run_id,
            "suspension_head": run.suspension_head,
        },
        "budget": {"used": run.hop_used, "remaining": run.hop_remaining},
        "hops": [
            {
                "hop_id": hop.hop_id,
                **({"phase": hop.phase} if run.schema_version == 3 else {}),
                "role": hop.role,
                "outcome": hop.outcome,
                "range": f"{hop.base_head}..{hop.end_head}",
                "finding_delta_ids": list(hop.finding_delta_ids),
                "evidence_refs": list(hop.evidence_refs),
                "route": hop.applied_route,
                "summary": hop.summary,
                "attribution": hop.attribution,
                **(
                    {
                        "requested_route": hop.requested_route,
                        "applied_route": hop.applied_route,
                        "route_reason": hop.route_reason,
                    }
                    if run.schema_version == 3
                    else {}
                ),
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
        "evidence": [observation_to_data(observation) for observation in observations],
        "next_permitted_route_keys": list(permitted_route_keys(run, profile)),
    }
    if qualified_cycle_limit is not None:
        facts = derive_convergence_facts(
            run, qualification_index, qualified_cycle_limit
        )
        projection["convergence_facts"] = [
            {
                "finding_id": fact.finding_id,
                "strategy_generation": fact.strategy_generation,
                "qualified_cycle_count": fact.qualified_cycle_count,
                "qualified_cycle_limit": fact.qualified_cycle_limit,
                "goalpost_reached": fact.goalpost_reached,
                "source_evaluator_hops": list(fact.source_evaluator_hops),
            }
            for fact in facts
        ]
        if run.schema_version == 3:
            latest_coverage: dict[str, dict[str, Any]] = {}
            latest_evaluation: dict[str, dict[str, Any]] = {}
            for hop in run.hops:
                for claim in hop.coverage_claims:
                    latest_coverage[claim.finding_id] = {
                        "source_hop_id": hop.hop_id,
                        "disposition": claim.disposition.value,
                        "evidence_refs": list(claim.evidence_refs),
                        "attribution": "agent_attributed",
                    }
                for assessment in hop.convergence_assessments:
                    latest_evaluation[assessment.finding_id] = {
                        "source_hop_id": hop.hop_id,
                        "correction_hop_id": assessment.correction_hop_id,
                        "result": assessment.result.value,
                        "evidence_refs": list(assessment.evidence_refs),
                        "attribution": "agent_attributed",
                    }
            projection["convergence"] = {
                "attribution": "coordinator_derived",
                "open_tracked_obligations": [
                    {
                        "finding_id": finding.finding_id,
                        "owner": finding.correction_obligation.owner.value,
                        "introduced_by_hop": finding.correction_obligation.introduced_by_hop,
                        "invariant": finding.invariant,
                        "evidence_refs": list(finding.evidence_refs),
                        "semantic_attribution": "agent_attributed",
                        "latest_coverage": latest_coverage.get(finding.finding_id),
                        "latest_evaluation": latest_evaluation.get(finding.finding_id),
                    }
                    for finding in sorted(run.findings, key=lambda item: item.finding_id)
                    if (
                        finding.correction_obligation is not None
                        and finding.state.value == "open"
                        and finding.blocking
                    )
                ],
                "facts": [
                    {
                        "finding_id": fact.finding_id,
                        "strategy_generation": fact.strategy_generation,
                        "qualified_cycle_count": fact.qualified_cycle_count,
                        "qualified_cycle_limit": fact.qualified_cycle_limit,
                        "goalpost_reached": fact.goalpost_reached,
                        "source_evaluator_hops": list(fact.source_evaluator_hops),
                    }
                    for fact in facts
                ],
                "escape_routes": [
                    {
                        "evaluator_hop_id": hop.hop_id,
                        "triggering_finding_ids": [
                            item
                            for item in (hop.route_reason or "").partition(":")[2].split(",")
                            if item
                        ],
                        "trigger_generations": {
                            finding_id: sum(
                                1
                                for prior in run.hops[:index]
                                if prior.strategy_assessment is not None
                                and prior.strategy_assessment.result.value == "accepted"
                                and finding_id in prior.strategy_assessment.finding_ids
                            )
                            for finding_id in (hop.route_reason or "").partition(":")[2].split(",")
                            if finding_id
                        },
                        "requested_route": hop.requested_route,
                        "applied_route": hop.applied_route,
                        "route_reason": hop.route_reason,
                        "attribution": "coordinator_derived",
                        "state": (
                            "consumed" if any(
                                item.strategy_assessment is not None
                                and item.strategy_assessment.escape_hop_id == hop.hop_id
                                and item.strategy_assessment.result.value == "accepted"
                                for item in run.hops
                            ) else "unconsumed"
                        ),
                        "diagnosis": next(({"hop_id": item.hop_id, "classification": item.diagnosis.classification.value, "analysis": item.diagnosis.analysis, "evidence_refs": list(item.diagnosis.evidence_refs), "attribution": "agent_attributed"} for item in run.hops if item.diagnosis is not None and item.diagnosis.escape_hop_id == hop.hop_id), None),
                        "strategy_revision": next(({"hop_id": item.hop_id, "kind": item.strategy_revision.kind.value, "plan": item.strategy_revision.plan, "evidence_refs": list(item.strategy_revision.evidence_refs), "attribution": "agent_attributed"} for item in reversed(run.hops) if item.strategy_revision is not None and item.strategy_revision.escape_hop_id == hop.hop_id), None),
                        "strategy_assessment": next(({"hop_id": item.hop_id, "result": item.strategy_assessment.result.value, "evidence_refs": list(item.strategy_assessment.evidence_refs), "attribution": "agent_attributed"} for item in reversed(run.hops) if item.strategy_assessment is not None and item.strategy_assessment.escape_hop_id == hop.hop_id), None),
                    }
                    for index, hop in enumerate(run.hops)
                    if hop.route_reason is not None
                    and hop.route_reason.startswith("convergence_escape:")
                ],
            }
    return projection


def validate_checkpoint_inputs(
    checkpoint: Mapping[str, Any],
    run: Run,
    *,
    execution_evidence_json: str | None,
    dispatch_marker: DispatchMarker | None = None,
) -> None:
    """Reject a checkpoint that is not bound to one valid authoritative pair."""

    _qualification_index_for_inputs(
        run, execution_evidence_json, marker=dispatch_marker
    )

    try:
        identity = checkpoint["identity"]
        run_digest = identity["run_json_sha256"]
        evidence_digest = identity["execution_evidence_sha256"]
    except (KeyError, TypeError) as error:
        raise ReductionError("checkpoint lacks dual-input identity") from error
    expected_run = hashlib.sha256(run_to_json(run).encode("utf-8")).hexdigest()
    expected_evidence = (
        None
        if execution_evidence_json is None
        else hashlib.sha256(execution_evidence_json.encode("utf-8")).hexdigest()
    )
    if run_digest != expected_run:
        raise ReductionError("checkpoint Run digest mismatch")
    if evidence_digest != expected_evidence:
        raise ReductionError("checkpoint execution-evidence digest mismatch")


__all__ = [
    "atomic_write_json",
    "checkpoint_projection",
    "execution_evidence_run_identity",
    "load_execution_evidence",
    "load_dispatch_marker",
    "observation_to_data",
    "load_run",
    "recover_uncertain_dispatch",
    "publish_execution_evidence",
    "qualification_index_for_inputs",
    "trusted_qualification_index_for_run",
    "validate_checkpoint_inputs",
    "write_dispatch_marker",
    "write_run_snapshot",
]
