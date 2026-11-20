"""BP4a installed lifecycle controller: ``wfc run`` / ``wfc resume`` / ``wfc status``.

A thin, restartable driver *above* the unchanged engine (``operator.py`` via
its existing bare-flag entry point). This module never duplicates reducer,
Finding, review, routing, adapter, admission (``operator.admit_parsed``) or
``trusted_commit``/``bounded_path_commit`` logic; it only orchestrates the
existing BP2b/BP3 compiler and admission seams, launches the existing engine
as a separate process, and classifies its persisted facts.

Lifecycle order, each step journaled write-ahead (an "intent" record fsynced
before the step's real effect, a completion record after): authorize both
digests once; pre-launch checks; a one-shot baseline probe; the Commission
commit (``git_facts.bounded_path_commit``); ``frontdoor.verify.verify_commission``;
engine launch; wait; classification. ``run`` performs the first authorization;
``resume`` continues an already-authorized checkout purely from journal and
filesystem facts -- it never re-prompts, never recommits, and never resumes
the engine's own Run/Hop/session.

Exactly-once launch cannot always be proven from a dead controller's remains.
Per maintainer decision DG-3 (2026-11-19, Variant B), this module refuses
honestly instead of inventing a guarantee: an unresolved ``launch_intent`` is
always ``uncertain_launch`` and is never launched past, and a free ownership
lock never implies "safe to relaunch" once any launch attempt occurred.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from ..commissioning import CommissionError, compile_commission
from ..git_facts import (
    GitPreflightError,
    NewPathCommitIntent,
    TrustedCommitError,
    bounded_path_commit,
    observe_repository,
    repository_head,
    repository_identity,
)
from ..implementation_identity import implementation_identity
from ..model import RunStatus
from ..serialization import load_run, run_execution_configuration_from_yaml
from .config import ExecutionEnvelope, consumer_config_from_yaml, resolve_execution
from . import postrun, preservation
from .journal import (
    CanonicalCheckout,
    Journal,
    JournalError,
    JournalRecord,
    canonical_checkout,
    checkout_directory,
    query_lock_held,
    release_lock,
    require_disjoint_journal_root,
    state_home,
    try_acquire_lock,
)
from .request import consumer_request_from_yaml
from .verify import verify_commission

DEFAULT_CONFIG_PATH = ".wfc/config.yaml"
DEFAULT_EXECUTABLE = "codex"
DEFAULT_HOP_TIMEOUT_SECONDS = 900.0
MAX_LAUNCH_ATTEMPTS = 3


def _default_engine_command() -> tuple[str, ...]:
    """The real, installed ``wfc`` console-script entry point.

    ``frontdoor.cli`` has no ``__main__`` guard (it is a library module
    dispatched by that installed script, not a runnable ``-m`` target), so
    the legacy bare-flag path can only be reached by invoking the actual
    installed ``wfc`` script -- the same one documented in
    ``pyproject.toml``'s ``[project.scripts]`` -- found next to the current
    interpreter.
    """

    sibling = Path(sys.executable).parent / "wfc"
    if sibling.is_file():
        return (str(sibling),)
    found = shutil.which("wfc")
    if found:
        return (found,)
    raise RuntimeError("the installed `wfc` console script could not be located")


# ``None`` resolves lazily (at launch time) to the real installed engine via
# ``_default_engine_command``. Tests may set this to a fake engine fixture's
# argv prefix; production code never does.
_ENGINE_COMMAND: tuple[str, ...] | None = None


def _resolve_engine_command() -> tuple[str, ...]:
    return _ENGINE_COMMAND if _ENGINE_COMMAND is not None else _default_engine_command()


EXIT_CODES: dict[str, int] = {
    "completed": 0,
    "awaiting_human": 10,
    "awaiting_human_budget": 11,
    "reconcile_required": 12,
    "stopped": 13,
    "interrupted": 14,
    "engine_active": 15,
    "engine_exited_before_state": 16,
    "uncertain_launch": 17,
    "inconsistent": 18,
    "launch_failed_no_engine": 19,
    "refused_unauthorized": 20,
    "refused_drift": 21,
    "refused_prelaunch": 22,
    "refused_probe": 23,
    "refused_identity_changed": 24,
    "refused_launch_exhausted": 25,
    "refused_active_lifecycle": 26,
    "refused_journal_root": 27,
    "refused_no_lifecycle": 28,
    "not_launched": 29,
    "lifecycle_active": 30,
    "no_lifecycle": 31,
}

# BP6c (additive; every value in EXIT_CODES above is frozen and unchanged):
# the post-classification archive/audit/report step itself refused or
# failed. This is never a ``LifecycleResult.classification`` value -- the
# engine outcome's own classification is reported unchanged, so this stays
# outside EXIT_CODES's classification-keyed domain (which `wfc report`'s
# fixed next-choice table is pinned against, one line per real
# classification). It only ever replaces the *exit code* a caller sees, via
# ``LifecycleResult.postrun_exit_override``.
ARCHIVE_FAILED_EXIT_CODE = 32

_TERMINAL_CLASSIFICATIONS = frozenset({
    "completed", "awaiting_human", "awaiting_human_budget", "reconcile_required",
    "stopped", "interrupted", "engine_exited_before_state",
})


class LifecycleError(RuntimeError):
    """A truthful, step-bounded refusal; never a fabricated guarantee."""

    def __init__(self, message: str, *, classification: str):
        super().__init__(message)
        self.classification = classification


@dataclass(frozen=True)
class LifecycleResult:
    classification: str
    message: str
    run_id: str | None = None
    run_status: str | None = None
    end_head: str | None = None
    state_root: str | None = None
    checkout_state_root: str | None = None
    # BP6c additions: never set by anything upstream of the post-run step,
    # so every existing construction of this dataclass is unaffected.
    postrun_exit_override: int | None = None
    postrun_notice: str | None = None
    postrun_report_text: str | None = None

    @property
    def exit_code(self) -> int:
        if self.postrun_exit_override is not None:
            return self.postrun_exit_override
        return EXIT_CODES.get(self.classification, 1)


@dataclass(frozen=True)
class RunInputs:
    repository: Path
    config_path: str
    request_path: Path
    target_dir: str | None
    overrides: Mapping[str, Mapping[str, str]]
    executable: str
    hop_timeout_seconds: float
    confirm: Callable[[str, str], bool]


def _fsync_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(descriptor, text.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(("git", "-C", str(repository), *args), text=True, capture_output=True)
    if completed.returncode:
        raise GitPreflightError(completed.stderr.strip() or "Git fact lookup failed")
    return completed.stdout.strip()


def _git_succeeds(repository: Path, *args: str) -> bool:
    return subprocess.run(
        ("git", "-C", str(repository), *args), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


def _git_blob_text(repository: Path, revision: str, path: str) -> str:
    """Exact blob text at ``revision:path``, with no trailing-whitespace strip."""

    completed = subprocess.run(
        ("git", "-C", str(repository), "show", f"{revision}:{path}"), text=True, capture_output=True,
    )
    if completed.returncode:
        raise GitPreflightError(completed.stderr.strip() or "Git fact lookup failed")
    return completed.stdout


def _materials_digest(texts: Mapping[str, str], request_file_path: str, request_raw_text: str) -> str:
    materials = dict(texts)
    materials[request_file_path] = request_raw_text
    per_path = {path: hashlib.sha256(text.encode("utf-8")).hexdigest() for path, text in materials.items()}
    return hashlib.sha256(json.dumps(per_path, sort_keys=True).encode("utf-8")).hexdigest()


def _compile(
    repository: Path, config_text: str, request_text: str, *, target_dir: str | None,
    overrides: Mapping[str, Mapping[str, str]],
) -> tuple[Any, Any, Any, ExecutionEnvelope, str, str]:
    config = consumer_config_from_yaml(config_text)
    request = consumer_request_from_yaml(request_text)
    compiled = compile_commission(request, config, repository=repository, target_dir=target_dir)
    envelope = resolve_execution(config, overrides, tuple(sorted(request.personas)))
    request_file_path = f"{compiled.target_dir}/request.yaml"
    digest = _materials_digest(compiled.texts, request_file_path, request.raw_text)
    return request, config, compiled, envelope, request_file_path, digest


def _cached_materials(repository: Path, checkout_dir: Path, authorized: JournalRecord):
    commission_dir = checkout_dir / "commission"
    request_text = (commission_dir / "request.yaml").read_text(encoding="utf-8")
    config_text = (commission_dir / "config.yaml").read_text(encoding="utf-8")
    envelope_text = (commission_dir / "envelope.yaml").read_text(encoding="utf-8")
    config = consumer_config_from_yaml(config_text)
    request = consumer_request_from_yaml(request_text)
    compiled = compile_commission(request, config, repository=repository, target_dir=authorized.data["target_dir"])
    return request, config, compiled, envelope_text


# --------------------------------------------------------------------------
# Step 1: authorization (``run`` only).
# --------------------------------------------------------------------------

def _authorize(inputs: RunInputs, checkout_dir: Path, journal: Journal) -> tuple[JournalRecord, Any]:
    request_text = inputs.request_path.read_text(encoding="utf-8")
    config_text = (inputs.repository / inputs.config_path).read_text(encoding="utf-8")
    _, _, compiled, envelope, request_file_path, commission_digest = _compile(
        inputs.repository, config_text, request_text, target_dir=inputs.target_dir, overrides=inputs.overrides,
    )
    if not inputs.confirm(commission_digest, envelope.digest):
        raise LifecycleError("authorization refused", classification="refused_unauthorized")

    # Re-derive both digests once more right after authorization is granted:
    # a real gap (an interactive prompt, or a scripted caller racing an edit)
    # must never let a human approve one Commission and silently launch a
    # different one.
    request_text_2 = inputs.request_path.read_text(encoding="utf-8")
    config_text_2 = (inputs.repository / inputs.config_path).read_text(encoding="utf-8")
    request, config, compiled_2, envelope_2, request_file_path_2, commission_digest_2 = _compile(
        inputs.repository, config_text_2, request_text_2, target_dir=inputs.target_dir, overrides=inputs.overrides,
    )
    if commission_digest_2 != commission_digest or envelope_2.digest != envelope.digest:
        raise LifecycleError(
            "commission digest drifted between plan and authorization", classification="refused_drift",
        )

    lock = try_acquire_lock(checkout_dir)
    if lock is None:
        raise LifecycleError(
            "active lifecycle already running for this checkout", classification="refused_prelaunch",
        )
    try:
        base_head = repository_head(inputs.repository)
        commission_dir = checkout_dir / "commission"
        _fsync_write_text(commission_dir / "request.yaml", request_text_2)
        _fsync_write_text(commission_dir / "config.yaml", config_text_2)
        _fsync_write_text(commission_dir / "envelope.yaml", envelope_2.text)
        journal.append("authorize_intent", {
            "commission_digest": commission_digest_2, "envelope_digest": envelope_2.digest,
        })
        record = journal.append("authorized", {
            "commission_digest": commission_digest_2, "envelope_digest": envelope_2.digest,
            "target_dir": compiled_2.target_dir, "overrides": dict(inputs.overrides),
            "request_file_path": request_file_path_2, "config_path": inputs.config_path,
            "commission_id": request.commission_id, "work_item_id": request.work_item_id,
            "executable_argument": inputs.executable, "hop_timeout_seconds": inputs.hop_timeout_seconds,
            "base_head": base_head,
            "engine_state_root": str(checkout_dir / "engine-state"),
        })
    except Exception:
        release_lock(lock)
        raise
    return record, lock


# --------------------------------------------------------------------------
# Step 2: pre-launch checks.
# --------------------------------------------------------------------------

def _resolve_executable(executable: str) -> str:
    if os.path.isabs(executable) or os.sep in executable:
        return str(Path(executable).resolve())
    found = shutil.which(executable)
    if found is None:
        raise LifecycleError(f"provider executable is not resolvable on PATH: {executable}", classification="refused_prelaunch")
    return found


def _do_prelaunch(repository: Path, checkout_dir: Path, journal: Journal, authorized: JournalRecord) -> JournalRecord:
    journal.append("prelaunch_intent", {})

    executable_path = _resolve_executable(authorized.data["executable_argument"])
    if not os.path.isfile(executable_path) or not os.access(executable_path, os.X_OK):
        raise LifecycleError(
            f"provider executable does not exist or is not executable: {executable_path}",
            classification="refused_prelaunch",
        )
    executable_sha256 = hashlib.sha256(Path(executable_path).read_bytes()).hexdigest()
    package_identity = implementation_identity()

    checkout = canonical_checkout(repository)
    require_disjoint_journal_root(state_home(), checkout)

    facts = observe_repository(repository)
    if facts.head != authorized.data["base_head"]:
        raise LifecycleError("repository HEAD moved since authorization", classification="refused_prelaunch")
    if not facts.clean or facts.unresolved_submodules:
        raise LifecycleError("repository worktree is not clean at the authorized baseline", classification="refused_prelaunch")

    engine_state_root = Path(authorized.data["engine_state_root"])
    if engine_state_root.exists():
        raise LifecycleError("engine state root already exists", classification="refused_prelaunch")

    return journal.append("prelaunch_passed", {
        "executable_path": executable_path, "executable_sha256": executable_sha256,
        "package_identity": package_identity, "head": facts.head,
        "engine_state_root": str(engine_state_root),
    })


def _verify_identities_unchanged(prelaunch: JournalRecord) -> None:
    current_identity = implementation_identity()
    if prelaunch.data["package_identity"] != current_identity:
        raise LifecycleError(
            "package implementation identity changed since authorization", classification="refused_identity_changed",
        )
    executable_path = prelaunch.data["executable_path"]
    try:
        digest = hashlib.sha256(Path(executable_path).read_bytes()).hexdigest()
    except OSError as error:
        raise LifecycleError(
            f"provider executable is no longer available: {error}", classification="refused_prelaunch",
        ) from error
    if digest != prelaunch.data["executable_sha256"]:
        raise LifecycleError(
            "provider executable changed since authorization", classification="refused_identity_changed",
        )


# --------------------------------------------------------------------------
# Step 3: the one-shot baseline probe.
# --------------------------------------------------------------------------

def _do_probe(repository: Path, checkout_dir: Path, journal: Journal, authorized: JournalRecord) -> JournalRecord:
    journal.append("probe_intent", {})

    facts_before = observe_repository(repository)
    if facts_before.head != authorized.data["base_head"] or not facts_before.clean or facts_before.unresolved_submodules:
        raise LifecycleError("repository is not at the clean authorized baseline", classification="refused_prelaunch")

    request_text = (checkout_dir / "commission" / "request.yaml").read_text(encoding="utf-8")
    request = consumer_request_from_yaml(request_text)
    argv = list(request.evidence.argv)
    try:
        completed = subprocess.run(
            argv, cwd=str(repository), timeout=request.evidence.timeout_seconds, capture_output=True,
        )
    except subprocess.TimeoutExpired as error:
        raise LifecycleError(f"baseline probe timed out; no provider ran: {error}", classification="refused_probe") from error
    except OSError as error:
        raise LifecycleError(f"baseline probe executable is unresolvable; no provider ran: {error}", classification="refused_probe") from error

    facts_after = observe_repository(repository)
    if completed.returncode != 0:
        raise LifecycleError(
            f"baseline probe failed with exit code {completed.returncode}; no provider ran",
            classification="refused_probe",
        )
    if facts_after.head != facts_before.head or not facts_after.clean or facts_after.unresolved_submodules:
        changed = sorted({*facts_after.tracked_changes, *facts_after.untracked_changes})
        raise LifecycleError(
            f"baseline probe mutated the repository; no provider ran (changed: {', '.join(changed) or 'HEAD moved'})",
            classification="refused_probe",
        )
    return journal.append("probe_passed", {})


# --------------------------------------------------------------------------
# Step 4: the Commission commit.
# --------------------------------------------------------------------------

def _try_adopt_commit(
    repository: Path, base_head: str, expected_paths: tuple[str, ...], expected_subject: str,
    expected_texts: Mapping[str, str],
) -> str | None:
    head = _git(repository, "rev-parse", "HEAD")
    if head == base_head:
        return None
    if not _git_succeeds(repository, "rev-parse", f"{head}^"):
        return None
    parent = _git(repository, "rev-parse", f"{head}^")
    if parent != base_head:
        return None
    subject = _git(repository, "show", "-s", "--format=%s", head)
    if subject != expected_subject:
        return None
    changed = sorted(filter(None, _git(repository, "diff", "--no-renames", "--name-only", f"{base_head}..{head}").splitlines()))
    if changed != sorted(expected_paths):
        return None
    for path in expected_paths:
        if not _git_succeeds(repository, "cat-file", "-e", f"{head}:{path}"):
            return None
        actual = _git_blob_text(repository, head, path)
        if actual != expected_texts[path]:
            return None
    return head


def _materialize_commission(repository: Path, expected_texts: Mapping[str, str]) -> None:
    """Write the compiled Commission plus the request file, tolerating exact-
    content leftovers from a crash between materialization and the commit
    call on a prior attempt at this same step.  Any other pre-existing
    content at a destination path is a contradiction, not a resumable crash.
    """

    for path, text in expected_texts.items():
        destination = repository / path
        if destination.exists():
            if destination.read_text(encoding="utf-8") != text:
                raise LifecycleError(
                    f"unexpected content already present at {path}; refusing to overwrite",
                    classification="inconsistent",
                )
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")


def _reconcile_stray_staged_state(repository: Path, base_head: str, expected_texts: Mapping[str, str]) -> None:
    """Undo index leftovers from a crash inside a prior attempt at this exact
    step, after that attempt's own ``git add`` (inside ``bounded_path_commit``)
    but before its ``git commit``. ``bounded_path_commit`` itself is never
    modified or re-entered here; this only restores the clean index it
    requires before staging again. Reconciliation proceeds only when every
    currently staged path is one of this lifecycle's own expected new leaves,
    absent at ``base_head``, with byte-identical staged content; anything
    else staged is a contradiction, refused by name with no auto-clean.
    """

    staged = sorted(filter(None, _git(repository, "diff", "--no-ext-diff", "--cached", "--name-only").splitlines()))
    if not staged:
        return
    unexpected = sorted(set(staged) - set(expected_texts))
    if unexpected:
        raise LifecycleError(
            "unexpected staged state blocks the Commission commit: " + ", ".join(unexpected),
            classification="inconsistent",
        )
    for path in staged:
        if _git_succeeds(repository, "cat-file", "-e", f"{base_head}:{path}"):
            raise LifecycleError(
                f"staged path already exists at base HEAD, refusing to reconcile: {path}",
                classification="inconsistent",
            )
        if _git_blob_text(repository, "", path) != expected_texts[path]:
            raise LifecycleError(
                f"staged content does not match the expected Commission text, refusing to reconcile: {path}",
                classification="inconsistent",
            )
    _git(repository, "reset", "-q", "HEAD", "--", *staged)


def _do_commit(repository: Path, checkout_dir: Path, journal: Journal, authorized: JournalRecord) -> JournalRecord:
    request_text = (checkout_dir / "commission" / "request.yaml").read_text(encoding="utf-8")
    config_text = (checkout_dir / "commission" / "config.yaml").read_text(encoding="utf-8")
    request, config, compiled, _, request_file_path, digest = _compile(
        repository, config_text, request_text, target_dir=authorized.data["target_dir"],
        overrides=authorized.data["overrides"],
    )
    if digest != authorized.data["commission_digest"]:
        raise LifecycleError(
            "commission digest changed since authorization; refusing to commit a different Commission",
            classification="refused_drift",
        )

    base_head = authorized.data["base_head"]
    all_paths = tuple(sorted({*compiled.texts.keys(), request_file_path}))
    subject = f"feat(commission): commission {request.commission_id} for {request.work_item_id}"
    expected_texts = dict(compiled.texts)
    expected_texts[request_file_path] = request_text

    journal.append("commission_commit_intent", {"paths": list(all_paths), "subject": subject})

    facts = observe_repository(repository)
    if facts.head != base_head:
        adopted = _try_adopt_commit(repository, base_head, all_paths, subject, expected_texts)
        if adopted is None:
            raise LifecycleError(
                "repository HEAD moved unexpectedly since authorization; refusing a second commit",
                classification="inconsistent",
            )
        commit = adopted
    else:
        _materialize_commission(repository, expected_texts)
        _reconcile_stray_staged_state(repository, base_head, expected_texts)
        intent = NewPathCommitIntent(paths=all_paths, subject=subject)
        try:
            committed = bounded_path_commit(
                repository, expected_repository=repository, base_head=base_head, allowed_paths=all_paths,
                intent=intent, work_item_id=request.work_item_id, hop_id=request.commission_id,
            )
        except TrustedCommitError as error:
            raise LifecycleError(f"Commission commit refused: {error}", classification="refused_prelaunch") from error
        commit = committed.commit

    return journal.append("commission_committed", {
        "commit": commit, "request_file_path": request_file_path,
        "work_item_id": request.work_item_id, "commission_id": request.commission_id,
        "target_dir": compiled.target_dir,
    })


# --------------------------------------------------------------------------
# Step 5: re-verification at the new HEAD.
# --------------------------------------------------------------------------

def _do_verify(repository: Path, checkout_dir: Path, journal: Journal, authorized: JournalRecord, commit_rec: JournalRecord) -> JournalRecord:
    envelope_text = (checkout_dir / "commission" / "envelope.yaml").read_text(encoding="utf-8")
    execution_configuration = run_execution_configuration_from_yaml(envelope_text)
    envelope = ExecutionEnvelope(
        configuration=execution_configuration, text=envelope_text, digest=authorized.data["envelope_digest"],
    )
    journal.append("verify_intent", {})
    try:
        verify_commission(
            commit_rec.data["request_file_path"], authorized.data["config_path"],
            repository=repository, expected_head=commit_rec.data["commit"],
            execution_envelope=envelope, target_dir=commit_rec.data["target_dir"],
        )
    except CommissionError as error:
        raise LifecycleError(f"post-commit verification failed: {error}", classification="inconsistent") from error
    return journal.append("verify_passed", {})


# --------------------------------------------------------------------------
# Step 6-8: engine launch, wait, classification.
# --------------------------------------------------------------------------

def _engine_argv(repository: Path, checkout_dir: Path, authorized: JournalRecord, prelaunch: JournalRecord, commit_rec: JournalRecord) -> list[str]:
    _, _, compiled, _ = _cached_materials(repository, checkout_dir, authorized)
    envelope_cache_path = checkout_dir / "commission" / "envelope.yaml"
    return [
        *_resolve_engine_command(),
        "--repository", str(repository),
        "--state-root", prelaunch.data["engine_state_root"],
        "--expected-head", commit_rec.data["commit"],
        "--work-item", compiled.paths["work_item"],
        "--profile", compiled.paths["profile"],
        "--policy", compiled.paths["policy"],
        "--components", compiled.paths["components"],
        "--task", compiled.paths["task"],
        "--execution-bindings", str(envelope_cache_path),
        "--timeout-seconds", str(authorized.data["hop_timeout_seconds"]),
        "--executable", prelaunch.data["executable_path"],
    ]


def _do_launch(
    repository: Path, checkout_dir: Path, journal: Journal, lock, authorized: JournalRecord,
    prelaunch: JournalRecord, commit_rec: JournalRecord,
) -> tuple[JournalRecord, subprocess.Popen]:
    engine_state_root = Path(prelaunch.data["engine_state_root"])
    if engine_state_root.exists():
        raise LifecycleError("engine state root unexpectedly exists before launch", classification="inconsistent")

    argv = _engine_argv(repository, checkout_dir, authorized, prelaunch, commit_rec)
    journal.append("launch_intent", {"argv": argv})
    try:
        process = subprocess.Popen(argv, pass_fds=(lock.fd,), close_fds=True, cwd=str(repository))
    except OSError as error:
        journal.append("launch_failed_no_engine", {"error": str(error)})
        raise LifecycleError(f"engine failed to launch: {error}", classification="launch_failed_no_engine") from error
    record = journal.append("launch_spawned", {"pid": process.pid, "start_time": time.time()})
    return record, process


def _classify(run, dispatch_marker_present: bool, summary_path: Path, facts) -> str:
    status = run.status
    if status == RunStatus.COMPLETED:
        consistent = False
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                summary = None
            if isinstance(summary, dict):
                consistent = (
                    summary.get("run_status") == "completed"
                    and summary.get("worktree_clean") is True
                    and summary.get("final_head") == facts.head
                )
        if consistent and facts.clean and not facts.unresolved_submodules:
            return "completed"
        return "inconsistent"
    if status in (RunStatus.AWAITING_HUMAN, RunStatus.AWAITING_HUMAN_BUDGET, RunStatus.RECONCILE_REQUIRED, RunStatus.STOPPED):
        return status.value
    if status in (RunStatus.READY, RunStatus.RUNNING, RunStatus.AWAITING_SUPERVISOR, RunStatus.SUSPENDED) or dispatch_marker_present:
        return "interrupted"
    return "inconsistent"


def _classify_from_state_root(repository: Path, engine_state_root: Path):
    """Read-only; returns ``(classification, run or None)``. Never mutates."""

    association_path = engine_state_root / "checkout-association.json"
    if not association_path.exists():
        return "inconsistent", None
    try:
        association = json.loads(association_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "inconsistent", None
    checkout_id = association.get("checkout_id")
    if not checkout_id:
        return "inconsistent", None
    run_root = engine_state_root / "checkouts" / checkout_id
    run_path = run_root / "run.json"
    if not run_path.exists():
        return "inconsistent", None
    try:
        run = load_run(run_path)
    except Exception:
        return "inconsistent", None
    dispatch_marker_present = (run_root / "dispatch.json").exists()
    summary_path = engine_state_root / "operator-summary.json"
    facts = observe_repository(repository)
    return _classify(run, dispatch_marker_present, summary_path, facts), run


def _evaluate(repository: Path, checkout_dir: Path, journal: Journal) -> LifecycleResult:
    """Pure (no side effect) read of the furthest truthful lifecycle state."""

    classified = journal.latest(("classified",))
    if classified is not None:
        data = classified.data
        return LifecycleResult(
            classification=data["classification"], message="lifecycle already classified (no-op)",
            run_id=data.get("run_id"), run_status=data.get("run_status"), end_head=data.get("end_head"),
            state_root=data.get("state_root"), checkout_state_root=data.get("checkout_state_root"),
        )

    authorized = journal.latest(("authorized",))
    if authorized is None:
        return LifecycleResult(classification="no_lifecycle", message="no lifecycle recorded for this checkout")

    launch_marker = journal.latest(("launch_intent", "launch_spawned", "launch_failed_no_engine"))
    if launch_marker is None:
        held = query_lock_held(checkout_dir)
        classification = "lifecycle_active" if held else "not_launched"
        return LifecycleResult(classification=classification, message="authorized; launch not yet reached")
    if launch_marker.kind == "launch_intent":
        return LifecycleResult(
            classification="uncertain_launch",
            message="a launch attempt's outcome is unknown (crash between intent and completion)",
        )
    if launch_marker.kind == "launch_failed_no_engine":
        attempts = journal.count("launch_intent")
        return LifecycleResult(
            classification="launch_failed_no_engine",
            message=f"no engine process was ever created (attempt {attempts} of {MAX_LAUNCH_ATTEMPTS})",
        )

    # launch_spawned.
    if query_lock_held(checkout_dir):
        return LifecycleResult(classification="engine_active", message="the engine (or its launcher) still holds the ownership lock")

    engine_state_root = Path(authorized.data["engine_state_root"])
    if not engine_state_root.exists():
        return LifecycleResult(
            classification="engine_exited_before_state", message="no provider dispatch recorded",
            state_root=str(engine_state_root),
        )
    classification, run = _classify_from_state_root(repository, engine_state_root)
    if classification == "inconsistent":
        return LifecycleResult(classification="inconsistent", message="engine/journal facts are contradictory", state_root=str(engine_state_root))
    association = json.loads((engine_state_root / "checkout-association.json").read_text(encoding="utf-8"))
    run_root = engine_state_root / "checkouts" / association["checkout_id"]
    return LifecycleResult(
        classification=classification, message=f"classified: {classification}",
        run_id=run.run_id, run_status=run.status.value, end_head=run.current_head,
        state_root=str(engine_state_root), checkout_state_root=str(run_root),
    )


def _record_classification_if_new(journal: Journal, result: LifecycleResult) -> LifecycleResult:
    if journal.latest(("classified",)) is not None:
        return result  # Already durably recorded; never append a second "classified" record.
    if result.classification not in _TERMINAL_CLASSIFICATIONS:
        return result
    journal.append("classified", {
        "classification": result.classification, "run_id": result.run_id, "run_status": result.run_status,
        "end_head": result.end_head, "state_root": result.state_root, "checkout_state_root": result.checkout_state_root,
    })
    return result


# --------------------------------------------------------------------------
# The shared advance/drive loop used by both ``run`` and ``resume``.
# --------------------------------------------------------------------------

def _advance(repository: Path, checkout_dir: Path, journal: Journal, *, authorize: Callable[[], tuple[JournalRecord, Any]] | None) -> LifecycleResult:
    existing_authorized = journal.latest(("authorized",))
    if existing_authorized is None:
        if authorize is None:
            raise LifecycleError("no lifecycle recorded for this checkout; nothing to resume", classification="refused_no_lifecycle")
        authorized, lock = authorize()
        held_lock = lock
    else:
        if authorize is not None:
            raise LifecycleError(
                "a lifecycle already exists for this checkout; use `wfc resume`", classification="refused_active_lifecycle",
            )
        authorized = existing_authorized
        held_lock = None

    launch_marker = journal.latest(("launch_intent", "launch_spawned", "launch_failed_no_engine"))
    if launch_marker is not None and launch_marker.kind == "launch_intent":
        if held_lock is not None:
            release_lock(held_lock)
        raise LifecycleError(
            "a launch attempt's outcome is unknown (crash between intent and completion)",
            classification="uncertain_launch",
        )

    if launch_marker is not None:
        if held_lock is not None:
            release_lock(held_lock)
        return _continue_after_launch(repository, checkout_dir, journal, authorized, launch_marker)

    lock = held_lock
    if lock is None:
        lock = try_acquire_lock(checkout_dir)
        if lock is None:
            raise LifecycleError("active lifecycle already running for this checkout", classification="refused_prelaunch")
    try:
        prelaunch = journal.latest(("prelaunch_passed",))
        if prelaunch is not None:
            _verify_identities_unchanged(prelaunch)
        if prelaunch is None:
            prelaunch = _do_prelaunch(repository, checkout_dir, journal, authorized)

        probe = journal.latest(("probe_passed",))
        if probe is None:
            probe = _do_probe(repository, checkout_dir, journal, authorized)

        commit_rec = journal.latest(("commission_committed",))
        if commit_rec is None:
            commit_rec = _do_commit(repository, checkout_dir, journal, authorized)

        verify_rec = journal.latest(("verify_passed",))
        if verify_rec is None:
            verify_rec = _do_verify(repository, checkout_dir, journal, authorized, commit_rec)

        spawned, process = _do_launch(repository, checkout_dir, journal, lock, authorized, prelaunch, commit_rec)
    except LifecycleError:
        release_lock(lock)
        raise

    process.wait()
    release_lock(lock)
    return _finish_after_spawn(repository, journal, prelaunch)


def _finish_after_spawn(repository: Path, journal: Journal, prelaunch: JournalRecord) -> LifecycleResult:
    """Classify right after this very process confirmed the engine has exited.

    Only valid immediately after a ``wait()`` on a child this process itself
    spawned -- there is no lock query here because none is needed: we just
    watched the only other holder of that lock terminate.
    """

    engine_state_root = Path(prelaunch.data["engine_state_root"])
    if not engine_state_root.exists():
        result = LifecycleResult(
            classification="engine_exited_before_state", message="no provider dispatch recorded",
            state_root=str(engine_state_root),
        )
        return _record_classification_if_new(journal, result)
    classification, run = _classify_from_state_root(repository, engine_state_root)
    if classification == "inconsistent":
        raise LifecycleError("engine/journal facts are contradictory", classification="inconsistent")
    association = json.loads((engine_state_root / "checkout-association.json").read_text(encoding="utf-8"))
    run_root = engine_state_root / "checkouts" / association["checkout_id"]
    result = LifecycleResult(
        classification=classification, message=f"classified: {classification}",
        run_id=run.run_id, run_status=run.status.value, end_head=run.current_head,
        state_root=str(engine_state_root), checkout_state_root=str(run_root),
    )
    return _record_classification_if_new(journal, result)


def _continue_after_launch(
    repository: Path, checkout_dir: Path, journal: Journal, authorized: JournalRecord, launch_marker: JournalRecord,
) -> LifecycleResult:
    if launch_marker.kind == "launch_failed_no_engine":
        attempts = journal.count("launch_intent")
        if attempts >= MAX_LAUNCH_ATTEMPTS:
            raise LifecycleError(
                f"launch attempts exhausted ({attempts} of {MAX_LAUNCH_ATTEMPTS})",
                classification="refused_launch_exhausted",
            )
        lock = try_acquire_lock(checkout_dir)
        if lock is None:
            raise LifecycleError("active lifecycle already running for this checkout", classification="refused_prelaunch")
        try:
            prelaunch = journal.latest(("prelaunch_passed",))
            commit_rec = journal.latest(("commission_committed",))
            _verify_identities_unchanged(prelaunch)
            spawned, process = _do_launch(repository, checkout_dir, journal, lock, authorized, prelaunch, commit_rec)
        except LifecycleError:
            release_lock(lock)
            raise
        process.wait()
        release_lock(lock)
        return _finish_after_spawn(repository, journal, prelaunch)

    # launch_spawned: a previous (possibly now-dead) controller already launched.
    result = _evaluate(repository, checkout_dir, journal)
    if result.classification == "inconsistent":
        raise LifecycleError("engine/journal facts are contradictory", classification="inconsistent")
    return _record_classification_if_new(journal, result)


# --------------------------------------------------------------------------
# Public entry points.
# --------------------------------------------------------------------------

def _prepare_checkout(repository: Path) -> tuple[CanonicalCheckout, Path, Journal]:
    checkout = canonical_checkout(repository)
    root = state_home()
    require_disjoint_journal_root(root, checkout)
    checkout_dir = checkout_directory(checkout, root=root)
    journal = Journal(checkout_dir / "journal.jsonl")
    return checkout, checkout_dir, journal


def _apply_postrun(result: LifecycleResult, status: postrun.PostrunStatus) -> LifecycleResult:
    """Fold the post-run archive/audit/report step's outcome into the
    lifecycle result. Never changes ``classification``, ``run_id`` or any
    other engine-outcome field -- only adds a print-only notice/report and,
    on refusal only, overrides the reported exit code (``archive_failed``)."""

    if status.kind == "absent":
        return result
    if status.kind == "refused":
        return replace(
            result, postrun_exit_override=ARCHIVE_FAILED_EXIT_CODE, postrun_notice=status.notice,
        )
    if status.kind == "skipped":
        return replace(result, postrun_notice=status.notice)
    return replace(result, postrun_report_text=status.report_text)


def run_lifecycle(inputs: RunInputs) -> LifecycleResult:
    checkout, checkout_dir, journal = _prepare_checkout(inputs.repository)

    def authorize() -> tuple[JournalRecord, Any]:
        return _authorize(inputs, checkout_dir, journal)

    result = _advance(inputs.repository, checkout_dir, journal, authorize=authorize)
    preservation.run_pending_preservation(inputs.repository, checkout_dir, journal)
    postrun_status = postrun.run_pending_postrun(inputs.repository, checkout_dir, journal)
    return _apply_postrun(result, postrun_status)


def resume_lifecycle(repository: Path) -> LifecycleResult:
    checkout, checkout_dir, journal = _prepare_checkout(repository)
    result = _advance(repository, checkout_dir, journal, authorize=None)
    preservation.run_pending_preservation(repository, checkout_dir, journal)
    postrun_status = postrun.run_pending_postrun(repository, checkout_dir, journal)
    return _apply_postrun(result, postrun_status)


def status_lifecycle(repository: Path) -> LifecycleResult:
    checkout, checkout_dir, journal = _prepare_checkout(repository)
    return _evaluate(repository, checkout_dir, journal)


# --------------------------------------------------------------------------
# CLI.
# --------------------------------------------------------------------------

def _execution_overrides_from_flags(values: list[str]) -> dict[str, dict[str, str]]:
    overrides: dict[str, dict[str, str]] = {}
    for item in values:
        if "=" not in item:
            raise CommissionError(f"--execution-override must be ROLE.FIELD=VALUE, got: {item}")
        dotted_key, value = item.split("=", 1)
        role, _, field = dotted_key.partition(".")
        if not role or field not in {"adapter", "model", "reasoning"}:
            raise CommissionError(
                f"--execution-override must be ROLE.FIELD=VALUE with FIELD one of "
                f"adapter/model/reasoning, got: {item}"
            )
        overrides.setdefault(role, {})[field] = value
    return overrides


def _make_confirm(*, yes: bool, out=sys.stdout, in_=sys.stdin) -> Callable[[str, str], bool]:
    def confirm(commission_digest: str, envelope_digest: str) -> bool:
        print(f"Commission digest: sha256:{commission_digest}", file=out)
        print(f"Execution envelope digest: sha256:{envelope_digest}", file=out)
        if yes:
            return True
        if not (hasattr(in_, "isatty") and in_.isatty() and hasattr(out, "isatty") and out.isatty()):
            return False
        print("Authorize this lifecycle? [y/N] ", end="", file=out)
        answer = in_.readline().strip().lower()
        return answer in ("y", "yes")
    return confirm


def _parse_run_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="wfc run", description=__doc__)
    parser.add_argument("--repository", default=".")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--request", required=True, help="path to an already-authored Consumer Request YAML file")
    parser.add_argument("--target-dir", default=None)
    parser.add_argument("--execution-override", action="append", default=[], metavar="ROLE.FIELD=VALUE")
    parser.add_argument("--executable", default=DEFAULT_EXECUTABLE)
    parser.add_argument("--hop-timeout-seconds", type=float, default=DEFAULT_HOP_TIMEOUT_SECONDS)
    parser.add_argument("--yes", action="store_true")
    return parser.parse_args(argv)


def _parse_simple_args(prog: str, argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--repository", default=".")
    return parser.parse_args(argv)


def _print_result(result: LifecycleResult) -> None:
    print(f"CLASSIFICATION: {result.classification}")
    print(f"MESSAGE: {result.message}")
    if result.run_id:
        print(f"RUN ID: {result.run_id}")
    if result.run_status:
        print(f"RUN STATUS: {result.run_status}")
    if result.end_head:
        print(f"END HEAD: {result.end_head}")
    if result.state_root:
        print(f"STATE ROOT: {result.state_root}")
    if result.checkout_state_root:
        print(f"CHECKOUT STATE ROOT: {result.checkout_state_root}")


def _stdout_safe(text: str) -> str:
    """A journaled refusal reason can embed a worktree path that is not valid
    UTF-8 (``os.fsdecode`` admits lone surrogates); print it losslessly
    rather than letting a strict-encoding stdout raise ``UnicodeEncodeError``
    and abort the report before the operator sees the reason.
    """

    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, "backslashreplace").decode(encoding, "replace")


def _print_preservation_status(status: preservation.PreservationStatus) -> None:
    """Report-only: never called from any BP4a write path, never mutates."""

    if status.kind == "absent":
        return
    print(f"PRESERVATION: {status.kind}")
    for key in ("ref", "commit", "tree", "file_count", "bytes", "quiescence", "reason"):
        if key in status.data:
            print(f"  {key}: {_stdout_safe(str(status.data[key]))}")


def _print_postrun_status(status: postrun.PostrunStatus) -> None:
    """Report-only: never called from any BP4a write path, never mutates."""

    if status.kind == "absent":
        return
    print(f"ARCHIVE: {status.kind}")
    for key in ("outcome", "commit", "destination_relative", "retention", "reason_code", "reason"):
        if key in status.data:
            print(f"  {key}: {_stdout_safe(str(status.data[key]))}")


def _print_postrun_notice(result: LifecycleResult) -> None:
    """``wfc run``/``wfc resume`` only: the loud ARCHIVE SKIPPED/FAILED line
    and, on a successful archive, the consolidated report text."""

    if result.postrun_notice:
        print(_stdout_safe(result.postrun_notice))
    if result.postrun_report_text:
        sys.stdout.write(_stdout_safe(result.postrun_report_text))


def cmd_run(argv: list[str]) -> int:
    args = _parse_run_args(argv)
    try:
        repository = repository_identity(Path(args.repository))
        overrides = _execution_overrides_from_flags(args.execution_override)
        inputs = RunInputs(
            repository=repository, config_path=args.config, request_path=Path(args.request).resolve(),
            target_dir=args.target_dir, overrides=overrides, executable=args.executable,
            hop_timeout_seconds=args.hop_timeout_seconds, confirm=_make_confirm(yes=args.yes),
        )
        result = run_lifecycle(inputs)
    except (LifecycleError, CommissionError, GitPreflightError, JournalError, OSError) as error:
        classification = getattr(error, "classification", "refused_prelaunch")
        print(f"wfc run: {error}", file=sys.stderr)
        return EXIT_CODES.get(classification, 2)
    _print_result(result)
    _print_postrun_notice(result)
    return result.exit_code


def cmd_resume(argv: list[str]) -> int:
    args = _parse_simple_args("wfc resume", argv)
    try:
        repository = repository_identity(Path(args.repository))
        result = resume_lifecycle(repository)
    except (LifecycleError, CommissionError, GitPreflightError, JournalError, OSError) as error:
        classification = getattr(error, "classification", "refused_prelaunch")
        print(f"wfc resume: {error}", file=sys.stderr)
        return EXIT_CODES.get(classification, 2)
    _print_result(result)
    _print_postrun_notice(result)
    return result.exit_code


def cmd_status(argv: list[str]) -> int:
    args = _parse_simple_args("wfc status", argv)
    try:
        repository = repository_identity(Path(args.repository))
        result = status_lifecycle(repository)
        preservation_status = preservation.latest_preservation_status(repository)
        postrun_status = postrun.latest_postrun_status(repository)
    except (CommissionError, GitPreflightError, JournalError, OSError) as error:
        print(f"wfc status: {error}", file=sys.stderr)
        return 2
    _print_result(result)
    _print_preservation_status(preservation_status)
    _print_postrun_status(postrun_status)
    # ``status`` is purely read-only reporting (like the legacy
    # ``--state-root`` status, which always exits 0): a 0 exit means the
    # report was produced truthfully, not that the reported state is good
    # news. Only "nothing to report" is itself a failure to report anything.
    return 0 if result.classification != "no_lifecycle" else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["run"]:
        return cmd_run(args[1:])
    if args[:1] == ["resume"]:
        return cmd_resume(args[1:])
    if args[:1] == ["status"]:
        return cmd_status(args[1:])
    print("wfc: expected one of: run, resume, status", file=sys.stderr)
    return 2
