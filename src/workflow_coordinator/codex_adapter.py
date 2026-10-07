"""Codex CLI transport adapter and its OpenAI strict-schema projection.

This module owns Codex argv/process behavior and the OpenAI Structured Outputs
dialect.  It derives that dialect projection from canonical semantic facts in
``serialization``; it neither parses nor accepts a semantic RoutingResult.
"""

from __future__ import annotations

import os
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
from .persistence import atomic_write_json, atomic_write_text
from .serialization import (
    ESCALATION_KINDS,
    ESCALATION_REQUIRED_KEYS,
    COMMIT_INTENT_REQUIRED_KEYS,
    EXISTING_FINDING_DELTA_KEYS,
    NEW_FINDING_DELTA_KEYS,
    NEW_FINDING_DELTA_V2_KEYS,
    ROUTING_RESULT_REQUIRED_KEYS,
    ROUTING_RESULT_V2_REQUIRED_KEYS,
    STRUCTURED_RESULT_ENVELOPE_KEYS,
)
from .model import (
    ConvergenceAssessmentResult,
    CoverageDisposition,
    DiagnosisClassification,
    FindingState,
    Outcome,
    ResultContractKind,
    StrategyAssessmentResult,
    StrategyRevisionKind,
)


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


def routing_result_openai_schema(contract_version: int = 1) -> dict[str, Any]:
    """Project canonical RoutingResult facts into the Codex strict dialect."""
    if contract_version not in {1, 2}:
        raise OpenAIStrictSchemaError("unsupported RoutingResult contract version")
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
    if contract_version == 2:
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
            "requested_route": {"type": "string"}, "scope_changed": {"type": "boolean"},
            "requires_human": {"type": "boolean"}, "escalation": {"anyOf": [{"type": "null"}, escalation]},
            "findings": {"type": "array", "items": finding}, "claimed_commits": string_array,
            "commit_intent": {"anyOf": [{"type": "null"}, commit_intent]},
            "evidence_refs": string_array, "summary": {"type": "string"},
        }
    if contract_version == 2:
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


def structured_result_envelope_openai_schema(
    invocation: Invocation, contract_version: int = 1
) -> dict[str, Any]:
    """Return the exact strict schema supplied by ``CodexCliAdapter``.

    Correlation identity is Coordinator-owned dispatch data known exactly before
    the provider is entered, so each identity field projects as a one-element
    typed enum of its dispatched value instead of free generative text.  This
    only narrows what the provider may emit; ``adapter.validated_result``
    remains the independent provider-neutral byte-exact correlation authority
    over whatever the returned envelope actually contains.
    """
    def dispatched(value: str) -> dict[str, Any]:
        return {"type": "string", "enum": [value]}
    return {
        "type": "object", "additionalProperties": False,
        "required": list(STRUCTURED_RESULT_ENVELOPE_KEYS),
        "properties": {
            "schema_version": {"type": "integer", "enum": [contract_version]},
            "run_id": dispatched(invocation.run_id), "hop_id": dispatched(invocation.hop_id),
            "invocation_id": dispatched(invocation.invocation_id),
            "result": routing_result_openai_schema(contract_version),
        },
    }


@dataclass(frozen=True)
class CodexCliAdapter:
    """Concrete single-provider implementation of the execution port."""

    result_directory: Path
    model: str
    reasoning_effort: str
    executable: str = "codex"

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
            "-c", f'model_reasoning_effort="{self.reasoning_effort}"', "--cd", str(request.repository.resolve()),
            "--output-schema", str(schema), "--output-last-message", str(result), "-",
        )

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionOutcome:
        if request.result_contract != ROUTING_RESULT_ENVELOPE_CONTRACT:
            raise CodexCliError(f"unsupported result contract: {request.result_contract}")
        if request.result_contract_kind is not ResultContractKind.GENERIC_ROUTING_RESULT:
            raise CodexCliError(f"unsupported result contract kind: {request.result_contract_kind}")
        if request.timeout_seconds <= 0:
            raise CodexCliError("timeout must be positive")
        if request.contract_version not in {1, 2}:
            raise CodexCliError("unsupported result contract version")
        try:
            schema = self.result_directory / "structured-result.schema.json"
            result = self.result_directory / "result-message.json"
            atomic_write_json(
                schema,
                structured_result_envelope_openai_schema(
                    request.invocation, request.contract_version
                ),
            )
            atomic_write_text(result, "")
            child = subprocess.Popen(
                self.argv(request, schema, result), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, start_new_session=True,
            )
        except (OSError, ValueError) as error:
            raise CodexCliError("local pre-acceptance executable launch failure") from error
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
        candidate = result.read_text(encoding="utf-8") or None
        diagnostics = () if child.returncode == 0 and not timed_out else (
            "Codex CLI timed out" if timed_out else f"Codex CLI exited {child.returncode}",
        )
        return AgentExecutionOutcome(candidate, child.returncode, timed_out, _bounded(stdout), _bounded(stderr), diagnostics)
