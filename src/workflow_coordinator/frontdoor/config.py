"""Bounded consumer repository configuration and execution resolution (BP2b).

``ConsumerConfig`` is the durable, per-repository half of a BP2b compile
input (governance target, scope ceiling, hop ceiling, and default execution
bindings).  ``resolve_execution`` turns a config, an explicit per-role
override mapping, and the role set a chosen program actually uses into one
``RunExecutionConfiguration`` with its own stable digest -- entirely outside
the Commission's own immutable bytes, per C1.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

import yaml

from ..commissioning import CommissionError, _mapping, _path, _text, _yaml_load
from ..model import ExecutionBinding, RunExecutionConfiguration, WorkflowRole

_ADAPTERS = {"codex_cli", "claude_cli"}


@dataclass(frozen=True)
class ExecutionBindingValue:
    adapter: str
    model: str
    reasoning: str


@dataclass(frozen=True)
class ConsumerConfig:
    schema_version: int
    repository_name: str
    governance_dir: str
    work_item_scope_ceiling: tuple[str, ...]
    max_autonomous_hops: int
    default_execution: Mapping[str, ExecutionBindingValue]
    raw_text: str


@dataclass(frozen=True)
class ExecutionEnvelope:
    """An execution binding set with an identity independent of the Commission."""

    configuration: RunExecutionConfiguration
    text: str
    digest: str


def _execution_value(value: Any, *, context: str) -> str:
    text = _text(value, context=context)
    if text.splitlines() != [text]:
        raise CommissionError(f"{context} must be a single-line value")
    return text


def _execution_binding_value(value: Any, *, context: str) -> ExecutionBindingValue:
    data = _mapping(value, context=context, required={"adapter", "model", "reasoning"})
    adapter = _text(data["adapter"], context=f"{context}.adapter")
    if adapter not in _ADAPTERS:
        raise CommissionError(f"{context}.adapter must be one of: {', '.join(sorted(_ADAPTERS))}")
    return ExecutionBindingValue(
        adapter=adapter,
        model=_execution_value(data["model"], context=f"{context}.model"),
        reasoning=_execution_value(data["reasoning"], context=f"{context}.reasoning"),
    )


def consumer_config_from_yaml(text: str) -> ConsumerConfig:
    """Parse the deliberately small per-repository consumer-config vocabulary."""

    raw = _yaml_load(text, context="Consumer Config")
    data = _mapping(
        raw,
        context="Consumer Config",
        required={
            "schema_version", "repository_name", "governance_dir", "work_item_scope_ceiling",
            "max_autonomous_hops", "default_execution",
        },
    )
    if data["schema_version"] != 1:
        raise CommissionError("Consumer Config.schema_version must be 1")
    governance_dir = _path(data["governance_dir"], context="Consumer Config.governance_dir")
    if not isinstance(data["work_item_scope_ceiling"], list) or not data["work_item_scope_ceiling"]:
        raise CommissionError("Consumer Config.work_item_scope_ceiling must be a non-empty list")
    ceiling = tuple(_path(value, context="Consumer Config.work_item_scope_ceiling") for value in data["work_item_scope_ceiling"])
    if len(set(ceiling)) != len(ceiling):
        raise CommissionError("Consumer Config.work_item_scope_ceiling must not contain duplicates")
    hops = data["max_autonomous_hops"]
    if not isinstance(hops, int) or isinstance(hops, bool) or hops <= 0:
        raise CommissionError("Consumer Config.max_autonomous_hops must be a positive integer")
    default_execution_raw = data["default_execution"]
    if not isinstance(default_execution_raw, dict):
        raise CommissionError("Consumer Config.default_execution must be a mapping")
    default_execution: dict[str, ExecutionBindingValue] = {}
    for role_name, binding_value in default_execution_raw.items():
        if not isinstance(role_name, str):
            raise CommissionError("Consumer Config.default_execution keys must be strings")
        try:
            WorkflowRole(role_name)
        except ValueError as error:
            raise CommissionError(f"Consumer Config.default_execution names an unknown role: {role_name}") from error
        default_execution[role_name] = _execution_binding_value(
            binding_value, context=f"Consumer Config.default_execution.{role_name}"
        )
    return ConsumerConfig(
        schema_version=1,
        repository_name=_text(data["repository_name"], context="Consumer Config.repository_name"),
        governance_dir=governance_dir,
        work_item_scope_ceiling=ceiling,
        max_autonomous_hops=hops,
        default_execution=default_execution,
        raw_text=text,
    )


def _execution_yaml(bindings: Mapping[str, ExecutionBindingValue]) -> str:
    payload = {
        "schema_version": 1,
        "bindings": {
            role: {"adapter": binding.adapter, "model": binding.model, "reasoning": binding.reasoning}
            for role, binding in sorted(bindings.items())
        },
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def resolve_execution(
    config: ConsumerConfig,
    overrides: Mapping[str, Mapping[str, str]],
    roles: tuple[str, ...],
) -> ExecutionEnvelope:
    """Resolve one explicit, authority-neutral execution envelope for ``roles``.

    ``overrides`` wins field-by-field over ``config.default_execution`` for the
    same role; every field named by either source for an unselected role is a
    refusal, never a silent ignore -- execution scope cannot be widened past
    what the chosen program actually uses.
    """

    required_roles = set(roles)
    unknown_roles = sorted(set(overrides) - required_roles)
    if unknown_roles:
        raise CommissionError(
            "execution overrides name roles the selected program does not use: " + ", ".join(unknown_roles)
        )
    unknown_default_roles = sorted(set(config.default_execution) - required_roles)
    if unknown_default_roles:
        raise CommissionError(
            "execution config defaults name roles the selected program does not use: "
            + ", ".join(unknown_default_roles)
        )
    bindings: dict[str, ExecutionBindingValue] = {}
    for role in required_roles:
        base = config.default_execution.get(role)
        override = overrides.get(role)
        merged = {
            "adapter": (base.adapter if base else None),
            "model": (base.model if base else None),
            "reasoning": (base.reasoning if base else None),
        }
        if override is not None:
            for key in ("adapter", "model", "reasoning"):
                if key in override:
                    merged[key] = override[key]
        missing = sorted(key for key, value in merged.items() if value is None)
        if missing:
            raise CommissionError(
                f"execution binding for role {role} is missing: " + ", ".join(missing)
            )
        bindings[role] = _execution_binding_value(merged, context=f"resolved execution binding for role {role}")
    text = _execution_yaml(bindings)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    configuration = RunExecutionConfiguration(
        schema_version=1,
        bindings={
            WorkflowRole(role): ExecutionBinding(adapter=binding.adapter, model=binding.model, reasoning=binding.reasoning)
            for role, binding in bindings.items()
        },
    )
    return ExecutionEnvelope(configuration=configuration, text=text, digest=digest)
