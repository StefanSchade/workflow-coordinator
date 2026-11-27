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

from ..commissioning import CommissionError, _path, compile_commission
from ..evidence import UNTRUSTED_DIAGNOSTIC_CAPTURE_LIMIT_BYTES
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
from ..model import DispatchMarker, FindingState, Run, RunStatus
from ..persistence import (
    atomic_write_json,
    checkpoint_projection,
    load_dispatch_marker,
    write_dispatch_marker,
    write_run_snapshot,
)
from ..reducer import (
    BUDGET_EXTENSION_MAX_PER_CALL,
    ReductionError,
    budget_extension_total_bound,
    diagnose_budget_extension_amount,
    diagnose_budget_extension_state,
    diagnose_mechanical_recovery_eligibility,
    diagnose_quiescent_interruption_recovery_eligibility,
    extend_hop_budget,
    refuse_degenerate_authority,
    require_reconcile,
    resume_after_reconcile,
)
from ..serialization import (
    load_run,
    repository_policy_from_yaml,
    run_execution_configuration_from_yaml,
    workflow_profile_from_yaml,
)
from .config import ExecutionEnvelope, consumer_config_from_yaml, execution_overrides_from_flags, resolve_execution
from . import postrun, preservation
from .journal import (
    CanonicalCheckout,
    Journal,
    JournalError,
    JournalRecord,
    canonical_checkout,
    checkout_directory,
    journal_path,
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
DEFAULT_HOP_TIMEOUT_SECONDS = 14400.0
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
    # B, WI-WFC-RECOVERY-ROUTING-001 (additive; every value above is frozen
    # and unchanged): the regular mechanical-recovery predicate (re-verified
    # against live Run and checkout facts) refused. Never mutates anything;
    # the cached classification this `wfc resume` attempt started from is
    # unaffected and remains the truthful record of the engine's own outcome.
    "refused_not_recoverable": 33,
    # O8, WI-WFC-BETA2-O8-001 (additive; every value above is frozen and
    # unchanged): `wfc extend-budget` refused by name. Never mutates
    # anything; the Run keeps its `awaiting_human_budget` record, its stop
    # reason and every charged Hop.
    "refused_budget_extension": 34,
}

# O8: the outcome of ``wfc extend-budget --preview``. Deliberately *not* an
# ``EXIT_CODES`` entry: that table is the Run-outcome classification
# vocabulary ``wfc report`` must carry a fixed next-human-choice line for,
# and a preview is not a Run outcome -- it writes nothing, is never recorded
# in a ``classified`` journal record, and can therefore never appear in an
# audit or a report. ``cmd_extend_budget`` maps it to exit 0 explicitly.
BUDGET_EXTENSION_PREVIEW_CLASSIFICATION = "budget_extension_preview"

# O10B, WI-WFC-BETA2-O10B-001: the outcome of ``wfc resume --preview``,
# deliberately not an ``EXIT_CODES`` entry for exactly the reason the
# budget-extension preview above is not one either: it writes nothing, takes
# no lock, is never recorded in a ``classified`` journal record and can
# therefore never appear in an audit or a report. ``cmd_resume`` maps it to
# exit 0 explicitly.
RECOVERY_PREVIEW_CLASSIFICATION = "recovery_preview"

# O10B: the two same-Run recovery classes ``wfc resume`` admits, named so
# that a journal record, a printed block and a refusal all say the same
# word. Every stop outside both classes keeps refusing, or no-opping,
# exactly as before -- see ``_diagnose_recovery``.
RECOVERY_CLASS_DURABLE_ARTIFACT = "durable_artifact_anomaly"
RECOVERY_CLASS_QUIESCENT_INTERRUPTION = "quiescent_interruption"

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
    # O8: every recorded maintainer Hop-budget extension of this checkout's
    # Run, read from the checkout's own append-only journal, for the
    # report-only ``wfc status`` surface. Empty for every checkout that was
    # never extended, so no existing construction of this dataclass changes.
    budget_extensions: tuple[Mapping[str, Any], ...] = ()
    # O10B: every same-Run recovery actually performed on this checkout, read
    # from the checkout's own append-only journal, for the report-only
    # ``wfc status``/``wfc run``/``wfc resume`` surface. Empty for every
    # checkout that was never recovered, so no existing construction of this
    # dataclass changes.
    recovery_events: tuple[Mapping[str, Any], ...] = ()

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
    envelope = resolve_execution(config, overrides, compiled.phase_roles)
    request_file_path = f"{compiled.target_dir}/request.yaml"
    digest = _materials_digest(compiled.texts, request_file_path, request.raw_text)
    return request, config, compiled, envelope, request_file_path, digest


def _pinned_envelope_text(checkout_dir: Path, authorized: JournalRecord) -> str:
    try:
        data = (checkout_dir / "commission" / "envelope.yaml").read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise LifecycleError("pinned execution envelope is unavailable or invalid", classification="refused_drift") from error
    if hashlib.sha256(data).hexdigest() != authorized.data["envelope_digest"]:
        raise LifecycleError("execution envelope digest changed since authorization", classification="refused_drift")
    return text


def _cached_commission(repository: Path, checkout_dir: Path, authorized: JournalRecord):
    commission_dir = checkout_dir / "commission"
    request = consumer_request_from_yaml((commission_dir / "request.yaml").read_text(encoding="utf-8"))
    config = consumer_config_from_yaml((commission_dir / "config.yaml").read_text(encoding="utf-8"))
    compiled = compile_commission(request, config, repository=repository, target_dir=authorized.data["target_dir"])
    return request, compiled


# --------------------------------------------------------------------------
# Step 1: authorization (``run`` only).
# --------------------------------------------------------------------------

def _authorize(inputs: RunInputs, checkout_dir: Path, journal: Journal) -> tuple[JournalRecord, Any]:
    # O2(a): the same A1-normalized, repository-relative shape
    # ``verify_commission`` re-checks post-commit (``frontdoor/verify.py``,
    # via ``commissioning._path``), checked here before authorization
    # instead -- the input either passes this identical shape check or is
    # refused now, not after a Commission commit, as ``inconsistent``.
    _path(inputs.config_path, context="--config")
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


# O2(b): each adapter's own known provider binary name -- the same bare
# names already defaulted elsewhere (``DEFAULT_EXECUTABLE``, the adapters'
# own ``executable`` defaults). Used only to catch an *unambiguous* mismatch;
# never widened into a registry of acceptable names.
_ADAPTER_KNOWN_BASENAME = {"codex_cli": "codex", "claude_cli": "claude"}


def _envelope_adapter(checkout_dir: Path, authorized: JournalRecord) -> str:
    """The single adapter the authorized envelope resolved to.

    ``resolve_execution`` already refuses a mixed-adapter envelope, so every
    binding (role-keyed or phase-keyed) names the same adapter; any one of
    them is representative.
    """

    configuration = run_execution_configuration_from_yaml(_pinned_envelope_text(checkout_dir, authorized))
    bindings = configuration.bindings if configuration.schema_version == 1 else configuration.phase_bindings
    return next(iter(bindings.values())).adapter


def _refuse_cross_adapter_executable_mismatch(executable_argument: str, executable_path: str, adapter: str) -> None:
    """Refuse only an unambiguous cross-adapter ``--executable``, by name.

    Never an explicit path (absolute or containing a separator) and never an
    unrecognized bare name -- both stay launchable, so a legitimate provider
    wrapper is never blocked (BUG-WFC-PRELAUNCH-REFUSAL-ERGONOMICS (b)).
    """

    if os.path.isabs(executable_argument) or os.sep in executable_argument:
        return
    basename = os.path.basename(executable_path)
    other_basenames = {name for other, name in _ADAPTER_KNOWN_BASENAME.items() if other != adapter}
    if basename in other_basenames:
        raise LifecycleError(
            f"--executable resolved to '{basename}', the known binary of a different adapter than "
            f"this envelope's '{adapter}'; pass the executable for '{adapter}' or an explicit path",
            classification="refused_prelaunch",
        )


