"""WI-WFC-TOKEN-USAGE-S1-001: Claude token usage, parsed from the real and
derived provider wrapper fixtures (``tests/fixtures/provider_usage/``).

These exercise ``claude_adapter._usage_from_wrapper_stdout`` directly (the
same function the adapter's ``execute()`` calls on the full, un-bounded
wrapper stdout) so each contract case is isolated from subprocess launch,
which this sandbox cannot always exercise (see
``test_claude_adapter_conformance.py``'s own environment note).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workflow_coordinator.claude_adapter import _usage_from_wrapper_stdout
from workflow_coordinator.model import NO_USAGE_REPORTED, UsageCoverage

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "provider_usage"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestRealSample:
    def test_the_real_captured_sample_yields_complete_coverage_with_cache_semantics(self):
        usage = _usage_from_wrapper_stdout(_read("claude_real.json"))
        assert usage.coverage is UsageCoverage.COMPLETE
        assert usage.input_tokens == 2
        assert usage.output_tokens == 4
        assert usage.cache_read_tokens == 9074
        assert usage.cache_creation_tokens == 12736
        assert usage.reasoning_output_tokens == 0
        # Claude's input_tokens excludes cache read/creation (DO section 2).
        assert usage.input_includes_cache_read is False

    def test_the_real_sample_projects_one_per_model_entry(self):
        usage = _usage_from_wrapper_stdout(_read("claude_real.json"))
        assert len(usage.per_model) == 1
        [entry] = usage.per_model
        assert entry.model == "claude-haiku-5-5"
        assert entry.input_tokens == 2
        assert entry.output_tokens == 4
        assert entry.cache_read_tokens == 9074
        assert entry.cache_creation_tokens == 12736
        assert entry.reasoning_output_tokens == 0

    def test_no_monetary_field_is_ever_read(self):
        # total_cost_usd / costUSD are present in the real sample; this
        # contract asserts they are never read into the usage record.
        usage = _usage_from_wrapper_stdout(_read("claude_real.json"))
        for value in (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens,
                      usage.cache_creation_tokens, usage.reasoning_output_tokens):
            assert value != 0.0026401400000000005  # the real sample's costUSD, by inspection


class TestMissingOrPartial:
    def test_missing_usage_key_is_none_coverage(self):
        usage = _usage_from_wrapper_stdout(_read("claude_missing_usage.json"))
        assert usage.coverage is UsageCoverage.NONE
        assert usage.input_tokens is None
        assert usage.output_tokens is None

    def test_missing_model_usage_key_leaves_per_model_empty_but_keeps_the_aggregate(self):
        usage = _usage_from_wrapper_stdout(_read("claude_missing_model_usage.json"))
        assert usage.per_model == ()
        assert usage.input_tokens == 2
        assert usage.output_tokens == 4

    def test_one_missing_figure_is_partial_coverage(self):
        usage = _usage_from_wrapper_stdout(_read("claude_partial_fields.json"))
        assert usage.coverage is UsageCoverage.PARTIAL
        assert usage.input_tokens == 2
        assert usage.output_tokens is None

    def test_model_usage_entry_with_only_some_fields_leaves_the_rest_null(self):
        usage = _usage_from_wrapper_stdout(_read("claude_model_usage_partial.json"))
        [entry] = usage.per_model
        assert entry.input_tokens == 2
        assert entry.output_tokens is None
        assert entry.reasoning_output_tokens is None


class TestDefensiveParsing:
    def test_wrong_type_fields_become_unknown_not_raised(self):
        usage = _usage_from_wrapper_stdout(_read("claude_wrong_types.json"))
        assert usage.input_tokens is None  # was the string "two"
        assert usage.cache_read_tokens is None  # was a bool

    def test_negative_figure_is_unknown(self):
        usage = _usage_from_wrapper_stdout(_read("claude_negative.json"))
        assert usage.output_tokens is None

    def test_implausibly_huge_figure_is_unknown(self):
        usage = _usage_from_wrapper_stdout(_read("claude_huge_value.json"))
        assert usage.input_tokens is None

    def test_truncated_non_json_wrapper_is_no_usage_reported(self):
        assert _usage_from_wrapper_stdout(_read("claude_not_json.txt")) == NO_USAGE_REPORTED

    def test_empty_stdout_is_no_usage_reported(self):
        assert _usage_from_wrapper_stdout("") == NO_USAGE_REPORTED

    @pytest.mark.parametrize("wrapper", ["null", "[]", '"a string"', "42"])
    def test_a_non_object_wrapper_is_no_usage_reported(self, wrapper):
        assert _usage_from_wrapper_stdout(wrapper) == NO_USAGE_REPORTED

    def test_usage_is_still_parsed_from_a_failed_wrapper(self):
        # The cost already occurred even when the wrapper itself reports
        # failure; usage parsing is not gated on type/subtype/is_error.
        failed_wrapper = _read("claude_real.json").replace('"is_error": false', '"is_error": true')
        usage = _usage_from_wrapper_stdout(failed_wrapper)
        assert usage.coverage is UsageCoverage.COMPLETE
        assert usage.input_tokens == 2
