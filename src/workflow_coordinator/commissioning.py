"""Closed-program, non-authoritative Commission Intent rendering.

This module deliberately has no reducer integration and makes no provider
selection.  It turns the small ``direct@v1`` request vocabulary into ordinary
WFC governing files, then (only for ``verify_render_manifest``) asks the
existing operator preflight to admit those files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from yaml.constructor import ConstructorError

from .operator import OperatorPreflightError, _preflight
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
_COMPONENT_MANIFEST_PATH = "governance/self-development/components.yaml"
_PROGRAM_PATH = "governance/workflow-programs/direct/v1.yaml"
_POLICY_PATH = "governance/policy-bases/repository-standard-v1.yaml"


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
    personas = _mapping(data["personas"], context="Commission Intent.personas", required={"worker"})
    personas = {role: _text(value, context=f"Commission Intent.personas.{role}") for role, value in personas.items()}
    artifacts_raw = _mapping(data["durable_artifacts"], context="Commission Intent.durable_artifacts", required=set())
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
        program=_source_ref(data["program"], context="Commission Intent.program"),
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


def _repository_standard_v1_record(policy: Any) -> bool:
    """Return whether a parsed basis is the one immutable Direct-v1 basis.

    Identity alone is deliberately insufficient: every policy field below is a
    closed input to Direct-v1.  The render may narrow only the work-item scope
    ceiling, after this source record has been verified in full.
    """

    return (
        policy.schema_version == 3
        and policy.policy_id == "repository-standard"
        and policy.revision == "v1"
        and policy.scope == ("workflow-coordinator",)
        and policy.governance_paths == ("governance",)
        and policy.work_item_scope_ceiling == ("src", "tests", "scripts", "docs")
        and policy.max_autonomous_hops == 10
        and policy.actions == ("edit", "commit", "review")
        and set(policy.evidence_commands) == {"task-check"}
        and policy.evidence_commands["task-check"].argv == ("python", "-m", "pytest", "-q")
        and policy.evidence_commands["task-check"].timeout_seconds == 900
    )


def _load_sources(intent: CommissionIntent, repository: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str]:
    if (intent.program.id, intent.program.revision) != ("direct", "v1"):
        raise CommissionError("only the closed direct@v1 program is supported")
    if (intent.policy_basis.id, intent.policy_basis.revision) != ("repository-standard", "v1"):
        raise CommissionError("direct@v1 requires repository-standard@v1")
    if intent.components_ref.path != _COMPONENT_MANIFEST_PATH:
        raise CommissionError("direct@v1 requires the fixed self-development component manifest")
    program_text = _git_text(repository, intent.program.source_commit, _PROGRAM_PATH, context="program catalogue")
    policy_text = _git_text(repository, intent.policy_basis.source_commit, _POLICY_PATH, context="policy basis")
    components_text = _git_text(repository, intent.components_ref.source_commit, intent.components_ref.path, context="component manifest")
    program = _yaml_load(program_text, context="pinned program")
    if not isinstance(program, dict):
        raise CommissionError("pinned program must be a mapping")
    semantic_program = dict(program)
    semantic_program.pop("source", None)
    if semantic_program != _direct_template():
        raise CommissionError("pinned program does not equal the closed direct@v1 literal record")
    try:
        policy = repository_policy_from_yaml(policy_text)
        manifest = component_manifest_from_yaml(components_text)
    except ValidationError as error:
        raise CommissionError(str(error)) from error
    if not _repository_standard_v1_record(policy):
        raise CommissionError("pinned policy basis does not equal the immutable repository-standard@v1 record")
    if "worker" not in manifest:
        raise CommissionError("fixed component manifest does not define worker")
    return program, {"policy": policy, "manifest": manifest}, program_text, policy_text, components_text


def _yaml(data: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(data), sort_keys=False, allow_unicode=True)


def _task_text(intent: CommissionIntent) -> str:
    task = intent.task
    return (
        f"# {task.title}\n\n{task.body}\n\n## Acceptance\n\n{task.acceptance}\n\n"
        f"## Withheld authority\n\n{task.withheld_authority}\n\n## Default escalation\n\n{task.escalation_default}\n"
    )


def render_commission(intent: CommissionIntent, *, repository: Path | str, target_dir: str | None = None) -> RenderedCommission:
    """Render a closed direct@v1 projection without making an admission decision."""

    repository = Path(repository).resolve()
    if not repository.is_dir():
        raise CommissionError(f"repository is not a directory: {repository}")
    program, loaded, program_text, policy_text, components_text = _load_sources(intent, repository)
    policy = loaded["policy"]
    manifest = loaded["manifest"]
    if set(intent.personas) != {"worker"}:
        raise CommissionError("direct@v1 requires exactly the worker persona")
    if intent.evidence_profile not in program["evidence_profiles"]:
        raise CommissionError("Intent selects an undefined direct@v1 evidence profile")
    if intent.durable_artifacts:
        raise CommissionError("direct@v1 has no durable-artifact phases")
    if intent.max_autonomous_hops > policy.max_autonomous_hops:
        raise CommissionError("Intent hop limit exceeds the policy-basis ceiling")
    if any(not _within(path, tuple(policy.work_item_scope_ceiling)) for path in intent.scope):
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
        phase["authorized_scope"] = list(intent.scope) if phase.pop("scope") == "S" else phase["scope"]
    profile_phases["verification"]["evidence"] = list(program["evidence_profiles"][intent.evidence_profile]["evidence"])
    profile_data = {"schema_version": 2, "profile_id": f"{intent.commission_id}-direct-v1", "revision": "1", "phases": profile_phases}
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
        "profile_labels": ["program_id=direct", "program_revision=v1", f"program_source_commit={intent.program.source_commit}"],
        "durable_artifacts": {},
    }
    texts = {paths["work_item"]: _yaml(item_data), paths["profile"]: _yaml(profile_data), paths["policy"]: _yaml(policy_data), paths["task"]: _task_text(intent)}
    selected_components = {"worker": manifest["worker"].path}
    source_digests = {
        "intent": _sha(intent.raw_text), "program": _sha(program_text), "policy_basis": _sha(policy_text),
        "components": _sha(components_text),
        **{f"component:{name}": _sha(_git_text(repository, intent.components_ref.source_commit, path, context=f"component {name}")) for name, path in selected_components.items()},
    }
    manifest_data = {
        "schema_version": 1,
        "intent": {"sha256": source_digests["intent"]},
        "sources": {
            "program": {"id": intent.program.id, "revision": intent.program.revision, "source_commit": intent.program.source_commit, "path": _PROGRAM_PATH, "sha256": source_digests["program"]},
            "policy_basis": {"id": intent.policy_basis.id, "revision": intent.policy_basis.revision, "source_commit": intent.policy_basis.source_commit, "path": _POLICY_PATH, "sha256": source_digests["policy_basis"]},
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
        bindings.write_text(
            "schema_version: 1\nbindings:\n  worker: {adapter: codex_cli, model: commission-check, reasoning: medium}\n",
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
