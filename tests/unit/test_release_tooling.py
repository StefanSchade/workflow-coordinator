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
release_prepare = _load("wfc_release_prepare", "scripts/wfc_release_prepare.py")
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
    readme_blob_version: str | None = None,
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
    if readme_relative:
        link = "docs/guide.adoc"
    elif readme_blob_version is not None:
        link = (
            "https://github.com/StefanSchade/workflow-coordinator/blob/"
            f"v{readme_blob_version}/docs/guide.adoc"
        )
    else:
        link = "https://example.invalid/guide.adoc"
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


def test_branch_mode_passes_readme_blob_link_pinned_to_the_released_version(tmp_path):
    repo = _init_repo_pair(tmp_path, readme_blob_version=VERSION)
    _, failures = release_check.run_checks(repo, None)
    assert failures == []


def test_branch_mode_refuses_readme_blob_link_pinned_to_a_stale_version(tmp_path):
    repo = _init_repo_pair(tmp_path, readme_blob_version="9.9.9")
    _, failures = release_check.run_checks(repo, None)
    assert any(
        "blob link(s) pinned to a version other than" in failure and "9.9.9" in failure
        for failure in failures
    )


def test_branch_mode_readme_blob_check_still_refuses_a_relative_link(tmp_path):
    repo = _init_repo_pair(tmp_path, readme_relative=True)
    _, failures = release_check.run_checks(repo, None)
    assert any("repository-relative documentation link" in failure for failure in failures)
    assert not any("blob link(s) pinned to a version other than" in failure for failure in failures)


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
# Release preparation
# ---------------------------------------------------------------------------

PREPARE_OLD = "1.0.0"
PREPARE_NEW = "2.0.0"


def _blob_link(version: str) -> str:
    return f"https://github.com/StefanSchade/workflow-coordinator/blob/v{version}/docs/x.adoc"


def _write_surface_tree(
    repo: Path, *, old: str = PREPARE_OLD, notes_status: str = "planned"
) -> None:
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "workflow_coordinator"\nversion = "{old}"\n', encoding="utf-8"
    )

    identity_dir = repo / "src" / "workflow_coordinator"
    identity_dir.mkdir(parents=True)
    (identity_dir / "implementation_identity.py").write_text(
        f'"""Identity."""\nVERSION = "{old}"\n', encoding="utf-8"
    )

    bp1_dir = repo / "tests" / "architecture"
    bp1_dir.mkdir(parents=True)
    (bp1_dir / "test_bp1_identity.py").write_text(
        "def test_identity():\n"
        f"    assert first['version'] == '{old}' and first['source_commit'] is None\n",
        encoding="utf-8",
    )

    (repo / "README.md").write_text(
        f"# Project\n\nSee [docs]({_blob_link(old)}).\n\n"
        f"Once {old} is published to PyPI.\n\n"
        f"`python3 -m pip install 'workflow-coordinator=={old}'`\n",
        encoding="utf-8",
    )

    skill_dir = identity_dir / "agent_skill" / "wfc"
    refs_dir = skill_dir / "references"
    refs_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"[link]({_blob_link(old)})\n", encoding="utf-8")
    (refs_dir / "commission.md").write_text(f"[link]({_blob_link(old)})\n", encoding="utf-8")
    (refs_dir / "setup.md").write_text(
        f"[link]({_blob_link(old)})\n\n`python3 -m pip install 'workflow-coordinator=={old}'`\n",
        encoding="utf-8",
    )
    (refs_dir / "stops-and-evidence.md").write_text(
        f"[link]({_blob_link(old)})\n", encoding="utf-8"
    )

    quickstart_dir = repo / "docs" / "operator_manual" / "ch09_consumer_quickstart"
    quickstart_dir.mkdir(parents=True)
    (quickstart_dir / "01_quickstart.adoc").write_text(
        f"Once {old} is published to PyPI.\n\n"
        f"`python3 -m pip install 'workflow-coordinator=={old}'`\n\n"
        f"The exact `=={old}` pin installs this.\n\n"
        f'Reports `"version": "{old}"` here.\n',
        encoding="utf-8",
    )

    notes_dir = repo / "docs" / "release_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "release_notes.adoc").write_text(
        f"= Notes\n\n== {PREPARE_NEW} -- {notes_status}\n\nNew body.\n\n"
        f"== {old} -- (released)\n\nOld body.\n",
        encoding="utf-8",
    )


def _snapshot(repo: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in sorted(repo.rglob("*")) if path.is_file()}


