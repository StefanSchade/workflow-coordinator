"""BP6c: the post-classification archive/audit/report step on the installed
lifecycle.

Runs once per checkout, after ``classified`` and after BP4b's candidate
preservation, from ``wfc run``/``wfc resume`` only -- never from ``wfc
status``, which only reads the facts this writes (``latest_postrun_status``).
Uses only already-published BP4a/BP4b/BP5/BP6a/BP6b contracts: the
``classified`` and ``ext.preservation.*`` journal facts, the checkout's
cached Consumer Config, ``frontdoor.config.resolve_evidence``,
``frontdoor.archive.archive_run`` and ``frontdoor.report.build_report``. It
adds no new BP4a transition and changes no BP4a/BP4b record, classification
or exit code; the engine outcome always stands.

Identity decision (maintainer, binding): the Attempt identity this module
passes to ``archive_run`` is derived from the Consumer Config's
``repository_name`` and the Run id recorded in the ``classified`` journal
record -- never from the engine state root's own directory name (BP4a
always names that ``engine-state``, identical across every checkout on a
machine). ``_attempt_identity`` is deterministic, stable across ``resume``
and repeated archiving of the same Run, and restricted to the same safe
character set ``archive.validate_explicit_attempt_identity`` requires.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..git_facts import GitPreflightError
from . import archive
from . import audit as audit_module
from . import report as report_module
from .config import consumer_config_from_yaml, resolve_evidence
from .journal import (
    Journal,
    canonical_checkout,
    checkout_directory,
    require_disjoint_journal_root,
    state_home,
)

_ARCHIVABLE_CLASSIFICATIONS = frozenset({
    "completed", "awaiting_human", "awaiting_human_budget", "reconcile_required",
    "stopped", "interrupted",
})

_PRESERVATION_TERMINAL_KINDS = ("ext.preservation.result", "ext.preservation.not_needed", "ext.preservation.refused")
_POSTRUN_TERMINAL_KINDS = ("ext.archive.result", "ext.archive.refused", "ext.archive.skipped")

_ATTEMPT_IDENTITY_UNSAFE_RE = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True)
class PostrunStatus:
    """The latest durable BP6c fact for one checkout, or its absence.

    ``kind`` is one of ``"absent"`` (nothing recorded, or the step does not
    apply to the current classification), ``"result"``, ``"refused"`` or
    ``"skipped"``. ``notice`` is a human-facing, print-only line (never
    persisted); ``report_text`` is the consolidated report text, present only
    when ``kind == "result"``.
    """

    kind: str
    data: Mapping[str, Any]
    report_text: str | None = None
    notice: str | None = None


def _latest_fact(journal: Journal) -> PostrunStatus:
    record = journal.latest(_POSTRUN_TERMINAL_KINDS)
    if record is None:
        return PostrunStatus(kind="absent", data={})
    return PostrunStatus(kind=record.kind.rsplit(".", 1)[-1], data=dict(record.data))


def _cached_config(checkout_dir: Path):
    config_text = (checkout_dir / "commission" / "config.yaml").read_text(encoding="utf-8")
    return consumer_config_from_yaml(config_text)


def _attempt_identity(repository_name: str, run_id: str) -> str:
    """Deterministic, Run-derived Attempt identity for the product path:
    ``<repository_name>-<run_id>``, lower-cased and restricted to
    ``[a-z0-9._-]`` -- never derived from the engine state root's own
    directory name. Stable across ``resume`` and repeated archiving of the
    same Run, since both inputs come from facts already frozen at
    authorization (``repository_name``) and classification (``run_id``)."""

    raw = f"{repository_name}-{run_id}".lower()
    candidate = _ATTEMPT_IDENTITY_UNSAFE_RE.sub("-", raw).strip("-")
    if not candidate:
        raise archive.ArchiveError(
            f"cannot derive a usable attempt identity from repository_name={repository_name!r} "
            f"and run_id={run_id!r}"
        )
    return candidate


def _preservation_fact(journal: Journal) -> dict | None:
    """The latest BP4b fact, reshaped for ``frontdoor.audit``'s ``lifecycle.preservation``
    allow-list (read-only; never mutates BP4b's own journal record)."""

    record = journal.latest(_PRESERVATION_TERMINAL_KINDS)
    if record is None:
        return None
    kind = record.kind.rsplit(".", 1)[-1]
    data = record.data
    if kind == "result":
        return {
            "outcome": "result", "ref": data.get("ref"), "commit": data.get("commit"),
            "tree": data.get("tree"), "file_count": data.get("file_count"),
            "bytes": data.get("bytes"), "quiescence": data.get("quiescence"),
        }
    if kind == "refused":
        return {"outcome": "refused", "reason_code": data.get("reason")}
    return {"outcome": "not_needed"}


def _reason_code_for(error: Exception) -> str:
    if isinstance(error, audit_module.ProjectionError):
        return "privacy_guard_refused"
    if isinstance(error, audit_module.AuditError):
        return "audit_structural_error"
    if isinstance(error, archive.ArchiveError):
        return "archive_refused"
    return "git_preflight_failed"


def _skip_notice(evidence_path: Path) -> str:
    return (
        f"ARCHIVE SKIPPED: no Evidence repository at {evidence_path}; "
        "run `wfc init` against that location (or configure evidence.repository), "
        "then `wfc resume` to archive this Attempt."
    )


def _failure_notice(reason_code: str, error: Exception) -> str:
    return f"ARCHIVE FAILED ({reason_code}): {error}; resolve the cause, then `wfc resume` to retry."


def _read_report_text(evidence_repo: Path, fact: Mapping[str, Any]) -> str | None:
    destination_relative = fact.get("destination_relative")
    if not destination_relative:
        return None
    audit_path = evidence_repo / destination_relative / archive.AUDIT_FILENAME
    if not audit_path.is_file():
        return None
    try:
        audit_doc = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    commit = fact.get("commit")
    archive_ref = f"{destination_relative}@{commit}" if commit else destination_relative
    built = report_module.build_report(audit_doc, archive_ref=archive_ref)
    return report_module.render_report_text(built)


def run_pending_postrun(repository: Path, checkout_dir: Path, journal: Journal) -> PostrunStatus:
    """Idempotently run the one pending post-run step for a just-classified Run.

    Called by ``wfc run``/``wfc resume`` after ``classified`` (and BP4b's
    preservation step) is recorded; never by ``wfc status``. Only a recorded
    ``ext.archive.result`` is cached and returned without recomputation (the
    consolidated report is still recomputed, read-only, from the
    already-placed ``audit.json``, per the no-second-side-effect rule). A
    prior ``ext.archive.skipped`` or ``ext.archive.refused`` fact is never
    cached -- the whole point of those outcomes is that the next `wfc
    resume` tries again, so every call that has not yet succeeded re-attempts
    the step. A crash between the archive commit and the result record is
    resolved by ``frontdoor.archive.archive_run`` itself finding the
    already-committed destination (its own idempotent no-op/resume
    detection), so a retry here records the result without a second commit.
    """

    classified = journal.latest(("classified",))
    if classified is None:
        return PostrunStatus(kind="absent", data={})
    data = classified.data
    classification = data.get("classification")
    run_id = data.get("run_id")
    state_root = data.get("state_root")
    if classification not in _ARCHIVABLE_CLASSIFICATIONS or not run_id or not state_root:
        return PostrunStatus(kind="absent", data={})

    config = _cached_config(checkout_dir)
    settings = resolve_evidence(config, repository)

    result_record = journal.latest(("ext.archive.result",))
    if result_record is not None:
        fact = dict(result_record.data)
        report_text = _read_report_text(settings.path, fact)
        return PostrunStatus(kind="result", data=fact, report_text=report_text)

    if not settings.path.is_dir():
        fact = {"reason": "evidence_repository_absent"}
        journal.append("ext.archive.skipped", fact)
        return PostrunStatus(kind="skipped", data=fact, notice=_skip_notice(settings.path))

    lifecycle_facts = {"classification": classification, "preservation": _preservation_fact(journal)}
    journal.append("ext.archive.intent", {"run_id": run_id})
    try:
        attempt_identity = _attempt_identity(config.repository_name, run_id)
        archived = archive.archive_run(
            state_root=Path(state_root), evidence_repo=settings.path,
            raw_retention=settings.raw, lifecycle=lifecycle_facts, attempt_identity=attempt_identity,
        )
    except (archive.ArchiveError, audit_module.ProjectionError, audit_module.AuditError, GitPreflightError) as error:
        reason_code = _reason_code_for(error)
        fact = {"reason_code": reason_code}
        journal.append("ext.archive.refused", fact)
        return PostrunStatus(kind="refused", data=fact, notice=_failure_notice(reason_code, error))

    fact = {
        "outcome": archived.outcome, "commit": archived.commit,
        "destination_relative": archived.destination_relative, "retention": settings.raw,
    }
    journal.append("ext.archive.result", fact)
    report_text = _read_report_text(settings.path, fact)
    return PostrunStatus(kind="result", data=fact, report_text=report_text)


def latest_postrun_status(repository: Path) -> PostrunStatus:
    """Read-only: the latest ``ext.archive.*`` fact for this checkout, for ``wfc status``."""

    checkout = canonical_checkout(repository)
    root = state_home()
    require_disjoint_journal_root(root, checkout)
    checkout_dir = checkout_directory(checkout, root=root)
    journal = Journal(checkout_dir / "journal.jsonl")
    return _latest_fact(journal)
