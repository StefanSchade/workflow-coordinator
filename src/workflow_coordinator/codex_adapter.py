"""Codex CLI transport adapter and its OpenAI strict-schema projection.

This module owns Codex argv/process behavior and the OpenAI Structured Outputs
dialect.  It derives that dialect projection from canonical semantic facts in
``serialization``; it neither parses nor accepts a semantic RoutingResult.
"""

from __future__ import annotations

import contextlib
import json
import os
import copy
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapter import (
    ROUTING_RESULT_ENVELOPE_CONTRACT,
    AgentExecutionOutcome,
    AgentExecutionRequest,
    ExecutionNotStartedError,
    Invocation,
)
from .execution_launcher import (
    BUBBLEWRAP,
    BoundaryGrant,
    LaunchPlan,
    close_started_signal_write_end,
    direct_grant,
    open_started_signal,
    prepare,
    provider_started,
)
from .persistence import atomic_write_json, atomic_write_text
from .serialization import (
    ESCALATION_KINDS,
    ESCALATION_REQUIRED_KEYS,
    COMMIT_INTENT_REQUIRED_KEYS,
    EXISTING_FINDING_DELTA_KEYS,
    FINDING_CITATION_KEYS,
    FINDING_TRANSITION_KEYS,
    NEW_FINDING_DELTA_KEYS,
    NEW_FINDING_DELTA_V2_KEYS,
    ROUTING_RESULT_REQUIRED_KEYS,
    ROUTING_RESULT_V2_REQUIRED_KEYS,
)
from .model import (
    ConvergenceAssessmentResult,
    CoverageDisposition,
    DiagnosisClassification,
    FindingState,
    NO_USAGE_REPORTED,
    Outcome,
    ResultContractKind,
    UsageRecord,
    admits_result_contract_version,
    coerce_token_count,
    usage_coverage_for,
    StrategyAssessmentResult,
    StrategyRevisionKind,
)
from .structured_result_schema import StructuredResultSchemaError, structured_result_envelope_schema


class CodexCliError(ExecutionNotStartedError, RuntimeError):
    """A local pre-launch Codex setup or launch failure."""


class OpenAIStrictSchemaError(ValueError):
    """The Codex Structured Outputs projection leaves the supported subset."""


_STRICT_KEYS = frozenset({"type", "additionalProperties", "required", "properties", "items", "enum", "anyOf"})


def assert_openai_strict_schema(schema: dict[str, Any]) -> None:
    """Check the documented strict subset used by this exact projection offline."""
    def walk(node: Any, location: str) -> None:
        if not isinstance(node, dict):
            raise OpenAIStrictSchemaError(f"{location}: schema node is not an object")
        unsupported = set(node) - _STRICT_KEYS
        if unsupported:
            raise OpenAIStrictSchemaError(f"{location}: unsupported keywords {sorted(unsupported)}")
        if "anyOf" in node:
            if set(node) != {"anyOf"}:
                raise OpenAIStrictSchemaError(f"{location}: composition must be a pure anyOf node")
            branches = node["anyOf"]
            if not isinstance(branches, list) or not branches:
                raise OpenAIStrictSchemaError(f"{location}: anyOf must have branches")
            for index, branch in enumerate(branches):
                walk(branch, f"{location}.anyOf[{index}]")
            return
        node_type = node.get("type")
        if node_type is None:
            raise OpenAIStrictSchemaError(f"{location}: explicit type is required")
        types = node_type if isinstance(node_type, list) else [node_type]
        if not types or not all(item in {"object", "array", "string", "integer", "boolean", "null"} for item in types):
            raise OpenAIStrictSchemaError(f"{location}: unsupported type")
        if "object" in types:
            properties = node.get("properties")
            if not isinstance(properties, dict) or node.get("additionalProperties") is not False:
                raise OpenAIStrictSchemaError(f"{location}: object must be strict")
            if set(node.get("required", [])) != set(properties):
                raise OpenAIStrictSchemaError(f"{location}: required must exactly cover properties")
            for name, child in properties.items():
                walk(child, f"{location}.properties.{name}")
        if "array" in types:
            if "items" not in node:
                raise OpenAIStrictSchemaError(f"{location}: array requires items")
            walk(node["items"], f"{location}.items")
    walk(schema, "$")


