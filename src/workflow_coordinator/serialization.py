"""Fail-closed YAML input and JSON Run-state serialization."""

from __future__ import annotations

import base64
import binascii
import json
import hashlib
import re
from dataclasses import dataclass, fields
from enum import Enum
from pathlib import Path
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping, TypeVar

import yaml
from yaml.constructor import ConstructorError

from .model import (
    COMMIT_ACTION,
    COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS,
    COORDINATOR_HARD_MAX_EVIDENCE_TIMEOUT_SECONDS,
    CommitIntent,
    ConvergenceConfiguration,
    ConvergenceAssessment,
    ConvergenceAssessmentResult,
    ConvergenceProtocol,
    ConvergenceRole,
    DiagnosisClassification,
    DiagnosisReport,
    CorrectionObligation,
    CorrectionObligationOwner,
    CoverageClaim,
    CoverageDisposition,
    DispatchMarker,
    DurableArtifact,
    EDIT_ACTION,
    EvidenceCommand,
    ExecutionEvidenceDocument,
    ExecutionEvidenceRunIdentity,
    ExecutionBinding,
    Escalation,
    FindingCitation,
    FindingTransition,
    FindingTransitionTarget,
    ExistingFindingDelta,
    Finding,
    FindingDeltaValue,
    FindingState,
    NewFindingDelta,
    ReopenFinding,
    ResidualFinding,
    ResolveFinding,
    SupersedeFinding,
    HopSummary,
    LegacyExecutionEvidenceDocument,
    Observation,
    Outcome,
    Phase,
    ResultContractKind,
    RepositoryPolicy,
    RequiredEvidenceIdentity,
    ReviewAuthority,
    ReviewDescriptor,
    Route,
    RouteEffect,
    RoutingResult,
    ReviewDisposition,
    ReviewVerdict,
    StrategyAssessment,
    StrategyAssessmentResult,
    StrategyRevision,
    StrategyRevisionKind,
    Run,
    RunExecutionConfiguration,
    RunStatus,
    SemanticStop,
    SuccessorProvenance,
    StagedQualificationAttempt,
    StatFingerprint,
    UntrustedDiagnosticCapture,
    WorkItem,
    WorkflowProfile,
    WorkflowRole,
    WorkResult,
    WorkStatus,
)


class ValidationError(ValueError):
    """A deliberate fail-closed structured-data diagnostic."""


@dataclass(frozen=True)
class PromptComponentReference:
    """One declarative repository-relative source for a prompt component."""

    path: str


EnumType = TypeVar("EnumType", bound=Enum)


ROUTING_RESULT_REQUIRED_KEYS = (
    "schema_version",
    "outcome",
    "requested_route",
    "scope_changed",
    "requires_human",
    "escalation",
    "findings",
    "claimed_commits",
    "commit_intent",
    "evidence_refs",
    "summary",
)
ROUTING_RESULT_V2_ADDITIONAL_KEYS = (
    "coverage_claims",
    "convergence_assessments",
    "diagnosis",
    "strategy_revision",
    "strategy_assessment",
)
ROUTING_RESULT_V2_REQUIRED_KEYS = (
    *ROUTING_RESULT_REQUIRED_KEYS,
    *ROUTING_RESULT_V2_ADDITIONAL_KEYS,
)
NEW_FINDING_DELTA_REQUIRED_KEYS = (
    "finding_id",
    "invariant",
    "blocking",
    "proposed_state",
    "evidence_refs",
)
NEW_FINDING_DELTA_OPTIONAL_KEYS = ("successor_ref", "new_material_evidence")
NEW_FINDING_DELTA_KEYS = (*NEW_FINDING_DELTA_REQUIRED_KEYS, *NEW_FINDING_DELTA_OPTIONAL_KEYS)
NEW_FINDING_DELTA_V2_KEYS = (*NEW_FINDING_DELTA_KEYS, "local_ref")
EXISTING_FINDING_DELTA_REQUIRED_KEYS = (
    "finding_id",
    "proposed_state",
    "evidence_refs",
)
EXISTING_FINDING_DELTA_OPTIONAL_KEYS = ("successor_ref", "new_material_evidence")
EXISTING_FINDING_DELTA_KEYS = (
    *EXISTING_FINDING_DELTA_REQUIRED_KEYS,
    *EXISTING_FINDING_DELTA_OPTIONAL_KEYS,
)
FINDING_CITATION_KEYS = ("finding_id", "evidence_refs")
FINDING_TRANSITION_KEYS = ("finding_id", "target", "evidence_refs")
RESOLVE_TARGET_KEYS = ("state",)
RESIDUAL_TARGET_KEYS = ("state",)
SUPERSEDE_TARGET_KEYS = ("state", "successor_ref")
REOPEN_TARGET_KEYS = ("state", "new_material_evidence")
# Compatibility name for consumers describing the new-Finding branch.
FINDING_DELTA_KEYS = NEW_FINDING_DELTA_KEYS
COMMIT_INTENT_REQUIRED_KEYS = ("paths", "subject")
ESCALATION_REQUIRED_KEYS = ("kind", "question")
ESCALATION_KINDS = (
    "scope",
    "semantic_authority",
    "public_surface",
    "trust_boundary",
    "prerequisite",
    "operational",
)
STRUCTURED_RESULT_ENVELOPE_KEYS = (
    "schema_version",
    "run_id",
    "hop_id",
    "invocation_id",
    "result",
)


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


