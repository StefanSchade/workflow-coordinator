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
import subprocess
from typing import Sequence


class BoundaryConfigurationError(ValueError):
    """The requested boundary cannot be represented safely."""


class MountMode(Enum):
    READ_ONLY = "read-only"
    WRITABLE = "writable"
    SCRATCH = "scratch"


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


def _real_path(path: Path) -> Path:
    if not path.is_absolute() or path != path.resolve(strict=False):
        raise BoundaryConfigurationError(f"source must be an absolute real path: {path}")
    if not path.exists():
        raise BoundaryConfigurationError(f"source does not exist: {path}")
    return path


def validate(repository: Path, mounts: Sequence[Mount], *, binary: str = "bwrap") -> ValidatedBoundary:
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
            checked.append(Mount(source, dest, mount.mode))
            continue
        checked.append(mount)
    if sum(m.mode is MountMode.SCRATCH for m in checked) != 1:
        raise BoundaryConfigurationError("exactly one private scratch mount is required")
    return ValidatedBoundary(repository, tuple(checked), executable)


def build_argv(boundary: ValidatedBoundary, command: Sequence[str]) -> list[str]:
    # Revalidate at the execution boundary; callers cannot bypass validation by
    # directly constructing the public dataclass.
    boundary = validate(boundary.repository, boundary.mounts, binary=boundary.binary)
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
    return args + ['--chdir', '/repo', '--', *command]


def run(boundary: ValidatedBoundary, command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    """Run only through bwrap, returning its status and unmodified output bytes."""
    return subprocess.run(build_argv(boundary, command), capture_output=True, check=False)
