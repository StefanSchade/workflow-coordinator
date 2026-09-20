"""Focused A3 tests for the sole Coordinator-owned operating contract."""

from __future__ import annotations

import hashlib
import inspect

import pytest

from scripts.workflow_coordinator.adapter import Invocation
from scripts.workflow_coordinator import prompt
from scripts.workflow_coordinator.operator import run_serial_hops
from scripts.workflow_coordinator.prompt import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    RESOURCE,
    REVIEWED_SHA256,
    AgentOperatingContract,
    CoordinatorResourceError,
    PromptComponent,
    assemble_prompt,
    load_agent_operating_contract,
)
from scripts.workflow_coordinator.slice4 import run_real_one_hop
from scripts.workflow_coordinator.vertical import run_one_hop
from tests.utils.workflow_coordinator import phase, profile, route, run_for

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


class _Resource:
    def __init__(self, content: bytes | OSError):
        self.content = content

    def __truediv__(self, _part: str):
        return self

    def read_bytes(self) -> bytes:
        if isinstance(self.content, OSError):
            raise self.content
        return self.content


def _package(contract: AgentOperatingContract):
    workflow = profile({"work": phase({"done": route("complete")})})
    current = workflow.phases["work"]
    components = {
        "worker": PromptComponent("worker", "worker.txt", "HEAD", "FIRST CONSUMER"),
        "testing": PromptComponent("testing", "testing.txt", "HEAD", "SECOND CONSUMER"),
    }
    return assemble_prompt(
        run_for(workflow),
        current,
        components,
        agent_operating_contract=contract,
        invocation=Invocation("RUN-1", "H001", "INV-1"),
        task_payload="semantic task",
    )


def test_production_loader_has_exact_identity_prologue_digest_and_repeatability():
    first = load_agent_operating_contract()
    second = load_agent_operating_contract()

    assert (CONTRACT_ID, CONTRACT_VERSION, RESOURCE) == (
        "workflow_coordinator.agent_operating_contract",
        1,
        "resources/agent_operating_contract_v1.txt",
    )
    assert first.text.startswith(
        "[agent_operating_contract]\n"
        "contract_id=workflow_coordinator.agent_operating_contract\n"
        "version=1\n"
    )
    assert first == second and first is not second
    assert first.sha256 == REVIEWED_SHA256
    assert hashlib.sha256(first.text.encode("utf-8")).hexdigest() == REVIEWED_SHA256
    assert first.provenance() == second.provenance() == {
        "contract_id": CONTRACT_ID,
        "version": CONTRACT_VERSION,
        "resource": RESOURCE,
        "byte_count": 1688,
        "sha256": REVIEWED_SHA256,
    }
    normalized = " ".join(first.text.split())
    for required in (
        "authorized_scope",
        "git commit --amend",
        "Read-only Git inspection is allowed",
        "prevail over any conflicting consumer task or component text",
        "neither proof that a commit occurred nor an expansion of authority",
        "structured-result envelope is mandatory",
        "not Coordinator-trusted evidence",
        "public-surface authority",
        "trust-boundary authority",
    ):
        assert required in normalized
    assert "authorized_actions" not in first.text
    assert "human_gates" not in first.text


def test_all_four_internal_seams_require_keyword_only_contract_values():
    for seam in (run_serial_hops, run_real_one_hop, run_one_hop, assemble_prompt):
        parameter = inspect.signature(seam).parameters["agent_operating_contract"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


def test_prompt_places_complete_contract_once_after_header_before_consumers_and_footer():
    contract = load_agent_operating_contract()
    package = _package(contract)

    assert package.text.count(contract.text) == 1
    assert package.text.index("[identity]") < package.text.index(contract.text)
    assert package.text.index(contract.text) < package.text.index("FIRST CONSUMER")
    assert package.text.rstrip().endswith(
        "[result_output_contract]\n"
        "Return one JSON structured_result_v1 envelope; prose cannot replace fields."
    )
    assert package.agent_operating_contract == contract.provenance()


def test_contract_is_not_subject_to_component_omission_limits():
    contract = load_agent_operating_contract()
    workflow = profile({"work": phase({"done": route("complete")})})
    components = {
        "worker": PromptComponent(
            "worker", "worker.txt", "HEAD", "x" * (prompt.COMPONENT_LIMIT_BYTES + 1)
        ),
        "testing": PromptComponent("testing", "testing.txt", "HEAD", "testing"),
    }
    package = assemble_prompt(
        run_for(workflow),
        workflow.phases["work"],
        components,
        agent_operating_contract=contract,
        invocation=Invocation("RUN-1", "H001", "INV-1"),
        task_payload="task",
    )

    assert package.text.count(contract.text) == 1
    assert package.omissions[0]["kind"] == "component"


@pytest.mark.parametrize("content", [b"altered", b""])
def test_digest_mismatch_refuses_with_the_stable_resource_error(monkeypatch, content):
    monkeypatch.setattr(prompt.resources, "files", lambda _package: _Resource(content))
    with pytest.raises(CoordinatorResourceError, match="resource is unavailable"):
        load_agent_operating_contract()


def test_missing_package_resource_has_no_repository_or_cwd_fallback(monkeypatch, tmp_path):
    fallback = tmp_path / RESOURCE
    fallback.parent.mkdir()
    fallback.write_bytes(load_agent_operating_contract().text.encode("utf-8"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        prompt.resources,
        "files",
        lambda _package: _Resource(FileNotFoundError("missing package resource")),
    )

    with pytest.raises(CoordinatorResourceError, match="resource is unavailable"):
        load_agent_operating_contract()


def test_internal_value_mismatch_refuses_before_prompt_is_returned():
    with pytest.raises(CoordinatorResourceError, match="resource is unavailable"):
        _package(AgentOperatingContract("tampered"))
