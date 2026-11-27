"""Synthetic archive-tree builder for BP6a audit/report tests.

Builds a ``manifest.json`` + ``run/`` (and ``ops/``) tree shaped like a real
``wfc archive`` output -- without any real engine, provider or Git involved --
so audit-projection tests can plant known canary strings (a fake home
directory, user name, scratch/state/ops path, persona and provider prose)
in every source field, including nested dicts and list elements, and assert
that none of them ever reach ``audit.json``.

Never point a test at the real Evidence repository; everything here is
synthetic and self-contained under a ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

DEFAULT_HOME = "/home/canary-home-user"
DEFAULT_USER = "canary-user-name"
DEFAULT_SCRATCH = "/home/canary-home-user/wfc-scratch/canary-attempt"
DEFAULT_STATE = "/home/canary-home-user/wfc-state/canary-attempt"
DEFAULT_OPS = "/home/canary-home-user/wfc-ops/canary-attempt"
PERSONA_CANARY = "CANARY-PERSONA-PROSE-should-never-leak"
PROVIDER_PROSE_CANARY = "CANARY-PROVIDER-DIAGNOSTIC-prose-should-never-leak"
REMOTE_URL_CANARY = "https://canary-remote.example.invalid/canary/repo.git"

# The admission-time fingerprint ``build_attempt`` records in
# ``operator-plan.json`` for ``task-check``. Pass this same mapping as an
# ``ObservationSpec.stat_fingerprint`` to get ``executable_identity_stable``
# True from the default plan; pass any different mapping to get False.
ADMITTED_TASK_CHECK_FINGERPRINT = {
    "st_dev": 1, "st_ino": 2, "st_mode": 33261, "st_size": 123, "st_mtime_ns": 456,
}


@dataclass
class ObservationSpec:
    command_name: str = "task-check"
    status: str = "pass"
    exit_code: int | None = 0
    error_class: str | None = None
    argv_digest: str = "a" * 64
    stdout_sha256: str | None = "b" * 64
    stderr_sha256: str | None = "c" * 64
    stat_fingerprint: Mapping[str, int] | None = None
    summary_prose: str = PROVIDER_PROSE_CANARY

    def to_dict(self, *, scratch: str) -> dict:
        return {
            "provider": self.command_name,
            "status": self.status,
            "command_name": self.command_name,
            "command": [self.command_name],
            "summary": self.summary_prose,
            "artifact_ref": None,
            "argv_digest": self.argv_digest,
            "authored_argv0": self.command_name,
            "timeout_seconds": 900,
            "cwd_policy": "canonical-repository-root-v1",
            "invocation_path": f"{scratch}/bin/{self.command_name}",
            "canonical_path": f"{scratch}/bin/{self.command_name}",
            "stat_fingerprint": dict(self.stat_fingerprint) if self.stat_fingerprint else None,
            "environment_policy": "coordinator-inherited-environment-v1",
            "path_source": "inherited",
            "path_digest": "d" * 64,
            "exit_code": self.exit_code,
            "signal": None,
            "error_class": self.error_class,
            "stdout_byte_count": 12,
            "stdout_sha256": self.stdout_sha256,
            "stdout_body_omitted": False,
            "stderr_byte_count": 0,
            "stderr_sha256": self.stderr_sha256,
            "stderr_body_omitted": False,
            "untrusted_diagnostic": {
                "stdout_tail_base64": "Y2FuYXJ5",
                "stdout_tail_truncated": False,
                "stderr_tail_base64": "Y2FuYXJ5",
                "stderr_tail_truncated": False,
            },
        }


def usage_record(
    *,
    invocation_id: str = "INV-fixture",
    role: str = "worker",
    adapter: str = "claude_cli",
    model: str | None = "claude-sonnet-5-5",
    coverage: str = "complete",
    input_tokens: int | None = 19,
    output_tokens: int | None = 9,
    cache_read_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    reasoning_output_tokens: int | None = None,
    input_includes_cache_read: bool = False,
    per_model: Sequence[Mapping[str, Any]] = (),
) -> dict:
    """One ``usage.json`` record, shaped exactly like
    ``serialization.usage_record_to_data``'s own output."""

    return {
        "invocation_id": invocation_id,
        "role": role,
        "adapter": adapter,
        "model": model,
        "coverage": coverage,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "input_includes_cache_read": input_includes_cache_read,
        "per_model": list(per_model),
    }


