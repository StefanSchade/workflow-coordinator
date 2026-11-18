"""Consumer-neutral Commission re-verification and admission (BP2b).

``verify_commission`` is the only caller of ``operator.admit_parsed`` on the
consumer-neutral path.  It establishes every caller obligation that
docstring names -- strict parse of the corresponding objects, matching
normalized paths and pinned texts actually re-read at ``expected_head``,
repository identity, a full clean expected HEAD with resolved submodules, a
nonexistent resolved state root, binding-source provenance, and
operating-contract validity -- then calls ``admit_parsed`` once; it never
reimplements admission.  It lives here rather than in ``commissioning``
because it is the only BP2b piece that must parse ``ConsumerRequest``/
``ConsumerConfig`` text, and the kernel import-direction invariant
(``tests/architecture/test_bp1_identity.py``) refuses any kernel module
naming ``frontdoor``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..commissioning import CommissionError, CompiledCommission, _SHA, _git_text, _path, _tree_text, compile_commission
from ..git_facts import GitPreflightError, observe_repository, repository_identity
from ..operator import OperatorPreflightError, _pinned_text, _relative_path, admit_parsed
from ..prompt import load_agent_operating_contract
from ..serialization import (
    ValidationError,
    component_manifest_from_yaml,
    repository_policy_from_yaml,
    run_execution_configuration_from_yaml,
    workflow_profile_from_yaml,
    work_item_from_yaml,
)
from .config import consumer_config_from_yaml
from .request import consumer_request_from_yaml

_VERIFY_COMMISSION_TIMEOUT_SECONDS = 900.0


def verify_commission(
    request_path: str, config_path: str, *, repository: Path | str, expected_head: str,
    execution_envelope: Any, target_dir: str | None = None, evidence_runner: Any = None,
) -> tuple[CompiledCommission, Any]:
    """Re-project pinned blobs at ``expected_head``, compare, then admit directly.

    ``execution_envelope`` (a ``frontdoor.config.ExecutionEnvelope``) is
    resolved entirely outside this Commission's own pinned bytes; a
    different envelope never changes any compiled Commission byte compared
    here, only the envelope's own separate digest.
    """

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
