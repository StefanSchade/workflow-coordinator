"""BP5 retry-integrity correction (WI-WFC-BETA1-BP5-RETRY-INTEGRITY-001).

``test_archive_safety.py``'s ``InterruptedRetryTamperTests`` already proves
that a placed-but-uncommitted archive is reverified against *its own*
manifest before a retry resumes it: the attempt id, the recorded sources and
every copied file's own digest/size must match. That self-check alone has a
gap -- it only proves internal self-consistency between the placed file and
its own manifest entry. An attacker (or a corrupting process) who alters a
placed file *and* updates its own manifest's recorded digest/size to match,
or who simply falsifies a manifest fact, source or semantic field without
touching any file, passes every one of those checks.

This module proves the closed gap: ``_verify_resumable_placement`` now also
re-derives a manifest from the current authoritative Run/OPS sources (the
same ``_manifest_candidate`` helper the already-committed no-op path uses)
and refuses to commit unless the placed manifest matches it exactly, modulo
only ``archived_at``. Per-file ``source`` (provenance), not just
path/hash/size, is part of that comparison.

Every test here places an archive the same way a real interrupted run would
-- via ``ar._manifest_candidate`` against the fixture's own authoritative
state root, then moved into the destination without committing -- so a
"genuinely intact" fixture is actually derived from authoritative sources,
not hand-rolled.
"""

from __future__ import annotations

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
    git(repo, "config", "user.name", "Retry Integrity Fixture")
    git(repo, "config", "user.email", "retry-integrity@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture baseline")


def _head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def _snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class RetryIntegrityTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "wfc retry integrity sandbox"
        self.base.mkdir(parents=True)
        self.repo = self.base / "evidence"
        _init_evidence_repo(self.repo)

    def _build(self, attempt: str, *, with_ops: bool = False) -> tuple[Path, Path, Path | None]:
        """A minimal authoritative state root (and, optionally, OPS
        directory) for ``attempt``. Returns ``(home, state_root, ops_dir)``;
        ``ops_dir`` is unresolved (as ``archive_run`` itself resolves it)."""

        home = self.base / "home"
        state = home / "wfc-state" / attempt
        write_json(state / "operator-summary.json", {
            "schema_version": 1, "run_id": f"RUN-RETRY-{attempt}",
            "work_item_id": "WI-RETRY-INTEGRITY-001", "run_status": "completed",
        })
        ops_dir = None
        if with_ops:
            ops_dir = home / "wfc-ops" / attempt
            ops_dir.mkdir(parents=True)
            (ops_dir / "run.log").write_text("fixture ops run log\n")
            (ops_dir / "preparation.log").write_text("fixture preparation log\n")
        return home, state.resolve(), ops_dir

    def _place_genuine(
        self, *, attempt: str, state: Path, ops_dir: Path | None, semantic_payload: dict | None = None,
    ) -> tuple[Path, dict]:
        """Place an archive exactly the way a real placement would -- via
        ``_manifest_candidate`` against the authoritative sources -- then
        move it into the destination without committing: the state a kill
        between the atomic rename and the commit leaves."""

        domain = semantic_payload.get("domain") if semantic_payload else None
        workstream = semantic_payload.get("workstream") if semantic_payload else None
        dest = ar.destination_for(self.repo, domain, workstream, attempt)
        staging = self.repo / ".wfc-archive-staging" / attempt
        ops_resolved = ops_dir.resolve() if ops_dir else None
        manifest, _ = ar._manifest_candidate(
            attempt_id=attempt, state_root=state, ops_dir=ops_resolved,
            semantic_payload=semantic_payload, staging=staging,
        )
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n")
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(dest)
        return dest, manifest

    def _retry(self, *, state: Path, home: Path) -> ar.ArchiveReport:
        return ar.archive_run(state_root=state, evidence_repo=self.repo, home=home)


class SelfConsistentTamperIsRefusedTests(RetryIntegrityTestCase):
    """A file changed together with its own manifest's digest/size, so the
    self-check (file vs. its own manifest entry) alone would pass, must
    still be refused once compared against a freshly derived manifest."""

    def test_altered_file_with_matching_altered_self_digest_is_refused(self):
        attempt = "wfc-retry-self-consistent-tamper-01"
        home, state, ops_dir = self._build(attempt)
        dest, manifest = self._place_genuine(attempt=attempt, state=state, ops_dir=ops_dir)
        before_head = _head(self.repo)
        before_snapshot = _snapshot(dest)

        rel = manifest["copied_files"][0]["path"]
        target = dest / rel
        tampered = b"not the authoritative content, but self-consistently tampered\n"
        target.write_bytes(tampered)
        for entry in manifest["copied_files"]:
            if entry["path"] == rel:
                entry["sha256"] = ar.sha256_of(target)
                entry["bytes"] = target.stat().st_size
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

        with self.assertRaises(ar.ArchiveError) as cm:
            self._retry(state=state, home=home)
        self.assertIn("does not match a manifest freshly derived", str(cm.exception))

        self.assertEqual(_head(self.repo), before_head)
        self.assertEqual(git(self.repo, "diff", "--cached", "--name-only").strip(), "")
        after_snapshot = _snapshot(dest)
        self.assertEqual(after_snapshot[rel], tampered, "a refusal must preserve the placed data, not revert it")
        for key, value in before_snapshot.items():
            if key != rel and key != "manifest.json":
                self.assertEqual(after_snapshot[key], value)