@dataclass
class HopSpec:
    hop_id: str
    sequence: int
    role: str = "worker"
    phase: str | None = "implement"
    outcome: str = "completed"
    applied_route: str | None = "accept"
    actual_commits: Sequence[str] = ()
    finding_delta_ids: Sequence[str] = ()
    adapter: str = "claude_cli"
    model: str = "claude-sonnet-5-5"
    reasoning: str = "high"
    persona: str = PERSONA_CANARY
    summary_prose: str = PROVIDER_PROSE_CANARY
    attribution: str = "agent"
    observations: Sequence[ObservationSpec] = field(default_factory=lambda: (ObservationSpec(),))
    # O7: the declared evidence plan as ``(command_name, argv_digest)`` pairs,
    # for a fixture that must show a planned gate the fail-fast stop never
    # reached. ``None`` omits the key entirely, exactly as a pre-O7 attempt
    # record does.
    required_evidence: Sequence[tuple[str, str]] | None = None
    # ``None`` (the default) means no usage.json record was ever persisted
    # for this Hop -- distinct from a persisted record whose own coverage is
    # "none". Pass a dict shaped like ``usage_record_to_data``'s output (see
    # ``workflow_coordinator.serialization``) to simulate a captured record.
    usage: Mapping[str, Any] | None = None

    def run_hop_dict(self) -> dict:
        return {
            "hop_id": self.hop_id,
            "sequence": self.sequence,
            "phase": self.phase,
            "role": self.role,
            "outcome": self.outcome,
            "base_head": "0" * 40,
            "end_head": "1" * 40,
            "actual_commits": list(self.actual_commits),
            "finding_delta_ids": list(self.finding_delta_ids),
            "evidence_refs": [],
            "applied_route": self.applied_route,
            "stop_reason": None,
            "summary": self.summary_prose,
            "attribution": self.attribution,
        }

    def execution_evidence_attempt(self, *, scratch: str) -> dict:
        planned = (
            {}
            if self.required_evidence is None
            else {
                "required_evidence": [
                    {"command_name": name, "argv_digest": digest, "timeout_seconds": 900}
                    for name, digest in self.required_evidence
                ]
            }
        )
        return {
            **planned,
            "hop_id": self.hop_id,
            "invocation_id": f"INV-{self.hop_id}",
            "phase": self.phase,
            "workflow_role": self.role,
            "persona": self.persona,
            "persona_sha256": hashlib.sha256(self.persona.encode("utf-8")).hexdigest(),
            "adapter": self.adapter,
            "model": self.model,
            "reasoning": self.reasoning,
            "context": "fresh",
            "timeout_seconds": 900,
            "returncode": 0,
            "timed_out": False,
            "candidate_result_present": True,
            "diagnostics": [PROVIDER_PROSE_CANARY],
            "diagnostics_artifact": None,
            "outcome": self.outcome,
            "base_head": "0" * 40,
            "end_head": "1" * 40,
            "actual_commits": list(self.actual_commits),
            "trusted_observations": [obs.to_dict(scratch=scratch) for obs in self.observations],
        }


@dataclass
class FindingSpec:
    finding_id: str
    state: str = "open"
    blocking: bool = True
    disposition: str = "pending"
    introduced_by_hop: str = "H001"
    changed_by_hop: str = "H001"
    invariant_prose: str = PROVIDER_PROSE_CANARY
    evidence_refs: Sequence[str] = (PROVIDER_PROSE_CANARY,)

    def to_dict(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "invariant": self.invariant_prose,
            "blocking": self.blocking,
            "state": self.state,
            "introduced_by_hop": self.introduced_by_hop,
            "changed_by_hop": self.changed_by_hop,
            "evidence_refs": list(self.evidence_refs),
            "disposition": self.disposition,
            "successor_ref": None,
            "external_work_item_ref": None,
            "correction_obligation": None,
        }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def default_hops() -> list[HopSpec]:
    return [
        HopSpec(hop_id="H001", sequence=1, role="worker", phase="implement",
                actual_commits=("1" * 40,), finding_delta_ids=()),
        HopSpec(hop_id="H002", sequence=2, role="independent_reviewer", phase="broad_review",
                applied_route="accept", actual_commits=("2" * 40,), finding_delta_ids=(),
                observations=(ObservationSpec(command_name="task-check", status="pass", exit_code=0),)),
    ]


