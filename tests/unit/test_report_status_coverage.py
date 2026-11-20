"""BP6b (BP6a review observations): ``wfc report`` maps a privacy-guard
refusal to exit code 3, and the fixed next-choice table covers every real
``model.RunStatus`` value and every ``frontdoor.lifecycle.EXIT_CODES``
classification.
"""

from __future__ import annotations

import json

import pytest

from tests.fixtures.bp6 import archive_builder as ab
from workflow_coordinator.frontdoor import audit as au, report as rp
from workflow_coordinator.frontdoor.cli import main as cli_main
from workflow_coordinator.frontdoor.lifecycle import EXIT_CODES
from workflow_coordinator.model import RunStatus


def _audit_for(tmp_path, **kwargs):
    attempt_dir = ab.build_attempt(tmp_path, **kwargs)
    return au.project_audit(attempt_dir)


class TestNextChoiceTableCoverage:
    def test_table_covers_every_real_run_status_value(self):
        missing = {status.value for status in RunStatus} - {"completed"} - set(rp._NEXT_HUMAN_CHOICE)
        assert missing == set(), f"model.RunStatus values missing a fixed next-choice line: {missing}"

    def test_table_covers_every_lifecycle_exit_code_classification(self):
        missing = set(EXIT_CODES) - {"completed"} - set(rp._NEXT_HUMAN_CHOICE)
        assert missing == set(), f"frontdoor.lifecycle.EXIT_CODES classifications missing a fixed next-choice line: {missing}"

    @pytest.mark.parametrize("status", sorted({s.value for s in RunStatus} - {"completed"}))
    def test_every_run_status_produces_a_fixed_truthful_line_not_unknown(self, tmp_path, status):
        audit = _audit_for(tmp_path, status=status, stop_reason="fixture-stop-reason")
        report = rp.build_report(audit)
        assert report["status"] == status
        assert report["status_known"] is True
        assert report["next_action"] == rp._NEXT_HUMAN_CHOICE[status]
        assert report["next_action"] != rp._UNKNOWN_NEXT_ACTION

    @pytest.mark.parametrize("classification", sorted(set(EXIT_CODES) - {"completed"}))
    def test_every_lifecycle_classification_produces_a_fixed_truthful_line_not_unknown(self, tmp_path, classification):
        attempt_dir = ab.build_attempt(tmp_path)
        audit = au.project_audit(attempt_dir, lifecycle={"classification": classification})
        report = rp.build_report(audit)
        assert report["status"] == classification
        assert report["status_known"] is True
        assert report["next_action"] == rp._NEXT_HUMAN_CHOICE[classification]
        assert report["next_action"] != rp._UNKNOWN_NEXT_ACTION

    def test_a_genuinely_unknown_value_is_still_reported_as_unknown(self, tmp_path):
        audit = _audit_for(tmp_path)
        audit = dict(audit)
        audit["run"] = dict(audit["run"])
        audit["run"]["status"] = "no_real_vocabulary_has_ever_used_this_value"
        report = rp.build_report(audit)
        assert report["status"] == "unknown"
        assert report["status_known"] is False
        assert report["next_action"] == rp._UNKNOWN_NEXT_ACTION


def _attempt_with_forged_governing_input(tmp_path):
    """Reproduce the BP6a broad review's own repro for the uncovered exit
    path: an absolute path injected as a ``governing_inputs`` key. Admission
    never produces this in production; it is forged directly here, below
    the CLI, exactly as the review describes."""

    attempt_dir = ab.build_attempt(tmp_path)
    plan_path = attempt_dir / "run" / "operator-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["governing_inputs"]["/etc/shadow"] = "f" * 64
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return attempt_dir


class TestReportProjectionErrorExitCode:
    def test_wfc_report_on_a_guard_refusal_exits_3_naming_only_the_json_path(self, tmp_path, capsys):
        attempt_dir = _attempt_with_forged_governing_input(tmp_path)

        code = cli_main(["report", str(attempt_dir)])
        assert code == 3
        captured = capsys.readouterr()
        assert "/etc/shadow" not in captured.err
        assert "governance.governing_inputs" in captured.err
        assert captured.out == ""

    def test_wfc_audit_on_the_same_attempt_also_exits_3(self, tmp_path, capsys):
        attempt_dir = _attempt_with_forged_governing_input(tmp_path)
        code = cli_main(["audit", str(attempt_dir)])
        assert code == 3
        assert not (attempt_dir / "audit.json").exists()

    def test_ordinary_structural_refusals_still_exit_2_not_3(self, tmp_path, capsys):
        missing = tmp_path / "not-an-attempt"
        code = cli_main(["report", str(missing)])
        assert code == 2
