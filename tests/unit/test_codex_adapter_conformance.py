"""Offline Codex/OpenAI strict Structured Outputs conformance tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow_coordinator.adapter import (
    ROUTING_RESULT_ENVELOPE_CONTRACT,
    AgentExecutionRequest,
    Invocation,
    ResultValidationError,
    validated_result,
)
from workflow_coordinator.codex_adapter import (
    CodexCliAdapter,
    OpenAIStrictSchemaError,
    assert_openai_strict_schema,
    structured_result_envelope_openai_schema,
)
from workflow_coordinator.serialization import (
    EXISTING_FINDING_DELTA_KEYS,
    NEW_FINDING_DELTA_KEYS,
    NEW_FINDING_DELTA_V2_KEYS,
    ROUTING_RESULT_REQUIRED_KEYS,
    ROUTING_RESULT_V2_REQUIRED_KEYS,
)


def _invocation(run_id: str = "RUN-A", hop_id: str = "H003", invocation_id: str = "INV-X") -> Invocation:
    return Invocation(run_id, hop_id, invocation_id)


def _envelope_schema(invocation: Invocation | None = None) -> dict:
    """The projection is invocation-specific, so every schema test dispatches one."""
    return structured_result_envelope_openai_schema(invocation or _invocation())


def test_exact_codex_projection_conforms_to_supported_openai_strict_subset():
    assert_openai_strict_schema(_envelope_schema())


def test_schema_version_uses_the_documented_single_value_enum_in_both_envelopes():
    schema = _envelope_schema()
    assert schema["properties"]["schema_version"] == {"type": "integer", "enum": [1]}
    assert schema["properties"]["result"]["properties"]["schema_version"] == {"type": "integer", "enum": [1]}


def test_conformance_rejects_both_historical_live_defects():
    opaque = _envelope_schema()
    opaque["properties"]["result"] = {"type": "object"}
    with pytest.raises(OpenAIStrictSchemaError, match="object must be strict"):
        assert_openai_strict_schema(opaque)
    untyped_enum = _envelope_schema()
    del untyped_enum["properties"]["schema_version"]["type"]
    with pytest.raises(OpenAIStrictSchemaError, match="explicit type"):
        assert_openai_strict_schema(untyped_enum)


def test_conformance_walks_nested_properties_arrays_and_nullable_anyof_branches():
    schema = _envelope_schema()
    nested = schema["properties"]["result"]["properties"]
    del nested["findings"]["items"]["anyOf"][0]["properties"]["invariant"]["type"]
    with pytest.raises(OpenAIStrictSchemaError, match="invariant: explicit type"):
        assert_openai_strict_schema(schema)
    schema = _envelope_schema()
    del schema["properties"]["result"]["properties"]["escalation"]["anyOf"][0]["type"]
    with pytest.raises(OpenAIStrictSchemaError, match=r"anyOf\[0\]: explicit type"):
        assert_openai_strict_schema(schema)


def test_projection_derives_from_canonical_semantic_contract_facts():
    result = _envelope_schema()["properties"]["result"]
    assert set(result["required"]) == set(ROUTING_RESULT_REQUIRED_KEYS)
    new_finding, existing_finding = result["properties"]["findings"]["items"]["anyOf"]
    assert set(new_finding["required"]) == set(NEW_FINDING_DELTA_KEYS)
    assert set(existing_finding["required"]) == set(EXISTING_FINDING_DELTA_KEYS)
    assert "invariant" not in existing_finding["properties"]
    assert "blocking" not in existing_finding["properties"]


def test_result_v2_projection_is_strict_closed_and_null_only_for_later_slice_records():
    schema = structured_result_envelope_openai_schema(_invocation(), 2)
    assert_openai_strict_schema(schema)
    assert schema["properties"]["schema_version"]["enum"] == [2]
    result = schema["properties"]["result"]
    assert set(result["required"]) == set(ROUTING_RESULT_V2_REQUIRED_KEYS)
    new_finding, existing_finding = result["properties"]["findings"]["items"]["anyOf"]
    assert set(new_finding["required"]) == set(NEW_FINDING_DELTA_V2_KEYS)
    assert "local_ref" not in existing_finding["properties"]
    for field in ("diagnosis", "strategy_revision", "strategy_assessment"):
        assert result["properties"][field] == {"type": "null"}


def test_contract_version_selector_defaults_to_v1_and_rejects_mixed_envelopes():
    invocation = _invocation()
    result_v2 = {
        "schema_version": 2,
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
        "coverage_claims": [],
        "convergence_assessments": [],
        "diagnosis": None,
        "strategy_revision": None,
        "strategy_assessment": None,
    }
    envelope = json.dumps({
        "schema_version": 2,
        "run_id": invocation.run_id,
        "hop_id": invocation.hop_id,
        "invocation_id": invocation.invocation_id,
        "result": result_v2,
    })
    assert validated_result(envelope, invocation, 2).schema_version == 2
    with pytest.raises(ResultValidationError, match="unsupported schema version"):
        validated_result(envelope, invocation)

    mixed = json.loads(envelope)
    mixed["schema_version"] = 1
    with pytest.raises(ResultValidationError, match="does not match"):
        validated_result(json.dumps(mixed), invocation, 1)


def test_codex_execute_uses_explicit_v2_selector_without_changing_default(tmp_path: Path):
    executable = tmp_path / "fake-codex"
    executable.write_text("#!/usr/bin/env python3\npass\n")
    executable.chmod(0o755)
    adapter = CodexCliAdapter(
        tmp_path, model="explicit-model", reasoning_effort="high", executable=str(executable)
    )
    request = AgentExecutionRequest(
        _invocation(), "prompt", tmp_path, ROUTING_RESULT_ENVELOPE_CONTRACT, 5,
        contract_version=2,
    )
    adapter.execute(request)
    emitted = json.loads(
        (tmp_path / "structured-result.schema.json").read_text(encoding="utf-8")
    )
    assert emitted == structured_result_envelope_openai_schema(request.invocation, 2)


def test_codex_argv_is_adapter_owned_and_request_is_provider_neutral(tmp_path: Path):
    request = AgentExecutionRequest(Invocation("RUN", "H001", "INV"), "prompt", tmp_path, ROUTING_RESULT_ENVELOPE_CONTRACT, 1)
    adapter = CodexCliAdapter(
        tmp_path, model="explicit-model", reasoning_effort="high", executable="fake-codex"
    )
    argv = adapter.argv(request, tmp_path / "schema.json", tmp_path / "result.json")
    assert argv[:4] == ("fake-codex", "--ask-for-approval", "never", "exec")
    assert "--output-schema" in argv and "--output-last-message" in argv
    assert "codex" not in request.result_contract
    assert not hasattr(adapter, "outcome")


def test_adapter_rejects_an_unsupported_result_contract_before_launch(tmp_path: Path):
    adapter = CodexCliAdapter(
        tmp_path, model="explicit-model", reasoning_effort="high", executable="must-not-run"
    )
    request = AgentExecutionRequest(Invocation("RUN", "H001", "INV"), "prompt", tmp_path, "other-contract", 1)
    with pytest.raises(RuntimeError, match="unsupported result contract"):
        adapter.execute(request)


def test_production_adapter_has_no_model_or_reasoning_fallback(tmp_path: Path):
    with pytest.raises(TypeError):
        CodexCliAdapter(tmp_path)
    with pytest.raises(ValueError, match="explicit and non-empty"):
        CodexCliAdapter(tmp_path, model="", reasoning_effort="high")


def test_conformance_rejects_constructs_outside_the_projection_subset():
    schema = _envelope_schema()
    schema["properties"]["result"]["oneOf"] = []
    with pytest.raises(OpenAIStrictSchemaError, match="unsupported keywords"):
        assert_openai_strict_schema(schema)
    schema = _envelope_schema()
    schema["properties"]["schema_version"] = {"type": "integer", "const": 1}
    with pytest.raises(OpenAIStrictSchemaError, match="unsupported keywords"):
        assert_openai_strict_schema(schema)


def test_dispatched_correlation_identity_is_projected_as_exact_value_enums():
    schema = _envelope_schema(_invocation("RUN-A", "H003", "INV-X"))["properties"]
    assert schema["run_id"] == {"type": "string", "enum": ["RUN-A"]}
    assert schema["hop_id"] == {"type": "string", "enum": ["H003"]}
    assert schema["invocation_id"] == {"type": "string", "enum": ["INV-X"]}


def test_identity_projection_is_invocation_specific_with_no_unconstrained_fallback():
    first = _envelope_schema(_invocation("RUN-A", "H003", "INV-X"))["properties"]
    second = _envelope_schema(_invocation("RUN-B", "H004", "INV-Y"))["properties"]
    for field in ("run_id", "hop_id", "invocation_id"):
        assert first[field] != second[field]
        assert len(first[field]["enum"]) == 1 and len(second[field]["enum"]) == 1
    with pytest.raises(TypeError):
        structured_result_envelope_openai_schema()


def test_invocation_specific_identity_projection_stays_inside_the_strict_subset():
    schema = _envelope_schema(_invocation("RUN-A", "H003", "INV-X"))
    assert_openai_strict_schema(schema)
    del schema["properties"]["run_id"]["type"]
    with pytest.raises(OpenAIStrictSchemaError, match="run_id: explicit type"):
        assert_openai_strict_schema(schema)


def test_execute_writes_the_schema_derived_from_the_exact_dispatched_invocation(tmp_path: Path):
    executable = tmp_path / "fake-codex"
    executable.write_text("#!/usr/bin/env python3\npass\n")
    executable.chmod(0o755)
    invocation = _invocation("RUN-WIRED", "H007", "INV-WIRED")
    request = AgentExecutionRequest(invocation, "prompt", tmp_path, ROUTING_RESULT_ENVELOPE_CONTRACT, 5)
    adapter = CodexCliAdapter(tmp_path, model="explicit-model", reasoning_effort="high", executable=str(executable))

    adapter.execute(request)

    emitted = json.loads((tmp_path / "structured-result.schema.json").read_text(encoding="utf-8"))
    assert emitted == structured_result_envelope_openai_schema(invocation)
    assert emitted["properties"]["run_id"]["enum"] == ["RUN-WIRED"]


def test_execute_never_repairs_a_divergent_returned_identity(tmp_path: Path):
    """The adapter must not manufacture the identity ``validated_result`` checks."""
    divergent = json.dumps({
        "schema_version": 1, "run_id": "RUN-WIRED,", "hop_id": "H007", "invocation_id": "INV-WIRED",
        "result": {"schema_version": 1, "outcome": "completed", "requested_route": "done",
                   "scope_changed": False, "requires_human": False, "escalation": None, "findings": [],
                   "claimed_commits": [], "commit_intent": None, "evidence_refs": [], "summary": "done"},
    })
    executable = tmp_path / "fake-codex"
    executable.write_text(
        "#!/usr/bin/env python3\nfrom pathlib import Path\n"
        f"Path({str(tmp_path / 'result-message.json')!r}).write_text({divergent!r})\n"
    )
    executable.chmod(0o755)
    invocation = _invocation("RUN-WIRED", "H007", "INV-WIRED")
    request = AgentExecutionRequest(invocation, "prompt", tmp_path, ROUTING_RESULT_ENVELOPE_CONTRACT, 5)
    adapter = CodexCliAdapter(tmp_path, model="explicit-model", reasoning_effort="high", executable=str(executable))

    outcome = adapter.execute(request)

    assert json.loads(outcome.candidate_result)["run_id"] == "RUN-WIRED,"
    with pytest.raises(ResultValidationError, match="does not correlate"):
        validated_result(outcome.candidate_result, invocation)
