#!/usr/bin/env python3
"""wfc-op -- WFC Operator Harness v0 (complete: ``prepare`` / ``launch`` /
``status`` / ``archive``).

Deterministic, repository-owned replacement for the hand-authored PREPARE /
LAUNCH / STATUS / ARCHIVE shell blocks documented in
``docs/run-preparation.adoc`` and ``docs/self-development.adoc``.  See
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
pinned Runtime's own canonical provider-free preflight, materializing
``launch.sh``, and finally re-revalidating every pinned identity immediately
before declaring success -- and then stops.  It never launches.

``launch`` revalidates the prepared envelope against drift and, only then,
executes the already-materialized ``launch.sh`` exactly once, reporting its
stdout/stderr/return code faithfully.  It never repairs, retries, or
reconciles.

``status`` is a strictly read-only mechanical projection of whatever Harness
preparation facts and WFC-owned persisted Run state already exist on disk for
an attempt.  It never launches, retries, reconciles, or mutates anything, and
never reinterprets a semantic conclusion WFC itself did not already persist.

``archive`` is a deterministic, delegating wrapper around the canonical
``workflow-coordinator-run-evidence/scripts/wfc-archive-run`` mechanism: it
locates the prepared STATE/Evidence facts, performs mechanical prechecks, and
invokes the canonical wrapper's own ``--dry-run`` plan before any real
archive proceeds. It implements no copying, manifest, or archive-destination
logic of its own.

This module intentionally duplicates none of the pinned Runtime's own
admission/preflight semantics (see ``run_canonical_preflight_seam``) and none
of the canonical archiver's copy/manifest/commit semantics (see
``cmd_archive``).
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


# ------------------------------------------------------------ Commission seam

def _commissioning_api():
    """Load the optional source-tree compiler only for the new command group.

    Existing Harness commands remain stdlib-only when this script is invoked
    directly from a checkout, as their historical subprocess tests require.
    """

    source_root = str(Path(__file__).resolve().parents[1] / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    from workflow_coordinator.commissioning import (  # pylint: disable=import-outside-toplevel
        CommissionError, commission_intent_from_yaml, render_commission, verify_render_manifest,
    )
    return CommissionError, commission_intent_from_yaml, render_commission, verify_render_manifest

def _commission_target_dir(value: str | None, commission_id: str) -> str:
    """Keep rendered governing paths repository-relative and inspectable."""

    return value or f"governance/commissions/{commission_id}"


def _commission_report(rendered) -> str:
    """Compact provenance-labelled preview; this is not an authorization act."""

    intent = rendered.intent
    sources = rendered.manifest["sources"]
    lines = [
        f"user authority_source: {intent.authority_source}",
        f"user intent_sha256: {rendered.manifest['intent']['sha256']}",
        f"program id/revision/source_commit: {intent.program.id}/{intent.program.revision}/{intent.program.source_commit}",
        f"policy id/revision/source_commit: {intent.policy_basis.id}/{intent.policy_basis.revision}/{intent.policy_basis.source_commit}",
        f"user requested_scope: {', '.join(intent.scope)}",
        "runtime at PREPARE: expected clean bundle HEAD is checked by commission check, not recorded here",
    ]
    for phase, data in __import__("yaml").safe_load(rendered.texts[rendered.paths["profile"]])["phases"].items():
        review = data["review"] or {}
        lines.append(f"program phase {phase} scope: {', '.join(data['authorized_scope'])}")
        lines.append(f"program phase {phase} actions: {', '.join(data['authorized_actions'])}")
        lines.append(f"program phase {phase} evidence: {', '.join(data['evidence']) or '(none)'}")
        lines.append(f"program phase {phase} gate breadth/purpose/Finding authority: {review.get('breadth', '(none)')} / {review.get('purpose', '(none)')} / {review.get('finding_authority', [])}")
    lines.append("program required_artifacts: none")
    for entry in sources["selected_components"]:
        lines.append(f"program selected_component {entry['id']}: {entry['path']} sha256={entry['sha256']}")
    for role, persona in intent.personas.items():
        lines.append(f"user persona {role}: {persona}")
    for name, path in rendered.paths.items():
        digest = __import__("hashlib").sha256(rendered.texts[path].encode("utf-8")).hexdigest()
        lines.append(f"program rendered_{name}: {path} sha256={digest}")
    return "\n".join(lines)


def cmd_commission_render(args: argparse.Namespace) -> int:
    repository = Path.cwd().resolve()
    CommissionError, commission_intent_from_yaml, render_commission, _ = _commissioning_api()
    try:
        intent = commission_intent_from_yaml(Path(args.intent).read_text(encoding="utf-8"))
        target_dir = _commission_target_dir(args.out_dir, intent.commission_id)
        rendered = render_commission(intent, repository=repository, target_dir=target_dir)
        rendered.write_to(repository)
    except (OSError, CommissionError) as error:
        raise HarnessRefusal(str(error)) from error
    print(f"RENDERED COMMISSION: {intent.commission_id}")
    for path in rendered.paths.values():
        print(path)
    if args.check:
        print(_commission_report(rendered))
    return 0


def cmd_commission_check(args: argparse.Namespace) -> int:
    repository = Path(args.repository).resolve()
    CommissionError, commission_intent_from_yaml, _, verify_render_manifest = _commissioning_api()
    try:
        intent_path = str(Path(args.intent).resolve().relative_to(repository))
        intent = commission_intent_from_yaml((repository / intent_path).read_text(encoding="utf-8"))
        rendered = verify_render_manifest(
            intent_path, repository=repository, expected_head=args.expected_head,
            target_dir=_commission_target_dir(args.out_dir, intent.commission_id),
        )
    except (OSError, ValueError, CommissionError) as error:
        raise HarnessRefusal(f"Commission check refused: {error}") from error
    print(f"COMMISSION CHECK OK: {rendered.intent.commission_id} at {args.expected_head}")
    return 0


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


# ------------------------------------------------------- status / archive facts
#
# Shared, strictly read-only helpers for projecting already-persisted Harness
# preparation facts and WFC-owned Run state.  None of this reinterprets or
# resolves semantic meaning; disagreement between sources that each carry
# their own non-empty value for the same field is surfaced, never silently
# picked -- the same discipline `wfc-archive-run`'s own `agree()` helper
# already enforces (see
# ``workflow-coordinator-run-evidence/scripts/wfc-archive-run``).

_ABSENT = object()


def _read_json_optional(path: Path) -> tuple[dict | None, str | None]:
    """``(data, None)`` if absent or a parseable JSON object; ``(None, error)``
    if present but corrupt or not a JSON object.  Never raises.
    """

    if not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return None, f"{path}: corrupt JSON: {error}"
    if not isinstance(data, dict):
        return None, f"{path}: expected a JSON object, found {type(data).__name__}"
    return data, None


def _read_preparation_pins(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return values


def _first_run_json(state: Path) -> tuple[Path | None, dict | None, str | None]:
    """The first ``checkouts/<id>/run.json``, in sorted checkout-directory
    order -- the same location `wfc-archive-run`'s own `facts` probe reads.
    Current practice registers exactly one checkout per state root.
    """

    checkouts = state / "checkouts"
    if not checkouts.is_dir():
        return None, None, None
    for directory in sorted(p for p in checkouts.iterdir() if p.is_dir()):
        candidate = directory / "run.json"
        if candidate.is_file():
            data, error = _read_json_optional(candidate)
            return candidate, data, error
    return None, None, None


def _agree(field: str, *labeled_sources: tuple[str, dict | None]) -> tuple[str | None, list[str]]:
    """First non-empty string value for ``field`` across labeled sources, in
    priority order; a list of mechanical-inconsistency messages if two
    sources that each carry their own non-empty value disagree.
    """

    found = [
        (label, source[field].strip())
        for label, source in labeled_sources
        if source is not None and isinstance(source.get(field), str) and source.get(field, "").strip()
    ]
    distinct = sorted({value for _, value in found})
    if len(distinct) > 1:
        detail = ", ".join(f"{label}={value!r}" for label, value in found)
        return None, [f"{field} disagrees across persisted sources: {detail}"]
    return (found[0][1] if found else None), []


def _first_present(key: str, *dicts: dict | None) -> object:
    """The value of ``key`` from the first dict (in priority order) that
    actually has it -- including an explicitly persisted ``null`` -- never a
    guess, and distinguishable from "no source carries this fact" via
    ``_ABSENT``.
    """

    for d in dicts:
        if d is not None and key in d:
            return d[key]
    return _ABSENT


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

    # Final repository revalidation, immediately before declaring PREPARED --
    # the smallest equivalent of the old manual preparation flow's closing
    # verification step.  Reuses the exact same fail-closed helper `launch`
    # already uses against drift between `prepare` and `launch`; drift that
    # occurred *during* preparation itself (e.g. a concurrent push/commit
    # into Development/Runtime/Evidence while venvs were being built) must
    # equally never be reported as PREPARED.
    _revalidate({"path": str(development.path), "head": development.head}, name="Development", pin_fn=pin_development)
    _revalidate({"path": str(runtime.path), "head": runtime.head}, name="Runtime", pin_fn=pin_runtime)
    _revalidate({"path": str(evidence.path), "head": evidence.head}, name="Evidence", pin_fn=pin_evidence)

    if state.exists():
        raise HarnessRefusal("STATE unexpectedly materialized during preparation")
    for artifact in (
        ops / "preparation-pins.txt", ops / "harness-prepared.json", launch_sh, execution_bindings,
    ):
        if not artifact.is_file():
            raise HarnessRefusal(f"expected prepared artifact is missing at final revalidation: {artifact}")

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


# ----------------------------------------------------------------------- status

def _render_text(value: object) -> str:
    if value is _ABSENT:
        return "(not persisted)"
    return repr(value) if isinstance(value, (list, dict)) else str(value)


def cmd_status(args: argparse.Namespace) -> int:
    """Strictly read-only.  Never creates, mutates, retries, reconciles, or
    launches anything -- only projects already-persisted Harness preparation
    facts and WFC-owned Run state.  Always exits 0: an unknown attempt,
    missing preparation, or a mechanical inconsistency are reportable facts,
    not failures of this command itself.
    """

    attempt = validate_attempt(args.attempt)
    ops = ops_root(attempt)
    state = state_root(attempt)

    report: dict[str, object] = {"attempt": attempt, "ops": str(ops), "state": str(state)}

    if not ops.exists() and not state.exists():
        report["lifecycle"] = "UNKNOWN_ATTEMPT"
        _print_status(report, as_json=args.json)
        return 0

    prep_record, prep_record_error = _read_json_optional(ops / "harness-prepared.json")
    prep_pins = _read_preparation_pins(ops / "preparation-pins.txt")
    corrupt: list[str] = []
    if prep_record_error:
        corrupt.append(prep_record_error)

    report["preparation_present"] = bool(prep_record or prep_pins)
    pinned_development = (prep_pins or {}).get("development_head") or (
        (prep_record or {}).get("development") or {}
    ).get("head")
    pinned_runtime = (prep_pins or {}).get("runtime_head") or ((prep_record or {}).get("runtime") or {}).get("head")
    pinned_evidence = (prep_pins or {}).get("evidence_head") or ((prep_record or {}).get("evidence") or {}).get("head")
    if pinned_development:
        report["prepared_development_head"] = pinned_development
    if pinned_runtime:
        report["prepared_runtime_head"] = pinned_runtime
    if pinned_evidence:
        report["prepared_evidence_head"] = pinned_evidence

    if not state.exists():
        report["state_present"] = False
        if ops.exists() and not (prep_record or prep_pins):
            report["lifecycle"] = "PREPARATION_INCOMPLETE_OR_UNREADABLE"
        else:
            report["lifecycle"] = "PREPARED_NOT_LAUNCHED"
        if corrupt:
            report["corrupt_sources"] = corrupt
        _print_status(report, as_json=args.json)
        return 0

    report["state_present"] = True

    plan, plan_error = _read_json_optional(state / "operator-plan.json")
    summary, summary_error = _read_json_optional(state / "operator-summary.json")
    run_json_path, run_json, run_json_error = _first_run_json(state)
    assoc, assoc_error = _read_json_optional(state / "checkout-association.json")

    for label, error in (
        ("operator-plan.json", plan_error), ("operator-summary.json", summary_error),
        (str(run_json_path) if run_json_path else "run.json", run_json_error),
        ("checkout-association.json", assoc_error),
    ):
        if error:
            corrupt.append(error)
    if corrupt:
        report["corrupt_sources"] = corrupt

    issues: list[str] = []
    run_id, run_id_issues = _agree(
        "run_id", ("operator-summary.json", summary), ("operator-plan.json", plan), ("run.json", run_json),
    )
    work_item_id, wi_issues = _agree(
        "work_item_id",
        ("operator-summary.json", summary), ("operator-plan.json", plan), ("run.json", run_json),
    )
    checkout_id, checkout_issues = _agree(
        "checkout_id", ("operator-summary.json", summary), ("checkout-association.json", assoc),
    )
    issues += run_id_issues + wi_issues + checkout_issues
    if issues:
        report["inconsistencies"] = issues

    def emit(name: str, key: str, *dicts: dict | None) -> None:
        value = _first_present(key, *dicts)
        if value is not _ABSENT:
            report[name] = value

    if run_id is not None:
        report["run_id"] = run_id
    if work_item_id is not None:
        report["work_item_id"] = work_item_id
    if checkout_id is not None:
        report["checkout_id"] = checkout_id

    # `run_status` lives under different keys in the two artifacts
    # (`operator-summary.json["run_status"]` vs. the canonical
    # `run.json["status"]`) -- the exact distinction `wfc-archive-run`'s own
    # `facts` probe preserves rather than merging.
    run_status = _first_present("run_status", summary)
    if run_status is _ABSENT:
        run_status = _first_present("status", run_json)
    if run_status is not _ABSENT:
        report["run_status"] = run_status

    emit("current_phase", "phase", run_json)
    emit("hop_used", "hop_used", summary, run_json)
    emit("hop_limit", "hop_limit", summary, run_json)
    emit("stop_reason", "stop_reason", summary, run_json)
    emit("human_question", "human_question", summary, run_json)
    emit("operator_status", "operator_status", summary)
    emit("operator_error", "operator_error", summary)
    emit("final_head", "final_head", summary)
    emit("worktree_clean", "worktree_clean", summary)
    emit("anomalies", "anomalies", summary, run_json)

    if not (summary or plan or run_json):
        report["lifecycle"] = "STATE_PRESENT_NO_RECOGNIZABLE_WFC_STATE"
    else:
        report["lifecycle"] = "RUN_RECORDED"

    _print_status(report, as_json=args.json)
    return 0


_STATUS_FIELD_ORDER = (
    "attempt", "ops", "state", "lifecycle",
    "preparation_present", "prepared_development_head", "prepared_runtime_head", "prepared_evidence_head",
    "state_present", "run_id", "work_item_id", "checkout_id", "run_status", "current_phase",
    "hop_used", "hop_limit", "stop_reason", "human_question", "operator_status", "operator_error",
    "final_head", "worktree_clean", "anomalies", "inconsistencies", "corrupt_sources",
)


def _print_status(report: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    for key in _STATUS_FIELD_ORDER:
        if key in report:
            print(f"{key}: {_render_text(report[key])}")


# ---------------------------------------------------------------------- archive

def _resolve_recorded_evidence_path(ops: Path) -> Path:
    """The Evidence repository path recorded during `prepare` -- never a
    user-supplied override (the CLI surface takes none).  Refuses rather than
    guesses if the two preparation records disagree.
    """

    pins = _read_preparation_pins(ops / "preparation-pins.txt")
    record, record_error = _read_json_optional(ops / "harness-prepared.json")
    if record_error:
        raise HarnessRefusal(f"harness-prepared.json is corrupt: {record_error}")

    pins_path = (pins or {}).get("evidence_path") or None
    record_path = None
    if record and isinstance(record.get("evidence"), dict):
        value = record["evidence"].get("path")
        record_path = value if isinstance(value, str) and value else None

    if not pins_path and not record_path:
        raise HarnessRefusal(
            f"cannot derive Evidence repository: no preparation record found for attempt (checked {ops})"
        )
    if pins_path and record_path and Path(pins_path).resolve() != Path(record_path).resolve():
        raise HarnessRefusal(
            "preparation facts disagree on Evidence repository path: "
            f"preparation-pins.txt={pins_path!r} harness-prepared.json={record_path!r}"
        )
    return Path(pins_path or record_path)


_REPORT_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z ]*):\s+(.*)$")


def _parse_wrapper_report(stdout: str) -> dict[str, str]:
    """Thin extraction of the canonical wrapper's OWN already-printed labeled
    report lines (e.g. ``dest:       archive/unclassified/<attempt>``) -- not
    a reimplementation of how it derives them.
    """

    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        match = _REPORT_LINE_RE.match(line)
        if match:
            key = match.group(1).strip().lower().replace(" ", "_")
            fields[key] = match.group(2).strip()
    return fields


def _invoke_canonical_archive(
    wrapper: Path, state: Path, evidence: Path, *, dry_run: bool, semantic_json: Path | None,
) -> subprocess.CompletedProcess:
    argv = [str(wrapper), str(state), str(evidence)]
    if semantic_json is not None:
        argv += ["--semantic-json", str(semantic_json)]
    if dry_run:
        argv.append("--dry-run")
    return subprocess.run(argv, capture_output=True, text=True)


def cmd_archive(args: argparse.Namespace) -> int:
    """Deterministic, delegating wrapper around the canonical
    ``workflow-coordinator-run-evidence/scripts/wfc-archive-run``.  Harness
    derives only the wrapper's two positional arguments (state root, Evidence
    repository) from preparation facts, performs mechanical prechecks, and
    always runs the wrapper's own ``--dry-run`` plan before any real archive
    proceeds.  It implements no copying, hashing, manifest, or
    fact-extraction logic of its own -- that remains entirely owned by the
    canonical wrapper and `scripts/archive_run.py` in the Evidence repository.
    """

    attempt = validate_attempt(args.attempt)
    ops = ops_root(attempt)
    state = state_root(attempt)

    if not state.is_dir():
        raise HarnessRefusal(f"STATE is absent; nothing to archive: {state}")
    if not ops.is_dir():
        raise HarnessRefusal(f"OPS directory not found; cannot derive Evidence repository automatically: {ops}")

    evidence_path = _resolve_recorded_evidence_path(ops)
    evidence = _require_git_toplevel(evidence_path, name="Evidence")
    if not _is_clean(evidence):
        raise HarnessRefusal(f"Evidence worktree is not clean; refusing to archive: {evidence}")

    wrapper = evidence / "scripts" / "wfc-archive-run"
    if not wrapper.is_file() or not os.access(wrapper, os.X_OK):
        raise HarnessRefusal(f"canonical archive wrapper is missing or not executable: {wrapper}")

    semantic_json = Path(args.semantic_json).resolve() if args.semantic_json else None

    head_before = _git(evidence, "rev-parse", "HEAD")

    dry = _invoke_canonical_archive(wrapper, state, evidence, dry_run=True, semantic_json=semantic_json)
    if dry.returncode != 0:
        detail = (dry.stderr or dry.stdout).strip()
        raise HarnessRefusal(f"canonical archive dry-run refused: {detail}")
    dry_fields = _parse_wrapper_report(dry.stdout)

    print(dry.stdout)
    print(f"ATTEMPT: {attempt}")
    print(f"EVIDENCE: {evidence}")
    print(f"EVIDENCE HEAD (before): {head_before}")

    if args.dry_run:
        print("ARCHIVE DRY-RUN ONLY: no real archive performed")
        return 0

    real = _invoke_canonical_archive(wrapper, state, evidence, dry_run=False, semantic_json=semantic_json)
    print(real.stdout)
    if real.returncode != 0:
        detail = (real.stderr or real.stdout).strip()
        raise HarnessRefusal(f"canonical archive failed: {detail}")
    real_fields = _parse_wrapper_report(real.stdout)

    head_after = _git(evidence, "rev-parse", "HEAD")
    destination = real_fields.get("dest") or dry_fields.get("dest") or "(unknown; not reported by wrapper)"
    commit = real_fields.get("commit") or head_after

    print(f"EVIDENCE HEAD (after): {head_after}")
    print(f"ARCHIVE DESTINATION: {destination}")
    print(f"ARCHIVE COMMIT: {commit}")
    print("ARCHIVED")
    return 0


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

    status = sub.add_parser(
        "status",
        help="strictly read-only mechanical projection of persisted Harness/WFC facts for an attempt",
    )
    status.add_argument("attempt")
    status.add_argument(
        "--json", action="store_true",
        help="emit a small JSON projection of persisted facts instead of human-readable text",
    )

    archive = sub.add_parser(
        "archive",
        help="delegate to the canonical Evidence archive wrapper (dry-run, then real archive)",
    )
    archive.add_argument("attempt")
    archive.add_argument("--semantic-json", help="passed verbatim to wfc-archive-run")
    archive.add_argument(
        "--dry-run", action="store_true",
        help="invoke only the canonical wrapper's dry-run plan; perform no real archive",
    )

    commission = sub.add_parser(
        "commission",
        help="non-authoritative closed-program rendering and provider-free projection checking",
    )
    commission_sub = commission.add_subparsers(dest="commission_command", required=True)
    commission_render = commission_sub.add_parser("render", help="render a direct@v1 Commission bundle")
    commission_render.add_argument("--intent", required=True, help="Commission Intent YAML")
    commission_render.add_argument("--out-dir", help="repository-relative rendered bundle directory")
    commission_render.add_argument("--check", action="store_true", help="print the provenance-labelled render report")
    commission_check = commission_sub.add_parser(
        "check", help="rerender committed inputs, compare bytes, then call canonical operator preflight",
    )
    commission_check.add_argument("--intent", required=True, help="tracked Commission Intent YAML")
    commission_check.add_argument("--repository", required=True, help="repository containing the committed bundle")
    commission_check.add_argument("--expected-head", required=True, help="clean 40-character bundle HEAD")
    commission_check.add_argument("--out-dir", help="repository-relative rendered bundle directory")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            return cmd_prepare(args)
        if args.command == "launch":
            return cmd_launch(args)
        if args.command == "status":
            return cmd_status(args)
        if args.command == "archive":
            return cmd_archive(args)
        if args.command == "commission":
            if args.commission_command == "render":
                return cmd_commission_render(args)
            if args.commission_command == "check":
                return cmd_commission_check(args)
        parser.error("unknown command")
        return 2
    except HarnessRefusal as error:
        print(f"wfc-op: REFUSED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
