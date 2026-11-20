"""BP6a: the sanitized audit projection built from a synthetic archived Attempt.

Every test below is mutation-sensitive: it does not merely assert that a
field is present with *some* value, it changes one source fact and asserts
the projected field changes with it (or, for an excluded field, stays
provably absent regardless of the source value). ``tests/fixtures/bp6``
builds the synthetic archive trees; the real Evidence repository is never
read here.
"""

from __future__ import annotations

import json

import pytest

from tests.fixtures.bp6 import archive_builder as ab
from workflow_coordinator.frontdoor import audit as au


def _project(tmp_path, **kwargs):
    attempt_dir = ab.build_attempt(tmp_path, **kwargs)
    return au.project_audit(attempt_dir)


class TestCompletedProjection:
    def test_every_frozen_field_has_the_exact_source_value(self, tmp_path):
        hops = [
            ab.HopSpec(
                hop_id="H001", sequence=1, role="worker", phase="implement",
                outcome="completed", applied_route="accept", actual_commits=("1" * 40,),
                finding_delta_ids=("F001",), adapter="claude_cli", model="claude-sonnet-5-5",
                reasoning="high",
                observations=(ab.ObservationSpec(command_name="task-check", status="pass", exit_code=0,
                                                   stat_fingerprint=ab.ADMITTED_TASK_CHECK_FINGERPRINT),),
            ),
        ]
        findings = [ab.FindingSpec(finding_id="F001", state="resolved", blocking=False, disposition="fixed",
                                    introduced_by_hop="H001", changed_by_hop="H001")]
        audit = _project(
            tmp_path, hops=hops, hop_used=1, findings=findings, run_id="RUN-abc", work_item_id="WI-ABC-001",
            policy_id="repository-standard", policy_revision="7", profile_id="robust", profile_revision="3",
            baseline_head="a" * 40, final_head="b" * 40,
        )

        assert audit["schema"] == "wfc.audit/1"
        run = audit["run"]
        assert run["run_id"] == "RUN-abc"
        assert run["work_item_id"] == "WI-ABC-001"
        assert run["status"] == "completed"
        assert run["stop_reason"] is None
        assert run["hops_used"] == 1
        assert run["hop_limit"] == 8
        assert run["baseline_head"] == "a" * 40
        assert run["final_head"] == "b" * 40
        assert run["worktree_clean"] is True
        assert run["human_question_present"] is False
        assert run["semantic_stop_present"] is False

        governance = audit["governance"]
        assert governance["policy_id"] == "repository-standard"
        assert governance["policy_revision"] == "7"
        assert governance["profile_id"] == "robust"
        assert governance["profile_revision"] == "3"
        assert governance["agent_contract"] == {
            "id": "workflow_coordinator.agent_operating_contract", "version": 6, "sha256": "f" * 64,
        }
        assert governance["governing_inputs"] == {
            ".wfc/commissions/fixture/work_item.yaml": "1" * 64,
            ".wfc/commissions/fixture/profile.yaml": "2" * 64,
            ".wfc/commissions/fixture/policy.yaml": "3" * 64,
        }
        assert governance["component_digests"] == {
            "simple-self-check": {"path": "resources/components/simple-self-check.md", "digest": "e" * 64},
        }
        assert governance["role_bindings"] == {
            "worker": {"adapter": "claude_cli", "model": "claude-sonnet-5-5", "reasoning": "high"},
        }
        assert governance["executable"] == {"name": "codex"}

        [hop] = audit["hops"]
        assert hop["hop_id"] == "H001"
        assert hop["sequence"] == 1
        assert hop["phase"] == "implement"
        assert hop["role"] == "worker"
        assert hop["adapter"] == "claude_cli"
        assert hop["model"] == "claude-sonnet-5-5"
        assert hop["reasoning"] == "high"
        assert hop["outcome"] == "completed"
        assert hop["applied_route"] == "accept"
        assert hop["commits"] == ["1" * 40]
        assert hop["finding_delta_ids"] == ["F001"]
        assert hop["usage"] is None
        [evidence] = hop["evidence"]
        assert evidence["command_name"] == "task-check"
        assert evidence["status"] == "pass"
        assert evidence["exit_code"] == 0
        assert evidence["argv_digest"] == "a" * 64
        assert evidence["stdout_sha256"] == "b" * 64
        assert evidence["stderr_sha256"] == "c" * 64
        assert evidence["executable_identity_stable"] is True

        [finding] = audit["findings"]
        assert finding == {
            "finding_id": "F001", "state": "resolved", "blocking": False, "disposition": "fixed",
            "introduced_by_hop": "H001", "changed_by_hop": "H001",
        }
        assert audit["anomaly_count"] == 0
        assert audit["lifecycle"] is None
        assert "no_lifecycle_facts" in audit["limits"]
        assert "usage_not_reported" in audit["limits"]

    @pytest.mark.parametrize("field,value", [
        ("baseline_head", "9" * 40),
        ("work_item_id", "WI-MUTATED-001"),
    ])
    def test_mutating_a_run_fact_changes_the_projection(self, tmp_path, field, value):
        base = _project(tmp_path / "base")
        mutated = _project(tmp_path / "mutated", **{field: value})
        assert base["run"][field] != mutated["run"][field]
        assert mutated["run"][field] == value

    def test_mutating_a_hop_outcome_changes_only_that_hop(self, tmp_path):
        hops = [
            ab.HopSpec(hop_id="H001", sequence=1, role="worker", outcome="changes_required"),
        ]
        audit = _project(tmp_path, hops=hops, hop_used=1)
        assert audit["hops"][0]["outcome"] == "changes_required"


