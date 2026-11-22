"""Synthetic-repository and synthetic-archive coverage for the release tools.

No test here depends on this checkout's own state or reaches the network:
each release-check case builds its own Git repository plus a local bare
'origin', and each dist-audit case builds its own in-memory wheel or sdist.
"""

from __future__ import annotations

import importlib.util
import io
import subprocess
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_check = _load("wfc_release_check", "scripts/wfc_release_check.py")
dist_audit = _load("wfc_dist_audit", "scripts/wfc_dist_audit.py")

VERSION = "0.1.0b1"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args), text=True, capture_output=True, check=True
    ).stdout.strip()


def _write_baseline(
    repo: Path,
    *,
    version: str = VERSION,
    notes_version: str | None = None,
    notes_status: str = "(released)",
    readme_relative: bool = False,
) -> None:
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "workflow_coordinator"\nversion = "{version}"\n', encoding="utf-8"
    )
    notes_dir = repo / "docs" / "release_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "release_notes.adoc").write_text(
        f"= Release Notes\n\n== {notes_version or version} -- {notes_status}\n\nBody.\n",
        encoding="utf-8",
    )
    link = "docs/guide.adoc" if readme_relative else "https://example.invalid/guide.adoc"
    (repo / "README.md").write_text(f"# Project\n\nSee [docs]({link}).\n", encoding="utf-8")


def _init_repo_pair(tmp_path: Path, **baseline_kwargs) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(("git", "init", "--bare", str(origin)), check=True, capture_output=True)
    repo = tmp_path / "repo"
    subprocess.run(("git", "init", str(repo)), check=True, capture_output=True)
    for key, value in (("user.email", "fixture@example.invalid"), ("user.name", "Fixture")):
        subprocess.run(("git", "-C", str(repo), "config", key, value), check=True)
    _write_baseline(repo, **baseline_kwargs)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "chore: baseline")
    _git(repo, "branch", "-M", "main")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "origin", "main")
    _git(repo, "fetch", "origin")
    return repo


# ---------------------------------------------------------------------------
# Branch mode
# ---------------------------------------------------------------------------


def test_branch_mode_passes_clean_release_candidate(tmp_path):
    repo = _init_repo_pair(tmp_path)
    mode, failures = release_check.run_checks(repo, None)
    assert mode == "branch"
    assert failures == []


def test_branch_mode_refuses_wrong_branch(tmp_path):
    repo = _init_repo_pair(tmp_path)
    _git(repo, "checkout", "-b", "topic")
    mode, failures = release_check.run_checks(repo, None)
    assert mode == "branch"
    assert any("not 'main'" in failure for failure in failures)


def test_branch_mode_refuses_dirty_tree(tmp_path):
    repo = _init_repo_pair(tmp_path)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    _, failures = release_check.run_checks(repo, None)
    assert any("not clean" in failure for failure in failures)


def test_branch_mode_refuses_local_main_ahead_of_origin(tmp_path):
    repo = _init_repo_pair(tmp_path)
    (repo / "extra.txt").write_text("more\n", encoding="utf-8")
    _git(repo, "add", "extra.txt")
    _git(repo, "commit", "-m", "chore: extra")
    _, failures = release_check.run_checks(repo, None)
    assert any("out of sync" in failure for failure in failures)
    assert any("not reachable" in failure for failure in failures)


def test_branch_mode_refuses_missing_origin_tracking(tmp_path):
    repo = _init_repo_pair(tmp_path)
    _git(repo, "remote", "remove", "origin")
    _, failures = release_check.run_checks(repo, None)
    assert any("not known locally" in failure for failure in failures)


def test_branch_mode_refuses_tag_version_mismatch(tmp_path):
    repo = _init_repo_pair(tmp_path)
    _, failures = release_check.run_checks(repo, "v9.9.9")
    assert any("does not match the version-derived tag" in failure for failure in failures)


def test_branch_mode_refuses_missing_release_notes_section(tmp_path):
    repo = _init_repo_pair(tmp_path, notes_version="9.9.9")
    _, failures = release_check.run_checks(repo, None)
    assert any("no section for version" in failure for failure in failures)


