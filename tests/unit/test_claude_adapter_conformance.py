"""Offline conformance tests for the closed Claude Code CLI adapter."""

from __future__ import annotations

import json
import stat
import textwrap
from pathlib import Path

import pytest

from workflow_coordinator.adapter import (
    ROUTING_RESULT_ENVELOPE_CONTRACT, AgentExecutionRequest, Invocation,
    ResultValidationError, validated_result,
)
from workflow_coordinator.claude_adapter import ClaudeCliAdapter, ClaudeCliError


ROUTES = ("broad_review",)


def request(repository: Path, invocation: Invocation | None = None, timeout: float = 2) -> AgentExecutionRequest:
    return AgentExecutionRequest(invocation or Invocation("RUN-C", "H001", "INV-C"), "make the bounded change", repository, ROUTING_RESULT_ENVELOPE_CONTRACT, timeout, permitted_routes=ROUTES)


def envelope(invocation: Invocation, *, run_id: str | None = None) -> dict:
    return {"schema_version": 1, "run_id": run_id or invocation.run_id, "hop_id": invocation.hop_id, "invocation_id": invocation.invocation_id, "result": {"schema_version": 1, "outcome": "completed", "requested_route": "broad_review", "scope_changed": False, "requires_human": False, "escalation": None, "findings": [], "claimed_commits": [], "commit_intent": None, "evidence_refs": [], "summary": "done"}}


