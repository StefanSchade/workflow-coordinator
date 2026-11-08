"""Process-observable qualification probes for nested bwrap mechanics.

These do not invoke Claude Code.  The fixture's stub is intentionally limited
to preconditions that can be isolated safely without provider credentials or
network: a real nested bwrap, the re-granted external scratch, and AF_UNIX.
"""
from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath

from workflow_coordinator.execution_boundary import Mount, MountMode, run, validate


FIXTURES = Path(__file__).parents[1] / "fixtures" / "nested_claude_bwrap_qualification"
SUN_PATH_MAXIMUM = 108


def test_outer_boundary_reports_the_missing_proc_precondition_before_nested_bwrap(tmp_path: Path):
    """The accepted outer primitive has no /proc, so the inner bwrap stops."""
    repository = tmp_path / "repository"
    shutil.copytree(FIXTURES / "repository", repository)
    (repository / ".git").mkdir()
    stub = repository / "provider_stub.sh"
    shutil.copy2(FIXTURES / "provider_stub.sh", stub)
    external_scratch = tmp_path / "external-scratch"
    external_scratch.mkdir()

    boundary = validate(
        repository,
        [Mount(None, PurePosixPath("/tmp"), MountMode.SCRATCH)],
        external_scratch=external_scratch,
    )
    result = run(
        boundary,
        [
            "/bin/sh", "-ceu",
            "TMPDIR=/tmp/s/claude-tmp; export TMPDIR; exec /bin/sh /repo/provider_stub.sh",
        ],
    )

    assert result.returncode == 1
    assert result.stderr == b"bwrap: Can't read /proc/sys/kernel/overflowuid: No such file or directory\n"
    tmpdir = external_scratch / "claude-tmp"
    assert (tmpdir / "outer-marker").read_text() == "outer"
    assert not (tmpdir / "nested-marker").exists()
    assert not (external_scratch / "outer-marker").exists()


def test_outer_boundary_denies_short_unix_socket_in_external_scratch(tmp_path: Path):
    """Observe the socket syscall itself; a pathname calculation is insufficient."""
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    external_scratch = tmp_path / "external-scratch"
    external_scratch.mkdir()
    socket_path = PurePosixPath("/tmp/s/claude-tmp/outer.sock")
    assert len(str(socket_path).encode()) < SUN_PATH_MAXIMUM
    boundary = validate(
        repository,
        [Mount(None, PurePosixPath("/tmp"), MountMode.SCRATCH)],
        external_scratch=external_scratch,
    )

    result = run(
        boundary,
        [
            "/usr/bin/python3", "-c",
            "import os, socket; os.makedirs('/tmp/s/claude-tmp'); "
            "socket.socket(socket.AF_UNIX).bind('/tmp/s/claude-tmp/outer.sock')",
        ],
    )

    assert result.returncode == 1
    assert b"PermissionError: [Errno 1] Operation not permitted" in result.stderr
    assert not (external_scratch / "claude-tmp" / "outer.sock").exists()
