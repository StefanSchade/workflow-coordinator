"""Fail-closed YAML input and JSON Run-state serialization."""

from __future__ import annotations

import json
from dataclasses import fields
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, TypeVar

import yaml
from yaml.constructor import ConstructorError

from .model import (
    DispatchMarker,
    DurableArtifact,
    Escalation,
    Finding,
    FindingDelta,
    FindingState,
    HopSummary,
    Outcome,
    Phase,
    RepositoryPolicy,
    ReviewAuthority,
    ReviewDescriptor,
    Route,
    RouteEffect,
    RoutingResult,
    Run,
    RunStatus,
    WorkItem,
    WorkflowProfile,
)


class ValidationError(ValueError):
    """A deliberate fail-closed structured-data diagnostic."""


EnumType = TypeVar("EnumType", bound=Enum)


class _StrictLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key: {key}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValidationError(f"{context} must be a mapping with string keys")
    return value


def _fields(
    value: Any,
    *,
    context: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> Mapping[str, Any]:
    data = _mapping(value, context=context)
    unknown = sorted(set(data) - required - optional)
    missing = sorted(required - set(data))
    if unknown:
        raise ValidationError(f"{context} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ValidationError(f"{context} is missing fields: {', '.join(missing)}")
    return data


def _string(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{context} must be a non-empty string")
    return value


def _optional_string(value: Any, *, context: str) -> str | None:
    if value is None:
        return None
    return _string(value, context=context)


def _integer(value: Any, *, context: str, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{context} must be an integer")
    if positive and value <= 0:
        raise ValidationError(f"{context} must be a positive integer")
    return value


def _version(value: Any, *, context: str) -> int:
    version = _integer(value, context=context)
    if version != 1:
        raise ValidationError(f"{context} has unsupported version: {version}")
    return version


def _boolean(value: Any, *, context: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{context} must be a boolean")
    return value


def _strings(value: Any, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValidationError(f"{context} must be a list")
    return tuple(_string(item, context=f"{context} item") for item in value)


def _enum(enum_type: type[EnumType], value: Any, *, context: str) -> EnumType:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValidationError(f"{context} must be one of: {allowed}") from error


def _yaml_mapping(text: str, *, context: str) -> Mapping[str, Any]:
    try:
        value = yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as error:
        raise ValidationError(f"invalid {context} YAML: {error}") from error
    return _mapping(value, context=context)


def work_item_from_yaml(text: str) -> WorkItem:
    data = _fields(
        _yaml_mapping(text, context="WorkItem"),
        context="WorkItem",
        required={
            "schema_version",
            "work_item_id",
            "authority_source",
            "profile_ref",
            "repository",
            "scope",
            "initial_phase",
            "max_autonomous_hops",
        },
        optional={"profile_labels"},
    )
    return WorkItem(
        schema_version=_version(data["schema_version"], context="WorkItem.schema_version"),
        work_item_id=_string(data["work_item_id"], context="WorkItem.work_item_id"),
        authority_source=_string(data["authority_source"], context="WorkItem.authority_source"),
        profile_ref=_string(data["profile_ref"], context="WorkItem.profile_ref"),
        repository=_string(data["repository"], context="WorkItem.repository"),
        scope=_strings(data["scope"], context="WorkItem.scope"),
        initial_phase=_string(data["initial_phase"], context="WorkItem.initial_phase"),
        max_autonomous_hops=_integer(
            data["max_autonomous_hops"],
            context="WorkItem.max_autonomous_hops",
            positive=True,
        ),
        profile_labels=_strings(data.get("profile_labels", []), context="WorkItem.profile_labels"),
    )


def repository_policy_from_yaml(text: str) -> RepositoryPolicy:
    data = _fields(
        _yaml_mapping(text, context="repository policy"),
        context="repository policy",
        required={"schema_version", "policy_id", "revision", "scope", "actions", "evidence_states"},
    )
    return RepositoryPolicy(
        schema_version=_version(data["schema_version"], context="policy.schema_version"),
        policy_id=_string(data["policy_id"], context="policy.policy_id"),
        revision=_string(data["revision"], context="policy.revision"),
        scope=_strings(data["scope"], context="policy.scope"),
        actions=_strings(data["actions"], context="policy.actions"),
        evidence_states=_strings(data["evidence_states"], context="policy.evidence_states"),
    )


def _review(value: Any, *, context: str) -> ReviewDescriptor | None:
    if value is None:
        return None
    data = _fields(
        value,
        context=context,
        required={
            "purpose",
            "breadth",
            "authority",
            "context",
            "blocking_policy",
            "trigger",
            "exit",
        },
        optional={"finding_authority"},
    )
    context_policy = _string(data["context"], context=f"{context}.context")
    if context_policy != "fresh":
        raise ValidationError(f"{context}.context must be fresh in Slice 1")
    permitted = _strings(data.get("finding_authority", []), context=f"{context}.finding_authority")
    allowed = {"resolve", "residual", "supersede", "reopen"}
    unknown = sorted(set(permitted) - allowed)
    if unknown:
        raise ValidationError(
            f"{context}.finding_authority has unknown operations: {', '.join(unknown)}"
        )
    authority = _enum(ReviewAuthority, data["authority"], context=f"{context}.authority")
    if authority is ReviewAuthority.ADVISORY and permitted:
        raise ValidationError(
            f"{context} advisory review cannot carry Finding disposition authority"
        )
    return ReviewDescriptor(
        purpose=_string(data["purpose"], context=f"{context}.purpose"),
        breadth=_string(data["breadth"], context=f"{context}.breadth"),
        authority=authority,
        context=context_policy,
        blocking_policy=_string(data["blocking_policy"], context=f"{context}.blocking_policy"),
        trigger=_string(data["trigger"], context=f"{context}.trigger"),
        exit=_string(data["exit"], context=f"{context}.exit"),
        finding_authority=permitted,
    )


def _route(value: Any, *, context: str) -> Route:
    data = _fields(
        value,
        context=context,
        required={"effect"},
        optional={"target", "question", "supervisor"},
    )
    effect = _enum(RouteEffect, data["effect"], context=f"{context}.effect")
    target = _optional_string(data.get("target"), context=f"{context}.target")
    question = _optional_string(data.get("question"), context=f"{context}.question")
    supervisor = _boolean(data.get("supervisor", False), context=f"{context}.supervisor")
    if (effect is RouteEffect.GOTO) != (target is not None):
        raise ValidationError(f"{context} target is required only for goto")
    if effect is RouteEffect.AWAIT_HUMAN and question is None:
        raise ValidationError(f"{context} await_human requires a question")
    if effect is not RouteEffect.AWAIT_HUMAN and question is not None:
        raise ValidationError(f"{context} question is valid only for await_human")
    if supervisor and effect in {RouteEffect.AWAIT_HUMAN, RouteEffect.SUSPEND_FOR_PREREQUISITE}:
        raise ValidationError(
            f"{context} supervisor cannot authorize human or prerequisite effects"
        )
    return Route(effect=effect, target=target, question=question, supervisor=supervisor)


def workflow_profile_from_yaml(text: str) -> WorkflowProfile:
    data = _fields(
        _yaml_mapping(text, context="workflow profile"),
        context="workflow profile",
        required={"schema_version", "profile_id", "revision", "phases"},
    )
    raw_phases = _mapping(data["phases"], context="profile.phases")
    if not raw_phases:
        raise ValidationError("profile.phases must not be empty")
    phases: dict[str, Phase] = {}
    for phase_key, value in raw_phases.items():
        phase_context = f"profile.phases.{phase_key}"
        phase_data = _fields(
            value,
            context=phase_context,
            required={
                "role",
                "components",
                "durable_artifact",
                "authorized_scope",
                "authorized_actions",
                "human_gates",
                "evidence",
                "review",
                "routes",
            },
        )
        components = _fields(
            phase_data["components"],
            context=f"{phase_context}.components",
            required={"role", "modifiers", "repository_policy"},
        )
        raw_routes = _mapping(phase_data["routes"], context=f"{phase_context}.routes")
        if not raw_routes:
            raise ValidationError(f"{phase_context}.routes must not be empty")
        routes = {
            key: _route(route, context=f"{phase_context}.routes.{key}")
            for key, route in raw_routes.items()
        }
        phases[phase_key] = Phase(
            role=_string(phase_data["role"], context=f"{phase_context}.role"),
            role_components=_strings(
                components["role"], context=f"{phase_context}.components.role"
            ),
            modifier_components=_strings(
                components["modifiers"], context=f"{phase_context}.components.modifiers"
            ),
            repository_policy_components=_strings(
                components["repository_policy"],
                context=f"{phase_context}.components.repository_policy",
            ),
            durable_artifact=_enum(
                DurableArtifact,
                phase_data["durable_artifact"],
                context=f"{phase_context}.durable_artifact",
            ),
            authorized_scope=_strings(
                phase_data["authorized_scope"], context=f"{phase_context}.authorized_scope"
            ),
            authorized_actions=_strings(
                phase_data["authorized_actions"], context=f"{phase_context}.authorized_actions"
            ),
            human_gates=_strings(phase_data["human_gates"], context=f"{phase_context}.human_gates"),
            evidence=_strings(phase_data["evidence"], context=f"{phase_context}.evidence"),
            review=_review(phase_data["review"], context=f"{phase_context}.review"),
            routes=routes,
        )
    for phase_key, phase in phases.items():
        for route_key, route in phase.routes.items():
            if route.target is not None and route.target not in phases:
                raise ValidationError(
                    f"profile.phases.{phase_key}.routes.{route_key} targets unknown phase {route.target}"
                )
    return WorkflowProfile(
        schema_version=_version(data["schema_version"], context="profile.schema_version"),
        profile_id=_string(data["profile_id"], context="profile.profile_id"),
        revision=_string(data["revision"], context="profile.revision"),
        phases=phases,
    )


def _finding_delta(value: Any, *, context: str) -> FindingDelta:
    data = _fields(
        value,
        context=context,
        required={"finding_id", "invariant", "blocking", "proposed_state", "evidence_refs"},
        optional={"successor_ref", "new_material_evidence"},
    )
    return FindingDelta(
        finding_id=_optional_string(data["finding_id"], context=f"{context}.finding_id"),
        invariant=_string(data["invariant"], context=f"{context}.invariant"),
        blocking=_boolean(data["blocking"], context=f"{context}.blocking"),
        proposed_state=_enum(
            FindingState, data["proposed_state"], context=f"{context}.proposed_state"
        ),
        evidence_refs=_strings(data["evidence_refs"], context=f"{context}.evidence_refs"),
        successor_ref=_optional_string(
            data.get("successor_ref"), context=f"{context}.successor_ref"
        ),
        new_material_evidence=_boolean(
            data.get("new_material_evidence", False),
            context=f"{context}.new_material_evidence",
        ),
    )


def routing_result_from_data(value: Any) -> RoutingResult:
    data = _fields(
        value,
        context="routing result",
        required={
            "schema_version",
            "outcome",
            "requested_route",
            "scope_changed",
            "requires_human",
            "escalation",
            "findings",
            "claimed_commits",
            "evidence_refs",
            "summary",
        },
    )
    escalation = None
    if data["escalation"] is not None:
        raw_escalation = _fields(
            data["escalation"],
            context="routing result.escalation",
            required={"kind", "question"},
        )
        kind = _string(raw_escalation["kind"], context="routing result.escalation.kind")
        allowed_kinds = {
            "scope",
            "semantic_authority",
            "public_surface",
            "trust_boundary",
            "prerequisite",
            "operational",
        }
        if kind not in allowed_kinds:
            raise ValidationError(f"routing result.escalation.kind is unknown: {kind}")
        escalation = Escalation(
            kind=kind,
            question=_string(
                raw_escalation["question"], context="routing result.escalation.question"
            ),
        )
    raw_findings = data["findings"]
    if not isinstance(raw_findings, list):
        raise ValidationError("routing result.findings must be a list")
    return RoutingResult(
        schema_version=_version(data["schema_version"], context="routing result.schema_version"),
        outcome=_enum(Outcome, data["outcome"], context="routing result.outcome"),
        requested_route=_string(data["requested_route"], context="routing result.requested_route"),
        scope_changed=_boolean(data["scope_changed"], context="routing result.scope_changed"),
        requires_human=_boolean(data["requires_human"], context="routing result.requires_human"),
        escalation=escalation,
        findings=tuple(
            _finding_delta(item, context=f"routing result.findings[{index}]")
            for index, item in enumerate(raw_findings)
        ),
        claimed_commits=_strings(data["claimed_commits"], context="routing result.claimed_commits"),
        evidence_refs=_strings(data["evidence_refs"], context="routing result.evidence_refs"),
        summary=_string(data["summary"], context="routing result.summary"),
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def run_to_json(run: Run) -> str:
    return json.dumps(_json_value(run), sort_keys=True, separators=(",", ":")) + "\n"


def _hop_summary(value: Any, *, context: str) -> HopSummary:
    data = _fields(value, context=context, required={field.name for field in fields(HopSummary)})
    return HopSummary(
        hop_id=_string(data["hop_id"], context=f"{context}.hop_id"),
        sequence=_integer(data["sequence"], context=f"{context}.sequence", positive=True),
        role=_string(data["role"], context=f"{context}.role"),
        outcome=_string(data["outcome"], context=f"{context}.outcome"),
        base_head=_string(data["base_head"], context=f"{context}.base_head"),
        end_head=_string(data["end_head"], context=f"{context}.end_head"),
        actual_commits=_strings(data["actual_commits"], context=f"{context}.actual_commits"),
        applied_route=_optional_string(data["applied_route"], context=f"{context}.applied_route"),
        stop_reason=_optional_string(data["stop_reason"], context=f"{context}.stop_reason"),
        summary=_string(data["summary"], context=f"{context}.summary"),
        attribution=_string(data["attribution"], context=f"{context}.attribution"),
    )


def _finding(value: Any, *, context: str) -> Finding:
    data = _fields(value, context=context, required={field.name for field in fields(Finding)})
    return Finding(
        finding_id=_string(data["finding_id"], context=f"{context}.finding_id"),
        invariant=_string(data["invariant"], context=f"{context}.invariant"),
        blocking=_boolean(data["blocking"], context=f"{context}.blocking"),
        state=_enum(FindingState, data["state"], context=f"{context}.state"),
        introduced_by_hop=_string(
            data["introduced_by_hop"], context=f"{context}.introduced_by_hop"
        ),
        changed_by_hop=_string(data["changed_by_hop"], context=f"{context}.changed_by_hop"),
        evidence_refs=_strings(data["evidence_refs"], context=f"{context}.evidence_refs"),
        disposition=_string(data["disposition"], context=f"{context}.disposition"),
        successor_ref=_optional_string(data["successor_ref"], context=f"{context}.successor_ref"),
        external_work_item_ref=_optional_string(
            data["external_work_item_ref"], context=f"{context}.external_work_item_ref"
        ),
    )


def run_from_json(text: str) -> Run:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValidationError(f"invalid Run JSON: {error}") from error
    data = _fields(value, context="Run", required={field.name for field in fields(Run)})
    raw_hops = data["hops"]
    raw_findings = data["findings"]
    if not isinstance(raw_hops, list) or not isinstance(raw_findings, list):
        raise ValidationError("Run.hops and Run.findings must be lists")
    run = Run(
        schema_version=_version(data["schema_version"], context="Run.schema_version"),
        run_id=_string(data["run_id"], context="Run.run_id"),
        work_item_id=_string(data["work_item_id"], context="Run.work_item_id"),
        profile_id=_string(data["profile_id"], context="Run.profile_id"),
        profile_revision=_string(data["profile_revision"], context="Run.profile_revision"),
        policy_id=_string(data["policy_id"], context="Run.policy_id"),
        policy_revision=_string(data["policy_revision"], context="Run.policy_revision"),
        repository=_string(data["repository"], context="Run.repository"),
        baseline_head=_string(data["baseline_head"], context="Run.baseline_head"),
        current_head=_string(data["current_head"], context="Run.current_head"),
        phase=_string(data["phase"], context="Run.phase"),
        status=_enum(RunStatus, data["status"], context="Run.status"),
        hop_limit=_integer(data["hop_limit"], context="Run.hop_limit", positive=True),
        hop_used=_integer(data["hop_used"], context="Run.hop_used"),
        hops=tuple(
            _hop_summary(item, context=f"Run.hops[{index}]") for index, item in enumerate(raw_hops)
        ),
        findings=tuple(
            _finding(item, context=f"Run.findings[{index}]")
            for index, item in enumerate(raw_findings)
        ),
        child_run_id=_optional_string(data["child_run_id"], context="Run.child_run_id"),
        suspension_head=_optional_string(data["suspension_head"], context="Run.suspension_head"),
        reconcile_reason=_optional_string(data["reconcile_reason"], context="Run.reconcile_reason"),
        reconcile_anchor=_optional_string(data["reconcile_anchor"], context="Run.reconcile_anchor"),
        stop_reason=_optional_string(data["stop_reason"], context="Run.stop_reason"),
        human_question=_optional_string(data["human_question"], context="Run.human_question"),
        anomalies=_strings(data["anomalies"], context="Run.anomalies"),
    )
    if run.hop_used != len(run.hops):
        raise ValidationError("Run.hop_used must equal the number of Hop summaries")
    if run.hop_used > run.hop_limit:
        raise ValidationError("Run.hop_used exceeds Run.hop_limit")
    if run.hop_used < 0:
        raise ValidationError("Run.hop_used must not be negative")
    if len({hop.hop_id for hop in run.hops}) != len(run.hops):
        raise ValidationError("Run contains duplicate Hop IDs")
    if len({finding.finding_id for finding in run.findings}) != len(run.findings):
        raise ValidationError("Run contains duplicate Finding IDs")
    return run


def dispatch_marker_to_data(marker: DispatchMarker) -> dict[str, Any]:
    return _json_value(marker)


def dispatch_marker_from_json(text: str) -> DispatchMarker:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValidationError(f"invalid dispatch marker JSON: {error}") from error
    data = _fields(
        value, context="dispatch marker", required={field.name for field in fields(DispatchMarker)}
    )
    return DispatchMarker(
        schema_version=_version(data["schema_version"], context="dispatch marker.schema_version"),
        run_id=_string(data["run_id"], context="dispatch marker.run_id"),
        hop_id=_string(data["hop_id"], context="dispatch marker.hop_id"),
        sequence=_integer(data["sequence"], context="dispatch marker.sequence", positive=True),
        invocation_id=_string(data["invocation_id"], context="dispatch marker.invocation_id"),
        base_head=_string(data["base_head"], context="dispatch marker.base_head"),
        budget_reserved=_integer(
            data["budget_reserved"], context="dispatch marker.budget_reserved", positive=True
        ),
    )


def load_run(path: Path) -> Run:
    return run_from_json(path.read_text(encoding="utf-8"))