class ForgedManifestFactsAreRefusedTests(RetryIntegrityTestCase):
    """Forged manifest facts/semantic fields, with every copied file left
    byte-identical, must still be refused."""

    def test_forged_facts_run_id_is_refused(self):
        attempt = "wfc-retry-forged-run-id-01"
        home, state, ops_dir = self._build(attempt)
        dest, manifest = self._place_genuine(attempt=attempt, state=state, ops_dir=ops_dir)
        before_head = _head(self.repo)
        before_snapshot = _snapshot(dest)

        manifest["facts"]["run_id"] = "RUN-GENUINELY-FORGED-0000000000000001"
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

        with self.assertRaises(ar.ArchiveError) as cm:
            self._retry(state=state, home=home)
        self.assertIn("does not match a manifest freshly derived", str(cm.exception))
        self.assertEqual(_head(self.repo), before_head)
        self.assertEqual(_snapshot(dest)["manifest.json"], json.dumps(manifest, indent=2).encode() + b"\n")
        for key, value in before_snapshot.items():
            if key != "manifest.json":
                self.assertEqual(_snapshot(dest)[key], value)

    def test_forged_semantic_payload_is_refused(self):
        attempt = "wfc-retry-forged-semantic-01"
        home, state, ops_dir = self._build(attempt)
        genuine_semantic = {"domain": "warm_storage", "workstream": "retry_integrity"}
        dest, manifest = self._place_genuine(
            attempt=attempt, state=state, ops_dir=ops_dir, semantic_payload=genuine_semantic,
        )
        before_head = _head(self.repo)

        manifest["semantic"] = {"domain": "warm_storage", "workstream": "a-different-workstream"}
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

        with self.assertRaises(ar.ArchiveError) as cm:
            # The retry's own --semantic-json input is unchanged (still the
            # genuine payload); only the placed manifest's recorded semantic
            # field was forged.
            ar.archive_run(
                state_root=state, evidence_repo=self.repo, home=home,
                semantic_path=self._write_semantic(genuine_semantic),
            )
        self.assertIn("does not match a manifest freshly derived", str(cm.exception))
        self.assertEqual(_head(self.repo), before_head)

    def _write_semantic(self, payload: dict) -> Path:
        path = self.base / "semantic.json"
        path.write_text(json.dumps(payload))
        return path

    def test_forged_per_file_source_is_refused(self):
        """Per-file ``source`` provenance, not just path/hash/size, must be
        part of the comparison."""

        attempt = "wfc-retry-forged-source-01"
        home, state, ops_dir = self._build(attempt)
        dest, manifest = self._place_genuine(attempt=attempt, state=state, ops_dir=ops_dir)
        before_head = _head(self.repo)

        manifest["copied_files"][0]["source"] = "/totally/fabricated/provenance/path.json"
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

        with self.assertRaises(ar.ArchiveError) as cm:
            self._retry(state=state, home=home)
        self.assertIn("does not match a manifest freshly derived", str(cm.exception))
        self.assertEqual(_head(self.repo), before_head)

    def test_forged_per_file_source_added_as_extra_key_is_refused(self):
        """A genuinely derived ``copied_files`` entry has exactly the four
        keys ``_copy_one`` writes (path/source/sha256/bytes). Adding an
        *extra* key, rather than substituting one of those four, must be
        caught too -- a comparison that only projects known keys would miss
        it, since a freshly derived entry can never carry that key."""

        attempt = "wfc-retry-forged-extra-key-01"
        home, state, ops_dir = self._build(attempt)
        dest, manifest = self._place_genuine(attempt=attempt, state=state, ops_dir=ops_dir)
        before_head = _head(self.repo)

        manifest["copied_files"][0]["verified_by"] = "FORGED: WFC Coordinator trusted review"
        manifest["copied_files"][0]["original_source"] = "/attacker/controlled/path.json"
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

        with self.assertRaises(ar.ArchiveError) as cm:
            self._retry(state=state, home=home)
        self.assertIn("does not match a manifest freshly derived", str(cm.exception))
        self.assertEqual(_head(self.repo), before_head)
        self.assertEqual(git(self.repo, "diff", "--cached", "--name-only").strip(), "")


