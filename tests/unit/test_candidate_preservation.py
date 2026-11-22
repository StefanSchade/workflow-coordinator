"""Real-Git acceptance fixtures for BP4b ``git_facts.preserve_candidate``.

Exercises each capture predicate and the private-index mechanism directly,
without the lifecycle controller, so every refusal and its left-byte-identical
invariant is pinned independently of BP4a's own classification machinery
(covered separately in ``test_preservation_lifecycle.py``).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from workflow_coordinator import git_facts as GF

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), check=True, text=True, capture_output=True
    ).stdout.strip()


def head(repository: Path) -> str:
    return git(repository, "rev-parse", "HEAD")


def refs_snapshot(repository: Path) -> set[str]:
    output = git(repository, "for-each-ref", "--format=%(refname) %(objectname)")
    return set(filter(None, output.splitlines()))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "config", "user.email", "consumer@example.invalid")
    git(repository, "config", "user.name", "Consumer test")
    (repository / "scope").mkdir()
    (repository / "scope" / "a.txt").write_text("v1\n", encoding="utf-8")
    (repository / "outside").mkdir()
    (repository / "outside" / "b.txt").write_text("outside\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "chore: seed disposable repository")
    return repository


def test_clean_worktree_is_not_needed(repo: Path):
    outcome = GF.preserve_candidate(
        repo, expected_repository=repo, base_head=head(repo), allowed_paths=("scope",), run_id="RUN-clean",
    )
    assert outcome.status == "not_needed"
    assert outcome.ref is None and outcome.commit is None and outcome.tree is None
    assert outcome.file_count == 0 and outcome.bytes_total == 0


def test_dirty_and_untracked_in_scope_is_preserved_and_repository_otherwise_untouched(repo: Path):
    base = head(repo)
    (repo / "scope" / "a.txt").write_text("v2\n", encoding="utf-8")
    (repo / "scope" / "new.txt").write_text("new\n", encoding="utf-8")
    before_status = git(repo, "status", "--porcelain=v1")
    before_refs = refs_snapshot(repo)

    outcome = GF.preserve_candidate(
        repo, expected_repository=repo, base_head=base, allowed_paths=("scope",), run_id="RUN-capture",
    )

    assert outcome.status == "preserved"
    assert outcome.ref == "refs/wfc/preserved/RUN-capture/1"
    assert outcome.quiescence == "unattested"
    assert outcome.file_count == 2
    assert git(repo, "show", f"{outcome.commit}:scope/a.txt") == "v2"
    assert git(repo, "show", f"{outcome.commit}:scope/new.txt") == "new"
    # Branch/HEAD/worktree/index and every pre-existing ref are byte-identical.
    assert head(repo) == base
    assert git(repo, "status", "--porcelain=v1") == before_status
    after_refs = refs_snapshot(repo)
    assert before_refs <= after_refs
    assert after_refs - before_refs == {f"{outcome.ref} {outcome.commit}"}


def test_in_scope_paths_with_space_and_non_ascii_byte_are_preserved_not_refused(repo: Path):
    # F001 regression: Git's default porcelain C-quotes any path with a
    # space, quote, backslash or non-ASCII byte; an in-scope path must not
    # be read as the literal quoted string and refused as out-of-scope.
    base = head(repo)
    (repo / "scope" / "wip notes.txt").write_text("untracked with space\n", encoding="utf-8")
    (repo / "scope" / "ümlaut.txt").write_text("untracked non-ascii\n", encoding="utf-8")
    (repo / "scope" / "a.txt").write_text("v2\n", encoding="utf-8")

    outcome = GF.preserve_candidate(
        repo, expected_repository=repo, base_head=base, allowed_paths=("scope",), run_id="RUN-quoted",
    )

    assert outcome.status == "preserved"
    assert outcome.file_count == 3
    assert git(repo, "show", f"{outcome.commit}:scope/wip notes.txt") == "untracked with space"
    assert git(repo, "show", f"{outcome.commit}:scope/ümlaut.txt") == "untracked non-ascii"


def test_in_scope_path_not_valid_utf8_is_preserved_not_an_unhandled_crash(repo: Path):
    # F002 regression: ``-z`` delivers the raw path bytes Git read from the
    # worktree, and a filename need not be valid UTF-8 (unlike the F001
    # cases above, which are all valid UTF-8). A strict text decode of that
    # status output must not raise UnicodeDecodeError out of
    # preserve_candidate; it must be read (via os.fsdecode/surrogateescape)
    # and captured like any other in-scope path.
    base = head(repo)
    raw_name = b"caf\xe9.txt"
    (repo / "scope" / os.fsdecode(raw_name)).write_bytes(b"non-utf8 filename content\n")

    outcome = GF.preserve_candidate(
        repo, expected_repository=repo, base_head=base, allowed_paths=("scope",), run_id="RUN-nonutf8",
    )

    assert outcome.status == "preserved"
    assert outcome.file_count == 1
    decoded_name = os.fsdecode(raw_name)
    assert git(repo, "show", f"{outcome.commit}:scope/{decoded_name}") == "non-utf8 filename content"


def test_unreadable_non_utf8_path_in_scope_is_refused_not_an_unhandled_crash(repo: Path):
    # F003 regression: Git reports an unindexable worktree path (for
    # example one it cannot open) in *stderr* of the ``git add`` that
    # ``_build_candidate_tree`` runs while building the candidate tree --
    # not only in the ``status`` output F002 already covers. A strict UTF-8
    # decode of that stderr must not raise UnicodeDecodeError out of
    # preserve_candidate for a non-UTF-8 name; it must be read tolerantly
    # and reported as a named PreservationRefused.
    base = head(repo)
    raw_name = b"caf\xe9-unreadable.txt"
    target = repo / "scope" / os.fsdecode(raw_name)
    target.write_bytes(b"non-utf8 filename content\n")
    target.chmod(0)
    before_status = git(repo, "status", "--porcelain=v1")
    before_refs = refs_snapshot(repo)
    try:
        with pytest.raises(GF.PreservationRefused):
            GF.preserve_candidate(
                repo, expected_repository=repo, base_head=base,
                allowed_paths=("scope",), run_id="RUN-unreadable",
            )
    finally:
        target.chmod(0o644)

    assert head(repo) == base
    assert git(repo, "status", "--porcelain=v1") == before_status
    assert refs_snapshot(repo) == before_refs


def test_repeat_capture_is_idempotent_no_second_ref(repo: Path):
    base = head(repo)
    (repo / "scope" / "a.txt").write_text("v2\n", encoding="utf-8")
    first = GF.preserve_candidate(
        repo, expected_repository=repo, base_head=base, allowed_paths=("scope",), run_id="RUN-repeat",
    )
    second = GF.preserve_candidate(
        repo, expected_repository=repo, base_head=base, allowed_paths=("scope",), run_id="RUN-repeat",
    )
    assert first.ref == second.ref == "refs/wfc/preserved/RUN-repeat/1"
    assert first.commit == second.commit
    refs = git(repo, "for-each-ref", "--format=%(refname)", "refs/wfc/preserved/RUN-repeat/")
    assert refs.splitlines() == ["refs/wfc/preserved/RUN-repeat/1"]


def test_isolated_index_copy_preserves_live_index_mtime_not_fresh_copy_time(repo: Path):
    """Regression for an intermittent false ``not_needed`` on a same-size,
    same-second worktree edit (``test_repeat_capture_is_idempotent_no_second_ref``
    flaked on exactly this). Git's own racy-clean protection -- the defence
    against a worktree edit landing within the index's own timestamp
    granularity being wrongly read as clean -- decides whether to trust a
    cached stat match by comparing a cache entry's mtime against the *index
    file's own* mtime. A plain byte copy stamps a fresh "now" mtime on the
    copy and so silently defeats that protection for any edit close enough to
    the commit that staged it. The throwaway copy ``preserve_candidate``
    reads ``git status`` through must therefore carry the live index's own
    mtime forward, not the time of the copy -- checked here as an exact
    attribute equality, not a timing-dependent race.
    """
    live_index = repo / ".git" / "index"
    live_mtime_ns = live_index.stat().st_mtime_ns

    copied_index = GF._isolated_index_copy(repo)
    try:
        assert copied_index.stat().st_mtime_ns == live_mtime_ns
    finally:
        copied_index.unlink()


def test_head_moved_since_classification_is_refused(repo: Path):
    base = head(repo)
    (repo / "scope" / "a.txt").write_text("v2\n", encoding="utf-8")
    git(repo, "add", "scope/a.txt")
    git(repo, "commit", "-q", "-m", "chore: advance HEAD past classification")
    with pytest.raises(GF.PreservationRefused, match="HEAD movement"):
        GF.preserve_candidate(
            repo, expected_repository=repo, base_head=base, allowed_paths=("scope",), run_id="RUN-head-moved",
        )


def test_dirty_path_outside_scope_is_refused_and_untouched(repo: Path):
    base = head(repo)
    (repo / "outside" / "b.txt").write_text("changed\n", encoding="utf-8")
    before_status = git(repo, "status", "--porcelain=v1")
    with pytest.raises(GF.PreservationRefused, match="outside authorized scope"):
        GF.preserve_candidate(
            repo, expected_repository=repo, base_head=base, allowed_paths=("scope",), run_id="RUN-outside",
        )
    assert git(repo, "status", "--porcelain=v1") == before_status
    assert head(repo) == base
    assert git(repo, "for-each-ref", "refs/wfc/preserved/RUN-outside/") == ""


def test_configured_clean_filter_is_refused(repo: Path):
    base = head(repo)
    (repo / "scope" / "a.txt").write_text("v2\n", encoding="utf-8")
    git(repo, "config", "filter.wfctest.clean", "cat")
    with pytest.raises(GF.PreservationRefused, match="clean/process filter"):
        GF.preserve_candidate(
            repo, expected_repository=repo, base_head=base, allowed_paths=("scope",), run_id="RUN-filter",
        )


def test_configured_clean_filter_with_non_utf8_subsection_name_is_refused(repo: Path):
    # F005 regression: the frozen ``_configured_external_filters`` asks
    # Git's own ``--get-regexp``, which (under a UTF-8 locale) does not
    # match a configured filter key whose subsection is not valid UTF-8,
    # even though Git still applies that filter when building a tree.
    # Candidate preservation must refuse this configuration too, not only
    # the plain-ASCII case ``test_configured_clean_filter_is_refused`` covers.
    base = head(repo)
    (repo / "scope" / "a.txt").write_text("v2\n", encoding="utf-8")
    raw_subsection = b"caf\xe9"
    with (repo / ".git" / "config").open("ab") as handle:
        handle.write(b'[filter "' + raw_subsection + b'"]\n\tclean = cat\n')
    with pytest.raises(GF.PreservationRefused, match="clean/process filter"):
        GF.preserve_candidate(
            repo, expected_repository=repo, base_head=base,
            allowed_paths=("scope",), run_id="RUN-filter-nonutf8",
        )


def test_unsafe_special_file_in_scope_is_refused(repo: Path):
    base = head(repo)
    (repo / "scope" / "a.txt").write_text("v2\n", encoding="utf-8")
    os.mkfifo(repo / "scope" / "fifo")
    before_status = git(repo, "status", "--porcelain=v1")
    with pytest.raises(GF.PreservationRefused, match="not a regular"):
        GF.preserve_candidate(
            repo, expected_repository=repo, base_head=base, allowed_paths=("scope",), run_id="RUN-fifo",
        )
    assert git(repo, "status", "--porcelain=v1") == before_status
    assert head(repo) == base


def test_escaping_symlink_in_scope_is_refused(repo: Path, tmp_path: Path):
    base = head(repo)
    (repo / "scope" / "a.txt").write_text("v2\n", encoding="utf-8")
    outside_target = tmp_path / "outside-target.txt"
    outside_target.write_text("elsewhere\n", encoding="utf-8")
    (repo / "scope" / "escape").symlink_to(outside_target)
    before_status = git(repo, "status", "--porcelain=v1")
    with pytest.raises(GF.PreservationRefused):
        GF.preserve_candidate(
            repo, expected_repository=repo, base_head=base, allowed_paths=("scope",), run_id="RUN-symlink",
        )
    assert git(repo, "status", "--porcelain=v1") == before_status
    assert head(repo) == base


def test_total_size_cap_exceeded_is_refused(repo: Path):
    base = head(repo)
    (repo / "scope" / "a.txt").write_text("v2\n", encoding="utf-8")
    with pytest.raises(GF.PreservationRefused, match="bytes exceeds limit"):
        GF.preserve_candidate(
            repo, expected_repository=repo, base_head=base, allowed_paths=("scope",), run_id="RUN-size",
            max_total_bytes=1,
        )


def test_file_count_cap_exceeded_is_refused(repo: Path):
    base = head(repo)
    (repo / "scope" / "a.txt").write_text("v2\n", encoding="utf-8")
    (repo / "scope" / "new.txt").write_text("new\n", encoding="utf-8")
    with pytest.raises(GF.PreservationRefused, match="file count exceeds limit"):
        GF.preserve_candidate(
            repo, expected_repository=repo, base_head=base, allowed_paths=("scope",), run_id="RUN-count",
            max_files=1,
        )


def test_unstable_worktree_across_two_tree_observations_is_refused(repo: Path, monkeypatch: pytest.MonkeyPatch):
    base = head(repo)
    (repo / "scope" / "a.txt").write_text("v2\n", encoding="utf-8")
    trees = iter(["tree-observation-one", "tree-observation-two"])
    monkeypatch.setattr(GF, "_build_candidate_tree", lambda *args, **kwargs: next(trees))
    with pytest.raises(GF.PreservationRefused, match="not stable"):
        GF.preserve_candidate(
            repo, expected_repository=repo, base_head=base, allowed_paths=("scope",), run_id="RUN-unstable",
        )


def test_staged_residue_is_captured_from_private_index_live_index_untouched(repo: Path):
    """Simulates a refused ``trusted_commit``/``bounded_path_commit`` rename-shaped
    leftover: the live index is stale (``v2``) while the worktree has moved on
    (``v3``). Capture must use the worktree content and never touch the stale
    staged state.
    """

    base = head(repo)
    (repo / "scope" / "a.txt").write_text("v2\n", encoding="utf-8")
    git(repo, "add", "scope/a.txt")
    (repo / "scope" / "a.txt").write_text("v3\n", encoding="utf-8")
    staged_before = git(repo, "diff", "--cached", "--name-only")
    staged_blob_before = git(repo, "show", ":scope/a.txt")

    outcome = GF.preserve_candidate(
        repo, expected_repository=repo, base_head=base, allowed_paths=("scope",), run_id="RUN-staged",
    )

    assert outcome.status == "preserved"
    assert git(repo, "show", f"{outcome.commit}:scope/a.txt") == "v3"
    assert git(repo, "diff", "--cached", "--name-only") == staged_before
    assert git(repo, "show", ":scope/a.txt") == staged_blob_before
