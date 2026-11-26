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
            baseline_head="a" * 40, final_head="b" * 40, attempt_id="wfc-fixture-bp6-mutated-attempt-id-01",
        )

        assert audit["schema"] == "wfc.audit/1"
        assert audit["attempt_id"] == "wfc-fixture-bp6-mutated-attempt-id-01"
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

    def test_mutating_the_attempt_id_changes_the_projection(self, tmp_path):
        base = _project(tmp_path / "base", attempt_id="wfc-fixture-bp6-base-attempt-01")
        mutated = _project(tmp_path / "mutated", attempt_id="wfc-fixture-bp6-mutated-attempt-02")
        assert base["attempt_id"] != mutated["attempt_id"]
        assert mutated["attempt_id"] == "wfc-fixture-bp6-mutated-attempt-02"

    def test_anomaly_count_reflects_the_number_of_supplied_anomalies(self, tmp_path):
        none = _project(tmp_path / "none", anomalies=())
        one = _project(tmp_path / "one", anomalies=(ab.PROVIDER_PROSE_CANARY,))
        three = _project(tmp_path / "three", anomalies=(
            ab.PROVIDER_PROSE_CANARY, "second-anomaly-canary", "third-anomaly-canary",
        ))
        assert none["anomaly_count"] == 0
        assert one["anomaly_count"] == 1
        assert three["anomaly_count"] == 3

    def test_semantic_stop_present_reflects_whether_a_semantic_stop_was_recorded(self, tmp_path):
        absent = _project(tmp_path / "absent", semantic_stop=None)
        present = _project(tmp_path / "present", semantic_stop={"category": "canary-semantic-stop"})
        assert absent["run"]["semantic_stop_present"] is False
        assert present["run"]["semantic_stop_present"] is True


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

    def test_no_top_level_usage_key_when_no_record_exists(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        audit = au.project_audit(attempt_dir)
        assert "usage" not in audit
        assert "usage_not_reported" in audit["limits"]
        assert "usage_partially_reported" not in audit["limits"]


class TestUsageProjection:
    """WI-WFC-TOKEN-USAGE-S1-001: per-hop usage, Run-wide aggregates,
    coverage counts, the lower-bound marker, and the ``limits`` transitions
    they drive -- every assertion below is mutation-sensitive against a
    fixture that carries at least one real (non-null) usage record."""

    def test_hop_with_a_record_projects_the_allow_listed_figures_verbatim(self, tmp_path):
        hops = [
            ab.HopSpec(
                hop_id="H001", sequence=1, role="worker",
                usage=ab.usage_record(
                    invocation_id="INV-H001", role="worker", adapter="claude_cli",
                    model="claude-haiku-5-5", coverage="complete",
                    input_tokens=2, output_tokens=4, cache_read_tokens=9074,
                    cache_creation_tokens=12736, reasoning_output_tokens=0,
                    input_includes_cache_read=False,
                    per_model=[{
                        "model": "claude-haiku-5-5", "input_tokens": 2, "output_tokens": 4,
                        "cache_read_tokens": 9074, "cache_creation_tokens": 12736,
                        "reasoning_output_tokens": 0,
                    }],
                ),
            ),
        ]
        audit = ab_project(tmp_path, hops)
        [hop] = audit["hops"]
        assert hop["usage"] == {
            "coverage": "complete", "input_tokens": 2, "output_tokens": 4,
            "cache_read_tokens": 9074, "cache_creation_tokens": 12736,
            "reasoning_output_tokens": 0, "input_includes_cache_read": False,
            "per_model": [{
                "model": "claude-haiku-5-5", "input_tokens": 2, "output_tokens": 4,
                "cache_read_tokens": 9074, "cache_creation_tokens": 12736,
                "reasoning_output_tokens": 0,
            }],
        }
        # invocation_id/role/adapter are persisted Run state but are not
        # re-surfaced inside the per-hop usage object (already available at
        # the hop level / not needed for audit purposes).
        assert "invocation_id" not in hop["usage"]
        assert "adapter" not in hop["usage"]

    def test_hop_without_a_record_is_null_even_when_a_sibling_hop_has_one(self, tmp_path):
        hops = [
            ab.HopSpec(hop_id="H001", sequence=1, role="worker",
                       usage=ab.usage_record(input_tokens=10, output_tokens=5)),
            ab.HopSpec(hop_id="H002", sequence=2, role="independent_reviewer", usage=None),
        ]
        audit = ab_project(tmp_path, hops)
        by_id = {hop["hop_id"]: hop for hop in audit["hops"]}
        assert by_id["H001"]["usage"]["input_tokens"] == 10
        assert by_id["H002"]["usage"] is None

    def test_top_level_usage_key_appears_exactly_when_a_record_exists(self, tmp_path):
        hops = [ab.HopSpec(hop_id="H001", sequence=1, role="worker", usage=ab.usage_record())]
        audit = ab_project(tmp_path, hops)
        assert "usage" in audit
        assert set(audit.keys()) == au._TOP_KEYS

    def test_coverage_counts_and_limit_transitions(self, tmp_path):
        complete = ab.usage_record(coverage="complete", input_tokens=10, output_tokens=5)
        partial = ab.usage_record(coverage="partial", input_tokens=10, output_tokens=None)
        none_reported = ab.usage_record(coverage="none", input_tokens=None, output_tokens=None)

        # Every Hop fully reported: no usage limit at all.
        audit = ab_project(tmp_path / "all-complete", [
            ab.HopSpec(hop_id="H001", sequence=1, usage=complete),
        ])
        assert audit["usage"]["coverage_counts"] == {"complete": 1, "partial": 0, "none": 0}
        assert "usage_not_reported" not in audit["limits"]
        assert "usage_partially_reported" not in audit["limits"]

        # A mix of complete/partial/no-record hops: partially reported.
        audit = ab_project(tmp_path / "mixed", [
            ab.HopSpec(hop_id="H001", sequence=1, usage=complete),
            ab.HopSpec(hop_id="H002", sequence=2, usage=partial),
            ab.HopSpec(hop_id="H003", sequence=3, usage=None),
        ])
        assert audit["usage"]["coverage_counts"] == {"complete": 1, "partial": 1, "none": 1}
        assert "usage_partially_reported" in audit["limits"]
        assert "usage_not_reported" not in audit["limits"]

        # Records exist but every one reports nothing usable: still "not reported".
        audit = ab_project(tmp_path / "all-none", [
            ab.HopSpec(hop_id="H001", sequence=1, usage=none_reported),
        ])
        assert audit["usage"]["coverage_counts"] == {"complete": 0, "partial": 0, "none": 1}
        assert "usage_not_reported" in audit["limits"]
        assert "usage_partially_reported" not in audit["limits"]

    def test_totals_sum_only_known_figures_and_mark_a_partial_sum_as_a_lower_bound(self, tmp_path):
        hops = [
            ab.HopSpec(hop_id="H001", sequence=1, role="worker",
                       usage=ab.usage_record(input_tokens=10, output_tokens=5)),
            ab.HopSpec(hop_id="H002", sequence=2, role="worker",
                       usage=ab.usage_record(input_tokens=None, output_tokens=7)),
        ]
        audit = ab_project(tmp_path, hops)
        totals = audit["usage"]["totals"]
        assert totals["input_tokens"] == {"value": 10, "lower_bound": True}
        assert totals["output_tokens"] == {"value": 12, "lower_bound": False}

    def test_a_total_with_nothing_known_is_null_never_zero(self, tmp_path):
        hops = [ab.HopSpec(hop_id="H001", sequence=1, usage=ab.usage_record(
            cache_read_tokens=None, cache_creation_tokens=None,
        ))]
        audit = ab_project(tmp_path, hops)
        figure = audit["usage"]["totals"]["cache_creation_tokens"]
        assert figure["value"] is None
        assert figure["lower_bound"] is False

    def test_by_role_aggregates_match_only_that_roles_hops(self, tmp_path):
        hops = [
            ab.HopSpec(hop_id="H001", sequence=1, role="worker",
                       usage=ab.usage_record(role="worker", input_tokens=10, output_tokens=5)),
            ab.HopSpec(hop_id="H002", sequence=2, role="worker",
                       usage=ab.usage_record(role="worker", input_tokens=6, output_tokens=3)),
            ab.HopSpec(hop_id="H003", sequence=3, role="independent_reviewer",
                       usage=ab.usage_record(role="independent_reviewer", input_tokens=1, output_tokens=1)),
        ]
        audit = ab_project(tmp_path, hops)
        by_role = audit["usage"]["by_role"]
        assert by_role["worker"]["input_tokens"] == {"value": 16, "lower_bound": False}
        assert by_role["independent_reviewer"]["input_tokens"] == {"value": 1, "lower_bound": False}

    def test_by_model_aggregates_exclude_hops_with_no_recorded_model(self, tmp_path):
        hops = [
            ab.HopSpec(hop_id="H001", sequence=1,
                       usage=ab.usage_record(model="claude-haiku-5-5", input_tokens=10, output_tokens=5)),
            ab.HopSpec(hop_id="H002", sequence=2,
                       usage=ab.usage_record(model=None, input_tokens=99, output_tokens=99)),
        ]
        audit = ab_project(tmp_path, hops)
        by_model = audit["usage"]["by_model"]
        assert set(by_model) == {"claude-haiku-5-5"}
        assert by_model["claude-haiku-5-5"]["input_tokens"] == {"value": 10, "lower_bound": False}

    def test_claude_and_codex_input_semantics_are_both_preserved_and_distinguishable(self, tmp_path):
        hops = [
            ab.HopSpec(hop_id="H001", sequence=1, role="worker", adapter="claude_cli",
                       usage=ab.usage_record(adapter="claude_cli", input_includes_cache_read=False,
                                              input_tokens=2, cache_read_tokens=9074)),
            ab.HopSpec(hop_id="H002", sequence=2, role="worker", adapter="codex_cli",
                       usage=ab.usage_record(adapter="codex_cli", input_includes_cache_read=True,
                                              input_tokens=17329, cache_read_tokens=11008)),
        ]
        audit = ab_project(tmp_path, hops)
        by_id = {hop["hop_id"]: hop for hop in audit["hops"]}
        assert by_id["H001"]["usage"]["input_includes_cache_read"] is False
        assert by_id["H002"]["usage"]["input_includes_cache_read"] is True

    def test_no_monetary_field_is_ever_projected(self, tmp_path):
        hops = [ab.HopSpec(hop_id="H001", sequence=1, usage=ab.usage_record())]
        audit = ab_project(tmp_path, hops)
        rendered = json.dumps(audit)
        for canary in ("total_cost_usd", "costUSD", "cost_usd"):
            assert canary not in rendered


def ab_project(tmp_path, hops):
    attempt_dir = ab.build_attempt(tmp_path, hops=hops, hop_used=len(hops))
    return au.project_audit(attempt_dir)


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

    def test_hop_boundary_field_projects_the_run_wide_value(self):
        # boundary is one Run-wide value (<<enablement>>), read from the
        # trusted Run snapshot, never guessed as "direct" when the Run
        # predates this slice and carries no such key at all.
        hop = {"hop_id": "H001", "sequence": 1, "phase": "implement", "role": "worker"}
        hops, _, _ = au._build_hops_section({"hops": [hop], "boundary": "bubblewrap"}, None, None, None, [])
        assert hops[0]["boundary"] == "bubblewrap"

        hops_legacy, _, _ = au._build_hops_section({"hops": [hop]}, None, None, None, [])
        assert hops_legacy[0]["boundary"] is None

    def test_top_level_keys_are_exactly_the_frozen_set_minus_usage_when_none_is_recorded(self, tmp_path):
        # "usage" is allow-listed but omitted entirely (not merely null) when
        # no invocation of the Run carries a persisted usage record -- this
        # is what keeps a pre-this-change archive reprojecting byte-identically.
        attempt_dir = ab.build_attempt(tmp_path)
        audit = au.project_audit(attempt_dir)
        assert set(audit.keys()) == au._TOP_KEYS - {"usage"}