class TestExecutableIdentityStability:
    def test_matching_fingerprints_are_stable(self, tmp_path):
        hops = [ab.HopSpec(hop_id="H001", sequence=1,
                            observations=(ab.ObservationSpec(stat_fingerprint=ab.ADMITTED_TASK_CHECK_FINGERPRINT),))]
        audit = _project(tmp_path, hops=hops, hop_used=1)
        assert audit["hops"][0]["evidence"][0]["executable_identity_stable"] is True

    def test_mismatching_fingerprints_are_unstable(self, tmp_path):
        fingerprint = {"st_dev": 1, "st_ino": 2, "st_mode": 33261, "st_size": 999999, "st_mtime_ns": 20}
        hops = [ab.HopSpec(hop_id="H001", sequence=1,
                            observations=(ab.ObservationSpec(stat_fingerprint=fingerprint),))]
        audit = _project(tmp_path, hops=hops, hop_used=1)
        assert audit["hops"][0]["evidence"][0]["executable_identity_stable"] is False

    def test_absent_observation_fingerprint_omits_the_comparison(self, tmp_path):
        hops = [ab.HopSpec(hop_id="H001", sequence=1,
                            observations=(ab.ObservationSpec(stat_fingerprint=None),))]
        audit = _project(tmp_path, hops=hops, hop_used=1)
        assert "executable_identity_stable" not in audit["hops"][0]["evidence"][0]


