import os
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import pytest

from workflow_coordinator.execution_boundary import (
    BoundaryConfigurationError, EXTERNAL_SCRATCH_DESTINATION, Mount, MountMode, build_argv, run, validate,
)


def test_file_grant_is_exact_and_invalid_config_never_spawns(tmp_path: Path):
    (tmp_path / '.git').mkdir()
    file = tmp_path / 'file'
    file.write_text('data')
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)
    grant = Mount(file, PurePosixPath(str(file)), MountMode.WRITABLE)
    boundary = validate(tmp_path, [grant, scratch])
    argv = build_argv(boundary, ['/usr/bin/cat', str(file)])
    index = argv.index('--bind')
    assert argv[index:index + 3] == ['--bind', str(file), str(file)]
    link = tmp_path / 'link'
    link.symlink_to(file)
    bad = [
        Mount(link, PurePosixPath(str(link)), MountMode.WRITABLE),
        Mount(Path('relative'), PurePosixPath(str(file)), MountMode.WRITABLE),
        Mount(tmp_path / '.git', PurePosixPath(str(tmp_path / '.git')), MountMode.WRITABLE),
        Mount(file, PurePosixPath(str(tmp_path / 'missing' / 'file')), MountMode.WRITABLE),
        Mount(file, PurePosixPath('/repo/file'), MountMode.WRITABLE),  # the retired /repo convention
    ]
    with patch('subprocess.run') as spawn:
        for item in bad:
            with pytest.raises(BoundaryConfigurationError):
                validate(tmp_path, [item, scratch])
        with pytest.raises(BoundaryConfigurationError, match='bwrap'):
            validate(tmp_path, [scratch], binary=str(tmp_path / 'missing'))
        with pytest.raises(BoundaryConfigurationError):
            run(boundary, [])
        spawn.assert_not_called()


def test_git_file_shaped_worktree_is_accepted(tmp_path: Path):
    """A linked Git worktree's `.git` is a regular file; validate() accepts it
    and build_argv() masks it with the FD-backed ro-bind-data wrapper."""
    (tmp_path / '.git').write_text('gitdir: /elsewhere/.git/worktrees/x\n')
    boundary = validate(tmp_path, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    argv = build_argv(boundary, ['/bin/true'])
    assert argv[:3] == ['/bin/sh', '-c', 'exec 9</dev/null && exec "$0" "$@"']
    assert argv[3] == boundary.binary
    index = argv.index('--ro-bind-data')
    assert argv[index:index + 3] == ['--ro-bind-data', '9', f"{tmp_path}/.git"]


def test_git_symlink_is_rejected(tmp_path: Path):
    real_git = tmp_path.parent / 'real-git'
    real_git.mkdir()
    (tmp_path / '.git').symlink_to(real_git)
    with pytest.raises(BoundaryConfigurationError, match='.git'):
        validate(tmp_path, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])


def test_overlapping_destinations_rejected(tmp_path: Path):
    (tmp_path / '.git').mkdir()
    directory = tmp_path / 'dir'
    directory.mkdir()
    (directory / 'file').touch()
    with pytest.raises(BoundaryConfigurationError, match='overlapping'):
        validate(tmp_path, [Mount(directory, PurePosixPath(str(directory)), MountMode.WRITABLE),
                            Mount(directory / 'file', PurePosixPath(str(directory / 'file')), MountMode.WRITABLE),
                            Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])


def test_unreadable_writable_tree_fails_closed(tmp_path: Path):
    (tmp_path / '.git').mkdir()
    directory = tmp_path / 'dir'
    directory.mkdir()
    mounts = [Mount(directory, PurePosixPath(str(directory)), MountMode.WRITABLE),
              Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)]
    boundary = validate(tmp_path, mounts)
    with patch('workflow_coordinator.execution_boundary.os.scandir', side_effect=PermissionError('denied')):
        with patch('subprocess.run') as spawn:
            with pytest.raises(BoundaryConfigurationError, match='cannot inspect writable source'):
                run(boundary, ['/bin/true'])
            spawn.assert_not_called()


