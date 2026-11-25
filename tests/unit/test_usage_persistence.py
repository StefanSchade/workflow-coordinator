"""WI-WFC-TOKEN-USAGE-S1-001: ``usage.json`` persistence and its JSON shape.

``write_usage_record`` is the one new write surface this slice adds, inside
an existing checkout's own state directory; these tests pin its crash-safety
discipline (atomic replace, idempotent repeat, refused conflicting
duplicate) and that ``usage_record_to_data`` carries only numbers,
enumerations and provider/model identifiers -- never a prompt, output, path
or credential.
"""

from __future__ import annotations

import json

import pytest

from workflow_coordinator.model import ModelUsage, UsageCoverage, UsageRecord
from workflow_coordinator.persistence import write_usage_record
from workflow_coordinator.serialization import usage_record_to_data


def _record(**overrides) -> UsageRecord:
    defaults = dict(
        coverage=UsageCoverage.COMPLETE,
        input_tokens=2,
        output_tokens=4,
        cache_read_tokens=9074,
        cache_creation_tokens=12736,
        reasoning_output_tokens=0,
        input_includes_cache_read=False,
        per_model=(ModelUsage("claude-haiku-5-5", 2, 4, 9074, 12736, 0),),
    )
    defaults.update(overrides)
    return UsageRecord(**defaults)


class TestUsageRecordToData:
    def test_projects_identity_binding_and_every_figure(self):
        data = usage_record_to_data(
            _record(), invocation_id="INV-1", role="worker", adapter="claude_cli",
            model="claude-haiku-5-5",
        )
        assert data == {
            "invocation_id": "INV-1", "role": "worker", "adapter": "claude_cli",
            "model": "claude-haiku-5-5", "coverage": "complete", "input_tokens": 2,
            "output_tokens": 4, "cache_read_tokens": 9074, "cache_creation_tokens": 12736,
            "reasoning_output_tokens": 0, "input_includes_cache_read": False,
            "per_model": [{
                "model": "claude-haiku-5-5", "input_tokens": 2, "output_tokens": 4,
                "cache_read_tokens": 9074, "cache_creation_tokens": 12736,
                "reasoning_output_tokens": 0,
            }],
        }

    def test_carries_no_monetary_field(self):
        data = usage_record_to_data(
            _record(), invocation_id="INV-1", role="worker", adapter="claude_cli", model=None,
        )
        rendered = json.dumps(data)
        for canary in ("cost", "price", "usd", "USD"):
            assert canary not in rendered

    def test_is_json_serializable_as_is(self):
        data = usage_record_to_data(
            _record(), invocation_id="INV-1", role="worker", adapter="codex_cli", model=None,
        )
        json.dumps(data)  # must not raise


class TestWriteUsageRecord:
    def test_first_write_creates_the_schema_1_keyed_document(self, tmp_path):
        path = tmp_path / "usage.json"
        record = usage_record_to_data(_record(), invocation_id="INV-1", role="worker",
                                       adapter="claude_cli", model="claude-haiku-5-5")
        document = write_usage_record(path, "RUN-A", "H001", record)
        assert document == {"schema_version": 1, "run_id": "RUN-A", "records": {"H001": record}}
        assert json.loads(path.read_text(encoding="utf-8")) == document

    def test_a_second_hop_is_appended_beside_the_first(self, tmp_path):
        path = tmp_path / "usage.json"
        first = usage_record_to_data(_record(), invocation_id="INV-1", role="worker",
                                      adapter="claude_cli", model="claude-haiku-5-5")
        second = usage_record_to_data(_record(coverage=UsageCoverage.NONE, input_tokens=None,
                                               output_tokens=None, cache_read_tokens=None,
                                               cache_creation_tokens=None,
                                               reasoning_output_tokens=None, per_model=()),
                                       invocation_id="INV-2", role="independent_reviewer",
                                       adapter="codex_cli", model=None)
        write_usage_record(path, "RUN-A", "H001", first)
        document = write_usage_record(path, "RUN-A", "H002", second)
        assert set(document["records"]) == {"H001", "H002"}
        assert document["records"]["H002"]["coverage"] == "none"

    def test_repeating_the_identical_record_for_the_same_hop_is_idempotent(self, tmp_path):
        path = tmp_path / "usage.json"
        record = usage_record_to_data(_record(), invocation_id="INV-1", role="worker",
                                       adapter="claude_cli", model="claude-haiku-5-5")
        write_usage_record(path, "RUN-A", "H001", record)
        before = path.read_bytes()
        write_usage_record(path, "RUN-A", "H001", record)
        assert path.read_bytes() == before

    def test_a_conflicting_record_for_an_already_recorded_hop_is_refused(self, tmp_path):
        path = tmp_path / "usage.json"
        record = usage_record_to_data(_record(), invocation_id="INV-1", role="worker",
                                       adapter="claude_cli", model="claude-haiku-5-5")
        write_usage_record(path, "RUN-A", "H001", record)
        conflicting = dict(record, input_tokens=999)
        with pytest.raises(Exception):
            write_usage_record(path, "RUN-A", "H001", conflicting)
        # The original record is left exactly as it was, not overwritten.
        assert json.loads(path.read_text(encoding="utf-8"))["records"]["H001"]["input_tokens"] == 2

    def test_a_mismatched_run_id_against_an_existing_file_is_refused(self, tmp_path):
        path = tmp_path / "usage.json"
        record = usage_record_to_data(_record(), invocation_id="INV-1", role="worker",
                                       adapter="claude_cli", model="claude-haiku-5-5")
        write_usage_record(path, "RUN-A", "H001", record)
        with pytest.raises(Exception):
            write_usage_record(path, "RUN-OTHER", "H002", record)
