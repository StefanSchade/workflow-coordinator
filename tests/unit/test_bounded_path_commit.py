"""Real-Git coverage for ``git_facts.bounded_path_commit`` (BP3).

Modeled on ``tests/unit/test_slice4b_git_authority.py``'s direct
``trusted_commit`` coverage, but exercising the narrower sibling: only an
exact, newly created leaf file may ever be committed.  No agent Hop, provider
or reducer is involved here -- this primitive is a Coordinator-owned
building block for callers outside a Hop (``wfc init``, the archive port).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from workflow_coordinator.git_facts import (
    GitPreflightError,
    NewPathCommitIntent,
    TrustedCommitError,
    bounded_path_commit,
)


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(("git", "init", str(repo)), check=True, capture_output=True)
    for key, value in (("user.email", "fixture@example.invalid"), ("user.name", "Fixture")):
        subprocess.run(("git", "-C", str(repo), "config", key, value), check=True)
    (repo / "src").mkdir()
    (repo / "src" / "existing.py").write_text("before\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "unrelated.adoc").write_text("before\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "test: baseline")
    return repo


def test_commits_an_exact_new_leaf_file_and_leaves_tree_clean(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "new.py").write_text("new\n", encoding="utf-8")
    committed = bounded_path_commit(
        repository, expected_repository=repository, base_head=base, allowed_paths=("src",),
        intent=NewPathCommitIntent(("src/new.py",), "feat(workflow): WI-1 H001 add new file"),
        work_item_id="WI-1", hop_id="H001",
    )
    assert committed.commit == git(repository, "rev-parse", "HEAD")
    assert committed.delta.changed_paths == ("src/new.py",)
    assert git(repository, "status", "--porcelain") == ""
    assert git(repository, "show", "-s", "--format=%s", committed.commit) == "feat(workflow): WI-1 H001 add new file"


def test_refuses_modification_of_an_existing_tracked_file(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "existing.py").write_text("after\n", encoding="utf-8")
    with pytest.raises(TrustedCommitError, match="already exists at base HEAD"):
        bounded_path_commit(
            repository, expected_repository=repository, base_head=base, allowed_paths=("src",),
            intent=NewPathCommitIntent(("src/existing.py",), "fix(workflow): WI-1 H001 change"),
            work_item_id="WI-1", hop_id="H001",
        )
    assert git(repository, "rev-parse", "HEAD") == base
    assert git(repository, "diff", "--cached", "--name-only") == ""
    assert (repository / "src" / "existing.py").read_text() == "after\n"


def test_refuses_deletion_of_an_existing_tracked_file(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "existing.py").unlink()
    with pytest.raises(TrustedCommitError, match="already exists at base HEAD"):
        bounded_path_commit(
            repository, expected_repository=repository, base_head=base, allowed_paths=("src",),
            intent=NewPathCommitIntent(("src/existing.py",), "fix(workflow): WI-1 H001 remove file"),
            work_item_id="WI-1", hop_id="H001",
        )
    assert git(repository, "rev-parse", "HEAD") == base
    assert git(repository, "diff", "--cached", "--name-only") == ""


def test_refuses_directory_shaped_intent_path_without_expanding_it(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "newdir").mkdir()
    (repository / "src" / "newdir" / "a.txt").write_text("a\n", encoding="utf-8")
    (repository / "src" / "newdir" / "b.txt").write_text("b\n", encoding="utf-8")
    with pytest.raises(TrustedCommitError, match="directory, not a leaf file"):
        bounded_path_commit(
            repository, expected_repository=repository, base_head=base, allowed_paths=("src",),
            intent=NewPathCommitIntent(("src/newdir",), "feat(workflow): WI-1 H001 add directory"),
            work_item_id="WI-1", hop_id="H001",
        )
    assert git(repository, "rev-parse", "HEAD") == base
    assert git(repository, "diff", "--cached", "--name-only") == ""
    assert git(repository, "status", "--porcelain") != ""


def test_refuses_symlinked_new_path(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "linked.py").symlink_to(repository / "src" / "existing.py")
    with pytest.raises(TrustedCommitError, match="not a regular file"):
        bounded_path_commit(
            repository, expected_repository=repository, base_head=base, allowed_paths=("src",),
            intent=NewPathCommitIntent(("src/linked.py",), "feat(workflow): WI-1 H001 add symlink"),
            work_item_id="WI-1", hop_id="H001",
        )
    assert git(repository, "rev-parse", "HEAD") == base
    assert git(repository, "diff", "--cached", "--name-only") == ""


def test_refuses_missing_worktree_mutation(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    with pytest.raises(TrustedCommitError, match="does not exist in the worktree"):
        bounded_path_commit(
            repository, expected_repository=repository, base_head=base, allowed_paths=("src",),
            intent=NewPathCommitIntent(("src/new.py",), "feat(workflow): WI-1 H001 add new file"),
            work_item_id="WI-1", hop_id="H001",
        )
    assert git(repository, "rev-parse", "HEAD") == base


@pytest.mark.parametrize("path", [".", "/tmp/escape", "../escape", "src/../new.py", "src//new.py"])
def test_refuses_escape_or_ambiguous_path_before_staging(repository: Path, path: str):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "new.py").write_text("new\n", encoding="utf-8")
    with pytest.raises(TrustedCommitError):
        bounded_path_commit(
            repository, expected_repository=repository, base_head=base, allowed_paths=("src",),
            intent=NewPathCommitIntent((path,), "feat(workflow): WI-1 H001 add new file"),
            work_item_id="WI-1", hop_id="H001",
        )
    assert git(repository, "rev-parse", "HEAD") == base
    assert git(repository, "diff", "--cached", "--name-only") == ""


def test_refuses_out_of_scope_path(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "new.py").write_text("new\n", encoding="utf-8")
    with pytest.raises(TrustedCommitError, match="outside authorized scope"):
        bounded_path_commit(
            repository, expected_repository=repository, base_head=base, allowed_paths=("docs",),
            intent=NewPathCommitIntent(("src/new.py",), "feat(workflow): WI-1 H001 add new file"),
            work_item_id="WI-1", hop_id="H001",
        )
    assert git(repository, "rev-parse", "HEAD") == base


def test_refuses_unrelated_staged_state(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "new.py").write_text("new\n", encoding="utf-8")
    (repository / "docs" / "unrelated.adoc").write_text("dirty staged\n", encoding="utf-8")
    git(repository, "add", "docs/unrelated.adoc")
    with pytest.raises(TrustedCommitError, match="pre-existing staged state"):
        bounded_path_commit(
            repository, expected_repository=repository, base_head=base, allowed_paths=("src", "docs"),
            intent=NewPathCommitIntent(("src/new.py",), "feat(workflow): WI-1 H001 add new file"),
            work_item_id="WI-1", hop_id="H001",
        )
    assert git(repository, "rev-parse", "HEAD") == base
    assert git(repository, "diff", "--cached", "--name-only") == "docs/unrelated.adoc"


def test_refuses_additional_unauthorized_dirty_path(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "new.py").write_text("new\n", encoding="utf-8")
    (repository / "loose.txt").write_text("keep untracked\n", encoding="utf-8")
    with pytest.raises(TrustedCommitError, match="unexpected dirty path"):
        bounded_path_commit(
            repository, expected_repository=repository, base_head=base, allowed_paths=("src", "."),
            intent=NewPathCommitIntent(("src/new.py",), "feat(workflow): WI-1 H001 add new file"),
            work_item_id="WI-1", hop_id="H001",
        )
    assert git(repository, "rev-parse", "HEAD") == base
    assert (repository / "loose.txt").exists()


def test_refuses_moved_head(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "new.py").write_text("new\n", encoding="utf-8")
    git(repository, "commit", "--allow-empty", "-m", "test: external movement")
    with pytest.raises(TrustedCommitError, match="unexpected HEAD movement"):
        bounded_path_commit(
            repository, expected_repository=repository, base_head=base, allowed_paths=("src",),
            intent=NewPathCommitIntent(("src/new.py",), "feat(workflow): WI-1 H001 add new file"),
            work_item_id="WI-1", hop_id="H001",
        )


def test_refuses_repository_identity_mismatch(repository: Path, tmp_path: Path):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "new.py").write_text("new\n", encoding="utf-8")
    with pytest.raises(TrustedCommitError, match="repository identity mismatch"):
        bounded_path_commit(
            repository, expected_repository=tmp_path / "other", base_head=base, allowed_paths=("src",),
            intent=NewPathCommitIntent(("src/new.py",), "feat(workflow): WI-1 H001 add new file"),
            work_item_id="WI-1", hop_id="H001",
        )


@pytest.mark.parametrize("subject", ["", "fix(workflow): missing identity", "not conventional: WI-1 H001 change"])
def test_refuses_invalid_subject_before_git_mutation(repository: Path, subject: str):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "new.py").write_text("new\n", encoding="utf-8")
    with pytest.raises(TrustedCommitError):
        bounded_path_commit(
            repository, expected_repository=repository, base_head=base, allowed_paths=("src",),
            intent=NewPathCommitIntent(("src/new.py",), subject),
            work_item_id="WI-1", hop_id="H001",
        )
    assert git(repository, "rev-parse", "HEAD") == base
    assert git(repository, "diff", "--cached", "--name-only") == ""


def test_refuses_duplicate_intent_paths(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "new.py").write_text("new\n", encoding="utf-8")
    with pytest.raises(TrustedCommitError, match="duplicate"):
        bounded_path_commit(
            repository, expected_repository=repository, base_head=base, allowed_paths=("src",),
            intent=NewPathCommitIntent(("src/new.py", "src/new.py"), "feat(workflow): WI-1 H001 add new file"),
            work_item_id="WI-1", hop_id="H001",
        )
    assert git(repository, "diff", "--cached", "--name-only") == ""


def _write_hook(repository: Path, directory: str, side_effect: str) -> str:
    hook = repository / directory / "pre-commit"
    hook.parent.mkdir()
    hook.write_text(f"#!/bin/sh\nprintf hook > {side_effect}\n", encoding="utf-8")
    hook.chmod(0o755)
    return hook.relative_to(repository).as_posix()


def test_neutralizes_agent_authored_worktree_hook(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    git(repository, "config", "core.hooksPath", ".githooks")
    hook_path = _write_hook(repository, ".githooks", "hook-executed")
    (repository / "src" / "new.py").write_text("new\n", encoding="utf-8")

    committed = bounded_path_commit(
        repository, expected_repository=repository, base_head=base, allowed_paths=("src", ".githooks"),
        intent=NewPathCommitIntent(("src/new.py", hook_path), "feat(workflow): WI-1 H001 neutralize hook"),
        work_item_id="WI-1", hop_id="H001",
    )
    assert committed.commit == git(repository, "rev-parse", "HEAD")
    assert not (repository / "hook-executed").exists()


def test_strips_inherited_git_config_hook_injection(repository: Path, monkeypatch):
    base = git(repository, "rev-parse", "HEAD")
    hook_path = _write_hook(repository, ".injected-hooks", "injected-hook-executed")
    (repository / "src" / "new.py").write_text("new\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", ".injected-hooks")

    committed = bounded_path_commit(
        repository, expected_repository=repository, base_head=base, allowed_paths=("src", ".injected-hooks"),
        intent=NewPathCommitIntent(("src/new.py", hook_path), "feat(workflow): WI-1 H001 ignore config injection"),
        work_item_id="WI-1", hop_id="H001",
    )
    assert committed.commit == git(repository, "rev-parse", "HEAD")
    assert not (repository / "injected-hook-executed").exists()


def test_refuses_configured_clean_filter_before_staging(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    git(repository, "config", "filter.agent.clean", "touch filter-executed")
    (repository / ".gitattributes").write_text("src/new.py filter=agent\n", encoding="utf-8")
    (repository / "src" / "new.py").write_text("new\n", encoding="utf-8")

    with pytest.raises(TrustedCommitError, match="configured clean/process filter"):
        bounded_path_commit(
            repository, expected_repository=repository, base_head=base, allowed_paths=("src", "."),
            intent=NewPathCommitIntent(("src/new.py", ".gitattributes"), "feat(workflow): WI-1 H001 reject filter"),
            work_item_id="WI-1", hop_id="H001",
        )
    assert git(repository, "rev-parse", "HEAD") == base
    assert not (repository / "filter-executed").exists()


def test_deterministic_staging_failure_reports_truthfully_without_reset(repository: Path, monkeypatch):
    import workflow_coordinator.git_facts as git_facts

    base = git(repository, "rev-parse", "HEAD")
    original = git_facts._trusted_git

    def fail_add(repo: Path, *args: str, **kwargs: object) -> str:
        if args[:1] == ("add",):
            raise GitPreflightError("injected stage failure")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(git_facts, "_trusted_git", fail_add)
    (repository / "src" / "new.py").write_text("new\n", encoding="utf-8")
    with pytest.raises(TrustedCommitError, match="staging failed: injected stage failure"):
        bounded_path_commit(
            repository, expected_repository=repository, base_head=base, allowed_paths=("src",),
            intent=NewPathCommitIntent(("src/new.py",), "feat(workflow): WI-1 H001 add new file"),
            work_item_id="WI-1", hop_id="H001",
        )
    assert git(repository, "rev-parse", "HEAD") == base
    assert (repository / "src" / "new.py").read_text() == "new\n"
