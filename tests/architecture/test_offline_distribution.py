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
        names = archive.namelist()
        assert not [name for name in names if 'examples/' in name]
        # S1 (FTR-WFC-AGENT-SKILL-DISTRIBUTION, AC-1): exactly the four packaged
        # skill files appear under agent_skill/, by exact name, not by a glob.
        skill_entries = {name for name in names if 'agent_skill/' in name}
        assert skill_entries == set(dist_audit.WHEEL_SKILL_FILES)
    sdist = next(dist.glob('*.tar.gz'))
    with tarfile.open(sdist, 'r:gz') as archive:
        assert not [member.name for member in archive.getmembers() if 'examples/' in member.name]
    venv = tmp_path / 'installed'
    run(sys.executable, '-m', 'venv', str(venv), cwd=tmp_path)
    python = venv / 'bin/python'
    run(str(python), '-m', 'pip', 'install', str(next(dist.glob('*.whl'))), cwd=tmp_path)
    run(str(python), '-m', 'pip', 'check', cwd=tmp_path)
    run(str(venv / 'bin/wfc'), '--help', cwd=tmp_path)
    # S2 (FTR-WFC-AGENT-SKILL-DISTRIBUTION, AC-2): from a clean environment, in a
    # directory outside the checkout, `wfc skill show` prints the packaged SKILL.md
    # and `wfc skill install` works for both hosts into a temporary home.
    show = run(str(venv / 'bin/wfc'), 'skill', 'show', cwd=tmp_path)
    assert show.stdout.startswith('---\nname: wfc\n')
    for host, skill_subpath in (
        ('claude', ('.claude', 'skills', 'wfc')),
        ('codex', ('.agents', 'skills', 'wfc')),
    ):
        fake_home = tmp_path / f'home-{host}'
        fake_home.mkdir()
        host_env = os.environ.copy()
        host_env['HOME'] = str(fake_home)
        host_env.pop('PYTHONPATH', None)
        host_env.pop('PYTHONSAFEPATH', None)
        run(str(venv / 'bin/wfc'), 'skill', 'install', host, cwd=tmp_path, env=host_env)
        installed_skill_md = fake_home.joinpath(*skill_subpath, 'SKILL.md')
        assert installed_skill_md.is_file()
        assert installed_skill_md.read_text() == show.stdout
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
