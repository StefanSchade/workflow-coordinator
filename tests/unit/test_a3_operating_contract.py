"""Focused A3 tests for the sole Coordinator-owned operating contract."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import fields

import pytest

from workflow_coordinator.adapter import Invocation
from workflow_coordinator import prompt
from workflow_coordinator.model import RepositoryPolicy, WorkItem, WorkflowProfile
from workflow_coordinator.operator import run_serial_hops
from workflow_coordinator.prompt import (
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
from workflow_coordinator.slice4 import run_real_one_hop
from workflow_coordinator.vertical import run_one_hop
from tests.support import phase, profile, route, run_for

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")

LEGACY_V1_SHA256 = "058fde8651898afa170160b35d4c6969459130652a69cac1ae2f6112d8e5a194"
LEGACY_V2_SHA256 = "c5f3657812b10a2a5367382c5b02db7586bb1917bae3674519c05b130fd0029b"
LEGACY_V3_SHA256 = "b5ae6d229afb15f02c9915ffd818a36a752165b3c6a635ac7a1a71a43f49390f"
V4_INVOCATION_FACTS = (
    b"The Coordinator projects these invocation facts: WorkItem ID, Run ID, Hop ID,\n"
    b"invocation ID, base HEAD, phase, authorized_scope, authorized_actions,\n"
    b"permitted_routes, finding_authority, evidence names, and output_contract. The\n"
    b"[workflow_policy] block supplies durable_artifact. The result schema is supplied\n"
    b"through the adapter.\n"
)
V3_INVOCATION_FACTS = (
    b"The Coordinator projects these invocation facts: WorkItem ID, Run ID, Hop ID,\n"
    b"invocation ID, base HEAD, phase, authorized_scope, authorized_actions,\n"
    b"permitted_routes, evidence names, and output_contract. The [workflow_policy]\n"
    b"block supplies durable_artifact. The result schema is supplied through the\n"
    b"adapter.\n"
)
V3_FINDING_IDENTITY_BLOCK = (
    b"\nfinding_id is a run-local authoritative identifier. A non-null finding_id may\n"
    b"be used only when that exact ID is present in the authoritative Finding state\n"
    b"projected for the current Run. Labels or identifiers in task prose, historical\n"
    b"review artifacts, documents, evidence references, commit messages, filenames,\n"
    b"or other repository material are not current-Run Finding IDs merely because\n"
    b"they look like one. Such historical labels may be cited as evidence or\n"
    b"provenance, but must not be submitted as existing Finding IDs unless the\n"
    b"current Run contains that authoritative ID.\n"
    b"\n"
    b"Propose a genuinely new Finding with finding_id = null and let the Coordinator\n"
    b"allocate its run-local ID. If the current Run has no authoritative Findings,\n"
    b"do not resolve or otherwise disposition a historical Finding label as current\n"
    b"Run state. Do not fabricate a Finding delta merely to record that implementation\n"
    b"work addressed a historical review concern; record that fact in summary and/or\n"
    b"evidence_refs unless it represents a genuinely new current-Run Finding.\n"
)
V4_FINDING_AUTHORITY_BLOCK = (
    b"\n"
    b"Finding identity, Finding evidence, and Finding disposition authority are\n"
    b"distinct. A run-local finding_id identifies an authoritative current-Run\n"
    b"Finding. A Hop may report evidence or summary information concerning a Finding\n"
    b"as permitted by the result protocol. Only operations named by the projected\n"
    b"finding_authority may authoritatively change Finding state. An empty projected\n"
    b"value, finding_authority=, means no authoritative Finding disposition operation\n"
    b"is permitted in this Hop, including resolve, residual, supersede, or reopen.\n"
    b"\n"
    b"Correction, edit, or commit authority does not imply Finding disposition\n"
    b"authority. In a disposition-free correction Hop, perform the bounded correction,\n"
    b"report correction evidence through normal result evidence_refs and summary\n"
    b"fields, and normally submit no Finding delta merely to state that the correction\n"
    b"addressed a Finding. Request the configured next route and leave authoritative\n"
    b"Finding disposition to a later phase that has the configured authority. A\n"
    b"same-state citation of an existing open Finding may add evidence without\n"
    b"disposition authority, but an unnecessary Finding-ledger update is less clear\n"
    b"than reporting correction evidence in the normal result fields. Workflow\n"
    b"choreography remains configuration-driven; no particular phase name or route is\n"
    b"implied by this contract.\n"
)


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


def _reconstruct_legacy_v3_bytes() -> bytes:
    current = load_agent_operating_contract().text.encode("utf-8")
    assert current.count(b"version=4\n") == 1
    assert current.count(V4_INVOCATION_FACTS) == 1
    assert current.count(V4_FINDING_AUTHORITY_BLOCK) == 1
    return (
        current.replace(b"version=4\n", b"version=3\n", 1)
        .replace(V4_INVOCATION_FACTS, V3_INVOCATION_FACTS, 1)
        .replace(V4_FINDING_AUTHORITY_BLOCK, b"", 1)
    )


def _reconstruct_legacy_v2_bytes() -> bytes:
    current = _reconstruct_legacy_v3_bytes()
    assert current.count(b"version=3\n") == 1
    assert current.count(V3_FINDING_IDENTITY_BLOCK) == 1
    return current.replace(b"version=3\n", b"version=2\n", 1).replace(
        V3_FINDING_IDENTITY_BLOCK, b"", 1
    )


def test_production_loader_has_exact_identity_prologue_digest_and_repeatability():
    first = load_agent_operating_contract()
    second = load_agent_operating_contract()

    assert (CONTRACT_ID, CONTRACT_VERSION, RESOURCE) == (
        "workflow_coordinator.agent_operating_contract",
        4,
        "resources/agent_operating_contract_v4.txt",
    )
    assert first.text.startswith(
        "[agent_operating_contract]\n"
        "contract_id=workflow_coordinator.agent_operating_contract\n"
        "version=4\n"
    )
    assert first == second and first is not second
    assert first.sha256 == REVIEWED_SHA256
    assert hashlib.sha256(first.text.encode("utf-8")).hexdigest() == REVIEWED_SHA256
    assert first.provenance() == second.provenance() == {
        "contract_id": CONTRACT_ID,
        "version": CONTRACT_VERSION,
        "resource": RESOURCE,
        "byte_count": 4842,
        "sha256": REVIEWED_SHA256,
    }
    normalized = " ".join(first.text.split())
    for required in (
        "authorized_scope",
        "authorized_actions describe the allowed effects and work during the current Hop",
        "permitted_routes describe only the allowed disposition or exit from the current Hop",
        "A sole permitted route named review does not make the current Hop review-only",
        "Evidence names identify Coordinator-owned trusted evidence",
        "The Coordinator prepares and executes trusted evidence separately according to admitted policy",
        "Agent-side tests and checks may be useful exploratory validation, but they are not Coordinator-trusted evidence",
        "Failure to resolve or execute the same authored evidence command in the agent shell is not, by itself, a failure or unavailability of Coordinator-trusted evidence",
        "must not, by itself, cause an operational escalation",
        "finding_id is a run-local authoritative identifier",
        "A non-null finding_id may be used only when that exact ID is present in the authoritative Finding state projected for the current Run",
        "historical review artifacts, documents, evidence references, commit messages, filenames",
        "are not current-Run Finding IDs merely because they look like one",
        "historical labels may be cited as evidence or provenance, but must not be submitted as existing Finding IDs unless the current Run contains that authoritative ID",
        "Propose a genuinely new Finding with finding_id = null",
        "let the Coordinator allocate its run-local ID",
        "If the current Run has no authoritative Findings, do not resolve or otherwise disposition a historical Finding label as current Run state",
        "Do not fabricate a Finding delta merely to record that implementation work addressed a historical review concern",
        "record that fact in summary and/or evidence_refs",
        "Finding identity, Finding evidence, and Finding disposition authority are distinct",
        "A run-local finding_id identifies an authoritative current-Run Finding",
        "A Hop may report evidence or summary information concerning a Finding",
        "Only operations named by the projected finding_authority may authoritatively change Finding state",
        "finding_authority=, means no authoritative Finding disposition operation is permitted in this Hop",
        "Correction, edit, or commit authority does not imply Finding disposition authority",
        "normally submit no Finding delta merely to state that the correction addressed a Finding",
        "Request the configured next route",
        "Workflow choreography remains configuration-driven",
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
    assert "human_gates" not in first.text


def test_legacy_v3_contract_bytes_remain_exact_and_cannot_pass_v4_digest(monkeypatch):
    legacy = _reconstruct_legacy_v3_bytes()
    assert len(legacy) == 3490
    assert hashlib.sha256(legacy).hexdigest() == LEGACY_V3_SHA256
    assert legacy.startswith(
        b"[agent_operating_contract]\n"
        b"contract_id=workflow_coordinator.agent_operating_contract\n"
        b"version=3\n"
    )
    assert LEGACY_V3_SHA256 != REVIEWED_SHA256

    monkeypatch.setattr(prompt.resources, "files", lambda _package: _Resource(legacy))
    with pytest.raises(CoordinatorResourceError, match="resource is unavailable"):
        load_agent_operating_contract()


def test_legacy_v2_contract_bytes_remain_exact_and_cannot_pass_v4_digest(monkeypatch):
    legacy = _reconstruct_legacy_v2_bytes()
    assert len(legacy) == 2445
    assert hashlib.sha256(legacy).hexdigest() == LEGACY_V2_SHA256
    assert legacy.startswith(
        b"[agent_operating_contract]\n"
        b"contract_id=workflow_coordinator.agent_operating_contract\n"
        b"version=2\n"
    )
    assert LEGACY_V2_SHA256 != REVIEWED_SHA256

    monkeypatch.setattr(prompt.resources, "files", lambda _package: _Resource(legacy))
    with pytest.raises(CoordinatorResourceError, match="resource is unavailable"):
        load_agent_operating_contract()


def test_legacy_v1_contract_bytes_remain_reconstructable_from_v2():
    v2 = _reconstruct_legacy_v2_bytes().decode("utf-8")
    legacy = v2.replace("version=2\n", "version=1\n", 1).replace(
        "phase, authorized_scope, authorized_actions,\n"
        "permitted_routes, evidence names, and output_contract. The [workflow_policy]\n"
        "block supplies durable_artifact. The result schema is supplied through the\n"
        "adapter.",
        "phase, authorized_scope, permitted_routes, evidence\n"
        "names, and output_contract. The [workflow_policy] block supplies\n"
        "durable_artifact. The result schema is supplied through the adapter.",
        1,
    ).replace(
        "\n"
        "authorized_actions describe the allowed effects and work during the current\n"
        "Hop. permitted_routes describe only the allowed disposition or exit from the\n"
        "current Hop. A sole permitted route named review does not make the current Hop\n"
        "review-only.\n"
        "\n"
        "Evidence names identify Coordinator-owned trusted evidence. The Coordinator\n"
        "prepares and executes trusted evidence separately according to admitted policy.\n"
        "Agent-side tests and checks may be useful exploratory validation, but they are\n"
        "not Coordinator-trusted evidence. Failure to resolve or execute the same\n"
        "authored evidence command in the agent shell is not, by itself, a failure or\n"
        "unavailability of Coordinator-trusted evidence and must not, by itself, cause\n"
        "an operational escalation.\n",
        "",
        1,
    ).encode("utf-8")
    assert hashlib.sha256(legacy).hexdigest() == LEGACY_V1_SHA256
    assert LEGACY_V1_SHA256 != REVIEWED_SHA256


def test_all_four_internal_seams_require_keyword_only_contract_values():
    for seam in (run_serial_hops, run_real_one_hop, run_one_hop, assemble_prompt):
        parameter = inspect.signature(seam).parameters["agent_operating_contract"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


def test_consumer_models_and_loader_expose_no_contract_selector_or_replacement():
    assert not inspect.signature(load_agent_operating_contract).parameters
    for consumer_model in (WorkItem, WorkflowProfile, RepositoryPolicy, PromptComponent):
        assert "agent_operating_contract" not in {
            field.name for field in fields(consumer_model)
        }


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