def _bounded(value: str, limit: int = 16_384) -> str:
    return value[-limit:]


_CODEX_USAGE_FIELDS = (
    "input_tokens", "output_tokens", "cached_input_tokens",
    "cache_write_input_tokens", "reasoning_output_tokens",
)


def _usage_from_stdout(stdout: str) -> UsageRecord:
    """Parse Codex's token usage from the full, un-bounded ``exec --json``
    stdout stream.

    Collects every ``turn.completed`` event's ``usage`` object across the
    whole invocation (Codex may emit more than one per invocation). Rule for
    multiple events, per field: sum across events only when every single
    event reports that field as a well-formed non-negative integer; if any
    event is missing it, has the wrong type, or the event itself is not a
    well-formed usage object, that one figure becomes unknown (``None``)
    rather than a guessed or partial sum -- never raises, never alters
    acceptance. A single event is the degenerate case of this same rule. No
    event at all (including fully malformed/truncated stdout) yields
    ``NO_USAGE_REPORTED``.
    """

    usage_events: list[object] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError, RecursionError):
            continue
        if isinstance(event, dict) and event.get("type") == "turn.completed":
            usage_events.append(event.get("usage"))
    if not usage_events:
        return NO_USAGE_REPORTED

    totals: dict[str, int | None] = {}
    for field_name in _CODEX_USAGE_FIELDS:
        total = 0
        well_formed = True
        for usage in usage_events:
            if not isinstance(usage, dict):
                well_formed = False
                break
            value = coerce_token_count(usage.get(field_name))
            if value is None:
                well_formed = False
                break
            total += value
        totals[field_name] = total if well_formed else None

    return UsageRecord(
        coverage=usage_coverage_for(totals["input_tokens"], totals["output_tokens"]),
        input_tokens=totals["input_tokens"],
        output_tokens=totals["output_tokens"],
        cache_read_tokens=totals["cached_input_tokens"],
        cache_creation_tokens=totals["cache_write_input_tokens"],
        reasoning_output_tokens=totals["reasoning_output_tokens"],
        # Codex's ``input_tokens`` already includes ``cached_input_tokens``
        # (cache read); unlike Claude, it is not reported net of cache.
        input_includes_cache_read=True,
        per_model=(),
    )


def routing_result_openai_schema(
    contract_version: int = 1, *, permitted_routes: tuple[str, ...]
) -> dict[str, Any]:
    """Project canonical RoutingResult facts into the Codex strict dialect."""
    return _result_openai_schema(contract_version, permit_v3=False, permitted_routes=permitted_routes)


