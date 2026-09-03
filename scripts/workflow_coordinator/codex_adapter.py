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
)
from .persistence import atomic_write_json, atomic_write_text
from .serialization import (
    ESCALATION_KINDS,
    ESCALATION_REQUIRED_KEYS,
    COMMIT_INTENT_REQUIRED_KEYS,
    FINDING_DELTA_KEYS,
    ROUTING_RESULT_REQUIRED_KEYS,
    STRUCTURED_RESULT_ENVELOPE_KEYS,
)
from .model import FindingState, Outcome


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


def routing_result_openai_schema() -> dict[str, Any]:
    """Project canonical RoutingResult facts into the Codex strict dialect."""
    string_array = {"type": "array", "items": {"type": "string"}}
    finding = {
        "type": "object", "additionalProperties": False, "required": list(FINDING_DELTA_KEYS),
        "properties": {
            "finding_id": {"type": ["string", "null"]}, "invariant": {"type": "string"},
            "blocking": {"type": "boolean"},
            "proposed_state": {"type": "string", "enum": [state.value for state in FindingState]},
            "evidence_refs": string_array, "successor_ref": {"type": ["string", "null"]},
            "new_material_evidence": {"type": "boolean"},
        },
    }
    escalation = {
        "type": "object", "additionalProperties": False, "required": list(ESCALATION_REQUIRED_KEYS),
        "properties": {"kind": {"type": "string", "enum": list(ESCALATION_KINDS)}, "question": {"type": "string"}},
    }
    commit_intent = {
        "type": "object", "additionalProperties": False,
        "required": list(COMMIT_INTENT_REQUIRED_KEYS),
        "properties": {"paths": string_array, "subject": {"type": "string"}},
    }
    return {
        "type": "object", "additionalProperties": False, "required": list(ROUTING_RESULT_REQUIRED_KEYS),
        "properties": {
            "schema_version": {"type": "integer", "enum": [1]},
            "outcome": {"type": "string", "enum": [outcome.value for outcome in Outcome]},
            "requested_route": {"type": "string"}, "scope_changed": {"type": "boolean"},
            "requires_human": {"type": "boolean"}, "escalation": {"anyOf": [{"type": "null"}, escalation]},
            "findings": {"type": "array", "items": finding}, "claimed_commits": string_array,
            "commit_intent": {"anyOf": [{"type": "null"}, commit_intent]},
            "evidence_refs": string_array, "summary": {"type": "string"},
        },
    }


def structured_result_envelope_openai_schema() -> dict[str, Any]:
    """Return the exact strict schema supplied by ``CodexCliAdapter``."""
    return {
        "type": "object", "additionalProperties": False,
        "required": list(STRUCTURED_RESULT_ENVELOPE_KEYS),
        "properties": {
            "schema_version": {"type": "integer", "enum": [1]}, "run_id": {"type": "string"},
            "hop_id": {"type": "string"}, "invocation_id": {"type": "string"},
            "result": routing_result_openai_schema(),
        },
    }


@dataclass(frozen=True)
class CodexCliAdapter:
    """Concrete single-provider implementation of the execution port."""

    result_directory: Path
    executable: str = "codex"
    model: str = "gpt-5.6-terra"
    reasoning_effort: str = "medium"

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
        if request.timeout_seconds <= 0:
            raise CodexCliError("timeout must be positive")
        try:
            schema = self.result_directory / "structured-result.schema.json"
            result = self.result_directory / "result-message.json"
            atomic_write_json(schema, structured_result_envelope_openai_schema())
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
