"""Canonical closed WFC structured-result envelope projection."""

from __future__ import annotations

import copy
from typing import Any

from .adapter import Invocation
from .model import (
    ConvergenceAssessmentResult, CoverageDisposition, DiagnosisClassification,
    FindingState, Outcome, ResultContractKind, StrategyAssessmentResult,
    StrategyRevisionKind, admits_result_contract_version,
)
from .serialization import (
    ESCALATION_KINDS, ESCALATION_REQUIRED_KEYS, COMMIT_INTENT_REQUIRED_KEYS,
    EXISTING_FINDING_DELTA_KEYS, FINDING_CITATION_KEYS, FINDING_TRANSITION_KEYS,
    NEW_FINDING_DELTA_KEYS, NEW_FINDING_DELTA_V2_KEYS, ROUTING_RESULT_REQUIRED_KEYS,
    ROUTING_RESULT_V2_REQUIRED_KEYS, STRUCTURED_RESULT_ENVELOPE_KEYS,
)


class StructuredResultSchemaError(ValueError):
    """The closed WFC projection cannot represent the selected contract."""


def routing_result_schema(
    contract_version: int = 1, *, permitted_routes: tuple[str, ...]
) -> dict[str, Any]:
    return _result_schema(contract_version, permit_v3=False, permitted_routes=permitted_routes)


def _strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object", "additionalProperties": False,
        "required": list(properties), "properties": properties,
    }


def _result_schema(
    contract_version: int, *, permit_v3: bool, permitted_routes: tuple[str, ...]
) -> dict[str, Any]:
    if contract_version not in ({1, 2, 3} if permit_v3 else {1, 2}):
        raise StructuredResultSchemaError("unsupported RoutingResult contract version")
    if not permitted_routes:
        raise StructuredResultSchemaError(
            "requested_route enum requires at least one current-phase permitted route"
        )
    string_array = {"type": "array", "items": {"type": "string"}}
    state = {"type": "string", "enum": [item.value for item in FindingState]}
    new_properties: dict[str, Any] = {
        "finding_id": {"type": "null"}, "invariant": {"type": "string"},
        "blocking": {"type": "boolean"}, "proposed_state": state,
        "evidence_refs": string_array, "successor_ref": {"type": ["string", "null"]},
        "new_material_evidence": {"type": "boolean"},
    }
    if contract_version in {2, 3}:
        new_properties["local_ref"] = {"type": ["string", "null"]}
    new_finding = _strict_object(new_properties)
    new_finding["required"] = list(
        NEW_FINDING_DELTA_KEYS if contract_version == 1 else NEW_FINDING_DELTA_V2_KEYS
    )
    existing_finding = _strict_object({
        "finding_id": {"type": "string"}, "proposed_state": state,
        "evidence_refs": string_array, "successor_ref": {"type": ["string", "null"]},
        "new_material_evidence": {"type": "boolean"},
    })
    existing_finding["required"] = list(EXISTING_FINDING_DELTA_KEYS)
    if contract_version == 3:
        citation = _strict_object({"finding_id": {"type": "string"}, "evidence_refs": string_array})
        citation["required"] = list(FINDING_CITATION_KEYS)
        transition = _strict_object({
            "finding_id": {"type": "string"}, "evidence_refs": string_array,
            "target": {"anyOf": [
                _strict_object({"state": {"type": "string", "enum": [FindingState.RESOLVED.value]}}),
                _strict_object({"state": {"type": "string", "enum": [FindingState.RESIDUAL.value]}}),
                _strict_object({"state": {"type": "string", "enum": [FindingState.SUPERSEDED.value]}, "successor_ref": {"type": "string"}}),
                _strict_object({"state": {"type": "string", "enum": [FindingState.OPEN.value]}, "new_material_evidence": {"type": "boolean"}}),
            ]},
        })
        transition["required"] = list(FINDING_TRANSITION_KEYS)
        finding = {"anyOf": [new_finding, citation, transition]}
    else:
        finding = {"anyOf": [new_finding, existing_finding]}
    escalation = _strict_object({
        "kind": {"type": "string", "enum": list(ESCALATION_KINDS)},
        "question": {"type": "string"},
    })
    escalation["required"] = list(ESCALATION_REQUIRED_KEYS)
    commit_intent = _strict_object({"paths": string_array, "subject": {"type": "string"}})
    commit_intent["required"] = list(COMMIT_INTENT_REQUIRED_KEYS)
    properties: dict[str, Any] = {
        "schema_version": {"type": "integer", "enum": [contract_version]},
        "outcome": {"type": "string", "enum": [item.value for item in Outcome]},
        "requested_route": {"type": "string", "enum": list(permitted_routes)},
        "scope_changed": {"type": "boolean"}, "requires_human": {"type": "boolean"},
        "escalation": {"anyOf": [{"type": "null"}, escalation]},
        "findings": {"type": "array", "items": finding}, "claimed_commits": string_array,
        "commit_intent": {"anyOf": [{"type": "null"}, commit_intent]},
        "evidence_refs": string_array, "summary": {"type": "string"},
    }
    if contract_version in {2, 3}:
        coverage = _strict_object({
            "finding_id": {"type": "string"},
            "disposition": {"type": "string", "enum": [item.value for item in CoverageDisposition]},
            "evidence_refs": string_array,
        })
        assessment = _strict_object({
            "finding_id": {"type": "string"}, "correction_hop_id": {"type": "string"},
            "result": {"type": "string", "enum": [item.value for item in ConvergenceAssessmentResult]},
            "evidence_refs": string_array,
        })
        diagnosis = _strict_object({
            "escape_hop_id": {"type": "string"}, "finding_ids": string_array,
            "classification": {"type": "string", "enum": [item.value for item in DiagnosisClassification]},
            "analysis": {"type": "string"}, "evidence_refs": string_array,
        })
        revision = _strict_object({
            "escape_hop_id": {"type": "string"}, "diagnosis_hop_id": {"type": "string"},
            "finding_ids": string_array,
            "kind": {"type": "string", "enum": [item.value for item in StrategyRevisionKind]},
            "plan": {"type": "string"}, "evidence_refs": string_array,
        })
        strategy = _strict_object({
            "escape_hop_id": {"type": "string"}, "diagnosis_hop_id": {"type": "string"},
            "revision_hop_id": {"type": "string"}, "finding_ids": string_array,
            "result": {"type": "string", "enum": [item.value for item in StrategyAssessmentResult]},
            "evidence_refs": string_array,
        })
        properties.update({
            "coverage_claims": {"type": "array", "items": coverage},
            "convergence_assessments": {"type": "array", "items": assessment},
            "diagnosis": {"anyOf": [{"type": "null"}, diagnosis]},
            "strategy_revision": {"anyOf": [{"type": "null"}, revision]},
            "strategy_assessment": {"anyOf": [{"type": "null"}, strategy]},
        })
    return {
        "type": "object", "additionalProperties": False,
        "required": list(ROUTING_RESULT_REQUIRED_KEYS if contract_version == 1 else ROUTING_RESULT_V2_REQUIRED_KEYS),
        "properties": properties,
    }


