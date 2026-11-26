"""Bounded consumer repository configuration and execution resolution (BP2b).

``ConsumerConfig`` is the durable, per-repository half of a BP2b compile
input (governance target, scope ceiling, hop ceiling, and default execution
bindings). ``resolve_execution`` combines the config and per-role CLI
overrides against the executing phases of the chosen program, producing one
``RunExecutionConfiguration`` with its own stable digest -- entirely outside
the Commission's own immutable bytes, per C1.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..commissioning import CommissionError, _mapping, _path, _text, _yaml_load
from ..model import RunExecutionConfiguration, WorkflowRole
from ..serialization import ValidationError, execution_binding_value, run_execution_configuration_from_yaml

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
    phase_execution: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    boundary: str = "direct"


@dataclass(frozen=True)
class ExecutionEnvelope:
    """An execution binding set with an identity independent of the Commission."""

    configuration: RunExecutionConfiguration
    text: str
    digest: str


def _single_line_value(value: Any, *, context: str) -> str:
    text = _text(value, context=context)
    if text.splitlines() != [text]:
        raise CommissionError(f"{context} must be a single-line value")
    return text


def _evidence_config_value(value: Any, *, context: str) -> EvidenceConfigValue:
    data = _mapping(value, context=context, required=set(), optional={"repository", "raw"})
    repository = None
    if "repository" in data:
        repository = _single_line_value(data["repository"], context=f"{context}.repository")
    raw = _DEFAULT_EVIDENCE_RAW
    if "raw" in data:
        raw_value = data["raw"]
        if raw_value not in EVIDENCE_RAW_CHOICES:
            raise CommissionError(
                f"{context}.raw must be one of: {', '.join(EVIDENCE_RAW_CHOICES)}"
            )
        raw = raw_value
    return EvidenceConfigValue(repository=repository, raw=raw)


_EXECUTION_FIELDS = {"adapter", "model", "reasoning"}


def execution_overrides_from_flags(values: list[str]) -> dict[str, dict[str, str]]:
    """Parse the role override flags shared by preview and authorization."""

    overrides: dict[str, dict[str, str]] = {}
    for item in values:
        dotted_key, equals, value = item.partition("=")
        if not equals:
            raise CommissionError(f"--execution-override must be ROLE.FIELD=VALUE, got: {item}")
        role, _, field = dotted_key.partition(".")
        if not role or field not in _EXECUTION_FIELDS:
            raise CommissionError(
                f"--execution-override must be ROLE.FIELD=VALUE with FIELD one of "
                f"adapter/model/reasoning, got: {item}"
            )
        overrides.setdefault(role, {})[field] = value
    return overrides


def _execution_fields(value: Any, *, context: str, partial: bool) -> dict[str, str]:
    data = _mapping(
        value, context=context,
        required=set() if partial else _EXECUTION_FIELDS, optional=_EXECUTION_FIELDS,
    )
    fields = {}
    for key, value in sorted(data.items()):
        try:
            selected = execution_binding_value(value, context=f"{context}.{key}")
        except ValidationError as error:
            raise CommissionError(str(error)) from error
        if key == "adapter" and selected not in _ADAPTERS:
            raise CommissionError(f"{context}.adapter must be one of: {', '.join(sorted(_ADAPTERS))}")
        fields[key] = selected
    return fields


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
        optional={"evidence", "phase_execution", "boundary"},
    )
    if data["schema_version"] != 1:
        raise CommissionError("Consumer Config.schema_version must be 1")
    boundary = data.get("boundary", "direct")
    if boundary not in {"direct", "bubblewrap"}:
        raise CommissionError("Consumer Config.boundary must be direct or bubblewrap")
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
        default_execution[role_name] = ExecutionBindingValue(
            **_execution_fields(binding_value, context=f"Consumer Config.default_execution.{role_name}", partial=False)
        )
    phase_execution_raw = data.get("phase_execution", {})
    if not isinstance(phase_execution_raw, dict) or not all(isinstance(key, str) for key in phase_execution_raw):
        raise CommissionError("Consumer Config.phase_execution must be a mapping with string keys")
    phase_execution: dict[str, dict[str, str]] = {}
    for key, binding_value in sorted(phase_execution_raw.items()):
        phase = _single_line_value(key, context="Consumer Config.phase_execution phase key")
        context = f"Consumer Config.phase_execution.{phase}"
        fields = _execution_fields(binding_value, context=context, partial=True)
        if not fields:
            raise CommissionError(f"{context} must specify at least one field")
        phase_execution[phase] = fields
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
        phase_execution=phase_execution,
        boundary=boundary,
    )


def resolve_execution(
    config: ConsumerConfig,
    overrides: Mapping[str, Mapping[str, str]],
    phase_roles: Mapping[str, WorkflowRole],
) -> ExecutionEnvelope:
    """Resolve role defaults, role CLI overrides, then phase overrides."""

    required_roles = {role.value for role in phase_roles.values()}
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
    unknown_phases = sorted(set(config.phase_execution) - set(phase_roles))
    if unknown_phases:
        raise CommissionError(
            "phase_execution names unknown or nonexecuting phases in the selected program: "
            + ", ".join(unknown_phases)
        )
    role_defaults: dict[str, dict[str, str]] = {}
    for role in sorted(required_roles):
        base = config.default_execution.get(role)
        role_defaults[role] = (asdict(base) if base is not None else {}) | _execution_fields(
            dict(overrides.get(role, {})), context=f"execution override for role {role}", partial=True,
        )

    if config.phase_execution:
        version, key, kind = 2, "phase_bindings", "phase"
        resolved = {
            phase: role_defaults[role.value] | config.phase_execution.get(phase, {})
            for phase, role in phase_roles.items()
        }
    else:
        version, key, kind = 1, "bindings", "role"
        resolved = role_defaults

    bindings = {}
    for name, fields in sorted(resolved.items()):
        missing = sorted(_EXECUTION_FIELDS - fields.keys())
        if missing:
            raise CommissionError(
                f"execution binding for {kind} {name} is missing: " + ", ".join(missing)
            )
        bindings[name] = {field: fields[field] for field in ("adapter", "model", "reasoning")}
    if len({binding["adapter"] for binding in bindings.values()}) != 1:
        raise CommissionError("mixed execution adapters are incompatible with the single scalar executable")
    envelope: dict[str, object] = {"schema_version": version, key: bindings}
    if config.boundary != "direct":
        envelope["boundary"] = config.boundary
    text = yaml.safe_dump(envelope, sort_keys=False, allow_unicode=True)
    configuration = run_execution_configuration_from_yaml(text)
    return ExecutionEnvelope(configuration=configuration, text=text, digest=configuration.sha256)


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
