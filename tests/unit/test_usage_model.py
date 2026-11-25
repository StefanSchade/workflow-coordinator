"""WI-WFC-TOKEN-USAGE-S1-001: the shared provider-neutral usage primitives.

These fail without the implementation: ``coerce_token_count``,
``usage_coverage_for``, ``UsageRecord``/``ModelUsage`` and
``NO_USAGE_REPORTED`` did not exist before this change.
"""

from __future__ import annotations

import pytest

from workflow_coordinator.model import (
    MAX_PLAUSIBLE_TOKEN_COUNT,
    ModelUsage,
    NO_USAGE_REPORTED,
    UsageCoverage,
    UsageRecord,
    coerce_token_count,
    usage_coverage_for,
)


class TestCoerceTokenCount:
    def test_a_well_formed_non_negative_integer_is_returned_unchanged(self):
        assert coerce_token_count(0) == 0
        assert coerce_token_count(42) == 42

    @pytest.mark.parametrize("value", [-1, -100])
    def test_a_negative_integer_is_unknown(self, value):
        assert coerce_token_count(value) is None

    @pytest.mark.parametrize("value", [True, False])
    def test_a_boolean_is_unknown_even_though_it_is_a_python_int_subclass(self, value):
        assert coerce_token_count(value) is None

    @pytest.mark.parametrize("value", ["5", 5.0, None, [], {}, object()])
    def test_any_non_integer_json_shape_is_unknown(self, value):
        assert coerce_token_count(value) is None

    def test_a_value_beyond_the_plausible_bound_is_unknown(self):
        assert coerce_token_count(MAX_PLAUSIBLE_TOKEN_COUNT) == MAX_PLAUSIBLE_TOKEN_COUNT
        assert coerce_token_count(MAX_PLAUSIBLE_TOKEN_COUNT + 1) is None
        assert coerce_token_count(10**18) is None


class TestUsageCoverageFor:
    def test_both_figures_known_is_complete(self):
        assert usage_coverage_for(1, 2) is UsageCoverage.COMPLETE

    @pytest.mark.parametrize("input_tokens,output_tokens", [(1, None), (None, 2)])
    def test_exactly_one_figure_known_is_partial(self, input_tokens, output_tokens):
        assert usage_coverage_for(input_tokens, output_tokens) is UsageCoverage.PARTIAL

    def test_neither_figure_known_is_none(self):
        assert usage_coverage_for(None, None) is UsageCoverage.NONE


class TestNoUsageReported:
    def test_is_the_none_coverage_with_every_figure_unknown(self):
        assert NO_USAGE_REPORTED.coverage is UsageCoverage.NONE
        assert NO_USAGE_REPORTED.input_tokens is None
        assert NO_USAGE_REPORTED.output_tokens is None
        assert NO_USAGE_REPORTED.cache_read_tokens is None
        assert NO_USAGE_REPORTED.cache_creation_tokens is None
        assert NO_USAGE_REPORTED.reasoning_output_tokens is None
        assert NO_USAGE_REPORTED.per_model == ()


class TestUsageRecordIsFrozenAndCarriesNoPrice:
    def test_is_immutable(self):
        with pytest.raises(AttributeError):
            NO_USAGE_REPORTED.input_tokens = 5  # type: ignore[misc]

    def test_dataclass_fields_never_include_a_monetary_name(self):
        record_fields = set(UsageRecord.__dataclass_fields__)
        model_fields = set(ModelUsage.__dataclass_fields__)
        for name in record_fields | model_fields:
            lowered = name.lower()
            assert "cost" not in lowered
            assert "price" not in lowered
            assert "usd" not in lowered