def _number(value: Any, *, context: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{context} must be a number")
    if positive and value <= 0:
        raise ValidationError(f"{context} must be positive")
    return float(value)


def _version(value: Any, *, context: str) -> int:
    version = _integer(value, context=context)
    if version != 1:
        raise ValidationError(f"{context} has unsupported version: {version}")
    return version


def _migrated_version(value: Any, *, context: str, expected: int) -> int:
    version = _integer(value, context=context)
    if version < expected:
        raise ValidationError(
            f"{context} {version} is unsupported; migration to schema_version {expected} is required"
        )
    if version != expected:
        raise ValidationError(f"{context} has unsupported version: {version}")
    return version


def _policy_version(value: Any) -> int:
    version = _integer(value, context="policy.schema_version")
    if version in {1, 2}:
        raise ValidationError(
            f"repository policy schema_version {version} is unsupported; "
            "migration to schema_version 3 is required"
        )
    if version != 3:
        raise ValidationError(f"policy.schema_version has unsupported version: {version}")
    return version


def _boolean(value: Any, *, context: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{context} must be a boolean")
    return value


def _strings(value: Any, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValidationError(f"{context} must be a list")
    return tuple(_string(item, context=f"{context} item") for item in value)


def _evidence_commands(value: Any) -> dict[str, EvidenceCommand]:
    raw = _mapping(value, context="policy.evidence_commands")
    commands: dict[str, EvidenceCommand] = {}
    for name, raw_command in raw.items():
        if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) is None:
            raise ValidationError(
                "policy.evidence_commands command name must match "
                "[a-z][a-z0-9_-]{0,63}: " + repr(name)
            )
        context = f"policy.evidence_commands.{name}"
        command = _fields(
            raw_command,
            context=context,
            required={"argv", "timeout_seconds"},
        )
        raw_argv = command["argv"]
        if not isinstance(raw_argv, list) or not raw_argv:
            raise ValidationError(f"{context}.argv must be a non-empty list")
        argv: list[str] = []
        for index, item in enumerate(raw_argv):
            if not isinstance(item, str):
                raise ValidationError(f"{context}.argv[{index}] must be a string")
            if "\0" in item:
                raise ValidationError(f"{context}.argv[{index}] must not contain NUL")
            if index == 0 and not item:
                raise ValidationError(f"{context}.argv[0] must be non-empty")
            argv.append(item)
        timeout = _integer(command["timeout_seconds"], context=f"{context}.timeout_seconds")
        if not 1 <= timeout <= COORDINATOR_HARD_MAX_EVIDENCE_TIMEOUT_SECONDS:
            raise ValidationError(
                f"{context}.timeout_seconds must be in "
                f"1..{COORDINATOR_HARD_MAX_EVIDENCE_TIMEOUT_SECONDS}"
            )
        commands[name] = EvidenceCommand(tuple(argv), timeout)
    return commands


def _repository_path(value: Any, *, context: str) -> str:
    path = _string(value, context=context)
    candidate = PurePosixPath(path)
    if (
        "\\" in path
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or candidate.as_posix() != path
    ):
        raise ValidationError(f"{context} must be a normalized repository-relative path")
    return path


def a1_path_shape(value: object) -> bool:
    """Return whether *value* is one canonical non-root A1 repository path."""

    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if "\\" in value or any(character in value for character in "*?[]"):
        return False
    candidate = PurePosixPath(value)
    return (
        value != "."
        and not candidate.is_absolute()
        and all(part not in {"", ".", ".."} for part in candidate.parts)
        and candidate.as_posix() == value
        and "//" not in value
        and not value.endswith("/")
    )


def _policy_paths(value: Any, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValidationError(f"{context} must be a non-empty list")
    paths = tuple(value)
    malformed = sorted(repr(path) for path in paths if not a1_path_shape(path))
    if malformed:
        raise ValidationError(
            f"{context} must contain normalized repository-relative A1 paths: "
            + ", ".join(malformed)
        )
    if len(set(paths)) != len(paths):
        raise ValidationError(f"{context} paths must be unique")
    overlaps = sorted(
        {
            tuple(sorted((left, right)))
            for index, left in enumerate(paths)
            for right in paths[index + 1 :]
            if left.startswith(f"{right}/") or right.startswith(f"{left}/")
        }
    )
    if overlaps:
        rendered = ", ".join(f"{left} <-> {right}" for left, right in overlaps)
        raise ValidationError(f"{context} paths must not overlap: {rendered}")
    return paths


def _durable_artifacts(value: Any, *, context: str) -> dict[str, tuple[str, ...]]:
    raw = _mapping(value, context=context)
    result: dict[str, tuple[str, ...]] = {}
    for phase_key, raw_paths in raw.items():
        phase = _string(phase_key, context=f"{context} phase key")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise ValidationError(f"{context}.{phase} must be a non-empty list")
        paths = tuple(
            _repository_path(path, context=f"{context}.{phase} item")
            for path in raw_paths
        )
        if len(set(paths)) != len(paths):
            raise ValidationError(f"{context}.{phase} paths must be unique")
        result[phase] = paths
    return result


def _persona(value: Any, *, context: str) -> str:
    persona = _string(value, context=context)
    if persona.splitlines() != [persona]:
        raise ValidationError(f"{context} must be a single-line string")
    if len(persona.encode("utf-8")) > 512:
        raise ValidationError(f"{context} must not exceed 512 UTF-8 bytes")
    return persona


def _execution_value(value: Any, *, context: str) -> str:
    selected = _string(value, context=context)
    if selected.splitlines() != [selected] or "\0" in selected:
        raise ValidationError(f"{context} must be a single-line value")
    return selected


def _personas(value: Any) -> Mapping[WorkflowRole, str]:
    raw = _mapping(value, context="WorkItem.personas")
    if not raw:
        raise ValidationError("WorkItem.personas must not be empty")
    personas: dict[WorkflowRole, str] = {}
    for raw_role, raw_persona in raw.items():
        role = _enum(WorkflowRole, raw_role, context="WorkItem.personas role")
        personas[role] = _persona(
            raw_persona, context=f"WorkItem.personas.{role.value}"
        )
    return MappingProxyType(personas)


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


def component_manifest_from_yaml(text: str) -> dict[str, PromptComponentReference]:
    """Load the deliberately small, non-executable prompt-component manifest."""

    data = _fields(
        _yaml_mapping(text, context="component manifest"),
        context="component manifest",
        required={"schema_version", "components"},
    )
    _version(data["schema_version"], context="component manifest.schema_version")
    raw_components = _mapping(data["components"], context="component manifest.components")
    components: dict[str, PromptComponentReference] = {}
    for component_id, value in raw_components.items():
        identifier = _string(component_id, context="component manifest component ID")
        entry = _fields(value, context=f"component manifest.components.{identifier}", required={"path"})
        path = _string(entry["path"], context=f"component manifest.components.{identifier}.path")
        candidate = PurePosixPath(path)
        if "\\" in path or candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            raise ValidationError(f"component manifest.components.{identifier}.path must be repository-relative")
        if candidate.as_posix() != path:
            raise ValidationError(f"component manifest.components.{identifier}.path is ambiguous after normalization")
        components[identifier] = PromptComponentReference(path=path)
    return components


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
            "personas",
        },
        optional={"profile_labels", "durable_artifacts"},
    )
    return WorkItem(
        schema_version=_migrated_version(
            data["schema_version"], context="WorkItem.schema_version", expected=2
        ),
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
        personas=_personas(data["personas"]),
        profile_labels=_strings(data.get("profile_labels", []), context="WorkItem.profile_labels"),
        durable_artifacts=(
            _durable_artifacts(data["durable_artifacts"], context="WorkItem.durable_artifacts")
            if "durable_artifacts" in data
            else None
        ),
    )


def repository_policy_from_yaml(text: str) -> RepositoryPolicy:
    raw = _yaml_mapping(text, context="repository policy")
    policy_version = _policy_version(raw["schema_version"]) if "schema_version" in raw else None
    data = _fields(
        raw,
        context="repository policy",
        required={
            "schema_version", "policy_id", "revision", "scope", "governance_paths",
            "work_item_scope_ceiling", "max_autonomous_hops", "actions", "evidence_commands",
        },
    )
    max_autonomous_hops = _integer(
        data["max_autonomous_hops"], context="policy.max_autonomous_hops", positive=True,
    )
    if max_autonomous_hops > COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS:
        raise ValidationError(
            "policy.max_autonomous_hops exceeds Coordinator hard maximum: "
            f"{COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS}"
        )
    return RepositoryPolicy(
        schema_version=(policy_version if policy_version is not None else _policy_version(data["schema_version"])),
        policy_id=_string(data["policy_id"], context="policy.policy_id"),
        revision=_string(data["revision"], context="policy.revision"),
        scope=_strings(data["scope"], context="policy.scope"),
        governance_paths=_policy_paths(data["governance_paths"], context="policy.governance_paths"),
        work_item_scope_ceiling=_policy_paths(
            data["work_item_scope_ceiling"], context="policy.work_item_scope_ceiling",
        ),
        max_autonomous_hops=max_autonomous_hops,
        actions=_strings(data["actions"], context="policy.actions"),
        evidence_commands=_evidence_commands(data["evidence_commands"]),
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
        optional={"finding_authority", "failed_evidence_route"},
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
    blocking_policy = _string(data["blocking_policy"], context=f"{context}.blocking_policy")
    if blocking_policy != "blocking_only":
        raise ValidationError(f"{context}.blocking_policy must be blocking_only")
    failed_evidence_route = _optional_string(
        data.get("failed_evidence_route"), context=f"{context}.failed_evidence_route"
    )
    if failed_evidence_route is not None and authority is not ReviewAuthority.GATE:
        raise ValidationError(f"{context}.failed_evidence_route is valid only for a gate review")
    return ReviewDescriptor(
        purpose=_string(data["purpose"], context=f"{context}.purpose"),
        breadth=_string(data["breadth"], context=f"{context}.breadth"),
        authority=authority,
        context=context_policy,
        blocking_policy=blocking_policy,
        trigger=_string(data["trigger"], context=f"{context}.trigger"),
        exit=_string(data["exit"], context=f"{context}.exit"),
        finding_authority=permitted,
        failed_evidence_route=failed_evidence_route,
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


def _check_action_coherence(
    authorized_actions: tuple[str, ...],
    durable_artifact: DurableArtifact,
    *,
    context: str,
) -> None:
    """Require ``edit`` and ``commit`` to be a coherent pair for any mutating phase.

    Landing agent-created semantic worktree mutation exercises both capabilities:
    ``edit`` authorizes the semantic mutation, and ``commit`` authorizes the
    trusted Coordinator to materialize it as one ordinary local Git commit (the
    agent never runs ``git commit``).  Neither substitutes for the other, so a
    phase that declares one without the other can only ever fail closed and is a
    statically incoherent profile.  A genuinely non-mutating phase (for example
    ``authorized_actions: [review]`` with ``durable_artifact: none``) declares
    neither token and remains valid.  A ``durable_artifact: required`` phase must
    both produce and land its required artifact, so it requires both tokens.
    """

    has_edit = EDIT_ACTION in authorized_actions
    has_commit = COMMIT_ACTION in authorized_actions
    if has_edit and not has_commit:
        raise ValidationError(
            f"{context}.authorized_actions authorizes '{EDIT_ACTION}' without "
            f"'{COMMIT_ACTION}'; the agent never runs git commit, so an authorized "
            "worktree mutation can only be landed by the Coordinator's trusted commit"
        )
    if has_commit and not has_edit:
        raise ValidationError(
            f"{context}.authorized_actions authorizes '{COMMIT_ACTION}' without "
            f"'{EDIT_ACTION}'; a trusted commit lands agent-created worktree mutation, "
            f"which requires the '{EDIT_ACTION}' authority, so '{COMMIT_ACTION}' "
            f"cannot substitute for an absent '{EDIT_ACTION}'"
        )
    if durable_artifact is DurableArtifact.REQUIRED and not (has_edit and has_commit):
        raise ValidationError(
            f"{context} requires a durable artifact but its authorized_actions do "
            f"not include both '{EDIT_ACTION}' and '{COMMIT_ACTION}'; producing and "
            "landing a required durable artifact exercises both capabilities"
        )


def workflow_profile_from_yaml(text: str) -> WorkflowProfile:
    raw_profile = _yaml_mapping(text, context="workflow profile")
    profile_version = _integer(
        raw_profile.get("schema_version"), context="profile.schema_version"
    )
    if profile_version not in {2, 3}:
        raise ValidationError(
            f"profile.schema_version has unsupported version: {profile_version}"
        )
    data = _fields(
        raw_profile,
        context="workflow profile",
        required={
            "schema_version",
            "profile_id",
            "revision",
            "phases",
            *({"convergence"} if profile_version == 3 else set()),
        },
    )
    convergence = None
    if profile_version == 3:
        raw_convergence = _fields(
            data["convergence"],
            context="profile.convergence",
            required={"protocol", "qualified_cycle_limit"},
        )
        limit = _integer(
            raw_convergence["qualified_cycle_limit"],
            context="profile.convergence.qualified_cycle_limit",
            positive=True,
        )
        if not 2 <= limit <= COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS:
            raise ValidationError(
                "profile.convergence.qualified_cycle_limit must be between 2 and "
                f"{COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS}"
            )
        convergence = ConvergenceConfiguration(
            protocol=_enum(
                ConvergenceProtocol,
                raw_convergence["protocol"],
                context="profile.convergence.protocol",
            ),
            qualified_cycle_limit=limit,
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
                *({"convergence_role"} if profile_version == 3 else set()),
            },
            optional=(
                {"normal_correction_route", "escape_route"}
                if profile_version == 3
                else set()
            ),
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
        durable_artifact = _enum(
            DurableArtifact,
            phase_data["durable_artifact"],
            context=f"{phase_context}.durable_artifact",
        )
        authorized_actions = _strings(
            phase_data["authorized_actions"], context=f"{phase_context}.authorized_actions"
        )
        _check_action_coherence(
            authorized_actions, durable_artifact, context=phase_context
        )
        evidence = _strings(phase_data["evidence"], context=f"{phase_context}.evidence")
        if len(set(evidence)) != len(evidence):
            raise ValidationError(f"{phase_context}.evidence references must be unique")
        convergence_role = (
            _enum(
                ConvergenceRole,
                phase_data["convergence_role"],
                context=f"{phase_context}.convergence_role",
            )
            if profile_version == 3
            else ConvergenceRole.NONE
        )
        normal_correction_route = _optional_string(
            phase_data.get("normal_correction_route"),
            context=f"{phase_context}.normal_correction_route",
        )
        escape_route = _optional_string(
            phase_data.get("escape_route"), context=f"{phase_context}.escape_route"
        )
        if convergence_role is ConvergenceRole.EVALUATOR:
            if normal_correction_route is None or escape_route is None:
                raise ValidationError(
                    f"{phase_context} evaluator requires normal_correction_route and escape_route"
                )
            if normal_correction_route == escape_route:
                raise ValidationError(
                    f"{phase_context} evaluator routes must be distinct"
                )
        elif normal_correction_route is not None or escape_route is not None:
            raise ValidationError(
                f"{phase_context} convergence route declarations are evaluator-only"
            )
        phases[phase_key] = Phase(
            role=_enum(
                WorkflowRole, phase_data["role"], context=f"{phase_context}.role"
            ),
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
            durable_artifact=durable_artifact,
            authorized_scope=_strings(
                phase_data["authorized_scope"], context=f"{phase_context}.authorized_scope"
            ),
            authorized_actions=authorized_actions,
            human_gates=_strings(phase_data["human_gates"], context=f"{phase_context}.human_gates"),
            evidence=evidence,
            review=_review(phase_data["review"], context=f"{phase_context}.review"),
            routes=routes,
            convergence_role=convergence_role,
            normal_correction_route=normal_correction_route,
            escape_route=escape_route,
        )
    for phase_key, phase in phases.items():
        for route_key, route in phase.routes.items():
            if route.target is not None and route.target not in phases:
                raise ValidationError(
                    f"profile.phases.{phase_key}.routes.{route_key} targets unknown phase {route.target}"
                )
        if phase.review and phase.review.failed_evidence_route is not None:
            route = phase.routes.get(phase.review.failed_evidence_route)
            if route is None:
                raise ValidationError(
                    f"profile.phases.{phase_key}.review.failed_evidence_route names unknown route"
                )
            if route.effect not in {RouteEffect.GOTO, RouteEffect.AWAIT_HUMAN}:
                raise ValidationError(
                    f"profile.phases.{phase_key}.review.failed_evidence_route must name a goto or await_human route"
                )
        if profile_version == 3:
            role = phase.convergence_role
            goto_targets = tuple(
                phases[route.target]
                for route in phase.routes.values()
                if route.effect is RouteEffect.GOTO and route.target is not None
            )
            if role is ConvergenceRole.CORRECTOR and any(
                target.convergence_role is not ConvergenceRole.EVALUATOR
                for target in goto_targets
            ):
                raise ValidationError(
                    f"profile.phases.{phase_key} corrector goto routes must target evaluators"
                )
            if role is ConvergenceRole.EVALUATOR:
                if (
                    phase.review is None
                    or phase.review.authority is not ReviewAuthority.GATE
                    or phase.role is not WorkflowRole.INDEPENDENT_REVIEWER
                ):
                    raise ValidationError(
                        f"profile.phases.{phase_key} evaluator must be an independent gate review"
                    )
                if "reopen" in phase.review.finding_authority:
                    raise ValidationError(
                        f"profile.phases.{phase_key} evaluator cannot authorize reopen"
                    )
                if phase.review.failed_evidence_route == phase.escape_route:
                    raise ValidationError(
                        f"profile.phases.{phase_key} reserved escape_route cannot be a failed-evidence route"
                    )
                missing_authority = {
                    "resolve",
                    "residual",
                    "supersede",
                } - set(phase.review.finding_authority)
                if missing_authority:
                    raise ValidationError(
                        f"profile.phases.{phase_key} evaluator lacks Finding authority: "
                        + ", ".join(sorted(missing_authority))
                    )
                if (
                    phase.normal_correction_route not in phase.routes
                    or phase.escape_route not in phase.routes
                ):
                    raise ValidationError(
                        f"profile.phases.{phase_key} evaluator names an unknown convergence route"
                    )
                normal = phase.routes[phase.normal_correction_route or ""]
                escape = phase.routes[phase.escape_route or ""]
                goto_route_keys = {
                    key
                    for key, candidate in phase.routes.items()
                    if candidate.effect is RouteEffect.GOTO
                }
                permitted_goto_keys = {
                    phase.normal_correction_route,
                    phase.escape_route,
                }
                if (
                    phase.review.failed_evidence_route is not None
                    and phase.routes[phase.review.failed_evidence_route].effect
                    is RouteEffect.GOTO
                ):
                    permitted_goto_keys.add(phase.review.failed_evidence_route)
                if goto_route_keys != permitted_goto_keys:
                    raise ValidationError(
                        f"profile.phases.{phase_key} evaluator goto routes must be exactly its normal, escape, and optional failed-evidence routes"
                    )
                if (
                    normal.effect is not RouteEffect.GOTO
                    or phases[normal.target or ""].convergence_role
                    is not ConvergenceRole.CORRECTOR
                ):
                    raise ValidationError(
                        f"profile.phases.{phase_key}.normal_correction_route must goto a corrector"
                    )
                if (
                    escape.effect is not RouteEffect.GOTO
                    or phases[escape.target or ""].convergence_role
                    is not ConvergenceRole.DIAGNOSIS
                ):
                    raise ValidationError(
                        f"profile.phases.{phase_key}.escape_route must goto diagnosis"
                    )
            if role in {ConvergenceRole.DIAGNOSIS, ConvergenceRole.REFRAME} and phase.review is not None:
                raise ValidationError(
                    f"profile.phases.{phase_key} {role.value} must be a non-review phase"
                )
            if role is ConvergenceRole.DIAGNOSIS and (
                EDIT_ACTION in phase.authorized_actions
                or COMMIT_ACTION in phase.authorized_actions
                or phase.durable_artifact is not DurableArtifact.NONE
            ):
                raise ValidationError(
                    f"profile.phases.{phase_key} diagnosis must be read-only with no durable artifact"
                )
            if role is ConvergenceRole.STRATEGY_GATE and (
                phase.review is None
                or phase.review.authority is not ReviewAuthority.GATE
                or phase.review.finding_authority
            ):
                raise ValidationError(
                    f"profile.phases.{phase_key} strategy_gate must be a gate with empty Finding authority"
                )
            target_roles = {target.convergence_role for target in goto_targets}
            if role is ConvergenceRole.DIAGNOSIS and (
                not target_roles or target_roles != {ConvergenceRole.REFRAME}
            ):
                raise ValidationError(
                    f"profile.phases.{phase_key} diagnosis goto routes must target reframe"
                )
            if role is ConvergenceRole.REFRAME and (
                not target_roles or target_roles != {ConvergenceRole.STRATEGY_GATE}
            ):
                raise ValidationError(
                    f"profile.phases.{phase_key} reframe goto routes must target strategy_gate"
                )
            if role is ConvergenceRole.STRATEGY_GATE and (
                not target_roles
                or target_roles != {ConvergenceRole.CORRECTOR, ConvergenceRole.REFRAME}
            ):
                raise ValidationError(
                    f"profile.phases.{phase_key} strategy_gate goto routes must target both corrector and reframe"
                )
    if profile_version == 3:
        declared_roles = {phase.convergence_role for phase in phases.values()}
        required_roles = {
            ConvergenceRole.CORRECTOR,
            ConvergenceRole.EVALUATOR,
            ConvergenceRole.DIAGNOSIS,
            ConvergenceRole.REFRAME,
            ConvergenceRole.STRATEGY_GATE,
        }
        missing_roles = required_roles - declared_roles
        if missing_roles:
            raise ValidationError(
                "profile v3 convergence topology lacks roles: "
                + ", ".join(sorted(role.value for role in missing_roles))
            )
    return WorkflowProfile(
        schema_version=profile_version,
        profile_id=_string(data["profile_id"], context="profile.profile_id"),
        revision=_string(data["revision"], context="profile.revision"),
        phases=phases,
        convergence=convergence,
    )


def run_execution_configuration_from_yaml(text: str) -> RunExecutionConfiguration:
    """Parse the strict closed-provider MVP binding grammar exactly once upstream."""

    data = _fields(
        _yaml_mapping(text, context="Run execution configuration"),
        context="Run execution configuration",
        required={"schema_version", "bindings"},
    )
    raw_bindings = _mapping(
        data["bindings"], context="Run execution configuration.bindings"
    )
    bindings: dict[WorkflowRole, ExecutionBinding] = {}
    for raw_role, raw_binding in raw_bindings.items():
        role = _enum(
            WorkflowRole,
            raw_role,
            context="Run execution configuration.bindings role",
        )
        context = f"Run execution configuration.bindings.{role.value}"
        binding = _fields(
            raw_binding,
            context=context,
            required={"adapter", "model", "reasoning"},
        )
        adapter = _string(binding["adapter"], context=f"{context}.adapter")
        if adapter not in {"codex_cli", "claude_cli"}:
            raise ValidationError(f"{context}.adapter must be codex_cli or claude_cli")
        bindings[role] = ExecutionBinding(
            adapter=adapter,
            model=_execution_value(binding["model"], context=f"{context}.model"),
            reasoning=_execution_value(
                binding["reasoning"], context=f"{context}.reasoning"
            ),
        )
    return RunExecutionConfiguration(
        schema_version=_version(
            data["schema_version"],
            context="Run execution configuration.schema_version",
        ),
        bindings=MappingProxyType(bindings),
    )


def _finding_delta(value: Any, *, context: str) -> FindingDeltaValue:
    return _finding_delta_for_version(value, context=context, result_version=1)


def _finding_delta_for_version(
    value: Any, *, context: str, result_version: int
) -> FindingDeltaValue:
    raw = _mapping(value, context=context)
    finding_id = _optional_string(raw.get("finding_id"), context=f"{context}.finding_id")
    if result_version == 3 and finding_id is not None:
        if "target" not in raw:
            data = _fields(raw, context=context, required=set(FINDING_CITATION_KEYS))
            return FindingCitation(
                finding_id=_string(data["finding_id"], context=f"{context}.finding_id"),
                evidence_refs=_strings(data["evidence_refs"], context=f"{context}.evidence_refs"),
            )
        data = _fields(raw, context=context, required=set(FINDING_TRANSITION_KEYS))
        target_raw = _mapping(data["target"], context=f"{context}.target")
        state = _string(target_raw.get("state"), context=f"{context}.target.state")
        target: FindingTransitionTarget
        if state == FindingState.RESOLVED.value:
            _fields(target_raw, context=f"{context}.target", required=set(RESOLVE_TARGET_KEYS))
            target = ResolveFinding()
        elif state == FindingState.RESIDUAL.value:
            _fields(target_raw, context=f"{context}.target", required=set(RESIDUAL_TARGET_KEYS))
            target = ResidualFinding()
        elif state == FindingState.SUPERSEDED.value:
            target_data = _fields(
                target_raw, context=f"{context}.target", required=set(SUPERSEDE_TARGET_KEYS)
            )
            target = SupersedeFinding(
                _string(target_data["successor_ref"], context=f"{context}.target.successor_ref")
            )
        elif state == FindingState.OPEN.value:
            target_data = _fields(
                target_raw, context=f"{context}.target", required=set(REOPEN_TARGET_KEYS)
            )
            target = ReopenFinding(
                _boolean(
                    target_data["new_material_evidence"],
                    context=f"{context}.target.new_material_evidence",
                )
            )
        else:
            raise ValidationError(f"{context}.target.state is not an admitted transition")
        return FindingTransition(
            finding_id=_string(data["finding_id"], context=f"{context}.finding_id"),
            target=target,
            evidence_refs=_strings(data["evidence_refs"], context=f"{context}.evidence_refs"),
        )
    if finding_id is not None:
        data = _fields(
            raw,
            context=context,
            required=set(EXISTING_FINDING_DELTA_REQUIRED_KEYS),
            optional=set(EXISTING_FINDING_DELTA_OPTIONAL_KEYS),
        )
        return ExistingFindingDelta(
            finding_id=finding_id,
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
    data = _fields(
        raw,
        context=context,
        required=set(NEW_FINDING_DELTA_REQUIRED_KEYS),
        optional=set(NEW_FINDING_DELTA_OPTIONAL_KEYS) | ({"local_ref"} if result_version in {2, 3} else set()),
    )
    return NewFindingDelta(
        finding_id=finding_id,
        proposed_state=_enum(
            FindingState, data["proposed_state"], context=f"{context}.proposed_state"
        ),
        invariant=_string(data["invariant"], context=f"{context}.invariant"),
        blocking=_boolean(data["blocking"], context=f"{context}.blocking"),
        evidence_refs=_strings(data["evidence_refs"], context=f"{context}.evidence_refs"),
        successor_ref=_optional_string(
            data.get("successor_ref"), context=f"{context}.successor_ref"
        ),
        new_material_evidence=_boolean(
            data.get("new_material_evidence", False),
            context=f"{context}.new_material_evidence",
        ),
        local_ref=_optional_string(data.get("local_ref"), context=f"{context}.local_ref"),
    )


def _commit_intent(value: Any, *, context: str) -> CommitIntent | None:
    if value is None:
        return None
    data = _fields(value, context=context, required=set(COMMIT_INTENT_REQUIRED_KEYS))
    paths = _strings(data["paths"], context=f"{context}.paths")
    if not paths:
        raise ValidationError(f"{context}.paths must not be empty")
    return CommitIntent(paths=paths, subject=_string(data["subject"], context=f"{context}.subject"))


def _coverage_claim(value: Any, *, context: str) -> CoverageClaim:
    data = _fields(
        value,
        context=context,
        required={"finding_id", "disposition", "evidence_refs"},
    )
    return CoverageClaim(
        finding_id=_string(data["finding_id"], context=f"{context}.finding_id"),
        disposition=_enum(
            CoverageDisposition,
            data["disposition"],
            context=f"{context}.disposition",
        ),
        evidence_refs=_strings(data["evidence_refs"], context=f"{context}.evidence_refs"),
    )


def _convergence_assessment(value: Any, *, context: str) -> ConvergenceAssessment:
    data = _fields(
        value,
        context=context,
        required={"finding_id", "correction_hop_id", "result", "evidence_refs"},
    )
    return ConvergenceAssessment(
        finding_id=_string(data["finding_id"], context=f"{context}.finding_id"),
        correction_hop_id=_string(data["correction_hop_id"], context=f"{context}.correction_hop_id"),
        result=_enum(ConvergenceAssessmentResult, data["result"], context=f"{context}.result"),
        evidence_refs=_strings(data["evidence_refs"], context=f"{context}.evidence_refs"),
    )


def _nonempty_ids(value: Any, *, context: str) -> tuple[str, ...]:
    ids = _strings(value, context=context)
    if not ids:
        raise ValidationError(f"{context} must not be empty")
    return ids


def _diagnosis_report(value: Any, *, context: str) -> DiagnosisReport | None:
    if value is None:
        return None
    data = _fields(value, context=context, required={"escape_hop_id", "finding_ids", "classification", "analysis", "evidence_refs"})
    return DiagnosisReport(
        escape_hop_id=_string(data["escape_hop_id"], context=f"{context}.escape_hop_id"),
        finding_ids=_nonempty_ids(data["finding_ids"], context=f"{context}.finding_ids"),
        classification=_enum(DiagnosisClassification, data["classification"], context=f"{context}.classification"),
        analysis=_string(data["analysis"], context=f"{context}.analysis"),
        evidence_refs=_strings(data["evidence_refs"], context=f"{context}.evidence_refs"),
    )


def _strategy_revision(value: Any, *, context: str) -> StrategyRevision | None:
    if value is None:
        return None
    data = _fields(value, context=context, required={"escape_hop_id", "diagnosis_hop_id", "finding_ids", "kind", "plan", "evidence_refs"})
    return StrategyRevision(
        escape_hop_id=_string(data["escape_hop_id"], context=f"{context}.escape_hop_id"),
        diagnosis_hop_id=_string(data["diagnosis_hop_id"], context=f"{context}.diagnosis_hop_id"),
        finding_ids=_nonempty_ids(data["finding_ids"], context=f"{context}.finding_ids"),
        kind=_enum(StrategyRevisionKind, data["kind"], context=f"{context}.kind"),
        plan=_string(data["plan"], context=f"{context}.plan"),
        evidence_refs=_strings(data["evidence_refs"], context=f"{context}.evidence_refs"),
    )


def _strategy_assessment(value: Any, *, context: str) -> StrategyAssessment | None:
    if value is None:
        return None
    data = _fields(value, context=context, required={"escape_hop_id", "diagnosis_hop_id", "revision_hop_id", "finding_ids", "result", "evidence_refs"})
    return StrategyAssessment(
        escape_hop_id=_string(data["escape_hop_id"], context=f"{context}.escape_hop_id"),
        diagnosis_hop_id=_string(data["diagnosis_hop_id"], context=f"{context}.diagnosis_hop_id"),
        revision_hop_id=_string(data["revision_hop_id"], context=f"{context}.revision_hop_id"),
        finding_ids=_nonempty_ids(data["finding_ids"], context=f"{context}.finding_ids"),
        result=_enum(StrategyAssessmentResult, data["result"], context=f"{context}.result"),
        evidence_refs=_strings(data["evidence_refs"], context=f"{context}.evidence_refs"),
    )


def routing_result_from_data(value: Any) -> RoutingResult:
    raw = _mapping(value, context="routing result")
    version = _integer(raw.get("schema_version"), context="routing result.schema_version")
    if version not in {1, 2}:
        raise ValidationError(f"routing result.schema_version has unsupported version: {version}")
    data = _fields(
        raw,
        context="routing result",
        required=set(
            ROUTING_RESULT_REQUIRED_KEYS
            if version == 1
            else ROUTING_RESULT_V2_REQUIRED_KEYS
        ),
    )
    escalation = None
    if data["escalation"] is not None:
        raw_escalation = _fields(
            data["escalation"],
            context="routing result.escalation",
            required=set(ESCALATION_REQUIRED_KEYS),
        )
        kind = _string(raw_escalation["kind"], context="routing result.escalation.kind")
        if kind not in ESCALATION_KINDS:
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
    raw_coverage = data.get("coverage_claims", [])
    raw_assessments = data.get("convergence_assessments", [])
    if not isinstance(raw_coverage, list):
        raise ValidationError("routing result.coverage_claims must be a list")
    if not isinstance(raw_assessments, list):
        raise ValidationError("routing result.convergence_assessments must be a list")
    coverage_claims = tuple(
        _coverage_claim(item, context=f"routing result.coverage_claims[{index}]")
        for index, item in enumerate(raw_coverage)
    )
    assessments = tuple(
        _convergence_assessment(
            item, context=f"routing result.convergence_assessments[{index}]"
        )
        for index, item in enumerate(raw_assessments)
    )
    return RoutingResult(
        schema_version=version,
        outcome=_enum(Outcome, data["outcome"], context="routing result.outcome"),
        requested_route=_string(data["requested_route"], context="routing result.requested_route"),
        scope_changed=_boolean(data["scope_changed"], context="routing result.scope_changed"),
        requires_human=_boolean(data["requires_human"], context="routing result.requires_human"),
        escalation=escalation,
        findings=tuple(
            _finding_delta_for_version(
                item,
                context=f"routing result.findings[{index}]",
                result_version=version,
            )
            for index, item in enumerate(raw_findings)
        ),
        claimed_commits=_strings(data["claimed_commits"], context="routing result.claimed_commits"),
        commit_intent=_commit_intent(data["commit_intent"], context="routing result.commit_intent"),
        evidence_refs=_strings(data["evidence_refs"], context="routing result.evidence_refs"),
        summary=_string(data["summary"], context="routing result.summary"),
        coverage_claims=coverage_claims,
        convergence_assessments=assessments,
        diagnosis=_diagnosis_report(data.get("diagnosis"), context="routing result.diagnosis"),
        strategy_revision=_strategy_revision(data.get("strategy_revision"), context="routing result.strategy_revision"),
        strategy_assessment=_strategy_assessment(data.get("strategy_assessment"), context="routing result.strategy_assessment"),
    )


def _family_result_data(value: Any, *, field: str, values: set[str], context: str) -> tuple[dict[str, Any], int, Escalation | None]:
    raw = _mapping(value, context=context)
    version = _integer(raw.get("schema_version"), context=f"{context}.schema_version")
    if version not in {1, 2, 3}:
        raise ValidationError(f"{context}.schema_version has unsupported version: {version}")
    required = {
        "schema_version", field, "requested_route", "scope_changed", "requires_human",
        "escalation", "findings", "claimed_commits", "commit_intent", "evidence_refs", "summary",
    }
    data = _fields(raw, context=context, required=required)
    parsed = _string(data[field], context=f"{context}.{field}")
    if parsed not in values:
        raise ValidationError(f"{context}.{field} is not admitted by this result family")
    escalation = None
    if data["escalation"] is not None:
        raw_escalation = _fields(data["escalation"], context=f"{context}.escalation", required=set(ESCALATION_REQUIRED_KEYS))
        kind = _string(raw_escalation["kind"], context=f"{context}.escalation.kind")
        if kind not in ESCALATION_KINDS:
            raise ValidationError(f"{context}.escalation.kind is unknown: {kind}")
        escalation = Escalation(kind=kind, question=_string(raw_escalation["question"], context=f"{context}.escalation.question"))
    if not isinstance(data["findings"], list):
        raise ValidationError(f"{context}.findings must be a list")
    return data, version, escalation


def work_result_from_data(value: Any) -> WorkResult:
    data, version, escalation = _family_result_data(value, field="status", values={item.value for item in WorkStatus}, context="work result")
    return WorkResult(schema_version=version, status=_enum(WorkStatus, data["status"], context="work result.status"), requested_route=_string(data["requested_route"], context="work result.requested_route"), scope_changed=_boolean(data["scope_changed"], context="work result.scope_changed"), requires_human=_boolean(data["requires_human"], context="work result.requires_human"), escalation=escalation, findings=tuple(_finding_delta_for_version(item, context=f"work result.findings[{index}]", result_version=version) for index, item in enumerate(data["findings"])), claimed_commits=_strings(data["claimed_commits"], context="work result.claimed_commits"), commit_intent=_commit_intent(data["commit_intent"], context="work result.commit_intent"), evidence_refs=_strings(data["evidence_refs"], context="work result.evidence_refs"), summary=_string(data["summary"], context="work result.summary"))


def review_verdict_from_data(value: Any) -> ReviewVerdict:
    data, version, escalation = _family_result_data(value, field="verdict", values={item.value for item in ReviewDisposition}, context="review verdict")
    return ReviewVerdict(schema_version=version, verdict=_enum(ReviewDisposition, data["verdict"], context="review verdict.verdict"), requested_route=_string(data["requested_route"], context="review verdict.requested_route"), scope_changed=_boolean(data["scope_changed"], context="review verdict.scope_changed"), requires_human=_boolean(data["requires_human"], context="review verdict.requires_human"), escalation=escalation, findings=tuple(_finding_delta_for_version(item, context=f"review verdict.findings[{index}]", result_version=version) for index, item in enumerate(data["findings"])), claimed_commits=_strings(data["claimed_commits"], context="review verdict.claimed_commits"), commit_intent=_commit_intent(data["commit_intent"], context="review verdict.commit_intent"), evidence_refs=_strings(data["evidence_refs"], context="review verdict.evidence_refs"), summary=_string(data["summary"], context="review verdict.summary"))


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
    data = _json_value(run)
    # A read-only inspection must be able to serialize an historical v1 Run
    # without silently manufacturing the two v2 fields.
    if run.schema_version == 1:
        data.pop("semantic_stop", None)
        data.pop("successor_provenance", None)
    if run.schema_version in {1, 2}:
        for hop in data["hops"]:
            hop.pop("coverage_claims", None)
            hop.pop("convergence_assessments", None)
            hop.pop("pre_route_anchor_sha256", None)
            hop.pop("requested_route", None)
            hop.pop("route_reason", None)
            hop.pop("phase", None)
            hop.pop("diagnosis", None)
            hop.pop("strategy_revision", None)
            hop.pop("strategy_assessment", None)
        for finding in data["findings"]:
            finding.pop("correction_obligation", None)
    elif run.schema_version == 3:
        for hop in data["hops"]:
            if hop.get("pre_route_anchor_sha256") is None:
                hop.pop("pre_route_anchor_sha256", None)
    for hop in data["hops"]:
        if hop.get("result_contract_kind") is None:
            hop.pop("result_contract_kind", None)
        if hop.get("result_contract_version") is None:
            hop.pop("result_contract_version", None)
    text = json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n"
    # Callers may construct immutable dataclasses directly.  Persisted Run
    # truth must still pass the same closed-wire validation as loaded state.
    run_from_json(text)
    return text


def _hop_summary(value: Any, *, context: str, run_version: int) -> HopSummary:
    all_fields = {field.name for field in fields(HopSummary)}
    convergence_fields = {"coverage_claims", "convergence_assessments", "diagnosis", "strategy_revision", "strategy_assessment"}
    anchor_field = {"pre_route_anchor_sha256"}
    contract_identity_fields = {"result_contract_kind", "result_contract_version"}
    routing_truth_fields = {"requested_route", "route_reason"}
    phase_field = {"phase"}
    data = _fields(
        value,
        context=context,
        required=(
            all_fields - anchor_field - contract_identity_fields
            if run_version == 3
            else all_fields
            - convergence_fields
            - anchor_field
            - contract_identity_fields
            - routing_truth_fields
            - phase_field
        ),
        optional=(anchor_field | contract_identity_fields)
        if run_version == 3 else contract_identity_fields,
    )
    raw_coverage = data.get("coverage_claims", [])
    raw_assessments = data.get("convergence_assessments", [])
    if not isinstance(raw_coverage, list) or not isinstance(raw_assessments, list):
        raise ValidationError(f"{context} convergence records must be lists")
    return HopSummary(
        hop_id=_string(data["hop_id"], context=f"{context}.hop_id"),
        sequence=_integer(data["sequence"], context=f"{context}.sequence", positive=True),
        role=_string(data["role"], context=f"{context}.role"),
        outcome=_string(data["outcome"], context=f"{context}.outcome"),
        base_head=_string(data["base_head"], context=f"{context}.base_head"),
        end_head=_string(data["end_head"], context=f"{context}.end_head"),
        actual_commits=_strings(data["actual_commits"], context=f"{context}.actual_commits"),
        finding_delta_ids=_strings(
            data["finding_delta_ids"], context=f"{context}.finding_delta_ids"
        ),
        evidence_refs=_strings(data["evidence_refs"], context=f"{context}.evidence_refs"),
        applied_route=_optional_string(data["applied_route"], context=f"{context}.applied_route"),
        stop_reason=_optional_string(data["stop_reason"], context=f"{context}.stop_reason"),
        summary=_string(data["summary"], context=f"{context}.summary"),
        attribution=_string(data["attribution"], context=f"{context}.attribution"),
        coverage_claims=tuple(
            _coverage_claim(item, context=f"{context}.coverage_claims[{index}]")
            for index, item in enumerate(raw_coverage)
        ),
        convergence_assessments=tuple(
            _convergence_assessment(
                item, context=f"{context}.convergence_assessments[{index}]"
            )
            for index, item in enumerate(raw_assessments)
        ),
        pre_route_anchor_sha256=(
            None
            if "pre_route_anchor_sha256" not in data
            or data["pre_route_anchor_sha256"] is None
            else _sha256(
                data["pre_route_anchor_sha256"],
                context=f"{context}.pre_route_anchor_sha256",
            )
        ),
        result_contract_kind=(
            None
            if data.get("result_contract_kind") is None
            else _enum(
                ResultContractKind, data["result_contract_kind"],
                context=f"{context}.result_contract_kind",
            )
        ),
        result_contract_version=(
            None
            if data.get("result_contract_version") is None
            else _integer(
                data["result_contract_version"],
                context=f"{context}.result_contract_version", positive=True,
            )
        ),
        requested_route=(
            None
            if run_version != 3
            else _optional_string(
                data["requested_route"], context=f"{context}.requested_route"
            )
        ),
        route_reason=(
            None
            if run_version != 3
            else _optional_string(data["route_reason"], context=f"{context}.route_reason")
        ),
        phase=(
            None
            if run_version != 3
            else _string(data["phase"], context=f"{context}.phase")
        ),
        diagnosis=(None if run_version != 3 else _diagnosis_report(data["diagnosis"], context=f"{context}.diagnosis")),
        strategy_revision=(None if run_version != 3 else _strategy_revision(data["strategy_revision"], context=f"{context}.strategy_revision")),
        strategy_assessment=(None if run_version != 3 else _strategy_assessment(data["strategy_assessment"], context=f"{context}.strategy_assessment")),
    )


def _finding(value: Any, *, context: str, run_version: int) -> Finding:
    all_fields = {field.name for field in fields(Finding)}
    data = _fields(
        value,
        context=context,
        required=(all_fields if run_version == 3 else all_fields - {"correction_obligation"}),
    )
    obligation = None
    if run_version == 3 and data["correction_obligation"] is not None:
        raw_obligation = _fields(
            data["correction_obligation"],
            context=f"{context}.correction_obligation",
            required={"owner", "introduced_by_hop"},
        )
        obligation = CorrectionObligation(
            owner=_enum(
                CorrectionObligationOwner,
                raw_obligation["owner"],
                context=f"{context}.correction_obligation.owner",
            ),
            introduced_by_hop=_string(
                raw_obligation["introduced_by_hop"],
                context=f"{context}.correction_obligation.introduced_by_hop",
            ),
        )
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
        correction_obligation=obligation,
    )


def _semantic_stop(value: Any, *, context: str) -> SemanticStop | None:
    if value is None:
        return None
    data = _fields(value, context=context, required={"kind", "question", "source_hop_id"})
    kind = _string(data["kind"], context=f"{context}.kind")
    if kind not in ESCALATION_KINDS:
        raise ValidationError(f"{context}.kind is unknown: {kind}")
    return SemanticStop(
        kind=kind,
        question=_string(data["question"], context=f"{context}.question"),
        source_hop_id=_string(data["source_hop_id"], context=f"{context}.source_hop_id"),
    )


def _sha256(value: Any, *, context: str) -> str:
    text = _string(value, context=context)
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        raise ValidationError(f"{context} must be a lowercase SHA-256 digest")
    return text


def _optional_integer(value: Any, *, context: str) -> int | None:
    if value is None:
        return None
    return _integer(value, context=context)


def _optional_boolean(value: Any, *, context: str) -> bool | None:
    if value is None:
        return None
    return _boolean(value, context=context)


def canonical_candidate_result_sha256(text: str) -> str:
    """Digest canonical JSON for an already validated candidate envelope."""

    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValidationError(f"invalid candidate result JSON: {error}") from error
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def pre_route_anchor_material(
    run: ExecutionEvidenceRunIdentity,
    *,
    hop_id: str,
    hop_sequence: int,
    invocation_id: str,
    phase: str,
    workflow_role: str,
    outcome: str,
    base_head: str,
    end_head: str,
    actual_commits: tuple[str, ...],
    candidate_result_sha256: str,
    required_evidence: tuple[RequiredEvidenceIdentity, ...],
) -> dict[str, Any]:
    """Return the exact route-independent anchor tuple as closed JSON data."""

    return {
        "run": _json_value(run),
        "hop_id": hop_id,
        "hop_sequence": hop_sequence,
        "invocation_id": invocation_id,
        "phase": phase,
        "workflow_role": workflow_role,
        "outcome": outcome,
        "base_head": base_head,
        "end_head": end_head,
        "actual_commits": list(actual_commits),
        "candidate_result_sha256": candidate_result_sha256,
        "required_evidence": [_json_value(item) for item in required_evidence],
    }


def pre_route_anchor_sha256(
    run: ExecutionEvidenceRunIdentity,
    **values: Any,
) -> str:
    material = pre_route_anchor_material(run, **values)
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stat_fingerprint(value: Any, *, context: str) -> StatFingerprint | None:
    if value is None:
        return None
    data = _fields(
        value,
        context=context,
        required={"st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns"},
    )
    return StatFingerprint(
        st_dev=_integer(data["st_dev"], context=f"{context}.st_dev"),
        st_ino=_integer(data["st_ino"], context=f"{context}.st_ino"),
        st_mode=_integer(data["st_mode"], context=f"{context}.st_mode"),
        st_size=_integer(data["st_size"], context=f"{context}.st_size"),
        st_mtime_ns=_integer(data["st_mtime_ns"], context=f"{context}.st_mtime_ns"),
    )


def _base64_string(value: Any, *, context: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{context} must be a base64-encoded string")
    try:
        base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValidationError(f"{context} must be a base64-encoded string") from error
    return value


def _untrusted_diagnostic(value: Any, *, context: str) -> UntrustedDiagnosticCapture | None:
    if value is None:
        return None
    data = _fields(
        value,
        context=context,
        required={
            "stdout_tail_base64",
            "stdout_tail_truncated",
            "stderr_tail_base64",
            "stderr_tail_truncated",
        },
    )
    return UntrustedDiagnosticCapture(
        stdout_tail_base64=_base64_string(
            data["stdout_tail_base64"], context=f"{context}.stdout_tail_base64"
        ),
        stdout_tail_truncated=_boolean(
            data["stdout_tail_truncated"], context=f"{context}.stdout_tail_truncated"
        ),
        stderr_tail_base64=_base64_string(
            data["stderr_tail_base64"], context=f"{context}.stderr_tail_base64"
        ),
        stderr_tail_truncated=_boolean(
            data["stderr_tail_truncated"], context=f"{context}.stderr_tail_truncated"
        ),
    )


def _observation(value: Any, *, context: str) -> Observation:
    data = _fields(
        value,
        context=context,
        required={field.name for field in fields(Observation)} - {"untrusted_diagnostic"},
        optional={"untrusted_diagnostic"},
    )
    status = _string(data["status"], context=f"{context}.status")
    if status not in {"pass", "fail", "error", "not_run"}:
        raise ValidationError(f"{context}.status is unsupported: {status}")
    command = _strings(data["command"], context=f"{context}.command")
    exit_code = _optional_integer(data["exit_code"], context=f"{context}.exit_code")
    signal = _optional_integer(data["signal"], context=f"{context}.signal")
    error_class = _optional_string(data["error_class"], context=f"{context}.error_class")
    if status == "pass" and (exit_code != 0 or signal is not None):
        raise ValidationError(f"{context} pass outcome is inconsistent")
    if status == "fail" and (
        exit_code is None or exit_code <= 0 or signal is not None
    ):
        raise ValidationError(f"{context} fail outcome is inconsistent")
    if status == "error" and error_class is None:
        raise ValidationError(f"{context} error outcome requires error_class")
    stream_values: dict[str, tuple[int | None, str | None, bool | None]] = {}
    for stream in ("stdout", "stderr"):
        byte_count = _optional_integer(
            data[f"{stream}_byte_count"], context=f"{context}.{stream}_byte_count"
        )
        digest = (
            None
            if data[f"{stream}_sha256"] is None
            else _sha256(data[f"{stream}_sha256"], context=f"{context}.{stream}_sha256")
        )
        omitted = _optional_boolean(
            data[f"{stream}_body_omitted"],
            context=f"{context}.{stream}_body_omitted",
        )
        if (byte_count is None, digest is None, omitted is None) not in {
            (True, True, True),
            (False, False, False),
        }:
            raise ValidationError(f"{context} has incomplete {stream} facts")
        if byte_count is not None and (
            byte_count < 0 or omitted != (byte_count > 0)
        ):
            raise ValidationError(f"{context} has invalid {stream} facts")
        if status in {"pass", "fail"} and byte_count is None:
            raise ValidationError(f"{context} lacks required {stream} facts")
        stream_values[stream] = (byte_count, digest, omitted)
    provider = _string(data["provider"], context=f"{context}.provider")
    command_name = _string(
        data["command_name"], context=f"{context}.command_name"
    )
    if provider != command_name:
        raise ValidationError(f"{context} provider/command_name identity mismatch")
    return Observation(
        provider=provider,
        status=status,
        command_name=command_name,
        command=command,
        summary=_string(data["summary"], context=f"{context}.summary"),
        artifact_ref=_optional_string(data["artifact_ref"], context=f"{context}.artifact_ref"),
        argv_digest=_sha256(data["argv_digest"], context=f"{context}.argv_digest"),
        authored_argv0=_string(data["authored_argv0"], context=f"{context}.authored_argv0"),
        timeout_seconds=_integer(
            data["timeout_seconds"], context=f"{context}.timeout_seconds", positive=True
        ),
        cwd_policy=_string(data["cwd_policy"], context=f"{context}.cwd_policy"),
        invocation_path=_optional_string(
            data["invocation_path"], context=f"{context}.invocation_path"
        ),
        canonical_path=_optional_string(
            data["canonical_path"], context=f"{context}.canonical_path"
        ),
        stat_fingerprint=_stat_fingerprint(
            data["stat_fingerprint"], context=f"{context}.stat_fingerprint"
        ),
        environment_policy=_string(
            data["environment_policy"], context=f"{context}.environment_policy"
        ),
        path_source=_string(data["path_source"], context=f"{context}.path_source"),
        path_digest=_sha256(data["path_digest"], context=f"{context}.path_digest"),
        exit_code=exit_code,
        signal=signal,
        error_class=error_class,
        stdout_byte_count=stream_values["stdout"][0],
        stdout_sha256=stream_values["stdout"][1],
        stdout_body_omitted=stream_values["stdout"][2],
        stderr_byte_count=stream_values["stderr"][0],
        stderr_sha256=stream_values["stderr"][1],
        stderr_body_omitted=stream_values["stderr"][2],
        untrusted_diagnostic=_untrusted_diagnostic(
            data.get("untrusted_diagnostic"), context=f"{context}.untrusted_diagnostic"
        ),
    )


def _required_evidence_identity(
    value: Any, *, context: str
) -> RequiredEvidenceIdentity:
    data = _fields(
        value,
        context=context,
        required={"command_name", "argv_digest", "timeout_seconds"},
    )
    return RequiredEvidenceIdentity(
        command_name=_string(data["command_name"], context=f"{context}.command_name"),
        argv_digest=_sha256(data["argv_digest"], context=f"{context}.argv_digest"),
        timeout_seconds=_integer(
            data["timeout_seconds"], context=f"{context}.timeout_seconds", positive=True
        ),
    )


def execution_attempt_to_data(attempt: StagedQualificationAttempt) -> dict[str, Any]:
    return _json_value(attempt)


def _staged_qualification_attempt(
    value: Any,
    *,
    context: str,
    run: ExecutionEvidenceRunIdentity,
) -> StagedQualificationAttempt:
    data = _fields(
        value,
        context=context,
        required={field.name for field in fields(StagedQualificationAttempt)},
    )
    raw_plan = data["required_evidence"]
    raw_observations = data["trusted_observations"]
    if not isinstance(raw_plan, list) or not isinstance(raw_observations, list):
        raise ValidationError(f"{context} evidence plan and observations must be lists")
    plan = tuple(
        _required_evidence_identity(item, context=f"{context}.required_evidence[{index}]")
        for index, item in enumerate(raw_plan)
    )
    if len({item.command_name for item in plan}) != len(plan):
        raise ValidationError(f"{context}.required_evidence command names must be unique")
    observations = tuple(
        _observation(item, context=f"{context}.trusted_observations[{index}]")
        for index, item in enumerate(raw_observations)
    )
    attempt = StagedQualificationAttempt(
        hop_id=_string(data["hop_id"], context=f"{context}.hop_id"),
        hop_sequence=_integer(
            data["hop_sequence"], context=f"{context}.hop_sequence", positive=True
        ),
        invocation_id=_string(data["invocation_id"], context=f"{context}.invocation_id"),
        pre_route_anchor_sha256=_sha256(
            data["pre_route_anchor_sha256"],
            context=f"{context}.pre_route_anchor_sha256",
        ),
        phase=_string(data["phase"], context=f"{context}.phase"),
        workflow_role=_enum(
            WorkflowRole,
            data["workflow_role"],
            context=f"{context}.workflow_role",
        ).value,
        persona=_string(data["persona"], context=f"{context}.persona"),
        persona_sha256=_sha256(data["persona_sha256"], context=f"{context}.persona_sha256"),
        adapter=_string(data["adapter"], context=f"{context}.adapter"),
        executable=_string(data["executable"], context=f"{context}.executable"),
        model=_optional_string(data["model"], context=f"{context}.model"),
        reasoning=_optional_string(data["reasoning"], context=f"{context}.reasoning"),
        context=_string(data["context"], context=f"{context}.context"),
        timeout_seconds=_number(
            data["timeout_seconds"], context=f"{context}.timeout_seconds", positive=True
        ),
        returncode=_optional_integer(data["returncode"], context=f"{context}.returncode"),
        timed_out=_boolean(data["timed_out"], context=f"{context}.timed_out"),
        candidate_result_present=_boolean(
            data["candidate_result_present"],
            context=f"{context}.candidate_result_present",
        ),
        candidate_result_sha256=_sha256(
            data["candidate_result_sha256"],
            context=f"{context}.candidate_result_sha256",
        ),
        publication=_string(data["publication"], context=f"{context}.publication"),
        outcome=_enum(
            Outcome, data["outcome"], context=f"{context}.outcome"
        ).value,
        base_head=_string(data["base_head"], context=f"{context}.base_head"),
        end_head=_string(data["end_head"], context=f"{context}.end_head"),
        actual_commits=_strings(data["actual_commits"], context=f"{context}.actual_commits"),
        required_evidence=plan,
        trusted_observations=observations,
    )
    if not attempt.candidate_result_present:
        raise ValidationError(f"{context} must record a validated candidate result")
    if attempt.publication != "staged":
        raise ValidationError(f"{context}.publication must be staged")
    if hashlib.sha256(attempt.persona.encode("utf-8")).hexdigest() != attempt.persona_sha256:
        raise ValidationError(f"{context}.persona_sha256 mismatch")
    expected_anchor = pre_route_anchor_sha256(
        run,
        hop_id=attempt.hop_id,
        hop_sequence=attempt.hop_sequence,
        invocation_id=attempt.invocation_id,
        phase=attempt.phase,
        workflow_role=attempt.workflow_role,
        outcome=attempt.outcome,
        base_head=attempt.base_head,
        end_head=attempt.end_head,
        actual_commits=attempt.actual_commits,
        candidate_result_sha256=attempt.candidate_result_sha256,
        required_evidence=attempt.required_evidence,
    )
    if expected_anchor != attempt.pre_route_anchor_sha256:
        raise ValidationError(f"{context}.pre_route_anchor_sha256 mismatch")
    plan_identities = {
        (item.command_name, item.argv_digest, item.timeout_seconds) for item in plan
    }
    observation_identities = [
        (item.command_name, item.argv_digest, item.timeout_seconds)
        for item in observations
    ]
    if len(set(observation_identities)) != len(observation_identities):
        raise ValidationError(f"{context} has duplicate trusted observations")
    extras = set(observation_identities) - plan_identities
    if extras:
        raise ValidationError(f"{context} has extra or identity-mismatched observations")
    return attempt


def execution_evidence_to_json(document: ExecutionEvidenceDocument) -> str:
    text = json.dumps(_json_value(document), sort_keys=True, separators=(",", ":")) + "\n"
    parsed = execution_evidence_from_json(text)
    if parsed != document:
        raise ValidationError("execution evidence does not round-trip through schema 4")
    return text


def execution_evidence_from_json(
    text: str,
) -> ExecutionEvidenceDocument | LegacyExecutionEvidenceDocument:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValidationError(f"invalid execution evidence JSON: {error}") from error
    raw = _mapping(value, context="execution evidence")
    version = _integer(
        raw.get("schema_version"), context="execution evidence.schema_version"
    )
    if version in {1, 2, 3}:
        data = _fields(
            raw,
            context="execution evidence",
            required={"schema_version", "attempts"},
        )
        if not isinstance(data["attempts"], list) or not all(
            isinstance(item, dict) for item in data["attempts"]
        ):
            raise ValidationError("legacy execution evidence attempts must be a list of objects")
        return LegacyExecutionEvidenceDocument(
            schema_version=version,
            attempts=tuple(MappingProxyType(dict(item)) for item in data["attempts"]),
        )
    if version != 4:
        raise ValidationError(
            f"execution evidence has unsupported version: {version}"
        )
    data = _fields(
        raw,
        context="execution evidence",
        required={"schema_version", "run", "attempts"},
    )
    raw_run = _fields(
        data["run"],
        context="execution evidence.run",
        required={"run_id", "work_item_id", "profile_id", "profile_revision"},
    )
    run = ExecutionEvidenceRunIdentity(
        run_id=_string(raw_run["run_id"], context="execution evidence.run.run_id"),
        work_item_id=_string(
            raw_run["work_item_id"], context="execution evidence.run.work_item_id"
        ),
        profile_id=_string(
            raw_run["profile_id"], context="execution evidence.run.profile_id"
        ),
        profile_revision=_string(
            raw_run["profile_revision"],
            context="execution evidence.run.profile_revision",
        ),
    )
    if not isinstance(data["attempts"], list):
        raise ValidationError("execution evidence.attempts must be a list")
    attempts = tuple(
        _staged_qualification_attempt(
            item,
            context=f"execution evidence.attempts[{index}]",
            run=run,
        )
        for index, item in enumerate(data["attempts"])
    )
    hop_keys = [(item.hop_id, item.hop_sequence) for item in attempts]
    invocation_ids = [item.invocation_id for item in attempts]
    if len(set(hop_keys)) != len(hop_keys):
        raise ValidationError("execution evidence has duplicate Hop rows")
    if len(set(invocation_ids)) != len(invocation_ids):
        raise ValidationError("execution evidence has duplicate invocation IDs")
    return ExecutionEvidenceDocument(4, run, attempts)


def _successor_provenance(value: Any, *, context: str) -> SuccessorProvenance | None:
    if value is None:
        return None
    data = _fields(
        value,
        context=context,
        required={
            "predecessor_run_id", "predecessor_work_item_id", "predecessor_final_head",
            "authority_decision_ref", "authority_decision_sha256",
            "continuation_descriptor_sha256", "salvage_manifest_sha256",
        },
    )
    final_head = _string(data["predecessor_final_head"], context=f"{context}.predecessor_final_head")
    if re.fullmatch(r"[0-9a-f]{40}", final_head) is None:
        raise ValidationError(f"{context}.predecessor_final_head must be a full commit SHA")
    raw_salvage = data["salvage_manifest_sha256"]
    return SuccessorProvenance(
        predecessor_run_id=_string(data["predecessor_run_id"], context=f"{context}.predecessor_run_id"),
        predecessor_work_item_id=_string(data["predecessor_work_item_id"], context=f"{context}.predecessor_work_item_id"),
        predecessor_final_head=final_head,
        authority_decision_ref=_string(data["authority_decision_ref"], context=f"{context}.authority_decision_ref"),
        authority_decision_sha256=_sha256(data["authority_decision_sha256"], context=f"{context}.authority_decision_sha256"),
        continuation_descriptor_sha256=_sha256(data["continuation_descriptor_sha256"], context=f"{context}.continuation_descriptor_sha256"),
        salvage_manifest_sha256=None if raw_salvage is None else _sha256(raw_salvage, context=f"{context}.salvage_manifest_sha256"),
    )


def _validate_v3_run(run: Run) -> None:
    """Validate the closed durable Slice-1 graph without reconstructing history."""

    hops_by_id = {hop.hop_id: hop for hop in run.hops}
    hop_order = {hop.hop_id: index for index, hop in enumerate(run.hops)}
    findings_by_id = {finding.finding_id: finding for finding in run.findings}
    for index, hop in enumerate(run.hops, start=1):
        if hop.sequence != index:
            raise ValidationError("Run v3 Hop sequence is not contiguous and ordered")
        if hop.route_reason is not None:
            prefix = "convergence_escape:"
            if not hop.route_reason.startswith(prefix):
                raise ValidationError(
                    f"Run v3 Hop {hop.hop_id} has unknown route_reason"
                )
            trigger_ids = hop.route_reason.removeprefix(prefix).split(",")
            if (
                not trigger_ids
                or trigger_ids != sorted(set(trigger_ids))
                or any(re.fullmatch(r"F[0-9]{3,}", item) is None for item in trigger_ids)
                or any(item not in findings_by_id for item in trigger_ids)
                or hop.requested_route is None
                or (
                    hop.applied_route is not None
                    and hop.requested_route == hop.applied_route
                )
            ):
                raise ValidationError(
                    f"Run v3 Hop {hop.hop_id} has malformed convergence route truth"
                )
    for finding in run.findings:
        if re.fullmatch(r"F[0-9]{3,}", finding.finding_id) is None:
            raise ValidationError(f"Run v3 has invalid concrete Finding ID: {finding.finding_id}")
        if finding.introduced_by_hop not in hops_by_id or finding.changed_by_hop not in hops_by_id:
            raise ValidationError(
                f"Run v3 Finding {finding.finding_id} references an absent Hop"
            )
        if finding.finding_id not in hops_by_id[finding.introduced_by_hop].finding_delta_ids:
            raise ValidationError(
                f"Run v3 Finding {finding.finding_id} introduction Hop lacks its delta ID"
            )
        if finding.finding_id not in hops_by_id[finding.changed_by_hop].finding_delta_ids:
            raise ValidationError(
                f"Run v3 Finding {finding.finding_id} change Hop lacks its delta ID"
            )
        if hop_order[finding.changed_by_hop] < hop_order[finding.introduced_by_hop]:
            raise ValidationError(
                f"Run v3 Finding {finding.finding_id} changed before its introduction"
            )
        obligation = finding.correction_obligation
        if finding.state is FindingState.OPEN and finding.blocking and obligation is None:
            raise ValidationError(
                f"Run v3 open blocking Finding {finding.finding_id} lacks correction_obligation"
            )
        if obligation is not None:
            if obligation.introduced_by_hop not in hops_by_id:
                raise ValidationError(
                    f"Run v3 Finding {finding.finding_id} obligation references an absent Hop"
                )
            if obligation.introduced_by_hop != finding.introduced_by_hop:
                raise ValidationError(
                    f"Run v3 Finding {finding.finding_id} obligation introduction is inconsistent"
                )
        if finding.successor_ref is not None:
            if finding.state is not FindingState.SUPERSEDED:
                raise ValidationError(
                    f"Run v3 Finding {finding.finding_id} has successor_ref without supersession"
                )
            if finding.successor_ref.startswith("local:"):
                raise ValidationError("Run v3 successor_ref must contain only a concrete Finding ID")
            successor = findings_by_id.get(finding.successor_ref)
            if successor is None:
                raise ValidationError(
                    f"Run v3 Finding {finding.finding_id} has unknown successor_ref"
                )
            if obligation is not None and successor.correction_obligation is None:
                raise ValidationError(
                    f"Run v3 tracked Finding {finding.finding_id} has an untracked successor"
                )
            if hop_order[successor.introduced_by_hop] > hop_order[finding.changed_by_hop]:
                raise ValidationError(
                    f"Run v3 Finding {finding.finding_id} successor was introduced later"
                )

    # Strategy records are durable v3 causal history, not annotations that a
    # reader may trust independently.  Reconstruct each chain before a
    # persisted accepted assessment is allowed to consume its escape (and thus
    # affect the derived generation).  Profile-specific route authority is
    # checked by reduction/replay; this wire-level check closes the causal
    # graph without needing to deserialize a profile here.
    escapes: dict[str, tuple[str, ...]] = {}
    diagnoses: dict[str, tuple[object, tuple[str, ...]]] = {}
    diagnosis_escapes: set[str] = set()
    revisions: dict[str, tuple[object, tuple[str, ...], str]] = {}
    accepted_escapes: set[str] = set()
    tracked_ids = {
        finding.finding_id
        for finding in run.findings
        if finding.correction_obligation is not None
    }
    for hop in run.hops:
        trigger_ids = (
            tuple(hop.route_reason.removeprefix("convergence_escape:").split(","))
            if hop.route_reason is not None
            and hop.route_reason.startswith("convergence_escape:")
            else None
        )
        if trigger_ids is not None:
            escapes[hop.hop_id] = trigger_ids
            if len(set(escapes) - accepted_escapes) > 1:
                raise ValidationError(
                    "Run v3 contains more than one unconsumed escape"
                )

        records = (
            hop.diagnosis,
            hop.strategy_revision,
            hop.strategy_assessment,
        )
        if sum(record is not None for record in records) > 1:
            raise ValidationError(
                f"Run v3 Hop {hop.hop_id} has multiple strategy records"
            )
        if any(record is not None for record in records) and len(
            set(escapes) - accepted_escapes
        ) != 1:
            raise ValidationError(
                f"Run v3 Hop {hop.hop_id} strategy record requires exactly one "
                "unconsumed escape"
            )
        if hop.diagnosis is not None:
            report = hop.diagnosis
            expected = escapes.get(report.escape_hop_id)
            if (
                expected is None
                or report.escape_hop_id in accepted_escapes
                or report.finding_ids != expected
                or hop_order[report.escape_hop_id] >= hop_order[hop.hop_id]
                or not set(expected).issubset(tracked_ids)
            ):
                raise ValidationError(
                    f"Run v3 Hop {hop.hop_id} diagnosis does not correlate to an active escape"
                )
            if report.escape_hop_id in diagnosis_escapes:
                raise ValidationError(
                    f"Run v3 escape {report.escape_hop_id} has more than one diagnosis"
                )
            diagnosis_escapes.add(report.escape_hop_id)
            diagnoses[hop.hop_id] = (report, expected)
            continue
        if hop.strategy_revision is not None:
            revision = hop.strategy_revision
            diagnosis_entry = diagnoses.get(revision.diagnosis_hop_id)
            expected = escapes.get(revision.escape_hop_id)
            if (
                expected is None
                or revision.escape_hop_id in accepted_escapes
                or revision.finding_ids != expected
                or diagnosis_entry is None
                or hop_order[revision.diagnosis_hop_id] >= hop_order[hop.hop_id]
                or diagnosis_entry[0].escape_hop_id != revision.escape_hop_id
                or diagnosis_entry[1] != expected
                or run.hops[hop_order[revision.diagnosis_hop_id]].outcome
                != Outcome.COMPLETED.value
                or not set(expected).issubset(tracked_ids)
            ):
                raise ValidationError(
                    f"Run v3 Hop {hop.hop_id} revision does not correlate to a completed diagnosis"
                )
            revisions[hop.hop_id] = (revision, expected, revision.diagnosis_hop_id)
            continue
        if hop.strategy_assessment is not None:
            assessment = hop.strategy_assessment
            diagnosis_entry = diagnoses.get(assessment.diagnosis_hop_id)
            revision_entry = revisions.get(assessment.revision_hop_id)
            expected = escapes.get(assessment.escape_hop_id)
            latest_revision = next(
                (
                    prior.hop_id
                    for prior in reversed(run.hops[:hop_order[hop.hop_id]])
                    if prior.strategy_revision is not None
                    and prior.strategy_revision.escape_hop_id == assessment.escape_hop_id
                    and prior.strategy_revision.diagnosis_hop_id
                    == assessment.diagnosis_hop_id
                ),
                None,
            )
            if (
                expected is None
                or assessment.escape_hop_id in accepted_escapes
                or assessment.finding_ids != expected
                or diagnosis_entry is None
                or revision_entry is None
                or latest_revision != assessment.revision_hop_id
                or hop_order[assessment.diagnosis_hop_id] >= hop_order[hop.hop_id]
                or hop_order[assessment.revision_hop_id] >= hop_order[hop.hop_id]
                or diagnosis_entry[0].escape_hop_id != assessment.escape_hop_id
                or diagnosis_entry[1] != expected
                or revision_entry[0].escape_hop_id != assessment.escape_hop_id
                or revision_entry[1] != expected
                or revision_entry[2] != assessment.diagnosis_hop_id
                or run.hops[hop_order[assessment.diagnosis_hop_id]].outcome
                != Outcome.COMPLETED.value
                or run.hops[hop_order[assessment.revision_hop_id]].outcome
                != Outcome.COMPLETED.value
                or not set(expected).issubset(tracked_ids)
            ):
                raise ValidationError(
                    f"Run v3 Hop {hop.hop_id} strategy assessment does not correlate to the current chain"
                )
            if assessment.result is StrategyAssessmentResult.ACCEPTED:
                accepted_escapes.add(assessment.escape_hop_id)

    assessed_correction_hops: set[str] = set()
    for hop in run.hops:
        unknown_delta_ids = sorted(set(hop.finding_delta_ids) - set(findings_by_id))
        if unknown_delta_ids:
            raise ValidationError(
                f"Run v3 Hop {hop.hop_id} references unknown Finding delta IDs"
            )
        claim_ids = [claim.finding_id for claim in hop.coverage_claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValidationError(f"Run v3 Hop {hop.hop_id} has duplicate coverage claims")
        for claim in hop.coverage_claims:
            finding = findings_by_id.get(claim.finding_id)
            if finding is None or finding.correction_obligation is None:
                raise ValidationError(
                    f"Run v3 Hop {hop.hop_id} coverage references unknown or untracked Finding"
                )
            if hop_order[finding.introduced_by_hop] >= hop_order[hop.hop_id]:
                raise ValidationError(
                    f"Run v3 Hop {hop.hop_id} coverage does not reference a prior Finding"
                )
        assessment_ids = [item.finding_id for item in hop.convergence_assessments]
        if len(set(assessment_ids)) != len(assessment_ids):
            raise ValidationError(
                f"Run v3 Hop {hop.hop_id} has duplicate convergence assessments"
            )
        correction_ids = {item.correction_hop_id for item in hop.convergence_assessments}
        if len(correction_ids) > 1:
            raise ValidationError(
                f"Run v3 Hop {hop.hop_id} assessments name multiple correction Hops"
            )
        if correction_ids:
            correction_hop_id = next(iter(correction_ids))
            if correction_hop_id in assessed_correction_hops:
                raise ValidationError(
                    f"Run v3 correction Hop {correction_hop_id} is assessed more than once"
                )
            assessed_correction_hops.add(correction_hop_id)
            correction_hop = hops_by_id.get(correction_hop_id)
            if (
                correction_hop is None
                or hop_order[correction_hop_id] >= hop_order[hop.hop_id]
            ):
                raise ValidationError(
                    f"Run v3 Hop {hop.hop_id} references an absent or non-prior correction Hop"
                )
            expected_ids = {claim.finding_id for claim in correction_hop.coverage_claims}
            if set(assessment_ids) != expected_ids:
                raise ValidationError(
                    f"Run v3 Hop {hop.hop_id} assessments are not total for correction Hop"
                )
        for assessment in hop.convergence_assessments:
            finding = findings_by_id.get(assessment.finding_id)
            if finding is None or finding.correction_obligation is None:
                raise ValidationError(
                    f"Run v3 Hop {hop.hop_id} assessment references unknown or untracked Finding"
                )
            if hop_order[finding.introduced_by_hop] >= hop_order[assessment.correction_hop_id]:
                raise ValidationError(
                    f"Run v3 Hop {hop.hop_id} assessment Finding was not projected into correction"
                )

    edges = {
        finding.finding_id: finding.successor_ref
        for finding in run.findings
        if finding.successor_ref is not None
    }
    for start in edges:
        seen: set[str] = set()
        cursor: str | None = start
        while cursor in edges:
            if cursor in seen:
                raise ValidationError("Run v3 Finding supersession graph contains a cycle")
            seen.add(cursor)
            cursor = edges[cursor]
def run_from_json(text: str) -> Run:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValidationError(f"invalid Run JSON: {error}") from error
    raw = _mapping(value, context="Run")
    version = _integer(raw.get("schema_version"), context="Run.schema_version")
    v2_fields = {field.name for field in fields(Run)}
    v1_fields = v2_fields - {"semantic_stop", "successor_provenance"}
    if version == 1:
        data = _fields(raw, context="Run", required=v1_fields)
    elif version in {2, 3}:
        data = _fields(raw, context="Run", required=v2_fields)
    else:
        raise ValidationError(f"Run.schema_version has unsupported version: {version}")
    raw_hops = data["hops"]
    raw_findings = data["findings"]
    if not isinstance(raw_hops, list) or not isinstance(raw_findings, list):
        raise ValidationError("Run.hops and Run.findings must be lists")
    run = Run(
        schema_version=version,
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
            _hop_summary(item, context=f"Run.hops[{index}]", run_version=version)
            for index, item in enumerate(raw_hops)
        ),
        findings=tuple(
            _finding(item, context=f"Run.findings[{index}]", run_version=version)
            for index, item in enumerate(raw_findings)
        ),
        child_run_id=_optional_string(data["child_run_id"], context="Run.child_run_id"),
        suspension_head=_optional_string(data["suspension_head"], context="Run.suspension_head"),
        reconcile_reason=_optional_string(data["reconcile_reason"], context="Run.reconcile_reason"),
        reconcile_anchor=_optional_string(data["reconcile_anchor"], context="Run.reconcile_anchor"),
        stop_reason=_optional_string(data["stop_reason"], context="Run.stop_reason"),
        human_question=_optional_string(data["human_question"], context="Run.human_question"),
        anomalies=_strings(data["anomalies"], context="Run.anomalies"),
        semantic_stop=(
            None if version == 1 else _semantic_stop(data["semantic_stop"], context="Run.semantic_stop")
        ),
        successor_provenance=(
            None if version == 1 else _successor_provenance(
                data["successor_provenance"], context="Run.successor_provenance"
            )
        ),
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
    if run.semantic_stop is not None and run.semantic_stop.source_hop_id not in {
        hop.hop_id for hop in run.hops
    }:
        raise ValidationError("Run.semantic_stop.source_hop_id is absent from Run.hops")
    if version == 3:
        _validate_v3_run(run)
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
        pre_route_anchor_sha256=(
            None
            if data["pre_route_anchor_sha256"] is None
            else _sha256(
                data["pre_route_anchor_sha256"],
                context="dispatch marker.pre_route_anchor_sha256",
            )
        ),
    )


def load_run(path: Path) -> Run:
    return run_from_json(path.read_text(encoding="utf-8"))
