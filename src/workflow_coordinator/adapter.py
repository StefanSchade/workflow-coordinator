"""Provider-neutral execution port and deterministic local test adapters."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .model import RoutingResult
from .serialization import ValidationError, routing_result_from_data


class ResultValidationError(ValidationError):
    """An adapter result is malformed, incomplete, or belongs to another invocation."""


class ExecutionResultValidationError(ResultValidationError):
    """A semantic rejection paired with its completed transport outcome."""

    def __init__(self, outcome: AgentExecutionOutcome, message: str):
        super().__init__(message)
        self.outcome = outcome


class ExecutionNotStartedError(ValueError):
    """A local failure that proves the execution port was not entered."""


@dataclass(frozen=True)
class Invocation:
    run_id: str
    hop_id: str
    invocation_id: str


@dataclass(frozen=True)
class AgentExecutionRequest:
    """Facts the application supplies for one bounded agent invocation.

    ``result_contract`` identifies the canonical semantic result envelope; its
    provider transport projection is deliberately an adapter concern.
    """

    invocation: Invocation
    prompt: str
    repository: Path
    result_contract: str
    timeout_seconds: float


@dataclass(frozen=True)
class AgentExecutionOutcome:
    """Transport facts, before provider-neutral semantic acceptance.

    A candidate result is intentionally not a ``RoutingResult``.  The caller
    must still parse, validate, and correlate it through ``validated_result``.
    """

    candidate_result: str | None
    returncode: int | None
    timed_out: bool
    stdout: str
    stderr: str
    diagnostics: tuple[str, ...] = ()


ROUTING_RESULT_ENVELOPE_CONTRACT = "routing-result-envelope/v1"


class AgentExecutionPort(Protocol):
    def execute(self, request: AgentExecutionRequest) -> AgentExecutionOutcome: ...


@dataclass(frozen=True)
class FakeAdapter:
    output: str

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionOutcome:
        return AgentExecutionOutcome(self.output, 0, False, "", "")


@dataclass(frozen=True)
class SubprocessAdapter:
    """A disposable-fixture adapter, deliberately not a provider SDK."""

    argv: tuple[str, ...]

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionOutcome:
        completed = subprocess.run(self.argv, cwd=request.repository, input=request.prompt, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        return AgentExecutionOutcome(
            completed.stdout if completed.returncode == 0 else None,
            completed.returncode,
            False,
            completed.stdout,
            completed.stderr,
            (f"subprocess adapter exited {completed.returncode}",) if completed.returncode else (),
        )


def validated_result(raw: str, expected: Invocation) -> RoutingResult:
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ResultValidationError("truncated or invalid structured result") from error
    if not isinstance(envelope, dict) or set(envelope) != {"schema_version", "run_id", "hop_id", "invocation_id", "result"}:
        raise ResultValidationError("structured result has missing or unknown envelope fields")
    if envelope["schema_version"] != 1:
        raise ResultValidationError("structured result has unsupported schema version")
    actual = (envelope["run_id"], envelope["hop_id"], envelope["invocation_id"])
    wanted = (expected.run_id, expected.hop_id, expected.invocation_id)
    if actual != wanted:
        raise ResultValidationError("structured result identity does not correlate to dispatch")
    try:
        return routing_result_from_data(envelope["result"])
    except ValidationError as error:
        raise ResultValidationError(str(error)) from error
