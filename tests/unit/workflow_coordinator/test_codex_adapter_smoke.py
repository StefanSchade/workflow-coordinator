"""Deterministic fake-executable tests for the one-shot Codex smoke runner."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.workflow_coordinator import codex_adapter_smoke
from scripts.workflow_coordinator.codex_adapter_smoke import run_smoke


def _fake(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake-codex"
    executable.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    executable.chmod(0o755)
    return executable


def _result_writer(*, result: str) -> str:
    return f'''import json
import re
import sys
from pathlib import Path
prompt = sys.stdin.read()
ids = dict(re.findall(r"(run_id|hop_id|invocation_id)=([^,\\n.]+)", prompt))
output = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
output.write_text({result!r}, encoding="utf-8")
'''


def _valid_result(run_id: str, hop_id: str, invocation_id: str) -> str:
    return json.dumps({
        "schema_version": 1,
        "run_id": run_id,
        "hop_id": hop_id,
        "invocation_id": invocation_id,
        "result": {
            "schema_version": 1, "outcome": "completed", "requested_route": "done",
            "scope_changed": False, "requires_human": False, "escalation": None,
            "findings": [], "claimed_commits": [], "evidence_refs": [], "summary": "smoke complete",
        },
    })


def _dynamic_valid_writer() -> str:
    return _result_writer(result="PLACEHOLDER").replace(
        "output.write_text('PLACEHOLDER', encoding=\"utf-8\")",
        '''output.write_text(json.dumps({"schema_version": 1, **ids, "result": {
"schema_version": 1, "outcome": "completed", "requested_route": "done", "scope_changed": False,
"requires_human": False, "escalation": None, "findings": [], "claimed_commits": [],
"evidence_refs": [], "summary": "smoke complete"}}), encoding="utf-8")''',
    )


def _summary(root: Path) -> dict[str, object]:
    return json.loads((root / "smoke-summary.json").read_text(encoding="utf-8"))


def test_fake_success_uses_one_execution_and_ordinary_non_git_workspace(tmp_path: Path):
    marker = tmp_path / "runs"
    fake = _fake(tmp_path, _dynamic_valid_writer() + f"\nPath({str(marker)!r}).write_text('one')\n")
    root = tmp_path / "smoke"

    assert run_smoke(root, executable=str(fake), timeout_seconds=1) == 0

    summary = _summary(root)
    assert marker.read_text() == "one"
    assert summary["smoke_status"] == "succeeded"
    assert summary["invocation_attempted"] is True
    assert summary["semantic_validation_succeeded"] is True
    assert summary["configured_executable_smoke_succeeded"] is True
    assert "real_provider_compatibility_established" not in summary
    assert summary["timeout_seconds"] == 1
    assert (root / "workspace" / ".git").exists() is False


def test_existing_root_fails_before_fake_executable_runs(tmp_path: Path):
    root = tmp_path / "smoke"
    root.mkdir()
    marker = tmp_path / "ran"
    fake = _fake(tmp_path, f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')")

    assert run_smoke(root, executable=str(fake)) == 2

    assert marker.exists() is False
    assert (root / "smoke-summary.json").exists() is False


def test_nonzero_fake_transport_failure_is_truthful_and_not_retried(tmp_path: Path):
    marker = tmp_path / "runs"
    fake = _fake(tmp_path, f"from pathlib import Path\nPath({str(marker)!r}).write_text('one')\nraise SystemExit(7)")
    root = tmp_path / "smoke"

    assert run_smoke(root, executable=str(fake), timeout_seconds=1) == 1

    summary = _summary(root)
    assert marker.read_text() == "one"
    assert summary["smoke_status"] == "transport_failure"
    assert summary["returncode"] == 7
    assert summary["candidate_result_present"] is False
    assert summary["semantic_validation_succeeded"] is False


def test_malformed_candidate_is_semantic_failure(tmp_path: Path):
    fake = _fake(tmp_path, _result_writer(result="not json"))
    root = tmp_path / "smoke"

    assert run_smoke(root, executable=str(fake), timeout_seconds=1) == 1

    summary = _summary(root)
    assert summary["smoke_status"] == "semantic_failure"
    assert summary["candidate_result_present"] is True
    assert summary["semantic_validation_succeeded"] is False


def test_uncorrelated_candidate_is_semantic_failure(tmp_path: Path):
    fake = _fake(tmp_path, _result_writer(result=_valid_result("other", "H001", "INV")))
    root = tmp_path / "smoke"

    assert run_smoke(root, executable=str(fake), timeout_seconds=1) == 1

    summary = _summary(root)
    assert summary["smoke_status"] == "semantic_failure"
    assert summary["semantic_validation_succeeded"] is False
    assert "does not correlate" in " ".join(summary["diagnostics"])


def test_timeout_is_bounded_transport_failure_without_retry(tmp_path: Path):
    marker = tmp_path / "runs"
    fake = _fake(tmp_path, f"import time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('one')\ntime.sleep(3)")
    root = tmp_path / "smoke"

    assert run_smoke(root, executable=str(fake), timeout_seconds=0.05) == 1

    summary = _summary(root)
    assert marker.read_text() == "one"
    assert summary["smoke_status"] == "transport_failure"
    assert summary["timed_out"] is True
    assert summary["semantic_validation_succeeded"] is False


def test_post_launch_decode_failure_is_not_reported_as_not_attempted(tmp_path: Path):
    marker = tmp_path / "runs"
    fake = _fake(
        tmp_path,
        "import sys\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('one')\n"
        "output = Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
        "output.write_bytes(b'\\xff')",
    )
    root = tmp_path / "smoke"

    assert run_smoke(root, executable=str(fake), timeout_seconds=1) == 1

    summary = _summary(root)
    assert marker.read_text() == "one"
    assert summary["smoke_status"] == "post_execution_error"
    assert summary["invocation_attempted"] is True
    assert summary["configured_executable_smoke_succeeded"] is False


def test_missing_executable_is_proven_pre_launch_failure(tmp_path: Path):
    root = tmp_path / "smoke"

    assert run_smoke(root, executable=str(tmp_path / "does-not-exist")) == 1

    summary = _summary(root)
    assert summary["smoke_status"] == "local_pre_execution_error"
    assert summary["invocation_attempted"] is False
    assert summary["configured_executable_smoke_succeeded"] is False


def test_unexpected_execution_exception_writes_indeterminate_summary(tmp_path: Path, monkeypatch):
    marker = tmp_path / "runs"

    class ExecutingThenFailingAdapter:
        def __init__(self, **_: object):
            pass

        def execute(self, _: object):
            marker.write_text("one")
            raise RuntimeError("unexpected execution-side failure")

    monkeypatch.setattr(codex_adapter_smoke, "CodexCliAdapter", ExecutingThenFailingAdapter)
    root = tmp_path / "smoke"

    assert run_smoke(root, executable="fake") == 1

    summary = _summary(root)
    assert marker.read_text() == "one"
    assert summary["smoke_status"] == "post_execution_error"
    assert summary["invocation_attempted"] is True
    assert summary["configured_executable_smoke_succeeded"] is False