def test_release_prepare_rewrites_every_enumerated_site_and_nothing_else(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_surface_tree(repo)

    errors = release_prepare.prepare(repo, PREPARE_NEW)

    assert errors == []
    assert (repo / "pyproject.toml").read_text(encoding="utf-8") == (
        f'[project]\nname = "workflow_coordinator"\nversion = "{PREPARE_NEW}"\n'
    )
    assert (repo / "src/workflow_coordinator/implementation_identity.py").read_text(
        encoding="utf-8"
    ) == f'"""Identity."""\nVERSION = "{PREPARE_NEW}"\n'
    assert (repo / "tests/architecture/test_bp1_identity.py").read_text(encoding="utf-8") == (
        f"def test_identity():\n"
        f"    assert first['version'] == '{PREPARE_NEW}' and first['source_commit'] is None\n"
    )
    assert (repo / "README.md").read_text(encoding="utf-8") == (
        f"# Project\n\nSee [docs]({_blob_link(PREPARE_NEW)}).\n\n"
        f"Once {PREPARE_NEW} is published to PyPI.\n\n"
        f"`python3 -m pip install 'workflow-coordinator=={PREPARE_NEW}'`\n"
    )
    skill_dir = repo / "src/workflow_coordinator/agent_skill/wfc"
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == (
        f"[link]({_blob_link(PREPARE_NEW)})\n"
    )
    assert (skill_dir / "references/commission.md").read_text(encoding="utf-8") == (
        f"[link]({_blob_link(PREPARE_NEW)})\n"
    )
    assert (skill_dir / "references/setup.md").read_text(encoding="utf-8") == (
        f"[link]({_blob_link(PREPARE_NEW)})\n\n"
        f"`python3 -m pip install 'workflow-coordinator=={PREPARE_NEW}'`\n"
    )
    assert (skill_dir / "references/stops-and-evidence.md").read_text(encoding="utf-8") == (
        f"[link]({_blob_link(PREPARE_NEW)})\n"
    )
    quickstart = repo / "docs/operator_manual/ch09_consumer_quickstart/01_quickstart.adoc"
    assert quickstart.read_text(encoding="utf-8") == (
        f"Once {PREPARE_NEW} is published to PyPI.\n\n"
        f"`python3 -m pip install 'workflow-coordinator=={PREPARE_NEW}'`\n\n"
        f"The exact `=={PREPARE_NEW}` pin installs this.\n\n"
        f'Reports `"version": "{PREPARE_NEW}"` here.\n'
    )
    notes = repo / "docs/release_notes/release_notes.adoc"
    assert notes.read_text(encoding="utf-8") == (
        f"= Notes\n\n== {PREPARE_NEW} -- (released)\n\nNew body.\n\n"
        f"== {PREPARE_OLD} -- (released)\n\nOld body.\n"
    )


def test_release_prepare_is_idempotent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_surface_tree(repo)

    assert release_prepare.prepare(repo, PREPARE_NEW) == []
    after_first = _snapshot(repo)

    errors = release_prepare.prepare(repo, PREPARE_NEW)

    assert errors != []
    assert any("already" in error and "released" in error for error in errors)
    assert _snapshot(repo) == after_first


def test_release_prepare_refuses_an_invalid_version(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_surface_tree(repo)
    before = _snapshot(repo)

    errors = release_prepare.prepare(repo, "not-a-version")

    assert any("not a valid PEP 440 version" in error for error in errors)
    assert _snapshot(repo) == before


def test_release_prepare_refuses_a_missing_release_notes_section(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_surface_tree(repo)
    before = _snapshot(repo)

    errors = release_prepare.prepare(repo, "3.0.0")

    assert any("no section for version 3.0.0" in error for error in errors)
    assert _snapshot(repo) == before


def test_release_prepare_refuses_an_already_released_notes_section(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_surface_tree(repo)
    before = _snapshot(repo)

    errors = release_prepare.prepare(repo, PREPARE_OLD)

    assert any("already '(released)'" in error for error in errors)
    assert _snapshot(repo) == before


def test_release_prepare_refuses_a_missing_enumerated_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_surface_tree(repo)
    (repo / "README.md").unlink()
    before = _snapshot(repo)

    errors = release_prepare.prepare(repo, PREPARE_NEW)

    assert any("site file not found: README.md" in error for error in errors)
    assert _snapshot(repo) == before


def test_release_prepare_refuses_a_version_literal_matched_zero_times(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_surface_tree(repo)
    bp1 = repo / "tests/architecture/test_bp1_identity.py"
    bp1.write_text(
        'def test_identity():\n    assert first["version"] == "1.0.0"\n', encoding="utf-8"
    )
    before = _snapshot(repo)

    errors = release_prepare.prepare(repo, PREPARE_NEW)

    assert any(
        "test_bp1_identity.py" in error
        and "expected exactly one occurrence" in error
        and "found 0" in error
        for error in errors
    )
    assert _snapshot(repo) == before


def test_release_prepare_refuses_a_version_literal_matched_twice(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_surface_tree(repo)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8") + f'version = "{PREPARE_OLD}"\n', encoding="utf-8"
    )
    before = _snapshot(repo)

    errors = release_prepare.prepare(repo, PREPARE_NEW)

    assert any(
        "pyproject.toml" in error
        and "expected exactly one occurrence" in error
        and "found 2" in error
        for error in errors
    )
    assert _snapshot(repo) == before


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
    for skill_file in dist_audit.WHEEL_SKILL_FILES:
        entries[skill_file] = "x"
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


def test_dist_audit_rejects_a_wheel_missing_a_required_skill_file(tmp_path):
    entries = _conforming_wheel_entries()
    del entries["workflow_coordinator/agent_skill/wfc/references/setup.md"]
    wheel = tmp_path / WHEEL_NAME
    _write_wheel(wheel, entries)
    violations = dist_audit.audit(wheel)
    assert any("missing required skill file" in violation for violation in violations)


def test_dist_audit_rejects_a_fifth_file_under_agent_skill(tmp_path):
    entries = _conforming_wheel_entries()
    entries["workflow_coordinator/agent_skill/wfc/references/extra.md"] = "bad"
    wheel = tmp_path / WHEEL_NAME
    _write_wheel(wheel, entries)
    violations = dist_audit.audit(wheel)
    assert any(
        "agent_skill/wfc/references/extra.md" in violation and "not in the C2 allow-list" in violation
        for violation in violations
    )
