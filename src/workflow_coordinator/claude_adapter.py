"""Claude Code CLI transport with a restricted noninteractive profile."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .adapter import (
    ROUTING_RESULT_ENVELOPE_CONTRACT,
    AgentExecutionOutcome,
    AgentExecutionRequest,
    ExecutionNotStartedError,
)
from .model import (
    ModelUsage,
    NO_USAGE_REPORTED,
    ResultContractKind,
    UsageRecord,
    admits_result_contract_version,
    coerce_token_count,
    usage_coverage_for,
)
from .persistence import atomic_write_json
from .structured_result_schema import structured_result_envelope_schema


class ClaudeCliError(ExecutionNotStartedError, RuntimeError):
    """A local pre-launch Claude setup or launch failure."""


_TOOLS = "Read,Glob,Grep,Edit,Write,Bash,WebFetch,WebSearch"
_ENVIRONMENT_NAMES = ("PATH", "HOME", "USER", "LANG")


def _extra_environment_names() -> tuple[str, ...]:
    """Read comma-separated additional environment names once at construction."""
    value = os.environ.get("WFC_CLAUDE_EXTRA_ENV_NAMES", "")
    return tuple(name.strip() for name in value.split(",")) if value else ()

# Claude Code derives its own per-session IPC directory from TMPDIR (keyed by
# uid, the launch cwd with "/" replaced by "-", and a session UUID) and binds
# AF_UNIX sockets inside it for the Bash tool's sandbox bridge. AF_UNIX socket
# paths are capped at 108 bytes (`sockaddr_un.sun_path`); WFC's own ambient
# TMPDIR is a long, human-readable, Attempt-scoped scratch/state directory
# name, and inheriting it reliably pushes that derived path past the kernel
# limit, failing the sandbox bridge even for trivial commands. Give the
# subprocess a short, invocation-scoped TMPDIR instead of inheriting it.
_SHORT_TMP_ROOT = Path("/tmp")

# Unprivileged, WFC-owned fallback used only when the ambient TMPDIR/TMP/TEMP
# (or, failing those, the platform tempfile default) resolves to
# _SHORT_TMP_ROOT itself -- e.g. the stock Ubuntu/WSL default with none of
# those set, or an operator explicitly setting TMPDIR=/tmp -- which would
# otherwise make the short symlink and its own real target the same path.
# Historically mirrored create_attempt_scratch()'s own unprivileged
# `~/wfc-scratch` default (the now-retired `scripts/wfc_op.py` Harness).
_WFC_FALLBACK_SCRATCH_ROOT = Path.home() / ".wfc-claude-scratch"


def _external_scratch_root() -> Path:
    """The large-capacity, Attempt-isolated scratch WFC's own launch already
    exports via TMPDIR/TMP/TEMP ahead of the adapter shortening it for the
    subprocess (FD-08); falls back to the platform default outside WFC so
    the adapter still works standalone, without assuming that layout. Never
    returns a path that resolves to _SHORT_TMP_ROOT; falls back to
    _WFC_FALLBACK_SCRATCH_ROOT instead in that degenerate case."""
    for name in ("TMPDIR", "TMP", "TEMP"):
        value = os.environ.get(name)
        if value:
            root = Path(value)
            break
    else:
        root = Path(tempfile.gettempdir())
    if root.resolve() == _SHORT_TMP_ROOT.resolve():
        return _WFC_FALLBACK_SCRATCH_ROOT
    return root


def _supersede_or_reject_leftover(path: Path, description: str) -> None:
    """Deterministically clear a leftover at a WFC-derived, invocation-scoped
    path (e.g. after an unclean prior shutdown of the same invocation),
    without ever touching a path this process does not itself own."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if info.st_uid != os.geteuid():
        raise ClaudeCliError(f"{description} already exists and is not owned by the current user: {path}")
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _remove_scratch_path(path: Path) -> None:
    """Best-effort removal of either the short TMPDIR symlink or the real
    scratch directory it points at. Never raises, so a failure removing one
    cleanup target can never suppress the other cleanup step or mask an
    in-flight exception from the caller's try block."""
    try:
        if path.is_symlink():
            path.unlink(missing_ok=True)
        else:
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _bounded(value: str, limit: int = 16_384) -> str:
    return value[-limit:]


