"""Closed-program, non-authoritative Commission compilation.

This module deliberately has no reducer integration and makes no provider
selection.  It turns the small ``simple@v1``/``robust@v1`` Consumer Request
vocabulary into ordinary WFC governing files for the product Front Door;
re-verification and admission of those files live in
``frontdoor.verify.verify_commission`` rather than here.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Hashable
from dataclasses import dataclass
from importlib.resources import files as _resource_files
from pathlib import Path
from typing import Any, Mapping

import yaml
from yaml.constructor import ConstructorError

from .implementation_identity import RESOURCE_DIGESTS as _PACKAGE_RESOURCE_DIGESTS, VERSION as _PACKAGE_VERSION
from .model import WorkflowRole
from .serialization import (
    ValidationError,
    a1_path_shape,
    component_manifest_from_yaml,
    repository_policy_from_yaml,
    workflow_profile_from_yaml,
    work_item_from_yaml,
)


class CommissionError(ValueError):
    """A fail-closed Intent, source-pin, or projection refusal."""


_SHA = re.compile(r"[0-9a-f]{40}\Z")


class _IntentLoader(yaml.SafeLoader):
    """Safe YAML with duplicate mappings refused instead of silently merged."""


def _construct_unique_mapping(loader: _IntentLoader, node: yaml.MappingNode, deep: bool = False):
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, Hashable):
            raise ConstructorError("while constructing a mapping", node.start_mark, "found unhashable key", key_node.start_mark)
        if key in result:
            raise ConstructorError("while constructing a mapping", node.start_mark, f"duplicate key: {key}", key_node.start_mark)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_IntentLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _yaml_load(text: str, *, context: str) -> Any:
    try:
        return yaml.load(text, Loader=_IntentLoader)
    except yaml.YAMLError as error:
        raise CommissionError(f"invalid {context} YAML: {error}") from error


@dataclass(frozen=True)
class TaskBinding:
    title: str
    body: str
    acceptance: str
    withheld_authority: str
    escalation_default: str


def _mapping(value: Any, *, context: str, required: set[str], optional: set[str] = set()) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CommissionError(f"{context} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise CommissionError(f"{context} keys must be strings")
    keys = set(value)
    unexpected = keys - required - optional
    missing = required - keys
    if unexpected:
        raise CommissionError(f"{context} contains forbidden field(s): {', '.join(sorted(unexpected))}")
    if missing:
        raise CommissionError(f"{context} is missing required field(s): {', '.join(sorted(missing))}")
    return value


def _text(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CommissionError(f"{context} must be a non-empty string")
    return value


def _path(value: Any, *, context: str) -> str:
    text = _text(value, context=context)
    # Rendering must reject exactly the malformed paths that canonical
    # admission rejects; otherwise it could emit a bundle guaranteed to fail
    # later preflight.  Keep that shape predicate in serialization as the
    # single library definition rather than reproducing a near-match here.
    if not a1_path_shape(text):
        raise CommissionError(f"{context} must be an A1-normalized repository-relative path")
    return text


def _git_text(repository: Path, commit: str, path: str, *, context: str) -> str:
    path = _path(path, context=context)
    found = subprocess.run(
        ("git", "-C", str(repository), "ls-tree", "-r", "--name-only", commit, "--", path),
        capture_output=True, text=True,
    )
    if found.returncode or found.stdout.splitlines() != [path]:
        raise CommissionError(f"{context} is absent or ambiguous at pinned source commit {commit}: {path}")
    blob = subprocess.run(("git", "-C", str(repository), "show", f"{commit}:{path}"), capture_output=True, text=True)
    if blob.returncode:
        raise CommissionError(f"unable to read {context} at pinned source commit {commit}: {path}")
    return blob.stdout


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _within(path: str, scope: tuple[str, ...]) -> bool:
    return any(path == root or path.startswith(root.rstrip("/") + "/") for root in scope)


def _robust_template() -> dict[str, Any]:
    """The accepted robust@v1 literal record, excluding catalogue provenance."""

    return {
        "schema_version": 1, "program_id": "robust", "revision": "v1",
        "roles": ["worker", "independent_reviewer"], "scope_slot": "S", "artifact_scope_slot": "A",
        "evidence_slot": "E",
        "phases": {
            "implement": {"role": "worker", "components": {"role": ["worker"], "modifiers": [], "repository_policy": []}, "durable_artifact": "none", "scope": "S", "artifact_key": None, "authorized_actions": ["edit", "commit"], "human_gates": [], "evidence": [], "review": None, "routes": {"broad_review": {"effect": "goto", "target": "broad_review"}}},
            "broad_review": {"role": "independent_reviewer", "components": {"role": ["reviewer"], "modifiers": [], "repository_policy": []}, "durable_artifact": "required", "scope": "A", "artifact_key": "broad_review", "authorized_actions": ["review", "edit", "commit"], "human_gates": [], "evidence": "E", "review": {"purpose": "independent adversarial full-scope review of the rendered Commission", "breadth": "broad", "authority": "gate", "context": "fresh", "blocking_policy": "blocking_only", "trigger": "profile_selected", "exit": "declared_route", "finding_authority": ["resolve"], "failed_evidence_route": "correction"}, "routes": {"accept": {"effect": "complete"}, "correction": {"effect": "goto", "target": "correct"}}},
            "correct": {"role": "worker", "components": {"role": ["worker"], "modifiers": [], "repository_policy": []}, "durable_artifact": "none", "scope": "S", "artifact_key": None, "authorized_actions": ["edit", "commit"], "human_gates": [], "evidence": [], "review": None, "routes": {"focused_review": {"effect": "goto", "target": "focused_review"}}},
            "focused_review": {"role": "independent_reviewer", "components": {"role": ["reviewer"], "modifiers": [], "repository_policy": []}, "durable_artifact": "required", "scope": "A", "artifact_key": "focused_review", "authorized_actions": ["review", "edit", "commit"], "human_gates": [], "evidence": "E", "review": {"purpose": "independent focused verification of correction and disposition of the prior broad/focused Findings", "breadth": "focused", "authority": "gate", "context": "fresh", "blocking_policy": "blocking_only", "trigger": "profile_selected", "exit": "declared_route", "finding_authority": ["resolve"], "failed_evidence_route": "correction"}, "routes": {"correction": {"effect": "goto", "target": "correct"}, "renew_broad_review": {"effect": "goto", "target": "broad_review"}}},
        },
    }


def _simple_template() -> dict[str, Any]:
    """The accepted simple@v1 literal record, excluding catalogue provenance."""

    return {
        "schema_version": 1, "program_id": "simple", "revision": "v1", "roles": ["worker"], "scope_slot": "S",
        "evidence_slot": "E",
        "phases": {
            "implement": {"role": "worker", "components": {"role": ["worker"], "modifiers": ["simple-self-check"], "repository_policy": []}, "durable_artifact": "none", "scope": "S", "authorized_actions": ["edit", "commit"], "human_gates": [], "evidence": "E", "review": None, "routes": {"accept": {"effect": "complete"}}},
        },
    }


def _yaml(data: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(data), sort_keys=False, allow_unicode=True)


def _task_text(request: Any) -> str:
    task = request.task
    return (
        f"# {task.title}\n\n{task.body}\n\n## Acceptance\n\n{task.acceptance}\n\n"
        f"## Withheld authority\n\n{task.withheld_authority}\n\n## Default escalation\n\n{task.escalation_default}\n"
    )


def _tree_text(repository: Path, head: str, path: str, *, context: str) -> str:
    return _git_text(repository, head, path, context=context)


# ---------------------------------------------------------------------------
# BP2b -- consumer-neutral compilation with execution bindings kept separate.
#
# ``compile_commission`` compiles a bounded Commission for an arbitrary
# (non-WFC) consumer repository from package-owned product resources
# (``resources/roles/*.txt``, digest-checked against
# ``implementation_identity.RESOURCE_DIGESTS``) and a consumer-authored
# ``repository_policy`` guidance file already tracked in the consumer's own
# repository.  Execution bindings are never read, written, or admitted here;
# ``frontdoor.config.resolve_execution`` produces a separate envelope whose
# identity is independent of these Commission bytes (C1).  Re-verification
# and admission (``frontdoor.verify.verify_commission``) live in
# ``frontdoor`` rather than here, since that is the only BP2b step that must
# parse ``ConsumerRequest``/``ConsumerConfig`` text and the kernel
# import-direction invariant refuses any kernel module naming ``frontdoor``.
# ---------------------------------------------------------------------------

_CONSUMER_ROLE_RESOURCES = {
    "worker": "roles/worker.txt",
    "reviewer": "roles/reviewer.txt",
    "simple-self-check": "roles/simple-self-check.txt",
}
_REPOSITORY_POLICY_COMPONENT_ID = "repository_policy"


def _package_resource_text(relative: str) -> str:
    """Return the reviewed bundled resource's text, digest-checked every call."""

    root = Path(str(_resource_files("workflow_coordinator"))).resolve()
    path = root / "resources" / relative
    try:
        data = path.read_bytes()
    except OSError as error:
        raise CommissionError(f"cannot read package resource {relative}: {error}") from error
    expected = _PACKAGE_RESOURCE_DIGESTS.get(relative)
    digest = hashlib.sha256(data).hexdigest()
    if expected is None or digest != expected:
        raise CommissionError(f"package resource digest mismatch or unreviewed: {relative}")
    return data.decode("utf-8")


