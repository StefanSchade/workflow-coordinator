"""The parsed admission seam retains the operator's checks and result."""

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from workflow_coordinator import operator
from workflow_coordinator.serialization import (
    component_manifest_from_yaml,
    repository_policy_from_yaml,
    run_execution_configuration_from_yaml,
    work_item_from_yaml,
    workflow_profile_from_yaml,
)
from tests.support import route
from tests.unit.test_operator import args, git, repository  # noqa: F401 -- pytest fixture re-exported by import

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


@pytest.fixture
def parsed_inputs(repository: Path, tmp_path: Path):  # noqa: F811 -- pytest fixture param shadows the re-exported import
    command = args(repository, tmp_path / "state", "unused")
    namespace = operator._parse_args(command)
    paths = {name: getattr(namespace, name) for name in
             ("work_item", "profile", "policy", "components", "task")}
    texts = {name: (repository / path).read_text() for name, path in paths.items()}
    binding_source = Path(namespace.execution_bindings).resolve()
    return namespace, dict(
        work_item=work_item_from_yaml(texts["work_item"]),
        profile=workflow_profile_from_yaml(texts["profile"]),
        policy=repository_policy_from_yaml(texts["policy"]),
        manifest=component_manifest_from_yaml(texts["components"]),
        execution_configuration=run_execution_configuration_from_yaml(binding_source.read_text()),
        repository=repository.resolve(), expected_head=git(repository, "rev-parse", "HEAD"),
        state_root=Path(namespace.state_root).resolve(), input_paths=paths, texts=texts,
        binding_source=binding_source,
        agent_operating_contract=operator.load_agent_operating_contract(),
        durable_artifact=namespace.durable_artifact, timeout_seconds=namespace.timeout_seconds,
    )


def test_direct_parsed_admission_matches_preflight(parsed_inputs):
    namespace, inputs = parsed_inputs
    direct = operator.admit_parsed(**inputs)
    assert direct == operator._preflight(namespace)
    assert direct[8]["worker"].content == "worker guidance\n"
    assert direct[11]["worker.txt"] == operator._governing_digest("worker guidance\n")
    assert not inputs["state_root"].exists()


def test_profile_reference_rejection_precedes_role_validation(parsed_inputs):
    _, inputs = parsed_inputs
    inputs["work_item"] = replace(inputs["work_item"], profile_ref="other.yml", personas={})
    with pytest.raises(operator.OperatorPreflightError, match="WorkItem.profile_ref does not identify"):
        operator.admit_parsed(**inputs)
    inputs["work_item"] = replace(inputs["work_item"], profile_ref="profile.yml")
    with pytest.raises(operator.OperatorPreflightError, match="WorkItem.personas must exactly match"):
        operator.admit_parsed(**inputs)


def test_evidence_preparation_failure_precedes_timeout(parsed_inputs, monkeypatch):
    _, inputs = parsed_inputs
    inputs["timeout_seconds"] = 0

    def fail(*args, **kwargs):
        raise ValueError("evidence preparation failed")

    monkeypatch.setattr(operator, "prepare_evidence_commands", fail)
    with pytest.raises(operator.OperatorPreflightError, match="evidence preparation failed") as error:
        operator.admit_parsed(**inputs)
    assert isinstance(error.value.__cause__, ValueError)


def test_phase_envelope_admission_requires_exact_executing_phases_and_one_adapter(parsed_inputs):
    _, inputs = parsed_inputs
    workflow = inputs["profile"]
    first = inputs["work_item"].initial_phase
    inputs["profile"] = replace(workflow, phases={**workflow.phases, "unused": workflow.phases[first]})
    executing = workflow.executing_phase_roles(first)
    bindings = {
        name: {"adapter": "codex_cli", "model": f"{name}-model", "reasoning": "high"}
        for name in executing
    }

    def admit(phase_bindings):
        config = run_execution_configuration_from_yaml(yaml.safe_dump({
            "schema_version": 2, "phase_bindings": phase_bindings,
        }))
        return operator.admit_parsed(**{**inputs, "execution_configuration": config})

    assert set(admit(bindings)[-1].phase_bindings) == set(executing)
    with pytest.raises(operator.OperatorPreflightError, match=f"missing phases: {first}"):
        admit({name: binding for name, binding in bindings.items() if name != first})
    with pytest.raises(operator.OperatorPreflightError, match="unknown phases: unused"):
        admit({**bindings, "unused": bindings[first]})

    existing = workflow.phases[first]
    second = "extra"
    inputs["profile"] = replace(workflow, phases={
        **workflow.phases, first: replace(existing, routes={second: route("goto", second)}),
        second: existing,
    })
    with pytest.raises(operator.OperatorPreflightError, match="mixed execution adapters"):
        admit({**bindings, second: {**bindings[first], "adapter": "claude_cli"}})


def test_engine_argv_digest_rejects_changed_phase_envelope_before_admission(parsed_inputs):
    namespace, inputs = parsed_inputs
    source = inputs["binding_source"]
    phase = inputs["work_item"].initial_phase
    pinned = yaml.safe_dump({"schema_version": 2, "phase_bindings": {
        phase: {"adapter": "codex_cli", "model": "pinned-model", "reasoning": "high"},
    }})
    namespace.execution_bindings_sha256 = run_execution_configuration_from_yaml(pinned).sha256
    source.write_text(pinned.replace("pinned-model", "other-model"))
    with pytest.raises(operator.OperatorPreflightError, match="execution envelope digest changed"):
        operator._preflight(namespace)
    assert not inputs["state_root"].exists()