def test_branch_mode_refuses_planned_release_notes(tmp_path):
    repo = _init_repo_pair(tmp_path, notes_status="planned")
    _, failures = release_check.run_checks(repo, None)
    assert any("still marked 'planned'" in failure for failure in failures)


def test_branch_mode_refuses_relative_readme_link(tmp_path):
    repo = _init_repo_pair(tmp_path, readme_relative=True)
    _, failures = release_check.run_checks(repo, None)
    assert any("repository-relative documentation link" in failure for failure in failures)


# ---------------------------------------------------------------------------
# Tag mode
# ---------------------------------------------------------------------------


def test_tag_mode_passes_on_tagged_commit_reachable_from_origin(tmp_path):
    repo = _init_repo_pair(tmp_path)
    _git(repo, "tag", f"v{VERSION}")
    _git(repo, "checkout", "--detach", f"v{VERSION}")
    mode, failures = release_check.run_checks(repo, None)
    assert mode == "tag"
    assert failures == []


def test_tag_mode_refuses_unreachable_commit(tmp_path):
    repo = _init_repo_pair(tmp_path)
    (repo / "extra.txt").write_text("more\n", encoding="utf-8")
    _git(repo, "add", "extra.txt")
    _git(repo, "commit", "-m", "chore: extra")
    _git(repo, "tag", f"v{VERSION}")
    _git(repo, "checkout", "--detach", f"v{VERSION}")
    mode, failures = release_check.run_checks(repo, None)
    assert mode == "tag"
    assert any("not reachable from origin/main" in failure for failure in failures)


def test_tag_mode_refuses_commit_without_exact_tag(tmp_path):
    repo = _init_repo_pair(tmp_path)
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "--detach", sha)
    mode, failures = release_check.run_checks(repo, None)
    assert mode == "tag"
    assert any("not checked out at an exact tag" in failure for failure in failures)


def test_tag_mode_refuses_explicit_tag_mismatch(tmp_path):
    repo = _init_repo_pair(tmp_path)
    _git(repo, "tag", f"v{VERSION}")
    _git(repo, "checkout", "--detach", f"v{VERSION}")
    _, failures = release_check.run_checks(repo, "v9.9.9")
    assert any("does not match the version-derived tag" in failure for failure in failures)


def test_tag_mode_refuses_planned_notes_and_relative_readme(tmp_path):
    repo = _init_repo_pair(tmp_path, notes_status="planned", readme_relative=True)
    _git(repo, "tag", f"v{VERSION}")
    _git(repo, "checkout", "--detach", f"v{VERSION}")
    mode, failures = release_check.run_checks(repo, None)
    assert mode == "tag"
    assert any("still marked 'planned'" in failure for failure in failures)
    assert any("repository-relative documentation link" in failure for failure in failures)


# ---------------------------------------------------------------------------
# Distribution audit
# ---------------------------------------------------------------------------

WHEEL_NAME = "workflow_coordinator-0.1.0b1-py3-none-any.whl"
SDIST_NAME = "workflow_coordinator-0.1.0b1.tar.gz"


def _write_wheel(path: Path, entries: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


def _conforming_wheel_entries() -> dict[str, str]:
    entries = {
        "workflow_coordinator/__init__.py": "",
        "workflow_coordinator/frontdoor/cli.py": "",
        "workflow_coordinator-0.1.0b1.dist-info/METADATA": "x",
        "workflow_coordinator-0.1.0b1.dist-info/RECORD": "x",
    }
    for resource in dist_audit.WHEEL_RESOURCE_FILES:
        entries[resource] = "x"
    return entries


def _write_sdist(
    path: Path,
    prefix: str,
    entries: dict[str, str],
    directories: tuple[str, ...] = (),
    unprefixed: dict[str, str | None] | None = None,
) -> None:
    with tarfile.open(path, "w:gz") as archive:
        root = tarfile.TarInfo(prefix)
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        for directory in directories:
            info = tarfile.TarInfo(f"{prefix}/{directory}")
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
        for name, content in entries.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(f"{prefix}/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        for name, content in (unprefixed or {}).items():
            if content is None:
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                data = content.encode("utf-8")
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))


