"""BP6a: privacy as a mechanism, not a convention.

Every source field of the synthetic fixture carries a known canary (a fake
home directory, user name, scratch/state/ops path, persona and provider
prose, or a Git remote URL) -- including inside nested dicts and list
elements -- and this module asserts none of them, and no absolute path,
ever reaches ``audit.json``. It also proves the final guard actually
refuses production (never silently drops a value) when a disallowed string
reaches it despite the allow-list, and that a refusal writes nothing.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.fixtures.bp6 import archive_builder as ab
from workflow_coordinator.frontdoor import audit as au
from workflow_coordinator.frontdoor.cli import main as cli_main

_ABSOLUTE_PATH_IN_JSON_STRING = re.compile(r'"(/[^"\\]*|[A-Za-z]:[\\/][^"\\]*)"')


def _canary_attempt(tmp_path):
    hops = [
        ab.HopSpec(
            hop_id="H001", sequence=1, role="worker", phase="implement",
            persona=ab.PERSONA_CANARY, summary_prose=ab.PROVIDER_PROSE_CANARY,
            observations=(ab.ObservationSpec(summary_prose=ab.PROVIDER_PROSE_CANARY),),
        ),
        ab.HopSpec(
            hop_id="H002", sequence=2, role="independent_reviewer", phase="broad_review",
            persona=ab.PERSONA_CANARY, summary_prose=ab.PROVIDER_PROSE_CANARY,
            observations=(ab.ObservationSpec(summary_prose=ab.PROVIDER_PROSE_CANARY),),
        ),
    ]
    findings = [ab.FindingSpec(finding_id="F001", invariant_prose=ab.PROVIDER_PROSE_CANARY,
                                evidence_refs=(ab.PROVIDER_PROSE_CANARY,))]
    return ab.build_attempt(
        tmp_path, hops=hops, hop_used=2, findings=findings,
        anomalies=(ab.PROVIDER_PROSE_CANARY,), human_question=ab.PROVIDER_PROSE_CANARY,
        semantic_stop={"kind": "clarify", "question": ab.PROVIDER_PROSE_CANARY, "source_hop_id": "H001"},
        home=ab.DEFAULT_HOME, user=ab.DEFAULT_USER, scratch=ab.DEFAULT_SCRATCH,
        state_path=ab.DEFAULT_STATE, ops_path=ab.DEFAULT_OPS,
        semantic_notes=ab.PROVIDER_PROSE_CANARY,
    )


_PRIVATE_STRINGS = (ab.DEFAULT_HOME, ab.DEFAULT_USER, ab.DEFAULT_SCRATCH, ab.DEFAULT_STATE, ab.DEFAULT_OPS)


class TestNoCanaryOrPrivateStringReachesTheOutput:
    def test_full_canary_run_leaks_nothing(self, tmp_path):
        attempt_dir = _canary_attempt(tmp_path)
        audit = au.project_audit(attempt_dir, private_strings=_PRIVATE_STRINGS)
        rendered = json.dumps(audit)

        for private in _PRIVATE_STRINGS:
            assert private not in rendered, f"{private!r} leaked into audit.json"
        assert ab.PERSONA_CANARY not in rendered
        assert ab.PROVIDER_PROSE_CANARY not in rendered
        assert ab.REMOTE_URL_CANARY not in rendered
        assert not _ABSOLUTE_PATH_IN_JSON_STRING.search(rendered)

    def test_canary_in_a_list_element_is_still_excluded(self, tmp_path):
        findings = [ab.FindingSpec(finding_id="F001", evidence_refs=(ab.DEFAULT_HOME, ab.PROVIDER_PROSE_CANARY))]
        attempt_dir = ab.build_attempt(tmp_path, findings=findings)
        audit = au.project_audit(attempt_dir, private_strings=_PRIVATE_STRINGS)
        rendered = json.dumps(audit)
        assert ab.DEFAULT_HOME not in rendered
        assert ab.PROVIDER_PROSE_CANARY not in rendered

    def test_executable_is_projected_as_basename_only(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path, executable_argument=f"{ab.DEFAULT_SCRATCH}/bin/codex")
        audit = au.project_audit(attempt_dir, private_strings=_PRIVATE_STRINGS)
        assert audit["governance"]["executable"] == {"name": "codex"}

    def test_raw_files_never_carry_the_absolute_source_path(self, tmp_path):
        attempt_dir = _canary_attempt(tmp_path)
        audit = au.project_audit(attempt_dir, private_strings=_PRIVATE_STRINGS)
        for entry in audit["raw"]["files"]:
            assert set(entry.keys()) == {"path", "sha256", "bytes"}
            assert not entry["path"].startswith("/")


class TestPrivacyGuardRefusesRatherThanSilentlyDropping:
    def test_an_injected_absolute_path_is_refused_naming_only_the_json_path(self):
        with pytest.raises(au.ProjectionError) as excinfo:
            au._guard_privacy({"lifecycle": {"preservation": {"reason_code": "/etc/shadow"}}}, "", ())
        message = str(excinfo.value)
        assert "lifecycle.preservation.reason_code" in message
        assert "/etc/shadow" not in message

    def test_an_injected_private_string_is_refused(self):
        with pytest.raises(au.ProjectionError):
            au._guard_privacy({"governance": {"executable": {"name": ab.DEFAULT_USER}}}, "", (ab.DEFAULT_USER,))

    def test_a_git_remote_url_is_refused(self):
        with pytest.raises(au.ProjectionError):
            au._guard_privacy({"raw": {"files": [{"path": ab.REMOTE_URL_CANARY}]}}, "", ())

    def test_project_audit_refuses_when_supplied_lifecycle_facts_leak_a_private_string(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        with pytest.raises(au.ProjectionError):
            au.project_audit(
                attempt_dir,
                lifecycle={"classification": "stopped",
                           "preservation": {"outcome": "refused", "reason_code": ab.DEFAULT_HOME}},
                private_strings=_PRIVATE_STRINGS,
            )

    def test_cli_audit_exits_3_and_writes_nothing_on_refusal(self, tmp_path, monkeypatch, capsys):
        attempt_dir = ab.build_attempt(tmp_path)
        lifecycle_path = tmp_path / "lifecycle.json"
        lifecycle_path.write_text(json.dumps({
            "classification": "stopped",
            "preservation": {"outcome": "refused", "reason_code": "/etc/shadow"},
        }))
        code = cli_main([
            "audit", str(attempt_dir), "--lifecycle-json", str(lifecycle_path),
        ])
        assert code == 3
        assert not (attempt_dir / "audit.json").exists()
        captured = capsys.readouterr()
        assert "/etc/shadow" not in captured.err

    def test_mutating_the_offending_value_away_allows_success(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        with pytest.raises(au.ProjectionError):
            au.project_audit(
                attempt_dir,
                lifecycle={"classification": "stopped",
                           "preservation": {"outcome": "refused", "reason_code": "/etc/shadow"}},
            )
        audit = au.project_audit(
            attempt_dir,
            lifecycle={"classification": "stopped", "preservation": {"outcome": "refused", "reason_code": "lock_contended"}},
        )
        assert audit["lifecycle"]["preservation"]["reason_code"] == "lock_contended"


class TestCliUsageAndStructuralRefusalsExitTwo:
    def test_malformed_lifecycle_json_exits_2_not_3(self, tmp_path):
        attempt_dir = ab.build_attempt(tmp_path)
        lifecycle_path = tmp_path / "lifecycle.json"
        lifecycle_path.write_text("not valid json")
        code = cli_main(["audit", str(attempt_dir), "--lifecycle-json", str(lifecycle_path)])
        assert code == 2
        assert not (attempt_dir / "audit.json").exists()

    def test_a_non_archive_directory_exits_2(self, tmp_path):
        empty = tmp_path / "not-an-attempt"
        empty.mkdir()
        code = cli_main(["audit", str(empty)])
        assert code == 2

    def test_an_unknown_raw_retention_choice_is_a_usage_refusal(self, tmp_path, capsys):
        attempt_dir = ab.build_attempt(tmp_path)
        with pytest.raises(SystemExit) as excinfo:
            cli_main(["audit", str(attempt_dir), "--raw-retention", "nowhere"])
        assert excinfo.value.code == 2
