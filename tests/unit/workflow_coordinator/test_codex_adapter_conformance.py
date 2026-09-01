"""Offline Codex/OpenAI strict Structured Outputs conformance tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.workflow_coordinator.adapter import AgentExecutionRequest, Invocation
from scripts.workflow_coordinator.codex_adapter import (
    CodexCliAdapter,
    OpenAIStrictSchemaError,
    assert_openai_strict_schema,
    structured_result_envelope_openai_schema,
)
from scripts.workflow_coordinator.serialization import FINDING_DELTA_KEYS, ROUTING_RESULT_REQUIRED_KEYS


def test_exact_codex_projection_conforms_to_supported_openai_strict_subset():
    assert_openai_strict_schema(structured_result_envelope_openai_schema())


def test_conformance_rejects_both_historical_live_defects():
    opaque = structured_result_envelope_openai_schema()
    opaque["properties"]["result"] = {"type": "object"}
    with pytest.raises(OpenAIStrictSchemaError, match="object must be strict"):
        assert_openai_strict_schema(opaque)
    untyped_const = structured_result_envelope_openai_schema()
    del untyped_const["properties"]["schema_version"]["type"]
    with pytest.raises(OpenAIStrictSchemaError, match="explicit type"):
        assert_openai_strict_schema(untyped_const)


def test_conformance_walks_nested_properties_arrays_and_nullable_anyof_branches():
    schema = structured_result_envelope_openai_schema()
    nested = schema["properties"]["result"]["properties"]
    del nested["findings"]["items"]["properties"]["invariant"]["type"]
    with pytest.raises(OpenAIStrictSchemaError, match="invariant: explicit type"):
        assert_openai_strict_schema(schema)
    schema = structured_result_envelope_openai_schema()
    del schema["properties"]["result"]["properties"]["escalation"]["anyOf"][0]["type"]
    with pytest.raises(OpenAIStrictSchemaError, match=r"anyOf\[0\]: explicit type"):
        assert_openai_strict_schema(schema)


def test_projection_derives_from_canonical_semantic_contract_facts():
    result = structured_result_envelope_openai_schema()["properties"]["result"]
    assert set(result["required"]) == set(ROUTING_RESULT_REQUIRED_KEYS)
    finding = result["properties"]["findings"]["items"]
    assert set(finding["required"]) == set(FINDING_DELTA_KEYS)


def test_codex_argv_is_adapter_owned_and_request_is_provider_neutral(tmp_path: Path):
    request = AgentExecutionRequest(Invocation("RUN", "H001", "INV"), "prompt", tmp_path, "routing-result-envelope/v1", 1)
    adapter = CodexCliAdapter(tmp_path, executable="fake-codex")
    argv = adapter.argv(request, tmp_path / "schema.json", tmp_path / "result.json")
    assert argv[:4] == ("fake-codex", "--ask-for-approval", "never", "exec")
    assert "--output-schema" in argv and "--output-last-message" in argv
    assert "codex" not in request.result_contract
    assert not hasattr(adapter, "outcome")


def test_conformance_rejects_constructs_outside_the_projection_subset():
    schema = structured_result_envelope_openai_schema()
    schema["properties"]["result"]["oneOf"] = []
    with pytest.raises(OpenAIStrictSchemaError, match="unsupported keywords"):
        assert_openai_strict_schema(schema)
