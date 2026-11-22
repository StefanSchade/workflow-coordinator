"""Regression for the index-copy mtime defect at the three sibling sites to
``_isolated_index_copy`` (package E0b; E0's own site and test are in
``test_candidate_preservation.py``).

Each throwaway index copy must carry the live index's own mtime forward, not
a fresh copy-time stamp, or Git's racy-clean defence against a same-size,
same-second worktree edit silently switches off through the copy. Every
assertion here is an exact attribute equality or a forced, non-racing
mtime offset -- never a wall-clock race.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
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


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "config", "user.email", "consumer@example.invalid")
    git(repository, "config", "user.name", "Consumer test")
    (repository / "a.txt").write_text("v1\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "chore: seed disposable repository")
    return repository


def _live_index_mtime_ns(repository: Path) -> int:
    return (repository / ".git" / "index").stat().st_mtime_ns


def _dirty_same_size(repository: Path) -> None:
    (repository / "a.txt").write_text("v2\n", encoding="utf-8")


def _capture_copy_mtimes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Record (src_mtime_ns, dst_mtime_ns) for every real copyfile/copy2 call.

    Wraps the real implementations rather than replacing them, so production
    behaviour (whichever of the two a call site uses) is unchanged; only the
    resulting metadata is observed. ``shutil.copy2`` calls the module-level
    ``copyfile`` internally before applying ``copystat``, so wrapping both
    names records one entry for that inner data copy (before the mtime is
    corrected) and one for the outer call (after); the last entry is always
    the state actually left on disk for the caller to use.
    """

    records: list[tuple[int, int]] = []
    real_copyfile = shutil.copyfile
    real_copy2 = shutil.copy2

    def copyfile(src: str, dst: str, *args, **kwargs):
        result = real_copyfile(src, dst, *args, **kwargs)
        records.append((os.stat(src).st_mtime_ns, os.stat(dst).st_mtime_ns))
        return result

    def copy2(src: str, dst: str, *args, **kwargs):
        result = real_copy2(src, dst, *args, **kwargs)
        records.append((os.stat(src).st_mtime_ns, os.stat(dst).st_mtime_ns))
        return result

    monkeypatch.setattr(GF.shutil, "copyfile", copyfile)
    monkeypatch.setattr(GF.shutil, "copy2", copy2)
    return records


def test_salvage_inventory_index_copy_preserves_live_index_mtime(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    records = _capture_copy_mtimes(monkeypatch)
    GF.salvage_inventory(repo, base_head=head(repo), allowed_paths=("a.txt",))
    assert records, "expected salvage_inventory to copy the live index"
    src_mtime_ns, dst_mtime_ns = records[-1]
    assert dst_mtime_ns == src_mtime_ns


def test_salvage_omissions_index_copy_preserves_live_index_mtime(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    records = _capture_copy_mtimes(monkeypatch)
    GF.salvage_omissions(repo, allowed_paths=("a.txt",))
    assert records, "expected salvage_omissions to copy the live index"
    src_mtime_ns, dst_mtime_ns = records[-1]
    assert dst_mtime_ns == src_mtime_ns


def test_observe_repository_index_copy_preserves_live_index_mtime(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    records = _capture_copy_mtimes(monkeypatch)
    GF.observe_repository(repo)
    assert records, "expected observe_repository to copy the live index"
    src_mtime_ns, dst_mtime_ns = records[-1]
    assert dst_mtime_ns == src_mtime_ns


def test_salvage_inventory_reads_dirty_same_size_edit_through_the_copy(repo: Path):
    base = head(repo)
    _dirty_same_size(repo)
    captured = GF.salvage_inventory(repo, base_head=base, allowed_paths=("a.txt",))
    assert len(captured) == 1
    assert captured[0].object_id == "tracked-diff"
    assert b"-v1" in captured[0].content and b"+v2" in captured[0].content


def test_observe_repository_reads_dirty_same_size_edit_through_the_copy(repo: Path):
    _dirty_same_size(repo)
    facts = GF.observe_repository(repo)
    assert not facts.clean
    assert facts.tracked_changes == ("M a.txt",)


def test_preflight_hop_refuses_dirty_same_size_edit_through_the_copy(repo: Path):
    base = head(repo)
    _dirty_same_size(repo)
    with pytest.raises(GF.GitPreflightError):
        GF.preflight_hop(repo, expected_repository=GF.repository_identity(repo), expected_head=base)


def test_observe_repository_reads_dirty_edit_as_clean_if_copy_mtime_is_forced_a_whole_second_later(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """Deterministic demonstration of the mechanism this fix removes.

    Forcing the offset explicitly (rather than waiting on real elapsed
    time) makes the consequence of ever losing the mtime-preserving
    invariant reproducible on demand: if the copy's mtime ever again lands
    a whole second after the live index's own mtime, this is what breaks --
    a gate whose job is to refuse a dirty tree instead passes it. This
    patches ``shutil.copy2``, the call the fixed ``observe_repository`` /
    ``preflight_hop`` path actually makes through the shared helper; it is
    a guard against ever losing the invariant again, not itself a
    differentiator between the pre-fix and fixed source (pre-fix, this
    site called ``shutil.copyfile`` directly, which this patch does not
    touch).
    """

    real_copy2 = shutil.copy2

    def copy2_with_mtime_forced_later(src: str, dst: str, *args, **kwargs):
        result = real_copy2(src, dst, *args, **kwargs)
        # A large, fixed margin (not "+1s" relative to the copy's own
        # mtime) keeps this forced offset clear of the entry/index mtime
        # relationship under any realistic scheduling jitter.
        future = time.time() + 10
        os.utime(dst, (future, future))
        return result

    monkeypatch.setattr(GF.shutil, "copy2", copy2_with_mtime_forced_later)
    base = head(repo)
    _dirty_same_size(repo)

    facts = GF.observe_repository(repo)
    assert facts.clean

    # The gate whose job is to refuse a dirty tree instead passes it: no
    # GitPreflightError is raised for a worktree that is actually dirty.
    passed = GF.preflight_hop(repo, expected_repository=GF.repository_identity(repo), expected_head=base)
    assert passed.clean
