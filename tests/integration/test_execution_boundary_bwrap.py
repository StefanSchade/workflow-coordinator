import ast
import os
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from workflow_coordinator.execution_boundary import (
    BoundaryConfigurationError, EXTERNAL_SCRATCH_DESTINATION, Mount, MountMode, build_argv, run, validate,
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
    boundary = validate(repo, [Mount(writable, PurePosixPath(str(writable)), MountMode.WRITABLE),
                               Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    script = f'''
set -eu
[ "$(cat {repo}/readable)" = 'read content' ]
printf changed > {writable}/created
if (echo bad > {repo}/protected) 2>/dev/null; then exit 1; fi
if [ -e "$1" ] || [ -e "$2" ] || cat "$1" 2>/dev/null || ls "$2" 2>/dev/null; then exit 2; fi
if cat {repo}/.git/secret 2>/dev/null || (echo bad > {repo}/.git/new) 2>/dev/null; then exit 3; fi
printf scratch > /tmp/private-file
[ "$(cat /tmp/private-file)" = scratch ]
if (echo bad > {writable}/escape) 2>/dev/null; then exit 4; fi
if (echo bad > {writable}/../../private/secret) 2>/dev/null; then exit 5; fi
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
    destination = str(alias) if grant_kind == 'file' else str(directory)
    mounts = [Mount(source, PurePosixPath(destination), MountMode.WRITABLE),
              Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)]
    boundary = validate(repo, mounts)
    result = run(boundary, ['/bin/sh', '-c', f'printf allowed > {alias}'])
    assert result.returncode == 0, result.stderr
    assert alias.read_text() == 'allowed'
    alias.unlink()
    os.link(target, alias)
    with pytest.raises(BoundaryConfigurationError, match='hardlink aliases'):
        validate(repo, mounts)
    with pytest.raises(BoundaryConfigurationError, match='hardlink aliases'):
        run(boundary, ['/bin/sh', '-c', f'printf breached > {alias}'])
    assert target.read_text() == 'protected content'


def test_sandbox_cannot_create_alias_to_protected_file(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    protected = repo / 'protected'
    protected.write_text('unchanged')
    writable = repo / 'writable'
    writable.mkdir()
    boundary = validate(repo, [Mount(writable, PurePosixPath(str(writable)), MountMode.WRITABLE),
                               Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    result = run(boundary, ['/bin/sh', '-c',
                            f'if ln {repo}/protected {writable}/alias 2>/dev/null; then '
                            f'printf breached > {writable}/alias; exit 1; fi'])
    assert result.returncode == 0, result.stderr
    assert protected.read_text() == 'unchanged'
    assert not (writable / 'alias').exists()


@pytest.mark.parametrize('target_kind', ['protected', 'git', 'external'])
def test_writable_symlink_hardlink_metadata_is_rejected(tmp_path, target_kind):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    writable = repo / 'writable'
    writable.mkdir()
    alias = writable / 'alias'
    alias.symlink_to('missing')
    mounts = [Mount(writable, PurePosixPath(str(writable)), MountMode.WRITABLE),
              Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)]
    boundary = validate(repo, mounts)
    result = run(boundary, ['/bin/sh', '-c', f'touch -h -d @123 {writable}/alias'])
    assert result.returncode == 0, result.stderr
    assert alias.lstat().st_mtime_ns == 123_000_000_000

    target = {'protected': repo / 'protected-link', 'git': repo / '.git' / 'secret-link',
              'external': tmp_path / 'external-link'}[target_kind]
    target.symlink_to('missing')
    before = target.lstat()
    alias.unlink()
    os.link(target, alias, follow_symlinks=False)
    with pytest.raises(BoundaryConfigurationError, match='hardlink aliases'):
        validate(repo, mounts)
    with pytest.raises(BoundaryConfigurationError, match='hardlink aliases'):
        run(boundary, ['/bin/sh', '-c', f'touch -h -d @123 {writable}/alias'])
    assert target.lstat().st_mtime_ns == before.st_mtime_ns


def test_external_scratch_bind_coexists_with_private_tmp_tmpfs(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    external_root = tmp_path / 'external'
    external_root.mkdir()
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)], external_scratch=external_root)
    script = f'''
set -eu
printf private > /tmp/private-file
printf external > {EXTERNAL_SCRATCH_DESTINATION}/external-file
[ "$(cat /tmp/private-file)" = private ]
[ "$(cat {EXTERNAL_SCRATCH_DESTINATION}/external-file)" = external ]
if [ -e {EXTERNAL_SCRATCH_DESTINATION}/private-file ]; then exit 1; fi
if [ -e /tmp/external-file ]; then exit 2; fi
'''
    result = run(boundary, ['/bin/sh', '-c', script])
    assert result.returncode == 0, result.stderr
    assert (external_root / 'external-file').read_text() == 'external'
    assert not (external_root / 'private-file').exists()


def test_external_scratch_internal_symlink_escape_is_contained(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    external_root = tmp_path / 'external'
    external_root.mkdir()
    forbidden = tmp_path / 'forbidden'
    forbidden.mkdir()
    secret = forbidden / 'secret'
    secret.write_text('host secret')
    (external_root / 'escape').symlink_to(forbidden)
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)], external_scratch=external_root)
    script = f'''
set -eu
if cat {EXTERNAL_SCRATCH_DESTINATION}/escape/secret 2>/dev/null; then exit 1; fi
if (echo bad > {EXTERNAL_SCRATCH_DESTINATION}/escape/newfile) 2>/dev/null; then exit 2; fi
'''
    result = run(boundary, ['/bin/sh', '-c', script])
    assert result.returncode == 0, result.stderr
    assert secret.read_text() == 'host secret'
    assert not (forbidden / 'newfile').exists()
    assert sorted(p.name for p in forbidden.iterdir()) == ['secret']


# -- New in Slice A -----------------------------------------------------


def test_git_file_shaped_worktree_is_masked_as_empty_unwritable_regular_file(tmp_path):
    """N3 (the .git-file shape): the linked-worktree `.git` file is masked,
    not reachable, and a Git operation inside the view fails by design."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    gitdir = tmp_path / 'elsewhere-gitdir'
    gitdir.mkdir()
    (gitdir / 'HEAD').write_text('ref: refs/heads/main\n')
    (repo / '.git').write_text(f'gitdir: {gitdir}\n')
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    script = f'''
set -eu
[ -f {repo}/.git ]
[ ! -s {repo}/.git ]
if (echo bad > {repo}/.git) 2>/dev/null; then exit 1; fi
if git -C {repo} rev-parse HEAD 2>/dev/null; then exit 2; fi
'''
    result = run(boundary, ['/bin/sh', '-c', script])
    assert result.returncode == 0, result.stderr
    assert gitdir.exists()
    assert (gitdir / 'HEAD').read_text() == 'ref: refs/heads/main\n'


def test_linked_worktree_gitdir_and_common_dir_are_unreachable(tmp_path):
    """N4: neither the per-worktree gitdir nor the common Git directory is
    mounted or reachable, by absolute path, from a linked worktree."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    common_dir = tmp_path / 'main-repo' / '.git'
    common_dir.mkdir(parents=True)
    (common_dir / 'objects').mkdir()
    (common_dir / 'objects' / 'secret-object').write_text('object data')
    worktree_gitdir = common_dir / 'worktrees' / 'repo'
    worktree_gitdir.mkdir(parents=True)
    (repo / '.git').write_text(f'gitdir: {worktree_gitdir}\n')
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    script = f'''
set -eu
if [ -e {worktree_gitdir} ]; then exit 1; fi
if [ -e {common_dir} ]; then exit 2; fi
if cat {common_dir}/objects/secret-object 2>/dev/null; then exit 3; fi
'''
    result = run(boundary, ['/bin/sh', '-c', script])
    assert result.returncode == 0, result.stderr
    assert (common_dir / 'objects' / 'secret-object').read_text() == 'object data'


def test_etc_name_resolution_entries_are_readable_when_present(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    present = [p for p in ('/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf', '/etc/passwd')
               if os.path.exists(p)]
    assert present
    checks = ' && '.join(f'[ -r {path} ]' for path in present)
    result = run(boundary, ['/bin/sh', '-c', checks])
    assert result.returncode == 0, result.stderr


def test_pid_namespace_is_unshared(tmp_path):
    """N13, the visible-process-table half: the sandboxed process sees only
    its own branch of the process table. Without --as-pid-1, bwrap's own
    reaper is PID 1 of the new namespace and the launched command is a
    low-numbered descendant of it (not of any host PID) -- that is the
    property --unshare-pid plus --die-with-parent depend on, not a
    particular PID value for the command itself. The double-fork,
    timeout-and-kill, no-surviving-descendant half of N13 is
    ``tests/integration/test_execution_boundary_dispatch.py::test_n13_killing_a_timed_out_invocation_leaves_no_surviving_descendant``."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    result = run(boundary, ['/usr/bin/python3', '-c',
                            "import os; print(os.getpid()); print(sorted(int(n) for n in os.listdir('/proc') if n.isdigit()))"])
    assert result.returncode == 0, result.stderr
    lines = result.stdout.decode().splitlines()
    pid = int(lines[0])
    visible_pids = ast.literal_eval(lines[1])
    assert pid != 1  # bwrap's own reaper holds PID 1; the command is its child
    assert pid in visible_pids
    assert max(visible_pids) < 100  # only this sandbox's own small branch is visible


def test_tmp_size_cap_is_enforced_and_host_tmp_is_unaffected(tmp_path):
    """N12 (the /tmp size-cap half): writes beyond the cap fail inside the
    view, and nothing lands on the host's own /tmp."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    boundary = validate(
        repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)], tmp_size_bytes=1_048_576,
    )
    script = '''
set -eu
if dd if=/dev/zero of=/tmp/overflow bs=1M count=8 2>/dev/null; then exit 1; fi
'''
    result = run(boundary, ['/bin/sh', '-c', script])
    assert result.returncode == 0, result.stderr
    assert not (Path('/tmp') / 'overflow').exists()


def test_sandbox_root_remount_ro_does_not_disturb_writable_grants(tmp_path):
    """N12 (the sandbox-root half, the part a test can establish):
    --remount-ro / must not be recursive in a way that breaks the private
    /tmp, the external scratch or a writable repository grant.

    Whether --remount-ro / also closes bwrap's own base tmpfs at the sandbox
    root (<<mounts>>, the sandbox root row) is NOT established here. That is
    a one-time qualification measurement, taken with and without the flag on
    a named kernel and bwrap version and recorded in
    docs/warm_storage/post-beta-planning/wfc_execution_boundary_slice_a_layer1_qualification_record.adoc;
    it is not a recurring regression test, and this case asserts nothing
    about it in either direction.
    """
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    writable = repo / 'writable'
    writable.mkdir()
    external_root = tmp_path / 'external'
    external_root.mkdir()
    boundary = validate(
        repo, [Mount(writable, PurePosixPath(str(writable)), MountMode.WRITABLE),
               Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)],
        external_scratch=external_root,
    )
    script = f'''
set -eu
printf ok > {writable}/still-writable
printf ok > /tmp/still-writable
printf ok > {EXTERNAL_SCRATCH_DESTINATION}/still-writable
'''
    result = run(boundary, ['/bin/sh', '-c', script])
    assert result.returncode == 0, result.stderr
    assert (writable / 'still-writable').read_text() == 'ok'
    assert (external_root / 'still-writable').read_text() == 'ok'


def test_sandbox_cannot_create_a_new_mount_or_user_namespace(tmp_path):
    """N8: the sandboxed process cannot create a new mount or a new user
    namespace that re-mounts a host path."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    protected = repo / 'protected'
    protected.write_text('unchanged')
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    script = f'''
set -eu
if mount -t tmpfs tmpfs /tmp 2>/dev/null; then exit 1; fi
if {boundary.binary} --unshare-user --bind {repo} {repo} -- /bin/true 2>/dev/null; then exit 2; fi
'''
    result = run(boundary, ['/bin/sh', '-c', script])
    assert result.returncode == 0, result.stderr
    assert protected.read_text() == 'unchanged'


def test_provider_channel_both_directions(tmp_path):
    """The channel mount works both ways: read-only for a Claude-shaped
    invocation, read-write (including a rename-over-the-target) for a
    Codex-shaped one."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    channel = tmp_path / 'channel'
    channel.mkdir()
    (channel / 'settings.json').write_text('{"k": "v"}')

    read_only = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)], provider_channel=channel)
    result = run(read_only, ['/bin/sh', '-c', f'cat {channel}/settings.json'])
    assert result.returncode == 0, result.stderr
    assert result.stdout == b'{"k": "v"}'
    result = run(read_only, ['/bin/sh', '-c', f'echo bad > {channel}/result.json'])
    assert result.returncode != 0

    writable = validate(
        repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)],
        provider_channel=channel, provider_channel_writable=True,
    )
    script = f'''
set -eu
printf direct > {channel}/result.json
[ "$(cat {channel}/result.json)" = direct ]
printf renamed > {channel}/result.json.tmp
mv {channel}/result.json.tmp {channel}/result.json
[ "$(cat {channel}/result.json)" = renamed ]
'''
    result = run(writable, ['/bin/sh', '-c', script])
    assert result.returncode == 0, result.stderr
    assert (channel / 'result.json').read_text() == 'renamed'


def test_json_status_fd_is_written_by_bwrap_and_is_not_open_inside_the_view(tmp_path):
    """F1: real `bwrap`, real ``--json-status-fd``. (3), the Coordinator's
    half: the generated argv names the exact fd passed in, and the fd is
    genuinely written to by the outer, trusted `bwrap` process -- an
    ``exit-code`` document is observed for a command that ran and exited
    0. (3), the confined half: from inside the view, the very same fd
    number is not open at all, so the sandboxed process can neither forge
    nor withdraw what bwrap itself reports."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    read_fd, write_fd = os.pipe()
    try:
        argv = build_argv(
            boundary,
            ['/usr/bin/python3', '-c',
             f'import os\ntry:\n os.write({write_fd}, b"x")\nexcept OSError:\n print("closed")\nelse:\n print("open")'],
            status_fd=write_fd,
        )
        index = argv.index('--json-status-fd')
        assert argv[index + 1] == str(write_fd)
        result = subprocess.run(argv, capture_output=True, check=False, pass_fds=(write_fd,))
    finally:
        os.close(write_fd)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == b'closed'
    os.set_blocking(read_fd, False)
    try:
        chunk = os.read(read_fd, 65536)
    except BlockingIOError:
        chunk = b''
    os.close(read_fd)
    assert b'"exit-code"' in chunk


def test_json_status_fd_reports_no_exit_code_document_when_mount_setup_fails(tmp_path):
    """F1: the companion not-started case, through the same primitive, with
    a bind source that does not exist so `bwrap`'s own mount setup fails
    before any command is exec'd."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    missing_source = tmp_path / 'does-not-exist'
    boundary = validate(repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)])
    read_fd, write_fd = os.pipe()
    try:
        argv = build_argv(boundary, ['/bin/true'], status_fd=write_fd)
        # Corrupt one bind source after validate()/build_argv() have already
        # produced a valid argv, so bwrap's own mount setup -- not this
        # module's own validation -- is what fails.
        bind_index = argv.index('--ro-bind')
        argv[bind_index + 1] = str(missing_source)
        result = subprocess.run(argv, capture_output=True, check=False, pass_fds=(write_fd,))
    finally:
        os.close(write_fd)
    assert result.returncode != 0
    os.set_blocking(read_fd, False)
    try:
        chunk = os.read(read_fd, 65536)
    except BlockingIOError:
        chunk = b''
    os.close(read_fd)
    assert b'"exit-code"' not in chunk


def test_readable_host_path_is_not_writable(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    auth = tmp_path / 'auth-token'
    auth.write_text('secret-token')
    boundary = validate(
        repo, [Mount(None, PurePosixPath('/tmp'), MountMode.SCRATCH)], readable_host_paths=(auth,),
    )
    result = run(boundary, ['/bin/sh', '-c', f'cat {auth}'])
    assert result.returncode == 0, result.stderr
    assert result.stdout == b'secret-token'
    result = run(boundary, ['/bin/sh', '-c', f'echo bad > {auth}'])
    assert result.returncode != 0
    assert auth.read_text() == 'secret-token'
