"""A non-Python consumer at the current literal evidence admission seam."""
import os
import subprocess

from workflow_coordinator.evidence import PRODUCTION_EVIDENCE_RUNNER, prepare_evidence_commands
from workflow_coordinator.model import EvidenceCommand


def test_shell_consumer_uses_real_evidence_runner(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'checks').mkdir()
    (tmp_path / 'story.txt').write_text('ready\n')
    (tmp_path / 'checks/check.sh').write_text('IFS= read -r value < story.txt\n[ "$value" = ready ]\n')
    command = EvidenceCommand(('sh', 'checks/check.sh'), 10)
    prepared = prepare_evidence_commands(('check',), {'check': command}, tmp_path, environ={'PATH': os.defpath})[0]
    assert PRODUCTION_EVIDENCE_RUNNER.observe(prepared).status == 'pass'
    (tmp_path / 'story.txt').write_text('broken\n')
    assert PRODUCTION_EVIDENCE_RUNNER.observe(prepared).status == 'fail'
    assert not list(tmp_path.rglob('*.py')) and not (tmp_path / 'pyproject.toml').exists()