def projected_result_schema(
    kind: ResultContractKind, contract_version: int, *, permitted_routes: tuple[str, ...]
) -> dict[str, Any]:
    if kind is ResultContractKind.GENERIC_ROUTING_RESULT:
        return routing_result_schema(contract_version, permitted_routes=permitted_routes)
    if kind not in {ResultContractKind.WORK_RESULT, ResultContractKind.REVIEW_VERDICT}:
        raise StructuredResultSchemaError(f"unsupported result contract kind: {kind}")
    if not admits_result_contract_version(kind, contract_version):
        raise StructuredResultSchemaError("unsupported result contract version")
    schema = copy.deepcopy(_result_schema(contract_version, permit_v3=True, permitted_routes=permitted_routes))
    result, required = schema["properties"], schema["required"]
    result.pop("outcome")
    required.remove("outcome")
    for name in ("coverage_claims", "convergence_assessments", "diagnosis", "strategy_revision", "strategy_assessment"):
        result.pop(name, None)
        if name in required:
            required.remove(name)
    field, values = (("status", [Outcome.COMPLETED.value, Outcome.BLOCKED.value])
                     if kind is ResultContractKind.WORK_RESULT
                     else ("verdict", ["accept", Outcome.CHANGES_REQUIRED.value]))
    result[field] = {"type": "string", "enum": values}
    required.append(field)
    return schema


def structured_result_envelope_schema(
    invocation: Invocation, contract_version: int = 1,
    result_contract_kind: ResultContractKind = ResultContractKind.GENERIC_ROUTING_RESULT,
    *, permitted_routes: tuple[str, ...],
) -> dict[str, Any]:
    """Build a closed envelope; semantic and correlation acceptance stays downstream."""
    def dispatched(value: str) -> dict[str, Any]:
        return {"type": "string", "enum": [value]}
    return {
        "type": "object", "additionalProperties": False,
        "required": list(STRUCTURED_RESULT_ENVELOPE_KEYS),
        "properties": {
            "schema_version": {"type": "integer", "enum": [contract_version]},
            "run_id": dispatched(invocation.run_id), "hop_id": dispatched(invocation.hop_id),
            "invocation_id": dispatched(invocation.invocation_id),
            "result": projected_result_schema(result_contract_kind, contract_version, permitted_routes=permitted_routes),
        },
    }
