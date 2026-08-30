"""Deterministic fake-executable coverage for the Slice-4A proof harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.workflow_coordinator.proof_harness import (
    DIAGNOSTIC_LIMIT_CHARS,
    EXPECTED_SUBJECT,
    collect_launch_provenance,
    proof_exit_status,
    run_disposable_proof,
)


pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


def _fake(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake-codex"
    executable.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    executable.chmod(0o755)
    return executable


def _summary(result_path: Path) -> dict[str, object]:
    return json.loads(result_path.read_text(encoding="utf-8"))


def test_successful_fake_proof_writes_self_contained_durable_evidence(tmp_path: Path):
    executable = _fake(
        tmp_path,
        '''import json, pathlib, subprocess, sys
args=sys.argv[1:]
repo=args[args.index("--cd") + 1]
output=pathlib.Path(args[args.index("--output-last-message") + 1])
pathlib.Path(repo, "PROOF.md").write_text("WFC-S4-PROOF: completed marker\\n")
subprocess.run(("git", "-C", repo, "add", "PROOF.md"), check=True)
subprocess.run(("git", "-C", repo, "commit", "-m", "docs(workflow): WFC-S4-PROOF H001 mark fixture"), check=True, stdout=subprocess.DEVNULL)
commit=subprocess.run(("git", "-C", repo, "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
sys.stdout.write("fake stdout")
sys.stderr.write("fake stderr")
prompt=sys.stdin.read()
run_id=prompt.split("run_id=", 1)[1].split("\\n", 1)[0]
invocation_id=prompt.split("result invocation_id is `", 1)[1].split("`", 1)[0]
output.write_text(json.dumps({"schema_version": 1, "run_id": run_id, "hop_id": "H001", "invocation_id": invocation_id, "result": {"schema_version": 1, "outcome": "completed", "requested_route": "complete", "scope_changed": False, "requires_human": False, "escalation": None, "findings": [], "claimed_commits": [commit], "evidence_refs": [], "summary": "done"}}))
''',
    )
    result = run_disposable_proof(tmp_path / "proof", executable=str(executable), timeout_seconds=2)
    summary = _summary(result.summary_path)
    assert result.error is None
    assert summary["vertical_success"] is True
    assert summary["final_run_status"] == "completed"
    assert summary["hop_used"] == 1
    assert summary["dispatch_marker_present"] is False
    assert summary["actual_commits"] and summary["tracked_paths"] == ["PROOF.md"]
    assert summary["proof_md_final_content"] == "WFC-S4-PROOF: completed marker\n"
    assert summary["expected_commit_subject"] == EXPECTED_SUBJECT
    assert summary["actual_commit_subjects"] == [EXPECTED_SUBJECT]
    assert summary["stdout_tail"] == "fake stdout" and summary["stderr_tail"] == "fake stderr"
    assert Path(str(summary["launch_provenance_path"])).exists()
    assert proof_exit_status(result) == 0


def test_post_spawn_fake_failure_is_charged_and_diagnosable_with_bounded_tails(tmp_path: Path):
    executable = _fake(
        tmp_path,
        "import sys\nsys.stdout.write('\u00e4' * 20000)\nsys.stderr.write('\u00df' * 20000)\nsys.exit(7)\n",
    )
    result = run_disposable_proof(tmp_path / "proof", executable=str(executable), timeout_seconds=2)
    summary = _summary(result.summary_path)
    assert result.error is None
    assert summary["process_returncode"] == 7
    assert summary["acceptance"] == "uncertain"
    assert summary["vertical_success"] is False
    assert summary["final_run_status"] == "reconcile_required"
    assert summary["hop_used"] == 1
    assert summary["dispatch_marker_present"] is True
    assert summary["diagnostic_limit_chars"] == DIAGNOSTIC_LIMIT_CHARS
    assert len(str(summary["stdout_tail"])) == DIAGNOSTIC_LIMIT_CHARS
    assert len(str(summary["stderr_tail"])) == DIAGNOSTIC_LIMIT_CHARS
    assert len(str(summary["stdout_tail"]).encode()) > DIAGNOSTIC_LIMIT_CHARS
    assert len(str(summary["stderr_tail"]).encode()) > DIAGNOSTIC_LIMIT_CHARS
    assert summary["proof_md_final_content"] == "WFC-S4-PROOF: pending marker\n"
    assert proof_exit_status(result) != 0


def test_pre_spawn_failure_writes_summary_without_charging_a_hop(tmp_path: Path):
    result = run_disposable_proof(tmp_path / "proof", executable=str(tmp_path / "missing-codex"), timeout_seconds=2)
    summary = _summary(result.summary_path)
    assert result.error is not None
    assert summary["acceptance"] == "not_started"
    assert summary["hop_used"] == 0
    assert summary["dispatch_marker_present"] is False
    assert "pre-acceptance executable launch failure" in str(summary["operator_error"])
    assert proof_exit_status(result) != 0


def test_provenance_is_bounded_and_never_records_environment_values():
    proc = {
        "/proc/30/comm": "operator-shell\n",
        "/proc/30/stat": "30 (operator-shell) S 20 0 0 0\n",
        "/proc/20/comm": "sshd\n",
        "/proc/20/stat": "20 (sshd) S 1 0 0 0\n",
    }
    provenance = collect_launch_provenance(
        pid=30,
        ppid=20,
        read_text=lambda path: proc[str(path)],
        environ={"SECRET_TOKEN": "do-not-record", "CODEX_HOME": "/private"},
    )
    rendered = json.dumps(provenance)
    assert [item["pid"] for item in provenance["ancestors"]] == [30, 20]
    assert provenance["environment_presence"]["codex_related"] is True
    assert provenance["environment_values_recorded"] is False
    assert "SECRET_TOKEN" not in rendered and "do-not-record" not in rendered