def _claude_model_usage(model_usage: object) -> tuple[ModelUsage, ...]:
    """Project Claude's ``modelUsage`` (keyed by model id) defensively; an
    entry whose own shape is wrong is skipped, never raised."""

    if not isinstance(model_usage, dict):
        return ()
    entries = []
    for model_id, raw_entry in model_usage.items():
        if not isinstance(model_id, str) or not model_id or not isinstance(raw_entry, dict):
            continue
        entries.append(
            ModelUsage(
                model=model_id,
                input_tokens=coerce_token_count(raw_entry.get("inputTokens")),
                output_tokens=coerce_token_count(raw_entry.get("outputTokens")),
                cache_read_tokens=coerce_token_count(raw_entry.get("cacheReadInputTokens")),
                cache_creation_tokens=coerce_token_count(raw_entry.get("cacheCreationInputTokens")),
                reasoning_output_tokens=coerce_token_count(raw_entry.get("thinkingTokens")),
            )
        )
    return tuple(sorted(entries, key=lambda item: item.model))


def _usage_from_wrapper_stdout(stdout: str) -> UsageRecord:
    """Parse Claude's token usage from the full, un-bounded wrapper stdout.

    Independent of ``_candidate_from_wrapper``'s success criteria: the
    invocation's cost already occurred even when the wrapper reports failure,
    so usage is read whenever the wrapper itself parses as a JSON object,
    never gated on ``type``/``subtype``/``is_error``. A monetary field such
    as ``total_cost_usd``/``costUSD`` is never read here.
    """

    try:
        wrapper = json.loads(stdout)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return NO_USAGE_REPORTED
    if not isinstance(wrapper, dict):
        return NO_USAGE_REPORTED
    usage = wrapper.get("usage")
    input_tokens = output_tokens = cache_read_tokens = cache_creation_tokens = None
    reasoning_output_tokens = None
    if isinstance(usage, dict):
        input_tokens = coerce_token_count(usage.get("input_tokens"))
        output_tokens = coerce_token_count(usage.get("output_tokens"))
        cache_read_tokens = coerce_token_count(usage.get("cache_read_input_tokens"))
        cache_creation_tokens = coerce_token_count(usage.get("cache_creation_input_tokens"))
        details = usage.get("output_tokens_details")
        if isinstance(details, dict):
            reasoning_output_tokens = coerce_token_count(details.get("thinking_tokens"))
    return UsageRecord(
        coverage=usage_coverage_for(input_tokens, output_tokens),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
        # Claude's ``input_tokens`` is reported net of both cache read and
        # cache creation (see the fixture README and the verification
        # record); never includes them the way Codex's does.
        input_includes_cache_read=False,
        per_model=_claude_model_usage(wrapper.get("modelUsage")),
    )