def build_attempt(
    root: Path,
    *,
    attempt_id: str = "wfc-fixture-bp6-20261120-01",
    checkout_id: str = "11111111-2222-3333-4444-555555555555",
    run_id: str = "RUN-" + "f" * 32,
    work_item_id: str = "WI-FIXTURE-BP6-001",
    status: str = "completed",
    stop_reason: str | None = None,
    human_question: str | None = None,
    semantic_stop: Mapping[str, Any] | None = None,
    hop_limit: int = 8,
    hop_used: int | None = None,
    hops: Sequence[HopSpec] | None = None,
    findings: Sequence[FindingSpec] = (),
    anomalies: Sequence[str] = (),
    policy_id: str = "repository-standard",
    policy_revision: str = "1",
    profile_id: str = "robust",
    profile_revision: str = "1",
    baseline_head: str = "0" * 40,
    final_head: str | None = None,
    worktree_clean: bool = True,
    include_operator_summary: bool = True,
    include_operator_plan: bool = True,
    include_execution_evidence: bool = True,
    home: str = DEFAULT_HOME,
    user: str = DEFAULT_USER,
    scratch: str = DEFAULT_SCRATCH,
    state_path: str = DEFAULT_STATE,
    ops_path: str = DEFAULT_OPS,
    semantic_notes: str | None = PROVIDER_PROSE_CANARY,
    executable_argument: str | None = None,
) -> Path:
    """Write a synthetic archived Attempt under ``root`` and return its path."""

    hops = list(hops) if hops is not None else default_hops()
    if hop_used is None:
        hop_used = len(hops)
    if final_head is None:
        final_head = hops[-1].run_hop_dict()["end_head"] if hops else baseline_head
    if executable_argument is None:
        executable_argument = f"{scratch}/bin/codex"

    attempt_dir = root / attempt_id

    copied_files: list[dict] = []

    def _place(relpath: str, payload: Mapping[str, Any], *, source_abs: str) -> None:
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        data = text.encode("utf-8")
        target = attempt_dir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        copied_files.append({
            "path": relpath, "source": source_abs, "sha256": _sha256_bytes(data), "bytes": len(data),
        })

    def _place_text(relpath: str, text: str, *, source_abs: str) -> None:
        data = text.encode("utf-8")
        target = attempt_dir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        copied_files.append({
            "path": relpath, "source": source_abs, "sha256": _sha256_bytes(data), "bytes": len(data),
        })

    roles = sorted({hop.role for hop in hops})
    role_bindings = {
        role: {"adapter": next(h.adapter for h in hops if h.role == role),
               "model": next(h.model for h in hops if h.role == role),
               "reasoning": next(h.reasoning for h in hops if h.role == role)}
        for role in roles
    }

    if include_operator_plan:
        plan = {
            "coordinator": {"distribution": "workflow-coordinator", "version": "0.1.0b1"},
            "schema_version": 2,
            "repository": f"{scratch}/repo",
            "expected_baseline": baseline_head,
            "inputs": {
                "work_item": ".wfc/commissions/fixture/work_item.yaml",
                "profile": ".wfc/commissions/fixture/profile.yaml",
                "policy": ".wfc/commissions/fixture/policy.yaml",
                "task": ".wfc/commissions/fixture/task.md",
                "components": ".wfc/commissions/fixture/components.yaml",
            },
            "components": {
                "simple-self-check": {"path": "resources/components/simple-self-check.md", "digest": "e" * 64},
            },
            "durable_artifacts": {}, "legacy_durable_artifacts": [],
            "state_root": state_path, "run_id": run_id,
            "executable": executable_argument, "timeout_seconds": 900,
            "execution_bindings_source": f"{ops_path}/execution-bindings.yaml",
            "role_personas": {role: PERSONA_CANARY for role in roles},
            "role_bindings": role_bindings,
            "max_autonomous_hops": hop_limit, "retry": False,
            "agent_operating_contract": {
                "contract_id": "workflow_coordinator.agent_operating_contract",
                "version": 6, "resource": "resources/agent_operating_contract.md",
                "byte_count": 4096, "sha256": "f" * 64,
            },
            "governing_inputs": {
                ".wfc/commissions/fixture/work_item.yaml": "1" * 64,
                ".wfc/commissions/fixture/profile.yaml": "2" * 64,
                ".wfc/commissions/fixture/policy.yaml": "3" * 64,
            },
            "evidence": {
                "environment_policy": "coordinator-inherited-environment-v1",
                "path_source": "inherited", "path_digest": "9" * 64,
                "selected_commands": [
                    {
                        "command_name": "task-check", "argv_digest": "a" * 64, "timeout_seconds": 900,
                        "authored_argv0": "task-check", "invocation_path": f"{scratch}/bin/task-check",
                        "canonical_path": f"{scratch}/bin/task-check",
                        "stat_fingerprint": dict(ADMITTED_TASK_CHECK_FINGERPRINT),
                    },
                ],
            },
        }
        _place("run/operator-plan.json", plan, source_abs=f"{state_path}/operator-plan.json")

    run_doc = {
        "schema_version": 3,
        "run_id": run_id, "work_item_id": work_item_id,
        "profile_id": profile_id, "profile_revision": profile_revision,
        "policy_id": policy_id, "policy_revision": policy_revision,
        "repository": f"{scratch}/repo",
        "baseline_head": baseline_head, "current_head": final_head,
        "phase": hops[-1].phase if hops else "implement",
        "status": status, "hop_limit": hop_limit, "hop_used": hop_used,
        "hops": [hop.run_hop_dict() for hop in hops],
        "findings": [finding.to_dict() for finding in findings],
        "child_run_id": None, "suspension_head": None,
        "reconcile_reason": PROVIDER_PROSE_CANARY if status == "reconcile_required" else None,
        "reconcile_anchor": None,
        "stop_reason": stop_reason,
        "human_question": human_question,
        "anomalies": list(anomalies),
        "semantic_stop": dict(semantic_stop) if semantic_stop else None,
        "successor_provenance": None,
    }
    _place(f"run/checkouts/{checkout_id}/run.json", run_doc, source_abs=f"{state_path}/checkouts/{checkout_id}/run.json")

    if include_execution_evidence:
        evidence_doc = {
            "schema_version": 3,
            "attempts": [hop.execution_evidence_attempt(scratch=scratch) for hop in hops],
        }
        _place(
            f"run/checkouts/{checkout_id}/execution-evidence.json", evidence_doc,
            source_abs=f"{state_path}/checkouts/{checkout_id}/execution-evidence.json",
        )

    usage_records = {hop.hop_id: hop.usage for hop in hops if hop.usage is not None}
    if usage_records:
        usage_doc = {"schema_version": 1, "run_id": run_id, "records": usage_records}
        _place(
            f"run/checkouts/{checkout_id}/usage.json", usage_doc,
            source_abs=f"{state_path}/checkouts/{checkout_id}/usage.json",
        )

    if include_operator_summary:
        summary = {
            "schema_version": 2, "work_item_id": work_item_id, "run_id": run_id,
            "invocation_id": f"INV-{hops[-1].hop_id}" if hops else None,
            "baseline_head": baseline_head, "current_head": final_head, "final_head": final_head,
            "run_status": status, "hop_used": hop_used, "hop_limit": hop_limit,
            "stop_reason": stop_reason, "human_question": human_question,
            "semantic_stop": dict(semantic_stop) if semantic_stop else None,
            "successor_provenance": None,
            "anomalies": list(anomalies),
            "next_explicit_action": PROVIDER_PROSE_CANARY,
            "provider": {
                "returncode": 0, "timed_out": False, "candidate_result_present": True,
                "diagnostics": [PROVIDER_PROSE_CANARY], "diagnostics_artifact": None,
            },
            "commits": [
                {"hash": commit, "subject": PROVIDER_PROSE_CANARY}
                for hop in hops for commit in hop.actual_commits
            ],
            "changed_paths": ["src/workflow_coordinator/fixture.py"],
            "worktree_clean": worktree_clean, "operator_error": None,
            "hops": [
                {"hop_id": hop.hop_id, "base_head": "0" * 40, "end_head": "1" * 40,
                 "actual_commits": list(hop.actual_commits), "changed_paths": [], "outcome": hop.outcome}
                for hop in hops
            ],
            "execution_evidence": [], "checkout_id": checkout_id,
        }
        _place("run/operator-summary.json", summary, source_abs=f"{state_path}/operator-summary.json")

    _place_text(
        "ops/run.log",
        f"HOME={home}\nUSER={user}\nSCRATCH={scratch}\nSTATE={state_path}\nOPS={ops_path}\n"
        f"remote {REMOTE_URL_CANARY}\n{PROVIDER_PROSE_CANARY}\n",
        source_abs=f"{ops_path}/run.log",
    )

    manifest = {
        "attempt_id": attempt_id,
        "archived_at": "2026-11-20T00:00:00+00:00",
        "sources": {"run_dir": state_path, "ops_dir": ops_path},
        "copied_files": sorted(copied_files, key=lambda item: item["path"]),
        "facts": {
            "run_id": run_id, "work_item_id": work_item_id, "status": status,
            "ops_source": ops_path,
            "repository_identities": {"WFC": "canary-identity-" + user},
            "pinned_revisions": {"values": {"WFC": "0" * 40}, "source_files": ["pinned-revisions.txt"]},
        },
        "semantic": {"domain": "unclassified", "workstream": "fixture", "notes": semantic_notes} if semantic_notes else None,
    }
    manifest_path = attempt_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    return attempt_dir
