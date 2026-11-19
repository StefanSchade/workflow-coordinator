"""New, non-ported coverage for the BP5 completion safety fixes.

Two explicit BP5 acceptance requirements reproduced as violations against the
pre-fix port, and closed here without redesign:

* *Destination-boundary containment.* A tracked symlink at ``archive/`` (or
  at the ``.wfc-archive-staging/`` root) pointing outside the Evidence
  repository must never let a write land outside the repository, even though
  the later Coordinator-owned commit (``bounded_path_commit``) would
  separately refuse to commit it. ``SymlinkEscapeContainmentTests`` exercises
  real filesystem effects in a disposable Git Evidence repository and asserts
  the outside tree is untouched and no commit results -- not merely that an
  error is eventually raised.
* *Interrupted copy-to-commit retry safety.* A destination that is placed
  but not yet committed (the window ``StagingAndRetryTests`` in
  ``test_archive_port.py`` proves is resumable) must not resume blindly: the
  attempt id, the recorded sources and every copied file's own digest/size
  must still match the manifest, and the manifest's own ``copied_files``
  paths must themselves be safe (no absolute, traversal, duplicate or
  symlink-escape path), all checked before any file is read outside the
  destination or any commit is attempted. ``InterruptedRetryTamperTests``
  constructs a placed-but-uncommitted destination directly (the same state an
  interruption between placement and commit leaves) and tampers with it the
  same way a resumed, interrupted process could be tampered with.
  ``test_archive_retry_integrity.py`` extends this: a file tampered together
  with a matching altered digest in its own manifest, or a falsified fact,
  source or semantic field, cannot pass self-consistency alone -- the
  manifest itself must also match one freshly derived from the current
  authoritative Run/OPS sources.

Both areas are otherwise covered by existing ported/equivalence tests
(``test_archive_port.py``, ``test_archive_conformance.py``,
``test_archive_equivalence.py``); this module adds only the new properties,
it does not re-prove the ported ones.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from workflow_coordinator.frontdoor import archive as ar

REAL_GIT = shutil.which("git")


def git(repository: Path, *args: str) -> str:
    proc = subprocess.run([REAL_GIT, "-C", str(repository), *args],
                          capture_output=True, text=True, check=True)
    return proc.stdout


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _init_evidence_repo(repo: Path) -> None:
    (repo / "archive").mkdir(parents=True)
    (repo / "archive" / ".gitkeep").write_text("")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Safety Fixture")
    git(repo, "config", "user.email", "safety@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture baseline")


def _build_identity(base: Path, attempt: str) -> tuple[Path, Path]:
    """A minimal state root that satisfies ``run_identity()``: just enough
    operator-summary.json for a run id, nothing else required by this
    module's checks."""

    home = base / "home"
    state = home / "wfc-state" / attempt
    write_json(state / "operator-summary.json", {
        "schema_version": 1, "run_id": f"RUN-SAFETY-{attempt}",
        "work_item_id": "WI-SAFETY-001", "run_status": "completed",
    })
    # archive_run() resolves state_root before recording it as the
    # manifest's own source; match that so a fixture-constructed manifest's
    # "sources" agrees with what a real placement would have recorded.
    return home, state.resolve()


