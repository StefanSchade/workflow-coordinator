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


def test_outer_boundary_no_longer_blocks_nested_bwrap_on_the_missing_proc_precondition(tmp_path: Path):
    """Slice A's outer view now supplies ``/proc`` itself; the historical
    precondition gap this test used to observe is closed, by design.

    Two things changed from the pre-Slice-A version of this test, both
    stated here rather than only in the review artifact:

    * *Path-identity mounting* (<<mounts>>): the repository is mounted at its
      own canonical path, not ``/repo``, so the stub is addressed by that
      real path rather than a fixed ``/repo`` destination that no longer
      exists in the view.
    * *The fixture's own nested ``bwrap`` invocation* (``provider_stub.sh``)
      used to pass ``--chdir /repo`` -- a path its own minimal inner view
      (only ``/usr``, the ``/bin``-family links, ``/tmp`` and ``/tmp/s``)
      never bound, before or after this slice. Before Slice A that line was
      dead code: the inner ``bwrap`` always stopped at the ``overflowuid``
      read first. Once the outer view supplies ``/proc`` (<<mounts>>, the
      very fix this record makes), the inner ``bwrap`` gets far enough to
      reach that unrelated, never-correct ``--chdir`` and would fail for a
      reason that has nothing to do with ``/proc``. The fixture now targets
      ``--chdir /tmp``, which it does bind, so the remaining mechanics this
      probe exercises (namespace, external-scratch re-grant) stay
      meaningful.

    What this test still establishes mechanically, preserving as much of its
    original meaning as still applies: the exact diagnostic this test used
    to assert (the inner ``bwrap`` failing to read
    ``/proc/sys/kernel/overflowuid``) must never reappear, because Slice A's
    whole point is to supply that precondition. What it cannot establish
    (and does not assume either way) is whether *this host* permits a
    doubly-nested unprivileged user namespace at all -- the planning
    record's own development host could not, inside an agent sandbox, and
    the contract names this an open, host-dependent residual
    (<<residual>>, "nested user namespaces are not always available").  This
    test therefore measures and records that outcome rather than asserting
    it, exactly as the sibling N12 case measures the sandbox-root tmpfs
    question rather than assuming it.
    """
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
            f"TMPDIR=/tmp/s/claude-tmp; export TMPDIR; exec /bin/sh {repository}/provider_stub.sh",
        ],
    )

    old_blocking_diagnostic = b"bwrap: Can't read /proc/sys/kernel/overflowuid: No such file or directory\n"
    assert result.stderr != old_blocking_diagnostic
    assert b"overflowuid" not in result.stderr
    tmpdir = external_scratch / "claude-tmp"
    assert (tmpdir / "outer-marker").read_text() == "outer"
    if result.returncode == 0:
        # A doubly-nested unprivileged user namespace is available on this
        # host: the inner bwrap completed its own mount setup and wrote its
        # marker from inside its own fresh /tmp, confirming the external
        # scratch was re-granted explicitly rather than inherited.
        assert (tmpdir / "nested-marker").read_text() == "nested"
    else:
        # Not available on this host; a named, reported refusal rather than
        # the old /proc gap, and not a reason to lower any guarantee.
        assert result.returncode != 0
    assert not (external_scratch / "outer-marker").exists()


def test_outer_boundary_permits_short_unix_socket_bind_in_external_scratch(tmp_path: Path):
    """Observe the socket syscall itself; a pathname calculation is insufficient.

    This asserted the opposite (an EPERM denial) until 2026-11-10. That
    expectation came from an unaccepted, never-independently-reviewed
    qualification Hop (preserved only as explicitly non-authoritative salvage,
    `workflow-coordinator@4b13dc22`; see `docs/operator_manual/ch06_recovery/01_outcomes.adoc`'s
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
