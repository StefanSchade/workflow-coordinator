"""One-shot Level-3 preparation runner for the Codex adapter boundary.

This is intentionally below the coordinator: it creates an ordinary non-Git
workspace, invokes the Codex adapter exactly once, and validates the returned
routing envelope.  It does not exercise Run/Hop lifecycle, reduction, Git, or
the Slice-4 proof harness.  A later real invocation remains separately
authorized.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from uuid import uuid4

if __package__ in {None, ""}:  # Allow the documented file-based launcher.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.workflow_coordinator.adapter import (
    ROUTING_RESULT_ENVELOPE_CONTRACT,
    AgentExecutionRequest,
    Invocation,
    ResultValidationError,
    validated_result,
)
from scripts.workflow_coordinator.codex_adapter import CodexCliAdapter, CodexCliError
from scripts.workflow_coordinator.persistence import atomic_write_json


DEFAULT_TIMEOUT_SECONDS = 120.0


def _prompt(invocation: Invocation) -> str:
    return f"""Return only one structured envelope matching the supplied output schema.
Use these exact correlation IDs: run_id={invocation.run_id}, hop_id={invocation.hop_id}, invocation_id={invocation.invocation_id}.
Return a minimal valid RoutingResult: schema_version 1, outcome completed, requested_route done, false scope_changed/requires_human, null escalation, empty findings/commits/evidence, and a concise summary. Do no repository, shell, tool, or Git work."""


def _summary_base(
    invocation: Invocation,
    *,
    executable: str,
    model: str,
    reasoning_effort: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "smoke_status": "local_pre_execution_error",
        "run_id": invocation.run_id,
        "hop_id": invocation.hop_id,
        "invocation_id": invocation.invocation_id,
        "executable": executable,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "result_contract": ROUTING_RESULT_ENVELOPE_CONTRACT,
        "invocation_attempted": False,
        "returncode": None,
        "timed_out": False,
        "diagnostics": [],
        "stdout_tail": "",
        "stderr_tail": "",
        "candidate_result_present": False,
        "semantic_validation_succeeded": False,
        "real_provider_compatibility_established": False,
    }


def run_smoke(
    smoke_root: Path,
    *,
    executable: str = "codex",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    model: str = "gpt-5.6-terra",
    reasoning_effort: str = "medium",
) -> int:
    """Run exactly one adapter invocation, returning a shell-compatible status."""
    try:
        smoke_root.mkdir()
    except FileExistsError:
        # Do not write into an existing root: it is the one-shot execution guard.
        return 2

    invocation = Invocation(
        run_id=f"SMOKE-{uuid4().hex}", hop_id="H001", invocation_id=f"INV-{uuid4().hex}"
    )
    summary = _summary_base(
        invocation, executable=executable, model=model, reasoning_effort=reasoning_effort
    )
    summary_path = smoke_root / "smoke-summary.json"
    workspace = smoke_root / "workspace"
    result_directory = smoke_root / "result"

    try:
        workspace.mkdir()
        result_directory.mkdir()
        adapter = CodexCliAdapter(
            result_directory=result_directory,
            executable=executable,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        outcome = adapter.execute(
            AgentExecutionRequest(
                invocation=invocation,
                prompt=_prompt(invocation),
                repository=workspace,
                result_contract=ROUTING_RESULT_ENVELOPE_CONTRACT,
                timeout_seconds=timeout_seconds,
            )
        )
    except (CodexCliError, OSError, ValueError) as error:
        summary["diagnostics"] = [str(error)]
        atomic_write_json(summary_path, summary)
        return 1

    summary.update(
        invocation_attempted=True,
        returncode=outcome.returncode,
        timed_out=outcome.timed_out,
        diagnostics=list(outcome.diagnostics),
        stdout_tail=outcome.stdout,
        stderr_tail=outcome.stderr,
        candidate_result_present=outcome.candidate_result is not None,
    )
    schema_path = result_directory / "structured-result.schema.json"
    if schema_path.exists():
        summary["result_schema_sha256"] = hashlib.sha256(schema_path.read_bytes()).hexdigest()

    validated = None
    if outcome.candidate_result is not None:
        try:
            validated = validated_result(outcome.candidate_result, invocation)
        except ResultValidationError as error:
            summary["diagnostics"] = [*outcome.diagnostics, f"semantic validation failed: {error}"]
        else:
            summary["semantic_validation_succeeded"] = True
            summary["semantic_result_summary"] = {
                "outcome": validated.outcome.value,
                "requested_route": validated.requested_route,
                "summary": validated.summary,
            }

    transport_succeeded = outcome.returncode == 0 and not outcome.timed_out
    if transport_succeeded and validated is not None:
        summary["smoke_status"] = "succeeded"
        summary["real_provider_compatibility_established"] = True
        exit_code = 0
    elif not transport_succeeded:
        summary["smoke_status"] = "transport_failure"
        exit_code = 1
    else:
        summary["smoke_status"] = "semantic_failure"
        exit_code = 1
    atomic_write_json(summary_path, summary)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the separately authorized one-shot Codex adapter smoke.")
    parser.add_argument("--smoke-root", type=Path, required=True, help="new durable output directory")
    parser.add_argument("--executable", default="codex")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning-effort", default="medium")
    args = parser.parse_args(argv)
    return run_smoke(
        args.smoke_root,
        executable=args.executable,
        timeout_seconds=args.timeout_seconds,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
    )


if __name__ == "__main__":
    raise SystemExit(main())