def _consumer_program_template(program_id: str, program_revision: str) -> dict[str, Any]:
    """Return the closed simple@v1/robust@v1 literal with repository_policy filled.

    ``direct@v1`` is deliberately withheld from the consumer compiler.  The
    `repository_policy` slot every phase already declares (and leaves empty
    in the self-development templates) is filled here with one shared
    component ID so every phase binds the same consumer guidance.
    """

    if (program_id, program_revision) == ("simple", "v1"):
        template = _simple_template()
    elif (program_id, program_revision) == ("robust", "v1"):
        template = _robust_template()
    else:
        raise CommissionError(
            "only the closed simple@v1 and robust@v1 programs are supported by the consumer compiler"
        )
    template = json.loads(json.dumps(template))
    for phase in template["phases"].values():
        phase["components"]["repository_policy"] = [_REPOSITORY_POLICY_COMPONENT_ID]
    return template


@dataclass(frozen=True)
class CompiledCommission:
    """The deterministic, still-untrusted consumer-neutral projection."""

    request: Any
    config: Any
    target_dir: str
    paths: Mapping[str, str]
    texts: Mapping[str, str]
    manifest: Mapping[str, Any]
    phase_roles: Mapping[str, WorkflowRole]

    def write_to(self, repository: Path, *, overwrite: bool = False) -> None:
        """Materialize ordinary files below ``repository``; refuse to overwrite."""

        for path, text in self.texts.items():
            destination = repository / path
            if destination.exists() and not overwrite:
                raise CommissionError(f"refusing to overwrite existing compiled file: {path}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8")