def _listing(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


class SafetyTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "wfc safety sandbox"
        self.base.mkdir(parents=True)


class SymlinkEscapeContainmentTests(SafetyTestCase):
    """BP5's destination containment requirement, including symlink escape
    refusal, reproduced and closed as an isolated real-filesystem-effects
    test: a tracked ``archive/`` (or staging-root) symlink pointing outside
    the Evidence repository must never place data outside it."""

    def test_symlinked_archive_directory_is_refused_before_any_outside_write(self):
        outside = self.base / "outside-tree"
        outside.mkdir()
        (outside / "sentinel.txt").write_text("do not touch\n")

        repo = self.base / "evidence"
        repo.mkdir()
        (repo / "archive").symlink_to(outside, target_is_directory=True)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.name", "Safety Fixture")
        git(repo, "config", "user.email", "safety@example.invalid")
        git(repo, "config", "commit.gpgsign", "false")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "fixture: archive/ is a symlink out of the repository")

        home, state = _build_identity(self.base, "wfc-safety-symlink-archive-01")
        before_outside = _snapshot(outside)
        before_listing = _listing(outside)
        before_head = _head(repo)

        with self.assertRaises(ar.ArchiveError) as cm:
            ar.archive_run(state_root=state, evidence_repo=repo, home=home)
        self.assertIn("symlink escaping the evidence repository root", str(cm.exception))

        self.assertEqual(_snapshot(outside), before_outside, "no file content outside the repo must change")
        self.assertEqual(_listing(outside), before_listing, "no new file or directory outside the repo must appear")
        self.assertEqual(_head(repo), before_head)
        self.assertEqual(git(repo, "status", "--porcelain"), "")
        self.assertFalse((repo / ".wfc-archive-staging").exists())

    def test_symlinked_staging_root_is_refused_before_any_outside_write(self):
        outside = self.base / "outside-staging-tree"
        outside.mkdir()
        (outside / "sentinel.txt").write_text("do not touch\n")

        repo = self.base / "evidence"
        _init_evidence_repo(repo)
        (repo / ".wfc-archive-staging").symlink_to(outside, target_is_directory=True)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "fixture: staging root is a symlink out of the repository")

        home, state = _build_identity(self.base, "wfc-safety-symlink-staging-01")
        before_outside = _snapshot(outside)
        before_listing = _listing(outside)
        before_head = _head(repo)

        with self.assertRaises(ar.ArchiveError) as cm:
            ar.archive_run(state_root=state, evidence_repo=repo, home=home)
        self.assertIn("symlink escaping the evidence repository root", str(cm.exception))

        self.assertEqual(_snapshot(outside), before_outside)
        self.assertEqual(_listing(outside), before_listing)
        self.assertEqual(_head(repo), before_head)
        self.assertEqual(git(repo, "status", "--porcelain"), "")
        self.assertFalse((repo / "archive" / "unclassified").exists())


