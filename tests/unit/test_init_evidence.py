"""BP6b: ``wfc init`` creates or adopts the Evidence repository.

Every invocation goes through ``workflow_coordinator.frontdoor.cli.main``,
the real installed CLI path, exactly like ``test_frontdoor_init_plan.py``.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
from pathlib import Path

import pytest

from workflow_coordinator.frontdoor.cli import main


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "consumer-repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", str(repo)), check=True)
    git(repo, "config", "user.email", "consumer@example.invalid")
    git(repo, "config", "user.name", "Consumer test")
    (repo / "src").mkdir()
    (repo / "src/app.txt").write_text("hello\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: seed disposable consumer repository")
    return repo


def _run(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


def _tree_snapshot(repository: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for path in repository.rglob("*"):
        if ".git" in path.relative_to(repository).parts:
            continue
        if path.is_file():
            snapshot[path.relative_to(repository).as_posix()] = path.read_bytes()
    return snapshot


def test_init_creates_a_new_evidence_repository_with_archive_gitignore_and_one_commit(
    repository: Path, tmp_path: Path,
):
    evidence_path = tmp_path / "evidence-repo"
    code, out, err = _run([
        "init", "--repository", str(repository), "--scope-ceiling", "src",
        "--evidence-repository", str(evidence_path),
    ])
    assert code == 0, err
    assert evidence_path.is_dir()
    assert (evidence_path / "archive").is_dir()
    assert (evidence_path / ".gitignore").is_file()
    assert "created" in out.lower()
    assert "NOTHING WAS PUSHED" in out

    # Exactly one commit, no remote, clean tree.
    log = git(evidence_path, "log", "--format=%H")
    assert len(log.splitlines()) == 1
    assert git(evidence_path, "remote") == ""
    assert git(evidence_path, "status", "--porcelain") == ""
    tracked = set(git(evidence_path, "ls-files").splitlines())
    assert tracked == {"archive/.gitkeep", ".gitignore"}


def test_init_reuses_an_existing_valid_evidence_repository_untouched(repository: Path, tmp_path: Path):
    evidence_path = tmp_path / "evidence-repo"
    code, _, err = _run([
        "init", "--repository", str(repository), "--scope-ceiling", "src",
        "--evidence-repository", str(evidence_path),
    ])
    assert code == 0, err

    head_before = git(evidence_path, "rev-parse", "HEAD")
    tree_before = _tree_snapshot(evidence_path)

    # Second payload repository reuses the same Evidence repository.
    repository2 = tmp_path / "consumer-repo-2"
    repository2.mkdir()
    subprocess.run(("git", "init", "-q", str(repository2)), check=True)
    git(repository2, "config", "user.email", "consumer@example.invalid")
    git(repository2, "config", "user.name", "Consumer test")
    (repository2 / "src").mkdir()
    (repository2 / "src/app.txt").write_text("hello\n", encoding="utf-8")
    git(repository2, "add", ".")
    git(repository2, "commit", "-q", "-m", "chore: seed disposable consumer repository")

    code, out, err = _run([
        "init", "--repository", str(repository2), "--scope-ceiling", "src",
        "--evidence-repository", str(evidence_path),
    ])
    assert code == 0, err
    assert "reusing" in out.lower()
    assert git(evidence_path, "rev-parse", "HEAD") == head_before
    assert _tree_snapshot(evidence_path) == tree_before


def test_init_refuses_a_foreign_non_empty_directory_before_writing_anything(repository: Path, tmp_path: Path):
    evidence_path = tmp_path / "foreign-dir"
    evidence_path.mkdir()
    (evidence_path / "SENTINEL.txt").write_text("not an evidence repository\n", encoding="utf-8")

    code, out, err = _run([
        "init", "--repository", str(repository), "--scope-ceiling", "src",
        "--evidence-repository", str(evidence_path),
    ])
    assert code != 0
    assert "refusing" in err
    # Nothing written to the foreign directory.
    assert list(evidence_path.iterdir()) == [evidence_path / "SENTINEL.txt"]
    assert not (evidence_path / "archive").exists()
    assert not (evidence_path / ".git").exists()


def test_init_leaves_the_payload_repository_modified_only_by_the_consumer_config(repository: Path, tmp_path: Path):
    evidence_path = tmp_path / "evidence-repo"
    head_before = git(repository, "rev-parse", "HEAD")

    code, out, err = _run([
        "init", "--repository", str(repository), "--scope-ceiling", "src",
        "--evidence-repository", str(evidence_path),
    ])
    assert code == 0, err
    assert git(repository, "rev-parse", "HEAD") == head_before
    status = git(repository, "status", "--porcelain", "--untracked-files=all")
    assert status.splitlines() == ["?? .wfc/config.yaml"]


def test_init_no_evidence_repository_flag_skips_evidence_creation(repository: Path, tmp_path: Path):
    evidence_path = tmp_path / "should-not-exist"
    code, out, err = _run([
        "init", "--repository", str(repository), "--scope-ceiling", "src",
        "--evidence-repository", str(evidence_path), "--no-evidence-repository",
    ])
    assert code == 0, err
    assert not evidence_path.exists()
    assert "Evidence repository" not in out


def test_init_writes_evidence_block_only_when_non_default(repository: Path, tmp_path: Path):
    evidence_path = tmp_path / "evidence-repo"
    code, out, err = _run([
        "init", "--repository", str(repository), "--scope-ceiling", "src",
        "--evidence-repository", str(evidence_path), "--evidence-raw", "commit",
    ])
    assert code == 0, err
    config_text = (repository / ".wfc/config.yaml").read_text(encoding="utf-8")
    assert "evidence:" in config_text
    assert "raw: commit" in config_text
    assert str(evidence_path) in config_text


def test_init_omits_evidence_block_when_only_default_raw_is_given_explicitly(repository: Path):
    code, out, err = _run([
        "init", "--repository", str(repository), "--scope-ceiling", "src", "--evidence-raw", "local",
        "--no-evidence-repository",
    ])
    assert code == 0, err
    config_text = (repository / ".wfc/config.yaml").read_text(encoding="utf-8")
    assert "evidence:" not in config_text
