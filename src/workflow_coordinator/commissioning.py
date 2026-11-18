"""Closed-program, non-authoritative Commission Intent rendering.

This module deliberately has no reducer integration and makes no provider
selection.  It turns the small ``direct@v1`` and ``robust@v1`` request
vocabularies into ordinary WFC governing files, then (only for
``verify_render_manifest``) asks the existing operator preflight to admit
those files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from importlib.resources import files as _resource_files
from pathlib import Path
from typing import Any, Mapping

import yaml
from yaml.constructor import ConstructorError

from .git_facts import GitPreflightError, observe_repository, repository_identity
from .implementation_identity import RESOURCE_DIGESTS as _PACKAGE_RESOURCE_DIGESTS, VERSION as _PACKAGE_VERSION
from .operator import OperatorPreflightError, _pinned_text, _preflight, _relative_path, admit_parsed
from .prompt import load_agent_operating_contract
from .serialization import (
    ValidationError,
    a1_path_shape,
    component_manifest_from_yaml,
    repository_policy_from_yaml,
    run_execution_configuration_from_yaml,
    workflow_profile_from_yaml,
    work_item_from_yaml,
)


class CommissionError(ValueError):
    """A fail-closed Intent, source-pin, or projection refusal."""


_SHA = re.compile(r"[0-9a-f]{40}\Z")
_COMPONENT_MANIFEST_PATH = "governance/self-development/components.yaml"
_PROGRAM_PATH = "governance/workflow-programs/direct/v1.yaml"
_ROBUST_PROGRAM_PATH = "governance/workflow-programs/robust/v1.yaml"
_POLICY_PATHS = {
    "v1": "governance/policy-bases/repository-standard-v1.yaml",
    "v2": "governance/policy-bases/repository-standard-v2.yaml",
}


class _IntentLoader(yaml.SafeLoader):
    """Safe YAML with duplicate mappings refused instead of silently merged."""


def _construct_unique_mapping(loader: _IntentLoader, node: yaml.MappingNode, deep: bool = False):
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
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
class SourceRef:
    id: str
    revision: str
    source_commit: str


@dataclass(frozen=True)
class TaskBinding:
    title: str
    body: str
    acceptance: str
    withheld_authority: str
    escalation_default: str


@dataclass(frozen=True)
class ComponentsRef:
    path: str
    source_commit: str


@dataclass(frozen=True)
class CommissionIntent:
    schema_version: int
    commission_id: str
    work_item_id: str
    authority_source: str
    program: SourceRef
    task: TaskBinding
    scope: tuple[str, ...]
    personas: Mapping[str, str]
    evidence_profile: str
    policy_basis: SourceRef
    max_autonomous_hops: int
    durable_artifacts: Mapping[str, tuple[str, ...]]
    components_ref: ComponentsRef
    raw_text: str


@dataclass(frozen=True)
class RenderedCommission:
    """The deterministic, still-untrusted governing bundle."""

    intent: CommissionIntent
    target_dir: str
    paths: Mapping[str, str]
    texts: Mapping[str, str]
    manifest: Mapping[str, Any]

    def write_to(self, repository: Path, *, overwrite: bool = False) -> None:
        """Materialize ordinary files below ``repository``.

        Rendering itself is pure.  This small explicit write seam refuses to
        overwrite an existing Commission bundle, including an old manifest.
        """

        for path, text in self.texts.items():
            destination = repository / path
            if destination.exists() and not overwrite:
                raise CommissionError(f"refusing to overwrite existing rendered file: {path}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8")


def _mapping(value: Any, *, context: str, required: set[str], optional: set[str] = set()) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CommissionError(f"{context} must be a mapping")
    keys = set(value)
    unexpected = keys - required - optional
    missing = required - keys
    if unexpected:
        raise CommissionError(f"{context} contains forbidden field(s): {', '.join(sorted(unexpected))}")
    if missing:
        raise CommissionError(f"{context} is missing required field(s): {', '.join(sorted(missing))}")
    if any(not isinstance(key, str) for key in value):
        raise CommissionError(f"{context} keys must be strings")
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


def _source_ref(value: Any, *, context: str) -> SourceRef:
    data = _mapping(value, context=context, required={"id", "revision", "source_commit"})
    source_commit = _text(data["source_commit"], context=f"{context}.source_commit")
    if not _SHA.fullmatch(source_commit):
        raise CommissionError(f"{context}.source_commit must be a full lowercase 40-character SHA")
    return SourceRef(_text(data["id"], context=f"{context}.id"), _text(data["revision"], context=f"{context}.revision"), source_commit)


def commission_intent_from_yaml(text: str) -> CommissionIntent:
    """Parse exactly the deliberately small Commission Intent vocabulary."""

    raw = _yaml_load(text, context="Commission Intent")
    data = _mapping(
        raw,
        context="Commission Intent",
        required={
            "schema_version", "commission_id", "work_item_id", "authority_source", "program", "task",
            "scope", "personas", "evidence_profile", "policy_basis", "max_autonomous_hops",
            "durable_artifacts", "components_ref",
        },
    )
    if data["schema_version"] != 1:
        raise CommissionError("Commission Intent.schema_version must be 1")
    task = _mapping(
        data["task"], context="Commission Intent.task",
        required={"title", "body", "acceptance", "withheld_authority", "escalation_default"},
    )
    if not isinstance(data["scope"], list) or not data["scope"]:
        raise CommissionError("Commission Intent.scope must be a non-empty list")
    scope = tuple(_path(value, context="Commission Intent.scope") for value in data["scope"])
    if len(set(scope)) != len(scope):
        raise CommissionError("Commission Intent.scope must not contain duplicates")
    program = _source_ref(data["program"], context="Commission Intent.program")
    required_personas = {
        ("direct", "v1"): {"worker"},
        ("robust", "v1"): {"worker", "independent_reviewer"},
        ("simple", "v1"): {"worker"},
    }.get((program.id, program.revision))
    if required_personas is None:
        raise CommissionError("only the closed direct@v1, robust@v1, and simple@v1 programs are supported")
    personas = _mapping(data["personas"], context="Commission Intent.personas", required=required_personas)
    personas = {role: _text(value, context=f"Commission Intent.personas.{role}") for role, value in personas.items()}
    artifacts_raw = data["durable_artifacts"]
    if not isinstance(artifacts_raw, dict) or any(not isinstance(key, str) for key in artifacts_raw):
        raise CommissionError("Commission Intent.durable_artifacts must be a string-keyed mapping")
    artifacts: dict[str, tuple[str, ...]] = {}
    for phase, values in artifacts_raw.items():
        if not isinstance(values, list) or not values:
            raise CommissionError(f"Commission Intent.durable_artifacts.{phase} must be a non-empty list")
        paths = tuple(_path(value, context=f"Commission Intent.durable_artifacts.{phase}") for value in values)
        if len(set(paths)) != len(paths):
            raise CommissionError(f"Commission Intent.durable_artifacts.{phase} contains duplicates")
        artifacts[phase] = paths
    components = _mapping(data["components_ref"], context="Commission Intent.components_ref", required={"path", "source_commit"})
    component_commit = _text(components["source_commit"], context="Commission Intent.components_ref.source_commit")
    if not _SHA.fullmatch(component_commit):
        raise CommissionError("Commission Intent.components_ref.source_commit must be a full lowercase 40-character SHA")
    hops = data["max_autonomous_hops"]
    if not isinstance(hops, int) or isinstance(hops, bool) or hops <= 0:
        raise CommissionError("Commission Intent.max_autonomous_hops must be a positive integer")
    return CommissionIntent(
        schema_version=1,
        commission_id=_text(data["commission_id"], context="Commission Intent.commission_id"),
        work_item_id=_text(data["work_item_id"], context="Commission Intent.work_item_id"),
        authority_source=_text(data["authority_source"], context="Commission Intent.authority_source"),
        program=program,
        task=TaskBinding(**{key: _text(value, context=f"Commission Intent.task.{key}") for key, value in task.items()}),
        scope=scope,
        personas=personas,
        evidence_profile=_text(data["evidence_profile"], context="Commission Intent.evidence_profile"),
        policy_basis=_source_ref(data["policy_basis"], context="Commission Intent.policy_basis"),
        max_autonomous_hops=hops,
        durable_artifacts=artifacts,
        components_ref=ComponentsRef(_path(components["path"], context="Commission Intent.components_ref.path"), component_commit),
        raw_text=text,
    )


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


def _direct_template() -> dict[str, Any]:
    return {
        "schema_version": 1, "program_id": "direct", "revision": "v1", "roles": ["worker"], "scope_slot": "S",
        "evidence_profiles": {"none": {"evidence": []}, "task-check-v1": {"evidence": ["task-check"]}},
        "phases": {
            "implement": {"role": "worker", "components": {"role": ["worker"], "modifiers": [], "repository_policy": []}, "durable_artifact": "none", "scope": "S", "authorized_actions": ["edit", "commit"], "human_gates": [], "evidence": [], "review": None, "routes": {"verify": {"effect": "goto", "target": "verification"}}},
            "verification": {"role": "worker", "components": {"role": ["worker"], "modifiers": [], "repository_policy": []}, "durable_artifact": "none", "scope": "S", "authorized_actions": ["review"], "human_gates": [], "evidence": "E", "review": {"purpose": "deterministic task-check disposition only; no semantic review", "breadth": "verification", "authority": "gate", "context": "fresh", "blocking_policy": "blocking_only", "trigger": "profile_selected", "exit": "declared_route", "finding_authority": [], "failed_evidence_route": "correction"}, "routes": {"accept": {"effect": "complete"}, "correction": {"effect": "goto", "target": "implement"}}},
        },
    }


def _robust_template() -> dict[str, Any]:
    """The accepted robust@v1 literal record, excluding catalogue provenance."""

    return {
        "schema_version": 1, "program_id": "robust", "revision": "v1",
        "roles": ["worker", "independent_reviewer"], "scope_slot": "S", "artifact_scope_slot": "A",
        "evidence_profiles": {"task-check-v1": {"evidence": ["task-check"]}},
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
        "evidence_profiles": {"task-check-v1": {"evidence": ["task-check"]}},
        "phases": {
            "implement": {"role": "worker", "components": {"role": ["worker"], "modifiers": ["simple-self-check"], "repository_policy": []}, "durable_artifact": "none", "scope": "S", "authorized_actions": ["edit", "commit"], "human_gates": [], "evidence": "E", "review": None, "routes": {"accept": {"effect": "complete"}}},
        },
    }


def _repository_standard_record(policy: Any, revision: str) -> bool:
    """Return whether a parsed basis matches its immutable supported record.

    Identity alone is deliberately insufficient: every policy field below is a
    closed input to Direct-v1.  The render may narrow only the work-item scope
    ceiling, after this source record has been verified in full.
    """

    return (
        policy.schema_version == 3
        and policy.policy_id == "repository-standard"
        and policy.revision == revision
        and policy.scope == ("workflow-coordinator",)
        and policy.governance_paths == ("governance",)
        and policy.work_item_scope_ceiling == (
            ("src", "tests", "scripts", "docs") if revision == "v1"
            else ("src", "tests", "scripts", "docs", "README.md", "Makefile")
        )
        and policy.max_autonomous_hops == 10
        and policy.actions == ("edit", "commit", "review")
        and set(policy.evidence_commands) == {"task-check"}
        and policy.evidence_commands["task-check"].argv == ("python", "-m", "pytest", "-q")
        and policy.evidence_commands["task-check"].timeout_seconds == 900
    )


def _load_sources(intent: CommissionIntent, repository: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    program_specs = {
        ("direct", "v1"): (_PROGRAM_PATH, _direct_template, {"worker"}),
        ("robust", "v1"): (_ROBUST_PROGRAM_PATH, _robust_template, {"worker", "reviewer"}),
        ("simple", "v1"): ("governance/workflow-programs/simple/v1.yaml", _simple_template, {"worker", "simple-self-check"}),
    }
    try:
        program_path, expected_template, required_components = program_specs[(intent.program.id, intent.program.revision)]
    except KeyError as error:
        raise CommissionError("only the closed direct@v1, robust@v1, and simple@v1 programs are supported") from error
    if intent.policy_basis.id != "repository-standard" or intent.policy_basis.revision not in _POLICY_PATHS:
        raise CommissionError(f"{intent.program.id}@{intent.program.revision} requires repository-standard@v1 or @v2")
    policy_path = _POLICY_PATHS[intent.policy_basis.revision]
    if intent.components_ref.path != _COMPONENT_MANIFEST_PATH:
        raise CommissionError(f"{intent.program.id}@{intent.program.revision} requires the fixed self-development component manifest")
    program_text = _git_text(repository, intent.program.source_commit, program_path, context="program catalogue")
    policy_text = _git_text(repository, intent.policy_basis.source_commit, policy_path, context="policy basis")
    components_text = _git_text(repository, intent.components_ref.source_commit, intent.components_ref.path, context="component manifest")
    program = _yaml_load(program_text, context="pinned program")
    if not isinstance(program, dict):
        raise CommissionError("pinned program must be a mapping")
    semantic_program = dict(program)
    semantic_program.pop("source", None)
    if semantic_program != expected_template():
        raise CommissionError(
            f"pinned program does not equal the closed {intent.program.id}@{intent.program.revision} literal record"
        )
    try:
        policy = repository_policy_from_yaml(policy_text)
        manifest = component_manifest_from_yaml(components_text)
    except ValidationError as error:
        raise CommissionError(str(error)) from error
    if not _repository_standard_record(policy, intent.policy_basis.revision):
        raise CommissionError(
            f"pinned policy basis does not equal the immutable repository-standard@{intent.policy_basis.revision} record"
        )
    if missing_components := required_components - set(manifest):
        raise CommissionError("fixed component manifest does not define " + ", ".join(sorted(missing_components)))
    return program, {"policy": policy, "manifest": manifest}, program_text, policy_text, components_text, program_path


def _yaml(data: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(data), sort_keys=False, allow_unicode=True)


def _task_text(intent: CommissionIntent) -> str:
    task = intent.task
    return (
        f"# {task.title}\n\n{task.body}\n\n## Acceptance\n\n{task.acceptance}\n\n"
        f"## Withheld authority\n\n{task.withheld_authority}\n\n## Default escalation\n\n{task.escalation_default}\n"
    )


def render_commission(intent: CommissionIntent, *, repository: Path | str, target_dir: str | None = None) -> RenderedCommission:
    """Render a closed program projection without making an admission decision."""

    repository = Path(repository).resolve()
    if not repository.is_dir():
        raise CommissionError(f"repository is not a directory: {repository}")
    program, loaded, program_text, policy_text, components_text, program_path = _load_sources(intent, repository)
    policy = loaded["policy"]
    manifest = loaded["manifest"]
    required_personas = set(program["roles"])
    if set(intent.personas) != required_personas:
        raise CommissionError(
            f"{intent.program.id}@{intent.program.revision} requires exactly "
            + ", ".join(sorted(required_personas)) + " personas"
        )
    if intent.evidence_profile not in program["evidence_profiles"]:
        raise CommissionError(f"Intent selects an undefined {intent.program.id}@{intent.program.revision} evidence profile")
    required_artifacts = {
        phase["artifact_key"]
        for phase in program["phases"].values()
        if phase["durable_artifact"] == "required"
    }
    if set(intent.durable_artifacts) != required_artifacts:
        if not required_artifacts:
            raise CommissionError(f"{intent.program.id}@{intent.program.revision} contains forbidden durable-artifact mappings")
        raise CommissionError(
            f"{intent.program.id}@{intent.program.revision} requires exactly durable-artifact mappings: "
            + ", ".join(sorted(required_artifacts))
        )
    if any(not _within(path, intent.scope) for paths in intent.durable_artifacts.values() for path in paths):
        raise CommissionError("Intent durable-artifact paths must be contained in Intent scope")
    if intent.max_autonomous_hops > policy.max_autonomous_hops:
        raise CommissionError("Intent hop limit exceeds the policy-basis ceiling")
    # Root whitelist entries are files, not directory-shaped scope grants.
    # The four existing directory ceilings retain their original prefix behavior.
    if any(
        not (_within(path, ("src", "tests", "scripts", "docs"))
             or (policy.revision == "v2" and path in ("README.md", "Makefile")))
        for path in intent.scope
    ):
        raise CommissionError("Intent scope exceeds the policy-basis work-item ceiling")
    if any(_within(path, tuple(policy.governance_paths)) for path in intent.scope):
        raise CommissionError("Intent scope overlaps repository governance paths")
    target_dir = _path(target_dir or f"governance/commissions/{intent.commission_id}", context="render target directory")
    if not _within(target_dir, tuple(policy.governance_paths)):
        raise CommissionError("render target directory is outside the policy-basis governance paths")
    paths = {
        "work_item": f"{target_dir}/work-item.yaml", "profile": f"{target_dir}/profile.yaml",
        "policy": f"{target_dir}/policy.yaml", "task": f"{target_dir}/task.md",
        "manifest": f"{target_dir}/render-manifest.json",
    }
    profile_phases = json.loads(json.dumps(program["phases"]))
    for phase in profile_phases.values():
        scope_slot = phase.pop("scope")
        artifact_key = phase.pop("artifact_key", None)
        if scope_slot == "S":
            phase["authorized_scope"] = list(intent.scope)
        else:
            phase["authorized_scope"] = list(intent.durable_artifacts[artifact_key])
        if phase["evidence"] == "E":
            phase["evidence"] = list(program["evidence_profiles"][intent.evidence_profile]["evidence"])
    profile_data = {
        "schema_version": 2, "profile_id": f"{intent.commission_id}-{intent.program.id}-{intent.program.revision}",
        "revision": "1", "phases": profile_phases,
    }
    policy_data = {
        "schema_version": policy.schema_version, "policy_id": policy.policy_id, "revision": policy.revision,
        "scope": list(policy.scope), "governance_paths": list(policy.governance_paths),
        "work_item_scope_ceiling": list(intent.scope), "max_autonomous_hops": policy.max_autonomous_hops,
        "actions": list(policy.actions),
        "evidence_commands": {name: {"argv": list(command.argv), "timeout_seconds": command.timeout_seconds} for name, command in policy.evidence_commands.items()},
    }
    item_data = {
        "schema_version": 2, "work_item_id": intent.work_item_id, "authority_source": intent.authority_source,
        "profile_ref": paths["profile"], "repository": policy.scope[0], "scope": list(intent.scope),
        "initial_phase": "implement", "max_autonomous_hops": intent.max_autonomous_hops,
        "personas": dict(intent.personas),
        "profile_labels": [f"program_id={intent.program.id}", f"program_revision={intent.program.revision}", f"program_source_commit={intent.program.source_commit}"],
        "durable_artifacts": {key: list(paths) for key, paths in intent.durable_artifacts.items()},
    }
    texts = {paths["work_item"]: _yaml(item_data), paths["profile"]: _yaml(profile_data), paths["policy"]: _yaml(policy_data), paths["task"]: _task_text(intent)}
    selected_components = {
        component: manifest[component].path
        for component in dict.fromkeys(
            component
            for phase in program["phases"].values()
            for component in (
                *phase["components"]["role"],
                *phase["components"]["modifiers"],
                *phase["components"]["repository_policy"],
            )
        )
    }
    source_digests = {
        "intent": _sha(intent.raw_text), "program": _sha(program_text), "policy_basis": _sha(policy_text),
        "components": _sha(components_text),
        **{f"component:{name}": _sha(_git_text(repository, intent.components_ref.source_commit, path, context=f"component {name}")) for name, path in selected_components.items()},
    }
    manifest_data = {
        "schema_version": 1,
        "intent": {"sha256": source_digests["intent"]},
        "sources": {
            "program": {"id": intent.program.id, "revision": intent.program.revision, "source_commit": intent.program.source_commit, "path": program_path, "sha256": source_digests["program"]},
            "policy_basis": {"id": intent.policy_basis.id, "revision": intent.policy_basis.revision, "source_commit": intent.policy_basis.source_commit, "path": _POLICY_PATHS[intent.policy_basis.revision], "sha256": source_digests["policy_basis"]},
            "components": {"path": intent.components_ref.path, "source_commit": intent.components_ref.source_commit, "sha256": source_digests["components"]},
            "selected_components": [{"id": name, "path": path, "source_commit": intent.components_ref.source_commit, "sha256": source_digests[f"component:{name}"]} for name, path in selected_components.items()],
        },
        "targets": paths,
        "digests": {path: _sha(value) for path, value in texts.items()},
    }
    manifest_text = json.dumps(manifest_data, sort_keys=True, indent=2) + "\n"
    texts[paths["manifest"]] = manifest_text
    # Exercise the unchanged schemas now, before callers ever commit a malformed projection.
    try:
        work_item_from_yaml(texts[paths["work_item"]])
        workflow_profile_from_yaml(texts[paths["profile"]])
        repository_policy_from_yaml(texts[paths["policy"]])
    except ValidationError as error:
        raise CommissionError(f"internal rendered schema refusal: {error}") from error
    return RenderedCommission(intent, target_dir, paths, texts, manifest_data)


def _tree_text(repository: Path, head: str, path: str, *, context: str) -> str:
    return _git_text(repository, head, path, context=context)


def _clean_head(repository: Path, expected_head: str) -> None:
    if not _SHA.fullmatch(expected_head):
        raise CommissionError("expected_head must be a full lowercase 40-character SHA")
    current = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), capture_output=True, text=True)
    status = subprocess.run(("git", "-C", str(repository), "status", "--porcelain=v1", "--untracked-files=all"), capture_output=True, text=True)
    if current.returncode or current.stdout.strip() != expected_head or status.returncode or status.stdout:
        raise CommissionError("repository must be clean at expected_head before Commission equivalence checking")


def verify_render_manifest(
    intent_path: str, *, repository: Path | str, expected_head: str, target_dir: str | None = None,
    evidence_runner: Any = None,
) -> RenderedCommission:
    """Re-project pinned blobs at ``expected_head``, compare, then call `_preflight`.

    The state root and execution binding source are temporary and used solely to
    satisfy the existing provider-free preflight interface; no agent is invoked.
    """

    repository = Path(repository).resolve()
    _clean_head(repository, expected_head)
    intent_text = _tree_text(repository, expected_head, intent_path, context="Commission Intent")
    intent = commission_intent_from_yaml(intent_text)
    chosen_dir = _path(target_dir or f"governance/commissions/{intent.commission_id}", context="render target directory")
    # Re-resolve the pinned sources before reading a single rendered blob.  The
    # target directory is an explicit invocation input, never a location
    # supplied by an unverified manifest.
    rendered = render_commission(intent, repository=repository, target_dir=chosen_dir)
    for name, path in rendered.paths.items():
        actual = _tree_text(repository, expected_head, path, context=f"rendered {name}")
        if actual != rendered.texts[path]:
            raise CommissionError(f"projection equivalence mismatch for {name}: {path}")
    # The reuse inputs are just as governing as the five main files.
    for path in (intent.components_ref.path, *(entry["path"] for entry in rendered.manifest["sources"]["selected_components"])):
        observed = _tree_text(repository, expected_head, path, context="reused component input")
        pinned = _git_text(repository, intent.components_ref.source_commit, path, context="pinned reused component input")
        if observed != pinned:
            raise CommissionError(f"reused component differs from its pinned source object: {path}")
    with tempfile.TemporaryDirectory(prefix="wfc-commission-check-") as root:
        temp = Path(root)
        bindings = temp / "execution-bindings.yaml"
        binding_lines = "".join(
            f"  {role}: {{adapter: codex_cli, model: commission-check, reasoning: medium}}\n"
            for role in intent.personas
        )
        bindings.write_text(
            "schema_version: 1\nbindings:\n" + binding_lines,
            encoding="utf-8",
        )
        args = argparse.Namespace(
            repository=str(repository), state_root=str(temp / "state"), expected_head=expected_head,
            work_item=rendered.paths["work_item"], profile=rendered.paths["profile"], policy=rendered.paths["policy"],
            components=intent.components_ref.path, task=rendered.paths["task"], execution_bindings=str(bindings),
            durable_artifact=[], timeout_seconds=900.0, executable="commission-check-no-dispatch",
        )
        try:
            _preflight(args, **({"evidence_runner": evidence_runner} if evidence_runner is not None else {}))
        except OperatorPreflightError as error:
            raise CommissionError(f"operator preflight refused projected bundle: {error}") from error
    return rendered


# ---------------------------------------------------------------------------
# BP2b -- consumer-neutral compilation with execution bindings kept separate.
#
# ``compile_commission``/``verify_commission`` compile a bounded Commission
# for an arbitrary (non-WFC) consumer repository from package-owned product
# resources (``resources/roles/*.txt``, digest-checked against
# ``implementation_identity.RESOURCE_DIGESTS``) and a consumer-authored
# ``repository_policy`` guidance file already tracked in the consumer's own
# repository.  Execution bindings are never read, written, or admitted here;
# ``frontdoor.config.resolve_execution`` produces a separate envelope whose
# identity is independent of these Commission bytes (C1).  Admission itself
# is never duplicated: ``verify_commission`` establishes every caller
# obligation ``operator.admit_parsed`` documents, then calls it directly.
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
    ``source_commit``; every other governing input is package-owned.  No
    ``bindings``/``execution``/``provider``/``model``/``effort`` field is a
    legal part of ``request`` or ``config`` -- their closed schemas (see
    ``frontdoor.request``/``frontdoor.config``) already refuse any such key
    before this function ever runs.
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
    evidence_profile_names = list(template["evidence_profiles"])
    if len(evidence_profile_names) != 1:
        raise CommissionError("internal: consumer programs must define exactly one evidence profile")
    evidence_names = tuple(template["evidence_profiles"][evidence_profile_names[0]]["evidence"])
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
            "task-check": {"argv": list(request.evidence.argv), "timeout_seconds": request.evidence.timeout_seconds},
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
        work_item_from_yaml(texts[paths["work_item"]])
        workflow_profile_from_yaml(texts[paths["profile"]])
        repository_policy_from_yaml(texts[paths["policy"]])
        component_manifest_from_yaml(texts[paths["components"]])
    except ValidationError as error:
        raise CommissionError(f"internal compiled schema refusal: {error}") from error
    return CompiledCommission(request, config, target_dir, paths, texts, manifest_data)


_VERIFY_COMMISSION_TIMEOUT_SECONDS = 900.0


def verify_commission(
    request_path: str, config_path: str, *, repository: Path | str, expected_head: str,
    execution_envelope: Any, target_dir: str | None = None, evidence_runner: Any = None,
) -> tuple[CompiledCommission, Any]:
    """Re-project pinned blobs at ``expected_head``, compare, then admit directly.

    This establishes every caller obligation ``operator.admit_parsed``'s
    docstring names -- strict parse of the corresponding objects, matching
    normalized paths and pinned texts actually re-read at ``expected_head``,
    repository identity, a full clean expected HEAD with resolved
    submodules, a nonexistent resolved state root, binding-source
    provenance, and operating-contract validity -- then calls
    ``admit_parsed`` once; it never reimplements admission.

    ``execution_envelope`` (a ``frontdoor.config.ExecutionEnvelope``) is
    resolved entirely outside this Commission's own pinned bytes; a
    different envelope never changes any compiled Commission byte compared
    here, only the envelope's own separate digest.
    """

    from .frontdoor.config import consumer_config_from_yaml
    from .frontdoor.request import consumer_request_from_yaml

    repository = Path(repository).resolve()
    if not repository.is_dir():
        raise CommissionError(f"repository is not a directory: {repository}")
    try:
        if repository_identity(repository) != repository:
            raise CommissionError("repository path is not the Git top-level")
    except GitPreflightError as error:
        raise CommissionError(str(error)) from error
    if not _SHA.fullmatch(expected_head):
        raise CommissionError("expected HEAD must be a full 40-character lowercase commit SHA")
    facts = observe_repository(repository)
    if facts.head != expected_head:
        raise CommissionError("current HEAD does not equal expected HEAD")
    if not facts.clean or facts.unresolved_submodules:
        raise CommissionError("worktree preconditions fail: repository must be clean with resolved submodules")

    request_text = _tree_text(repository, expected_head, request_path, context="Consumer Request")
    config_text = _tree_text(repository, expected_head, config_path, context="Consumer Config")
    request = consumer_request_from_yaml(request_text)
    config = consumer_config_from_yaml(config_text)

    chosen_dir = _path(target_dir or f"{config.governance_dir}/{request.commission_id}", context="compile target directory")
    # Re-resolve pinned/package sources before trusting a single compiled blob.
    compiled = compile_commission(request, config, repository=repository, target_dir=chosen_dir)
    # Every compiled byte is governing, not merely the five named top-level
    # files: a materialized package-resource component (for example
    # ``components/worker.txt``) must match exactly as much as the profile
    # or policy does, or admission would trust an unverified committed copy.
    for path, expected_text in compiled.texts.items():
        actual = _tree_text(repository, expected_head, path, context=f"compiled {path}")
        if actual != expected_text:
            raise CommissionError(f"compiled projection equivalence mismatch: {path}")

    observed_guidance = _tree_text(repository, expected_head, request.guidance.path, context="consumer guidance")
    pinned_guidance = _git_text(
        repository, request.guidance.source_commit, request.guidance.path, context="pinned consumer guidance"
    )
    if observed_guidance != pinned_guidance:
        raise CommissionError(f"consumer guidance differs from its pinned source object: {request.guidance.path}")

    agent_operating_contract = load_agent_operating_contract()
    with tempfile.TemporaryDirectory(prefix="wfc-consumer-commission-check-") as root:
        temp = Path(root)
        state_root = temp / "state"
        bindings_path = temp / "execution-bindings.yaml"
        bindings_path.write_text(execution_envelope.text, encoding="utf-8")

        input_paths = {
            name: _relative_path(path, context=name.replace("_", " "))
            for name, path in (
                ("work_item", compiled.paths["work_item"]), ("profile", compiled.paths["profile"]),
                ("policy", compiled.paths["policy"]), ("components", compiled.paths["components"]),
                ("task", compiled.paths["task"]),
            )
        }
        texts = {
            name: _pinned_text(repository, expected_head, path, context=name.replace("_", " "))
            for name, path in input_paths.items()
        }
        try:
            work_item = work_item_from_yaml(texts["work_item"])
            profile = workflow_profile_from_yaml(texts["profile"])
            policy = repository_policy_from_yaml(texts["policy"])
            manifest = component_manifest_from_yaml(texts["components"])
            binding_text = bindings_path.read_text(encoding="utf-8")
            execution_configuration = run_execution_configuration_from_yaml(binding_text)
        except (ValidationError, OSError, UnicodeError) as error:
            raise CommissionError(str(error)) from error
        try:
            result = admit_parsed(
                work_item=work_item, profile=profile, policy=policy, manifest=manifest,
                execution_configuration=execution_configuration,
                repository=repository, expected_head=expected_head, state_root=state_root,
                input_paths=input_paths, texts=texts, binding_source=bindings_path,
                agent_operating_contract=agent_operating_contract,
                durable_artifact=[], timeout_seconds=_VERIFY_COMMISSION_TIMEOUT_SECONDS,
                **({"evidence_runner": evidence_runner} if evidence_runner is not None else {}),
            )
        except OperatorPreflightError as error:
            raise CommissionError(f"operator admission refused compiled bundle: {error}") from error
    return compiled, result
