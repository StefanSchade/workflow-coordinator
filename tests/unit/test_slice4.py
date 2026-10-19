"""Deterministic Slice-4 external-state and real-process composition tests."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from workflow_coordinator.adapter import AgentExecutionOutcome, Invocation, validated_result
from workflow_coordinator.codex_adapter import CodexCliAdapter, structured_result_envelope_openai_schema
from workflow_coordinator.serialization import (
    EXISTING_FINDING_DELTA_KEYS,
    NEW_FINDING_DELTA_KEYS,
    ROUTING_RESULT_REQUIRED_KEYS,
    execution_evidence_from_json,
    load_run,
)
from workflow_coordinator.slice4 import (
    Slice4Error, invoke_codex, register_checkout, run_real_one_hop, validate_checkout,
)
from tests.support import (
    charge_hop,
    coverage,
    phase,
    prepared_evidence,
    profile,
    result,
    route,
    run_for,
    simulated_observation,
    tracked_convergence_run,
)
from workflow_coordinator.prompt import PromptComponent, load_agent_operating_contract
from workflow_coordinator.model import (
    DispatchMarker,
    ExecutionEvidenceDocument,
    ResultContractKind,
)
from workflow_coordinator.persistence import (
    trusted_qualification_index_for_run,
    write_dispatch_marker,
)
from workflow_coordinator.reducer import ReductionError, derive_convergence_facts, reduce_result


pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(("git", "init", str(repo)), check=True, capture_output=True)
    for key, value in (("user.email", "fixture@example.invalid"), ("user.name", "Fixture")):
        subprocess.run(("git", "-C", str(repo), "config", key, value), check=True)
    (repo / "PROOF.md").write_text("before\n")
    subprocess.run(("git", "-C", str(repo), "add", "PROOF.md"), check=True)
    subprocess.run(("git", "-C", str(repo), "commit", "-m", "docs: baseline"), check=True, capture_output=True)
    return repo


def _components() -> dict[str, PromptComponent]:
    return {"worker": PromptComponent("worker", "worker.txt", "PIN", "worker"),
            "testing": PromptComponent("testing", "testing.txt", "PIN", "testing")}


def _fake(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake-codex"
    executable.write_text("#!/usr/bin/env python3\n" + body)
    executable.chmod(0o755)
    return executable


_PERMITTED_ROUTES = ("done",)


def _expected() -> Invocation:
    return Invocation("RUN-1", "H001", "INV-1")


def _assert_matches_schema(value, schema):
    if "const" in schema:
        assert value == schema["const"]
    if "anyOf" in schema:
        assert any(_matches_schema(value, branch) for branch in schema["anyOf"])
        return
    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
        assert any(_matches_type(value, candidate) for candidate in expected_types)
    if "enum" in schema:
        assert value in schema["enum"]
    if schema.get("type") == "object":
        assert isinstance(value, dict)
        properties = schema["properties"]
        assert set(schema["required"]).issubset(value)
        if schema["additionalProperties"] is False:
            assert set(value).issubset(properties)
        for key, child_schema in properties.items():
            if key in value:
                _assert_matches_schema(value[key], child_schema)
    if schema.get("type") == "array":
        assert isinstance(value, list)
        for item in value:
            _assert_matches_schema(item, schema["items"])


def _matches_schema(value, schema):
    try:
        _assert_matches_schema(value, schema)
    except AssertionError:
        return False
    return True


def _matches_type(value, expected_type):
    return {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }[expected_type]()


def _walk_object_schemas(schema):
    if schema.get("type") == "object":
        yield schema
    for child in schema.get("properties", {}).values():
        yield from _walk_object_schemas(child)
    if "items" in schema:
        yield from _walk_object_schemas(schema["items"])
    for child in schema.get("anyOf", []):
        yield from _walk_object_schemas(child)


def _known_good_envelope(result_contract_kind=ResultContractKind.GENERIC_ROUTING_RESULT):
    return {
        "schema_version": 1,
        "run_id": "RUN-1",
        "hop_id": "H001",
        "invocation_id": "INV-1",
        "result": {
            "schema_version": 1,
            **(
                {"status": "completed"}
                if result_contract_kind is ResultContractKind.WORK_RESULT
                else {"outcome": "completed"}
            ),
            "requested_route": "done",
            "scope_changed": False,
            "requires_human": False,
            "escalation": None,
            "findings": [],
            "claimed_commits": [],
            "commit_intent": None,
            "evidence_refs": [],
            "summary": "done",
        },
    }


def test_codex_projection_makes_every_object_strict_and_complete():
    schema = structured_result_envelope_openai_schema(_expected(), permitted_routes=_PERMITTED_ROUTES)
    result = schema["properties"]["result"]
    escalation = result["properties"]["escalation"]["anyOf"][1]
    finding = result["properties"]["findings"]["items"]
    new_finding, existing_finding = finding["anyOf"]
    object_schemas = list(_walk_object_schemas(schema))
    assert all(candidate in object_schemas for candidate in (schema, result, escalation, new_finding, existing_finding))
    for object_schema in object_schemas:
        assert object_schema["additionalProperties"] is False
        assert set(object_schema["required"]) == set(object_schema["properties"])


def test_codex_projection_tracks_routing_result_validator_contract():
    result = structured_result_envelope_openai_schema(
        _expected(), permitted_routes=_PERMITTED_ROUTES
    )["properties"]["result"]
    assert set(result["required"]) == set(ROUTING_RESULT_REQUIRED_KEYS)


def test_codex_projection_tracks_complete_finding_delta_contract():
    schema = structured_result_envelope_openai_schema(_expected(), permitted_routes=_PERMITTED_ROUTES)
    finding = schema["properties"]["result"]["properties"]["findings"]["items"]
    new_finding, existing_finding = finding["anyOf"]
    assert set(new_finding["required"]) == set(NEW_FINDING_DELTA_KEYS)
    assert new_finding["properties"]["finding_id"] == {"type": "null"}
    assert set(existing_finding["required"]) == set(EXISTING_FINDING_DELTA_KEYS)
    assert existing_finding["properties"]["finding_id"] == {"type": "string"}
    assert "invariant" not in existing_finding["properties"]
    assert "blocking" not in existing_finding["properties"]
    new_envelope = _known_good_envelope()
    new_envelope["result"]["findings"] = [{
        "finding_id": None, "invariant": "new invariant", "blocking": True,
        "proposed_state": "open", "evidence_refs": [], "successor_ref": None,
        "new_material_evidence": False,
    }]
    existing_envelope = _known_good_envelope()
    existing_envelope["result"]["findings"] = [{
        "finding_id": "F001", "proposed_state": "resolved", "evidence_refs": [],
        "successor_ref": None, "new_material_evidence": False,
    }]
    assert _matches_schema(new_envelope, schema)
    assert _matches_schema(existing_envelope, schema)


def test_invoke_codex_writes_the_canonical_structured_result_schema(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    executable = _fake(tmp_path, "pass\n")
    invoke_codex(repository=repository, state_root=state, association=association, prompt="x", expected=_expected(), timeout_seconds=2, model="explicit-test-model", reasoning_effort="high", permitted_routes=_PERMITTED_ROUTES, executable=str(executable))
    emitted = json.loads((state / "checkouts" / association.checkout_id / "structured-result.schema.json").read_text())
    assert emitted == structured_result_envelope_openai_schema(_expected(), permitted_routes=_PERMITTED_ROUTES)


def test_known_good_envelope_passes_adapter_validation_and_provider_schema():
    envelope = _known_good_envelope()
    assert validated_result(json.dumps(envelope), _expected()).summary == "done"
    _assert_matches_schema(
        envelope,
        structured_result_envelope_openai_schema(_expected(), permitted_routes=_PERMITTED_ROUTES),
    )


def test_state_root_nesting_is_rejected_before_any_trusted_write(repository, tmp_path):
    inside = repository / "bad-state"
    with pytest.raises(Slice4Error, match="disjoint"):
        register_checkout(inside, repository)
    assert not (inside / "checkout-association.json").exists()
    assert not (inside / "checkouts").exists()
    with pytest.raises(Slice4Error, match="disjoint"):
        register_checkout(tmp_path, repository)


def test_association_is_stable_and_malformed_or_git_mismatched_forms_fail_closed(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    assert validate_checkout(state, repository) == association
    mapping = state / "checkout-association.json"
    for malformed in ("{", json.dumps({"schema_version": 2}), json.dumps({"schema_version": 1, "checkout_id": 1})):
        mapping.write_text(malformed)
        with pytest.raises(Slice4Error):
            validate_checkout(state, repository)
    mapping.write_text(json.dumps({"schema_version": 1, **association.__dict__, "common_dir": "/wrong"}))
    with pytest.raises(Slice4Error, match="mismatch"):
        validate_checkout(state, repository)


def test_linked_worktree_git_dir_and_common_dir_are_recorded_and_checked(repository, tmp_path):
    linked = tmp_path / "linked"
    subprocess.run(("git", "-C", str(repository), "worktree", "add", "-b", "linked", str(linked)), check=True, capture_output=True)
    association = register_checkout(tmp_path / "external", linked)
    assert association.git_dir != association.common_dir
    assert validate_checkout(tmp_path / "external", linked) == association


@pytest.mark.parametrize("payload", ["{", '{"schema_version":1,"run_id":"wrong"}'])
def test_nonempty_invalid_result_is_not_established(repository, tmp_path, payload):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    executable = _fake(tmp_path, "import pathlib,sys\np=pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]); p.write_text(" + repr(payload) + ")\n")
    outcome = invoke_codex(repository=repository, state_root=state, association=association, prompt="x", expected=_expected(), timeout_seconds=2, model="explicit-test-model", reasoning_effort="high", permitted_routes=_PERMITTED_ROUTES, executable=str(executable))
    assert outcome.candidate_result == payload


def test_nonzero_without_result_is_uncertain(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    executable = _fake(tmp_path, "import sys\nsys.exit(7)\n")
    outcome = invoke_codex(repository=repository, state_root=state, association=association, prompt="x", expected=_expected(), timeout_seconds=2, model="explicit-test-model", reasoning_effort="high", permitted_routes=_PERMITTED_ROUTES, executable=str(executable))
    assert outcome.returncode == 7 and outcome.candidate_result is None


def test_real_vertical_composition_uses_fake_cli_and_existing_reducer(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    script = '''import json, pathlib, subprocess, sys
args=sys.argv[1:]; repo=args[args.index("--cd")+1]; output=pathlib.Path(args[args.index("--output-last-message")+1])
(output.parent / "argv.json").write_text(json.dumps(args))
pathlib.Path(repo, "PROOF.md").write_text("after\\n")
output.write_text(json.dumps({"schema_version":1,"run_id":"RUN-1","hop_id":"H001","invocation_id":"INV-1","result":{"schema_version":1,"status":"completed","requested_route":"done","scope_changed":False,"requires_human":False,"escalation":None,"findings":[],"claimed_commits":[],"commit_intent":{"paths":["PROOF.md"],"subject":"docs(workflow): WI-1 H001 fixture"},"evidence_refs":[],"summary":"done"}}))
'''
    executable = _fake(tmp_path, script)
    workflow = profile({"work": phase({"done": route("complete")})})
    workflow = replace(workflow, phases={"work": replace(workflow.phases["work"], authorized_scope=("PROOF.md",), evidence=())})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)
    result = run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, agent_operating_contract=load_agent_operating_contract(), persona="fixture persona", task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, model="explicit-test-model", reasoning_effort="high", executable=str(executable))
    assert result.reduction.run.status.value == "completed"
    assert result.reduction.run.hop_used == 1 and result.reduction.run.hops[0].actual_commits
    assert result.checkpoint["state"]["status"] == "completed" and (repository / "PROOF.md").read_text() == "after\n"
    argv = json.loads((state / "checkouts" / association.checkout_id / "argv.json").read_text())
    assert argv[:3] == ["--ask-for-approval", "never", "exec"]
    for token in ("--ephemeral", "--json", "--color", "never", "--sandbox", "workspace-write", "--model", "explicit-test-model", "-"):
        assert token in argv
    assert 'model_reasoning_effort="high"' in argv
    assert argv[argv.index("--cd") + 1] == str(repository.resolve())
    for option in ("--output-schema", "--output-last-message"):
        assert Path(argv[argv.index(option) + 1]).is_relative_to(state.resolve())
    assert not (state / "checkouts" / association.checkout_id / "dispatch.json").exists()


def test_real_dispatch_schema_requested_route_tracks_the_live_phase_routes(repository, tmp_path):
    """H001 correction, wired end to end: the schema actually written for a real
    dispatch enumerates exactly the current phase's permitted routes, not a
    fixture-only property of ``structured_result_envelope_openai_schema``."""
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    script = '''import json, pathlib, sys
args=sys.argv[1:]; output=pathlib.Path(args[args.index("--output-last-message")+1])
output.write_text(json.dumps({"schema_version":1,"run_id":"RUN-1","hop_id":"H001","invocation_id":"INV-1","result":{"schema_version":1,"status":"completed","requested_route":"accept","scope_changed":False,"requires_human":False,"escalation":None,"findings":[],"claimed_commits":[],"commit_intent":None,"evidence_refs":[],"summary":"done"}}))
'''
    executable = _fake(tmp_path, script)
    workflow = profile({"work": phase({"accept": route("complete"), "correction": route("goto", "work")})})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)
    result = run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, agent_operating_contract=load_agent_operating_contract(), persona="fixture persona", task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, model="explicit-test-model", reasoning_effort="high", executable=str(executable))
    assert result.reduction.run.status.value == "completed"
    emitted = json.loads((state / "checkouts" / association.checkout_id / "structured-result.schema.json").read_text())
    assert emitted["properties"]["result"]["properties"]["requested_route"] == {
        "type": "string", "enum": ["accept", "correction"],
    }


def test_real_dispatch_review_verdict_prerequisite_escalation_reaches_awaiting_human(
    repository, tmp_path
):
    """Closes review observation O4 with the end-to-end H001 shape: a GATE
    review phase dispatches a REVIEW_VERDICT schema enumerating its own
    accept/correction routes; a compliant fake provider returns a correlated
    prerequisite escalation naming an enumerated route; canonical validation
    admits those bytes and the Coordinator reduces them to awaiting_human with
    the prerequisite semantic stop.  Unlike the reducer-only cases, this
    traverses the pre-reducer boundary where the incident actually occurred
    (``validated_result`` -> ``ExecutionResultValidationError`` ->
    ``recover_uncertain``), so an uncertain_dispatch charge or
    reconcile_required would fail it."""
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    script = '''import json, pathlib, sys
args=sys.argv[1:]; output=pathlib.Path(args[args.index("--output-last-message")+1])
output.write_text(json.dumps({"schema_version":1,"run_id":"RUN-1","hop_id":"H001","invocation_id":"INV-1","result":{"schema_version":1,"verdict":"changes_required","requested_route":"accept","scope_changed":False,"requires_human":True,"escalation":{"kind":"prerequisite","question":"Is the Coordinator-trusted suite green?"},"findings":[],"claimed_commits":[],"commit_intent":None,"evidence_refs":["agent-local pytest"],"summary":"prerequisite unresolved before any route request"}}))
'''
    executable = _fake(tmp_path, script)
    workflow = profile({"review": phase(
        {"accept": route("complete"), "correction": route("goto", "review")},
        role="independent_reviewer", review="gate",
    )})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=2), current_head=head, baseline_head=head)

    stopped = run_real_one_hop(
        run,
        workflow,
        repository,
        {
            **_components(),
            "independent_reviewer": PromptComponent(
                "independent_reviewer", "reviewer.txt", "PIN", "reviewer"
            ),
        },
        state_root=state,
        association=association,
        agent_operating_contract=load_agent_operating_contract(),
        persona="fixture independent reviewer",
        task_payload="fixture",
        invocation_id="INV-1",
        timeout_seconds=2,
        model="explicit-test-model",
        reasoning_effort="high",
        executable=str(executable),
    )

    run_root = state / "checkouts" / association.checkout_id
    emitted = json.loads((run_root / "structured-result.schema.json").read_text())
    dispatched_result = emitted["properties"]["result"]["properties"]
    assert dispatched_result["requested_route"] == {
        "type": "string", "enum": ["accept", "correction"],
    }
    assert "verdict" in dispatched_result and "status" not in dispatched_result

    assert stopped.vertical is not None
    assert stopped.run.status.value == "awaiting_human"
    assert stopped.run.stop_reason == "mechanical_preemption:human_authority:prerequisite"
    assert stopped.run.semantic_stop is not None
    assert stopped.run.semantic_stop.kind == "prerequisite"
    assert stopped.run.semantic_stop.source_hop_id == "H001"
    assert stopped.run.semantic_stop.question == "Is the Coordinator-trusted suite green?"
    assert stopped.run.anomalies == ()
    hop = stopped.run.hops[-1]
    assert (hop.hop_id, hop.role, hop.outcome) == (
        "H001", "independent_reviewer", "changes_required",
    )
    assert hop.result_contract_kind is ResultContractKind.REVIEW_VERDICT
    assert hop.requested_route == "accept" and hop.applied_route is None
    assert stopped.checkpoint["state"]["status"] == "awaiting_human"
    assert not (run_root / "dispatch.json").exists()
    assert load_run(run_root / "run.json").status.value == "awaiting_human"


def test_completed_pair_marker_is_reconciled_idempotently_without_redispatch(
    repository, tmp_path
):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    calls: list[str] = []

    class Adapter:
        def execute(self, request):
            calls.append(request.invocation.invocation_id)
            envelope = _known_good_envelope(request.result_contract_kind)
            return workflow_coordinator.adapter.AgentExecutionOutcome(
                json.dumps(envelope), 0, False, "", ""
            )

    import workflow_coordinator

    workflow = profile({"work": phase({"done": route("complete")})})
    workflow = replace(
        workflow,
        phases={
            "work": replace(
                workflow.phases["work"],
                authorized_scope=("PROOF.md",),
                evidence=(),
            )
        },
    )
    head = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    run = replace(
        run_for(workflow, budget=1, run_schema_version=3),
        current_head=head,
        baseline_head=head,
    )
    completed = run_real_one_hop(
        run,
        workflow,
        repository,
        _components(),
        state_root=state,
        association=association,
        agent_operating_contract=load_agent_operating_contract(),
        persona="fixture persona",
        task_payload="fixture",
        invocation_id="INV-1",
        timeout_seconds=2,
        adapter=Adapter(),
    )
    run_root = state / "checkouts" / association.checkout_id
    hop = completed.run.hops[-1]
    marker = DispatchMarker(
        1,
        completed.run.run_id,
        hop.hop_id,
        hop.sequence,
        "INV-1",
        hop.base_head,
        1,
        hop.pre_route_anchor_sha256,
    )
    stale = replace(marker, invocation_id="INV-stale")
    write_dispatch_marker(run_root / "dispatch.json", stale)
    with pytest.raises(Slice4Error, match="conflicts with completed Hop pair"):
        run_real_one_hop(
            completed.run,
            workflow,
            repository,
            _components(),
            state_root=state,
            association=association,
            agent_operating_contract=load_agent_operating_contract(),
            persona="fixture persona",
            task_payload="fixture",
            invocation_id="INV-unused",
            timeout_seconds=2,
            adapter=Adapter(),
        )
    write_dispatch_marker(run_root / "dispatch.json", marker)
    recovered = run_real_one_hop(
        completed.run,
        workflow,
        repository,
        _components(),
        state_root=state,
        association=association,
        agent_operating_contract=load_agent_operating_contract(),
        persona="fixture persona",
        task_payload="fixture",
        invocation_id="INV-unused",
        timeout_seconds=2,
        adapter=Adapter(),
    )
    assert recovered.vertical is None
    assert recovered.run == completed.run
    assert calls == ["INV-1"]
    assert not (run_root / "dispatch.json").exists()


def test_live_evaluator_uses_the_same_published_qualification_as_replay(
    repository, tmp_path
):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    workflow, tracked = tracked_convergence_run()
    workflow = replace(
        workflow,
        phases={
            **workflow.phases,
            "evaluate": replace(workflow.phases["evaluate"], evidence=("tests",)),
        },
    )
    head = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    tracked = replace(tracked, baseline_head=head, current_head=head)
    corrected, correction_hop = charge_hop(tracked, end_head=head)
    corrected = reduce_result(
        corrected,
        result(
            "evaluate",
            schema_version=2,
            coverage_claims=(coverage("F001", "addressed", "live-coverage"),),
        ),
        workflow,
        hop_id=correction_hop,
    ).run

    class Adapter:
        def execute(self, request):
            assert request.contract_version == 2
            assert request.result_contract_kind is ResultContractKind.GENERIC_ROUTING_RESULT
            payload = {
                "schema_version": 2,
                "run_id": request.invocation.run_id,
                "hop_id": request.invocation.hop_id,
                "invocation_id": request.invocation.invocation_id,
                "result": {
                    "schema_version": 2,
                    "outcome": "changes_required",
                    "requested_route": "correct",
                    "scope_changed": False,
                    "requires_human": False,
                    "escalation": None,
                    "findings": [
                        {
                            "finding_id": "F001",
                            "proposed_state": "open",
                            "evidence_refs": ["live-counterexample"],
                            "successor_ref": None,
                            "new_material_evidence": False,
                        }
                    ],
                    "claimed_commits": [],
                    "commit_intent": None,
                    "evidence_refs": ["agent-reference-is-not-qualification"],
                    "summary": "live evaluator",
                    "coverage_claims": [],
                    "convergence_assessments": [
                        {
                            "finding_id": "F001",
                            "correction_hop_id": correction_hop,
                            "result": "carried_new_counterexample",
                            "evidence_refs": ["live-counterexample"],
                        }
                    ],
                    "diagnosis": None,
                    "strategy_revision": None,
                    "strategy_assessment": None,
                },
            }
            return AgentExecutionOutcome(json.dumps(payload), 0, False, "", "")

    commands = prepared_evidence(repository)

    class PassingRunner:
        def observe(self, command):
            return simulated_observation(command, status="pass")

    completed = run_real_one_hop(
        corrected,
        workflow,
        repository,
        {
            **_components(),
            "independent_reviewer": PromptComponent(
                "independent_reviewer", "reviewer.txt", "PIN", "reviewer"
            ),
        },
        state_root=state,
        association=association,
        agent_operating_contract=load_agent_operating_contract(),
        persona="fixture evaluator",
        task_payload="fixture",
        invocation_id="INV-live-evaluator",
        timeout_seconds=2,
        adapter=Adapter(),
        evidence_runner=PassingRunner(),
        evidence_commands=commands,
    )
    assert completed.vertical is not None
    live_facts = derive_convergence_facts(
        completed.run, completed.vertical.qualification_index, 1
    )
    assert live_facts[0].source_evaluator_hops == ("H003",)
    assert live_facts[0].goalpost_reached is True

    run_root = state / "checkouts" / association.checkout_id
    replayed = load_run(run_root / "run.json")
    evidence = execution_evidence_from_json(
        (run_root / "execution-evidence.json").read_text(encoding="utf-8")
    )
    assert isinstance(evidence, ExecutionEvidenceDocument)
    replay_index = trusted_qualification_index_for_run(replayed, evidence)
    assert derive_convergence_facts(replayed, replay_index, 1) == live_facts


def test_legacy_completed_marker_recovery_does_not_require_schema4_evidence(
    repository, tmp_path
):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    calls: list[str] = []

    class Adapter:
        def execute(self, request):
            calls.append(request.invocation.invocation_id)
            return AgentExecutionOutcome(
                json.dumps(_known_good_envelope(request.result_contract_kind)), 0, False, "", ""
            )

    workflow = profile({"work": phase({"done": route("complete")})})
    head = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)
    completed = run_real_one_hop(
        run,
        workflow,
        repository,
        _components(),
        state_root=state,
        association=association,
        agent_operating_contract=load_agent_operating_contract(),
        persona="fixture worker",
        task_payload="fixture",
        invocation_id="INV-legacy",
        timeout_seconds=2,
        adapter=Adapter(),
    )
    run_root = state / "checkouts" / association.checkout_id
    assert not (run_root / "execution-evidence.json").exists()
    hop = completed.run.hops[-1]
    write_dispatch_marker(
        run_root / "dispatch.json",
        DispatchMarker(
            1,
            completed.run.run_id,
            hop.hop_id,
            hop.sequence,
            "INV-legacy",
            hop.base_head,
            1,
            None,
        ),
    )
    recovered = run_real_one_hop(
        completed.run,
        workflow,
        repository,
        _components(),
        state_root=state,
        association=association,
        agent_operating_contract=load_agent_operating_contract(),
        persona="fixture worker",
        task_payload="fixture",
        invocation_id="INV-unused",
        timeout_seconds=2,
        adapter=Adapter(),
    )
    assert recovered.run == completed.run
    assert recovered.vertical is None
    assert calls == ["INV-legacy"]
    assert not (run_root / "dispatch.json").exists()


def test_failed_evidence_publication_charges_uncertain_without_partial_row(
    repository, tmp_path, monkeypatch
):
    import workflow_coordinator.slice4 as slice4

    state = tmp_path / "external"
    association = register_checkout(state, repository)
    workflow = profile({"work": phase({"done": route("complete")})})
    workflow = replace(
        workflow,
        phases={"work": replace(workflow.phases["work"], evidence=())},
    )
    head = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    run = replace(
        run_for(workflow, budget=1, run_schema_version=3),
        current_head=head,
        baseline_head=head,
    )
    calls: list[str] = []

    class Adapter:
        def execute(self, request):
            calls.append(request.invocation.invocation_id)
            return AgentExecutionOutcome(
                json.dumps(_known_good_envelope(request.result_contract_kind)), 0, False, "", ""
            )

    monkeypatch.setattr(
        slice4,
        "publish_execution_evidence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("injected evidence publication failure")
        ),
    )
    recovered = run_real_one_hop(
        run,
        workflow,
        repository,
        _components(),
        state_root=state,
        association=association,
        agent_operating_contract=load_agent_operating_contract(),
        persona="fixture persona",
        task_payload="fixture",
        invocation_id="INV-1",
        timeout_seconds=2,
        adapter=Adapter(),
    )
    run_root = state / "checkouts" / association.checkout_id
    assert recovered.run.hops[-1].outcome == "uncertain"
    assert recovered.run.hops[-1].pre_route_anchor_sha256
    assert not (run_root / "execution-evidence.json").exists()
    assert (run_root / "dispatch.json").exists()
    checkpoint = recovered.checkpoint
    marker_text = (run_root / "dispatch.json").read_text(encoding="utf-8")

    recovered_again = run_real_one_hop(
        recovered.run,
        workflow,
        repository,
        _components(),
        state_root=state,
        association=association,
        agent_operating_contract=load_agent_operating_contract(),
        persona="fixture persona",
        task_payload="fixture",
        invocation_id="INV-unused",
        timeout_seconds=2,
        adapter=Adapter(),
    )
    assert recovered_again.vertical is None
    assert recovered_again.run == recovered.run
    assert recovered_again.run.hop_used == 1
    assert recovered_again.checkpoint == checkpoint
    assert not (run_root / "execution-evidence.json").exists()
    assert (run_root / "dispatch.json").exists()
    assert (run_root / "dispatch.json").read_text(encoding="utf-8") == marker_text
    assert calls == ["INV-1"]


def test_staged_unpaired_row_is_abandoned_on_recovery_after_run_write_failure(
    repository, tmp_path, monkeypatch
):
    import workflow_coordinator.slice4 as slice4

    state = tmp_path / "external"
    association = register_checkout(state, repository)
    workflow = profile({"work": phase({"done": route("complete")})})
    workflow = replace(
        workflow,
        phases={"work": replace(workflow.phases["work"], evidence=())},
    )
    head = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    run = replace(
        run_for(workflow, budget=1, run_schema_version=3),
        current_head=head,
        baseline_head=head,
    )
    calls: list[str] = []

    class Adapter:
        def execute(self, request):
            calls.append(request.invocation.invocation_id)
            return AgentExecutionOutcome(
                json.dumps(_known_good_envelope(request.result_contract_kind)), 0, False, "", ""
            )

    original_write = slice4.write_run_snapshot

    def fail_reduced_run(path, observed_run, **kwargs):
        if observed_run.hop_used == 1 and observed_run.hops[-1].role != "uncertain_dispatch":
            raise OSError("injected Run publication failure")
        return original_write(path, observed_run, **kwargs)

    monkeypatch.setattr(slice4, "write_run_snapshot", fail_reduced_run)
    with pytest.raises(OSError, match="injected Run publication failure"):
        run_real_one_hop(
            run,
            workflow,
            repository,
            _components(),
            state_root=state,
            association=association,
            agent_operating_contract=load_agent_operating_contract(),
            persona="fixture persona",
            task_payload="fixture",
            invocation_id="INV-1",
            timeout_seconds=2,
            adapter=Adapter(),
        )
    run_root = state / "checkouts" / association.checkout_id
    assert (run_root / "execution-evidence.json").exists()
    assert (run_root / "dispatch.json").exists()
    persisted_prior = load_run(run_root / "run.json")
    assert persisted_prior.hop_used == 0

    monkeypatch.setattr(slice4, "write_run_snapshot", original_write)
    recovered = run_real_one_hop(
        persisted_prior,
        workflow,
        repository,
        _components(),
        state_root=state,
        association=association,
        agent_operating_contract=load_agent_operating_contract(),
        persona="fixture persona",
        task_payload="fixture",
        invocation_id="INV-unused",
        timeout_seconds=2,
        adapter=Adapter(),
    )
    assert recovered.vertical is None
    assert recovered.run.hops[-1].role == "uncertain_dispatch"
    assert recovered.run.hops[-1].pre_route_anchor_sha256
    assert recovered.run.status.value == "reconcile_required"
    assert calls == ["INV-1"]
    checkpoint = recovered.checkpoint
    marker_text = (run_root / "dispatch.json").read_text(encoding="utf-8")

    recovered_again = run_real_one_hop(
        recovered.run,
        workflow,
        repository,
        _components(),
        state_root=state,
        association=association,
        agent_operating_contract=load_agent_operating_contract(),
        persona="fixture persona",
        task_payload="fixture",
        invocation_id="INV-unused-again",
        timeout_seconds=2,
        adapter=Adapter(),
    )
    assert recovered_again.vertical is None
    assert recovered_again.run == recovered.run
    assert recovered_again.run.hop_used == 1
    assert recovered_again.checkpoint == checkpoint
    assert len(execution_evidence_from_json(
        (run_root / "execution-evidence.json").read_text(encoding="utf-8")
    ).attempts) == 1
    assert (run_root / "dispatch.json").exists()
    assert (run_root / "dispatch.json").read_text(encoding="utf-8") == marker_text
    assert calls == ["INV-1"]

    conflicting_marker = json.loads(marker_text)
    conflicting_marker["invocation_id"] = "INV-conflicting"
    (run_root / "dispatch.json").write_text(
        json.dumps(conflicting_marker), encoding="utf-8"
    )
    with pytest.raises(
        ReductionError, match="abandoned staged evidence lacks exact marker correlation"
    ):
        run_real_one_hop(
            recovered_again.run,
            workflow,
            repository,
            _components(),
            state_root=state,
            association=association,
            agent_operating_contract=load_agent_operating_contract(),
            persona="fixture persona",
            task_payload="fixture",
            invocation_id="INV-unused-conflict",
            timeout_seconds=2,
            adapter=Adapter(),
        )
    assert calls == ["INV-1"]


def test_timeout_kills_process_group_descendant(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    child_pid = tmp_path / "descendant.pid"
    executable = _fake(tmp_path, f"import subprocess,time\np=subprocess.Popen(('sleep','30'))\nopen({str(child_pid)!r},'w').write(str(p.pid))\ntime.sleep(30)\n")
    outcome = invoke_codex(repository=repository, state_root=state, association=association, prompt="x", expected=_expected(), timeout_seconds=0.1, model="explicit-test-model", reasoning_effort="high", permitted_routes=_PERMITTED_ROUTES, executable=str(executable))
    assert outcome.timed_out
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(int(child_pid.read_text()), 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("process-group descendant survived timeout")


def test_real_driver_recovers_post_spawn_uncertainty_with_external_anchors(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    executions = tmp_path / "executions"
    executable = _fake(tmp_path, f"import pathlib,sys\np=pathlib.Path({str(executions)!r})\np.write_text(str(int(p.read_text())+1) if p.exists() else '1')\nsys.exit(7)\n")
    workflow = profile({"work": phase({"done": route("complete")})})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(
        run_for(workflow, budget=1, run_schema_version=3),
        current_head=head,
        baseline_head=head,
    )
    outcome = run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, agent_operating_contract=load_agent_operating_contract(), persona="fixture persona", task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, model="explicit-test-model", reasoning_effort="high", executable=str(executable))
    run_root = state / "checkouts" / association.checkout_id
    assert outcome.vertical is None and outcome.run.hop_used == 1
    assert outcome.run.status.value == "reconcile_required"
    assert outcome.run.stop_reason == "reconcile_requires_explicit_resume"
    assert outcome.checkpoint["state"]["status"] == "reconcile_required"
    assert executions.read_text() == "1" and (repository / "PROOF.md").read_text() == "before\n"
    assert subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip() == head
    assert (run_root / "dispatch.json").exists() and (run_root / "run.json").exists()
    assert (run_root / "checkpoint.json").exists()
    checkpoint = outcome.checkpoint
    marker_text = (run_root / "dispatch.json").read_text(encoding="utf-8")

    recovered_again = run_real_one_hop(
        outcome.run,
        workflow,
        repository,
        _components(),
        state_root=state,
        association=association,
        agent_operating_contract=load_agent_operating_contract(),
        persona="fixture persona",
        task_payload="fixture",
        invocation_id="INV-unused",
        timeout_seconds=2,
        model="explicit-test-model",
        reasoning_effort="high",
        executable=str(executable),
    )
    assert recovered_again.vertical is None
    assert recovered_again.run == outcome.run
    assert recovered_again.run.hop_used == 1
    assert recovered_again.run.hops[-1].pre_route_anchor_sha256 is None
    assert recovered_again.checkpoint == checkpoint
    assert executions.read_text() == "1"
    assert (run_root / "dispatch.json").exists()
    assert (run_root / "dispatch.json").read_text(encoding="utf-8") == marker_text


def test_real_driver_retains_dispatch_after_post_execution_mutating_failure(repository, tmp_path, monkeypatch):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    executions = tmp_path / "executions"
    script = f'''import json, pathlib, subprocess, sys
args=sys.argv[1:]; repo=args[args.index("--cd")+1]; output=pathlib.Path(args[args.index("--output-last-message")+1])
counter=pathlib.Path({str(executions)!r}); counter.write_text(str(int(counter.read_text())+1) if counter.exists() else "1")
pathlib.Path(repo, "PROOF.md").write_text("after\\n")
subprocess.run(("git", "-C", repo, "add", "PROOF.md"), check=True)
subprocess.run(("git", "-C", repo, "commit", "-m", "docs(workflow): WI-1 H001 fixture"), check=True)
commit=subprocess.run(("git", "-C", repo, "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
output.write_text(json.dumps({{"schema_version": 1, "run_id": "RUN-1", "hop_id": "H001", "invocation_id": "INV-1", "result": {{"schema_version": 1, "status": "completed", "requested_route": "done", "scope_changed": False, "requires_human": False, "escalation": None, "findings": [], "claimed_commits": [commit], "commit_intent": None, "evidence_refs": [], "summary": "done"}}}}))
'''
    executable = _fake(tmp_path, script)
    workflow = profile({"work": phase({"done": route("complete")})})
    workflow = replace(workflow, phases={"work": replace(workflow.phases["work"], authorized_scope=("PROOF.md",))})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)

    import workflow_coordinator.vertical as vertical

    monkeypatch.setattr(vertical, "observe_hop", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("post-execution observation failure")))
    outcome = run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, agent_operating_contract=load_agent_operating_contract(), persona="fixture persona", task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, model="explicit-test-model", reasoning_effort="high", executable=str(executable))

    run_root = state / "checkouts" / association.checkout_id
    assert executions.read_text() == "1" and (repository / "PROOF.md").read_text() == "after\n"
    assert subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip() != head
    assert outcome.vertical is None and outcome.run.hop_used == 1
    assert outcome.run.status.value == "reconcile_required"
    assert outcome.run.stop_reason == "reconcile_requires_explicit_resume"
    assert outcome.checkpoint["state"]["status"] == "reconcile_required"
    assert (run_root / "dispatch.json").exists() and (run_root / "run.json").exists()
    assert executions.read_text() == "1"


def test_real_driver_fails_closed_for_unclassified_execution_boundary_exception(repository, tmp_path, monkeypatch):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    workflow = profile({"work": phase({"done": route("complete")})})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)
    executions: list[str] = []

    def unexpected_execute(self, request):
        executions.append(request.invocation.invocation_id)
        raise RuntimeError("execution boundary became unavailable")

    monkeypatch.setattr(CodexCliAdapter, "execute", unexpected_execute)
    outcome = run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, agent_operating_contract=load_agent_operating_contract(), persona="fixture persona", task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, model="explicit-test-model", reasoning_effort="high")

    run_root = state / "checkouts" / association.checkout_id
    assert executions == ["INV-1"]
    assert outcome.vertical is None and outcome.run.status.value == "reconcile_required"
    assert outcome.run.hop_used == 1 and (run_root / "dispatch.json").exists()


def test_real_driver_preserves_no_hop_for_unlaunchable_executable(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    workflow = profile({"work": phase({"done": route("complete")})})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)
    with pytest.raises(Slice4Error, match="pre-acceptance"):
        run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, agent_operating_contract=load_agent_operating_contract(), persona="fixture persona", task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, model="explicit-test-model", reasoning_effort="high", executable=str(tmp_path / "missing-codex"))
    assert run.hop_used == 0
    assert not (state / "checkouts" / association.checkout_id / "dispatch.json").exists()


@pytest.mark.parametrize("budget,used", [(1, 1), (2, 2)])
def test_real_boundary_refuses_ineligible_or_exhausted_run_before_marker(repository, tmp_path, budget, used):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    workflow = profile({"work": phase({"done": route("complete")})})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=budget), current_head=head, baseline_head=head, hop_used=used)
    with pytest.raises(Slice4Error, match="not dispatch-eligible"):
        run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, agent_operating_contract=load_agent_operating_contract(), persona="fixture persona", task_payload="fixture", invocation_id="INV-1", timeout_seconds=2)
    assert not (state / "checkouts" / association.checkout_id / "dispatch.json").exists()


def test_real_driver_cleans_marker_for_local_prompt_failure_before_spawn(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    executions = tmp_path / "executions"
    executable = _fake(tmp_path, f"import pathlib\npathlib.Path({str(executions)!r}).write_text('1')\n")
    workflow = profile({"work": phase({"done": route("complete")})})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)
    with pytest.raises(ValueError, match="prompt component"):
        run_real_one_hop(run, workflow, repository, {"testing": _components()["testing"]}, state_root=state, association=association, agent_operating_contract=load_agent_operating_contract(), persona="fixture persona", task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, model="explicit-test-model", reasoning_effort="high", executable=str(executable))
    assert not executions.exists() and run.hop_used == 0
    assert not (state / "checkouts" / association.checkout_id / "dispatch.json").exists()
    assert (repository / "PROOF.md").read_text() == "before\n"
