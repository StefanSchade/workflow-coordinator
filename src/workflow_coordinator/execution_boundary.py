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


def validate(
    repository: Path,
    mounts: Sequence[Mount],
    *,
    binary: str = "bwrap",
    external_scratch: Path | None = None,
) -> ValidatedBoundary:
    executable = shutil.which(binary)
    if not executable or not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        raise BoundaryConfigurationError(f"bwrap is missing or not executable: {binary}")
    repository = _real_path(Path(repository))
    system_roots = tuple(Path(p).resolve() for p in ("/usr", "/bin", "/sbin", "/lib", "/lib64") if os.path.exists(p))
    if any(repository == root or repository in root.parents or root in repository.parents for root in system_roots):
        raise BoundaryConfigurationError("repository overlaps a system mount")
    if not repository.is_dir() or not (repository / ".git").is_dir() or (repository / ".git").is_symlink():
        raise BoundaryConfigurationError("repository must contain a real .git directory")
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
            if not relative.parts or '.git' in relative.parts or dest != PurePosixPath('/repo').joinpath(*relative.parts):
                raise BoundaryConfigurationError("bind must map an existing non-.git repository subpath to its matching destination")
            if not (source.is_file() or source.is_dir()):
                raise BoundaryConfigurationError("only regular files and directories are supported")
            if mount.mode is MountMode.WRITABLE:
                _check_writable_aliases(source)
            checked.append(Mount(source, dest, mount.mode))
            continue
        checked.append(mount)
    if sum(m.mode is MountMode.SCRATCH for m in checked) != 1:
        raise BoundaryConfigurationError("exactly one private scratch mount is required")
    resolved_external_scratch: Path | None = None
    if external_scratch is not None:
        resolved_external_scratch = _real_path(Path(external_scratch))
        if not resolved_external_scratch.is_dir():
            raise BoundaryConfigurationError("external scratch root must be a directory")
        _check_no_overlap(resolved_external_scratch, repository, "external scratch root overlaps the repository")
        for root in system_roots:
            _check_no_overlap(resolved_external_scratch, root, "external scratch root overlaps a system mount")
        for mount in checked:
            if mount.source is not None:
                _check_no_overlap(resolved_external_scratch, mount.source, "external scratch root overlaps another mount's source")
        for other_dest in destinations:
            if other_dest == PurePosixPath('/tmp'):
                continue  # the external scratch bind is deliberately nested inside the private tmpfs
            if (EXTERNAL_SCRATCH_DESTINATION == other_dest or EXTERNAL_SCRATCH_DESTINATION in other_dest.parents
                    or other_dest in EXTERNAL_SCRATCH_DESTINATION.parents):
                raise BoundaryConfigurationError(f"external scratch destination overlaps another mount: {other_dest}")
        _check_writable_aliases(resolved_external_scratch)
    return ValidatedBoundary(repository, tuple(checked), executable, resolved_external_scratch)


def build_argv(boundary: ValidatedBoundary, command: Sequence[str]) -> list[str]:
    # Revalidate at the execution boundary; callers cannot bypass validation by
    # directly constructing the public dataclass.
    boundary = validate(boundary.repository, boundary.mounts, binary=boundary.binary, external_scratch=boundary.external_scratch)
    if not command or not command[0] or any(not isinstance(arg, str) for arg in command):
        raise BoundaryConfigurationError("command must contain a nonempty executable and string arguments")
    args = [boundary.binary, '--die-with-parent', '--dev', '/dev', '--ro-bind', '/usr', '/usr']
    for path in ('/bin', '/sbin', '/lib', '/lib64'):
        if os.path.islink(path):
            args += ['--symlink', os.readlink(path), path]
        elif os.path.exists(path):
            args += ['--ro-bind', path, path]
    args += ['--ro-bind', str(boundary.repository), '/repo', '--tmpfs', '/repo/.git', '--remount-ro', '/repo/.git']
    for mount in boundary.mounts:
        if mount.mode is MountMode.SCRATCH:
            args += ['--tmpfs', str(mount.destination)]
        else:
            args += ['--bind' if mount.mode is MountMode.WRITABLE else '--ro-bind', str(mount.source), str(mount.destination)]
    # Appended after the mounts loop so this bind always lands strictly after
    # the private /tmp tmpfs mount, regardless of that mount's position above.
    if boundary.external_scratch is not None:
        args += ['--bind', str(boundary.external_scratch), str(EXTERNAL_SCRATCH_DESTINATION)]
    return args + ['--chdir', '/repo', '--', *command]


def run(boundary: ValidatedBoundary, command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    """Run only through bwrap, returning its status and unmodified output bytes."""
    return subprocess.run(build_argv(boundary, command), capture_output=True, check=False)