class InterruptedRetryTamperTests(SafetyTestCase):
    """A placed-but-uncommitted destination -- exactly what a kill between
    placement and commit leaves, per ``StagingAndRetryTests`` in
    ``test_archive_port.py`` -- must be reverified before it is trusted for
    commit, not resumed blindly."""

    def _repo_and_identity(self, attempt: str) -> tuple[Path, Path, Path]:
        repo = self.base / "evidence"
        _init_evidence_repo(repo)
        home, state = _build_identity(self.base, attempt)
        return repo, home, state

    def _place(self, dest: Path, *, attempt: str, state: Path, copied_files: list[dict],
               attempt_override: str | None = None, sources_override: dict | None = None) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        manifest = {
            "attempt_id": attempt if attempt_override is None else attempt_override,
            "archived_at": "2026-11-19T00:00:00+00:00",
            "sources": {"run_dir": str(state), "ops_dir": None} if sources_override is None else sources_override,
            "copied_files": copied_files,
            "facts": {"run_id": f"RUN-SAFETY-{attempt}"},
            "semantic": None,
        }
        write_json(dest / "manifest.json", manifest)

    def _place_file(self, dest: Path, rel: str, content: bytes) -> dict:
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {"path": rel, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}

    def test_tampered_copied_file_content_is_refused_and_placed_data_preserved(self):
        attempt = "wfc-safety-tamper-content-01"
        repo, home, state = self._repo_and_identity(attempt)
        dest = repo / "archive" / "unclassified" / attempt
        entry = self._place_file(dest, "run/sample.json", b'{"a":1}\n')
        self._place(dest, attempt=attempt, state=state, copied_files=[entry])

        tampered = b'{"a":9}\n'  # same byte length as the original, different content/digest
        self.assertEqual(len(tampered), len(b'{"a":1}\n'))
        (dest / "run" / "sample.json").write_bytes(tampered)
        before_head = _head(repo)

        with self.assertRaises(ar.ArchiveError) as cm:
            ar.archive_run(state_root=state, evidence_repo=repo, home=home)
        self.assertIn("does not match its recorded digest", str(cm.exception))

        self.assertEqual(_head(repo), before_head)
        self.assertEqual(git(repo, "diff", "--cached", "--name-only").strip(), "")
        self.assertEqual((dest / "run" / "sample.json").read_bytes(), tampered,
                          "a refusal must preserve the placed data, not revert or delete it")

    def test_tampered_copied_file_size_is_refused(self):
        attempt = "wfc-safety-tamper-size-01"
        repo, home, state = self._repo_and_identity(attempt)
        dest = repo / "archive" / "unclassified" / attempt
        entry = self._place_file(dest, "run/sample.json", b'{"a":1}\n')
        self._place(dest, attempt=attempt, state=state, copied_files=[entry])

        (dest / "run" / "sample.json").write_bytes(b'{"a":1}\nextra-bytes-appended\n')
        before_head = _head(repo)

        with self.assertRaises(ar.ArchiveError) as cm:
            ar.archive_run(state_root=state, evidence_repo=repo, home=home)
        self.assertIn("does not match its recorded size", str(cm.exception))
        self.assertEqual(_head(repo), before_head)

    def test_tampered_manifest_attempt_id_is_refused_not_committed(self):
        attempt = "wfc-safety-tamper-attempt-id-01"
        repo, home, state = self._repo_and_identity(attempt)
        dest = repo / "archive" / "unclassified" / attempt
        entry = self._place_file(dest, "run/sample.json", b'{"a":1}\n')
        self._place(dest, attempt=attempt, state=state, copied_files=[entry],
                    attempt_override="RUN-GENUINELY-DIFFERENT-ATTEMPT")
        before_head = _head(repo)

        with self.assertRaises(ar.ArchiveError) as cm:
            ar.archive_run(state_root=state, evidence_repo=repo, home=home)
        self.assertIn("staged for attempt", str(cm.exception))
        self.assertEqual(_head(repo), before_head)
        self.assertEqual(git(repo, "diff", "--cached", "--name-only").strip(), "")

    def test_tampered_manifest_source_is_refused_not_committed(self):
        attempt = "wfc-safety-tamper-source-01"
        repo, home, state = self._repo_and_identity(attempt)
        dest = repo / "archive" / "unclassified" / attempt
        entry = self._place_file(dest, "run/sample.json", b'{"a":1}\n')
        self._place(dest, attempt=attempt, state=state, copied_files=[entry],
                    sources_override={"run_dir": "/totally/different/run/dir", "ops_dir": None})
        before_head = _head(repo)

        with self.assertRaises(ar.ArchiveError) as cm:
            ar.archive_run(state_root=state, evidence_repo=repo, home=home)
        self.assertIn("different sources", str(cm.exception))
        self.assertEqual(_head(repo), before_head)

    def test_absolute_manifest_path_is_refused_without_reading_or_committing(self):
        attempt = "wfc-safety-unsafe-absolute-01"
        repo, home, state = self._repo_and_identity(attempt)
        dest = repo / "archive" / "unclassified" / attempt
        outside_secret = self.base / "outside-secret.txt"
        outside_secret.write_text("never read\n")
        entry = {"path": str(outside_secret), "sha256": "0" * 64, "bytes": 0}
        self._place(dest, attempt=attempt, state=state, copied_files=[entry])
        before_head = _head(repo)

        with self.assertRaises(ar.ArchiveError) as cm:
            ar.archive_run(state_root=state, evidence_repo=repo, home=home)
        self.assertIn("unsafe file path", str(cm.exception))
        self.assertEqual(outside_secret.read_text(), "never read\n")
        self.assertEqual(_head(repo), before_head)

    def test_traversal_manifest_path_is_refused_without_reading_or_committing(self):
        attempt = "wfc-safety-unsafe-traversal-01"
        repo, home, state = self._repo_and_identity(attempt)
        dest = repo / "archive" / "unclassified" / attempt
        outside_secret = self.base / "outside-secret.txt"
        outside_secret.write_text("never read\n")
        entry = {"path": "../../outside-secret.txt", "sha256": "0" * 64, "bytes": 0}
        self._place(dest, attempt=attempt, state=state, copied_files=[entry])
        before_head = _head(repo)

        with self.assertRaises(ar.ArchiveError) as cm:
            ar.archive_run(state_root=state, evidence_repo=repo, home=home)
        self.assertIn("unsafe file path", str(cm.exception))
        self.assertEqual(outside_secret.read_text(), "never read\n")
        self.assertEqual(_head(repo), before_head)

    def test_duplicate_manifest_path_is_refused(self):
        attempt = "wfc-safety-unsafe-duplicate-01"
        repo, home, state = self._repo_and_identity(attempt)
        dest = repo / "archive" / "unclassified" / attempt
        entry = self._place_file(dest, "run/sample.json", b'{"a":1}\n')
        self._place(dest, attempt=attempt, state=state, copied_files=[entry, dict(entry)])
        before_head = _head(repo)

        with self.assertRaises(ar.ArchiveError) as cm:
            ar.archive_run(state_root=state, evidence_repo=repo, home=home)
        self.assertIn("duplicate file path", str(cm.exception))
        self.assertEqual(_head(repo), before_head)

    def test_symlink_escape_manifest_path_is_refused_without_reading_or_committing(self):
        attempt = "wfc-safety-unsafe-symlink-01"
        repo, home, state = self._repo_and_identity(attempt)
        dest = repo / "archive" / "unclassified" / attempt
        dest.mkdir(parents=True)

        outside = self.base / "outside-manifest-escape"
        outside.mkdir()
        (outside / "secret.txt").write_text("never read\n")
        (dest / "escape").symlink_to(outside, target_is_directory=True)

        entry = {"path": "escape/secret.txt", "sha256": "0" * 64, "bytes": 0}
        self._place(dest, attempt=attempt, state=state, copied_files=[entry])
        before_head = _head(repo)
        before_outside = _snapshot(outside)

        with self.assertRaises(ar.ArchiveError) as cm:
            ar.archive_run(state_root=state, evidence_repo=repo, home=home)
        self.assertIn("escapes the archive destination via a symlink", str(cm.exception))
        self.assertEqual(_snapshot(outside), before_outside)
        self.assertEqual(_head(repo), before_head)

    def test_genuinely_intact_interruption_resumes_to_exactly_one_commit(self):
        """Positive control: the new reverification does not block a
        genuinely untampered resume, which still ends in exactly one
        commit. (A repeated already-committed archive being a no-op is
        covered by ``test_archive_port.py``'s
        ``test_second_archive_of_the_same_attempt_is_a_no_op`` against a
        manifest produced by a real ``archive_run()`` call.)

        The placed manifest here is built the same way a real placement
        would build it -- via ``ar._manifest_candidate`` against this
        fixture's own authoritative state root -- and then moved straight
        into the destination without committing, exactly the state a kill
        between the atomic rename and the commit leaves. A hand-rolled
        manifest with invented ``facts`` would no longer be "genuinely
        intact": it would disagree with a manifest freshly derived from the
        authoritative sources, which is precisely the new check this test
        must not trip for an untampered resume."""

        attempt = "wfc-safety-intact-resume-01"
        repo, home, state = self._repo_and_identity(attempt)
        dest = repo / "archive" / "unclassified" / attempt
        staging = repo / ".wfc-archive-staging" / attempt
        manifest, _ = ar._manifest_candidate(
            attempt_id=attempt, state_root=state, ops_dir=None, semantic_payload=None, staging=staging,
        )
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(dest)
        before_head = _head(repo)

        report = ar.archive_run(state_root=state, evidence_repo=repo, home=home)
        self.assertEqual(report.outcome, "archived")
        self.assertEqual(git(repo, "rev-list", "--count", f"{before_head}..HEAD").strip(), "1")


if __name__ == "__main__":
    unittest.main()
