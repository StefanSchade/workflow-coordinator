"""BP6a: a pure, read-only, default-deny projection of an archived Attempt.

``project_audit`` builds ``wfc.audit/1`` from the raw files a ``wfc archive``
run already copied and verified (``manifest.json``, ``run/operator-plan.json``,
``run/operator-summary.json``, ``run/checkouts/<id>/run.json``,
``run/checkouts/<id>/execution-evidence.json``). Every field of the result is
read from an explicit, named source key by an explicit allow-list -- there is
no copy-and-scrub of any source object. A field whose source file or key is
absent is omitted or set to ``null`` and the omission is named in ``limits``;
nothing is guessed.

This module never writes to, reads git state from, or re-derives trust for the
raw archive; it only reads the files ``wfc archive`` already placed and
verified. It performs no Git operation and never touches ``manifest.json`` or
any raw file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

_RAW_RETENTION_CHOICES = ("local", "commit", "discard")

SCHEMA = "wfc.audit/1"

_CHECKOUT_RUN_RE = re.compile(r"^run/checkouts/([^/]+)/run\.json$")

_RUN_KEYS = frozenset({
    "run_id", "work_item_id", "status", "stop_reason", "hops_used", "hop_limit",
    "baseline_head", "final_head", "worktree_clean", "human_question_present",
    "semantic_stop_present",
})
_AGENT_CONTRACT_KEYS = frozenset({"id", "version", "sha256"})
_GOVERNANCE_KEYS = frozenset({
    "policy_id", "policy_revision", "profile_id", "profile_revision",
    "agent_contract", "governing_inputs", "component_digests", "role_bindings",
    "executable", "phase_bindings", "execution_envelope_sha256",
})
_COMPONENT_DIGEST_KEYS = frozenset({"path", "digest"})
_EXECUTABLE_KEYS = frozenset({"name"})
_HOP_KEYS = frozenset({
    "hop_id", "sequence", "phase", "role", "adapter", "model", "reasoning",
    "outcome", "applied_route", "commits", "finding_delta_ids", "usage",
    "evidence",
})
_HOP_USAGE_KEYS = frozenset({
    "coverage", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_creation_tokens", "reasoning_output_tokens",
    "input_includes_cache_read", "per_model",
})
_MODEL_USAGE_KEYS = frozenset({
    "model", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_creation_tokens", "reasoning_output_tokens",
})
_USAGE_METRIC_NAMES = (
    "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_creation_tokens", "reasoning_output_tokens",
)
_USAGE_FIGURE_KEYS = frozenset({"value", "lower_bound"})
_USAGE_SUMMARY_KEYS = frozenset({"coverage_counts", "totals", "by_role", "by_model"})
_COVERAGE_COUNT_KEYS = frozenset({"complete", "partial", "none"})
_EVIDENCE_KEYS = frozenset({
    "command_name", "status", "exit_code", "argv_digest", "error_class",
    "stdout_sha256", "stderr_sha256", "executable_identity_stable",
})
_FINDING_KEYS = frozenset({
    "finding_id", "state", "blocking", "disposition", "introduced_by_hop",
    "changed_by_hop",
})
_PRESERVATION_KEYS = frozenset({
    "outcome", "ref", "commit", "tree", "file_count", "bytes", "quiescence",
    "reason_code",
})
_LIFECYCLE_KEYS = frozenset({"classification", "preservation"})
_RAW_FILE_KEYS = frozenset({"path", "sha256", "bytes"})
_RAW_KEYS = frozenset({"retention", "manifest_sha256", "files"})
_TOP_KEYS = frozenset({
    "schema", "attempt_id", "run", "governance", "hops", "findings",
    "anomaly_count", "lifecycle", "raw", "limits", "usage",
})

_LIMIT_USAGE_NOT_REPORTED = "usage_not_reported"
_LIMIT_USAGE_PARTIALLY_REPORTED = "usage_partially_reported"
_LIMIT_NO_LIFECYCLE_FACTS = "no_lifecycle_facts"
_LIMIT_PRESERVATION_NOT_RECORDED = "preservation_not_recorded"
_LIMIT_GOVERNANCE_NOT_RECORDED = "governance_not_recorded"
_LIMIT_RUN_SUMMARY_NOT_RECORDED = "run_summary_not_recorded"
_LIMIT_TRUSTED_EVIDENCE_NOT_RECORDED = "trusted_evidence_not_recorded"

_WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")
_REMOTE_URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://|\bgit@[\w.\-]+:")
_PATH_COMPONENT_RE = re.compile(r"[\\/]")
_TOKEN_RE = re.compile(r"[^0-9A-Za-z_]+")


class AuditError(RuntimeError):
    """A structural refusal: the archive is not projectable as given."""


class ProjectionError(RuntimeError):
    """The privacy guard refused to produce output; names only the JSON path."""


def _assert_keys(value: Mapping[str, Any], allowed: frozenset[str], *, context: str) -> None:
    extra = set(value.keys()) - allowed
    if extra:
        raise AuditError(f"internal: {context} carries unexpected keys {sorted(extra)}")


def _load_manifest(attempt_dir: Path) -> dict:
    manifest_path = attempt_dir / "manifest.json"
    if not manifest_path.is_file():
        raise AuditError(f"missing manifest.json under {attempt_dir}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise AuditError(f"manifest.json does not parse: {error}") from error
    if not isinstance(manifest, dict):
        raise AuditError("manifest.json does not contain a JSON object")
    copied = manifest.get("copied_files")
    if not isinstance(copied, list) or not copied:
        raise AuditError("manifest.json lists no copied files")
    return manifest


def _copied_relpaths(manifest: dict) -> frozenset[str]:
    paths = set()
    for entry in manifest["copied_files"]:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            paths.add(entry["path"])
    return frozenset(paths)


def _read_copied_json(attempt_dir: Path, relpath: str, copied: frozenset[str]) -> dict | None:
    if relpath not in copied:
        return None
    path = attempt_dir / relpath
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise AuditError(f"{relpath} does not parse: {error}") from error
    if not isinstance(data, dict):
        raise AuditError(f"{relpath} does not contain a JSON object")
    return data


def _find_checkout(copied: frozenset[str]) -> tuple[str, str] | None:
    """Return ``(checkout_id, run.json relative path)`` for the single checkout."""

    matches = sorted(
        (match.group(1), relpath)
        for relpath in copied
        for match in (_CHECKOUT_RUN_RE.match(relpath),)
        if match is not None
    )
    return matches[0] if matches else None


def _basename(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def _build_run_section(run_doc: dict, summary_doc: dict | None, limits: list[str]) -> dict:
    final_head = None
    worktree_clean = None
    if summary_doc is not None:
        final_head = summary_doc.get("final_head")
        worktree_clean = summary_doc.get("worktree_clean")
    else:
        limits.append(_LIMIT_RUN_SUMMARY_NOT_RECORDED)
    if final_head is None:
        final_head = run_doc.get("current_head")

    section = {
        "run_id": run_doc.get("run_id"),
        "work_item_id": run_doc.get("work_item_id"),
        "status": run_doc.get("status"),
        "stop_reason": run_doc.get("stop_reason"),
        "hops_used": run_doc.get("hop_used"),
        "hop_limit": run_doc.get("hop_limit"),
        "baseline_head": run_doc.get("baseline_head"),
        "final_head": final_head,
        "worktree_clean": worktree_clean,
        "human_question_present": run_doc.get("human_question") is not None,
        "semantic_stop_present": run_doc.get("semantic_stop") is not None,
    }
    _assert_keys(section, _RUN_KEYS, context="run section")
    return section


def _build_governance_section(plan_doc: dict | None, run_doc: dict, limits: list[str]) -> dict | None:
    if plan_doc is None:
        limits.append(_LIMIT_GOVERNANCE_NOT_RECORDED)
        return None

    contract = plan_doc.get("agent_operating_contract")
    agent_contract = None
    if isinstance(contract, dict):
        agent_contract = {
            "id": contract.get("contract_id"),
            "version": contract.get("version"),
            "sha256": contract.get("sha256"),
        }
        _assert_keys(agent_contract, _AGENT_CONTRACT_KEYS, context="agent_contract")

    governing_inputs = dict(plan_doc.get("governing_inputs") or {})

    component_digests = {}
    for component_id, reference in (plan_doc.get("components") or {}).items():
        if not isinstance(reference, dict):
            continue
        entry = {"path": reference.get("path"), "digest": reference.get("digest")}
        _assert_keys(entry, _COMPONENT_DIGEST_KEYS, context="component_digests entry")
        component_digests[component_id] = entry

    executable = None
    authored_executable = plan_doc.get("executable")
    if isinstance(authored_executable, str) and authored_executable:
        executable = {"name": _basename(authored_executable)}
        _assert_keys(executable, _EXECUTABLE_KEYS, context="executable")

    section = {
        "policy_id": run_doc.get("policy_id"),
        "policy_revision": run_doc.get("policy_revision"),
        "profile_id": run_doc.get("profile_id"),
        "profile_revision": run_doc.get("profile_revision"),
        "agent_contract": agent_contract,
        "governing_inputs": governing_inputs,
        "component_digests": component_digests,
        "executable": executable,
    }
    for key in ("role_bindings", "phase_bindings"):
        if key == "role_bindings" or key in plan_doc:
            section[key] = {
                name: {field: binding.get(field) for field in ("adapter", "model", "reasoning")}
                for name, binding in (plan_doc.get(key) or {}).items()
                if isinstance(binding, dict)
            }
    if "execution_envelope_sha256" in plan_doc:
        section["execution_envelope_sha256"] = plan_doc["execution_envelope_sha256"]
    _assert_keys(section, _GOVERNANCE_KEYS, context="governance section")
    return section


def _evidence_entry(observation: dict, admitted_fingerprints: Mapping[str, Any]) -> dict:
    entry: dict[str, Any] = {
        "command_name": observation.get("command_name"),
        "status": observation.get("status"),
        "exit_code": observation.get("exit_code"),
        "argv_digest": observation.get("argv_digest"),
    }
    if observation.get("error_class") is not None:
        entry["error_class"] = observation["error_class"]
    if observation.get("stdout_sha256") is not None:
        entry["stdout_sha256"] = observation["stdout_sha256"]
    if observation.get("stderr_sha256") is not None:
        entry["stderr_sha256"] = observation["stderr_sha256"]
    admitted = admitted_fingerprints.get(observation.get("command_name"))
    observed = observation.get("stat_fingerprint")
    if admitted is not None and observed is not None:
        entry["executable_identity_stable"] = admitted == observed
    _assert_keys(entry, _EVIDENCE_KEYS, context="hop evidence entry")
    return entry


def _hop_usage_entry(raw_record: Mapping[str, Any] | None) -> dict | None:
    """Project one persisted invocation usage record into the exact,
    allow-listed per-hop audit shape, or ``None`` when no record was
    persisted for this Hop (never guessed, never zero-filled)."""

    if raw_record is None:
        return None
    per_model = []
    for item in raw_record.get("per_model") or []:
        if not isinstance(item, Mapping):
            continue
        model_entry = {
            "model": item.get("model"),
            "input_tokens": item.get("input_tokens"),
            "output_tokens": item.get("output_tokens"),
            "cache_read_tokens": item.get("cache_read_tokens"),
            "cache_creation_tokens": item.get("cache_creation_tokens"),
            "reasoning_output_tokens": item.get("reasoning_output_tokens"),
        }
        _assert_keys(model_entry, _MODEL_USAGE_KEYS, context="hop usage per_model entry")
        per_model.append(model_entry)
    entry = {
        "coverage": raw_record.get("coverage"),
        "input_tokens": raw_record.get("input_tokens"),
        "output_tokens": raw_record.get("output_tokens"),
        "cache_read_tokens": raw_record.get("cache_read_tokens"),
        "cache_creation_tokens": raw_record.get("cache_creation_tokens"),
        "reasoning_output_tokens": raw_record.get("reasoning_output_tokens"),
        "input_includes_cache_read": raw_record.get("input_includes_cache_read"),
        "per_model": per_model,
    }
    _assert_keys(entry, _HOP_USAGE_KEYS, context="hop usage entry")
    return entry


def _usage_figure(values: Sequence[Any]) -> dict:
    known = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
    if not known:
        figure = {"value": None, "lower_bound": False}
    else:
        figure = {"value": sum(known), "lower_bound": len(known) != len(values)}
    _assert_keys(figure, _USAGE_FIGURE_KEYS, context="usage figure")
    return figure


def _usage_metrics(raw_records: Sequence[Mapping[str, Any] | None]) -> dict:
    metrics = {
        name: _usage_figure([record.get(name) if record is not None else None for record in raw_records])
        for name in _USAGE_METRIC_NAMES
    }
    _assert_keys(metrics, frozenset(_USAGE_METRIC_NAMES), context="usage metrics")
    return metrics


def _build_usage_section(
    raw_records: Sequence[Mapping[str, Any] | None], hop_roles: Sequence[str | None],
) -> dict | None:
    """Only when at least one Hop carries a persisted usage record (even one
    whose own coverage is ``none``): the Run-wide totals, per-role and
    per-model aggregates, and coverage counts. Omitted entirely otherwise, so
    an archive with no usage capture at all reprojects byte-identically to
    before this field existed."""

    if not any(record is not None for record in raw_records):
        return None
    coverage_counts = {"complete": 0, "partial": 0, "none": 0}
    for record in raw_records:
        coverage = record.get("coverage") if record is not None else None
        bucket = coverage if coverage in coverage_counts else "none"
        coverage_counts[bucket] += 1
    _assert_keys(coverage_counts, _COVERAGE_COUNT_KEYS, context="usage coverage_counts")

    totals = _usage_metrics(raw_records)

    by_role: dict[str, dict] = {}
    for role in sorted({role for role in hop_roles if role is not None}):
        subset = [
            record for record, hop_role in zip(raw_records, hop_roles, strict=True)
            if hop_role == role
        ]
        by_role[role] = _usage_metrics(subset)

    by_model: dict[str, dict] = {}
    models = sorted({
        record.get("model") for record in raw_records
        if record is not None and isinstance(record.get("model"), str)
    })
    for model in models:
        subset = [
            record for record in raw_records
            if record is not None and record.get("model") == model
        ]
        by_model[model] = _usage_metrics(subset)

    section = {
        "coverage_counts": coverage_counts, "totals": totals, "by_role": by_role, "by_model": by_model,
    }
    _assert_keys(section, _USAGE_SUMMARY_KEYS, context="usage section")
    return section


def _build_hops_section(
    run_doc: dict, plan_doc: dict | None, exec_evidence_doc: dict | None,
    usage_doc: dict | None, limits: list[str],
) -> tuple[list[dict], list[Mapping[str, Any] | None], list[str | None]]:
    role_bindings = (plan_doc or {}).get("role_bindings") or {}
    phase_bindings = (plan_doc or {}).get("phase_bindings")

    admitted_fingerprints: dict[str, Any] = {}
    if plan_doc is not None:
        for command in (plan_doc.get("evidence") or {}).get("selected_commands", []) or []:
            name = command.get("command_name")
            if name:
                admitted_fingerprints[name] = command.get("stat_fingerprint")

    attempts_by_hop: dict[str, dict] = {}
    if exec_evidence_doc is not None:
        for attempt in exec_evidence_doc.get("attempts", []) or []:
            hop_id = attempt.get("hop_id")
            if hop_id:
                attempts_by_hop[hop_id] = attempt
    else:
        limits.append(_LIMIT_TRUSTED_EVIDENCE_NOT_RECORDED)

    raw_hops = sorted(
        run_doc.get("hops", []) or [],
        key=lambda hop: (hop.get("sequence") if isinstance(hop.get("sequence"), int) else 0, hop.get("hop_id") or ""),
    )

    hops_section: list[dict] = []
    raw_usage_records: list[Mapping[str, Any] | None] = []
    hop_roles: list[str | None] = []
    for hop in raw_hops:
        role = hop.get("role")
        attempt = attempts_by_hop.get(hop.get("hop_id"))
        phase = hop.get("phase")
        if phase_bindings is not None and phase is None and attempt is not None:
            # Legacy Hop summaries omit the phase. The execution attempt records it.
            phase = attempt.get("phase")
        binding = phase_bindings.get(phase) if phase_bindings is not None else role_bindings.get(role)
        if not isinstance(binding, dict):
            binding = {}
        evidence_list = []
        if attempt is not None:
            for observation in attempt.get("trusted_observations", []) or []:
                evidence_list.append(_evidence_entry(observation, admitted_fingerprints))

        raw_usage_record = None
        if usage_doc is not None:
            raw_usage_record = (usage_doc.get("records") or {}).get(hop.get("hop_id"))
            if not isinstance(raw_usage_record, Mapping):
                raw_usage_record = None
        raw_usage_records.append(raw_usage_record)
        hop_roles.append(role)

        entry = {
            "hop_id": hop.get("hop_id"),
            "sequence": hop.get("sequence"),
            "phase": phase,
            "role": role,
            "adapter": binding.get("adapter"),
            "model": binding.get("model"),
            "reasoning": binding.get("reasoning"),
            "outcome": hop.get("outcome"),
            "applied_route": hop.get("applied_route"),
            "commits": list(hop.get("actual_commits") or []),
            "finding_delta_ids": list(hop.get("finding_delta_ids") or []),
            "usage": _hop_usage_entry(raw_usage_record),
            "evidence": evidence_list,
        }
        _assert_keys(entry, _HOP_KEYS, context="hop entry")
        hops_section.append(entry)
    return hops_section, raw_usage_records, hop_roles


def _build_findings_section(run_doc: dict) -> list[dict]:
    findings_section = []
    for finding in run_doc.get("findings", []) or []:
        entry = {
            "finding_id": finding.get("finding_id"),
            "state": finding.get("state"),
            "blocking": finding.get("blocking"),
            "disposition": finding.get("disposition"),
            "introduced_by_hop": finding.get("introduced_by_hop"),
            "changed_by_hop": finding.get("changed_by_hop"),
        }
        _assert_keys(entry, _FINDING_KEYS, context="finding entry")
        findings_section.append(entry)
    return findings_section


def _build_lifecycle_section(lifecycle: Mapping[str, Any] | None, limits: list[str]) -> dict | None:
    if lifecycle is None:
        limits.append(_LIMIT_NO_LIFECYCLE_FACTS)
        return None

    preservation_in = lifecycle.get("preservation")
    preservation_out = None
    if isinstance(preservation_in, Mapping):
        preservation_out = {
            "outcome": preservation_in.get("outcome"),
            "ref": preservation_in.get("ref"),
            "commit": preservation_in.get("commit"),
            "tree": preservation_in.get("tree"),
            "file_count": preservation_in.get("file_count"),
            "bytes": preservation_in.get("bytes"),
            "quiescence": preservation_in.get("quiescence"),
            "reason_code": preservation_in.get("reason_code"),
        }
        _assert_keys(preservation_out, _PRESERVATION_KEYS, context="preservation fact")
    else:
        limits.append(_LIMIT_PRESERVATION_NOT_RECORDED)

    section = {"classification": lifecycle.get("classification"), "preservation": preservation_out}
    _assert_keys(section, _LIFECYCLE_KEYS, context="lifecycle section")
    return section


def _build_raw_section(manifest: dict, manifest_path: Path, raw_retention: str) -> dict:
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    files = []
    for entry in manifest["copied_files"]:
        if not isinstance(entry, dict):
            continue
        file_entry = {
            "path": entry.get("path"),
            "sha256": entry.get("sha256"),
            "bytes": entry.get("bytes"),
        }
        _assert_keys(file_entry, _RAW_FILE_KEYS, context="raw file entry")
        files.append(file_entry)
    files.sort(key=lambda item: item["path"] or "")
    section = {"retention": raw_retention, "manifest_sha256": manifest_sha256, "files": files}
    _assert_keys(section, _RAW_KEYS, context="raw section")
    return section


def _whole_component_or_token_match(value: str, names: Sequence[str]) -> bool:
    """True when ``value`` contains one of ``names`` as an exact path
    component (split on ``/``/``\\``) or an exact token (split on anything
    but letters, digits and underscore) -- never as a mere substring of a
    longer component or token. Used for matching that must not collide on
    an incidental substring, such as a short OS user name."""

    wanted = {name for name in names if name}
    if not wanted:
        return False
    pieces = set(_PATH_COMPONENT_RE.split(value)) | set(_TOKEN_RE.split(value))
    return bool(wanted & pieces)


def _string_violation(
    value: str, private_strings: Sequence[str], private_tokens: Sequence[str] = (),
) -> bool:
    stripped = value
    if stripped.startswith("/") or _WINDOWS_ABS_RE.match(stripped) or stripped.startswith("\\\\"):
        return True
    if _REMOTE_URL_RE.search(stripped):
        return True
    for private in private_strings:
        if private and private in value:
            return True
    return _whole_component_or_token_match(value, private_tokens)


def _guard_privacy(
    value: Any, path: str, private_strings: Sequence[str], private_tokens: Sequence[str] = (),
) -> None:
    if isinstance(value, str):
        if _string_violation(value, private_strings, private_tokens):
            raise ProjectionError(f"privacy guard refused projection at {path or '<root>'}")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and _string_violation(key, private_strings, private_tokens):
                raise ProjectionError(f"privacy guard refused projection at {path}.<key>")
            child_path = f"{path}.{key}" if path else str(key)
            _guard_privacy(item, child_path, private_strings, private_tokens)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _guard_privacy(item, f"{path}[{index}]", private_strings, private_tokens)


def project_audit(
    attempt_dir: Path | str,
    *,
    raw_retention: str = "local",
    lifecycle: Mapping[str, Any] | None = None,
    private_strings: Sequence[str] = (),
    private_tokens: Sequence[str] = (),
) -> dict:
    """Build the ``wfc.audit/1`` projection of ``attempt_dir``.

    Pure and read-only: no write, no Git operation, no re-verification of the
    raw archive (``wfc archive`` already verified it). Raises ``AuditError``
    when the attempt directory is not a projectable archive, and
    ``ProjectionError`` when the built projection would carry an absolute
    path, a caller-supplied private string or private token, or a Git remote
    URL. ``private_strings`` matches as a plain substring; ``private_tokens``
    matches only a whole path component or whole token (see
    ``_whole_component_or_token_match``), so a short value (for example an
    OS user name) does not collide with an unrelated field that merely
    contains it as a substring.
    """

    attempt_dir = Path(attempt_dir)
    manifest_path = attempt_dir / "manifest.json"
    manifest = _load_manifest(attempt_dir)
    copied = _copied_relpaths(manifest)

    attempt_id = manifest.get("attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id:
        raise AuditError("manifest.json carries no usable attempt_id")

    checkout = _find_checkout(copied)
    if checkout is None:
        raise AuditError("no run.json found among the archived files; not a projectable attempt")
    checkout_id, run_relpath = checkout
    run_doc = _read_copied_json(attempt_dir, run_relpath, copied)
    if run_doc is None:
        raise AuditError(f"{run_relpath} is listed in the manifest but is not readable")

    limits: list[str] = []

    summary_doc = _read_copied_json(attempt_dir, "run/operator-summary.json", copied)
    plan_doc = _read_copied_json(attempt_dir, "run/operator-plan.json", copied)
    exec_evidence_doc = _read_copied_json(
        attempt_dir, f"run/checkouts/{checkout_id}/execution-evidence.json", copied,
    )
    usage_doc = _read_copied_json(attempt_dir, f"run/checkouts/{checkout_id}/usage.json", copied)

    run_section = _build_run_section(run_doc, summary_doc, limits)
    governance_section = _build_governance_section(plan_doc, run_doc, limits)
    hops_section, raw_usage_records, hop_roles = _build_hops_section(
        run_doc, plan_doc, exec_evidence_doc, usage_doc, limits
    )
    findings_section = _build_findings_section(run_doc)
    anomaly_count = len(run_doc.get("anomalies") or [])
    lifecycle_section = _build_lifecycle_section(lifecycle, limits)
    raw_section = _build_raw_section(manifest, manifest_path, raw_retention)
    usage_section = _build_usage_section(raw_usage_records, hop_roles)

    if usage_section is None:
        limits.append(_LIMIT_USAGE_NOT_REPORTED)
    else:
        counts = usage_section["coverage_counts"]
        if counts["complete"] + counts["partial"] == 0:
            limits.append(_LIMIT_USAGE_NOT_REPORTED)
        elif counts["partial"] > 0 or counts["none"] > 0:
            limits.append(_LIMIT_USAGE_PARTIALLY_REPORTED)

    audit = {
        "schema": SCHEMA,
        "attempt_id": attempt_id,
        "run": run_section,
        "governance": governance_section,
        "hops": hops_section,
        "findings": findings_section,
        "anomaly_count": anomaly_count,
        "lifecycle": lifecycle_section,
        "raw": raw_section,
        "limits": sorted(set(limits)),
    }
    if usage_section is not None:
        audit["usage"] = usage_section
    _assert_keys(audit, _TOP_KEYS, context="audit document")

    _guard_privacy(
        audit, "", tuple(s for s in private_strings if s), tuple(t for t in private_tokens if t),
    )
    return audit


def render_audit_json(audit: Mapping[str, Any]) -> str:
    """Deterministic bytes for one audit document: sorted keys, no timestamps."""

    return json.dumps(audit, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _create_only_write(target: Path, text: str) -> None:
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if existing == text:
            return
        raise AuditError(f"refusing to overwrite existing audit.json with different content: {target}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def write_audit(
    attempt_dir: Path | str,
    *,
    raw_retention: str = "local",
    lifecycle: Mapping[str, Any] | None = None,
    private_strings: Sequence[str] = (),
    private_tokens: Sequence[str] = (),
) -> Path:
    """Project and write ``audit.json`` beside ``manifest.json``, create-only.

    Idempotent for byte-identical content; refuses a different existing
    ``audit.json``. Never edits ``manifest.json`` or any raw file, and
    performs no Git operation.
    """

    attempt_dir = Path(attempt_dir)
    audit = project_audit(
        attempt_dir, raw_retention=raw_retention, lifecycle=lifecycle,
        private_strings=private_strings, private_tokens=private_tokens,
    )
    text = render_audit_json(audit)
    target = attempt_dir / "audit.json"
    _create_only_write(target, text)
    return target


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wfc audit",
        description="Project a sanitized wfc.audit/1 record beside an already archived Attempt's manifest.json.",
    )
    parser.add_argument("attempt_dir", metavar="attempt-dir", help="an archived Attempt directory (contains manifest.json)")
    parser.add_argument("--raw-retention", choices=_RAW_RETENTION_CHOICES, default="local")
    parser.add_argument("--lifecycle-json", metavar="FILE", default=None, help="caller-supplied lifecycle facts, stored verbatim within the allow-list")
    parser.add_argument("--private-string", action="append", default=[], metavar="S", dest="private_strings")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    lifecycle = None
    if args.lifecycle_json is not None:
        try:
            lifecycle = json.loads(Path(args.lifecycle_json).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"wfc audit: --lifecycle-json is not readable JSON: {error}", file=sys.stderr)
            return 2
        if not isinstance(lifecycle, dict):
            print("wfc audit: --lifecycle-json must contain a JSON object", file=sys.stderr)
            return 2
    try:
        target = write_audit(
            args.attempt_dir, raw_retention=args.raw_retention, lifecycle=lifecycle,
            private_strings=tuple(args.private_strings),
        )
    except ProjectionError as error:
        print(f"wfc audit: {error}", file=sys.stderr)
        return 3
    except AuditError as error:
        print(f"wfc audit: {error}", file=sys.stderr)
        return 2
    print(f"AUDIT: {target}")
    return 0
