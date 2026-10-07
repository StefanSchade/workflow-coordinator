#!/usr/bin/env python3
"""wfc-op -- WFC Operator Harness v0 (Slice H0-1: ``prepare`` + ``launch``).

Deterministic, repository-owned replacement for the hand-authored PREPARE /
LAUNCH shell blocks documented in ``docs/run-preparation.adoc`` and
``docs/self-development.adoc``.  See
``docs/wfc_operator_harness_v0_concept_2026-10-06.adoc`` for the accepted
concept this implements.

Authority boundary (unchanged by this tool):

    HARNESS owns mechanics.           WFC owns workflow execution.
    GOVERNING INPUTS own authority.   AGENTS own bounded semantic work.
    MAINTAINER owns authorization / promotion / push.

``prepare`` performs every deterministic preparation mechanic -- pinning
Development/Runtime/Evidence identities, exporting and installing the pinned
Runtime, building the Development/evidence environment, writing
``execution-bindings.yaml``, assembling the controlled ``PATH``, running the
pinned Runtime's own canonical provider-free preflight, and materializing
``launch.sh`` -- and then stops.  It never launches.

``launch`` revalidates the prepared envelope against drift and, only then,
executes the already-materialized ``launch.sh`` exactly once, reporting its
stdout/stderr/return code faithfully.  It never repairs, retries, or
reconciles.

This module intentionally duplicates none of the pinned Runtime's own
admission/preflight semantics (see ``run_canonical_preflight_seam``) and does
not implement ``status`` or ``archive`` (Slice H0-2, not yet built).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class HarnessRefusal(RuntimeError):
    """A named, fail-closed Harness precondition or revalidation failure."""


# --------------------------------------------------------------- attempt id

ATTEMPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def validate_attempt(attempt: str) -> str:
    """Reject empty, path-traversing, absolute, or otherwise implausible attempt ids.

    Mirrors the refusal shape of
    ``workflow-coordinator-run-evidence/scripts/wfc-archive-run``'s own
    ``attempt_id`` derivation so attempt names stay usable by both tools.
    """

    if not attempt or attempt != attempt.strip():
        raise HarnessRefusal("attempt id must be non-empty with no surrounding whitespace")
    if attempt in (".", ".."):
        raise HarnessRefusal(f"refusing implausible attempt id: {attempt!r}")
    if attempt.startswith("-"):
        raise HarnessRefusal(f"refusing implausible attempt id: {attempt!r}")
    if any(ord(c) < 0x20 for c in attempt):
        raise HarnessRefusal("attempt id contains a control character")
    if "/" in attempt or "\\" in attempt or os.sep in attempt:
        raise HarnessRefusal(f"attempt id must not contain a path separator: {attempt!r}")
    if Path(attempt).is_absolute():
        raise HarnessRefusal(f"attempt id must not be an absolute path: {attempt!r}")
    if not ATTEMPT_RE.fullmatch(attempt):
        raise HarnessRefusal(f"attempt id contains characters outside {ATTEMPT_RE.pattern!r}: {attempt!r}")
    return attempt


def ops_root(attempt: str) -> Path:
    return Path.home() / "wfc-ops" / attempt


def state_root(attempt: str) -> Path:
    return Path.home() / "wfc-state" / attempt


# ----------------------------------------------------------------- Git facts

@dataclass(frozen=True)
class RepoPins:
    path: Path
    head: str


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(("git", "-C", str(repo), *args), text=True, capture_output=True)
    if completed.returncode != 0:
        raise HarnessRefusal(f"git {' '.join(args)} failed in {repo}: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _is_clean(repo: Path) -> bool:
    status = subprocess.run(
        ("git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"),
        text=True, capture_output=True, check=True,
    )
    return status.stdout.strip() == ""


def _is_detached(repo: Path) -> bool:
    result = subprocess.run(("git", "-C", str(repo), "symbolic-ref", "-q", "HEAD"), capture_output=True)
    return result.returncode != 0


def _pushurl(repo: Path) -> str | None:
    result = subprocess.run(
        ("git", "-C", str(repo), "config", "--get", "remote.origin.pushurl"),
        text=True, capture_output=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _require_git_toplevel(path: Path, *, name: str) -> Path:
    if not path.is_dir():
        raise HarnessRefusal(f"{name} path is not a directory: {path}")
    result = subprocess.run(
        ("git", "-C", str(path), "rev-parse", "--show-toplevel"), text=True, capture_output=True,
    )
    if result.returncode != 0:
        raise HarnessRefusal(f"{name} is not a Git repository: {path}")
    top = Path(result.stdout.strip()).resolve()
    if top != path.resolve():
        raise HarnessRefusal(f"{name} path is not the Git top-level (top-level is {top}): {path}")
    return top


def pin_development(path: Path) -> RepoPins:
    repo = _require_git_toplevel(path, name="Development")
    if not _is_clean(repo):
        raise HarnessRefusal(f"Development worktree is not clean (including untracked files): {repo}")
    return RepoPins(repo, _git(repo, "rev-parse", "HEAD"))


def pin_runtime(path: Path) -> RepoPins:
    repo = _require_git_toplevel(path, name="Runtime")
    if not _is_clean(repo):
        raise HarnessRefusal(f"Runtime worktree is not clean (including untracked files): {repo}")
    if not _is_detached(repo):
        raise HarnessRefusal(f"Runtime is not detached (self-development invariant): {repo}")
    pushurl = _pushurl(repo)
    if pushurl != "DISABLED":
        raise HarnessRefusal(
            f"Runtime remote.origin.pushurl must be DISABLED, found {pushurl!r}: {repo}"
        )
    return RepoPins(repo, _git(repo, "rev-parse", "HEAD"))


def pin_evidence(path: Path) -> RepoPins:
    repo = _require_git_toplevel(path, name="Evidence")
    if not _is_clean(repo):
        raise HarnessRefusal(f"Evidence worktree is not clean (including untracked files): {repo}")
    return RepoPins(repo, _git(repo, "rev-parse", "HEAD"))


def _revalidate(record: dict, *, name: str, pin_fn) -> RepoPins:
    path = Path(record["path"])
    try:
        pins = pin_fn(path)
    except HarnessRefusal as error:
        raise HarnessRefusal(f"{name} revalidation failed: {error}") from error
    if pins.head != record["head"]:
        raise HarnessRefusal(
            f"{name} HEAD drift since preparation: expected {record['head']}, found {pins.head} at {path}"
        )
    return pins


# ------------------------------------------------------------- attempt roots

def require_fresh_attempt(ops: Path, state: Path) -> None:
    if ops.exists():
        raise HarnessRefusal(f"OPS already exists; refusing to reuse an attempt id: {ops}")
    if state.exists():
        raise HarnessRefusal(f"STATE already exists; refusing to reuse an attempt id: {state}")


# -------------------------------------------------------------- governing inputs

def _governing_relative_path(value: str, *, name: str) -> str:
    """A light mechanical sanity check only -- not a re-implementation of
    ``operator._relative_path``'s normalization/scope semantics. Harness does
    not semantically validate or reinterpret governing inputs; the canonical
    preflight seam (run under the pinned Runtime) owns that.
    """

    if not value or value != value.strip() or "\\" in value:
        raise HarnessRefusal(f"--{name} must be a repository-relative path: {value!r}")
    if value.startswith("/"):
        raise HarnessRefusal(f"--{name} must not be an absolute path: {value!r}")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise HarnessRefusal(f"--{name} must not escape Development: {value!r}")
    return value


def require_governing_input(development: Path, relative: str, *, name: str) -> None:
    relative = _governing_relative_path(relative, name=name)
    if not (development / relative).is_file():
        raise HarnessRefusal(f"--{name} does not exist beneath Development: {relative}")


# ------------------------------------------------------------ environment build

def build_venv(path: Path) -> Path:
    result = subprocess.run((sys.executable, "-m", "venv", str(path)), capture_output=True, text=True)
    if result.returncode != 0:
        raise HarnessRefusal(f"failed to create virtual environment at {path}: {result.stderr.strip()}")
    return path


def pip_install(env_dir: Path, target: str) -> None:
    pip = env_dir / "bin" / "pip"
    result = subprocess.run((str(pip), "install", target), capture_output=True, text=True)
    if result.returncode != 0:
        raise HarnessRefusal(f"pip install {target!r} into {env_dir} failed: {result.stderr.strip()}")


def export_runtime(runtime: Path, head: str, ops: Path) -> Path:
    """``git archive`` the exact pinned Runtime commit into fresh external storage.

    Never installs from the live Runtime checkout; Runtime is never mutated
    and never written into (self-development.adoc "Topology and preparation").
    """

    src = ops / "runtime-src"
    src.mkdir(parents=True)
    archiver = subprocess.Popen(
        ("git", "-C", str(runtime), "archive", head), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    extractor = subprocess.run(("tar", "-x", "-C", str(src)), stdin=archiver.stdout, capture_output=True)
    archiver.stdout.close()
    archive_stderr = archiver.stderr.read()
    archiver.stderr.close()
    archiver.wait()
    if archiver.returncode != 0 or extractor.returncode != 0:
        raise HarnessRefusal(
            f"failed to export Runtime {head} from {runtime}: "
            f"{(archive_stderr or extractor.stderr).decode(errors='replace').strip()}"
        )
    return src


def prepare_runtime_environment(runtime_pins: RepoPins, ops: Path) -> Path:
    """Export + non-editably install the pinned Runtime; verify its absolute ``wfc``.

    Never uses bare ``wfc``; only the absolute ``<runtime-env>/bin/wfc`` is ever
    referenced, here and in the materialized ``launch.sh``.
    """

    src = export_runtime(runtime_pins.path, runtime_pins.head, ops)
    env_dir = ops / "runtime-env"
    build_venv(env_dir)
    pip_install(env_dir, str(src))
    wfc = env_dir / "bin" / "wfc"
    result = subprocess.run((str(wfc), "--help"), capture_output=True, text=True)
    if result.returncode != 0:
        raise HarnessRefusal(f"pinned Runtime executable failed --help: {wfc}: {result.stderr.strip()}")
    return env_dir


def prepare_development_environment(development_pins: RepoPins, ops: Path) -> Path:
    """Build the development/evidence venv, installed from the LIVE Development
    checkout (never an export).

    Unlike Runtime, self-development.adoc never exports Development/evidence:
    Development is the live-edited target itself, and pinned source identity
    (verified separately via Git HEAD/clean checks) is a different question
    from test-execution substrate.  Installing from an archived export would
    strip ``.git``, which is exactly the S4-005 class of mistake this slice
    exists to prevent by construction (see the concept's S12).
    """

    env_dir = ops / "dev-env"
    build_venv(env_dir)
    pip_install(env_dir, f"{development_pins.path}[dev]")
    return env_dir


def controlled_path(dev_env: Path, runtime_env: Path, base_path: str) -> str:
    """``<development/evidence-env>/bin:<runtime-env>/bin:<trusted base PATH>``."""

    return os.pathsep.join([str(dev_env / "bin"), str(runtime_env / "bin"), base_path])


def verify_controlled_path(dev_env: Path, path_value: str) -> None:
    resolved = shutil.which("python3", path=path_value)
    expected = str(dev_env / "bin" / "python3")
    if resolved != expected:
        raise HarnessRefusal(
            "controlled PATH does not resolve python3 to the development/evidence "
            f"interpreter (resolved {resolved!r}, expected {expected!r})"
        )


# --------------------------------------------------------- execution bindings

ROLE_BINDING_RE = re.compile(r"^(?P<role>[A-Za-z_]+)=(?P<adapter>[^:]+):(?P<model>[^:]+):(?P<reasoning>[^:]+)$")


def parse_role_binding(value: str) -> tuple[str, dict[str, str]]:
    match = ROLE_BINDING_RE.fullmatch(value)
    if not match:
        raise HarnessRefusal(
            f"--role-binding must look like ROLE=ADAPTER:MODEL:REASONING, got {value!r}"
        )
    return match["role"], {
        "adapter": match["adapter"], "model": match["model"], "reasoning": match["reasoning"],
    }


def write_execution_bindings(ops: Path, role_bindings: list[str]) -> Path:
    """The equivalent of ``execution-bindings.yaml``, from explicit maintainer
    inputs only.  No automatic model/provider selection, no inferred fallback.
    """

    bindings: dict[str, dict[str, str]] = {}
    for value in role_bindings:
        role, binding = parse_role_binding(value)
        if role in bindings:
            raise HarnessRefusal(f"duplicate --role-binding role: {role}")
        bindings[role] = binding
    if not bindings:
        raise HarnessRefusal("at least one --role-binding is required")
    lines = ["schema_version: 1", "bindings:"]
    for role in sorted(bindings):
        binding = bindings[role]
        lines.append(f"  {role}:")
        lines.append(f"    adapter: {binding['adapter']}")
        lines.append(f"    model: {binding['model']}")
        lines.append(f"    reasoning: {binding['reasoning']}")
    path = ops / "execution-bindings.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------- canonical preflight

PREFLIGHT_SEAM_SCRIPT = """\
import sys

from workflow_coordinator.operator import OperatorPreflightError, _parse_args, _preflight
from workflow_coordinator.evidence import PRODUCTION_EVIDENCE_RUNNER

try:
    args = _parse_args(sys.argv[1:])
    _preflight(args, evidence_runner=PRODUCTION_EVIDENCE_RUNNER)
except OperatorPreflightError as error:
    print(f"PREFLIGHT REFUSED: {error}", file=sys.stderr)
    raise SystemExit(1)
except Exception as error:  # noqa: BLE001 -- surfaced verbatim as a Harness refusal
    print(f"PREFLIGHT ERROR: {type(error).__name__}: {error}", file=sys.stderr)
    raise SystemExit(1)
print("PREFLIGHT OK")
"""


def run_canonical_preflight_seam(runtime_env: Path, ops: Path, seam_argv: list[str], env: dict[str, str]) -> None:
    """Execute the PINNED RUNTIME'S OWN ``operator._preflight()`` under its own
    venv interpreter -- the one narrow, isolated compatibility seam described
    in ``docs/wfc_operator_harness_v0_concept_2026-10-06.adoc`` S7/S15.

    Harness neither reimplements nor duplicates any of ``_preflight()``'s
    validation rules; it only constructs the same CLI argv an ordinary launch
    would use (``_parse_args``, unmodified, from the pinned Runtime itself) and
    asks the pinned Runtime's own code -- never Development's copy -- to
    validate them.  This performs no Run allocation, no STATE creation, no
    checkout registration, no adapter construction, and invokes no provider;
    ``_preflight()`` itself only reads governing inputs and resolves (never
    executes) evidence-command executables.
    """

    script = ops / "_preflight_seam.py"
    script.write_text(PREFLIGHT_SEAM_SCRIPT, encoding="utf-8")
    python = runtime_env / "bin" / "python3"
    result = subprocess.run((str(python), str(script), *seam_argv), capture_output=True, text=True, env=env)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise HarnessRefusal(f"canonical provider-free preflight refused: {detail}")


# --------------------------------------------------------- preparation record

def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_preparation_pins(
    ops: Path, *, attempt: str, state: Path,
    development: RepoPins, runtime: RepoPins, evidence: RepoPins,
    executable: str, timeout_seconds: str,
    work_item: str, profile: str, policy: str, components: str, task: str,
    execution_bindings: Path,
) -> Path:
    """Extends the ``attempt_id``/``state_root`` keys
    ``wfc-archive-run`` already reads with Development/Runtime/Evidence pins,
    rather than introducing a second, incompatible preparation-facts format.
    """

    lines = [
        f"attempt_id={attempt}",
        f"state_root={state}",
        f"development_path={development.path}",
        f"development_head={development.head}",
        f"runtime_path={runtime.path}",
        f"runtime_head={runtime.head}",
        f"evidence_path={evidence.path}",
        f"evidence_head={evidence.head}",
        f"executable={executable}",
        f"timeout_seconds={timeout_seconds}",
        f"work_item={work_item}",
        f"profile={profile}",
        f"policy={policy}",
        f"components={components}",
        f"task={task}",
        f"execution_bindings={execution_bindings}",
    ]
    path = ops / "preparation-pins.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_launch_sh(
    ops: Path, *, runtime_env: Path, development: Path,
    state: Path, expected_head: str, work_item: str, profile: str,
    policy: str, components: str, task: str, execution_bindings: Path,
    executable: str, timeout_seconds: str, controlled_path_value: str,
) -> Path:
    """Materialize the already-prepared launch artifact.

    The controlled PATH is frozen here as a literal value, not a `$PATH`
    reference: ``controlled_path_value`` is the exact string canonical
    preflight was admitted under during ``prepare`` (see ``cmd_prepare``), so
    a later ``launch`` -- from any shell, with any ambient PATH -- still
    executes the pinned Runtime under the identical environment preflight
    validated.  Every dynamic value is passed through ``shlex.quote`` so a
    path or executable string containing shell metacharacters cannot gain
    shell interpretation.
    """

    wfc = runtime_env / "bin" / "wfc"
    q = shlex.quote
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"export PATH={q(controlled_path_value)}",
        f"exec {q(str(wfc))} \\",
        f"  --repository {q(str(development))} \\",
        f"  --state-root {q(str(state))} \\",
        f"  --expected-head {q(expected_head)} \\",
        f"  --work-item {q(work_item)} \\",
        f"  --profile {q(profile)} \\",
        f"  --policy {q(policy)} \\",
        f"  --components {q(components)} \\",
        f"  --task {q(task)} \\",
        f"  --execution-bindings {q(str(execution_bindings))} \\",
        f"  --timeout-seconds {q(timeout_seconds)} \\",
        f"  --executable {q(executable)}",
        "",
    ]
    path = ops / "launch.sh"
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o755)
    return path


def write_harness_record(
    ops: Path, *, attempt: str, state: Path,
    development: RepoPins, runtime: RepoPins, evidence: RepoPins,
    launch_sh: Path, execution_bindings: Path,
    runtime_env: Path, dev_env: Path, controlled_path_value: str,
) -> Path:
    """One small Harness-owned mechanical record -- attempt/pins/paths/launch
    artifact digests and the chosen preflight substrate.  Records no semantic
    Run state; that remains WFC-owned, persisted separately under STATE.
    """

    record = {
        "schema_version": 1,
        "attempt": attempt,
        "ops": str(ops),
        "state_root": str(state),
        "development": {"path": str(development.path), "head": development.head},
        "runtime": {"path": str(runtime.path), "head": runtime.head},
        "evidence": {"path": str(evidence.path), "head": evidence.head},
        "runtime_env": str(runtime_env),
        "dev_env": str(dev_env),
        "controlled_path": controlled_path_value,
        "preflight_substrate": "live pinned Development checkout (self-development: no export)",
        "launch_sh_sha256": _digest(launch_sh),
        "execution_bindings_sha256": _digest(execution_bindings),
        "preflight_seam": "ok",
        "prepared": True,
    }
    path = ops / "harness-prepared.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------- prepare

def cmd_prepare(args: argparse.Namespace) -> int:
    attempt = validate_attempt(args.attempt)
    ops = ops_root(attempt)
    state = state_root(attempt)

    development = pin_development(Path(args.development).resolve())
    runtime = pin_runtime(Path(args.runtime).resolve())
    evidence = pin_evidence(Path(args.evidence).resolve())

    require_fresh_attempt(ops, state)

    for name, value in (
        ("work-item", args.work_item), ("profile", args.profile), ("policy", args.policy),
        ("components", args.components), ("task", args.task),
    ):
        require_governing_input(development.path, value, name=name)

    if args.timeout_seconds <= 0:
        raise HarnessRefusal("--timeout-seconds must be positive")

    ops.mkdir(parents=True)

    runtime_env = prepare_runtime_environment(runtime, ops)
    dev_env = prepare_development_environment(development, ops)

    base_path = os.environ.get("PATH", "")
    path_value = controlled_path(dev_env, runtime_env, base_path)
    verify_controlled_path(dev_env, path_value)

    execution_bindings = write_execution_bindings(ops, args.role_binding)

    seam_argv = [
        "--repository", str(development.path),
        "--state-root", str(state),
        "--expected-head", development.head,
        "--work-item", args.work_item,
        "--profile", args.profile,
        "--policy", args.policy,
        "--components", args.components,
        "--task", args.task,
        "--execution-bindings", str(execution_bindings),
        "--timeout-seconds", str(args.timeout_seconds),
        "--executable", args.executable,
    ]
    seam_env = {**os.environ, "PATH": path_value}
    run_canonical_preflight_seam(runtime_env, ops, seam_argv, seam_env)

    launch_sh = write_launch_sh(
        ops, runtime_env=runtime_env, development=development.path,
        state=state, expected_head=development.head, work_item=args.work_item,
        profile=args.profile, policy=args.policy, components=args.components,
        task=args.task, execution_bindings=execution_bindings, executable=args.executable,
        timeout_seconds=str(args.timeout_seconds), controlled_path_value=path_value,
    )
    write_preparation_pins(
        ops, attempt=attempt, state=state, development=development, runtime=runtime,
        evidence=evidence, executable=args.executable, timeout_seconds=str(args.timeout_seconds),
        work_item=args.work_item, profile=args.profile, policy=args.policy,
        components=args.components, task=args.task, execution_bindings=execution_bindings,
    )
    write_harness_record(
        ops, attempt=attempt, state=state, development=development, runtime=runtime,
        evidence=evidence, launch_sh=launch_sh, execution_bindings=execution_bindings,
        runtime_env=runtime_env, dev_env=dev_env, controlled_path_value=path_value,
    )

    if state.exists():
        raise HarnessRefusal("STATE unexpectedly materialized during preparation")

    print(f"OPS: {ops}")
    print(f"STATE: {state} (absent)")
    print("PREPARED / NOT LAUNCHED")
    return 0


# ----------------------------------------------------------------------- launch

def cmd_launch(args: argparse.Namespace) -> int:
    attempt = validate_attempt(args.attempt)
    ops = ops_root(attempt)
    state = state_root(attempt)

    record_path = ops / "harness-prepared.json"
    pins_path = ops / "preparation-pins.txt"
    if not ops.is_dir() or not record_path.is_file() or not pins_path.is_file():
        raise HarnessRefusal(f"attempt is not prepared (missing OPS/preparation records): {ops}")

    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise HarnessRefusal(f"harness-prepared.json is corrupt: {error}") from error
    if not record.get("prepared"):
        raise HarnessRefusal("harness-prepared.json does not record a completed preparation")

    if state.exists():
        raise HarnessRefusal(f"STATE already exists; refusing to launch: {state}")

    launch_sh = ops / "launch.sh"
    execution_bindings = ops / "execution-bindings.yaml"
    if not launch_sh.is_file():
        raise HarnessRefusal(f"prepared launch artifact is missing: {launch_sh}")
    if not execution_bindings.is_file():
        raise HarnessRefusal(f"prepared execution bindings are missing: {execution_bindings}")

    if _digest(launch_sh) != record.get("launch_sh_sha256"):
        raise HarnessRefusal("launch.sh differs from its prepared identity; refusing to launch a drifted artifact")
    if _digest(execution_bindings) != record.get("execution_bindings_sha256"):
        raise HarnessRefusal(
            "execution-bindings.yaml differs from its prepared identity; refusing to launch a drifted artifact"
        )

    _revalidate(record["development"], name="Development", pin_fn=pin_development)
    _revalidate(record["runtime"], name="Runtime", pin_fn=pin_runtime)
    _revalidate(record["evidence"], name="Evidence", pin_fn=pin_evidence)

    runtime_env = Path(record["runtime_env"])
    dev_env = Path(record["dev_env"])
    if not (runtime_env / "bin" / "wfc").is_file():
        raise HarnessRefusal(f"prepared Runtime executable is missing: {runtime_env / 'bin' / 'wfc'}")
    if not (dev_env / "bin" / "python3").is_file():
        raise HarnessRefusal(f"prepared development/evidence interpreter is missing: {dev_env}")

    result = subprocess.run(("bash", str(launch_sh)))
    print(f"LAUNCH RETURN CODE: {result.returncode}", file=sys.stderr)
    return result.returncode


# -------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wfc-op", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser(
        "prepare",
        help="deterministic PREPARE mechanics against an already maintainer-selected envelope; never launches",
    )
    prepare.add_argument("--attempt", required=True)
    prepare.add_argument("--development", required=True, help="Development repository path")
    prepare.add_argument("--runtime", required=True, help="Runtime repository path")
    prepare.add_argument("--evidence", required=True, help="Evidence repository path")
    prepare.add_argument("--work-item", required=True, help="Development-relative path")
    prepare.add_argument("--profile", required=True, help="Development-relative path")
    prepare.add_argument("--policy", required=True, help="Development-relative path")
    prepare.add_argument("--components", required=True, help="Development-relative path")
    prepare.add_argument("--task", required=True, help="Development-relative path")
    prepare.add_argument("--executable", required=True, help="provider executable; no default is invented")
    prepare.add_argument("--timeout-seconds", required=True, type=float)
    prepare.add_argument(
        "--role-binding", action="append", default=[], required=True,
        metavar="ROLE=ADAPTER:MODEL:REASONING",
        help="repeatable; one per profile role. No automatic selection.",
    )

    launch = sub.add_parser("launch", help="revalidate and execute the already-prepared launch artifact exactly once")
    launch.add_argument("attempt")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            return cmd_prepare(args)
        if args.command == "launch":
            return cmd_launch(args)
        parser.error("unknown command")
        return 2
    except HarnessRefusal as error:
        print(f"wfc-op: REFUSED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