@dataclass(frozen=True)
class ClaudeCliAdapter:
    """Concrete Claude Code implementation of the unchanged execution port."""

    result_directory: Path
    model: str
    reasoning_effort: str
    executable: str = "claude"
    extra_environment_names: tuple[str, ...] = field(default_factory=_extra_environment_names)

    def __post_init__(self) -> None:
        if not isinstance(self.extra_environment_names, tuple):
            raise ValueError("Claude CLI extra environment names must be a tuple")
        for name in self.extra_environment_names:
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                    or name.startswith("CLAUDE_CODE_") or name in {"TMPDIR", "TMP", "TEMP"}):
                raise ValueError("invalid or reserved Claude CLI extra environment name")
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
    def _settings(request: AgentExecutionRequest, external_scratch_dir: Path) -> dict[str, object]:
        scratch = str(external_scratch_dir)
        return {
            "sandbox": {
                "enabled": True,
                "failIfUnavailable": True,
                "allowUnsandboxedCommands": False,
                "filesystem": {
                    "denyRead": ["~/"],
                    "allowRead": [str(request.repository.resolve()), scratch],
                    "allowWrite": [scratch],
                },
                # Claude 2.1.322 merges WebFetch(domain:*) below into the Bash
                # proxy allowlist. This requests broad networking, including
                # potential intranet access, not public-only containment.
                "network": {"allowedDomains": []},
            },
            "permissions": {
                # Restricted mode confines file tools to the working directory.
                "allow": ["Edit", "Write", "WebFetch(domain:*)", "WebSearch", "Bash(curl:*)"],
                "deny": [],
                "blockReadsOutsideWorkingDirectories": True,
            },
        }

    @staticmethod
    def _invocation_token(request: AgentExecutionRequest) -> str:
        return hashlib.sha256(
            f"{request.invocation.run_id}/{request.invocation.hop_id}/"
            f"{request.invocation.invocation_id}".encode()
        ).hexdigest()[:16]

    @classmethod
    def _short_subprocess_tmp_dir(cls, request: AgentExecutionRequest) -> Path:
        """A short, deterministic per-invocation TMPDIR symlink path; see
        `_SHORT_TMP_ROOT`. Its real target is `_external_scratch_dir()`."""
        return _SHORT_TMP_ROOT / f"wfc-claude-{cls._invocation_token(request)}"

    @classmethod
    def _external_scratch_dir(cls, request: AgentExecutionRequest) -> Path:
        """The dedicated, invocation-scoped real directory the short TMPDIR
        symlink points at; see `_external_scratch_root()`."""
        return _external_scratch_root() / f"wfc-claude-{cls._invocation_token(request)}"

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
        tmp_dir = self._short_subprocess_tmp_dir(request)
        real_dir = self._external_scratch_dir(request)
        _supersede_or_reject_leftover(real_dir, "external scratch target")
        _supersede_or_reject_leftover(tmp_dir, "short TMPDIR symlink")
        try:
            try:
                real_dir.mkdir(parents=True, mode=0o700)
                real_dir.chmod(0o700)
                real_dir = real_dir.resolve()
                tmp_dir.symlink_to(real_dir, target_is_directory=True)
                schema = self.result_directory / "structured-result.schema.json"
                settings = self.result_directory / "claude-closed-settings.json"
                schema_object = structured_result_envelope_schema(
                    request.invocation, request.contract_version, request.result_contract_kind,
                    permitted_routes=request.permitted_routes,
                    finding_authority=request.finding_authority,
                )
                atomic_write_json(schema, schema_object)
                atomic_write_json(settings, self._settings(request, real_dir))
                environment = {
                    name: os.environ[name]
                    for name in (*_ENVIRONMENT_NAMES, *self.extra_environment_names)
                    if name in os.environ
                }
                environment.update(TMPDIR=str(tmp_dir), TMP=str(tmp_dir), TEMP=str(tmp_dir))
                child = subprocess.Popen(
                    self.argv(
                        request,
                        json.dumps(schema_object, separators=(",", ":"), sort_keys=True),
                        settings,
                    ),
                    cwd=request.repository.resolve(),
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
        finally:
            # tmp_dir is normally a symlink (shutil.rmtree() refuses to
            # operate on one, silently no-oping under ignore_errors=True)
            # but may be a plain directory if symlink creation itself never
            # completed; remove each target independently so a failure
            # removing one can never suppress the other or the exception
            # already in flight from the try block above.
            _remove_scratch_path(tmp_dir)
            _remove_scratch_path(real_dir)
        stdout = stdout or ""
        stderr = stderr or ""
        candidate = None if timed_out or child.returncode != 0 else self._candidate_from_wrapper(stdout)
        usage = _usage_from_wrapper_stdout(stdout)
        diagnostics = () if child.returncode == 0 and not timed_out else (
            "Claude CLI timed out" if timed_out else f"Claude CLI exited {child.returncode}",
        )
        return AgentExecutionOutcome(
            candidate, child.returncode, timed_out, _bounded(stdout), _bounded(stderr), diagnostics,
            usage=usage,
        )
