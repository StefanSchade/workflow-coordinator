"""Process-observable qualification probes for nested bwrap mechanics.

These do not invoke Claude Code.  The fixture's stub is intentionally limited
to preconditions that can be isolated safely without provider credentials or
network: a real nested bwrap, the re-granted external scratch, and AF_UNIX.
"""
from __future__ import annotations

import shutil
import stat
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


def test_outer_boundary_permits_short_unix_socket_bind_in_external_scratch(tmp_path: Path):
    """Observe the socket syscall itself; a pathname calculation is insufficient.

    This asserted the opposite (an EPERM denial) until 2026-11-10. That
    expectation came from an unaccepted, never-independently-reviewed
    qualification Hop (preserved only as explicitly non-authoritative salvage,
    `workflow-coordinator@4b13dc22`; see `docs/run-recovery-and-salvage.adoc`'s
    authority statement -- salvage is not trusted evidence) whose own "outside
    any WFC Run" reproduction still ran inside that Hop's own agent-provider
    Bash-tool sandbox, not a bare host. Reproduced directly on the bare host
    (`Seccomp: 0`, no WFC/agent sandbox) against this same, unchanged
    `execution_boundary.py`: the bind succeeds (`returncode=0`, empty stderr)
    and a real socket inode is visible at the expected host path. Nothing in
    `build_argv()` adds a seccomp filter, capability drop, or mount option
    that would restrict `socket()`/`bind()`; a plain `--bind` grants ordinary
    filesystem use, sockets included. No documented product/security
    requirement (`docs/backlog/FTR-WFC-EXECUTION-TRUST-BOUNDARIES.adoc`'s
    2026-11-08 section lists this only as an open investigation question, not
    an invariant) calls for denying it either. The externally-scratch-mount
    feature's purpose -- letting a provider's short-`TMPDIR`-symlink `AF_UNIX`
    IPC land in real external scratch -- requires exactly this to work.
    """
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

    assert result.returncode == 0
    assert result.stderr == b""
    bound_socket = external_scratch / "claude-tmp" / "outer.sock"
    assert bound_socket.exists()
    assert stat.S_ISSOCK(bound_socket.lstat().st_mode)