def compile_commission(
    request: Any, config: Any, *, repository: Path | str, target_dir: str | None = None,
) -> CompiledCommission:
    """Compile one consumer-neutral Commission without any execution binding.

    ``repository`` is read only to pin ``request.guidance`` at its own
    ``source_commit``. Execution settings in the config are resolved separately
    against the compiled topology. The manifest still pins the raw config.
    """

    repository = Path(repository).resolve()
    if not repository.is_dir():
        raise CommissionError(f"repository is not a directory: {repository}")
    program_id, program_revision = request.program.id, request.program.revision
    template = _consumer_program_template(program_id, program_revision)
    required_personas = set(template["roles"])
    if set(request.personas) != required_personas:
        raise CommissionError(
            f"{program_id}@{program_revision} requires exactly " + ", ".join(sorted(required_personas)) + " personas"
        )
    # The program decides WHICH phases are gated (``evidence: "E"`` versus
    # ``evidence: []``); the Consumer Request decides WHAT the gates are, in
    # order. Every gated phase receives the identical, complete list, so no
    # Commission can give one phase a weaker gate than another (FTR-WFC-
    # EVIDENCE-COMMAND-SEQUENCE, Design decision (b)).
    evidence_names = tuple(command.name for command in request.evidence)
    if not evidence_names:
        raise CommissionError("Consumer Request must declare at least one evidence command")
    required_artifacts = {
        phase["artifact_key"] for phase in template["phases"].values() if phase["durable_artifact"] == "required"
    }
    if set(request.durable_artifacts) != required_artifacts:
        if not required_artifacts:
            raise CommissionError(f"{program_id}@{program_revision} contains forbidden durable-artifact mappings")
        raise CommissionError(
            f"{program_id}@{program_revision} requires exactly durable-artifact mappings: "
            + ", ".join(sorted(required_artifacts))
        )
    if any(not _within(path, request.scope) for paths in request.durable_artifacts.values() for path in paths):
        raise CommissionError("Consumer Request durable-artifact paths must be contained in Consumer Request scope")
    if request.max_autonomous_hops > config.max_autonomous_hops:
        raise CommissionError("Consumer Request hop limit exceeds the Consumer Config ceiling")
    if any(not _within(path, config.work_item_scope_ceiling) for path in request.scope):
        raise CommissionError("Consumer Request scope exceeds the Consumer Config work-item scope ceiling")
    guidance_within_governance_dir = _within(request.guidance.path, (config.governance_dir,))
    governance_paths = (
        (config.governance_dir,) if guidance_within_governance_dir else (config.governance_dir, request.guidance.path)
    )
    if any(_within(path, governance_paths) for path in request.scope):
        raise CommissionError("Consumer Request scope overlaps the repository_policy guidance or governance directory")
    target_dir = _path(target_dir or f"{config.governance_dir}/{request.commission_id}", context="compile target directory")
    if not _within(target_dir, (config.governance_dir,)):
        raise CommissionError("compile target directory is outside the Consumer Config governance directory")

    guidance_text = _git_text(repository, request.guidance.source_commit, request.guidance.path, context="consumer guidance")

    role_component_ids = sorted({
        component_id for phase in template["phases"].values() for component_id in phase["components"]["role"]
    })
    modifier_component_ids = sorted({
        component_id for phase in template["phases"].values() for component_id in phase["components"]["modifiers"]
    })
    package_component_ids = [*role_component_ids, *modifier_component_ids]
    component_resources: dict[str, str] = {}
    component_texts: dict[str, str] = {}
    for component_id in package_component_ids:
        resource_relative = _CONSUMER_ROLE_RESOURCES.get(component_id)
        if resource_relative is None:
            raise CommissionError(f"internal: no package resource is bound for component: {component_id}")
        component_resources[component_id] = resource_relative
        component_texts[component_id] = _package_resource_text(resource_relative)

    paths = {
        "work_item": f"{target_dir}/work-item.yaml", "profile": f"{target_dir}/profile.yaml",
        "policy": f"{target_dir}/policy.yaml", "task": f"{target_dir}/task.md",
        "components": f"{target_dir}/components.yaml", "manifest": f"{target_dir}/commission-manifest.json",
    }
    component_paths = {
        component_id: f"{target_dir}/components/{component_id}.txt" for component_id in package_component_ids
    }
    component_paths[_REPOSITORY_POLICY_COMPONENT_ID] = request.guidance.path

    profile_phases = template["phases"]
    for phase in profile_phases.values():
        scope_slot = phase.pop("scope")
        artifact_key = phase.pop("artifact_key", None)
        if scope_slot == "S":
            phase["authorized_scope"] = list(request.scope)
        else:
            phase["authorized_scope"] = list(request.durable_artifacts[artifact_key])
        if phase["evidence"] == "E":
            phase["evidence"] = list(evidence_names)
    profile_data = {
        "schema_version": 2, "profile_id": f"{request.commission_id}-{program_id}-{program_revision}",
        "revision": "1", "phases": profile_phases,
    }
    policy_data = {
        "schema_version": 3, "policy_id": "consumer-standard", "revision": "v1",
        "scope": [config.repository_name], "governance_paths": list(governance_paths),
        "work_item_scope_ceiling": list(config.work_item_scope_ceiling), "max_autonomous_hops": config.max_autonomous_hops,
        "actions": ["edit", "commit", "review"],
        "evidence_commands": {
            command.name: {"argv": list(command.argv), "timeout_seconds": command.timeout_seconds}
            for command in request.evidence
        },
    }
    item_data = {
        "schema_version": 2, "work_item_id": request.work_item_id, "authority_source": request.authority_source,
        "profile_ref": paths["profile"], "repository": config.repository_name, "scope": list(request.scope),
        "initial_phase": "implement", "max_autonomous_hops": request.max_autonomous_hops,
        "personas": dict(request.personas),
        "profile_labels": [
            f"program_id={program_id}", f"program_revision={program_revision}", "program_source=package",
        ],
        "durable_artifacts": {key: list(value) for key, value in request.durable_artifacts.items()},
    }
    texts: dict[str, str] = {
        paths["work_item"]: _yaml(item_data), paths["profile"]: _yaml(profile_data), paths["policy"]: _yaml(policy_data),
        paths["task"]: _task_text(request),
    }
    for component_id, text in component_texts.items():
        texts[component_paths[component_id]] = text
    components_data = {
        "schema_version": 1,
        "components": {component_id: {"path": path} for component_id, path in component_paths.items()},
    }
    texts[paths["components"]] = _yaml(components_data)

    source_digests = {
        "request": _sha(request.raw_text), "config": _sha(config.raw_text), "guidance": _sha(guidance_text),
        **{f"component:{component_id}": _sha(text) for component_id, text in component_texts.items()},
    }
    manifest_data = {
        "schema_version": 1,
        "request": {"sha256": source_digests["request"]},
        "config": {"sha256": source_digests["config"]},
        "package": {
            "version": _PACKAGE_VERSION,
            "resources": {
                component_resources[component_id]: source_digests[f"component:{component_id}"]
                for component_id in package_component_ids
            },
        },
        "program": {"id": program_id, "revision": program_revision},
        "guidance": {
            "path": request.guidance.path, "source_commit": request.guidance.source_commit,
            "sha256": source_digests["guidance"],
        },
        "targets": paths,
        "selected_components": [
            {
                "id": component_id,
                "path": component_paths[component_id],
                "origin": "guidance" if component_id == _REPOSITORY_POLICY_COMPONENT_ID else "package",
                "sha256": (
                    source_digests["guidance"]
                    if component_id == _REPOSITORY_POLICY_COMPONENT_ID
                    else source_digests[f"component:{component_id}"]
                ),
            }
            for component_id in component_paths
        ],
        "digests": {path: _sha(value) for path, value in texts.items()},
    }
    manifest_text = json.dumps(manifest_data, sort_keys=True, indent=2) + "\n"
    texts[paths["manifest"]] = manifest_text
    # Exercise the unchanged schemas now, before callers ever commit a malformed projection.
    try:
        work_item = work_item_from_yaml(texts[paths["work_item"]])
        profile = workflow_profile_from_yaml(texts[paths["profile"]])
        repository_policy_from_yaml(texts[paths["policy"]])
        component_manifest_from_yaml(texts[paths["components"]])
    except ValidationError as error:
        raise CommissionError(f"internal compiled schema refusal: {error}") from error
    return CompiledCommission(
        request, config, target_dir, paths, texts, manifest_data,
        profile.executing_phase_roles(work_item.initial_phase),
    )
