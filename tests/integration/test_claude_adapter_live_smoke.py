"""Optional live-provider smoke test for the Claude CLI adapter's TMPDIR fix.

The unit conformance tests in `test_claude_adapter_conformance.py` use a fake
executable and therefore only prove environment plumbing and cleanup; they
never exercise Claude Code's own Bash tool or its sandbox bridge. This test
does -- it calls the real `claude` binary from this repository's own working
directory, with a simulated long ambient ``TMPDIR`` matching what WFC's
`launch.sh` actually exports for a real Attempt, and asserts that the Bash
tool's sandbox actually initializes and a trivial command actually runs.

It costs a small real API charge and requires network access plus an
authenticated `claude` installation, so -- like
`test_optional_real_canonical_wrapper_archives_a_real_completed_attempt` in
`test_wfc_op.py` -- it is skipped unless explicitly opted into; ordinary
`pytest` success in this repository never depends on it.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from workflow_coordinator.adapter import (
    ROUTING_RESULT_ENVELOPE_CONTRACT, AgentExecutionRequest, Invocation,
)
from workflow_coordinator.claude_adapter import ClaudeCliAdapter

REPO_ROOT = Path(__file__).resolve().parents[2]
_CLAUDE_EXECUTABLE = shutil.which("claude")
_OPT_IN = os.environ.get("WFC_RUN_LIVE_CLAUDE_SMOKE") == "1"


@pytest.mark.skipif(
    not (_CLAUDE_EXECUTABLE and _OPT_IN),
    reason=(
        "optional live-provider integration: set WFC_RUN_LIVE_CLAUDE_SMOKE=1 "
        "and ensure an authenticated `claude` executable is on PATH to run"
    ),
)
def test_real_claude_cli_bash_sandbox_initializes_with_a_long_ambient_tmpdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    # Reproduce the real failure's trigger condition: a long, human-readable,
    # Attempt-scoped ambient TMPDIR, as WFC's own launch.sh exports it, on
    # top of this repository's own (non-trivial) absolute path as the
    # worker's cwd. RUN-921eae416d264bbaba51c603b33b33c0 stopped
    # awaiting_human under exactly this combination before the fix.
    long_ambient = tmp_path / (
        "wfc-bubblewrap-fs-isolation-001-review-001-correction-002-20261106-01"
    )
    long_ambient.mkdir()
    monkeypatch.setenv("TMPDIR", str(long_ambient))
    monkeypatch.setenv("TMP", str(long_ambient))
    monkeypatch.setenv("TEMP", str(long_ambient))

    adapter = ClaudeCliAdapter(
        result_directory=tmp_path, model="claude-sonnet-5", reasoning_effort="low",
        executable=_CLAUDE_EXECUTABLE,
    )
    invocation = Invocation("RUN-LIVESMOKE0000000000000000000", "H001", "INV-LIVESMOKE00000000000000000001")
    request = AgentExecutionRequest(
        invocation=invocation,
        prompt=(
            "Use the Bash tool to run exactly this command: echo wfc-smoke-test-ok\n"
            "Then, as your final answer, output only a JSON object exactly matching "
            "the provided schema. Set schema_version, run_id, hop_id, invocation_id to "
            "their required enum values. In result: set outcome to \"completed\" only "
            "if the Bash command actually ran and printed wfc-smoke-test-ok, or "
            "\"blocked\" if the Bash tool's sandbox failed to initialize or the command "
            "could not run for any reason. Set requested_route to \"smoke_test\", "
            "scope_changed to false, requires_human to false, escalation to null, "
            "findings to [], claimed_commits to [], commit_intent to null, "
            "evidence_refs to [], and summary to a short sentence stating exactly what "
            "happened, including the verbatim text of any sandbox initialization error."
        ),
        repository=REPO_ROOT,
        result_contract=ROUTING_RESULT_ENVELOPE_CONTRACT,
        timeout_seconds=120,
        permitted_routes=("smoke_test",),
    )

    tmp_dir = adapter._short_subprocess_tmp_dir(request)
    assert len(str(tmp_dir)) <= 40, "the adapter's own short TMPDIR grew unexpectedly long"

    outcome = adapter.execute(request)

    assert outcome.returncode == 0 and not outcome.timed_out, (outcome.stdout, outcome.stderr)
    assert outcome.candidate_result is not None, "no structured candidate: " + outcome.stdout
    envelope = json.loads(outcome.candidate_result)
    result = envelope["result"]
    assert result["outcome"] == "completed", result["summary"]
    assert "sandbox" not in result["summary"].lower() or "no sandbox" in result["summary"].lower()
    assert not tmp_dir.exists(), "subprocess TMPDIR was not cleaned up"
