"""Fake and local subprocess test adapters; this module contains no provider integration."""

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


@dataclass(frozen=True)
class Invocation:
    run_id: str
    hop_id: str
    invocation_id: str


class Adapter(Protocol):
    def invoke(self, prompt: str, repository: Path) -> str: ...


@dataclass(frozen=True)
class FakeAdapter:
    output: str

    def invoke(self, prompt: str, repository: Path) -> str:
        return self.output


@dataclass(frozen=True)
class SubprocessAdapter:
    """A disposable-fixture adapter, deliberately not a provider SDK."""

    argv: tuple[str, ...]

    def invoke(self, prompt: str, repository: Path) -> str:
        completed = subprocess.run(self.argv, cwd=repository, input=prompt, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if completed.returncode:
            raise ResultValidationError(f"subprocess adapter failed: exit {completed.returncode}")
        return completed.stdout


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