def fake(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake-claude"
    executable.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def adapter(tmp_path: Path, executable: Path) -> ClaudeCliAdapter:
    return ClaudeCliAdapter(tmp_path, model="claude-model", reasoning_effort="high", executable=str(executable))


def test_exact_closed_argv_profile_settings_cwd_prompt_and_scrubbed_environment(tmp_path: Path):
    observed = tmp_path / "observed.json"
    executable = fake(tmp_path, f'''\
        import json, os, pathlib, sys
        arguments = sys.argv[1:]
        schema = json.loads(arguments[arguments.index("--json-schema") + 1])
        settings = pathlib.Path(arguments[arguments.index("--settings") + 1])
        pathlib.Path({str(observed)!r}).write_text(json.dumps({{
            "argv": arguments, "cwd": os.getcwd(), "scrub": os.environ.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"),
            "schema": schema, "settings": json.loads(settings.read_text()),
        }}))
        print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                          "structured_output": {envelope(Invocation("RUN-C", "H001", "INV-C"))!r}}}))
    ''')
    outcome = adapter(tmp_path, executable).execute(request(tmp_path))
    observation = json.loads(observed.read_text())
    argv = observation["argv"]
    assert argv[:4] == ["-p", "--output-format", "json", "--json-schema"]
    schema = observation["schema"]
    assert argv[4] == json.dumps(schema, separators=(",", ":"), sort_keys=True)
    assert argv[5:] == ["--model", "claude-model", "--effort", "high", "--restricted", "--safe-mode", "--strict-mcp-config", "--tools", "Read,Glob,Grep,Edit,Write,Bash", "--settings", str(tmp_path / "claude-closed-settings.json"), "--permission-mode", "acceptEdits", "--permission-prompts", "none", "--no-session-persistence", "make the bounded change"]
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert schema["required"] == ["schema_version", "run_id", "hop_id", "invocation_id", "result"]
    assert set(schema["properties"]) == set(schema["required"])
    assert schema["properties"]["schema_version"]["enum"] == [1]
    assert schema["properties"]["run_id"]["enum"] == ["RUN-C"]
    assert schema["properties"]["hop_id"]["enum"] == ["H001"]
    assert schema["properties"]["invocation_id"]["enum"] == ["INV-C"]
    result_schema = schema["properties"]["result"]
    assert result_schema["properties"]["schema_version"]["enum"] == [1]
    assert result_schema["required"] == [
        "schema_version", "outcome", "requested_route", "scope_changed", "requires_human",
        "escalation", "findings", "claimed_commits", "commit_intent", "evidence_refs", "summary",
    ]
    assert result_schema["properties"]["outcome"]["enum"] == [
        "completed", "changes_required", "blocked", "failed",
    ]
    assert result_schema["properties"]["requested_route"]["enum"] == ["broad_review"]
    forbidden = ("codex", "--mcp-config", "--allowed-tools", "--fallback-model", "--resume", "--continue", "--add-dir", "--dangerously-skip-permissions")
    assert not any(item in argv for item in forbidden)
    assert observation["cwd"] == str(tmp_path) and observation["scrub"] == "1"
    assert observation["settings"] == {"sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False, "filesystem": {"denyRead": ["~/"], "allowRead": [str(tmp_path)]}, "network": {"allowedDomains": []}}, "permissions": {"allow": ["Edit", "Write"], "deny": ["WebFetch", "WebSearch"], "blockReadsOutsideWorkingDirectories": True}}
    assert outcome.returncode == 0 and not outcome.timed_out
    assert json.loads(outcome.candidate_result or "{}") == envelope(Invocation("RUN-C", "H001", "INV-C"))


def test_edit_grant_uses_only_resolved_repository_as_working_directory(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(repository, target_is_directory=True)
    observed = tmp_path / "grant.json"
    executable = fake(tmp_path, f'''\
        import json, os, pathlib, sys
        args = sys.argv[1:]
        settings = json.loads(pathlib.Path(args[args.index("--settings") + 1]).read_text())
        pathlib.Path({str(observed)!r}).write_text(json.dumps({{
            "cwd": os.getcwd(), "args": args, "settings": settings,
            "scrub": os.environ.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"),
        }}))
    ''')
    adapter(tmp_path, executable).execute(request(alias))
    result = json.loads(observed.read_text())
    assert result["cwd"] == str(repository.resolve())
    assert result["scrub"] == "1"
    assert "--restricted" in result["args"]
    assert "--add-dir" not in result["args"]
    assert result["settings"]["permissions"]["allow"] == ["Edit", "Write"]
    assert result["settings"]["sandbox"]["filesystem"] == {
        "denyRead": ["~/"], "allowRead": [str(repository.resolve())],
    }


def test_successful_wrapper_candidate_is_still_validated_by_wfc(tmp_path: Path):
    invocation = Invocation("RUN-C", "H001", "INV-C")
    executable = fake(tmp_path, f'''\
        import json
        print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "structured_output": {envelope(invocation)!r}}}))
    ''')
    outcome = adapter(tmp_path, executable).execute(request(tmp_path, invocation))
    assert validated_result(outcome.candidate_result or "", invocation).summary == "done"


@pytest.mark.parametrize("wrapper", ["not-json", json.dumps({"type": "result", "subtype": "error", "is_error": True}), json.dumps({"type": "result", "subtype": "success", "is_error": False}), json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": "not-an-object"})])
def test_zero_exit_malformed_or_unsuccessful_wrapper_never_yields_a_candidate(tmp_path: Path, wrapper: str):
    outcome = adapter(tmp_path, fake(tmp_path, f"print({wrapper!r})\n")).execute(request(tmp_path))
    assert outcome.returncode == 0 and not outcome.timed_out and outcome.candidate_result is None


def test_malformed_extracted_envelope_is_not_repaired_or_accepted(tmp_path: Path):
    wrapper = json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": {"bad": True}})
    outcome = adapter(tmp_path, fake(tmp_path, f"print({wrapper!r})\n")).execute(request(tmp_path))
    assert outcome.candidate_result == '{"bad": true}'
    with pytest.raises(ResultValidationError):
        validated_result(outcome.candidate_result, Invocation("RUN-C", "H001", "INV-C"))


def test_mismatched_correlation_is_never_repaired(tmp_path: Path):
    invocation = Invocation("RUN-C", "H001", "INV-C")
    executable = fake(tmp_path, f'''\
        import json
        print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "structured_output": {envelope(invocation, run_id="RUN-OTHER")!r}}}))
    ''')
    outcome = adapter(tmp_path, executable).execute(request(tmp_path, invocation))
    assert json.loads(outcome.candidate_result or "{}")["run_id"] == "RUN-OTHER"
    with pytest.raises(ResultValidationError, match="does not correlate"):
        validated_result(outcome.candidate_result or "", invocation)


def test_nonzero_valid_looking_output_and_timeout_partial_output_are_transport_only(tmp_path: Path):
    good = json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": envelope(Invocation("RUN-C", "H001", "INV-C"))})
    outcome = adapter(tmp_path, fake(tmp_path, f"import sys\nprint({good!r})\nsys.exit(9)\n")).execute(request(tmp_path))
    assert outcome.returncode == 9 and outcome.candidate_result is None
    slow = fake(tmp_path, "import sys, time\nsys.stdout.write('{\\\"partial\\\":')\nsys.stdout.flush()\ntime.sleep(10)\n")
    outcome = adapter(tmp_path, slow).execute(request(tmp_path, timeout=0.05))
    assert outcome.timed_out and outcome.candidate_result is None and "partial" in outcome.stdout


def test_local_startup_failure_and_unroutable_request_never_launch(tmp_path: Path):
    with pytest.raises(ClaudeCliError, match="pre-acceptance executable launch failure"):
        adapter(tmp_path, tmp_path / "missing-claude").execute(request(tmp_path))
    executable = tmp_path / "must-not-run"
    bad = AgentExecutionRequest(Invocation("RUN-C", "H001", "INV-C"), "p", tmp_path, ROUTING_RESULT_ENVELOPE_CONTRACT, 1, permitted_routes=())
    with pytest.raises(ClaudeCliError, match="no permitted routes"):
        adapter(tmp_path, executable).execute(bad)
    assert not executable.exists()


def test_zero_exit_sandbox_refusal_does_not_retry_or_invent_transport_failure(tmp_path: Path):
    observed = tmp_path / "calls"
    executable = fake(tmp_path, f'''\
        import pathlib
        pathlib.Path({str(observed)!r}).write_text("one")
        print('{{"type":"result","subtype":"success","is_error":false}}')
    ''')
    outcome = adapter(tmp_path, executable).execute(request(tmp_path))
    assert observed.read_text() == "one"
    assert outcome.returncode == 0 and not outcome.timed_out and outcome.candidate_result is None
