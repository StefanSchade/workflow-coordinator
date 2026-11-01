"""Claude Code CLI transport adapter with a closed noninteractive profile."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .adapter import (
    ROUTING_RESULT_ENVELOPE_CONTRACT,
    AgentExecutionOutcome,
    AgentExecutionRequest,
    ExecutionNotStartedError,
)
from .model import ResultContractKind, admits_result_contract_version
from .persistence import atomic_write_json
from .structured_result_schema import structured_result_envelope_schema


class ClaudeCliError(ExecutionNotStartedError, RuntimeError):
    """A local pre-launch Claude setup or launch failure."""


_TOOLS = "Read,Glob,Grep,Edit,Write,Bash"


def _bounded(value: str, limit: int = 16_384) -> str:
    return value[-limit:]


@dataclass(frozen=True)
class ClaudeCliAdapter:
    """Concrete Claude Code implementation of the unchanged execution port."""

    result_directory: Path
    model: str
    reasoning_effort: str
    executable: str = "claude"

    def __post_init__(self) -> None:
        for name, value in (("model", self.model), ("reasoning_effort", self.reasoning_effort)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Claude CLI {name} must be explicit and non-empty")
            if value.splitlines() != [value] or "\0" in value:
                raise ValueError(f"Claude CLI {name} must be a single-line value")

    def argv(
        self, request: AgentExecutionRequest, serialized_schema: str, settings: Path
    ) -> tuple[str, ...]:
        return (
            self.executable, "-p", "--output-format", "json", "--json-schema", serialized_schema,
            "--model", self.model, "--effort", self.reasoning_effort,
            "--restricted", "--safe-mode", "--strict-mcp-config", "--tools", _TOOLS,
            "--settings", str(settings), "--permission-mode", "acceptEdits",
            "--permission-prompts", "none", "--no-session-persistence", request.prompt,
        )

    @staticmethod
    def _settings(request: AgentExecutionRequest) -> dict[str, object]:
        return {
            "sandbox": {
                "enabled": True,
                "failIfUnavailable": True,
                "allowUnsandboxedCommands": False,
                "filesystem": {
                    "denyRead": ["~/"],
                    "allowRead": [str(request.repository.resolve())],
                },
                "network": {"allowedDomains": []},
            },
            "permissions": {
                "deny": ["WebFetch", "WebSearch"],
                "blockReadsOutsideWorkingDirectories": True,
            },
        }

    @staticmethod
    def _candidate_from_wrapper(stdout: str) -> str | None:
        """Return only Claude's successful structured-output member, verbatim."""
        try:
            wrapper = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        if not isinstance(wrapper, dict):
            return None
        if wrapper.get("type") != "result" or wrapper.get("subtype") != "success":
            return None
        if wrapper.get("is_error") is not False:
            return None
        structured_output = wrapper.get("structured_output")
        if not isinstance(structured_output, dict):
            return None
        return json.dumps(structured_output)

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionOutcome:
        if request.result_contract != ROUTING_RESULT_ENVELOPE_CONTRACT:
            raise ClaudeCliError(f"unsupported result contract: {request.result_contract}")
        if request.result_contract_kind not in set(ResultContractKind):
            raise ClaudeCliError(f"unsupported result contract kind: {request.result_contract_kind}")
        if request.timeout_seconds <= 0:
            raise ClaudeCliError("timeout must be positive")
        if not admits_result_contract_version(request.result_contract_kind, request.contract_version):
            raise ClaudeCliError("unsupported result contract version")
        if not request.permitted_routes:
            raise ClaudeCliError(
                "current phase has no permitted routes; refusing to dispatch an unroutable invocation"
            )
        try:
            schema = self.result_directory / "structured-result.schema.json"
            settings = self.result_directory / "claude-closed-settings.json"
            schema_object = structured_result_envelope_schema(
                request.invocation, request.contract_version, request.result_contract_kind,
                permitted_routes=request.permitted_routes,
            )
            atomic_write_json(schema, schema_object)
            atomic_write_json(settings, self._settings(request))
            environment = {**os.environ, "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1"}
            child = subprocess.Popen(
                self.argv(
                    request,
                    json.dumps(schema_object, separators=(",", ":"), sort_keys=True),
                    settings,
                ),
                cwd=request.repository,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True, env=environment,
            )
        except (OSError, ValueError) as error:
            raise ClaudeCliError("local pre-acceptance executable launch failure") from error
        try:
            stdout, stderr = child.communicate(timeout=request.timeout_seconds)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(child.pid, signal.SIGTERM)
            try:
                stdout, stderr = child.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                stdout, stderr = child.communicate()
        stdout = stdout or ""
        stderr = stderr or ""
        candidate = None if timed_out or child.returncode != 0 else self._candidate_from_wrapper(stdout)
        diagnostics = () if child.returncode == 0 and not timed_out else (
            "Claude CLI timed out" if timed_out else f"Claude CLI exited {child.returncode}",
        )
        return AgentExecutionOutcome(
            candidate, child.returncode, timed_out, _bounded(stdout), _bounded(stderr), diagnostics
        )