class FreshAuthoritativeSourceChangeIsRefusedTests(RetryIntegrityTestCase):
    """The underlying invariant, not just forged-manifest examples: if the
    authoritative Run/OPS sources themselves changed, or partially vanished,
    between placement and retry, the retry must refuse rather than commit
    stale placed data as if it still reflected current authoritative truth."""

    def test_source_content_changed_after_placement_is_refused(self):
        attempt = "wfc-retry-source-changed-01"
        home, state, ops_dir = self._build(attempt)
        dest, manifest = self._place_genuine(attempt=attempt, state=state, ops_dir=ops_dir)
        before_head = _head(self.repo)
        placed_bytes = (dest / "run" / "operator-summary.json").read_bytes()

        # The authoritative source changes after placement -- legitimately,
        # not an attack -- but the placed copy was never updated to match.
        write_json(state / "operator-summary.json", {
            "schema_version": 1, "run_id": f"RUN-RETRY-{attempt}",
            "work_item_id": "WI-RETRY-INTEGRITY-001", "run_status": "failed",
        })

        with self.assertRaises(ar.ArchiveError) as cm:
            self._retry(state=state, home=home)
        self.assertIn("does not match a manifest freshly derived", str(cm.exception))
        self.assertEqual(_head(self.repo), before_head)
        self.assertEqual((dest / "run" / "operator-summary.json").read_bytes(), placed_bytes,
                          "a refusal must preserve the placed data")

    def test_partial_ops_input_loss_after_placement_is_refused(self):
        attempt = "wfc-retry-partial-ops-loss-01"
        home, state, ops_dir = self._build(attempt, with_ops=True)
        dest, manifest = self._place_genuine(attempt=attempt, state=state, ops_dir=ops_dir)
        before_head = _head(self.repo)

        # One of the two OPS files the placement copied vanishes before the
        # retry -- a partial input loss, not a full absence.
        (ops_dir / "preparation.log").unlink()

        with self.assertRaises(ar.ArchiveError) as cm:
            self._retry(state=state, home=home)
        self.assertIn("does not match a manifest freshly derived", str(cm.exception))
        self.assertEqual(_head(self.repo), before_head)

    def test_ops_directory_entirely_missing_after_placement_is_refused(self):
        attempt = "wfc-retry-ops-vanished-01"
        home, state, ops_dir = self._build(attempt, with_ops=True)
        dest, manifest = self._place_genuine(attempt=attempt, state=state, ops_dir=ops_dir)
        before_head = _head(self.repo)

        shutil.rmtree(ops_dir)

        with self.assertRaises(ar.ArchiveError) as cm:
            self._retry(state=state, home=home)
        # The OPS directory's full absence is caught even earlier, by the
        # recorded-sources check (no OPS directory is resolved at all for
        # this retry), rather than needing the freshly-derived-manifest
        # comparison -- still a refusal, still fails closed.
        self.assertIn("different sources", str(cm.exception))
        self.assertEqual(_head(self.repo), before_head)


class GenuineRetryStillSucceedsTests(RetryIntegrityTestCase):
    """Positive controls: the new reverification must not block a
    genuinely intact resume, including ordinary ``archived_at`` variation,
    and the already-committed no-op property is unaffected."""

    def test_genuinely_unchanged_placement_resumes_to_exactly_one_commit(self):
        attempt = "wfc-retry-genuine-resume-01"
        home, state, ops_dir = self._build(attempt, with_ops=True)
        dest, manifest = self._place_genuine(attempt=attempt, state=state, ops_dir=ops_dir)
        before_head = _head(self.repo)

        # Ordinary archived_at variation: the placed manifest's own
        # archived_at timestamp differs from whatever this retry's fresh
        # candidate would compute; this must be the one legitimate
        # difference the comparison ignores.
        manifest["archived_at"] = "2020-01-01T00:00:00+00:00"
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

        report = self._retry(state=state, home=home)
        self.assertEqual(report.outcome, "archived")
        self.assertEqual(git(self.repo, "rev-list", "--count", f"{before_head}..HEAD").strip(), "1")

    def test_identical_already_committed_archive_remains_a_no_op(self):
        attempt = "wfc-retry-already-committed-noop-01"
        home, state, ops_dir = self._build(attempt, with_ops=True)
        first = self._retry(state=state, home=home)
        self.assertEqual(first.outcome, "archived")
        head_after_first = _head(self.repo)

        second = self._retry(state=state, home=home)
        self.assertEqual(second.outcome, "no_op")
        self.assertEqual(_head(self.repo), head_after_first)


if __name__ == "__main__":
    unittest.main()
