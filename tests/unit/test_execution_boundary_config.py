import os
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import pytest

from workflow_coordinator.execution_boundary import (
    BoundaryConfigurationError, Mount, MountMode, build_argv, run, validate,
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
