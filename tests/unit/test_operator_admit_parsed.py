"""The parsed admission seam retains the operator's checks and result."""

from dataclasses import replace
from pathlib import Path

import pytest

from workflow_coordinator import operator
from workflow_coordinator.serialization import (
    component_manifest_from_yaml,
    repository_policy_from_yaml,
    run_execution_configuration_from_yaml,
    work_item_from_yaml,
    workflow_profile_from_yaml,
)
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