def _conforming_sdist_entries() -> dict[str, str]:
    return {
        "pyproject.toml": "x",
        "setup.cfg": "x",
        "PKG-INFO": "x",
        "README.md": "x",
        "LICENSE": "x",
        "src/workflow_coordinator/__init__.py": "x",
    }


def test_dist_audit_accepts_a_conforming_wheel_and_sdist(tmp_path):
    wheel = tmp_path / WHEEL_NAME
    _write_wheel(wheel, _conforming_wheel_entries())
    sdist = tmp_path / SDIST_NAME
    _write_sdist(
        sdist,
        "workflow_coordinator-0.1.0b1",
        _conforming_sdist_entries(),
        directories=("src", "src/workflow_coordinator"),
    )
    assert dist_audit.audit(wheel) == []
    assert dist_audit.audit(sdist) == []


def test_dist_audit_rejects_an_unlisted_wheel_entry(tmp_path):
    entries = _conforming_wheel_entries()
    entries["workflow_coordinator/evil.txt"] = "bad"
    wheel = tmp_path / WHEEL_NAME
    _write_wheel(wheel, entries)
    violations = dist_audit.audit(wheel)
    assert any("evil.txt" in violation for violation in violations)


def test_dist_audit_rejects_a_forbidden_wheel_directory_entry(tmp_path):
    entries = _conforming_wheel_entries()
    entries["docs/"] = "bad"
    wheel = tmp_path / WHEEL_NAME
    _write_wheel(wheel, entries)
    violations = dist_audit.audit(wheel)
    assert any(
        "docs/" in violation and "forbidden by name" in violation for violation in violations
    )


def test_dist_audit_rejects_a_forbidden_sdist_entry(tmp_path):
    entries = _conforming_sdist_entries()
    entries["docs/evil.adoc"] = "bad"
    sdist = tmp_path / SDIST_NAME
    _write_sdist(
        sdist,
        "workflow_coordinator-0.1.0b1",
        entries,
        directories=("src", "src/workflow_coordinator", "docs"),
    )
    violations = dist_audit.audit(sdist)
    assert any(
        "docs/evil.adoc" in violation and "forbidden by name" in violation
        for violation in violations
    )


def test_dist_audit_rejects_a_forbidden_sdist_directory_entry(tmp_path):
    sdist = tmp_path / SDIST_NAME
    _write_sdist(
        sdist,
        "workflow_coordinator-0.1.0b1",
        _conforming_sdist_entries(),
        directories=("src", "src/workflow_coordinator", "docs"),
    )
    violations = dist_audit.audit(sdist)
    assert any(
        "/docs" in violation and "forbidden by name" in violation for violation in violations
    )


def test_dist_audit_rejects_an_sdist_directory_outside_the_archive_root(tmp_path):
    sdist = tmp_path / SDIST_NAME
    _write_sdist(
        sdist,
        "workflow_coordinator-0.1.0b1",
        _conforming_sdist_entries(),
        directories=("src", "src/workflow_coordinator"),
        unprefixed={"docs": None},
    )
    violations = dist_audit.audit(sdist)
    assert any("'docs'" in violation for violation in violations)


def test_dist_audit_rejects_an_sdist_file_outside_the_archive_root(tmp_path):
    sdist = tmp_path / SDIST_NAME
    _write_sdist(
        sdist,
        "workflow_coordinator-0.1.0b1",
        _conforming_sdist_entries(),
        directories=("src", "src/workflow_coordinator"),
        unprefixed={"secret.ods": "bad"},
    )
    violations = dist_audit.audit(sdist)
    assert any("secret.ods" in violation for violation in violations)


def test_dist_audit_rejects_a_wheel_missing_a_required_resource(tmp_path):
    entries = _conforming_wheel_entries()
    del entries["workflow_coordinator/resources/roles/reviewer.txt"]
    wheel = tmp_path / WHEEL_NAME
    _write_wheel(wheel, entries)
    violations = dist_audit.audit(wheel)
    assert any("missing required resource file" in violation for violation in violations)
