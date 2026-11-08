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
    grant = Mount(file, PurePosixPath('/repo/file'), MountMode.WRITABLE)
    boundary = validate(tmp_path, [grant, scratch])
    argv = build_argv(boundary, ['/usr/bin/cat', '/repo/file'])
    index = argv.index('--bind')
    assert argv[index:index + 3] == ['--bind', str(file), '/repo/file']
    link = tmp_path / 'link'
    link.symlink_to(file)
    bad = [
        Mount(link, PurePosixPath('/repo/link'), MountMode.WRITABLE),
        Mount(Path('relative'), PurePosixPath('/repo/file'), MountMode.WRITABLE),
        Mount(tmp_path / '.git', PurePosixPath('/repo/.git'), MountMode.WRITABLE),
        Mount(file, PurePosixPath('/repo/missing/file'), MountMode.WRITABLE),
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


def test_overlapping_destinations_rejected(tmp_path: Path):
    (tmp_path / '.git').mkdir()
    directory = tmp_path / 'dir'
    directory.mkdir()
    (directory / 'file').touch()
    with pytest.raises(BoundaryConfigurationError, match='overlapping'):
        validate(tmp_path, [Mount(directory, PurePosixPath('/repo/dir'), MountMode.WRITABLE),
                            Mount(directory / 'file', PurePosixPath('/repo/dir/file'), MountMode.WRITABLE),
                            Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])


def test_unreadable_writable_tree_fails_closed(tmp_path: Path):
    (tmp_path / '.git').mkdir()
    directory = tmp_path / 'dir'
    directory.mkdir()
    mounts = [Mount(directory, PurePosixPath('/repo/dir'), MountMode.WRITABLE),
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
    mounts = [Mount(directory, PurePosixPath('/repo/dir'), MountMode.WRITABLE),
              Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)]
    with pytest.raises(BoundaryConfigurationError, match='hardlink aliases'):
        validate(tmp_path, mounts)


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