def _result_openai_schema(
    contract_version: int, *, permit_v3: bool, permitted_routes: tuple[str, ...]
) -> dict[str, Any]:
    """Build the shared result projection for one already-admitted family."""
    if contract_version not in ({1, 2, 3} if permit_v3 else {1, 2}):
        raise OpenAIStrictSchemaError("unsupported RoutingResult contract version")
    if not permitted_routes:
        raise OpenAIStrictSchemaError(
            "requested_route enum requires at least one current-phase permitted route"
        )
    string_array = {"type": "array", "items": {"type": "string"}}
    new_finding = {
        "type": "object", "additionalProperties": False,
        "required": list(
            NEW_FINDING_DELTA_KEYS
            if contract_version == 1
            else NEW_FINDING_DELTA_V2_KEYS
        ),
        "properties": {
            "finding_id": {"type": "null"}, "invariant": {"type": "string"},
            "blocking": {"type": "boolean"},
            "proposed_state": {"type": "string", "enum": [state.value for state in FindingState]},
            "evidence_refs": string_array, "successor_ref": {"type": ["string", "null"]},
            "new_material_evidence": {"type": "boolean"},
        },
    }


    if contract_version in {2, 3}:
        new_finding["properties"]["local_ref"] = {"type": ["string", "null"]}
    existing_finding = {
        "type": "object", "additionalProperties": False, "required": list(EXISTING_FINDING_DELTA_KEYS),
        "properties": {
            "finding_id": {"type": "string"},
            "proposed_state": {"type": "string", "enum": [state.value for state in FindingState]},
            "evidence_refs": string_array, "successor_ref": {"type": ["string", "null"]},
            "new_material_evidence": {"type": "boolean"},
        },
    }
    if contract_version == 3:
        citation = {
            "type": "object", "additionalProperties": False,
            "required": list(FINDING_CITATION_KEYS),
            "properties": {
                "finding_id": {"type": "string"}, "evidence_refs": string_array,
            },
        }

        def target(properties: dict[str, Any]) -> dict[str, Any]:
            return {
                "type": "object", "additionalProperties": False,
                "required": list(properties), "properties": properties,
            }

        transition = {
            "type": "object", "additionalProperties": False,
            "required": list(FINDING_TRANSITION_KEYS),
            "properties": {
                "finding_id": {"type": "string"}, "evidence_refs": string_array,
                "target": {"anyOf": [
                    target({"state": {"type": "string", "enum": [FindingState.RESOLVED.value]}}),
                    target({"state": {"type": "string", "enum": [FindingState.RESIDUAL.value]}}),
                    target({
                        "state": {"type": "string", "enum": [FindingState.SUPERSEDED.value]},
                        "successor_ref": {"type": "string"},
                    }),
                    target({
                        "state": {"type": "string", "enum": [FindingState.OPEN.value]},
                        "new_material_evidence": {"type": "boolean"},
                    }),
                ]},
            },
        }
        finding = {"anyOf": [new_finding, citation, transition]}
    else:
        finding = {"anyOf": [new_finding, existing_finding]}
    escalation = {
        "type": "object", "additionalProperties": False, "required": list(ESCALATION_REQUIRED_KEYS),
        "properties": {"kind": {"type": "string", "enum": list(ESCALATION_KINDS)}, "question": {"type": "string"}},
    }
    commit_intent = {
        "type": "object", "additionalProperties": False,
        "required": list(COMMIT_INTENT_REQUIRED_KEYS),
        "properties": {"paths": string_array, "subject": {"type": "string"}},
    }
    properties = {
            "schema_version": {"type": "integer", "enum": [contract_version]},
            "outcome": {"type": "string", "enum": [outcome.value for outcome in Outcome]},
            "requested_route": {"type": "string", "enum": list(permitted_routes)}, "scope_changed": {"type": "boolean"},
            "requires_human": {"type": "boolean"}, "escalation": {"anyOf": [{"type": "null"}, escalation]},
            "findings": {"type": "array", "items": finding}, "claimed_commits": string_array,
            "commit_intent": {"anyOf": [{"type": "null"}, commit_intent]},
            "evidence_refs": string_array, "summary": {"type": "string"},
        }
    if contract_version in {2, 3}:
        coverage_claim = {
            "type": "object", "additionalProperties": False,
            "required": ["finding_id", "disposition", "evidence_refs"],
            "properties": {
                "finding_id": {"type": "string"},
                "disposition": {
                    "type": "string",
                    "enum": [item.value for item in CoverageDisposition],
                },
                "evidence_refs": string_array,
            },
        }
        assessment = {
            "type": "object", "additionalProperties": False,
            "required": ["finding_id", "correction_hop_id", "result", "evidence_refs"],
            "properties": {
                "finding_id": {"type": "string"},
                "correction_hop_id": {"type": "string"},
                "result": {
                    "type": "string",
                    "enum": [item.value for item in ConvergenceAssessmentResult],
                },
                "evidence_refs": string_array,
            },
        }
        diagnosis = {
            "type": "object", "additionalProperties": False,
            "required": ["escape_hop_id", "finding_ids", "classification", "analysis", "evidence_refs"],
            "properties": {"escape_hop_id": {"type": "string"}, "finding_ids": string_array,
                           "classification": {"type": "string", "enum": [item.value for item in DiagnosisClassification]},
                           "analysis": {"type": "string"}, "evidence_refs": string_array},
        }
        revision = {
            "type": "object", "additionalProperties": False,
            "required": ["escape_hop_id", "diagnosis_hop_id", "finding_ids", "kind", "plan", "evidence_refs"],
            "properties": {"escape_hop_id": {"type": "string"}, "diagnosis_hop_id": {"type": "string"},
                           "finding_ids": string_array, "kind": {"type": "string", "enum": [item.value for item in StrategyRevisionKind]},
                           "plan": {"type": "string"}, "evidence_refs": string_array},
        }
        strategy_assessment = {
            "type": "object", "additionalProperties": False,
            "required": ["escape_hop_id", "diagnosis_hop_id", "revision_hop_id", "finding_ids", "result", "evidence_refs"],
            "properties": {"escape_hop_id": {"type": "string"}, "diagnosis_hop_id": {"type": "string"},
                           "revision_hop_id": {"type": "string"}, "finding_ids": string_array,
                           "result": {"type": "string", "enum": [item.value for item in StrategyAssessmentResult]},
                           "evidence_refs": string_array},
        }
        properties.update({
            "coverage_claims": {"type": "array", "items": coverage_claim},
            "convergence_assessments": {"type": "array", "items": assessment},
            "diagnosis": {"anyOf": [{"type": "null"}, diagnosis]},
            "strategy_revision": {"anyOf": [{"type": "null"}, revision]},
            "strategy_assessment": {"anyOf": [{"type": "null"}, strategy_assessment]},
        })
    return {
        "type": "object", "additionalProperties": False,
        "required": list(
            ROUTING_RESULT_REQUIRED_KEYS
            if contract_version == 1
            else ROUTING_RESULT_V2_REQUIRED_KEYS
        ),
        "properties": properties,
    }


