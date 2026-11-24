"""BP6a: the consolidated ``wfc report``, built purely from an audit projection."""

from __future__ import annotations

import json

import pytest

from tests.fixtures.bp6 import archive_builder as ab
from workflow_coordinator.frontdoor import audit as au, report as rp
from workflow_coordinator.frontdoor.cli import main as cli_main


def _audit_for(tmp_path, **kwargs):
    attempt_dir = ab.build_attempt(tmp_path, **kwargs)
    return au.project_audit(attempt_dir)


class TestCompletedReport:
    def test_completed_run_reports_success_with_program_and_hop_budget(self, tmp_path):
        audit = _audit_for(tmp_path)
        report = rp.build_report(audit)
        assert report["status"] == "completed"
        assert report["status_known"] is True
        assert report["program"] == "robust@1"
        assert report["hops_used"] == 2
        assert report["hop_limit"] == 8
        assert report["next_action"] is None
        assert report["commits"] == ["1" * 40, "2" * 40]

    def test_text_render_contains_outcome_program_budget_commits_and_evidence(self, tmp_path):
        audit = _audit_for(tmp_path)
        text = rp.render_report_text(rp.build_report(audit))
        assert "outcome" in text and "completed" in text
        assert "robust@1" in text
        assert "2 of 8 hops" in text
        assert "1" * 40 in text
        assert "evidence" in text and "task-check" in text
        assert "findings    none open" in text
        assert "not done    nothing was pushed" in text

    def test_json_render_carries_the_same_content_as_the_dict(self, tmp_path):
        audit = _audit_for(tmp_path)
        report = rp.build_report(audit)
        assert json.loads(rp.render_report_json(report)) == report


class TestFindingBearingReport:
    def test_open_blocking_findings_are_listed_by_identifier(self, tmp_path):
        findings = [
            ab.FindingSpec(finding_id="F001", state="open", blocking=True),
            ab.FindingSpec(finding_id="F002", state="open", blocking=False),
            ab.FindingSpec(finding_id="F003", state="resolved", blocking=True),
        ]
        audit = _audit_for(tmp_path, findings=findings)
        report = rp.build_report(audit)
        assert report["findings_open_blocking"] == ["F001"]
        text = rp.render_report_text(report)
        assert "F001" in text
        assert "F002" not in text
        assert "F003" not in text


class TestStopStatusesNeverReportedAsSuccess:
    @pytest.mark.parametrize("status,stop_reason", [
        ("awaiting_human", "human_question_pending"),
        ("awaiting_human_budget", "hop_budget_exhausted"),
        ("reconcile_required", "convergence_conflict"),
        ("stopped", "max_hops_reached"),
    ])
    def test_stop_status_reports_status_stop_reason_and_the_fixed_next_choice_line(self, tmp_path, status, stop_reason):
        # An "awaiting_human" Attempt with a recorded human question is the
        # shape the fixed table's text actually describes (WI-WFC-RECOVERY-
        # ROUTING-001); the no-question shape has its own dedicated test
        # below, since promising to "answer the open question" when none
        # exists is exactly the incident this WorkItem fixes.
        extra = {"human_question": "Which branch should be authoritative?"} if status == "awaiting_human" else {}
        audit = _audit_for(tmp_path, status=status, stop_reason=stop_reason, **extra)
        report = rp.build_report(audit)
        assert report["status"] == status
        assert report["status"] != "completed"
        assert report["stop_reason"] == stop_reason
        assert report["next_action"] == rp._NEXT_HUMAN_CHOICE[status]
        text = rp.render_report_text(report)
        assert status in text
        assert stop_reason in text
        assert "completed" not in text.split("\n")[2]  # the outcome line itself

    def test_awaiting_human_without_a_recorded_question_never_promises_one(self, tmp_path):
        """A mechanical (not semantic) awaiting_human stop names only a real path."""

        audit = _audit_for(
            tmp_path, status="awaiting_human",
            stop_reason="mechanical_preemption:required durable artifact has no CommitIntent",
        )
        report = rp.build_report(audit)
        assert report["next_action"] != rp._NEXT_HUMAN_CHOICE["awaiting_human"]
        assert "answer the open question" not in report["next_action"]
        assert "wfc resume" in report["next_action"]

    @pytest.mark.parametrize("classification", ["interrupted", "uncertain_launch"])
    def test_lifecycle_supplied_classification_drives_the_report_status(self, tmp_path, classification):
        attempt_dir = ab.build_attempt(tmp_path)
        audit = au.project_audit(attempt_dir, lifecycle={"classification": classification})
        report = rp.build_report(audit)
        assert report["status"] == classification
        assert report["status"] != "completed"
        assert report["next_action"] == rp._NEXT_HUMAN_CHOICE[classification]

    def test_unknown_status_is_reported_as_unknown_never_as_success(self, tmp_path):
        audit = _audit_for(tmp_path)
        audit = dict(audit)
        audit["run"] = dict(audit["run"])
        audit["run"]["status"] = "some_future_status_this_report_has_never_seen"
        report = rp.build_report(audit)
        assert report["status"] == "unknown"
        assert report["status_known"] is False
        assert report["next_action"] == rp._UNKNOWN_NEXT_ACTION
        text = rp.render_report_text(report)
        assert "unknown" in text
        assert "not reported as success" in text


