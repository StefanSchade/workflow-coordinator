"""Opt-in Linux filesystem boundary; callers supply already-authorized grants.

This is not an authority resolver or a replacement for post-hop scope checks.
Sources must remain stable between validation and execution; do not use with a
hostile actor concurrently changing the mount tree.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
from typing import Sequence


class BoundaryConfigurationError(ValueError):
    """The requested boundary cannot be represented safely."""


class MountMode(Enum):
    READ_ONLY = "read-only"
    WRITABLE = "writable"
    SCRATCH = "scratch"
    EXTERNAL_SCRATCH = "external-scratch"


# Fixed, short destination for the one caller-designated external scratch
# bind; nested inside the private /tmp tmpfs so it never substitutes for it.
EXTERNAL_SCRATCH_DESTINATION = PurePosixPath('/tmp/s')

# Default private /tmp tmpfs size cap (256 MiB); see the Operator Manual.
# A bound is Slice A's answer to scratch abuse at this one location: an agent
# filling the private tmpfs exhausts a bounded allocation, not the host's own
# /tmp. It is not the whole answer -- bwrap's own base/root tmpfs is a second
# in-view target, which --remount-ro / (below) addresses separately.
DEFAULT_TMP_SIZE_BYTES = 256 * 1024 * 1024

# Narrow, generated (never caller-supplied) read-only /etc view: name
# resolution, TLS trust and user lookup. Each is bound only if present on the
# host. Classification: generated system-view entries (outside-sources);
# fixed, identical for every invocation, never a Mount value.
GENERATED_ETC_READONLY: tuple[str, ...] = (
    '/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf', '/etc/ssl/certs', '/etc/passwd',
)

# The Coordinator's own Run-state/audit file names; a provider channel
# directory must contain none of them, so a Run state directory passed by
# mistake as the channel is refused rather than mounted.
RUN_STATE_FILE_NAMES = frozenset({
    'run.json', 'checkpoint.json', 'dispatch.json', 'execution-evidence.json', 'usage.json',
})

# The fd number the file-shaped .git masking opens to empty (/dev/null)
# content before exec-ing into the real bwrap invocation; see build_argv().
_GIT_FILE_MASK_FD = 9


@dataclass(frozen=True)
class Mount:
    source: Path | None
    destination: PurePosixPath
    mode: MountMode


@dataclass(frozen=True)
class ValidatedBoundary:
    repository: Path
    mounts: tuple[Mount, ...]
    binary: str
    external_scratch: Path | None = None
    provider_channel: Path | None = None
    provider_channel_writable: bool = False
    readable_host_paths: tuple[Path, ...] = ()
    tmp_size_bytes: int = DEFAULT_TMP_SIZE_BYTES


def _real_path(path: Path) -> Path:
    if not path.is_absolute() or path != path.resolve(strict=False):
        raise BoundaryConfigurationError(f"source must be an absolute real path: {path}")
    if not path.exists():
        raise BoundaryConfigurationError(f"source does not exist: {path}")
    return path


def _check_writable_aliases(source: Path) -> None:
    """Reject non-directory inodes reachable through another name.

    Do not follow symlinks: their targets are resolved inside the sandbox.
    As with mount validation generally, the source tree must remain stable.
    """
    try:
        pending = [source]
        while pending:
            path = pending.pop()
            info = path.lstat()
            # Directory link counts include their subdirectories. Linux does
            # not permit ordinary users to create hardlinks to directories.
            # Every other inode type, including symlinks and special files,
            # can have metadata changed through a writable hardlink alias.
            if not stat.S_ISDIR(info.st_mode) and info.st_nlink != 1:
                raise BoundaryConfigurationError(f"writable entry has hardlink aliases: {path}")
            if stat.S_ISDIR(info.st_mode):
                with os.scandir(path) as entries:
                    pending.extend(Path(entry.path) for entry in entries)
    except OSError as exc:
        raise BoundaryConfigurationError(f"cannot inspect writable source: {source}") from exc


def _check_no_overlap(path: Path, other: Path, message: str) -> None:
    if path == other or path in other.parents or other in path.parents:
        raise BoundaryConfigurationError(message)


def _system_roots() -> tuple[Path, ...]:
    return tuple(Path(p).resolve() for p in ("/usr", "/bin", "/sbin", "/lib", "/lib64") if os.path.exists(p))


def _generated_system_view_paths(system_roots: Sequence[Path]) -> tuple[PurePosixPath, ...]:
    """Fixed absolute destinations the generated view occupies unconditionally."""
    generated = [PurePosixPath(str(root)) for root in system_roots]
    generated += [PurePosixPath(p) for p in GENERATED_ETC_READONLY if os.path.exists(p)]
    generated += [PurePosixPath('/dev'), PurePosixPath('/proc'), PurePosixPath('/tmp')]
    return tuple(generated)


def _check_not_in_generated_system_view(dest: PurePosixPath, system_roots: Sequence[Path], *, context: str) -> None:
    for generated in _generated_system_view_paths(system_roots):
        if dest == generated or dest in generated.parents or generated in dest.parents:
            raise BoundaryConfigurationError(f"{context} destination overlaps the generated system view: {dest}")


def _check_channel_not_run_state(channel: Path) -> None:
    try:
        with os.scandir(channel) as entries:
            names = {entry.name for entry in entries}
    except OSError as exc:
        raise BoundaryConfigurationError(f"cannot inspect provider channel directory: {channel}") from exc
    present = names & RUN_STATE_FILE_NAMES
    if present:
        raise BoundaryConfigurationError(
            "provider channel directory must not be the Coordinator's Run state directory "
            f"(contains: {', '.join(sorted(present))})"
        )


def validate(
    repository: Path,
    mounts: Sequence[Mount],
    *,
    binary: str = "bwrap",
    external_scratch: Path | None = None,
    provider_channel: Path | None = None,
    provider_channel_writable: bool = False,
    readable_host_paths: Sequence[Path] = (),
    tmp_size_bytes: int = DEFAULT_TMP_SIZE_BYTES,
) -> ValidatedBoundary:
    executable = shutil.which(binary)
    if not executable or not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        raise BoundaryConfigurationError(f"bwrap is missing or not executable: {binary}")
    if not isinstance(tmp_size_bytes, int) or isinstance(tmp_size_bytes, bool) or tmp_size_bytes <= 0:
        raise BoundaryConfigurationError("tmp_size_bytes must be a positive integer")
    repository = _real_path(Path(repository))
    system_roots = _system_roots()
    if any(repository == root or repository in root.parents or root in repository.parents for root in system_roots):
        raise BoundaryConfigurationError("repository overlaps a system mount")
    git_entry = repository / ".git"
    if not repository.is_dir() or git_entry.is_symlink() or not (git_entry.is_dir() or git_entry.is_file()):
        raise BoundaryConfigurationError("repository must contain a real .git directory or file")
    checked = []
    destinations: list[PurePosixPath] = []
    for mount in mounts:
        if not isinstance(mount, Mount) or not isinstance(mount.mode, MountMode) or not isinstance(mount.destination, PurePosixPath):
            raise BoundaryConfigurationError("invalid mount specification")
        if mount.mode is MountMode.EXTERNAL_SCRATCH:
            raise BoundaryConfigurationError(
                "external scratch mounts must be requested via the external_scratch parameter, not constructed directly")
        dest = mount.destination
        if not dest.is_absolute() or '..' in dest.parts or str(dest) == '/':
            raise BoundaryConfigurationError(f"invalid destination: {dest}")
        if any(dest == other or dest in other.parents or other in dest.parents for other in destinations):
            raise BoundaryConfigurationError(f"overlapping destinations: {dest}")
        destinations.append(dest)
        if mount.mode is MountMode.SCRATCH:
            if mount.source is not None or dest != PurePosixPath('/tmp'):
                raise BoundaryConfigurationError("scratch must be private tmpfs at /tmp")
        else:
            if mount.source is None:
                raise BoundaryConfigurationError("bind source required")
            source = _real_path(Path(mount.source))
            try:
                relative = source.relative_to(repository)
            except ValueError as exc:
                raise BoundaryConfigurationError("bind sources must be inside repository") from exc
            if not relative.parts or '.git' in relative.parts or dest != PurePosixPath(str(source)):
                raise BoundaryConfigurationError(
                    "bind must map an existing non-.git repository subpath to its own path (path-identity mounting)"
                )
            if not (source.is_file() or source.is_dir()):
                raise BoundaryConfigurationError("only regular files and directories are supported")
            if mount.mode is MountMode.WRITABLE:
                _check_writable_aliases(source)
            checked.append(Mount(source, dest, mount.mode))
            continue
        checked.append(mount)
    if sum(m.mode is MountMode.SCRATCH for m in checked) != 1:
        raise BoundaryConfigurationError("exactly one private scratch mount is required")

    def _check_outside_source(source: Path, *, kind: str) -> Path:
        resolved = _real_path(Path(source))
        _check_no_overlap(resolved, repository, f"{kind} overlaps the repository")
        for root in system_roots:
            _check_no_overlap(resolved, root, f"{kind} overlaps a system mount")
        for mount in checked:
            if mount.source is not None:
                _check_no_overlap(resolved, mount.source, f"{kind} overlaps another mount's source")
        return resolved

    resolved_external_scratch: Path | None = None
    if external_scratch is not None:
        resolved_external_scratch = _check_outside_source(external_scratch, kind="external scratch root")
        if not resolved_external_scratch.is_dir():
            raise BoundaryConfigurationError("external scratch root must be a directory")
        for other_dest in destinations:
            if other_dest == PurePosixPath('/tmp'):
                continue  # the external scratch bind is deliberately nested inside the private tmpfs
            if (EXTERNAL_SCRATCH_DESTINATION == other_dest or EXTERNAL_SCRATCH_DESTINATION in other_dest.parents
                    or other_dest in EXTERNAL_SCRATCH_DESTINATION.parents):
                raise BoundaryConfigurationError(f"external scratch destination overlaps another mount: {other_dest}")
        _check_writable_aliases(resolved_external_scratch)

    resolved_provider_channel: Path | None = None
    if provider_channel is not None:
        resolved_provider_channel = _check_outside_source(provider_channel, kind="provider channel directory")
        if not resolved_provider_channel.is_dir():
            raise BoundaryConfigurationError("provider channel directory must be a directory")
        if resolved_external_scratch is not None:
            _check_no_overlap(resolved_provider_channel, resolved_external_scratch,
                               "provider channel directory overlaps the external scratch root")
        _check_channel_not_run_state(resolved_provider_channel)
        channel_dest = PurePosixPath(str(resolved_provider_channel))
        if any(channel_dest == other or channel_dest in other.parents or other in channel_dest.parents
               for other in destinations):
            raise BoundaryConfigurationError(f"provider channel destination overlaps another mount: {channel_dest}")
        if channel_dest == EXTERNAL_SCRATCH_DESTINATION or channel_dest in EXTERNAL_SCRATCH_DESTINATION.parents \
                or EXTERNAL_SCRATCH_DESTINATION in channel_dest.parents:
            raise BoundaryConfigurationError("provider channel destination overlaps the external scratch bind")
        _check_not_in_generated_system_view(channel_dest, system_roots, context="provider channel")
        destinations.append(channel_dest)
        if provider_channel_writable:
            _check_writable_aliases(resolved_provider_channel)

    resolved_readable_host_paths: list[Path] = []
    for candidate in readable_host_paths:
        resolved = _check_outside_source(candidate, kind="provider authentication path")
        if not (resolved.is_file() or resolved.is_dir()):
            raise BoundaryConfigurationError("provider authentication path must be a regular file or directory")
        if resolved_provider_channel is not None:
            _check_no_overlap(resolved, resolved_provider_channel,
                               "provider authentication path overlaps the provider channel directory")
        if resolved_external_scratch is not None:
            _check_no_overlap(resolved, resolved_external_scratch,
                               "provider authentication path overlaps the external scratch root")
        for already in resolved_readable_host_paths:
            _check_no_overlap(resolved, already, "provider authentication paths overlap one another")
        dest = PurePosixPath(str(resolved))
        if any(dest == other or dest in other.parents or other in dest.parents for other in destinations):
            raise BoundaryConfigurationError(f"provider authentication destination overlaps another mount: {dest}")
        _check_not_in_generated_system_view(dest, system_roots, context="provider authentication path")
        destinations.append(dest)
        resolved_readable_host_paths.append(resolved)

    return ValidatedBoundary(
        repository, tuple(checked), executable, resolved_external_scratch,
        resolved_provider_channel, bool(provider_channel_writable),
        tuple(resolved_readable_host_paths), tmp_size_bytes,
    )


def build_argv(boundary: ValidatedBoundary, command: Sequence[str], *, status_fd: int | None = None) -> list[str]:
    # Revalidate at the execution boundary; callers cannot bypass validation by
    # directly constructing the public dataclass.
    boundary = validate(
        boundary.repository, boundary.mounts, binary=boundary.binary,
        external_scratch=boundary.external_scratch,
        provider_channel=boundary.provider_channel,
        provider_channel_writable=boundary.provider_channel_writable,
        readable_host_paths=boundary.readable_host_paths,
        tmp_size_bytes=boundary.tmp_size_bytes,
    )
    if not command or not command[0] or any(not isinstance(arg, str) for arg in command):
        raise BoundaryConfigurationError("command must contain a nonempty executable and string arguments")
    if status_fd is not None and (not isinstance(status_fd, int) or isinstance(status_fd, bool) or status_fd < 0):
        raise BoundaryConfigurationError("status_fd must be a non-negative integer file descriptor")
    if status_fd == _GIT_FILE_MASK_FD:
        # The .git-file masking wrapper below reopens this exact fd number to
        # /dev/null immediately before exec-ing into bwrap; letting the
        # caller's started-signal pipe land on the same number would have the
        # wrapper silently close the Coordinator's own pipe out from under it.
        raise BoundaryConfigurationError(
            f"status_fd must not reuse the reserved .git-file-mask descriptor ({_GIT_FILE_MASK_FD})"
        )
    args = ['--die-with-parent']
    if status_fd is not None:
        # bwrap writes JSON status documents to this fd from the outer,
        # trusted bwrap process itself; see execution_launcher.StartedSignal.
        args += ['--json-status-fd', str(status_fd)]
    args += ['--dev', '/dev', '--proc', '/proc', '--unshare-pid', '--new-session',
             '--ro-bind', '/usr', '/usr']
    for path in ('/bin', '/sbin', '/lib', '/lib64'):
        if os.path.islink(path):
            args += ['--symlink', os.readlink(path), path]
        elif os.path.exists(path):
            args += ['--ro-bind', path, path]
    for path in GENERATED_ETC_READONLY:
        if os.path.exists(path):
            args += ['--ro-bind', path, path]
    repository = str(boundary.repository)
    args += ['--ro-bind', repository, repository]
    git_entry = boundary.repository / '.git'
    git_dest = f"{repository}/.git"
    git_is_file = git_entry.is_file()
    if git_is_file:
        args += ['--ro-bind-data', str(_GIT_FILE_MASK_FD), git_dest]
    else:
        args += ['--tmpfs', git_dest, '--remount-ro', git_dest]
    for mount in boundary.mounts:
        if mount.mode is MountMode.SCRATCH:
            args += ['--size', str(boundary.tmp_size_bytes), '--tmpfs', str(mount.destination)]
        else:
            args += ['--bind' if mount.mode is MountMode.WRITABLE else '--ro-bind', str(mount.source), str(mount.destination)]
    if boundary.provider_channel is not None:
        channel = str(boundary.provider_channel)
        args += ['--bind' if boundary.provider_channel_writable else '--ro-bind', channel, channel]
    for path in boundary.readable_host_paths:
        args += ['--ro-bind', str(path), str(path)]
    # Appended after the mounts loop so this bind always lands strictly after
    # the private /tmp tmpfs mount, regardless of that mount's position above.
    if boundary.external_scratch is not None:
        args += ['--bind', str(boundary.external_scratch), str(EXTERNAL_SCRATCH_DESTINATION)]
    # Close the sandbox root and every directory bwrap materialised to carry
    # a bind destination; --remount-ro is documented non-recursive, so this
    # must not, and per measurement must not, disturb the writable grants,
    # the private /tmp or the external scratch bound above.
    args += ['--remount-ro', '/']
    args += ['--chdir', repository, '--', *command]
    if git_is_file:
        # The file-shaped .git case masks it with an empty, read-only regular
        # file via --ro-bind-data, which reads from an already-open FD rather
        # than a Mount source. Open that FD to empty (/dev/null) content in a
        # tiny wrapping shell and exec into the real bwrap binary -- same PID,
        # same process group, same session -- so no process-handling property
        # of either adapter changes.
        return ['/bin/sh', '-c', f'exec {_GIT_FILE_MASK_FD}</dev/null && exec "$0" "$@"', boundary.binary, *args]
    return [boundary.binary, *args]


def run(
    boundary: ValidatedBoundary, command: Sequence[str], *, status_fd: int | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run only through bwrap, returning its status and unmodified output bytes.

    ``status_fd``, when given, is passed through to the spawned process (so
    bwrap itself, not a descendant, can write to it) and is otherwise this
    function's only difference from an unconfigured invocation.
    """
    argv = build_argv(boundary, command, status_fd=status_fd)
    pass_fds = (status_fd,) if status_fd is not None else ()
    return subprocess.run(argv, capture_output=True, check=False, pass_fds=pass_fds)
