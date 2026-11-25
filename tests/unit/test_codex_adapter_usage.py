"""WI-WFC-TOKEN-USAGE-S1-001: Codex token usage, parsed from the real and
derived ``exec --json`` stdout fixtures (``tests/fixtures/provider_usage/``).

Exercises ``codex_adapter._usage_from_stdout`` directly, the same function
``execute()`` calls on the full, un-bounded stdout stream before it is
bounded to its 16 KiB diagnostic tail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workflow_coordinator.codex_adapter import _usage_from_stdout
from workflow_coordinator.model import NO_USAGE_REPORTED, UsageCoverage

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "provider_usage"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestRealSample:
    def test_the_real_captured_sample_yields_complete_coverage_with_cache_semantics(self):
        usage = _usage_from_stdout(_read("codex_real.jsonl"))
        assert usage.coverage is UsageCoverage.COMPLETE
        assert usage.input_tokens == 17329
        assert usage.output_tokens == 5
        assert usage.cache_read_tokens == 11008
        assert usage.cache_creation_tokens == 0
        assert usage.reasoning_output_tokens == 0
        # Codex's input_tokens already includes cached_input_tokens, unlike
        # Claude's (DO section 2) -- the one faithfully-preserved difference.
        assert usage.input_includes_cache_read is True
        assert usage.per_model == ()


class TestMissingEvents:
    def test_no_turn_completed_event_is_no_usage_reported(self):
        assert _usage_from_stdout(_read("codex_missing_usage.jsonl")) == NO_USAGE_REPORTED

    def test_empty_stdout_is_no_usage_reported(self):
        assert _usage_from_stdout("") == NO_USAGE_REPORTED

    def test_non_jsonl_garbage_lines_are_skipped_not_raised(self):
        garbage = "not json at all\n{not json either\n"
        assert _usage_from_stdout(garbage) == NO_USAGE_REPORTED

    def test_turn_completed_event_without_a_usage_key_is_none_coverage_not_raised(self):
        usage = _usage_from_stdout(_read("codex_event_without_usage_field.jsonl"))
        assert usage.coverage is UsageCoverage.NONE
        assert usage.input_tokens is None
        assert usage.output_tokens is None

    def test_truncated_final_line_does_not_raise_and_still_uses_the_prior_valid_event(self):
        usage = _usage_from_stdout(_read("codex_truncated.jsonl"))
        assert usage.coverage is UsageCoverage.COMPLETE
        assert usage.input_tokens == 17329


class TestMultipleEvents:
    def test_two_well_formed_events_sum_every_field(self):
        usage = _usage_from_stdout(_read("codex_two_events_well_formed.jsonl"))
        assert usage.coverage is UsageCoverage.COMPLETE
        assert usage.input_tokens == 17329 + 500
        assert usage.output_tokens == 5 + 20
        assert usage.cache_read_tokens == 11008 + 100
        assert usage.cache_creation_tokens == 0 + 0
        assert usage.reasoning_output_tokens == 0 + 1

    def test_one_malformed_event_leaves_only_that_field_unknown_not_summed_or_guessed(self):
        usage = _usage_from_stdout(_read("codex_two_events_one_malformed.jsonl"))
        # input_tokens was well-formed on both events: still summed.
        assert usage.input_tokens == 17329 + 500
        # output_tokens was missing on the second event: unknown, not a
        # partial/guessed sum of only the first event's value.
        assert usage.output_tokens is None
        assert usage.coverage is UsageCoverage.PARTIAL


class TestDefensiveParsing:
    def test_wrong_type_figure_is_unknown(self):
        usage = _usage_from_stdout(_read("codex_wrong_types.jsonl"))
        assert usage.input_tokens is None  # was a float, not an int
        assert usage.output_tokens == 5  # unaffected field stays known

    def test_negative_figure_is_unknown(self):
        usage = _usage_from_stdout(_read("codex_negative.jsonl"))
        assert usage.cache_read_tokens is None

    @pytest.mark.parametrize("usage_value", [None, "not-a-dict", 5, []])
    def test_a_non_object_usage_value_yields_no_usage_reported_for_that_single_event(self, usage_value):
        import json
        event = json.dumps({"type": "turn.completed", "usage": usage_value})
        usage = _usage_from_stdout(event)
        assert usage.coverage is UsageCoverage.NONE
        assert usage.input_tokens is None

    def test_pathologically_nested_json_line_is_skipped_not_raised(self):
        # json.loads() raises RecursionError (not JSONDecodeError) on very
        # deep nesting; this must never escape into the Hop (F004).
        deeply_nested_line = "[" * 200_000 + "]" * 200_000
        usage = _usage_from_stdout(deeply_nested_line)
        assert usage == NO_USAGE_REPORTED
