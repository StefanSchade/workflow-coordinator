"""Fixed trusted evidence callables for Slice 2."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .model import Observation


@dataclass(frozen=True)
class EvidenceCallable:
    provider: str
    argv: tuple[str, ...]


TRUSTED_EVIDENCE: dict[str, EvidenceCallable] = {
    "diff_hygiene": EvidenceCallable("diff_hygiene", ("git", "diff", "--check")),
    "git_version": EvidenceCallable("git_version", ("git", "--version")),
}


def run_evidence(provider: str, repository: Path, *, selected: bool = True) -> Observation:
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
