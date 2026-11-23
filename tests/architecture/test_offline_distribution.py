"""Real isolated distribution builds and installed-package checks."""

import importlib.util
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "wfc_dist_audit", ROOT / "scripts/wfc_dist_audit.py"
)
dist_audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dist_audit)
PROBE = '''
import hashlib, importlib.resources, pathlib, workflow_coordinator
p = pathlib.Path(workflow_coordinator.__file__).resolve()
assert p.is_relative_to(pathlib.Path(__import__('sys').prefix).resolve()), p
r = importlib.resources.files('workflow_coordinator').joinpath('resources/agent_operating_contract_v6.txt')
assert hashlib.sha256(r.read_bytes()).hexdigest() == '7abf57ab315965c4266674efdb71c6cd1415989cdb771b9232501563862ca8f7'
assert sorted(x.name for x in r.parent.iterdir() if x.name.startswith('agent_operating_contract_v')) == ['agent_operating_contract_v6.txt']
'''


def run(*args, cwd, env=None):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_clean_isolated_distribution_and_wheel_install(tmp_path):
    export = tmp_path / 'source'
    export.mkdir()
    archive = subprocess.run(['git', 'archive', 'HEAD'], cwd=ROOT, capture_output=True, check=True).stdout
    subprocess.run(['tar', '-x', '-C', str(export)], input=archive, check=True)
    dist = tmp_path / 'dist'
    # The default build frontend builds an sdist and then a wheel from that sdist,
    # each in a fresh PEP-517 isolated environment.
    result = run(sys.executable, '-m', 'build', '--outdir', str(dist), str(export), cwd=tmp_path)
    assert (result.stdout + result.stderr).count('Creating isolated environment') >= 2
    assert len(list(dist.glob('*.tar.gz'))) == len(list(dist.glob('*.whl'))) == 1
    for archive in (*dist.glob('*.whl'), *dist.glob('*.tar.gz')):
        assert dist_audit.audit(archive) == []
    # AC-9 (FTR-WFC-CONSUMER-DEMO-WORKSPACE): neither artifact carries any
    # path under examples/, the new top-level directory the consumer demo
    # kit lives in.
    wheel = next(dist.glob('*.whl'))
    with zipfile.ZipFile(wheel) as archive:
        assert not [name for name in archive.namelist() if 'examples/' in name]
    sdist = next(dist.glob('*.tar.gz'))
    with tarfile.open(sdist, 'r:gz') as archive:
        assert not [member.name for member in archive.getmembers() if 'examples/' in member.name]
    venv = tmp_path / 'installed'
    run(sys.executable, '-m', 'venv', str(venv), cwd=tmp_path)
    python = venv / 'bin/python'
    run(str(python), '-m', 'pip', 'install', str(next(dist.glob('*.whl'))), cwd=tmp_path)
    run(str(python), '-m', 'pip', 'check', cwd=tmp_path)
    run(str(venv / 'bin/wfc'), '--help', cwd=tmp_path)
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONSAFEPATH', None)
    run(str(python), '-I', '-c', PROBE, cwd=tmp_path, env=env)
    # A source override must not defeat isolated mode, and a missing resource must fail.
    env['PYTHONPATH'] = str(ROOT / 'src')
    run(str(python), '-I', '-c', PROBE, cwd=tmp_path, env=env)
    resource = next(venv.glob('lib/python*/site-packages/workflow_coordinator/resources/agent_operating_contract_v6.txt'))
    resource.unlink()
    damaged = subprocess.run([str(python), '-I', '-c', PROBE], cwd=tmp_path, env=env, capture_output=True)
    assert damaged.returncode != 0
