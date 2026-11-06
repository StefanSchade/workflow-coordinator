import os
from pathlib import PurePosixPath

import pytest

from workflow_coordinator.execution_boundary import (
    BoundaryConfigurationError, Mount, MountMode, run, validate,
)


def test_real_filesystem_boundary(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    (repo / '.git' / 'secret').write_text('git secret')
    (repo / 'readable').write_text('read content')
    protected = repo / 'protected'
    protected.write_text('unchanged')
    before = protected.stat().st_mtime_ns
    writable = repo / 'writable'
    writable.mkdir()
    repo_tmp = repo / 'tmp'
    repo_tmp.mkdir()
    sentinel = repo_tmp / 'sentinel'
    sentinel.write_text('repository temporary data')
    tmp_before = repo_tmp.stat().st_mtime_ns
    private = tmp_path / 'private'
    private.mkdir()
    secret = private / 'secret'
    secret.write_text('host secret')
    (writable / 'escape').symlink_to(secret)
    boundary = validate(repo, [Mount(writable, PurePosixPath('/repo/writable'), MountMode.WRITABLE),
                               Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    script = '''
set -eu
[ "$(cat /repo/readable)" = 'read content' ]
printf changed > /repo/writable/created
if (echo bad > /repo/protected) 2>/dev/null; then exit 1; fi
if [ -e "$1" ] || [ -e "$2" ] || cat "$1" 2>/dev/null || ls "$2" 2>/dev/null; then exit 2; fi
if cat /repo/.git/secret 2>/dev/null || (echo bad > /repo/.git/new) 2>/dev/null; then exit 3; fi
printf scratch > /tmp/private-file
[ "$(cat /tmp/private-file)" = scratch ]
if (echo bad > /repo/writable/escape) 2>/dev/null; then exit 4; fi
if (echo bad > /repo/writable/../../private/secret) 2>/dev/null; then exit 5; fi
'''
    result = run(boundary, ['/bin/sh', '-c', script, 'fixture', str(secret), str(private)])
    assert result.returncode == 0, result.stderr
    assert (writable / 'created').read_text() == 'changed'
    assert protected.read_text() == 'unchanged'
    assert protected.stat().st_mtime_ns == before
    assert secret.read_text() == 'host secret'
    assert list(repo_tmp.iterdir()) == [sentinel]
    assert sentinel.read_text() == 'repository temporary data'
    assert repo_tmp.stat().st_mtime_ns == tmp_before


def test_output_and_exit_status_are_preserved(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    result = run(boundary, ['/bin/sh', '-c',
                            "printf 'out\\r\\n\\377'; printf 'err\\r\\n\\376' >&2; exit 37"])
    assert result.returncode == 37
    assert result.stdout == b'out\r\n\xff'
    assert result.stderr == b'err\r\n\xfe'


@pytest.mark.parametrize('target_kind', ['protected', 'git', 'external'])
@pytest.mark.parametrize('grant_kind', ['file', 'directory'])
def test_writable_hardlink_alias_is_rejected_before_launch(tmp_path, target_kind, grant_kind):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    directory = repo / 'writable'
    directory.mkdir()
    nested = directory / 'nested'
    nested.mkdir()
    alias = nested / 'alias'
    alias.write_text('initial')
    target = {'protected': repo / 'protected', 'git': repo / '.git' / 'secret',
              'external': tmp_path / 'external'}[target_kind]
    target.write_text('protected content')
    source = alias if grant_kind == 'file' else directory
    destination = '/repo/writable/nested/alias' if grant_kind == 'file' else '/repo/writable'
    mounts = [Mount(source, PurePosixPath(destination), MountMode.WRITABLE),
              Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)]
    boundary = validate(repo, mounts)
    result = run(boundary, ['/bin/sh', '-c', 'printf allowed > /repo/writable/nested/alias'])
    assert result.returncode == 0, result.stderr
    assert alias.read_text() == 'allowed'
    alias.unlink()
    os.link(target, alias)
    with pytest.raises(BoundaryConfigurationError, match='hardlink aliases'):
        validate(repo, mounts)
    with pytest.raises(BoundaryConfigurationError, match='hardlink aliases'):
        run(boundary, ['/bin/sh', '-c', 'printf breached > /repo/writable/nested/alias'])
    assert target.read_text() == 'protected content'


def test_sandbox_cannot_create_alias_to_protected_file(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    protected = repo / 'protected'
    protected.write_text('unchanged')
    writable = repo / 'writable'
    writable.mkdir()
    boundary = validate(repo, [Mount(writable, PurePosixPath('/repo/writable'), MountMode.WRITABLE),
                               Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    result = run(boundary, ['/bin/sh', '-c',
                            'if ln /repo/protected /repo/writable/alias 2>/dev/null; then '
                            'printf breached > /repo/writable/alias; exit 1; fi'])
    assert result.returncode == 0, result.stderr
    assert protected.read_text() == 'unchanged'
    assert not (writable / 'alias').exists()
