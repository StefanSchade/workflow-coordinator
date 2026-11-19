"""BP4a journal framing, canonical checkout identity and ownership-lock tests.

These exercise ``frontdoor.journal`` in isolation from the lifecycle driver:
append/replay framing (including torn-tail tolerance versus real corruption),
canonical checkout identity stability across a symlinked path, journal-root
disjointness refusal, and the ``flock``-based ownership lock, including real
process inheritance (``pass_fds``) and survival past the owning controller's
death -- exercised with an actual forked child, not a simulation.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from workflow_coordinator.frontdoor import journal as J


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
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: seed")
    return repo


# --------------------------------------------------------------------------
# Append/replay framing.
# --------------------------------------------------------------------------

def test_append_and_read_all_round_trip(tmp_path: Path):
    j = J.Journal(tmp_path / "journal.jsonl")
    j.append("alpha", {"x": 1})
    j.append("beta", {"y": [1, 2, 3]})
    records = j.read_all()
    assert [record.kind for record in records] == ["alpha", "beta"]
    assert records[0].data == {"x": 1}
    assert records[1].data == {"y": [1, 2, 3]}
    assert records[0].seq == 1 and records[1].seq == 2


def test_torn_final_record_is_silently_ignored(tmp_path: Path):
    path = tmp_path / "journal.jsonl"
    j = J.Journal(path)
    j.append("alpha", {"x": 1})
    j.append("beta", {"y": 2})
    whole = path.read_bytes()
    # Truncate inside the final record's payload, as a crash mid-``write()`` would.
    path.write_bytes(whole[:-6])
    records = J.Journal(path).read_all()
    assert [record.kind for record in records] == ["alpha"]


def test_torn_final_record_missing_trailing_newline_is_ignored(tmp_path: Path):
    path = tmp_path / "journal.jsonl"
    j = J.Journal(path)
    j.append("alpha", {"x": 1})
    whole = path.read_bytes()
    assert whole.endswith(b"\n")
    path.write_bytes(whole[:-1])
    records = J.Journal(path).read_all()
    assert records == ()


def test_digest_corruption_mid_journal_refuses_whole_journal(tmp_path: Path):
    path = tmp_path / "journal.jsonl"
    j = J.Journal(path)
    j.append("alpha", {"x": 1})
    j.append("beta", {"y": 2})
    raw = bytearray(path.read_bytes())
    # Flip one payload byte inside the *first* (non-tail) record; the second
    # record remains perfectly well-formed, so this cannot be explained by an
    # ordinary torn write and must be reported as corruption.
    first_space = raw.index(b" ")
    second_space = raw.index(b" ", first_space + 1)
    raw[second_space + 3] ^= 0xFF
    path.write_bytes(bytes(raw))
    with pytest.raises(J.JournalCorruptionError):
        J.Journal(path).read_all()


def test_digest_corruption_on_a_complete_final_record_refuses(tmp_path: Path):
    """A complete-length record with a bad digest cannot be a torn write."""

    path = tmp_path / "journal.jsonl"
    j = J.Journal(path)
    j.append("alpha", {"x": 1})
    raw = bytearray(path.read_bytes())
    first_space = raw.index(b" ")
    second_space = raw.index(b" ", first_space + 1)
    raw[second_space + 3] ^= 0xFF
    path.write_bytes(bytes(raw))
    with pytest.raises(J.JournalCorruptionError):
        J.Journal(path).read_all()


def test_latest_and_count(tmp_path: Path):
    j = J.Journal(tmp_path / "journal.jsonl")
    j.append("launch_intent", {})
    j.append("launch_intent", {})
    j.append("launch_failed_no_engine", {})
    assert j.count("launch_intent") == 2
    assert j.latest(("launch_failed_no_engine", "launch_spawned")).kind == "launch_failed_no_engine"
    assert j.latest(("classified",)) is None


def test_empty_or_missing_journal_reads_as_no_records(tmp_path: Path):
    assert J.Journal(tmp_path / "does-not-exist.jsonl").read_all() == ()


def test_each_appended_record_is_fsynced_durably_readable_by_a_new_reader(tmp_path: Path):
    """Each append is independently durable; a fresh ``Journal`` sees it all."""

    path = tmp_path / "journal.jsonl"
    J.Journal(path).append("one", {})
    J.Journal(path).append("two", {})
    assert [record.kind for record in J.Journal(path).read_all()] == ["one", "two"]


# --------------------------------------------------------------------------
# Canonical checkout identity and journal-root disjointness.
# --------------------------------------------------------------------------

def test_canonical_checkout_identity_stable_across_symlinked_path(repository: Path, tmp_path: Path):
    linked = tmp_path / "linked-checkout"
    linked.symlink_to(repository, target_is_directory=True)
    direct = J.canonical_checkout(repository)
    via_link = J.canonical_checkout(linked)
    assert direct.checkout_id == via_link.checkout_id
    assert direct.toplevel == via_link.toplevel


def test_canonical_checkout_identity_distinct_across_checkouts(tmp_path: Path):
    def make(name: str) -> Path:
        repo = tmp_path / name
        repo.mkdir()
        subprocess.run(("git", "init", "-q", str(repo)), check=True)
        git(repo, "config", "user.email", "consumer@example.invalid")
        git(repo, "config", "user.name", "Consumer test")
        (repo / "f.txt").write_text("x\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "chore: seed")
        return repo

    a, b = make("repo-a"), make("repo-b")
    assert J.canonical_checkout(a).checkout_id != J.canonical_checkout(b).checkout_id


def test_journal_root_inside_repository_is_refused(repository: Path):
    checkout = J.canonical_checkout(repository)
    with pytest.raises(J.JournalRootError):
        J.require_disjoint_journal_root(repository / "state", checkout)


def test_journal_root_containing_repository_is_refused(repository: Path, tmp_path: Path):
    checkout = J.canonical_checkout(repository)
    with pytest.raises(J.JournalRootError):
        J.require_disjoint_journal_root(tmp_path, checkout)


def test_journal_root_disjoint_from_repository_is_accepted(repository: Path, tmp_path: Path):
    checkout = J.canonical_checkout(repository)
    disjoint_root = tmp_path.parent / f"{tmp_path.name}-wfc-state-sibling"
    assert J._disjoint(disjoint_root.resolve(), checkout.toplevel)
    J.require_disjoint_journal_root(disjoint_root, checkout)


# --------------------------------------------------------------------------
# Ownership lock: acquire/release/query, and real process inheritance.
# --------------------------------------------------------------------------

def test_try_acquire_lock_is_exclusive_and_query_reflects_it(tmp_path: Path):
    checkout_dir = tmp_path / "checkout"
    handle = J.try_acquire_lock(checkout_dir)
    assert handle is not None
    assert J.try_acquire_lock(checkout_dir) is None
    assert J.query_lock_held(checkout_dir) is True
    J.release_lock(handle)
    assert J.query_lock_held(checkout_dir) is False


def test_query_lock_held_on_a_never_acquired_checkout_is_false(tmp_path: Path):
    assert J.query_lock_held(tmp_path / "checkout") is False


def test_lock_inheritance_survives_a_killed_controller_with_a_real_child_process(tmp_path: Path):
    """A real fork+exec'd child inherits the lock fd and keeps it held after
    the spawning ("controller") process is killed with SIGKILL."""

    checkout_dir = tmp_path / "checkout"
    handle = J.try_acquire_lock(checkout_dir)
    assert handle is not None

    sentinel = tmp_path / "child.ready"
    child_script = (
        "import pathlib,time,sys\n"
        f"pathlib.Path({str(sentinel)!r}).write_text('ready')\n"
        "time.sleep(60)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_script],
        pass_fds=(handle.fd,), close_fds=True,
    )
    try:
        deadline = time.time() + 10
        while not sentinel.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert sentinel.exists(), "child process never started"

        # The "controller" (this test process's own handle) closes its fd
        # without ever releasing the flock explicitly, simulating a crash:
        # only the still-running child keeps the lock held.
        os.close(handle.fd)

        assert J.query_lock_held(checkout_dir) is True, "lock must still be held by the surviving child"
    finally:
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=10)

    deadline = time.time() + 10
    while J.query_lock_held(checkout_dir) and time.time() < deadline:
        time.sleep(0.05)
    assert J.query_lock_held(checkout_dir) is False, "lock must be free once the child is gone"


def test_provider_style_grandchild_without_pass_fds_does_not_inherit_the_lock(tmp_path: Path):
    """The default ``close_fds=True`` (no ``pass_fds``) used for provider
    invocations must not leak the lock fd to that process."""

    checkout_dir = tmp_path / "checkout"
    handle = J.try_acquire_lock(checkout_dir)
    assert handle is not None
    try:
        probe = subprocess.run(
            [sys.executable, "-c", f"import os\ntry:\n    os.fstat({handle.fd})\nexcept OSError:\n    raise SystemExit(0)\nraise SystemExit(1)"],
        )
        assert probe.returncode == 0, "grandchild must not inherit the lock fd without pass_fds"
    finally:
        J.release_lock(handle)
