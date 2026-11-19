"""Durable, append-only lifecycle journal and the per-checkout ownership lock.

The journal is the only durable memory ``frontdoor.lifecycle`` has across
process death: every step it takes writes an "intent" record before the
step's real-world effect and a completion record after, so a later ``resume``
can tell exactly how far a previous, now-dead controller got.  Nothing here
decides workflow semantics; it only stores and replays facts.

Location: ``$XDG_STATE_HOME/wfc`` (fallback ``~/.local/state/wfc``), keyed by
a stable identity of the *canonical* checkout -- the resolved Git top-level
plus the resolved Git common directory -- so two different working-tree
paths that are actually the same checkout (for example reached through a
symlink) share one identity, while two distinct checkouts never collide.

The on-disk journal format is deliberately not "one JSON object per line":
each record is framed with its own encoded byte length and a SHA-256 digest
of those bytes, so a reader can tell a record that was *fully* written from
one truncated mid-``write`` by a crash.  Only the physically last record in
the file may ever be torn (an ``fsync``-before-append discipline guarantees
every earlier record was durable before the next one began); a torn last
record is silently dropped, but any other framing or digest mismatch is
reported as corruption and refuses the whole journal, since that cannot be
explained by an ordinary crash.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

_RECORD_HEADER_SEP = " "


class JournalError(RuntimeError):
    """The journal cannot be read or written safely."""


class JournalCorruptionError(JournalError):
    """A non-tail journal record failed its own length/digest check."""


class JournalRootError(JournalError):
    """The configured journal root is not a safe location."""


def state_home() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured) if configured else Path.home() / ".local" / "state"
    return base / "wfc"


def _git_common_dir(repository: Path) -> Path:
    completed = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "--git-common-dir"),
        text=True, capture_output=True, check=True,
    )
    common = Path(completed.stdout.strip())
    return (repository / common).resolve() if not common.is_absolute() else common.resolve()


@dataclass(frozen=True)
class CanonicalCheckout:
    checkout_id: str
    toplevel: Path
    git_common_dir: Path


def canonical_checkout(repository: Path) -> CanonicalCheckout:
    """Identify ``repository`` by its resolved Git facts, not its given path.

    Hashing the resolved top-level *and* the resolved Git common directory
    (rather than either alone) means a symlinked working tree and a linked
    worktree both map to the one checkout identity their shared object store
    actually represents.
    """

    from ..git_facts import repository_identity

    toplevel = repository_identity(repository)
    common = _git_common_dir(toplevel)
    digest = hashlib.sha256(f"{toplevel}\n{common}".encode("utf-8")).hexdigest()
    return CanonicalCheckout(checkout_id=digest, toplevel=toplevel, git_common_dir=common)


def _is_within_or_equal(candidate: Path, boundary: Path) -> bool:
    return candidate == boundary or boundary in candidate.parents


def _disjoint(a: Path, b: Path) -> bool:
    return not (_is_within_or_equal(a, b) or _is_within_or_equal(b, a))


def require_disjoint_journal_root(journal_root: Path, checkout: CanonicalCheckout) -> None:
    """Refuse a journal root that is, contains, or is contained by the repository."""

    resolved = journal_root.resolve()
    if not _disjoint(resolved, checkout.toplevel) or not _disjoint(resolved, checkout.git_common_dir):
        raise JournalRootError(
            f"journal root is not disjoint from the repository: {resolved}"
        )


def checkout_directory(checkout: CanonicalCheckout, *, root: Path | None = None) -> Path:
    base = root if root is not None else state_home()
    return base / "checkouts" / checkout.checkout_id


@dataclass(frozen=True)
class JournalRecord:
    kind: str
    data: Mapping[str, Any]
    seq: int
    ts: float


def _record_bytes(kind: str, data: Mapping[str, Any], *, seq: int, ts: float) -> bytes:
    payload = {"kind": kind, "seq": seq, "ts": ts, "data": dict(data)}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _frame(payload: bytes) -> bytes:
    digest = hashlib.sha256(payload).hexdigest()
    header = f"{len(payload)}{_RECORD_HEADER_SEP}{digest}{_RECORD_HEADER_SEP}".encode("ascii")
    return header + payload + b"\n"


class Journal:
    """One append-only, fsync-durable record log for a single checkout."""

    def __init__(self, path: Path):
        self.path = path

    def _next_seq(self) -> int:
        return len(self.read_all()) + 1

    def append(self, kind: str, data: Mapping[str, Any] | None = None) -> JournalRecord:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        seq = self._next_seq()
        ts = time.time()
        payload = _record_bytes(kind, data or {}, seq=seq, ts=ts)
        frame = _frame(payload)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(descriptor, frame)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return JournalRecord(kind=kind, data=data or {}, seq=seq, ts=ts)

    def read_all(self) -> tuple[JournalRecord, ...]:
        """Replay every durable record, dropping only a genuinely torn tail.

        A crash can only ever shorten the file below what the record being
        appended would require (an incomplete header, or a complete header
        whose declared payload length and trailing newline are not fully
        present) -- that shape is indistinguishable from an ordinary torn
        write and is silently dropped. A record whose declared length and
        trailing newline *are* fully present but whose digest (or JSON) does
        not match cannot be produced by a crash during a single ``write()``;
        that is reported as corruption instead, wherever in the file it is.
        """

        if not self.path.exists():
            return ()
        raw = self.path.read_bytes()
        records: list[JournalRecord] = []
        offset = 0
        length = len(raw)
        sep = _RECORD_HEADER_SEP.encode("ascii")
        while offset < length:
            first_space = raw.find(sep, offset)
            if first_space == -1:
                break  # torn: header itself was cut off before completion.
            second_space = raw.find(sep, first_space + 1)
            if second_space == -1:
                break  # torn: digest field was cut off before completion.
            try:
                declared_len = int(raw[offset:first_space].decode("ascii"))
            except ValueError as error:
                raise JournalCorruptionError(f"journal corruption in {self.path}: {error}") from error
            digest_hex = raw[first_space + 1:second_space].decode("ascii")
            payload_start = second_space + 1
            payload_end = payload_start + declared_len
            if payload_end + 1 > length:
                break  # torn: payload (and/or its trailing newline) was cut off.
            if raw[payload_end:payload_end + 1] != b"\n":
                raise JournalCorruptionError(f"journal corruption in {self.path}: missing record terminator")
            payload = raw[payload_start:payload_end]
            if hashlib.sha256(payload).hexdigest() != digest_hex:
                raise JournalCorruptionError(f"journal corruption in {self.path}: digest mismatch")
            try:
                parsed = json.loads(payload.decode("utf-8"))
            except (ValueError, UnicodeError) as error:
                raise JournalCorruptionError(f"journal corruption in {self.path}: {error}") from error
            records.append(JournalRecord(
                kind=parsed["kind"], data=parsed["data"], seq=parsed["seq"], ts=parsed["ts"],
            ))
            offset = payload_end + 1
        return tuple(records)

    def latest(self, kinds: tuple[str, ...] | None = None) -> JournalRecord | None:
        records = self.read_all()
        if kinds is None:
            return records[-1] if records else None
        for record in reversed(records):
            if record.kind in kinds:
                return record
        return None

    def count(self, kind: str) -> int:
        return sum(1 for record in self.read_all() if record.kind == kind)


@dataclass(frozen=True)
class LockHandle:
    """An open file descriptor holding the per-checkout exclusive ``flock``.

    Keep this object (and its fd) alive for as long as this process may be
    the sole authority over the checkout; closing it (``release_lock``) is
    the only way to give it up early. Passing ``handle.fd`` to a child via
    ``subprocess.Popen(..., pass_fds=(handle.fd,))`` lets that child keep the
    same lock held purely by holding the same open file description, even
    after this process exits without ever calling ``release_lock``.
    """

    fd: int
    path: Path


def lock_path(checkout_dir: Path) -> Path:
    return checkout_dir / "lock"


def try_acquire_lock(checkout_dir: Path) -> LockHandle | None:
    """Attempt one non-blocking exclusive acquire; ``None`` if already held."""

    checkout_dir.mkdir(parents=True, exist_ok=True)
    path = lock_path(checkout_dir)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        return None
    return LockHandle(fd=descriptor, path=path)


def release_lock(handle: LockHandle) -> None:
    try:
        fcntl.flock(handle.fd, fcntl.LOCK_UN)
    finally:
        os.close(handle.fd)


def query_lock_held(checkout_dir: Path) -> bool:
    """Report whether some process currently holds the ownership lock.

    This is ownership evidence only: not process supervision and not a
    quiescence proof. A held lock means a process launched by some lifecycle
    attempt on this checkout may still be running. A free lock means no such
    holder remains right now -- it does *not* prove an engine was never
    started; after any launch attempt of unknown outcome, the lifecycle
    refuses to launch again rather than trust a free lock alone (DG-3,
    2026-11-19). This is the one query BP4b's candidate-preservation slice
    is published to use.
    """

    handle = try_acquire_lock(checkout_dir)
    if handle is None:
        return True
    release_lock(handle)
    return False
