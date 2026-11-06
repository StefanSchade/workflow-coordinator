from pathlib import PurePosixPath

from workflow_coordinator.execution_boundary import Mount, MountMode, run, validate


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