def _do_prelaunch(repository: Path, checkout_dir: Path, journal: Journal, authorized: JournalRecord) -> JournalRecord:
    journal.append("prelaunch_intent", {})

    executable_argument = authorized.data["executable_argument"]
    executable_path = _resolve_executable(executable_argument)
    if not os.path.isfile(executable_path) or not os.access(executable_path, os.X_OK):
        raise LifecycleError(
            f"provider executable does not exist or is not executable: {executable_path}",
            classification="refused_prelaunch",
        )
    _refuse_cross_adapter_executable_mismatch(
        executable_argument, executable_path, _envelope_adapter(checkout_dir, authorized),
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

def _bounded_untrusted_tail(stdout: bytes, stderr: bytes) -> str:
    """The already-captured probe output, bounded and labelled untrusted.

    Reuses the in-Hop evidence path's own limit
    (``evidence.UNTRUSTED_DIAGNOSTIC_CAPTURE_LIMIT_BYTES``) rather than
    choosing a new one. Printed only, as part of a refusal message: nothing
    here is written to disk or to any journal/evidence record
    (BUG-WFC-PRELAUNCH-REFUSAL-ERGONOMICS (c)).
    """

    limit = UNTRUSTED_DIAGNOSTIC_CAPTURE_LIMIT_BYTES

    def rendered(label: str, value: bytes) -> str:
        tail = value[-limit:]
        notice = f" (showing the last {limit} of {len(value)} bytes)" if len(value) > limit else ""
        return f"{label} tail, untrusted{notice}:\n{tail.decode('utf-8', 'backslashreplace')}"

    return rendered("stdout", stdout) + "\n" + rendered("stderr", stderr)


def _do_probe(repository: Path, checkout_dir: Path, journal: Journal, authorized: JournalRecord) -> JournalRecord:
    journal.append("probe_intent", {})

    facts_before = observe_repository(repository)
    if facts_before.head != authorized.data["base_head"] or not facts_before.clean or facts_before.unresolved_submodules:
        raise LifecycleError("repository is not at the clean authorized baseline", classification="refused_prelaunch")

    request_text = (checkout_dir / "commission" / "request.yaml").read_text(encoding="utf-8")
    request = consumer_request_from_yaml(request_text)
    # The whole declared sequence, in declared order, fail-fast, with each
    # command's own timeout: exactly what the Coordinator will run as the
    # task-check after every Worker Hop. A probe over only a cheap prefix
    # would admit a Run whose first Hop fails the task-check for a reason
    # that was already there (FTR-WFC-EVIDENCE-COMMAND-SEQUENCE, Design
    # decision (d)). Every refusal names the command it belongs to.
    for command in request.evidence:
        try:
            completed = subprocess.run(
                list(command.argv), cwd=str(repository), timeout=command.timeout_seconds,
                capture_output=True,
            )
        except subprocess.TimeoutExpired as error:
            raise LifecycleError(
                f"baseline probe command '{command.name}' timed out; no provider ran: {error}",
                classification="refused_probe",
            ) from error
        except OSError as error:
            raise LifecycleError(
                f"baseline probe command '{command.name}' is unresolvable; no provider ran: {error}",
                classification="refused_probe",
            ) from error

        facts_after = observe_repository(repository)
        if completed.returncode != 0:
            raise LifecycleError(
                f"baseline probe command '{command.name}' failed with exit code "
                f"{completed.returncode}; no provider ran\n"
                + _bounded_untrusted_tail(completed.stdout, completed.stderr),
                classification="refused_probe",
            )
        if facts_after.head != facts_before.head or not facts_after.clean or facts_after.unresolved_submodules:
            changed = sorted({*facts_after.tracked_changes, *facts_after.untracked_changes})
            raise LifecycleError(
                f"baseline probe command '{command.name}' mutated the repository; no provider ran "
                f"(changed: {', '.join(changed) or 'HEAD moved'})",
                classification="refused_probe",
            )
    return journal.append("probe_passed", {"commands": [command.name for command in request.evidence]})


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
    _pinned_envelope_text(checkout_dir, authorized)
    request, compiled = _cached_commission(repository, checkout_dir, authorized)
    request_text = request.raw_text
    request_file_path = f"{compiled.target_dir}/request.yaml"
    digest = _materials_digest(compiled.texts, request_file_path, request_text)
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
    envelope_text = _pinned_envelope_text(checkout_dir, authorized)
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
    _pinned_envelope_text(checkout_dir, authorized)
    _, compiled = _cached_commission(repository, checkout_dir, authorized)
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
        "--execution-bindings-sha256", authorized.data["envelope_digest"],
        "--timeout-seconds", str(authorized.data["hop_timeout_seconds"]),
        "--executable", prelaunch.data["executable_path"],
    ]


