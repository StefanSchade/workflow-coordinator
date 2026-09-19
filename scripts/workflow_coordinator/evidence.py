"""Policy-bound preparation and execution of A2 evidence commands."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence

from .model import (
    COORDINATOR_HARD_MAX_EVIDENCE_TIMEOUT_SECONDS,
    EvidenceCommand,
    Observation,
    StatFingerprint,
)


ENVIRONMENT_POLICY = "coordinator-inherited-environment-v1"
CWD_POLICY = "canonical-repository-root-v1"


class EvidencePreparationError(ValueError):
    """A selected policy command could not be prepared during admission."""


class ExecutableResolutionError(OSError):
    """An authored executable cannot resolve to an executable regular file."""


@dataclass(frozen=True)
class EnvironmentSnapshot:
    values: Mapping[str, str]
    path_source: str
    path_digest: str


@dataclass(frozen=True)
class ExecutableResolution:
    invocation_path: str
    canonical_path: str
    stat_fingerprint: StatFingerprint


@dataclass(frozen=True)
class PreparedEvidenceCommand:
    command_name: str
    authored_argv: tuple[str, ...]
    argv_digest: str
    timeout_seconds: int
    repository: Path
    environment: EnvironmentSnapshot
    admission_resolution: ExecutableResolution


class EvidenceRunner(Protocol):
    """Execution-only seam; command authority was already admitted and prepared."""

    def observe(self, command: PreparedEvidenceCommand) -> Observation: ...


class FailClosedEvidenceRunner:
    """Default execution seam that grants no authority and executes nothing."""

    def observe(self, command: PreparedEvidenceCommand) -> Observation:
        raise RuntimeError("evidence runner was not explicitly supplied")


FAIL_CLOSED_EVIDENCE_RUNNER = FailClosedEvidenceRunner()


def authored_argv_digest(argv: Sequence[str]) -> str:
    encoded = json.dumps(
        list(argv), ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def snapshot_environment(environ: Mapping[str, str] | None = None) -> EnvironmentSnapshot:
    values = dict(os.environ if environ is None else environ)
    if "PATH" in values:
        path_source = "inherited"
    else:
        values["PATH"] = os.defpath
        path_source = "os.defpath"
    path_digest = hashlib.sha256(
        values["PATH"].encode("utf-8", errors="surrogateescape")
    ).hexdigest()
    return EnvironmentSnapshot(MappingProxyType(values), path_source, path_digest)


def _fingerprint(path: str) -> StatFingerprint:
    facts = os.stat(path)
    if not stat.S_ISREG(facts.st_mode) or not os.access(path, os.X_OK):
        raise ExecutableResolutionError("selected executable is not an executable regular file")
    return StatFingerprint(
        st_dev=facts.st_dev,
        st_ino=facts.st_ino,
        st_mode=facts.st_mode,
        st_size=facts.st_size,
        st_mtime_ns=facts.st_mtime_ns,
    )


def resolve_executable(
    authored_argv0: str,
    repository: Path,
    environment: EnvironmentSnapshot,
) -> ExecutableResolution:
    """Resolve one POSIX executable with repository-root relative PATH semantics."""

    root = repository.resolve()
    candidates: list[str]
    if os.path.isabs(authored_argv0):
        candidates = [os.path.abspath(authored_argv0)]
    elif "/" in authored_argv0:
        candidates = [os.path.abspath(os.path.join(root, authored_argv0))]
    else:
        candidates = []
        # ``str.split`` deliberately maps PATH="" to one empty entry.
        for entry in environment.values["PATH"].split(os.pathsep):
            base = entry if os.path.isabs(entry) else os.path.join(root, entry)
            candidates.append(os.path.abspath(os.path.join(base, authored_argv0)))
    for candidate in candidates:
        try:
            fingerprint = _fingerprint(candidate)
        except (FileNotFoundError, NotADirectoryError, PermissionError, ExecutableResolutionError):
            continue
        return ExecutableResolution(
            invocation_path=candidate,
            canonical_path=os.path.realpath(candidate),
            stat_fingerprint=fingerprint,
        )
    raise ExecutableResolutionError(
        f"executable does not resolve to an executable regular file: {authored_argv0}"
    )


def prepare_evidence_commands(
    selected_names: Sequence[str],
    definitions: Mapping[str, EvidenceCommand],
    repository: Path,
    *,
    environ: Mapping[str, str] | None = None,
    environment: EnvironmentSnapshot | None = None,
) -> tuple[PreparedEvidenceCommand, ...]:
    """Snapshot the environment once and admission-resolve selected definitions."""

    if environ is not None and environment is not None:
        raise ValueError("supply either environ or a prepared environment snapshot")
    active_environment = environment or snapshot_environment(environ)
    prepared: list[PreparedEvidenceCommand] = []
    for name in selected_names:
        definition = definitions[name]
        if (
            isinstance(definition.timeout_seconds, bool)
            or not isinstance(definition.timeout_seconds, int)
            or not 1
            <= definition.timeout_seconds
            <= COORDINATOR_HARD_MAX_EVIDENCE_TIMEOUT_SECONDS
        ):
            raise EvidencePreparationError(
                f"evidence command {name}: timeout_seconds must be a non-boolean "
                f"integer in 1..{COORDINATOR_HARD_MAX_EVIDENCE_TIMEOUT_SECONDS}"
            )
        try:
            resolution = resolve_executable(definition.argv[0], repository, active_environment)
        except ExecutableResolutionError as error:
            raise EvidencePreparationError(f"evidence command {name}: {error}") from error
        prepared.append(
            PreparedEvidenceCommand(
                command_name=name,
                authored_argv=definition.argv,
                argv_digest=authored_argv_digest(definition.argv),
                timeout_seconds=definition.timeout_seconds,
                repository=repository.resolve(),
                environment=active_environment,
                admission_resolution=resolution,
            )
        )
    return tuple(prepared)


def _stream_facts(value: bytes) -> tuple[int, str, bool]:
    return len(value), hashlib.sha256(value).hexdigest(), bool(value)


def _observation(
    command: PreparedEvidenceCommand,
    *,
    status: str,
    executed_argv: tuple[str, ...],
    summary: str,
    resolution: ExecutableResolution | None,
    exit_code: int | None = None,
    signal: int | None = None,
    error_class: str | None = None,
    stdout: bytes | None = None,
    stderr: bytes | None = None,
) -> Observation:
    stdout_facts = (None, None, None) if stdout is None else _stream_facts(stdout)
    stderr_facts = (None, None, None) if stderr is None else _stream_facts(stderr)
    return Observation(
        provider=command.command_name,
        status=status,
        command_name=command.command_name,
        command=executed_argv,
        summary=summary,
        artifact_ref=None,
        argv_digest=command.argv_digest,
        authored_argv0=command.authored_argv[0],
        timeout_seconds=command.timeout_seconds,
        cwd_policy=CWD_POLICY,
        invocation_path=None if resolution is None else resolution.invocation_path,
        canonical_path=None if resolution is None else resolution.canonical_path,
        stat_fingerprint=None if resolution is None else resolution.stat_fingerprint,
        environment_policy=ENVIRONMENT_POLICY,
        path_source=command.environment.path_source,
        path_digest=command.environment.path_digest,
        exit_code=exit_code,
        signal=signal,
        error_class=error_class,
        stdout_byte_count=stdout_facts[0],
        stdout_sha256=stdout_facts[1],
        stdout_body_omitted=stdout_facts[2],
        stderr_byte_count=stderr_facts[0],
        stderr_sha256=stderr_facts[1],
        stderr_body_omitted=stderr_facts[2],
    )


class ProductionEvidenceRunner:
    """Execute an already-authorized prepared command without name authority."""

    def observe(self, command: PreparedEvidenceCommand) -> Observation:
        try:
            resolution = resolve_executable(
                command.authored_argv[0], command.repository, command.environment
            )
        except (OSError, ValueError) as error:
            return _observation(
                command,
                status="error",
                executed_argv=(),
                summary=f"resolution_error:{type(error).__name__}",
                resolution=None,
                error_class=f"resolution:{type(error).__name__}",
            )
        executed = (resolution.invocation_path, *command.authored_argv[1:])
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                executed,
                shell=False,
                cwd=command.repository,
                stdin=subprocess.DEVNULL,
                env=command.environment.values,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, ValueError) as error:
            return _observation(
                command,
                status="error",
                executed_argv=executed,
                summary=f"spawn_error:{type(error).__name__}",
                resolution=resolution,
                error_class=f"spawn:{type(error).__name__}",
            )
        try:
            stdout, stderr = process.communicate(timeout=command.timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            return _observation(
                command,
                status="error",
                executed_argv=executed,
                summary=f"timeout after {command.timeout_seconds}s",
                resolution=resolution,
                error_class="timeout",
            )
        except Exception as error:
            if process.poll() is None:
                process.kill()
            process.wait()
            return _observation(
                command,
                status="error",
                executed_argv=executed,
                summary=f"communication_error:{type(error).__name__}",
                resolution=resolution,
                error_class=f"communication:{type(error).__name__}",
            )
        returncode = process.returncode
        if returncode < 0:
            return _observation(
                command,
                status="error",
                executed_argv=executed,
                summary=f"terminated by signal {-returncode}",
                resolution=resolution,
                signal=-returncode,
                error_class="signal",
                stdout=stdout,
                stderr=stderr,
            )
        status = "pass" if returncode == 0 else "fail"
        return _observation(
            command,
            status=status,
            executed_argv=executed,
            summary=f"exit {returncode}",
            resolution=resolution,
            exit_code=returncode,
            stdout=stdout,
            stderr=stderr,
        )


PRODUCTION_EVIDENCE_RUNNER = ProductionEvidenceRunner()


def observation_error(
    observation: Observation, *, expected: PreparedEvidenceCommand
) -> str | None:
    """Validate an execution-only runner return against its prepared authority."""

    if observation.provider != expected.command_name or observation.command_name != expected.command_name:
        return "malformed trusted evidence: provider/command_name identity mismatch"
    if observation.provider != observation.command_name:
        return "malformed trusted evidence: provider and command_name must be equal"
    if observation.status not in {"pass", "fail", "error", "not_run"}:
        return f"malformed trusted evidence status: {observation.status}"
    if not observation.summary:
        return "malformed trusted evidence: empty summary"
    if observation.argv_digest != expected.argv_digest:
        return "malformed trusted evidence: authored argv digest mismatch"
    if observation.authored_argv0 != expected.authored_argv[0]:
        return "malformed trusted evidence: authored argv[0] mismatch"
    if observation.timeout_seconds != expected.timeout_seconds:
        return "malformed trusted evidence: timeout mismatch"
    if not isinstance(observation.command, tuple) or any(
        not isinstance(item, str) for item in observation.command
    ):
        return "malformed trusted evidence: invalid command"
    if observation.command and not observation.command[0]:
        return "malformed trusted evidence: invalid command executable"
    if observation.command and observation.command[1:] != expected.authored_argv[1:]:
        return "malformed trusted evidence: executed argv tail mismatch"
    if observation.artifact_ref is not None and (
        not isinstance(observation.artifact_ref, str) or not observation.artifact_ref
    ):
        return "malformed trusted evidence: invalid artifact reference"
    if observation.status == "pass" and (
        observation.exit_code != 0 or observation.signal is not None
    ):
        return "malformed trusted evidence: pass outcome mismatch"
    if observation.status == "fail" and (
        isinstance(observation.exit_code, bool)
        or not isinstance(observation.exit_code, int)
        or observation.exit_code <= 0
        or observation.signal is not None
    ):
        return "malformed trusted evidence: fail outcome mismatch"
    if observation.status == "error" and not observation.error_class:
        return "malformed trusted evidence: error requires error_class"
    for stream, byte_count, sha256, body_omitted in (
        (
            "stdout",
            observation.stdout_byte_count,
            observation.stdout_sha256,
            observation.stdout_body_omitted,
        ),
        (
            "stderr",
            observation.stderr_byte_count,
            observation.stderr_sha256,
            observation.stderr_body_omitted,
        ),
    ):
        if byte_count is sha256 is body_omitted is None:
            if observation.status in {"pass", "fail"}:
                return f"malformed trusted evidence: incomplete or invalid {stream} facts"
            continue
        if (
            isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count < 0
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
            or not isinstance(body_omitted, bool)
            or body_omitted != (byte_count > 0)
        ):
            return f"malformed trusted evidence: incomplete or invalid {stream} facts"
    return None