class TestRawDigests:
    def test_every_raw_file_digest_and_byte_count_matches_the_manifest(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        manifest = json.loads((attempt_dir / "manifest.json").read_text())
        audit = au.project_audit(attempt_dir)
        by_path = {entry["path"]: entry for entry in manifest["copied_files"]}
        assert len(audit["raw"]["files"]) == len(by_path)
        for entry in audit["raw"]["files"]:
            source = by_path[entry["path"]]
            assert entry["sha256"] == source["sha256"]
            assert entry["bytes"] == source["bytes"]
            assert "source" not in entry

    def test_manifest_sha256_matches_the_manifest_files_own_digest(self, tmp_path):
        import hashlib
        attempt_dir = ab.build_attempt(tmp_path)
        audit = au.project_audit(attempt_dir)
        expected = hashlib.sha256((attempt_dir / "manifest.json").read_bytes()).hexdigest()
        assert audit["raw"]["manifest_sha256"] == expected

    def test_mutating_a_manifest_digest_changes_the_projected_digest(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        manifest_path = attempt_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        target_path = manifest["copied_files"][0]["path"]
        manifest["copied_files"][0]["sha256"] = "9" * 64
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        audit = au.project_audit(attempt_dir)
        entry = next(item for item in audit["raw"]["files"] if item["path"] == target_path)
        assert entry["sha256"] == "9" * 64

    def test_raw_retention_is_the_callers_supplied_value(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        for retention in ("local", "commit", "discard"):
            audit = au.project_audit(attempt_dir, raw_retention=retention)
            assert audit["raw"]["retention"] == retention


class TestFindingBearingRun:
    def test_findings_expose_only_identifier_state_blocking_disposition_and_hops(self, tmp_path):
        findings = [
            ab.FindingSpec(finding_id="F001", state="open", blocking=True, disposition="pending",
                            introduced_by_hop="H001", changed_by_hop="H001"),
            ab.FindingSpec(finding_id="F002", state="superseded", blocking=False, disposition="superseded_by_F001",
                            introduced_by_hop="H001", changed_by_hop="H002"),
        ]
        audit = _project(tmp_path, findings=findings)
        assert audit["findings"] == [
            {"finding_id": "F001", "state": "open", "blocking": True, "disposition": "pending",
             "introduced_by_hop": "H001", "changed_by_hop": "H001"},
            {"finding_id": "F002", "state": "superseded", "blocking": False, "disposition": "superseded_by_F001",
             "introduced_by_hop": "H001", "changed_by_hop": "H002"},
        ]
        rendered = json.dumps(audit)
        assert ab.PROVIDER_PROSE_CANARY not in rendered

    def test_mutating_finding_state_changes_the_projection(self, tmp_path):
        findings = [ab.FindingSpec(finding_id="F001", state="open")]
        before = _project(tmp_path / "before", findings=findings)
        findings[0].state = "resolved"
        after = _project(tmp_path / "after", findings=findings)
        assert before["findings"][0]["state"] == "open"
        assert after["findings"][0]["state"] == "resolved"


class TestStoppedRunsProjectTruthfully:
    @pytest.mark.parametrize("status,stop_reason", [
        ("awaiting_human", "human_question_pending"),
        ("awaiting_human_budget", "hop_budget_exhausted"),
        ("reconcile_required", "convergence_conflict"),
        ("stopped", "max_hops_reached"),
    ])
    def test_stop_status_and_stop_reason_project_exactly_and_never_as_completed(self, tmp_path, status, stop_reason):
        audit = _project(tmp_path, status=status, stop_reason=stop_reason, human_question="CANARY-QUESTION")
        assert audit["run"]["status"] == status
        assert audit["run"]["status"] != "completed"
        assert audit["run"]["stop_reason"] == stop_reason
        assert audit["run"]["human_question_present"] is True
        rendered = json.dumps(audit)
        assert "CANARY-QUESTION" not in rendered

    def test_awaiting_human_without_a_question_reports_absence(self, tmp_path):
        audit = _project(tmp_path, status="awaiting_human", human_question=None)
        assert audit["run"]["human_question_present"] is False


class TestLifecycleFacts:
    def test_absent_lifecycle_is_null_with_a_named_limit(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        audit = au.project_audit(attempt_dir)
        assert audit["lifecycle"] is None
        assert "no_lifecycle_facts" in audit["limits"]

    @pytest.mark.parametrize("classification", ["interrupted", "uncertain_launch"])
    def test_supplied_classification_without_preservation_is_recorded(self, tmp_path, classification):
        attempt_dir = ab.build_attempt(tmp_path)
        audit = au.project_audit(attempt_dir, lifecycle={"classification": classification})
        assert audit["lifecycle"] == {"classification": classification, "preservation": None}
        assert "preservation_not_recorded" in audit["limits"]
        assert "no_lifecycle_facts" not in audit["limits"]

    def test_supplied_preservation_result_projects_allow_listed_facts_only(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        preservation = {
            "outcome": "result", "ref": "refs/wfc/preserved/RUN-x/1", "commit": "a" * 40, "tree": "b" * 40,
            "file_count": 3, "bytes": 512, "quiescence": "unattested", "reason_code": None,
            "reason": "CANARY-FREE-TEXT-REASON-should-never-leak",
        }
        audit = au.project_audit(attempt_dir, lifecycle={"classification": "stopped", "preservation": preservation})
        assert audit["lifecycle"]["preservation"] == {
            "outcome": "result", "ref": "refs/wfc/preserved/RUN-x/1", "commit": "a" * 40, "tree": "b" * 40,
            "file_count": 3, "bytes": 512, "quiescence": "unattested", "reason_code": None,
        }
        assert "reason" not in audit["lifecycle"]["preservation"]
        rendered = json.dumps(audit)
        assert "CANARY-FREE-TEXT-REASON-should-never-leak" not in rendered
        assert "preservation_not_recorded" not in audit["limits"]

    def test_supplied_preservation_refusal_projects_reason_code_not_reason_text(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        preservation = {
            "outcome": "refused", "reason_code": "lock_contended",
            "reason": "CANARY-REFUSAL-PROSE-should-never-leak",
        }
        audit = au.project_audit(attempt_dir, lifecycle={"classification": "stopped", "preservation": preservation})
        assert audit["lifecycle"]["preservation"]["outcome"] == "refused"
        assert audit["lifecycle"]["preservation"]["reason_code"] == "lock_contended"
        rendered = json.dumps(audit)
        assert "CANARY-REFUSAL-PROSE-should-never-leak" not in rendered


class TestUsageIsAlwaysNullNeverZero:
    def test_every_hop_usage_is_null(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        audit = au.project_audit(attempt_dir)
        assert audit["hops"], "fixture must carry at least one Hop"
        for hop in audit["hops"]:
            assert hop["usage"] is None
            assert hop["usage"] != 0


class TestMissingOptionalSourceFiles:
    def test_missing_execution_evidence_yields_empty_evidence_and_a_named_limit(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path, include_execution_evidence=False)
        audit = au.project_audit(attempt_dir)
        assert audit["hops"]
        for hop in audit["hops"]:
            assert hop["evidence"] == []
        assert "trusted_evidence_not_recorded" in audit["limits"]

    def test_missing_operator_summary_nulls_final_head_fallback_and_worktree_clean(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path, include_operator_summary=False)
        audit = au.project_audit(attempt_dir)
        assert audit["run"]["worktree_clean"] is None
        assert audit["run"]["final_head"] is not None  # falls back to run.json's current_head
        assert "run_summary_not_recorded" in audit["limits"]

    def test_missing_operator_plan_nulls_governance(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path, include_operator_plan=False)
        audit = au.project_audit(attempt_dir)
        assert audit["governance"] is None
        assert "governance_not_recorded" in audit["limits"]

    def test_missing_manifest_raises_without_crashing_uncaught(self, tmp_path):
        attempt_dir = tmp_path / "empty-attempt"
        attempt_dir.mkdir()
        with pytest.raises(au.AuditError):
            au.project_audit(attempt_dir)


class TestDeterminism:
    def test_identical_input_yields_byte_identical_output(self, tmp_path):
        first = ab.build_attempt(tmp_path / "a")
        second = ab.build_attempt(tmp_path / "b")
        rendered_first = au.render_audit_json(au.project_audit(first))
        rendered_second = au.render_audit_json(au.project_audit(second))
        assert rendered_first == rendered_second
        assert rendered_first.endswith("\n")

    def test_one_changed_fact_yields_different_bytes(self, tmp_path):
        first = ab.build_attempt(tmp_path / "a")
        second = ab.build_attempt(tmp_path / "b", stop_reason="a_different_fact")
        rendered_first = au.render_audit_json(au.project_audit(first))
        rendered_second = au.render_audit_json(au.project_audit(second))
        assert rendered_first != rendered_second

    def test_output_carries_no_timestamp_field(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        audit = au.project_audit(attempt_dir)
        assert "archived_at" not in json.dumps(audit)


class TestWriteAuditCreateOnly:
    def test_write_creates_audit_json_beside_manifest(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        target = au.write_audit(attempt_dir)
        assert target == attempt_dir / "audit.json"
        assert target.is_file()

    def test_write_is_idempotent_for_identical_content(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        first = au.write_audit(attempt_dir)
        before = first.read_bytes()
        second = au.write_audit(attempt_dir)
        assert second == first
        assert first.read_bytes() == before

    def test_write_refuses_a_differing_existing_file(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        target = attempt_dir / "audit.json"
        target.write_text("not a real projection\n")
        with pytest.raises(au.AuditError):
            au.write_audit(attempt_dir)
        assert target.read_text() == "not a real projection\n"

    def test_write_never_touches_manifest_or_raw_files(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        manifest_path = attempt_dir / "manifest.json"
        before_manifest = manifest_path.read_bytes()
        before_raw = {
            path: path.read_bytes()
            for path in attempt_dir.rglob("*")
            if path.is_file() and path.name not in ("manifest.json", "audit.json")
        }
        au.write_audit(attempt_dir)
        assert manifest_path.read_bytes() == before_manifest
        for path, content in before_raw.items():
            assert path.read_bytes() == content

    def test_write_performs_no_git_operation(self, tmp_path, monkeypatch):
        import subprocess as subprocess_module

        def _forbidden(*args, **kwargs):
            raise AssertionError("write_audit must never invoke a subprocess")

        monkeypatch.setattr(subprocess_module, "run", _forbidden)
        monkeypatch.setattr(subprocess_module, "Popen", _forbidden)
        attempt_dir = ab.build_attempt(tmp_path)
        au.write_audit(attempt_dir)  # must not raise


class TestDefaultDenyAllowList:
    def test_an_unexpected_key_in_an_assembled_section_is_refused(self):
        with pytest.raises(au.AuditError):
            au._assert_keys({"allowed": 1, "not_allowed": 2}, frozenset({"allowed"}), context="test section")

    def test_hop_entry_keys_are_exactly_the_frozen_set(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        audit = au.project_audit(attempt_dir)
        for hop in audit["hops"]:
            assert set(hop.keys()) == au._HOP_KEYS

    def test_top_level_keys_are_exactly_the_frozen_set(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        audit = au.project_audit(attempt_dir)
        assert set(audit.keys()) == au._TOP_KEYS