def _continuation_engine_argv(
    repository: Path, checkout_dir: Path, authorized: JournalRecord, prelaunch: JournalRecord,
    *, current_head: str,
) -> list[str]:
    """B, WI-WFC-RECOVERY-ROUTING-001: identical to ``_engine_argv`` except
    ``--expected-head`` is the Run's own live current HEAD (never the
    original baseline) and ``--continue-existing`` selects the engine's
    continuation admission path, which never creates a new Run."""

    _pinned_envelope_text(checkout_dir, authorized)
    _, compiled = _cached_commission(repository, checkout_dir, authorized)
    envelope_cache_path = checkout_dir / "commission" / "envelope.yaml"
    return [
        *_resolve_engine_command(),
        "--repository", str(repository),
        "--state-root", prelaunch.data["engine_state_root"],
        "--expected-head", current_head,
        "--work-item", compiled.paths["work_item"],
        "--profile", compiled.paths["profile"],
        "--policy", compiled.paths["policy"],
        "--components", compiled.paths["components"],
        "--task", compiled.paths["task"],
        "--execution-bindings", str(envelope_cache_path),
        "--execution-bindings-sha256", authorized.data["envelope_digest"],
        "--timeout-seconds", str(authorized.data["hop_timeout_seconds"]),
        "--executable", prelaunch.data["executable_path"],
        "--continue-existing",
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

    # O8: every granted Hop-budget extension of this checkout's Run, so
    # ``wfc status`` shows it on every reported shape, not only after a
    # continuation. Read-only; a checkout that was never extended reports
    # an empty tuple, exactly as before.
    extensions = _journaled_budget_extensions(journal)
    # O10B: every same-Run recovery performed on this checkout, for the same
    # reason and on the same read-only terms as the extensions above.
    recoveries = _journaled_recovery_events(journal)

    classified = journal.latest(("classified",))
    if classified is not None:
        data = classified.data
        return LifecycleResult(
            classification=data["classification"], message="lifecycle already classified (no-op)",
            run_id=data.get("run_id"), run_status=data.get("run_status"), end_head=data.get("end_head"),
            state_root=data.get("state_root"), checkout_state_root=data.get("checkout_state_root"),
            budget_extensions=extensions, recovery_events=recoveries,
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
        budget_extensions=extensions, recovery_events=recoveries,
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
# B, WI-WFC-RECOVERY-ROUTING-001: the regular, authorized mechanical-
# recovery path through ``wfc resume``. Reachable only from an already
# ``classified`` checkout (otherwise ``_advance`` below is unaffected and
# handles every other shape exactly as before); named refusals never mutate
# anything, and a successful recovery only ever appends new journal records,
# never rewrites or deletes one, and never touches the already-archived
# attempt from a prior classification.
#
# O10B, WI-WFC-BETA2-O10B-001: the identical path now admits a *second*
# recovery class, the proven-quiescent post-dispatch interruption, through
# its own pure predicate and the same live-fact re-verification in the same
# pre-mutation order. Every stop outside both classes keeps its behaviour
# exactly: refusing by name where a class's own predicate is in play and one
# element fails, and the unchanged "lifecycle already classified (no-op)"
# otherwise. See ``FTR-WFC-ATOMIC-HOP-RECOVERY``'s Design section for the
# per-class evaluation and the classes it deliberately leaves out.
# --------------------------------------------------------------------------

def _refused_recovery(run: Run, reason: str) -> LifecycleResult:
    return LifecycleResult(
        classification="refused_not_recoverable",
        message=f"mechanical recovery refused: {reason}",
        run_id=run.run_id, run_status=run.status.value, end_head=run.current_head,
    )


def _journaled_recovery_events(journal: Journal) -> tuple[Mapping[str, Any], ...]:
    """O10B: every same-Run recovery actually performed on this checkout.

    Read-only, from the append-only journal, in the order they happened. A
    recovery whose continuation launch failed and was rolled back is
    excluded here (it changed nothing in the end), while its ``*_intent``
    and ``*_rolled_back`` records stay in the journal as the attempt's own
    auditable trace -- exactly the rule O8's budget-extension projection
    uses, for the same reason.
    """

    performed: list[Mapping[str, Any]] = []
    for record in journal.read_all():
        if record.kind == "mechanical_recovery_intent":
            performed.append(dict(record.data))
        elif record.kind == "mechanical_recovery_rolled_back" and performed:
            # Written under the same held ownership lock as the recovery it
            # undoes, and nothing else can recover in between, so it always
            # undoes exactly the latest attempt.
            performed.pop()
    return tuple(performed)


@dataclass(frozen=True)
class _RecoveryDiagnosis:
    """The recovery decision for one persisted Run, before any live fact.

    Exactly one of three shapes, which is what keeps every non-admitted stop
    fail-closed: ``in_scope=False`` ("never a candidate for either
    predicate", so the caller falls through to its own unchanged behaviour),
    ``refusal`` set ("in scope, and this named element is violated"), or
    ``recovery_class`` and ``proven`` set ("every element of that class's
    predicate holds").
    """

    in_scope: bool
    recovery_class: str | None = None
    proven: str | None = None
    refusal: str | None = None
    # Only the durable-artifact class has an anomaly text; the interruption
    # class deliberately has none rather than a fabricated one.
    anomaly: str | None = None
    # The charged uncertain Hop the interruption class proved quiescent.
    interrupted_hop_id: str | None = None


_NOT_IN_SCOPE = _RecoveryDiagnosis(in_scope=False)


def _diagnose_recovery(run: Run) -> _RecoveryDiagnosis:
    """Decide which same-Run recovery class, if any, this Run belongs to.

    The two pure predicates are consulted in order and are mutually
    exclusive by construction: the durable-artifact one requires a
    ``mechanical_preemption:`` stop reason, the interruption one requires
    ``reconcile_reason == "interruption"`` with its own
    ``reconcile_requires_explicit_resume`` stop reason. A third class would
    be a third pure predicate and one more branch here -- deliberately not a
    registry (the Design section rejects a framework for two classes).
    """

    anomaly, refusal = diagnose_mechanical_recovery_eligibility(run)
    if anomaly is not None:
        return _RecoveryDiagnosis(
            in_scope=True, recovery_class=RECOVERY_CLASS_DURABLE_ARTIFACT,
            proven=f"recoverable durable-artifact anomaly: {anomaly}", anomaly=anomaly,
        )
    if refusal is not None:
        return _RecoveryDiagnosis(
            in_scope=True, recovery_class=RECOVERY_CLASS_DURABLE_ARTIFACT, refusal=refusal,
        )

    hop_id, refusal = diagnose_quiescent_interruption_recovery_eligibility(run)
    if hop_id is not None:
        return _RecoveryDiagnosis(
            in_scope=True, recovery_class=RECOVERY_CLASS_QUIESCENT_INTERRUPTION,
            proven=(
                f"quiescent post-dispatch interruption at charged uncertain Hop {hop_id}: "
                "no commit, no HEAD movement, no recorded repository anomaly, no staged "
                "qualification attempt, no route and no Finding delta"
            ),
            interrupted_hop_id=hop_id,
        )
    if refusal is not None:
        return _RecoveryDiagnosis(
            in_scope=True, recovery_class=RECOVERY_CLASS_QUIESCENT_INTERRUPTION, refusal=refusal,
        )
    return _NOT_IN_SCOPE


def _recovery_commission_identity_ok(repository: Path, checkout_dir: Path, authorized: JournalRecord) -> bool:
    """Re-verify the exact same Commission-digest check ``_do_commit`` makes
    at first launch, read-only, against the cached request/config this
    checkout authorized -- "no changed Commission/envelope identity"."""

    try:
        _pinned_envelope_text(checkout_dir, authorized)
        request, compiled = _cached_commission(repository, checkout_dir, authorized)
    except (LifecycleError, OSError, UnicodeError, CommissionError):
        return False
    request_file_path = f"{compiled.target_dir}/request.yaml"
    digest = _materials_digest(compiled.texts, request_file_path, request.raw_text)
    return digest == authorized.data["commission_digest"]


def _do_continuation_launch(
    repository: Path, checkout_dir: Path, journal: Journal, lock, authorized: JournalRecord,
    prelaunch: JournalRecord, *, current_head: str,
    record_prefix: str = "mechanical_recovery",
    refusal_classification: str = "refused_not_recoverable",
    what: str = "recovery",
) -> tuple[JournalRecord, subprocess.Popen]:
    """Distinct record kinds from ``_do_launch``'s own ``launch_*`` family:
    a continuation launch must never be mistaken for, or retried by, the
    original launch's own crash-matrix bookkeeping (``MAX_LAUNCH_ATTEMPTS``,
    ``_continue_after_launch``), which stays unchanged and unaware of it.

    O8: ``record_prefix``/``refusal_classification``/``what`` keep the two
    continuation callers' journal vocabularies and named refusals distinct
    (mechanical recovery versus a recorded budget extension). The defaults
    are the pre-O8 mechanical-recovery values, so that path's records and
    refusal classification are unchanged.
    """

    argv = _continuation_engine_argv(repository, checkout_dir, authorized, prelaunch, current_head=current_head)
    journal.append(f"{record_prefix}_launch_intent", {"argv": argv})
    try:
        process = subprocess.Popen(argv, pass_fds=(lock.fd,), close_fds=True, cwd=str(repository))
    except OSError as error:
        journal.append(f"{record_prefix}_launch_failed", {"error": str(error)})
        raise LifecycleError(
            f"{what} continuation engine failed to launch: {error}",
            classification=refusal_classification,
        ) from error
    record = journal.append(f"{record_prefix}_launch_spawned", {"pid": process.pid, "start_time": time.time()})
    return record, process


def _recovery_candidate_run(classified: JournalRecord) -> tuple[Path, Run] | None:
    """The persisted Run a classified checkout might recover, or ``None``.

    ``None`` is the "never in scope" answer for every classification outside
    the two recovery classes' own statuses and for an absent or unreadable
    Run record -- deliberately identical to the pre-O10B behaviour, which
    fell through to the unchanged no-op for exactly these shapes.
    """

    if classified.data.get("classification") not in ("reconcile_required", "awaiting_human"):
        return None
    checkout_state_root_value = classified.data.get("checkout_state_root")
    if not checkout_state_root_value:
        return None
    run_path = Path(checkout_state_root_value) / "run.json"
    if not run_path.is_file():
        return None
    try:
        return run_path, load_run(run_path)
    except Exception:
        return None


def _correlated_predecessor_marker(
    run_root: Path, run: Run,
) -> tuple[Path, DispatchMarker] | str | None:
    """O10B: the already-reconciled predecessor dispatch marker, or a refusal.

    Returns ``(path, marker)`` when a marker is present and correlates
    *exactly* with the charged uncertain Hop the pure predicate proved
    quiescent, ``None`` when no marker file exists at all (nothing to
    retire; the recovery proceeds), or a refusal string. The correlation is
    derived from the Coordinator's own two records -- the marker it wrote
    before dispatching and the Hop it charged afterwards -- never from any
    agent claim: an uncorrelated marker means this checkout's state is not
    the state the predicate reasoned about, so it is refused rather than
    silently retired.
    """

    marker_path = run_root / "dispatch.json"
    if not marker_path.exists():
        return None
    try:
        marker = load_dispatch_marker(marker_path)
    except Exception as error:
        return f"the predecessor dispatch marker is not readable: {error}"
    hop = run.hops[-1]
    if (
        marker.run_id != run.run_id
        or marker.hop_id != hop.hop_id
        or marker.sequence != hop.sequence
        or marker.base_head != hop.base_head
        or marker.pre_route_anchor_sha256 is not None
    ):
        return (
            "the predecessor dispatch marker does not correlate with the charged "
            f"uncertain Hop {hop.hop_id}"
        )
    return marker_path, marker


def _attempt_mechanical_recovery(
    repository: Path, checkout_dir: Path, journal: Journal, classified: JournalRecord,
) -> LifecycleResult | None:
    """Return ``None`` when this checkout's cached classification was never
    in scope for same-Run recovery at all (the caller then falls through
    to the unchanged "lifecycle already classified (no-op)" behaviour for
    every other classification, and for an "awaiting_human"/
    "reconcile_required" Run that matches neither recovery class's own
    predicate). Otherwise always returns a ``LifecycleResult``: a named
    refusal (no mutation at all) or a completed continuation (new journal
    records only, Run moved to ``READY`` and the engine re-driven to a
    further ``classified`` record)."""

    candidate = _recovery_candidate_run(classified)
    if candidate is None:
        return None
    run_path, run = candidate
    checkout_state_root_value = classified.data["checkout_state_root"]

    diagnosis = _diagnose_recovery(run)
    if not diagnosis.in_scope:
        return None  # Scope gate: neither recovery class was ever in play.
    if diagnosis.refusal is not None:
        return _refused_recovery(run, diagnosis.refusal)
    recovery_class = diagnosis.recovery_class

    authorized = journal.latest(("authorized",))
    prelaunch = journal.latest(("prelaunch_passed",))
    if authorized is None or prelaunch is None:
        return _refused_recovery(run, "lifecycle authorization or launch provenance is incomplete")

    facts = observe_repository(repository)
    if facts.head != run.current_head:
        return _refused_recovery(run, "current HEAD differs from the Run's recorded end HEAD")
    if not facts.clean or facts.unresolved_submodules:
        return _refused_recovery(run, "worktree is not clean with resolved submodules")
    if run.hop_used >= run.hop_limit:
        return _refused_recovery(run, "no autonomous Hop budget remains for a continuation")
    if not _recovery_commission_identity_ok(repository, checkout_dir, authorized):
        return _refused_recovery(run, "Commission or execution envelope identity changed since authorization")
    try:
        # Re-verify the provider executable is still present and unchanged
        # *before* any mutation below, so the one ordinary cause of a launch
        # failure here (a since-removed/changed executable) is a refusal
        # with no side effect, not a mutated Run with no process to drive it.
        _verify_identities_unchanged(prelaunch)
    except LifecycleError as error:
        return _refused_recovery(run, str(error))

    try:
        _, compiled = _cached_commission(repository, checkout_dir, authorized)
        profile = workflow_profile_from_yaml(compiled.texts[compiled.paths["profile"]])
    except Exception as error:
        return _refused_recovery(run, f"governing profile could not be re-derived: {error}")

    # F004: every remaining predicate element -- the pure reconcile/resume
    # re-verification and the continuation engine's resolvability -- is
    # checked, and the ownership lock is taken, *before* any mutation below,
    # for the same reason the provider-executable re-verification above
    # already runs pre-mutation. Otherwise a since-removed/renamed `wfc`
    # console script, a concurrent lifecycle holding the lock, or a failed
    # reconcile/resume re-verification is discovered only after the Run has
    # already been rewritten to `ready` with its mechanical diagnosis
    # erased, leaving it unresumable.
    try:
        reconciled = run if run.status is RunStatus.RECONCILE_REQUIRED else require_reconcile(
            run, reason="mechanical_recoverable", current_head=run.current_head,
        )
        resumed = resume_after_reconcile(reconciled, profile, current_head=facts.head)
    except ReductionError as error:
        return _refused_recovery(run, f"recovery predicate re-verification failed: {error}")

    # O10B: the interruption class is the only one with a predecessor
    # dispatch marker still in place, and it must be retired below -- so its
    # exact correlation with the charged uncertain Hop is established here,
    # pre-mutation, like every other element. The durable-artifact class
    # reaches its stop after the marker was already retired normally
    # (`slice4.py:429`), so there is nothing to correlate or retire for it.
    marker_to_retire: tuple[Path, DispatchMarker] | None = None
    if recovery_class == RECOVERY_CLASS_QUIESCENT_INTERRUPTION:
        correlated = _correlated_predecessor_marker(Path(checkout_state_root_value), run)
        if isinstance(correlated, str):
            return _refused_recovery(run, correlated)
        marker_to_retire = correlated
    try:
        engine_argv0 = _resolve_engine_command()[0]
    except RuntimeError as error:
        return _refused_recovery(run, str(error))
    if not os.path.isfile(engine_argv0) or not os.access(engine_argv0, os.X_OK):
        return _refused_recovery(run, f"no engine executable is available for a continuation: {engine_argv0}")

    lock = try_acquire_lock(checkout_dir)
    if lock is None:
        return _refused_recovery(run, "active lifecycle already running for this checkout")
    try:
        journal.append("mechanical_recovery_intent", {
            "run_id": run.run_id, "checkout_state_root": checkout_state_root_value,
            # Unchanged for the durable-artifact class; ``None`` for the
            # interruption class, which has no anomaly text and must not be
            # given a fabricated one.
            "anomaly": diagnosis.anomaly, "from_status": run.status.value,
            # O10B: additive, and always present from this slice on, so a
            # reader never has to infer the class from the anomaly text.
            "recovery_class": recovery_class, "proven": diagnosis.proven,
            "work_item_id": run.work_item_id, "phase": run.phase,
            "interrupted_hop_id": diagnosis.interrupted_hop_id,
            "hop_used_at_resume": run.hop_used, "hop_limit_at_resume": run.hop_limit,
        })
        write_run_snapshot(run_path, resumed)
        atomic_write_json(Path(checkout_state_root_value) / "checkpoint.json", checkpoint_projection(resumed, profile))
        journal.append("mechanical_recovery_resumed", {
            "run_id": resumed.run_id, "to_status": resumed.status.value,
            "recovery_class": recovery_class,
        })
        # O10B, in exactly this order: the resumed Run snapshot and its
        # checkpoint are durably persisted *before* the already-reconciled
        # predecessor marker is retired, and the marker is retired before any
        # later dispatch is permitted -- the ordering
        # ``FTR-WFC-ATOMIC-HOP-RECOVERY`` makes mandatory. Without the
        # retirement the continuation re-enters
        # ``slice4.run_real_one_hop``'s stale-marker branch, re-reconciles
        # the identical already-charged uncertain Hop and the Run can never
        # progress. Retiring it enables no replay (the Hop id is in
        # ``run.hops``, so the next dispatch takes the following sequence
        # with a fresh invocation identity) and loses no information (the
        # predicate proved no staged qualification attempt refers to it).
        if marker_to_retire is not None:
            marker_to_retire[0].unlink()
            journal.append("mechanical_recovery_marker_retired", {
                "run_id": run.run_id, "recovery_class": recovery_class,
                "interrupted_hop_id": diagnosis.interrupted_hop_id,
            })

        _, process = _do_continuation_launch(
            repository, checkout_dir, journal, lock, authorized, prelaunch, current_head=facts.head,
        )
    except LifecycleError as error:
        # F004: a launch failure here would otherwise leave the Run rewritten
        # to `ready` with its mechanical diagnosis erased and no process to
        # drive it -- unresumable. Roll the Run and checkpoint back to their
        # pre-recovery state and record the rollback; the journal stays
        # append-only, nothing already written is rewritten or deleted.
        # O10B: a retired marker is restored from its own identical bytes
        # too, so a rolled-back attempt leaves the Run and the marker exactly
        # as it found them (the checkpoint is re-projected from the restored
        # Run, as on the pre-existing path).
        write_run_snapshot(run_path, run)
        atomic_write_json(Path(checkout_state_root_value) / "checkpoint.json", checkpoint_projection(run, profile))
        if marker_to_retire is not None:
            write_dispatch_marker(*marker_to_retire)
        journal.append("mechanical_recovery_rolled_back", {"run_id": run.run_id, "error": str(error)})
        release_lock(lock)
        raise
    process.wait()
    release_lock(lock)

    engine_state_root = Path(prelaunch.data["engine_state_root"])
    continuation_classification, continued_run = _classify_from_state_root(repository, engine_state_root)
    if continuation_classification == "inconsistent":
        raise LifecycleError(
            "engine/journal facts are contradictory after a recovery continuation", classification="inconsistent",
        )
    association = json.loads((engine_state_root / "checkout-association.json").read_text(encoding="utf-8"))
    run_root = engine_state_root / "checkouts" / association["checkout_id"]
    result = LifecycleResult(
        classification=continuation_classification,
        message=(
            f"mechanically recovered ({recovery_class}); "
            f"classified: {continuation_classification}"
        ),
        run_id=continued_run.run_id, run_status=continued_run.status.value, end_head=continued_run.current_head,
        state_root=str(engine_state_root), checkout_state_root=str(run_root),
        budget_extensions=_journaled_budget_extensions(journal),
        recovery_events=_journaled_recovery_events(journal),
    )
    journal.append("classified", {
        "classification": result.classification, "run_id": result.run_id, "run_status": result.run_status,
        "end_head": result.end_head, "state_root": result.state_root, "checkout_state_root": result.checkout_state_root,
    })
    return result


# --------------------------------------------------------------------------
# O10B: ``wfc resume --preview`` -- the recovery decision, before it is acted
# on. Strictly read-only: no ownership lock, no Run, checkpoint or marker
# write, no journal record, no engine launch. It therefore never appears in
# an audit or a report, which is why its classification is not an
# ``EXIT_CODES`` entry.
# --------------------------------------------------------------------------

_RECOVERY_PREVIEW_WOULD_DO = (
    "retire the already-reconciled predecessor dispatch marker, move this identical "
    "Run back to `ready` at its unchanged phase, charge no further Hop for the "
    "recovery itself, and continue the engine's own Hop loop at the next Hop"
)
_RECOVERY_PREVIEW_HEADING = "RECOVERY PREVIEW (nothing was changed)"


def _recovery_preview_live_facts(repository: Path, run: Run) -> list[str]:
    """The two cheap live Git facts, observed read-only, so a preview that
    says "would proceed" is not contradicted by the first refusal the real
    resume would hit. Every remaining live element (Commission and
    implementation identity, engine availability, the checkout lock) is
    named as still unchecked rather than guessed at."""

    try:
        facts = observe_repository(repository)
    except (GitPreflightError, OSError) as error:
        return [f"  live facts        could not be observed: {error}"]
    clean = facts.clean and not facts.unresolved_submodules
    return [
        f"  live head         {facts.head}"
        + ("  (matches)" if facts.head == run.current_head else "  (DIFFERS from the Run's end HEAD)"),
        f"  live worktree     {'clean' if clean else 'NOT clean with resolved submodules'}",
    ]


def _recovery_preview_block(
    repository: Path, run: Run, diagnosis: _RecoveryDiagnosis, *, classification: str,
) -> str:
    """The one visible preview block, printed before any decision is acted on."""

    open_blocking = tuple(
        finding.finding_id for finding in run.findings
        if finding.state is FindingState.OPEN and finding.blocking
    )
    lines = [
        _RECOVERY_PREVIEW_HEADING,
        f"  run id            {run.run_id}",
        f"  work item         {run.work_item_id}",
        f"  phase             {run.phase}",
        f"  classification    {classification}",
        f"  run status        {run.status.value} ({run.stop_reason})",
        f"  reconcile reason  {run.reconcile_reason}",
        f"  hops used         {run.hop_used} of {run.hop_limit} (charged; never refunded)",
        f"  open findings     {', '.join(open_blocking) if open_blocking else 'none open and blocking'}",
        f"  recovery class    {diagnosis.recovery_class or 'none -- not a recovery class'}",
    ]
    if diagnosis.refusal is not None:
        lines.append(f"  would refuse      {diagnosis.refusal}")
        lines.append(
            "  would then do     nothing at all; the Run keeps its stop and every charged Hop"
        )
    elif diagnosis.proven is not None:
        lines.append(f"  proven            {diagnosis.proven}")
        lines.extend(_recovery_preview_live_facts(repository, run))
        lines.append(f"  would then do     {_RECOVERY_PREVIEW_WOULD_DO}")
        lines.append(
            "  not yet checked   the Commission and implementation identity, the engine's "
            "availability and the checkout's ownership lock, each of which can still "
            "refuse by name"
        )
    else:
        lines.append(
            "  would then do     nothing recovery-related; `wfc resume` reports this "
            "checkout's already recorded classification unchanged"
        )
    return "\n".join(lines) + "\n"


def _preview_recovery(
    repository: Path, checkout_dir: Path, journal: Journal, out,
) -> LifecycleResult:
    """Report what ``wfc resume`` would decide for this checkout, changing nothing."""

    journaled = {
        "budget_extensions": _journaled_budget_extensions(journal),
        "recovery_events": _journaled_recovery_events(journal),
    }
    classified = journal.latest(("classified",))
    if classified is None:
        evaluated = _evaluate(repository, checkout_dir, journal)
        out.write(
            f"{_RECOVERY_PREVIEW_HEADING}\n"
            f"  classification    {evaluated.classification}\n"
            "  recovery class    none -- no classified Run to recover\n"
            "  would then do     advance this already-authorized lifecycle from its "
            "journal and filesystem facts, which is `wfc resume`'s ordinary behaviour "
            "and not a recovery\n"
        )
        return LifecycleResult(
            classification=RECOVERY_PREVIEW_CLASSIFICATION,
            message="preview only; no recovery decision is pending for this checkout",
            **journaled,
        )

    classification = str(classified.data.get("classification"))
    candidate = _recovery_candidate_run(classified)
    if candidate is None:
        out.write(
            f"{_RECOVERY_PREVIEW_HEADING}\n"
            f"  classification    {classification}\n"
            "  recovery class    none -- this classification admits no same-Run recovery\n"
            "  would then do     nothing recovery-related; `wfc resume` reports this "
            "checkout's already recorded classification unchanged\n"
        )
        return LifecycleResult(
            classification=RECOVERY_PREVIEW_CLASSIFICATION,
            message="preview only; this classification admits no same-Run recovery",
            run_id=classified.data.get("run_id"),
            run_status=classified.data.get("run_status"),
            end_head=classified.data.get("end_head"),
            checkout_state_root=classified.data.get("checkout_state_root"),
            **journaled,
        )

    _, run = candidate
    diagnosis = _diagnose_recovery(run)
    out.write(_recovery_preview_block(repository, run, diagnosis, classification=classification))
    if diagnosis.refusal is not None:
        message = f"preview only; recovery would refuse: {diagnosis.refusal}"
    elif diagnosis.proven is not None:
        message = (
            f"preview only; recovery would proceed for class {diagnosis.recovery_class} "
            "once every live fact still holds"
        )
    else:
        message = "preview only; this Run matches no same-Run recovery class"
    return LifecycleResult(
        classification=RECOVERY_PREVIEW_CLASSIFICATION, message=message,
        run_id=run.run_id, run_status=run.status.value, end_head=run.current_head,
        checkout_state_root=classified.data.get("checkout_state_root"),
        **journaled,
    )


# --------------------------------------------------------------------------
# O8, WI-WFC-BETA2-O8-001: ``wfc extend-budget`` -- the explicit, recorded
# maintainer extension of a budget-stopped Run's effective Hop ceiling
# (decision D7a; the design is the record's own ``Design`` section).
#
# Reachable only from a checkout already ``classified`` as
# ``awaiting_human_budget``; every other classification, Run state, amount
# or authority statement is refused by name and mutates nothing at all.
# A granted extension only ever *appends* journal records and raises the
# Run's own ``hop_limit`` -- it never resets or refunds a charged Hop, never
# rewrites the admitted Commission or its digest, and never reads, writes,
# opens or closes a Finding (D7b). ``wfc resume``'s mechanical-recovery
# path above is untouched by it.
# --------------------------------------------------------------------------

def _refused_budget_extension(reason: str, run: Run | None = None) -> LifecycleResult:
    return LifecycleResult(
        classification="refused_budget_extension",
        message=f"budget extension refused: {reason}",
        run_id=None if run is None else run.run_id,
        run_status=None if run is None else run.status.value,
        end_head=None if run is None else run.current_head,
    )


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _budget_extension_confirmation(
    run: Run, *, additional_hops: int, authority: str, policy_max_autonomous_hops: int,
) -> str:
    """The one visible confirmation block, printed before any mutation.

    States exactly what the human is authorizing: which Run, how far it has
    already been charged, the ceiling now and after, both bounds with this
    Run's consumption of them, and the authority statement with its digest.
    """

    bound = budget_extension_total_bound(run)
    already = run.extended_hops
    lines = [
        "BUDGET EXTENSION (recorded maintainer authority)",
        f"  run id            {run.run_id}",
        f"  work item         {run.work_item_id}",
        f"  phase             {run.phase}",
        f"  run status        {run.status.value} ({run.stop_reason})",
        f"  hops used         {run.hop_used} of {run.hop_limit} (charged; never refunded)",
        f"  admitted ceiling  {run.admitted_hop_limit}",
        f"  current ceiling   {run.hop_limit}",
        f"  additional hops   {additional_hops}",
        f"  new ceiling       {run.hop_limit + additional_hops}",
        f"  per-call bound    {BUDGET_EXTENSION_MAX_PER_CALL}",
        f"  total bound       {bound} ({already + additional_hops} granted for this Run after this extension)",
        f"  policy ceiling    {policy_max_autonomous_hops}",
        f"  authority         {authority}",
        f"  authority sha256  {hashlib.sha256(authority.encode('utf-8')).hexdigest()}",
    ]
    return "\n".join(lines) + "\n"


def _journaled_budget_extensions(journal: Journal) -> tuple[Mapping[str, Any], ...]:
    """Every granted extension recorded for this checkout, in grant order.

    Read-only, from the append-only journal: a rolled-back grant is excluded
    here (it changed nothing in the end), while its ``*_intent`` and
    ``*_rolled_back`` records stay in the journal as the attempt's own
    auditable trace.
    """

    granted: list[Mapping[str, Any]] = []
    for record in journal.read_all():
        if record.kind == "budget_extension_granted":
            granted.append(dict(record.data))
        elif record.kind == "budget_extension_rolled_back" and granted:
            # A rollback is written under the same held ownership lock as
            # the grant it undoes, and nothing else can be granted in
            # between, so it always undoes exactly the latest grant.
            granted.pop()
    return tuple(granted)


def extend_budget_lifecycle(
    repository: Path, *, additional_hops: int, authority: str, preview: bool = False,
    out=None,
) -> LifecycleResult:
    """Record one maintainer Hop-budget extension and continue the same Run.

    Returns a named ``refused_budget_extension`` result (nothing mutated) or,
    on success, the continuation's own further classification. ``preview``
    prints the identical confirmation block, takes no ownership lock and
    changes nothing.
    """

    # Resolved at call time, never bound as a default: a caller (or a test)
    # that redirects ``sys.stdout`` must see the confirmation block.
    out = sys.stdout if out is None else out
    checkout, checkout_dir, journal = _prepare_checkout(repository)
    classified = journal.latest(("classified",))
    if classified is None:
        raise LifecycleError(
            "no lifecycle recorded for this checkout; nothing to extend",
            classification="refused_no_lifecycle",
        )
    classification = classified.data.get("classification")
    if classification != "awaiting_human_budget":
        return _refused_budget_extension(
            "this checkout's recorded classification is not awaiting_human_budget: "
            f"{classification}"
        )
    checkout_state_root_value = classified.data.get("checkout_state_root")
    if not checkout_state_root_value:
        return _refused_budget_extension("the classified Run's own state root is not recorded")
    run_path = Path(checkout_state_root_value) / "run.json"
    if not run_path.is_file():
        return _refused_budget_extension(f"no Run record is present at {run_path.name}")
    try:
        run = load_run(run_path)
    except Exception as error:
        return _refused_budget_extension(f"the recorded Run is not loadable: {error}")

    state_refusal = diagnose_budget_extension_state(run)
    if state_refusal is not None:
        return _refused_budget_extension(state_refusal, run)

    authorized = journal.latest(("authorized",))
    prelaunch = journal.latest(("prelaunch_passed",))
    if authorized is None or prelaunch is None:
        return _refused_budget_extension(
            "lifecycle authorization or launch provenance is incomplete", run,
        )

    try:
        _, compiled = _cached_commission(repository, checkout_dir, authorized)
        profile = workflow_profile_from_yaml(compiled.texts[compiled.paths["profile"]])
        policy = repository_policy_from_yaml(compiled.texts[compiled.paths["policy"]])
    except Exception as error:
        return _refused_budget_extension(
            f"the governing profile or policy could not be re-derived: {error}", run,
        )
    if run.phase not in profile.phases:
        return _refused_budget_extension(
            f"the Run's recorded phase is absent from the governing profile: {run.phase}", run,
        )

    # The admitted policy ceiling, re-derived from this checkout's cached,
    # digest-verified Commission -- never read from the live
    # ``.wfc/config.yaml``, which the invoking agent can edit.
    policy_max_autonomous_hops = policy.max_autonomous_hops
    amount_refusal = diagnose_budget_extension_amount(
        run, additional_hops, policy_max_autonomous_hops=policy_max_autonomous_hops,
    )
    if amount_refusal is not None:
        return _refused_budget_extension(amount_refusal, run)
    authority_refusal = refuse_degenerate_authority(authority)
    if authority_refusal is not None:
        return _refused_budget_extension(authority_refusal, run)

    out.write(_budget_extension_confirmation(
        run, additional_hops=additional_hops, authority=authority,
        policy_max_autonomous_hops=policy_max_autonomous_hops,
    ))
    if preview:
        return LifecycleResult(
            classification=BUDGET_EXTENSION_PREVIEW_CLASSIFICATION,
            message="preview only; no extension was recorded and nothing was changed",
            run_id=run.run_id, run_status=run.status.value, end_head=run.current_head,
            checkout_state_root=str(checkout_state_root_value),
            budget_extensions=_journaled_budget_extensions(journal),
        )

    # Every remaining predicate element -- the live Git facts, the
    # Commission/envelope identity, the provider and engine executables, and
    # the pure reduction itself -- is checked, and the ownership lock taken,
    # *before* any mutation, for the same reason the mechanical-recovery path
    # above does it: a refusal must never leave the Run rewritten to `ready`
    # with its honest budget stop erased and no process to drive it.
    facts = observe_repository(repository)
    if facts.head != run.current_head:
        return _refused_budget_extension(
            "current HEAD differs from the Run's recorded end HEAD", run,
        )
    if not facts.clean or facts.unresolved_submodules:
        return _refused_budget_extension(
            "worktree is not clean with resolved submodules", run,
        )
    if not _recovery_commission_identity_ok(repository, checkout_dir, authorized):
        return _refused_budget_extension(
            "Commission or execution envelope identity changed since authorization", run,
        )
    try:
        _verify_identities_unchanged(prelaunch)
    except LifecycleError as error:
        return _refused_budget_extension(str(error), run)
    try:
        engine_argv0 = _resolve_engine_command()[0]
    except RuntimeError as error:
        return _refused_budget_extension(str(error), run)
    if not os.path.isfile(engine_argv0) or not os.access(engine_argv0, os.X_OK):
        return _refused_budget_extension(
            f"no engine executable is available for a continuation: {engine_argv0}", run,
        )
    granted_at = _utc_timestamp()
    try:
        extended = extend_hop_budget(
            run, additional_hops=additional_hops, authority=authority,
            granted_at=granted_at, policy_max_autonomous_hops=policy_max_autonomous_hops,
        )
    except ReductionError as error:
        return _refused_budget_extension(f"budget extension re-verification failed: {error}", run)

    lock = try_acquire_lock(checkout_dir)
    if lock is None:
        return _refused_budget_extension(
            "active lifecycle already running for this checkout", run,
        )
    grant = extended.budget_extensions[-1]
    grant_facts = {
        "run_id": run.run_id, "work_item_id": run.work_item_id,
        "checkout_state_root": checkout_state_root_value,
        "sequence": grant.sequence, "granted_at": grant.granted_at,
        "additional_hops": grant.additional_hops,
        "hop_limit_before": grant.hop_limit_before, "hop_limit_after": grant.hop_limit_after,
        "hop_used_at_grant": grant.hop_used_at_grant,
        "run_status_at_grant": grant.run_status_at_grant,
        "stop_reason_at_grant": grant.stop_reason_at_grant,
        "current_head_at_grant": grant.current_head_at_grant,
        "authority": grant.authority, "authority_sha256": grant.authority_sha256,
        "phase": run.phase,
    }
    try:
        journal.append("budget_extension_intent", grant_facts)
        write_run_snapshot(run_path, extended)
        atomic_write_json(
            Path(checkout_state_root_value) / "checkpoint.json",
            checkpoint_projection(extended, profile),
        )
        journal.append("budget_extension_granted", grant_facts)
        _, process = _do_continuation_launch(
            repository, checkout_dir, journal, lock, authorized, prelaunch,
            current_head=facts.head, record_prefix="budget_extension",
            refusal_classification="refused_budget_extension", what="budget extension",
        )
    except LifecycleError as error:
        # The continuation engine resolved at the pre-mutation check and then
        # failed to spawn: roll the Run and checkpoint back to their
        # pre-extension state so the Run keeps its honest budget stop rather
        # than sitting in `ready` with no process driving it. The journal
        # stays append-only -- the grant record and this rollback record are
        # both kept, so the attempt is auditable -- and because the grant is
        # no longer in the Run it also does not consume the total bound.
        write_run_snapshot(run_path, run)
        atomic_write_json(
            Path(checkout_state_root_value) / "checkpoint.json",
            checkpoint_projection(run, profile),
        )
        journal.append("budget_extension_rolled_back", {
            "run_id": run.run_id, "sequence": grant.sequence, "error": str(error),
        })
        release_lock(lock)
        raise
    process.wait()
    release_lock(lock)

    engine_state_root = Path(prelaunch.data["engine_state_root"])
    continuation_classification, continued_run = _classify_from_state_root(repository, engine_state_root)
    if continuation_classification == "inconsistent":
        raise LifecycleError(
            "engine/journal facts are contradictory after a budget-extension continuation",
            classification="inconsistent",
        )
    association = json.loads((engine_state_root / "checkout-association.json").read_text(encoding="utf-8"))
    run_root = engine_state_root / "checkouts" / association["checkout_id"]
    result = LifecycleResult(
        classification=continuation_classification,
        message=(
            f"Hop budget extended by {grant.additional_hops} to {grant.hop_limit_after} on "
            f"recorded maintainer authority; classified: {continuation_classification}"
        ),
        run_id=continued_run.run_id, run_status=continued_run.status.value,
        end_head=continued_run.current_head,
        state_root=str(engine_state_root), checkout_state_root=str(run_root),
        budget_extensions=_journaled_budget_extensions(journal),
    )
    journal.append("classified", {
        "classification": result.classification, "run_id": result.run_id, "run_status": result.run_status,
        "end_head": result.end_head, "state_root": result.state_root, "checkout_state_root": result.checkout_state_root,
    })
    preservation.run_pending_preservation(repository, checkout_dir, journal)
    postrun_status = postrun.run_pending_postrun(repository, checkout_dir, journal)
    return _apply_postrun(result, postrun_status)


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
    journal = Journal(journal_path(checkout_dir))
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


def resume_lifecycle(repository: Path, *, preview: bool = False, out=None) -> LifecycleResult:
    checkout, checkout_dir, journal = _prepare_checkout(repository)
    if preview:
        # O10B: strictly read-only, and returns before `_advance` can take the
        # ownership lock or any other step can perform an effect. ``out`` is
        # resolved at call time, never bound as a default, so a caller (or a
        # test) that redirects ``sys.stdout`` sees the preview block.
        return _preview_recovery(
            repository, checkout_dir, journal, sys.stdout if out is None else out,
        )
    classified = journal.latest(("classified",))
    if classified is not None:
        # B, WI-WFC-RECOVERY-ROUTING-001: attempt mechanical recovery before
        # ``_advance``'s own unconditional "lifecycle already classified
        # (no-op)" shortcut would otherwise apply. ``None`` means this
        # checkout's classification was never in scope (every other
        # classification, and an "awaiting_human"/"reconcile_required" Run
        # that is not the honest mechanical-recoverable class): fall through
        # to the unchanged no-op path below, exactly as before this
        # WorkItem. A named refusal changes nothing, so preservation/postrun
        # (which key off the *cached* classification, unaffected by a
        # refusal) are skipped; a successful recovery's own further
        # ``classified`` record is handled identically to any other.
        recovered = _attempt_mechanical_recovery(repository, checkout_dir, journal, classified)
        if recovered is not None:
            if recovered.classification == "refused_not_recoverable":
                return recovered
            preservation.run_pending_preservation(repository, checkout_dir, journal)
            postrun_status = postrun.run_pending_postrun(repository, checkout_dir, journal)
            return _apply_postrun(recovered, postrun_status)
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
    parser.add_argument(
        "--hop-timeout-seconds", type=float, default=None,
        help=(
            "per-Hop timeout in seconds for the engine process; a CAP meant only to end a "
            "genuinely hung provider invocation, never a budget. Precedence: this flag, then "
            "the hop_timeout_seconds key in .wfc/config.yaml, then the built-in default of "
            f"{int(DEFAULT_HOP_TIMEOUT_SECONDS)} seconds (4 hours)."
        ),
    )
    parser.add_argument("--yes", action="store_true")
    return parser.parse_args(argv)


def _parse_simple_args(prog: str, argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--repository", default=".")
    return parser.parse_args(argv)


def _parse_extend_budget_args(argv: list[str]) -> argparse.Namespace:
    """O8: deliberately no ``--yes``.

    The launch ritual's ``--yes`` is an authorisation *gesture*; this command
    asks for an explicit amount and a statement of the maintainer's own
    instruction instead, and refuses a gesture in its place. Both value
    arguments are required: there is no default amount and no default
    authority statement (see `FTR-WFC-STOPPED-RUN-CONTINUATION-ECONOMY`'s
    Design section, "What this cannot enforce, stated honestly").
    """

    parser = argparse.ArgumentParser(
        prog="wfc extend-budget",
        description=(
            "Record an explicit maintainer extension of a budget-stopped Run's Hop "
            "ceiling and continue the same Run. Used Hops are never reset or refunded, "
            "the admitted Commission and its digest are never rewritten, and only the "
            "independent reviewer may ever dispose of a Finding. An agent never extends "
            "a budget without the maintainer's explicit instruction in the current "
            "session, and says so in its report."
        ),
    )
    parser.add_argument("--repository", default=".")
    parser.add_argument(
        "--additional-hops", type=int, required=True, metavar="N",
        help=(
            "how many autonomous Hops to add to this Run's effective ceiling; "
            f"at most {BUDGET_EXTENSION_MAX_PER_CALL} per call, and in total at most "
            "half the ceiling the Commission itself admitted"
        ),
    )
    parser.add_argument(
        "--authority", required=True, metavar="STATEMENT",
        help=(
            "the maintainer's own instruction, quoted, recorded verbatim in the Run, "
            "the journal and the archive; a bare authorisation token is refused"
        ),
    )
    parser.add_argument(
        "--preview", action="store_true",
        help="print the confirmation block and change nothing at all",
    )
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
    _print_budget_extensions(result)
    _print_recovery_events(result)


def _print_recovery_events(result: LifecycleResult) -> None:
    """O10B: report-only; one block per same-Run recovery actually performed.

    Printed by every surface that prints a ``LifecycleResult``, so a
    recovery of this checkout's Run is never invisible on the command an
    operator actually runs.
    """

    for index, event in enumerate(result.recovery_events, start=1):
        print(f"RECOVERY {index}:")
        print(f"  class: {event.get('recovery_class')}")
        print(f"  run id: {event.get('run_id')}  phase: {event.get('phase')}")
        if event.get("interrupted_hop_id"):
            print(f"  interrupted hop: {event.get('interrupted_hop_id')} (charged; never replayed)")
        print(
            f"  resumed from: {event.get('from_status')} at "
            f"{event.get('hop_used_at_resume')} of {event.get('hop_limit_at_resume')} hops "
            "(charged; never refunded)"
        )
        print(f"  proven: {_stdout_safe(str(event.get('proven')))}")


def _print_budget_extensions(result: LifecycleResult) -> None:
    """O8: report-only; one block per recorded maintainer Hop-budget grant.

    Printed by every surface that prints a ``LifecycleResult``, so an
    extension is never invisible on the command an operator actually runs.
    """

    for extension in result.budget_extensions:
        print(f"BUDGET EXTENSION {extension.get('sequence')}:")
        print(
            f"  additional hops: {extension.get('additional_hops')} "
            f"({extension.get('hop_limit_before')} -> {extension.get('hop_limit_after')})"
        )
        print(f"  hops used at grant: {extension.get('hop_used_at_grant')} (charged; never refunded)")
        print(f"  granted at: {extension.get('granted_at')}")
        print(f"  run state at grant: {extension.get('run_status_at_grant')} ({extension.get('stop_reason_at_grant')})")
        print(f"  authority: {_stdout_safe(str(extension.get('authority')))}")
        print(f"  authority sha256: {extension.get('authority_sha256')}")


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


def _resolve_hop_timeout_seconds(flag_value: float | None, repository: Path, config_path: str) -> float:
    """Precedence: the ``--hop-timeout-seconds`` flag, then ``.wfc/config.yaml``'s
    ``hop_timeout_seconds``, then ``DEFAULT_HOP_TIMEOUT_SECONDS``."""

    if flag_value is not None:
        return flag_value
    config_text = (repository / config_path).read_text(encoding="utf-8")
    config = consumer_config_from_yaml(config_text)
    if config.hop_timeout_seconds is not None:
        return config.hop_timeout_seconds
    return DEFAULT_HOP_TIMEOUT_SECONDS


def cmd_run(argv: list[str]) -> int:
    args = _parse_run_args(argv)
    try:
        repository = repository_identity(Path(args.repository))
        overrides = execution_overrides_from_flags(args.execution_override)
        hop_timeout_seconds = _resolve_hop_timeout_seconds(args.hop_timeout_seconds, repository, args.config)
        inputs = RunInputs(
            repository=repository, config_path=args.config, request_path=Path(args.request).resolve(),
            target_dir=args.target_dir, overrides=overrides, executable=args.executable,
            hop_timeout_seconds=hop_timeout_seconds, confirm=_make_confirm(yes=args.yes),
        )
        result = run_lifecycle(inputs)
    except (LifecycleError, CommissionError, GitPreflightError, JournalError, OSError) as error:
        classification = getattr(error, "classification", "refused_prelaunch")
        print(f"wfc run: {error}", file=sys.stderr)
        return EXIT_CODES.get(classification, 2)
    _print_result(result)
    _print_postrun_notice(result)
    return result.exit_code


def _parse_resume_args(argv: list[str]) -> argparse.Namespace:
    """O10B: ``wfc resume`` gains exactly one flag, ``--preview``.

    Deliberately not a separate ``wfc preview-recovery`` subcommand: a
    preview of a command belongs to that command, as O8 already decided for
    ``wfc extend-budget --preview``; and deliberately not the default, which
    would silently change ``wfc resume``'s behaviour for every other
    classification.
    """

    parser = argparse.ArgumentParser(
        prog="wfc resume",
        description=(
            "Continue an already-authorized checkout from its journal and filesystem "
            "facts. For a Run whose own recorded facts satisfy one of the two same-Run "
            "recovery predicates -- a recoverable durable-artifact anomaly, or a "
            "proven-quiescent post-dispatch interruption -- it recovers that identical "
            "Run: the stopped Hop stays charged, the interrupted invocation is never "
            "replayed, and every other stop keeps refusing by name."
        ),
    )
    parser.add_argument("--repository", default=".")
    parser.add_argument(
        "--preview", action="store_true",
        help=(
            "print the recovery decision -- the class, what was proven or which element "
            "would refuse, and what resume would then do -- and change nothing at all"
        ),
    )
    return parser.parse_args(argv)


def cmd_resume(argv: list[str]) -> int:
    args = _parse_resume_args(argv)
    try:
        repository = repository_identity(Path(args.repository))
        result = resume_lifecycle(repository, preview=args.preview)
    except (LifecycleError, CommissionError, GitPreflightError, JournalError, OSError) as error:
        classification = getattr(error, "classification", "refused_prelaunch")
        print(f"wfc resume: {error}", file=sys.stderr)
        return EXIT_CODES.get(classification, 2)
    _print_result(result)
    _print_postrun_notice(result)
    if result.classification == RECOVERY_PREVIEW_CLASSIFICATION:
        # A preview reports successfully that it changed nothing; it is not
        # a Run outcome and therefore carries no EXIT_CODES value.
        return 0
    return result.exit_code


def cmd_extend_budget(argv: list[str]) -> int:
    args = _parse_extend_budget_args(argv)
    try:
        repository = repository_identity(Path(args.repository))
        result = extend_budget_lifecycle(
            repository, additional_hops=args.additional_hops, authority=args.authority,
            preview=args.preview,
        )
    except (LifecycleError, CommissionError, GitPreflightError, JournalError, OSError) as error:
        classification = getattr(error, "classification", "refused_budget_extension")
        print(f"wfc extend-budget: {error}", file=sys.stderr)
        return EXIT_CODES.get(classification, 2)
    _print_result(result)
    _print_postrun_notice(result)
    if result.classification == BUDGET_EXTENSION_PREVIEW_CLASSIFICATION:
        # A preview reports successfully that it changed nothing; it is not
        # a Run outcome and therefore carries no EXIT_CODES value.
        return 0
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
    if args[:1] == ["extend-budget"]:
        return cmd_extend_budget(args[1:])
    print("wfc: expected one of: run, resume, status, extend-budget", file=sys.stderr)
    return 2
