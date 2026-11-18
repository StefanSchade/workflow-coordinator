"""Identity of the executing package and its reviewed bundled resources."""
from __future__ import annotations

import hashlib
from importlib.resources import files as resource_files
import subprocess
from pathlib import Path

VERSION = "0.1.0b1"
RESOURCE_DIGESTS = {
    "agent_operating_contract_v6.txt": "7abf57ab315965c4266674efdb71c6cd1415989cdb771b9232501563862ca8f7",
    "roles/reviewer.txt": "739a5db2044b7897655c0120ea9f0c4c2c5fc8cc3074a7f03e93385f67dac4b4",
    "roles/simple-self-check.txt": "7b5436e7998c955b0ed06812fdfb575d27eed4879334d154f84c2b9b3c7e97cb",
    "roles/worker.txt": "179656ee9824c91c151c7edb7284ab848067a28fc575b1e0fe5549df8a99daab",
}


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo'))


def _source_commit(root: Path, files: list[Path]) -> str | None:
    # Only a checkout with precisely the committed package bytes can establish
    # this fact. Installed wheels and exported archives have no such proof.
    if root.parent.name != 'src':
        return None
    repo = root.parent.parent
    def git(*args: str) -> bytes:
        return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL)
    try:
        if Path(git('rev-parse', '--show-toplevel').decode().strip()).resolve() != repo.resolve():
            return None
        if git('status', '--porcelain', '--untracked-files=all'):
            return None
        commit = git('rev-parse', '--verify', 'HEAD').decode().strip()
        paths = git('ls-tree', '-r', '--name-only', commit, '--', 'src/workflow_coordinator').decode().splitlines()
        relative = {str(p.relative_to(repo)) for p in files}
        if relative != set(paths):
            return None
        for path in paths:
            if (repo / path).read_bytes() != git('show', f'{commit}:{path}'):
                return None
        return commit
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        return None


def implementation_identity() -> dict:
    root = Path(str(resource_files('workflow_coordinator'))).resolve()
    for name, expected in RESOURCE_DIGESTS.items():
        path = root / 'resources' / name
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise RuntimeError(f'Cannot read reviewed resource {name}: {error}') from error
        if actual != expected:
            raise RuntimeError(f'Reviewed resource digest mismatch: {name}')
    files = _files(root)
    digest = hashlib.sha256()
    for path in files:
        name = path.relative_to(root).as_posix().encode('utf-8')
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, 'big'))
        digest.update(name)
        digest.update(len(data).to_bytes(8, 'big'))
        digest.update(data)
    return {'version': VERSION, 'source_commit': _source_commit(root, files),
            'implementation_tree_sha256': digest.hexdigest(), 'resources': dict(RESOURCE_DIGESTS)}
