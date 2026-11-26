"""Provider-neutral launcher seam between a CLI adapter and the boundary.

One pure transformation (`prepare`, dispatching on `plan.grant.mode` alone),
two implementations of it (`prepare_direct`, `prepare_bubblewrap`), and the
Coordinator-side derivation of the value they consume (`derive_boundary_grant`).
Nothing here owns process handling: each adapter keeps its own
``subprocess.Popen`` call, stream wiring and kill sequence; this module only
computes the ``argv``/``cwd``/``environment`` it should use.

``prepare_direct`` reads nothing from the grant, so an adapter constructed
without one (``boundary=None``) behaves exactly as it does without this
module at all -- which is why an unrecognised mode is a refusal, never a
silent direct launch.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from . import execution_boundary
from .execution_boundary import BoundaryConfigurationError, Mount, MountMode

DIRECT = "direct"
BUBBLEWRAP = "bubblewrap"
BOUNDARY_MODES = (DIRECT, BUBBLEWRAP)


@dataclass(frozen=True)
class BoundaryGrant:
    """Coordinator-computed per invocation; the security content of this slice.

    ``writable_host_paths`` is reserved: Slice A authorizes no writable
    mount kind outside the repository beyond the external scratch root and
    the provider channel, both carried on their own dedicated fields, so
    this must stay empty here -- ``prepare_bubblewrap`` refuses otherwise.
    """

    mode: str
    repository: Path
    writable_repository_paths: tuple[str, ...] = ()
    readable_host_paths: tuple[Path, ...] = ()
    writable_host_paths: tuple[Path, ...] = ()
    provider_channel: Path | None = None
    # True only for an adapter whose provider writes into the channel
    # (Codex); a Claude-shaped invocation's channel stays read-only.
    provider_channel_writable: bool = False
    external_scratch: Path | None = None
    tmp_size_bytes: int = execution_boundary.DEFAULT_TMP_SIZE_BYTES


@dataclass(frozen=True)
class LaunchPlan:
    """The grant, plus what the adapter itself builds.

    ``status_fd`` is the write end of the adapter's own ``StartedSignal``
    pipe (below), already opened and not yet passed to any process. It is
    *not* part of ``BoundaryGrant``: a grant is Coordinator-computed,
    invocation-shaped policy, while the fd number only exists once the
    adapter itself opens the pipe, immediately before this plan is built.
    ``prepare_bubblewrap`` requires it; ``prepare_direct`` ignores it, since
    a direct launch has no bwrap process to prove started.
    """

    grant: BoundaryGrant
    argv: tuple[str, ...]
    cwd: Path | None
    environment: Mapping[str, str] | None = None
    status_fd: int | None = None


@dataclass(frozen=True)
class PreparedLaunch:
    """What the adapter passes to ``Popen``."""

    argv: tuple[str, ...]
    cwd: Path | None
    environment: Mapping[str, str] | None
    boundary: str


def direct_grant(repository: Path) -> BoundaryGrant:
    """The carrier an adapter builds for itself when constructed with
    ``boundary=None``. ``prepare_direct`` reads nothing from it."""
    return BoundaryGrant(mode=DIRECT, repository=repository)


def normalized_writable_scope(authorized_scope: Sequence[str]) -> tuple[str, ...]:
    """Drop any entry contained in another, sort the remainder, and refuse a
    scope list that cannot be normalized to a disjoint set (N15).

    <<mounts>> states the precondition this relies on: every entry is a
    canonical relative path, with no globs and no ``..``. That precondition
    is what makes the containment-based drop below always terminate in a
    disjoint set -- for well-formed canonical relative paths, "nested inside
    another entry" is a true partial order, and the maximal elements of any
    finite partial order are always pairwise incomparable. A scope list
    cannot fail to normalize to a disjoint set *because of* containment; it
    can only fail to normalize at all, because an entry breaks the
    precondition itself (empty, absolute, or escaping through ``..``) and so
    cannot be reconciled into a repository-relative hierarchy in the first
    place. That is refused here, before any containment comparison.
    """
    deduplicated = sorted(set(authorized_scope))
    for entry in deduplicated:
        if not entry or PurePosixPath(entry).is_absolute() or '..' in PurePosixPath(entry).parts:
            raise BoundaryConfigurationError(
                f"authorized scope cannot be normalized to a disjoint set: {entry!r} is not a "
                "canonical repository-relative path"
            )
    kept: list[str] = [
        entry for entry in deduplicated
        if not any(
            entry != other and entry.startswith(f"{other.rstrip('/')}/")
            for other in deduplicated
        )
    ]
    kept.sort()
    return tuple(kept)


class AbsentWritableScopeEntryError(BoundaryConfigurationError):
    """Named, fail-closed preflight refusal (F5; maintainer disposition
    2026-11-26, see `docs/backlog/FTR-WFC-EXECUTION-BOUNDARY-FILE-SCOPE-CREATION.adoc`).

    No file extension is a reliable shape signal, so an absent scope entry
    can never be told apart from a not-yet-existing *file*; pre-creating one
    as a directory would silently make such a Commission unrunnable (a
    directory materializes where the agent meant to create a file) without
    ever naming the problem. Rather than guess, the boundary refuses by
    name, before any provider dispatch and before anything is created on the
    host: declare the path as a durable artifact for the executing phase (so
    it resolves to its nearest existing ancestor directory instead), or
    create the path -- even empty -- in the repository before dispatching
    under the boundary.
    """


def _resolve_scope_entry(repository: Path, relative: str, artifact_paths: frozenset[str]) -> str:
    """Resolve one normalized, already-existing-or-not scope entry to what
    is actually mounted (<<mounts>>, "Not-yet-existing authorized targets").

    * if it already exists (file or directory), itself;
    * otherwise, if it is a declared durable-artifact path, the nearest
      existing ancestor strictly inside the repository (the artifact's own
      directory never exists before the phase starts) -- refusing by name
      (`AbsentWritableScopeEntryError`) rather than falling back to the
      repository root itself when no such ancestor exists either;
    * otherwise (an absent entry that is not a declared artifact), refuse by
      name (`AbsentWritableScopeEntryError`): never pre-create a directory
      at a path the Commission may mean as a file, and never widen the
      grant to an ancestor the Commission never named.
    """
    target = repository / relative
    if target.exists():
        return relative
    if relative not in artifact_paths:
        raise AbsentWritableScopeEntryError(
            f"authorized writable scope entry does not exist and is not a declared durable "
            f"artifact for the executing phase: {relative!r}. Declare it as a durable artifact, or "
            "create it (even empty) in the repository, before dispatching under the execution boundary."
        )
    cursor = target.parent
    while cursor != repository and not cursor.exists():
        cursor = cursor.parent
    if cursor == repository:
        raise AbsentWritableScopeEntryError(
            f"declared durable artifact {relative!r} has no existing ancestor directory inside the "
            "repository; create at least its parent directory in the repository before dispatching "
            "under the execution boundary."
        )
    return str(cursor.relative_to(repository))


def derive_boundary_grant(
    *,
    mode: str,
    repository: Path,
    authorized_scope: Sequence[str],
    artifact_paths: Sequence[str] = (),
    provider_channel: Path | None = None,
    provider_channel_writable: bool = False,
    authentication_path: Path | None = None,
    external_scratch: Path | None = None,
    tmp_size_bytes: int = execution_boundary.DEFAULT_TMP_SIZE_BYTES,
) -> BoundaryGrant:
    """The one function both construction sites (`operator.py`'s per-Hop
    dispatch frame and `slice4.run_real_one_hop`'s default construction)
    share, so a per-adapter copy cannot drift (<<a-carrier>>).

    ``artifact_paths`` names the executing phase's own declared durable
    artifacts -- the only entries resolved to an existing ancestor directory
    when absent. Every other absent writable entry is refused by name
    (`AbsentWritableScopeEntryError`, F5), never pre-created as a directory
    and never widened to an ancestor the Commission never named.
    """
    if mode not in BOUNDARY_MODES:
        raise BoundaryConfigurationError(f"unrecognised boundary mode: {mode!r}")
    if mode == BUBBLEWRAP:
        known_artifacts = frozenset(artifact_paths)
        normalized = normalized_writable_scope(authorized_scope)
        resolved = {_resolve_scope_entry(repository, entry, known_artifacts) for entry in normalized}
        normalized = normalized_writable_scope(resolved)
    else:
        normalized = ()
    readable_host_paths = (authentication_path,) if authentication_path is not None else ()
    return BoundaryGrant(
        mode=mode,
        repository=repository,
        writable_repository_paths=normalized,
        readable_host_paths=readable_host_paths,
        provider_channel=provider_channel if mode == BUBBLEWRAP else None,
        provider_channel_writable=provider_channel_writable,
        external_scratch=external_scratch,
        tmp_size_bytes=tmp_size_bytes,
    )


def prepare(plan: LaunchPlan) -> PreparedLaunch:
    """The single entry point both adapters call. Dispatches on
    ``plan.grant.mode`` alone; an unrecognised mode is a refusal, never a
    direct launch."""
    if plan.grant.mode == DIRECT:
        return prepare_direct(plan)
    if plan.grant.mode == BUBBLEWRAP:
        return prepare_bubblewrap(plan)
    raise BoundaryConfigurationError(f"unrecognised boundary mode: {plan.grant.mode!r}")


def prepare_direct(plan: LaunchPlan) -> PreparedLaunch:
    """An unisolated invocation: byte-identical to today's. Reads nothing
    from the grant."""
    return PreparedLaunch(tuple(plan.argv), plan.cwd, plan.environment, DIRECT)


@dataclass(frozen=True)
class StartedSignal:
    """A Coordinator-owned, unidirectional pipe proving a `bwrap`-launched
    provider actually exec'd -- the trusted replacement for the former
    agent-writable marker file (F1).

    The Coordinator (never the confined process) opens this pipe with
    ``open_started_signal()`` before launch, hands the *write* end to `bwrap`
    itself via ``--json-status-fd`` (never to the sandboxed provider, and
    never inherited by it: `bwrap` does not pass the fd on when it execs the
    wrapped command), and reads the *read* end back with
    ``provider_started()`` after the process has exited. Because the write
    end is never reachable inside the view, the confined agent can neither
    write to it (there is nothing to unlink or forge) nor withdraw it.

    Semantics of what is read back (`bwrap` 0.11.0, observed): `bwrap`
    writes one JSON document per status change. A ``child-pid`` document is
    written as soon as `bwrap` forks, *before* it knows whether `exec`-ing
    the wrapped command will even succeed -- so its presence alone proves
    nothing about whether the provider started. An ``exit-code`` document is
    written only once the wrapped command has actually been exec'd *and* has
    exited; its presence is therefore the proof this slice relies on.
    ``provider_started()`` returns whether an ``exit-code`` document was
    observed. The pipe is deliberately never consulted when the adapter's
    own timeout/kill sequence fired (``timed_out``): a `bwrap` killed by
    ``SIGKILL`` before it can write anything, or a provider killed by the
    forwarded ``SIGTERM``, both leave the classification question moot --
    the adapter already has a definite, differently-labelled outcome
    (``timed_out=True``) for that case, and must report it as the timeout it
    is rather than reclassify it as "not started" from an absent document
    that a kill, not a setup failure, explains.
    """

    read_fd: int
    write_fd: int


def open_started_signal() -> StartedSignal:
    """Open the pipe. The caller passes ``write_fd`` to the `bwrap` process
    alone (``pass_fds=(write_fd,)``) and must close its own copy immediately
    after spawning (``close_started_signal_write_end``), so that the pipe's
    write end is held open by nothing but `bwrap` itself and reading EOFs
    reliably once `bwrap` has exited -- never blocking, because by the time
    ``provider_started`` is called the process has already been waited on."""
    read_fd, write_fd = os.pipe()
    return StartedSignal(read_fd, write_fd)


def close_started_signal_write_end(signal: StartedSignal) -> None:
    os.close(signal.write_fd)


_STATUS_FD_MAX_BYTES = 65_536


def provider_started(signal: StartedSignal) -> bool:
    """Drain every JSON document `bwrap` wrote to the status pipe and
    return whether an ``exit-code`` document was among them.

    Must be called only after the `bwrap` process has already been waited
    on (so its copy of the write end, the only one besides the Coordinator's
    own already-closed copy, is gone and the kernel reports EOF): the read
    below is therefore bounded both by that EOF and by a fixed byte cap,
    never by an open-ended blocking wait on a live process.
    """
    chunks = []
    total = 0
    while total < _STATUS_FD_MAX_BYTES:
        chunk = os.read(signal.read_fd, 4096)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    raw = b"".join(chunks).decode("utf-8", errors="replace")
    decoder = json.JSONDecoder()
    index = 0
    length = len(raw)
    started = False
    while index < length:
        while index < length and raw[index].isspace():
            index += 1
        if index >= length:
            break
        try:
            document, index = decoder.raw_decode(raw, index)
        except ValueError:
            break
        if isinstance(document, dict) and "exit-code" in document:
            started = True
    return started


def validate_grant(grant: BoundaryGrant) -> execution_boundary.ValidatedBoundary:
    """Validate a bubblewrap grant's mount set, without building argv.

    Shared by the Coordinator's own preflight (run once the pre-create
    bracket has materialised every missing writable target, and before any
    provider dispatch) and by ``prepare_bubblewrap`` below, so the two
    cannot drift. Raises ``BoundaryConfigurationError`` -- never called for
    a direct-mode grant.
    """
    if grant.writable_host_paths:
        raise BoundaryConfigurationError(
            "no writable host mount kind beyond the external scratch root and the provider "
            "channel is authorized in Slice A"
        )
    repository = grant.repository
    mounts: list[Mount] = [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)]
    for relative in grant.writable_repository_paths:
        source = repository / relative
        mounts.append(Mount(source, PurePosixPath(str(source)), MountMode.WRITABLE))
    return execution_boundary.validate(
        repository, mounts,
        external_scratch=grant.external_scratch,
        provider_channel=grant.provider_channel,
        provider_channel_writable=grant.provider_channel_writable,
        readable_host_paths=grant.readable_host_paths,
        tmp_size_bytes=grant.tmp_size_bytes,
    )


def prepare_bubblewrap(plan: LaunchPlan) -> PreparedLaunch:
    grant = plan.grant
    validated = validate_grant(grant)
    if plan.status_fd is None:
        raise BoundaryConfigurationError(
            "a bubblewrap launch requires a started-signal status descriptor (LaunchPlan.status_fd); "
            "the confined agent must never be the only proof that a provider started"
        )
    argv = execution_boundary.build_argv(validated, tuple(plan.argv), status_fd=plan.status_fd)
    return PreparedLaunch(tuple(argv), None, plan.environment, BUBBLEWRAP)