def test_hardlinked_fifo_in_writable_tree_is_rejected(tmp_path: Path):
    (tmp_path / '.git').mkdir()
    directory = tmp_path / 'dir'
    directory.mkdir()
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo)
    os.link(fifo, directory / 'alias')
    mounts = [Mount(directory, PurePosixPath(str(directory)), MountMode.WRITABLE),
              Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)]
    with pytest.raises(BoundaryConfigurationError, match='hardlink aliases'):
        validate(tmp_path, mounts)


def test_tmp_scratch_size_cap_is_generated(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)], tmp_size_bytes=123456)
    argv = build_argv(boundary, ['/bin/true'])
    index = next(i for i in range(len(argv) - 1) if argv[i] == '--tmpfs' and argv[i + 1] == '/tmp')
    assert argv[index - 2:index + 2] == ['--size', '123456', '--tmpfs', '/tmp']


def test_sandbox_root_is_remounted_read_only_after_all_mounts(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    argv = build_argv(boundary, ['/bin/true'])
    chdir_index = argv.index('--chdir')
    root_remount_indexes = [i for i, arg in enumerate(argv) if arg == '--remount-ro' and argv[i + 1] == '/']
    assert len(root_remount_indexes) == 1
    assert root_remount_indexes[0] < chdir_index


def test_proc_and_pid_namespace_flags_present_without_as_pid_1(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    argv = build_argv(boundary, ['/bin/true'])
    assert '--unshare-pid' in argv
    assert '--new-session' in argv
    assert '--as-pid-1' not in argv
    index = argv.index('--proc')
    assert argv[index:index + 2] == ['--proc', '/proc']


def test_generated_etc_entries_are_bound_read_only_when_present(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    argv = build_argv(boundary, ['/bin/true'])
    for etc_path in ('/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf', '/etc/ssl/certs', '/etc/passwd'):
        if os.path.exists(etc_path):
            index = argv.index(etc_path)
            assert argv[index - 1] == '--ro-bind'
            assert argv[index + 1] == etc_path


def test_external_scratch_is_validated_and_mounted_after_private_tmp(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    external_root = tmp_path / 'external'
    external_root.mkdir()
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)
    boundary = validate(repo, [scratch], external_scratch=external_root)
    assert boundary.external_scratch == external_root
    argv = build_argv(boundary, ['/bin/true'])
    tmpfs_index = argv.index('/tmp', argv.index('--tmpfs')) - 1
    assert argv[tmpfs_index:tmpfs_index + 2] == ['--tmpfs', '/tmp']
    bind_index = argv.index(str(external_root))
    assert argv[bind_index - 1] == '--bind'
    assert argv[bind_index + 1] == str(EXTERNAL_SCRATCH_DESTINATION)
    assert bind_index > tmpfs_index


def test_external_scratch_absent_by_default(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    assert boundary.external_scratch is None
    argv = build_argv(boundary, ['/bin/true'])
    assert str(EXTERNAL_SCRATCH_DESTINATION) not in argv


def test_external_scratch_invalid_configurations_never_spawn(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    external_root = tmp_path / 'external'
    external_root.mkdir()
    nested_in_repo = repo / 'nested'
    nested_in_repo.mkdir()
    forbidden_file = tmp_path / 'forbidden-file'
    forbidden_file.write_text('not a directory')
    link_root = tmp_path / 'external-link'
    link_root.symlink_to(external_root)
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)
    system_root = next((Path(p) for p in ('/usr', '/bin', '/sbin', '/lib', '/lib64') if os.path.exists(p)), None)
    bad_external_scratch = [
        nested_in_repo,  # overlaps the repository
        forbidden_file,  # not a directory
        link_root,  # not a real (non-symlink) path
        tmp_path / 'missing',  # does not exist
        Path('relative-external'),  # not absolute
    ] + ([system_root] if system_root is not None else [])
    with patch('subprocess.run') as spawn:
        for bad in bad_external_scratch:
            with pytest.raises(BoundaryConfigurationError):
                validate(repo, [scratch], external_scratch=bad)
        spawn.assert_not_called()


def test_external_scratch_arbitrary_host_path_cannot_be_substituted(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    external_root = tmp_path / 'external'
    external_root.mkdir()
    unauthorized = tmp_path / 'unauthorized'
    unauthorized.mkdir()
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)
    substitute = Mount(unauthorized, EXTERNAL_SCRATCH_DESTINATION, MountMode.EXTERNAL_SCRATCH)
    with patch('subprocess.run') as spawn:
        with pytest.raises(BoundaryConfigurationError, match='external_scratch parameter'):
            validate(repo, [scratch, substitute])
        # Even alongside a legitimately authorized external_scratch, a directly
        # constructed EXTERNAL_SCRATCH mount must still be rejected: the
        # dedicated parameter is the only authorized channel, never an entry
        # in the ordinary mounts sequence.
        with pytest.raises(BoundaryConfigurationError, match='external_scratch parameter'):
            validate(repo, [scratch, substitute], external_scratch=external_root)
        # Two directly constructed EXTERNAL_SCRATCH mounts (an attempt at more
        # than one external scratch bind) are rejected the same way.
        second_substitute = Mount(external_root, EXTERNAL_SCRATCH_DESTINATION, MountMode.EXTERNAL_SCRATCH)
        with pytest.raises(BoundaryConfigurationError, match='external_scratch parameter'):
            validate(repo, [scratch, substitute, second_substitute])
        spawn.assert_not_called()


def test_external_scratch_hardlink_alias_is_rejected(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    external_root = tmp_path / 'external'
    external_root.mkdir()
    fifo = tmp_path / 'external-fifo'
    os.mkfifo(fifo)
    os.link(fifo, external_root / 'alias')
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)
    with pytest.raises(BoundaryConfigurationError, match='hardlink aliases'):
        validate(repo, [scratch], external_scratch=external_root)


def test_external_scratch_root_symlink_is_rejected_before_any_access(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    forbidden = tmp_path / 'forbidden'
    forbidden.mkdir()
    secret = forbidden / 'secret'
    secret.write_text('host secret')
    root_link = tmp_path / 'external-link'
    root_link.symlink_to(forbidden)
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)
    with patch('subprocess.run') as spawn:
        with pytest.raises(BoundaryConfigurationError):
            validate(repo, [scratch], external_scratch=root_link)
        spawn.assert_not_called()
    assert secret.read_text() == 'host secret'


def test_provider_channel_is_bound_read_only_for_claude_and_read_write_for_codex(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    channel = tmp_path / 'channel'
    channel.mkdir()
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)

    read_only = validate(repo, [scratch], provider_channel=channel)
    argv = build_argv(read_only, ['/bin/true'])
    index = argv.index(str(channel))
    assert argv[index - 1] == '--ro-bind'

    writable = validate(repo, [scratch], provider_channel=channel, provider_channel_writable=True)
    argv = build_argv(writable, ['/bin/true'])
    index = argv.index(str(channel))
    assert argv[index - 1] == '--bind'


def test_provider_channel_must_not_be_the_run_state_directory(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    run_root = tmp_path / 'run-root'
    run_root.mkdir()
    (run_root / 'run.json').write_text('{}')
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)
    with pytest.raises(BoundaryConfigurationError, match='Run state directory'):
        validate(repo, [scratch], provider_channel=run_root)


def test_provider_channel_must_be_disjoint_and_a_directory(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    inside_repo = repo / 'channel'
    inside_repo.mkdir()
    not_a_directory = tmp_path / 'channel-file'
    not_a_directory.write_text('x')
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)
    for bad in (inside_repo, not_a_directory, tmp_path / 'missing'):
        with pytest.raises(BoundaryConfigurationError):
            validate(repo, [scratch], provider_channel=bad)


def test_provider_channel_writable_grant_runs_hardlink_walk(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    channel = tmp_path / 'channel'
    channel.mkdir()
    fifo = tmp_path / 'channel-fifo'
    os.mkfifo(fifo)
    os.link(fifo, channel / 'alias')
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)
    with pytest.raises(BoundaryConfigurationError, match='hardlink aliases'):
        validate(repo, [scratch], provider_channel=channel, provider_channel_writable=True)


def test_readable_host_paths_are_bound_read_only(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    auth = tmp_path / 'auth-token'
    auth.write_text('token')
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)
    boundary = validate(repo, [scratch], readable_host_paths=(auth,))
    argv = build_argv(boundary, ['/bin/true'])
    index = argv.index(str(auth))
    assert argv[index - 1] == '--ro-bind'
    assert argv[index + 1] == str(auth)


def test_readable_host_path_requires_an_existing_absolute_path(tmp_path: Path):
    """States exactly what this check proves: a nonexistent path and a
    relative path are refused. It does *not* prove narrowness -- an
    arbitrary *existing*, disjoint host path is accepted by design; see
    ``test_readable_host_path_accepts_any_existing_disjoint_host_path``
    below and F3's disposition (the operator's own responsibility, not a
    technically enforced invariant)."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)
    for bad in (tmp_path / 'missing', Path('relative-auth')):
        with pytest.raises(BoundaryConfigurationError):
            validate(repo, [scratch], readable_host_paths=(bad,))


def test_readable_host_path_accepts_any_existing_disjoint_host_path(tmp_path: Path):
    """F3 (maintainer disposition, 2026-11-26): the choice of the
    authentication mount is the operator's own responsibility; WFC enforces
    the boundary the operator set against the agent, and does not protect
    the operator from the operator's own authorization decisions. No
    narrowing check exists or is added -- a whole directory (not just a
    single file) is accepted exactly like a single file, as long as it is
    absolute, canonical, existing and disjoint from every other mount. This
    is accepted behaviour, not a defect; see
    `docs/operator_manual/ch01_orientation/02_boundaries.adoc` for the
    operator-facing statement that this is an explicit extension of the
    read authority, and the contract's `<<outside-sources>>` section for
    why no further restriction is authorized here."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)
    whole_directory = tmp_path / 'a-whole-credential-directory'
    whole_directory.mkdir()
    (whole_directory / 'id_ed25519').write_text('not a real key')
    (whole_directory / 'unrelated-project').mkdir()
    boundary = validate(repo, [scratch], readable_host_paths=(whole_directory,))
    assert boundary.readable_host_paths == (whole_directory,)
    argv = build_argv(boundary, ['/bin/true'])
    index = argv.index(str(whole_directory))
    assert argv[index - 1] == '--ro-bind'


def test_designated_external_mounts_cannot_overlap_the_generated_system_view(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)
    # /etc is the parent of generated-view entries (/etc/passwd etc.) but not
    # one of the system roots, so this exercises the generated-system-view
    # check specifically rather than the earlier system-mount overlap check.
    assert os.path.isdir('/etc')
    with pytest.raises(BoundaryConfigurationError, match='generated system view'):
        validate(repo, [scratch], provider_channel=Path('/etc'))


def test_undesignated_mount_with_outside_repository_source_is_refused(tmp_path: Path):
    """N16's companion assertion: the three designated external-mount
    parameters (external scratch, provider channel, provider authentication)
    are each separately validated, never a general relaxation of
    ``source.relative_to(repository)``. An arbitrary host path offered
    through a plain, undesignated ``Mount`` -- the fourth, unenumerated
    route -- is still refused by that unchanged generic rule, and the
    attempted source is left untouched."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    outside = tmp_path / 'outside-host-path'
    outside.write_text('data')
    scratch = Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)
    with pytest.raises(BoundaryConfigurationError, match='inside repository'):
        validate(repo, [scratch, Mount(outside, PurePosixPath(str(outside)), MountMode.WRITABLE)])
    assert outside.read_text() == 'data'
