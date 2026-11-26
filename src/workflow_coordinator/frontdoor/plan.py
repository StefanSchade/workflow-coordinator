"""Installed ``wfc plan`` -- a non-executing preview of a bounded task.

``wfc plan`` never runs a project command, provider, or Git mutation: it
only parses a task document and CLI-supplied fields into the exact BP2b
``ConsumerRequest``/``ConsumerConfig`` shapes, then calls the accepted
``commissioning.compile_commission`` and ``frontdoor.config.resolve_execution``
to preview the Commission bytes and the separately resolved execution
envelope that would later be compiled/resolved for real. Both of those
calls are themselves read-only (no file is written below the repository);
this module adds no further mutation on top of them. It never calls
``frontdoor.verify.verify_commission`` or ``operator.admit_parsed`` -- those
belong to actual admission, not to a preview. There is no durable binding
left behind by this preview: any temporary artifact BP2b's own verification
path once returned no longer exists once that call returns, and nothing
here should be treated as live launch provenance.

``wfc request`` (``frontdoor/emit_request.py``, D1 Option B) shares every
argument definition and the request-construction/compile path with ``wfc
plan`` through ``add_plan_input_arguments`` and ``resolve_plan_inputs``
below -- it adds only an explicit ``--output`` and overwrite/location
refusals on top. Nothing here is written to by that command: ``wfc plan``
itself still performs no write of any kind.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..commissioning import CommissionError, CompiledCommission, _path, compile_commission
from ..git_facts import GitPreflightError, repository_head, repository_identity
from .config import (
    ConsumerConfig, ExecutionEnvelope, consumer_config_from_yaml, execution_overrides_from_flags, resolve_execution,
)
from .request import ConsumerRequest, consumer_request_from_yaml

DEFAULT_CONFIG_PATH = ".wfc/config.yaml"
DEFAULT_EVIDENCE_TIMEOUT_SECONDS = 900


@dataclass(frozen=True)
class TaskDocument:
    """The parsed, human-authored task description for one `wfc plan` preview."""

    title: str
    body: str
    acceptance: str
    withheld_authority: str
    escalation_default: str
    raw_text: str


_TASK_DOCUMENT_RE = re.compile(
    r"\A#\s+(?P<title>.+?)\s*\n+(?P<body>.*?)"
    r"\n+##\s+Acceptance\s*\n+(?P<acceptance>.*?)"
    r"\n+##\s+Withheld authority\s*\n+(?P<withheld_authority>.*?)"
    r"\n+##\s+Default escalation\s*\n+(?P<escalation_default>.*?)\s*\Z",
    re.DOTALL,
)


def task_document_from_markdown(text: str) -> TaskDocument:
    """Parse the same task-document shape ``commissioning._task_text`` renders.

    This reuses an existing, already-rendered convention rather than
    inventing a new one: ``# <title>`` then a body, then, in order,
    ``## Acceptance``, ``## Withheld authority`` and ``## Default escalation``.
    """

    match = _TASK_DOCUMENT_RE.match(text)
    if not match:
        raise CommissionError(
            "task document must be '# <title>' followed by a body, then "
            "'## Acceptance', '## Withheld authority' and '## Default escalation' "
            "sections, in that order"
        )
    fields = {key: value.strip() for key, value in match.groupdict().items()}
    empty = sorted(key for key, value in fields.items() if not value)
    if empty:
        raise CommissionError(f"task document section(s) must not be empty: {', '.join(empty)}")
    return TaskDocument(raw_text=text, **fields)


def _parse_key_value(item: str, *, flag: str, separator: str = "=") -> tuple[str, str]:
    if separator not in item:
        raise CommissionError(f"{flag} must contain '{separator}', got: {item}")
    key, _, value = item.partition(separator)
    key, value = key.strip(), value.strip()
    if not key or not value:
        raise CommissionError(f"{flag} must be a non-empty KEY{separator}VALUE pair, got: {item}")
    return key, value


def _personas_from_flags(values: list[str]) -> dict[str, str]:
    personas: dict[str, str] = {}
    for item in values:
        role, value = _parse_key_value(item, flag="--persona")
        if role in personas:
            raise CommissionError(f"--persona names role more than once: {role}")
        personas[role] = value
    return personas


def _durable_artifacts_from_flags(values: list[str]) -> dict[str, list[str]]:
    artifacts: dict[str, list[str]] = {}
    for item in values:
        phase, path = _parse_key_value(item, flag="--durable-artifact")
        artifacts.setdefault(phase, []).append(path)
    return artifacts


class PlanInputError(CommissionError):
    """A refusal resolving the shared ``plan``/``request`` CLI inputs."""


def _program_ref_from_flag(value: str) -> tuple[str, str]:
    if "@" not in value:
        raise CommissionError(f"--program must be ID@REVISION, got: {value}")
    program_id, _, revision = value.partition("@")
    if not program_id or not revision:
        raise CommissionError(f"--program must be ID@REVISION, got: {value}")
    return program_id, revision


def consumer_request_from_plan_inputs(
    *, task: TaskDocument, commission_id: str, work_item_id: str, authority_source: str,
    program: str, scope: list[str], personas: list[str], evidence_argv: list[str],
    evidence_timeout: int, max_autonomous_hops: int, durable_artifact: list[str],
    guidance_path: str, guidance_commit: str,
) -> ConsumerRequest:
    """Assemble and strictly re-parse one ``ConsumerRequest`` from plan CLI inputs.

    Building the YAML text and calling ``consumer_request_from_yaml`` (rather
    than constructing the dataclass directly) exercises the identical closed
    BP2b schema a committed request would, including its field-forgery and
    scope refusals.
    """

    program_id, program_revision = _program_ref_from_flag(program)
    request_data = {
        "schema_version": 1,
        "commission_id": commission_id,
        "work_item_id": work_item_id,
        "authority_source": authority_source,
        "program": {"id": program_id, "revision": program_revision},
        "task": {
            "title": task.title, "body": task.body, "acceptance": task.acceptance,
            "withheld_authority": task.withheld_authority, "escalation_default": task.escalation_default,
        },
        "scope": list(scope),
        "personas": _personas_from_flags(personas),
        "evidence": {"argv": list(evidence_argv), "timeout_seconds": evidence_timeout},
        "max_autonomous_hops": max_autonomous_hops,
        "durable_artifacts": _durable_artifacts_from_flags(durable_artifact),
        "guidance": {"path": guidance_path, "source_commit": guidance_commit},
    }
    request_text = yaml.safe_dump(request_data, sort_keys=True, allow_unicode=True)
    return consumer_request_from_yaml(request_text)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_commission_preview_lines(
    *, header: str, task: TaskDocument, request: ConsumerRequest, config: ConsumerConfig,
    compiled: CompiledCommission, envelope: ExecutionEnvelope,
) -> list[str]:
    """The Commission/envelope preview body shared by ``wfc plan`` and ``wfc
    request`` (D1 Option B) -- everything both commands print identically for
    the same inputs, up to and excluding each command's own closing
    statement. Reused, never duplicated, so the two can never silently drift
    apart on what a given input set projects."""

    lines: list[str] = []
    lines.append(header)
    lines.append(f"program: {request.program.id}@{request.program.revision}")
    lines.append(f"authority_source: {request.authority_source}")
    lines.append("")
    lines.append("Task:")
    lines.append(f"  title: {task.title}")
    lines.append(f"  body: {task.body}")
    lines.append(f"  acceptance: {task.acceptance}")
    lines.append(f"  withheld_authority: {task.withheld_authority}")
    lines.append(f"  escalation_default: {task.escalation_default}")
    lines.append("")
    lines.append(f"scope: {', '.join(request.scope)}")
    lines.append(
        "personas: " + ", ".join(f"{role}={value}" for role, value in sorted(request.personas.items()))
    )
    lines.append(f"max_autonomous_hops: {request.max_autonomous_hops} (config ceiling: {config.max_autonomous_hops})")
    lines.append(f"evidence.argv: {' '.join(request.evidence.argv)}")
    lines.append(f"evidence.timeout_seconds: {request.evidence.timeout_seconds}")
    if request.durable_artifacts:
        lines.append("durable_artifacts:")
        for phase in sorted(request.durable_artifacts):
            lines.append(f"  {phase}: {', '.join(request.durable_artifacts[phase])}")
    lines.append(f"guidance: {request.guidance.path}@{request.guidance.source_commit}")
    lines.append("")
    lines.append(f"projected Commission target directory (not created by plan): {compiled.target_dir}")
    lines.append("projected Commission files (not written by plan):")
    for path in sorted(compiled.texts):
        lines.append(f"  {path}  sha256:{_sha256(compiled.texts[path])}")
    manifest_digest = hashlib.sha256(json.dumps(compiled.manifest, sort_keys=True).encode("utf-8")).hexdigest()
    lines.append(f"projected Commission manifest digest: sha256:{manifest_digest}")
    lines.append("")
    lines.append(
        "role defaults ignored by this program: "
        + (", ".join(envelope.ignored_default_roles) if envelope.ignored_default_roles else "(none)")
    )
    lines.append("resolved execution envelope (separate from the Commission; not published):")
    for role in sorted(envelope.configuration.bindings, key=lambda value: value.value):
        binding = envelope.configuration.bindings[role]
        lines.append(f"  {role.value}: adapter={binding.adapter} model={binding.model} reasoning={binding.reasoning}")
    lines.append("effective execution by phase:")
    for phase, role in sorted(compiled.phase_roles.items()):
        binding = envelope.configuration.binding_for(phase, role)
        lines.append(f"  {phase} ({role.value}): adapter={binding.adapter} model={binding.model} reasoning={binding.reasoning}")
    lines.append(f"execution envelope digest: sha256:{envelope.digest}")
    return lines


def render_plan_summary(
    *, task: TaskDocument, request: ConsumerRequest, config: ConsumerConfig,
    compiled: CompiledCommission, envelope: ExecutionEnvelope,
) -> str:
    """Render a deterministic, human-readable preview; nothing here is written."""

    header = f"wfc plan preview -- commission {request.commission_id} / work item {request.work_item_id}"
    lines = render_commission_preview_lines(
        header=header, task=task, request=request, config=config, compiled=compiled, envelope=envelope,
    )
    lines.append("")
    lines.append(
        "NEXT EXPLICIT ACTION: nothing was run or written by this preview -- no project "
        "command, provider, Commission write or Git mutation. Launching a Run is separate, "
        "later work (not part of this preview)."
    )
    return "\n".join(lines) + "\n"


def add_plan_input_arguments(parser: argparse.ArgumentParser) -> None:
    """Every argument ``wfc plan`` and ``wfc request`` (D1 Option B) take in
    common -- the one argument definition each shares, never redefined."""

    parser.add_argument("task", help="path to a '# title' / body / Acceptance / Withheld authority / "
                                      "Default escalation task document")
    parser.add_argument("--repository", default=".", help="consumer repository root (default: current directory)")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                         help=f"Consumer Config path, relative to --repository (default: {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--scope", action="append", default=[], required=True, metavar="PATH",
                         help="repository-relative scope path (repeatable, at least one)")
    parser.add_argument("--commission-id", required=True)
    parser.add_argument("--work-item-id", required=True)
    parser.add_argument("--authority-source", required=True)
    parser.add_argument("--program", required=True, help="ID@REVISION, e.g. simple@v1 or robust@v1")
    parser.add_argument("--persona", action="append", default=[], required=True, metavar="ROLE=PERSONA",
                         help="persona text for one program role (repeatable, one per required role)")
    parser.add_argument("--evidence-argv", action="append", default=[], required=True, metavar="TOKEN",
                         help="one argv token of the task-check command (repeatable, at least one)")
    parser.add_argument("--evidence-timeout", type=int, default=DEFAULT_EVIDENCE_TIMEOUT_SECONDS)
    parser.add_argument("--max-autonomous-hops", type=int, default=None,
                         help="defaults to the Consumer Config's own ceiling")
    parser.add_argument("--durable-artifact", action="append", default=[], metavar="PHASE=PATH",
                         help="required-artifact path for a review phase (repeatable; robust@v1 only)")
    parser.add_argument("--guidance-path", required=True)
    parser.add_argument("--guidance-commit", default=None,
                         help="40-character pinned commit SHA; defaults to the repository's current HEAD")
    parser.add_argument("--execution-override", action="append", default=[], metavar="ROLE.FIELD=VALUE",
                         help="override one resolved execution binding field (repeatable)")
    parser.add_argument("--target-dir", default=None,
                         help="defaults to '<governance_dir>/<commission-id>'")


@dataclass(frozen=True)
class ResolvedPlanInputs:
    """Everything ``render_commission_preview_lines`` needs, resolved once
    from the shared CLI inputs -- the one place ``wfc plan`` and ``wfc
    request`` each call, never duplicating the resolution steps."""

    repository: Path
    task: TaskDocument
    request: ConsumerRequest
    config: ConsumerConfig
    compiled: CompiledCommission
    envelope: ExecutionEnvelope


def resolve_plan_inputs(args: argparse.Namespace) -> ResolvedPlanInputs:
    """Resolve the shared ``plan``/``request`` CLI inputs through the exact
    BP2b compile/resolve path, raising ``PlanInputError`` (never printing or
    exiting) on any refusal so each command can prefix it with its own name."""

    try:
        repository = repository_identity(Path(args.repository))
    except GitPreflightError as error:
        raise PlanInputError(str(error)) from error

    try:
        task_text = Path(args.task).read_text(encoding="utf-8")
    except OSError as error:
        raise PlanInputError(f"cannot read task document '{args.task}': {error}") from error

    try:
        _path(args.config, context="--config")
    except CommissionError as error:
        raise PlanInputError(str(error)) from error

    config_path = Path(args.config)
    config_file = repository / config_path
    try:
        config_text = config_file.read_text(encoding="utf-8")
    except OSError as error:
        raise PlanInputError(
            f"cannot read Consumer Config '{config_file}': {error} (run `wfc init` first, or pass --config)"
        ) from error

    try:
        task = task_document_from_markdown(task_text)
        config = consumer_config_from_yaml(config_text)
        guidance_commit = args.guidance_commit or repository_head(repository)
        max_autonomous_hops = args.max_autonomous_hops if args.max_autonomous_hops is not None else config.max_autonomous_hops
        request = consumer_request_from_plan_inputs(
            task=task, commission_id=args.commission_id, work_item_id=args.work_item_id,
            authority_source=args.authority_source, program=args.program, scope=args.scope,
            personas=args.persona, evidence_argv=args.evidence_argv, evidence_timeout=args.evidence_timeout,
            max_autonomous_hops=max_autonomous_hops, durable_artifact=args.durable_artifact,
            guidance_path=args.guidance_path, guidance_commit=guidance_commit,
        )
        compiled = compile_commission(request, config, repository=repository, target_dir=args.target_dir)
        overrides = execution_overrides_from_flags(args.execution_override)
        envelope = resolve_execution(config, overrides, compiled.phase_roles)
    except (CommissionError, GitPreflightError) as error:
        raise PlanInputError(str(error)) from error

    return ResolvedPlanInputs(
        repository=repository, task=task, request=request, config=config, compiled=compiled, envelope=envelope,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wfc plan",
        description=(
            "Preview a bounded task: parse it and the given fields into a BP2b Consumer "
            "Request/Config, project the Commission bytes and resolve the separate "
            "execution envelope. Entirely read-only: no project command, provider, "
            "Commission write or Git mutation."
        ),
    )
    add_plan_input_arguments(parser)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        inputs = resolve_plan_inputs(args)
    except PlanInputError as error:
        print(f"wfc plan: {error}", file=sys.stderr)
        return 2

    print(
        render_plan_summary(
            task=inputs.task, request=inputs.request, config=inputs.config,
            compiled=inputs.compiled, envelope=inputs.envelope,
        ),
        end="",
    )
    return 0
