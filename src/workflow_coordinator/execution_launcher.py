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
    """The grant, plus what the adapter itself builds."""

    grant: BoundaryGrant
    argv: tuple[str, ...]
    cwd: Path | None
    environment: Mapping[str, str] | None = None


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


def _resolve_scope_entry(repository: Path, relative: str, artifact_paths: frozenset[str]) -> str:
    """Resolve one normalized, already-existing-or-not scope entry to what
    is actually mounted (<<mounts>>, "Not-yet-existing authorized targets").

    No file extension is a reliable shape signal, so a missing entry is
    treated as a *directory* to be pre-created at its own path -- unless the
    caller already knows it is file-shaped because it is one of the
    executing phase's own declared durable-artifact paths (the reviewer
    case: a durable review artifact never exists when its phase starts).
    Only that known-file case falls back to the nearest existing ancestor
    directory, which is the stated overgrant residual
    (<<residual>>, item 3).

    * if it already exists (file or directory), itself;
    * otherwise, if it is a declared durable-artifact path, the nearest
      existing ancestor strictly inside the repository (the artifact's own
      directory never exists before the phase starts);
    * otherwise, itself -- a directory to be pre-created by the Coordinator
      dispatch frame before validation (<<a-lifecycle>>).
    """
    target = repository / relative
    if target.exists():
        return relative
    if relative not in artifact_paths:
        return relative
    cursor = target.parent
    while cursor != repository and not cursor.exists():
        cursor = cursor.parent
    return str(cursor.relative_to(repository)) if cursor != repository else relative


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
    artifacts (empty for a worker phase in `robust@v1`, since a worker's
    scope is the whole Commission scope, not an artifact list) -- the only
    entries treated as file-shaped when absent.
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


def missing_writable_targets(grant: BoundaryGrant) -> tuple[Path, ...]:
    """Authorized writable targets that do not yet exist.

    Read-only: the Coordinator's per-Hop dispatch frame creates each one
    (mode ``0o700``) before ``validate()`` runs, and removes it afterwards if
    it is still empty (<<a-lifecycle>>). The agent and the adapter own
    neither end of that bracket.
    """
    if grant.mode != BUBBLEWRAP:
        return ()
    missing = []
    for relative in grant.writable_repository_paths:
        target = grant.repository / relative
        if not target.exists():
            missing.append(target)
    return tuple(missing)


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


BOUNDARY_STARTED_MARKER_NAME = ".wfc-boundary-started"


def boundary_started_marker(grant: BoundaryGrant) -> tuple[PurePosixPath, Path] | None:
    """A host-visible path a wrapped command touches immediately before
    exec-ing into the real provider argv, so an adapter can tell a `bwrap`
    setup failure (not-started) apart from a started provider that then
    failed, by `bwrap`'s own exit path rather than by guessing from its
    diagnostic text on stderr (<<enablement>>, error classification).

    Returns ``(in_view_path, host_path)``, or ``None`` when the grant is not
    a bubblewrap grant or carries no writable host-visible location to place
    it in -- a direct-mode grant, or (defensively) a bubblewrap grant with
    neither a writable provider channel nor an external scratch root.
    """
    if grant.mode != BUBBLEWRAP:
        return None
    if grant.provider_channel is not None and grant.provider_channel_writable:
        host = grant.provider_channel / BOUNDARY_STARTED_MARKER_NAME
        return PurePosixPath(str(host)), host
    if grant.external_scratch is not None:
        host = grant.external_scratch / BOUNDARY_STARTED_MARKER_NAME
        in_view = execution_boundary.EXTERNAL_SCRATCH_DESTINATION / BOUNDARY_STARTED_MARKER_NAME
        return in_view, host
    return None


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
    command = tuple(plan.argv)
    marker = boundary_started_marker(grant)
    if marker is not None:
        in_view, _host = marker
        command = ('/bin/sh', '-c', ': > "$1"; shift; exec "$@"', 'sh', str(in_view), *command)
    argv = execution_boundary.build_argv(validated, command)
    return PreparedLaunch(tuple(argv), None, plan.environment, BUBBLEWRAP)
