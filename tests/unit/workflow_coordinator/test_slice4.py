"""Deterministic Slice-4 external-state and real-process composition tests."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.workflow_coordinator.adapter import Invocation, validated_result
from scripts.workflow_coordinator.codex_adapter import CodexCliAdapter, structured_result_envelope_openai_schema
from scripts.workflow_coordinator.serialization import (
    FINDING_DELTA_KEYS,
    ROUTING_RESULT_REQUIRED_KEYS,
)
from scripts.workflow_coordinator.slice4 import (
    Slice4Error, invoke_codex, register_checkout, run_real_one_hop, validate_checkout,
)
from tests.utils.workflow_coordinator import phase, profile, route, run_for
from scripts.workflow_coordinator.prompt import PromptComponent


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


def _known_good_envelope():
    return {
        "schema_version": 1,
        "run_id": "RUN-1",
        "hop_id": "H001",
        "invocation_id": "INV-1",
        "result": {
            "schema_version": 1,
            "outcome": "completed",
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
    schema = structured_result_envelope_openai_schema()
    result = schema["properties"]["result"]
    escalation = result["properties"]["escalation"]["anyOf"][1]
    finding = result["properties"]["findings"]["items"]
    object_schemas = list(_walk_object_schemas(schema))
    assert all(candidate in object_schemas for candidate in (schema, result, escalation, finding))
    for object_schema in object_schemas:
        assert object_schema["additionalProperties"] is False
        assert set(object_schema["required"]) == set(object_schema["properties"])


def test_codex_projection_tracks_routing_result_validator_contract():
    result = structured_result_envelope_openai_schema()["properties"]["result"]
    assert set(result["required"]) == set(ROUTING_RESULT_REQUIRED_KEYS)


def test_codex_projection_tracks_complete_finding_delta_contract():
    finding = structured_result_envelope_openai_schema()["properties"]["result"]["properties"]["findings"]["items"]
    properties = finding["properties"]
    assert set(finding["required"]) == set(FINDING_DELTA_KEYS)
    assert properties["finding_id"]["type"] == ["string", "null"]
    assert properties["successor_ref"]["type"] == ["string", "null"]
    assert properties["new_material_evidence"] == {"type": "boolean"}


def test_invoke_codex_writes_the_canonical_structured_result_schema(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    executable = _fake(tmp_path, "pass\n")
    invoke_codex(repository=repository, state_root=state, association=association, prompt="x", expected=_expected(), timeout_seconds=2, executable=str(executable))
    emitted = json.loads((state / "checkouts" / association.checkout_id / "structured-result.schema.json").read_text())
    assert emitted == structured_result_envelope_openai_schema()


def test_known_good_envelope_passes_adapter_validation_and_provider_schema():
    envelope = _known_good_envelope()
    assert validated_result(json.dumps(envelope), _expected()).summary == "done"
    _assert_matches_schema(envelope, structured_result_envelope_openai_schema())


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
    outcome = invoke_codex(repository=repository, state_root=state, association=association, prompt="x", expected=_expected(), timeout_seconds=2, executable=str(executable))
    assert outcome.candidate_result == payload


def test_nonzero_without_result_is_uncertain(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    executable = _fake(tmp_path, "import sys\nsys.exit(7)\n")
    outcome = invoke_codex(repository=repository, state_root=state, association=association, prompt="x", expected=_expected(), timeout_seconds=2, executable=str(executable))
    assert outcome.returncode == 7 and outcome.candidate_result is None


def test_real_vertical_composition_uses_fake_cli_and_existing_reducer(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    script = '''import json, pathlib, subprocess, sys
args=sys.argv[1:]; repo=args[args.index("--cd")+1]; output=pathlib.Path(args[args.index("--output-last-message")+1])
(output.parent / "argv.json").write_text(json.dumps(args))
pathlib.Path(repo, "PROOF.md").write_text("after\\n")
output.write_text(json.dumps({"schema_version":1,"run_id":"RUN-1","hop_id":"H001","invocation_id":"INV-1","result":{"schema_version":1,"outcome":"completed","requested_route":"done","scope_changed":False,"requires_human":False,"escalation":None,"findings":[],"claimed_commits":[],"commit_intent":{"paths":["PROOF.md"],"subject":"docs(workflow): WI-1 H001 fixture"},"evidence_refs":[],"summary":"done"}}))
'''
    executable = _fake(tmp_path, script)
    workflow = profile({"work": phase({"done": route("complete")})})
    workflow = replace(workflow, phases={"work": replace(workflow.phases["work"], authorized_scope=("PROOF.md",), evidence=("git_version",))})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)
    result = run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, executable=str(executable))
    assert result.reduction.run.status.value == "completed"
    assert result.reduction.run.hop_used == 1 and result.reduction.run.hops[0].actual_commits
    assert result.checkpoint["state"]["status"] == "completed" and (repository / "PROOF.md").read_text() == "after\n"
    argv = json.loads((state / "checkouts" / association.checkout_id / "argv.json").read_text())
    assert argv[:3] == ["--ask-for-approval", "never", "exec"]
    for token in ("--ephemeral", "--json", "--color", "never", "--sandbox", "workspace-write", "--model", "gpt-5.6-terra", "-"):
        assert token in argv
    assert argv[argv.index("--cd") + 1] == str(repository.resolve())
    for option in ("--output-schema", "--output-last-message"):
        assert Path(argv[argv.index(option) + 1]).is_relative_to(state.resolve())
    assert not (state / "checkouts" / association.checkout_id / "dispatch.json").exists()


def test_timeout_kills_process_group_descendant(repository, tmp_path):
    state = tmp_path / "external"
    association = register_checkout(state, repository)
    child_pid = tmp_path / "descendant.pid"
    executable = _fake(tmp_path, f"import subprocess,time\np=subprocess.Popen(('sleep','30'))\nopen({str(child_pid)!r},'w').write(str(p.pid))\ntime.sleep(30)\n")
    outcome = invoke_codex(repository=repository, state_root=state, association=association, prompt="x", expected=_expected(), timeout_seconds=0.1, executable=str(executable))
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
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)
    outcome = run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, executable=str(executable))
    run_root = state / "checkouts" / association.checkout_id
    assert outcome.vertical is None and outcome.run.hop_used == 1
    assert outcome.run.status.value == "reconcile_required"
    assert outcome.run.stop_reason == "reconcile_requires_explicit_resume"
    assert outcome.checkpoint["state"]["status"] == "reconcile_required"
    assert executions.read_text() == "1" and (repository / "PROOF.md").read_text() == "before\n"
    assert subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip() == head
    assert (run_root / "dispatch.json").exists() and (run_root / "run.json").exists()
    assert (run_root / "checkpoint.json").exists()


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
output.write_text(json.dumps({{"schema_version": 1, "run_id": "RUN-1", "hop_id": "H001", "invocation_id": "INV-1", "result": {{"schema_version": 1, "outcome": "completed", "requested_route": "done", "scope_changed": False, "requires_human": False, "escalation": None, "findings": [], "claimed_commits": [commit], "commit_intent": None, "evidence_refs": [], "summary": "done"}}}}))
'''
    executable = _fake(tmp_path, script)
    workflow = profile({"work": phase({"done": route("complete")})})
    workflow = replace(workflow, phases={"work": replace(workflow.phases["work"], authorized_scope=("PROOF.md",))})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)

    import scripts.workflow_coordinator.vertical as vertical

    monkeypatch.setattr(vertical, "observe_hop", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("post-execution observation failure")))
    outcome = run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, executable=str(executable))

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
    outcome = run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, task_payload="fixture", invocation_id="INV-1", timeout_seconds=2)

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
        run_real_one_hop(run, workflow, repository, _components(), state_root=state, association=association, task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, executable=str(tmp_path / "missing-codex"))
    assert run.hop_used == 0
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
        run_real_one_hop(run, workflow, repository, {"testing": _components()["testing"]}, state_root=state, association=association, task_payload="fixture", invocation_id="INV-1", timeout_seconds=2, executable=str(executable))
    assert not executions.exists() and run.hop_used == 0
    assert not (state / "checkouts" / association.checkout_id / "dispatch.json").exists()
    assert (repository / "PROOF.md").read_text() == "before\n"
