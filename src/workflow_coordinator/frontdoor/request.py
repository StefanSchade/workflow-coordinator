"""Bounded consumer-neutral Commission request parsing (BP2b).

A ``ConsumerRequest`` is the task-shaped half of a BP2b compile input.  It
deliberately carries no execution binding: no adapter, model, reasoning
effort, or provider key is a legal field anywhere in this grammar.  Execution
choices are resolved separately by ``frontdoor.config.resolve_execution``.
``evidence`` is an *ordered* list of named commands -- the sequence the
Coordinator runs, in that order and fail-fast, as the pre-launch baseline
probe and after every gated Hop (FTR-WFC-EVIDENCE-COMMAND-SEQUENCE).
The closed-schema parsing helpers reused from ``commissioning`` reject any
unexpected field, which is what actually refuses a forged binding key -- this
module does not additionally deny-list field names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from ..commissioning import CommissionError, TaskBinding, _SHA, _mapping, _path, _text, _within, _yaml_load
from ..model import COORDINATOR_HARD_MAX_EVIDENCE_TIMEOUT_SECONDS

_SUPPORTED_PROGRAMS = {("simple", "v1"), ("robust", "v1")}
_REQUIRED_PERSONAS = {
    ("simple", "v1"): {"worker"},
    ("robust", "v1"): {"worker", "independent_reviewer"},
}
_REQUIRED_ARTIFACTS = {
    ("simple", "v1"): frozenset(),
    ("robust", "v1"): frozenset({"broad_review", "focused_review"}),
}


@dataclass(frozen=True)
class ProgramRef:
    id: str
    revision: str


# Deliberately the same shape ``policy.evidence_commands`` enforces
# (``serialization._evidence_commands``): a request must never be able to
# compile into a policy its own loader would refuse.
_EVIDENCE_COMMAND_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")
DEGENERATE_EVIDENCE_COMMAND_NAME = "task-check"


@dataclass(frozen=True)
class EvidenceCommandRequest:
    """One named gate of the ordered evidence sequence (O7, decision D6)."""

    name: str
    argv: tuple[str, ...]
    timeout_seconds: int


@dataclass(frozen=True)
class GuidanceRef:
    """A consumer-owned repository_policy source, pinned like any other input."""

    path: str
    source_commit: str


@dataclass(frozen=True)
class ConsumerRequest:
    schema_version: int
    commission_id: str
    work_item_id: str
    authority_source: str
    program: ProgramRef
    task: TaskBinding
    scope: tuple[str, ...]
    personas: Mapping[str, str]
    evidence: tuple[EvidenceCommandRequest, ...]
    max_autonomous_hops: int
    durable_artifacts: Mapping[str, tuple[str, ...]]
    guidance: GuidanceRef
    raw_text: str


def _evidence_command_request(value: Any, *, context: str, name: str | None) -> EvidenceCommandRequest:
    """Parse one gate. ``name`` is None for a list item, which names itself."""

    required = {"argv", "timeout_seconds"} if name is not None else {"name", "argv", "timeout_seconds"}
    data = _mapping(value, context=context, required=required)
    if name is None:
        name = _text(data["name"], context=f"{context}.name")
        if _EVIDENCE_COMMAND_NAME.fullmatch(name) is None:
            raise CommissionError(
                f"{context}.name must match [a-z][a-z0-9_-]{{0,63}}: {name!r}"
            )
    raw_argv = data["argv"]
    if not isinstance(raw_argv, list) or not raw_argv:
        raise CommissionError(f"{context}.argv must be a non-empty list")
    argv: list[str] = []
    for index, item in enumerate(raw_argv):
        if not isinstance(item, str) or "\0" in item:
            raise CommissionError(f"{context}.argv[{index}] must be a string without NUL")
        if index == 0 and not item:
            raise CommissionError(f"{context}.argv[0] must be non-empty")
        argv.append(item)
    timeout = data["timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= COORDINATOR_HARD_MAX_EVIDENCE_TIMEOUT_SECONDS:
        raise CommissionError(
            f"{context}.timeout_seconds must be a non-boolean integer in 1..{COORDINATOR_HARD_MAX_EVIDENCE_TIMEOUT_SECONDS}"
        )
    return EvidenceCommandRequest(name, tuple(argv), timeout)


def _evidence_sequence(value: Any, *, context: str) -> tuple[EvidenceCommandRequest, ...]:
    """Parse the ordered, non-empty evidence sequence in either admitted form.

    A YAML *list* declares the ordered named gates; a single YAML *mapping*
    is the degenerate list of exactly one gate named ``task-check`` -- the
    pre-O7 form, which stays admitted unchanged (FTR-WFC-EVIDENCE-COMMAND-
    SEQUENCE, Design decision (c)). The two are distinguished by type, never
    by a flag, so no request can declare both.
    """

    if isinstance(value, dict):
        if "name" in value:
            # Refuse by name rather than through the closed schema's generic
            # "forbidden field" message: naming a command is exactly what the
            # list form is for, and the author is one bracket away from it.
            raise CommissionError(
                f"{context} names a command, so it must be a list of commands, not one mapping"
            )
        return (
            _evidence_command_request(
                value, context=context, name=DEGENERATE_EVIDENCE_COMMAND_NAME
            ),
        )
    if not isinstance(value, list) or not value:
        raise CommissionError(
            f"{context} must be a non-empty list of named commands, or one "
            "{argv, timeout_seconds} mapping as the degenerate single-command form"
        )
    commands = tuple(
        _evidence_command_request(item, context=f"{context}[{index}]", name=None)
        for index, item in enumerate(value)
    )
    names = [command.name for command in commands]
    if len(set(names)) != len(names):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise CommissionError(f"{context} command names must be unique: {', '.join(duplicates)}")
    return commands


def _guidance_ref(value: Any, *, context: str) -> GuidanceRef:
    data = _mapping(value, context=context, required={"path", "source_commit"})
    source_commit = _text(data["source_commit"], context=f"{context}.source_commit")
    if not _SHA.fullmatch(source_commit):
        raise CommissionError(f"{context}.source_commit must be a full lowercase 40-character SHA")
    return GuidanceRef(_path(data["path"], context=f"{context}.path"), source_commit)


def _program_ref(value: Any, *, context: str) -> ProgramRef:
    data = _mapping(value, context=context, required={"id", "revision"})
    program = ProgramRef(_text(data["id"], context=f"{context}.id"), _text(data["revision"], context=f"{context}.revision"))
    if (program.id, program.revision) not in _SUPPORTED_PROGRAMS:
        raise CommissionError("only the closed simple@v1 and robust@v1 programs are supported by the consumer compiler")
    return program


def consumer_request_from_yaml(text: str) -> ConsumerRequest:
    """Parse exactly the deliberately small consumer-request vocabulary."""

    raw = _yaml_load(text, context="Consumer Request")
    data = _mapping(
        raw,
        context="Consumer Request",
        required={
            "schema_version", "commission_id", "work_item_id", "authority_source", "program", "task",
            "scope", "personas", "evidence", "max_autonomous_hops", "durable_artifacts", "guidance",
        },
    )
    if data["schema_version"] != 1:
        raise CommissionError("Consumer Request.schema_version must be 1")
    program = _program_ref(data["program"], context="Consumer Request.program")
    task_data = _mapping(
        data["task"], context="Consumer Request.task",
        required={"title", "body", "acceptance", "withheld_authority", "escalation_default"},
    )
    task = TaskBinding(**{key: _text(value, context=f"Consumer Request.task.{key}") for key, value in task_data.items()})
    if not isinstance(data["scope"], list) or not data["scope"]:
        raise CommissionError("Consumer Request.scope must be a non-empty list")
    scope = tuple(_path(value, context="Consumer Request.scope") for value in data["scope"])
    if len(set(scope)) != len(scope):
        raise CommissionError("Consumer Request.scope must not contain duplicates")
    required_personas = _REQUIRED_PERSONAS[(program.id, program.revision)]
    personas_data = _mapping(data["personas"], context="Consumer Request.personas", required=required_personas)
    personas = {role: _text(value, context=f"Consumer Request.personas.{role}") for role, value in personas_data.items()}
    hops = data["max_autonomous_hops"]
    if not isinstance(hops, int) or isinstance(hops, bool) or hops <= 0:
        raise CommissionError("Consumer Request.max_autonomous_hops must be a positive integer")
    artifacts_raw = data["durable_artifacts"]
    if not isinstance(artifacts_raw, dict) or any(not isinstance(key, str) for key in artifacts_raw):
        raise CommissionError("Consumer Request.durable_artifacts must be a string-keyed mapping")
    artifacts: dict[str, tuple[str, ...]] = {}
    for phase, values in artifacts_raw.items():
        if not isinstance(values, list) or not values:
            raise CommissionError(f"Consumer Request.durable_artifacts.{phase} must be a non-empty list")
        paths = tuple(_path(value, context=f"Consumer Request.durable_artifacts.{phase}") for value in values)
        if len(set(paths)) != len(paths):
            raise CommissionError(f"Consumer Request.durable_artifacts.{phase} contains duplicates")
        artifacts[phase] = paths
    required_artifacts = _REQUIRED_ARTIFACTS[(program.id, program.revision)]
    if set(artifacts) != required_artifacts:
        if not required_artifacts:
            raise CommissionError(f"{program.id}@{program.revision} contains forbidden durable-artifact mappings")
        raise CommissionError(
            f"{program.id}@{program.revision} requires exactly durable-artifact mappings: "
            + ", ".join(sorted(required_artifacts))
        )
    if any(not _within(path, scope) for paths in artifacts.values() for path in paths):
        raise CommissionError("Consumer Request durable-artifact paths must be contained in Consumer Request scope")
    return ConsumerRequest(
        schema_version=1,
        commission_id=_text(data["commission_id"], context="Consumer Request.commission_id"),
        work_item_id=_text(data["work_item_id"], context="Consumer Request.work_item_id"),
        authority_source=_text(data["authority_source"], context="Consumer Request.authority_source"),
        program=program,
        task=task,
        scope=scope,
        personas=personas,
        evidence=_evidence_sequence(data["evidence"], context="Consumer Request.evidence"),
        max_autonomous_hops=hops,
        durable_artifacts=artifacts,
        guidance=_guidance_ref(data["guidance"], context="Consumer Request.guidance"),
        raw_text=text,
    )