def projected_result_openai_schema(
    kind: ResultContractKind, contract_version: int, *, permitted_routes: tuple[str, ...]
) -> dict[str, Any]:
    """Project one selected semantic family without widening it to RoutingResult."""
    if kind is ResultContractKind.GENERIC_ROUTING_RESULT:
        return routing_result_openai_schema(contract_version, permitted_routes=permitted_routes)
    if kind not in {ResultContractKind.WORK_RESULT, ResultContractKind.REVIEW_VERDICT}:
        raise OpenAIStrictSchemaError(f"unsupported result contract kind: {kind}")
    if not admits_result_contract_version(kind, contract_version):
        raise OpenAIStrictSchemaError("unsupported result contract version")
    schema = copy.deepcopy(
        _result_openai_schema(contract_version, permit_v3=True, permitted_routes=permitted_routes)
    )
    result = schema["properties"]
    required = schema["required"]
    result.pop("outcome")
    required.remove("outcome")
    # Convergence records remain exclusively on the untouched generic family.
    for name in ("coverage_claims", "convergence_assessments", "diagnosis", "strategy_revision", "strategy_assessment"):
        result.pop(name, None)
        if name in required:
            required.remove(name)
    field, values = (
        ("status", [Outcome.COMPLETED.value, Outcome.BLOCKED.value])
        if kind is ResultContractKind.WORK_RESULT
        else ("verdict", ["accept", Outcome.CHANGES_REQUIRED.value])
    )
    result[field] = {"type": "string", "enum": values}
    required.append(field)
    return schema


