"""BP4b: bounded, non-authoritative candidate preservation on the installed lifecycle.

Uses only BP4a's published contract -- the ownership-lock query
(``journal.query_lock_held`` and the ``try_acquire_lock``/``release_lock``
pair), the ``classified`` journal record, and opaque ``ext.*`` journal
records accepted after ``classified`` -- and adds no new BP4a transition and
changes no BP4a record, classification or exit code. Preservation is never
adoption, acceptance, scope widening or Finding transfer and gives no credit
toward review; it does not replace the manual salvage/successor procedure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .. import git_facts
from .journal import (
    Journal,
    canonical_checkout,
    checkout_directory,
    release_lock,
    require_disjoint_journal_root,
    state_home,
    try_acquire_lock,
)
from .request import consumer_request_from_yaml

_TRIGGER_CLASSIFICATIONS = frozenset({
    "interrupted", "awaiting_human", "awaiting_human_budget", "reconcile_required", "stopped",
})

_TERMINAL_KINDS = ("ext.preservation.result", "ext.preservation.not_needed", "ext.preservation.refused")


@dataclass(frozen=True)
class PreservationStatus:
    """The latest durable BP4b fact for one checkout, or its absence.

    ``kind`` is one of ``"absent"`` (nothing recorded, or preservation does
    not apply to the current classification), ``"result"``, ``"not_needed"``
    or ``"refused"``.
    """

    kind: str
    data: Mapping[str, Any]


def _latest_fact(journal: Journal) -> PreservationStatus:
    record = journal.latest(_TERMINAL_KINDS)
    if record is None:
        return PreservationStatus(kind="absent", data={})
    return PreservationStatus(kind=record.kind.rsplit(".", 1)[-1], data=dict(record.data))


def _authorized_scope(checkout_dir: Path) -> tuple[str, ...]:
    request_text = (checkout_dir / "commission" / "request.yaml").read_text(encoding="utf-8")
    return consumer_request_from_yaml(request_text).scope


def run_pending_preservation(repository: Path, checkout_dir: Path, journal: Journal) -> PreservationStatus:
    """Idempotently run the one pending capture step for a just-classified Run.

    Called by ``wfc run``/``wfc resume`` after ``classified`` is recorded;
    never by ``wfc status``, which only reads the facts this writes. Safe to
    call again after a crash: once a terminal ``ext.preservation.*`` record
    exists for this checkout it is returned as-is and no further capture is
    attempted; a crash between ref creation and the result record is resolved
    by ``git_facts.preserve_candidate`` itself finding the already-created
    ref by identical tree, so a retry here records the result without a
    second ref.
    """

    classified = journal.latest(("classified",))
    if classified is None:
        return PreservationStatus(kind="absent", data={})
    data = classified.data
    run_id = data.get("run_id")
    end_head = data.get("end_head")
    checkout_state_root = data.get("checkout_state_root")
    if (
        data.get("classification") not in _TRIGGER_CLASSIFICATIONS
        or not run_id or not end_head or not checkout_state_root
    ):
        return PreservationStatus(kind="absent", data={})

    existing = _latest_fact(journal)
    if existing.kind != "absent":
        return existing

    lock = try_acquire_lock(checkout_dir)
    if lock is None:
        refusal = {"run_id": run_id, "reason": "ownership lock held"}
        journal.append("ext.preservation.refused", refusal)
        return PreservationStatus(kind="refused", data=refusal)
    try:
        journal.append("ext.preservation.intent", {"run_id": run_id})
        try:
            outcome = git_facts.preserve_candidate(
                repository, expected_repository=repository, base_head=end_head,
                allowed_paths=_authorized_scope(checkout_dir), run_id=run_id,
            )
        except git_facts.PreservationRefused as error:
            refusal = {"run_id": run_id, "reason": str(error)}
            journal.append("ext.preservation.refused", refusal)
            return PreservationStatus(kind="refused", data=refusal)
    finally:
        release_lock(lock)

    if outcome.status == "not_needed":
        fact = {"run_id": run_id}
        journal.append("ext.preservation.not_needed", fact)
        return PreservationStatus(kind="not_needed", data=fact)

    fact = {
        "run_id": run_id, "ref": outcome.ref, "commit": outcome.commit, "tree": outcome.tree,
        "file_count": outcome.file_count, "bytes": outcome.bytes_total, "quiescence": outcome.quiescence,
    }
    journal.append("ext.preservation.result", fact)
    return PreservationStatus(kind="result", data=fact)


def latest_preservation_status(repository: Path) -> PreservationStatus:
    """Read-only: the latest preservation fact for this checkout, for ``wfc status``."""

    checkout = canonical_checkout(repository)
    root = state_home()
    require_disjoint_journal_root(root, checkout)
    checkout_dir = checkout_directory(checkout, root=root)
    journal = Journal(checkout_dir / "journal.jsonl")
    return _latest_fact(journal)
