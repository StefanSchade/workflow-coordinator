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
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..commissioning import CommissionError, _mapping, _path, _text, _yaml_load
from ..model import ExecutionBinding, RunExecutionConfiguration, WorkflowRole

_ADAPTERS = {"codex_cli", "claude_cli"}

EVIDENCE_RAW_CHOICES = ("local", "commit", "discard")
_DEFAULT_EVIDENCE_RAW = "local"

# The managed block ``wfc init`` writes into a new Evidence repository's root
# ``.gitignore``. Content-style globs (``**`` suffix, not a trailing-slash
# directory match) so a per-Attempt ``commit``-mode archive can still
# re-include its own raw files with a nested ``!`` negation -- a
# directory-match pattern (``archive/**/run/``) would make Git refuse to
# traverse into ``run/`` at all, and no later rule could ever re-include a
# file beneath it.
EVIDENCE_GITIGNORE_MARKER_BEGIN = "# >>> wfc managed evidence raw-retention block (do not edit by hand) >>>"
EVIDENCE_GITIGNORE_MARKER_END = "# <<< wfc managed evidence raw-retention block <<<"
EVIDENCE_GITIGNORE_PATTERNS = ("archive/**/manifest.json", "archive/**/run/**", "archive/**/ops/**")


def evidence_gitignore_block() -> str:
    """The exact managed block text ``wfc init`` writes into a fresh Evidence repository."""

    lines = (EVIDENCE_GITIGNORE_MARKER_BEGIN, *EVIDENCE_GITIGNORE_PATTERNS, EVIDENCE_GITIGNORE_MARKER_END)
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ExecutionBindingValue:
    adapter: str
    model: str
    reasoning: str


@dataclass(frozen=True)
class EvidenceConfigValue:
    """The parsed, optional ``evidence`` block of a Consumer Config.

    ``repository`` is ``None`` when the author omitted the key (the default
    location applies); when present it is stored exactly as authored --
    resolution against the payload repository root happens in
    ``resolve_evidence``, never here.
    """

    repository: str | None
    raw: str


@dataclass(frozen=True)
class EvidenceSettings:
    """The resolved Evidence destination and raw-retention mode for one repository."""

    path: Path
    raw: str


@dataclass(frozen=True)
class ConsumerConfig:
    schema_version: int
    repository_name: str
    governance_dir: str
    work_item_scope_ceiling: tuple[str, ...]
    max_autonomous_hops: int
    default_execution: Mapping[str, ExecutionBindingValue]
    raw_text: str
    evidence: EvidenceConfigValue | None = None


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


def _evidence_config_value(value: Any, *, context: str) -> EvidenceConfigValue:
    data = _mapping(value, context=context, required=set(), optional={"repository", "raw"})
    repository = None
    if "repository" in data:
        repository = _execution_value(data["repository"], context=f"{context}.repository")
    raw = _DEFAULT_EVIDENCE_RAW
    if "raw" in data:
        raw_value = data["raw"]
        if raw_value not in EVIDENCE_RAW_CHOICES:
            raise CommissionError(
                f"{context}.raw must be one of: {', '.join(EVIDENCE_RAW_CHOICES)}"
            )
        raw = raw_value
    return EvidenceConfigValue(repository=repository, raw=raw)


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
        optional={"evidence"},
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
    evidence = None
    if "evidence" in data:
        evidence = _evidence_config_value(data["evidence"], context="Consumer Config.evidence")
    return ConsumerConfig(
        schema_version=1,
        repository_name=_text(data["repository_name"], context="Consumer Config.repository_name"),
        governance_dir=governance_dir,
        work_item_scope_ceiling=ceiling,
        max_autonomous_hops=hops,
        default_execution=default_execution,
        raw_text=text,
        evidence=evidence,
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


def resolve_evidence(
    config: ConsumerConfig,
    repository_root: Path | str,
    environ: Mapping[str, str] | None = None,
) -> EvidenceSettings:
    """Resolve the Evidence destination and raw-retention mode for ``config``.

    Absent an ``evidence`` block, the default applies: raw retention
    ``local`` and a repository at ``<data home>/wfc/evidence/<repository_name>``,
    where data home is ``$XDG_DATA_HOME`` falling back to ``~/.local/share``.
    An explicit ``repository`` is resolved against ``repository_root`` when
    relative (including one that escapes it with ``..``, as self-development
    does); an absolute ``repository`` is used as given.
    """

    env = os.environ if environ is None else environ
    repository_root = Path(repository_root)
    evidence = config.evidence
    raw = evidence.raw if evidence is not None else _DEFAULT_EVIDENCE_RAW
    repository_setting = evidence.repository if evidence is not None else None

    if repository_setting is not None:
        candidate = Path(repository_setting)
        path = candidate if candidate.is_absolute() else Path(os.path.normpath(repository_root / candidate))
    else:
        data_home = env.get("XDG_DATA_HOME")
        if data_home:
            base = Path(data_home)
        else:
            home = env.get("HOME")
            if not home:
                raise CommissionError("cannot resolve the default Evidence location: HOME is not set")
            base = Path(home) / ".local" / "share"
        path = base / "wfc" / "evidence" / config.repository_name
    return EvidenceSettings(path=path, raw=raw)