def structured_result_envelope_openai_schema(
    invocation: Invocation, contract_version: int = 1,
    result_contract_kind: ResultContractKind = ResultContractKind.GENERIC_ROUTING_RESULT,
    *, permitted_routes: tuple[str, ...], finding_authority: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Return the shared closed WFC schema in Codex's strict dialect."""
    try:
        return structured_result_envelope_schema(
            invocation, contract_version, result_contract_kind,
            permitted_routes=permitted_routes, finding_authority=finding_authority,
        )
    except StructuredResultSchemaError as error:
        raise OpenAIStrictSchemaError(str(error)) from error


@dataclass(frozen=True)
class CodexCliAdapter:
    """Concrete single-provider implementation of the execution port."""

    result_directory: Path
    model: str
    reasoning_effort: str
    executable: str = "codex"
    # None (the default) means today's unisolated behaviour; see the matching
    # field on ClaudeCliAdapter.
    boundary: BoundaryGrant | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("model", self.model),
            ("reasoning_effort", self.reasoning_effort),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Codex CLI {name} must be explicit and non-empty")
            if value.splitlines() != [value] or "\0" in value:
                raise ValueError(f"Codex CLI {name} must be a single-line value")

    def argv(self, request: AgentExecutionRequest, schema: Path, result: Path) -> tuple[str, ...]:
        return (
            self.executable, "--ask-for-approval", "never", "exec", "--ephemeral", "--json",
            "--color", "never", "--sandbox", "workspace-write", "--model", self.model,
            "-c", f'model_reasoning_effort="{self.reasoning_effort}"',
            "-c", "sandbox_workspace_write.network_access=true", "--cd", str(request.repository.resolve()),
            "--output-schema", str(schema), "--output-last-message", str(result), "-",
        )

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionOutcome:
        if request.result_contract != ROUTING_RESULT_ENVELOPE_CONTRACT:
            raise CodexCliError(f"unsupported result contract: {request.result_contract}")
        if request.result_contract_kind not in set(ResultContractKind):
            raise CodexCliError(f"unsupported result contract kind: {request.result_contract_kind}")
        if request.timeout_seconds <= 0:
            raise CodexCliError("timeout must be positive")
        if not admits_result_contract_version(
            request.result_contract_kind, request.contract_version
        ):
            raise CodexCliError("unsupported result contract version")
        if not request.permitted_routes:
            raise CodexCliError(
                "current phase has no permitted routes; refusing to dispatch an unroutable invocation"
            )
        grant = self.boundary if self.boundary is not None else direct_grant(request.repository.resolve())
        started_signal = None
        try:
            try:
                started_signal = open_started_signal() if grant.mode == BUBBLEWRAP else None
                schema = self.result_directory / "structured-result.schema.json"
                result = self.result_directory / "result-message.json"
                atomic_write_json(
                    schema,
                    structured_result_envelope_openai_schema(
                        request.invocation, request.contract_version, request.result_contract_kind,
                        permitted_routes=request.permitted_routes,
                        finding_authority=request.finding_authority,
                    ),
                )
                atomic_write_text(result, "")
                plan = LaunchPlan(
                    grant, self.argv(request, schema, result), None, None,
                    status_fd=None if started_signal is None else started_signal.write_fd,
                )
                prepared = prepare(plan)
                child = subprocess.Popen(
                    prepared.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, start_new_session=True,
                    pass_fds=() if started_signal is None else (started_signal.write_fd,),
                )
            except (OSError, ValueError) as error:
                raise CodexCliError("local pre-acceptance executable launch failure") from error
            finally:
                if started_signal is not None:
                    close_started_signal_write_end(started_signal)
            try:
                stdout, stderr = child.communicate(request.prompt, timeout=request.timeout_seconds)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    stdout, stderr = child.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    stdout, stderr = child.communicate()
            started = provider_started(started_signal) if started_signal is not None else True
            if not timed_out and child.returncode != 0 and not started:
                # The started-signal pipe never received an `exit-code`
                # document: bwrap's own setup failed before Codex ever ran
                # (not-started), proven by a descriptor the confined process
                # can neither write nor withdraw -- never by guessing from
                # stderr text.
                raise CodexCliError("execution boundary failed before the provider started")
        finally:
            if started_signal is not None:
                with contextlib.suppress(OSError):
                    os.close(started_signal.read_fd)
        candidate = result.read_text(encoding="utf-8") or None
        usage = _usage_from_stdout(stdout)
        diagnostics = () if child.returncode == 0 and not timed_out else (
            "Codex CLI timed out" if timed_out else f"Codex CLI exited {child.returncode}",
        )
        return AgentExecutionOutcome(
            candidate, child.returncode, timed_out, _bounded(stdout), _bounded(stderr), diagnostics,
            usage=usage,
        )
