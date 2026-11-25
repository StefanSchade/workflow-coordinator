"""The two version-consistency guards FTR-WFC-RELEASE-PREPARATION adds (R1).

Both guards run against this checkout's own current state, not a synthetic
fixture: `tests/unit/test_release_tooling.py` already covers the tool on
synthetic trees. Each guard is demonstrated to actually fail -- not merely
asserted to -- by running it again on a copy of the real file with one site
reverted.
"""

from __future__ import annotations

import re
import shutil
import tomllib
from pathlib import Path

from workflow_coordinator.implementation_identity import VERSION

ROOT = Path(__file__).resolve().parents[2]
BLOB_LINK = re.compile(r"https://github\.com/StefanSchade/workflow-coordinator/blob/v([^/]+)/")


def _pyproject_version(repo: Path) -> str:
    with (repo / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def _identity_version(repo: Path) -> str:
    text = (repo / "src/workflow_coordinator/implementation_identity.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^VERSION = "([^"]+)"$', text, re.MULTILINE)
    assert match, "implementation_identity.py has no VERSION literal"
    return match.group(1)


def check_version_consistency(repo: Path) -> str | None:
    """Guard 1: `pyproject.toml`'s version must equal `implementation_identity.VERSION`."""
    pyproject_version = _pyproject_version(repo)
    identity_version = _identity_version(repo)
    if pyproject_version != identity_version:
        return (
            f"pyproject.toml version {pyproject_version!r} does not match "
            f"implementation_identity.VERSION {identity_version!r}"
        )
    return None


def check_readme_blob_versions(repo: Path, version: str) -> str | None:
    """Guard 2: every `blob/v<x>/` URL in README.md must name `version`."""
    text = (repo / "README.md").read_text(encoding="utf-8")
    versions = set(BLOB_LINK.findall(text))
    stale = sorted(versions - {version})
    if stale:
        return f"README.md has blob link(s) pinned to version(s) other than {version!r}: {stale}"
    return None


def test_pyproject_version_matches_implementation_identity():
    assert check_version_consistency(ROOT) is None


def test_every_readme_blob_link_names_the_current_version():
    assert check_readme_blob_versions(ROOT, VERSION) is None


def test_guard_fails_when_pyproject_and_identity_disagree(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src/workflow_coordinator").mkdir(parents=True)
    shutil.copy(ROOT / "pyproject.toml", repo / "pyproject.toml")
    identity_path = repo / "src/workflow_coordinator/implementation_identity.py"
    shutil.copy(ROOT / "src/workflow_coordinator/implementation_identity.py", identity_path)

    original = identity_path.read_text(encoding="utf-8")
    mutated = original.replace(f'VERSION = "{VERSION}"', 'VERSION = "9.9.9"')
    assert mutated != original, "fixture did not change the VERSION literal"
    identity_path.write_text(mutated, encoding="utf-8")

    error = check_version_consistency(repo)
    assert error is not None and "9.9.9" in error


def test_guard_fails_when_a_readme_blob_link_is_stale(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    readme_path = repo / "README.md"
    shutil.copy(ROOT / "README.md", readme_path)

    original = readme_path.read_text(encoding="utf-8")
    stale = original.replace(f"/blob/v{VERSION}/", "/blob/v9.9.9/", 1)
    assert stale != original, "fixture did not revert a blob link"
    readme_path.write_text(stale, encoding="utf-8")

    error = check_readme_blob_versions(repo, VERSION)
    assert error is not None and "9.9.9" in error
