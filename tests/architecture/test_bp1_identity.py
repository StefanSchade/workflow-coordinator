"""Installed identity and compatibility checks using real offline builds."""
import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def call(*args, cwd):
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)


def test_installed_identity_and_real_preflight(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    for name in ('pyproject.toml', 'README.md', 'LICENSE'):
        shutil.copy(ROOT / name, source / name)
    shutil.copytree(ROOT / 'src', source / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info'))
    dist = tmp_path / 'dist'
    result = call(sys.executable, '-m', 'build', '--outdir', str(dist), str(source), cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (result.stdout + result.stderr).count('Creating isolated environment') >= 2
    assert len(list(dist.glob('*.tar.gz'))) == len(list(dist.glob('*.whl'))) == 1
    venv = tmp_path / 'venv'
    assert call(sys.executable, '-m', 'venv', str(venv), cwd=tmp_path).returncode == 0
    python, wfc = venv / 'bin/python', venv / 'bin/wfc'
    install = call(str(python), '-m', 'pip', 'install', str(next(dist.glob('*.whl'))), cwd=tmp_path)
    assert install.returncode == 0, install.stderr
    def identity():
        result = call(str(wfc), '--version', '--json', cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    first = identity()
    assert first == identity()
    assert first['version'] == '0.1.0b1' and first['source_commit'] is None
    assert len(first['resources']) == 4
    package = next(venv.glob('lib/python*/site-packages/workflow_coordinator'))
    target = package / 'model.py'
    target.write_bytes(target.read_bytes() + b'\n')
    assert identity()['implementation_tree_sha256'] != first['implementation_tree_sha256']
    result = call(str(wfc), '--repository', str(tmp_path / 'missing'), '--state-root', str(tmp_path / 'state'),
                  '--expected-head', '0' * 40, '--work-item', 'x', '--profile', 'x', '--policy', 'x',
                  '--components', 'x', '--task', 'x', '--execution-bindings', 'x', cwd=tmp_path)
    assert result.returncode == 2 and 'repository path is not a directory' in result.stderr
    resource = package / 'resources/roles/worker.txt'
    resource.write_bytes(resource.read_bytes() + b'!')
    result = call(str(wfc), '--version', '--json', cwd=tmp_path)
    assert result.returncode == 2 and 'digest mismatch' in result.stderr


def test_roles_match_reviewed_sources_and_kernel_import_direction():
    from workflow_coordinator.implementation_identity import implementation_identity
    assert implementation_identity()['resources']
    for path in (ROOT / 'governance/self-development/prompts').glob('*.txt'):
        assert path.read_bytes() == (ROOT / 'src/workflow_coordinator/resources/roles' / path.name).read_bytes()
    for path in (ROOT / 'src/workflow_coordinator').rglob('*.py'):
        if 'frontdoor' in path.parts:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not (n.name == 'workflow_coordinator.frontdoor' or n.name.startswith('workflow_coordinator.frontdoor.')) for n in node.names), path
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or '').startswith(('frontdoor', 'workflow_coordinator.frontdoor')), path
                assert not any(n.name == 'frontdoor' for n in node.names), path


def test_dispatch_preserves_legacy_arguments(monkeypatch):
    from workflow_coordinator import operator
    from workflow_coordinator.frontdoor.cli import main
    seen = []
    monkeypatch.setattr(operator, 'main', lambda argv: seen.append(argv) or 17)
    arguments = ['--repository', '/somewhere', '--durable-artifact', 'phase=path', '--executable', 'fake']
    assert main(arguments) == 17
    assert seen == [arguments]


def test_source_commit_requires_clean_checkout_and_matching_bytes(tmp_path, monkeypatch):
    from workflow_coordinator import implementation_identity as identity
    root = tmp_path / 'src/workflow_coordinator'
    root.mkdir(parents=True)
    module = root / 'example.py'
    module.write_bytes(b'original')
    dirty = []
    def git(command, stderr):
        args = command[3:]
        if args == ['rev-parse', '--show-toplevel']:
            return str(tmp_path).encode()
        if args == ['status', '--porcelain', '--untracked-files=all']:
            return b'M changed\n' if dirty else b''
        if args == ['rev-parse', '--verify', 'HEAD']:
            return b'a' * 40 + b'\n'
        if args[0] == 'ls-tree':
            return b'src/workflow_coordinator/example.py\n'
        if args[0] == 'show':
            return b'original'
        raise AssertionError(args)
    monkeypatch.setattr(identity.subprocess, 'check_output', git)
    assert identity._source_commit(root, [module]) == 'a' * 40
    module.write_bytes(b'changed')
    assert identity._source_commit(root, [module]) is None
    module.write_bytes(b'original')
    dirty.append(True)
    assert identity._source_commit(root, [module]) is None
