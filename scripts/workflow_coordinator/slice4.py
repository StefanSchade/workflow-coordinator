"""Bounded Slice-4 local Codex boundary for the one disposable proof.

This is intentionally a concrete Codex CLI call, not an adapter registry.  Its
state root is external to the repository passed to Codex.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from .persistence import atomic_write_json, atomic_write_text


class Slice4Error(RuntimeError):
    """The real-agent boundary cannot safely proceed."""


@dataclass(frozen=True)
class CheckoutAssociation:
    checkout_id: str
    canonical_path: str
    git_dir: str
    common_dir: str


@dataclass(frozen=True)
class CodexOutcome:
    returncode: int | None
    timed_out: bool
    acceptance: str
    stdout: str
    stderr: str
    result_text: str | None


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(("git", "-C", str(repository), *args), text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode:
        raise Slice4Error(completed.stderr.strip() or "Git fact lookup failed")
    return completed.stdout.strip()


def _facts(repository: Path) -> tuple[str, str, str]:
    root = Path(_git(repository, "rev-parse", "--show-toplevel")).resolve()
    git_dir = Path(_git(root, "rev-parse", "--absolute-git-dir")).resolve()
    common_dir = Path(_git(root, "rev-parse", "--git-common-dir"))
    if not common_dir.is_absolute():
        common_dir = (root / common_dir).resolve()
    return str(root), str(git_dir), str(common_dir)


def register_checkout(state_root: Path, repository: Path) -> CheckoutAssociation:
    """Create the one opaque association, or validate its existing exact facts."""
    state_root = state_root.resolve()
    canonical_path, git_dir, common_dir = _facts(repository)
    mapping = state_root / "checkout-association.json"
    if mapping.exists():
        return validate_checkout(state_root, repository)
    association = CheckoutAssociation(str(uuid.uuid4()), canonical_path, git_dir, common_dir)
    atomic_write_json(mapping, {"schema_version": 1, **association.__dict__})
    (state_root / "checkouts" / association.checkout_id).mkdir(parents=True, exist_ok=True)
    return association


def validate_checkout(state_root: Path, repository: Path) -> CheckoutAssociation:
    """Fail closed; a moved checkout needs explicit maintainer re-registration."""
    mapping = state_root.resolve() / "checkout-association.json"
    try:
        data = json.loads(mapping.read_text(encoding="utf-8"))
        expected = CheckoutAssociation(**{key: data[key] for key in CheckoutAssociation.__dataclass_fields__})
    except (FileNotFoundError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise Slice4Error("external checkout association is absent or malformed") from error
    if data.get("schema_version") != 1 or not expected.checkout_id:
        raise Slice4Error("external checkout association has unsupported schema")
    actual = _facts(repository)
    if actual != (expected.canonical_path, expected.git_dir, expected.common_dir):
        raise Slice4Error("checkout/Git association mismatch; explicit maintainer re-registration required")
    return expected


def checkout_state_root(state_root: Path, association: CheckoutAssociation) -> Path:
    return state_root.resolve() / "checkouts" / association.checkout_id


def _bounded(value: str, limit: int = 16_384) -> str:
    return value[-limit:]


def invoke_codex(
    *, repository: Path, state_root: Path, association: CheckoutAssociation,
    prompt: str, timeout_seconds: float, executable: str = "codex",
) -> CodexOutcome:
    """Run one fresh Codex process and terminate its whole session on deadline."""
    if timeout_seconds <= 0:
        raise Slice4Error("timeout must be positive")
    validate_checkout(state_root, repository)
    run_root = checkout_state_root(state_root, association)
    schema = run_root / "structured-result.schema.json"
    result = run_root / "result-message.json"
    if repository.resolve() in (state_root.resolve(), *state_root.resolve().parents):
        raise Slice4Error("trusted state root must be outside agent workspace")
    schema_data = {
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "run_id", "hop_id", "invocation_id", "result"],
        "properties": {"schema_version": {"const": 1}, "run_id": {"type": "string"},
                       "hop_id": {"type": "string"}, "invocation_id": {"type": "string"},
                       "result": {"type": "object"}},
    }
    atomic_write_json(schema, schema_data)
    atomic_write_text(result, "")
    argv = (executable, "--ask-for-approval", "never", "exec", "--ephemeral", "--json",
            "--color", "never", "--sandbox", "workspace-write", "--model", "gpt-5.6-terra",
            "-c", 'model_reasoning_effort="medium"', "--cd", str(repository.resolve()),
            "--output-schema", str(schema), "--output-last-message", str(result), "-")
    try:
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, start_new_session=True)
    except OSError as error:
        raise Slice4Error("local pre-acceptance executable launch failure") from error
    try:
        stdout, stderr = child.communicate(prompt, timeout=timeout_seconds)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(child.pid, signal.SIGTERM)
        try:
            stdout, stderr = child.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            stdout, stderr = child.communicate()
    result_text = result.read_text(encoding="utf-8") or None
    # Only an independently validated correlated result establishes acceptance.
    # Any other post-spawn state is conservatively uncertain and charged.
    acceptance = "established" if result_text else "uncertain"
    return CodexOutcome(child.returncode, timed_out, acceptance, _bounded(stdout), _bounded(stderr), result_text)