class TestReportInterpretsOnlyPersistedFacts:
    def test_no_archive_reference_without_one_supplied(self, tmp_path):
        audit = _audit_for(tmp_path)
        report = rp.build_report(audit)
        assert report["archive_ref"] is None
        assert "archive" not in rp.render_report_text(report)

    def test_archive_reference_appears_only_when_supplied(self, tmp_path):
        audit = _audit_for(tmp_path)
        report = rp.build_report(audit, archive_ref="evidence @ deadbeef archive/x/audit.json")
        assert report["archive_ref"] == "evidence @ deadbeef archive/x/audit.json"
        assert "archive" in rp.render_report_text(report)

    def test_limits_are_surfaced_as_an_explicit_not_done_line(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path, include_execution_evidence=False)
        audit = au.project_audit(attempt_dir)
        report = rp.build_report(audit)
        assert any("trusted_evidence_not_recorded" in item for item in report["not_done"])


class TestCliDispatchAndExitCodes:
    def test_report_reads_an_attempt_directory_directly(self, tmp_path, capsys):
        attempt_dir = ab.build_attempt(tmp_path)
        code = cli_main(["report", str(attempt_dir)])
        assert code == 0
        out = capsys.readouterr().out
        assert "WFC RUN" in out

    def test_report_reads_an_already_written_audit_json(self, tmp_path, capsys):
        attempt_dir = ab.build_attempt(tmp_path)
        au.write_audit(attempt_dir)
        code = cli_main(["report", str(attempt_dir / "audit.json")])
        assert code == 0
        out = capsys.readouterr().out
        assert "WFC RUN" in out

    def test_report_json_flag_emits_parseable_json(self, tmp_path, capsys):
        attempt_dir = ab.build_attempt(tmp_path)
        code = cli_main(["report", str(attempt_dir), "--json"])
        assert code == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["schema"] == "wfc.report/1"

    def test_report_on_a_non_attempt_path_exits_2(self, tmp_path, capsys):
        missing = tmp_path / "not-an-attempt"
        code = cli_main(["report", str(missing)])
        assert code == 2

    def test_audit_then_report_round_trip_through_the_cli(self, tmp_path, capsys):
        attempt_dir = ab.build_attempt(tmp_path)
        assert cli_main(["audit", str(attempt_dir)]) == 0
        capsys.readouterr()
        assert cli_main(["report", str(attempt_dir)]) == 0
        out = capsys.readouterr().out
        assert "completed" in out

    def test_existing_dispatch_is_unchanged(self, tmp_path, capsys):
        code = cli_main(["--version", "--json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert "version" in payload
