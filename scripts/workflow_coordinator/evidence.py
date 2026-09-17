"""Fixed trusted evidence callables for Slice 2."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .model import Observation


@dataclass(frozen=True)
class EvidenceCallable:
    provider: str
    argv: tuple[str, ...]


TRUSTED_EVIDENCE: dict[str, EvidenceCallable] = {
    "diff_hygiene": EvidenceCallable("diff_hygiene", ("git", "diff", "--check")),
    "git_version": EvidenceCallable("git_version", ("git", "--version")),
}


class EvidenceRunner(Protocol):
    """One fixed runner/configuration shared by preflight and execution."""

    def supports(self, provider: str) -> bool: ...

    def observe(self, provider: str, repository: Path) -> Observation: ...


class TrustedEvidenceRunner:
    """Production runner over the sole fixed in-code trusted registry."""

    def supports(self, provider: str) -> bool:
        return provider in TRUSTED_EVIDENCE

    def observe(self, provider: str, repository: Path) -> Observation:
        return _run_registered_evidence(provider, repository)


PRODUCTION_EVIDENCE_RUNNER = TrustedEvidenceRunner()


def _run_registered_evidence(
    provider: str, repository: Path, *, selected: bool = True
) -> Observation:
    """Run only an in-code registered argv; observations never accept a Run."""

    if provider not in TRUSTED_EVIDENCE:
        raise ValueError(f"unknown trusted evidence provider: {provider}")
    specification = TRUSTED_EVIDENCE[provider]
    if not selected:
        return Observation(provider, "not_run", specification.argv, "not selected by trusted policy", None, None)
    try:
        completed = subprocess.run(
            specification.argv,
            cwd=repository,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        return Observation(specification.provider, "error", specification.argv, str(error), None, None)
    output = completed.stdout + completed.stderr
    digest = hashlib.sha256(output.encode()).hexdigest()
    status = "pass" if completed.returncode == 0 else "fail"
    summary = f"exit {completed.returncode}" if not output.strip() else output.strip().splitlines()[0]
    return Observation(specification.provider, status, specification.argv, summary, None, digest)


def run_evidence(provider: str, repository: Path, *, selected: bool = True) -> Observation:
    """Compatibility entry point bound to the production registry."""

    return _run_registered_evidence(provider, repository, selected=selected)


def observation_error(observation: Observation, *, expected_provider: str) -> str | None:
    """Validate a trusted-runner return before it can influence routing."""

    if observation.provider != expected_provider:
        return "malformed trusted evidence: provider identity mismatch"
    if observation.status not in {"pass", "fail", "error", "not_run"}:
        return f"malformed trusted evidence status: {observation.status}"
    if not observation.summary:
        return "malformed trusted evidence: empty summary"
    if not isinstance(observation.command, tuple) or not all(
        isinstance(item, str) and item for item in observation.command
    ):
        return "malformed trusted evidence: invalid command"
    if observation.artifact_ref is not None and (
        not isinstance(observation.artifact_ref, str) or not observation.artifact_ref
    ):
        return "malformed trusted evidence: invalid artifact reference"
    if observation.digest is not None and (
        not isinstance(observation.digest, str) or not observation.digest
    ):
        return "malformed trusted evidence: invalid digest"
    if observation.status in {"pass", "fail"} and observation.digest is None:
        return "malformed trusted evidence: pass/fail requires digest"
    return None
